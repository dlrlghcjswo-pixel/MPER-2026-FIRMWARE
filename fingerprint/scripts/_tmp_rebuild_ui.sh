#!/bin/bash
set -e
cd "$HOME/fingerprint/ui"
sed -i 's/\r$//' locker_board.py server.py sfm_bridge.py wait.js 2>/dev/null || true
find static -type f -name '*.js' -o -name '*.html' -o -name '*.css' | while read -r f; do sed -i 's/\r$//' "$f"; done
if [ ! -x .venv/bin/pyinstaller ]; then
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r requirements.txt pyinstaller
fi
.venv/bin/pyinstaller --noconfirm --clean locker-ui.spec
cp -f dist/locker-ui "$HOME/fingerprint/bin/locker-ui"
chmod +x "$HOME/fingerprint/bin/locker-ui"
pkill -9 -x locker-ui || true
sleep 1
mkdir -p "$HOME/fingerprint/logs"
nohup env SFM_EXTERNAL_DAEMON=1 "$HOME/fingerprint/bin/locker-ui" >> "$HOME/fingerprint/logs/ui.log" 2>&1 &
sleep 3
pgrep -a locker-ui
code=$(curl -s -m 3 -o /tmp/st.json -w "%{http_code}" http://127.0.0.1:8080/api/status || true)
echo "http=$code"
python3 -c 'import json;p=json.load(open("/tmp/st.json"));b=p.get("board")or{};print("sens",b.get("sens"),"door",b.get("door"),"ok",b.get("ok"))'