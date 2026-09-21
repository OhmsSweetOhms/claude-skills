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
MB = HERE.parent.parent / "mailbox" / "scripts" / "mb.py"
if not MB.is_file():                  # a renamed single-skill trial copy has no sibling
    MB = Path.home() / ".claude" / "skills" / "mailbox" / "scripts" / "mb.py"
sys.path.insert(0, str(MB.parent))
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
        self.sid = f"fire-session-{os.getpid()}"
        self.env = dict(os.environ, FIRE_TMUX_SOCKET=self.sock,
                        CLAUDE_CODE_SESSION_ID=self.sid,
                        XDG_STATE_HOME=str(self.tmp / "state"))
        self.env.pop("TMUX", None)
        self.env.pop("TMUX_PANE", None)
        os.environ["XDG_STATE_HOME"] = self.env["XDG_STATE_HOME"]   # for in-process mb
        self.addCleanup(os.environ.pop, "XDG_STATE_HOME", None)
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
        return subprocess.run([sys.executable, str(FIRE), "--inbox", str(self.inbox), *extra],
                              capture_output=True, text=True, env=env or self.env)

    def _watched(self) -> dict:
        return mb.read_registry(self.sid)["mailboxes"]

    def _server_typed_anything(self) -> list[str]:
        """The tmux server's own message log: every command a client ran. A
        doorbell that types would show up here as send-keys."""
        out = subprocess.run(["tmux", "-L", self.sock, "show-messages"],
                             capture_output=True, text=True)
        return [line for line in out.stdout.splitlines() if "send-keys" in line]

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

    def test_fire_opens_a_detached_window_launches_the_worker_and_arms_the_waiter(self):
        out = self._fire()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), f"FIRE_REQUESTED {self.inbox.resolve()}")
        self.assertTrue(self._wait(self._launched), "worker never reached WORKER_LAUNCHED")
        self.assertIn("plan-test-fire", self._tmux("list-windows", "-t", "t", "-F", "#{window_name}"))
        self.assertEqual(self._tmux("display-message", "-p", "-t", "t", "#{window_name}"), "orch",
                         "fire must not move the operator's focus")
        self.assertEqual(list(self._watched()), [str((self.inbox / "mailbox.md").resolve())],
                         "fire must register the mailbox for this Claude session")
        self.assertEqual(self._server_typed_anything(), [])
        # WORKER_LAUNCHED is recorded BEFORE the launcher releases its gate
        # (launch_codex_worker.py: event, then gate), so the child may not have
        # run yet: wait for its argv file instead of reading it at once.
        argv_file = self.inbox / "child-argv.json"
        self.assertTrue(self._wait(lambda: argv_file.exists() and argv_file.stat().st_size > 0))
        argv = json.loads(argv_file.read_text())
        self.assertIn(str(self.inbox / "turn1.md"), argv[-1])
        self.assertFalse((self.inbox / "relay.log").exists(), "fire must start no relay")

    def test_a_block_sent_after_fire_reaches_the_waiter_the_fire_armed(self):
        """No relay, no pane, no keystroke: the block is delivered because fire
        registered the mailbox for this session."""
        self.assertEqual(self._fire().returncode, 0)
        self.assertTrue(self._wait(self._launched))
        n = mb.send(self.inbox / "mailbox.md", "worker", "orchestrator", "QUESTION", "which reading?")
        hook = subprocess.run([sys.executable, str(MB), "wait", "--deadline", "5", "--poll", "0.05"],
                              input=json.dumps({"session_id": self.sid}),
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(hook.returncode, 2, hook.stdout)
        self.assertEqual(hook.stderr.strip(),
                         f"MAILBOX {n} {(self.inbox / 'mailbox.md').resolve()}")
        self.assertEqual(self._server_typed_anything(), [])

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
        self.assertIn("launcher refused:", failed.read_text(),
                      "the launcher's own message must outlive the pane it was printed in")
        self.assertFalse((self.inbox / "worker-state.json").exists())
        # ... and the refusal WAKES the orchestrator instead of waiting to be found.
        box = self.inbox / "mailbox.md"
        self.assertTrue(self._wait(lambda: any(b["kind"] == "FIRE_FAILED" and b["complete"]
                                               for b in mb.read_blocks(box))),
                        "a refused launch sent no FIRE_FAILED block")
        block = [b for b in mb.read_blocks(box) if b["kind"] == "FIRE_FAILED"][0]
        self.assertEqual((block["sender"], block["to"]), ("worker", "orchestrator"))
        self.assertIn("launcher refused:", block["body"])

    def test_refusals_open_no_window(self):
        def windows() -> int:
            return len(self._tmux("list-windows", "-a", "-F", "#{window_id}").splitlines())
        before = windows()
        no_tmux = {k: v for k, v in self.env.items() if k != "FIRE_TMUX_SOCKET"}
        out = self._fire(env=no_tmux)
        self.assertEqual(out.returncode, 2)
        self.assertIn("fire by hand", out.stderr)
        no_session = {k: v for k, v in self.env.items() if k != "CLAUDE_CODE_SESSION_ID"}
        out = self._fire(env=no_session)
        self.assertEqual(out.returncode, 2)
        self.assertIn("no Claude session id", out.stderr)
        for name in ("fire.sh", "turn1.md"):
            moved = self.inbox / (name + ".away")
            (self.inbox / name).rename(moved)
            out = self._fire()
            self.assertEqual(out.returncode, 2)
            self.assertIn(f"missing {name}", out.stderr)
            moved.rename(self.inbox / name)
        stray = self.repo / "not-an-inbox"
        stray.mkdir()
        out = subprocess.run([sys.executable, str(FIRE), "--inbox", str(stray)],
                             capture_output=True, text=True, env=self.env)
        self.assertEqual(out.returncode, 2)
        self.assertIn("not a packet inbox", out.stderr)
        self.assertEqual(windows(), before)
        self.assertFalse((self.inbox / "worker-state.json").exists())
        self.assertEqual(self._watched(), {}, "a refused fire must arm nothing")

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


class RenamedSingleSkillCopy(unittest.TestCase):
    """A trial may be ONE skill copied under another name, with no `mailbox/` beside it.
    The fire script must still import `mb` — from the installed mailbox skill — instead
    of dying at import (`No module named 'mb'`, 2026-09-21)."""

    def test_the_fire_script_imports_from_a_copy_with_no_sibling_mailbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "threads-next" / "scripts"
            copy.mkdir(parents=True)
            for name in ("fire_codex_worker.py", "launch_codex_worker.py"):
                shutil.copy(HERE / name, copy / name)
            self.assertFalse((Path(tmp) / "mailbox").exists())
            run = subprocess.run([sys.executable, str(copy / "fire_codex_worker.py"), "--help"],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertNotIn("No module named", run.stderr)


if __name__ == "__main__":
    unittest.main()
