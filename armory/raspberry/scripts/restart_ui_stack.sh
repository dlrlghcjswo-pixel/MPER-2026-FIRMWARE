#!/bin/bash
set -uo pipefail
ROOT=/home/pi/fingerprint

echo "Cleaning old processes..."
# anything on 8080
if command -v fuser >/dev/null 2>&1; then
  fuser -k 8080/tcp 2>/dev/null || true
fi
pkill -9 -f "$ROOT/ui/.venv/bin/python" || true
pkill -9 -f "$ROOT/ui/server.py" || true
pkill -9 -x sfm_daemon || true
sleep 2

# force remove zombies' pid tracking
rm -f "$ROOT/run/sfm.sock" "$ROOT/run/sfm_daemon.pid"
mkdir -p "$ROOT/run" "$ROOT/logs" "$ROOT/data"

echo "Starting daemon..."
PORT=""
shopt -s nullglob
for link in /dev/serial/by-id/*SmartLocker*; do
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
if [[ -z "$PORT" || ! -e "$PORT" ]]; then
  echo "fingerprint port not found (SmartLocker or ttyACM)" >> "$ROOT/logs/sfm_daemon.log"
  exit 1
fi
nohup "$ROOT/bin/sfm_daemon" "$PORT" "$ROOT/run/sfm.sock" \
  >> "$ROOT/logs/sfm_daemon.log" 2>&1 &
DPID=$!
echo "$DPID" > "$ROOT/run/sfm_daemon.pid"
echo "daemon_pid=$DPID"

ok=0
for i in $(seq 1 50); do
  if [[ -S "$ROOT/run/sfm.sock" ]]; then
    # verify accept works
    if python3 -c "import socket; s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(1); s.connect('$ROOT/run/sfm.sock'); s.close()"; then
      ok=1
      break
    fi
  fi
  # dead?
  if ! kill -0 "$DPID" 2>/dev/null; then
    echo "daemon died early"
    tail -n 30 "$ROOT/logs/sfm_daemon.log"
    exit 1
  fi
  sleep 0.2
done
if [[ "$ok" != 1 ]]; then
  echo "socket not ready"
  ls -la "$ROOT/run" || true
  tail -n 30 "$ROOT/logs/sfm_daemon.log"
  exit 1
fi
echo "daemon socket OK"

echo "Starting UI..."
cd "$ROOT/ui"
export SFM_EXTERNAL_DAEMON=1
nohup env SFM_EXTERNAL_DAEMON=1 .venv/bin/python server.py >> "$ROOT/logs/ui.log" 2>&1 &
echo "ui_pid=$!"
sleep 5

echo "=== processes ==="
ps -C sfm_daemon -o pid=,stat=,args= || echo "no daemon"
ss -ltnp 2>/dev/null | grep 8080 || true

echo "=== status ==="
curl -s --max-time 3 http://127.0.0.1:8080/api/status || echo "curl_fail"
echo
echo "=== daemon log (tail) ==="
tail -n 12 "$ROOT/logs/sfm_daemon.log"
echo "=== run dir ==="
ls -la "$ROOT/run"
