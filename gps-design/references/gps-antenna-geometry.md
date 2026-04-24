# GPS Antenna Geometry and Link Budget

**Status: Stub.** Expand when geometry / link-budget work surfaces
non-trivial design / debug questions.

This chapter covers the scenario-engine-side physics that feeds the
IQ generator -- SV position propagation, antenna gain, free-space
path loss, elevation masking, cislunar visibility geometry. These
are NOT receiver-side concerns; the receiver consumes C/N0 as a
scalar and doesn't know about geometry. But getting geometry right
is essential for end-to-end test validity.

---

## What This Chapter Will Cover

- WGS84 ECEF coordinate system and LLA conversions (coordinates.py)
- Receiver trajectory models:
  - `StaticReceiver` -- fixed ECEF (default: Huntsville BS)
  - `LunarStaticReceiver` -- fixed ECI at lunar surface
  - `WaypointTrajectory` -- interpolated waypoints
  - `OEMTrajectory` -- CCSDS OEM-format ephemeris
- Orbit propagation (Keplerian + perturbations, Thompson 2019)
- Range / range-rate computation (geometry.py)
- Elevation / azimuth / range (AER) from receiver viewpoint
- SV antenna gain pattern (IIF / III) -- off-boresight angle
- Receiver antenna gain pattern -- directional for lunar / anti-jam
- Link budget -- FSPL + antenna gains + noise figure -> C/N0
- Earth occultation check for cislunar scenarios
- Visibility transitions (elevation mask crossing, occultation)
- Sagnac rotation (applies to pseudorange computation on the rx side)
- Relativistic correction (applies to SV clock bias on the SV side)

## Current Implementation

`scenario_engine/` package:

| File | Purpose |
|------|---------|
| `scenario_engine.py` | Main orchestrator: `ScenarioEngine`, `ScenarioEpoch`, `SVObservation` |
| `orbit_propagator.py` | Keplerian SV propagation, `make_24sv_constellation()` |
| `trajectory.py` | `StaticReceiver`, `WaypointTrajectory`, `OEMTrajectory` |
| `coordinates.py` | WGS84 ECEF/LLA conversions |
| `geometry.py` | Range, Doppler, AER, Earth occultation |
| `link_budget.py` | Free-space path loss, C/N0 computation |
| `antenna_patterns/` | SV (IIF/III) and receiver antenna gain models |

## Research Already Done

| Session | Content |
|---------|---------|
| `.research/session-20260328-203606` | Cislunar GPS on FPGA (AGGA-4, LuGRE, weak-signal) |
| `.research/GPS-SV-velocity-and-acceleration.pdf` | Thompson 2019 velocity/acceleration |

## Key Constants

Basestation (Huntsville static) ECEF: `(315873, -5242406, 3607328)` m.
LLA: `34.663420 degN, 86.551901 degW, 194.2 m (WGS84)`.

Other key constants live in `scenario_engine/constants.py` --
OMEGA_E, MU_EARTH, GPS_WEEK_SECONDS, WGS84 parameters.

## Known Gaps

Split cleanly by layer so the fix lands in the right place:

- **IQ-generator side (link-budget / CN0):** atmospheric obliquity-
  factor CN0 loss (0.3–10 dB range) **is implemented** in
  `scenario_engine/link_budget.py`. Verified by the head-to-head
  atmospheric ablation in
  `gps_receiver/threads/receiver/20260421-gnss-sdr-comparative-pipeline/`.
- **IQ-generator side (nav message content):** `nav_gen` does NOT
  yet populate SF4 page-18 Klobuchar α/β or `tgd`. Tracked in
  `gps_receiver/threads/gps_iq_gen/20260419-iq-gen-tau-convention-fidelity/`.
  Accounts for the final ~25 m gap between our post-cursor-path
  PVT (200 m) and GNSS-SDR's oracle (174 m).
- **Receiver / PVT side (iono / tropo corrections):** PS.B12 does
  not apply Klobuchar or Saastamoinen. Not a priority until the
  nav_gen α/β gap is closed; running `iono=OFF` against the IQ
  with zero α/β is the correct behavior.
- **Relativistic corrections:** SV clock rate is exact per
  IS-GPS-200 but second-order effects are not modeled.
- **Solar pressure / gravity gradient perturbations** not modeled
  (orbit propagator is Keplerian only). Acceptable over minutes,
  may matter over hours.

## When to Expand This Chapter

- Writing / modifying `scenario_engine/` files.
- Adding new trajectory types (rover, aircraft, LEO, HEO).
- Debugging C/N0 profiles that don't match expected link-budget
  values.
- Adding cislunar-specific antenna patterns or occultation cases.
- Generating validation scenarios for absolute-accuracy benchmarking.
