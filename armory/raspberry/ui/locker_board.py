"""Serial client for Rifle/Pistol Main Board (ATmega2560 + FT232RL).

Slot numbers are the same as the locker UI: 1 through 10.
This port is the locker FT232. The fingerprint reader is a second FT232
whose serial id is SmartLocker, and it must not be opened here.

Board SENS/DOOR are polled on a background thread so /api/status never
blocks OMS on a slow or flaky USART (common on pistol boards).
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional

import serial
from serial.tools import list_ports

BAUD = 115200
FTDI_VID = 0x0403
FTDI_PID = 0x6001
FINGER_SERIAL = "SmartLocker"
LOCKER_SERIAL = "SmartSerial"
POLL_MS = 0.20


class LockerBoardError(RuntimeError):
    pass


class LockerBoard:
    def __init__(self) -> None:
        self._io = threading.Lock()
        self._cache_lock = threading.Lock()
        self._ser: serial.Serial | None = None
        self._cached: Dict[str, object] = {}
        self._stop = threading.Event()
        self._prev_sens = ""
        self._prev_door = ""
        self._on_change = None
        self._thread = threading.Thread(target=self._poll_loop, name="locker-board", daemon=True)
        self._thread.start()

    def set_change_hook(self, callback) -> None:
        """callback(prev_door, prev_sens, door, sens) after each successful STAT."""
        self._on_change = callback

    def close(self) -> None:
        self._stop.set()
        with self._io:
            self._close_unlocked()

    def open_slot(self, slot: int) -> str:
        self._check_slot(slot)
        reply = self._transact(f"OPEN {slot}")
        if reply != f"OK OPEN {slot}":
            raise LockerBoardError(reply or "no reply")
        try:
            self._refresh_once()
        except LockerBoardError:
            pass
        return reply

    def close_slot(self, slot: int) -> str:
        self._check_slot(slot)
        reply = self._transact(f"CLOSE {slot}")
        if reply != f"OK CLOSE {slot}":
            raise LockerBoardError(reply or "no reply")
        try:
            self._refresh_once()
        except LockerBoardError:
            pass
        return reply

    def status(self) -> Dict[str, object]:
        """Return latest board map without blocking on serial I/O."""
        with self._cache_lock:
            cached = dict(self._cached)
        if len(str(cached.get("sens") or "")) != 10:
            raise LockerBoardError("no reply")
        return cached

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._refresh_once()
            except LockerBoardError:
                with self._io:
                    self._close_unlocked()
            self._stop.wait(POLL_MS)

    def _refresh_once(self) -> None:
        last_err: Optional[Exception] = None
        for _ in range(2):
            try:
                reply = self._transact("STAT")
                door, sens = _parse_stat(reply)
                payload: Dict[str, object] = {
                    "reply": reply,
                    "door": door,
                    "sens": sens,
                    "ok": True,
                    "at": time.time(),
                }
                with self._cache_lock:
                    prev_door = self._prev_door
                    prev_sens = self._prev_sens
                    self._cached = payload
                    self._prev_door = door
                    self._prev_sens = sens
                if prev_sens != sens or prev_door != door:
                    try:
                        log_dir = Path("/home/pi/fingerprint/logs")
                        log_dir.mkdir(parents=True, exist_ok=True)
                        with open(log_dir / "board_chg.log", "a", encoding="utf-8") as log:
                            log.write(
                                f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                                f"prev_door={prev_door} door={door} "
                                f"prev_sens={prev_sens} sens={sens}\n"
                            )
                    except Exception:
                        pass
                hook = self._on_change
                if hook is not None and (prev_sens != sens or prev_door != door):
                    try:
                        hook(prev_door, prev_sens, door, sens)
                    except Exception as exc:
                        try:
                            with open("/home/pi/fingerprint/logs/board_chg.log", "a", encoding="utf-8") as log:
                                log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} hook_error={exc!r}\n")
                        except Exception:
                            pass
                return
            except (LockerBoardError, serial.SerialException, OSError) as exc:
                last_err = exc
                with self._io:
                    self._close_unlocked()
                time.sleep(0.03)
        raise LockerBoardError(str(last_err) if last_err else "no reply")

    def _transact(self, command: str) -> str:
        with self._io:
            try:
                port = self._ensure_unlocked()
                self._drain_unlocked(port)
                port.write((command + "\n").encode("ascii"))
                port.flush()
                deadline = time.time() + 0.45
                while time.time() < deadline:
                    line = port.readline().decode("ascii", errors="replace").strip()
                    if not line:
                        continue
                    if line == "READY" or line.startswith("{"):
                        continue
                    if command == "STAT" and "SENS=" not in line and not line.startswith("STAT"):
                        continue
                    if command.startswith("OPEN") and not (line.startswith("OK") or line.startswith("ERR")):
                        continue
                    if command.startswith("CLOSE") and not (line.startswith("OK") or line.startswith("ERR")):
                        continue
                    return line
                raise LockerBoardError("no reply")
            except (serial.SerialException, OSError) as exc:
                self._close_unlocked()
                raise LockerBoardError(str(exc)) from exc

    def _drain_unlocked(self, port: serial.Serial) -> None:
        try:
            port.reset_input_buffer()
        except serial.SerialException:
            pass
        old = port.timeout
        port.timeout = 0.01
        try:
            for _ in range(6):
                chunk = port.readline()
                if not chunk:
                    break
        finally:
            port.timeout = old

    def _ensure_unlocked(self) -> serial.Serial:
        if self._ser is not None and self._ser.is_open:
            return self._ser
        port_name = _port_name()
        self._ser = serial.Serial()
        self._ser.port = port_name
        self._ser.baudrate = BAUD
        self._ser.timeout = 0.35
        self._ser.write_timeout = 0.35
        self._ser.rtscts = False
        self._ser.dsrdtr = False
        self._ser.dtr = False
        self._ser.rts = False
        self._ser.open()
        time.sleep(0.03)
        self._ser.reset_input_buffer()
        return self._ser

    def _close_unlocked(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except serial.SerialException:
                pass
            self._ser = None

    @staticmethod
    def _check_slot(slot: int) -> None:
        if slot < 1 or slot > 10:
            raise LockerBoardError("invalid slot")


def _port_name() -> str:
    chosen = os.environ.get("LOCKER_PORT", "").strip()
    if chosen:
        return chosen
    ports = list(list_ports.comports())
    ftdi = [port for port in ports if port.vid == FTDI_VID and port.pid == FTDI_PID]
    for port in ftdi:
        if LOCKER_SERIAL in (port.serial_number or ""):
            return port.device
    for port in ftdi:
        if FINGER_SERIAL not in (port.serial_number or ""):
            return port.device
    for port in ports:
        device = port.device or ""
        serial_number = port.serial_number or ""
        if "ttyUSB" in device and FINGER_SERIAL not in serial_number:
            return device
    return "/dev/ttyUSB0"


def _parse_stat(reply: str) -> tuple[str, str]:
    door = ""
    sens = ""
    for part in reply.split():
        if part.startswith("DOOR="):
            door = part.split("=", 1)[1]
        elif part.startswith("SENS="):
            sens = part.split("=", 1)[1]
    if len(door) != 10 or len(sens) != 10:
        raise LockerBoardError(reply or "no reply")
    return door, sens


board = LockerBoard()
