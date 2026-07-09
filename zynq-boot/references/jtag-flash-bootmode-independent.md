# Boot-mode-independent QSPI flash over JTAG (no UART) — reference

**STATUS: HARDWARE-VERIFIED** on a hardwired-QSPI xc7z020 (Stages 1–3 incl. a full
destructive erase → write-back → verify → power-cycle-boot round-trip, 2026-06-30 →
2026-07-01; see History). The proven, packaged form of this flow is the browser
workbench — `references/zynq-jtag-flash-workbench.md`.

For a board you **cannot put into JTAG boot mode** — e.g. boot-mode straps hardwired
to QSPI with resistors — and `program_flash` therefore fails. Generic technique, not
board-specific; the per-board paths (ps7_init, FSBL, U-Boot) are arguments.

Drivers: **`scripts/jtag_erase_reflash.tcl`** (one-shot, fully automated erase /
erase+flash — start here; see its section below) and
`scripts/jtag_qspi_flash.sh` → `scripts/jtag_qspi_flash.tcl` (interactive: brings up
the PS + DCC console and leaves you at the `Zynq>` prompt to drive `sf` by hand).
Provenance for the findings below: research session
`.research/session-20260629-093358/report.md` in the workbench repo
(`OhmsSweetOhms/zynq-jtag-flash-workbench`) — findings A, C, E, F.

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

**Building the custom DCC U-Boot** (folds DCC console + the JEDEC bypass into one image) is
in `references/custom-dcc-uboot-build.md`, **HW-verified 2026-07-06** from
`xlnx_rebase_v2022.01_2022.2` (patch `patches/0002-...-2022.01.patch`; original 2023.01
patch `0001-...` kept). Once built, point `--uboot` at its `u-boot.elf`.

**Stock helper vs custom build — when to use which.** The stock `zynq_qspi_x1_single`
cfgmem helper stays the DEFAULT (proven, zero build). The custom build is the FALLBACK for
**a chip not in `spi_nor_ids[]`**: its generic-SFDP bypass is now proven on HW to probe an
unknown chip at correct geometry with byte-exact reads. Two device-tree gotchas that cost a
bench session and are baked into the build reference: force `&qspi is-dual = <0>` on a
single-chip board (the zc706 default is dual-parallel → doubled geometry + `0x55` garbage),
and pick the read lane width to match the chip's SFDP quality (x1 is universal; x4 needs the
table's params via `SPI_NOR_SKIP_SFDP`).

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
JEDEC issue — build the generic-SFDP bypass U-Boot (`references/custom-dcc-uboot-build.md`,
HW-verified 2026-07-06) and use it here. It probes any SFDP-compliant chip not in
`spi_nor_ids[]`; a chip with no/bad SFDP still needs a real table entry.

## One-shot erase / reprogram — `scripts/jtag_erase_reflash.tcl` (HW-verified 2026-07-09)

The fully automated form of this whole flow: one `xsdb` invocation does bring-up →
(parse `.mcs` → stage) → full-chip erase → program → byte-for-byte verify, with a
timestamped run log and an actionable hint on every failure. Pure TCL — the same
command line on Windows and Linux, no wrapper, no env vars; the only tool assumed is
`xsdb` on PATH.

```
# Linux                                          # Windows
source <Vitis>/settings64.sh                     <Vitis>\settings64.bat
xsdb jtag_erase_reflash.tcl erase       --ps7 ps7_init.tcl
xsdb jtag_erase_reflash.tcl erase+flash boot.mcs --ps7 ps7_init.tcl
```

### Copy-paste quick start (novice-friendly, Linux)

Each line is safe to paste as-is; adjust only the Vitis version and your own file names.
Steps 4 and 5 are **DESTRUCTIVE** — they wipe the entire QSPI flash.

```bash
# 0) ONE-TIME: put the U-Boot helper next to the script (it is not shipped in this repo)
cp /tools/Xilinx/Vitis/2022.2/data/xicom/cfgmem/uboot/zynq_qspi_x1_single.bin ~/.claude/skills/zynq-boot/scripts/

# 1) EVERY NEW TERMINAL: put xsdb on PATH (match your installed Vitis version)
source /tools/Xilinx/Vitis/2022.2/settings64.sh

# 2) get ps7_init.tcl out of YOUR board's .xsa export (an .xsa is just a zip;
#    if this says "caution: filename not matched", run `unzip -l system_wrapper.xsa`
#    to see where ps7_init.tcl lives inside it)
unzip -o system_wrapper.xsa ps7_init.tcl

# 3) read the tool's built-in help (safe, touches nothing)
xsdb ~/.claude/skills/zynq-boot/scripts/jtag_erase_reflash.tcl --help

# 4) DESTRUCTIVE: wipe the whole flash and verify it reads back blank
xsdb ~/.claude/skills/zynq-boot/scripts/jtag_erase_reflash.tcl erase --ps7 ./ps7_init.tcl

# 5) DESTRUCTIVE: wipe, program boot.mcs, and verify byte-for-byte
xsdb ~/.claude/skills/zynq-boot/scripts/jtag_erase_reflash.tcl erase+flash ./boot.mcs --ps7 ./ps7_init.tcl

# 6) power-cycle the board — it boots the new image via its QSPI strap
```

Variations you may need:

```bash
# a Vivado GUI (or another xsdb) already owns the JTAG cable -> join ITS hw_server:
xsdb ~/.claude/skills/zynq-boot/scripts/jtag_erase_reflash.tcl erase --ps7 ./ps7_init.tcl --url tcp:localhost:3121

# shortcut that skips step 1 (the wrapper finds Vitis and sources it for you):
~/.claude/skills/zynq-boot/scripts/jtag_erase_reflash.sh erase+flash ./boot.mcs --ps7 ./ps7_init.tcl

# something failed? the run log holds every command + result (newest last):
ls -t jtag_erase_reflash-*.log | head -1
```

Windows (Command Prompt) equivalents of steps 1/3/5 — same script, same arguments:

```bat
C:\Xilinx\Vitis\2022.2\settings64.bat
xsdb %USERPROFILE%\.claude\skills\zynq-boot\scripts\jtag_erase_reflash.tcl --help
xsdb %USERPROFILE%\.claude\skills\zynq-boot\scripts\jtag_erase_reflash.tcl erase+flash boot.mcs --ps7 ps7_init.tcl
```

Options: `--uboot <elf>` (default: the 2022.2 `zynq_qspi_x1_single.bin` carried next to
the script — a Xilinx-EULA binary, so it is NOT in this public repo; copy it from
`<Vitis-2022.2>/data/xicom/cfgmem/uboot/`), `--url <hw_server>` (default auto-starts a
local one; point it at a Vivado GUI's server if that owns the cable), `--chip-size <n>`,
`--log <path>`. Exit codes: 0 ok / 1 runtime failure / 2 usage.

What it bakes in beyond the interactive script (all HW-proven on the workbench board):

- **`rst -system -stop`** halt-on-reset (AR 68065) — the debugger suspends the cores AS
  PART OF the reset, so the QSPI FSBL can never win the race. (The interactive script's
  older `rst -system; stop` pair loses that race on a board whose flash holds a valid
  image — the FSBL boots, and `ps7_init` then dies with `AP transaction timeout
  @0xE0001034`.)
- **`rst -dap` self-recovery** — a board that sat FSBL-booted can wedge its A9 debug AP
  (DAP status `0x30000021`, A9 gone from `targets`); the script detects it and resets
  the DAP before giving up with a power-cycle hint.
- **`.mcs` decoded in TCL** (Intel-HEX types 00/01/04, per-line checksums) because
  U-Boot `sf write` needs raw bytes in DRAM; a Zynq boot-header guard refuses non-boot
  images before anything is erased.
- **Fail-fast ordering** — parse+validate before connecting, stage+DRAM-spot-check
  before erasing: a bad image or a failed JTAG load can never leave the chip blank.
- **In-process DCC** — the U-Boot console socket (`jtagterminal -socket`) is driven from
  xsdb's own Tcl, with non-blocking reads + `vwait` sleeps so the event loop that pumps
  the DCC bridge never starves. Erase-verify readback stages at `0x02000000` to keep
  clear of the write image staged at `0x00100000`.

Failure → hint catalog (each printed by the script itself on that failure):

| Failure | Hint printed |
|---|---|
| `no targets found … Cortex-A9` | auto-tries `rst -dap`; if still absent → power-cycle the board |
| `AP transaction timeout @0xE0001034` during ps7_init | FSBL seized the debug bus — should not happen after `rst -system -stop`; power-cycle if deep-wedged |
| `Cannot reset APU` / PLL-lock errors | ps7_init/.xsa is for a different board — use the board's own export |
| `OCM is not enabled at 0xFFFC0000` on `dow` | OCM-high remap didn't take — check SLCR unlock + `OCM_CFG` write |
| U-Boot banner ends `Please RESET the board` | dud cfgmem helper (the 2021.1 env_init/-ENODEV bug) — use the pinned 2022.2 build |
| `sf probe` no-detect after 4 tries | wrong QSPI MIO in ps7_init, dud helper, or flaky DCC link (reseat cable) |
| bad Intel-HEX record/checksum at line N | corrupted/truncated image — regenerate the `.mcs` with bootgen |

## Verify on bring-up — RESOLVED on hardware (kept for the next board)

The two author-flagged unknowns were both settled on the real board (2026-06-29/30):

1. **Halt timing vs BootROM: race WON with immediate `rst -system; stop` (NO delay).**
   Confirmed twice independently (halted at PC `0x7a00` inside BootROM, DDR still
   uninitialized). Any inserted delay (`after 200`) defeats the halt — it was removed
   from the script. On another board, if the stale image still boots too fast: erase
   sector 0 first, or `rst -processor` / halt-on-reset.
2. **The cfgmem helper IS interactive over DCC.** Real `Zynq>` prompt via
   `jtagterminal`; `version`, `sf probe`, `sf read` all work. No custom build needed
   for a chip that's in the ID table. Caveat: the helper is OCM-linked at
   `0xFFFC0000` — remap OCM high (SLCR-unlock → `OCM_CFG=0xF`) before `dow`.

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
- 2026-06-29/30: Stage 1 verified on a hardwired-QSPI xc7z020 — AR 76051 halt race won
  with immediate `rst -system; stop`; PS/DDR up over JTAG after two board-specific root
  causes (DDR/IO `PLL_PWRDWN=1` at cold boot → clear before ps7_init; stale A9 MMU →
  `rst -processor`), both baked into `scripts/jtag_qspi_flash.tcl`.
- 2026-06-30: Stage 2 verified — stock cfgmem helper interactive over DCC
  (`jtagterminal`), `sf probe` = s25fl128s 16 MiB, full dump + `.mcs` + partition
  extraction. OCM-high remap required before `dow` (helper linked at `0xFFFC0000`).
- 2026-07-01: Stage 3 verified — destructive round-trip: full-chip `sf erase` (verified
  0xFF) → write-back via `dow -data` + `sf write` → re-dump sha256 == source →
  power-cycle boots. STATUS promoted. Full record: the workbench repo's
  `.threads/zynq-boot/20260629-hardwired-qspi-jtag-flash/`.
- 2026-07-09: `jtag_erase_reflash.tcl` landed (workbench plan-08) — the one-shot
  automated erase / erase+flash, HW-verified end-to-end on the same board: erase (49 s),
  erase+flash of a 4.2 MiB image with byte-identical readback (~1 m 25 s), and a
  post-flash free-run `rst -system` boot check (PLL 0x3F, DDRC normal, app PC in DDR).
  Carries the two 2026-07-09 driver fixes: halt-on-reset `rst -system -stop` (e2bf200)
  and `rst -dap` DAP self-recovery (b210080).
