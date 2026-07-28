# spin_dr FT600 timing-closure reference

Read this reference only for the `spin_dr` FT600 interface or when using that
work as an explicitly labelled case study. The values below are project facts,
not generic Zynq defaults.

## Architecture

- Device: Zynq UltraScale+ MPSoC, `xczu2cg-sfvc784-1-i`.
- Interface: FT600 16-bit FIFO bus, HP bank 66, LVCMOS18.
- External clock: `ft600_clk` on E5.
- Supported hardware mode: 15.000 ns period (`hw66`).
- Hardware launch phase: MMCM waveform phase 186 degrees, producing a
  7.750 ns launch edge relative to the package-clock rising edge.
- Non-blocking desk-stress mode: 10.000 ns period and 189-degree launch phase.
  This does not qualify 100 MHz operation on the 1.8 V hardware.
- Physical-I/O layer: `ft600_ooc/rtl/ft600_io.vhd`.
- Integrated wrapper: `ft600_stream_top`.
- Registered receive-to-launch boundary: 22 registers.

The exact package map is owned by
`ft600_ooc/xdc/ft600_io_ooc.xdc`. Do not reproduce its pins in the generic
skill or infer them from another carrier.

## Canonical constraint ownership

The FT600 timing flow has three canonical constraint sources:

1. `ft600_ooc/xdc/ft600_io_ooc.xdc`
2. `ft600_ooc/xdc/ft600_io_ooc_post_synth.xdc`
3. `ft600_cdc/xdc/ft600_stream_cdc.xdc`

The full build renders their named OOC/full hierarchy substitutions. The
integrated verifier reapplies the post-synthesis SelectIO arc constraints after
opening the routed checkpoint because not every disabled primitive arc is
serialized into the checkpoint.

The full verifier proves:

- 24 FT600 package ports and their exact pin/I/O-standard map;
- one launch `MMCME4_ADV` with `PHASESHIFT_MODE=WAVEFORM`;
- 18 data/byte-enable `IDDRE1`;
- two registered status inputs;
- 19 merged `OSERDESE3`;
- 22 registered launch-boundary registers;
- 37 disabled redundant serializer falling arcs while rising arcs remain timed;
- eight datapath-only maximum-delay constraints; and
- seven bus-skew groups.

## CDC and exception inventory

The corrected architecture uses registered production source boundaries for
the FIFO, mailbox, scalar/toggle, held-data, and reset structures.

The current exception inventory is exactly five structure-specific
`set_false_path` commands:

1. reset acknowledgement;
2. launch platform reset;
3. coordinated launch reset;
4. receive local reset; and
5. extension scalars.

There are:

- no broad clock groups;
- no multicycle paths;
- zero methodology waivers; and
- no nominal false paths from the MMCM `LOCKED` output.

The MMCM lock endpoints form a three-member structural family. The verifier
requires exact fan-in and synchronizer properties, with no valid nominal timing
exception.

Accepted full CDC evidence contains:

- `CDC-6=4`;
- `CDC-15=823`;
- no critical CDC row;
- no `CDC-26`, `CDC-7`, or `CDC-10`; and
- no path whose exception class is `None`.

## Required matrix

The intended positive matrix has 25 unique runs:

| Scope | Mode | Sweep | Runs | Disposition |
|---|---|---|---:|---|
| Physical-I/O OOC | `hw66` | five directives in `full_system_envelope` | 5 | Required |
| Physical-I/O OOC distance | `hw66` | `Default` in near/integrated/far bands | 3 | Required |
| Physical-I/O OOC | `stress100` | five directives in `full_system_envelope` | 5 | Non-blocking |
| CDC OOC | both modes | fixed runner | 2 | Required support |
| Full project | `hw66` | five directives | 5 | Release gate |
| Full project | `stress100` | five directives | 5 | Non-blocking |

The five directive labels used by the pinned Vivado 2021.1 flow are:

1. `Default`
2. `Explore`
3. `ExtraNetDelay_high`
4. `AltSpreadLogic_high`
5. `ExtraTimingOpt`

The physical OOC matrix separates directive diversity from the three-band
distance sweep. Do not expand it into a five-by-four Cartesian product.

## OOC placement bands

The `hw66/Default` distance probes are:

| Band | Allowed slices | Representative actual placement | Fast-min / slow-max |
|---|---|---|---:|
| `bank66_near` | X44:45, Y136:159 | X44:45, Y153:159 | 0.229 / 0.880 ns |
| `bank66_integrated` | X38:45, Y136:159 | X44:45, Y153:159 | 0.229 / 0.880 ns |
| `bank66_far` | X30:37, Y136:159 | X37, Y152:159 | 0.338 / 1.335 ns |
| `full_system_envelope` | X35:43, Y137:162 | X42:43, Y150:162 | 0.242 / 0.977 ns |

These are probes, not a requirement that every full-project register remain
inside their union. Full placement may differ; compare the matching member to
the closest OOC member using Manhattan distance and a deterministic lexical
`run_id` tie-break.

## Four banked hardware runs

The July 2026 stopping point completed four of the five full `hw66` directives:

| Directive | Global setup / hold | Input setup / hold | Output setup / hold | Boundary | Fast-min / slow-max | Note |
|---|---:|---:|---:|---|---:|---|
| `Default` | +1.619 / +0.013 ns | +2.586 / +1.572 ns | +2.425 / +2.514 ns | X37:44, Y148:153 | 0.291 / 1.166 ns | Pass |
| `Explore` | +1.619 / +0.013 ns | +2.586 / +1.572 ns | +2.425 / +2.514 ns | X37:44, Y148:153 | 0.291 / 1.166 ns | Pass |
| `ExtraNetDelay_high` | +1.644 / +0.010 ns | +2.586 / +1.572 ns | +2.425 / +2.514 ns | X42:46, Y141:145 | 0.273 / 1.194 ns | Pass; X46 excursion |
| `AltSpreadLogic_high` | +1.660 / +0.012 ns | +2.586 / +1.572 ns | +2.425 / +2.514 ns | X38:44, Y149:152 | 0.273 / 1.084 ns | Pass |
| `ExtraTimingOpt` | not run | not run | not run | not run | not run | Stopped by user |

Use independent-verifier values in the final table. During the
`ExtraNetDelay_high` run, the router estimated +1.630 ns setup; after the
canonical post-synthesis arcs were reapplied, the verifier reported +1.644 ns.

## X46 excursion lesson

`ExtraNetDelay_high` placed 21 of 22 launch-boundary registers inside the OOC
band union and one at `SLICE_X46Y144`, one slice beyond X45. Global routed
timing and all detailed FT600 I/O timing checks passed. The route-delay range,
0.273–1.194 ns, remained inside the distance-sweep range.

The first verifier incorrectly failed because it required every full register
to lie inside an OOC pblock. That contradicted the parity rule allowing
placement differences. The correction:

- still requires all four exact OOC band summaries;
- still records all full register LOC/BEL values and the bounding box;
- removes only pblock-union containment as a pass condition; and
- leaves timing, CDC, clock interaction, bus skew, DRC, methodology, route, and
  signature gates unchanged.

General lesson: an OOC physical envelope is a comparator set, not automatically
a full-project floorplan. If containment is desired, state it as an explicit
design requirement and constrain it deliberately.

## Checkpoint freshness lesson

The matrix originally compared routed-checkpoint modification time with the
final build-log modification time. This always fails because the log closes
after the checkpoint is written.

The correct proof captures `build_started_ns` immediately before launching the
build and requires:

```text
routed_checkpoint.mtime_ns >= build_started_ns
```

Also require the expected checkpoint to exist. A successful build return code
without a current routed checkpoint is a failure.

## Signature and manifest rules

OOC and full implementations export 19 normalized path families. Architectural
fields compare exactly:

- member identity/cardinality;
- clocks and edges;
- requirements;
- exception class;
- source-boundary register presence; and
- primitive/logic structure.

Placement, insertion, skew, route delay, and slack are retained but do not
compare for equality.

The manifest must select and label the minimum-Manhattan OOC comparator for
each registered-boundary member, including the lexical `run_id` tie-break.
Recording every distance row without identifying the selected comparator is
incomplete.

Every run is bound to an immutable source commit and hashes of canonical
constraints, renderers, verifier, and production RTL. Write the run's
`source_commit.txt` only after its complete gate passes. A dirty tree or stale
marker cannot be resumed as release evidence.

## Current stopping-point caveat

The retained stopping point contains:

- all 13 physical OOC runs passed;
- both CDC OOC modes passed;
- four full `hw66` directives passed;
- `ExtraTimingOpt` not run; and
- all full `stress100` directives not run.

The verifier changed after some artifacts were built. The handoff therefore
records mixed provenance and does not claim an exact-current 25-run manifest.
Resume through reviewed provenance or rerun against one immutable source
commit; do not rewrite old markers.
