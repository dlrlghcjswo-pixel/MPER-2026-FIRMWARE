/*
 * Rifle Main Board R3
 * ATmega2560 @ 16 MHz, USART3 115200 8N1 via FT232RL
 *
 * LED3 (PG0) blinks as the run indicator. LED1/LED2 are FT232 TX/RX LEDs.
 * DIP switches are unused.
 * OPEN n drives that slot for 20 seconds, then releases it.
 *
 * Slot 1 is the left-most bit in STAT. Sensors are active-low at the pin.
 * Door outputs are active-low. Boot drives every door off (pin high).
 *
 * Slot 6 on the schematic connector is named DS-IN5 / FWLR-OUT5.
 * This firmware uses PST5 (PL4) and SWD5 (PL5) for slot 6.
 */

#define F_CPU 16000000UL

#include <avr/io.h>
#include <avr/interrupt.h>
#include <stdint.h>
#include <util/atomic.h>

#define SLOT_COUNT 10
#define DOOR_OPEN_MS 20000UL
#define LED_BLINK_MS 500UL
#define RX_SIZE 64
#define LINE_SIZE 24

#define LED_PORT PORTG
#define LED_DDR DDRG
#define LED_BIT PG0

typedef struct {
    volatile uint8_t *in_pin;
    volatile uint8_t *in_ddr;
    volatile uint8_t *in_port;
    uint8_t in_mask;
    volatile uint8_t *out_port;
    volatile uint8_t *out_ddr;
    uint8_t out_mask;
} slot_io_t;

static slot_io_t slots[SLOT_COUNT];
static uint8_t door_on[SLOT_COUNT];
static uint32_t door_deadline[SLOT_COUNT];

static volatile uint32_t millis_count;
static volatile uint8_t rx_buf[RX_SIZE];
static volatile uint8_t rx_head;
static volatile uint8_t rx_tail;

static void slot_bind(uint8_t index,
                      volatile uint8_t *in_pin,
                      volatile uint8_t *in_ddr,
                      volatile uint8_t *in_port,
                      uint8_t in_bit,
                      volatile uint8_t *out_port,
                      volatile uint8_t *out_ddr,
                      uint8_t out_bit)
{
    slots[index].in_pin = in_pin;
    slots[index].in_ddr = in_ddr;
    slots[index].in_port = in_port;
    slots[index].in_mask = (uint8_t)(1u << in_bit);
    slots[index].out_port = out_port;
    slots[index].out_ddr = out_ddr;
    slots[index].out_mask = (uint8_t)(1u << out_bit);
}

static void clock_full_speed(void)
{
    /* Drop CKDIV8 so a 16 MHz crystal is not divided down. */
    CLKPR = (1 << CLKPCE);
    CLKPR = 0;
}

static void io_init(void)
{
    /* Pi slot = index + 1. Sensor pin, then door pin. */
    slot_bind(0, &PINA, &DDRA, &PORTA, PA0, &PORTJ, &DDRJ, PJ7);
    slot_bind(1, &PINA, &DDRA, &PORTA, PA6, &PORTA, &DDRA, PA5);
    slot_bind(2, &PINC, &DDRC, &PORTC, PC6, &PORTC, &DDRC, PC7);
    slot_bind(3, &PINC, &DDRC, &PORTC, PC0, &PORTC, &DDRC, PC1);
    slot_bind(4, &PIND, &DDRD, &PORTD, PD2, &PORTD, &DDRD, PD3);
    slot_bind(5, &PINL, &DDRL, &PORTL, PL4, &PORTL, &DDRL, PL5);
    slot_bind(6, &PINE, &DDRE, &PORTE, PE4, &PORTE, &DDRE, PE5);
    slot_bind(7, &PINH, &DDRH, &PORTH, PH2, &PORTH, &DDRH, PH3);
    slot_bind(8, &PINB, &DDRB, &PORTB, PB1, &PORTB, &DDRB, PB2);
    slot_bind(9, &PINB, &DDRB, &PORTB, PB7, &PORTH, &DDRH, PH7);

    for (uint8_t i = 0; i < SLOT_COUNT; i++) {
        *slots[i].out_port |= slots[i].out_mask;
        *slots[i].out_ddr |= slots[i].out_mask;
        *slots[i].in_ddr &= (uint8_t)~slots[i].in_mask;
        *slots[i].in_port |= slots[i].in_mask;
        door_on[i] = 0;
    }

    /* LED3 anode is on VCC. A low pin turns it on. */
    LED_DDR |= (1 << LED_BIT);
    LED_PORT &= (uint8_t)~(1u << LED_BIT);
}

static void timer_init(void)
{
    /* 16 MHz / 64 / 250 = 1 kHz. */
    TCCR1A = 0;
    TCCR1B = (1 << WGM12) | (1 << CS11) | (1 << CS10);
    OCR1A = 249;
    TCNT1 = 0;
    TIMSK1 = (1 << OCIE1A);
}

static void uart_init(void)
{
    /* USART3, 115200 8N1, U2X, UBRR = 16. */
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

static void door_drive(uint8_t index, uint8_t on)
{
    if (on) {
        *slots[index].out_port &= (uint8_t)~slots[index].out_mask;
        door_on[index] = 1;
        door_deadline[index] = millis() + DOOR_OPEN_MS;
    } else {
        *slots[index].out_port |= slots[index].out_mask;
        door_on[index] = 0;
    }
}

static uint8_t sensor_active(uint8_t index)
{
    return ((*slots[index].in_pin & slots[index].in_mask) == 0u) ? 1u : 0u;
}

static void send_stat(void)
{
    char door[SLOT_COUNT + 1];
    char sens[SLOT_COUNT + 1];

    for (uint8_t i = 0; i < SLOT_COUNT; i++) {
        door[i] = door_on[i] ? '1' : '0';
        sens[i] = sensor_active(i) ? '1' : '0';
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

static void handle_line_fixed(char *line)
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
            door_drive(index, 1);
            uart_puts("OK OPEN ");
        } else {
            door_drive(index, 0);
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
                handle_line_fixed(line);
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

static void service_doors(void)
{
    uint32_t now = millis();

    for (uint8_t i = 0; i < SLOT_COUNT; i++) {
        if (door_on[i] && (int32_t)(now - door_deadline[i]) >= 0) {
            door_drive(i, 0);
        }
    }
}

static void service_led(void)
{
    static uint32_t last;
    uint32_t now = millis();

    if ((now - last) >= LED_BLINK_MS) {
        last += LED_BLINK_MS;
        LED_PORT ^= (1 << LED_BIT);
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
        service_doors();
        service_led();
    }
}
