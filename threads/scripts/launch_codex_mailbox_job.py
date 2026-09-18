#!/usr/bin/env python3
"""Launch a detached, mailbox-recorded job without model-visible polling.

The caller supplies a JSON contract.  This launcher validates the contract,
creates a content-addressed run directory under the Codex handoff inbox,
writes request.json atomically, starts a detached supervisor, and returns.
The supervisor writes complete logs plus one bounded result.json.  Only after
that record exists may it optionally ring the *same* Codex thread with a
path-only ``JOB_TERMINAL`` doorbell.  Cross-agent content never travels in
the doorbell; the mailbox file is authoritative.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from launch_codex_worker import LaunchError, read_state as read_worker_state


TERMINAL_STATES = {"complete", "failed", "timed_out", "cancelled"}


def die(message: str, code: int = 2) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def read_contract(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    try:
        contract = json.loads(raw)
    except json.JSONDecodeError as exc:
        die(f"invalid JSON contract {path}: {exc}")
    if contract.get("schema_version") != "1":
        die("schema_version must be the string '1'")
    name = contract.get("job_name")
    if not isinstance(name, str) or not name or not all(
        character.islower() or character.isdigit() or character == "-"
        for character in name
    ):
        die("job_name must be descriptive lowercase kebab-case")
    commands = contract.get("commands")
    if not isinstance(commands, list) or not commands:
        die("commands must be a non-empty array")
    seen_names: set[str] = set()
    for command in commands:
        command_name = command.get("name")
        argv = command.get("argv")
        if not isinstance(command_name, str) or not command_name:
            die("every command needs a non-empty name")
        if command_name in seen_names:
            die(f"duplicate command name: {command_name}")
        seen_names.add(command_name)
        if not isinstance(argv, list) or not argv or not all(
            isinstance(item, str) and item for item in argv
        ):
            die(f"command {command_name}: argv must be a non-empty string array")
        attempts = command.get("max_attempts", 1)
        if not isinstance(attempts, int) or not 1 <= attempts <= 3:
            die(f"command {command_name}: max_attempts must be 1..3")
    timeout = contract.get("timeout_seconds")
    if not isinstance(timeout, int) or timeout < 1:
        die("timeout_seconds must be a positive integer")
    parallel = contract.get("max_parallel", 1)
    if not isinstance(parallel, int) or not 1 <= parallel <= len(commands):
        die("max_parallel must be between 1 and len(commands)")
    summary = contract.get("summary", {})
    if not isinstance(summary, dict):
        die("summary must be an object")
    tail_lines = summary.get("tail_lines", 40)
    max_bytes = summary.get("max_bytes", 8192)
    if not isinstance(tail_lines, int) or not 0 <= tail_lines <= 200:
        die("summary.tail_lines must be 0..200")
    if not isinstance(max_bytes, int) or not 256 <= max_bytes <= 32768:
        die("summary.max_bytes must be 256..32768")
    doorbell = contract.get("doorbell", {"mode": "none"})
    if not isinstance(doorbell, dict) or doorbell.get("mode", "none") not in {
        "none",
        "codex-self",
    }:
        die("doorbell.mode must be 'none' or 'codex-self'")
    containment = contract.get("containment")
    if containment != {"mode": "systemd-user-scope"}:
        die("containment must be exactly {'mode': 'systemd-user-scope'}")
    return contract, raw


def git_head(workdir: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "not-a-git-worktree"
    return result.stdout.strip() or "not-a-git-worktree"


def bounded_tail(path: Path, *, line_limit: int, byte_limit: int) -> str:
    if line_limit == 0 or not path.exists():
        return ""
    data = path.read_bytes()
    if len(data) > byte_limit:
        data = data[-byte_limit:]
    text = data.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-line_limit:])


def marker_verdict(
    log_text: str,
    *,
    success_markers: list[str],
    failure_markers: list[str],
) -> tuple[list[str], list[str]]:
    missing = [marker for marker in success_markers if marker not in log_text]
    present_failures = [marker for marker in failure_markers if marker in log_text]
    return missing, present_failures


def systemctl(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *arguments],
        text=True,
        capture_output=True,
    )


def scope_details(unit: str) -> tuple[str, str]:
    shown = systemctl(
        "show",
        unit,
        "--property=ActiveState",
        "--property=ControlGroup",
    )
    if shown.returncode != 0:
        return "not-found", ""
    values = {}
    for line in shown.stdout.splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values.get("ActiveState", "unknown"), values.get("ControlGroup", "")


def cgroup_is_empty(control_group: str) -> bool:
    if not control_group:
        return True
    events = Path("/sys/fs/cgroup") / control_group.lstrip("/") / "cgroup.events"
    if not events.exists():
        return True
    for line in events.read_text().splitlines():
        if line.startswith("populated "):
            return line.split()[1] == "0"
    return False


def terminate_scope(unit: str, control_group: str, *, grace_seconds: float = 10.0) -> bool:
    """Kill the full cgroup and prove it is empty/removed before returning."""
    systemctl("kill", "--kill-who=all", "--signal=SIGTERM", unit)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        active, current_group = scope_details(unit)
        group = current_group or control_group
        if active in {"inactive", "failed", "not-found"} and cgroup_is_empty(group):
            return True
        time.sleep(0.2)
    systemctl("kill", "--kill-who=all", "--signal=SIGKILL", unit)
    systemctl("stop", unit)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        active, current_group = scope_details(unit)
        group = current_group or control_group
        if active in {"inactive", "failed", "not-found"} and cgroup_is_empty(group):
            return True
        time.sleep(0.2)
    return False


def read_control(control_path: Path) -> tuple[str | None, str | None]:
    if not control_path.exists():
        return None, None
    try:
        value = json.loads(control_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return "invalid", str(exc)
    if value.get("action") != "cancel":
        return "invalid", "only action='cancel' is supported"
    return "cancel", str(value.get("reason", "operator requested cancellation"))


def run_command(
    command: dict[str, Any],
    *,
    workdir: Path,
    run_dir: Path,
    timeout_seconds: int,
    tail_lines: int,
    max_bytes: int,
    control_path: Path,
) -> dict[str, Any]:
    command_name = command["name"]
    log_path = run_dir / f"{command_name}.log"
    success_markers = command.get("success_markers", [])
    failure_markers = command.get("failure_markers", [])
    expected_artifacts = command.get("expected_artifacts", [])
    started = time.time()
    final: dict[str, Any] | None = None
    for attempt in range(1, command.get("max_attempts", 1) + 1):
        mode = "ab" if attempt > 1 else "wb"
        with log_path.open(mode) as log_handle:
            if attempt > 1:
                log_handle.write(f"\n--- retry {attempt} ---\n".encode())
            test_process_group = (
                os.environ.get("CODEX_MAILBOX_TEST_MODE") == "1"
                and os.environ.get("CODEX_MAILBOX_CONTAINMENT")
                == "process-group-test-only"
            )
            unit_stem = "-".join(
                [
                    "codex-mailbox",
                    command_name[:24],
                    run_dir.name[-12:],
                    str(attempt),
                ]
            )
            unit = f"{unit_stem}.scope"
            argv = (
                command["argv"]
                if test_process_group
                else [
                    "systemd-run",
                    "--user",
                    "--scope",
                    "--collect",
                    "--quiet",
                    f"--unit={unit_stem}",
                    "--property=KillMode=control-group",
                    f"--property=RuntimeMaxSec={timeout_seconds + 30}s",
                    "--",
                    *command["argv"],
                ]
            )
            process = subprocess.Popen(
                argv,
                cwd=workdir,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            control_group = ""
            if not test_process_group:
                for _ in range(50):
                    _, control_group = scope_details(unit)
                    if control_group or process.poll() is not None:
                        break
                    time.sleep(0.1)
            deadline = time.monotonic() + timeout_seconds
            interrupted_state = None
            interrupted_reason = None
            cleanup_verified = True
            while process.poll() is None:
                action, reason = read_control(control_path)
                if action in {"cancel", "invalid"}:
                    interrupted_state = "cancelled" if action == "cancel" else "failed"
                    interrupted_reason = reason
                elif time.monotonic() >= deadline:
                    interrupted_state = "timed_out"
                    interrupted_reason = f"timeout after {timeout_seconds}s"
                if interrupted_state:
                    if test_process_group:
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            process.wait()
                    else:
                        cleanup_verified = terminate_scope(unit, control_group)
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    break
                time.sleep(0.2)
            return_code = process.returncode
            if not test_process_group and not interrupted_state:
                active, remaining_group = scope_details(unit)
                group = remaining_group or control_group
                if active not in {"inactive", "failed", "not-found"} or not cgroup_is_empty(group):
                    cleanup_verified = terminate_scope(unit, group)
                    if cleanup_verified:
                        interrupted_state = "failed"
                        interrupted_reason = "command exited while descendants remained; containment was killed"
            if interrupted_state and not cleanup_verified:
                interrupted_state = "failed"
                interrupted_reason = "containment cleanup failed; cancellation is not verified"
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        missing, failures = marker_verdict(
            log_text,
            success_markers=success_markers,
            failure_markers=failure_markers,
        )
        missing_artifacts = [
            artifact
            for artifact in expected_artifacts
            if not (workdir / artifact).exists()
        ]
        state = (
            interrupted_state
            if interrupted_state
            else "complete"
            if return_code == 0 and not missing and not failures and not missing_artifacts
            else "failed"
        )
        final = {
            "name": command_name,
            "state": state,
            "attempts": attempt,
            "return_code": return_code,
            "log_path": str(log_path),
            "missing_success_markers": missing,
            "present_failure_markers": failures,
            "missing_expected_artifacts": missing_artifacts,
            "containment": {
                "mode": "process-group-test-only" if test_process_group else "systemd-user-scope",
                "unit": unit if not test_process_group else None,
                "control_group": control_group if not test_process_group else None,
                "cleanup_verified": cleanup_verified,
            },
            "interrupted_reason": interrupted_reason,
            "bounded_tail": bounded_tail(
                log_path, line_limit=tail_lines, byte_limit=max_bytes
            ),
        }
        if state in {"complete", "timed_out", "cancelled"}:
            break
    assert final is not None
    final["duration_seconds"] = round(time.time() - started, 3)
    return final


def supervise(request_path: Path) -> int:
    request = json.loads(request_path.read_text())
    contract = request["contract"]
    run_dir = request_path.parent
    workdir = Path(request["working_directory"])
    control_path = run_dir / "control.json"
    summary = contract.get("summary", {})
    atomic_json(
        run_dir / "state.json",
        {"schema_version": "1", "state": "running", "job_name": contract["job_name"]},
    )
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=contract.get("max_parallel", 1)
    ) as executor:
        futures = [
            executor.submit(
                run_command,
                command,
                workdir=workdir,
                run_dir=run_dir,
                timeout_seconds=contract["timeout_seconds"],
                tail_lines=summary.get("tail_lines", 40),
                max_bytes=summary.get("max_bytes", 8192),
                control_path=control_path,
            )
            for command in contract["commands"]
        ]
        command_results = [future.result() for future in futures]
    states = {result["state"] for result in command_results}
    state = (
        "failed"
        if "failed" in states
        else "timed_out"
        if "timed_out" in states
        else "cancelled"
        if "cancelled" in states
        else "complete"
    )
    result_path = run_dir / "result.json"
    result = {
        "schema_version": "1",
        "job_name": contract["job_name"],
        "state": state,
        "source_head": request["source_head"],
        "contract_sha256": request["contract_sha256"],
        "commands": command_results,
        "finished_unix_seconds": int(time.time()),
    }
    atomic_json(result_path, result)
    atomic_json(
        run_dir / "state.json",
        {"schema_version": "1", "state": state, "job_name": contract["job_name"]},
    )

    doorbell = contract.get("doorbell", {"mode": "none"})
    if doorbell.get("mode", "none") == "codex-self":
        thread_id = request.get("codex_thread_id")
        doorbell_result: dict[str, Any]
        if not binding_still_matches(request.get("worker_binding", {})):
            doorbell_result = {
                "state": "failed",
                "reason": "worker launch/session binding changed; refusing a stale or wrong-thread doorbell",
            }
        elif not thread_id:
            doorbell_result = {
                "state": "skipped",
                "reason": "CODEX_THREAD_ID was absent; resume the worker manually",
            }
        else:
            queue_binary = os.environ.get("CODEX_QUEUE_BINARY", "codex")
            completed = subprocess.run(
                [
                    queue_binary,
                    "queue",
                    "--thread",
                    thread_id,
                    "--message",
                    f"JOB_TERMINAL {result_path}",
                ],
                text=True,
                capture_output=True,
            )
            doorbell_result = {
                "state": "sent" if completed.returncode == 0 else "failed",
                "return_code": completed.returncode,
                "stderr_tail": "\n".join(completed.stderr.splitlines()[-10:]),
            }
        atomic_json(run_dir / "doorbell.json", doorbell_result)
    return 0 if state == "complete" else 1


def request_cancel(run_dir: Path, reason: str) -> int:
    state_path = run_dir / "state.json"
    if not state_path.exists():
        die(f"state.json not found: {state_path}")
    state = json.loads(state_path.read_text()).get("state")
    if state in TERMINAL_STATES:
        die(f"job is already terminal ({state}); cancellation was not written")
    control_path = run_dir / "control.json"
    if control_path.exists():
        die(f"control request already exists: {control_path}")
    atomic_json(
        control_path,
        {
            "schema_version": "1",
            "action": "cancel",
            "reason": reason,
            "requested_unix_seconds": int(time.time()),
        },
    )
    print(f"CANCEL_REQUESTED {control_path}")
    print("End the model turn. Cancellation is complete only when result.json says cancelled and cleanup_verified is true for every command.")
    return 0


def resolve_worker_binding(inbox: Path, contract: dict[str, Any]) -> dict[str, Any]:
    if contract.get("doorbell", {}).get("mode", "none") != "codex-self":
        return {"mode": "none"}
    environment_thread_id = os.environ.get("CODEX_THREAD_ID")
    if not environment_thread_id:
        die("codex-self doorbell requires CODEX_THREAD_ID")
    state_path = inbox.resolve() / "worker-state.json"
    if not state_path.exists():
        if os.environ.get("CODEX_MAILBOX_TEST_MODE") == "1":
            return {
                "mode": "test-unverified",
                "session_id": environment_thread_id,
            }
        die(f"codex-self doorbell requires launcher-owned worker state: {state_path}")
    try:
        raw_state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read worker state for doorbell binding: {exc}")
    if raw_state.get("schema_version") not in {"2", "3"}:
        # Migration boundary for packets already running when v2 was installed.
        # A v2 record falls through to read_worker_state and is refused loudly:
        # v3 replaced mailbox.questions with mailbox.messages, and the skill is
        # cut over only with no live worker.
        return {
            "mode": "legacy-v1",
            "session_id": environment_thread_id,
            "state_path": str(state_path),
        }
    try:
        state = read_worker_state(state_path)
    except LaunchError as exc:
        die(f"invalid worker state for doorbell binding: {exc}")
    bound_session = state.get("session_id")
    if not bound_session:
        die("worker session is not bound; run launch_codex_worker.py bind-session first")
    if bound_session != environment_thread_id:
        die(
            "CODEX_THREAD_ID does not match worker-state session_id; refusing a wrong-thread doorbell"
        )
    return {
        "mode": "verified-v2",
        "session_id": bound_session,
        "worker_run_id": state["worker_run_id"],
        "state_path": str(state_path),
    }


def binding_still_matches(binding: dict[str, Any]) -> bool:
    if binding.get("mode") != "verified-v2":
        return True
    try:
        state = read_worker_state(Path(binding["state_path"]))
    except (LaunchError, KeyError):
        return False
    return (
        state.get("worker_run_id") == binding.get("worker_run_id")
        and state.get("session_id") == binding.get("session_id")
    )


def launch(contract_path: Path, inbox: Path, *, foreground: bool) -> int:
    contract, raw = read_contract(contract_path)
    worker_binding = resolve_worker_binding(inbox, contract)
    workdir_raw = contract.get("working_directory", ".")
    workdir = (contract_path.parent / workdir_raw).resolve()
    if not workdir.is_dir():
        die(f"working_directory is not a directory: {workdir}")
    contract_digest = hashlib.sha256(raw).hexdigest()
    source_head = git_head(workdir)
    run_key = f"{source_head[:12]}-{contract_digest[:12]}"
    run_dir = inbox.resolve() / "jobs" / contract["job_name"] / run_key
    request_path = run_dir / "request.json"
    result_path = run_dir / "result.json"
    state_path = run_dir / "state.json"
    if state_path.exists():
        existing = json.loads(state_path.read_text())
        die(
            f"run already exists in state {existing.get('state')}: {run_dir}; "
            "change the source or contract instead of duplicating it"
        )
    request = {
        "schema_version": "1",
        "job_name": contract["job_name"],
        "contract_sha256": contract_digest,
        "source_head": source_head,
        "working_directory": str(workdir),
        "codex_thread_id": worker_binding.get("session_id"),
        "worker_binding": worker_binding,
        "contract": contract,
        "created_unix_seconds": int(time.time()),
    }
    atomic_json(request_path, request)
    atomic_json(
        state_path,
        {"schema_version": "1", "state": "queued", "job_name": contract["job_name"]},
    )
    if foreground:
        return supervise(request_path)
    with (run_dir / "supervisor.log").open("ab") as supervisor_log:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--supervise", str(request_path)],
            stdin=subprocess.DEVNULL,
            stdout=supervisor_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    print(
        json.dumps(
            {
                "job_name": contract["job_name"],
                "state": "queued",
                "supervisor_pid": process.pid,
                "request_path": str(request_path),
                "result_path": str(result_path),
                "instruction": "End the model turn. Read result.json only after JOB_TERMINAL or manual resume.",
            },
            sort_keys=True,
        )
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch a detached job that reports through a Codex handoff mailbox"
    )
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--inbox", type=Path)
    parser.add_argument("--foreground", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--supervise", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--cancel", type=Path, help="Run directory to cancel through control.json")
    parser.add_argument("--reason", default="operator redirected the worker")
    args = parser.parse_args()
    if args.supervise:
        raise SystemExit(supervise(args.supervise.resolve()))
    if args.cancel:
        raise SystemExit(request_cancel(args.cancel.resolve(), args.reason))
    if not args.contract or not args.inbox:
        parser.error("--contract and --inbox are required")
    raise SystemExit(
        launch(args.contract.resolve(), args.inbox.resolve(), foreground=args.foreground)
    )


if __name__ == "__main__":
    main()
