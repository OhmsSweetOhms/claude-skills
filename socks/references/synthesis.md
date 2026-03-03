# Synthesis Audit Script and Checklist

Read this file before Stage 3 (synthesis audit) and Stage 9 (Vivado synthesis).

## Audit script structure

The audit script checks VHDL source files for 12 common synthesis hazards. It takes file paths as arguments and prints per-file per-check PASS/FAIL. Exit code 0 if all pass, 1 if any fail.

Use `socks/scripts/stage4_audit.py` for the automated version. The 12 checks are:

1. No `2**N` integer overflow (N >= 32)
2. Saturation constants use bit-vector aggregates (no `to_signed(2**N, ...)`)
3. No `abs()` on signed values
4. No dead signals (declared but unused)
5. Product widths >= sum of operand widths
6. All for loop bounds are static constants
7. ASYNC_REG attribute on CDC synchroniser pairs
8. No `clk'event` usage (only `rising_edge`)
9. Reset inside `rising_edge(clk)` block (synchronous reset)
10. Architecture name is `rtl`
11. No component declarations (direct entity instantiation)
12. State enum values use `ST_` prefix

---

## Synthesis checklist (Vivado / Zynq)

| Check | Rule |
|-------|------|
| Integer constants | No 2**N with N >= 32 -- use bit-vector aggregate |
| Multiply widths | Product signal must be >= sum of operand widths |
| abs() on signed | Replace with two-sided comparison |
| Async reset | Zynq prefers synchronous reset -- use `if rising_edge(clk)` outer |
| ASYNC_REG attribute | Required on every CDC synchroniser pair |
| DSP inference | 32x16 -> DSP48E1. Wider multiplies use LUTs unless `use_dsp = "yes"` pragma |
| Combinational path | Multiply + accumulate + clamp = ~6-8 ns on 7-series. Budget <= 8 ns at 100 MHz |
| shift_right(signed, n) | Verify resize() applied before narrowing assignment |
| Loop bounds | All for loop bounds must be static (generics or constants) |

---

## DSP48E1 resource estimate

```
Resource budget for Zynq-7010 (80 DSP48E1 total):
  32x16 multiply    -> 2 DSP48E1
  32-bit adder      -> 0-1 DSP48E1 (tool decides; usually LUT)
  Accumulator       -> 1 DSP48E1 if paired with multiply above

Keep total module usage under 50% to leave headroom for the rest of the design.
```

---

## Synthesis TCL scripts

### `synth_check.tcl` — utilization and unconstrained timing

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

### `synth_timing.tcl` — constrained timing at target frequency

```tcl
set proj_dir [pwd]
create_project -in_memory -part xc7z020clg484-1
add_files ${proj_dir}/my_module.vhd
set_property file_type {VHDL 2008} [get_files my_module.vhd]
synth_design -top my_module -part xc7z020clg484-1
create_clock -period 10.0 -name sys_clk [get_ports clk]
report_timing_summary -file ${proj_dir}/my_module_timing_constrained.txt
report_timing -nworst 5 -file ${proj_dir}/my_module_timing_paths.txt
```

### Running synthesis

Use `scripts/stage9_synth.py` which generates TCL, invokes Vivado, and parses reports:

```bash
python scripts/stage9_synth.py --top my_module --part xc7z020clg484-1 \
    --src-dir src/ --out-dir sim/
python scripts/stage9_synth.py --top my_module --part xc7z020clg484-1 \
    --src-dir src/ --out-dir sim/ --clock-period 10.0 --async-ports rxd
```

### What to check in reports

1. **Utilization** (`_utilization.txt`): LUTs, FFs, BRAM, DSP48E1, BUFG counts
2. **Timing** (`_timing_constrained.txt`): WNS (setup), WHS (hold), WPWS (pulse width) — all must be positive (MET)
3. **DRC** (`_drc.txt`): No critical violations. NSTD-1/UCIO-1 (missing I/O constraints) expected for synthesis-only check
4. **Critical path** (`_timing_paths.txt`): Worst-case path, LUT depth, max frequency estimate

---

## Cross-reference audit script

Run after every file change. Check consistency across VHDL entity, wrapper, and SV testbench:

```python
checks = [
    ("All VHDL ports in SV port map",
     all(re.search(rf'\.{p}\s*\(', sv) for p in vhdl_ports)),
    ("All VHDL generics in SV parameter map",
     all(re.search(rf'\.{g}\s*\(', sv) for g in vhdl_generics)),
    ("Monitor ports in wrapper entity",
     all(p in wrap for p in monitor_ports)),
    ("No HRE in SV TB code",
     not re.search(r'\.dut\.\w+', sv_code_only)),
    ("4294967296.0 used, not 2.0**32",
     '4294967296.0' in sv_code_only and
     not re.search(r'2\.0\s*\*\*\s*32', sv_code_only)),
]
```

Print explicit PASS/FAIL for every check. Do not declare a file complete until all checks pass.
