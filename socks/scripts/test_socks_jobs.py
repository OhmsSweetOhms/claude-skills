#!/usr/bin/env python3
"""
Self-test for scripts/socks_jobs.py -- stdlib unittest, no Vivado needed.

Covers the five behaviours the governor exists for:
  * admission arithmetic against a class budget
  * stale reclaim after a holder is SIGKILLed (the wedged-queue fix)
  * SOCKS_JOB_HELD reentrancy short-circuit
  * SIGTERM on the wrapper releases the slot
  * legacy /tmp/socks-build-class.lock flock still excludes new-style builds

Run: python3 test_socks_jobs.py   (completes in well under 60 s)
"""

import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
JOBS = os.path.join(HERE, "socks_jobs.py")
sys.path.insert(0, HERE)

import socks_jobs  # noqa: E402


def slot_files(directory):
    return [f for f in os.listdir(directory)
            if f.startswith("slot-") and f.endswith(".json")]


def wait_for(pred, timeout=10.0, interval=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return False


class JobsTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "jobs")
        os.makedirs(self.dir)
        self.legacy = os.path.join(self.tmp.name, "build-class.lock")
        self.env = dict(os.environ)
        self.env.pop("SOCKS_JOB_HELD", None)
        self.env["SOCKS_JOBS_DIR"] = self.dir
        self.env["SOCKS_BUILD_CLASS_LOCK"] = self.legacy
        self.env["SOCKS_SIM_BUDGET"] = "20"
        self.env["SOCKS_BUILD_BUDGET"] = "16"
        self.procs = []
        self.inner_pidfiles = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except OSError:
                    pass
                p.wait(timeout=10)
            if p.stdout is not None:
                p.stdout.close()
        # `run` puts the child in its own session, so killing the wrapper's
        # group never reaches it -- reap recorded inner pids explicitly.
        for pf in self.inner_pidfiles:
            try:
                with open(pf) as fh:
                    os.kill(int(fh.read().strip()), signal.SIGKILL)
            except (OSError, ValueError):
                pass
        self.tmp.cleanup()

    def spawn(self, *args, **kw):
        kw.setdefault("stdout", subprocess.DEVNULL)
        kw.setdefault("stderr", subprocess.DEVNULL)
        p = subprocess.Popen([sys.executable, JOBS] + list(args),
                             env=kw.pop("env", self.env),
                             start_new_session=True, **kw)
        self.procs.append(p)
        return p

    def sleeper(self, seconds=60):
        """A long-running child that records its own pid, so the test can
        reap it even after the wrapper above it is SIGKILLed."""
        pidfile = os.path.join(self.tmp.name, f"inner-{len(self.inner_pidfiles)}.pid")
        self.inner_pidfiles.append(pidfile)
        return [sys.executable, "-c",
                "import os,sys,time;"
                "open(sys.argv[1],'w').write(str(os.getpid()));"
                "time.sleep(float(sys.argv[2]))",
                pidfile, str(seconds)]

    def run_sync(self, *args, **kw):
        return subprocess.run([sys.executable, JOBS] + list(args),
                              env=kw.pop("env", self.env),
                              capture_output=True, text=True, **kw)


class TestAdmissionArithmetic(JobsTestBase):
    def test_used_weight_sums_only_its_class(self):
        slots = [{"class": "sim", "weight": 8}, {"class": "sim", "weight": 8},
                 {"class": "build", "weight": 8}]
        self.assertEqual(socks_jobs.used_weight(slots, "sim"), 16)
        self.assertEqual(socks_jobs.used_weight(slots, "build"), 8)

    def test_third_heavy_sim_is_refused_on_budget_20(self):
        a = self.spawn("run", "--class", "sim", "--weight", "8", "--",
                       *self.sleeper())
        b = self.spawn("run", "--class", "sim", "--weight", "8", "--",
                       *self.sleeper())
        self.assertTrue(wait_for(lambda: len(slot_files(self.dir)) == 2),
                        "two 8-core sims should be admitted on a budget of 20")
        r = self.run_sync("wait", "--class", "sim", "--weight", "8",
                          "--timeout", "2")
        self.assertEqual(r.returncode, 75, r.stderr)
        self.assertIn("16/20 cores in use", r.stderr)
        # ...but a light job still fits in the remaining 4 cores.
        r2 = self.run_sync("wait", "--class", "sim", "--weight", "2",
                           "--timeout", "2")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        for p in (a, b):
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)

    def test_weight_over_budget_is_a_hard_error(self):
        r = self.run_sync("wait", "--class", "sim", "--weight", "99",
                          "--timeout", "2")
        self.assertEqual(r.returncode, 2, r.stderr)


class TestStaleReclaim(JobsTestBase):
    def test_sigkilled_holder_is_reclaimed_on_next_scan(self):
        p = self.spawn("run", "--class", "sim", "--", *self.sleeper())
        self.assertTrue(wait_for(lambda: len(slot_files(self.dir)) == 1))
        name = slot_files(self.dir)[0]
        with open(os.path.join(self.dir, name)) as fh:
            rec = json.load(fh)
        self.assertEqual(rec["pid"], p.pid)
        self.assertEqual(rec["weight"], 2)

        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        p.wait(timeout=10)
        # SIGKILL cannot be trapped: the slot file outlives its owner.
        self.assertEqual(len(slot_files(self.dir)), 1)

        r = self.run_sync("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("status is read-only", r.stdout)
        self.assertEqual(len(slot_files(self.dir)), 1)

        # The next host admission scan owns stale reclamation.
        r = self.run_sync("wait", "--class", "sim", "--weight", "2",
                          "--timeout", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(slot_files(self.dir), [])


class TestReentrancy(JobsTestBase):
    def test_socks_job_held_execs_without_taking_a_slot(self):
        env = dict(self.env)
        env["SOCKS_JOB_HELD"] = "sim"
        r = subprocess.run(
            [sys.executable, JOBS, "run", "--class", "sim", "--",
             sys.executable, "-c", "print('inner-ran')"],
            env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("inner-ran", r.stdout)
        self.assertEqual(slot_files(self.dir), [])

    def test_acquiring_exports_the_env_to_the_child(self):
        r = self.run_sync("run", "--class", "sim", "--",
                          sys.executable, "-c",
                          "import os; print(os.environ['SOCKS_JOB_HELD'])")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "sim")
        self.assertEqual(slot_files(self.dir), [])


class TestSignalRelease(JobsTestBase):
    def test_sigterm_releases_the_slot(self):
        p = self.spawn("run", "--class", "sim", "--", *self.sleeper())
        self.assertTrue(wait_for(lambda: len(slot_files(self.dir)) == 1))
        p.send_signal(signal.SIGTERM)
        rc = p.wait(timeout=15)
        self.assertEqual(rc, 128 + signal.SIGTERM)
        self.assertTrue(wait_for(lambda: slot_files(self.dir) == []),
                        "slot must be released when the wrapper is SIGTERMed")

    def test_child_exit_code_is_propagated(self):
        r = self.run_sync("run", "--class", "sim", "--",
                          sys.executable, "-c", "raise SystemExit(7)")
        self.assertEqual(r.returncode, 7, r.stderr)


class TestLegacyLock(JobsTestBase):
    def test_old_style_flock_holder_blocks_a_new_style_build(self):
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import fcntl,sys,time;"
             "fd=open(sys.argv[1],'w');"
             "fcntl.flock(fd, fcntl.LOCK_EX);"
             "print('held', flush=True);"
             "time.sleep(60)",
             self.legacy],
            stdout=subprocess.PIPE, text=True, start_new_session=True)
        self.procs.append(holder)
        self.assertEqual(holder.stdout.readline().strip(), "held")

        r = self.run_sync("run", "--class", "build", "--timeout", "3", "--",
                          "sleep", "1")
        self.assertEqual(r.returncode, 75, r.stderr)
        self.assertIn("legacy build-class lock", r.stderr)
        # the refused job must not leave a slot behind
        self.assertEqual(slot_files(self.dir), [])

        os.killpg(os.getpgid(holder.pid), signal.SIGKILL)
        holder.wait(timeout=10)
        r2 = self.run_sync("run", "--class", "build", "--timeout", "10", "--",
                           "sleep", "0.1")
        self.assertEqual(r2.returncode, 0, r2.stderr)

    def test_new_style_build_holds_the_legacy_lock_for_child_lifetime(self):
        p = self.spawn("run", "--class", "build", "--", *self.sleeper())
        self.assertTrue(wait_for(lambda: len(slot_files(self.dir)) == 1))
        fd = open(self.legacy, "w")
        with self.assertRaises(OSError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.close()
        os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        p.wait(timeout=15)


class TestStatus(JobsTestBase):
    def test_clean_dir_status_is_empty_and_exit_zero(self):
        r = self.run_sync("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("reclaimed-stale: 0 (status is read-only", r.stdout)
        for cls in ("sim", "build"):
            self.assertRegex(r.stdout, rf"(?m)^{cls}\s")


if __name__ == "__main__":
    unittest.main(verbosity=2)
