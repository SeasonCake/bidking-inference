from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import sys
import time
import types

from bidking_lab.live.fatbeans import (
    load_fatbeans_packets_from_rows,
    parse_fatbeans_packets,
)
from bidking_lab.live.monitor import _action_result_rows


ROOT = Path(__file__).resolve().parents[1]


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field_varint(field_no: int, value: int) -> bytes:
    return _varint((field_no << 3) | 0) + _varint(value)


def _field_bytes(field_no: int, value: bytes) -> bytes:
    return _varint((field_no << 3) | 2) + _varint(len(value)) + value


def _send_frame(
    message_id: int,
    *,
    session: str = "2401:9000000000000002",
    value: int = 123456,
) -> bytes:
    body = _field_varint(1, 1285603971496202)
    body += _field_bytes(2, session.encode("utf-8"))
    body += _field_varint(3, value)
    frame_len = 12 + len(body)
    return (
        frame_len.to_bytes(4, "big")
        + b"\x32\xd2\x55\x5f"
        + message_id.to_bytes(4, "big")
        + body
    )


def _rev_state_frame(
    message_id: int = 0x0025,
    *,
    session: str = "2401:9000000000000002",
    map_id: int = 2401,
    round_no: int | None = 1,
) -> bytes:
    payload = _field_bytes(1, session.encode("utf-8"))
    payload += _field_varint(2, map_id)
    if round_no is not None:
        payload += _field_varint(3, round_no)
    if message_id in (0x0021, 0x0025):
        body = _field_bytes(1, payload)
    elif message_id == 0x002D:
        body = _field_varint(1, 393075406931726)
        body += _field_bytes(2, payload)
    else:
        raise ValueError(f"unsupported state message id: {message_id}")
    frame_len = 16 + len(body)
    return (
        frame_len.to_bytes(4, "big")
        + b"\x32\xd2\x55\x60"
        + b"\x00\x00\x00\x00"
        + message_id.to_bytes(4, "big")
        + body
    )


def _rev_action_response_frame(
    *,
    action_id: int = 100105,
    result: int | None = 48,
    result_field: int | None = 14,
    packet_tag: int = 0,
) -> bytes:
    payload = _field_varint(4, action_id)
    if result is not None and result_field is not None:
        payload += _field_varint(result_field, result)
    body = _field_bytes(2, payload)
    frame_len = 16 + len(body)
    return (
        frame_len.to_bytes(4, "big")
        + b"\x32\xd2\x55\x60"
        + packet_tag.to_bytes(4, "big")
        + (0x0027).to_bytes(4, "big")
        + body
    )


def _rev_status_frame(
    *,
    session: str = "2401:9000000000000002",
) -> bytes:
    body = _field_varint(1, 436506118628741)
    body += _field_bytes(2, session.encode("utf-8"))
    frame_len = 16 + len(body)
    return (
        frame_len.to_bytes(4, "big")
        + b"\x32\xd2\x55\x61"
        + b"\x00\x00\x00\x00"
        + (0x0077).to_bytes(4, "big")
        + body
    )


def _row(direction: str, payload: bytes, *, sort_id: int = 1) -> dict[str, object]:
    return {
        "SortID": sort_id,
        "Direct": direction,
        "Protocol": "Tcp",
        "SrcIP": "8.133.195.27" if direction == "REV" else "198.18.0.1",
        "SrcPort": 10000 if direction == "REV" else 60213,
        "DstIP": "198.18.0.1" if direction == "REV" else "8.133.195.27",
        "DstPort": 60213 if direction == "REV" else 10000,
        "CaptureTime": "2026-06-02 12:00:00.000",
        "Data": base64.b64encode(payload).decode("ascii"),
        "DataLength": len(payload),
        "PID": 1234,
        "ProcessName": "BidKing.exe",
        "Source": "WinDivert",
    }


def _module():
    path = ROOT / "scripts" / "run_windivert_live_monitor.py"
    spec = importlib.util.spec_from_file_location("run_windivert_live_monitor", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_native_session_generation_matches_monitor_ascii_wire_grammar() -> None:
    module = _module()

    class SessionText(str):
        pass

    assert module._native_session_generation("2405:9000000000000056") == 9000000000000056
    assert module._native_session_generation("2405:18446744073709551615") == (
        (1 << 64) - 1
    )
    assert module._native_session_generation("2405:18446744073709551616") is None
    assert module._native_session_generation("2405:123456789012345678901") is None
    assert module._native_session_generation("٢٤٠٥:١٢٣") is None
    assert module._native_session_generation(SessionText("2405:123")) is None


class FakePacket:
    src_addr = "127.0.0.1"
    src_port = 50123
    dst_addr = "8.133.195.27"
    dst_port = 10000
    payload = b"\x00\x00\x00\x04"


class FakeFlowIndex:
    def match(self, key):
        return types.SimpleNamespace(
            direction="SEND",
            pid=1234,
            process_name="BidKing.exe",
            local=("127.0.0.1", 50123),
            remote=("8.133.195.27", 10000),
        )


def test_default_filter_can_be_port_limited_or_broad() -> None:
    module = _module()

    assert module._default_filter([10000], broad=False) == (
        "tcp and tcp.PayloadLength > 0 and "
        "(tcp.SrcPort == 10000 or tcp.DstPort == 10000)"
    )
    assert module._default_filter([10000], broad=True) == (
        "tcp and tcp.PayloadLength > 0"
    )


def test_packet_to_fatbeans_row_uses_process_flow_direction() -> None:
    module = _module()

    row = module._packet_to_fatbeans_row(
        FakePacket(),
        sort_id=9,
        flow_index=FakeFlowIndex(),
    )

    assert row["SortID"] == 9
    assert row["Direct"] == "SEND"
    assert row["Protocol"] == "Tcp"
    assert row["SrcIP"] == "127.0.0.1"
    assert row["SrcPort"] == 50123
    assert row["DstPort"] == 10000
    assert row["DataLength"] == 4
    assert base64.b64decode(row["Data"]) == b"\x00\x00\x00\x04"
    assert row["ProcessName"] == "BidKing.exe"
    assert row["Source"] == "WinDivert"


def test_write_source_status_records_windivert_open_error(tmp_path: Path) -> None:
    module = _module()

    module._write_source_status(
        tmp_path,
        process_name="BidKing.exe",
        filter_text="tcp",
        active_flows=2,
        accepted_packets=0,
        sniffed_packets=0,
        raw_packets=0,
        ignored_frames=0,
        dropped_bytes=0,
        active_session_id=None,
        error_code="windivert_dependency_missing",
        error_message="[WinError 2]",
        error_hint="重新解压 full 包",
        device_statuses=[{
            "description": "Realtek Gaming 2.5GbE Family Controller",
            "state": "error",
            "capture_mode": "open_failed",
        }],
    )

    payload = json.loads(
        (tmp_path / "capture_source_status.json").read_text(encoding="utf-8")
    )
    assert payload["error_code"] == "windivert_dependency_missing"
    assert payload["error_message"] == "[WinError 2]"
    assert payload["error_hint"] == "重新解压 full 包"
    assert payload["active_flows"] == 2
    assert payload["capture_devices"][0]["capture_mode"] == "open_failed"
    # 楔死取证字段是【可选】的: windivert 路径不提供 TCP seq, 不能凭空写 0 —— 那会让
    # "没有空洞" 和 "没测过空洞" 长得一模一样, 把下一个读诊断包的人骗过去。
    assert "seq_gaps_gate" not in payload
    assert "kernel_ifdrop" not in payload


def test_write_source_status_carries_wedge_forensics(tmp_path: Path) -> None:
    """⭐楔死取证必须【落进诊断包】, 否则群友导出的包里还是看不到根因证据。

    诊断包是唯一的取证信道(0711 那 13 个包连原始 TCP 包都没存, 只存了 gate 成功提取的帧,
    所以事后无法离线复盘)。这些计数不写进 json = 下一次还是查不出来。
    """
    module = _module()
    module._write_source_status(
        tmp_path,
        source="npcap",
        process_name="BidKing.exe",
        filter_text="tcp and (port 10000)",
        active_flows=1,
        accepted_packets=285,
        sniffed_packets=6365,
        raw_packets=6274,
        ignored_frames=28495,
        dropped_bytes=354163,
        kernel_drop=0,
        kernel_recv=10296,
        queue_full_drops=0,
        kernel_ifdrop=4,
        snap_truncated=0,
        ip_truncated=7,
        deduped_packets=88,
        seq_gaps_wire={"packets": 6365, "gaps": 2, "gap_bytes": 2920,
                       "max_gap_bytes": 1460, "overlaps": 11},
        seq_gaps_gate={"packets": 6274, "gaps": 5, "gap_bytes": 9990,
                       "max_gap_bytes": 4380, "overlaps": 9},
        tcp_reassembly={
            "enabled": True,
            "buffered_bytes": 1460,
            "unresolved_gap_bytes": 1460,
            "recent_segments": [{
                "flow": "a1b2c3d4e5f6",
                "seq": 1234,
                "length": 1460,
                "verdict": "gap",
                "emitted": 0,
                "buffered": 1460,
            }],
        },
        buffer_resyncs=2,
        boundary_resyncs=1,
        active_session_id="5606:9000000000000019",
    )
    payload = json.loads(
        (tmp_path / "capture_source_status.json").read_text(encoding="utf-8")
    )
    assert payload["kernel_ifdrop"] == 4
    assert payload["ip_truncated"] == 7
    assert payload["deduped_packets"] == 88
    assert payload["seq_gaps_wire"]["gaps"] == 2
    assert payload["seq_gaps_gate"]["gaps"] == 5
    assert payload["seq_gaps_gate"]["gap_bytes"] == 9990
    assert payload["tcp_reassembly"]["enabled"] is True
    assert payload["tcp_reassembly"]["unresolved_gap_bytes"] == 1460
    assert payload["tcp_reassembly"]["recent_segments"][0]["flow"] == "a1b2c3d4e5f6"
    # ⭐这份样本数据本身就是判据的演示: gate(5) > wire(2) 就意味着 dedup 自己吃掉了 3 个空洞。
    assert payload["seq_gaps_gate"]["gaps"] > payload["seq_gaps_wire"]["gaps"]


def test_flow_direction_map_indexes_bidking_send_and_receive(
    monkeypatch,
) -> None:
    module = _module()

    fake_conn = types.SimpleNamespace(
        pid=1234,
        laddr=types.SimpleNamespace(ip="127.0.0.1", port=50123),
        raddr=types.SimpleNamespace(ip="8.133.195.27", port=10000),
    )
    fake_psutil = types.SimpleNamespace(
        net_connections=lambda kind: [fake_conn],
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(
        module,
        "_process_name_for_pid",
        lambda pid: "BidKing.exe" if pid == 1234 else None,
    )

    flows = module._flow_direction_map("BidKing.exe")

    send = flows[("127.0.0.1", 50123, "8.133.195.27", 10000)]
    recv = flows[("8.133.195.27", 10000, "127.0.0.1", 50123)]
    assert send.direction == "SEND"
    assert recv.direction == "REV"
    assert send.pid == 1234


def test_flow_index_filters_loopback_but_keeps_server_port(
    monkeypatch,
) -> None:
    module = _module()
    flows = {
        ("127.0.0.1", 60215, "127.0.0.1", 60214): module.FlowMatch(
            direction="SEND",
            pid=1,
            process_name="BidKing.exe",
            local=("127.0.0.1", 60215),
            remote=("127.0.0.1", 60214),
        ),
        ("198.18.0.1", 60213, "8.133.195.27", 10000): module.FlowMatch(
            direction="SEND",
            pid=1,
            process_name="BidKing.exe",
            local=("198.18.0.1", 60213),
            remote=("8.133.195.27", 10000),
        ),
    }
    monkeypatch.setattr(module, "_flow_direction_map", lambda _name: flows)

    index = module.FlowIndex(
        process_name="BidKing.exe",
        refresh_seconds=60.0,
        server_ports={10000},
        include_loopback=False,
    )
    index.refresh_if_due(force=True)

    assert index.match(("127.0.0.1", 60215, "127.0.0.1", 60214)) is None
    assert (
        index.match(("198.18.0.1", 60213, "8.133.195.27", 10000)).direction
        == "SEND"
    )


def test_flow_index_can_include_loopback_flows(monkeypatch) -> None:
    module = _module()
    flows = {
        ("127.0.0.1", 15940, "127.0.0.1", 7897): module.FlowMatch(
            direction="SEND",
            pid=1,
            process_name="BidKing.exe",
            local=("127.0.0.1", 15940),
            remote=("127.0.0.1", 7897),
        ),
    }
    monkeypatch.setattr(module, "_flow_direction_map", lambda _name: flows)

    index = module.FlowIndex(
        process_name="BidKing.exe",
        refresh_seconds=60.0,
        server_ports={10000},
        include_loopback=True,
    )
    index.refresh_if_due(force=True)

    assert (
        index.match(("127.0.0.1", 15940, "127.0.0.1", 7897)).direction
        == "SEND"
    )


def test_game_frame_gate_accepts_status_frame_for_session_prewarm() -> None:
    module = _module()
    gate = module.GameFrameGate()

    emitted = gate.feed_row(_row("REV", _rev_status_frame()))

    assert len(emitted.rows) == 1
    assert emitted.reset_session is False
    assert emitted.rows[0]["MessageID"] == "0x0077"
    assert emitted.rows[0]["SessionID"] == "2401:9000000000000002"
    assert gate.active_session_id == "2401:9000000000000002"
    assert gate.ignored_frames == 0
    assert not module._should_schedule_monitor_process(emitted.rows[0])


def test_game_frame_gate_accepts_state_and_matching_session_sends() -> None:
    module = _module()
    gate = module.GameFrameGate()

    early_send = gate.feed_row(_row("SEND", _send_frame(0x0022), sort_id=1))
    state = gate.feed_row(_row("REV", _rev_state_frame(), sort_id=2))
    matching_send = gate.feed_row(_row("SEND", _send_frame(0x0026), sort_id=3))
    other_session_send = gate.feed_row(
        _row(
            "SEND",
            _send_frame(0x0026, session="2401:9999999999999999"),
            sort_id=4,
        )
    )

    assert len(early_send.rows) == 1
    assert early_send.rows[0]["MessageID"] == "0x0022"
    assert early_send.rows[0]["SessionID"] == "2401:9000000000000002"
    assert len(state.rows) == 1
    assert state.rows[0]["MessageID"] == "0x0025"
    assert state.rows[0]["SessionID"] == "2401:9000000000000002"
    assert state.rows[0]["Source"] == "WinDivertFrameGate"
    assert state.rows[0]["SortID"] == 2
    assert gate.active_session_id == "2401:9000000000000002"
    assert len(matching_send.rows) == 1
    assert matching_send.rows[0]["MessageID"] == "0x0026"
    assert matching_send.rows[0]["SortID"] == 3
    assert other_session_send.rows == ()
    assert gate.ignored_frames == 1


def test_game_frame_gate_uses_first_send_to_prewarm_direct_action_session() -> None:
    module = _module()
    gate = module.GameFrameGate()

    first_send = gate.feed_row(_row("SEND", _send_frame(0x0026), sort_id=1))
    action_response = gate.feed_row(
        _row("REV", _rev_action_response_frame(), sort_id=2)
    )

    assert len(first_send.rows) == 1
    assert first_send.rows[0]["MessageID"] == "0x0026"
    assert gate.active_session_id == "2401:9000000000000002"
    assert len(action_response.rows) == 1
    assert action_response.rows[0]["MessageID"] == "0x0027"
    assert action_response.rows[0]["SessionID"] == "2401:9000000000000002"


def test_game_frame_gate_accepts_session_started_state() -> None:
    module = _module()
    gate = module.GameFrameGate()

    started = gate.feed_row(
        _row("REV", _rev_state_frame(0x0021, round_no=None), sort_id=1)
    )
    first_send = gate.feed_row(_row("SEND", _send_frame(0x0022), sort_id=2))

    assert len(started.rows) == 1
    assert started.rows[0]["MessageID"] == "0x0021"
    assert started.rows[0]["SessionID"] == "2401:9000000000000002"
    assert gate.active_session_id == "2401:9000000000000002"
    assert len(first_send.rows) == 1
    assert first_send.rows[0]["MessageID"] == "0x0022"


def test_game_frame_gate_resets_on_new_bid_send_session() -> None:
    module = _module()
    gate = module.GameFrameGate()

    first_state = gate.feed_row(
        _row(
            "REV",
            _rev_state_frame(session="2401:1111111111111111"),
            sort_id=1,
        )
    )
    first_send = gate.feed_row(
        _row(
            "SEND",
            _send_frame(0x0022, session="2401:1111111111111111"),
            sort_id=2,
        )
    )
    new_bid_send = gate.feed_row(
        _row(
            "SEND",
            _send_frame(0x0022, session="2410:2222222222222222"),
            sort_id=3,
        )
    )
    new_action_send = gate.feed_row(
        _row(
            "SEND",
            _send_frame(0x0026, session="2410:2222222222222222"),
            sort_id=4,
        )
    )

    assert len(first_state.rows) == 1
    assert len(first_send.rows) == 1
    assert new_bid_send.reset_session is True
    assert len(new_bid_send.rows) == 1
    assert new_bid_send.rows[0]["SortID"] == 1
    assert new_bid_send.rows[0]["MessageID"] == "0x0022"
    assert new_bid_send.rows[0]["SessionID"] == "2410:2222222222222222"
    assert gate.active_session_id == "2410:2222222222222222"
    assert len(new_action_send.rows) == 1
    assert new_action_send.rows[0]["SortID"] == 2
    assert new_action_send.rows[0]["SessionID"] == "2410:2222222222222222"


def test_game_frame_gate_keeps_session_when_only_stream_buffers_reset() -> None:
    module = _module()
    gate = module.GameFrameGate()

    started = gate.feed_row(
        _row("REV", _rev_state_frame(0x0021, round_no=None), sort_id=1)
    )
    gate.reset_stream_buffers(clear_session=False)
    send_after_flow_change = gate.feed_row(
        _row("SEND", _send_frame(0x0022), sort_id=2)
    )

    assert len(started.rows) == 1
    assert gate.active_session_id == "2401:9000000000000002"
    assert len(send_after_flow_change.rows) == 1
    assert send_after_flow_change.rows[0]["MessageID"] == "0x0022"


def test_game_frame_gate_accepts_current_session_direct_action_response() -> None:
    module = _module()
    gate = module.GameFrameGate()

    early_response = gate.feed_row(
        _row("REV", _rev_action_response_frame(), sort_id=1)
    )
    started = gate.feed_row(
        _row("REV", _rev_state_frame(0x0021, round_no=None), sort_id=2)
    )
    direct_response = gate.feed_row(
        _row("REV", _rev_action_response_frame(), sort_id=3)
    )
    empty_ack = gate.feed_row(
        _row("REV", _rev_action_response_frame(packet_tag=1234), sort_id=4)
    )

    assert early_response.rows == ()
    assert len(started.rows) == 1
    assert len(direct_response.rows) == 1
    assert direct_response.rows[0]["MessageID"] == "0x0027"
    assert direct_response.rows[0]["SessionID"] == "2401:9000000000000002"
    assert direct_response.rows[0]["SortID"] == 2
    assert empty_ack.rows == ()
    assert gate.ignored_frames == 2


def test_game_frame_gate_accepts_live_nonzero_tag_after_matching_action_send() -> None:
    module = _module()
    gate = module.GameFrameGate()

    started = gate.feed_row(
        _row("REV", _rev_state_frame(0x0021, round_no=3), sort_id=1)
    )
    action_send = gate.feed_row(
        _row("SEND", _send_frame(0x0026, value=100105), sort_id=2)
    )
    direct_response = gate.feed_row(
        _row(
            "REV",
            _rev_action_response_frame(
                action_id=100105,
                result=48,
                result_field=14,
                packet_tag=104_365_359,
            ),
            sort_id=3,
        )
    )

    rows = [*started.rows, *action_send.rows, *direct_response.rows]
    events = parse_fatbeans_packets(load_fatbeans_packets_from_rows(rows))
    result_rows = _action_result_rows(
        events,
        {100105: types.SimpleNamespace(name="普品格数")},
        session_id="2401:9000000000000002",
    )

    assert len(direct_response.rows) == 1
    assert direct_response.rows[0]["MessageID"] == "0x0027"
    assert result_rows[0]["source_kind"] == "direct_action_response"
    assert result_rows[0]["round"] == 3
    assert result_rows[0]["result"] == 48


def test_correlated_reply_keeps_send_round_after_next_round_state() -> None:
    module = _module()
    gate = module.GameFrameGate()

    first = gate.feed_row(
        _row("REV", _rev_state_frame(0x0021, round_no=3), sort_id=1)
    )
    action_send = gate.feed_row(
        _row("SEND", _send_frame(0x0026, value=100105), sort_id=2)
    )
    next_round = gate.feed_row(
        _row("REV", _rev_state_frame(0x0025, round_no=4), sort_id=3)
    )
    late_response = gate.feed_row(
        _row(
            "REV",
            _rev_action_response_frame(
                action_id=100105,
                result=48,
                result_field=14,
                packet_tag=104_365_364,
            ),
            sort_id=4,
        )
    )

    rows = [*first.rows, *action_send.rows, *next_round.rows, *late_response.rows]
    events = parse_fatbeans_packets(load_fatbeans_packets_from_rows(rows))
    result_rows = _action_result_rows(
        events,
        {100105: types.SimpleNamespace(name="普品格数")},
        session_id="2401:9000000000000002",
    )

    assert len(late_response.rows) == 1
    assert result_rows[0]["round"] == 3
    assert result_rows[0]["result"] == 48


def test_game_frame_gate_accepts_empty_action_ack_for_same_round_zero_recovery() -> None:
    module = _module()
    gate = module.GameFrameGate()

    status = gate.feed_row(_row("REV", _rev_status_frame(), sort_id=1))
    action_send = gate.feed_row(
        _row("SEND", _send_frame(0x0026, value=100110), sort_id=2)
    )
    empty_ack = gate.feed_row(
        _row(
            "REV",
            _rev_action_response_frame(
                action_id=100110,
                result=None,
                result_field=None,
            ),
            sort_id=3,
        )
    )

    assert len(status.rows) == 1
    assert len(action_send.rows) == 1
    assert len(empty_ack.rows) == 1
    assert empty_ack.rows[0]["MessageID"] == "0x0027"
    assert empty_ack.rows[0]["SessionID"] == "2401:9000000000000002"
    assert "rev_tool_parse_failed" not in gate.ignored_reasons_dict()
    events = parse_fatbeans_packets(
        load_fatbeans_packets_from_rows(
            [*status.rows, *action_send.rows, *empty_ack.rows]
        )
    )
    result_rows = _action_result_rows(
        events,
        {100110: types.SimpleNamespace(name="普品均格")},
        session_id="2401:9000000000000002",
    )
    assert result_rows[0]["action_id"] == 100110
    assert result_rows[0]["result"] == 0
    assert result_rows[0]["inferred_zero"] is True


def test_game_frame_gate_carries_direct_action_after_settlement_to_next_session() -> None:
    module = _module()
    gate = module.GameFrameGate()

    started = gate.feed_row(
        _row("REV", _rev_state_frame(0x0021, round_no=None), sort_id=1)
    )
    settled = gate.feed_row(
        _row("REV", _rev_state_frame(0x002D, round_no=3), sort_id=2)
    )
    late_direct = gate.feed_row(
        _row("REV", _rev_action_response_frame(), sort_id=3)
    )
    next_bid = gate.feed_row(
        _row(
            "SEND",
            _send_frame(0x0022, session="2501:2222222222222222"),
            sort_id=4,
        )
    )

    assert len(started.rows) == 1
    assert len(settled.rows) == 1
    assert settled.rows[0]["MessageID"] == "0x002d"
    assert late_direct.rows == ()
    assert "rev_tool_after_settlement" not in gate.ignored_reasons_dict()
    assert next_bid.reset_session is True
    assert len(next_bid.rows) == 2
    assert next_bid.rows[0]["SortID"] == 1
    assert next_bid.rows[0]["MessageID"] == "0x0027"
    assert next_bid.rows[0]["SessionID"] == "2501:2222222222222222"
    assert next_bid.rows[1]["SortID"] == 2
    assert next_bid.rows[1]["MessageID"] == "0x0022"
    assert next_bid.rows[1]["SessionID"] == "2501:2222222222222222"


def test_game_frame_gate_accepts_next_session_action_send_after_settlement() -> None:
    module = _module()
    gate = module.GameFrameGate()

    gate.feed_row(_row("REV", _rev_state_frame(0x0021, round_no=None), sort_id=1))
    gate.feed_row(_row("REV", _rev_state_frame(0x002D, round_no=3), sort_id=2))
    next_action = gate.feed_row(
        _row(
            "SEND",
            _send_frame(0x0026, session="2501:2222222222222222"),
            sort_id=3,
        )
    )

    assert next_action.reset_session is True
    assert len(next_action.rows) == 1
    assert next_action.rows[0]["SortID"] == 1
    assert next_action.rows[0]["MessageID"] == "0x0026"
    assert next_action.rows[0]["SessionID"] == "2501:2222222222222222"
    assert gate.active_session_id == "2501:2222222222222222"


def test_game_frame_gate_reconstructs_split_frames() -> None:
    module = _module()
    gate = module.GameFrameGate()
    frame = _rev_state_frame()

    first = gate.feed_row(_row("REV", frame[:7], sort_id=1))
    second = gate.feed_row(_row("REV", frame[7:], sort_id=2))

    assert first.rows == ()
    assert len(second.rows) == 1
    assert second.rows[0]["DataLength"] == len(frame)


def test_game_frame_gate_keeps_split_buffers_per_tcp_flow() -> None:
    module = _module()
    gate = module.GameFrameGate()
    # The split flow completes after the other flow, so give it the newer native
    # generation.  This keeps the test about per-flow reassembly rather than asking
    # the session gate to revive an older auction.
    first_frame = _rev_state_frame(session="2401:2222222222222222")
    second_frame = _rev_state_frame(session="2401:1111111111111111")
    first_head = _row("REV", first_frame[:7], sort_id=1)
    first_tail = _row("REV", first_frame[7:], sort_id=3)
    second = _row("REV", second_frame, sort_id=2)
    second["DstPort"] = 60214

    assert gate.feed_row(first_head).rows == ()
    emitted_second = gate.feed_row(second)
    emitted_first = gate.feed_row(first_tail)

    assert len(emitted_second.rows) == 1
    assert emitted_second.rows[0]["SessionID"] == "2401:1111111111111111"
    assert len(emitted_first.rows) == 1
    assert emitted_first.rows[0]["SessionID"] == "2401:2222222222222222"
    assert emitted_first.reset_session is True


def test_game_frame_gate_prunes_stale_flow_buffers_but_keeps_active_flow() -> None:
    module = _module()
    gate = module.GameFrameGate()
    first_frame = _rev_state_frame(session="2401:1111111111111111")
    second_frame = _rev_state_frame(session="2401:2222222222222222")
    first_head = _row("REV", first_frame[:7], sort_id=1)
    first_tail = _row("REV", first_frame[7:], sort_id=3)
    second_head = _row("REV", second_frame[:7], sort_id=2)
    second_head["DstPort"] = 60214
    second_tail = _row("REV", second_frame[7:], sort_id=4)
    second_tail["DstPort"] = 60214

    assert gate.feed_row(first_head).rows == ()
    assert gate.feed_row(second_head).rows == ()
    gate.reset_stream_buffers(
        keep_flow_keys={
            ("8.133.195.27", 10000, "198.18.0.1", 60214),
        },
        clear_session=False,
    )

    emitted_first = gate.feed_row(first_tail)
    emitted_second = gate.feed_row(second_tail)

    assert emitted_first.rows == ()
    assert len(emitted_second.rows) == 1
    assert emitted_second.rows[0]["SessionID"] == "2401:2222222222222222"


def test_game_frame_gate_selfheal_clears_wedged_buffer_and_relocks() -> None:
    """0710 "中途不计算/卡上一局" root-cause regression: a capture hole leaves bytes that
    decode as a false-but-plausible length prefix; the gate then waits for up to
    MAX_FRAME_BYTES of fill that a quiet stream never delivers, staying wedged for the
    rest of the match. The stale-buffer sweep must clear the wedge so the next clean
    segment re-locks the stream."""
    module = _module()
    gate = module.GameFrameGate()
    good = _rev_state_frame(session="2401:1111111111111111")
    assert len(gate.feed_row(_row("REV", good, sort_id=1)).rows) == 1

    # False prefix demands 16384 bytes -> gate waits forever on a quiet stream.
    wedge = (16384).to_bytes(4, "big") + b"\x99" * 40
    assert gate.feed_row(_row("REV", wedge, sort_id=2)).rows == ()
    # The failure mode being fixed: valid frames get swallowed into the wedge.
    assert gate.feed_row(_row("REV", good, sort_id=3)).rows == ()
    assert gate.buffer_resyncs == 0

    # Fast-forward the wedge past STALE_BUFFER_SECONDS and reopen the sweep window.
    gate._last_stale_sweep = -1e9
    for key in list(gate._buffer_progress):
        gate._buffer_progress[key] = -1e9

    healed = gate.feed_row(_row("REV", good, sort_id=4))
    assert gate.buffer_resyncs == 1
    assert len(healed.rows) == 1
    assert healed.rows[0]["SessionID"] == "2401:1111111111111111"


def test_game_frame_gate_selfheal_disabled_by_env(monkeypatch) -> None:
    monkeypatch.setenv("BIDKING_DISABLE_GATE_SELFHEAL", "1")
    module = _module()
    gate = module.GameFrameGate()
    wedge = (16384).to_bytes(4, "big") + b"\x99" * 40
    assert gate.feed_row(_row("REV", wedge, sort_id=1)).rows == ()
    gate._last_stale_sweep = -1e9
    for key in list(gate._buffer_progress):
        gate._buffer_progress[key] = -1e9
    good = _rev_state_frame()
    assert gate.feed_row(_row("REV", good, sort_id=2)).rows == ()
    assert gate.buffer_resyncs == 0


def _backdate_buffer_progress(gate, seconds: float) -> None:
    stale = time.monotonic() - seconds
    for key in list(gate._buffer_progress):
        gate._buffer_progress[key] = stale


def test_game_frame_gate_boundary_resync_relocks_on_new_session_frame() -> None:
    """BUG-A (0710 opening blind window): a wedge formed on the previous match's tail
    swallows the NEXT match's opening frames until the 20s sweep discards them — 20-25s
    with no r01 snapshot. The boundary relock must find the intact new-session frames
    inside the wedged buffer, drop only the garbage prefix, and emit them immediately."""
    module = _module()
    gate = module.GameFrameGate()
    prev = _rev_state_frame(session="2401:1111111111111111")
    assert len(gate.feed_row(_row("REV", prev, sort_id=1)).rows) == 1

    wedge = (16384).to_bytes(4, "big") + b"\x99" * 40
    assert gate.feed_row(_row("REV", wedge, sort_id=2)).rows == ()
    opening_status = _rev_status_frame(session="2402:2222222222222222")
    opening_state = _rev_state_frame(0x0021, session="2402:2222222222222222", map_id=2402)
    # Within the 3s grace the opening frames are still swallowed into the wedge.
    assert gate.feed_row(_row("REV", opening_status, sort_id=3)).rows == ()
    assert gate.feed_row(_row("REV", opening_state, sort_id=4)).rows == ()
    assert gate.boundary_resyncs == 0

    # Past the grace (but far under the 20s sweep) the next chunk must relock at the
    # first opening frame and release everything buffered behind the garbage prefix.
    _backdate_buffer_progress(gate, 4.0)
    follow_up = _rev_state_frame(session="2402:2222222222222222", map_id=2402, round_no=1)
    healed = gate.feed_row(_row("REV", follow_up, sort_id=5))

    assert gate.boundary_resyncs == 1
    assert gate.buffer_resyncs == 1
    assert [row["MessageID"] for row in healed.rows] == ["0x0077", "0x0021", "0x0025"]
    assert healed.reset_session is True
    assert gate.active_session_id == "2402:2222222222222222"
    assert gate.dropped_bytes == len(wedge)


def test_game_frame_gate_boundary_resync_ignores_pattern_inside_filling_frame() -> None:
    """Zero-impact guard: a healthy large frame whose PAYLOAD happens to contain the
    boundary byte pattern must not be corrupted by a relock — not even while it is still
    filling and the grace window has elapsed. A candidate only wins if the pattern-derived
    boundary yields a COMPLETE frame that parses with a valid session id."""
    module = _module()
    gate = module.GameFrameGate()
    session = "2401:9000000000000002"
    embedded = (
        b"\x00\x00\x00\x00" + (0x0021).to_bytes(4, "big")
        + b"\x00\x00\x00\x00" + (0x0077).to_bytes(4, "big")
    )
    # Field 15 (both REV boundary patterns) placed FIRST so the scan sees them while the
    # frame is still incomplete; session/map/round follow.
    payload = _field_bytes(15, embedded)
    payload += _field_bytes(1, session.encode("utf-8"))
    payload += _field_varint(2, 2401)
    payload += _field_varint(3, 1)
    body = _field_bytes(1, payload)
    frame = (
        (16 + len(body)).to_bytes(4, "big")
        + b"\x32\xd2\x55\x60"
        + b"\x00\x00\x00\x00"
        + (0x0025).to_bytes(4, "big")
        + body
    )
    # cut1 keeps both embedded patterns in-buffer but the frame incomplete.
    cut1 = frame.rfind(embedded) + len(embedded)
    assert gate.feed_row(_row("REV", frame[:cut1], sort_id=1)).rows == ()

    # Grace elapses while the frame is STILL filling: the scan runs and must reject every
    # embedded-pattern candidate (invalid length or not-yet-complete), never relocking.
    _backdate_buffer_progress(gate, 4.0)
    assert gate.feed_row(_row("REV", frame[cut1 : cut1 + 1], sort_id=2)).rows == ()
    assert gate.boundary_resyncs == 0

    _backdate_buffer_progress(gate, 4.0)
    done = gate.feed_row(_row("REV", frame[cut1 + 1 :], sort_id=3))

    assert gate.boundary_resyncs == 0
    assert len(done.rows) == 1
    assert done.rows[0]["SessionID"] == session
    assert done.rows[0]["DataLength"] == len(frame)


def test_game_frame_gate_boundary_resync_leaves_mid_match_state_frames_wedged() -> None:
    """Scan set stays conservative: ordinary mid-match 0x0025 state frames are not
    session-opening msgids, so a mid-match wedge keeps the 20s sweep semantics (a 6s-old
    wedge is neither boundary-relocked nor swept early while a session is live)."""
    module = _module()
    gate = module.GameFrameGate()
    live = _rev_state_frame(session="2401:1111111111111111")
    assert len(gate.feed_row(_row("REV", live, sort_id=1)).rows) == 1
    wedge = (16384).to_bytes(4, "big") + b"\x99" * 40
    assert gate.feed_row(_row("REV", wedge, sort_id=2)).rows == ()

    _backdate_buffer_progress(gate, 6.0)
    gate._last_stale_sweep = -1e9
    swallowed = gate.feed_row(_row("REV", live, sort_id=3))

    assert swallowed.rows == ()
    assert gate.buffer_resyncs == 0
    assert gate.boundary_resyncs == 0


def test_game_frame_gate_boundary_resync_preserves_settlement_frame() -> None:
    """BUG-K(0712 作者报·原名 BUG-F, 因与调参窗口 TRIAGE 的 BUG-F 撞车而改名):
    结算冻在上一轮、等 20 秒多才跳结算,而且【结算异常/藏品缺失】。

    根因: 结算【前】REV 流楔死 → 结算帧(0x002D)被吞进楔死缓冲。它此前不在边界扫描列表里 →
    relock 不触发; 而 settled-5s 快扫要 _current_session_settled=True,结算前它还是 False →
    只能等 20s 自愈,而自愈是【清流丢弃缓冲】的 → 结算帧连同缓冲一起丢掉。
    卡 20 秒 + 藏品缺失,同一个原因。

    修复 = 把 0x002D 加进扫描。relock 与自愈的关键区别: relock【保留】缓冲里的完整帧,
    只丢前面的垃圾前缀 → 结算帧保得住。本例断言结算帧【被保留并 emit】,而不是被丢弃。"""
    module = _module()
    gate = module.GameFrameGate()
    session = "5611:9000000000000020"

    # 竞价中(注意: 尚未 settled → settled-5s 快扫不生效 → 只有 relock 能救)
    live = _rev_state_frame(0x0025, session=session)
    assert len(gate.feed_row(_row("REV", live, sort_id=1)).rows) == 1
    assert gate._current_session_settled is False

    # 结算前楔死: 假长度前缀
    wedge = (16384).to_bytes(4, "big") + b"\x99" * 40
    assert gate.feed_row(_row("REV", wedge, sort_id=2)).rows == ()

    # 结算帧到达 → 被吞进楔死缓冲(这一刻用户看到的还是上一轮的数据)
    settle = _rev_state_frame(0x002D, session=session)
    assert gate.feed_row(_row("REV", settle, sort_id=3)).rows == ()

    # 过了 3s 宽限(远早于 20s 自愈)→ 边界 relock 必须扫到结算帧并【保留】它
    _backdate_buffer_progress(gate, 4.0)
    healed = gate.feed_row(_row("REV", _rev_state_frame(0x0025, session=session), sort_id=4))

    assert gate.boundary_resyncs == 1, "结算帧没触发边界 relock → 会等到 20s 自愈把它丢掉"
    msgids = [row["MessageID"] for row in healed.rows]
    assert "0x002d" in msgids, f"结算帧被丢了(藏品缺失的根因)! 实际 emit: {msgids}"
    assert gate._current_session_settled is True, "结算帧到达后应标记 settled"


def test_game_frame_gate_settlement_frame_parses_for_relock() -> None:
    """0x002D 必须能被 _boundary_frame_session_id 解析出合法 session —— 否则 pattern 命中了
    也会被 relock 的校验拒掉(等于没加)。"""
    module = _module()
    gate = module.GameFrameGate()
    session = "2401:9000000000000002"
    raw = _rev_state_frame(0x002D, session=session)
    got = gate._boundary_frame_session_id("REV", 0x002D, raw)
    assert got == session


def test_game_frame_gate_boundary_resync_disabled_by_env(monkeypatch) -> None:
    monkeypatch.setenv("BIDKING_DISABLE_GATE_BOUNDARY_RESYNC", "1")
    module = _module()
    gate = module.GameFrameGate()
    prev = _rev_state_frame(session="2401:1111111111111111")
    assert len(gate.feed_row(_row("REV", prev, sort_id=1)).rows) == 1
    wedge = (16384).to_bytes(4, "big") + b"\x99" * 40
    assert gate.feed_row(_row("REV", wedge, sort_id=2)).rows == ()

    _backdate_buffer_progress(gate, 4.0)
    opening = _rev_status_frame(session="2402:2222222222222222")
    assert gate.feed_row(_row("REV", opening, sort_id=3)).rows == ()
    assert gate.boundary_resyncs == 0
    assert gate.buffer_resyncs == 0


def test_game_frame_gate_sweep_uses_boundary_threshold_between_matches() -> None:
    """Option-B safety net: once the session settled (between matches) no legitimate
    frame can be mid-fill for long, so a wedge is swept at 5s instead of 20s even when
    the boundary scan never matches (e.g. garbage holds no opening-frame image)."""
    module = _module()
    gate = module.GameFrameGate()
    settled = _rev_state_frame(0x002D, session="2401:1111111111111111")
    assert len(gate.feed_row(_row("REV", settled, sort_id=1)).rows) == 1
    wedge = (16384).to_bytes(4, "big") + b"\x99" * 40
    assert gate.feed_row(_row("REV", wedge, sort_id=2)).rows == ()

    _backdate_buffer_progress(gate, 6.0)
    gate._last_stale_sweep = -1e9
    healed = gate.feed_row(_row("REV", _rev_state_frame(0x0025), sort_id=3))

    assert gate.buffer_resyncs == 1
    assert gate.boundary_resyncs == 0
    assert len(healed.rows) == 1


def test_game_frame_gate_boundary_resync_relocks_send_stream() -> None:
    """SEND streams wedge too (bids swallowed); the 4-byte 0x0026 pattern alone is weak,
    so the relock must be carried by the complete-frame + session-id validation."""
    module = _module()
    gate = module.GameFrameGate()
    wedge = (16384).to_bytes(4, "big") + b"\x99" * 40
    assert gate.feed_row(_row("SEND", wedge, sort_id=1)).rows == ()
    swallowed = _send_frame(0x0026, session="2402:2222222222222222")
    assert gate.feed_row(_row("SEND", swallowed, sort_id=2)).rows == ()

    _backdate_buffer_progress(gate, 4.0)
    follow_up = _send_frame(0x0026, session="2402:2222222222222222")
    healed = gate.feed_row(_row("SEND", follow_up, sort_id=3))

    assert gate.boundary_resyncs == 1
    assert [row["MessageID"] for row in healed.rows] == ["0x0026", "0x0026"]
    assert gate.active_session_id == "2402:2222222222222222"


def test_game_frame_gate_ignored_sample_carries_capture_device() -> None:
    """npcap rows carry CaptureDevice (adapter attribution for multi-adapter desync
    diagnosis); windivert rows don't — the sample must include it only when present."""
    module = _module()
    gate = module.GameFrameGate()
    # REV frame with non-zero packet tag + non-game message id -> rev_not_game_frame.
    junk = (
        (16).to_bytes(4, "big")
        + b"\x32\xd2\x55\x60"
        + (5).to_bytes(4, "big")
        + (0x019B).to_bytes(4, "big")
    )
    tagged = _row("REV", junk, sort_id=1)
    tagged["CaptureDevice"] = "Intel Wi-Fi 6E AX211"
    assert gate.feed_row(tagged).rows == ()
    assert gate.ignored_samples[-1]["device"] == "Intel Wi-Fi 6E AX211"

    plain = _row("REV", junk, sort_id=2)
    assert gate.feed_row(plain).rows == ()
    assert "device" not in gate.ignored_samples[-1]


def test_game_frame_gate_resets_on_new_state_session() -> None:
    module = _module()
    gate = module.GameFrameGate()

    first_state = gate.feed_row(
        _row(
            "REV",
            _rev_state_frame(session="2401:1111111111111111"),
            sort_id=1,
        )
    )
    first_send = gate.feed_row(
        _row(
            "SEND",
            _send_frame(0x0022, session="2401:1111111111111111"),
            sort_id=2,
        )
    )
    second_state = gate.feed_row(
        _row(
            "REV",
            _rev_state_frame(session="2405:2222222222222222", map_id=2405),
            sort_id=3,
        )
    )

    assert len(first_state.rows) == 1
    assert first_state.rows[0]["SortID"] == 1
    assert len(first_send.rows) == 1
    assert first_send.rows[0]["SortID"] == 2
    assert second_state.reset_session is True
    assert len(second_state.rows) == 1
    assert second_state.rows[0]["SortID"] == 1
    assert second_state.rows[0]["SessionID"] == "2405:2222222222222222"


def test_game_frame_gate_resets_on_new_status_session() -> None:
    module = _module()
    gate = module.GameFrameGate()

    first_state = gate.feed_row(
        _row(
            "REV",
            _rev_state_frame(session="2401:1111111111111111"),
            sort_id=1,
        )
    )
    next_status = gate.feed_row(
        _row(
            "REV",
            _rev_status_frame(session="2407:2222222222222222"),
            sort_id=2,
        )
    )
    next_state = gate.feed_row(
        _row(
            "REV",
            _rev_state_frame(session="2407:2222222222222222", map_id=2407),
            sort_id=3,
        )
    )

    assert len(first_state.rows) == 1
    assert next_status.reset_session is True
    assert len(next_status.rows) == 1
    assert next_status.rows[0]["SortID"] == 1
    assert next_status.rows[0]["MessageID"] == "0x0077"
    assert gate.active_session_id == "2407:2222222222222222"
    assert len(next_state.rows) == 1
    assert next_state.rows[0]["SortID"] == 2
    assert next_state.rows[0]["SessionID"] == "2407:2222222222222222"


def test_game_frame_gate_ignores_late_retired_session_without_reset() -> None:
    module = _module()
    gate = module.GameFrameGate()
    old_session = "2401:1111111111111111"
    current_session = "2405:2222222222222222"

    gate.feed_row(
        _row(
            "REV",
            _rev_state_frame(session=old_session, map_id=2401),
            sort_id=1,
        )
    )
    opened = gate.feed_row(
        _row(
            "REV",
            _rev_status_frame(session=current_session),
            sort_id=2,
        )
    )
    current = gate.feed_row(
        _row(
            "REV",
            _rev_state_frame(session=current_session, map_id=2405, round_no=2),
            sort_id=3,
        )
    )
    late_status = gate.feed_row(
        _row("REV", _rev_status_frame(session=old_session), sort_id=4)
    )
    late_state = gate.feed_row(
        _row(
            "REV",
            _rev_state_frame(session=old_session, map_id=2401, round_no=2),
            sort_id=5,
        )
    )
    current_action = gate.feed_row(
        _row(
            "SEND",
            _send_frame(0x0026, session=current_session, value=100100),
            sort_id=6,
        )
    )

    assert opened.reset_session is True
    assert len(current.rows) == 1
    assert late_status == module.FrameGateEmit(())
    assert late_state == module.FrameGateEmit(())
    assert gate.active_session_id == current_session
    assert len(current_action.rows) == 1
    assert current_action.reset_session is False
    assert gate.ignored_reasons_dict()["retired_session"] == 2


def test_game_frame_gate_rejects_unseen_older_native_session() -> None:
    module = _module()
    gate = module.GameFrameGate()
    old_session = "2401:9000000000000055"
    current_session = "2405:9000000000000056"

    current = gate.feed_row(
        _row(
            "REV",
            _rev_state_frame(session=current_session, map_id=2405, round_no=2),
            sort_id=1,
        )
    )
    late_status = gate.feed_row(
        _row("REV", _rev_status_frame(session=old_session), sort_id=2)
    )
    late_state = gate.feed_row(
        _row(
            "REV",
            _rev_state_frame(session=old_session, map_id=2401, round_no=2),
            sort_id=3,
        )
    )
    current_action = gate.feed_row(
        _row(
            "SEND",
            _send_frame(0x0026, session=current_session, value=100100),
            sort_id=4,
        )
    )

    assert len(current.rows) == 1
    assert late_status == module.FrameGateEmit(())
    assert late_state == module.FrameGateEmit(())
    assert gate.active_session_id == current_session
    assert len(current_action.rows) == 1
    assert current_action.reset_session is False


def test_game_frame_gate_generation_guard_survives_retired_history_eviction() -> None:
    module = _module()
    gate = module.GameFrameGate()
    first_session = "2401:1000"

    gate.feed_row(_row("REV", _rev_status_frame(session=first_session), sort_id=1))
    for offset in range(1, 19):
        next_session = f"2401:{1000 + offset}"
        emitted = gate.feed_row(
            _row("REV", _rev_status_frame(session=next_session), sort_id=offset + 1)
        )
        assert emitted.reset_session is True
        assert len(emitted.rows) == 1

    # first_session has fallen out of the bounded deque, but its native generation
    # still proves that it is stale relative to the active session.
    late_evicted = gate.feed_row(
        _row("REV", _rev_status_frame(session=first_session), sort_id=30)
    )
    assert late_evicted == module.FrameGateEmit(())
    assert gate.active_session_id == "2401:1018"


def test_game_frame_gate_emits_unseen_lower_generation_settlement_without_reset() -> None:
    module = _module()
    gate = module.GameFrameGate()
    old_session = "2401:9000000000000055"
    current_session = "2405:9000000000000056"

    gate.feed_row(
        _row(
            "REV",
            _rev_state_frame(session=current_session, map_id=2405, round_no=2),
            sort_id=1,
        )
    )
    settle_frame = _rev_state_frame(
        0x002D,
        session=old_session,
        map_id=2401,
        round_no=3,
    )
    head = gate.feed_row(_row("REV", settle_frame[:7], sort_id=2))
    tail = gate.feed_row(_row("REV", settle_frame[7:], sort_id=3))

    assert head == module.FrameGateEmit(())
    assert tail.reset_session is False
    assert len(tail.rows) == 1
    assert tail.rows[0]["SessionID"] == old_session
    assert tail.rows[0]["MessageID"] == "0x002d"
    assert gate.active_session_id == current_session


def test_game_frame_gate_process_epoch_can_reuse_retired_session_id() -> None:
    module = _module()
    gate = module.GameFrameGate()
    first_session = "2401:1111111111111111"
    second_session = "2405:2222222222222222"

    gate.feed_row(
        _row("REV", _rev_status_frame(session=first_session), sort_id=1)
    )
    gate.feed_row(
        _row("REV", _rev_status_frame(session=second_session), sort_id=2)
    )
    assert gate.feed_row(
        _row("REV", _rev_status_frame(session=first_session), sort_id=3)
    ).rows == ()

    gate.reset_stream_buffers(clear_session=True)
    reopened = gate.feed_row(
        _row("REV", _rev_status_frame(session=first_session), sort_id=4)
    )
    assert len(reopened.rows) == 1
    assert reopened.reset_session is False
    assert gate.active_session_id == first_session


def test_game_frame_gate_emits_late_settlement_without_resetting_session() -> None:
    module = _module()
    gate = module.GameFrameGate()
    session_a = "2401:1111111111111111"
    session_b = "2501:2222222222222222"

    gate.feed_row(_row("REV", _rev_state_frame(0x0021, session=session_a), sort_id=1))
    gate.feed_row(_row("REV", _rev_state_frame(0x002D, session=session_a), sort_id=2))
    next_bid = gate.feed_row(
        _row("SEND", _send_frame(0x0022, session=session_b), sort_id=3)
    )
    late_settle = gate.feed_row(
        _row("REV", _rev_state_frame(0x002D, session=session_a), sort_id=4)
    )

    assert next_bid.reset_session is True
    assert late_settle.reset_session is False
    assert len(late_settle.rows) == 1
    assert late_settle.rows[0]["MessageID"] == "0x002d"
    assert late_settle.rows[0]["SessionID"] == session_a

    unrelated_action = gate.feed_row(
        _row(
            "SEND",
            _send_frame(0x0026, session="2403:3333333333333333"),
            sort_id=5,
        )
    )
    assert unrelated_action.rows == ()
    assert unrelated_action.reset_session is False
    assert gate.active_session_id == session_b


def test_game_frame_gate_keeps_split_settlement_across_session_reset() -> None:
    module = _module()
    gate = module.GameFrameGate()
    session_a = "2401:1111111111111111"
    session_b = "2501:2222222222222222"
    settle_frame = _rev_state_frame(0x002D, session=session_a)

    gate.feed_row(_row("REV", _rev_state_frame(0x0021, session=session_a), sort_id=1))
    head = gate.feed_row(_row("REV", settle_frame[:7], sort_id=2))
    reset_emit = gate.feed_row(
        _row("SEND", _send_frame(0x0022, session=session_b), sort_id=3)
    )
    tail = gate.feed_row(_row("REV", settle_frame[7:], sort_id=4))

    assert head.rows == ()
    assert reset_emit.reset_session is True
    assert len(tail.rows) == 1
    assert tail.rows[0]["MessageID"] == "0x002d"
    assert tail.rows[0]["SessionID"] == session_a
