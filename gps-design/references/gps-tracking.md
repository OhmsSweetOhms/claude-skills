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
          +-- B7: PLL Filter (3rd-order bilinear-z + FLL aid)
          +-- B8: FLL Disc (cross-dot atan2, BPSK squared)
          +-- B9: C/N0 + Lock Detect (M2M4, NBPW, PLI)
```

## Research-Backed Topology

These choices are fixed across all profiles, backed by consensus
across 6 implementations:

| Block | Choice | Provenance |
|-------|--------|------------|
| PL.B1 | Dynamic MSB select, 12->4 bit | Majoral thesis; Hegarty 2011 |
| PL.B2 | PCPS (FFT), peak1/peak2 | GNSS-SDR; universal |
| PL.B3 | 32-bit NCO, 3-tap E/P/L, 1 kHz dump | gps-fpga, GNSS-SDR |
| PS.B4 | Normalized E-L envelope | Unanimous 6/6 |
| PS.B5 | 2nd-order, zeta=0.707, carrier-aided | GNSS-SDR, Kaplan |
| PS.B6 | Costas atan(Q/I) | Unanimous 6/6 |
| PS.B7 | 3rd-order PLL, FLL-assisted hard switch | GNSS-SDR, sturdr, Kaplan |
| PS.B8 | Cross-dot atan2, BPSK squared | PocketSDR, Foucras 2014 |
| PS.B9 | M2M4 C/N0, NBPW lock, PLI carrier lock | GNSS-SDR (code-verified) |

## Tracking State Machine

```
IDLE -> ACQUIRING -> PULL_IN -> TRACKING -> LOCKED
                       |                      |
                       +------ loss <---------+
```

| State | Carrier Loop | Code Loop |
|-------|-------------|-----------|
| PULL_IN | Proportional FLL only (no integrators) | DLL 2 Hz |
| TRACKING | 3rd-order PLL (18 Hz) + FLL assist (2 Hz) | DLL 2 Hz |
| LOCKED | 3rd-order PLL (5 Hz), FLL off | DLL 0.5 Hz |

### PULL_IN Architecture

PLL discriminator output is ZEROED. FLL uses proportional-only gain:
```
K = 4 * Bn * T / (2*pi)
carrier_freq_adj = K * freq_error
nco_freq = nco_freq + carrier_freq_adj   (Convention B -- no integrator)
```

This is the PocketSDR/GNSS-DSP-tools pattern. No integrator means
no drift during pull-in, even with nav-bit transitions.

### TRACKING Architecture

3rd-order PLL at pll_bw_tracking_hz with FLL assist at
fll_bw_tracking_hz. Uses total-correction convention:
```
carrier_freq_adj = pll_filter.update(phase_error, freq_error)
nco_freq = carrier_freq_base + carrier_freq_adj   (Convention A)
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
| PULL_IN | TRACKING | >= fll_pull_in_time_ms epochs AND median P/max(E,L) > 1.3 |
| TRACKING | LOCKED | CN0 >= 25 AND LI >= 0.6 AND PLI >= 0.85 for 100 consecutive ms |
| TRACKING | PULL_IN | Both CN0 < 25 AND LI < 0.6 for max_consecutive_fails epochs |
| LOCKED | PULL_IN | is_locked drops (CN0 or LI or PLI below threshold) |

### Re-Entry to PULL_IN

`_enter_pull_in()` resets PLL and DLL filter integrators, restores
pull-in E-L spacing and bandwidths. Stale narrow-BW integrator state
would destabilize the carrier when bandwidths widen.

## Profile Parameters (open_sky baseline)

```json
"PS.B7": {
  "carrier_method": "fll_assisted_hard_switch",
  "fll_assist_pull_in_enabled": true,
  "fll_steady_state_enabled": false,
  "fll_pull_in_time_ms": 500,
  "pll_bw_pull_in_hz": 50.0,
  "pll_bw_tracking_hz": 18.0,
  "pll_bw_locked_hz": 5.0,
  "pll_order": 3
}
"PS.B8": {
  "fll_bw_pull_in_hz": 15.0,
  "fll_bw_tracking_hz": 2.0,
  "discriminator_method": "cross_dot_atan2"
}
```

### Profile Differences (only parameters that vary)

| Parameter | open_sky | urban | high_dynamic |
|-----------|----------|-------|--------------|
| PL.B2.doppler_max_hz | 5000 | 5000 | 10000 |
| PS.B5.dll_bw_pull_in_hz | 2.0 | 2.0 | 3.0 |
| PS.B5.dll_bw_locked_hz | 0.5 | 0.5 | 1.0 |
| PS.B5.el_half_spacing_locked | 0.5 | 0.25 | 0.5 |
| PS.B7.pll_bw_pull_in_hz | 50 | 35 | 50 |
| PS.B7.pll_bw_tracking_hz | 18 | 15 | 25 |
| PS.B7.pll_bw_locked_hz | 5 | 8 | 15 |
| PS.B7.fll_pull_in_time_ms | 500 | 500 | 1000 |
| PS.B8.fll_bw_pull_in_hz | 15 | 15 | 25 |
| PS.B8.fll_bw_tracking_hz | 2 | 2 | 5 |
| PS.B9.cn0_lock_min_dbhz | 25 | 22 | 25 |
| PS.B9.max_consecutive_lock_fails | 50 | 60 | 80 |

Pattern: urban relaxes thresholds and tightens E-L for multipath.
high_dynamic widens all BWs, extends pull-in time, widens Doppler search.

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

## Deferred Gaps (not implemented, no research backing)

- Anti-windup policy (no implementation has it)
- Transition gating thresholds (no implementation has it)
- Frequency error clipping (no implementation has it)
- Adaptive lock detector thresholds (only Stevanovic 2017)

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
