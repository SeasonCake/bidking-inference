"""Live regressions for the two 2026-07-19 zero-value reports.

The fixtures are exact prefixes of the r1 portable raw reset archives at the sort where the
screenshots were rendered.  They deliberately exercise monitor -> scheduler -> engine/display,
not a settlement-truth-only reconstruction.
"""
from __future__ import annotations

import sys
import time
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "scripts", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ahmad_live_panel_server import (  # noqa: E402
    _aisha_dual_pass_ref_result,
    _hero_single_pass_ref_result,
    _red_display_ranges,
    summarize_snapshot,
)
from bidking_lab.live.fatbeans import parse_fatbeans_capture  # noqa: E402
from bidking_lab.live.monitor import (  # noqa: E402
    build_monitor_artifact_from_events,
    load_monitor_tables,
)

FIXTURES = ROOT / "tests" / "fixtures" / "live_zero_regressions"
AISHA_R1_SORT3 = FIXTURES / "aisha_5610_9000000000000022_sort3.json"
AHMED_R3_SORT17 = FIXTURES / "ahmed_2541_9000000000000023_sort17.json"


@lru_cache(maxsize=1)
def _tables():
    return load_monitor_tables()


def _artifact(path: Path) -> dict:
    return build_monitor_artifact_from_events(
        parse_fatbeans_capture(path),
        file=path.name,
        tables=_tables(),
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )


def _public_info(artifact: dict) -> dict:
    return ((((artifact.get("ui_contract") or {}).get("constraints") or {}).get("public_info")) or {})


def test_aisha_5610_r1_b6_never_erases_nonred_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """r1 sort=3 consumed real q4 avg-cells, then B6 erased both safe quote tiers."""
    artifact = _artifact(AISHA_R1_SORT3)
    assert artifact["ui_contract"]["context"]["session_id"] == "5610:9000000000000022"
    assert artifact["round"] == 1
    assert artifact["phase"] == "bidding"
    assert artifact["action_result_rows"] == [
        {
            "sort": 3,
            "time": "2026-07-19 09:43:40.920",
            "action_id": 100112,
            "tool": "优品均格",
            "result": pytest.approx(3.4000000953674316),
            "result_field": 11,
            "revealed_items": 0,
            "revealed_items_detail": [],
            "revealed_summary": "",
        }
    ]

    monkeypatch.delenv("BIDKING_DISABLE_B6_NONRED_VALUE_FLOOR", raising=False)
    fixed, _timing = _aisha_dual_pass_ref_result(
        artifact,
        round_no=1,
        public_info=_public_info(artifact),
    )
    assert [fixed[key] for key in ("conservative", "balanced", "aggressive")] == [
        215_077,
        253_072,
        1_118_053,
    ]
    assert "aisha_quote_pass:item" in fixed["notes"]
    assert "aisha_no_room_nonred_value_floor:0->281191" in fixed["notes"]

    monkeypatch.setenv("BIDKING_DISABLE_B6_NONRED_VALUE_FLOOR", "1")
    rollback, _timing = _aisha_dual_pass_ref_result(
        artifact,
        round_no=1,
        public_info=_public_info(artifact),
    )
    assert [rollback[key] for key in ("conservative", "balanced", "aggressive")] == [
        0,
        0,
        1_118_053,
    ]
    for key in (
        "red_count_range",
        "red_cells_range",
        "red_value_range",
        "quality_count_ranges",
        "quality_cells_ranges",
        "total_grid_range",
    ):
        assert fixed[key] == rollback[key], key


def test_ahmed_2541_r3_does_not_turn_percentile_collapse_into_false_red_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The 2.1% q5=10/q6=0 arm is real until R5; BUG-D must not hide only its count."""
    artifact = _artifact(AHMED_R3_SORT17)
    assert artifact["ui_contract"]["context"]["session_id"] == "2541:9000000000000023"
    assert artifact["round"] == 3
    assert artifact["phase"] == "bidding"

    result = _hero_single_pass_ref_result(
        artifact,
        hero_key="ahmed",
        round_no=3,
        public_info=_public_info(artifact),
    )
    assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
        1_142_157,
        1_236_344,
        1_330_530,
    ]
    assert result["quality_count_ranges"]["q5"] == [5, 5, 5]
    assert result["quality_count_candidates"]["q5"] == [
        {"value": 5, "prob": pytest.approx(0.9787)},
        {"value": 10, "prob": pytest.approx(0.0213)},
    ]
    assert result["red_count_range"] == [0, 5, 5]
    assert result["red_cells_range"] == [0, 16, 39]

    monkeypatch.delenv("BIDKING_DISABLE_RED_PAIRED_CANDIDATE_GUARD", raising=False)
    display_count, display_cells = _red_display_ranges(result)
    assert display_count == [0, 5, 5]
    assert display_cells == [0, 16, 39]

    monkeypatch.setenv("BIDKING_DISABLE_RED_PAIRED_CANDIDATE_GUARD", "1")
    rollback_count, rollback_cells = _red_display_ranges(result)
    assert rollback_count == [5, 5, 5]
    assert rollback_cells == [0, 16, 39]

    monkeypatch.delenv("BIDKING_DISABLE_RED_PAIRED_CANDIDATE_GUARD", raising=False)
    artifact["created_at"] = time.time()
    artifact["ui_contract"].setdefault("source", {})["created_at"] = time.time()
    summary = summarize_snapshot(
        artifact,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )
    assert summary["reference"]["decision_range"] == "1,142,157 / 1,236,344 / 1,330,530"
    assert summary["red"]["count_range"] == "0 / 5 / 5"
    assert summary["red"]["cells_range"] == "0 / 16 / 39"
    assert summary["red"]["value_range"] == "0 / 1,003,869 / 1,186,599"
    assert summary["red"]["low_quality_count_probability_hover"] == (
        "白绿件候选：13件 100%；蓝件候选：10件 100%"
    )
    assert "红品：5件/15格 32.6%" in summary["red"]["quality_probability_hover"]
    assert "0件/0格 2.1%" in summary["red"]["quality_probability_hover"]
