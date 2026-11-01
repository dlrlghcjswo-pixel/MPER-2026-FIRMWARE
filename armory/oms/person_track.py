"""PTZ follow while a person-alert session is active.

Uses JPEG snapshots + EfficientDet bbox center → ONVIF ContinuousMove.
Stops on ack (caller) or when tracking is cleared.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_active = False
_thread: threading.Thread | None = None
_stop = threading.Event()

_INTERVAL = 0.28
_DEADZONE = 0.08
_SPEED = 0.38
_PULSE = 0.12  # short burst; long ContinuousMove overshoots on FS-IR


def tracking_active() -> bool:
    with _lock:
        return bool(_active)


def start_person_track(channel: int = 0) -> None:
    global _active, _thread
    with _lock:
        if _active:
            return
        _active = True
        _stop.clear()
        _thread = threading.Thread(
            target=_loop,
            args=(max(0, int(channel)),),
            name="oms-person-track",
            daemon=True,
        )
        _thread.start()


def stop_person_track(*, home: bool = False) -> None:
    global _active
    _stop.set()
    with _lock:
        _active = False
    try:
        from cctv import ptz_stop

        ptz_stop()
    except Exception:
        pass
    if home:
        try:
            from core import STORE

            STORE._home_if_no_alarm()
        except Exception:
            pass


def _person_center(detector: Any, mp: Any, rgb: Any) -> tuple[float, float, float] | None:
    """Return (cx, cy, score) in 0..1 image coords, or None."""
    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = detector.detect(image)
    best: tuple[float, float, float] | None = None
    for det in result.detections or []:
        cats = list(det.categories or [])
        if not cats:
            continue
        name = str(cats[0].category_name or "").strip().lower()
        score = float(cats[0].score or 0.0)
        if name != "person" or score < 0.35:
            continue
        box = det.bounding_box
        if box is None:
            continue
        w = float(getattr(rgb, "shape", [0, 0])[1] or 1)
        h = float(getattr(rgb, "shape", [0, 0])[0] or 1)
        ox = float(getattr(box, "origin_x", 0) or 0)
        oy = float(getattr(box, "origin_y", 0) or 0)
        bw = float(getattr(box, "width", 0) or 0)
        bh = float(getattr(box, "height", 0) or 0)
        if w <= 1 or h <= 1 or bw <= 0 or bh <= 0:
            continue
        cx = (ox + bw / 2.0) / w
        cy = (oy + bh / 2.0) / h
        if best is None or score > best[2]:
            best = (cx, cy, score)
    return best


def _fresh_jpeg(cfg: dict[str, Any]) -> bytes:
    """Prefer the RTSP live grabber's newest frame (low latency)."""
    from cctv import fetch_snapshot

    jpeg, _ = fetch_snapshot(cfg)
    return jpeg


def _loop(channel: int) -> None:
    detector = None
    mp = None
    try:
        from person_ai import _build_detector, _decode_jpeg

        pair, _backend = _build_detector()
        detector, mp = pair
    except Exception:
        stop_person_track(home=False)
        return

    miss = 0
    while not _stop.is_set():
        t0 = time.monotonic()
        try:
            from cctv import load_config, ptz_move, ptz_stop
            from person_ai import _decode_jpeg

            cfg = load_config(channel) or load_config(0)
            jpeg = _fresh_jpeg(cfg)
            rgb = _decode_jpeg(jpeg)
            center = _person_center(detector, mp, rgb)
            if center is None:
                miss += 1
                ptz_stop(cfg)
                if miss >= 10:
                    break
                _stop.wait(_INTERVAL)
                continue
            miss = 0
            cx, cy, _score = center
            # Logical ONVIF: +pan = right, +tilt = up. cctv.ptz_move applies tilt quirks.
            err_x = cx - 0.5
            err_y = cy - 0.5
            pan = 0.0
            tilt = 0.0
            if abs(err_x) > _DEADZONE:
                # Person left of center → pan left (−).
                pan = max(-1.0, min(1.0, err_x / 0.30)) * _SPEED
            if abs(err_y) > _DEADZONE:
                # Person above center → tilt up (+).
                tilt = max(-1.0, min(1.0, -err_y / 0.30)) * _SPEED
            if abs(pan) < 0.02 and abs(tilt) < 0.02:
                ptz_stop(cfg)
            else:
                ptz_move(pan=pan, tilt=tilt, zoom=0.0, cfg=cfg)
                if _stop.wait(_PULSE):
                    break
                ptz_stop(cfg)
                # Let the live grabber catch up before the next decision (cuts chase lag).
                _stop.wait(0.08)
        except Exception:
            miss += 1
            if miss >= 12:
                break
        _stop.wait(max(0.05, _INTERVAL - (time.monotonic() - t0)))

    try:
        from cctv import ptz_stop

        ptz_stop()
    except Exception:
        pass
    with _lock:
        global _active
        _active = False
