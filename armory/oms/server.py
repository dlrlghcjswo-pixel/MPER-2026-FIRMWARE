"""Local HTTP API for the OMS window."""

from __future__ import annotations

import json
import mimetypes
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core import STORE, history_csv, poll_forever

def resource_root() -> Path:
    """Prefer files beside the .exe so title/UI updates apply without rebuilding."""
    if getattr(sys, "frozen", False):
        beside = Path(sys.executable).resolve().parent
        if (beside / "static" / "index.html").is_file():
            return beside
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


ROOT = resource_root()
STATIC = ROOT / "static"
ASSETS = ROOT / "assets"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self._json(STORE.snapshot(self._actor()))
            return
        if parsed.path == "/api/history":
            actor = self._actor()
            if not actor:
                self._json({"ok": False, "error": "로그인이 필요합니다."}, 401)
                return
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            self._json({"ok": True, "rows": STORE.history_rows(query)})
            return
        if parsed.path == "/api/history.csv":
            actor = self._actor()
            if not actor:
                self._json({"ok": False, "error": "로그인이 필요합니다."}, 401)
                return
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            body = history_csv(STORE.history_rows(query)).encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=history.csv")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/cctv/status":
            from cctv import camera_status, channel_index

            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            idx = channel_index(query.get("ch") or query.get("channel"), 0)
            self._json({"ok": True, **camera_status(idx)})
            return
        if parsed.path == "/api/cctv/person-ai":
            from person_ai import status as person_ai_status

            self._json({"ok": True, **person_ai_status()})
            return
        if parsed.path == "/api/patlite/status":
            try:
                from patlite import status as patlite_status

                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                force = str(query.get("force") or "").strip().lower() in ("1", "true", "yes")
                self._json(patlite_status(force=force))
            except Exception as exc:  # noqa: BLE001
                self._json({
                    "ok": False,
                    "connected": False,
                    "devices": [],
                    "error": str(exc) or "경광등 상태 확인 실패",
                    "profiles": {},
                    "enables": {"red": True, "yellow": True, "green": True},
                    "pattern_choices": [],
                    "pitch_choices": [],
                })
            return
        if parsed.path == "/api/rfid/status":
            try:
                from rfid import status as rfid_status

                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                force = str(query.get("force") or "").strip().lower() in ("1", "true", "yes")
                self._json(rfid_status(force=force))
            except Exception as exc:  # noqa: BLE001
                self._json({
                    "ok": False,
                    "connected": False,
                    "devices": {"com": [], "usb": []},
                    "error": str(exc) or "RFID 상태 확인 실패",
                    "config": {},
                })
            return
        if parsed.path == "/api/cctv/snapshot":
            try:
                from cctv import channel_index, snapshot_bytes

                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                idx = channel_index(query.get("ch") or query.get("channel"), 0)
                data, ctype = snapshot_bytes(idx)
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "error": str(exc)}, 502)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype or "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/api/cctv/file":
            from cctv import resolve_file

            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            path = resolve_file(str(query.get("kind") or "event"), str(query.get("name") or ""))
            if not path:
                self._json({"ok": False, "error": "파일을 찾지 못했습니다."}, 404)
                return
            if path.suffix.lower() == ".mp4":
                from cctv import browser_video

                path = browser_video(path)
            mime = {
                ".mp4": "video/mp4",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
            }.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self._send_media(path, mime)
            return
        self._file(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        body = self._body()
        actor = self._actor()
        if parsed.path == "/api/login":
            result = STORE.login(str(body.get("login_id") or ""), str(body.get("password") or ""))
            if result.get("ok"):
                self._json(result, extra_cookie=result["token"])
            else:
                self._json(result, 401)
            return
        if parsed.path == "/api/logout":
            STORE.logout(self._token())
            self._json({"ok": True}, clear_cookie=True)
            return
        if parsed.path == "/api/bootstrap":
            result = STORE.bootstrap(body)
            if result.get("ok"):
                self._json(result, extra_cookie=result.get("token"))
            else:
                self._json(result, 400)
            return
        if parsed.path == "/api/person":
            result = STORE.save_person(str(body.get("kind") or ""), int(body.get("slot") or 0), body, actor)
            self._json(result, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/person/delete":
            result = STORE.delete_person(str(body.get("kind") or ""), int(body.get("slot") or 0), actor)
            self._json(result, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/catalog":
            result = STORE.save_catalog(body, actor)
            self._json(result, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/catalog/delete":
            try:
                index = int(body.get("index"))
            except (TypeError, ValueError):
                self._json({"ok": False, "error": "순번이 올바르지 않습니다."}, 400)
                return
            result = STORE.delete_catalog(index, actor)
            self._json(result, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/gun":
            result = STORE.save_gun(int(body.get("slot") or 0), body, actor)
            self._json(result, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/gun/delete":
            result = STORE.delete_gun(int(body.get("slot") or 0), actor)
            self._json(result, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/gun/rfid":
            action = str((body or {}).get("action") or "bind").strip().lower()
            try:
                slot = int((body or {}).get("slot") or 0)
            except (TypeError, ValueError):
                self._json({"ok": False, "error": "칸 번호가 올바르지 않습니다."}, 400)
                return
            if action in ("unbind", "clear", "delete"):
                result = STORE.unbind_gun_rfid(slot, actor)
                self._json(result, 200 if result.get("ok") else 400)
                return
            if action in ("lookup", "find"):
                found = STORE.find_gun_by_rfid(str((body or {}).get("uid") or ""))
                self._json({"ok": True, "gun": found})
                return
            # bind / register: optionally inventory-read first when uid omitted.
            # Pause continuous watch + suppress 미등록/불출 alarms while registering.
            uid = str((body or {}).get("uid") or "").strip()
            STORE.begin_rfid_enroll(90.0)
            try:
                from rfid import ensure_connected, inventory as rfid_inventory
                from rfid import start_watch, stop_watch

                stop_watch()
                if not uid:
                    try:
                        ensure_connected()
                        inv = rfid_inventory()
                    except Exception as exc:  # noqa: BLE001
                        self._json({"ok": False, "error": str(exc) or "RFID 읽기 실패"}, 400)
                        return
                    if not inv.get("ok"):
                        self._json(
                            {
                                "ok": False,
                                "error": inv.get("error") or "RFID 읽기 실패",
                                "tags": inv.get("tags") or [],
                            },
                            400,
                        )
                        return
                    tags = inv.get("tags") or []
                    if not tags:
                        self._json(
                            {
                                "ok": False,
                                "error": "태그를 읽지 못했습니다. 스티커를 리더에 가까이 대세요.",
                                "tags": [],
                            },
                            400,
                        )
                        return
                    if len(tags) > 1:
                        self._json({
                            "ok": False,
                            "error": f"태그 {len(tags)}개가 감지되었습니다. 한 장만 가까이 대세요.",
                            "tags": tags,
                        }, 400)
                        return
                    uid = str(tags[0].get("uid") or "")
                    if not uid:
                        self._json({
                            "ok": False,
                            "error": "태그 UID를 해석하지 못했습니다. RFID 창에서 ‘지금 읽기’로 먼저 확인해 주세요.",
                            "tags": tags,
                        }, 400)
                        return
                result = STORE.bind_gun_rfid(slot, uid, actor)
                if result.get("ok"):
                    result["tags"] = [{"uid": result.get("rfid_uid")}]
                self._json(result, 200 if result.get("ok") else 400)
            finally:
                # Brief quiet so the pad sticker does not fire gate logic the instant bind ends.
                STORE.end_rfid_enroll(3.0)
                try:
                    from rfid import start_watch

                    start_watch()
                except Exception:
                    pass
            return
        if parsed.path == "/api/locker":
            result = STORE.save_locker(body, actor)
            self._json(result, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/locker/add":
            result = STORE.add_locker(body, actor)
            self._json(result, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/locker/delete":
            result = STORE.delete_locker(body, actor)
            self._json(result, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/locker/select":
            result = STORE.select_locker(body)
            self._json(result, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/unit":
            result = STORE.save_unit_name(str(body.get("name") or ""), actor)
            self._json(result, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/purpose":
            result = STORE.save_purpose_label(str(body.get("label") or ""), actor)
            self._json(result, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/memo":
            result = STORE.save_memo(str(body.get("text") or ""), actor)
            self._json(result, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/alerts/ack":
            ids = body.get("ids") if isinstance(body.get("ids"), list) else []
            self._json({"ok": True, "alerts": STORE.ack_alerts(ids)})
            return
        if parsed.path == "/api/armory-inside":
            active = bool((body or {}).get("armory_inside") if "armory_inside" in (body or {}) else (body or {}).get("active"))
            self._json(STORE.set_armory_inside(active))
            return
        if parsed.path == "/api/patlite/test":
            from patlite import preview, status as patlite_status

            mode = str((body or {}).get("mode") or (body or {}).get("sound") or "alarm").strip().lower()
            sound = str((body or {}).get("sound_id") or "").strip().lower()
            profile = (body or {}).get("profile") if isinstance((body or {}).get("profile"), dict) else None
            if profile is None and any(k in (body or {}) for k in ("pattern", "limit", "pitch_a", "pitch_b")):
                profile = {
                    "pattern": (body or {}).get("pattern"),
                    "limit": (body or {}).get("limit"),
                    "pitch_a": (body or {}).get("pitch_a"),
                    "pitch_b": (body or {}).get("pitch_b"),
                }
            ok = preview(mode, sound or None, profile)
            info = patlite_status(force=True)
            if not ok and not info.get("connected"):
                self._json({"ok": False, "error": info.get("error") or "경광등이 연결되어 있지 않습니다.", **info}, 400)
                return
            self._json({"ok": bool(ok), **info})
            return
        if parsed.path == "/api/patlite/config":
            from patlite import save_profiles, status as patlite_status

            profiles = save_profiles(body if isinstance(body, dict) else {})
            self._json({"ok": True, "profiles": profiles, "sounds": profiles, **patlite_status(force=True)})
            return
        if parsed.path == "/api/patlite/devices":
            from patlite import list_devices, select_device, status as patlite_status

            if str((body or {}).get("action") or "").strip().lower() in ("select", "set", "connect"):
                result = select_device(str((body or {}).get("path") or (body or {}).get("device_path") or ""))
                self._json({**patlite_status(force=True), **result})
                return
            devices = list_devices()
            self._json({"ok": True, "devices": devices, **patlite_status(force=True)})
            return
        if parsed.path == "/api/rfid/config":
            from rfid import save_config, status as rfid_status

            cfg = save_config(body if isinstance(body, dict) else {})
            self._json({"ok": True, "config": cfg, **rfid_status(force=True)})
            return
        if parsed.path == "/api/rfid/connect":
            from rfid import connect as rfid_connect
            from rfid import disconnect as rfid_disconnect
            from rfid import status as rfid_status

            action = str((body or {}).get("action") or "connect").strip().lower()
            if action in ("disconnect", "close", "off"):
                result = rfid_disconnect()
                self._json({"ok": True, **result})
                return
            result = rfid_connect(body if isinstance(body, dict) else {})
            code = 200 if result.get("ok") or result.get("connected") else 400
            self._json(result if "ok" in result else {"ok": bool(result.get("connected")), **result}, code)
            return
        if parsed.path == "/api/rfid/devices":
            from rfid import list_devices as rfid_list_devices
            from rfid import status as rfid_status

            self._json({"ok": True, "devices": rfid_list_devices(), **rfid_status(force=True)})
            return
        if parsed.path == "/api/rfid/inventory":
            from rfid import inventory as rfid_inventory
            from rfid import status as rfid_status

            result = rfid_inventory()
            self._json({**rfid_status(force=False), **result}, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/cctv/config":
            from cctv import camera_status, channel_index, public_config, save_config

            payload = body if isinstance(body, dict) else {}
            idx = channel_index(payload.get("channel"), 0)
            cfg = save_config(payload, channel=idx)
            self._json({"ok": True, "config": public_config(cfg), **camera_status(idx)})
            return
        if parsed.path == "/api/cctv/test":
            from cctv import channel_index, load_config, public_config, save_config, test_connection

            payload = dict(body) if isinstance(body, dict) else {}
            do_save = bool(payload.pop("save", True))
            idx = channel_index(payload.get("channel"), 0)
            keys = (
                "host", "http_port", "https_port", "use_https", "user", "password",
                "snapshot_path", "rtsp_port", "rtsp_path", "name", "mode",
                "onvif_device_path", "onvif_media_path", "onvif_profile",
            )
            if do_save:
                cfg = save_config(payload, channel=idx)
            else:
                cfg = load_config(idx)
                for key in keys:
                    if key in payload and payload[key] not in (None, ""):
                        cfg[key] = payload[key]
            probe = test_connection(cfg)
            # Do not merge camera_status() here — a second probe can overwrite ok/detail
            # and makes connect feel like a failure when the first probe already succeeded.
            self._json({
                "ok": bool(probe.get("ok")),
                "online": bool(probe.get("online")),
                "detail": probe.get("detail") or "",
                "bytes": probe.get("bytes") or 0,
                "onvif": probe.get("onvif") or {},
                "diagnose": probe.get("diagnose"),
                "config": public_config(cfg),
            })
            return
        if parsed.path == "/api/cctv/diagnose":
            from cctv import channel_index, diagnose, load_config, save_config

            payload = dict(body) if isinstance(body, dict) else {}
            idx = channel_index(payload.get("channel"), 0)
            if payload.get("save"):
                cfg = save_config(payload, channel=idx)
            else:
                cfg = load_config(idx)
                for key, value in payload.items():
                    if key in cfg and value not in (None, ""):
                        cfg[key] = value
            report = diagnose(cfg)
            self._json({"ok": bool(report.get("ok")), **report})
            return
        if parsed.path == "/api/cctv/record":
            from cctv import trigger_event_recording

            result = trigger_event_recording(
                reason=str(body.get("reason") or "manual"),
                message=str(body.get("message") or "수동 이벤트 녹화"),
                locker=str(body.get("locker") or ""),
                slot=body.get("slot"),
            )
            self._json(result, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/cctv/person":
            # 표 4: CCTV 사람 인지 — 로그 + 황색 경광등 + PTZ (알람창 없음)
            msg = str((body or {}).get("message") or "무기고에 사람이 출입했습니다.")
            result = STORE.notify_person_enter(msg, source=str((body or {}).get("source") or "cctv"))
            self._json(result, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/cctv/person-ai":
            from person_ai import status as person_ai_status

            self._json({"ok": True, **person_ai_status()})
            return
        if parsed.path == "/api/cctv/purge":
            from cctv import purge_old_recordings, camera_status

            removed = purge_old_recordings()
            self._json({"ok": True, "removed": removed, **camera_status()})
            return
        if parsed.path == "/api/cctv/ptz":
            from cctv import (
                channel_index,
                load_config,
                ptz_bind_locker,
                ptz_bind_situation,
                ptz_bind_slot,
                ptz_goto_event,
                ptz_goto_locker,
                ptz_goto_preset,
                ptz_goto_situation,
                ptz_home,
                ptz_list_presets,
                ptz_move,
                ptz_set_home,
                ptz_set_preset,
                ptz_stop,
            )

            action = str((body or {}).get("action") or "").strip().lower()
            cfg = load_config(channel_index((body or {}).get("channel"), 0))
            if action in ("move", "continuous"):
                result = ptz_move(
                    pan=float(body.get("pan") or 0),
                    tilt=float(body.get("tilt") or 0),
                    zoom=float(body.get("zoom") or 0),
                    cfg=cfg,
                )
            elif action == "stop":
                result = ptz_stop(cfg)
            elif action in ("home", "goto_home"):
                result = ptz_home(cfg)
            elif action == "set_home":
                result = ptz_set_home(cfg)
            elif action == "presets":
                result = ptz_list_presets(cfg)
            elif action == "goto":
                result = ptz_goto_preset(str(body.get("token") or ""), cfg)
            elif action == "set_preset":
                result = ptz_set_preset(str(body.get("name") or ""), cfg)
            elif action == "bind_locker":
                result = ptz_bind_locker(
                    str(body.get("locker_id") or ""),
                    str(body.get("kind") or "emergency"),
                    cfg,
                )
            elif action in ("bind_situation", "bind_slot"):
                raw = body.get("situation", body.get("slot"))
                try:
                    sit = int(raw)
                except (TypeError, ValueError):
                    self._json({"ok": False, "error": "situation(1~10) 필요"}, 400)
                    return
                result = ptz_bind_situation(sit, cfg) if action == "bind_situation" else ptz_bind_slot(sit, cfg)
            elif action == "goto_situation":
                try:
                    sit = int(body.get("situation"))
                except (TypeError, ValueError):
                    self._json({"ok": False, "error": "situation 필요"}, 400)
                    return
                result = ptz_goto_situation(sit, cfg)
            elif action == "goto_event":
                try:
                    locker_no = int(body.get("locker_no") or 0)
                except (TypeError, ValueError):
                    locker_no = 0
                result = ptz_goto_event(
                    str(body.get("reason") or body.get("kind") or "manual"),
                    cfg,
                    locker_id=str(body.get("locker_id") or ""),
                    locker_no=locker_no,
                )
            elif action == "goto_locker":
                slot_raw = body.get("slot")
                try:
                    slot = int(slot_raw) if slot_raw is not None and str(slot_raw) != "" else None
                except (TypeError, ValueError):
                    slot = None
                result = ptz_goto_locker(str(body.get("locker") or ""), slot, cfg)
            else:
                self._json({
                    "ok": False,
                    "error": "action: move|stop|home|set_home|presets|goto|set_preset|bind_situation|goto_situation|goto_event",
                }, 400)
                return
            self._json({"ok": bool(result.get("ok")), **result}, 200 if result.get("ok") else 400)
            return
        self._json({"ok": False, "error": "없는 주소입니다."}, 404)

    def _actor(self):
        return STORE.session_user(self._token())

    def _token(self) -> str | None:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "oms":
                return value or None
        return None

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _json(self, payload, status: int = 200, extra_cookie: str | None = None, clear_cookie: bool = False) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        if extra_cookie:
            self.send_header("Set-Cookie", f"oms={extra_cookie}; Path=/; HttpOnly; SameSite=Lax")
        if clear_cookie:
            self.send_header("Set-Cookie", "oms=; Path=/; Max-Age=0")
        self.end_headers()
        self.wfile.write(raw)

    def _send_media(self, path: Path, mime: str) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            self._json({"ok": False, "error": "파일을 찾지 못했습니다."}, 404)
            return
        start, end = 0, max(0, size - 1)
        status = 200
        range_header = self.headers.get("Range") or ""
        if range_header.startswith("bytes=") and size:
            spec = range_header.split("=", 1)[1].split(",")[0].strip()
            start_s, _, end_s = spec.partition("-")
            try:
                if start_s == "":
                    tail = int(end_s or "0")
                    start = max(0, size - tail)
                    end = size - 1
                else:
                    start = int(start_s)
                    end = int(end_s) if end_s else size - 1
            except ValueError:
                start, end = 0, size - 1
            if start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            end = min(end, size - 1)
            status = 206
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
        self.end_headers()
        try:
            with path.open("rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except OSError:
            return

    def _file(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        if path.startswith("/assets/"):
            target = (ASSETS / path.removeprefix("/assets/")).resolve()
            root = ASSETS.resolve()
        else:
            target = (STATIC / path.lstrip("/")).resolve()
            root = STATIC.resolve()
        if root not in target.parents and target != root:
            self.send_error(404)
            return
        if not target.is_file():
            self.send_error(404)
            return
        data = target.read_bytes()
        mime = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".ico": "image/x-icon",
        }.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        if target.suffix.lower() in {".png", ".jpg", ".jpeg", ".ico"}:
            self.send_header("Cache-Control", "public, max-age=86400")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def serve(port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stop = threading.Event()
    threading.Thread(target=poll_forever, args=(stop,), daemon=True).start()
    try:
        from patlite import connected as patlite_connected
        from patlite import reset as patlite_reset

        def _boot_patlite() -> None:
            try:
                # USB가 꽂혀 있으면 버튼 없이 자동 인식 (부팅을 막지 않도록 백그라운드)
                patlite_connected(force=True)
                patlite_reset()
            except Exception:
                pass

        threading.Thread(target=_boot_patlite, name="oms-patlite-boot", daemon=True).start()
    except Exception:
        pass
    try:
        from rfid import boot_autofill as rfid_boot

        threading.Thread(target=rfid_boot, name="oms-rfid-boot", daemon=True).start()
    except Exception:
        pass
    try:
        from cctv import start_continuous_recording

        start_continuous_recording()
    except Exception:
        pass
    try:
        from person_ai import start_person_ai

        start_person_ai()
    except Exception:
        pass
    return server, server.server_address[1]
