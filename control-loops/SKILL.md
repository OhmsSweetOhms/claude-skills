---
name: control-loops
description: "Digital control loop design, debug, and test for PLLs, FLLs, DLLs, and tracking loops. Use this skill when designing loop filters (2nd/3rd order), computing Kaplan/Ward coefficients, choosing discretization (bilinear-z/Tustin vs forward Euler), debugging loop instability or integrator divergence, writing unit tests for loop math, translating floating-point models to fixed-point or VHDL pipelines, or setting up JSON-driven parameter profiles for control systems. Also triggers on: NCO convention (total-correction vs incremental), carrier/code tracking, lock detection (M2M4, NBPW, PLI), C/N0 estimation, phase/frequency discriminators, loop bandwidth tuning, and stability analysis. Provider-neutral: for GPS-specific topology (Kaplan 3rd-order L1 C/A, Costas PLL, cross-dot FLL), use the gps-design skill instead."
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

## 1. Loop Filter Design

### 2nd-Order Loop (DLL, simple PLL)

Standard analog prototype with bilinear-z discretization:

```
H(s) = (tau2*s + 1) / (tau1*s)

Natural frequency:  wn = 2*Bn / (zeta + 1/(4*zeta))
Time constants:     tau1 = 1/wn^2,  tau2 = 2*zeta/wn
Digital gains:      k_prop = tau2/tau1,  k_int = T/tau1
```

Where Bn is noise bandwidth (Hz), zeta is damping ratio (0.707 =
Butterworth), T is update interval (s).

### 3rd-Order Loop (carrier PLL with acceleration tracking)

Kaplan coefficients (standard across all production GPS receivers):

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
The FLL gain must include the 2*pi conversion:

```
w0f = 4 * Bn_fll * T * 2*pi   (Hz input -> rad/s internal units)
```

Omitting the 2*pi under-weights the FLL by 6.28x -- the FLL will
appear to do almost nothing while the PLL drifts.

---

## 2. NCO Update Convention

**This is the single most common source of PLL instability bugs.**

There are two conventions for how the loop filter output drives the NCO:

### Convention A: Total Correction (correct for filters with integrators)

```python
nco_freq = base_freq + filter_output
```

The filter's integrators accumulate the total frequency offset. The
NCO is SET to base + offset each epoch. The base frequency is recorded
at the loop handoff point (e.g., pull-in to tracking transition).

Loop order = filter integrators + 1 (NCO phase integration).
A filter with 2 integrators + NCO phase = type-III (3rd order).

### Convention B: Incremental (correct for proportional-only filters)

```python
nco_freq = nco_freq + filter_output
```

The filter output is the per-epoch frequency INCREMENT. The NCO
accumulates these. Correct when the filter has NO integrators
(proportional-only gain), because the NCO accumulation provides
the single needed integration.

### The Type-IV Trap

Using Convention B with a filter that HAS integrators creates an
extra integration in the frequency path:
- Filter: 2 integrators (total correction)
- NCO freq accumulation: +1 (unwanted)
- NCO phase integration: +1
- Total: 4 integrations = type-IV loop

Type-IV loops are marginally stable at best. They appear to work in
short tests (1-2 seconds) but diverge over 5-30 seconds as the extra
integration causes the integrator state to grow without bound.

**Diagnosis:** If a PLL tracks correctly for a few seconds then the
carrier frequency runs away, check the NCO convention first.

**Fix:** Store the base frequency at the handoff point. Use
`nco = base + output` for PLL states. Use `nco += output` only for
proportional-only modes (like a pull-in FLL with no integrators).

---

## 3. Discretization

### Bilinear-z (Tustin) -- Preferred

Maps the entire stable s-domain to the stable z-domain. Uses the
trapezoidal rule -- average of current and previous input:

```python
# Per-integrator update
integrator += gain * (error + prev_error) * 0.5
prev_error = error
```

Requires storing previous error values. Unconditionally stable if the
continuous-time system is stable.

### Forward Euler -- Simpler, Conditionally Stable

```python
integrator += gain * error
```

Stable when `wn * T << 1`. For a 3rd-order PLL at 25 Hz BW with
T=1ms: wn*T = 0.032, well within the margin. But the stability
margin shrinks with higher BW or lower update rate.

### When to Choose

- **Default to bilinear-z** for consistency and robustness.
- Forward Euler is acceptable when wn*T < 0.05 and simplicity matters
  (e.g., bare-metal C on a microcontroller with no FPU).
- The gain formulas are IDENTICAL for both methods when wn*T << 1.
  Only the integrator update rule differs.
- If mixing DLL (bilinear-z) and PLL (forward Euler) in the same
  receiver, switch to bilinear-z for both -- consistency prevents
  confusion in code reviews and debugging.

---

## 4. JSON Spec Stack Pattern

Control system projects should use a layered JSON configuration:

```
tracking-mode-profiles.v1.json       <- "test this scenario"
  |-- iq_gen_defaults                -> signal generator config
  +-- receiver_profile: "open_sky"   -> receiver-block-profiles.v1.json
        +-- per-block params         -> shared-interfaces.v1.json
              +-- telemetry signals  -> monitor-signals.json
```

### Key Files

| File | Purpose | Consumed By |
|------|---------|-------------|
| `shared-interfaces.v1.json` | Block IDs, module/class, I/O contracts | Code structure, tests |
| `receiver-block-profiles.v1.json` | Runtime params per profile per block | Runtime init |
| `monitor-signals.json` | Telemetry signals with types and rates | Telemetry, viewer |
| `tracking-mode-profiles.v1.json` | Scenario -> signal config + profile | End-to-end tests |

### Rules

- **Code reads from JSON at runtime** -- don't hardcode values that
  should come from profiles. Use `.get(key, default)` with defaults
  matching the baseline profile.
- **Parameter names in JSON must match `.get()` keys** in code.
- **Block IDs are canonical** across all four files. Don't invent new
  IDs without updating all files.
- **Topology (algorithms, methods) is fixed** across profiles. Only
  tuning parameters (bandwidths, thresholds, spacings) vary.
- **Profiles represent dynamics envelopes**, not labels. `open_sky` is
  the default/baseline. Others widen BWs, relax thresholds, etc.

### Profile Diff Pattern

When adding a new profile, start from the baseline and change ONLY
the parameters that need to differ. Document the rationale in a
provenance section:

```json
"provenance": {
  "PS.B7.pll_bw_locked_hz": {
    "level": "backed",
    "source": "GNSS-SDR default 5.0 Hz"
  }
}
```

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

The bilinear-z transform halves the integrator input on the first
step (prev=0). This is a measurable 0.5% difference from forward
Euler at typical GPS bandwidths -- small but detectable:

```python
def test_bilinear_z_first_step_halves_input(self):
    filt = PLLLoopFilter(pll_bw_hz=18.0, fll_bw_hz=0.0)
    out = filt.update(0.1)
    self.assertAlmostEqual(out, 0.8811, places=3)  # not 0.8858 (fwd Euler)
```

### Tier 3: Closed-Loop Convergence

Simulate a PLL in pure Python (no IQ gen needed). Feed a known
frequency offset, use atan() as the discriminator, check convergence:

```python
for k in range(500):
    sig_phase += TWO_PI * true_freq * T
    nco_phase += TWO_PI * nco_freq * T
    phase_err = np.arctan(np.sin(sig_phase - nco_phase)
                          / max(np.cos(sig_phase - nco_phase), 1e-20))
    freq_adj = filt.update(phase_err)
    nco_freq = base_freq + freq_adj  # Convention A
```

Assert: residual < 2 Hz after 500 epochs, max deviation < 50 Hz.

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
    # ... run 30s tracking, assert carrier within ±5 Hz
```

### Tier 6: End-to-End Integration

Full receiver with IQ generation, state machine transitions, nav
data. These are slow (~10s each) but catch wiring bugs:

- State transition timing (PULL_IN -> TRACKING at epoch >= min_epochs)
- Bandwidth narrowing at lock
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

**Always use Costas for BPSK data channels.** The atan2 variant is
for pilot channels only.

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

Normalized envelope is unanimous across production implementations.

---

## 7. Lock Detection

### C/N0 Estimation (M2M4)

```
M2 = mean(|P|^2)
M4 = mean(|P|^4)
Pd = sqrt(2*M2^2 - M4)     (clamp Pd_sq >= 0)
SNR = Pd / (M2 - Pd)       (clamp denom > 0, else 60 dB)
C/N0 = 10*log10(SNR / T)
```

EMA smoothing: `cn0 = alpha*raw + (1-alpha)*cn0_prev`, with warmup
period using unsmoothed values.

### NBPW Lock Indicator

```
NBP = (sum(IP))^2 + (sum(QP))^2    (coherent power)
WBP = sum(IP^2 + QP^2)              (total power)
LI = (NBP/WBP - 1) / (M - 1)       (normalized to [0,1])
```

Coherent signal: LI -> 1.0. Noise: LI -> 0.

### PLI (Phase Lock Indicator)

```
PLI = (sum_I^2 - sum_Q^2) / (sum_I^2 + sum_Q^2)
```

Equivalent to cos(2*phi). Strong phase lock: PLI -> 1.0.

### Lock Criteria

Require ALL of: C/N0 >= threshold AND LI >= threshold AND PLI >=
threshold for N consecutive epochs. Typical: C/N0 >= 25 dB-Hz,
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

- **Correlator inputs:** 4-bit (0.05 dB loss vs 12-bit, Hegarty 2011)
- **NCO phase accumulator:** 32-bit unsigned (0.001 Hz resolution at 100 MHz)
- **Accumulator outputs:** 32-bit signed (4096 samples * 4-bit input = 24-bit max, plus margin)
- **Loop filter coefficients:** Fixed-point with enough fractional bits to represent the smallest gain (w0p ~ 0.01 needs ~10 fractional bits minimum)

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

    def accumulate(self, sample, replica):
        product = int(sample) * int(replica)  # exact integer multiply
        self.accum = max(-self.accum_max - 1,
                         min(self.accum_max, self.accum + product))
```

### Pipeline / Time-Sharing for FPGA

When the FPGA must process multiple channels:

- **Time-shared correlator:** One multiply-accumulate engine processes
  N channels sequentially within each sample period. At 4.096 MSPS
  with 100 MHz clock: 24 clock cycles per sample = up to 12 channels
  with E/P/L * I/Q = 6 correlations each (2 cycles per correlation).
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
   Incremental: w2p = b*wn*T (includes T).

5. **Run in isolation.** Feed constant phase error into the filter
   alone (no NCO, no discriminator). The output should grow
   monotonically (integrators accumulating). If it oscillates or
   diverges, the filter math is wrong independent of the loop.

6. **Run closed-loop simulation** (Section 5, Tier 3). Pure Python,
   no IQ generation. If this diverges, the loop dynamics are wrong.
   If it converges but the full receiver diverges, the bug is in
   wiring (sign, units, timing).

7. **Add noise.** A loop that's stable without noise but diverges with
   noise may be marginally stable. Check wn*T and consider bilinear-z.

8. **Check for nav-bit-induced FLL integrator windup.** If the FLL has
   an integrator and the signal has nav-bit transitions, the integrator
   accumulates spurious errors at each transition. Solutions: use
   1st-order (proportional-only) FLL during pull-in, or limit FLL
   duration, or reset integrators at handoff.
