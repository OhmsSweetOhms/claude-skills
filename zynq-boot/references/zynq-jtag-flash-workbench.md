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
| QSPI flash | s25fl128s, 16 MiB; RDID `01 20 18 4D 01 80` |
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
