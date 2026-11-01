#!/bin/bash
# Install ZELLOOTEC plymouth splash (needs sudo once).
set -euo pipefail
ROOT=/home/pi/fingerprint
SRC="$ROOT/scripts/plymouth/zellootec"
DEST=/usr/share/plymouth/themes/zellootec

if [[ ! -f "$SRC/splash.png" && -f "$ROOT/ui/static/img/zellootec.jpg" ]]; then
  if command -v convert >/dev/null 2>&1; then
    convert "$ROOT/ui/static/img/zellootec.jpg" -resize 1200x700 -gravity center -extent 1920x1080 -background white "$SRC/splash.png"
  elif python3 -c "from PIL import Image" 2>/dev/null; then
    python3 - <<PY
from PIL import Image
im = Image.open("$ROOT/ui/static/img/zellootec.jpg").convert("RGBA")
bg = Image.new("RGBA", (1920, 1080), (255, 255, 255, 255))
w, h = im.size
s = min(1200 / w, 700 / h)
nw, nh = int(w * s), int(h * s)
im = im.resize((nw, nh), Image.LANCZOS)
bg.paste(im, ((1920 - nw) // 2, (1080 - nh) // 2), im)
bg.convert("RGB").save("$SRC/splash.png", quality=95)
print("splash.png created")
PY
  else
    echo "Need ImageMagick or Pillow to build splash.png"
    exit 1
  fi
fi

sudo mkdir -p "$DEST"
sudo cp "$SRC/zellootec.plymouth" "$SRC/zellootec.script" "$SRC/splash.png" "$DEST/"
sudo sed -i 's/\r$//' "$DEST/zellootec.plymouth" "$DEST/zellootec.script"

# Point default theme to zellootec
if [[ -e /etc/alternatives/default.plymouth ]]; then
  sudo ln -sfn "$DEST/zellootec.plymouth" /etc/alternatives/default.plymouth
fi
if [[ -f /etc/plymouth/plymouthd.conf ]]; then
  sudo sed -i 's/^Theme=.*/Theme=zellootec/' /etc/plymouth/plymouthd.conf || true
  if ! grep -q '^Theme=' /etc/plymouth/plymouthd.conf; then
    printf '\n[Daemon]\nTheme=zellootec\n' | sudo tee -a /etc/plymouth/plymouthd.conf >/dev/null
  fi
fi

# Keep quiet splash on cmdline
if [[ -f /boot/firmware/cmdline.txt ]] && ! grep -q 'splash' /boot/firmware/cmdline.txt; then
  sudo sed -i 's/$/ quiet splash plymouth.ignore-serial-consoles/' /boot/firmware/cmdline.txt
fi

if command -v update-initramfs >/dev/null 2>&1; then
  sudo update-initramfs -u
fi

echo "Boot splash installed: zellootec"
