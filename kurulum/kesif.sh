#!/bin/sh
# CNC Log System — kesif (tesbit) betigi
#
# Bu betik HICBIR SEY DEGISTIRMEZ. Sadece okur ve ekrana yazar.
# Tezgahta veya tezgahin yanindaki bilgisayarda calistirin, ciktinin
# tamamini kopyalayip geri gonderin.
#
# Kullanim:
#     sh kesif.sh                 (ag testi yapmadan)
#     sh kesif.sh 192.168.1.50    (tezgahin IP adresi ile)
#
# ASCII kullanilmistir; eski/kisitli terminallerde de duzgun gorunsun diye.

TNC_IP="$1"

cizgi() { echo "------------------------------------------------------------"; }
baslik() { echo; cizgi; echo "  $1"; cizgi; }

echo "============================================================"
echo "  CNC LOG SYSTEM - SISTEM KESIF RAPORU"
echo "  Tarih: $(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo bilinmiyor)"
echo "============================================================"

# ---------------------------------------------------------------- 1. SISTEM
baslik "1. ISLETIM SISTEMI"
echo "  uname -a:"
echo "    $(uname -a 2>/dev/null || echo 'okunamadi')"
if [ -r /etc/os-release ]; then
    echo "  /etc/os-release:"
    sed 's/^/    /' /etc/os-release | head -8
else
    echo "  /etc/os-release yok"
fi
echo "  Mimari : $(uname -m 2>/dev/null)"
echo "  Kullanici: $(id -un 2>/dev/null) (uid $(id -u 2>/dev/null))"

# ------------------------------------------------------- 2. MASAUSTU ORTAMI
baslik "2. MASAUSTU ORTAMI  (XFCE mi, HEROS mu?)"
echo "  XDG_CURRENT_DESKTOP : ${XDG_CURRENT_DESKTOP:-tanimsiz}"
echo "  DESKTOP_SESSION     : ${DESKTOP_SESSION:-tanimsiz}"
echo "  DISPLAY             : ${DISPLAY:-tanimsiz}"
echo "  Calisan pencere yoneticileri / masaustu surecleri:"
if command -v ps >/dev/null 2>&1; then
    ps ax 2>/dev/null \
      | grep -iE 'xfce|xfwm|xfdesktop|heros|kwin|gnome-shell|openbox|fluxbox|icewm|jwm' \
      | grep -v grep | awk '{print "    " $5, $6}' | sort -u | head -10
    if [ -z "$(ps ax 2>/dev/null | grep -iE 'xfce|heros' | grep -v grep)" ]; then
        echo "    (xfce veya heros sureci bulunamadi)"
    fi
fi
echo "  HEROS izleri:"
for p in /opt/heros /HEROS /usr/share/heros /mnt/tnc /TNC; do
    [ -e "$p" ] && echo "    VAR: $p"
done
echo "    (yukarida hicbir satir yoksa HEROS degil, normal bir Linux)"

# ------------------------------------------------------------- 3. GUVENLIK
baslik "3. GUVENLIK KISITLARI (kurulum yapilabilir mi?)"
if command -v getenforce >/dev/null 2>&1; then
    echo "  SELinux : $(getenforce 2>/dev/null)"
elif [ -r /sys/fs/selinux/enforce ]; then
    echo "  SELinux : enforce=$(cat /sys/fs/selinux/enforce 2>/dev/null)"
else
    echo "  SELinux : tespit edilemedi (muhtemelen yok)"
fi
if command -v aa-status >/dev/null 2>&1; then
    echo "  AppArmor: kurulu"
fi
if [ "$(id -u 2>/dev/null)" = "0" ]; then
    echo "  Root yetkisi: VAR (root olarak calisiyorsunuz)"
elif command -v sudo >/dev/null 2>&1; then
    echo "  sudo komutu: var (yetki olup olmadigi denenmedi)"
else
    echo "  Root yetkisi: yok, sudo da yok"
fi

# --------------------------------------------------------------- 4. PYTHON
baslik "4. PYTHON  (program bunu kullanacak)"
BULUNAN_PY=""
for py in python3 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7 python; do
    if command -v "$py" >/dev/null 2>&1; then
        SURUM=$("$py" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)
        [ -z "$SURUM" ] && continue
        echo "  $py -> $SURUM   ($(command -v "$py"))"
        [ -z "$BULUNAN_PY" ] && BULUNAN_PY="$py"
    fi
done
if [ -z "$BULUNAN_PY" ]; then
    echo "  !!! PYTHON BULUNAMADI - program bu makinede calisamaz."
else
    echo
    echo "  Gerekli modul kontrolu ($BULUNAN_PY ile):"
    for mod in sqlite3 http.server json threading configparser csv fcntl; do
        if "$BULUNAN_PY" -c "import $mod" 2>/dev/null; then
            echo "    $mod : VAR"
        else
            echo "    $mod : YOK  <-- onemli"
        fi
    done
    echo
    echo "  Surum yeterli mi (en az 3.7 gerekiyor)?"
    if "$BULUNAN_PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,7) else 1)' 2>/dev/null; then
        echo "    EVET"
    else
        echo "    HAYIR - Python 3.7 veya uzeri gerekli"
    fi
fi

# ---------------------------------------------------------- 5. YAZMA IZNI
baslik "5. YAZMA IZNI (veriler nereye kaydedilebilir?)"
for d in "$HOME" "$HOME/cnclog" /tmp /var/tmp .; do
    [ -z "$d" ] && continue
    if [ -d "$d" ] && [ -w "$d" ]; then
        echo "  YAZILABILIR : $d"
    elif [ -d "$d" ]; then
        echo "  yazilamaz   : $d"
    fi
done
DENEME="$HOME/.cnclog_yazma_denemesi"
if echo test > "$DENEME" 2>/dev/null; then
    echo "  Ev dizinine dosya yazma: BASARILI"
    rm -f "$DENEME" 2>/dev/null
else
    echo "  Ev dizinine dosya yazma: BASARISIZ  <-- onemli"
fi
echo "  Bos alan:"
df -h "$HOME" 2>/dev/null | sed 's/^/    /' | head -3

# ------------------------------------------------------------------ 6. AG
baslik "6. TEZGAH BAGLANTISI"
if [ -z "$TNC_IP" ]; then
    echo "  Tezgah IP adresi verilmedi."
    echo "  Test icin soyle calistirin:   sh kesif.sh 192.168.1.50"
    echo
    echo "  Bu bilgisayarin ag adresleri:"
    (ip -4 addr show 2>/dev/null | grep inet || ifconfig 2>/dev/null | grep 'inet ') \
        | sed 's/^/    /' | head -6
else
    echo "  Hedef tezgah: $TNC_IP"
    if command -v ping >/dev/null 2>&1; then
        if ping -c 2 -W 2 "$TNC_IP" >/dev/null 2>&1; then
            echo "  ping        : ULASILIYOR"
        else
            echo "  ping        : ULASILAMIYOR (ping kapali da olabilir)"
        fi
    fi
    for port_bilgi in "19000 LSV2 (ucretsiz surucu icin)" "4840 OPC UA (Opsiyon 56 icin)"; do
        port=$(echo "$port_bilgi" | cut -d' ' -f1)
        aciklama=$(echo "$port_bilgi" | cut -d' ' -f2-)
        sonuc="test edilemedi"
        if command -v nc >/dev/null 2>&1; then
            nc -z -w 3 "$TNC_IP" "$port" >/dev/null 2>&1 && sonuc="ACIK" || sonuc="kapali"
        elif [ -n "$BULUNAN_PY" ]; then
            sonuc=$("$BULUNAN_PY" - "$TNC_IP" "$port" <<'PYEOF' 2>/dev/null
import socket, sys
s = socket.socket()
s.settimeout(3)
try:
    s.connect((sys.argv[1], int(sys.argv[2])))
    print("ACIK")
except Exception:
    print("kapali")
finally:
    s.close()
PYEOF
)
        fi
        echo "  port $port  : $sonuc   - $aciklama"
    done
fi

# ------------------------------------------------------------ 7. TARAYICI
baslik "7. TARAYICI (arayuz bununla acilacak)"
BULUNDU=0
for b in chromium chromium-browser google-chrome google-chrome-stable firefox brave-browser midori epiphany; do
    if command -v "$b" >/dev/null 2>&1; then
        echo "  VAR: $b"
        BULUNDU=1
    fi
done
[ "$BULUNDU" = "0" ] && echo "  Tarayici bulunamadi. Arayuz yine calisir, ag uzerinden baska"
[ "$BULUNDU" = "0" ] && echo "  bir bilgisayardan acilabilir."

# ----------------------------------------------------------------- 8. OZET
baslik "8. SONUC"
if [ -z "$BULUNAN_PY" ]; then
    echo "  * Python yok -> program BU MAKINEDE CALISAMAZ."
    echo "    Tezgahin yanindaki baska bir Linux bilgisayara kurulmali."
else
    echo "  * Python var -> program buraya kopyalanip calistirilabilir."
    echo "    Kurulum gerekmez, sadece klasoru kopyalayin."
fi
echo
echo "  BUNLARI DA KONTROL EDIN (bu betik goremez, tezgah ekranindan bakin):"
echo "    - TNC ekraninda MOD tusuna basin, lisansli opsiyonlari listeleyin."
echo "      Ozellikle su numaralar onemli:"
echo "         Opsiyon 18  = DNC        -> veri okumak icin GEREKLI"
echo "         Opsiyon 56  = OPC UA     -> gercek F/S degerleri icin"
echo "         Opsiyon 133 = Remote Desktop Manager"
echo "                                  -> tezgah ekranindan programa bakmak icin"
echo "    - Ag ayarlarinda LSV2 / DNC erisimi acik mi?"
echo
echo "============================================================"
echo "  RAPOR SONU - bu ciktinin TAMAMINI kopyalayip gonderin"
echo "============================================================"
