# FPGA PL bring-up

This chapter covers the FPGA fabric (PL) side of the GPS receiver
pipeline — hardware targets, decimation chain, verification framework,
HIL substrate, and the per-PL-block thread structure that drives
implementation.

The PL side translates the Python golden models in
`gps_receiver/blocks/pl_*.py` into VHDL, validates against xsim
testbenches that consume Python-model expected outputs, exercises the
RTL via a hardware streaming substrate, and graduates each block to a
SOCKS module for reuse.

## Hardware target matrix

The receiver supports three hardware platforms; PL block VHDL must
remain portable across all three.

| Platform | OSC | PL sample rate | Decimation chain | PL.DECIMATOR? | PL fabric clock |
|----------|-----|----------------|------------------|---------------|-----------------|
| **ZCU102** + AD9986-FMC (Zynq UltraScale+ MPSoC, xczu9eg) | **122.88 MHz** | 4.096 MSPS (post-DDC, via JESD204C) | AD9986 internal CDDC/FDDC: **/30 = /2 × /3 × /5** | **Not instantiated** (AD9986 owns it) | 122.88 MHz (= OSC) |
| **Zynq-7030** + FMCOMMS-5 (xc7z030, dual AD9361) | **61.44 MHz** | 61.44 MSPS (LVDS DDR via `axi_ad9361`) | PL.DECIMATOR: **/15 = /3 × /5** | **Instantiated** | MMCM-synth (typ. 100-122.88 MHz) |
| **Zedboard** + FMCOMMS-5 (xc7z020, dual AD9361) | **61.44 MHz** | 61.44 MSPS (LVDS DDR via `axi_ad9361`) | PL.DECIMATOR: **/15 = /3 × /5** | **Instantiated** | MMCM-synth (typ. 100-122.88 MHz) |

Why 122.88 MHz specifically: it is the **only** LTE-family clock rate
that gives integer-exact /30 decimation to 4.096 MSPS with a clean
prime factorization (2 × 3 × 5). 100 MHz, 120 MHz, and 125 MHz all
produce non-integer ratios, which would require fractional decimation
or fractional NCO error tolerance throughout the chain.

The 3 × 5 = 15 core is shared between the AD9361 and AD9986 paths;
AD9986 just adds the leading /2 stage. This enables a parametric-N
PL.DECIMATOR design (one VHDL IP serving multiple platforms via a
synthesis generic for the decimation factor).

## PL block list (post-2026-04-24)

| Block | Python golden model | Notes |
|-------|---------------------|-------|
| PL.B1 — Dynamic Bit Select | `gps_receiver/blocks/pl_b1_bit_select.py` | 12 → 4/2 bit power-window quantizer. Simplest PL block. |
| PL.DECIMATOR | *(no Python model — RF layer)* | Multi-tap FIR /15 (AD9361 platforms only). Vendor-IP-first via Xilinx FIR Compiler. |
| PL.B2 — PCPS FFT acquisition | `gps_receiver/blocks/pl_b2_acquisition.py` | 4096-pt FFT, non-coherent dwell accumulation. Vendor-IP-first via Xilinx FFT IP. |
| PL.B3 — Time-shared 12-channel correlator | `gps_receiver/blocks/pl_b3_correlator.py` | E/P/L correlator, sample-serial channel-serial scheduling. Most architecturally complex. |
| PL.B3a — Bit-sync histogram | `gps_receiver/blocks/pl_b3a_bit_sync_histogram.py` | Faithful port of GNSS-SDR `bit_synchronizer.cc`. Sub-block of PL.B3. |
| ~~PL.RF_IF~~ | n/a | RETIRED. ADI HDL reference designs (`ad9986_fmca/zcu102` for AD9986; FMCOMMS-5 reference for AD9361) own the RF ingest path. We tap into ADI streaming pipelines at the FIFO boundary, not the LVDS/JESD boundary. |

PL.B3 compute budget on ZCU102: 122.88 MHz fabric / 4.096 MSPS sample
= 30 fabric cycles per sample. 12 channels × 3 taps × 2 IQ = 72 MAC
slots per sample → 2.4 ops/cycle. Trivial for a pipelined MAC. Same
budget appears at the 4.096 MSPS boundary on AD9361 platforms (where
fabric is MMCM-synthesized; PL.DECIMATOR converts 61.44 MSPS → 4.096
MSPS upstream).

## Verification framework: xsim, not cocotb

The project standardizes on **Vivado xsim with SystemVerilog
testbenches** across all SOCKS modules (usart, can, i2c, spi, sdlc,
dpll_v5 — and now the GPS PL blocks). Cocotb was considered and
rejected on consistency grounds: introducing a second simulator +
Python cosim runtime would split the toolchain across the project.

The replacement pattern preserves Python-golden-model verification
without cocotb:

1. Python golden model runs offline, generates expected-output files
   (per-sample register values, dump arrays, decision events, etc.).
2. xsim SV testbench reads the expected-output files, drives the RTL
   stimulus, asserts bit-identical RTL output against the file.
3. Test pass = bit-identical match across the full stimulus length.

Same correctness guarantee as cocotb cosim; one simulator; standard
file-I/O instead of Python-RTL bridge.

## HIL is the system

The architectural pattern for FPGA bring-up: **the streaming HIL
substrate IS the system in which PL blocks get tested**. Substrate
lands first; PL blocks plug in second.

The substrate (per `fpga/20260424-zcu102-streaming-system` thread):

```
PC (Python iq_gen client)
  ↓ TCP port 5001 (IQ ingress, 32-byte header + payload)
ZCU102 R5_0 (lwIP + AXI DMA + regmap drivers)
  ↓ AXI-Lite control register / AXI-Stream data
PL streaming subsystem (axi_dma + axis_fifo + streaming_ctrl_0 IP @ 0xA000_0000)
  ↓ AXI-Stream
PL.B1 → PL.DECIMATOR (AD9361 platforms only) → PL.B2 / PL.B3 / PL.B3a
  ↓ dump output FIFO
PL → R5 → PC (TCP port 5002 telemetry)
```

PL blocks integrate into this substrate at the AXI-Stream + AXI-Lite
boundaries. The substrate's first-test pattern (sawtooth(1024) → CRC32
= `0x255203CF` via JTAG-to-AXI, NO PL block involvement) validates the
transport before any PL block exercises it.

The SOCKS skill gains a new HIL streaming mode (per
`socks/20260424-hil-streaming-mode` thread) that runs after JTAG
connect + bitstream/ELF push, opens TCP control + data, runs the
test, tears down. PL blocks invoke this mode via their `socks/hil.json`
config.

## Per-PL-block thread structure

Each PL block has its own thread with a 4-plan progression:

| Plan | Scope | Gates on |
|------|-------|----------|
| **plan-01 — Spec + architecture** | I/O JSON, AXI register map JSON, RTL architecture sketch, dual-target compatibility annotations, resource estimate | nothing — actionable now |
| **plan-02 — VHDL + xsim TB** | `<core>_axi` AXI-Lite wrapper, RTL implementation, SV TB consuming Python-model expected-output files, synthesis audit on both 7-series and UltraScale+ | own plan-01 + streaming substrate plan-01 closed |
| **plan-03 — HIL test** | Drive the block via the streaming substrate (PC → R5 → DMA → block → output → JTAG-AXI verification matches Python expectation) | streaming substrate plan-07 (substrate alive) |
| **plan-04 — SOCKS module graduation** | Module lands at `<workspace-root>/socks/modules/<block-name>/`, peer to `socks/modules/usart` etc. | own plan-03 |

Cross-block coordination (AXI address allocation, doc errata, dual-
target constraint enforcement) lives in the coordinator thread
`fpga/20260424-zynq-pl-bringup`. Per-block threads consume the
coordinator's outputs but own their own VHDL + tests + module.

## SOCKS conventions

PL blocks follow the existing 6-module SOCKS pattern:

- **AXI-Lite wrapper entity name:** `<core>_axi` (e.g., `pl_b1_axi`).
- **Standard register layout:** status @ 0x00, control @ 0x04, version
  @ 0x08, block-specific from 0x10.
- **Module structure:** `socks.json` metadata, `src/` VHDL, `sw/` C
  drivers, `tb/` Python model + SV testbench, `constraints/` XDC,
  `build/` synthesis artefacts.
- **AXI base:** `0x43C00000` for SOCKS modules (existing convention).
  Streaming subsystem regmap at `0xA000_0000` is separate (its own
  AXI base; PL blocks under it via the streaming control plane).

## Threads infrastructure pointer

The PL-side work spans 8+ threads under `gps_receiver/threads/`:

- `socks/20260424-hil-streaming-mode` — new HIL mode in SOCKS skill
- `fpga/20260424-zcu102-streaming-system` — substrate ingestion + graduation
- `fpga/20260424-zynq-pl-bringup` — cross-block coordinator
- 5 × `fpga/20260424-pl-*` — per-block specs

For overall sequencing across all 16 active project threads, see
`gps_receiver/threads/tiered-execution-flow.md` (the PL-block work
spans Tiers 2-5; substrate is Tier 3; per-block VHDL gates on Tier 3
landing).

## When to consult this chapter

- Touching any file under `gps_receiver/blocks/pl_*.py` with intent to
  port to VHDL.
- Authoring `socks/modules/<pl_block>/` content.
- Discussing FPGA hardware targets (ZCU102 vs Zynq-7030 vs Zedboard).
- Resolving decimation chain math (the /15 vs /30 prime-factor
  cascade story).
- Making cross-block AXI register allocation decisions.
- Adding a HIL test to any PL block.
- Asking why we use xsim instead of cocotb.

## When NOT to consult this chapter

- PS-side firmware port work — see `references/gps-tracking.md`,
  `references/gps-pvt.md`, `references/gps-nav-decode.md`.
- Pure Python golden-model debugging — see
  `references/pseudorange-anchoring.md` for the three-way pattern;
  the Python models themselves are at `gps_receiver/blocks/`.
- Scenario engine / IQ generator work — those are upstream of the
  PL chain and have their own threads in `scenario_engine/` and
  `gps_iq_gen/` namespaces.
