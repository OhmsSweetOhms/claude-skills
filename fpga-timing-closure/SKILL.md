---
name: fpga-timing-closure
description: >-
  Close and audit Vivado timing for Zynq-7000 and Zynq UltraScale+ (ZynqMP)
  programmable-logic designs using a disciplined XDC → out-of-context (OOC) →
  full Project Implementation loop. Use this skill whenever work involves
  negative WNS/WHS, setup or hold closure, unconstrained paths, XDC ownership
  or rendering, PS/PL or generated-clock relationships, source-synchronous I/O,
  CDC max-delay or bus-skew constraints, OOC-versus-full timing differences,
  implementation directive sweeps, routed-checkpoint freshness, timing waivers,
  or proving that a routed Zynq build is release-ready. Trigger even when the
  user asks only why OOC passes but the full project fails, whether a false
  path/clock group/multicycle is safe, how to structure a Vivado timing matrix,
  or how to bank timing evidence. For the spin_dr FT600 interface, also read
  references/ft600-spin-dr.md.
compatibility: AMD/Xilinx Vivado Tcl and project-generated timing reports
---

# Zynq FPGA timing closure

## Purpose

Treat timing closure as an evidence loop, not a hunt for a green WNS number:

```text
real clock/protocol intent
        ↓
canonical XDC with exact resolved objects
        ↓
OOC architecture and physical-envelope probes
        ↓
fully routed Zynq project implementation
        ↓
independent reports + OOC/full classification
        ↓
root-cause fix, rerun, and commit-bound evidence
```

OOC proves that an architecture and its constraints are internally coherent.
The full routed project remains the release authority because it adds the real
PS/PL clock topology, clock insertion, placement competition, congestion, I/O
sites, and unrelated paths. Do not expect equal slack between the two scopes;
expect equal architecture and timing intent.

This skill covers Zynq-7000 and Zynq UltraScale+ PL implementation. It does not
replace project-specific build instructions, CDC protocol design, RTL
functional verification, boot-image generation, or hardware acceptance.

## Select project-specific references

Read a matching reference only when the project requires it:

| Project/interface | Reference |
|---|---|
| `spin_dr` FT600 FIFO interface | `references/ft600-spin-dr.md` |

If no reference exists, use the generic workflow and capture verified
project-specific facts in a new reference only with user approval. Never move
one board's pins, clock phases, pblocks, or warning counts into the generic
workflow.

## Establish the exact build under test

Before interpreting a report or launching Vivado, identify:

1. repository and project root;
2. exact git commit and working-tree state;
3. Zynq family, device part, package, and speed grade;
4. Vivado version required by the project;
5. authoritative build entry point;
6. top-level design and block-design hierarchy;
7. supported clock/rate modes;
8. canonical XDC sources and their processing order; and
9. locations of the routed checkpoint and generated reports.

Read applicable project guidance and the full source/XDC files before editing.
Map which scripts render or consume every constraint file. A generated XDC is
not an independent source of truth: trace it back to the canonical template and
the renderer that supplies hierarchy or mode substitutions.

If the user asks why a decision was made, diagnose and explain first. Do not
silently rewrite constraints.

## Start with cheap desk checks

Use code, configuration, and existing artifacts to eliminate hypotheses before
another implementation run:

- diff the passing OOC and failing full XDC, modes, phases, and directives;
- inspect the real RTL source and destination structures;
- compare raw timing-path source, destination, clocks, edges, requirements,
  exceptions, logic/route split, fanout, and primitive chain;
- inspect `report_exceptions`, `report_clock_interaction`, `report_cdc`, and
  `report_bus_skew`, not only `report_timing_summary`;
- verify every constraint query resolves to the expected nonzero, exact count;
- check whether the report or checkpoint predates the build being claimed; and
- separate a genuine violation from a stale checkpoint, wrong mode, wrong
  hierarchy render, or parser error.

A rerun is justified after the current evidence cannot answer the question.

## Map the Zynq clock topology

Build a clock table before authoring exceptions:

| Clock | Source | Frequency/waveform | Consumers | Relationship |
|---|---|---|---|---|
| PS fabric clock | PS FCLK/PL clock output | project value | PL logic | primary or BD-owned |
| External PL clock | package pin/buffer | measured contract | I/O/PL logic | primary |
| Generated clock | MMCM/PLL/buffer/divider | derived edges | destination domain | related |
| Virtual I/O clock | timing model only | device contract | input/output delays | external |

Zynq-specific rules:

- A block design may already create PS fabric clocks. Do not add a duplicate
  `create_clock`; prove ownership from the implemented design.
- Treat clock-wizard/MMCM/PLL outputs as generated clocks with their real
  waveform and phase. Do not convert a known relationship into asynchronous
  clocks because timing is difficult.
- Do not assume a Zynq-7000 primitive, bank, or clock route exists on ZynqMP,
  or vice versa. Inspect the actual part and synthesized primitives.
- Keep the debug hub on an honest, free-running PL clock. Do not use a gated or
  intermittently available interface clock as a convenience.
- Pin constraints, I/O standards, termination, and voltage are part of timing
  intent. Never inherit a pinout from a different carrier or evaluation board.

## Own and render XDC deliberately

Prefer a small set of canonical constraint sources, processed in dependency
order:

1. primary and virtual clocks;
2. generated clocks and case analysis that affects them;
3. clock groups only when the entire relationship is intentionally cut;
4. bus-skew and I/O timing assertions;
5. structure-specific false paths, max/min delays, and multicycles;
6. timing-arc disables when a specific unused primitive arc must be removed;
7. physical constraints.

Name every allowed OOC/full substitution. Typical substitutions include a
top-level port clock in OOC versus a PS/BD clock pin in full context, or an OOC
hierarchy root versus the packaged instance root. Reject:

- an empty query;
- a query whose count changes unexpectedly;
- a wildcard that selects unrelated logic;
- a generated file applied at the wrong stage;
- a constraint silently overridden by higher-precedence XDC; and
- a post-synthesis primitive constraint evaluated before those primitives
  exist.

For each constraint group, record canonical source, rendered source hash,
resolved source/destination objects, expected count, actual count, and
processing stage.

## Constrain from the transfer structure

Choose constraints after identifying how data crosses:

### Synchronous and source-synchronous I/O

- Model the external device with primary or virtual clocks and real input/output
  delay windows.
- Preserve active launch/capture edges and phase shifts.
- Report setup and hold separately for every relevant edge and endpoint class.
- For serializer/deserializer primitives, prove that only genuinely redundant
  arcs are disabled and that the intended arcs remain timed.

### Scalar synchronizers

Target the first synchronization stage or exact asynchronous control endpoint.
Prove the expected synchronizer structure and `ASYNC_REG` properties. A false
path does not make an unsafe synchronizer safe.

### Gray buses, asynchronous FIFOs, and held-data protocols

Use the protocol contract:

- apply `set_max_delay -datapath_only` when physical latency must remain
  bounded;
- apply `set_bus_skew` when bit-to-bit capture spread matters; and
- verify both with their dedicated reports.

Do not add a broad asynchronous `set_clock_groups` constraint when it would
override path-specific max-delay bounds. AMD guidance explicitly warns that
clock groups and false paths can supersede `set_max_delay`.

### Asynchronous reset

Require asynchronous assertion and synchronous local release where the design
depends on both. Constrain only the reviewed asynchronous assertion endpoints,
then prove the reset synchronizer structure and recovery/removal behavior.
Never cut an entire clock pair to hide reset topology.

### Multicycle paths

Use a multicycle only when the functional protocol guarantees the extra cycles,
not because the path is slow. Pair setup and hold constraints correctly and
verify the enabling logic. A software register that changes rarely is not
automatically a multicycle path.

## Build OOC proofs that resemble production

An OOC design should reuse production RTL, source/destination boundaries, and
canonical constraints. Test-only replicas tend to preserve names while losing
the real mux, register, reset, or primitive structure.

Define the OOC envelope deliberately:

- clock/rate modes;
- hierarchy substitutions;
- representative I/O delays;
- placement bands or distance probes;
- implementation directives; and
- exact positive and negative structural controls.

Use OOC for:

- fast constraint-resolution checks;
- primitive and source-boundary verification;
- CDC topology and exact endpoint counts;
- source-synchronous I/O modeling;
- controlled placement/distance experiments; and
- sensitivity to selected implementation directives.

Do not use OOC slack as a prediction of exact full-chip slack. Retain physical
locations and route delays so a full result can be compared to an actual OOC
member rather than a label.

## Run the full Project Implementation gate

Use the project's authoritative build entry point. A release candidate normally
progresses through:

1. package or generate the exact source/IP used by the block design;
2. synthesize the full project;
3. place and route to completion;
4. write a routed checkpoint;
5. generate the bitstream when required by the project gate; and
6. open that routed checkpoint in an independent verifier.

The verifier should emit and exact-gate:

- route status;
- DRC;
- methodology, including waived and no-waiver views;
- timing summary with unconstrained-path checks;
- worst setup and hold paths;
- interface-specific setup and hold paths;
- CDC details;
- clock interaction;
- bus skew;
- applied exceptions/constraints; and
- normalized path-family signatures.

Do not trust the build's return code alone. Require a routed checkpoint whose
modification time is at or after the captured build-start time. Comparing the
checkpoint with the final log time is wrong because the log normally closes
after the checkpoint is written.

## Compare OOC and full path families

Normalize each release-relevant family by architectural identity, not generated
hierarchy text. Capture:

- member ID and cardinality;
- source and destination boundary;
- source and destination clocks;
- setup/hold type and active edges;
- requirement;
- exception class;
- logic-level/primitive structure; and
- physical site, route, insertion, skew, and slack as non-equality evidence.

Require exact equality for timing intent and structure. Permit expected
physical differences. Classify every mismatch before changing the design:

Never subtract headline OOC WNS from headline full-project WNS and call the
difference an integration penalty unless both values belong to the same
normalized member with the same clocks, edges, and timing requirement. The two
headlines often name different worst paths.

| Classification | Meaning | Corrective direction |
|---|---|---|
| `physical_route_or_clock_tree_delta` | Same architecture; placement/route/skew differs | Improve or deliberately constrain physical implementation |
| `source_boundary_mismatch` | Extra mux/decode or missing register in one scope | Correct production and OOC boundary architecture |
| `constraint_rendering_mismatch` | Clock, edge, requirement, exception, or object set differs | Fix canonical ownership/rendering |
| `ooc_abstraction_gap` | Full member has no production-faithful OOC counterpart | Extend OOC using shared production structure |
| `genuine_full_only_synchronous_path` | Architecturally appropriate full-only path | Close it in the full design |
| `context_pruned_in_full` | OOC member is provably unused in full context | Record pruning proof; retain coverage of every full member |
| `unsupported_stress_failure` | Failure occurs only in an unsupported stress mode | Retain as non-blocking evidence |

For physical-distance comparisons, select the closest matching OOC member using
a deterministic distance metric and tie-break. Do not require the full site to
fall inside an OOC pblock unless that containment is itself a documented design
requirement.

## Choose the fix from evidence

Apply the smallest root-cause correction:

1. fix missing/incorrect clocks or canonical XDC rendering;
2. fix CDC/reset/source-boundary architecture;
3. fix synchronous RTL depth, fanout, or pipelining;
4. make OOC physical assumptions representative;
5. improve deliberate full placement/floorplanning; then
6. try a reviewed implementation directive for a genuinely physical problem.

Do not reach first for:

- a broad false path or clock group;
- a multicycle with no protocol proof;
- relaxed I/O delay or clock phase;
- a waiver;
- a high-effort directive; or
- a pblock that merely moves the current worst path elsewhere.

After an RTL latency change, update functional models, valid/ready pipelines,
counters, and tests before accepting timing.

## Run a controlled matrix

Define the matrix in project data rather than an ad hoc shell loop. Keep:

- a canonical directive order supported by the pinned Vivado version;
- required hardware modes separate from unsupported stress modes;
- physical-distance sweeps separate from directive sweeps to avoid an
  unnecessary Cartesian product; and
- release gates distinct from non-blocking evidence.

Stop on the first failed gate and retain its checkpoint, reports, rendered XDC,
logs, and signature. Diagnose before continuing.

Maintain this table while runs execute:

| Mode/directive | Build | Setup WNS | Hold WHS | Interface margins | Boundary/route | Verification | Note |
|---|---:|---:|---:|---|---|---:|---|
| `<mode>/<directive>` | Pass/Fail | `<ns>` | `<ns>` | `<setup/hold>` | `<sites/delays>` | Pass/Fail | `<classification>` |

Use final independent-verifier values in the table. Label router estimates as
estimates when reporting progress.

## Bind evidence to immutable source

Before a release matrix:

- require a clean source tree;
- record the full source commit;
- hash canonical constraints, renderers, verifiers, and production RTL;
- capture build start time before launching the build;
- archive the routed checkpoint and exact reports per run;
- write a current-source marker only after all gates pass; and
- generate a machine-validated JSON manifest.

Resume only artifacts carrying the exact current source marker. If verifier-only
source changes after a build, preserve mixed provenance explicitly or reverify
archived checkpoints through a reviewed mechanism. Never relabel old evidence
as current by rewriting markers.

## Release and stopping-point criteria

A supported full-system run passes only when the project-defined gates pass,
including at minimum:

- non-negative setup and hold slack;
- zero failed/unrouted/overlapping nets;
- no unconstrained internal endpoints;
- exact interface timing checks;
- accepted CDC and clock interactions;
- passing bus-skew assertions;
- accepted DRC/methodology findings;
- explicit zero or reviewed waiver inventory; and
- a normalized signature matching OOC architectural intent.

If the user stops early, bank a handoff that states:

- completed, failed, and unrun rows;
- exact final timing numbers;
- physical excursions and classifications;
- constraint and waiver inventory;
- source commits and mixed provenance;
- retained evidence locations; and
- what remains before release.

Never call a partial matrix released.

## Authoritative AMD references

Consult the versions matching the project's pinned Vivado release:

- UG903, *Vivado Design Suite User Guide: Using Constraints*
- UG906, *Vivado Design Suite User Guide: Design Analysis and Closure
  Techniques*
- UG949, *Vivado Design Suite User Guide: Design Methodology*

## Relationship to other skills

- Use `socks` to author or change RTL, XDC, block designs, and testbenches.
- Use `fpga-datapath-map` first when the symptom is a streaming
  handshake/reset/re-arm wedge rather than timing.
- Use `control-loops` for PLL/DPLL loop mathematics or fixed-point control
  design.
- Use `zynq-boot` after implementation when the task is packaging or flashing a
  Zynq boot image.
