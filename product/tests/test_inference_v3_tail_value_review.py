from pathlib import Path

from bidking_lab.inference.map_likelihood import QuantileSummary
from bidking_lab.inference.v3.posterior import V3PosteriorReport
from bidking_lab.inference.v3.tail_value_review import (
    TailValueReviewEntry,
    empty_tail_value_review_flat_dict,
    load_tail_value_review_entries,
    review_tail_value_posterior_report,
    tail_value_review_entry_for,
)


def _posterior() -> V3PosteriorReport:
    value = QuantileSummary(p10=80_000, p50=100_000, p90=200_000)
    tail_value = QuantileSummary(p10=90_000, p50=130_000, p90=260_000)
    q6_value = QuantileSummary(p10=20_000, p50=50_000, p90=120_000)
    q6_tail = QuantileSummary(p10=30_000, p50=70_000, p90=160_000)
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
        tail_replacement_decision_value=tail_value,
        q6_count=QuantileSummary(p10=0, p50=1, p90=3),
        q6_cells=QuantileSummary(p10=0, p50=4, p90=12),
        q6_value=q6_value,
        q6_formal_decision_value=q6_value,
        q6_tail_replacement_decision_value=q6_tail,
    )


def test_tail_review_flags_candidate_without_affecting_bid() -> None:
    entry = TailValueReviewEntry(
        hero="aisha",
        map_id=2506,
        archive_windows=43,
        archive_sessions=13,
        status="watch_only_q6_tail_value_candidate",
        gate_reason="tail_holdout_q6_positive",
        tail_delta_p50_mae=-7935.2,
        q6_tail_delta_p50_mae=-5562.9,
    )

    flat = review_tail_value_posterior_report(
        _posterior(),
        entry,
        hero="aisha",
    ).to_flat_dict()

    assert flat["v3_tail_review_candidate"] is True
    assert flat["v3_tail_review_active"] is False
    assert flat["v3_tail_review_affects_bid"] is False
    assert flat["v3_tail_review_source"] == "tail_value_review_candidate"
    assert flat["v3_tail_review_tail_replacement_decision_value_p50"] == 130_000
    assert flat["v3_tail_review_q6_tail_replacement_decision_value_p50"] == 70_000


def test_tail_review_flags_hurt_guard_as_baseline() -> None:
    entry = TailValueReviewEntry(
        hero="ethan",
        map_id=2601,
        archive_windows=40,
        archive_sessions=11,
        status="blocked_tail_estimate_hurts",
        gate_reason="tail_holdout_hurts",
        tail_delta_p50_mae=13_339.4,
        q6_tail_delta_p50_mae=24_471.3,
    )

    flat = review_tail_value_posterior_report(
        _posterior(),
        entry,
        hero="ethan",
    ).to_flat_dict()

    assert flat["v3_tail_review_candidate"] is False
    assert flat["v3_tail_review_hurt_guard"] is True
    assert flat["v3_tail_review_source"] == "tail_value_hurt_guard"
    assert flat["v3_tail_review_affects_bid"] is False


def test_tail_review_loads_entries_by_hero_map(tmp_path: Path) -> None:
    path = tmp_path / "entries.json"
    path.write_text(
        """
{
  "affects_bid": false,
  "entries": [
    {
      "hero": "Aisha",
      "map_id": 2506,
      "status": "watch_only_q6_tail_value_candidate",
      "q6_tail_delta_p50_mae": -5562.9,
      "archive_sessions": 13
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )

    entries = load_tail_value_review_entries(path)
    entry = tail_value_review_entry_for(entries, hero="aisha", map_id=2506)

    assert entry is not None
    assert entry.hero == "aisha"
    assert entry.archive_sessions == 13
    assert entry.q6_tail_delta_p50_mae == -5562.9


def test_empty_tail_review_flat_dict_is_bid_safe() -> None:
    flat = empty_tail_value_review_flat_dict()

    assert flat["v3_tail_review_available"] is False
    assert flat["v3_tail_review_affects_bid"] is False
    assert flat["v3_tail_review_active"] is False
    assert flat["v3_tail_review_candidate"] is False
    assert flat["v3_tail_review_hurt_guard"] is False
    assert flat["v3_tail_review_status"] == "missing_entry"
