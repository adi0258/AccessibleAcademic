#!/bin/bash
# Rebuild the poster PDF. Run from this directory:  ./build.sh
set -e
cd "$(dirname "$0")"

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# inline the Hebrew webfont so the PDF is self-contained
python3 - <<'PY'
import io
html = io.open('poster.html', encoding='utf-8').read()
b64  = io.open('heebo.b64').read().strip()
io.open('poster.built.html', 'w', encoding='utf-8').write(html.replace('__HEEBO__', b64))
PY

"$CHROME" --headless=new --disable-gpu --no-pdf-header-footer \
          --virtual-time-budget=20000 \
          --print-to-pdf=poster.pdf poster.built.html 2>/dev/null

# the exhibition guidelines require a JPG; 150 dpi at 70x100 cm
pdftoppm -jpeg -r 150 -jpegopt quality=92 poster.pdf poster-print
mv poster-print-1.jpg "Accessible Academic - poster.jpg"

echo "poster.pdf + 'Accessible Academic - poster.jpg' rebuilt - 700 x 1000 mm @ 150 dpi"
