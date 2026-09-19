# GPS L1 C/A Tracking Loop Reference

GPS-specific topology, coefficients, and conventions for the
control-loops skill. This file supplements the general SKILL.md
with details from Kaplan, Ward, and 6 cross-validated open-source
implementations (GNSS-SDR, PocketSDR, SoftGNSS, sturdr,
GNSS-DSP-tools, gps-walkthrough).

---

## Receiver Architecture

```
IQ @ 4.096 MSPS
  |
  v
B1: DynamicBitSelect (12->4 bit)
  |
  +---> B2: PCPS Acquisition (FFT, peak1/peak2)
  |
  +---> B3: TrackingChannel (E/P/L correlator, 1 kHz dump)
          |
          +-- B4: DLL Disc (normalized envelope)
          +-- B5: DLL Filter (2nd-order bilinear-z)
          +-- B6: PLL Disc (Costas atan)
          +-- B7: PLL Filter (3rd-order project recurrence + FLL aid)
          +-- B8: FLL Disc (cross-dot atan2, BPSK squared)
          +-- B9: C/N0 + Lock Detect (M2M4, NBPW, PLI)
```

## Research-Backed Topology

This table records the C/A project topology informed by the local six-receiver
survey. Survey agreement is scoped to those versions, not every receiver or
signal family. Verify the active profile and source before implementation:

| Block | Choice | Provenance |
|-------|--------|------------|
| PL.B1 | Dynamic MSB select, 12->4 bit | Majoral thesis; Hegarty 2011 |
| PL.B2 | PCPS (FFT), peak1/peak2 | GNSS-SDR; project selection |
| PL.B3 | 32-bit NCO, 3-tap E/P/L, 1 kHz dump | gps-fpga, GNSS-SDR |
| PS.B4 | Normalized E-L envelope | Unanimous 6/6 |
| PS.B5 | 2nd-order, zeta=0.707, carrier-aided | GNSS-SDR, Kaplan |
| PS.B6 | Costas atan(Q/I) | Unanimous 6/6 |
| PS.B7 | 3rd-order PLL, FLL-assisted hard switch | GNSS-SDR, sturdr, Kaplan |
| PS.B8 | Cross-dot atan2, BPSK squared | PocketSDR, Foucras 2014 |
| PS.B9 | M2M4 C/N0, NBPW lock, PLI carrier lock | GNSS-SDR (code-verified) |

PS.B7's source recurrence is authoritative:
`gps_receiver/blocks/ps_b7_pll_loop_filter.py:135` averages current/previous
phase-error inputs but adds the newly updated acceleration state directly to
the velocity state. This is not a cascade of trapezoidal updates for every
state. Do not change the measured algorithm merely to match a transform label.

## Tracking State Machine

```
IDLE -> ACQUIRING -> PULL_IN -> TRACKING -> LOCKED
                       |                      |
                       +------ loss <---------+
```

| State | Carrier Loop | Code Loop |
|-------|-------------|-----------|
| PULL_IN | Proportional FLL, profile-selected gain | Profile-selected pull-in DLL |
| TRACKING | Profile-selected 3rd-order PLL + FLL assist | Profile-selected pull-in DLL |
| LOCKED | Profile-selected 3rd-order PLL, FLL off | Profile-selected DLL |

### PULL_IN Architecture

PLL discriminator output is ZEROED. FLL uses proportional-only gain:
```
K = 4 * Bn * T / (2*pi)
carrier_freq_adj = K * freq_error
nco_freq = nco_freq + carrier_freq_adj   (frequency increment)
```

This is the PocketSDR/GNSS-DSP-tools pattern. No filter integrator avoids that source of windup; frequency accumulation,
noise and unhandled sign transitions can still move the NCO.

### TRACKING Architecture

3rd-order PLL at pll_bw_tracking_hz with FLL assist at
fll_bw_tracking_hz. Uses total-correction convention:
```
carrier_freq_adj = pll_filter.update(phase_error, freq_error)
nco_freq = carrier_freq_base + carrier_freq_adj   (total correction)
```

carrier_freq_base is recorded at the PULL_IN->TRACKING transition.
One-shot Costas phase alignment at entry:
```
phase_align = pll_disc.process(IP, QP)
channel._carrier_phase_rad += phase_align
```

### LOCKED Architecture

3rd-order PLL at pll_bw_locked_hz, FLL disabled (freq_error=0).
Narrowed DLL bandwidth and E-L spacing.

### Transitions

| From | To | Criteria |
|------|----|----------|
| PULL_IN | TRACKING | Minimum window from `PS.B7.fll_pull_in_time_ms`, full P/max(E,L) history, and median strictly above `PS.B5.pull_in_to_tracking_threshold` (open_sky 1.1) |
| TRACKING | LOCKED | `CN0Estimator.is_locked` latch is true; see earning/holding semantics below |
| TRACKING | PULL_IN | Both C/N0 and LI below their `PS.B9` thresholds for a streak strictly greater than `max_consecutive_lock_fails` |
| LOCKED | PULL_IN | `CN0Estimator.is_locked` latch drops after its hold-failure policy |

Authority: `gps_receiver/receiver.py:772` onward and
`gps_receiver/blocks/ps_b9_cn0_lock_detect.py:266` onward. Inspect the selected
revision if the code moves. Pull-in counts are epochs; the millisecond-named
parameter assumes the 1 ms pull-in path.

**Earning and holding lock differ.** Earning an epoch requires C/N0, LI and
PLI to meet `cn0_lock_min_dbhz`, `lock_indicator_min`, and `carrier_lock_th`.
Holding uses C/N0 and LI only. The implementation accumulates qualifying entry
epochs until `consecutive_lock_epochs`; despite the parameter name, an isolated
bad epoch does not reset that count. A hold-failure streak strictly greater
than `max_consecutive_lock_fails` clears the counter and latch. Do not replace
this with a literal consecutive-good-epochs rule or reintroduce PLI in the
hold predicate. The separate sustained-loss/reacquisition policy is below.

### Re-Entry to PULL_IN

`_enter_pull_in()` resets PLL and DLL filter integrators, restores
pull-in E-L spacing and bandwidths. Stale narrow-BW integrator state
would destabilize the carrier when bandwidths widen.

## Profile authority and carrier tuning

Read `receiver-block-profiles.json::profiles` and the consumer defaults in
`gps_receiver/receiver.py`; do not copy a static table of bandwidths into a new
experiment. Capture the complete effective profile with the run. The C/A
`open_sky` locked bandwidth is 18 Hz on the carrier-wander branch at
`f6486424f9f2`; other profiles and nested L1C values were not changed. This is
branch-scoped evidence, not a statement that all branches or deployed firmware
use 18 Hz. Literal static no-worsening was waived by the operator; the full
receiver suite remained gate-incomplete at night handback.

See [carrier wander and replay](carrier-wander-replay.md) for measurement and
acceptance discipline, and control-loops for the reusable NCO-frame estimator.

## Key Constants

```python
GPS_L1_HZ = 1575.42e6           # L1 carrier frequency
CHIP_RATE_HZ = 1.023e6          # C/A code chipping rate
CODE_LENGTH = 1023              # C/A code length (chips)
FS_HZ = 4.096e6                 # Sample rate after decimation
SAMPLES_PER_CODE = 4096         # Samples per 1ms code period
CARRIER_AIDING_SF = 6.493e-4    # Rc/fL = 1.023e6/1575.42e6
```

## Carrier Aiding

Code rate is coupled to carrier Doppler:
```
code_freq = CHIP_RATE + carrier_freq * CARRIER_AIDING_SF - code_freq_adj
```

The scale factor (Rc/fL) converts carrier Doppler (Hz) to code
Doppler (chips/s). This dramatically reduces DLL dynamics.

## Loss of lock is a TIME, not the weak-epoch counter (measured 2026-09-10)

`max_consecutive_lock_fails` (50 open_sky … 200 cislunar) is ADR-009's
in-`TRACKING` weak-epoch counter behind the transient `PULL_IN` re-entry. It
is NOT a sustained-loss test: pricing the per-satellite loss return on it
tore down two good satellites (PRNs 3 and 25) at 0.9 s of the 45 s static run
of record and never recovered them (RMS 1.578 → 1.695 m). The PS.B9 weak
AND-gate (`cn0 < cn0_lock_min AND lock_indicator < lock_indicator_min`) goes
the other way and never fires on a 1.9 s outage, because the 2e-3 C/N0
smoother lags ~500 epochs while the lock indicator is at 0.00 within 50 ms.
The lock indicator is the fast observable; the hold is a time:
`PS.B9.sustained_loss_hold_ms = 1500` in every C/A profile (ADR-022's banked
loss-hold re-scoped from the solution to one satellite), which leaves the
static run bit-identical on every PVT figure. ADR-033's `TRACKED → REACQUIRE`
row was amended to say so. Two more traps from the same hop: `STATUS_FLAG_LOCKED`
must follow the channel STATE (LOCKED after the pull-in gate), not PS.B9's own
declaration, which can fire at epoch 122 while the channel is still in pull-in
(gate clears at 500); and a seeded re-search must anchor to the RELEASED
channel's own sample cursor, never the global sample counter — invisible with
two channels, fatal with eleven. Bit-exact C parity against these goldens
required mirroring numpy's PAIRWISE summation (`np.mean`/`np.sum` over the
PS.B9 window: a plain loop below eight elements, a balanced tree of eight
partial accumulators at or above) — a naive windowed sum leaves ~1 ULP in
every mean.

## Candidate improvements require current evidence

The early survey did not establish a project policy for anti-windup, frequency
clipping, or adaptive lock thresholds. That is not evidence that no receiver
implements them. Read current source and research before proposing a change.
Transition gates already exist in `TrackingState._check_transitions`; inspect those
and the separate sustained-loss policy above before calling gating absent.

## Research Sessions

Local research with cross-implementation analysis:

| Session | Blocks | Content |
|---------|--------|---------|
| session-20260405-140000 (A) | PS.B7-B8 | FLL discriminator, PLL coefficients |
| session-20260405-150000 (B) | PS.B4-B6, B9 | DLL/PLL disc consensus, C/N0, lock |
| session-20260405-160000 (PL) | PL.B1-B3 | Quantization, PCPS, correlator arch |
| session-20260405-170000 (C) | All | Cross-implementation synthesis |

## Reference Implementations

| Repo | License | Used For |
|------|---------|----------|
| GNSS-SDR | GPL-3.0 | Production tracking loops, M2M4, lock detect |
| PocketSDR | BSD-2 | Prop-only FLL pull-in, cross-dot squared |
| SoftGNSS | GPL-2.0 | Bilinear-z DLL, Borre Chapter 7 |
| sturdr | MIT | Kaplan 3rd-order, KF tracking |
| GNSS-DSP-tools | MIT | C/A code gen, acquisition, 1st-order FLL |
| gps-walkthrough | -- | Complete Python receiver, validation |
