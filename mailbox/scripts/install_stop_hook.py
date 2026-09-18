#!/usr/bin/env python3
"""Install the mailbox waiter as a Claude Code `Stop` hook — when the OPERATOR runs it.

The waiter is what makes a Claude orchestrator hear a worker: at every turn end
the harness runs `mb.py wait`, which exits 0 at once unless this session watches
a mailbox, and otherwise blocks until a block addressed to the orchestrator
arrives and exits 2 to wake the session.

    install_stop_hook.py                 # DRY RUN: print the entry and a diff
    install_stop_hook.py --apply         # write it, after a timestamped backup
    install_stop_hook.py --apply --remove

No model edits a settings file. This script writes nothing without `--apply`,
touches only its own Stop entry, leaves every other hook and every other Stop
entry alone, and refuses a settings file it cannot parse.

`timeout` is the harness's: a hook is killed SILENTLY at it. The waiter's own
`--deadline` is shorter, so it wakes the session with MAILBOX_WAITER_RENEW and
the next turn end re-arms it, rather than dying unnoticed.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import json
import os
import sys
from pathlib import Path

DEFAULT_SETTINGS = Path.home() / ".claude" / "settings.json"
DEFAULT_TIMEOUT_S = 3600
DEFAULT_DEADLINE_S = 3300
MB = Path(__file__).resolve().parent / "mb.py"


def portable(path: Path) -> str:
    """`$HOME/...` when it is under the home directory — hook commands run through
    a shell, and the settings file then survives a different home."""
    try:
        return f"$HOME/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def hook_command(deadline_s: int) -> str:
    return f'python3 "{portable(MB)}" wait --deadline {deadline_s}'


def is_ours(entry: dict) -> bool:
    """Our entry is the one whose command runs THIS mb.py's `wait`, whatever its
    deadline. An entry that merely mentions another mb.py is someone else's."""
    for hook in entry.get("hooks", []):
        cmd = hook.get("command", "")
        if portable(MB) in cmd and " wait" in cmd:
            return True
    return False


def build_entry(timeout_s: int, deadline_s: int) -> dict:
    return {"hooks": [{"type": "command", "command": hook_command(deadline_s),
                       "asyncRewake": True, "timeout": timeout_s}]}


def plan(settings: dict, timeout_s: int, deadline_s: int, remove: bool) -> tuple[dict, str]:
    """Return (new settings, what changed). Never mutates the input."""
    new = json.loads(json.dumps(settings))       # deep copy; settings are small
    stop = new.setdefault("hooks", {}).setdefault("Stop", [])
    mine = [i for i, e in enumerate(stop) if isinstance(e, dict) and is_ours(e)]
    if remove:
        if not mine:
            return settings, "nothing to remove: the waiter is not installed"
        for i in reversed(mine):
            del stop[i]
        if not stop:
            del new["hooks"]["Stop"]
        if not new["hooks"]:
            del new["hooks"]
        return new, f"removed {len(mine)} waiter entr{'y' if len(mine) == 1 else 'ies'}"
    wanted = build_entry(timeout_s, deadline_s)
    if len(mine) == 1 and stop[mine[0]] == wanted:
        return settings, "already installed, unchanged"
    if mine:
        for i in reversed(mine[1:]):             # collapse any duplicates
            del stop[i]
        stop[mine[0]] = wanted
        return new, "updated the existing waiter entry"
    stop.append(wanted)
    return new, "installed the waiter entry"


def render(obj: dict) -> str:
    return json.dumps(obj, indent=2) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--settings", default=str(DEFAULT_SETTINGS))
    ap.add_argument("--apply", action="store_true", help="write the change (default: dry run)")
    ap.add_argument("--remove", action="store_true", help="take the waiter entry out again")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S,
                    help="the HARNESS timeout; a hook is killed silently at it")
    ap.add_argument("--deadline", type=int, default=DEFAULT_DEADLINE_S,
                    help="the waiter's own deadline; must be under --timeout")
    a = ap.parse_args()

    if a.deadline >= a.timeout:
        print(f"install_stop_hook: --deadline {a.deadline} must be under --timeout {a.timeout}, "
              "or the hook is killed before it can renew itself", file=sys.stderr)
        return 2
    if not MB.is_file():
        print(f"install_stop_hook: no mb.py beside this script: {MB}", file=sys.stderr)
        return 2

    path = Path(a.settings).expanduser()
    if path.exists():
        try:
            before = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"install_stop_hook: refusing to touch unparseable settings {path}: {exc}",
                  file=sys.stderr)
            return 2
        if not isinstance(before, dict):
            print(f"install_stop_hook: {path} is not a JSON object", file=sys.stderr)
            return 2
    else:
        before = {}

    after, what = plan(before, a.timeout, a.deadline, a.remove)
    print(f"settings : {path}{'' if path.exists() else '  (would be created)'}")
    print(f"entry    : {json.dumps(build_entry(a.timeout, a.deadline))}")
    print(f"change   : {what}")

    if after == before:
        return 0
    diff = list(difflib.unified_diff(render(before).splitlines(True),
                                     render(after).splitlines(True),
                                     fromfile=str(path), tofile=str(path) + " (new)"))
    print("".join(diff), end="")
    if not a.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to write it.")
        return 0

    if path.exists():
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.bak-{stamp}")
        backup.write_text(render(before), encoding="utf-8")
        print(f"backup   : {backup}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(render(after), encoding="utf-8")
    os.replace(tmp, path)
    print(f"WROTE {path}")
    print("It takes effect in sessions started from now on.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
