#!/usr/bin/env python3
"""Tests for mb.py. tmux tests run on a PRIVATE server (`tmux -L`), never the
operator's. Run as a module: `python3 -m unittest test_mb -v`."""

from __future__ import annotations

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


def run_mb(*args: str, env: dict | None = None, stdin: str | None = None):
    return subprocess.run([sys.executable, str(MB), *args], capture_output=True,
                          text=True, env=env, input=stdin)


def drop_socket(name: str) -> None:
    """kill-server leaves the dead socket file behind; do not litter the tmux dir."""
    base = Path(os.environ.get("TMUX_TMPDIR", "/tmp")) / f"tmux-{os.getuid()}"
    (base / name).unlink(missing_ok=True)


class BlockFormat(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mbtest-"))
        self.box = self.tmp / "inbox" / "mailbox.md"
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_send_writes_one_complete_numbered_block(self):
        out = run_mb("send", str(self.box), "--from", "worker", "--to", "orchestrator",
                     "--kind", "QUESTION", "--body", "which reading?\nsecond line")
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
        self.assertEqual(run_mb("send", str(self.box), "--from", "worker", "--to",
                                "orchestrator", "--kind", "NOTE", "--body-file", str(f)).returncode, 0)
        self.assertEqual(run_mb("send", str(self.box), "--from", "orchestrator", "--to",
                                "worker", "--kind", "ANSWER", stdin="from stdin\n").returncode, 0)
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
            ("--from", "worker", "--to", "nobody", "--kind", "NOTE", "--body", "x"),
            ("--from", "worker", "--to", "orchestrator", "--kind", "lower", "--body", "x"),
            ("--from", "worker", "--to", "orchestrator", "--kind", "NOTE", "--body", "  \n"),
        ]
        for case in cases:
            out = run_mb("send", str(self.box), *case)
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
        self.assertIn("a body", run_mb("read", str(self.box)).stdout)
        one = run_mb("read", str(self.box), "1").stdout
        self.assertIn("| QUESTION", one)
        self.assertIn("q body", one)
        self.assertEqual(run_mb("read", str(self.box), "7").returncode, 2)

    def test_newest_claim_per_role_wins_and_bad_pane_is_refused(self):
        self.assertEqual(run_mb("claim", str(self.box), "--role", "worker", "--pane", "%3").returncode, 0)
        self.assertEqual(run_mb("claim", str(self.box), "--role", "orchestrator", "--pane", "%1").returncode, 0)
        self.assertEqual(run_mb("claim", str(self.box), "--role", "worker", "--pane", "%8").returncode, 0)
        self.assertEqual(mb.newest_claims(mb.read_blocks(self.box)),
                         {"worker": "%8", "orchestrator": "%1"})
        env = {k: v for k, v in os.environ.items() if k != "TMUX_PANE"}
        self.assertEqual(run_mb("claim", str(self.box), "--role", "worker", env=env).returncode, 2)

    def test_pending_names_the_unanswered_block_and_any_unterminated_one(self):
        mb.send(self.box, "worker", "relay", "CLAIM", "%3")
        mb.send(self.box, "worker", "orchestrator", "QUESTION", "q")
        self.assertEqual(mb.pending(self.box, "orchestrator"), [f"PENDING 2 QUESTION {self.box}"])
        self.assertEqual(mb.pending(self.box, "worker"), [])
        mb.send(self.box, "orchestrator", "worker", "ANSWER", "a")
        self.assertEqual(mb.pending(self.box, "orchestrator"), [])
        self.assertEqual(mb.pending(self.box, "worker"), [f"PENDING 3 ANSWER {self.box}"])
        with self.box.open("a") as f:
            f.write("\n=== 4 | worker -> orchestrator | 2026-01-01T00:00:00Z | NOTE\nhalf\n")
        self.assertIn(f"UNTERMINATED_BLOCK 4 {self.box}", mb.pending(self.box, "orchestrator"))


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class RelayOnPrivateTmux(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mbrelay-"))
        self.box = self.tmp / "mailbox.md"
        self.sock = f"mbtest-{os.getpid()}-{self._testMethodName[-12:]}"
        self.env = dict(os.environ, MB_TMUX_SOCKET=self.sock)
        self.env.pop("TMUX", None)
        self.env.pop("TMUX_PANE", None)
        self.relay = None
        self.addCleanup(self._teardown)
        self.orch = self._new_pane("orch", first=True)
        self.work = self._new_pane("work")

    def _tmux(self, *args: str) -> str:
        out = subprocess.run(["tmux", "-L", self.sock, *args], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def _new_pane(self, name: str, first: bool = False) -> str:
        verb = ["new-session", "-d", "-s", "t", "-x", "250", "-y", "50"] if first else ["new-window", "-d"]
        return self._tmux(*verb, "-n", name, "-P", "-F", "#{pane_id}", "cat")

    def _teardown(self):
        if self.relay and self.relay.poll() is None:
            self.relay.terminate()
            self.relay.wait(timeout=5)
        if self.relay and self.relay.stdout:
            self.relay.stdout.close()
        subprocess.run(["tmux", "-L", self.sock, "kill-server"], capture_output=True)
        drop_socket(self.sock)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _start_relay(self, *extra: str):
        self.relay = subprocess.Popen([sys.executable, str(MB), "relay", str(self.box),
                                       "--poll", "0.1", *extra], env=self.env,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        # Block until the relay has taken its "already seen" snapshot; a sleep
        # here races a loaded machine and turns a late start into a lost ping.
        self.started = self.relay.stdout.readline()
        self.assertIn("RELAY_START", self.started)

    def _pings(self, pane: str) -> list[str]:
        seen = []
        for line in self._tmux("capture-pane", "-p", "-t", pane).splitlines():
            if line.startswith("MAILBOX ") and line not in seen:   # `cat` echoes each line back
                seen.append(line)
        return seen

    def _wait_for(self, pane: str, count: int, timeout: float = 8.0) -> list[str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            got = self._pings(pane)
            if len(got) >= count:
                return got
            time.sleep(0.1)
        return self._pings(pane)

    def _claim_both(self):
        mb.send(self.box, "orchestrator", "relay", "CLAIM", self.orch)
        mb.send(self.box, "worker", "relay", "CLAIM", self.work)

    def test_each_side_is_pinged_for_blocks_addressed_to_it_and_claims_are_silent(self):
        self._claim_both()
        self._start_relay()
        mb.send(self.box, "worker", "orchestrator", "QUESTION", "q")
        self.assertEqual(self._wait_for(self.orch, 1), [f"MAILBOX 3 {self.box.resolve()}"])
        mb.send(self.box, "orchestrator", "worker", "ANSWER", "a")
        self.assertEqual(self._wait_for(self.work, 1), [f"MAILBOX 4 {self.box.resolve()}"])
        self.assertEqual(self._pings(self.orch), [f"MAILBOX 3 {self.box.resolve()}"])

    def test_a_burst_gets_one_ping_per_block_in_order(self):
        self._claim_both()
        self._start_relay("--poll", "1.5")          # all three land inside one look
        for k in range(3):
            mb.send(self.box, "worker", "orchestrator", "NOTE", f"burst {k}")
        got = self._wait_for(self.orch, 3, timeout=12)
        self.assertEqual(got, [f"MAILBOX {n} {self.box.resolve()}" for n in (3, 4, 5)])

    def test_an_unterminated_block_is_never_pinged(self):
        self._claim_both()
        self._start_relay()
        with self.box.open("a") as f:
            f.write("\n=== 3 | worker -> orchestrator | 2026-01-01T00:00:00Z | QUESTION\nhalf\n")
        time.sleep(1.0)
        self.assertEqual(self._pings(self.orch), [])
        mb.send(self.box, "worker", "orchestrator", "NOTE", "whole")
        self.assertEqual(self._wait_for(self.orch, 1), [f"MAILBOX 4 {self.box.resolve()}"])

    def test_blocks_already_complete_at_start_are_not_replayed(self):
        self._claim_both()
        mb.send(self.box, "worker", "orchestrator", "NOTE", "old")
        self._start_relay()
        mb.send(self.box, "worker", "orchestrator", "NOTE", "new")
        self.assertEqual(self._wait_for(self.orch, 1), [f"MAILBOX 4 {self.box.resolve()}"])

    def test_a_block_waits_for_its_addressee_to_claim_a_pane(self):
        mb.send(self.box, "worker", "relay", "CLAIM", self.work)
        self._start_relay()
        mb.send(self.box, "worker", "orchestrator", "QUESTION", "early")
        time.sleep(0.8)
        self.assertEqual(self._pings(self.orch), [])
        mb.send(self.box, "orchestrator", "relay", "CLAIM", self.orch)
        self.assertEqual(self._wait_for(self.orch, 1), [f"MAILBOX 2 {self.box.resolve()}"])

    def test_a_failed_ping_is_retried_only_after_a_new_claim(self):
        mb.send(self.box, "worker", "relay", "CLAIM", self.work)
        mb.send(self.box, "orchestrator", "relay", "CLAIM", "%999")   # no such pane
        self._start_relay()
        mb.send(self.box, "worker", "orchestrator", "QUESTION", "lost?")
        time.sleep(1.0)
        self.assertEqual(self._pings(self.orch), [])
        mb.send(self.box, "orchestrator", "relay", "CLAIM", self.orch)
        self.assertEqual(self._wait_for(self.orch, 1), [f"MAILBOX 3 {self.box.resolve()}"])

    def test_relay_exits_when_the_worker_pane_is_gone(self):
        self._claim_both()
        self._start_relay()
        self._tmux("kill-pane", "-t", self.work)
        self.assertEqual(self.relay.wait(timeout=15), 0)
        self.assertIn("RELAY_EXIT", self.relay.stdout.read())

    def test_relay_exits_when_the_tmux_server_is_gone(self):
        self._claim_both()
        self._start_relay()
        self._tmux("kill-server")
        self.assertEqual(self.relay.wait(timeout=25), 0)
        self.assertIn("RELAY_EXIT", self.relay.stdout.read())


if __name__ == "__main__":
    unittest.main()
