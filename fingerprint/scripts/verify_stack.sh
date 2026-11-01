#!/bin/bash
set -euo pipefail
ROOT=/home/pi/fingerprint
bash "$ROOT/scripts/build_all.sh" | tail -n 6
bash "$ROOT/scripts/restart_ui_stack.sh"
sleep 2
echo "=== sock test ==="
python3 "$ROOT/scripts/debug_sock2.py" || true
echo "=== api status ==="
python3 - <<'PY'
import json, urllib.request, time
print(urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=3).read().decode())
time.sleep(1)
print(urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=3).read().decode())
req=urllib.request.Request(
    'http://127.0.0.1:8080/api/enroll/start',
    data=b'{"user_id":1}',
    headers={'Content-Type':'application/json'},
    method='POST')
try:
    print('enroll', urllib.request.urlopen(req, timeout=3).read().decode())
except Exception as e:
    print('enroll err', e)
time.sleep(0.5)
print('state', urllib.request.urlopen('http://127.0.0.1:8080/api/enroll/state', timeout=3).read().decode())
req=urllib.request.Request(
    'http://127.0.0.1:8080/api/enroll/abort',
    data=b'{}',
    headers={'Content-Type':'application/json'},
    method='POST')
print('abort', urllib.request.urlopen(req, timeout=3).read().decode())
PY
echo "=== daemon tail ==="
tail -n 15 "$ROOT/logs/sfm_daemon.log"
