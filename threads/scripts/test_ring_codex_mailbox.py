"""Focused routing and repeat-delivery tests; no actual sessions are notified."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ring_codex_mailbox import ring

REAL_RUN = subprocess.run


class DoorbellTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.inbox = Path(self.scratch.name)
        env = patch.dict("os.environ", {"CODEX_HOME": str(self.inbox)})
        env.start()
        self.addCleanup(env.stop)
        self.owner = "11111111-1111-4111-8111-111111111111"
        self.worker = "22222222-2222-4222-8222-222222222222"
        (self.inbox / "codex-mailbox-route.json").write_text(json.dumps(
            {"orchestrator": self.owner, "worker": self.worker}))
        (self.inbox / "question.md").write_text("---\nstatus: open\n---\n## Question\nWhich value?\n## Resolution\n")

    @patch("ring_codex_mailbox.subprocess.run")
    def test_correct_target_and_duplicate(self, queue):
        queue.return_value = subprocess.CompletedProcess([], 0, "queued", "")
        self.assertEqual(ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker)["state"], "sent")
        argv = queue.call_args.args[0]
        self.assertEqual(argv[3], self.owner)
        self.assertEqual(argv[5], f"OPEN_QUESTION {self.inbox / 'question.md'}")
        self.assertEqual(ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker)["state"], "duplicate")
        queue.assert_called_once()

    @patch("ring_codex_mailbox.subprocess.run")
    def test_wrong_sender_and_missing_record(self, queue):
        with self.assertRaises(ValueError):
            ring(self.inbox, "OPEN_QUESTION", "question.md", self.owner)
        with self.assertRaises(OSError):
            ring(self.inbox, "OPEN_QUESTION", "missing.md", self.worker)
        queue.assert_not_called()

    @patch("ring_codex_mailbox.subprocess.run")
    def test_answer_requires_body(self, queue):
        (self.inbox / "question.md").write_text("status: answered\n## Resolution\n<!-- pending -->\n")
        with self.assertRaises(ValueError):
            ring(self.inbox, "ANSWER_READY", "question.md", self.owner)
        queue.assert_not_called()
        (self.inbox / "question.md").write_text("status: answered\n## Resolution\nUse violet.\n")
        queue.return_value = subprocess.CompletedProcess([], 0, "queued", "")
        ring(self.inbox, "ANSWER_READY", "question.md", self.owner)
        self.assertEqual(queue.call_args.args[0][3], self.worker)

    @patch("ring_codex_mailbox.subprocess.run")
    def test_timeout_retains_evidence_and_prevents_blind_retry(self, queue):
        queue.side_effect = subprocess.TimeoutExpired("codex", 20)
        with self.assertRaises(ValueError):
            ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker)
        receipt = next((self.inbox / "doorbells").glob("*.json"))
        self.assertEqual(json.loads(receipt.read_text())["state"], "uncertain")
        with self.assertRaises(ValueError):
            ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker)
        queue.assert_called_once()

    @patch("ring_codex_mailbox.subprocess.run")
    def test_handback_requires_object(self, queue):
        queue.return_value = subprocess.CompletedProcess([], 0, "queued", "")
        (self.inbox / "handback.json").write_text("[]")
        with self.assertRaises(ValueError):
            ring(self.inbox, "HANDBACK", "handback.json", self.worker)
        queue.assert_not_called()
        (self.inbox / "handback.json").write_text('{"state":"complete"}')
        self.assertEqual(ring(self.inbox, "HANDBACK", "handback.json", self.worker)["state"], "sent")
        self.assertEqual(queue.call_args.args[0][3], self.owner)

    @patch("ring_codex_mailbox.subprocess.run")
    def test_failed_queue_retains_receipt(self, queue):
        queue.return_value = subprocess.CompletedProcess([], 1, "", "unavailable")
        with self.assertRaises(ValueError):
            ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker)
        receipt = next((self.inbox / "doorbells").glob("*.json"))
        self.assertEqual(json.loads(receipt.read_text())["state"], "failed")

    @patch("ring_codex_mailbox.subprocess.run")
    def test_invalid_route_rejected(self, queue):
        (self.inbox / "codex-mailbox-route.json").write_text(json.dumps(
            {"orchestrator": "guessed-name", "worker": self.worker}))
        with self.assertRaises(ValueError):
            ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker)
        queue.assert_not_called()

    @patch("ring_codex_mailbox.subprocess.run")
    def test_changed_content_is_new_event(self, queue):
        queue.return_value = subprocess.CompletedProcess([], 0, "queued", "")
        ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker)
        with (self.inbox / "question.md").open("a") as stream:
            stream.write("\nMore context.\n")
        self.assertEqual(ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker)["state"], "sent")
        self.assertEqual(queue.call_count, 2)

    @patch("ring_codex_mailbox.subprocess.run")
    def test_authorized_retry_retains_original(self, queue):
        queue.return_value = subprocess.CompletedProcess([], 1, "", "database initialization failed")
        with self.assertRaises(ValueError):
            ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker)
        queue.return_value = subprocess.CompletedProcess([], 0, "queued", "")
        ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker, "Operator approved host retry after startup failure")
        receipt = json.loads(next((self.inbox / "doorbells").glob("*.json")).read_text())
        self.assertEqual(receipt["state"], "sent")
        self.assertEqual(receipt["previous_attempt"]["state"], "failed")
        self.assertEqual(receipt["previous_attempt"]["stderr"], "database initialization failed")
        self.assertIn("Operator approved", receipt["recovery_reason"])
        self.assertEqual(ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker)["state"], "duplicate")
        self.assertEqual(queue.call_count, 2)

    @patch("ring_codex_mailbox.subprocess.run")
    def test_retry_is_bounded(self, queue):
        queue.return_value = subprocess.CompletedProcess([], 1, "", "failed")
        for reason in (None, "Confirmed failure; retry approved"):
            with self.assertRaises(ValueError):
                ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker, reason)
        with self.assertRaises(ValueError):
            ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker, "Try again")
        self.assertEqual(queue.call_count, 2)

    @patch("ring_codex_mailbox.subprocess.run")
    def test_retry_refuses_uncertain_or_missing_receipt(self, queue):
        with self.assertRaises(ValueError):
            ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker, "No receipt")
        queue.assert_not_called()
        queue.side_effect = subprocess.TimeoutExpired("codex", 20)
        with self.assertRaises(ValueError):
            ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker)
        with self.assertRaises(ValueError):
            ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker, "Retry requested")
        queue.assert_called_once()

    @patch("ring_codex_mailbox.subprocess.run")
    def test_preflight_does_not_poison_host_attempt(self, queue):
        with patch("ring_codex_mailbox.os.access", return_value=False):
            with self.assertRaisesRegex(ValueError, "HOST_EXECUTION_REQUIRED"):
                ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker)
        self.assertFalse((self.inbox / "doorbells").exists())
        queue.assert_not_called()
        queue.return_value = subprocess.CompletedProcess([], 0, "queued", "")
        self.assertEqual(ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker)["state"], "sent")

    @patch("ring_codex_mailbox.subprocess.run")
    def test_receipts_are_gitignored(self, queue):
        REAL_RUN(["git", "init", "-q", str(self.inbox)], check=True)
        queue.return_value = subprocess.CompletedProcess([], 0, "queued", "")
        ring(self.inbox, "OPEN_QUESTION", "question.md", self.worker)
        self.assertEqual((self.inbox / "doorbells" / ".gitignore").read_text(), "*\n")
        receipt = next((self.inbox / "doorbells").glob("*.json"))
        checked = REAL_RUN(["git", "-C", str(self.inbox), "check-ignore", str(receipt)], capture_output=True)
        self.assertEqual(checked.returncode, 0)

    @patch("ring_codex_mailbox.subprocess.run")
    def test_escape_is_rejected(self, queue):
        (self.inbox / "escape.md").symlink_to(Path(__file__).resolve())
        with self.assertRaises(ValueError):
            ring(self.inbox, "OPEN_QUESTION", "escape.md", self.worker)
        queue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
