#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from launch_codex_worker import atomic_write_json, process_identity, utc_now


SCRIPT = Path(__file__).with_name("launch_codex_mailbox_job.py")


class MailboxJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.inbox = self.root / "inbox"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_contract(self, value: dict) -> Path:
        path = self.root / "contract.json"
        path.write_text(json.dumps(value))
        return path

    def write_bound_worker_state(self, session_id: str) -> None:
        now = utc_now()
        identity = process_identity(os.getpid())
        assert identity is not None
        state = {
            "schema_version": "3",
            "worker_run_id": str(uuid.uuid4()),
            "plan_id": "plan-test-worker",
            "thread_id": "fpga/20260101-test-worker",
            "state": "running",
            "source_head": "0" * 40,
            "launch": {
                "requested_at": now,
                "worktree": str(self.root),
                "branch": "test-worker",
                "model": "gpt-test",
                "reasoning_effort": "high",
                "auto_compact_token_limit": 300000,
            },
            "process": {
                "state": "running",
                "launcher_pid": os.getpid(),
                "launcher_identity": identity,
                "worker_pid": os.getpid(),
                "worker_identity": identity,
                "started_at": now,
                "exited_at": None,
                "exit_code": None,
            },
            "mailbox": {
                "state": "worker-state.json",
                "prompt": "prompt.md",
                "handback_json": "handback.json",
                "handback_markdown": "handback.md",
                "messages": "mailbox.md",
                "progress": "progress.json",
            },
            "session_id": session_id,
            "reason": None,
            "events": [
                {"event": "WORKER_STARTING", "at": now, "state": "starting"},
                {"event": "WORKER_LAUNCHED", "at": now, "state": "running"},
                {"event": "WORKER_SESSION_BOUND", "at": now, "state": "running"},
            ],
            "updated_at": now,
        }
        atomic_write_json(self.inbox / "worker-state.json", state)

    def base_contract(self) -> dict:
        return {
            "schema_version": "1",
            "job_name": "delayed-pass",
            "working_directory": ".",
            "timeout_seconds": 10,
            "max_parallel": 1,
            "summary": {"tail_lines": 4, "max_bytes": 512},
            "doorbell": {"mode": "none"},
            "containment": {"mode": "systemd-user-scope"},
            "commands": [
                {
                    "name": "pass-command",
                    "argv": ["bash", "-lc", "sleep 0.1; echo TERMINAL_PASS"],
                    "success_markers": ["TERMINAL_PASS"],
                    "failure_markers": ["TERMINAL_FAIL"],
                    "max_attempts": 1
                }
            ]
        }

    def run_foreground(self, contract: dict) -> tuple[subprocess.CompletedProcess, dict]:
        path = self.write_contract(contract)
        environment = os.environ.copy()
        environment["CODEX_MAILBOX_TEST_MODE"] = "1"
        environment["CODEX_MAILBOX_CONTAINMENT"] = "process-group-test-only"
        result = subprocess.run(
            [str(SCRIPT), "--contract", str(path), "--inbox", str(self.inbox), "--foreground"],
            text=True,
            capture_output=True,
            env=environment,
        )
        result_paths = list(self.inbox.glob("jobs/*/*/result.json"))
        self.assertEqual(len(result_paths), 1, result.stderr)
        return result, json.loads(result_paths[0].read_text())

    def test_complete(self) -> None:
        completed, result = self.run_foreground(self.base_contract())
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(result["state"], "complete")

    def test_failure_marker_overrides_zero_exit(self) -> None:
        contract = self.base_contract()
        contract["commands"][0]["argv"] = ["bash", "-lc", "echo TERMINAL_FAIL"]
        completed, result = self.run_foreground(contract)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(result["state"], "failed")

    def test_timeout(self) -> None:
        contract = self.base_contract()
        contract["timeout_seconds"] = 1
        contract["commands"][0]["argv"] = ["bash", "-lc", "sleep 5"]
        completed, result = self.run_foreground(contract)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(result["state"], "timed_out")

    def test_duplicate_contract_is_refused(self) -> None:
        contract = self.base_contract()
        self.run_foreground(contract)
        path = self.root / "contract.json"
        repeated = subprocess.run(
            [str(SCRIPT), "--contract", str(path), "--inbox", str(self.inbox), "--foreground"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(repeated.returncode, 2)
        self.assertIn("run already exists", repeated.stderr)

    def test_parallel_failure_propagates(self) -> None:
        contract = self.base_contract()
        contract["max_parallel"] = 2
        contract["commands"].append(
            {
                "name": "failing-command",
                "argv": ["bash", "-lc", "echo bad; exit 7"],
                "success_markers": [],
                "failure_markers": [],
                "max_attempts": 1
            }
        )
        completed, result = self.run_foreground(contract)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(len(result["commands"]), 2)

    def test_detached_launch_returns_before_completion(self) -> None:
        contract = self.base_contract()
        contract["commands"][0]["argv"] = ["bash", "-lc", "sleep 1; echo TERMINAL_PASS"]
        path = self.write_contract(contract)
        started = time.monotonic()
        environment = os.environ.copy()
        environment["CODEX_MAILBOX_TEST_MODE"] = "1"
        environment["CODEX_MAILBOX_CONTAINMENT"] = "process-group-test-only"
        launched = subprocess.run(
            [str(SCRIPT), "--contract", str(path), "--inbox", str(self.inbox)],
            text=True,
            capture_output=True,
            check=True,
            env=environment,
        )
        self.assertLess(time.monotonic() - started, 0.8)
        launch_record = json.loads(launched.stdout)
        result_path = Path(launch_record["result_path"])
        deadline = time.monotonic() + 5
        while not result_path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(result_path.exists())
        self.assertEqual(json.loads(result_path.read_text())["state"], "complete")

    def test_self_doorbell_failure_preserves_terminal_record(self) -> None:
        contract = self.base_contract()
        contract["doorbell"] = {"mode": "codex-self"}
        fake_queue = self.root / "fake-codex"
        fake_queue.write_text("#!/usr/bin/env bash\nexit 9\n")
        fake_queue.chmod(0o755)
        self.write_bound_worker_state("test-thread")
        path = self.write_contract(contract)
        environment = os.environ.copy()
        environment["CODEX_THREAD_ID"] = "test-thread"
        environment["CODEX_QUEUE_BINARY"] = str(fake_queue)
        environment["CODEX_MAILBOX_TEST_MODE"] = "1"
        environment["CODEX_MAILBOX_CONTAINMENT"] = "process-group-test-only"
        completed = subprocess.run(
            [str(SCRIPT), "--contract", str(path), "--inbox", str(self.inbox), "--foreground"],
            text=True,
            capture_output=True,
            env=environment,
        )
        self.assertEqual(completed.returncode, 0)
        result_path = next(self.inbox.glob("jobs/*/*/result.json"))
        doorbell_path = result_path.with_name("doorbell.json")
        self.assertEqual(json.loads(result_path.read_text())["state"], "complete")
        self.assertEqual(json.loads(doorbell_path.read_text())["state"], "failed")

    def test_self_doorbell_refuses_mismatched_worker_binding(self) -> None:
        contract = self.base_contract()
        contract["doorbell"] = {"mode": "codex-self"}
        self.write_bound_worker_state("bound-thread")
        path = self.write_contract(contract)
        environment = os.environ.copy()
        environment["CODEX_THREAD_ID"] = "different-thread"
        environment["CODEX_MAILBOX_TEST_MODE"] = "1"
        environment["CODEX_MAILBOX_CONTAINMENT"] = "process-group-test-only"
        completed = subprocess.run(
            [str(SCRIPT), "--contract", str(path), "--inbox", str(self.inbox), "--foreground"],
            text=True,
            capture_output=True,
            env=environment,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("does not match worker-state session_id", completed.stderr)
        self.assertFalse(list(self.inbox.glob("jobs/*/*/request.json")))

    def test_cancel_request_reaches_terminal_cancelled_state(self) -> None:
        contract = self.base_contract()
        contract["job_name"] = "cancelled-job"
        contract["commands"][0]["argv"] = ["bash", "-lc", "sleep 20"]
        contract["commands"][0]["success_markers"] = []
        path = self.write_contract(contract)
        environment = os.environ.copy()
        environment["CODEX_MAILBOX_TEST_MODE"] = "1"
        environment["CODEX_MAILBOX_CONTAINMENT"] = "process-group-test-only"
        launched = subprocess.run(
            [str(SCRIPT), "--contract", str(path), "--inbox", str(self.inbox)],
            text=True,
            capture_output=True,
            check=True,
            env=environment,
        )
        result_path = Path(json.loads(launched.stdout)["result_path"])
        run_dir = result_path.parent
        subprocess.run(
            [str(SCRIPT), "--cancel", str(run_dir), "--reason", "test redirect"],
            text=True,
            capture_output=True,
            check=True,
            env=environment,
        )
        deadline = time.monotonic() + 5
        while not result_path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(result_path.exists())
        result = json.loads(result_path.read_text())
        self.assertEqual(result["state"], "cancelled")
        self.assertTrue(result["commands"][0]["containment"]["cleanup_verified"])


if __name__ == "__main__":
    unittest.main()
