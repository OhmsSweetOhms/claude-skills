# init-stub — a no-race PS-init ELF for JTAG flashing

**STATUS: authored, NOT yet hardware-verified.**

A tiny ELF that brings the Zynq-7000 PS up (`ps7_init`: clocks, DDR, MIO incl. QSPI) and
then **sits forever** (`while(1)`) instead of booting from QSPI. Hand it to
`jtag_qspi_flash` as the `--fsbl` input and there is **no race to halt** — the stub never
touches the boot device, so you stop it whenever and `dow` U-Boot over it. It pins the
same JTAG-handoff behavior the stock Xilinx FSBL uses in JTAG boot mode
(`FsblHandoffJtagExit` == `while(1)`), regardless of the hardwired-QSPI strap.

Use it when you'd rather hand over one self-contained "make the PS ready" ELF than a
`ps7_init.tcl`. If you already have `ps7_init.tcl` or the `.xsa` (which auto-extracts it),
those are still simpler — xsdb runs the register writes directly, nothing to build.

## What you supply

`ps7_init.c` + `ps7_init.h` **for your board** — they encode your PS config (DDR part,
clocks, MIO pin-mux). Find them in:
- the `.xsa` hardware export (unzip it), or
- a Vitis standalone BSP (`.../bsp/.../libsrc/standalone_*/src/ps7_init.c`), or
- an FSBL source tree.

## Build — route A: Vitis (recommended, robust)

The platform's standalone crt0 + linker + `ps7_init.c` are all handled for you:

1. Create a platform from your `.xsa` in Vitis.
2. Create a standalone **Empty Application** (or Hello World) on `ps7_cortexa9_0`.
3. Replace its `main()` with `init_stub.c` (the body here).
4. Build → `app.elf`. That's your init-stub ELF.

## Build — route B: this directory (minimal CLI)

```bash
# source the Vitis settings so the arm cross-gcc is on PATH (or set CROSS=)
cp /path/from/your/export/ps7_init.{c,h} .
make                       # -> init_stub.elf
```

`start.S` + `init_stub.ld` here are a deliberately tiny OCM startup (set SP, zero .bss,
call main). Enough for a poke-and-spin `ps7_init`; it does **not** set up caches/MMU/full
vectors — if a board needs more, use route A.

## Use it

```bash
jtag_qspi_flash.sh --arch zynq \
  --fsbl  /abs/path/init_stub.elf \
  --uboot /tools/Xilinx/Vitis/2023.2/data/xicom/cfgmem/uboot/zynq_qspi_x1_single.bin
```

The flow `dow`s the stub, `con`s it, polls DDR up (here it's already up and just
spinning — instant), halts it, then loads U-Boot. See
`references/jtag-flash-bootmode-independent.md`.

## Notes / caveats

- **OCM-linked** (route B `init_stub.ld` → 0x00000000): it must run from OCM because
  `ps7_init` is what brings DDR up.
- It **sits forever** — power-cycle to clear, or just `dow` U-Boot over it (what the flow does).
- `ps7_init.c` is board-specific; the stock one from a different board will mis-configure DDR/MIO.
- Route B startup is minimal by design; route A is the safe default for an unfamiliar board.
