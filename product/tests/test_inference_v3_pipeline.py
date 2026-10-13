from bidking_lab.inference.map_likelihood import QuantileSummary
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import BucketTruth, SessionTruth
from bidking_lab.inference.v3 import (
    BucketFeasibleSummary,
    CapacitySourceExpansionEntry,
    ConstraintSet,
    EvidenceEvent,
    FeasibleSummaryReport,
    FormalValueStressDetail,
    UnderestimateRepairEntry,
    V3CcvOptions,
    V3FormalValueSamplerReport,
    V3UnderestimateRepairReport,
    V3PosteriorReport,
    advise_practical_report,
    estimate_shadow_pipeline,
)


def _item(
    item_id: int,
    *,
    quality: int,
    value: int,
) -> Item:
    return Item(
        item_id=item_id,
        name=f"item_{item_id}",
        description="",
        name_key="",
        desc_key="",
        quality=quality,
        quality_color="",
        value=value,
        shape_w=1,
        shape_h=1,
        tags=[],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )


def _truth(
    *,
    q6_count: int,
    q6_cells: int,
    q6_value: int,
    q1_cells: int = 10,
) -> SessionTruth:
    q1_item = _item(1001, quality=1, value=100)
    q6_items = [
        _item(6000 + index, quality=6, value=q6_value // max(1, q6_count))
        for index in range(max(0, q6_count))
    ]
    return SessionTruth(
        map_id=2401,
        map_name="test_map",
        warehouse_total_cells=q1_cells + q6_cells,
        buckets={
            1: BucketTruth(
                quality=1,
                count=1,
                total_cells=q1_cells,
                value_sum=100,
                items=[q1_item],
            ),
            6: BucketTruth(
                quality=6,
                count=q6_count,
                total_cells=q6_cells,
                value_sum=q6_value,
                items=q6_items,
            ),
        },
    )


def _q(p10: int, p50: int, p90: int) -> QuantileSummary:
    return QuantileSummary(p10=float(p10), p50=float(p50), p90=float(p90))


def _posterior_report(
    *,
    map_id: int = 2401,
    match_scope: str = "summary_likelihood",
    n_matched: int = 2,
    q6_present_rate: float | None = 1.0,
    q6_value: QuantileSummary | None = None,
    q6_formal: QuantileSummary | None = None,
    formal: QuantileSummary | None = None,
    total_value: QuantileSummary | None = None,
    tail_replacement: QuantileSummary | None = None,
) -> V3PosteriorReport:
    q6_value = q6_value or _q(0, 100_000, 150_000)
    q6_formal = q6_formal or q6_value
    formal = formal or _q(100_000, 300_000, 420_000)
    total_value = total_value or formal
    tail_replacement = tail_replacement or formal
    return V3PosteriorReport(
        map_id=map_id,
        map_name="test_map",
        n_total=2,
        n_matched=n_matched,
        n_strict_matched=n_matched if match_scope == "strict" else 0,
        match_scope=match_scope,
        q6_present_rate=q6_present_rate,
        total_cells=_q(20, 30, 40),
        total_value=total_value,
        formal_decision_value=formal,
        tail_replacement_decision_value=tail_replacement,
        q6_count=_q(1, 1, 2),
        q6_cells=_q(4, 4, 8),
        q6_value=q6_value,
        q6_formal_decision_value=q6_formal,
        q6_tail_replacement_decision_value=q6_value,
        diagnostics=(),
    )


def test_v3_shadow_pipeline_emits_all_shadow_namespaces() -> None:
    summary = FeasibleSummaryReport(
        session_total_count_exact=None,
        session_total_cells_exact=None,
        known_count_floor=0,
        known_cells_floor=0,
        known_value_floor=0,
        buckets=(
            BucketFeasibleSummary(
                quality=6,
                count_exact=1,
                cells_exact=4,
                value_exact=100_000,
            ),
        ),
    )

    report = estimate_shadow_pipeline(
        map_id=2401,
        map_name="test_map",
        summary=summary,
        truths=(
            _truth(q6_count=1, q6_cells=4, q6_value=100_000),
            _truth(q6_count=2, q6_cells=8, q6_value=200_000),
        ),
        hero="ethan",
    )
    flat = report.to_flat_dict()

    assert report.posterior.ready is True
    assert flat["v3_post_available"] is True
    assert flat["v3_post_affects_bid"] is False
    assert flat["v3_ccv_available"] is True
    assert flat["v3_ccv_affects_bid"] is False
    assert report.ccv_component_posterior is None
    assert flat["v3_ccvc_available"] is False
    assert flat["v3_ccvc_affects_bid"] is False
    assert flat["v3_resid_available"] is True
    assert flat["v3_resid_affects_bid"] is False
    assert flat["v3_resid_gate_available"] is True
    assert flat["v3_resid_gate_affects_bid"] is False
    assert flat["v3_rtc_available"] is True
    assert flat["v3_rtc_affects_bid"] is False
    assert flat["v3_rtc_active"] is False
    assert flat["v3_cal_available"] is True
    assert flat["v3_cal_status"] == "missing_entry"
    assert flat["v3_under_available"] is True
    assert flat["v3_under_status"] == "missing_entry"
    assert flat["v3_under_affects_bid"] is False
    assert flat["v3_tail_review_available"] is True
    assert flat["v3_tail_review_status"] == "missing_entry"
    assert flat["v3_tail_review_affects_bid"] is False
    assert flat["v3_fv_available"] is True
    assert flat["v3_fv_affects_bid"] is False
    assert flat["v3_fv_active"] is False
    assert flat["v3_fv_status"] == "prior_unavailable"
    assert flat["v3_cse_available"] is True
    assert flat["v3_cse_affects_bid"] is False
    assert flat["v3_cse_active"] is False
    assert flat["v3_cse_status"] == "missing_entry"
    assert flat["v3_practical_available"] is True
    assert flat["v3_practical_affects_bid"] is False
    assert flat["v3_practical_active"] is False
    assert flat["v3_practical_status"] == "baseline_passthrough"


def test_v3_shadow_pipeline_can_emit_component_ccv_shadow() -> None:
    summary = FeasibleSummaryReport(
        session_total_count_exact=None,
        session_total_cells_exact=26,
        known_count_floor=1,
        known_cells_floor=4,
        known_value_floor=100_000,
        buckets=(
            BucketFeasibleSummary(
                quality=6,
                count_floor=1,
                cells_floor=4,
                value_floor=100_000,
            ),
        ),
    )

    report = estimate_shadow_pipeline(
        map_id=2401,
        map_name="test_map",
        summary=summary,
        truths=(
            _truth(q6_count=1, q6_cells=4, q6_value=100_000, q1_cells=10),
            _truth(q6_count=1, q6_cells=16, q6_value=200_000, q1_cells=4),
        ),
        hero="ethan",
        ccv_options=V3CcvOptions(component_likelihood=True),
    )
    flat = report.to_flat_dict()

    assert report.ccv_component_posterior is not None
    assert flat["v3_ccvc_available"] is True
    assert flat["v3_ccvc_affects_bid"] is False
    assert flat["v3_ccvc_match_scope"] == "ccv_component_likelihood"


def test_v3_shadow_pipeline_can_emit_formal_value_candidate() -> None:
    summary = FeasibleSummaryReport(
        session_total_count_exact=None,
        session_total_cells_exact=None,
        known_count_floor=0,
        known_cells_floor=0,
        known_value_floor=500_000,
        buckets=(
            BucketFeasibleSummary(
                quality=6,
                value_floor=400_000,
            ),
        ),
    )

    report = estimate_shadow_pipeline(
        map_id=2401,
        map_name="test_map",
        summary=summary,
        truths=(
            _truth(q6_count=1, q6_cells=4, q6_value=1_500_000),
            _truth(q6_count=1, q6_cells=4, q6_value=1_600_000),
            _truth(q6_count=1, q6_cells=4, q6_value=1_700_000),
        ),
        hero="ethan",
        prior_fields={
            "v3_prior_available": True,
            "v3_prior_expected_value": 100_000,
            "v3_prior_q6_expected_value": 80_000,
        },
    )
    flat = report.to_flat_dict()

    assert flat["v3_fv_available"] is True
    assert flat["v3_fv_candidate"] is True
    assert flat["v3_fv_active"] is False
    assert flat["v3_fv_affects_bid"] is False
    assert flat["v3_fv_status"] == "watch_only_value_floor_candidate"
    assert flat["v3_fv_total_value_target"] == 500_000
    assert flat["v3_fv_q6_value_target"] == 400_000
    assert flat["v3_practical_status"] == "watch_raise_candidate"
    assert flat["v3_practical_mode"] == "value_floor_watch"
    assert flat["v3_practical_recommendation"] == "raise_watch"
    assert flat["v3_practical_affects_bid"] is False
    assert flat["v3_practical_active"] is False
    assert "formal_value" in flat["v3_practical_source_lanes"]
    assert "value_floor_candidate" in flat["v3_practical_risk_flags"]


def test_v3_shadow_pipeline_marks_cse_as_practical_risk_watch() -> None:
    summary = FeasibleSummaryReport(
        session_total_count_exact=None,
        session_total_cells_exact=None,
        known_count_floor=18,
        known_cells_floor=0,
        known_value_floor=0,
        buckets=(),
    )

    report = estimate_shadow_pipeline(
        map_id=2401,
        map_name="test_map",
        summary=summary,
        truths=(
            _truth(q6_count=0, q6_cells=0, q6_value=0),
            _truth(q6_count=0, q6_cells=0, q6_value=0, q1_cells=12),
        ),
        hero="ethan",
        prior_fields={
            "v3_prior_available": True,
            "v3_prior_items_per_session_max": 10,
        },
        capacity_source_expansion_entry=CapacitySourceExpansionEntry(
            scope="map_id",
            group="2401",
            status="watch_capacity_source_expansion_shadow_only",
            unique_non_temp_p95=16,
            unique_non_temp_max=20,
            public_total_match_rows=1,
        ),
    )
    flat = report.to_flat_dict()

    assert flat["v3_cse_pressure_candidate"] is True
    assert flat["v3_practical_status"] == "watch_risk_no_numeric_shift"
    assert flat["v3_practical_recommendation"] == "risk_watch"
    assert flat["v3_practical_confidence"] == "low"
    assert "capacity_source_expansion" in flat["v3_practical_source_lanes"]
    assert "capacity_source_pressure" in flat["v3_practical_risk_flags"]
    assert flat["v3_practical_source"] == "risk_watch"


def test_v3_shadow_pipeline_marks_q6_prior_floor_as_practical_p90_watch() -> None:
    summary = FeasibleSummaryReport(
        session_total_count_exact=None,
        session_total_cells_exact=None,
        known_count_floor=0,
        known_cells_floor=0,
        known_value_floor=0,
        buckets=(),
    )

    report = estimate_shadow_pipeline(
        map_id=2401,
        map_name="test_map",
        summary=summary,
        truths=(
            _truth(q6_count=1, q6_cells=4, q6_value=100_000),
            _truth(q6_count=1, q6_cells=4, q6_value=150_000),
        ),
        hero="ethan",
        constraints=ConstraintSet(),
        prior_fields={
            "v3_prior_available": True,
            "v3_prior_q6_expected_value": 420_000,
        },
    )
    flat = report.to_flat_dict()

    assert flat["v3_fv_candidate"] is False
    assert flat["v3_practical_status"] == "watch_q6_prior_floor"
    assert flat["v3_practical_mode"] == "q6_prior_floor_watch"
    assert flat["v3_practical_recommendation"] == "raise_watch"
    assert flat["v3_practical_affects_bid"] is False
    assert flat["v3_practical_active"] is False
    assert "q6_prior_floor_watch" in flat["v3_practical_risk_flags"]
    assert "q6_prior_tail_ceiling" in flat["v3_practical_risk_flags"]
    assert flat["v3_practical_delta_formal_decision_value_p50"] == 0.0
    assert flat["v3_practical_delta_formal_decision_value_p90"] > 0
    assert flat["v3_practical_q6_formal_decision_value_p90"] == 1_050_000


def test_v3_practical_combines_q6_prior_floor_with_tail_ceiling() -> None:
    baseline = _posterior_report(
        map_id=2501,
        formal=_q(100_000, 400_000, 550_000),
        q6_value=_q(0, 100_000, 100_000),
        q6_formal=_q(0, 100_000, 100_000),
    )
    empty = FormalValueStressDetail(
        source="none",
        target=None,
        prior_expected=None,
    )
    formal_value = V3FormalValueSamplerReport(
        baseline=baseline,
        summary=None,
        prior_fields={
            "v3_prior_available": True,
            "v3_prior_q6_expected_value": 300_000,
        },
        total_count=empty,
        total_cells=empty,
        q6_count=empty,
        q6_cells=empty,
        total_value=empty,
        q6_value=empty,
    )

    report = advise_practical_report(baseline, formal_value=formal_value)
    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "watch_q6_prior_floor"
    assert flat["v3_practical_mode"] == "q6_prior_floor_watch"
    assert flat["v3_practical_recommendation"] == "raise_watch"
    assert flat["v3_practical_affects_bid"] is False
    assert flat["v3_practical_active"] is False
    assert "q6_prior_floor_watch" in flat["v3_practical_risk_flags"]
    assert "q6_prior_tail_ceiling" in flat["v3_practical_risk_flags"]
    assert flat["v3_practical_delta_formal_decision_value_p50"] == 0.0
    assert flat["v3_practical_delta_formal_decision_value_p90"] == 650_000.0
    assert flat["v3_practical_delta_q6_formal_decision_value_p50"] == 0.0
    assert flat["v3_practical_delta_q6_formal_decision_value_p90"] == 650_000.0
    assert flat["v3_practical_q6_formal_decision_value_p90"] == 750_000.0
    assert flat["v3_practical_q6_value_p90"] == 300_000.0


def test_v3_practical_keeps_q6_prior_tail_ceiling_outside_shipwreck_villa() -> None:
    baseline = _posterior_report(
        map_id=2601,
        formal=_q(100_000, 400_000, 550_000),
        q6_value=_q(0, 100_000, 100_000),
        q6_formal=_q(0, 100_000, 100_000),
    )
    empty = FormalValueStressDetail(
        source="none",
        target=None,
        prior_expected=None,
    )
    formal_value = V3FormalValueSamplerReport(
        baseline=baseline,
        summary=None,
        prior_fields={
            "v3_prior_available": True,
            "v3_prior_q6_expected_value": 300_000,
        },
        total_count=empty,
        total_cells=empty,
        q6_count=empty,
        q6_cells=empty,
        total_value=empty,
        q6_value=empty,
    )

    report = advise_practical_report(baseline, formal_value=formal_value)
    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "watch_q6_prior_floor"
    assert "q6_prior_tail_ceiling" not in flat["v3_practical_risk_flags"]
    assert flat["v3_practical_delta_formal_decision_value_p50"] == 0.0
    assert flat["v3_practical_delta_formal_decision_value_p90"] == 200_000.0
    assert flat["v3_practical_q6_formal_decision_value_p90"] == 300_000.0


def test_v3_shadow_pipeline_marks_tail_replacement_as_practical_p90_watch() -> None:
    summary = FeasibleSummaryReport(
        session_total_count_exact=None,
        session_total_cells_exact=None,
        known_count_floor=0,
        known_cells_floor=0,
        known_value_floor=0,
        buckets=(),
    )

    report = estimate_shadow_pipeline(
        map_id=2401,
        map_name="test_map",
        summary=summary,
        truths=(
            _truth(q6_count=1, q6_cells=4, q6_value=1_500_000),
            _truth(q6_count=1, q6_cells=4, q6_value=1_600_000),
            _truth(q6_count=1, q6_cells=4, q6_value=1_700_000),
        ),
        hero="ethan",
        constraints=ConstraintSet(),
        replacement_values={(6, 1, 1): 120_000},
        prior_fields={"v3_prior_available": True},
    )
    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "watch_tail_replacement_p90"
    assert flat["v3_practical_mode"] == "tail_replacement_p90_watch"
    assert flat["v3_practical_recommendation"] == "ceiling_watch"
    assert flat["v3_practical_affects_bid"] is False
    assert flat["v3_practical_active"] is False
    assert "tail_replacement_p90_watch" in flat["v3_practical_risk_flags"]
    assert flat["v3_practical_delta_formal_decision_value_p50"] == 0.0
    assert flat["v3_practical_delta_formal_decision_value_p90"] >= 50_000


def test_v3_shadow_pipeline_marks_random_avg_value_as_practical_floor() -> None:
    summary = FeasibleSummaryReport(
        session_total_count_exact=None,
        session_total_cells_exact=None,
        known_count_floor=0,
        known_cells_floor=0,
        known_value_floor=0,
        buckets=(),
    )
    random_avg_event = EvidenceEvent(
        event_id="public:1:200031:0",
        source_kind="public_info",
        source_id="200031",
        semantic="random_6_avg_value",
        strength="diagnostic",
        constraint="diagnostic_random_avg_signal",
        targets=("random_avg_value",),
        payload={"value": 75_000},
    )

    report = estimate_shadow_pipeline(
        map_id=2401,
        map_name="test_map",
        summary=summary,
        truths=(_truth(q6_count=0, q6_cells=0, q6_value=0),),
        constraints=ConstraintSet(),
        evidence_events=(random_avg_event,),
    )

    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "watch_random_avg_value_floor"
    assert flat["v3_practical_mode"] == "random_avg_value_floor_watch"
    assert flat["v3_practical_recommendation"] == "raise_watch"
    assert flat["v3_practical_affects_bid"] is False
    assert flat["v3_practical_active"] is False
    assert "random_avg_value_floor_watch" in flat["v3_practical_risk_flags"]
    assert flat["v3_practical_formal_decision_value_p50"] == 450_000
    assert flat["v3_practical_formal_decision_value_p90"] == 450_000


def test_v3_shadow_pipeline_marks_random_avg_value_p50_floor_without_alert() -> None:
    summary = FeasibleSummaryReport(
        session_total_count_exact=None,
        session_total_cells_exact=None,
        known_count_floor=0,
        known_cells_floor=0,
        known_value_floor=0,
        buckets=(),
    )
    random_avg_event = EvidenceEvent(
        event_id="public:1:200031:0",
        source_kind="public_info",
        source_id="200031",
        semantic="random_6_avg_value",
        strength="diagnostic",
        constraint="diagnostic_random_avg_signal",
        targets=("random_avg_value",),
        payload={"value": 75_000},
    )

    report = estimate_shadow_pipeline(
        map_id=2401,
        map_name="test_map",
        summary=summary,
        truths=(
            _truth(q6_count=0, q6_cells=0, q6_value=0),
            _truth(q6_count=0, q6_cells=0, q6_value=0),
            _truth(q6_count=1, q6_cells=4, q6_value=900_000),
        ),
        constraints=ConstraintSet(),
        evidence_events=(random_avg_event,),
    )

    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "watch_random_avg_value_p50_floor"
    assert flat["v3_practical_recommendation"] == "ceiling_watch"
    assert flat["v3_practical_affects_bid"] is False
    assert flat["v3_practical_active"] is False
    assert flat["v3_practical_formal_decision_value_p50"] == 450_000
    assert flat["v3_practical_delta_formal_decision_value_p90"] == 0.0


def test_v3_practical_marks_random_avg_high_signal_as_p90_ceiling() -> None:
    baseline = _posterior_report(
        formal=_q(100_000, 500_000, 520_000),
    )
    random_avg_event = EvidenceEvent(
        event_id="public:1:200031:0",
        source_kind="public_info",
        source_id="200031",
        semantic="random_3_avg_value",
        strength="diagnostic",
        constraint="diagnostic_random_avg_signal",
        targets=("random_avg_value",),
        payload={"value": 90_000},
    )

    report = advise_practical_report(
        baseline,
        evidence_events=(random_avg_event,),
    )
    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "watch_random_avg_high_signal_ceiling"
    assert flat["v3_practical_mode"] == "random_avg_high_signal_ceiling_watch"
    assert flat["v3_practical_recommendation"] == "ceiling_watch"
    assert flat["v3_practical_affects_bid"] is False
    assert flat["v3_practical_active"] is False
    assert "random_avg_high_signal_ceiling" in flat["v3_practical_risk_flags"]
    assert flat["v3_practical_delta_formal_decision_value_p50"] == 0.0
    assert flat["v3_practical_delta_formal_decision_value_p90"] == 155_000.0
    assert flat["v3_practical_delta_q6_formal_decision_value_p90"] == 0.0


def test_v3_practical_marks_source_profile_q6_tail_ceiling() -> None:
    baseline = _posterior_report(
        map_id=2501,
        q6_present_rate=0.9,
        formal=_q(100_000, 300_000, 500_000),
        total_value=_q(100_000, 300_000, 650_000),
        q6_value=_q(0, 100_000, 180_000),
        q6_formal=_q(0, 100_000, 180_000),
    )

    report = advise_practical_report(
        baseline,
        hero="ethan",
        evidence_profile_key="public:random_avg+shape",
    )
    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "watch_source_profile_q6_tail_ceiling"
    assert flat["v3_practical_mode"] == "source_profile_q6_tail_ceiling_watch"
    assert flat["v3_practical_recommendation"] == "raise_watch"
    assert flat["v3_practical_affects_bid"] is False
    assert flat["v3_practical_active"] is False
    assert "source_profile_q6_tail_ceiling" in flat["v3_practical_risk_flags"]
    assert flat["v3_practical_delta_formal_decision_value_p50"] == 0.0
    assert flat["v3_practical_delta_formal_decision_value_p90"] == 400_000.0
    assert flat["v3_practical_delta_q6_formal_decision_value_p50"] == 0.0
    assert flat["v3_practical_delta_q6_formal_decision_value_p90"] == 400_000.0


def test_v3_practical_marks_ethan_shipwreck_shape_source_profile_ceiling() -> None:
    baseline = _posterior_report(
        map_id=2506,
        q6_present_rate=0.9,
        formal=_q(100_000, 300_000, 1_100_000),
        total_value=_q(100_000, 300_000, 1_350_000),
        q6_value=_q(0, 100_000, 800_000),
        q6_formal=_q(0, 100_000, 300_000),
    )

    report = advise_practical_report(
        baseline,
        hero="ethan",
        evidence_profile_key="shape",
    )
    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "watch_source_profile_q6_tail_ceiling"
    assert flat["v3_practical_mode"] == "source_profile_q6_tail_ceiling_watch"
    assert flat["v3_practical_recommendation"] == "raise_watch"
    assert flat["v3_practical_affects_bid"] is False
    assert flat["v3_practical_active"] is False
    assert "source_profile_q6_tail_ceiling" in flat["v3_practical_risk_flags"]
    assert flat["v3_practical_delta_formal_decision_value_p50"] == 0.0
    assert flat["v3_practical_delta_formal_decision_value_p90"] == 500_000.0
    assert flat["v3_practical_delta_q6_formal_decision_value_p50"] == 0.0
    assert flat["v3_practical_delta_q6_formal_decision_value_p90"] == 500_000.0


def test_v3_practical_marks_ethan_villa_dense_shape_source_profile_ceiling() -> None:
    baseline = _posterior_report(
        map_id=2401,
        q6_present_rate=0.8,
        formal=_q(100_000, 450_000, 780_000),
        total_value=_q(100_000, 450_000, 780_000),
        q6_value=_q(0, 200_000, 630_000),
        q6_formal=_q(0, 200_000, 630_000),
    )

    report = advise_practical_report(
        baseline,
        hero="ethan",
        evidence_profile_key="item+shape",
        shape_anchor_count=33,
    )
    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "watch_source_profile_q6_tail_ceiling"
    assert flat["v3_practical_mode"] == "source_profile_q6_tail_ceiling_watch"
    assert flat["v3_practical_recommendation"] == "raise_watch"
    assert flat["v3_practical_affects_bid"] is False
    assert flat["v3_practical_active"] is False
    assert "source_profile_q6_tail_ceiling" in flat["v3_practical_risk_flags"]
    assert flat["v3_practical_delta_formal_decision_value_p50"] == 0.0
    assert flat["v3_practical_delta_formal_decision_value_p90"] == 1_000_000.0
    assert flat["v3_practical_delta_q6_formal_decision_value_p50"] == 0.0
    assert flat["v3_practical_delta_q6_formal_decision_value_p90"] == 1_000_000.0


def test_v3_practical_keeps_ethan_villa_dense_shape_ceiling_shape_scoped() -> None:
    baseline = _posterior_report(
        map_id=2401,
        q6_present_rate=0.8,
        formal=_q(100_000, 450_000, 780_000),
        total_value=_q(100_000, 450_000, 780_000),
        q6_value=_q(0, 200_000, 630_000),
        q6_formal=_q(0, 200_000, 630_000),
    )

    report = advise_practical_report(
        baseline,
        hero="ethan",
        evidence_profile_key="item+shape",
        shape_anchor_count=32,
    )
    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "baseline_passthrough"
    assert "source_profile_q6_tail_ceiling" not in flat["v3_practical_risk_flags"]
    assert flat["v3_practical_delta_formal_decision_value_p90"] == 0.0


def test_v3_practical_marks_aisha_shipwreck_dense_item_shape_ceiling() -> None:
    baseline = _posterior_report(
        map_id=2506,
        q6_present_rate=0.3,
        formal=_q(100_000, 700_000, 1_120_000),
        total_value=_q(100_000, 700_000, 1_120_000),
        q6_value=_q(0, 250_000, 700_000),
        q6_formal=_q(0, 250_000, 700_000),
    )
    underestimate = V3UnderestimateRepairReport(
        baseline=baseline,
        hero="aisha",
        entry=UnderestimateRepairEntry(
            hero="aisha",
            map_id=2506,
            status="watch_only_upshift_candidate",
            gate_reason="bounded_hero_map_upshift",
            scale=1.02,
        ),
    )

    report = advise_practical_report(
        baseline,
        underestimate=underestimate,
        hero="aisha",
        evidence_profile_key="item+shape",
        item_anchor_count=4,
        shape_anchor_count=28,
    )
    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "watch_raise_candidate"
    assert flat["v3_practical_mode"] == "bounded_underestimate_repair"
    assert flat["v3_practical_recommendation"] == "raise_watch"
    assert flat["v3_practical_affects_bid"] is False
    assert flat["v3_practical_active"] is False
    assert "underestimate_repair_candidate" in flat["v3_practical_risk_flags"]
    assert "source_profile_q6_tail_ceiling" in flat["v3_practical_risk_flags"]
    assert flat["v3_practical_delta_formal_decision_value_p50"] == 14_000.0
    assert flat["v3_practical_delta_formal_decision_value_p90"] == 522_400.0
    assert flat["v3_practical_delta_q6_formal_decision_value_p90"] == 514_000.0


def test_v3_practical_keeps_aisha_shipwreck_dense_item_shape_item_scoped() -> None:
    baseline = _posterior_report(
        map_id=2506,
        q6_present_rate=0.3,
        formal=_q(100_000, 700_000, 1_120_000),
        total_value=_q(100_000, 700_000, 1_120_000),
        q6_value=_q(0, 250_000, 700_000),
        q6_formal=_q(0, 250_000, 700_000),
    )

    report = advise_practical_report(
        baseline,
        hero="aisha",
        evidence_profile_key="item+shape",
        item_anchor_count=3,
        shape_anchor_count=28,
    )
    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "baseline_passthrough"
    assert "source_profile_q6_tail_ceiling" not in flat["v3_practical_risk_flags"]
    assert flat["v3_practical_delta_formal_decision_value_p90"] == 0.0


def test_v3_practical_keeps_source_profile_q6_tail_ceiling_profile_scoped() -> None:
    baseline = _posterior_report(
        map_id=2501,
        q6_present_rate=0.9,
        formal=_q(100_000, 300_000, 500_000),
        total_value=_q(100_000, 300_000, 650_000),
        q6_value=_q(0, 100_000, 180_000),
        q6_formal=_q(0, 100_000, 180_000),
    )

    report = advise_practical_report(
        baseline,
        hero="ethan",
        evidence_profile_key="item+shape",
    )
    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "baseline_passthrough"
    assert "source_profile_q6_tail_ceiling" not in flat["v3_practical_risk_flags"]
    assert flat["v3_practical_delta_formal_decision_value_p90"] == 0.0


def test_v3_practical_marks_low_support_q6_raw_tail_as_ceiling() -> None:
    baseline = _posterior_report(
        match_scope="strict",
        n_matched=1,
        formal=_q(100_000, 500_000, 650_000),
        tail_replacement=_q(100_000, 500_000, 760_000),
        q6_value=_q(0, 500_000, 1_300_000),
        q6_formal=_q(0, 400_000, 500_000),
    )

    report = advise_practical_report(baseline)
    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "watch_q6_raw_tail_low_support_ceiling"
    assert flat["v3_practical_mode"] == "q6_raw_tail_low_support_ceiling_watch"
    assert flat["v3_practical_recommendation"] == "ceiling_watch"
    assert flat["v3_practical_affects_bid"] is False
    assert flat["v3_practical_active"] is False
    assert "q6_raw_tail_low_support_ceiling" in flat["v3_practical_risk_flags"]
    assert flat["v3_practical_delta_formal_decision_value_p50"] == 0.0
    assert flat["v3_practical_delta_formal_decision_value_p90"] == 600_000.0
    assert flat["v3_practical_delta_q6_formal_decision_value_p90"] == 600_000.0


def test_v3_practical_combines_value_stress_with_q6_raw_tail_ceiling() -> None:
    baseline = _posterior_report(
        formal=_q(100_000, 500_000, 650_000),
        q6_value=_q(0, 500_000, 1_300_000),
        q6_formal=_q(0, 400_000, 500_000),
    )
    empty = FormalValueStressDetail(
        source="none",
        target=None,
        prior_expected=None,
    )
    formal_value = V3FormalValueSamplerReport(
        baseline=baseline,
        summary=None,
        prior_fields={},
        total_count=empty,
        total_cells=empty,
        q6_count=empty,
        q6_cells=empty,
        total_value=empty,
        q6_value=empty,
        stress_classes=("value_floor_stress",),
    )

    report = advise_practical_report(baseline, formal_value=formal_value)
    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "watch_raise_candidate"
    assert flat["v3_practical_mode"] == "value_floor_watch"
    assert flat["v3_practical_recommendation"] == "raise_watch"
    assert "q6_raw_tail_value_stress_ceiling" in flat[
        "v3_practical_risk_flags"
    ]
    assert flat["v3_practical_delta_formal_decision_value_p50"] == 0.0
    assert flat["v3_practical_delta_formal_decision_value_p90"] == 300_000.0
    assert flat["v3_practical_delta_q6_formal_decision_value_p90"] == 300_000.0
    assert flat["v3_practical_baseline_q6_value_p90"] == 1_300_000.0
    assert flat["v3_practical_delta_q6_value_p90"] == 0.0
    assert flat["v3_practical_baseline_q6_raw_gap_to_formal_p90"] == 800_000.0
    assert flat["v3_practical_q6_raw_gap_to_formal_p90"] == 500_000.0


def test_v3_practical_does_not_mark_broad_q6_raw_tail_without_low_support() -> None:
    baseline = _posterior_report(
        match_scope="summary_likelihood",
        n_matched=64,
        formal=_q(100_000, 500_000, 650_000),
        tail_replacement=_q(100_000, 500_000, 760_000),
        q6_value=_q(0, 500_000, 1_300_000),
        q6_formal=_q(0, 400_000, 500_000),
    )

    report = advise_practical_report(baseline)
    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "watch_tail_replacement_p90"
    assert "q6_raw_tail_low_support_ceiling" not in flat[
        "v3_practical_risk_flags"
    ]


def test_v3_practical_marks_residual_q6_value_raise_watch() -> None:
    baseline = _posterior_report()
    residual = _posterior_report(
        match_scope="residual_likelihood",
        q6_value=_q(0, 360_000, 390_000),
    )

    report = advise_practical_report(
        baseline,
        residual_posterior=residual,
    )
    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "watch_q6_value_raise"
    assert flat["v3_practical_mode"] == "q6_value_ceiling_watch"
    assert flat["v3_practical_recommendation"] == "raise_watch"
    assert flat["v3_practical_affects_bid"] is False
    assert flat["v3_practical_active"] is False
    assert flat["v3_practical_source"] == "q6_value_residual"
    assert "q6_value_ceiling_watch" in flat["v3_practical_risk_flags"]
    assert flat["v3_practical_delta_formal_decision_value_p50"] == 260_000
    assert flat["v3_practical_delta_formal_decision_value_p90"] == 240_000


def test_v3_practical_marks_residual_q6_value_ceiling_watch() -> None:
    baseline = _posterior_report()
    residual = _posterior_report(
        match_scope="residual_likelihood",
        q6_value=_q(0, 220_000, 280_000),
    )

    report = advise_practical_report(
        baseline,
        residual_posterior=residual,
    )
    flat = report.to_flat_dict()

    assert flat["v3_practical_status"] == "watch_q6_value_ceiling"
    assert flat["v3_practical_recommendation"] == "ceiling_watch"
    assert flat["v3_practical_delta_formal_decision_value_p50"] == 120_000
    assert flat["v3_practical_delta_formal_decision_value_p90"] == 130_000


def test_v3_shadow_pipeline_can_freeze_component_cells() -> None:
    summary = FeasibleSummaryReport(
        session_total_count_exact=None,
        session_total_cells_exact=26,
        known_count_floor=1,
        known_cells_floor=4,
        known_value_floor=100_000,
        buckets=(
            BucketFeasibleSummary(
                quality=6,
                count_floor=1,
                cells_floor=4,
                value_floor=100_000,
            ),
        ),
    )

    report = estimate_shadow_pipeline(
        map_id=2401,
        map_name="test_map",
        summary=summary,
        truths=(
            _truth(q6_count=1, q6_cells=4, q6_value=100_000, q1_cells=10),
            _truth(q6_count=1, q6_cells=16, q6_value=200_000, q1_cells=4),
        ),
        hero="ethan",
        ccv_options=V3CcvOptions(
            component_likelihood=True,
            component_move_cells=False,
        ),
    )
    flat = report.to_flat_dict()

    assert report.ccv_component_posterior is not None
    assert flat["v3_ccvc_q6_cells_p50"] == flat["v3_post_q6_cells_p50"]
    assert "ccvc_cells_passthrough" in flat["v3_ccvc_diagnostics"]
