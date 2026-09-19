#!/usr/bin/env python3
"""Fire a prepared Codex worker into a new, detached tmux window.

Run by the orchestrator ONLY when the operator says "fire": preparing, emitting
and arming a packet never launch a worker. This replaces the operator's two
pastes (the fire script into a new terminal, then turn 1 into the TUI) — it
does not move launch authority.

  fire_codex_worker.py --inbox <worktree>/codex-handoff/<plan-id>

Before it opens anything it registers `<inbox>/mailbox.md` for THIS Claude
session (`$CLAUDE_CODE_SESSION_ID`), so the `Stop` hook's waiter is armed before
the worker can say anything. Then it opens `tmux new-window -d -n <plan-id> -c
<worktree>` running `<inbox>/fire.sh`. A launcher refusal is recorded in
`<inbox>/fire-failed.log` WITH the launcher's message, sent to the orchestrator as
a `FIRE_FAILED` mailbox block so it is woken rather than left to find out, and
the window stays open until a key is pressed.

tmux is used for exactly one thing: opening the window. `new-window` starts a
process in its own pane — it types nothing into any keyboard, and nothing here
ever will (`mailbox/SKILL.md` records why).

Output: one pointer line, `FIRE_REQUESTED <inbox>`. Success of the worker itself
is `WORKER_LAUNCHED` in `<inbox>/worker-state.json`, as before.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

SKILLS_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILLS_ROOT / "mailbox" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mb  # noqa: E402
from launch_codex_worker import LaunchError, existing_worker_is_live, read_state  # noqa: E402

PANE = re.compile(r"^%\d+$")


def tmux(*args: str) -> subprocess.CompletedProcess:
    """Set FIRE_TMUX_SOCKET=<name> to talk to `tmux -L <name>`; tests use it, and
    never point a test at the operator's server."""
    sock = os.environ.get("FIRE_TMUX_SOCKET")
    cmd = ["tmux"] + (["-L", sock] if sock else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def fire(inbox: Path, session_id: str) -> str:
    inbox = inbox.expanduser().resolve()
    if inbox.parent.name != "codex-handoff":
        raise LaunchError(f"not a packet inbox (expected <worktree>/codex-handoff/<plan-id>): {inbox}")
    plan_id, worktree = inbox.name, inbox.parent.parent
    fire_sh, turn1 = inbox / "fire.sh", inbox / "turn1.md"
    for needed in (fire_sh, turn1):
        if not needed.is_file():
            raise LaunchError(f"packet is not prepared, missing {needed.name}: {inbox}")
    if not (os.environ.get("TMUX") or os.environ.get("FIRE_TMUX_SOCKET")):
        raise LaunchError(f"not inside tmux; fire by hand in a new terminal: bash {fire_sh}")
    if not session_id:
        raise LaunchError("no Claude session id: pass --session-id or run where "
                          "$CLAUDE_CODE_SESSION_ID is set (the waiter is routed by it)")
    state_path = inbox / "worker-state.json"
    if state_path.exists():
        if existing_worker_is_live(read_state(state_path)):
            raise LaunchError(f"refusing duplicate live worker at {state_path}")
        raise LaunchError(
            f"worker state already exists at {state_path}; regenerate the packet for a relaunch"
        )

    # Arm the wake BEFORE the worker exists: a block sent in its first seconds
    # must find a registered mailbox, not a race.
    mb.watch(inbox / "mailbox.md", session_id)

    q = shlex.quote
    failed = inbox / "fire-failed.log"
    mailbox = inbox / "mailbox.md"
    mb_py = Path(mb.__file__).resolve()
    # A non-zero exit is a FAILED FIRE only if the launcher never recorded
    # WORKER_LAUNCHED: fire.sh execs the launcher, which stays in the foreground
    # for the worker's whole life, so a late non-zero exit is just a closed TUI.
    # A failed fire wakes the orchestrator with the launcher's own words (it
    # appends them to fire-failed.log); nothing is bound yet, so mb.py has no
    # worker identity to hold this block against.
    script = (
        f"bash {q(str(fire_sh))}; rc=$?; "
        f"if [ $rc -ne 0 ]; then "
        f"printf '%s fire.sh exited rc=%s\\n' \"$(date -u +%FT%TZ)\" \"$rc\" >> {q(str(failed))}; "
        f"if ! grep -q WORKER_LAUNCHED {q(str(state_path))} 2>/dev/null; then "
        f"python3 {q(str(mb_py))} send {q(str(mailbox))} --from worker --to orchestrator "
        f"--kind FIRE_FAILED --body-file {q(str(failed))}; fi; "
        f"printf '\\nfire failed (rc=%s) - press Enter to close this window\\n' \"$rc\"; read -r _; "
        f"fi; exit $rc"
    )
    args = ["new-window", "-d", "-P", "-F", "#{pane_id}", "-n", plan_id, "-c", str(worktree)]
    if os.environ.get("FIRE_TMUX_SOCKET"):   # the pane inherits the SERVER's environment
        args += ["-e", f"FIRE_TMUX_SOCKET={os.environ['FIRE_TMUX_SOCKET']}"]
    out = tmux(*args, script)
    pane = out.stdout.strip()
    if out.returncode != 0 or not PANE.match(pane):
        raise LaunchError(f"tmux new-window failed: {out.stderr.strip() or out.stdout.strip()}")
    return pane


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--inbox", required=True)
    ap.add_argument("--session-id", default=os.environ.get("CLAUDE_CODE_SESSION_ID", ""))
    a = ap.parse_args()
    try:
        fire(Path(a.inbox), a.session_id)
    except (LaunchError, ValueError, OSError) as exc:
        print(f"fire_codex_worker: {exc}", file=sys.stderr)
        return 2
    print(f"FIRE_REQUESTED {Path(a.inbox).expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
