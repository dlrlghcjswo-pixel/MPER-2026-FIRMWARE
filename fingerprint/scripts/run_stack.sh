#!/bin/bash
# Robust boot starter: UI always up; daemon retries when serial appears.
set -uo pipefail
ROOT=/home/pi/fingerprint
FINGER_ID=SmartLocker
PORT=""
SOCK="$ROOT/run/sfm.sock"
LOGD="$ROOT/logs/sfm_daemon.log"
LOGU="$ROOT/logs/ui.log"
mkdir -p "$ROOT/run" "$ROOT/logs" "$ROOT/data"

UIPID=""
DPID=""

cleanup() {
  if [[ -n "${UIPID:-}" ]]; then kill -TERM "$UIPID" 2>/dev/null || true; fi
  if [[ -n "${DPID:-}" ]]; then kill -TERM "$DPID" 2>/dev/null || true; fi
  sleep 0.4
  if [[ -n "${UIPID:-}" ]]; then kill -KILL "$UIPID" 2>/dev/null || true; fi
  if [[ -n "${DPID:-}" ]]; then kill -KILL "$DPID" 2>/dev/null || true; fi
  pkill -9 -x sfm_daemon 2>/dev/null || true
  exit 0
}
trap cleanup TERM INT EXIT

resolve_finger_port() {
  local link acm
  PORT=""
  shopt -s nullglob
  for link in /dev/serial/by-id/*"${FINGER_ID}"*; do
    if [[ -e "$link" ]]; then
      PORT=$(readlink -f "$link")
      if [[ -n "$PORT" && -e "$PORT" ]]; then
        return 0
      fi
    fi
  done
  # Direct USB (SFM Slim CDC). Locker board stays on ttyUSB/FT232.
  for acm in /dev/ttyACM[0-9]*; do
    if [[ -c "$acm" ]]; then
      PORT="$acm"
      return 0
    fi
  done
  PORT=""
  return 1
}

start_ui() {
  if [[ -n "${UIPID:-}" ]] && kill -0 "$UIPID" 2>/dev/null; then
    return 0
  fi
  export SFM_EXTERNAL_DAEMON=1
  export FP_ROOT="$ROOT"
  if [[ ! -x "$ROOT/bin/locker-ui" ]]; then
    echo "$(date -Is) locker-ui missing" >> "$LOGU"
    return 0
  fi
  "$ROOT/bin/locker-ui" >> "$LOGU" 2>&1 &
  UIPID=$!
  echo "$(date -Is) ui started pid=$UIPID" >> "$LOGU"
}

start_daemon() {
  if [[ ! -e "$PORT" ]]; then
    return 1
  fi
  if [[ -n "${DPID:-}" ]] && kill -0 "$DPID" 2>/dev/null; then
    return 0
  fi
  pkill -9 -x sfm_daemon 2>/dev/null || true
  rm -f "$SOCK" "$ROOT/run/sfm_daemon.pid"
  sleep 0.2
  "$ROOT/bin/sfm_daemon" "$PORT" "$SOCK" >> "$LOGD" 2>&1 &
  DPID=$!
  echo "$DPID" > "$ROOT/run/sfm_daemon.pid"
  echo "$(date -Is) daemon started pid=$DPID port=$PORT" >> "$LOGD"

  local ok=0
  local i
  for i in $(seq 1 40); do
    if [[ -S "$SOCK" ]] && python3 -c "import socket; s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(1); s.connect('$SOCK'); s.close()" 2>/dev/null; then
      ok=1
      break
    fi
    if ! kill -0 "$DPID" 2>/dev/null; then
      echo "$(date -Is) daemon died early" >> "$LOGD"
      DPID=""
      return 1
    fi
    sleep 0.25
  done
  if [[ "$ok" != 1 ]]; then
    echo "$(date -Is) socket not ready" >> "$LOGD"
    kill "$DPID" 2>/dev/null || true
    DPID=""
    return 1
  fi
  return 0
}

# UI must come up even if fingerprint USB is unplugged (otherwise kiosk stuck on logo)
start_ui

# Keep service alive; retry daemon whenever serial appears
while true; do
  if [[ -z "${UIPID:-}" ]] || ! kill -0 "$UIPID" 2>/dev/null; then
    echo "$(date -Is) ui exited — restarting" >> "$LOGU"
    start_ui
  fi

  resolve_finger_port || true
  if [[ -n "$PORT" && -e "$PORT" ]]; then
    if [[ -n "${DPID:-}" ]] && kill -0 "$DPID" 2>/dev/null && [[ "${BOUND:-}" != "$PORT" ]]; then
      kill "$DPID" 2>/dev/null || true
      DPID=""
      echo "$(date -Is) serial moved to $PORT — daemon restart" >> "$LOGD"
    fi
    if [[ -z "${DPID:-}" ]] || ! kill -0 "$DPID" 2>/dev/null; then
      if start_daemon; then
        BOUND="$PORT"
      fi
    fi
  else
    if [[ -n "${DPID:-}" ]]; then
      kill "$DPID" 2>/dev/null || true
      DPID=""
      BOUND=""
      echo "$(date -Is) fingerprint serial gone — daemon stopped" >> "$LOGD"
    fi
  fi
  sleep 2
done
