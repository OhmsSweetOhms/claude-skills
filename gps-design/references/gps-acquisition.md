# GPS Acquisition -- PL.B2 (PCPS FFT)

**Status: Stub.** Expand this chapter when acquisition work surfaces
non-trivial design / debug questions.

---

## What This Chapter Will Cover

- PCPS (Parallel Code Phase Search) FFT implementation
- Peak1/peak2 metric and acquisition threshold calibration
- Doppler bin spacing (typically 250 Hz for 1-ms coherent integration)
- Non-coherent averaging (dwell count vs sensitivity vs false-alarm rate)
- Cold-start vs warm-start search space pruning
- Handoff to PL.B3 tracking (code phase convention, Doppler handoff)
- Weak-signal Tiers 1-3 acquisition extensions for cislunar / indoor
- FPGA implementation trade-offs (4096-point FFT, BRAM layout)

## Research Already Done

| Session | Content |
|---------|---------|
| `.research/session-20260322-142449` | Leclere comparison, PCPS vs serial |
| `.research/session-20260405-160000` | PCPS params, peak detection thresholds |

## Current Implementation

`gps_receiver/blocks/pl_b2_acquisition.py` -- FFT-based PCPS
acquisition. Backing references:

- **Universal across production implementations** -- GNSS-SDR,
  PocketSDR, SoftGNSS, sturdr all use PCPS.
- **Peak1/peak2 ratio** as acquisition metric (peak ratio > threshold
  -> declare acquired).
- **Doppler search** centered on 0 Hz by default; profile-dependent
  max doppler (5 kHz open_sky, 10 kHz high_dynamic).

## Known Gaps

- **Weak-signal PULL_IN extended integration** (Tier 3) not yet
  implemented.
- **Decision-directed squaring** for sub-nav-bit integration not yet
  implemented.
- **Warm-start ephemeris-guided search** not yet implemented (would
  accelerate reacquisition after brief loss).

## When to Expand This Chapter

- Writing or debugging `pl_b2_acquisition.py`.
- Porting PCPS to VHDL (FPGA build Phase 4).
- Weak-signal work -- cislunar, indoor, or high-dynamic scenarios
  where sensitivity is bounded by acquisition threshold.
- Anti-jam / anti-spoof work where acquisition becomes adversarial.
