#!/bin/sh
# CNC Log System - baslatici (Linux, macOS, Windows/Git Bash)
#
# Kullanim:
#     ./baslat.sh                 normal calistir (arayuz acilir)
#     ./baslat.sh --tara          tezgahi agda ara
#     ./baslat.sh --test-baglanti tezgaha baglanmayi dene
#     ./baslat.sh --rapor bugun   gun raporunu ekrana bas
#     ./baslat.sh --python-bilgi  hangi Python kullanilacagini goster
#
# Windows'ta CMD veya PowerShell kullaniyorsaniz baslat.bat dosyasini
# calistirin; bu dosya Git Bash icindir.
#
# PYTHON HAKKINDA
# ---------------
# Program Python 3.7+ gerektirir. Heidenhain HEROS uzerinde sistem Python'u
# 2.7'dir, bazi makinelerde hic yoktur. Bu yuzden paketin icinde tasinabilir
# bir Python 3.11 gelir (cnclog/vendor/python/). Sisteme HICBIR SEY KURULMAZ:
# arsiv yazilabilir bir klasore acilir ve oradan calistirilir.
#
# Sira: sistem python3 -> daha once acilmis gomulu -> arsivden ac.

case "$0" in
    */*) BETIK_DIZIN="${0%/*}" ;;
    *)   BETIK_DIZIN="." ;;
esac
KLASOR=$(cd "$BETIK_DIZIN" 2>/dev/null && pwd)
if [ -z "$KLASOR" ] || ! cd "$KLASOR"; then
    echo "HATA: program klasorune girilemedi: $BETIK_DIZIN" >&2
    exit 1
fi

GOMULU_DIZIN="$KLASOR/cnclog/vendor/python"
BILGI_MODU=0
for arg in "$@"; do
    [ "$arg" = "--python-bilgi" ] && BILGI_MODU=1
done
bilgi() { [ "$BILGI_MODU" = "1" ] && echo "  $1"; }

# Windows mi? Git Bash / MSYS / Cygwin altinda calisiyor olabiliriz.
SISTEM=$(uname -s 2>/dev/null || echo bilinmiyor)
WINDOWS=0
case "$SISTEM" in
    *NT*|MINGW*|MSYS*|CYGWIN*|Windows*) WINDOWS=1 ;;
esac

# Acilmis bir dagitimda python calistiricisinin yolu. Windows'ta python.exe
# koktedir, Unix'te bin/python3 altindadir.
python_yolu() {
    if [ -x "$1/python/bin/python3" ]; then
        echo "$1/python/bin/python3"
    elif [ -f "$1/python/python.exe" ]; then
        echo "$1/python/python.exe"
    fi
}

# Bir Python'un gercekten ise yarayip yaramadigini dener: sadece "var mi"
# degil, surumu yeterli mi ve gerekli moduller iceride mi.
python_uygun_mu() {
    [ -n "$1" ] || return 1
    "$1" - <<'PYEOF' >/dev/null 2>&1
import sys
if sys.version_info < (3, 7):
    raise SystemExit(1)
import sqlite3, socket, json, threading, configparser, csv, http.server
raise SystemExit(0)
PYEOF
}

PY=""

# --- 1) Sistemde kullanilabilir bir Python 3 var mi? ---------------------
for aday in python3 python3.13 python3.12 python3.11 python3.10 python3.9 \
            python3.8 python3.7 python; do
    YOL=$(command -v "$aday" 2>/dev/null) || continue
    if python_uygun_mu "$YOL"; then
        PY="$YOL"
        bilgi "sistem Python bulundu: $YOL"
        break
    fi
    bilgi "atlandi (surum veya modul eksik): $YOL"
done

# --- 2) Daha once acilmis gomulu Python var mi? --------------------------
if [ -z "$PY" ]; then
    for kok in "$GOMULU_DIZIN/rt" "$HOME/.cnclog/python"; do
        ADAY=$(python_yolu "$kok")
        if python_uygun_mu "$ADAY"; then
            PY="$ADAY"
            bilgi "gomulu Python kullaniliyor: $PY"
            break
        fi
    done
fi

# --- 3) Arsivden ac ------------------------------------------------------
if [ -z "$PY" ]; then
    if ! command -v tar >/dev/null 2>&1; then
        echo "HATA: 'tar' komutu yok, gomulu Python acilamiyor." >&2
        exit 1
    fi

    # Yazilabilir bir hedef sec. USB salt-okunur baglanmis olabilir.
    HEDEF=""
    for aday in "$GOMULU_DIZIN/rt" "$HOME/.cnclog/python"; do
        if mkdir -p "$aday" 2>/dev/null && [ -w "$aday" ]; then
            HEDEF="$aday"
            break
        fi
    done
    if [ -z "$HEDEF" ]; then
        echo "HATA: Python'u acacak yazilabilir bir klasor bulunamadi." >&2
        echo "      Program klasorunu ev dizinine kopyalayip tekrar deneyin." >&2
        exit 1
    fi

    # Platforma uygun arsivleri sirayla dene. Linux'ta once musl (tamamen
    # statik, sistem kutuphanelerinden bagimsiz), sonra glibc surumu.
    if [ "$WINDOWS" = "1" ]; then
        ADAYLAR="windows"
    else
        ADAYLAR="linux-musl linux-gnu"
    fi

    for tip in $ADAYLAR; do
        ARSIV="$GOMULU_DIZIN/cpython-$tip.tar.gz"
        [ -f "$ARSIV" ] || continue
        echo "Python 3 hazirlaniyor ($tip)... ilk calistirmada bir defa yapilir."
        rm -rf "$HEDEF/python" 2>/dev/null
        if tar xzf "$ARSIV" -C "$HEDEF" 2>/dev/null; then
            ADAY=$(python_yolu "$HEDEF")
            if python_uygun_mu "$ADAY"; then
                PY="$ADAY"
                echo "Hazir: $PY"
                break
            fi
            bilgi "$tip derlemesi bu sistemde calismadi"
        fi
        rm -rf "$HEDEF/python" 2>/dev/null
    done
fi

# --- 4) Hicbiri olmadi ---------------------------------------------------
if [ -z "$PY" ]; then
    echo "============================================================" >&2
    echo " HATA: Calisir bir Python 3 bulunamadi." >&2
    echo "" >&2
    echo " Sistem: $SISTEM" >&2
    if [ "$WINDOWS" = "1" ]; then
        echo " Windows'ta CMD veya PowerShell acip su dosyayi deneyin:" >&2
        echo "     baslat.bat" >&2
    else
        echo " Su komutu calistirip ciktisini gonderin:" >&2
        echo "     sh kurulum/kesif.sh" >&2
    fi
    echo "============================================================" >&2
    exit 1
fi

if [ "$BILGI_MODU" = "1" ]; then
    echo "Kullanilacak Python: $PY"
    "$PY" --version
    echo "Sistem             : $SISTEM"
    echo "Program klasoru    : $KLASOR"
    exit 0
fi

exec "$PY" -m cnclog "$@"
