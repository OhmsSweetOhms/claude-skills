# AD9081 no-OS on ZCU102 — build & flash reference

ADI AD9081 no-OS application on a ZCU102 (Zynq UltraScale+ / ZynqMP). Bare-metal
app with the IIO daemon, booted standalone from QSPI. Paths below were verified
against the live tree; if a path stops resolving, the build was moved or
re-`make`d and the BIF needs re-checking.

- **Project root:** `/media/$USER/Work1/V5/no-OS/projects/ad9081` (`$USER`
  shell-expands to your home username; commands below paste-and-run as-is)
- **Boot working dir:** `<root>/boot/` (holds `boot.bif`, `boot.bin`, `boot.mcs`,
  dated backups, and this guide's ancestor `bootstrap.md`)
- **Toolchain:** Vivado/Vitis **2022.2** at `/tools/Xilinx/`

## Prerequisites

- ADI no-OS repo cloned, HDL build complete (bitstream exists).
- ZCU102 connected via JTAG (USB) and UART.
- For a JTAG debug session: **SW6 all ON** (JTAG boot mode).

## Build

```bash
source /tools/Xilinx/Vitis/2022.2/.settings64-Vitis.sh
cd /media/$USER/Work1/V5/no-OS/projects/ad9081/build
make IIOD=y
```

Produces the ELF, bitstream references, and Vitis workspace artifacts. Keep
`IIOD=y` unless you explicitly don't want the IIO daemon. The app source lives in
`<root>/src/` — e.g. the downconverter gain constant `DISTANCE_4_METERS_GAIN`
(currently `138`) is defined in `src/AD5601.h` and used in `src/app.c`. After
editing source, **re-run `make` so `app.elf` is rebuilt before bootgen**, or the
boot image carries the old code.

## Critical rules (these are load-bearing)

1. **NEVER rebuild the platform project in Vitis.** It regenerates Xilinx default
   startup files (`boot.S`, `vectors.S`, Tcl init scripts) and clobbers ADI's
   patched versions — breaks things in subtle ways.
2. **Only rebuild `app` / `system_app` in Vitis** if you edit source from inside
   the IDE. The platform/BSP must stay as `make` left it.
3. **Debug config MUST use FSBL init, not `psu_init.tcl`.** psu_init boots but DDR
   doesn't come up right; the app loads then spins in the vector table at ~0x200.

## Launch Vitis (for IDE debug)

```bash
vitis -workspace /media/$USER/Work1/V5/no-OS/projects/ad9081/build
```

Open the workspace `make` already created — do **not** create a new one (missing
ADI BSP patches, linker-script mismatches).

### Debug configuration

1. **Run → Debug Configurations**, select/create `SystemDebugger_app_system`.
2. Under **Target Setup**, verify:
   - **"Use FSBL flow for initialization"** — **checked**
   - **"Program FPGA"** — checked
   - **"Reset entire system"** — checked
   - Bitstream path → `_ide/bitstream/system_top.bit`
3. **Debug**.

## Boot components (verified paths)

| File | Path |
|---|---|
| `fsbl.elf` | `<root>/build/system_top/export/system_top/sw/system_top/boot/fsbl.elf` |
| `pmufw.elf` | `<root>/build/system_top/export/system_top/sw/system_top/boot/pmufw.elf` |
| `system_top.bit` | `<root>/build/app/_ide/bitstream/system_top.bit` |
| `app.elf` | `<root>/build/app/Debug/app.elf` |

(`pmufw.elf` also exists at `build/system_top/zynqmp_pmufw/pmufw.elf`; the working
`boot.bif` uses the `export/.../boot/` copy above.)

`make` also writes `build/output_boot_bin/project.bif` — that one is a **JTAG
debug shortcut that omits PMUFW**, not for QSPI boot. Use `boot/boot.bif` below.

## BIF (`<root>/boot/boot.bif`)

Shown with `$USER` standing in for the home username. Unlike the shell commands,
**bootgen does not expand `$USER` inside a `.bif`** — the on-disk `boot.bif` holds
the fully-resolved absolute paths (substitute your real `<root>`).

```bif
//arch = zynqmp; split = false; format = MCS
the_ROM_image:
{
    [bootloader, destination_cpu=a53-0] /media/$USER/Work1/V5/no-OS/projects/ad9081/build/system_top/export/system_top/sw/system_top/boot/fsbl.elf
    [pmufw_image] /media/$USER/Work1/V5/no-OS/projects/ad9081/build/system_top/export/system_top/sw/system_top/boot/pmufw.elf
    [destination_device=pl] /media/$USER/Work1/V5/no-OS/projects/ad9081/build/app/_ide/bitstream/system_top.bit
    [destination_cpu=a53-0] /media/$USER/Work1/V5/no-OS/projects/ad9081/build/app/Debug/app.elf
}
```

The header comment is bootgen's own auto-note; `format = MCS` is why the `.mcs`
extension alone is enough (no `-interface` flag — see below).

## Generate the QSPI image

```bash
source /tools/Xilinx/Vitis/2022.2/.settings64-Vitis.sh
cd /media/$USER/Work1/V5/no-OS/projects/ad9081/boot

# Back up the current known-good images first (use a date stamp you supply)
cp boot.bin boot.bin.<stamp> ; cp boot.mcs boot.mcs.<stamp>

# Raw boot image
bootgen -arch zynqmp -image boot.bif -o boot.bin -w

# QSPI MCS — ZynqMP: the .mcs extension selects MCS. Do NOT add -interface/-size;
# bootgen 2022.2 rejects them under -arch zynqmp (they're Zynq-7000 leftovers).
bootgen -arch zynqmp -image boot.bif -o boot.mcs -w
```

Expected sizes (reference point): `boot.bin` ≈ 13.4 MB, `boot.mcs` ≈ 36.9 MB.
Confirm a source change moved bytes: `cmp -l boot.mcs boot.mcs.<stamp> | wc -l`
should be non-zero (a gain-constant change moved ~44 k bytes — rebuilt
timestamps + the constant).

## Flash to QSPI

From `xsct` (after sourcing the toolchain):

```tcl
connect
targets -set -filter {name =~ "Cortex-A53 #0"}
rst -system
exec program_flash -f boot.mcs -flash_type qspi-x8-dual_parallel \
    -fsbl /media/$USER/Work1/V5/no-OS/projects/ad9081/build/system_top/export/system_top/sw/system_top/boot/fsbl.elf
```

Or Vitis GUI → **Xilinx → Program Flash**.

## Boot mode switches (SW6)

This project boots from **QSPI**:

| Mode | SW6 (1 → 4) |
|---|---|
| **QSPI32 boot** (standalone) | **ON OFF ON ON** |
| **JTAG** (for Vitis/XSCT debug) | **all ON** |

After setting, **power-cycle** (not reset). Don't confuse these with the AD9986 +
Kuiper **SD-boot** board (`ad9986-zcu102-kuiper-sdboot.md`), which uses a different
SW6 pattern (`1-ON, 2-4-OFF` on Rev1.0) and a different boot device entirely.

## UART console

ZCU102 USB-UART typically enumerates as `/dev/ttyUSB1` at **115200 8N1**. If the
app reaches `main()` you'll see IIO daemon output there.

## Symptoms of doing it wrong

| What you did | What happens |
|---|---|
| Used psu_init.tcl instead of FSBL | `EDITR not ready`, or app loads then spins at 0x200 |
| Rebuilt the platform in Vitis | `can't read "map": no such variable` Tcl error, or startup files silently replaced |
| Created a fresh Vitis workspace | Missing ADI BSP patches, linker-script mismatches |
| Forgot `source .settings64-Vitis.sh` | `make`/`bootgen` can't find the toolchain |
| Added `-interface spi -size 128` on `-arch zynqmp` | bootgen: `'-interface' option supported only for FPGA architecture '-arch fpga'` |

## If something goes wrong

- **Don't panic-rebuild the platform** — it makes it worse.
- If the workspace is corrupted, delete it and re-run `make IIOD=y` from scratch.
- A53 stuck in assembly at low addresses (0x0–0x200) ⇒ startup/init problem, not
  your app. Check the debug-config FSBL setting first.

## History

The original `boot/` images were built 2026-04-10. On 2026-05-18 the images were
regenerated for a `DISTANCE_4_METERS_GAIN` change (Apr-10 copies saved as
`boot.bin.apr10` / `boot.mcs.apr10`), and the legacy `bootstrap.md` had its broken
`bootgen ... -interface spi -size 128` MCS line corrected — that fix is reflected
in the bootgen section above.
