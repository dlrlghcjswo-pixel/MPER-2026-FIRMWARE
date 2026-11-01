"""Andea RL17W / RL1700 HF RFID reader integration for OMS (관제 PC).

Uses AE SDK (rfidlib_reader.dll) when available. Connection defaults match the
RL17W / R-Tool: COM · 38400 · Frame 8E1 · BusAddr 255.

While connected, a background inventory loop waits for stickers (no button).
Registered tags → live log; fingerprint bay ↔ RFID gun cross-check can raise
emergency alarm on mismatch.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import winreg
from pathlib import Path
from typing import Any

_DEFAULTS: dict[str, Any] = {
    # AE SDK 패키지에 RL1700 전용 드라이버가 없으면 RL8000 계열이 동작하는 경우가 많음.
    "rd_type": "RL8000",
    "comm_type": "COM",  # COM | USB
    "com_name": "",
    "baud_rate": "38400",
    "frame": "8E1",
    "bus_addr": "255",
    "usb_addr_mode": "0",
    "usb_serial": "",
    "cross_check": True,
    "mismatch_alarm": True,
    "beep_on_read": True,
    "auto_connect": True,
}

_BAUD_CHOICES = [
    {"id": "9600", "label": "9600"},
    {"id": "19200", "label": "19200"},
    {"id": "38400", "label": "38400 (기본)"},
    {"id": "57600", "label": "57600"},
    {"id": "115200", "label": "115200"},
    {"id": "230400", "label": "230400"},
]

_FRAME_CHOICES = [
    {"id": "8E1", "label": "8E1 (기본 · R-Tool 영상)"},
    {"id": "8N1", "label": "8N1"},
    {"id": "8O1", "label": "8O1"},
]

_COMM_CHOICES = [
    {"id": "COM", "label": "COM / RS232 (SYSTEMBASE 등)"},
    {"id": "USB", "label": "USB-HID"},
]

_lock = threading.RLock()
_cfg: dict[str, Any] = dict(_DEFAULTS)
_last_error = ""
_connected = False
_handle = ctypes.c_void_p(0)
_dll: Any = None
_aip_iso15693: Any = None
_drivers_loaded = False
_driver_names: list[str] = []
_last_tags: list[dict[str, Any]] = []
_sdk_root: Path | None = None
_watch_stop = threading.Event()
_watch_thread: threading.Thread | None = None
_watch_started = False
# Faster polls catch stickers that only briefly enter the antenna field.
_WATCH_INTERVAL_SEC = 0.28
_INVEN_PASSES = 3  # soft retries inside one inventory() call
# Rising-edge only: fire once when a UID appears; clear after N empty polls so
# the same sticker can tag again after it leaves the antenna.
# One empty inventory (~0.28s) is enough to allow the same sticker to re-tag.
_WATCH_ABSENT_CLEAR_POLLS = 1
_watch_visible: set[str] = set()
_watch_absent_streak: dict[str, int] = {}

_AI_TYPE_NEW = 1
_AI_TYPE_CONTINUE = 2
_RFID_SEEK_FIRST = 1
_RFID_SEEK_NEXT = 2
_INVEN_STOP_TRIGGER_TIMEOUT = 3


def _oms_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _repo_rfid_roots() -> list[Path]:
    """Locate AE SDK / R-Tool folders shipped beside the firmware tree."""
    roots: list[Path] = []
    oms = _oms_root()
    candidates = [
        oms / "rfid_runtime",
        oms / "_rfid",
        oms.parent.parent / "RFID" / "AE SDK 20260121" / "Samples" / "rfid.sdk.windows" / "rfid_reader_HF-高频读卡器" / "run" / "win64" / "unicode_debug",
        oms.parent.parent / "RFID" / "AE SDK 20260121" / "Samples" / "rfid.sdk.windows" / "rfid_reader_HF-高频读卡器" / "run" / "win64" / "debug",
        oms.parent.parent / "RFID" / "r_tool_v2.2.5.20" / "r_tool_release-main",
    ]
    for path in candidates:
        try:
            if path.is_dir():
                roots.append(path.resolve())
        except OSError:
            continue
    return roots


def _find_sdk() -> Path | None:
    global _sdk_root
    if _sdk_root and (_sdk_root / "rfidlib_reader.dll").is_file():
        return _sdk_root
    # Prefer local ASCII rfid_runtime first (CJK paths break some DLL loaders).
    candidates: list[Path] = [_oms_root() / "rfid_runtime"]
    # PyInstaller one-file: packed datas land under _MEIPASS.
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "rfid_runtime")
    for preferred in candidates:
        if (preferred / "rfidlib_reader.dll").is_file():
            _sdk_root = preferred.resolve()
            return _sdk_root
    for root in _repo_rfid_roots():
        if (root / "rfidlib_reader.dll").is_file():
            _sdk_root = root
            return root
    return None


def _legacy_cfg_path() -> Path:
    return _oms_root() / "rfid.json"


def _load_raw_cfg() -> dict[str, Any]:
    from oms_bundle import SECTION_RFID, migrate_section_from_file, read_section

    try:
        raw = read_section(SECTION_RFID)
        if isinstance(raw, dict):
            return raw
        migrated = migrate_section_from_file(SECTION_RFID, _legacy_cfg_path())
        if isinstance(migrated, dict):
            return migrated
    except Exception as exc:  # noqa: BLE001
        global _last_error
        _last_error = f"rfid config: {exc}"
    return {}


def _save_raw_cfg(data: dict[str, Any]) -> None:
    from oms_bundle import SECTION_RFID, write_section

    write_section(SECTION_RFID, data)


def _normalize_cfg(raw: Any) -> dict[str, Any]:
    base = dict(_DEFAULTS)
    if not isinstance(raw, dict):
        return base
    out = dict(base)
    out["rd_type"] = str(raw.get("rd_type") or base["rd_type"]).strip() or base["rd_type"]
    comm = str(raw.get("comm_type") or base["comm_type"]).strip().upper()
    out["comm_type"] = comm if comm in ("COM", "USB") else "COM"
    out["com_name"] = str(raw.get("com_name") or "").strip()
    baud = str(raw.get("baud_rate") or base["baud_rate"]).strip()
    out["baud_rate"] = baud if baud in {c["id"] for c in _BAUD_CHOICES} else base["baud_rate"]
    frame = str(raw.get("frame") or base["frame"]).strip().upper()
    # R-Tool 사용법 영상 기준 RL17W 기본은 8E1. 예전 잘못된 8N1 저장값 보정.
    if frame == "8N1":
        frame = "8E1"
    out["frame"] = frame if frame in {c["id"] for c in _FRAME_CHOICES} else base["frame"]
    out["bus_addr"] = str(raw.get("bus_addr") or base["bus_addr"]).strip() or "255"
    out["usb_addr_mode"] = str(raw.get("usb_addr_mode") or "0").strip() or "0"
    out["usb_serial"] = str(raw.get("usb_serial") or "").strip()
    out["cross_check"] = bool(raw.get("cross_check", True))
    out["mismatch_alarm"] = bool(raw.get("mismatch_alarm", True))
    out["beep_on_read"] = bool(raw.get("beep_on_read", True))
    out["auto_connect"] = bool(raw.get("auto_connect", True))
    return out


def load_config() -> dict[str, Any]:
    global _cfg
    with _lock:
        _cfg = _normalize_cfg(_load_raw_cfg())
        return dict(_cfg)


def save_config(payload: dict[str, Any] | None) -> dict[str, Any]:
    global _cfg
    current = load_config()
    merged = dict(current)
    if isinstance(payload, dict):
        # UI / callers may send com_port instead of com_name.
        if payload.get("com_port") and not payload.get("com_name"):
            payload = dict(payload)
            payload["com_name"] = payload.get("com_port")
        merged.update(payload)
    cleaned = _normalize_cfg(merged)
    with _lock:
        _cfg = cleaned
    _save_raw_cfg(cleaned)
    return dict(cleaned)


def _list_com_ports_winreg() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
    except OSError:
        return rows
    try:
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            port = str(value or "").strip()
            if port:
                rows.append({"id": port, "label": port, "path": str(name or ""), "kind": "COM"})
            i += 1
    finally:
        winreg.CloseKey(key)
    rows.sort(key=lambda item: item["id"])
    return rows


def _ensure_dll() -> Any:
    global _dll, _last_error, _drivers_loaded, _driver_names, _sdk_root
    if _dll is not None:
        return _dll
    root = _find_sdk()
    if not root:
        _last_error = (
            "RFID SDK(rfidlib_reader.dll)를 찾지 못했습니다. "
            "oms/rfid_runtime 폴더에 AE SDK win64 DLL을 넣으세요."
        )
        raise FileNotFoundError(_last_error)
    # Prefer ASCII-path runtime so Windows MBCS paths with CJK never break load.
    try:
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(root))
            drivers = root / "Drivers"
            if drivers.is_dir():
                os.add_dll_directory(str(drivers))
        os.environ["PATH"] = (
            str(root) + os.pathsep + str(root / "Drivers") + os.pathsep + os.environ.get("PATH", "")
        )
    except Exception:
        pass
    dll_path = root / "rfidlib_reader.dll"
    try:
        _dll = ctypes.WinDLL(str(dll_path))
    except OSError as exc:
        _last_error = f"rfidlib_reader.dll 로드 실패: {exc}"
        raise
    # unicode_debug build of AE SDK (CharSet.Unicode)
    _dll.RDR_LoadReaderDrivers.argtypes = [ctypes.c_wchar_p]
    _dll.RDR_LoadReaderDrivers.restype = ctypes.c_int
    _dll.RDR_GetLoadedReaderDriverCount.argtypes = []
    _dll.RDR_GetLoadedReaderDriverCount.restype = ctypes.c_uint32
    _dll.RDR_GetLoadedReaderDriverOpt.argtypes = [
        ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)
    ]
    _dll.RDR_GetLoadedReaderDriverOpt.restype = ctypes.c_int
    _dll.COMPort_Enum.argtypes = []
    _dll.COMPort_Enum.restype = ctypes.c_uint32
    _dll.COMPort_GetEnumItem.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_uint32]
    _dll.COMPort_GetEnumItem.restype = ctypes.c_int
    _dll.HID_Enum.argtypes = [ctypes.c_wchar_p]
    _dll.HID_Enum.restype = ctypes.c_uint32
    _dll.HID_GetEnumItem.argtypes = [
        ctypes.c_uint32, ctypes.c_byte, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)
    ]
    _dll.HID_GetEnumItem.restype = ctypes.c_int
    _dll.RDR_Open.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
    _dll.RDR_Open.restype = ctypes.c_int
    _dll.RDR_Close.argtypes = [ctypes.c_void_p]
    _dll.RDR_Close.restype = ctypes.c_int
    _dll.RDR_CreateInvenParamSpecList.argtypes = []
    _dll.RDR_CreateInvenParamSpecList.restype = ctypes.c_void_p
    _dll.RDR_TagInventory.argtypes = [
        ctypes.c_void_p, ctypes.c_byte, ctypes.c_byte, ctypes.c_void_p, ctypes.c_void_p
    ]
    _dll.RDR_TagInventory.restype = ctypes.c_int
    try:
        _dll.RDR_SetInvenStopTrigger.argtypes = [
            ctypes.c_void_p, ctypes.c_byte, ctypes.c_uint32, ctypes.c_uint32
        ]
        _dll.RDR_SetInvenStopTrigger.restype = ctypes.c_int
    except AttributeError:
        pass
    try:
        _dll.RDR_ResetRF.argtypes = [ctypes.c_void_p]
        _dll.RDR_ResetRF.restype = ctypes.c_int
    except AttributeError:
        pass
    try:
        _dll.RDR_OpenRFTransmitter.argtypes = [ctypes.c_void_p]
        _dll.RDR_OpenRFTransmitter.restype = ctypes.c_int
    except AttributeError:
        pass
    try:
        _dll.RDR_DeviceDebugBeepTest.argtypes = [ctypes.c_void_p, ctypes.c_byte]
        _dll.RDR_DeviceDebugBeepTest.restype = ctypes.c_int
    except AttributeError:
        pass
    _dll.RDR_GetTagDataReportCount.argtypes = [ctypes.c_void_p]
    _dll.RDR_GetTagDataReportCount.restype = ctypes.c_uint32
    _dll.RDR_GetTagDataReport.argtypes = [ctypes.c_void_p, ctypes.c_byte]
    _dll.RDR_GetTagDataReport.restype = ctypes.c_void_p
    _dll.RDR_ParseTagDataReportRaw.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32)
    ]
    _dll.RDR_ParseTagDataReportRaw.restype = ctypes.c_int
    _dll.DNODE_Destroy.argtypes = [ctypes.c_void_p]
    _dll.DNODE_Destroy.restype = ctypes.c_int

    # ISO15693 AIP helper (same folder as rfidlib_reader.dll).
    global _aip_iso15693
    aip_path = root / "rfidlib_aip_iso15693.dll"
    if aip_path.is_file():
        try:
            _aip_iso15693 = ctypes.WinDLL(str(aip_path))
            _aip_iso15693.ISO15693_CreateInvenParam.argtypes = [
                ctypes.c_void_p, ctypes.c_byte, ctypes.c_byte, ctypes.c_byte, ctypes.c_byte
            ]
            _aip_iso15693.ISO15693_CreateInvenParam.restype = ctypes.c_void_p
            _aip_iso15693.ISO15693_ParseTagDataReport.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_byte),
                ctypes.c_char_p,
            ]
            _aip_iso15693.ISO15693_ParseTagDataReport.restype = ctypes.c_int
        except OSError:
            _aip_iso15693 = None

    if not _drivers_loaded:
        load_paths: list[str] = []
        drv = root / "Drivers"
        if drv.is_dir():
            load_paths.append(str(drv))
            load_paths.append("Drivers")
        for extra in _repo_rfid_roots():
            extra_drv = extra / "Drivers"
            if extra_drv.is_dir() and str(extra_drv) not in load_paths:
                # Skip paths with non-ASCII that break some loaders; absolute ASCII ok
                try:
                    str(extra_drv).encode("ascii")
                    load_paths.append(str(extra_drv))
                except UnicodeEncodeError:
                    continue
        cwd0 = os.getcwd()
        try:
            os.chdir(str(root))
            for path in load_paths:
                try:
                    _dll.RDR_LoadReaderDrivers(path)
                    if int(_dll.RDR_GetLoadedReaderDriverCount()) > 0:
                        break
                except Exception:
                    continue
        finally:
            try:
                os.chdir(cwd0)
            except Exception:
                pass
        names: list[str] = []
        try:
            count = int(_dll.RDR_GetLoadedReaderDriverCount())
            for idx in range(count):
                buf = ctypes.create_unicode_buffer(128)
                nsize = ctypes.c_uint32(128)
                iret = _dll.RDR_GetLoadedReaderDriverOpt(
                    idx, "NAME", buf, ctypes.byref(nsize)
                )
                if iret == 0:
                    name = (buf.value or "").strip()
                    if name:
                        names.append(name)
        except Exception as exc:  # noqa: BLE001
            _last_error = f"드라이버 목록: {exc}"
        _driver_names = names
        _drivers_loaded = True
        _sdk_root = root
    return _dll


def _com_adapter_score(row: dict[str, str]) -> int:
    """Higher = better candidate for SYSTEMBASE / USB-RS232 RFID dongles."""
    blob = f"{row.get('id', '')} {row.get('path', '')} {row.get('label', '')}".upper()
    score = 0
    for token in (
        "SYSTEMBASE",
        "USB SERIAL",
        "USB-SERIAL",
        "USBSER",
        "VCP",
        "FTDI",
        "FT232",
        "CH340",
        "CH341",
        "PROLIFIC",
        "SILICON",
        "CP210",
        "SERIALCOMM",
    ):
        if token in blob:
            score += 10
    name = str(row.get("id") or "").upper()
    # Built-in motherboard UART is almost never the RFID adapter.
    if name == "COM1":
        score -= 50
    try:
        num = int(name.replace("COM", "") or "0")
        # Prefer higher COM numbers a bit (USB adapters often land there).
        if num >= 3:
            score += 1
    except ValueError:
        pass
    return score


def list_com_ports() -> list[dict[str, str]]:
    rows = _list_com_ports_winreg()
    try:
        dll = _ensure_dll()
        n = int(dll.COMPort_Enum())
        seen = {r["id"] for r in rows}
        for idx in range(n):
            buf = ctypes.create_unicode_buffer(64)
            if dll.COMPort_GetEnumItem(idx, buf, 64) == 0:
                name = (buf.value or "").strip()
                if name and name not in seen:
                    rows.append({"id": name, "label": name, "path": "", "kind": "COM"})
                    seen.add(name)
    except Exception:
        pass
    # Prefer USB serial adapters; keep stable order among equals.
    rows.sort(key=lambda item: (-_com_adapter_score(item), item["id"]))
    return rows


def _present_com_ids() -> set[str]:
    return {str(row.get("id") or "").strip().upper() for row in list_com_ports() if row.get("id")}


def _com_port_candidates(saved: str) -> list[str]:
    """Build open order: saved port (if still present) then auto-discovered adapters."""
    ports = list_com_ports()
    present = {str(row.get("id") or "").strip() for row in ports if row.get("id")}
    present_u = {p.upper() for p in present}
    ordered: list[str] = []
    saved_name = str(saved or "").strip()
    if saved_name and saved_name.upper() in present_u:
        # Keep exact casing from the live list.
        for p in present:
            if p.upper() == saved_name.upper():
                ordered.append(p)
                break
    for row in ports:
        pid = str(row.get("id") or "").strip()
        if not pid:
            continue
        if pid in ordered:
            continue
        # Skip motherboard COM1 unless nothing else exists.
        if pid.upper() == "COM1" and len(present) > 1:
            continue
        ordered.append(pid)
    if not ordered and "COM1" in present_u:
        for p in present:
            if p.upper() == "COM1":
                ordered.append(p)
                break
    return ordered


def list_hid_devices(rd_type: str | None = None) -> list[dict[str, str]]:
    cfg = load_config()
    name = (rd_type or cfg.get("rd_type") or "RL1700").strip()
    rows: list[dict[str, str]] = []
    try:
        dll = _ensure_dll()
        n = int(dll.HID_Enum(name))
        for idx in range(n):
            sernum = ctypes.create_unicode_buffer(128)
            nsize = ctypes.c_uint32(128)
            iret = dll.HID_GetEnumItem(idx, 1, sernum, ctypes.byref(nsize))
            serial = (sernum.value or "").strip() if iret == 0 else ""
            rows.append({
                "id": serial or str(idx),
                "label": f"{name} · {serial or idx}",
                "serial": serial,
                "kind": "USB",
                "rd_type": name,
            })
    except Exception as exc:  # noqa: BLE001
        global _last_error
        _last_error = str(exc)
    return rows


def list_devices() -> dict[str, Any]:
    cfg = load_config()
    return {
        "com": list_com_ports(),
        "usb": list_hid_devices(str(cfg.get("rd_type") or "RL1700")),
        "drivers": list(_driver_names),
    }


def _conn_string(cfg: dict[str, Any]) -> str:
    rd = str(cfg.get("rd_type") or "RL1700")
    if str(cfg.get("comm_type") or "COM").upper() == "USB":
        return (
            f"RDType={rd};CommType=USB;"
            f"AddrMode={cfg.get('usb_addr_mode') or '0'};"
            f"SerNum={cfg.get('usb_serial') or ''}"
        )
    return (
        f"RDType={rd};CommType=COM;"
        f"COMName={cfg.get('com_name') or ''};"
        f"BaudRate={cfg.get('baud_rate') or '38400'};"
        f"Frame={cfg.get('frame') or '8E1'};"
        f"BusAddr={cfg.get('bus_addr') or '255'}"
    )


def disconnect() -> dict[str, Any]:
    global _connected, _handle, _last_error
    stop_watch()
    with _lock:
        h = _handle
        _handle = ctypes.c_void_p(0)
        _connected = False
    if h:
        try:
            dll = _ensure_dll()
            dll.RDR_Close(h)
        except Exception as exc:  # noqa: BLE001
            _last_error = str(exc)
    return status(force=True)


def connect(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Open reader with current/updated settings.

    COM number is PC-specific. Saved COM21 on another machine is ignored when
    that port is missing; all present adapters are probed (USB serial first).
    """
    global _connected, _handle, _last_error
    if isinstance(payload, dict) and payload:
        # Empty com_name from UI means "auto pick on this PC".
        save_config(payload)
    cfg = load_config()
    if str(cfg.get("comm_type") or "").upper() == "USB" and not cfg.get("usb_serial"):
        hid = list_hid_devices(str(cfg.get("rd_type") or "RL8000"))
        if hid:
            cfg = save_config({"usb_serial": hid[0].get("serial") or hid[0]["id"]})
    disconnect()
    try:
        dll = _ensure_dll()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc) or _last_error, **status(force=True)}

    # Prefer selected driver, then known HF reader types. Skip RPAN unless chosen.
    preferred = str(cfg.get("rd_type") or "RL8000").strip() or "RL8000"
    alts = [preferred]
    for name in ("RL8000", "RL1700", "RD242", "RD5200", "RD5100", "M201"):
        if name not in alts:
            alts.append(name)

    if str(cfg.get("comm_type") or "COM").upper() == "COM":
        port_candidates = _com_port_candidates(str(cfg.get("com_name") or ""))
        if not port_candidates:
            _last_error = (
                "COM 포트를 찾지 못했습니다. SYSTEMBASE USB-RS232를 꽂은 뒤 "
                "[다시 검색] → [연결] 하세요."
            )
            _connected = False
            return {"ok": False, "error": _last_error, **status(force=True)}
    else:
        port_candidates = [str(cfg.get("com_name") or "") or ""]

    # RL17W R-Tool default is 8E1; keep that first, then allow fallbacks.
    frame_primary = str(cfg.get("frame") or "8E1").upper()
    if frame_primary == "8N1":
        frame_primary = "8E1"
    frame_alts = [frame_primary]
    for fr in ("8E1", "8N1", "8O1"):
        if fr not in frame_alts:
            frame_alts.append(fr)

    opened = False
    hr = ctypes.c_void_p(0)
    conn = _conn_string(cfg)
    last_iret = -1
    tried_ports: list[str] = []
    for port in port_candidates:
        if not port:
            continue
        tried_ports.append(port)
        for name in alts:
            for frame in frame_alts:
                trial = dict(cfg, rd_type=name, com_name=port, frame=frame)
                conn = _conn_string(trial)
                hr = ctypes.c_void_p(0)
                iret = int(dll.RDR_Open(conn, ctypes.byref(hr)))
                last_iret = iret
                if iret == 0 and hr:
                    cfg = save_config({"rd_type": name, "com_name": port, "frame": frame})
                    opened = True
                    break
                # Failed open may still leave a handle; close defensively.
                if hr:
                    try:
                        dll.RDR_Close(hr)
                    except Exception:
                        pass
            if opened:
                break
        if opened:
            break
    if not opened:
        ports_txt = ", ".join(tried_ports) if tried_ports else "(없음)"
        _last_error = (
            f"RFID 연결 실패 (코드 {last_iret}, 시도 {ports_txt}). "
            "SYSTEMBASE COM · 점퍼 232 · 38400/8E1/255 · R-Tool과 COM 동시 사용 여부를 확인하세요."
        )
        _connected = False
        return {"ok": False, "error": _last_error, "conn": conn, **status(force=True)}
    with _lock:
        _handle = hr
        _connected = True
        _last_error = ""
    if hasattr(dll, "RDR_OpenRFTransmitter"):
        try:
            dll.RDR_OpenRFTransmitter(hr)
        except Exception:
            pass
    start_watch()
    return {"ok": True, "connected": True, "conn": _conn_string(cfg), **status(force=True)}


def connected(force: bool = False) -> bool:
    global _connected
    with _lock:
        if _connected and _handle:
            return True
    if not force:
        return False
    cfg = load_config()
    if not cfg.get("auto_connect"):
        return False
    # Soft check: selected COM still present
    if str(cfg.get("comm_type") or "COM").upper() == "COM":
        name = str(cfg.get("com_name") or "")
        if name and any(p["id"] == name for p in list_com_ports()):
            # Do not auto RDR_Open on every poll — only report port present if not opened.
            return bool(_connected)
    return bool(_connected)


def _collect_inventory_reports(dll: Any, h: Any, aip: Any) -> list[dict[str, Any]]:
    tags: list[dict[str, Any]] = []
    seen: set[str] = set()
    report = dll.RDR_GetTagDataReport(h, _RFID_SEEK_FIRST)
    while report:
        uid = ""
        if aip is not None:
            aip_id = ctypes.c_uint32(0)
            tag_id = ctypes.c_uint32(0)
            ant_id = ctypes.c_uint32(0)
            dsfid = ctypes.c_byte(0)
            uid_buf = ctypes.create_string_buffer(8)
            if int(aip.ISO15693_ParseTagDataReport(
                report,
                ctypes.byref(aip_id),
                ctypes.byref(tag_id),
                ctypes.byref(ant_id),
                ctypes.byref(dsfid),
                uid_buf,
            )) == 0:
                uid = uid_buf.raw[:8].hex().upper()
        if not uid:
            raw = ctypes.create_string_buffer(64)
            nsize = ctypes.c_uint32(64)
            if dll.RDR_ParseTagDataReportRaw(report, raw, ctypes.byref(nsize)) == 0:
                data = raw.raw[: int(nsize.value)]
                if len(data) >= 8:
                    uid = data[-8:].hex().upper()
                elif data:
                    uid = data.hex().upper()
        if uid and uid not in seen:
            seen.add(uid)
            tags.append({"uid": uid})
        report = dll.RDR_GetTagDataReport(h, _RFID_SEEK_NEXT)
    return tags


def _one_inventory_pass(
    dll: Any,
    h: Any,
    aip: Any,
    *,
    ai_type: int,
    slot_type: int,
) -> tuple[int, list[dict[str, Any]]]:
    """Single ISO15693 inventory; slot_type 1 = 1-slot (better for one sticker at gate)."""
    param_list = ctypes.c_void_p(0)
    try:
        param_list = dll.RDR_CreateInvenParamSpecList()
        if param_list and aip is not None:
            aip.ISO15693_CreateInvenParam(param_list, 0, 0, 0, int(slot_type) & 0xFF)
        if param_list and hasattr(dll, "RDR_SetInvenStopTrigger"):
            try:
                # Give the reader a bit more time to catch a weak/edge tag.
                dll.RDR_SetInvenStopTrigger(
                    param_list, _INVEN_STOP_TRIGGER_TIMEOUT, 350, 0
                )
            except Exception:
                pass
        iret = int(dll.RDR_TagInventory(h, int(ai_type) & 0xFF, 0, None, param_list or None))
        tags = _collect_inventory_reports(dll, h, aip)
        return iret, tags
    finally:
        if param_list:
            try:
                dll.DNODE_Destroy(param_list)
            except Exception:
                pass


def inventory(timeout_note: str = "") -> dict[str, Any]:
    """Run ISO15693 inventory with short soft-retries; returns UIDs."""
    global _last_tags, _last_error
    _ = timeout_note
    with _lock:
        h = _handle
        ok = _connected and bool(h)
    if not ok:
        return {"ok": False, "error": "RFID가 연결되어 있지 않습니다. 먼저 연결하세요.", "tags": []}
    try:
        dll = _ensure_dll()
        aip = _aip_iso15693
        # Gate use is almost always one sticker — 1-slot first, then default 0.
        # AI_TYPE_NEW resets RF; CONTINUE helps if the first pass was partial.
        pass_plan = [
            (_AI_TYPE_NEW, 1),
            (_AI_TYPE_CONTINUE, 1),
            (_AI_TYPE_NEW, 0),
        ][: max(1, int(_INVEN_PASSES))]
        tags: list[dict[str, Any]] = []
        iret = -1
        seen: set[str] = set()
        for idx, (ai_type, slot_type) in enumerate(pass_plan):
            if idx == 0 and hasattr(dll, "RDR_ResetRF"):
                try:
                    dll.RDR_ResetRF(h)
                except Exception:
                    pass
            iret, found = _one_inventory_pass(
                dll, h, aip, ai_type=ai_type, slot_type=slot_type
            )
            for row in found:
                uid = str(row.get("uid") or "")
                if uid and uid not in seen:
                    seen.add(uid)
                    tags.append(row)
            if tags:
                break
        soft_empty = iret in (0, -2, 2, -1)
        with _lock:
            _last_tags = tags
            if tags or iret == 0:
                _last_error = ""
            elif soft_empty:
                if _last_error.startswith("Inventory 코드"):
                    _last_error = ""
            else:
                _last_error = f"Inventory 코드 {iret}"
        if tags:
            if bool(load_config().get("beep_on_read", True)) and hasattr(
                dll, "RDR_DeviceDebugBeepTest"
            ):
                try:
                    dll.RDR_DeviceDebugBeepTest(h, 0)
                except Exception:
                    pass
            return {"ok": True, "tags": tags, "count": len(tags), "iret": iret}
        return {
            "ok": False,
            "error": (
                ""
                if soft_empty
                else f"Inventory 코드 {iret}"
            )
            or "태그를 읽지 못했습니다. 스티커를 리더 안테나에 가까이 대세요.",
            "tags": [],
            "iret": iret,
        }
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        return {"ok": False, "error": _last_error, "tags": []}


def status(force: bool = False) -> dict[str, Any]:
    cfg = load_config()
    # Always list COM ports even when SDK DLL is missing.
    com_ports = list_com_ports()
    try:
        usb_ports = list_hid_devices(str(cfg.get("rd_type") or "RL8000"))
    except Exception:
        usb_ports = []
    devices = {"com": com_ports, "usb": usb_ports, "drivers": list(_driver_names)}
    sdk = _find_sdk()
    err = ""
    is_conn = False
    handle_on = False
    tags: list[dict[str, Any]] = []
    try:
        is_conn = connected(force=force)
        with _lock:
            err = _last_error
            tags = list(_last_tags)
            handle_on = bool(_handle)
    except Exception as exc:  # noqa: BLE001
        err = str(exc) or "RFID 상태 확인 실패"
    if not sdk and not err:
        err = f"SDK 없음 — {_oms_root() / 'rfid_runtime'} 에 rfidlib_reader.dll 필요"
    return {
        "ok": True,
        "connected": bool(is_conn and handle_on),
        "model": "RL17W / RL1700 HF",
        "bus": "COM/RS232" if cfg.get("comm_type") == "COM" else "USB-HID",
        "sdk_loaded": bool(sdk),
        "sdk_path": str(sdk) if sdk else "",
        "error": err,
        "config": cfg,
        "devices": devices,
        "drivers": list(_driver_names),
        "last_tags": tags,
        "baud_choices": _BAUD_CHOICES,
        "frame_choices": _FRAME_CHOICES,
        "comm_choices": _COMM_CHOICES,
        "note": (
            "RL17W: SYSTEMBASE USB-RS232 · 점퍼 232 · DB9 2/3/5. "
            "COM 번호는 PC마다 자동. 연결되면 태깅 대기(버튼 없이 자동 인식). "
            "R-Tool/OMS 공통: 38400 · 8E1 · BusAddr 255. R-Tool과 OMS는 COM 동시 사용 금지."
        ),
        "watching": bool(_watch_started and not _watch_stop.is_set()),
    }


def ensure_connected() -> dict[str, Any]:
    """Return current connection, auto-opening when configured."""
    with _lock:
        if _connected and _handle:
            return {"ok": True, "connected": True}
    cfg = load_config()
    if not cfg.get("auto_connect", True):
        return {"ok": False, "connected": False, "error": "RFID가 연결되어 있지 않습니다."}
    return connect()


_watch_fail_streak = 0


def _reset_watch_presence() -> None:
    _watch_visible.clear()
    _watch_absent_streak.clear()


def _note_absent_uids(current: set[str]) -> None:
    """Forget UIDs that stayed off the antenna long enough to allow re-tag."""
    for uid in list(_watch_visible):
        if uid in current:
            continue
        streak = int(_watch_absent_streak.get(uid) or 0) + 1
        if streak >= int(_WATCH_ABSENT_CLEAR_POLLS):
            _watch_visible.discard(uid)
            _watch_absent_streak.pop(uid, None)
        else:
            _watch_absent_streak[uid] = streak


def _watch_loop() -> None:
    global _watch_fail_streak
    while not _watch_stop.wait(_WATCH_INTERVAL_SEC):
        with _lock:
            alive = bool(_connected and _handle)
        if not alive:
            # Another OMS instance may have stolen the COM port — try reclaim.
            _reset_watch_presence()
            try:
                ensure_connected()
            except Exception:
                pass
            continue
        try:
            result = inventory()
        except Exception:
            _watch_fail_streak += 1
            result = {}
        tags = result.get("tags") if isinstance(result, dict) else None
        iret = int(result.get("iret") or 0) if isinstance(result, dict) else 0
        if isinstance(tags, list) and tags:
            _watch_fail_streak = 0
            current: set[str] = set()
            for row in tags:
                uid = str((row or {}).get("uid") or "").strip()
                if uid:
                    current.add(uid)
            fresh = [uid for uid in current if uid not in _watch_visible]
            for uid in current:
                _watch_visible.add(uid)
                _watch_absent_streak.pop(uid, None)
            _note_absent_uids(current)
            if fresh:
                try:
                    from core import STORE

                    for uid in fresh:
                        STORE.handle_rfid_tag(uid)
                except Exception:
                    pass
            continue
        # Empty field — count toward clearing presence so the next tap re-fires.
        _note_absent_uids(set())
        # Hard inventory failures (not empty-field soft codes): reopen COM.
        if iret not in (0, -2, 2, -1) and isinstance(result, dict) and result.get("ok") is False:
            _watch_fail_streak += 1
        else:
            _watch_fail_streak = 0
        if _watch_fail_streak >= 5:
            _watch_fail_streak = 0
            _reset_watch_presence()
            try:
                disconnect()
            except Exception:
                pass
            try:
                connect()
            except Exception:
                pass


def start_watch() -> None:
    """Start continuous inventory while the reader stays connected."""
    global _watch_started, _watch_thread
    with _lock:
        if _watch_started:
            return
        if not (_connected and _handle):
            return
        _watch_started = True
        _watch_stop.clear()
        _reset_watch_presence()
        _watch_thread = threading.Thread(target=_watch_loop, name="oms-rfid-watch", daemon=True)
        _watch_thread.start()


def stop_watch() -> None:
    global _watch_started
    _watch_stop.set()
    with _lock:
        _watch_started = False
    _reset_watch_presence()


def boot_autofill() -> None:
    """Background: scan COM ports and auto-connect with saved 8E1 settings."""
    try:
        cfg = load_config()
        if not cfg.get("auto_connect", True):
            return
        # Normalize defaults used across PCs (COM number may change).
        patch: dict[str, Any] = {}
        if str(cfg.get("frame") or "") != "8E1":
            patch["frame"] = "8E1"
        if str(cfg.get("baud_rate") or "") != "38400":
            patch["baud_rate"] = "38400"
        if str(cfg.get("bus_addr") or "") != "255":
            patch["bus_addr"] = "255"
        # Drop stale COM from another PC so connect() re-probes locally.
        saved = str(cfg.get("com_name") or "").strip()
        if saved and saved.upper() not in _present_com_ids():
            patch["com_name"] = ""
        if patch:
            save_config(patch)
        try:
            connect()
        except Exception:
            pass
    except Exception:
        pass
