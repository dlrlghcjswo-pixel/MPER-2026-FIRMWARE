# Locker main-board firmware

ATmega2560 @ 16 MHz, USART3 115200 8N1 (FT232RL USB-serial).

| Board | Folder | Source | Hex |
|-------|--------|--------|-----|
| Rifle Main Board R3 | [`rifle/`](rifle/) | `main.c` | `rifle/rifle_main.hex` |
| Personal Pistol / Personal I/O Board | [`pistol/`](pistol/) | `pistol_main.c` (`PistolMain.atsln`) | `pistol/pistol_main.hex` or `Release/PistolMain.hex` |

## Build

```bash
cd firmware
make rifle    # rifle/rifle_main.hex
make pistol   # pistol/pistol_main.hex
make all      # both
```

Requires `avr-gcc` and `avr-objcopy`.

## Serial protocol (Pi locker-ui / OMS)

Same for rifle and pistol — see [`rifle/README.md`](rifle/README.md) and [`pistol/README.md`](pistol/README.md) for board-specific pin maps and behavior.

| Command | Reply |
|---------|--------|
| `STAT` | `STAT DOOR=xxxxxxxxxx SENS=xxxxxxxxxx` |
| `OPEN n` | `OK OPEN n` (`n` = 1..10) |
| `CLOSE n` | `OK CLOSE n` |

Boot prints `READY`. Bit order: left = slot 1 … right = slot 10.

## Pi / OMS

Connect the locker FT232 to the Pi (SmartSerial — not fingerprint SmartLocker). OMS uses `STAT` / `OPEN` / `CLOSE` via `locker_board.py` on the Pi.
