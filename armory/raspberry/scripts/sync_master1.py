#!/usr/bin/env python3
"""Ensure Master1 is present in masters.json without wiping subs."""
import json
import os
import time
from pathlib import Path

p = Path("/home/pi/fingerprint/data/masters.json")
p.parent.mkdir(parents=True, exist_ok=True)

if p.exists():
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        data = {}
else:
    data = {}

data.setdefault("masters", {str(i): None for i in range(1, 6)})
data.setdefault("subs", {str(i): None for i in range(1, 31)})
data.setdefault("users", {str(i): None for i in range(1, 11)})

# Master1이 비어 있으면 기본값으로 채움 (기존 등록 유지)
if not data["masters"].get("1"):
    data["masters"]["1"] = {
        "user_id": 1,
        "quality": 80,
        "enrolled_at": "2026-09-24T21:00:00",
    }

for i in range(1, 6):
    data["masters"].setdefault(str(i), None)
for i in range(1, 31):
    data["subs"].setdefault(str(i), None)
for i in range(1, 11):
    data["users"].setdefault(str(i), None)

payload = json.dumps(data, indent=2, ensure_ascii=False)
tmp = p.parent / f".masters.json.tmp.sync.{os.getpid()}.{time.time_ns()}"
try:
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)
finally:
    if tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            pass
print(p.read_text(encoding="utf-8"))
