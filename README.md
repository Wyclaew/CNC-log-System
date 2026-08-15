# CNC Log System

Heidenhain TNC 640 kontrollü CNC tezgahlar için yerel veri kayıt ve raporlama
programı. Çalışma/duruş sürelerini, alarmları ve program sürelerini kaydeder;
tarayıcıda açılan bir arayüzde gösterir.

- **Operatör için → [KULLANIM.md](KULLANIM.md)**
- **Kurulum için → [KURULUM.md](KURULUM.md)**

```bash
python baslat.py                     # tezgahı ağda bul, bağlan, kaydet
python baslat.py --tara              # sadece ara ve sonucu göster
python baslat.py --test-baglanti     # bağlan, tek okuma yap, çık
python baslat.py --teshis            # ortam teşhisi (Python, noexec, ağ)
```

Windows'ta (CMD / PowerShell) aynı komutlar `baslat.bat` ile çalışır. Windows
tarafının asıl amacı, TNC 640 Programming Station simülatörüne bağlanıp
programı tezgaha gitmeden denemektir.

---

## Hedef ortamın dayattığı kısıtlar

Bu programın her mimari kararı, şu ortamdan çıktı:

| Kısıt | Sonuç |
|---|---|
| HEROS SELinux ile kilitli, pip yok | Sıfır kurulum: klasörü kopyala, çalıştır |
| Sistem Python **2.7.15** | Taşınabilir Python 3.11 pakete gömülü |
| İnternet yok | Hiçbir şey indirilmez; CDN, font, paket yok |
| Tezgah üretimde | Sürücülerde yazma metodu **yok** |
| Operatör IP bilmiyor | Tezgahı program kendisi bulur |
| HEROS'ta shell betiği çalıştırılamıyor | `baslat.py` — exec izni gerektirmez |

---

## Tasarım kararları

**Sıfır harici bağımlılık.** Yalnızca Python standart kütüphanesi. Tek dış
kütüphane olan pyLSV2 (MIT) `cnclog/vendor/` içinde gelir.

**Salt okunur.** `Driver` arayüzünde yazma metodu yoktur ve hiçbir sürücü
tezgaha veri göndermez — bir test bunu zorunlu tutar. PLC hafızası okuma bilerek
uygulanmadı: `PLCDEBUG` erişimi şifrelidir ve PLC'ye yazma yetkisi de açar.

**Sahte veriye asla düşülmez.** Tezgah bulunamazsa BAĞLANTI YOK kaydedilir ve
arama sürer. Simülatör yalnızca adıyla istendiğinde çalışır.

**Ham durum ile onaylanmış durum ayrı.** Ölçüm kayıtları anlık durumu, olay
kayıtları duruş eşiğini geçmiş durumu tutar. 5 saniyelik bir duraklama veriden
kaybolmaz ama olay listesini şişirmez. Onaylanan duruş, eşiğin dolduğu ana
değil **gerçekte başladığı ana** tarihlenir.

**Süreler olaylardan hesaplanır, ölçüm sayısından değil.** Örnekleme aralığı
değişebilir, program yeniden başlatılabilir, eski ölçümler silinir. Olay
kayıtları bunların hiçbirinden etkilenmez ve asla silinmez.

**BAĞLANTI YOK duruş değildir.** Ölçemediğimiz zamanı duruş saymak
kullanılabilirlik oranını yanlış gösterirdi; ayrı raporlanır, paydaya girmez.

**Kayıt hiçbir tek hatayla durmaz.** Disk dolarsa metin log atlanır ama
veritabanı yazmaya devam eder; sürücü çökerse backoff ile yeniden bağlanılır;
beklenmeyen bir hata toplayıcı thread'ini öldürmez.

---

## Proje yapısı

```
cnclog/
├── model.py            Snapshot, Sample, Event, MachineState
├── config.py           config.ini okuma (Türkçe anahtar, İngilizce alan)
├── state.py            durum makinesi, duruş eşiği, sebep çıkarımı
├── storage.py          SQLite + JSONL yedeği + günlük metin log
├── collector.py        örnekleme döngüsü (arka plan thread)
├── discovery.py        ağda TNC arama (port taraması + LSV2 doğrulaması)
├── report.py           gün/vardiya özeti, program süreleri, CSV
├── lock.py             tek örnek kilidi (flock / msvcrt)
├── app.py              orkestratör, çökme kurtarma
├── __main__.py         CLI
│   (kök dizinde baslat.py: Python 2.7 ile de çalışan başlatıcı)
├── drivers/
│   ├── base.py             Driver arayüzü (yazma metodu YOK)
│   ├── registry.py         isimden sürücü üretimi (geç import)
│   ├── auto.py             keşif + LSV2'ye devir  ← varsayılan
│   ├── simulator.py        sahte tezgah (enjekte edilebilir saat)
│   ├── heidenhain_lsv2.py  TNC 640, pyLSV2 üzerinden
│   └── heidenhain_opcua.py OPC UA NC Server (Opsiyon 56)
├── web/
│   ├── server.py       stdlib ThreadingHTTPServer, sadece GET
│   ├── api.py          JSON uçları
│   └── static/         tek sayfa arayüz (harici kaynak yok)
└── vendor/
    ├── pyLSV2/         gömülü kütüphane (MIT)
    └── python/         taşınabilir Python 3.11 (linux musl+glibc, windows)
```

## Veri akışı

```
AutoDriver.connect() → discovery.find_control() → HeidenhainLsv2Driver
      ↓
Driver.read() → Snapshot   (okunamayan alanlar None kalır)
      ↓
StateTracker.update() → ham durum + onaylanmış durum + açılan/kapanan olaylar
      ↓
Collector.tick() ──→ Storage  (samples: ham · events: onaylanmış)
                └──→ TextLog  (günlük insan-okunur log)
      ↓
report.summarize() → süreler, kullanılabilirlik, program bazlı toplamlar
      ↓
web/api → tarayıcı
```

## Yeni sürücü eklemek

`drivers/base.py` içindeki `Driver` sınıfından türetin, `connect()`, `read()`,
`disconnect()` yazın, `drivers/registry.py` içindeki `_DRIVERS` sözlüğüne
ekleyin. `read()` yalnızca kontrolün gerçekten verdiği alanları doldurmalı,
kalanları `None` bırakmalıdır.

## Testler

```bash
python3 -m unittest discover -s tests -t . -v
```

146 test: durum makinesi ve eşik davranışı, rapor aritmetiği, log formatı ↔ web
ayrıştırıcı uyumu, sürücü eşlemeleri, keşif/soket temizliği/arayüz sayımı,
"reddedildi" ile "bulunamadı" ayrımı, tek örnek kilidi, çökme kurtarma,
sekiz saatlik uçtan uca simüle vardiya.

## Lisans notu

`cnclog/vendor/pyLSV2/` üçüncü taraf koddur; MIT lisansı kendi klasöründedir.
`cnclog/vendor/python/` içindeki Python dağıtımları
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
projesinden gelir (PSF lisansı).
