# Pseudorange Anchoring — SV Transmit-Time Recovery

Physics and debug methodology for recovering per-SV transmit time at
the receiver so `pseudorange = (t_rx − tow) × c` is correct. The
**specifics of the code** live in the project; this file is the
conceptual + debug-heuristic layer.

**Authoritative pointers (read these for implementation detail):**

- `gps_receiver/blocks/ps_b_telemetry_decoder.py` — PS.TLM (bit
  accumulator + preamble sync + subframe decode + TOW forward
  projection).
- `gps_receiver/blocks/ps_b13_observables.py` — PS.B13 (per-PRN
  observable history, TOW interpolation, pseudorange emission at a
  common strobe).
- `gps_receiver/blocks_map.json` — `timing_metadata_chain` section
  walks the `sample_counter + code_phase_samples_residual`
  propagation from PL.B3 through Observables, with per-hop formulas
  and file:line citations. **Read this before touching any timing
  field.**
- `.research/session-20260416-215648/repos/gnss-sdr-extracts/`
  (`hybrid_observables_gs.cc`, `dll_pll_veml_tracking.cc`,
  `gnss_synchro.h`) — the reference architecture the project mirrors.

If the skill and code disagree, **the code is authoritative.**

---

## 1. The Chain (Current)

```
PL.B3 correlator
    │  every 1 ms dump: sample_counter, code_phase_samples_residual,
    │                   is_bit_edge (from PL.B3a bit-sync histogram)
    ▼
PS.TLM (ps_b_telemetry_decoder.py)
    │  consumes per-ms prompts, majority-votes 20→1 symbols, does
    │  preamble sync, parity, LNAV decode. On decode, installs
    │  TOW_at_current_symbol_ms = tow_6s × 6000 and forward-projects
    │  by +20 ms per emitted symbol.
    ▼
PS.B13 Observables
    │  on every 1-ms dump, .push() with flag_valid_word set iff
    │  a symbol was emitted this dump. Keeps a deque of packets per
    │  PRN; the most recent two labeled packets define the TOW rate.
    ▼
emit_pseudoranges(strobe_t_rx_s)
    │  linear-interpolates tow_s at the common strobe from the
    │  newest two labeled packets; returns (strobe − tow_s) × c.
    ▼
PS.B12 PVT solver
```

The architectural predecessor (fixed-block correlator discarding
per-PRN acquired fractional chip phase) is documented with closure
evidence in
`gps_receiver/threads/receiver/20260419-arch1-migration/findings-2026-04-24.md`.
Read that first if you are debugging per-PRN pseudorange bias
signatures.

---

## 2. IS-GPS-200 TOW Convention

IS-GPS-200 §20.3.3.x fixes these invariants. They do not change with
project architecture.

- The HOW TOW count in subframe N refers to SV-transmit-time **at the
  leading edge of subframe N+1** (one subframe later). So if HOW says
  `tow_6s = T`, then subframe N started transmitting at
  `(T − 1) × 6 s` and ends at `T × 6 s`.
- The navigation bit period is exactly 20 C/A code periods. Bit
  boundaries **always coincide with C/A code-period rollovers** in
  the received signal. At any rx sample on a bit edge, the prompt
  replica is also at a code rollover.
- Bit sync must discover the 1-ms slot within a 20-ms cycle where nav
  bits transition; that slot is the per-PRN cycle-offset.

If the skill, a thread finding, or the code ever implies a bit
boundary falls **between** code rollovers in the physical signal,
that's a bug in the synthetic IQ (cf. the H3a sign fix in
`scenario_engine/geometry.py:compute_code_phase`), not in real GPS.

---

## 3. Three-Way Diagnostic Methodology

This project's hardest bugs live between blocks. The pattern that
consistently localizes them:

**Compare the receiver's claim against two independent physical-truth
sources.** If the two truths agree and the receiver disagrees, the
bug is in the receiver. If the truths disagree, the bug is at the
IQ-generator-vs-oracle convention boundary. If all three disagree,
chase the nearest anomaly first.

For timing work, the three columns are:

1. **`claimed`** — what the receiver reports (per-labeled-packet
   `tow_s`, or per-PVT-attempt pseudorange, or per-anchor-event
   tow_6s).
2. **`iqgen_truth`** — what the IQ generator actually embedded into
   the signal. Reconstruct via the scenario engine's
   `tx_time_offset_profiles` (per-PRN, per-epoch, dense — see
   `gps_scenario._build_scenario_profiles`), or directly from
   `rx_gps_time_s − euclid_range(rx_gps_time_s)/c`. No Sagnac, no SV
   clock bias, no light-time iteration — deliberately simple to
   match what's embedded.
3. **`oracle_truth`** — what a physics-complete model says. The
   canonical oracle form: light-time-iterated range + Sagnac rotation
   + SV clock bias:
   ```
   tau = 0.07
   for _ in range(6):
       sv = propagate_sv(eph, rx_gps_time_s - tau)
       sv_rot = sagnac_rotate(sv.ecef_m, OMEGA_E * tau)
       tau = |sv_rot - rx_ecef| / c
   oracle_tow = rx_gps_time_s - tau + sv.clock_bias_s
   ```

### Decision tree

| `oracle` vs `iqgen` | `claimed` vs `iqgen` | Verdict |
|---|---|---|
| agree within ~1 µs | agree within ~1 µs | receiver is correct; chase non-timing sources |
| agree within ~1 µs | disagree by tens–hundreds of µs | **receiver bug** — localize via single-block instrumentation |
| disagree by ~SV-clock-bias-scale (10 ns – 1 ms) | agrees with either | IQ gen vs oracle convention mismatch — decide which is authoritative for the scenario class |
| large spread across the board | — | scenario setup bug (wrong `wn`/`tow`/`start_gps_s`/ephemeris) |

### Expected residuals (idealised scenario)

For `scenario_static` with the default `make_24sv_constellation`
ephemerides (zero SV clock drift, zero tgd, zero atmospheric delay):

- SV clock bias: ~10 ns
- Sagnac (static ground receiver at mid-latitudes): ~100–200 ns
- Light-time vs Euclidean-at-rx: ~1 µs

These cancel to sub-µs. A per-PRN oracle-vs-iqgen spread of 0.4 µs
across 6 SVs is normal for this scenario class. **Don't treat that as
a bug** — it's the oracle adding physics the IQ generator doesn't
need. The residuals tracked in
`gps_receiver/threads/gps_iq_gen/20260419-iq-gen-tau-convention-fidelity/`
are the current project-level gaps (missing `tgd`, zero Klobuchar).

---

## 4. Symptom-to-Investigation Map

| Symptom | Likely locus | First diagnostic |
|---|---|---|
| First-fix error > 100 km, per-PRN pseudorange offsets are **stable per PRN** and correlate with `range_k/c mod 1 ms` | Dump-alignment in correlator (sample_counter grid vs received code-rollover grid) | Compare `sample_counter mod 4096` across PRNs. If non-zero and per-PRN constant, fixed-block-vs-cursor is the issue. See `findings-2026-04-24.md`. |
| First-fix error > 100 km, per-PRN offsets **integer-ms apart** | Bit-sync histogram picked wrong 1-ms slot for one or more PRNs | Look at PL.B3a histogram peak vs runner-up per PRN; confirm `is_bit_edge` alignment |
| First-fix error > 100 km, per-PRN offsets **drift with time** | Clock-bias or rate estimation in PS.B12, or TOW forward-projection drift in PS.TLM | Check `_TOW_at_current_symbol_ms` per-symbol increments == 20 ms exactly |
| All pseudoranges shifted by a common offset (10s of km) | Common-mode issue (absorbed by PVT clock bias; check clock_bias magnitude) | Compute `median_clock_bias_s × c`; if it equals the shift, benign |
| `oracle_sv − iqgen_sv` nonzero at hundreds of µs but `claimed` matches `iqgen` | IQ generator missing a physics term the oracle models | Bypass test: simpler oracle that matches iqgen's model |

For concrete examples of each symptom producing a finding, see:

- `gps_receiver/threads/receiver/20260419-arch1-migration/findings-2026-04-24.md` — dump-alignment (the row-1 symptom).
- `gps_receiver/threads/receiver/20260421-gnss-sdr-comparative-pipeline/findings-2026-04-22.md` — the H3a IQ-generator sign fix (row-5 symptom).

---

## 5. Debug Ordering

Before making changes:

1. Read `gps_receiver/CLAUDE.md` and the arch1-migration thread's
   `handoff.md` "Current state" block. The current-known state is
   not in this skill file.
2. Scan `.research/session-*/` for the relevant topic. Many answers
   are already investigated.
3. Run an existing live diagnostic under
   `gps_receiver/threads/receiver/*/diagnostics/` before writing a
   new one. The three-way pattern is embodied in several of them.
4. Check `blocks_map.json`'s `timing_metadata_chain` for the current
   formulas and per-hop file:line pointers.
5. Apply the three-way methodology (§3) to localize. **Predict** the
   outcome in writing before running; a refuted prediction is a
   finding.

### What NOT to do

- Don't edit `gps_scenario._build_scenario_profiles` or
  `scenario_engine/geometry.py:compute_code_phase` without
  independent evidence from the three-way pattern that the IQ side
  is wrong. The IQ generator is cross-validated against
  GNSS-DSP-tools and (post H3a fix) aligned with IS-GPS-200 physics.
- Don't edit the oracle's physics set (`propagate_sv`, Sagnac,
  light-time) without a textbook citation. The oracle is
  deliberately physics-complete; it is not a bug that it includes
  terms the IQ generator omits.
- Don't chase the median-of-a-per-PRN-error as the fix target when
  the std is the story. Common-mode is absorbed by PVT clock bias;
  only between-PRN variation maps to position error.

---

## 6. Project Threads for Cross-Reference

| Thread | Relevance |
|---|---|
| `receiver/20260419-arch1-migration/` | Active. Plan-02 landed the PL.B3 emit-format port, consolidated PS.TLM, and introduced Observables. Plan-03 hop 1 (2026-04-24) localized the remaining 257 km residual to the fixed-block correlator and validated the cursor path at 200 m. |
| `receiver/20260421-gnss-sdr-comparative-pipeline/` | GNSS-SDR on the same canonical IQ fixture (sha256 `69fa22a1…`) reaches 174 m with `PVT.iono_model=OFF` — the project's PASS-gate oracle. |
| `gps_iq_gen/20260419-iq-gen-tau-convention-fidelity/` | IQ-side physics gaps (missing `tgd`, zero Klobuchar α/β). Accounts for the final ~25 m gap between our 200 m and GNSS-SDR's 174 m. |

Closed threads retain their findings; read them as signed
investigation history, not as current-state docs.
