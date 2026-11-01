MCU = atmega2560
F_CPU = 16000000UL

CC = avr-gcc
OBJCOPY = avr-objcopy
SIZE = avr-size

CFLAGS = -mmcu=$(MCU) -DF_CPU=$(F_CPU) -Os -Wall -Wextra -std=gnu99 \
	-ffunction-sections -fdata-sections
LDFLAGS = -mmcu=$(MCU) -Wl,--gc-sections

.PHONY: all clean

all: pistol_main.hex

pistol_main.elf: pistol_main.c
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ pistol_main.c

pistol_main.hex: pistol_main.elf
	$(OBJCOPY) -O ihex -R .eeprom $< $@
	-$(SIZE) $<

clean:
	-del /Q pistol_main.elf pistol_main.hex 2>nul || rm -f pistol_main.elf pistol_main.hex
