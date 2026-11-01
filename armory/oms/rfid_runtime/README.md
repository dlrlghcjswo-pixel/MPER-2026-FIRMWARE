# RFID runtime (AE SDK)

OMS `rfid.py`는 여기의 `rfidlib_reader.dll`을 가장 먼저 찾습니다. (경로에 한글이 없어 DLL 로딩이 안정적입니다.)

## 복사할 파일

`RFID/AE SDK 20260121/Samples/rfid.sdk.windows/rfid_reader_HF-高频读卡器/run/win64/unicode_debug` 에서:

- `rfidlib_reader.dll`
- `rfidlib_aip_iso15693.dll` (및 필요 시 다른 `rfidlib_aip_*.dll`)
- `Drivers\` 폴더 전체

RL17W 현장 연결(RS485): 전원 **12VDC**, 핀점퍼 **485**, A/B → USB-RS485 → PC **COM**.  
통신 기본값: **COM / 38400 / 8N1 / BusAddr 255**.
