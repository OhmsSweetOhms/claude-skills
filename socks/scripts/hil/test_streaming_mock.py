"""
Mock TCP server + smoke-test runner for streaming.py.

Spins up a Python TCP server that mimics the R5 lwIP firmware's streaming behavior
(IQ ingress on iq_port, telemetry egress on tlm_port at ~100 Hz cadence). Then runs
streaming.default_digital_matrix() against it and asserts overall PASS.

Scope:
- digital_loopback: full 9-scenario matrix (multi-packet contiguous, split header/
  payload, multi-frame-one-write, bad magic, bad fs_hz, sample-index gap, oversized
  packet, post-negative recovery, baseline contract). PASS exit code 0.
- analog_loopback: verifies the dispatch raises NotImplementedError (the documented
  "gated on plan-05" stub) when streaming.expected_signal_integrity contains
  expected_snr_db. Once the substrate brings up the analog chain, _validate_analog
  in streaming.py and the analog branch of this mock will be filled in.

Run:
    python3 ~/.claude/skills/socks/scripts/hil/test_streaming_mock.py
"""

from __future__ import annotations

import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from dataclasses import dataclass

# Import the helper module under test. When run as a script, the parent dir of
# hil/ is on sys.path via the conventional layout.
import streaming as s
import hil_run as hr
import hil_firmware as hf


# ============================================================
# Mock R5 firmware state (thread-shared)
# ============================================================

@dataclass
class MockState:
    """Mirror of streaming_ctrl_0 regmap counters."""
    fs_hz: int = s.DEFAULT_FS_HZ
    iq_max_n_samples: int = s.DEFAULT_IQ_MAX_N_SAMPLES
    sample_count: int = 0
    byte_count: int = 0
    crc32_current: int = 0
    drop_count: int = 0
    underrun_count: int = 0
    dma_status: int = 0x00010008  # benign idle pattern matching live baseline
    axis_fifo_level: int = 0
    hdr_fail_count: int = 0
    expected_next_index: int = 0
    t_us_counter: int = 0
    tlm_seq: int = 0


class MockServer:
    """Two-port TCP server mimicking R5 lwIP streaming firmware."""

    def __init__(self, ip: str = "127.0.0.1", iq_port: int = 0,
                 tlm_port: int = 0, fs_hz: int = s.DEFAULT_FS_HZ,
                 iq_max_n_samples: int = s.DEFAULT_IQ_MAX_N_SAMPLES,
                 tlm_period_s: float = 0.01) -> None:
        self.ip = ip
        self.state = MockState(fs_hz=fs_hz, iq_max_n_samples=iq_max_n_samples)
        self.lock = threading.Lock()
        self.tlm_period_s = tlm_period_s
        self.stop_event = threading.Event()

        # Bind sockets; ports auto-assigned if 0
        self.iq_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.iq_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.iq_listener.bind((ip, iq_port))
        self.iq_listener.listen(4)
        self.iq_port = self.iq_listener.getsockname()[1]

        self.tlm_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tlm_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tlm_listener.bind((ip, tlm_port))
        self.tlm_listener.listen(2)
        self.tlm_port = self.tlm_listener.getsockname()[1]

        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        for target in (self._iq_accept_loop, self._tlm_accept_loop):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self.threads.append(t)

    def stop(self) -> None:
        self.stop_event.set()
        try:
            self.iq_listener.close()
        except OSError:
            pass
        try:
            self.tlm_listener.close()
        except OSError:
            pass

    # ---------------- IQ ingress ----------------

    def _iq_accept_loop(self) -> None:
        self.iq_listener.settimeout(0.2)
        while not self.stop_event.is_set():
            try:
                conn, _ = self.iq_listener.accept()
            except (socket.timeout, OSError):
                continue
            t = threading.Thread(target=self._iq_handle_connection,
                                  args=(conn,), daemon=True)
            t.start()

    def _iq_handle_connection(self, conn: socket.socket) -> None:
        conn.settimeout(2.0)
        try:
            while not self.stop_event.is_set():
                hdr_bytes = self._recv_or_close(conn, s.HEADER_SIZE)
                if hdr_bytes is None:
                    return
                hdr = s.Header.unpack(hdr_bytes)

                # Negative checks consume header only (no payload follows on these)
                if hdr.magic != s.IQ_MAGIC_IQ:
                    self._inc_hdr_fail()
                    continue
                with self.lock:
                    fs_match = (hdr.fs_hz == self.state.fs_hz)
                    n_too_large = (hdr.n_samples > self.state.iq_max_n_samples)
                if not fs_match or n_too_large:
                    self._inc_hdr_fail()
                    continue

                # Payload follows
                payload_size = hdr.n_samples * 4
                payload = self._recv_or_close(conn, payload_size)
                if payload is None:
                    return
                self._process_iq_frame(hdr, payload)
        finally:
            conn.close()

    def _recv_or_close(self, conn: socket.socket, nbytes: int):
        chunks = []
        got = 0
        while got < nbytes:
            try:
                chunk = conn.recv(nbytes - got)
            except (socket.timeout, OSError):
                return None
            if not chunk:
                return None
            chunks.append(chunk)
            got += len(chunk)
        return b"".join(chunks)

    def _inc_hdr_fail(self) -> None:
        with self.lock:
            self.state.hdr_fail_count += 1

    def _process_iq_frame(self, hdr: s.Header, payload: bytes) -> None:
        """Mirror R5 firmware's behavior:
        - IQ_FLAG_SOS sets sample_index as a new resync baseline (no drop).
        - Without SOS, sample_index mismatch → drop_count += n_samples; payload not
          accumulated.
        - Match → accumulate sample_count, byte_count, crc32_current.
        """
        with self.lock:
            if hdr.flags & s.IQ_FLAG_SOS:
                self.state.expected_next_index = hdr.sample_index
            if hdr.sample_index != self.state.expected_next_index:
                self.state.drop_count += hdr.n_samples
                self.state.expected_next_index = hdr.sample_index + hdr.n_samples
                return
            self.state.sample_count += hdr.n_samples
            self.state.byte_count += len(payload)
            self.state.crc32_current = (zlib.crc32(payload, self.state.crc32_current)
                                          & 0xFFFFFFFF)
            self.state.expected_next_index = hdr.sample_index + hdr.n_samples

    # ---------------- Telemetry egress ----------------

    def _tlm_accept_loop(self) -> None:
        self.tlm_listener.settimeout(0.2)
        while not self.stop_event.is_set():
            try:
                conn, _ = self.tlm_listener.accept()
            except (socket.timeout, OSError):
                continue
            t = threading.Thread(target=self._tlm_send_loop,
                                  args=(conn,), daemon=True)
            t.start()

    def _tlm_send_loop(self, conn: socket.socket) -> None:
        try:
            while not self.stop_event.is_set():
                with self.lock:
                    snapshot = MockState(**self.state.__dict__)
                    self.state.t_us_counter += int(self.tlm_period_s * 1_000_000)
                    self.state.tlm_seq = (self.state.tlm_seq + 1) & 0xFFFFFFFF
                hdr = s.Header(
                    magic=s.IQ_MAGIC_TLM,
                    seq=snapshot.tlm_seq,
                    sample_index=snapshot.sample_count,  # contract: sample_index == sample_count
                    fs_hz=snapshot.fs_hz,
                    n_samples=0,
                    channel_id=s.IQ_CH_L1,
                    fmt=s.IQ_FMT_INT16_IQ_INTERLEAVED,
                    flags=s.IQ_FLAG_TAG,
                    reserved=0,
                )
                tlm_bytes = s.TELEMETRY_STRUCT.pack(
                    snapshot.t_us_counter,
                    snapshot.sample_count,
                    snapshot.byte_count,
                    snapshot.crc32_current,
                    snapshot.drop_count,
                    snapshot.underrun_count,
                    snapshot.dma_status,
                    snapshot.axis_fifo_level,
                    snapshot.hdr_fail_count,
                    0, 0, 0, 0,
                )
                try:
                    conn.sendall(hdr.pack() + tlm_bytes)
                except (BrokenPipeError, OSError):
                    return
                time.sleep(self.tlm_period_s)
        finally:
            conn.close()


# ============================================================
# Smoke test runner
# ============================================================

def smoke_test_digital_loopback() -> int:
    """Spin up a mock + run default_digital_matrix; assert PASS."""
    print("=== smoke test: digital_loopback ===")
    server = MockServer(ip="127.0.0.1", iq_port=0, tlm_port=0)
    server.start()
    try:
        time.sleep(0.05)  # let listeners come up
        config = s.StreamingConfig(
            enabled=True,
            test_mode="digital_loopback",
            ip="127.0.0.1",
            iq_port=server.iq_port,
            tlm_port=server.tlm_port,
            fs_hz=s.DEFAULT_FS_HZ,
            connect_timeout_ms=2000,
            run_timeout_ms=5000,
        )
        rc = s.default_digital_matrix(config, samples=1024,
                                        telemetry_baseline_frames=3)
        if rc != 0:
            print("FAIL: digital_loopback smoke test returned non-zero", file=sys.stderr)
            return 1
    finally:
        server.stop()
    print("PASS: digital_loopback smoke test\n")
    return 0


def smoke_test_analog_loopback_dispatch() -> int:
    """Verify analog_loopback dispatch raises NotImplementedError (plan-05 stub)."""
    print("=== smoke test: analog_loopback dispatch ===")
    expected = {
        "expected_snr_db": 30.0,
        "tolerance_quantization_bits": 4,
    }
    fake_baseline = s.Telemetry(0, 0, 0, 0, 0, 0, 0, 0, 0, (0, 0, 0, 0))
    fake_final = s.Telemetry(1000, 0, 0, 0, 0, 0, 0, 0, 0, (0, 0, 0, 0))
    try:
        s.validate_signal_integrity("analog_loopback", expected,
                                      fake_final, fake_baseline)
    except NotImplementedError as exc:
        msg = str(exc)
        if "plan-05" not in msg:
            print(f"FAIL: NotImplementedError raised but message missing 'plan-05' "
                  f"reference: {msg!r}", file=sys.stderr)
            return 1
        print("PASS: analog_loopback dispatch raises documented NotImplementedError\n")
        return 0
    print("FAIL: analog_loopback dispatch did NOT raise NotImplementedError",
          file=sys.stderr)
    return 1


def smoke_test_disabled() -> int:
    """Verify StreamingConfig.from_hil_json returns enabled=False when streaming
    is absent or streaming.enabled=false."""
    print("=== smoke test: backward-compat (streaming absent) ===")
    import tempfile
    import json

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"dut": {"entity": "foo"}, "axi": {"base_address": "0x43C00000"}}, f)
        path1 = f.name
    cfg = s.StreamingConfig.from_hil_json(path1)
    if cfg.enabled:
        print("FAIL: streaming absent should yield enabled=False", file=sys.stderr)
        return 1

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"streaming": {"enabled": False}}, f)
        path2 = f.name
    cfg = s.StreamingConfig.from_hil_json(path2)
    if cfg.enabled:
        print("FAIL: streaming.enabled=false should yield enabled=False",
              file=sys.stderr)
        return 1
    print("PASS: backward-compat (streaming absent / enabled=false)\n")
    return 0


def smoke_test_generic_diagnostics_helpers() -> int:
    """Verify generic Ethernet diagnostic helpers without hardware or tshark."""
    print("=== smoke test: generic Ethernet diagnostics helpers ===")
    uart_text = "\n".join(
        [
            "UNDERRUN_TIMING: start=0 final_delta=3 bins=2",
            "UNDERRUN_BIN: idx=0 start_s=0 delta=0",
            "UNDERRUN_BIN: idx=1 start_s=1 delta=0",
            "UNDERRUN_BIN_PARTIAL: idx=2 start_s=2 delta=3",
            "UART_FINAL: crc=0x383643a7 drop=0 underrun=3 "
            "output_stall=0:00000000 hdr_fail=0",
            "UART_FINAL_DELTA: samples=123 bytes=456 underrun=3 "
            "output_stall=0:00000000",
            "UART_FINAL: pbufs=0 queued=0 owned=0 owned_hi=7 "
            "drain_pkt_hi=4 submit_fail=0",
            "UART_PROFILE: backlog q_hi=16777216/2048 dma_hi=8192/1 "
            "total_hi=16777216 credit_pending_hi=4096 "
            "credit_defer=10 credit_release=10",
        ]
    )
    parsed = s.parse_strict_uart_text(uart_text)
    ok, errors, summary = s.evaluate_strict_uart(parsed)
    if not ok:
        print(f"FAIL: strict UART helper rejected EOS-tail-only case: {errors}",
              file=sys.stderr)
        return 1
    if summary["active_underrun_delta"] != 0:
        print(f"FAIL: active underrun should be 0: {summary!r}", file=sys.stderr)
        return 1
    if summary["eos_tail_excluded_delta"] != 3:
        print(f"FAIL: EOS tail should be 3: {summary!r}", file=sys.stderr)
        return 1

    active_text = uart_text.replace(
        "UNDERRUN_BIN: idx=1 start_s=1 delta=0",
        "UNDERRUN_BIN: idx=1 start_s=1 delta=1",
    )
    active_ok, active_errors, _summary = s.evaluate_strict_uart(
        s.parse_strict_uart_text(active_text))
    if active_ok or not any("active underrun" in err for err in active_errors):
        print("FAIL: strict UART helper did not fail active underrun",
              file=sys.stderr)
        return 1

    bins, stats = s.parse_tshark_payload_fields(
        ["100.000\t100\n", "100.050\t50\n", "100.160\t25\n"],
        start_epoch=100.0,
        bin_ms=100,
        display_filter="tcp.len > 0",
    )
    if bins != {0: {"bytes": 150, "packets": 2},
                1: {"bytes": 25, "packets": 1}}:
        print(f"FAIL: unexpected TShark bins: {bins!r}", file=sys.stderr)
        return 1
    if stats["payload_bytes"] != 175 or stats["payload_packets"] != 3:
        print(f"FAIL: unexpected TShark stats: {stats!r}", file=sys.stderr)
        return 1

    with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
        f.write(b"streaming-crc-fixture")
        crc_path = f.name
    try:
        crc = s.file_crc32(crc_path)
        s.assert_expected_crc32("fixture", crc, s.crc32_hex(crc))
    finally:
        os.unlink(crc_path)

    sizing = s.analyze_streaming_lwip_sizing(
        pbuf_pool_bufsize=9700,
        max_frame_size_jumbo=10368,
        tcp_snd_buf=262144,
        requested_rcv_window_bytes=16 * 1024 * 1024,
        tcp_rcv_scale=8,
        tcp_window_scaling=False,
        tcp_write_flag_more_on_iq_header=True,
        reservoir_low_bytes=8 * 1024 * 1024,
        reservoir_high_bytes=16 * 1024 * 1024,
    )
    if sizing["ok"]:
        print("FAIL: lwIP sizing helper missed intentionally bad profile",
              file=sys.stderr)
        return 1
    if sizing["recommended_tcp_rcv_scale"] != 9:
        print(f"FAIL: expected TCP_RCV_SCALE 9: {sizing!r}", file=sys.stderr)
        return 1
    if not any("TCP_WRITE_FLAG_MORE" in issue for issue in sizing["issues"]):
        print(f"FAIL: missing header MORE warning: {sizing!r}", file=sys.stderr)
        return 1

    clean_sizing = s.analyze_streaming_lwip_sizing(
        pbuf_pool_bufsize=10368,
        max_frame_size_jumbo=10368,
        tcp_snd_buf=65535,
        requested_rcv_window_bytes=16 * 1024 * 1024,
        tcp_rcv_scale=9,
        tcp_window_scaling=True,
        tcp_write_flag_more_on_iq_header=False,
        reservoir_low_bytes=8 * 1024 * 1024,
        reservoir_high_bytes=16 * 1024 * 1024,
    )
    if not clean_sizing["ok"]:
        print(f"FAIL: lwIP sizing helper rejected clean profile: {clean_sizing!r}",
              file=sys.stderr)
        return 1

    print("PASS: generic Ethernet diagnostics helpers\n")
    return 0


def _run_mock_multifw(with_post_ready: bool) -> int:
    events = []
    event_lock = threading.Lock()

    def record(name, value):
        with event_lock:
            events.append((name, value))

    class FakeUartCapture:
        instances = []

        def __init__(self, port, baud=115200, pass_marker="HIL_PASS",
                     fail_marker="HIL_FAIL", pass_markers=None,
                     match_mode="all", log_path=None):
            self.port = port
            self.pass_markers = pass_markers or [pass_marker]
            self.matched = {pattern: False for pattern in self.pass_markers}
            self.log_path = log_path
            self.stop_event = threading.Event()
            self.thread = None
            FakeUartCapture.instances.append(self)

        def start(self):
            role = os.path.basename(self.log_path or self.port)

            def _run():
                record("uart-start", role)
                while not self.stop_event.is_set():
                    time.sleep(0.005)

            self.thread = threading.Thread(target=_run, daemon=True)
            self.thread.start()

        def wait_result(self, timeout):
            time.sleep(0.02)
            for marker in self.pass_markers:
                self.matched[marker] = True
            record("markers", tuple(self.pass_markers))
            return "PASS"

        def stop(self):
            record("uart-stop", os.path.basename(self.log_path or self.port))
            self.stop_event.set()
            if self.thread:
                self.thread.join(timeout=1)

    original_uart = hr.UartCapture
    original_select_uart = hr._select_uart_for_role
    original_first_cmd = hr._build_first_firmware_cmd
    original_secondary_cmd = hr._build_secondary_firmware_cmd
    original_run = hr.subprocess.run

    try:
        with tempfile.TemporaryDirectory() as project_dir:
            build_dir = os.path.join(project_dir, "build", "hil")
            os.makedirs(build_dir, exist_ok=True)
            a53_elf = os.path.join(project_dir, "a53.elf")
            r5_elf = os.path.join(project_dir, "r5.elf")
            for path in (a53_elf, r5_elf):
                with open(path, "w") as f:
                    f.write("mock elf\n")
            sentinel = os.path.join(project_dir, "post-ready.txt")
            post_cmd = [
                sys.executable,
                "-c",
                (
                    "import os, pathlib; "
                    f"pathlib.Path({sentinel!r}).write_text("
                    "os.environ['HIL_PROJECT_DIR'])"
                ),
            ]

            firmware = {
                "firmwares": [
                    {
                        "role": "A53",
                        "elf": "a53.elf",
                        "uart_role": "a53",
                        "pass_markers": ["A53_READY"],
                    },
                    {
                        "role": "R5_0",
                        "elf": "r5.elf",
                        "uart_role": "r5",
                        "pass_markers": ["R5_READY"],
                    },
                ],
            }
            if with_post_ready:
                firmware["post_ready_cmd"] = post_cmd
                firmware["post_ready_timeout_s"] = 5

            hil_config = {
                "firmware": firmware,
                "board": {"family": "zynqmp"},
            }

            def fake_run(cmd, *args, **kwargs):
                if cmd and cmd[0] == "mock_flash":
                    record("flash", cmd[1])
                    return subprocess.CompletedProcess(
                        cmd, 0, stdout=f"{cmd[1]} flashed\n")
                if with_post_ready and cmd == post_cmd:
                    alive = [
                        uart.thread is not None and uart.thread.is_alive()
                        for uart in FakeUartCapture.instances
                    ]
                    record("post-dispatch", alive)
                return original_run(cmd, *args, **kwargs)

            hr.UartCapture = FakeUartCapture
            hr._select_uart_for_role = lambda _cfg, role: f"mock-{role}"
            hr._build_first_firmware_cmd = (
                lambda *args, **kwargs: (["mock_flash", "A53"], "mock_first.tcl")
            )
            hr._build_secondary_firmware_cmd = (
                lambda *args, **kwargs: (["mock_flash", "R5_0"], "mock_second.tcl")
            )
            hr.subprocess.run = fake_run

            class Args:
                timeout = 1

            rc = hr._run_multi_firmware(
                hil_config=hil_config,
                project_dir=project_dir,
                build_dir=build_dir,
                bitstream="mock.bit",
                boot_init="mock_init.tcl",
                family="zynqmp",
                zynqmp_boot_elfs=[],
                xsdb="mock_xsdb",
                args=Args(),
                processor="psu_cortexa53_0",
                no_os_flow=False,
                timestamp="mock",
            )
            if rc != 0:
                print("FAIL: multi-firmware mock returned non-zero", file=sys.stderr)
                return 1

            marker_indexes = [
                idx for idx, event in enumerate(events)
                if event[0] == "markers"
            ]
            if len(marker_indexes) != 2:
                print(f"FAIL: expected two marker events, got {events!r}",
                      file=sys.stderr)
                return 1

            post_indexes = [
                idx for idx, event in enumerate(events)
                if event[0] == "post-dispatch"
            ]
            stop_indexes = [
                idx for idx, event in enumerate(events)
                if event[0] == "uart-stop"
            ]

            if with_post_ready:
                if not os.path.isfile(sentinel):
                    print("FAIL: post_ready_cmd did not write sentinel",
                          file=sys.stderr)
                    return 1
                if open(sentinel).read() != project_dir:
                    print("FAIL: post_ready_cmd did not receive HIL_PROJECT_DIR",
                          file=sys.stderr)
                    return 1
                if len(post_indexes) != 1:
                    print(f"FAIL: expected one post dispatch, got {events!r}",
                          file=sys.stderr)
                    return 1
                post_idx = post_indexes[0]
                if not all(idx < post_idx for idx in marker_indexes):
                    print(f"FAIL: post_ready_cmd ran before all markers: {events!r}",
                          file=sys.stderr)
                    return 1
                alive_at_post = events[post_idx][1]
                if alive_at_post != [True, True]:
                    print(f"FAIL: UART loggers not alive at post dispatch: "
                          f"{alive_at_post!r}", file=sys.stderr)
                    return 1
                if stop_indexes and min(stop_indexes) < post_idx:
                    print(f"FAIL: UART logger stopped before post dispatch: {events!r}",
                          file=sys.stderr)
                    return 1
            else:
                if post_indexes:
                    print(f"FAIL: no-postready config dispatched post cmd: {events!r}",
                          file=sys.stderr)
                    return 1
                if os.path.exists(sentinel):
                    print("FAIL: no-postready config wrote sentinel",
                          file=sys.stderr)
                    return 1
            return 0
    finally:
        hr.UartCapture = original_uart
        hr._select_uart_for_role = original_select_uart
        hr._build_first_firmware_cmd = original_first_cmd
        hr._build_secondary_firmware_cmd = original_secondary_cmd
        hr.subprocess.run = original_run


def smoke_test_multifw_post_ready() -> int:
    print("=== smoke test: multi-firmware post_ready_cmd ordering ===")
    rc = _run_mock_multifw(with_post_ready=True)
    if rc == 0:
        print("PASS: multi-firmware post_ready_cmd ordering\n")
    return rc


def smoke_test_multifw_no_post_ready() -> int:
    print("=== smoke test: multi-firmware no-postready compatibility ===")
    rc = _run_mock_multifw(with_post_ready=False)
    if rc == 0:
        print("PASS: multi-firmware no-postready compatibility\n")
    return rc


def smoke_test_multifw_stage16_mock() -> int:
    print("=== smoke test: multi-firmware Stage 16 per-entry build ===")
    original_find_xsct = hf.find_xsct
    original_run_no_os_make = hf.run_no_os_make
    original_run = hf.subprocess.run

    try:
        with tempfile.TemporaryDirectory() as project_dir:
            build_dir = os.path.join(project_dir, "build", "hil")
            os.makedirs(build_dir, exist_ok=True)
            os.makedirs(os.path.join(project_dir, "sw", "r5", "drv"), exist_ok=True)
            os.makedirs(os.path.join(project_dir, "sw", "shared"), exist_ok=True)
            with open(os.path.join(build_dir, "system_wrapper.xsa"), "w") as f:
                f.write("mock xsa\n")
            with open(os.path.join(project_dir, "sw", "r5", "main.c"), "w") as f:
                f.write("int main(void) { return 0; }\n")
            with open(os.path.join(project_dir, "sw", "r5", "drv", "drv.c"), "w") as f:
                f.write("void drv(void) {}\n")
            with open(os.path.join(project_dir, "sw", "shared", "shared.c"), "w") as f:
                f.write("void shared(void) {}\n")

            hil_config = {
                "firmware": {
                    "firmwares": [
                        {
                            "role": "A53",
                            "flow": "no_os_make",
                            "elf": "build/hil/no_os/ad9081.elf",
                        },
                        {
                            "role": "R5_0",
                            "flow": "vitis",
                            "processor": "psu_cortexr5_0",
                            "elf": "build/hil/vitis_ws/hil_r5/Debug/hil_r5.elf",
                            "test_src": "sw/r5/main.c",
                            "source_roots": [
                                {"src": "sw/r5/drv", "dest": "drv"},
                                {"src": "sw/shared", "dest": "shared"},
                            ],
                            "linker_placement": {
                                "memory_region": "psu_r5_ddr_0_MEM_0",
                                "origin": "0x50000000",
                                "length": "0x18000000",
                            },
                        },
                    ]
                }
            }
            socks_cfg = {
                "build": {
                    "no_os_make": {
                        "no_os_root": "ADI/no-OS/work/active",
                        "project_dir": "projects/ad9081",
                        "hardware": "build/hil/system_wrapper.xsa",
                    }
                }
            }

            def fake_no_os(project_dir_arg, build_dir_arg, cfg, settings_path):
                no_os_dir = os.path.join(build_dir_arg, "no_os")
                os.makedirs(no_os_dir, exist_ok=True)
                with open(os.path.join(no_os_dir, "ad9081.elf"), "w") as f:
                    f.write("mock no-os elf\n")
                return 0

            def fake_run(cmd, *args, **kwargs):
                inner = cmd[2] if isinstance(cmd, list) and len(cmd) >= 3 else ""
                ws_dir = os.path.join(build_dir, "vitis_ws")
                app_dir = os.path.join(ws_dir, "hil_r5")
                if "--phase create" in inner:
                    os.makedirs(os.path.join(app_dir, "src"), exist_ok=True)
                    with open(os.path.join(app_dir, "src", "lscript.ld"), "w") as f:
                        f.write(
                            "MEMORY {\n"
                            "  psu_r5_ddr_0_MEM_0 : ORIGIN = 0x00000000, "
                            "LENGTH = 0x00100000\n"
                            "}\n"
                        )
                    return subprocess.CompletedProcess(cmd, 0, stdout="create\n", stderr="")
                if "--phase build" in inner:
                    os.makedirs(os.path.join(app_dir, "Debug"), exist_ok=True)
                    with open(os.path.join(app_dir, "Debug", "hil_r5.elf"), "w") as f:
                        f.write("mock r5 elf\n")
                    return subprocess.CompletedProcess(cmd, 0, stdout="build\n", stderr="")
                return original_run(cmd, *args, **kwargs)

            hf.find_xsct = lambda: "mock_xsct"
            hf.run_no_os_make = fake_no_os
            hf.subprocess.run = fake_run

            class Args:
                debug = False
                settings = None

            rc = hf.run_multi_firmware_build(
                project_dir, build_dir, socks_cfg, hil_config, Args())
            if rc != 0:
                print("FAIL: multi-firmware Stage 16 mock returned non-zero",
                      file=sys.stderr)
                return 1

            r5_elf = os.path.join(
                build_dir, "vitis_ws", "hil_r5", "Debug", "hil_r5.elf")
            no_os_elf = os.path.join(build_dir, "no_os", "ad9081.elf")
            lscript = os.path.join(build_dir, "vitis_ws", "hil_r5", "src",
                                   "lscript.ld")
            staged_drv = os.path.join(build_dir, "fw_src", "R5_0", "drv", "drv.c")
            if not os.path.isfile(no_os_elf) or not os.path.isfile(r5_elf):
                print("FAIL: expected per-entry ELF outputs missing",
                      file=sys.stderr)
                return 1
            if not os.path.isfile(staged_drv):
                print("FAIL: per-entry source root was not staged",
                      file=sys.stderr)
                return 1
            lscript_text = open(lscript).read()
            if "0x50000000" not in lscript_text or "0x18000000" not in lscript_text:
                print("FAIL: per-entry linker placement was not applied",
                      file=sys.stderr)
                return 1
    finally:
        hf.find_xsct = original_find_xsct
        hf.run_no_os_make = original_run_no_os_make
        hf.subprocess.run = original_run

    print("PASS: multi-firmware Stage 16 per-entry build\n")
    return 0


def main() -> int:
    rc = 0
    rc |= smoke_test_disabled()
    rc |= smoke_test_generic_diagnostics_helpers()
    rc |= smoke_test_digital_loopback()
    rc |= smoke_test_analog_loopback_dispatch()
    rc |= smoke_test_multifw_stage16_mock()
    rc |= smoke_test_multifw_post_ready()
    rc |= smoke_test_multifw_no_post_ready()
    if rc == 0:
        print("PASS: all smoke tests")
    else:
        print("FAIL: one or more smoke tests failed")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
