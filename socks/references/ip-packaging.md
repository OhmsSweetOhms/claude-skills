# IP Packaging Reference (Stage 21)

Stage 21 packages a SOCKS module as a Vivado IP block using the native
`ipx::` API. The packaged IP is used by xsim simulation (stage 7) and
HIL project creation (stage 14).

---

## socks.json `ip` Section

**Mandatory** for all module-scope projects. System-scope projects do not
have this section (stage 21 skips them).

```json
"ip": {
    "vendor": "socks",
    "library": "socks",
    "version": "1.0",
    "display_name": "Human-readable IP name",
    "description": "One-line description of the IP",
    "vendor_display_name": "SOCKS",
    "company_url": "",
    "taxonomy": "/SOCKS"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `vendor` | Yes | VLNV vendor field (default: `socks`) |
| `library` | Yes | VLNV library field (default: `socks`) |
| `version` | Yes | VLNV version (e.g., `1.0`) |
| `display_name` | Yes | Human-readable name shown in IP Catalog |
| `description` | Yes | One-line description |
| `vendor_display_name` | Yes | Vendor label in IP Catalog |
| `company_url` | No | Optional URL |
| `taxonomy` | Yes | IP Catalog category (default: `/SOCKS`) |

The VLNV (Vendor:Library:Name:Version) is constructed as:
`{vendor}:{library}:{entity_name}:{version}`

---

## Interface Detection Rules

The script parses the top-level VHDL entity and groups ports into bus
interfaces based on naming conventions.

### Port Type Constraint

**Top-level IP ports must be `std_logic` or `std_logic_vector` only.**
These are the only types valid for Vivado IP Integrator. `unsigned`,
`signed`, records, and other complex types are not supported at the IP
boundary. The parser will error if unsupported types are found.

### AXI-Lite Detection

Ports are grouped by prefix (e.g., `s_axi_`). A group matches AXI-Lite if
it contains all required signals:

**Required:** `awaddr`, `awvalid`, `awready`, `wdata`, `wvalid`, `wready`,
`bresp`, `bvalid`, `bready`, `araddr`, `arvalid`, `arready`, `rdata`,
`rresp`, `rvalid`, `rready`

**Optional:** `awprot`, `wstrb`, `arprot`

Direction (master/slave) is inferred from `awvalid`: if `in`, it's a slave
interface. Address range is inferred from `awaddr` width (e.g., 8-bit
address -> 256-byte range). Base address is assigned at block design level.

### AXI-Stream Detection

Ports grouped by prefix matching the AXIS signal set:

**Required:** `tdata`, `tvalid`

**Optional:** `tready`, `tlast`, `tkeep`, `tstrb`, `tid`, `tdest`, `tuser`

Direction: if `tvalid` is `out`, it's a master; if `in`, it's a slave.

### AXI-Full Detection

Same as AXI-Lite but with burst signals present (`awlen`, `awsize`,
`awburst`, `arlen`, etc.). The presence of these signals distinguishes
AXI-Full from AXI-Lite. Both use `xilinx.com:interface:aximm_rtl:1.0`.

### Clock Detection

Ports matching: `clk`, `aclk`, `*_clk`, `*_aclk` (must be `std_logic in`).

The clock interface includes:
- `ASSOCIATED_BUSIF`: colon-separated list of all AXI bus interface names
- `ASSOCIATED_RESET`: the detected reset signal name

### Reset Detection

Ports matching: `rst*`, `*_rstn`, `*_rst_n`, `*_aresetn`, `aresetn`
(must be `std_logic in`).

Polarity is inferred from the name: `n` suffix -> `ACTIVE_LOW`, otherwise
`ACTIVE_HIGH`.

### Scalar Ports

Everything not claimed by the above detectors remains as bare ports
(individual pins in IP Integrator). This includes `mon_*` monitor ports,
`irq` outputs, and any other project-specific signals.

---

## Multi-Interface DUTs

A single DUT may have multiple interfaces:

- Multiple AXI-Lite (e.g., `s_axi_cfg_*` + `s_axi_data_*`)
- Mixed AXI-Lite + AXI-Stream (e.g., `s_axi_*` + `m_axis_*`)
- AXI-Full master + AXI-Lite slave

Each detected interface group gets its own bus interface declaration with a
unique name derived from the port prefix. The block design template
parameterizes interconnect ports and address assignments for each interface.

---

## Generic Mapping

VHDL generics are mapped to IP parameters:

| VHDL Type | IP-XACT Type |
|-----------|-------------|
| `integer` | `long` |
| `natural` | `long` |
| `positive` | `long` |
| `boolean` | `bool` |
| `real` | `float` |
| `string` | `string` |

Default values from the VHDL entity are preserved.

---

## Hash-Based Caching

Stage 21 computes a SHA-256 hash of all VHDL source files plus the
`socks.json` `ip` section. This hash is stored in `build/ip/.ip_hash`.

On subsequent runs, if the hash matches and `component.xml` exists, Vivado
is skipped entirely. This saves ~30-60s per design loop iteration when
RTL hasn't changed.

The hash is recomputed when:
- Any VHDL source file changes
- The `ip` section of `socks.json` changes

---

## Output Artifacts

```
build/ip/
├── package_ip.tcl     # Generated Tcl (regenerated each run)
├── component.xml      # IP-XACT descriptor
├── xgui/              # Vivado GUI parameter files
├── package_ip.log     # Vivado log
├── package_ip.jou     # Vivado journal
└── .ip_hash           # Source hash for skip logic
```

All of `build/ip/` is gitignored, consistent with `build/sim/` and
`build/synth/` conventions. Everything is regenerated from `socks.json`
+ VHDL sources.

---

## Troubleshooting

### "socks.json is missing the mandatory 'ip' section"

Add the `ip` section to your module's `socks.json`. See the schema above.

### "Port has unsupported type"

Top-level ports must be `std_logic` or `std_logic_vector`. Convert
`unsigned`/`signed` ports to `std_logic_vector` with explicit casts inside
the architecture.

### "No entity found in file"

The parser looks for the first `entity ... is ... end entity;` block.
Ensure the top-level source file (first in `dut.sources`) contains the
entity declaration.

### Vivado ipx:: errors

Check `build/ip/package_ip.log` for detailed Vivado output. Common issues:
- Port name collisions with Vivado reserved names
- Missing source files (check `dut.sources` paths)
- VHDL syntax errors (run stage 3/4 first)

### component.xml not generated

Verify that `build/ip/package_ip.tcl` was created and that Vivado is
accessible. Check `build/ip/package_ip.log` for errors.
