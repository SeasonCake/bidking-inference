"""Tests for round-aware bid strategy heuristics."""

from __future__ import annotations

from types import SimpleNamespace

from bidking_lab.inference.bid_strategy import recommend_bid_strategy
from bidking_lab.inference.observation import QualityBucketObs, SessionObs


def _summary(p10: int = 100_000, p50: int = 200_000, p90: int = 400_000):
    return SimpleNamespace(p10=p10, p50=p50, p90=p90)


def test_low_information_round_one_uses_round_multiplier() -> None:
    report = recommend_bid_strategy(
        latest_bids={"me": 120_000, "npc": 180_000},
        value_summary=_summary(),
        evidence_label="map baseline",
        session=SessionObs(map_id=2401, hero="aisha"),
        round_no=1,
        total_rounds=4,
        posterior_samples=50,
    )

    assert report is not None
    assert report.info_strength == "低"
    assert report.thresholds.warehouse_multiplier == 2.0
    assert report.thresholds.probe_bid == 50_000
    assert report.thresholds.defend_bid == 115_000
    assert report.thresholds.attack_bid == 200_000
    assert report.thresholds.stop_bid == 200_000
    assert "轻微成交溢价" in report.rationale
    assert report.action == "保守探价，不主动追高"
    assert "估值÷倍率" in report.rationale
    assert "仓储未知" in report.rationale
    assert "宝光四鉴" in report.next_info_hint
    assert "抽检二" in report.next_info_hint
    assert "总仓储" in report.next_info_hint


def test_medium_information_uses_p90_as_stop_not_overpay() -> None:
    session = SessionObs(
        map_id=2401,
        hero="aisha",
        warehouse_total_cells_approx=110,
        warehouse_total_cells_tolerance=8,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=12, count=6),
            3: QualityBucketObs(quality=3, total_cells=40, count=10),
        },
    )
    report = recommend_bid_strategy(
        latest_bids={"leader": 310_000},
        value_summary=_summary(),
        evidence_label="live",
        session=session,
        round_no=2,
        total_rounds=4,
        posterior_samples=200,
    )

    assert report is not None
    assert report.info_strength == "中"
    assert report.thresholds.warehouse_multiplier == 1.6
    assert report.thresholds.defend_bid == 143_750
    assert report.thresholds.attack_bid == 250_000
    assert report.thresholds.stop_bid == 250_000
    assert report.risk_band == "过热区"
    assert report.warehouse_status == "估计 110±8"


def test_high_information_allows_small_p90_premium() -> None:
    session = SessionObs(
        map_id=2401,
        hero="aisha",
        warehouse_total_cells=114,
        total_item_count=42,
        buckets={
            q: QualityBucketObs(quality=q, total_cells=q * 10, count=q)
            for q in (1, 2, 3, 4)
        },
    )
    report = recommend_bid_strategy(
        latest_bids={"leader": 370_000},
        value_summary=_summary(),
        evidence_label="live",
        session=session,
        round_no=4,
        total_rounds=4,
        posterior_samples=600,
    )

    assert report is not None
    assert report.info_strength == "高"
    assert report.thresholds.warehouse_multiplier == 1.1
    assert report.thresholds.defend_bid == 209_091
    assert report.thresholds.attack_bid == 363_637
    assert report.thresholds.stop_bid == 374_546
    assert report.risk_band == "高风险抢仓"
    assert "仓储已精确锁定" in report.rationale
    assert "不再使用总仓储" in report.next_info_hint
    assert "抽检二" in report.next_info_hint
    assert "扫描" in report.next_info_hint
    assert "临近追价阈值" in report.next_info_hint


def test_warehouse_estimate_can_improve_information_strength() -> None:
    warehouse_estimate = SimpleNamespace(
        confidence="高",
        total_cells=SimpleNamespace(p10=100, p50=110, p90=120),
    )
    report = recommend_bid_strategy(
        latest_bids={"leader": 150_000},
        value_summary=_summary(),
        evidence_label="live",
        session=SessionObs(
            map_id=2401,
            hero="aisha",
            buckets={
                1: QualityBucketObs(quality=1, total_cells=12, count=6),
                3: QualityBucketObs(quality=3, total_cells=40, count=10),
            },
        ),
        round_no=2,
        total_rounds=4,
        posterior_samples=600,
        warehouse_estimate=warehouse_estimate,
    )

    assert report is not None
    assert report.info_strength == "高"
    assert report.warehouse_status == "后验 100/110/120 (高)"
    assert "抽检" in report.next_info_hint
    assert "配置已携带" in report.next_info_hint


def test_round_two_defend_bid_adds_mild_win_premium_above_fair_bid() -> None:
    report = recommend_bid_strategy(
        latest_bids={"leader": 240_000},
        value_summary=_summary(p10=120_000, p50=400_000, p90=600_000),
        evidence_label="live",
        session=SessionObs(map_id=2401, hero="aisha"),
        round_no=2,
        total_rounds=5,
        posterior_samples=50,
    )

    assert report is not None
    assert report.thresholds.warehouse_multiplier == 1.6
    assert report.thresholds.probe_bid == 75_000
    assert report.thresholds.defend_bid == 268_750
    assert report.thresholds.defend_bid > 400_000 / 1.6
    assert report.thresholds.attack_bid == 375_000
    assert report.thresholds.stop_bid == 375_000


def test_missing_value_summary_or_bids_returns_none() -> None:
    assert recommend_bid_strategy(
        latest_bids={},
        value_summary=_summary(),
        evidence_label="x",
    ) is None
    assert recommend_bid_strategy(
        latest_bids={"a": 1},
        value_summary=SimpleNamespace(p50=1),
        evidence_label="x",
    ) is None
