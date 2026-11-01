"""OMS-embedded CCTV / NVR helpers.

Live view modes:
  - auto   : try ONVIF snapshot → JPEG path → HTTPS JPEG → RTSP(ffmpeg) probe
  - onvif  : ONVIF GetSnapshotUri / GetStreamUri
  - jpeg   : HTTP(S) JPEG snapshot path
  - rtsp   : RTSP URL (live still via snapshot if available; record via ffmpeg)

Retention: delete full-day segments and event clips after the configured days (default 1).
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import (
    HTTPBasicAuthHandler,
    HTTPDigestAuthHandler,
    HTTPPasswordMgrWithDefaultRealm,
    HTTPSHandler,
    Request,
    build_opener,
)

def _oms_root() -> Path:
    """Prefer folder beside the .exe so CCTV settings survive on other PCs."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = _oms_root()
DATA_DIR = ROOT / "data"
# CCTV settings live inside oms.json ("cctv" section). Legacy files still migrate once.
CFG_PATH = ROOT / "cctv.json"
LEGACY_CFG_PATH = DATA_DIR / "cctv.json"
REC_DIR = DATA_DIR / "cctv_recordings"
EVENT_DIR = REC_DIR / "events"
CONT_DIR = REC_DIR / "continuous"

_lock = threading.Lock()
_browser_lock = threading.Lock()
_ptz_guard = threading.Lock()
_aim_wall = 0.0
_home_wall = 0.0
_ptz_instance = uuid.uuid4().hex
_ptz_bus_started = False
_PTZ_BUS_PORT = 47321
_rec_procs: dict[str, subprocess.Popen] = {}
_cont_gen = 0
_cont_started = False
# Soft upper bound for one continuous file (rotate at hour boundary sooner).
# Not a "10-minute clip" UX — continuous full recording with hourly files.
_CONT_MAX_SEC = 3600
_last_purge_mono = 0.0
_last_event_mono = 0.0
_EVENT_COOLDOWN_SEC = 8.0
_onvif_cache: dict[str, dict[str, Any]] = {}
_ffmpeg_cached: str | None | bool = False  # False=unset, None=missing, str=path
# Cameras without HTTP snapshot (e.g. FlexWATCH FS-IR307A) need a persistent RTSP
# grabber; spawning ffmpeg per /api/cctv/snapshot (~3–4s) makes live view crawl.
_live_grabbers: dict[str, "_RtspLiveGrabber"] = {}
_jpeg_skip_until: dict[str, float] = {}
CHANNEL_COUNT = 4

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "name": "무기고 카메라 #1",
    "host": "192.168.0.78",
    "http_port": 80,
    "https_port": 443,
    "use_https": True,
    "rtsp_port": 554,
    "user": "admin",
    "password": "1q2w3e4r1!",
    "mode": "auto",  # auto | onvif | jpeg | rtsp
    "snapshot_path": "/capture/ch1.jpg",
    "rtsp_path": "/video1+audio1",
    "onvif_device_path": "/onvif/device_service",
    "onvif_media_path": "/onvif/media_service",
    "onvif_profile": "",
    "grid": "1x1",
    "retention_days": 1,
    "event_record_sec": 30,
    "continuous_record": True,
    "monitor_inside": True,
    "monitor_outside": True,
    "auto_event_on_emergency": True,
    "auto_event_on_unauthorized": True,
    "auto_event_on_offline": False,
    "auto_event_on_rfid_emergency": True,
    "onvif_ptz_path": "/onvif/PTZ",
    "ptz_speed": 0.45,
    # None/omit = auto by camera model. True = reverse ONVIF axis for this camera.
    "ptz_pan_invert": None,
    "ptz_tilt_invert": None,
    "auto_ptz_on_event": True,
    # 상황 번호(1~10) -> ONVIF 프리셋 토큰, e.g. {"1": "5", "2": "3"}
    "ptz_presets": {},
    # 보관함 id -> {"emergency": token, "unauthorized": token}. 보관함이 늘면 항목도 늘어난다.
    "ptz_lockers": {},
    # 이벤트 종류 -> 상황 번호 (비상개폐=1, 미인가=2 …)
    "ptz_event_map": {
        "emergency": 1,
        "unauthorized": 2,
        "offline": 3,
        "person": 4,
        "ops": 1,
        "manual": 5,
        "rfid_emergency": 6,
    },
    # Home / original position preset token (fallback if GotoHomePosition unsupported)
    "ptz_home_preset": "",
    # CCTV 사람 인지(표 4) 시 자동 PTZ/경광등 연동
    "auto_ptz_on_person": True,
    "auto_light_on_person": True,
    # PC 온디바이스 AI(MediaPipe)로 스냅샷 사람 감지
    "person_ai_enabled": True,
}

# UI labels for situation 1~10 (operator can remap events → situation)
PTZ_SITUATION_HINTS: dict[int, str] = {
    1: "비상개폐(기본)",
    2: "미인가 접근(기본)",
    3: "네트워크 스위치",
    4: "CCTV 사람 인지",
    5: "수동 녹화",
    6: "RFID 태깅 비상",
    7: "예비7",
    8: "예비8",
    9: "예비9",
    10: "예비10",
}


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EVENT_DIR.mkdir(parents=True, exist_ok=True)
    CONT_DIR.mkdir(parents=True, exist_ok=True)


def channel_index(value: Any, default: int = 0) -> int:
    """API/UI channel numbers are 1~4. Missing or 0 selects channel 1."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if 1 <= n <= CHANNEL_COUNT:
        return n - 1
    return default


def _defaults_copy() -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    cfg["ptz_presets"] = {}
    cfg["ptz_event_map"] = dict(DEFAULTS["ptz_event_map"])
    return cfg


def _blank_channel(index: int) -> dict[str, Any]:
    cfg = _defaults_copy()
    cfg["name"] = f"카메라 {index + 1}"
    cfg["host"] = ""
    cfg["password"] = ""
    cfg["enabled"] = False
    cfg["ptz_home_preset"] = ""
    cfg["onvif_profile"] = ""
    return cfg


def _merge_loaded(base: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(base)
    cfg["ptz_presets"] = dict(base.get("ptz_presets") or {})
    cfg["ptz_event_map"] = dict(DEFAULTS["ptz_event_map"])
    if isinstance(base.get("ptz_event_map"), dict):
        cfg["ptz_event_map"].update(base["ptz_event_map"])
    if not isinstance(raw, dict):
        return cfg
    for key in DEFAULTS:
        if key not in raw:
            continue
        if key == "ptz_event_map" and isinstance(raw[key], dict):
            merged = dict(DEFAULTS["ptz_event_map"])
            for ek, sit in raw[key].items():
                try:
                    sit_n = int(sit)
                except (TypeError, ValueError):
                    continue
                if 1 <= sit_n <= 10:
                    merged[str(ek).strip().lower()] = sit_n
            cfg[key] = merged
        elif key == "ptz_presets" and isinstance(raw[key], dict):
            cfg[key] = {str(k): str(v) for k, v in raw[key].items() if str(v).strip()}
        else:
            cfg[key] = raw[key]
    # Legacy: 보관 후 RFID 태깅 → RFID 태깅 비상
    if "auto_event_on_rfid_emergency" not in cfg and "auto_event_on_rfid_stored" in raw:
        cfg["auto_event_on_rfid_emergency"] = bool(raw.get("auto_event_on_rfid_stored"))
    event_map = cfg.get("ptz_event_map") if isinstance(cfg.get("ptz_event_map"), dict) else {}
    if "rfid_emergency" not in event_map and "rfid_stored" in event_map:
        event_map = dict(event_map)
        event_map["rfid_emergency"] = event_map["rfid_stored"]
        cfg["ptz_event_map"] = event_map
    return cfg


def _read_cfg(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _ensure_cfg_file() -> None:
    """Migrate data/cctv.json → beside-exe cctv.json once (then into oms.json)."""
    if CFG_PATH.is_file() or not LEGACY_CFG_PATH.is_file():
        return
    try:
        CFG_PATH.write_bytes(LEGACY_CFG_PATH.read_bytes())
    except OSError:
        return


def _load_cctv_raw() -> Any:
    from oms_bundle import SECTION_CCTV, migrate_section_from_file, read_section

    raw = read_section(SECTION_CCTV)
    if isinstance(raw, dict):
        return raw
    _ensure_cfg_file()
    # Prefer beside-exe cctv.json, then data/cctv.json.
    for legacy in (CFG_PATH, LEGACY_CFG_PATH):
        migrated = migrate_section_from_file(SECTION_CCTV, legacy)
        if isinstance(migrated, dict):
            return migrated
    return None


def load_channels() -> list[dict[str, Any]]:
    _ensure_dirs()
    channels = [_blank_channel(i) for i in range(CHANNEL_COUNT)]
    raw = _load_cctv_raw()
    if isinstance(raw, dict) and isinstance(raw.get("channels"), list):
        for i, item in enumerate(raw["channels"][:CHANNEL_COUNT]):
            if isinstance(item, dict):
                channels[i] = _merge_loaded(_blank_channel(i), item)
        return channels
    if isinstance(raw, dict):
        channels[0] = _merge_loaded(_defaults_copy(), raw)
        return channels
    channels[0] = _defaults_copy()
    return channels


def load_config(channel: int = 0) -> dict[str, Any]:
    channels = load_channels()
    idx = max(0, min(CHANNEL_COUNT - 1, int(channel)))
    cfg = dict(channels[idx])
    cfg["ptz_presets"] = dict(cfg.get("ptz_presets") or {})
    cfg["ptz_event_map"] = dict(cfg.get("ptz_event_map") or DEFAULTS["ptz_event_map"])
    cfg["_channel"] = idx
    return cfg


def _persist_channels(channels: list[dict[str, Any]]) -> None:
    from oms_bundle import SECTION_CCTV, write_section

    payload_channels: list[dict[str, Any]] = []
    for cfg in channels:
        item = {key: cfg[key] for key in DEFAULTS if key in cfg}
        payload_channels.append(item)
    _ensure_dirs()
    write_section(SECTION_CCTV, {"channels": payload_channels})


def _onvif_cache_key(cfg: dict[str, Any]) -> str:
    host = str(cfg.get("host") or "").strip().lower()
    https = bool(cfg.get("use_https"))
    port = int(cfg.get("https_port") or 443) if https else int(cfg.get("http_port") or 80)
    return f"{int(cfg.get('_channel') or 0)}|{host}|{int(https)}|{port}"


def public_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return {key: cfg.get(key) for key in DEFAULTS if key != "password"}


def _channel_of(cfg: dict[str, Any] | None) -> int:
    try:
        return max(0, min(CHANNEL_COUNT - 1, int((cfg or {}).get("_channel") or 0)))
    except (TypeError, ValueError):
        return 0


def save_config(updates: dict[str, Any] | None = None, channel: int = 0) -> dict[str, Any]:
    updates = dict(updates or {})
    updates.pop("channel", None)
    updates.pop("save", None)
    updates.pop("channels", None)
    idx = max(0, min(CHANNEL_COUNT - 1, int(channel)))
    with _lock:
        channels = load_channels()
        cfg = dict(channels[idx])
        cfg["ptz_presets"] = dict(cfg.get("ptz_presets") or {})
        cfg["ptz_event_map"] = dict(cfg.get("ptz_event_map") or DEFAULTS["ptz_event_map"])
        for key, value in updates.items():
            if key not in DEFAULTS:
                continue
            if key in ("http_port", "https_port", "rtsp_port", "retention_days", "event_record_sec"):
                try:
                    cfg[key] = int(value)
                except (TypeError, ValueError):
                    continue
            elif key in (
                "enabled",
                "use_https",
                "continuous_record",
                "monitor_inside",
                "monitor_outside",
                "auto_event_on_emergency",
                "auto_event_on_unauthorized",
                "auto_event_on_offline",
                "auto_event_on_rfid_emergency",
                "auto_ptz_on_event",
                "auto_ptz_on_person",
                "auto_light_on_person",
                "person_ai_enabled",
                "ptz_pan_invert",
                "ptz_tilt_invert",
            ):
                if value is None or (isinstance(value, str) and value.strip().lower() in ("", "auto", "none", "null")):
                    cfg[key] = None
                elif isinstance(value, str):
                    cfg[key] = value.strip().lower() in ("1", "true", "yes", "on")
                else:
                    cfg[key] = bool(value)
            elif key == "password":
                text = str(value or "").strip()
                if text:
                    cfg[key] = text
            elif key == "mode":
                mode = str(value or "auto").strip().lower()
                cfg[key] = mode if mode in ("auto", "onvif", "jpeg", "rtsp") else "auto"
            elif key == "ptz_presets":
                if isinstance(value, dict):
                    cleaned: dict[str, str] = {}
                    for sk, token in value.items():
                        key_s = str(sk).strip()
                        tok = str(token or "").strip()
                        if key_s and tok:
                            cleaned[key_s] = tok
                    cfg[key] = cleaned
            elif key == "ptz_lockers":
                if isinstance(value, dict):
                    cleaned_lockers: dict[str, dict[str, str]] = {}
                    for locker_id, row in value.items():
                        lid = str(locker_id or "").strip()
                        if not lid or not isinstance(row, dict):
                            continue
                        kept: dict[str, str] = {}
                        for aim in ("emergency", "unauthorized", "ops", "rfid_emergency"):
                            token = str(row.get(aim) or "").strip()
                            if token:
                                kept[aim] = token
                        # Migrate previous key name if present.
                        legacy = str(row.get("rfid_stored") or "").strip()
                        if legacy and "rfid_emergency" not in kept:
                            kept["rfid_emergency"] = legacy
                        if kept:
                            cleaned_lockers[lid] = kept
                    cfg[key] = cleaned_lockers
            elif key == "ptz_event_map":
                if isinstance(value, dict):
                    cleaned_map: dict[str, int] = {}
                    for ek, sit in value.items():
                        kind = str(ek or "").strip().lower()
                        if not kind:
                            continue
                        try:
                            sit_n = int(sit)
                        except (TypeError, ValueError):
                            continue
                        if 1 <= sit_n <= 10:
                            cleaned_map[kind] = sit_n
                    if cleaned_map:
                        base_map = dict(DEFAULTS["ptz_event_map"])
                        base_map.update(cleaned_map)
                        cfg[key] = base_map
            elif key == "ptz_speed":
                try:
                    cfg[key] = max(0.05, min(1.0, float(value)))
                except (TypeError, ValueError):
                    continue
            else:
                cfg[key] = str(value).strip() if value is not None else cfg[key]
        cfg["retention_days"] = _clamp_retention(cfg.get("retention_days"))
        cfg["event_record_sec"] = max(5, min(300, int(cfg.get("event_record_sec") or 30)))
        cfg.pop("_channel", None)
        channels[idx] = cfg
        _persist_channels(channels)
        _onvif_cache.pop(_onvif_cache_key({**cfg, "_channel": idx}), None)
    saved = dict(cfg)
    saved["ptz_presets"] = dict(saved.get("ptz_presets") or {})
    saved["ptz_event_map"] = dict(saved.get("ptz_event_map") or {})
    saved["_channel"] = idx
    _bump_recording()
    return saved


def _base_url(cfg: dict[str, Any], https: bool | None = None) -> str:
    use_https = bool(cfg.get("use_https")) if https is None else bool(https)
    host = str(cfg.get("host") or "").strip()
    if use_https:
        port = int(cfg.get("https_port") or 443)
        if port == 443:
            return f"https://{host}"
        return f"https://{host}:{port}"
    port = int(cfg.get("http_port") or 80)
    if port == 80:
        return f"http://{host}"
    return f"http://{host}:{port}"


def _opener(cfg: dict[str, Any], base: str | None = None):
    base = base or _base_url(cfg)
    ctx = ssl._create_unverified_context()
    pm = HTTPPasswordMgrWithDefaultRealm()
    user = str(cfg.get("user") or "")
    pw = str(cfg.get("password") or "")
    pm.add_password(None, base, user, pw)
    # Also register without port / with host only for digest realm quirks
    pm.add_password(None, f"http://{cfg.get('host')}", user, pw)
    pm.add_password(None, f"https://{cfg.get('host')}", user, pw)
    return build_opener(
        HTTPSHandler(context=ctx),
        HTTPDigestAuthHandler(pm),
        HTTPBasicAuthHandler(pm),
    ), base


def _http_get(cfg: dict[str, Any], url: str, timeout: float = 6.0) -> tuple[bytes, str]:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    op, _ = _opener(cfg, base)
    req = Request(url, headers={"User-Agent": "OMS-CCTV/1.0"})
    with op.open(req, timeout=timeout) as resp:
        data = resp.read()
        ctype = resp.headers.get("Content-Type") or "application/octet-stream"
    return data, ctype


def _soap(cfg: dict[str, Any], base: str, path: str, body: str, action: str, timeout: float = 8.0) -> str:
    if not path.startswith("/"):
        path = "/" + path
    op, _ = _opener(cfg, base)
    req = Request(
        base + path,
        data=body.encode("utf-8"),
        headers={
            "Content-Type": "application/soap+xml; charset=utf-8",
            "SOAPAction": action,
            "User-Agent": "OMS-CCTV/1.0",
        },
        method="POST",
    )
    with op.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _xml_uris(text: str) -> list[str]:
    return re.findall(r"<[^:>]*:?Uri>([^<]+)</[^:>]*:?Uri>", text, flags=re.I)


def _xml_profile_tokens(text: str) -> list[str]:
    # Prefer Profiles token=...
    tokens = re.findall(r"<[^:>]*:?Profiles\b[^>]*\btoken=\"([^\"]+)\"", text, flags=re.I)
    if tokens:
        return tokens
    return re.findall(r"\btoken=\"([^\"]*PROFILE[^\"]*)\"", text, flags=re.I)


def _media_paths(cfg: dict[str, Any]) -> list[str]:
    """ONVIF Media endpoint candidates (some cameras expose Media on device_service)."""
    primary = str(cfg.get("onvif_media_path") or "").strip() or "/onvif/media_service"
    device = str(cfg.get("onvif_device_path") or "").strip() or "/onvif/device_service"
    alts = [
        primary,
        device,
        "/onvif/media_service",
        "/onvif/device_service",
        "/onvif/Media",
        "/onvif/media",
    ]
    out: list[str] = []
    for path in alts:
        if not path.startswith("/"):
            path = "/" + path
        if path not in out:
            out.append(path)
    return out


def onvif_discover(cfg: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    cfg = dict(cfg or load_config())
    now = time.monotonic()
    key = _onvif_cache_key(cfg)
    with _lock:
        cached = dict(_onvif_cache.get(key) or {})
        if not force and cached.get("ok") and now - float(cached.get("at") or 0) < 60:
            return cached

    errors: list[str] = []
    # Prefer configured scheme first, then the other. HTTPS often fails TLS on these cams.
    bases: list[str] = []
    for candidate in (
        _base_url(cfg),
        _base_url(cfg, https=not bool(cfg.get("use_https"))),
    ):
        if candidate not in bases:
            bases.append(candidate)

    profile_pref = str(cfg.get("onvif_profile") or "").strip()

    for base in bases:
        for media_path in _media_paths(cfg):
            try:
                profiles_xml = _soap(
                    cfg,
                    base,
                    media_path,
                    """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body><GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/></s:Body>
</s:Envelope>""",
                    "http://www.onvif.org/ver10/media/wsdl/GetProfiles",
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{base}{media_path}: {exc}")
                continue
            tokens = _xml_profile_tokens(profiles_xml)
            if profile_pref and profile_pref in tokens:
                tokens = [profile_pref] + [t for t in tokens if t != profile_pref]
            if not tokens:
                errors.append(f"{base}{media_path}: ONVIF 프로필 없음")
                continue
            token = tokens[0]
            snap_url = ""
            stream_url = ""
            try:
                snap_xml = _soap(
                    cfg,
                    base,
                    media_path,
                    f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body>
    <GetSnapshotUri xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ProfileToken>{token}</ProfileToken>
    </GetSnapshotUri>
  </s:Body>
</s:Envelope>""",
                    "http://www.onvif.org/ver10/media/wsdl/GetSnapshotUri",
                )
                uris = _xml_uris(snap_xml)
                if uris:
                    snap_url = uris[0].strip()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"GetSnapshotUri: {exc}")
            try:
                stream_xml = _soap(
                    cfg,
                    base,
                    media_path,
                    f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body>
    <GetStreamUri xmlns="http://www.onvif.org/ver10/media/wsdl">
      <StreamSetup>
        <Stream xmlns="http://www.onvif.org/ver10/schema">RTP-Unicast</Stream>
        <Transport xmlns="http://www.onvif.org/ver10/schema"><Protocol>RTSP</Protocol></Transport>
      </StreamSetup>
      <ProfileToken>{token}</ProfileToken>
    </GetStreamUri>
  </s:Body>
</s:Envelope>""",
                    "http://www.onvif.org/ver10/media/wsdl/GetStreamUri",
                )
                uris = _xml_uris(stream_xml)
                if uris:
                    stream_url = uris[0].strip()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"GetStreamUri: {exc}")

            if not (snap_url or stream_url):
                errors.append(f"{base}{media_path}: 스냅샷/스트림 URI 없음")
                continue

            result = {
                "at": now,
                "base": base,
                "media_path": media_path,
                "profile": token,
                "profiles": tokens[:8],
                "snapshot_url": snap_url,
                "stream_url": stream_url,
                "errors": errors,
                "ok": True,
            }
            with _lock:
                _onvif_cache[key] = result
            return result

    fail = {
        "at": now,
        "base": "",
        "media_path": "",
        "profile": "",
        "profiles": [],
        "snapshot_url": "",
        "stream_url": "",
        "errors": errors or ["ONVIF 연결 실패"],
        "ok": False,
    }
    with _lock:
        _onvif_cache[key] = fail
    return fail


def _inject_rtsp_auth(url: str, cfg: dict[str, Any]) -> str:
    user = str(cfg.get("user") or "")
    pw = str(cfg.get("password") or "")
    if not user or "@" in urlparse(url).netloc:
        return url
    p = urlparse(url)
    netloc = f"{user}:{pw}@{p.hostname}"
    if p.port:
        netloc += f":{p.port}"
    return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))


def rtsp_url(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or load_config()
    mode = str(cfg.get("mode") or "auto").lower()
    user = str(cfg.get("user") or "")
    pw = str(cfg.get("password") or "")
    host = str(cfg.get("host") or "")
    port = int(cfg.get("rtsp_port") or 554)
    path = str(cfg.get("rtsp_path") or "/video1")
    if not path.startswith("/"):
        path = "/" + path
    # Known camera path (e.g. FS-IR /cam0_0): skip ONVIF SOAP on every live tick.
    configured = ""
    if host and path:
        auth = f"{user}:{pw}@" if user else ""
        configured = f"rtsp://{auth}{host}:{port}{path}"
    if mode == "rtsp" and configured:
        return configured
    if mode in ("auto", "onvif"):
        # Prefer configured path for low-latency live; ONVIF only as fallback.
        if configured and path not in ("/video1", "/video1+audio1", "/"):
            return configured
        disc = onvif_discover(cfg)
        if disc.get("stream_url"):
            return _inject_rtsp_auth(str(disc["stream_url"]), cfg)
    return configured or f"rtsp://{host}:{port}{path}"


def _snapshot_candidates(cfg: dict[str, Any]) -> list[tuple[str, str]]:
    """Return list of (label, url)."""
    mode = str(cfg.get("mode") or "auto").lower()
    out: list[tuple[str, str]] = []
    path = str(cfg.get("snapshot_path") or "/capture/ch1.jpg")
    if not path.startswith("/"):
        path = "/" + path

    if mode in ("auto", "onvif"):
        disc = onvif_discover(cfg)
        if disc.get("snapshot_url"):
            out.append(("onvif-snapshot", str(disc["snapshot_url"])))

    if mode in ("auto", "jpeg", "onvif"):
        for https in (bool(cfg.get("use_https")), not bool(cfg.get("use_https"))):
            base = _base_url(cfg, https=https)
            label = "https-jpeg" if https else "http-jpeg"
            out.append((label, base + path))
            # common aliases
            for alt in ("/capturx/ch1.jpg", "/capture/ch1.jpg"):
                if alt != path:
                    out.append((label + "-alt", base + alt))

    # dedupe preserving order
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for label, url in out:
        if url in seen:
            continue
        seen.add(url)
        uniq.append((label, url))
    return uniq


class _RtspLiveGrabber:
    """Keep one ffmpeg RTSP→MJPEG pipe open and expose the latest JPEG frame."""

    def __init__(self, key: str, url: str) -> None:
        self.key = key
        self.url = url
        self.jpeg = b""
        self.at = 0.0
        self.error = ""
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"oms-live-{key[:24]}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

    def get(self, wait_sec: float = 4.0) -> bytes:
        deadline = time.monotonic() + max(0.05, wait_sec)
        with self._cond:
            while not self.jpeg and not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(timeout=min(0.4, remaining))
            if self.jpeg:
                return self.jpeg
        if self.error:
            raise RuntimeError(self.error)
        raise RuntimeError("RTSP 라이브 프레임 대기 시간 초과")

    def _publish(self, data: bytes) -> None:
        with self._cond:
            self.jpeg = data
            self.at = time.monotonic()
            self.error = ""
            self._cond.notify_all()

    def _run(self) -> None:
        ffmpeg = _ffmpeg_bin()
        if not ffmpeg:
            with self._cond:
                self.error = "ffmpeg 없음 — RTSP 스냅샷 불가"
                self._cond.notify_all()
            return
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        while not self._stop.is_set():
            proc: subprocess.Popen | None = None
            try:
                proc = subprocess.Popen(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-fflags",
                        "nobuffer+discardcorrupt",
                        "-flags",
                        "low_delay",
                        "-probesize",
                        "32",
                        "-analyzeduration",
                        "0",
                        "-max_delay",
                        "0",
                        "-rtsp_transport",
                        "tcp",
                        "-i",
                        self.url,
                        "-an",
                        "-vf",
                        # Smaller JPEG + capped FPS keeps UI snappy on LAN.
                        "scale=960:-2,fps=15",
                        "-q:v",
                        "8",
                        "-f",
                        "image2pipe",
                        "-vcodec",
                        "mjpeg",
                        "pipe:1",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation,
                    bufsize=0,
                )
                assert proc.stdout is not None
                buf = b""
                while not self._stop.is_set():
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    if len(buf) > 8_000_000:
                        buf = buf[-2_000_000:]
                    while True:
                        start = buf.find(b"\xff\xd8")
                        if start < 0:
                            buf = b""
                            break
                        end = buf.find(b"\xff\xd9", start + 2)
                        if end < 0:
                            buf = buf[start:]
                            break
                        frame = buf[start : end + 2]
                        buf = buf[end + 2 :]
                        if len(frame) > 500:
                            self._publish(frame)
            except Exception as exc:  # noqa: BLE001
                with self._cond:
                    self.error = str(exc)[:160]
                    self._cond.notify_all()
            finally:
                if proc is not None:
                    try:
                        if proc.poll() is None:
                            proc.kill()
                        proc.wait(timeout=2)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            if self._stop.wait(1.0):
                break


def _grabber_key(cfg: dict[str, Any]) -> str:
    return f"{_channel_of(cfg)}|{cfg.get('host')}|{cfg.get('rtsp_path')}|{cfg.get('user')}"


def _rtsp_live_snapshot(cfg: dict[str, Any], wait_sec: float = 4.0) -> tuple[bytes, str]:
    url = rtsp_url(cfg)
    key = _grabber_key(cfg)
    with _lock:
        grabber = _live_grabbers.get(key)
        if grabber is None or grabber.url != url or not grabber._thread.is_alive():
            if grabber is not None:
                grabber.stop()
            grabber = _RtspLiveGrabber(key, url)
            _live_grabbers[key] = grabber
        # Drop stale grabbers for same channel/other paths
        stale = [k for k, g in _live_grabbers.items() if k != key and k.startswith(f"{_channel_of(cfg)}|")]
        for k in stale:
            old = _live_grabbers.pop(k, None)
            if old:
                old.stop()
    # Always serve the newest buffered frame immediately — never block the UI
    # waiting for a "newer" frame (that was the main live lag source).
    if grabber.jpeg:
        return grabber.jpeg, "image/jpeg"
    data = grabber.get(wait_sec=min(max(0.2, wait_sec), 2.5))
    return data, "image/jpeg"


def stop_live_grabbers() -> None:
    with _lock:
        grabbers = list(_live_grabbers.values())
        _live_grabbers.clear()
    for grabber in grabbers:
        try:
            grabber.stop()
        except Exception:  # noqa: BLE001
            pass


def _rtsp_snapshot_bytes(cfg: dict[str, Any]) -> tuple[bytes, str]:
    """One-shot RTSP frame via ffmpeg (fallback when live grabber is unavailable)."""
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("ffmpeg 없음 — RTSP 스냅샷 불가")
    url = rtsp_url(cfg)
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            url,
            "-frames:v",
            "1",
            "-f",
            "image2",
            "pipe:1",
        ],
        capture_output=True,
        timeout=12,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    data = proc.stdout or b""
    if proc.returncode != 0 or len(data) < 100 or data[:2] != b"\xff\xd8":
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()[:160]
        raise RuntimeError(err or f"RTSP 스냅샷 실패 (code={proc.returncode})")
    return data, "image/jpeg"


def _prefer_rtsp_snapshot(cfg: dict[str, Any]) -> bool:
    """True when HTTP/ONVIF JPEG is known missing — skip slow 404/TLS retries."""
    mode = str(cfg.get("mode") or "auto").lower()
    if mode == "rtsp":
        return True
    key = _grabber_key(cfg)
    if time.monotonic() < float(_jpeg_skip_until.get(key) or 0):
        return True
    if mode in ("auto", "onvif"):
        disc = onvif_discover(cfg)
        if disc.get("ok") and disc.get("stream_url") and not disc.get("snapshot_url"):
            return True
    return False


def fetch_snapshot(cfg: dict[str, Any] | None = None) -> tuple[bytes, str]:
    cfg = cfg or load_config()
    mode = str(cfg.get("mode") or "auto").lower()
    errors: list[str] = []
    key = _grabber_key(cfg)

    if _prefer_rtsp_snapshot(cfg):
        try:
            return _rtsp_live_snapshot(cfg)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"rtsp-live: {exc}")
            try:
                return _rtsp_snapshot_bytes(cfg)
            except Exception as exc2:  # noqa: BLE001
                errors.append(f"rtsp-frame: {exc2}")
                raise RuntimeError(" / ".join(errors[:4]) or "snapshot failed") from exc2

    http_ok = False
    for label, url in _snapshot_candidates(cfg):
        try:
            data, ctype = _http_get(cfg, url, timeout=2.0)
            if data[:2] == b"\xff\xd8" or "jpeg" in (ctype or "").lower() or len(data) > 500:
                http_ok = True
                return data, ctype or "image/jpeg"
            errors.append(f"{label}: not jpeg ({ctype})")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")
    if not http_ok:
        _jpeg_skip_until[key] = time.monotonic() + 120.0

    # FlexWATCH / FS-IR307A: ONVIF GetSnapshotUri and JPEG paths fail; RTSP works.
    if mode in ("auto", "rtsp", "onvif"):
        try:
            return _rtsp_live_snapshot(cfg)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"rtsp-live: {exc}")
            try:
                return _rtsp_snapshot_bytes(cfg)
            except Exception as exc2:  # noqa: BLE001
                errors.append(f"rtsp-frame: {exc2}")
    raise RuntimeError(" / ".join(errors[:4]) or "snapshot failed")


def _ffmpeg_bin() -> str | None:
    """Locate ffmpeg. Other PCs often fail recording when only PATH/winget was checked."""
    global _ffmpeg_cached
    if _ffmpeg_cached is not False:
        return _ffmpeg_cached if isinstance(_ffmpeg_cached, str) else None

    candidates: list[Path] = []
    # Portable: put ffmpeg.exe next to OMS.exe / oms folder / _deps
    for base in (ROOT, Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT):
        candidates.extend(
            [
                base / "ffmpeg.exe",
                base / "ffmpeg" / "ffmpeg.exe",
                base / "bin" / "ffmpeg.exe",
                base / "_deps" / "ffmpeg.exe",
                base / "_deps" / "ffmpeg" / "bin" / "ffmpeg.exe",
                base / "_deps" / "ffmpeg" / "ffmpeg.exe",
            ]
        )
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        mp = Path(meipass)
        candidates.extend(
            [
                mp / "ffmpeg.exe",
                mp / "ffmpeg" / "ffmpeg.exe",
                mp / "bin" / "ffmpeg.exe",
            ]
        )
    which = shutil.which("ffmpeg")
    if which:
        candidates.append(Path(which))
    winget = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    if winget.is_dir():
        try:
            for path in winget.rglob("ffmpeg.exe"):
                candidates.append(path)
                break
        except OSError:
            pass

    for path in candidates:
        try:
            if path.is_file():
                _ffmpeg_cached = str(path.resolve())
                return _ffmpeg_cached
        except OSError:
            continue
    _ffmpeg_cached = None
    return None


def continuous_status() -> dict[str, Any]:
    """Why continuous recording is on / off (helps other laptops without ffmpeg)."""
    ffmpeg = _ffmpeg_bin()
    channels = load_channels()
    targets = [
        i + 1
        for i, cfg in enumerate(channels)
        if cfg.get("enabled")
        and cfg.get("continuous_record", True)
        and str(cfg.get("host") or "").strip()
    ]
    active = _continuous_active()
    if not ffmpeg:
        detail = (
            "ffmpeg 없음 — 실시간(전체) 녹화를 할 수 없습니다. "
            "OMS 폴더에 ffmpeg.exe를 넣거나 PATH에 설치하세요."
        )
        return {
            "active": False,
            "ffmpeg": False,
            "targets": targets,
            "detail": detail,
            "error": "ffmpeg_missing",
        }
    if not targets:
        return {
            "active": False,
            "ffmpeg": True,
            "targets": [],
            "detail": "전체 녹화가 꺼져 있거나 카메라 IP가 없습니다. CCTV 설정에서 확인하세요.",
            "error": "no_target",
        }
    if _disk_low():
        return {
            "active": False,
            "ffmpeg": True,
            "targets": targets,
            "detail": "디스크 여유 공간 부족(2GB 미만) — 전체 녹화를 잠시 멈췄습니다.",
            "error": "disk_low",
        }
    return {
        "active": active,
        "ffmpeg": True,
        "targets": targets,
        "detail": "전체 연속 녹화 중" if active else "전체 연속 녹화 준비 중…",
        "error": "",
    }


def _probe_rtsp(cfg: dict[str, Any]) -> dict[str, Any]:
    url = rtsp_url(cfg)
    ffprobe = None
    ffmpeg = _ffmpeg_bin()
    if ffmpeg:
        cand = Path(ffmpeg).with_name("ffprobe.exe")
        if cand.is_file():
            ffprobe = str(cand)
        else:
            ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"ok": None, "detail": "ffprobe 없음 (RTSP 녹화는 ffmpeg로 시도)", "url": url.split("@")[-1]}
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", url],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        ok = proc.returncode == 0 and "video" in out.lower()
        return {
            "ok": ok,
            "detail": "RTSP OK" if ok else f"RTSP 실패: {out.strip()[:160] or proc.returncode}",
            "url": url.split("@")[-1],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"RTSP 오류: {exc}", "url": url.split("@")[-1]}


def diagnose(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = dict(cfg or load_config())
    steps: list[dict[str, Any]] = []

    # 1) ONVIF
    disc = onvif_discover(cfg, force=True)
    steps.append(
        {
            "name": "ONVIF",
            "ok": bool(disc.get("ok")),
            "detail": (
                f"profile={disc.get('profile') or '-'} snap={disc.get('snapshot_url') or '-'} "
                f"rtsp={disc.get('stream_url') or '-'}"
            ),
            "errors": disc.get("errors") or [],
        }
    )

    # 2) JPEG candidates
    jpeg_ok = False
    jpeg_detail = ""
    for label, url in _snapshot_candidates(cfg)[:8]:
        try:
            data, ctype = _http_get(cfg, url, timeout=5.0)
            if data[:2] == b"\xff\xd8" or "jpeg" in ctype.lower():
                jpeg_ok = True
                jpeg_detail = f"{label} OK ({len(data)} bytes) {url}"
                break
            steps.append({"name": f"JPEG:{label}", "ok": False, "detail": f"not jpeg {ctype} {url}"})
        except Exception as exc:  # noqa: BLE001
            steps.append({"name": f"JPEG:{label}", "ok": False, "detail": f"{exc}"})
    if jpeg_ok:
        steps.append({"name": "JPEG", "ok": True, "detail": jpeg_detail})
    elif not any(s["name"] == "JPEG" for s in steps):
        steps.append({"name": "JPEG", "ok": False, "detail": "모든 JPEG 경로 실패"})

    # 3) RTSP
    rtsp = _probe_rtsp(cfg)
    steps.append({"name": "RTSP", "ok": rtsp.get("ok"), "detail": rtsp.get("detail"), "url": rtsp.get("url")})

    rtsp_frame_ok = False
    if not jpeg_ok and (disc.get("stream_url") or rtsp.get("ok")):
        try:
            data, ctype = _rtsp_snapshot_bytes(cfg)
            rtsp_frame_ok = len(data) > 100
            steps.append(
                {
                    "name": "RTSP-frame",
                    "ok": True,
                    "detail": f"ffmpeg JPEG OK ({len(data)} bytes, {ctype})",
                }
            )
        except Exception as exc:  # noqa: BLE001
            steps.append({"name": "RTSP-frame", "ok": False, "detail": str(exc)})

    online = bool(jpeg_ok or disc.get("snapshot_url") or rtsp_frame_ok or disc.get("stream_url"))
    advice = []
    if not online:
        advice.append("아이디/비밀번호가 카메라 웹과 같은지 확인하세요 (FlexWATCH는 보통 root).")
        advice.append("HTTPS TLS 오류면 'HTTPS 사용'을 끄고 HTTP(80)로 연결하세요.")
        advice.append("방화벽에서 OMS PC → 카메라 80/443/554 포트를 허용하세요.")
    if disc.get("ok") and not jpeg_ok and not rtsp_frame_ok:
        advice.append("ONVIF는 됐지만 스냅샷이 없습니다. ffmpeg로 RTSP 프레임을 쓰거나 RTSP 경로(/cam0_0)를 확인하세요.")
    if rtsp.get("ok") is False:
        advice.append("RTSP 실패 시 이벤트 녹화·라이브 스냅샷이 안 됩니다.")

    return {
        "ok": online,
        "online": online,
        "mode": cfg.get("mode"),
        "use_https": bool(cfg.get("use_https")),
        "host": cfg.get("host"),
        "steps": steps,
        "onvif": {
            k: disc.get(k)
            for k in ("profile", "profiles", "snapshot_url", "stream_url", "base", "media_path")
        },
        "advice": advice,
        "detail": jpeg_detail
        or (f"RTSP 프레임 OK" if rtsp_frame_ok else "")
        or (steps[0].get("detail") if steps else "진단 완료"),
    }


def _persist_discovered_paths(cfg: dict[str, Any], disc: dict[str, Any]) -> None:
    """Remember working ONVIF/RTSP endpoints so reconnect does not depend on discovery alone."""
    fixes: dict[str, Any] = {}
    media_path = str(disc.get("media_path") or "").strip()
    if media_path:
        fixes["onvif_media_path"] = media_path
        if media_path.rstrip("/").endswith("device_service"):
            fixes["onvif_device_path"] = media_path
            fixes["onvif_ptz_path"] = media_path
    base = str(disc.get("base") or "")
    if base.startswith("http://"):
        fixes["use_https"] = False
    elif base.startswith("https://"):
        fixes["use_https"] = True
    stream = str(disc.get("stream_url") or "").strip()
    if stream:
        try:
            path = urlparse(stream).path or ""
            if path:
                fixes["rtsp_path"] = path
        except Exception:  # noqa: BLE001
            pass
    if not fixes:
        return
    try:
        save_config(fixes, channel=_channel_of(cfg))
    except Exception:  # noqa: BLE001
        pass


def test_connection(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = dict(cfg or load_config())
    try:
        data, ctype = fetch_snapshot(cfg)
        ok = len(data) > 100
        disc = onvif_discover(cfg)
        if ok and disc.get("ok"):
            _persist_discovered_paths(cfg, disc)
        return {
            "ok": ok,
            "online": ok,
            "detail": f"연결 성공 ({len(data)} bytes, {ctype})"
            + (f" / ONVIF {disc.get('profile')}" if disc.get("ok") else ""),
            "bytes": len(data),
            "onvif": {
                k: disc.get(k)
                for k in ("profile", "snapshot_url", "stream_url", "media_path", "base")
            },
        }
    except Exception as exc:  # noqa: BLE001
        report = diagnose(cfg)
        return {
            "ok": False,
            "online": False,
            "detail": f"연결 실패: {exc}",
            "bytes": 0,
            "diagnose": report,
        }


def camera_status(channel: int = 0) -> dict[str, Any]:
    maybe_purge_recordings()
    idx = max(0, min(CHANNEL_COUNT - 1, int(channel)))
    channels = load_channels()
    cfg = dict(channels[idx])
    cfg["_channel"] = idx
    host = str(cfg.get("host") or "").strip()
    if host and cfg.get("enabled", True):
        probe = test_connection(cfg)
    elif host:
        probe = {"ok": False, "online": False, "detail": "이 채널은 사용 안 함", "onvif": {}}
    else:
        probe = {"ok": False, "online": False, "detail": "카메라 미설정", "onvif": {}}
    rtsp = rtsp_url(cfg) if host else ""
    listed: list[dict[str, Any]] = []
    for i, item in enumerate(channels):
        view = public_config(item)
        view["channel"] = i + 1
        view["has_password"] = bool(item.get("password"))
        listed.append(view)
    return {
        "ok": bool(probe.get("ok")),
        "online": bool(probe.get("online")),
        "detail": probe.get("detail") or "",
        "channel": idx + 1,
        "rtsp": rtsp.split("@")[-1] if "@" in rtsp else rtsp,
        "config": listed[idx],
        "channels": listed,
        "has_password": bool(cfg.get("password")),
        "ffmpeg": bool(_ffmpeg_bin()),
        "retention_days": int(cfg.get("retention_days") or 30),
        "onvif": probe.get("onvif") or {},
        "diagnose": probe.get("diagnose"),
        "recordings": list_recordings(limit=400),
        "continuous_recording": _continuous_active(),
        "continuous": continuous_status(),
        "events": list_events(limit=40),
        "person_ai": _person_ai_status(),
    }


def _person_ai_status() -> dict[str, Any]:
    try:
        from person_ai import status as person_ai_status

        return person_ai_status()
    except Exception as exc:  # noqa: BLE001
        return {"enabled": False, "running": False, "error": str(exc)}


def _clip_span(path: Path, mtime: float) -> str:
    match = re.match(r"(\d{8})_(\d{6})", path.stem)
    if not match:
        return datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M")
    start = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    end = datetime.fromtimestamp(mtime)
    if end < start:
        end = start
    return f"{start.strftime('%m-%d %H:%M')}–{end.strftime('%H:%M')}"


def list_recordings(limit: int = 50) -> list[dict[str, Any]]:
    _ensure_dirs()
    with _lock:
        active = set(_rec_procs)
    rows: list[dict[str, Any]] = []
    for folder, kind in ((CONT_DIR, "continuous"), (EVENT_DIR, "event")):
        for path in folder.glob("*.mp4"):
            if path.name.endswith(".web.mp4") or path.name in active:
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_size < 1000:
                continue
            rows.append(
                {
                    "id": path.name,
                    "kind": kind,
                    "name": path.name,
                    "label": _clip_span(path, st.st_mtime),
                    "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "url": f"/api/cctv/file?kind={kind}&name={path.name}",
                }
            )
    rows.sort(key=lambda r: r.get("mtime") or "", reverse=True)
    return rows[:limit]


def list_events(limit: int = 50) -> list[dict[str, Any]]:
    _ensure_dirs()
    meta_path = EVENT_DIR / "events.jsonl"
    rows: list[dict[str, Any]] = []
    if meta_path.is_file():
        try:
            lines = meta_path.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines[-500:]):
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and _event_still_kept(item):
                    item = dict(item)
                    video_name = Path(str(item.get("video") or "")).name
                    with _lock:
                        recording = bool(video_name) and video_name in _rec_procs
                    item["recording"] = recording
                    if recording:
                        item["video_size"] = 0
                    else:
                        try:
                            item["video_size"] = (EVENT_DIR / video_name).stat().st_size if video_name else 0
                        except OSError:
                            item["video_size"] = 0
                    rows.append(item)
                    if len(rows) >= limit:
                        break
        except OSError:
            pass
    return rows


def _append_event_meta(item: dict[str, Any]) -> None:
    _ensure_dirs()
    meta_path = EVENT_DIR / "events.jsonl"
    with _lock:
        with open(meta_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")


def resolve_file(kind: str, name: str) -> Path | None:
    safe = Path(name).name
    if not (safe.endswith(".mp4") or safe.endswith(".jpg")):
        return None
    base = EVENT_DIR if kind == "event" else CONT_DIR
    path = base / safe
    return path if path.is_file() else None


def purge_old_recordings(cfg: dict[str, Any] | None = None) -> int:
    """Delete full recordings and event clips older than the configured days."""
    _ = cfg
    days = _retention_days(None)
    cutoff = time.time() - days * 86400
    removed = 0
    for folder in (EVENT_DIR, CONT_DIR):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if not path.is_file() or path.name == "events.jsonl":
                continue
            if path.name in _rec_procs:
                continue
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                web = path.with_name(path.stem + ".web.mp4")
                path.unlink(missing_ok=True)
                if web != path:
                    web.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    _prune_event_index()
    return removed


def maybe_purge_recordings() -> None:
    """Apply the configured retention about every 10 minutes."""
    global _last_purge_mono
    now = time.monotonic()
    if now - _last_purge_mono < 600:
        return
    _last_purge_mono = now
    purge_old_recordings()


def _event_still_kept(item: dict[str, Any]) -> bool:
    name = Path(str(item.get("video") or "")).name
    snap = Path(str(item.get("snapshot") or "")).name
    if name and name in _rec_procs:
        return True
    if name and (EVENT_DIR / name).is_file():
        return True
    if snap and (EVENT_DIR / snap).is_file():
        return True
    try:
        when = datetime.strptime(str(item.get("time") or ""), "%Y-%m-%d %H:%M:%S")
        if (datetime.now() - when).total_seconds() < 180:
            return True
    except ValueError:
        pass
    return False


def _prune_event_index() -> None:
    meta = EVENT_DIR / "events.jsonl"
    if not meta.is_file():
        return
    with _lock:
        try:
            lines = meta.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        kept = []
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and _event_still_kept(item):
                kept.append(line)
        if len(kept) == len(lines):
            return
        try:
            tmp = meta.with_suffix(".jsonl.tmp")
            tmp.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
            tmp.replace(meta)
        except OSError:
            pass


def _clamp_retention(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = 1
    return max(1, min(365, days))


def _retention_days(cfg: dict[str, Any] | None = None) -> int:
    """Days to keep both the full recording and event clips. 1 deletes after a day."""
    if cfg is None:
        days = 1
        for item in load_channels():
            if not str(item.get("host") or "").strip():
                continue
            days = max(days, _clamp_retention(item.get("retention_days") or 1))
        return days
    return _clamp_retention((cfg or {}).get("retention_days") or 1)


def _bump_recording() -> None:
    global _cont_gen
    with _lock:
        _cont_gen += 1


def _continuous_active() -> bool:
    with _lock:
        names = list(_rec_procs)
    return any((CONT_DIR / name).is_file() for name in names)


def _disk_low() -> bool:
    try:
        _ensure_dirs()
        return shutil.disk_usage(CONT_DIR).free < 2 * 1024 * 1024 * 1024
    except OSError:
        return False


def _seconds_until_hour_or_cap() -> int:
    """Keep one continuous file open until the next clock hour (capped)."""
    now = datetime.now()
    nxt = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    left = int((nxt - now).total_seconds())
    return max(90, min(_CONT_MAX_SEC, left))


def _finalize_cont_mp4(path: Path) -> None:
    """Remux fragmented continuous file to browser-friendly faststart when possible."""
    if not path.is_file():
        return
    try:
        if path.stat().st_size < 1000:
            return
    except OSError:
        return
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        return
    tmp = path.with_suffix(".fast.mp4")
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(tmp),
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if tmp.is_file() and tmp.stat().st_size > 1000:
            tmp.replace(path)
        elif tmp.is_file():
            tmp.unlink(missing_ok=True)
    except (OSError, subprocess.SubprocessError):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _record_segment(channel: int, gen: int) -> None:
    """Record continuous full stream until hour boundary / config bump (not 10-min clips)."""
    cfg = load_config(channel)
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg or not str(cfg.get("host") or "").strip():
        return
    _ensure_dirs()
    # Avoid stacking encoders if a previous segment failed to exit cleanly.
    with _lock:
        stale = [
            (name, proc)
            for name, proc in list(_rec_procs.items())
            if f"_ch{channel + 1}." in name
        ]
    for name, proc in stale:
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass
        with _lock:
            _rec_procs.pop(name, None)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{stamp}_ch{channel + 1}.mp4"
    path = CONT_DIR / name
    log_path = path.with_suffix(".log")
    # No short -t cut: run until we stop the process. Fragmented MP4 stays recoverable.
    cmd = [
        ffmpeg,
        "-y",
        "-rtsp_transport",
        "tcp",
        "-i",
        rtsp_url(cfg),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-an",
        "-f",
        "mp4",
        "-movflags",
        "+frag_keyframe+empty_moov+default_base_moof",
        str(path),
    ]
    log_fh = None
    proc = None
    rotate_after = _seconds_until_hour_or_cap()
    try:
        log_fh = open(log_path, "w", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=log_fh,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with _lock:
            _rec_procs[name] = proc
        deadline = time.time() + rotate_after + 30
        while proc.poll() is None:
            with _lock:
                changed = _cont_gen != gen
            if changed or time.time() > deadline or _disk_low():
                try:
                    proc.terminate()
                except OSError:
                    proc.kill()
                break
            time.sleep(1)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
    except (OSError, subprocess.SubprocessError):
        if proc is not None and proc.poll() is None:
            proc.kill()
    finally:
        with _lock:
            _rec_procs.pop(name, None)
        if log_fh is not None:
            try:
                log_fh.close()
            except OSError:
                pass
        try:
            if path.is_file() and path.stat().st_size > 1000:
                log_path.unlink(missing_ok=True)
                _finalize_cont_mp4(path)
            elif path.is_file():
                path.unlink(missing_ok=True)
                log_path.unlink(missing_ok=True)
        except OSError:
            pass


def _continuous_loop() -> None:
    purge_old_recordings()
    while True:
        with _lock:
            gen = _cont_gen
        status = continuous_status()
        if status.get("error") in ("ffmpeg_missing", "no_target", "disk_low"):
            time.sleep(15)
            continue
        channels = load_channels()
        targets = [
            index
            for index, cfg in enumerate(channels)
            if cfg.get("enabled")
            and cfg.get("continuous_record", True)
            and str(cfg.get("host") or "").strip()
            and _ffmpeg_bin()
        ]
        if not targets:
            time.sleep(15)
            continue
        workers = [
            threading.Thread(target=_record_segment, args=(index, gen), name=f"oms-cont-{index}", daemon=True)
            for index in targets
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        time.sleep(1)


def _kill_orphan_cont_ffmpeg() -> int:
    """Kill leftover continuous-recording ffmpeg from previous OMS runs.

    OMS restarts leave RTSP encoders alive; stacked copies saturate the camera
    and this PC — live CCTV then loads very slowly.
    """
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='ffmpeg.exe'\" | ForEach-Object {"
        "  $cl = [string]$_.CommandLine;"
        "  if ($cl -match 'cctv_recordings[\\\\/]+continuous') {"
        "    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue"
        "  }"
        "}"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return 1
    except (OSError, subprocess.SubprocessError):
        return 0


def stop_continuous_recording() -> None:
    """Stop tracked continuous/event ffmpeg and orphan encoders writing to CONT_DIR."""
    global _cont_gen
    stop_live_grabbers()
    with _lock:
        _cont_gen += 1
        procs = list(_rec_procs.items())
        _rec_procs.clear()
    for _name, proc in procs:
        try:
            if proc.poll() is None:
                proc.terminate()
        except OSError:
            pass
    for _name, proc in procs:
        try:
            if proc.poll() is None:
                proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass
    _kill_orphan_cont_ffmpeg()


def _warm_live_grabbers() -> None:
    """Open RTSP live pipes early so the first CCTV screen is not a multi-second wait."""
    try:
        channels = load_channels()
    except Exception:  # noqa: BLE001
        return
    for idx, cfg in enumerate(channels):
        if not cfg.get("enabled"):
            continue
        if not str(cfg.get("host") or "").strip():
            continue
        try:
            if _prefer_rtsp_snapshot({**cfg, "_channel": idx}):
                _rtsp_live_snapshot({**cfg, "_channel": idx}, wait_sec=0.05)
        except Exception:  # noqa: BLE001
            pass


def start_continuous_recording() -> None:
    """Keep a continuous full recording while OMS is open (hourly file rotation)."""
    global _cont_started
    with _lock:
        if _cont_started:
            return
        _cont_started = True
    # Drop zombies from prior EXE/bat runs before opening a new RTSP pull.
    _kill_orphan_cont_ffmpeg()
    threading.Thread(target=_warm_live_grabbers, name="oms-live-warm", daemon=True).start()
    threading.Thread(target=_continuous_loop, name="oms-cont-rec", daemon=True).start()


def browser_video(path: Path) -> Path:
    """Return an H.264 MP4 a browser can play. Camera copies are often HEVC."""
    with _browser_lock:
        return _browser_video_locked(path)


def _browser_video_locked(path: Path) -> Path:
    if path.suffix.lower() != ".mp4" or not path.is_file():
        return path
    try:
        if path.stat().st_size < 1000:
            return path
    except OSError:
        return path
    if path.name.endswith(".web.mp4") or path.name in _rec_procs:
        return path
    cached = path.with_name(path.stem + ".web.mp4")
    try:
        src_mtime = path.stat().st_mtime
        if (
            cached.is_file()
            and cached.stat().st_size > 1000
            and cached.stat().st_mtime >= src_mtime
        ):
            return cached
        head = path.read_bytes()[:1_500_000]
    except OSError:
        return path
    if b"avc1" in head or b"avc3" in head:
        return path
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        return path
    cmd = [
        ffmpeg, "-y", "-i", str(path),
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-an", "-movflags", "+faststart", str(cached),
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return path
    try:
        if cached.is_file() and cached.stat().st_size > 1000:
            return cached
    except OSError:
        pass
    return path


def trigger_event_recording(
    reason: str,
    message: str = "",
    locker: str = "",
    slot: int | None = None,
) -> dict[str, Any]:
    global _last_event_mono
    cfg = load_config()
    if not cfg.get("enabled"):
        return {"ok": False, "error": "cctv disabled"}
    reason = str(reason or "event")
    if reason == "emergency" and not cfg.get("auto_event_on_emergency", True):
        return {"ok": False, "error": "emergency recording off"}
    if reason == "unauthorized" and not cfg.get("auto_event_on_unauthorized", True):
        return {"ok": False, "error": "unauthorized recording off"}
    if reason == "offline" and not cfg.get("auto_event_on_offline", False):
        return {"ok": False, "error": "offline recording off"}
    if reason == "rfid_emergency" and not cfg.get("auto_event_on_rfid_emergency", True):
        return {"ok": False, "error": "rfid_emergency recording off"}
    if reason == "rfid_stored":
        # Backward-compatible alias → RFID tagging emergency.
        reason = "rfid_emergency"
        if not cfg.get("auto_event_on_rfid_emergency", True):
            return {"ok": False, "error": "rfid_emergency recording off"}
    now = time.monotonic()
    if now - _last_event_mono < _EVENT_COOLDOWN_SEC:
        return {"ok": False, "error": "cooldown"}
    _last_event_mono = now
    _ensure_dirs()
    purge_old_recordings(cfg)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slot_bit = f"_s{int(slot)}" if slot else ""
    base = f"{stamp}_{reason}{slot_bit}"
    video_name = f"{base}.mp4"
    snap_name = f"{base}.jpg"
    video_path = EVENT_DIR / video_name
    snap_path = EVENT_DIR / snap_name
    try:
        data, _ = fetch_snapshot(cfg)
        snap_path.write_bytes(data)
    except Exception:
        snap_path = None
    meta = {
        "id": base,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reason": reason,
        "message": message or reason,
        "locker": locker,
        "slot": slot,
        "video": video_name,
        "snapshot": snap_name if snap_path else "",
        "snapshot_url": f"/api/cctv/file?kind=event&name={snap_name}" if snap_path else "",
        "video_url": f"/api/cctv/file?kind=event&name={video_name}",
    }
    _append_event_meta(meta)
    ffmpeg = _ffmpeg_bin()
    sec = int(cfg.get("event_record_sec") or 30)
    if not ffmpeg:
        meta["error"] = "ffmpeg missing — snapshot only"
        return {"ok": True, "event": meta, "recording": False}
    url = rtsp_url(cfg)
    cmd = [
        ffmpeg,
        "-y",
        "-rtsp_transport",
        "tcp",
        "-i",
        url,
        "-t",
        str(sec),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-an",
        "-movflags",
        "+faststart",
        str(video_path),
    ]

    def _run() -> None:
        log_path = video_path.with_suffix(".log")
        log_fh = None
        proc = None
        try:
            log_fh = open(log_path, "w", encoding="utf-8", errors="replace")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=log_fh,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            with _lock:
                _rec_procs[video_name] = proc
            proc.wait(timeout=sec + 45)
        except subprocess.TimeoutExpired:
            if proc is not None:
                proc.kill()
        except Exception:
            pass
        finally:
            with _lock:
                _rec_procs.pop(video_name, None)
            if log_fh is not None:
                try:
                    log_fh.close()
                except OSError:
                    pass
            try:
                if video_path.is_file() and video_path.stat().st_size > 1000:
                    log_path.unlink(missing_ok=True)
            except OSError:
                pass

    threading.Thread(target=_run, daemon=True).start()
    if cfg.get("auto_ptz_on_event", True):
        def _aim() -> None:
            try:
                ptz_goto_event(reason)
            except Exception:
                pass

        threading.Thread(target=_aim, daemon=True).start()
    return {"ok": True, "event": meta, "recording": True, "seconds": sec}


def snapshot_bytes(channel: int = 0) -> tuple[bytes, str]:
    return fetch_snapshot(load_config(channel))


def _ptz_paths(cfg: dict[str, Any]) -> list[str]:
    primary = str(cfg.get("onvif_ptz_path") or "/onvif/PTZ").strip() or "/onvif/PTZ"
    device = str(cfg.get("onvif_device_path") or "/onvif/device_service").strip() or "/onvif/device_service"
    media = str(cfg.get("onvif_media_path") or "").strip()
    alts = [primary, device, media, "/onvif/PTZ", "/onvif/ptz", "/onvif/ptz_service", "/onvif/device_service"]
    out: list[str] = []
    for path in alts:
        if not path:
            continue
        if not path.startswith("/"):
            path = "/" + path
        if path not in out:
            out.append(path)
    return out


def _ptz_profile(cfg: dict[str, Any]) -> tuple[str, str]:
    """Return (base_url, profile_token) for PTZ calls."""
    disc = onvif_discover(cfg)
    base = str(disc.get("base") or _base_url(cfg))
    token = str(disc.get("profile") or cfg.get("onvif_profile") or "").strip()
    return base, token


def _ptz_soap(cfg: dict[str, Any], body_inner: str, action: str) -> tuple[bool, str]:
    base, token = _ptz_profile(cfg)
    if not token:
        return False, "ONVIF 프로필을 찾지 못했습니다."
    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body>{body_inner}</s:Body>
</s:Envelope>"""
    errors: list[str] = []
    for path in _ptz_paths(cfg):
        try:
            text = _soap(cfg, base, path, envelope.replace("__PROFILE__", token), action)
            if "Fault" in text and "soap" in text.lower():
                errors.append(f"{path}: SOAP Fault")
                continue
            return True, text
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path}: {exc}")
    return False, "; ".join(errors) or "PTZ 실패"


# host -> (pan sign, tilt sign, sticky PRESETn label).
# FS-IR / IP_VIDEO: ONVIF +pan/+tilt are reversed vs standard (right/up).
_ptz_quirk_cache: dict[str, tuple[float, float, bool]] = {}


def _invert_flag(value: Any) -> float | None:
    """True → reverse (-1), False → normal (+1), unset → None (auto)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return -1.0 if value else 1.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return -1.0 if value else 1.0
    text = str(value).strip().lower()
    if text in ("", "auto", "none", "null"):
        return None
    if text in ("1", "true", "yes", "on"):
        return -1.0
    if text in ("0", "false", "no", "off"):
        return 1.0
    return None


def _ptz_quirks(cfg: dict[str, Any]) -> tuple[float, float, bool]:
    """Return (pan velocity sign, tilt velocity sign, clear PRESETn OSD after GotoPreset)."""
    forced_pan = _invert_flag(cfg.get("ptz_pan_invert"))
    forced_tilt = _invert_flag(cfg.get("ptz_tilt_invert"))
    host = str(cfg.get("host") or "").strip()
    cached = _ptz_quirk_cache.get(host)
    if cached is None:
        pan_sign, tilt_sign, sticky = 1.0, 1.0, False
        try:
            base = _base_url(cfg)
            path = str(cfg.get("onvif_device_path") or "/onvif/device_service")
            env = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body><GetDeviceInformation xmlns="http://www.onvif.org/ver10/device/wsdl"/></s:Body>
</s:Envelope>"""
            text = _soap(cfg, base, path, env, "http://www.onvif.org/ver10/device/wsdl/GetDeviceInformation")
            manu = re.search(r"<[^:>]*:?Manufacturer>([^<]*)</", text, flags=re.I)
            model = re.search(r"<[^:>]*:?Model>([^<]*)</", text, flags=re.I)
            manu_s = (manu.group(1) if manu else "").strip().upper()
            model_s = (model.group(1) if model else "").strip().upper()
            # FS-IR307A: pan/tilt follow ONVIF (+pan right, +tilt up). Only PRESETn OSD is sticky.
            if manu_s == "IP_VIDEO" or model_s.startswith("FS-"):
                pan_sign, tilt_sign, sticky = 1.0, 1.0, True
        except Exception:
            pan_sign, tilt_sign, sticky = 1.0, 1.0, False
        if host:
            _ptz_quirk_cache[host] = (pan_sign, tilt_sign, sticky)
        cached = (pan_sign, tilt_sign, sticky)
    pan_sign, tilt_sign, sticky = cached
    if forced_pan is not None:
        pan_sign = forced_pan
    if forced_tilt is not None:
        tilt_sign = forced_tilt
    return pan_sign, tilt_sign, sticky


def ptz_move(pan: float = 0.0, tilt: float = 0.0, zoom: float = 0.0, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """ONVIF ContinuousMove. Values roughly -1.0 .. 1.0. +pan=right, +tilt=up (logical)."""
    cfg = cfg or load_config()
    speed = float(cfg.get("ptz_speed") or 0.45)
    pan = max(-1.0, min(1.0, float(pan) * speed))
    tilt = max(-1.0, min(1.0, float(tilt) * speed))
    zoom = max(-1.0, min(1.0, float(zoom) * speed))
    pan_sign, tilt_sign, _sticky = _ptz_quirks(cfg)
    sent_pan = pan * pan_sign
    sent_tilt = tilt * tilt_sign
    inner = f"""
    <ContinuousMove xmlns="http://www.onvif.org/ver20/ptz/wsdl">
      <ProfileToken>__PROFILE__</ProfileToken>
      <Velocity>
        <PanTilt xmlns="http://www.onvif.org/ver10/schema" x="{sent_pan:.3f}" y="{sent_tilt:.3f}"
          space="http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace"/>
        <Zoom xmlns="http://www.onvif.org/ver10/schema" x="{zoom:.3f}"
          space="http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace"/>
      </Velocity>
    </ContinuousMove>"""
    ok, detail = _ptz_soap(cfg, inner, "http://www.onvif.org/ver20/ptz/wsdl/ContinuousMove")
    return {"ok": ok, "detail": "이동 중" if ok else detail, "pan": pan, "tilt": tilt, "zoom": zoom}


def ptz_stop(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    inner = """
    <Stop xmlns="http://www.onvif.org/ver20/ptz/wsdl">
      <ProfileToken>__PROFILE__</ProfileToken>
      <PanTilt>true</PanTilt>
      <Zoom>true</Zoom>
    </Stop>"""
    ok, detail = _ptz_soap(cfg, inner, "http://www.onvif.org/ver20/ptz/wsdl/Stop")
    return {"ok": ok, "detail": "정지" if ok else detail}


def ptz_list_presets(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    inner = """
    <GetPresets xmlns="http://www.onvif.org/ver20/ptz/wsdl">
      <ProfileToken>__PROFILE__</ProfileToken>
    </GetPresets>"""
    ok, text = _ptz_soap(cfg, inner, "http://www.onvif.org/ver20/ptz/wsdl/GetPresets")
    presets: list[dict[str, str]] = []
    if ok:
        # Cameras use <tptz:Preset token="..."> or <PTZPreset token="...">
        for m in re.finditer(
            r"<[^:>]*:?(?:PTZ)?Preset\b[^>]*\btoken=\"([^\"]+)\"[^>]*>(.*?)</[^:>]*:?(?:PTZ)?Preset>",
            text,
            flags=re.I | re.S,
        ):
            token = m.group(1).strip()
            name_m = re.search(r"<[^:>]*:?Name>([^<]*)</", m.group(2), flags=re.I)
            name = (name_m.group(1).strip() if name_m else token) or token
            presets.append({"token": token, "name": name})
        if not presets:
            for token in re.findall(
                r"<[^:>]*:?(?:PTZ)?Preset\b[^>]*\btoken=\"([^\"]+)\"",
                text,
                flags=re.I,
            ):
                presets.append({"token": token, "name": token})
    return {"ok": ok, "presets": presets, "detail": "" if ok else text}


def _wait_ptz_idle(cfg: dict[str, Any], timeout: float = 12.0) -> None:
    """Wait until a GotoPreset has started and then finished.

    Status stays IDLE for a moment after the command is accepted. Clearing the
    overlay in that gap cancels the move.
    """
    inner = """
    <GetStatus xmlns="http://www.onvif.org/ver20/ptz/wsdl">
      <ProfileToken>__PROFILE__</ProfileToken>
    </GetStatus>"""
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    saw_move = False
    while time.monotonic() < deadline:
        ok, text = _ptz_soap(cfg, inner, "http://www.onvif.org/ver20/ptz/wsdl/GetStatus")
        if not ok:
            return
        if re.search(r"PanTilt>\s*MOVING\s*<", text, flags=re.I):
            saw_move = True
        elif saw_move and re.search(r"PanTilt>\s*IDLE\s*<", text, flags=re.I):
            return
        elif (not saw_move) and (time.monotonic() - started) > 2.5 and re.search(r"PanTilt>\s*IDLE\s*<", text, flags=re.I):
            return
        time.sleep(0.25)


def _clear_sticky_preset_label(cfg: dict[str, Any]) -> None:
    """FS domes keep 'PRESETn' on the picture after GotoPreset.

    A short pan away and back drops the overlay. GotoPreset itself would paint it again.
    """
    ptz_move(pan=0.35, tilt=0.0, zoom=0.0, cfg=cfg)
    time.sleep(0.22)
    ptz_stop(cfg)
    time.sleep(0.15)
    ptz_move(pan=-0.35, tilt=0.0, zoom=0.0, cfg=cfg)
    time.sleep(0.22)
    ptz_stop(cfg)


def ptz_goto_preset(token: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    token = str(token or "").strip()
    if not token:
        return {"ok": False, "detail": "프리셋 토큰이 없습니다."}
    # escape XML special chars lightly
    safe = token.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    inner = f"""
    <GotoPreset xmlns="http://www.onvif.org/ver20/ptz/wsdl">
      <ProfileToken>__PROFILE__</ProfileToken>
      <PresetToken>{safe}</PresetToken>
    </GotoPreset>"""
    ok, detail = _ptz_soap(cfg, inner, "http://www.onvif.org/ver20/ptz/wsdl/GotoPreset")
    if ok and _ptz_quirks(cfg)[2]:
        try:
            _wait_ptz_idle(cfg)
            _clear_sticky_preset_label(cfg)
        except Exception:
            pass
    return {"ok": ok, "detail": f"프리셋 {token}" if ok else detail, "token": token}


def ptz_set_preset(name: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Save current PTZ position as a named preset. Returns token if possible."""
    cfg = cfg or load_config()
    name = str(name or "").strip() or f"OMS_{int(time.time())}"
    safe = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    inner = f"""
    <SetPreset xmlns="http://www.onvif.org/ver20/ptz/wsdl">
      <ProfileToken>__PROFILE__</ProfileToken>
      <PresetName>{safe}</PresetName>
    </SetPreset>"""
    ok, text = _ptz_soap(cfg, inner, "http://www.onvif.org/ver20/ptz/wsdl/SetPreset")
    token = ""
    if ok:
        m = re.search(r"<[^:>]*:?PresetToken>([^<]+)</", text, flags=re.I)
        if m:
            token = m.group(1).strip()
    return {"ok": ok, "detail": "프리셋 저장" if ok else text, "token": token or name, "name": name}


def _situation_token(situation: int, cfg: dict[str, Any] | None = None) -> str:
    """Preset token for a situation. Saved binding first, then a camera preset named SIT_n."""
    cfg = cfg or load_config()
    try:
        sit = int(situation)
    except (TypeError, ValueError):
        return ""
    presets = cfg.get("ptz_presets") if isinstance(cfg.get("ptz_presets"), dict) else {}
    token = str((presets or {}).get(str(sit)) or "").strip()
    if token:
        return token
    listed = ptz_list_presets(cfg)
    want = f"SIT_{sit}"
    for item in listed.get("presets") or []:
        if str(item.get("name") or "").strip().upper() == want:
            return str(item.get("token") or "").strip()
    return ""


def ptz_goto_situation(situation: int, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Move camera to the preset registered for situation 1~10."""
    cfg = cfg or load_config()
    try:
        sit = int(situation)
    except (TypeError, ValueError):
        return {"ok": False, "detail": "상황 번호가 올바르지 않습니다."}
    if sit < 1 or sit > 10:
        return {"ok": False, "detail": "상황 번호는 1~10 입니다."}
    token = _situation_token(sit, cfg)
    if not token:
        return {"ok": False, "detail": f"상황{sit} PTZ가 없습니다. 설정에서 상황{sit}「지금위치저장」을 하세요."}
    result = ptz_goto_preset(token, cfg)
    if result.get("ok"):
        hint = PTZ_SITUATION_HINTS.get(sit, "")
        result["detail"] = f"상황{sit}{' · ' + hint if hint else ''} (프리셋 {token})"
        result["situation"] = sit
    return result


def open_situation(detected_at: float | None = None) -> bool:
    """Allow PTZ to leave Home for a new situation. A situation already confirmed stays Home."""
    global _aim_wall
    _ensure_ptz_bus()
    when = float(detected_at or time.time())
    with _ptz_guard:
        if _home_wall and _home_wall >= when - 2.0:
            return False
        _aim_wall = max(_aim_wall, when)
        return True


def situation_hold_home() -> bool:
    """True after any OMS confirmed and no newer situation has opened."""
    with _ptz_guard:
        return _home_wall > 0 and _home_wall >= _aim_wall


def release_ptz_home() -> None:
    """One confirmation sends the camera Home and tells every other OMS."""
    global _home_wall
    _ensure_ptz_bus()
    wall = time.time()
    with _ptz_guard:
        _home_wall = max(_home_wall, wall)
    _broadcast_ptz({"op": "home", "wall": wall})
    threading.Thread(target=ptz_home, daemon=True).start()


def _apply_remote_home(wall: float) -> None:
    global _home_wall
    with _ptz_guard:
        if wall <= _home_wall:
            return
        _home_wall = wall
    try:
        ptz_home()
    except Exception:
        pass


def _broadcast_ptz(payload: dict[str, Any]) -> None:
    body = dict(payload)
    body["src"] = _ptz_instance
    raw = b"OMS-PTZ1" + json.dumps(body).encode("utf-8")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(raw, ("255.255.255.255", _PTZ_BUS_PORT))
        sock.close()
    except OSError:
        pass


def _ptz_bus_loop() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", _PTZ_BUS_PORT))
    except OSError:
        sock.close()
        return
    sock.settimeout(1.0)
    while True:
        try:
            data, _addr = sock.recvfrom(512)
        except socket.timeout:
            continue
        except OSError:
            return
        if not data.startswith(b"OMS-PTZ1"):
            continue
        try:
            msg = json.loads(data[len(b"OMS-PTZ1"):].decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(msg, dict) or msg.get("src") == _ptz_instance:
            continue
        if msg.get("op") == "home":
            try:
                _apply_remote_home(float(msg.get("wall") or 0))
            except (TypeError, ValueError):
                continue


def _ensure_ptz_bus() -> None:
    global _ptz_bus_started
    with _ptz_guard:
        if _ptz_bus_started:
            return
        _ptz_bus_started = True
    threading.Thread(target=_ptz_bus_loop, name="oms-ptz-bus", daemon=True).start()


_LOCKER_AIM = {
    "emergency": ("E", "비상개폐"),
    "unauthorized": ("U", "비인가 접근"),
    "ops": ("O", "보관·불출"),
    "rfid_emergency": ("R", "RFID 태깅 비상"),
}


def _locker_preset_name(locker_id: str, kind: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]", "", str(locker_id or ""))[:16] or "X"
    mark = _LOCKER_AIM.get(kind, _LOCKER_AIM["emergency"])[0]
    return f"L{safe}{mark}"


def _token_by_preset_name(name: str, cfg: dict[str, Any]) -> str:
    want = str(name or "").strip().upper()
    if not want:
        return ""
    try:
        listed = ptz_list_presets(cfg)
    except Exception:
        return ""
    for item in listed.get("presets") or []:
        if str(item.get("name") or "").strip().upper() == want:
            return str(item.get("token") or "").strip()
    return ""


def _locker_event_token(cfg: dict[str, Any], locker_id: str, kind: str, locker_no: int = 0) -> str:
    """Preset for this locker's emergency, unauthorized, or issue/store view."""
    aim = kind if kind in _LOCKER_AIM else "emergency"
    book = cfg.get("ptz_lockers") if isinstance(cfg.get("ptz_lockers"), dict) else {}
    row = book.get(str(locker_id or "")) if isinstance(book.get(str(locker_id or "")), dict) else {}
    token = str((row or {}).get(aim) or "").strip()
    if not token and aim != "emergency":
        token = str((row or {}).get("emergency") or "").strip()
    if token:
        return token
    # 첫 보관함은 예전에 저장한 상황 1(보관함 위치)을 그대로 쓴다.
    if int(locker_no or 0) == 1 or not locker_id:
        legacy = _situation_token(1, cfg)
        if legacy:
            return legacy
    if not locker_id:
        return ""
    named = _token_by_preset_name(_locker_preset_name(locker_id, aim), cfg)
    if not named and aim != "emergency":
        named = _token_by_preset_name(_locker_preset_name(locker_id, "emergency"), cfg)
    return named


def ptz_bind_locker(locker_id: str, kind: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Save the current camera pose for one locker's emergency, unauthorized, or issue/store view."""
    lid = str(locker_id or "").strip()
    aim = str(kind or "").strip().lower()
    if aim not in _LOCKER_AIM:
        aim = "emergency"
    if not lid:
        return {"ok": False, "detail": "보관함 정보가 없습니다."}
    cfg = cfg or load_config()
    result = ptz_set_preset(_locker_preset_name(lid, aim), cfg)
    token = str(result.get("token") or "").strip()
    if not result.get("ok") or not token:
        return {"ok": False, "detail": result.get("detail") or "프리셋 저장 실패"}
    book = dict(cfg.get("ptz_lockers") or {}) if isinstance(cfg.get("ptz_lockers"), dict) else {}
    row = dict(book.get(lid) or {}) if isinstance(book.get(lid), dict) else {}
    row[aim] = token
    book[lid] = row
    save_config({"ptz_lockers": book}, channel=_channel_of(cfg))
    label = _LOCKER_AIM[aim][1]
    return {"ok": True, "locker_id": lid, "kind": aim, "token": token, "detail": f"{label} 위치 등록 완료 (프리셋 {token})"}


def ptz_goto_event(
    reason: str,
    cfg: dict[str, Any] | None = None,
    locker_id: str = "",
    locker_no: int = 0,
) -> dict[str, Any]:
    """Map event kind to a camera pose. Locker events use that locker's saved view."""
    cfg = cfg or load_config()
    kind = str(reason or "").strip().lower() or "manual"
    if kind == "unreturned":
        return {"ok": False, "detail": "미반납 상황과 알림은 사용하지 않습니다."}
    if kind != "manual" and situation_hold_home():
        return {"ok": True, "detail": "다른 화면에서 확인해 Home으로 돌아갑니다.", "homed": True}
    if kind in ("rfid_stored",):
        kind = "rfid_emergency"
    if kind in ("emergency", "unauthorized", "ops", "rfid_emergency"):
        token = _locker_event_token(cfg, locker_id, kind, locker_no)
        if not token and kind == "rfid_emergency":
            # Fall back to emergency pose, then mapped situation 6.
            token = _locker_event_token(cfg, locker_id, "emergency", locker_no)
        if token:
            result = ptz_goto_preset(token, cfg)
            if result.get("ok"):
                result["detail"] = f"보관함 위치 (프리셋 {token})"
            if kind != "manual" and situation_hold_home():
                try:
                    ptz_home(cfg)
                except Exception:
                    pass
                return {"ok": True, "detail": "확인되어 Home으로 돌아갑니다.", "homed": True}
            return result
        if kind != "rfid_emergency":
            return {"ok": False, "detail": "이 보관함 팬틸트 위치가 없습니다. 설정에서 지금위치저장을 하세요."}
    event_map = cfg.get("ptz_event_map") if isinstance(cfg.get("ptz_event_map"), dict) else {}
    if not event_map:
        event_map = DEFAULTS["ptz_event_map"]
    sit = event_map.get(kind)
    if sit is None and kind == "rfid_emergency":
        sit = event_map.get("rfid_stored", event_map.get("emergency", 6))
    if sit is None and kind == "ops":
        sit = event_map.get("emergency", 1)
    if kind in ("unauthorized", "ops", "rfid_emergency") and sit is not None:
        # 비인가·불출/보관은 해당 보관함. 전용 위치가 없으면 비상개폐(보관함) 위치를 쓴다.
        try:
            locker_sit = int(event_map.get("emergency", 1))
            if int(sit) != locker_sit and not _situation_token(int(sit), cfg):
                sit = locker_sit
        except (TypeError, ValueError):
            sit = event_map.get("emergency", 1)
    if sit is None:
        # allow numeric reason as situation
        try:
            sit = int(kind)
        except (TypeError, ValueError):
            sit = None
    if sit is None:
        return {"ok": False, "detail": f"이벤트 '{kind}'에 연결된 상황이 없습니다."}
    result = ptz_goto_situation(int(sit), cfg)
    if kind != "manual" and situation_hold_home():
        try:
            ptz_home(cfg)
        except Exception:
            pass
        return {"ok": True, "detail": "확인되어 Home으로 돌아갑니다.", "homed": True}
    return result


def ptz_goto_locker(locker: str = "", slot: int | None = None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Deprecated slot aiming — kept for API compat; prefer ptz_goto_event / ptz_goto_situation."""
    _ = locker
    if slot is None:
        return {"ok": False, "detail": "상황 기반 PTZ를 사용하세요 (goto_event / goto_situation)."}
    # Best-effort: treat slot number as situation number if 1~10
    try:
        sit = int(slot)
    except (TypeError, ValueError):
        return {"ok": False, "detail": "잘못된 번호"}
    return ptz_goto_situation(sit, cfg)


def ptz_bind_situation(situation: int, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Save current PTZ pose as this situation (1~10)."""
    try:
        sit = int(situation)
    except (TypeError, ValueError):
        return {"ok": False, "detail": "상황 번호가 올바르지 않습니다."}
    if sit < 1 or sit > 10:
        return {"ok": False, "detail": "상황 번호는 1~10 입니다."}
    cfg = cfg or load_config()
    result = ptz_set_preset(f"SIT_{sit}", cfg)
    token = str(result.get("token") or "").strip()
    if not result.get("ok") or not token:
        return {"ok": False, "detail": result.get("detail") or "프리셋 저장 실패"}
    presets = dict(cfg.get("ptz_presets") or {}) if isinstance(cfg.get("ptz_presets"), dict) else {}
    presets[str(sit)] = token
    save_config({"ptz_presets": presets}, channel=_channel_of(cfg))
    hint = PTZ_SITUATION_HINTS.get(sit, "")
    return {
        "ok": True,
        "situation": sit,
        "token": token,
        "detail": f"상황{sit}{'(' + hint + ')' if hint else ''} 위치 등록 완료 (프리셋 {token})",
    }


def ptz_bind_slot(slot: int, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compat alias → situation bind."""
    return ptz_bind_situation(slot, cfg)


def ptz_home(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return camera to home / original position.

    Many cameras (including this unit) reject GotoHomePosition (HTTP 400).
    Prefer a saved ONVIF preset token instead.
    """
    cfg = cfg or load_config()
    token = str(cfg.get("ptz_home_preset") or "").strip()
    presets_map = cfg.get("ptz_presets") if isinstance(cfg.get("ptz_presets"), dict) else {}
    if not token and isinstance(presets_map, dict):
        token = str(presets_map.get("home") or presets_map.get("0") or "").strip()
    if not token:
        listed = ptz_list_presets(cfg)
        for item in listed.get("presets") or []:
            name = str(item.get("name") or "").strip().upper()
            if name in ("OMS_HOME", "HOME", "원위치", "HOME_POSITION"):
                token = str(item.get("token") or "").strip()
                break
    if token:
        result = ptz_goto_preset(token, cfg)
        if result.get("ok"):
            result["detail"] = f"원래 위치(프리셋 {token})"
            return result
    # Last resort: native GotoHomePosition (often unsupported → 400)
    inner = """
    <GotoHomePosition xmlns="http://www.onvif.org/ver20/ptz/wsdl">
      <ProfileToken>__PROFILE__</ProfileToken>
    </GotoHomePosition>"""
    ok, _detail = _ptz_soap(cfg, inner, "http://www.onvif.org/ver20/ptz/wsdl/GotoHomePosition")
    if ok:
        return {"ok": True, "detail": "원래 위치(Home)로 이동"}
    return {
        "ok": False,
        "detail": "Home이 아직 등록되지 않았습니다. PTZ로 원위치를 맞춘 뒤「Home저장」을 누르세요.",
    }


def ptz_set_home(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Save current PTZ pose as Home via SetPreset (GotoHomePosition unsupported on many cams)."""
    cfg = cfg or load_config()
    # Prefer SetPreset — reliable on this Pelco/ONVIF camera
    result = ptz_set_preset("OMS_HOME", cfg)
    token = str(result.get("token") or "").strip()
    if result.get("ok") and token:
        save_config({"ptz_home_preset": token}, channel=_channel_of(cfg))
        return {"ok": True, "detail": f"현재 위치를 Home으로 저장 (프리셋 {token})", "token": token}
    # Optional native SetHomePosition
    inner = """
    <SetHomePosition xmlns="http://www.onvif.org/ver20/ptz/wsdl">
      <ProfileToken>__PROFILE__</ProfileToken>
    </SetHomePosition>"""
    ok, detail = _ptz_soap(cfg, inner, "http://www.onvif.org/ver20/ptz/wsdl/SetHomePosition")
    if ok:
        return {"ok": True, "detail": "현재 위치를 Home으로 저장"}
    return {"ok": False, "detail": result.get("detail") or detail or "Home 저장 실패"}
