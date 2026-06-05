# Dynamic Bit-Select DSP Reference

Read this after `references/dsp/fixed-point-vhdl.md` when the block performs
windowed power measurement, dynamic right-shift selection, rounding, clipping,
or byte-width quantization for a downstream DSP path. This covers PL.B1-style
bit selectors and similar adaptive fixed-point format blocks.

This file is for bit-select/quantizer-specific contracts. General fixed-point,
DSP inference, timing, lane scheduling, and CDC rules stay in
`fixed-point-vhdl.md`.

---

## Stage 1 Bit-Select Contract

Capture these before RTL:

1. Input stream contract: sample rate, I/Q packing, signedness, input container
   width, actual signal width if sign-extended, `tkeep`, `tlast`, and
   backpressure behavior.
2. Output stream contract: output container width, valid byte lanes, I/Q packing,
   output bit depths supported at runtime, `tkeep`, `tlast`, and whether
   downstream blocks expect byte-native or full-container samples.
3. Window contract: default window length, legal range, whether power windows
   align to `tlast`, whether partial windows are allowed, and when a new shift
   takes effect.
4. Power metric: `I^2 + Q^2`, absolute-value approximations, RMS proxy, or other
   metric; accumulator width; wrap/saturate behavior; and reset/clear timing.
5. Shift-selection rule: threshold tables, log formula, hysteresis if any,
   output-bit dependence, clamp range, and the source artifact used to generate
   constants.
6. Rounding and clipping: truncation, round-half-up, round-half-to-even, sign
   handling, saturation limits for each output width, and overflow counter
   semantics.
7. Buffering: whether the block must hold a full window before replaying it,
   whether ping-pong RAM is used, BRAM depth, and how output pressure stalls
   input windows.
8. Control/status: enable/reset behavior, output-bit register, window-length
   register, measured power readback, selected-shift readback, sample counter,
   overflow flag/count, and monitor ports.
9. Clocking: single-clock AXI/stream design or split control/data clocks. If the
   module is single-clock, state that system-level CDC is outside the module.
10. Acceptance authority: pinned Python/fixed-point golden, deterministic vectors,
    tolerance, and whether non-default output widths are local-only or HIL gates.

If a threshold table is valid only for one window length, say so. Do not let
firmware change `WINDOW_SAMPLES` before the thresholds are regenerated or proven
general.

---

## Golden Discipline

Bit-select blocks are deceptively simple. The gate must cover the exact rounding,
shift-selection, and clipping convention, not just output width or packet count.

A good vector proof checks:

- selected shift per completed window;
- measured power per window;
- output I/Q samples after rounding and clipping;
- saturation/overflow counts;
- `tlast` propagation and final partial-window behavior when relevant;
- runtime output widths, at least `{2,4,6,8}` when the block exposes them.

Use the pinned fixed-point/Python golden as the authority. Do not rewrite the
golden to match RTL during the hop. If the hardware proof runs through upstream
or downstream packetization, separate numeric mismatch from transport shortfall.

---

## Windowed Power And Shift Selection

For dynamic-MSB selection, write down the exact power-to-shift transform.
PL.B1 uses a windowed power sum and integer threshold tables for supported
output widths instead of evaluating a logarithm in RTL.

Design rules:

- Accumulate enough guard bits for the largest legal window and input magnitude.
- Define whether the window's selected shift is applied to the same window or to
  the next window. If the same window is replayed with the selected shift, the
  samples must be buffered.
- Threshold tables must be tied to output bit width and window length.
- Clamp selected shift to the legal range; PL.B1-style blocks commonly clamp to
  `[0, 12]` for 16-bit input containers.
- Readback should expose the last completed window power and shift, not a
  partially accumulated unstable value unless the register is documented as live.

Avoid continuous-gain language. A bit selector chooses a power-of-two shift and
clips; it is not an AGC unless the design explicitly adds closed-loop gain
control.

---

## Rounding, Saturation, And Packing

The rounding rule is part of the numerical interface. For PL.B1-style behavior,
round the magnitude with round-half-to-even, restore the sign, then saturate to
the configured output width.

Document each output width:

| Output bits | Signed range |
|---:|---:|
| 2 | `[-2, 1]` |
| 4 | `[-8, 7]` |
| 6 | `[-32, 31]` |
| 8 | `[-128, 127]` |

For byte-native output, make the packing explicit. PL.B1 packs signed int8 I/Q
into the low halfword, `I` in bits `[7:0]`, `Q` in bits `[15:8]`, clears the
upper halfword, and drives `TKEEP=0x3`.

Overflow accounting should count clipped I and Q independently if both lanes
clip in one complex sample. State whether the overflow counter is per-window,
cumulative, or cleared by software.

---

## Buffering And AXIS Backpressure

A same-window dynamic shift requires buffering: the block cannot know the shift
until the window power is complete. A common architecture is ping-pong RAM:

1. accept input samples into the write buffer while accumulating power;
2. at window completion, choose the shift and mark that buffer ready;
3. replay the ready buffer through rounding/clipping while writing the other
   buffer;
4. backpressure input if the next write buffer is still ready/full.

Record BRAM depth, word layout, and `tlast` storage. For PL.B1, each buffered
word carries the 32-bit IQ sample plus one stored `tlast` bit.

Do not hide packetizer issues inside the bit-select proof. If egress drops a
final non-full packet, record that as a system/packetizer residual, not a
bit-select arithmetic failure.

---

## Timing Closure Notes

The classic critical path is:

```text
FIFO or input register -> sample_power -> accumulator -> threshold compare -> selected_shift/ready
```

At higher clocks, retime the root path in RTL:

- register the accepted sample and metadata before sample-power work;
- register completed-window power and metadata before threshold comparison;
- delay `buf*_ready` and selected-shift decisions with the delayed metadata;
- preserve one accepted sample per clock if throughput requires it.

Do not constrain around this chain. If the design is moved from a 100 MHz clock
to a 200 MHz sample island, run both OOC timing and integrated Stage 14/15. OOC
may pass while integrated placement exposes a path launched from an upstream
FIFO or clock converter.

Resource expectations for PL.B1-style blocks:

- two 16x16 power multiplies may infer DSPs;
- ping-pong 4096-sample buffers may infer BRAM;
- shift/clip logic should not require multiplier/DSP IP;
- raw Vivado utilization is the authority, not source-code inspection.

---

## Clocking And CDC

Many bit-select blocks are simplest as single-clock modules: AXI-Lite,
AXI-Stream input, AXI-Stream output, RAM buffering, and monitor/readback all use
one `aclk`. If the surrounding system runs the stream datapath faster than the
control plane, prefer one of these explicit choices:

- keep the whole bit-select module in the faster sample island and add an
  AXI-Lite clock converter around it;
- split the RTL interface into `s_axi_aclk` and stream/sample clocks, then add
  frozen config transfer, status snapshot, and any monitor CDC deliberately;
- leave the bit-select module at the slower clock and cross the stream with an
  AXIS clock converter before/after it.

Do not silently sample single-clock monitor ports from another clock domain.
Telemetry consumers need a documented CDC contract.

---

## HIL And Telemetry Gates

For hardware gates, separate numeric proof from transport proof.

Numeric proof should report:

- golden model commit or committed vector bundle;
- output width and window length;
- selected-shift sequence;
- measured-power sequence or summary;
- max integer error for output samples;
- overflow count and whether clipping was expected.

Telemetry proof should report that readback fields are live and coherent:
selected shift, measured power, overflow count, and any duplicate Ethernet TM
fields. A live telemetry check is not a substitute for a bit-exact numeric gate.

When a downstream correlator uses the bit-select output as a bridge for another
proof, prove the bit selector is transparent for that vector. Example: selected
shift 0 and overflow 0 means sign-extended IQ can pass through without changing
integer values. That proof is vector-specific.

---

## Acceptance Gate Template

Use explicit gates in the handback:

| Gate | Evidence |
|---|---|
| Local bit-exact | Python/xsim/SV testbench against the pinned bit-select golden, including shift, power, samples, and overflow. |
| Runtime widths | Directed vectors for all supported output widths, usually `{2,4,6,8}`. |
| Timing/resource | OOC timing/resource plus integrated Stage 14/15 when used in the system clock island. |
| Hardware numeric | Board checker compares egress samples against golden with the intended window/output width. |
| Telemetry | Live selected shift, measured power, and overflow fields decoded from AXI/telemetry. |
| Residual scope | Packetizer tails, unsupported output widths, threshold/window coupling, or cross-domain monitor use. |

The handback should say whether failures are arithmetic, buffering, packetizer,
CDC, or checker-alignment failures. Do not collapse them into a generic HIL
failure.
