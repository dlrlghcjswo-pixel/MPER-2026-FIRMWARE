"""Patlite LR6-USB (RYG) signal tower control for OMS.

Uses the official HID protocol (VID 0x191A / PID 0x8003).
Full buzzer settings: pattern, count (0=continuous / 1–15), pitch A/B.
Volume is hardware-only (body High/Low switch).
"""

from __future__ import annotations

import json
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any

VENDOR_ID = 0x191A
PRODUCT_ID = 0x8003

LED_OFF = 0x0
LED_ON = 0x1          # 점등(고정)
LED_PATTERN1 = 0x2    # 빠른 점멸
LED_PATTERN2 = 0x3    # 점멸
LED_PATTERN3 = 0x4
LED_PATTERN4 = 0x5

BUZZER_OFF = 0x0
BUZZER_ON = 0x1
BUZZER_PATTERN1 = 0x2
BUZZER_PATTERN2 = 0x3
BUZZER_PATTERN3 = 0x4
BUZZER_PATTERN4 = 0x5

PITCH_OFF = 0x0
PITCH_DFLT_A = 0xE
PITCH_DFLT_B = 0xF

PATTERN_CHOICES = [
    {"id": 0, "label": "끄기"},
    {"id": 1, "label": "연속음 (톤)"},
    {"id": 2, "label": "패턴1 (사이렌)"},
    {"id": 3, "label": "패턴2"},
    {"id": 4, "label": "패턴3"},
    {"id": 5, "label": "패턴4"},
]

LED_CHOICES = [
    {"id": 1, "label": "점등 (고정)"},
    {"id": 2, "label": "점멸1 (빠름)"},
    {"id": 3, "label": "점멸2"},
    {"id": 4, "label": "점멸3"},
    {"id": 5, "label": "점멸4"},
]

PITCH_CHOICES = [
    {"id": 0, "label": "OFF (무음)"},
    {"id": 1, "label": "A6 (1760Hz)"},
    {"id": 2, "label": "B♭6 (1865Hz)"},
    {"id": 3, "label": "B6 (1976Hz)"},
    {"id": 4, "label": "C7 (2093Hz)"},
    {"id": 5, "label": "D♭7 (2218Hz)"},
    {"id": 6, "label": "D7 (2349Hz)"},
    {"id": 7, "label": "E♭7 (2489Hz)"},
    {"id": 8, "label": "E7 (2637Hz)"},
    {"id": 9, "label": "F7 (2794Hz)"},
    {"id": 10, "label": "G♭7 (2960Hz)"},
    {"id": 11, "label": "G7 (3136Hz)"},
    {"id": 12, "label": "A♭7 (3322Hz)"},
    {"id": 13, "label": "A7 (3520Hz)"},
    {"id": 14, "label": "기본(음A=D7)"},
    {"id": 15, "label": "기본(음B=정지)"},
]

# Legacy preset id → full profile (for old patlite.json)
_LEGACY_PRESETS: dict[str, dict[str, int]] = {
    "off": {"pattern": 0, "limit": 0, "pitch_a": 0, "pitch_b": 0},
    "continuous": {"pattern": 1, "limit": 0, "pitch_a": 14, "pitch_b": 15},
    "pattern1": {"pattern": 2, "limit": 0, "pitch_a": 14, "pitch_b": 15},
    "pattern2": {"pattern": 3, "limit": 0, "pitch_a": 14, "pitch_b": 15},
    "pattern3": {"pattern": 4, "limit": 0, "pitch_a": 14, "pitch_b": 15},
    "pattern4": {"pattern": 5, "limit": 0, "pitch_a": 14, "pitch_b": 15},
    "pattern1_x2": {"pattern": 2, "limit": 2, "pitch_a": 14, "pitch_b": 15},
    "pattern2_x2": {"pattern": 3, "limit": 2, "pitch_a": 14, "pitch_b": 15},
    "pattern3_x2": {"pattern": 4, "limit": 2, "pitch_a": 14, "pitch_b": 15},
    "pattern4_x2": {"pattern": 5, "limit": 2, "pitch_a": 14, "pitch_b": 15},
}

_DEFAULT_PROFILES: dict[str, dict[str, int]] = {
    # led: 1=점등, 2~5=점멸 패턴 (기본 점멸2)
    "red": {"pattern": 2, "limit": 0, "pitch_a": 14, "pitch_b": 15, "led": 3},
    "yellow": {"pattern": 4, "limit": 2, "pitch_a": 14, "pitch_b": 15, "led": 3},
    "green": {"pattern": 3, "limit": 2, "pitch_a": 14, "pitch_b": 15, "led": 3},
}

_MODE_OFF = "off"
_MODE_ALARM = "alarm"
_MODE_OPS = "ops"
_MODE_YELLOW = "yellow"

_lock = threading.RLock()
_io_lock = threading.Lock()  # serialize HID open/write — concurrent opens drop reports
_mode = _MODE_OFF
_ops_active = False
_alarm_active = False
_yellow_active = False
_yellow_until = 0.0
_YELLOW_HOLD_SEC = 45.0
_last_error = ""
_hid_module: Any = None
_connected_cache = False
_connected_at = 0.0
_CONNECTED_TTL = 1.0
_devices_cache: list[dict[str, Any]] = []
_devices_cache_at = 0.0
_DEVICES_TTL = 0.8
_profiles: dict[str, dict[str, int]] = {k: dict(v) for k, v in _DEFAULT_PROFILES.items()}
_DEFAULT_ENABLES: dict[str, bool] = {"red": True, "yellow": True, "green": True}
_enables: dict[str, bool] = dict(_DEFAULT_ENABLES)
_device_path: str = ""


def _oms_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _legacy_cfg_path() -> Path:
    return _oms_root() / "patlite.json"


def _ensure_deps_path() -> None:
    candidates: list[Path] = []
    root = _oms_root()
    candidates.append(root / "_deps")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "_deps")
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass))
        candidates.append(Path(meipass) / "_deps")
    for deps in candidates:
        if deps.is_dir() and str(deps) not in sys.path:
            sys.path.insert(0, str(deps))


def _hid() -> Any:
    global _hid_module, _last_error
    if _hid_module is not None:
        return _hid_module
    _ensure_deps_path()
    try:
        import hid as mod  # type: ignore
    except Exception as exc:  # noqa: BLE001
        _last_error = (
            f"hidapi import failed: {exc}. "
            "스마트 무기고 시스템.exe를 최신으로 다시 빌드하거나, "
            "OMS.bat(파이썬 실행) / _deps/hidapi 를 확인하세요."
        )
        raise
    _hid_module = mod
    return mod


def _clip_int(raw: Any, lo: int, hi: int, fallback: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return max(lo, min(hi, value))


def _normalize_profile(raw: Any, fallback: dict[str, int]) -> dict[str, int]:
    base = dict(fallback)
    if isinstance(raw, str):
        preset = _LEGACY_PRESETS.get(raw.strip().lower())
        return dict(preset) if preset else base
    if not isinstance(raw, dict):
        return base
    return {
        "pattern": _clip_int(raw.get("pattern"), 0, 5, base["pattern"]),
        "limit": _clip_int(raw.get("limit"), 0, 15, base["limit"]),
        "pitch_a": _clip_int(raw.get("pitch_a"), 0, 15, base["pitch_a"]),
        "pitch_b": _clip_int(raw.get("pitch_b"), 0, 15, base["pitch_b"]),
        "led": _clip_int(raw.get("led"), 1, 5, base.get("led", LED_PATTERN2)),
    }


def _load_raw_cfg() -> dict[str, Any]:
    from oms_bundle import SECTION_PATLITE, migrate_section_from_file, read_section

    try:
        raw = read_section(SECTION_PATLITE)
        if isinstance(raw, dict):
            return raw
        migrated = migrate_section_from_file(SECTION_PATLITE, _legacy_cfg_path())
        if isinstance(migrated, dict):
            return migrated
    except Exception as exc:  # noqa: BLE001
        global _last_error
        _last_error = f"patlite config: {exc}"
    return {}


def _save_raw_cfg(data: dict[str, Any]) -> None:
    from oms_bundle import SECTION_PATLITE, write_section

    write_section(SECTION_PATLITE, data)


def _coerce_enable(raw: Any, fallback: bool = True) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return int(raw) != 0
    text = str(raw or "").strip().lower()
    if text in ("0", "false", "off", "no", "n", "끄기", "알림끄기"):
        return False
    if text in ("1", "true", "on", "yes", "y", "켜기", "알림켜기"):
        return True
    return bool(fallback)


def _normalize_enables(raw: Any) -> dict[str, bool]:
    base = dict(_DEFAULT_ENABLES)
    if not isinstance(raw, dict):
        return base
    for color in ("red", "yellow", "green"):
        if color in raw:
            base[color] = _coerce_enable(raw.get(color), base[color])
    return base


def load_enables() -> dict[str, bool]:
    global _enables
    raw = _load_raw_cfg()
    next_map = _normalize_enables(raw.get("enables"))
    with _lock:
        _enables = next_map
    return dict(next_map)


def color_enabled(color: str) -> bool:
    key = str(color or "").strip().lower()
    with _lock:
        if _enables:
            return bool(_enables.get(key, True))
    enables = load_enables()
    return bool(enables.get(key, True))


def load_profiles() -> dict[str, dict[str, int]]:
    global _profiles, _device_path, _enables
    data = {k: dict(v) for k, v in _DEFAULT_PROFILES.items()}
    raw = _load_raw_cfg()
    bucket = raw.get("profiles")
    if not isinstance(bucket, dict):
        bucket = raw.get("sounds") if isinstance(raw.get("sounds"), dict) else raw
    if isinstance(bucket, dict):
        for color in ("red", "yellow", "green"):
            data[color] = _normalize_profile(bucket.get(color), data[color])
    preferred = str(raw.get("device_path") or "").strip()
    enables = _normalize_enables(raw.get("enables"))
    with _lock:
        _profiles = data
        _enables = enables
        if preferred:
            _device_path = preferred
    return {k: dict(v) for k, v in data.items()}


def set_color_enabled(color: str, enabled: bool, *, persist: bool = True) -> dict[str, bool]:
    """Mute/unmute one tower color (used by 출입중 ↔ 황색 알림켜기/끄기)."""
    global _enables
    key = str(color or "").strip().lower()
    if key not in ("red", "yellow", "green"):
        return load_enables()
    load_profiles()
    with _lock:
        next_map = dict(_enables or _DEFAULT_ENABLES)
        next_map[key] = bool(enabled)
        _enables = next_map
        profiles = {k: dict(v) for k, v in _profiles.items()}
        device_path = str(_device_path or "")
    if persist:
        _save_raw_cfg(
            {
                "profiles": profiles,
                "enables": next_map,
                "device_path": device_path,
                "note": "LR6-USB is USB-HID (not COM). volume is hardware High/Low only",
            }
        )
    try:
        _refresh(beep_ops=False, beep_yellow=False)
    except Exception:
        pass
    return dict(next_map)


def save_profiles(payload: dict[str, Any] | None) -> dict[str, dict[str, int]]:
    global _profiles, _device_path, _enables
    current = load_profiles()
    body = payload if isinstance(payload, dict) else {}
    bucket = body.get("profiles") if isinstance(body.get("profiles"), dict) else body.get("sounds")
    if not isinstance(bucket, dict):
        bucket = body if isinstance(body, dict) else {}
    next_map = {
        color: _normalize_profile((bucket or {}).get(color), current[color])
        for color in ("red", "yellow", "green")
    }
    prev_enables = dict(_enables or _DEFAULT_ENABLES)
    enables = _normalize_enables(body.get("enables") if "enables" in body else _enables)
    with _lock:
        _profiles = next_map
        _enables = enables
        device_path = str(body.get("device_path") if body.get("device_path") is not None else _device_path or "").strip()
        _device_path = device_path
    _save_raw_cfg(
        {
            "profiles": next_map,
            "enables": enables,
            "device_path": device_path,
            "note": "LR6-USB is USB-HID (not COM). volume is hardware High/Low only",
        }
    )
    # Re-apply current mode so a newly muted color goes dark immediately.
    try:
        _refresh(beep_ops=False, beep_yellow=False)
    except Exception:
        pass
    # 황색 알림켜기 → 출입중 해제 / 알림끄기 → 출입중(사람감지 중지)
    try:
        was_yellow = bool(prev_enables.get("yellow", True))
        now_yellow = bool(enables.get("yellow", True))
        if was_yellow != now_yellow:
            from core import STORE

            if now_yellow and bool(STORE.data.get("armory_inside")):
                STORE.set_armory_inside(False, sync_patlite_enable=False)
            elif not now_yellow and not bool(STORE.data.get("armory_inside")):
                STORE.set_armory_inside(True, sync_patlite_enable=False)
    except Exception:
        pass
    return {k: dict(v) for k, v in next_map.items()}


def _path_to_str(path: Any) -> str:
    if path is None:
        return ""
    if isinstance(path, bytes):
        # latin-1 round-trip keeps every byte for open_path later
        return path.decode("latin-1", errors="ignore")
    return str(path)


def _path_to_bytes(path: Any) -> bytes:
    if isinstance(path, bytes):
        return path
    return str(path or "").encode("latin-1", errors="ignore")


def _hid_enumerate_raw(timeout_sec: float = 2.5) -> list[Any]:
    """Enumerate Patlite VID devices with a hard timeout (Windows HID can stall)."""
    global _last_error
    box: dict[str, Any] = {"rows": [], "error": ""}

    def worker() -> None:
        try:
            hid = _hid()
            found: list[Any] = []
            # Prefer filtered enumerate — full-bus scan is slow and can hang.
            try:
                found.extend(list(hid.enumerate(VENDOR_ID, PRODUCT_ID) or []))
            except Exception:
                found = []
            if not found:
                try:
                    found.extend(list(hid.enumerate(VENDOR_ID, 0) or []))
                except Exception:
                    pass
            if not found:
                for info in hid.enumerate() or []:
                    if int(info.get("vendor_id") or 0) == VENDOR_ID:
                        found.append(info)
            box["rows"] = found
        except Exception as exc:  # noqa: BLE001
            box["error"] = str(exc)

    thread = threading.Thread(target=worker, name="patlite-hid-enum", daemon=True)
    thread.start()
    thread.join(max(0.5, float(timeout_sec)))
    if thread.is_alive():
        _last_error = "USB HID 검색 시간 초과 — USB 재연결 후 다시 검색하세요."
        return []
    if box.get("error"):
        _last_error = str(box["error"])
        return []
    return list(box.get("rows") or [])


def list_devices(*, use_cache: bool = False) -> list[dict[str, Any]]:
    """List Patlite LR6-USB HID devices (and mark preferred)."""
    global _devices_cache, _devices_cache_at, _last_error
    now = time.monotonic()
    if use_cache and _devices_cache and (now - _devices_cache_at) < _DEVICES_TTL:
        with _lock:
            preferred = _device_path
        out = []
        for row in _devices_cache:
            item = dict(row)
            item["preferred"] = item.get("path") == preferred
            out.append(item)
        return out
    rows: list[dict[str, Any]] = []
    try:
        seen: set[str] = set()
        preferred = ""
        with _lock:
            preferred = _device_path
        for info in _hid_enumerate_raw():
            vid = int(info.get("vendor_id") or 0)
            pid = int(info.get("product_id") or 0)
            if vid != VENDOR_ID:
                continue
            path = _path_to_str(info.get("path"))
            if not path or path in seen:
                continue
            seen.add(path)
            rows.append(
                {
                    "path": path,
                    "vendor_id": f"0x{vid:04X}",
                    "product_id": f"0x{pid:04X}",
                    "manufacturer": str(info.get("manufacturer_string") or ""),
                    "product": str(info.get("product_string") or "Signal Tower USB"),
                    "serial": str(info.get("serial_number") or ""),
                    "preferred": path == preferred,
                    "patlite": pid == PRODUCT_ID,
                }
            )
        rows.sort(key=lambda item: (0 if item.get("patlite") else 1, item.get("product") or ""))
        _devices_cache = [dict(row) for row in rows]
        _devices_cache_at = now
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
    return rows


def select_device(path: str) -> dict[str, Any]:
    """Remember which HID path to open for writes."""
    global _device_path, _connected_cache, _connected_at
    wanted = str(path or "").strip()
    devices = list_devices()
    match = next((row for row in devices if row.get("path") == wanted), None)
    if wanted and not match:
        return {"ok": False, "error": "선택한 USB 장치를 찾지 못했습니다. 다시 검색하세요.", "devices": devices}
    with _lock:
        _device_path = wanted
        _connected_cache = False
        _connected_at = 0.0
    raw = _load_raw_cfg()
    raw["device_path"] = wanted
    raw.setdefault("profiles", load_profiles())
    raw["enables"] = _normalize_enables(raw.get("enables") if "enables" in raw else _enables)
    _save_raw_cfg(raw)
    ok = connected(force=True)
    return {
        "ok": bool(ok or not wanted),
        "connected": ok,
        "device_path": wanted,
        "devices": list_devices(),
        "error": "" if (ok or not wanted) else (_last_error or "장치에 연결하지 못했습니다."),
    }


# Back-compat aliases used by older server routes / imports
def load_sounds() -> dict[str, dict[str, int]]:
    return load_profiles()


def save_sounds(payload: dict[str, Any] | None) -> dict[str, dict[str, int]]:
    return save_profiles(payload)


def _profile_of(color: str) -> dict[str, int]:
    with _lock:
        if _profiles:
            return dict(_profiles.get(color) or _DEFAULT_PROFILES.get(color) or _DEFAULT_PROFILES["red"])
    load_profiles()
    with _lock:
        return dict(_profiles.get(color) or _DEFAULT_PROFILES.get(color) or _DEFAULT_PROFILES["red"])


def _auto_pick_device(devices: list[dict[str, Any]]) -> str:
    match = next((row for row in devices if row.get("patlite")), None)
    if not match and devices:
        match = devices[0]
    return str((match or {}).get("path") or "")


def connected(force: bool = False) -> bool:
    global _connected_cache, _connected_at, _last_error, _device_path
    now = time.monotonic()
    if not force and (now - _connected_at) < _CONNECTED_TTL:
        return _connected_cache
    found = False
    try:
        devices = list_devices()
        if _device_path:
            found = any(row.get("path") == _device_path for row in devices)
            if found:
                _last_error = ""
            else:
                # USB 포트/허브가 바뀌면 path 가 달라짐 → 같은 Patlite 로 자동 재선택
                picked = _auto_pick_device(devices)
                if picked:
                    _device_path = picked
                    found = True
                    _last_error = ""
                    _persist_device_path(_device_path)
                else:
                    _last_error = "선택한 USB 장치가 없습니다. 목록에서 다시 선택하세요."
        else:
            # Auto-pick first Patlite LR6-USB if nothing selected yet.
            picked = _auto_pick_device(devices)
            if picked:
                _device_path = picked
                found = True
                _last_error = ""
                _persist_device_path(_device_path)
            else:
                found = False
                if not _last_error:
                    _last_error = "Patlite LR6-USB(HID)를 찾지 못했습니다."
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        found = False
    _connected_cache = found
    _connected_at = now
    return found


def _persist_device_path(path: str) -> None:
    """Remember auto-detected USB path so reconnect needs no button."""
    wanted = str(path or "").strip()
    if not wanted:
        return
    try:
        raw = _load_raw_cfg()
        if str(raw.get("device_path") or "") == wanted:
            return
        raw["device_path"] = wanted
        if "profiles" not in raw:
            raw["profiles"] = {k: dict(v) for k, v in _DEFAULT_PROFILES.items()}
        # Never drop per-color mute flags when remembering the USB path.
        raw["enables"] = _normalize_enables(raw.get("enables") if "enables" in raw else _enables)
        _save_raw_cfg(raw)
    except Exception:
        pass


def _yellow_still_active() -> bool:
    global _yellow_active
    if not _yellow_active:
        return False
    if time.monotonic() > float(_yellow_until or 0.0):
        _yellow_active = False
        return False
    return True


def status(force: bool = False) -> dict[str, Any]:
    profiles = load_profiles()
    enables = load_enables()
    is_connected = connected(force=force)
    # connected() already enumerated — reuse cache to avoid a second HID scan.
    devices = list_devices(use_cache=True)
    with _lock:
        mode = _mode
        alarm = _alarm_active
        ops = _ops_active
        path = _device_path
        err = _last_error
    return {
        "ok": True,
        "connected": is_connected,
        "mode": mode,
        "alarm": alarm,
        "ops": ops,
        "yellow": _yellow_still_active(),
        "model": "LR6-USB (RYG)",
        "vendor_id": f"0x{VENDOR_ID:04X}",
        "product_id": f"0x{PRODUCT_ID:04X}",
        "device_path": path,
        "devices": devices,
        "bus": "USB-HID",
        "error": err,
        "profiles": profiles,
        "enables": enables,
        "sounds": profiles,  # back-compat for older UI
        "pattern_choices": PATTERN_CHOICES,
        "pitch_choices": PITCH_CHOICES,
        "led_choices": LED_CHOICES,
        "limit_min": 0,
        "limit_max": 15,
        "volume": {
            "software": False,
            "note": "음량은 경광등 본체 옆 High/Low 스위치로만 조절됩니다. LR6-USB는 COM 포트가 아니라 USB-HID입니다.",
        },
    }


def _pack(
    buz_state: int,
    limit: int,
    pitch_a: int,
    pitch_b: int,
    red: int,
    yellow: int,
    green: int,
    blue: int = LED_OFF,
    white: int = LED_OFF,
) -> bytes:
    return struct.pack(
        "BBBBBBBx",
        0x00,
        0x00,
        ((limit & 0xF) << 4) | (buz_state & 0xF),
        ((pitch_a & 0xF) << 4) | (pitch_b & 0xF),
        ((red & 0xF) << 4) | (yellow & 0xF),
        ((green & 0xF) << 4) | (blue & 0xF),
        ((white & 0xF) << 4),
    )


def _connected_cache_invalidate() -> None:
    global _connected_at
    _connected_at = 0.0


def _write_once(payload: bytes) -> bool:
    """Send one HID report. Caller must hold `_io_lock`."""
    global _last_error, _connected_cache, _connected_at
    box: dict[str, Any] = {"ok": False, "error": ""}

    def worker() -> None:
        try:
            hid = _hid()
            preferred = ""
            with _lock:
                preferred = str(_device_path or "")
            path_bytes: bytes | None = _path_to_bytes(preferred) if preferred else None
            if not path_bytes:
                for info in _hid_enumerate_raw(timeout_sec=1.2):
                    vid = int(info.get("vendor_id") or 0)
                    pid = int(info.get("product_id") or 0)
                    if vid == VENDOR_ID and pid == PRODUCT_ID:
                        raw_path = info.get("path")
                        path_bytes = raw_path if isinstance(raw_path, (bytes, bytearray)) else _path_to_bytes(raw_path)
                        break
            if not path_bytes:
                box["error"] = _last_error or "Patlite LR6-USB(HID)를 찾지 못했습니다."
                return
            dev = hid.device()
            dev.open_path(path_bytes)
            try:
                written = dev.write(bytes([0x00]) + payload)
                if written < 1:
                    box["error"] = "HID write returned 0"
                    return
                box["ok"] = True
            finally:
                try:
                    dev.close()
                except Exception:
                    pass
        except Exception as exc:  # noqa: BLE001
            box["error"] = str(exc)

    thread = threading.Thread(target=worker, name="patlite-hid-write", daemon=True)
    thread.start()
    thread.join(2.5)
    if thread.is_alive():
        _last_error = "USB HID 응답 시간 초과"
        _connected_cache_invalidate()
        return False
    if box.get("ok"):
        _last_error = ""
        _connected_cache = True
        _connected_at = time.monotonic()
        return True
    _last_error = str(box.get("error") or "HID write failed")
    _connected_cache_invalidate()
    return False


def _write(payload: bytes, *, retries: int = 3) -> bool:
    """Serialize HID writes and retry — overlapping open/write was dropping alarms."""
    attempts = max(1, int(retries))
    with _io_lock:
        for attempt in range(attempts):
            if _write_once(payload):
                return True
            if attempt + 1 < attempts:
                time.sleep(0.08 * (attempt + 1))
    return False


def _emit(profile: dict[str, int], *, color: str | None = None, light: bool = True) -> bool:
    pattern = int(profile.get("pattern") or 0)
    limit = int(profile.get("limit") or 0)
    pitch_a = int(profile.get("pitch_a") if profile.get("pitch_a") is not None else PITCH_DFLT_A)
    pitch_b = int(profile.get("pitch_b") if profile.get("pitch_b") is not None else PITCH_DFLT_B)
    led = _clip_int(profile.get("led"), 1, 5, LED_PATTERN2)
    if pattern <= 0:
        pattern = BUZZER_OFF
        limit = 0
        pitch_a = PITCH_OFF
        pitch_b = PITCH_OFF
    # 0x1=점등, 0x2~0x5=점멸 패턴 (매뉴얼 LED pattern 1~4)
    red = led if light and color == "red" else LED_OFF
    yellow = led if light and color == "yellow" else LED_OFF
    green = led if light and color == "green" else LED_OFF
    return _write(_pack(pattern, limit, pitch_a, pitch_b, red, yellow, green))


def _apply_color(color: str, *, beep: bool = True) -> bool:
    profile = _profile_of(color)
    if not beep:
        profile = dict(profile)
        profile["pattern"] = BUZZER_OFF
        profile["limit"] = 0
        profile["pitch_a"] = PITCH_OFF
        profile["pitch_b"] = PITCH_OFF
    return _emit(profile, color=color, light=True)


def _apply_alarm() -> bool:
    return _apply_color("red", beep=True)


def _apply_ops(beep: bool = True) -> bool:
    return _apply_color("green", beep=beep)


def _apply_off() -> bool:
    return _write(_pack(BUZZER_OFF, 0, PITCH_OFF, PITCH_OFF, LED_OFF, LED_OFF, LED_OFF))


def _refresh(beep_ops: bool = False, beep_yellow: bool = False) -> bool:
    """Priority: red(alarm) > green(ops) > yellow(person) > off.

    Per-color 알림끄기 mutes that tier on the tower only (OMS popup still works).
    """
    global _mode
    if _alarm_active and color_enabled("red"):
        _mode = _MODE_ALARM
        return _apply_alarm()
    if _ops_active and color_enabled("green"):
        _mode = _MODE_OPS
        return _apply_ops(beep=beep_ops)
    if _yellow_still_active() and color_enabled("yellow"):
        _mode = _MODE_YELLOW
        return _apply_color("yellow", beep=beep_yellow)
    _mode = _MODE_OFF
    return _apply_off()


def set_alarm(active: bool = True) -> bool:
    global _alarm_active
    with _lock:
        _alarm_active = bool(active)
    # HID I/O must not run while holding _lock (status API would freeze).
    return _refresh(beep_ops=False, beep_yellow=False)


def set_ops(active: bool = True) -> bool:
    global _ops_active
    with _lock:
        was = _ops_active
        _ops_active = bool(active)
        beep = bool(active) and not was and not _alarm_active
    return _refresh(beep_ops=beep, beep_yellow=False)


def set_yellow(active: bool = True, hold_sec: float | None = None) -> bool:
    """CCTV person detect (표 4): yellow + alert tone."""
    global _yellow_active, _yellow_until
    with _lock:
        entering = bool(active) and not _yellow_still_active()
        _yellow_active = bool(active)
        hold = 0.0
        if active:
            hold = float(_YELLOW_HOLD_SEC if hold_sec is None else hold_sec)
            _yellow_until = time.monotonic() + max(5.0, hold)
        else:
            _yellow_until = 0.0
    ok = _refresh(beep_ops=False, beep_yellow=entering)
    if active and hold > 0:
        def _expire() -> None:
            time.sleep(max(5.0, hold) + 0.2)
            if _yellow_still_active():
                return
            _refresh(beep_ops=False, beep_yellow=False)

        threading.Thread(target=_expire, name="oms-patlite-yellow", daemon=True).start()
    return ok


def reset() -> bool:
    global _alarm_active, _ops_active, _yellow_active, _yellow_until, _mode
    with _lock:
        _alarm_active = False
        _ops_active = False
        _yellow_active = False
        _yellow_until = 0.0
        _mode = _MODE_OFF
    return _apply_off()


def sync_from_oms(*, alarm_pending: bool, ops_active: bool, yellow_active: bool | None = None) -> bool:
    global _alarm_active, _ops_active, _yellow_active, _yellow_until
    with _lock:
        entering_ops = bool(ops_active) and not _ops_active and not bool(alarm_pending)
        entering_yellow = False
        _alarm_active = bool(alarm_pending)
        _ops_active = bool(ops_active)
        if yellow_active is not None:
            entering_yellow = bool(yellow_active) and not _yellow_still_active() and not _alarm_active and not _ops_active
            _yellow_active = bool(yellow_active)
            if _yellow_active and _yellow_until <= time.monotonic():
                _yellow_until = time.monotonic() + _YELLOW_HOLD_SEC
            if not _yellow_active:
                _yellow_until = 0.0
    return _refresh(beep_ops=entering_ops, beep_yellow=entering_yellow)


def preview(kind: str, sound_id: str | None = None, profile: dict[str, Any] | None = None) -> bool:
    """Manual test: color / legacy preset / custom profile / off."""
    global _mode, _alarm_active, _ops_active, _yellow_active, _yellow_until
    key = str(kind or "").strip().lower()
    custom = _normalize_profile(profile, _DEFAULT_PROFILES["red"]) if isinstance(profile, dict) else None
    if sound_id and sound_id in _LEGACY_PRESETS and custom is None:
        custom = dict(_LEGACY_PRESETS[sound_id])

    if key in ("off", "reset", "clear", "stop"):
        with _lock:
            _alarm_active = False
            _ops_active = False
            _yellow_active = False
            _yellow_until = 0.0
            _mode = _MODE_OFF
        return _apply_off()

    if key in ("red", "alarm", "siren"):
        with _lock:
            _mode = _MODE_ALARM
        return _emit(custom or _profile_of("red"), color="red", light=True)
    if key in ("yellow", "amber"):
        with _lock:
            _mode = _MODE_YELLOW
        return _emit(custom or _profile_of("yellow"), color="yellow", light=True)
    if key in ("green", "ops", "alert"):
        with _lock:
            _mode = _MODE_OPS
        return _emit(custom or _profile_of("green"), color="green", light=True)
    if key in ("custom", "lab", "buzzer"):
        with _lock:
            _mode = "preview"
        return _emit(custom or _DEFAULT_PROFILES["red"], color=None, light=False)
    if key in _LEGACY_PRESETS:
        with _lock:
            _mode = "preview"
        return _emit(_LEGACY_PRESETS[key], color=None, light=False)
    if custom is not None:
        with _lock:
            _mode = "preview"
        return _emit(custom, color=None, light=False)
    return False


def _bg(fn, *args, **kwargs) -> None:
    threading.Thread(target=lambda: fn(*args, **kwargs), name="oms-patlite", daemon=True).start()


def notify_alarm() -> None:
    _bg(set_alarm, True)


def notify_ops_begin() -> None:
    _bg(set_ops, True)


def notify_ops_end() -> None:
    _bg(set_ops, False)


def notify_person() -> None:
    """표 4: CCTV 사람 인지 → 황색 + 알림."""
    _bg(set_yellow, True)


def notify_sync(*, alarm_pending: bool, ops_active: bool, yellow_active: bool | None = None) -> None:
    _bg(sync_from_oms, alarm_pending=alarm_pending, ops_active=ops_active, yellow_active=yellow_active)


try:
    load_profiles()
except Exception:
    pass
