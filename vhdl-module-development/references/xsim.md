# Xsim Compatibility Rules and Patterns

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

## CSV debug logger

Log one row per meaningful event, not every sys_clk cycle. Choose the event that captures the module's control loop or data pipeline rate (e.g. `output_valid` for a filter, `phase_err_valid` for a PLL, state transitions for a state machine).

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

Start capture BEFORE the event you want to debug. If the logger only enables after a success condition, failures produce empty CSV files.

```systemverilog
// Enable CSV before the wait
csv_enable = 1;
csv_cycle  = 0;

// ... wait for convergence / lock / completion ...

// Disable CSV after timeout or capture window expires
csv_enable = 0;
$fflush(csv_fd);
```

---

## Compile order

```bash
xvhdl --2008 my_module.vhd
xvhdl --2008 my_module_wrap.vhd
xvlog -sv    my_module_tb_xsim.sv
xelab -debug typical my_module_tb -s my_module_sim
xsim my_module_sim -R
```
