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

Neither agent pings. `relay` runs on the host (a sandboxed Codex command cannot
reach the tmux socket) and, once per newly COMPLETED block, types

    MAILBOX <n> <path>

into the addressee's tmux pane. That line is a pointer to the file, never an
instruction. Pane ids are blocks too (`claim` = a CLAIM block addressed to
`relay`; the newest claim per role wins), which is why the file has no header
to rewrite.

Usage:
  mb.py send <mailbox> --from R --to R --kind K [--body T | --body-file F | stdin]
  mb.py read <mailbox> [n]
  mb.py claim <mailbox> --role R [--pane %N]        (pane defaults to $TMUX_PANE)
  mb.py pending <mailbox> --role R
  mb.py relay <mailbox> [--poll S] [--from-start]

Roles: orchestrator, worker, relay (an addressee only). Kinds are uppercase
tokens; the worker rules name QUESTION, ANSWER, HANDBACK, NOTE and CLAIM.
Set MB_TMUX_SOCKET=<name> to talk to `tmux -L <name>` (tests use this; never
point a test at the operator's server).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import fcntl
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROLES = ("orchestrator", "worker", "relay")
HEADER = re.compile(r"^=== (\d+) \| (\w+) -> (\w+) \| (\S+) \| ([A-Z][A-Z0-9_]*)$")
END = re.compile(r"^=== end (\d+)$")
KIND = re.compile(r"^[A-Z][A-Z0-9_]*$")
PANE = re.compile(r"^%\d+$")
PING_GAP_S = 0.3        # text, pause, Enter: a TUI reads a fast Enter as a paste
PANE_CHECK_S = 5.0


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


def newest_claims(blocks: list[dict]) -> dict[str, str]:
    panes: dict[str, str] = {}
    for b in blocks:
        if b["complete"] and b["kind"] == "CLAIM" and b["to"] == "relay":
            pane = b["body"].strip()
            if PANE.match(pane):
                panes[b["sender"]] = pane
    return panes


def send(mailbox: Path, sender: str, to: str, kind: str, body: str) -> int:
    if sender not in ROLES[:2]:
        raise ValueError(f"--from must be one of {ROLES[:2]}")
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


def tmux(*args: str) -> subprocess.CompletedProcess:
    sock = os.environ.get("MB_TMUX_SOCKET")
    cmd = ["tmux"] + (["-L", sock] if sock else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def ping(pane: str, n: int, mailbox: Path) -> bool:
    first = tmux("send-keys", "-t", pane, "-l", f"MAILBOX {n} {mailbox}")
    if first.returncode != 0:
        return False
    time.sleep(PING_GAP_S)
    return tmux("send-keys", "-t", pane, "Enter").returncode == 0


def live_panes() -> set[str] | None:
    out = tmux("list-panes", "-a", "-F", "#{pane_id}")
    return set(out.stdout.split()) if out.returncode == 0 else None


def log(msg: str) -> None:
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{now} {msg}", flush=True)


def relay(mailbox: Path, poll_s: float, from_start: bool) -> int:
    mailbox = mailbox.resolve()
    seen = set() if from_start else {b["n"] for b in read_blocks(mailbox) if b["complete"]}
    tried: dict[int, str] = {}               # block -> pane a ping already failed on
    quiet: set[int] = set()
    stamp, last_pane_check = None, 0.0
    log(f"RELAY_START {mailbox} seen={len(seen)}")
    while True:
        now = time.monotonic()
        if now - last_pane_check >= PANE_CHECK_S:
            last_pane_check = now
            worker = newest_claims(read_blocks(mailbox)).get("worker")
            panes = live_panes()
            if worker and panes is not None and worker not in panes:
                log(f"RELAY_EXIT worker pane {worker} is gone")
                return 0
        try:
            st = mailbox.stat()
            cur = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            cur = None
        if cur != stamp or tried:
            stamp = cur
            blocks = read_blocks(mailbox)
            panes_by_role = newest_claims(blocks)
            for b in blocks:
                n = b["n"]
                if not b["complete"] or n in seen:
                    continue
                if b["to"] == "relay":
                    seen.add(n)
                    continue
                pane = panes_by_role.get(b["to"])
                if pane is None:
                    if n not in quiet:
                        quiet.add(n)
                        log(f"NO_PANE {n} no claim for {b['to']} yet")
                    continue
                if tried.get(n) == pane:     # retry only once that role re-claims
                    continue
                if ping(pane, n, mailbox):
                    seen.add(n)
                    tried.pop(n, None)
                    log(f"PINGED {n} {b['kind']} -> {b['to']} {pane}")
                else:
                    tried[n] = pane
                    log(f"PING_FAILED {n} -> {b['to']} {pane}")
        time.sleep(poll_s)


def pending(mailbox: Path, role: str) -> list[str]:
    blocks = read_blocks(mailbox)
    lines = [f"UNTERMINATED_BLOCK {b['n']} {mailbox}" for b in blocks if not b["complete"]]
    talk = [b for b in blocks if b["complete"] and b["to"] != "relay"]
    if talk and talk[-1]["to"] == role:
        lines.append(f"PENDING {talk[-1]['n']} {talk[-1]['kind']} {mailbox}")
    return lines


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
    p = sub.add_parser("claim")
    p.add_argument("mailbox")
    p.add_argument("--role", required=True, choices=ROLES[:2])
    p.add_argument("--pane", default=os.environ.get("TMUX_PANE", ""))
    p = sub.add_parser("pending")
    p.add_argument("mailbox")
    p.add_argument("--role", required=True, choices=ROLES[:2])
    p = sub.add_parser("relay")
    p.add_argument("mailbox")
    p.add_argument("--poll", type=float, default=0.5)
    p.add_argument("--from-start", action="store_true")
    a = ap.parse_args()
    mailbox = Path(a.mailbox)

    try:
        if a.cmd == "send":
            if a.body_file:
                body = Path(a.body_file).read_text()
            elif a.body is not None:
                body = a.body
            else:
                body = sys.stdin.read()
            n = send(mailbox, a.sender, a.to, a.kind, body)
            print(f"SENT {n} {mailbox}")
        elif a.cmd == "claim":
            if not PANE.match(a.pane):
                raise ValueError("no tmux pane id: pass --pane %N or run inside tmux")
            n = send(mailbox, a.role, "relay", "CLAIM", a.pane)
            print(f"CLAIMED {n} {a.role} {a.pane}")
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
        elif a.cmd == "relay":
            return relay(mailbox, a.poll, a.from_start)
    except (ValueError, OSError) as exc:
        print(f"mb: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
