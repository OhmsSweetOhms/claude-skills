# SOCKS Discovery Phase

> **System scope:** For system scope designs (SoC integration of Xilinx IP),
> read `references/discovery-system.md` instead of this file.

The discovery phase runs before Stage 1 Architecture on `/socks --design`.
It produces `docs/DESIGN-INTENT.md` -- the contract that gates the design loop.

Discovery is a **conversation** between Claude and the user, not a script.

---

## How Discovery Works

1. Claude asks the **core questions** below (scope-specific)
2. Claude analyzes answers and asks **generative follow-ups**
3. Claude synthesizes all answers into `docs/DESIGN-INTENT.md`
4. User approves or iterates
5. Once approved, Claude runs Stage 0+ pipeline

**Generative follow-ups** are Claude-driven clarifications triggered by user
answers. Examples:
- User says "AXI interface" -> Claude asks "AXI4-Lite or AXI4-Full? Burst support?"
- User says "timing critical" -> Claude asks "Target frequency? Setup margin budget?"
- User says "two clock domains" -> Claude asks "CDC strategy? Async FIFO or handshake?"

Ask follow-ups until the design space is sufficiently constrained. Stop when
every section of the DESIGN-INTENT template can be filled without guessing.

---

## Core Questions

### Module Scope

One or more VHDL entities forming a self-contained peripheral (e.g. a CRC
engine, SPI master with AXI-Lite wrapper, UART controller with TX/RX cores).

1. **What are you building?** Name, one-sentence purpose.
2. **What are the inputs and outputs?** Signal names, widths, directions.
3. **What clock domain(s)?** Single clock? If multiple, which signals cross?
4. **What is the target frequency?** And the FPGA part if known.
5. **What protocol or timing constraints?** Bit rates, sample rates, latency bounds.
6. **What are the success criteria?** Timing MET, test pass count, coverage target.
7. **Any existing code or constraints to integrate with?**

If the module will be used on a SoC (typical), also ask:

8. **What sub-modules do you expect?** Names and rough responsibilities (or single entity).
9. **Will it have an AXI-Lite interface?** If yes, what register map? See `references/regmap.md` for the standard layout.
10. **What does the bare-metal C driver need to do?** Init, configure, poll, IRQ?

---

## DESIGN-INTENT.md Template

Claude synthesizes discovery answers into this structure:

```markdown
# Design Intent: {name}

## What Are We Building?
- **Name:** {entity/project name}
- **Scope:** {module | system}
- **Purpose:** {one paragraph}

## Design Space Constraints
- **Clock domains:** {list with frequencies and CDC strategy}
- **Interfaces:** {list with protocol, direction, width}
- **Data widths:** {key signal widths, intermediate widths}
- **Throughput:** {rates, latencies, pipeline depth}
- **Resource budget:** {LUT/DSP/BRAM limits if any}
- **Power:** {constraints if any}

## Success Criteria
- [ ] Timing MET at {frequency} on {part}
- [ ] {N} tests passing
- [ ] Coverage >= {X}%
- [ ] Resource usage within budget
- [ ] {any other criteria}

## Alternatives Rejected
- {option A} -- rejected because {reason}
- {option B} -- rejected because {reason}

## Open Questions
- {questions for the design loop to resolve}
```

For modules with an AXI-Lite interface, add:

```markdown
## Register Map
| Address | Name | Access | Description |
|---------|------|--------|-------------|
| 0x00    | STATUS | RO/W1C | ... |
| 0x04    | CTRL   | RW     | ... |
(see references/regmap.md for standard layout)

## Sub-Module Decomposition
| Module | Responsibility | Interfaces |
|--------|---------------|------------|
| ...    | ...           | ...        |
```

---

## Scope Creep Detection

During the design loop (Stages 2-9), if work exceeds the boundaries of
DESIGN-INTENT.md:

1. **Stop and flag it.** "This change goes beyond the approved design intent."
2. **Ask the user:** "Should we update DESIGN-INTENT.md to include this, or
   defer it to a follow-up design?"
3. If updating, re-run discovery for the expanded scope and get approval
   before continuing the design loop.

Scope creep signals:
- Adding a new interface not in the intent
- Adding a clock domain not in the intent
- Exceeding the resource budget
- Changing the register map structure (not just values)
- Adding a sub-module not listed in the decomposition
