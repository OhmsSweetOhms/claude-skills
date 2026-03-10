#!/usr/bin/env python3
"""
log_stage.py -- Log a guidance stage result to the session manifest.

Used by Claude to record completion of guidance-only stages (2, 3, 6, 12)
or any manual stage work.

Usage:
    python scripts/log_stage.py --project-dir . --stage 2 --status pass --note "wrote can_core.vhd"
    python scripts/log_stage.py --project-dir . --stage 6 --status pass --note "wrote sw/can_core.h" --files sw/can_core.h
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from session import _session_path, append_session_entry


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Log a guidance stage to the session manifest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("--project-dir", type=str, required=True,
                        help="Project root directory")
    parser.add_argument("--stage", type=int, required=True,
                        help="Stage number (0-13)")
    parser.add_argument("--status", type=str, required=True,
                        choices=["pass", "fail", "skip"],
                        help="Stage outcome")
    parser.add_argument("--note", type=str, default=None,
                        help="Description of what was done")
    parser.add_argument("--files", type=str, nargs="*", default=None,
                        help="Files created or modified")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)

    entry = append_session_entry(
        project_dir=project_dir,
        stage_num=args.stage,
        status=args.status,
        source="guidance",
        note=args.note,
        files=args.files,
        log_file=None,
    )

    print(f"  Logged stage {args.stage} ({args.status}) "
          f"iteration {entry['iteration']} -> "
          f"{_session_path(project_dir)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
