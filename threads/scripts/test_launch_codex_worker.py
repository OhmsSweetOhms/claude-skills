#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path


HERE = Path(__file__).resolve().parent
LAUNCHER = HERE / "launch_codex_worker.py"
SCHEMA = HERE.parent / "assets" / "schemas" / "codex-worker-state.schema.json"
EMITTER = HERE / "emit_codex_launch_packet.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorkerLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "worker"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"], check=True)
        (self.repo / "source.txt").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "source.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "baseline"], check=True)
        self.branch = subprocess.check_output(
            ["git", "-C", str(self.repo), "branch", "--show-current"], text=True
        ).strip()
        self.head = subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True
        ).strip()
        self.inbox = self.repo / "codex-handoff" / "plan-test-worker"
        self.inbox.mkdir(parents=True)
        self.fake_codex = self.repo / "fake-codex"
        self.fake_codex.write_text(
            """#!/usr/bin/env python3
import json
import os
import pathlib
import time

import sys

inbox = pathlib.Path(os.environ["FAKE_INBOX"])
(inbox / "child-argv.json").write_text(json.dumps(sys.argv[1:]))
state = json.loads((inbox / "worker-state.json").read_text())
if state["state"] != "running" or state["process"]["state"] != "running":
    raise SystemExit(91)
(inbox / "child-observed-state.json").write_text(json.dumps(state))
status = os.environ.get("FAKE_HANDBACK_STATUS")
if status:
    handback = {
        "schema_version": "2",
        "plan_id": state["plan_id"],
        "thread_id": state["thread_id"],
        "session_date": "2026-01-01",
        "status": status,
        "worktree": {
            "branch": state["launch"]["branch"],
            "base_at_hop_start": state["source_head"],
            "head_at_handback": state["source_head"],
            "diff_stat": {"files_changed": 0, "insertions": 0, "deletions": 0}
        },
        "commits": [],
        "gates": [],
        "discoveries": [],
        "investigations": [],
        "follow_ons": [],
        "plan_hindsight": "Nothing notable"
    }
    if os.environ.get("FAKE_INVALID_HANDBACK"):
        handback = {"status": status}
    (inbox / "handback.json").write_text(json.dumps(handback))
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
raise SystemExit(int(os.environ.get("FAKE_EXIT", "0")))
""",
            encoding="utf-8",
        )
        self.fake_codex.chmod(0o755)
        self.turn1 = self.inbox / "turn1.md"
        self.turn1.write_text("Execute the plan.\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def command(self, *extra: str) -> list[str]:
        return [
            sys.executable,
            str(LAUNCHER),
            "launch",
            "--inbox", str(self.inbox),
            "--thread-id", "fpga/20260101-test-worker",
            "--plan-id", "plan-test-worker",
            "--expected-branch", self.branch,
            "--expected-head", self.head,
            "--model", "gpt-test",
            "--reasoning-effort", "high",
            "--auto-compact-token-limit", "300000",
            "--turn1-file", str(self.turn1),
            "--codex-bin", str(self.fake_codex),
            *extra,
        ]

    def environment(self, **updates: str) -> dict[str, str]:
        env = os.environ.copy()
        env["FAKE_INBOX"] = str(self.inbox)
        env.update(updates)
        return env

    def read_state(self) -> dict:
        return json.loads((self.inbox / "worker-state.json").read_text(encoding="utf-8"))

    def wait_for_running(self, process: subprocess.Popen[str]) -> dict:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(f"launcher exited early: {process.communicate()}")
            try:
                state = self.read_state()
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.02)
                continue
            if state["state"] == "running" and state["process"]["state"] == "running":
                return state
            time.sleep(0.02)
        self.fail("worker did not reach running state")

    def assert_schema_valid(self, state: dict) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is unavailable")
        jsonschema.Draft7Validator(schema).validate(state)

    def test_completed_requires_handback_and_validates_schema(self) -> None:
        result = subprocess.run(
            self.command(), cwd=self.repo, env=self.environment(FAKE_HANDBACK_STATUS="complete"),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.read_state()
        self.assertEqual(state["state"], "completed", state["events"][-1])
        self.assertEqual(state["process"]["state"], "exited")
        self.assertTrue((self.inbox / "child-observed-state.json").exists())
        self.assertEqual(state["schema_version"], "3")
        self.assertEqual(state["mailbox"]["messages"], "mailbox.md")
        self.assertNotIn("questions", state["mailbox"])
        self.assertIn("WORKER_LAUNCHED", result.stdout)
        self.assertIn("WORKER_COMPLETED", result.stdout)
        self.assert_schema_valid(state)

    def test_turn1_pointer_is_the_prompt_argument_and_never_the_file_content(self) -> None:
        self.turn1.write_text("SECRET-TURN-ONE-BODY\n", encoding="utf-8")
        result = subprocess.run(
            self.command(), cwd=self.repo, env=self.environment(FAKE_HANDBACK_STATUS="complete"),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = json.loads((self.inbox / "child-argv.json").read_text(encoding="utf-8"))
        launcher = load_module(LAUNCHER, "launch_codex_worker_prompt_test")
        self.assertEqual(argv[-1], launcher.turn1_prompt(self.turn1.resolve()))
        self.assertIn(str(self.turn1.resolve()), argv[-1])
        self.assertNotIn("SECRET-TURN-ONE-BODY", " ".join(argv))
        self.assertEqual(argv[:2], ["--model", "gpt-test"])

    def test_missing_or_empty_turn1_is_refused_before_any_state_is_written(self) -> None:
        for prepare in (self.turn1.unlink, lambda: self.turn1.write_text("  \n", encoding="utf-8")):
            self.turn1.write_text("x\n", encoding="utf-8")
            prepare()
            result = subprocess.run(
                self.command(), cwd=self.repo, env=self.environment(),
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("turn-1 file is missing or empty", result.stderr + result.stdout)
            self.assertFalse((self.inbox / "worker-state.json").exists())
            self.assertFalse((self.inbox / "child-argv.json").exists())

    def test_clean_process_exit_is_not_plan_completion(self) -> None:
        result = subprocess.run(
            self.command(), cwd=self.repo, env=self.environment(),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.read_state()
        self.assertEqual(state["state"], "exited")
        self.assertEqual(state["events"][-1]["event"], "WORKER_EXITED")
        self.assert_schema_valid(state)

    def test_invalid_handback_is_not_plan_completion(self) -> None:
        result = subprocess.run(
            self.command(), cwd=self.repo,
            env=self.environment(
                FAKE_HANDBACK_STATUS="complete", FAKE_INVALID_HANDBACK="1"
            ),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.read_state()
        self.assertEqual(state["state"], "exited")
        self.assertIn("not schema-valid", state["events"][-1]["detail"])
        self.assert_schema_valid(state)

    def test_duplicate_live_worker_is_refused(self) -> None:
        first = subprocess.Popen(
            self.command(), cwd=self.repo, env=self.environment(FAKE_SLEEP="30"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.wait_for_running(first)
        bind = subprocess.run(
            [sys.executable, str(LAUNCHER), "bind-session", "--inbox", str(self.inbox),
             "--session-id", "test-session-id"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(bind.returncode, 0, bind.stderr)
        self.assertEqual(self.read_state()["session_id"], "test-session-id")
        self.assertIn("WORKER_LAUNCHED", [row["event"] for row in self.read_state()["events"]])

        duplicate = subprocess.run(
            self.command(), cwd=self.repo, env=self.environment(),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn("refusing duplicate live worker", duplicate.stderr)
        first.terminate()
        first.communicate(timeout=5)

    def test_relaunch_archives_prior_terminal_receipt(self) -> None:
        first = subprocess.run(
            self.command(), cwd=self.repo, env=self.environment(),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        first_state = self.read_state()
        second = subprocess.run(
            self.command("--relaunch"), cwd=self.repo, env=self.environment(),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        archive = self.inbox / "worker-state-history" / f"{first_state['worker_run_id']}.json"
        self.assertTrue(archive.exists())
        self.assertNotEqual(self.read_state()["worker_run_id"], first_state["worker_run_id"])

    def test_terminal_lifecycle_cannot_transition_back_to_running(self) -> None:
        first = subprocess.run(
            self.command(), cwd=self.repo, env=self.environment(),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        update = subprocess.run(
            [sys.executable, str(LAUNCHER), "update", "--inbox", str(self.inbox),
             "--state", "running"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(update.returncode, 2)
        self.assertIn("illegal worker transition exited -> running", update.stderr)

    def test_validate_rejects_schema_invalid_state(self) -> None:
        first = subprocess.run(
            self.command(), cwd=self.repo, env=self.environment(),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        state = self.read_state()
        state["unexpected"] = True
        (self.inbox / "worker-state.json").write_text(json.dumps(state))
        validate = subprocess.run(
            [sys.executable, str(LAUNCHER), "validate", "--inbox", str(self.inbox)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(validate.returncode, 2)
        self.assertIn("not schema-valid", validate.stderr)

    def test_a_fast_exit_keeps_its_launch_event_in_the_record(self) -> None:
        first = subprocess.run(
            self.command(), cwd=self.repo, env=self.environment(),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("WORKER_LAUNCHED", [row["event"] for row in self.read_state()["events"]])

    def test_emitter_builds_lifecycle_owning_launch_command(self) -> None:
        emitter = load_module(EMITTER, "emit_codex_launch_packet_test")
        command = emitter.build_worker_launch_command(
            handback_inbox=Path("/worktree/codex-handoff/plan-test-worker"),
            thread_id="fpga/20260101-test-worker",
            plan_id="plan-test-worker",
            branch="test-worker",
            base_sha="0123456",
            codex_model="gpt-test",
            reasoning_effort="high",
            auto_compact_token_limit=300000,
            turn1_file=Path("/worktree/codex-handoff/plan-test-worker/turn1.md"),
        )
        self.assertIn("--turn1-file /worktree/codex-handoff/plan-test-worker/turn1.md", command)
        self.assertIn("launch_codex_worker.py", command)
        self.assertIn("--expected-head 0123456", command)
        self.assertNotIn(" codex --model ", command)
        syntax = subprocess.run(
            ["bash", "-n", "-c", command], capture_output=True, text=True
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

        packet = emitter.emit_packet(
            plan_file=Path("/main/.threads/fpga/thread/plan-test-worker.md"),
            kickoff_file=Path("/main/.threads/fpga/thread/kickoff-plan-test-worker.md"),
            worktree=Path("/worktree"),
            main_repo=Path("/main"),
            branch="test-worker",
            base_sha="0123456",
            handback_inbox=Path("/worktree/codex-handoff/plan-test-worker"),
            thread_id="fpga/thread",
            plan_id="plan-test-worker",
            codex_model="gpt-test",
            reasoning_effort="high",
            auto_compact_token_limit=300000,
        )
        self.assertIn("launch_codex_mailbox_job.py --contract", packet)
        # Questions and the handback travel as blocks in the one shared mailbox;
        # nothing waits on a file and no wait job exists for a question.
        self.assertIn("mb.py\" send codex-handoff/plan-test-worker/mailbox.md --from worker "
                      "--to orchestrator --kind QUESTION", packet)
        self.assertIn("--kind HANDBACK", packet)
        self.assertIn("MAILBOX <n> <path>", packet)
        for retired in ("await_codex_answer", "watch_codex_questions", "q-NN", "questions/"):
            self.assertNotIn(retired, packet)
        self.assertNotIn("\\       --contract", packet)
        self.assertNotIn("Block on\n     `bash", packet)

        # The doorbell types nothing and routes by session id, so turn 1 must
        # bind the session before the worker can be reached at all — and must
        # not promise a relay or a keystroke.
        turn1 = packet.split("```")[1]
        bind = [i for i, line in enumerate(turn1.splitlines()) if "bind-session" in line]
        self.assertEqual(len(bind), 1, "turn 1 must name bind-session exactly once")
        worker_commands = [i for i, line in enumerate(turn1.splitlines())
                           if line.startswith("python3 ")]
        self.assertEqual(worker_commands[0], bind[0],
                         "bind-session must be the worker's FIRST command in turn 1")
        self.assertIn("QUEUED into this session", turn1)
        started = [i for i, line in enumerate(turn1.splitlines()) if "--kind STARTED" in line]
        self.assertEqual(len(started), 1, "turn 1 must announce the launch exactly once")
        self.assertEqual(worker_commands[1], started[0],
                         "STARTED must be the worker's SECOND command, right after the bind")
        self.assertIn('fork_turns "none"', turn1)
        self.assertIn("--kind ACK --reply-to <n>", turn1)
        self.assertIn("codex-handoff/plan-test-worker/temp/", turn1)
        self.assertIn("A sub-agent never writes to mailbox.md and never binds a session", turn1)
        for retired in ("host relay", "pings", "typed into this session"):
            self.assertNotIn(retired, packet)

        # The ACK: without it the orchestrator cannot tell "never woke" from
        # "woke and working" (hop-12 trial, 2026-09-18). It must be ordered
        # BEFORE the work, or it reports nothing the next block would not.
        self.assertIn("--kind ACK", turn1)
        self.assertIn("BEFORE any other tool call", turn1)
        self.assertLess(turn1.index("--kind ACK"), turn1.index("--kind HANDBACK"),
                        "the ACK must be described before the handback")
        self.assertIn('titled "Constraints" or "Hard constraints"', turn1,
                      "a plan whose heading is Constraints must not be missed")

    def test_a_packet_names_the_checkout_it_was_emitted_from(self) -> None:
        """A packet emitted from a branch worktree must run THAT checkout's
        scripts: the installed skill's launcher may not know the branch's flags.
        From the installed skill the portable forms are kept."""
        emitter = load_module(EMITTER, "emit_codex_launch_packet_localize_test")
        # A packet names two skills now: the worker runs `mailbox`'s mb.py and
        # `threads`' launcher, and BOTH come from the checkout under trial when
        # it supplies both.
        text = ('python3 "$HOME/.claude/skills/mailbox/scripts/mb.py" and '
                '~/.claude/skills/threads/references/x.md')
        here = str(HERE.parent)
        if emitter.SKILL_DIR == emitter.LIVE_SKILL_DIR.resolve():
            self.assertEqual(emitter.localize(text), text)
        else:
            self.assertIn(f"{here}/references/x.md", emitter.localize(text))
        emitter.SKILL_DIR = emitter.LIVE_SKILL_DIR.resolve()       # as if installed
        self.assertEqual(emitter.localize(text), text)
        with tempfile.TemporaryDirectory() as tmp:                 # as if a skills-repo worktree
            root = Path(tmp).resolve()
            (root / "threads").mkdir()
            (root / "mailbox").mkdir()
            emitter.SKILL_DIR = root / "threads"
            out = emitter.localize(text)
            self.assertIn(f'"{root}/mailbox/scripts/mb.py"', out)
            self.assertIn(f"{root}/threads/references/x.md", out)
            self.assertNotIn(".claude/skills/", out.replace(str(root), ""))
            command = emitter.build_worker_launch_command(
                handback_inbox=Path("/worktree/codex-handoff/plan-x"), thread_id="a/b", plan_id="plan-x",
                branch="b", base_sha="0123456", codex_model="m", reasoning_effort="low",
                auto_compact_token_limit=1, turn1_file=Path("/worktree/codex-handoff/plan-x/turn1.md"),
            )
            self.assertIn(f"{root}/threads/scripts/launch_codex_worker.py", command)

    def test_a_renamed_single_skill_copy_rewrites_only_what_exists(self) -> None:
        """A trial may be ONE skill copied under another name (`threads-next`
        beside no `mailbox`). Its packet names the copy for the threads skill and
        keeps the installed path for a skill the trial does not supply — never a
        `threads/` or `mailbox/` directory that is not there (it killed every
        script path of a real packet, 2026-09-21)."""
        emitter = load_module(EMITTER, "emit_codex_launch_packet_renamed_copy_test")
        text = ('python3 "$HOME/.claude/skills/mailbox/scripts/mb.py" and '
                '~/.claude/skills/threads/scripts/fire_codex_worker.py and '
                '$HOME/.claude/skills/fingerprint/fingerprint_scan.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "threads-next").mkdir()
            emitter.SKILL_DIR = root / "threads-next"
            out = emitter.localize(text)
            self.assertIn(f"{root}/threads-next/scripts/fire_codex_worker.py", out)
            self.assertIn('"$HOME/.claude/skills/mailbox/scripts/mb.py"', out)
            self.assertIn("$HOME/.claude/skills/fingerprint/fingerprint_scan.py", out)
            self.assertNotIn(f"{root}/threads/", out)
            self.assertNotIn(f"{root}/mailbox/", out)

    def test_staged_env_file_sources_cleanly_under_errexit_without_an_envrc(self) -> None:
        """fire.sh runs `set -euo pipefail` then sources env.sh. A worktree with no
        .envrc must not make that source return non-zero (it killed a real fire
        silently: rc=1, nothing on the pane)."""
        emitter = load_module(EMITTER, "emit_codex_launch_packet_env_test")
        env_path, _how = emitter.stage_env_file(
            self.inbox, env_file=None, worktree=self.repo,
            thread_id="fpga/20260101-test-worker", plan_id="plan-test-worker",
        )
        self.assertFalse((self.repo / ".envrc").exists())
        run = subprocess.run(
            ["bash", "-c", f"set -euo pipefail; cd {self.repo}; source {env_path}; echo sourced-ok"],
            capture_output=True, text=True,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("sourced-ok", run.stdout)
        # The skeleton's header comment names the launcher of THIS checkout, as
        # the rest of the packet does (a side-checkout trial found it stale).
        if emitter.SKILL_DIR != emitter.LIVE_SKILL_DIR.resolve():
            self.assertNotIn(".claude/skills/threads/", env_path.read_text())



class WorktreeDiscoveryTests(unittest.TestCase):
    """The emitter never guesses a worktree: a merged entry is never returned, a
    pathless active entry is never skipped in silence (2026-09-19, arm plan-15)."""

    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.base = Path(self.scratch.name)
        self.main = self.base / "main-checkout"
        self.main.mkdir()
        self.thread_json = self.main / "thread.json"
        self.emitter = load_module(EMITTER, "emit_codex_launch_packet_discovery_test")
        env = unittest.mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("WORKBASE", None)

    def worktree(self, name: str) -> Path:
        path = self.base / name
        path.mkdir()
        return path

    def write(self, *entries: dict) -> None:
        self.thread_json.write_text(json.dumps({"codex_worktrees": list(entries)}))

    def discover(self):
        return self.emitter.discover_worktree(self.thread_json, self.main)

    def test_a_pathless_active_entry_is_found_by_name_and_the_merged_one_never_is(self) -> None:
        active = self.worktree("project-active-work")
        self.worktree("other-repo-merged-work")
        self.write(
            {"worktree": "project-active-work", "path": None, "branch": "active", "status": "active"},
            {"path": "$WORKBASE/other-repo-merged-work", "branch": "old", "status": "merged"},
        )
        self.assertEqual(self.discover(), (active.resolve(), "active"))

    def test_an_active_entry_that_resolves_nowhere_dies_naming_it_and_never_falls_through(self) -> None:
        self.worktree("other-repo-merged-work")
        self.write(
            {"worktree": "was-never-created", "path": None, "branch": "active", "status": "active"},
            {"path": "$WORKBASE/other-repo-merged-work", "branch": "old", "status": "merged"},
        )
        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):
            self.discover()
        self.assertIn("was-never-created", err.getvalue())
        self.assertIn("A merged entry is never used", err.getvalue())
        self.assertIn("--worktree-path", err.getvalue())

    def test_two_live_entries_are_an_error_however_they_are_written(self) -> None:
        self.worktree("first")
        self.worktree("second")
        self.write({"worktree": "first", "status": "active", "branch": "a"},
                   {"path": "$WORKBASE/second", "status": "active", "branch": "b"})
        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):
            self.discover()
        self.assertIn("2 live codex worktrees", err.getvalue())

    def test_out_outside_the_resolved_inbox_is_refused(self) -> None:
        inbox = self.worktree("project-active-work") / "codex-handoff" / "plan-15-x"
        elsewhere = self.worktree("other-repo-merged-work") / "codex-handoff" / "plan-15-x"
        self.emitter.refuse_a_split_packet(inbox / "prompt.md", inbox)      # the inbox's own
        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):
            self.emitter.refuse_a_split_packet(elsewhere / "prompt.md", inbox)
        self.assertIn("is not in the resolved inbox", err.getvalue())


if __name__ == "__main__":
    unittest.main()
