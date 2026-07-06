# Staged validation plan — `jtag_qspi_flash` (boot-mode-independent QSPI flash over JTAG)

Target: Zynq-7000 (primary) / ZynqMP (secondary), Vitis 2023.2 at `/tools/Xilinx/Vitis/2023.2`.
Driver under test: `scripts/jtag_qspi_flash.sh` -> `scripts/jtag_qspi_flash.tcl`.
Reference: `references/jtag-flash-bootmode-independent.md`.

This plan resolves the two author-flagged unknowns (halt-vs-BootROM timing; whether
the cfgmem helper presents an interactive DCC prompt) and one reviewer-found gap
(the helper has no `loadx`/`loady`, so the documented manual transfer step cannot
run as written). Each stage has explicit pass/fail and exact commands. Stages are
gated: do not advance until the prior stage passes.

> **Fixes already applied to the script (2026-06-29, post-review).** Two review Majors
> were corrected in `jtag_qspi_flash.tcl` before this plan was filed:
> (1) the `after 200` delay between `rst -system` and `stop` was **removed** (it defeated
> the AR 76051 halt), and (2) the non-existent `loadx` transfer was **replaced** by JTAG
> staging via `dow -data <img> 0x01000000` (script `--image`/`ZB_IMAGE`). So Stage-2
> decision-tree A is now a *confirmation* that immediate `rst -system; stop` wins (not a
> fix), and the Stage-2 "reviewer gap" path 1 (`dow -data`) is the built-in default.
> STATUS: **Stages 1–3 PASSED on hardware** (hardwired-QSPI xc7z020; Stage 1–2
> 2026-06-30, Stage 3 destructive round-trip 2026-07-01). This plan is kept as the
> template for validating the flow on the *next* board; the executed record lives in
> the workbench repo's `.threads/zynq-boot/20260629-hardwired-qspi-jtag-flash/` and in
> `references/jtag-flash-bootmode-independent.md` History.

---

## Stage 0 — Static, no hardware

All runnable on this host now (Vitis 2023.2 present; no board attached).

| # | Command | Pass criteria |
|---|---|---|
| 0.1 | `bash -n scripts/jtag_qspi_flash.sh` | exit 0 (already confirmed OK) |
| 0.2 | `shellcheck scripts/jtag_qspi_flash.sh` (install if absent: `apt-get install shellcheck`) | no error-level findings; review SC2086/word-split warnings |
| 0.3 | Tcl parse: `tclsh -c 'source scripts/jtag_qspi_flash.tcl'` will fail at `connect` (no env/server) — instead do a brace/parse-only check: `echo 'proc connect a {}; proc targets a {}; ...stub all xsdb cmds...' ` is heavy; simplest is `tclsh` + `info complete [read file]`. Acceptable substitute: visual brace balance + Stage-0.7 (commands exist) | no `missing close-brace`/`unmatched` parse error |
| 0.4 | `grep -nE '/home/|/Users/|/media/[^ ]*\$USER\|<username>' scripts/*.sh scripts/*.tcl references/jtag-flash-bootmode-independent.md` | **zero hits** (confirmed). `/tools/Xilinx` is allowed |
| 0.5 | `readelf -h /tools/Xilinx/Vitis/2023.2/data/xicom/cfgmem/uboot/zynq_qspi_x1_single.bin \| grep -E 'Type\|Entry'` | `Type: EXEC`, `Entry point address: 0xfffc0000` (confirmed) |
| 0.6 | `strings -a <helper> \| grep -iE 'arm_dcc'` and `... \| grep -iE 'serial_zynq\|cadence_serial\|zynq_serial'` | arm_dcc present; **no** Cadence/Zynq UART serial driver symbol (confirmed: only `arm_dcc_*` + no-op `*debug_uart*` stubs) |
| 0.7 | `source /tools/Xilinx/Vitis/2023.2/.settings64-Vitis.sh; for c in connect targets stop con mwr dow rst jtagterminal; do grep -q "^$c$\|^$c " /tools/Xilinx/Vitis/2023.2/scripts/xsct/xsdb/cmdlist && echo "$c OK"; done` | all 8 print OK (confirmed) |
| 0.8 | Verify options exist in `scripts/xsct/xsdb/xsdb.tcl`: `targets` has `set/nocase/filter/target-properties`; `rst` has `system/processor/dap`; `jtagterminal` has `-start/-stop/-socket` | all present (confirmed) |
| 0.9 | **Reviewer gap check:** `strings -a <helper> \| grep -iE 'loadx\|loady\|ymodem\|xmodem'` | **EXPECT EMPTY.** Confirms the cfgmem helper cannot ingest an image interactively — Stage 2/3 must stage the payload to DRAM via xsct memory write (`dow -data`/`mwr`), not via `loadx`. If this is non-empty on another helper variant, the loadx path is viable |

Stage 0 capture for History: tool version, helper md5 (`md5sum <helper>`), confirmation
strings, shellcheck summary.

**Gate:** all of 0.1–0.9 pass. 0.9 in particular changes the Stage-2/3 method.

---

## Stage 1 — xsct connect + PS init, NO U-Boot, NO flash writes

Hardware on bench, JTAG (and hw_server) only. The board is in its hardwired-QSPI
strap; we never change straps. **Read-only to flash.**

Run a trimmed Tcl by hand (do NOT run the full driver yet — it loads U-Boot and
opens jtagterminal). Use the driver's steps up to and including `ps7_init`:

```tcl
connect -url tcp:localhost:3121
targets -set -nocase -filter {name =~ "*Cortex-A9*#0"}
rst -system
stop                          ;# NOTE: test BOTH with and without the after-200 (see decision tree)
source /abs/ps7_init.tcl
ps7_init
catch { ps7_post_config }
```

Pass/fail per step:

| Step | Pass | Fail -> action |
|---|---|---|
| `connect` | returns a channel id, no error | start `hw_server`; check JTAG cable/url |
| `targets -set` | exactly one target selected; `targets` echo shows the A9#0 with a `*` | filter matched 0 or >1 — adjust `--tgt` |
| `rst -system; stop` | `stop` returns; subsequent `mrd 0xF8000000` (SLCR) succeeds | if `stop` errors "no target" the DAP needs re-poll after reset — see decision tree |
| `ps7_init` | returns `""` / no error | wrong ps7_init for the silicon; check rev |
| **prove PS init took** | read back a register ps7_init writes and confirm a non-reset value, e.g. clock or QSPI MIO: `mrd 0xF8000700` (MIO_PIN_00) and a QSPI MIO pin e.g. `mrd 0xF8000708`; and the QSPI ref clk `mrd 0xF8000158` (QSPI_CLK_CTRL) — expect divisors/MIO mux set, not the POR default | if regs still read POR defaults, ps7_init didn't run on the active target — confirm target selected before `ps7_init` |

Pick **one** MIO/clock register whose post-`ps7_init` value you know (compare against a
sibling board that boots, or against the ps7_init.tcl source `mwr` list). The proof is:
value matches ps7_init's intended write, not the POR reset value.

Stage 1 capture: the exact register addr/value pair proving init, the target name
string, and whether `stop` succeeded immediately after `rst -system` or needed a delay.

**Gate:** PS-init register readback proves init. Do NOT load U-Boot until this passes.

---

## Stage 2 — Load U-Boot, get a DCC prompt. NO erase/write.

Still read-only to flash. This stage resolves the two flagged unknowns.

```tcl
# (continue the Stage-1 session, core halted, PS inited)
dow /tools/Xilinx/Vitis/2023.2/data/xicom/cfgmem/uboot/zynq_qspi_x1_single.bin
con
jtagterminal -start          ;# opens a TCP terminal bridged to the target DCC
```

Then attach a terminal to the socket jtagterminal prints (e.g. `nc localhost <port>`
or `xsct`'s own terminal), and observe.

### Decision tree A — halt-vs-BootROM timing (the `after 200` question)

The shipped Tcl does `rst -system; after 200; stop`. The grounding source (research
finding E / report recipe 2) and AR 76051 say `rst -system; stop` **immediately**.
200 ms is enough for the BootROM to boot a small stale FSBL. Test order:

1. Run with the current `after 200`. After `stop`, read the PC: `rrd pc` (A9) or
   inspect `mrd` of OCM `0xFFFF0000`. Also read a tell-tale: does DDR/OCM already
   contain a booted image (non-zero at the stale image's load addr)?
   - **If the core had already left the BootROM / a stale image is resident** ->
     the race was LOST. Go to step 2.
   - **If PC is in BootROM/OCM ROM space and no stale image loaded** -> race won
     even at 200 ms; record the margin but still try step 2 to confirm robustness.
2. Edit Tcl to remove the delay (`rst -system; stop`). Re-run. Re-check PC/residue.
   - Race won consistently -> **fix: drop the `after 200`** (aligns with grounding).
3. If even immediate `stop` loses (BootROM boots faster than xsdb can re-select the
   DAP and halt): escalate to **halt-on-reset** — `rst -processor` won't help (PS reset
   re-runs ROM); instead erase QSPI sector 0 first so there is no valid image for the
   BootROM to boot (chicken-and-egg: do this from a one-time successful halt), or use
   the debugger's reset-and-halt if exposed for this part.
4. Record which of {after-200 won, immediate won, sector-0-erase needed} is true for
   this board — that is the load-bearing History datum.

### Decision tree B — interactive prompt vs program_flash framing

After `con` + `jtagterminal`, in the attached terminal press Enter / Ctrl-C during the
boot-delay window:

- **A U-Boot prompt appears (`Zynq> ` / `=> `):** the helper has an interactive DCC
  console. Proceed. Run ONLY `sf probe 0 0 0` (read-only). Pass = it prints flash
  geometry (`SF: Detected ...`). If it prints `unrecognized JEDEC id bytes` -> that is
  the separate JEDEC gate (research finding A); stop and switch to a JEDEC-patched
  U-Boot before any Stage 3.
- **No prompt, only scripted framing / silence:** the cfgmem helper may have
  `bootdelay<=0` or speak only program_flash's protocol. -> Switch `--uboot` to a
  custom `CONFIG_ARM_DCC=y` + `stdin/stdout/stderr=dcc` build **that also has
  `CONFIG_CMD_LOADX/LOADY`** (needed for Stage 3 transfer — see gap below). Re-run.

### Reviewer gap — how the image actually gets into DRAM

Stage 0.9 confirms the cfgmem helper has **no `loadx`/`loady`**. So the reference's and
Tcl comment's "loadx / load over DCC" step **cannot be executed with the cfgmem helper**.
Two correct alternatives — settle which works here:

1. **Stage payload to DRAM via xsct memory write (works with the cfgmem helper):**
   halt the core, `dow -data /abs/image.bin 0x01000000` (raw load to DDR), resume, then
   at the U-Boot prompt `sf write 0x01000000 0 <len_hex>`. Pass = `sf write` reports OK.
2. **Custom U-Boot with `CONFIG_CMD_LOADX` + DCC console:** then `loadx` over the DCC
   terminal works as the docs describe.

Record which path is used; the reference must be corrected to match (see review).

Stage 2 capture: prompt string seen (or "none"), `sf probe` output (geometry or JEDEC
error), which transfer path was chosen, and the resolved halt-timing setting.

**Gate:** a working interactive prompt AND a proven DRAM-staging method AND a clean
`sf probe`. Do NOT erase/write until all three hold.

---

## Stage 3 — Full flash + verify (DESTRUCTIVE — attended only)

Backup first; this erases QSPI. **Never run unattended.**

1. **Backup the current flash** (so a bad write is recoverable):
   `sf probe 0 0 0; sf read 0x02000000 0 <total_len_hex>` then from xsct dump DDR to a
   host file: `mrd -bin -file qspi_backup.bin 0x02000000 <words>`. Keep `qspi_backup.bin`.
2. **Erase + write** the known-good image (staged to DRAM per Stage 2):
   ```
   sf probe 0 0 0
   sf erase 0 <len_hex>
   sf write <dram_addr> 0 <len_hex>
   ```
   Pass = `Erased: OK` and `Written: OK` (no timeout/abort).
3. **Read-back verify in-place:** `sf read 0x03000000 0 <len_hex>`, then compare the
   read-back DDR region against the source image. From xsct: `mrd -bin -file rb.bin
   0x03000000 <words>` then host `cmp rb.bin image.bin`. Pass = identical (or identical
   over the written length).
4. **Power-cycle to standalone boot** (the board is hardwired QSPI, so just power-cycle
   — no strap change): confirm the flashed firmware boots (its own console/LED/telemetry).
   Pass = the new image runs.
5. **Rollback path if step 4 fails:** re-enter JTAG, repeat Stage 1–2, `sf erase` +
   `sf write` of `qspi_backup.bin` to restore the prior image.

Stage 3 capture for History: image name + sha256, erase/program elapsed times, the
`Written: OK` / verify-compare result, and "booted standalone after power-cycle: yes/no".

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Race lost: BootROM boots stale flash before `stop`; PS seized | High (this is the whole AR 76051 failure) | Flash flow fails / inconsistent | Stage-2 decision tree A; drop `after 200`; fall back to sector-0 erase from a one-time halt |
| `sf erase`/`write` interrupted (cable yank, host sleep, Ctrl-C mid-write) -> half-erased QSPI -> board won't boot ("brick" until re-flashed) | Medium | Board non-bootable but JTAG still alive (straps don't disable JTAG) -> recoverable | Stage-3 backup first; never run unattended; disable host sleep; UPS/stable power |
| Wrong `-flash_type`/topology helper for the actual QSPI wiring -> writes garbage layout | Medium | Bad image; possible wedged controller | Match helper variant to the board's QSPI bus width/connection; verify `sf probe` geometry against the datasheet density |
| JEDEC id not in table -> `sf probe` aborts | Medium | Can't flash with stock helper | Use JEDEC-patched helper (research finding A) |
| ZynqMP path: `mwr 0xFF5E0200 0x100` bit encoding / PMUFW-before-psu_init ordering wrong | Unknown (unverified) | psu_init fails / wrong boot-mode override | Verify the BOOT_MODE_USER bitfield against UG1085 before trusting; test PMUFW `con` reaches its ready print |
| `dow -data` to DDR before DDR is up | Low (ps7_init runs first) | Load silently fails | Confirm Stage-1 PS-init readback proves DDR controller configured before staging payload |

**Do NOT run unattended:** Stage 3 entirely (erase/write/verify), and any halt-timing
experiment that leaves the board mid-reset. Stage 0 is safe to automate; Stage 1–2 are
read-only but should be watched because they reset the PS.
