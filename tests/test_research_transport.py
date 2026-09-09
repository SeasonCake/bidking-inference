"""Synthetic v0.3.0 reassembly cases adapted to unittest."""
import json
import unittest
from research.transport.tcp_reassembly import SEQ_MOD, TcpSeqReassembler

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



def test_capacity_is_checked_even_before_contiguous_drain():
    gate = TcpSeqReassembler(per_stream_buffer_bytes=2)
    assert gate.feed(FLOW, 1, b"abc", now=1) == b""
    assert gate.as_dict()["cap_drops"] == 1
    assert gate.feed(FLOW, 1, b"ab", now=2) == b"ab"


def test_first_observed_segment_is_anchor_not_earliest_possible_sequence():
    gate = TcpSeqReassembler()
    assert gate.feed(FLOW, 104, b"BBBB", now=1) == b"BBBB"
    assert gate.feed(FLOW, 100, b"AAAA", now=2) == b""


def test_empty_payload_does_not_refresh_stale_state():
    gate = TcpSeqReassembler(stale_seconds=5)
    gate.feed(FLOW, 1, b"a", now=0)
    assert gate.feed(FLOW, 2, b"", now=4) == b""
    assert gate.sweep(now=5) == 1
    assert gate.as_dict()["packets"] == 1


def test_invalid_positive_bounds_and_event_opt_out():
    for kwargs in ({"stale_seconds": 0}, {"max_streams": 0},
                   {"per_stream_buffer_bytes": 0}, {"global_buffer_bytes": -1}):
        try:
            TcpSeqReassembler(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid bounds were accepted")
    gate = TcpSeqReassembler(event_limit=0)
    gate.feed(FLOW, 1, b"a", now=1)
    assert gate.as_dict()["recent_segments"] == []


def test_wraparound_retransmit_is_discarded():
    gate = TcpSeqReassembler()
    assert gate.feed(FLOW, SEQ_MOD - 2, b"abcd", now=1) == b"abcd"
    assert gate.feed(FLOW, SEQ_MOD - 1, b"bcd", now=2) == b""
    assert gate.feed(FLOW, 2, b"ef", now=3) == b"ef"


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(value) for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value))
