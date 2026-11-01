"""Local on-device person detector for OMS (MediaPipe EfficientDet).

Runs in a daemon thread at low FPS using CCTV JPEG snapshots (not a second RTSP
stream), then calls Store.notify_person_enter → yellow Patlite / PTZ situation 4.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_started = False
_stop = threading.Event()
_thread: threading.Thread | None = None

_status: dict[str, Any] = {
    "enabled": False,
    "running": False,
    "backend": "",
    "model": "",
    "error": "",
    "last_check": 0.0,
    "last_person": 0.0,
    "last_score": 0.0,
    "last_channel": 0,
    "frames": 0,
    "hits": 0,
}

# Interval between snapshot checks (seconds). Snappy re-detect after ack.
_INTERVAL_SEC = 0.7
_SCORE_MIN = 0.40
_HIT_STREAK = 1  # fire on first confident person frame
_CLEAR_STREAK = 2  # empty frames required after ops need_clear before re-arm


def _oms_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _ensure_deps_path() -> None:
    candidates = [_oms_root() / "_deps"]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "_deps")
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass))
        candidates.append(Path(meipass) / "_deps")
    for deps in candidates:
        if deps.is_dir() and str(deps) not in sys.path:
            sys.path.insert(0, str(deps))


def _ascii_model_cache(src: Path) -> Path:
    """MediaPipe/TFLite native loader fails on non-ASCII Windows paths (e.g. C:\\Users\\한글\\...).

    Copy the bundled model once into an ASCII-only ProgramData folder and load from there.
    """
    import os
    import shutil

    root = Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData") / "ArmoryOMS" / "ai"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        root = Path(os.environ.get("TEMP") or ".") / "ArmoryOMS_ai"
        root.mkdir(parents=True, exist_ok=True)
    dst = root / src.name
    try:
        need = (not dst.is_file()) or dst.stat().st_size != src.stat().st_size
        if need:
            shutil.copy2(src, dst)
    except OSError:
        return src
    return dst if dst.is_file() else src


def _model_path() -> Path:
    name = Path("assets") / "ai" / "efficientdet_lite0.tflite"
    candidates = [_oms_root() / name]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.insert(0, Path(meipass) / name)
    found: Path | None = None
    for path in candidates:
        if path.is_file():
            found = path
            break
    if found is None:
        return candidates[0]
    # Prefer ASCII path so native TFLite can open the file on Korean-profile PCs.
    try:
        str(found).encode("ascii")
        return found
    except UnicodeEncodeError:
        return _ascii_model_cache(found)


def status() -> dict[str, Any]:
    with _lock:
        out = dict(_status)
    out["interval_sec"] = _INTERVAL_SEC
    out["score_min"] = _SCORE_MIN
    out["model_file"] = str(_model_path())
    out["model_ready"] = _model_path().is_file()
    return out


def _set(**kwargs: Any) -> None:
    with _lock:
        _status.update(kwargs)


def _build_detector() -> tuple[Any, str]:
    _ensure_deps_path()
    model = _model_path()
    if not model.is_file():
        raise FileNotFoundError(f"AI model missing: {model}")
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

    base = python.BaseOptions(model_asset_path=str(model))
    opts = vision.ObjectDetectorOptions(
        base_options=base,
        score_threshold=float(_SCORE_MIN),
        max_results=8,
        category_allowlist=["person"],
    )
    detector = vision.ObjectDetector.create_from_options(opts)
    return (detector, mp), f"mediapipe-efficientdet-lite0"


def _decode_jpeg(data: bytes) -> Any:
    _ensure_deps_path()
    import cv2
    import numpy as np

    arr = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError("JPEG decode failed")
    # Downscale large frames for CPU friendliness.
    h, w = frame.shape[:2]
    max_side = 640
    if max(h, w) > max_side:
        scale = max_side / float(max(h, w))
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return rgb


def _best_person_score(detector: Any, mp: Any, rgb: Any) -> float:
    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = detector.detect(image)
    best = 0.0
    for det in result.detections or []:
        cats = list(det.categories or [])
        if not cats:
            continue
        name = str(cats[0].category_name or "").strip().lower()
        score = float(cats[0].score or 0.0)
        if name == "person":
            best = max(best, score)
    return best


def _notify(channel: int, score: float) -> None:
    try:
        from core import STORE

        _ = score
        STORE.notify_person_enter(
            "무기고에 사람이 출입했습니다.",
            source="person-ai",
            channel=max(0, int(channel)),
        )
    except Exception as exc:  # noqa: BLE001
        _set(error=f"notify failed: {exc}")


def _loop() -> None:
    present_streak = 0
    clear_streak = 0
    try:
        pair, backend = _build_detector()
        detector, mp = pair
        _set(backend=backend, model=str(_model_path().name), error="", running=True)
    except Exception as exc:  # noqa: BLE001
        _set(running=False, error=f"AI init failed: {exc}", backend="")
        return

    while not _stop.is_set():
        t0 = time.monotonic()
        try:
            from core import STORE
            from cctv import fetch_snapshot, load_channels

            # 수불(지문 절차) 중이거나 황색 출입 알림이 열려 있으면 감지 안 함.
            if STORE.person_ai_blocked():
                present_streak = 0
                clear_streak = 0
                _set(enabled=False, error="", last_check=time.time())
                _stop.wait(_INTERVAL_SEC)
                continue

            channels = load_channels()
            targets = [
                (i, cfg)
                for i, cfg in enumerate(channels)
                if cfg.get("enabled")
                and cfg.get("person_ai_enabled", True)
                and str(cfg.get("host") or "").strip()
            ]
            _set(enabled=bool(targets))
            if not targets:
                present_streak = 0
                _stop.wait(_INTERVAL_SEC)
                continue

            # Prefer channel 1 (index 0); otherwise first enabled AI channel.
            channel, cfg = targets[0]
            jpeg, _ctype = fetch_snapshot(cfg)
            rgb = _decode_jpeg(jpeg)
            score = _best_person_score(detector, mp, rgb)
            frames = 0
            with _lock:
                frames = int(_status.get("frames") or 0) + 1
            present = score >= _SCORE_MIN
            _set(
                last_check=time.time(),
                last_score=round(score, 3),
                last_channel=channel + 1,
                frames=frames,
                error="",
            )

            if present:
                present_streak += 1
                clear_streak = 0
            else:
                present_streak = 0
                clear_streak += 1
                # 확인 후 같은 사람이 화면에서 사라질 때까지 재감지 보류.
                if STORE.person_ai_needs_clear() and clear_streak >= _CLEAR_STREAK:
                    STORE.person_ai_mark_clear()
                    clear_streak = 0

            if STORE.person_ai_needs_clear():
                # Still waiting for empty frame — do not fire.
                pass
            elif present_streak >= _HIT_STREAK:
                present_streak = 0
                with _lock:
                    _status["hits"] = int(_status.get("hits") or 0) + 1
                    _status["last_person"] = time.time()
                _notify(channel, score)
        except Exception as exc:  # noqa: BLE001
            present_streak = 0
            _set(error=str(exc)[:240], last_check=time.time())

        elapsed = time.monotonic() - t0
        _stop.wait(max(0.4, _INTERVAL_SEC - elapsed))

    _set(running=False)


def start_person_ai() -> None:
    """Start background person detector once per OMS process."""
    global _started, _thread
    with _lock:
        if _started:
            return
        _started = True
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="oms-person-ai", daemon=True)
        _thread.start()
        _status["running"] = True


def stop_person_ai() -> None:
    global _started
    _stop.set()
    with _lock:
        _started = False
        _status["running"] = False
