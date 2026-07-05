# Recon-subagent prompt template

Fill the `<...>` placeholders and hand this to a read-only recon subagent (a
cheaper model is fine). Keep it read-only — the agent maps, it does not edit.

---

You are doing read-only reconnaissance on an FPGA design to localize a datapath
wedge. Report **conclusions with exact net names and `file:line` anchors** — not
file dumps. Use repo-relative paths only (never absolute paths or usernames). Do
NOT edit anything.

**Repo / worktree:** `<repo-or-worktree>`. **Trace at commit `<SHA>`** — the exact
build the failing image was produced from (the current HEAD / working tree is at
it). A map of the wrong revision points wrong.

**Datapath under test:** `<source → … → sink chain, e.g. ADC → JESD → FIR →
cpack → FIFO → data_offload → DMA → DDR>`, `<N>` parallel instances
(`<instance names>`).

**Symptom you are mapping toward:** `<the exact stuck/dropped/no-data symptom +
any ILA evidence, e.g. "on a 2nd run without reboot, consumer s_axis_tready=0 the
whole active window while upstream ready is open and data is offered; 0 bytes
reach memory; only a reboot clears it">`.

**Key files** (read what applies; grep for the rest):
- Block design / wiring: `<bd tcl / top>`
- The wedged consumer + its FSM + internal storage: `<e.g. data_offload.v,
  data_offload_fsm.v, util_do_ram.v>`
- Upstream producers / packers / FIFOs / filters: `<...>`
- The DMA / mover: `<...>`
- Runtime/firmware that arms & resets: `<e.g. capture_main.c, the driver>`
- Any control/release/reset-gating block: `<...>`

Deliver these sections, each with exact nets + `file:line` (see the skill's
`references/deliverable-template.md` for the shape):

- **A. Handshake/backpressure chain**, per instance: stage → driver valid/wr_en →
  consumer ready → REAL backpressure? (Y/N) → `file:line`. Flag every point where
  ready is tied high / free-running / has no port (silent-drop points).
- **B. The stuck-signal dependency chain**: trace `<the stuck net>` INTO the RTL —
  what FSM state/boolean generates it, which state must hold for it to assert,
  and the exact event that releases the parked state. State it as "S is high iff
  FSM in state X; to reach X requires events Y,Z."
- **C. Reset network map**: every reset in the datapath — source → gate/logic →
  sinks — each classified **per-run/per-transaction** vs **power-on/global only**.
- **D. Re-arm/acceptance trigger chain**: what must fire for the consumer to
  accept the next transaction. **Does the runtime/software reset force the
  accepting state unconditionally, or does some internal RAM/counter/sub-state
  sit OUTSIDE the software-reset domain and survive it?** (This is usually the
  crux of a re-arm/warm-restart wedge.)
- **E. Multi-instance coupling**: are the instances independent (separate
  bases/memories/resets) or shared? Does the defect hit one or all? Any
  cross-instance deadlock on a sequenced/interleaved run?
- **F. Provenance**: `git log <base>..<SHA> -- <suspect paths>` — did the
  suspected culprit change, or does the wedge predate it?
- **G. Root-cause hypothesis + CONFIRM/KILL test**: the single most likely cause,
  ranked by confidence, and a CHEAP confirm test — prefer probing signals that
  are **already `mark_debug`/instrumented** but not in the active probe list over
  adding new ones. Say explicitly what a CONFIRM vs a KILL looks like.

Be specific and grounded — exact signal names, states, `file:line`. Where you
must infer a net you cannot fully trace, say so and give your confidence. Do not
present a static-trace inference as a proven fact.
