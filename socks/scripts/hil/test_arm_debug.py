#!/usr/bin/env python3
"""
Tests for ARM debug integration: XSDBSession, VivadoILA.wait_response,
and breakpoint validation in hil_ila.py.

Run:  python3 scripts/hil/test_arm_debug.py
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        msg = f"  [FAIL] {name}"
        if detail:
            msg += f" -- {detail}"
        print(msg)


# ---------------------------------------------------------------------------
# Test 1: XSDBSession _read_until with a real subprocess (echo script)
# ---------------------------------------------------------------------------

def test_xsdb_read_until():
    """_read_until finds the sentinel via a real subprocess."""
    from hil_lib import XSDBSession, XSDB_MARKER
    import selectors

    # Use a small shell script that prints a banner then echoes marker
    proc = subprocess.Popen(
        ["bash", "-c",
         'echo "===XSDB_DONE==="; read line; echo "some output"; echo "another line"; '
         f'echo "{XSDB_MARKER}"'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )

    session = object.__new__(XSDBSession)
    session.proc = proc
    session._sel = selectors.DefaultSelector()
    session._sel.register(proc.stdout, selectors.EVENT_READ)

    # Consume banner
    session._read_until(XSDB_MARKER, timeout=5)

    # Trigger the response
    proc.stdin.write("go\n")
    proc.stdin.flush()

    result = session._read_until(XSDB_MARKER, timeout=5)

    check("read_until returns text before marker",
          "some output" in result,
          f"got: {result!r}")

    check("read_until excludes marker from result",
          XSDB_MARKER not in result,
          f"got: {result!r}")

    session._sel.close()
    proc.terminate()
    proc.wait()


def test_xsdb_read_until_timeout():
    """_read_until raises TimeoutError when sentinel never appears."""
    from hil_lib import XSDBSession, XSDB_MARKER
    import selectors

    # Script that prints banner then partial output but no marker
    proc = subprocess.Popen(
        ["bash", "-c", 'echo "===XSDB_DONE==="; sleep 2; echo "still waiting"'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )

    session = object.__new__(XSDBSession)
    session.proc = proc
    session._sel = selectors.DefaultSelector()
    session._sel.register(proc.stdout, selectors.EVENT_READ)

    # Consume banner
    session._read_until(XSDB_MARKER, timeout=5)

    timed_out = False
    try:
        session._read_until(XSDB_MARKER, timeout=0.5)
    except TimeoutError:
        timed_out = True

    check("read_until times out without sentinel", timed_out)

    session._sel.close()
    proc.terminate()
    proc.wait()


# ---------------------------------------------------------------------------
# Test 2: XSDBSession _cmd via real subprocess
# ---------------------------------------------------------------------------

def test_xsdb_cmd():
    """_cmd sends command with marker framing and returns output."""
    from hil_lib import XSDBSession, XSDB_MARKER
    import selectors

    # Script: print banner, then echo output for commands, echo marker for puts
    proc = subprocess.Popen(
        ["bash", "-c", """
echo "===XSDB_DONE==="
while IFS= read -r line; do
    if [[ "$line" == "puts ===XSDB_DONE===" ]]; then
        echo "===XSDB_DONE==="
    elif [[ "$line" == "state" ]]; then
        echo "Running"
    fi
done
"""],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )

    session = object.__new__(XSDBSession)
    session.proc = proc
    session._sel = selectors.DefaultSelector()
    session._sel.register(proc.stdout, selectors.EVENT_READ)

    # Consume banner
    session._read_until(XSDB_MARKER, timeout=5)

    result = session._cmd("state", timeout=5)

    check("_cmd returns response text",
          "Running" in result,
          f"got: {result!r}")

    session._sel.close()
    proc.stdin.close()
    proc.terminate()
    proc.wait()


# ---------------------------------------------------------------------------
# Test 3: XSDBSession debug commands (command string verification)
# ---------------------------------------------------------------------------

def test_xsdb_breakpoint_commands():
    """breakpoint() sends correct XSDB commands for symbol and address."""
    from hil_lib import XSDBSession, XSDB_MARKER
    import selectors

    captured = []

    # Script: capture stdin lines to stderr, echo marker for puts
    proc = subprocess.Popen(
        ["bash", "-c", """
echo "===XSDB_DONE==="
while IFS= read -r line; do
    echo "CMD:$line" >&2
    if [[ "$line" == "puts ===XSDB_DONE===" ]]; then
        echo "===XSDB_DONE==="
    fi
done
"""],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )

    session = object.__new__(XSDBSession)
    session.proc = proc
    session._sel = selectors.DefaultSelector()
    session._sel.register(proc.stdout, selectors.EVENT_READ)

    # Consume banner
    session._read_until(XSDB_MARKER, timeout=5)

    # Test symbol breakpoint
    session._cmd("bpadd -addr &run_test_phase_1", timeout=5)
    # Test address breakpoint
    session._cmd("bpadd -addr 0x00100a4c", timeout=5)

    proc.stdin.close()
    proc.terminate()
    stderr = proc.stderr.read()
    proc.wait()
    session._sel.close()

    check("breakpoint symbol command sent",
          "CMD:bpadd -addr &run_test_phase_1" in stderr,
          f"stderr: {stderr!r}")

    check("breakpoint address command sent",
          "CMD:bpadd -addr 0x00100a4c" in stderr,
          f"stderr: {stderr!r}")


def test_xsdb_state_parsing():
    """state() parses 'state: Running' format correctly."""
    from hil_lib import XSDBSession, XSDB_MARKER
    import selectors

    proc = subprocess.Popen(
        ["bash", "-c", """
echo "===XSDB_DONE==="
while IFS= read -r line; do
    if [[ "$line" == "state" ]]; then
        echo "state: Running"
    elif [[ "$line" == "puts ===XSDB_DONE===" ]]; then
        echo "===XSDB_DONE==="
    fi
done
"""],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )

    session = object.__new__(XSDBSession)
    session.proc = proc
    session._sel = selectors.DefaultSelector()
    session._sel.register(proc.stdout, selectors.EVENT_READ)
    session._read_until(XSDB_MARKER, timeout=5)

    result = session.state()

    check("state() parses 'state: Running' -> 'Running'",
          result == "Running",
          f"got: {result!r}")

    session._sel.close()
    proc.stdin.close()
    proc.terminate()
    proc.wait()


# ---------------------------------------------------------------------------
# Test 4: VivadoILA.wait_response
# ---------------------------------------------------------------------------

def _make_vivado_ila_with_pipe():
    """Create a VivadoILA with a pipe-backed stdout for testing."""
    from hil_ila import VivadoILA
    r_fd, w_fd = os.pipe()
    stdout = os.fdopen(r_fd, "r")
    writer = os.fdopen(w_fd, "w")

    ila = object.__new__(VivadoILA)
    ila.proc = MagicMock()
    ila.proc.stdout = stdout
    return ila, writer, stdout


def test_vivado_wait_response_ila_done():
    """wait_response returns ('ILA_DONE', path) on ILA_DONE marker."""
    ila, writer, stdout = _make_vivado_ila_with_pipe()

    def write_response():
        time.sleep(0.05)
        writer.write("ILA_DONE /tmp/capture.csv\n")
        writer.flush()

    t = threading.Thread(target=write_response)
    t.start()

    marker, detail = ila.wait_response(
        ["ILA_DONE", "ILA_TIMEOUT", "ILA_ERROR"], timeout=5)
    t.join()

    check("wait_response returns ILA_DONE marker",
          marker == "ILA_DONE",
          f"got: {marker!r}")
    check("wait_response returns csv path",
          "/tmp/capture.csv" in detail,
          f"got: {detail!r}")

    writer.close()
    stdout.close()


def test_vivado_wait_response_timeout_marker():
    """wait_response returns ('ILA_TIMEOUT', ...) on ILA_TIMEOUT marker."""
    ila, writer, stdout = _make_vivado_ila_with_pipe()

    def write_response():
        time.sleep(0.05)
        writer.write("ILA_TIMEOUT\n")
        writer.flush()

    t = threading.Thread(target=write_response)
    t.start()

    marker, detail = ila.wait_response(
        ["ILA_DONE", "ILA_TIMEOUT", "ILA_ERROR"], timeout=5)
    t.join()

    check("wait_response returns ILA_TIMEOUT",
          marker == "ILA_TIMEOUT",
          f"got: {marker!r}")

    writer.close()
    stdout.close()


def test_vivado_wait_response_dump_axi():
    """wait_response handles DUMP_AXI_DONE marker."""
    ila, writer, stdout = _make_vivado_ila_with_pipe()

    def write_response():
        time.sleep(0.05)
        writer.write("DUMP_AXI_DONE /tmp/fault_peripheral.csv\n")
        writer.flush()

    t = threading.Thread(target=write_response)
    t.start()

    marker, detail = ila.wait_response(
        ["DUMP_AXI_DONE", "ILA_ERROR"], timeout=5)
    t.join()

    check("wait_response handles DUMP_AXI_DONE",
          marker == "DUMP_AXI_DONE",
          f"got: {marker!r}")

    writer.close()
    stdout.close()


def test_vivado_wait_response_raises_on_timeout():
    """wait_response raises TimeoutError when no marker arrives."""
    ila, writer, stdout = _make_vivado_ila_with_pipe()

    def close_after_junk():
        time.sleep(0.05)
        writer.write("random vivado output\n")
        writer.flush()
        time.sleep(0.3)
        writer.close()

    t = threading.Thread(target=close_after_junk)
    t.start()

    timed_out = False
    try:
        ila.wait_response(["ILA_DONE", "ILA_TIMEOUT"], timeout=0.5)
    except TimeoutError:
        timed_out = True

    t.join()
    check("wait_response raises TimeoutError", timed_out)
    stdout.close()


def test_vivado_wait_response_skips_non_marker_lines():
    """wait_response ignores non-marker output before the real marker."""
    ila, writer, stdout = _make_vivado_ila_with_pipe()

    def write_multi():
        time.sleep(0.05)
        writer.write("Vivado% info: loading design\n")
        writer.write("setting up probes...\n")
        writer.write("ILA_DONE /tmp/result.csv\n")
        writer.flush()

    t = threading.Thread(target=write_multi)
    t.start()

    marker, detail = ila.wait_response(
        ["ILA_DONE", "ILA_TIMEOUT", "ILA_ERROR"], timeout=5)
    t.join()

    check("wait_response skips non-marker lines",
          marker == "ILA_DONE",
          f"got: {marker!r}")

    writer.close()
    stdout.close()


# ---------------------------------------------------------------------------
# Test 5: Breakpoint validation
# ---------------------------------------------------------------------------

def test_breakpoint_validation_missing():
    """Capture with no break_before or break_before_addr is rejected."""
    captures = [
        {"name": "test_capture", "trigger_probe": "mon_state_s",
         "trigger_value": "0010", "trigger_compare": "eq",
         "output": "ila_test.csv"},
    ]

    errors = [c["name"] for c in captures
              if not c.get("break_before") and not c.get("break_before_addr")]

    check("capture without breakpoint is rejected",
          errors == ["test_capture"],
          f"errors: {errors}")


def test_breakpoint_validation_symbol_ok():
    """Capture with break_before symbol passes."""
    captures = [
        {"name": "tx_loopback", "break_before": "run_test_phase_1",
         "trigger_probe": "mon_state_s", "trigger_value": "0010",
         "trigger_compare": "eq", "output": "ila_tx.csv"},
    ]

    errors = [c["name"] for c in captures
              if not c.get("break_before") and not c.get("break_before_addr")]

    check("capture with break_before passes", len(errors) == 0)


def test_breakpoint_validation_addr_ok():
    """Capture with break_before_addr passes."""
    captures = [
        {"name": "rx_validate", "break_before_addr": "0x00100A4C",
         "trigger_probe": "mon_rx_valid", "trigger_value": "1",
         "trigger_compare": "eq", "output": "ila_rx.csv"},
    ]

    errors = [c["name"] for c in captures
              if not c.get("break_before") and not c.get("break_before_addr")]

    check("capture with break_before_addr passes", len(errors) == 0)


def test_breakpoint_validation_mixed():
    """Mix of valid and invalid: only bad one flagged."""
    captures = [
        {"name": "good", "break_before": "fn1", "trigger_probe": "p",
         "trigger_value": "1", "trigger_compare": "eq", "output": "a.csv"},
        {"name": "bad", "trigger_probe": "p",
         "trigger_value": "1", "trigger_compare": "eq", "output": "b.csv"},
        {"name": "also_good", "break_before_addr": "0x100", "trigger_probe": "p",
         "trigger_value": "1", "trigger_compare": "eq", "output": "c.csv"},
    ]

    errors = [c["name"] for c in captures
              if not c.get("break_before") and not c.get("break_before_addr")]

    check("mixed plan: only 'bad' flagged",
          errors == ["bad"],
          f"errors: {errors}")


# ---------------------------------------------------------------------------
# Test 6: Debug section auto-generation
# ---------------------------------------------------------------------------

def test_debug_section_generation():
    """generate_debug_section parses register defines and C globals."""
    from hil_prep import generate_debug_section

    with tempfile.TemporaryDirectory() as tmpdir:
        sw_dir = os.path.join(tmpdir, "sw")
        os.makedirs(sw_dir)

        with open(os.path.join(sw_dir, "my_module_axi.h"), "w") as f:
            f.write("#define MY_MODULE_REG_CTRL    0x00\n"
                    "#define MY_MODULE_REG_STATUS  0x04\n"
                    "#define MY_MODULE_REG_TX_DATA 0x08\n"
                    "#define MY_MODULE_REG_RX_DATA 0x0C\n")

        with open(os.path.join(sw_dir, "hil_test_main.c"), "w") as f:
            f.write("volatile uint32_t g_test_phase = 0;\n"
                    "uint32_t g_tx_count = 0;\n"
                    "int main(void) {\n"
                    "    int local_var = 0;\n"
                    "    return local_var;\n"
                    "}\n")

        hil_config = {
            "axi": {"base_address": "0x43C00000"},
            "firmware": {"test_src": "sw/hil_test_main.c"},
        }

        debug = generate_debug_section(tmpdir, hil_config)

        check("4 register addresses found",
              len(debug["watch_addrs"]) == 4,
              f"got {len(debug['watch_addrs'])}")

        check("CTRL = base + 0x00",
              debug["watch_addrs"].get("MY_MODULE_REG_CTRL") == "0x43C00000",
              f"got: {debug['watch_addrs'].get('MY_MODULE_REG_CTRL')}")

        check("STATUS = base + 0x04",
              debug["watch_addrs"].get("MY_MODULE_REG_STATUS") == "0x43C00004",
              f"got: {debug['watch_addrs'].get('MY_MODULE_REG_STATUS')}")

        check("g_test_phase in watch_vars",
              "g_test_phase" in debug["watch_vars"])

        check("g_tx_count in watch_vars",
              "g_tx_count" in debug["watch_vars"])

        check("local_var excluded from watch_vars",
              "local_var" not in debug["watch_vars"])

        check("jtag_axi_dump has peripheral",
              "peripheral" in debug["jtag_axi_dump"])

        check("jtag_axi_dump has slcr_clocks at 0xF8000120",
              debug["jtag_axi_dump"]["slcr_clocks"]["base"] == "0xF8000120")

        check("peripheral count = 4 (number of REG defines)",
              debug["jtag_axi_dump"]["peripheral"]["count"] == 4,
              f"got: {debug['jtag_axi_dump']['peripheral']['count']}")


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

def main():
    print("\n=== ARM Debug Integration Tests ===\n")

    print("--- XSDBSession marker framing ---")
    test_xsdb_read_until()
    test_xsdb_read_until_timeout()
    test_xsdb_cmd()

    print("\n--- XSDBSession debug commands ---")
    test_xsdb_breakpoint_commands()
    test_xsdb_state_parsing()

    print("\n--- VivadoILA.wait_response ---")
    test_vivado_wait_response_ila_done()
    test_vivado_wait_response_timeout_marker()
    test_vivado_wait_response_dump_axi()
    test_vivado_wait_response_raises_on_timeout()
    test_vivado_wait_response_skips_non_marker_lines()

    print("\n--- Breakpoint validation ---")
    test_breakpoint_validation_missing()
    test_breakpoint_validation_symbol_ok()
    test_breakpoint_validation_addr_ok()
    test_breakpoint_validation_mixed()

    print("\n--- Debug section auto-generation ---")
    test_debug_section_generation()

    print(f"\n=== Results: {PASS} passed, {FAIL} failed ===")
    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
