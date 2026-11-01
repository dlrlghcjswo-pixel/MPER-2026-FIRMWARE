/*
 * Personal pistol locker, ATmega2560 @ 16 MHz.
 *
 * Pin map and drive levels are the verified 2025-04-15 pistol firmware
 * (firmware_20250415), not the sequential Personal I/O port table.
 * The Raspberry Pi still speaks ASCII at 115200 8N1 on USART3 (PJ0/PJ1):
 *   STAT     -> STAT DOOR=xxxxxxxxxx SENS=xxxxxxxxxx
 *   OPEN n   -> OK OPEN n
 *   CLOSE n  -> OK CLOSE n
 *
 * SENS 1 = pistol present (PST pin reads 0)
 * DOOR 1 = door switch open (DS pin reads 1)
 * SWD, window LEDs, and UVC are active-low. Idle is high.
 * OPEN releases the maglock for 2 seconds and holds the open lamp for 10 seconds.
 * A stored pistol turns UVC on after 10 seconds and off after 20 minutes,
 * or immediately when that slot is opened.
 */

#define F_CPU 16000000UL

#include <avr/io.h>
#include <avr/interrupt.h>
#include <stdint.h>
#include <util/atomic.h>

#define SLOT_COUNT 10
#define UNLOCK_MS 2000UL
#define OPEN_LAMP_MS 10000UL
#define UVC_DELAY_MS 10000UL
#define UVC_RUN_MS 1200000UL
#define LED_BLINK_MS 500UL
#define RX_SIZE 64
#define LINE_SIZE 24

typedef struct {
    volatile uint8_t *pst_pin;
    uint8_t pst_mask;
    volatile uint8_t *ds_pin;
    uint8_t ds_mask;
    volatile uint8_t *swd_port;
    uint8_t swd_mask;
    volatile uint8_t *uvc_port;
    uint8_t uvc_mask;
    volatile uint8_t *red_port;
    uint8_t red_mask;
    volatile uint8_t *green_port;
    uint8_t green_mask;
} slot_io_t;

static slot_io_t slots[SLOT_COUNT];
static uint8_t unlock_on[SLOT_COUNT];
static uint32_t unlock_deadline[SLOT_COUNT];
static uint8_t lamp_open[SLOT_COUNT];
static uint32_t lamp_deadline[SLOT_COUNT];
static uint8_t uvc_on[SLOT_COUNT];
static uint32_t present_since[SLOT_COUNT];
static uint8_t present_timing[SLOT_COUNT];
static uint32_t uvc_deadline[SLOT_COUNT];

static volatile uint32_t millis_count;
static volatile uint8_t rx_buf[RX_SIZE];
static volatile uint8_t rx_head;
static volatile uint8_t rx_tail;

static void slot_bind(uint8_t index,
                      volatile uint8_t *pst_pin, uint8_t pst_bit,
                      volatile uint8_t *ds_pin, uint8_t ds_bit,
                      volatile uint8_t *swd_port, uint8_t swd_bit,
                      volatile uint8_t *uvc_port, uint8_t uvc_bit,
                      volatile uint8_t *red_port, uint8_t red_bit,
                      volatile uint8_t *green_port, uint8_t green_bit)
{
    slots[index].pst_pin = pst_pin;
    slots[index].pst_mask = (uint8_t)(1u << pst_bit);
    slots[index].ds_pin = ds_pin;
    slots[index].ds_mask = (uint8_t)(1u << ds_bit);
    slots[index].swd_port = swd_port;
    slots[index].swd_mask = (uint8_t)(1u << swd_bit);
    slots[index].uvc_port = uvc_port;
    slots[index].uvc_mask = (uint8_t)(1u << uvc_bit);
    slots[index].red_port = red_port;
    slots[index].red_mask = (uint8_t)(1u << red_bit);
    slots[index].green_port = green_port;
    slots[index].green_mask = (uint8_t)(1u << green_bit);
}

static void clock_full_speed(void)
{
    CLKPR = (1 << CLKPCE);
    CLKPR = 0;
}

static void io_init(void)
{
    /* Directions and idle levels copied from the verified Init(). */
    DDRA = 0xBA;
    PORTA = 0xBA;
    DDRB = 0x5D;
    PORTB = 0x5D;
    DDRC = 0xAE;
    PORTC = 0xAE;
    DDRD = 0xBA;
    PORTD = 0xBA;
    DDRE = 0xEB;
    PORTE = 0xEB;
    DDRG = 0x01;
    PORTG = 0x01;
    DDRH = 0xBA;
    PORTH = 0xBA;
    DDRJ = 0xB0;
    PORTJ = 0xB0;
    DDRL = 0xEB;
    PORTL = 0xEB;

    slot_bind(0, &PINA, PA0, &PINA, PA2, &PORTJ, PJ7, &PORTA, PA1, &PORTA, PA3, &PORTA, PA4);
    slot_bind(1, &PINA, PA6, &PINJ, PJ6, &PORTA, PA5, &PORTA, PA7, &PORTJ, PJ5, &PORTJ, PJ4);
    slot_bind(2, &PINC, PC6, &PINC, PC4, &PORTC, PC7, &PORTC, PC5, &PORTC, PC3, &PORTC, PC2);
    slot_bind(3, &PINC, PC0, &PIND, PD6, &PORTC, PC1, &PORTD, PD7, &PORTD, PD5, &PORTD, PD4);
    slot_bind(4, &PIND, PD2, &PIND, PD0, &PORTD, PD3, &PORTD, PD1, &PORTL, PL7, &PORTL, PL6);
    slot_bind(5, &PINL, PL4, &PINL, PL2, &PORTL, PL5, &PORTL, PL3, &PORTL, PL1, &PORTL, PL0);
    slot_bind(6, &PINE, PE4, &PINE, PE2, &PORTE, PE5, &PORTE, PE3, &PORTE, PE1, &PORTE, PE0);
    slot_bind(7, &PINH, PH2, &PINH, PH0, &PORTH, PH3, &PORTH, PH1, &PORTE, PE7, &PORTE, PE6);
    slot_bind(8, &PINB, PB1, &PINH, PH6, &PORTB, PB2, &PORTB, PB0, &PORTH, PH5, &PORTH, PH4);
    slot_bind(9, &PINB, PB7, &PINB, PB5, &PORTH, PH7, &PORTB, PB6, &PORTB, PB4, &PORTB, PB3);
}

static void timer_init(void)
{
    TCCR1A = 0;
    TCCR1B = (1 << WGM12) | (1 << CS11) | (1 << CS10);
    OCR1A = 249;
    TCNT1 = 0;
    TIMSK1 = (1 << OCIE1A);
}

static void uart_init(void)
{
    UCSR3A = (1 << U2X3);
    UBRR3 = 16;
    UCSR3B = (1 << RXEN3) | (1 << TXEN3) | (1 << RXCIE3);
    UCSR3C = (1 << UCSZ31) | (1 << UCSZ30);
}

ISR(TIMER1_COMPA_vect)
{
    millis_count++;
}

ISR(USART3_RX_vect)
{
    uint8_t data = UDR3;
    uint8_t next = (uint8_t)((rx_head + 1u) & (RX_SIZE - 1u));
    if (next != rx_tail) {
        rx_buf[rx_head] = data;
        rx_head = next;
    }
}

static uint32_t millis(void)
{
    uint32_t now;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        now = millis_count;
    }
    return now;
}

static int rx_get(uint8_t *out)
{
    if (rx_head == rx_tail) {
        return 0;
    }
    *out = rx_buf[rx_tail];
    rx_tail = (uint8_t)((rx_tail + 1u) & (RX_SIZE - 1u));
    return 1;
}

static void uart_putc(char c)
{
    while (!(UCSR3A & (1 << UDRE3))) {
    }
    UDR3 = (uint8_t)c;
}

static void uart_puts(const char *text)
{
    while (*text) {
        uart_putc(*text++);
    }
}

static void drive_low(volatile uint8_t *port, uint8_t mask)
{
    *port &= (uint8_t)~mask;
}

static void drive_high(volatile uint8_t *port, uint8_t mask)
{
    *port |= mask;
}

static uint8_t pistol_present(uint8_t index)
{
    return ((*slots[index].pst_pin & slots[index].pst_mask) == 0u) ? 1u : 0u;
}

static uint8_t door_is_open(uint8_t index)
{
    return ((*slots[index].ds_pin & slots[index].ds_mask) != 0u) ? 1u : 0u;
}

static void set_unlock(uint8_t index, uint8_t on)
{
    if (on) {
        drive_low(slots[index].swd_port, slots[index].swd_mask);
        unlock_on[index] = 1;
        unlock_deadline[index] = millis() + UNLOCK_MS;
    } else {
        drive_high(slots[index].swd_port, slots[index].swd_mask);
        unlock_on[index] = 0;
    }
}

static void set_uvc(uint8_t index, uint8_t on)
{
    if (on) {
        drive_low(slots[index].uvc_port, slots[index].uvc_mask);
        uvc_on[index] = 1;
        uvc_deadline[index] = millis() + UVC_RUN_MS;
    } else {
        drive_high(slots[index].uvc_port, slots[index].uvc_mask);
        uvc_on[index] = 0;
    }
}

static void lamp_red(uint8_t index, uint8_t on)
{
    if (on) {
        drive_low(slots[index].red_port, slots[index].red_mask);
    } else {
        drive_high(slots[index].red_port, slots[index].red_mask);
    }
}

static void lamp_green(uint8_t index, uint8_t on)
{
    if (on) {
        drive_low(slots[index].green_port, slots[index].green_mask);
    } else {
        drive_high(slots[index].green_port, slots[index].green_mask);
    }
}

static void begin_open(uint8_t index)
{
    uint32_t now = millis();
    set_uvc(index, 0);
    set_unlock(index, 1);
    lamp_open[index] = 1;
    lamp_deadline[index] = now + OPEN_LAMP_MS;
    lamp_red(index, 1);
    lamp_green(index, 0);
}

static void end_open(uint8_t index)
{
    set_unlock(index, 0);
    lamp_open[index] = 0;
}

static void send_stat(void)
{
    char door[SLOT_COUNT + 1];
    char sens[SLOT_COUNT + 1];
    uint8_t i;

    for (i = 0; i < SLOT_COUNT; i++) {
        door[i] = door_is_open(i) ? '1' : '0';
        sens[i] = pistol_present(i) ? '1' : '0';
    }
    door[SLOT_COUNT] = '\0';
    sens[SLOT_COUNT] = '\0';
    uart_puts("STAT DOOR=");
    uart_puts(door);
    uart_puts(" SENS=");
    uart_puts(sens);
    uart_puts("\r\n");
}

static int parse_slot(const char *text, uint8_t *index)
{
    uint16_t value = 0;

    if (*text < '0' || *text > '9') {
        return 0;
    }
    while (*text >= '0' && *text <= '9') {
        value = (uint16_t)(value * 10u + (uint16_t)(*text - '0'));
        if (value > SLOT_COUNT) {
            return 0;
        }
        text++;
    }
    if (*text != '\0' || value < 1u) {
        return 0;
    }
    *index = (uint8_t)(value - 1u);
    return 1;
}

static void send_slot_number(uint8_t index)
{
    uint8_t number = (uint8_t)(index + 1u);
    if (number >= 10u) {
        uart_putc('1');
        uart_putc('0');
    } else {
        uart_putc((char)('0' + number));
    }
}

static void handle_line(char *line)
{
    char *arg;
    uint8_t index;
    uint8_t open_cmd;

    while (*line == ' ') {
        line++;
    }
    arg = line;
    while (*arg && *arg != ' ') {
        if (*arg >= 'a' && *arg <= 'z') {
            *arg = (char)(*arg - 32);
        }
        arg++;
    }
    if (*arg == ' ') {
        *arg++ = '\0';
        while (*arg == ' ') {
            arg++;
        }
    }
    if (line[0] == '\0') {
        return;
    }
    if (line[0] == 'S' && line[1] == 'T' && line[2] == 'A' && line[3] == 'T' && line[4] == '\0') {
        if (*arg != '\0') {
            uart_puts("ERR\r\n");
            return;
        }
        send_stat();
        return;
    }
    open_cmd = (line[0] == 'O' && line[1] == 'P' && line[2] == 'E' && line[3] == 'N' && line[4] == '\0');
    if (open_cmd ||
        (line[0] == 'C' && line[1] == 'L' && line[2] == 'O' && line[3] == 'S' && line[4] == 'E' && line[5] == '\0')) {
        if (!parse_slot(arg, &index)) {
            uart_puts("ERR\r\n");
            return;
        }
        if (open_cmd) {
            begin_open(index);
            uart_puts("OK OPEN ");
        } else {
            end_open(index);
            uart_puts("OK CLOSE ");
        }
        send_slot_number(index);
        uart_puts("\r\n");
        return;
    }
    uart_puts("ERR\r\n");
}

static void service_uart(void)
{
    static char line[LINE_SIZE];
    static uint8_t len;
    static uint8_t overflow;
    uint8_t data;

    while (rx_get(&data)) {
        if (data == '\r' || data == '\n') {
            line[len] = '\0';
            if (!overflow) {
                handle_line(line);
            }
            len = 0;
            overflow = 0;
            continue;
        }
        if (overflow) {
            continue;
        }
        if (len < (LINE_SIZE - 1u)) {
            line[len++] = (char)data;
        } else {
            overflow = 1;
            len = 0;
            uart_puts("ERR\r\n");
        }
    }
}

static void service_locks(void)
{
    uint32_t now = millis();
    uint8_t i;

    for (i = 0; i < SLOT_COUNT; i++) {
        if (unlock_on[i] && (int32_t)(now - unlock_deadline[i]) >= 0) {
            set_unlock(i, 0);
        }
        if (lamp_open[i] && (int32_t)(now - lamp_deadline[i]) >= 0) {
            lamp_open[i] = 0;
        }
    }
}

static void service_uvc(void)
{
    uint32_t now = millis();
    uint8_t i;

    for (i = 0; i < SLOT_COUNT; i++) {
        if (lamp_open[i]) {
            present_timing[i] = 0;
            if (uvc_on[i]) {
                set_uvc(i, 0);
            }
            continue;
        }
        if (!pistol_present(i)) {
            present_timing[i] = 0;
            continue;
        }
        if (!present_timing[i]) {
            present_timing[i] = 1;
            present_since[i] = now;
        }
        if (!uvc_on[i] && (int32_t)(now - present_since[i]) >= (int32_t)UVC_DELAY_MS) {
            set_uvc(i, 1);
        }
        if (uvc_on[i] && (int32_t)(now - uvc_deadline[i]) >= 0) {
            set_uvc(i, 0);
            present_since[i] = now;
        }
    }
}

static void service_lamps(void)
{
    uint32_t now = millis();
    uint8_t blink = (uint8_t)((now / LED_BLINK_MS) & 1u);
    uint8_t i;

    for (i = 0; i < SLOT_COUNT; i++) {
        if (lamp_open[i]) {
            lamp_red(i, 1);
            lamp_green(i, 0);
            continue;
        }
        if (door_is_open(i)) {
            lamp_green(i, 0);
            lamp_red(i, blink);
            continue;
        }
        if (pistol_present(i)) {
            lamp_red(i, 0);
            lamp_green(i, 1);
        } else {
            lamp_red(i, 0);
            lamp_green(i, 0);
        }
    }
}

static void service_run_led(void)
{
    static uint32_t last;
    uint32_t now = millis();

    if ((now - last) >= LED_BLINK_MS) {
        last += LED_BLINK_MS;
        PORTG ^= (1 << PG0);
    }
}

int main(void)
{
    clock_full_speed();
    io_init();
    timer_init();
    uart_init();
    sei();
    uart_puts("READY\r\n");

    for (;;) {
        service_uart();
        service_locks();
        service_uvc();
        service_lamps();
        service_run_led();
    }
}
