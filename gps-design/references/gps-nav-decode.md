# GPS Nav Decode — LNAV Subframe Semantics

This chapter is **semantics only** (preamble, parity, HOW/TOW,
ephemeris fields). For **timing** — how `sample_counter` +
`code_phase_samples_residual` map to `tow_s` in the emitted
observable, and where that mapping breaks — see
`pseudorange-anchoring.md`.

**Authoritative pointers:**

- `gps_receiver/blocks/ps_b_telemetry_decoder.py` — PS.TLM. The
  consolidated block: bit accumulator (majority-vote 20 per-ms
  signs → symbol), preamble sync (`d_stat` 0 → 1 state machine),
  parity (`gps_word_parity_check`), subframe field decode
  (`_decode_sf1_fields` / `_decode_sf2_fields` / `_decode_sf3_fields`),
  TOW forward projection.
- `gps_receiver/nav_gen/` — LNAV subframe encoder + ephemeris
  builder, the ground-truth inverse used by scenario tests.
- `.research/session-20260416-215648/repos/gnss-sdr-extracts/gps_l1_ca_telemetry_decoder_gs.{cc,h}` —
  the reference implementation PS.TLM ports.
- **IS-GPS-200 §20.3** — LNAV message structure (preamble, HOW,
  subframes 1–5, ephemeris fields, URA, IODC/IODE).

---

## LNAV Invariants (from IS-GPS-200)

- **Subframe:** 300 bits = 10 words × 30 bits = 6 s at 50 bps.
- **Preamble:** 8 bits `10001011` at the start of every subframe's
  TLM word. Two consecutive preambles 6 s apart is the
  frame-synchronization criterion.
- **HOW (word 2):** 17-bit TOW count (bits 1–17), 3-bit subframe ID
  (bits 20–22). TOW is expressed in 6-second units and refers to
  **SV-transmit time at the leading edge of the NEXT subframe**.
- **Parity:** Hamming(32,26) with D29\*/D30\* (prior-word parity bits)
  carried between consecutive words. Two equivalent formulations
  verify the same bits — GNSS-SDR's XOR-mask form and SoftGNSS's
  Hamming-matrix form. PS.TLM uses the XOR-mask for frame
  synchronization and retains the Hamming form for the
  data-bit-extraction path.
- **Polarity:** when the PLL is 180° phase-locked (i.e., all nav
  bits inverted), parity check fails on correctly-decoded bits.
  PS.TLM retries with inverted bits; on match, flips
  `_flag_PLL_180_deg_phase_locked` for all subsequent words.
- **Bit period = 20 code periods exactly.** Nav bit boundaries
  always coincide with C/A code rollovers in the received signal
  (not necessarily in the receiver's fixed sample grid — see
  `pseudorange-anchoring.md`).

---

## Ephemeris Extraction

Subframes 1 / 2 / 3 carry one complete ephemeris set, repeated every
30 s. Fields per subframe:

- **SF1:** `wn_mod1024`, `code_l2`, `ura`, `sv_health`, `iodc`, `tgd`,
  `toc`, `af0`, `af1`, `af2`. Clock-correction parameters.
- **SF2:** `iode`, `Crs`, `delta_n`, `M0`, `Cuc`, `e`, `Cus`,
  `sqrt_a`, `toe`, `fit_interval_h`. Keplerian core.
- **SF3:** `iode`, `Cic`, `omega0`, `Cis`, `i0`, `Crc`, `omega`,
  `omega_dot`, `i_dot`. Keplerian perturbations + rates.

`PS.TLM._ephemeris` accumulates SF1+2+3 into a single dict. Consume
via `get_ephemeris()` after all three subframes have been decoded with
matching IODE. `try_build_ephemeris(prn, reference_wn, sf_cache)` in
`receiver.py:_drive_nav_decode` turns that dict into a
`scenario_engine.Ephemeris` usable by PS.B12.

**Not decoded in the current runtime:** SF4 / SF5 content (almanac,
ionospheric model, UTC parameters). Parity is checked but fields are
discarded. Relevant gap: `nav_gen` doesn't populate SF4 page-18
Klobuchar α/β coefficients — tracked in
`gps_receiver/threads/gps_iq_gen/20260419-iq-gen-tau-convention-fidelity/`.

---

## TOW Forward Projection

PS.TLM installs `_TOW_at_current_symbol_ms = tow_6s × 6000` at the
dump emitting the **last symbol of the decoded subframe**. Subsequent
symbols advance `_TOW_at_current_symbol_ms += 20` each via
`_push_symbol`. Between decodes, TOW increments **per symbol**, not
per millisecond — dumps within a 20-ms symbol window share the same
`tow_at_current_symbol_ms` value. Only when `symbol_emitted=True`
does the receiver attach the label to the dump via
`Observables.push(..., flag_valid_word=True)`.

This is the GNSS-SDR pattern; it means labeled packets are 20 ms
apart. PS.B13's `interp_tow_at` linearly interpolates between the
newest two labeled packets to land TOW at an arbitrary rx strobe.
The per-dump packets without labels still contribute to pseudorange
interpolation via `emit_pseudoranges` on the `_channels` deque.

---

## TOW Continuity Check

`_is_tow_consistent` (ported from
`gps_l1_ca_telemetry_decoder_gs.cc:458`) rejects a decoded TOW whose
predicted-vs-actual error exceeds 2 s. Needed because a spurious
preamble+parity match can occur; the continuity gate prevents a
single bad TOW from rewriting the receiver's GPS-time anchor.

On first decode the check is a no-op (no prior anchor); subsequent
decodes compare against `_last_decoded_tow_s + elapsed_symbols ×
20 ms` rounded to seconds.

---

## Common Debug Scenarios

| Symptom | First check |
|---|---|
| `d_stat` stuck at 0 (no frame sync) | PLL lock quality and CN0 — preamble correlation magnitude is proportional to amplitude |
| `d_stat = 1` but parity keeps failing after first subframe | Polarity flip mid-stream (PLL cycle slip); inspect `_flag_PLL_180_deg_phase_locked` |
| TOW skips by 6 s | `_is_tow_consistent` false-positive rejection, or missed subframe (look for gap > 3 × 6 s) |
| `_TOW_at_current_symbol_ms` not monotonic | Symbol counter off or `_push_symbol` not firing on `symbol_emitted` dumps |

`gps_receiver/tests/test_ps_tlm_telemetry_decoder.py` is the authoritative behavioral test.
