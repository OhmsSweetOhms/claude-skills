# Ingesting a foreign IQ capture

**Scope — read this only for the rare one-off:** an IQ capture from an
**outside source** (a public dataset, a colleague, another rig) that was sampled
**how *they* chose** — some foreign rate, bit depth, layout, and center
frequency. The job is to land it on our analysis rails and run our
receiver/analysis on it.

**This is NOT the path for our own captures.** When we capture our own IQ we
choose the rate (4.096 MHz, or a clean multiple), so it lands on-rail by
construction — replay it as an `.iq16` (the v2 scenario `signal_source: live`,
`source: file`, `iq_path` stub is shaped for exactly that). Foreign captures are
the oddball because the sampling is out of our control; everything below exists
to absorb that one variable.

## The principle

The variable part of a foreign capture is **how it was sampled** — that's
*data* (rate / dtype / layout / center / band), passed as arguments. The
processing **spine is fixed and already first-class** (library functions, not a
script):

```
read_iq_raw(...)  →  to_receiver_rail(...)  →  cold-start GPSReceiver  →  C/N0 / PSD / detector
```

Don't write a new per-dataset script; call the spine with the capture's
parameters. (The three real-IQ thread diagnostics that each re-derived the
JT23 `30.69 MHz / int8 / interleaved` chain are the anti-pattern — see
"Consolidation" below.)

## Step 1 — read it as-sampled (`gps_iq_gen/iq_io.py`)

```python
from gps_iq_gen.iq_io import read_iq_raw
iq = read_iq_raw(path, dtype="int8", layout="iq_interleaved", count=None)  # → complex64
```

- `dtype`: per-component sample type (`int8` / `int16` / `float32` / …, or an
  `np.dtype`). Headerless raw is assumed.
- `layout`: `iq_interleaved` (`I0 Q0 I1 Q1 …`) or `qi_interleaved`.
- `count`: number of *complex samples* to read (None = whole file). Capping never
  splits a sample — use it to grab a leading chunk of a huge file.
- **No scaling / normalization** — int → complex64 verbatim. The capture's
  amplitude is **uncalibrated** (a 3-bit capture has arbitrary dBFS). This is
  load-bearing for Step 3.

## Step 2 — land it on the rail (`gps_iq_gen/rails.py`)

```python
from gps_iq_gen.rails import to_receiver_rail, to_wideband_rail
rail, fs = to_receiver_rail(iq, fs_in=30_690_000.0, f_shift_hz=0.0)   # → (complex64, 4.096e6)
# wide analysis grid (49.152 = 12×4.096, exact) when you need the full span:
wide, fsw = to_wideband_rail(iq, fs_in=30_690_000.0)
```

- **One shared kernel** (`to_rail`): an optional complex mix by `−f_shift_hz`,
  then an **exact rational polyphase resample** (`scipy.signal.resample_poly` —
  real anti-alias FIR + rational rate change, never an FFT brick-wall). The ratio
  is reduced exactly (e.g. 30.69→4.096 = `2048/15345`); non-clean ratios work,
  the filter is just long (fine offline, not for a real-time budget).
- **`f_shift_hz` recenters a band before decimation.** The anti-alias keeps only
  ±2.048 MHz around DC, so an off-center band of interest must be mixed to
  baseband first or it's filtered away. (A capture already centered on L1 →
  `f_shift_hz=0`.)
- **Same kernel for real and synthetic.** If you ever compare a foreign capture
  to a synthetic recreation, both must flow through `to_rail` or the scorecard
  compares decimation-filter artifacts, not signal content.

## Step 3 — run the receiver (cold-start; unknown sky)

A foreign capture has no scenario/ephemeris context, so warm-seeding
(`run_receiver_on_iq_file`, `--stream-replay`) does **not** apply — use the
in-RAM cold-start path directly:

```python
import numpy as np
from gps_receiver.receiver import GPSReceiver, load_profile

rx = GPSReceiver(profile=load_profile("open_sky"))   # profile must be a DICT, not a name
# acquisition needs coherent_ms × noncoherent_dwells samples (open_sky: 1 ms × 80 = 80 ms):
n_acq = int(0.100 * fs)
acq = rx.acquire(np.asarray(rail[:n_acq], np.complex128), prn_list=list(range(1, 33)))
rx.load_iq_source(np.asarray(rail, np.complex128))
while rx.can_process_one_code_per_channel():
    res = rx.process_one_code_per_channel()          # per-PRN dict: cn0_dbhz, is_locked, …
```

Acquiring a sensible PRN set with coherent C/N0 (and one distinct from any
synthetic constellation) is the evidence the recovery is genuine, not leakage.

## Step 4 — analysis (`analysis/`)

- **PSD shape** vs another rail: `analysis.compare_rails(real, synth, fs, metrics=[...])`
  (flatness / peak-over-median / occupied-BW / in-band density). Use the
  **scale-invariant** metrics — amplitude is uncalibrated (Step 1).
- **Interference detection:** `analysis.detect_interference(cnr_by_signal, …)` on
  the per-SV C/N0 series. **It detects an *onset*** (the 2nd-difference of a step),
  so it needs (a) a real H0→H1 **transition** in the record and (b) more than
  `2·l+1 = 13` epochs at 1 Hz (>~13 s). A short clip or a steady-state
  (always-on) capture cannot exercise it — source σ from a separate clean/H0
  reference and say so; never splice a fake transition.

## Caveats (the hard-won ones)

- **Uncalibrated amplitude** → scale-invariant metrics + relative C/N0 *drops*,
  never absolute power, unless you calibrate from a documented EIRP/link budget.
- **Anti-alias keeps only the in-band slice** (±2.048 MHz). A wideband jammer's
  out-of-band energy is filtered out — in-band J/N ≪ total J/N (why a receiver
  can still acquire under a "strong" wideband jammer).
- **Don't commit raw IQ** — large and often license-bound. Keep it in gitignored
  `temp/`; record provenance + license + the exact byte format in a dataset
  register (see the `cross-cutting/20260606-real-iq-validation-anchor` thread's
  `data/datasets.json` for the pattern).

## Consolidation (when the next foreign capture shows up)

The read→rail→cold-start spine is currently open-coded in three thread
diagnostics (`ingest_fgi_jsdr.py`, `scorecard_jt23_4_1_5.py`,
`validate_detector.py::run_real_capture` under
`.threads/cross-cutting/20260606-real-iq-validation-anchor/diagnostics/`). When a
second foreign source arrives, promote the spine into **one helper**
`gps_iq_gen.ingest_capture(path, *, dtype, layout, fs_in, f_shift_hz=0.0,
rail="receiver", run=False)` (a small refactor, not a thread) and collapse those
diagnostics onto it. Until then, the building blocks above are the recipe. Do
**not** wire `signal_source: live` into the scenario stack for this — that's the
mainline "real capture as a first-class scenario" future, which our own
clean-rate captures don't need.

## Worked reference

`cross-cutting/20260606-real-iq-validation-anchor` (FGI Jammertest 2023,
JT23_4.1.5): headerless int8 interleaved @ 30.69 MHz, center L1 →
`read_iq_raw(dtype="int8")` → `to_receiver_rail(fs_in=30.69e6)` (2048/15345) →
cold-start receiver acquired 8 real GPS PRNs under the jammer. See that thread's
`findings-2026-06-06-detector-validation.md` and `diagnostics/validate_detector.py`.
