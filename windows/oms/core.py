"""Local OMS store and Raspberry Pi locker link.

The running locker is the source of truth: 5 masters, 30 subs, 10 slots,
and the six purposes the cabinet already uses.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

PURPOSES = ("사격", "훈련", "정비", "근무", "기타", "젤루텍")
MASTER_SLOTS = 5
SUB_SLOTS = 30
USER_SLOTS = 10

ID_RE = re.compile(r"^[a-z0-9!@#$%^&*_\-+=.?]{1,10}$")


def data_path() -> Path:
    override = os.environ.get("OMS_DATA", "").strip()
    if override:
        return Path(override)
    root = Path(os.environ.get("APPDATA", str(Path.home())))
    return root / "Zellootec" / "RifleOMS" / "oms.json"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return salt, digest.hex()


def valid_id(value: str) -> bool:
    return bool(ID_RE.fullmatch(value or ""))


def valid_password(value: str) -> bool:
    return bool(value)


def _blank_map(count: int) -> dict[str, Any]:
    return {str(i): None for i in range(1, count + 1)}


def _merge_slots(raw: Any, count: int) -> dict[str, Any]:
    merged = _blank_map(count)
    if isinstance(raw, dict):
        for i in range(1, count + 1):
            merged[str(i)] = raw.get(str(i))
    return merged


def _catalog_of(raw: Any, guns: dict[str, Any]) -> list[dict[str, str]]:
    if isinstance(raw, list):
        return _clean_catalog(raw)
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for slot in range(1, USER_SLOTS + 1):
        gun = (guns or {}).get(str(slot)) or {}
        if not isinstance(gun, dict):
            continue
        model = _clip(gun.get("model"), 40)
        if not model or model in seen:
            continue
        category = _clip(gun.get("category") or "소총", 8)
        if category not in ("권총", "소총", "기타"):
            category = "소총"
        seen.add(model)
        found.append({"category": category, "model": model})
    return found


def _clean_catalog(raw: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return items
    for item in raw:
        if not isinstance(item, dict):
            continue
        model = _clip(item.get("model"), 40)
        if not model:
            continue
        category = _clip(item.get("category") or "소총", 8)
        if category not in ("권총", "소총", "기타"):
            category = "소총"
        items.append({"category": category, "model": model})
    return items


def empty_cabinet(name: str = "1번 보관함", host: str = "192.168.0.14", port: int = 8080, cab_id: str = "1") -> dict[str, Any]:
    return {
        "id": cab_id,
        "name": name,
        "host": host,
        "port": int(port),
        "users": _blank_map(USER_SLOTS),
        "guns": _blank_map(USER_SLOTS),
        "catalog": [],
        "memo": "",
    }


def empty_store() -> dict[str, Any]:
    return {
        "lockers": [empty_cabinet()],
        "selected": "1",
        "masters": _blank_map(MASTER_SLOTS),
        "subs": _blank_map(SUB_SLOTS),
        "operator": None,
        "history": [],
        "purpose_label": "젤루텍",
    }


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_path()
        self._lock = threading.Lock()
        self.data = empty_store()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.lives: dict[str, dict[str, Any]] = {}
        self._last_users: dict[str, dict[str, dict[str, Any]] | None] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        base = empty_store()
        for key in ("masters", "subs", "operator", "history", "selected"):
            if key in loaded:
                base[key] = loaded[key]
        label = loaded.get("purpose_label")
        base["purpose_label"] = str(label).strip()[:10] if isinstance(label, str) and label.strip() else "젤루텍"
        lockers = loaded.get("lockers")
        if not isinstance(lockers, list) or not lockers:
            old = loaded.get("locker") or {}
            cab = empty_cabinet(
                str(old.get("name") or "1번 보관함"),
                str(old.get("host") or "192.168.0.14"),
                int(old.get("port") or 8080),
                "1",
            )
            cab["users"] = _merge_slots(loaded.get("users"), USER_SLOTS)
            cab["guns"] = _merge_slots(loaded.get("guns"), USER_SLOTS)
            cab["catalog"] = _catalog_of(loaded.get("catalog"), cab["guns"])
            cab["memo"] = str(loaded.get("memo") or "")
            lockers = [cab]
        cleaned = []
        for index, raw in enumerate(lockers, start=1):
            if not isinstance(raw, dict):
                continue
            cab = empty_cabinet(
                str(raw.get("name") or f"{index}번 보관함"),
                str(raw.get("host") or ""),
                int(raw.get("port") or 8080),
                str(raw.get("id") or index),
            )
            cab["users"] = _merge_slots(raw.get("users"), USER_SLOTS)
            cab["guns"] = _merge_slots(raw.get("guns"), USER_SLOTS)
            cab["catalog"] = _catalog_of(raw.get("catalog"), cab["guns"])
            cab["memo"] = str(raw.get("memo") or "")
            cleaned.append(cab)
        base["lockers"] = cleaned or [empty_cabinet()]
        if not any(item["id"] == str(base.get("selected")) for item in base["lockers"]):
            base["selected"] = base["lockers"][0]["id"]
        for key, count in (("masters", MASTER_SLOTS), ("subs", SUB_SLOTS)):
            base[key] = _merge_slots(base.get(key), count)
        self.data = base

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _selected(self) -> dict[str, Any]:
        chosen = str(self.data.get("selected") or "")
        for item in self.data["lockers"]:
            if item["id"] == chosen:
                return item
        self.data["selected"] = self.data["lockers"][0]["id"]
        return self.data["lockers"][0]

    def _live_of(self, cab_id: str) -> dict[str, Any]:
        return self.lives.get(cab_id) or {"online": False, "error": "연결 전", "status": {}, "board": {}}

    def master_count(self) -> int:
        return sum(1 for item in self.data["masters"].values() if _profile_complete("master", item))

    def session_user(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        return self.sessions.get(token)

    def login(self, login_id: str, password: str) -> dict[str, Any]:
        login_id = (login_id or "").strip()
        if self.master_count() == 0:
            return {"ok": False, "code": "empty"}
        found = self._find_login(login_id)
        if not found:
            return {"ok": False, "code": "missing"}
        kind, slot, record = found
        salt = record.get("salt") or ""
        _, digest = _hash_password(password or "", salt)
        if digest != record.get("password"):
            return {"ok": False, "code": "mismatch"}
        token = secrets.token_urlsafe(24)
        who = {
            "role": "operator" if kind == "operator" else "master",
            "kind": kind,
            "slot": slot,
            "rank": record.get("rank") or "",
            "name": record.get("name") or "",
            "login_id": record.get("login_id") or "",
        }
        self.sessions[token] = who
        return {"ok": True, "token": token, "who": who}

    def logout(self, token: str | None) -> None:
        if token:
            self.sessions.pop(token, None)

    def bootstrap(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.master_count() != 0:
            return {"ok": False, "error": "최초 마스터 관리자가 이미 등록되어 있습니다."}
        body = dict(body)
        body["slot"] = 1
        actor = {"role": "master", "slot": 1}
        saved = self.save_person("master", 1, body, actor)
        if not saved.get("ok"):
            return saved
        return self.login(str(body.get("login_id") or "").strip(), str(body.get("password") or ""))

    def _find_login(self, login_id: str) -> tuple[str, int, dict[str, Any]] | None:
        for slot, record in self.data["masters"].items():
            if record and record.get("login_id") == login_id and record.get("password"):
                return "master", int(slot), record
        op = self.data.get("operator")
        if op and op.get("login_id") == login_id and op.get("password"):
            return "operator", 0, op
        return None

    def _id_taken(self, login_id: str, ignore: tuple[str, int] | None = None) -> bool:
        for slot, record in self.data["masters"].items():
            if ignore == ("master", int(slot)):
                continue
            if record and record.get("login_id") == login_id:
                return True
        op = self.data.get("operator")
        if ignore != ("operator", 0) and op and op.get("login_id") == login_id:
            return True
        return False

    def save_person(self, kind: str, slot: int, body: dict[str, Any], actor: dict[str, Any] | None) -> dict[str, Any]:
        error = self._person_error(kind, slot, body, actor)
        if error:
            return {"ok": False, "error": error}
        with self._lock:
            record = self._public_record(kind, slot) or {}
            enrolled = self._enrolled(kind, slot)
            rank = _clip(body.get("rank"), 4)
            name = _clip(body.get("name"), 5)
            sn = _digits(body.get("sn"), 10)
            next_rec = {
                "rank": rank,
                "name": name,
                "sn": sn,
            }
            if kind in ("master", "operator"):
                login_id = (body.get("login_id") or "").strip()
                password = body.get("password") or ""
                if not valid_id(login_id):
                    return {"ok": False, "error": "ID는 영문 소문자, 숫자, 기호 포함 10자리까지입니다."}
                if self._id_taken(login_id, (kind, slot)):
                    return {"ok": False, "error": "이미 사용 중인 ID입니다."}
                if password or not record.get("password"):
                    if password != (body.get("password2") or ""):
                        return {"ok": False, "error": "Password가 일치하지 않습니다."}
                    if not valid_password(password):
                        return {"ok": False, "error": "Password를 입력하세요."}
                    salt, digest = _hash_password(password)
                    next_rec["salt"] = salt
                    next_rec["password"] = digest
                else:
                    next_rec["salt"] = record.get("salt")
                    next_rec["password"] = record.get("password")
                next_rec["login_id"] = login_id
            if kind == "operator":
                self.data["operator"] = next_rec
            elif kind == "user":
                self._selected()["users"][str(slot)] = next_rec
            else:
                self.data[_bucket(kind)][str(slot)] = next_rec
            self.save()
            if enrolled:
                self._push_name(self._selected(), kind, slot, name, rank)
        return {"ok": True}

    def delete_person(self, kind: str, slot: int, actor: dict[str, Any] | None) -> dict[str, Any]:
        if not actor or actor.get("role") != "master":
            return {"ok": False, "error": "마스터 관리자만 삭제할 수 있습니다."}
        if kind == "master" and actor.get("slot") == slot:
            return {"ok": False, "error": "로그인 중인 마스터는 삭제할 수 없습니다."}
        if self._enrolled(kind, slot):
            return {"ok": False, "error": "지문이 등록되어 있습니다. 보관함에서 지문을 삭제한 뒤 OMS에서 삭제하세요."}
        with self._lock:
            if kind == "operator":
                self.data["operator"] = None
            elif kind == "user":
                self._selected()["users"][str(slot)] = None
            else:
                self.data[_bucket(kind)][str(slot)] = None
            self.save()
        return {"ok": True}

    def save_gun(self, slot: int, body: dict[str, Any], actor: dict[str, Any] | None) -> dict[str, Any]:
        if not actor or actor.get("role") != "master":
            return {"ok": False, "error": "마스터 관리자만 총기 목록을 수정할 수 있습니다."}
        if slot < 1 or slot > USER_SLOTS:
            return {"ok": False, "error": "칸 번호가 올바르지 않습니다."}
        model = _clip(body.get("model"), 40)
        if not model:
            return {"ok": False, "error": "모델명을 입력하세요."}
        category = _clip(body.get("category") or "소총", 8)
        if category not in ("권총", "소총", "기타"):
            category = "소총"
        with self._lock:
            current = self._selected()["guns"].get(str(slot)) or {}
            if "serial" in body:
                serial = _clip(body.get("serial"), 20)
            else:
                serial = str(current.get("serial") or "")
            self._selected()["guns"][str(slot)] = {"category": category, "model": model, "serial": serial}
            self.save()
        return {"ok": True}

    def save_catalog(self, body: dict[str, Any], actor: dict[str, Any] | None) -> dict[str, Any]:
        if not actor or actor.get("role") != "master":
            return {"ok": False, "error": "마스터 관리자만 총기 목록을 수정할 수 있습니다."}
        model = _clip(body.get("model"), 40)
        if not model:
            return {"ok": False, "error": "모델명을 입력하세요."}
        category = _clip(body.get("category") or "권총", 8)
        if category not in ("권총", "소총", "기타"):
            category = "소총"
        raw_index = body.get("index")
        with self._lock:
            catalog = self._selected().setdefault("catalog", [])
            entry = {"category": category, "model": model}
            if raw_index in (None, ""):
                catalog.append(entry)
                saved = len(catalog) - 1
            else:
                try:
                    saved = int(raw_index)
                except (TypeError, ValueError):
                    return {"ok": False, "error": "순번이 올바르지 않습니다."}
                if saved < 0 or saved >= len(catalog):
                    return {"ok": False, "error": "순번이 올바르지 않습니다."}
                catalog[saved] = entry
            self.save()
        return {"ok": True, "index": saved}

    def delete_catalog(self, index: int, actor: dict[str, Any] | None) -> dict[str, Any]:
        if not actor or actor.get("role") != "master":
            return {"ok": False, "error": "마스터 관리자만 삭제할 수 있습니다."}
        with self._lock:
            catalog = self._selected().setdefault("catalog", [])
            if index < 0 or index >= len(catalog):
                return {"ok": False, "error": "순번이 올바르지 않습니다."}
            catalog.pop(index)
            self.save()
        return {"ok": True}

    def delete_gun(self, slot: int, actor: dict[str, Any] | None) -> dict[str, Any]:
        if not actor or actor.get("role") != "master":
            return {"ok": False, "error": "마스터 관리자만 삭제할 수 있습니다."}
        if slot < 1 or slot > USER_SLOTS:
            return {"ok": False, "error": "칸 번호가 올바르지 않습니다."}
        with self._lock:
            self._selected()["guns"][str(slot)] = None
            self.save()
        return {"ok": True}

    def save_locker(self, body: dict[str, Any], actor: dict[str, Any] | None) -> dict[str, Any]:
        if not actor or actor.get("role") != "master":
            return {"ok": False, "error": "마스터 관리자만 보관함 정보를 수정할 수 있습니다."}
        name = _clip(body.get("name"), 20)
        host = (body.get("host") or "").strip()
        try:
            port = int(body.get("port") or 8080)
        except (TypeError, ValueError):
            return {"ok": False, "error": "포트 번호가 올바르지 않습니다."}
        if not name or not host or port < 1 or port > 65535:
            return {"ok": False, "error": "보관함 이름과 IP를 입력하세요."}
        with self._lock:
            cab = self._selected()
            cab["name"] = name
            cab["host"] = host
            cab["port"] = port
            self._last_users[cab["id"]] = None
            self.save()
        return {"ok": True}

    def add_locker(self, body: dict[str, Any], actor: dict[str, Any] | None) -> dict[str, Any]:
        if not actor or actor.get("role") != "master":
            return {"ok": False, "error": "마스터 관리자만 보관함을 등록할 수 있습니다."}
        name = _clip(body.get("name"), 20)
        host = (body.get("host") or "").strip()
        try:
            port = int(body.get("port") or 8080)
        except (TypeError, ValueError):
            return {"ok": False, "error": "포트 번호가 올바르지 않습니다."}
        if not name or not host or port < 1 or port > 65535:
            return {"ok": False, "error": "보관함 이름과 IP를 입력하세요."}
        with self._lock:
            cab_id = "1"
            while any(item["id"] == cab_id for item in self.data["lockers"]):
                cab_id = str(int(cab_id) + 1)
            self.data["lockers"].append(empty_cabinet(name, host, port, cab_id))
            self.data["selected"] = cab_id
            self.save()
        return {"ok": True, "id": cab_id}

    def delete_locker(self, body: dict[str, Any], actor: dict[str, Any] | None) -> dict[str, Any]:
        if not actor or actor.get("role") != "master":
            return {"ok": False, "error": "마스터 관리자만 보관함을 삭제할 수 있습니다."}
        if len(self.data["lockers"]) <= 1:
            return {"ok": False, "error": "보관함은 한 대 이상 남아 있어야 합니다."}
        cab_id = str(body.get("id") or "")
        with self._lock:
            left = [item for item in self.data["lockers"] if item["id"] != cab_id]
            if len(left) == len(self.data["lockers"]):
                return {"ok": False, "error": "보관함을 찾지 못했습니다."}
            self.data["lockers"] = left
            if self.data.get("selected") == cab_id:
                self.data["selected"] = left[0]["id"]
            self.lives.pop(cab_id, None)
            self._last_users.pop(cab_id, None)
            self.save()
        return {"ok": True}

    def select_locker(self, body: dict[str, Any]) -> dict[str, Any]:
        cab_id = str(body.get("id") or "")
        if not any(item["id"] == cab_id for item in self.data["lockers"]):
            return {"ok": False, "error": "보관함을 찾지 못했습니다."}
        self.data["selected"] = cab_id
        self.save()
        return {"ok": True}

    def save_purpose_label(self, text: str, actor: dict[str, Any] | None) -> dict[str, Any]:
        if not actor or actor.get("role") != "master":
            return {"ok": False, "error": "마스터 관리자만 불출 항목을 수정할 수 있습니다."}
        label = _clip(text, 10) or "젤루텍"
        with self._lock:
            self.data["purpose_label"] = label
            self.save()
        return {"ok": True, "label": label}

    def save_memo(self, text: str, actor: dict[str, Any] | None) -> dict[str, Any]:
        if not actor or actor.get("role") != "master":
            return {"ok": False, "error": "마스터 관리자만 메모를 수정할 수 있습니다."}
        with self._lock:
            self._selected()["memo"] = (text or "")[:500]
            self.save()
        return {"ok": True}

    def history_rows(self, query: dict[str, str]) -> list[dict[str, Any]]:
        rows = list(self.data.get("history") or [])
        start = (query.get("from") or "").strip()
        end = (query.get("to") or "").strip()
        slot = (query.get("slot") or "").strip()
        rank = (query.get("rank") or "").strip()
        sn = (query.get("sn") or "").strip()
        name = (query.get("name") or "").strip()
        picked = []
        for row in rows:
            ts = row.get("time") or ""
            day = ts[:10]
            if start and day < start:
                continue
            if end and day > end:
                continue
            if slot and str(row.get("slot")) != slot:
                continue
            if rank and rank not in (row.get("rank") or ""):
                continue
            if sn and sn not in (row.get("sn") or ""):
                continue
            if name and name not in (row.get("name") or ""):
                continue
            locker_name = (query.get("locker") or "").strip()
            if locker_name and locker_name != (row.get("locker") or ""):
                continue
            picked.append(row)
        picked.reverse()
        return picked

    def refresh_live(self) -> None:
        for cab in list(self.data["lockers"]):
            self._refresh_one(cab)

    def _refresh_one(self, cab: dict[str, Any]) -> None:
        url = f"http://{cab['host']}:{cab['port']}/api/status"
        try:
            with urllib.request.urlopen(url, timeout=2.5) as res:
                payload = json.loads(res.read().decode("utf-8"))
            board = self._board_status(cab)
            self.lives[cab["id"]] = {"online": True, "error": "", "status": payload, "board": board, "at": _now()}
            self._record_changes(cab, payload)
            self._sync_names(cab, payload)
            self._push_time(cab)
        except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
            self.lives[cab["id"]] = {"online": False, "error": "보관함 응답 없음", "detail": str(exc), "at": _now()}

    def _board_status(self, locker: dict[str, Any]) -> dict[str, Any]:
        url = f"http://{locker['host']}:{locker['port']}/api/locker/status"
        try:
            with urllib.request.urlopen(url, timeout=2.0) as res:
                return json.loads(res.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            return {}

    def _record_changes(self, cab: dict[str, Any], payload: dict[str, Any]) -> None:
        users = (payload.get("users") or {}) if isinstance(payload, dict) else {}
        current: dict[str, dict[str, Any]] = {}
        for slot in range(1, USER_SLOTS + 1):
            info = users.get(str(slot))
            if isinstance(info, dict) and info.get("user_id"):
                current[str(slot)] = {
                    "status": info.get("status") or "stored",
                    "purpose": info.get("purpose") or "",
                }
        previous = self._last_users.get(cab["id"])
        self._last_users[cab["id"]] = current
        if previous is None:
            return
        rows = []
        for slot, info in current.items():
            before = previous.get(slot)
            if not before or before.get("status") == info.get("status"):
                continue
            action = "불출" if info.get("status") == "issued" else "보관"
            purpose = info.get("purpose") or before.get("purpose") or ""
            local = (cab.get("users") or {}).get(slot) or {}
            gun = (cab.get("guns") or {}).get(slot) or {}
            rows.append(
                {
                    "time": _now(),
                    "locker_id": cab["id"],
                    "locker": cab.get("name") or "",
                    "slot": int(slot),
                    "rank": local.get("rank") or "",
                    "sn": local.get("sn") or "",
                    "name": local.get("name") or "",
                    "category": "소총",
                    "model": gun.get("model") or "",
                    "serial": gun.get("serial") or "",
                    "action": action,
                    "purpose": purpose if action == "불출" else "",
                    "detail": f"{action}{(' / ' + purpose) if action == '불출' and purpose else ''}",
                }
            )
        if not rows:
            return
        with self._lock:
            history = self.data.setdefault("history", [])
            history.extend(rows)
            del history[:-5000]
            self.save()

    def _sync_names(self, cab: dict[str, Any], payload: dict[str, Any]) -> None:
        pairs = (
            ("master", self.data["masters"], MASTER_SLOTS),
            ("sub", self.data["subs"], SUB_SLOTS),
            ("user", cab["users"], USER_SLOTS),
        )
        for kind, local_map, count in pairs:
            live_map = payload.get({"master": "masters", "sub": "subs", "user": "users"}[kind]) or {}
            for slot in range(1, count + 1):
                live = live_map.get(str(slot))
                local = local_map.get(str(slot))
                if not isinstance(live, dict) or not live.get("user_id") or not isinstance(local, dict):
                    continue
                name = (local.get("name") or "").strip()
                rank = (local.get("rank") or "").strip()
                label = f"{rank} {name}".strip() if rank else name
                if not label or live.get("name") == label:
                    continue
                self._push_name(cab, kind, slot, name, rank)

    def _push_time(self, cab: dict[str, Any]) -> None:
        url = f"http://{cab['host']}:{cab['port']}/api/time"
        body = json.dumps({"epoch": time.time()}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=2.0).read()
        except (OSError, urllib.error.URLError, TimeoutError):
            return

    def _push_name(self, cab: dict[str, Any], kind: str, slot: int, name: str, rank: str) -> None:
        label = f"{rank} {name}".strip()
        if not label or kind == "operator":
            return
        url = f"http://{cab['host']}:{cab['port']}/api/rename"
        body = json.dumps({"kind": kind, "slot": slot, "name": label}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=2.0).read()
        except (OSError, urllib.error.URLError, TimeoutError):
            return

    def _enrolled(self, kind: str, slot: int) -> bool:
        status = self._live_of(self._selected()["id"]).get("status") or {}
        if kind == "operator":
            return False
        bucket = _bucket(kind)
        info = (status.get(bucket) or {}).get(str(slot))
        return isinstance(info, dict) and bool(info.get("user_id"))

    def _public_record(self, kind: str, slot: int) -> dict[str, Any] | None:
        if kind == "operator":
            return self.data.get("operator")
        if kind == "user":
            return self._selected()["users"].get(str(slot))
        return self.data[_bucket(kind)].get(str(slot))

    def _person_error(self, kind: str, slot: int, body: dict[str, Any], actor: dict[str, Any] | None) -> str:
        if not actor or actor.get("role") != "master":
            return "마스터 관리자만 등록할 수 있습니다."
        if kind not in ("master", "sub", "operator", "user"):
            return "구분 값이 올바르지 않습니다."
        limits = {"master": MASTER_SLOTS, "sub": SUB_SLOTS, "user": USER_SLOTS, "operator": 1}
        if kind != "operator" and (slot < 1 or slot > limits[kind]):
            return "번호가 올바르지 않습니다."
        if kind == "master" and self._public_record(kind, slot) and actor.get("slot") != slot:
            return "다른 마스터 관리자 정보는 열 수 없습니다."
        if not _clip(body.get("rank"), 4) or not _clip(body.get("name"), 5) or not _digits(body.get("sn"), 10):
            return "계급, 성명, 군번을 입력하세요."
        if self._enrolled(kind, slot) and _profile_complete(kind, self._public_record(kind, slot)):
            return "지문이 등록된 정보는 수정할 수 없습니다. 보관함에서 지문을 삭제한 뒤 수정하세요."
        return ""

    def _sens(self, board: dict[str, Any] | None) -> str:
        sens = str((board or {}).get("sens") or "")
        if len(sens) != USER_SLOTS:
            return "0" * USER_SLOTS
        return sens

    def _bays(self, status: dict[str, Any], board: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        users = status.get("users") or {}
        sens = self._sens(board)
        bays = []
        for slot in range(1, USER_SLOTS + 1):
            info = users.get(str(slot))
            enrolled = isinstance(info, dict) and bool(info.get("user_id"))
            name = str((info or {}).get("name") or "").strip() if enrolled else ""
            present = enrolled and sens[slot - 1] == "1"
            bays.append(
                {
                    "slot": slot,
                    "enrolled": enrolled,
                    "issued": enrolled and not present,
                    "name": name or f"사용자{slot}",
                }
            )
        return bays

    def _counts(self, status: dict[str, Any], board: dict[str, Any] | None = None) -> dict[str, Any]:
        sens = self._sens(board)
        enrolled = issued = stored = empty = 0
        purposes = {name: 0 for name in PURPOSES}
        for slot in range(1, USER_SLOTS + 1):
            live = (status.get("users") or {}).get(str(slot))
            if not isinstance(live, dict) or not live.get("user_id"):
                empty += 1
                continue
            enrolled += 1
            if sens[slot - 1] == "1":
                stored += 1
                continue
            issued += 1
            purpose = str(live.get("purpose") or "").strip()
            if purpose not in purposes:
                purpose = "기타"
            purposes[purpose] += 1
        return {
            "registered": enrolled,
            "issued": issued,
            "stored": stored,
            "empty": empty,
            "unpaid": stored,
            "purposes": [{"name": name, "count": purposes[name]} for name in PURPOSES],
        }

    def snapshot(self, actor: dict[str, Any] | None) -> dict[str, Any]:
        privileged = bool(actor)
        operator = bool(actor and actor.get("role") == "operator")
        master = bool(actor and actor.get("role") == "master")
        cab = self._selected()
        live = self._live_of(cab["id"])
        status = live.get("status") or {}
        board = live.get("board") or {}
        door = str(board.get("door") or "")
        sens = str(board.get("sens") or "")
        counts = self._counts(status, board)
        purposes = counts["purposes"]
        slots = []
        present = 0
        for slot in range(1, USER_SLOTS + 1):
            info = (status.get("users") or {}).get(str(slot))
            enrolled = isinstance(info, dict) and bool(info.get("user_id"))
            state = (info or {}).get("status") if enrolled else ""
            if slot - 1 < len(sens) and sens[slot - 1] == "1":
                present += 1
            local = cab["users"].get(str(slot))
            gun = cab["guns"].get(str(slot))
            slots.append(
                {
                    "slot": slot,
                    "enrolled": enrolled,
                    "status": state or ("empty" if not enrolled else "stored"),
                    "purpose": (info or {}).get("purpose") if enrolled else "",
                    "live_name": (info or {}).get("name") if enrolled else "",
                    "door_open": slot - 1 < len(door) and door[slot - 1] == "1",
                    "present": slot - 1 < len(sens) and sens[slot - 1] == "1",
                    "person": _view_person(local, master or operator) if privileged else None,
                    "gun": gun if privileged else None,
                    "locked": enrolled and _profile_complete("user", local),
                }
            )
        boards = []
        for item in self.data["lockers"]:
            item_live = self._live_of(item["id"])
            item_board = item_live.get("board") or {}
            item_counts = self._counts(item_live.get("status") or {}, item_board) if item_live.get("online") else {
                "registered": 0, "issued": 0, "stored": 0, "empty": 0, "unpaid": 0,
                "purposes": [{"name": name, "count": 0} for name in PURPOSES],
            }
            boards.append({
                "id": item["id"],
                "name": item["name"],
                "host": item["host"],
                "port": item["port"],
                "selected": item["id"] == cab["id"],
                "online": bool(item_live.get("online")),
                "error": item_live.get("error") or "",
                "checked_at": item_live.get("at") or "",
                "sensor": bool(((item_live.get("status") or {}).get("sensor") or {}).get("connected")),
                "bays": self._bays(item_live.get("status") or {}, item_board),
                **item_counts,
            })
        return {
            "who": actor,
            "needs_bootstrap": self.master_count() == 0,
            "boards": boards,
            "locker": {
                "id": cab["id"],
                "name": cab["name"],
                "host": cab["host"],
                "port": cab["port"],
                "online": bool(live.get("online")),
                "error": live.get("error") or "",
                "sensor": bool((status.get("sensor") or {}).get("connected")),
                "checked_at": live.get("at") or "",
            },
            "slots": slots,
            "summary": {"stored": counts["stored"], "issued": counts["issued"], "empty": counts["empty"], "present": present, "registered": counts["registered"]},
            "purposes": purposes,
            "purpose_label": self.data.get("purpose_label") or "젤루텍",
            "catalog": [
                {"index": index, "no": index + 1, "category": item["category"], "model": item["model"]}
                for index, item in enumerate(cab.get("catalog") or [])
            ] if master else [],
            "masters": self._people_view("master", MASTER_SLOTS, master),
            "subs": self._people_view("sub", SUB_SLOTS, master),
            "operator": self._operator_view(master or operator),
            "memo": cab.get("memo") or "" if master else "",
            "can": {
                "manage": master,
                "history": master or operator,
                "status": True,
            },
        }

    def _people_view(self, kind: str, count: int, show: bool) -> list[dict[str, Any]]:
        status = self._live_of(self._selected()["id"]).get("status") or {}
        bucket = _bucket(kind)
        rows = []
        for slot in range(1, count + 1):
            local = self._public_record(kind, slot)
            live = (status.get(bucket) or {}).get(str(slot))
            enrolled = isinstance(live, dict) and bool(live.get("user_id"))
            rows.append(
                {
                    "slot": slot,
                    "enrolled": enrolled,
                    "live_name": (live or {}).get("name") if enrolled else "",
                    "person": _view_person(local, show),
                    "locked": enrolled and _profile_complete(kind, local),
                    "has_login": bool(local and local.get("password")),
                }
            )
        return rows

    def _operator_view(self, show: bool) -> dict[str, Any]:
        local = self.data.get("operator")
        return {
            "person": _view_person(local, show),
            "locked": _profile_complete("operator", local),
            "has_login": bool(local and local.get("password")),
        }


def _bucket(kind: str) -> str:
    return {"master": "masters", "sub": "subs", "user": "users"}[kind]


def _clip(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _digits(value: Any, limit: int) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    return text[:limit]


def _profile_complete(kind: str, record: dict[str, Any] | None) -> bool:
    if not record:
        return False
    if not record.get("rank") or not record.get("name") or not record.get("sn"):
        return False
    if kind in ("master", "operator"):
        return bool(record.get("login_id") and record.get("password"))
    return True


def _view_person(record: dict[str, Any] | None, show: bool) -> dict[str, Any] | None:
    if not show or not record:
        return None
    return {
        "rank": record.get("rank") or "",
        "name": record.get("name") or "",
        "sn": record.get("sn") or "",
        "login_id": record.get("login_id") or "",
    }


STORE = Store()


def poll_forever(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            STORE.refresh_live()
        except Exception:
            pass
        stop.wait(2.0)


def history_csv(rows: list[dict[str, Any]]) -> str:
    header = ["시간", "보관함", "칸", "계급", "군번", "성명", "총기종류", "모델", "총기번호", "내용"]
    lines = [",".join(header)]
    for row in rows:
        cells = [
            row.get("time", ""),
            row.get("locker", ""),
            str(row.get("slot", "")),
            row.get("rank", ""),
            row.get("sn", ""),
            row.get("name", ""),
            row.get("category", ""),
            row.get("model", ""),
            row.get("serial", ""),
            row.get("detail", ""),
        ]
        lines.append(",".join('"' + str(cell).replace('"', '""') + '"' for cell in cells))
    return "\n".join(lines) + "\n"
