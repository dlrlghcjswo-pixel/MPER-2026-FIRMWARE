#!/bin/bash
set -uo pipefail
# Unstick fingerprint-stack if stuck in deactivating, then start clean.
systemctl --user kill -s SIGKILL fingerprint-stack.service 2>/dev/null || true
pkill -9 -f /home/pi/fingerprint/scripts/run_stack.sh 2>/dev/null || true
pkill -9 -x sfm_daemon 2>/dev/null || true
pkill -9 -f /home/pi/fingerprint/ui/.venv/bin/python 2>/dev/null || true
fuser -k 8080/tcp 2>/dev/null || true
sleep 1
systemctl --user reset-failed fingerprint-stack.service 2>/dev/null || true
cp /home/pi/fingerprint/scripts/systemd/fingerprint-stack.service /home/pi/.config/systemd/user/
sed -i 's/\r$//' /home/pi/fingerprint/scripts/run_stack.sh /home/pi/.config/systemd/user/fingerprint-stack.service
chmod +x /home/pi/fingerprint/scripts/run_stack.sh
systemctl --user daemon-reload
systemctl --user start fingerprint-stack.service
sleep 5
systemctl --user show fingerprint-stack -p ActiveState -p SubState
pgrep -a sfm_daemon | head -1 || echo no_daemon
pgrep -af 'ui/.venv/bin/python' | head -1 || echo no_ui
curl -sf --max-time 3 http://127.0.0.1:8080/api/status | head -c 160
echo
echo DONE
