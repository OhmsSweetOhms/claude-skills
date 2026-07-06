---
name: zynq-boot
description: >-
  Build, package, and flash boot images for Xilinx Zynq / ZynqMP (UltraScale+)
  boards — assembling FSBL + PMUFW + bitstream + application into boot.bin /
  boot.mcs with bootgen and writing them to QSPI flash with program_flash. Use
  this skill whenever the user wants to build or rebuild a Zynq/ZynqMP boot
  image, regenerate a boot.mcs or boot.bin, flash a board over JTAG/QSPI, work
  with bootgen BIF files, run program_flash, set up an FSBL debug session, or
  pick up bring-up work on a specific board (e.g. ZCU102, AD9081 no-OS). Trigger
  it even when the user names only the artifact or tool ("regen the mcs", "flash
  the zcu102", "rebuild the boot image", "the bootgen line is broken") rather
  than saying "skill". Also covers SD-card boot of ADI Kuiper Linux on ZCU102
  (AD9986 / AD9081 FMC), BOOT.BIN deployment, and the ZCU102 SW6 boot-mode switch
  settings (QSPI vs SD vs JTAG), including the Rev1.0 UG1182 polarity gotcha.
  Project-specific paths, BIFs, switch tables, and quirks live in
  references/<project>.md — consult the matching one. This is a personal
  reference that grows: new boards get added as references/ files.
---

# Zynq / ZynqMP Build & Flash

A workflow + reference for taking a Zynq board from compiled artifacts to a
booting QSPI image. The general procedure (bootgen BIF anatomy, the MCS gotcha,
`program_flash`, boot-mode switches) lives here; the **exact paths, build
commands, and per-board quirks live in `references/<project>.md`**. Always open
the matching project reference before running anything — the generic steps below
are a skeleton, and a board will not boot from guessed paths.

## Pick the project reference first

| Project | Board | Boot device | Reference |
|---|---|---|---|
| AD9081 no-OS (bare-metal) | ZCU102 (ZynqMP) | QSPI | `references/ad9081-zcu102-noos.md` |
| AD9986 + ADI Kuiper Linux | ZCU102 Rev1.0 + AD9986-FMCB | SD card | `references/ad9986-zcu102-kuiper-sdboot.md` |
| JTAG flash workbench (browser dashboard) | custom Zynq-7000 xc7z020, **hardwired QSPI** | QSPI (flashed over JTAG/DCC, no UART) | `references/zynq-jtag-flash-workbench.md` |

If the user's board/project isn't in this table, say so and ask whether to (a)
proceed generically using the skeleton below, or (b) capture a new reference as
you go (see "Adding a new project" at the bottom). Don't invent paths for a
board you have no reference for — that's exactly the searching this skill exists
to avoid.

## The flow

```
compile artifacts  →  bootgen (BIF → boot.bin / boot.mcs)  →  program_flash (→ QSPI)  →  set boot-mode switches  →  power cycle
   make / Vitis          assembles FSBL+PMUFW+bit+app           writes flash over JTAG        DIP switches → QSPI         boots standalone
```

For interactive debug (no flashing) you skip bootgen entirely and run the app
from Vitis/XSCT over JTAG — but **the init method matters** (FSBL flow vs
`psu_init.tcl`); see the project reference, because getting it wrong looks like
an application bug when it's really a DDR/startup problem.

## 1. Source the toolchain

Every shell that runs `make`, `bootgen`, `vitis`, `xsct`, or `program_flash`
must first source the Vitis settings, or the tools aren't on `PATH` and you get
cryptic "command not found" / toolchain failures:

```bash
source /tools/Xilinx/Vitis/<version>/.settings64-Vitis.sh
```

The version is pinned per project (mismatched bootgen/program_flash versions
produce subtly broken images) — the reference names the exact one.

## 2. Build the artifacts

A boot image is assembled from four already-compiled pieces. Build them with the
project's build system (`make`, Vitis, etc.) — the reference gives the exact
command and the **critical "don't rebuild X" rules**, which are load-bearing on
ADI/no-OS trees where a stray Vitis platform rebuild silently clobbers
hand-patched startup files.

| Component | Role | Where it comes from |
|---|---|---|
| `fsbl.elf` | First-stage boot loader (brings up DDR, loads the rest) | build export / boot dir |
| `pmufw.elf` | Platform Management Unit firmware (ZynqMP only) | build export / pmufw dir |
| `system_top.bit` | PL bitstream | build `_ide/bitstream/` |
| `app.elf` | Your application | build `app/Debug/` |

Before regenerating an image, **check the artifact mtimes against your last
source edit** — if `app.elf` predates your `app.c` change, the change isn't in
the ELF yet and you must rebuild before bootgen, or you'll flash stale code. This
is the single most common way to "flash a fix" and see no change.

## 3. The BIF

bootgen reads a `.bif` that lists the components and how each is loaded. The
canonical ZynqMP layout:

```bif
the_ROM_image:
{
    [bootloader, destination_cpu=a53-0] fsbl.elf
    [pmufw_image]                       pmufw.elf
    [destination_device=pl]             system_top.bit
    [destination_cpu=a53-0]             app.elf
}
```

Notes that bite people:
- The `[bootloader]` attribute marks the FSBL — there is exactly one.
- `[pmufw_image]` is **ZynqMP-only**; on Zynq-7000 there is no PMUFW and the line
  doesn't exist.
- A BIF that some build systems auto-generate (e.g. `output_boot_bin/project.bif`)
  may be a **JTAG-debug shortcut that omits PMUFW** — fine for `xsct` download,
  not suitable for standalone QSPI boot. Use the project's standalone BIF.
- Paths in the BIF can be absolute or relative-to-CWD; if absolute, they must all
  still exist (a moved build tree breaks bootgen silently-ish). The reference's
  BIF records the known-good paths.

## 4. Generate boot.bin / boot.mcs with bootgen

```bash
# Raw boot image (SD / JTAG / debug)
bootgen -arch zynqmp -image boot.bif -o boot.bin -w

# QSPI image — for ZynqMP the .mcs EXTENSION ALONE selects MCS output
bootgen -arch zynqmp -image boot.bif -o boot.mcs -w
```

**ZynqMP MCS gotcha (this is the thing people get wrong):** older Zynq-7000
workflows append `-interface spi -size 128` to make an MCS. With `-arch zynqmp`
bootgen **rejects** those flags:

```
'-interface' option supported only for FPGA architecture '-arch fpga'
```

For ZynqMP the `.mcs` extension is sufficient. If a doc, script, or your memory
still has the `-interface/-size` flags on a `zynqmp` command, that's a stale
Zynq-7000 leftover — drop them.

Use `-arch zynq` (not `zynqmp`) for Zynq-7000 / 7-series parts.

**Before flashing, sanity-check that the new image actually differs** from the
prior one (`cmp -l old.mcs new.mcs | wc -l`). A source change should move bytes
inside an otherwise-identical layout; zero differing bytes means you rebuilt
nothing. Back up the previous known-good image first (e.g. `boot.mcs.<date>`)
so you can roll back if the new one doesn't boot.

## 5. Flash to QSPI with program_flash

From `xsct` (source the toolchain first, then run `xsct`):

```tcl
connect
targets -set -filter {name =~ "Cortex-A53 #0"}
rst -system
puts [exec program_flash -f boot.mcs -flash_type qspi-x8-dual_parallel \
    -fsbl /abs/path/to/fsbl.elf]
```

- **Wrap `program_flash` in `puts [exec ...]`, not a bare `exec`.** Tcl's `exec`
  captures the child's stdout into its *return value*; a bare `exec program_flash`
  therefore runs the flash but **swallows the entire progress/verification log** —
  you see the `hw_server` banner and then nothing, and `xsct` exits 0. That exit 0
  is real (a bare `exec` *does* raise a Tcl error and exit non-zero if
  `program_flash` returns non-zero, so a hard failure won't pass silently), but you
  get **no positive confirmation** — no erase/program percentages, no
  `Flash Operation Successful`. `puts [exec ...]` prints the captured log so you can
  actually see `Program Operation successful` / `Flash Operation Successful` and the
  per-sector `Written: OK` lines. Don't report a flash as done off exit 0 alone;
  confirm the success string.
- `-flash_type` is board-specific (`qspi-x8-dual_parallel` is the ZCU102 dual
  QSPI); the reference names it.
- `program_flash` needs the **FSBL** to drive the flash, separate from the FSBL
  baked into the image — pass an absolute path.
- **`$USER` / shell vars do NOT expand inside the xsct/Tcl script** — only the
  invoking bash shell expands them. Resolve every path to a literal absolute path
  before handing it to xsct (e.g. build the `.tcl` in bash with the var expanded,
  or paste the fully-resolved path). A `$USER` left in a Tcl `-fsbl` arg reaches
  `program_flash` verbatim and the file won't be found.
- The Vitis GUI equivalent is **Xilinx → Program Flash**.

## 6. Set boot mode and power-cycle

Booting from a given device only happens if the board's boot-mode straps select
it. On the ZCU102 that's the **SW6** DIP block; JTAG, QSPI, and SD boot are each
a different switch pattern. **These are board-rev-specific and the boot device
matters** — the per-project reference records the exact pattern, and you should
trust it over generic tables:

- **AD9081 no-OS → QSPI:** SW6 = `ON OFF ON ON` (QSPI32); JTAG debug = SW6 all ON.
- **AD9986 + Kuiper → SD:** SW6 = `1-ON, 2-4-OFF` on ZCU102 **Rev1.0** — and note
  **Xilinx UG1182 Table 13-1 is wrong for that rev** (it prescribes the opposite
  polarity, which silently boots a stale onboard FSBL). See the AD9986 reference.

Don't carry a switch setting from one board/boot-device to another. After setting
SW6, power-cycle (not just reset) so the straps are re-latched.

## When you can't select JTAG boot mode (hardwired QSPI/NAND)

Everything above (§5–6) assumes you can strap the board to JTAG to flash, then to
QSPI to boot. **Some custom boards hardwire the boot mode to QSPI with resistors —
there is no JTAG strap to select**, and `program_flash` then fails (a documented
2020.x+ regression, **AMD AR 76051**: the BootROM boots stale QSPI and seizes the PS
before the tool can load its FSBL).

You don't need JTAG *boot* mode — only JTAG *access*, which is always alive
regardless of the straps. `ps7_init`/`psu_init` reconfigures the whole PS (clocks,
DDR, QSPI MIO) by writing registers directly, independent of the boot mode. And
`program_flash` already talks to its helper over the **ARM DCC (JTAG), not a UART** —
so the whole flash can run with no serial port.

Use **`scripts/jtag_qspi_flash.sh`**: it halts the BootROM (`rst -system; stop`),
re-inits the PS, loads a **DCC-console U-Boot** (the cfgmem helper, or a custom
`CONFIG_ARM_DCC` build), and opens `jtagterminal` so you can run `sf probe/erase/write`
over JTAG. Full rationale, the UART-vs-DCC table, and the bring-up verification steps
live in **`references/jtag-flash-bootmode-independent.md`**. The flow is HW-proven and
packaged as a **browser-dashboard workbench** (bring-up, DCC console, dump/erase/
write-back, boot-image map) — for the board it was built on, prefer that tool:
`references/zynq-jtag-flash-workbench.md`. (Quicker alternative if
`program_flash` is otherwise fine: just prepend `rst -system; stop` before the
`program_flash` call to apply the AR 76051 workaround without going fully manual.)

## Common failure → cause

| Symptom | Likely cause |
|---|---|
| `'-interface' option supported only for FPGA architecture` | `-interface/-size` flags on a `-arch zynqmp` bootgen call — drop them |
| Flashed a fix, board behaves identically | `app.elf` older than your source edit (didn't rebuild), or flashed the backup |
| bootgen "cannot find" a component | BIF has a stale absolute path; build tree moved or component not built yet |
| App loads then spins at a low address (≈0x0–0x200) | Wrong init (psu_init vs FSBL flow) — DDR not up; a startup problem, not your code |
| `make`/`bootgen`/`xsct`: command not found | Forgot to `source .settings64-Vitis.sh` in this shell |
| `program_flash` ran but no log printed, `xsct` exited 0 | Bare `exec program_flash` swallowed stdout — wrap in `puts [exec ...]` to see the `Flash Operation Successful` confirmation |
| `program_flash` can't find fsbl/mcs path that "looks right" | `$USER`/shell var left unexpanded in the Tcl script — xsct doesn't expand it; resolve to a literal absolute path before calling xsct |
| `program_flash` fails on a board hardwired to QSPI/NAND boot mode | AMD AR 76051 regression — BootROM boots stale flash and seizes the PS. Prepend `rst -system; stop`, or use `scripts/jtag_qspi_flash.sh` (boot-mode-independent JTAG flash, no UART) |

## Adding a new project

When the user works a board that has no reference yet, capture it so the next
session doesn't have to rediscover it:

1. Create `references/<project>-<board>.md`.
2. Record, with **verified** values (check the files exist — don't transcribe
   from memory): toolchain version + settings path; build command and any
   "never rebuild X" rules; the four component paths; the standalone BIF
   (verbatim); the exact bootgen, `program_flash`, and boot-mode-switch commands;
   and any board-specific quirks or symptoms-of-doing-it-wrong table.
3. Add a row to the "Pick the project reference first" table above.

Keep each reference self-contained — someone reading only that file should be
able to go from source edit to booting board without guessing.
