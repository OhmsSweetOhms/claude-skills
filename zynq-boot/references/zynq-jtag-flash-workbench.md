# Hardwired-QSPI Zynq-7000 + the JTAG flash workbench — project reference

**Board:** custom Zynq-7000 (xc7z020), boot-mode straps **hardwired to QSPI** (resistors
— JTAG boot can never be selected), Digilent **JTAG-HS3** cable, Spansion **s25fl128s**
16 MiB QSPI. `program_flash` fails on it (AMD AR 76051 class); the boot-mode-independent
flow in `references/jtag-flash-bootmode-independent.md` is the only flash path.

**The tool for this board is the workbench repo, not the bare scripts.**
`zynq-jtag-flash-workbench` (GitHub: `OhmsSweetOhms/zynq-jtag-flash-workbench`) is a
browser dashboard (FastAPI + xterm.js) over xsct/hw_server that packages the whole flow
and is **hardware-proven end-to-end** — including a full destructive erase → write-back →
sha256-verify → power-cycle-boot round-trip (2026-07-01). Prefer it over driving
`jtag_qspi_flash.sh` by hand on this board; it carries fixes the raw scripts predate.

## What the workbench does (all HW-verified on this board)

- **PS/DDR bring-up over JTAG** (`/api/bringup`): `rst -system; stop` → clear DDR/IO
  `PLL_PWRDWN` → `ps7_init` → `rst -processor` → DRAM write/read verify.
- **Interactive DCC U-Boot console** (`/api/load-uboot` + WebSocket bridge): loads the
  Vitis cfgmem helper (or any ELF via the `uboot_elf` override / helper-picker dropdown),
  OCM-high remap for OCM-linked helpers, `jtagterminal` DCC↔TCP↔WS.
- **Flash ops**: `sf probe` + boot-image partition map (grouped by shared image-header =
  one multi-segment ELF), full 16 MiB dump → `.mcs` + per-partition extraction,
  content-addressed backups, confirm-gated **erase** and **Write QSPI** driven over DCC.
  **Write QSPI** flashes the boot image loaded in "Boot image to flash" and is disabled
  until that image parses to a valid Zynq header with a **good header checksum**; the driver
  (`write_flash`) independently refuses a blank/garbled image, so an erased-then-dumped
  (all-`0xFF`) backup can never be written over good firmware. (This replaced a "Write back
  dump" button that flashed the *last dump* — a footgun that wrote a blank image after an
  erase→dump cycle. 2026-07-09.)
- **Boot-image parser** `dashboard/backend/bootimg.py` — offset conventions verified
  against this board and pinned by unit tests (`test_bootimg.py`).
- **Validation harness** `validate_dashboard.py` (mock + `--live` non-destructive).

Run it: `dashboard/run.sh` in the repo; local `config.json` (gitignored) points at the
board's `.xsa` and the cfgmem helper. See the repo's README/dashboard docs.

## Board facts (verified values)

| Fact | Value |
|---|---|
| Device / cable | xc7z020 on Digilent JTAG-HS3 (FTDI 0403:6014) |
| QSPI flash | **fleet varies** — **s25fl128s** (RDID `01 20 18 4D 01 80`) on the original board; **w25q128jv** (Winbond) on a later board (verified 2026-07-14). Both 16 MiB, both auto-detected by the cfgmem helper's `sf probe`; not a wedge cause |
| Shipped boot image | **OCM-resident** (runs at PC ≈ `0x1ac54`), **never inits DDR** — "attach to warm DDR" is impossible on this board |
| Partitions | `fsbl.elf`, bitstream `.bit`, `ldr_b_app.elf` as 2 segments (DDR `0x18600` + OCM `0xFFFF0000`, shared image header) |
| PS-init input | board `.xsa` (V4 = known-good; V3 also passes). **V1 (2023-era) is a DUD — wedges the DAP**, needs a physical power-cycle; keep it blocklisted |
| cfgmem helper | **2022.2 works** (U-Boot 2022.01, interactive DCC, `sf probe` OK). **2021.1 is a DUD** (aborts at `env_init`, -ENODEV, before the prompt). 2023.2 was proven on the original machine |

## Quirks that cost real debug time (fixes live in the workbench / skill script)

| Quirk | Handling |
|---|---|
| DDR/IO PLLs **powered down at cold boot** (`PLL_PWRDWN=1`, `PLL_STATUS 0x39`) — ps7_init poll hangs | RC1: clear PWRDWN bit (UG585 Exit-Sleep) before ps7_init — baked into `scripts/jtag_qspi_flash.tcl` and the workbench |
| Stale A9 MMU faults DRAM after init | RC2: `rst -processor` drops it, DDR survives — baked in |
| ~50% `sf probe` failures per bring-up | jtagterminal **socket leak** (two terminals on one DCC channel) — driver now does `jtagterminal -stop` first; 8/8 after fix |
| `mrd -value` returns **decimal** over xsdbserver | parse decimal, not hex (workbench `_reg()` handles it) |
| JTAG-HS3 drops off USB under load | reseat / own port; start a **fresh hw_server** (stale DCC stream state causes empty `sf probe`) |
| OCM-linked helper `dow` fails "OCM is not enabled at 0xFFFC0000" | SLCR-unlock → `OCM_CFG=0xF` remap first (skip for DDR-linked ELFs) |
| Dashboard stuck on "loading…" (helper dropdown blank, JTAG chain won't auto-populate) | Orphaned `hw_server`/`xsdb`/`jtagterminal`/`uvicorn` from killed dashboards contend the one FTDI cable (a 2nd `uvicorn` can even steal `:8088`) — the browser hits a wedged instance while the endpoints look healthy to `curl`. Kill them all, then `dashboard/run.sh --fresh` for one clean JTAG stack. **Own the server in a real terminal** — a reaped/background launch dies mid-session and orphans the DCC bridge, which is the usual cause. |
| `pkill -f hw_server` kills your own shell | With `-f`, pkill matches its own invoking shell's command line (which contains the string). Use the bracket trick — `pkill -f '[h]w_server'` — or match by process name without `-f`. |
| `ps7_init failed: Memory read error at 0xE0001034. AP transaction timeout` (DAP then wedges `0x30000021`) | **2021.1 `hw_server` loses the `rst -system -stop` halt-on-reset race** to the on-flash FSBL; 2022.2 wins it. Flash with a 2022.2+ `hw_server`/`xsdb`. Recover the wedge with `rst -dap` under 2022.2. See "Toolchain: flash with a 2022.2+ hw_server" below. |

## Toolchain: flash with a 2022.2+ `hw_server`/`xsdb` — 2021.1 loses the halt-on-reset race

**HW-confirmed 2026-07-14 on a clean, power-cycled baseline.** `rst -system -stop`
(halt-on-reset, AR 68065) must pin the A9 at the reset vector before the hardwired-QSPI
BootROM boots the on-flash FSBL. Whether the halt wins that race is **`hw_server`-version
dependent**:

- **2021.1** (Vivado *or* Vitis): **loses.** On a freshly power-cycled board holding a valid
  image, `ps7_init`'s first PS access fails — `Memory read error at 0xE0001034. AP
  transaction timeout` (the FSBL already seized the PS) — and the A9 falls off the debug bus
  (DAP status `0x30000021`, "AHB AP transaction error"). Warming the `hw_server` first does
  **not** help (a warm 2021.1 server still lost on a pristine board).
- **2022.2**: **wins**, cold, every time — clean `ps7_init` → erase → program → byte-verify.

Session evidence on the real `scripts/jtag_erase_reflash.tcl`: **2021.1 = 0/4 pass, 2022.2 =
3/3 pass**; the clean A/B was 2021.1-*warm* FAIL vs 2022.2-*cold* SUCCESS on back-to-back
power-cycled boots, only the toolchain differing (and 2021.1 had the warm advantage). Note a
**blank** chip hides the bug (no FSBL to race), so a 2021.1 "success" on an already-erased
board is a false positive — always test against a board holding a valid image.

**Fix:** run the flash with a **2022.2-or-newer** `hw_server` — same script, same `ps7_init`,
same board. A **Windows** box on **Vivado 2021.1** reproduces the `0xE0001034` failure
reliably for exactly this reason; get a 2022.2+ `hw_server` there.

**The fix is SERVER-SIDE — you do NOT need a full toolchain upgrade (HW-confirmed 2026-07-14).**
A **2022.2 `hw_server` with a 2021.1 `xsdb`/`program_flash` client** (pointed at it via
`--url tcp:localhost:3121`) WINS the race on the clean power-cycled baseline where an all-2021.1
stack loses. So on Windows you can keep your 2021.1 client and just run a lightweight 2022.2
`hw_server` — AMD's free **Vivado Lab Edition** / standalone hardware server (2022.2+) is far
smaller than full Vivado/Vitis. Start it, then `--url` your existing tools at it.

**Recovery:** `rst -dap` under 2022.2 puts the A9 back on the bus (the driver's `select_a9`
does this automatically — commit b210080). A deep/accumulated wedge from repeated failed
halts can defeat even `rst -dap` → physical power-cycle.

**Priming/retry does NOT work — disconfirmed 2026-07-14.** A plain `rst -system` (+`con`, let
the FSBL boot) *before* the `rst -system -stop` let 2021.1 win in ~13 probe runs, which looked
like a version-independent workaround — but on a **clean power-cycled baseline** it still failed
with `E0001034`. Those ~13 "wins" were artifacts of reset-saturated, non-pristine board state,
not a reproducible fix. Do not rely on a priming/retry reset; use a 2022.2 `hw_server` (see the
server-side note above).

## Where the full history lives

The investigation record (plans, findings, session journal) is in the workbench repo at
`.threads/zynq-boot/20260629-hardwired-qspi-jtag-flash/` — read its `handoff.md`
"Current truth" first. Primary-source research: the repo's
`.research/session-20260629-093358/` (cfgmem-helper forensics, AR 76051, DCC, JEDEC
bypass). The custom JEDEC-ID-agnostic U-Boot build for this flow is
`references/custom-dcc-uboot-build.md` + `patches/0002-…-2022.01.patch`.

## History

- 2026-07-06: reference created. Flow HW-proven Stages 1–3 (2026-06-30 → 2026-07-01);
  workbench repo published; JEDEC-bypass U-Boot built (bench proof pending).
- 2026-07-09: flash UI reworked — a single header-checksum-gated **Write QSPI** button
  replaces "Write back dump" + "Flash uploaded image"; `write_flash` now refuses
  blank/invalid images (after a write-back-the-erased-dump scare that briefly left the
  chip blank). Full destructive Write-QSPI round-trip re-proven on HW — recovered the
  board from the erased state with the verified backup. Also merged the parallel-machine
  work (recover-from-dud DCC fix, session logging, launch preflight, reconstruct-project).
- 2026-07-14: diagnosed a "wedges the DAP" board (a second fleet unit with a Winbond
  `w25q128jv` flash and a different mcs). Root cause = **toolchain**: the flash's
  `rst -system -stop` halt-on-reset loses the FSBL race under a **2021.1 `hw_server`** →
  `E0001034` + DAP `0x30000021`, but **wins under 2022.2** — confirmed on a clean
  power-cycled A/B (2021.1-warm FAIL vs 2022.2-cold SUCCESS) plus a 0/4-vs-3/3 real-script
  tally. Also reproduced on the user's Windows Vivado-2021.1 box. Ruled out (via controlled
  tests): the FSBL/mcs (both MDR_B and LDR_B images behaved the same), `hw_server` warmth,
  and cold-vs-warm start. Fix: use 2022.2+. Follow-ups same day: the fix is **server-side** — a
  2022.2 `hw_server` + a 2021.1 `xsdb` client (via `--url`) WINS (so a lightweight Lab-Edition
  server suffices, no full toolchain upgrade); and a **priming/retry reset is NOT a workaround**
  (won ~13× on non-pristine boards but failed on a clean power-cycled baseline). See "Toolchain:
  flash with a 2022.2+ hw_server".
