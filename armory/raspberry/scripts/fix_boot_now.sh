#!/bin/bash
set -euo pipefail
cat > "$HOME/.config/labwc/autostart" <<'EOF'
#!/bin/sh
# Keep Raspberry Pi desktop defaults, then launch locker kiosk.
if [ -f /etc/xdg/labwc/autostart ]; then
  /bin/sh /etc/xdg/labwc/autostart
fi
/home/pi/fingerprint/scripts/run_kiosk.sh &
EOF
chmod +x "$HOME/.config/labwc/autostart"

# Also update install script copy for future
cp "$HOME/.config/labwc/autostart" /tmp/labwc_autostart_ok 2>/dev/null || true

systemctl --user restart fingerprint-stack.service
sleep 6
systemctl --user --no-pager status fingerprint-stack.service | head -22
echo "=== status api ==="
curl -sf --max-time 3 http://127.0.0.1:8080/api/status | head -c 220
echo
echo "=== daemon ==="
ps -C sfm_daemon -o pid=,stat= || echo no_daemon
echo "=== launch kiosk ==="
pkill -f "chromium.*(127\.0\.0\.1:8080)" 2>/dev/null || true
sleep 1
nohup /home/pi/fingerprint/scripts/run_kiosk.sh >/dev/null 2>&1 &
sleep 3
pgrep -a chromium | head -3 || echo no_chromium
tail -n 20 "$HOME/fingerprint/logs/kiosk.log" || true
echo DONE
