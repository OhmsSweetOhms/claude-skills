# ADI Vendoring Profiles

ADI-based system HIL projects can use a named profile to keep HDL and no-OS
patches reproducible across Stage 14, Stage 16, and Stage 17.

## Configuration

Set `build.flow` to `adi_make` and set `adi.active_profile` in `socks.json`.
The profile manifest must live under the project `ADI/` tree and identify the
ADI HDL project, the no-OS upstream source, copied files, and patch files.

Stage 14 calls `scripts/hil/adi_profile_apply.py` before ADI Make. The helper:

- selects the manifest entry whose project directory matches `build.project_dir`
- materializes no-OS under `ADI/no-OS/work/active`
- copies HDL upstream files into the live ADI project
- applies HDL and no-OS patches
- writes `build/state/adi-profile-apply.json`

`ADI/no-OS/work/` must be ignored by the project repository. Do not commit the
materialized active tree.

## Patch Application Rule

HDL patches are applied from the repository root with an explicit
`--directory=<target-dir>` prefix. Do not apply patches from inside the ADI
project subdirectory when that subdirectory is inside a parent Git repository;
Git can report success while changing no files if the patch paths are rooted
for a different directory.

## Rules of Thumb

Treat an ADI MxFE profile as a coupled HDL, no-OS, HMC7044, FPGA GT, and
hardware-validation contract. A numerically correct converter plan is only one
part of the profile.

- Start from a hardware-proven ADI operating point when possible. Change one
  axis at a time: converter clocking, JESD M/L/S/NP, GT refclk, lane rate,
  HMC7044 outputs, or no-OS datapath.
- Validate AD9081/AD9986 JESD mode and datapath first with pyadi-jif or the
  local mode tables. Clean NCO math does not prove that the required
  decimation/interpolation split exists.
- Do not assume RX and TX can stay symmetric. The valid RX DDC and TX DUC mode
  tables can force different sample rates, lane counts, or JESD modes.
- Use the 8B/10B lane-rate relation before editing HDL:
  `lane_rate = sample_rate * M * NP * 10 / (L * 8)`. For the ADI 40-bit GT
  datapath, the lane-rate-derived link clock is usually `lane_rate / 40`.
- Keep HDL-supported `NP` constraints explicit. A pyadi-jif-valid `NP24` mode
  is not usable unless the target HDL transport supports it.
- FPGA GT refclk legality is separate from converter/JESD legality. A rate that
  is clean for NCO planning can still be illegal or awkward for CPLL/QPLL.
- Check `util_mxfe_xcvr` static parameters before a long Vivado/HIL run when
  changing lane rates or GT refclk. Mixed RX/TX rates can conflict in shared
  CPLL fields even when each direction is individually legal.
- If RX and TX Wizard references disagree on shared fields such as
  `CH_HSPMUX`, `CPLL_CFG0/1/2`, or `CPLL_FBDIV*`, do not apply a blind global
  override. Retarget rates onto a common CPLL family, split the transceiver
  util, add per-channel parameterization, or use a targeted DRP experiment.
- no-OS ADXCVR setup can rewrite runtime fields such as CPLL dividers,
  OUT_DIV, PROGDIV, and CLK25 dividers, but do not assume it rewrites GTH4 CDR
  fields. Static RX CDR settings can still decide whether a low-rate RX link
  reaches DATA.
- Treat SYSREF as secondary when it is captured and alignment errors are clear.
  Zero lane status/ILAS with an ADXCVR buffer error usually points first at GT
  recovery, lane driving, or lane mapping.
- Static lane-map review must include both sides: no-OS logical/physical lane
  enables and the board XDC pin mapping. Non-QUAD low-lane-count modes can use
  surprising physical lanes.
- UART `DATA` and IIOD readiness prove HDL/no-OS/JESD bring-up. They do not by
  themselves prove RF spectrum, DAC physical mapping, or per-band virtual
  converter routing.

## Verified Profile Catalog

The following ZCU102 + AD9986-FMCA profiles are hardware-validated through
SOCKS Stage 17 (RX/JTX + TX/JRX both in `DATA`, SYSREF aligned, IIOD running).
Patch and profile-directory paths are repo-relative to the ADI vendoring tree
that ships with the consuming project (typically a `socks/` monorepo with
`ADI/projects/<adi-project>/profiles/<profile-name>/` and
`ADI/no-OS/patches/<NNNN>-*.patch`).

### Profile `6144-l1-clean-jesd204b-rxm8l4-txm8l8`

Purpose: GPS L1 clean-NCO profile. The 3.93216 GHz AD9986 converter clock
makes 1575.42 MHz exactly `26257 / 65536` of converter rate (FTW48 =
`112772956291072`, 32 trailing binary zeros, error = 0 Hz).

| Side | JESD mode | M / L / S / NP / K / HD | Sample rate | Converter clock | Lane rate | Datapath |
|------|-----------|--------------------------|-------------|-----------------|-----------|----------|
| RX (JTX) | 10 | 8 / 4 / 1 / 16 / 32 / 0 | 61.44 MSPS | 3.93216 GHz ADC | 2.4576 Gbps | 4× main / 16× channel decimation, NCO = 1575.42 MHz |
| TX (JRX) | 15 | 8 / 8 / 1 / 16 / 32 / 0 | 122.88 MSPS | 3.93216 GHz DAC | 2.4576 Gbps | 4× main / 8× channel interpolation, NCO = 1575.42 MHz |

HMC7044 clock outputs:

- PLL2 = 2.94912 GHz
- FPGA GT refclk = 122.88 MHz
- device refclk = 245.76 MHz
- TX device clk = RX device clk = 61.44 MHz
- SYSREF = 1.92 MHz

Profile directories (HDL manifest + patches + README + operating-point record):

- `ADI/projects/gps_streaming/profiles/6144-l1-clean-jesd204b-rxm8l4-txm8l8/`
- `ADI/projects/ad9081_fmca_ebz/zcu102/profiles/6144-l1-clean-jesd204b-rxm8l4-txm8l8/`
  (ADI reference mirror)

Required no-OS patch:

- `ADI/no-OS/patches/0008-app-config-gps-l1-clean-6144.patch` — retargets the
  6144 no-OS app config to 3.93216 GHz converter clocks, GPS L1 main NCOs,
  RX 4×16, and TX mode 15 4×8.

Required HDL patches: live inside each profile directory (system_project +
timing patches that apply to upstream `hdl/projects/<adi-project>/zcu102/`).

pyadi-jif gate evidence:

- RX mode 10.0 M8/L4/F4/S1/NP16/K32/HD0 supports 4×16 decimation at 3.93216
  GHz with 2.4576 Gbps lanes.
- TX mode 15 M8/L8/F2/S1/NP16/K32/HD0 exposes 4×8 interpolation at the same
  converter clock.

Hardware validation command:

```bash
python3 ~/.claude/skills/socks/scripts/socks.py \
  --project-dir systems/zcu102-gps-streaming \
  --validate --clean \
  --settings /tools/Xilinx/Vivado/2023.2/.settings64-Vivado.sh
```

Decisive PASS artifacts (`codex-handoff/plan-03d/artifacts/`):

- `uart-20260508-162030.log` — Stage 17 UART transcript
- `pipeline_20260508_162102.log` — SOCKS pipeline log
- `pipeline_20260508_162102.chart` — stage chart

Observed at PASS: TX DAC initialized 122.876 MHz, RX ADC initialized 61.438
MHz, both ADXCVRs 2457.6 MHz, lane rate /40 = 61.440 MHz, TX LMFC 3.840 MHz,
RX LMFC 1.920 MHz, no SYSREF alignment error, IIOD started.

### Profile `24576-clean-l1-jesd204b-rxm8l4-txm8l4`

Purpose: same clean GPS L1 NCO math at the ADI 245.76 MHz M8/L4 operating
point. This is a *separate HDL bitstream* from the 6144 profile, not a runtime
toggle — different GT refclk and PL clock domain.

| Side | JESD mode | M / L / S / NP / K / HD | Sample rate | Converter clock | Lane rate | Datapath |
|------|-----------|--------------------------|-------------|-----------------|-----------|----------|
| RX (JTX) | 10 | 8 / 4 / 1 / 16 / 32 / 0 | 245.76 MSPS | 3.93216 GHz ADC | 9.8304 Gbps | 4× main / 4× channel decimation, NCO = 1575.42 MHz |
| TX (JRX) | 9 | 8 / 4 / 1 / 16 / 32 / 0 | 245.76 MSPS | 3.93216 GHz DAC | 9.8304 Gbps | 4× main / 4× channel interpolation, NCO = 1575.42 MHz |

HMC7044 clock outputs:

- PLL2 = 2.94912 GHz
- FPGA GT refclk = 491.52 MHz
- device refclk = 491.52 MHz
- TX device clk = RX device clk = 245.76 MHz
- SYSREF = 1.92 MHz

Profile directories:

- `ADI/projects/gps_streaming/profiles/24576-clean-l1-jesd204b-rxm8l4-txm8l4/`
- `ADI/projects/ad9081_fmca_ebz/zcu102/profiles/24576-clean-l1-jesd204b-rxm8l4-txm8l4/`

Required no-OS patch:

- `ADI/no-OS/patches/0011-app-config-gps-l1-clean-24576.patch` — retargets the
  245.76 no-OS app config to 3.93216 GHz converter clocks, GPS L1 main NCOs,
  and RX/TX mode 10/9 M8/L4 4×4.

pyadi-jif gate evidence:

- RX mode 10.0 and TX mode 9 validate as HD=0 M8/L4/F4/S1/NP16/K32 at 245.76
  MSPS with 9.8304 Gbps lanes.

Hardware validation command: same `--validate --clean` SOCKS invocation as
above against the same `systems/zcu102-gps-streaming` project dir.

Decisive PASS artifacts (`codex-handoff/plan-03d/artifacts/`):

- `uart-20260508-193305.log`
- `pipeline_20260508_193337.log`
- `pipeline_20260508_193337.chart`

Observed at PASS: TX DAC initialized 245.755 MHz, RX ADC initialized 245.753
MHz, both ADXCVRs 9830.4 MHz, lane rate /40 = 245.76 MHz, LMFC 7.680 MHz, no
SYSREF alignment error, IIOD started.

### Profile `2048-quad-band-jesd204b-rxm8l2-txm8l4` (active for gps_design)

Purpose: quad-band-capable clean-NCO substrate (groundwork for L5 + L2 + L1 +
Iridium ALT-NAV). RX runs native 20.48 MSPS per channel; TX runs 81.92 MSPS.
The retarget from the earlier `txm8l8` (TX 122.88 MSPS) variant places RX
1.6384 Gbps and TX 3.2768 Gbps onto a shared GTH4 CPLL static family, which
the earlier txm8l8 sibling could not (see Known Pitfalls below).

| Side | JESD mode | M / L / S / NP / K / F / HD | Sample rate | Converter clock | Lane rate | Datapath |
|------|-----------|------------------------------|-------------|-----------------|-----------|----------|
| RX (JTX) | 4 | 8 / 2 / 1 / 16 / 32 / 8 / 0 | 20.48 MSPS | 1.96608 GHz ADC | 1.6384 Gbps | 4× main / 24× channel decimation, link clock 40.96 MHz |
| TX (JRX) | 9 | 8 / 4 / 1 / 16 / 32 / 4 / 0 | 81.92 MSPS | 3.93216 GHz DAC | 3.2768 Gbps | 6× main / 8× channel interpolation, link clock 81.92 MHz |

HMC7044 clock outputs:

- PLL2 = 2.4576 GHz
- FPGA GT refclk = 204.8 MHz
- device refclk = 245.76 MHz
- RX device clk = 40.96 MHz
- TX device clk = 81.92 MHz
- SYSREF = 1.28 MHz

Profile directories:

- `ADI/projects/gps_streaming/profiles/2048-quad-band-jesd204b-rxm8l2-txm8l4/`
- `ADI/projects/ad9081_fmca_ebz/zcu102/profiles/2048-quad-band-jesd204b-rxm8l2-txm8l4/`

Required no-OS patch stack (delta order; the profile manifest enumerates the
canonical apply order):

- `ADI/no-OS/patches/0012-app-clock-hmc7044-2048-profile.patch` — 2.4576 GHz
  HMC7044 PLL2 / 204.8 MHz FPGA GT refclk clock point (2048 base).
- `ADI/no-OS/patches/0014-app-config-gps-quad-band-2048.patch` — RX mode 4
  M8/L2 at ADC 1.96608 GHz with 4×24 decimation + quad-band NCO plan.
- `ADI/no-OS/patches/0015-app-jesd-progdiv-outclk-2048-profile.patch` — ADXCVR
  PROGDIV clock selection for mixed RX/TX link clocks.
- `ADI/no-OS/patches/0016-app-clock-hmc7044-2048-tx8192-profile.patch` —
  HMC7044 channel 6 retargeted to 81.92 MHz TX core clock (delta on 0012).
- `ADI/no-OS/patches/0017-app-jesd-init-2048-tx8192-profile.patch` —
  `app_jesd_init` retargeted to 40.96 MHz RX / 81.92 MHz TX link clocks +
  1.6384 / 3.2768 Gbps lanes (supersedes the txm8l8 sibling's 0013).
- `ADI/no-OS/patches/0018-app-config-gps-quad-band-2048-tx8192.patch` —
  TX/JRX moved to mode 9 M8/L4 with 6×8 interpolation (preserves 3.93216 GHz
  DAC clock + clean L1 NCO).

Required HDL patches: live inside the profile directory; cover system_project,
timing, system_bd, and a static GT override needed by the shared CPLL family.

pyadi-jif gate evidence:

- RX mode 4.0 M8/L2/F8/S1/NP16/K32/HD0 at 20.48 MSPS with ADC 1.96608 GHz
  and 1.6384 Gbps lanes.
- TX mode 9 M8/L4/F4/S1/NP16/K32/HD0 at 81.92 MSPS with DAC 3.93216 GHz
  and 3.2768 Gbps lanes.

Generated-BD ADXCVR static check (run before Vivado):

```bash
python3 ~/.claude/skills/socks/scripts/hil/adxcvr_gt_param_check.py \
  --project-dir systems/zcu102-gps-streaming
```

Expected match against cached 1.6384/3.2768 GTH4 references:
`CH_HSPMUX = 0x3c3c`, `CPLL_CFG0/1/2 = 0x0ffa / 0x0021 / 0x0202`,
`CPLL_FBDIV / FBDIV_4_5 = 4 / 4`, `RXCDR_CFG2_GEN2 = 0x245`.

Hardware validation command:

```bash
python3 ~/.claude/skills/socks/scripts/socks.py \
  --project-dir systems/zcu102-gps-streaming \
  --stages 14,15,16,17 \
  --settings /tools/Xilinx/Vivado/2023.2/.settings64-Vivado.sh
```

(`--validate --clean` is equivalent when starting from a clean checkout. The
explicit stage list is the form used during the plan-03d retarget runs.)

Decisive PASS artifacts (`codex-handoff/plan-03d/artifacts/`):

- `2048-txm8l4-stage17-pass-20260511-1951.md` — evidence summary
- `uart-20260511-195057.log` — UART transcript (regression baseline)
- `pipeline_20260511_195129.log` — SOCKS Stage 17 PASS pipeline log
- `pipeline_20260511_195129.chart` — stage chart

Observed at PASS: RX/JTX Link2 in `DATA` at 1.6384 Gbps, TX/JRX Link0 in
`DATA` at 3.2768 Gbps, RX lane rate /40 = 40.960 MHz, TX lane rate /40 =
81.920 MHz, SYSREF captured, no alignment error, IIOD started.

## Known Pitfalls

These all bit during plan-03d on the ZCU102 + AD9986-FMCA. Document them in a
profile README when a new operating point is added.

### Shared CPLL conflicts on mixed RX/TX rates

The `util_mxfe_xcvr` HDL block exposes `CPLL_CFG0/1/2` and `CPLL_FBDIV*` as
common parameters across all GT channels. When RX and TX lane rates land on
different GTH4 Wizard reference families, a blind global override is not safe.
The plan-03d failure path was:

- `2048-quad-band-jesd204b-rxm8l2-txm8l8` (TX 122.88 MSPS, lanes 2.4576 Gbps
  TX + 1.6384 Gbps RX) — RX/JTX never left CGS. The 2.4576 Gbps TX Wizard
  reference and the 1.6384 Gbps RX Wizard reference disagree on
  `CPLL_CFG0/1/2` (`0x01fa / 0x0023 / 0x0002` vs `0x0ffa / 0x0021 / 0x0202`)
  and on `CPLL_FBDIV` (`3` vs `4`). No global setting could satisfy both.

The fix that worked was to retarget rates onto a common family: TX → 81.92
MSPS mode 9 M8/L4 → 3.2768 Gbps. The 3.2768 Gbps and 1.6384 Gbps GTH4 Wizard
references *do* share `CPLL_CFG0/1/2 = 0x0ffa / 0x0021 / 0x0202`,
`CPLL_FBDIV = 4`, and `CH_HSPMUX = 0x3c3c`. RX-only direction-specific
dividers and CDR fields stay separate.

Before any long Vivado/HIL run that changes lane rates, run the static check:

```bash
python3 ~/.claude/skills/socks/scripts/hil/adxcvr_gt_param_check.py \
  --project-dir <project-dir> \
  --rx-lane-rate <gbps> \
  --tx-lane-rate <gbps> \
  --refclk-mhz <mhz>
```

A `needs per-channel/split util or lane-rate retarget` warning is a real HDL
architecture blocker, not a cosmetic note.

### Stage 17 RX pass-marker regex

The original SOCKS Stage 17 UART parser used
`JESD RX \(JTX\) Link2 .* in DATA` (requires whitespace then text between
`Link2` and `in DATA`). Real ZCU102 + AD9986 UART output emits
`JESD RX (JTX) Link2 in DATA, ...` — no token between `Link2` and `in`. The
fix is `JESD RX \(JTX\) Link2.* in DATA` (drop the explicit space). Without
this, Stage 17 reports a false negative on a link that is actually in DATA.

This change applies to whatever stage script or scenario manifest carries
the per-link pass markers; update it once and re-run.

### AD9986-FMCA power preflight

If the AD9986-FMCA daughtercard is not powered, Stage 17 fails *before* JESD
setup with symptoms that look like SPI / register-access errors:

- HMC7044 readback warnings
- AD9986 readback returns `0xff`
- `ad9081_init` returns `-50`

This is *not* a GT, CDR, or JESD problem and should be ruled out first. Add an
explicit AD9986 power check (or a board-level visual check) to the Stage 0
preflight when running an unfamiliar profile or a freshly re-cabled board.

### ZCU102 ethernet MAC + Vivado license setup

Two environment-level preflight items can silently block Stage 14 or Stage 17:

- ZCU102 ethernet MAC must be set before Vivado license checkout / network
  reach. The plan-03d session used:

  ```bash
  sudo /usr/bin/ip link set dev enp4s0 address A4:2B:B0:E[6]:8C:2E
  ```

  (Character class on `E[6]` is the fingerprint-guard idiom; the literal byte
  is `E6`. Substitute your own host's NIC name and licensed MAC.)

- Vivado must be able to check out the Synthesis license for the target part
  (e.g. `xczu9eg` on ZCU102). Plan-03d hit two Stage 14 license-checkout
  failures that resolved on retry; if these recur, check
  `LM_LICENSE_FILE` / `XILINXD_LICENSE_FILE` and the floating-license server
  reach before assuming a build problem.

### XSDB PSU/APU target visibility

A separate failure mode is Stage 17 blocking *before* programming because
XSDB can see only `PS TAP`, `PMU`, `PL`, and `DAP` (with an
`AXI AP transaction error`) and no `PSU/APU/psu_cortexa53_0`. This is a JTAG
target-visibility preflight issue, not a profile problem. The plan-03d
clean-validation Stage 0 saw the board normally a few hours later, so it
behaves as transient and not as a persistent profile blocker.

## Verification Gates

After applying a profile, verify both sides before running hardware:

```bash
python3 ~/.claude/skills/socks/scripts/hil/adi_profile_apply.py \
  --project-dir systems/zcu102-gps-streaming
```

Then inspect profile-specific facts in the live HDL and active no-OS tree.
Cross-check the live values against the Verified Profile Catalog entry for
the active profile. For example, the proven ZCU102 AD9986-FMCA
`6144-l1-clean-jesd204b-rxm8l4-txm8l8` profile must show:

- HDL lane/refclock parameters for the 2.4576 Gbps transceiver lane profile
- `CHIPID_AD9986 0x9986`
- `clkin_freq = {122880000, 30720000, 0, 0}` (FPGA GT refclk = 122.88 MHz)
- `pll2_freq = 2949120000` (HMC7044 PLL2 = 2.94912 GHz)
- `AD9081_RX_JESD_MODE 10`
- `AD9081_TX_JESD_MODE 15`

For ADI MxFE profiles that change JESD lane rates or FPGA GT refclks, also
check the generated `util_mxfe_xcvr` GT parameters against cached GT Wizard
references before spending a hardware run:

```bash
python3 ~/.claude/skills/socks/scripts/hil/adxcvr_gt_param_check.py \
  --project-dir systems/zcu102-gps-streaming
```

Use explicit rates when evaluating a candidate profile that is not yet
materialized into the Vivado BD:

```bash
python3 ~/.claude/skills/socks/scripts/hil/adxcvr_gt_param_check.py \
  --project-dir systems/zcu102-gps-streaming \
  --rx-lane-rate 1.6384 \
  --tx-lane-rate 3.2768 \
  --refclk-mhz 204.8
```

Treat `needs per-channel/split util or lane-rate retarget` as a real HDL
architecture warning: shared CPLL fields do not support one global
`util_mxfe_xcvr` setting for the selected RX/TX rates. Treat
`likely RX CDR-only issue` as a narrower receive-side transceiver/CDR debug
item.

The end-to-end gate is real hardware validation:

```bash
python3 scripts/socks.py --validate systems/zcu102-gps-streaming
```

The pass condition is Stage 17 UART evidence from the ZCU102 plus AD9986-FMCA,
not simulation.
