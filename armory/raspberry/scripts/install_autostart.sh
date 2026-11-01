#!/bin/bash
# Fix/install boot autostart: single stack service + kiosk (no dependency cycles).
set -euo pipefail
ROOT=/home/pi/fingerprint
USER_UNIT_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"

chmod +x \
  "$ROOT/scripts/run_sfm_daemon.sh" \
  "$ROOT/scripts/run_ui_server.sh" \
  "$ROOT/scripts/run_stack.sh" \
  "$ROOT/scripts/run_kiosk.sh" \
  "$ROOT/scripts/fix_display.sh" \
  "$ROOT/scripts/install_autostart.sh"

sed -i 's/\r$//' \
  "$ROOT/scripts/"*.sh \
  "$ROOT/scripts/systemd/"*.service \
  "$ROOT/scripts/autostart/"*.desktop 2>/dev/null || true

mkdir -p "$USER_UNIT_DIR" "$AUTOSTART_DIR" "$ROOT/run" "$ROOT/logs" "$ROOT/data"

# Replace old split units with one stack unit
systemctl --user disable --now fingerprint-daemon.service fingerprint-ui.service 2>/dev/null || true
rm -f "$USER_UNIT_DIR/fingerprint-daemon.service" \
      "$USER_UNIT_DIR/fingerprint-ui.service" \
      "$HOME/.config/systemd/user/default.target.wants/fingerprint-daemon.service" \
      "$HOME/.config/systemd/user/default.target.wants/fingerprint-ui.service"

cp "$ROOT/scripts/systemd/fingerprint-stack.service" "$USER_UNIT_DIR/"
# Avoid double-start: labwc/autostart launches kiosk; disable xdg .desktop copy
cat > "$AUTOSTART_DIR/fingerprint-kiosk.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Fingerprint Locker Kiosk
Comment=Disabled — launched from labwc/autostart to avoid Chromium SingletonLock races
Exec=/bin/bash /home/pi/fingerprint/scripts/run_kiosk.sh
Terminal=false
Hidden=true
X-GNOME-Autostart-enabled=false
StartupNotify=false
EOF
chmod +x "$AUTOSTART_DIR/fingerprint-kiosk.desktop"

# Stop leftovers
if command -v fuser >/dev/null 2>&1; then
  fuser -k 8080/tcp 2>/dev/null || true
fi
pkill -9 -f "$ROOT/ui/.venv/bin/python" 2>/dev/null || true
pkill -9 -x sfm_daemon 2>/dev/null || true
pkill -f "chromium.*(127\.0\.0\.1:8080)" 2>/dev/null || true
sleep 1

systemctl --user daemon-reload
systemctl --user enable fingerprint-stack.service
systemctl --user restart fingerprint-stack.service
sleep 5

echo "=== stack status ==="
systemctl --user --no-pager --full status fingerprint-stack.service || true
echo "=== curl ==="
curl -sf --max-time 3 http://127.0.0.1:8080/api/status && echo || echo "UI not ready"
echo "=== processes ==="
ps -C sfm_daemon,python -o pid=,stat=,args= 2>/dev/null | head -10 || true

# Also drop a labwc autostart that keeps stock desktop + kiosk
LABWC_AS="$HOME/.config/labwc/autostart"
mkdir -p "$HOME/.config/labwc"
cat > "$LABWC_AS" <<'EOF'
#!/bin/sh
# Keep Raspberry Pi desktop defaults, then launch locker kiosk.
if [ -f /etc/xdg/labwc/autostart ]; then
  /bin/sh /etc/xdg/labwc/autostart
fi
/bin/bash /home/pi/fingerprint/scripts/fix_display.sh >/tmp/fp-display-mode 2>/dev/null || true
wayvnc 0.0.0.0 5900 >>/home/pi/fingerprint/logs/wayvnc.log 2>&1 &
/bin/bash /home/pi/fingerprint/scripts/run_kiosk.sh >>/home/pi/fingerprint/logs/kiosk-autostart.log 2>&1 &
EOF
chmod +x "$LABWC_AS"

echo
echo "Fixed. Reboot to verify, or run: /bin/bash $ROOT/scripts/run_kiosk.sh"
