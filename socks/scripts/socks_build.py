#!/usr/bin/env python3
"""
socks_build.py -- the `socks build <recipe>` driver (unified staged recipes).

One command, one recipe format, no code-path picking: the recipe IS the
build. A recipe is a profile's unified platforms/profiles/<p>/build-recipe.json
(schema_version 3, staged shape), schema-validated at load. Selection is
one-pointer (operator ruling 2026-07-25): an explicit recipe argument
ALWAYS wins; with no argument, socks.json::build.recipe in --project-dir
is the per-project default. There is no active_profile machinery and no
mismatch to police -- a path cannot disagree with itself.

Modes:
  --plan      (default) Emit the exact commands + tree state per stage.
              No Vivado license needed -- the Codex handback form.
  --execute   Run on a licensed host (HDL/no-OS wraps the proven
              apply_recipe + Stage-14 engine; Linux is a defined-interface
              stub until plan-03 ports the cold_rebuild stages + gates).
  --describe  Human-readable "how is this profile built": identity, pins,
              toolchain, operating point + bands, stages, verification --
              including a live mxfe_rate_math re-derivation of every NCO word.

Stages: --stage hdl|linux|all (default all).

Ledger (dashboard Phase A). Every --execute run appends a schema-validated
build-ledger.jsonl beside its artifacts -- run identity, per-patch and
per-Vivado-command steps, live log milestones, gate verdicts, artifact
hashes, and the baseline-comparability verdict. Render it with
socks_build_dashboard.py, during the run or long after. The emitter is a
THIN OBSERVER: it only reads what the build already produces, so
--no-ledger yields a byte-identical build. With --run-label it also writes
the bundle manifest systems/builds/<label>/build-output.json.

Distinct from build.py (single-module clean-and-rebuild pipeline).
Origin thread: cross-cutting/20260703-socks-canonical-build-driver.
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import threading
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "hil"))

from adi_profile_apply import load_recipe, apply_recipe, _repo_root  # noqa: E402
from mxfe_rate_math import verify_operating_point  # noqa: E402


# ---------------------------------------------------------------- helpers

def resolve(args):
    """Resolve the recipe (explicit path wins; else project default)."""
    project_dir = os.path.abspath(args.project_dir) if args.project_dir else os.getcwd()
    recipe_abs, recipe = load_recipe(project_dir, recipe_path=args.recipe)
    return project_dir, recipe_abs, recipe


def stage_selected(args, stage):
    return args.stage in ("all", stage)


# ------------------------------------------------------------------- plan

def plan_hdl(recipe, recipe_abs, project_dir):
    stage = recipe["stages"]["hdl_no_os"]
    pin = recipe["upstream_pin"]
    print(f"# stage hdl_no_os : materialize -> patch -> make")
    print(f"# upstream pin: hdl {pin['hdl_repo']} @ {pin['hdl_sha']}")
    print(f"#               no-OS {pin['no_os_repo']} @ {pin['no_os_sha']}")
    for kind in ("hdl", "no_os"):
        for e in stage["patches"][kind]:
            print(f"#    {kind} patch: {e['file']} -> {e['applies_to']}")
    print(f"python3 {os.path.join(SCRIPT_DIR, 'hil', 'adi_profile_apply.py')} "
          f"--project-dir {project_dir} --recipe {recipe_abs}")
    print(f"# build (Stage-14 contract; version pin derived from the toolchain slot)")
    print(f"export REQUIRED_VIVADO_VERSION={recipe['toolchain']['vivado']}")
    for k, v in sorted(stage["build_env"].items()):
        print(f"export {k}={v}")
    print(f'source "<Vivado-{recipe["toolchain"]["vivado"]} settings64.sh>" && '
          f'make -C "<adi_root>/{stage["hdl_project"]}"')


def plan_linux(recipe):
    lx = recipe["stages"]["linux"]
    print(f"# stage linux : kernel -> dt -> boot assembly")
    k = lx["kernel"]
    print(f"# kernel {k['release']} from {k['source']['repo']} @ {k['source']['commit']}")
    for c in k["commands"]:
        print(c)
    print(f"# dt: {lx['dt']['source']} -> {lx['dt']['dtb']}")
    if "pl" in lx:
        print(f"# pl: from stage hdl_no_os (internal edge)")
    else:
        print(f"# pl_source: pinned external derivation -- see recipe stages.linux.pl_source")
    ba = lx["boot_assembly"]
    print(f"# boot assembly (verbatim; stage_d placeholder substitution contract):")
    print(ba["command"])
    print(f"# output: {ba['output']}")


# --------------------------------------------------------------- describe

def describe(recipe, recipe_abs):
    op = recipe["operating_point"]
    tc = recipe["toolchain"]
    pin = recipe["upstream_pin"]
    st = recipe["stages"]
    print(f"{recipe['name']}  [{recipe['board']} / {recipe['device']}]")
    print(f"  {recipe['description']}")
    print(f"  recipe:    {recipe_abs}")
    print(f"  toolchain: vivado {tc['vivado']} · vitis {tc['vitis']} · "
          f"bootgen {tc['bootgen']} · xsct {tc['xsct']}")
    print(f"  pins:      hdl @ {pin['hdl_sha'][:12]}  no-OS @ {pin['no_os_sha'][:12]}")
    print(f"  jesd:      {op['jesd_variant']} {op['encoding']}")
    rx, tx = op.get("rx", {}), op.get("tx", {})
    if "adc_frequency_hz" in rx:
        print(f"  rx:        ADC {rx['adc_frequency_hz']/1e9:.5f} GHz, "
              f"decim {rx.get('main_decimation','?')}x{rx.get('channel_decimation','?')}, "
              f"{len(rx.get('bands',[]))} bands")
        for b in rx.get("bands", []):
            print(f"             {b['name']:16s} {b['center_hz']/1e6:10.3f} MHz  "
                  f"ftw_48 {b['ftw_48']}")
    if "dac_frequency_hz" in tx:
        print(f"  tx:        DAC {tx['dac_frequency_hz']/1e9:.5f} GHz, "
              f"main NCO {tx.get('main_nco_shift_hz',0)/1e6:.3f} MHz")
    print(f"  stage hdl_no_os: project {st['hdl_no_os']['hdl_project']}, "
          f"{len(st['hdl_no_os']['patches']['hdl'])} hdl + "
          f"{len(st['hdl_no_os']['patches']['no_os'])} no-os patches, "
          f"env {sorted(st['hdl_no_os']['build_env'])}")
    k = st["linux"]["kernel"]
    print(f"  stage linux:     kernel {k['release']}, "
          f"{len(k['required_patches'])} kernel patches, "
          f"boot -> {st['linux']['boot_assembly']['output']}")
    print(f"  verification:    {len(recipe['verification']['uart_pass_markers'])} UART pass markers")
    findings = verify_operating_point(op)
    if findings:
        for x in findings:
            print(f"  RATE-MATH FINDING: {x}")
        return 1
    print(f"  rate math: ALL NCO words re-derive bit-exact")
    return 0


# ----------------------------------------------------------------- ledger
# Dashboard spec sections 2-4. The ledger is the product; the dashboard is a
# regenerable view of it. Every method here is write-only and derives from
# data the build already produced -- nothing below can change a build result.

ARTIFACT_KINDS = [
    (lambda n: n == "BOOT.BIN", "boot-image"),
    (lambda n: n == "Image", "kernel-image"),
    (lambda n: n.endswith(".bit"), "fpga-bitstream"),
    (lambda n: n.endswith(".xsa"), "fpga-xsa"),
    (lambda n: n.endswith(".dcp"), "fpga-dcp"),
    (lambda n: n.endswith(".dtb"), "device-tree-blob"),
    (lambda n: n.endswith(".elf"), "r5-firmware-elf"),
]


def artifact_kind(name):
    """Map a produced filename onto the ONE artifact vocabulary shared by
    build-manifest / build-ledger / build-output. None = not classifiable,
    which the emitter reports rather than silently dropping."""
    for match, kind in ARTIFACT_KINDS:
        if match(name):
            return kind
    return None


def file_digests(path):
    """sha256 + FULL 32-hex md5 + size, streamed. Hashes are recorded whole,
    never truncated (the 8-hex md5 lesson)."""
    h256, hmd5, size = hashlib.sha256(), hashlib.md5(), 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h256.update(chunk)
            hmd5.update(chunk)
            size += len(chunk)
    return h256.hexdigest(), hmd5.hexdigest(), size


class NullLedger:
    """--no-ledger: every hook is a no-op, so the wiring below has no
    conditionals and the observed build is byte-identical."""
    path = None
    enabled = False

    def emit(self, event, **fields):
        return None

    def gate(self, name, verdict, **fields):
        return None

    def artifact(self, path, root, kind=None):
        sha, md5, size = file_digests(path)
        kind = artifact_kind(os.path.basename(path)) if kind is None else kind
        if kind is None:
            return None
        return {"file": os.path.basename(path), "sha256": sha, "md5": md5,
                "bytes": size, "kind": kind}

    def baseline(self, artifact, observed, recorded, source):
        return ("no_baseline" if recorded is None
                else "reproduces" if recorded == observed else "diverges")

    def elapsed(self):
        return 0

    def summary(self):
        return {"gates_passed": 0, "gates_failed": 0, "artifacts": 0,
                "baseline_reproduces": 0, "baseline_diverges": 0, "baseline_absent": 0}


class Ledger(NullLedger):
    """Append-only JSONL flight recorder, flushed per line so a dashboard
    snapshot taken mid-build sees everything up to the current instant."""
    enabled = True

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.t0 = time.time()
        self._lock = threading.Lock()
        self._tally = {"gates_passed": 0, "gates_failed": 0, "artifacts": 0,
                       "baseline_reproduces": 0, "baseline_diverges": 0,
                       "baseline_absent": 0}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        open(self.path, "w").close()          # one file per run, never appended across runs

    def emit(self, event, **fields):
        rec = {"t": datetime.now().isoformat(timespec="seconds"), "event": event}
        rec.update({k: v for k, v in fields.items() if v is not None})
        line = json.dumps(rec)
        with self._lock:
            with open(self.path, "a") as fh:
                fh.write(line + "\n")
                fh.flush()
        return rec

    def gate(self, name, verdict, **fields):
        self._tally["gates_passed" if verdict == "pass" else "gates_failed"] += 1
        return self.emit("gate", name=name, verdict=verdict, **fields)

    def artifact(self, path, root, kind=None):
        sha, md5, size = file_digests(path)
        kind = kind or artifact_kind(os.path.basename(path))
        if kind is None:
            self.emit("progress", stage="gates",
                      detail=f"unclassified artifact skipped: {os.path.basename(path)}")
            return None
        self._tally["artifacts"] += 1
        self.emit("artifact", path=os.path.relpath(path, root), sha256=sha,
                  md5=md5, bytes=size, kind=kind)
        return {"file": os.path.basename(path), "sha256": sha, "md5": md5,
                "bytes": size, "kind": kind}

    def baseline(self, artifact, observed, recorded, source):
        verdict = ("no_baseline" if recorded is None
                   else "reproduces" if recorded == observed else "diverges")
        self._tally[{"reproduces": "baseline_reproduces",
                     "diverges": "baseline_diverges",
                     "no_baseline": "baseline_absent"}[verdict]] += 1
        self.emit("baseline", artifact=artifact, observed_sha256=observed,
                  recorded_sha256=recorded, verdict=verdict, source=source)
        return verdict

    def summary(self):
        return dict(self._tally)

    def elapsed(self):
        return round(time.time() - self.t0, 1)


def make_ledger(args, project_dir):
    """Ledger emission is ON by default for --execute (dashboard spec 7)."""
    if args.no_ledger or args.mode != "execute":
        return NullLedger()
    path = args.ledger or os.path.join(project_dir, "build", "hil", "build-ledger.jsonl")
    ledger = Ledger(path)
    print(f"  ledger:   {path}")
    return ledger


def worktree_state(root):
    """HEAD sha + dirty flag of the socks checkout, read-only."""
    import subprocess
    try:
        sha = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", root, "status", "--porcelain"],
                                    capture_output=True, text=True, check=True).stdout.strip())
        return sha, dirty
    except Exception:
        return None, None


# ------------------------------------------------- Vivado live log tailing

VIVADO_COMMANDS = ("synth_design", "opt_design", "place_design",
                   "phys_opt_design", "route_design", "write_bitstream")
RE_COMMAND = re.compile(r"^Command:\s+(" + "|".join(VIVADO_COMMANDS) + r")\b")
RE_PHASE = re.compile(r"^(Phase [\d.]+ .+|Starting \w[\w ]* Task|Ending \w[\w ]* Task)\s*$")


class VivadoTail(threading.Thread):
    """Poll the ADI project's vivado.log and turn milestones into ledger
    events while the make runs. Poll (not inotify) for the same reason
    cold_rebuild.stage_b polls: the build is hours long and the log is the
    only live surface. Note stage14_adi_make.log is NOT usable here -- the
    Stage-14 runner buffers make's stdout and writes that file only on exit."""

    def __init__(self, ledger, log_path, poll_s=20.0):
        super().__init__(daemon=True)
        self.ledger, self.log_path, self.poll_s = ledger, log_path, poll_s
        self.stop_evt = threading.Event()
        self._pos = 0
        self._seen = set()
        self._steps = 0

    def _drain(self):
        if not os.path.isfile(self.log_path):
            return
        size = os.path.getsize(self.log_path)
        if size < self._pos:               # Vivado re-ran and truncated the log
            self._pos = 0
        if size == self._pos:
            return
        with open(self.log_path, errors="replace") as fh:
            fh.seek(self._pos)
            chunk = fh.read()
            self._pos = fh.tell()
        for line in chunk.splitlines():
            line = line.rstrip()
            m = RE_COMMAND.match(line)
            if m:
                self._steps += 1
                self.ledger.emit("step", stage="hdl_make", name=m.group(1),
                                 index=self._steps)
                continue
            if RE_PHASE.match(line) and line not in self._seen:
                self._seen.add(line)
                self.ledger.emit("progress", stage="hdl_make", detail=line[:200])

    def run(self):
        while not self.stop_evt.is_set():
            try:
                self._drain()
            except OSError:
                pass
            self.stop_evt.wait(self.poll_s)
        try:
            self._drain()                  # final drain after make exits
        except OSError:
            pass

    def stop(self):
        self.stop_evt.set()
        self.join(timeout=30)


# --------------------------------------------- what actually built the bits
# A declared pin only says which toolchain was SELECTED. These read the
# version back out of the artifacts themselves (and the logs that made them),
# so the run record can assert what the bitstream was actually built with.

RE_LOG_VERSION = re.compile(r"Vivado v(\d{4}\.\d+)")
RE_BIT_VERSION = re.compile(rb"Version=(\d{4}\.\d+)")
RE_XSA_GENAPP = re.compile(r'<GenAppInfo[^>]*Name="Vivado"[^>]*Version="(\d{4}\.\d+)"')


def vivado_version_from_bitstream(path):
    """Vivado writes the tool version into the .bit header's 'a' field, e.g.
    b'system_top;COMPRESS=TRUE;UserID=0XFFFFFFFF;Version=2022.2'."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return None
    m = RE_BIT_VERSION.search(head)
    return m.group(1).decode() if m else None


def vivado_version_from_xsa(path):
    """An XSA is a zip; xsa.xml carries <GenAppInfo Name="Vivado" Version=...>."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            m = RE_XSA_GENAPP.search(z.read("xsa.xml").decode(errors="replace"))
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    return m.group(1) if m else None


def vivado_versions_from_logs(project_dir):
    """Both Vivado log banner forms ('****** Vivado v2022.2' in the console
    log, '# Vivado v2022.2' in the journal) match the same regex."""
    found = {}
    for path in sorted(glob.glob(os.path.join(project_dir, "*.log"))):
        try:
            with open(path, errors="replace") as fh:
                versions = set(RE_LOG_VERSION.findall(fh.read(8192)))
        except OSError:
            continue
        if versions:
            found[path] = versions
    return found


def gate_toolchain_actual(ledger, pinned, adi_project_dir, build_dir, root):
    """Assert every version readable from the produced artifacts and their
    build logs equals the recipe's pin. Fails closed when NOTHING is
    readable -- 'we cannot tell what built this' is the condition worth
    surfacing, not one worth passing silently."""
    seen = []          # (source, version)
    for path, versions in vivado_versions_from_logs(adi_project_dir).items():
        for v in sorted(versions):
            seen.append((os.path.relpath(path, root), v))
    for dirpath, _dirs, names in os.walk(build_dir):
        for name in sorted(names):
            path = os.path.join(dirpath, name)
            v = (vivado_version_from_bitstream(path) if name.endswith(".bit")
                 else vivado_version_from_xsa(path) if name.endswith(".xsa")
                 else None)
            if v:
                seen.append((os.path.relpath(path, root), v))
    if not seen:
        ledger.gate("toolchain_actual", "fail",
                    evidence=f"no Vivado version readable from artifacts or logs "
                             f"under {os.path.relpath(build_dir, root)}; cannot verify the "
                             f"{pinned} pin")
        print(f"  toolchain actual: UNVERIFIABLE (pin {pinned})  [FAIL]")
        return False
    disagree = [f"{s}={v}" for s, v in seen if v != pinned]
    ok = not disagree
    ledger.gate("toolchain_actual", "pass" if ok else "fail",
                evidence=(f"{len(seen)} sources all read Vivado {pinned}: "
                          + ", ".join(s for s, _ in seen))[:400] if ok
                         else (f"pinned {pinned} but " + "; ".join(disagree))[:400])
    print(f"  toolchain actual: {len(seen)} sources, pin {pinned} -- "
          f"{'all agree' if ok else 'DISAGREE: ' + '; '.join(disagree)}"
          f"  [{'PASS' if ok else 'FAIL'}]")
    return ok


RE_TIMING_ROW = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(\d+)\s+(\d+)\s+"
    r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(\d+)\s+(\d+)\s+"
    r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(\d+)\s+(\d+)", re.M)


def gate_routed_timing(ledger, timing_report, root):
    """WNS/WHS >= 0 off the routed Design Timing Summary. Same report and
    same row-parse as cold_rebuild.parse_timing_summary -- one authority."""
    if not os.path.isfile(timing_report):
        ledger.gate("timing_wns", "fail",
                    evidence=f"routed timing report missing: {os.path.relpath(timing_report, root)}")
        return False
    rows = RE_TIMING_ROW.findall(open(timing_report, errors="replace").read())
    if not rows:
        ledger.gate("timing_wns", "fail",
                    evidence=f"cannot parse Design Timing Summary in "
                             f"{os.path.relpath(timing_report, root)}")
        return False
    wns, whs = float(rows[-1][0]), float(rows[-1][4])
    ok = wns >= 0 and whs >= 0
    ledger.gate("timing_wns", "pass" if ok else "fail", wns_ns=wns, whs_ns=whs,
                evidence=os.path.relpath(timing_report, root))
    print(f"  routed timing: WNS {wns:+.3f} ns  WHS {whs:+.3f} ns  "
          f"[{'PASS' if ok else 'FAIL'}]")
    return ok


# ----------------------------------------------------- baseline comparison

def baseline_index(root):
    """filename -> (recorded sha256, manifest path) over every tracked
    *.build-manifest.json. A produced artifact with no entry is a FIRST
    BUILD, not a failure (dashboard spec section 5)."""
    index = {}
    for path in sorted(glob.glob(os.path.join(root, "platforms", "**", "*.build-manifest.json"),
                                 recursive=True)):
        try:
            art = json.load(open(path))["artifact"]
            index[art["file"]] = (art["sha256"], os.path.relpath(path, root))
        except (KeyError, ValueError, OSError):
            continue
    return index


def compare_baselines(ledger, produced, root):
    """Emit one baseline event per produced artifact. Returns the comparison
    rows for the bundle manifest."""
    index = baseline_index(root)
    rows = []
    for entry in produced:
        recorded, source = index.get(entry["file"], (None, None))
        verdict = ledger.baseline(entry["file"], entry["sha256"], recorded, source)
        row = {"artifact": entry["file"], "observed_sha256": entry["sha256"],
               "verdict": verdict}
        if recorded:
            row["recorded_sha256"] = recorded
            row["source"] = source
        rows.append(row)
        print(f"  baseline {entry['file']:28s} {verdict}")
    return rows


def bundle_verdict(rows):
    kinds = {r["verdict"] for r in rows}
    if not kinds:
        return "no_baseline"
    if kinds == {"reproduces"}:
        return "reproduces"
    if kinds == {"diverges"}:
        return "diverges"
    if kinds == {"no_baseline"}:
        return "no_baseline"
    return "mixed"


# ---------------------------------------------------------------- execute

def vivado_settings_for(version):
    """Settings64.sh for the version the RECIPE pins -- not the newest one on
    the host. socks_lib.find_vivado_settings() deliberately returns the latest
    install; for a recipe-driven build that is the wrong answer whenever the
    host carries a newer Vivado than the profile was validated on."""
    from socks_lib import VIVADO_SEARCH_PATHS
    for pattern in VIVADO_SEARCH_PATHS:
        for path in sorted(glob.glob(pattern)):
            if f"/Vivado/{version}/" in path:
                return path
    raise SystemExit(f"ERROR: recipe pins Vivado {version}, which is not installed "
                     f"(searched {', '.join(VIVADO_SEARCH_PATHS)})")


def apply_build_env(recipe, ledger):
    """The recipe IS the build: its toolchain pin and build_env are applied
    here rather than left to whatever the invoking shell happened to export.
    An inherited value that DISAGREES is overridden and said out loud."""
    stage = recipe["stages"]["hdl_no_os"]
    env = {"REQUIRED_VIVADO_VERSION": recipe["toolchain"]["vivado"]}
    env.update(stage["build_env"])
    for key, value in sorted(env.items()):
        prior = os.environ.get(key)
        if prior is not None and prior != value:
            msg = f"recipe overrides inherited {key}={prior} -> {value}"
            print(f"  NOTE: {msg}")
            ledger.emit("progress", stage="hdl_make", detail=msg[:200])
        os.environ[key] = value
    ledger.gate("build_env", "pass",
                evidence=" ".join(f"{k}={v}" for k, v in sorted(env.items()))[:400])
    print(f"  build env: {' '.join(f'{k}={v}' for k, v in sorted(env.items()))}")
    return env


def execute_hdl(recipe, recipe_abs, project_dir, ledger, root):
    stage_t0 = time.time()
    ledger.emit("stage_start", stage="apply")
    result = apply_recipe(project_dir, recipe_path=recipe_abs)
    if result.get("status") != "applied":
        ledger.emit("stage_done", stage="apply", status="fail", elapsed_s=round(time.time() - stage_t0, 1),
                    detail=str(result)[:400])
        ledger.emit("run_done", status="fail", elapsed_s=ledger.elapsed() or 0,
                    summary=ledger.summary(), detail="recipe apply did not complete")
        raise SystemExit(f"ERROR: recipe apply did not complete: {result}")
    patches = result["patches"]["no_os"] + result["patches"]["hdl"]
    for i, entry in enumerate(patches, 1):
        ledger.emit("step", stage="apply", name=entry["file"], index=i,
                    total=len(patches), sha256=entry.get("sha256"))
    ledger.gate("apply_recipe", "pass",
                evidence=f"{len(patches)} patches applied from "
                         f"{result['manifest_path']}")
    ledger.emit("stage_done", stage="apply", status="ok",
                elapsed_s=round(time.time() - stage_t0, 1))

    from hil_project import run_adi_make_stage14
    with open(os.path.join(project_dir, "socks.json")) as f:
        build_cfg = json.load(f).get("build", {})
    build_dir = os.path.join(project_dir, "build", "hil")
    adi_project_dir = os.path.abspath(os.path.join(
        project_dir, build_cfg["adi_root"], build_cfg["project_dir"]))
    settings = vivado_settings_for(recipe["toolchain"]["vivado"])
    apply_build_env(recipe, ledger)
    ledger.gate("toolchain_pin", "pass", evidence=settings)
    print(f"  vivado:    {settings}")

    stage_t0 = time.time()
    ledger.emit("stage_start", stage="hdl_make")
    tail = None
    if ledger.enabled:
        tail = VivadoTail(ledger, os.path.join(adi_project_dir, "vivado.log"))
        tail.start()
    try:
        rc = run_adi_make_stage14(project_dir, build_dir, build_cfg, settings)
    finally:
        if tail:
            tail.stop()
    if rc != 0:
        ledger.emit("stage_done", stage="hdl_make", status="fail",
                    elapsed_s=round(time.time() - stage_t0, 1), detail=f"make rc={rc}")
        ledger.emit("run_done", status="fail", elapsed_s=ledger.elapsed() or 0,
                    summary=ledger.summary(), detail=f"Stage-14 make rc={rc}")
        raise SystemExit(rc)
    ledger.emit("stage_done", stage="hdl_make", status="ok",
                elapsed_s=round(time.time() - stage_t0, 1))

    ledger.emit("stage_start", stage="gates")
    gate_routed_timing(ledger, os.path.join(adi_project_dir, "timing_impl.log"), root)
    produced = emit_sha256_manifest(build_dir, ledger, root)
    gate_toolchain_actual(ledger, recipe["toolchain"]["vivado"], adi_project_dir,
                          build_dir, root)
    return produced


def execute_linux(recipe, ledger):
    ledger.emit("progress", stage="boot_assembly",
                detail="Linux --execute not implemented (plan-03 port); "
                       "interim executor platforms/tools/cold_rebuild.py")
    raise SystemExit("ERROR: Linux --execute is not implemented yet (plan-03: port the "
                     "cold_rebuild.py kernel/dt/boot stages + reproducibility gates). "
                     "Use --plan for the command contract, or platforms/tools/cold_rebuild.py.")


def emit_sha256_manifest(build_dir, ledger=None, root=None):
    """Walk the build dir, write the sha256 manifest, and stream one ledger
    artifact event per file as it is hashed."""
    ledger = ledger or NullLedger()
    exts = (".bit", ".xsa", ".dcp", ".elf")
    entries, produced = [], []
    for dirpath, _dirnames, filenames in sorted(os.walk(build_dir)):
        for name in sorted(filenames):
            if name.endswith(exts) or name == "BOOT.BIN":
                path = os.path.join(dirpath, name)
                digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
                entries.append({"file": os.path.relpath(path, build_dir),
                                "sha256": digest})
                banked = ledger.artifact(path, root or build_dir)
                if banked:
                    produced.append(banked)
    out = os.path.join(build_dir, "socks-build-artifacts.sha256.json")
    with open(out, "w") as f:
        json.dump({"artifacts": entries}, f, indent=2)
        f.write("\n")
    print(f"artifact manifest: {out} ({len(entries)} artifacts)")
    return produced


# ------------------------------------------------- build-output (bundle)

def write_build_output(root, run_label, recipe, recipe_abs, recipe_sha, ledger,
                       produced, baseline_rows, verdict, stage):
    """The bundle manifest -- systems/builds/<run-label>/build-output.json.
    Schema-governed (build-output.schema.json); the bank README is a pointer."""
    bundle = os.path.join(root, "systems", "builds", run_label)
    sha, dirty = worktree_state(root)
    if sha is None:
        # Fail closed: a bundle manifest that cannot pin the worktree it was
        # built from is not a reproducibility record. Never write a partial one.
        raise SystemExit(f"ERROR: cannot read the worktree HEAD of {root}; "
                         f"refusing to write an unpinned build-output.json")
    os.makedirs(bundle, exist_ok=True)
    ledger_rel = "build-ledger.jsonl"
    if ledger.path:
        import shutil
        shutil.copy2(ledger.path, os.path.join(bundle, ledger_rel))
    doc = {
        "schema": "build-output/1",
        "run_label": run_label,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "recipe_path": os.path.relpath(recipe_abs, root),
        "recipe_sha256": recipe_sha,
        "recipe_name": recipe["name"],
        "worktree_sha": sha,
        "worktree_dirty": bool(dirty),
        "toolchain": dict(recipe["toolchain"]),
        "ledger": ledger_rel,
        "deploy": [{k: v for k, v in e.items()} for e in produced],
        "baseline": {"verdict": bundle_verdict(baseline_rows),
                     "comparisons": baseline_rows},
        "verdict": verdict,
        "produced_by": f"socks_build.py --execute --stage {stage} "
                       f"(thread cross-cutting/20260703-socks-canonical-build-driver)",
    }
    out = os.path.join(bundle, "build-output.json")
    with open(out, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(f"bundle manifest: {os.path.relpath(out, root)}")
    return out


# ------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(
        description="Drive a build from a schema-validated unified recipe (the recipe IS the build)")
    parser.add_argument("recipe", nargs="?", default=None,
                        help="Recipe path (wins over socks.json::build.recipe)")
    parser.add_argument("--project-dir", default=None,
                        help="SOCKS project dir; source of the build.recipe default and of "
                             "adi_root/no_os_subtree for --execute")
    parser.add_argument("--stage", choices=["hdl", "linux", "all"], default="all")
    parser.add_argument("--ledger", default=None,
                        help="build-ledger.jsonl path (default <project>/build/hil/)")
    parser.add_argument("--no-ledger", action="store_true",
                        help="Disable ledger emission (the build itself is unaffected either way)")
    parser.add_argument("--run-label", default=None,
                        help="Bank the bundle manifest at systems/builds/<label>/build-output.json")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", dest="mode", action="store_const", const="plan", default="plan",
                      help="Emit exact commands + tree state (default; no license needed)")
    mode.add_argument("--execute", dest="mode", action="store_const", const="execute",
                      help="Run the build on a licensed host")
    mode.add_argument("--describe", dest="mode", action="store_const", const="describe",
                      help="Human-readable summary of how this profile is built")
    args = parser.parse_args()

    project_dir, recipe_abs, recipe = resolve(args)

    if args.mode == "describe":
        return describe(recipe, recipe_abs)

    if args.mode == "plan":
        print(f"# socks build --plan : {recipe['name']} (stage={args.stage})")
        if stage_selected(args, "hdl"):
            plan_hdl(recipe, recipe_abs, project_dir)
        if stage_selected(args, "linux"):
            plan_linux(recipe)
        return 0

    # execute
    root = _repo_root(project_dir)
    ledger = make_ledger(args, project_dir)
    recipe_sha = hashlib.sha256(open(recipe_abs, "rb").read()).hexdigest()
    wt_sha, wt_dirty = worktree_state(root)
    if wt_sha is None and (ledger.enabled or args.run_label):
        # Fail FAST, before an hours-long make: an unpinnable run cannot
        # produce a valid ledger or bundle manifest.
        raise SystemExit(f"ERROR: cannot read the worktree HEAD of {root}; "
                         f"re-run with --no-ledger to build without a run record")
    stage_plan = [s for s in ("apply", "hdl_make") if stage_selected(args, "hdl")]
    stage_plan += [s for s in ("kernel", "dt", "boot_assembly") if stage_selected(args, "linux")]
    stage_plan.append("gates")
    ledger.emit("run_start", recipe_path=os.path.relpath(recipe_abs, root),
                recipe_sha256=recipe_sha, recipe_name=recipe["name"],
                toolchain=dict(recipe["toolchain"]),
                upstream_pin=dict(recipe["upstream_pin"]),
                worktree_sha=wt_sha, worktree_dirty=wt_dirty,
                stage_plan=stage_plan, mode="execute", run_label=args.run_label)
    ledger.gate("schema_validate", "pass",
                evidence=f"{os.path.relpath(recipe_abs, root)} @ sha256 {recipe_sha}")
    rate_findings = verify_operating_point(recipe["operating_point"])
    ledger.gate("rate_math", "fail" if rate_findings else "pass",
                evidence="; ".join(rate_findings)[:400] if rate_findings
                         else "all NCO words re-derive bit-exact")

    produced = []
    if stage_selected(args, "hdl"):
        produced = execute_hdl(recipe, recipe_abs, project_dir, ledger, root)
    if stage_selected(args, "linux"):
        execute_linux(recipe, ledger)

    baseline_rows = compare_baselines(ledger, produced, root)
    summary = ledger.summary()
    verdict = "fail" if summary["gates_failed"] else "pass"
    ledger.emit("stage_done", stage="gates", status=verdict,
                elapsed_s=ledger.elapsed() or 0)
    ledger.emit("run_done", status=verdict, elapsed_s=ledger.elapsed() or 0,
                summary=summary)
    if args.run_label:
        write_build_output(root, args.run_label, recipe, recipe_abs, recipe_sha,
                           ledger, produced, baseline_rows, verdict, args.stage)
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
