#!/usr/bin/env python3
"""Deliver an explicitly routed, file-first Codex mailbox event once."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from answer_question import find_status, resolution_body_nonempty


def atomic_json(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".doorbell-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def ring(inbox: Path, event: str, relative_file: str, sender: str,
         retry_failed_reason: str | None = None) -> dict:
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    if not os.access(codex_home, os.W_OK):
        raise ValueError("HOST_EXECUTION_REQUIRED: Codex home is not writable; no delivery attempted")
    if retry_failed_reason is not None and not retry_failed_reason.strip():
        raise ValueError("retry requires a nonempty confirmed-nondelivery reason")
    inbox = inbox.resolve(strict=True)
    route = json.loads((inbox / "codex-mailbox-route.json").read_text())
    if set(route) != {"orchestrator", "worker"}:
        raise ValueError("route must name exactly orchestrator and worker")
    for identity in route.values():
        if str(uuid.UUID(identity)) != identity:
            raise ValueError("route requires canonical session UUIDs")
    if route["orchestrator"] == route["worker"]:
        raise ValueError("orchestrator and worker must be distinct sessions")
    source, target = ("orchestrator", "worker") if event == "ANSWER_READY" else ("worker", "orchestrator")
    if sender != route[source]:
        raise ValueError(f"CODEX_THREAD_ID is not the bound {source}")
    if Path(relative_file).is_absolute():
        raise ValueError("--file must be inbox-relative")
    record = (inbox / relative_file).resolve(strict=True)
    record.relative_to(inbox)
    if not record.is_file() or "\n" in str(record) or "\r" in str(record):
        raise ValueError("event must reference a regular mailbox file")
    content = record.read_bytes()
    if event in {"OPEN_QUESTION", "ANSWER_READY"}:
        match, _ = find_status(content.decode())
        expected = "open" if event == "OPEN_QUESTION" else "answered"
        if not match or match.group(2).lower() != expected:
            raise ValueError(f"{event} requires status {expected}")
        if event == "ANSWER_READY" and not resolution_body_nonempty(content.decode()):
            raise ValueError("ANSWER_READY requires a nonempty resolution")
    elif event == "HANDBACK":
        if not isinstance(json.loads(content), dict):
            raise ValueError("HANDBACK requires a JSON object; consumer validates its schema")
    else:
        raise ValueError("unsupported event")
    message = f"{event} {record}"
    key = hashlib.sha256(json.dumps([route, message, hashlib.sha256(content).hexdigest()], sort_keys=True).encode()).hexdigest()
    receipts = inbox / "doorbells"
    receipts.mkdir(exist_ok=True)
    ignore = receipts / ".gitignore"
    try:
        with ignore.open("x") as handle:
            handle.write("*\n")
    except FileExistsError:
        if ignore.read_text() != "*\n":
            raise ValueError("receipt ignore policy differs; inspect before delivery")
    with (receipts / ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receipt = receipts / f"{key}.json"
        result = {"state": "sending", "event": event, "target": route[target], "file": relative_file}
        if receipt.exists():
            previous = json.loads(receipt.read_text())
            if previous["state"] == "sent":
                return {"state": "duplicate", "receipt": str(receipt)}
            if (not retry_failed_reason or previous["state"] != "failed"
                    or "previous_attempt" in previous):
                raise ValueError(f"delivery already attempted; inspect {receipt} before recovery")
            result.update(previous_attempt=previous, recovery_reason=retry_failed_reason.strip())
        elif retry_failed_reason is not None:
            raise ValueError("retry requires an existing failed receipt")
        atomic_json(receipt, result)
        try:
            queued = subprocess.run(["codex", "queue", "--thread", route[target], "--message", message],
                                    capture_output=True, text=True, timeout=20)
            result.update(state="sent" if queued.returncode == 0 else "failed",
                          exit_code=queued.returncode, stdout=queued.stdout[-2048:], stderr=queued.stderr[-2048:])
        except (OSError, subprocess.TimeoutExpired) as exc:
            result.update(state="uncertain", error=str(exc))
        atomic_json(receipt, result)
        if result["state"] != "sent":
            raise ValueError(f"doorbell {result['state']}; inspect {receipt}")
        return {"state": "sent", "receipt": str(receipt)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inbox", required=True, type=Path)
    parser.add_argument("--event", required=True, choices=["OPEN_QUESTION", "ANSWER_READY", "HANDBACK"])
    parser.add_argument("--file", required=True)
    parser.add_argument("--retry-failed-reason",
                        help="one authorized retry after confirming non-delivery; retains original receipt")
    args = parser.parse_args()
    try:
        print(json.dumps(ring(args.inbox, args.event, args.file, os.environ.get("CODEX_THREAD_ID", ""),
                              args.retry_failed_reason)))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"doorbell refused: {exc}\n")


if __name__ == "__main__":
    main()
