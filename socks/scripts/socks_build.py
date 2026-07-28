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
  --execute   Run on a licensed host. HDL/no-OS wraps the proven
              apply_recipe + Stage-14 engine; Linux builds kernel -> dt ->
              r5 -> boot assembly, each under its reproducibility gate.
              --stage all yields the complete six-artifact deploy set.
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
import shlex
import shutil
import subprocess
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
    print(f"# stage linux : kernel -> dt -> r5 -> boot assembly")
    k = lx["kernel"]
    print(f"# kernel {k['release']} from {k['source']['repo']} @ {k['source']['commit']}")
    for c in k["commands"]:
        print(c)
    print(f"# dt: {lx['dt']['source']} -> {lx['dt']['dtb']}")
    print(f"# r5: standalone_v8_0 BSP from this run's XSA -> "
          f"r5_0_capture_rproc.elf (+ host desk tests)")
    if "pl" in lx:
        print(f"# pl: from stage hdl_no_os (internal edge)")
    else:
        print(f"# pl_source: pinned external derivation -- see recipe stages.linux.pl_source")
    ba = lx["boot_assembly"]
    print(f"# boot assembly (verbatim; stage_d placeholder substitution contract):")
    print(ba["command"])
    print(f"# output: {ba['output']}")


def plan_dt(recipe):
    """--stage dt: the Linux half's dt stage ALONE (see the --stage help)."""
    dt = recipe["stages"]["linux"]["dt"]
    print(f"# stage dt : dtc only -- kernel, r5 and boot assembly are NOT run")
    print(f"# dt: {dt['source']} -> {dt['dtb']}")
    print(f"# manifest: {dt['dtb_manifest']} (the dtb_sha gate compares against it)")
    print(f"# bundle: a SUBSET deploy set -- the device-tree-blob only")


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

    def baseline(self, artifact, observed, recorded, source, verdict):
        return verdict

    def elapsed(self):
        return 0

    def summary(self):
        return {"gates_passed": 0, "gates_failed": 0, "artifacts": 0,
                "baseline_reproduces": 0, "baseline_diverges": 0,
                "baseline_absent": 0, "baseline_load_equivalent": 0}


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
                       "baseline_absent": 0, "baseline_load_equivalent": 0}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        open(self.path, "w").close()          # one file per run, never appended across runs

    def emit(self, event, **fields):
        # Self-check the one place two vocabularies can cross: a GATE has a
        # pass/fail verdict, a STAGE or RUN has an ok/fail status. Feeding a
        # gate verdict into a stage status writes a schema-invalid record that
        # nothing notices until a harness reads it back -- so fail at the
        # emit, loudly, with the offending value in hand.
        if event in ("stage_done", "run_done") and fields.get("status") not in ("ok", "fail"):
            raise ValueError(f"{event}.status must be ok|fail (a stage is not a gate); "
                             f"got {fields.get('status')!r}")
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

    def baseline(self, artifact, observed, recorded, source, verdict):
        self._tally[{"reproduces": "baseline_reproduces",
                     "diverges": "baseline_diverges",
                     "no_baseline": "baseline_absent",
                     "load_equivalent": "baseline_load_equivalent"}[verdict]] += 1
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
    events while the make runs. Poll (not inotify) because the build is hours
    long and the log is the only live surface. Note stage14_adi_make.log is NOT usable here -- the
    Stage-14 runner buffers make's stdout and writes that file only on exit."""

    def __init__(self, ledger, log_path, poll_s=20.0):
        super().__init__(daemon=True)
        self.ledger, self.log_path, self.poll_s = ledger, log_path, poll_s
        self.stop_evt = threading.Event()
        # Start at the END of whatever is already on disk. A previous run's
        # vivado.log is still there when this one starts -- the ADI makefile
        # deletes it only after make begins -- so draining from byte 0 would
        # replay the LAST build's synth/place/route/write_bitstream milestones
        # into THIS run's ledger as if they had just happened. The truncation
        # check below then picks up the new log from its start.
        try:
            self._pos = os.path.getsize(log_path)
        except OSError:
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
    """WNS/WHS >= 0 off the routed Design Timing Summary -- the last
    table Vivado writes, parsed row-wise with the last row winning."""
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


# ------------------------------------------------- bounded normalization
# A Vivado bitstream and an XSA are not bit-reproducible, but they are not
# arbitrarily different either: everything that varies between two builds of
# identical source sits in a NAMED, structurally locatable field. Hashing the
# artifact with exactly those fields zeroed -- after asserting the structure
# the manifest declares -- turns "we cannot compare these" into a machine
# verdict, which is the whole point of a baseline.
#
# The pattern is the one normalized_kernel_image_sha256 already uses, and its
# rule is the important part: ASSERT THE DECLARED STRUCTURE FIRST. Zeroing a
# field found by an unanchored search would let arbitrary drift hide inside it.
#
# EVIDENCE (2026-07-25, plan-03 Step 6). The instrumented flow writes the
# bitstream twice per run, before and after debug-core insertion. Those two
# files -- same design, same placement, same routing, written four minutes
# apart -- differ in exactly THREE bytes, all inside the 'd' (time) header
# record. The raw configuration body is byte-identical. So the manifests'
# stated reason was wrong on one point: there is no per-build bitstream UUID.
# See findings-2026-07-25-plan03-linux-port.md.

BIT_HEADER_MAGIC = bytes.fromhex("0ff00ff00ff00ff000")


def parse_bitstream_header(body):
    """Return (records, payload_offset) for a Xilinx .bit.

    Structure (fixed, documented): a 2-byte length + 9-byte magic + 2-byte
    0x0001, then TLV records keyed 'a'..'d' with 2-byte big-endian lengths,
    then key 'e' with a 4-byte length introducing the raw configuration data.
    Each record's span is returned so normalization can address a field by its
    parsed position rather than by searching for its value."""
    if len(body) < 16 or struct_unpack_be16(body, 0) != 9:
        raise StageFail("not a Xilinx .bit: leading header-field length is not 9")
    if body[2:11] != BIT_HEADER_MAGIC:
        raise StageFail("not a Xilinx .bit: 9-byte header magic mismatch")
    offset = 13
    records = {}
    while offset < len(body):
        key = chr(body[offset])
        offset += 1
        if key == "e":
            length = int.from_bytes(body[offset:offset + 4], "big")
            return records, offset + 4, length
        if key not in "abcd":
            raise StageFail(f"unexpected .bit header record key {key!r} at offset {offset}")
        length = struct_unpack_be16(body, offset)
        offset += 2
        records[key] = (offset, offset + length)
        offset += length
    raise StageFail("no 'e' payload record in the .bit header")


def struct_unpack_be16(body, offset):
    return int.from_bytes(body[offset:offset + 2], "big")


def normalized_bitstream_sha256(path, policy):
    """sha256 of a .bit with the declared header records zeroed.

    Only records the policy names are zeroed, and only after the parsed record
    set matches the policy exactly -- an extra or missing header record means
    the artifact is not the shape the baseline was recorded for, which is a
    failure rather than something to normalize away."""
    with open(path, "rb") as fh:
        body = bytearray(fh.read())
    records, payload_at, payload_len = parse_bitstream_header(body)
    expected = policy["expected_header_records"]
    if sorted(records) != sorted(expected):
        raise StageFail(f"{os.path.basename(path)} header records {sorted(records)} "
                        f"!= policy {sorted(expected)}")
    if payload_at + payload_len != len(body):
        raise StageFail(f"{os.path.basename(path)} payload length {payload_len} does not "
                        f"reach end of file ({len(body) - payload_at} bytes present)")
    for key in policy["zeroed_header_records"]:
        start, end = records[key]
        body[start:end] = bytes(end - start)
    return hashlib.sha256(bytes(body)).hexdigest()


RE_XSA_TIMESTAMP = re.compile(rb'(TimeStamp|TIMESTAMP)="[^"]*"')
RE_XSA_JSON_TIMESTAMP = re.compile(rb'("generatedTimestamp"\s*:\s*)"[^"]*"')


def normalized_xsa_sha256(path, policy):
    """sha256 of a canonical rendering of an XSA with declared fields zeroed.

    An XSA is a zip, so three classes of noise have to go: the entry mtimes
    (dropped by hashing name+content rather than the container), the generator
    timestamps carried inside xsa.xml / xsa.json / every *.hwh, and the
    embedded bitstream's own header records. Every count the policy declares
    is asserted before anything is rewritten."""
    import zipfile
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise StageFail(f"{os.path.basename(path)} is not a readable XSA "
                        f"(an XSA is a zip): {exc}")
    with archive:
        names = sorted(archive.namelist())
        if len(names) != policy["expected_entries"]:
            raise StageFail(f"{os.path.basename(path)} has {len(names)} zip entries; "
                            f"policy declares {policy['expected_entries']}")
        digest = hashlib.sha256()
        stamps = {"xml": 0, "json": 0, "hwh": 0}
        for name in names:
            data = archive.read(name)
            if name.endswith(".bit"):
                if name != policy["embedded_bitstream"]:
                    raise StageFail(f"unexpected embedded bitstream {name}; policy "
                                    f"declares {policy['embedded_bitstream']}")
                tmp = os.path.join(os.path.dirname(os.path.abspath(path)),
                                   f".{os.path.basename(name)}.normalize")
                try:
                    with open(tmp, "wb") as fh:
                        fh.write(data)
                    data = normalized_bitstream_sha256(tmp, policy["bitstream"]).encode()
                finally:
                    if os.path.exists(tmp):
                        os.remove(tmp)
            elif name.endswith(".hwh"):
                data, n = RE_XSA_TIMESTAMP.subn(rb'\1=""', data)
                stamps["hwh"] += n
            elif name == "xsa.xml":
                data, n = RE_XSA_TIMESTAMP.subn(rb'\1=""', data)
                stamps["xml"] += n
            elif name == "xsa.json":
                data, n = RE_XSA_JSON_TIMESTAMP.subn(rb'\1""', data)
                stamps["json"] += n
            digest.update(name.encode() + b"\0")
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
    declared = {"xml": policy["expected_xsa_xml_timestamps"],
                "json": policy["expected_xsa_json_timestamps"],
                "hwh": policy["expected_hwh_timestamps"]}
    if stamps != declared:
        raise StageFail(f"{os.path.basename(path)} timestamp fields {stamps} != policy "
                        f"{declared}")
    return digest.hexdigest()


def normalizers():
    """Resolved at call time: the kernel normalizer is defined with the Linux
    stages it belongs to, further down the file."""
    return {
        "kernel-image-load-equivalence/1": normalized_kernel_image_sha256,
        "fpga-bitstream-load-equivalence/1": normalized_bitstream_sha256,
        "fpga-xsa-load-equivalence/1": normalized_xsa_sha256,
    }


def normalized_sha256(path, policy):
    """Dispatch on the policy's declared normalization schema. An unknown
    schema is a failure, never a silent pass -- the manifest is claiming a
    verification this code cannot perform."""
    NORMALIZERS = normalizers()
    schema = policy.get("normalization_schema")
    fn = NORMALIZERS.get(schema)
    if fn is None:
        raise StageFail(f"no normalizer for normalization_schema {schema!r} "
                        f"(known: {', '.join(sorted(NORMALIZERS))})")
    return fn(path, policy)


# ----------------------------------------------------- baseline comparison

def baseline_index(root, prefer_dir=None):
    """artifact filename -> {sha256, source, mode} over every tracked
    *.build-manifest.json. A produced artifact with no entry is a FIRST
    BUILD, not a failure (dashboard spec section 5).

    Artifact names are NOT globally unique -- two profiles may each home a
    `system_top.bit`. On a collision, a manifest under prefer_dir (the
    building profile's own home) wins; otherwise the entry is marked
    ambiguous and the caller refuses to guess."""
    by_name = {}
    for path in sorted(glob.glob(os.path.join(root, "platforms", "**", "*.build-manifest.json"),
                                 recursive=True)):
        try:
            doc = json.load(open(path))
            art = doc["artifact"]
            policy = ((doc.get("build") or {}).get("reproducibility") or {})
            by_name.setdefault(art["file"], []).append(
                {"sha256": art["sha256"], "source": os.path.relpath(path, root),
                 "mode": policy.get("mode", "bit-reproducible"), "policy": policy})
        except (KeyError, ValueError, OSError):
            continue
    index = {}
    for name, entries in by_name.items():
        if len(entries) == 1:
            index[name] = entries[0]
            continue
        preferred = [e for e in entries
                     if prefer_dir and e["source"].startswith(prefer_dir.rstrip("/") + "/")]
        index[name] = (preferred[0] if len(preferred) == 1
                       else {"ambiguous": [e["source"] for e in entries]})
    return index


def compare_baselines(ledger, produced, root, prefer_dir=None, deploy_dir=None):
    """Emit one baseline event per produced artifact. Returns the comparison
    rows for the bundle manifest.

    A raw-sha comparison is only meaningful for a BIT-REPRODUCIBLE artifact.
    A Vivado bitstream embeds a build date and time, so comparing one by raw
    hash reports a divergence on every rebuild from identical source -- an
    alarm that always fires is an alarm that gets ignored. Such artifacts
    declare build.reproducibility.mode in their manifest:

      bit-reproducible            raw sha must match      -> reproduces/diverges
      load-equivalent-normalized  NORMALIZED sha must match, under the
                                  manifest's bounded policy
                                  -> load_equivalent (VERIFIED) / diverges
      load-equivalent-unverified  nothing is checked      -> load_equivalent

    The middle row is what plan-03 Step 6 adds. Before it, every
    load-equivalent artifact returned load_equivalent unconditionally -- the
    verdict said "not comparable by raw hash" and was read as "fine"."""
    index = baseline_index(root, prefer_dir)
    rows = []
    for entry in produced:
        b = index.get(entry["file"])
        recorded = source = normalized = None
        if b is None:
            verdict = "no_baseline"
        elif "ambiguous" in b:
            # Two manifests claim this artifact name and none is under the
            # building profile. Refuse to guess, and say so loudly.
            verdict = "no_baseline"
            ledger.gate("baseline_ambiguous", "fail",
                        evidence=f"{entry['file']} is claimed by "
                                 f"{len(b['ambiguous'])} manifests: "
                                 f"{', '.join(b['ambiguous'])}"[:400])
            print(f"  baseline {entry['file']:28s} AMBIGUOUS -- "
                  f"{len(b['ambiguous'])} manifests claim this name")
        else:
            recorded, source = b["sha256"], b["source"]
            if b["mode"] == "load-equivalent-normalized":
                verdict, normalized = _verify_normalized(
                    ledger, entry, b, root, deploy_dir)
            elif b["mode"].startswith("load-equivalent"):
                verdict = "load_equivalent"
            else:
                verdict = "reproduces" if recorded == entry["sha256"] else "diverges"
        ledger.baseline(entry["file"], entry["sha256"], recorded, source, verdict)
        row = {"artifact": entry["file"], "observed_sha256": entry["sha256"],
               "verdict": verdict}
        if recorded:
            row["recorded_sha256"] = recorded
            row["source"] = source
        if normalized:
            row["normalized_sha256"] = normalized
        rows.append(row)
        if b is None or "ambiguous" not in b:
            note = "" if b is None else f"  ({b['mode']})"
            print(f"  baseline {entry['file']:28s} {verdict}{note}")
    return rows


def _verify_normalized(ledger, entry, baseline, root, deploy_dir):
    """Machine-verify a load-equivalent artifact against its bounded policy.

    Returns (verdict, normalized_sha). A policy that cannot be applied -- the
    artifact is not where we can read it, the declared structure does not
    match, the schema is unknown -- fails as `diverges` with the reason banked,
    never as a quiet pass. 'We could not check' must not look like 'it
    matched'."""
    policy = baseline.get("policy") or {}
    path = os.path.join(deploy_dir, entry["file"]) if deploy_dir else None
    if not path or not os.path.isfile(path):
        ledger.gate("baseline_normalized", "fail",
                    evidence=f"{entry['file']} declares "
                             f"load-equivalent-normalized but the artifact is not "
                             f"readable for normalization")
        return "diverges", None
    try:
        got = normalized_sha256(path, policy)
    except Exception as exc:
        # Deliberately broad. Anything that stops the policy being applied --
        # a malformed artifact, a policy field the manifest never declared, a
        # parser blowing up on bytes that are not the shape we expected -- is
        # a DIVERGENCE with the reason banked. An exception escaping here
        # would abort a build over a comparison that is meant to be a report.
        ledger.gate("baseline_normalized", "fail",
                    evidence=f"{entry['file']}: {type(exc).__name__}: {exc}"[:400])
        print(f"  baseline {entry['file']:28s} NORMALIZATION FAILED: {exc}")
        return "diverges", None
    want = policy.get("normalized_sha256")
    ok = got == want
    ledger.gate("baseline_normalized", "pass" if ok else "fail",
                evidence=f"{entry['file']} normalized sha256 {got} vs manifest {want} "
                         f"({policy.get('normalization_schema')})")
    return ("load_equivalent" if ok else "diverges"), got


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
    if kinds == {"load_equivalent"}:
        return "load_equivalent"
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


def vitis_settings_for(version):
    """Same, for Vitis -- the Linux stages need xsct, dtc, bootgen and the
    aarch64/armr5 cross toolchains, all of which ship with a Vitis install.
    A profile may be an ERA MIX (txm8l4 pins Vivado 2023.2 over a 2022.2 boot
    chain), so this resolves the vitis slot independently of the vivado one."""
    from socks_lib import VIVADO_SEARCH_PATHS
    roots = sorted({p.split("/Vivado/")[0] for p in VIVADO_SEARCH_PATHS})
    for root in roots:
        path = os.path.join(root, "Vitis", version, "settings64.sh")
        if os.path.isfile(path):
            return path
    raise SystemExit(f"ERROR: recipe pins Vitis {version}, which is not installed "
                     f"(searched {', '.join(os.path.join(r, 'Vitis') for r in roots)})")


def apply_build_env(recipe, ledger=None):
    """The recipe IS the build: its toolchain pin and build_env are applied
    here rather than left to whatever the invoking shell happened to export.
    An inherited value that DISAGREES is overridden and said out loud.

    Shared with hil_project.run_adi_make_stage14 so the Stage-14 entry point
    and `socks build --execute` cannot tell two different stories about what
    the image was built from; ledger is optional so the non-ledgered caller
    needs no dummy."""
    ledger = ledger or NullLedger()
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


def gate_patch_mirror(ledger, apply_result, root):
    """Assert `upstream + patch == active` for the HDL twins.

    apply_recipe rebuilds every twinned file from its pristine copy plus the
    recipe's patch series, so if the tracked working copy differs from that
    reconstruction, apply just SILENTLY REVERTED someone's edit -- which is
    exactly the failure mode CLAUDE.md marks 'breaks silently if skipped'. The
    invariant is therefore visible as a git diff over the tracked HDL project
    after the apply, and it must be empty.

    The interim Linux executor this replaced checked the same invariant by
    copy-patch-cmp over a glob, which the plan-02 rehoming of the patches had
    silently emptied -- it iterated nothing and passed. This form cannot go
    vacuous: it compares the real tree against the real reconstruction, so
    there is no list that can be empty."""
    hdl_dir = apply_result.get("hdl_project_dir")
    if not hdl_dir:
        ledger.gate("patch_mirror", "fail",
                    evidence="apply result named no hdl_project_dir")
        raise SystemExit("ERROR: apply result named no hdl_project_dir")
    try:
        diff = subprocess.run(["git", "-C", root, "diff", "--name-only", "--", hdl_dir],
                              capture_output=True, text=True, check=True).stdout.split()
    except (subprocess.CalledProcessError, OSError) as exc:
        ledger.gate("patch_mirror", "fail", evidence=f"cannot read git diff: {exc}"[:400])
        raise SystemExit(f"ERROR: patch-mirror check could not run: {exc}")
    ok = not diff
    ledger.gate("patch_mirror", "pass" if ok else "fail",
                evidence=(f"tracked {hdl_dir} is diff-empty after apply: "
                          f"upstream + patch == active" if ok else
                          f"apply rewrote tracked files, so the working copy did NOT "
                          f"equal upstream+patch: {', '.join(diff)}")[:400])
    print(f"  patch mirror: {hdl_dir} "
          f"{'diff-empty (upstream + patch == active)' if ok else 'DIVERGED: ' + ', '.join(diff)}"
          f"  [{'PASS' if ok else 'FAIL'}]")
    if not ok:
        raise SystemExit(f"ERROR: patch-mirror violation -- apply_recipe rewrote "
                         f"{', '.join(diff)}. Regenerate the patch from the active "
                         f"file (or restore the active file) before building.")


def execute_hdl(recipe, ctx, ledger):
    recipe_abs, project_dir, root = ctx["recipe_abs"], ctx["project_dir"], ctx["root"]
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
    gate_patch_mirror(ledger, result, root)
    ledger.emit("stage_done", stage="apply", status="ok",
                elapsed_s=round(time.time() - stage_t0, 1))

    from hil_project import run_adi_make_stage14
    with open(os.path.join(project_dir, "socks.json")) as f:
        build_cfg = json.load(f).get("build", {})
    build_dir = ctx["build_dir"]
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

    gate_routed_timing(ledger, os.path.join(adi_project_dir, "timing_impl.log"), root)
    emit_sha256_manifest(build_dir)          # build-dir inventory, not the deploy set
    gate_toolchain_actual(ledger, recipe["toolchain"]["vivado"], adi_project_dir,
                          build_dir, root)

    # The deploy set is named, not discovered. Walking the build dir would bank
    # system_top.bit twice -- Stage 14 stages one copy under
    # vivado_project/<proj>.runs/impl_1/ and Stage 16 leaves another at the
    # build-dir root -- and a deploy list with two rows for one filename has no
    # single baseline to compare against.
    xsa = os.path.join(build_dir, "system_wrapper.xsa")
    bits = sorted(glob.glob(os.path.join(build_dir, "vivado_project", "*.runs",
                                         "impl_1", "*.bit")))
    if not bits:
        raise SystemExit(f"ERROR: Stage 14 exited 0 but staged no bitstream under "
                         f"{os.path.relpath(build_dir, root)}/vivado_project/*/impl_1")
    if not os.path.isfile(xsa):
        raise SystemExit(f"ERROR: Stage 14 exited 0 but system_wrapper.xsa is missing")
    produced = [ledger.artifact(bank_deployable(bits[0], os.path.basename(bits[0]),
                                                ctx["deploy"]), root),
                ledger.artifact(bank_deployable(xsa, "system_wrapper.xsa",
                                                ctx["deploy"]), root)]
    return {"produced": [p for p in produced if p],
            "xsa": xsa, "xsa_sha": file_digests(xsa)[0],
            "no_os_tree": result["no_os_build_root"]}


# -------------------------------------------------------- execute: linux
# Ported (plan-03 Steps 2-5) from the interim Linux executor that preceded
# this driver; its stage ids survive in the plan-28 desk-run ledger banked
# under codex-handoff/. Every reproducibility gate that guarded a stage there
# is kept, and is now emitted as a ledger gate instead of a private JSON
# ledger. The traps in each stage's docstring each cost a build or a board
# cycle to find -- they are load-bearing, not commentary.


class StageFail(Exception):
    """A Linux stage gate failed. Caught in execute_linux, which banks the
    failure as a ledger gate before re-raising as a SystemExit -- a stage that
    dies must still leave a readable run record."""


def run_step(ledger, stage, name, argv, cwd, log_path, env=None, check=True):
    """Run one command, tee stdout+stderr to a log, emit a ledger step.

    argv is a list -- no implicit shell. Commands that genuinely need a shell
    (the settings64.sh sourcing) build their own `bash -c` via sourced()."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    t0 = time.time()
    proc = subprocess.run(argv, cwd=cwd, env={**os.environ, **(env or {})},
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    with open(log_path, "w") as fh:
        fh.write(proc.stdout)
    ledger.emit("step", stage=stage, name=name,
                elapsed_s=round(time.time() - t0, 1),
                detail=None if proc.returncode == 0 else f"rc={proc.returncode}")
    if check and proc.returncode != 0:
        tail = "\n".join(proc.stdout.strip().splitlines()[-15:])
        raise StageFail(f"{stage}/{name} exited {proc.returncode}; see "
                        f"{os.path.basename(log_path)}\n{tail}")
    return proc


def sourced(settings, cmd):
    """A DEDICATED clean bash that sources a Xilinx settings64.sh first.

    plan-28 desk-run ledger E-DTB1: sourcing inside a compound or piped
    command left dtc unresolved. The settings script exports tool paths into the shell
    it runs in, so it needs its own shell whose only job is to run this one
    command."""
    return ["bash", "-c", f"source {shlex.quote(settings)} && {cmd}"]


def bank_deployable(source, filename, deploy_dir):
    """Copy one gated deployable into this run's canonical deploy set.

    Resume is permitted only when the existing banked file is byte-identical;
    a conflicting file fails closed rather than silently mixing two builds
    into one deploy directory (rule carried over verbatim from the interim
    executor)."""
    if not os.path.isfile(source):
        raise StageFail(f"deployable source is missing: {source}")
    os.makedirs(deploy_dir, exist_ok=True)
    destination = os.path.join(deploy_dir, filename)
    source_sha = file_digests(source)[0]
    if os.path.exists(destination):
        if not os.path.isfile(destination) or file_digests(destination)[0] != source_sha:
            raise StageFail(f"conflicting banked deployable: {destination}")
    else:
        shutil.copy2(source, destination)
    if file_digests(destination)[0] != source_sha:
        raise StageFail(f"banked deployable hash mismatch: {destination}")
    return destination


def normalized_kernel_image_sha256(path, policy):
    """Hash load-semantic Image bytes under the manifest's bounded policy.

    The accepted kernel predates complete reproducibility controls. Its raw
    Image varies only in GNU build-ID payloads (derived from non-loaded DWARF)
    and empty-initramfs newc mtimes. The manifest-declared structure counts are
    asserted BEFORE zeroing those fields, so normalization cannot mask
    arbitrary drift -- the pattern Step 6 must follow for the bitstream."""
    with open(path, "rb") as fh:
        body = bytearray(fh.read())
    note = b"\x04\x00\x00\x00\x14\x00\x00\x00\x03\x00\x00\x00GNU\x00"
    note_offsets, offset = [], 0
    while True:
        found = body.find(note, offset)
        if found < 0:
            break
        note_offsets.append(found)
        start = found + len(note)
        body[start:start + 20] = bytes(20)
        offset = start + 20
    expected_notes = policy["expected_gnu_build_id_notes"]
    if len(note_offsets) != expected_notes:
        raise StageFail(f"kernel Image has {len(note_offsets)} GNU build-ID notes; "
                        f"normalization policy requires {expected_notes}")
    cpio_header = re.compile(b"070701[0-9A-Fa-f]{40}([0-9A-Fa-f]{8})")
    cpio_mtimes = [m.span(1) for m in cpio_header.finditer(body)]
    expected_headers = policy["expected_newc_headers"]
    if len(cpio_mtimes) != expected_headers:
        raise StageFail(f"kernel Image has {len(cpio_mtimes)} newc headers; "
                        f"normalization policy requires {expected_headers}")
    for start, end in cpio_mtimes:
        body[start:end] = b"00000000"
    return hashlib.sha256(body).hexdigest()


def _manifest_for(root, recipe_abs, rel_or_path):
    """Resolve a recipe-declared manifest pointer. Recipe paths are either
    repo-relative (the patch convention) or relative to the profile home (the
    dt block's convention); both are accepted, neither is guessed at."""
    for base in (root, os.path.dirname(recipe_abs)):
        candidate = os.path.join(base, rel_or_path)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    raise StageFail(f"recipe-declared manifest does not resolve: {rel_or_path}")


# ---- Step 2: kernel -------------------------------------------------------

def stage_kernel(recipe, ctx, ledger):
    """Build the kernel Image from stages.linux.kernel under the reproducibility
    policy its build-manifest declares.

    Two traps, both from the plan-28 desk-run ledger, both preserved:
      C4  the .config seed is MANDATORY -- `olddefconfig` in a bare O= dir
          produces a DEFAULT config, not the Kuiper baseline. The build would
          succeed and the board would not boot the same kernel.
      C3  the kernel patches carry no git-format-patch `From:` headers, so
          `git am` fails STRUCTURALLY. Always `git apply` + a scripted commit.
    """
    k = recipe["stages"]["linux"]["kernel"]
    stage_t0 = time.time()
    ledger.emit("stage_start", stage="kernel")
    manifest_path = _manifest_for(ctx["root"], ctx["recipe_abs"], k["artifact_manifest"])
    manifest = json.load(open(manifest_path))
    policy = k["reproducibility"]          # the recipe is the master (ruling 3)

    # The same facts are declared twice -- in the recipe (which builds) and in
    # the artifact manifest (which records what was accepted). Gate that they
    # still agree rather than silently preferring one; a contract stated twice
    # and checked never is how the retired-pointer defects got in.
    drift = []
    if manifest["kernel"]["release"] != k["release"]:
        drift.append(f"release {manifest['kernel']['release']} != {k['release']}")
    if manifest["source"]["commit"] != k["source"]["commit"]:
        drift.append("source.commit")
    if manifest["build"]["release_string_controls"] != k["release_string_controls"]:
        drift.append("release_string_controls")
    if manifest["build"]["reproducibility"] != policy:
        drift.append("reproducibility policy")
    m_patches = [p["file"] for p in manifest["build"]["required_kernel_patches"]
                 if p["status"] == "required"]
    r_patches = [p["file"] for p in k["required_patches"] if p["status"] == "required"]
    if m_patches != r_patches:
        drift.append("required kernel patch list")
    ledger.gate("kernel_inputs_agree", "fail" if drift else "pass",
                evidence=("; ".join(drift) if drift else
                          f"recipe and {os.path.relpath(manifest_path, ctx['root'])} "
                          f"declare the same kernel build inputs")[:400])
    if drift:
        raise StageFail(f"recipe and kernel build-manifest disagree: {'; '.join(drift)}")

    src = os.path.join(ctx["scratch"], "adi-linux")
    build = os.path.join(ctx["scratch"], "build")
    os.makedirs(ctx["scratch"], exist_ok=True)
    pin = k["source"]["commit"]
    repo_url = f"https://{k['source']['repo']}"
    if not os.path.isdir(src):
        # blobless first; a full clone only as the fallback (ledger C1)
        print(f"  kernel:    cloning {k['source']['repo']} (blobless) -> {ctx['scratch_label']}")
        proc = run_step(ledger, "kernel", "git clone --filter=blob:none",
                        ["git", "clone", "--filter=blob:none", repo_url, src],
                        ctx["scratch"], os.path.join(ctx["logs"], "kernel-clone.log"),
                        check=False)
        if proc.returncode != 0:
            run_step(ledger, "kernel", "git clone (full fallback)",
                     ["git", "clone", repo_url, src], ctx["scratch"],
                     os.path.join(ctx["logs"], "kernel-clone-full.log"))
    run_step(ledger, "kernel", f"git checkout {pin[:12]}",
             ["git", "checkout", "--force", pin], src,
             os.path.join(ctx["logs"], "kernel-checkout.log"))

    for entry in k["required_patches"]:
        if entry["status"] != "required":
            continue
        patch = os.path.join(ctx["root"], entry["file"])
        name = os.path.basename(entry["file"])
        run_step(ledger, "kernel", f"git apply --check {name}",
                 ["git", "apply", "--check", patch], src,
                 os.path.join(ctx["logs"], f"kernel-patch-check-{name}.log"))
        run_step(ledger, "kernel", f"git apply {name}",
                 ["git", "apply", patch], src,
                 os.path.join(ctx["logs"], f"kernel-patch-{name}.log"))
        run_step(ledger, "kernel", f"commit {name}",
                 ["git", "-c", "user.name=socks_build",
                  "-c", "user.email=socks_build@local", "commit", "-am",
                  f"apply {name}"], src,
                 os.path.join(ctx["logs"], f"kernel-commit-{name}.log"))

    os.makedirs(build, exist_ok=True)
    config_src = os.path.join(os.path.dirname(manifest_path), k["config"])
    if not os.path.isfile(config_src):
        raise StageFail(f"kernel .config seed is missing: {config_src} -- olddefconfig "
                        f"without it silently produces a DEFAULT config")
    shutil.copyfile(config_src, os.path.join(build, ".config"))
    controls = k["release_string_controls"]
    with open(os.path.join(src, ".scmversion"), "w") as fh:
        fh.write(controls[".scmversion"] + "\n")
    build_env = {key: str(controls[key]) for key in
                 ("KBUILD_BUILD_VERSION", "KBUILD_BUILD_TIMESTAMP",
                  "KBUILD_BUILD_USER", "KBUILD_BUILD_HOST")}
    cross = os.path.join(os.path.dirname(ctx["vitis_settings"]),
                         "gnu/aarch64/lin/aarch64-linux/bin/aarch64-linux-gnu-")
    mk = ["make", f"O={build}", "ARCH=arm64", f"CROSS_COMPILE={cross}"]

    olddefconfig_log = os.path.join(ctx["logs"], "kernel-olddefconfig.log")
    run_step(ledger, "kernel", "make olddefconfig", mk + ["olddefconfig"], src,
             olddefconfig_log, env=build_env)
    # C4 refinement: "No change to .config" proves the seed is current against
    # THIS tree's Kconfig. A changed config is a drift WARNING, not a stopper
    # (recorded review ruling) -- but it must be visible, so it is a gate.
    seed_held = "No change to .config" in open(olddefconfig_log, errors="replace").read()
    ledger.gate("kernel_config_seed", "pass" if seed_held else "fail",
                evidence=("olddefconfig reports 'No change to .config' -- the seed is "
                          "current against this tree's Kconfig" if seed_held else
                          "olddefconfig CHANGED the seeded .config -- the baseline "
                          "config has drifted against this kernel tree (FINDING)"))
    print(f"  kernel:    .config seed {'held' if seed_held else 'DRIFTED (finding)'}")

    run_step(ledger, "kernel", f"make -j{ctx['jobs']} Image",
             mk + [f"-j{ctx['jobs']}", "Image"], src,
             os.path.join(ctx["logs"], "kernel-build.log"), env=build_env)
    image = os.path.join(build, "arch/arm64/boot/Image")
    if not os.path.isfile(image):
        raise StageFail("kernel build exited 0 but Image is missing")

    rel = run_step(ledger, "kernel", "make -s kernelrelease", mk + ["-s", "kernelrelease"],
                   src, os.path.join(ctx["logs"], "kernel-release.log"),
                   env=build_env).stdout.strip().splitlines()[-1].strip()
    ok_rel = rel == k["release"]
    ledger.gate("kernel_release", "pass" if ok_rel else "fail",
                evidence=f"kernelrelease {rel!r}, recipe declares {k['release']!r}")
    if not ok_rel:
        raise StageFail(f"kernelrelease {rel!r} != recipe {k['release']!r}")

    raw = file_digests(image)[0]
    normalized = normalized_kernel_image_sha256(image, policy)
    ok_norm = normalized == policy["normalized_sha256"]
    ledger.gate("repro_normalized_sha", "pass" if ok_norm else "fail",
                evidence=f"Image normalized sha256 {normalized} vs manifest "
                         f"{policy['normalized_sha256']} (raw {raw})")
    if not ok_norm:
        raise StageFail(f"Image normalized sha256 {normalized} != manifest "
                        f"{policy['normalized_sha256']}; raw sha256 {raw}")
    exact = raw == manifest["artifact"]["sha256"]
    if not exact:
        # Review ruling: a raw-sha difference is a FINDING, not a run-stopper.
        # The manifest declares mode load-equivalent-normalized, so the baseline
        # comparison reports load_equivalent rather than diverges.
        ledger.emit("progress", stage="kernel",
                    detail=(f"FINDING: raw Image sha {raw} differs from the accepted "
                            f"cache {manifest['artifact']['sha256']}; bounded "
                            f"normalized hash passed")[:200])
    print(f"  kernel:    {rel}  normalized sha PASS  "
          f"(raw {'== accepted cache' if exact else 'differs -- finding'})")
    banked = bank_deployable(image, "Image", ctx["deploy"])
    ledger.emit("stage_done", stage="kernel", status="ok",
                elapsed_s=round(time.time() - stage_t0, 1))
    return banked


# ---- Step 3: device tree --------------------------------------------------

def dt_nco_shifts(dts_text):
    """-> {node path: signed Hz} for every adi,nco-frequency-shift-hz cell of a
    decompiled DTS. The property is two 32-bit cells carrying one signed 64-bit
    Hz value; negative shifts are normal (L2 and L5 sit below the CDDC)."""
    shifts, stack = {}, []
    cell = re.compile(r"adi,nco-frequency-shift-hz = <(0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+)>;")
    for line in dts_text.split("\n"):
        s = line.strip()
        if s.endswith("{"):
            stack.append(s[:-1].strip())
            continue
        if s == "};":
            if stack:
                stack.pop()
            continue
        m = cell.match(s)
        if m:
            raw = (int(m.group(1), 16) << 32) | int(m.group(2), 16)
            shifts["/".join(stack)] = raw - (1 << 64) if raw >> 63 else raw
    return shifts


def check_dt_operating_point(recipe, dtb_path, ctx, ledger):
    """Assert the BUILT DT means what the same recipe's operating_point says.

    Why this gate exists (the 2026-07-27 Iridium finding): the DT source was
    edited on one branch while the DTB of record was built on another, so the
    board ran a band 1.92 MHz off while every label -- sigmf_writer BANDS[],
    the recorder's legal centers -- carried the recentered value. The recipe
    knew the right answer the whole time: operating_point.rx.bands[] declares
    each offset with error_hz 0. Nothing compared the compiled artifact to it.
    A dtb_sha gate cannot catch this class at all -- the blob matched its own
    manifest perfectly; it was the manifest and the blob that were both a
    branch behind. Reproducibility and meaning are different questions.

    RX ONLY, deliberately. operating_point.rx maps onto this DT one-for-one:
    cddc_center_hz -> every adi,rx-adcs main-data-path adc@*, and each band's
    fddc -> the matching channelizer-path channel@<fddc>. The tx block
    declares a DIFFERENT plan from the DT's quad-band TX mirror
    (main_nco_shift_hz 1575420000 vs the DT's 1404240000;
    channel_nco_shift_hz 0 vs four per-band offsets), so gating tx here would
    manufacture false failures. Reconcile that discrepancy before extending
    this gate to the TX side -- do not simply add it.
    """
    op = (recipe.get("operating_point") or {}).get("rx")
    if not op or not op.get("bands"):
        ledger.gate("dt_operating_point", "pass",
                    evidence="recipe declares no operating_point.rx bands; "
                             "nothing to cross-check")
        return
    decompiled = os.path.join(ctx["tmp"], "dt-operating-point.dts")
    run_step(ledger, "dt", "dtc -I dtb -O dts (operating-point readback)",
             sourced(ctx["vitis_settings"],
                     f"dtc -I dtb -O dts -o {shlex.quote(decompiled)} "
                     f"{shlex.quote(dtb_path)}"),
             ctx["root"], os.path.join(ctx["logs"], "dt-operating-point.log"))
    shifts = dt_nco_shifts(open(decompiled).read())

    def rx(kind, node):
        for path, hz in shifts.items():
            if f"adi,rx-adcs/adi,{kind}/{node}" in path:
                return hz
        return None

    checks, bad = [], []
    want_cddc = op.get("cddc_center_hz")
    if want_cddc is not None:
        seen = {p: hz for p, hz in shifts.items()
                if "adi,rx-adcs/adi,main-data-paths/adc@" in p}
        if not seen:
            bad.append("no rx main-data-path adc@* nodes found in the built DT")
        for path, hz in sorted(seen.items()):
            checks.append(f"{path.rsplit('/', 1)[-1]}={hz}")
            if hz != want_cddc:
                bad.append(f"{path.rsplit('/', 1)[-1]} CDDC shift {hz} != "
                           f"operating_point.rx.cddc_center_hz {want_cddc}")
    for band in op["bands"]:
        node = f"channel@{band['fddc']}"
        hz = rx("channelizer-paths", node)
        want = band["offset_hz"]
        checks.append(f"{band['name']}/{node}={hz}")
        if hz is None:
            bad.append(f"band {band['name']} declares fddc {band['fddc']} but "
                       f"the built DT has no rx channelizer {node}")
        elif hz != want:
            bad.append(f"{band['name']} ({node}) shift {hz} != declared "
                       f"offset_hz {want} (delta {hz - want} Hz)")
    ledger.gate("dt_operating_point", "fail" if bad else "pass",
                evidence=("; ".join(bad) if bad else
                          "built DT matches operating_point.rx: " +
                          ", ".join(checks))[:400])
    if bad:
        # A run-stopping FINDING, unlike dtb_sha's reproducibility signal: the
        # artifact contradicts the operating point this same recipe declares,
        # so one of the two is wrong and neither may be shipped on a guess.
        print(f"  dt:        OPERATING-POINT MISMATCH -- {bad[0]}")
    else:
        print(f"  dt:        operating point matches ({len(checks)} NCO cells)")


def stage_dt(recipe, ctx, ledger):
    """Compile the profile DTS to its DTB and compare against its manifest.

    Trap (plan-28 desk-run ledger E-DTB1): dtc MUST be invoked with the Vitis
    settings sourced in a dedicated clean bash -- see sourced(). dtc output is
    deterministic, so this artifact is genuinely bit-reproducible and a
    mismatch is a real finding rather than expected build noise."""
    dt = recipe["stages"]["linux"]["dt"]
    stage_t0 = time.time()
    ledger.emit("stage_start", stage="dt")
    profile_home = os.path.dirname(ctx["recipe_abs"])
    dts = os.path.join(profile_home, dt["source"])
    if not os.path.isfile(dts):
        raise StageFail(f"recipe-declared DTS does not resolve: {dt['source']}")
    manifest_path = _manifest_for(ctx["root"], ctx["recipe_abs"], dt["dtb_manifest"])
    expected = json.load(open(manifest_path))["artifact"]["sha256"]
    out = os.path.join(ctx["tmp"], os.path.basename(dt["dtb"]))
    os.makedirs(ctx["tmp"], exist_ok=True)
    run_step(ledger, "dt", "dtc -I dts -O dtb",
             sourced(ctx["vitis_settings"],
                     f"dtc -I dts -O dtb -o {shlex.quote(out)} {shlex.quote(dts)}"),
             ctx["root"], os.path.join(ctx["logs"], "dtb.log"))
    got = file_digests(out)[0]
    ok = got == expected
    ledger.gate("dtb_sha", "pass" if ok else "fail",
                evidence=f"{os.path.basename(out)} sha256 {got} vs manifest {expected}")
    if not ok:
        # Reproducibility FINDING per the recorded review ruling, not a
        # run-stopper -- but the artifact is declared bit-reproducible, so this
        # is a real signal, never expected noise.
        ledger.emit("progress", stage="dt",
                    detail=f"FINDING: dtb sha {got} != manifest {expected}"[:200])
    print(f"  dt:        {os.path.basename(out)} sha "
          f"{'== manifest' if ok else 'DIFFERS (finding)'}")
    # Reproducibility is settled above; now ask whether the artifact MEANS what
    # the recipe declares. The two questions are independent -- see the gate.
    check_dt_operating_point(recipe, out, ctx, ledger)
    # Bank under the RECIPE-declared basename, not a generic system.dtb: the
    # deploy set is evidence, and the baseline comparator matches an artifact
    # to its manifest BY FILENAME. Renaming for the board's /boot is a
    # deploy-time concern, not a build-output one.
    banked = bank_deployable(out, os.path.basename(dt["dtb"]), ctx["deploy"])
    ledger.emit("stage_done", stage="dt", status="ok",
                elapsed_s=round(time.time() - stage_t0, 1))
    return banked


# ---- Step 4: R5 capture firmware ------------------------------------------

def scrubbed_path(path, ctx):
    """Repo-relative when the path is inside the worktree; a placeholder when
    it is not.

    The ledger is a TRACKED file, and the project's fingerprint guard refuses
    any commit carrying an absolute host path or a username. Plain relpath()
    of an off-tree path produces '../../../../../home/<user>/...', which trips
    that guard -- and --scratch is DOCUMENTED as belonging on a different
    filesystem from the worktree, so following this tool's own advice was
    enough to produce an uncommittable bank (2026-07-27, TX-B image run).
    Evidence keeps its meaning without the host prefix: what the gate asserts
    is that the BSP was found, not where the operator's disk is mounted."""
    root = os.path.abspath(ctx["root"])
    target = os.path.abspath(path)
    if target == root or target.startswith(root + os.sep):
        return os.path.relpath(target, root)
    scratch = os.path.abspath(ctx.get("scratch") or "")
    if scratch and (target == scratch or target.startswith(scratch + os.sep)):
        return os.path.join("<scratch>", os.path.relpath(target, scratch))
    return os.path.join("<offtree>", os.path.basename(target))


def stage_r5(recipe, ctx, ledger, xsa, no_os_tree):
    """Regenerate the R5 BSP from THIS run's XSA and build the capture ELF.

    Three traps, each of which cost a bring-up cycle:
      1. The BSP must be standalone_v8_0. v9_0 wedges Xil_SetMPURegion /
         Xil_ExceptionEnable under bare-rproc ELFs. The capture Makefile
         hard-fails on anything else (check-bsp-v8) and so does this gate.
      2. The profile Makefile pins `NO-OS := $(realpath ../upstream)`, which
         does not exist for this profile (the ADR-017 layer gap). NO-OS is
         passed on the make COMMAND LINE, where it beats a `:=` in the body.
      3. The no-OS tree is the one THIS run materialized and patched -- never
         a pre-seeded, gitignored ADI/no-OS/work/active of unknown provenance.
    All build products (BSP, objects, ELF, desk-test binaries) land under the
    run bundle so the tracked source tree stays untouched."""
    stage_t0 = time.time()
    ledger.emit("stage_start", stage="r5")
    profile_home = os.path.dirname(ctx["recipe_abs"])
    capture = os.path.join(profile_home, "no-os/capture")
    gen_tcl = os.path.join(capture, "gen_r5_bsp.tcl")
    if not os.path.isfile(gen_tcl):
        raise StageFail(f"R5 BSP generator missing: {gen_tcl}")
    if not os.path.isdir(no_os_tree):
        raise StageFail(f"patched no-OS tree missing: {no_os_tree}")
    # RUN-INDEPENDENT paths, deliberately. These land in the ELF: the BSP
    # include dir is passed absolute (-I$(RPROC_BSP)/include) and the sources
    # compile with -g3, so the path is baked into DWARF. Homing them under the
    # per-run bundle made two builds of identical source differ in 543 bytes --
    # every one of them the run label inside a debug-info string. Same-source
    # rebuilds must produce the same ELF, so the scratch root (already
    # run-independent, already off the worktree volume) owns them. The ELF is
    # still BANKED into this run's deploy set; only the workspace is shared.
    r5_root = os.path.join(ctx["scratch"], "r5")
    bsp_root = os.path.join(r5_root, "bsp")
    bsp = os.path.join(bsp_root, "psu_cortexr5_0")
    rproc_build = os.path.join(r5_root, "capture")
    desk_build = os.path.join(r5_root, "desk")
    os.makedirs(bsp_root, exist_ok=True)

    run_step(ledger, "r5", "xsct gen_r5_bsp.tcl",
             sourced(ctx["vitis_settings"],
                     f"xsct {shlex.quote(gen_tcl)} {shlex.quote(xsa)} "
                     f"{shlex.quote(bsp_root)}"),
             ctx["root"], os.path.join(ctx["logs"], "r5-bsp-gen.log"))
    v8 = os.path.isdir(os.path.join(bsp, "libsrc/standalone_v8_0"))
    libxil = os.path.isfile(os.path.join(bsp, "lib/libxil.a"))
    ledger.gate("r5_bsp_standalone_v8_0", "pass" if (v8 and libxil) else "fail",
                evidence=f"standalone_v8_0 {'present' if v8 else 'ABSENT'}, "
                         f"lib/libxil.a {'present' if libxil else 'ABSENT'} in "
                         f"{scrubbed_path(bsp, ctx)}")
    if not v8:
        raise StageFail(f"generated BSP is not standalone_v8_0: {bsp} -- v9_0 wedges "
                        f"Xil_SetMPURegion/ExceptionEnable under bare-rproc ELFs")
    if not libxil:
        raise StageFail(f"generated BSP lacks lib/libxil.a: {bsp}")

    run_step(ledger, "r5", "make rproc-capture",
             sourced(ctx["vitis_settings"],
                     f"make -C {shlex.quote(capture)} rproc-capture "
                     f"HARDWARE={shlex.quote(xsa)} TARGET_CPU=psu_cortexr5_0 "
                     f"NO-OS={shlex.quote(no_os_tree)} RPROC_BSP={shlex.quote(bsp)} "
                     f"RPROC_CAPTURE_BUILD_DIR={shlex.quote(rproc_build)} "
                     f"RPROC_CC=armr5-none-eabi-gcc "
                     f"RPROC_OBJCOPY=armr5-none-eabi-objcopy"),
             ctx["root"], os.path.join(ctx["logs"], "r5-build.log"))
    elf = os.path.join(rproc_build, "r5_0_capture_rproc.elf")
    if not os.path.isfile(elf):
        raise StageFail(f"R5 build exited 0 but the ELF is missing: {elf}")

    # Host desk tests for the recovery ladder -- no cross tools, no BSP.
    desk = run_step(ledger, "r5", "make desk-test",
                    ["make", "-C", capture, "desk-test", f"DESK_BUILD_DIR={desk_build}"],
                    ctx["root"], os.path.join(ctx["logs"], "r5-desk-test.log"),
                    check=False)
    ledger.gate("r5_desk_test", "pass" if desk.returncode == 0 else "fail",
                evidence=f"make desk-test rc={desk.returncode} "
                         f"(capture recovery-ladder host tests)")
    if desk.returncode != 0:
        raise StageFail(f"R5 desk tests failed (rc={desk.returncode}); see r5-desk-test.log")
    print(f"  r5:        BSP standalone_v8_0 + desk-test PASS")
    banked = bank_deployable(elf, "r5_0_capture_rproc.elf", ctx["deploy"])
    ledger.emit("stage_done", stage="r5", status="ok",
                elapsed_s=round(time.time() - stage_t0, 1))
    return banked


# ---- Step 5: boot assembly (internal edge) --------------------------------

def stage_boot_assembly(recipe, ctx, ledger, xsa_sha):
    """Run the recipe's boot_assembly.command verbatim, substituting only the
    per-run fields, and gate its four output invariants.

    The recipe command is the SINGLE command authority -- flags are never
    restated here (restating them is how the --profile/--xsa doc bugs
    happened). xsa_matches_build binds to the XSA sha of THIS run, which is
    the seam that forced the 2026-07-25 hand-invocation: the packager was told
    an XSA, but nothing checked it was the one the bitstream came from."""
    ba = recipe["stages"]["linux"]["boot_assembly"]
    stage_t0 = time.time()
    ledger.emit("stage_start", stage="boot_assembly")
    out_dir = os.path.join(ctx["bundle"], "boot-package")
    cmd = (ba["command"]
           .replace("codex-handoff/<plan>/artifacts/<label>/boot-package", out_dir)
           .replace("<label>", ctx["run_label"])
           .replace("<design>", ctx["build_dir"])
           .replace("<Vitis-2022.2>/settings64.sh", ctx["vitis_settings"]))
    if "<" in cmd and ">" in cmd:
        leftover = re.findall(r"<[^>\s]+>", cmd)
        if leftover:
            raise StageFail(f"boot_assembly.command has unsubstituted placeholders: "
                            f"{', '.join(leftover)}")
    run_step(ledger, "boot_assembly", "package boot chain",
             ["bash", "-c", cmd], ctx["root"],
             os.path.join(ctx["logs"], "boot-package.log"))

    manifest_path = os.path.join(out_dir, "package_manifest.json")
    if not os.path.isfile(manifest_path):
        raise StageFail(f"boot packager wrote no package_manifest.json at {manifest_path}")
    manifest = json.load(open(manifest_path))
    boot_bin = os.path.join(out_dir, "BOOT.BIN")
    gates = {
        "homed_match_passed": manifest["homed_match_passed"] is True,
        "no_r5_partitions": manifest["no_r5_partitions"] is True,
        "xsa_matches_build": manifest["fsbl_pmufw_source"]["xsa_sha256"] == xsa_sha,
        "boot_sha": (os.path.isfile(boot_bin)
                     and file_digests(boot_bin)[0] == manifest["output_boot_sha256"]),
    }
    evidence = {
        "homed_match_passed": "both stock payloads match their homed manifests",
        "no_r5_partitions": "no R5 partition in the packaged image",
        "xsa_matches_build": (f"FSBL/PMUFW built from xsa sha256 "
                              f"{manifest['fsbl_pmufw_source']['xsa_sha256']}; this "
                              f"run's XSA is {xsa_sha}"),
        "boot_sha": f"BOOT.BIN sha equals package_manifest.output_boot_sha256",
    }
    for name, passed in gates.items():
        ledger.gate(name, "pass" if passed else "fail", evidence=evidence[name][:400])
        print(f"  boot:      {name:22s} [{'PASS' if passed else 'FAIL'}]")
    if not all(gates.values()):
        raise StageFail(f"boot-package gates failed: "
                        f"{[n for n, v in gates.items() if not v]}")
    banked = bank_deployable(boot_bin, "BOOT.BIN", ctx["deploy"])
    sha, md5, _size = file_digests(banked)
    block = {
        "manifest": os.path.relpath(manifest_path, ctx["bundle"]),
        "boot_bin_sha256": sha, "boot_bin_md5": md5,
        "gates": [{"name": n, "verdict": "pass", "evidence": evidence[n][:400]}
                  for n in gates],
    }
    ledger.emit("stage_done", stage="boot_assembly", status="ok",
                elapsed_s=round(time.time() - stage_t0, 1))
    return banked, block


def ensure_no_os_tree(project_dir, recipe_abs, ledger):
    """Materialize + patch the no-OS tree for a standalone --stage linux run.

    The R5 stage must consume the tree THIS run produced. When the HDL stage
    ran, its apply result names it; when it did not, apply the recipe here
    rather than trusting whatever a previous session left in the gitignored
    ADI/no-OS/work/active."""
    result = apply_recipe(project_dir, recipe_path=recipe_abs)
    if result.get("status") != "applied":
        raise StageFail(f"recipe apply did not complete: {result}")
    ledger.gate("apply_recipe", "pass",
                evidence=f"{len(result['patches']['no_os']) + len(result['patches']['hdl'])} "
                         f"patches applied from {result['manifest_path']} "
                         f"(--stage linux: no-OS tree materialized for the R5 build)")
    return result["no_os_build_root"]


def execute_linux(recipe, ctx, ledger, xsa=None, xsa_sha=None, no_os_tree=None):
    """Run the four Linux stages. kernel and dt are independent of the HDL
    stage; r5 and boot_assembly consume this run's XSA over the internal edge
    the unified recipe declares (stages.linux.pl.from_stage == hdl_no_os)."""
    if xsa is None:
        # --stage linux on its own: the XSA in the project build dir IS this
        # invocation's PL input. Bind to it explicitly rather than letting the
        # packager compare an XSA against itself.
        xsa = os.path.join(ctx["build_dir"], "system_wrapper.xsa")
        if not os.path.isfile(xsa):
            raise StageFail(f"--stage linux needs a built XSA at "
                            f"{os.path.relpath(xsa, ctx['root'])}; run --stage all "
                            f"or --stage hdl first")
        xsa_sha = file_digests(xsa)[0]
        ledger.emit("progress", stage="r5",
                    detail=(f"PL input from a prior hdl_no_os run: "
                            f"{os.path.relpath(xsa, ctx['root'])} sha256 {xsa_sha}")[:200])
    produced = [stage_kernel(recipe, ctx, ledger),
                stage_dt(recipe, ctx, ledger),
                stage_r5(recipe, ctx, ledger, xsa, no_os_tree)]
    boot_bin, boot_block = stage_boot_assembly(recipe, ctx, ledger, xsa_sha)
    produced.append(boot_bin)
    return produced, boot_block


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
                       produced, baseline_rows, verdict, stage, boot_package=None):
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
    if boot_package:
        doc["boot_package"] = boot_package
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
    parser.add_argument("--stage", choices=["hdl", "linux", "dt", "all"], default="all",
                        help="hdl | linux | all are the two halves and both. dt is a "
                             "RE-ENTRY POINT, not a third half: it runs the Linux half's "
                             "dt stage alone, because the recipe's dt block needs only the "
                             "DTS and the Vitis dtc. A DT-only correction is a recurring "
                             "real case (a reserved-memory or NCO cell moves while the "
                             "board keeps running its current kernel, R5 ELF and BOOT.BIN), "
                             "and before this existed the smallest flyable unit was the "
                             "whole Linux half -- which rebuilds all three of those and so "
                             "cannot describe the image the board is actually booting. "
                             "Under --stage all the dt stage still runs inside the Linux "
                             "half; this flag does not double it.")
    parser.add_argument("--ledger", default=None,
                        help="build-ledger.jsonl path (default <project>/build/hil/)")
    parser.add_argument("--no-ledger", action="store_true",
                        help="Disable ledger emission (the build itself is unaffected either way)")
    parser.add_argument("--run-label", default=None,
                        help="Bundle name under systems/builds/<label>/ (deploy set, logs, "
                             "ledger, build-output.json). Default: a timestamped label")
    parser.add_argument("--scratch", default="~/bench-scratch/socks-build",
                        help="Kernel tree + large build scratch. Keep this on a roomy "
                             "volume -- it is a DIFFERENT filesystem from the worktree, "
                             "so freeing one does not help the other")
    parser.add_argument("--jobs", type=int, default=8,
                        help="make -j for the kernel build (runbook-documented value: 8)")
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
        if args.stage == "dt":
            plan_dt(recipe)
        elif stage_selected(args, "linux"):
            plan_linux(recipe)
        return 0

    # execute
    root = _repo_root(project_dir)
    run_label = args.run_label or ("socks-build-" + datetime.now().strftime("%Y%m%dT%H%M%S"))
    if not args.run_label:
        print(f"  run label: {run_label} (synthesized -- pass --run-label to name it)")
    # Evidence is homed under the run bundle on the worktree volume, never in
    # /tmp: a host reboot mid-window once wiped a full pass of verdicts.
    bundle = os.path.join(root, "systems", "builds", run_label)
    ctx = {
        "root": root, "project_dir": project_dir, "recipe_abs": recipe_abs,
        "run_label": run_label, "bundle": bundle,
        "build_dir": os.path.join(project_dir, "build", "hil"),
        "deploy": os.path.join(bundle, "deploy"),
        "logs": os.path.join(bundle, "logs"),
        "tmp": os.path.join(bundle, "tmp"),
        # The kernel tree is tens of GiB and belongs on the roomy volume, which
        # is a DIFFERENT filesystem from the worktree -- freeing one does not
        # help the other.
        "scratch": os.path.abspath(os.path.expanduser(args.scratch)),
        "scratch_label": args.scratch,
        "jobs": args.jobs,
        "vitis_settings": None,
    }
    ledger = make_ledger(args, project_dir)
    recipe_sha = hashlib.sha256(open(recipe_abs, "rb").read()).hexdigest()
    wt_sha, wt_dirty = worktree_state(root)
    if wt_sha is None and (ledger.enabled or args.run_label):
        # Fail FAST, before an hours-long make: an unpinnable run cannot
        # produce a valid ledger or bundle manifest.
        raise SystemExit(f"ERROR: cannot read the worktree HEAD of {root}; "
                         f"re-run with --no-ledger to build without a run record")
    stage_plan = [s for s in ("apply", "hdl_make") if stage_selected(args, "hdl")]
    stage_plan += [s for s in ("kernel", "dt", "r5", "boot_assembly")
                   if stage_selected(args, "linux")]
    if args.stage == "dt":
        stage_plan.append("dt")
    stage_plan.append("gates")
    if stage_selected(args, "linux") or args.stage == "dt":
        # Resolve the Vitis slot BEFORE the hours-long HDL make: the Linux half
        # cannot start without it, and finding that out four hours in is a
        # wholly avoidable way to lose an evening. dt needs the same slot --
        # dtc comes from it (trap E-DTB1) -- so it resolves here too.
        ctx["vitis_settings"] = vitis_settings_for(recipe["toolchain"]["vitis"])
        print(f"  vitis:     {ctx['vitis_settings']}")
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

    produced, hdl, boot_block = [], {}, None
    try:
        if stage_selected(args, "hdl"):
            hdl = execute_hdl(recipe, ctx, ledger)
            produced += hdl["produced"]
        if stage_selected(args, "linux"):
            no_os_tree = hdl.get("no_os_tree") or ensure_no_os_tree(
                project_dir, recipe_abs, ledger)
            banked, boot_block = execute_linux(
                recipe, ctx, ledger, xsa=hdl.get("xsa"),
                xsa_sha=hdl.get("xsa_sha"), no_os_tree=no_os_tree)
            produced += [a for a in (ledger.artifact(p, root) for p in banked) if a]
        if args.stage == "dt":
            # Re-entry at dt alone. Deliberately NOT routed through
            # execute_linux: that function's contract is the four-stage Linux
            # half, and the whole point here is to fly one stage without the
            # three that would rebuild artifacts the board is still booting.
            # The bundle this writes is a SUBSET deploy set -- one
            # device-tree-blob -- which build-output.schema.json permits by
            # design ("a stage-scoped run carries the subset it produced").
            # A subset bundle cannot bind a rootfs on its own: provision-rootfs
            # needs BOOT.BIN + Image + DTB together, so pair this with the
            # bundle that carries the other five.
            produced += [a for a in
                         (ledger.artifact(p, root)
                          for p in [stage_dt(recipe, ctx, ledger)]) if a]
    except StageFail as exc:
        ledger.emit("run_done", status="fail", elapsed_s=ledger.elapsed() or 0,
                    summary=ledger.summary(), detail=str(exc)[:400])
        raise SystemExit(f"ERROR: {exc}")

    ledger.emit("stage_start", stage="gates")
    prefer_dir = os.path.dirname(os.path.relpath(recipe_abs, root))
    baseline_rows = compare_baselines(ledger, produced, root, prefer_dir,
                                      deploy_dir=ctx["deploy"])
    summary = ledger.summary()
    # Two vocabularies, deliberately kept apart: stages and runs are ok|fail,
    # gate/bundle verdicts are pass|fail.
    status = "fail" if summary["gates_failed"] else "ok"
    verdict = "fail" if summary["gates_failed"] else "pass"
    ledger.emit("stage_done", stage="gates", status=status,
                elapsed_s=ledger.elapsed() or 0)
    ledger.emit("run_done", status=status, elapsed_s=ledger.elapsed() or 0,
                summary=summary)
    if ledger.enabled:
        write_build_output(root, run_label, recipe, recipe_abs, recipe_sha,
                           ledger, produced, baseline_rows, verdict, args.stage,
                           boot_block)
    else:
        # build-output.schema.json requires a ledger pointer, and every claim in
        # the bundle is meant to be derivable from it. Writing one that names a
        # file this run deliberately did not produce would be a declared
        # contract nobody can execute.
        print("  bundle manifest skipped (--no-ledger: nothing to derive it from)")
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
