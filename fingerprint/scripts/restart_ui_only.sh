#!/bin/bash
set -uo pipefail
ROOT=/home/pi/fingerprint
python3 "$ROOT/scripts/sync_master1.py"
if command -v fuser >/dev/null 2>&1; then
  fuser -k 8080/tcp 2>/dev/null || true
fi
pkill -9 -f "$ROOT/ui/.venv/bin/python" || true
sleep 1
cd "$ROOT/ui"
nohup env SFM_EXTERNAL_DAEMON=1 .venv/bin/python server.py >> "$ROOT/logs/ui.log" 2>&1 &
echo "ui_pid=$!"
sleep 3
curl -s --max-time 3 http://127.0.0.1:8080/api/status
echo
ps -C sfm_daemon -o pid=,stat= || echo no_daemon
