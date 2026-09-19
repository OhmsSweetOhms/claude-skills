"""The cutover preflight: liveness by the process, never by what the record says."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import cutover_preflight
from launch_codex_worker import process_identity


def departed_identity() -> dict:
    """A real identity whose process has exited and been reaped."""
    child = subprocess.Popen(["sleep", "30"])
    identity = process_identity(child.pid)
    child.kill()
    child.wait()
    return identity


class PreflightTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        self.proc = self.root / "proc"          # an empty /proc: no launcher running
        self.proc.mkdir()

    def record(self, plan: str, lifecycle: str, process: object, worktree: Path | None = None) -> Path:
        inbox = (worktree or self.worktree) / "codex-handoff" / plan
        inbox.mkdir(parents=True)
        path = inbox / "worker-state.json"
        path.write_text(json.dumps({"schema_version": "3", "state": lifecycle, "process": process}))
        return path

    def run_preflight(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cutover_preflight.main(list(argv) or ["--worktree", str(self.worktree)], proc=self.proc)
        return rc, out.getvalue()

    def fake_process(self, pid: int, *argv: str) -> None:
        (self.proc / str(pid)).mkdir()
        (self.proc / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")

    def test_a_completed_worker_whose_process_lives_refuses(self):
        # The hop-12 shape: lifecycle says it is over, the TUI is still there.
        self.record("plan-a", "completed",
                    {"state": "running", "worker_identity": process_identity(os.getpid())})
        rc, out = self.run_preflight()
        self.assertEqual(rc, 1)
        self.assertIn(f"LIVE {self.worktree}", out)
        self.assertIn("CUTOVER_REFUSED live=1 unreadable=0", out)

    def test_a_running_record_with_a_dead_pid_is_gone(self):
        # The tracked socks record's shape: running/running, and nobody home.
        self.record("plan-a", "running", {"state": "running", "worker_identity": departed_identity()})
        rc, out = self.run_preflight()
        self.assertEqual(rc, 0)
        self.assertIn("CUTOVER_CLEAR records=1 gone=1 no_identity=0", out)

    def test_a_recycled_pid_is_gone(self):
        mine = process_identity(os.getpid())
        self.record("plan-a", "running",
                    {"state": "running", "worker_identity": {**mine, "start_ticks": mine["start_ticks"] - 1}})
        self.assertEqual(self.run_preflight()[0], 0)

    def test_a_live_launcher_identity_alone_refuses(self):
        self.record("plan-a", "launching",
                    {"state": "starting", "worker_identity": None,
                     "launcher_identity": process_identity(os.getpid())})
        self.assertEqual(self.run_preflight()[0], 1)

    def test_a_record_with_no_identity_is_named_and_does_not_refuse(self):
        self.record("plan-a", "gate-incomplete", None)
        rc, out = self.run_preflight()
        self.assertEqual(rc, 0)
        self.assertIn("NO_IDENTITY", out)
        self.assertIn("no_identity=1", out)

    def test_an_unreadable_record_refuses(self):
        path = self.record("plan-a", "completed", None)
        path.write_text("{ not json")
        rc, out = self.run_preflight()
        self.assertEqual(rc, 1)
        self.assertIn("CUTOVER_REFUSED live=0 unreadable=1", out)

    def test_a_running_launcher_refuses_with_no_record_at_all(self):
        self.fake_process(4242, "python3", "/skills/threads/scripts/launch_codex_worker.py",
                          "launch", "--inbox", "/somewhere")
        rc, out = self.run_preflight()
        self.assertEqual(rc, 1)
        self.assertIn("LIVE_LAUNCHER 4242", out)
        self.assertIn("CUTOVER_REFUSED live=1", out)

    def test_other_launcher_verbs_and_other_processes_are_not_workers(self):
        self.fake_process(11, "python3", "/skills/threads/scripts/launch_codex_worker.py", "bind-session")
        self.fake_process(12, "codex", "--model", "x")
        self.fake_process(13, "vim", "launch_codex_worker.py")
        self.assertEqual(cutover_preflight.live_launchers(self.proc), [])
        self.assertEqual(self.run_preflight()[0], 0)

    def test_repo_searches_every_git_worktree(self):
        repo = self.root / "repo"
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        def git(*args):
            subprocess.run(["git", *args], check=True, capture_output=True, env=env)
        git("init", "-q", str(repo))
        git("-C", str(repo), "commit", "-q", "--allow-empty", "-m", "base")
        sibling = self.root / "sibling"
        git("-C", str(repo), "worktree", "add", "-q", "-b", "side", str(sibling))
        self.record("plan-b", "completed",
                    {"state": "running", "worker_identity": process_identity(os.getpid())},
                    worktree=sibling)
        rc, out = self.run_preflight("--repo", str(repo))
        self.assertEqual(rc, 1)
        self.assertIn("SEARCHED worktrees=2 records=1", out)

    def test_searching_nowhere_is_refused(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cutover_preflight.main([], proc=self.proc)


if __name__ == "__main__":
    unittest.main()
