#!/usr/bin/env python3
"""Tests for fire_codex_worker.py, on a PRIVATE tmux server (`tmux -L`), never
the operator's. Run as a module: `python3 -m unittest test_fire_codex_worker -v`."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIRE = HERE / "fire_codex_worker.py"
LAUNCHER = HERE / "launch_codex_worker.py"
sys.path.insert(0, str(HERE))
import mb  # noqa: E402

FAKE_CODEX = """#!/usr/bin/env python3
import json, os, pathlib, sys, time
inbox = pathlib.Path(os.environ["FAKE_INBOX"])
(inbox / "child-argv.json").write_text(json.dumps(sys.argv[1:]))
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
"""


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class FireOnPrivateTmux(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mbfire-"))
        self.repo = self.tmp / "worker"
        self.repo.mkdir()
        git = ["git", "-C", str(self.repo)]
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(git + ["config", "user.name", "Test"], check=True)
        subprocess.run(git + ["config", "user.email", "test@example.invalid"], check=True)
        (self.repo / "source.txt").write_text("baseline\n")
        subprocess.run(git + ["add", "source.txt"], check=True)
        subprocess.run(git + ["commit", "-qm", "baseline"], check=True)
        self.branch = subprocess.check_output(git + ["branch", "--show-current"], text=True).strip()
        self.head = subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip()
        self.inbox = self.repo / "codex-handoff" / "plan-test-fire"
        self.inbox.mkdir(parents=True)
        fake = self.repo / "fake-codex"
        fake.write_text(FAKE_CODEX)
        fake.chmod(0o755)
        (self.inbox / "turn1.md").write_text("Execute the plan.\n")
        (self.inbox / "env.sh").write_text(
            f"export FAKE_INBOX={shlex.quote(str(self.inbox))}\nexport FAKE_SLEEP=6\n")
        self._write_fire_sh(self.head)

        self.sock = f"mbfire-{os.getpid()}-{self._testMethodName[-10:]}"
        self.env = dict(os.environ, MB_TMUX_SOCKET=self.sock)
        self.env.pop("TMUX", None)
        self.env.pop("TMUX_PANE", None)
        self.addCleanup(self._teardown)
        self.orch = self._tmux("new-session", "-d", "-s", "t", "-x", "250", "-y", "50",
                               "-n", "orch", "-P", "-F", "#{pane_id}", "cat")

    def _write_fire_sh(self, expected_head: str):
        q = shlex.quote
        (self.inbox / "fire.sh").write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            f"cd {q(str(self.repo))}\nsource codex-handoff/plan-test-fire/env.sh\n"
            f"exec {q(sys.executable)} {q(str(LAUNCHER))} launch --inbox {q(str(self.inbox))} "
            f"--thread-id fpga/20260101-test-fire --plan-id plan-test-fire "
            f"--expected-branch {q(self.branch)} --expected-head {q(expected_head)} "
            f"--model gpt-test --reasoning-effort high --auto-compact-token-limit 300000 "
            f"--turn1-file {q(str(self.inbox / 'turn1.md'))} --codex-bin {q(str(self.repo / 'fake-codex'))}\n")

    def _tmux(self, *args: str) -> str:
        out = subprocess.run(["tmux", "-L", self.sock, *args], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def _teardown(self):
        subprocess.run(["tmux", "-L", self.sock, "kill-server"], capture_output=True)
        base = Path(os.environ.get("TMUX_TMPDIR", "/tmp")) / f"tmux-{os.getuid()}"
        (base / self.sock).unlink(missing_ok=True)
        time.sleep(0.3)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fire(self, *extra: str, env: dict | None = None):
        return subprocess.run([sys.executable, str(FIRE), "--inbox", str(self.inbox),
                               "--orchestrator-pane", self.orch, *extra],
                              capture_output=True, text=True, env=env or self.env)

    def _wait(self, predicate, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.1)
        return predicate()

    def _launched(self) -> bool:
        try:
            state = json.loads((self.inbox / "worker-state.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        return any(e["event"] == "WORKER_LAUNCHED" for e in state["events"])

    def test_fire_opens_a_detached_window_launches_the_worker_and_claims_both_panes(self):
        out = self._fire()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), f"FIRE_REQUESTED {self.inbox.resolve()}")
        self.assertTrue(self._wait(self._launched), "worker never reached WORKER_LAUNCHED")
        self.assertIn("plan-test-fire", self._tmux("list-windows", "-t", "t", "-F", "#{window_name}"))
        self.assertEqual(self._tmux("display-message", "-p", "-t", "t", "#{window_name}"), "orch",
                         "fire must not move the operator's focus")
        claims = mb.newest_claims(mb.read_blocks(self.inbox / "mailbox.md"))
        self.assertEqual(claims["orchestrator"], self.orch)
        self.assertRegex(claims["worker"], r"^%\d+$")
        self.assertNotEqual(claims["worker"], self.orch)
        argv = json.loads((self.inbox / "child-argv.json").read_text())
        self.assertIn(str(self.inbox / "turn1.md"), argv[-1])
        self.assertTrue(self._wait(lambda: "RELAY_START" in (self.inbox / "relay.log").read_text()
                                   if (self.inbox / "relay.log").exists() else False))

    def test_a_block_sent_after_fire_pings_the_orchestrator_pane_through_the_fired_relay(self):
        self.assertEqual(self._fire().returncode, 0)
        self.assertTrue(self._wait(self._launched))
        self.assertTrue(self._wait(lambda: (self.inbox / "relay.log").exists()))
        n = mb.send(self.inbox / "mailbox.md", "worker", "orchestrator", "QUESTION", "which reading?")
        want = f"MAILBOX {n} {(self.inbox / 'mailbox.md').resolve()}"
        self.assertTrue(self._wait(lambda: want in self._tmux("capture-pane", "-p", "-t", self.orch)))

    def test_second_fire_while_the_worker_is_live_is_refused(self):
        self.assertEqual(self._fire().returncode, 0)
        self.assertTrue(self._wait(self._launched))
        again = self._fire()
        self.assertEqual(again.returncode, 2)
        self.assertIn("refusing duplicate live worker", again.stderr)

    def test_launcher_refusal_is_recorded_in_a_file_and_no_state_is_written(self):
        self._write_fire_sh("0" * 40)                      # HEAD mismatch: launcher refuses
        self.assertEqual(self._fire().returncode, 0)       # the fire itself was requested
        failed = self.inbox / "fire-failed.log"
        self.assertTrue(self._wait(failed.exists), "launcher refusal was not recorded")
        self.assertIn("rc=", failed.read_text())
        self.assertFalse((self.inbox / "worker-state.json").exists())

    def test_refusals_open_no_window(self):
        def windows() -> int:
            return len(self._tmux("list-windows", "-a", "-F", "#{window_id}").splitlines())
        before = windows()
        no_tmux = {k: v for k, v in self.env.items() if k != "MB_TMUX_SOCKET"}
        out = self._fire(env=no_tmux)
        self.assertEqual(out.returncode, 2)
        self.assertIn("fire by hand", out.stderr)
        out = subprocess.run([sys.executable, str(FIRE), "--inbox", str(self.inbox)],
                             capture_output=True, text=True, env=self.env)   # no pane id anywhere
        self.assertEqual(out.returncode, 2)
        self.assertIn("no orchestrator pane id", out.stderr)
        for name in ("fire.sh", "turn1.md"):
            moved = self.inbox / (name + ".away")
            (self.inbox / name).rename(moved)
            out = self._fire()
            self.assertEqual(out.returncode, 2)
            self.assertIn(f"missing {name}", out.stderr)
            moved.rename(self.inbox / name)
        stray = self.repo / "not-an-inbox"
        stray.mkdir()
        out = subprocess.run([sys.executable, str(FIRE), "--inbox", str(stray),
                              "--orchestrator-pane", self.orch],
                             capture_output=True, text=True, env=self.env)
        self.assertEqual(out.returncode, 2)
        self.assertIn("not a packet inbox", out.stderr)
        self.assertEqual(windows(), before)
        self.assertFalse((self.inbox / "worker-state.json").exists())
        self.assertFalse((self.inbox / "mailbox.md").exists())

    def test_preparing_a_packet_never_launches(self):
        """Decision 64 item 1: prepare/emit can write the launch surface but only
        an explicit fire crosses the boundary."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("emit_for_fire_test", HERE / "emit_codex_launch_packet.py")
        emitter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(emitter)
        before = self._tmux("list-windows", "-a", "-F", "#{window_id}")
        (self.inbox / "fire.sh").unlink()
        emitter.write_turn1_file(handback_inbox=self.inbox, packet="```\nturn one\n```\n")
        emitter.write_fire_script(handback_inbox=self.inbox, worktree=self.repo,
                                  plan_id="plan-test-fire", launch_command="true")
        time.sleep(0.5)
        self.assertTrue((self.inbox / "fire.sh").exists())
        self.assertEqual(self._tmux("list-windows", "-a", "-F", "#{window_id}"), before)
        self.assertFalse((self.inbox / "worker-state.json").exists())


if __name__ == "__main__":
    unittest.main()
