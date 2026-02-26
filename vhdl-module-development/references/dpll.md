# DPLL / PLL / Clock Recovery Reference

Read this file before starting Stage 1 when the design involves a PLL, DPLL, NCO, clock recovery, or fractional-N architecture. These patterns were learned through multi-iteration DPLL development and cover failure modes that are subtle and non-obvious.

## Table of Contents

1. [Non-integer clock ratios](#non-integer-clock-ratios)
2. [Fractional-N phase detector: the four failure modes](#fractional-n-phase-detector-the-four-failure-modes)
3. [NCO duty cycle awareness](#nco-duty-cycle-awareness)
4. [Serial data verification methodology](#serial-data-verification-methodology)
5. [Serial RX shift register: false-SYNC lock-up](#serial-rx-shift-register-false-sync-lock-up)
6. [CSV debug patterns for loop dynamics](#csv-debug-patterns-for-loop-dynamics)
7. [Diagnosing phase detector failures from CSV data](#diagnosing-phase-detector-failures-from-csv-data)

---

## Non-integer clock ratios

- If the module samples an external signal at a sys-clock boundary, quantisation jitter = +/-1 sys-clock cycle.
- For a reference at f_ref with sys-clock at f_sys: jitter amplitude = f_ref / f_sys x full-scale.
- If jitter > lock band, phase lock is impossible without a fractional-N accumulator.
- Rule of thumb: if f_sys / f_ref is not an integer, use a frac-N accumulator.

---

## Fractional-N phase detector: the four failure modes

A DPLL with a fractional-N accumulator has four potential failure modes that interact. Any phase detector design must handle all four simultaneously:

1. **Quantisation jitter:** Direct sampling of phase_accum at ref edges has +/-1 sys_clk cycle jitter. At non-integer ratios, this exceeds the lock band. Requires an inter-edge interpolation mechanism (e.g. ref_frac_acc).

2. **Accumulator lockstep drift:** A free-running ref_frac_acc that advances at the same per-cycle rate as phase_accum (both at ~freq_word) produces a constant phase_err regardless of actual ref frequency. The loop is blind to freq_word quantisation error, causing unbounded NCO drift.

3. **Single-period measurement noise:** Measuring phase advance over one ref period (snapshot approach) produces huge swings at non-integer ratios. For example, at 200/15 MHz = 13.333 cycles, periods alternate 13 and 14 cycles, producing phase_err swings of +/-100-250M (vs LOCK_BAND of 134M).

4. **Cumulative phase error from fixed-constant anchor:** Snapping ref_frac_acc to a fixed canonical value (e.g. 2^(N-1)) on each ref_rise resets ref_frac_acc each ref period, but phase_accum free-runs continuously. Phase_err grows by M x freq_word per ref period. The PI proportional term partially compensates but the swing reaches 87% of LOCK_BAND at 15 MHz and exceeds it at 17.77 MHz (causing lock timeout). Additionally, `to_unsigned(2**(N-1), N)` overflows the VHDL integer type when N >= 32.

**Solution: snap-to-phase_accum fractional-N accumulator.** Snap ref_frac_acc to `phase_accum + ref_step_reg` on each ref_rise, then free-run at ref_step between edges:

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

- **#1 and #3 (inter-edge smoothing):** Between ref edges, both accumulators advance by M x ~freq_word (since nfw ~ ref_step when locked). The M=13/14 variation is identical in both paths and cancels in phase_err.
- **#4 (no cumulative error):** Snapping ref_frac_acc to phase_accum resets the accumulation baseline each ref period. phase_err = (M+1) x (nfw - ref_step), which is constant when nfw is constant -- no growth.
- **#2 (lockstep drift):** Snap-to-phase_accum reintroduces the lockstep property where the loop cannot detect sub-LSB freq_word quantisation error. Worst-case drift: +/-0.5 LSB = 0.023 Hz at 1 MHz (0.023 ppm), negligible for this application.
- **Off-by-one compensation:** The `+ ref_step_reg` in the snap expression compensates for p_nco incrementing phase_accum on the same clock edge as the snap. Without it, the loop locks with a systematic frequency offset of -f/SYS_CLK. With it, phase_err = 0 exactly when locked.

---

## NCO duty cycle awareness

When sys_clk / f_out is not an even integer, the NCO MSB toggles asymmetrically. This is a fundamental property of single-accumulator NCO architecture. If 50% duty cycle matters, choose sys_clk such that sys_clk / f_out is even for all target frequencies. With clock-enable sampling, duty cycle is irrelevant -- only the edge timing matters.

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
| phase_err constant (near zero), nco_freq_word constant, but clk_out drifts | Lockstep drift: ref_frac_acc and phase_accum advance at same rate | Add edge-anchoring to ref_frac_acc |
| phase_err oscillates with period = integer ratio denominator (e.g. 3 for 13.33) | Single-period quantisation noise: measurement sees 13-cycle and 14-cycle periods | Add inter-edge smoothing (free-running ref_frac_acc between edges) |
| phase_err huge on first sample, then oscillates wildly | Absolute phase measurement instead of relative | Use differential measurement (snapshot or edge-anchored accumulator) |
| phase_err converges but locked never asserts | Lock band too narrow, or lock counter threshold too high | Widen LOCK_THRESH or check lock counter reset logic |
