"""Open the OMS window after the Zellootec splash."""

from __future__ import annotations

import atexit
import os
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        beside = Path(sys.executable).resolve().parent
        if (beside / "static" / "index.html").is_file():
            return beside
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


ROOT = _app_root()
# Source run and frozen exe both look beside the app for _deps (hidapi, etc.).
if not getattr(sys, "frozen", False):
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
deps = Path(sys.executable).resolve().parent / "_deps" if getattr(sys, "frozen", False) else ROOT / "_deps"
if deps.is_dir() and str(deps) not in sys.path:
    sys.path.insert(0, str(deps))

from server import serve  # noqa: E402


APP_TITLE = "스마트 무기고 시스템"
# Safety only: never leave splash forever if boot hangs.
SPLASH_MAX_MS = 180000


def log_path() -> Path:
    root = Path(os.environ.get("APPDATA", str(Path.home()))) / "Zellootec" / "ArmoryOMS"
    root.mkdir(parents=True, exist_ok=True)
    return root / "oms.log"


def edge_path() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _close_boot_splash() -> None:
    """Close PyInstaller bootloader splash (covers big-exe unpack + Python boot)."""
    try:
        import pyi_splash  # type: ignore

        if pyi_splash.is_alive():
            pyi_splash.close()
    except Exception:
        pass


def show_splash(ready: threading.Event) -> None:
    """Dev/source run: show logo while server boots. No artificial extra delay."""
    import tkinter as tk

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="white")
    width, height = 780, 440
    left = max(0, (root.winfo_screenwidth() - width) // 2)
    top = max(0, (root.winfo_screenheight() - height) // 2)
    root.geometry(f"{width}x{height}+{left}+{top}")
    logo = ROOT / "assets" / "zellootec.png"
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and not logo.is_file():
        cand = Path(meipass) / "assets" / "zellootec.png"
        if cand.is_file():
            logo = cand
    image = tk.PhotoImage(file=str(logo))
    while image.width() > 640:
        image = image.subsample(2, 2)
    label = tk.Label(root, image=image, bg="white", borderwidth=0)
    label.image = image
    label.pack(expand=True)
    started = time.monotonic()

    def try_close() -> None:
        if int((time.monotonic() - started) * 1000) >= SPLASH_MAX_MS:
            root.destroy()
            return
        if not ready.is_set():
            root.after(100, try_close)
            return
        root.destroy()

    root.after(100, try_close)
    root.mainloop()


def edge_count(marker: str) -> int:
    script = (
        "$n = @(Get-CimInstance Win32_Process -Filter \"Name = 'msedge.exe'\" | "
        f"Where-Object {{ $_.CommandLine -and $_.CommandLine.Contains('{marker}') }}).Count; "
        "Write-Output $n"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return int((out or "0").strip() or "0")
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


def edge_profile() -> Path:
    # Separate from locker OMS profile so Edge does not keep the old app title.
    root = Path(os.environ.get("APPDATA", str(Path.home()))) / "Zellootec" / "ArmoryOMS" / "edge-profile"
    root.mkdir(parents=True, exist_ok=True)
    return root


def open_window(port: int) -> Path | None:
    edge = edge_path()
    url = f"http://127.0.0.1:{port}/"
    if edge is None:
        webbrowser.open(url)
        return None
    profile = edge_profile()
    # Keep flags identical to the working locker OMS --app window.
    # Extra flags / Korean URL params can break --app and fall back to a normal Edge tab.
    cmd = [
        str(edge),
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--window-size=1480,940",
        "--no-first-run",
        "--disable-features=Translate,msEdgeFirstRun",
        "--disable-sync",
        "--no-default-browser-check",
    ]
    subprocess.Popen(cmd)
    return profile


def wait_for_window(profile: Path, port: int) -> bool:
    markers = [profile.name, f"127.0.0.1:{port}"]
    for _ in range(40):
        if any(edge_count(marker) > 0 for marker in markers):
            while any(edge_count(marker) > 0 for marker in markers):
                time.sleep(2.0)
            return True
        time.sleep(0.4)
    return False


# Kept alive for process lifetime so Windows does not release the mutex early.
_INSTANCE_MUTEX = None
_MUTEX_NAME = "Global\\ZellootecArmoryOMSSingleInstance"
_LOCK_NAME = "oms.single.lock"


def _focus_existing_window() -> None:
    """Bring an already-running OMS Edge/app window to the foreground."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        found = wintypes.HWND(0)

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lparam):  # type: ignore[misc]
            nonlocal found
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            title = buf.value or ""
            if APP_TITLE in title or "ArmoryOMS" in title:
                found = hwnd
                return False
            return True

        user32.EnumWindows(_enum, 0)
        if found:
            user32.ShowWindow(found, 9)  # SW_RESTORE
            user32.SetForegroundWindow(found)
    except Exception:
        pass


def _show_already_running() -> None:
    _focus_existing_window()
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showwarning(
            APP_TITLE,
            "스마트 무기고 시스템이 이미 실행 중입니다.\n\n"
            "프로그램을 두 개 켜면 RFID·사람인식이 끊길 수 있습니다.\n"
            "이미 열린 창을 사용하세요.",
        )
        root.destroy()
    except Exception:
        pass


def _lockfile_path() -> Path:
    base = Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData") / "ArmoryOMS"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path(os.environ.get("TEMP") or ".") 
    return base / _LOCK_NAME


def _pid_alive(pid: int) -> bool:
    if pid <= 0 or os.name != "nt":
        return False
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False


def _acquire_lockfile() -> object | None:
    """Fallback when CreateMutex is unavailable — exclusive create of a lock file."""
    path = _lockfile_path()
    try:
        if path.is_file():
            try:
                old_pid = int(path.read_text(encoding="utf-8").strip().splitlines()[0])
            except Exception:
                old_pid = 0
            if _pid_alive(old_pid):
                return None
            try:
                path.unlink()
            except OSError:
                return None
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
    except FileExistsError:
        return None
    except OSError:
        return None

    class _Lock:
        def __init__(self, file_fd: int, file_path: Path) -> None:
            self.fd = file_fd
            self.path = file_path

        def close(self) -> None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            try:
                self.path.unlink()
            except OSError:
                pass

    lock = _Lock(fd, path)
    atexit.register(lock.close)
    return lock


def _acquire_single_instance() -> None:
    """Hard block a second OMS (exe or python) — RFID COM / person-AI / Edge profile.

    Always exits the process if another instance already owns the lock.
    """
    global _INSTANCE_MUTEX
    if os.name != "nt":
        if _acquire_lockfile() is None:
            _show_already_running()
            sys.exit(0)
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.SetLastError(0)
        # Global\ = machine-wide (admin + normal user share one lock).
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        err = ctypes.get_last_error()
        if not handle:
            if _acquire_lockfile() is None:
                _show_already_running()
                sys.exit(0)
            return
        if err == 183:  # ERROR_ALREADY_EXISTS
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
            _show_already_running()
            sys.exit(0)
        _INSTANCE_MUTEX = handle
        return
    except SystemExit:
        raise
    except Exception:
        if _acquire_lockfile() is None:
            _show_already_running()
            sys.exit(0)


def main() -> None:
    ready = threading.Event()
    boot: dict[str, object] = {"server": None, "port": 0, "profile": None, "error": ""}
    frozen = bool(getattr(sys, "frozen", False))

    def boot_worker() -> None:
        try:
            try:
                import pyi_splash  # type: ignore

                if pyi_splash.is_alive():
                    pyi_splash.update_text("로딩 중…")
            except Exception:
                pass
            server, port = serve()
            boot["server"] = server
            boot["port"] = port
            boot["profile"] = open_window(port)
        except Exception as exc:  # noqa: BLE001
            boot["error"] = str(exc)
        finally:
            ready.set()
            _close_boot_splash()

    threading.Thread(target=boot_worker, name="oms-boot", daemon=True).start()
    if frozen:
        # Bootloader splash stays up through unpack + serve(); close when ready.
        ready.wait(timeout=SPLASH_MAX_MS / 1000.0)
        _close_boot_splash()
    else:
        show_splash(ready)

    if boot.get("error"):
        _close_boot_splash()
        raise RuntimeError(str(boot["error"]))
    server = boot.get("server")
    port = int(boot.get("port") or 0)
    profile = boot.get("profile")
    if server is None or not port:
        _close_boot_splash()
        raise RuntimeError("OMS 서버를 시작하지 못했습니다.")
    try:
        if profile is None or not wait_for_window(profile, port):  # type: ignore[arg-type]
            webbrowser.open(f"http://127.0.0.1:{port}/")
            show_fallback()
    finally:
        _shutdown_patlite()
        _shutdown_rfid()
        _shutdown_cctv()
        try:
            server.shutdown()  # type: ignore[union-attr]
        except Exception:
            pass


def _shutdown_patlite() -> None:
    """Turn tower off on exit — HID keeps last LED/buzzer state if process just dies."""
    try:
        from patlite import reset as patlite_reset

        patlite_reset()
    except Exception:
        pass


def _shutdown_rfid() -> None:
    try:
        from rfid import disconnect as rfid_disconnect

        rfid_disconnect()
    except Exception:
        pass


def _shutdown_cctv() -> None:
    """Stop continuous RTSP encoders so restarts do not stack ffmpeg pulls."""
    try:
        from cctv import stop_continuous_recording

        stop_continuous_recording()
    except Exception:
        pass


def show_fallback() -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title(APP_TITLE)
    icon = ROOT / "assets" / "favicon.ico"
    if icon.is_file():
        root.iconbitmap(str(icon))
    root.geometry("420x160")
    tk.Label(root, text="브라우저에서 관리 화면을 열었습니다.\n이 창을 닫으면 프로그램이 종료됩니다.", padx=24, pady=28).pack()
    root.mainloop()


if __name__ == "__main__":
    # Before splash / server / Edge — second double-click must die here.
    _acquire_single_instance()
    atexit.register(_shutdown_patlite)
    atexit.register(_shutdown_rfid)
    atexit.register(_shutdown_cctv)
    try:
        main()
    except Exception:
        _close_boot_splash()
        log_path().write_text(traceback.format_exc(), encoding="utf-8")
        raise
