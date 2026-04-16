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

- **Atmospheric losses** (tropospheric delay, ionospheric delay)
  simplified or absent. For ground receivers at mid-latitudes this is
  small enough to ignore for test validity, but matters for absolute
  accuracy benchmarking.
- **Relativistic corrections** simplified -- SV clock rate is exact
  per IS-GPS-200 but second-order effects are not modeled.
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
