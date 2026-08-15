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

# Hangi platformdayiz? Rapordaki bazi bolumler buna gore anlam degistirir.
SISTEM=$(uname -s 2>/dev/null || echo bilinmiyor)
PLATFORM=linux
case "$SISTEM" in
    *NT*|MINGW*|MSYS*|CYGWIN*|Windows*) PLATFORM=windows ;;
    Darwin*)                            PLATFORM=macos ;;
esac

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
if [ "$PLATFORM" = "windows" ]; then
    echo "  Bu bir WINDOWS makinesi (Git Bash / MSYS altinda calisiyorsunuz)."
    echo "  Bu bolum tezgahin Linux tarafi icindir, burada anlamsizdir."
    echo "  Windows'ta programi CMD/PowerShell'den baslat.bat ile calistirin."
elif [ "$PLATFORM" = "macos" ]; then
    echo "  Bu bir macOS makinesi. Bu bolum tezgah icindir, burada anlamsizdir."
else
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
fi

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
echo "  Sistemde bulunan Python surumleri:"
BULUNAN_PY=""      # herhangi bir python, ag testinde kullanilir
PY3=""             # 3.7+ olan; program bunu ister
for py in python3 python3.13 python3.12 python3.11 python3.10 python3.9 \
          python3.8 python3.7 python python2; do
    command -v "$py" >/dev/null 2>&1 || continue
    SURUM=$("$py" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)
    [ -z "$SURUM" ] && continue
    echo "    $py -> $SURUM   ($(command -v "$py"))"
    [ -z "$BULUNAN_PY" ] && BULUNAN_PY="$py"
    if [ -z "$PY3" ] && "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3,7) else 1)' 2>/dev/null; then
        PY3="$py"
    fi
done
[ -z "$BULUNAN_PY" ] && echo "    (hicbiri bulunamadi)"

echo
if [ -n "$PY3" ]; then
    echo "  SONUC: Python 3.7+ VAR ($PY3) - program dogrudan bunu kullanir."
    echo
    echo "  Gerekli modul kontrolu ($PY3 ile):"
    for mod in sqlite3 http.server json threading configparser csv fcntl socket; do
        if "$PY3" -c "import $mod" 2>/dev/null; then
            echo "    $mod : VAR"
        else
            echo "    $mod : YOK  <-- onemli"
        fi
    done
else
    echo "  SONUC: Sistemde Python 3.7+ YOK (sadece Python 2 var veya hic yok)."
    echo "         SORUN DEGIL: program kendi tasinabilir Python 3'unu getirir,"
    echo "         sisteme hicbir sey kurulmaz. Ilk calistirmada bir defa acilir."
fi

echo
echo "  Pakete gomulu tasinabilir Python arsivleri:"
case "$0" in
    */*) BETIK_DIZIN="${0%/*}" ;;
    *)   BETIK_DIZIN="." ;;
esac
KOK=$(cd "$BETIK_DIZIN/.." 2>/dev/null && pwd)
GOMULU_VAR=0
if [ "$PLATFORM" = "windows" ]; then
    GEREKLI=windows
else
    GEREKLI=linux-musl
fi
for tip in linux-musl linux-gnu windows; do
    A="$KOK/cnclog/vendor/python/cpython-$tip.tar.gz"
    ISARET=""
    [ "$tip" = "$GEREKLI" ] && ISARET="   <-- bu makine icin gerekli olan"
    if [ -f "$A" ]; then
        echo "    $tip : VAR ($(du -h "$A" 2>/dev/null | cut -f1))$ISARET"
        [ "$tip" = "$GEREKLI" ] && GOMULU_VAR=1
    else
        echo "    $tip : yok$ISARET"
    fi
done
if [ "$GOMULU_VAR" = "0" ] && [ -z "$PY3" ]; then
    echo
    echo "    !!! Ne sistemde Python 3 var ne de bu platform icin gomulu arsiv."
    echo "        Program calismaz. Paketin eksiksiz kopyalandigindan emin olun."
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
    if [ "$PLATFORM" = "windows" ]; then
        ipconfig 2>/dev/null | grep -iE "IPv4|IP Address" | sed 's/^/    /' | head -6
    else
        (ip -4 addr show 2>/dev/null | grep inet || ifconfig 2>/dev/null | grep 'inet ') \
            | sed 's/^/    /' | head -6
    fi
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
if [ -n "$PY3" ]; then
    echo "  * Sistemde Python 3 var -> program dogrudan calisir."
elif [ "$GOMULU_VAR" = "1" ]; then
    echo "  * Sistemde Python 3 yok ama pakete gomulu surum var -> program"
    echo "    yine calisir. Ilk acilista Python bir defa hazirlanir."
else
    echo "  * Ne sistemde Python 3 var ne de bu platform icin gomulu arsiv."
    echo "    Program BU MAKINEDE CALISAMAZ. Paket eksik kopyalanmis olabilir."
fi
if [ "$PLATFORM" = "windows" ]; then
    echo
    echo "  * Windows'ta calistirmak icin CMD veya PowerShell acip:"
    echo "        baslat.bat"
    echo "    (Git Bash'ten 'sh baslat.sh' da calisir.)"
    echo "  * Windows'ta esas amac TNC 640 Programming Station ile denemektir."
fi
echo
echo "  BUNLARI DA KONTROL EDIN (bu betik goremez, tezgah ekranindan bakin):"
echo "    - TNC ekraninda MOD tusuna basin, lisansli opsiyonlari listeleyin."
echo "      Ozellikle su numaralar onemli:"
echo "         Opsiyon 18  = DNC        -> veri okumak icin GEREKLI"
echo "         Opsiyon 56  = OPC UA     -> gercek F/S degerleri icin"
echo "         Opsiyon 133 = Remote Desktop Manager  (bu tezgahta VAR)"
echo "    - Ag ayarlarinda LSV2 / DNC erisimi acik mi?"
echo
echo "  SONRAKI ADIM: tezgahi otomatik aratmak icin"
echo "      sh baslat.sh --tara"
echo
echo "============================================================"
echo "  RAPOR SONU - bu ciktinin TAMAMINI kopyalayip gonderin"
echo "============================================================"
