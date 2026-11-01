#!/usr/bin/env python3
import socket
import errno
from pathlib import Path
p = Path("/home/pi/fingerprint/run/sfm.sock")
print("exists", p.exists(), "is_socket", p.is_socket() if p.exists() else None)
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(2)
try:
    s.connect(str(p))
    print("connected OK")
    s.sendall(b'{"cmd":"status"}\n')
    import time; time.sleep(0.3)
    print(s.recv(4096))
except OSError as e:
    print("OSError", e.errno, errno.errorcode.get(e.errno), e)
finally:
    s.close()
