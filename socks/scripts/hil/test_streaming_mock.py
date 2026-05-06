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

import socket
import struct
import sys
import threading
import time
import zlib
from dataclasses import dataclass

# Import the helper module under test. When run as a script, the parent dir of
# hil/ is on sys.path via the conventional layout.
import streaming as s


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


def main() -> int:
    rc = 0
    rc |= smoke_test_disabled()
    rc |= smoke_test_digital_loopback()
    rc |= smoke_test_analog_loopback_dispatch()
    if rc == 0:
        print("PASS: all smoke tests")
    else:
        print("FAIL: one or more smoke tests failed")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
