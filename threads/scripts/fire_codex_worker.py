#!/usr/bin/env python3
"""Fire a prepared Codex worker into a new, detached tmux window.

Run by the orchestrator ONLY when the operator says "fire": preparing, emitting
and arming a packet never launch a worker. This replaces the operator's two
pastes (the fire script into a new terminal, then turn 1 into the TUI) — it
does not move launch authority.

  fire_codex_worker.py --inbox <worktree>/codex-handoff/<plan-id>

It appends the orchestrator's pane claim to `<inbox>/mailbox.md`, opens
`tmux new-window -d -n <plan-id> -c <worktree>` running the mailbox relay in the
background and then `<inbox>/fire.sh`, and appends the worker's pane claim from
the pane id tmux prints. The relay's output goes to `<inbox>/relay.log`; the
pane belongs to the Codex TUI and nothing else writes to it. A launcher refusal
is recorded in `<inbox>/fire-failed.log` and the window stays open until a key
is pressed, so the refusal is readable both from a file and from the pane.

`new-window` starts a process in its own pane. It is not `send-keys`: it types
nothing into any keyboard.

Output: one pointer line, `FIRE_REQUESTED <inbox>`. Success of the worker itself
is `WORKER_LAUNCHED` in `<inbox>/worker-state.json`, as before.
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mb  # noqa: E402
from launch_codex_worker import LaunchError, existing_worker_is_live, read_state  # noqa: E402

MB = Path(__file__).resolve().parent / "mb.py"


def fire(inbox: Path, orchestrator_pane: str) -> str:
    inbox = inbox.expanduser().resolve()
    if inbox.parent.name != "codex-handoff":
        raise LaunchError(f"not a packet inbox (expected <worktree>/codex-handoff/<plan-id>): {inbox}")
    plan_id, worktree = inbox.name, inbox.parent.parent
    fire_sh, turn1 = inbox / "fire.sh", inbox / "turn1.md"
    for needed in (fire_sh, turn1):
        if not needed.is_file():
            raise LaunchError(f"packet is not prepared, missing {needed.name}: {inbox}")
    if not (os.environ.get("TMUX") or os.environ.get("MB_TMUX_SOCKET")):
        raise LaunchError(f"not inside tmux; fire by hand in a new terminal: bash {fire_sh}")
    if not mb.PANE.match(orchestrator_pane):
        raise LaunchError("no orchestrator pane id: pass --orchestrator-pane %N or run inside tmux")
    state_path = inbox / "worker-state.json"
    if state_path.exists():
        if existing_worker_is_live(read_state(state_path)):
            raise LaunchError(f"refusing duplicate live worker at {state_path}")
        raise LaunchError(
            f"worker state already exists at {state_path}; regenerate the packet for a relaunch"
        )

    mailbox = inbox / "mailbox.md"
    mb.send(mailbox, "orchestrator", "relay", "CLAIM", orchestrator_pane)

    q = shlex.quote
    failed = inbox / "fire-failed.log"
    script = (
        f"{q(sys.executable)} {q(str(MB))} relay {q(str(mailbox))} >> {q(str(inbox / 'relay.log'))} 2>&1 & "
        f"bash {q(str(fire_sh))}; rc=$?; "
        f"if [ $rc -ne 0 ]; then "
        f"printf '%s fire.sh exited rc=%s\\n' \"$(date -u +%FT%TZ)\" \"$rc\" >> {q(str(failed))}; "
        f"printf '\\nfire failed (rc=%s) - press Enter to close this window\\n' \"$rc\"; read -r _; "
        f"fi; exit $rc"
    )
    args = ["new-window", "-d", "-P", "-F", "#{pane_id}", "-n", plan_id, "-c", str(worktree)]
    if os.environ.get("MB_TMUX_SOCKET"):     # the pane inherits the SERVER's environment
        args += ["-e", f"MB_TMUX_SOCKET={os.environ['MB_TMUX_SOCKET']}"]
    out = mb.tmux(*args, script)
    pane = out.stdout.strip()
    if out.returncode != 0 or not mb.PANE.match(pane):
        raise LaunchError(f"tmux new-window failed: {out.stderr.strip() or out.stdout.strip()}")
    mb.send(mailbox, "worker", "relay", "CLAIM", pane)
    return pane


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--inbox", required=True)
    ap.add_argument("--orchestrator-pane", default=os.environ.get("TMUX_PANE", ""))
    a = ap.parse_args()
    try:
        fire(Path(a.inbox), a.orchestrator_pane)
    except (LaunchError, ValueError, OSError) as exc:
        print(f"fire_codex_worker: {exc}", file=sys.stderr)
        return 2
    print(f"FIRE_REQUESTED {Path(a.inbox).expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
