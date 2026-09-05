#!/usr/bin/env python3
"""Wait outside the model loop for a packet's foreground worker receipt."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from launch_codex_worker import LaunchError, identity_is_live, read_state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inbox", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=86400)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if args.timeout_seconds <= 0 or args.interval_seconds <= 0:
        parser.error("timeouts and intervals must be positive")

    state_path = Path(args.inbox).expanduser().resolve() / "worker-state.json"
    deadline = time.monotonic() + args.timeout_seconds
    while time.monotonic() < deadline:
        if not state_path.exists():
            time.sleep(args.interval_seconds)
            continue
        try:
            state = read_state(state_path)
        except LaunchError:
            print(f"WORKER_STATE_INVALID {state_path}")
            return 1
        process_state = state.get("process", {}).get("state")
        lifecycle_state = state.get("state")
        events = {row.get("event") for row in state.get("events", [])}
        if "WORKER_LAUNCHED" in events:
            if process_state == "running" and not identity_is_live(
                state["process"].get("worker_identity")
            ):
                print(f"WORKER_STATE_STALE {state_path}")
                return 1
            print(f"WORKER_LAUNCHED {state_path}")
            return 0
        if process_state == "starting" and not identity_is_live(
            state["process"].get("launcher_identity")
        ):
            print(f"WORKER_STATE_STALE {state_path}")
            return 1
        if lifecycle_state in {"failed", "completed", "exited"}:
            print(f"WORKER_TERMINAL {state_path}")
            return 1
        time.sleep(args.interval_seconds)

    print(f"WORKER_LAUNCH_TIMEOUT {state_path}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
