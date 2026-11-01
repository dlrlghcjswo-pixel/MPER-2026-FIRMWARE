#!/usr/bin/env python3
import socket
import json
import time
from pathlib import Path

sock = Path("/home/pi/fingerprint/run/sfm.sock")
print("sock exists", sock.exists())
print("pid file", Path("/home/pi/fingerprint/run/sfm_daemon.pid").read_text().strip() if Path("/home/pi/fingerprint/run/sfm_daemon.pid").exists() else None)

import subprocess
subprocess.run(["ps", "-C", "sfm_daemon", "-o", "pid=,stat=,args="])

if not sock.exists():
    raise SystemExit("no socket")

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(2)
s.connect(str(sock))
s.sendall(b'{"cmd":"status"}\n')
time.sleep(0.4)
data = s.recv(4096)
print("recv:", data.decode())
s.close()
