MCU = atmega2560
F_CPU = 16000000UL
TARGET = rifle_main

CC = avr-gcc
OBJCOPY = avr-objcopy

CFLAGS = -mmcu=$(MCU) -DF_CPU=$(F_CPU) -Os -Wall -Wextra -std=gnu99 \
	-ffunction-sections -fdata-sections
LDFLAGS = -mmcu=$(MCU) -Wl,--gc-sections

.PHONY: all clean

all: $(TARGET).hex

$(TARGET).elf: main.c
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ main.c

$(TARGET).hex: $(TARGET).elf
	$(OBJCOPY) -O ihex -R .eeprom $< $@
	avr-size --mcu=$(MCU) --format=avr $(TARGET).elf

clean:
	-del /Q $(TARGET).elf $(TARGET).hex 2>nul || rm -f $(TARGET).elf $(TARGET).hex
