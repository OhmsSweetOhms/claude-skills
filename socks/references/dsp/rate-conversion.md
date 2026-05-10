# DSP Rate-Conversion Reference

Read this file before Stage 1 when a module performs interpolation,
decimation, sample-rate adaptation, or wraps vendor FIR IP. These
patterns are for DSP blocks that use AXI-Stream data paths, static FIR
coefficient files, and clock-rate contracts that must remain visible in
architecture docs, testbenches, synthesis constraints, and HIL plans.

This reference is intentionally shared by interpolators and decimators.
Keep block-specific facts in the module's `docs/` directory; add only
reusable SOCKS workflow lessons here.

---

## When To Read This

Read this reference before Stage 1 for designs with any of these traits:

- Xilinx FIR Compiler, FFT, CIC, or equivalent vendor DSP IP in the data
  path.
- Interpolation or decimation factors that are fixed at synthesis but
  selected at runtime through wrapper logic.
- Coefficients delivered as CSV, JSON, MATLAB, scipy, or text artifacts
  that must become Vivado `.coe` files.
- AXI-Stream input/output rates that differ, even when the fabric clock
  is a single clock.
- Group delay that downstream receivers, timestamping, loopback tests,
  or HIL comparisons must absorb.
- Multiple substrate rates feeding one common system boundary.

Stage 1 must make the clocking, rate, factor, coefficient, and latency
contracts explicit. Do not leave those contracts to Vivado IP settings
alone; generated IP metadata is not a design intent document.

---

## Stage 1 Checklist

Before entering the design loop, capture these items in
`docs/DESIGN-INTENT.md` and `docs/ARCHITECTURE.md`:

1. Input and output AXI-Stream data layout, including I/Q ordering,
   container width, signedness, and whether `tlast` is meaningful.
2. Each supported rate mode: input rate, output rate, factor, clock
   domain, filter chain, and use case.
3. Which factors are synthesis-time IP parameters and which controls are
   runtime wrapper parameters.
4. Coefficient source of truth and exact conversion path to `.coe`.
5. Coefficient quantization width, rounding mode, gain convention, and
   saturation or overflow behavior.
6. Group delay per FIR stage and total group delay per runtime mode.
7. Clock-domain crossings, including whether the vendor IP owns a CDC
   boundary or the wrapper must synchronize control/status bits.
8. Synthesis part, target clock periods, and constraints that prove the
   highest-rate domain.
9. Verification alignment rule: how the Python reference and SV TB skip
   fill latency and compare only valid steady-state samples.

If any of these fields are unknown, stop and log a tracker item before
improvising. Most DSP-module failures are contract failures between
rate modes, not arithmetic mistakes in the multiply-adds.

---

## FIR Compiler v7.2 Customization

For Xilinx FIR Compiler v7.2, treat interpolation and decimation factor
as synthesis-time parameters. Runtime coefficient reload is a separate
feature and does not make one instance a general runtime `xN` or `/M`
rate converter. If a module needs runtime factor selection, instantiate
one fixed-factor IP path per factor and place AXI-Stream switch logic
around those paths.

Record these settings per instance:

| Setting | Guidance |
|---|---|
| Filter type | Interpolation or decimation, not a runtime mode. |
| Factor | Fixed per IP instance. Expose runtime selection in the wrapper. |
| Coefficients | Load from a committed `.coe` derived from the approved source file. |
| Coefficient width | Use the design intent value; 16-bit signed is the current GPS rate-conversion default. |
| Input width | Match the project stream contract. GPS uses complex int16 containers for 12-bit sign-extended I/Q. |
| Output width | Match the downstream stream contract unless the design intent explicitly keeps guard bits. |
| Rounding | Prefer convergent rounding to avoid DC bias when reducing width. |
| Saturation | Enable when downstream DAC/ADC container width must clip rather than wrap. |
| AXIS interfaces | Keep stream names conventional so Stage 21 can infer bus interfaces. |
| `s_axis_config` | Leave disabled unless coefficient reload is an explicit requirement. |

For vendor-IP-heavy modules, the checked-in RTL wrapper is still the DUT.
Generated IP products remain build artifacts. The wrapper should expose a
stable SOCKS interface while the build scripts regenerate the underlying
IP from `socks.json`, `.coe`, and Vivado Tcl.

---

## Coefficient Pipeline

Use committed coefficient artifacts as the source of truth. If a design
thread says the CSV is canonical, do not regenerate coefficients from the
upstream script during module authoring; convert the CSV exactly and
record the conversion command in the handback.

Recommended flow:

1. Copy the approved coefficient source files into `src/coeffs/`.
2. Generate one `.coe` per source file in the same directory.
3. Preserve a deterministic CSV-to-COE helper under the handoff inbox or
   promote it to the SOCKS skill when it becomes reusable.
4. Verify tap count, symmetry, sum/gain, quantized min/max, and whether
   any tap saturated.
5. Record the generated `.coe` file paths in `docs/ARCHITECTURE.md`.

Vivado `.coe` files for signed decimal coefficients should use this
shape:

```text
memory_initialization_radix=10;
memory_initialization_vector=
12,
-5,
...
0;
```

Quantization rule must be explicit. The GPS rate-conversion modules use
float64 FIR taps quantized to signed Q1.15-style integers:

```text
q = round(coef * (2^(bits - 1) - 1))
q = clamp(q, -2^(bits - 1), 2^(bits - 1) - 1)
```

After quantization, report:

- tap count;
- coefficient integer range;
- sum of float taps and quantized taps;
- symmetry mismatch count;
- saturation count;
- passband ripple and stopband attenuation when a scipy reference is
  available.

Log a `new_script` tracker item if SOCKS lacks a generic converter or
quantization reporter for the artifact format in use.

---

## Multi-Rate Clock Documentation

Do not assume "different sample rate" means "different clock domain."
Document both separately.

Common cases:

| Case | Pattern | Documentation requirement |
|---|---|---|
| Single clock, rate-changing data | One fabric clock, AXIS `tvalid` cadence changes. | Show clock frequency, input sample period, output sample period, and valid cadence. |
| Dual clock vendor IP | Separate input and output AXIS clocks, CDC owned by IP. | Cite the IP boundary, reset sequencing, and synchronized control bits. |
| Wrapper-owned CDC | Control/status or stream data crosses outside vendor IP. | Add synchronizer design, constraints, and reset behavior. |

For runtime controls crossing from AXI-Lite into a stream clock domain,
use a 2-FF or handshake synchronizer for static controls. For pulse
controls, use a toggle or request/acknowledge handshake so writes are
not missed by a slower clock.

Architecture docs should include a clocking diagram with:

- AXI-Lite clock and reset;
- input stream clock and reset;
- output stream clock and reset;
- vendor IP clock ports;
- CDC ownership;
- associated constraints.

---

## Group-Delay Accounting

Linear-phase FIR group delay is:

```text
delay_samples = (num_taps - 1) / 2
delay_seconds = delay_samples / filter_sample_rate_hz
```

For cascades, add delay in seconds across stages. Do not add raw sample
counts across different sample rates.

Every rate mode needs a delay table:

| Mode | Stages | Delay per stage | Total delay | Consumer action |
|---|---|---|---|---|
| `x30` | `x5 + x6` | `7.324 us + 0.423 us` | `7.747 us` | TB skips fill samples; loopback accounts for fixed offset. |

The Python model, SV TB, VCD verifier, CSV cross-check, and HIL
comparison must use the same alignment rule. If the testbench discards
initial samples to absorb FIR fill latency, state the exact count and
the clock domain used for that count.

---

## Polyphase Decomposition

Use vendor polyphase FIR IP unless the plan explicitly requires a custom
polyphase engine. The design docs should still describe the decomposition
so reviewers can reason about latency and resource estimates.

Interpolation by `N`:

- prototype taps split into `N` branches;
- each input sample produces `N` output phases;
- branch `k` contains taps `h[k], h[k+N], h[k+2N], ...`;
- per-branch tap count is `ceil(num_taps / N)`.

Decimation by `M`:

- prototype taps split into `M` branches;
- only every `M`th filtered output is retained;
- branches usually run at the output sample rate in an efficient
  implementation;
- per-branch tap count is `ceil(num_taps / M)`.

Prefer cascades when a direct large factor creates a narrow transition
band at the highest sample rate. Prefer a single stage when one filter
meets the attenuation, passband, latency, and resource budget with less
control logic than a cascade.

---

## Runtime Ratio Select With AXIS Switches

When FIR Compiler factor is fixed but the block must support runtime
ratio selection, use one fixed-factor path per mode and switch AXI-Stream
traffic around those paths.

Recommended wrapper behavior:

- `enable = 0`: deassert output `tvalid` and back-pressure input.
- `ratio_select`: selects the active path after synchronization into the
  relevant stream clock domain.
- inactive paths see `tvalid = 0`.
- output mux forwards only the active path's `tdata`, `tvalid`, and
  status.
- unsupported encodings route to a disabled path with `tvalid = 0`.
- software performs `disable -> ratio_select -> reset -> enable` for
  glitch-free changes.

Avoid changing ratio while streaming unless the design intent explicitly
allows a dropped or duplicated boundary sample. If allowed, document the
transient and expose enough status for software to confirm the active
path.

---

## Verification Pattern

Use the Python model as the numerical reference, but separate numerical
filter comparison from AXI protocol checks.

Minimum tests:

- impulse response per ratio;
- step response per ratio;
- deterministic counting-pattern stream to catch drops and duplicates;
- complex I/Q sinusoid or chirp for phase, image, and gain checks;
- reset while idle;
- disable/enable while idle;
- reserved ratio encoding;
- back-pressure if the wrapper claims to support it.

For CSV/VCD comparison, export event-indexed rows with at least:

- cycle or timestamp;
- ratio mode;
- input valid/ready/fire;
- output valid/ready/fire;
- output I/Q;
- reference I/Q;
- alignment index;
- error.

Tolerance must be tied to coefficient and sample quantization. For
bit-true wrappers, expect exact integer equality after alignment. For
scipy float references, compare after the same coefficient quantization
and output rounding used by the RTL/IP.

---

## Worked Example: PL.INTERPOLATOR

PL.INTERPOLATOR is the Tx-side GPS rate converter from the chosen
substrate rate to the clean-6144 AD9986 122.88 MSPS TX boundary.

Runtime modes:

| `ratio_select` | Input rate | Output rate | Implementation | Group delay |
|---|---:|---:|---|---:|
| `0b00` | 4.096 MSPS | 122.88 MSPS | `x5` F_DECINT_5 then shared `x6` F_INT_6 | 7.747 us |
| `0b01` | 20.48 MSPS | 122.88 MSPS | shared `x6` F_INT_6 | 0.423 us |
| `0b10` | 61.44 MSPS | 122.88 MSPS | `x2` F_INT_2 | 0.317 us |
| `0b11` | n/a | n/a | disabled/reserved | n/a |

Plan-01 decisions:

- FIR Compiler v7.2 factor is fixed per instance.
- The wrapper is the runtime-parametric block.
- F_INT_6 is shared between the `x30` cascade second stage and the
  `x6` single-stage path.
- Coefficient reload is not exposed.
- Coefficients come from committed thread-root CSVs and are converted to
  `.coe` during module authoring.
- AXI-Lite exposes `enable`, `reset`, `clear_underrun`, and
  `ratio_select`; software uses disable/select/reset/enable for clean
  transitions.

PL.INTERPOLATOR is the first SOCKS DSP module. Any missing scripts,
reference gaps, Vivado FIR Compiler quirks, or HIL schema gaps found
while building it should be logged in the module-local
`docs/socks_tracker.json`.

---

## Worked Example Placeholder: PL.DECIMATOR

PL.DECIMATOR is the Rx-side mirror. Its plan-02 should extend this file
with decimator-specific lessons after its SOCKS implementation is walked:

- anti-alias filter placement before rate reduction;
- `/3` and `/5` cascade details;
- shared F_DECINT_5 coefficient use;
- dual-tap output pattern for 20.48 MSPS and 4.096 MSPS consumers;
- portability differences between DSP48E1 and DSP48E2 targets.

Do not add speculative decimator guidance here before the decimator plan
has produced evidence.
