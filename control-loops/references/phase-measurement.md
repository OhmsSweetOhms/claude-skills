# Measuring carrier phase in the NCO frame

Think of the NCO as the moving camera and prompt phase as the small movement
left in the picture. Add the camera's known movement back only after measuring
that small residual. Unwrapping the restored carrier can throw away real turns.

Use `scripts/measure_carrier_phase.py` for offline BPSK prompt-epoch analysis
when diagnosing carrier wander, comparing loop bandwidths, or separating
tracking error from the carrier trajectory. It does not modify a loop or read
raw RF. NumPy is the only dependency. GPS capture/selection guidance lives in
`gps-design/references/carrier-wander-replay.md` in the shared skills repository.

## Inputs and invocation

Prepare a numeric NPZ containing:

- `time_s`: one-dimensional actual dump midpoint times in seconds.
- `prompt`: complex I+jQ, shape `(epochs,)` or `(..., epochs)`.
- `nco_phase_rad`: continuous applied NCO phase at those same midpoints,
  with final axis `epochs`, broadcastable to the prompt shape. Preserve turns.

The assumed mixer sign is `prompt = A * exp(j * (signal - nco))`. Verify this
in the producer. For constant frequency during a dump of N samples at fs,
its applied midpoint phase is `phase_start + pi * frequency_hz * N / fs`.
Accumulate with actual sample counts, including variable-length dumps. A
frequency updated after the dump belongs to the next dump. Do not substitute
nominal 1 ms timing or a wrapped NCO angle. A known linear reference may be
removed from NCO phase for numerical conditioning; record that reference.

From the control-loops skill directory:

```bash
python3 scripts/measure_carrier_phase.py epochs.npz phase.npz --block-epochs 20
python3 -m unittest discover -s scripts -p 'test_measure_carrier_phase.py'
```

Choose the block count before inspecting the phase result. Twenty epochs was
validated for specific approximately 1 ms GPS dumps, noise levels, and motion;
it is not a universal 20 ms setting. Longer blocks reduce noise but also smear
motion and increase aliasing risk. The script counts epochs, not milliseconds.

Select continuous, eligible segments before calling it, and retain the selection
criteria and excluded intervals. Never concatenate over gaps, NCO resets, or
state transitions. The default cadence guard rejects midpoint spacings above
1.5 times the median; `--max-gap-s` can encode a known cadence bound. Neither
check detects hidden reset events or all missing data. Channels with different
gaps need separate calls. Only an incomplete final block is discarded, and its
count is returned. Outputs refuse to overwrite an existing file.

## Method and outputs

Within each block, average `prompt**2`, unwrap its doubled angle, then halve it.
Squaring removes BPSK sign flips and weights epochs by prompt power. Restore the
arithmetic mean of the known NCO phase afterward. This approximates mean input
phase when relative phase varies little within a block; it is not an exact
arithmetic mean for arbitrary amplitude or phase motion.

The NPZ contains `phase_rad`, `relative_phase_rad`, actual block `time_s`,
`residual_rad` after a cubic least-squares detrend, `coherence`,
`unwrap_branch`, `relative_step_rad`, and tail/block counts. Phase has an unknown
integer-pi offset. Branch counters are unwrap bookkeeping, not physical slip
counts. The cubic removes real low-frequency content as well as deterministic
trends; report duration, detrending, spectrum window, frequency band, and units
when using the residual. Four blocks suffice algebraically but leave no residual
degrees of freedom: they cannot support a wander claim. No spectrum is computed
here; check actual grid regularity before using uniform-rate spectral routines.

## Controls before interpretation

Use matched nulls and known phase injections at the measured C/N0, integration
time, amplitude variation, timestamps, and NCO-motion envelope. Retain seeds,
trial counts, failures, amplitude/phase recovery, and noise-floor distributions.
Include large known NCO motion with a separate bounded tracking error. A quiet
NCO control alone misses loss of real turns during reconstruction.

Unwrapping assumes the true relative phase moves less than pi/2 between blocks,
with additional margin for noise; within-block rotation can also cancel the
phasors. Small observed steps do not prove this assumption: aliased motion can
look quiet. Low coherence, near-boundary steps, or failed controls leave the
measurement unresolved; do not delete those blocks to obtain a passing result.
Numerically cancelling blocks fail explicitly. Nonzero coherence is not a
validity certificate. Finite passing trials do not establish zero failure risk.

Squaring does not undo destructive integration across a sign transition.
QPSK components, secondary codes, and navigation edges require signal-specific
handling before forming a valid prompt. In particular, this script is not an
L5 NH transport or acquisition validator.

## Attribution limits

Residual prompt error measures how well the loop follows; reconstructed phase
also contains input motion and receiver-reference effects. Compare synchronous
satellites using the same preprocessing and noise controls. Cross-channel
coherence supports a common component, but cannot uniquely identify oscillator,
converter, propagation, or tracking contributions from one capture.

A pure-tone DAC-to-ADC loopback can test deterministic spurs and the measurement
chain. With a shared clock, correlated oscillator fluctuations may cancel;
differential delay and scaling determine what remains. It is not an independent
measurement of absolute oscillator phase noise. Separate ADC sampling jitter,
clock/LO noise, digital NCO quantization spurs, and register-update discontinuities
as hypotheses; an independent reference is needed for stronger attribution.
