# Synthesis Audit Script and Checklist

## Audit script structure

```python
import re

with open("module.vhd") as f:
    vhd = f.read()

results = []
def chk(name, ok, note=""):
    results.append((ok, name, note))

chk("No 2**N integer overflow",
    not re.search(r'2\s*\*\*\s*([3-9]\d|[4-9])', vhd),
    "2**N with N>=32 overflows VHDL integer")

chk("Saturation constants use bit-vector aggregates",
    bool(re.search(r"=> '1', others => '0'", vhd)))

chk("No abs() on signed values in comparator",
    not re.search(r'\babs\s*\(\s*\w*err\w*\s*\)', vhd))

signals = re.findall(r'signal\s+(\w+)\s*:', vhd)
for sig in signals:
    uses = len(re.findall(rf'\b{sig}\b', vhd))
    chk(f"Signal '{sig}' used (not dead)", uses >= 3,
        f"Only {uses} occurrences")

fails = [r for r in results if not r[0]]
for ok, name, note in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{note}]" if note and not ok else ""))
print(f"\n{len(results)-len(fails)}/{len(results)} checks passed.")
if fails:
    print("BLOCKING FAILURES - fix before synthesis.")
```

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

## Cross-reference audit script

Run after every file change. Check every constant, port name, signal name, and arithmetic formula is consistent across the VHDL entity, wrapper, and SV testbench.

```python
import re

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

for ok, name, note in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
```

Print explicit PASS/FAIL for every check. Do not declare a file complete until all checks pass.
