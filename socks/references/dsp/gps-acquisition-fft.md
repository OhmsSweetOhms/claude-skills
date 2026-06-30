# FFT Acquisition Engine DSP Reference (PCPS)

Read this after `references/dsp/fixed-point-vhdl.md` when the block is an
FFT-based acquisition engine: PCPS (parallel code phase search), streaming
FFT/IFFT, spectrum pointwise multiply, power grid scan, or peak detection
with exact integer result fields.

This file is for FFT-engine and acquisition-datapath contracts. Correlator
tracking contracts stay in `gps-correlator.md`; general fixed-point, CDC,
and timing rules stay in `fixed-point-vhdl.md`. Provenance: the PL.B2
acquisition thread (plan-03, 2026-06).

---

## Engine Architecture: Iterative In-Place Is A Trap

An iterative in-place radix-2 FFT (one butterfly per cycle, shared RAM) is
the natural first authoring and matches a Python loop golden line-for-line —
but it CANNOT synthesize as written:

- the butterfly needs 2 reads + 2 writes per cycle at random addresses on
  one memory = 4 ports; BRAM has 2;
- the reads are combinational; BRAM is synchronous-read. `ram_style="block"`
  on an async-read array is silently ignored and the storage spills to
  LUTRAM/FF fabric (hundreds of Kb for N=4096 at 48-bit complex);
- the resulting RAM-mux -> wide multiply -> writeback cone will not close
  at a 200 MHz sample clock.

It is still a legitimate **v1 bit-exactness vehicle**: cheap to author,
trivially matched to the golden, retired once the pipelined engine passes
the same vector gate. Plan that retirement explicitly (delete the file,
scrub the name from live docs) or the dead engine becomes anti-context.

The synthesizable architecture is **single-path delay feedback (SDF)**:
one stage per FFT level, each with a fixed-length delay line. Delay lines
are sequential circular FIFOs (1W + 1R) — exactly what BRAM/SRL want. Total
storage is the same as in-place (~N complex words); what changes is the
access pattern.

---

## SDF Stage Discipline

Split each stage into two units so the feedback loop has ZERO multiplier
latency — otherwise the tail stages (delay length < multiplier latency)
cannot return the rotated diff in time:

```
[butterfly core + delay line]  -->  [inter-stage rotator]
     (1-cycle feedback)          (pipelined cmul + latency-matched bypass)
```

- DIF (forward): rotator AFTER the core, applied to drained diff samples
  only; sum samples take a register bypass of equal latency so stream
  order is preserved. The sum leg must stay exact if the golden's sum leg
  is exact.
- DIT (inverse): rotator BEFORE the core, applied to butterfly-phase input
  samples only.
- Delay lines: generate split — SRL chain below ~32 deep, circular BRAM
  with a lead-by-one read pointer above. Pin the effective delay with a
  directed test; the off-by-one is the classic bug.
- No backpressure anywhere. Valid/sop tags ride the datapath; frames are
  N valids + flush pushers. Count tagged samples; never hand-compute
  absolute latencies in the consumer FSM.

DIF natural-in/bit-reversed-out chained with DIT bit-reversed-in/natural-out
lets the PCPS pointwise multiply live in the bit-reversed domain with no
reorder buffer — both spectra are in the SAME order, so the code-spectrum
RAM read is purely sequential.

---

## Rounding Schedule Is A Config Axis — One Golden Per Schedule

If the Python golden multiplies EVERY stage's leg through a lossy Q15 path
(including W^0 = 32767/32768 — `(x*32767)>>15 != x`), then a radix-2^2
optimization (exact -j rotations, merged twiddles, half the multipliers)
is NOT bit-exact to it. The DSP saving comes precisely from skipping
multiplies the golden performs lossily.

Consequences:

- "Configurable at synthesis" = a `SCHEDULE` generic selecting per-stage
  rotator units (full multiplier every stage vs trivial-rotation/merged
  pairs). The delay lines, control, and butterflies are shared.
- Each schedule gates `max_abs_lsb=0` against ITS OWN golden/vector set.
  Never cross-gate, never loosen to a tolerance. Backend first: author the
  second rounding schedule in the Python golden, regenerate bundles at a
  clean SHA, then RTL.
- Exponent-0 entries in the merged schedule pass through EXACTLY (bypass
  mux, not a multiply by (32767, 0)). State this in the contract; it is
  the easiest place for golden and RTL to silently disagree.
- The radix-2 schedule costs ~2x the DSPs and is the natural default
  authority; the radix-2^2 schedule is the small-fabric/power config.

---

## Per-Stage Vector Gating

Never debug a multi-stage FFT end-to-end. Have the golden emit per-stage
intermediate vectors (one tap per hardware stage, exact integers) plus
round-trip frames, and gate stage-by-stage:

- frames worth committing: a real wiped-signal frame, the +/-1 code frame,
  an impulse, and a full-scale corner (wrap/resize differences hide in
  signal frames and show in corners);
- the TB reports the FIRST diverging stage + sample index;
- input vectors must be byte-identical across schedule variants (same IQ,
  different model outputs) — verify with `diff -q`, not by trust.

---

## Streaming PCPS Datapath

Replace load/start/wait FSMs with a straight tagged stream:

```
sample RAM -> carrier wipe -> fwd FFT -> pointwise(conj code spectrum)
          -> inv FFT -> |x|^2 -> row scan -> (last bin) peak2 scan -> result
```

- every RAM sequential-access synchronous-read (capture RAM, code-spectrum
  RAM, power buffers);
- best-row selection by ping-pong buffer SWAP (a 1-bit role register), not
  a copy pass;
- wide multiplies (pointwise products, power) as free-running registered
  pipelines with valid shift-registers — never combinational in an FSM
  state;
- result-field math (gcd/seed fractions) may stay multi-cycle sequential
  logic in the final state: nothing consumes the result for thousands of
  cycles.

---

## Testbench Lessons (xsim)

- **IRQ enable mask.** If the wrapper follows the two-register interrupt
  idiom (`irq <= status and enable`), the TB must write the enable. A
  "timeout waiting for irq" with all result-field checks passing is
  self-refuting evidence for an FSM hang — read what the failing run
  PASSED before theorizing.
- **`$fscanf` mis-parses signed CSV rows** under xsim (silently accepts a
  subset, then `scanned != n` on a negative field). Parse by comma split +
  `string.atoi()`.
- **Invocation shape matters.** Run module-local: `--project-dir .` with
  short `build/sim*` work dirs. Repo-root invocations with long external
  work dirs reproduce `ERROR: unexpected exception when evaluating tcl
  command` even for known-good targets.

---

## Acceptance Gate Template

| Gate | Evidence |
|---|---|
| Engine round trip (per schedule) | xsim forward+inverse on backend-emitted frames, per-stage taps, exact. |
| End-to-end vector gate (per schedule) | corner-PRN bundles, every result field, `max_abs_lsb=0`. |
| Code-source sweep | all-PRN code-spectrum check vs golden (matrix runner, per-top logs). |
| Timing/resource (per schedule) | OOC at target clock; BRAM report shows delay lines/datapath RAMs actually in block RAM — zero ram_style-ignored warnings. |
| Legacy retirement | v1 engine deleted after both gates green; name scrubbed from live docs. |
| Residual scope | schedules/N-sizes/dwell modes not proven by this gate. |
