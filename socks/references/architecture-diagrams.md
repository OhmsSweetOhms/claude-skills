# Architecture Diagrams (Mermaid)

Stage 1 produces two Mermaid diagrams — **Data Flow** and **Clocking** — plus a **Rate Summary** table. Write them into `ARCHITECTURE.md` and render to PNG with `mmdc`. These diagrams catch hierarchy, connectivity, and clock/rate mismatches before any VHDL is written.

## Prerequisites

Mermaid CLI (`mmdc`) must be installed:
```bash
mmdc --help
```

## File Layout

```
project_name/
├── ARCHITECTURE.md              # Mermaid source (two ```mermaid blocks)
├── ARCHITECTURE_dataflow.png    # Rendered data flow diagram
└── ARCHITECTURE_clocking.png    # Rendered clocking diagram
```

## Rendering

`mmdc` numbers output files when the input contains multiple diagrams:
```bash
mmdc -i ARCHITECTURE.md -o arch.png -w 1400 -e png -b white
mv arch-1.png ARCHITECTURE_dataflow.png
mv arch-2.png ARCHITECTURE_clocking.png
```

Re-render after every edit and visually inspect the PNG before presenting to the user.

---

## Diagram 1: Data Flow

Shows the complete signal path from PS through the design and back. The goal is to verify module hierarchy, port connectivity, and signal naming before writing RTL.

### Structure

Use `graph TD` (top-down) as the main direction. Nest subgraphs to reflect the VHDL entity hierarchy:

```
PS (external, top)
  └── Top-Level Wrapper (subgraph)
        ├── Register File
        └── Frame Processing (subgraph)
              ├── TX Frame FSM
              ├── Sub-entity instances (subgraphs, direction LR for TX/RX pairs)
              ├── Clock / Baud layer (subgraph, direction LR)
              ├── Loopback / external connection (subgraph)
              └── RX Frame FSM
```

### Preamble

Always start with this init block for clean right-angle routing:
```
%%{init: {'flowchart': {'curve': 'linear', 'nodeSpacing': 40, 'rankSpacing': 50},
          'theme': 'base', 'themeVariables': {'fontSize': '14px'}}}%%
```

- `curve: 'linear'` — orthogonal (right-angle) arrows
- `nodeSpacing` / `rankSpacing` — controls density; 40/50 is a good starting point

### Node Labels

Use HTML-style labels inside `["..."]` for rich content:
```
NODE_ID["<b>Display Name</b><br/>detail line 1<br/>detail line 2"]
```

Every node should show:
- Bold name matching the VHDL process or entity instance label
- Key function (what it does)
- Data format or rate where relevant

### Edge Conventions

| Path | Style | Meaning |
|------|-------|---------|
| TX / forward | `--` solid with `-->` | Data flowing from software toward the wire |
| RX / return | `-.` dashed with `.->` | Data flowing from the wire back to software |
| Invisible rank anchor | `~~~` | Forces two nodes onto the same rank without a visible edge |

Label edges with the actual VHDL signal names. Use `<br/>` for multi-signal labels:
```
A -- "tx_data / tx_valid / tx_last" --> B
C -. "sdlc_rx_in<br/>ref_clk_in" .-> D
```

### Subgraph Hierarchy

Subgraphs represent VHDL entity boundaries and logical groupings:
- **Outer subgraph** = top-level wrapper entity (solid border)
- **Inner subgraphs** = sub-entity instances or process groups (dashed border)
- Use `direction LR` inside subgraphs to pair TX/RX counterparts side by side
- Label subgraphs with the VHDL instance name: `subgraph U_SDLC ["u_sdlc : sdlc_v1  (read-only)"]`
- Mark external/read-only modules in the label: `(read-only)`

### Mermaid Layout Pitfalls

Mermaid's dagre layout places each node at the rank of its deepest incoming edge. This causes problems when an RX return path feeds a node that logically belongs at an earlier rank (e.g. RX Frame FSM next to TX Frame FSM). Known workarounds:

1. **Accept the natural rank** — place the RX consumer (e.g. RXF) at the bottom of its containing subgraph rather than beside the TX producer. The return edge goes back up to the register file. This gives a U-shaped flow: TX down, through loopback, RX continues down, then returns up.

2. **Invisible rank anchors** — `A ~~~ B` forces co-ranking for nodes that aren't directly connected. Useful for pairing TX/RX sub-entities at the same level.

3. **`direction LR` inside subgraphs** — works reliably when both nodes in the subgraph have edges at the same rank depth. Gets overridden by the parent `graph TD` when cross-subgraph edges pull nodes to different ranks.

4. **Never fight dagre** — if a node drifts, restructure the hierarchy to make the natural rank order match the desired visual order. Prefer restructuring over adding invisible edges.

### Loopback / External Connections

For designs with loopback test connectivity, use a dedicated subgraph showing the pin mapping:
```
subgraph LOOPBACK ["Loopback Test Connection"]
    LB["<b>tx_out → sdlc_rx_in</b><br/><b>tx_clk → ref_clk_in</b><br/><i>Far end or DPI-C loopback</i>"]
end
```

This makes the test topology explicit in the architecture diagram.

### Colour Palette

Consistent colours by ownership / function make the diagram scannable at a glance:

| Category | Fill | Stroke | Use for |
|----------|------|--------|---------|
| Register file / AXI | `#dbeafe` | `#2563eb` | Registers, bus interface |
| TX processes (own code) | `#fef3c7` | `#d97706` | TX FSM, baud counter |
| RX processes (own code) | `#ede9fe` | `#7c3aed` | RX FSM |
| Read-only sub-entities | `#d1fae5` | `#059669` | Instantiated IP (sdlc_v1, etc.) |
| Clock recovery / DPLL | `#fce7f3` | `#db2777` | DPLL, NCO, PLL |
| External / PS | `#e0e7ff` | `#4f46e5` | Zynq PS, off-chip |
| Loopback / test | `#fefce8` | `#ca8a04` | Test connections (dashed stroke) |

Subgraph borders: use `stroke-dasharray: 4 4` for internal groupings, solid for the top-level wrapper.

---

## Diagram 2: Clocking

Shows how sys_clk fans out to every rate-generating process. The goal is to verify that every frequency in the design has a documented derivation and that no clock domain crossing is missed.

### Structure

```
sys_clk (PS FCLK_CLK0) at top
  └── All logic (subgraph)
        ├── AXI + Register File (sys_clk)
        ├── Frame FSMs (sys_clk)
        ├── TX Rate Generation (subgraph)
        │     baud counter → tx_clk toggle → tx_out
        └── RX Rate Recovery (subgraph)
              NCO → PI filter → sample_en
```

### Key Rules

- **sys_clk is PS FCLK_CLK0**, not an external oscillator. Always label it as such on Zynq designs.
- Show the **derivation formula** inside each node (e.g. `sys_clk / (TX_BIT_DIV + 1)` or `freq_word × sys_clk / 2³²`).
- Include a **concrete example** in italics (e.g. `100 MHz / 100 = 1 MHz tick`).
- Group closely-related processes into subgraphs (TX Rate Generation, RX Rate Recovery).
- Chain nodes within each subgraph to show the derivation order.
- Colour-code TX rate nodes differently from RX rate nodes (same palette as data flow).

---

## Rate Summary Table

After the two diagrams, include a markdown table listing every rate in the design:

```markdown
## Rate Summary

| Rate | Value | Derivation | Signals |
|------|-------|-----------|---------|
| sys_clk | 100 MHz | PS FCLK_CLK0 | All registered logic |
| TX baud tick | <range> | sys_clk / (TX_BIT_DIV + 1) | Internal to TX engine |
| ... | ... | ... | ... |
```

If the design supports configurable rates, add a second table:

```markdown
### Configurable Bit Rates

| Bit Rate | TX_BIT_DIV | freq_word | KP | KI |
|----------|-----------|-----------|-----|-----|
| 1 MHz | 99 | 0x028F5C29 | 131 | 8 |
| ... | ... | ... | ... | ... |
```

---

## Checklist

Before leaving Stage 1, verify:

- [ ] Every VHDL entity appears as a subgraph or node in the data flow diagram
- [ ] Every signal that crosses an entity boundary is labelled on an edge
- [ ] TX path uses solid arrows, RX path uses dashed arrows
- [ ] Loopback / external connection topology is shown explicitly
- [ ] Clocking diagram shows sys_clk source as PS FCLK_CLK0
- [ ] Every derived rate has a formula and a concrete numeric example
- [ ] Rate summary table accounts for all frequencies in the design
- [ ] PNGs render cleanly (no overlapping nodes, no escaped subgraphs)
- [ ] Read-only sub-entities are marked `(read-only)` in subgraph labels
