# CNC Log System — Kurulum ve Kullanım Rehberi

Heidenhain TNC 640 kontrollü tezgahın çalışma verilerini otomatik kaydeden,
duruşları ve alarmları tutan, gün/vardiya raporu çıkaran bir program.

**Tek cümlelik özet:** Klasörü kopyalayın, `./baslat.sh` yazın, tarayıcıda açılır.
Kurulum, paket indirme, internet gerekmez.

---

## İçindekiler

1. [Önce bunu okuyun — programın sınırları](#1-önce-bunu-okuyun--programın-sınırları)
2. [Adım 0: Keşif betiği](#2-adım-0-keşif-betiği--en-önce-bunu-çalıştırın)
3. [Kurulum](#3-kurulum)
4. [İlk deneme — simülatörle](#4-i̇lk-deneme--simülatörle)
5. [Gerçek tezgaha bağlanma](#5-gerçek-tezgaha-bağlanma)
6. [Arayüzün kullanımı](#6-arayüzün-kullanımı)
7. [Ayarlar (config.ini)](#7-ayarlar-configini)
8. [Otomatik başlatma](#8-otomatik-başlatma)
9. [Tezgah ekranından bakmak](#9-tezgah-ekranından-bakmak-opsiyon-133)
10. [Veriler nerede, nasıl yedeklenir](#10-veriler-nerede-nasıl-yedeklenir)
11. [Sorun giderme](#11-sorun-giderme)
12. [Sık sorulan sorular](#12-sık-sorulan-sorular)

---

## 1. Önce bunu okuyun — programın sınırları

Bu bölüm sizi zaman kaybından kurtarır. Lütfen atlamayın.

### Program tezgaha hiçbir şey yazmaz

Program yalnızca **okur**. Kod içinde tezgaha veri gönderen, dosya aktaran,
tuş basan, parametre değiştiren **hiçbir fonksiyon yoktur**. Üretimdeki bir
tezgaha bağlanmanın ön şartı budur.

### Kontrol ünitesinin içine kurulum büyük olasılıkla mümkün değil

TNC 640'ın işletim sistemi **HEROS**, SELinux ile kilitlidir. Heidenhain'in
kendi kılavuzu, sistem dosyalarının yalnızca izin verilmiş programlarca
değiştirilebildiğini yazar. Pratikte:

- root erişiminiz yoktur,
- paket kuramazsınız,
- kendi programınızı kurmak engellenir ve servis bunu garanti dışı sayar.

**Bu program o kısıtı hesaba katarak yazıldı:** hiçbir şey kurmaz, hiçbir paket
indirmez. HEROS'ta Python varsa ve çalıştırmaya izin veriliyorsa doğrudan orada
çalışır. Verilmiyorsa — ki muhtemel olan bu — **tezgahın yanındaki ayrı bir
Linux bilgisayarda** çalıştırın ve tezgaha ağ üzerinden bağlanın. Aynı kod, tek
fark nereye kopyaladığınız. Tezgah ekranından bakmak için
[9. bölüme](#9-tezgah-ekranından-bakmak-opsiyon-133) bakın.

Hangisinin geçerli olduğunu [keşif betiği](#2-adım-0-keşif-betiği--en-önce-bunu-çalıştırın)
söyleyecek.

### Gerçek F ve S değerleri için ek lisans gerekir

Bu önemli, çünkü beklentiyi doğru kurmak lazım:

| Veri | LSV2 (ücretsiz) | OPC UA (Opsiyon 56) |
|---|:---:|:---:|
| Çalışıyor / duruyor / alarm | ✅ | ✅ |
| Duruş süreleri, parça değişimi | ✅ | ✅ |
| Alarm kodu ve mesajı | ✅ | ✅ |
| Program adı, satır no, takım no | ✅ | ✅ |
| İlerleme/devir **override yüzdesi** | ✅ | ✅ |
| **Gerçek ilerleme (mm/dk)** | ❌ | ✅ |
| **Gerçek devir (dev/dk)** | ❌ | ✅ |

LSV2 protokolü gerçek F ve S değerlerini **hiçbir tezgahta** vermez; sadece
override yüzdesini verir. Bu programın eksiği değil, protokolün sınırı.

Okunamayan alanlar arayüzde ve logda `—` görünür. **Program yine tam olarak
çalışır** — duruş takibi, alarmlar, süreler, raporlar hepsi çalışır.

> PLC hafızasından gerçek F/S okumak teknik olarak mümkün ama **bilerek
> yapılmadı**: o yöntem `PLCDEBUG` erişimi ister, o da şifrelidir ve PLC'ye
> **yazma** yetkisi de açar. "Tezgaha hiçbir şey yazmaz" güvencesi iki sayıdan
> daha değerli.

### LSV2 için tezgahta DNC opsiyonu gerekebilir

Veri okuyan tüm fonksiyonlar `DNC` yetkisiyle çalışır; bu genelde **Opsiyon 18
(DNC)** lisansını gerektirir. Lisans yoksa program bağlanır ama veri okuyamaz —
ve size bunu net bir mesajla söyler, sessizce boş kayıt tutmaz.

---

## 2. Adım 0: Keşif betiği — en önce bunu çalıştırın

Bu betik **hiçbir şeyi değiştirmez**, sadece bakar ve rapor yazar. Hangi
kurulum yolunun geçerli olduğunu bu belirler.

Tezgahta veya tezgahın yanındaki bilgisayarda:

```bash
sh kurulum/kesif.sh
```

Tezgahın IP adresini biliyorsanız ağ testini de yapsın:

```bash
sh kurulum/kesif.sh 192.168.1.50
```

Çıktının **tamamını** kopyalayıp saklayın. Şunları söyler:

- İşletim sistemi ve masaüstü ortamı — gerçekten XFCE mi, yoksa HEROS mu
- Python var mı, sürümü yeterli mi, `sqlite3` modülü derlenmiş mi
- Dosya yazma izniniz var mı
- Tezgahın **19000** (LSV2) ve **4840** (OPC UA) portları açık mı
- SELinux açık mı

Betiğin göremediği, **tezgah ekranından bakmanız gereken** tek şey lisanslı
opsiyonlar. TNC ekranında **MOD** tuşuna basıp listeye bakın:

| Opsiyon | Adı | Ne işe yarar |
|---|---|---|
| **18** | DNC | LSV2 ile veri okumak için gerekli |
| **56** | OPC UA NC Server | Gerçek F/S değerleri için |
| **133** | Remote Desktop Manager | Tezgah ekranından programa bakmak için |

---

## 3. Kurulum

Kurulum diye bir şey yok — **klasörü kopyalayın, bitti.**

1. Program klasörünü USB veya ağ üzerinden bilgisayara kopyalayın.
   Örnek hedef: `/home/operator/cnclog`

2. Başlatıcıya çalıştırma izni verin:

```bash
chmod +x baslat.sh kurulum/*.sh
```

3. Çalıştırın:

```bash
./baslat.sh
```

Bu kadar. Program açılır, tarayıcıda arayüz gelir, kayıt başlar.

**Gereken tek şey Python 3.7 veya üzeridir.** Başka hiçbir paket, kütüphane
veya internet bağlantısı gerekmez. Kullanılan tek dış kütüphane olan pyLSV2
program klasörünün içinde gelir (`cnclog/vendor/pyLSV2`, MIT lisanslı).

---

## 4. İlk deneme — simülatörle

**Tezgaha hiç bağlanmadan** programın nasıl çalıştığını görün. Bu tamamen
güvenlidir; sahte bir tezgah simüle edilir.

```bash
./baslat.sh --surucu simulator
```

Tarayıcı açılır, loglar akmaya başlar. Kapatmak için terminalde **Ctrl+C**.

Hızlandırılmış deneme — 1 saatlik vardiyayı 1 dakikada oynatır:

```bash
./baslat.sh --surucu simulator --sim-hiz 60
```

Simülatör kasten zor durumları üretir: parça değişimi duruşları, eşiğin altında
kalan kısa duraklamalar, alarmlar, kopan bağlantı. Böylece programın bunları
doğru ayırdığını kendiniz görebilirsiniz.

Denemeden sonra raporu görün:

```bash
./baslat.sh --rapor bugun
```

> **Not:** Deneme verilerini gerçek verilerden ayırmak isterseniz
> `--dizin /tmp/deneme` ekleyin; kayıtlar oraya yazılır.

---

## 5. Gerçek tezgaha bağlanma

### 5.1 Önce bağlantıyı test edin

Kayıt tutmadan, sadece tek bir okuma yapıp sonucu gösterir:

```bash
./baslat.sh --surucu heidenhain_lsv2 --test-baglanti
```

Bu komut size şunları söyler:

- Bağlantı kuruldu mu
- Tezgahtan hangi alanlar okunabiliyor, hangileri okunamıyor
- LSV2 yeteneklerinin tek tek listesi (program durumu, alarm, takım…)

Başarılıysa şuna benzer bir çıktı alırsınız:

```
Bağlantı kuruldu. Tek okuma yapılıyor…

  Çalışma durumu (ham)  : running
  Türetilen durum       : ÇALIŞIYOR
  İlerleme F (mm/dk)    : —
  Devir S (dev/dk)      : —
  F override (%)        : 100
  Program               : PARCA_12.H
  ...
  LSV2 okuma yetenekleri (bu tezgahta):
    Program durumu            : evet
    Hata/alarm mesajları      : evet
    ...
```

`F` ve `S` yanında `—` görmeniz normaldir — [1. bölümdeki tabloya](#gerçek-f-ve-s-değerleri-için-ek-lisans-gerekir)
bakın.

### 5.2 Ayarları yazın

```bash
cp config.ornek.ini config.ini
```

`config.ini` içinde en az şunları düzenleyin:

```ini
[genel]
makine_id = TEZGAH-01
makine_adi = DMG Mori / TNC 640

[surucu]
tip = heidenhain_lsv2
tnc_ip = 192.168.1.50
```

### 5.3 Çalıştırın

```bash
./baslat.sh
```

### 5.4 OPC UA kullanacaksanız (Opsiyon 56 varsa)

Gerçek F/S değerlerini istiyorsanız ve tezgahta Opsiyon 56 lisanslıysa:

```bash
pip3 install asyncua
./baslat.sh --surucu heidenhain_opcua --test-baglanti
```

Bu komut sunucudaki değişkenleri tarayıp node kimliklerini listeler. İlgili
olanları `config.ini` içindeki `[opcua]` bölümüne yazın, sonra `tip =
heidenhain_opcua` yapın.

> **Dürüst uyarı:** OPC UA sürücüsü gerçek bir lisanslı tezgahta test
> edilemedi (elimizde öyle bir makine yoktu). Node kimlikleri tezgahtan
> tezgaha değişir, o yüzden elle yapılandırılır. LSV2 sürücüsü ise
> tamamlanmış ve mantığı test edilmiştir.

---

## 6. Arayüzün kullanımı

Arayüz `http://127.0.0.1:8760` adresinde açılır.

### Ana sayfa

- **Büyük durum kartı** — makinenin şu anki hali, renkli:
  🟢 ÇALIŞIYOR · 🟠 DURUŞ · 🔴 ALARM · 🔵 KURULUM · ⚫ BAĞLANTI YOK
- **Günün özeti** — çalışma süresi, duruş süresi, kullanılabilirlik %,
  duruş sayısı, alarm sayısı
- **Anlık değerler** — F, S, override'lar, program adı, blok no, takım no
- **Kayıtlar** — solda saat, sağda olay. En yeni üstte, 2 saniyede bir yenilenir.
  - **Tarih** seçici ile geçmiş günlere bakılır
  - **Göster** filtresi: sadece duruşlar / sadece alarmlar / sadece programlar

Bir log satırı şöyle okunur:

```
14:32:07  ÇALIŞIYOR   F=  1250 mm/dk  S=  8000 dev/dk  Prog=PARCA_12.H  N=1240  T=12
14:33:12  DURUŞ       başladı — program yüklü değil (parça değişimi veya bekleme)
14:36:41  DURUŞ       bitti — süre 3dk 29sn
14:41:02  ALARM       [2.15.1024] Soğutma sıvısı basıncı düşük
```

### Rapor sekmesi

Gün özeti, vardiya kırılımı, program bazlı süreler, en uzun duruşlar, alarm
listesi. **CSV indir** düğmesi Excel'de açılabilen bir dosya verir (Türkçe
Excel'de sütunlar doğru ayrılır).

### Terminalden rapor

```bash
./baslat.sh --rapor bugun
./baslat.sh --rapor dun
./baslat.sh --rapor 2026-08-10
```

Bu komut program çalışırken de kullanılabilir; kaydı bozmaz.

---

## 7. Ayarlar (config.ini)

Tüm ayarlar `config.ornek.ini` içinde tek tek açıklanmıştır. En çok
değiştirilenler:

| Ayar | Ne yapar |
|---|---|
| `durus_esigi_sn` | Bir duruşun kayda geçmesi için gereken en az süre. **10** = kısa duraklamalar logu şişirmez. **0** = her duraklama anında kaydedilir. |
| `ornekleme_araligi_sn` | Kaç saniyede bir ölçüm alınacak (varsayılan 2) |
| `saklama_gun` | Ayrıntılı ölçümler kaç gün saklanacak (varsayılan 90). **Duruş, alarm ve program kayıtları asla silinmez.** |
| `bind` | `127.0.0.1` sadece bu bilgisayar; `0.0.0.0` ağdaki herkes (şifre yoktur!) |
| `baslangiclar` | Vardiya saatleri |

Bozuk bir değer yazarsanız program durmaz, o ayar varsayılanına döner.

---

## 8. Otomatik başlatma

### XFCE menüsüne ve masaüstüne kısayol

```bash
sh kurulum/menuye-ekle.sh
```

Menüde **CNC Log** olarak görünür. Kaldırmak için `sh kurulum/menuye-ekle.sh --kaldir`.

### Bilgisayar açılınca kendiliğinden başlasın (systemd)

Bu yöntem programı arka planda sürekli çalıştırır ve çökerse yeniden başlatır.
Root gerekmez:

```bash
mkdir -p ~/.config/systemd/user
cp kurulum/cnclog.service ~/.config/systemd/user/
```

Dosyayı açıp `WorkingDirectory` ve `ExecStart` satırlarındaki yolları kendi
klasörünüze göre düzenleyin, sonra:

```bash
systemctl --user daemon-reload
systemctl --user enable --now cnclog
```

Durumu görmek için:

```bash
systemctl --user status cnclog
journalctl --user -u cnclog -f
```

Kimse oturum açmamışken de çalışması için (tezgah başında genelde böyledir):

```bash
sudo loginctl enable-linger $USER
```

> systemd yoksa: XFCE → **Ayarlar → Oturum ve Başlangıç → Otomatik Başlatılan
> Uygulamalar** bölümünden `baslat.sh --tarayici-yok` komutunu ekleyin.

---

## 9. Tezgah ekranından bakmak (Opsiyon 133)

Program tezgahın yanındaki ayrı bir bilgisayarda çalışıyorsa, operatörün
tezgahtan kalkması gerekmez.

Heidenhain **Remote Desktop Manager (Opsiyon 133)**, ağdaki bir bilgisayarın
ekranını TNC ekranında gösterir ve **kontrol klavyesindeki bir tuşla** TNC
ekranı ile o bilgisayar arasında geçiş yaptırır. **VNC ile Linux makineleri de
destekler.**

Kurulum sırası:

1. Log bilgisayarında bir VNC sunucusu çalıştırın (`x11vnc`, `tigervnc` vb.)
2. TNC'de **MOD → Remote Desktop Manager** bölümünden yeni bir **VNC**
   bağlantısı tanımlayın, log bilgisayarının IP'sini girin
3. Artık tezgah ekranından tek tuşla log ekranına geçilebilir

Opsiyon 133 yoksa daha basit alternatifler:

- Log bilgisayarına küçük bir ikinci ekran bağlamak
- `config.ini` içinde `bind = 0.0.0.0` yapıp ofisteki bir bilgisayardan
  tarayıcıyla bakmak *(şifre koruması yoktur — sadece güvendiğiniz ağda)*

---

## 10. Veriler nerede, nasıl yedeklenir

Her şey **yerelde**, program klasöründeki `veri/` dizininde durur. Hiçbir veri
internete veya başka bir yere gönderilmez.

```
veri/
├── cnclog.db          → veritabanı (ölçümler + olaylar)
├── cnclog.lock        → çalışan kopya kilidi (otomatik)
└── loglar/
    ├── 2026-08-13.log → günlük metin log (elle okunabilir)
    └── 2026-08-14.log
```

Metin logları herhangi bir metin düzenleyiciyle açabilirsiniz — arayüz zaten bu
dosyaları gösterir.

**Yedekleme:** `veri/` klasörünü kopyalamak yeterlidir. Program çalışırken bile
kopyalanabilir.

```bash
tar czf cnclog-yedek-$(date +%F).tar.gz veri/
```

**Yer kaplama:** 2 saniyelik örnekleme ile günde ~40 000 ölçüm satırı, yaklaşık
5–8 MB. 90 günlük varsayılan saklama ile veritabanı ~500 MB civarında dengelenir.
Duruş/alarm/program kayıtları çok küçüktür ve hiç silinmez.

---

## 11. Sorun giderme

| Belirti | Sebep ve çözüm |
|---|---|
| `Python 3.7 veya üzeri bulunamadı` | Bu makinede Python yok. `sh kurulum/kesif.sh` çalıştırıp çıktıya bakın. HEROS'ta ise programı yan bilgisayara kurun. |
| `Program zaten çalışıyor (PID …)` | İkinci kopya açmaya çalıştınız. Çalışanı kapatın veya `--dizin` ile başka bir klasör verin. Bu koruma kasıtlıdır: iki kopya aynı veriye yazarsa süreler bozulur. |
| `Tezgaha bağlanılamadı … timed out` | IP yanlış, tezgah kapalı veya ağ erişimi yok. `sh kurulum/kesif.sh <IP>` ile port 19000'i test edin. |
| `Bağlantı kuruldu ama hiçbir veri okunamıyor` | DNC opsiyonu (Opsiyon 18) lisanslı değil veya LSV2 erişimi kapalı. Tezgah servisinden açılmasını isteyin. |
| `Web arayüzü başlatılamadı` / port kullanımda | `./baslat.sh --port 8761` ile başka port deneyin. |
| Arayüzde F ve S sürekli `—` | Normal. LSV2 bu değerleri vermez. Gerçek F/S için Opsiyon 56 + OPC UA sürücüsü gerekir. |
| Loglarda `BAĞLANTI YOK` çok görünüyor | Ağ kopuyor veya tezgah kapatılıyor. Bu süre **duruş sayılmaz**, raporda ayrı gösterilir. |
| Duruşlar loglanmıyor | `durus_esigi_sn` çok yüksek olabilir. `0` yapıp deneyin. |
| Tarayıcı açılmıyor | Sunucu yine çalışıyordur. Tarayıcıyı elle açıp `http://127.0.0.1:8760` yazın. |
| `Önceki oturum düzgün kapanmamış` | Bilgi mesajıdır, hata değil. Program çökmüş veya elektrik kesilmiş; açık kayıtlar son ölçüm zamanıyla kapatıldı. |

Programı durdurmak: terminalde **Ctrl+C**. Açık kayıtlar düzgün kapatılır.

---

## 12. Sık sorulan sorular

**Tezgaha zarar verir mi?**
Hayır. Program yalnızca okur; tezgaha veri gönderen hiçbir kod yoktur.
`--test-baglanti` çıktısı da her seferinde "Tezgaha hiçbir şey yazılmadı" der.

**İnternet gerekiyor mu?**
Hayır. Hiçbir aşamada. Program hiçbir yere veri göndermez.

**Aynı anda birden fazla tezgah izleyebilir miyim?**
Şu an her tezgah için ayrı bir kopya çalıştırmanız gerekir (`--dizin` ile ayrı
veri klasörü vererek). Veritabanı ve arayüz `makine_id` alanıyla çoklu tezgaha
hazırdır; tek arayüzde birden fazla tezgah göstermek sonradan eklenebilir.

**Program çökerse veri kaybolur mu?**
Her ölçüm anında diske yazılır, bellekte biriktirilmez. Çökme sonrası açık
kalan kayıtlar bir sonraki açılışta son bilinen ölçüm zamanıyla kapatılır ve
log dosyasına `BAKIM` satırı düşülür.

**Duruş süresi neyi kapsıyor?**
Programın ilerlemediği her an: parça değişimi, bekleme, program durdurma ve
alarm süreleri. **Bağlantı kopukluğu duruşa dahil değildir** — ölçemediğimiz
zamanı duruş saymak kullanılabilirlik oranını yanlış gösterirdi.

**"Kullanılabilirlik" nasıl hesaplanıyor?**
`çalışma süresi ÷ gözlenen süre`. Gözlenen süre = çalışma + duruş + kurulum.
Bağlantının olmadığı süre paydaya girmez.

**Takım değişimi neden duruş sayılmıyor?**
Program çalışmaya devam ediyorsa (takım değişimi, bekleme çevrimi) makine
üretim yapıyor sayılır. Duruş, programın ilerlemediği zamandır. Parça değişimi
ise program durduğu için duruş olarak kaydedilir — istediğiniz davranış budur.

**Logları başka bir programa aktarabilir miyim?**
Evet. `veri/cnclog.db` standart bir SQLite veritabanıdır (`samples` ve `events`
tabloları). Ayrıca arayüzden CSV indirebilir, `veri/loglar/*.log` metin
dosyalarını doğrudan okuyabilirsiniz.

---

## Hızlı komut özeti

```bash
sh kurulum/kesif.sh 192.168.1.50          # sistemi ve bağlantıyı incele
./baslat.sh --surucu simulator            # sahte tezgahla dene
./baslat.sh --surucu simulator --sim-hiz 60   # hızlandırılmış deneme
./baslat.sh --test-baglanti               # gerçek tezgaha bağlanmayı dene
./baslat.sh                               # normal çalıştır
./baslat.sh --web-yok                     # arayüzsüz, sadece kayıt
./baslat.sh --rapor bugun                 # günün raporunu ekrana bas
./baslat.sh --port 8761                   # başka port kullan
./baslat.sh --help                        # tüm seçenekler
sh kurulum/menuye-ekle.sh                 # XFCE menüsüne ekle
```
