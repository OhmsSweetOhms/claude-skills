# Reusing Vendored IP in a SOCKS Module (ADI / 3rd-party)

How to consume an ADI or other 3rd-party RTL cell (a DDS, FIR, CORDIC, mux,
PRN gen) inside a hand-authored SOCKS module — **module-level** reuse.

> **Altitude.** This is the *module* pattern: a vendored cell inside one SOCKS
> module. For *system*-level ADI project/profile vendoring (the block design,
> JESD profiles, `adi_make`, HDL/no-OS patches), see
> `adi-vendoring-profiles.md`. For module directory layout, see
> `structure-module.md`.

## Core rule: reuse, don't author; wrap, don't fork

- **Don't reimplement vendor IP.** Instantiate it. A hand-rolled DDS/FIR is a
  liability you now own and must verify.
- **Keep the vendor source pristine.** Never silently edit a vendored file in
  the tree. Put *your* logic in a hand-authored **wrapper module** that
  instantiates the unmodified cell.

## Three cases — pick by how you consume the IP

| Case | What | Durable home | Provenance |
|------|------|--------------|------------|
| **1. Used as-is** | A static vendored cell already in the tree (e.g. `ADI/library/common/ad_dds.v`), instantiated by reference | none of its own — it's just a dep | list in the module's `socks.json` deps |
| **2. Imported / generated** | IP brought in from another branch, or generated from a vendor tool (Xilinx FIR Compiler COE + a gen TCL) | `platforms/<...>/hdl/<name>/` (the gen recipe + outputs) | **`source-manifest.json`** (see below) |
| **3. Modified vendor RTL** | You genuinely must edit vendor source | a **patch** against the pinned base, never an edited copy | patch file + `source-manifest.json`; see `adi-vendoring-profiles.md` §"Patch Application Rule" |

Most module work is **case 1**. Cases 2 and 3 require recorded provenance
because you can't regenerate them from a clean vendor checkout.

## The wrapper module (case 1 — the common one)

Build a SOCKS module that instantiates the vendored cells plus your glue:

- **Collapse BD-glue into RTL.** When you lift a subgraph out of a block design,
  most of its cells are glue that exist only because a BD canvas can't express
  inline logic: constants (`xlconstant`) → generics/constants; bit-slices
  (`xlslice`) → `signal(i)` indexing; logic gates (`util_vector_logic`) →
  `not`/`and`. These vanish into a few lines of RTL.
- **Keep functional vendor cells as instances** (a DDS, a mux) — especially when
  you need **bit-exact equivalence** to a BD-inline reference build. Instantiate
  them in a `generate` loop for repeated lanes.
- **Single source.** The BD instantiates **one** reference to your module; the
  same RTL file serves both the SOCKS xsim gate and the BD deployment. Never
  fork a BD-inline copy and a module copy — they drift.

## Mixed-language: a VHDL (or SV) wrapper around Verilog vendor IP

Vendored ADI cells are Verilog; SOCKS modules are conventionally VHDL. Mixed
language **works** in both Vivado synth and Xsim.

- **VHDL wrapping Verilog:** declare a `component` matching the Verilog module's
  name + ports (Verilog has no VHDL entity to bind directly), with a `generic`
  list for its parameters, then instantiate. Pass params via `generic map`;
  map ports `std_logic`/`std_logic_vector` ↔ Verilog `wire [N:0]`.
- **The #1 bug — case sensitivity.** Verilog is case-sensitive; VHDL is not.
  Copy the Verilog **module name and every port name verbatim** into the
  component declaration. A casing mismatch surfaces as an "unbound component" at
  elaboration.
- **SV wrapping Verilog** is frictionless (native instantiation, direct param
  passing). If a module is *nothing but* a Verilog-IP aggregator and the
  mixed-language binding fights back, an SV core is the documented fallback —
  but try VHDL first for house consistency.

## `socks.json` dependencies

List the vendored cell **and its full dependency chain** as vendored deps so the
SOCKS sim filelist elaborates. Example — `ad_dds` pulls in `ad_dds_1`,
`ad_dds_2`, `ad_dds_sine`, `ad_dds_sine_cordic`, `ad_dds_cordic_pipe`,
`ad_addsub`. Miss one and xsim fails to elaborate the wrapper.

## Durable homes

| Artifact | Home |
|----------|------|
| Wrapper RTL | `socks/modules/<name>/src/<name>.vhd` |
| **Module unit test** (SV tb + Python golden) | `socks/modules/<name>/tb/` — runs under the SOCKS Xsim flow |
| System integration test (bench harness) | profile-side (e.g. `platforms/<...>/`) — *not* in the module `tb/` |
| Vendored cells | stay in `ADI/library/common/` (or the vendor tree) — **pristine, untouched** |

Keep the two test altitudes separate: the **module unit test** (does the wrapper
produce the right output for given inputs — deterministic, `max_abs_lsb=0`
against the golden) lives in the module's `tb/`; the **system integration test**
(does the IP behave through synth → bench) lives profile-side. Conflating them
puts a bench harness in a module's `tb/` or a unit test out under `platforms/`.

## Provenance: `source-manifest.json` (cases 2 & 3)

Pin where imported/generated IP came from so a future agent can re-derive or
audit it. Shape (worked example: the D5 FIR decimator):

```json
{
  "name": "<ip-name>",
  "decision": "vendor_ip_owned",            // or vendor_ip_patched
  "imported_files": [
    {
      "path": "platforms/<...>/hdl/<name>/<file>",
      "sha256": "<hash>",
      "source": { "branch": "<branch>", "commit": "<sha>", "git_object": "<path-on-that-branch>" },
      "local_modification": "<what you changed and why, or omit if byte-identical>",
      "byte_identical_sources": [ { "branch": "...", "commit": "...", "git_object": "...", "sha256": "..." } ]
    }
  ]
}
```

For **case 3** (modified vendor RTL) the modification is a *patch* against the
pinned base — the manifest records the base SHA and the patch, never a silently
forked copy.

## Worked examples

- **D5 `fir_decint_5` (case 2):** a Xilinx FIR Compiler core generated from a
  `gen_fir_ip.tcl` + `.coe`, homed under `platforms/<...>/hdl/fir_decint_5/`
  with a `source-manifest.json` pinning the COE's origin branch/commit/sha256
  and the tcl's `local_modification` (parameterized clock).
- **`gps_tone_inject` (case 1):** a SOCKS module wrapping 2× `ad_dds` + 8×
  `ad_bus_mux` (vendored, pristine) plus inlined glue; the BD instantiates one
  module reference; the SV tb + Python golden live in the module `tb/`. `ad_dds`
  / `ad_bus_mux` stay in `ADI/library/common/`, listed as vendored deps.

## See also

- `adi-vendoring-profiles.md` — system/profile-level ADI vendoring, `adi_make`,
  HDL/no-OS patch application.
- `structure-module.md` — SOCKS module directory layout (`src/`, `tb/`, `sw/`).
