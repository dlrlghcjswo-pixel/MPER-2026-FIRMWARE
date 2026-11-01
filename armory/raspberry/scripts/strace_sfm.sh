#!/bin/bash
set -euo pipefail
DPID=$(ps -C sfm_daemon -o pid= | awk '{print $1; exit}')
echo "pid=$DPID"
if [[ -z "$DPID" ]]; then
  echo "no daemon"
  exit 1
fi
echo "threads:" 
ls "/proc/$DPID/task"
echo "fds:"
ls -l "/proc/$DPID/fd/" | head -30

timeout 4 strace -f -p "$DPID" -e accept,accept4,read,write,poll,ppoll 2>/tmp/strace_sfm.txt &
SP=$!
sleep 0.7
python3 /home/pi/fingerprint/scripts/debug_sock2.py || true
wait "$SP" || true
echo "---STRACE---"
wc -l /tmp/strace_sfm.txt
tail -n 60 /tmp/strace_sfm.txt
