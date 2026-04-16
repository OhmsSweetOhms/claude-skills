# GPS Nav Decode -- PS.B11 (LNAV Subframe Decode)

**Status: Stub.** Expand this chapter when nav-decode work surfaces
non-trivial design / debug questions.

This chapter covers **semantics** only (parity, HOW/TOW extraction,
ephemeris field decoding). For **timing** questions -- specifically
the `subframe_end_sample_idx` attribution and anchor-point claim --
see `pseudorange-anchoring.md`. The two concerns are deliberately
separated.

---

## What This Chapter Will Cover

- TLM preamble (8 bits: 10001011) and HOW word structure
- Hamming(32,26) parity check with D29*/D30* carry between words
- TOW (17-bit count of next-subframe start in 6-s units)
- Subframe ID (3 bits: 1-5)
- Polarity resolution (+1 / -1 bits -- the receiver flips if parity
  fails consistently)
- SF1 fields: week number, SV health, IODC, clock corrections (af0/1/2)
- SF2/SF3 fields: ephemeris (Keplerian parameters + perturbations)
- SF4/SF5 fields: almanac + ionospheric model (v1: parity-check only,
  ignore content)
- Multi-subframe chaining (rolling SF1+2+3 accumulator for one valid
  ephemeris)
- Re-sync on parity failure (loss-of-lock recovery within the
  nav-decode layer)

## Current Implementation

`gps_receiver/blocks/ps_b11_subframe_decode.py` -- SubframeDecoder
class. Key design choices:

- Searches for preamble at **every bit boundary** (not just word
  boundaries -- the plan called for word-boundary-only but the impl
  is a superset, safe to keep).
- Two-preamble confirmation is done by PS.B10a, not PS.B11.
- Polarity is resolved on the fly -- if a preamble match fails parity
  in one polarity, re-searches with inverted bits.
- Emits `subframe_end_sample_idx` / `subframe_start_sample_idx` from
  the bounding NavBitObservation fields. **Those fields are
  downstream-timing-sensitive; see pseudorange-anchoring.md.**

## Research Already Done

- IS-GPS-200 section 20.3.3.x covers the LNAV message structure.
- SoftGNSS's `ephemeris.m` is the reference implementation.
- `.research/session-20260328-222755/repos/python-gps-receiver` is
  our local reference for Python-style nav decoding.

## Known Gaps

- SF4/SF5 content (almanac, ionospheric model) not decoded.
- Iono model for single-frequency corrections not yet applied in PVT.
- Ephemeris validity (IODE chaining, refresh trigger) not fully wired.

## When to Expand This Chapter

- Writing or debugging `ps_b11_subframe_decode.py`.
- Adding L1 iono corrections to PVT.
- Implementing almanac-driven sky-plot / weak-signal cold-start guidance.
- Porting PS.B11 to bare-metal C (the Hamming parity logic needs care).
