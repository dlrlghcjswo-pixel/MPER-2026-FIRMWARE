#!/bin/bash
# Force the design resolution (1280x800) on the headless (VNC) output only.
# Real panels (DSI 800x480, HDMI) keep their native mode; the UI scales itself.
set -uo pipefail
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

DESIGN_W=1280
DESIGN_H=800

if ! command -v wlr-randr >/dev/null 2>&1; then
  echo "no-wlr"
  exit 0
fi

for _ in $(seq 1 20); do
  if wlr-randr >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

apply_mode() {
  local out="$1"
  wlr-randr --output "$out" --mode "${DESIGN_W}x${DESIGN_H}" >/dev/null 2>&1 && return 0
  wlr-randr --output "$out" --custom-mode "${DESIGN_W}x${DESIGN_H}" >/dev/null 2>&1 && return 0
  wlr-randr --output "$out" --custom-mode 1280x720 >/dev/null 2>&1 && return 0
  return 1
}

mapfile -t LINES < <(wlr-randr 2>/dev/null || true)
OUT=""
KIND="unknown"
ok=0
for line in "${LINES[@]:-}"; do
  if [[ "$line" =~ ^([A-Za-z0-9-]+)[[:space:]] ]]; then
    OUT="${BASH_REMATCH[1]}"
    if [[ "$line" == *"Headless"* ]]; then
      KIND="headless"
      if [[ -n "$OUT" ]] && apply_mode "$OUT"; then
        ok=1
      fi
    else
      KIND="panel"
    fi
  fi
done

if [[ "$ok" == 1 ]]; then
  echo "${KIND}:${DESIGN_W}x${DESIGN_H}"
else
  echo "${KIND}:unchanged"
fi
exit 0
