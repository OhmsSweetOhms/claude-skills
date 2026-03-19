# SOCKS Test Discovery Phase -- System Scope

For system scope designs (Xilinx IP block design + optional custom HDL wrapper).
Module and block scope test discovery is in `references/test_discovery.md`.

---

## How Test Discovery Works (System Scope)

1. Claude reads DESIGN-INTENT.md and hil.json (NOT SV TB, VCD, or driver headers -- none exist for system scope)
2. Claude asks the **core questions** below
3. Claude analyzes answers and asks **generative follow-ups**
4. Claude synthesizes into `docs/TEST-INTENT.md` (system scope template below)
5. User approves
6. Run `--hil` (no `--top` needed for system scope -- uses `dut.entity` from socks.json)

---

## Core Questions

1. **What AXI peripherals need register-level testing?** (read/write verification per IP core)
2. **What external I/O needs physical verification (ILA)?** (which pins/signals to probe)
3. **Can loopback be implemented internally in VHDL?** (e.g., SPI MOSI->MISO inside wrapper)
4. **What firmware test scenarios?** (register access, loopback, multi-device, GPIO patterns)
5. **What ILA captures are needed?** (trigger signals, expected transitions)
6. **What are the ILA timing constraints?** (4096 samples at fclk -- capture window vs test duration)
7. **What constitutes hardware pass/fail?** (register read-back, loopback data match, status checks)
8. **Any board-specific overrides?** (serial port, baud rate, non-default addresses)

---

## Generative Follow-Up Patterns

### "Register access test"
- Ask: which registers, expected default values, read-back verification
- Map to Xil_Out32/Xil_In32 sequences

### "SPI loopback"
- Ask: internal VHDL loopback or external jumper?
- Calculate: byte duration vs ILA window (4096 samples at fclk)
- If internal: loopback goes in system_wrapper.vhd

### "Multi-device"
- Ask: how many slaves, what varies per slave, sequential or parallel
- Map to slave select register sequences

### "GPIO toggle"
- Ask: which patterns, delays between (for ILA capture window), active-low/high
- Calculate: if toggle is visible in ILA window

---

## TEST-INTENT.md Template (System Scope)

```markdown
# Test Intent: {system_name}

## Source Analysis
- **Project type:** Vivado block design (Xilinx IP) + custom HDL wrapper (if ILA probes)
- **Custom RTL:** {src/system_wrapper.vhd if probe wrapper exists, otherwise "None"}
- **SV TB:** None (no simulation)
- **Signal map:** N/A (no VCD -- ILA captures are the verification source)
- **Driver API:** Bare-metal register-level access via Xil_Out32/Xil_In32

## Test Scenarios

### Scenario 1: {name}
- **What:** {description}
- **Register sequence:** {step-by-step register reads/writes}
- **Expected behavior:** {what registers/signals should do}
- **Success criteria:** {read-back match, status bits, data comparison}
- **ILA verification:** {which probed signals to check, expected transitions}

### Scenario 2: {name}
...

## ILA Capture Strategy
- **Monitor signals (via registered probe wrapper):**
  - {signal_name} ({width} bits) -- {description}
  - ...
- **Total probe width:** {sum} bits
- **ILA depth:** 4096 samples at {fclk} MHz = {window_us} us
- **Transaction duration:** {at target rate, fits in capture window?}

### Capture Plan
| # | Trigger signal | Trigger value | Covers | Description |
|---|---------------|---------------|--------|-------------|
| 1 | ... | ... | ... | ... |

## Firmware Structure
- **Base addresses:** {IP -> address for each core}
- **Test pattern:** {how data is generated/verified}
- **Loopback:** {internal VHDL or external jumper}
- **Verification:** {compare strategy, UART output format}
- **Output:** HIL_PASS if all pass, HIL_FAIL on first failure

## VHDL Loopback Design
{Description of internal loopback if applicable -- which signals connected, always-on or gated}

## Pass/Fail Criteria
- [ ] {criterion 1}
- [ ] {criterion 2}
- ...
```

---

## Scope Creep Detection

If user requests tests requiring custom RTL (e.g., FSM coverage, protocol-level verification), ask if this warrants a separate `/socks --design block/module` instance with full simulation.
