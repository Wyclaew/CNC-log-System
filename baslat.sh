#!/bin/sh
# CNC Log System - Linux baslatici
#
# Kullanim:
#     ./baslat.sh                 normal calistir (arayuz acilir)
#     ./baslat.sh --web-yok       arayuzsuz, sadece kayit tut
#     ./baslat.sh --test-baglanti tezgaha baglanmayi dene
#     ./baslat.sh --rapor bugun   gun raporunu ekrana bas
#
# Verilen butun parametreler programa oldugu gibi aktarilir.

# Betigin bulundugu klasore gec; program veriyi buraya yazar.
KLASOR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
cd "$KLASOR" || {
    echo "HATA: program klasorune girilemedi: $KLASOR" >&2
    exit 1
}

# Uygun bir Python bul. HEROS gibi kisitli sistemlerde 'python3' olmayabilir.
PY=""
for aday in python3 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7; do
    if command -v "$aday" >/dev/null 2>&1; then
        if "$aday" -c 'import sys; sys.exit(0 if sys.version_info >= (3,7) else 1)' 2>/dev/null; then
            PY="$aday"
            break
        fi
    fi
done

if [ -z "$PY" ]; then
    echo "============================================================" >&2
    echo " HATA: Python 3.7 veya uzeri bulunamadi." >&2
    echo "" >&2
    echo " Bu program calismak icin Python 3 gerektirir." >&2
    echo " Once su komutu calistirip sonucu inceleyin:" >&2
    echo "     sh kurulum/kesif.sh" >&2
    echo "============================================================" >&2
    exit 1
fi

exec "$PY" -m cnclog "$@"
