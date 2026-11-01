#!/bin/bash
set -euo pipefail
mkdir -p ~/fingerprint/sdk ~/fingerprint/src ~/fingerprint/logs
sudo usermod -aG dialout "$USER" || true
python3 -m pip install --user --upgrade pip
python3 -m pip install --user pysfm pyserial
echo "=== ports ==="
ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || echo "no USB serial yet"
echo "=== groups ==="
groups
echo "=== python ==="
python3 -c "import pysfm; print('pysfm OK')"
echo "Setup done. Re-login SSH once for dialout group."