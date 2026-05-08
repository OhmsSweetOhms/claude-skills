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

## Verification Gates

After applying a profile, verify both sides before running hardware:

```bash
python3 ~/.claude/skills/socks/scripts/hil/adi_profile_apply.py \
  --project-dir systems/zcu102-gps-streaming
```

Then inspect profile-specific facts in the live HDL and active no-OS tree. For
the proven ZCU102 AD9986-FMCA `6144-jesd204b-m8l4` profile, expected facts
include:

- HDL lane/refclock parameters for the 2457.6 MHz transceiver lane profile
- `CHIPID_AD9986 0x9986`
- `clkin_freq = {122880000, 30720000, 0, 0}`
- `pll2_freq = 2949120000`
- `AD9081_TX_JESD_MODE 9`
- `AD9081_RX_JESD_MODE 10`

The end-to-end gate is real hardware validation:

```bash
python3 scripts/socks.py --validate systems/zcu102-gps-streaming
```

The pass condition is Stage 17 UART evidence from the ZCU102 plus AD9986-FMCA,
not simulation.
