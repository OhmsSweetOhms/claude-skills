#!/usr/bin/env python3
"""One shared mailbox file between a Claude orchestrator and a Codex worker.

The file (`<inbox>/mailbox.md`) is append-only and is never rewritten. Everything
in it is a block:

    === 7 | worker -> orchestrator | 2026-09-18T15:02:11Z | QUESTION
    free text, as long as the sender likes
    === end 7

Nobody edits the file by hand. `send` takes a file lock, numbers the block,
composes header + body + end marker in memory and appends them in ONE write, so
a half-written block never exists and the end marker is never the agent's job.

**Nothing here types.** A doorbell that types into a pane is a keyboard for
whatever is on screen: a pane in tmux copy mode swallows it as key bindings, and
a pane at a permission dialog ANSWERS it (both measured 2026-09-18). The two
wakes are the ones the harnesses already provide:

  worker  <- `codex queue --thread <session_id>`, run by `send --to worker` on
             the host, right after the append. The worker's session id is
             `worker-state.json.session_id` beside the mailbox.
  orchestrator <- `wait`, run by a Claude Code `Stop` hook with `asyncRewake`.
             It blocks on the mailboxes this session `watch`es and exits 2 with
             a `MAILBOX <n> <path>` line, which reaches the session as hook
             feedback — labelled, not impersonating a user turn.

A `MAILBOX <n> <path>` line is a POINTER to the file, never an instruction: read
the block and act on the BLOCK under normal authority.

Usage:
  mb.py send <mailbox> --from R --to R --kind K [--body T | --body-file F | stdin]
  mb.py read <mailbox> [n]
  mb.py pending <mailbox> --role R
  mb.py watch <mailbox> [--session-id ID]      (default $CLAUDE_CODE_SESSION_ID)
  mb.py wait [--deadline S] [--poll S]         (Stop hook: session_id on stdin)

Roles: orchestrator, worker. Kinds are uppercase tokens; the worker rules name
QUESTION, ANSWER, HANDBACK, NOTE and ACK (the worker's one-line "I have this,
here is what I am about to do", sent the moment it consumes a pointer).

Exit codes: 0 ok; 2 refused (nothing written); 3 from `send` only — the block IS
in the file but the doorbell was skipped, with the reason on the `RING_SKIPPED`
line. `wait` is a hook, so its codes are the harness's: 0 nothing to say, 2 wake
the session (stderr is the message), 1 a broken invocation.

Environment:
  CLAUDE_CODE_SESSION_ID  the session `watch` registers (Claude Code sets it)
  XDG_STATE_HOME          where the watch registry lives (default ~/.local/state)
  MB_CODEX_BIN            the `codex` binary `send --to worker` rings (tests
                          point this at a fake; a unit test never spends a turn)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROLES = ("orchestrator", "worker")
HEADER = re.compile(r"^=== (\d+) \| (\w+) -> (\w+) \| (\S+) \| ([A-Z][A-Z0-9_]*)$")
END = re.compile(r"^=== end (\d+)$")
KIND = re.compile(r"^[A-Z][A-Z0-9_]*$")
SESSION_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")   # also a filename in the registry

# The waiter serves the orchestrator role only: a Claude WORKER on a mailbox is
# not a shape this harness has yet, and a field nobody can set is not a design.
WAIT_ROLE = "orchestrator"
WAIT_DEADLINE_S = 3300.0   # under the hook's 3600 s timeout, which kills SILENTLY
WAIT_POLL_S = 0.5
QUEUE_TIMEOUT_S = 20.0


# ---------------------------------------------------------------- the file


def parse(text: str) -> list[dict]:
    """Every block in file order. A block whose end marker is missing (or whose
    header is followed by another header) is returned with complete=False."""
    blocks: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        head = HEADER.match(line)
        if head:
            if cur is not None:
                blocks.append(cur)          # unterminated: a new header cut it off
            cur = {"n": int(head.group(1)), "sender": head.group(2),
                   "to": head.group(3), "at": head.group(4),
                   "kind": head.group(5), "body": [], "complete": False}
            continue
        end = END.match(line)
        if end and cur is not None and int(end.group(1)) == cur["n"]:
            cur["complete"] = True
            blocks.append(cur)
            cur = None
            continue
        if cur is not None:
            cur["body"].append(line)
    if cur is not None:
        blocks.append(cur)
    for b in blocks:
        b["body"] = "\n".join(b["body"])
    return blocks


def read_blocks(mailbox: Path) -> list[dict]:
    try:
        return parse(mailbox.read_text(errors="replace"))
    except FileNotFoundError:
        return []


def send(mailbox: Path, sender: str, to: str, kind: str, body: str) -> int:
    if sender not in ROLES:
        raise ValueError(f"--from must be one of {ROLES}")
    if to not in ROLES or to == sender:
        raise ValueError(f"--to must be one of {ROLES} and differ from --from")
    if not KIND.match(kind):
        raise ValueError("--kind must be an uppercase token, e.g. QUESTION")
    if not body.strip():
        raise ValueError("refusing to send an empty body")
    # A body line that starts with the marker prefix is indented one space so it
    # can never be read as a header or an end marker.
    safe = "\n".join(" " + l if l.startswith("===") else l
                     for l in body.rstrip("\n").splitlines())
    mailbox.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(mailbox, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        text = mailbox.read_text(errors="replace")
        n = max((b["n"] for b in parse(text)), default=0) + 1
        now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        lead = "" if not text or text.endswith("\n") else "\n"
        block = (f"{lead}\n=== {n} | {sender} -> {to} | {now} | {kind}\n"
                 f"{safe}\n=== end {n}\n").encode()
        view = memoryview(block)
        while view:                          # one write in practice; never stop short
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)                         # closing drops the lock
    return n


def pending(mailbox: Path, role: str) -> list[str]:
    blocks = read_blocks(mailbox)
    lines = [f"UNTERMINATED_BLOCK {b['n']} {mailbox}" for b in blocks if not b["complete"]]
    talk = [b for b in blocks if b["complete"]]
    if talk and talk[-1]["to"] == role:
        lines.append(f"PENDING {talk[-1]['n']} {talk[-1]['kind']} {mailbox}")
    return lines


# ------------------------------------------------- the worker's own state


def worker_state(mailbox: Path) -> dict:
    """The worker record beside the mailbox, as plain JSON.

    Only two facts are read — `session_id` and whether the worker is over — so
    the mailbox skill stays usable without the threads skill. It never writes
    this file and never validates its schema; the launcher owns both.
    """
    path = mailbox.parent / "worker-state.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LookupError(f"no worker-state.json beside {mailbox}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise LookupError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise LookupError(f"{path} is not a worker record")
    return data


def process_identity(pid: int) -> dict | None:
    """The launcher's own identity shape, field for field, so the two agree.
    Duplicated rather than imported: the mailbox skill works without threads."""
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        stat_text = Path(f"/proc/{pid}/stat").read_text()
        tail = stat_text[stat_text.rfind(") ") + 2:].split()
        start_ticks = int(tail[19])
    except (OSError, ValueError, IndexError):
        return None
    return {"pid": pid, "boot_id": boot_id, "start_ticks": start_ticks}


def worker_process_is_gone(state: dict) -> bool:
    """Whether the worker PROCESS has departed — the only honest liveness signal
    the record carries.

    NOT the lifecycle. A `completed` worker's TUI is still sitting there and can
    still be rung, and that is exactly how a post-handback follow-up works:
    verification finds something, the orchestrator sends a NOTE, the worker fixes
    it and hands back again. Reading `completed` as "nothing more will be said"
    made the ring refuse a live worker and made the waiter drop its watch, and
    both were measured on the hop-12 trial (2026-09-18).

    NOT `process.state` alone either: nothing reaps it when the operator closes
    the window, so it reads `running` for a dead pid forever.
    """
    process = state.get("process") or {}
    identity = process.get("worker_identity")
    if isinstance(identity, dict) and identity.get("pid"):
        return process_identity(identity["pid"]) != identity
    return process.get("state") == "exited"     # never launched, or already reaped


def ring_worker(mailbox: Path, n: int) -> tuple[str | None, str]:
    """Ring the Codex worker for block `n`. Returns (session_id, "") on success
    or (None, reason). Never raises: a doorbell that fails must be LOUD, and the
    block is in the file either way."""
    try:
        state = worker_state(mailbox)
    except LookupError as exc:
        return None, str(exc)
    # No lifecycle pre-check: `codex queue` IS the liveness test, and it fails
    # loudly. A pre-check that refuses a live worker is strictly worse than one
    # failed queue call — the hop-12 trial spent a hand-rung pointer proving it.
    session_id = state.get("session_id")
    if not session_id:
        return None, ("worker has no bound session — "
                      "launch_codex_worker.py bind-session has not run")
    codex = os.environ.get("MB_CODEX_BIN", "codex")
    cmd = [codex, "queue", "--thread", str(session_id),
           "--message", f"MAILBOX {n} {mailbox.resolve()}"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=QUEUE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, f"codex queue did not return within {QUEUE_TIMEOUT_S:.0f}s"
    except OSError as exc:
        return None, f"cannot run {codex}: {exc}"
    if out.returncode != 0:
        detail = (out.stderr.strip() or out.stdout.strip() or "no output")
        return None, f"codex queue exited {out.returncode}: {detail[-500:]}"
    return str(session_id), ""


# ------------------------------------------------------- the watch registry


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / "codex-mailbox" / "sessions"


def registry_path(session_id: str) -> Path:
    if not SESSION_ID.match(session_id):
        raise ValueError(f"not a usable session id: {session_id!r}")
    return state_dir() / f"{session_id}.json"


class registry_lock:
    """Serializes read-modify-write of one session's registry file. The registry
    lives OUTSIDE every repo, so a worktree never carries a host's watch list."""

    def __init__(self, session_id: str):
        self.path = registry_path(session_id).with_suffix(".registry.lock")

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        os.close(self.fd)
        return False


def read_registry(session_id: str) -> dict:
    try:
        data = json.loads(registry_path(session_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"mailboxes": {}}
    except (OSError, json.JSONDecodeError):
        return {"mailboxes": {}}          # a corrupt registry watches nothing
    if not isinstance(data, dict) or not isinstance(data.get("mailboxes"), dict):
        return {"mailboxes": {}}
    return data


def write_registry(session_id: str, data: dict) -> None:
    path = registry_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def initial_cursor(mailbox: Path) -> int:
    """Where a fresh watch starts: past the history, but NOT past an exchange
    that is still waiting on us. A successor session taking over a live packet
    must be woken about the question the dead waiter never delivered; it must
    not be replayed the whole conversation."""
    done = [b for b in read_blocks(mailbox) if b["complete"]]
    if not done:
        return 0
    last = done[-1]
    return last["n"] - 1 if last["to"] == WAIT_ROLE else last["n"]


def watch(mailbox: Path, session_id: str) -> Path:
    mailbox = mailbox.resolve()
    with registry_lock(session_id):
        data = read_registry(session_id)
        if str(mailbox) not in data["mailboxes"]:     # re-watching never rewinds
            data["mailboxes"][str(mailbox)] = {"cursor": initial_cursor(mailbox)}
            write_registry(session_id, data)
    return mailbox


# ------------------------------------------------------------- the waiter


def new_blocks(mailbox: Path, cursor: int) -> list[dict]:
    return sorted((b for b in read_blocks(mailbox)
                   if b["complete"] and b["to"] == WAIT_ROLE and b["n"] > cursor),
                  key=lambda b: b["n"])


def worker_has_departed(mailbox: Path) -> bool:
    """Drop from the watch only when nobody is home: the worker's process is
    gone, so no further block can come from it. Called only after every
    undelivered block has been handed over."""
    try:
        return worker_process_is_gone(worker_state(mailbox))
    except LookupError:
        return False              # no record yet (or unreadable): keep watching


def collect(session_id: str) -> list[str]:
    """One look at every watched mailbox. Advances the cursor and returns the
    pointer lines BEFORE the caller exits, so a block is delivered once. Drops
    spent mailboxes."""
    lines: list[str] = []
    with registry_lock(session_id):
        data = read_registry(session_id)
        changed = False
        for path in sorted(data["mailboxes"]):
            entry = data["mailboxes"][path]
            mailbox = Path(path)
            fresh = new_blocks(mailbox, int(entry.get("cursor", 0)))
            if fresh:
                entry["cursor"] = fresh[-1]["n"]
                changed = True
                lines += [f"MAILBOX {b['n']} {mailbox}" for b in fresh]
            elif worker_has_departed(mailbox):
                del data["mailboxes"][path]
                changed = True
        if changed:
            write_registry(session_id, data)
    return lines


def wait(session_id: str, deadline_s: float, poll_s: float) -> int:
    if not read_registry(session_id)["mailboxes"]:
        return 0                          # this session orchestrates nothing
    lock_path = registry_path(session_id).with_suffix(".waiter.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0                      # a waiter is already blocked for us
        end = time.monotonic() + deadline_s
        while True:
            lines = collect(session_id)
            if lines:
                print("\n".join(lines), file=sys.stderr)
                return 2
            if not read_registry(session_id)["mailboxes"]:
                return 0                  # every watched worker has gone
            if time.monotonic() >= end:
                # The hook is killed SILENTLY at its own timeout, which would
                # leave nothing armed. Wake the session instead so the next turn
                # end re-arms us: one small turn per deadline.
                print(f"MAILBOX_WAITER_RENEW {deadline_s:.0f}s elapsed, nothing new",
                      file=sys.stderr)
                return 2
            time.sleep(poll_s)
    finally:
        os.close(fd)


def hook_session_id() -> str:
    """The Claude Code hook payload on stdin carries session_id, transcript_path
    and cwd."""
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"hook stdin is not JSON: {exc}") from exc
    session_id = (payload or {}).get("session_id") if isinstance(payload, dict) else None
    if not session_id or not SESSION_ID.match(str(session_id)):
        raise ValueError("hook stdin carries no usable session_id")
    return str(session_id)


# ------------------------------------------------------------------- CLI


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("send")
    p.add_argument("mailbox")
    p.add_argument("--from", dest="sender", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--kind", required=True)
    p.add_argument("--body")
    p.add_argument("--body-file")
    p = sub.add_parser("read")
    p.add_argument("mailbox")
    p.add_argument("n", nargs="?", type=int)
    p = sub.add_parser("pending")
    p.add_argument("mailbox")
    p.add_argument("--role", required=True, choices=ROLES)
    p = sub.add_parser("watch")
    p.add_argument("mailbox")
    p.add_argument("--session-id", default=os.environ.get("CLAUDE_CODE_SESSION_ID", ""))
    p = sub.add_parser("wait")
    p.add_argument("--deadline", type=float, default=WAIT_DEADLINE_S)
    p.add_argument("--poll", type=float, default=WAIT_POLL_S)
    a = ap.parse_args()

    try:
        if a.cmd == "wait":
            try:
                session_id = hook_session_id()
            except ValueError as exc:
                print(f"mb: {exc}", file=sys.stderr)
                return 1                       # a broken hook never wakes anyone
            return wait(session_id, a.deadline, a.poll)

        mailbox = Path(a.mailbox)
        if a.cmd == "send":
            if a.body_file:
                body = Path(a.body_file).read_text()
            elif a.body is not None:
                body = a.body
            else:
                body = sys.stdin.read()
            n = send(mailbox, a.sender, a.to, a.kind, body)
            print(f"SENT {n} {mailbox}")
            if a.to == "worker":
                session_id, why = ring_worker(mailbox, n)
                if session_id is None:
                    print(f"RING_SKIPPED {why}")
                    return 3                   # the block IS in the file
                print(f"RANG worker {session_id}")
        elif a.cmd == "read":
            done = [b for b in read_blocks(mailbox) if b["complete"]]
            pick = [b for b in done if b["n"] == a.n] if a.n else done[-1:]
            if not pick:
                raise ValueError(f"no complete block {a.n if a.n else ''}".strip())
            b = pick[-1]
            print(f"=== {b['n']} | {b['sender']} -> {b['to']} | {b['at']} | {b['kind']}")
            print(b["body"])
        elif a.cmd == "pending":
            for line in pending(mailbox, a.role):
                print(line)
        elif a.cmd == "watch":
            if not a.session_id:
                raise ValueError("no Claude session id: pass --session-id or run "
                                 "where $CLAUDE_CODE_SESSION_ID is set")
            print(f"WATCHING {a.session_id} {watch(mailbox, a.session_id)}")
    except (ValueError, OSError) as exc:
        print(f"mb: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
