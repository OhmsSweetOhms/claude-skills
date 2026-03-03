# Xsim Compatibility Rules and Patterns

Read this file before Stage 7 (SystemVerilog testbench for Xsim).

## Compatibility rules

Check every testbench against all seven rules before declaring it Xsim-ready.

| Code | Rule | Xsim behaviour if violated |
|------|------|---------------------------|
| X1 | No SV->VHDL hierarchical refs to internal signals. Use wrapper monitor ports. | Elaboration error |
| X2 | No real variable declarations inside named generate blocks. Declare at module scope. | Compile error |
| X3 | No `localparam real NAME [N]` unpacked real arrays. Use module-scope `real NAME [N]`; assign in initial block. | Compile error |
| X4 | No always_ff mixing blocking `real =` with non-blocking `<=` to logic. Use `always @(negedge clk)` with all blocking. negedge prevents delta-cycle races with VHDL synchronisers. | Compile error |
| X5 | No assert/cover property inside generate loops with genvar-indexed arrays. Use a single `always_ff` with a for loop. | Silent misbehaviour or elaboration error |
| X6 | Never write `2.0 ** 32`. Use the literal `4294967296.0`. | Evaluates to 0 at runtime in some Xsim versions |
| X7 | No successive `#(delay)` for non-integer timing. Use absolute-time edge scheduling. | Cumulative drift corrupts stimulus after milliseconds of sim time |

---

## Absolute-time edge scheduling (X7 fix)

Xsim quantises every `#(delay)` to 1 ps resolution. When a stimulus half-period is irrational (e.g. 33333.333 ps at 15 MHz), successive `#(half_period)` calls accumulate rounding error -- at 15 MHz, the drift reaches 1 bit period after ~3.3 ms, corrupting data-dependent tests over long simulations.

The fix: compute each edge time as a single multiply from a base time. The quantisation error is bounded to +/-0.5 ps regardless of simulation length.

```systemverilog
// WRONG: cumulative drift from successive delays
forever begin
    stim_out = 1'b1;
    #(half_period_ns);              // error accumulates each call
    stim_out = 1'b0;
    #(half_period_ns);
end

// CORRECT: absolute-time scheduling, bounded error
real base_ns;
integer edge_num;

initial begin
    base_ns = 0.0;
    edge_num = 0;
    forever begin
        edge_num = edge_num + 1;
        stim_out = 1'b1;
        #(base_ns + edge_num * half_period_ns - $realtime);
        edge_num = edge_num + 1;
        stim_out = 1'b0;
        #(base_ns + edge_num * half_period_ns - $realtime);
    end
end
```

On frequency change, reset `base_ns = $realtime` and `edge_num = 0` to avoid a phase glitch.

---

## Reference driver pattern (correct for Xsim)

```systemverilog
real ref_phase [N_CASES];
real F_TARGET_HZ [N_CASES];

initial begin : init_reals
    F_TARGET_HZ[0] = 1.0e6;
    for (int i = 0; i < N_CASES; i++) ref_phase[i] = 0.0;
end

// always @(negedge clk) not always_ff -- X4 fix
// negedge: ref_in stable before VHDL p_sync samples it on posedge
always @(negedge clk) begin : ref_driver
    for (int i = 0; i < N_CASES; i++) begin
        ref_phase[i] = ref_phase[i] + (F_TARGET_HZ[i] / real'(SYS_CLK_HZ));
        if (ref_phase[i] >= 1.0) ref_phase[i] -= 1.0;
        ref_in[i] = (ref_phase[i] < 0.5) ? 1'b1 : 1'b0;
    end
end
```

---

## $signed() cast on VHDL signed signals

VHDL signed(N-1 downto 0) arrives in SV as logic[N-1:0] -- unsigned by default. Apply $signed() everywhere the value is used arithmetically or compared.

```systemverilog
// CORRECT
if ($signed(mon_err) > -BAND && $signed(mon_err) < BAND) ...

// WRONG - interprets the MSB as magnitude, not sign
if (mon_err < BAND) ...  // always true for negative values
```

---

## Frequency word arithmetic -- avoid 2.0 ** 32

```systemverilog
// CORRECT (X6 fix)
real out_mhz = real'(mon_freq_word) * real'(SYS_CLK_HZ) / 4294967296.0 / 1.0e6;

// WRONG - unreliable in Xsim
real out_mhz = real'(mon_freq_word) * real'(SYS_CLK_HZ) / (2.0 ** 32) / 1.0e6;
```

---

## One-cycle pulse capture

When the DUT produces a 1-cycle validity pulse (e.g. `rx_valid`, `output_valid`), a task that waits for a separate event (e.g. `tx_done`) will miss it. Use a background `always` monitor to latch the pulse:

```systemverilog
// Background monitor -- captures 1-cycle pulses
logic rx_got_valid;
logic [7:0] rx_got_data;

always @(posedge clk) begin
    if (dut.rx_valid) begin
        rx_got_valid      <= 1;
        rx_got_data       <= dut.rx_data;
    end
end

// Task clears the latch, runs the operation, waits for both conditions
task send_and_receive(input [7:0] data);
    rx_got_valid = 0;
    // ... start TX ...
    wait (tx_done && rx_got_valid);
    // Check rx_got_data, etc.
endtask
```

---

## CSV debug logger

Log one row per meaningful event, not every sys_clk cycle.

```systemverilog
integer csv_fd;
integer csv_cycle;
reg csv_enable;

initial begin
    csv_fd = $fopen("debug.csv", "w");
    $fwrite(csv_fd, "cycle,time_ns,event,signal_a,signal_b,signal_c\n");
    csv_cycle = 0;
    csv_enable = 0;
end

always @(posedge clk) begin : csv_logger
    if (csv_enable && event_valid) begin
        $fwrite(csv_fd, "%0d,%0d,EV,%0d,%0d,%b\n",
                csv_cycle, $time,
                $signed(mon_signal_a), mon_signal_b, mon_flag);
        csv_cycle = csv_cycle + 1;
    end
end
```

Start capture BEFORE the event you want to debug. Enable CSV before the wait, disable after timeout or capture window.

---

## Build and simulate

Use `scripts/stage6_xsim.py` for all compilation and simulation. It handles Vivado settings sourcing, compile order, elaboration, and simulation automatically.

```bash
# Full build + simulate (auto-discovers src/*.vhd and tb/*.sv)
python scripts/stage6_xsim.py --project-dir . --top module_tb

# Compile only
python scripts/stage6_xsim.py --project-dir . --top module_tb --compile-only

# Simulate only (already compiled)
python scripts/stage6_xsim.py --project-dir . --top module_tb --sim-only

# With VCD generation
python scripts/stage6_xsim.py --project-dir . --top module_tb --vcd

# Clean artifacts
python scripts/stage6_xsim.py --project-dir . --clean
```

### Compile order

The script compiles in this order:

1. **VHDL** (`xvhdl --2008`) — two-pass when multiple files
2. **SV** (`xvlog -sv`)
3. **DPI-C** (`xsc`) — if `tb/*.c` files exist
4. **Elaborate** (`xelab`, with `-sv_lib dpi` if DPI-C present)
5. **Simulate** (`xsim`)

Each step sources `settings64.sh` internally.

**Two-pass VHDL compilation:** Entity names don't sort alphabetically by dependency (e.g. `sdlc_axi.vhd` sorts before `sdlc_v1.vhd` that it instantiates). Pass 1 compiles all files silently to populate the library; pass 2 recompiles with error reporting. This resolves forward references without requiring manual file ordering. The two-pass approach only activates when there are multiple VHDL files.

---

## DPI-C integration

When the SV testbench needs to call C functions (e.g. to share parameter computation logic with the bare-metal driver), place `.c` files in `tb/`. The build script auto-discovers them.

### C side (`tb/module_dpi.c`)

```c
#include <stdint.h>

void calc_params(
    unsigned int  sys_clk_hz,
    unsigned int  bit_rate_hz,
    unsigned int *divisor,
    unsigned int *freq_word)
{
    *divisor   = sys_clk_hz / bit_rate_hz - 1;
    uint64_t num = ((uint64_t)bit_rate_hz << 32) + sys_clk_hz / 2;
    *freq_word = (unsigned int)(num / sys_clk_hz);
}
```

No special headers needed — Xsim's `xsc` compiler handles the DPI linkage.

### SV side

```systemverilog
import "DPI-C" function void calc_params(
    input  int unsigned sys_clk_hz,
    input  int unsigned bit_rate_hz,
    output int unsigned divisor,
    output int unsigned freq_word
);

// Usage in initial block or task:
int unsigned div_val, fw_val;
calc_params(100_000_000, 1_000_000, div_val, fw_val);
```

### Build flow

`stage6_xsim.py` handles everything automatically:
- Discovers `tb/*.c` files
- Compiles them with `xsc` after SV compilation
- Adds `-sv_lib dpi` to the `xelab` elaboration command

No manual flags needed — just put the `.c` file in `tb/` and add the `import "DPI-C"` declaration in SV.

### When to use DPI-C

Use DPI-C when the SV testbench and bare-metal C driver share non-trivial computation (DPLL parameter calculation, CRC tables, protocol encoding). This keeps the formulas in one place — the C driver is the single source of truth, and the TB calls the same code. Avoids the risk of SV and C diverging silently.
