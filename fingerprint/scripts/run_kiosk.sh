#!/bin/bash
# Open Chromium kiosk after UI is ready. Shows ZELLOOTEC splash while waiting.
set -uo pipefail
ROOT=/home/pi/fingerprint
URL="http://127.0.0.1:8080/?v=20"
SPLASH="file://${ROOT}/splash/boot-splash.html"
LOG="$ROOT/logs/kiosk.log"
mkdir -p "$ROOT/logs" "$ROOT/run"

{
  echo "$(date -Is) kiosk start DISPLAY=${DISPLAY:-} WAYLAND=${WAYLAND_DISPLAY:-} XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-}"
} >> "$LOG"

# Only one kiosk launcher at a time. The lock lives on tmpfs so a power cut cannot leave a stale lock.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
LOCKDIR="$XDG_RUNTIME_DIR/fingerprint-kiosk.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  oldpid="$(cat "$LOCKDIR/pid" 2>/dev/null || true)"
  if [[ -n "$oldpid" ]] && kill -0 "$oldpid" 2>/dev/null && tr '\0' ' ' < "/proc/$oldpid/cmdline" 2>/dev/null | grep -q 'run_kiosk.sh'; then
    echo "$(date -Is) another kiosk launch in progress — exit" >> "$LOG"
    exit 0
  fi
  rm -rf "$LOCKDIR"
  mkdir "$LOCKDIR" 2>/dev/null || exit 0
fi
echo $$ > "$LOCKDIR/pid"
trap 'rm -rf "$LOCKDIR"' EXIT

pkill -f 'wf-panel-pi' 2>/dev/null || true
pkill -f 'waybar' 2>/dev/null || true

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DISPLAY="${DISPLAY:-:0}"

for i in $(seq 1 60); do
  if [[ -n "${WAYLAND_DISPLAY:-}" && -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]]; then
    break
  fi
  if [[ -S "$XDG_RUNTIME_DIR/wayland-0" ]]; then
    export WAYLAND_DISPLAY=wayland-0
    break
  fi
  sleep 0.5
done
echo "$(date -Is) display ready DISPLAY=$DISPLAY WAYLAND=${WAYLAND_DISPLAY:-}" >> "$LOG"

# Unified design resolution (web / HDMI / VNC)
CHROME_SCALE=1
if [[ -x "$ROOT/scripts/fix_display.sh" ]]; then
  disp_mode="$("$ROOT/scripts/fix_display.sh" || true)"
  echo "$(date -Is) display mode=$disp_mode" >> "$LOG"
fi

CHROME=$(command -v chromium || command -v chromium-browser || true)
if [[ -z "$CHROME" ]]; then
  echo "$(date -Is) chromium not found" >> "$LOG"
  exit 1
fi

clear_chrome_singleton() {
  pkill -f "chromium.*(boot-splash|127\.0\.0\.1:8080|kiosk)" 2>/dev/null || true
  sleep 0.4
  rm -f /home/pi/.config/chromium/SingletonLock \
        /home/pi/.config/chromium/SingletonCookie \
        /home/pi/.config/chromium/SingletonSocket 2>/dev/null || true
}

clear_chrome_singleton

nohup "$CHROME" \
  --kiosk \
  --app="$SPLASH" \
  --force-device-scale-factor="$CHROME_SCALE" \
  --noerrdialogs \
  --disable-infobars \
  --no-first-run \
  --password-store=basic \
  --disable-session-crashed-bubble \
  "$SPLASH" >> "$LOG" 2>&1 &
echo $! > /tmp/fingerprint-splash.pid
echo "$(date -Is) splash pid=$(cat /tmp/fingerprint-splash.pid) scale=$CHROME_SCALE" >> "$LOG"

ready=0
waited=0
while [[ "$ready" != 1 ]]; do
  if curl -sf --connect-timeout 1 --max-time 1 "$URL" >/dev/null 2>&1; then
    ready=1
    echo "$(date -Is) UI ready after ${waited}s" >> "$LOG"
    break
  fi
  waited=$((waited + 1))
  if (( waited % 10 == 0 )); then
    echo "$(date -Is) UI not ready after ${waited}s" >> "$LOG"
  fi
  sleep 1
done

pkill -9 -f /usr/lib/chromium/chromium 2>/dev/null || true
sleep 0.6
rm -f /home/pi/.config/chromium/SingletonLock \
      /home/pi/.config/chromium/SingletonCookie \
      /home/pi/.config/chromium/SingletonSocket 2>/dev/null || true

echo "$(date -Is) launching app $URL scale=$CHROME_SCALE" >> "$LOG"
while true; do
  "$CHROME" \
    --kiosk \
    --app="$URL" \
    --force-device-scale-factor="$CHROME_SCALE" \
    --start-fullscreen \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-restore-session-state \
    --check-for-update-interval=31536000 \
    --autoplay-policy=no-user-gesture-required \
    --disable-features=TranslateUI \
    --no-first-run \
    --password-store=basic \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --disk-cache-size=1 \
    --disable-gpu-shader-disk-cache \
    --disable-font-subpixel-positioning \
    --font-render-hinting=medium \
    "$URL" >> "$LOG" 2>&1
  ec=$?
  echo "$(date -Is) chromium exited code=$ec ??restart in 2s" >> "$LOG"
  clear_chrome_singleton
  sleep 2
done
