#!/bin/bash
ROOT=/home/pi/fingerprint
pkill -9 -x sfm_daemon || true
pkill -9 -f "$ROOT/ui/.venv/bin/python" || true
sleep 1
rm -f "$ROOT/run/sfm.sock" "$ROOT/run/sfm_daemon.pid"
# run in foreground briefly
timeout 6 "$ROOT/bin/sfm_daemon" /dev/ttyACM0 "$ROOT/run/sfm.sock" > /tmp/sfm_fg.log 2>&1 &
DPID=$!
echo "pid=$DPID"
sleep 3
ps -p "$DPID" -o pid=,stat=,args= || echo dead
ls -la "$ROOT/run" || true
python3 "$ROOT/scripts/debug_sock2.py" || true
sleep 1
ps -p "$DPID" -o pid=,stat= || echo dead_after
cat /tmp/sfm_fg.log
