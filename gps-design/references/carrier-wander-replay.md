# Carrier wander and golden-tape replay

Use this reference when investigating carrier motion, tuning PS.B7, comparing
real RF with synthetic controls, or trying a second band. General estimator
math and the reusable CLI live in control-loops:
`references/phase-measurement.md` and `scripts/measure_carrier_phase.py`.

## Start with the actual consumer

Trace the selected v2 scenario root to its receiver profile, constructor and
capture entrypoint. Immutable scenario configuration requires a real replacement
(e.g. `dataclasses.replace` where the current type is a frozen dataclass), not
an assignment that a stub-only desk test happens to accept. Verify the effective
configuration used by the real replay before spending a long remote run.

Read `tools/probe_carrier_loop_epochs.py` for the maintained C/A epoch capture
contract. It captures prompt values, actual timing and applied NCO context.
Read field names from that revision; do not reconstruct input phase from the
frequency reported after the tracking update. Selection should use the state
that produced the dump (`state_before` in this probe), not the next state.
The probe's `nco_phase_mid_rad` is based on a wrapped start accumulator; it
is not the continuous phase input expected by the shared estimator. Within a
verified segment, reconstruct starts by accumulating `2*pi *
nco_frequency_used_hz * dump_duration_s` from the first start phase, account
for any separately known phase adjustment, then add each half-dump phase.
Verify reconstructed phase modulo 2*pi against the captured phase and reject
unexplained discontinuities. Do not unwrap downsampled midpoint phases to
recover unknown turns. This adapter belongs with the capture producer.

Keep continuous segments and complete dump boundaries. A 1 Hz report cannot
resolve 1–3 Hz carrier motion and may alias it into an apparent slow wobble.

## Controls and variants

Validate estimator nulls and known injections before interpreting real phase.
Use actual dump timing, matched noise and large NCO motion with an independent
bounded tracking error. Freeze selection and averaging rules before reading
phase results. Weak channels with unresolved BPSK branch ambiguity stay
unresolved; smooth plots or small wrapped steps do not establish continuity.

An offline replay of fixed discriminator samples can test filter coefficients
and transition-state continuity. It cannot predict changed closed-loop prompts.
Run true closed-loop variants on identical input samples and initialization,
plus matched synthetic controls. Retain failed alternatives. Changing PLL
bandwidth trades motion-following against thermal-noise jitter; judge mean
frequency bias, phase dynamics, slips, lock and navigation separately.

The carrier-wander branch at `f6486424f9f2` adopted 18 Hz for C/A open_sky
locked PLL only. It substantially improved the full recorded-tape outcome;
it did not identify a unique hardware noise source. The static PVT RMS rose
about 1.58 mm and p95 about 0.31 mm, explicitly accepted by the operator.
The full receiver suite still had three failures at handback, including an
every-epoch 5 Hz frequency bound and unavailable L5 frozen-bundle inputs.
Keep that acceptance scope; do not promote it to a universal tuning rule or
claim all regression gates passed. Durable project authority is the
`receiver/20260911-golden-tape-replay` thread's plan-06 evidence/handback.

## Acceptance beyond the phase plot

Compare full-tape navigation subframes, ephemerides, first-fix time, fix coverage,
gaps, satellite count, geometry and PVT against the fixture's surveyed reference.
Keep ellipsoid versus orthometric height and antenna identity explicit. A short
phase window alone is not a navigation or PVT gate. Use the same synthetic
fixture on both revisions to expose small regressions.

A bit-coherent prompt metric such as `abs(sum(I)) / sum(hypot(I,Q))` requires
verified physical navigation-bit boundaries. It includes prompt noise and is
neither BER nor isolated thermal or carrier loss. Do not choose bit alignment
by maximizing the result independently on each variant.

If a wider-bandwidth revision fails an instantaneous frequency threshold,
replay that exact seeded fixture at baseline and candidate settings and examine
bias/jitter/slips. A plausible jitter explanation is an inference until checked;
do not edit a source test merely to sidestep its failure.

## L5 is a separate measurement route

A C/A phase estimator does not validate L5 Q5 acquisition, coherent folding, or
NH secondary-code transport. Establish deterministic known-truth controls for
fractional primary-code boundaries and NH sign transitions first. A successful
full-versus-narrow acquisition grid at one fixture does not prove that the
selected NH hypothesis transports correctly across a later slice.

Pre-register eligible windows and minimum coverage independently of phase
quality. Preserve windows rejected by acquisition/coherence checks. If none
qualify, report no L5 phase conclusion; do not keep moving the window until a
quiet slice appears. In this investigation L5 comparison remained unresolved.

## Remote replay and frozen inputs

Use the project's `tools/simhost.py` and its current example/configuration for
remote Python simulation. Read its subcommands and host-job contract before
launching; do not confuse simulation-host SSH with board control. Never put
credentials or host-specific paths in the shared skill. Banked raw tapes and
derived receiver-rail tapes are different inputs: verify manifest, size, hash,
rate, layout, scaling and exact remote file before replay.

Record source commit, effective config, interpreter/dependencies, input hashes,
command, logs, output hashes and terminal exit. Use separate immutable baseline
and candidate output locations. Retrieve failure logs even when the run exits
nonzero; chaining retrieval behind a successful-run condition loses evidence.

For tests importing external frozen bundles, inspect the current path resolver
and inventory the required signal families and rounding metadata. A directory
existence check does not prove its vectors are present. In particular, read
`gps_receiver/tests/test_pl_b2_fold_fixed_point.py` and its `SOCKS_TB` override
before interpreting missing L5 bundles as an arithmetic regression. Do not
substitute generated fixtures, skip a test, or change rounding authority just
to make an environmental failure disappear.
