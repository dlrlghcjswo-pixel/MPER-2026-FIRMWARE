# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

root = Path(SPECPATH)
deps = root / "_deps"

hid_datas, hid_binaries, hid_hidden = collect_all("hid")
if (deps / "hid.cp312-win_amd64.pyd").is_file():
    hid_binaries = list(hid_binaries) + [(str(deps / "hid.cp312-win_amd64.pyd"), ".")]
hid_binaries = list(hid_binaries) + list(collect_dynamic_libs("hid"))

# On-device person AI (MediaPipe + OpenCV + NumPy) from portable _deps
ai_datas: list = []
ai_binaries: list = []
ai_hidden: list = []
for pkg in ("mediapipe", "cv2", "numpy", "google", "absl", "flatbuffers", "sounddevice", "PIL", "matplotlib"):
    try:
        d, b, h = collect_all(pkg)
        ai_datas += list(d)
        ai_binaries += list(b)
        ai_hidden += list(h)
    except Exception:
        pass

a = Analysis(
    ["main.py"],
    pathex=[str(deps)] if deps.is_dir() else [],
    binaries=list(hid_binaries) + ai_binaries,
    datas=[
        ("static", "static"),
        ("assets", "assets"),
        ("rfid_runtime", "rfid_runtime"),
    ] + list(hid_datas) + ai_datas,
    hiddenimports=[
        "server",
        "core",
        "cctv",
        "patlite",
        "rfid",
        "oms_bundle",
        "person_ai",
        "person_track",
        "hid",
        "mediapipe",
        "mediapipe.tasks",
        "mediapipe.tasks.c",
        "mediapipe.tasks.python",
        "mediapipe.tasks.python.vision",
        "mediapipe.tasks.python.core",
        "mediapipe.tasks.python.components",
        "cv2",
        "numpy",
        *hid_hidden,
        *ai_hidden,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# Show company logo for the whole unpack + Python boot of the large one-file exe.
splash = Splash(
    str(root / "assets" / "zellootec.png"),
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(36, 400),
    text_size=13,
    text_color="black",
    minify_script=True,
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    splash,
    splash.binaries,
    [],
    name="스마트 무기고 시스템",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["assets/favicon.ico"],
)
