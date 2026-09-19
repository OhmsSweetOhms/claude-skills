#!/usr/bin/env python3
"""Launch a foreground Codex worker with an atomic mailbox lifecycle record.

The Codex TUI remains attached to the operator's terminal.  This wrapper owns
only worker-process lifecycle; long Vivado/Xsim jobs remain the responsibility
of launch_codex_mailbox_job.py.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = "3"   # 3: mailbox.messages (the one shared mailbox.md) replaced mailbox.questions
HANDOFF_SCHEMA = Path(__file__).resolve().parent.parent / "assets" / "schemas" / "codex-handback.schema.json"
WORKER_SCHEMA = Path(__file__).resolve().parent.parent / "assets" / "schemas" / "codex-worker-state.schema.json"
ALLOWED_UPDATES = {
    "running": {"blocked", "completed", "failed"},
    "blocked": {"running", "completed", "failed"},
}


class LaunchError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def die(message: str, code: int = 2) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def git_value(worktree: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise LaunchError(result.stderr.strip() or f"git {' '.join(args)} failed")
    value = result.stdout.strip()
    if not value:
        raise LaunchError(f"git {' '.join(args)} returned no value")
    return value


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


@contextmanager
def state_lock(inbox: Path) -> Iterator[None]:
    inbox.mkdir(parents=True, exist_ok=True)
    lock_path = inbox / ".worker-state.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def read_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LaunchError(f"worker state does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise LaunchError(f"cannot read worker state {path}: {exc}") from exc
    validate_state(data)
    return data


def validate_state(data: object) -> None:
    try:
        import jsonschema
        schema = json.loads(WORKER_SCHEMA.read_text(encoding="utf-8"))
        jsonschema.Draft7Validator(
            schema, format_checker=jsonschema.FormatChecker()
        ).validate(data)
    except Exception as exc:
        raise LaunchError(f"worker state is not schema-valid: {exc}") from exc
    assert isinstance(data, dict)
    if data["events"][-1]["state"] != data["state"]:
        raise LaunchError("last worker event state must equal top-level state")
    if data["events"][-1]["at"] != data["updated_at"]:
        raise LaunchError("updated_at must equal the last worker event timestamp")
    transitions = {
        "WORKER_LAUNCHED": {("starting", "running")},
        "WORKER_SESSION_BOUND": {("running", "running"), ("blocked", "blocked")},
        "WORKER_RUNNING": {("blocked", "running")},
        "WORKER_BLOCKED": {("running", "blocked")},
        "WORKER_COMPLETED": {("running", "completed"), ("blocked", "completed")},
        "WORKER_FAILED": {("starting", "failed"), ("running", "failed"), ("blocked", "failed")},
        "WORKER_EXITED": {("starting", "exited"), ("running", "exited")},
        "WORKER_PROCESS_EXITED": {
            ("blocked", "blocked"), ("completed", "completed"), ("failed", "failed")
        },
        "WORKER_LAUNCH_FAILED": {("starting", "failed")},
    }
    events = data["events"]
    if events[0]["event"] != "WORKER_STARTING" or events[0]["state"] != "starting":
        raise LaunchError("first worker event must be WORKER_STARTING")
    prior_state = "starting"
    prior_time = events[0]["at"]
    for event in events[1:]:
        transition = (prior_state, event["state"])
        if transition not in transitions.get(event["event"], set()):
            raise LaunchError(
                f"illegal event transition {prior_state} --{event['event']}--> {event['state']}"
            )
        if event["at"] < prior_time:
            raise LaunchError("worker event timestamps must be monotonic")
        prior_state = event["state"]
        prior_time = event["at"]
    process = data["process"]
    if process["state"] == "starting" and process["worker_pid"] is not None:
        raise LaunchError("starting process cannot already have worker_pid")
    if process["state"] == "running":
        if process["worker_pid"] is None or process["worker_identity"] is None:
            raise LaunchError("running process requires worker PID identity")
        if process["started_at"] is None:
            raise LaunchError("running process requires started_at")
    if process["state"] == "exited":
        if process["exited_at"] is None or process["exit_code"] is None:
            raise LaunchError("exited process requires exited_at and exit_code")


def append_event(state: dict, event: str, lifecycle_state: str, detail: str | None = None) -> None:
    at = utc_now()
    row = {"event": event, "at": at, "state": lifecycle_state}
    if detail:
        row["detail"] = detail
    state["events"].append(row)
    state["state"] = lifecycle_state
    state["updated_at"] = at


def process_identity(pid: int) -> dict | None:
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        stat_text = Path(f"/proc/{pid}/stat").read_text()
        tail = stat_text[stat_text.rfind(") ") + 2:].split()
        start_ticks = int(tail[19])
    except (OSError, ValueError, IndexError):
        return None
    return {"pid": pid, "boot_id": boot_id, "start_ticks": start_ticks}


def identity_is_live(value: object) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("pid"), int):
        return False
    return process_identity(value["pid"]) == value


def existing_worker_is_live(state: dict) -> bool:
    process = state.get("process", {})
    if process.get("state") not in {"starting", "running"}:
        return False
    return identity_is_live(process.get("worker_identity")) or identity_is_live(
        process.get("launcher_identity")
    )


def read_conforming_handback_status(inbox: Path, state: dict) -> str | None:
    handback_path = inbox / "handback.json"
    if not handback_path.exists():
        return None
    try:
        handback = json.loads(handback_path.read_text(encoding="utf-8"))
        schema = json.loads(HANDOFF_SCHEMA.read_text(encoding="utf-8"))
        import jsonschema
        jsonschema.Draft7Validator(schema).validate(handback)
    except Exception as exc:
        raise LaunchError(f"handback.json is not schema-valid: {exc}") from exc
    if handback.get("plan_id") != state.get("plan_id"):
        raise LaunchError("handback.json plan_id does not match worker state")
    if handback.get("thread_id") != state.get("thread_id"):
        raise LaunchError("handback.json thread_id does not match worker state")
    return handback.get("status")


def turn1_prompt(turn1_file: Path) -> str:
    """The one sentence Codex is started with. Turn 1 itself stays in the file:
    only this pointer goes on the command line, so nothing long is ever rendered
    into argv or pasted into the TUI. The emitter prints the same sentence as
    the manual fallback; this is its only home."""
    return (
        f"Read {turn1_file} in full and follow it as your turn-1 instructions; "
        "do not summarize it back, start executing."
    )


def archive_existing_state(inbox: Path, state: dict) -> Path:
    history = inbox / "worker-state-history"
    history.mkdir(parents=True, exist_ok=True)
    destination = history / f"{state['worker_run_id']}.json"
    if destination.exists():
        raise LaunchError(f"worker-state archive already exists: {destination}")
    atomic_write_json(destination, state)
    return destination


def launch(args: argparse.Namespace) -> int:
    worktree = Path.cwd().resolve()
    inbox = Path(args.inbox).expanduser().resolve()
    try:
        inbox.relative_to(worktree)
    except ValueError as exc:
        raise LaunchError(f"inbox must be inside the current worktree: {inbox}") from exc

    turn1_file = Path(args.turn1_file).expanduser().resolve()
    if not turn1_file.is_file() or not turn1_file.read_text(encoding="utf-8").strip():
        raise LaunchError(f"turn-1 file is missing or empty: {turn1_file}; regenerate packet")

    branch = git_value(worktree, "branch", "--show-current")
    source_head = git_value(worktree, "rev-parse", "HEAD")
    if branch != args.expected_branch:
        raise LaunchError(
            f"branch mismatch: packet expects {args.expected_branch!r}, worktree is {branch!r}"
        )
    if not source_head.startswith(args.expected_head):
        raise LaunchError(
            f"HEAD mismatch: packet expects {args.expected_head}, worktree is {source_head}; regenerate packet"
        )

    state_path = inbox / "worker-state.json"
    with state_lock(inbox):
        if state_path.exists():
            previous = read_state(state_path)
            if existing_worker_is_live(previous):
                raise LaunchError(
                    f"refusing duplicate live worker {previous['worker_run_id']} at {state_path}"
                )
            if not args.relaunch:
                raise LaunchError(
                    f"worker state already exists at {state_path}; inspect it and pass --relaunch for a new attempt"
                )
            archive_existing_state(inbox, previous)

        now = utc_now()
        run_id = str(uuid.uuid4())
        launcher_identity = process_identity(os.getpid())
        if launcher_identity is None:
            raise LaunchError("cannot establish launcher process identity from /proc")
        state = {
            "schema_version": SCHEMA_VERSION,
            "worker_run_id": run_id,
            "plan_id": args.plan_id,
            "thread_id": args.thread_id,
            "state": "starting",
            "source_head": source_head,
            "launch": {
                "requested_at": now,
                "worktree": str(worktree),
                "branch": branch,
                "model": args.model,
                "reasoning_effort": args.reasoning_effort,
                "auto_compact_token_limit": args.auto_compact_token_limit,
            },
            "process": {
                "state": "starting",
                "launcher_pid": os.getpid(),
                "launcher_identity": launcher_identity,
                "worker_pid": None,
                "worker_identity": None,
                "started_at": None,
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
            "session_id": None,
            "reason": None,
            "events": [{"event": "WORKER_STARTING", "at": now, "state": "starting"}],
            "updated_at": now,
        }
        atomic_write_json(state_path, state)

    command = [
        args.codex_bin,
        "--model", args.model,
        "-c", f'model_reasoning_effort="{args.reasoning_effort}"',
        "-c", f"model_auto_compact_token_limit={args.auto_compact_token_limit}",
        turn1_prompt(turn1_file),
    ]
    child: subprocess.Popen | None = None
    abort_before_start = False
    terminate_before_start: int | None = None
    previous_handlers = {
        signum: signal.getsignal(signum)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    }

    def handle_launch_signal(signum: int, _frame: object) -> None:
        nonlocal abort_before_start, terminate_before_start
        if child is None:
            abort_before_start = True
            terminate_before_start = signum
        elif signum != signal.SIGINT and child.poll() is None:
            child.send_signal(signum)

    for signum in previous_handlers:
        signal.signal(signum, handle_launch_signal)

    gate_read, gate_write = os.pipe()
    gated_command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--gate-exec",
        str(gate_read),
        *command,
    ]

    try:
        child = subprocess.Popen(
            gated_command,
            cwd=worktree,
            pass_fds=(gate_read,),
        )
        os.close(gate_read)
    except OSError as exc:
        os.close(gate_read)
        os.close(gate_write)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        with state_lock(inbox):
            state = read_state(state_path)
            state["process"].update({
                "state": "exited",
                "exited_at": utc_now(),
                "exit_code": 127,
            })
            append_event(state, "WORKER_LAUNCH_FAILED", "failed", str(exc))
            state["reason"] = str(exc)
            atomic_write_json(state_path, state)
        print(f"WORKER_LAUNCH_FAILED {state_path}", flush=True)
        raise LaunchError(f"could not start Codex: {exc}") from exc

    worker_identity = process_identity(child.pid)
    if worker_identity is None:
        child.kill()
        os.close(gate_write)
        exit_code = child.wait()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        with state_lock(inbox):
            state = read_state(state_path)
            state["process"].update({
                "state": "exited",
                "worker_pid": child.pid,
                "exited_at": utc_now(),
                "exit_code": exit_code,
            })
            append_event(
                state, "WORKER_LAUNCH_FAILED", "failed",
                "cannot establish worker process identity from /proc",
            )
            atomic_write_json(state_path, state)
        raise LaunchError("cannot establish worker process identity from /proc")

    try:
        with state_lock(inbox):
            state = read_state(state_path)
            if state["worker_run_id"] != run_id:
                raise LaunchError("worker state changed during launch")
            started_at = utc_now()
            state["process"].update({
                "state": "running",
                "worker_pid": child.pid,
                "worker_identity": worker_identity,
                "started_at": started_at,
            })
            append_event(state, "WORKER_LAUNCHED", "running")
            atomic_write_json(state_path, state)
    except BaseException:
        child.kill()
        os.close(gate_write)
        child.wait()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        raise
    os.write(gate_write, b"1")
    os.close(gate_write)
    print(f"WORKER_LAUNCHED {state_path}", flush=True)
    if abort_before_start and child.poll() is None:
        child.send_signal(terminate_before_start or signal.SIGTERM)
    try:
        while True:
            try:
                exit_code = child.wait()
                break
            except InterruptedError:
                continue
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    with state_lock(inbox):
        state = read_state(state_path)
        if state["worker_run_id"] != run_id:
            raise LaunchError("worker state run id changed before process exit")
        state["process"].update({
            "state": "exited",
            "exited_at": utc_now(),
            "exit_code": exit_code,
        })
        handback_error = None
        try:
            handback_status = read_conforming_handback_status(inbox, state)
        except LaunchError as exc:
            handback_status = None
            handback_error = str(exc)

        if state["state"] in {"starting", "running"}:
            if handback_status in {"complete", "scope-cut"}:
                event, lifecycle = "WORKER_COMPLETED", "completed"
            elif handback_status in {"blocked", "gate-incomplete"}:
                event, lifecycle = "WORKER_BLOCKED", "blocked"
            elif exit_code == 0:
                event, lifecycle = "WORKER_EXITED", "exited"
            else:
                event, lifecycle = "WORKER_FAILED", "failed"
            detail = f"codex exit code {exit_code}"
            if handback_error:
                detail += f"; {handback_error}"
            append_event(state, event, lifecycle, detail)
        else:
            append_event(
                state,
                "WORKER_PROCESS_EXITED",
                state["state"],
                f"codex exit code {exit_code}",
            )
        atomic_write_json(state_path, state)
        final_event = state["events"][-1]["event"]
    print(f"{final_event} {state_path}", flush=True)
    return exit_code if exit_code >= 0 else 128 + abs(exit_code)


def update(args: argparse.Namespace) -> int:
    inbox = Path(args.inbox).expanduser().resolve()
    state_path = inbox / "worker-state.json"
    with state_lock(inbox):
        state = read_state(state_path)
        allowed = ALLOWED_UPDATES.get(state["state"], set())
        if args.state not in allowed:
            raise LaunchError(
                f"illegal worker transition {state['state']} -> {args.state}"
            )
        if args.state in {"blocked", "failed"} and not args.reason:
            raise LaunchError(f"--reason is required for state {args.state}")
        if args.state == "completed":
            if read_conforming_handback_status(inbox, state) not in {"complete", "scope-cut"}:
                raise LaunchError(
                    "completed requires a schema-valid handback.json for this plan/thread "
                    "with status complete or scope-cut"
                )
        verb = {
            "running": "WORKER_RUNNING",
            "blocked": "WORKER_BLOCKED",
            "completed": "WORKER_COMPLETED",
            "failed": "WORKER_FAILED",
        }[args.state]
        append_event(state, verb, args.state, args.reason)
        if args.reason:
            state["reason"] = args.reason
        elif args.state == "running":
            state["reason"] = None
        if args.session_id:
            state["session_id"] = args.session_id
        atomic_write_json(state_path, state)
    print(f"{verb} {state_path}")
    return 0


def bind_session(args: argparse.Namespace) -> int:
    inbox = Path(args.inbox).expanduser().resolve()
    state_path = inbox / "worker-state.json"
    with state_lock(inbox):
        state = read_state(state_path)
        if state["state"] not in {"running", "blocked"}:
            raise LaunchError(
                f"cannot bind a session while worker state is {state['state']}"
            )
        existing = state.get("session_id")
        if existing and existing != args.session_id:
            raise LaunchError(
                f"worker is already bound to a different session: {existing}"
            )
        if existing == args.session_id:
            print(f"WORKER_SESSION_BOUND {state_path}")
            return 0
        state["session_id"] = args.session_id
        append_event(
            state, "WORKER_SESSION_BOUND", state["state"],
            "foreground Codex session bound for self-doorbells",
        )
        atomic_write_json(state_path, state)
    print(f"WORKER_SESSION_BOUND {state_path}")
    return 0


def validate(args: argparse.Namespace) -> int:
    state_path = Path(args.inbox).expanduser().resolve() / "worker-state.json"
    read_state(state_path)
    print(f"VALID {state_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch and record a foreground Codex worker",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    launch_parser = subparsers.add_parser("launch", help="launch a new foreground worker")
    launch_parser.add_argument("--inbox", required=True)
    launch_parser.add_argument("--thread-id", required=True)
    launch_parser.add_argument("--plan-id", required=True)
    launch_parser.add_argument("--expected-branch", required=True)
    launch_parser.add_argument("--expected-head", required=True)
    launch_parser.add_argument("--model", required=True)
    launch_parser.add_argument(
        "--reasoning-effort",
        required=True,
        choices=("none", "low", "medium", "high", "xhigh", "max"),
    )
    launch_parser.add_argument("--auto-compact-token-limit", required=True, type=int)
    launch_parser.add_argument("--turn1-file", required=True)
    launch_parser.add_argument("--codex-bin", default="codex")
    launch_parser.add_argument("--relaunch", action="store_true")
    launch_parser.set_defaults(func=launch)

    update_parser = subparsers.add_parser("update", help="record a semantic lifecycle transition")
    update_parser.add_argument("--inbox", required=True)
    update_parser.add_argument(
        "--state", required=True,
        choices=("running", "blocked", "completed", "failed"),
    )
    update_parser.add_argument("--reason")
    update_parser.add_argument("--session-id")
    update_parser.set_defaults(func=update)

    bind_parser = subparsers.add_parser(
        "bind-session", help="bind the foreground Codex thread for self-doorbells"
    )
    bind_parser.add_argument("--inbox", required=True)
    bind_parser.add_argument("--session-id", required=True)
    bind_parser.set_defaults(func=bind_session)

    validate_parser = subparsers.add_parser("validate", help="validate the current worker state")
    validate_parser.add_argument("--inbox", required=True)
    validate_parser.set_defaults(func=validate)
    return parser


def record_refused_launch(inbox: str, message: str) -> None:
    """Keep the refusal where the orchestrator can read it. The message went to a
    pane that dies with its window; `fire_codex_worker.py` sends this file as the
    body of a FIRE_FAILED block. Best effort: a refusal must still be a refusal."""
    try:
        path = Path(inbox).expanduser().resolve() / "fire-failed.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log:
            log.write(f"{utc_now()} launcher refused: {message}\n")
    except OSError:
        pass


def main() -> int:
    if len(sys.argv) >= 4 and sys.argv[1] == "--gate-exec":
        gate_fd = int(sys.argv[2])
        command = sys.argv[3:]
        token = os.read(gate_fd, 1)
        os.close(gate_fd)
        if token != b"1":
            return 125
        os.execvp(command[0], command)
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "auto_compact_token_limit", 1) <= 0:
        die("--auto-compact-token-limit must be positive")
    try:
        return args.func(args)
    except LaunchError as exc:
        if args.command == "launch":
            record_refused_launch(args.inbox, str(exc))
        die(str(exc))
    return 2


if __name__ == "__main__":
    sys.exit(main())
