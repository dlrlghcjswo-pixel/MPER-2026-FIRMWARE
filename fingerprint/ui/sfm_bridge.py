"""Talk to sfm_daemon over a Unix domain socket."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

ROOT = Path(os.environ.get("FP_ROOT", str(Path.home() / "fingerprint")))
SOCK_PATH = Path(os.environ.get("SFM_SOCK", str(ROOT / "run" / "sfm.sock")))
DAEMON_BIN = Path(os.environ.get("SFM_DAEMON", str(ROOT / "bin" / "sfm_daemon")))
PID_PATH = ROOT / "run" / "sfm_daemon.pid"
MASTERS_PATH = ROOT / "data" / "masters.json"
EXTERNAL_DAEMON = os.environ.get("SFM_EXTERNAL_DAEMON", "0") == "1"
FINGER_SERIAL = "SmartLocker"
_spawn_lock = threading.Lock()
_store_lock = threading.RLock()


def fingerprint_port() -> Optional[str]:
    """SmartLocker FT232 first, then direct USB (ttyACM) for bench tests."""
    override = os.environ.get("SFM_PORT", "").strip()
    if override:
        return override
    by_id = Path("/dev/serial/by-id")
    if by_id.is_dir():
        for link in sorted(by_id.iterdir()):
            if FINGER_SERIAL in link.name:
                try:
                    target = link.resolve()
                except OSError:
                    continue
                if target.exists():
                    return str(target)
    for i in range(8):
        acm = Path(f"/dev/ttyACM{i}")
        if acm.exists():
            return str(acm)
    return None


class SfmBridge:
    def __init__(self) -> None:
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._events: Deque[Dict[str, Any]] = deque(maxlen=200)
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._reader: Optional[threading.Thread] = None
        self._running = False
        self._last_status: Dict[str, Any] = {"connected": False}
        self._enroll: Dict[str, Any] = {
            "active": False,
            "user_id": None,
            "scans": 0,
            "done": False,
            "ok": None,
            "error": None,
            "quality": None,
        }
        self._daemon_proc: Optional[subprocess.Popen] = None
        self._last_spawn_attempt = 0.0

    def start(self) -> None:
        self._ensure_daemon()
        self._connect_loop()
        # wait briefly for first status/connected event
        for _ in range(30):
            if self._last_status.get("connected"):
                break
            if self._sock is not None:
                self.send({"cmd": "status"})
            time.sleep(0.1)

    def stop(self) -> None:
        self._running = False
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    def _daemon_alive(self) -> bool:
        if not PID_PATH.exists():
            return False
        try:
            pid = int(PID_PATH.read_text(encoding="utf-8").strip())
        except ValueError:
            return False
        proc = Path(f"/proc/{pid}")
        if not proc.exists():
            return False
        try:
            stat = (proc / "stat").read_text(encoding="utf-8")
            # /proc/pid/stat: pid (comm) state ...
            rparen = stat.rfind(")")
            state = stat[rparen + 2] if rparen != -1 else ""
            if state == "Z":
                return False
        except OSError:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _probe_socket(self) -> bool:
        if not SOCK_PATH.exists():
            return False
        last_err: Optional[OSError] = None
        for _ in range(8):
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(2.0)
                s.connect(str(SOCK_PATH))
                s.close()
                return True
            except OSError as exc:
                last_err = exc
                # listen backlog 가득 찼을 때 EAGAIN
                if exc.errno in (11, 35):  # EAGAIN / EWOULDBLOCK
                    time.sleep(0.15)
                    continue
                break
        if last_err and not self._daemon_alive():
            try:
                SOCK_PATH.unlink()
            except OSError:
                pass
        return False

    def _ensure_daemon(self) -> None:
        with _spawn_lock:
            if self._probe_socket():
                return

            if EXTERNAL_DAEMON:
                # 외부에서 데몬을 띄운 경우 여기서 죽/생성하지 않음
                for _ in range(20):
                    if self._probe_socket():
                        return
                    time.sleep(0.1)
                return

            # 데몬 프로세스는 있는데 소켓 path 가 없으면 깨진 상태 → 재시작
            if self._daemon_alive() and not SOCK_PATH.exists():
                try:
                    pid = int(PID_PATH.read_text(encoding="utf-8").strip())
                    os.kill(pid, 9)
                except (ValueError, OSError):
                    pass
                time.sleep(0.5)

            if self._daemon_alive():
                for _ in range(30):
                    if self._probe_socket():
                        return
                    time.sleep(0.1)
                return

            now = time.time()
            if now - self._last_spawn_attempt < 3.0:
                return
            self._last_spawn_attempt = now

            if not DAEMON_BIN.exists():
                raise FileNotFoundError(f"sfm_daemon not found: {DAEMON_BIN}")

            port = fingerprint_port()
            if not port:
                raise FileNotFoundError("fingerprint port not found (SmartLocker or ttyACM)")
            SOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            log_path = ROOT / "logs" / "sfm_daemon.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            logf = open(log_path, "a", buffering=1)
            self._daemon_proc = subprocess.Popen(
                [str(DAEMON_BIN), port, str(SOCK_PATH)],
                stdout=logf,
                stderr=logf,
                cwd=str(ROOT),
                start_new_session=True,
            )
            PID_PATH.write_text(str(self._daemon_proc.pid), encoding="utf-8")
            for _ in range(40):
                if self._probe_socket():
                    return
                time.sleep(0.1)
            raise TimeoutError("sfm_daemon socket not ready")

    def _connect_loop(self) -> None:
        self._running = True
        self._reader = threading.Thread(target=self._reader_main, daemon=True)
        self._reader.start()

    def _reader_main(self) -> None:
        buf = b""
        while self._running:
            try:
                if self._sock is None:
                    last = None
                    for _ in range(10):
                        try:
                            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                            s.settimeout(2.0)
                            s.connect(str(SOCK_PATH))
                            s.settimeout(1.0)
                            with self._lock:
                                self._sock = s
                            self._push({"event": "bridge", "ok": True})
                            self.send({"cmd": "status"})
                            last = None
                            break
                        except OSError as exc:
                            last = exc
                            if exc.errno in (11, 35):
                                time.sleep(0.2)
                                continue
                            raise
                    if last is not None and self._sock is None:
                        raise last
                assert self._sock is not None
                try:
                    chunk = self._sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    raise ConnectionError("daemon closed")
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        continue
                    self._handle_event(msg)
            except Exception:
                with self._lock:
                    if self._sock:
                        try:
                            self._sock.close()
                        except OSError:
                            pass
                        self._sock = None
                self._last_status["connected"] = False
                self._push({"event": "status", "connected": False, "port": self._last_status.get("port")})
                buf = b""
                time.sleep(0.5)
                try:
                    self._ensure_daemon()
                except Exception:
                    time.sleep(1.0)

    def _handle_event(self, msg: Dict[str, Any]) -> None:
        ev = msg.get("event")
        if ev == "status":
            self._last_status.update(msg)
        if ev == "connected":
            self._last_status["connected"] = True
            self._last_status.update(msg)
        if ev == "error" and msg.get("message") in ("no_ttyACM", "open_fail"):
            self._last_status["connected"] = False
        if ev == "enroll_waiting":
            self._enroll.update(
                {
                    "active": True,
                    "user_id": msg.get("user_id"),
                    "scans": 0,
                    "done": False,
                    "ok": None,
                    "error": None,
                }
            )
        if ev == "enroll_scan":
            self._enroll["scans"] = int(msg.get("count") or 0)
            self._enroll["active"] = True
        if ev == "enroll_done":
            self._enroll.update(
                {
                    "active": False,
                    "done": True,
                    "ok": bool(msg.get("ok")),
                    "error": msg.get("error"),
                    "quality": msg.get("quality"),
                    "user_id": msg.get("user_id", self._enroll.get("user_id")),
                }
            )
        self._push(msg)

    def _push(self, msg: Dict[str, Any]) -> None:
        self._events.append(msg)
        for cb in list(self._listeners):
            try:
                cb(msg)
            except Exception:
                pass

    def subscribe(self, cb: Callable[[Dict[str, Any]], None]) -> Callable[[], None]:
        self._listeners.append(cb)

        def unsub() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return unsub

    def publish(self, msg: Dict[str, Any]) -> None:
        """Push a UI/OMS event that did not come from the fingerprint daemon."""
        self._push(msg)

    def send(self, obj: Dict[str, Any]) -> bool:
        raw = (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            if not self._sock:
                return False
            try:
                self._sock.sendall(raw)
                return True
            except OSError:
                return False

    def start_enroll(self, user_id: int = 1) -> Dict[str, Any]:
        ok = self.send({"cmd": "enroll", "user_id": user_id})
        if not ok:
            return {"ok": False, "enroll": dict(self._enroll)}
        self._enroll = {
            "active": True,
            "user_id": user_id,
            "scans": 0,
            "done": False,
            "ok": None,
            "error": None,
            "quality": None,
        }
        return {"ok": True, "enroll": dict(self._enroll)}

    def abort(self) -> Dict[str, Any]:
        ok = self.send({"cmd": "abort"})
        self._enroll["active"] = False
        return {"ok": ok}

    def delete_user(self, user_id: int) -> Dict[str, Any]:
        ok = self.send({"cmd": "delete", "user_id": user_id})
        return {"ok": ok}

    def _wait_event(self, names: set[str], timeout: float, match: Optional[Callable[[Dict[str, Any]], bool]] = None) -> Optional[Dict[str, Any]]:
        found: Dict[str, Any] = {}
        flag = threading.Event()

        def on_msg(msg: Dict[str, Any]) -> None:
            if msg.get("event") not in names:
                return
            if match and not match(msg):
                return
            found.update(msg)
            flag.set()

        unsub = self.subscribe(on_msg)
        try:
            if flag.wait(timeout):
                return found
            return None
        finally:
            unsub()

    def delete_user_wait(self, user_id: int, timeout: float = 6.0) -> Dict[str, Any]:
        """Delete one template and wait for daemon ack (single-slot cmd queue)."""
        found: Dict[str, Any] = {}
        flag = threading.Event()
        uid = int(user_id)

        def on_msg(msg: Dict[str, Any]) -> None:
            if msg.get("event") != "delete_done":
                return
            if int(msg.get("user_id") or 0) != uid:
                return
            found.update(msg)
            flag.set()

        unsub = self.subscribe(on_msg)
        try:
            if not self.send({"cmd": "delete", "user_id": uid}):
                return {"ok": False, "error": "send_failed", "user_id": uid}
            if not flag.wait(timeout):
                return {"ok": False, "error": "timeout", "user_id": uid}
            return {"ok": bool(found.get("ok")), "error": found.get("error"), "user_id": uid}
        finally:
            unsub()

    def delete_all(self, timeout: float = 45.0) -> Dict[str, Any]:
        """Wipe every template on the sensor via UF_DeleteAll."""
        found: Dict[str, Any] = {}
        flag = threading.Event()

        def on_msg(msg: Dict[str, Any]) -> None:
            if msg.get("event") != "delete_all_done":
                return
            found.update(msg)
            flag.set()

        unsub = self.subscribe(on_msg)
        try:
            if not self.send({"cmd": "delete_all"}):
                return {"ok": False, "error": "send_failed"}
            if not flag.wait(timeout):
                return {"ok": False, "error": "timeout"}
            return {
                "ok": bool(found.get("ok")),
                "error": found.get("error"),
                "templates": found.get("templates"),
            }
        finally:
            unsub()

    def request_status(self) -> Dict[str, Any]:
        self.send({"cmd": "status"})
        return dict(self._last_status)

    def enroll_state(self) -> Dict[str, Any]:
        return dict(self._enroll)

    def snapshot(self) -> Dict[str, Any]:
        # Status/SSE polls must not rewrite masters.json (OMS hammers /api/status).
        store = load_masters(persist=False)
        status = dict(self._last_status)
        tty_present = fingerprint_port() is not None
        connected = bool(status.get("connected")) and self._sock is not None
        kind = store.get("locker_kind") if store.get("locker_kind") in ("rifle", "pistol") else "rifle"
        return {
            "status": status,
            "sensor": {
                "connected": connected,
                "tty_present": tty_present,
                "port": status.get("port") or fingerprint_port() or "",
            },
            "enroll": dict(self._enroll),
            "masters": store,
            "purpose_label": store.get("purpose_label") or "젤루텍",
            "subs": store.get("subs") or {},
            "users": store.get("users") or {},
            "stats": store.get("stats") or {},
            "locker_kind": kind,
            "reset_seq": int(store.get("reset_seq") or 0),
        }


def _empty_store() -> Dict[str, Any]:
    return {
        "masters": {str(i): None for i in range(1, 6)},
        "subs": {str(i): None for i in range(1, 31)},
        "users": {str(i): None for i in range(1, 11)},
        "locker_kind": "rifle",
        "reset_seq": 0,
        "purpose_label": "젤루텍",
        "stats": {"issue_counts": {p: 0 for p in ("사격", "훈련", "정비", "근무", "기타", "젤루텍")}},
    }


def _write_store(data: Dict[str, Any]) -> None:
    """Atomic replace with a unique temp file (never share masters.json.tmp across threads)."""
    with _store_lock:
        MASTERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, ensure_ascii=False)
        tmp = MASTERS_PATH.parent / (
            f".masters.json.tmp.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}"
        )
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, MASTERS_PATH)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass


def _parse_masters_text(raw: str) -> Dict[str, Any]:
    """Parse masters.json; tolerate trailing garbage from interrupted writes."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data, _idx = json.JSONDecoder().raw_decode(raw.lstrip("\ufeff \t\r\n"))
    if not isinstance(data, dict):
        raise json.JSONDecodeError("masters.json root must be object", raw, 0)
    return data


def load_masters(*, persist: bool = True) -> Dict[str, Any]:
    with _store_lock:
        return _load_masters_unlocked(persist=persist)


def _load_masters_unlocked(*, persist: bool = True) -> Dict[str, Any]:
    MASTERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not MASTERS_PATH.exists():
        data = _empty_store()
        if persist:
            _write_store(data)
        return data
    data = None
    last_error: Optional[Exception] = None
    for _ in range(3):
        try:
            data = _parse_masters_text(MASTERS_PATH.read_text(encoding="utf-8"))
            break
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            time.sleep(0.05)
    if data is None:
        # Last resort: keep a backup and start from empty so OMS stays online.
        try:
            bak = MASTERS_PATH.with_suffix(".json.corrupt")
            bak.write_bytes(MASTERS_PATH.read_bytes())
        except OSError:
            pass
        data = _empty_store()
        if persist:
            _write_store(data)
        print(f"[warn] masters.json unreadable ({last_error}); reset to empty store")
    data.setdefault("masters", {str(i): None for i in range(1, 6)})
    changed = False
    kind = data.get("locker_kind")
    if kind not in ("rifle", "pistol"):
        data["locker_kind"] = "rifle"
        changed = True
    try:
        data["reset_seq"] = int(data.get("reset_seq") or 0)
    except (TypeError, ValueError):
        data["reset_seq"] = 0
        changed = True
    if "subs" not in data:
        data["subs"] = {str(i): None for i in range(1, 31)}
        changed = True
    if "users" not in data:
        data["users"] = {str(i): None for i in range(1, 11)}
        changed = True
    for i in range(1, 6):
        if str(i) not in data["masters"]:
            data["masters"][str(i)] = None
            changed = True
        m = data["masters"].get(str(i))
        if isinstance(m, dict) and m.get("user_id") and not m.get("name"):
            m["name"] = f"마스터{i}관리자"
            changed = True
    for i in range(1, 31):
        s = (data.get("subs") or {}).get(str(i))
        if isinstance(s, dict) and s.get("user_id") and not s.get("name"):
            s["name"] = f"서브{i}관리자"
            changed = True
    for i in range(1, 11):
        if str(i) not in data["users"]:
            data["users"][str(i)] = None
            changed = True
        elif isinstance(data["users"][str(i)], dict):
            u = data["users"][str(i)]
            if u.get("user_id") and not u.get("name"):
                u["name"] = f"사용자{i}"
                changed = True
            if u.get("user_id") and not u.get("status"):
                u["status"] = "stored"
                changed = True
    if "stats" not in data:
        data["stats"] = {"issue_counts": {p: 0 for p in ("사격", "훈련", "정비", "근무", "기타", "젤루텍")}}
        changed = True
    _ensure_stats(data)
    before = dict(data.get("stats", {}).get("issue_counts") or {})
    _recompute_issue_counts(data)
    if data["stats"]["issue_counts"] != before:
        changed = True
    if changed and persist:
        _write_store(data)
    return data


def save_master(slot: int, user_id: int, quality: Optional[int] = None) -> Dict[str, Any]:
    with _store_lock:
        data = _load_masters_unlocked()
        data.setdefault("masters", {})
        prev = data["masters"].get(str(slot)) or {}
        name = prev.get("name") if isinstance(prev, dict) and prev.get("name") else f"마스터{slot}관리자"
        data["masters"][str(slot)] = {
            "user_id": user_id,
            "name": name,
            "quality": quality,
            "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _write_store(data)
        return data


def clear_master(slot: int) -> Dict[str, Any]:
    with _store_lock:
        data = _load_masters_unlocked()
        data.setdefault("masters", {})
        data["masters"][str(slot)] = None
        _write_store(data)
        return data


def sub_user_id(slot: int) -> int:
    """S1→101 … S30→130 (마스터 1~5 와 충돌 방지)"""
    return 100 + int(slot)


def save_sub(slot: int, user_id: Optional[int] = None, quality: Optional[int] = None) -> Dict[str, Any]:
    with _store_lock:
        data = _load_masters_unlocked()
        data.setdefault("subs", {})
        uid = user_id if user_id else sub_user_id(slot)
        prev = data["subs"].get(str(slot)) or {}
        name = prev.get("name") if isinstance(prev, dict) and prev.get("name") else f"서브{slot}관리자"
        data["subs"][str(slot)] = {
            "user_id": uid,
            "name": name,
            "quality": quality,
            "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _write_store(data)
        return data


def clear_sub(slot: int) -> Dict[str, Any]:
    with _store_lock:
        data = _load_masters_unlocked()
        data.setdefault("subs", {})
        data["subs"][str(slot)] = None
        _write_store(data)
        return data


def rename_entry(kind: str, slot: int, name: str) -> Dict[str, Any]:
    with _store_lock:
        data = _load_masters_unlocked()
        if kind == "master":
            bucket, default = "masters", f"마스터{slot}관리자"
            if slot < 1 or slot > 5:
                raise ValueError("invalid master slot")
        elif kind == "sub":
            bucket, default = "subs", f"서브{slot}관리자"
            if slot < 1 or slot > 30:
                raise ValueError("invalid sub slot")
        elif kind == "user":
            bucket, default = "users", f"사용자{slot}"
            if slot < 1 or slot > 10:
                raise ValueError("invalid user slot")
        else:
            raise ValueError("invalid kind")
        data.setdefault(bucket, {})
        info = data[bucket].get(str(slot))
        if not info or not info.get("user_id"):
            raise ValueError("not registered")
        cleaned = (name or "").strip() or default
        info["name"] = cleaned[:24]
        data[bucket][str(slot)] = info
        _write_store(data)
        return data


def rename_user(slot: int, name: str) -> Dict[str, Any]:
    return rename_entry("user", slot, name)


def set_purpose_label(text: str) -> Dict[str, Any]:
    with _store_lock:
        data = _load_masters_unlocked()
        label = (text or "").strip()[:10] or "젤루텍"
        data["purpose_label"] = label
        _write_store(data)
        return data


def locker_user_id(slot: int) -> int:
    """사용자1→201 … 사용자10→210"""
    return 200 + int(slot)


PURPOSES = ("사격", "훈련", "정비", "근무", "기타", "젤루텍")


def _default_issue_counts() -> Dict[str, int]:
    return {p: 0 for p in PURPOSES}


def _ensure_stats(data: Dict[str, Any]) -> Dict[str, Any]:
    stats = data.setdefault("stats", {})
    counts = stats.setdefault("issue_counts", _default_issue_counts())
    for p in PURPOSES:
        counts.setdefault(p, 0)
    return data


def _recompute_issue_counts(data: Dict[str, Any]) -> Dict[str, Any]:
    """불출 현황 = 현재 issued 상태인 소총의 목적별 개수(아이콘과 동기)."""
    _ensure_stats(data)
    counts = _default_issue_counts()
    for info in (data.get("users") or {}).values():
        if not isinstance(info, dict) or info.get("status") != "issued":
            continue
        p = info.get("purpose") if info.get("purpose") in PURPOSES else "기타"
        counts[p] = int(counts.get(p, 0)) + 1
    data["stats"]["issue_counts"] = counts
    return data


def save_user(slot: int, user_id: Optional[int] = None, quality: Optional[int] = None) -> Dict[str, Any]:
    with _store_lock:
        data = _load_masters_unlocked()
        data.setdefault("users", {})
        _ensure_stats(data)
        uid = user_id if user_id else locker_user_id(slot)
        prev = data["users"].get(str(slot)) or {}
        name = prev.get("name") if isinstance(prev, dict) and prev.get("name") else f"사용자{slot}"
        data["users"][str(slot)] = {
            "user_id": uid,
            "name": name,
            "quality": quality,
            "status": "stored",
            "purpose": None,
            "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _write_store(data)
        return data


def clear_user(slot: int) -> Dict[str, Any]:
    with _store_lock:
        data = _load_masters_unlocked()
        data.setdefault("users", {})
        data["users"][str(slot)] = None
        _write_store(data)
        return data


def set_user_ops(
    slot: int,
    kind: str,
    purpose: Optional[str] = None,
    approver: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """kind: issue | store — updates user status and purpose counts, then notifies UI."""
    with _store_lock:
        data = _load_masters_unlocked()
        data.setdefault("users", {})
        _ensure_stats(data)
        info = data["users"].get(str(slot))
        if not info or not info.get("user_id"):
            raise ValueError("user not registered")
        if kind == "issue":
            p = purpose if purpose in PURPOSES else "기타"
            info["status"] = "issued"
            info["purpose"] = p
        else:
            info["status"] = "stored"
            info["purpose"] = None
        info["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if isinstance(approver, dict) and approver.get("role") in ("master", "sub"):
            info["approver_role"] = approver.get("role")
            info["approver_slot"] = int(approver.get("slot") or 0) or None
            info["approver_name"] = str(approver.get("name") or "").strip()
            info["approver_user_id"] = int(approver.get("user_id") or 0) or None
        else:
            info.pop("approver_role", None)
            info.pop("approver_slot", None)
            info.pop("approver_name", None)
            info.pop("approver_user_id", None)
        data["users"][str(slot)] = info
        _recompute_issue_counts(data)
        _write_store(data)
        payload = {
            "event": "store_update",
            "users": data.get("users") or {},
            "stats": data.get("stats") or {},
            "store": data,
        }
    bridge._push(payload)
    return data


def set_locker_kind(kind: str) -> Dict[str, Any]:
    if kind not in ("rifle", "pistol"):
        raise ValueError("invalid locker kind")
    with _store_lock:
        data = _load_masters_unlocked()
        data["locker_kind"] = kind
        _write_store(data)
        return data


_reset_lock = threading.Lock()


def factory_reset_locker() -> Dict[str, Any]:
    """Wipe sensor templates and local people records. Keep locker_kind."""
    if not _reset_lock.acquire(blocking=False):
        raise RuntimeError("reset busy")
    try:
        return _factory_reset_locker_locked()
    finally:
        _reset_lock.release()


def _factory_reset_locker_locked() -> Dict[str, Any]:
    with _store_lock:
        data = _load_masters_unlocked()
        kind = data.get("locker_kind") if data.get("locker_kind") in ("rifle", "pistol") else "rifle"
        next_seq = int(data.get("reset_seq") or 0) + 1
    # Sensor wipe outside the store lock — can take tens of seconds.
    # One delete_all only. A preceding abort fills the daemon's single command
    # slot and the wipe is discarded. Per-ID fallback then overlaps a second tap
    # and leaves the sensor scanning (green blink) until the UI is restarted.
    wiped = bridge.delete_all(timeout=60.0)
    if not wiped.get("ok"):
        raise RuntimeError(str(wiped.get("error") or "wipe failed"))
    fresh = _empty_store()
    fresh["locker_kind"] = kind
    fresh["reset_seq"] = next_seq
    _write_store(fresh)
    bridge._push({"event": "store_update", "store": fresh, "reset_seq": next_seq, "locker_kind": kind})
    return fresh


bridge = SfmBridge()
