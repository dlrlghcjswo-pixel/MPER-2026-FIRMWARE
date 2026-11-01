MCU = atmega2560
F_CPU = 16000000UL

CC = avr-gcc
OBJCOPY = avr-objcopy
SIZE = avr-size

CFLAGS = -mmcu=$(MCU) -DF_CPU=$(F_CPU) -Os -Wall -Wextra -std=gnu99 \
	-ffunction-sections -fdata-sections
LDFLAGS = -mmcu=$(MCU) -Wl,--gc-sections

.PHONY: all clean

all: rifle_main.hex

rifle_main.elf: main.c
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ main.c

rifle_main.hex: rifle_main.elf
	$(OBJCOPY) -O ihex -R .eeprom $< $@
	-$(SIZE) $<

clean:
	-del /Q rifle_main.elf rifle_main.hex 2>nul || rm -f rifle_main.elf rifle_main.hex
