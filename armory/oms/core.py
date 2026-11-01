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
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

PURPOSES = ("사격", "훈련", "정비", "근무", "기타", "젤루텍")
MASTER_SLOTS = 5
SUB_SLOTS = 30
USER_SLOTS = 10
OMS_PI_HEADER = {"X-Zellootec-OMS": "1"}

ID_RE = re.compile(r"^[a-z0-9!@#$%^&*_\-+=.?]{1,10}$")


def _pi_request(url: str, data: bytes | None = None, timeout: float = 1.2) -> bytes:
    headers = dict(OMS_PI_HEADER)
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def data_path() -> Path:
    from oms_bundle import oms_json_path

    return oms_json_path()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _event_stamp(raw: Any) -> str:
    text = str(raw or "").strip().replace("T", " ")[:19]
    try:
        datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""
    return text


def _history_cutoff() -> datetime:
    now = datetime.now()
    try:
        return now.replace(year=now.year - 5)
    except ValueError:
        return now.replace(year=now.year - 5, day=28)


def _trim_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cutoff = _history_cutoff()
    kept: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            when = datetime.strptime(str(row.get("time") or "")[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            kept.append(row)
            continue
        if when >= cutoff:
            kept.append(row)
    return kept


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
        "kind": "rifle",
        "last_reset_seq": 0,
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
        "unit_name": "지문인식",
        # Door-sensor stand-in: while True, person AI alerts are suppressed.
        "armory_inside": False,
    }


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_path()
        self._lock = threading.Lock()
        self.data = empty_store()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.lives: dict[str, dict[str, Any]] = {}
        self._last_users: dict[str, dict[str, dict[str, Any]] | None] = {}
        self._recent_actions: dict[tuple[str, str], tuple[str, float]] = {}
        self._after_lock = threading.Lock()
        self._after_latest: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
        self._after_running: dict[str, bool] = {}
        self._misses: dict[str, int] = {}
        self._time_sent: dict[str, float] = {}
        self._offline_alerted: set[str] = set()
        self._pulse = 0
        self._alerts: list[dict[str, Any]] = []
        self._seen_pi_alerts: set[str] = set()
        self._seen_pi_access: set[str] = set()
        self._last_door: dict[str, str] = {}
        self._last_sens: dict[str, str] = {}
        self._authorized_open_until: dict[str, float] = {}
        self._emergency_until: dict[str, float] = {}
        self._ops_gen = 0
        self._ops_aimed = False
        self._person_until = 0.0
        # Person AI: yellow OMS popup open; brief mute after ack (no empty-frame wait).
        self._person_alert_open = False
        self._person_need_clear = False
        self._person_need_clear_until = 0.0
        self._person_ack_mute_until = 0.0
        self._PERSON_NEED_CLEAR_SEC = 90.0
        self._PERSON_ACK_MUTE_SEC = 0.8
        self._alert_seq = 0
        self._log_seq = 0
        self._logs: list[dict[str, Any]] = []
        # RFID watch uses rising-edge in rfid.py; keep only a tiny anti-double-fire window.
        self._rfid_tag_seen: dict[str, float] = {}
        self._RFID_TAG_COOLDOWN_SEC = 0.6
        self._RFID_ISSUE_MATCH_SEC = 120.0
        # Fingerprint-authorized issue windows for gate RFID match (never emergency 불출).
        self._rfid_ok_until: dict[str, float] = {}
        # While True (until monotonic), gun RFID enroll is in progress — no gate alarms.
        self._rfid_enroll_until = 0.0
        self.load()
        self._push_log("OMS 시작", "info")

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
        base["history"] = _trim_history(list(base.get("history") or []))
        label = loaded.get("purpose_label")
        base["purpose_label"] = str(label).strip()[:10] if isinstance(label, str) and label.strip() else "젤루텍"
        unit = loaded.get("unit_name")
        base["unit_name"] = str(unit).strip()[:20] if isinstance(unit, str) and unit.strip() else "지문인식"
        base["armory_inside"] = bool(loaded.get("armory_inside"))
        lockers = loaded.get("lockers")
        if not isinstance(lockers, list):
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
            kind = str(raw.get("kind") or "rifle").strip()
            cab["kind"] = kind if kind in ("rifle", "pistol") else "rifle"
            try:
                cab["last_reset_seq"] = int(raw.get("last_reset_seq") or 0)
            except (TypeError, ValueError):
                cab["last_reset_seq"] = 0
            cleaned.append(cab)
        base["lockers"] = cleaned
        if not any(item["id"] == str(base.get("selected")) for item in base["lockers"]):
            base["selected"] = base["lockers"][0]["id"] if base["lockers"] else ""
        for key, count in (("masters", MASTER_SLOTS), ("subs", SUB_SLOTS)):
            base[key] = _merge_slots(base.get(key), count)
        self.data = base

    def save(self) -> None:
        from oms_bundle import write_store_document

        # Keep cctv / patlite sections already living in the same oms.json.
        self.path = write_store_document(self.data)

    def _selected(self) -> dict[str, Any] | None:
        lockers = self.data.get("lockers") or []
        if not lockers:
            self.data["selected"] = ""
            return None
        chosen = str(self.data.get("selected") or "")
        for item in lockers:
            if item["id"] == chosen:
                return item
        self.data["selected"] = lockers[0]["id"]
        return lockers[0]

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
            cab = self._selected()
            if kind == "operator":
                self.data["operator"] = next_rec
            elif kind == "user":
                if cab is None:
                    return {"ok": False, "error": "보관함을 먼저 등록하세요."}
                cab["users"][str(slot)] = next_rec
            else:
                self.data[_bucket(kind)][str(slot)] = next_rec
            self.save()
            if enrolled and cab:
                self._push_name(cab, kind, slot, name, rank)
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
                cab = self._selected()
                if cab is None:
                    return {"ok": False, "error": "보관함을 먼저 등록하세요."}
                cab["users"][str(slot)] = None
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
            cab = self._selected()
            if cab is None:
                return {"ok": False, "error": "보관함을 먼저 등록하세요."}
            current = cab["guns"].get(str(slot)) or {}
            if "serial" in body:
                serial = _clip(body.get("serial"), 20)
            else:
                serial = str(current.get("serial") or "")
            if "rfid_uid" in body:
                rfid_uid = _normalize_rfid_uid(body.get("rfid_uid"))
            else:
                rfid_uid = _normalize_rfid_uid(current.get("rfid_uid"))
            if rfid_uid and rfid_uid != _normalize_rfid_uid(current.get("rfid_uid")):
                owner = self._find_rfid_owner(
                    rfid_uid,
                    skip_locker_id=str(cab.get("id") or ""),
                    skip_slot=slot,
                )
                if owner:
                    return {
                        "ok": False,
                        "error": (
                            f"이 RFID 스티커는 이미 "
                            f"{owner.get('locker') or '보관함'} {owner.get('slot')}번 칸에 "
                            f"등록되어 있습니다."
                        ),
                        "owner": owner,
                    }
            cab["guns"][str(slot)] = {
                "category": category,
                "model": model,
                "serial": serial,
                "rfid_uid": rfid_uid,
            }
            self.save()
        return {"ok": True}

    def _find_rfid_owner(
        self,
        uid_norm: str,
        *,
        skip_locker_id: str | None = None,
        skip_slot: int | None = None,
    ) -> dict[str, Any] | None:
        """Return locker/slot already using this RFID UID, if any."""
        if not uid_norm:
            return None
        for cab in self.data.get("lockers") or []:
            if not isinstance(cab, dict):
                continue
            cab_id = str(cab.get("id") or "")
            for other_slot, other in (cab.get("guns") or {}).items():
                if not isinstance(other, dict):
                    continue
                try:
                    slot_no = int(other_slot)
                except (TypeError, ValueError):
                    continue
                if skip_locker_id and cab_id == str(skip_locker_id) and skip_slot == slot_no:
                    continue
                if _normalize_rfid_uid(other.get("rfid_uid")) == uid_norm:
                    return {
                        "locker_id": cab_id,
                        "locker": str(cab.get("name") or cab_id),
                        "slot": slot_no,
                        "serial": str(other.get("serial") or ""),
                        "model": str(other.get("model") or ""),
                        "rfid_uid": uid_norm,
                    }
        return None

    def bind_gun_rfid(self, slot: int, uid: str, actor: dict[str, Any] | None) -> dict[str, Any]:
        """Bind an RFID tag UID to the gun registered on this bay."""
        if not actor or actor.get("role") != "master":
            return {"ok": False, "error": "마스터 관리자만 RFID를 등록할 수 있습니다."}
        if slot < 1 or slot > USER_SLOTS:
            return {"ok": False, "error": "칸 번호가 올바르지 않습니다."}
        uid_norm = _normalize_rfid_uid(uid)
        if not uid_norm:
            return {"ok": False, "error": "RFID UID가 올바르지 않습니다."}
        with self._lock:
            cab = self._selected()
            if cab is None:
                return {"ok": False, "error": "보관함을 먼저 등록하세요."}
            gun = cab["guns"].get(str(slot))
            if not isinstance(gun, dict) or not (gun.get("serial") or gun.get("model")):
                return {"ok": False, "error": "먼저 총기종류·총기번호를 저장한 뒤 RFID를 등록하세요."}
            # Same tag already on this bay → treat as success (idempotent).
            if _normalize_rfid_uid(gun.get("rfid_uid")) == uid_norm:
                return {
                    "ok": True,
                    "slot": slot,
                    "rfid_uid": uid_norm,
                    "serial": str(gun.get("serial") or ""),
                    "model": str(gun.get("model") or ""),
                }
            # Block duplicate sticker across all lockers / bays.
            owner = self._find_rfid_owner(
                uid_norm,
                skip_locker_id=str(cab.get("id") or ""),
                skip_slot=slot,
            )
            if owner:
                return {
                    "ok": False,
                    "error": (
                        f"이 RFID 스티커는 이미 "
                        f"{owner.get('locker') or '보관함'} {owner.get('slot')}번 칸에 "
                        f"등록되어 있습니다. 해제 후 다시 등록하세요."
                    ),
                    "owner": owner,
                }
            gun = dict(gun)
            gun["rfid_uid"] = uid_norm
            cab["guns"][str(slot)] = gun
            self.save()
            return {
                "ok": True,
                "slot": slot,
                "rfid_uid": uid_norm,
                "serial": str(gun.get("serial") or ""),
                "model": str(gun.get("model") or ""),
            }

    def unbind_gun_rfid(self, slot: int, actor: dict[str, Any] | None) -> dict[str, Any]:
        if not actor or actor.get("role") != "master":
            return {"ok": False, "error": "마스터 관리자만 RFID를 해제할 수 있습니다."}
        if slot < 1 or slot > USER_SLOTS:
            return {"ok": False, "error": "칸 번호가 올바르지 않습니다."}
        with self._lock:
            cab = self._selected()
            if cab is None:
                return {"ok": False, "error": "보관함을 먼저 등록하세요."}
            gun = cab["guns"].get(str(slot))
            if not isinstance(gun, dict):
                return {"ok": False, "error": "등록된 총기가 없습니다."}
            gun = dict(gun)
            gun["rfid_uid"] = ""
            cab["guns"][str(slot)] = gun
            self.save()
            return {"ok": True, "slot": slot, "rfid_uid": ""}

    def find_gun_by_rfid(self, uid: str) -> dict[str, Any] | None:
        uid_norm = _normalize_rfid_uid(uid)
        if not uid_norm:
            return None
        with self._lock:
            owner = self._find_rfid_owner(uid_norm)
            if not owner:
                return None
            cab = next(
                (
                    item
                    for item in (self.data.get("lockers") or [])
                    if str(item.get("id") or "") == str(owner.get("locker_id") or "")
                ),
                None,
            )
            gun = ((cab or {}).get("guns") or {}).get(str(owner.get("slot"))) or {}
            return {
                "locker_id": owner.get("locker_id"),
                "locker": owner.get("locker"),
                "slot": owner.get("slot"),
                "serial": str(gun.get("serial") or owner.get("serial") or ""),
                "model": str(gun.get("model") or owner.get("model") or ""),
                "category": str(gun.get("category") or ""),
                "rfid_uid": uid_norm,
            }

    def _rfid_ok_key(self, cab_id: str, slot: int) -> str:
        return f"{cab_id}:{int(slot)}"

    def _mark_rfid_ok_issue(self, cab_id: str, slot: int, sec: float | None = None) -> None:
        """Allow gate RFID for this bay after a real fingerprint issue (not emergency)."""
        hold = float(self._RFID_ISSUE_MATCH_SEC if sec is None else sec)
        self._rfid_ok_until[self._rfid_ok_key(cab_id, slot)] = time.monotonic() + max(1.0, hold)

    def _clear_rfid_ok_issue(self, cab_id: str, slot: int) -> None:
        self._rfid_ok_until.pop(self._rfid_ok_key(cab_id, slot), None)

    def _rfid_expected_issue_slots(self) -> list[tuple[str, int]]:
        """Slots from confirmed normal 불출 only — never store unlock / emergency 불출."""
        now = time.monotonic()
        out: list[tuple[str, int]] = []
        with self._lock:
            for key, until in list(self._rfid_ok_until.items()):
                if now >= float(until or 0.0):
                    self._rfid_ok_until.pop(key, None)
                    continue
                try:
                    cab_id, slot_s = str(key).split(":", 1)
                    out.append((str(cab_id), int(slot_s)))
                except (TypeError, ValueError):
                    continue
        return out

    def begin_rfid_enroll(self, seconds: float = 90.0) -> None:
        """Pause gate-side RFID alarms while a sticker is being bound to a gun."""
        with self._lock:
            self._rfid_enroll_until = time.monotonic() + max(1.0, float(seconds or 0.0))

    def end_rfid_enroll(self, quiet_after: float = 25.0) -> None:
        """Keep enroll-quiet window briefly so the pad tag does not fire gate logic."""
        with self._lock:
            self._rfid_enroll_until = time.monotonic() + max(0.0, float(quiet_after or 0.0))

    def rfid_enrolling(self) -> bool:
        with self._lock:
            return time.monotonic() < float(self._rfid_enroll_until or 0.0)

    def handle_rfid_tag(self, uid: str) -> dict[str, Any]:
        """Handle one continuous-inventory tag read (gate / exit tagging).

        - Registered sticker → live log that that bay's gun left the armory
        - cross_check + recent fingerprint issue bay ≠ RFID bay → emergency alarm
        - Unknown sticker → log only (no popup / no tower) so RFID 등록 작업이 가능
        """
        uid_norm = _normalize_rfid_uid(uid)
        if not uid_norm:
            return {"ok": False, "error": "empty uid"}
        if self.rfid_enrolling():
            return {"ok": True, "enrolling": True, "uid": uid_norm, "alarm": False}
        now = time.monotonic()
        with self._lock:
            last = float(self._rfid_tag_seen.get(uid_norm) or 0.0)
            if now - last < float(self._RFID_TAG_COOLDOWN_SEC):
                return {"ok": True, "deduped": True, "uid": uid_norm}
            self._rfid_tag_seen[uid_norm] = now
            if len(self._rfid_tag_seen) > 200:
                cutoff = now - float(self._RFID_TAG_COOLDOWN_SEC) * 2
                self._rfid_tag_seen = {
                    key: ts for key, ts in self._rfid_tag_seen.items() if ts >= cutoff
                }

        try:
            from rfid import load_config as rfid_load_config

            cfg = rfid_load_config()
        except Exception:
            cfg = {"cross_check": True, "mismatch_alarm": True}
        cross_check = bool(cfg.get("cross_check", True))
        mismatch_alarm = bool(cfg.get("mismatch_alarm", True))

        gun = self.find_gun_by_rfid(uid_norm)
        if not gun:
            # 미등록 = 등록 대기 스티커. 비상/미인가 경고·경광등에서 제외.
            short = uid_norm if len(uid_norm) <= 16 else f"{uid_norm[:8]}…{uid_norm[-4:]}"
            self._push_log(f"미등록 RFID 태그 인식 (등록 가능) · {short}", "info")
            return {"ok": True, "registered": False, "uid": uid_norm, "alarm": False}

        try:
            slot = int(gun.get("slot") or 0)
        except (TypeError, ValueError):
            slot = 0
        locker_id = str(gun.get("locker_id") or "")
        locker_name = str(gun.get("locker") or "")
        serial = str(gun.get("serial") or "").strip()
        model = str(gun.get("model") or "").strip()
        gun_label = " ".join(part for part in (model, serial) if part) or "총기"
        with self._lock:
            cab = next(
                (
                    item
                    for item in (self.data.get("lockers") or [])
                    if str(item.get("id") or "") == locker_id
                ),
                None,
            )
        subject = self._rfid_subject(cab, slot)

        def _save_rfid_history(status: str, detail: str) -> None:
            if not cab:
                return
            self._append_history([
                self._rfid_history_row(
                    cab,
                    slot,
                    gun_label=gun_label,
                    uid=uid_norm,
                    status=status,
                    detail=detail,
                )
            ])

        def _record_cctv(reason: str, message: str) -> None:
            try:
                from cctv import trigger_event_recording

                trigger_event_recording(
                    reason=reason,
                    message=message,
                    locker=locker_name or str((cab or {}).get("name") or ""),
                    slot=slot if slot else None,
                )
            except Exception:
                pass

        if not cross_check:
            leave_msg = f"{subject} 총기가 무기고에서 불출되었습니다."
            self._push_log(leave_msg, "warn", cab, slot if slot else None)
            _save_rfid_history(
                "tag",
                f"무기고 불출 / RFID 태깅 · {subject}",
            )
            return {
                "ok": True,
                "registered": True,
                "uid": uid_norm,
                "slot": slot,
                "locker": locker_name,
                "matched": None,
                "alarm": False,
            }

        # 보관 / 비상개폐 이후 태깅은 무조건 비상 (정상 불출 일치보다 우선).
        if self._rfid_tag_must_alarm(cab, slot):
            bad = (
                f"RFID 태깅 비상: {subject}. "
                f"보관함 보관 상태인데 무기고에서 태그가 인식되었습니다."
            )
            self._push_log(bad, "error", cab, slot if slot else None)
            _save_rfid_history("stored_tag", f"무기고 비상 / {bad}")
            if mismatch_alarm:
                self._queue_alert("rfid_emergency", bad, cab, slot if slot else None)
                _record_cctv("rfid_emergency", bad)
            return {
                "ok": True,
                "registered": True,
                "uid": uid_norm,
                "slot": slot,
                "locker": locker_name,
                "matched": False,
                "stored": True,
                "alarm": mismatch_alarm,
            }

        expected = self._rfid_expected_issue_slots()
        matched = (locker_id, slot) in expected

        if matched:
            leave_msg = f"{subject} 총기가 무기고에서 불출되었습니다."
            self._push_log(leave_msg, "warn", cab, slot if slot else None)
            self._push_log(
                f"RFID 이중교차 일치 · {subject}",
                "ok",
                cab,
                slot if slot else None,
            )
            _save_rfid_history(
                "match",
                f"무기고 불출 / RFID 태깅(일치) · {subject}",
            )
            return {
                "ok": True,
                "registered": True,
                "uid": uid_norm,
                "slot": slot,
                "locker": locker_name,
                "matched": True,
                "alarm": False,
            }

        if expected:
            # Fingerprint issued a different bay than the tagged gun.
            exp_txt = ", ".join(f"{s}번" for _c, s in expected[:6]) or "?"
            bad = (
                f"RFID 불일치: 지문 불출 칸({exp_txt})과 "
                f"태깅 총기({subject})가 다릅니다."
            )
            self._push_log(bad, "error", cab, slot if slot else None)
            _save_rfid_history("mismatch", f"무기고 불출 / {bad}")
            if mismatch_alarm:
                self._queue_alert("emergency", bad, cab, slot if slot else None)
                _record_cctv("emergency", bad)
            return {
                "ok": True,
                "registered": True,
                "uid": uid_norm,
                "slot": slot,
                "matched": False,
                "alarm": mismatch_alarm,
            }

        # Door/fingerprint issue never opened — tag at gate only.
        bad = (
            f"RFID 태깅 비상: {subject}. "
            f"보관함 문을 열지 않았는데 무기고에서 태그가 인식되었습니다."
        )
        self._push_log(bad, "error", cab, slot if slot else None)
        _save_rfid_history("no_issue", f"무기고 비상 / {bad}")
        if mismatch_alarm:
            self._queue_alert("rfid_emergency", bad, cab, slot if slot else None)
            _record_cctv("rfid_emergency", bad)
        return {
            "ok": True,
            "registered": True,
            "uid": uid_norm,
            "slot": slot,
            "locker": locker_name,
            "matched": False,
            "alarm": mismatch_alarm,
        }

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
            cab = self._selected()
            if cab is None:
                return {"ok": False, "error": "보관함을 먼저 등록하세요."}
            catalog = cab.setdefault("catalog", [])
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
            cab = self._selected()
            if cab is None:
                return {"ok": False, "error": "보관함을 먼저 등록하세요."}
            catalog = cab.setdefault("catalog", [])
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
            cab = self._selected()
            if cab is None:
                return {"ok": False, "error": "보관함을 먼저 등록하세요."}
            cab["guns"][str(slot)] = None
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
            if cab is None:
                return {"ok": False, "error": "보관함을 먼저 등록하세요."}
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
        cab_id = str(body.get("id") or "")
        with self._lock:
            left = [item for item in self.data["lockers"] if item["id"] != cab_id]
            if len(left) == len(self.data["lockers"]):
                return {"ok": False, "error": "보관함을 찾지 못했습니다."}
            self.data["lockers"] = left
            if not left or self.data.get("selected") == cab_id:
                self.data["selected"] = left[0]["id"] if left else ""
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

    def save_unit_name(self, text: str, actor: dict[str, Any] | None) -> dict[str, Any]:
        if not actor or actor.get("role") != "master":
            return {"ok": False, "error": "로그인한 뒤에 수정할 수 있습니다." if not actor else "이 계정으로는 수정할 수 없습니다."}
        name = _clip(text, 20) or "지문인식"
        with self._lock:
            self.data["unit_name"] = name
            self.save()
        return {"ok": True, "name": name}

    def save_purpose_label(self, text: str, actor: dict[str, Any] | None) -> dict[str, Any]:
        if not actor:
            return {"ok": False, "error": "로그인한 뒤에 수정할 수 있습니다."}
        label = _clip(text, 10) or "젤루텍"
        with self._lock:
            self.data["purpose_label"] = label
            self.save()
            lockers = list(self.data["lockers"])
        self._push_purpose_label(lockers, label)
        return {"ok": True, "label": label}

    def _push_purpose_label(self, lockers: list[dict[str, Any]], label: str) -> None:
        body = json.dumps({"label": label}).encode("utf-8")
        for cab in lockers:
            url = f"http://{cab['host']}:{cab['port']}/api/purpose-label"
            try:
                _pi_request(url, data=body, timeout=2.0)
            except (OSError, urllib.error.URLError, TimeoutError):
                continue

    def save_memo(self, text: str, actor: dict[str, Any] | None) -> dict[str, Any]:
        if not actor or actor.get("role") != "master":
            return {"ok": False, "error": "마스터 관리자만 메모를 수정할 수 있습니다."}
        with self._lock:
            cab = self._selected()
            if cab is None:
                return {"ok": False, "error": "보관함을 먼저 등록하세요."}
            cab["memo"] = (text or "")[:500]
            self.save()
        return {"ok": True}

    def history_rows(self, query: dict[str, str]) -> list[dict[str, Any]]:
        rows = list(self.data.get("history") or [])
        cab = self._selected()
        locker_id = (query.get("locker_id") or "").strip() or (str(cab["id"]) if cab else "")
        if not locker_id:
            return []
        start = (query.get("from") or "").strip()
        end = (query.get("to") or "").strip()
        slot = (query.get("slot") or "").strip()
        rank = (query.get("rank") or "").strip()
        sn = (query.get("sn") or "").strip()
        name = (query.get("name") or "").strip()
        scope = (query.get("scope") or "gun").strip().lower()
        if scope not in ("gun", "all"):
            scope = "gun"
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
            if str(row.get("locker_id")) != locker_id:
                continue
            locker_name = (query.get("locker") or "").strip()
            if locker_name and locker_name != (row.get("locker") or ""):
                continue
            row_kind = str(row.get("kind") or "gun")
            if scope == "gun" and row_kind != "gun":
                continue
            picked.append(row)
        picked.reverse()
        return picked

    def pending_alerts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._alerts]

    def pending_logs(self, limit: int = 40) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._logs[-max(1, int(limit or 40)) :]]

    def ack_alerts(self, ids: list[Any]) -> list[dict[str, Any]]:
        wanted = set()
        for raw in ids:
            try:
                wanted.add(int(raw))
            except (TypeError, ValueError):
                continue
        removed_situation = False
        removed_person = False
        next_kind = ""
        next_locker = ""
        next_no = 0
        with self._lock:
            before = list(self._alerts)
            self._alerts = [item for item in self._alerts if int(item.get("id") or 0) not in wanted]
            removed = [item for item in before if item not in self._alerts]
            situation = {"emergency", "unauthorized", "offline", "rfid_emergency"}
            removed_situation = any(str(item.get("kind") or "") in situation for item in removed)
            removed_person = any(str(item.get("kind") or "") == "person" for item in removed)
            if removed_person:
                self._person_alert_open = False
                # After confirm: allow re-detect quickly (short mute only — no empty-frame wait).
                self._person_need_clear = False
                self._person_need_clear_until = 0.0
                self._person_ack_mute_until = time.monotonic() + float(self._PERSON_ACK_MUTE_SEC)
                self._person_until = 0.0
            pending = [item for item in self._alerts if str(item.get("kind") or "") in situation]
            if pending:
                next_kind = str(pending[0].get("kind") or "")
                next_locker = str(pending[0].get("locker_id") or "")
                try:
                    next_no = self._locker_number({"id": next_locker})
                except Exception:
                    next_no = 0
            left = [dict(item) for item in self._alerts]
        if removed_person:
            # Always stop follow on person confirm; Home only when nothing else needs the camera.
            try:
                from person_track import stop_person_track

                stop_person_track(home=False)
            except Exception:
                pass
        if removed_situation and next_kind:
            try:
                from person_track import stop_person_track

                stop_person_track(home=False)
            except Exception:
                pass
            try:
                from cctv import ptz_goto_event

                threading.Thread(
                    target=ptz_goto_event,
                    args=(next_kind,),
                    kwargs={"locker_id": next_locker, "locker_no": next_no},
                    daemon=True,
                ).start()
            except Exception:
                pass
        elif removed_situation or removed_person:
            # Last cleared alarm of this ack — Home only if no other situation/person/ops remains.
            self._home_if_no_alarm()
        if removed_situation or removed_person:
            self._sync_patlite()
        return left

    def _push_log(
        self,
        text: str,
        level: str = "info",
        cab: dict[str, Any] | None = None,
        slot: int | None = None,
    ) -> None:
        message = str(text or "").strip()
        if not message:
            return
        level_name = str(level or "info").strip().lower() or "info"
        if level_name not in ("info", "warn", "error", "ok"):
            level_name = "info"
        locker_name = str((cab or {}).get("name") or "").strip()
        with self._lock:
            self._log_seq += 1
            item = {
                "id": self._log_seq,
                "time": _now(),
                "level": level_name,
                "text": message[:160],
                "locker": locker_name,
                "slot": int(slot) if isinstance(slot, int) or (isinstance(slot, str) and str(slot).isdigit()) else slot,
            }
            self._logs.append(item)
            if len(self._logs) > 200:
                self._logs = self._logs[-200:]

    def _queue_alert(self, kind: str, message: str, cab: dict[str, Any] | None = None, slot: int | None = None) -> dict[str, Any]:
        locker_id = str((cab or {}).get("id") or "")
        situation = kind in ("emergency", "unauthorized", "offline", "rfid_emergency")
        deduped = False
        item: dict[str, Any] | None = None
        with self._lock:
            for existing in list(self._alerts):
                if (
                    str(existing.get("kind") or "") == kind
                    and str(existing.get("locker_id") or "") == locker_id
                    and existing.get("slot") == slot
                ):
                    # RFID 태깅 비상은 재태깅마다 새 알림으로 다시 띄움.
                    if kind == "rfid_emergency":
                        self._alerts = [row for row in self._alerts if row is not existing]
                        break
                    item = dict(existing)
                    deduped = True
                    break
            if not deduped:
                self._alert_seq += 1
                item = {
                    "id": self._alert_seq,
                    "kind": kind,
                    "message": message,
                    "locker_id": locker_id,
                    "locker": (cab or {}).get("name") or "",
                    "slot": slot,
                    "time": _now(),
                }
                self._alerts.append(item)
                if len(self._alerts) > 50:
                    self._alerts = self._alerts[-50:]
        if situation:
            # Always (re)sound the tower — even when the OMS popup was already open.
            # A failed HID write on the first edge used to leave a silent pending alert.
            try:
                from patlite import notify_alarm

                notify_alarm()
            except Exception:
                pass
            if not deduped:
                try:
                    from cctv import open_situation, ptz_goto_event

                    if open_situation(time.time()):
                        # Red situation owns the camera — stop person follow so aims don't fight.
                        try:
                            from person_track import stop_person_track

                            stop_person_track(home=False)
                        except Exception:
                            pass
                        locker_no = self._locker_number(cab) if cab else 0
                        threading.Thread(
                            target=ptz_goto_event,
                            args=(kind,),
                            kwargs={"locker_id": locker_id, "locker_no": locker_no},
                            daemon=True,
                        ).start()
                except Exception:
                    pass
        if not deduped and item is not None:
            level = "error" if kind in ("emergency", "unauthorized", "offline", "rfid_emergency", "unreturned") else "warn"
            flat = " ".join(str(message or "").split())
            # message already includes locker name for offline; avoid '1번 보관함' prefix
            if kind == "offline":
                self._push_log(flat, level, cab, slot)
            else:
                label = self._locker_index_label(cab) if cab else ""
                slot_bit = f" {int(slot)}번" if slot else ""
                prefix = f"[{label}{slot_bit}] " if label or slot_bit else ""
                self._push_log(f"{prefix}{flat}", level, cab, slot)
        return dict(item or {})

    def _append_history(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with self._lock:
            history = _trim_history(self.data.setdefault("history", []) + rows)
            self.data["history"] = history
            self.save()
        for row in rows:
            if not isinstance(row, dict):
                continue
            kind = str(row.get("kind") or "gun")
            if kind == "security":
                continue  # already logged via _queue_alert
            detail = str(row.get("detail") or row.get("action") or "").strip()
            if not detail:
                continue
            locker = str(row.get("locker") or "").strip()
            slot = row.get("slot")
            slot_bit = f"{slot}번 " if slot not in (None, "", 0, "0") else ""
            who = str(row.get("name") or "").strip()
            who_bit = f"{who} " if who else ""
            self._push_log(f"[{locker}] {slot_bit}{who_bit}{detail}".strip(), "info", slot=slot if isinstance(slot, int) else None)

    def refresh_live(self) -> None:
        lockers = list(self.data["lockers"])
        if len(lockers) <= 1:
            for cab in lockers:
                self._refresh_one(cab)
            return
        workers = min(8, len(lockers))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(self._refresh_one, lockers))

    def _locker_number(self, cab: dict[str, Any] | None) -> int:
        cab_id = str((cab or {}).get("id") or "")
        for index, item in enumerate(self.data.get("lockers") or [], start=1):
            if str(item.get("id") or "") == cab_id:
                return index
        return 1

    def _rfid_subject(self, cab: dict[str, Any] | None, slot: int) -> str:
        """Alert/log subject: 'N번 보관함 계급 성명 총기번호' (no bay 'N번' alone)."""
        cab = cab or {}
        locker_bit = f"{self._locker_number(cab)}번 보관함"
        slot_key = str(int(slot or 0))
        local = (cab.get("users") or {}).get(slot_key) or {}
        gun = (cab.get("guns") or {}).get(slot_key) or {}
        rank = str(local.get("rank") or "").strip()
        name = str(local.get("name") or "").strip()
        if not name:
            live = self._live_of(str(cab.get("id") or ""))
            info = (_people_maps(live.get("status") or {}).get("users") or {}).get(slot_key) or {}
            live_rank, live_name = _split_label("user", int(slot or 0), info.get("name") or "")
            rank = rank or live_rank
            name = live_name or str(info.get("name") or "").strip()
        who = " ".join(part for part in (rank, name) if part).strip()
        serial = str(gun.get("serial") or "").strip()
        bits = [locker_bit]
        if who:
            bits.append(who)
        if serial:
            bits.append(serial)
        return " ".join(bits)

    def _rfid_tag_must_alarm(self, cab: dict[str, Any] | None, slot: int) -> bool:
        """보관·비상개폐 후 게이트 태깅은 무조건 비상. 정상 지문 불출 창만 예외."""
        if not cab or int(slot or 0) < 1:
            return False
        cab_id = str(cab.get("id") or "")
        slot_key = str(int(slot))
        ok_key = self._rfid_ok_key(cab_id, int(slot))
        # 정상 불출로 RFID 일치 창이 열린 칸만 통과.
        if time.monotonic() < float(self._rfid_ok_until.get(ok_key, 0.0) or 0.0):
            return False
        if self._is_emergency_slot(cab_id, int(slot)):
            return True
        recent = self._recent_actions.get((cab_id, slot_key))
        if recent and str(recent[0] or "") == "보관":
            return True
        live = self._live_of(cab_id)
        info = (_people_maps(live.get("status") or {}).get("users") or {}).get(slot_key)
        if isinstance(info, dict):
            status = str(info.get("status") or "").strip().lower()
            if status == "stored":
                return True
            # issued 이지만 RFID 불출 창 없음 → 아래 no_issue/mismatch로 처리
            if status == "issued":
                return False
            if info.get("present") is True:
                return True
        sens = str((live.get("board") or {}).get("sens") or "")
        if len(sens) >= int(slot) and sens[int(slot) - 1] == "1":
            return True
        return False

    def _gun_slot_stored(self, cab: dict[str, Any] | None, slot: int) -> bool:
        """True when the bay is stored / present — RFID gate tag must not happen."""
        return self._rfid_tag_must_alarm(cab, slot)

    def _situation_message(self, kind: str, cab: dict[str, Any] | None, slot: int | None = None) -> str:
        """Alert copy: line1 = situation, line2 = locker (and gun slot for emergency)."""
        locker = self._locker_index_label(cab or {})
        if kind == "emergency":
            detail = f"{locker} {int(slot)}번 총기" if slot else locker
            return f"비상개폐가 되었습니다.\n{detail}"
        if kind in ("offline", "network"):
            return f"네트워크연결이 단절되었습니다.\n{locker}"
        return f"비인가자가 접근했습니다.\n{locker}"

    def _locker_index_label(self, cab: dict[str, Any]) -> str:
        """등록된 보관함 표시 이름(예: 권총보관함). 'N번 보관함' 쓰지 않음."""
        name = str((cab or {}).get("name") or "").strip()
        if name:
            return name
        cab_id = str((cab or {}).get("id") or "")
        for item in self.data.get("lockers") or []:
            if str(item.get("id") or "") == cab_id:
                fallback = str(item.get("name") or "").strip()
                if fallback:
                    return fallback
        return "보관함"

    def _refresh_one(self, cab: dict[str, Any]) -> None:
        url = f"http://{cab['host']}:{cab['port']}/api/status"
        try:
            payload = json.loads(_pi_request(url, timeout=1.2).decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            self._keep_trying(cab)
            return
        board = payload.get("board") if isinstance(payload.get("board"), dict) else {}
        if len(str(board.get("sens") or "")) != 10:
            board = self._board_status(cab)
        if len(str(board.get("sens") or "")) != 10:
            # Keep last good bay map on transient serial/API glitches (common on pistol boards).
            prev_board = (self.lives.get(cab["id"]) or {}).get("board") or {}
            if len(str(prev_board.get("sens") or "")) == 10:
                board = prev_board
        cab_id = str(cab["id"])
        was_offline = cab_id in self._offline_alerted or not bool((self.lives.get(cab_id) or {}).get("online", True))
        self._misses[cab_id] = 0
        recovered = False
        if cab_id in self._offline_alerted:
            self._offline_alerted.discard(cab_id)
            recovered = True
            # 단절 알림은 사용자가 확인할 때까지 유지 (재연결 시 자동 삭제하지 않음)
        with self._lock:
            self._pulse += 1
        self.lives[cab_id] = {
            "online": True,
            "linking": False,
            "talking": True,
            "error": "",
            "status": payload,
            "board": board,
            "at": _now(),
        }
        if was_offline or recovered:
            self._push_log(f"[{self._locker_index_label(cab)}] 연결이 복구됐습니다.", "ok", cab)
        # Sync Pi authorized-open window so normal fingerprint issue is not emergency.
        # RFID gate match: only when this unlock is 불출(status=issued), never 보관.
        people = _people_maps(payload).get("users") or {}
        for raw in payload.get("authorized_open") or []:
            try:
                slot_no = int(raw)
            except (TypeError, ValueError):
                continue
            self._mark_authorized_open(cab_id, slot_no)
            info = people.get(str(slot_no)) if isinstance(people, dict) else None
            if isinstance(info, dict) and str(info.get("status") or "").strip().lower() == "issued":
                self._mark_rfid_ok_issue(cab_id, slot_no)
        # Detect emergency on the poll thread before after-work coalescing can drop a transition.
        if isinstance(board, dict):
            self._detect_emergency_sens(cab, board)
        # Keep poll loop snappy: name/time sync must not delay the next sensor read.
        self._schedule_after(dict(cab), payload, board if isinstance(board, dict) else {})

    def _schedule_after(self, cab: dict[str, Any], payload: dict[str, Any], board: dict[str, Any]) -> None:
        cab_id = str(cab.get("id") or "")
        with self._after_lock:
            self._after_latest[cab_id] = (cab, payload, board)
            if self._after_running.get(cab_id):
                return
            self._after_running[cab_id] = True
        threading.Thread(target=self._drain_after, args=(cab_id,), daemon=True).start()

    def _drain_after(self, cab_id: str) -> None:
        while True:
            with self._after_lock:
                item = self._after_latest.pop(cab_id, None)
                if item is None:
                    self._after_running[cab_id] = False
                    return
            try:
                self._after_refresh(*item)
            except Exception:
                continue

    def _after_refresh(self, cab: dict[str, Any], payload: dict[str, Any], board: dict[str, Any] | None = None) -> None:
        try:
            self._sync_kind_and_reset(cab, payload)
            self._ingest_pi_alerts(cab, payload)
            self._ingest_pi_access(cab, payload)
            self._record_changes(cab, payload, board or {})
            self._sync_names(cab, payload)
            self._sync_purpose(cab, payload)
            self._push_time(cab)
        except Exception:
            return

    def _post_json(self, cab: dict[str, Any], path: str, body: dict[str, Any], timeout: float = 1.5) -> None:
        url = f"http://{cab['host']}:{cab['port']}{path}"
        raw = json.dumps(body).encode("utf-8")
        try:
            _pi_request(url, data=raw, timeout=timeout)
        except (OSError, urllib.error.URLError, TimeoutError):
            return

    def _ingest_pi_access(self, cab: dict[str, Any], payload: dict[str, Any]) -> None:
        events = payload.get("access") if isinstance(payload.get("access"), list) else []
        rows: list[dict[str, Any]] = []
        ack_ids: list[int] = []
        begin_ops = False
        finish_ops = False
        for item in events:
            if not isinstance(item, dict):
                continue
            raw_id = item.get("id")
            if raw_id is None:
                continue
            try:
                event_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            key = f"{cab.get('id')}:access:{event_id}"
            if key in self._seen_pi_access:
                ack_ids.append(event_id)
                continue
            self._seen_pi_access.add(key)
            if len(self._seen_pi_access) > 500:
                self._seen_pi_access = set(list(self._seen_pi_access)[-300:])
            ack_ids.append(event_id)
            rows.append(self._access_history_row(cab, item))
            action = str(item.get("action") or "").strip()
            # 관리자 지문 인지(보관·불출 로그인) → 해당 보관함으로 카메라
            if action in ("로그인", "보관불출", "보관·불출", "불출", "보관"):
                begin_ops = True
            # 대기화면 복귀 / 세션 종료 → Home
            elif action in ("대기", "로그아웃", "세션종료", "종료", "완료", "홈"):
                finish_ops = True
        self._append_history(rows)
        if ack_ids:
            self._post_json(cab, "/api/access/ack", {"ids": ack_ids})
        if finish_ops:
            self._finish_ops_view()
        elif begin_ops:
            self._begin_ops_view(cab)

    def _ingest_pi_alerts(self, cab: dict[str, Any], payload: dict[str, Any]) -> None:
        alerts = payload.get("alerts") if isinstance(payload.get("alerts"), list) else []
        rows: list[dict[str, Any]] = []
        ack_ids: list[int] = []
        for item in alerts:
            if not isinstance(item, dict):
                continue
            raw_id = item.get("id")
            if raw_id is None:
                continue
            try:
                event_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            key = f"{cab.get('id')}:{event_id}"
            if key in self._seen_pi_alerts:
                ack_ids.append(event_id)
                continue
            kind = str(item.get("kind") or "unauthorized")
            if kind == "unreturned":
                ack_ids.append(event_id)
                self._seen_pi_alerts.add(key)
                continue
            if kind == "network":
                kind = "offline"
            if kind not in ("unauthorized", "emergency", "offline"):
                kind = "unauthorized"
            self._seen_pi_alerts.add(key)
            if len(self._seen_pi_alerts) > 500:
                self._seen_pi_alerts = set(list(self._seen_pi_alerts)[-300:])
            slot_raw = item.get("slot")
            try:
                slot = int(slot_raw) if slot_raw is not None else None
            except (TypeError, ValueError):
                slot = None
            message = self._situation_message(kind, cab, slot if kind == "emergency" else None)
            when = str(item.get("time") or "").strip() or _now()
            self._queue_alert(kind, message, cab, slot)
            rows.append(self._security_history_row(cab, kind, message, when, slot))
            ack_ids.append(event_id)
            if kind in ("emergency", "unauthorized"):
                try:
                    from cctv import trigger_event_recording

                    trigger_event_recording(
                        reason=kind,
                        message=message,
                        locker=str(cab.get("name") or ""),
                        slot=slot,
                    )
                except Exception:
                    pass
        self._append_history(rows)
        if ack_ids:
            self._post_json(cab, "/api/alerts/ack", {"ids": ack_ids})

    def _clear_kind_alerts(self, kind: str, cab: dict[str, Any] | None = None) -> None:
        locker_id = str((cab or {}).get("id") or "")
        with self._lock:
            self._alerts = [
                item
                for item in self._alerts
                if not (
                    str(item.get("kind") or "") == kind
                    and (not locker_id or str(item.get("locker_id") or "") == locker_id)
                )
            ]

    def _keep_trying(self, cab: dict[str, Any]) -> None:
        cab_id = str(cab["id"])
        misses = int(self._misses.get(cab_id, 0)) + 1
        self._misses[cab_id] = misses
        previous = dict(self.lives.get(cab_id) or {})
        was_online = bool(previous.get("online"))
        # 랜선 분리 즉시 오프라인 + 알림 (첫 실패부터)
        if misses >= 1:
            previous["online"] = False
            previous["linking"] = True
            previous["talking"] = False
            if cab_id not in self._offline_alerted:
                self._offline_alerted.add(cab_id)
                warn = self._situation_message("offline", cab)
                self._queue_alert("offline", warn, cab)
                self._append_history([
                    self._security_history_row(
                        cab,
                        "offline",
                        warn,
                        _now(),
                    )
                ])
        else:
            if previous.get("status"):
                previous["online"] = True
            previous["linking"] = True
            previous["talking"] = False
        previous["error"] = ""
        previous["at"] = _now()
        self.lives[cab_id] = previous

    def _board_status(self, locker: dict[str, Any]) -> dict[str, Any]:
        url = f"http://{locker['host']}:{locker['port']}/api/locker/status"
        try:
            return json.loads(_pi_request(url, timeout=0.8).decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            return {}

    def _sync_kind_and_reset(self, cab: dict[str, Any], payload: dict[str, Any]) -> None:
        wrapped = payload.get("masters") if isinstance(payload.get("masters"), dict) else {}
        kind = str(payload.get("locker_kind") or wrapped.get("locker_kind") or cab.get("kind") or "rifle").strip()
        if kind not in ("rifle", "pistol"):
            kind = "rifle"
        try:
            reset_seq = int(payload.get("reset_seq") if payload.get("reset_seq") is not None else wrapped.get("reset_seq") or 0)
        except (TypeError, ValueError):
            reset_seq = 0
        changed = False
        with self._lock:
            target = next((item for item in self.data["lockers"] if item["id"] == cab["id"]), None)
            if not target:
                return
            if target.get("kind") != kind:
                target["kind"] = kind
                cab["kind"] = kind
                changed = True
            last = int(target.get("last_reset_seq") or 0)
            if reset_seq > last:
                target["last_reset_seq"] = reset_seq
                cab["last_reset_seq"] = reset_seq
                target["users"] = _blank_map(USER_SLOTS)
                target["guns"] = _blank_map(USER_SLOTS)
                target["catalog"] = []
                target["memo"] = ""
                cab["users"] = target["users"]
                cab["guns"] = target["guns"]
                cab["catalog"] = []
                cab["memo"] = ""
                self.data["masters"] = _blank_map(MASTER_SLOTS)
                self.data["subs"] = _blank_map(SUB_SLOTS)
                self.data["operator"] = None
                locker_id = str(cab["id"])
                self.data["history"] = [
                    row for row in (self.data.get("history") or [])
                    if str(row.get("locker_id")) != locker_id
                ]
                self.data["purpose_label"] = "젤루텍"
                self.data["unit_name"] = "지문인식"
                self.sessions.clear()
                self._last_users[cab["id"]] = None
                changed = True
            if changed:
                self.save()

    def _mark_authorized_open(self, cab_id: str, slot: int, sec: float = 45.0) -> None:
        # Door unlock only (issue OR store). RFID gate match is armed later on
        # confirmed normal 불출 — never on store unlock.
        key = f"{cab_id}:{int(slot)}"
        self._authorized_open_until[key] = time.monotonic() + float(sec)

    def _clear_authorized_open(self, cab_id: str, slot: int) -> None:
        self._authorized_open_until.pop(f"{cab_id}:{int(slot)}", None)

    def _is_authorized_open(self, cab_id: str, slot: int) -> bool:
        key = f"{cab_id}:{int(slot)}"
        return time.monotonic() < float(self._authorized_open_until.get(key, 0.0) or 0.0)

    def _mark_emergency_slot(self, cab_id: str, slot: int, sec: float = 20.0) -> None:
        self._emergency_until[f"{cab_id}:{int(slot)}"] = time.monotonic() + float(sec)

    def _is_emergency_slot(self, cab_id: str, slot: int) -> bool:
        key = f"{cab_id}:{int(slot)}"
        return time.monotonic() < float(self._emergency_until.get(key, 0.0) or 0.0)

    def _force_emergency_purpose(self, info: dict[str, Any]) -> dict[str, Any]:
        """비상개폐 불출은 목적 '기타'로만 기록."""
        row = dict(info or {})
        row["purpose"] = "기타"
        return row

    def _detect_emergency_sens(self, cab: dict[str, Any], board: dict[str, Any]) -> None:
        """SENS 1→0 without authorized unlock → emergency. Door bit is ignored."""
        sens = str((board or {}).get("sens") or "")
        door = str((board or {}).get("door") or "")
        sens_ok = len(sens) == USER_SLOTS
        door_ok = len(door) == USER_SLOTS
        cab_id = str(cab.get("id") or "")
        if door_ok:
            self._last_door[cab_id] = door
        prev_sens = self._last_sens.get(cab_id, "")
        if sens_ok:
            self._last_sens[cab_id] = sens
        if not (sens_ok and len(prev_sens) == USER_SLOTS):
            return
        for index in range(USER_SLOTS):
            if not (prev_sens[index] == "1" and sens[index] == "0"):
                continue
            slot_no = index + 1
            if self._is_authorized_open(cab_id, slot_no):
                continue
            self._mark_emergency_slot(cab_id, slot_no)
            # Emergency take-out must never look like a fingerprint issue for gate RFID.
            self._clear_authorized_open(cab_id, slot_no)
            self._clear_rfid_ok_issue(cab_id, slot_no)
            warn = self._situation_message("emergency", cab, slot_no)
            self._queue_alert("emergency", warn, cab, slot_no)
            # 비상개폐 불출은 예외로 목적 '기타'
            users = ((self.lives.get(cab_id) or {}).get("status") or {})
            people = _people_maps(users if isinstance(users, dict) else {}).get("users") or {}
            live = people.get(str(slot_no)) if isinstance(people, dict) else None
            info = self._force_emergency_purpose(live if isinstance(live, dict) else {"purpose": "기타"})
            self._append_history([
                self._security_history_row(cab, "emergency", warn, _now(), slot_no),
                self._history_row(cab, str(slot_no), info, "불출", _now(), extra="비상개폐"),
            ])
            self._recent_actions[(cab_id, str(slot_no))] = ("불출", time.monotonic())
            try:
                from cctv import trigger_event_recording  # local module

                trigger_event_recording(
                    reason="emergency",
                    message=warn,
                    locker=str(cab.get("name") or ""),
                    slot=slot_no,
                )
            except Exception:
                pass

    def _situation_pending(self) -> bool:
        with self._lock:
            return any(
                str(item.get("kind") or "") in ("emergency", "unauthorized", "offline", "rfid_emergency")
                for item in self._alerts
            )

    def _person_active(self) -> bool:
        with self._lock:
            if self._person_alert_open:
                return True
            return time.monotonic() < float(self._person_until or 0.0)

    def person_ai_blocked(self) -> bool:
        """True while ops / inside-armory / person alert open — no new person detects."""
        with self._lock:
            if self._ops_aimed:
                return True
            if bool(self.data.get("armory_inside")):
                return True
            # Keep flag in sync with the real queue (orphan open flag would mute forever).
            pending_person = any(str(item.get("kind") or "") == "person" for item in self._alerts)
            self._person_alert_open = pending_person
            if pending_person:
                return True
            return False

    def set_armory_inside(
        self,
        active: bool,
        *,
        sync_patlite_enable: bool = True,
    ) -> dict[str, Any]:
        """Door-sensor stand-in: while checked, person-entry alerts stay silent.

        Also mirrors 황색 경광등 알림끄기/켜기 so OMS popup + tower stay in sync.
        """
        wanted = bool(active)
        with self._lock:
            prev = bool(self.data.get("armory_inside"))
            self.data["armory_inside"] = wanted
        if prev != wanted:
            self.save()
            self._push_log(
                "무기고 출입중 체크됨 — 사람 출입 알림 일시 중지" if wanted else "무기고 출입중 해제 — 사람 출입 알림 재개",
                "info",
            )
        if wanted:
            try:
                from person_track import stop_person_track

                stop_person_track(home=False)
            except Exception:
                pass
            with self._lock:
                self._person_until = 0.0
                self._person_alert_open = False
                # Drop pending person popups while inside.
                self._alerts = [item for item in self._alerts if str(item.get("kind") or "") != "person"]
            if sync_patlite_enable:
                try:
                    from patlite import set_color_enabled

                    set_color_enabled("yellow", False)
                except Exception:
                    pass
            self._sync_patlite()
            # 출입중 체크만으로 person 알림이 지워지면 ack 경로를 타지 않음 → 여기서 Home 판정.
            self._home_if_no_alarm()
        else:
            if sync_patlite_enable:
                try:
                    from patlite import set_color_enabled

                    set_color_enabled("yellow", True)
                except Exception:
                    pass
            with self._lock:
                # 출입중 해제 = 감시 재개. need_clear(화면 비움 대기)는
                # 알림 확인(ack) 전용 — 여기선 쓰면 사람이 화면에 남아 있을 때
                # 최대 90초간 재감지가 막혀 "잘 안 되는" 느낌이 난다.
                self._person_need_clear = False
                self._person_need_clear_until = 0.0
                self._person_alert_open = False
                self._person_until = 0.0
            self._sync_patlite()
        return {
            "ok": True,
            "armory_inside": wanted,
            "yellow_enable": (not wanted) if sync_patlite_enable else None,
        }

    def person_ai_needs_clear(self) -> bool:
        with self._lock:
            if not self._person_need_clear:
                return False
            # Safety: do not mute person alerts forever if the frame never clears.
            if time.monotonic() > float(self._person_need_clear_until or 0.0):
                self._person_need_clear = False
                self._person_need_clear_until = 0.0
                return False
            return True

    def person_ai_mark_clear(self) -> None:
        """AI saw empty frame after ack — allow next entry detect."""
        with self._lock:
            self._person_need_clear = False
            self._person_need_clear_until = 0.0

    def _arm_person_need_clear(self) -> None:
        with self._lock:
            self._person_need_clear = True
            self._person_need_clear_until = time.monotonic() + float(self._PERSON_NEED_CLEAR_SEC)

    def _sync_patlite(self) -> None:
        """Match Patlite LR6-USB to current OMS alarm / fingerprint-ops / person state."""
        try:
            from patlite import notify_sync

            with self._lock:
                ops = bool(self._ops_aimed)
            notify_sync(
                alarm_pending=self._situation_pending(),
                ops_active=ops,
                yellow_active=self._person_active(),
            )
        except Exception:
            pass

    def notify_person_enter(
        self,
        message: str = "",
        source: str = "cctv",
        channel: int = 0,
    ) -> dict[str, Any]:
        """표 4: CCTV 사람 인지 — 황색 OMS 알림 + 황색 경광등 (+ PTZ 추적). 적색/사이렌 없음."""
        text = str(message or "").strip() or "무기고에 사람이 출입했습니다."
        with self._lock:
            if self._ops_aimed:
                return {"ok": True, "deduped": True, "reason": "ops", "message": text}
            if bool(self.data.get("armory_inside")):
                return {"ok": True, "deduped": True, "reason": "armory-inside", "message": text}
            pending_person = any(str(item.get("kind") or "") == "person" for item in self._alerts)
            self._person_alert_open = pending_person
            if pending_person:
                return {"ok": True, "deduped": True, "reason": "queued", "message": text}
            if self._person_need_clear and time.monotonic() <= float(self._person_need_clear_until or 0.0):
                return {"ok": True, "deduped": True, "reason": "need-clear", "message": text}
            if time.monotonic() < float(self._person_ack_mute_until or 0.0):
                return {"ok": True, "deduped": True, "reason": "ack-mute", "message": text}
            self._person_need_clear = False
            self._person_need_clear_until = 0.0
            self._person_ack_mute_until = 0.0
            self._person_alert_open = True
            self._person_until = time.monotonic() + 45.0
        alert = self._queue_alert("person", text)
        self._push_log(text, "warn")
        try:
            from cctv import load_config, open_situation

            cfg = load_config()
            if cfg.get("auto_light_on_person", True):
                try:
                    from patlite import notify_person

                    notify_person()
                except Exception:
                    pass
            if cfg.get("auto_ptz_on_person", True) and cfg.get("auto_ptz_on_event", True):
                if open_situation(time.time()):
                    try:
                        from person_track import start_person_track

                        start_person_track(max(0, int(channel)))
                    except Exception:
                        pass
        except Exception:
            pass
        _ = source
        return {"ok": True, "message": text, "kind": "person", "alert": alert}

    def _person_alert_pending(self) -> bool:
        with self._lock:
            return any(str(item.get("kind") or "") == "person" for item in self._alerts)

    def _camera_busy(self) -> str:
        """Why the camera must stay off Home, or empty when idle."""
        with self._lock:
            if self._ops_aimed:
                return "ops"
        if self._situation_pending():
            return "situation"
        if self._person_alert_pending():
            return "person"
        try:
            from person_track import tracking_active

            if tracking_active():
                return "track"
        except Exception:
            pass
        return ""

    def _home_if_no_alarm(self) -> None:
        """Return camera Home only when no situation / person / ops still needs PTZ."""
        busy = self._camera_busy()
        if busy:
            return

        def run() -> None:
            # Re-check after scheduling — overlapping acks may have opened a new aim.
            busy_now = self._camera_busy()
            if busy_now:
                return
            try:
                import cctv as cctv_mod

                # Stop any leftover continuous move before GotoHome / preset.
                try:
                    cctv_mod.ptz_stop()
                except Exception:
                    pass
                # Mark shared home wall for other OMS windows, then move once.
                cctv_mod._ensure_ptz_bus()
                wall = time.time()
                with cctv_mod._ptz_guard:
                    cctv_mod._home_wall = max(float(cctv_mod._home_wall or 0.0), wall)
                cctv_mod._broadcast_ptz({"op": "home", "wall": wall})
                result = cctv_mod.ptz_home()
                if result.get("ok"):
                    self._push_log(str(result.get("detail") or "카메라 Home 복귀"), "ok")
                else:
                    self._push_log(
                        str(result.get("detail") or "Home 이동 실패. CCTV에서 Home저장을 하세요."),
                        "warn",
                    )
            except Exception as exc:  # noqa: BLE001
                self._push_log(f"Home 복귀 오류: {exc}", "warn")

        threading.Thread(target=run, name="oms-ptz-home", daemon=True).start()

    def _begin_ops_view(self, cab: dict[str, Any] | None = None) -> None:
        """Aim at the locker when Pi recognizes an admin fingerprint for issue/store."""
        if self._situation_pending():
            return
        with self._lock:
            self._ops_gen += 1
            gen = self._ops_gen
            self._ops_aimed = True
            # 수불 중에는 사람 AI 재감지 안 함. 황색 출입 홀드는 종료.
            self._person_until = 0.0
            self._person_need_clear = True
            self._person_need_clear_until = time.monotonic() + float(self._PERSON_NEED_CLEAR_SEC)

        locker_id = str((cab or {}).get("id") or "")
        locker_no = self._locker_number(cab) if cab else 0
        try:
            from patlite import notify_ops_begin

            notify_ops_begin()
        except Exception:
            pass

        def aim() -> None:
            try:
                from cctv import open_situation, ptz_goto_event

                if open_situation(time.time()):
                    result = ptz_goto_event("ops", locker_id=locker_id, locker_no=locker_no)
                    if not result.get("ok"):
                        self._push_log(str(result.get("detail") or "보관·불출 PTZ 이동 실패"), "warn")
            except Exception:
                pass

        def fallback() -> None:
            # Safety only: normal Home is when Pi returns to standby ("대기").
            time.sleep(180)
            with self._lock:
                if self._ops_gen != gen or not self._ops_aimed:
                    return
                self._ops_aimed = False
                self._ops_gen += 1
            try:
                from patlite import notify_ops_end

                notify_ops_end()
            except Exception:
                pass
            self._sync_patlite()
            self._home_if_no_alarm()

        threading.Thread(target=aim, name="oms-ops-aim", daemon=True).start()
        threading.Thread(target=fallback, name="oms-ops-home", daemon=True).start()

    def _finish_ops_view(self) -> None:
        """Return Home when Pi ops session ends and standby is shown."""
        with self._lock:
            self._ops_aimed = False
            self._ops_gen += 1
            # 수불 종료 후에도 같은 사람이 화면에 있으면 바로 재감지하지 않음.
            self._person_need_clear = True
            self._person_need_clear_until = time.monotonic() + float(self._PERSON_NEED_CLEAR_SEC)
        try:
            from patlite import notify_ops_end

            notify_ops_end()
        except Exception:
            pass
        self._sync_patlite()
        # Standby always means camera should go Home (even if aim flag was lost).
        self._home_if_no_alarm()

    def _record_changes(self, cab: dict[str, Any], payload: dict[str, Any], board: dict[str, Any] | None = None) -> None:
        users = _people_maps(payload).get("users") or {}
        sens = str((board or {}).get("sens") or "")
        sens_ok = len(sens) == USER_SLOTS
        cab_id = str(cab.get("id") or "")
        # Emergency already handled in _detect_emergency_sens on the poll thread.
        current: dict[str, dict[str, Any]] = {}
        for slot in range(1, USER_SLOTS + 1):
            info = users.get(str(slot))
            if isinstance(info, dict) and info.get("user_id"):
                present = sens[slot - 1] == "1" if sens_ok else None
                current[str(slot)] = {
                    "status": info.get("status") or "stored",
                    "purpose": info.get("purpose") or "",
                    "present": present,
                    "name": str(info.get("name") or "").strip(),
                    "updated": _event_stamp(info.get("updated_at") or info.get("enrolled_at")),
                    "approver_role": str(info.get("approver_role") or "").strip(),
                    "approver_slot": info.get("approver_slot"),
                    "approver_name": str(info.get("approver_name") or "").strip(),
                }
        previous = self._last_users.get(cab["id"])
        self._last_users[cab["id"]] = current
        rows: list[dict[str, Any]] = []
        now_mono = time.monotonic()
        if previous is None:
            seen = {
                (str(row.get("locker_id")), str(row.get("slot")), str(row.get("kind") or "gun"))
                for row in self.data.get("history") or []
                if str(row.get("kind") or "gun") == "gun"
            }
            for slot, info in current.items():
                if (str(cab["id"]), slot, "gun") in seen:
                    continue
                action = "불출" if info.get("status") == "issued" else "보관"
                if action == "불출" and self._is_emergency_slot(cab_id, int(slot)):
                    info = self._force_emergency_purpose(info)
                rows.append(self._history_row(cab, slot, info, action, info.get("updated") or _now()))
        else:
            for slot, info in current.items():
                before = previous.get(slot)
                emergency = False
                if not before:
                    action = "불출" if info.get("status") == "issued" else "보관"
                else:
                    present_changed = (
                        info.get("present") is not None
                        and before.get("present") is not None
                        and before.get("present") != info.get("present")
                    )
                    status_changed = before.get("status") != info.get("status")
                    touched = bool(info.get("updated")) and info.get("updated") != before.get("updated")
                    if present_changed:
                        # 비인가 센서 이탈 → 불출/기타 (사격·훈련 등 정상 목적 금지)
                        if bool(before.get("present")) and not bool(info.get("present")):
                            if not self._is_authorized_open(cab_id, int(slot)):
                                self._mark_emergency_slot(cab_id, int(slot))
                                emergency = True
                                info = self._force_emergency_purpose(info)
                                action = "불출"
                            else:
                                action = "불출"
                        else:
                            action = "보관" if info.get("present") else "불출"
                    elif status_changed or touched:
                        action = "불출" if info.get("status") == "issued" else "보관"
                    else:
                        continue
                if action == "불출" and (emergency or self._is_emergency_slot(cab_id, int(slot))):
                    info = self._force_emergency_purpose(info)
                    emergency = True
                recent = self._recent_actions.get((cab["id"], slot))
                # 비상개폐에서 이미 불출/기타를 남겼으면 사격 등 중복 불출 로그 금지
                if recent and recent[0] == action and now_mono - recent[1] < 8.0:
                    continue
                self._recent_actions[(cab["id"], slot)] = (action, now_mono)
                if action == "보관":
                    # 보관 완료 후에는 불출 인가 창·RFID 정상 일치가 더 이상 유효하지 않음.
                    self._clear_authorized_open(cab_id, int(slot))
                    self._clear_rfid_ok_issue(cab_id, int(slot))
                elif action == "불출" and emergency:
                    self._clear_authorized_open(cab_id, int(slot))
                    self._clear_rfid_ok_issue(cab_id, int(slot))
                elif action == "불출" and not emergency:
                    # Fingerprint / authorized issue — gate RFID may match this bay.
                    self._mark_rfid_ok_issue(cab_id, int(slot))
                extra = "비상개폐" if emergency and action == "불출" else ""
                rows.append(self._history_row(cab, slot, info, action, _now(), extra=extra))
        self._append_history(rows)
        # PTZ aim/home is driven by Pi admin access (로그인/대기), not slot sensor edges.

    def _history_row(
        self,
        cab: dict[str, Any],
        slot: str,
        info: dict[str, Any],
        action: str,
        when: str,
        extra: str = "",
    ) -> dict[str, Any]:
        local = (cab.get("users") or {}).get(slot) or {}
        gun = (cab.get("guns") or {}).get(slot) or {}
        rank = str(local.get("rank") or "").strip()
        name = str(local.get("name") or "").strip()
        if not name:
            rank_live, name_live = _split_label("user", int(slot), info.get("name") or "")
            rank = rank or rank_live
            name = name_live or str(info.get("name") or "").strip()
        purpose = str(info.get("purpose") or "").strip()
        kind = "권총" if cab.get("kind") == "pistol" else "소총"
        # 총기수불 = 보관함 센서/지문 수불. RFID 무기고 태깅과 구분.
        if action == "불출":
            bits = ["보관함 불출"]
            if purpose:
                bits.append(purpose)
        elif action == "보관":
            bits = ["보관함 보관"]
        else:
            bits = [action]
        if extra:
            bits.append(extra)
        approver = self._approver_label(info)
        if approver:
            bits.append(approver)
        return {
            "time": when,
            "locker_id": cab["id"],
            "locker": cab.get("name") or "",
            "slot": int(slot),
            "rank": rank,
            "sn": local.get("sn") or "",
            "name": name,
            "category": kind,
            "model": gun.get("model") or "",
            "serial": gun.get("serial") or "",
            "action": action,
            "purpose": purpose if action == "불출" else "",
            "approver": approver,
            "detail": " / ".join(bits),
            "kind": "gun",
            "place": "locker",
        }

    def _rfid_history_row(
        self,
        cab: dict[str, Any] | None,
        slot: int,
        *,
        gun_label: str,
        uid: str,
        status: str,
        detail: str,
    ) -> dict[str, Any]:
        """Armory-gate RFID tagging — 전체이력 only (kind=rfid), not 총기수불."""
        cab = cab or {}
        locker_id = str(cab.get("id") or "")
        slot_key = str(int(slot or 0))
        local = (cab.get("users") or {}).get(slot_key) or {}
        gun = (cab.get("guns") or {}).get(slot_key) or {}
        short_uid = uid if len(uid) <= 16 else f"{uid[:8]}…{uid[-4:]}"
        return {
            "time": _now(),
            "locker_id": locker_id,
            "locker": cab.get("name") or "",
            "slot": int(slot or 0),
            "rank": str(local.get("rank") or "").strip(),
            "sn": str(local.get("sn") or "").strip(),
            "name": str(local.get("name") or "").strip(),
            "category": "RFID",
            "model": str(gun.get("model") or "RFID"),
            "serial": str(gun.get("serial") or ""),
            "action": "무기고불출",
            "purpose": "",
            "approver": "",
            "detail": detail or f"무기고 불출 / RFID 태깅 · {slot}번 · {gun_label} · {short_uid}",
            "kind": "rfid",
            "place": "armory",
            "rfid_uid": uid,
            "rfid_status": status,
        }

    def _approver_label(self, info: dict[str, Any]) -> str:
        role = str(info.get("approver_role") or "").strip().lower()
        if role not in ("master", "sub"):
            return ""
        try:
            slot = int(info.get("approver_slot") or 0)
        except (TypeError, ValueError):
            slot = 0
        live_name = str(info.get("approver_name") or "").strip()
        rank, name, sn = self._admin_profile(role, slot, live_name)
        role_label = "마스터" if role == "master" else "서브"
        who = " ".join(part for part in (rank, name) if part).strip() or live_name or f"{role_label}{slot or ''}"
        if sn:
            return f"{role_label} {who} ({sn})"
        return f"{role_label} {who}".strip()

    def _admin_profile(self, role: str, slot: int, live_name: str = "") -> tuple[str, str, str]:
        bucket = "masters" if role == "master" else "subs"
        local = (self.data.get(bucket) or {}).get(str(slot)) if slot else None
        local = local if isinstance(local, dict) else {}
        rank = str(local.get("rank") or "").strip()
        name = str(local.get("name") or "").strip()
        sn = str(local.get("sn") or "").strip()
        if not name or _is_placeholder(role, slot or 0, name):
            live_rank, live_name_part = _split_label(role, slot or 0, live_name)
            rank = rank or live_rank
            name = live_name_part or ""
        if not name:
            name = f"{'마스터' if role == 'master' else '서브'}{slot}" if slot else ""
        return rank, name, sn

    def _access_history_row(self, cab: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        role = str(item.get("role") or "").strip().lower()
        if role not in ("master", "sub"):
            role = "master"
        try:
            slot = int(item.get("slot") or 0)
        except (TypeError, ValueError):
            slot = 0
        live_name = str(item.get("name") or "").strip()
        rank, name, sn = self._admin_profile(role, slot, live_name)
        role_label = "마스터" if role == "master" else "서브"
        action = str(item.get("action") or "").strip() or "로그인"
        when = str(item.get("time") or "").strip() or _now()
        return {
            "time": when,
            "locker_id": cab["id"],
            "locker": cab.get("name") or "",
            "slot": slot,
            "rank": rank,
            "sn": sn,
            "name": name,
            "category": role_label,
            "model": role_label,
            "serial": "",
            "action": action,
            "purpose": "",
            "detail": action,
            "kind": "access",
        }

    def _security_history_row(
        self,
        cab: dict[str, Any],
        kind: str,
        message: str,
        when: str,
        slot: int | None = None,
    ) -> dict[str, Any]:
        labels = {
            "emergency": "비상개폐",
            "unauthorized": "비인가 접근",
            "offline": "네트워크 단절",
            "unreturned": "미반납",
        }
        label = labels.get(kind, "보안")
        flat = " ".join(str(message or "").split())
        detail = f"{label} / {flat}"
        if slot:
            detail = f"{label} (슬롯 {slot}) / {flat}"
        return {
            "time": when,
            "locker_id": cab["id"],
            "locker": cab.get("name") or "",
            "slot": int(slot or 0),
            "rank": "",
            "sn": "",
            "name": "",
            "category": "보안",
            "model": "",
            "serial": "",
            "action": label,
            "purpose": "",
            "detail": detail,
            "kind": "security",
        }

    def _sync_names(self, cab: dict[str, Any], payload: dict[str, Any]) -> None:
        maps = _people_maps(payload)
        pairs = (
            ("master", self.data["masters"], "masters", MASTER_SLOTS),
            ("sub", self.data["subs"], "subs", SUB_SLOTS),
            ("user", cab["users"], "users", USER_SLOTS),
        )
        changed = False
        pushes: list[tuple[str, int, dict[str, Any]]] = []
        for kind, local_map, key, count in pairs:
            live_map = maps.get(key) or {}
            for slot in range(1, count + 1):
                live = live_map.get(str(slot))
                if not isinstance(live, dict) or not live.get("user_id"):
                    continue
                local = local_map.get(str(slot))
                merged, push = _merge_profile(kind, slot, local if isinstance(local, dict) else None, live.get("name") or "")
                if merged and merged != local:
                    local_map[str(slot)] = merged
                    changed = True
                if push and merged:
                    pushes.append((kind, slot, merged))
        if changed:
            with self._lock:
                self.save()
        for kind, slot, record in pushes[:8]:
            self._push_name(cab, kind, slot, str(record.get("name") or ""), str(record.get("rank") or ""))

    def _sync_purpose(self, cab: dict[str, Any], payload: dict[str, Any]) -> None:
        wrapped = payload.get("masters") if isinstance(payload.get("masters"), dict) else {}
        live = str(payload.get("purpose_label") or (wrapped or {}).get("purpose_label") or "").strip()
        local = str(self.data.get("purpose_label") or "젤루텍").strip()
        live_score = 0 if not live or live == "젤루텍" else 2 + len(live)
        local_score = 0 if not local or local == "젤루텍" else 2 + len(local)
        if live_score > local_score:
            with self._lock:
                self.data["purpose_label"] = live[:10]
                self.save()
            return
        if local_score > live_score and local != live:
            self._push_purpose_label([cab], local)

    def _push_time(self, cab: dict[str, Any]) -> None:
        # Heartbeat for Pi OMS-link detection (also sent on every /api/status via header).
        now = time.time()
        if now - self._time_sent.get(cab["id"], 0.0) < 3.0:
            return
        self._time_sent[cab["id"]] = now
        url = f"http://{cab['host']}:{cab['port']}/api/time"
        body = json.dumps({
            "epoch": time.time(),
            "locker_no": self._locker_number(cab),
            "locker_name": self._locker_index_label(cab),
        }).encode("utf-8")
        try:
            _pi_request(url, data=body, timeout=2.0)
        except (OSError, urllib.error.URLError, TimeoutError):
            return

    def _push_name(self, cab: dict[str, Any], kind: str, slot: int, name: str, rank: str) -> None:
        label = f"{rank} {name}".strip()
        if not label or kind == "operator":
            return
        url = f"http://{cab['host']}:{cab['port']}/api/rename"
        body = json.dumps({"kind": kind, "slot": slot, "name": label}).encode("utf-8")
        try:
            _pi_request(url, data=body, timeout=2.0)
        except (OSError, urllib.error.URLError, TimeoutError):
            return

    def _enrolled(self, kind: str, slot: int) -> bool:
        cab = self._selected()
        if cab is None:
            return False
        status = self._live_of(cab["id"]).get("status") or {}
        if kind == "operator":
            return False
        info = (_people_maps(status).get(_bucket(kind)) or {}).get(str(slot))
        return isinstance(info, dict) and bool(info.get("user_id"))

    def _public_record(self, kind: str, slot: int) -> dict[str, Any] | None:
        if kind == "operator":
            return self.data.get("operator")
        if kind == "user":
            cab = self._selected()
            if cab is None:
                return None
            return cab["users"].get(str(slot))
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
        # 사용자(칸): 지문만 있어도 OMS에 사람/총기 정보를 추가로 넣을 수 있음.
        # 마스터/서브/운영자만 지문(또는 로그인) 완료 후 수정 잠금.
        if kind != "user" and self._enrolled(kind, slot) and _profile_complete(kind, self._public_record(kind, slot)):
            return "지문이 등록된 정보는 수정할 수 없습니다. 보관함에서 지문을 삭제한 뒤 수정하세요."
        return ""

    def _sens(self, board: dict[str, Any] | None) -> str:
        sens = str((board or {}).get("sens") or "")
        if len(sens) != USER_SLOTS:
            return "0" * USER_SLOTS
        return sens

    def _bays(self, status: dict[str, Any], board: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        users = _people_maps(status).get("users") or {}
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
            live = (_people_maps(status).get("users") or {}).get(str(slot))
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
            "unpaid": empty,
            "purposes": [{"name": name, "count": purposes[name]} for name in PURPOSES],
        }

    def snapshot(self, actor: dict[str, Any] | None) -> dict[str, Any]:
        privileged = bool(actor)
        operator = bool(actor and actor.get("role") == "operator")
        master = bool(actor and actor.get("role") == "master")
        cab = self._selected()
        live = self._live_of(cab["id"]) if cab else {"online": False, "error": "", "status": {}, "board": {}}
        status = live.get("status") or {}
        board = live.get("board") or {}
        door = str(board.get("door") or "")
        sens = str(board.get("sens") or "")
        counts = self._counts(status, board) if cab else {
            "registered": 0, "issued": 0, "stored": 0, "empty": 0, "unpaid": 0,
            "purposes": [{"name": name, "count": 0} for name in PURPOSES],
        }
        purposes = counts["purposes"]
        slots = []
        present = 0
        if cab:
            for slot in range(1, USER_SLOTS + 1):
                info = (_people_maps(status).get("users") or {}).get(str(slot))
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
                        # 지문만 있어도 사람/총기/RFID 추가 등록 가능. 삭제는 enrolled 시 차단.
                        "locked": False,
                    }
                )
        boards = []
        for index, item in enumerate(self.data["lockers"], start=1):
            item_live = self._live_of(item["id"])
            item_board = item_live.get("board") or {}
            item_counts = self._counts(item_live.get("status") or {}, item_board) if item_live.get("online") else {
                "registered": 0, "issued": 0, "stored": 0, "empty": 0, "unpaid": 0,
                "purposes": [{"name": name, "count": 0} for name in PURPOSES],
            }
            boards.append({
                "id": item["id"],
                "no": index,
                "name": item["name"],
                "host": item["host"],
                "port": item["port"],
                "kind": item.get("kind") if item.get("kind") in ("rifle", "pistol") else "rifle",
                "selected": bool(cab) and item["id"] == cab["id"],
                "online": bool(item_live.get("online")),
                "linking": bool(item_live.get("linking")) and not item_live.get("online"),
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
                "kind": cab.get("kind") if cab.get("kind") in ("rifle", "pistol") else "rifle",
                "online": bool(live.get("online")),
                "error": live.get("error") or "",
                "sensor": bool((status.get("sensor") or {}).get("connected")),
                "checked_at": live.get("at") or "",
            } if cab else None,
            "slots": slots,
            "summary": {"stored": counts["stored"], "issued": counts["issued"], "empty": counts["empty"], "present": present, "registered": counts["registered"]},
            "purposes": purposes,
            "purpose_label": self.data.get("purpose_label") or "젤루텍",
            "unit_name": self.data.get("unit_name") or "지문인식",
            "armory_inside": bool(self.data.get("armory_inside")),
            "link": {
                "talking": any(bool((self.lives.get(item["id"]) or {}).get("talking")) for item in self.data["lockers"]),
                "pulse": self._pulse,
            },
            "catalog": [
                {"index": index, "no": index + 1, "category": item["category"], "model": item["model"]}
                for index, item in enumerate(cab.get("catalog") or [])
            ] if master and cab else [],
            "masters": self._people_view("master", MASTER_SLOTS, master),
            "subs": self._people_view("sub", SUB_SLOTS, master),
            "operator": self._operator_view(master or operator),
            "memo": (cab.get("memo") or "") if master and cab else "",
            "history": [
                row for row in reversed(self.data.get("history") or [])
                if str(row.get("locker_id")) == str(cab["id"]) and str(row.get("kind") or "gun") == "gun"
            ][:300] if cab and (master or operator) else [],
            "alerts": self.pending_alerts(),
            "logs": self.pending_logs(40),
            "patlite": self._patlite_snapshot(),
            "rfid": self._rfid_snapshot(),
            "can": {
                "manage": master,
                "history": master or operator,
                "status": True,
            },
        }

    def _rfid_snapshot(self) -> dict[str, Any]:
        try:
            from rfid import status as rfid_status

            info = rfid_status(force=False)
            cfg = info.get("config") if isinstance(info.get("config"), dict) else {}
            return {
                "connected": bool(info.get("connected")),
                "model": str(info.get("model") or "RL17W"),
                "bus": str(info.get("bus") or ""),
                "cross_check": bool(cfg.get("cross_check", True)),
                "error": str(info.get("error") or ""),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "connected": False,
                "model": "RL17W",
                "bus": "",
                "cross_check": True,
                "error": str(exc) or "",
            }

    def _patlite_snapshot(self) -> dict[str, Any]:
        try:
            from patlite import status as patlite_status

            info = patlite_status(force=False)
            return {
                "connected": bool(info.get("connected")),
                "mode": str(info.get("mode") or "off"),
                "alarm": bool(info.get("alarm")),
                "ops": bool(info.get("ops")),
                "yellow": bool(info.get("yellow")),
                "model": str(info.get("model") or "LR6-USB"),
                "error": str(info.get("error") or ""),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "connected": False,
                "mode": "off",
                "alarm": False,
                "ops": False,
                "yellow": False,
                "model": "LR6-USB",
                "error": str(exc),
            }

    def _people_view(self, kind: str, count: int, show: bool) -> list[dict[str, Any]]:
        cab = self._selected()
        status = self._live_of(cab["id"]).get("status") or {} if cab else {}
        bucket = _bucket(kind)
        rows = []
        for slot in range(1, count + 1):
            local = self._public_record(kind, slot)
            live = (_people_maps(status).get(bucket) or {}).get(str(slot))
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


def _people_maps(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    payload = payload or {}
    wrapped = payload.get("masters")
    store = wrapped if isinstance(wrapped, dict) and isinstance(wrapped.get("masters"), dict) else None
    maps: dict[str, dict[str, Any]] = {}
    for key in ("masters", "subs", "users"):
        raw = store.get(key) if store is not None else None
        if not isinstance(raw, dict):
            raw = payload.get(key)
        if isinstance(raw, dict) and isinstance(raw.get("masters"), dict):
            raw = raw.get("masters")
        maps[key] = raw if isinstance(raw, dict) else {}
    return maps


def _compact(text: Any) -> str:
    return str(text or "").replace(" ", "")


def _is_placeholder(kind: str, slot: int, name: Any) -> bool:
    text = _compact(name)
    if kind == "user":
        return text in ("", f"사용자{slot}")
    if kind == "master":
        return text in ("", f"마스터{slot}", f"마스터{slot}관리자")
    if kind == "sub":
        return text in ("", f"서브{slot}", f"서브{slot}관리자")
    return not text


def _split_label(kind: str, slot: int, label: Any) -> tuple[str, str]:
    raw = str(label or "").strip()
    if not raw or _is_placeholder(kind, slot, raw):
        return "", ""
    if " " in raw:
        rank, name = raw.split(" ", 1)
        rank, name = rank.strip(), name.strip()
        if rank and name and len(rank) <= 4:
            return rank, name
    return "", raw


def _profile_score(kind: str, slot: int, rank: Any, name: Any, sn: Any) -> int:
    score = 0
    if str(rank or "").strip():
        score += 2
    if str(sn or "").strip():
        score += 3
    text = str(name or "").strip()
    if text and not _is_placeholder(kind, slot, text):
        score += 4 + min(len(text), 12)
    return score


def _merge_profile(kind: str, slot: int, local: dict[str, Any] | None, live_label: Any) -> tuple[dict[str, Any] | None, bool]:
    local = dict(local) if isinstance(local, dict) else {}
    live_rank, live_name = _split_label(kind, slot, live_label)
    local_rank = str(local.get("rank") or "").strip()
    local_name = str(local.get("name") or "").strip()
    local_sn = str(local.get("sn") or "").strip()
    local_score = _profile_score(kind, slot, local_rank, "" if _is_placeholder(kind, slot, local_name) else local_name, local_sn)
    live_score = _profile_score(kind, slot, live_rank, live_name, "")
    rank, name = local_rank, "" if _is_placeholder(kind, slot, local_name) else local_name
    if live_score > local_score:
        if live_rank:
            rank = live_rank
        if live_name:
            name = live_name
    elif live_score == local_score:
        if not rank and live_rank:
            rank = live_rank
        if not name and live_name:
            name = live_name
    record = dict(local)
    rank = _clip(rank, 4)
    name = _clip(name, 5)
    changed = rank != local_rank or name != local_name
    if changed:
        record["rank"] = rank
        record["name"] = name
    elif not local:
        return None, False
    label = f"{record.get('rank') or ''} {record.get('name') or ''}".strip()
    live_clean = str(live_label or "").strip()
    push = bool(label) and not _is_placeholder(kind, slot, label) and label != live_clean
    if not record.get("rank") and not record.get("name"):
        return (local or None) or None, False
    return record if changed or local else None, push


def _clip(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _normalize_rfid_uid(value: Any) -> str:
    """Keep hex UID only (uppercase, no separators)."""
    text = str(value or "").strip().upper()
    cleaned = "".join(ch for ch in text if ch in "0123456789ABCDEF")
    return cleaned[:64]


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
        stop.wait(0.35)


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
