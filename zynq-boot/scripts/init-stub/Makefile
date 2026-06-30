# Build init_stub.elf for Zynq-7000 (CLI route).
#
# PREREQ: copy ps7_init.c + ps7_init.h for YOUR board into this directory first
#   (they live in the .xsa hardware export, or the Vitis standalone BSP's libsrc).
#
# Then:  make            -> init_stub.elf
# Use:   jtag_qspi_flash.sh --arch zynq --fsbl /abs/init_stub.elf --uboot <dcc-uboot>
#
# CROSS defaults to the bare-metal A9 toolchain shipped with Vitis (source the Vitis
# settings first so it's on PATH), or any arm-none-eabi-gcc. Override: make CROSS=...
CROSS  ?= arm-none-eabi-
CFLAGS  = -mcpu=cortex-a9 -O2 -ffreestanding -nostdlib -Wall -I.

all: init_stub.elf

init_stub.elf: start.S init_stub.c ps7_init.c init_stub.ld
	$(CROSS)gcc $(CFLAGS) -T init_stub.ld start.S init_stub.c ps7_init.c -o $@
	@echo "built $@ — point jtag_qspi_flash.sh --fsbl at it"

clean:
	rm -f init_stub.elf

.PHONY: all clean
