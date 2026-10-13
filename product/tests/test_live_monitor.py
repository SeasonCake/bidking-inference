from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import bidking_lab.live.monitor as monitor_module
from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.live.fatbeans import (
    FatbeansActionResult,
    FatbeansCaptureEvents,
    FatbeansInventoryItem,
    FatbeansObservedItem,
    FatbeansPlayerBid,
    FatbeansPublicInfo,
    FatbeansSendEvent,
    FatbeansSkillReveal,
    FatbeansStateEvent,
)
from bidking_lab.live.monitor import (
    DEFAULT_Q6_SHADOW_TRIALS_CAP,
    MonitorTables,
    _build_zero_match_fallback_rows,
    _inventory_quality_breakdown,
    _inventory_totals,
    _model_eval_row,
    _resolve_shadow_trials,
    _settlement_inventory_items,
    build_monitor_artifact_from_file,
    build_monitor_artifact_from_events,
    load_monitor_tables,
    write_monitor_logs,
)
from bidking_lab.live.warehouse_source import (
    DISABLE_PLAYER_CUSTOM_WAREHOUSE_QUALIFICATION_ENV,
    OFFICIAL_RANDOM_WAREHOUSE,
    POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
)
from bidking_lab.live.types import (
    FieldUpdate,
    GridItemObservation,
    LiveObservationBatch,
)
from bidking_lab.inference.observation import QualityBucketObs, SessionObs
from bidking_lab.inference.q6_residual import (
    aisha_q6_quality_only_deep_local_risk,
    q6_quality_only_local_diagnostics,
    q6_residual_prior_floor_ratio_for_profile,
)
from bidking_lab.inference.v2 import LayoutFeasibility, ResidualProblem


def test_action_round_advances_after_completed_state() -> None:
    start = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=1,
                capture_time="",
                message_id=0x0021,
                session_id="2401:1",
                map_id=2401,
                round_index=None,
            ),
        ),
    )
    after_round_one = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            *start.states,
            FatbeansStateEvent(
                sort_id=2,
                capture_time="",
                message_id=0x0025,
                session_id="2401:1",
                map_id=2401,
                round_index=1,
            ),
        ),
    )
    settled = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            *after_round_one.states,
            FatbeansStateEvent(
                sort_id=3,
                capture_time="",
                message_id=0x002D,
                session_id="2401:1",
                map_id=2401,
                round_index=3,
                inventory_items=(
                    FatbeansInventoryItem(
                        runtime_id=1,
                        item_id=1001,
                        quality=4,
                        cells=4,
                    ),
                ),
            ),
        ),
    )

    assert monitor_module._action_round(start) == 1
    assert monitor_module._action_round(after_round_one) == 2
    assert monitor_module._action_round(settled) == 3
    assert monitor_module._latest_round(after_round_one) == 1


def test_settlement_inventory_items_merges_multiple_0x002d_chunks() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=1,
                capture_time="",
                message_id=0x002D,
                session_id="2502:1",
                map_id=2502,
                round_index=3,
                inventory_items=(
                    FatbeansInventoryItem(
                        runtime_id=10,
                        item_id=1_001_001,
                        quality=4,
                        cells=3,
                    ),
                ),
            ),
            FatbeansStateEvent(
                sort_id=2,
                capture_time="",
                message_id=0x002D,
                session_id="2502:1",
                map_id=2502,
                round_index=3,
                inventory_items=(
                    FatbeansInventoryItem(
                        runtime_id=11,
                        item_id=1_001_002,
                        quality=5,
                        cells=4,
                    ),
                ),
            ),
        ),
    )

    merged = _settlement_inventory_items(events)
    assert len(merged) == 2
    inventory_count, inventory_cells = _inventory_totals(events)
    assert inventory_count == 2
    assert inventory_cells == 7


def test_action_round_uses_sent_bid_count_when_settlement_round_lags() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(
            FatbeansSendEvent(
                sort_id=5,
                capture_time="",
                message_id=0x0022,
                session_id="2409:1",
                value=780_000,
            ),
            FatbeansSendEvent(
                sort_id=12,
                capture_time="",
                message_id=0x0022,
                session_id="2409:1",
                value=780_000,
            ),
            FatbeansSendEvent(
                sort_id=22,
                capture_time="",
                message_id=0x0022,
                session_id="2409:1",
                value=780_000,
            ),
        ),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=24,
                capture_time="",
                message_id=0x002D,
                session_id="2409:1",
                map_id=2409,
                round_index=2,
                inventory_items=(
                    FatbeansInventoryItem(
                        runtime_id=1,
                        item_id=1001,
                        quality=4,
                        cells=4,
                    ),
                ),
            ),
        ),
    )

    assert monitor_module._action_round(events) == 3


def test_action_round_infers_roundless_bidding_state_from_bid_count() -> None:
    first_prebid = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=3,
                capture_time="",
                message_id=0x0027,
                session_id="2401:1",
                map_id=2401,
                round_index=None,
                action_results=(
                    FatbeansActionResult(
                        action_id=100129,
                        result=4,
                        result_field=14,
                        observed_items=(),
                    ),
                ),
            ),
        ),
    )
    third_prebid = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(
            FatbeansSendEvent(
                sort_id=5,
                capture_time="",
                message_id=0x0022,
                session_id="2401:1",
                value=100_000,
            ),
            FatbeansSendEvent(
                sort_id=10,
                capture_time="",
                message_id=0x0022,
                session_id="2401:1",
                value=120_000,
            ),
        ),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=12,
                capture_time="",
                message_id=0x0027,
                session_id="2401:1",
                map_id=2401,
                round_index=None,
            ),
        ),
    )

    assert monitor_module._action_round(first_prebid) == 1
    assert monitor_module._action_round(third_prebid) == 3


def _item() -> Item:
    return Item(
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


def test_action_result_rows_include_revealed_item_details() -> None:
    rows = monitor_module._action_result_rows(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=9,
                    capture_time="2026-06-03 00:00:00.000",
                    message_id=0x0027,
                    session_id="2501:1",
                    map_id=2501,
                    round_index=3,
                    action_results=(
                        FatbeansActionResult(
                            action_id=100136,
                            result=12,
                            result_field=14,
                            observed_items=(
                                FatbeansObservedItem(
                                    local_index=14,
                                    runtime_id=101,
                                    item_id=None,
                                    quality=2,
                                    value=None,
                                    shape_code=None,
                                    cells=None,
                                ),
                                FatbeansObservedItem(
                                    local_index=76,
                                    runtime_id=None,
                                    item_id=None,
                                    quality=3,
                                    value=None,
                                    shape_code=None,
                                    cells=None,
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        {100136: SimpleNamespace(name="宝光四鉴")},
    )

    assert rows[0]["revealed_items"] == 2
    assert rows[0]["revealed_items_detail"][0]["local_index"] == 14
    assert rows[0]["revealed_items_detail"][0]["quality"] == 2
    assert rows[0]["revealed_items_detail"][1]["local_index"] == 76
    assert rows[0]["revealed_items_detail"][1]["quality"] == 3


def test_action_result_rows_ignore_settlement_replay_results() -> None:
    rows = monitor_module._action_result_rows(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=10,
                    capture_time="2026-06-08 17:16:32.000",
                    message_id=0x0027,
                    session_id="2409:1",
                    map_id=2409,
                    round_index=1,
                    action_results=(
                        FatbeansActionResult(
                            action_id=100104,
                            result=17,
                            result_field=14,
                            observed_items=(),
                        ),
                    ),
                ),
                FatbeansStateEvent(
                    sort_id=18,
                    capture_time="2026-06-08 17:17:01.000",
                    message_id=0x0027,
                    session_id="2409:1",
                    map_id=2409,
                    round_index=2,
                    action_results=(
                        FatbeansActionResult(
                            action_id=100110,
                            result=1.8888888359069824,
                            result_field=14,
                            observed_items=(),
                        ),
                    ),
                ),
                FatbeansStateEvent(
                    sort_id=24,
                    capture_time="2026-06-08 17:18:39.000",
                    message_id=0x002D,
                    session_id="2409:1",
                    map_id=2409,
                    round_index=2,
                    inventory_items=(
                        FatbeansInventoryItem(
                            runtime_id=1,
                            item_id=1001,
                            quality=4,
                            cells=4,
                        ),
                    ),
                    action_results=(
                        FatbeansActionResult(
                            action_id=100104,
                            result=17,
                            result_field=14,
                            observed_items=(),
                        ),
                        FatbeansActionResult(
                            action_id=100110,
                            result=1.8888888359069824,
                            result_field=14,
                            observed_items=(),
                        ),
                    ),
                ),
            ),
        ),
        {
            100104: SimpleNamespace(name="普品扫描"),
            100110: SimpleNamespace(name="普品均格"),
        },
    )

    assert [row["action_id"] for row in rows] == [100110, 100104]
    assert rows[0]["sort"] == 18
    assert rows[0]["tool"] == "普品均格"


def test_action_result_rows_prefer_direct_result_over_state_replay() -> None:
    rows = monitor_module._action_result_rows(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=10,
                    capture_time="2026-06-08 17:37:05.000",
                    message_id=0x0027,
                    session_id="2401:1",
                    map_id=2401,
                    round_index=1,
                    action_results=(
                        FatbeansActionResult(
                            action_id=100104,
                            result=8,
                            result_field=14,
                            observed_items=(),
                        ),
                    ),
                ),
                FatbeansStateEvent(
                    sort_id=19,
                    capture_time="2026-06-08 17:37:34.000",
                    message_id=0x0027,
                    session_id="2401:1",
                    map_id=2401,
                    round_index=2,
                    action_results=(
                        FatbeansActionResult(
                            action_id=100106,
                            result=41,
                            result_field=14,
                            observed_items=(),
                        ),
                    ),
                ),
                FatbeansStateEvent(
                    sort_id=24,
                    capture_time="2026-06-08 17:38:05.000",
                    message_id=0x0025,
                    session_id="2401:1",
                    map_id=2401,
                    round_index=3,
                    action_results=(
                        FatbeansActionResult(
                            action_id=100104,
                            result=8,
                            result_field=14,
                            observed_items=(),
                        ),
                        FatbeansActionResult(
                            action_id=100106,
                            result=41,
                            result_field=14,
                            observed_items=(),
                        ),
                    ),
                ),
            ),
        ),
        {
            100104: SimpleNamespace(name="普品扫描"),
            100106: SimpleNamespace(name="优品扫描"),
        },
    )

    assert [row["action_id"] for row in rows] == [100106, 100104]
    assert rows[0]["sort"] == 19
    assert rows[0]["tool"] == "优品扫描"


def test_action_result_rows_do_not_infer_zero_without_target_or_empty_ack() -> None:
    rows = monitor_module._action_result_rows(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(
                FatbeansSendEvent(
                    sort_id=12,
                    capture_time="2026-06-09 12:05:41.529",
                    message_id=0x0026,
                    session_id="2404:1",
                    value=100113,
                ),
            ),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=17,
                    capture_time="2026-06-09 12:06:01.000",
                    message_id=0x0025,
                    session_id="2404:1",
                    map_id=2404,
                    round_index=3,
                    action_results=(),
                ),
            ),
        ),
        {
            100113: SimpleNamespace(name="极品均格"),
        },
    )

    assert rows == []


def test_action_result_rows_do_not_infer_zero_from_unrelated_later_state() -> None:
    rows = monitor_module._action_result_rows(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(
                FatbeansSendEvent(
                    sort_id=12,
                    capture_time="2026-06-09 12:05:41.529",
                    message_id=0x0026,
                    session_id="2404:2",
                    value=100113,
                ),
            ),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=5,
                    capture_time="2026-06-09 12:01:01.000",
                    message_id=0x0027,
                    session_id="2404:1",
                    map_id=2404,
                    round_index=2,
                    action_results=(
                        FatbeansActionResult(
                            action_id=100113,
                            result=3.0,
                            result_field=11,
                            observed_items=(),
                        ),
                    ),
                ),
                FatbeansStateEvent(
                    sort_id=17,
                    capture_time="2026-06-09 12:06:01.000",
                    message_id=0x0025,
                    session_id="2404:2",
                    map_id=2404,
                    round_index=3,
                    action_results=(),
                ),
            ),
        ),
        {
            100113: SimpleNamespace(name="极品均格"),
        },
        session_id="2404:2",
    )

    assert rows == []


def test_action_result_rows_keep_empty_result_without_valid_target_session() -> None:
    rows = monitor_module._action_result_rows(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(
                FatbeansSendEvent(
                    sort_id=12,
                    capture_time="2026-06-09 12:05:41.529",
                    message_id=0x0026,
                    session_id="2404:1",
                    value=100113,
                ),
            ),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=13,
                    capture_time="2026-06-09 12:05:42.000",
                    message_id=0x0027,
                    session_id="2404:1",
                    map_id=2404,
                    round_index=3,
                    action_results=(
                        FatbeansActionResult(
                            action_id=100113,
                            result=None,
                            result_field=None,
                            observed_items=(),
                        ),
                    ),
                ),
                FatbeansStateEvent(
                    sort_id=17,
                    capture_time="2026-06-09 12:06:01.000",
                    message_id=0x0025,
                    session_id="2404:1",
                    map_id=2404,
                    round_index=3,
                    action_results=(),
                ),
            ),
        ),
        {
            100113: SimpleNamespace(name="极品均格"),
        },
    )

    assert rows[0]["action_id"] == 100113
    assert rows[0]["result"] is None
    assert "inferred_zero" not in rows[0]


def test_action_result_rows_do_not_infer_zero_before_later_state() -> None:
    rows = monitor_module._action_result_rows(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(
                FatbeansSendEvent(
                    sort_id=12,
                    capture_time="2026-06-09 12:05:41.529",
                    message_id=0x0026,
                    session_id="2404:1",
                    value=100113,
                ),
            ),
            statuses=(),
            states=(),
        ),
        {
            100113: SimpleNamespace(name="极品均格"),
        },
    )

    assert rows == []


def test_public_info_rows_include_revealed_item_details() -> None:
    rows = monitor_module._public_info_rows(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=11,
                    capture_time="2026-06-04 03:44:38.710",
                    message_id=0x0025,
                    session_id="2401:1",
                    map_id=2401,
                    round_index=1,
                    public_infos=(
                        FatbeansPublicInfo(
                            info_id=200027,
                            map_id=2401,
                            value=6,
                            value_field=6,
                            observed_items=(
                                FatbeansObservedItem(
                                    local_index=42,
                                    runtime_id=501,
                                    item_id=None,
                                    quality=4,
                                    value=None,
                                    shape_code=None,
                                    cells=None,
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        {},
    )

    assert rows[0]["info_id"] == 200027
    assert rows[0]["revealed_items"] == 1
    assert rows[0]["revealed_items_detail"][0]["local_index"] == 42
    assert rows[0]["revealed_items_detail"][0]["quality"] == 4
    assert rows[0]["revealed_summary"] == "Q4x1 / pos 42"


def test_skill_reveal_rows_include_maria_skill_payload() -> None:
    rows = monitor_module._skill_reveal_rows(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=1,
                    capture_time="2026-06-12 19:37:36.488",
                    message_id=0x0021,
                    session_id="2406:1",
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
                            ),
                        ),
                    ),
                ),
            ),
        ),
        {},
    )

    assert len(rows) == 2
    by_skill = {int(row["skill_id"]): row for row in rows}
    assert by_skill[100108]["result"] == 37063
    assert by_skill[100108]["tool"] == "玛丽亚·总价"
    assert by_skill[10010801]["tool"] == "玛丽亚·品质"
    assert by_skill[10010801]["revealed_items_detail"][0]["quality"] == 3


def test_skill_reveal_rows_label_ethan_and_aisha_skills() -> None:
    rows = monitor_module._skill_reveal_rows(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=1,
                    capture_time="2026-06-12 20:00:00.000",
                    message_id=0x0021,
                    session_id="2401:1",
                    map_id=2401,
                    round_index=3,
                    skill_reveals=(
                        FatbeansSkillReveal(
                            skill_id=1002082,
                            hero_id=208,
                            round_index=3,
                        ),
                        FatbeansSkillReveal(
                            skill_id=1001032,
                            hero_id=103,
                            round_index=3,
                        ),
                    ),
                ),
            ),
        ),
        {},
    )

    by_skill = {(int(row["hero_id"]), int(row["skill_id"])): row for row in rows}
    assert by_skill[(208, 1002082)]["tool"] == "伊森·R2 已知品质轮廓"
    assert by_skill[(103, 1001032)]["tool"] == "艾莎·R3 蓝品轮廓"


def test_r1_missing_wire_round_uses_skill_identity_not_later_state_round() -> None:
    rows = monitor_module._skill_reveal_rows(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=9,
                    capture_time="2026-08-26 21:30:00.000",
                    message_id=0x0021,
                    session_id="5603:safe",
                    map_id=5603,
                    round_index=4,
                    skill_reveals=(
                        FatbeansSkillReveal(
                            skill_id=1001034,
                            hero_id=103,
                            round_index=None,
                        ),
                        FatbeansSkillReveal(
                            skill_id=1001041,
                            hero_id=104,
                            round_index=None,
                        ),
                        FatbeansSkillReveal(
                            skill_id=1999999,
                            hero_id=999,
                            round_index=None,
                        ),
                    ),
                ),
            ),
        ),
        {},
    )
    by_skill = {int(row["skill_id"]): row for row in rows}
    for skill_id in (1001034, 1001041):
        assert by_skill[skill_id]["round_index"] == 0
        assert by_skill[skill_id]["round_index_state"] == "skill_canonical"
        assert "round_index_wire" not in by_skill[skill_id]
    assert by_skill[1999999]["round_index"] is None
    assert by_skill[1999999]["round_index_state"] == "unavailable"


@pytest.mark.parametrize("hero_id,skill_id", [(103, 1001034), (104, 1001041)])
def test_r1_explicit_wire_round_conflict_fails_closed(
    hero_id: int,
    skill_id: int,
) -> None:
    def row_for(wire_round_index: object) -> dict[str, object]:
        rows = monitor_module._skill_reveal_rows(
            FatbeansCaptureEvents(
                packets=(),
                frames=(),
                sends=(),
                statuses=(),
                states=(
                    FatbeansStateEvent(
                        sort_id=9,
                        capture_time="",
                        message_id=0x0021,
                        session_id="safe",
                        map_id=5603,
                        round_index=4,
                        skill_reveals=(
                            FatbeansSkillReveal(
                                skill_id=skill_id,
                                hero_id=hero_id,
                                round_index=wire_round_index,
                            ),
                        ),
                    ),
                ),
            ),
            {},
        )
        return rows[0]

    accepted = row_for(0)
    assert accepted["round_index"] == 0
    assert accepted["round_index_state"] == "wire"

    conflict = row_for(4)
    assert conflict["round_index"] is None
    assert conflict["round_index_state"] == "conflict"
    assert conflict["round_index_wire"] == 4
    assert monitor_module._skill_reveals_for_ref([conflict])[0][
        "round_index"
    ] is None

    malformed = row_for(True)
    assert malformed["round_index"] is None
    assert malformed["round_index_state"] == "conflict"
    assert "round_index_wire" not in malformed


def test_public_info_rows_attach_item_names_to_revealed_details() -> None:
    rows = monitor_module._public_info_rows(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(),
            statuses=(),
            states=(
                FatbeansStateEvent(
                    sort_id=11,
                    capture_time="2026-06-04 03:44:38.710",
                    message_id=0x0025,
                    session_id="2401:1",
                    map_id=2401,
                    round_index=1,
                    public_infos=(
                        FatbeansPublicInfo(
                            info_id=200027,
                            map_id=2401,
                            value=6,
                            value_field=6,
                            observed_items=(
                                FatbeansObservedItem(
                                    local_index=42,
                                    runtime_id=501,
                                    item_id=1001,
                                    quality=4,
                                    value=20000,
                                    shape_code=22,
                                    cells=4,
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        _tables().items,
    )

    detail = rows[0]["revealed_items_detail"][0]
    assert detail["item_id"] == 1001
    assert detail["item_name"] == "test_item"
    assert detail["shape_code"] == 22
    assert "test_item" in rows[0]["revealed_summary"]


def test_q6_quality_only_local_diagnostics_is_review_only() -> None:
    store = SimpleNamespace(
        items=lambda: (
            SimpleNamespace(quality=6, cells=None, local_index=142),
            SimpleNamespace(quality=6, cells=16, local_index=130),
            SimpleNamespace(quality=5, cells=None, local_index=160),
        )
    )

    diagnostics = q6_quality_only_local_diagnostics(store)

    assert diagnostics == {
        "count": 1,
        "deepest_local_index": 142,
        "deepest_start_row": 15,
    }
    assert aisha_q6_quality_only_deep_local_risk(
        hero="aisha",
        map_family="shipwreck",
        evidence_profile_key="shape+layout",
        deepest_start_row=diagnostics["deepest_start_row"],
    )
    assert not aisha_q6_quality_only_deep_local_risk(
        hero="aisha",
        map_family="villa",
        evidence_profile_key="shape+layout",
        deepest_start_row=diagnostics["deepest_start_row"],
    )


def test_ethan_villa_random_avg_prior_floor_gate_is_narrow() -> None:
    assert (
        q6_residual_prior_floor_ratio_for_profile(
            hero="ethan",
            map_family="villa",
            evidence_profile_key="public:random_avg+layout",
            requested_ratio=1.0,
            gate="ethan_villa_random_avg_v1",
        )
        == 1.0
    )
    assert (
        q6_residual_prior_floor_ratio_for_profile(
            hero="ethan",
            map_family="villa",
            evidence_profile_key="layout",
            requested_ratio=1.0,
            gate="ethan_villa_random_avg_v1",
        )
        == 0.0
    )
    assert (
        q6_residual_prior_floor_ratio_for_profile(
            hero="aisha",
            map_family="villa",
            evidence_profile_key="public:random_avg+layout",
            requested_ratio=1.0,
            gate="ethan_villa_random_avg_v1",
        )
        == 0.0
    )


def test_conditional_target_shadow_summary_counts_as_active() -> None:
    summary = monitor_module._q6_residual_boost_shadow_summary(
        None,
        label="ethan_shipwreck_layout_conditional_c4_cells15",
        requested_boost=1.0,
        active_boost=1.0,
        gate="ethan_shipwreck_layout_v1",
        evidence_profile_key="layout",
        trials=10,
        requested_conditional_target_count=4.0,
        active_conditional_target_count=4.0,
        requested_conditional_target_cells=15.0,
        active_conditional_target_cells=15.0,
    )

    assert summary["active"] is True
    assert summary["active_conditional_target_count"] == 4.0
    assert summary["active_conditional_target_cells"] == 15.0


def _tables() -> MonitorTables:
    item = _item()
    return MonitorTables(
        maps={
            2401: BidMap(
                map_id=2401,
                name="test_map",
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
            ),
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
            ),
        },
        items={item.item_id: item},
    )


def _activity_alias_tables() -> MonitorTables:
    tables = _tables()
    maps = dict(tables.maps)
    maps[2507] = BidMap(
        map_id=2507,
        name="activity_alias_shipwreck",
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
    return MonitorTables(maps=maps, drops=tables.drops, items=tables.items)


def test_minimap_table_shape_requires_cell_count_match() -> None:
    rows = monitor_module._minimap_grid_items(
        (
            LiveObservationBatch(
                source="packet",
                phase="settled",
                grid_items=(
                    GridItemObservation(
                        cells=3,
                        source="packet",
                        confidence="exact",
                        item_id=1001,
                        quality=4,
                        local_index=14,
                    ),
                ),
            ),
        ),
        _tables().items,
    )

    assert rows[0]["item_name"] == "test_item"
    assert rows[0]["shape_key"] is None
    assert rows[0]["row"] is None
    assert rows[0]["col"] is None


def test_ahmad_ref_inputs_bridge_keeps_pre_settlement_fields_only() -> None:
    bridge = monitor_module._ahmad_ref_inputs_from_batches(
        (
            LiveObservationBatch(
                source="packet",
                event_kind="tool_revealed",
                phase="bidding",
                sequence=8,
                field_updates=(
                    FieldUpdate(
                        path=("session", "total_item_count"),
                        value=24,
                        source="packet",
                        confidence="exact",
                    ),
                    FieldUpdate(
                        path=("bucket", "5", "avg_cells"),
                        value=3.2,
                        source="packet",
                        confidence="exact",
                    ),
                ),
            ),
            LiveObservationBatch(
                source="packet",
                event_kind="session_settled",
                phase="settled",
                sequence=30,
                field_updates=(
                    FieldUpdate(
                        path=("bucket", "6", "count"),
                        value=9,
                        source="packet",
                        confidence="exact",
                    ),
                ),
            ),
        ),
        hero="ahmed",
    )

    assert bridge["total_count"] == 24
    assert bridge["avg_cells"] == {"q5": 3.2}
    assert bridge["counts"] == {}
    assert all(row["phase"] != "settled" for row in bridge["field_updates"])
    assert monitor_module._ahmad_ref_inputs_from_batches((), hero="ethan") == {}


@pytest.mark.parametrize(
    ("hero", "canonical"),
    (
        ("gabriela", "gabriela"),
        ("加布里埃拉", "gabriela"),
        ("raven", "raven"),
        ("拉文", "raven"),
    ),
)
def test_structured_ref_inputs_bridge_supports_raven_and_gabriela(
    hero: str, canonical: str
) -> None:
    bridge = monitor_module._ahmad_ref_inputs_from_batches(
        (
            LiveObservationBatch(
                source="packet",
                event_kind="public_info",
                phase="bidding",
                sequence=7,
                field_updates=(
                    FieldUpdate(
                        path=("session", "total_item_count"),
                        value=27,
                        source="packet",
                        confidence="exact",
                    ),
                    FieldUpdate(
                        path=("bucket", "6", "count"),
                        value=2,
                        source="packet",
                        confidence="exact",
                    ),
                ),
            ),
        ),
        hero=hero,
    )

    assert monitor_module._normalize_structured_ref_hero(hero) == canonical
    assert bridge["hero"] == canonical
    assert bridge["total_count"] == 27
    assert bridge["counts"] == {"q6": 2}


def test_ahmad_ref_inputs_bridge_captures_reveal_footprints() -> None:
    """桥补【揭示 footprint】(纯几何)供 draw/揭示英雄回放可见锚/footprint。取 pre-settled 揭示态,
    跳过 settlement 全真相; items=None(无目录)时不崩返回空。"""
    batches = (
        LiveObservationBatch(
            source="packet",
            event_kind="tool_revealed",
            phase="bidding",
            sequence=8,
            grid_items=(
                GridItemObservation(
                    cells=2,
                    source="packet",
                    confidence="exact",
                    item_id=1001,
                    quality=6,
                    local_index=83,
                    shape_key="12",
                ),
            ),
        ),
        LiveObservationBatch(
            source="packet",
            event_kind="session_settled",
            phase="settled",
            sequence=30,
            grid_items=(
                GridItemObservation(
                    cells=4,
                    source="packet",
                    confidence="exact",
                    item_id=1002,
                    quality=5,
                    local_index=5,
                    shape_key="22",
                ),
            ),
        ),
    )
    bridge = monitor_module._ahmad_ref_inputs_from_batches(
        batches, hero="aisha", items=_tables().items
    )
    mi = bridge["minimap_items"]
    assert isinstance(mi, list) and len(mi) == 1  # 只取 pre-settled, settled 全真相被跳过
    row = mi[0]
    assert row["quality"] == 6 and row["item_id"] == 1001
    assert {"quality", "cells", "row", "col", "local_index", "width", "height"}.issubset(row.keys())
    assert row["row"] == 9 and row["col"] == 4  # local_index 83 -> row=83//10+1, col=83%10+1
    # 无目录 -> 不崩, 返回空(footprint 需目录增强)
    empty = monitor_module._ahmad_ref_inputs_from_batches(batches, hero="aisha", items=None)
    assert empty["minimap_items"] == []


def test_ahmad_ref_inputs_bridge_keeps_quality_value_fields() -> None:
    bridge = monitor_module._ahmad_ref_inputs_from_batches(
        (
            LiveObservationBatch(
                source="packet",
                event_kind="tool_revealed",
                phase="bidding",
                sequence=11,
                field_updates=(
                    FieldUpdate(
                        path=("bucket", "5", "avg_value"),
                        value=34288.75,
                        source="packet",
                        confidence="exact",
                    ),
                    FieldUpdate(
                        path=("bucket", "5", "value_sum"),
                        value=137155,
                        source="packet",
                        confidence="exact",
                    ),
                ),
            ),
        ),
        hero="ahmed",
    )

    assert bridge["avg_values"] == {"q5": 34288.75}
    assert bridge["quality_values"] == {"q5": 137155}
    assert any(row["path"] == ["bucket", "5", "value_sum"] for row in bridge["field_updates"])


def test_structured_ref_inputs_bridge_supports_aisha_split_low_quality() -> None:
    bridge = monitor_module._ahmad_ref_inputs_from_batches(
        (
            LiveObservationBatch(
                source="packet",
                event_kind="tool_revealed",
                phase="bidding",
                sequence=4,
                field_updates=(
                    FieldUpdate(
                        path=("bucket", "1", "total_cells"),
                        value=5,
                        source="packet",
                        confidence="exact",
                    ),
                    FieldUpdate(
                        path=("bucket", "1", "count"),
                        value=3,
                        source="packet",
                        confidence="exact",
                    ),
                    FieldUpdate(
                        path=("bucket_split", "white", "total_cells"),
                        value=5,
                        source="packet",
                        confidence="exact",
                    ),
                    FieldUpdate(
                        path=("bucket_split", "white", "count"),
                        value=3,
                        source="packet",
                        confidence="exact",
                    ),
                    FieldUpdate(
                        path=("bucket_split", "green", "total_cells"),
                        value=8,
                        source="packet",
                        confidence="exact",
                    ),
                    FieldUpdate(
                        path=("bucket_split", "green", "count"),
                        value=4,
                        source="packet",
                        confidence="exact",
                    ),
                    FieldUpdate(
                        path=("bucket_split", "green", "value_sum"),
                        value=2400,
                        source="packet",
                        confidence="exact",
                    ),
                    FieldUpdate(
                        path=("bucket", "3", "total_cells"),
                        value=12,
                        source="packet",
                        confidence="exact",
                    ),
                    FieldUpdate(
                        path=("bucket", "3", "count"),
                        value=6,
                        source="packet",
                        confidence="exact",
                    ),
                ),
            ),
        ),
        hero="aisha",
    )

    assert bridge["split_quality_cells"] == {"green": 8, "white": 5}
    assert bridge["split_counts"] == {"green": 4, "white": 3}
    assert bridge["quality_value_floors"] == {"q1": 2400}
    assert bridge["quality_cells"] == {"q3": 12}
    assert bridge["counts"] == {"q3": 6}
    assert "q1" not in bridge["quality_cells"]
    assert "q1" not in bridge["counts"]
    assert any(row["path"] == ["bucket_split", "white", "count"] for row in bridge["field_updates"])
    assert any(
        row["path"] == ["bucket_split", "green", "value_sum"]
        for row in bridge["field_updates"]
    )


def test_structured_ref_inputs_bridge_supports_victor_count_sum() -> None:
    bridge = monitor_module._ahmad_ref_inputs_from_batches(
        (
            LiveObservationBatch(
                source="packet",
                event_kind="tool_revealed",
                phase="bidding",
                sequence=4,
                field_updates=(
                    FieldUpdate(
                        path=("bucket_group", "q4q5q6", "count"),
                        value=8,
                        source="packet",
                        confidence="exact",
                    ),
                ),
            ),
        ),
        hero="victor",
    )

    assert bridge["count_sums"] == {"q4q5q6": 8}
    assert bridge["field_updates"][0]["path"] == [
        "bucket_group",
        "q4q5q6",
        "count",
    ]


def _two_item_tables() -> MonitorTables:
    first = Item(
        item_id=1001,
        name="test_item",
        description="",
        name_key="test_item",
        desc_key="test_item_desc",
        quality=4,
        quality_color="purple",
        value=20_000,
        shape_w=1,
        shape_h=1,
        tags=[107],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )
    second = Item(
        item_id=1002,
        name="test_item_2",
        description="",
        name_key="test_item_2",
        desc_key="test_item_2_desc",
        quality=4,
        quality_color="purple",
        value=10_000,
        shape_w=1,
        shape_h=1,
        tags=[107],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )
    return MonitorTables(
        maps={
            2401: BidMap(
                map_id=2401,
                name="two_item_map",
                description="",
                category=101,
                auction_mode="open",
                sub_pool_weights=[],
                rounds_total=5,
                entry_fee_silver=0,
                starting_budget_silver=100_000,
                drop_pool_id=9001,
                items_per_session_min=1,
                items_per_session_max=2,
                value_tier_ui="",
                mode_flag=4,
                bid_price_ladder=[],
                raw_row=[],
            ),
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
                        item_id=first.item_id,
                        n_min=1,
                        n_max=1,
                        weight=1,
                    ),
                    DropEntry(
                        category=107,
                        item_id=second.item_id,
                        n_min=1,
                        n_max=1,
                        weight=1,
                    ),
                ),
            ),
        },
        items={first.item_id: first, second.item_id: second},
    )


def _events() -> FatbeansCaptureEvents:
    return FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=1,
                capture_time="",
                message_id=0x002D,
                session_id="s1",
                map_id=2401,
                round_index=5,
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
                settlement_parse_residual=0,
            ),
        ),
    )


def _warehouse_source_tables(name: str) -> MonitorTables:
    tables = _tables()
    maps = dict(tables.maps)
    maps[2401] = maps[2401].model_copy(update={"name": name})
    return MonitorTables(maps=maps, drops=tables.drops, items=tables.items)


def _events_at(capture_time: str) -> FatbeansCaptureEvents:
    events = _events()
    return replace(
        events,
        states=(replace(events.states[0], capture_time=capture_time),),
    )


def test_monitor_artifact_keeps_historical_unknown_name_official() -> None:
    artifact = build_monitor_artifact_from_events(
        _events_at("2026-07-19 12:00:00.000"),
        file="historical_2401.json",
        tables=_warehouse_source_tables("未知别墅"),
        n_trials=10,
        roi_trials=0,
    )

    assert artifact["warehouse_source_qualification"] == OFFICIAL_RANDOM_WAREHOUSE
    assert artifact["official_distribution_eligible"] is True
    assert artifact["warehouse_source"]["reason"] == (
        "preupdate_official_random_generation"
    )


def test_monitor_artifact_fails_closed_on_stale_postupdate_table_without_quote_clamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tables = _warehouse_source_tables("未知别墅")
    events = _events_at("2026-07-23 12:00:00.000")
    monkeypatch.delenv(
        DISABLE_PLAYER_CUSTOM_WAREHOUSE_QUALIFICATION_ENV,
        raising=False,
    )
    qualified = build_monitor_artifact_from_events(
        events,
        file="postupdate_2401.json",
        tables=tables,
        n_trials=10,
        roi_trials=0,
    )
    monkeypatch.setenv(
        DISABLE_PLAYER_CUSTOM_WAREHOUSE_QUALIFICATION_ENV,
        "1",
    )
    rollback = build_monitor_artifact_from_events(
        events,
        file="postupdate_2401.json",
        tables=tables,
        n_trials=10,
        roi_trials=0,
    )

    assert qualified["warehouse_source_qualification"] == (
        POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    )
    assert qualified["official_distribution_eligible"] is False
    assert qualified["warehouse_source"]["reason"] == "pw2_map_contract_untrusted"
    assert qualified["warehouse_source"]["quarantine_required"] is True
    assert qualified["warehouse_source"]["table_status"] == "stale"
    assert qualified["warehouse_source"]["map_table_sha256"]
    assert qualified["warehouse_source"]["expected_map_table_sha256"]
    assert rollback["warehouse_source_qualification"] == OFFICIAL_RANDOM_WAREHOUSE
    assert rollback["official_distribution_eligible"] is True
    assert qualified["bid_rows"] == rollback["bid_rows"]
    assert qualified["balanced"] == rollback["balanced"]


def test_live_v3_shadow_marks_unknown_252x_as_activity_prior_unavailable() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=1,
                capture_time="",
                message_id=0x0025,
                session_id="2526:activity",
                map_id=2526,
                round_index=1,
            ),
        ),
    )

    shadow = monitor_module._v3_posterior_shadow_summary(
        events,
        map_id=2526,
        hero="aisha",
        tables=_tables(),
        trials=10,
        seed=0,
    )

    assert shadow["error"] == "unknown_map_id"
    assert shadow["v3_prior_available"] is False
    assert shadow["v3_prior_error"] == "KeyError"
    assert shadow["v3_robust_status"] == "prior_unavailable"
    assert shadow["v3_robust_prior_usable"] is False
    assert shadow["v3_robust_prior_trusted"] is False
    assert shadow["v3_robust_activity_candidate"] is True
    assert shadow["v3_robust_fallback_mode"] == "missing_prior_truth_only"
    assert "activity_map_id_candidate" in shadow["v3_robust_reasons"]
    assert shadow["v3_robust_affects_bid"] is False


def test_build_monitor_artifact_does_not_crash_on_unknown_map() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=1,
                capture_time="",
                message_id=0x0025,
                session_id="2527:activity",
                map_id=2527,
                round_index=1,
                bids=(
                    FatbeansPlayerBid(
                        player_id=1,
                        name="leader",
                        hero_id=103,
                        values=(12_000,),
                    ),
                ),
            ),
        ),
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="unknown_map.json",
        tables=_tables(),
        n_trials=10,
        roi_trials=0,
        formal_mode="v3_practical",
    )

    assert artifact["map_id"] == 2527
    assert artifact["evidence_label"] == "unsupported_map:2527"
    assert artifact["formal_mode_reason"] == "unsupported_map"
    assert artifact["formal_mode"] == "v2"
    assert artifact["inference_input_constraints"] == {
        "mode": "unsupported_map",
        "map_id": 2527,
    }
    assert artifact["bid_rows"] == []
    assert artifact["v3_practical_bid_rows"] == []


def test_build_monitor_artifact_uses_activity_shipwreck_alias() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=1,
                capture_time="",
                message_id=0x0025,
                session_id="2527:activity",
                map_id=2527,
                round_index=1,
                bids=(
                    FatbeansPlayerBid(
                        player_id=1,
                        name="leader",
                        hero_id=103,
                        values=(12_000,),
                    ),
                ),
            ),
        ),
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="activity_map.json",
        tables=_activity_alias_tables(),
        n_trials=10,
        roi_trials=0,
        formal_mode="v3_practical",
    )

    assert artifact["map_id"] == 2527
    assert artifact["model_map_id"] == 2507
    assert artifact["map_alias_mode"] == "activity_shipwreck_minus20"
    assert artifact["map_alias"]["source_map_id"] == 2527
    assert artifact["map_alias"]["model_map_id"] == 2507
    assert "unsupported_map" not in artifact["formal_mode_reason"]
    assert artifact["inference_input_constraints"]["source_map_id"] == 2527
    assert artifact["inference_input_constraints"]["model_map_id"] == 2507
    assert artifact["inference_input_constraints"]["map_alias"]["mode"] == (
        "activity_shipwreck_minus20"
    )
    assert artifact["bid_rows"]

    contract = artifact["ui_contract"]
    assert contract["context"]["map_id"] == 2527
    assert contract["context"]["model_map_id"] == 2507
    assert contract["constraints"]["summary"]["map_alias_label"] == (
        "活动图 2527->旧沉船 2507"
    )


def _shipwreck_base_map(map_id: int, drop_pool_id: int) -> BidMap:
    return BidMap(
        map_id=map_id,
        name=f"base_{map_id}",
        description="",
        category=101,
        auction_mode="open",
        sub_pool_weights=[],
        rounds_total=5,
        entry_fee_silver=0,
        starting_budget_silver=100_000,
        drop_pool_id=drop_pool_id,
        items_per_session_min=1,
        items_per_session_max=1,
        value_tier_ui="",
        mode_flag=4,
        bid_price_ladder=[],
        raw_row=[],
    )


def test_build_monitor_artifact_uses_red_crate_activity_alias() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=1,
                capture_time="",
                message_id=0x0025,
                session_id="2545:red-crate-activity",
                map_id=2545,
                round_index=1,
                bids=(
                    FatbeansPlayerBid(
                        player_id=1,
                        name="leader",
                        hero_id=103,
                        values=(12_000,),
                    ),
                ),
            ),
        ),
    )
    tables = _tables()
    maps = dict(tables.maps)
    maps[2505] = _shipwreck_base_map(2505, 9001)

    artifact = build_monitor_artifact_from_events(
        events,
        file="red_crate_activity_map.json",
        tables=MonitorTables(maps=maps, drops=tables.drops, items=tables.items),
        n_trials=10,
        roi_trials=0,
        formal_mode="v3_practical",
    )

    assert artifact["map_id"] == 2545
    assert artifact["model_map_id"] == 2505
    assert artifact["map_alias_mode"] == "red_crate_shipwreck_minus40"
    assert "unsupported_map" not in artifact["formal_mode_reason"]
    assert artifact["bid_rows"]
    assert artifact["ui_contract"]["constraints"]["summary"]["map_alias_label"] == (
        "红箱活动图 2545→旧沉船 2505"
    )


def test_activity_shipwreck_alias_covers_new_activity_blocks() -> None:
    # Activity maps postdate the 06-11 tables. Each ten-map block preserves the base-map
    # position, so it aliases to 2501-2510 / 4501-4510 without changing the source id.
    tables = _tables()
    maps = dict(tables.maps)
    maps[2501] = _shipwreck_base_map(2501, 9001)
    maps[2505] = _shipwreck_base_map(2505, 9001)
    maps[4502] = _shipwreck_base_map(4502, 9001)
    maps[4505] = _shipwreck_base_map(4505, 9001)
    drops = tables.drops

    a2531 = monitor_module._activity_shipwreck_alias(2531, maps, drops)
    assert a2531["model_map_id"] == 2501
    assert a2531["mode"] == "activity_shipwreck_minus30"

    a4532 = monitor_module._activity_shipwreck_alias(4532, maps, drops)
    assert a4532["model_map_id"] == 4502
    assert a4532["mode"] == "activity_shipwreck_45xx_minus30"

    # 2026-07-16 red-crate block is another +40 positional copy. The monitor borrows only
    # the old map/drop model while preserving source_map_id; the reference engine uses that
    # source id to apply the independently calibrated red-crate q6 prior.
    a2545 = monitor_module._activity_shipwreck_alias(2545, maps, drops)
    assert a2545["model_map_id"] == 2505
    assert a2545["mode"] == "red_crate_shipwreck_minus40"

    a4545 = monitor_module._activity_shipwreck_alias(4545, maps, drops)
    assert a4545["model_map_id"] == 4505
    assert a4545["mode"] == "red_crate_shipwreck_45xx_minus40"

    # Out of the activity blocks -> no alias (stays unsupported until a table ships it).
    assert monitor_module._activity_shipwreck_alias(2551, maps, drops) == {}
    assert monitor_module._activity_shipwreck_alias(4551, maps, drops) == {}


def test_build_monitor_artifact_aliases_activity_bidmap_with_missing_drop() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=1,
                capture_time="",
                message_id=0x0025,
                session_id="2527:activity",
                map_id=2527,
                round_index=1,
                bids=(
                    FatbeansPlayerBid(
                        player_id=1,
                        name="leader",
                        hero_id=103,
                        values=(12_000,),
                    ),
                ),
            ),
        ),
    )
    tables = _activity_alias_tables()
    maps = dict(tables.maps)
    maps[2527] = BidMap(
        map_id=2527,
        name="activity_shipwreck_drop_missing",
        description="",
        category=105,
        auction_mode="open",
        sub_pool_weights=[],
        rounds_total=5,
        entry_fee_silver=0,
        starting_budget_silver=100_000,
        drop_pool_id=2527,
        items_per_session_min=1,
        items_per_session_max=1,
        value_tier_ui="",
        mode_flag=4,
        bid_price_ladder=[],
        raw_row=[],
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="activity_drop_missing.json",
        tables=MonitorTables(maps=maps, drops=tables.drops, items=tables.items),
        n_trials=10,
        roi_trials=0,
        formal_mode="v3_practical",
    )

    assert artifact["map_id"] == 2527
    assert artifact["model_map_id"] == 2507
    assert artifact["map_alias"]["reason"] == (
        "missing_activity_drop_use_corresponding_old_shipwreck"
    )
    assert artifact["inference_input_constraints"]["map_alias_reason"] == (
        "missing_activity_drop_use_corresponding_old_shipwreck"
    )
    assert artifact["bid_rows"]


def test_build_monitor_artifact_includes_panel_and_eval() -> None:
    artifact = build_monitor_artifact_from_events(
        _events(),
        file="sample.json",
        tables=_tables(),
        n_trials=10,
        roi_trials=0,
    )

    assert artifact["file"] == "sample.json"
    assert artifact["n_trials"] == 10
    assert artifact["roi_trials"] == 0
    assert artifact["shadow_trials"] == 10
    assert artifact["processing_seconds"] >= 0
    assert artifact["session_id"] == "s1"
    assert artifact["hero"] == "aisha"
    assert artifact["map_id"] == 2401
    assert artifact["known_value_sum"] == 20_000
    assert artifact["final_q6_count"] == 0
    assert artifact["inference_input_constraints"]["mode"] == "session_totals_stripped"
    assert "total_item_count" not in artifact["inference_input_constraints"]
    assert artifact["latest_bids"] == {"leader": 15_000}
    assert artifact["warehouse_rows"]
    assert artifact["v2_posterior_rows"]
    assert artifact["v3_posterior_shadow"]["label"] == "v3_summary_likelihood"
    assert artifact["v3_posterior_shadow"]["trials"] == 10
    assert artifact["v3_posterior_shadow"]["v3_post_available"] is True
    assert artifact["v3_posterior_shadow"]["v3_post_affects_bid"] is False
    assert artifact["v3_posterior_shadow"]["v3_post_ready"] is True
    assert artifact["v3_posterior_shadow"]["v3_prior_available"] is True
    assert artifact["v3_posterior_shadow"]["v3_prior_error"] is None
    assert artifact["v3_posterior_shadow"]["v3_robust_available"] is True
    assert artifact["v3_posterior_shadow"]["v3_robust_affects_bid"] is False
    assert artifact["v3_posterior_shadow"]["v3_robust_prior_usable"] is True
    assert artifact["v3_posterior_shadow"]["v3_robust_activity_candidate"] is False
    assert artifact["v3_posterior_shadow"]["v3_ccv_available"] is True
    assert artifact["v3_posterior_shadow"]["v3_ccv_affects_bid"] is False
    assert artifact["v3_posterior_shadow"]["v3_resid_available"] is True
    assert artifact["v3_posterior_shadow"]["v3_resid_affects_bid"] is False
    assert artifact["v3_posterior_shadow"]["v3_resid_gate_available"] is True
    assert artifact["v3_posterior_shadow"]["v3_resid_gate_affects_bid"] is False
    assert artifact["v3_posterior_shadow"]["v3_under_available"] is True
    assert artifact["v3_posterior_shadow"]["v3_under_affects_bid"] is False
    assert artifact["v3_posterior_shadow"]["v3_under_active"] is False
    assert artifact["v3_posterior_shadow"]["v3_under_candidate"] is False
    assert artifact["v3_posterior_shadow"]["v3_tail_review_available"] is True
    assert artifact["v3_posterior_shadow"]["v3_tail_review_affects_bid"] is False
    assert artifact["v3_posterior_shadow"]["v3_tail_review_active"] is False
    assert artifact["v3_posterior_shadow"]["v3_fv_available"] is True
    assert artifact["v3_posterior_shadow"]["v3_fv_affects_bid"] is False
    assert artifact["v3_posterior_shadow"]["v3_fv_active"] is False
    assert artifact["v3_posterior_shadow"]["v3_fv_candidate"] is False
    assert artifact["v3_posterior_shadow"]["v3_fv_status"] == "baseline_passthrough"
    assert artifact["v3_posterior_shadow"]["v3_scp_available"] is True
    assert artifact["v3_posterior_shadow"]["v3_scp_affects_bid"] is False
    assert artifact["v3_posterior_shadow"]["v3_scp_active"] is False
    assert artifact["v3_posterior_shadow"]["v3_scp_status"] == (
        "observed_exceeds_table_caps_shadow_only"
    )
    assert artifact["v3_posterior_shadow"]["v3_cse_available"] is True
    assert artifact["v3_posterior_shadow"]["v3_cse_affects_bid"] is False
    assert artifact["v3_posterior_shadow"]["v3_cse_active"] is False
    assert "v3_cse_pressure_candidate" in artifact["v3_posterior_shadow"]
    assert "v3_cse_source_context_classes" in artifact["v3_posterior_shadow"]
    assert artifact["v3_posterior_shadow"]["v3_practical_available"] is True
    assert artifact["v3_posterior_shadow"]["v3_practical_affects_bid"] is False
    assert artifact["v3_posterior_shadow"]["v3_practical_active"] is False
    assert "v3_practical_status" in artifact["v3_posterior_shadow"]
    assert "v3_practical_formal_decision_value_p50" in artifact[
        "v3_posterior_shadow"
    ]
    assert artifact["q6_residual_boost_shadow"]["label"] == "profile_b5"
    assert artifact["q6_residual_boost_shadow"]["gate"] == "shipwreck_profile_v1"
    assert artifact["q6_residual_boost_shadow"]["trials"] == 10
    assert artifact["q6_residual_boost_shadow"]["active"] is False
    assert artifact["q6_residual_deep_floor_shadow"]["label"] == "aisha_deep_floor1"
    assert (
        artifact["q6_residual_deep_floor_shadow"]["gate"]
        == "aisha_shipwreck_deep_v1"
    )
    assert artifact["q6_residual_deep_floor_shadow"]["trials"] == 10
    assert artifact["q6_residual_deep_floor_shadow"]["active"] is False
    assert (
        artifact["q6_residual_deep11_floor_shadow"]["label"]
        == "aisha_deep11_floor1"
    )
    assert (
        artifact["q6_residual_deep11_floor_shadow"]["gate"]
        == "aisha_shipwreck_deep11_v1"
    )
    assert artifact["q6_residual_deep11_floor_shadow"]["trials"] == 10
    assert artifact["q6_residual_deep11_floor_shadow"]["active"] is False
    assert (
        artifact["q6_residual_hidden_floor_shadow"]["label"]
        == "aisha_hidden_floor15"
    )
    assert artifact["q6_residual_hidden_floor_shadow"]["gate"] == "aisha_hidden_v1"
    assert artifact["q6_residual_hidden_floor_shadow"]["trials"] == 10
    assert artifact["q6_residual_hidden_floor_shadow"]["active"] is False
    assert artifact["q6_residual_villa_floor_shadow"]["label"] == (
        "aisha_villa_floor05"
    )
    assert artifact["q6_residual_villa_floor_shadow"]["gate"] == (
        "aisha_villa_shape_layout_v1"
    )
    assert artifact["q6_residual_villa_floor_shadow"]["trials"] == 10
    assert artifact["q6_residual_villa_floor_shadow"]["active"] is False
    assert (
        artifact["q6_residual_ethan_villa_random_floor_shadow"]["label"]
        == "ethan_villa_random_avg_floor1"
    )
    assert (
        artifact["q6_residual_ethan_villa_random_floor_shadow"]["gate"]
        == "ethan_villa_random_avg_v1"
    )
    assert artifact["q6_residual_ethan_villa_random_floor_shadow"]["trials"] == 10
    assert artifact["q6_residual_ethan_villa_random_floor_shadow"]["active"] is False
    assert (
        artifact["q6_residual_ethan_shipwreck_layout_conditional_shadow"]["label"]
        == "ethan_shipwreck_layout_conditional_c4_cells15"
    )
    assert (
        artifact["q6_residual_ethan_shipwreck_layout_conditional_shadow"]["gate"]
        == "ethan_shipwreck_layout_v1"
    )
    assert (
        artifact["q6_residual_ethan_shipwreck_layout_conditional_shadow"]["trials"]
        == 10
    )
    assert (
        artifact["q6_residual_ethan_shipwreck_layout_conditional_shadow"]["active"]
        is False
    )
    assert [row["策略"] for row in artifact["q6_residual_boost_shadow_rows"]] == [
        "profile_b5",
        "aisha_deep_floor1",
        "aisha_deep11_floor1",
        "aisha_hidden_floor15",
        "aisha_villa_floor05",
        "ethan_villa_random_avg_floor1",
        "ethan_shipwreck_layout_conditional_c4_cells15",
    ]
    assert artifact["q6_residual_boost_shadow_rows"]
    assert "q6先验缺口" in artifact["v2_posterior_rows"][0]
    assert "q6先验风险参考" in artifact["v2_posterior_rows"][0]
    assert "q6先验风险" in artifact["v2_posterior_rows"][0]
    assert "q6实战门控" in artifact["v2_posterior_rows"][0]
    assert "q6实战参考P90" in artifact["v2_posterior_rows"][0]
    assert "category_grid_items" in artifact
    assert "minimap_grid_items" in artifact
    assert artifact["minimap_grid_items"][0]["item_name"] == "test_item"
    assert artifact["minimap_grid_items"][0]["layout_source"] == "settlement_inventory"
    assert artifact["minimap_grid_items"][0]["row"] == 2
    assert artifact["minimap_grid_items"][0]["col"] == 5
    assert artifact["minimap_grid_items"][0]["shape_key"] == "22"
    assert artifact["ui_contract"]["minimap"]["layout_source"] == "settlement_inventory"
    assert artifact["ui_contract"]["minimap"]["layout_complete"] is True
    assert artifact["ui_contract"]["minimap"]["drawable_items"] == 1
    assert artifact["bid_rows"]
    assert artifact["bid_rows"][0]["价值口径"] == "decision_value"
    assert artifact["formal_mode"] == "v2"
    assert artifact["bid_rows"][0]["formal_mode"] == "v2"
    assert artifact["bid_rows"][0]["决策价值 P10/P50/P90"]
    assert artifact["bid_rows"][0]["原始价值 P10/P50/P90"]
    assert "上界风险" in artifact["bid_rows"][0]
    assert artifact["panel"]["summary_rows"]
    assert artifact["ui_contract"]["schema_version"] == 1
    assert artifact["ui_contract"]["mode"] == "baseline_first_shadow_reference"
    assert artifact["ui_contract"]["truth"]["complete"] is True
    assert artifact["ui_contract"]["truth"]["partial"] is False
    assert artifact["ui_contract"]["baseline"]["official"] is True
    assert artifact["ui_contract"]["baseline"]["affects_bid"] is True
    assert artifact["ui_contract"]["baseline"]["posterior"][
        "total_item_count_status"
    ] == "not_estimated_by_v2"
    assert artifact["ui_contract"]["q6_risk_reference"]["affects_bid"] is False
    assert [
        shadow["label"]
        for shadow in artifact["ui_contract"]["shadows"]
    ] == [
        "profile_b5",
        "aisha_deep_floor1",
        "aisha_deep11_floor1",
        "aisha_hidden_floor15",
        "aisha_villa_floor05",
        "ethan_villa_random_avg_floor1",
        "ethan_shipwreck_layout_conditional_c4_cells15",
    ]
    assert all(
        shadow["affects_bid"] is False
        for shadow in artifact["ui_contract"]["shadows"]
    )

    assert artifact["model_eval"]["final_value"] == 20_000
    assert artifact["model_eval"]["final_cells"] == 4
    assert artifact["model_eval"]["hero"] == "aisha"
    assert artifact["model_eval"]["final_q6_count"] == 0
    assert artifact["model_eval"]["final_top_item_name"] == "test_item"
    assert artifact["model_eval"]["final_top_item_value"] == 20_000
    assert artifact["model_eval"]["decision_value_p50"] == 20_000
    assert artifact["model_eval"]["posterior_samples"] == 10
    assert artifact["model_eval"]["posterior_total_samples"] == 10
    assert artifact["model_eval"]["monitor_n_trials"] == 10
    assert artifact["model_eval"]["monitor_roi_trials"] == 0
    assert artifact["model_eval"]["monitor_shadow_trials"] == 10
    assert artifact["model_eval"]["monitor_processing_seconds"] >= 0
    assert artifact["model_eval"]["input_constraints_mode"] == (
        "session_totals_stripped"
    )
    assert artifact["model_eval"]["input_total_item_count"] is None
    assert artifact["model_eval"]["q6_top_size_band"] == "no_q6"
    assert "v2_q6_value_p90_under_by" in artifact["model_eval"]
    assert "q6_count_cell_prior_risk" in artifact["model_eval"]
    assert "q6_count_cell_prior_gap" in artifact["model_eval"]
    assert "q6_count_cell_prior_floor_value" in artifact["model_eval"]
    assert "q6_practical_gate" in artifact["model_eval"]
    assert "q6_practical_p90" in artifact["model_eval"]
    assert "q6_practical_gate_hit" in artifact["model_eval"]
    assert "q6_practical_gate_false_positive_proxy" in artifact["model_eval"]
    assert "q6_practical_gate_under_before" in artifact["model_eval"]
    assert "q6_practical_gate_covered_after" in artifact["model_eval"]
    assert "q6_practical_gate_helped" in artifact["model_eval"]
    assert "q6_practical_p90_under_by" in artifact["model_eval"]
    assert artifact["model_eval"]["v3_post_shadow_label"] == "v3_summary_likelihood"
    assert artifact["model_eval"]["v3_post_shadow_trials"] == 10
    assert artifact["model_eval"]["v3_post_available"] is True
    assert artifact["model_eval"]["v3_post_ready"] is True
    assert artifact["model_eval"]["v3_post_affects_bid"] is False
    assert artifact["model_eval"]["v3_post_formal_decision_value_p50"] == 20_000
    assert "v3_formal_decision_value_p50_error_vs_formal" in artifact["model_eval"]
    assert "v3_q6_formal_decision_value_p90_under_by" in artifact["model_eval"]
    assert artifact["model_eval"]["v3_prior_available"] is True
    assert artifact["model_eval"]["v3_prior_error"] is None
    assert artifact["model_eval"]["v3_prior_map_id"] == 2401
    assert artifact["model_eval"]["v3_prior_expected_value"] == 20_000
    assert "v3_prior_q6_expected_count" in artifact["model_eval"]
    assert artifact["model_eval"]["v3_robust_available"] is True
    assert artifact["model_eval"]["v3_robust_affects_bid"] is False
    assert artifact["model_eval"]["v3_robust_prior_usable"] is True
    assert artifact["model_eval"]["v3_robust_activity_candidate"] is False
    assert "v3_robust_status" in artifact["model_eval"]
    assert "v3_robust_fallback_mode" in artifact["model_eval"]
    assert artifact["model_eval"]["v3_cse_available"] is True
    assert artifact["model_eval"]["v3_cse_affects_bid"] is False
    assert artifact["model_eval"]["v3_cse_active"] is False
    assert "v3_cse_status" in artifact["model_eval"]
    assert "v3_cse_pressure_candidate" in artifact["model_eval"]
    assert "v3_cse_target_count_source" in artifact["model_eval"]
    assert "v3_cse_target_count" in artifact["model_eval"]
    assert "v3_cse_prior_items_per_session_max" in artifact["model_eval"]
    assert "v3_cse_target_prior_max_delta" in artifact["model_eval"]
    assert "v3_cse_target_to_unique_non_temp_p95_delta" in artifact["model_eval"]
    assert "v3_cse_source_context_classes" in artifact["model_eval"]
    assert artifact["model_eval"]["v3_ccv_available"] is True
    assert artifact["model_eval"]["v3_ccv_affects_bid"] is False
    assert "v3_ccv_q6_count_p50" in artifact["model_eval"]
    assert "v3_ccv_q6_cells_p50" in artifact["model_eval"]
    assert artifact["model_eval"]["v3_resid_available"] is True
    assert artifact["model_eval"]["v3_resid_affects_bid"] is False
    assert "v3_resid_q6_count_p50" in artifact["model_eval"]
    assert "v3_resid_q6_cells_p50" in artifact["model_eval"]
    assert "v3_resid_q6_value_p50" in artifact["model_eval"]
    assert artifact["model_eval"]["v3_resid_gate_available"] is True
    assert artifact["model_eval"]["v3_resid_gate_affects_bid"] is False
    assert "v3_resid_gate_active" in artifact["model_eval"]
    assert "v3_resid_gate_q6_value_p50" in artifact["model_eval"]
    assert artifact["model_eval"]["v3_cal_affects_bid"] is False
    assert "v3_cal_ready" in artifact["model_eval"]
    assert "v3_cal_active" in artifact["model_eval"]
    assert "v3_cal_status" in artifact["model_eval"]
    assert "v3_cal_scale" in artifact["model_eval"]
    assert "v3_cal_formal_decision_value_p50" in artifact["model_eval"]
    assert "v3_cal_formal_decision_value_p50_error_vs_formal" in artifact[
        "model_eval"
    ]
    assert artifact["model_eval"]["v3_under_affects_bid"] is False
    assert artifact["model_eval"]["v3_under_active"] is False
    assert "v3_under_ready" in artifact["model_eval"]
    assert "v3_under_candidate" in artifact["model_eval"]
    assert "v3_under_status" in artifact["model_eval"]
    assert "v3_under_scale" in artifact["model_eval"]
    assert "v3_under_formal_decision_value_p50" in artifact["model_eval"]
    assert "v3_under_formal_decision_value_p50_error_vs_formal" in artifact[
        "model_eval"
    ]
    assert artifact["model_eval"]["v3_tail_review_affects_bid"] is False
    assert artifact["model_eval"]["v3_tail_review_active"] is False
    assert "v3_tail_review_ready" in artifact["model_eval"]
    assert "v3_tail_review_candidate" in artifact["model_eval"]
    assert "v3_tail_review_hurt_guard" in artifact["model_eval"]
    assert "v3_tail_review_status" in artifact["model_eval"]
    assert "v3_tail_review_tail_replacement_decision_value_p50" in artifact[
        "model_eval"
    ]
    assert "v3_tail_review_q6_tail_replacement_decision_value_p50" in artifact[
        "model_eval"
    ]
    assert artifact["model_eval"]["v3_fv_available"] is True
    assert artifact["model_eval"]["v3_fv_affects_bid"] is False
    assert artifact["model_eval"]["v3_fv_active"] is False
    assert artifact["model_eval"]["v3_fv_candidate"] is False
    assert artifact["model_eval"]["v3_fv_status"] == "baseline_passthrough"
    assert artifact["model_eval"]["v3_fv_stress_class"] == "none"
    assert artifact["model_eval"]["v3_fv_formal_decision_value_p50"] == 20_000
    assert "v3_fv_total_count_source" in artifact["model_eval"]
    assert "v3_fv_total_count_target_prior_ratio" in artifact["model_eval"]
    assert "v3_fv_total_cells_source" in artifact["model_eval"]
    assert "v3_fv_total_cells_target" in artifact["model_eval"]
    assert "v3_fv_total_cells_prior_expected" in artifact["model_eval"]
    assert "v3_fv_q6_count_source" in artifact["model_eval"]
    assert "v3_fv_q6_count_target_prior_ratio" in artifact["model_eval"]
    assert "v3_fv_q6_cells_source" in artifact["model_eval"]
    assert "v3_fv_q6_cells_target" in artifact["model_eval"]
    assert "v3_fv_q6_cells_prior_expected" in artifact["model_eval"]
    assert "v3_fv_total_value_source" in artifact["model_eval"]
    assert "v3_fv_total_value_target_prior_ratio" in artifact["model_eval"]
    assert "v3_fv_q6_value_source" in artifact["model_eval"]
    assert "v3_fv_q6_value_target_prior_ratio" in artifact["model_eval"]
    assert "v3_fv_formal_decision_value_p50_error_vs_formal" in artifact[
        "model_eval"
    ]
    assert "v3_fv_q6_formal_decision_value_p90_under_by" in artifact[
        "model_eval"
    ]
    assert artifact["model_eval"]["v3_scp_available"] is True
    assert artifact["model_eval"]["v3_scp_affects_bid"] is False
    assert artifact["model_eval"]["v3_scp_active"] is False
    assert artifact["model_eval"]["v3_scp_status"] == (
        "observed_exceeds_table_caps_shadow_only"
    )
    assert "v3_scp_prior_max_to_observed_p95_delta" in artifact["model_eval"]
    assert artifact["model_eval"]["v3_practical_available"] is True
    assert artifact["model_eval"]["v3_practical_affects_bid"] is False
    assert artifact["model_eval"]["v3_practical_active"] is False
    assert "v3_practical_status" in artifact["model_eval"]
    assert "v3_practical_recommendation" in artifact["model_eval"]
    assert "v3_practical_formal_decision_value_p50" in artifact["model_eval"]
    assert "v3_practical_formal_decision_value_p50_error_vs_formal" in artifact[
        "model_eval"
    ]
    assert artifact["ui_contract"]["diagnostics"]["v3_practical"][
        "affects_bid"
    ] is False
    assert "recommendation" in artifact["ui_contract"]["diagnostics"][
        "v3_practical"
    ]
    assert "q6_residual_boost_shadow_active" in artifact["model_eval"]
    assert "q6_residual_boost_shadow_q6_p90_delta" in artifact["model_eval"]
    assert artifact["model_eval"]["q6_residual_boost_shadow_active"] is False
    assert artifact["model_eval"]["q6_residual_boost_shadow_trials"] == 10
    assert "q6_residual_deep_floor_shadow_active" in artifact["model_eval"]
    assert artifact["model_eval"]["q6_residual_deep_floor_shadow_trials"] == 10
    assert "q6_residual_deep_floor_shadow_q6_p90_delta" in artifact["model_eval"]
    assert artifact["model_eval"]["q6_residual_deep_floor_shadow_active"] is False
    assert "q6_residual_deep11_floor_shadow_active" in artifact["model_eval"]
    assert artifact["model_eval"]["q6_residual_deep11_floor_shadow_trials"] == 10
    assert (
        "q6_residual_deep11_floor_shadow_q6_p90_delta"
        in artifact["model_eval"]
    )
    assert artifact["model_eval"]["q6_residual_deep11_floor_shadow_active"] is False
    assert "q6_residual_hidden_floor_shadow_active" in artifact["model_eval"]
    assert artifact["model_eval"]["q6_residual_hidden_floor_shadow_trials"] == 10
    assert (
        "q6_residual_hidden_floor_shadow_q6_p90_delta"
        in artifact["model_eval"]
    )
    assert artifact["model_eval"]["q6_residual_hidden_floor_shadow_active"] is False
    assert "q6_residual_villa_floor_shadow_active" in artifact["model_eval"]
    assert artifact["model_eval"]["q6_residual_villa_floor_shadow_trials"] == 10
    assert (
        "q6_residual_villa_floor_shadow_q6_p90_delta"
        in artifact["model_eval"]
    )
    assert artifact["model_eval"]["q6_residual_villa_floor_shadow_active"] is False
    assert (
        "q6_residual_ethan_villa_random_floor_shadow_active"
        in artifact["model_eval"]
    )
    assert (
        artifact["model_eval"][
            "q6_residual_ethan_villa_random_floor_shadow_trials"
        ]
        == 10
    )
    assert (
        "q6_residual_ethan_villa_random_floor_shadow_q6_p90_delta"
        in artifact["model_eval"]
    )
    assert (
        artifact["model_eval"][
            "q6_residual_ethan_villa_random_floor_shadow_active"
        ]
        is False
    )
    assert (
        "q6_residual_ethan_shipwreck_layout_conditional_shadow_active"
        in artifact["model_eval"]
    )
    assert (
        artifact["model_eval"][
            "q6_residual_ethan_shipwreck_layout_conditional_shadow_trials"
        ]
        == 10
    )
    assert (
        "q6_residual_ethan_shipwreck_layout_conditional_shadow_q6_p90_delta"
        in artifact["model_eval"]
    )
    assert (
        artifact["model_eval"][
            "q6_residual_ethan_shipwreck_layout_conditional_shadow_active"
        ]
        is False
    )
    assert "raw_minus_decision_p90" in artifact["model_eval"]
    assert "layout_conflict_root" in artifact["model_eval"]
    assert "shape_target_count" in artifact["model_eval"]
    assert "category_target_count" in artifact["model_eval"]
    assert "category_exclusion_count" in artifact["model_eval"]
    assert "anchor_count" in artifact["model_eval"]
    assert "random_sample_avg_values" in artifact["model_eval"]
    assert "random_sample_avg_signal_values" in artifact["model_eval"]
    assert "public_constraint_key" in artifact["model_eval"]
    assert "evidence_profile_key" in artifact["model_eval"]
    assert "q6_aisha_bottom_row_risk" in artifact["model_eval"]
    assert artifact["model_eval"]["q6_quality_only_local_count"] == 0
    assert artifact["model_eval"]["q6_quality_only_deep_local_risk"] is False
    assert artifact["model_eval"]["q6_quality_only_deep_row_threshold"] == 13
    assert "final_q6_tail_replacement_value" in artifact["model_eval"]
    assert "q6_tail_replacement_p90_misses_truth" in artifact["model_eval"]
    assert "v2_q6_tail_replacement_estimate_p90" in artifact["model_eval"]
    assert (
        "q6_tail_replacement_estimate_p90_misses_truth"
        in artifact["model_eval"]
    )
    assert (
        "v2_q6_tail_replacement_decision_value_p90_under_by"
        in artifact["model_eval"]
    )
    assert "layout_bottom_row" in artifact["model_eval"]
    assert artifact["model_eval"]["evidence_stage"] == "full_5"
    assert artifact["model_eval"]["information_density_band"] in {
        "low",
        "medium",
        "high",
    }
    assert "hero_information_density" in artifact["model_eval"]
    assert artifact["model_eval"]["relaxed_exact_used"] is False


def test_incomplete_settlement_skips_model_eval_and_marks_partial_truth() -> None:
    settlement = _events().states[0]
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=0,
                capture_time="",
                message_id=0x0025,
                session_id="s1",
                map_id=2401,
                round_index=4,
                action_results=(
                    FatbeansActionResult(
                        action_id=100115,
                        result=2,
                        result_field=7,
                    ),
                ),
            ),
            settlement,
        ),
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="partial.json",
        tables=_tables(),
        n_trials=10,
        roi_trials=0,
    )

    assert artifact["settlement_incomplete"] is True
    assert artifact["settlement_incomplete_detail"] == {
        "captured_items": 1,
        "declared_items": 2,
        "captured_value": 20_000,
        "source": "packet",
    }
    assert artifact["known_value_sum"] == 20_000
    assert artifact["final_q6_count"] == 0
    assert "model_eval" not in artifact
    assert artifact["model_eval_skipped_reason"] == "settlement_incomplete"
    assert artifact["ui_contract"]["truth"]["available"] is True
    assert artifact["ui_contract"]["truth"]["complete"] is False
    assert artifact["ui_contract"]["truth"]["partial"] is True
    assert artifact["ui_contract"]["truth"]["captured_items"] == 1
    assert artifact["ui_contract"]["truth"]["declared_items"] == 2


def test_live_formal_mode_v3_practical_rebuilds_bid_rows(monkeypatch) -> None:
    def fake_v3_shadow(*_args, **kwargs):
        return {
            **monitor_module._empty_v3_posterior_shadow(
                trials=int(kwargs.get("trials") or 1),
            ),
            "v3_practical_available": True,
            "v3_practical_ready": True,
            "v3_practical_candidate": True,
            "v3_practical_recommendation": "raise_watch",
            "v3_practical_confidence": "medium",
            "v3_practical_source_lanes": "formal_value+prior_q6_floor",
            "v3_practical_risk_flags": "q6_prior_floor_watch",
            "v3_practical_reason": "test v3 formal override",
            "v3_practical_formal_decision_value_p10": 30_000,
            "v3_practical_formal_decision_value_p50": 80_000,
            "v3_practical_formal_decision_value_p90": 160_000,
            "v3_practical_total_value_p10": 40_000,
            "v3_practical_total_value_p50": 100_000,
            "v3_practical_total_value_p90": 220_000,
        }

    monkeypatch.setattr(
        monitor_module,
        "_v3_posterior_shadow_summary",
        fake_v3_shadow,
    )

    artifact = build_monitor_artifact_from_events(
        _events(),
        file="sample.json",
        tables=_tables(),
        n_trials=10,
        roi_trials=0,
        formal_mode="v3_practical",
    )

    row = artifact["bid_rows"][0]
    assert artifact["formal_mode_requested"] == "v3_practical"
    assert artifact["formal_mode"] == "v3_practical"
    assert artifact["formal_mode_reason"] == "v3_practical_ready"
    assert row["证据"] == "v3 practical formal"
    assert row["formal_override"] == "是"
    assert row["决策价值 P10/P50/P90"] == "30,000 / 80,000 / 160,000"
    assert row["原始价值 P10/P50/P90"] == "40,000 / 100,000 / 220,000"
    assert artifact["v2_bid_rows"][0]["证据"] == "v2 decision_value"
    assert artifact["v2_bid_rows"][0]["formal_mode"] == "v2"
    assert artifact["v3_practical_bid_rows"][0]["证据"] == "v3 practical formal"
    assert artifact["model_eval"]["formal_mode"] == "v3_practical"
    assert artifact["ui_contract"]["mode"] == "v3_practical_formal_with_v2_reference"
    assert artifact["ui_contract"]["baseline"]["source"] == "v3_practical"
    assert (
        artifact["ui_contract"]["baseline"]["posterior"]["decision_value_range"]
        == "30,000 / 80,000 / 160,000"
    )
    assert (
        artifact["ui_contract"]["baseline"]["posterior"]["raw_value_range"]
        == "40,000 / 100,000 / 220,000"
    )
    assert artifact["ui_contract"]["v2_reference"]["available"] is True
    assert artifact["ui_contract"]["v2_reference"]["affects_bid"] is False


def test_live_formal_mode_v3_practical_guards_low_confidence_prior_only_raise(
    monkeypatch,
) -> None:
    def fake_v3_shadow(*_args, **kwargs):
        return {
            **monitor_module._empty_v3_posterior_shadow(
                trials=int(kwargs.get("trials") or 1),
            ),
            "v3_practical_available": True,
            "v3_practical_ready": True,
            "v3_practical_candidate": True,
            "v3_practical_recommendation": "raise_watch",
            "v3_practical_confidence": "low_medium",
            "v3_practical_source_lanes": (
                "formal_value+prior_q6_floor+settlement_count_prior"
            ),
            "v3_practical_risk_flags": (
                "q6_prior_floor_watch+settlement_count_prior_candidate"
            ),
            "v3_practical_reason": "prior-only raise",
            "v3_practical_formal_decision_value_p10": 30_000,
            "v3_practical_formal_decision_value_p50": 80_000,
            "v3_practical_formal_decision_value_p90": 300_000,
            "v3_practical_total_value_p10": 30_000,
            "v3_practical_total_value_p50": 80_000,
            "v3_practical_total_value_p90": 300_000,
        }

    monkeypatch.setattr(
        monitor_module,
        "_v3_posterior_shadow_summary",
        fake_v3_shadow,
    )

    artifact = build_monitor_artifact_from_events(
        _events(),
        file="sample.json",
        tables=_tables(),
        n_trials=10,
        roi_trials=0,
        formal_mode="v3_practical",
    )

    row = artifact["bid_rows"][0]

    assert artifact["formal_mode"] == "v3_practical"
    assert artifact["formal_mode_reason"] == "v3_practical_ready_live_guarded"
    assert row["formal_mode_reason"] == "v3_practical_ready_live_guarded"
    assert row["决策价值 P10/P50/P90"] == "30,000 / 80,000 / 155,000"
    assert row["v3_practical_unguarded_decision_value"] == (
        "30,000 / 80,000 / 300,000"
    )
    assert row["v3_practical_live_guard"] == "是"
    assert "live_prior_only_raise_guard" in row["后验诊断"]
    assert artifact["model_eval"]["v3_practical_live_guard"] == "是"
    assert (
        artifact["model_eval"]["v3_practical_live_guard_reason"]
        == row["v3_practical_live_guard_reason"]
    )
    assert artifact["model_eval"]["v3_practical_unguarded_decision_value"] == (
        "30,000 / 80,000 / 300,000"
    )
    assert artifact["model_eval"]["v3_practical_unguarded_decision_value_p10"] == 30_000
    assert artifact["model_eval"]["v3_practical_unguarded_decision_value_p50"] == 80_000
    assert artifact["model_eval"]["v3_practical_unguarded_decision_value_p90"] == 300_000
    assert (
        artifact["ui_contract"]["baseline"]["posterior"]["decision_value_range"]
        == "30,000 / 80,000 / 155,000"
    )


def test_live_formal_mode_v3_practical_guards_low_support_baseline(
    monkeypatch,
) -> None:
    def fake_v3_shadow(*_args, **kwargs):
        return {
            **monitor_module._empty_v3_posterior_shadow(
                trials=int(kwargs.get("trials") or 1),
            ),
            "v3_post_available": True,
            "v3_post_ready": True,
            "v3_post_strict_ready": False,
            "v3_post_match_scope": "summary_likelihood",
            "v3_post_diagnostics": (
                "no_strict_summary_matched_samples;"
                "summary_likelihood_fallback;"
                "summary_likelihood_effective_samples=2.500;"
                "practical_p50_guard_quantile=0.60"
            ),
            "v3_practical_available": True,
            "v3_practical_ready": True,
            "v3_practical_candidate": False,
            "v3_practical_source": "baseline",
            "v3_practical_status": "baseline_passthrough",
            "v3_practical_recommendation": "baseline_reference",
            "v3_practical_confidence": "baseline",
            "v3_practical_source_lanes": "settlement_count_prior",
            "v3_practical_risk_flags": "settlement_count_prior_candidate",
            "v3_practical_reason": "broad_risk_recorded_no_action",
            "v3_practical_formal_decision_value_p10": 200_000,
            "v3_practical_formal_decision_value_p50": 500_000,
            "v3_practical_formal_decision_value_p90": 700_000,
            "v3_practical_total_value_p10": 200_000,
            "v3_practical_total_value_p50": 500_000,
            "v3_practical_total_value_p90": 700_000,
        }

    monkeypatch.setattr(
        monitor_module,
        "_v3_posterior_shadow_summary",
        fake_v3_shadow,
    )

    artifact = build_monitor_artifact_from_events(
        _events(),
        file="sample.json",
        tables=_tables(),
        n_trials=10,
        roi_trials=0,
        formal_mode="v3_practical",
    )

    row = artifact["bid_rows"][0]

    assert artifact["formal_mode"] == "v3_practical"
    assert artifact["formal_mode_reason"] == "v3_practical_ready_live_guarded"
    assert row["formal_mode_reason"] == "v3_practical_ready_live_guarded"
    assert row["决策价值 P10/P50/P90"] == "200,000 / 350,000 / 500,000"
    assert row["v3_practical_unguarded_decision_value"] == (
        "200,000 / 500,000 / 700,000"
    )
    assert row["v3_practical_live_guard"] == "是"
    assert "live_low_support_baseline_guard" in row["后验诊断"]
    assert artifact["model_eval"]["v3_practical_live_guard"] == "是"
    assert (
        artifact["model_eval"]["v3_practical_live_guard_reason"]
        == row["v3_practical_live_guard_reason"]
    )
    assert artifact["model_eval"]["v3_practical_unguarded_decision_value"] == (
        "200,000 / 500,000 / 700,000"
    )
    assert artifact["model_eval"]["v3_practical_unguarded_decision_value_p10"] == 200_000
    assert artifact["model_eval"]["v3_practical_unguarded_decision_value_p50"] == 500_000
    assert artifact["model_eval"]["v3_practical_unguarded_decision_value_p90"] == 700_000
    assert (
        artifact["ui_contract"]["baseline"]["posterior"]["decision_value_range"]
        == "200,000 / 350,000 / 500,000"
    )


def test_live_formal_mode_v2_keeps_v2_bid_rows_even_when_v3_available(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        monitor_module,
        "_v3_posterior_shadow_summary",
        lambda *_args, **kwargs: {
            **monitor_module._empty_v3_posterior_shadow(
                trials=int(kwargs.get("trials") or 1),
            ),
            "v3_practical_available": True,
            "v3_practical_ready": True,
            "v3_practical_formal_decision_value_p10": 30_000,
            "v3_practical_formal_decision_value_p50": 80_000,
            "v3_practical_formal_decision_value_p90": 160_000,
        },
    )

    artifact = build_monitor_artifact_from_events(
        _events(),
        file="sample.json",
        tables=_tables(),
        n_trials=10,
        roi_trials=0,
        formal_mode="v2",
    )

    assert artifact["formal_mode"] == "v2"
    assert artifact["formal_mode_reason"] == "v2_mode_requested"
    assert artifact["bid_rows"][0]["证据"] == "v2 decision_value"
    assert artifact["bid_rows"][0]["formal_override"] == "否"
    assert artifact["v3_practical_bid_rows"] == []
    assert artifact["ui_contract"]["mode"] == "baseline_first_shadow_reference"


def test_model_eval_uses_problem_evidence_profile_when_available() -> None:
    row = _model_eval_row(
        file="sample.json",
        artifact={
            "file": "sample.json",
            "hero": "aisha",
            "map_id": 2501,
            "round": 3,
            "evidence_profile_key": "shape+layout",
            "bid_rows": [
                {
                    "决策价值 P10/P50/P90": "100 / 200 / 300",
                    "原始价值 P10/P50/P90": "100 / 200 / 300",
                },
            ],
            "warehouse_rows": [{"价值 P10/P50/P90": "100 / 200 / 300"}],
            "v2_posterior_rows": [
                {
                    "形状约束数": 2,
                    "分类约束数": 0,
                    "分类反排数": 0,
                    "诊断": "",
                },
            ],
        },
        final_value=200,
        final_cells=10,
        truth_breakdown={},
    )

    assert row is not None
    assert row["evidence_profile_key"] == "shape+layout"


def test_model_eval_uses_v2_value_when_bid_rows_are_absent() -> None:
    row = _model_eval_row(
        file="opening.json",
        artifact={
            "file": "opening.json",
            "hero": "ethan",
            "map_id": 2401,
            "round": 1,
            "warehouse_rows": [{"价值 P10/P50/P90": "100 / 200 / 300"}],
            "v2_posterior_rows": [
                {
                    "匹配": "3/10",
                    "决策价值 P10/P50/P90": "100 / 250 / 400",
                    "原始价值 P10/P50/P90": "120 / 260 / 420",
                    "q6价值 P10/P50/P90": "0 / 0 / 0",
                    "q6决策价值 P10/P50/P90": "0 / 0 / 0",
                    "诊断": "",
                },
            ],
        },
        final_value=300,
        final_cells=10,
        truth_breakdown={},
    )

    assert row is not None
    assert row["decision_value_p50"] == 250
    assert row["decision_value_p90"] == 400
    assert row["decision_value_truth"] == 300
    assert row["decision_value_truth_source"] == "raw"
    assert row["decision_value_p50_error"] == -50
    assert row["decision_value_p50_error_vs_formal"] is None
    assert row["decision_value_p50_error_vs_raw"] == -50
    assert row["raw_value_p50"] == 260
    assert row["raw_value_p90"] == 420


def test_model_eval_surfaces_v3_capacity_prior_gap() -> None:
    row = _model_eval_row(
        file="capacity.json",
        artifact={
            "file": "capacity.json",
            "hero": "ethan",
            "map_id": 2501,
            "round": 3,
            "bid_rows": [
                {
                    "决策价值 P10/P50/P90": "100 / 200 / 300",
                    "原始价值 P10/P50/P90": "100 / 200 / 300",
                },
            ],
            "warehouse_rows": [{"价值 P10/P50/P90": "100 / 200 / 300"}],
            "v2_posterior_rows": [{"诊断": ""}],
            "v3_posterior_shadow": {
                "v3_summary_known_count_floor": 6,
                "v3_summary_session_total_count_exact": 7,
                "v3_prior_items_per_session_min": 2,
                "v3_prior_items_per_session_max": 5,
            },
        },
        final_value=300,
        final_cells=10,
        truth_breakdown={"final_quality_counts": "q4=2;q6=5"},
    )

    assert row is not None
    assert row["v3_capacity_total_count_source"] == "exact"
    assert row["v3_capacity_total_count_target"] == 7
    assert row["v3_capacity_truth_item_count"] == 7
    assert row["v3_capacity_prior_items_per_session_max"] == 5
    assert row["v3_capacity_target_prior_max_delta"] == 2
    assert row["v3_capacity_truth_prior_max_delta"] == 2
    assert row["v3_capacity_target_truth_delta"] == 0
    assert row["v3_capacity_target_prior_max_ratio"] == 1.4
    assert row["v3_capacity_truth_prior_max_ratio"] == 1.4
    assert row["v3_capacity_flags"] == (
        "target_count_above_prior_max+truth_count_above_prior_max"
    )
    assert row["v3_capacity_cases"] == "direct_prior_max_conflict"


def test_model_eval_decision_error_uses_replacement_truth_and_keeps_comparisons() -> None:
    row = _model_eval_row(
        file="tail.json",
        artifact={
            "file": "tail.json",
            "hero": "aisha",
            "map_id": 2506,
            "round": 4,
            "bid_rows": [
                {
                    "决策价值 P10/P50/P90": "400000 / 550000 / 800000",
                    "原始价值 P10/P50/P90": "500000 / 700000 / 1100000",
                },
            ],
            "warehouse_rows": [{"价值 P10/P50/P90": "500000 / 700000 / 1100000"}],
            "v2_posterior_rows": [{"诊断": ""}],
        },
        final_value=1_000_000,
        final_cells=10,
        truth_breakdown={
            "final_decision_value": 600_000,
            "final_decision_value_with_tail_replacement": 650_000,
        },
    )

    assert row is not None
    assert row["decision_value_truth"] == 650_000
    assert row["decision_value_truth_source"] == "tail_replacement"
    assert row["decision_value_p50_error"] == -100_000
    assert row["decision_value_p90_error"] == 150_000
    assert row["decision_value_p50_error_vs_formal"] == -50_000
    assert row["decision_value_p90_error_vs_formal"] == 200_000
    assert row["decision_value_p50_error_vs_raw"] == -450_000
    assert row["decision_value_p90_error_vs_raw"] == -200_000


def test_model_eval_shadow_readiness_uses_plannable_q6_truth() -> None:
    row = _model_eval_row(
        file="tail.json",
        artifact={
            "file": "tail.json",
            "hero": "aisha",
            "map_id": 2501,
            "round": 4,
            "bid_rows": [
                {
                    "决策价值 P10/P50/P90": "100 / 200 / 300",
                    "原始价值 P10/P50/P90": "100 / 200 / 300",
                },
            ],
            "warehouse_rows": [{"价值 P10/P50/P90": "100 / 200 / 300"}],
            "v2_posterior_rows": [
                {
                    "q6价值 P10/P50/P90": "0 / 0 / 0",
                    "q6决策价值 P10/P50/P90": "0 / 0 / 0",
                    "诊断": "",
                },
            ],
            "q6_residual_deep_floor_shadow": {
                "label": "aisha_deep_floor1",
                "active": True,
                "q6_decision_value_p90": 900_000,
            },
        },
        final_value=1_039_000,
        final_cells=2,
        truth_breakdown={
            "final_q6_value": 1_039_000,
            "final_q6_decision_value": 0,
        },
    )

    assert row is not None
    assert row["q6_p90_misses_truth"] is True
    assert row["q6_plannable_p90_misses_truth"] is None
    assert row["q6_no_plannable_control"] is True
    assert row["q6_zero_q6_proven_control"] is False
    assert row["q6_residual_deep_floor_shadow_under_before"] is False
    assert row["q6_residual_deep_floor_shadow_helped"] is False
    assert row["q6_residual_deep_floor_shadow_no_plannable_control"] is True
    assert (
        row["q6_residual_deep_floor_shadow_no_plannable_positive_proxy"]
        is True
    )
    assert (
        row["q6_residual_deep_floor_shadow_zero_q6_proven_false_positive"]
        is False
    )
    assert row["q6_residual_deep_floor_shadow_false_positive_proxy"] is True


def test_model_eval_separates_zero_q6_proven_shadow_false_positive() -> None:
    row = _model_eval_row(
        file="isabella_zero.json",
        artifact={
            "file": "isabella_zero.json",
            "hero": "isabella",
            "map_id": 2401,
            "round": 3,
            "bid_rows": [
                {
                    "决策价值 P10/P50/P90": "100 / 200 / 300",
                    "原始价值 P10/P50/P90": "100 / 200 / 300",
                },
            ],
            "warehouse_rows": [{"价值 P10/P50/P90": "100 / 200 / 300"}],
            "v2_posterior_rows": [
                {
                    "q6价值 P10/P50/P90": "0 / 0 / 0",
                    "q6决策价值 P10/P50/P90": "0 / 0 / 0",
                    "诊断": "public_max_quality:5",
                },
            ],
            "q6_residual_villa_floor_shadow": {
                "label": "aisha_villa_floor05",
                "active": True,
                "q6_decision_value_p90": 120_000,
            },
        },
        final_value=300_000,
        final_cells=10,
        truth_breakdown={
            "final_q6_value": 0,
            "final_q6_decision_value": 0,
        },
    )

    assert row is not None
    assert row["q6_no_plannable_control"] is True
    assert row["q6_zero_q6_proven_control"] is True
    assert row["q6_residual_villa_floor_shadow_false_positive_proxy"] is True
    assert (
        row["q6_residual_villa_floor_shadow_zero_q6_proven_false_positive"]
        is True
    )


def test_model_eval_tail_replacement_miss_requires_replacement_value() -> None:
    row = _model_eval_row(
        file="sample58.json",
        artifact={
            "file": "sample58.json",
            "hero": "aisha",
            "map_id": 2502,
            "round": 2,
            "bid_rows": [
                {
                    "决策价值 P10/P50/P90": "100 / 200 / 300",
                    "原始价值 P10/P50/P90": "100 / 200 / 300",
                },
            ],
            "warehouse_rows": [{"价值 P10/P50/P90": "100 / 200 / 300"}],
            "v2_posterior_rows": [
                {
                    "q6价值 P10/P50/P90": "0 / 0 / 2,100,000",
                    "q6决策价值 P10/P50/P90": "0 / 0 / 2,100,000",
                    "诊断": "",
                },
            ],
            "q6_residual_deep_floor_shadow": {},
        },
        final_value=3_185_700,
        final_cells=38,
        truth_breakdown={
            "final_q6_value": 3_185_700,
            "final_q6_decision_value": 3_185_700,
            "final_q6_trimmed_tail_value": 0,
            "final_q6_tail_replacement_value": 0,
            "final_q6_decision_value_with_tail_replacement": 3_185_700,
        },
    )

    assert row is not None
    assert row["q6_plannable_p90_misses_truth"] is True
    assert row["q6_tail_replacement_p90_misses_truth"] is None
    assert row["v2_q6_tail_replacement_decision_value_p90_under_by"] is None


def test_inventory_quality_breakdown_keeps_exact_tail_anchor_plannable() -> None:
    tail = Item(
        item_id=9001,
        name="tail",
        description="",
        name_key="tail",
        desc_key="tail_desc",
        quality=6,
        quality_color="red",
        value=1_039_000,
        shape_w=1,
        shape_h=2,
        tags=[109],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )
    ordinary_same_shape = Item(
        item_id=9002,
        name="ordinary",
        description="",
        name_key="ordinary",
        desc_key="ordinary_desc",
        quality=6,
        quality_color="red",
        value=93_000,
        shape_w=1,
        shape_h=2,
        tags=[109],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=1,
                capture_time="",
                message_id=0x002D,
                session_id="s1",
                map_id=2501,
                round_index=4,
                inventory_items=(
                    FatbeansInventoryItem(
                        runtime_id=1,
                        item_id=tail.item_id,
                        quality=6,
                        cells=2,
                    ),
                ),
            ),
        ),
    )

    item_table = {
        tail.item_id: tail,
        ordinary_same_shape.item_id: ordinary_same_shape,
    }
    unsupported = _inventory_quality_breakdown(events, item_table)
    anchored = _inventory_quality_breakdown(
        events,
        item_table,
        problem=ResidualProblem(
            map_id=2501,
            map_name="shipwreck",
            anchors=(),
            known_item_count=1,
            known_cells=2,
            known_value=tail.value,
            anchor_item_counts={tail.item_id: 1},
            bucket_targets={},
            category_targets=(),
            shape_targets=(),
            layout=LayoutFeasibility(
                footprint_count=0,
                trusted_footprint_count=0,
                occupied_cells=0,
                item_cells=0,
                overlap_cells=0,
                overflow_count=0,
                bottom_row=None,
                bounding_cells=0,
                score=1.0,
            ),
        ),
    )

    assert unsupported["final_q6_value"] == tail.value
    assert unsupported["final_q6_decision_value"] == 0
    assert unsupported["final_q6_trimmed_tail_value"] == tail.value
    assert unsupported["final_q6_tail_replacement_value"] == 93_000
    assert unsupported["final_q6_tail_replacement_count"] == 1
    assert unsupported["final_q6_tail_replacement_source"] == "item_table_median"
    assert unsupported["final_q6_decision_value_with_tail_replacement"] == 93_000
    assert unsupported["final_decision_value_with_tail_replacement"] == 93_000
    assert anchored["final_q6_decision_value"] == tail.value
    assert anchored["final_decision_value_with_tail_replacement"] == tail.value
    assert anchored["final_q6_trimmed_tail_value"] == 0
    assert anchored["final_q6_tail_replacement_value"] == 0


def test_debug_shadow_can_be_skipped_without_suppressing_baseline(monkeypatch) -> None:
    original_estimate = monitor_module.estimate_posterior_v2
    calls: list[dict] = []

    def wrapped_estimate(*args, **kwargs):
        calls.append(dict(kwargs))
        return original_estimate(*args, **kwargs)

    monkeypatch.setattr(
        monitor_module,
        "q6_residual_boost_for_profile",
        lambda **_kwargs: 5.0,
    )
    monkeypatch.setattr(
        monitor_module,
        "estimate_posterior_v2",
        wrapped_estimate,
    )

    artifact = build_monitor_artifact_from_events(
        _events(),
        file="sample.json",
        tables=_tables(),
        n_trials=10,
        roi_trials=0,
        run_debug_shadows=False,
    )

    assert artifact["q6_residual_boost_shadow"]["active"] is False
    assert artifact["q6_residual_boost_shadow"]["active_boost"] == 1.0
    assert any(
        "q6_residual_boost" not in call
        and "q6_residual_prior_floor_ratio" not in call
        for call in calls
    )
    assert not any(float(call.get("q6_residual_boost", 1.0)) > 1.0 for call in calls)


def test_zero_match_fallback_rows_use_relaxed_v1_reference() -> None:
    tables = _tables()
    session = SessionObs(
        map_id=2401,
        hero="aisha",
        warehouse_total_cells=4,
        total_item_count=1,
        buckets={
            6: QualityBucketObs(
                quality=6,
                total_cells=99,
            ),
        },
        visible_outline_item_count_min=5,
        visible_outline_total_cells_min=99,
    )

    map_rows, warehouse_rows, bid_rows = _build_zero_match_fallback_rows(
        candidate_map_ids=(2401,),
        inference_session=session,
        latest_bids={"leader": 15_000},
        round_no=4,
        maps=tables.maps,
        drops=tables.drops,
        items=tables.items,
        n_trials=10,
        seed=1,
        cells_tol=2,
        count_tol=1,
    )

    assert map_rows[0]["匹配"] == "10/10"
    assert warehouse_rows[0]["匹配"] == "10/10"
    assert bid_rows[0]["fallback"] == "是"
    assert bid_rows[0]["fallback_mode"] == "v1_map_prior_zero_match"
    assert bid_rows[0]["价值口径"] == "raw_value"
    assert bid_rows[0]["原始价值 P10/P50/P90"]
    assert bid_rows[0]["建议"]


def test_ethan_sample37_residual_does_not_break_exact_bucket_targets() -> None:
    sample_root = Path(__file__).resolve().parents[1] / "data" / "samples" / "fatbeans"
    candidates = sorted(
        sample_root.rglob("*2501_1295018621897985*.json")
    )
    if not candidates:
        pytest.skip("local Fatbeans sample37 capture is unavailable")
    sample = candidates[0]
    artifact = build_monitor_artifact_from_file(
        sample,
        tables=load_monitor_tables(),
        n_trials=80,
        roi_trials=0,
        shadow_trials=20,
    )

    assert artifact["v2_posterior_rows"][0]["匹配"] != "0/80"
    assert artifact["bid_rows"]
    assert not artifact["fallback_bid_rows"]


def test_monitor_preserves_pre_settlement_full_outline_totals() -> None:
    events = FatbeansCaptureEvents(
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
                round_index=4,
                bids=(
                    FatbeansPlayerBid(
                        player_id=1,
                        name="leader",
                        hero_id=103,
                        values=(12_000,),
                    ),
                ),
                action_results=(
                    FatbeansActionResult(
                        action_id=100100,
                        result=None,
                        result_field=None,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=0,
                                runtime_id=101,
                                item_id=None,
                                quality=None,
                                value=None,
                                shape_code=11,
                                cells=None,
                            ),
                            FatbeansObservedItem(
                                local_index=1,
                                runtime_id=102,
                                item_id=None,
                                quality=None,
                                value=None,
                                shape_code=11,
                                cells=None,
                            ),
                        ),
                    ),
                ),
            ),
            FatbeansStateEvent(
                sort_id=2,
                capture_time="",
                message_id=0x002D,
                session_id="s1",
                map_id=2401,
                round_index=5,
                inventory_items=(
                    FatbeansInventoryItem(
                        runtime_id=101,
                        item_id=1001,
                        quality=4,
                        cells=1,
                    ),
                    FatbeansInventoryItem(
                        runtime_id=102,
                        item_id=1002,
                        quality=4,
                        cells=1,
                    ),
                ),
            ),
        ),
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="full_outline.json",
        tables=_two_item_tables(),
        n_trials=20,
        roi_trials=0,
    )

    constraints = artifact["inference_input_constraints"]
    assert constraints["mode"] == "pre_settlement_trusted_totals"
    assert constraints["warehouse_total_cells"]["value"] == 2
    assert constraints["total_item_count"]["value"] == 2
    assert artifact["model_eval"]["input_warehouse_total_cells"] == 2
    assert artifact["model_eval"]["input_total_item_count"] == 2
    assert artifact["ui_contract"]["baseline"]["posterior"][
        "total_item_count_status"
    ] == "exact_input_constraint"
    assert artifact["ui_contract"]["constraints"]["summary"][
        "input_total_item_count"
    ] == 2


def test_shadow_trials_defaults_to_live_cap() -> None:
    assert DEFAULT_Q6_SHADOW_TRIALS_CAP == 80
    assert _resolve_shadow_trials(500, None) == 80
    assert _resolve_shadow_trials(20, None) == 20
    assert _resolve_shadow_trials(500, 120) == 120
    assert _resolve_shadow_trials(500, 0) == 1


def test_q6_risk_reference_text_is_explicitly_non_binding() -> None:
    text = monitor_module._q6_risk_reference_text(
        {
            "risk": True,
            "summary": "件数P90低0.41；格数P90低5.6",
            "floor_value": 499_973,
            "gate": "shipwreck_positive_net",
            "practical_p90": 499_973,
        }
    )

    assert "件数P90低0.41" in text
    assert "参考P90 499,973" in text
    assert "shipwreck_positive_net" in text
    assert "正式停止价由当前 formal_mode 统一重算" in text


def test_q6_reference_with_shadow_keeps_reference_non_binding() -> None:
    merged = monitor_module._q6_reference_with_shadow(
        {
            "risk": True,
            "summary": "件数P90低2.40",
            "floor_value": 486_510,
            "gate": "shipwreck_positive_net",
            "practical_p90": 486_510,
        },
        {
            "active": True,
            "label": "ethan_shipwreck_layout_conditional_c4_cells15",
            "gate": "ethan_shipwreck_layout_v1",
            "q6_decision_value_p90": 854_210,
        },
    )
    text = monitor_module._q6_risk_reference_text(merged)

    assert merged["practical_p90"] == 854_210
    assert (
        "ethan_shipwreck_layout_conditional_c4_cells15 q6P90 854,210"
        in merged["summary"]
    )
    assert "ethan_shipwreck_layout_v1" in merged["gate"]
    assert "正式停止价由当前 formal_mode 统一重算" in text


def test_q6_prior_gap_summary_uses_random_sample_avg_signal_as_reference() -> None:
    summary = monitor_module._q6_prior_gap_summary(
        SimpleNamespace(
            map_id=2401,
            q6_count=SimpleNamespace(p90=2),
            q6_prior_expected_count=2,
            q6_cells=SimpleNamespace(p90=10),
            q6_prior_expected_cells=10,
            q6_prior_expected_value=180_000,
            q6_decision_value=SimpleNamespace(p90=200_000),
            random_sample_avg_values=((3, 124_892.0),),
        )
    )

    assert summary["risk"] is True
    assert "随机3件均价高124,892" in summary["summary"]
    assert summary["floor_value"] == 374_676
    assert summary["gate"] == "random_avg_signal"
    assert summary["practical_p90"] == 374_676


def test_write_monitor_logs_updates_latest_and_jsonl(tmp_path: Path) -> None:
    artifact = build_monitor_artifact_from_events(
        _events(),
        file="sample.json",
        tables=_tables(),
        n_trials=10,
        roi_trials=0,
    )

    write_monitor_logs(artifact, log_dir=tmp_path)

    latest = json.loads((tmp_path / "latest_snapshot.json").read_text(encoding="utf-8"))
    assert latest["file"] == "sample.json"
    assert (tmp_path / "sessions.jsonl").read_text(encoding="utf-8")
    assert (tmp_path / "model_eval.jsonl").read_text(encoding="utf-8")


def test_write_monitor_logs_can_update_latest_without_appending_jsonl(
    tmp_path: Path,
) -> None:
    artifact = build_monitor_artifact_from_events(
        _events(),
        file="fast.json",
        tables=_tables(),
        n_trials=10,
        roi_trials=0,
    )

    write_monitor_logs(artifact, log_dir=tmp_path, append_logs=False)

    latest = json.loads((tmp_path / "latest_snapshot.json").read_text(encoding="utf-8"))
    assert latest["file"] == "fast.json"
    assert not (tmp_path / "sessions.jsonl").exists()
    assert not (tmp_path / "model_eval.jsonl").exists()


def test_atomic_write_json_retries_transient_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_replace = Path.replace
    calls = 0

    def flaky_replace(path: Path, target: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("transient snapshot lock")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr(monitor_module.time, "sleep", lambda _seconds: None)

    target = tmp_path / "latest_snapshot.json"
    monitor_module._atomic_write_json(target, {"file": "sample.json"})

    assert calls == 2
    assert json.loads(target.read_text(encoding="utf-8"))["file"] == "sample.json"
    assert [path for path in tmp_path.iterdir() if path != target] == []
