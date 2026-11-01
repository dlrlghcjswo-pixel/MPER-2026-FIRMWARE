#!/bin/bash
set -uo pipefail
systemctl --user stop fingerprint-stack.service || true
pkill -9 -x locker-ui || true
pkill -9 -f "/home/pi/fingerprint/ui/.venv/bin/python server.py" || true
rm -rf /tmp/_MEI* || true
sync
cd /home/pi/fingerprint/ui
.venv/bin/pyinstaller --noconfirm --clean locker-ui.spec
cp -f dist/locker-ui /home/pi/fingerprint/bin/locker-ui
chmod +x /home/pi/fingerprint/bin/locker-ui
systemctl --user start fingerprint-stack.service
sleep 5
code=$(curl -s -m 5 -o /tmp/st.json -w "%{http_code}" http://127.0.0.1:8080/api/status || true)
echo "http=$code"
if [ "$code" != "200" ]; then
  echo "FALLBACK_VENV"
  systemctl --user stop fingerprint-stack.service || true
  pkill -9 -x locker-ui || true
  pkill -9 -x sfm_daemon || true
  sleep 1
  # start daemon + venv UI
  bash /home/pi/fingerprint/scripts/run_stack.sh >> /home/pi/fingerprint/logs/stack_manual.log 2>&1 &
  sleep 2
  # kill crashing binary UI and use venv instead
  pkill -9 -x locker-ui || true
  export SFM_EXTERNAL_DAEMON=1
  export FP_ROOT=/home/pi/fingerprint
  cd /home/pi/fingerprint/ui
  nohup .venv/bin/python server.py >> /home/pi/fingerprint/logs/ui.log 2>&1 &
  echo "venv_ui_pid=$!"
  sleep 3
  code=$(curl -s -m 5 -o /tmp/st.json -w "%{http_code}" http://127.0.0.1:8080/api/status || true)
  echo "http=$code"
fi
pgrep -a locker-ui || true
pgrep -af "server.py|sfm_daemon" | head -8
grep -n "대기\|비상개폐가 되었습니다" /home/pi/fingerprint/ui/static/js/wait.js | head -8
echo DONE