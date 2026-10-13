from __future__ import annotations

import json
from pathlib import Path

import pytest

from bidking_lab.extract.bid_map_table import (
    BID_MAP_UPDATE606_CANONICAL_SHA256,
    BID_MAP_UPDATE606_GENERATION,
    BidMap,
)
from bidking_lab.extract.current_rankmap_package_contract import (
    CURRENT_BUILDIN_CATALOG_BYTES,
    CURRENT_BUILDIN_CATALOG_JSON,
    CURRENT_PACKAGE_GENERATION,
    CURRENT_PACKAGE_HASH_FILE,
    CURRENT_RANKMAP_MAP_IDS,
    CURRENT_SCRIPTS,
    CURRENT_STEAM_BUILD_ID,
    CURRENT_TEXT_ASSET_IDENTITIES,
    RAVEN_CHAOTIC_SPACE_MAP_IDS,
    RAVEN_CHAOTIC_SPACE_POOL_IDS,
    RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON,
)
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import (
    Item,
    ITEM_TABLE_UPDATE606_CANONICAL_SHA256,
    ITEM_TABLE_UPDATE606_ROWS,
)
from bidking_lab.live.monitor import (
    MonitorTables,
    RAVEN_TEMPLATE_ESTIMATE_ALIAS_MODE,
    RAVEN_TEMPLATE_ESTIMATE_DISABLE_ENV,
    RAVEN_TEMPLATE_ESTIMATE_LABEL,
    _raven_chaotic_space_support_receipt,
    _raven_preopen_template_alias,
    build_monitor_artifact_from_events,
)
from bidking_lab.live.fatbeans import (
    FatbeansCaptureEvents,
    FatbeansPlayerBid,
    FatbeansStateEvent,
)
from bidking_lab.runtime.snapshot import (
    _ui_map_alias_label,
    project_raven_template_alias_receipt,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT / "tests" / "fixtures" / "raven_chaotic_space_d66aee_contract.json"
)


def _fixture() -> dict[str, object]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


def _map(map_id: int) -> BidMap:
    return BidMap(
        map_id=map_id,
        name="错乱时空·拉文",
        description="拉文活动",
        category=501,
        auction_mode="sealed",
        sub_pool_weights=(
            [
                (5101, 10),
                (5102, 10),
                (5102, 10),
                (5103, 10),
                (5104, 10),
                (5105, 10),
                (5106, 10),
                (5107, 10),
                (5108, 10),
                (5109, 10),
                (5110, 10),
            ]
            if map_id == 5101
            else []
        ),
        col11_value_tier_scale_basis_points=3000,
        rounds_total=30,
        entry_fee_silver=25_000,
        starting_budget_silver=5_000_000,
        drop_pool_id=map_id - 100,
        items_per_session_min=22,
        items_per_session_max=44,
        value_tier_ui="ui_value_high",
        mode_flag=4,
        bid_price_ladder=[2000, 1600, 1300, 1100, 0],
        round_category_hints=[30105, 0, 0, 0, 0],
        raw_row=[],
    )


def _tables(*maps: BidMap) -> MonitorTables:
    return MonitorTables(
        maps={row.map_id: row for row in maps},
        drops={},
        items={},
    )


def _template_item() -> Item:
    return Item(
        item_id=1001,
        name="模板藏品",
        description="",
        name_key="template_item",
        desc_key="template_item_desc",
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


def _template_model_map(map_id: int) -> BidMap:
    return BidMap(
        map_id=map_id,
        name=f"旧沉船模板{map_id}",
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


def _estimated_tables() -> MonitorTables:
    item = _template_item()
    maps = {
        row.map_id: row
        for row in (
            *(_map(map_id) for map_id in RAVEN_CHAOTIC_SPACE_MAP_IDS),
            *(
                _template_model_map(map_id)
                for map_id in range(2501, 2511)
            ),
        )
    }
    return MonitorTables(
        maps=maps,
        drops={
            9001: DropPool(
                pool_id=9001,
                name="template_pool",
                description="",
                pool_type=2,
                entries=[
                    DropEntry(
                        category=107,
                        item_id=item.item_id,
                        n_min=1,
                        n_max=1,
                        weight=1,
                    )
                ],
            )
        },
        items={item.item_id: item},
    )


def test_d66aee_fixture_matches_source_authority() -> None:
    fixture = _fixture()
    assert fixture["steam_build_id"] == CURRENT_STEAM_BUILD_ID == "24830618"
    assert (
        fixture["package_generation"]
        == CURRENT_PACKAGE_GENERATION
        == BID_MAP_UPDATE606_GENERATION
        == "d66aee-2026-08-20-599"
    )
    assert fixture["yooasset_artifacts"] == {
        "BuildinCatalog.bytes": CURRENT_BUILDIN_CATALOG_BYTES,
        "BuildinCatalog.json": CURRENT_BUILDIN_CATALOG_JSON,
        "DefaultPackage.hash": CURRENT_PACKAGE_HASH_FILE,
    }
    scripts = fixture["scripts"]
    assert type(scripts) is dict
    assert scripts == CURRENT_SCRIPTS
    identities = fixture["text_assets"]
    assert type(identities) is dict
    assert set(identities) == set(CURRENT_TEXT_ASSET_IDENTITIES)
    for name, identity in CURRENT_TEXT_ASSET_IDENTITIES.items():
        assert identities[name] == {
            "bytes": identity.utf8_size,
            "columns": identity.columns,
            "rows": identity.rows,
            "sha256": identity.utf8_sha256,
        }
    assert ITEM_TABLE_UPDATE606_ROWS == 1_336
    assert (
        ITEM_TABLE_UPDATE606_CANONICAL_SHA256
        == "7acfa973b2e8f5ff1bca5545057658f00c4e57ccff8e115cc9c5661bc8854db4"
    )
    assert (
        BID_MAP_UPDATE606_CANONICAL_SHA256
        == "bc351df9b9b2c905aa1ecc0f96f74e6f9f2af845dde0b4ba049e6af5cc10eb6b"
    )


def test_raven_scope_is_not_promoted_to_exact_current_rankmap_authority() -> None:
    fixture = _fixture()
    mapping = fixture["mapping"]
    assert type(mapping) is dict
    assert tuple(mapping["logical_map_ids"]) == RAVEN_CHAOTIC_SPACE_MAP_IDS
    assert tuple(mapping["pool_ids"]) == RAVEN_CHAOTIC_SPACE_POOL_IDS
    assert not set(RAVEN_CHAOTIC_SPACE_MAP_IDS) & set(CURRENT_RANKMAP_MAP_IDS)
    assert not set(RAVEN_CHAOTIC_SPACE_POOL_IDS) & set(CURRENT_RANKMAP_MAP_IDS)
    qualification = fixture["qualification"]
    assert type(qualification) is dict
    assert qualification == {
        "model_authority": False,
        "quote_available": False,
        "reason": RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON,
    }


@pytest.mark.parametrize(
    ("map_id", "model_map_id", "roles", "entry_count", "raw_weight"),
    [
        (5101, 2501, ["root", "self"], 1, 10),
        (5102, 2502, ["leaf"], 2, 20),
        (5110, 2510, ["leaf"], 1, 10),
    ],
)
def test_preopen_alias_preserves_selector_roles_without_exact_rate_claim(
    map_id: int,
    model_map_id: int,
    roles: list[str],
    entry_count: int,
    raw_weight: int,
) -> None:
    alias = _raven_preopen_template_alias(
        map_id,
        _estimated_tables(),
        enabled=True,
    )

    assert alias["mode"] == RAVEN_TEMPLATE_ESTIMATE_ALIAS_MODE
    assert alias["display_label"] == RAVEN_TEMPLATE_ESTIMATE_LABEL
    assert alias["source_map_id"] == map_id
    assert alias["model_map_id"] == model_map_id
    assert alias["estimated"] is True
    assert alias["observed_raven_games"] == 0
    assert alias["pool_graph_claim"] is False
    assert alias["exact_rate_claim"] is False
    assert alias["source_generation"] == CURRENT_PACKAGE_GENERATION
    assert alias["model_input_mode"] == "current_runtime_weights_priors_alias"
    assert alias["direct_pool_proxy_mode"] == "shadow_probe_only"
    assert alias["direct_pool_proxy_quote_authority"] is False
    selector = alias["selector"]
    assert selector["root_map_id"] == 5101
    assert selector["selected_roles"] == roles
    assert selector["selected_entry_count"] == entry_count
    assert selector["selected_raw_weight"] == raw_weight
    assert selector["raw_total_weight"] == 110
    assert [
        (entry["map_id"], entry["weight"])
        for entry in selector["raw_entries"]
    ] == [
        (5101, 10),
        (5102, 10),
        (5102, 10),
        (5103, 10),
        (5104, 10),
        (5105, 10),
        (5106, 10),
        (5107, 10),
        (5108, 10),
        (5109, 10),
        (5110, 10),
    ]


def test_preopen_alias_is_scope_atomic_and_fail_closed() -> None:
    tables = _estimated_tables()
    maps = dict(tables.maps)
    maps[5109] = _map(5109).model_copy(update={"category": 104})
    drifted = MonitorTables(maps=maps, drops=tables.drops, items=tables.items)

    assert _raven_preopen_template_alias(5101, drifted, enabled=True) == {}
    assert _raven_preopen_template_alias(5101, tables, enabled=False) == {}
    assert _raven_preopen_template_alias(5101, tables, enabled=1) == {}
    assert _raven_preopen_template_alias(True, tables, enabled=True) == {}


class _RavenStrSubclass(str):
    pass


class _RavenIntSubclass(int):
    pass


class _RavenAliasDictSubclass(dict):
    pass


def _valid_ui_alias_receipt() -> dict[str, object]:
    return {
        "mode": RAVEN_TEMPLATE_ESTIMATE_ALIAS_MODE,
        "display_label": RAVEN_TEMPLATE_ESTIMATE_LABEL,
        "source_map_id": 5102,
        "model_map_id": 2502,
        "estimated": True,
        "observed_raven_games": 0,
        "pool_graph_claim": False,
        "exact_rate_claim": False,
        "source_generation": CURRENT_PACKAGE_GENERATION,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_generation", "wrong-generation"),
        ("source_generation", _RavenStrSubclass(CURRENT_PACKAGE_GENERATION)),
        ("source_generation", True),
        ("source_map_id", 5506),
        ("source_map_id", True),
        ("source_map_id", _RavenIntSubclass(5102)),
        ("model_map_id", 2503),
        ("model_map_id", False),
        ("model_map_id", _RavenIntSubclass(2502)),
        ("observed_raven_games", False),
    ],
)
def test_ui_consumer_rejects_wrong_or_unstable_raven_receipt_fields(
    field: str,
    value: object,
) -> None:
    alias = _valid_ui_alias_receipt()
    alias[field] = value

    assert project_raven_template_alias_receipt(alias) == {}
    assert _ui_map_alias_label({"map_alias": alias}) == ""


@pytest.mark.parametrize(
    "missing_field",
    ["source_generation", "source_map_id", "model_map_id"],
)
def test_ui_consumer_rejects_missing_raven_identity_field(
    missing_field: str,
) -> None:
    alias = _valid_ui_alias_receipt()
    del alias[missing_field]

    assert project_raven_template_alias_receipt(alias) == {}
    assert _ui_map_alias_label({"map_alias": alias}) == ""


def test_ui_consumer_accepts_only_exact_raven_pair_and_plain_dict() -> None:
    alias = _valid_ui_alias_receipt()

    assert project_raven_template_alias_receipt(alias) == {
        "source_map_id": 5102,
        "model_map_id": 2502,
        "source_generation": CURRENT_PACKAGE_GENERATION,
    }
    assert _ui_map_alias_label({"map_alias": alias}) == "模板估算 · 5102→2502"
    assert project_raven_template_alias_receipt(
        _RavenAliasDictSubclass(alias),
        claimed_mode=RAVEN_TEMPLATE_ESTIMATE_ALIAS_MODE,
    ) == {}
    assert _ui_map_alias_label(
        {
            "map_alias": _RavenAliasDictSubclass(alias),
            "map_alias_mode": RAVEN_TEMPLATE_ESTIMATE_ALIAS_MODE,
        }
    ) == ""


@pytest.mark.parametrize("map_id", RAVEN_CHAOTIC_SPACE_MAP_IDS)
def test_exact_raven_map_projects_fixed_unavailable_receipt(map_id: int) -> None:
    receipt = _raven_chaotic_space_support_receipt(map_id, _tables(_map(map_id)))
    assert receipt == {
        "schema_version": 1,
        "status": "recognized_unavailable",
        "reason": RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON,
        "activity_id": 40002,
        "activity_map_id": 20002,
        "hero_id": 301,
        "map_group_id": 501,
        "map_id": map_id,
        "model_map_id": map_id,
        "pool_id": map_id - 100,
        "model_authority": False,
        "quote_available": False,
        "first_live_session_status": "pending",
    }


@pytest.mark.parametrize(
    "replacement",
    [
        {"category": 104},
        {"drop_pool_id": 5101},
        {"items_per_session_min": 21},
        {"items_per_session_max": 45},
        {"round_category_hints": [100301, 0, 0, 0, 0]},
        {"sub_pool_weights": [(5101, 10), (5102, 20)]},
    ],
)
def test_raven_mapping_drift_is_conflict_not_quote(
    replacement: dict[str, object],
) -> None:
    row = _map(5101).model_copy(update=replacement)
    receipt = _raven_chaotic_space_support_receipt(5101, _tables(row))
    assert receipt["status"] == "mapping_conflict"
    assert receipt["reason"] == RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON
    assert receipt["model_authority"] is False
    assert receipt["quote_available"] is False


def test_missing_raven_map_is_conflict_and_neighbor_is_ignored() -> None:
    receipt = _raven_chaotic_space_support_receipt(5101, _tables())
    assert receipt["status"] == "mapping_conflict"
    assert receipt["quote_available"] is False
    assert _raven_chaotic_space_support_receipt(5506, _tables()) == {}
    assert _raven_chaotic_space_support_receipt(True, _tables()) == {}


def test_raven_monitor_product_path_is_recognized_but_never_quotes() -> None:
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
                session_id="5101:raven-preopen",
                map_id=5101,
                round_index=1,
                bids=(
                    FatbeansPlayerBid(
                        player_id=1,
                        name="player",
                        hero_id=301,
                        values=(100_000,),
                    ),
                ),
            ),
        ),
    )

    artifact = build_monitor_artifact_from_events(
        events,
        file="raven-5101-preopen.json",
        tables=_tables(_map(5101)),
        n_trials=10,
        roi_trials=0,
        formal_mode="v3_practical",
    )

    assert artifact["map_id"] == 5101
    assert artifact["formal_mode_reason"] == (
        RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON
    )
    assert artifact["bid_rows"] == []
    assert artifact["v3_practical_bid_rows"] == []
    assert artifact["inference_input_constraints"] == {
        "mode": RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON,
        "map_id": 5101,
        "model_map_id": 5101,
        "activity_id": 40002,
        "activity_map_id": 20002,
        "hero_id": 301,
        "map_group_id": 501,
        "mapping_status": "recognized_unavailable",
        "quote_available": False,
        "first_live_session_status": "pending",
    }
    assert artifact["raven_chaotic_space_support"]["status"] == (
        "recognized_unavailable"
    )
    assert artifact["raven_chaotic_space_support"]["quote_available"] is False


def _raven_events(map_id: int) -> FatbeansCaptureEvents:
    return FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=1,
                capture_time="",
                message_id=0x0025,
                session_id=f"{map_id}:raven-template",
                map_id=map_id,
                round_index=1,
                bids=(
                    FatbeansPlayerBid(
                        player_id=1,
                        name="player",
                        hero_id=301,
                        values=(100_000,),
                    ),
                ),
            ),
        ),
    )


def test_raven_monitor_uses_labeled_template_estimate_without_pool_claim() -> None:
    artifact = build_monitor_artifact_from_events(
        _raven_events(5102),
        file="raven-5102-preopen-template.json",
        tables=_estimated_tables(),
        n_trials=10,
        roi_trials=0,
        formal_mode="v3_practical",
    )

    assert artifact["map_id"] == 5102
    assert artifact["model_map_id"] == 2502
    assert artifact["map_alias_mode"] == RAVEN_TEMPLATE_ESTIMATE_ALIAS_MODE
    assert artifact["map_alias"]["estimated"] is True
    assert artifact["map_alias"]["pool_graph_claim"] is False
    assert artifact["map_alias"]["exact_rate_claim"] is False
    assert artifact["map_alias"]["observed_raven_games"] == 0
    assert artifact["map_alias"]["source_generation"] == CURRENT_PACKAGE_GENERATION
    assert artifact["raven_chaotic_space_support"]["status"] == (
        "estimated_template"
    )
    assert artifact["raven_chaotic_space_support"]["quote_available"] is True
    assert artifact["evidence_label"] == "模板估算（5102→2502）"
    assert artifact["bid_rows"]
    assert artifact["ui_contract"]["constraints"]["summary"][
        "map_alias_label"
    ] == "模板估算 · 5102→2502"


def test_raven_template_kill_switch_restores_recognized_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(RAVEN_TEMPLATE_ESTIMATE_DISABLE_ENV, "1")

    artifact = build_monitor_artifact_from_events(
        _raven_events(5102),
        file="raven-5102-template-disabled.json",
        tables=_estimated_tables(),
        n_trials=10,
        roi_trials=0,
        formal_mode="v3_practical",
    )

    assert artifact["map_alias"] == {}
    assert artifact["model_map_id"] == 5102
    assert artifact["bid_rows"] == []
    assert artifact["formal_mode_reason"] == (
        RAVEN_CHAOTIC_SPACE_QUALIFICATION_REASON
    )
    assert artifact["raven_chaotic_space_support"]["status"] == (
        "recognized_unavailable"
    )
