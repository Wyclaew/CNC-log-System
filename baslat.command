#!/bin/sh
# CNC Log System - macOS baslatici
#
# Finder'da bu dosyaya CIFT TIKLAYIN. Terminal acilir ve program baslar.
# Kapatmak icin terminal penceresinde Ctrl+C yapin.
#
# Not: macOS ilk acilista "gelistirici dogrulanamadi" diyebilir. O zaman
# dosyaya sag tiklayip "Ac" (Open) secin.

KLASOR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
cd "$KLASOR" || exit 1

PY=""
for aday in python3 /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if command -v "$aday" >/dev/null 2>&1; then
        PY="$aday"
        break
    fi
done

if [ -z "$PY" ]; then
    echo "HATA: python3 bulunamadi."
    echo "Terminal'de 'xcode-select --install' calistirip tekrar deneyin."
    echo ""
    echo "Kapatmak icin bu pencereyi kapatabilirsiniz."
    read -r _
    exit 1
fi

"$PY" -m cnclog "$@"

echo ""
echo "Program kapandi. Bu pencereyi kapatabilirsiniz."
read -r _
