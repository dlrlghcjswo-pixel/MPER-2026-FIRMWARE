#!/bin/bash
ROOT=/home/pi/fingerprint
pkill -9 -x sfm_daemon || true
sleep 1
rm -f "$ROOT/run/sfm.sock"
cd /tmp
gdb -batch -ex "set pagination off" -ex "run /dev/ttyACM0 $ROOT/run/sfm.sock" -ex "bt" --args "$ROOT/bin/sfm_daemon" /dev/ttyACM0 "$ROOT/run/sfm.sock" > /tmp/gdb_sfm.txt 2>&1 &
GPID=$!
sleep 2
python3 -c "import socket,time; s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.settimeout(2); s.connect('$ROOT/run/sfm.sock'); time.sleep(0.5); print('connected'); s.close()" || true
sleep 2
kill "$GPID" 2>/dev/null || true
wait "$GPID" 2>/dev/null || true
cat /tmp/gdb_sfm.txt | tail -n 80
