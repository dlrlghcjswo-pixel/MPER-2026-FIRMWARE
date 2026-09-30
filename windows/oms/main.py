"""Open the OMS window after the Zellootec splash."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import webbrowser
from pathlib import Path

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
if not getattr(sys, "frozen", False):
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))

from server import serve  # noqa: E402


def log_path() -> Path:
    root = Path(os.environ.get("APPDATA", str(Path.home()))) / "Zellootec" / "RifleOMS"
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


def show_splash() -> None:
    import tkinter as tk

    root = tk.Tk()
    root.overrideredirect(True)
    root.configure(bg="white")
    width, height = 780, 440
    left = max(0, (root.winfo_screenwidth() - width) // 2)
    top = max(0, (root.winfo_screenheight() - height) // 2)
    root.geometry(f"{width}x{height}+{left}+{top}")
    image = tk.PhotoImage(file=str(ROOT / "assets" / "zellootec.png"))
    while image.width() > 640:
        image = image.subsample(2, 2)
    label = tk.Label(root, image=image, bg="white", borderwidth=0)
    label.image = image
    label.pack(expand=True)
    root.after(1700, root.destroy)
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


def open_window(port: int) -> Path | None:
    edge = edge_path()
    url = f"http://127.0.0.1:{port}/"
    if edge is None:
        webbrowser.open(url)
        return None
    profile = Path(tempfile.mkdtemp(prefix="zellootec-oms-"))
    cmd = [
        str(edge),
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--window-size=1480,940",
        "--no-first-run",
        "--disable-features=Translate",
    ]
    subprocess.Popen(cmd)
    return profile


def wait_for_window(profile: Path) -> bool:
    marker = profile.name
    for _ in range(25):
        if edge_count(marker) > 0:
            while edge_count(marker) > 0:
                time.sleep(2.0)
            return True
        time.sleep(0.4)
    return False


def main() -> None:
    server, port = serve()
    profile: Path | None = None
    try:
        show_splash()
        profile = open_window(port)
        if profile is None or not wait_for_window(profile):
            webbrowser.open(f"http://127.0.0.1:{port}/")
            show_fallback()
    finally:
        server.shutdown()
        if profile is not None:
            shutil.rmtree(profile, ignore_errors=True)


def show_fallback() -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title("스마트 소총 보관함 OMS")
    icon = ROOT / "assets" / "favicon.ico"
    if icon.is_file():
        root.iconbitmap(str(icon))
    root.geometry("420x160")
    tk.Label(root, text="브라우저에서 관리 화면을 열었습니다.\n이 창을 닫으면 프로그램이 종료됩니다.", padx=24, pady=28).pack()
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_path().write_text(traceback.format_exc(), encoding="utf-8")
        raise
