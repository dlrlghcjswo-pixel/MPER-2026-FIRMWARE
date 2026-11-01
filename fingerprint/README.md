# Fingerprint control (SFM Slim + Raspberry Pi 4)

## Status
- SSH alias: `raspberrypi-fingerprint` (pi@192.168.0.22, key auth)
- Device USB: Suprema SFM Slim on `/dev/ttyACM0`
- Stack: Suprema C SDK 3.3.0 (`lib/linux/arm64`) — verified working
- Note: official `pysfm` on PyPI is Python 2 only; use C SDK (or a future Python3 wrapper)

## Smoke test result (verified)
- UF_InitCommPort OK
- module_id=1
- firmware=0x53333641
- serial=0x37966039
- enrolled_fingers=0

## On Pi
```bash
~/fingerprint/scripts/build_smoke.sh
~/fingerprint/bin/smoke_sfm /dev/ttyACM0
```

## From Windows (agent)
```bash
ssh raspberrypi-fingerprint 'hostname; ~/fingerprint/bin/smoke_sfm /dev/ttyACM0'
```

## Project layout (Pi: ~/fingerprint)
- sdk/sfm-sdk — C/C++ SDK
- sdk/sfm-sdk-python — examples only
- src/smoke_sfm.c — connection smoke test
- bin/smoke_sfm — built binary
- .venv — Python venv (pyserial installed; pysfm N/A)
