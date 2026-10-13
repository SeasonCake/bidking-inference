from dataclasses import replace

from bidking_lab.inference.map_likelihood import QuantileSummary
from bidking_lab.inference.v3.calibration import (
    calibrate_posterior_report,
    empty_prior_calibration_flat_dict,
    propose_prior_calibration,
)
from bidking_lab.inference.v3.posterior import V3PosteriorReport
from bidking_lab.inference.v3.residual_gate import gate_residual_posterior_report


def _posterior() -> V3PosteriorReport:
    value = QuantileSummary(p10=80_000, p50=100_000, p90=200_000)
    q6_value = QuantileSummary(p10=20_000, p50=50_000, p90=120_000)
    return V3PosteriorReport(
        map_id=2506,
        map_name="test",
        n_total=100,
        n_matched=20,
        n_strict_matched=5,
        match_scope="summary_likelihood",
        q6_present_rate=0.5,
        total_cells=QuantileSummary(p10=30, p50=40, p90=60),
        total_value=value,
        formal_decision_value=value,
        tail_replacement_decision_value=value,
        q6_count=QuantileSummary(p10=0, p50=1, p90=3),
        q6_cells=QuantileSummary(p10=0, p50=4, p90=12),
        q6_value=q6_value,
        q6_formal_decision_value=q6_value,
        q6_tail_replacement_decision_value=q6_value,
    )


def test_prior_calibration_activates_bounded_upward_shadow() -> None:
    entry = propose_prior_calibration(
        map_id=2506,
        map_family="shipwreck",
        archive_sessions=21,
        prior_trials=10_000,
        median_ratio=1.842591,
        p90_ratio=1.907574,
        formal_p50_over_rate=0.30,
        baseline_formal_p50_mae=409_096.7,
        baseline_formal_p50_bias=-246_686.8,
    )

    assert entry.status == "active_shadow"
    assert entry.gate_reason == "upward_prior_shift"
    assert entry.scale == 1.25

    report = calibrate_posterior_report(_posterior(), entry)
    flat = report.to_flat_dict()

    assert flat["v3_cal_active"] is True
    assert flat["v3_cal_affects_bid"] is False
    assert flat["v3_cal_formal_decision_value_p50"] == 125_000
    assert flat["v3_cal_q6_formal_decision_value_p50"] == 62_500
    assert flat["v3_cal_total_cells_p50"] == 40
    assert flat["v3_cal_q6_count_p50"] == 1


def test_prior_calibration_keeps_hidden_low_sample_watch_only() -> None:
    entry = propose_prior_calibration(
        map_id=2601,
        map_family="hidden",
        archive_sessions=22,
        median_ratio=1.62585,
        p90_ratio=1.15062,
        formal_p50_over_rate=0.46,
    )

    assert entry.status == "watch_only"
    assert entry.gate_reason == "hidden_low_sample"
    assert entry.scale == 1.0


def test_prior_calibration_blocks_high_over_or_neutral_maps() -> None:
    not_under = propose_prior_calibration(
        map_id=2501,
        map_family="shipwreck",
        archive_sessions=87,
        median_ratio=1.57,
        p90_ratio=1.31,
        formal_p50_over_rate=0.47,
        baseline_formal_p50_mae=331_341.2,
        baseline_formal_p50_bias=-51_551.8,
    )
    high_over = propose_prior_calibration(
        map_id=2507,
        map_family="shipwreck",
        archive_sessions=21,
        median_ratio=1.25,
        p90_ratio=1.05,
        formal_p50_over_rate=0.62,
    )
    neutral = propose_prior_calibration(
        map_id=2507,
        map_family="shipwreck",
        archive_sessions=21,
        median_ratio=0.95,
        p90_ratio=0.96,
        formal_p50_over_rate=0.38,
    )

    assert high_over.status == "inactive"
    assert high_over.gate_reason == "high_over_guard"
    assert not_under.status == "watch_only"
    assert not_under.gate_reason == "not_systemic_under"
    assert neutral.status == "inactive"
    assert neutral.gate_reason == "neutral_ratio"


def test_empty_prior_calibration_flat_dict_is_bid_safe() -> None:
    flat = empty_prior_calibration_flat_dict()

    assert flat["v3_cal_available"] is False
    assert flat["v3_cal_affects_bid"] is False
    assert flat["v3_cal_active"] is False
    assert flat["v3_cal_status"] == "missing_entry"


def test_residual_gate_keeps_unproven_candidate_watch_only() -> None:
    entry = propose_prior_calibration(
        map_id=2506,
        map_family="shipwreck",
        archive_sessions=21,
        median_ratio=1.84,
        p90_ratio=1.90,
        formal_p50_over_rate=0.30,
        baseline_formal_p50_mae=409_096.7,
        baseline_formal_p50_bias=-246_686.8,
    )
    baseline = _posterior()
    residual = replace(
        baseline,
        match_scope="residual_likelihood",
        q6_cells=QuantileSummary(p10=0, p50=4, p90=10),
        q6_value=QuantileSummary(p10=20_000, p50=40_000, p90=110_000),
    )

    report = gate_residual_posterior_report(baseline, residual, entry)
    flat = report.to_flat_dict()

    assert flat["v3_resid_gate_active"] is False
    assert flat["v3_resid_gate_affects_bid"] is False
    assert flat["v3_resid_gate_status"] == "watch_only"
    assert flat["v3_resid_gate_gate_reason"] == "residual_gate_unproven"
    assert flat["v3_resid_gate_source"] == "baseline"
    assert flat["v3_resid_gate_q6_value_p50"] == 50_000
    assert flat["v3_resid_gate_q6_cells_delta_p50"] == 0


def test_residual_gate_blocks_cells_p50_increase() -> None:
    entry = propose_prior_calibration(
        map_id=2506,
        map_family="shipwreck",
        archive_sessions=21,
        median_ratio=1.84,
        p90_ratio=1.90,
        formal_p50_over_rate=0.30,
        baseline_formal_p50_mae=409_096.7,
        baseline_formal_p50_bias=-246_686.8,
    )
    baseline = _posterior()
    residual = replace(
        baseline,
        match_scope="residual_likelihood",
        q6_cells=QuantileSummary(p10=0, p50=8, p90=12),
    )

    report = gate_residual_posterior_report(baseline, residual, entry)
    flat = report.to_flat_dict()

    assert flat["v3_resid_gate_active"] is False
    assert flat["v3_resid_gate_status"] == "watch_only"
    assert flat["v3_resid_gate_gate_reason"] == "q6_cells_p50_increase"
    assert flat["v3_resid_gate_source"] == "baseline"


def test_residual_gate_blocks_count_p50_increase() -> None:
    entry = propose_prior_calibration(
        map_id=2506,
        map_family="shipwreck",
        archive_sessions=21,
        median_ratio=1.84,
        p90_ratio=1.90,
        formal_p50_over_rate=0.30,
        baseline_formal_p50_mae=409_096.7,
        baseline_formal_p50_bias=-246_686.8,
    )
    baseline = _posterior()
    residual = replace(
        baseline,
        match_scope="residual_likelihood",
        q6_count=QuantileSummary(p10=0, p50=2, p90=3),
        q6_cells=QuantileSummary(p10=0, p50=4, p90=10),
    )

    report = gate_residual_posterior_report(baseline, residual, entry)
    flat = report.to_flat_dict()

    assert flat["v3_resid_gate_active"] is False
    assert flat["v3_resid_gate_status"] == "watch_only"
    assert flat["v3_resid_gate_gate_reason"] == "q6_count_p50_increase"
    assert flat["v3_resid_gate_source"] == "baseline"


def test_residual_gate_blocks_value_p50_increase() -> None:
    entry = propose_prior_calibration(
        map_id=2506,
        map_family="shipwreck",
        archive_sessions=21,
        median_ratio=1.84,
        p90_ratio=1.90,
        formal_p50_over_rate=0.30,
        baseline_formal_p50_mae=409_096.7,
        baseline_formal_p50_bias=-246_686.8,
    )
    baseline = _posterior()
    residual = replace(
        baseline,
        match_scope="residual_likelihood",
        q6_cells=QuantileSummary(p10=0, p50=4, p90=10),
        q6_value=QuantileSummary(p10=20_000, p50=60_000, p90=130_000),
    )

    report = gate_residual_posterior_report(baseline, residual, entry)
    flat = report.to_flat_dict()

    assert flat["v3_resid_gate_active"] is False
    assert flat["v3_resid_gate_status"] == "watch_only"
    assert flat["v3_resid_gate_gate_reason"] == "q6_value_p50_increase"
    assert flat["v3_resid_gate_source"] == "baseline"
