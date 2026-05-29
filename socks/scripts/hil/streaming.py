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
import csv
import re
import socket
import struct
import subprocess
import sys
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


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
# Cross-cutting Ethernet diagnostics
# ============================================================

def parse_crc32(value: int | str) -> int:
    """Return an unsigned 32-bit CRC from an int or decimal/hex string."""
    if isinstance(value, str):
        parsed = int(value, 0)
    else:
        parsed = int(value)
    if parsed < 0 or parsed > 0xFFFFFFFF:
        raise ValueError(f"CRC32 outside uint32 range: {value!r}")
    return parsed


def crc32_hex(value: int | str) -> str:
    """Format a CRC32 value in canonical lower-case hex."""
    return f"0x{parse_crc32(value):08x}"


def file_crc32(path: Path | str, chunk_bytes: int = 4 * 1024 * 1024) -> int:
    """Compute a streaming CRC32 over a file without loading it all at once."""
    crc = 0
    with Path(path).open("rb") as f:
        while True:
            chunk = f.read(chunk_bytes)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc) & 0xFFFFFFFF
    return crc


def assert_expected_crc32(label: str, observed_crc32: int | str,
                          expected_crc32: int | str) -> None:
    """Raise if a measured CRC32 does not match the expected gate value."""
    observed = parse_crc32(observed_crc32)
    expected = parse_crc32(expected_crc32)
    if observed != expected:
        raise RuntimeError(
            f"{label}: CRC {crc32_hex(observed)} != expected {crc32_hex(expected)}"
        )


def tshark_capture_filter(dut_host: str, ingress_port: int,
                          egress_port: int) -> str:
    """BPF capture filter for a two-port streaming IQ/TLM TCP pair."""
    return (
        f"host {dut_host} and tcp and "
        f"(port {int(ingress_port)} or port {int(egress_port)})"
    )


def tshark_first_frame_epoch(tshark_bin: str, pcap_path: Path | str
                             ) -> Optional[float]:
    """Return the first pcap frame epoch timestamp via tshark, or None."""
    result = subprocess.run(
        [
            tshark_bin,
            "-r",
            str(pcap_path),
            "-c",
            "1",
            "-T",
            "fields",
            "-e",
            "frame.time_epoch",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return float(text) if text else None


def parse_tshark_payload_fields(lines: Iterable[str], start_epoch: float,
                                bin_ms: int,
                                display_filter: str = ""
                                ) -> tuple[dict[int, dict[str, int]],
                                           dict[str, Any]]:
    """Bin tshark `frame.time_epoch<TAB>tcp.len` field output by payload bytes."""
    if bin_ms <= 0:
        raise ValueError("bin_ms must be positive")

    bins: dict[int, dict[str, int]] = {}
    total_bytes = 0
    packets = 0
    first_epoch = None
    last_epoch = None
    for line in lines:
        fields = line.rstrip("\n").split("\t")
        if len(fields) < 2 or not fields[0] or not fields[1]:
            continue
        try:
            epoch = float(fields[0])
            payload_len = int(fields[1])
        except ValueError:
            continue
        if payload_len <= 0:
            continue
        bucket = int(((epoch - start_epoch) * 1000.0) // bin_ms)
        if bucket < 0:
            bucket = 0
        entry = bins.setdefault(bucket, {"bytes": 0, "packets": 0})
        entry["bytes"] += payload_len
        entry["packets"] += 1
        total_bytes += payload_len
        packets += 1
        first_epoch = epoch if first_epoch is None else min(first_epoch, epoch)
        last_epoch = epoch if last_epoch is None else max(last_epoch, epoch)

    if bins:
        max_bin_bytes = max(entry["bytes"] for entry in bins.values())
        min_nonzero_bin_bytes = min(entry["bytes"] for entry in bins.values())
        max_bucket = max(bins)
        zero_bins = sum(1 for idx in range(max_bucket + 1) if idx not in bins)
    else:
        max_bin_bytes = 0
        min_nonzero_bin_bytes = 0
        max_bucket = -1
        zero_bins = 0

    stats = {
        "display_filter": display_filter,
        "payload_bytes": total_bytes,
        "payload_packets": packets,
        "first_payload_s": None if first_epoch is None else first_epoch - start_epoch,
        "last_payload_s": None if last_epoch is None else last_epoch - start_epoch,
        "max_bin_bytes": max_bin_bytes,
        "min_nonzero_bin_bytes": min_nonzero_bin_bytes,
        "max_bucket": max_bucket,
        "zero_bins": zero_bins,
    }
    return bins, stats


def collect_tshark_payload_bins(tshark_bin: str, pcap_path: Path | str,
                                display_filter: str, start_epoch: float,
                                bin_ms: int
                                ) -> tuple[dict[int, dict[str, int]],
                                           dict[str, Any]]:
    """Run tshark over a pcap and bin TCP payload bytes for one display filter."""
    cmd = [
        tshark_bin,
        "-r",
        str(pcap_path),
        "-Y",
        display_filter,
        "-T",
        "fields",
        "-E",
        "separator=\t",
        "-e",
        "frame.time_epoch",
        "-e",
        "tcp.len",
    ]
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or f"tshark failed for {display_filter}")
    bins, stats = parse_tshark_payload_fields(
        result.stdout.splitlines(), start_epoch, bin_ms,
        display_filter=display_filter)
    return bins, stats


def write_tshark_bin_csv(csv_path: Path | str, bin_ms: int,
                         ingress_bins: dict[int, dict[str, int]],
                         egress_bins: dict[int, dict[str, int]]) -> None:
    """Write aligned ingress/egress payload-bin counters for review plots."""
    max_bucket = max(
        max(ingress_bins.keys(), default=-1),
        max(egress_bins.keys(), default=-1),
    )
    with Path(csv_path).open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "bucket",
                "start_s",
                "ingress_bytes",
                "ingress_packets",
                "egress_bytes",
                "egress_packets",
            ]
        )
        for bucket in range(max_bucket + 1):
            ingress = ingress_bins.get(bucket, {"bytes": 0, "packets": 0})
            egress = egress_bins.get(bucket, {"bytes": 0, "packets": 0})
            writer.writerow(
                [
                    bucket,
                    f"{bucket * bin_ms / 1000.0:.6f}",
                    ingress["bytes"],
                    ingress["packets"],
                    egress["bytes"],
                    egress["packets"],
                ]
            )


def summarize_tshark_pcap(tshark_bin: str, pcap_path: Path | str,
                          ingress_port: int, egress_port: int,
                          bin_ms: int = 100,
                          csv_path: Optional[Path | str] = None
                          ) -> dict[str, Any]:
    """Summarize ingress and egress payload bytes in one streaming pcap."""
    start_epoch = tshark_first_frame_epoch(tshark_bin, pcap_path)
    if start_epoch is None:
        raise RuntimeError("could not read first frame timestamp from pcap")

    ingress_filter = f"tcp.dstport == {int(ingress_port)} && tcp.len > 0"
    egress_filter = f"tcp.srcport == {int(egress_port)} && tcp.len > 0"
    ingress_bins, ingress_stats = collect_tshark_payload_bins(
        tshark_bin, pcap_path, ingress_filter, start_epoch, bin_ms)
    egress_bins, egress_stats = collect_tshark_payload_bins(
        tshark_bin, pcap_path, egress_filter, start_epoch, bin_ms)
    if csv_path is not None:
        write_tshark_bin_csv(csv_path, bin_ms, ingress_bins, egress_bins)
    return {
        "bin_ms": bin_ms,
        "csv": None if csv_path is None else str(csv_path),
        "ingress": ingress_stats,
        "egress": egress_stats,
    }


STRICT_UART_TIMING_RE = re.compile(
    r"UNDERRUN_TIMING: .*final_delta=(?P<final>\d+) .*bins=(?P<bins>\d+)"
)
STRICT_UART_BIN_RE = re.compile(
    r"UNDERRUN_BIN: idx=(?P<idx>\d+) .*delta=(?P<delta>\d+)"
)
STRICT_UART_PARTIAL_RE = re.compile(
    r"UNDERRUN_BIN_PARTIAL: idx=(?P<idx>\d+) .*delta=(?P<delta>\d+)"
)
STRICT_UART_FINAL_RE = re.compile(
    r"UART_FINAL: crc=0x(?P<crc>[0-9a-fA-F]+) drop=(?P<drop>\d+) "
    r"underrun=(?P<underrun>\d+) output_stall=(?P<stall_hi>\d+):"
    r"(?P<stall_lo>[0-9a-fA-F]+) hdr_fail=(?P<hdr>\d+)"
)
STRICT_UART_FINAL_DELTA_RE = re.compile(
    r"UART_FINAL_DELTA: samples=.* underrun=(?P<underrun>\d+) "
    r"output_stall=(?P<stall_hi>\d+):(?P<stall_lo>[0-9a-fA-F]+)"
)
STRICT_UART_PBUFS_RE = re.compile(
    r"UART_FINAL: pbufs=(?P<pbufs>\d+) queued=(?P<queued>\d+) "
    r"owned=(?P<owned>\d+) owned_hi=(?P<owned_hi>\d+) "
    r"drain_pkt_hi=(?P<drain_pkt_hi>\d+) submit_fail=(?P<submit_fail>\d+)"
)
STRICT_UART_BACKLOG_RE = re.compile(
    r"UART_PROFILE: backlog q_hi=(?P<q_hi_bytes>\d+)/(?P<q_hi_pbufs>\d+) "
    r"dma_hi=(?P<dma_hi_bytes>\d+)/(?P<dma_hi_segments>\d+) "
    r"total_hi=(?P<total_hi_bytes>\d+) "
    r"credit_pending_hi=(?P<credit_pending_hi>\d+) "
    r"credit_defer=(?P<credit_defer>\d+) "
    r"credit_release=(?P<credit_release>\d+)"
)


def _strict_uart_stall_value(hi: str, lo: str) -> int:
    return (int(hi, 10) << 32) | int(lo, 16)


def parse_strict_uart_text(text: str) -> dict[str, Any]:
    """Parse final UART timing/accounting lines for strict streaming soaks."""
    out: dict[str, Any] = {
        "underrun_bins": [],
    }
    for line in text.splitlines():
        if match := STRICT_UART_TIMING_RE.search(line):
            out["timing_final_delta"] = int(match.group("final"))
            out["timing_bin_count"] = int(match.group("bins"))
            continue
        if match := STRICT_UART_BIN_RE.search(line):
            out["underrun_bins"].append(
                {
                    "idx": int(match.group("idx")),
                    "delta": int(match.group("delta")),
                }
            )
            continue
        if match := STRICT_UART_PARTIAL_RE.search(line):
            out["partial_bin"] = {
                "idx": int(match.group("idx")),
                "delta": int(match.group("delta")),
            }
            continue
        if match := STRICT_UART_FINAL_RE.search(line):
            out["final"] = {
                "crc32": "0x" + match.group("crc").lower(),
                "drop_count": int(match.group("drop")),
                "underrun_count": int(match.group("underrun")),
                "output_stall_count": _strict_uart_stall_value(
                    match.group("stall_hi"), match.group("stall_lo")
                ),
                "hdr_fail_count": int(match.group("hdr")),
            }
            continue
        if match := STRICT_UART_FINAL_DELTA_RE.search(line):
            out["final_delta"] = {
                "underrun_delta": int(match.group("underrun")),
                "output_stall_delta": _strict_uart_stall_value(
                    match.group("stall_hi"), match.group("stall_lo")
                ),
            }
            continue
        if match := STRICT_UART_PBUFS_RE.search(line):
            out["final_backlog"] = {
                key: int(match.group(key))
                for key in (
                    "pbufs",
                    "queued",
                    "owned",
                    "owned_hi",
                    "drain_pkt_hi",
                    "submit_fail",
                )
            }
            continue
        if match := STRICT_UART_BACKLOG_RE.search(line):
            out["profile_backlog"] = {
                key: int(match.group(key))
                for key in (
                    "q_hi_bytes",
                    "q_hi_pbufs",
                    "dma_hi_bytes",
                    "dma_hi_segments",
                    "total_hi_bytes",
                    "credit_pending_hi",
                    "credit_defer",
                    "credit_release",
                )
            }
    return out


def summarize_strict_uart(parsed: dict[str, Any]) -> dict[str, Any]:
    """Separate true active underrun from benign post-EOS tail accounting."""
    active = sum(int(entry["delta"]) for entry in parsed.get("underrun_bins", []))
    partial = int(parsed.get("partial_bin", {}).get("delta", 0))
    final_delta = int(
        parsed.get("final_delta", {}).get(
            "underrun_delta", parsed.get("timing_final_delta", active + partial)
        )
    )
    return {
        "active_underrun_delta": active,
        "partial_bin_delta": partial,
        "final_underrun_delta": final_delta,
        "eos_tail_excluded_delta": max(0, final_delta - active),
        "output_stall_delta": int(
            parsed.get("final_delta", {}).get("output_stall_delta", 0)
        ),
        "nonzero_active_bins": [
            entry for entry in parsed.get("underrun_bins", [])
            if int(entry["delta"]) != 0
        ],
    }


def evaluate_strict_uart(parsed: dict[str, Any],
                         max_active_underrun: int = 0,
                         max_output_stall: int = 0,
                         require_final_clean: bool = True
                         ) -> tuple[bool, list[str], dict[str, Any]]:
    """Evaluate final UART counters for a strict active-soak gate."""
    summary = summarize_strict_uart(parsed)
    errors: list[str] = []
    if "timing_final_delta" not in parsed:
        errors.append("missing UNDERRUN_TIMING line")
    if not parsed.get("underrun_bins"):
        errors.append("missing UNDERRUN_BIN lines")
    if "final_delta" not in parsed:
        errors.append("missing UART_FINAL_DELTA line")
    if summary["active_underrun_delta"] > max_active_underrun:
        errors.append(
            f"active underrun {summary['active_underrun_delta']} > "
            f"{max_active_underrun}"
        )
    if summary["output_stall_delta"] > max_output_stall:
        errors.append(
            f"output stall {summary['output_stall_delta']} > {max_output_stall}"
        )
    if require_final_clean:
        final = parsed.get("final") or {}
        if not final:
            errors.append("missing UART_FINAL crc/drop/hdr line")
        if int(final.get("drop_count", 0)) != 0:
            errors.append(f"drop_count {final.get('drop_count')} != 0")
        if int(final.get("hdr_fail_count", 0)) != 0:
            errors.append(f"hdr_fail_count {final.get('hdr_fail_count')} != 0")
        backlog = parsed.get("final_backlog") or {}
        if int(backlog.get("submit_fail", 0)) != 0:
            errors.append(f"submit_fail {backlog.get('submit_fail')} != 0")
    return not errors, errors, summary


def recommend_tcp_rcv_scale(requested_window_bytes: int,
                            base_window_limit: int = 65535) -> int:
    """Return the minimum TCP_RCV_SCALE for advertising a large receive window."""
    if requested_window_bytes <= 0:
        raise ValueError("requested_window_bytes must be positive")
    scale = 0
    while (base_window_limit << scale) < requested_window_bytes:
        scale += 1
    return scale


def analyze_streaming_lwip_sizing(
    *,
    pbuf_pool_bufsize: int,
    max_frame_size_jumbo: int = 10368,
    tcp_snd_buf: int = 65535,
    requested_rcv_window_bytes: Optional[int] = None,
    tcp_rcv_scale: Optional[int] = None,
    tcp_window_scaling: bool = False,
    tcp_write_flag_more_on_iq_header: bool = False,
    reservoir_low_bytes: int = 0,
    reservoir_high_bytes: int = 0,
) -> dict[str, Any]:
    """Check generic lwIP sizing hazards for high-throughput streaming tests."""
    issues: list[str] = []
    recommendations: list[str] = []
    if tcp_write_flag_more_on_iq_header:
        issues.append(
            "clear TCP_WRITE_FLAG_MORE on the small IQ header; with TCP_OVERSIZE "
            "it can force payload copy into the header pbuf"
        )
    if pbuf_pool_bufsize < max_frame_size_jumbo:
        issues.append(
            f"PBUF_POOL_BUFSIZE {pbuf_pool_bufsize} < jumbo frame request "
            f"{max_frame_size_jumbo}"
        )
    if tcp_snd_buf > 0xFFFF and not tcp_window_scaling:
        issues.append(
            f"TCP_SND_BUF {tcp_snd_buf} exceeds unscaled u16_t tcpwnd_size_t; "
            f"effective wrap would be {tcp_snd_buf & 0xFFFF}"
        )

    recommended_scale = None
    if requested_rcv_window_bytes is not None:
        recommended_scale = recommend_tcp_rcv_scale(requested_rcv_window_bytes)
        if tcp_rcv_scale is None:
            recommendations.append(
                f"set TCP_RCV_SCALE >= {recommended_scale} for receive window "
                f"{requested_rcv_window_bytes} bytes"
            )
        elif tcp_rcv_scale < recommended_scale:
            issues.append(
                f"TCP_RCV_SCALE {tcp_rcv_scale} is too small for "
                f"{requested_rcv_window_bytes} bytes; use >= {recommended_scale}"
            )

    if reservoir_low_bytes or reservoir_high_bytes:
        if reservoir_low_bytes <= 0:
            issues.append("reservoir_low_bytes must be > 0 when reservoir is enabled")
        if reservoir_high_bytes <= reservoir_low_bytes:
            issues.append(
                "reservoir_high_bytes must be greater than reservoir_low_bytes"
            )
        if (requested_rcv_window_bytes is not None and
                reservoir_high_bytes > requested_rcv_window_bytes):
            issues.append(
                "reservoir_high_bytes exceeds requested receive window; host "
                "cannot fill the intended high watermark"
            )
        recommendations.append(
            "treat the PS-side reservoir as backlog margin; still gate stream "
            "enable on stable ingress and egress connections"
        )

    return {
        "ok": not issues,
        "issues": issues,
        "recommendations": recommendations,
        "effective_tcp_snd_buf_u16": tcp_snd_buf & 0xFFFF,
        "recommended_tcp_rcv_scale": recommended_scale,
    }


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
