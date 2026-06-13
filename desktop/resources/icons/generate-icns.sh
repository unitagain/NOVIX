#!/usr/bin/env bash
# generate-icns.sh — Convert SVG icon to macOS .icns via iconutil.
# Run on macOS only.  Requires: sips (built-in) and iconutil (built-in).
#
# Usage:
#   cd desktop && bash resources/icons/generate-icns.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SVG_SOURCE="$SCRIPT_DIR/wenshape-icon.svg"
ICONSET_DIR="$SCRIPT_DIR/wenshape.iconset"
ICNS_OUTPUT="$SCRIPT_DIR/wenshape.icns"

# Prefer rsvg-convert (Homebrew: librsvg), fall back to sips (cannot read SVG).
# If neither can handle SVG, the user must supply a 1024x1024 PNG manually.
PNG_1024="$SCRIPT_DIR/_wenshape_1024.png"

if command -v rsvg-convert &>/dev/null; then
  rsvg-convert -w 1024 -h 1024 "$SVG_SOURCE" -o "$PNG_1024"
elif command -v magick &>/dev/null; then
  magick "$SVG_SOURCE" -resize 1024x1024 "$PNG_1024"
elif [ -f "$SCRIPT_DIR/wenshape-1024.png" ]; then
  cp "$SCRIPT_DIR/wenshape-1024.png" "$PNG_1024"
else
  echo "[generate-icns] ERROR: no SVG rasterizer found and no wenshape-1024.png fallback."
  echo "  Install librsvg (brew install librsvg) or supply a 1024x1024 PNG."
  exit 1
fi

rm -rf "$ICONSET_DIR"
mkdir -p "$ICONSET_DIR"

# Apple requires these exact sizes for .iconset
for SIZE in 16 32 64 128 256 512; do
  sips -z "$SIZE" "$SIZE" "$PNG_1024" --out "$ICONSET_DIR/icon_${SIZE}x${SIZE}.png" >/dev/null
  DOUBLE=$((SIZE * 2))
  sips -z "$DOUBLE" "$DOUBLE" "$PNG_1024" --out "$ICONSET_DIR/icon_${SIZE}x${SIZE}@2x.png" >/dev/null
done
# 512@2x is 1024
cp "$PNG_1024" "$ICONSET_DIR/icon_512x512@2x.png"

iconutil -c icns -o "$ICNS_OUTPUT" "$ICONSET_DIR"
rm -rf "$ICONSET_DIR" "$PNG_1024"

echo "[generate-icns] $ICNS_OUTPUT created successfully"
