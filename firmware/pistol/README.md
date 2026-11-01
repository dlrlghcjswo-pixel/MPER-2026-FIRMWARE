# 권총 보관함 보드

`pistol_main.c`를 ATmega2560에 굽습니다. 핀과 출력 극성은 2025-04-15에 검증된 권총 펌웨어와 같습니다. 라즈베리파이는 소총 보드와 같이 USART3, 115200 8N1로 `STAT` / `OPEN n` / `CLOSE n`을 보냅니다.

```bash
cd firmware/pistol
make
```

## 응답

- `SENS` = 권총 있음. PST 핀이 0이면 `1`
- `DOOR` = 문 열림. DS 핀이 1이면 `1`
- `OPEN n` = 자물쇠를 2초 열었다가 잠그고, 적색 창은 10초 동안 켜 둠. UVC는 끔
- 문이 닫혀 있고 권총이 있으면 녹색 점등. 문이 열리면 적색이 0.5초마다 깜박임
- 권총이 10초 계속 있으면 UVC를 켜고, 20분 뒤 또는 그 칸을 열면 끔

자물쇠, 창 램프, UVC는 모두 핀을 내리면 동작합니다. 평소에는 올려 둡니다.

## 핀

| 칸 | 권총 PST | 문 DS | 자물쇠 SWD | UVC | 적색 | 녹색 |
|----|----------|-------|------------|-----|------|------|
| 1 | PA0 | PA2 | PJ7 | PA1 | PA3 | PA4 |
| 2 | PA6 | PJ6 | PA5 | PA7 | PJ5 | PJ4 |
| 3 | PC6 | PC4 | PC7 | PC5 | PC3 | PC2 |
| 4 | PC0 | PD6 | PC1 | PD7 | PD5 | PD4 |
| 5 | PD2 | PD0 | PD3 | PD1 | PL7 | PL6 |
| 6 | PL4 | PL2 | PL5 | PL3 | PL1 | PL0 |
| 7 | PE4 | PE2 | PE5 | PE3 | PE1 | PE0 |
| 8 | PH2 | PH0 | PH3 | PH1 | PE7 | PE6 |
| 9 | PB1 | PH6 | PB2 | PB0 | PH5 | PH4 |
| 10 | PB7 | PB5 | PH7 | PB6 | PB4 | PB3 |

RUN 램프는 PG0입니다. USB 시리얼은 PJ0, PJ1입니다.
