#!/bin/sh
# CNC Log System - XFCE menusune ve masaustune kisayol ekler.
#
# Root gerekmez; her sey kullanicinin kendi klasorune yazilir.
# Kaldirmak icin:  sh kurulum/menuye-ekle.sh --kaldir
#
# Yaptigi tek sey bir .desktop dosyasi olusturmaktir. Sisteme baska hicbir
# degisiklik yapmaz.

KLASOR=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
HEDEF="$HOME/.local/share/applications/cnclog.desktop"
MASAUSTU_DIZIN=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")
MASAUSTU="$MASAUSTU_DIZIN/cnclog.desktop"

if [ "$1" = "--kaldir" ]; then
    rm -f "$HEDEF" "$MASAUSTU"
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$HOME/.local/share/applications" 2>/dev/null
    echo "Kisayollar kaldirildi."
    exit 0
fi

if [ ! -f "$KLASOR/baslat.sh" ]; then
    echo "HATA: baslat.sh bulunamadi ($KLASOR)" >&2
    exit 1
fi
chmod +x "$KLASOR/baslat.sh" 2>/dev/null

mkdir -p "$(dirname "$HEDEF")" || exit 1

# .desktop sablonundaki ornek yollari gercek klasorle degistir.
sed -e "s|^Exec=.*|Exec=$KLASOR/baslat.sh|" \
    -e "s|^Path=.*|Path=$KLASOR|" \
    "$KLASOR/kurulum/cnclog.desktop" > "$HEDEF" || exit 1
chmod +x "$HEDEF"

echo "Menuye eklendi : $HEDEF"

if [ -d "$MASAUSTU_DIZIN" ]; then
    cp "$HEDEF" "$MASAUSTU" 2>/dev/null && chmod +x "$MASAUSTU" && \
        echo "Masaustune eklendi: $MASAUSTU"
fi

command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null

echo
echo "Artik XFCE menusunde 'CNC Log' olarak gorunecek."
echo "Menude gorunmezse oturumu kapatip acin."
