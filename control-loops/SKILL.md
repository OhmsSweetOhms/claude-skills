---
name: control-loops
description: "Digital control loop design, debug, and test for PLLs, FLLs, DLLs, and tracking loops. Use this skill when designing loop filters (2nd/3rd order), computing Kaplan/Ward coefficients, choosing discretization (bilinear-z/Tustin vs forward Euler), debugging loop instability or integrator divergence, writing unit tests for loop math, translating floating-point models to fixed-point or VHDL pipelines, or setting up JSON-driven parameter profiles for control systems. Also triggers on: NCO convention (total-correction vs incremental), carrier/code tracking, lock detection (M2M4, NBPW, PLI), C/N0 estimation, phase/frequency discriminators, loop bandwidth tuning, stability analysis, and offline carrier phase measurement from prompt epochs. Provider-neutral: for GPS-specific topology (Kaplan 3rd-order L1 C/A, Costas PLL, cross-dot FLL), use the gps-design skill instead."
---

# Control Loops -- Digital PLL/FLL/DLL Design & Test

## When to Use

This skill covers the full lifecycle of digital control loops:
designing loop filters, debugging stability, writing verification
tests, and bridging from floating-point Python to fixed-point VHDL.

It applies to any phase/frequency/delay locked loop -- clock recovery,
DPLLs, motor control PLLs, or any DSP feedback loop with NCOs and
discriminators.

This skill is deliberately provider-neutral. GPS-specific topology
(Kaplan L1 C/A 3rd-order, Costas discriminator, cross-dot FLL, nav-bit
handling, C/N0 + lock detector tuning for GPS) lives in the
**gps-design** skill. Use that skill for any GPS-specific work; use
this one for general control-loop mathematics, debugging stability
issues, and the float -> fixed-point -> VHDL porting pattern.

---

## Carrier phase measurement

Use [the phase measurement guide](references/phase-measurement.md) and
`scripts/measure_carrier_phase.py` when measuring wander from BPSK prompt epochs.
It averages squared prompts in the NCO frame, unwraps the residual, then restores
known continuous NCO phase. The guide specifies inputs, CLI use, matched controls,
ambiguity limits, and why shared-clock loopback cannot isolate oscillator noise.

## 1. Loop Filter Design

### 2nd-Order Loop (DLL, simple PLL)

Unity detector/NCO gain analog prototype; gains below assume radians and seconds:

```
H(s) = (tau2*s + 1) / (tau1*s)

Natural frequency:  wn = 2*Bn / (zeta + 1/(4*zeta))
Time constants:     tau1 = 1/wn^2,  tau2 = 2*zeta/wn
Digital gains:      k_prop = tau2/tau1,  k_int = T/tau1
```

Where Bn is noise bandwidth (Hz), zeta is damping ratio (0.707 =
Butterworth), T is update interval (s).

### 3rd-Order Loop (carrier PLL with acceleration tracking)

One Kaplan-style third-order parameterization (not universal across receivers):

```
a3 = 1.1,  b3 = 2.4
wn = Bn / 0.7845              (rad/s, from Bn in Hz)

Gains (total-correction convention):
  w2p = b3 * wn               (proportional, rad/s per rad)
  w1p = a3 * wn^2 * T         (velocity integrator, per step)
  w0p = wn^3 * T^2            (acceleration integrator, per step)
```

The proportional gain w2p has NO factor of T -- it's the instantaneous
response. The integrator gains include T from the discretization.

### FLL Aiding

When combining FLL frequency aid into a PLL filter, the FLL
discriminator outputs Hz but the filter internals are in rad/s.
For the Hz-input, radian-state parameterization below, the gain includes 2*pi:

```
w0f = 4 * Bn_fll * T * 2*pi   (Hz input -> rad/s internal units)
```

Omitting the 2*pi under-weights the FLL by 6.28x -- the FLL will
appear to do almost nothing while the PLL drifts.

---

## 2. NCO Update Convention

**This is the single most common source of PLL instability bugs.**

There are two conventions for how the loop filter output drives the NCO:

### Total correction output

```python
nco_freq = base_freq + filter_output
```

The filter's integrators accumulate the total frequency offset. The
NCO is SET to base + offset each epoch. The base frequency is recorded
at the loop handoff point (e.g., pull-in to tracking transition).

In this minimal topology, two filter integrators plus NCO phase integration
give type III. Loop type counts open-loop integrations; closed-loop order also
includes other dynamic states, including explicit delays.

### Frequency increment output

```python
nco_freq = nco_freq + filter_output
```

The filter output is the per-epoch frequency INCREMENT. The NCO
accumulates these. A proportional frequency-error controller can use this
form. A deliberately derived velocity-form controller can too; inspect the
output contract rather than deciding from the presence of state alone.

### The Type-IV Trap

Accumulating an output designed as total correction creates an extra
integration in the frequency path. For the two-integrator example:

- Filter: 2 integrators (total correction)
- NCO freq accumulation: +1 (unwanted)
- NCO phase integration: +1
- Total: 4 integrations = type-IV loop

That changes the characteristic equation and can destabilize a controller
designed for total correction. Neither instability nor a time-to-divergence
is implied by loop type alone; derive poles for the actual sampled feedback.

**Diagnosis:** If a PLL tracks correctly for a few seconds then the
carrier frequency runs away, check the NCO convention first.

**Fix:** Store the base frequency at the handoff point. Use
`nco = base + output` for a total-correction filter. Use `nco += output`
only when the controller explicitly returns a frequency increment.

---

## 3. Discretization

### Bilinear-z (Tustin)

Maps the entire stable s-domain to the stable z-domain. Uses the
trapezoidal rule -- average of current and previous input:

```python
# Per-integrator update
integrator += gain * (error + prev_error) * 0.5
prev_error = error
```

Requires previous input state. The bilinear transform maps stable analog
poles inside the unit circle, but applying trapezoidal updates to a loop filter
alone does not prove stability of the sampled feedback loop. Include NCO timing,
computation delay, detector gain, saturation, and integration windows.

### Euler and implementation timing

`integrator += gain * error` is a rectangular update. Whether it represents
forward or backward Euler depends on the time indices and output ordering.
Read those indices before naming the method. Small `wn*T` can justify a
continuous-time approximation; a universal `wn*T < 0.05` rule is not a proof.

Keep the validated discretization unless a change is needed. Derive the actual
recurrence and check the resulting poles/response; changing every integrator to
Tustin for stylistic consistency can change latency and bandwidth. See the
[bilinear transform definition](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.bilinear.html)
for the substitution and frequency-warping caveat.

---

## 4. JSON Spec Stack Pattern

Use the project's existing JSON schema and profile loader. Record which
scenario selects which receiver profile, which keys feed the controller, and
which defaults apply if keys are absent. Do not create a parallel spec stack.
For GPS, the v2 scenario root and thin mode registry are documented in gps-design.

After tuning, verify the selected profile, code fallback, generated firmware
configuration, and test expectations agree. Scope changes by signal family:
a shared block name does not imply all profiles or nested signal overrides
should change. Keep measured provenance and distinguish a branch experiment
from a released default.

---

## 5. Test Methodology

### Tier 1: Formula Verification (no simulation needed)

Verify that computed gains match hand-derived Kaplan/Ward values.
These are static -- they catch coefficient bugs immediately.

```python
def test_pll_gain_w2p_matches_kaplan(self):
    """Proportional gain w2p = b*wn at Bn=18 Hz."""
    filt = PLLLoopFilter(pll_bw_hz=18.0, fll_bw_hz=0.0)
    wn = 18.0 / 0.7845  # 22.9446
    self.assertAlmostEqual(filt._w2p, 2.4 * wn, places=1)
```

Test ALL gains (w0p, w1p, w2p, w0f) at a representative bandwidth.
Include the FLL gain's 2*pi factor explicitly.

### Tier 2: Discretization Signature

For an initially zero trapezoidal integrator, the first update from a
step input contributes half the rectangular-update increment. Test that
recurrence, initial conditions, and output ordering against an independent
calculation. Do not copy a numeric output from a differently parameterized
filter; the whole-filter output difference depends on its other paths.

### Tier 3: Closed-Loop Convergence

Simulate a PLL in pure Python (no IQ gen needed). Feed a known
frequency offset, use a modulo-pi discriminator, and check convergence:

```python
for k in range(500):
    sig_phase += TWO_PI * true_freq * T
    nco_phase += TWO_PI * nco_freq * T
    delta = sig_phase - nco_phase
    phase_err = 0.5 * np.arctan2(np.sin(2 * delta), np.cos(2 * delta))
    freq_adj = filt.update(phase_err)
    nco_freq = base_freq + freq_adj  # total correction
```

Choose convergence bounds from the fixture dynamics and capture range. Check
both BPSK signs: clamping a negative cosine denominator to a positive epsilon
changes the Costas discriminator and can hide a quadrant/sign defect.

### Tier 4: Regression Test for Known Failure Mode

If you've fixed a bug, add a test that REPRODUCES the old behavior
and asserts it fails. This proves the fix is load-bearing:

```python
def test_incremental_convention_diverges_regression(self):
    """Old nco += output convention diverges (type-IV bug)."""
    # Same closed loop but with nco_freq += freq_adj
    # Assert max_deviation > 100 Hz within 5000 epochs
```

### Tier 5: Profile Sweep

Run the same stability test across all profiles. Different BWs
stress different stability margins:

```python
for profile_name in ['open_sky', 'urban', 'high_dynamic']:
    profile = load_profile(profile_name)
    # ... run matched tracking fixtures, assess bias, jitter, slips and lock
```

A wider bandwidth can reduce dynamic lag while increasing noisy instantaneous
frequency excursions. Separate mean error, jitter distribution, phase slips,
lock time, and navigation/PVT outcomes. A fixed every-epoch frequency bound may
encode one bandwidth's noise assumptions. Investigate a failure using the same
fixture before changing the bound; never relax it just to turn the suite green.

Offline replay of recorded discriminator inputs checks filter arithmetic and
transition continuity. It cannot rank tracking performance: a changed filter
would alter future prompts. Compare actual closed-loop variants, including
profile loading and state transitions. At bandwidth changes, inspect each
state's units and output contribution before proposing resets or rescaling.

### Tier 6: End-to-End Integration

Full receiver with IQ generation, state machine transitions, and nav
data catches wiring bugs; runtime depends on the fixture and host.

- State transition timing (PULL_IN -> TRACKING at epoch >= min_epochs)
- Profile-selected bandwidth changes at lock
- Loss-of-lock recovery
- Multi-channel isolation

Use deterministic seeds (`np.random.default_rng(42)`) for all
stochastic tests. No flaky tests.

---

## 6. Discriminator Reference

### Phase (PLL)

| Method | Formula | Range | Data-sensitive? |
|--------|---------|-------|-----------------|
| Costas atan | `atan(Q/I)` | +/-pi/2 | No (squares out nav bits) |
| atan2 | `atan2(Q,I)` | +/-pi | Yes (fails on bit flips) |

Use a data-insensitive discriminator for BPSK with unknown signs. Four-quadrant
atan2 is also usable after verified data/secondary-code wipeoff; a pilot may
still carry a secondary code. Handle zero prompt power explicitly.

### Frequency (FLL)

| Method | Formula | Nav-bit robust? |
|--------|---------|-----------------|
| Cross-dot atan2 | `w=z*conj(z_prev); freq=atan2(w.imag,w.real)/(2pi*T)` | No |
| Cross-dot squared | `w=z*conj(z_prev); w2=w*w; freq=0.5*atan2(w2.imag,w2.real)/(2pi*T)` | Yes |

The squared variant (`w^2`) removes 180-degree BPSK ambiguity but
halves the unambiguous range to +/-1/(4T) Hz. At T=1ms: +/-250 Hz.

### Code (DLL)

| Method | Formula |
|--------|---------|
| Normalized envelope | `(|E|-|L|) / (|E|+|L|)` where `|X|=sqrt(IX^2+QX^2)` |
| Power | `(|E|^2-|L|^2) / (2*|P|^2)` |

Normalized envelope is common; detector gain also depends on correlator spacing
and the signal autocorrelation. Match the filter gain to that normalization.

---

## 7. Lock Detection

### C/N0 Estimation (M2M4)

```
M2 = mean(|P|^2)
M4 = mean(|P|^4)
Pd = sqrt(2*M2^2 - M4)     (clamp Pd_sq >= 0)
SNR = Pd / (M2 - Pd)       (valid only for a positive noise estimate)
C/N0 = 10*log10(SNR / T)
```

Invalid finite-window moment estimates need an explicit status or documented
bound, not an unexplained perfect-lock value.

EMA smoothing: `cn0 = alpha*raw + (1-alpha)*cn0_prev`, with warmup
period using unsmoothed values.

### NBPW Lock Indicator

```
NBP = (sum(IP))^2 + (sum(QP))^2    (coherent power)
WBP = sum(IP^2 + QP^2)              (total power)
LI = (NBP/WBP - 1) / (M - 1)       (M > 1, WBP > 0)
```

Coherent signal: LI -> 1.0. Noise: expected LI -> 0; individual estimates
can be negative. Coherent sums require sign-aligned windows or verified bit
wipeoff. A navigation transition can collapse the sum without loss of tracking.

### PLI (Phase Lock Indicator)

```
PLI = (sum_I^2 - sum_Q^2) / (sum_I^2 + sum_Q^2)
```

Equivalent to cos(2*phi). Strong phase lock: PLI -> 1.0.

### Lock Criteria

Require ALL of: C/N0 >= threshold AND LI >= threshold AND PLI >=
threshold for N consecutive epochs in this example policy. Example: C/N0 >= 25 dB-Hz,
LI >= 0.6, PLI >= 0.85, N=100 (100 ms).

---

## 8. Fixed-Point Bridge

When translating a floating-point Python model to fixed-point for
FPGA implementation:

### Analysis Before Conversion

1. **Range analysis** -- instrument the Python model to log min/max
   of every intermediate value across test scenarios.
2. **Sensitivity analysis** -- quantize each variable independently,
   measure output degradation. Find the minimum bits that maintain
   <0.1 dB performance loss.
3. **Bit-growth tracking** -- multiplications double bit width,
   accumulations grow by log2(N) bits. Track through the datapath.

### Representation

Derive widths from the actual input/replica ranges and summation length.
A W-bit phase accumulator has frequency resolution `fs / 2**W`; compute it
for the clock actually advancing the accumulator. Specify rounding, saturation
versus wrap, and coefficient resolution separately. Quantization loss depends
on scaling and signal/noise statistics; no universal bit-count loss applies.

### Python Fixed-Point Model

Create a parallel Python implementation that uses integer arithmetic
matching the FPGA's exact bit widths. Compare outputs against the
floating-point golden model sample-by-sample:

```python
class CorrelatorFixedPoint:
    """Bit-exact Python model of the FPGA correlator."""
    def __init__(self, input_bits=4, accum_bits=32):
        self.input_bits = input_bits
        self.accum_bits = accum_bits
        self.accum_max = (1 << (accum_bits - 1)) - 1
        self.accum = 0

    def accumulate(self, sample, replica):
        product = int(sample) * int(replica)  # exact integer multiply
        self.accum = max(-self.accum_max - 1,
                         min(self.accum_max, self.accum + product))
```

### Pipeline / Time-Sharing for FPGA

When the FPGA must process multiple channels:

- **Time-shared correlator:** One multiply-accumulate engine processes
  N channels within each sample period. Budget available fabric cycles against
  correlations per channel, MAC issue rate, number of parallel lanes, context
  load/store, and pipeline overhead. Pipeline latency is not the issue interval.
- **Pipeline registers:** Insert pipeline stages at multiply output
  and accumulator input. Match latency in the control path.
- **Context switching:** Store per-channel state (NCO phase, code
  phase, accumulator values) in block RAM. Load/store one channel
  context per time slot.

---

## 9. Debugging Checklist

When a tracking loop diverges or oscillates:

1. **Check NCO convention** (Section 2). Is the filter output total
   or incremental? Does the NCO update match?

2. **Check units.** Is the discriminator output in radians or cycles?
   Does the filter expect the same? Is the FLL path in Hz being added
   to a rad/s integrator without 2*pi conversion?

3. **Check sign.** DLL: when code is late (L > E), discriminator is
   negative, correction must speed up code (subtract, not add). PLL:
   when NCO phase lags signal, discriminator is positive, correction
   must increase frequency.

4. **Check gain scaling.** Do the gains include T factors appropriate
   for the convention? Total-correction: w2p = b*wn (no T).
   An incremental realization must be derived as a complete recurrence;
   multiplying the proportional gain by T is not a convention conversion.

5. **Run in isolation.** Feed constant phase error into the filter
   alone and compare the predicted state sequence, including initial state.
   Unbounded growth under constant error is expected for an integrator;
   open-loop growth alone is not evidence of a defect.

6. **Run closed-loop simulation** (Section 5, Tier 3). Pure Python,
   no IQ generation. If this diverges, the loop dynamics are wrong.
   If it converges but the receiver diverges, inspect wiring and omitted
   dynamics (noise, data transitions, integration, quantization and saturation).

7. **Add noise.** A loop that's stable without noise but diverges with
   noise may be marginally stable. Check wn*T and consider bilinear-z.

8. **Check for nav-bit-induced FLL integrator windup.** If the FLL has
   an integrator and the signal has nav-bit transitions, the integrator
   accumulates spurious errors at each transition. Solutions: use
   1st-order (proportional-only) FLL during pull-in, or limit FLL
   duration, or reset integrators at handoff.
