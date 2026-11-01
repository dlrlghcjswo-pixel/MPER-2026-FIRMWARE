#!/usr/bin/env python3
"""Fingerprint locker UI + SFM daemon bridge."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn

from locker_board import LockerBoardError, board
from sfm_bridge import (
    bridge,
    clear_master,
    clear_sub,
    clear_user,
    factory_reset_locker,
    load_masters,
    locker_user_id,
    rename_entry,
    rename_user,
    set_locker_kind,
    set_purpose_label,
    save_master,
    save_sub,
    save_user,
    set_user_ops,
    sub_user_id,
)

ENGINEER_PASSWORD = "0263091150"

_ALERT_LOCK = threading.Lock()
_ALERT_SEQ = 0
_ALERTS: list[dict[str, Any]] = []
_ALERT_MAX = 50
_ACCESS_LOCK = threading.Lock()
_ACCESS_SEQ = 0
_ACCESS: list[dict[str, Any]] = []
_ACCESS_MAX = 100


def _push_alert(kind: str, message: str, slot: int | None = None) -> dict[str, Any]:
    global _ALERT_SEQ
    with _ALERT_LOCK:
        _ALERT_SEQ += 1
        item = {
            "id": _ALERT_SEQ,
            "kind": kind,
            "message": message,
            "slot": slot,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        _ALERTS.append(item)
        if len(_ALERTS) > _ALERT_MAX:
            del _ALERTS[:-_ALERT_MAX]
        return dict(item)


def _pending_alerts() -> list[dict[str, Any]]:
    with _ALERT_LOCK:
        return [dict(item) for item in _ALERTS]


def _ack_alerts(ids: list[int]) -> list[dict[str, Any]]:
    wanted = set()
    for raw in ids:
        try:
            wanted.add(int(raw))
        except (TypeError, ValueError):
            continue
    with _ALERT_LOCK:
        _ALERTS[:] = [item for item in _ALERTS if int(item.get("id") or 0) not in wanted]
        return [dict(item) for item in _ALERTS]


def _push_access(
    role: str,
    slot: int,
    name: str = "",
    user_id: int | None = None,
    action: str = "로그인",
) -> dict[str, Any]:
    global _ACCESS_SEQ
    with _ACCESS_LOCK:
        _ACCESS_SEQ += 1
        item = {
            "id": _ACCESS_SEQ,
            "kind": "access",
            "role": role,
            "slot": slot,
            "name": name,
            "user_id": user_id,
            "action": action or "로그인",
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        _ACCESS.append(item)
        if len(_ACCESS) > _ACCESS_MAX:
            del _ACCESS[:-_ACCESS_MAX]
        return dict(item)


def _pending_access() -> list[dict[str, Any]]:
    with _ACCESS_LOCK:
        return [dict(item) for item in _ACCESS]


def _ack_access(ids: list[int]) -> list[dict[str, Any]]:
    wanted = set()
    for raw in ids:
        try:
            wanted.add(int(raw))
        except (TypeError, ValueError):
            continue
    with _ACCESS_LOCK:
        _ACCESS[:] = [item for item in _ACCESS if int(item.get("id") or 0) not in wanted]
        return [dict(item) for item in _ACCESS]


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


ROOT = resource_root()
STATIC = ROOT / "static"

app = FastAPI(title="FP Locker UI")


@app.middleware("http")
async def oms_heartbeat_middleware(request, call_next):
    # OMS PC polls /api/* from a LAN IP; kiosk Chromium uses 127.0.0.1.
    # Treat remote API traffic (or explicit OMS header) as proof the link is up.
    client = (request.client.host if request.client else "") or ""
    path = request.url.path or ""
    if request.headers.get("x-zellootec-oms") == "1":
        _touch_oms()
    elif path.startswith("/api/") and client not in ("127.0.0.1", "::1", ""):
        _touch_oms()
    return await call_next(request)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


class EnrollStart(BaseModel):
    user_id: int = Field(default=1, ge=1, le=10000)


class MasterAssign(BaseModel):
    slot: int = Field(ge=1, le=5)
    user_id: int = Field(default=1, ge=1)


class MasterDelete(BaseModel):
    slot: int = Field(ge=1, le=5)
    user_id: int = Field(default=0, ge=0)


class SubAssign(BaseModel):
    slot: int = Field(ge=1, le=30)
    user_id: int = Field(default=0, ge=0)


class SubDelete(BaseModel):
    slot: int = Field(ge=1, le=30)
    user_id: int = Field(default=0, ge=0)


class UserAssign(BaseModel):
    slot: int = Field(ge=1, le=10)
    user_id: int = Field(default=0, ge=0)


class UserDelete(BaseModel):
    slot: int = Field(ge=1, le=10)
    user_id: int = Field(default=0, ge=0)


class UserRename(BaseModel):
    slot: int = Field(ge=1, le=10)
    name: str = Field(default="", max_length=24)


class RoleRename(BaseModel):
    kind: str = Field(default="user")
    slot: int = Field(ge=1, le=30)
    name: str = Field(default="", max_length=24)


class UserOps(BaseModel):
    slot: int = Field(ge=1, le=10)
    kind: str = Field(default="issue")
    purpose: str = Field(default="")
    approver_role: str = Field(default="")
    approver_slot: int = Field(default=0, ge=0, le=30)
    approver_name: str = Field(default="", max_length=40)
    approver_user_id: int = Field(default=0, ge=0)


class AccessLog(BaseModel):
    role: str = Field(default="master")
    slot: int = Field(default=1, ge=1, le=30)
    name: str = Field(default="", max_length=40)
    user_id: int = Field(default=0, ge=0)
    action: str = Field(default="로그인", max_length=20)


class PurposeLabel(BaseModel):
    label: str = Field(default="", max_length=10)


class LockerSlot(BaseModel):
    slot: int = Field(ge=1, le=10)


class TimeSync(BaseModel):
    epoch: float
    locker_no: int | None = Field(default=None, ge=1, le=99)
    locker_name: str = Field(default="", max_length=40)


class EngineerLogin(BaseModel):
    password: str = Field(default="")


class EngineerKind(BaseModel):
    kind: str = Field(default="rifle")


class AlertPush(BaseModel):
    kind: str = Field(default="unauthorized")
    message: str = Field(default="인가되지 않은 접근입니다!")
    slot: int | None = Field(default=None, ge=1, le=10)


class AlertAck(BaseModel):
    ids: list[int] = Field(default_factory=list)


_time_sync = {"epoch": None, "mono": None}
_EMERGENCY_COOLDOWN: dict[int, float] = {}
_EMERGENCY_COOLDOWN_SEC = 8.0
_AUTHORIZED_OPEN_UNTIL: dict[int, float] = {}
_AUTHORIZED_OPEN_SEC = 45.0
_OMS_LAST = 0.0
_OMS_SEEN = False
_OMS_TIMEOUT_SEC = 8.0
_NETWORK_ALERT_ACTIVE = False
_LAN_WAS_UP = True
_LOCKER_NO = 0
_LOCKER_NAME = ""


def _touch_oms() -> None:
    global _OMS_LAST, _OMS_SEEN
    _OMS_LAST = time.monotonic()
    _OMS_SEEN = True


def _oms_online() -> bool:
    """OMS 프로그램이 최근에 폴링했는지 (프로그램 종료 ≠ 네트워크 단절)."""
    if not _OMS_SEEN:
        return True
    return (time.monotonic() - _OMS_LAST) < _OMS_TIMEOUT_SEC


def _lan_online() -> bool:
    """실제 랜 링크(케이블) 연결 여부. OMS 프로그램 on/off 와 무관."""
    net = Path("/sys/class/net")
    if not net.is_dir():
        return True
    for iface in sorted(net.iterdir()):
        name = iface.name
        if name == "lo" or name.startswith("docker") or name.startswith("veth"):
            continue
        try:
            oper = (iface / "operstate").read_text(encoding="ascii").strip().lower()
        except OSError:
            oper = ""
        try:
            carrier = (iface / "carrier").read_text(encoding="ascii").strip()
        except OSError:
            carrier = ""
        if carrier == "1" or oper == "up":
            return True
    return False


def _locker_phrase() -> str:
    name = str(_LOCKER_NAME or "").strip()
    return name or "보관함"


def _emergency_message(slot: int | None) -> str:
    locker = _locker_phrase()
    detail = f"{locker} {int(slot)}번 총기" if slot else locker
    return f"비상개폐가 되었습니다.\n{detail}"


def _unauthorized_message() -> str:
    return f"비인가자가 접근했습니다.\n{_locker_phrase()}"


def _network_message() -> str:
    return f"네트워크연결이 단절되었습니다.\n{_locker_phrase()}"


def _ensure_network_alert() -> None:
    """랜 케이블/링크가 떨어질 때만 네트워크 단절 알람. OMS 창을 닫는 것과는 무관."""
    global _NETWORK_ALERT_ACTIVE, _LAN_WAS_UP
    up = _lan_online()
    if up:
        _LAN_WAS_UP = True
        _NETWORK_ALERT_ACTIVE = False
        return
    # 한번이라도 링크가 살아 있던 뒤에 끊긴 경우만
    if not _LAN_WAS_UP:
        return
    if _NETWORK_ALERT_ACTIVE:
        return
    _NETWORK_ALERT_ACTIVE = True
    message = _network_message()
    item = _push_alert("network", message)
    try:
        bridge._push(
            {
                "event": "security_alert",
                "id": item.get("id"),
                "kind": "network",
                "message": item.get("message") or message,
                "slot": None,
            }
        )
    except Exception:
        pass


def _mark_authorized_open(slot: int) -> None:
    _AUTHORIZED_OPEN_UNTIL[int(slot)] = time.monotonic() + _AUTHORIZED_OPEN_SEC


def _is_authorized_open(slot: int) -> bool:
    return time.monotonic() < float(_AUTHORIZED_OPEN_UNTIL.get(int(slot), 0.0) or 0.0)


def _emit_emergency(slot: int, prev_door: str, prev_sens: str, door: str, sens: str, reason: str) -> None:
    now = time.monotonic()
    last = _EMERGENCY_COOLDOWN.get(slot, 0.0)
    if now - last < _EMERGENCY_COOLDOWN_SEC:
        return
    _EMERGENCY_COOLDOWN[slot] = now
    message = _emergency_message(slot)
    item = _push_alert("emergency", message, slot)
    try:
        Path("/home/pi/fingerprint/logs").mkdir(parents=True, exist_ok=True)
        with open("/home/pi/fingerprint/logs/emergency.log", "a", encoding="utf-8") as log:
            log.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} slot={slot} reason={reason} "
                f"prev_sens={prev_sens} sens={sens} prev_door={prev_door} door={door}\n"
            )
    except Exception:
        pass
    try:
        bridge._push(
            {
                "event": "security_alert",
                "id": item.get("id"),
                "kind": "emergency",
                "message": item.get("message") or message,
                "slot": slot,
            }
        )
    except Exception:
        pass


def _on_board_change(prev_door: str, prev_sens: str, door: str, sens: str) -> None:
    """SENS 1→0 without authorized fingerprint unlock → emergency.

    Door bit alone is NOT trusted (board often reports door=1 while pulling a gun).
    Normal issue/return must call /api/open first (_mark_authorized_open).
    """
    if len(prev_sens) != 10 or len(sens) != 10:
        return
    for index in range(10):
        slot = index + 1
        if not (prev_sens[index] == "1" and sens[index] == "0"):
            continue
        if _is_authorized_open(slot):
            continue
        _emit_emergency(slot, prev_door, prev_sens, door, sens, "sens_drop")


def _install_board_hook() -> None:
    try:
        board.set_change_hook(_on_board_change)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] board emergency hook failed: {exc}")


# Import-time install: do not rely only on FastAPI startup (frozen onefile edge cases).
_install_board_hook()


@app.on_event("startup")
def on_startup() -> None:
    try:
        bridge.start()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] sfm bridge start failed: {exc}")
    _install_board_hook()


@app.on_event("shutdown")
def on_shutdown() -> None:
    try:
        board.set_change_hook(None)
    except Exception:
        pass
    board.close()
    bridge.stop()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    # Return cached daemon snapshot + board map in one round-trip.
    # Board SENS is polled in the background — never block OMS on USART.
    _ensure_network_alert()
    snap = bridge.snapshot()
    try:
        snap["board"] = {"ok": True, **board.status()}
    except LockerBoardError:
        snap["board"] = {}
    snap["alerts"] = _pending_alerts()
    snap["access"] = _pending_access()
    snap["oms_online"] = _oms_online()
    snap["oms_seen"] = _OMS_SEEN
    snap["lan_online"] = _lan_online()
    snap["authorized_open"] = [s for s in range(1, 11) if _is_authorized_open(s)]
    return snap


@app.get("/api/alerts")
def api_alerts() -> dict[str, Any]:
    return {"ok": True, "alerts": _pending_alerts()}


@app.post("/api/alerts")
def api_alerts_push(body: AlertPush) -> dict[str, Any]:
    kind = (body.kind or "unauthorized").strip() or "unauthorized"
    if kind == "unreturned":
        return {"ok": True, "alerts": _pending_alerts()}
    if kind not in ("unauthorized", "emergency", "network"):
        kind = "unauthorized"
    defaults = {
        "unauthorized": _unauthorized_message(),
        "emergency": _emergency_message(body.slot),
        "network": _network_message(),
    }
    message = (body.message or "").strip() or defaults.get(kind, _unauthorized_message())
    item = _push_alert(kind, message, body.slot)
    return {"ok": True, "alert": item, "alerts": _pending_alerts()}


@app.post("/api/alerts/ack")
def api_alerts_ack(body: AlertAck) -> dict[str, Any]:
    return {"ok": True, "alerts": _ack_alerts(list(body.ids or []))}


@app.post("/api/access")
def api_access_push(body: AccessLog) -> dict[str, Any]:
    role = (body.role or "").strip().lower()
    if role not in ("master", "sub"):
        raise HTTPException(status_code=400, detail="invalid role")
    slot = int(body.slot or 0)
    if role == "master" and not (1 <= slot <= 5):
        raise HTTPException(status_code=400, detail="invalid master slot")
    if role == "sub" and not (1 <= slot <= 30):
        raise HTTPException(status_code=400, detail="invalid sub slot")
    action = (body.action or "").strip() or "로그인"
    name = (body.name or "").strip()
    user_id = int(body.user_id or 0) or None
    item = _push_access(role, slot, name, user_id, action)
    return {"ok": True, "access": item, "events": _pending_access()}


@app.post("/api/access/ack")
def api_access_ack(body: AlertAck) -> dict[str, Any]:
    return {"ok": True, "access": _ack_access(list(body.ids or []))}


@app.get("/api/enroll/state")
def api_enroll_state() -> dict[str, Any]:
    return bridge.enroll_state()


@app.post("/api/enroll/start")
def api_enroll_start(body: EnrollStart) -> dict[str, Any]:
    result = bridge.start_enroll(body.user_id)
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail="fingerprint daemon not connected")
    return result


@app.post("/api/enroll/abort")
def api_enroll_abort() -> dict[str, Any]:
    return bridge.abort()


@app.post("/api/enroll/discard")
def api_enroll_discard(body: EnrollStart) -> dict[str, Any]:
    bridge.abort()
    deleted = bridge.delete_user(body.user_id)
    return {"ok": True, "delete": deleted, "store": load_masters()}


@app.post("/api/enroll/commit")
def api_enroll_commit(body: EnrollStart) -> dict[str, Any]:
    """최초 마스터1 등록 확정."""
    st = bridge.enroll_state()
    if not st.get("done") or not st.get("ok"):
        raise HTTPException(status_code=400, detail="enroll not completed")
    data = save_master(1, body.user_id, st.get("quality"))
    return {"ok": True, "masters": data, "enroll": st}


@app.post("/api/master/delete")
def api_master_delete(body: MasterDelete) -> dict[str, Any]:
    data = load_masters()
    slot_info = (data.get("masters") or {}).get(str(body.slot))
    user_id = body.user_id
    if not user_id and slot_info:
        user_id = int(slot_info.get("user_id") or body.slot)
    if not user_id:
        user_id = body.slot
    deleted = bridge.delete_user(user_id)
    cleared = clear_master(body.slot)
    return {"ok": True, "delete": deleted, "masters": cleared}


@app.post("/api/master/assign")
def api_master_assign(body: MasterAssign) -> dict[str, Any]:
    st = bridge.enroll_state()
    quality = st.get("quality") if st.get("user_id") == body.user_id else None
    data = save_master(body.slot, body.user_id, quality)
    return {"ok": True, "masters": data}


@app.post("/api/sub/assign")
def api_sub_assign(body: SubAssign) -> dict[str, Any]:
    st = bridge.enroll_state()
    uid = body.user_id or sub_user_id(body.slot)
    if not st.get("done") or not st.get("ok"):
        raise HTTPException(status_code=400, detail="enroll not completed")
    quality = st.get("quality")
    data = save_sub(body.slot, uid, quality)
    return {"ok": True, "store": data, "enroll": st}


@app.post("/api/sub/delete")
def api_sub_delete(body: SubDelete) -> dict[str, Any]:
    data = load_masters()
    slot_info = (data.get("subs") or {}).get(str(body.slot))
    user_id = body.user_id
    if not user_id and slot_info:
        user_id = int(slot_info.get("user_id") or sub_user_id(body.slot))
    if not user_id:
        user_id = sub_user_id(body.slot)
    deleted = bridge.delete_user(user_id)
    cleared = clear_sub(body.slot)
    return {"ok": True, "delete": deleted, "store": cleared}


@app.post("/api/user/assign")
def api_user_assign(body: UserAssign) -> dict[str, Any]:
    st = bridge.enroll_state()
    uid = body.user_id or locker_user_id(body.slot)
    if not st.get("done") or not st.get("ok"):
        raise HTTPException(status_code=400, detail="enroll not completed")
    quality = st.get("quality")
    data = save_user(body.slot, uid, quality)
    return {"ok": True, "store": data, "enroll": st}


@app.post("/api/user/delete")
def api_user_delete(body: UserDelete) -> dict[str, Any]:
    data = load_masters()
    slot_info = (data.get("users") or {}).get(str(body.slot))
    user_id = body.user_id
    if not user_id and slot_info:
        user_id = int(slot_info.get("user_id") or locker_user_id(body.slot))
    if not user_id:
        user_id = locker_user_id(body.slot)
    deleted = bridge.delete_user(user_id)
    cleared = clear_user(body.slot)
    return {"ok": True, "delete": deleted, "store": cleared}


@app.post("/api/user/rename")
def api_user_rename(body: UserRename) -> dict[str, Any]:
    try:
        data = rename_user(body.slot, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "store": data}


@app.post("/api/rename")
def api_role_rename(body: RoleRename) -> dict[str, Any]:
    kind = body.kind if body.kind in ("master", "sub", "user") else "user"
    try:
        data = rename_entry(kind, body.slot, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "store": data}


@app.post("/api/purpose-label")
def api_purpose_label(body: PurposeLabel) -> dict[str, Any]:
    data = set_purpose_label(body.label)
    return {"ok": True, "label": data.get("purpose_label") or "젤루텍", "store": data}


@app.post("/api/user/ops")
def api_user_ops(body: UserOps) -> dict[str, Any]:
    kind = body.kind if body.kind in ("issue", "store") else "issue"
    role = (body.approver_role or "").strip().lower()
    approver = None
    if role in ("master", "sub") and int(body.approver_slot or 0) > 0:
        approver = {
            "role": role,
            "slot": int(body.approver_slot),
            "name": (body.approver_name or "").strip(),
            "user_id": int(body.approver_user_id or 0) or None,
        }
    try:
        data = set_user_ops(body.slot, kind, body.purpose or None, approver=approver)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    door = _open_board_slot(body.slot)
    return {"ok": True, "store": data, "door": door}


def _open_board_slot(slot: int) -> str:
    # Mark before OPEN so the board poll/hook cannot treat the unlock as 비상개폐.
    _mark_authorized_open(int(slot))
    try:
        return board.open_slot(slot)
    except LockerBoardError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/locker/open")
def api_locker_open(body: LockerSlot) -> dict[str, Any]:
    return {"ok": True, "door": _open_board_slot(body.slot)}


@app.post("/api/time")
def api_set_time(body: TimeSync) -> dict[str, Any]:
    """OMS clock + heartbeat. The locker screen follows this instead of the Pi clock."""
    global _LOCKER_NO, _LOCKER_NAME
    _touch_oms()
    _time_sync["epoch"] = float(body.epoch)
    _time_sync["mono"] = time.monotonic()
    if body.locker_no:
        _LOCKER_NO = int(body.locker_no)
    name = str(body.locker_name or "").strip()
    if name:
        _LOCKER_NAME = name[:40]
    return {"ok": True, "oms_online": True, "locker_no": _LOCKER_NO, "locker_name": _LOCKER_NAME}


@app.get("/api/time")
def api_get_time() -> dict[str, Any]:
    if _time_sync["epoch"] is None or _time_sync["mono"] is None:
        return {"synced": False, "epoch": time.time()}
    return {"synced": True, "epoch": _time_sync["epoch"] + (time.monotonic() - _time_sync["mono"])}


@app.get("/api/locker/status")
def api_locker_status() -> dict[str, Any]:
    _ensure_network_alert()
    try:
        return {
            "ok": True,
            **board.status(),
            "oms_online": _oms_online(),
            "oms_seen": _OMS_SEEN,
            "lan_online": _lan_online(),
            "locker_no": _LOCKER_NO,
            "locker_name": _LOCKER_NAME,
        }
    except LockerBoardError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/engineer/login")
def api_engineer_login(body: EngineerLogin) -> dict[str, Any]:
    if str(body.password or "") != ENGINEER_PASSWORD:
        raise HTTPException(status_code=403, detail="비밀번호가 올바르지 않습니다.")
    return {"ok": True}


@app.post("/api/engineer/kind")
def api_engineer_kind(body: EngineerKind) -> dict[str, Any]:
    kind = str(body.kind or "").strip()
    if kind not in ("rifle", "pistol"):
        raise HTTPException(status_code=400, detail="보관함 종류가 올바르지 않습니다.")
    data = set_locker_kind(kind)
    return {"ok": True, "locker_kind": kind, "store": data, **bridge.snapshot()}


@app.post("/api/engineer/reset")
def api_engineer_reset() -> dict[str, Any]:
    try:
        data = factory_reset_locker()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        snap = bridge.snapshot()
    except (OSError, json.JSONDecodeError):
        return {"ok": True, "store": data, "locker_kind": data.get("locker_kind"), "reset_seq": data.get("reset_seq")}
    return {"ok": True, "store": data, **snap}


@app.get("/api/events")
async def api_events() -> StreamingResponse:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
    loop = asyncio.get_running_loop()

    def on_msg(msg: dict[str, Any]) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, msg)
        except Exception:
            pass

    unsub = bridge.subscribe(on_msg)

    async def gen() -> AsyncIterator[str]:
        try:
            yield f"data: {json.dumps({'event': 'snapshot', **bridge.snapshot()}, ensure_ascii=False)}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'event': 'ping'})}\n\n"
        finally:
            unsub()

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, reload=False)
