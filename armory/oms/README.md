# 스마트 무기고 시스템 (OMS)

> 보관함용 `windows/oms/` 전체를 복사한 무기고 작업 트리입니다.  
> 원본(`../../windows/oms/`)은 보관함용으로 그대로 유지합니다.  
> RFID / CCTV / 경광등 연동으로 UI·서버 변경은 이 폴더에서 진행합니다.

프로그램 표시 이름: **스마트 무기고 시스템**

## 포함 내용

`main.py`, `server.py`, `core.py`, `static/`, `assets/`, 실행 스크립트·빌드 산출물 등 원본과 동일하게 복사됨.

## 다른 PC로 옮기기

설정은 모두 `oms.json` 한 파일에 모입니다 (`lockers` / 인원 + `cctv` + `patlite`).

- 최소: `스마트 무기고 시스템.exe` + `oms.json`
- 예전 `cctv.json` / `patlite.json`이 있으면 첫 실행 때 `oms.json`으로 자동 흡수됩니다.

## 온디바이스 사람 AI (MediaPipe)

CCTV JPEG 스냅샷을 초당 ~0.4장 검사(별도 스레드). 사람 확정 시 표 4 → 로그·황색 경광등·PTZ.

- 모델: `assets/ai/efficientdet_lite0.tflite`
- 런타임: `OMS.bat` + `_deps`(mediapipe/opencv) 또는 동일 폴더 구성
- AI 없는 PC: 기능만 꺼지고 OMS 나머지는 정상 동작

## Patlite LR6-USB 경광등

모델: **LR6-USB (RYG)** — USB HID, VID `0x191A` / PID `0x8003`  
제어: 공식 통신 프로토콜 (`patlite.py`, Patlite Windows Python 샘플과 동일). DLL/MyPage 불필요.

| 상황 | 경광등 |
|------|--------|
| 비상개폐 / 지문 3회 이상 실패 / 네트워크 단절 | 적색 + 사이렌 (확인까지) |
| 지문 스캔(보관·불출) | 녹색 + 알림음 (절차 종료 시 소등) |

의존성 설치 (개발 PC):

```bat
python -m pip install -r requirements.txt --target _deps
```

상태/수동 테스트 API:

- `GET /api/patlite/status`
- `POST /api/patlite/test` body `{"mode":"alarm"|"ops"|"off"}`

## 예정 확장

- 무기고 대시보드·출입/자산 현황
- RFID 이벤트·인원 매핑 설정
- CCTV 뷰·알람 연동 UI
- 로그·이력·권한 관리
