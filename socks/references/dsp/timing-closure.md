# Timing Closure Reference for Authored DSP Datapaths

Read this when a synthesis timing gate fails on an authored VHDL DSP
datapath, or before starting retime work on one. It pairs with
`references/dsp/fixed-point-vhdl.md` (bit-exact gating discipline) and
the threads-skill handback contract's "Synthesis-gate evidence" section
(the `.dcp` + `failing_paths.txt` rules).

Keep block-specific margins and worst-path names in the project's
per-block spec/docs. Add only reusable closure lessons here.

Provenance: distilled from a five-iteration 200 MHz closure campaign on
a 12-stage SDF FFT acquisition datapath (WNS −1048 → +1.08 ns) where
every retime had to preserve bit-exactness to a Python golden.

---

## 1. Evidence before surgery

- **Never scope a fix from the single worst path.** A timing summary
  shows one path; fixing it reveals the cones it was masking. Three
  consecutive iterations of the provenance campaign had the real fix
  masked behind the reported worst path.
- Extract ALL failing paths from the post-synth checkpoint
  (`get_timing_paths -max_paths 3000 -slack_lesser_than 0`) and group
  by destination register/instance family. Each family is one root
  cause; fix every family in one pass, then re-gate once.
- A huge endpoint count usually collapses to one decision. Synthesis
  absorbs output flip-flops into DSP input registers
  (`DSP_A_B_DATA`), so one combinational multiply cone replicated
  across N stage instances reports as thousands of endpoints.
- Logic-levels in the path report tell you the fix class: ~30 levels
  spanning two DSP traversals = missing pipeline stages; ~10 levels of
  LUT = a lookup/select cone that needs its own cycle.

## 2. The latency-only invariant

Under bit-exact gating, timing surgery is safe precisely because it is
**latency-only**: every retime must reproduce the simulation gates with
IDENTICAL check counts and `max_abs_lsb=0`. If a value changes, the
retime has a bug — never re-tune arithmetic, widths, or rounding to
chase timing, and never "fix" a post-retime mismatch by adjusting the
expected vectors. Identical counts across iterations are the proof the
engine that closes timing is still the engine that was verified.

Latency added in feed-forward paths is absorbed by valid-driven
counters and flush lengths. Derive the new total latency analytically,
set the flush/`FLUSH_LEN` constant from the derivation, and assert it
in the TB — do not hand-tune until it passes.

## 3. The squeeze ladder (cheapest first)

1. **BRAM output registers** — take the Synth 8-7052 hints if BRAM
   reads appear in failing families. Cheap, latency-only.
2. **Full DSP pipelining** — a combinational multiply between two
   fabric registers is never acceptable at 200 MHz+. Use a pipelined
   multiplier primitive (input regs + M + P; a Q15 complex multiply is
   a 3-stage entity) and give every bypass path a register chain of
   matched depth with a per-sample select mux.
3. **Look-ahead registration of deterministic index math** — twiddle
   indices, table addresses, and phase indices computed from a
   free-running counter are pure functions of counter state known a
   cycle early. Compute at t−1, register, consume at t. Address/index
   math must never extend into a DSP or table-lookup cone.
4. **Lookups get their own cycle** — a ROM/case-statement lookup
   (sin/cos quarter-wave, code-chip sign, constant tables) between the
   index math and the consuming multiply is a pipeline stage, not free
   logic.
5. **FSM product-capture states** — in multi-cycle FSMs, no multiply
   result ≥ 32×32 is consumed in the state that computes it: one state
   registers the product, the next consumes. Where latency is free
   (post-IRQ result paths), states are free — use them.
6. **Cone splitting / architectural change** — last resort. If the
   datapath architecture is pinned by a binding spec, a deviation that
   "passes simulation" is still a deviation (the provenance campaign's
   dominant failure was an inlined rotator that violated its own
   binding header). Hand back / escalate before restructuring.

## 4. Recurring trap: bounded-integer arithmetic

VHDL bounded-integer arithmetic with `to_integer` operands is both a
timing hazard and a synthesis crash class: integer-division nodes
(`to_integer(x) / constant`) segfaulted Vivado 2023.2's dfgOptPass1
range optimization outright. `to_integer` for array indexing is fine;
as an arithmetic operand it is not. Use vector arithmetic
(`signed`/`unsigned`), bit-serial dividers for true division, or exact
modular-inverse multiplies for divisibility-guaranteed constant
division.

## 5. OOC margin is not in-context margin

OOC synthesis-estimate timing has no routing congestion, no SLR
crossings, and no neighbors. Treat a thin positive OOC margin
(< ~0.5 ns at 5 ns clock) as a watch item, not a closed book:

- Record per-config WNS, the named worst cones, and the *unpulled
  levers* (BRAM output regs not yet added, `phys_opt_design` not yet
  run, retiming options) in the block's living spec doc, anchored to a
  commit SHA. When the integrated design gets crowded, the squeeze
  starts from that ledger instead of a fresh investigation.
- First response to in-context degradation is implementation-level
  (placement, phys-opt, the recorded unpulled levers) — RTL retiming
  of a bit-exact-gated block is the lever of last resort because it
  forces a full re-gate.

## 6. Re-gate discipline — two tiers

Timing squeezing is an inner loop; full verification is a closure
gate. Do not pay full-suite cost per squeeze attempt.

**Smoke tier (per timing-fix attempt, quick turnaround):** the
narrowest simulation set that exercises the retimed cone, plus one
end-to-end case per config as a canary. Use case-sharding plusargs
(`xsim.py --plusarg CASE_FIRST=n --plusarg CASE_LAST=m`, TB reads
`$value$plusargs`) to run only the vector cases that drive the touched
logic — e.g. a finish-FSM retime needs the bin-extreme and no-detect
cases, not the whole ladder. Then re-synth only the failing config.

**Full tier (once, before the handback / iteration close):** every
simulation suite for every config with IDENTICAL check counts (the
latency-only proof), then synthesis for all configs. A squeeze
iteration is not done until the full tier passes — smoke-tier green is
a working state, never a closure claim, and the handback must report
full-tier results only.

Independent vector cases parallelize at the process level: each
`xsim.py --work-dir` invocation is self-contained, so shard cases
across workers (compile once per config with `--compile-only`, fan out
`--sim-only` workers via `xargs -P`). The aggregate gate must require
every shard to pass AND the per-shard case counts to sum to the
expected total — a silently dead shard must read as a failure, not a
pass.

Per the handback contract, leave the `.dcp` on disk (unstaged) and
emit `failing_paths.txt` when timing fails; a root-cause proposal must
be consistent with the family histogram. If timing still fails after
the directed fixes, hand back with the histogram — do not freelance
another restructuring pass.
