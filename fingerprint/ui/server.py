#!/usr/bin/env python3
"""Fingerprint locker UI + SFM daemon bridge."""

from __future__ import annotations

import asyncio
import json
import sys
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

def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


ROOT = resource_root()
STATIC = ROOT / "static"

app = FastAPI(title="FP Locker UI")
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


class PurposeLabel(BaseModel):
    label: str = Field(default="", max_length=10)


class LockerSlot(BaseModel):
    slot: int = Field(ge=1, le=10)


class TimeSync(BaseModel):
    epoch: float


class EngineerLogin(BaseModel):
    password: str = Field(default="")


class EngineerKind(BaseModel):
    kind: str = Field(default="rifle")


_time_sync = {"epoch": None, "mono": None}


@app.on_event("startup")
def on_startup() -> None:
    try:
        bridge.start()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] sfm bridge start failed: {exc}")


@app.on_event("shutdown")
def on_shutdown() -> None:
    board.close()
    bridge.stop()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    # Return cached daemon snapshot + board map in one round-trip.
    # Board SENS is polled in the background — never block OMS on USART.
    snap = bridge.snapshot()
    try:
        snap["board"] = {"ok": True, **board.status()}
    except LockerBoardError:
        snap["board"] = {}
    return snap


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
    try:
        data = set_user_ops(body.slot, kind, body.purpose or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    door = _open_board_slot(body.slot)
    return {"ok": True, "store": data, "door": door}


def _open_board_slot(slot: int) -> str:
    try:
        return board.open_slot(slot)
    except LockerBoardError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/locker/open")
def api_locker_open(body: LockerSlot) -> dict[str, Any]:
    return {"ok": True, "door": _open_board_slot(body.slot)}


@app.post("/api/time")
def api_set_time(body: TimeSync) -> dict[str, Any]:
    """OMS clock. The locker screen follows this instead of the Pi clock."""
    _time_sync["epoch"] = float(body.epoch)
    _time_sync["mono"] = time.monotonic()
    return {"ok": True}


@app.get("/api/time")
def api_get_time() -> dict[str, Any]:
    if _time_sync["epoch"] is None or _time_sync["mono"] is None:
        return {"synced": False, "epoch": time.time()}
    return {"synced": True, "epoch": _time_sync["epoch"] + (time.monotonic() - _time_sync["mono"])}


@app.get("/api/locker/status")
def api_locker_status() -> dict[str, Any]:
    try:
        return {"ok": True, **board.status()}
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
