# SOCKS Test Discovery Phase

> **System scope:** For system scope designs (Xilinx IP block design),
> read `references/test-discovery-system.md` instead of this file.

The test discovery phase runs before the HIL flow (`--hil`). It produces
`docs/TEST-INTENT.md` -- the contract that drives artifact generation
(`hil_prep.py`) and ILA capture planning.

Test discovery is a **conversation** between Claude and the user, not a script.

---

## How Test Discovery Works

1. Claude reads existing project artifacts:
   - `tb/*_tb.sv` -- SV testbench (test scenarios, register sequences, verification checks)
   - `tb/vcd_signal_map.json` -- observable signals
   - `sw/*.h` -- driver API (function prototypes, register defines)
   - `src/*.vhd` -- FSM state types, monitor ports, generics

2. Claude asks the **core questions** below
3. Claude analyzes answers and asks **generative follow-ups**
4. Claude synthesizes all answers into `docs/TEST-INTENT.md`
5. User approves or iterates
6. Once approved, run: `python scripts/socks.py --project-dir . --hil --top <entity>`

**Generative follow-ups** are Claude-driven clarifications triggered by user
answers. Examples:
- User says "loopback test" -> Claude calculates: frame duration at target baud
  vs ILA window (4096 samples at fclk), proposes capture breakdown
- User says "error injection" -> Claude asks: "How? Corrupt sync word? Break
  loopback wire? Software-controlled?"
- User says "all states" -> Claude shows FSM state list from VHDL, asks which
  transitions matter

Ask follow-ups until every section of the TEST-INTENT template can be filled
without guessing.

---

## Core Questions

1. **What test scenarios from the SV TB should run on hardware?**
   (loopback, error injection, multi-frame, throughput, etc.)

2. **What ILA captures are needed?**
   Which FSM states or transitions to observe? Which monitor signals matter?

3. **What are the ILA timing constraints?**
   4096 samples at fclk_mhz -- how long is the capture window vs test duration?
   Does the test complete within one capture, or must it be broken up?

4. **How should captures be broken up?**
   One trigger per state transition? Group by test phase? One capture per frame?

5. **What constitutes hardware pass/fail?**
   Data match? Counter values? No CRC errors? Status register checks?

6. **Any board-specific overrides?**
   Serial port path, FPGA part, baud rate, non-default base address?

---

## Generative Follow-Up Patterns

These are triggered by specific user answers:

### "Loopback test"
- Calculate frame duration: `(sync_bits + num_words * 32 + crc_bits) * (1/baud)`
- Calculate ILA window: `4096 / (fclk_mhz * 1e6)` seconds
- Compare: does one frame fit in the ILA window?
- Propose: capture TX FSM entering SYNC, capture RX frame valid, etc.

### "Error injection"
- Ask: "How is the error injected? Options:"
  - Corrupt sync word via software (write wrong value)
  - Break loopback wire (physical -- can't automate)
  - Software-controlled CRC corruption (if supported)
  - Timeout test (disable TX, wait for RX timeout)
- Map each to specific register sequences and expected status bits

### "All FSM states"
- Parse `type *_state_t is (...)` from VHDL sources
- List states with their `'pos` encoding values
- Ask: "Which transitions are most important to capture?"
- Suggest: one capture per major transition (IDLE->active, active->complete)

### "Multi-frame test"
- Ask: "How many frames? What varies between frames?"
- Calculate: total test duration vs ILA window
- Suggest: debug mode with inter-frame gap for per-frame ILA captures

---

## TEST-INTENT.md Template

Claude synthesizes discovery answers into this structure. All sections are
required for `hil_prep.py` to generate artifacts.

```markdown
# Test Intent: {entity_name}

## Source Analysis
- **SV TB:** {summary of what tb/*_tb.sv tests}
- **Signal map:** {observable signals from vcd_signal_map.json}
- **Driver API:** {key functions from sw/*.h}

## Test Scenarios
### Scenario 1: {name}
- **What:** {description}
- **Register sequence:** {init -> config -> enable -> wait -> verify}
- **Expected behavior:** {what signals should do}
- **Success criteria:** {data match, status bits, counters}

### Scenario 2: {name}
...

## ILA Capture Strategy
- **Monitor signals:** {list with widths}
- **Total probe width:** {sum of monitor widths + loopback}
- **ILA depth:** 4096 samples at {fclk_mhz} MHz = {window_us} us
- **Frame/transaction duration:** {duration at target baud}

### Capture Plan
| # | Trigger signal | Trigger value | Covers | Description |
|---|---------------|---------------|--------|-------------|
| 1 | mon_tx_state_s | 001 (SYNC) | TX start | Capture TX FSM entering SYNC |
| 2 | ... | ... | ... | ... |

## Firmware Structure
- **Init params:** {base_addr, clk_hz, baud, sync_word, num_words}
- **Test pattern:** {how to generate TX data}
- **Loopback:** {enable sequence -- RX first, then TX}
- **Verification:** {compare RX buffer to TX buffer, check status}
- **Debug mode:** {serial pacing for ILA, inter-frame gap}
- **Iteration count:** normal={N}, debug={M}

## FSM Encodings
| State type | States | Encoding |
|------------|--------|----------|
| tx_frame_state_t | ST_TXF_IDLE=0, ST_TXF_SYNC=1, ... | positional (3 bits) |
| rx_frame_state_t | ST_RXF_IDLE=0, ST_RXF_HUNT=1, ... | positional (3 bits) |

## Signal Observables
| Signal | Width | Expected behavior | VCD path |
|--------|-------|-------------------|----------|
| mon_tx_state | 3 | IDLE->SYNC->DATA->CRC->WAIT | dut.mon_tx_state |
| ... | ... | ... | ... |
```

---

## Scope Creep Detection

During test discovery, if the user requests tests that go beyond the
simulation testbench:

1. **Flag it.** "This test scenario isn't covered by the SV TB."
2. **Ask:** "Should we add it to the SV TB first (re-run sim stages), or
   proceed with hardware-only testing?"
3. If adding to TB, defer HIL until simulation passes with the new test.
