# GPS PVT Solver — PS.B12

Weighted Least Squares position/velocity/time solver consuming the
pseudoranges emitted by PS.B13 `Observables.emit_pseudoranges`. Pure
golden reference in the Python model; the firmware port replaces
scipy primitives with `math.h`-only implementations.

**Authoritative pointers:**

- `gps_receiver/blocks/ps_b12_pvt_solver.py` — `PVTSolver` class,
  `.solve(pseudoranges, ephemerides, t_rx) → PVTFix` entry.
- `scenario_engine/orbit_propagator.py` — `propagate_sv(eph, gps_t)`,
  returns `SVState` with `ecef_m`, `vel_mps`, `clock_bias_s`.
- `.research/session-20260412-115500/` — PVT solver design trade-offs
  (PS vs HLS rate, RTKLIB pattern).
- `.research/GPS-SV-velocity-and-acceleration.pdf` — Thompson 2019
  closed-form SV velocity / acceleration.
- `docs/implementation-plan-firmware-port.md` — math.h-only firmware
  port plan.

---

## Algorithm

Iterative WLS over four unknowns `[x, y, z, c·Δt_rx]`:

1. **SV position at transmit time.** For each SV k, compute
   `gps_t_tx = gps_t_rx − pr_k / c`, propagate
   `sv_k = propagate_sv(eph_k, gps_t_tx)`, apply SV clock correction
   (`af0 + af1·(t−toc) + af2·(t−toc)² + Δt_rel`), Sagnac-rotate SV
   ECEF by `θ = Ω_E · pr_k / c`.
2. **Linearize** about current `x_est`. Design matrix `H` rows are
   `[(x_est − sv_k) / ||x_est − sv_k||, 1]`. Residual `r_k = pr_k −
   ||x_est − sv_k|| − c·Δt_est + c·Δt_sv,k`.
3. **Solve** `(Hᵀ W H) Δx = Hᵀ W r` via Cholesky (scipy in Python,
   hand-rolled in firmware). `W` defaults to identity; could be
   CN0-weighted.
4. **Update** `x_est += Δx`. Iterate until `||Δx|| < 1 mm` or 10
   iterations.
5. **Light-time iteration** is implicit in step 1 each outer
   iteration; no inner loop needed if the outer loop converges.

Post-solve, compute GDOP / PDOP / HDOP / VDOP / TDOP from
`(HᵀH)⁻¹` (unweighted) or `(Hᵀ W H)⁻¹` (weighted).

---

## `PVTFix` Contract

Actual fields (see `ps_b12_pvt_solver.py`):

```python
@dataclass
class PVTFix:
    gps_time_s: float
    position_ecef_m: np.ndarray        # (3,)
    velocity_ecef_mps: np.ndarray      # (3,)
    clock_bias_s: float
    clock_drift_s_per_s: float
    n_svs: int
    gdop: float = nan
    pdop: float = nan
    iterations: int = 0
    residual_norm_m: float = nan
    converged: bool = False
    iterations_trace: list = []        # per-iter (idx, ||r||, ||Δx||, position, Δt)
```

When adding fields, update `test_ps_b12_pvt_solver_unit.py`,
`gen_baseline_lnav.py:_fix_to_dict`, and firmware port.

---

## Velocity Solution

The same WLS skeleton over Doppler measurements gives velocity +
clock drift. Thompson 2019 provides the closed-form SV velocity from
broadcast ephemeris (required for correct Doppler prediction). PS.B12
solves velocity when `Carrier_Doppler_hz` is available per SV;
otherwise velocity stays zero.

---

## Failure Modes

| Mode | Cause | Symptom |
|---|---|---|
| `converged=False` after 10 iterations | Bad initial seed (e.g., origin) with weak geometry, or diverging residuals from mismatched ephemeris / inconsistent pseudoranges | Check `iterations_trace` for `Δx` monotonicity |
| Cholesky failure | `HᵀWH` near-singular (< 4 SVs, coplanar geometry, duplicated rows) | Falls back / returns pre-solve state; caller must inspect `n_svs` and `gdop` |
| Position offset ~1 ms × c (300 km) | Pseudorange inputs carry per-PRN TOW-label bias (not absorbed by clock bias because per-PRN, not common-mode). Historical root cause: fixed-block correlator discarding acq chip phase — see `pseudorange-anchoring.md` and `findings-2026-04-24.md`. | between-PRN intrinsic pseudorange std > 10 km |
| Huge clock bias (ms scale) | Common-mode absorption of an upstream offset (e.g., acquisition-window 80 ms, SoftGNSS's intentional 6-s bias). Benign for position, matters for absolute-time applications. | `median_clock_bias_s` × c equals an integer-ms multiple |

---

## Initial Seed Strategy

Default: ECEF origin `[0, 0, 0]`. Converges in ~6 iterations for
ground scenarios with 4+ SVs. For tougher geometries (cislunar,
indoor) consider a previous fix or a static-BS prior, set via
`PVTSolver(initial_x_ecef_m=...)` or a helper on the receiver.

---

## Iono / Tropo Corrections

Not applied in the Python golden model. When iono/tropo are needed:

- **Iono:** Klobuchar α/β from SF4 page 18. Currently `nav_gen`
  writes zeros; tracked in
  `gps_receiver/threads/gps_iq_gen/20260419-iq-gen-tau-convention-fidelity/`.
  GNSS-SDR on our canonical IQ only reaches 174 m after
  `PVT.iono_model=OFF` for this reason.
- **Tropo:** Saastamoinen model from met-data or standard atmosphere.
  Also deferred.

When the IQ generator side populates real α/β, the PVT solver's Iono
correction hook should live here. Until then, running iono-OFF is
correct for our synthesized IQ.

---

## Firmware Port Notes

- scipy `linalg.cholesky` → hand-rolled `ldlT` or unpacked `llT`
  (6 × 6 max for PVT + velocity). See `docs/implementation-plan-firmware-port.md`.
- scipy `linalg.solve` → back-substitution after Cholesky.
- `numpy` vectorization → loops with SIMD intrinsics (A9 NEON).
- Determinism: avoid `pow`, transcendentals outside initialization;
  the iteration loop should be closed-form multiply-adds.

Precision: `double` is required for the `norm(x_est − sv_k)` step
(numbers on the order of 2e7 m; cm-scale precision needs ~50 bits).
Don't drop to `float`.

---

## Rate

Default `pvt_rate_hz = 5.0` (one fix every 200 ms). Driven by
`PVTSolver.should_solve(sim_time_s)` in `receiver._attempt_pvt_solve`.
Higher rates are possible but depend on observables-history window
length; at 5 Hz the history easily covers a common strobe with
margin.

GNSS-SDR emits observables per `d_T_rx_step_ms` (default 20 ms,
i.e., 50 Hz) and PVT consumes at that rate. Our 5 Hz matches a
typical navigation output rate, not their raw observable rate — not
an accuracy concern, but worth noting when cross-comparing fix
counts.
