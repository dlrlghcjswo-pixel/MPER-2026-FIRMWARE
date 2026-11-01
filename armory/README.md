# 무기고(Armory) 시스템

소총/권총 보관함(`fingerprint/`, `windows/oms/`, `firmware/`)에서 검증한 인증·상태·OMS 구조를 응용한 **무기고 통합 관리** 영역입니다.

펌웨어(MCU)는 이 폴더에 두지 않습니다. 보드/릴레이 제어가 필요하면 기존 `firmware/`를 참고하거나 별도 보드 펌웨어로 확장합니다.

## 폴더 구성

| 경로 | 역할 |
|------|------|
| `raspberry/` | 라즈베리파이 측 (현장 게이트웨이·연동) |
| `oms/` | Windows OMS (관제·설정·이력 UI) |

## 연동 예정

- **RFID** — 인원/자산 식별
- **CCTV** — 출입·이상 상황 영상 연동
- **경광등** — 알람·경보 출력

## 기존 보관함과의 관계

- 보관함 전용 코드는 **원본 그대로** 유지·운용합니다.
  - Pi/지문 UI: `fingerprint/`
  - OMS: `windows/oms/`
  - MCU: `firmware/`
- 무기고용으로 아래를 **복사**해 두었습니다. 이후 수정은 `armory/`에서만 합니다.
  - `fingerprint/` → `armory/raspberry/`
  - `windows/oms/` → `armory/oms/`
