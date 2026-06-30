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
cd /media/$USER/Work1/V5/no-OS/projects/ad9081   # project ROOT — Makefile lives here, NOT build/
make IIOD=y
```

**The Makefile is at the project root, not `build/`.** `build/` is *generated* by
this make. Running `make` from inside `build/` fails with `make: *** No targets
specified and no makefile found.  Stop.` (a prior version of this guide had the
`cd .../build` wrong.)

Produces the ELF, bitstream references, and Vitis workspace artifacts. Keep
`IIOD=y` unless you explicitly don't want the IIO daemon. The app source lives in
`<root>/src/` — e.g. the downconverter gain constant `DISTANCE_4_METERS_GAIN`
(currently `138`) and `DISTANCE_4_METERS_ATTEN` are defined in `src/AD5601.h` and
used in `src/app.c`. After editing source, **rebuild before bootgen** — but read
the "Which ELF / how the build sees your edits" gotchas below first, because two
different ELFs exist and the *boot* flow uses the Vitis-IDE one.

### Which ELF, and how the build sees your edits (verified 2026-06-22)

These three things cost a whole session of false leads — they are the load-bearing
gotchas for this tree:

1. **Two ELFs, and the QSPI boot uses the IDE one.** The CLI `make IIOD=y` builds
   `build/ad9081.elf`. The Vitis **IDE** app project builds `build/app/Debug/app.elf`
   — and **that** is what `boot/boot.bif` points at, so that's what ends up on QSPI.
   They are compiled by different flows and are NOT byte-identical (different sizes).
   To get a source change onto the board, rebuild **app_system in the Vitis IDE**
   (or repoint the BIF), not just `make`.
2. **Sources are SYMLINKED into the build mirror — do not trust the mtime.** The no-OS
   build (LINK_SRCS=y) symlinks `src/*.c/*.h` into `build/app/ad9081/src/`. The
   symlink's mtime is when the *link* was created (project setup, e.g. Apr 9), **not**
   its content — `ls`/`stat` show the old link date while the file content is your
   live `src/`. Do **not** conclude "the build is compiling a stale copy" from the
   mtime; `readlink`/`cat` it to see it points at live `src/`. (The BUILD_LOCK in
   `generic.mk` does skip the `update` copy step on incremental builds, but because
   sources are *symlinked*, content is still live — the lock only matters if a build
   used `LINK_SRCS=n` to make real copies.)
3. **A one-constant change is a TINY diff — small ≠ no-change.** Changing a single
   `#define` (e.g. `DISTANCE_4_METERS_ATTEN` 60→90) moves only ~2 bytes in the ELF
   (the initialized byte in `.data` plus the immediate in `.text`); the resulting
   `boot.mcs` differed by only **51 bytes** vs the prior image (the rest is build
   timestamps). **Do not read a small `cmp -l | wc -l` as "nothing changed."** To
   verify a value *actually* compiled in, read the byte out of the ELF:

   ```bash
   # value of a static like `static char up_conv_atten = DISTANCE_4_METERS_ATTEN;`
   ADDR=$(aarch64-none-elf-nm build/app/Debug/app.elf | awk '/ up_conv_atten$/{print $1}')
   aarch64-none-elf-objdump -s -j .data build/app/Debug/app.elf | grep " ${ADDR:9:5} "
   # decode the byte at ADDR:  0x3c = 60,  0x5a = 90
   ```

   This is ground truth; the mcs byte-count is not.

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
should be non-zero. **But do not judge by magnitude** — a single-`#define` change
(e.g. `DISTANCE_4_METERS_ATTEN` 60→90) moves only ~**51 bytes** in the `.mcs` (a
couple bytes of changed constant + build timestamps), and that is a *real* change,
not noise. A bigger move (tens of kB) just means more code/rebuild churn came along
for the ride. To confirm a specific value truly compiled in, read the ELF byte (see
"Which ELF / how the build sees your edits" above) — the mcs byte-count cannot tell
a real one-constant change from pure timestamp drift.

## Flash to QSPI

From `xsct` (after sourcing the toolchain). **Build the `.tcl` in bash with paths
already resolved** — xsct does NOT expand `$USER`, so a `$USER` left in the script
reaches `program_flash` verbatim and the file isn't found. Wrap `program_flash` in
`puts [exec ...]`, not a bare `exec`: a bare `exec` runs the flash but captures its
stdout into the return value and discards it, so you see the `hw_server` banner and
then nothing (and `xsct` still exits 0). `puts` prints the log so you can confirm
`Flash Operation Successful`.

```bash
ROOT=/media/$USER/Work1/V5/no-OS/projects/ad9081
FSBL=$ROOT/build/system_top/export/system_top/sw/system_top/boot/fsbl.elf
cat > /tmp/flash_qspi.tcl <<EOF
connect
targets -set -filter {name =~ "Cortex-A53 #0"}
rst -system
puts [exec program_flash -f $ROOT/boot/boot.mcs -flash_type qspi-x8-dual_parallel -fsbl $FSBL]
EOF
xsct /tmp/flash_qspi.tcl
```

A good run prints (mt25qu512a detected via the dual-parallel mini-U-Boot, ~13.5 MB):

```
Performing Erase Operation...
SF: 13500416 bytes @ 0x0 Erased: OK          (Elapsed time ≈ 11 sec)
Performing Program Operation...
0%...50%...100%   ... per-sector "Written: OK" ...
Program Operation successful.                (Elapsed time ≈ 90 sec)
Flash Operation Successful
```

Re-flashing the same `boot.mcs` is idempotent/harmless — fine to re-run if the
first run's log got swallowed. Or use the Vitis GUI → **Xilinx → Program Flash**.

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
| Bare `exec program_flash` (no `puts`) | Flash actually runs but the log is swallowed; you see only the `hw_server` banner and `xsct` exits 0 — no `Flash Operation Successful`. Wrap in `puts [exec ...]` |
| Left `$USER` in the xsct `.tcl` | xsct doesn't expand it; `program_flash` gets a literal `$USER` path and fails to find fsbl/mcs. Resolve paths in bash first |
| `make` from inside `build/` | `make: *** No targets specified and no makefile found.` — Makefile is at the project **root**; `build/` is generated. `cd` to root |
| Concluded "build is using a stale source copy" from the mtime | The mirror in `build/app/ad9081/src/` is a **symlink** to live `src/`; its mtime is link-creation time, not content. `readlink` it — content is live |
| Dismissed a small `boot.mcs` diff (tens of bytes) as "no change" | A single-`#define` change is genuinely ~51 mcs bytes. Verify the actual value by reading the ELF `.data` byte, not by diff size |
| Edited `src/` then only ran CLI `make`, but flashed image is unchanged code | The QSPI flow uses the **Vitis-IDE** `app/Debug/app.elf`, not CLI `ad9081.elf`. Rebuild app_system in the IDE (or repoint the BIF) |

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

On 2026-06-10 the May-18 `boot.mcs` was re-flashed to QSPI to confirm the flow
end-to-end (mt25qu512a, erase 11 s + program 90 s, `Flash Operation Successful`).
Two flashing gotchas hit that run and are now fixed in the flash section above:
bare `exec program_flash` swallows the verification log (use `puts [exec ...]`),
and xsct does not expand `$USER` (resolve paths in bash before building the
`.tcl`).

On 2026-06-22 `DISTANCE_4_METERS_ATTEN` was changed 60→90 in `src/AD5601.h`,
app_system rebuilt in the Vitis IDE, and `boot.mcs` regenerated and flashed
(`Flash Operation Successful`, program 89 s). The board's RF attenuation visibly
changed, confirming 60→90 reached the hardware. The compiled value was verified
at the byte level: `up_conv_atten` @ `0x86a21` in `.data` = `0x5a` = 90. This run
exposed four diagnostic traps now captured above ("Which ELF / how the build sees
your edits" + the symptoms table): the Makefile is at the project root not `build/`;
the build-mirror sources are *symlinks* (so the link mtime is misleading and edits
to `src/` are live); a one-`#define` change is a genuine but tiny (~51-byte) mcs
diff that must not be mistaken for "no change"; and the QSPI flow flashes the
Vitis-IDE `app/Debug/app.elf`, not the CLI `ad9081.elf`.
