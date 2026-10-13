from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import bidking_lab.inference.v2 as v2_module
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.v2 import (
    EvidenceFact,
    EvidenceStoreBuilder,
    PHYSICAL_GOLD_AGGREGATE_EXCLUSION_DISABLE,
    RuntimeEvidence,
    evidence_store_from_fatbeans_events,
    physical_candidate_domains_from_store,
    physical_red_shape_rule_matrix,
)
from bidking_lab.live.fatbeans import (
    FatbeansActionResult,
    FatbeansObservedItem,
    FatbeansPublicInfo,
    FatbeansSkillReveal,
    FatbeansStateEvent,
)


def _item(
    item_id: int,
    *,
    quality: int,
    value: int,
    shape: tuple[int, int],
    categories: tuple[int, ...],
) -> Item:
    return Item(
        item_id=item_id,
        name=f"item_{item_id}",
        description="",
        name_key=f"item_{item_id}",
        desc_key=f"item_{item_id}_desc",
        quality=quality,
        quality_color="red" if quality == 6 else "other",
        value=value,
        shape_w=shape[0],
        shape_h=shape[1],
        tags=list(categories),
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )


def _domain(
    monkeypatch: pytest.MonkeyPatch,
    *,
    evidence: RuntimeEvidence,
    rows: dict[int, tuple[int, int, int, int, tuple[int, ...]]],
    facts: tuple[EvidenceFact, ...] = (),
):
    monkeypatch.setattr(
        v2_module,
        "_validated_physical_category_catalog",
        lambda: (
            rows,
            {5608: frozenset(rows)},
            "ok",
            "physical-category-v1:dynamic-test",
        ),
    )
    items = {
        item_id: _item(
            item_id,
            quality=row[0],
            value=row[1],
            shape=(row[2], row[3]),
            categories=row[4],
        )
        for item_id, row in rows.items()
    }
    builder = EvidenceStoreBuilder()
    builder.add_item(evidence)
    for fact in facts:
        builder.add_fact(fact)
    build = physical_candidate_domains_from_store(
        5608,
        builder.build(),
        items=items,
    )
    assert build.catalog_status == "ok"
    assert len(build.domains) == 1
    return build.domains[0]


def test_current_catalog_red_shape_matrix_is_generated_not_hand_maintained() -> None:
    build = physical_red_shape_rule_matrix(5608)
    build_2401 = physical_red_shape_rule_matrix(2401)

    assert build.catalog_status == "ok"
    assert build.catalog_generation.startswith("physical-category-v1:")
    # Complete multi-category authority adds the real 5608 overlap that the
    # legacy single-category memory could not express.
    # eb1 adds seven reachable category/shape combinations.  The count is
    # generation-bound to the current 702/711 modeled supports, not a hand list.
    assert len(build.rules) == 83
    by_key = {
        (rule.category, rule.shape_key): rule
        for rule in build.rules
    }
    home_2x2 = by_key[(101, "22")]
    antique_4x4 = by_key[(106, "44")]
    digital_3x5 = by_key[(107, "35")]
    home_1x2 = by_key[(101, "12")]
    assert home_2x2.candidate_qualities == (1, 2, 3, 4, 6)
    assert home_2x2.required_excluded_qualities == (1, 2, 3, 4)
    assert {1016004, 1116025} <= set(home_2x2.red_candidate_item_ids)
    assert {1916039, 1916054} <= set(home_1x2.red_candidate_item_ids)
    assert home_1x2.required_excluded_qualities == (1, 2, 3, 4)
    assert antique_4x4.candidate_qualities == (3, 6)
    assert antique_4x4.required_excluded_qualities == (3,)
    assert {1016006, 1066006, 1116012, 1916058} == set(
        antique_4x4.red_candidate_item_ids
    )
    assert sum(5 not in rule.candidate_qualities for rule in build.rules) == 30
    assert digital_3x5.required_excluded_qualities == (5,)
    assert digital_3x5.gold_candidate_item_ids == (1075011,)
    assert digital_3x5.gold_candidate_values == (97_382,)
    assert 1076005 in digital_3x5.red_candidate_item_ids
    assert digital_3x5.as_dict()["gold_min_total_cells"] == 15
    assert digital_3x5.as_dict()["gold_min_total_value"] == 97_382
    digital_3x5_2401 = next(
        rule
        for rule in build_2401.rules
        if rule.category == 107 and rule.shape_key == "35"
    )
    assert digital_3x5_2401.gold_candidate_item_ids == (1075011,)
    assert digital_3x5_2401.red_candidate_item_ids == (1076005,)


def test_added_home_box_demotes_black_box_item_lock_but_keeps_one_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = RuntimeEvidence(
        runtime_id=97001,
        shape_key="22",
        cells=4,
        categories=(101,),
        excluded_categories=(106, 107),
        excluded_qualities=(1, 2, 3, 4),
    )
    rows = {
        1016004: (6, 7_402_320, 2, 2, (101,)),
        1116025: (6, 170_362, 2, 2, (101,)),
        1014001: (4, 8_974, 2, 2, (101,)),
    }

    with_box = _domain(monkeypatch, evidence=evidence, rows=rows)
    without_box = _domain(
        monkeypatch,
        evidence=evidence,
        rows={key: value for key, value in rows.items() if key != 1116025},
    )

    assert with_box.exact_quality == 6
    assert with_box.quality_source == "catalog_red_domain"
    assert with_box.candidate_item_ids == (1016004, 1116025)
    assert with_box.exact_item_id is None
    assert with_box.exact_value is None
    assert without_box.exact_quality == 6
    assert without_box.exact_item_id == 1016004
    assert without_box.exact_value == 7_402_320


def test_gold_candidate_blocks_special_red_hardening_even_when_low_tiers_are_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain = _domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=97002,
            shape_key="22",
            cells=4,
            categories=(101,),
            excluded_qualities=(1, 2, 3, 4),
        ),
        rows={
            1: (5, 500_000, 2, 2, (101,)),
            2: (6, 700_000, 2, 2, (101,)),
        },
    )

    assert domain.candidate_qualities == (5, 6)
    assert domain.hard_eligible is False
    assert domain.exact_quality is None
    assert domain.exact_item_id is None


@pytest.mark.parametrize(
    "fact",
    (
        EvidenceFact("public_info", "200011", 14, "public"),
        EvidenceFact("action", "100125", 97_381, "action:100125"),
    ),
)
def test_gold_aggregate_below_server_cabinet_excludes_q5_and_hardens_gpu_red(
    monkeypatch: pytest.MonkeyPatch,
    fact: EvidenceFact,
) -> None:
    domain = _domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=97011,
            shape_key="35",
            cells=15,
            categories=(107,),
            excluded_qualities=(1, 2, 3, 4),
        ),
        rows={
            1075011: (5, 97_382, 3, 5, (107,)),
            1076005: (6, 375_000, 3, 5, (107,)),
        },
        facts=(fact,),
    )

    assert domain.candidate_item_ids == (1076005,)
    assert domain.excluded_qualities == (1, 2, 3, 4, 5)
    assert domain.exact_quality == 6
    assert domain.exact_item_id == 1076005
    assert any(source.startswith("inferred:gold_aggregate_") for source in domain.sources)


def test_gold_singleton_aggregate_requires_same_cells_and_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = (
        EvidenceFact("public_info", "200019", 1, "public"),
        EvidenceFact("public_info", "200011", 15, "public"),
        EvidenceFact("action", "100125", 97_382, "action:100125"),
    )
    domain = _domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=97012,
            shape_key="35",
            cells=15,
            categories=(107,),
            excluded_qualities=(1, 2, 3, 4),
        ),
        rows={
            1075011: (5, 97_382, 3, 5, (107,)),
            1076005: (6, 375_000, 3, 5, (107,)),
        },
        facts=facts,
    )

    assert domain.candidate_qualities == (5, 6)
    assert domain.exact_quality is None


def test_gold_count_and_public_averages_reconstruct_integer_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = (
        EvidenceFact("public_info", "200019", 1, "public"),
        EvidenceFact("public_info", "200015", 14.0, "public"),
        EvidenceFact("public_info", "200037", 97_381.0, "public"),
    )
    domain = _domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=97013,
            shape_key="35",
            cells=15,
            categories=(107,),
            excluded_qualities=(1, 2, 3, 4),
        ),
        rows={
            1075011: (5, 97_382, 3, 5, (107,)),
            1076005: (6, 375_000, 3, 5, (107,)),
        },
        facts=facts,
    )

    assert domain.exact_quality == 6
    assert domain.excluded_qualities == (1, 2, 3, 4, 5)


def test_gold_total_cells_divided_by_avg_cells_derives_count_then_avg_value_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain = _domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=97017,
            shape_key="35",
            cells=15,
            categories=(107,),
            excluded_qualities=(1, 2, 3, 4),
        ),
        rows={
            1075011: (5, 97_382, 3, 5, (107,)),
            1076005: (6, 375_000, 3, 5, (107,)),
        },
        facts=(
            EvidenceFact("public_info", "200011", 30, "public"),
            EvidenceFact("public_info", "200015", 15.0, "public"),
            EvidenceFact("public_info", "200037", 48_000.0, "public"),
        ),
    )

    assert domain.exact_quality == 6
    assert "inferred:gold_aggregate_total_value_below_candidate" in domain.sources


def test_non_integer_gold_average_ratio_does_not_exclude_gold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain = _domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=97018,
            shape_key="35",
            cells=15,
            categories=(107,),
            excluded_qualities=(1, 2, 3, 4),
        ),
        rows={
            1075011: (5, 97_382, 3, 5, (107,)),
            1076005: (6, 375_000, 3, 5, (107,)),
        },
        facts=(
            EvidenceFact("public_info", "200011", 30, "public"),
            EvidenceFact("public_info", "200015", 14.0, "public"),
            EvidenceFact("public_info", "200037", 48_000.0, "public"),
        ),
    )

    assert domain.candidate_qualities == (5, 6)
    assert domain.exact_quality is None


def test_conflicting_gold_aggregate_fails_closed_and_gold_ab_rollback_restores_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = RuntimeEvidence(
        runtime_id=97014,
        shape_key="35",
        cells=15,
        categories=(107,),
        excluded_qualities=(1, 2, 3, 4),
    )
    rows = {
        1075011: (5, 97_382, 3, 5, (107,)),
        1076005: (6, 375_000, 3, 5, (107,)),
    }
    conflict = _domain(
        monkeypatch,
        evidence=evidence,
        rows=rows,
        facts=(
            EvidenceFact("public_info", "200011", 14, "public"),
            EvidenceFact("public_info", "200011", 15, "public"),
        ),
    )
    assert conflict.candidate_qualities == (5, 6)
    assert conflict.exact_quality is None

    monkeypatch.setenv(PHYSICAL_GOLD_AGGREGATE_EXCLUSION_DISABLE, "1")
    rollback = _domain(
        monkeypatch,
        evidence=evidence,
        rows=rows,
        facts=(EvidenceFact("public_info", "200011", 14, "public"),),
    )
    assert rollback.candidate_qualities == (5, 6)
    assert rollback.exact_quality is None


def test_gold_aggregate_ab_changes_only_relevant_domain_and_is_otherwise_byte_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = RuntimeEvidence(
        runtime_id=97019,
        shape_key="35",
        cells=15,
        categories=(107,),
        excluded_qualities=(1, 2, 3, 4),
    )
    rows = {
        1075011: (5, 97_382, 3, 5, (107,)),
        1076005: (6, 375_000, 3, 5, (107,)),
    }
    fact = EvidenceFact("public_info", "200011", 14, "public")
    default = _domain(
        monkeypatch,
        evidence=evidence,
        rows=rows,
        facts=(fact,),
    )
    monkeypatch.setenv(PHYSICAL_GOLD_AGGREGATE_EXCLUSION_DISABLE, "1")
    rollback = _domain(
        monkeypatch,
        evidence=evidence,
        rows=rows,
        facts=(fact,),
    )
    assert default.exact_quality == 6
    assert rollback.exact_quality is None

    monkeypatch.delenv(PHYSICAL_GOLD_AGGREGATE_EXCLUSION_DISABLE)
    unrelated_default = _domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=97020,
            shape_key="54",
            cells=20,
            categories=(107,),
        ),
        rows={1916002: (6, 1_012_000, 5, 4, (107,))},
    )
    monkeypatch.setenv(PHYSICAL_GOLD_AGGREGATE_EXCLUSION_DISABLE, "1")
    unrelated_rollback = _domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=97020,
            shape_key="54",
            cells=20,
            categories=(107,),
        ),
        rows={1916002: (6, 1_012_000, 5, 4, (107,))},
    )
    encode = lambda domain: json.dumps(  # noqa: E731 - exact A/B byte probe
        domain.as_dict(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert encode(unrelated_default) == encode(unrelated_rollback)


def test_antique_4x4_book_miss_tracks_box_and_needs_home_hit_for_jade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {
        1016006: (6, 361_000, 4, 4, (101, 106, 110)),
        1066006: (6, 844_000, 4, 4, (101, 106)),
        1116012: (6, 361_000, 4, 4, (106,)),
    }
    antique_book_miss = _domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=97003,
            quality=6,
            shape_key="44",
            cells=16,
            categories=(106,),
            excluded_categories=(110,),
        ),
        rows=rows,
    )
    plus_home = _domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=97003,
            quality=6,
            shape_key="44",
            cells=16,
            categories=(101, 106),
            excluded_categories=(110,),
        ),
        rows=rows,
    )

    assert antique_book_miss.candidate_item_ids == (1066006, 1116012)
    assert antique_book_miss.exact_item_id is None
    assert plus_home.candidate_item_ids == (1066006,)
    assert plus_home.exact_item_id == 1066006


def test_tatiana_home_red_2x2_antique_hit_is_compass_and_miss_is_slipper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {
        1016011: (6, 3_615_569, 2, 2, (101, 103, 106)),
        1016014: (6, 888_888, 2, 2, (101, 103)),
        1036003: (6, 266_050, 2, 2, (103, 110)),
    }
    compass = _domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=97004,
            quality=6,
            shape_key="22",
            cells=4,
            categories=(101, 103, 106),
        ),
        rows=rows,
    )
    slipper = _domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=97005,
            quality=6,
            shape_key="22",
            cells=4,
            categories=(101, 103),
            excluded_categories=(106,),
        ),
        rows=rows,
    )

    assert compass.candidate_item_ids == (1016011,)
    assert compass.exact_item_id == 1016011
    assert slipper.candidate_item_ids == (1016014,)
    assert slipper.exact_item_id == 1016014


def _quality_negative_state(
    *,
    session_id: str,
    runtime_id: int,
    action_order: tuple[int, ...],
    use_aisha: bool,
) -> FatbeansStateEvent:
    target = FatbeansObservedItem(2, runtime_id, None, None, None, 22, 4)
    actions = tuple(
        FatbeansActionResult(
            action_id,
            None,
            None,
            (target,) if action_id == 100151 else (),
        )
        for action_id in action_order
    )
    if use_aisha:
        skills = tuple(
            FatbeansSkillReveal(skill_id, 103, quality, None, None, ())
            for quality, skill_id in enumerate(
                (1001034, 1001033, 1001032, 1001031),
                start=1,
            )
        )
        public_infos: tuple[FatbeansPublicInfo, ...] = ()
    else:
        skills = (FatbeansSkillReveal(10010801, 108, 1, None, None, ()),)
        public_infos = (FatbeansPublicInfo(200001, 5608, 0, 7, ()),)
    return FatbeansStateEvent(
        sort_id=1,
        capture_time="",
        message_id=0x0025,
        session_id=session_id,
        map_id=5608,
        round_index=4,
        action_results=actions,
        skill_reveals=skills,
        public_infos=public_infos,
    )


@pytest.mark.parametrize(
    "action_order",
    ((100151, 100156, 100157), (100157, 100156, 100151)),
)
def test_aisha_quality_and_category_misses_are_order_independent_and_atomic(
    action_order: tuple[int, ...],
) -> None:
    state = _quality_negative_state(
        session_id="5608:new",
        runtime_id=97006,
        action_order=action_order,
        use_aisha=True,
    )
    store = evidence_store_from_fatbeans_events(SimpleNamespace(states=(state,)))
    evidence = store.by_runtime[97006]

    assert evidence.categories == (101,)
    assert evidence.excluded_categories == (106, 107)
    assert evidence.excluded_qualities == (1, 2, 3, 4)
    assert evidence.conflicts == ()


def test_maria_low_tiers_plus_public_purple_create_the_same_quality_exclusions() -> None:
    state = _quality_negative_state(
        session_id="5608:new",
        runtime_id=97007,
        action_order=(100151,),
        use_aisha=False,
    )
    store = evidence_store_from_fatbeans_events(SimpleNamespace(states=(state,)))

    assert store.by_runtime[97007].excluded_qualities == (1, 2, 3, 4)


def test_complete_public_gold_outline_miss_is_atomic_q5_exclusion_and_gold_total_is_retained() -> None:
    target = FatbeansObservedItem(2, 97015, None, None, None, 35, 15)
    state = FatbeansStateEvent(
        sort_id=1,
        capture_time="",
        message_id=0x0025,
        session_id="5608:new",
        map_id=5608,
        round_index=4,
        action_results=(
            FatbeansActionResult(100157, None, None, (target,)),
            FatbeansActionResult(100125, 97_381, None, ()),
        ),
        public_infos=(FatbeansPublicInfo(200002, 5608, 0, 8, ()),),
    )

    store = evidence_store_from_fatbeans_events(SimpleNamespace(states=(state,)))

    assert store.by_runtime[97015].categories == (107,)
    assert store.by_runtime[97015].excluded_qualities == (5,)
    assert any(
        fact.kind == "action" and fact.key == "100125" and fact.value == 97_381
        for fact in store.facts
    )


def test_aisha_gold_tools_total_avg_and_value_flow_into_dynamic_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = FatbeansObservedItem(2, 97021, None, None, None, 35, 15)
    state = FatbeansStateEvent(
        sort_id=1,
        capture_time="",
        message_id=0x0025,
        session_id="5608:new",
        map_id=5608,
        round_index=4,
        action_results=(
            FatbeansActionResult(100157, None, None, (target,)),
            FatbeansActionResult(100107, 30, None, ()),
            FatbeansActionResult(100113, 15.0, None, ()),
            FatbeansActionResult(100125, 96_000, None, ()),
        ),
        skill_reveals=tuple(
            FatbeansSkillReveal(skill_id, 103, quality, None, None, ())
            for quality, skill_id in enumerate(
                (1001034, 1001033, 1001032, 1001031),
                start=1,
            )
        ),
    )
    store = evidence_store_from_fatbeans_events(SimpleNamespace(states=(state,)))
    rows = {
        1075011: (5, 97_382, 3, 5, (107,)),
        1076005: (6, 375_000, 3, 5, (107,)),
    }
    monkeypatch.setattr(
        v2_module,
        "_validated_physical_category_catalog",
        lambda: (
            rows,
            {5608: frozenset(rows)},
            "ok",
            "physical-category-v1:dynamic-test",
        ),
    )
    items = {
        item_id: _item(
            item_id,
            quality=row[0],
            value=row[1],
            shape=(row[2], row[3]),
            categories=row[4],
        )
        for item_id, row in rows.items()
    }

    domain = physical_candidate_domains_from_store(
        5608,
        store,
        items=items,
    ).domains[0]

    assert store.by_runtime[97021].categories == (107,)
    assert store.by_runtime[97021].excluded_qualities == (1, 2, 3, 4)
    assert {fact.key for fact in store.facts if fact.kind == "action"} >= {
        "100107",
        "100113",
        "100125",
    }
    assert domain.exact_quality == 6
    assert domain.exact_item_id == 1076005
    assert "inferred:gold_aggregate_total_value_below_candidate" in domain.sources


@pytest.mark.parametrize("reverse", (False, True))
def test_same_session_cross_frame_positive_and_negative_evidence_is_order_independent(
    reverse: bool,
) -> None:
    target = FatbeansObservedItem(2, 97016, None, None, None, 35, 15)
    shape_state = FatbeansStateEvent(
        sort_id=1,
        capture_time="",
        message_id=0x0025,
        session_id="5608:new",
        map_id=5608,
        round_index=1,
        action_results=(FatbeansActionResult(100157, None, None, (target,)),),
    )
    negative_state = FatbeansStateEvent(
        sort_id=2,
        capture_time="",
        message_id=0x0025,
        session_id="5608:new",
        map_id=5608,
        round_index=4,
        action_results=(
            FatbeansActionResult(100156, None, None, ()),
            FatbeansActionResult(100151, None, None, ()),
        ),
        skill_reveals=tuple(
            FatbeansSkillReveal(skill_id, 103, quality, None, None, ())
            for quality, skill_id in enumerate(
                (1001034, 1001033, 1001032, 1001031),
                start=1,
            )
        ),
        public_infos=(FatbeansPublicInfo(200002, 5608, 0, 8, ()),),
    )
    states = (negative_state, shape_state) if reverse else (shape_state, negative_state)

    store = evidence_store_from_fatbeans_events(SimpleNamespace(states=states))
    evidence = store.by_runtime[97016]

    assert evidence.categories == (107,)
    assert evidence.excluded_categories == (101, 106)
    assert evidence.excluded_qualities == (1, 2, 3, 4, 5)
    assert evidence.conflicts == ()


def test_quality_negative_evidence_never_crosses_sessions() -> None:
    old = _quality_negative_state(
        session_id="5608:old",
        runtime_id=97008,
        action_order=(100151,),
        use_aisha=True,
    )
    new = _quality_negative_state(
        session_id="5608:new",
        runtime_id=97009,
        action_order=(100151,),
        use_aisha=False,
    )
    events = SimpleNamespace(states=(old, new))

    scoped = evidence_store_from_fatbeans_events(events, session_id="5608:new")
    ambiguous = evidence_store_from_fatbeans_events(events)

    assert set(scoped.by_runtime) == {97009}
    assert scoped.by_runtime[97009].excluded_qualities == (1, 2, 3, 4)
    assert ambiguous.by_runtime == {}


def test_observed_and_excluded_same_quality_conflict_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain = _domain(
        monkeypatch,
        evidence=RuntimeEvidence(
            runtime_id=97010,
            quality=4,
            shape_key="22",
            cells=4,
            categories=(101,),
            excluded_qualities=(4,),
        ),
        rows={1: (4, 40_000, 2, 2, (101,))},
    )

    assert domain.hard_eligible is False
    assert domain.exact_quality is None
    assert domain.exact_item_id is None
