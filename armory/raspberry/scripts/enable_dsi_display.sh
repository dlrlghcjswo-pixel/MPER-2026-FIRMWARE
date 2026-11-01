#!/bin/bash
set -euo pipefail
CFG=/boot/firmware/config.txt
cp -p "$CFG" "$CFG.bak-$(date +%Y%m%d-%H%M%S)"
if ! grep -q '^dtoverlay=vc4-kms-dsi-7inch' "$CFG"; then
  printf '\n# DFRobot 5inch DSI 800x480\ndtoverlay=vc4-kms-dsi-7inch\n' >> "$CFG"
fi
echo "--- $CFG (end) ---"
tail -4 "$CFG"
echo "Rebooting in 3 seconds..."
sleep 3
reboot
