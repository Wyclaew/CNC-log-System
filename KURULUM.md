# CNC Log System — Kurulum Rehberi

Heidenhain TNC 640 kontrollü tezgahın çalışma verilerini otomatik kaydeden,
duruşları ve alarmları tutan, gün/vardiya raporu çıkaran bir program.

> **Günlük kullanım için → [KULLANIM.md](KULLANIM.md)**
> Bu dosya kurulum içindir; operatörün okuması gereken belge diğeridir.

**Özet:** USB'yi tak, klasörü kopyala, `sh baslat.sh` yaz. Kurulum yok, paket
indirme yok, internet gerekmez. Program tezgahı ağda kendisi bulur.

| Nerede çalıştıracaksınız | Ne yapacaksınız |
|---|---|
| Tezgah (HEROS/Linux) | `sh baslat.sh` |
| Windows — CMD / PowerShell | `baslat.bat` |
| Windows — Git Bash | `sh baslat.sh` |
| macOS | `sh baslat.sh` |

**Windows'ta simülatörle denemek için → [3B bölümü](#3b-windowsta-tnc-640-programming-station-ile-deneme)**

---

## 1. Bu tezgah hakkında bilinenler

Gönderilen ekran görüntüleri ve videodan doğrulananlar:

| Konu | Durum |
|---|---|
| Kontrol | Heidenhain TNC 640, FPT tezgah, 5 eksen (X/Y/Z/C/A) |
| İşletim sistemi | HEROS, **masaüstü ortamı XFCE** |
| Terminal | **Var** — HEROS menüsü altından açılıyor |
| Web tarayıcı | **Var** — QupZilla (Tools menüsünde) |
| Remote Desktop Manager | **Var** (Opsiyon 133 lisanslı) |
| Sistem Python | **2.7.15** — program Python 3 ister, bkz. aşağısı |
| İnternet | **Yok** |

### Python 2.7 sorunu ve çözümü

Tezgahtaki `python` komutu **2.7.15** sürümünü veriyor. Bu program Python 3
gerektiriyor ve Heidenhain'e bağlanmak için kullanılan pyLSV2 kütüphanesi de
Python 3.5+ istiyor — yani Python 2.7 ile çalışması mümkün değil.

**Çözüm pakete dahil:** `cnclog/vendor/python/` içinde **taşınabilir bir
Python 3.11** geliyor (statik derlenmiş, hiçbir sistem kütüphanesine bağımlı
değil). Sisteme **hiçbir şey kurulmaz**; arşiv sadece bir klasöre açılır ve
oradan çalıştırılır. HEROS'un SELinux koruması etkilenmez.

`baslat.sh` sırayla dener:

1. Sistemde `python3` var mı ve 3.7+ mı? → onu kullanır
   *(videoda sadece `python` denenmişti; `python3` da kurulu olabilir)*
2. Daha önce açılmış gömülü Python var mı? → onu kullanır
3. Yoksa arşivi açar (ilk çalıştırmada bir defa, ~10 saniye) → kullanır

Hiçbiri olmazsa net bir hata verir, sessizce yarım çalışmaz.

---

## 2. Kurulum

### 2.1 USB'ye hazırlama (bunu siz yapın)

Program klasörünün **tamamını** USB'ye kopyalayın. Klasör yaklaşık 142 MB'tır;
bunun ~85 MB'ı üç platform için gömülü Python.

```
CNC log System/
├── baslat.sh          ← tezgahta / Linux / macOS
├── baslat.bat         ← Windows (CMD veya PowerShell)
├── KULLANIM.md        ← operatör rehberi
├── KURULUM.md         ← bu dosya
├── cnclog/            ← program
├── kurulum/           ← keşif betiği, menü kısayolu, systemd
└── config.ornek.ini
```

> **Yer kazanmak isterseniz:** tezgaha giden USB'de
> `cnclog/vendor/python/cpython-windows.tar.gz` (25 MB) gereksizdir, silebilirsiniz.
> Windows'ta denerken ise `cpython-linux-*.tar.gz` dosyaları (59 MB) gereksizdir.
> Emin değilseniz hepsini bırakın — fazladan dosya zarar vermez.

### 2.2 Tezgahta (abinizin yapacağı)

1. USB'yi tak. HEROS'ta dosya yöneticisi: **Menü → Tools → File Manager**

2. Klasörü USB'den **ev dizinine kopyala**. Masaüstüne veya `~/cnclog` olur.

   > **Neden kopyalamak gerekiyor:** USB genelde "çalıştırma yasak" (noexec)
   > olarak bağlanır ve program oradan çalışmaz. Ayrıca USB'yi çıkarınca kayıt
   > durur. Kopyalayın.

3. Terminali aç: **Menü → Tools → Terminal**
   *(videodaki gibi; `user ~/Desktop $` yazan pencere)*

4. Klasöre gir ve çalıştır:

```bash
cd ~/Desktop/cnclog
sh baslat.sh
```

`sh baslat.sh` şeklinde yazın — `./baslat.sh` çalıştırma izni ister, `sh` ile
başlatmak buna gerek bırakmaz.

İlk çalıştırmada şunu görürsünüz:

```
Python 3 hazirlaniyor (musl)... ilk calistirmada bir defa yapilir.
Hazir: /home/user/Desktop/cnclog/cnclog/vendor/python/rt/python/bin/python3
==============================================================
 CNC Log System 1.0.0 — Heidenhain TNC 640 (TEZGAH-01)
==============================================================
 ...
 Arayüz     : http://127.0.0.1:8760
==============================================================
 Kayıt başladı. Durdurmak için Ctrl+C.
```

Tarayıcı (QupZilla) kendiliğinden açılır. Açılmazsa **Menü → Tools → QupZilla**
ile açıp adres satırına `127.0.0.1:8760` yazın.

---

## 3. Tezgaha bağlanma

**IP adresi girmenize gerek yok.** Program açılınca tezgahı kendisi arar:

1. `config.ini` içinde adres yazıyorsa önce onu dener
2. Sonra bu bilgisayarın kendisini (`127.0.0.1`) — program HEROS'ta çalışıyorsa
   kontrol zaten burada
3. Sonra aynı ağdaki diğer adresleri (253 adres, ~3 saniye)

Bulduğunda otomatik bağlanır. Bulamazsa **BAĞLANTI YOK** olarak kaydeder ve
aramaya devam eder — **asla sahte veri üretmez**.

Aramayı ayrıca çalıştırıp sonucu görmek için:

```bash
sh baslat.sh --tara
```

Bağlantıyı test etmek, hangi verilerin okunabildiğini görmek için:

```bash
sh baslat.sh --test-baglanti
```

Bu komut tezgaha **hiçbir şey yazmaz**, tek bir okuma yapar ve çıkar. Çıktının
sonunda "Tezgaha hiçbir şey yazılmadı" yazar.

### Adresi sabitlemek (isteğe bağlı, aramayı hızlandırır)

```bash
cp config.ornek.ini config.ini
```

Sonra `config.ini` içinde:

```ini
[surucu]
tnc_ip = 192.168.1.50
```

---

## 3B. Windows'ta TNC 640 Programming Station ile deneme

Tezgaha gitmeden önce programı **simülatörle** denemek en iyisi. Heidenhain'in
TNC 640 Programming Station yazılımı gerçek kontrol yazılımını çalıştırır, yani
bu bir taklit değil — gerçek bir LSV2 testidir.

> pyLSV2 kütüphanesinin geliştiricisi testlerinin çoğunu programlama
> istasyonlarında yapmış ve TNC640 için dört ayrı sürümü doğrulanmış olarak
> listeliyor. Yani bu senaryonun çalışması bekleniyor.

### Adımlar

**1. Programming Station'ı başlatın** ve kontrol tamamen açılana kadar
bekleyin. PLC programının çalışıyor olması gerekir (normal açılışta çalışır).

**2. Dış erişimi açın — bu adım atlanırsa hiçbir şey bağlanmaz.**

Kontrol ekranında:

```
Programlama modu (Programming and Editing)
   → MOD tuşu
   → External access = ON
   → END
```

Heidenhain bu ayarı güvenlik gerekçesiyle **varsayılan olarak kapalı** tutar.
Aynı ayar gerçek tezgahta da gereklidir.

**3. Programı çalıştırın.** CMD veya PowerShell açıp:

```
cd C:\cnclog
baslat.bat --tara
```

İlk çalıştırmada gömülü Python bir defa hazırlanır (~10 saniye).

Simülatör aynı bilgisayarda çalıştığı için program onu `127.0.0.1` üzerinde
bulmalıdır. Beklenen çıktı:

```
  Bilinen adresler deneniyor (2 adet)…
  127.0.0.1:19000 açık, doğrulanıyor…
------------------------------------------------------------

BULUNDU: TNC640 340595 xx (127.0.0.1:19000)
```

**4. Hangi verilerin okunabildiğini görün:**

```
baslat.bat --test-baglanti
```

**5. Kaydı başlatın:**

```
baslat.bat
```

Simülatörde bir program çalıştırın; arayüzde durumun ÇALIŞIYOR'a döndüğünü,
durdurduğunuzda DURUŞ kaydı açıldığını görmelisiniz.

### Bulamazsa

| Belirti | Bakılacak |
|---|---|
| `127.0.0.1:19000` listede yok | Simülatör çalışmıyor veya LSV2 portu dinlemiyor |
| Port açık ama doğrulanamıyor | Dış erişim kapalı → MOD → External access = ON |
| Bağlanıyor, veri gelmiyor | DNC opsiyonu yok veya PLC programı çalışmıyor |

> **Not:** Windows'ta gömülü Python yalnızca denemek içindir. Tezgaha
> götüreceğiniz USB'de `cnclog/vendor/python/cpython-windows.tar.gz`
> dosyasını silebilirsiniz (25 MB kazanırsınız); tezgah Linux tarafını
> kullanır.

---

## 4. Neyin okunabildiği, neyin okunamadığı

Bu tabloyu bilmek beklentiyi doğru kurar:

| Veri | LSV2 (ücretsiz) | OPC UA (Opsiyon 56) |
|---|:---:|:---:|
| Çalışıyor / duruyor / alarm | ✅ | ✅ |
| Duruş süreleri, parça değişimi | ✅ | ✅ |
| Alarm kodu ve mesajı | ✅ | ✅ |
| Program adı, satır no, takım no | ✅ | ✅ |
| İlerleme/devir **override yüzdesi** | ✅ | ✅ |
| **Gerçek ilerleme (mm/dk)** | ❌ | ✅ |
| **Gerçek devir (dev/dk)** | ❌ | ✅ |

LSV2 protokolü gerçek F ve S değerlerini **hiçbir tezgahta** vermez — bu
programın eksiği değil, protokolün sınırı. Okunamayan alanlar `—` görünür ve
**program tam olarak çalışmaya devam eder**.

> **Neden PLC'den okumuyoruz:** Teknik olarak mümkün ama `PLCDEBUG` erişimi
> gerekiyor; o da şifreli ve PLC'ye **yazma** yetkisi açıyor. "Tezgaha hiçbir
> şey yazmaz" güvencesi iki sayıdan değerli. Gerçek F/S istiyorsanız doğru yol
> Opsiyon 56'dır.

**LSV2 için tezgahta DNC opsiyonu (Opsiyon 18) gerekebilir.** Yoksa program
bağlanır ama veri okuyamaz — ve size bunu net bir mesajla söyler.

Tezgahta hangi opsiyonların lisanslı olduğunu görmek için: TNC ekranında
**MOD** tuşuna basıp listeye bakın (18 = DNC, 56 = OPC UA, 133 = Remote Desktop).

---

## 5. Sürekli çalışması için

### XFCE menüsüne ekleme

```bash
sh kurulum/menuye-ekle.sh
```

Menüde **CNC Log** olarak görünür. HEROS'un XFCE masaüstünde alt-tab ile
geçiş yapılabilir.

### Bilgisayar açılınca kendiliğinden başlaması

```bash
mkdir -p ~/.config/systemd/user
cp kurulum/cnclog.service ~/.config/systemd/user/
```

Dosyadaki `WorkingDirectory` ve `ExecStart` yollarını kendi klasörünüze göre
düzenleyin, sonra:

```bash
systemctl --user daemon-reload
systemctl --user enable --now cnclog
```

> systemd yoksa veya izin verilmiyorsa: XFCE → **Ayarlar → Oturum ve Başlangıç
> → Otomatik Başlatılan Uygulamalar** bölümünden `sh /yol/baslat.sh
> --tarayici-yok` komutunu ekleyin.

### Ağdaki başka bir bilgisayardan bakmak

`config.ini` içinde:

```ini
[web]
bind = 0.0.0.0
```

Sonra ofisteki bilgisayardan `http://<tezgah-ip>:8760` adresine girin.

> **Uyarı:** Şifre koruması yoktur. Sadece güvendiğiniz bir ağda kullanın.

---

## 6. Veriler nerede

Her şey **yerelde**, program klasöründeki `veri/` dizininde. Hiçbir veri
internete veya başka bir yere gönderilmez (zaten internet de yok).

```
veri/
├── cnclog.db          → veritabanı (ölçümler + olaylar)
├── cnclog.lock        → çalışan kopya kilidi (otomatik)
└── loglar/
    ├── 2026-08-15.log → günlük metin log (elle okunabilir)
    └── 2026-08-16.log
```

**Yedekleme:** `veri/` klasörünü kopyalayın. Program çalışırken bile
kopyalanabilir.

**Yer kaplama:** 2 saniyelik örnekleme ile günde ~5–8 MB. 90 günlük varsayılan
saklama ile veritabanı ~500 MB civarında dengelenir. Duruş/alarm/program
kayıtları çok küçüktür ve **hiç silinmez**.

---

## 7. Sorun giderme

| Belirti | Çözüm |
|---|---|
| `Calisir bir Python 3 bulunamadi` | `sh kurulum/kesif.sh` çalıştırıp çıktıyı gönderin. Gömülü arşiv kopyalanmamış olabilir. |
| `Python'u acacak yazilabilir bir klasor bulunamadi` | Program klasörü salt-okunur bir yerde (USB). Ev dizinine kopyalayın. |
| Permission denied | `./baslat.sh` yerine `sh baslat.sh` yazın. |
| `Program zaten çalışıyor (PID …)` | İkinci kopya açılmaya çalışıldı. Bu koruma kasıtlıdır: iki kopya aynı veriye yazarsa süreler bozulur. |
| Tezgah bulunamıyor | `sh baslat.sh --tara`. Tezgah açık mı, LSV2/DNC erişimi açık mı kontrol edin. |
| Bağlanıyor ama veri yok | DNC opsiyonu (18) lisanslı değil veya LSV2 kapalı. Servisten açtırın. |
| F ve S sürekli `—` | Normal. Bkz. 4. bölüm. |
| Tarayıcı açılmıyor | Sunucu yine çalışıyor. QupZilla'yı elle açıp `127.0.0.1:8760` yazın. |
| Port kullanımda | `sh baslat.sh --port 8761` |
| `Önceki oturum düzgün kapanmamış` | Bilgi mesajı, hata değil. Açık kayıtlar onarıldı, veri kaybı yok. |

Programı durdurmak: terminalde **Ctrl+C**. Açık kayıtlar düzgün kapatılır.

---

## 8. Keşif betiği (sorun olursa)

Bir şey ters giderse, hiçbir şeyi değiştirmeyen bu betik durumu raporlar:

```bash
sh kurulum/kesif.sh
```

Tezgahın IP'sini biliyorsanız ağ testini de yapar:

```bash
sh kurulum/kesif.sh 192.168.1.50
```

Çıktının tamamını kopyalayıp gönderin.

---

## Hızlı komut özeti

```bash
sh baslat.sh                       # normal çalıştır
sh baslat.sh --tara                # tezgahı ağda ara
sh baslat.sh --test-baglanti       # bağlantıyı test et, veri oku, çık
sh baslat.sh --rapor bugun         # günün raporunu ekrana bas
sh baslat.sh --web-yok             # arayüzsüz, sadece kayıt
sh baslat.sh --port 8761           # başka port
sh baslat.sh --python-bilgi        # hangi Python kullanılıyor
sh baslat.sh --surucu simulator    # sahte tezgahla dene (sadece deneme!)
sh baslat.sh --help                # tüm seçenekler
sh kurulum/menuye-ekle.sh          # XFCE menüsüne ekle
sh kurulum/kesif.sh                # sistem raporu
```
