#!/usr/bin/env python3
"""Remote helper used from Windows: ssh raspberrypi-fingerprint <cmd>"""
import subprocess
import sys

HOST = "raspberrypi-fingerprint"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: pi_ssh.py <remote-command...>")
        return 2
    cmd = " ".join(sys.argv[1:])
    return subprocess.call(["ssh", HOST, cmd])


if __name__ == "__main__":
    raise SystemExit(main())
