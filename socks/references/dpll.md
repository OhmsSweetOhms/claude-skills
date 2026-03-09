# DPLL / PLL / Clock Recovery Reference

Read this file before starting Stage 1 when the design involves a PLL, DPLL, NCO, clock recovery, or fractional-N architecture. These patterns were learned through multi-iteration DPLL development and cover failure modes that are subtle and non-obvious.

## Table of Contents

1. [Non-integer clock ratios](#non-integer-clock-ratios)
2. [Fractional-N phase detector: the four failure modes](#fractional-n-phase-detector-the-four-failure-modes)
3. [NCO duty cycle awareness](#nco-duty-cycle-awareness)
4. [PI filter and gain tuning](#pi-filter-and-gain-tuning)
5. [Runtime frequency switching](#runtime-frequency-switching)
6. [Clock-enable sampling vs BUFR](#clock-enable-sampling-vs-bufr)
7. [Serial data verification methodology](#serial-data-verification-methodology)
8. [Serial RX shift register: false-SYNC lock-up](#serial-rx-shift-register-false-sync-lock-up)
9. [Xsim time-quantisation drift](#xsim-time-quantisation-drift)
10. [CSV debug patterns for loop dynamics](#csv-debug-patterns-for-loop-dynamics)
11. [Diagnosing phase detector failures from CSV data](#diagnosing-phase-detector-failures-from-csv-data)

---

## Non-integer clock ratios

- If the module samples an external signal at a sys-clock boundary, quantisation jitter = ±1 sys-clock cycle.
- For a reference at f_ref with sys-clock at f_sys: jitter amplitude = f_ref / f_sys × full-scale.
- If jitter > lock band, phase lock is impossible without a fractional-N accumulator.
- Rule of thumb: if f_sys / f_ref is not an integer, use a frac-N accumulator.

**Concrete example:** 100 MHz / 15 MHz = 6.667 cycles. Sampling jitter ±1 cycle = ±30% full-scale; default lock band ±6.25% → never locks. Simulation shows −439 ppm frequency error with phase oscillating ±10.66%.

---

## Fractional-N phase detector: the four failure modes

A DPLL with a fractional-N accumulator has four potential failure modes that interact. Any phase detector design must handle all four simultaneously:

### Failure mode 1: Quantisation jitter

Direct sampling of phase_accum at ref edges has ±1 sys_clk cycle jitter. At non-integer ratios, this exceeds the lock band. Requires an inter-edge interpolation mechanism (e.g. ref_frac_acc).

### Failure mode 2: Accumulator lockstep drift

A free-running ref_frac_acc that advances at the same per-cycle rate as phase_accum (both at ~freq_word) produces a constant phase_err regardless of actual ref frequency. The loop is blind to freq_word quantisation error, causing unbounded NCO drift.

**Key insight:** The free-running ref_frac_acc has zero frequency-tracking capability because it drifts in lockstep with phase_accum. The ref_rise edge timing (the only external frequency information) only triggers measurement — it doesn't affect the comparison result.

### Failure mode 3: Single-period measurement noise

Measuring phase advance over one ref period (snapshot approach) produces huge swings at non-integer ratios. At 200 MHz / 15 MHz = 13.333 cycles, periods alternate 13 and 14 cycles:

| M (cycles) | advance = M × freq_word | mod 2³² (signed) |
|------------|-------------------------|-------------------|
| 13 | 4,187,593,111 | −107,374,185 |
| 14 | 4,509,715,658 | +214,748,362 |

Both exceed LOCK_BAND (134,217,728). CSV confirms stable 3-sample oscillation (dt = 13, 13, 14).

### Failure mode 4: Cumulative phase error from fixed-constant anchor

Snapping ref_frac_acc to a fixed canonical value (e.g. 2^(N-1)) on each ref_rise resets ref_frac_acc each ref period, but phase_accum free-runs continuously. Phase_err grows by M × freq_word per ref period: `pe(K) = pe(K-1) + M × ref_step`.

**CSV evidence at 15 MHz:** steady-state swing {+118M, −2M, −115M} = 87% of LOCK_BAND. 133,179 locked PD samples show 3-sample cycle. 60 sporadic outliers at −203M from Xsim 1 ps quantisation beat. At 17.77 MHz, exceeds PI compensation → never locks.

**Root cause:** phase_accum at ref edges is determined by its entire history since reset (initial value 0, accumulated over millions of cycles). The offset between phase_accum and the fixed anchor is arbitrary and large. The "drift" it detects is dominated by this irrelevant historical offset.

Additionally, `to_unsigned(2**(N-1), N)` overflows the VHDL integer type when N >= 32. Fix with bit-vector aggregate: `(N-1 => '1', others => '0')`.

### Solution: snap-to-phase_accum

Snap ref_frac_acc to `phase_accum + ref_step_reg` on each ref_rise, then free-run at ref_step between edges:

```vhdl
p_ref_frac : process(clk)
begin
    if rising_edge(clk) then
        if rst_n = '0' then
            ref_frac_acc <= (others => '0');
        elsif ref_rise = '1' then
            ref_frac_acc <= phase_accum + ref_step_reg;  -- snap to NCO phase
        else
            ref_frac_acc <= ref_frac_acc + ref_step_reg;  -- free-run between edges
        end if;
    end if;
end process;
```

Why this solves all four:

| Requirement | How satisfied |
|-------------|--------------|
| #1 + #3: Inter-edge smoothing | Between ref edges, both accumulators advance by M × ~freq_word (nfw ≈ ref_step when locked). The M=13/14 variation is identical in both paths and cancels in phase_err. |
| #4: No cumulative error | Snapping ref_frac_acc to phase_accum resets the accumulation baseline each ref period. pe = (M+1) × (nfw − ref_step), constant when nfw is constant — no growth. |
| #2: Lockstep drift | Snap-to-phase_accum reintroduces the lockstep property. Worst-case drift: ±0.5 LSB = 0.023 ppm at 1 MHz, negligible. |

**Off-by-one compensation:** The `+ ref_step_reg` in the snap expression compensates for p_nco incrementing phase_accum on the same clock edge as the snap. Without it: `pe = (M+1)×nfw − M×rs` → locks at systematic offset of −f/SYS_CLK (−25,000 ppm at 5 MHz). With it: `pe = (M+1)×(nfw − rs)` → locks at `nfw = rs` with 0.00 ppm error.

---

## NCO duty cycle awareness

When sys_clk / f_out is not an even integer, the NCO MSB toggles asymmetrically. This is a fundamental property of single-accumulator NCO architecture (e.g. 43%/57% at 15 MHz on 100 MHz).

If 50% duty cycle matters, choose sys_clk such that sys_clk / f_out is even for all target frequencies. Example: 200 MHz sys_clk gives even-integer half-periods for all 1–20 MHz targets.

With clock-enable sampling, duty cycle is irrelevant — only the edge timing matters.

---

## PI filter and gain tuning

**Architecture:** 32×16 → 48-bit multiply (maps to 2 DSP48E1). Integrator saturates at ±2^46. Correction saturates at ±2^30 (±25% of NCO full-scale). Q16 gains: shift output right 16 before driving NCO.

**Lock detection:** 256 consecutive ref cycles with `|phase_err| < LOCK_THRESH × 2^24`. Two-sided comparison to avoid `abs(−2^31)` wrapping — never use `abs()` on signed.

**Gain formula:**
```
freq_word = round( f_target × 2^NCO_BITS / SYS_CLK_HZ )
KP        = round( 0.10 × freq_word × 65536 / 2^(NCO_BITS-1) )
KI        = KP // 16
```

**DSP48E1 estimate:** p_loop_filter: 2 × (32×16 → 48-bit) → ~4 DSP48E1. p_nco, p_ref_frac, p_phase_det: 32-bit adders → ~3 DSP48E1. Total: ~7 DSP48E1.

---

## Runtime frequency switching

Atomic shadow-register latch with integrator clear. A rising edge on `update` latches `freq_sel`, `ref_step`, `kp_in`, `ki_in` simultaneously and clears the PI integrator, but retains the NCO and reference accumulators. Re-acquisition within ~256 ref cycles without resetting phase state.

---

## Clock-enable sampling vs BUFR

For designs where the NCO output drives data sampling:

- **Option 1 (BUFR):** Route nco_out through BUFR to drive regional clock net. Requires CDC crossings, `create_generated_clock` constraints, BUFR LOC placement, and limits RX logic to one clock region.
- **Option 2 (clock-enable):** Detect nco_out edges in sys_clk domain, use clock-enable for data sampling. Everything stays in one clock domain. No BUFR, no CDC, no timing exceptions.

Option 2 has 1-sys_clk sampling jitter (e.g. 5 ns at 200 MHz) which is negligible for data rates where bit periods are 50–1000 ns.

The dirty reference clock justifies keeping the DPLL — the PI filter averages out jitter and the NCO provides a clean, predictable sampling strobe.

---

## Serial data verification methodology

For designs that recover a clock from a reference and use it to sample data, validate with a serial loopback test:

- TX: drive serial data synchronised to the reference clock (1 bit per ref edge)
- RX: sample serial data using the recovered clock (or clock-enable)
- Frame: SYNC word + pseudo-random data (LFSR) + CRC checksum
- Zero CRC errors across many frames = correct frequency and phase alignment

Key detail: frames in-flight during frequency transitions will be corrupted. Track `had_good_frame` per frequency and classify pre-first-good CRC failures as sync misses, not functional errors.

---

## Serial RX shift register: false-SYNC lock-up

If the data stream can contain the SYNC pattern within payload data (e.g. LFSR pseudo-random data), the RX shift register must run continuously in ALL states, not just HUNT. If the shift register only runs in HUNT and is cleared on CRC fail, the RX can lock onto a false SYNC at the wrong bit offset, collect misaligned data, fail CRC, return to HUNT with a cleared register, wait 16 bits, find the next false SYNC, and repeat indefinitely.

```systemverilog
// CORRECT: shift register runs unconditionally, SYNC check only in HUNT
always_ff @(posedge clk) begin
    if (sample_en) begin
        rx_shift_reg <= {rx_shift_reg[14:0], data_sampled};  // always shift

        case (rx_state)
            RX_HUNT: begin
                if (rx_shift_reg == SYNC_WORD)
                    rx_state <= RX_DATA;    // only check SYNC in HUNT
            end
            RX_DATA: begin
                // collect bytes, do NOT check for SYNC here
            end
            RX_CRC: begin
                // verify CRC, return to HUNT on completion
                // do NOT clear rx_shift_reg -- it's primed for next SYNC
            end
        endcase
    end
end

// WRONG: shift register only in HUNT, cleared on CRC fail
// -> false-SYNC lock-up on LFSR data containing the SYNC pattern
```

The fix guarantees that after any CRC failure, the shift register already contains the last 16 received bits and can detect the real SYNC within one frame period.

---

## Xsim time-quantisation drift

Successive `#(half_period_ns)` delays accumulate rounding error when the half-period is irrational. Xsim quantises each delay to 1 ps resolution:

| Frequency | Half-period (ps) | Quantised (ps) | Error/period | Time to drift 1 bit |
|-----------|-----------------|----------------|-------------|---------------------|
| 15.00 MHz | 33333.333 | 33333 | 0.667 ps | ~3.3 ms |
| 17.77 MHz | 28137.310 | 28137 | 0.620 ps | ~2.6 ms |

Over 100 ms, ref edges drift ~30 bit periods at 15 MHz. Integer ratios (5, 10, 20 MHz) have exact half-periods and zero drift.

**Fix: Absolute-time edge scheduling.** Each edge time is computed as `base_ns + edge_num × half_period_ns`, using a single multiply from base time. Quantisation error bounded to ±0.5 ps regardless of sim length. IEEE-754 double precision provides sub-femtosecond arithmetic precision.

```systemverilog
// Old: cumulative drift
ref_in_a = 1'b1;  #(half_period_ns);
ref_in_a = 1'b0;  #(half_period_ns);

// New: absolute-time scheduling
edge_num = edge_num + 1;
ref_in_a = 1'b1;
#(base_ns + edge_num * half_period_ns - $realtime);
edge_num = edge_num + 1;
ref_in_a = 1'b0;
#(base_ns + edge_num * half_period_ns - $realtime);
```

On frequency change, reset `base_ns` to `$realtime` and `edge_num` to 0.

---

## CSV debug patterns for loop dynamics

When debugging lock failures, log one row per phase_err_valid event (once per ref period) with the key loop signals. This gives a compact trace of the phase detector, loop filter, and lock detector behaviour without the noise of logging every sys_clk cycle.

```systemverilog
always @(posedge clk) begin : csv_logger
    if (csv_enable) begin
        if (mon_phase_err_valid) begin
            $fwrite(csv_fd, "%0d,%0d,PD,%b,%0d,%0d,%b\n",
                    csv_cycle, $time,
                    ref_in, $signed(mon_phase_err), mon_nco_freq_word, locked);
        end
        csv_cycle = csv_cycle + 1;
    end
end
```

**Critical: start capture BEFORE wait_for_lock.** If the CSV logger only enables after lock succeeds, lock failures produce empty CSV files. Enable CSV at the frequency switch, before the lock wait, and stop it on timeout or after the capture window expires.

---

## Diagnosing phase detector failures from CSV data

When phase_err oscillates instead of converging, look at these signals:

| Symptom | Diagnosis | Fix |
|---------|-----------|-----|
| phase_err constant (near zero), nco_freq_word constant, but clk_out drifts | Lockstep drift (#2): ref_frac_acc and phase_accum advance at same rate | Add edge-anchoring to ref_frac_acc |
| phase_err oscillates with period = integer ratio denominator (e.g. 3 for 13.33) | Single-period noise (#3): measurement sees 13-cycle and 14-cycle periods | Add inter-edge smoothing (free-running ref_frac_acc between edges) |
| phase_err huge on first sample, then oscillates wildly | Absolute phase measurement instead of relative | Use differential measurement (snapshot or edge-anchored accumulator) |
| phase_err steady-state swing 80-100% of LOCK_BAND, sporadic outliers | Cumulative error (#4): fixed-constant anchor + historical phase_accum offset | Switch to snap-to-phase_accum |
| phase_err converges but locked never asserts | Lock band too narrow, or lock counter threshold too high | Widen LOCK_THRESH or check lock counter reset logic |
