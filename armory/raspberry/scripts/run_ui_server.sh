#!/bin/bash
# Start FastAPI UI (expects sfm_daemon already running).
set -uo pipefail
ROOT=/home/pi/fingerprint
SOCK="$ROOT/run/sfm.sock"
mkdir -p "$ROOT/logs"

# Wait for daemon socket
for i in $(seq 1 60); do
  if [[ -S "$SOCK" ]]; then
    if python3 -c "import socket; s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(1); s.connect('$SOCK'); s.close()"; then
      break
    fi
  fi
  sleep 0.5
done

cd "$ROOT/ui"
export SFM_EXTERNAL_DAEMON=1
exec "$ROOT/ui/.venv/bin/python" server.py
