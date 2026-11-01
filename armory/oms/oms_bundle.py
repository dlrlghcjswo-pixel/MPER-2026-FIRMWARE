"""Single oms.json for OMS + CCTV + Patlite + RFID settings (portable copy).

Store / cctv / patlite / rfid all merge into the same file under a shared lock so
one section cannot wipe another on save.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any

_lock = threading.RLock()
SECTION_CCTV = "cctv"
SECTION_PATLITE = "patlite"
SECTION_RFID = "rfid"
_PRESERVE = (SECTION_CCTV, SECTION_PATLITE, SECTION_RFID)


def oms_json_path() -> Path:
    override = os.environ.get("OMS_DATA", "").strip()
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        beside = Path(sys.executable).resolve().parent / "oms.json"
    else:
        beside = Path(__file__).resolve().parent / "oms.json"
    legacy_armory = Path(os.environ.get("APPDATA", str(Path.home()))) / "Zellootec" / "ArmoryOMS" / "oms.json"
    legacy_locker = Path(os.environ.get("APPDATA", str(Path.home()))) / "Zellootec" / "RifleOMS" / "oms.json"
    if not beside.exists():
        if legacy_armory.is_file():
            return legacy_armory
        if legacy_locker.is_file():
            beside.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy_locker, beside)
    return beside


def _read_unlocked(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_unlocked(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_document() -> dict[str, Any]:
    with _lock:
        return _read_unlocked(oms_json_path())


def read_section(name: str) -> Any | None:
    with _lock:
        data = _read_unlocked(oms_json_path())
        return data.get(name)


def write_section(name: str, value: Any) -> None:
    """Merge one top-level section into oms.json without dropping others."""
    with _lock:
        path = oms_json_path()
        data = _read_unlocked(path)
        data[name] = value
        _write_unlocked(path, data)


def write_store_document(store_data: dict[str, Any]) -> Path:
    """Write OMS store fields while preserving cctv/patlite/rfid sections."""
    with _lock:
        path = oms_json_path()
        existing = _read_unlocked(path)
        out = dict(store_data)
        for key in _PRESERVE:
            if key in existing and key not in out:
                out[key] = existing[key]
        _write_unlocked(path, out)
        return path


def migrate_section_from_file(name: str, legacy: Path) -> Any | None:
    """If oms.json lacks section but legacy file exists, import once."""
    with _lock:
        path = oms_json_path()
        data = _read_unlocked(path)
        if name in data and data[name] is not None:
            return data[name]
        if not legacy.is_file():
            return None
        try:
            raw = json.loads(legacy.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return None
        data[name] = raw
        _write_unlocked(path, data)
        return raw
