#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/pi/fingerprint/ui")
from sfm_bridge import bridge, SOCK_PATH
print("sock", SOCK_PATH, SOCK_PATH.exists())
print("probe", bridge._probe_socket())
print("alive", bridge._daemon_alive())
bridge.start()
import time
time.sleep(2)
print("sock_obj", bridge._sock)
print("status", bridge.snapshot())
print("send status", bridge.send({"cmd": "status"}))
time.sleep(1)
print("status2", bridge.snapshot())
print("enroll", bridge.start_enroll(1))
time.sleep(1)
print("estate", bridge.enroll_state())
print("abort", bridge.abort())
