#!/bin/bash
# Suppress boot password / unlock prompts and apply UI CSS.
set -euo pipefail
ROOT=/home/pi/fingerprint
AS="$HOME/.config/autostart"
mkdir -p "$AS"

# Same-name overrides hide system XDG autostart entries
for name in pprompt.desktop gnome-keyring-secrets.desktop gnome-keyring-pkcs11.desktop gnome-keyring-ssh.desktop; do
  cat > "$AS/$name" <<'EOF'
[Desktop Entry]
Hidden=true
X-GNOME-Autostart-enabled=false
EOF
done

# Keep our kiosk entry
cp "$ROOT/scripts/autostart/fingerprint-kiosk.desktop" "$AS/"
chmod +x "$AS/fingerprint-kiosk.desktop" "$ROOT/scripts/run_kiosk.sh"
sed -i 's/\r$//' "$ROOT/scripts/run_kiosk.sh" "$AS/"*.desktop

# Soft-unlock keyring if present (empty password / ignore errors)
if command -v gnome-keyring-daemon >/dev/null 2>&1; then
  echo -n "" | gnome-keyring-daemon --unlock 2>/dev/null || true
fi

# Clear SSH default-password warning flag if writable
rm -f /run/sshwarn 2>/dev/null || true

echo "Boot prompt suppressors installed in $AS"
ls -la "$AS"
