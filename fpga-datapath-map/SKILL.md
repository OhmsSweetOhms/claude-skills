---
name: fpga-datapath-map
description: >-
  Localize an FPGA/RTL streaming-datapath wedge by statically mapping its
  handshake (valid/ready backpressure), reset, and re-arm/acceptance signal
  network across the block design + RTL, then naming a root-cause hypothesis
  with exact net names + file:line and a cheap confirm/kill test. Use this
  WHENEVER a streaming datapath stalls, drops, or won't re-arm — symptoms like
  a stuck `tready`/`tvalid`/`s_axis_ready`, "0 bytes reach DDR/memory/host", a
  DMA or data-offload that won't accept on the 2nd/repeated run, silent sample
  drops, a capture that only works after a full reboot, or "why does data stop
  at block X." Also trigger when asked to map/trace the handshake or reset
  network of a block design, audit backpressure, or figure out where in an
  AXIS/AXI-Stream chain data is being lost or blocked — even if the user says
  "trace the signals" or "map the resets" without naming a bug. Prefer this over
  ad-hoc grepping whenever the question is "where in the datapath does flow stop
  and why."
---

# FPGA datapath signal map

## What this is for

A streaming datapath (ADC/source → filters → packers → FIFOs → DMA/offload →
memory/host) wedges: data stops, drops, or won't re-arm. The bug is almost never
where the symptom shows — a `tready` stuck low at block X is usually *caused* by
a reset that never reached block Y, or an FSM parked waiting on an event that
never fires. Grepping around burns time and misses it. This skill is a
disciplined **static trace** that maps the three networks that govern flow —
**handshake/backpressure, reset, and re-arm/acceptance** — pins the stuck signal
to the exact RTL that generates it, and hands you a root-cause hypothesis plus a
cheap on-silicon confirm test.

It is a **read-only reconnaissance** method. It changes nothing; it produces a
map and a hypothesis. The fix is a separate step, gated on the confirm test —
because a confident static-trace hypothesis that skips hardware confirmation is
exactly how you burn a synth cycle building the wrong fix.

## When to reach for it

- A specific handshake signal is stuck (`tready`/`tvalid`/`ready`/`valid` pinned
  0 or 1) and you need to know *what gates it*.
- "0 MiB / 0 bytes reached memory" while the source is clearly producing.
- A DMA / data-offload / consumer accepts on the first run but wedges on the
  2nd+ (repeated-capture / warm-restart / re-arm failure), and only a full
  reboot clears it.
- Silent sample drops with no overflow flag firing.
- You're about to send an agent to "map the handshaking and reset signals" of a
  block design — this IS that job, made rigorous.

## How to run it

**Default to delegating this to a read-only recon subagent** (a cheaper model is
fine — the work is careful reading, not judgment), so the raw file-reading stays
out of the main context and you get back conclusions. Give the subagent the
prompt template in `references/subagent-prompt.md`, filled with your specifics.
Run it inline only for a small, single-block trace.

**Anchor the trace to the exact artifact under test.** Identify the git commit /
build the *failing* image was produced from and trace at that commit — not `main`,
not "roughly." A datapath map of the wrong revision is worse than none: it reads
authoritative and points you wrong. State the commit in the output.

**Report conclusions with exact net names and `file:line`, not file dumps.** The
value is a map someone can act on: "`offload_sready = wr_ready`, high only in
`WR_STATE_WR` (`data_offload_fsm.v:280`)", not a paste of the FSM.

## The method — map three networks, then converge

Work these in order. Each narrows the suspect set for the next.

### 1. Handshake / backpressure chain

For every producer→consumer stage in the datapath, record: the driver
`valid`/`wr_en` net, the consumer `ready`/`tready` net, and — the load-bearing
column — **does the consumer provide REAL backpressure, or is its ready tied
high / free-running / absent entirely?** A stage with no honored ready is a
**silent drop point**: when its downstream backs up, it discards data instead of
stalling, and usually raises no flag. Free-running FIR `m_axis_tready` tied VCC,
or a packer with no `tready` input port at all, are classic examples. Map the
whole chain so you know which stalls propagate upstream and which just drop.

### 2. Trace the stuck signal into the RTL

Take the actually-stuck net and trace it *inward* to the boolean/FSM state that
generates it. Land on a precise statement of the form: **"signal S is asserted
iff the FSM is in state X; to reach/hold X requires events Y and Z."** Then find
what produces Y and Z. This converts "tready is low" into "the write FSM is
parked in WAIT_RD because the read-side drain event never crossed back" — an
actionable cause, not a symptom.

### 3. Reset network — and the question that cracks re-arm bugs

Map every reset in the datapath: **source → gate/logic → sinks**, and classify
each as **per-transaction/per-run** (pulses each capture/arm) or
**power-on/global only** (toggles only on a full chip/PS reset). Then ask the
question that finds most re-arm and warm-restart wedges:

> **Does the runtime/software reset actually reach every stateful element — or
> is some internal state (a storage RAM, a request counter, an FSM sub-state)
> scoped to a reset the runtime never pulses?**

The killer pattern: the software "reset" register clears the *controller* FSM but
a sibling block's internal RAM/counter is wired only to the power-on reset. That
stale state survives every software reset and every firmware/driver reload, and
gets cleared *only by a physical reboot* — which is exactly the "works after
reboot, wedges on warm restart" fingerprint. When you see that fingerprint, hunt
for a stateful element outside the software-reset domain.

### 4. Re-arm / acceptance trigger chain

For a "won't accept the next transaction" bug, map what must happen for the
consumer to accept again: the drain/EOT event, the transfer-request / `init_req`,
the outstanding-count return to zero, the reset. State explicitly whether the
runtime reset forces the accepting state **unconditionally**, or only clears part
of the machine while some gating counter persists (per §3).

### 5. Multi-instance coupling

If the datapath has parallel instances (two rings, N channels), determine whether
they are **independent IP instances** (separate bases/memories/resets) or **share
a resource/arbiter/reset**. This decides whether a defect hits one or all at once,
and whether a sequenced/interleaved run can deadlock one instance on another. "All
instances wedge simultaneously with no cross-coupling" points at a shared
per-instance *defect*, not contention.

### 6. Provenance check

Cheaply establish whether the suspected-culprit code is *recent* or the bug
*predates* it: `git log <base>..<suspect> -- <path>` and a diff. A wedge that
predates the change everyone's blaming reframes the whole hunt — and stops you
"fixing" innocent code.

## The deliverable

Produce a structured map — full template and a worked example in
`references/deliverable-template.md`. The distilled shape:

- **A. Handshake chain** (per instance): stage → driver valid → consumer ready →
  real backpressure? (Y/N) → `file:line`. Flag every silent-drop point.
- **B. Stuck-signal dependency chain**: the stuck net → the FSM state/boolean
  that gates it → the events that release it, all with `file:line`.
- **C. Reset map**: each reset → source→gate→sinks → per-run vs power-on-only.
- **D. Re-arm trigger chain**: what must fire for the next transaction; does the
  runtime reset clear it fully?
- **E. Multi-instance coupling**: independent vs shared; one-or-all; deadlock?
- **F. Provenance**: did the suspect change, or does the bug predate it?
- **G. Root-cause hypothesis + CONFIRM/KILL test**: the single most likely cause,
  and a *cheap* test that proves or kills it — ideally by probing signals that
  are **already instrumented** (`mark_debug`/existing ILA/existing counters)
  before adding new ones. State what a CONFIRM looks like and what a KILL looks
  like.

## Two disciplines that make it trustworthy

- **A static-trace root cause is a hypothesis, not a fact — say so.** Rank it by
  confidence, and always pair it with the confirm/kill test. The failure mode to
  avoid is the confident-but-wrong trace (e.g. blaming a status flag that turns
  out to be architecturally dead / unconnected). Verify the deciding signal on
  hardware before anyone builds the fix.
- **Prefer confirming with signals that already exist.** Many designs already
  carry `mark_debug` taps or debug counters that never made it into the active
  probe list. Reusing them turns "confirm the hypothesis" from a rebuild into an
  attach-only capture. Call those out explicitly.

## Scope

This maps and hypothesizes; it does not edit RTL or run builds. It is
provider-neutral (AXI-Stream valid/ready is the common case, but the same
handshake/reset/re-arm logic applies to any streaming interface). For authoring
RTL, AXI wrappers, testbenches, or running synthesis, that's the `socks` skill's
job; for loop-filter/NCO control math, `control-loops`. This skill is the
debugging lens you run *first*, to point those at the right block.
