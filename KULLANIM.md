# CNC Log System — Kullanım Rehberi

Bu program tezgahın ne kadar çalıştığını, ne kadar durduğunu, hangi alarmların
verildiğini kendiliğinden kaydeder. Operatörün hiçbir şey girmesine gerek
yoktur — açık kalması yeterlidir.

> Kurulum için → [KURULUM.md](KURULUM.md)

---

## 1. En kısa özet

| Ne yapmak istiyorsunuz | Ne yapacaksınız |
|---|---|
| Programı başlatmak | Menüden **CNC Log**, veya terminalde `sh baslat.sh` |
| Ekrana bakmak | Tarayıcı penceresi; alt-tab ile geçilir |
| Programı kapatmak | Terminalde **Ctrl+C** |
| Günün raporunu almak | Arayüzde **Rapor** sekmesi |
| Raporu Excel'e almak | Rapor sekmesinde **CSV indir** |

**Program açık kaldığı sürece kayıt tutar.** Kapatırsanız kayıt durur.

---

## 2. Ana ekranda ne var

### Üstteki büyük renkli kutu — tezgahın şu anki hali

| Renk | Durum | Anlamı |
|---|---|---|
| 🟢 Yeşil | **ÇALIŞIYOR** | Program ilerliyor, parça işleniyor |
| 🟠 Turuncu | **DURUŞ** | Program durmuş, ilerleme yok |
| 🔴 Kırmızı | **ALARM** | Tezgah alarm veriyor |
| 🔵 Mavi | **KURULUM** | Elle kumanda / MDI modunda |
| ⚫ Gri | **BAĞLANTI YOK** | Program tezgahı okuyamıyor |

Altında "55sn önce başladı" yazar — bu durumun ne kadardır sürdüğü.

### Yanındaki kutular — bugünün toplamı

- **Bugün çalışma** — tezgahın bugün ne kadar çalıştığı
- **Bugün duruş** — ne kadar durduğu
- **Kullanılabilirlik** — çalışma süresinin yüzdesi
- **Duruş sayısı** — kaç kez durduğu
- **Alarm sayısı** — kaç alarm verdiği

Bunlar gece yarısında sıfırlanır; geçmiş günler **Rapor** sekmesinde durur.

### Ortadaki şerit — anlık değerler

İlerleme (F), devir (S), override yüzdeleri, çalışan program adı, satır
numarası, takım numarası.

> `—` işareti "bu tezgah bu bilgiyi vermiyor" demektir, "sıfır" değil.
> Ayrıntı için [5. bölüm](#5-neden-f-ve-s-yerine--yazıyor).

### Alttaki liste — kayıtlar

Solda saat, sağda olay. En yeni üstte. 2 saniyede bir kendiliğinden yenilenir.

**Tarih** kutusundan geçmiş günlere bakabilirsiniz.
**Göster** kutusundan sadece duruşları, sadece alarmları filtreleyebilirsiniz.

---

## 3. Log satırları nasıl okunur

```
14:32:07  ÇALIŞIYOR     F=  1250 mm/dk  S=  8000 dev/dk  Prog=PARCA_12.H  N=1240  T=12
14:33:12  DURUŞ         başladı — program yüklü değil (parça değişimi veya bekleme)
14:36:41  DURUŞ         bitti — süre 3dk 29sn
14:41:02  ALARM         [2.15.1024] Soğutma sıvısı basıncı düşük
14:41:50  ALARM         giderildi — [2.15.1024] (süre 48sn)
15:02:30  PROGRAM       PARCA_12.H bırakıldı — yüklü kalma süresi 24dk 11sn
```

| Kısaltma | Anlamı |
|---|---|
| `F=` | İlerleme, mm/dakika |
| `S=` | Mil devri, devir/dakika |
| `Prog=` | O anda yüklü NC programı |
| `N=` | Program satır numarası |
| `T=` | Takım numarası |
| `(hızlı hareket)` | G0 boşta ilerleme |

İki tür satır vardır:

- **Ölçüm satırları** — 30 saniyede bir yazılır, o anki değerleri gösterir
- **Olay satırları** — bir şey olduğunda yazılır (duruş başladı/bitti, alarm,
  program değişti)

---

## 4. Bilinmesi gerekenler

Bu bölüm önemlidir. Raporlardaki sayıların ne anlama geldiğini burası belirler.

### 4.1 Program tezgaha hiçbir şey yazmaz

Sadece **okur**. Tezgaha komut göndermez, dosya aktarmaz, ayar değiştirmez,
tuşa basmaz. Kodun içinde bunu yapabilecek hiçbir fonksiyon yoktur. Programı
açık bırakmak tezgahı hiçbir şekilde etkilemez.

### 4.2 "Duruş" ne demek

Duruş, **programın ilerlemediği** zamandır:

- ✅ Parça değişimi → duruş
- ✅ Program durdurulmuş → duruş
- ✅ Alarm süresi → duruş
- ✅ Bekleme, iş bekleme → duruş
- ❌ Takım değişimi (program devam ediyorsa) → duruş **değil**
- ❌ Bekleme çevrimi (G4), program çalışıyorsa → duruş **değil**

Mantık şu: program çalışmaya devam ediyorsa tezgah üretim yapıyor sayılır.
İlerleme sıfır olsa bile.

### 4.3 Kısa duraklamalar loga yazılmaz — ama kaybolmaz

**10 saniyeden kısa** duraklamalar olay olarak kaydedilmez. Amaç, günde
yüzlerce satır gereksiz kayıt oluşmasını önlemek.

Bu duraklamalar **veritabanında yine durur** — sadece olay listesini
şişirmezler. Parça değişimleri genelde 30 saniyeden uzun sürdüğü için bu eşikle
de yakalanır.

Eşiği değiştirmek isterseniz `config.ini` içinde:

```ini
[toplama]
durus_esigi_sn = 10     ; 0 yazarsanız her duraklama anında kaydedilir
```

### 4.4 "BAĞLANTI YOK" duruş sayılmaz

Program tezgahı okuyamadığı süre (ağ koptu, tezgah kapalı, program yeni
açıldı) **ayrı** raporlanır ve duruş süresine **eklenmez**.

Sebebi: göremediğimiz zamanı duruş saymak kullanılabilirlik oranını yanlış
gösterirdi. Gece kapalı duran bir tezgah, "8 saat duruş" olarak görünmemeli.

Kullanılabilirlik şöyle hesaplanır:

```
Kullanılabilirlik = çalışma süresi ÷ (çalışma + duruş + kurulum)
```

Bağlantının olmadığı süre bu hesaba **hiç girmez**.

### 4.5 Duruş süresi geriye dönük yazılır

10 saniyelik eşik yüzünden bir duruş 10 saniye sonra kayda geçer — ama
**başlangıç saati gerçek başlangıçtır**. Yani 14:33:12'de duran tezgah,
kayıtta 14:33:12'de durmuş görünür, 14:33:22'de değil. Süreler doğrudur.

Bu yüzden bazı satırların sonunda `[gerçek zaman 14:33:12]` yazabilir — o
satırın gerçekte hangi ana ait olduğunu belirtir.

### 4.6 Program iki kez açılamaz

Aynı klasöre iki kopya birden yazarsa süreler bozulur. Program bunu engeller
ve ikinci kopya `Program zaten çalışıyor` diyerek kapanır. Bu bir hata değil,
korumadır.

### 4.7 Elektrik kesilirse veri kaybolmaz

Her ölçüm anında diske yazılır, bellekte biriktirilmez. Program çökerse veya
elektrik giderse, bir sonraki açılışta açık kalan kayıtlar son bilinen ölçüm
zamanıyla kapatılır ve loga `BAKIM` satırı düşülür.

---

## 5. Neden F ve S yerine `—` yazıyor

Tezgahla konuşmak için kullanılan **LSV2** protokolü, gerçek ilerleme ve devir
değerlerini vermiyor — sadece override yüzdelerini veriyor. Bu **hiçbir
Heidenhain tezgahında** çalışmaz; protokolün sınırıdır, programın eksiği değil.

Gerçek mm/dk ve dev/dk değerleri için tezgahta **OPC UA NC Server (Opsiyon 56)**
lisansı gerekiyor.

**Bu eksiklik programın geri kalanını etkilemez.** Duruş takibi, alarm kaydı,
süreler, program bazlı raporlar, kullanılabilirlik — hepsi tam çalışır.

Programın uydurma sayı yazmaktansa `—` yazması bilinçli bir tercihtir.

---

## 6. Rapor alma

### Ekrandan

**Rapor** sekmesi → tarih seç. Şunları gösterir:

- Gün özeti (çalışma, duruş, kurulum, kullanılabilirlik)
- Vardiya kırılımı (08:00–16:00, 16:00–00:00, 00:00–08:00)
- **Program bazlı süreler** — hangi programı toplam kaç dakika çalıştırdınız
- En uzun duruşlar, sebepleriyle
- Alarm listesi

**CSV indir** düğmesi Excel'de açılabilen bir dosya verir (Türkçe Excel'de
sütunlar doğru ayrılır).

### Terminalden

```bash
sh baslat.sh --rapor bugun
sh baslat.sh --rapor dun
sh baslat.sh --rapor 2026-08-10
```

Bu komut program çalışırken de kullanılabilir, kaydı bozmaz.

### "Program bazlı süreler" tablosundaki iki sütun

- **Çalışma** — o program yüklüyken tezgahın gerçekten çalıştığı süre
- **Yüklü kalma** — o programın yüklü olduğu toplam süre (duruşlar dahil)

"Programı toplam kaç dakika çalıştırdık" sorusunun cevabı **Çalışma**
sütunudur.

---

## 7. Günlük rutin

**Vardiya başında:** Program açık mı diye bakın. Değilse menüden **CNC Log**.
Üstteki kutu yeşil/turuncu ise bağlantı var demektir.

**Vardiya boyunca:** Hiçbir şey yapmanıza gerek yok. İsterseniz alt-tab ile
bakabilirsiniz.

**Vardiya sonunda:** İsterseniz **Rapor** sekmesinden günün özetine bakın.

**Programı kapatmayın.** Sürekli açık kalması gerekir; kapalıyken geçen süre
"BAĞLANTI YOK" olarak kaydedilir ve o süre hakkında hiçbir bilgi kalmaz.

---

## 8. Sık sorulanlar

**Tezgaha zarar verir mi?**
Hayır. Program yalnızca okur. `--test-baglanti` çıktısı da her seferinde
"Tezgaha hiçbir şey yazılmadı" der.

**Tezgahı yavaşlatır mı?**
Hayır. 2 saniyede bir küçük bir okuma yapar; tezgahın işlemcisine yükü ihmal
edilebilir düzeydedir.

**İnternet gerekiyor mu?**
Hayır. Hiçbir aşamada. Program hiçbir yere veri göndermez.

**Ekranı kapatırsam kayıt durur mu?**
Hayır. Tarayıcı penceresini kapatabilirsiniz, kayıt arka planda devam eder.
Kayıt sadece terminalde **Ctrl+C** yapınca veya program kapanınca durur.

**Verileri nereye kaydediyor?**
Program klasöründeki `veri/` dizinine. Metin logları `veri/loglar/` içinde,
tarih adıyla. Bunları herhangi bir metin düzenleyiciyle açabilirsiniz.

**Kaç gün geriye bakabilirim?**
Duruş, alarm ve program kayıtları **hiç silinmez**. Ayrıntılı ölçüm kayıtları
90 gün saklanır (ayarlanabilir).

**Başka bir bilgisayardan bakabilir miyim?**
Evet, ama önce ayar gerekir — bkz. KURULUM.md 5. bölüm. Şifre koruması
olmadığını unutmayın.

**Yanlış bir şeye tıklarsam bozulur mu?**
Hayır. Arayüzde hiçbir düğme tezgahı etkilemez; sadece görüntüleme ve rapor
vardır. Programın kendisi de hiçbir şey yazamaz.

---

## Yardım gerekirse

Bir sorun olursa şunu çalıştırıp çıktısını gönderin — hiçbir şeyi değiştirmez:

```bash
sh kurulum/kesif.sh
```

Tezgah bulunamıyorsa:

```bash
sh baslat.sh --tara
```
