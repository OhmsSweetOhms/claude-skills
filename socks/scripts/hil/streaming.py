"""
SOCKS HIL streaming-mode helper library.

This module is the canonical lift of the proven ZCU102 streaming-validation pattern
(systems/zcu102-gps-streaming/tools/streaming_tcp_check.py). Consumer projects
import it from their hil.json::firmware.post_ready_cmd checker scripts to get the
wire-protocol structs, bounded-retry connect helpers, telemetry contract validation,
scenario runners, and topology-conditional signal-integrity validators.

Usage pattern from a consumer's checker script:

    from socks.scripts.hil import streaming as s

    config = s.StreamingConfig.from_hil_json("hil.json")
    if not config.enabled:
        sys.exit(0)
    sys.exit(s.default_digital_matrix(config))

For analog_loopback (gated on fpga/20260424-zcu102-streaming-system plan-05),
use validate_signal_integrity(test_mode="analog_loopback", ...) once the substrate
brings up the JESD204C + DAC + cable + ADC chain.

Contract source: live ZCU102 substrate, Stage 17 PASS 2026-04-28
(samples=1163, crc=0x55244279, drop=16, hdr_fail=3, dma=0x0001100a).
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


# ============================================================
# Wire-protocol constants
# ============================================================

IQ_MAGIC_IQ = 0x31205149   # "IQ 1" little-endian
IQ_MAGIC_TLM = 0x314D4C54  # "TLM1" little-endian

IQ_FLAG_SOS = 1 << 0
IQ_FLAG_EOS = 1 << 1
IQ_FLAG_TAG = 1 << 2

IQ_FMT_INT16_IQ_INTERLEAVED = 0
IQ_CH_L1 = 0

DEFAULT_FS_HZ = 4_096_000
DEFAULT_IQ_MAX_N_SAMPLES = 16_384
DEFAULT_CONNECT_RETRY_INTERVAL_S = 0.25
DEFAULT_DMA_ERROR_MASK = 0x70

HEADER_STRUCT = struct.Struct("<IIQIIHBBI")
TELEMETRY_STRUCT = struct.Struct("<QQQIIIIIIIIII")

HEADER_SIZE = HEADER_STRUCT.size
TELEMETRY_SIZE = TELEMETRY_STRUCT.size
assert HEADER_SIZE == 32, f"unexpected HEADER_SIZE={HEADER_SIZE}"
assert TELEMETRY_SIZE == 64, f"unexpected TELEMETRY_SIZE={TELEMETRY_SIZE}"


# ============================================================
# Wire-protocol structs
# ============================================================

@dataclass(frozen=True)
class Header:
    magic: int
    seq: int
    sample_index: int
    fs_hz: int
    n_samples: int
    channel_id: int
    fmt: int
    flags: int
    reserved: int

    @classmethod
    def unpack(cls, data: bytes) -> "Header":
        return cls(*HEADER_STRUCT.unpack(data))

    def pack(self) -> bytes:
        return HEADER_STRUCT.pack(
            self.magic, self.seq, self.sample_index, self.fs_hz,
            self.n_samples, self.channel_id, self.fmt, self.flags, self.reserved,
        )


@dataclass(frozen=True)
class Telemetry:
    t_us: int
    sample_count: int
    byte_count: int
    crc32_current: int
    drop_count: int
    underrun_count: int
    dma_status: int
    axis_fifo_level: int
    hdr_fail_count: int
    reserved: tuple

    @classmethod
    def unpack(cls, data: bytes) -> "Telemetry":
        vals = TELEMETRY_STRUCT.unpack(data)
        return cls(*vals[:9], tuple(vals[9:]))


@dataclass(frozen=True)
class IqFrame:
    seq: int
    sample_index: int
    n_samples: int
    flags: int
    payload: bytes
    fs_hz: int = DEFAULT_FS_HZ
    channel_id: int = IQ_CH_L1

    def pack(self) -> bytes:
        hdr = Header(
            magic=IQ_MAGIC_IQ,
            seq=self.seq,
            sample_index=self.sample_index,
            fs_hz=self.fs_hz,
            n_samples=self.n_samples,
            channel_id=self.channel_id,
            fmt=IQ_FMT_INT16_IQ_INTERLEAVED,
            flags=self.flags,
            reserved=0,
        )
        return hdr.pack() + self.payload


# ============================================================
# Streaming configuration (lifted from hil.json::streaming.*)
# ============================================================

@dataclass(frozen=True)
class StreamingConfig:
    enabled: bool = False
    test_mode: str = "digital_loopback"
    ip: str = "192.168.1.10"
    iq_port: int = 5001
    tlm_port: int = 5002
    fs_hz: int = DEFAULT_FS_HZ
    connect_timeout_ms: int = 20000
    run_timeout_ms: int = 60000
    iq_max_n_samples: int = DEFAULT_IQ_MAX_N_SAMPLES
    expected_signal_integrity: dict = field(default_factory=dict)
    checker_cmd: Optional[list] = None

    @classmethod
    def from_hil_json(cls, hil_json_path) -> "StreamingConfig":
        with open(hil_json_path) as f:
            d = json.load(f)
        s = d.get("streaming", {})
        if not s.get("enabled", False):
            return cls(enabled=False)
        return cls(
            enabled=True,
            test_mode=s.get("test_mode", "digital_loopback"),
            ip=s["ip"],
            iq_port=s["iq_port"],
            tlm_port=s["tlm_port"],
            fs_hz=s["fs_hz"],
            connect_timeout_ms=s.get("connect_timeout_ms", 20000),
            run_timeout_ms=s.get("run_timeout_ms", 60000),
            iq_max_n_samples=s.get("iq_max_n_samples", DEFAULT_IQ_MAX_N_SAMPLES),
            expected_signal_integrity=s.get("expected_signal_integrity", {}),
            checker_cmd=s.get("checker_cmd"),
        )

    @property
    def connect_timeout_s(self) -> float:
        return self.connect_timeout_ms / 1000.0

    @property
    def run_timeout_s(self) -> float:
        return self.run_timeout_ms / 1000.0


# ============================================================
# TCP connection helpers
# ============================================================

def connect_tcp(host: str, port: int, timeout_s: float,
                retry_interval_s: float = DEFAULT_CONNECT_RETRY_INTERVAL_S
                ) -> socket.socket:
    """Bounded-retry TCP connect; raises on deadline."""
    deadline = time.monotonic() + timeout_s
    last_exc = None
    while time.monotonic() < deadline:
        try:
            remaining = max(0.1, deadline - time.monotonic())
            return socket.create_connection((host, port), timeout=min(2.0, remaining))
        except OSError as exc:
            last_exc = exc
            time.sleep(retry_interval_s)
    if last_exc is not None:
        raise last_exc
    raise TimeoutError(f"timed out connecting to {host}:{port}")


def connect_iq(host: str, port: int, timeout_s: float) -> socket.socket:
    """Connect to IQ ingress port; sets TCP_NODELAY."""
    sock = connect_tcp(host, port, timeout_s)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock


def connect_telemetry(host: str, port: int, timeout_s: float) -> socket.socket:
    """Connect to telemetry egress port."""
    return connect_tcp(host, port, timeout_s)


def recv_exact(sock: socket.socket, nbytes: int, timeout_s: float) -> bytes:
    """Receive exactly nbytes; raise if socket closes early."""
    sock.settimeout(timeout_s)
    chunks = []
    got = 0
    while got < nbytes:
        chunk = sock.recv(nbytes - got)
        if not chunk:
            raise RuntimeError(f"socket closed while reading {nbytes} bytes")
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


# ============================================================
# Telemetry stream reader with contract validation
# ============================================================

class TelemetryReader:
    """Reads telemetry frames and validates the contract (monotonic seq + t_us,
    sample_index == sample_count, fs_hz match, n_samples=0, channel_id, fmt, flags,
    reserved=0)."""

    def __init__(self, sock: socket.socket, timeout_s: float,
                 fs_hz: int = DEFAULT_FS_HZ):
        self.sock = sock
        self.timeout_s = timeout_s
        self.fs_hz = fs_hz
        self.prev_hdr: Optional[Header] = None
        self.prev_t_us: Optional[int] = None
        self.frames_seen = 0

    def read(self, timeout_s: Optional[float] = None) -> tuple:
        timeout = self.timeout_s if timeout_s is None else timeout_s
        hdr = Header.unpack(recv_exact(self.sock, HEADER_SIZE, timeout))
        payload = recv_exact(self.sock, TELEMETRY_SIZE, timeout)
        tlm = Telemetry.unpack(payload)
        self._validate_contract(hdr, tlm, len(payload))
        self.prev_hdr = hdr
        self.prev_t_us = tlm.t_us
        self.frames_seen += 1
        return hdr, tlm

    def _validate_contract(self, hdr: Header, tlm: Telemetry,
                           payload_len: int) -> None:
        errors = []
        if payload_len != TELEMETRY_SIZE:
            errors.append(f"payload size {payload_len} != {TELEMETRY_SIZE}")
        if hdr.magic != IQ_MAGIC_TLM:
            errors.append(f"bad magic 0x{hdr.magic:08x}")
        if self.prev_hdr is not None:
            expected_seq = (self.prev_hdr.seq + 1) & 0xFFFFFFFF
            if hdr.seq != expected_seq:
                errors.append(f"seq {hdr.seq} != {expected_seq}")
        if self.prev_t_us is not None and tlm.t_us < self.prev_t_us:
            errors.append(f"t_us regressed {self.prev_t_us}->{tlm.t_us}")
        if hdr.sample_index != tlm.sample_count:
            errors.append(f"sample_index {hdr.sample_index} != "
                          f"sample_count {tlm.sample_count}")
        if hdr.fs_hz != self.fs_hz:
            errors.append(f"fs_hz {hdr.fs_hz} != {self.fs_hz}")
        if hdr.n_samples != 0:
            errors.append(f"n_samples {hdr.n_samples} != 0")
        if hdr.channel_id != IQ_CH_L1:
            errors.append(f"channel_id {hdr.channel_id} != {IQ_CH_L1}")
        if hdr.fmt != IQ_FMT_INT16_IQ_INTERLEAVED:
            errors.append(f"format {hdr.fmt} != {IQ_FMT_INT16_IQ_INTERLEAVED}")
        if hdr.flags != IQ_FLAG_TAG:
            errors.append(f"flags 0x{hdr.flags:02x} != TAG")
        if hdr.reserved != 0:
            errors.append(f"reserved 0x{hdr.reserved:08x} != 0")
        if any(tlm.reserved):
            errors.append(f"telemetry reserved words not zero: {tlm.reserved}")
        if errors:
            raise RuntimeError("telemetry contract violation: " + "; ".join(errors))


def wait_tlm(reader: TelemetryReader, timeout_s: float,
             predicate: Callable, description: str) -> tuple:
    """Poll telemetry until predicate(Telemetry) is True or timeout."""
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        frame = reader.read(min(1.0, remaining))
        last = frame
        if predicate(frame[1]):
            return frame
    if last is None:
        raise RuntimeError("timed out before receiving telemetry")
    raise RuntimeError(
        f"timed out waiting for {description}; "
        f"last sample_count={last[1].sample_count} "
        f"byte_count={last[1].byte_count} "
        f"crc=0x{last[1].crc32_current:08x} "
        f"drop={last[1].drop_count} "
        f"hdr_fail={last[1].hdr_fail_count} "
        f"dma_status=0x{last[1].dma_status:08x}"
    )


def read_telemetry_baseline(reader: TelemetryReader,
                             n_frames: int = 6) -> Telemetry:
    """Read N telemetry frames to validate contract and capture starting counters."""
    last = None
    for _ in range(n_frames):
        _, last = reader.read()
    if last is None:
        raise RuntimeError("baseline read no frames")
    print(f"PASS: telemetry contract: {n_frames} frames, fixed {TELEMETRY_SIZE}-byte "
          "payload, monotonic seq/t_us")
    return last


# ============================================================
# Test pattern + frame builders
# ============================================================

def make_sawtooth_payload(n_samples: int, start_sample: int = 0) -> bytes:
    """Sawtooth IQ pattern: I=(s & 0x7FF)-1024, Q=1023-(s & 0x7FF)."""
    out = bytearray()
    for i in range(n_samples):
        sample = start_sample + i
        i_val = ((sample & 0x7FF) - 1024)
        q_val = (1023 - (sample & 0x7FF))
        out += struct.pack("<hh", i_val, q_val)
    return bytes(out)


def make_frames(sample_counts, fs_hz: int = DEFAULT_FS_HZ,
                channel_id: int = IQ_CH_L1) -> list:
    """Build IqFrames with SOS on first, EOS on last."""
    frames = []
    sample_index = 0
    for seq, n_samples in enumerate(sample_counts):
        flags = 0
        if seq == 0:
            flags |= IQ_FLAG_SOS
        if seq == len(sample_counts) - 1:
            flags |= IQ_FLAG_EOS
        frames.append(IqFrame(
            seq=seq,
            sample_index=sample_index,
            n_samples=n_samples,
            flags=flags,
            payload=make_sawtooth_payload(n_samples, sample_index),
            fs_hz=fs_hz,
            channel_id=channel_id,
        ))
        sample_index += n_samples
    return frames


def cumulative_crc(seed: int, frames) -> int:
    """Reference CRC32 over frame payloads, seeded with prior telemetry CRC."""
    crc = seed
    for frame in frames:
        crc = zlib.crc32(frame.payload, crc) & 0xFFFFFFFF
    return crc


def header_with(*, magic: int = IQ_MAGIC_IQ, seq: int = 0, sample_index: int = 0,
                fs_hz: int = DEFAULT_FS_HZ, n_samples: int = 0,
                channel_id: int = IQ_CH_L1, fmt: int = IQ_FMT_INT16_IQ_INTERLEAVED,
                flags: int = IQ_FLAG_SOS | IQ_FLAG_EOS, reserved: int = 0
                ) -> Header:
    """Build a test header with overridable fields. Used by negative scenarios."""
    return Header(magic=magic, seq=seq, sample_index=sample_index, fs_hz=fs_hz,
                  n_samples=n_samples, channel_id=channel_id, fmt=fmt,
                  flags=flags, reserved=reserved)


# ============================================================
# Frame senders (different TCP write patterns)
# ============================================================

def send_frames_one_write(host: str, port: int, frames,
                          timeout_s: float) -> socket.socket:
    sock = connect_iq(host, port, timeout_s)
    try:
        sock.sendall(b"".join(frame.pack() for frame in frames))
    except Exception:
        sock.close()
        raise
    return sock


def send_frames_separate_writes(host: str, port: int, frames,
                                 timeout_s: float) -> socket.socket:
    sock = connect_iq(host, port, timeout_s)
    try:
        for frame in frames:
            sock.sendall(frame.pack())
            time.sleep(0.01)
    except Exception:
        sock.close()
        raise
    return sock


def send_frames_split_header_payload(host: str, port: int, frame: IqFrame,
                                      timeout_s: float) -> socket.socket:
    """Send one frame as 4 chunks crossing header/payload boundaries."""
    packet = frame.pack()
    chunks = [
        packet[:7],
        packet[7:HEADER_SIZE],
        packet[HEADER_SIZE : HEADER_SIZE + 13],
        packet[HEADER_SIZE + 13 :],
    ]
    sock = connect_iq(host, port, timeout_s)
    try:
        for chunk in chunks:
            sock.sendall(chunk)
            time.sleep(0.01)
    except Exception:
        sock.close()
        raise
    return sock


# ============================================================
# Scenario runners
# ============================================================

def validate_iq_result(name: str, before: Telemetry, after: Telemetry,
                       frames,
                       dma_error_mask: int = DEFAULT_DMA_ERROR_MASK) -> None:
    expected_samples = sum(frame.n_samples for frame in frames)
    expected_bytes = sum(len(frame.payload) for frame in frames)
    sample_delta = after.sample_count - before.sample_count
    byte_delta = after.byte_count - before.byte_count
    expected_after_crc = cumulative_crc(before.crc32_current, frames)

    errors = []
    if sample_delta != expected_samples:
        errors.append(f"sample delta {sample_delta} != {expected_samples}")
    if byte_delta != expected_bytes:
        errors.append(f"byte delta {byte_delta} != {expected_bytes}")
    if after.crc32_current != expected_after_crc:
        errors.append(f"crc 0x{after.crc32_current:08x} != "
                      f"0x{expected_after_crc:08x}")
    if after.drop_count != before.drop_count:
        errors.append(f"drop_count changed {before.drop_count}->{after.drop_count}")
    if after.hdr_fail_count != before.hdr_fail_count:
        errors.append(f"hdr_fail_count changed {before.hdr_fail_count}->"
                      f"{after.hdr_fail_count}")
    if after.dma_status & dma_error_mask:
        errors.append(f"DMA status has error bits set: 0x{after.dma_status:08x}")
    if errors:
        raise RuntimeError(f"{name}: " + "; ".join(errors))

    print(f"PASS: {name}: packets={len(frames)} samples={sample_delta} "
          f"bytes={byte_delta} crc=0x{after.crc32_current:08x}")


def run_iq_scenario(name: str, reader: TelemetryReader, host: str, port: int,
                    timeout_s: float, frames,
                    sender: Callable) -> Telemetry:
    """Read baseline, send frames, wait for counters to advance, validate."""
    _, before = reader.read(timeout_s)
    target_samples = before.sample_count + sum(f.n_samples for f in frames)
    target_bytes = before.byte_count + sum(len(f.payload) for f in frames)
    with sender(host, port, frames, timeout_s):
        _, after = wait_tlm(
            reader, timeout_s,
            lambda t: t.sample_count >= target_samples and t.byte_count >= target_bytes,
            f"{name} counters",
        )
        time.sleep(0.02)
    validate_iq_result(name, before, after, frames)
    return after


def run_split_write_scenario(reader: TelemetryReader, host: str, port: int,
                              timeout_s: float, fs_hz: int = DEFAULT_FS_HZ
                              ) -> Telemetry:
    """Convenience: 37-sample frame sent as split header/payload chunks."""
    frames = make_frames([37], fs_hz=fs_hz)
    return run_iq_scenario(
        "split header/payload writes", reader, host, port, timeout_s, frames,
        lambda h, p, fs, t: send_frames_split_header_payload(h, p, fs[0], t),
    )


def run_header_negative(name: str, reader: TelemetryReader, host: str, port: int,
                        timeout_s: float, hdr: Header) -> Telemetry:
    """Send a malformed header. Expect hdr_fail_count++ with no other movement."""
    _, before = reader.read(timeout_s)
    target_hdr_fail = before.hdr_fail_count + 1
    with connect_iq(host, port, timeout_s) as sock:
        sock.sendall(hdr.pack())
        _, after = wait_tlm(reader, timeout_s,
                            lambda t: t.hdr_fail_count >= target_hdr_fail,
                            f"{name} hdr_fail")
        time.sleep(0.02)

    errors = []
    if after.hdr_fail_count - before.hdr_fail_count != 1:
        errors.append(f"hdr_fail delta "
                      f"{after.hdr_fail_count - before.hdr_fail_count} != 1")
    if after.drop_count != before.drop_count:
        errors.append(f"drop_count changed {before.drop_count}->{after.drop_count}")
    if after.sample_count != before.sample_count:
        errors.append(f"sample_count changed {before.sample_count}->"
                      f"{after.sample_count}")
    if after.byte_count != before.byte_count:
        errors.append(f"byte_count changed {before.byte_count}->{after.byte_count}")
    if after.crc32_current != before.crc32_current:
        errors.append(f"crc changed 0x{before.crc32_current:08x}->"
                      f"0x{after.crc32_current:08x}")
    if errors:
        raise RuntimeError(f"{name}: " + "; ".join(errors))

    print(f"PASS: {name}: hdr_fail +1, drop +0, "
          f"samples={after.sample_count} bytes={after.byte_count}")
    return after


def run_sample_gap_negative(reader: TelemetryReader, host: str, port: int,
                             timeout_s: float, gap_samples: int = 16,
                             gap_start_sample: int = 64,
                             fs_hz: int = DEFAULT_FS_HZ) -> Telemetry:
    """Send a frame with a sample_index gap. Expect drop_count += gap_samples."""
    _, before = reader.read(timeout_s)
    payload = make_sawtooth_payload(gap_samples, gap_start_sample)
    hdr = header_with(seq=0, sample_index=gap_start_sample, n_samples=gap_samples,
                      flags=0, fs_hz=fs_hz)
    target_drop = before.drop_count + gap_samples
    with connect_iq(host, port, timeout_s) as sock:
        sock.sendall(hdr.pack() + payload)
        _, after = wait_tlm(reader, timeout_s,
                            lambda t: t.drop_count >= target_drop,
                            "sample-index gap drop")
        time.sleep(0.02)

    errors = []
    if after.drop_count - before.drop_count != gap_samples:
        errors.append(f"drop delta {after.drop_count - before.drop_count} != "
                      f"{gap_samples}")
    if after.hdr_fail_count != before.hdr_fail_count:
        errors.append(f"hdr_fail_count changed {before.hdr_fail_count}->"
                      f"{after.hdr_fail_count}")
    if after.sample_count != before.sample_count:
        errors.append(f"sample_count changed {before.sample_count}->"
                      f"{after.sample_count}")
    if after.byte_count != before.byte_count:
        errors.append(f"byte_count changed {before.byte_count}->{after.byte_count}")
    if after.crc32_current != before.crc32_current:
        errors.append(f"crc changed 0x{before.crc32_current:08x}->"
                      f"0x{after.crc32_current:08x}")
    if errors:
        raise RuntimeError("sample-index gap: " + "; ".join(errors))

    print(f"PASS: sample-index gap: drop +{gap_samples}, hdr_fail +0")
    return after


# ============================================================
# Signal-integrity validators (topology-conditional)
# ============================================================

def validate_signal_integrity(test_mode: str, expected: dict,
                              final: Telemetry, baseline: Telemetry) -> None:
    """Dispatch on test_mode to digital or analog validator."""
    if test_mode == "digital_loopback":
        return _validate_digital(expected, final, baseline)
    if test_mode == "analog_loopback":
        return _validate_analog(expected, final, baseline)
    raise ValueError(f"unknown test_mode: {test_mode!r}")


def _validate_digital(expected: dict, final: Telemetry, baseline: Telemetry) -> None:
    """digital_loopback: assert sample/byte counters and drop/hdr_fail bounds."""
    errors = []
    if "expected_sample_count" in expected:
        delta = final.sample_count - baseline.sample_count
        if delta != expected["expected_sample_count"]:
            errors.append(f"sample_count delta {delta} != "
                          f"{expected['expected_sample_count']}")
    if "expected_byte_count" in expected:
        delta = final.byte_count - baseline.byte_count
        if delta != expected["expected_byte_count"]:
            errors.append(f"byte_count delta {delta} != "
                          f"{expected['expected_byte_count']}")
    if "expected_crc32" in expected:
        exp_crc = int(expected["expected_crc32"], 16)
        if final.crc32_current != exp_crc:
            errors.append(f"crc 0x{final.crc32_current:08x} != 0x{exp_crc:08x}")
    if "max_drop_count" in expected:
        drop_delta = final.drop_count - baseline.drop_count
        if drop_delta > expected["max_drop_count"]:
            errors.append(f"drop_count delta {drop_delta} > max "
                          f"{expected['max_drop_count']}")
    if "max_hdr_fail_count" in expected:
        hdr_fail_delta = final.hdr_fail_count - baseline.hdr_fail_count
        if hdr_fail_delta > expected["max_hdr_fail_count"]:
            errors.append(f"hdr_fail_count delta {hdr_fail_delta} > max "
                          f"{expected['max_hdr_fail_count']}")
    if errors:
        raise RuntimeError("digital_loopback signal integrity: " +
                           "; ".join(errors))
    print("PASS: digital_loopback signal integrity")


def _validate_analog(expected: dict, final: Telemetry, baseline: Telemetry) -> None:
    """analog_loopback: SNR/spectral checks. Stub for future plan-05 work."""
    raise NotImplementedError(
        "analog_loopback validation is gated on "
        "fpga/20260424-zcu102-streaming-system plan-05 (Layer -1 + Tx data path "
        "+ DAC/ADC). Schema is ratified now; implementation lands when the "
        "substrate brings up the analog chain. Expected fields when available: "
        "expected_snr_db, expected_spectral_mask, tolerance_quantization_bits, "
        "tolerance_cable_loss_db, expected_pulse_compression_gain_db (pulsed "
        "consumers)."
    )


# ============================================================
# Default digital matrix (live ZCU102 9-scenario pattern)
# ============================================================

def _contiguous_counts(total_samples: int) -> list:
    if total_samples < 3:
        raise ValueError("total_samples must be at least 3")
    first = max(1, total_samples // 4)
    second = max(1, total_samples // 4)
    third = total_samples - first - second
    counts = [first, second, third]
    if any(count > 4096 for count in counts):
        raise ValueError("total_samples must keep every packet at or below 4096")
    return counts


def default_digital_matrix(config: StreamingConfig, samples: int = 1024,
                            telemetry_baseline_frames: int = 6) -> int:
    """Run the live ZCU102 9-scenario digital_loopback matrix.

    Scenarios (order preserved from streaming_tcp_check.py):
      1. Telemetry contract (N baseline frames; monotonic seq/t_us, fixed payload)
      2. Multi-packet contiguous frames (separate writes)
      3. Split header/payload writes (4 chunks crossing 32-byte boundary)
      4. Multiple frames in one write
      5. Bad magic (negative; expects hdr_fail+1)
      6. Bad fs_hz (negative; expects hdr_fail+1)
      7. Sample-index gap (negative; expects drop_count+=gap_samples)
      8. Oversized packet (negative; expects hdr_fail+1)
      9. Post-negative recovery frame (positive; confirms recovery)

    Returns 0 on overall PASS, non-zero on FAIL (suitable for sys.exit).
    """
    counts = _contiguous_counts(samples)
    try:
        with connect_telemetry(config.ip, config.tlm_port,
                                config.connect_timeout_s) as tlm:
            reader = TelemetryReader(tlm, config.run_timeout_s, fs_hz=config.fs_hz)
            baseline = read_telemetry_baseline(reader, telemetry_baseline_frames)
            print(f"baseline: samples={baseline.sample_count} "
                  f"bytes={baseline.byte_count} "
                  f"crc=0x{baseline.crc32_current:08x} drop={baseline.drop_count} "
                  f"hdr_fail={baseline.hdr_fail_count} "
                  f"dma=0x{baseline.dma_status:08x}")

            run_iq_scenario(
                "multi-packet contiguous frames", reader, config.ip, config.iq_port,
                config.run_timeout_s, make_frames(counts, fs_hz=config.fs_hz),
                send_frames_separate_writes,
            )
            run_split_write_scenario(reader, config.ip, config.iq_port,
                                      config.run_timeout_s, fs_hz=config.fs_hz)
            run_iq_scenario(
                "multiple frames in one write", reader, config.ip, config.iq_port,
                config.run_timeout_s, make_frames([19, 23, 29], fs_hz=config.fs_hz),
                send_frames_one_write,
            )

            run_header_negative(
                "bad magic", reader, config.ip, config.iq_port, config.run_timeout_s,
                header_with(magic=0x21444142, fs_hz=config.fs_hz),
            )
            run_header_negative(
                "bad fs_hz", reader, config.ip, config.iq_port, config.run_timeout_s,
                header_with(fs_hz=config.fs_hz + 1),
            )
            run_sample_gap_negative(reader, config.ip, config.iq_port,
                                     config.run_timeout_s, fs_hz=config.fs_hz)
            run_header_negative(
                "oversized packet", reader, config.ip, config.iq_port,
                config.run_timeout_s,
                header_with(n_samples=config.iq_max_n_samples + 1,
                             fs_hz=config.fs_hz),
            )

            final = run_iq_scenario(
                "post-negative recovery frame", reader, config.ip, config.iq_port,
                config.run_timeout_s, make_frames([31], fs_hz=config.fs_hz),
                send_frames_one_write,
            )
            print(f"final:    samples={final.sample_count} "
                  f"bytes={final.byte_count} "
                  f"crc=0x{final.crc32_current:08x} drop={final.drop_count} "
                  f"hdr_fail={final.hdr_fail_count} dma=0x{final.dma_status:08x}")

            if config.expected_signal_integrity:
                validate_signal_integrity(config.test_mode,
                                            config.expected_signal_integrity,
                                            final, baseline)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print("PASS: digital_loopback streaming validation matrix")
    return 0
