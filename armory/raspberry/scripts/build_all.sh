#!/bin/bash
set -euo pipefail
ROOT="$HOME/fingerprint"
INC="$ROOT/sdk/sfm-sdk/include"
LIB="$ROOT/sdk/sfm-sdk/lib/linux/arm64"
mkdir -p "$ROOT/bin" "$ROOT/src"

gcc -O2 -Wall -I"$INC" "$ROOT/src/smoke_sfm.c" \
  -L"$LIB" -lSFM_SDK -lm -Wl,-rpath,"$LIB" \
  -o "$ROOT/bin/smoke_sfm"

gcc -O2 -Wall -I"$INC" "$ROOT/src/sfm_test.c" \
  -L"$LIB" -lSFM_SDK -lm -pthread -Wl,-rpath,"$LIB" \
  -o "$ROOT/bin/sfm_test"

gcc -O2 -Wall -I"$INC" "$ROOT/src/sfm_daemon.c" \
  -L"$LIB" -lSFM_SDK -lm -pthread -Wl,-rpath,"$LIB" \
  -o "$ROOT/bin/sfm_daemon"

echo "Built:"
echo "  $ROOT/bin/smoke_sfm"
echo "  $ROOT/bin/sfm_test"
echo "  $ROOT/bin/sfm_daemon"
echo
echo "Run: $ROOT/bin/sfm_daemon [/dev/ttyACM0] [sock_path]"
