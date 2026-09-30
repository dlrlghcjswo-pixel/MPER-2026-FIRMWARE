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
    if getattr(sys, "frozen", False):
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
        if parsed.path == "/api/purpose":
            result = STORE.save_purpose_label(str(body.get("label") or ""), actor)
            self._json(result, 200 if result.get("ok") else 400)
            return
        if parsed.path == "/api/memo":
            result = STORE.save_memo(str(body.get("text") or ""), actor)
            self._json(result, 200 if result.get("ok") else 400)
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
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def serve(port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stop = threading.Event()
    threading.Thread(target=poll_forever, args=(stop,), daemon=True).start()
    return server, server.server_address[1]
