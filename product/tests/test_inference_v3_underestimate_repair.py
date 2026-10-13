from pathlib import Path

from bidking_lab.inference.map_likelihood import QuantileSummary
from bidking_lab.inference.v3.posterior import V3PosteriorReport
from bidking_lab.inference.v3.underestimate_repair import (
    UnderestimateRepairEntry,
    empty_underestimate_repair_flat_dict,
    load_underestimate_repair_entries,
    repair_underestimate_posterior_report,
    underestimate_entry_for,
)


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


def test_underestimate_repair_scales_value_shadow_without_affecting_bid() -> None:
    entry = UnderestimateRepairEntry(
        hero="aisha",
        map_id=2506,
        archive_windows=43,
        archive_sessions=13,
        status="watch_only_upshift_candidate",
        gate_reason="bounded_hero_map_upshift",
        scale=1.05,
    )

    report = repair_underestimate_posterior_report(
        _posterior(),
        entry,
        hero="aisha",
    )
    flat = report.to_flat_dict()

    assert flat["v3_under_candidate"] is True
    assert flat["v3_under_active"] is False
    assert flat["v3_under_affects_bid"] is False
    assert flat["v3_under_source"] == "bounded_upshift"
    assert flat["v3_under_formal_decision_value_p50"] == 105_000
    assert flat["v3_under_q6_formal_decision_value_p50"] == 52_500
    assert flat["v3_under_total_cells_p50"] == 40
    assert flat["v3_under_q6_count_p50"] == 1
    assert flat["v3_under_q6_cells_p50"] == 4


def test_underestimate_repair_keeps_needs_evidence_entry_as_baseline() -> None:
    entry = UnderestimateRepairEntry(
        hero="aisha",
        map_id=2601,
        map_family="hidden",
        archive_windows=38,
        archive_sessions=11,
        status="watch_only_needs_evidence",
        gate_reason="hidden_requires_separate_validation",
        scale=1.10,
    )

    flat = repair_underestimate_posterior_report(
        _posterior(),
        entry,
        hero="aisha",
    ).to_flat_dict()

    assert flat["v3_under_candidate"] is False
    assert flat["v3_under_source"] == "baseline"
    assert flat["v3_under_formal_decision_value_p50"] == 100_000
    assert flat["v3_under_status"] == "watch_only_needs_evidence"


def test_underestimate_repair_loads_entries_by_hero_map(tmp_path: Path) -> None:
    path = tmp_path / "entries.json"
    path.write_text(
        """
{
  "affects_bid": false,
  "entries": [
    {
      "hero": "Ethan",
      "map_id": 2506,
      "status": "watch_only_upshift_candidate",
      "scale": 1.045088,
      "archive_sessions": 8
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )

    entries = load_underestimate_repair_entries(path)
    entry = underestimate_entry_for(entries, hero="ethan", map_id=2506)

    assert entry is not None
    assert entry.hero == "ethan"
    assert entry.archive_sessions == 8
    assert entry.scale == 1.045088


def test_empty_underestimate_repair_flat_dict_is_bid_safe() -> None:
    flat = empty_underestimate_repair_flat_dict()

    assert flat["v3_under_available"] is False
    assert flat["v3_under_affects_bid"] is False
    assert flat["v3_under_active"] is False
    assert flat["v3_under_candidate"] is False
    assert flat["v3_under_status"] == "missing_entry"
