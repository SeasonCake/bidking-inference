from __future__ import annotations

import base64
import struct
from collections import Counter
from pathlib import Path

import pytest

from bidking_lab.live.fatbeans import (
    FatbeansActionResult,
    FatbeansCaptureEvents,
    FatbeansFrame,
    FatbeansInventoryItem,
    FatbeansObservedItem,
    FatbeansPlayerBid,
    FatbeansPublicInfo,
    FatbeansSkillReveal,
    FatbeansStateEvent,
    hero_mode_from_state,
    latest_player_bids,
    live_batches_from_fatbeans_capture,
    live_batches_from_fatbeans_events,
    live_batches_from_fatbeans_capture_payload,
    load_fatbeans_packets_from_rows,
    parse_fatbeans_packets,
    parse_fatbeans_capture,
    parse_fatbeans_capture_payload,
    _parse_action_result,
    _parse_direct_action_response,
    _parse_public_info,
    _parse_skill_reveal,
    _parse_inventory_items,
)
from bidking_lab.live import (
    FieldUpdate,
    LayoutEstimatePolicy,
    estimate_warehouse_from_layout,
    grid_footprint,
    final_truth_from_events,
    layout_evidence_from_batches,
    layout_grid_view,
    layout_replay_stages,
    layout_risk_label,
    GridItemObservation,
    LiveSessionState,
    LiveObservationBatch,
    apply_observation_batch,
    live_state_to_session_obs,
)


FATBEANS_SAMPLE_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "samples" / "fatbeans"
)

PROJECT4_CAPTURE = FATBEANS_SAMPLE_DIR / "bid_king_project4.json"


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


def _field_fixed32(field_no: int, value: float) -> bytes:
    return _varint((field_no << 3) | 5) + struct.pack("<f", value)


def _rev_status_frame(*, session: str, player_id: int) -> bytes:
    body = _field_varint(1, player_id)
    body += _field_bytes(2, session.encode("utf-8"))
    frame_len = 16 + len(body)
    return (
        frame_len.to_bytes(4, "big")
        + b"\x32\xd2\x55\x61"
        + b"\x00\x00\x00\x00"
        + (0x0077).to_bytes(4, "big")
        + body
    )


def _rev_direct_action_frame(*, action_id: int, result: int | None = None) -> bytes:
    payload = _field_varint(4, action_id)
    if result is not None:
        payload += _field_varint(14, result)
    body = _field_bytes(2, payload)
    frame_len = 16 + len(body)
    return (
        frame_len.to_bytes(4, "big")
        + b"\x32\xd2\x55\x60"
        + b"\x00\x00\x00\x00"
        + (0x0027).to_bytes(4, "big")
        + body
    )


def _send_bid_frame(*, session: str, value: int) -> bytes:
    body = _field_varint(1, 1285603971496202)
    body += _field_bytes(2, session.encode("utf-8"))
    body += _field_varint(3, value)
    frame_len = 12 + len(body)
    return (
        frame_len.to_bytes(4, "big")
        + b"\x32\xd2\x55\x5f"
        + (0x0022).to_bytes(4, "big")
        + body
    )


def _player_bid_block(
    *,
    player_id: int,
    name: str,
    hero_id: int,
    value: int,
) -> bytes:
    bid_value = _field_varint(2, value)
    return (
        _field_varint(1, player_id)
        + _field_bytes(2, name.encode("utf-8"))
        + _field_varint(3, hero_id)
        + _field_bytes(5, bid_value)
    )


def _rev_state_with_bids_frame(
    *,
    session: str,
    map_id: int,
    round_no: int,
    bids: tuple[bytes, ...],
) -> bytes:
    payload = _field_bytes(1, session.encode("utf-8"))
    payload += _field_varint(2, map_id)
    payload += _field_varint(3, round_no)
    for bid in bids:
        payload += _field_bytes(5, bid)
    body = _field_bytes(1, payload)
    frame_len = 16 + len(body)
    return (
        frame_len.to_bytes(4, "big")
        + b"\x32\xd2\x55\x60"
        + b"\x00\x00\x00\x00"
        + (0x0025).to_bytes(4, "big")
        + body
    )


def _fatbeans_row(
    direction: str,
    payload: bytes,
    *,
    sort_id: int,
    local_port: int = 60213,
) -> dict[str, object]:
    return {
        "SortID": sort_id,
        "Direct": direction,
        "Protocol": "Tcp",
        "SrcIP": "8.133.195.27" if direction == "REV" else "198.18.0.1",
        "SrcPort": 10000 if direction == "REV" else local_port,
        "DstIP": "198.18.0.1" if direction == "REV" else "8.133.195.27",
        "DstPort": local_port if direction == "REV" else 10000,
        "CaptureTime": "2026-06-04 12:00:00.000",
        "Data": base64.b64encode(payload).decode("ascii"),
        "DataLength": len(payload),
    }


def test_fatbeans_parser_reconstructs_interleaved_tcp_streams() -> None:
    first = _send_bid_frame(session="2401:stream_a", value=123456)
    split = len(first) // 2
    second = _send_bid_frame(session="2402:stream_b", value=234567)
    packets = load_fatbeans_packets_from_rows(
        [
            _fatbeans_row("SEND", first[:split], sort_id=1, local_port=60213),
            _fatbeans_row("SEND", second, sort_id=2, local_port=60214),
            _fatbeans_row("SEND", first[split:], sort_id=3, local_port=60213),
        ]
    )

    events = parse_fatbeans_packets(packets)

    assert [(send.session_id, send.value) for send in events.sends] == [
        ("2401:stream_a", 123456),
        ("2402:stream_b", 234567),
    ]


def test_fatbeans_parser_resyncs_after_leading_partial_frame_bytes() -> None:
    packets = load_fatbeans_packets_from_rows(
        [
            _fatbeans_row(
                "SEND",
                b"\x99\x88" + _send_bid_frame(session="2401:resync", value=345678),
                sort_id=1,
            ),
        ]
    )

    events = parse_fatbeans_packets(packets)

    assert [(send.session_id, send.value) for send in events.sends] == [
        ("2401:resync", 345678),
    ]


def test_parse_inventory_items_keeps_parent_local_index() -> None:
    first_item = (
        _field_varint(1, 101)
        + _field_varint(2, 1001001)
        + _field_bytes(4, b"\x08\x01")
        + _field_bytes(4, b"\x08\x02")
        + _field_varint(9, 5)
    )
    second_item = (
        _field_varint(1, 102)
        + _field_varint(2, 1001002)
        + _field_bytes(4, b"\x08\x03")
        + _field_varint(9, 4)
    )
    inventory = (
        _field_bytes(3, _field_varint(1, 41) + _field_bytes(3, first_item))
        + _field_bytes(3, _field_bytes(3, second_item))
    )

    items = _parse_inventory_items(inventory)

    assert [(item.runtime_id, item.item_id, item.local_index) for item in items] == [
        (101, 1001001, 41),
        (102, 1001002, 0),
    ]
    assert [item.cells for item in items] == [2, 1]


@pytest.mark.parametrize(
    "info_id",
    [200013, 200014, 200015, 200016],
)
def test_parse_public_avg_cells_zero_without_value_field(info_id: int) -> None:
    block = (
        _field_varint(1, info_id)
        + _field_varint(3, 2101)
        + _field_varint(5, 1749735471)
        + _field_varint(6, 1)
        + _field_varint(13, 900001)
    )

    info = _parse_public_info(block)

    assert info is not None
    assert info.info_id == info_id
    assert info.map_id == 2101
    assert info.value == 0.0
    assert info.value_field == 0


@pytest.mark.parametrize(
    "info_id",
    [200035, 200036, 200037, 200038],
)
def test_parse_public_avg_value_zero_without_value_field(info_id: int) -> None:
    block = (
        _field_varint(1, info_id)
        + _field_varint(3, 2102)
        + _field_varint(5, 1781269813634)
    )

    info = _parse_public_info(block)

    assert info is not None
    assert info.info_id == info_id
    assert info.map_id == 2102
    assert info.value == 0.0
    assert info.value_field == 0


@pytest.mark.parametrize(
    "info_id",
    [200018, 200019, 200020],
)
def test_parse_public_exact_count_zero_without_value_field(info_id: int) -> None:
    block = (
        _field_varint(1, info_id)
        + _field_varint(3, 2102)
        + _field_varint(5, 1781269858721)
        + _field_varint(6, 2)
    )

    info = _parse_public_info(block)

    assert info is not None
    assert info.info_id == info_id
    assert info.map_id == 2102
    assert info.value == 0
    assert info.value_field == 7


@pytest.mark.parametrize(
    "info_id",
    [200009, 200010, 200011, 200012],
)
def test_parse_public_total_cells_zero_without_value_field(info_id: int) -> None:
    block = (
        _field_varint(1, info_id)
        + _field_varint(3, 2105)
        + _field_varint(5, 1781272984074)
        + _field_varint(6, 1)
        + _field_varint(13, 1425654278796282)
    )

    info = _parse_public_info(block)

    assert info is not None
    assert info.info_id == info_id
    assert info.map_id == 2105
    assert info.value == 0
    assert info.value_field == 14


def test_parse_implied_zero_ahmad_gold_avg_skill_without_value_field() -> None:
    block = (
        _field_varint(1, 1002041)
        + _field_varint(2, 204)
        + _field_varint(5, 1781272984074)
        + _field_varint(6, 1)
        + _field_varint(13, 1425654278796282)
    )

    reveal = _parse_skill_reveal(block)

    assert reveal is not None
    assert reveal.skill_id == 1002041
    assert reveal.hero_id == 204
    assert reveal.round_index == 1
    assert reveal.result == 0.0
    assert reveal.result_field == 0
    assert reveal.observed_items == ()


@pytest.mark.parametrize(
    ("info_id", "value_field", "value"),
    [
        (200009, 14, 98),
        (200017, 7, 38),
    ],
)
def test_parse_public_numeric_exact_value_fields(
    info_id: int,
    value_field: int,
    value: int,
) -> None:
    block = (
        _field_varint(1, info_id)
        + _field_varint(3, 2403)
        + _field_varint(value_field, value)
    )

    info = _parse_public_info(block)

    assert info is not None
    assert info.info_id == info_id
    assert info.map_id == 2403
    assert info.value == value
    assert info.value_field == value_field


def test_parse_public_auction_category_total_value_field() -> None:
    block = (
        _field_varint(1, 200044)
        + _field_varint(3, 5611)
        + _field_varint(12, 1_430_456)
    )

    info = _parse_public_info(block)

    assert info is not None
    assert info.info_id == 200044
    assert info.map_id == 5611
    assert info.value == 1_430_456
    assert info.value_field == 12


def test_parse_unknown_public_info_does_not_guess_integer_field_12() -> None:
    block = (
        _field_varint(1, 299999)
        + _field_varint(3, 5611)
        + _field_varint(12, 1_430_456)
    )

    assert _parse_public_info(block) is None


def test_parse_numeric_only_ahmad_skill_reveal() -> None:
    block = (
        _field_varint(1, 1002041)
        + _field_varint(2, 204)
        + _field_varint(6, 2)
        + _field_fixed32(11, 8.5)
    )

    reveal = _parse_skill_reveal(block)

    assert reveal is not None
    assert reveal.skill_id == 1002041
    assert reveal.hero_id == 204
    assert reveal.round_index == 2
    assert reveal.result == pytest.approx(8.5)
    assert reveal.result_field == 11
    assert reveal.observed_items == ()


def test_parse_zero_avg_action_result_from_varint_field() -> None:
    block = _field_varint(4, 100113) + _field_varint(11, 0)

    result = _parse_action_result(block)

    assert result is not None
    assert result.action_id == 100113
    assert result.result == 0.0
    assert result.result_field == 11


def test_parse_direct_empty_action_ack_preserves_action_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BIDKING_DISABLE_EMPTY_ACTION_ACK_ZERO_RECOVERY", raising=False)
    raw = _rev_direct_action_frame(action_id=100110)

    result = _parse_direct_action_response(
        FatbeansFrame(
            index=0,
            direction="REV",
            sort_id=2,
            capture_time="2026-07-27 21:40:22.379",
            raw=raw,
        )
    )

    assert result is not None
    assert result.action_id == 100110
    assert result.result is None
    assert result.result_field is None
    assert result.observed_items == ()


def test_parse_direct_empty_action_ack_rollback_restores_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BIDKING_DISABLE_EMPTY_ACTION_ACK_ZERO_RECOVERY", "1")
    raw = _rev_direct_action_frame(action_id=100110)

    result = _parse_direct_action_response(
        FatbeansFrame(
            index=0,
            direction="REV",
            sort_id=2,
            capture_time="2026-07-27 21:40:22.379",
            raw=raw,
        )
    )

    assert result is None


def test_parse_zero_avg_skill_reveal_from_varint_field() -> None:
    block = (
        _field_varint(1, 1002041)
        + _field_varint(2, 204)
        + _field_varint(6, 2)
        + _field_varint(11, 0)
    )

    reveal = _parse_skill_reveal(block)

    assert reveal is not None
    assert reveal.skill_id == 1002041
    assert reveal.hero_id == 204
    assert reveal.result == 0.0
    assert reveal.result_field == 11


def test_parse_action_count_result_from_field7() -> None:
    block = _field_varint(4, 100117) + _field_varint(7, 13)

    result = _parse_action_result(block)

    assert result is not None
    assert result.action_id == 100117
    assert result.result == 13
    assert result.result_field == 7


def test_parse_ahmad_total_count_skill_reveal_from_field7() -> None:
    block = (
        _field_varint(1, 100204)
        + _field_varint(2, 204)
        + _field_varint(7, 39)
    )

    reveal = _parse_skill_reveal(block)

    assert reveal is not None
    assert reveal.skill_id == 100204
    assert reveal.hero_id == 204
    assert reveal.result == 39
    assert reveal.result_field == 7


@pytest.mark.parametrize(
    ("info_id", "path", "value_field", "value"),
    [
        (200009, ("session", "warehouse_total_cells"), 14, 98),
        (200010, ("bucket", "4", "total_cells"), 14, 23),
        (200011, ("bucket", "5", "total_cells"), 14, 18),
        (200012, ("bucket", "6", "total_cells"), 14, 0),
        (200017, ("session", "total_item_count"), 7, 38),
        (200018, ("bucket", "4", "count"), 7, 11),
        (200019, ("bucket", "5", "count"), 7, 5),
        (200020, ("bucket", "6", "count"), 7, 0),
    ],
)
def test_public_numeric_exact_infos_update_inference_fields(
    info_id: int,
    path: tuple[str, ...],
    value_field: int,
    value: int,
) -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=7,
                capture_time="",
                message_id=0x0025,
                session_id="s1",
                map_id=2403,
                round_index=1,
                public_infos=(
                    FatbeansPublicInfo(
                        info_id=info_id,
                        map_id=2403,
                        value=value,
                        value_field=value_field,
                    ),
                ),
            ),
        ),
    )

    batch = live_batches_from_fatbeans_events(events)[0]

    assert {update.path: update.value for update in batch.field_updates}[path] == value


def test_public_q5_outline_updates_exact_bucket_without_item_quality() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=7,
                capture_time="",
                message_id=0x0025,
                session_id="s1",
                map_id=2403,
                round_index=1,
                public_infos=(
                    FatbeansPublicInfo(
                        info_id=200002,
                        map_id=2403,
                        value=2,
                        value_field=8,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=0,
                                runtime_id=101,
                                item_id=None,
                                quality=None,
                                value=None,
                                shape_code=22,
                                cells=None,
                            ),
                            FatbeansObservedItem(
                                local_index=10,
                                runtime_id=102,
                                item_id=None,
                                quality=None,
                                value=None,
                                shape_code=12,
                                cells=None,
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    batch = live_batches_from_fatbeans_events(events)[0]
    updates = {update.path: update.value for update in batch.field_updates}

    assert updates[("bucket", "5", "count")] == 2
    assert updates[("bucket", "5", "total_cells")] == 6


def test_grid_footprint_decodes_fatbeans_local_and_shape() -> None:
    footprint = grid_footprint(94, "33")

    assert footprint is not None
    assert (footprint.row, footprint.col) == (10, 5)
    assert (footprint.width, footprint.height) == (3, 3)
    assert (footprint.bottom_row, footprint.right_col) == (12, 7)


def test_hero_mode_from_state_uses_structured_skill_when_seat_heroes_ambiguous() -> None:
    state = FatbeansStateEvent(
        sort_id=1,
        capture_time="",
        message_id=0x0025,
        session_id="2502:1",
        map_id=2502,
        round_index=1,
        bids=(
            FatbeansPlayerBid(player_id=1, name="p1", hero_id=103, values=(0,)),
            FatbeansPlayerBid(player_id=2, name="p2", hero_id=204, values=(0,)),
            FatbeansPlayerBid(player_id=3, name="p3", hero_id=104, values=(0,)),
            FatbeansPlayerBid(player_id=4, name="p4", hero_id=209, values=(0,)),
        ),
        skill_reveals=(
            FatbeansSkillReveal(
                skill_id=100204,
                hero_id=204,
                round_index=1,
                result=55,
                result_field=8,
                observed_items=(),
            ),
        ),
    )

    assert hero_mode_from_state(state) == "ahmed"


def test_inventory_items_mark_batch_settled_even_without_loss_packet() -> None:
    batches = live_batches_from_fatbeans_events(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=1,
                    capture_time="",
                    message_id=0x0025,
                    session_id="s1",
                    map_id=2401,
                    round_index=5,
                    inventory_items=(
                        FatbeansInventoryItem(
                            runtime_id=101,
                            item_id=1001,
                            quality=4,
                            cells=4,
                        ),
                    ),
                ),
            ),
        )
    )

    assert len(batches) == 1
    assert batches[0].phase == "settled"
    assert batches[0].event_kind == "session_settled"


def test_grid_footprint_treats_missing_local_as_zero() -> None:
    footprint = grid_footprint(None, "23")

    assert footprint is not None
    assert (footprint.row, footprint.col) == (1, 1)
    assert (footprint.bottom_row, footprint.right_col) == (3, 2)


def test_layout_evidence_prefers_pre_settlement_grid_and_reports_risk() -> None:
    batches = (
        LiveObservationBatch(
            source="packet",
            phase="reading",
            sequence=1,
            grid_items=(
                # Top-left default local 0: rows 1-3, cols 1-2.
                GridItemObservation(
                    cells=6,
                    source="packet",
                    confidence="exact",
                    quality=5,
                    shape_key="23",
                    local_index=None,
                ),
                # Sparse deep tail: rows 15-17, cols 1-2.
                GridItemObservation(
                    cells=6,
                    source="packet",
                    confidence="exact",
                    quality=5,
                    shape_key="23",
                    local_index=140,
                ),
            ),
        ),
        LiveObservationBatch(
            source="packet",
            phase="settled",
            sequence=2,
            grid_items=(
                GridItemObservation(
                    cells=1,
                    source="packet",
                    confidence="exact",
                    quality=1,
                    shape_key="11",
                    local_index=0,
                ),
            ),
        ),
    )

    evidence = layout_evidence_from_batches(batches)

    assert evidence is not None
    assert evidence.sequence == 1
    assert evidence.max_row == 17
    assert evidence.total_cells == 12
    assert evidence.bounding_cells == 170
    assert evidence.bottom_tail_item_count == 1
    assert evidence.sparsity_ratio > 0.9
    assert layout_risk_label(evidence).startswith("低")

    estimate = estimate_warehouse_from_layout(evidence)
    assert estimate.min_reasonable_cells == 12
    assert estimate.p50_guess is None
    assert estimate.p90_guess is None
    assert estimate.confidence == "低"
    assert not estimate.locked


def test_layout_estimate_policy_can_be_swapped_for_sample_fitting() -> None:
    batch = LiveObservationBatch(
        source="packet",
        phase="reading",
        sequence=3,
        grid_items=(
            GridItemObservation(
                cells=16,
                source="packet",
                confidence="exact",
                quality=5,
                shape_key="44",
                local_index=0,
            ),
            GridItemObservation(
                cells=1,
                source="packet",
                confidence="exact",
                quality=2,
                shape_key="11",
                local_index=39,
            ),
        ),
    )
    evidence = layout_evidence_from_batches((batch,))
    assert evidence is not None

    default = estimate_warehouse_from_layout(evidence)
    fitted = estimate_warehouse_from_layout(
        evidence,
        policy=LayoutEstimatePolicy(
            name="sample-fit-test",
            medium_p50_margin=5,
        ),
    )

    assert default.policy_name == "conservative-v0"
    assert default.p50_guess == 20
    assert fitted.policy_name == "sample-fit-test"
    assert fitted.p50_guess == 35
    assert fitted.p90_guess == 40


def test_layout_grid_view_is_frontend_neutral() -> None:
    batch = LiveObservationBatch(
        source="packet",
        phase="reading",
        sequence=7,
        grid_items=(
            GridItemObservation(
                cells=4,
                source="packet",
                confidence="exact",
                item_id=12345,
                quality=4,
                shape_key="22",
                local_index=23,
            ),
        ),
    )
    evidence = layout_evidence_from_batches((batch,))

    assert evidence is not None
    view = layout_grid_view(evidence)

    assert view.sequence == 7
    assert view.rows == 4
    assert view.columns == 10
    assert "已放置 1 件 / 4 格" in view.summary
    assert len(view.items) == 1
    item = view.items[0]
    assert (item.row, item.col, item.width, item.height) == (3, 4, 2, 2)
    assert item.quality == 4
    assert item.label == "Q4\n12345"
    assert "shape=2x2" in item.tooltip
    assert item.z_index == 10


def test_known_quality_grid_items_become_bucket_lower_bounds() -> None:
    state = LiveSessionState()
    batch = LiveObservationBatch(
        source="packet",
        event_kind="tool_revealed",
        phase="reading",
        sequence=1,
        field_updates=(
            FieldUpdate(
                path=("session", "map_id"),
                value=2401,
                source="packet",
                confidence="exact",
                sequence=1,
            ),
            FieldUpdate(
                path=("session", "hero"),
                value="aisha",
                source="packet",
                confidence="exact",
                sequence=1,
            ),
        ),
        grid_items=(
            GridItemObservation(
                cells=9,
                source="packet",
                confidence="exact",
                quality=3,
                shape_key="33",
                local_index=94,
            ),
            GridItemObservation(
                cells=4,
                source="packet",
                confidence="exact",
                quality=4,
                shape_key="22",
                local_index=23,
            ),
            GridItemObservation(
                cells=6,
                source="packet",
                confidence="exact",
                quality=None,
                shape_key="23",
                local_index=40,
            ),
        ),
    )

    session = live_state_to_session_obs(apply_observation_batch(state, batch))

    assert session is not None
    assert session.buckets[3].total_cells_min == 9
    assert session.buckets[3].count_min == 1
    assert session.buckets[4].total_cells_min == 4
    assert session.buckets[4].count_min == 1
    assert session.visible_outline_item_count_min == 3
    assert session.visible_outline_total_cells_min == 19
    assert session.unknown_outline_item_count == 1
    assert session.unknown_outline_total_cells == 6


def test_incremental_tool_reveal_keeps_prior_grid_items_and_enriches_runtime() -> None:
    initial = LiveObservationBatch(
        source="packet",
        event_kind="session_started",
        phase="reading",
        sequence=1,
        grid_items=(
            GridItemObservation(
                cells=4,
                source="packet",
                confidence="exact",
                runtime_id=101,
                quality=2,
                shape_key="22",
                local_index=10,
            ),
        ),
    )
    incremental = LiveObservationBatch(
        source="packet",
        event_kind="tool_revealed",
        phase="bidding",
        sequence=2,
        grid_items=(
            GridItemObservation(
                cells=4,
                source="packet",
                confidence="exact",
                runtime_id=101,
                item_id=1103003,
                quality=3,
                local_index=99,
            ),
            GridItemObservation(
                cells=2,
                source="packet",
                confidence="exact",
                runtime_id=102,
                item_id=1103004,
                quality=3,
                shape_key="21",
                local_index=30,
            ),
        ),
    )

    state = apply_observation_batch(LiveSessionState(), initial)
    state = apply_observation_batch(state, incremental)
    latest = layout_evidence_from_batches((initial, incremental))

    assert len(state.grid_items) == 2
    enriched = next(item for item in state.grid_items if item.runtime_id == 101)
    assert enriched.item_id == 1103003
    assert enriched.shape_key == "22"
    assert enriched.local_index == 10
    assert latest is not None
    assert latest.sequence == 2
    assert latest.total_cells == 6


def _session_with_bucket_fields(*updates: FieldUpdate) -> object:
    state = LiveSessionState()
    batch = LiveObservationBatch(
        source="packet",
        event_kind="public_info",
        phase="reading",
        field_updates=(
            FieldUpdate(
                path=("session", "map_id"),
                value=2401,
                source="packet",
                confidence="exact",
            ),
            FieldUpdate(
                path=("session", "hero"),
                value="ethan",
                source="packet",
                confidence="exact",
            ),
            *updates,
        ),
    )
    session = live_state_to_session_obs(apply_observation_batch(state, batch))
    assert session is not None
    return session


def test_residual_red_bucket_requires_explicit_non_red_cells() -> None:
    session = _session_with_bucket_fields(
        FieldUpdate(
            path=("session", "warehouse_total_cells"),
            value=100,
            source="packet",
            confidence="exact",
        ),
        FieldUpdate(
            path=("bucket", "1", "total_cells"),
            value=10,
            source="packet",
            confidence="exact",
        ),
        FieldUpdate(
            path=("bucket", "3", "total_cells"),
            value=20,
            source="packet",
            confidence="exact",
        ),
        FieldUpdate(
            path=("bucket", "4", "avg_cells"),
            value="2.307692",
            source="packet",
            confidence="exact",
        ),
        FieldUpdate(
            path=("bucket", "4", "count"),
            value=13,
            source="packet",
            confidence="exact",
        ),
        FieldUpdate(
            path=("bucket", "5", "total_cells"),
            value=30,
            source="packet",
            confidence="exact",
        ),
    )

    assert 6 not in session.buckets


def test_residual_red_bucket_uses_only_explicit_non_red_cells() -> None:
    session = _session_with_bucket_fields(
        FieldUpdate(
            path=("session", "warehouse_total_cells"),
            value=100,
            source="packet",
            confidence="exact",
        ),
        FieldUpdate(
            path=("bucket", "1", "total_cells"),
            value=10,
            source="packet",
            confidence="exact",
        ),
        FieldUpdate(
            path=("bucket", "3", "total_cells"),
            value=20,
            source="packet",
            confidence="exact",
        ),
        FieldUpdate(
            path=("bucket", "4", "total_cells"),
            value=30,
            source="packet",
            confidence="exact",
        ),
        FieldUpdate(
            path=("bucket", "5", "total_cells"),
            value=25,
            source="packet",
            confidence="exact",
        ),
    )

    assert session.buckets[6].total_cells == 15


@pytest.mark.parametrize(
    ("action_id", "path"),
    [
        (100104, ("bucket", "1", "total_cells")),
        (100107, ("bucket", "5", "total_cells")),
        (100113, ("bucket", "5", "avg_cells")),
        (100125, ("bucket", "5", "value_sum")),
    ],
)
def test_standard_quality_action_results_update_bucket_fields(
    action_id: int,
    path: tuple[str, str, str],
) -> None:
    batches = live_batches_from_fatbeans_events(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=1,
                    capture_time="",
                    message_id=0x0027,
                    session_id="s1",
                    map_id=2408,
                    round_index=4,
                    action_results=(
                        FatbeansActionResult(
                            action_id=action_id,
                            result=12,
                            result_field=14,
                        ),
                    ),
                ),
            ),
        )
    )

    updates = {
        update.path: update.value
        for batch in batches
        for update in batch.field_updates
    }
    assert updates[path] == 12


@pytest.mark.parametrize(
    ("action_id", "path"),
    [
        (100116, ("bucket_split", "green", "count")),
        (100122, ("bucket_split", "green", "value_sum")),
    ],
)
def test_green_only_action_results_update_split_fields(
    action_id: int,
    path: tuple[str, str, str],
) -> None:
    batches = live_batches_from_fatbeans_events(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=1,
                    capture_time="",
                    message_id=0x0027,
                    session_id="s1",
                    map_id=5610,
                    round_index=3,
                    action_results=(
                        FatbeansActionResult(
                            action_id=action_id,
                            result=12,
                            result_field=14,
                        ),
                    ),
                ),
            ),
        )
    )

    updates = {
        update.path: update.value
        for batch in batches
        for update in batch.field_updates
    }
    assert updates[path] == 12
    assert not any(
        update_path[:2] == ("bucket", "1") for update_path in updates
    )


def test_warehouse_total_action_result_updates_session_field() -> None:
    batches = live_batches_from_fatbeans_events(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=1,
                    capture_time="",
                    message_id=0x0027,
                    session_id="s1",
                    map_id=2408,
                    round_index=4,
                    action_results=(
                        FatbeansActionResult(
                            action_id=100103,
                            result=157,
                            result_field=14,
                        ),
                    ),
                ),
            ),
        )
    )

    updates = {
        update.path: update.value
        for batch in batches
        for update in batch.field_updates
    }
    assert updates[("session", "warehouse_total_cells")] == 157


@pytest.mark.parametrize(
    ("action_id", "path", "value"),
    [
        (100115, ("session", "total_item_count"), 42),
        (100119, ("bucket", "5", "count"), 3),
    ],
)
def test_count_action_results_update_inference_fields(
    action_id: int,
    path: tuple[str, ...],
    value: int,
) -> None:
    batches = live_batches_from_fatbeans_events(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=1,
                    capture_time="",
                    message_id=0x0027,
                    session_id="s1",
                    map_id=2408,
                    round_index=4,
                    action_results=(
                        FatbeansActionResult(
                            action_id=action_id,
                            result=value,
                            result_field=14,
                        ),
                    ),
                ),
            ),
        )
    )

    updates = {
        update.path: update.value
        for batch in batches
        for update in batch.field_updates
    }
    assert updates[path] == value


@pytest.mark.parametrize(
    ("action_id", "category"),
    [
        (100157, 107),
        (100160, 110),
    ],
)
def test_category_outline_action_becomes_soft_category_observation(
    action_id: int,
    category: int,
) -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=7,
                capture_time="",
                message_id=0x0025,
                session_id="s1",
                map_id=2401,
                round_index=1,
                action_results=(
                    FatbeansActionResult(
                        action_id=action_id,
                        result=None,
                        result_field=None,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=0,
                                runtime_id=11,
                                item_id=None,
                                quality=None,
                                value=None,
                                shape_code=13,
                                cells=None,
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    batches = live_batches_from_fatbeans_events(events)
    state = LiveSessionState()
    seed = LiveObservationBatch(
        source="manual",
        event_kind="manual_update",
        field_updates=(
            FieldUpdate(
                path=("session", "hero"),
                value="aisha",
                source="manual",
                confidence="exact",
            ),
        ),
    )
    state = apply_observation_batch(state, seed)
    for batch in batches:
        state = apply_observation_batch(state, batch)

    assert batches[0].grid_items[0].category == category
    session = live_state_to_session_obs(state)

    assert session is not None
    assert session.category_items[0].category == category
    assert session.category_items[0].cells == 3


def test_category_outline_merges_with_existing_runtime_item() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=7,
                capture_time="",
                message_id=0x0025,
                session_id="s1",
                map_id=2401,
                round_index=1,
                public_infos=(
                    FatbeansPublicInfo(
                        info_id=200050,
                        map_id=2401,
                        value=2,
                        value_field=11,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=10,
                                runtime_id=11,
                                item_id=1103006,
                                quality=3,
                                value=3240,
                                shape_code=21,
                                cells=2,
                            ),
                        ),
                    ),
                ),
                action_results=(
                    FatbeansActionResult(
                        action_id=100160,
                        result=None,
                        result_field=None,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=10,
                                runtime_id=11,
                                item_id=None,
                                quality=None,
                                value=None,
                                shape_code=21,
                                cells=None,
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    batch = live_batches_from_fatbeans_events(events)[0]

    assert len(batch.grid_items) == 1
    assert batch.grid_items[0].item_id == 1103006
    assert batch.grid_items[0].category == 110


def test_new_hero_skill_reveals_set_session_hero_and_category() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=7,
                capture_time="",
                message_id=0x0025,
                session_id="s1",
                map_id=2401,
                round_index=1,
                bids=(),
                skill_reveals=(
                    FatbeansSkillReveal(
                        skill_id=10002071,
                        hero_id=207,
                        round_index=1,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=22,
                                runtime_id=123,
                                item_id=None,
                                quality=None,
                                value=None,
                                shape_code=12,
                                cells=None,
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    batch = live_batches_from_fatbeans_events(events)[0]
    state = apply_observation_batch(LiveSessionState(), batch)
    session = live_state_to_session_obs(state)

    assert {update.path: update.value for update in batch.field_updates}[
        ("session", "hero")
    ] == "wuqilin"
    assert batch.grid_items[0].category == 106
    assert session is not None
    assert session.hero == "wuqilin"
    assert session.category_items[0].category == 106


def test_gabriela_bid_hero_id_sets_session_hero() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=7,
                capture_time="",
                message_id=0x0025,
                session_id="s1",
                map_id=2510,
                round_index=1,
                player_id=2,
                bids=(
                    FatbeansPlayerBid(
                        player_id=2,
                        name="local",
                        hero_id=104,
                        values=(688999,),
                    ),
                    FatbeansPlayerBid(
                        player_id=3,
                        name="other",
                        hero_id=208,
                        values=(566666,),
                    ),
                ),
                skill_reveals=(
                    FatbeansSkillReveal(
                        skill_id=1001041,
                        hero_id=104,
                        round_index=1,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=54,
                                runtime_id=123,
                                item_id=None,
                                quality=4,
                                value=None,
                                shape_code=11,
                                cells=None,
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    batch = live_batches_from_fatbeans_events(events)[0]
    state = apply_observation_batch(LiveSessionState(), batch)
    session = live_state_to_session_obs(state)

    assert {update.path: update.value for update in batch.field_updates}[
        ("session", "hero")
    ] == "gabriela"
    assert session is not None
    assert session.hero == "gabriela"
    assert batch.grid_items[0].quality == 4


def test_status_player_id_does_not_bind_local_player_without_bid_send() -> None:
    session = "2407:9000000000000008"
    rows = [
        _fatbeans_row(
            "REV",
            _rev_status_frame(session=session, player_id=3),
            sort_id=1,
        ),
        _fatbeans_row(
            "REV",
            _rev_state_with_bids_frame(
                session=session,
                map_id=2407,
                round_no=1,
                bids=(
                    _player_bid_block(
                        player_id=2,
                        name="local",
                        hero_id=105,
                        value=688999,
                    ),
                    _player_bid_block(
                        player_id=3,
                        name="other",
                        hero_id=208,
                        value=566666,
                    ),
                ),
            ),
            sort_id=2,
        ),
    ]

    events = parse_fatbeans_packets(load_fatbeans_packets_from_rows(rows))
    batch = live_batches_from_fatbeans_events(events)[0]

    assert events.statuses[0].player_id == 3
    assert events.states[0].player_id is None
    assert ("session", "hero") not in {
        update.path: update.value for update in batch.field_updates
    }


def test_single_skill_reveal_hero_sets_opening_hero_with_multiple_bid_heroes() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=1,
                capture_time="",
                message_id=0x0021,
                session_id="s1",
                map_id=2403,
                round_index=None,
                bids=(
                    FatbeansPlayerBid(
                        player_id=2,
                        name="local",
                        hero_id=105,
                        values=(),
                    ),
                    FatbeansPlayerBid(
                        player_id=3,
                        name="other",
                        hero_id=208,
                        values=(),
                    ),
                ),
                skill_reveals=(
                    FatbeansSkillReveal(
                        skill_id=100105,
                        hero_id=105,
                        round_index=1,
                        observed_items=(),
                    ),
                ),
            ),
        ),
    )

    batch = live_batches_from_fatbeans_events(events)[0]

    assert {update.path: update.value for update in batch.field_updates}[
        ("session", "hero")
    ] == "tatiana"


def test_local_bid_send_binds_hero_even_after_other_status_events() -> None:
    session = "2407:9000000000000008"
    rows = [
        _fatbeans_row(
            "REV",
            _rev_status_frame(session=session, player_id=3),
            sort_id=1,
        ),
        _fatbeans_row(
            "SEND",
            _send_bid_frame(session=session, value=688999),
            sort_id=2,
        ),
        _fatbeans_row(
            "REV",
            _rev_state_with_bids_frame(
                session=session,
                map_id=2407,
                round_no=1,
                bids=(
                    _player_bid_block(
                        player_id=2,
                        name="local",
                        hero_id=105,
                        value=688999,
                    ),
                    _player_bid_block(
                        player_id=3,
                        name="other",
                        hero_id=208,
                        value=566666,
                    ),
                ),
            ),
            sort_id=3,
        ),
    ]

    events = parse_fatbeans_packets(load_fatbeans_packets_from_rows(rows))
    batch = live_batches_from_fatbeans_events(events)[0]

    assert events.states[0].player_id == 2
    assert {update.path: update.value for update in batch.field_updates}[
        ("session", "hero")
    ] == "tatiana"


def test_local_player_hint_sets_opening_hero_with_multiple_bid_heroes() -> None:
    session = "2407:local_hint"
    rows = [
        _fatbeans_row(
            "REV",
            _rev_state_with_bids_frame(
                session=session,
                map_id=2407,
                round_no=1,
                bids=(
                    _player_bid_block(
                        player_id=2,
                        name="local",
                        hero_id=105,
                        value=0,
                    ),
                    _player_bid_block(
                        player_id=3,
                        name="other",
                        hero_id=208,
                        value=0,
                    ),
                ),
            ),
            sort_id=1,
        ),
    ]

    events = parse_fatbeans_packets(
        load_fatbeans_packets_from_rows(rows),
        local_player_id_hint=2,
    )
    batch = live_batches_from_fatbeans_events(events)[0]

    assert events.states[0].player_id == 2
    assert {update.path: update.value for update in batch.field_updates}[
        ("session", "hero")
    ] == "tatiana"


def test_local_bid_send_backfills_opening_state_hero() -> None:
    session = "2407:local_backfill"
    rows = [
        _fatbeans_row(
            "REV",
            _rev_state_with_bids_frame(
                session=session,
                map_id=2407,
                round_no=1,
                bids=(
                    _player_bid_block(
                        player_id=2,
                        name="local",
                        hero_id=105,
                        value=0,
                    ),
                    _player_bid_block(
                        player_id=3,
                        name="other",
                        hero_id=208,
                        value=0,
                    ),
                ),
            ),
            sort_id=1,
        ),
        _fatbeans_row(
            "SEND",
            _send_bid_frame(session=session, value=688999),
            sort_id=2,
        ),
        _fatbeans_row(
            "REV",
            _rev_state_with_bids_frame(
                session=session,
                map_id=2407,
                round_no=1,
                bids=(
                    _player_bid_block(
                        player_id=2,
                        name="local",
                        hero_id=105,
                        value=688999,
                    ),
                    _player_bid_block(
                        player_id=3,
                        name="other",
                        hero_id=208,
                        value=566666,
                    ),
                ),
            ),
            sort_id=3,
        ),
    ]

    events = parse_fatbeans_packets(load_fatbeans_packets_from_rows(rows))
    batches = live_batches_from_fatbeans_events(events)

    assert events.states[0].player_id == 2
    assert {update.path: update.value for update in batches[0].field_updates}[
        ("session", "hero")
    ] == "tatiana"


@pytest.mark.parametrize(
    ("hero_id", "hero"),
    [
        (101, "fatima"),
        (102, "chenmei"),
        (103, "aisha"),
        (104, "gabriela"),
        (105, "tatiana"),
        (106, "naomi"),
        (107, "sophie"),
        (108, "maria"),
        (109, "helena"),
        (110, "isabella"),
        (201, "george"),
        (202, "carlos"),
        (203, "leonard"),
        (204, "ahmed"),
        (205, "ivan"),
        (206, "takeda"),
        (207, "wuqilin"),
        (208, "ethan"),
        (209, "victor"),
        (301, "raven"),
        (302, "liss"),
    ],
)
def test_known_local_bid_hero_ids_set_session_hero(
    hero_id: int,
    hero: str,
) -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=7,
                capture_time="",
                message_id=0x0025,
                session_id="s1",
                map_id=2510,
                round_index=1,
                player_id=2,
                bids=(
                    FatbeansPlayerBid(
                        player_id=2,
                        name="local",
                        hero_id=hero_id,
                        values=(688999,),
                    ),
                ),
            ),
        ),
    )

    batch = live_batches_from_fatbeans_events(events)[0]

    assert {update.path: update.value for update in batch.field_updates}[
        ("session", "hero")
    ] == hero


def test_ahmad_numeric_skill_reveals_update_live_fields() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=11,
                capture_time="",
                message_id=0x0025,
                session_id="s1",
                map_id=2401,
                round_index=2,
                skill_reveals=(
                    FatbeansSkillReveal(
                        skill_id=100204,
                        hero_id=204,
                        round_index=1,
                        result=42,
                        result_field=14,
                    ),
                    FatbeansSkillReveal(
                        skill_id=1002041,
                        hero_id=204,
                        round_index=2,
                        result=8.5,
                        result_field=11,
                    ),
                    FatbeansSkillReveal(
                        skill_id=1002044,
                        hero_id=204,
                        round_index=5,
                        result=7,
                        result_field=14,
                    ),
                ),
            ),
        ),
    )

    batch = live_batches_from_fatbeans_events(events)[0]
    updates = {update.path: update.value for update in batch.field_updates}

    assert updates[("session", "hero")] == "ahmed"
    assert updates[("session", "total_item_count")] == 42
    assert updates[("bucket", "5", "avg_cells")] == 8.5
    assert updates[("bucket", "1", "count")] == 7


def test_victor_numeric_skill_reveal_updates_q4_q5_q6_count_sum() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=11,
                capture_time="",
                message_id=0x0025,
                session_id="s1",
                map_id=2401,
                round_index=1,
                skill_reveals=(
                    FatbeansSkillReveal(
                        skill_id=100209,
                        hero_id=209,
                        round_index=1,
                        result=8,
                        result_field=14,
                    ),
                ),
            ),
        ),
    )

    batch = live_batches_from_fatbeans_events(events)[0]
    updates = {update.path: update.value for update in batch.field_updates}

    assert updates[("session", "hero")] == "victor"
    assert updates[("bucket_group", "q4q5q6", "count")] == 8


def test_maria_skill_updates_value_sum_and_quality_markers() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=1,
                capture_time="",
                message_id=0x0021,
                session_id="s1",
                map_id=2406,
                round_index=1,
                skill_reveals=(
                    FatbeansSkillReveal(
                        skill_id=100108,
                        hero_id=108,
                        round_index=1,
                        result=37063,
                        result_field=12,
                    ),
                    FatbeansSkillReveal(
                        skill_id=10010801,
                        hero_id=108,
                        round_index=1,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=36,
                                runtime_id=501,
                                item_id=None,
                                quality=3,
                                value=None,
                                shape_code=None,
                                cells=None,
                            ),
                            FatbeansObservedItem(
                                local_index=90,
                                runtime_id=502,
                                item_id=None,
                                quality=1,
                                value=None,
                                shape_code=None,
                                cells=None,
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    batch = live_batches_from_fatbeans_events(events)[0]
    updates = {update.path: update.value for update in batch.field_updates}

    assert updates[("session", "hero")] == "maria"
    # 100108「玛丽亚·总价」= 低品三档合计, 不再误映射成 q1 白品 value_sum(2026-07-10 修·防回归)。
    # 玛丽亚估价证据在引擎侧走 skill_reveal_rows 直接路径, 不经此 structured_ref value_sum。
    assert ("bucket", "1", "value_sum") not in updates
    # 品质分布(10010801)仍作为位置 marker 进 grid_items。
    assert len(batch.grid_items) == 2
    assert {item.quality for item in batch.grid_items} == {1, 3}


def test_single_category_hero_skill_reveal_sets_category_evidence() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=7,
                capture_time="",
                message_id=0x0025,
                session_id="s1",
                map_id=2401,
                round_index=1,
                skill_reveals=(
                    FatbeansSkillReveal(
                        skill_id=100201,
                        hero_id=201,
                        round_index=1,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=10,
                                runtime_id=123,
                                item_id=None,
                                quality=4,
                                value=None,
                                shape_code=21,
                                cells=None,
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    batch = live_batches_from_fatbeans_events(events)[0]
    state = apply_observation_batch(LiveSessionState(), batch)
    session = live_state_to_session_obs(state)

    assert {update.path: update.value for update in batch.field_updates}[
        ("session", "hero")
    ] == "george"
    assert batch.grid_items[0].category == 104
    assert session is not None
    assert session.category_items[0].category == 104


def test_local_player_hero_does_not_use_first_table_bid() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=7,
                capture_time="",
                message_id=0x0025,
                session_id="s1",
                map_id=2401,
                round_index=1,
                player_id=2,
                bids=(
                    FatbeansPlayerBid(
                        player_id=1,
                        name="other",
                        hero_id=208,
                        values=(450_000,),
                    ),
                    FatbeansPlayerBid(
                        player_id=2,
                        name="me",
                        hero_id=103,
                        values=(369_250,),
                    ),
                ),
            ),
        ),
    )

    batches = live_batches_from_fatbeans_events(events)
    updates = {
        update.path: update.value
        for batch in batches
        for update in batch.field_updates
    }

    assert updates[("session", "hero")] == "aisha"


def test_ambiguous_table_heroes_do_not_guess_without_local_player() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=7,
                capture_time="",
                message_id=0x0025,
                session_id="s1",
                map_id=2401,
                round_index=1,
                bids=(
                    FatbeansPlayerBid(
                        player_id=1,
                        name="ethan",
                        hero_id=208,
                        values=(450_000,),
                    ),
                    FatbeansPlayerBid(
                        player_id=2,
                        name="aisha",
                        hero_id=103,
                        values=(369_250,),
                    ),
                ),
            ),
        ),
    )

    batches = live_batches_from_fatbeans_events(events)
    updates = {
        update.path: update.value
        for batch in batches
        for update in batch.field_updates
    }

    assert ("session", "hero") not in updates


def test_fatbeans_loader_skips_non_tcp_rows_with_http_payload_metadata() -> None:
    packets = load_fatbeans_packets_from_rows(
        [
            {
                "SortID": 1,
                "Protocol": "Https",
                "Direct": "SEND",
                "SrcIP": "127.0.0.1",
                "SrcPort": 1,
                "DstIP": "104.153.233.180",
                "DstPort": 443,
                "DataLength": 1781,
                "Data": "",
                "Url": "https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
            },
            {
                "SortID": 2,
                "Protocol": "Tcp",
                "Direct": "SEND",
                "SrcIP": "127.0.0.1",
                "SrcPort": 20062,
                "DstIP": "8.133.195.27",
                "DstPort": 10000,
                "CaptureTime": "2026-05-28T13:28:55",
                "DataLength": 12,
                "Data": "AAAAAAAAAAAAAAAA",
            },
        ]
    )

    assert len(packets) == 1
    assert packets[0].sort_id == 2
    assert packets[0].src == "127.0.0.1:20062"


@pytest.mark.skipif(
    not PROJECT4_CAPTURE.exists(),
    reason="local Fatbeans project4 capture is not committed",
)
def test_fatbeans_project4_extracts_known_round_facts() -> None:
    events = parse_fatbeans_capture(PROJECT4_CAPTURE)

    assert len(events.packets) == 97
    assert len(events.frames) == 83
    assert [send.value for send in events.sends if send.kind == "bid"] == [
        290000,
        290000,
        290000,
    ]
    assert [send.value for send in events.sends if send.kind == "action"] == [
        100105,
        100104,
        100124,
    ]

    start_state = next(state for state in events.states if state.sort_id == 10)
    assert start_state.message_id == 0x0021
    assert start_state.session_id == "2401:9000000000000003"
    assert start_state.map_id == 2401
    assert start_state.round_index is None
    assert start_state.public_infos[0].info_id == 200014
    assert start_state.public_infos[0].value == pytest.approx(2.965517, abs=1e-6)
    assert any(reveal.hero_id == 208 for reveal in start_state.skill_reveals)

    direct_actions = [
        (state.sort_id, result.action_id, result.result, state.session_id, state.map_id)
        for state in events.states
        if state.message_id == 0x0027
        for result in state.action_results
    ]
    assert direct_actions == [
        (26, 100105, 48, "2401:9000000000000003", 2401),
        (59, 100104, 8, "2401:9000000000000003", 2401),
        (82, 100124, 45778, "2401:9000000000000003", 2401),
    ]

    first_state = next(state for state in events.states if state.sort_id == 34)
    assert first_state.session_id == "2401:9000000000000003"
    assert first_state.map_id == 2401
    assert first_state.round_index == 1
    assert {bid.name: bid.current_value for bid in first_state.bids} == {
        "设计师lcjeremy": 153299,
        "加菲_Barista": 290000,
        "梦色幻想": 288888,
        "折翼的奇美拉": 220000,
    }
    assert [(r.action_id, r.result) for r in first_state.action_results] == [
        (100105, 48)
    ]
    assert first_state.public_infos[0].info_id == 200014
    assert first_state.public_infos[0].value == pytest.approx(2.965517, abs=1e-6)

    second_state = next(state for state in events.states if state.sort_id == 69)
    assert [(r.action_id, r.result) for r in second_state.action_results] == [
        (100105, 48),
        (100104, 8),
    ]
    assert [(info.info_id, info.value) for info in second_state.public_infos] == [
        (200014, pytest.approx(2.965517, abs=1e-6)),
        (200033, pytest.approx(3765.0, abs=1e-6)),
    ]

    settled = next(state for state in events.states if state.sort_id == 102)
    assert settled.settlement_loss_units == 16566
    assert [(r.action_id, r.result) for r in settled.action_results] == [
        (100105, 48),
        (100104, 8),
        (100124, 45778),
    ]
    assert latest_player_bids(events.states) == {
        "设计师lcjeremy": 417779,
        "加菲_Barista": 290000,
        "梦色幻想": 188888,
        "折翼的奇美拉": 220000,
    }


@pytest.mark.skipif(
    not PROJECT4_CAPTURE.exists(),
    reason="local Fatbeans project4 capture is not committed",
)
def test_fatbeans_project4_converts_supported_fields_to_live_batches() -> None:
    batches = live_batches_from_fatbeans_capture(PROJECT4_CAPTURE)
    updates = {
        update.path: update.value
        for batch in batches
        for update in batch.field_updates
    }

    assert updates[("session", "map_id")] == 2401
    assert updates[("session", "round")] == 2
    assert updates[("session", "hero")] == "ethan"
    assert updates[("bucket", "3", "total_cells")] == 48
    assert updates[("bucket", "4", "value_sum")] == 45778
    assert all(batch.source == "packet" for batch in batches)
    assert batches[0].sequence == 10
    assert batches[0].event_kind == "session_started"
    assert batches[0].phase == "reading"
    assert len(batches[0].grid_items) == 17
    direct_batch = next(batch for batch in batches if batch.sequence == 26)
    assert direct_batch.event_kind == "tool_revealed"
    assert direct_batch.phase == "bidding"
    assert {
        update.path: update.value
        for update in direct_batch.field_updates
    }[("bucket", "3", "total_cells")] == 48


@pytest.mark.skipif(
    not PROJECT4_CAPTURE.exists(),
    reason="local Fatbeans project4 capture is not committed",
)
def test_fatbeans_project4_payload_entrypoints_match_path_entrypoints() -> None:
    raw = PROJECT4_CAPTURE.read_bytes()

    from_path = parse_fatbeans_capture(PROJECT4_CAPTURE)
    from_payload = parse_fatbeans_capture_payload(raw)
    path_batches = live_batches_from_fatbeans_capture(PROJECT4_CAPTURE)
    payload_batches = live_batches_from_fatbeans_capture_payload(raw)

    assert len(from_payload.packets) == len(from_path.packets)
    assert len(from_payload.frames) == len(from_path.frames)
    assert len(from_payload.states) == len(from_path.states)
    assert payload_batches == path_batches


PACKAGE5_CAPTURE = FATBEANS_SAMPLE_DIR / "bid_king_packages_5.json"


@pytest.mark.skipif(
    not PACKAGE5_CAPTURE.exists(),
    reason="local Fatbeans package5 capture is not committed",
)
def test_fatbeans_package5_extracts_revealed_items_and_inventory() -> None:
    events = parse_fatbeans_capture(PACKAGE5_CAPTURE)

    assert len(events.packets) == 103
    assert len(events.frames) == 118
    assert [send.value for send in events.sends if send.kind == "action"] == [
        100136,
        100129,
        100105,
        100104,
        100112,
    ]

    first_state = next(state for state in events.states if state.sort_id == 72)
    treasure = first_state.action_results[0]
    assert treasure.action_id == 100136
    assert treasure.result is None
    assert [(item.runtime_id, item.quality) for item in treasure.observed_items] == [
        (1274127927583413, 4),
        (1274127927583400, 5),
        (1274127927583403, 4),
        (1274127927583386, 5),
    ]

    second_state = next(state for state in events.states if state.sort_id == 102)
    sampling = next(
        result for result in second_state.action_results
        if result.action_id == 100129
    )
    assert [(item.item_id, item.quality, item.value, item.cells) for item in sampling.observed_items] == [
        (1021005, 1, 207, 2),
        (1023009, 3, 1313, 1),
    ]
    assert sampling.result == 3

    settled = next(state for state in events.states if state.sort_id == 194)
    assert settled.round_no == 4
    assert len(settled.inventory_items) == 42
    assert sum(item.cells for item in settled.inventory_items) == 114
    purple_avg = next(
        result for result in settled.action_results
        if result.action_id == 100112
    )
    assert purple_avg.result == pytest.approx(2.3076923, abs=1e-6)
    by_runtime = {item.runtime_id: item for item in settled.inventory_items}
    assert by_runtime[1274127927583413].item_id == 1024001
    assert by_runtime[1274127927583413].cells == 4
    assert by_runtime[1274127927583400].item_id == 1045007
    assert by_runtime[1274127927583400].cells == 6


@pytest.mark.skipif(
    not PACKAGE5_CAPTURE.exists(),
    reason="local Fatbeans package5 capture is not committed",
)
def test_fatbeans_package5_live_batches_include_inventory_constraints() -> None:
    batches = live_batches_from_fatbeans_capture(PACKAGE5_CAPTURE)
    state = apply_observation_batch(
        LiveSessionState(),
        LiveObservationBatch(
            source="manual",
            field_updates=(
                FieldUpdate(
                    path=("session", "hero"),
                    value="ethan",
                    source="manual",
                    confidence="exact",
                ),
            ),
        ),
    )
    for batch in batches:
        state = apply_observation_batch(state, batch)

    session = live_state_to_session_obs(state)

    assert session is not None
    assert session.map_id == 2408
    assert session.warehouse_total_cells == 114
    assert session.total_item_count == 42
    assert session.buckets[1].total_cells == 3
    assert session.buckets[1].count == 2
    assert session.buckets[3].total_cells == 26
    assert session.buckets[4].total_cells == 30
    assert session.buckets[4].count == 13
    assert session.buckets[4].avg_cells is not None
    assert session.buckets[4].avg_cells.raw.startswith("2.307692")
    assert session.buckets[5].total_cells == 42
    assert session.buckets[6].total_cells == 9
    assert len(state.grid_items) == 42


PACKAGE6_CAPTURE = FATBEANS_SAMPLE_DIR / "bid_king_packages6.json"


@pytest.mark.skipif(
    not PACKAGE6_CAPTURE.exists(),
    reason="local Fatbeans package6 capture is not committed",
)
def test_fatbeans_package6_extracts_skill_public_and_inventory_facts() -> None:
    events = parse_fatbeans_capture(PACKAGE6_CAPTURE)

    assert len(events.packets) == 70
    assert len(events.frames) == 74
    assert [send.value for send in events.sends if send.kind == "action"] == [
        100129
    ]

    first_state = next(state for state in events.states if state.sort_id == 61)
    assert [(reveal.skill_id, len(reveal.observed_items)) for reveal in first_state.skill_reveals] == [
        (1001034, 6),
        (1001033, 13),
    ]

    third_state = next(state for state in events.states if state.sort_id == 116)
    public_reveal = next(
        info for info in third_state.public_infos
        if info.info_id == 200026
    )
    assert [(item.runtime_id, item.quality) for item in public_reveal.observed_items] == [
        (1274127940693032, 4),
        (1274127940693022, 5),
        (1274127940693030, 2),
    ]

    settled = next(state for state in events.states if state.sort_id == 146)
    sampling = settled.action_results[0]
    assert sampling.action_id == 100129
    assert [
        (item.item_id, item.quality, item.value, item.shape_code, item.cells)
        for item in sampling.observed_items
    ] == [
        (1025001, 5, 49920, 22, 4),
        (1071004, 1, 137, 11, 1),
    ]

    assert len(settled.inventory_items) == 53
    assert sum(item.cells for item in settled.inventory_items) == 137
    counts = Counter(item.quality for item in settled.inventory_items)
    cells = Counter()
    for item in settled.inventory_items:
        cells[item.quality] += item.cells
    assert counts == {1: 6, 2: 13, 3: 14, 4: 9, 5: 7, 6: 4}
    assert cells == {1: 11, 2: 24, 3: 28, 4: 32, 5: 29, 6: 13}


@pytest.mark.skipif(
    not PACKAGE6_CAPTURE.exists(),
    reason="local Fatbeans package6 capture is not committed",
)
def test_fatbeans_package6_live_batches_include_skill_shapes() -> None:
    batches = live_batches_from_fatbeans_capture(PACKAGE6_CAPTURE)
    first = next(batch for batch in batches if batch.sequence == 61)
    third = next(batch for batch in batches if batch.sequence == 116)
    settled = next(batch for batch in batches if batch.sequence == 146)

    assert len(first.grid_items) == 19
    assert sum(item.cells for item in first.grid_items) == 35
    assert len(third.grid_items) == 42
    assert sum(item.cells for item in third.grid_items) == 95
    assert len(settled.grid_items) == 53
    assert sum(item.cells for item in settled.grid_items) == 137


@pytest.mark.skipif(
    not PACKAGE6_CAPTURE.exists(),
    reason="local Fatbeans package6 capture is not committed",
)
def test_fatbeans_package6_aisha_reveals_pin_bucket_cells_and_counts() -> None:
    batches = live_batches_from_fatbeans_capture(PACKAGE6_CAPTURE)
    first = next(batch for batch in batches if batch.sequence == 61)
    third = next(batch for batch in batches if batch.sequence == 116)
    first_updates = {update.path: update.value for update in first.field_updates}
    third_updates = {update.path: update.value for update in third.field_updates}

    assert first_updates[("bucket", "1", "total_cells")] == 11
    assert first_updates[("bucket", "1", "count")] == 6
    assert first_updates[("bucket", "2", "total_cells")] == 24
    assert first_updates[("bucket", "2", "count")] == 13
    assert third_updates[("bucket", "3", "total_cells")] == 28
    assert third_updates[("bucket", "3", "count")] == 14
    assert third_updates[("bucket", "4", "total_cells")] == 32
    assert third_updates[("bucket", "4", "count")] == 9


def test_fatbeans_aisha_skill_emits_split_low_quality_updates() -> None:
    batches = live_batches_from_fatbeans_events(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=7,
                    capture_time="2026-06-09T00:00:00",
                    message_id=0x0021,
                    session_id="s1",
                    map_id=2401,
                    round_index=1,
                    skill_reveals=(
                        FatbeansSkillReveal(
                            skill_id=1001034,
                            hero_id=103,
                            round_index=1,
                            observed_items=(
                                FatbeansObservedItem(
                                    local_index=0,
                                    runtime_id=101,
                                    item_id=1001001,
                                    quality=1,
                                    value=None,
                                    shape_code=12,
                                    cells=None,
                                ),
                                FatbeansObservedItem(
                                    local_index=2,
                                    runtime_id=102,
                                    item_id=1001002,
                                    quality=1,
                                    value=None,
                                    shape_code=11,
                                    cells=None,
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )

    updates = {update.path: update.value for update in batches[0].field_updates}

    assert updates[("bucket", "1", "total_cells")] == 3
    assert updates[("bucket", "1", "count")] == 2
    assert updates[("bucket_split", "white", "total_cells")] == 3
    assert updates[("bucket_split", "white", "count")] == 2


PACKAGE7_CAPTURE = FATBEANS_SAMPLE_DIR / "bid_king_packages7.json"
PACKAGE8_CAPTURE = FATBEANS_SAMPLE_DIR / "bidking_package8.json"
PACKAGE9_CAPTURE = FATBEANS_SAMPLE_DIR / "bidking_package_9_with_whole_bucket_perspective.json"
PACKAGE10_CAPTURE = FATBEANS_SAMPLE_DIR / "bidking_package10.json"
SETTLEMENT_5604_F3_SPLIT_CAPTURE = (
    Path(__file__).resolve().parent
    / "fixtures/live/settlement_5604_f3_split_npcap_live.jsonl"
)
SETTLEMENT_2508_TAIL_DESYNC_CAPTURE = (
    Path(__file__).resolve().parent
    / "fixtures/live/settlement_2508_tail_desync_npcap_live.jsonl"
)
PACKAGE11_CAPTURE = (
    FATBEANS_SAMPLE_DIR
    / "bidking_package11_with_4itemsinspection_bucketperspective_ethansskill.json"
)
PACKAGE12_CAPTURE = (
    FATBEANS_SAMPLE_DIR
    / "bidking_package12_wholebucktperspection_scan_publicinfo_basicitems_aisha.json"
)
PACKAGE13_CAPTURE = FATBEANS_SAMPLE_DIR / "bidking_package13_eye_of_clarity_ethan.json"
PACKAGE14_CAPTURE = FATBEANS_SAMPLE_DIR / "bidking_package14_aisha.json"
PACKAGE15_CAPTURE = FATBEANS_SAMPLE_DIR / "bidking_package15_ethan.json"
PACKAGE16_CAPTURE = FATBEANS_SAMPLE_DIR / "bidking_package16_ethan.json"
PACKAGE17_CAPTURE = FATBEANS_SAMPLE_DIR / "bidking_package17_eye_of_clarity_ethan.json"


def _shape_cells(shape_code: int | None) -> int:
    if shape_code is None:
        return 0
    return (shape_code // 10) * (shape_code % 10)


@pytest.mark.skipif(
    not PACKAGE7_CAPTURE.exists(),
    reason="local Fatbeans package7 capture is not committed",
)
def test_fatbeans_package7_extracts_aisha_and_settlement_inventory() -> None:
    events = parse_fatbeans_capture(PACKAGE7_CAPTURE)

    assert [send.value for send in events.sends if send.kind == "action"] == [
        100129,
        100136,
    ]
    settled = next(state for state in events.states if state.inventory_items)

    assert settled.map_id == 2401
    assert settled.round_index == 2
    assert len(settled.inventory_items) == 33
    assert sum(item.cells for item in settled.inventory_items) == 85
    counts = Counter(item.quality for item in settled.inventory_items)
    cells = Counter()
    for item in settled.inventory_items:
        cells[item.quality] += item.cells
    assert counts == {1: 8, 2: 7, 3: 8, 4: 4, 5: 5, 6: 1}
    assert cells == {1: 18, 2: 11, 3: 27, 4: 9, 5: 19, 6: 1}


@pytest.mark.skipif(
    not PACKAGE7_CAPTURE.exists(),
    reason="local Fatbeans package7 capture is not committed",
)
def test_fatbeans_package7_live_batches_include_aisha_reveal_constraints() -> None:
    batches = live_batches_from_fatbeans_capture(PACKAGE7_CAPTURE)
    state = apply_observation_batch(
        LiveSessionState(),
        LiveObservationBatch(
            source="manual",
            field_updates=(
                FieldUpdate(
                    path=("session", "hero"),
                    value="aisha",
                    source="manual",
                    confidence="exact",
                ),
            ),
        ),
    )
    for batch in batches:
        state = apply_observation_batch(state, batch)
    session = live_state_to_session_obs(state)

    assert session is not None
    assert session.map_id == 2401
    assert session.buckets[1].count == 8
    assert session.buckets[1].total_cells == 18
    assert session.buckets[2].count == 7
    assert session.buckets[2].total_cells == 11
    assert session.buckets[3].count == 8
    assert session.buckets[3].total_cells == 27


@pytest.mark.skipif(
    not PACKAGE8_CAPTURE.exists(),
    reason="local Fatbeans package8 capture is not committed",
)
def test_fatbeans_package8_extracts_digital_outline_item_and_inventory() -> None:
    events = parse_fatbeans_capture(PACKAGE8_CAPTURE)

    assert [send.value for send in events.sends if send.kind == "action"] == [
        100136,
        100129,
        100157,
    ]
    settled = next(state for state in events.states if state.inventory_items)
    digital_outline = next(
        result for result in settled.action_results
        if result.action_id == 100157
    )

    assert [(item.shape_code, item.cells) for item in digital_outline.observed_items] == [
        (13, None),
        (11, None),
        (11, None),
    ]
    assert len(settled.inventory_items) == 37
    assert sum(item.cells for item in settled.inventory_items) == 101
    counts = Counter(item.quality for item in settled.inventory_items)
    cells = Counter()
    for item in settled.inventory_items:
        cells[item.quality] += item.cells
    assert counts == {1: 5, 2: 3, 3: 14, 4: 8, 5: 4, 6: 3}
    assert cells == {1: 7, 2: 4, 3: 44, 4: 35, 5: 7, 6: 4}


@pytest.mark.skipif(
    not PACKAGE8_CAPTURE.exists(),
    reason="local Fatbeans package8 capture is not committed",
)
def test_fatbeans_package8_live_batches_include_aisha_reveal_constraints() -> None:
    batches = live_batches_from_fatbeans_capture(PACKAGE8_CAPTURE)
    third = next(batch for batch in batches if batch.sequence == 59)
    updates = {update.path: update.value for update in third.field_updates}

    assert updates[("bucket", "1", "total_cells")] == 7
    assert updates[("bucket", "1", "count")] == 5
    assert updates[("bucket", "2", "total_cells")] == 4
    assert updates[("bucket", "2", "count")] == 3
    assert updates[("bucket", "3", "total_cells")] == 44
    assert updates[("bucket", "3", "count")] == 14
    assert updates[("bucket", "4", "total_cells")] == 35
    assert updates[("bucket", "4", "count")] == 8


@pytest.mark.skipif(
    not PACKAGE9_CAPTURE.exists(),
    reason="local Fatbeans package9 capture is not committed",
)
def test_fatbeans_package9_whole_bucket_perspective_matches_inventory_cells() -> None:
    events = parse_fatbeans_capture(PACKAGE9_CAPTURE)
    settled = next(state for state in events.states if state.inventory_items)
    perspective = next(
        result for result in settled.action_results
        if result.action_id == 100100
    )

    assert len(perspective.observed_items) == len(settled.inventory_items) == 41
    assert sum(_shape_cells(item.shape_code) for item in perspective.observed_items) == 101
    assert sum(item.cells for item in settled.inventory_items) == 101


@pytest.mark.skipif(
    not PACKAGE10_CAPTURE.exists(),
    reason="local Fatbeans package10 capture is not committed",
)
def test_fatbeans_package10_skips_https_noise_and_extracts_settlement() -> None:
    events = parse_fatbeans_capture(PACKAGE10_CAPTURE)
    settled = next(state for state in events.states if state.inventory_items)

    assert [send.value for send in events.sends if send.kind == "action"] == [
        100105,
        100104,
        100129,
    ]
    assert len(settled.inventory_items) == 38
    assert sum(item.cells for item in settled.inventory_items) == 121
    random_inspection = next(
        result for result in settled.action_results
        if result.action_id == 100129
    )
    assert [
        (item.item_id, item.quality, item.value, item.shape_code)
        for item in random_inspection.observed_items
    ] == [
        (1034007, 4, 4428, 12),
        (1083011, 3, 14659, 53),
    ]


@pytest.mark.skipif(
    not SETTLEMENT_5604_F3_SPLIT_CAPTURE.exists(),
    reason="5604 ranked settlement diag capture is not present",
)
def test_settlement_0x002d_merges_top_level_f3_inventory_blocks() -> None:
    """Ranked 5604 can attach per-player rows in top-level f3 on the same 0x002D."""
    import json

    rows = [
        json.loads(line)
        for line in SETTLEMENT_5604_F3_SPLIT_CAPTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    events = parse_fatbeans_packets(load_fatbeans_packets_from_rows(rows))
    settled = next(state for state in events.states if state.message_id == 0x002D)

    assert len(settled.inventory_items) == 33


def test_settlement_0x002d_recovers_tail_items_after_new_map_desync() -> None:
    """New-map (2508 shipwreck, 2026-07-03) 0x2D f4 body desyncs mid-frame.

    _parse_fields truncates the inner inventory block at the desync point, so
    every item wrapper after it is dropped: this frame parsed 41/47 items and
    read 424,916 vs the game's own 952,712 — the two most valuable reds (跑车
    293k / 黄唇鱼鱼胶 193k) sat in the lost tail. Tail recovery slides the raw
    block for the missed wrappers and unions them back. The kill-switch env
    reverts to the truncated 41. Old 56xx frames parse fully -> recovery is a
    no-op there (asserted above stays 33).
    """
    import json
    import os

    rows = [
        json.loads(line)
        for line in SETTLEMENT_2508_TAIL_DESYNC_CAPTURE.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]

    events = parse_fatbeans_packets(load_fatbeans_packets_from_rows(rows))
    settled = next(state for state in events.states if state.message_id == 0x002D)
    assert len(settled.inventory_items) == 47
    # The recovered tail is the high-value reds the truncated parse dropped.
    recovered_ids = {item.item_id for item in settled.inventory_items}
    assert 1086008 in recovered_ids  # 时尚轿跑 (red, 15 cells)
    assert 1096009 in recovered_ids  # 黄唇鱼鱼胶 (red)
    assert sum(1 for item in settled.inventory_items if item.quality == 6) == 3

    # Kill-switch reverts to the pre-fix truncated parse.
    previous = os.environ.get("BIDKING_DISABLE_SETTLEMENT_TAIL_RECOVERY")
    os.environ["BIDKING_DISABLE_SETTLEMENT_TAIL_RECOVERY"] = "1"
    try:
        disabled_events = parse_fatbeans_packets(
            load_fatbeans_packets_from_rows(rows)
        )
        disabled_settled = next(
            state for state in disabled_events.states if state.message_id == 0x002D
        )
        assert len(disabled_settled.inventory_items) == 41
    finally:
        if previous is None:
            os.environ.pop("BIDKING_DISABLE_SETTLEMENT_TAIL_RECOVERY", None)
        else:
            os.environ["BIDKING_DISABLE_SETTLEMENT_TAIL_RECOVERY"] = previous


@pytest.mark.skipif(
    not PACKAGE10_CAPTURE.exists(),
    reason="local Fatbeans package10 capture is not committed",
)
def test_fatbeans_package10_map_public_purple_outlines_join_ethan_shapes() -> None:
    events = parse_fatbeans_capture(PACKAGE10_CAPTURE)
    first_state = next(state for state in events.states if state.sort_id == 28)
    public_purple = next(
        info for info in first_state.public_infos
        if info.info_id == 200001
    )
    opening_ethan = next(
        reveal for reveal in first_state.skill_reveals
        if reveal.skill_id == 1002081
    )

    assert {item.quality for item in opening_ethan.observed_items} == {None}
    assert len(public_purple.observed_items) == 13
    assert {item.quality for item in public_purple.observed_items} == {4}
    assert sum(_shape_cells(item.shape_code) for item in public_purple.observed_items) == 39

    batches = live_batches_from_fatbeans_capture(PACKAGE10_CAPTURE)
    first_batch = next(batch for batch in batches if batch.sequence == 28)
    first_updates = {update.path: update.value for update in first_batch.field_updates}
    purple_items = [item for item in first_batch.grid_items if item.quality == 4]

    assert first_updates[("bucket", "4", "total_cells")] == 39
    assert first_updates[("bucket", "4", "count")] == 13
    assert len(purple_items) == 13
    assert sum(item.cells for item in purple_items) == 39

    state = LiveSessionState()
    state = apply_observation_batch(state, first_batch)
    session = live_state_to_session_obs(state)

    assert session is not None
    assert session.buckets[4].total_cells == 39
    assert session.buckets[4].count == 13
    assert session.visible_outline_item_count_min == 27
    assert session.visible_outline_total_cells_min == 96
    assert session.unknown_outline_item_count == 14
    assert session.unknown_outline_total_cells == 57


@pytest.mark.skipif(
    not PACKAGE11_CAPTURE.exists(),
    reason="local Fatbeans package11 capture is not committed",
)
def test_fatbeans_package11_ethan_skill_matches_whole_bucket_perspective() -> None:
    events = parse_fatbeans_capture(PACKAGE11_CAPTURE)
    pre_settlement = next(state for state in events.states if state.sort_id == 80)
    settled = next(state for state in events.states if state.inventory_items)
    final_skill = next(
        reveal for reveal in pre_settlement.skill_reveals
        if reveal.skill_id == 1002085
    )
    perspective = next(
        result for result in settled.action_results
        if result.action_id == 100100
    )

    skill_shapes = {
        item.runtime_id: item.shape_code for item in final_skill.observed_items
    }
    perspective_shapes = {
        item.runtime_id: item.shape_code for item in perspective.observed_items
    }

    assert skill_shapes == perspective_shapes
    assert len(skill_shapes) == len(settled.inventory_items) == 32
    assert sum(_shape_cells(shape) for shape in skill_shapes.values()) == 79
    assert sum(item.cells for item in settled.inventory_items) == 79


@pytest.mark.skipif(
    not PACKAGE11_CAPTURE.exists(),
    reason="local Fatbeans package11 capture is not committed",
)
def test_fatbeans_package11_baoguang_quality_joins_ethan_shapes() -> None:
    batches = live_batches_from_fatbeans_capture(PACKAGE11_CAPTURE)
    first = next(batch for batch in batches if batch.sequence == 19)
    cells_by_quality = Counter()
    count_by_quality = Counter()
    for item in first.grid_items:
        if item.quality is None:
            continue
        cells_by_quality[item.quality] += item.cells
        count_by_quality[item.quality] += 1

    assert count_by_quality[3] == 3
    assert cells_by_quality[3] == 6
    assert count_by_quality[4] == 1
    assert cells_by_quality[4] == 2


@pytest.mark.skipif(
    not PACKAGE11_CAPTURE.exists(),
    reason="local Fatbeans package11 capture is not committed",
)
def test_fatbeans_package11_full_ethan_outline_pins_live_warehouse() -> None:
    batches = live_batches_from_fatbeans_capture(PACKAGE11_CAPTURE)
    fourth = next(batch for batch in batches if batch.sequence == 80)
    updates = {update.path: update.value for update in fourth.field_updates}

    assert updates[("session", "warehouse_total_cells")] == 79
    assert updates[("session", "total_item_count")] == 32
    assert len(fourth.grid_items) == 32
    assert sum(item.cells for item in fourth.grid_items) == 79


@pytest.mark.skipif(
    not PACKAGE12_CAPTURE.exists(),
    reason="local Fatbeans package12 capture is not committed",
)
def test_fatbeans_package12_aisha_full_perspective_pins_warehouse() -> None:
    events = parse_fatbeans_capture(PACKAGE12_CAPTURE)
    third = next(state for state in events.states if state.sort_id == 79)
    inspection = next(
        result for result in third.action_results
        if result.action_id == 100128
    )
    assert [
        (item.local_index, item.item_id, item.quality, item.shape_code)
        for item in inspection.observed_items
    ] == [(2, 1063002, 3, 12)]

    second = next(state for state in events.states if state.sort_id == 61)
    random_inspection = next(
        result for result in second.action_results
        if result.action_id == 100129
    )
    assert {
        (item.local_index, item.item_id, item.quality, item.shape_code)
        for item in random_inspection.observed_items
    } == {
        (38, 1021004, 1, 21),
        (92, 1085003, 5, 32),
    }

    batches = live_batches_from_fatbeans_capture(PACKAGE12_CAPTURE)
    fourth = next(batch for batch in batches if batch.sequence == 98)
    updates = {update.path: update.value for update in fourth.field_updates}

    assert updates[("bucket", "1", "total_cells")] == 6
    assert updates[("bucket", "1", "count")] == 3
    assert updates[("bucket", "2", "total_cells")] == 11
    assert updates[("bucket", "2", "count")] == 4
    assert updates[("bucket", "3", "total_cells")] == 41
    assert updates[("bucket", "3", "count")] == 15
    assert updates[("bucket", "4", "total_cells")] == 27
    assert updates[("bucket", "4", "count")] == 10
    assert updates[("session", "warehouse_total_cells")] == 123
    assert updates[("session", "total_item_count")] == 42
    assert len(fourth.grid_items) == 42
    assert sum(item.cells for item in fourth.grid_items) == 123


@pytest.mark.skipif(
    not PACKAGE13_CAPTURE.exists(),
    reason="local Fatbeans package13 capture is not committed",
)
def test_fatbeans_package13_mirror_eye_joins_ethan_outlines() -> None:
    events = parse_fatbeans_capture(PACKAGE13_CAPTURE)
    settled = next(state for state in events.states if state.inventory_items)
    first = next(state for state in events.states if state.sort_id == 48)
    mirror = next(
        result for result in first.action_results
        if result.action_id == 100134
    )
    known_outline = next(
        reveal for reveal in first.skill_reveals
        if reveal.skill_id == 1002082
    )

    assert len(mirror.observed_items) == len(known_outline.observed_items) == 58
    assert len(settled.inventory_items) == 58
    assert sum(_shape_cells(item.shape_code) for item in known_outline.observed_items) == 216
    assert sum(item.cells for item in settled.inventory_items) == 216

    batches = live_batches_from_fatbeans_capture(PACKAGE13_CAPTURE)
    first_batch = next(batch for batch in batches if batch.sequence == 48)
    updates = {update.path: update.value for update in first_batch.field_updates}
    cells_by_quality = Counter()
    count_by_quality = Counter()
    for item in first_batch.grid_items:
        count_by_quality[item.quality] += 1
        cells_by_quality[item.quality] += item.cells
    local_quality_shapes = {
        (item.local_index, item.quality, item.shape_key)
        for item in first_batch.grid_items
    }

    assert updates[("session", "warehouse_total_cells")] == 216
    assert updates[("session", "total_item_count")] == 58
    assert count_by_quality == {1: 1, 2: 13, 3: 12, 4: 20, 5: 7, 6: 5}
    assert cells_by_quality == {1: 1, 2: 37, 3: 29, 4: 75, 5: 37, 6: 37}
    assert (83, 1, "11") in local_quality_shapes
    assert (86, 6, "34") in local_quality_shapes


@pytest.mark.skipif(
    not PACKAGE14_CAPTURE.exists(),
    reason="local Fatbeans package14 capture is not committed",
)
def test_fatbeans_package14_aisha_coordinates_match_user_marks() -> None:
    events = parse_fatbeans_capture(PACKAGE14_CAPTURE)
    second = next(state for state in events.states if state.sort_id == 58)
    random_inspection = next(
        result for result in second.action_results
        if result.action_id == 100129
    )
    public_highest_area = next(
        info for info in second.public_infos
        if info.info_id == 200050
    )

    assert {
        (item.local_index, item.item_id, item.quality, item.shape_code)
        for item in random_inspection.observed_items
    } == {
        (69, 1041009, 1, 11),  # 泡泡水弹: row 7, col 10.
        (75, 1015001, 5, 21),  # 章丘铁锅: row 8, col 6.
    }
    assert [
        (item.local_index, item.item_id, item.quality, item.shape_code)
        for item in public_highest_area.observed_items
    ] == [(94, 1093009, 3, 33)]  # 蓝纹奶酪: rows 10-12, cols 5-7.


@pytest.mark.skipif(
    not PACKAGE15_CAPTURE.exists(),
    reason="local Fatbeans package15 capture is not committed",
)
def test_fatbeans_package15_baoguang_runtime_joins_ethan_shapes() -> None:
    batches = live_batches_from_fatbeans_capture(PACKAGE15_CAPTURE)
    first = next(batch for batch in batches if batch.sequence == 19)
    local_quality_shapes = {
        (item.local_index, item.quality, item.shape_key, item.cells)
        for item in first.grid_items
    }

    assert (3, 4, "11", 1) in local_quality_shapes
    assert (4, 1, "11", 1) in local_quality_shapes
    assert (114, 6, "22", 4) in local_quality_shapes

    events = parse_fatbeans_capture(PACKAGE15_CAPTURE)
    second = next(state for state in events.states if state.sort_id == 38)
    random_inspection = next(
        result for result in second.action_results
        if result.action_id == 100129
    )
    assert {
        (item.local_index, item.item_id, item.quality, item.shape_code)
        for item in random_inspection.observed_items
    } == {
        (100, 1032005, 2, 22),  # 印花雨伞.
        (23, 1062004, 2, 22),  # 玛瑙棋.
    }


@pytest.mark.skipif(
    not PACKAGE16_CAPTURE.exists(),
    reason="local Fatbeans package16 capture is not committed",
)
def test_fatbeans_package16_baoguang_runtime_beats_quality_local_index() -> None:
    events = parse_fatbeans_capture(PACKAGE16_CAPTURE)
    settled = next(state for state in events.states if state.inventory_items)
    wall_fragment = next(
        item for item in settled.inventory_items
        if item.item_id == 1105004
    )
    shape_by_runtime = {
        item.runtime_id: (item.local_index, item.shape_code)
        for state in events.states
        for reveal in state.skill_reveals
        for item in reveal.observed_items
    }
    quality_by_runtime = {
        item.runtime_id: (item.local_index, item.quality)
        for state in events.states
        for result in state.action_results
        if result.action_id == 100136
        for item in result.observed_items
    }

    assert shape_by_runtime[wall_fragment.runtime_id] == (None, 23)
    assert quality_by_runtime[wall_fragment.runtime_id] == (10, 5)

    batches = live_batches_from_fatbeans_capture(PACKAGE16_CAPTURE)
    first = next(batch for batch in batches if batch.sequence == 51)
    assert (None, 5, "23", 6) in {
        (item.local_index, item.quality, item.shape_key, item.cells)
        for item in first.grid_items
    }


@pytest.mark.skipif(
    not PACKAGE17_CAPTURE.exists(),
    reason="local Fatbeans package17 capture is not committed",
)
def test_fatbeans_package17_mirror_eye_full_outline_and_scroll_rows() -> None:
    batches = live_batches_from_fatbeans_capture(PACKAGE17_CAPTURE)
    second = next(batch for batch in batches if batch.sequence == 45)
    live_state = LiveSessionState()
    for batch in batches:
        live_state = apply_observation_batch(live_state, batch)
        if batch.sequence == second.sequence:
            break
    session = live_state_to_session_obs(live_state)
    updates = {update.path: update.value for update in second.field_updates}
    cells_by_quality = Counter()
    count_by_quality = Counter()
    local_quality_shapes = {
        (item.local_index, item.quality, item.shape_key, item.cells)
        for item in second.grid_items
    }
    for item in second.grid_items:
        count_by_quality[item.quality] += 1
        cells_by_quality[item.quality] += item.cells

    assert updates[("session", "warehouse_total_cells")] == 157
    assert updates[("session", "total_item_count")] == 50
    assert count_by_quality == {1: 6, 2: 12, 3: 10, 4: 16, 5: 5, 6: 1}
    assert cells_by_quality == {1: 10, 2: 23, 3: 24, 4: 70, 5: 26, 6: 4}
    assert (60, 5, "61", 6) in local_quality_shapes  # 满分斯诺克纪念球杆.
    assert (105, 4, "33", 9) in local_quality_shapes  # 赛车座椅.
    assert (140, 5, "23", 6) in local_quality_shapes  # 单兵水下推进器.
    assert (None, 4, "11", 1) in local_quality_shapes  # 智能手表 at local 0.
    assert session is not None
    assert session.visible_outline_bottom_row_min == 17


@pytest.mark.skipif(
    not PACKAGE17_CAPTURE.exists(),
    reason="local Fatbeans package17 capture is not committed",
)
def test_fatbeans_package17_layout_replay_scores_against_settlement() -> None:
    events = parse_fatbeans_capture(PACKAGE17_CAPTURE)
    final_truth = final_truth_from_events(events)
    stages = layout_replay_stages(events)

    assert final_truth is not None
    assert final_truth.total_cells == 157
    assert final_truth.total_items == 50
    assert [(stage.sort_id, stage.layout.total_cells) for stage in stages] == [
        (6, 66),
        (26, 66),
        (45, 157),
        (60, 157),
        (74, 157),
    ]
    assert stages[0].known_cell_ratio == pytest.approx(66 / 157)
    assert stages[2].known_cell_ratio == 1
    assert stages[2].bounding_cell_error == 13
    locked = estimate_warehouse_from_layout(
        stages[2].layout,
        final_total_cells=stages[2].final_total_cells,
    )
    assert locked.locked
    assert locked.p50_guess == 157


def test_treasure_value_action_result_adds_value_only_grid_marker() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=3,
                capture_time="",
                message_id=0x0025,
                session_id="s1",
                map_id=2403,
                round_index=4,
                action_results=(
                    FatbeansActionResult(
                        action_id=100163,
                        result=None,
                        result_field=None,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=30,
                                runtime_id=1425860544908193,
                                item_id=None,
                                quality=None,
                                value=43650,
                                shape_code=None,
                                cells=None,
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    batch = live_batches_from_fatbeans_events(events)[0]
    assert len(batch.grid_items) == 1
    assert batch.grid_items[0].local_index == 30
    assert batch.grid_items[0].value == 43650
    assert batch.grid_items[0].cells == 1
    assert batch.grid_items[0].shape_key == "11"


def test_bare_quality_reveal_uses_shaped_anchor_not_scan_cell() -> None:
    # Raven's full-quality skill scan reports a multi-cell item's quality at a
    # scanned cell (local 103 = top-right of a 4x4), not the footprint anchor.
    # The public-info reveal of the same runtime carries the true top-left
    # anchor (local 100) plus the shape, so the footprint must anchor at col 1.
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=33,
                capture_time="",
                message_id=0x0021,
                session_id="s1",
                map_id=2407,
                round_index=5,
                skill_reveals=(
                    FatbeansSkillReveal(
                        skill_id=100301,
                        hero_id=301,
                        round_index=5,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=103,
                                runtime_id=999,
                                item_id=None,
                                quality=6,
                                value=None,
                                shape_code=None,
                                cells=None,
                            ),
                        ),
                    ),
                ),
                public_infos=(
                    FatbeansPublicInfo(
                        info_id=200050,
                        map_id=2407,
                        value=None,
                        value_field=None,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=100,
                                runtime_id=999,
                                item_id=1086001,
                                quality=6,
                                value=None,
                                shape_code=44,
                                cells=16,
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    batch = live_batches_from_fatbeans_events(events)[0]
    q6 = next(it for it in batch.grid_items if it.runtime_id == 999)
    assert q6.local_index == 100
    assert q6.shape_key == "44"
    footprint = q6.footprint()
    assert footprint is not None
    assert footprint.col == 1


def test_fatbeans_ethan_r2_outline_skips_items_without_known_quality() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=10,
                capture_time="2026-06-12 21:00:00.000",
                message_id=0x0021,
                session_id="2401:1",
                map_id=2401,
                round_index=2,
                skill_reveals=(
                    FatbeansSkillReveal(
                        skill_id=1002082,
                        hero_id=208,
                        round_index=2,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=11,
                                runtime_id=101,
                                item_id=None,
                                quality=None,
                                value=None,
                                shape_code=11,
                                cells=1,
                            ),
                            FatbeansObservedItem(
                                local_index=12,
                                runtime_id=102,
                                item_id=None,
                                quality=None,
                                value=None,
                                shape_code=22,
                                cells=4,
                            ),
                        ),
                    ),
                ),
                action_results=(
                    FatbeansActionResult(
                        action_id=100134,
                        result=None,
                        result_field=None,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=12,
                                runtime_id=102,
                                item_id=None,
                                quality=4,
                                value=None,
                                shape_code=22,
                                cells=4,
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    batch = live_batches_from_fatbeans_events(events)[0]
    assert len(batch.grid_items) == 1
    assert batch.grid_items[0].runtime_id == 102
    assert batch.grid_items[0].quality == 4
    assert batch.grid_items[0].cells == 4
