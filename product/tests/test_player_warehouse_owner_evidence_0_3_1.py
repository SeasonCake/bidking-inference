from __future__ import annotations

import base64
import json
from pathlib import Path
import re
import sys
from dataclasses import asdict, replace

import pytest

import bidking_lab.live.fatbeans as fatbeans
from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.live.fatbeans import (
    FatbeansCaptureEvents,
    FatbeansInventoryItem,
    FatbeansPlayerBid,
    load_fatbeans_packets_from_rows,
    parse_fatbeans_packets,
)
from bidking_lab.live.monitor import (
    MonitorTables,
    build_monitor_artifact_from_events,
    load_monitor_tables,
)
from bidking_lab.live.sample_upload import trim_artifact

_SESSION = "2408:9000000000000032"
_OWNER_UID = 8_765_432_109_876_543
_OWNER_NAME = "宝主隐私负控"
_OWNER_FIELD_NAMES = (
    "send_auction_user_uid",
    "send_auction_user_name",
    "send_auction_user_head",
    "send_auction_head_box",
    "send_auction_user_title",
)


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
    return _varint(field_no << 3) + _varint(value)


def _field_bytes(field_no: int, value: bytes) -> bytes:
    return _varint((field_no << 3) | 2) + _varint(len(value)) + value


def _owner_fields(
    *,
    name: str = _OWNER_NAME,
    omit: frozenset[int] = frozenset(),
    extra: bytes = b"",
    reverse: bool = False,
) -> bytes:
    fields = [
        (15, _field_varint(15, _OWNER_UID)),
        (16, _field_bytes(16, name.encode("utf-8"))),
        (17, _field_varint(17, 120_004)),
        (18, _field_varint(18, 140_006)),
        (19, _field_varint(19, 150_028)),
    ]
    if reverse:
        fields.reverse()
    return b"".join(raw for field_no, raw in fields if field_no not in omit) + extra


def _rev_state_frame(
    *,
    session: str,
    message_id: int,
    owner_fields: bytes = b"",
    player_block: bytes = b"",
) -> bytes:
    payload = _field_bytes(1, session.encode("utf-8"))
    payload += _field_varint(2, 2408)
    payload += _field_varint(3, 5)
    if player_block:
        payload += _field_bytes(5, player_block)
    payload += owner_fields
    body = _field_bytes(2 if message_id == 0x002D else 1, payload)
    return (
        (16 + len(body)).to_bytes(4, "big")
        + b"\x32\xd2\x55\x60"
        + b"\x00\x00\x00\x00"
        + message_id.to_bytes(4, "big")
        + body
    )


def _send_bid_frame(*, session: str, value: int = 1) -> bytes:
    body = _field_varint(1, 1_285_603_971_496_202)
    body += _field_bytes(2, session.encode("utf-8"))
    body += _field_varint(3, value)
    return (
        (12 + len(body)).to_bytes(4, "big")
        + b"\x32\xd2\x55\x5f"
        + (0x0022).to_bytes(4, "big")
        + body
    )


def _row(payload: bytes, *, sort_id: int) -> dict[str, object]:
    return {
        "SortID": sort_id,
        "Direct": "REV",
        "Protocol": "Tcp",
        "SrcIP": "203.0.113.10",
        "SrcPort": 10000,
        "DstIP": "198.51.100.20",
        "DstPort": 60213,
        "CaptureTime": "2026-09-03 13:00:00.000",
        "Data": base64.b64encode(payload).decode("ascii"),
        "DataLength": len(payload),
    }


def _send_row(payload: bytes, *, sort_id: int) -> dict[str, object]:
    row = _row(payload, sort_id=sort_id)
    row["Direct"] = "SEND"
    row["SrcIP"], row["DstIP"] = row["DstIP"], row["SrcIP"]
    row["SrcPort"], row["DstPort"] = row["DstPort"], row["SrcPort"]
    return row


def _events(
    *,
    current_session: str | None = _SESSION,
    settlement_session: str = _SESSION,
    owner_fields: bytes | None = None,
) -> FatbeansCaptureEvents:
    rows: list[dict[str, object]] = []
    if current_session is not None:
        rows.append(
            _row(
                _rev_state_frame(session=current_session, message_id=0x0025),
                sort_id=1,
            )
        )
    rows.append(
        _row(
            _rev_state_frame(
                session=settlement_session,
                message_id=0x002D,
                owner_fields=_owner_fields() if owner_fields is None else owner_fields,
            ),
            sort_id=2,
        )
    )
    return parse_fatbeans_packets(load_fatbeans_packets_from_rows(rows))


def _settlement(events: FatbeansCaptureEvents):
    return next(state for state in events.states if state.message_id == 0x002D)


@pytest.mark.parametrize("name", ["123456", "宝主甲"])
def test_current_session_complete_owner_block_is_privacy_safe_and_stable(
    name: str,
) -> None:
    events = _events(owner_fields=_owner_fields(name=name, reverse=True))
    evidence = _settlement(events).settlement_auction_owner_evidence

    assert evidence.extension_present is True
    assert evidence.validation_status == "valid"
    assert evidence.fields_present == _OWNER_FIELD_NAMES
    assert evidence.metadata_shape == "uid_name_head_headbox_title"
    assert re.fullmatch(r"pw1-uid-[0-9a-f]{24}", evidence.owner_identity_alias or "")
    assert re.fullmatch(r"pw1-name-[0-9a-f]{24}", evidence.owner_display_alias or "")
    assert evidence.conflicts == ()

    repeated = _events(owner_fields=_owner_fields(name=name, reverse=True))
    repeated_evidence = _settlement(repeated).settlement_auction_owner_evidence
    assert repeated_evidence.owner_identity_alias == evidence.owner_identity_alias
    assert repeated_evidence.owner_display_alias == evidence.owner_display_alias

    serialized = json.dumps(asdict(evidence), ensure_ascii=False)
    assert str(_OWNER_UID) not in serialized
    assert name not in serialized


def test_identical_duplicate_and_unknown_sibling_remain_single_value_valid() -> None:
    evidence = _settlement(
        _events(
            owner_fields=_owner_fields(
                extra=_field_varint(15, _OWNER_UID) + _field_varint(99, 7)
            )
        )
    ).settlement_auction_owner_evidence

    assert evidence.validation_status == "valid"
    assert evidence.conflicts == ()


@pytest.mark.parametrize(
    ("owner_fields", "expected_conflict"),
    [
        (_owner_fields(omit=frozenset({19})), "partial_extension"),
        (
            _owner_fields(omit=frozenset({17}), extra=_field_bytes(17, b"\x01")),
            "wrong_wire_type",
        ),
        (_owner_fields(extra=_field_varint(18, 140_007)), "duplicate_conflict"),
        (
            _owner_fields(omit=frozenset({16}), extra=_field_bytes(16, b"")),
            "invalid_text",
        ),
        (
            _owner_fields(
                omit=frozenset({16}), extra=_field_bytes(16, b"\xff\xfe")
            ),
            "invalid_text",
        ),
        (
            _owner_fields(omit=frozenset({17}), extra=_field_varint(17, 0)),
            "invalid_value",
        ),
    ],
)
def test_partial_wrong_wire_empty_and_duplicate_conflict_fail_closed(
    owner_fields: bytes,
    expected_conflict: str,
) -> None:
    evidence = _settlement(
        _events(owner_fields=owner_fields)
    ).settlement_auction_owner_evidence

    assert evidence.extension_present is True
    assert evidence.validation_status == "malformed_conflict"
    assert expected_conflict in evidence.conflicts
    assert evidence.owner_identity_alias is None
    assert evidence.owner_display_alias is None
    assert evidence.metadata_shape is None


@pytest.mark.parametrize(
    ("events", "expected_conflict"),
    [
        (
            _events(current_session="2408:current", settlement_session="2408:stale"),
            "session_mismatch",
        ),
        (
            _events(current_session=None, settlement_session="2408:orphan"),
            "session_unverified",
        ),
    ],
)
def test_cross_session_and_orphan_settlement_owner_evidence_fail_closed(
    events: FatbeansCaptureEvents,
    expected_conflict: str,
) -> None:
    evidence = _settlement(events).settlement_auction_owner_evidence

    assert evidence.validation_status == "malformed_conflict"
    assert expected_conflict in evidence.conflicts
    assert evidence.owner_identity_alias is None
    assert evidence.owner_display_alias is None


def test_stale_settlement_does_not_replace_current_owner_session() -> None:
    rows = [
        _row(_rev_state_frame(session="2408:old", message_id=0x0025), sort_id=1),
        _row(_rev_state_frame(session="2408:new", message_id=0x0025), sort_id=2),
        _row(
            _rev_state_frame(
                session="2408:old",
                message_id=0x002D,
                owner_fields=_owner_fields(),
            ),
            sort_id=3,
        ),
        _row(
            _rev_state_frame(
                session="2408:new",
                message_id=0x002D,
                owner_fields=_owner_fields(),
            ),
            sort_id=4,
        ),
    ]

    settlements = [
        state
        for state in parse_fatbeans_packets(
            load_fatbeans_packets_from_rows(rows)
        ).states
        if state.message_id == 0x002D
    ]

    assert settlements[0].settlement_auction_owner_evidence.validation_status == (
        "malformed_conflict"
    )
    assert "session_mismatch" in (
        settlements[0].settlement_auction_owner_evidence.conflicts
    )
    assert settlements[1].settlement_auction_owner_evidence.validation_status == (
        "valid"
    )


@pytest.mark.parametrize(
    "settlement_order",
    [
        ("2408:late", "2408:current"),
        ("2408:current", "2408:late"),
    ],
)
def test_late_send_never_replaces_confirmed_owner_session(
    settlement_order: tuple[str, str],
) -> None:
    rows = [
        _row(
            _rev_state_frame(session="2408:current", message_id=0x0025),
            sort_id=1,
        ),
        _send_row(_send_bid_frame(session="2408:late"), sort_id=2),
        *[
            _row(
                _rev_state_frame(
                    session=session,
                    message_id=0x002D,
                    owner_fields=_owner_fields(),
                ),
                sort_id=3 + index,
            )
            for index, session in enumerate(settlement_order)
        ],
    ]

    settlements = {
        state.session_id: state.settlement_auction_owner_evidence
        for state in parse_fatbeans_packets(
            load_fatbeans_packets_from_rows(rows)
        ).states
        if state.message_id == 0x002D
    }

    assert settlements["2408:current"].validation_status == "valid"
    assert settlements["2408:late"].validation_status == "malformed_conflict"
    assert "session_mismatch" in settlements["2408:late"].conflicts


def test_send_candidate_is_allowed_only_until_state_confirms_a_session() -> None:
    candidate_only_rows = [
        _send_row(_send_bid_frame(session="2408:candidate"), sort_id=1),
        _send_row(_send_bid_frame(session="2408:candidate"), sort_id=2),
        _row(
            _rev_state_frame(
                session="2408:candidate",
                message_id=0x002D,
                owner_fields=_owner_fields(),
            ),
            sort_id=3,
        ),
    ]
    candidate_evidence = _settlement(
        parse_fatbeans_packets(
            load_fatbeans_packets_from_rows(candidate_only_rows)
        )
    ).settlement_auction_owner_evidence
    assert candidate_evidence.validation_status == "valid"

    replacement_rows = [
        _row(_rev_state_frame(session="2408:old", message_id=0x0025), sort_id=1),
        _send_row(_send_bid_frame(session="2408:new"), sort_id=2),
        _row(_rev_state_frame(session="2408:new", message_id=0x0025), sort_id=3),
        _row(
            _rev_state_frame(
                session="2408:new",
                message_id=0x002D,
                owner_fields=_owner_fields(),
            ),
            sort_id=4,
        ),
        _row(
            _rev_state_frame(
                session="2408:old",
                message_id=0x002D,
                owner_fields=_owner_fields(),
            ),
            sort_id=5,
        ),
    ]
    replacement_settlements = [
        state
        for state in parse_fatbeans_packets(
            load_fatbeans_packets_from_rows(replacement_rows)
        ).states
        if state.message_id == 0x002D
    ]
    assert (
        replacement_settlements[0].settlement_auction_owner_evidence.validation_status
        == "valid"
    )
    assert (
        replacement_settlements[1].settlement_auction_owner_evidence.validation_status
        == "malformed_conflict"
    )
    assert "session_mismatch" in (
        replacement_settlements[1].settlement_auction_owner_evidence.conflicts
    )


def test_conflicting_send_candidates_without_state_fail_closed() -> None:
    rows = [
        _send_row(_send_bid_frame(session="2408:first"), sort_id=1),
        _send_row(_send_bid_frame(session="2408:second"), sort_id=2),
        _row(
            _rev_state_frame(
                session="2408:first",
                message_id=0x002D,
                owner_fields=_owner_fields(),
            ),
            sort_id=3,
        ),
        _row(
            _rev_state_frame(
                session="2408:second",
                message_id=0x002D,
                owner_fields=_owner_fields(),
            ),
            sort_id=4,
        ),
        _row(_rev_state_frame(session="2408:confirmed", message_id=0x0025), sort_id=5),
        _row(
            _rev_state_frame(
                session="2408:confirmed",
                message_id=0x002D,
                owner_fields=_owner_fields(),
            ),
            sort_id=6,
        ),
    ]

    settlements = [
        state
        for state in parse_fatbeans_packets(
            load_fatbeans_packets_from_rows(rows)
        ).states
        if state.message_id == 0x002D
    ]

    assert all(
        state.settlement_auction_owner_evidence.validation_status
        == "malformed_conflict"
        for state in settlements[:2]
    )
    assert all(
        "session_candidate_conflict"
        in state.settlement_auction_owner_evidence.conflicts
        for state in settlements[:2]
    )
    assert (
        settlements[2].settlement_auction_owner_evidence.validation_status == "valid"
    )


def test_user_log_or_non_settlement_owner_shaped_fields_do_not_create_evidence() -> None:
    player_block = (
        _field_varint(1, 1_234_567_890)
        + _field_bytes(2, b"player-name-canary")
        + _field_varint(3, 103)
    )
    rows = [
        _row(
            _rev_state_frame(
                session=_SESSION,
                message_id=0x0025,
                owner_fields=_owner_fields(),
                player_block=player_block,
            ),
            sort_id=1,
        ),
        _row(
            _rev_state_frame(session=_SESSION, message_id=0x002D),
            sort_id=2,
        ),
    ]

    events = parse_fatbeans_packets(load_fatbeans_packets_from_rows(rows))

    assert all(
        state.settlement_auction_owner_evidence.validation_status == "absent"
        for state in events.states
    )


def _monitor_tables() -> MonitorTables:
    item = Item(
        item_id=1001,
        name="test_item",
        description="",
        name_key="test_item",
        desc_key="test_item_desc",
        quality=4,
        quality_color="purple",
        value=20_000,
        shape_w=2,
        shape_h=2,
        tags=[107],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )
    return MonitorTables(
        maps={
            2408: BidMap(
                map_id=2408,
                name="test_open_map",
                description="",
                category=101,
                auction_mode="open",
                sub_pool_weights=[],
                rounds_total=5,
                entry_fee_silver=0,
                starting_budget_silver=100_000,
                drop_pool_id=9001,
                items_per_session_min=1,
                items_per_session_max=1,
                value_tier_ui="",
                mode_flag=4,
                bid_price_ladder=[],
                raw_row=[],
            )
        },
        drops={
            9001: DropPool(
                pool_id=9001,
                name="pool",
                description="",
                pool_type=2,
                entries=(
                    DropEntry(
                        category=107,
                        item_id=item.item_id,
                        n_min=1,
                        n_max=1,
                        weight=1,
                    ),
                ),
            )
        },
        items={item.item_id: item},
    )


def _product_events(*, owner_fields: bytes) -> FatbeansCaptureEvents:
    events = _events(owner_fields=owner_fields)
    settlement = _settlement(events)
    settlement = replace(
        settlement,
        bids=(
            FatbeansPlayerBid(
                player_id=1,
                name="leader",
                hero_id=103,
                values=(12_000, 15_000),
            ),
        ),
        inventory_items=(
            FatbeansInventoryItem(
                runtime_id=101,
                item_id=1001,
                quality=4,
                cells=4,
                local_index=14,
            ),
        ),
    )
    return replace(
        events,
        states=tuple(
            settlement if state.message_id == 0x002D else state
            for state in events.states
        ),
    )


def _retarget_events(
    events: FatbeansCaptureEvents,
    *,
    map_id: int,
    session_id: str,
) -> FatbeansCaptureEvents:
    return replace(
        events,
        states=tuple(
            replace(state, map_id=map_id, session_id=session_id)
            for state in events.states
        ),
    )


def _stable_artifact(
    events: FatbeansCaptureEvents,
    *,
    tables: MonitorTables | None = None,
) -> dict[str, object]:
    artifact = build_monitor_artifact_from_events(
        events,
        file="pw1-noop.json",
        tables=tables or _monitor_tables(),
        n_trials=8,
        roi_trials=0,
        shadow_trials=0,
        run_debug_shadows=False,
        seed=20260728,
        formal_mode="v2",
    )

    def drop_volatile_times(value):
        if isinstance(value, dict):
            return {
                key: drop_volatile_times(item)
                for key, item in value.items()
                if key
                not in {
                    "created_at",
                    "processing_seconds",
                    "monitor_processing_seconds",
                    "ts",
                }
            }
        if isinstance(value, list):
            return [drop_volatile_times(item) for item in value]
        if isinstance(value, tuple):
            return tuple(drop_volatile_times(item) for item in value)
        return value

    artifact = drop_volatile_times(artifact)
    return artifact


def test_owner_evidence_is_additive_to_monitor_api_upload_and_settlement(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(fatbeans, "LOG_SETTLEMENT_FRAMES", True)
    monkeypatch.setattr(fatbeans, "LOG_RAW_SETTLEMENT_FRAMES", False)
    with_owner_events = _product_events(owner_fields=_owner_fields())
    log_output = capsys.readouterr().out
    without_owner_events = _product_events(owner_fields=b"")

    with_owner = _stable_artifact(with_owner_events)
    without_owner = _stable_artifact(without_owner_events)

    source_only_keys = {
        "official_distribution_eligible",
        "ui_contract",
        "warehouse_source",
        "warehouse_source_generation",
        "warehouse_source_qualification",
    }
    assert {
        key: value
        for key, value in with_owner.items()
        if key not in source_only_keys
    } == {
        key: value
        for key, value in without_owner.items()
        if key not in source_only_keys
    }
    assert with_owner["warehouse_source_qualification"] == (
        "postupdate_unknown_warehouse_source"
    )
    assert with_owner["official_distribution_eligible"] is False
    assert with_owner["inventory_count"] == 1
    assert with_owner["inventory_cells"] == 4
    assert with_owner["known_value_sum"] == 20_000

    artifact_text = json.dumps(with_owner, ensure_ascii=False, sort_keys=True)
    upload_text = json.dumps(
        trim_artifact(with_owner) or {}, ensure_ascii=False, sort_keys=True
    )
    for text in (log_output, artifact_text, upload_text):
        assert str(_OWNER_UID) not in text
        assert _OWNER_NAME not in text
    assert "settlement_auction_owner_evidence" not in artifact_text
    assert "send_auction_user_" not in artifact_text


def test_pw2_real_table_open_owner_block_qualifies_without_calculation_changes() -> None:
    tables = load_monitor_tables()
    with_owner = _stable_artifact(
        _product_events(owner_fields=_owner_fields()),
        tables=tables,
    )
    without_owner = _stable_artifact(
        _product_events(owner_fields=b""),
        tables=tables,
    )

    assert with_owner["warehouse_source_qualification"] == (
        "player_custom_warehouse"
    )
    assert with_owner["official_distribution_eligible"] is False
    assert with_owner["warehouse_source"]["reason"] == (
        "pw2_open_valid_owner_block"
    )
    assert with_owner["warehouse_source"]["auction_mode"] == "open"
    assert with_owner["warehouse_source"]["quarantine_required"] is False

    assert without_owner["warehouse_source_qualification"] == (
        "postupdate_unknown_warehouse_source"
    )
    assert without_owner["warehouse_source"]["reason"] == (
        "pw2_open_owner_block_absent"
    )

    source_only_keys = {
        "official_distribution_eligible",
        "ui_contract",
        "warehouse_source",
        "warehouse_source_generation",
        "warehouse_source_qualification",
    }
    assert {
        key: value
        for key, value in with_owner.items()
        if key not in source_only_keys
    } == {
        key: value
        for key, value in without_owner.items()
        if key not in source_only_keys
    }

    serialized = json.dumps(with_owner, ensure_ascii=False, sort_keys=True)
    assert str(_OWNER_UID) not in serialized
    assert _OWNER_NAME not in serialized
    assert "owner_identity_alias" not in serialized
    assert "owner_display_alias" not in serialized


@pytest.mark.parametrize("map_id", (4401, 4501, 5601))
def test_pw2_real_table_sealed_owner_absence_is_official(map_id: int) -> None:
    events = _retarget_events(
        _product_events(owner_fields=b""),
        map_id=map_id,
        session_id=f"{map_id}:9000000000000032",
    )
    artifact = _stable_artifact(events, tables=load_monitor_tables())

    assert artifact["warehouse_source_qualification"] == (
        "official_random_warehouse"
    )
    assert artifact["official_distribution_eligible"] is True
    assert artifact["warehouse_source"]["auction_mode"] == "sealed"
    assert artifact["warehouse_source"]["reason"] == (
        "pw2_sealed_owner_block_absent"
    )
    assert artifact["warehouse_source"]["quarantine_required"] is False


def test_pw2_real_table_sealed_owner_block_is_conflict_and_calculation_noop() -> None:
    tables = load_monitor_tables()
    with_owner_events = _retarget_events(
        _product_events(owner_fields=_owner_fields()),
        map_id=4501,
        session_id="4501:9000000000000032",
    )
    without_owner_events = _retarget_events(
        _product_events(owner_fields=b""),
        map_id=4501,
        session_id="4501:9000000000000032",
    )
    with_owner = _stable_artifact(with_owner_events, tables=tables)
    without_owner = _stable_artifact(without_owner_events, tables=tables)

    assert with_owner["warehouse_source_qualification"] == (
        "postupdate_unknown_warehouse_source"
    )
    assert with_owner["official_distribution_eligible"] is False
    assert with_owner["warehouse_source"]["reason"] == (
        "pw2_sealed_owner_block_conflict"
    )
    assert with_owner["warehouse_source"]["source_conflict"] is True
    assert with_owner["warehouse_source"]["quarantine_required"] is True

    source_only_keys = {
        "official_distribution_eligible",
        "ui_contract",
        "warehouse_source",
        "warehouse_source_generation",
        "warehouse_source_qualification",
    }
    assert {
        key: value
        for key, value in with_owner.items()
        if key not in source_only_keys
    } == {
        key: value
        for key, value in without_owner.items()
        if key not in source_only_keys
    }


def test_pw2_old_session_owner_block_cannot_qualify_current_open_session() -> None:
    events = _events(
        current_session="2408:current",
        settlement_session="2408:old",
        owner_fields=_owner_fields(),
    )
    artifact = _stable_artifact(events, tables=load_monitor_tables())

    assert artifact["session_id"] == "2408:current"
    assert artifact["warehouse_source_qualification"] == (
        "postupdate_unknown_warehouse_source"
    )
    assert artifact["official_distribution_eligible"] is False
    assert artifact["warehouse_source"]["owner_evidence_status"] == (
        "malformed_conflict"
    )
    assert artifact["warehouse_source"]["reason"] == (
        "pw2_owner_evidence_conflict"
    )
    assert artifact["warehouse_source"]["source_conflict"] is True
    assert artifact["warehouse_source"]["quarantine_required"] is True


def test_pw2_real_monitor_trim_and_collector_rejects_forged_qualification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import sample_collector_server as collector

    def prepare_out(name: str) -> Path:
        out = tmp_path / name / "samples.jsonl"
        monkeypatch.setattr(collector, "_OUT", out)
        monkeypatch.setattr(collector, "_SEEN", set())
        monkeypatch.setattr(collector, "_QUAR_SEEN", set())
        monkeypatch.setattr(collector, "_MAP_COUNT", type(collector._MAP_COUNT)())
        return out

    open_absent = _stable_artifact(
        _product_events(owner_fields=b""),
        tables=load_monitor_tables(),
    )
    accepted_record = trim_artifact(open_absent)
    assert accepted_record is not None
    assert (
        accepted_record["warehouse_source_schema"]
        == "player_warehouse_source_pw2_68171c_v6"
    )
    normal = prepare_out("accepted")
    assert collector._accept_one(accepted_record) == "accepted"  # noqa: SLF001
    assert normal.exists()

    open_valid = _stable_artifact(
        _product_events(owner_fields=_owner_fields()),
        tables=load_monitor_tables(),
    )
    forged_record = trim_artifact(open_valid)
    assert forged_record is not None
    forged_record["warehouse_source_qualification"] = "official_random_warehouse"
    forged_record["official_distribution_eligible"] = True
    quarantine = prepare_out("quarantined")
    assert collector._accept_one(forged_record) == "quarantined"  # noqa: SLF001
    assert not quarantine.exists()
    stored = json.loads(
        quarantine.with_name("samples_quarantine.jsonl").read_text(
            encoding="utf-8"
        )
    )
    assert stored["server_quarantine_reason"] == "warehouse_source_pending"

    generation_forged = dict(accepted_record)
    generation_forged.update(
        {
            "session_hash": "pw2-real-generation-forgery",
            "warehouse_source_generation": "preupdate_official_random",
            "warehouse_source_qualification": "official_random_warehouse",
            "official_distribution_eligible": True,
            "warehouse_source_reason": "preupdate_official_random_generation",
        }
    )
    quarantine = prepare_out("generation-forgery")
    assert collector._accept_one(generation_forged) == "quarantined"  # noqa: SLF001
    assert not quarantine.exists()

    trigger_stripped = dict(accepted_record)
    trigger_stripped["session_hash"] = "pw2-real-trigger-strip"
    for key in (
        "warehouse_map_source_contract_sha256",
        "warehouse_expected_map_source_contract_sha256",
        "warehouse_auction_mode",
        "warehouse_owner_evidence_status",
        "warehouse_source_conflict",
        "warehouse_quarantine_required",
    ):
        trigger_stripped.pop(key)
    trigger_stripped.update(
        {
            "warehouse_source_qualification": "official_random_warehouse",
            "official_distribution_eligible": True,
            "warehouse_source_reason": "client-forged-official",
        }
    )
    quarantine = prepare_out("trigger-strip")
    assert collector._accept_one(trigger_stripped) == "quarantined"  # noqa: SLF001
    assert not quarantine.exists()
