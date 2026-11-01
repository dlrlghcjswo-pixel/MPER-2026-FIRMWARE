#!/usr/bin/env python3
import json
from pathlib import Path

p = Path("/home/pi/fingerprint/data/masters.json")
d = json.loads(p.read_text(encoding="utf-8"))
print("BEFORE:")
for k, v in (d.get("users") or {}).items():
    if v:
        print(k, v.get("name"), v.get("status"), v.get("purpose"))
print("stats", d.get("stats"))

for v in (d.get("users") or {}).values():
    if isinstance(v, dict) and v.get("status") == "issued" and not v.get("purpose"):
        v["purpose"] = "사격"

purposes = ("사격", "훈련", "정비", "근무", "기타", "젤루텍")
counts = {x: 0 for x in purposes}
for v in (d.get("users") or {}).values():
    if isinstance(v, dict) and v.get("status") == "issued":
        pur = v.get("purpose") if v.get("purpose") in counts else "기타"
        counts[pur] += 1
d.setdefault("stats", {})["issue_counts"] = counts
p.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
print("AFTER", counts)
