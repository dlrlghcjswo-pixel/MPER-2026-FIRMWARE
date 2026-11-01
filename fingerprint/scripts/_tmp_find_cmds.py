from pathlib import Path
import shutil

assets = Path(r"C:\Users\Zellootec\.cursor\projects\d-MPER-2026-FIRMWARE\assets")
dst_pi = Path(r"D:\MPER-2026 FIRMWARE\fingerprint\ui\static\img")
dst_oms = Path(r"D:\MPER-2026 FIRMWARE\windows\oms\assets")
dst_pi.mkdir(parents=True, exist_ok=True)
dst_oms.mkdir(parents=True, exist_ok=True)

pistol = next(assets.glob("*K5*a9b45877*"))
finger = next(assets.glob("*PNG-bb5d7b5f*"))
monitor = next(assets.glob("*9f2a1857*"))
print("src", pistol.name, finger.name, monitor.name)
for dst in (dst_pi, dst_oms):
    shutil.copy2(pistol, dst / "pistol.png")
    shutil.copy2(finger, dst / "fingerprint.png")
    shutil.copy2(monitor, dst / "monitor.png")
    for name in ("pistol.png", "fingerprint.png", "monitor.png"):
        p = dst / name
        print(p, p.stat().st_size)
