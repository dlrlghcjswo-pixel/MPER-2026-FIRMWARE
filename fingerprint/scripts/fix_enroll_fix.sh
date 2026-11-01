#!/bin/bash
set -uo pipefail
ROOT=/home/pi/fingerprint
bash "$ROOT/scripts/build_all.sh" | tail -n 5
# restart keeping fix
bash "$ROOT/scripts/restart_ui_stack.sh"
sleep 1
python3 - <<'PY'
import json, urllib.request, time
def get(url):
    return urllib.request.urlopen(url, timeout=3).read().decode()
def post(url, data):
    req=urllib.request.Request(url, data=json.dumps(data).encode(), headers={'Content-Type':'application/json'}, method='POST')
    return urllib.request.urlopen(req, timeout=3).read().decode()
print('status', get('http://127.0.0.1:8080/api/status')[:180])
print('enroll', post('http://127.0.0.1:8080/api/enroll/start', {'user_id':1}))
time.sleep(1.5)
# check daemon log for enroll_waiting
print('state', get('http://127.0.0.1:8080/api/enroll/state'))
PY
echo '--- daemon ---'
tail -n 20 "$ROOT/logs/sfm_daemon.log"
