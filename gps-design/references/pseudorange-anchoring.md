# Pseudorange Anchoring -- SV Transmit-Time Recovery

The GPS receiver's position solution requires accurate measurement of
when each satellite's signal was *transmitted* (SV-time) relative to
when it was *received* (rx-time). The difference, scaled by the speed
of light, is the pseudorange. This reference covers the anchor chain
that establishes the SV-time reference, the SoftGNSS pattern that our
receiver adopts, and the debug methodology that localizes bugs in it.

**Scope:** the chain from PS.B10a preamble detection through PS.B13
anchor-point management. Does NOT cover the PVT solver (PS.B12) that
consumes the pseudoranges -- that's `gps-pvt.md`.

---

## 1. The Anchor Chain

```
PL.B3 (correlator)
    |  per-code-epoch: dump_end_sample_idx, IP/QP
    v
PS.B10a (preamble sync)            PS.B10 (nav bit extract)
    |  detects 8-bit TLM preamble     |  accumulates 20 code epochs
    |  emits BitPhaseLockEvent with   |  emits NavBitObservation with
    |   preamble_start_sample_idx      |   bit_end_sample_idx
    +---- aligns 20-ms grid  --------->+
                                       |  per-bit to PS.B11
                                       v
                                   PS.B11 (subframe decode)
                                       |  parity, HOW/TOW, subframe ID
                                       |  emits subframe_end_sample_idx
                                       |   = last_obs.bit_end_sample_idx
                                       v
                                   PS.B13 (pseudorange measurement)
                                       |  set_anchor_point(anchor_abs, sv_time_s)
                                       |  deque of dump records
                                       v
                                   measure_tx_time(strobe)
                                       |  linear interp between bracketing dumps
                                       v
                                   PS.B12 (PVT solver)
```

Four blocks participate. `PS.B13` owns the stored anchor and the
interpolation; the other three contribute the inputs.

### The claim

When `receiver.py` calls `PS.B13.set_anchor_point(anchor_abs, sv_time_s)`,
it's asserting: *"at rx-sample `anchor_abs`, the signal being received
has SV-time `sv_time_s`."* PS.B13 uses that assertion + its dump history
to interpolate SV-time for any arbitrary rx-sample within the history
window.

**The anchor claim is the single most important line in pseudorange
measurement.** Every PVT residual depends on it being right. A 1 ms
error in sv_time_s translates to ~300 km of position error.

---

## 2. IS-GPS-200 TOW Convention

IS-GPS-200 section 20.3.3.2 specifies that the TOW count in the HOW
word refers to the SV-transmit-time **at the leading edge of the
*next* subframe**, not the current one. That is:

```
HOW in subframe N contains TOW count T
  =>  next subframe (N+1) starts transmitting at SV-time T * 6 seconds
  =>  current subframe (N) ends at SV-time T * 6 seconds
  =>  current subframe (N) began transmitting at SV-time (T - 1) * 6 seconds
  =>  preamble of current subframe started at SV-time (T - 1) * 6 seconds
```

This is the convention every reference implementation uses. When
anchoring SV-time to an rx-sample, you must pick which subframe
position you're referencing:

- Anchor at **subframe end** (tow boundary): `sv_time = tow_6s * 6.0`
- Anchor at **current subframe preamble start**: `sv_time = (tow_6s - 1) * 6.0`
- Anchor at **end of first 1-ms of preamble bit 0**:
  `sv_time = (tow_6s - 1) * 6.0 + 0.001` (SoftGNSS convention)

All three are mathematically equivalent **if** the rx-sample-to-sv-time
mapping is exact. In practice they aren't, and the choice of anchor
point determines which errors are *claimed* vs which are *absorbed* by
PS.B13's interpolation.

---

## 3. The SoftGNSS Reference Pattern

SoftGNSS (Borre/Akos/Plausinaitis) is the reference implementation for
this chain. Read it at `.research/session-20260414-132039/repos/SoftGNSS/`.
The key files and lines:

### findPreambles.m:139
```matlab
firstSubFrame(channelNr) = index(i);
```
Records the ms-index where a valid TLM preamble starts, **at detection
time**. Validated by parity check at lines 133-134 (`navPartyChk`).

### postNavigation.m:155-161
```matlab
for channelNr = activeChnList
    svTimeTable(channelNr).PRN = trackResults(channelNr).PRN;
    for i = 1:settings.msToProcess
        svTimeTable(channelNr).time(i) = ...
            TOW - subFrameStart(channelNr)*0.001 + (i-1)*0.001;
    end
end
```
Builds the SV-time table **directly from the preamble position**. Every
ms sample advances SV-time by exactly 1 ms. There is NO per-bit
bookkeeping. There is NO "subframe end" concept.

### findTransTime.m:63-75
```matlab
for channelNr = 1:length(readyChnList)
    index_a = max(find(trackResults(channelNr).absoluteSample <= sampleNum));
    index_b = min(find(trackResults(channelNr).absoluteSample >= sampleNum));
    % ...
    transmitTime(channelNr) = interp1(x2, y2, index_c);
end
```
Linear interpolation between bracketing dumps for any requested rx-sample.
Matches our `PseudorangeMeasurement.measure_tx_time`.

### The 6-second bias quirk

SoftGNSS's formula `TOW - subFrameStart*0.001 + (i-1)*0.001` has an
implicit 6-second bias relative to IS-GPS-200 (it writes
`svTimeTable(subFrameStart) = TOW - 0.001`, not `(TOW - 6) + 0.001`).
The PVT solver absorbs this as a constant clock-bias offset common to
all satellites. Position-only applications don't care; absolute-time
applications do.

**Our receiver uses the IS-GPS-200-correct version explicitly:**
`sv_time_at_preamble_first_ms_end = (tow_6s - 1) * 6.0 + 0.001`.

---

## 4. Our Receiver's Anchor Convention (current)

After the fix landed on 2026-04-16 (see
`gps_receiver/cursor-timing-debug-PLAN.md`), the receiver anchors
SoftGNSS-style at the preamble via forward-projection.

In `receiver.py` around line 1160, the anchor equation is:

```python
lock_ev = self.channels[prn]._last_preamble_lock_event
if (lock_ev is not None
        and lock_ev.preamble_start_sample_idx is not None):
    preamble_sample = float(lock_ev.preamble_start_sample_idx)
    rx_elapsed_s = (sf_end_sample_idx - preamble_sample) / float(self.fs)
    elapsed_subframes = int(round((rx_elapsed_s + 0.001) / 6.0))
    lock_tow_6s = tow_6s - float(elapsed_subframes) + 1.0
    sv_time_at_lock_preamble = (lock_tow_6s - 1.0) * 6.0 + 0.001
    sv_time_at_boundary = sv_time_at_lock_preamble + rx_elapsed_s
else:
    sv_time_at_boundary = tow_6s * 6.0   # legacy path
```

### Why forward-projection instead of direct preamble anchor

`PseudorangeMeasurement._samples` is a bounded deque (length
`max_history = 8000` dumps = 8 s). If PS.B10a's lock was >8 s before
the first decoded subframe (common on cold start due to two-preamble
confirmation), the preamble's rx-sample is no longer in the deque.
`set_anchor_point` would fail the "match within tol_samples" check.

The workaround: anchor at `sf_end_sample_idx` (always in the deque
because it's the most-recent dump) with `sv_time_at_boundary` computed
by forward-projecting from the preamble position. This uses the
preamble's STABLE captured sample_idx as the timing reference, but
places the anchor record at a recent deque entry.

### Why not anchor at `tow_6s * 6.0` directly (the old path)

Because that claims "at sf_end_sample_idx, SV-time = tow*6" without
actually verifying that `sf_end_sample_idx` is physically at the
subframe boundary. `sf_end_sample_idx` comes from
`PS.B11.subframe_end_sample_idx = last_obs.bit_end_sample_idx`, which
traverses 6 seconds of 20-ms bit-accumulation windows whose individual
boundaries may drift from physical bit boundaries due to tracking
loop imperfection. SoftGNSS avoids this by routing the anchor through
the preamble detector's single captured sample -- eliminates 6 seconds
of between-block error accumulation.

---

## 5. Three-Way Diagnostic Methodology

When the anchor chain is buggy, first-fix position error will be large
(>100 km). The three-way pattern localizes which block is wrong.

**Setup:** run a controlled scenario (`scenario_static` with
`scenario_engine`) and collect, per anchor event:

1. **`claimed_sv`** -- what the receiver's anchor equation says
   (`live_anchor_tow_s` in `anchor_events`).
2. **`iqgen_sv`** -- what the IQ generator actually embedded.
   Compute from `gps_scenario._build_scenario_profiles`'s
   `tx_offset = -obs.range_m / C`:
   ```python
   iqgen_sv(t_rx) = t_rx - euclid_range_at_rx(t_rx) / C
   ```
   No Sagnac, no SV clock bias, no light-time iteration. This is
   deliberately simple to match what's actually in the IQ samples.
3. **`oracle_sv`** -- what a fully physics-correct model says.
   `diagnose_anchor_truth.py:_iterated_truth`:
   ```python
   tau = 0.07
   for _ in range(6):
       sv = propagate_sv(eph, rx_gps_time_s - tau)
       sv_rot = sagnac_rotate(sv.ecef_m, OMEGA_E * tau)
       tau = |sv_rot - rx_ecef| / C
   oracle_sv = rx_gps_time_s - tau + sv.clock_bias_s
   ```

### Decision tree

| `oracle_sv` vs `iqgen_sv` | `claimed_sv` vs `iqgen_sv` | Verdict |
|---------------------------|----------------------------|---------|
| agree within < 1 us | agree within < 1 us | receiver is correct; chase non-timing sources |
| agree within < 1 us | disagree by 10s-1000s of us | **receiver bug** -- anchor chain is wrong (PS.B10a/B10/B11 attribution) |
| disagree by ~ SV clock bias (10 ns - 1 ms) | agrees with either | IQ gen physics model vs oracle convention mismatch -- investigate which is authoritative |
| large disagreements all around | n/a | likely scenario setup bug (wrong wn/tow/start_gps_s, or ephemeris mismatch) |

### What "agreement" looks like for this scenario class

For `scenario_static` with ideal `make_24sv_constellation`:

- SV clock bias (`af0 + af1*dt + rel`) ~ 10 ns
- Sagnac delay (for static ground receiver at ~34 degN) ~ 100-200 ns
- Light-time correction vs Euclidean-at-rx ~ 1 us

These cancel to sub-microsecond. An oracle-vs-iqgen spread of 0.4 us
across 6 SVs is normal. **Don't treat that as a bug** -- it's the
expected residual of the oracle adding physics the iqgen model
doesn't need for this scenario class.

### Worked example (2026-04-16 debug session)

Initial symptom: first-fix position error of 1015 km on
`scenario_static`. Per-SV anchor residuals (claimed - oracle):

| prn | residual us |
|-----|-------------|
|   6 | -3943       |
|  10 | +560        |
|  13 | +917        |
|  17 | +1172       |
|  20 | +1568       |
|  24 | +2760       |

Three-way diagnostic said `oracle_sv - iqgen_sv = 0.4 us spread`
(physics OK), `claimed_sv - oracle_sv = 6700 us spread` (receiver
wrong). Refuted the initial "IQ gen missing physics" hypothesis;
localized to receiver.

Preamble-alignment diagnostic said the preamble lock itself had only
~400 us sub-dump rounding residual. That meant the 3900-us spread was
entering AFTER preamble lock, between PS.B10a and PS.B13. The fix
switched to SoftGNSS-style preamble anchor; residual spread dropped
9.3x to 722 us, first-fix error dropped 5x to 204 km. See
`cursor-timing-debug-PLAN.md` in the project for the full narrative.

---

## 6. Common Bugs and How to Identify Them

### Bug A: anchor at subframe end without preamble reference

Symptom: large per-SV anchor residuals (100s of us to ms), non-monotonic
in range. The per-SV sign can be both positive and negative.

Root cause: the receiver is claiming `sv_time = tow*6` at
`sf_end_sample_idx` without verifying that `sf_end_sample_idx` is
physically at the subframe boundary. Accumulated tracking-loop
imperfection over 6 seconds of nav bits creates per-SV-specific offset.

Diagnostic: run `diagnose_anchor_truth.py --use-preamble-sync`
followed by `diagnose_preamble_alignment.py`. If preamble residuals
are small (< 500 us) and anchor residuals are large (> 500 us), this
is the bug.

Fix: switch to SoftGNSS-style anchor. See section 4.

### Bug B: integer-ms code-epoch mis-selection at preamble

Symptom: anchor residuals are integer milliseconds apart (e.g., 1000
us, 2000 us, 5000 us between adjacent SVs).

Root cause: PS.B10a picked the wrong 1-ms slot within the 20-ms nav
bit window. Each SV's preamble lands at a different sub-dump offset
and PS.B10a's `_evaluate_candidate` can resolve different 1-ms slots
per SV.

Diagnostic: `diagnose_preamble_alignment.py` decomposes residuals into
integer-20-ms / integer-1-ms / sub-ms components. If `int_epoch_err`
is nonzero for any SV, this is the bug.

Fix: tighten PS.B10a's preamble correlation threshold or improve its
slot-disambiguation logic. **Not observed** on the current receiver
-- `int_epoch_err = 0` across all SVs we've tested.

### Bug C: DLL pull-in sample_idx captured in preamble lock

Symptom: preamble residuals themselves are hundreds of us (not just
tens). First-fix position error remains at ~200 km even with SoftGNSS
anchor.

Root cause: PS.B10a buffers 1-ms prompt observations starting from
first dump, INCLUDING the DLL pull-in period when tracking hasn't
converged. The `dump_end_sample_idx` captured during pull-in is
systematically offset from the physical code-epoch boundary by the
residual code-phase error at that moment. When PS.B10a references a
buffer position from pull-in, it carries that offset into
`preamble_start_sample_idx`.

Diagnostic: compare `diagnose_preamble_alignment.py`'s sub-ms
residuals across SVs. If spread is ~500-1000 us and doesn't correlate
with any other physical quantity, and first lock happens within the
DLL pull-in time (~1 s), this is the cause.

Fix (Option A, not yet landed): delay PS.B10a's buffer admission until
DLL has converged. Conservative: require `dll_locked_epochs >= 1000`
(1 s at 1 kHz dump rate) before feeding prompt observations into PS.B10a.

### Bug D: IQ gen missing Sagnac / SV clock bias

Symptom: `oracle_sv - iqgen_sv` is nonzero by hundreds of us to ms.
`claimed_sv` matches `iqgen_sv` (receiver is faithful to its input).

Root cause: the scenario engine's `_build_scenario_profiles` embeds
only Euclidean range at rx-time. For scenarios where Sagnac or SV
clock bias are large (dynamic receivers, non-idealised ephemerides),
the physics the oracle expects isn't in the synthesized IQ.

Diagnostic: `diagnose_anchor_terms.py` reports
`(oracle - iqgen) - clock_bias - sagnac` as a residual. If that's
near zero, the oracle's added physics is what the IQ gen is missing.

Fix: `gps_scenario.py:481` should apply Sagnac + SV clock bias to the
tx_time_offset profile. **Not a bug for `scenario_static` on
idealised constellations** -- the terms cancel to sub-us.

---

## 7. PseudorangeMeasurement (PS.B13) API

### set_anchor_point(anchor_abs_sample_frac, sv_time_s, tol_samples=0.5) -> bool

Must be called AFTER at least one `append_dump`. Finds the deque entry
whose `abs_sample_frac` matches `anchor_abs_sample_frac` within
`tol_samples`, sets its `sv_time_s`, then propagates sv-times forward
and backward via `+/- t_dump_s` per entry. Returns False and leaves
the block unanchored if no match is found.

**Implication:** the anchor rx-sample MUST be within the
max_history-bounded deque. Anchoring 18 s back when max_history = 8 s
fails silently.

### measure_tx_time(strobe_sample_idx) -> Optional[float]

Linear interpolation between the two bracketing deque entries. Returns
None if:
- not anchored
- strobe is before the first logged dump
- strobe is after the last logged dump (no extrapolation; caller
  should retry on the next dump boundary)

### t_dump_s (property)

Set by the receiver via `set_dump_period()`. For 1-kHz dump rate,
`t_dump_s = 1 ms`. Used for forward/backward propagation during
anchor setup.

### max_history

Default 8000 (8 s at 1 kHz). This bounds both the deque length and
the anchor-lookback window. Increase if anchoring at a point > 8 s
before the first strobe is needed. At some point the cost of deque
management grows -- consider whether a different anchor strategy
(like forward-projection, section 4) is more appropriate.

---

## 8. Debugging Checklist

When first-fix position error is large (>100 km), or pseudoranges look
inconsistent:

1. **Scope the symptom.** Run `diagnose_anchor_truth.py
   --use-preamble-sync` on the failing scenario. Note per-SV anchor
   residuals and first-fix error.

2. **Run `ANCHOR_DECOMP=1 diagnose_anchor_truth.py --use-preamble-sync`**
   to see per-anchor-event residuals. Check drift: if zero across
   subframes, the per-SV offset is frozen at first anchor (anchor
   equation bug); if drifting at ~us/s rates, tracking is unstable
   (different bug in PS.B5 or PS.B7 -- consult `gps-tracking.md`).

3. **Run `diagnose_anchor_terms.py --duration-s 50`** for the three-way
   comparison. Classify per section 5's decision tree.

4. **If bug localizes to receiver anchor equation**, run
   `diagnose_preamble_alignment.py` to decompose preamble residuals.
   If `int_epoch_err = 0` and `sub_us` is bounded at < 500 us, the
   preamble is OK; bug is downstream in PS.B10/B11. If `int_epoch_err
   != 0`, the bug is in PS.B10a itself.

5. **Cross-check against SoftGNSS pattern** -- does our anchor equation
   match `postNavigation.m:155-161`? Route through the preamble, not
   the subframe end.

6. **If residuals remain after SoftGNSS-style anchor**, the floor is
   the preamble sub-dump rounding residual (Option A territory).
   See bug C.

### What NOT to do

- Don't change `gps_scenario._build_scenario_profiles` until you've
  verified via `diagnose_anchor_terms.py` that oracle and iqgen
  disagree. The IQ gen is cross-validated against GNSS-DSP-tools; it
  is almost certainly correct for the scenario class.
- Don't change `_iterated_truth` in `diagnose_anchor_truth.py` unless
  you've first verified via textbook (Kaplan Ch 2-3) what terms it
  should include. The oracle is deliberately more physics-complete
  than the IQ gen; that's not a bug.
- Don't "fix" PS.B11's preamble search convention before running
  `diagnose_preamble_alignment.py`. PS.B11 searches at every bit
  boundary (not word boundaries), which is a superset of the plan's
  contract, not a violation.
- Don't re-anchor on every subframe if PS.B10a's preamble_start_sample_idx
  is stable across subframes. A single forward-projection from the
  lock is more accurate than per-subframe re-anchoring.

---

## 9. Artifacts to Read Before Nontrivial Changes

| File | What to read for |
|------|------------------|
| `gps_receiver/cursor-timing-debug-PLAN.md` | Full history of the 2026-04-16 debug session; refuted hypotheses; the reasoning behind the current design |
| `gps_receiver/receiver.py` line ~1100-1200 | `_drive_nav_decode` and anchor equation -- where the fix lives |
| `gps_receiver/blocks/ps_b13_pseudorange.py` | `set_anchor_point`, `measure_tx_time`, deque management |
| `gps_receiver/blocks/ps_b11_subframe_decode.py` | `_try_sync`, `subframe_end_sample_idx` attribution |
| `gps_receiver/blocks/ps_b10a_preamble_sync.py` | `_evaluate_candidate`, `BitPhaseLockEvent` fields, preamble detection convention |
| `.research/session-20260414-132039/repos/SoftGNSS/` | `findPreambles.m`, `postNavigation.m`, `findTransTime.m` -- reference implementation |
| `.research/session-20260414-132039/CLAUDE.md` | Pre-digested summary of the SoftGNSS pattern |
| `docs/gps-scenario-engine/` | Scenario engine spec and integration plan; iq_gen conventions |

Read these in the order listed. `cursor-timing-debug-PLAN.md` is the
single most important file -- it captures the reasoning that led to
the current design, including the wrong turns that were refuted.
Understanding WHY a design exists matters more than what it does;
the code will always drift from design intent unless new work
reconstructs that intent.
