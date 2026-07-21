# Vivado Block-Design Authoring & Verification

How to add logic to a Vivado block design programmatically — module-reference
cells, vendor IP, net surgery on an existing BD, clock/interface metadata, CDC
constraints, and the verification ladder that catches what simulation cannot.

> **Altitude.** This is the *BD-integration* pattern: getting verified RTL and
> IP correctly wired into a top-level block design and proving the artifact.
> For wrapping vendor RTL inside one SOCKS module, see `vendored-ip-reuse.md`.
> For ADI project/profile vendoring (patches, `upstream/` twins, adi_make), see
> `adi-vendoring-profiles.md`. For the HIL gates this feeds (ILA equivalence,
> Stage 14-19), see `hil.md`.
>
> Provenance: the native-rate-pivot TX-replay integration (plan-04 Step 2,
> 2026-07-16) burned three consecutive Stage-14 builds on rules in this file —
> each failure class was invisible to a six-gate, all-green xsim TB. The RX
> capture-leg lineage (gps_pl_control / gps_capture_s2mm) is the proven
> counter-example the conventions are drawn from.

## The core lesson

**A bit-exact simulation proves the algorithm; only the real flow proves the
artifact.** A module packet whose TB passes every directed gate can still fail
BD integration three different ways (language level, interface metadata,
missing constraints) — none of which are bugs in the RTL's *behavior*. Author
modules for the flow they will land in, and gate the artifact separately from
the algorithm.

## 1. Module-reference cells (`create_bd_cell -type module -reference`)

### VHDL-93 is a hard flow constraint, not a style choice

- Vivado **rejects VHDL-2008 as the top file of a module-reference cell**
  (`filemgmt 56-195`, "This type is not allowed as the top file in the
  reference"). Setting `FILE_TYPE {VHDL 2008}` does not help — it converts a
  synthesis failure into a cell-creation failure.
- The trap is asymmetric: **xsim compiles VHDL as 2008 by default**, so
  out-port readback (`Synth 8-10557`) and conditional signal/variable
  assignment inside processes (`Synth 8-2757`) sail through every sim gate
  and die at first synthesis.
- **The mirror idiom** for out-port readback: drive each handshake output
  from an internal signal and read the internal signal in the FSM:

  ```vhdl
  -- VHDL-93 read-back mirrors for out ports (Vivado synthesizes module-ref
  -- sources as '93, where reading an out port is illegal; xsim --2008 hid
  -- this until the first BD synth).
  signal cmd_tvalid_s : std_logic;
  ...
  cmd_tvalid_s <= '1' when issue_enable_r = '1' and ... else '0';
  m_axis_cmd_tvalid <= cmd_tvalid_s;          -- port is write-only
  ...
  if cmd_tvalid_s = '1' and m_axis_cmd_tready = '1' then  -- FSM reads mirror
  ```

- In-process conditionals: unroll `x <= '1' when c else '0';` (sequential
  form is 2008-only) into `if c then x <= '1'; else x <= '0'; end if;`.
  Concurrent when-else assignments are 93-legal — only the in-process form
  is not.

### The catch-dance: create module refs with the vendor library filtered out

With an ADI (or similar) IP repo visible in `ip_repo_paths`, Vivado's bus
interface inference can match `s_axi_*` / control pins against vendor
interface definitions instead of `xilinx.com` aximm — the AXI-Lite interface
never forms and the address hookup fails. The proven pattern:

1. `add_files -norecurse` every module source BEFORE the BD build reaches the
   cell creation (module refs resolve against project sources).
2. Save `ip_repo_paths`, filter the vendor library dir out, `update_ip_catalog`.
3. Create **all** module-reference cells inside one `catch {}` block.
4. Restore `ip_repo_paths`, `update_ip_catalog`, re-throw any error.

### Interface inference and clock association

- Canonical AXIS/AXI port naming (`s_axis_*_tdata/tvalid/tready`,
  `s_axi_aw*/w*/b*/ar*/r*`) makes Vivado infer bus interfaces on module-ref
  cells with no attributes at all. **Inference of the interface is reliable;
  inference of its metadata is not.**
- **A clockless cell's inferred interfaces default to `FREQ_HZ 100000000`.**
  BD validation then hard-errors (`BD 41-237` FREQ_HZ mismatch) against any
  endpoint that declares its real rate, plus `BD 41-967` criticals ("not
  associated to any clock"). If a cell is pure combinational but connects at
  interface level, give it an `aclk` port used by nothing but association:

  ```vhdl
  attribute X_INTERFACE_INFO : string;
  attribute X_INTERFACE_INFO of aclk : signal is
      "xilinx.com:signal:clock:1.0 aclk CLK";
  attribute X_INTERFACE_PARAMETER : string;
  attribute X_INTERFACE_PARAMETER of aclk : signal is
      "ASSOCIATED_BUSIF s_axis_a:s_axis_b:m_axis";
  ```

  Comment WHY the unused port exists, or the next reviewer deletes it.
- **Vivado 2022.2 silently drops a multi-interface `ASSOCIATED_BUSIF` RTL
  attribute on module refs** (single-interface association imports fine).
  The convention is belt-and-suspenders — RTL attribute plus authoritative
  integration-time override on every multi-interface clock pin:

  ```tcl
  set_property CONFIG.ASSOCIATED_BUSIF \
    {s_axis:m_axis_cmd:m_axis_data:s_axis_sts} [get_bd_pins $cell/dma_clk]
  set_property CONFIG.FREQ_HZ 250000000 [get_bd_pins $cell/dma_clk]
  ```

  One `set_property` pair per clock pin that hosts interfaces. Single-clock,
  single-aximm cells (a plain CSR block) need neither.

## 2. Net surgery on an existing BD

### Cutting a scalar/data pin (per-lane): assert-single-load first

```tcl
set net  [get_bd_nets -of_objects [get_bd_pins tpl/adc_data_0]]
set load [get_bd_pins -of_objects $net -filter {DIR == I}]
if {$load ne [get_bd_pins cpack/fifo_wr_data_0]} {
  error "adc_data_0 has unexpected load(s) $load"
}
disconnect_bd_net $net $load          ;# NOT ad_disconnect (deletes the pin)
# rewire: driver -> mux in, new source -> other mux in, mux out -> old load
```

The `error` guard is load-bearing: if the topology under the cut ever changes
(an ILA tap, a second consumer), the build fails loudly instead of silently
rerouting the wrong net.

### Cutting an interface connection: assert both endpoints, delete, reconnect

```tcl
set seam [get_bd_intf_nets -of_objects [get_bd_intf_pins upack/s_axis]]
set ends [get_bd_intf_pins -of_objects $seam]
# assert exactly the two expected endpoints before deleting
delete_bd_objs $seam
connect_bd_intf_net [get_bd_intf_pins offload/m_axis] [get_bd_intf_pins mux/s_axis_a]
connect_bd_intf_net [get_bd_intf_pins mux/m_axis]     [get_bd_intf_pins upack/s_axis]
```

Never reconnect an interface seam member-pin-by-member-pin: `connect_bd_net`
on an interface *member* pin is the `BD 41-1306` class that silently severs
the rest of the interface while sim, synth, and routing all stay green. If
the new cell's interfaces might not form, `error` out when
`get_bd_intf_pins -quiet` comes back empty — fail at BD build, not on the
bench.

### HP/HPC port fan-in

`ad_mem_hpX_interconnect <clk> <master>` **adds a master to an existing HP
interconnect** — a port already claimed by a stock DMA can be shared instead
of stolen. After adding masters, sweep for the automation's unconnected ACLK
pins:

```tcl
foreach p [get_bd_pins -quiet axi_hp2_interconnect/aclk*] {
  if {[llength [get_bd_nets -quiet -of_objects $p]] == 0} {
    ad_connect sys_250m_clk [get_property PATH $p]
  }
}
```

Mixed master clocks on one HP interconnect are fine (the macro inserts
converters).

## 3. CDC constraints: pair, style, and hardware gates

### The pairing rule

**A module's CDC XDC changes in the same commit as any synchronizer added to
its RTL.** The failure mode is systematic, not careless: the RTL author adds
`*_meta_r`/`*_sync_r` pairs with ASYNC_REG and green TBs, and nothing in the
sim flow ever reads the XDC — the gap surfaces builds later as unconstrained
crossings. This has now happened twice on one project (a sim-only module
packet shipping no XDC at all; a CSR extension leaving the module XDC
untouched). Register + constraint travel together.

### Style by crossing type

| Crossing | Constraint | Never |
|---|---|---|
| Gray-coded pointer/counter bus | `set_max_delay -datapath_only` + `set_bus_skew` (both), source-reg `/C` to meta-reg `/D` | `set_false_path` — it voids the physical bound the Gray decode assumes |
| 2FF scalar, toggle, quasi-static config | `set_false_path -to <meta>/D` | forgetting ASYNC_REG in RTL (both stages) |
| Multi-bit diagnostic (double-flopped, documented non-transactional) | `set_false_path -to <meta>/D` | pretending it's coherent — document it as diagnostic-only |

Every `*_meta_r`/`*_sync_r` pair carries ASYNC_REG in RTL, no exceptions —
toggles and "just diagnostics" included.

### Make the wildcards provable: exact-count + bus-skew gates

An XDC glob that matches nothing is a silent no-op. For every Gray crossing,
add a post-route gate to the build script that (1) counts the exact expected
source-`/C` and meta-`/D` pins (`get_pins -hier -filter`, error on any other
count) and (2) runs `report_bus_skew` and errors on empty/violated. Pin
counts are computable at authoring time (pointer width × instances ×
directions). This converts "the constraints file exists" into "the
constraints landed on silicon."

**Why exact counts, not just non-zero — the Gray-MSB merge.** For any Gray
code computed as `v xor shift_right(v,1)`, the MSB equals the binary
counter's MSB, so synthesis *equivalent-register merging* fuses the two flops
— and the survivor carries the **binary** register's name. The XDC wildcard
and the gate glob (both matching `*gray_r_reg*`) then silently miss that one
bit: 14/15 endpoints matched, one physically unconstrained crossing bit, on
an otherwise all-green routed design (caught by the exact-count gate on its
first run, 2026-07-16). Fix at the source: `KEEP` on the Gray source
registers blocks the merge and makes every glob deterministic — one redundant
flop per pointer is the price. A `>0` check would have passed; only the exact
count caught it.

## 4. The verification ladder (in order, cheapest first)

1. **Module TB gates** (xsim) — algorithm correctness. Necessary, never
   sufficient for integration.
2. **Static synthesis checks** (SOCKS Stage 4) on every module — including
   trivial ones. A module that has only ever seen the sim stage is exactly
   where the 93/2008 and attribute classes hide.
3. **BD build + validate_bd_design** — interface formation, FREQ_HZ,
   addressing. Design the Tcl to `error` on every assumption (single load,
   expected endpoints, interfaces formed) so this stage fails loudly.
4. **Post-route CDC gates** — exact-count + `report_bus_skew` (§3).
5. **Static interface-integrity assertion** (hil.md gate Req 0,
   `assert_intf_integrity.py` on the routed checkpoint) — every new seam gets
   allowlist rows (driver glob → sink *instance hierarchy*, never a boundary
   port name). Add rows in the same change as the seam. Write driver globs
   from the cell's **physical port names, not the BD interface member names**:
   ADI util_cpack2/upack2 use `s_axis_ready`/`s_axis_valid`/`s_axis_data`
   (no `t`), so a `*s_axis_tready*` glob no-matches and reports a false
   "severed" on a perfectly wired seam (hit 2026-07-16). Check the vendor
   `.v` port list before writing the row.
6. **Warning-class parity** — the bar is "same classes, same counts" as the
   last-known-good baseline, not zero warnings. A build that adds cells must
   have its DRC/methodology deltas traced to specific instances and
   dispositioned, never just "totals went up."
7. **IP-Boundary Handshake Equivalence Gate** (hil.md) — HW ILA cadence vs
   SV-TB VCD over ≥512 samples at every new IP boundary. Until this is
   green, the module's sim PASS is provisional at the integrated boundary.

## 5. Authoring checklist for a BD-destined module packet

Write these into any module packet (Codex or agent) whose deliverable will
land as a BD module-reference cell:

- [ ] VHDL-93-compatible source (no out-port reads, no in-process
      conditional assignments, no 2008 constructs) — state it explicitly;
      xsim will not enforce it.
- [ ] No runtime division/modulo (or other multi-period combinational
      structures) in fast clock domains. `index mod blocks` with a runtime
      divisor synthesizes to a full combinational divider — ~26 ns, WNS
      −22 ns at a 250 MHz clock (2026-07-16 routed design) — and xsim is
      timing-blind to it. Replace with a wrapping counter that resets at the
      same points and compares equality against `limit-1`; quasi-static
      divisors make the equivalence exact. State the fastest target clock in
      the packet so the author designs to its period.
- [ ] Out-port mirror signals for every handshake output the FSM reads.
- [ ] ASYNC_REG on every meta/sync pair + a `constraints/<module>_cdc.xdc`
      covering every crossing, shipped in the packet.
- [ ] X_INTERFACE attributes for clock association; an `aclk` on
      combinational cells that will connect at interface level.
- [ ] Canonical AXIS/aximm port naming for inference.
- [ ] SOCKS Stage 4 (static synthesis checks) run and green, not just
      Stage 7.
- [ ] Integration-side facts documented in DESIGN-INTENT: which clock hosts
      which interfaces, expected `set_property` overrides, CDC gate pin
      counts.
