# SOCKS Skill Evaluation Guide

Read this reference when evaluating or modifying the `/socks` skill
(System-On-a-Chip Kit for Synthesis).

---

## Eval Workspace

Previous eval results live in `~/.claude/skills/socks-workspace/`.
Each iteration directory contains eval runs, benchmark.json, and an HTML
viewer. The latest comprehensive findings are in `iteration-5/findings.md`.

---

## Entry Points to Test

The socks skill has 6 entry points. All must be tested for full coverage:

| Entry Point | What It Does | Typical Eval Prompt |
|---|---|---|
| `--design` | Full design from discovery through Stage 9 | "Build USART_AXI block wrapping usart_v1 in AXI-Lite" |
| `--test` | Add/modify tests, run sim stages 4,5,7,8,9 | "Add back-to-back TX test to USART_AXI" |
| `--architecture` | Architecture change + full pipeline | "Add TX FIFO to USART_AXI" |
| `--bughunt` | Find and fix a bug, verify with sim+synth | "Find the bug in usart_v1 (overrun after first byte)" |
| `--migrate` | Convert flat/legacy project to SOCKS layout | "Migrate this flat UART project to SOCKS" |
| `--hil` | Hardware-in-the-loop on FPGA board | "Run HIL on MicroZed with USART_AXI" |

### Discovery Phase Limitation

Subagent evals **cannot test the interactive discovery conversation**.
Discovery is conversational (Claude asks questions, user answers, Claude
asks follow-ups). Subagents receive pre-baked Q&A answers and skip
straight to synthesizing DESIGN-INTENT.md. This means evals verify
pipeline execution quality but not discovery question quality.

---

## Pre-Answered Discovery Template

For `--design block` evals, use this Q&A format in the subagent prompt:

```
**Design Discovery (Block Scope):**
Q1 (What are you building?): [description + path to source module]
Q2 (What sub-modules?): [path to source]
Q3 (External interfaces?): [AXI-Lite, SPI, etc.]
Q4 (Clock domains?): [single/multi, frequencies]
Q5 (Register map?): This map is good.
Q6 (What register map?): [specifics]
Q7 (Success criteria?): Timing met, loopback passes, VCD and CSV confirm.
Q8 (C driver needs?): [API functions]
Q9 (Constraints?): No
Q10 (Integration?): [monitor ports, etc.]

**Test Discovery Answers:**
Q1 (Test scenarios?): All 3
Q2 (ILA captures?): Monitor all signals needed for VCD.
Q3 (Timing constraints?): Yes, identify from VCD.
Q4 (Capture plan?): Start with that.
Q5 (Pass/fail?): Correct.
Q6 (Board overrides?): Looks good.
```

---

## Subagent Prompt Tips

Include these in subagent prompts to avoid common failures:

```
- The Write tool auto-creates parent directories
- If `ln -s` fails, use `python3 -c "import os; os.symlink(src, dst)"`
```

Do NOT include `socks.py --stages` hints unless specifically testing
prompt sensitivity -- the skill itself documents orchestrator usage.

---

## External Test Assets

The USART module at `/media/$USER/Work1/Claude/modules/USART/` is the
primary test asset. It contains:

- `src/usart_v1.vhd` -- Full-duplex UART with 8 baud rates, parity,
  16x oversampling, majority vote. Has a known `rx_data_valid` bug
  (never cleared, causes false overrun).
- `tb/` -- Python TB, SV TB, VCD verifier, signal map
- `CLAUDE.md` -- Project documentation

**Do NOT modify files in this directory.** Copy or symlink into eval
output directories.

---

## Reference File Load-Bearing Analysis

Tested in iteration 5 ablation study (Opus 4.6):

| Reference | Load-bearing? | Notes |
|---|---|---|
| `vhdl.md` | **NO** | Opus knows VHDL conventions from training. 3/4 runs pass without it. |
| `axi-lite.md` | **NO** | Opus knows AXI-Lite protocol from training. 1/1 runs pass without it. |
| `xsim.md` | **YES** | Contains SOCKS-specific execution knowledge (how to invoke xsim.py). Agent skips Xsim entirely without it. |
| `design-loop.md` | **YES** | Defines the stage sequence, re-entry logic, and orchestrator usage. |
| `python-testbench.md` | Untested | Likely non-load-bearing (commit discipline is standard). |
| `baremetal.md` | Untested | Likely non-load-bearing (C driver patterns are standard). |

**Key insight:** Domain knowledge (VHDL, AXI-Lite, C drivers) is in
Opus's training data. Tool-specific knowledge (how to invoke SOCKS
scripts) is what reference files need to provide.

---

## Model Compatibility

| Model | Viable? | Notes |
|---|---|---|
| Opus 4.6 | **YES** | 100% across all 6 entry points (51/51 assertions) |
| Sonnet 4.6 | **NO** | Cannot recover from sandbox constraints. Best: 6/12 (50%) on --design with hints. |

Sonnet failure modes:
1. Gives up on Bash permission denial instead of trying Write tool
2. Uses `cd /path && python3 script` instead of `python3 script --project-dir /path`
3. Leaves `rx_busy` port disconnected (`open`)

---

## Stochastic Pipeline-Skip Issue

In ~15% of Opus `--design` runs, the agent skips stages 3,4,7,8,9 --
authoring all files but never calling `socks.py`. This is stochastic
(not caused by any reference file omission). Adding an explicit
`socks.py --stages` hint prevents it, but the skill should handle this
without hints.

---

## Assertion Template for --design block

Standard 12-assertion set for the USART_AXI wrapper:

1. VHDL instantiates usart_v1 correctly with all ports connected
2. AXI-Lite register map includes CTRL, STATUS, TX_DATA, RX_DATA, IRQ_EN
3. Monitor ports from usart_v1 brought up to wrapper level
4. Python TB mirrors VHDL with commit discipline
5. SV TB covers init, send, receive, poll status, IRQ, soft reset
6. VCD verification independently verifies waveform data
7. CSV cross-check compares SV simulation against Python model
8. Single 100 MHz clock domain throughout
9. DESIGN-INTENT.md covers full block scope
10. Architecture diagrams (data flow + clocking) in ARCHITECTURE.md
11. Soft reset via control register bit
12. IRQ active when enabled status conditions met

---

## Hardware-in-the-Loop

A MicroZed board (Zynq-7000, xc7z020clg484-1) is connected via JTAG
and UART. The `--hil` entry point can be tested. Known limitation:
921600 baud has 13% error with integer divisor at 100 MHz -- use
115200 baud for reliable loopback tests.
