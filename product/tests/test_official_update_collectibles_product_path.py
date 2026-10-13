from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from bidking_lab.live import fatbeans, monitor
from bidking_lab.live.fatbeans import (
    FatbeansActionResult,
    FatbeansCaptureEvents,
    FatbeansObservedItem,
    FatbeansPublicInfo,
    FatbeansSkillReveal,
    FatbeansStateEvent,
)
from bidking_lab.runtime.snapshot import _ui_minimap_contract


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))
CALIBRATION = ROOT / "scripts" / "calibration"
if str(CALIBRATION) not in sys.path:
    sys.path.insert(0, str(CALIBRATION))

from ahmad_ref_engine import (  # noqa: E402
    _known_quality_value_total_floor,
    _validated_q4_catalog,
    _validated_q5_catalog,
    _validated_q6_catalog,
    extract_evidence,
)
import rankmap_drop_model  # noqa: E402
import rebuild_items_droppable  # noqa: E402


OFFICIAL_UPDATE_ITEMS = (
    (1006009, "龙蛋", 1_233_333, 2, 3),
    (1016008, "天空之镜", 1_314_520, 1, 1),
    (1016009, "假日酒店房卡", 888_888, 1, 1),
    (1016010, "海螺留声机", 633_633, 3, 2),
    (1016011, "航海罗盘", 3_615_569, 2, 2),
    (1016012, "珊瑚礁摆件", 3_582_000, 2, 2),
    (1016013, "海王三叉戟", 1_999_999, 2, 3),
    (1016014, "广东人字拖", 888_888, 2, 2),
    (1096011, "“保熟” 西瓜", 713_400, 2, 2),
)
OFFICIAL_UPDATE_ITEM_IDS = frozenset(row[0] for row in OFFICIAL_UPDATE_ITEMS)
DRAGON_EGG_ID = 1006009
SUMMER_ACTIVITY_ITEM_IDS = OFFICIAL_UPDATE_ITEM_IDS - {DRAGON_EGG_ID}
RETIRED_WORLD_CUP_Q6_ITEM_IDS = frozenset(
    {1016007, 1036006, 1036007, 1036008}
)


def _events_for_item(item: FatbeansObservedItem) -> FatbeansCaptureEvents:
    state = FatbeansStateEvent(
        sort_id=3,
        capture_time="2026-07-23 00:00:00",
        message_id=0x0025,
        session_id="official-update-product-path",
        map_id=2401,
        round_index=3,
        skill_reveals=(
            FatbeansSkillReveal(
                skill_id=1001092,
                hero_id=109,
                round_index=3,
                observed_items=(item,),
            ),
        ),
        public_infos=(
            FatbeansPublicInfo(
                info_id=200048,
                map_id=2401,
                value=1,
                value_field=1,
                observed_items=(item,),
            ),
        ),
        action_results=(
            FatbeansActionResult(
                action_id=100135,
                result=None,
                result_field=None,
                observed_items=(item,),
            ),
        ),
    )
    return FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(state,),
    )


@pytest.mark.parametrize(
    ("item_id", "name", "value", "width", "height"),
    OFFICIAL_UPDATE_ITEMS,
)
def test_official_update_item_flows_through_monitor_engine_and_ui(
    item_id: int,
    name: str,
    value: int,
    width: int,
    height: int,
) -> None:
    tables = monitor.load_monitor_tables()
    catalog_item = tables.items[item_id]
    cells = width * height
    assert (
        catalog_item.name,
        catalog_item.quality,
        catalog_item.value,
        catalog_item.shape_w,
        catalog_item.shape_h,
    ) == (name, 6, value, width, height)

    observed = FatbeansObservedItem(
        local_index=11,
        runtime_id=70_000 + item_id,
        item_id=item_id,
        quality=6,
        value=value,
        shape_code=width * 10 + height,
        cells=cells,
    )
    events = _events_for_item(observed)
    state = events.states[0]

    skill_rows = monitor._skill_reveal_rows(events, tables.items)
    detail = skill_rows[0]["revealed_items_detail"][0]
    assert (detail["item_id"], detail["item_name"], detail["value"], detail["cells"]) == (
        item_id,
        name,
        value,
        cells,
    )

    # The same physical item can occur in skill/public/action views. The minimap
    # must merge those views by runtime identity instead of drawing three items.
    grid_items = fatbeans._state_grid_items(state)
    assert len(grid_items) == 1
    grid_item = grid_items[0]
    assert (
        grid_item.runtime_id,
        grid_item.item_id,
        grid_item.quality,
        grid_item.cells,
        grid_item.value,
        grid_item.shape_key,
    ) == (
        observed.runtime_id,
        item_id,
        6,
        cells,
        value,
        str(width * 10 + height),
    )

    minimap = _ui_minimap_contract(
        {
            "phase": "bidding",
            "minimap_grid_items": [
                {
                    "row": 2,
                    "col": 2,
                    "width": width,
                    "height": height,
                    "quality": 6,
                    "item_id": item_id,
                    "item_name": name,
                    "shape_key": str(width * 10 + height),
                    "cells": cells,
                    "value": value,
                    "runtime_id": observed.runtime_id,
                    "local_index": observed.local_index,
                    "source": "skill_reveal",
                }
            ],
        }
    )
    ui_item = minimap["items"][0]
    assert (
        ui_item["item_id"],
        ui_item["item_name"],
        ui_item["cells"],
        ui_item["value"],
        ui_item["render_mode"],
    ) == (item_id, name, cells, value, "footprint")

    revealed = {
        "runtime_id": observed.runtime_id,
        "local_index": observed.local_index,
        "item_id": item_id,
        "quality": 6,
        "value": value,
        "shape_code": width * 10 + height,
        "cells": cells,
    }
    evidence = extract_evidence(
        {
            "ui_contract": {
                "context": {
                    "hero": "gabriela",
                    "map_id": 2401,
                    "round": 3,
                    "phase": "bidding",
                },
                "constraints": {"public_info": {}},
            },
            "structured_ref_inputs": {"total_count": 34},
            "skill_reveals": [
                {
                    "hero_id": 104,
                    "skill_id": 1001041,
                    "observed_items": [dict(revealed)],
                }
            ],
            "public_info_rows": [
                {
                    "info_id": 200048,
                    "revealed_items_detail": [dict(revealed)],
                }
            ],
            "action_result_rows": [
                {
                    "action_id": 100135,
                    "revealed_items_detail": [dict(revealed)],
                }
            ],
        }
    )
    assert evidence.min_counts["q6"] == 1
    assert evidence.quality_cell_floors["q6"] == cells
    assert evidence.quality_cell_floor_item_counts["q6"] == 1
    assert evidence.quality_value_floors["q6"] == value
    assert evidence.quality_value_floor_item_counts["q6"] == 1
    assert _known_quality_value_total_floor(evidence) == value


def test_unknown_new_item_id_fails_open_without_fake_catalog_truth() -> None:
    tables = monitor.load_monitor_tables()
    unknown_id = 1_999_999
    assert unknown_id not in tables.items

    observed = FatbeansObservedItem(
        local_index=19,
        runtime_id=99_999,
        item_id=unknown_id,
        quality=6,
        value=None,
        shape_code=None,
        cells=None,
    )
    events = _events_for_item(observed)

    detail = monitor._skill_reveal_rows(events, tables.items)[0][
        "revealed_items_detail"
    ][0]
    assert detail["item_id"] == unknown_id
    assert detail["item_name"] == ""
    assert detail["value"] is None
    assert detail["shape_code"] is None
    assert detail["cells"] is None

    # A real quality observation may stay as a one-cell marker for display, but
    # catalog-derived identity, footprint, and value must remain unknown.
    grid_items = fatbeans._state_grid_items(events.states[0])
    assert len(grid_items) == 1
    assert grid_items[0].item_id == unknown_id
    assert grid_items[0].quality == 6
    assert grid_items[0].shape_key is None
    assert grid_items[0].value is None

    evidence = extract_evidence(
        {
            "ui_contract": {
                "context": {
                    "hero": "gabriela",
                    "map_id": 2401,
                    "round": 3,
                    "phase": "bidding",
                },
                "constraints": {"public_info": {}},
            },
            "structured_ref_inputs": {"total_count": 34},
            "skill_reveals": [
                {
                    "hero_id": 104,
                    "skill_id": 1001041,
                    "observed_items": [
                        {
                            "runtime_id": observed.runtime_id,
                            "local_index": observed.local_index,
                            "item_id": unknown_id,
                            "quality": 6,
                            "value": None,
                            "shape_code": None,
                            "cells": None,
                        }
                    ],
                }
            ],
        }
    )
    assert evidence.min_counts["q6"] == 1
    assert evidence.quality_cell_floors.get("q6") is None
    assert evidence.quality_value_floors.get("q6") is None
    assert (evidence.quality_catalog_min_value_floors or {}).get("q6") is None


def test_all_nine_update_items_enter_formal_q6_catalog() -> None:
    q4_pairs, q4_rows, q4_status = _validated_q4_catalog()
    q5_pairs, q5_rows, q5_status = _validated_q5_catalog()
    q6_pairs, q6_rows, q6_status = _validated_q6_catalog()

    assert (q4_status, q5_status, q6_status) == ("ok", "ok", "ok")
    assert q4_pairs and q4_rows
    assert q5_pairs and q5_rows
    assert q6_pairs and q6_rows
    q6_item_ids = {item_id for item_id, _value, _cells in q6_rows}
    assert OFFICIAL_UPDATE_ITEM_IDS <= q6_item_ids
    assert RETIRED_WORLD_CUP_Q6_ITEM_IDS.isdisjoint(q6_item_ids)

    full_catalog = json.loads(
        (ROOT / "data" / "processed" / "items.json").read_text(encoding="utf-8")
    )
    full_catalog_ids = {
        int(row["item_id"])
        for row in (
            full_catalog
            if isinstance(full_catalog, list)
            else full_catalog.values()
        )
    }
    assert RETIRED_WORLD_CUP_Q6_ITEM_IDS <= full_catalog_ids


def test_world_cup_canonical_ids_are_red_and_localization_aliases_are_not_rows() -> None:
    catalog = json.loads(
        (ROOT / "data" / "processed" / "items.json").read_text(encoding="utf-8")
    )
    by_id = {int(row["item_id"]): row for row in catalog}
    expected = {
        1016007: (758_000, 2, 2, [101, 107]),
        1036006: (7_202_026, 2, 3, [103]),
        1036007: (555_555, 3, 4, [103, 107]),
        1036008: (1_225_000, 2, 3, [103, 101]),
    }

    assert {
        item_id: (
            by_id[item_id]["value"],
            by_id[item_id]["shape_w"],
            by_id[item_id]["shape_h"],
            by_id[item_id]["tags"],
        )
        for item_id in expected
    } == expected
    assert all(by_id[item_id]["quality"] == 6 for item_id in expected)
    assert {1046010, 1046011, 1046012}.isdisjoint(by_id)


def test_all_nine_update_items_are_in_every_modeled_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "BIDKING_DISABLE_DRAGON_EGG_ALL_MAPS",
        "BIDKING_DISABLE_SUMMER_PARTY_2026_COLLECTIBLES",
        "BIDKING_ENABLE_RETIRED_WORLD_CUP_Q6_COLLECTIBLES",
        "RANKMAP_NO_PLACEMENT",
        "RANKMAP_VALUE_CEILING",
    ):
        monkeypatch.delenv(name, raising=False)

    rank_maps = {row[0] for row in rankmap_drop_model._load_b64("RankMap.txt")}
    memory_maps = set(
        json.loads(
            (
                ROOT / "data" / "processed" / "dropmap_mem_0625.json"
            ).read_text(encoding="utf-8")
        )
    )
    modeled_maps = sorted(
        (rank_maps | memory_maps) - {"6001"},
        key=int,
    )
    assert len(modeled_maps) == 175

    unavailable_maps: set[str] = set()
    nonzero_maps = 0
    checked_maps = 0
    for map_id in modeled_maps:
        try:
            rates, _count, _quality, _value, _name = rankmap_drop_model.drop_rates(
                map_id
            )
        except SystemExit:
            unavailable_maps.add(map_id)
            continue
        checked_maps += 1
        assert OFFICIAL_UPDATE_ITEM_IDS <= rates.keys(), map_id
        update_rates = [rates[item_id] for item_id in OFFICIAL_UPDATE_ITEM_IDS]
        assert all(rate > 0 for rate in update_rates) or all(
            rate == 0 for rate in update_rates
        ), map_id
        nonzero_maps += int(all(rate > 0 for rate in update_rates))
        assert RETIRED_WORLD_CUP_Q6_ITEM_IDS.isdisjoint(rates), map_id
    assert unavailable_maps == {str(map_id) for map_id in range(5101, 5111)}
    assert checked_maps == 165
    # Nine q6 items are eligible on every modeled map. Seven existing map tiers
    # carry zero q6 mass, so their per-item q6 rates correctly remain zero instead
    # of this lifecycle change silently increasing the map's overall red rate.
    assert nonzero_maps == 158


def test_update_collectible_lifecycle_switches_are_orthogonal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BIDKING_DISABLE_SUMMER_PARTY_2026_COLLECTIBLES", "1")
    ordinary, *_ = rankmap_drop_model.drop_rates(2401)
    ranked, *_ = rankmap_drop_model.drop_rates(5601)
    assert DRAGON_EGG_ID in ordinary
    assert DRAGON_EGG_ID in ranked
    assert SUMMER_ACTIVITY_ITEM_IDS.isdisjoint(ordinary)
    assert SUMMER_ACTIVITY_ITEM_IDS.isdisjoint(ranked)

    monkeypatch.delenv("BIDKING_DISABLE_SUMMER_PARTY_2026_COLLECTIBLES")
    monkeypatch.setenv("BIDKING_DISABLE_DRAGON_EGG_ALL_MAPS", "1")
    ordinary, *_ = rankmap_drop_model.drop_rates(2401)
    ranked, *_ = rankmap_drop_model.drop_rates(5601)
    assert DRAGON_EGG_ID not in ordinary
    assert DRAGON_EGG_ID in ranked
    assert SUMMER_ACTIVITY_ITEM_IDS <= ordinary.keys()
    assert SUMMER_ACTIVITY_ITEM_IDS <= ranked.keys()

    monkeypatch.delenv("BIDKING_DISABLE_DRAGON_EGG_ALL_MAPS")
    ordinary, *_ = rankmap_drop_model.drop_rates(2401)
    assert RETIRED_WORLD_CUP_Q6_ITEM_IDS.isdisjoint(ordinary)
    monkeypatch.setenv("BIDKING_ENABLE_RETIRED_WORLD_CUP_Q6_COLLECTIBLES", "1")
    ordinary_with_retired, *_ = rankmap_drop_model.drop_rates(2401)
    assert RETIRED_WORLD_CUP_Q6_ITEM_IDS <= ordinary_with_retired.keys()


def test_summer_party_window_is_explicit_and_not_wall_clock_driven() -> None:
    assert (
        rankmap_drop_model.SUMMER_PARTY_2026_START_BEIJING
        == "2026-07-23T10:00:00+08:00"
    )
    assert (
        rankmap_drop_model.SUMMER_PARTY_2026_END_BEIJING
        == "2026-09-03T10:00:00+08:00"
    )
    assert rankmap_drop_model.SUMMER_PARTY_2026_ITEM_IDS == SUMMER_ACTIVITY_ITEM_IDS
    assert (
        rankmap_drop_model.RETIRED_WORLD_CUP_Q6_ITEM_IDS
        == RETIRED_WORLD_CUP_Q6_ITEM_IDS
    )


def test_catalog_lifecycle_switches_match_weight_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for item_id in RETIRED_WORLD_CUP_Q6_ITEM_IDS:
        assert not rebuild_items_droppable.droppable_item(item_id, 101)
    for item_id in SUMMER_ACTIVITY_ITEM_IDS:
        assert rebuild_items_droppable.droppable_item(item_id, 101)

    monkeypatch.setenv(
        "BIDKING_ENABLE_RETIRED_WORLD_CUP_Q6_COLLECTIBLES", "1"
    )
    monkeypatch.setenv("BIDKING_DISABLE_SUMMER_PARTY_2026_COLLECTIBLES", "1")
    for item_id in RETIRED_WORLD_CUP_Q6_ITEM_IDS:
        assert rebuild_items_droppable.droppable_item(item_id, 101)
    for item_id in SUMMER_ACTIVITY_ITEM_IDS:
        assert not rebuild_items_droppable.droppable_item(item_id, 101)
