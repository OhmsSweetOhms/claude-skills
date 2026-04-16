# GPS PVT Solver -- PS.B12 (WLS Position / Velocity / Time)

**Status: Stub.** Expand this chapter when PVT work surfaces
non-trivial design / debug questions.

---

## What This Chapter Will Cover

- Weighted Least Squares iteration with Cholesky decomposition
- SV position from ephemeris (Keplerian + perturbations, Thompson 2019)
- SV velocity (closed-form Thompson 2019 extension) for Doppler-based
  velocity solution
- SV clock bias correction (af0 + af1*dt + af2*dt^2 + relativistic)
- Sagnac rotation (apply to SV ECEF with theta = OMEGA_E * tau)
- Light-time iteration (6 iterations typically converges to sub-mm)
- Initial position seed strategies (static BS coordinates, previous
  fix, ECEF center)
- Iteration convergence criteria (dx < 1 mm or 10 iterations max)
- DOP computation (GDOP, PDOP, HDOP, VDOP, TDOP)
- Elevation masking and satellite exclusion
- Troposphere + ionosphere corrections (when available)

## Research Already Done

| Session | Content |
|---------|---------|
| `.research/session-20260412-115500` | PVT solver: PS vs HLS trade-off, rate per profile, RTKLIB pattern |
| `.research/GPS-SV-velocity-and-acceleration.pdf` | Thompson 2019 closed-form SV velocity/acceleration from ephemeris |

## Current Implementation Status

**Blocked on nav decode** (PS.B11 must produce complete SF1+2+3
ephemeris per SV). As of 2026-04-16 this is partially working --
see `gps_receiver/CLAUDE.md` Phase 3 status.

`gps_receiver/blocks/ps_b12_pvt_solver.py` will contain:
- `PVTSolver` class with `.solve(pseudoranges, ephemerides, t_rx)` entry
- Iterative WLS with scipy.linalg.cholesky / scipy.linalg.solve
  (scipy is tolerated in PS.B12 because it's a pure golden reference;
  the bare-metal C port replaces scipy with math.h-only implementations)
- `PVTFix` dataclass: position_ecef_m, velocity_ecef_mps, clock_bias_s,
  clock_drift_s_per_s, gps_time_s, n_svs, gdop, residuals_m

## Expected Failure Modes (when implemented)

- **Singular normal equations** when geometry is weak (< 4 SVs or all
  SVs in one plane). Return NaN / last-valid-fix.
- **Numerical instability** in Cholesky on near-singular systems --
  fall back to QR or SVD.
- **Divergence on bad initial seed** -- check Newton iteration step
  magnitude; if |dx| > 1000 km, reset seed.
- **Inconsistent pseudoranges** -- the anchor chain feeds PVT. If
  anchor residuals are large (> 1 ms spread across SVs), PVT will
  absorb them into clock bias and produce position errors ~ spread * c.
  **This is the primary failure mode seen during the cursor-timing
  debug session.** See `pseudorange-anchoring.md`.

## When to Expand This Chapter

- Implementing `ps_b12_pvt_solver.py`.
- Debugging first-fix position errors that can't be attributed to the
  anchor chain (run the anchor diagnostics first -- see
  `pseudorange-anchoring.md`).
- Adding kinematic / doppler-based velocity solution.
- Porting to bare-metal C with math.h-only (no scipy).
- Implementing Kalman-filter PVT for smoother dynamic-scenario tracking.
