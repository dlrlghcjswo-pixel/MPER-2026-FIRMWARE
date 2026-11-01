#!/bin/bash
set -euo pipefail
echo "hostname=$(hostname)"
echo "arch=$(uname -m)"
echo "=== USB serial devices ==="
ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || echo "(none yet - plug SFM Slim USB)"
echo "=== lsusb fingerprint-related ==="
lsusb || true
echo "=== pysfm ==="
python3 -c "import pysfm; print('pysfm import OK')" 2>/dev/null || echo "pysfm not installed"