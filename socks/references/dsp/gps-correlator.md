# GPS/GNSS Correlator DSP Reference

Read this after `references/dsp/fixed-point-vhdl.md` when the block is a GPS or
GNSS correlator: carrier wipeoff, PRN/code replica generation, early/prompt/late
accumulation, integrate-and-dump, channel scheduler, or correlator dump readback.

This file is for correlator-specific contracts. General fixed-point, CDC,
resource, and timing rules stay in `fixed-point-vhdl.md`.

---

## Stage 1 Correlator Contract

Capture these before RTL:

1. Signal and code family: GPS L1 C/A, L1C, L2C, L5, or other. State which are
   in scope and which are explicitly out of scope.
2. PRN support: all PRNs, a committed single-PRN vector, ROM source, generator
   polynomial, tap convention, and code phase reset behavior.
3. Input stream contract: sample rate, IF/baseband convention, I/Q order,
   signedness, width, sign extension, `tlast`, and epoch/sample framing.
4. Carrier NCO convention: phase width, phase increment units, initial phase,
   sine/cosine lookup or approximation, quadrant/sign convention, and wipeoff
   multiply signs.
5. Code NCO convention: chip phase width, code frequency units, initial code
   phase, wrap behavior, and sample-to-chip update order.
6. Correlator tap positions: early/prompt/late spacing, direct chip-index helper
   behavior, wrap at code epoch, and whether fractional-chip interpolation is in
   scope.
7. Accumulator contract: E/P/L I/Q accumulator widths, integration interval,
   coherent/noncoherent behavior, clear timing, and overflow/saturation/wrap.
8. Dump format: word order, signed width, metadata fields, epoch/channel fields,
   FIFO depth, overflow/drop reporting, and readback protocol.
9. Channel config and scheduler: active mask, PRN, carrier/code frequencies,
   initial phases, channel update order, lane count, and dump order.
10. Acceptance authority: committed fixed-point vector bundle, config JSON,
    expected dumps, and `max_abs_lsb` tolerance.

If the plan says Step C, all-PRN support, L1C, bit-edge histograms, or 12-channel
hardware are out of scope, keep them out of the gate and list them as residual
scope.

---

## Fixed-Point Golden Discipline

For correlators, the float arm is useful for intuition but not a bit-exact gate
once a fixed-point golden exists. Use the committed integer vector bundle.

A good Gate-A bundle has:

- `input_iq.mem` or equivalent sample stream;
- config JSON with channel initialization;
- expected E/P/L dump CSV;
- generator metadata naming the fixed-point model commit;
- explicit `max_abs_lsb=0` or other tolerance.

The RTL testbench should drive the committed samples through the real stream
interface and compare emitted dump records field-by-field. Do not compare only
P prompt magnitude or only dump count.

---

## Carrier, Code, And Tap Ordering

Most correlator bugs come from update order, not from the multiply itself.
Document and test these orders:

- whether the current sample uses the old or incremented carrier phase;
- whether code phase advances before or after tap lookup;
- how E/P/L chip indices are derived near code wrap;
- whether epoch completion is detected before or after the final sample is
  accumulated;
- whether dumps contain pre-clear or post-clear accumulator values;
- whether config writes take effect immediately, at next channel visit, or at
  an epoch boundary.

Use small directed tests or fixed-vector traces that expose wrap and epoch edges.
A full-ms vector can hide a one-sample ordering error until late in the thread.

---

## Channel Scheduler And Lane Count

Keep lane-count generalization separate from channel feature scope.

Recommended proof split:

- `N=1` for hardware timing and hardware correctness;
- `N=2` and `N=3` in xsim to prove scheduler generalization;
- expected dump ordering checked for all active channels, not just total dump
  count;
- channel state arrays updated in one controlled writeback stage when possible.

For multi-lane designs, record whether lanes process adjacent channels,
round-robin channels, or scheduler-selected active channels. The dump FIFO order
must be part of the contract.

---

## Hardware Vector Injection

Prefer a direct PS-to-correlator IQ injection seam for hardware Gate A. If the
system does not have one, any bridge path must be proven transparent for the
specific vector.

A valid bridge proof needs:

- upstream bypass settings, for example FIR/D5 bypass;
- input width conversion proof, for example IQ8 sign-extended to IQ16;
- upstream gain/bit-select proof, for example selected shift 0 for every window;
- counters proving all expected samples reached the correlator;
- hardware dump comparison against the committed expected dumps.

Do not generalize a bridge proof to future vectors unless the same transparency
condition is proven. If a future vector can change upstream scaling, add a direct
correlator injection seam or a new bridge proof.

---

## Correlator CDC Pattern

Common Zynq/AXI topology keeps AXI-Lite/readback at 100 MHz while the sample
path runs faster. For a correlator, audit these specific crossings:

- channel config arrays from AXI to sample/scheduler domain;
- sample, raw-accept, epoch, dump, overflow, and schedule status back to AXI;
- dump FIFO payloads from sample domain to AXI readback;
- debug/monitor ports that might later feed telemetry.

Use the general patterns in `fixed-point-vhdl.md`: frozen config payload plus
request/ack, sample-domain status snapshot into AXI, and a real async FIFO for
dumps. Run xsim with the same clock ratio as hardware, for example 200 MHz
sample clock and 100 MHz AXI clock.

---

## Timing And Resource Notes For Correlators

Correlators often have many narrow operations and state-array updates. Avoid
forcing the whole architecture into DSPs.

Patterns that helped in the PL.B3 thread:

- direct E/L chip-index helpers instead of wide generalized arithmetic in the
  hot path;
- lane-parameterized prepare/writeback stage;
- accumulator and dump writeback retiming at epoch boundaries;
- OOC 5 ns timing before integrated system timing;
- integrated Stage 14/15 because the true 200 MHz failure can move into an
  upstream selector or power-estimation block.

If integrated timing fails in an upstream block, fix the upstream RTL root cause.
Do not constrain around a real `FIFO -> power -> threshold -> selected_shift`
style chain.

---

## Acceptance Gate Template

Use explicit gates in the handback:

| Gate | Evidence |
|---|---|
| Simulation bit-exact | xsim N=1, committed IQ vector, committed expected dump CSV, `max_abs_lsb=0`. |
| Scheduler generalization | xsim N=2 and N=3 with all expected channel dumps checked. |
| Timing/resource | OOC timing/resource plus integrated Stage 14/15 routed timing and report paths. |
| Hardware correctness | A53/R5 or equivalent hardware checker drains all expected dumps and reports `max_abs_lsb=0`. |
| Residual scope | PRNs/code families/histograms/channel counts/injection seams not proven by this gate. |

The handback should say which proof is simulation-only, which proof is hardware,
and whether the hardware proof predates any later CDC/timing changes.
