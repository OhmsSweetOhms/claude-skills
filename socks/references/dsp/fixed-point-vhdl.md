# Fixed-Point VHDL DSP Datapath Reference

Read this before Stage 1 for authored VHDL DSP datapaths that must be
bit-exact: mixers, NCO-driven datapaths, integrate-and-dump blocks,
accumulators, threshold/power logic, fixed-point control loops, or custom
sample pipelines. For FIR/rate-conversion vendor IP, read
`references/dsp/rate-conversion.md` instead. For GPS/GNSS correlators, read
this file first, then `references/dsp/gps-correlator.md`. For FFT engines and
PCPS acquisition datapaths, read this file first, then
`references/dsp/gps-acquisition-fft.md`.

Keep block-specific facts in the project docs and handback. Add only reusable
SOCKS workflow lessons here.

---

## Stage 1 Contract

Before RTL, capture these in `docs/DESIGN-INTENT.md`,
`docs/ARCHITECTURE.md`, or the plan handback:

1. Input and output integer layout: width, signedness, I/Q ordering, packing,
   sample cadence, `tvalid`/`tready`/`tlast` meaning.
2. Numeric source of truth: committed vector files, fixed-point model SHA or
   artifact bundle, config JSON, and expected dump/output format.
3. Fixed-point formats for every accumulator, NCO, phase, coefficient, product,
   rounding point, truncation point, saturation/wrap point, and output word.
4. Initial conditions for stateful numeric logic. Do not leave reset state or
   first-sample behavior implicit.
5. Latency and throughput: cycles from accepted input to output, any pipeline
   bubbles, lane scheduling order, and whether output order is input order or
   channel/scheduler order.
6. Clock domains: AXI-Lite/config clock, stream/sample clock, dump/readback
   clock, reset ownership, and CDC ownership.
7. Resource target: expected DSP, BRAM, LUT/carry, and register budgets, plus
   the Vivado report that will be used as evidence.
8. Timing target: OOC target period, integrated system clock target, and which
   stage/report is the signoff authority.

If any of these are unknown, stop and ask. Most failures in fixed-point DSP
threads are contract failures, not arithmetic mistakes.

---

## Bit-Exact Reference Discipline

Use the committed fixed-point artifact bundle as the gate authority. Do not
bit-compare RTL against a float model after a fixed-point golden exists.

Minimum bundle shape:

- input memory or packet file;
- config JSON or register-write transcript;
- expected output/dump CSV;
- README naming the generator commit and numeric convention;
- explicit tolerance, often `max_abs_lsb=0` for bit-exact gates.

Testbenches should report event counts and the largest integer error. A pass
line should make the acceptance gate auditable, for example:

```text
ALL TESTS PASSED case1 checks=307 records=20
```

When CSV parsing is part of the proof, make it deterministic. Avoid simulator
format-scan behavior that can differ between tools; a byte-wise integer parser
or a simple line parser is safer for signed dump fields.

---

## DSP Inference And Resource Control

Treat DSP inference as evidence-driven. Do not assume a `use_dsp` or
architecture-wide DSP preference helps.

Rules:

- Check actual `DSP48E1`/`DSP48E2` counts in Vivado reports after synthesis or
  implementation.
- Broad DSP attributes can increase resource use by forcing many narrow or
  scheduler-local operations into DSPs.
- For many narrow signed multiplies/adds, LUT/carry arithmetic can be smaller
  and easier to route than inferred DSPs.
- Keep resource history in the handback: baseline count, attempted fix count,
  final count, and the exact report path.
- If a datapath must avoid DSPs, prove it with OOC and integrated reports; do
  not rely on code inspection.

Use local attributes only when a specific operation must map or must not map to
DSPs, and re-check the resulting utilization. Attribute intent without report
evidence is not a result.

---

## Timing Closure Strategy

Use a fast OOC timing experiment to isolate the block, then run integrated
timing. OOC closure is not system closure.

Procedure:

1. Build a small OOC Tcl harness with the target part, target clocks, async
   clock groups, timing summary, worst paths, utilization, and a routed DCP.
2. If OOC fails, parse the real top failing paths and retime the block.
3. If OOC passes, run the integrated system build because the root path may
   move into a neighboring block or interconnect.
4. For failures, classify whether the root is intra-clock datapath, CDC,
   high-fanout route, or missing constraint before editing RTL.
5. Prefer RTL retiming at semantic boundaries over constraints around real
   single-cycle work.

Useful retiming boundaries in streaming DSP:

- accepted sample register after `valid && ready`;
- precomputed narrow products or power terms;
- completed-window accumulator/power/metadata register;
- delayed end-of-window decision aligned with the delayed data;
- writeback stage for state arrays and dump records.

Do not add a multicycle or false path because a chain is inconvenient. Use
constraints only when the data is genuinely stable for the claimed cycles or the
path is a real asynchronous crossing.

---

## Multi-Clock Control, Status, And Dump CDC

When the sample path runs in one clock and AXI/readback runs in another, audit
three crossings separately.

| Crossing | Required pattern |
|---|---|
| AXI config to sample domain | Freeze an AXI-domain payload, then cross with request/ack toggle or equivalent handshake. The sample side must not copy live AXI arrays after only a toggle. |
| Sample status to AXI readback | Capture a coherent sample-domain snapshot, toggle it into AXI, then read only the AXI-domain copy. Do not have AXI register reads sample live stream-domain counters or debug bits. |
| Dump/data records | Use a real async FIFO, Gray-pointer FIFO, or equivalent data-valid ownership protocol. Data must be written before the write pointer makes it visible. |

Mark two-flop synchronizers with the target tool's async-register attribute.
Keep reset crossing explicit. If monitor/debug ports expose sample-domain state,
do not assume they are safe for a slower telemetry consumer just because AXI
register readback was fixed.

Testbenches should run clocks at their intended hardware rates, not all at the
same period. For example, if sample logic is 200 MHz and AXI is 100 MHz, run the
simulation that way.

---

## Lane And Scheduler Parameterization

A parameterized lane count is a separate gate from feature completeness.

Recommended gates:

- hardware build uses the planned hardware lane count, often `N=1`;
- xsim proves at least two non-hardware counts, for example `N=2` and `N=3`;
- expected output ordering is documented: channel-major, lane-major,
  timestamp-major, or FIFO arrival order;
- scheduler state updates and dump ordering are compared against the fixed-point
  golden, not just counted.

Avoid promoting future channel count, code-family, or feature work into the
current gate unless the plan explicitly asks for it.

---

## Verification And Handback Evidence

Record enough evidence for the next agent to avoid rediscovery:

- vector bundle path and generator SHA or committed artifact metadata;
- xsim commands and pass lines for every lane/scheduler variant;
- OOC timing command, report path, WNS/TNS/WHS/THS, resource counts;
- integrated Stage 14/15 command, log path, timing report, and route status;
- hardware checker command/logs when hardware proof was run;
- residual scope, especially missing injection seams, non-general bridge proofs,
  monitor CDC decisions, and unimplemented code families.

Do not write "timing passed" without the report path and slack numbers.
Do not write "bit exact" without the vector path, expected output path, and
`max_abs_lsb` or equivalent result.
