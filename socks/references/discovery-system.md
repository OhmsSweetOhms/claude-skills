# SOCKS Discovery Phase -- System Scope

For system scope designs (SoC integration of Xilinx IP + optional custom blocks).
Module and block scope discovery is in `references/discovery.md`.

---

## How Discovery Works (System Scope)

1. Claude asks the **core questions** below
2. Claude analyzes answers and asks **generative follow-ups**
3. Claude synthesizes all answers into `docs/DESIGN-INTENT.md` (system scope template below)
4. User approves or iterates
5. Claude creates `socks.json` with scope, board, and sub-design info
6. Once approved, Claude runs Stage 0+ pipeline

---

## Core Questions

1. **What is the system?** Name, purpose, target board/part.
2. **What board are you targeting?** Check if board exists in `references/boards/`. If not, tell user: "Claude is not reliable at finding board documentation via web search. Please provide: (a) board hardware user guide or datasheet, (b) master XDC if available, (c) PS7 preset TCL (export from Vivado if needed)."
3. **What Xilinx IP cores does it use?** (AXI Quad SPI, AXI GPIO, AXI UART, etc.)
4. **What IP configuration parameters?** (mode, width, frequency ratio, slave count, FIFO depth, etc.)
5. **What are the system clocks?** (PS FCLK frequency, PL clocks, derived clocks)
6. **What external interfaces / pin assignments?** (board connector, bank, I/O standard, pin numbers)
7. **What are the AXI base addresses?** (auto-assign or specific)
8. **What firmware runs on the PS?** (bare-metal, RTOS, Linux? polling or interrupt-driven?)
9. **Any existing custom RTL blocks/modules to integrate?** If yes, ask if these warrant separate `/socks --design block/module` instances. Track as sub-designs.
10. **What are the success criteria?** (bitstream builds, IP addressable, pin DRC pass, firmware compiles)

---

## Generative Follow-Up Patterns

### "AXI Quad SPI"
- Ask: mode (standard/dual/quad), transaction width, SCK frequency ratio, number of slave selects, FIFO depth
- Calculate: SCK = FCLK / ratio, capture window for ILA

### "AXI GPIO"
- Ask: width, all-outputs or bidirectional, default output value
- Ask: what are the GPIO bits controlling (active-low resets, output enables, etc.)

### "Pin assignment"
- Ask: which connector (JX1/JX2), which bank, what I/O standard (LVCMOS33/LVCMOS25/LVDS)
- Verify: bank VCCO voltage matches I/O standard
- Check: master XDC has the requested pins

### "Custom RTL block"
- Ask: "Does this warrant a separate `/socks --design block` or `/socks --design module` instance?"
- Do NOT auto-enter the design loop for system scope
- Track sub-design reference in socks.json

---

## DESIGN-INTENT.md Template (System Scope)

```markdown
# Design Intent: {name}

## What Are We Building?
- **Name:** {system name}
- **Scope:** system
- **Purpose:** {one paragraph}

## Design Space Constraints
- **Clock domains:** {FCLK_CLK0 frequency, derived clocks}
- **Interfaces:** {list of Xilinx IP cores + external I/O}
- **Resource budget:** {usually N/A for IP-only systems}

## PL Block Diagram
{ASCII art: PS -> AXI Interconnect -> IP blocks -> external pins}

## IP Configuration
### {IP Core 1 Name} (e.g., AXI Quad SPI)
| Parameter | Value |
|-----------|-------|
| Mode | ... |
| Transaction width | ... |
| ... | ... |

### {IP Core 2 Name} (e.g., AXI GPIO)
| Parameter | Value |
|-----------|-------|
| ... | ... |

## Pin Assignment ({connector}, Bank {N}, {I/O standard})
| Signal | Connector Pin | FPGA Ball | Description |
|--------|--------------|-----------|-------------|
| ... | ... | ... | ... |

## Memory Map
| Block | Base Address | Range |
|-------|-------------|-------|
| ... | Auto-assigned or specific | ... |

## C Driver Functions
| Function | Description |
|----------|-------------|
| ... | ... |

## Sub-Designs
{List of custom RTL block/module designs integrated into this system, or "None -- IP-only system"}
| Name | Scope | Path | Status |
|------|-------|------|--------|
| ... | block/module | ../path/ | designed / planned |

## Success Criteria
- [ ] Vivado project builds and generates bitstream
- [ ] All IP blocks addressable from PS
- [ ] Pin constraints pass DRC
- [ ] Firmware compiles against standalone BSP

## Alternatives Rejected
- {option} -- rejected because {reason}

## Open Questions
- {questions to resolve during Stage 1}
```

---

## socks.json Creation

After discovery approval, Claude creates `socks.json` with:
- `name` from DESIGN-INTENT.md
- `scope: "system"`
- `board.part` from discovery answers (e.g., "xc7z020clg400-1")
- `board.preset` if a matching board exists in `references/boards/`
- `sub_designs: []` (populated later if custom blocks added)

This happens before Stage 0 runs. All scopes (module, block, system) create socks.json during discovery.

---

## Scope Creep Detection

For system scope: if user wants custom RTL, ask "Does this warrant a separate `/socks --design block` or `/socks --design module` instance?" Do NOT auto-enter the design loop. Track sub-design as a reference in socks.json.
