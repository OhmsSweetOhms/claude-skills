# socks build — the unified recipe driver

One command, one recipe format, no code-path picking: **the recipe IS
the build.** `socks_build.py` drives a profile's entire build chain from
its single `platforms/profiles/<profile>/build-recipe.json` (the
"unified recipe", schema_version 3). Settled by operator rulings 1–6,
2026-07-25 (gps_design thread
`cross-cutting/20260703-socks-canonical-build-driver`); this reference
is the cold-reader entry point.

## Quick start

```bash
# What is this profile and how is it built? (identity, pins, toolchain,
# band table, stage summary, live NCO re-derivation verdict)
python3 scripts/socks_build.py <recipe.json> --describe

# Exact commands + tree state, no Vivado license (Codex handback form)
python3 scripts/socks_build.py <recipe.json> --plan [--stage hdl|linux|all]

# Run it (licensed host). HDL/no-OS works today; Linux is a stub until
# the plan-03 hop ports the cold_rebuild stages + reproducibility gates.
python3 scripts/socks_build.py <recipe.json> --execute --project-dir <sysdir>

# One-pointer default: with no recipe argument, the project's
# socks.json::build.recipe is used. An explicit argument ALWAYS wins.
python3 scripts/socks_build.py --project-dir systems/<system> --describe
```

## The recipe (what the one document contains)

`build-recipe.json` owns the whole chain, validated by
`platforms/schemas/build-recipe.schema.json` at load (fail loud at parse
time, not hour 3 of a Vivado run):

| Section | Role |
|---|---|
| `toolchain` | **Canonical version slots** (vivado/vitis/bootgen/xsct). Commands never restate versions; `REQUIRED_VIVADO_VERSION` is derived from here. `build_env` is schema-forbidden from carrying it. |
| `upstream_pin` | The reproducibility anchor (40-hex no-OS + HDL SHAs). |
| `operating_point` | RF/JESD config authority: rates, decimation, band table with 48-bit FTWs. The sibling `operating-point.json` is a **derivation record** (provenance only — never copy values from it). |
| `verification` | UART pass markers (the boot acceptance contract). |
| `stages.hdl_no_os` | `hdl_project`, patch series (**explicit repo-relative paths** — no join conventions), `build_env`. Drives materialize → patch → make. |
| `stages.linux` | `kernel` (source pin, commands, release controls, required patches, reproducibility policy), `dt`, `boot_assembly` (verbatim command + inputs + post-build checks), and exactly one of `pl` (XSA from this recipe's own hdl stage — the internal edge) or `pl_source` (a distinct pinned PL derivation — do not substitute pins). |

A thin `profile.json` sits beside the recipe (identity · status ·
owning_adrs · provenance · the constant `recipe` pointer;
`platforms/schemas/profile.schema.json`). The registry
`platforms/manifest.json` catalogs profiles and holds per-OS validation
status in `combos[]`. `socks.json::build.recipe` is a per-project
bookmark, nothing more.

## Selection model (one-pointer)

There is no `adi.active_profile`, no `profile_search_path`, no
disambiguation. A recipe is selected by **path**: explicit CLI argument
wins; otherwise `socks.json::build.recipe`. A path cannot disagree with
itself — the Path-A/Path-B ambiguity class this tool exists to kill has
no representation left.

## Engine (what --execute hdl actually runs)

`scripts/hil/adi_profile_apply.py::apply_recipe(project_dir, recipe_path)`:

1. Load + schema-validate the recipe.
2. Restore pristine HDL files from `<hdl_project>/upstream/` for every
   `patches.hdl[].applies_to`.
3. Materialize no-OS: copy `<build.no_os_subtree>/upstream/` →
   `work/active`.
4. `git apply` every patch via its explicit repo-relative path.
5. Write `build/state/adi-profile-apply.json` — **historical key names
   preserved** (`active_profile`, `manifest_path` → the recipe path):
   `hil_run.py` UART-marker config, `a53_jesd_then_r5.py`, and the
   `use_active_profile_markers` HIL fixtures consume this contract.
6. Stage-14 make runs via the existing `hil_project.py` flow
   (`build.flow == "adi_make"` now requires `build.recipe`).

Invariant (proven at the cutover): after apply, the tracked vendored
tree is **diff-empty** — pristine + patch series reproduces the
committed patched state bit-exact.

## Verification stack

- Schema gates: recipe + thin profile + registry (strict,
  `additionalProperties: false`; pre-unification shapes are rejected).
- `scripts/hil/mxfe_rate_math.py` — canonical NCO/FTW math; re-derives
  every recorded CDDC/FDDC/TX-NCO word bit-exact
  (`verify_operating_point`); wired into `--describe`.
- SHA256 artifact manifest emitted after `--execute`
  (`socks-build-artifacts.sha256.json`).
- Results are pinned per-artifact by `*.build-manifest.json`
  (`build-manifest.schema.json`), whose `::recipe` points back at the
  recipe.

## Related / status

- `scripts/build.py` is the **module** clean-and-rebuild pipeline —
  unrelated to this driver.
- `platforms/tools/cold_rebuild.py` is the **interim** Linux executor;
  its kernel/boot stages and reproducibility gates port into
  `--execute --stage linux` in the plan-03 hop, after which it retires
  (gated on the first driver-validated cold rebuild).
- Vendoring/profile background: `references/adi-vendoring-profiles.md`.
