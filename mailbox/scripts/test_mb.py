#!/usr/bin/env python3
"""Tests for mb.py. Nothing here spends a real Codex turn: the doorbell runs a
FAKE `codex` binary through MB_CODEX_BIN, and the watch registry is redirected
with XDG_STATE_HOME. Run as a module: `python3 -m unittest test_mb -v`."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MB = HERE / "mb.py"
sys.path.insert(0, str(HERE))
import mb  # noqa: E402

# Records its argv, then exits how the caller asked. FAKE_CODEX_RC / _STDERR
# make a refusing `codex queue` without a Codex anywhere near the test.
FAKE_CODEX = """#!/usr/bin/env python3
import json, os, pathlib, sys
pathlib.Path(os.environ["FAKE_CODEX_ARGV"]).write_text(json.dumps(sys.argv[1:]))
sys.stderr.write(os.environ.get("FAKE_CODEX_STDERR", ""))
sys.exit(int(os.environ.get("FAKE_CODEX_RC", "0")))
"""


def run_mb(*args: str, env: dict | None = None, stdin: str | None = None):
    return subprocess.run([sys.executable, str(MB), *args], capture_output=True,
                          text=True, env=env, input=stdin)


class MailboxTest(unittest.TestCase):
    """A temp inbox, a private watch registry and a fake `codex`."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mbtest-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.box = self.tmp / "inbox" / "mailbox.md"
        self.argv = self.tmp / "codex-argv.json"
        fake = self.tmp / "fake-codex"
        fake.write_text(FAKE_CODEX)
        fake.chmod(0o755)
        self.env = dict(os.environ,
                        XDG_STATE_HOME=str(self.tmp / "state"),
                        MB_CODEX_BIN=str(fake),
                        FAKE_CODEX_ARGV=str(self.argv))
        self.env.pop("CLAUDE_CODE_SESSION_ID", None)
        self.env.pop("FAKE_CODEX_RC", None)
        self.env.pop("FAKE_CODEX_STDERR", None)
        # In-process helpers (mb.watch, mb.wait) read the same environment.
        self.restore = {k: os.environ.get(k) for k in
                        ("XDG_STATE_HOME", "MB_CODEX_BIN", "FAKE_CODEX_ARGV")}
        os.environ.update({k: self.env[k] for k in self.restore})
        self.addCleanup(self._restore_env)
        self.sid = "sess-0123456789ab"

    def _restore_env(self):
        for key, value in self.restore.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def write_worker_state(self, *, session_id="codex-thread-uuid", state="running",
                           process_state="running", alive=True):
        """`alive` controls the only liveness signal mb.py trusts: whether the
        recorded worker process identity still matches a running process."""
        (self.box.parent).mkdir(parents=True, exist_ok=True)
        identity = mb.process_identity(os.getpid()) if alive else {
            "pid": 999999, "boot_id": "0" * 36, "start_ticks": 1}
        (self.box.parent / "worker-state.json").write_text(json.dumps({
            "schema_version": "3", "state": state, "session_id": session_id,
            "process": {"state": process_state, "worker_identity": identity},
        }))

    def codex_argv(self):
        return json.loads(self.argv.read_text()) if self.argv.exists() else None


class BlockFormat(MailboxTest):
    def test_send_writes_one_complete_numbered_block(self):
        out = run_mb("send", str(self.box), "--from", "worker", "--to", "orchestrator",
                     "--kind", "QUESTION", "--body", "which reading?\nsecond line",
                     env=self.env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), f"SENT 1 {self.box}")
        blocks = mb.read_blocks(self.box)
        self.assertEqual(len(blocks), 1)
        b = blocks[0]
        self.assertTrue(b["complete"])
        self.assertEqual((b["n"], b["sender"], b["to"], b["kind"]),
                         (1, "worker", "orchestrator", "QUESTION"))
        self.assertEqual(b["body"], "which reading?\nsecond line")
        self.assertTrue(self.box.read_text().rstrip("\n").endswith("=== end 1"))

    def test_body_from_stdin_and_file(self):
        f = self.tmp / "body.md"
        f.write_text("from a file\n")
        self.write_worker_state()
        self.assertEqual(run_mb("send", str(self.box), "--from", "worker", "--to",
                                "orchestrator", "--kind", "NOTE", "--body-file", str(f),
                                env=self.env).returncode, 0)
        self.assertEqual(run_mb("send", str(self.box), "--from", "orchestrator", "--to",
                                "worker", "--kind", "ANSWER", stdin="from stdin\n",
                                env=self.env).returncode, 0)
        bodies = [b["body"] for b in mb.read_blocks(self.box)]
        self.assertEqual(bodies, ["from a file", "from stdin"])

    def test_marker_lookalikes_in_a_body_cannot_end_or_start_a_block(self):
        body = "before\n=== end 1\n=== 9 | worker -> orchestrator | x | QUESTION\nafter"
        mb.send(self.box, "worker", "orchestrator", "NOTE", body)
        mb.send(self.box, "orchestrator", "worker", "ANSWER", "ok")
        blocks = mb.read_blocks(self.box)
        self.assertEqual([(b["n"], b["complete"]) for b in blocks], [(1, True), (2, True)])
        self.assertIn(" === end 1", blocks[0]["body"])
        self.assertIn("after", blocks[0]["body"])

    def test_refusals_leave_the_file_untouched(self):
        cases = [
            ("--from", "worker", "--to", "worker", "--kind", "NOTE", "--body", "x"),
            ("--from", "relay", "--to", "worker", "--kind", "NOTE", "--body", "x"),
            ("--from", "worker", "--to", "relay", "--kind", "NOTE", "--body", "x"),
            ("--from", "worker", "--to", "nobody", "--kind", "NOTE", "--body", "x"),
            ("--from", "worker", "--to", "orchestrator", "--kind", "lower", "--body", "x"),
            ("--from", "worker", "--to", "orchestrator", "--kind", "NOTE", "--body", "  \n"),
        ]
        for case in cases:
            out = run_mb("send", str(self.box), *case, env=self.env)
            self.assertEqual(out.returncode, 2, case)
        self.assertFalse(self.box.exists())

    def test_concurrent_senders_never_share_a_number_or_interleave(self):
        procs = []
        for i in range(8):
            code = (f"import sys; sys.path.insert(0, {str(HERE)!r}); import mb\n"
                    f"from pathlib import Path\n"
                    f"for k in range(5):\n"
                    f"    mb.send(Path({str(self.box)!r}), 'worker', 'orchestrator', 'NOTE',\n"
                    f"            'sender {i} msg ' + str(k) + chr(10) + 'x' * 3000)\n")
            procs.append(subprocess.Popen([sys.executable, "-c", code]))
        for p in procs:
            self.assertEqual(p.wait(), 0)
        blocks = mb.read_blocks(self.box)
        self.assertEqual(sorted(b["n"] for b in blocks), list(range(1, 41)))
        self.assertTrue(all(b["complete"] for b in blocks))
        for b in blocks:
            first, rest = b["body"].split("\n", 1)
            self.assertRegex(first, r"^sender \d msg \d$")
            self.assertEqual(rest, "x" * 3000)

    def test_unterminated_block_is_not_complete_and_numbering_skips_it(self):
        mb.send(self.box, "worker", "orchestrator", "NOTE", "one")
        with self.box.open("a") as f:      # a hand edit that never wrote its end marker
            f.write("\n=== 2 | worker -> orchestrator | 2026-01-01T00:00:00Z | QUESTION\nhalf\n")
        self.assertEqual([b["complete"] for b in mb.read_blocks(self.box)], [True, False])
        self.assertEqual(mb.send(self.box, "orchestrator", "worker", "ANSWER", "three"), 3)
        self.assertEqual([(b["n"], b["complete"]) for b in mb.read_blocks(self.box)],
                         [(1, True), (2, False), (3, True)])

    def test_read_newest_and_by_number(self):
        mb.send(self.box, "worker", "orchestrator", "QUESTION", "q body")
        mb.send(self.box, "orchestrator", "worker", "ANSWER", "a body")
        self.assertIn("a body", run_mb("read", str(self.box), env=self.env).stdout)
        one = run_mb("read", str(self.box), "1", env=self.env).stdout
        self.assertIn("| QUESTION", one)
        self.assertIn("q body", one)
        self.assertEqual(run_mb("read", str(self.box), "7", env=self.env).returncode, 2)

    def test_pending_names_the_unanswered_block_and_any_unterminated_one(self):
        mb.send(self.box, "worker", "orchestrator", "QUESTION", "q")
        self.assertEqual(mb.pending(self.box, "orchestrator"), [f"PENDING 1 QUESTION {self.box}"])
        self.assertEqual(mb.pending(self.box, "worker"), [])
        mb.send(self.box, "orchestrator", "worker", "ANSWER", "a")
        self.assertEqual(mb.pending(self.box, "orchestrator"), [])
        self.assertEqual(mb.pending(self.box, "worker"), [f"PENDING 2 ANSWER {self.box}"])
        with self.box.open("a") as f:
            f.write("\n=== 3 | worker -> orchestrator | 2026-01-01T00:00:00Z | NOTE\nhalf\n")
        self.assertIn(f"UNTERMINATED_BLOCK 3 {self.box}", mb.pending(self.box, "orchestrator"))

    def test_the_helper_types_nothing(self):
        """The doorbell must never be a keyboard: a pane in copy mode swallows
        keys as bindings and a pane at a permission dialog obeys them. The
        module docstring may NAME tmux (it records why); the code may not
        touch it."""
        code = MB.read_text().split('"""', 2)[2]
        for banned in ("send-keys", "tmux"):
            hits = [line.strip() for line in code.splitlines() if banned in line]
            self.assertEqual(hits, [], f"mb.py must not touch {banned}: {hits}")


class RingOnSend(MailboxTest):
    def test_a_block_to_the_worker_rings_codex_queue_with_the_pointer(self):
        self.write_worker_state(session_id="thread-abc")
        out = run_mb("send", str(self.box), "--from", "orchestrator", "--to", "worker",
                     "--kind", "ANSWER", "--body", "use the second reading", env=self.env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.splitlines(),
                         [f"SENT 1 {self.box}", "RANG worker thread-abc"])
        self.assertEqual(self.codex_argv(),
                         ["queue", "--thread", "thread-abc",
                          "--message", f"MAILBOX 1 {self.box.resolve()}"])

    def test_a_block_to_the_orchestrator_rings_nothing(self):
        """The worker's own send runs inside the Codex sandbox, where the state
        database is read-only; the orchestrator is woken by its Stop hook."""
        self.write_worker_state()
        out = run_mb("send", str(self.box), "--from", "worker", "--to", "orchestrator",
                     "--kind", "QUESTION", "--body", "which reading?", env=self.env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), f"SENT 1 {self.box}")
        self.assertIsNone(self.codex_argv())

    def test_a_completed_worker_is_still_rung(self):
        """The hop-12 regression. A `completed` worker's TUI is still sitting
        there, and a post-handback follow-up is exactly the case where the
        lifecycle says "over" and the worker is not. Reading the lifecycle as
        liveness cost that trial a hand-rung pointer."""
        for lifecycle in ("completed", "failed", "exited", "blocked"):
            with self.subTest(lifecycle=lifecycle):
                self.argv.unlink(missing_ok=True)
                self.write_worker_state(state=lifecycle, session_id=f"t-{lifecycle}")
                out = run_mb("send", str(self.box), "--from", "orchestrator", "--to",
                             "worker", "--kind", "NOTE", "--body", "follow-up",
                             env=self.env)
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                self.assertIn(f"RANG worker t-{lifecycle}", out.stdout)
                self.assertEqual(self.codex_argv()[:3], ["queue", "--thread", f"t-{lifecycle}"])

    def test_every_skipped_ring_is_loud_and_leaves_the_block_in_the_file(self):
        cases = [
            ({}, "no worker-state.json"),
            ({"session_id": None}, "no bound session"),
        ]
        for n, (state_kwargs, expect) in enumerate(cases, start=1):
            with self.subTest(expect=expect):
                worker_state = self.box.parent / "worker-state.json"
                worker_state.unlink(missing_ok=True)
                if state_kwargs != {}:
                    self.write_worker_state(**state_kwargs)
                out = run_mb("send", str(self.box), "--from", "orchestrator", "--to",
                             "worker", "--kind", "NOTE", "--body", f"note {n}", env=self.env)
                self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
                lines = out.stdout.splitlines()
                self.assertEqual(lines[0], f"SENT {n} {self.box}")
                self.assertTrue(lines[1].startswith("RING_SKIPPED "), lines)
                self.assertIn(expect, lines[1])
                self.assertIsNone(self.codex_argv())
                block = [b for b in mb.read_blocks(self.box) if b["n"] == n][0]
                self.assertTrue(block["complete"])
                self.assertEqual(block["body"], f"note {n}")

    def test_a_refusing_codex_queue_is_reported_with_its_stderr(self):
        self.write_worker_state(session_id="gone-thread")
        env = dict(self.env, FAKE_CODEX_RC="1", FAKE_CODEX_STDERR="no such thread\n")
        out = run_mb("send", str(self.box), "--from", "orchestrator", "--to", "worker",
                     "--kind", "ANSWER", "--body", "a", env=env)
        self.assertEqual(out.returncode, 3)
        self.assertIn("RING_SKIPPED codex queue exited 1: no such thread", out.stdout)
        self.assertEqual(len(mb.read_blocks(self.box)), 1)

    def test_a_missing_codex_binary_is_reported_not_raised(self):
        self.write_worker_state()
        env = dict(self.env, MB_CODEX_BIN=str(self.tmp / "no-such-codex"))
        out = run_mb("send", str(self.box), "--from", "orchestrator", "--to", "worker",
                     "--kind", "ANSWER", "--body", "a", env=env)
        self.assertEqual(out.returncode, 3)
        self.assertIn("RING_SKIPPED cannot run", out.stdout)


class Watch(MailboxTest):
    def cursor(self, session_id=None):
        reg = mb.read_registry(session_id or self.sid)
        return {p: e["cursor"] for p, e in reg["mailboxes"].items()}

    def test_watch_registers_the_session_from_the_environment(self):
        mb.send(self.box, "worker", "orchestrator", "NOTE", "old")
        mb.send(self.box, "orchestrator", "worker", "NOTE", "older still")
        out = run_mb("watch", str(self.box), env=dict(self.env, CLAUDE_CODE_SESSION_ID=self.sid))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), f"WATCHING {self.sid} {self.box.resolve()}")
        self.assertEqual(self.cursor(), {str(self.box.resolve()): 2})

    def test_watch_without_a_session_id_is_refused(self):
        out = run_mb("watch", str(self.box), env=self.env)
        self.assertEqual(out.returncode, 2)
        self.assertIn("no Claude session id", out.stderr)

    def test_a_takeover_is_woken_about_the_block_still_waiting_on_it(self):
        """A successor must not be replayed the conversation, and must not miss
        the question a dead waiter never delivered."""
        mb.send(self.box, "worker", "orchestrator", "NOTE", "history")
        mb.send(self.box, "orchestrator", "worker", "ANSWER", "answered")
        mb.send(self.box, "worker", "orchestrator", "QUESTION", "still open")
        mb.watch(self.box, self.sid)
        self.assertEqual(self.cursor(), {str(self.box.resolve()): 2})
        self.assertEqual([b["n"] for b in mb.new_blocks(self.box.resolve(), 2)], [3])

    def test_re_watching_never_rewinds_the_cursor(self):
        mb.watch(self.box, self.sid)
        mb.send(self.box, "worker", "orchestrator", "NOTE", "delivered already")
        mb.collect(self.sid)
        self.assertEqual(self.cursor(), {str(self.box.resolve()): 1})
        mb.watch(self.box, self.sid)
        self.assertEqual(self.cursor(), {str(self.box.resolve()): 1})


class Wait(MailboxTest):
    """`wait` is the Stop hook: session_id arrives on stdin, 0 says nothing, 2
    wakes the session with stderr as the message."""

    def run_wait(self, *args: str, session_id: str | None = "", env: dict | None = None):
        payload = json.dumps({"session_id": session_id if session_id != "" else self.sid,
                              "transcript_path": "/dev/null", "cwd": str(self.tmp)})
        return run_mb("wait", "--poll", "0.05", *args, env=env or self.env, stdin=payload)

    def test_a_session_watching_nothing_exits_0_at_once(self):
        start = time.monotonic()
        out = self.run_wait("--deadline", "30")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertEqual(out.stderr, "")

    def test_broken_hook_stdin_never_wakes_anyone(self):
        self.assertEqual(run_mb("wait", env=self.env, stdin="not json").returncode, 1)
        self.assertEqual(run_mb("wait", env=self.env, stdin="{}").returncode, 1)
        self.assertEqual(run_mb("wait", env=self.env,
                                stdin=json.dumps({"session_id": "../escape"})).returncode, 1)

    def test_new_blocks_wake_the_session_once_each_and_in_order(self):
        mb.watch(self.box, self.sid)
        for k in range(3):
            mb.send(self.box, "worker", "orchestrator", "NOTE", f"burst {k}")
        mb.send(self.box, "orchestrator", "worker", "NOTE", "not for us")
        out = self.run_wait("--deadline", "30")
        self.assertEqual(out.returncode, 2, out.stdout)
        self.assertEqual(out.stderr.splitlines(),
                         [f"MAILBOX {n} {self.box.resolve()}" for n in (1, 2, 3)])
        again = self.run_wait("--deadline", "0.3")       # never re-delivered
        self.assertEqual(again.returncode, 2)
        self.assertIn("MAILBOX_WAITER_RENEW", again.stderr)
        self.assertNotIn("MAILBOX 1", again.stderr)

    def test_a_waiter_blocks_until_a_block_arrives(self):
        mb.watch(self.box, self.sid)
        payload = self.tmp / "hook-stdin.json"
        payload.write_text(json.dumps({"session_id": self.sid}))
        with payload.open() as stdin:
            proc = subprocess.Popen([sys.executable, str(MB), "wait", "--deadline", "30",
                                     "--poll", "0.05"], env=self.env, text=True,
                                    stdin=stdin, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        time.sleep(0.5)
        self.assertIsNone(proc.poll(), "the waiter returned before anything arrived")
        n = mb.send(self.box, "worker", "orchestrator", "QUESTION", "which reading?")
        stdout, stderr = proc.communicate(timeout=15)
        self.assertEqual(proc.returncode, 2, stdout)
        self.assertEqual(stderr.strip(), f"MAILBOX {n} {self.box.resolve()}")

    def test_an_unterminated_block_is_never_delivered(self):
        self.box.parent.mkdir(parents=True, exist_ok=True)
        mb.watch(self.box, self.sid)
        with self.box.open("a") as f:
            f.write("\n=== 1 | worker -> orchestrator | 2026-01-01T00:00:00Z | QUESTION\nhalf\n")
        out = self.run_wait("--deadline", "0.3")
        self.assertEqual(out.returncode, 2)
        self.assertIn("MAILBOX_WAITER_RENEW", out.stderr)
        self.assertNotIn("MAILBOX 1 ", out.stderr)

    def test_a_second_waiter_exits_0_while_the_first_holds_the_lock(self):
        mb.watch(self.box, self.sid)
        lock = mb.registry_path(self.sid).with_suffix(".waiter.lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        mb.send(self.box, "worker", "orchestrator", "NOTE", "would wake a free waiter")
        start = time.monotonic()
        out = self.run_wait("--deadline", "30")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertEqual(mb.read_registry(self.sid)["mailboxes"][str(self.box.resolve())]["cursor"], 0)

    def test_the_deadline_renews_the_hook_instead_of_letting_it_be_killed(self):
        mb.watch(self.box, self.sid)
        start = time.monotonic()
        out = self.run_wait("--deadline", "0.4")
        self.assertEqual(out.returncode, 2, out.stdout)
        self.assertIn("MAILBOX_WAITER_RENEW", out.stderr)
        self.assertLess(time.monotonic() - start, 10.0)

    def test_the_watch_survives_a_completed_worker_that_is_still_running(self):
        """The hop-12 regression, the other face. After a HANDBACK the waiter
        used to drop the mailbox because the LIFECYCLE was terminal, leaving the
        orchestrator deaf to the follow-up exchange that verification triggers."""
        mb.watch(self.box, self.sid)
        mb.send(self.box, "worker", "orchestrator", "HANDBACK", "handback.md")
        self.write_worker_state(state="completed")         # lifecycle over, process alive
        first = self.run_wait("--deadline", "0.4")
        self.assertEqual(first.returncode, 2)
        self.assertIn("MAILBOX 1 ", first.stderr)
        second = self.run_wait("--deadline", "0.4")        # nothing fresh, but still watched
        self.assertEqual(second.returncode, 2)
        self.assertIn("MAILBOX_WAITER_RENEW", second.stderr)
        self.assertIn(str(self.box.resolve()), mb.read_registry(self.sid)["mailboxes"])
        mb.send(self.box, "worker", "orchestrator", "HANDBACK", "handback.md, take two")
        third = self.run_wait("--deadline", "0.4")         # the follow-up still lands
        self.assertEqual(third.returncode, 2)
        self.assertIn("MAILBOX 2 ", third.stderr)

    def test_the_watch_drops_only_once_the_worker_process_is_gone(self):
        mb.watch(self.box, self.sid)
        mb.send(self.box, "worker", "orchestrator", "HANDBACK", "handback.md")
        self.write_worker_state(state="completed", alive=False)
        first = self.run_wait("--deadline", "0.4")         # the handback still arrives
        self.assertEqual(first.returncode, 2)
        self.assertIn("MAILBOX 1 ", first.stderr)
        second = self.run_wait("--deadline", "30")         # then the watch is over
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(mb.read_registry(self.sid)["mailboxes"], {})

    def test_a_departed_process_is_recognised_however_the_record_reads(self):
        """`process.state` is never reaped when the operator closes the window,
        so it reads `running` for a dead pid. The identity is the witness."""
        self.write_worker_state(state="running", process_state="running", alive=False)
        self.assertTrue(mb.worker_process_is_gone(json.loads(
            (self.box.parent / "worker-state.json").read_text())))
        self.write_worker_state(state="completed", process_state="running", alive=True)
        self.assertFalse(mb.worker_process_is_gone(json.loads(
            (self.box.parent / "worker-state.json").read_text())))


if __name__ == "__main__":
    unittest.main()
