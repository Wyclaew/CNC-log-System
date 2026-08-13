# CNC Log System

Heidenhain TNC 640 kontrollü CNC tezgahlar için yerel veri kayıt ve raporlama
programı. Çalışma/duruş sürelerini, alarmları ve program sürelerini kaydeder;
tarayıcıda açılan bir arayüzde gösterir.

**Kurulum ve kullanım için → [KURULUM.md](KURULUM.md)**

```bash
./baslat.sh --surucu simulator     # gerçek tezgah olmadan dene
./baslat.sh --test-baglanti        # tezgaha bağlanmayı test et
./baslat.sh                        # normal çalıştır
```

---

## Tasarım kararları

**Sıfır harici bağımlılık.** Yalnızca Python 3.7+ standart kütüphanesi. Hedef
ortam, SELinux ile kilitli bir HEROS kontrol bilgisayarı olabilir; orada pip
yoktur ve paket kurulamaz. Kurulum, klasörü kopyalamaktan ibaret olmalı.
Kullanılan tek dış kütüphane (pyLSV2, MIT) `cnclog/vendor/` içinde gelir.

**Salt okunur.** `Driver` arayüzünde yazma metodu yoktur ve hiçbir sürücü
tezgaha veri göndermez. PLC hafızası okuma bilerek uygulanmadı: `PLCDEBUG`
erişimi şifrelidir ve PLC'ye yazma yetkisi de açar.

**Ham durum ile onaylanmış durum ayrı.** Ölçüm kayıtları anlık (ham) durumu
tutar, olay kayıtları ise duruş eşiğini geçmiş (onaylanmış) durumu. Böylece
5 saniyelik bir duraklama veriden kaybolmaz ama olay listesini şişirmez.
Onaylanan duruş, eşiğin dolduğu ana değil **gerçekte başladığı ana** tarihlenir.

**Süreler olaylardan hesaplanır, ölçüm sayısından değil.** Örnekleme aralığı
değişebilir, program yeniden başlatılabilir, eski ölçümler saklama politikasıyla
silinir. Olay kayıtları bunların hiçbirinden etkilenmez ve asla silinmez.

**BAĞLANTI YOK duruş değildir.** Ölçemediğimiz zamanı duruş saymak
kullanılabilirlik oranını yanlış gösterirdi; ayrı raporlanır ve paydaya girmez.

---

## Proje yapısı

```
cnclog/
├── model.py            Snapshot, Sample, Event, MachineState
├── config.py           config.ini okuma (Türkçe anahtarlar, İngilizce alanlar)
├── state.py            durum makinesi, duruş eşiği/debounce, sebep çıkarımı
├── storage.py          SQLite + JSONL yedeği + günlük metin log
├── collector.py        örnekleme döngüsü (arka plan thread)
├── report.py           gün/vardiya özeti, program süreleri, CSV
├── lock.py             tek örnek kilidi (flock)
├── app.py              parçaları birleştiren orkestratör
├── __main__.py         CLI
├── drivers/
│   ├── base.py             Driver arayüzü (yazma metodu YOK)
│   ├── registry.py         isimden sürücü üretimi (geç import)
│   ├── simulator.py        sahte tezgah — test ve demo
│   ├── heidenhain_lsv2.py  TNC 640, pyLSV2 üzerinden
│   └── heidenhain_opcua.py OPC UA NC Server (Opsiyon 56)
├── web/
│   ├── server.py       stdlib ThreadingHTTPServer, sadece GET
│   ├── api.py          JSON uçları
│   └── static/         tek sayfa arayüz (harici CDN/font yok)
└── vendor/pyLSV2/      gömülü kütüphane (MIT)
```

## Veri akışı

```
Driver.read() → Snapshot
      ↓
StateTracker.update() → ham durum + onaylanmış durum + açılan/kapanan olaylar
      ↓
Collector.tick() ──→ Storage  (samples: ham durum · events: onaylanmış)
                └──→ TextLog  (günlük insan-okunur log)
      ↓
report.summarize() → süreler, kullanılabilirlik, program bazlı toplamlar
      ↓
web/api → tarayıcı
```

## Yeni sürücü eklemek

`drivers/base.py` içindeki `Driver` sınıfından türetin, `connect()`, `read()` ve
`disconnect()` yazın, `drivers/registry.py` içindeki `_DRIVERS` sözlüğüne
ekleyin. `read()` yalnızca kontrolün gerçekten verdiği alanları doldurmalı,
kalanları `None` bırakmalıdır — arayüz ve log bunları `—` olarak gösterir.

## Testler

```bash
python3 -m unittest discover -s tests -v
```

## Lisans notu

`cnclog/vendor/pyLSV2/` üçüncü taraf koddur; MIT lisansı kendi klasöründedir
(`cnclog/vendor/pyLSV2/LICENSE`).
