#!/usr/bin/env python3
"""
SOCKS job-slot governor -- weighted admission control for Vivado jobs.

Why: a single machine-wide advisory flock only ever serialized *build*
class jobs, and every caller copy-pasted its own flock snippet. Sim-class
jobs (xsim/xelab/xvhdl) were entirely unguarded, so a 3-hour single-core
testbench could run while more jobs piled on top of the same box, and an
operator-killed holder wedged the queue behind a stale lock.

This is a no-daemon governor. State is a directory of PID-stamped slot
files under ${SOCKS_JOBS_DIR:-/tmp/socks-jobs}, mutated only while holding
one flock'd meta lock (.meta.lock). Every scan reclaims slots whose owner
PID is dead, so a killed job cannot wedge the queue.

Two classes, independent budgets, weights denominated in "cores":

    build  weight 8/job   budget $SOCKS_BUILD_BUDGET (default 16)
           -> 2 concurrent Vivado synth/impl/OOC runs. The cap is set by
              RAM, not cores: a synth+impl run peaks around 10-12 GB, so
              three at once thrash a 30 GB box.
    sim    weight 2/job   budget $SOCKS_SIM_BUDGET (default nproc - 4)
           -> ~10 concurrent default-weight xsim shards, leaving 4 cores
              for the interactive session and the OS.

Reentrancy: if SOCKS_JOB_HELD names a class, `run` execs the command
directly without acquiring anything -- a sharded runner that already holds
one slot may fan its shards out inside it. `run` exports SOCKS_JOB_HELD to
the child whenever it does acquire.

Back-compat: `run --class build` also takes the legacy advisory flock on
${SOCKS_BUILD_CLASS_LOCK:-/tmp/socks-build-class.lock} for the child's
lifetime — SHARED among new-style jobs (so the weighted budget, not the
flock, sets build concurrency) but still mutually exclusive against an
old-style caller's exclusive flock while callers migrate.

Second-build RAM gate (operator ruling 2026-08-13): the budget admits 2
concurrent builds, but the 2nd+ build job is only admitted when
MemAvailable >= ${SOCKS_BUILD_RAM_MIN_GB:-24} GB, re-checked every poll.
The first build is never RAM-gated (it must always be able to run).

Usage:
    socks_jobs.py run --class sim --weight 2 --label mytb -- xsim ...
    socks_jobs.py status
    socks_jobs.py wait --class build --timeout 900
"""

import argparse
import errno
import fcntl
import json
import os
import signal
import subprocess
import sys
import time

CLASSES = ("sim", "build")
DEFAULT_WEIGHT = {"sim": 2, "build": 8}
POLL_SECONDS = 5
WAIT_MESSAGE_SECONDS = 60
EX_TEMPFAIL = 75


# --------------------------------------------------------------------------
# paths / config
# --------------------------------------------------------------------------

def jobs_dir() -> str:
    d = os.environ.get("SOCKS_JOBS_DIR") or "/tmp/socks-jobs"
    os.makedirs(d, exist_ok=True)
    return d


def legacy_lock_path() -> str:
    return os.environ.get("SOCKS_BUILD_CLASS_LOCK") or "/tmp/socks-build-class.lock"


def default_sim_budget() -> int:
    try:
        n = len(os.sched_getaffinity(0))
    except AttributeError:  # pragma: no cover - non-Linux fallback
        n = os.cpu_count() or 4
    return max(1, n - 4)


def budget_for(cls: str) -> int:
    if cls == "build":
        raw = os.environ.get("SOCKS_BUILD_BUDGET")
        return int(raw) if raw else 16
    raw = os.environ.get("SOCKS_SIM_BUDGET")
    return int(raw) if raw else default_sim_budget()


def build_ram_min_gb() -> float:
    raw = os.environ.get("SOCKS_BUILD_RAM_MIN_GB")
    return float(raw) if raw else 24.0


def mem_available_gb() -> float:
    """MemAvailable from /proc/meminfo, in GiB. Returns +inf if unreadable
    (non-Linux / test envs) so the gate fails open rather than wedging."""
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / (1024.0 * 1024.0)
    except (OSError, ValueError, IndexError):
        pass
    return float("inf")


# --------------------------------------------------------------------------
# meta lock + slot scanning
# --------------------------------------------------------------------------

class MetaLock:
    """Exclusive flock over <jobs_dir>/.meta.lock. All slot mutation runs
    inside this, so admission decisions are atomic across processes."""

    def __init__(self, directory: str):
        self.path = os.path.join(directory, ".meta.lock")
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o666)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)
        self.fd = None
        return False


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        return False
    return True


def scan_slots(directory: str):
    """Return (live_slots, reclaimed_count). MUST be called under MetaLock.

    Any slot whose owning PID is gone is deleted here -- this is the fix for
    operator-killed jobs wedging the queue.
    """
    live = []
    reclaimed = 0
    for name in sorted(os.listdir(directory)):
        if not (name.startswith("slot-") and name.endswith(".json")):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path) as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            try:
                os.unlink(path)
                reclaimed += 1
            except OSError:
                pass
            continue
        rec["path"] = path
        if not pid_alive(int(rec.get("pid", -1))):
            try:
                os.unlink(path)
                reclaimed += 1
            except OSError:
                pass
            continue
        live.append(rec)
    return live, reclaimed


def used_weight(slots, cls: str) -> int:
    return sum(int(s.get("weight", 0)) for s in slots if s.get("class") == cls)


def sanitize_cmd(cmd):
    """Record only argv[0..2], with argv[0] reduced to its basename, so the
    slot file never carries a full path or a long secret-bearing argv."""
    out = []
    for i, tok in enumerate(cmd[:3]):
        out.append(os.path.basename(tok) if i == 0 else str(tok)[:64])
    return out


def write_slot(directory, cls, weight, label, cmd):
    nonce = os.urandom(4).hex()
    name = f"slot-{cls}-{os.getpid()}-{nonce}.json"
    path = os.path.join(directory, name)
    rec = {
        "pid": os.getpid(),
        "class": cls,
        "weight": int(weight),
        "label": label or "",
        "cmd": sanitize_cmd(cmd or []),
        "started": time.time(),
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(rec, fh)
    os.replace(tmp, path)
    return path


def describe(slots, cls):
    now = time.time()
    parts = []
    for s in slots:
        if s.get("class") != cls:
            continue
        age = int(now - float(s.get("started", now)))
        tag = s.get("label") or " ".join(s.get("cmd") or []) or "?"
        parts.append(f"pid {s['pid']} w{s['weight']} {tag} {age}s")
    return "; ".join(parts) or "(none)"


# --------------------------------------------------------------------------
# acquisition
# --------------------------------------------------------------------------

class LegacyLock:
    """Advisory flock on the legacy build-class lock file, held for the
    child's lifetime. New-style jobs take it SHARED so they run
    concurrently under the weighted budget; a pre-migration caller's
    exclusive flock still excludes us (and ours excludes it)."""

    def __init__(self):
        self.fd = None

    def try_acquire(self) -> bool:
        fd = os.open(legacy_lock_path(), os.O_RDWR | os.O_CREAT, 0o666)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self.fd = fd
        return True

    def release(self):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


def queue_snapshot(directory, cls) -> str:
    with MetaLock(directory):
        slots, _ = scan_slots(directory)
    b = budget_for(cls)
    return (f"{cls}: {used_weight(slots, cls)}/{b} cores in use -- "
            f"{describe(slots, cls)}")


def acquire(directory, cls, weight, label, cmd, timeout, take_legacy,
            reserve=True, stream=sys.stderr):
    """Block until admission succeeds. Returns (slot_path, legacy_lock).

    reserve=False performs the same wait but takes no slot (the `wait`
    subcommand) -- it still round-trips the meta lock so stale slots get
    reclaimed while it polls.
    """
    start = time.time()
    deadline = (start + timeout) if timeout else None
    last_msg = 0.0
    legacy = LegacyLock() if take_legacy else None
    budget = budget_for(cls)

    if weight > budget:
        print(f"ERROR: requested weight {weight} exceeds the {cls} budget "
              f"{budget}; it could never be admitted.", file=stream)
        sys.exit(2)

    while True:
        slot_path = None
        blocked = None
        with MetaLock(directory):
            slots, _ = scan_slots(directory)
            used = used_weight(slots, cls)
            if used + weight > budget:
                blocked = (f"{used}/{budget} cores in use, need {weight}; "
                           f"holders: {describe(slots, cls)}")
            elif cls == "build" and any(s.get("class") == "build"
                                        for s in slots):
                # Second-build RAM gate: a build is already running; admit
                # another only if the box has headroom for it right now.
                avail = mem_available_gb()
                need = build_ram_min_gb()
                if avail < need:
                    blocked = (f"RAM gate: MemAvailable {avail:.1f} GiB < "
                               f"{need:.1f} GiB required for a 2nd "
                               f"concurrent build "
                               f"(SOCKS_BUILD_RAM_MIN_GB overrides)")
                elif reserve:
                    slot_path = write_slot(directory, cls, weight, label, cmd)
                else:
                    slot_path = ""
            else:
                if reserve:
                    slot_path = write_slot(directory, cls, weight, label, cmd)
                else:
                    slot_path = ""

        if slot_path is not None and legacy is not None:
            if not legacy.try_acquire():
                if slot_path:
                    with MetaLock(directory):
                        try:
                            os.unlink(slot_path)
                        except OSError:
                            pass
                slot_path = None
                blocked = (f"legacy build-class lock {legacy_lock_path()} "
                           f"is held by an old-style job")

        if slot_path is not None:
            waited = int(time.time() - start)
            if reserve:
                print(f"[socks-jobs] acquired {cls} slot w{weight} "
                      f"(budget {budget}) after {waited}s", file=stream)
            return slot_path, legacy

        now = time.time()
        if last_msg == 0.0 or (now - last_msg) >= WAIT_MESSAGE_SECONDS:
            print(f"[socks-jobs] waiting for {cls} slot: {blocked}",
                  file=stream)
            last_msg = now

        if deadline is not None and now >= deadline:
            print(f"[socks-jobs] TIMEOUT after {int(now - start)}s waiting "
                  f"for a {cls} slot", file=stream)
            print(f"[socks-jobs] queue: {queue_snapshot(directory, cls)}",
                  file=stream)
            sys.exit(EX_TEMPFAIL)

        nap = POLL_SECONDS
        if deadline is not None:
            nap = max(0.1, min(nap, deadline - now))
        time.sleep(nap)


def release(directory, slot_path, legacy):
    if slot_path:
        with MetaLock(directory):
            try:
                os.unlink(slot_path)
            except OSError:
                pass
    if legacy is not None:
        legacy.release()


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------

def cmd_run(args) -> int:
    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("ERROR: no command given (use -- CMD ARGS...)", file=sys.stderr)
        return 2

    # Reentrancy: an outer wrapper already holds a slot for us.
    if os.environ.get("SOCKS_JOB_HELD"):
        os.execvp(cmd[0], cmd)

    directory = jobs_dir()
    weight = args.weight or DEFAULT_WEIGHT[args.cls]
    slot_path, legacy = acquire(directory, args.cls, weight, args.label, cmd,
                                args.timeout, take_legacy=(args.cls == "build"))

    env = dict(os.environ)
    env["SOCKS_JOB_HELD"] = args.cls

    child = subprocess.Popen(cmd, env=env, start_new_session=True)
    state = {"signal": None}

    def forward(signum, _frame):
        state["signal"] = signum
        try:
            os.killpg(os.getpgid(child.pid), signum)
        except OSError:
            pass

    signal.signal(signal.SIGINT, forward)
    signal.signal(signal.SIGTERM, forward)

    try:
        while True:
            try:
                rc = child.wait()
                break
            except KeyboardInterrupt:  # pragma: no cover - belt and braces
                continue
    finally:
        release(directory, slot_path, legacy)

    if rc < 0:
        return 128 + (-rc)
    return rc


def cmd_status(args) -> int:
    directory = jobs_dir()
    with MetaLock(directory):
        slots, reclaimed = scan_slots(directory)
    now = time.time()
    print(f"SOCKS job slots  dir={directory}")
    print(f"{'CLASS':<7} {'BUDGET':>6} {'USED':>5} {'PID':>8} {'W':>3} "
          f"{'AGE':>6}  LABEL")
    for cls in CLASSES:
        b = budget_for(cls)
        used = used_weight(slots, cls)
        rows = [s for s in slots if s.get("class") == cls]
        if not rows:
            print(f"{cls:<7} {b:>6} {used:>5} {'-':>8} {'-':>3} {'-':>6}  -")
            continue
        for i, s in enumerate(rows):
            age = int(now - float(s.get("started", now)))
            label = s.get("label") or " ".join(s.get("cmd") or [])
            print(f"{cls if i == 0 else '':<7} {b if i == 0 else '':>6} "
                  f"{used if i == 0 else '':>5} {s['pid']:>8} "
                  f"{s['weight']:>3} {str(age) + 's':>6}  {label}")
    print(f"reclaimed-stale: {reclaimed}")
    return 0


def cmd_wait(args) -> int:
    directory = jobs_dir()
    weight = args.weight or DEFAULT_WEIGHT[args.cls]
    acquire(directory, args.cls, weight, args.label, [], args.timeout,
            take_legacy=False, reserve=False)
    print(f"[socks-jobs] {args.cls} class has room for w{weight}")
    return 0


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="socks_jobs.py",
        description="Weighted job-slot governor for Vivado build/sim jobs.",
        epilog=(
            "Budgets (cores): build = $SOCKS_BUILD_BUDGET (default 16) at "
            "weight 8/job -> 2 concurrent; a 2nd concurrent build is also "
            "gated on MemAvailable >= $SOCKS_BUILD_RAM_MIN_GB (default 24) "
            "GiB, re-checked every poll. sim = $SOCKS_SIM_BUDGET (default "
            "nproc-4) at weight 2/job. State dir: $SOCKS_JOBS_DIR (default "
            "/tmp/socks-jobs). Set SOCKS_JOB_HELD=<class> to short-circuit "
            "acquisition inside a job that already holds a slot."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="subcmd", required=True)

    r = sub.add_parser("run", help="acquire a slot, run CMD, release")
    r.add_argument("--class", dest="cls", choices=CLASSES, required=True)
    r.add_argument("--weight", type=int, default=0,
                   help="cores to charge (default: sim 2, build 8)")
    r.add_argument("--timeout", type=int, default=0,
                   help="seconds to wait for admission; 0 = forever. "
                        "A nonzero timeout exits 75 (EX_TEMPFAIL).")
    r.add_argument("--label", type=str, default="",
                   help="short tag shown in status/waiting lines")
    r.add_argument("cmd", nargs=argparse.REMAINDER,
                   metavar="-- CMD ARGS...")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("status", help="show budgets and live jobs")
    s.set_defaults(func=cmd_status)

    w = sub.add_parser("wait",
                       help="block until admission would succeed; take nothing")
    w.add_argument("--class", dest="cls", choices=CLASSES, required=True)
    w.add_argument("--weight", type=int, default=0)
    w.add_argument("--timeout", type=int, default=0)
    w.add_argument("--label", type=str, default="")
    w.set_defaults(func=cmd_wait)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
