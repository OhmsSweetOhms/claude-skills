#!/usr/bin/env python3
"""Four-stage hardware validation runner for ADI/no-OS HIL flows."""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_lib import hil_build_dir


SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_DIR = os.path.join(SKILL_DIR, "scripts")


class HilValidateRunner:
    """Orchestrate toolchain check, no-OS Make, XSDB program, and UART query."""

    def __init__(self, project_dir, board="zcu102"):
        self.project_dir = os.path.abspath(project_dir)
        self.board = board
        self.board_env = self._load_board_env(board)
        self.results = []

    def _load_board_env(self, board):
        path = os.path.join(SKILL_DIR, "references", "boards", board, "env.json")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Board env file not found: {path}")
        with open(path, "r") as f:
            return json.load(f)

    def _setting(self, key, env_name):
        return os.environ.get(env_name) or self.board_env.get(key)

    def _record(self, name, status, started, output="", artifacts=None):
        self.results.append({
            "name": name,
            "status": status,
            "duration_s": round(time.time() - started, 3),
            "output_tail": "\n".join(output.splitlines()[-40:]),
            "artifacts": artifacts or {},
        })

    def _run_shell(self, cmd, name):
        started = time.time()
        result = subprocess.run(
            ["bash", "-c", cmd],
            cwd=self.project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        status = "pass" if result.returncode == 0 else "fail"
        self._record(name, status, started, result.stdout)
        if result.returncode != 0:
            raise RuntimeError(f"{name} failed rc={result.returncode}\n{result.stdout}")
        return result.stdout

    def run(self):
        self._stage_0a_vivado_env_source()
        self._stage_0b_make_build()
        self._stage_0c_xsdb_program()
        self._stage_0d_uart_query()
        return self._emit_result_json()

    def _stage_0a_vivado_env_source(self):
        vivado = self._setting("vivado_settings64", "SOCKS_VIVADO_SETTINGS64")
        vitis = self._setting("vitis_settings64", "SOCKS_VITIS_SETTINGS64")
        if not vivado or not os.path.isfile(vivado):
            raise FileNotFoundError(f"Vivado settings64.sh not found: {vivado}")
        if not vitis or not os.path.isfile(vitis):
            raise FileNotFoundError(f"Vitis settings64.sh not found: {vitis}")
        expected = self.board_env.get("expected_vivado_version")
        check = (
            f"source {vivado!r} && source {vitis!r} && "
            "vivado -version | head -1 && "
            "aarch64-none-elf-gcc --version | head -1"
        )
        output = self._run_shell(check, "0a_toolchain_env_source")
        if expected and expected not in output:
            raise RuntimeError(
                f"Vivado version check did not find expected {expected}\n{output}")
        return output

    def _stage_0a_toolchain_env_source(self):
        return self._stage_0a_vivado_env_source()

    def _stage_0b_make_build(self):
        script = os.path.join(SCRIPTS_DIR, "hil", "hil_firmware.py")
        return self._run_shell(
            f"{sys.executable!r} {script!r} --project-dir {self.project_dir!r}",
            "0b_make_build",
        )

    def _stage_0c_xsdb_program(self):
        # hil_run.py owns the race-free ordering: it starts UART capture before
        # XSDB programming, then waits for configured pass markers.
        script = os.path.join(SCRIPTS_DIR, "hil", "hil_run.py")
        return self._run_shell(
            f"{sys.executable!r} {script!r} --project-dir {self.project_dir!r}",
            "0c_xsdb_program_and_capture",
        )

    def _stage_0d_uart_query(self):
        state_path = os.path.join(self.project_dir, "build", "state",
                                  "hil-validate-result.json")
        started = time.time()
        prior = self.results[-1] if self.results else {}
        if prior.get("status") != "pass":
            self._record("0d_uart_query", "fail", started,
                         "Stage 17 did not report PASS")
            raise RuntimeError("UART query failed because Stage 17 failed")
        self._record("0d_uart_query", "pass", started,
                     "UART pass markers matched by hil_run.py",
                     {"state_path": state_path})
        return prior

    def _emit_result_json(self):
        state_dir = os.path.join(self.project_dir, "build", "state")
        os.makedirs(state_dir, exist_ok=True)
        path = os.path.join(state_dir, "hil-validate-result.json")
        result = {
            "status": "pass",
            "timestamp": datetime.now().isoformat(),
            "board": self.board,
            "project_dir": self.project_dir,
            "stages": self.results,
        }
        with open(path, "w") as f:
            json.dump(result, f, indent=2)
            f.write("\n")
        print(json.dumps(result, indent=2))
        return result


def main():
    parser = argparse.ArgumentParser(description="Run ADI/no-OS HIL validation")
    parser.add_argument("--project-dir", required=True, help="SOCKS project root")
    parser.add_argument("--board", default="zcu102", help="Board env profile")
    args = parser.parse_args()
    try:
        HilValidateRunner(args.project_dir, board=args.board).run()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
