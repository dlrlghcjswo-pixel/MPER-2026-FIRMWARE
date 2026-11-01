#!/usr/bin/env python3
import json
import urllib.request

raw = urllib.request.urlopen("http://127.0.0.1:8080/api/status", timeout=5).read()
d = json.loads(raw.decode("utf-8"))
b = d.get("board") or {}
print("kind", d.get("locker_kind"))
print("board_ok", b.get("ok"))
print("door", b.get("door"))
print("sens", b.get("sens"))
print("reply", b.get("reply"))
print("sensor", d.get("sensor"))
users = d.get("users") or {}
for i in range(1, 11):
    u = users.get(str(i)) or {}
    print(f"user{i}", "id", u.get("user_id"), "status", u.get("status"), "name", u.get("name"))
