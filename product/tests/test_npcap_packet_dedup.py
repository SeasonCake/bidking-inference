from __future__ import annotations

import importlib.util
import queue
from pathlib import Path
import threading

from bidking_lab.live.capture import CapturedPacket, NpcapDeviceOpenError
from bidking_lab.live.stream_diag import StreamSeqDiag


ROOT = Path(__file__).resolve().parents[1]
FLOW = ("8.133.195.27", 10000, "192.168.100.9", 64541)


def _module():
    path = ROOT / "scripts" / "run_npcap_live_monitor.py"
    spec = importlib.util.spec_from_file_location("run_npcap_live_monitor_dedup", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _packet(*, seq: int, payload: bytes) -> CapturedPacket:
    return CapturedPacket(
        src_ip=FLOW[0],
        src_port=FLOW[1],
        dst_ip=FLOW[2],
        dst_port=FLOW[3],
        seq=seq,
        payload=payload,
    )


def test_repeated_payload_at_different_tcp_offsets_is_not_deduped() -> None:
    """4549 known-fail: equal protocol payload at a new seq is a real stream segment."""

    module = _module()
    first = _packet(seq=1_000, payload=b"same-payload")
    second = _packet(seq=1_012, payload=b"same-payload")

    # Prove the old detector would collapse the two before trusting the new verdict.
    old_first = (*FLOW, first.payload)
    old_second = (*FLOW, second.payload)
    assert old_first == old_second
    assert module._packet_dedup_key(first) != module._packet_dedup_key(second)

    gate = StreamSeqDiag(label="gate")
    seen = set()
    for pkt in (first, second):
        key = module._packet_dedup_key(pkt)
        assert key not in seen
        seen.add(key)
        gate.observe(FLOW, pkt.seq, len(pkt.payload))

    assert gate.unresolved_gap_bytes == 0


def test_exact_cross_adapter_copy_is_deduped_without_creating_a_gap() -> None:
    """Known-good: same flow/seq/payload from two adapters is still one TCP segment."""

    module = _module()
    payload = b"x" * 100
    first = _packet(seq=5_000, payload=payload)
    duplicate = _packet(seq=5_000, payload=payload)
    next_segment = _packet(seq=5_100, payload=payload)

    wire = StreamSeqDiag(label="wire")
    gate = StreamSeqDiag(label="gate")
    seen = set()
    dropped = 0
    for pkt in (first, duplicate, next_segment):
        wire.observe(FLOW, pkt.seq, len(pkt.payload))
        key = module._packet_dedup_key(pkt)
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        gate.observe(FLOW, pkt.seq, len(pkt.payload))

    assert dropped == 1
    assert wire.unresolved_gap_bytes == 0
    assert gate.unresolved_gap_bytes == 0


def test_dedup_key_keeps_direction_and_payload_boundaries() -> None:
    module = _module()
    base = _packet(seq=10, payload=b"a")
    changed_payload = _packet(seq=10, payload=b"b")
    reversed_flow = CapturedPacket(
        src_ip=FLOW[2],
        src_port=FLOW[3],
        dst_ip=FLOW[0],
        dst_port=FLOW[1],
        seq=10,
        payload=b"a",
    )

    assert module._packet_dedup_key(base) != module._packet_dedup_key(changed_payload)
    assert module._packet_dedup_key(base) != module._packet_dedup_key(reversed_flow)


def test_seq_jump_diagnostic_hashes_flow_but_keeps_gap_statistics() -> None:
    module = _module()

    line = module._format_seq_jump(
        gap_no=3,
        direction="REV",
        key=FLOW,
        jump_bytes=128,
        seq=4000,
        wire_gaps=2,
    )

    assert "[seq-jump] #3 REV flow=" in line
    assert "jump=128B seq=4000 (wire_gaps=2)" in line
    assert FLOW[0] not in line
    assert FLOW[2] not in line
    assert module._format_seq_jump(
        gap_no=3,
        direction="REV",
        key=FLOW,
        jump_bytes=128,
        seq=4000,
        wire_gaps=2,
    ) == line


def test_device_open_failure_is_promoted_while_no_adapter_receives_packets() -> None:
    module = _module()
    devices = [{
        "description": "Realtek Gaming 2.5GbE Family Controller",
        "error_code": "npcap_device_open_failed",
        "non_promisc_fallback_attempted": True,
    }]

    payload = module._capture_open_error_payload(devices, sniffed_packets=0)

    assert payload["error_code"] == "npcap_device_open_failed"
    assert "普通抓包兼容模式" in payload["error_hint"]
    assert module._capture_open_error_payload(devices, sniffed_packets=1) == {}


def test_one_open_adapter_suppresses_top_level_failure_from_another_adapter() -> None:
    module = _module()
    devices = [
        {"description": "usable", "open_ok": True, "state": "open"},
        {
            "description": "unavailable",
            "open_ok": False,
            "error_code": "npcap_device_open_failed",
        },
    ]

    assert module._capture_open_error_payload(devices, sniffed_packets=0) == {}


def test_drain_device_records_non_promisc_fallback(monkeypatch) -> None:
    module = _module()

    class FakeBackend:
        capture_mode = "non_promisc_fallback"
        promiscuous_mode = False
        non_promisc_fallback_attempted = True

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def packets(self):
            return iter(())

        def stats(self):
            return {"recv": 3, "drop": 0, "ifdrop": 0}

    monkeypatch.setattr(module, "NpcapBackend", FakeBackend)
    statuses = {}
    module._drain_device(
        "private-device-id",
        "tcp",
        queue.Queue(),
        threading.Event(),
        statuses,
        "Realtek Gaming 2.5GbE Family Controller",
    )

    status = statuses["private-device-id"]
    assert status["state"] == "open"
    assert status["capture_mode"] == "non_promisc_fallback"
    assert status["promisc"] == 0
    assert status["recv"] == 3


def test_drain_device_records_structured_open_failure(monkeypatch) -> None:
    module = _module()

    class FailingBackend:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            raise NpcapDeviceOpenError(
                "failed to set hardware filter to promiscuous mode",
                fallback_error="ordinary capture denied",
                non_promisc_fallback_attempted=True,
            )

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(module, "NpcapBackend", FailingBackend)
    statuses = {}
    module._drain_device(
        "private-device-id",
        "tcp",
        queue.Queue(),
        threading.Event(),
        statuses,
        "Realtek Gaming 2.5GbE Family Controller",
    )

    status = module._device_statuses_snapshot(statuses)[0]
    assert status["error_code"] == "npcap_device_open_failed"
    assert status["non_promisc_fallback_attempted"] is True
    assert status["description"] == "Realtek Gaming 2.5GbE Family Controller"
    assert "private-device-id" not in str(status)
