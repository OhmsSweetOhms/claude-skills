# FPGA PL bring-up

This chapter covers the FPGA fabric (PL) side of the GPS receiver
pipeline — the active hardware substrate, the rate-conversion ladder,
the verification gate stack, the HIL substrate pattern, and the
per-PL-block structure that drives implementation.

The PL side translates the Python golden models in
`gps_receiver/blocks/pl_*.py` into VHDL, gates each block **bit-exact
(`max_abs_lsb=0`) against its Python fixed-point golden**, exercises
the RTL on the ZCU102 streaming HIL substrate, and graduates each
block to a SOCKS module for reuse. Live thread/phase status is NOT
here — query `.threads/threads.json`; the durable per-block design
record is `docs/architecture/zynq-pl/<block>/` (schema authority:
`docs/architecture/README.md`).

## Hardware target matrix (ADR-007 era)

| Role | Platform | RX path | C/A boundary |
|------|----------|---------|--------------|
| **Active substrate** | ZCU102 + AD9986-FMCA, profile `2048-quad-band-jesd204b-rxm8l2-txm8l4` | 1.96608 GHz ADC clock, exact GPS L1 NCO, **RX 20.48 MSPS native** over JESD204B (mode 4 M=8/L=2); TX 81.92 MSPS (mode 9 M=8/L=4); both ports on a GTH4 CPLL static family @ 204.8 MHz refclk | 4.096 MSPS via the **/5** decimator tap; **L1C consumes the 20.48 MSPS rail as passthrough** (no decimation) |
| Divergent small-fabric target | MicroZed Zynq-7020 + AD9361 (the original concept; survives in the target matrix, not the active path) | 61.44 MSPS LVDS | /15 = /3 × /5 cascade |

Authority: **ADR-001/ADR-007** in `docs/decision-log.md` (ADR-007
supersedes the ADR-005 clean-6144 ladder). Do not trust rate figures
from memory or older docs — the whole 61.44 MSPS substrate family
(×30//15, ×6//3, ×2/passthrough) is preserved archaeology, not the
active path. The 122.88 MSPS TX sibling profile (txm8l8) is **dropped,
not deferred** — it failed hardware on RX/JTX CGS via the ADI
`util_mxfe_xcvr` global-static-parameter pitfall (see ADR-007). For
converter-clock / NCO / JESD math read
`references/ad9986-gps-nco-frequency-planning.md`.

## Rate conversion: the parametric (×N, /M) ladder

PL.INTERPOLATOR / PL.DECIMATOR are one parametric block family, not
per-rate one-offs. Active instances: **/5** (RX 20.48 → 4.096 MSPS,
filter **F_DECINT_5**: 301 taps, Kaiser β 5.653, group delay 150
input samples) and **×20** TX-side; the /3 prototype (F_DEC_3, 101
taps) is the first stage of the archaeological /15 cascade and the
20.48 MSPS single-stage tap.

- **FIR design authority:** `.threads/fpga/20260424-pl-decimator/`
  (`pl-decimator-fir-design.md` + `pl-fir-coeffs-f_decint_5.csv` /
  `pl-fir-coeffs-f_dec_3.csv`) co-designed with
  `.threads/fpga/20260507-pl-interpolator/` (shared reproducer
  `pl-fir-design.py`). **Never re-derive these coefficients** — every
  consumer (RTL FIR Compiler config, the Python decimator golden of
  ADR-023, tests) loads from this single authority.
- The /5 group delay (150 input samples = 30 output samples ≈
  2195.7 m) is a REAL receiver timing term compensated as a named
  constant in PS.B13 labeling — see `references/gps-l1c.md` §6 and
  ADR-023. One 4.096 MSPS sample = 73.19 m; hard requirement #2
  (nanoseconds matter) applies.

## PL block inventory

| Block | Python golden | Durable home | Notes |
|-------|---------------|--------------|-------|
| PL.B1 — dynamic bit select | `gps_receiver/blocks/pl_b1_bit_select.py` | `docs/architecture/zynq-pl/pl-b1-bit-select/` | 12 → 4/2-bit power-window quantizer; also owns `PreQuantizedIQSource`/cursor infrastructure the receiver driver loop uses. The worked per-block doc template. |
| PL.B2 — PCPS acquisition | `gps_receiver/blocks/pl_b2_acquisition.py` (`backend="fixed_point"` is the RTL vector authority) | `docs/architecture/zynq-pl/pl-b2-acquisition/` | 4096-pt **authored R2²SDF FFT** (ADR-PL-B2-003 — NOT the Xilinx FFT IP; the vendor-IP-first assumption was superseded). r2/r22 fixed-point schedules: one golden per RTL config — see `references/gps-acquisition.md`. |
| PL.B3 — time-shared 12-ch correlator | `gps_receiver/blocks/pl_b3_correlator.py` (`accel_backend="fixed_point"` = vector authority) | `docs/architecture/zynq-pl/pl-b3-correlator/` | E/P/L I/Q, 1 kHz dump; R5_1 is the sole dump drainer (ADR-011). |
| PL.B3a — bit-sync histogram | `gps_receiver/blocks/pl_b3a_bit_sync_histogram.py` | (sub-block of PL.B3) | GNSS-SDR `bit_synchronizer.cc` port. |
| PL.INTERPOLATOR / PL.DECIMATOR | ADR-023 adds the Python /5 decimator golden (dual-use: scenario receiver + RTL reference) | `.threads/fpga/20260424-pl-decimator/`, `20260507-pl-interpolator/` | Parametric ladder, above. |
| PL.PPS | — | `docs/architecture/zynq-pl/pl-pps/` | Timing/PPS block; see its block.json/spec.md. |
| Future L1C lanes | see `references/gps-l1c.md` | — | PL.B3_L1C is a distinct LANE_COUNT=2 instantiation (pilot TMBOC + data BOC11), NOT a parameterized PL.B3. |
| ~~PL.RF_IF~~ | n/a | — | RETIRED — ADI HDL reference designs own RF ingest; we tap ADI streaming pipelines at the FIFO boundary. |

Fixed-point backends are instantiated **directly** by tests and
vector generators, never through `GPSReceiver` (the receiver-level
sim path stays floating-point).

## Verification gate stack (in order; each is load-bearing)

1. **Bit-exact sim:** Vivado **xsim + SystemVerilog testbenches**
   consuming Python-golden expected-output files; pass =
   `max_abs_lsb=0` over the full stimulus. Cocotb was considered and
   rejected (second simulator + cosim runtime splits the toolchain);
   the file-I/O pattern preserves the same guarantee.
2. **IP-Boundary Handshake Equivalence Gate** — required whenever a
   block instantiates 3rd-party or custom IP into the BD. Bit-exact
   sim is NOT sufficient: the IP's boundary handshakes must match on
   hardware (`dbg_hub` + ILA at the boundary; HW `tvalid`/`tready`
   cadence vs the SV-TB VCD over ≥512 samples). Gate definition:
   socks skill `references/hil.md`. Rationale: the txm8l4 slow-path
   FIR ran at ÷2 throughput because of a `tready` the TB never
   modelled — found only by sim-vs-ILA compare.
3. **Static interface-integrity gate** — required post-route for any
   packet touching RTL/BD or inserting an ILA:
   `assert_intf_integrity.py --checkpoint <routed.dcp>` against the
   project's critical-interfaces allowlist. A `connect_bd_net` on an
   AXIS interface MEMBER pin (the `BD 41-1306` class — the ordinary
   way debug taps get wired) silently severs the source→sink
   interface net while synthesis, routing, and the bit-exact sim all
   still pass. Not hypothetical: it severed the txm8l4
   `data_offload`→DMAC path (0 MiB ring), found only by
   routed-netlist trace. See `docs/codex-packet-launch-contract.md`.
4. **HIL:** drive the block on the ZCU102 streaming substrate and
   match the Python expectation end-to-end.

Operational: Vivado synth/impl (and anything opening a `.dcp`) is
license-gated and must run OUTSIDE any sandboxed agent environment —
a standing rule in every synthesis packet
(`docs/codex-packet-launch-contract.md`).

## HIL is the system

The streaming HIL substrate IS the system PL blocks get tested in —
substrate lands first, blocks plug in second, and transport is
validated with a no-block pattern (e.g. sawtooth → CRC via
JTAG-to-AXI) before any block exercises it. PS-side ownership follows
**ADR-011/012**: 4-core static bare-metal AMP (A53_0 ADI no-OS RF,
A53_1 nav + PS.RX policy plane, R5_0 ethernet/TM, R5_1 the 1 kHz
tracking loop and sole PL.B3 drainer), single-writer non-cacheable
OCM channels, no OpenAMP.

Substrate + pipeline threads (query the registry for status):
`fpga/20260424-zcu102-streaming-system`,
`fpga/20260523-gps-streaming-hil-pl-pipeline`,
`fpga/20260630-txm8l4-rx-pipeline-contract`,
`fpga/20260701-txm8l4-tx-replay-contract`,
`cross-cutting/20260617-zcu102-ad9986-socks-durable`, and the SOCKS
HIL streaming mode (`socks/20260424-hil-streaming-mode`; invoked via
a block's `socks/hil.json`). The medium-term arc these serve (close
the Python ↔ FPGA ↔ real-RF validation loop; "one results contract,
two producers") is `docs/roadmap-hil-validation-loop.md`.

## Per-PL-block structure

Each PL block owns: a thread under `.threads/fpga/` (plans, findings,
handbacks), a durable home under `docs/architecture/zynq-pl/<block>/`
(`block.json`, `spec.md`, `decisions.md` with `ADR-PL-<BLOCK>-NNN`
records, generated `architecture.html` — never hand-edit it;
`tools/build_block_arch.py`), and on graduation a SOCKS module at
`socks/modules/<block-name>/`. The historical 4-plan progression
(spec → VHDL+TB → HIL → SOCKS graduation) still describes the shape,
but real threads deviate — read the thread's `handoff.md`, not this
file, for where a block actually is. Cross-block coordination
(ADR-PL-NNN, workstream ownership) lives in
`fpga/20260424-zynq-pl-bringup`, `fpga/20260610-pl-rtl-single-source`,
and `fpga/20260611-pl-workstream-ownership`; the generated topology
dashboard is `docs/architecture/index.html`
(`tools/build_thread_topology.py`).

## SOCKS conventions

PL blocks follow the established SOCKS module pattern: AXI-Lite
wrapper entity `<core>_axi`; standard register layout (status @ 0x00,
control @ 0x04, version @ 0x08, block-specific from 0x10); module
structure `socks.json` + `src/` + `sw/` + `tb/` + `constraints/` +
`build/`. Address allocation and the streaming-plane regmap are
project data — read them from the block's regmap JSON /
`shared-interfaces.json`, don't quote from memory.

## When to consult this chapter

- Porting any `gps_receiver/blocks/pl_*.py` golden to VHDL, or
  authoring `socks/modules/<pl_block>/` content.
- Rate-conversion math (the parametric ×N//M ladder, the /5 group
  delay, why 20.48 MSPS is the L1C rail).
- Choosing/verifying against a hardware target; anything JESD/NCO →
  `references/ad9986-gps-nco-frequency-planning.md`.
- Wiring a debug tap or instantiating IP into the BD (gates 2-3
  above — read BEFORE wiring, they exist because sims lied twice).
- Adding a HIL test; asking why xsim instead of cocotb.

## When NOT to consult this chapter

- PS-side firmware port work — `references/gps-tracking.md`,
  `references/gps-pvt.md`, `references/gps-nav-decode.md`.
- Pure Python golden-model debugging —
  `references/pseudorange-anchoring.md` (three-way pattern).
- L1C signal/receiver semantics — `references/gps-l1c.md`.
- Scenario engine / IQ generator work — upstream of the PL chain
  (`references/adding-a-scenario.md`).
