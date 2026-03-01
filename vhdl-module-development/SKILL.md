---
name: vhdl-module-development
description: Use this skill whenever creating, verifying, or iterating on a synthesisable VHDL module targeting Xilinx Vivado / Xsim. Covers the complete pipeline: architecture design -> VHDL authoring -> cycle-accurate Python testbench -> synthesis audit -> Python iteration -> Xsim-compatible SystemVerilog testbench -> VCD post-simulation verification -> CSV debug logging -> Vivado synthesis (utilization + timing) -> CLAUDE.md project guide. Trigger on any request involving new VHDL RTL, a Python model of VHDL, a synthesis check, or an Xsim testbench. Also use when the user mentions PLLs, DPLLs, NCOs, clock recovery, phase detectors, fractional-N architectures, PI loop filters, lock detection, DSP48E1 mapping, CDC synchronisers, or any Zynq-7000 / UltraScale fabric design -- even if they do not explicitly say VHDL or testbench.
---

# VHDL Module Development Pipeline

## Overview

The pipeline has eleven stages executed in order. Never skip a stage or reorder them -- each stage catches a class of bugs that the next stage cannot.

**Implementation model:** After Stage 0 (environment setup) and planning are complete, delegate the implementation of Stages 1-10 to a Sonnet agent via the Task tool. Opus handles planning and review; Sonnet handles implementation.

```
Environment -> Architecture -> VHDL -> Python TB -> Synthesis audit -> Python iteration -> SV/Xsim TB -> VCD verify -> CSV verify -> Vivado synthesis -> CLAUDE.md
```

Bugs caught at each stage:

- Architecture: wrong operator widths, overflow in constants, unachievable timing
- VHDL authoring: sign errors, dead signals, wrong reset values, multiple drivers
- Python TB: functional correctness -- lock time, steady-state error, edge cases
- Synthesis audit: constructs Vivado rejects, DSP mapping, timing path depth
- Python iteration: fixes introduced by synthesis changes, new operating points
- SV/Xsim TB: final RTL-level gate-check, regression baseline for future changes
- VCD verify: independent post-simulation verification from raw waveform data, no reliance on SV self-checks -- catches RTL bugs that SV testbench code may mask or share
- CSV verify: cross-check SV simulation dynamics against Python model predictions cycle-by-cycle
- Vivado synthesis: actual resource utilization, timing closure, DRC violations
- CLAUDE.md: project documentation with synthesis results as permanent record

**For DPLL, PLL, NCO, clock recovery, or fractional-N designs:** read `references/dpll.md` before starting Stage 1. It covers failure modes, phase detector patterns, and verification techniques specific to those architectures.

---

## Stage 0 - Environment Setup

Before writing any code, verify that the required EDA tools are on PATH and functional.

### Vivado / Xsim discovery

Search for Vivado's `settings64.sh` and source it. Common locations:

```bash
/tools/Xilinx/Vivado/*/settings64.sh
/opt/Xilinx/Vivado/*/settings64.sh
~/Xilinx/Vivado/*/settings64.sh
```

Verify by running:

```bash
source /path/to/Vivado/<version>/settings64.sh
which xvhdl xvlog xelab xsim vivado
```

All five must resolve. If they do not, ask the user for the Vivado installation path before proceeding.

**Important:** Every Bash tool call that invokes Vivado/Xsim commands must source `settings64.sh` first (or the environment must already contain the Vivado paths). Shell state does not persist between Bash tool calls.

### Shell command rules

**Always wrap compound commands in `bash -c '...'`.** Claude Code's permission system matches only the first token of a Bash tool call. A command like `source settings64.sh && vivado ...` is matched as `source:*`, and the `&&`-chained portion is not covered by any permission rule — causing an interactive approval prompt. Wrapping in `bash -c` makes the first token `bash`, which matches `Bash(bash:*)`.

```bash
# CORRECT — matches Bash(bash:*) permission, runs without prompt
bash -c 'source /tools/Xilinx/Vivado/2023.2/settings64.sh && xvhdl --2008 my_module.vhd'

# WRONG — permission system sees "source:*" but && portion is unmatched
source /tools/Xilinx/Vivado/2023.2/settings64.sh && xvhdl --2008 my_module.vhd
```

This rule applies to **every** Bash tool call that chains commands with `&&`, `||`, or `;`. Single commands (e.g. `python3 audit.py`) do not need wrapping.

**Never use process substitution** (`<(...)` or `>(...)`) in Bash tool calls. Process substitution triggers interactive approval prompts and breaks automated workflows. Instead:

- Use temporary files: `cmd > /tmp/out.txt && diff /tmp/a.txt /tmp/b.txt`
- Use pipes: `cmd1 | cmd2`
- Use sequential commands: `cmd1 > tmp.txt && cmd2 tmp.txt && rm tmp.txt`

This applies to all bash commands generated during the pipeline — build scripts, audit scripts, diff comparisons, and ad-hoc commands alike.

---

## Stage 1 - Architecture

Before writing any VHDL, answer these questions in a comment block at the top of the file.

**Datapath widths**

- What is the widest intermediate value in each arithmetic operation?
- Do any intermediate values exceed the VHDL integer range (-2^31 to 2^31-1)? If so, express all constants as bit-vector aggregates, never as integer arithmetic.
- Map every multiply to a DSP48E1 footprint: Zynq-7000 supports 27x18 natively. A 32x16 multiply requires 2 DSP48E1; a 48x48 requires many more.

**Saturation constants -- always write as bit-vector aggregates**

```vhdl
-- CORRECT: no integer arithmetic, elaborates at any width
constant MAX_VAL : signed(N-1 downto 0) := (N-2 => '1', others => '0');  -- +2^(N-2)
constant MIN_VAL : signed(N-1 downto 0) := (N-1 => '1', N-2 => '1', others => '0');  -- -2^(N-2)

-- WRONG: 2**(N-2) overflows VHDL integer when N > 33
constant MAX_VAL : signed(N-1 downto 0) := to_signed(2**(N-2), N);  -- BUG if N>33
```

**Pipeline latency**

- Trace every combinational path from input to output.
- For any path with more than 3 pipeline stages of accumulated delay, note it in the header.
- 7-series LUT+carry critical path: ~4 ns/stage. A multiply+accumulate+clamp is ~6-8 ns at 100 MHz.

**Pipeline latency matching**

When two correlated paths converge at a sampling point (e.g. an edge-detect path and a data path), match their registered stage counts rather than minimising either one. Unmatched pipelines shift the sampling point away from mid-bit, causing failures at frequencies where margin is tight. Adding pipeline registers is always acceptable for latency matching and timing closure -- prefer correctness and timing margin over minimum latency unless the design explicitly requires lowest-latency operation. Ask before adding more than 10 stages to any single path.

---

## Stage 2 - Writing VHDL

### File header (mandatory)

Every VHDL file must have a header comment block containing:

- Module name, target device, tool version
- List of generics with valid ranges
- List of ports with direction and description
- Architecture block diagram (ASCII)
- Gain/parameter formulae (if applicable)
- Integration notes (clock constraints, ASYNC_REG attributes, timing)
- Change log from previous version

### Entity rules

```vhdl
entity my_module is
    generic (
        SYS_CLK_HZ : positive := 100_000_000;  -- always include valid range in comment
        DATA_W     : positive := 32;
        GAIN_W     : positive := 16             -- width of gain ports, not a magic number
    );
    port (
        clk   : in  std_logic;
        rst_n : in  std_logic;                  -- active-low synchronous reset
        ...
    );
end entity my_module;
```

- All generics must have defaults.
- Document the valid range of every generic in a comment.
- Use positive for widths; use natural for values that may be zero.
- Active-low synchronous reset (rst_n) is the Zynq-compatible convention.

### Architecture rules

One process per register group. Do not put unrelated registers in one process.
Name every process: p_sync, p_edge, p_filter, p_nco, p_lock, etc.

Reset branch first, always:

```vhdl
p_example : process(clk)
begin
    if rising_edge(clk) then
        if rst_n = '0' then
            reg <= (others => '0');     -- explicit reset value
        else
            reg <= next_value;
        end if;
    end if;
end process p_example;
```

Saturation idiom -- use variables, not signals, inside the process:

```vhdl
p_filter : process(clk)
    variable sum_v : signed(WIDE-1 downto 0);
begin
    if rising_edge(clk) then
        if rst_n = '0' then
            result <= (others => '0');
        elsif valid = '1' then
            sum_v := a + b;
            if    sum_v > MAX_VAL then  result <= MAX_VAL;
            elsif sum_v < MIN_VAL then  result <= MIN_VAL;
            else                        result <= resize(sum_v, NARROW);
            end if;
        end if;
    end if;
end process p_filter;
```

Signed arithmetic -- avoid abs(). abs(signed(-2^(N-1))) wraps to -2^(N-1) in VHDL two's complement. Always use explicit two-sided comparison:

```vhdl
-- CORRECT
if (err > -BAND) and (err < BAND) then ...

-- WRONG: abs(-2^31) = -2^31 in VHDL, passes the check incorrectly
if abs(err) < BAND then ...
```

Multiplier widths -- the product width equals the sum of operand widths:

```vhdl
-- 32x16 -> 48-bit: exact, maps to 2 DSP48E1
prop_v := phase_err * to_signed(KP, GAIN_W);   -- signed(31) x signed(15) = signed(47)

-- Never assign a product to a signal narrower than sum of operand widths
-- without an explicit resize/shift.
```

### State machine enum naming

VHDL is case-insensitive. State enum values like `TX_START` or `RX_DATA` will collide with port names `tx_start` or `rx_data`. Always prefix state enum values with `ST_`:

```vhdl
type tx_state_t is (ST_TX_IDLE, ST_TX_START, ST_TX_DATA, ST_TX_PARITY, ST_TX_STOP);
type rx_state_t is (ST_RX_IDLE, ST_RX_START_DET, ST_RX_DATA, ST_RX_PARITY, ST_RX_STOP);
```

### Multi-driver avoidance

A signal must be driven by exactly one process. When two processes need to coordinate on a shared counter or register, use a handshake signal:

```vhdl
-- WRONG: two processes driving tick_cnt
p_counter : process(clk) ... tick_cnt <= tick_cnt - 1; ...
p_fsm     : process(clk) ... tick_cnt <= (others => '0'); ...  -- multi-driver!

-- CORRECT: FSM sets a request flag, counter process reads it
signal reset_cnt : std_logic := '0';
p_counter : process(clk) ...
    if reset_cnt = '1' then tick_cnt <= (others => '0');
    else tick_cnt <= tick_cnt - 1; end if; ...
p_fsm     : process(clk) ... reset_cnt <= '1'; ...
```

### Error flag timing

When an error condition is detected in one state but the validity pulse fires in a later state, latch the error into an intermediate signal. Otherwise the default assignment clears it before the output state:

```vhdl
-- In PARITY state: latch the check result
rx_parity_bad <= '1' when (voted_bit /= expected_parity) else '0';

-- In STOP state: output coincident with rx_valid
parity_err <= rx_parity_bad;
rx_valid   <= '1';
```

Clock domain crossing -- synchroniser attribute:

```vhdl
signal sync1 : std_logic := '0';
signal sync2 : std_logic := '0';
attribute ASYNC_REG : string;
attribute ASYNC_REG of sync1 : signal is "TRUE";
attribute ASYNC_REG of sync2 : signal is "TRUE";
```

Dead code check -- before declaring done:

- Every signal declared must be both driven and read.
- Every generic must be used in at least one expression.
- Run: grep -n "signal " module.vhd and verify each appears on both LHS and RHS of an assignment.

### Monitor ports

Prefer promoting internal signals as proper output ports on the entity rather than using VHDL-2008 external names in a wrapper. This is simpler, more portable, and avoids Xsim tool-version dependencies. Synthesis tools trim unconnected monitor ports automatically.

```vhdl
entity my_module is
    port (
        -- ... functional ports ...
        -- Monitor outputs (synthesis tools trim if unconnected)
        mon_internal_a : out std_logic_vector(N-1 downto 0);
        mon_valid      : out std_logic
    );
end entity;

mon_internal_a <= std_logic_vector(internal_a);
mon_valid      <= valid_flag;
```

Use VHDL-2008 external names (`<<signal .u_core.X : T>>`) only when modifying the core entity is not an option.

### IBUF handling for external inputs

Vivado automatically inserts IBUF on all top-level input ports during synthesis. Do not instantiate IBUF explicitly -- Vivado parses both branches of an `if generate` and will error on missing entities even when the branch is inactive.

If manual IBUF control is required (specific IOSTANDARD, placement, or differential pairs), instantiate them in a board-specific top-level above the wrapper using `library UNISIM; use UNISIM.vcomponents.all`. Keep the wrapper board-agnostic with direct wire assignments.

### Wrapper evolution

A wrapper can grow from a testbench monitor into a system integration layer:

1. **Passthrough wrapper**: Exposes internal signals as monitor ports. Core entity unchanged.
2. **Monitor-port wrapper**: Monitor signals promoted to entity ports. Wrapper is pure wire passthrough.
3. **Integration wrapper**: Wrapper adds CDC synchronisers, input muxing, clock-enable generation. Core stays clean and reusable.

Keep the core generic-parameterised and board-agnostic; put all board-specific I/O handling in the wrapper.

---

## Stage 3 - Cycle-Accurate Python Testbench

The Python model is the primary correctness vehicle. It must mirror the VHDL at register-transfer level, not behavioural approximation.

### Model structure

Each VHDL process becomes a section in `clock()`. All reads use `self.X` (the old value); all writes go to `n_` local variables. The commit block at the end mirrors VHDL signal assignment semantics. If you write `self.X = value` mid-function and then read `self.X` later in the same `clock()` call, you have a delta-cycle bug.

For the full multi-process skeleton and arithmetic helpers (`as_signed32`, `clamp`, `shift_right_arithmetic`), see `references/python-testbench.md`.

### What to verify

- Steady-state output error (e.g. frequency ppm): must converge for all operating points.
- Lock/convergence time: must be finite and bounded by expected gain/bandwidth.
- Post-lock stability: output must stay within tolerance band for all inputs.
- Corner cases: maximum step input, minimum gain, boundary conditions.
- Constants: verify every numeric constant in the VHDL header comment by computing it independently in the Python script. Wrong comments become wrong register values when engineers copy-paste them.

### Plots to produce

Always generate at minimum the key state variables vs time, with pass/fail bands overlaid. Use matplotlib with the Agg backend (no display required):

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
```

---

## Stage 4 - Synthesis Audit

Before running actual Vivado synthesis, write a Python audit script that checks the VHDL source statically. This catches the majority of synthesis failures in seconds.

For the full audit script skeleton, synthesis checklist, DSP48E1 resource estimates, and cross-reference audit script, see `references/synthesis.md`.

Key checks the audit must cover:

- No 2**N integer overflow (N >= 32)
- Saturation constants use bit-vector aggregates
- No abs() on signed values in comparators
- No dead signals (declared but unused)
- Product widths >= sum of operand widths
- All for loop bounds are static
- ASYNC_REG on every CDC synchroniser pair

---

## Stage 5 - Python Iteration

After the synthesis audit fixes are applied to the VHDL, re-run the Python testbench against the updated RTL. The Python model must mirror any VHDL changes made during the audit. This stage answers:

1. Did any fix change functional behaviour?
2. Do new operating points work?
3. Are the simulation results consistent with the expected values from the header?

If the new version is a superset of the previous, run both versions side-by-side on the same test cases and verify identical results for common operating points.

---

## Stage 6 - SystemVerilog Testbench for Xsim

### File structure

Xsim cannot access VHDL architecture-internal signals via SV hierarchical references. Two approaches:

- **Preferred (monitor ports on entity):** Add monitor output ports directly to the core entity. The SV testbench instantiates the core directly. No wrapper needed. Synthesis tools trim unconnected monitor ports automatically.
- **Wrapper approach:** If the core entity cannot be modified, create a VHDL wrapper (`module_wrap.vhd`) that uses VHDL-2008 external names to expose internals. The SV testbench instantiates the wrapper.

In either case: no cross-language hierarchical references anywhere in the SV file.

### Xsim compatibility rules (X1-X7)

Check every testbench against all seven rules before declaring it Xsim-ready. For the full rules table, code examples (absolute-time scheduling, reference driver pattern, $signed casts, frequency word arithmetic), and compile order, see `references/xsim.md`.

Critical rules summary:

- **X1**: No SV->VHDL hierarchical refs. Use wrapper monitor ports.
- **X4**: No `always_ff` mixing blocking `real =` with non-blocking `<=`. Use `always @(negedge clk)` with all blocking.
- **X6**: Never write `2.0 ** 32`. Use `4294967296.0`.
- **X7**: No successive `#(delay)` for non-integer timing. Use absolute-time edge scheduling.

### One-cycle pulse capture

When the DUT produces a 1-cycle validity pulse (e.g. `rx_valid`, `output_valid`), a task that waits for a separate event (e.g. `tx_done`) will miss it. Use a background `always` monitor to latch the pulse:

```systemverilog
// Background monitor -- captures 1-cycle pulses
logic rx_got_valid;
logic [7:0] rx_got_data;
logic rx_got_parity_err, rx_got_frame_err, rx_got_overrun_err;

always @(posedge clk) begin
    if (dut.rx_valid) begin
        rx_got_valid      <= 1;
        rx_got_data       <= dut.rx_data;
        rx_got_parity_err <= dut.parity_err;
        rx_got_frame_err  <= dut.frame_err;
        rx_got_overrun_err <= dut.overrun_err;
    end
end

// Task clears the latch, runs the operation, waits for both conditions
task send_and_receive(input [7:0] data);
    rx_got_valid = 0;
    // ... start TX ...
    wait (tx_done && rx_got_valid);
    // Check rx_got_data, rx_got_parity_err, etc.
endtask
```

Apply the same pattern in the Python model: check for validity pulses inside the main `clock()` loop of `send_byte()`, not after it returns.

---

## Stage 7 - VCD Post-Simulation Verification

The VCD verifier provides an independent verification path that does not rely on the SV testbench's own pass/fail reporting. If the SV testbench has a bug in its checker logic, the VCD verifier catches it.

**VCD verification comes before CSV cross-check** because VCD is generated by the simulator engine itself -- it is ground truth for what the signals actually did, independent of any testbench code. The CSV logger, by contrast, is SV testbench code that can have its own timing bugs (wrong delta cycle, missed events, sampling races). When debugging, VCD eliminates the testbench as a variable. A VCD verifier that independently recomputes expected values (e.g. CRC from raw bit streams) will pinpoint RTL bugs that CSV cross-checking may miss or misattribute to testbench issues.

### Why VCD verification matters

The SV testbench (Stage 6) and CSV logger (Stage 8) are both authored alongside the VHDL -- they share assumptions and can share bugs. The VCD verifier is a separate Python program that reads raw waveform data and applies its own verification logic. Three independent verification paths (Python model, SV self-checks, VCD verifier) make it very unlikely that a bug survives in all three.

### Architecture

Structure the verifier as three layers. Pure Python, no dependencies, handles multi-GB files:

1. **Streaming VCD parser** -- chunked I/O (128 MB), yields `(timestamp, changes)` tuples
2. **Signal tracker** -- maintains current state, provides `get()`, `get_signed()`, `rising_edge()`
3. **Verification engine** -- runs checks at meaningful events, collects per-segment PASS/FAIL

For the full three-layer skeleton, selective VCD Tcl logging, verification checks list, and build script integration, see `references/vcd-verify.md`.

### Integration with the build script

Add a `--vcd-verify` mode to the build/run script:

```bash
xsim my_module_sim -tclbatch _run_vcd.tcl
python my_module_vcd_verify.py my_module_verify.vcd
```

This gives three independent pass/fail signals: SV self-checks, VCD verification, and CSV cross-check. All three must pass before declaring the design verified.

---

## Stage 8 - CSV Debug Logger and Cross-Check

The SV testbench must include a CSV logger that captures key signals at meaningful events. This provides a compact, human-readable trace for debugging and a cross-check against the Python model's predictions.

### Why a separate stage

The SV testbench self-checks (Stage 6) verify the RTL against pass/fail thresholds, but they cannot catch subtle dynamics bugs -- wrong convergence rate, unexpected oscillation patterns, or off-by-one pipeline latency. The CSV trace exposes the actual signal trajectory so it can be compared directly against the Python model's output.

**Note:** CSV data is generated by SV testbench code (`$fwrite` calls), so it inherits any testbench timing bugs. VCD verification (Stage 7) should pass before relying on CSV data for cross-checking.

### What to log

Log one row per meaningful event, not every sys_clk cycle. Choose the event that captures the module's control loop or data pipeline rate (e.g. `output_valid` for a filter, `phase_err_valid` for a PLL, state transitions for a state machine).

For the CSV logger skeleton and capture window timing pattern, see the "CSV debug logger" section in `references/xsim.md`.

### Cross-checking against the Python model

After simulation, write a Python script that:

1. Reads the CSV and the Python model's logged output
2. Aligns them by event count (not wall time -- Xsim time resolution differs from Python's)
3. Compares each signal column within a tolerance (exact match for integers, epsilon for reals)
4. Reports the first divergence point with both expected and actual values

A divergence at event N means the VHDL and Python model disagree from that cycle onward. The most common causes: missed pipeline register in the Python model, wrong reset value, or a commit-order bug in clock().

---

## Stage 9 - Vivado Synthesis

After all simulation-based verification passes, run actual Vivado synthesis to get real resource utilization and timing numbers. This catches issues the static audit (Stage 4) cannot: inferred latches, unexpected BRAM/DSP inference, timing violations on real routing.

### Synthesis TCL scripts

Create two TCL scripts in the project directory:

**`synth_check.tcl`** — utilization and unconstrained timing:

```tcl
set proj_dir [pwd]
create_project -in_memory -part xc7z020clg484-1
add_files ${proj_dir}/my_module.vhd
set_property file_type {VHDL 2008} [get_files my_module.vhd]
synth_design -top my_module -part xc7z020clg484-1
report_utilization -file ${proj_dir}/my_module_utilization.txt
report_timing_summary -file ${proj_dir}/my_module_timing.txt
report_drc -file ${proj_dir}/my_module_drc.txt
```

**`synth_timing.tcl`** — constrained timing at target frequency:

```tcl
set proj_dir [pwd]
create_project -in_memory -part xc7z020clg484-1
add_files ${proj_dir}/my_module.vhd
set_property file_type {VHDL 2008} [get_files my_module.vhd]
synth_design -top my_module -part xc7z020clg484-1

# Clock constraint (adjust period for target frequency)
create_clock -period 10.0 -name sys_clk [get_ports clk]

# False paths for async inputs (adjust per design)
# set_false_path -from [get_ports async_input]

# False paths for slow-changing config inputs
# set_false_path -from [get_ports {config_port[*]}]

report_timing_summary -file ${proj_dir}/my_module_timing_constrained.txt
report_timing -nworst 5 -file ${proj_dir}/my_module_timing_paths.txt
```

Adapt the false-path constraints for the specific module's async and config inputs.

### Running synthesis

```bash
bash -c 'source /path/to/settings64.sh && vivado -mode batch -source synth_check.tcl -log vivado_synth.log -journal vivado_synth.jou'
bash -c 'source /path/to/settings64.sh && vivado -mode batch -source synth_timing.tcl -log vivado_timing.log -journal vivado_timing.jou'
```

### What to check

After synthesis completes, read the generated report files and verify:

1. **Utilization** (`_utilization.txt`): LUTs, FFs, BRAM, DSP48E1, BUFG counts. Compare against expectations from Stage 1.
2. **Timing** (`_timing_constrained.txt`): WNS (setup), WHS (hold), WPWS (pulse width) — all must be positive (MET).
3. **DRC** (`_drc.txt`): No critical violations. Expected warnings (missing I/O constraints, NSTD) are acceptable.
4. **Critical path** (`_timing_paths.txt`): Identify the worst-case path and its LUT depth.

If timing is not met, consider: adding pipeline registers, restructuring combinational logic, or adjusting the target frequency.

---

## Stage 10 - CLAUDE.md

The final stage creates a `CLAUDE.md` project guide in the module's root directory. This file serves as the permanent record of the design, its verification status, and synthesis results.

### Required sections

```markdown
# Module Name — Project Guide

## What This Is
One-line description of the module, its key parameters, and target.

## Architecture
- FSM states, data flow, key design decisions
- Block diagram reference (point to VHDL header)

## Files
| File | Purpose |
|------|---------|
| `module.vhd` | RTL source |
| `module_tb.py` | Cycle-accurate Python model + tests |
| ... | ... |

## Build & Test
```bash
./run_module.sh          # compile, simulate, verify
./run_module.sh clean    # clean artifacts first
```

Individual steps listed separately.

## Synthesis Results

### Resource Utilization
| Resource | Used | Available | Util% |
|----------|------|-----------|-------|
| LUTs     | N    | 53,200    | N%    |
| FFs      | N    | 106,400   | N%    |
| BRAM     | N    | 140       | N%    |
| DSP48E1  | N    | 220       | N%    |
| BUFG     | N    | 32        | N%    |

### Timing Summary
| Check             | Worst Slack | Status |
|-------------------|-------------|--------|
| Setup (WNS)       | +X.XXX ns   | MET    |
| Hold (WHS)        | +X.XXX ns   | MET    |
| Pulse Width (WPWS)| +X.XXX ns   | MET    |

### Critical Path
Description of worst-case path: source → destination, LUT depth, max frequency estimate.

## Vivado
- Version and settings path
- Reminder that every shell command must source settings64.sh

## Conventions
- Reset convention, process naming, state prefix, monitor ports, etc.
```

### Rules

- Extract utilization and timing numbers from the Stage 9 report files — do not guess or estimate.
- If synthesis was not run (e.g. Vivado unavailable), include a "Synthesis Results" section with "Not yet run" and the commands to run it.
- Keep the file concise — it is a quick-reference guide, not a full specification.

---

## Cross-Reference Audit

Run after every file change. Check every constant, port name, signal name, and arithmetic formula is consistent across the VHDL entity, wrapper, and SV testbench. For the full audit script, see `references/synthesis.md`.

---

## Methodology Principles

**Read the full file before modifying it.** Never edit a VHDL file based on a summary. Read every line, then plan every change, then make them all at once.

**Audit scripts are not throwaway code.** Write them to be re-runnable. They become the regression test for future modifications.

**Carry every fix forward through all layers.** If a bug is found in the VHDL, the same fix must be reflected in the Python model and the SV testbench. Each layer must document why the fix is present.

**Verify comments with the same rigour as code.** Every numeric constant in a header comment must be independently calculated. Wrong comments become bugs when copy-pasted into the next design.

**Never use abs() on signed values in comparators.** The two's-complement edge case silently passes out-of-range values. Always use explicit two-sided comparison. Apply this rule in VHDL, Python, and SystemVerilog.

**The Python model is the spec.** When the VHDL and the Python model disagree, the Python model is probably right -- it has no delta-cycle complexity. Fix the VHDL to match, not the other way around, unless the Python model has a demonstrable register-commitment bug.

### Clock-enable edge detection

When a module generates an internal clock signal that must sample data, avoid routing it as a fabric clock. Instead, detect its edges in the sys_clk domain and use a clock enable:

```vhdl
signal output_d1  : std_logic := '0';
signal sample_en  : std_logic := '0';

p_edge_detect : process(sys_clk)
begin
    if rising_edge(sys_clk) then
        output_d1 <= output_sig;
        if edge_sel = '0' then
            sample_en <= (not output_d1) and output_sig;       -- rising edge
        else
            sample_en <= output_d1 and (not output_sig);       -- falling edge
        end if;
    end if;
end process;
```

This avoids CDC crossings, BUFR/BUFG consumption, `create_generated_clock` constraints, and clock-region placement restrictions. Use BUFR/BUFG only when the generated clock fans out to many registers across clock regions or when sampling jitter must be tighter than 1 sys_clk period.
