# Boot-mode-independent QSPI flash over JTAG (no UART) — reference

**STATUS: authored from primary-source forensics + AMD docs, NOT yet hardware-verified.**
Promote to "verified" with a History entry after one real run (see "Verify on bring-up").

For a board you **cannot put into JTAG boot mode** — e.g. boot-mode straps hardwired
to QSPI with resistors — and `program_flash` therefore fails. Generic technique, not
board-specific; the per-board paths (ps7_init, FSBL, U-Boot) are arguments.

Driver: `scripts/jtag_qspi_flash.sh` → `scripts/jtag_qspi_flash.tcl`.
Provenance for the findings below: research session
`work/uboot/.research/session-20260629-093358/report.md` (findings A, C, E, F).

## Why program_flash fails on a hardwired-QSPI board

**AMD Answer Record 76051** — *"2020.x Vivado Hardware Manager and Vitis: Zynq-7000
flash programming fails when booting in QSPI and NAND boot mode."* `program_flash`
worked from a QSPI strap in 2019.x and regressed in 2020.x+.

Root cause: after the tool's reset, a QSPI strap lets the **BootROM** re-run QSPI boot;
if a valid (old) image is present it boots and **seizes the PS**, colliding with the
tool downloading its own FSBL. 2019.x halted the part first; 2020.x+ does not.

This happens **before** any U-Boot helper is loaded — so it is NOT fixable by patching
the helper. (The originally-tempting "make the helper ignore the boot-mode pins" is a
no-op: U-Boot's `sf` path never reads the strap; the strap is only read for the
`modeboot`/`boot_targets` env and for SPL.)

## Why it is always solvable over JTAG

1. **JTAG/DAP is electrically alive regardless of the boot-mode straps** (absent an
   eFUSE JTAG-disable). "Can't *boot* JTAG" ≠ "can't *access* over JTAG."
2. **`ps7_init` (Zynq-7000) / `psu_init` (ZynqMP) reconfigures the entire PS — clocks,
   DDR, MIO including the QSPI pins — by writing registers directly. It never reads the
   boot mode.** So after `rst -system; stop; ps7_init`, the part is in a known
   QSPI-capable state no matter what the BootROM did.

So the recipe is: halt the core before the BootROM boots stale flash → re-init the PS →
load a U-Boot → drive `sf`.

## No UART required: program_flash uses the ARM DCC over JTAG

The cfgmem helper's **only** compiled serial driver is `drivers/serial/arm_dcc.c`
(`arm_dcc_getc/putc/pending`); the only "uart" symbols are 4-byte no-op stubs. Its
console is the **ARM DCC (Debug Communications Channel)** — a two-word debug mailbox in
the CoreSight logic reachable by both the CPU and the JTAG debugger via the DAP. U-Boot
reads/writes it (Zynq-7000/A9: CP14 `DBGDTRTX`/`DBGDTRRX` + `DBGDSCR`; ZynqMP/A53:
`DBGDTR_EL0`); the debugger reaches the same registers over JTAG. That is how
`program_flash` scripts `sf …` and reads back `Erased: OK` / `Flash Operation
Successful` with **no UART connected**.

Host-side bridge: xsct's **`jtagterminal`** attaches a terminal to the target's DCC.

**To stay UART-free, the U-Boot you load must have a DCC console:**

| U-Boot you load | Console | UART needed |
|---|---|---|
| cfgmem helper (`…/cfgmem/uboot/zynq_qspi_x1_single.bin`) or your patched rebuild | DCC (`arm_dcc`) | **No** |
| stock `xilinx_zynq_virt` u-boot.elf | UART (`ttyPS`) | Yes |
| custom build, `CONFIG_ARM_DCC=y` + `stdin/stdout/stderr=dcc` | DCC | **No** |

The cfgmem helper is an ELF despite the `.bin` name (entry `0xFFFC0000`, runs from OCM,
no DDR needed), so `dow` loads it directly.

**Building the custom DCC U-Boot** (folds DCC console + the JEDEC bypass + `loadx` into
one image) is in `references/custom-dcc-uboot-build.md` with the defconfig delta and the
patch `patches/0001-spi_nor-generic-any-jedec-fallback.patch`. Once built, point
`--uboot` at its `u-boot.elf`.

## Usage

```bash
# Zynq-7000, UART-free (load the cfgmem helper, drive sf over DCC):
scripts/jtag_qspi_flash.sh \
  --arch  zynq \
  --psinit /abs/path/to/ps7_init.tcl \
  --uboot  /tools/Xilinx/Vitis/2023.2/data/xicom/cfgmem/uboot/zynq_qspi_x1_single.bin

# ZynqMP variant (psu_init + pmufw + BOOT_MODE_USER override are handled by the script):
scripts/jtag_qspi_flash.sh --arch zynqmp --psinit /abs/psu_init.tcl \
  --uboot /abs/dcc_uboot.elf --pmufw /abs/pmufw.elf
```

**PS init — one of `--psinit` or `--fsbl`:** `--psinit ps7_init.tcl` (preferred — direct
register writes, no side effects), OR `--fsbl fsbl.elf`, which does `ps7_init` by
*executing*; the tcl runs it only until DDR comes up, then halts before it boots from
QSPI (version-independent — polls DDR via the DAP, no reliance on FSBL symbols). The
`.xsa` path supplies `ps7_init.tcl` automatically. Use `--fsbl` when you have the FSBL
(e.g. from bootgen) but no separate tcl. **Cleanest FSBL variant:** a stub that inits the
PS and then `while(1)` — it never boots from QSPI, so there is no race at all. Build one
from `scripts/init-stub/` (a ~10-line `ps7_init(); ps7_post_config(); while(1);` ELF) and
pass it as `--fsbl`.

The script sources the toolchain, resolves every path to absolute (xsct does **not**
expand `$USER`), halts the BootROM, re-inits the PS, loads U-Boot, then opens
`jtagterminal`. In that DCC terminal:

```
sf probe 0 0 0
sf erase 0 <len_hex>
sf write 0x01000000 0 <len_hex>     # payload staged to DRAM by the script (see below)
```

**Getting the image into DRAM — there is no `loadx`.** The cfgmem helper has no
`loadx`/`ymodem` command compiled in, so you cannot transfer over the DCC console.
Stage it from the debugger over JTAG instead — exactly what program_flash does — by
passing `--image`/`ZB_IMAGE` to the script, which runs `dow -data <image> 0x01000000`
(DDR is up because `ps7_init` ran). Then `sf write 0x01000000 0 <len>`. (A custom
U-Boot built with `CONFIG_CMD_LOADX` **and** `CONFIG_ARM_DCC` is the only way to use
in-console transfer.)

If `sf probe` rejects the chip with `unrecognized JEDEC id bytes`, that's the separate
JEDEC issue — see research session finding A (patch `spi_nor_read_id` to fall back to a
generic SFDP entry) and use a patched U-Boot here.

## Verify on bring-up (then promote STATUS + add History)

Two spots are unverified and may need a board-specific tweak:

1. **Halt timing vs BootROM.** `rst -system; after 200; stop` should catch the core
   before stale QSPI boots. If the old image boots too fast, try `rst -processor`, a
   halt-on-reset, or erase the first flash sector first. Capture what worked.
2. **Does the cfgmem helper give an interactive DCC prompt?** It is standard U-Boot, so
   a boot-delay prompt is expected — but if `jtagterminal` shows no prompt (it may only
   speak program_flash's scripted framing), load a custom `CONFIG_ARM_DCC=y` U-Boot
   with `stdin/stdout/stderr=dcc` instead.

## Symptom → cause

| Symptom | Cause / fix |
|---|---|
| `program_flash` hangs/fails on a board strapped to QSPI/NAND | AR 76051 regression — use this JTAG flow (or prepend `rst -system; stop` before `program_flash`) |
| Old firmware boots and runs instead of halting | BootROM booted stale flash before `stop` — halt earlier (`rst -processor` / halt-on-reset) or erase sector 0 first |
| `jtagterminal` opens but no U-Boot prompt | Loaded U-Boot has no DCC console (stock = ttyPS) — load the cfgmem helper or a `CONFIG_ARM_DCC` build |
| `loadx`/`loady` "unknown command" in the DCC console | The cfgmem helper has no in-console transfer — stage the payload with `dow -data <img> 0x01000000` (script `--image`), or build U-Boot with `CONFIG_CMD_LOADX` |
| `sf probe`: `unrecognized JEDEC id bytes` | Flash not in `spi_nor_ids[]` — patch helper (research finding A), separate from this boot-mode flow |
| `dow`/`sf` runs but writes nothing usable | PS not initialized — confirm `ps7_init`/`psu_init` actually ran (it sets QSPI MIO/clocks) |
| xsct: `$USER` path not found | Resolve paths to absolute in bash; xsct never expands shell vars (the wrapper does this) |

## History

- Authored 2026-06-29 from research session 20260629-093358 (binary forensics of the
  2023.2 cfgmem helper + AR 76051 + u-boot-xlnx `xlnx_rebase_v2023.01_2023.2` source).
  Not yet run on hardware.
