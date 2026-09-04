# Common SDF FFT core — hard-won facts

One radix-2² SDF FFT source sits behind both GPS acquisition wrappers and
both Iridium blocks, each a separate physical elaboration. Everything below
is a MEASURED fact from an elaboration, a simulation, or a routed netlist;
the dates are preserved as written. Live thread state lives in `.threads/`,
not here, and the durable decision record is the ADR stores. Vivado / gate
methodology that generalizes beyond this core lives in the socks skill
`references/hil.md`.

Moved verbatim from the project `CLAUDE.md` (2026-09-04). Original
section opening:

> One radix-2² SDF FFT source (`modules/common_sdf_fft_core/` in socks, on
> main since `40d898c5`) sits behind both GPS acquisition wrappers and both
> Iridium blocks, each a SEPARATE physical elaboration (no arbiter, no TDM).
> Explainer: `docs/architecture/common-sdf-fft-core.html`. Live thread state
> lives in `.threads/`, **not here**.

## Generics and elaboration

- **Every shape generic of the core is MANDATORY (since the generics-hardening
  hop, socks `aa15117c`); name every generic in every instantiation.** The
  original defaults were mutually consistent (`N_MAX 4096`, fixed-N, forward,
  `IN 13 / OUT 25`), so an OMITTED generic once elaborated a legal but wrong
  4096-point forward pipe. Now an omitted actual is an `xvhdl` ANALYSIS error
  (`VRFC 10-3348`), not an elaboration error, and it fires even inside an
  untaken `if … generate` branch — the whole architecture is discarded and
  every downstream bench and gate stops compiling. Corollaries: a clean
  `xvhdl --2008` of a module's COMPLETE source list is a total proof that
  every instantiation binds every generic; a negative-elaboration row must
  pass the FULL generic set or it goes red for the wrong reason (its own
  assertion pre-empted by the missing-generic error); and the core is a
  consumer of itself (the pipe instantiates the stage twice).
- **Every generic of `common_sdf_fft_pipe` (and its stage/tail/delay/
  cmul) had a mutually consistent default, so an omitted generic
  elaborated a legal but WRONG 4096-point forward pipe** — the
  generics-hardening packet removes the size/width defaults
  (`ROT_LATENCY`, `SRL_MAX`, `DELAY_LEN_OVERRIDE` keep theirs); pin every
  generic explicitly. The core is ONE source instantiated THREE times in
  the TEST image (GPS batch engine, Iridium channelizer pipe, Iridium
  detector pipe), never a shared instance. **Vivado 2022.2 silently
  ignores `rom_style="distributed"` on an array-of-arrays ROM read in a
  loop and builds a mux ROM in RAMB18**; 1-D ROM entities map as asked.

(The second bullet above is the near-duplicate copy that the project
`CLAUDE.md` carried in its acquisition-engine section; both are kept
verbatim because each states something the other does not.)

## Datapath shape and cadence

- **`OUT_WIDTH` is the growth bound `IN_WIDTH + log2 n + 1`, not the data
  bound** (22 at N=128, 29 at N=16384): no stage container is capped, so
  the two widths move together.
- **Frames are strictly serialized: period `n + (n−1) + 3·log2 n` cycles;
  a `sop_i` inside a drain ABORTS the frame in flight** (the donor header's
  "frames may be back-to-back" described an unexercised path). N=128:
  276 cycles against a 312.5-cycle cadence; N=16384: 32,809 against
  40,000.
- **A fixed-N consumer (`cfg_log2n_i` tied) prunes the core** to 2,080 LUT /
  12 DSP at N=128 — the core-alone OOC row (2,655) overstates it by ~575.
- **The channelizer's FIR costs 32 DSP48E2 at the 100 MHz single clock**
  (24 products for 12 taps per cycle at 41 % duty plus adder-tree levels in
  DSP post-adders) against plan-03's 4 priced at 250 MHz; the contiguous
  frame law forces one branch per cycle — a faster FIR clock or TDM
  products is an integration decision.

## Detector golden and RTL cost

- **The detector golden's floor prime (median of each bin's first 64
  frames) is not fabric** (~28 Mbit); the RTL runs from a LOADED floor
  state. Its burst start time is a float-domain interpolation; the RTL
  emits integer event fields and the PS interpolates.
- **The `PL.B1ir` detector RTL (plan-24, 2026-09-03) measured 166 BRAM36 /
  30 DSP / 16,010 LUT at 100 MHz OOC** — plan-23's 151 packed tiles plus
  three stores the costing never priced (overlap 11, reorder 13, ratio-row
  12) net of what it over-priced; the 24.5-tile pre-trigger ring is
  downstream. The fixed-N core inside it prunes to 9,295 LUT / 24 DSP
  (`cfg_log2n_i` constant), ~5,500 LUT under the core-alone row at
  N = 16384. Budget the measured number, never the costing.
- **A frozen vector bundle's digests prove it reproduces, never that it is
  right.** The `pl_b1ir` bundle's `floor_q.csv` was N references to one
  in-place-updated array (every row the FINAL floor) and its digests were
  self-consistent; only a per-block re-derivation through the golden saw it.
  Every RTL bench derives its expected states by CALLING the golden along the
  vectors at least once and ties every row — a standing step, not an
  afterthought.
- **Two RTL defects that data cannot find, witnesses can:** a registered
  FIFO read port is not readable the cycle the empty flag drops (gate the pop
  on a registered non-empty flag and a pop shadow — the streamer popped a
  zero record and every run then tripped `window_overrun`); and one idle
  cycle between a flush and a pending frame makes the SDF pusher period
  `n + FLUSH + 1`, not `n + FLUSH`, which at the exact floor cadence slips a
  cycle per frame while the data stays bit-exact (the overwritten leading
  samples sat under the window's zero taps). Run every cadence-floor and
  overrun witness; budget one gate cycle for what they find.
