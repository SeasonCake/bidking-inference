from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

from bidking_lab.live.tcp_reassembly import SEQ_MOD, TcpSeqReassembler


ROOT = Path(__file__).resolve().parents[1]
REAL_SETTLEMENT_FIXTURE = (
    ROOT / "tests" / "fixtures" / "live" / "tcp_reassembly_2546_complete_settlement.json"
)
FLOW = ("203.0.113.10", 10000, "192.0.2.20", 54321)
OTHER_FLOW = ("192.0.2.20", 54321, "203.0.113.10", 10000)


def test_in_order_stream_is_byte_for_byte_unchanged() -> None:
    gate = TcpSeqReassembler()

    assert gate.feed(FLOW, 1000, b"abc", now=1) == b"abc"
    assert gate.feed(FLOW, 1003, b"def", now=2) == b"def"
    assert gate.as_dict()["buffered_bytes"] == 0


def test_out_of_order_segment_waits_for_gap_then_flushes_once() -> None:
    gate = TcpSeqReassembler()

    first = gate.feed(FLOW, 1000, b"1111", now=1)
    third = gate.feed(FLOW, 1008, b"3333", now=2)
    second = gate.feed(FLOW, 1004, b"2222", now=3)

    assert first == b"1111"
    assert third == b""
    assert second == b"22223333"
    assert first + third + second == b"111122223333"
    assert gate.as_dict()["unresolved_gap_bytes"] == 0


def test_exact_retransmit_and_pending_duplicate_emit_nothing_twice() -> None:
    gate = TcpSeqReassembler()

    assert gate.feed(FLOW, 500, b"aaaa", now=1) == b"aaaa"
    assert gate.feed(FLOW, 500, b"aaaa", now=2) == b""
    assert gate.feed(FLOW, 508, b"cccc", now=3) == b""
    assert gate.feed(FLOW, 508, b"cccc", now=4) == b""
    assert gate.feed(FLOW, 504, b"bbbb", now=5) == b"bbbbcccc"
    assert gate.as_dict()["duplicate_segments"] == 2


def test_partial_overlap_only_emits_new_tail_and_advances_high_water() -> None:
    gate = TcpSeqReassembler()

    assert gate.feed(FLOW, 10_000, b"abcdefgh", now=1) == b"abcdefgh"
    assert gate.feed(FLOW, 10_004, b"efghIJKL", now=2) == b"IJKL"
    assert gate.feed(FLOW, 10_012, b"MN", now=3) == b"MN"
    status = gate.as_dict()
    assert status["overlap_segments"] == 1
    assert status["overlap_bytes"] == 4


def test_pending_partial_overlap_preserves_first_copy_and_new_tail() -> None:
    gate = TcpSeqReassembler()

    assert gate.feed(FLOW, 100, b"AAAA", now=1) == b"AAAA"
    assert gate.feed(FLOW, 108, b"CCCC", now=2) == b""
    assert gate.feed(FLOW, 106, b"BBccccDD", now=3) == b""
    assert gate.feed(FLOW, 104, b"bb", now=4) == b"bbBBCCCCDD"


def test_sequence_wrap_is_contiguous() -> None:
    gate = TcpSeqReassembler()
    start = SEQ_MOD - 4

    assert gate.feed(FLOW, start, b"abcd", now=1) == b"abcd"
    assert gate.feed(FLOW, 4, b"ijkl", now=2) == b""
    assert gate.feed(FLOW, 0, b"efgh", now=3) == b"efghijkl"


def test_half_streams_are_isolated_by_directional_four_tuple() -> None:
    gate = TcpSeqReassembler()

    assert gate.feed(FLOW, 1, b"send", now=1) == b"send"
    assert gate.feed(OTHER_FLOW, 1, b"recv", now=1) == b"recv"
    assert gate.as_dict()["active_streams"] == 2


def test_stale_unresolved_gap_is_discarded_without_pseudo_output() -> None:
    gate = TcpSeqReassembler(stale_seconds=5)

    assert gate.feed(FLOW, 100, b"head", now=1) == b"head"
    assert gate.feed(FLOW, 108, b"tail", now=2) == b""
    assert gate.as_dict()["unresolved_gap_bytes"] == 4
    assert gate.sweep(now=8) == 1
    assert gate.as_dict()["buffered_bytes"] == 0
    assert gate.as_dict()["stale_flows"] == 1


def test_first_segment_after_stale_timeout_starts_a_fresh_stream() -> None:
    gate = TcpSeqReassembler(stale_seconds=5)
    assert gate.feed(FLOW, 100, b"old", now=1) == b"old"

    assert gate.feed(FLOW, 50_000, b"fresh", now=7) == b"fresh"
    assert gate.as_dict()["stale_flows"] == 1


def test_stream_and_capture_reset_reclaim_buffered_state() -> None:
    gate = TcpSeqReassembler()
    gate.feed(FLOW, 10, b"a", now=1)
    gate.feed(FLOW, 20, b"held", now=2)
    gate.feed(OTHER_FLOW, 10, b"b", now=2)

    assert gate.reset(FLOW) == 1
    assert gate.as_dict()["active_streams"] == 1
    assert gate.reset() == 1
    assert gate.as_dict()["active_streams"] == 0
    assert gate.as_dict()["buffered_bytes"] == 0


def test_per_stream_and_global_caps_drop_future_segment_without_emitting() -> None:
    gate = TcpSeqReassembler(
        per_stream_buffer_bytes=4,
        global_buffer_bytes=4,
    )

    assert gate.feed(FLOW, 1, b"a", now=1) == b"a"
    assert gate.feed(FLOW, 10, b"12345", now=2) == b""
    status = gate.as_dict()
    assert status["cap_drops"] == 1
    assert status["cap_drop_bytes"] == 5
    assert status["buffered_bytes"] == 0


def test_global_cap_is_shared_across_half_streams() -> None:
    gate = TcpSeqReassembler(
        per_stream_buffer_bytes=8,
        global_buffer_bytes=4,
    )
    assert gate.feed(FLOW, 1, b"a", now=1) == b"a"
    assert gate.feed(FLOW, 10, b"123", now=2) == b""
    assert gate.feed(OTHER_FLOW, 1, b"b", now=1) == b"b"
    assert gate.feed(OTHER_FLOW, 10, b"45", now=2) == b""

    status = gate.as_dict()
    assert status["buffered_bytes"] == 3
    assert status["cap_drops"] == 1
    assert status["cap_drop_bytes"] == 2


def test_stream_cap_evicts_oldest_flow_with_bounded_state() -> None:
    gate = TcpSeqReassembler(max_streams=1)

    assert gate.feed(FLOW, 1, b"a", now=1) == b"a"
    assert gate.feed(OTHER_FLOW, 1, b"b", now=2) == b"b"
    status = gate.as_dict()
    assert status["active_streams"] == 1
    assert status["evicted_flows"] == 1


def test_recent_segment_evidence_is_bounded_and_contains_no_ip_or_payload() -> None:
    gate = TcpSeqReassembler(event_limit=2)
    for offset, payload in ((1, b"secret-a"), (9, b"secret-b"), (17, b"secret-c")):
        gate.feed(FLOW, offset, payload, now=offset)

    serialized = json.dumps(gate.as_dict(), sort_keys=True)
    events = gate.as_dict()["recent_segments"]
    assert len(events) == 2
    assert FLOW[0] not in serialized and FLOW[2] not in serialized
    assert "secret" not in serialized
    assert all(len(event["flow"]) == 12 for event in events)


def _run_ab_process(*, disabled: bool) -> bytes:
    code = r'''
import base64, os
from bidking_lab.live.tcp_reassembly import TcpSeqReassembler, reassembly_enabled_from_env
flow = ("203.0.113.10", 10000, "192.0.2.20", 54321)
parts = [(100, b"1111"), (108, b"3333"), (104, b"2222")]
gate = TcpSeqReassembler()
out = []
for seq, payload in parts:
    out.append(gate.feed(flow, seq, payload) if reassembly_enabled_from_env() else payload)
print(base64.b64encode(b"".join(out)).decode("ascii"))
'''
    env = os.environ.copy()
    if disabled:
        env["BIDKING_DISABLE_TCP_SEQ_REASSEMBLY"] = "1"
    else:
        env.pop("BIDKING_DISABLE_TCP_SEQ_REASSEMBLY", None)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return base64.b64decode(result.stdout.strip())


def test_default_and_rollback_arms_are_independent_processes() -> None:
    assert _run_ab_process(disabled=False) == b"111122223333"
    assert _run_ab_process(disabled=True) == b"111133332222"


def _run_real_settlement_process(*, disabled: bool) -> dict:
    code = r'''
import base64, json, os, sys
from pathlib import Path
root = Path.cwd()
for path in (root / "scripts", root / "src", root / "hero_ref" / "src"):
    sys.path.insert(0, str(path))
from bidking_lab.live.monitor import build_monitor_artifact_from_payload, load_monitor_tables
from bidking_lab.live.tcp_reassembly import TcpSeqReassembler, reassembly_enabled_from_env
from run_windivert_live_monitor import GameFrameGate

fixture = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
frame = base64.b64decode(fixture["frame_base64"])
future = bytearray(48)
future[:4] = (48).to_bytes(4, "big")
future[12:16] = (0x019D).to_bytes(4, "big")
stream = frame + bytes(future)
chunks = [(offset, stream[offset:offset + 1460]) for offset in range(0, len(stream), 1460)]
# Six in-order 1460-byte chunks, then future 0x019d tail before the missing
# settlement tail, i.e. the stable 1,3,2 pollution signature.
arrival = chunks[:6] + [chunks[7], chunks[6]]
flow = ("203.0.113.10", 10000, "192.0.2.20", 54321)
seq_base = 0xFFF00000
reassembler = TcpSeqReassembler()
frame_gate = GameFrameGate()
rows = []
sort_id = 1
arrival_bytes = bytearray()
for offset, payload in arrival:
    arrival_bytes.extend(payload)
    ordered = (
        reassembler.feed(flow, (seq_base + offset) % (1 << 32), payload)
        if reassembly_enabled_from_env()
        else payload
    )
    if not ordered:
        continue
    row = {
        "SortID": sort_id,
        "Direct": "REV",
        "Protocol": "Tcp",
        "SrcIP": flow[0], "SrcPort": flow[1],
        "DstIP": flow[2], "DstPort": flow[3],
        "CaptureTime": "",
        "Data": base64.b64encode(ordered).decode("ascii"),
        "DataLength": len(ordered),
        "PID": 1, "ProcessName": "BidKing.exe", "Source": "Npcap-test",
    }
    sort_id += 1
    rows.extend(frame_gate.feed_row(row).rows)
exact = any(base64.b64decode(row["Data"]) == frame for row in rows)
truth = {}
if rows:
    try:
        artifact = build_monitor_artifact_from_payload(
            json.dumps(rows), file="tcp-reassembly", tables=load_monitor_tables(),
            n_trials=20, roi_trials=10,
        )
        truth = (artifact.get("ui_contract") or {}).get("truth") or {}
    except Exception as exc:
        truth = {"error": type(exc).__name__}
result = {
    "enabled": reassembly_enabled_from_env(),
    "exact_frame": exact,
    "arrival_polluted": bytes(arrival_bytes) != stream,
    "total_items": truth.get("total_items"),
    "total_cells": truth.get("total_cells"),
    "total_value": truth.get("total_value"),
}
print("RESULT_JSON=" + json.dumps(result, sort_keys=True))
'''
    env = os.environ.copy()
    if disabled:
        env["BIDKING_DISABLE_TCP_SEQ_REASSEMBLY"] = "1"
    else:
        env.pop("BIDKING_DISABLE_TCP_SEQ_REASSEMBLY", None)
    result = subprocess.run(
        [sys.executable, "-c", code, str(REAL_SETTLEMENT_FIXTURE)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    line = next(
        line for line in reversed(result.stdout.splitlines())
        if line.startswith("RESULT_JSON=")
    )
    return json.loads(line.split("=", 1)[1])


def test_real_44_item_settlement_default_and_rollback_process_ab() -> None:
    expected = json.loads(REAL_SETTLEMENT_FIXTURE.read_text(encoding="utf-8"))[
        "expected"
    ]

    default = _run_real_settlement_process(disabled=False)
    rollback = _run_real_settlement_process(disabled=True)

    assert default == {
        "enabled": True,
        "exact_frame": True,
        "arrival_polluted": True,
        **expected,
    }
    assert rollback["enabled"] is False
    assert rollback["arrival_polluted"] is True
    assert rollback["exact_frame"] is False
    # This particular fixture keeps the inventory before the corrupted tail, so
    # the parser may still recover the same truth. The rollback verdict is the
    # byte-level arrival-order pollution, not a forced semantic failure.


def test_windivert_without_real_seq_does_not_import_reassembler() -> None:
    source = (ROOT / "scripts" / "run_windivert_live_monitor.py").read_text(
        encoding="utf-8-sig"
    )
    assert "TcpSeqReassembler" not in source
    assert "BIDKING_DISABLE_TCP_SEQ_REASSEMBLY" not in source


def test_npcap_orders_dedup_then_reassembly_then_frame_gate() -> None:
    source = (ROOT / "scripts" / "run_npcap_live_monitor.py").read_text(
        encoding="utf-8-sig"
    )

    dedup = source.index("dedup_key = _packet_dedup_key(pkt)")
    reassemble = source.index("tcp_reassembler.feed(key, pkt.seq, pkt.payload, now=now)")
    frame_gate = source.index("emit = frame_gate.feed_row(row)", reassemble)
    assert dedup < reassemble < frame_gate
    assert "BIDKING_DISABLE_TCP_SEQ_REASSEMBLY" in (
        ROOT / "src" / "bidking_lab" / "live" / "tcp_reassembly.py"
    ).read_text(encoding="utf-8")
