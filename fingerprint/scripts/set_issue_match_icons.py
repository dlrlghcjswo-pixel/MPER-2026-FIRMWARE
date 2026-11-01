#!/usr/bin/env python3
import json
import time
from pathlib import Path

p = Path("/home/pi/fingerprint/data/masters.json")
d = json.loads(p.read_text(encoding="utf-8"))
users = d.setdefault("users", {})

u1 = users.get("1")
if isinstance(u1, dict) and u1.get("user_id"):
    u1["status"] = "issued"
    u1["purpose"] = "사격"
    u1["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    users["1"] = u1

u5 = users.get("5")
if isinstance(u5, dict) and u5.get("user_id"):
    u5["status"] = "stored"
    u5["purpose"] = None
    users["5"] = u5

purposes = ("사격", "훈련", "정비", "근무", "기타", "젤루텍")
counts = {x: 0 for x in purposes}
for v in users.values():
    if isinstance(v, dict) and v.get("status") == "issued":
        pur = v.get("purpose") if v.get("purpose") in counts else "기타"
        counts[pur] += 1
d.setdefault("stats", {})["issue_counts"] = counts
p.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
print("users:")
for k, v in users.items():
    if v:
        print(k, v.get("name"), v.get("status"), v.get("purpose"))
print("counts", counts)
