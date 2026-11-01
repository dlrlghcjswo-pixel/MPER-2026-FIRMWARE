#!/bin/bash
# Install UI fonts for Chromium/VNC and restart kiosk with clean cache.
set -euo pipefail
ROOT=/home/pi/fingerprint
FONTDIR="$HOME/.local/share/fonts/fingerprint"
mkdir -p "$FONTDIR"

cp -f "$ROOT/ui/static/fonts/nanum-gothic.ttf" "$FONTDIR/NanumGothic.ttf"
cp -f "$ROOT/ui/static/fonts/nanum-gothic-bold.ttf" "$FONTDIR/NanumGothicBold.ttf"
fc-cache -f "$FONTDIR" 2>/dev/null || fc-cache -f

echo "Installed fonts:"
fc-list | grep -iE 'Gungsuh|NanumMyeongjo|HYgsr|Nanum' || true

# Clear Chromium cache so old broken font face is not reused
pkill -f "chromium.*(127\.0\.0\.1:8080|boot-splash|kiosk)" 2>/dev/null || true
sleep 1
rm -rf "$HOME/.cache/chromium/Default/Cache" \
       "$HOME/.cache/chromium/Default/Code Cache" \
       "$HOME/.config/chromium/Default/GPUCache" 2>/dev/null || true

nohup "$ROOT/scripts/run_kiosk.sh" >/dev/null 2>&1 &
echo "kiosk restarted"
sleep 3
pgrep -a chromium | head -2 || echo "chromium not up yet"
