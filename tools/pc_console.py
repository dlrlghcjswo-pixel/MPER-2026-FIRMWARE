"""PC console for Rifle Main Board R3.

The board USB connector is the FT232RL. Windows shows it as a COM port.
A Raspberry Pi is not required for this test. The Pi will send the same lines later.

  python tools/pc_console.py
  python tools/pc_console.py COM5
  python tools/pc_console.py COM5 STAT
"""

import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("pyserial이 없습니다. python -m pip install pyserial")
    sys.exit(1)

BAUD = 115200


def list_com_ports():
    ports = list(list_ports.comports())
    if not ports:
        print("COM 포트가 없습니다. 보드 USB를 이 PC에 연결한 뒤 다시 실행하세요.")
        return []
    print("COM 포트")
    for port in ports:
        print(f"  {port.device:8}  {port.description}")
    return ports


def choose_port(prefer):
    if prefer:
        return prefer
    ports = list_com_ports()
    if len(ports) == 1:
        print(f"이 포트를 사용합니다: {ports[0].device}")
        return ports[0].device
    if not ports:
        return None
    typed = input("사용할 COM 번호 (예: COM5): ").strip()
    return typed or None


def open_port(name):
    ser = serial.Serial()
    ser.port = name
    ser.baudrate = BAUD
    ser.bytesize = serial.EIGHTBITS
    ser.parity = serial.PARITY_NONE
    ser.stopbits = serial.STOPBITS_ONE
    ser.timeout = 1.0
    ser.write_timeout = 1.0
    ser.rtscts = False
    ser.dsrdtr = False
    ser.open()
    time.sleep(0.05)
    ser.reset_input_buffer()
    return ser


def transact(ser, command):
    text = command.strip()
    if not text:
        return ""
    try:
        payload = (text + "\n").encode("ascii")
    except UnicodeEncodeError:
        return "영문 명령만 보낼 수 있습니다. 예: STAT, OPEN 1, CLOSE 1"
    ser.write(payload)
    ser.flush()
    line = ser.readline().decode("ascii", errors="replace").strip()
    return line


def interactive(ser):
    print("명령을 입력하세요. STAT, OPEN 1, CLOSE 1. 종료는 quit.")
    print("OPEN 은 그 칸을 20초 동안 열었다가 닫습니다.")
    while True:
        try:
            command = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if command.lower() in ("quit", "exit", "q"):
            return
        if not command:
            continue
        reply = transact(ser, command)
        print(reply if reply else "(응답 없음)")


def main():
    args = sys.argv[1:]
    port = None
    command = None
    if args:
        port = args[0]
        if len(args) > 1:
            command = " ".join(args[1:])
    else:
        port = choose_port(None)

    if not port:
        return 1

    try:
        ser = open_port(port)
    except serial.SerialException as exc:
        print(f"{port} 를 열 수 없습니다: {exc}")
        list_com_ports()
        return 1

    print(f"{port}  {BAUD} 8N1")
    try:
        if command:
            reply = transact(ser, command)
            print(reply if reply else "(응답 없음)")
        else:
            interactive(ser)
    finally:
        ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
