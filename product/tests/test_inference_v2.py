from __future__ import annotations

import numpy as np

import bidking_lab.inference.v2 as v2_module
from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import BucketTruth, SessionTruth
from bidking_lab.inference.observation import (
    CategoryItemObservation,
    QualityBucketObs,
    SessionObs,
)
from bidking_lab.inference.v2 import (
    ConditionalSampler,
    EvidenceFact,
    EvidenceStoreBuilder,
    RuntimeEvidence,
    actionable_size_avg_value_targets,
    build_residual_problem,
    cell_evidence_score,
    decision_value_for_truth,
    estimate_posterior_v2,
    evidence_store_from_fatbeans_events,
    global_evidence_score,
    known_footprints,
    known_item_anchors,
    layout_feasibility_from_store,
    layout_feasibility_score,
    q6_decision_value_for_truth,
    q6_tail_replacement_decision_value_for_truth,
    tail_replacement_decision_value_for_truth,
    size_avg_value_evidence_score,
    value_evidence_score,
)
from bidking_lab.live.fatbeans import (
    FatbeansActionResult,
    FatbeansCaptureEvents,
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
    tags: list[int] | None = None,
) -> Item:
    w, h = shape
    return Item(
        item_id=item_id,
        name=f"item_{item_id}",
        description="",
        name_key=f"item_{item_id}",
        desc_key=f"item_{item_id}_desc",
        quality=quality,
        quality_color="",
        value=value,
        shape_w=w,
        shape_h=h,
        tags=tags or [],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )


def _map() -> BidMap:
    return BidMap(
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
        items_per_session_min=2,
        items_per_session_max=2,
        value_tier_ui="",
        mode_flag=4,
        bid_price_ladder=[],
        raw_row=[],
    )


def _tables() -> tuple[dict[int, BidMap], dict[int, DropPool], dict[int, Item]]:
    anchor = _item(1103006, quality=3, value=3_240, shape=(2, 1), tags=[110])
    filler = _item(1011001, quality=1, value=100, shape=(1, 1), tags=[101])
    gold = _item(1055001, quality=5, value=30_000, shape=(1, 1), tags=[105])
    red = _item(1086001, quality=6, value=444_000, shape=(4, 4), tags=[108])
    return (
        {2401: _map()},
        {
            9001: DropPool(
                pool_id=9001,
                name="pool",
                description="",
                pool_type=2,
                entries=[
                    DropEntry(
                        category=110,
                        item_id=anchor.item_id,
                        n_min=1,
                        n_max=1,
                        weight=1,
                    ),
                    DropEntry(
                        category=101,
                        item_id=filler.item_id,
                        n_min=1,
                        n_max=1,
                        weight=999,
                    ),
                    DropEntry(
                        category=105,
                        item_id=gold.item_id,
                        n_min=1,
                        n_max=1,
                        weight=1,
                    ),
                    DropEntry(
                        category=108,
                        item_id=red.item_id,
                        n_min=1,
                        n_max=1,
                        weight=1,
                    ),
                ],
            ),
        },
        {
            anchor.item_id: anchor,
            filler.item_id: filler,
            gold.item_id: gold,
            red.item_id: red,
        },
    )


def test_evidence_store_from_fatbeans_merges_item_and_category_runtime() -> None:
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
                        info_id=200022,
                        map_id=2401,
                        value=4,
                        value_field=11,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=22,
                                runtime_id=123,
                                item_id=1103006,
                                quality=3,
                                value=3_240,
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
                                local_index=22,
                                runtime_id=123,
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
    _maps, _drops, items = _tables()

    store = evidence_store_from_fatbeans_events(events)
    anchors = known_item_anchors(store, items=items)

    assert len(store.by_runtime) == 1
    assert store.by_runtime[123].categories == (110,)
    assert len(anchors) == 1
    assert anchors[0].item_id == 1103006
    assert anchors[0].cells == 2
    assert anchors[0].categories == (110,)


def test_runtime_evidence_merge_uses_latest_layout_position() -> None:
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=1,
            local_index=0,
            shape_key="22",
            cells=4,
            sources=("skill:r1",),
        )
    )
    builder.add_item(
        RuntimeEvidence(
            runtime_id=2,
            local_index=0,
            shape_key="22",
            cells=4,
            sources=("skill:r1",),
        )
    )
    builder.add_item(
        RuntimeEvidence(
            runtime_id=1,
            local_index=20,
            shape_key="22",
            cells=4,
            sources=("skill:r2",),
        )
    )

    store = builder.build()
    layout = layout_feasibility_from_store(store)

    assert store.by_runtime[1].local_index == 20
    assert layout.overlap_cells == 0
    assert layout.trusted_footprint_count == 2


def test_quality_only_runtime_move_uses_latest_local_without_duplicate_floor() -> None:
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            local_index=12,
            quality=6,
            sources=("skill:baoguang:r1",),
        )
    )
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            local_index=47,
            quality=6,
            sources=("skill:baoguang:r2",),
        )
    )

    store = builder.build()
    layout = layout_feasibility_from_store(store)

    assert store.by_runtime[123].local_index == 47
    assert v2_module._quality_evidence_floors(store) == {6: (1, 0, 0)}
    assert layout.footprint_count == 0


def test_category_action_absence_adds_runtime_negative_category() -> None:
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
                        skill_id=1002081,
                        hero_id=208,
                        round_index=1,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=22,
                                runtime_id=123,
                                item_id=None,
                                quality=6,
                                value=None,
                                shape_code=21,
                                cells=None,
                            ),
                        ),
                    ),
                ),
                action_results=(
                    FatbeansActionResult(
                        action_id=100152,  # 医疗药品
                        result=None,
                        result_field=None,
                        observed_items=(),
                    ),
                ),
            ),
        ),
    )

    store = evidence_store_from_fatbeans_events(events)

    assert store.by_runtime[123].excluded_categories == (102,)
    assert "action_negative:100152" in store.by_runtime[123].sources


def test_wuqilin_skill_reveal_becomes_antique_category_evidence() -> None:
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

    store = evidence_store_from_fatbeans_events(events)

    assert store.by_runtime[123].categories == (106,)
    assert "skill:10002071" in store.by_runtime[123].sources


def test_category_action_matches_known_shape_by_local_key() -> None:
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
                        skill_id=1002081,
                        hero_id=208,
                        round_index=1,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=22,
                                runtime_id=123,
                                item_id=None,
                                quality=6,
                                value=None,
                                shape_code=21,
                                cells=None,
                            ),
                        ),
                    ),
                ),
                action_results=(
                    FatbeansActionResult(
                        action_id=100152,
                        result=None,
                        result_field=None,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=22,
                                runtime_id=None,
                                item_id=None,
                                quality=6,
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

    store = evidence_store_from_fatbeans_events(events)

    assert store.by_runtime[123].excluded_categories == ()
    assert not any(
        "action_negative:100152" in item.sources
        for item in store.items()
    )


def test_unique_shape_anchor_fails_closed_when_catalog_identity_mismatches() -> None:
    maps, _drops, _items = _tables()
    medical = _item(1026001, quality=6, value=120_000, shape=(2, 1), tags=[102])
    antique = _item(1066001, quality=6, value=260_000, shape=(2, 1), tags=[106])
    drops = {
        9001: DropPool(
            pool_id=9001,
            name="pool",
            description="",
            pool_type=2,
            entries=[
                DropEntry(
                    category=102,
                    item_id=medical.item_id,
                    n_min=1,
                    n_max=1,
                    weight=1,
                ),
                DropEntry(
                    category=106,
                    item_id=antique.item_id,
                    n_min=1,
                    n_max=1,
                    weight=1,
                ),
            ],
        ),
    }
    items = {medical.item_id: medical, antique.item_id: antique}
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            quality=6,
            shape_key="21",
            cells=2,
            excluded_categories=(102,),
            sources=("skill:1002081", "action_negative:100152"),
        )
    )

    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )

    assert problem.anchors == ()
    assert problem.physical_category_catalog_status == "catalog_item_identity_mismatch"


def test_conditional_sampler_forces_known_item_anchor() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            item_id=1103006,
            quality=3,
            shape_key="21",
            cells=2,
            categories=(110,),
            sources=("public:200022",),
        )
    )
    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)

    for _ in range(20):
        truth = sampler.sample(rng=np.random.default_rng(1))
        q3 = truth.buckets[3]
        assert any(item.item_id == 1103006 for item in q3.items)
        assert truth.total_value() >= 3_240


def test_conditional_sampler_honors_exact_total_item_count() -> None:
    maps, drops, items = _tables()
    obs = SessionObs(
        map_id=2401,
        hero="ethan",
        total_item_count=2,
        buckets={3: QualityBucketObs(quality=3, count_min=1)},
    )
    problem = build_residual_problem(
        2401,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
        obs=obs,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)

    truth = sampler.sample(rng=np.random.default_rng(4))

    assert sum(bucket.count for bucket in truth.buckets.values()) == 2


def test_conditional_sampler_honors_exact_total_count_and_cells() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            item_id=1103006,
            quality=3,
            shape_key="21",
            cells=2,
            sources=("public:200022",),
        )
    )
    obs = SessionObs(
        map_id=2401,
        hero="ethan",
        total_item_count=2,
        warehouse_total_cells=18,
    )
    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
        obs=obs,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)

    truth = sampler.sample(rng=np.random.default_rng(4))

    assert sum(bucket.count for bucket in truth.buckets.values()) == 2
    assert truth.warehouse_total_cells == 18
    assert truth.buckets[6].count == 1


def test_conditional_sampler_honors_exact_total_cells_without_total_count() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            item_id=1103006,
            quality=3,
            shape_key="21",
            cells=2,
            sources=("public:200022",),
        )
    )
    obs = SessionObs(
        map_id=2401,
        hero="ethan",
        warehouse_total_cells=18,
    )
    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
        obs=obs,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)

    truths = [
        sampler.sample(rng=np.random.default_rng(seed))
        for seed in range(10)
    ]

    assert all(truth.warehouse_total_cells == 18 for truth in truths)
    assert all(
        any(item.item_id == 1103006 for item in truth.buckets[3].items)
        for truth in truths
    )


def test_estimate_posterior_v2_uses_anchor_without_rejection_dead_end() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            item_id=1103006,
            quality=3,
            shape_key="21",
            cells=2,
            categories=(110,),
            sources=("public:200022",),
        )
    )
    obs = SessionObs(
        map_id=2401,
        hero="aisha",
        buckets={3: QualityBucketObs(quality=3, total_cells_min=2, count_min=1)},
    )

    report = estimate_posterior_v2(
        2401,
        obs,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
        n_trials=50,
        seed=1,
    )

    assert report.anchor_count == 1
    assert report.known_value == 3_240
    assert report.n_matched == 50
    assert report.total_value is not None
    assert report.total_value.p10 >= 3_240
    assert report.tail_replacement_decision_value is not None
    assert report.q6_match_rate is not None
    assert report.q6_value is not None
    assert report.q6_decision_value is not None
    assert report.q6_tail_replacement_decision_value is not None
    assert report.q6_count is not None
    assert report.q6_cells is not None
    assert any(
        diagnostic.startswith("q6_unconstrained_low_sample_rate:")
        for diagnostic in report.diagnostics
    )


def test_quality_drop_prior_uses_drop_weights_and_count_ranges() -> None:
    maps, drops, items = _tables()
    sampler = ConditionalSampler(
        build_residual_problem(
            2401,
            EvidenceStoreBuilder().build(),
            maps=maps,
            drops=drops,
            items=items,
        ),
        maps=maps,
        drops=drops,
        items=items,
    )

    prior = sampler.quality_drop_prior(6)

    assert prior is not None
    assert abs(prior.draw_probability - 1 / 1002) < 1e-12
    assert abs(prior.session_probability - (1 - (1001 / 1002) ** 2)) < 1e-12
    assert abs(prior.expected_session_count - (2 / 1002)) < 1e-12
    assert abs(prior.expected_session_cells - (2 * 16 / 1002)) < 1e-12
    assert abs(prior.expected_session_value - (2 * 444_000 / 1002)) < 1e-12


def test_session_drop_prior_uses_drop_weights_and_count_ranges() -> None:
    maps, drops, items = _tables()
    sampler = ConditionalSampler(
        build_residual_problem(
            2401,
            EvidenceStoreBuilder().build(),
            maps=maps,
            drops=drops,
            items=items,
        ),
        maps=maps,
        drops=drops,
        items=items,
    )

    prior = sampler.session_drop_prior()

    assert prior is not None
    expected_cells_per_draw = (2 + 999 + 1 + 16) / 1002
    expected_value_per_draw = (3_240 + 999 * 100 + 30_000 + 444_000) / 1002
    assert abs(prior.expected_session_count - 2) < 1e-12
    assert abs(prior.expected_session_cells - 2 * expected_cells_per_draw) < 1e-12
    assert abs(prior.expected_session_value - 2 * expected_value_per_draw) < 1e-12
    assert prior.expected_session_decision_value == prior.expected_session_value


def test_session_drop_prior_reports_tail_replacement_decision_value() -> None:
    tail = _item(1086002, quality=6, value=1_495_000, shape=(1, 1), tags=[108])
    ordinary = _item(1086006, quality=6, value=93_000, shape=(1, 1), tags=[108])
    maps = {
        2405: BidMap(
            map_id=2405,
            name="tail_prior_map",
            description="",
            category=108,
            auction_mode="open",
            sub_pool_weights=[],
            rounds_total=5,
            entry_fee_silver=0,
            starting_budget_silver=100_000,
            drop_pool_id=9005,
            items_per_session_min=1,
            items_per_session_max=1,
            value_tier_ui="",
            mode_flag=4,
            bid_price_ladder=[],
            raw_row=[],
        )
    }
    drops = {
        9005: DropPool(
            pool_id=9005,
            name="tail_prior_pool",
            description="",
            pool_type=2,
            entries=[
                DropEntry(
                    category=108,
                    item_id=tail.item_id,
                    n_min=1,
                    n_max=1,
                    weight=1,
                ),
                DropEntry(
                    category=108,
                    item_id=ordinary.item_id,
                    n_min=1,
                    n_max=1,
                    weight=3,
                ),
            ],
        )
    }
    items = {tail.item_id: tail, ordinary.item_id: ordinary}
    sampler = ConditionalSampler(
        build_residual_problem(
            2405,
            EvidenceStoreBuilder().build(),
            maps=maps,
            drops=drops,
            items=items,
        ),
        maps=maps,
        drops=drops,
        items=items,
    )

    prior = sampler.session_drop_prior()

    assert prior is not None
    assert abs(prior.expected_session_value - 443_500) < 1e-12
    assert abs(prior.expected_session_decision_value - 69_750) < 1e-12
    assert (
        abs(prior.expected_session_tail_replacement_decision_value - 93_000)
        < 1e-12
    )


def test_q6_residual_boost_only_changes_residual_sampling_weights() -> None:
    maps, drops, items = _tables()
    problem = build_residual_problem(
        2401,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    baseline = ConditionalSampler(problem, maps=maps, drops=drops, items=items)
    boosted = ConditionalSampler(
        problem,
        maps=maps,
        drops=drops,
        items=items,
        q6_residual_boost=100.0,
    )
    pool = baseline._sampler.pools[0]
    q6_mask = np.asarray([item.quality == 6 for item in pool.items])

    base_probs = baseline._residual_probabilities(pool)
    boosted_probs = boosted._residual_probabilities(pool)

    assert abs(float(base_probs[q6_mask].sum()) - 1 / 1002) < 1e-12
    assert float(boosted_probs[q6_mask].sum()) > float(base_probs[q6_mask].sum())
    assert abs(float(boosted_probs.sum()) - 1.0) < 1e-12


def test_q6_residual_value_tilt_preserves_q6_mass_and_prefers_high_value() -> None:
    maps, drops, items = _tables()
    problem = build_residual_problem(
        2401,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    baseline = ConditionalSampler(problem, maps=maps, drops=drops, items=items)
    tilted = ConditionalSampler(
        problem,
        maps=maps,
        drops=drops,
        items=items,
        q6_residual_value_power=0.5,
    )
    low = _item(6201, quality=6, value=120_000, shape=(2, 2))
    high = _item(6202, quality=6, value=480_000, shape=(2, 2))
    other = _item(5201, quality=5, value=90_000, shape=(2, 2))
    pool = type(
        "Pool",
        (),
        {
            "items": [other, low, high],
            "probabilities": np.asarray([1 / 3, 1 / 3, 1 / 3], dtype=np.float64),
            "qualities": np.asarray([5, 6, 6], dtype=np.int64),
        },
    )()
    q6_indexes = [1, 2]
    low_i = 1
    high_i = 2

    base_probs = baseline._residual_probabilities(pool)
    tilted_probs = tilted._residual_probabilities(pool)

    assert (
        abs(float(base_probs[q6_indexes].sum()) - float(tilted_probs[q6_indexes].sum()))
        < 1e-12
    )
    assert (
        tilted_probs[high_i] / tilted_probs[low_i]
        > base_probs[high_i] / base_probs[low_i]
    )
    assert abs(float(tilted_probs.sum()) - 1.0) < 1e-12


def test_q6_residual_prior_floor_sampler_adds_q6_when_enabled() -> None:
    maps, drops, items = _tables()
    problem = build_residual_problem(
        2401,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    sampler = ConditionalSampler(
        problem,
        maps=maps,
        drops=drops,
        items=items,
        q6_residual_prior_floor_ratio=1000.0,
    )

    sampled = sampler.sample(np.random.default_rng(7))

    assert sampled.buckets[6].count >= 1
    assert sampled.buckets[6].total_cells >= 16


def test_q6_residual_prior_cell_floor_can_be_tuned_separately() -> None:
    maps, drops, items = _tables()
    problem = build_residual_problem(
        2401,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    sampler = ConditionalSampler(
        problem,
        maps=maps,
        drops=drops,
        items=items,
        q6_residual_prior_floor_ratio=0.0,
        q6_residual_prior_cell_floor_ratio=1000.0,
    )

    sampled = sampler.sample(np.random.default_rng(7))

    assert sampled.buckets[6].total_cells >= 16


def test_q6_conditional_target_sampler_adds_q6_when_enabled() -> None:
    maps, drops, items = _tables()
    problem = build_residual_problem(
        2401,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    sampler = ConditionalSampler(
        problem,
        maps=maps,
        drops=drops,
        items=items,
        q6_conditional_target_count=1.0,
        q6_conditional_target_cells=16.0,
        q6_conditional_value_power=0.5,
    )

    sampled = sampler.sample(np.random.default_rng(7))

    assert sampled.buckets[6].count >= 1
    assert sampled.buckets[6].total_cells >= 16


def test_residual_problem_guides_per_quality_bucket_targets() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            item_id=1103006,
            quality=3,
            shape_key="21",
            cells=2,
            sources=("public:200022",),
        )
    )
    obs = SessionObs(
        map_id=2401,
        hero="aisha",
        buckets={3: QualityBucketObs(quality=3, total_cells_min=6, count_min=3)},
    )
    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
        obs=obs,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)

    truth = sampler.sample(rng=np.random.default_rng(2))

    assert problem.bucket_targets[3].total_cells_floor == 6
    assert problem.bucket_targets[3].count_floor == 3
    assert truth.buckets[3].total_cells >= 6
    assert truth.buckets[3].count >= 3


def test_exact_bucket_target_limits_conditional_sampler() -> None:
    maps, drops, items = _tables()
    obs = SessionObs(
        map_id=2401,
        hero="aisha",
        buckets={6: QualityBucketObs(quality=6, total_cells=16, count=1)},
    )
    problem = build_residual_problem(
        2401,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
        obs=obs,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)

    truth = sampler.sample(rng=np.random.default_rng(12))

    assert problem.bucket_targets[6].total_cells_exact == 16
    assert problem.bucket_targets[6].count_exact == 1
    assert truth.buckets[6].total_cells == 16
    assert truth.buckets[6].count == 1


def test_exact_bucket_combo_sampler_fills_count_and_cells() -> None:
    q3_1x1 = _item(3001, quality=3, value=1_000, shape=(1, 1), tags=[101])
    q3_2x1 = _item(3002, quality=3, value=2_000, shape=(2, 1), tags=[101])
    q3_2x2 = _item(3003, quality=3, value=4_000, shape=(2, 2), tags=[101])
    maps = {
        2402: BidMap(
            map_id=2402,
            name="combo_map",
            description="",
            category=101,
            auction_mode="open",
            sub_pool_weights=[],
            rounds_total=5,
            entry_fee_silver=0,
            starting_budget_silver=100_000,
            drop_pool_id=9002,
            items_per_session_min=3,
            items_per_session_max=3,
            value_tier_ui="",
            mode_flag=4,
            bid_price_ladder=[],
            raw_row=[],
        ),
    }
    drops = {
        9002: DropPool(
            pool_id=9002,
            name="combo_pool",
            description="",
            pool_type=2,
            entries=[
                DropEntry(category=101, item_id=q3_1x1.item_id, n_min=1, n_max=1, weight=1),
                DropEntry(category=101, item_id=q3_2x1.item_id, n_min=1, n_max=1, weight=1),
                DropEntry(category=101, item_id=q3_2x2.item_id, n_min=1, n_max=1, weight=1),
            ],
        ),
    }
    items = {item.item_id: item for item in (q3_1x1, q3_2x1, q3_2x2)}
    obs = SessionObs(
        map_id=2402,
        hero="aisha",
        buckets={3: QualityBucketObs(quality=3, total_cells=5, count=3)},
    )
    problem = build_residual_problem(
        2402,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
        obs=obs,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)

    truth = sampler.sample(rng=np.random.default_rng(123))

    assert problem.bucket_targets[3].total_cells_exact == 5
    assert problem.bucket_targets[3].count_exact == 3
    assert truth.buckets[3].total_cells == 5
    assert truth.buckets[3].count == 3


def test_temporary_blue_zodiac_items_are_v2_candidates() -> None:
    maps, drops, items = _tables()
    zodiac = _item(1306006, quality=3, value=8_888, shape=(2, 2), tags=[100])
    items[zodiac.item_id] = zodiac
    problem = build_residual_problem(
        2401,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)

    assert any(
        item.item_id == zodiac.item_id
        for pool in sampler._sampler.pools
        for item in pool.items
    )


def test_temporary_blue_zodiac_anchor_is_not_reported_missing() -> None:
    maps, drops, items = _tables()
    zodiac = _item(1306006, quality=3, value=8_888, shape=(2, 2), tags=[100])
    items[zodiac.item_id] = zodiac
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=1306006,
            item_id=zodiac.item_id,
            quality=3,
            shape_key="22",
            cells=4,
            sources=("public:200022",),
        )
    )

    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )

    assert not any(
        diagnostic.startswith("anchors_not_in_flattened_pool")
        for diagnostic in problem.diagnostics
    )


def test_unique_quality_shape_evidence_becomes_known_item_anchor() -> None:
    maps, drops, items = _tables()
    wall = _item(1103005, quality=3, value=8_880, shape=(5, 4), tags=[110, 101])
    other_blue = _item(1103008, quality=3, value=2_000, shape=(2, 2), tags=[110])
    items.update({wall.item_id: wall, other_blue.item_id: other_blue})
    drops[9001].entries.extend(
        [
            DropEntry(
                category=110,
                item_id=wall.item_id,
                n_min=1,
                n_max=1,
                weight=1,
            ),
            DropEntry(
                category=110,
                item_id=other_blue.item_id,
                n_min=1,
                n_max=1,
                weight=1,
            ),
        ]
    )
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=54,
            local_index=12,
            quality=3,
            shape_key="54",
            cells=20,
            sources=("skill:1002085",),
        )
    )

    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )

    assert [anchor.item_id for anchor in problem.anchors] == [wall.item_id]
    assert problem.known_value == wall.value
    assert problem.anchors[0].sources[-1] == "inferred:unique_shape"


def test_nonunique_quality_shape_evidence_stays_soft() -> None:
    maps, drops, items = _tables()
    first = _item(1103005, quality=3, value=8_880, shape=(5, 4), tags=[110])
    second = _item(1103010, quality=3, value=9_000, shape=(5, 4), tags=[101])
    items.update({first.item_id: first, second.item_id: second})
    drops[9001].entries.extend(
        [
            DropEntry(
                category=110,
                item_id=first.item_id,
                n_min=1,
                n_max=1,
                weight=1,
            ),
            DropEntry(
                category=101,
                item_id=second.item_id,
                n_min=1,
                n_max=1,
                weight=1,
            ),
        ]
    )
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=55,
            quality=3,
            shape_key="54",
            cells=20,
            sources=("skill:1002085",),
        )
    )

    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)
    truth = sampler.sample(rng=np.random.default_rng(7))
    q3_items = truth.buckets[3].items

    assert problem.anchors == ()
    assert len(problem.shape_targets) == 1
    assert problem.shape_targets[0].shape_key == "54"
    assert any(item.shape_w == 5 and item.shape_h == 4 for item in q3_items)


def test_estimate_posterior_v2_relaxes_exact_bucket_when_strict_has_no_matches() -> None:
    maps, drops, items = _tables()
    obs = SessionObs(
        map_id=2401,
        hero="aisha",
        buckets={6: QualityBucketObs(quality=6, total_cells=15, count=1)},
    )

    report = estimate_posterior_v2(
        2401,
        obs,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
        n_trials=50,
        seed=12,
    )

    assert report.n_matched > 0
    assert report.total_cells is not None
    assert report.total_cells.p10 >= 16
    assert report.diagnostics == (
        "relaxed_exact_bucket_targets:q6:count=1:cells=15",
    )


def test_quality_only_runtime_evidence_guides_count_floor() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=999,
            quality=6,
            sources=("public:200027",),
        )
    )
    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)

    truth = sampler.sample(rng=np.random.default_rng(3))

    assert problem.bucket_targets[6].count_floor == 1
    assert problem.bucket_targets[6].total_cells_floor is None
    assert truth.buckets[6].count >= 1


def test_category_shape_target_guides_conditional_sampler() -> None:
    maps, drops, items = _tables()
    obs = SessionObs(
        map_id=2401,
        hero="aisha",
        category_items=(
            CategoryItemObservation(
                category=108,
                quality=6,
                cells=16,
                shape_key="44",
            ),
        ),
    )
    problem = build_residual_problem(
        2401,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
        obs=obs,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)

    truth = sampler.sample(rng=np.random.default_rng(11))

    assert problem.category_targets == obs.category_items
    assert any(item.item_id == 1086001 for item in truth.buckets[6].items)


def test_q6_target_candidate_sampling_tilts_toward_value_without_global_boost() -> None:
    low = _item(6101, quality=6, value=120_000, shape=(3, 3), tags=[102])
    high = _item(6102, quality=6, value=480_000, shape=(3, 3), tags=[102])
    pool = type(
        "Pool",
        (),
        {
            "items": [low, high],
            "probabilities": np.asarray([1.0, 1.0], dtype=np.float64),
        },
    )()

    q6_probs = v2_module._target_sampling_probabilities(
        pool,
        [0, 1],
        quality=6,
    )
    q5_probs = v2_module._target_sampling_probabilities(
        pool,
        [0, 1],
        quality=5,
    )

    assert q6_probs is not None
    assert q5_probs is not None
    assert q6_probs[1] > q6_probs[0]
    assert q5_probs.tolist() == [0.5, 0.5]


def test_posterior_reports_remaining_space_pressure_diagnostics() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            local_index=0,
            shape_key="11",
            cells=1,
            sources=("test_layout",),
        )
    )

    report = estimate_posterior_v2(
        2401,
        SessionObs(map_id=2401, hero="aisha"),
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
        n_trials=80,
        seed=23,
    )

    assert report.n_matched > 0
    assert report.remaining_cells_after_layout is not None
    assert report.q6_space_pressure is not None
    assert report.q6_space_overflow_rate is not None
    assert report.remaining_cells_after_layout.p50 >= 1
    assert 0.0 <= report.q6_space_overflow_rate <= 1.0


def test_store_category_evidence_needs_controlled_catalog_target() -> None:
    valid_a = _item(7001, quality=6, value=210_000, shape=(2, 2), tags=[105])
    valid_b = _item(7002, quality=6, value=220_000, shape=(2, 2), tags=[105])
    excluded = _item(7003, quality=6, value=230_000, shape=(2, 2), tags=[105, 107])
    maps = {
        2403: BidMap(
            map_id=2403,
            name="category_from_store_map",
            description="",
            category=101,
            auction_mode="open",
            sub_pool_weights=[],
            rounds_total=5,
            entry_fee_silver=0,
            starting_budget_silver=100_000,
            drop_pool_id=9003,
            items_per_session_min=1,
            items_per_session_max=1,
            value_tier_ui="",
            mode_flag=4,
            bid_price_ladder=[],
            raw_row=[],
        ),
    }
    drops = {
        9003: DropPool(
            pool_id=9003,
            name="category_from_store_pool",
            description="",
            pool_type=2,
            entries=[
                DropEntry(
                    category=105,
                    item_id=valid_a.item_id,
                    n_min=1,
                    n_max=1,
                    weight=1,
                ),
                DropEntry(
                    category=105,
                    item_id=valid_b.item_id,
                    n_min=1,
                    n_max=1,
                    weight=1,
                ),
                DropEntry(
                    category=105,
                    item_id=excluded.item_id,
                    n_min=1,
                    n_max=1,
                    weight=99,
                ),
            ],
        ),
    }
    items = {item.item_id: item for item in (valid_a, valid_b, excluded)}
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            quality=6,
            shape_key="22",
            cells=4,
            categories=(105,),
            excluded_categories=(107,),
            sources=("action:100153", "action_negative:100154"),
        )
    )
    problem = build_residual_problem(
        2403,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    assert problem.category_targets == ()
    assert problem.anchors == ()
    assert problem.physical_category_catalog_status == "catalog_item_identity_mismatch"


def test_store_multi_category_evidence_needs_controlled_catalog_for_sampling() -> None:
    energy = _item(7101, quality=6, value=210_000, shape=(2, 2), tags=[108])
    fashion = _item(7102, quality=6, value=220_000, shape=(2, 2), tags=[103])
    intersection = _item(
        7103,
        quality=6,
        value=230_000,
        shape=(2, 2),
        tags=[103, 108],
    )
    maps = {
        2403: BidMap(
            map_id=2403,
            name="category_intersection_map",
            description="",
            category=101,
            auction_mode="open",
            sub_pool_weights=[],
            rounds_total=5,
            entry_fee_silver=0,
            starting_budget_silver=100_000,
            drop_pool_id=9003,
            items_per_session_min=1,
            items_per_session_max=1,
            value_tier_ui="",
            mode_flag=4,
            bid_price_ladder=[],
            raw_row=[],
        ),
    }
    drops = {
        9003: DropPool(
            pool_id=9003,
            name="category_intersection_pool",
            description="",
            pool_type=2,
            entries=[
                DropEntry(
                    category=108,
                    item_id=energy.item_id,
                    n_min=1,
                    n_max=1,
                    weight=99,
                ),
                DropEntry(
                    category=103,
                    item_id=fashion.item_id,
                    n_min=1,
                    n_max=1,
                    weight=99,
                ),
                DropEntry(
                    category=108,
                    item_id=intersection.item_id,
                    n_min=1,
                    n_max=1,
                    weight=1,
                ),
            ],
        ),
    }
    items = {item.item_id: item for item in (energy, fashion, intersection)}
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            quality=6,
            shape_key="22",
            cells=4,
            categories=(108, 103),
            sources=("action:100158", "action:100153"),
        )
    )
    problem = build_residual_problem(
        2403,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    assert problem.category_targets == ()
    assert problem.anchors == ()
    assert problem.physical_category_catalog_status == "catalog_item_identity_mismatch"


def test_category_target_counts_toward_exact_bucket() -> None:
    weapon = _item(6001, quality=6, value=1_003_000, shape=(3, 4), tags=[104])
    decoy = _item(6002, quality=6, value=844_000, shape=(4, 4), tags=[106])
    maps = {
        2403: BidMap(
            map_id=2403,
            name="category_exact_map",
            description="",
            category=101,
            auction_mode="open",
            sub_pool_weights=[],
            rounds_total=5,
            entry_fee_silver=0,
            starting_budget_silver=100_000,
            drop_pool_id=9003,
            items_per_session_min=1,
            items_per_session_max=1,
            value_tier_ui="",
            mode_flag=4,
            bid_price_ladder=[],
            raw_row=[],
        ),
    }
    drops = {
        9003: DropPool(
            pool_id=9003,
            name="category_exact_pool",
            description="",
            pool_type=2,
            entries=[
                DropEntry(category=104, item_id=weapon.item_id, n_min=1, n_max=1, weight=1),
                DropEntry(category=106, item_id=decoy.item_id, n_min=1, n_max=1, weight=1),
            ],
        ),
    }
    items = {item.item_id: item for item in (weapon, decoy)}
    obs = SessionObs(
        map_id=2403,
        hero="aisha",
        buckets={6: QualityBucketObs(quality=6, total_cells=12, count=1)},
        category_items=(
            CategoryItemObservation(
                category=104,
                quality=6,
                cells=12,
                shape_key="34",
            ),
        ),
    )
    problem = build_residual_problem(
        2403,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
        obs=obs,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)

    truth = sampler.sample(rng=np.random.default_rng(17))

    assert truth.buckets[6].count == 1
    assert truth.buckets[6].total_cells == 12
    assert truth.buckets[6].items[0].item_id == weapon.item_id


def test_category_shape_target_reports_no_pool_match() -> None:
    maps, drops, items = _tables()
    obs = SessionObs(
        map_id=2401,
        hero="aisha",
        category_items=(
            CategoryItemObservation(
                category=108,
                quality=6,
                cells=9,
                shape_key="33",
            ),
        ),
    )

    problem = build_residual_problem(
        2401,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
        obs=obs,
    )

    assert problem.category_targets == obs.category_items
    assert problem.diagnostics == ("category_target_no_pool_match:108:6:33:9",)


def test_known_footprints_build_layout_feasibility() -> None:
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            local_index=18,
            shape_key="22",
            cells=4,
            item_id=1103006,
            quality=3,
            sources=("public:200022",),
        )
    )
    store = builder.build()

    footprints = known_footprints(store)
    layout = layout_feasibility_from_store(store)

    assert len(footprints) == 1
    assert footprints[0].row == 2
    assert footprints[0].col == 9
    assert footprints[0].right_col == 10
    assert layout.footprint_count == 1
    assert layout.trusted_footprint_count == 1
    assert layout.occupied_cells == 4
    assert layout.score == 1.0


def test_shape_less_local_does_not_move_shape_footprint() -> None:
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            local_index=30,
            quality=5,
            sources=("action:100134",),
        )
    )
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            local_index=None,
            shape_key="44",
            cells=16,
            sources=("skill:100208",),
        )
    )
    store = builder.build()

    footprints = known_footprints(store)
    layout = layout_feasibility_from_store(store)

    assert len(footprints) == 1
    assert footprints[0].local_index == 0
    assert footprints[0].row == 1
    assert footprints[0].col == 1
    assert layout.diagnostics == ()


def test_latest_shape_bearing_local_updates_footprint() -> None:
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            local_index=30,
            shape_key="22",
            cells=4,
            sources=("skill:old",),
        )
    )
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            local_index=42,
            shape_key="22",
            cells=4,
            quality=4,
            sources=("skill:new",),
        )
    )
    store = builder.build()

    footprints = known_footprints(store)

    assert len(footprints) == 1
    assert footprints[0].local_index == 42
    assert footprints[0].row == 5
    assert footprints[0].col == 3


def test_quality_only_local_does_not_move_existing_shape_footprint() -> None:
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            local_index=30,
            shape_key="22",
            cells=4,
            sources=("skill:outline",),
        )
    )
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            local_index=42,
            quality=6,
            sources=("skill:baoguang",),
        )
    )

    store = builder.build()
    footprints = known_footprints(store)

    assert store.by_runtime[123].local_index == 30
    assert store.by_runtime[123].quality == 6
    assert len(footprints) == 1
    assert footprints[0].local_index == 30
    assert footprints[0].quality == 6
    assert footprints[0].row == 4
    assert footprints[0].col == 1


def test_layout_feasibility_rejects_impossible_sample() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            local_index=19,
            shape_key="22",
            cells=4,
            sources=("public:200022",),
        )
    )
    store = builder.build()
    problem = build_residual_problem(
        2401,
        store,
        maps=maps,
        drops=drops,
        items=items,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)
    truth = sampler.sample(rng=np.random.default_rng(4))

    assert 0 < problem.layout.score < 1
    assert problem.layout.diagnostics == (
        "footprint_overflow:1",
        "footprint_count_relaxed:1->0",
    )
    assert layout_feasibility_score(truth, problem.layout) > 0


def test_layout_footprint_count_guides_total_draws() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    for local_index in range(4):
        builder.add_item(
            RuntimeEvidence(
                local_index=local_index,
                shape_key="11",
                cells=1,
                sources=("action:100160",),
            )
        )
    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)

    truth = sampler.sample(rng=np.random.default_rng(5))

    assert problem.layout.footprint_count == 4
    assert problem.layout.trusted_footprint_count == 4
    assert sum(bucket.count for bucket in truth.buckets.values()) >= 4
    assert layout_feasibility_score(truth, problem.layout) > 0


def test_residual_draw_budget_uses_drop_slots_not_observed_item_count() -> None:
    known_low = _item(1010001, quality=1, value=100, shape=(1, 1), tags=[101])
    red = _item(1060001, quality=6, value=400_000, shape=(1, 1), tags=[106])
    maps = {
        2404: BidMap(
            map_id=2404,
            name="multi_count_drop_map",
            description="",
            category=101,
            auction_mode="open",
            sub_pool_weights=[],
            rounds_total=5,
            entry_fee_silver=0,
            starting_budget_silver=100_000,
            drop_pool_id=9004,
            items_per_session_min=2,
            items_per_session_max=2,
            value_tier_ui="",
            mode_flag=4,
            bid_price_ladder=[],
            raw_row=[],
        )
    }
    drops = {
        9004: DropPool(
            pool_id=9004,
            name="multi_count_pool",
            description="",
            pool_type=2,
            entries=[
                DropEntry(
                    category=101,
                    item_id=known_low.item_id,
                    n_min=2,
                    n_max=2,
                    weight=1,
                ),
                DropEntry(
                    category=106,
                    item_id=red.item_id,
                    n_min=2,
                    n_max=2,
                    weight=1_000_000,
                ),
            ],
        )
    }
    items = {known_low.item_id: known_low, red.item_id: red}
    builder = EvidenceStoreBuilder()
    for local_index in range(2):
        builder.add_item(
            RuntimeEvidence(
                local_index=local_index,
                quality=1,
                shape_key="11",
                cells=1,
                sources=("action:100160",),
            )
        )
    obs = SessionObs(
        map_id=2404,
        hero="aisha",
        buckets={1: QualityBucketObs(quality=1, total_cells=2, count=2)},
    )
    problem = build_residual_problem(
        2404,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
        obs=obs,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)
    pool = sampler._sampler.pools[0]

    assert sampler._estimated_draws_for_item_count(
        pool,
        item_count=2,
    ) == 1

    truth = sampler.sample(rng=np.random.default_rng(5))

    assert truth.buckets[1].count == 2
    assert truth.buckets[6].count == 2


def test_conflicting_layout_footprints_report_relaxed_count_diagnostic() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    for runtime_id in (1, 2):
        builder.add_item(
            RuntimeEvidence(
                runtime_id=runtime_id,
                local_index=0,
                shape_key="22",
                cells=4,
                sources=("action:100160",),
            )
        )
    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)

    truth = sampler.sample(rng=np.random.default_rng(5))

    assert problem.layout.footprint_count == 2
    assert problem.layout.trusted_footprint_count == 0
    assert "footprint_overlap_cells:4" in problem.layout.diagnostics
    assert "footprint_count_relaxed:2->0" in problem.layout.diagnostics
    assert sum(bucket.count for bucket in truth.buckets.values()) >= 1


def test_cells_only_exact_bucket_uses_combo_sampling() -> None:
    maps, drops, items = _tables()
    obs = SessionObs(
        map_id=2401,
        hero="ethan",
        buckets={3: QualityBucketObs(quality=3, total_cells=6, count_min=3)},
    )
    problem = build_residual_problem(
        2401,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
        obs=obs,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)

    pool = sampler._sampler.pools[0]
    indexes = np.flatnonzero(np.asarray([item.quality for item in pool.items]) == 3)
    buckets: dict[int, BucketTruth] = {}

    filled = sampler._sample_exact_cells_bucket_combo(
        pool,
        indexes,
        buckets,
        problem.bucket_targets[3],
        np.random.default_rng(3),
    )

    assert filled is True
    assert buckets[3].total_cells == 6
    assert buckets[3].count >= 3


def test_residual_problem_tracks_value_floor_from_exact_evidence() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=123,
            item_id=1103006,
            quality=3,
            value=3_240,
            shape_key="21",
            cells=2,
            sources=("public:200022",),
        )
    )
    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )

    assert problem.bucket_targets[3].value_floor == 3_240


def test_tool_value_sum_softly_penalizes_over_value_samples() -> None:
    maps, drops, items = _tables()
    obs = SessionObs(
        map_id=2401,
        hero="ethan",
        buckets={4: QualityBucketObs(quality=4, value_sum=20_000)},
    )
    problem = build_residual_problem(
        2401,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
        obs=obs,
    )
    matching = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=4,
        buckets={4: BucketTruth(quality=4, count=1, total_cells=4, value_sum=20_000)},
    )
    over_value = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=4,
        buckets={4: BucketTruth(quality=4, count=1, total_cells=4, value_sum=40_000)},
    )
    under_value = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=4,
        buckets={4: BucketTruth(quality=4, count=1, total_cells=4, value_sum=10_000)},
    )

    assert problem.bucket_targets[4].value_floor == 20_000
    assert problem.bucket_targets[4].value_exact == 20_000
    assert value_evidence_score(matching, problem) == 1
    assert 0 < value_evidence_score(over_value, problem) < 1
    assert value_evidence_score(under_value, problem) == 0


def test_public_gold_avg_value_scores_posterior_samples() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_fact(
        EvidenceFact(
            kind="public_info",
            key="200037",
            value=30_000,
            source="public",
        )
    )
    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    matching = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=1,
        buckets={
            5: BucketTruth(
                quality=5,
                count=1,
                total_cells=1,
                value_sum=30_000,
                items=[items[1055001]],
            ),
        },
    )
    mismatch = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=1,
        buckets={
            5: BucketTruth(
                quality=5,
                count=1,
                total_cells=1,
                value_sum=3_000,
                items=[items[1055001]],
            ),
        },
    )

    assert problem.bucket_targets[5].count_floor == 1
    assert problem.bucket_targets[5].avg_value == 30_000
    assert value_evidence_score(matching, problem) == 1
    assert 0 < value_evidence_score(mismatch, problem) < 1


def test_public_purple_avg_value_is_retained_as_soft_target() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_fact(
        EvidenceFact(
            kind="public_info",
            key="200036",
            value=8_958.21,
            source="public",
        )
    )

    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )

    assert problem.bucket_targets[4].count_floor == 1
    assert problem.bucket_targets[4].avg_value == 8_958.21


def test_public_quality_avg_cells_scores_posterior_samples() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_fact(
        EvidenceFact(
            kind="public_info",
            key="200013",
            value=2.5,
            source="public",
        )
    )

    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    matching = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=5,
        buckets={4: BucketTruth(quality=4, count=2, total_cells=5)},
    )
    mismatch = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=10,
        buckets={4: BucketTruth(quality=4, count=1, total_cells=10)},
    )

    assert problem.bucket_targets[4].count_floor == 1
    assert problem.bucket_targets[4].avg_cells == 2.5
    assert cell_evidence_score(matching, problem) == 1
    assert 0 < cell_evidence_score(mismatch, problem) < 1


def test_public_total_avg_cells_scores_posterior_samples() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_fact(
        EvidenceFact(
            kind="public_info",
            key="200014",
            value=3.0,
            source="public",
        )
    )

    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    matching = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=6,
        buckets={
            4: BucketTruth(quality=4, count=1, total_cells=4),
            5: BucketTruth(quality=5, count=1, total_cells=2),
        },
    )
    mismatch = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=10,
        buckets={4: BucketTruth(quality=4, count=1, total_cells=10)},
    )

    assert problem.total_avg_cells == 3.0
    assert "public_total_avg_cells:3.0000" in problem.diagnostics
    assert cell_evidence_score(matching, problem) == 1
    assert 0 < cell_evidence_score(mismatch, problem) < 1


def test_actionable_size_avg_value_uses_per_footprint_floors() -> None:
    assert actionable_size_avg_value_targets(((4, 4_000.0), (4, 6_000.0))) == ((4, 6_000.0),)
    assert actionable_size_avg_value_targets(((1, 1_500.0), (1, 2_500.0))) == ((1, 2_500.0),)


def test_action_size_avg_value_is_soft_target_not_quality_bucket() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_fact(
        EvidenceFact(
            kind="action",
            key="100172",
            value=120_000.0,
            source="action:100172",
            strength="soft",
        )
    )
    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    expensive_4 = _item(990_004, quality=5, value=120_000, shape=(2, 2))
    cheap_4 = _item(990_005, quality=4, value=20_000, shape=(2, 2))
    matching = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=4,
        buckets={
            5: BucketTruth(
                quality=5,
                count=1,
                total_cells=4,
                value_sum=120_000,
                items=[expensive_4],
            ),
        },
    )
    mismatch = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=4,
        buckets={
            4: BucketTruth(
                quality=4,
                count=1,
                total_cells=4,
                value_sum=20_000,
                items=[cheap_4],
            ),
        },
    )

    assert problem.size_avg_value_targets == ((4, 120_000.0),)
    assert any("size_bucket:4" in diag and "avg=120000" in diag for diag in problem.diagnostics)
    assert problem.bucket_targets == {}
    assert size_avg_value_evidence_score(matching, problem) == 1
    assert 0 < size_avg_value_evidence_score(mismatch, problem) < 1


def test_public_random_sample_avg_value_is_retained_but_not_bucket_target() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    for info_id, value in (
        (200031, 69_183.0),
        (200032, 96_897.6640625),
        (200033, 14_798.8),
        (200034, 88_888.0),
    ):
        builder.add_fact(
            EvidenceFact(
                kind="public_info",
                key=str(info_id),
                value=value,
                source="public",
            )
        )
    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )

    assert problem.random_sample_avg_values == (
        (3, 69_183.0),
        (6, 96_897.6640625),
        (9, 14_798.8),
        (12, 88_888.0),
    )
    assert problem.bucket_targets == {}
    assert problem.random_sample_value_floor == 1_013_323
    matching = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=10,
        buckets={1: BucketTruth(quality=1, count=1, total_cells=1, value_sum=1_100_000)},
    )
    too_low = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=10,
        buckets={1: BucketTruth(quality=1, count=1, total_cells=1, value_sum=900_000)},
    )
    assert global_evidence_score(matching, problem) == 1
    assert global_evidence_score(too_low, problem) == 0.10
    assert global_evidence_score(
        too_low,
        problem,
        random_sample_mode="hard",
    ) == 0
    assert global_evidence_score(
        too_low,
        problem,
        random_sample_mode="ignore",
    ) == 1
    assert v2_module._random_sample_hard_floor_min_matched(10) == 3
    assert v2_module._random_sample_hard_floor_min_matched(80) == 16
    assert v2_module._random_sample_hard_floor_min_matched(200) == 20
    assert v2_module.RANDOM_SAMPLE_HARD_FLOOR_EXTRA_TRIALS == 40


def test_public_highest_quality_limits_sample_quality() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=501,
            item_id=1055001,
            quality=5,
            shape_key="11",
            cells=1,
            sources=("public:200048",),
        )
    )
    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    with_red = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=17,
        buckets={
            5: BucketTruth(
                quality=5,
                count=1,
                total_cells=1,
                value_sum=30_000,
                items=[items[1055001]],
            ),
            6: BucketTruth(
                quality=6,
                count=1,
                total_cells=16,
                value_sum=444_000,
                items=[items[1086001]],
            ),
        },
    )
    without_red = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=1,
        buckets={
            5: BucketTruth(
                quality=5,
                count=1,
                total_cells=1,
                value_sum=30_000,
                items=[items[1055001]],
            ),
        },
    )

    assert problem.max_quality == 5
    assert "public_max_quality:5" in problem.diagnostics
    assert global_evidence_score(with_red, problem) == 0
    assert global_evidence_score(without_red, problem) == 1
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)
    pool = sampler._sampler.pools[0]
    probs = sampler._residual_probabilities(pool)
    q6_mask = np.asarray([item.quality == 6 for item in pool.items])
    assert float(probs[q6_mask].sum()) == 0.0


def test_public_highest_quality_suppresses_q6_drop_prior_warning() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=501,
            item_id=1055001,
            quality=5,
            shape_key="11",
            cells=1,
            sources=("public:200048",),
        )
    )
    report = estimate_posterior_v2(
        2401,
        SessionObs(map_id=2401, hero="isabella"),
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
        n_trials=40,
        seed=3,
    )

    diagnostics = ";".join(report.diagnostics)
    assert "public_max_quality:5" in diagnostics
    assert "q6_unconstrained_low_sample_rate" not in diagnostics
    assert "q6_below_drop_prior" not in diagnostics


def test_isabella_highest_quality_skill_limits_sample_quality() -> None:
    maps, drops, items = _tables()
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
                        skill_id=100110,
                        hero_id=110,
                        round_index=1,
                        observed_items=(
                            FatbeansObservedItem(
                                local_index=29,
                                runtime_id=501,
                                item_id=None,
                                quality=5,
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

    problem = build_residual_problem(
        2401,
        evidence_store_from_fatbeans_events(events),
        maps=maps,
        drops=drops,
        items=items,
    )
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)
    pool = sampler._sampler.pools[0]
    probs = sampler._residual_probabilities(pool)
    q6_mask = np.asarray([item.quality == 6 for item in pool.items])

    assert problem.max_quality == 5
    assert "public_max_quality:5" in problem.diagnostics
    assert float(probs[q6_mask].sum()) == 0.0


def test_public_largest_item_limits_sample_item_cells() -> None:
    maps, drops, items = _tables()
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=502,
            item_id=1055001,
            quality=5,
            shape_key="11",
            cells=1,
            sources=("public:200050",),
        )
    )
    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    too_large = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=17,
        buckets={
            5: BucketTruth(
                quality=5,
                count=1,
                total_cells=1,
                value_sum=30_000,
                items=[items[1055001]],
            ),
            6: BucketTruth(
                quality=6,
                count=1,
                total_cells=16,
                value_sum=444_000,
                items=[items[1086001]],
            ),
        },
    )
    matching = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=1,
        buckets={
            5: BucketTruth(
                quality=5,
                count=1,
                total_cells=1,
                value_sum=30_000,
                items=[items[1055001]],
            ),
        },
    )

    assert problem.max_item_cells == 1
    assert "public_max_item_cells:1" in problem.diagnostics
    assert global_evidence_score(too_large, problem) == 0
    assert global_evidence_score(matching, problem) == 1
    sampler = ConditionalSampler(problem, maps=maps, drops=drops, items=items)
    pool = sampler._sampler.pools[0]
    probs = sampler._residual_probabilities(pool)
    too_large_mask = np.asarray(
        [item.shape_w * item.shape_h > 1 for item in pool.items]
    )
    assert float(probs[too_large_mask].sum()) == 0.0
    for _ in range(20):
        sampled = sampler.sample(np.random.default_rng(7))
        assert all(
            item.shape_w * item.shape_h <= 1
            for bucket in sampled.buckets.values()
            for item in bucket.items
        )


def test_decision_value_trims_unconfirmed_small_rare_tail() -> None:
    maps, drops, items = _tables()
    small_rare = _item(1086002, quality=6, value=1_495_000, shape=(1, 1))
    large_rare = _item(1086003, quality=6, value=900_000, shape=(4, 4))
    items = {**items, small_rare.item_id: small_rare, large_rare.item_id: large_rare}
    problem = build_residual_problem(
        2401,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    truth = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=17,
        buckets={
            6: BucketTruth(
                quality=6,
                count=2,
                total_cells=17,
                value_sum=2_395_000,
                items=[small_rare, large_rare],
            ),
        },
    )

    assert decision_value_for_truth(truth, problem) == 900_000
    assert q6_decision_value_for_truth(truth, problem) == 900_000


def test_tail_replacement_decision_value_uses_same_shape_ordinary_value() -> None:
    maps, drops, items = _tables()
    small_rare = _item(1086002, quality=6, value=1_495_000, shape=(1, 1))
    ordinary_same_shape = _item(1086006, quality=6, value=93_000, shape=(1, 1))
    large_rare = _item(1086003, quality=6, value=900_000, shape=(4, 4))
    items = {
        **items,
        small_rare.item_id: small_rare,
        ordinary_same_shape.item_id: ordinary_same_shape,
        large_rare.item_id: large_rare,
    }
    problem = build_residual_problem(
        2401,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    truth = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=18,
        buckets={
            6: BucketTruth(
                quality=6,
                count=2,
                total_cells=17,
                value_sum=2_395_000,
                items=[small_rare, large_rare],
            ),
        },
    )
    replacements = {(6, 1, 1): ordinary_same_shape.value}

    assert decision_value_for_truth(truth, problem) == large_rare.value
    assert q6_decision_value_for_truth(truth, problem) == large_rare.value
    assert (
        tail_replacement_decision_value_for_truth(truth, problem, replacements)
        == large_rare.value + ordinary_same_shape.value
    )
    assert (
        q6_tail_replacement_decision_value_for_truth(truth, problem, replacements)
        == large_rare.value + ordinary_same_shape.value
    )


def test_decision_value_counts_exactly_identified_small_rare_tail() -> None:
    maps, drops, items = _tables()
    small_rare = _item(1086002, quality=6, value=1_495_000, shape=(1, 1))
    items = {**items, small_rare.item_id: small_rare}
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=321,
            item_id=small_rare.item_id,
            quality=6,
            value=small_rare.value,
            shape_key="11",
            cells=1,
            sources=("public:200022",),
        )
    )
    problem = build_residual_problem(
        2401,
        builder.build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    truth = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=1,
        buckets={
            6: BucketTruth(
                quality=6,
                count=1,
                total_cells=1,
                value_sum=small_rare.value,
                items=[small_rare],
            ),
        },
    )

    assert decision_value_for_truth(truth, problem) == small_rare.value
    assert q6_decision_value_for_truth(truth, problem) == small_rare.value


def test_decision_value_trims_unconfirmed_large_extreme_tail() -> None:
    maps, drops, items = _tables()
    radar = _item(1086004, quality=6, value=1_003_000, shape=(3, 4), tags=[108])
    normal = _item(1086005, quality=6, value=444_000, shape=(4, 4), tags=[108])
    items = {**items, radar.item_id: radar, normal.item_id: normal}
    problem = build_residual_problem(
        2401,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
    )
    truth = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=28,
        buckets={
            6: BucketTruth(
                quality=6,
                count=2,
                total_cells=28,
                value_sum=1_447_000,
                items=[radar, normal],
            ),
        },
    )

    assert decision_value_for_truth(truth, problem) == normal.value
    assert q6_decision_value_for_truth(truth, problem) == normal.value


def test_decision_value_counts_shape_category_supported_extreme_tail() -> None:
    maps, drops, items = _tables()
    radar = _item(1086004, quality=6, value=1_003_000, shape=(3, 4), tags=[108])
    items = {**items, radar.item_id: radar}
    obs = SessionObs(
        map_id=2401,
        hero="aisha",
        category_items=(
            CategoryItemObservation(
                category=108,
                quality=6,
                count=1,
                cells=12,
                shape_key="34",
            ),
        ),
    )
    problem = build_residual_problem(
        2401,
        EvidenceStoreBuilder().build(),
        maps=maps,
        drops=drops,
        items=items,
        obs=obs,
    )
    truth = SessionTruth(
        map_id=2401,
        map_name="test",
        warehouse_total_cells=12,
        buckets={
            6: BucketTruth(
                quality=6,
                count=1,
                total_cells=12,
                value_sum=radar.value,
                items=[radar],
            ),
        },
    )

    assert decision_value_for_truth(truth, problem) == radar.value
    assert q6_decision_value_for_truth(truth, problem) == radar.value
