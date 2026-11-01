#!/bin/bash
# Start SFM daemon + UI server (used by systemd and manual restart).
set -uo pipefail
ROOT=/home/pi/fingerprint
FINGER_ID=SmartLocker
PORT=""
SOCK="$ROOT/run/sfm.sock"
mkdir -p "$ROOT/run" "$ROOT/logs" "$ROOT/data"

# SmartLocker FT232 first, then direct USB ttyACM.
shopt -s nullglob
for i in $(seq 1 60); do
  PORT=""
  for link in /dev/serial/by-id/*"${FINGER_ID}"*; do
    if [[ -e "$link" ]]; then
      PORT=$(readlink -f "$link")
      break
    fi
  done
  if [[ -z "$PORT" || ! -e "$PORT" ]]; then
    PORT=""
    for acm in /dev/ttyACM[0-9]*; do
      if [[ -c "$acm" ]]; then
        PORT="$acm"
        break
      fi
    done
  fi
  if [[ -n "$PORT" && -e "$PORT" ]]; then
    break
  fi
  PORT=""
  sleep 1
done
if [[ -z "$PORT" || ! -e "$PORT" ]]; then
  echo "fingerprint port not found (SmartLocker or ttyACM)" >> "$ROOT/logs/sfm_daemon.log"
  exit 1
fi

pkill -9 -x sfm_daemon 2>/dev/null || true
rm -f "$SOCK" "$ROOT/run/sfm_daemon.pid"
sleep 0.3

exec "$ROOT/bin/sfm_daemon" "$PORT" "$SOCK"
