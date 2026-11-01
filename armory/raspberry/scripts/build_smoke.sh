#!/bin/bash
set -euo pipefail
ROOT="$HOME/fingerprint"
INC="$ROOT/sdk/sfm-sdk/include"
LIB="$ROOT/sdk/sfm-sdk/lib/linux/arm64"
SRC="$ROOT/src/smoke_sfm.c"
OUT="$ROOT/bin/smoke_sfm"

mkdir -p "$ROOT/bin" "$ROOT/src"
gcc -O2 -Wall -I"$INC" "$SRC" -L"$LIB" -lSFM_SDK -lm -Wl,-rpath,"$LIB" -o "$OUT"
echo "Built $OUT"
