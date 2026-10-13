from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from urllib.request import urlopen

import pytest

import bidking_lab.inference.v2 as v2_module
from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.v2 import (
    ConditionalSampler,
    PHYSICAL_CATEGORY_DOMAIN_DISABLE,
    EvidenceStoreBuilder,
    PhysicalCandidateDomain,
    RuntimeEvidence,
    build_residual_problem,
    evidence_store_from_fatbeans_events,
    physical_candidate_domains_from_store,
)
from bidking_lab.live.fatbeans import (
    FatbeansActionResult,
    FatbeansObservedItem,
    FatbeansStateEvent,
    _state_grid_items,
)
from bidking_lab.live.monitor import _ahmad_ref_inputs_from_batches
from bidking_lab.live.types import GridItemObservation, LiveObservationBatch


def _item(item_id: int, *, tags: list[int]) -> Item:
    return Item(
        item_id=item_id,
        name=f"item_{item_id}",
        description="",
        name_key=f"item_{item_id}",
        desc_key=f"item_{item_id}_desc",
        quality=6,
        quality_color="red",
        value=item_id * 10,
        shape_w=2,
        shape_h=2,
        tags=tags,
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )


def _domain_item(
    item_id: int,
    *,
    quality: int,
    value: int,
    shape: tuple[int, int],
    tags: list[int],
) -> Item:
    item = _item(item_id, tags=tags)
    return item.model_copy(
        update={
            "quality": quality,
            "value": value,
            "shape_w": shape[0],
            "shape_h": shape[1],
        }
    )


def _map_and_pool(items: list[Item]) -> tuple[dict[int, BidMap], dict[int, DropPool]]:
    bid_map = BidMap(
        map_id=2401,
        name="atomic-category-test",
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
    pool = DropPool(
        pool_id=9001,
        name="pool",
        description="",
        pool_type=2,
        entries=[
            DropEntry(
                category=item.tags[0] if item.tags else 100,
                item_id=item.item_id,
                n_min=1,
                n_max=1,
                weight=1,
            )
            for item in items
        ],
    )
    return {2401: bid_map}, {9001: pool}


def _intersection_anchors(categories: tuple[int, ...]) -> tuple[int, ...]:
    fashion_only = _item(601, tags=[103])
    antique_one = _item(602, tags=[106])
    antique_two = _item(603, tags=[106])
    items = {
        item.item_id: item
        for item in (fashion_only, antique_one, antique_two)
    }
    maps, drops = _map_and_pool(list(items.values()))
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=90001,
            quality=6,
            shape_key="22",
            cells=4,
            categories=categories,
            sources=("action:100153", "action:100156"),
        )
    )
    anchors = v2_module._unique_shape_item_anchors(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
        existing_keys=set(),
    )
    return tuple(anchor.item_id for anchor in anchors)


def test_multicategory_shape_domain_never_promotes_a_single_category_unique() -> None:
    assert _intersection_anchors((103, 106)) == ()


def test_multicategory_shape_domain_is_order_independent() -> None:
    assert _intersection_anchors((106, 103)) == ()


def test_atomic_intersection_promotes_only_the_all_category_singleton() -> None:
    candidates = [
        _item(701, tags=[103]),
        _item(702, tags=[106]),
        _item(703, tags=[103, 106]),
    ]
    items = {item.item_id: item for item in candidates}
    maps, drops = _map_and_pool(candidates)
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=90002,
            quality=6,
            shape_key="22",
            cells=4,
            categories=(106, 103),
        )
    )

    anchors = v2_module._unique_shape_item_anchors(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
        existing_keys=set(),
    )

    assert tuple(anchor.item_id for anchor in anchors) == (703,)
    assert anchors[0].categories == (103, 106)


def _controlled_domain(
    monkeypatch: pytest.MonkeyPatch,
    *,
    evidence: RuntimeEvidence,
    rows: dict[int, tuple[int, int, int, int, tuple[int, ...]]],
    reachable: set[int] | None = None,
):
    generation = "physical-category-v1:test-generation"
    monkeypatch.setattr(
        v2_module,
        "_validated_physical_category_catalog",
        lambda: (
            rows,
            {5608: frozenset(rows if reachable is None else reachable)},
            "ok",
            generation,
        ),
    )
    items = {
        item_id: _domain_item(
            item_id,
            quality=row[0],
            value=row[1],
            shape=(row[2], row[3]),
            tags=list(row[4]),
        )
        for item_id, row in rows.items()
    }
    builder = EvidenceStoreBuilder()
    builder.add_item(evidence)
    return physical_candidate_domains_from_store(
        5608,
        builder.build(),
        items=items,
    )


def test_digital_5x4_catalog_red_domain_hardens_quality_and_keeps_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {
        801: (6, 1_012_000, 5, 4, (107,)),
        802: (6, 843_200, 5, 4, (107,)),
    }
    unknown_quality = _controlled_domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=91001,
            shape_key="54",
            cells=20,
            categories=(107,),
        ),
        rows=rows,
    ).domains[0]
    exact_red = _controlled_domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=91001,
            quality=6,
            shape_key="54",
            cells=20,
            categories=(107,),
        ),
        rows=rows,
    ).domains[0]

    assert unknown_quality.candidate_qualities == (6,)
    assert unknown_quality.exact_quality == 6
    assert unknown_quality.quality_source == "catalog_red_domain"
    assert unknown_quality.hard_eligible is True
    assert unknown_quality.exact_item_id is None
    assert unknown_quality.exact_value is None
    assert exact_red.exact_quality == 6
    assert exact_red.exact_item_id is None
    assert exact_red.exact_value is None
    assert exact_red.candidate_values == (843_200, 1_012_000)


def test_catalog_inferred_red_ambiguous_support_enters_posterior_like_observed_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        _domain_item(803, quality=6, value=843_200, shape=(5, 4), tags=[]),
        _domain_item(804, quality=6, value=1_012_000, shape=(5, 4), tags=[]),
    ]
    rows = {
        item.item_id: (
            item.quality,
            item.value,
            item.shape_w,
            item.shape_h,
            (107,),
        )
        for item in candidates
    }
    monkeypatch.setattr(
        v2_module,
        "_validated_physical_category_catalog",
        lambda: (
            rows,
            {2401: frozenset(rows)},
            "ok",
            "physical-category-v1:test-generation",
        ),
    )
    items = {item.item_id: item for item in candidates}
    maps, drops = _map_and_pool(candidates)

    def problem(quality: int | None):
        builder = EvidenceStoreBuilder()
        builder.add_item(
            RuntimeEvidence(
                runtime_id=91008,
                quality=quality,
                shape_key="54",
                cells=20,
                categories=(107,),
            )
        )
        return build_residual_problem(
            2401,
            builder.build(),
            maps=maps,
            drops=drops,
            items=items,
        )

    exact_problem = problem(6)
    unknown_problem = problem(None)
    assert exact_problem.anchors == ()
    assert exact_problem.category_targets[0].candidate_item_ids == (803, 804)
    assert unknown_problem.anchors == ()
    assert unknown_problem.category_targets[0].quality == 6
    assert unknown_problem.category_targets[0].candidate_item_ids == (803, 804)

    truth = ConditionalSampler(
        exact_problem,
        maps=maps,
        drops=drops,
        items=items,
    ).sample()
    assert truth.buckets[6].items[0].item_id in {803, 804}


def test_digital_3x5_preserves_gold_red_counterexample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain = _controlled_domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=91002,
            shape_key="35",
            cells=15,
            categories=(107,),
        ),
        rows={
            811: (5, 97_382, 3, 5, (107,)),
            812: (6, 375_000, 3, 5, (107,)),
        },
    ).domains[0]

    assert domain.candidate_qualities == (5, 6)
    assert domain.exact_quality is None
    assert domain.exact_item_id is None


def test_fashion_antique_q6_2x2_uses_map_reachable_atomic_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain = _controlled_domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=91003,
            quality=6,
            shape_key="22",
            cells=4,
            categories=(106, 103),
        ),
        rows={
            821: (6, 3_615_569, 2, 2, (101, 103, 106)),
            822: (6, 999_999, 2, 2, (103, 106)),
            823: (6, 888_888, 2, 2, (103,)),
        },
        reachable={821, 823},
    ).domains[0]

    assert domain.required_categories == (103, 106)
    assert domain.candidate_item_ids == (821,)
    assert domain.exact_item_id == 821
    assert domain.exact_value == 3_615_569


def test_exact_excluded_categories_participate_in_the_same_atomic_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain = _controlled_domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=91005,
            quality=6,
            shape_key="22",
            cells=4,
            excluded_categories=(102,),
        ),
        rows={
            824: (6, 120_000, 2, 2, (102,)),
            825: (6, 260_000, 2, 2, (106,)),
        },
    ).domains[0]

    assert domain.required_categories == ()
    assert domain.excluded_categories == (102,)
    assert domain.candidate_item_ids == (825,)
    assert domain.exact_item_id == 825


def test_controlled_catalog_categories_fill_missing_live_item_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {826: (6, 1_012_000, 5, 4, (107,))}
    monkeypatch.setattr(
        v2_module,
        "_validated_physical_category_catalog",
        lambda: (
            rows,
            {5608: frozenset({826})},
            "ok",
            "physical-category-v1:test-generation",
        ),
    )
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=91006,
            quality=6,
            shape_key="54",
            cells=20,
            categories=(107,),
        )
    )
    live_item = _domain_item(
        826,
        quality=6,
        value=1_012_000,
        shape=(5, 4),
        tags=[],
    )

    domain = physical_candidate_domains_from_store(
        5608,
        builder.build(),
        items={826: live_item},
    ).domains[0]

    assert live_item.tags == []
    assert domain.exact_item_id == 826


@pytest.mark.parametrize(
    "catalog_status",
    ("catalog_unavailable", "catalog_duplicate_or_invalid_item", "catalog_generation_mismatch"),
)
def test_catalog_missing_duplicate_or_generation_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    catalog_status: str,
) -> None:
    monkeypatch.setattr(
        v2_module,
        "_validated_physical_category_catalog",
        lambda: ({}, {}, catalog_status, ""),
    )
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=91007,
            quality=6,
            shape_key="22",
            cells=4,
            categories=(103, 106),
        )
    )

    out = physical_candidate_domains_from_store(
        5608,
        builder.build(),
        items={},
    )

    assert out.domains == ()
    assert out.catalog_status == catalog_status


def test_partial_or_conflicting_identity_never_becomes_hard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {831: (6, 500_000, 2, 2, (103, 106))}
    partial = _controlled_domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            quality=6,
            shape_key="22",
            cells=4,
            categories=(103, 106),
        ),
        rows=rows,
    ).domains[0]
    conflicting = _controlled_domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=91004,
            quality=6,
            shape_key="22",
            cells=4,
            categories=(103, 106),
            conflicts=("conflicting_quality",),
        ),
        rows=rows,
    ).domains[0]

    assert partial.hard_eligible is False
    assert partial.exact_quality is None
    assert partial.exact_item_id is None
    assert conflicting.hard_eligible is False
    assert conflicting.exact_quality is None
    assert conflicting.exact_item_id is None


def test_runtime_merge_is_order_independent_and_records_conflicts() -> None:
    left = RuntimeEvidence(
        runtime_id=92001,
        quality=5,
        shape_key="22",
        cells=4,
        categories=(106,),
    )
    right = RuntimeEvidence(
        runtime_id=92001,
        quality=6,
        shape_key="22",
        cells=4,
        categories=(103,),
    )

    assert left.merge(right).categories == (103, 106)
    assert right.merge(left).categories == (103, 106)
    assert left.merge(left).conflicts == ()
    assert "conflicting_quality" in left.merge(right).conflicts


def test_event_store_scopes_categories_to_one_session_and_runtime() -> None:
    def state(sort_id: int, session_id: str, action_id: int, runtime_id: int):
        item = FatbeansObservedItem(2, runtime_id, None, None, None, 22, 4)
        return FatbeansStateEvent(
            sort_id=sort_id,
            capture_time="",
            message_id=0x0025,
            session_id=session_id,
            map_id=5608,
            round_index=1,
            action_results=(FatbeansActionResult(action_id, None, None, (item,)),),
        )

    events = SimpleNamespace(
        states=(
            state(1, "5608:old", 100153, 93001),
            state(2, "5608:new", 100153, 93002),
            state(3, "5608:new", 100156, 93002),
            state(4, "5608:new", 100156, 93003),
        )
    )
    store = evidence_store_from_fatbeans_events(events, session_id="5608:new")
    ambiguous = evidence_store_from_fatbeans_events(events)

    assert set(store.by_runtime) == {93002, 93003}
    assert store.by_runtime[93002].categories == (103, 106)
    assert store.by_runtime[93003].categories == (106,)
    assert ambiguous.by_runtime == {}


def test_live_grid_keeps_all_categories_independent_of_action_order() -> None:
    item = FatbeansObservedItem(2, 93501, None, None, None, 22, 4)

    def categories(action_ids: tuple[int, ...]) -> tuple[int, ...]:
        state = FatbeansStateEvent(
            sort_id=1,
            capture_time="",
            message_id=0x0025,
            session_id="5608:test",
            map_id=5608,
            round_index=1,
            action_results=tuple(
                FatbeansActionResult(action_id, None, None, (item,))
                for action_id in action_ids
            ),
        )
        row = next(
            row for row in _state_grid_items(state) if row.runtime_id == 93501
        )
        return row.categories

    assert categories((100156, 100153)) == (103, 106)
    assert categories((100153, 100156)) == (103, 106)


def test_rollback_disables_physical_domain_byte_stably(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PHYSICAL_CATEGORY_DOMAIN_DISABLE, "1")
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=94001,
            quality=6,
            shape_key="22",
            cells=4,
            categories=(103, 106),
        )
    )

    out = physical_candidate_domains_from_store(
        5608,
        builder.build(),
        items={},
    )

    assert out.domains == ()
    assert out.catalog_status == "rollback_disabled"

    batch = LiveObservationBatch(
        source="packet",
        event_kind="action_result",
        phase="bidding",
        session_id="5608:test",
        grid_items=(
            GridItemObservation(
                cells=4,
                source="packet",
                confidence="exact",
                runtime_id=94001,
                quality=6,
                shape_key="22",
                local_index=2,
                category=103,
                categories=(103, 106),
            ),
        ),
    )
    bridge = _ahmad_ref_inputs_from_batches(
        (batch,),
        hero="aisha",
        items={},
        session_id="5608:test",
    )
    assert "physical_candidate_domains" not in bridge
    assert "categories" not in bridge["minimap_items"][0]
    assert "category_labels" not in bridge["minimap_items"][0]
    rollback_wire = json.dumps(
        bridge,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    assert rollback_wire == (
        '{"avg_cells":{},"avg_values":{},"count_sums":{},"counts":{},'
        '"field_updates":[],"hero":"aisha","minimap_items":[{"category":103,'
        '"cells":4,"col":3,"height":2,"item_id":null,"local_index":2,'
        '"quality":6,"row":1,"shape_key":"22","value":null,"width":2}],'
        '"quality_cells":{},"quality_value_floors":{},"quality_values":{},'
        '"scoped_count_sums":[],"source":"monitor_pre_settlement_field_updates",'
        '"split_avg_cells":{},"split_counts":{},"split_quality_cells":{},'
        '"total_avg_cells":null,"total_cells":null,"total_count":null}'
    )


def test_domain_is_identical_in_direct_summary_api_panel_and_tk(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    for path in (root / "hero_ref" / "src", root / "hero_ref" / "tools"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from ahmad_live_panel_server import (  # noqa: PLC0415
        ServerConfig,
        make_handler,
        summarize_snapshot,
    )
    from ahmad_ref_engine import run_reference_engine  # noqa: PLC0415
    from ahmad_tk_overlay import _summary_diagnostic_row  # noqa: PLC0415

    gpu_cabinet = _domain_item(
        1076005,
        quality=6,
        value=375_000,
        shape=(3, 5),
        tags=[107],
    )
    domain = PhysicalCandidateDomain(
        key="runtime:95001",
        runtime_id=95001,
        local_index=2,
        observed_quality=None,
        shape_key="35",
        cells=15,
        required_categories=(107,),
        excluded_categories=(),
        excluded_qualities=(1, 2, 3, 4, 5),
        candidate_item_ids=(1076005,),
        candidate_qualities=(6,),
        candidate_values=(375_000,),
        catalog_status="ok",
        catalog_generation="physical-category-v1:test-generation",
        hard_eligible=True,
        sources=(
            "action:100157",
            "skill_quality_negative:1001034",
            "public_quality_negative:200002",
        ),
    )
    batch = LiveObservationBatch(
        source="packet",
        event_kind="action_result",
        phase="bidding",
        session_id="5608:test",
        grid_items=(
            GridItemObservation(
                cells=15,
                source="packet",
                confidence="exact",
                runtime_id=95001,
                quality=None,
                shape_key="35",
                local_index=2,
                category=107,
                categories=(107,),
                excluded_qualities=(1, 2, 3, 4, 5),
            ),
        ),
    )
    bridge = _ahmad_ref_inputs_from_batches(
        (batch,),
        hero="aisha",
        items={1076005: gpu_cabinet},
        session_id="5608:test",
        physical_candidate_domains=(domain,),
    )
    domain_row = domain.as_dict()
    assert bridge["physical_candidate_domains"] == [domain_row]
    assert bridge["minimap_items"][0]["quality"] == 6
    assert bridge["minimap_items"][0]["quality_source"] == "catalog_red_domain"
    assert bridge["minimap_items"][0]["item_id"] == 1076005
    assert bridge["minimap_items"][0]["value"] == 375_000
    assert bridge["minimap_items"][0]["category_labels"] == ["数码"]
    bridge["total_count"] = 3

    snapshot = {
        "hero": "aisha",
        "map_id": 5608,
        "phase": "bidding",
        "session_id": "5608:test",
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 5608,
                "phase": "bidding",
            },
            "constraints": {
                "public_info": {
                    "public_avg_values": [
                        {
                            "semantic": "q6_avg_value",
                            "kind": "avg_value",
                            "quality": 6,
                            "value": 375_000,
                        }
                    ]
                }
            },
        },
        "structured_ref_inputs": bridge,
        "physical_candidate_domains": [domain_row],
        "minimap_grid_items": bridge["minimap_items"],
    }
    for max_combos in (8_000, 50_000):
        direct = run_reference_engine(
            snapshot,
            max_combos=max_combos,
        ).as_dict()
        assert direct["evidence"]["physical_candidate_domains"] == [domain_row]
        assert direct["evidence"]["fixed_counts"].get("q6") is None
        assert {
            row["count"]
            for row in direct["evidence"]["quality_count_cells_options"]["q6"]
        } == {1, 2, 3}
        assert direct["evidence"]["q6_known_item_facts"] == [
            {"identity": "local:2", "cells": 15, "value": 375_000}
        ]

    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False),
        encoding="utf-8",
    )
    summary = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(
            ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - self-created loopback product contract
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=10,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
    tk_row = _summary_diagnostic_row(
        payload,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )

    assert summary["ahmed_ref"]["evidence"][
        "physical_candidate_domains"
    ] == [domain_row]
    assert payload["ahmed_ref"]["evidence"][
        "physical_candidate_domains"
    ] == [domain_row]
    assert tk_row["ref_v0"]["evidence"][
        "physical_candidate_domains"
    ] == [domain_row]
    for surface in (summary["ahmed_ref"], payload["ahmed_ref"], tk_row["ref_v0"]):
        assert surface["evidence"]["fixed_counts"].get("q6") is None
        assert {
            row["count"]
            for row in surface["evidence"]["quality_count_cells_options"]["q6"]
        } == {1, 2, 3}
        assert surface["evidence"]["q6_known_item_facts"] == [
            {"identity": "local:2", "cells": 15, "value": 375_000}
        ]
    tooltip = summary["minimap"]["items"][0]["tooltip"]
    assert "类别 数码" in tooltip
    assert "鉴影域推断红" in tooltip
    assert "排除品质 白/绿/蓝/紫/金" in tooltip


def test_reference_engine_rejects_conflicting_domain_sources() -> None:
    root = Path(__file__).resolve().parents[1]
    if str(root / "hero_ref" / "src") not in sys.path:
        sys.path.insert(0, str(root / "hero_ref" / "src"))
    from ahmad_ref_engine import extract_evidence  # noqa: PLC0415

    domain = PhysicalCandidateDomain(
        key="runtime:96001",
        runtime_id=96001,
        local_index=2,
        observed_quality=6,
        shape_key="54",
        cells=20,
        required_categories=(107,),
        excluded_categories=(),
        candidate_item_ids=(861, 862),
        candidate_qualities=(6,),
        candidate_values=(843_200, 1_012_000),
        catalog_status="ok",
        catalog_generation="physical-category-v1:test-generation",
        hard_eligible=True,
    ).as_dict()
    conflicting = dict(domain)
    conflicting["candidate_item_ids"] = [861]
    snapshot = {
        "hero": "aisha",
        "physical_candidate_domains": [domain],
        "structured_ref_inputs": {
            "hero": "aisha",
            "physical_candidate_domains": [conflicting],
        },
    }

    assert extract_evidence(snapshot).physical_candidate_domains == ()
