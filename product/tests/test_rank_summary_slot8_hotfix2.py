from __future__ import annotations

import json
from pathlib import Path

from bidking_lab.live.fatbeans import FatbeansRankScoreChange
from bidking_lab.live.monitor import _rank_summary_from_changes
from bidking_lab.live import sample_upload
from scripts import build_match_records
from scripts import match_records_html


_SELF_FIELDS = {"self_slot", "self_before", "self_delta", "self_after"}


def _assert_no_self_projection(summary: dict[str, object] | None) -> None:
    assert summary is not None
    assert _SELF_FIELDS.isdisjoint(summary)


def test_rank_summary_preserves_slots_and_selects_slot8_on_equal_before() -> None:
    changes = (
        FatbeansRankScoreChange(slot=7, before=1200.0, delta=30.0, after=1230.0),
        FatbeansRankScoreChange(slot=8, before=1200.0, delta=15.0, after=1215.0),
    )

    summary = _rank_summary_from_changes(1200.0, 5, changes)

    assert summary == {
        "self_rank_points": 1200.0,
        "self_rank_tier": 5,
        "changes": [
            {"slot": 7, "before": 1200.0, "delta": 30.0, "after": 1230.0},
            {"slot": 8, "before": 1200.0, "delta": 15.0, "after": 1215.0},
        ],
        "self_slot": 8,
        "self_before": 1200.0,
        "self_delta": 15.0,
        "self_after": 1215.0,
    }


def test_rank_summary_keeps_latest_pre_match_point_without_settlement_block() -> None:
    assert _rank_summary_from_changes(1337.0, 6, ()) == {
        "self_rank_points": 1337.0,
        "self_rank_tier": 6,
        "changes": [],
    }


def test_rank_summary_never_falls_back_to_matching_slot7_when_slot8_disagrees() -> None:
    changes = (
        FatbeansRankScoreChange(slot=7, before=1200.0, delta=30.0, after=1230.0),
        FatbeansRankScoreChange(slot=8, before=1190.0, delta=15.0, after=1205.0),
    )

    summary = _rank_summary_from_changes(1200.0, 5, changes)

    _assert_no_self_projection(summary)
    assert summary["self_rank_points"] == 1200.0


def test_rank_summary_requires_exactly_one_slot8() -> None:
    missing = (
        FatbeansRankScoreChange(slot=7, before=1200.0, delta=30.0, after=1230.0),
    )
    duplicate = (
        FatbeansRankScoreChange(slot=8, before=1200.0, delta=15.0, after=1215.0),
        FatbeansRankScoreChange(slot=8, before=1200.0, delta=10.0, after=1210.0),
    )

    _assert_no_self_projection(_rank_summary_from_changes(1200.0, 5, missing))
    _assert_no_self_projection(_rank_summary_from_changes(1200.0, 5, duplicate))


def test_rank_summary_ignores_slot9_even_when_its_score_matches() -> None:
    changes = (
        FatbeansRankScoreChange(slot=9, before=1200.0, delta=99.0, after=1299.0),
        FatbeansRankScoreChange(slot=8, before=1200.0, delta=-15.0, after=1185.0),
    )

    summary = _rank_summary_from_changes(1200.0, 5, changes)

    assert summary is not None
    assert summary["self_slot"] == 8
    assert summary["self_before"] == 1200.0
    assert summary["self_delta"] == -15.0
    assert summary["self_after"] == 1185.0


def test_rank_summary_rejects_inconsistent_slot8_arithmetic() -> None:
    changes = (
        FatbeansRankScoreChange(slot=8, before=1200.0, delta=15.0, after=1230.0),
    )

    _assert_no_self_projection(_rank_summary_from_changes(1200.0, 5, changes))


class _ExplodingFloat(float):
    calls = 0

    def __sub__(self, other: object) -> float:
        type(self).calls += 1
        raise AssertionError("numeric subclass must not be evaluated")

    def __add__(self, other: object) -> float:
        type(self).calls += 1
        raise AssertionError("numeric subclass must not be evaluated")

    def __float__(self) -> float:
        type(self).calls += 1
        raise AssertionError("numeric subclass must not be coerced")


class _ExplodingInt(int):
    calls = 0

    def __bool__(self) -> bool:
        type(self).calls += 1
        raise AssertionError("integer subclass must not be truth-tested")

    def __int__(self) -> int:
        type(self).calls += 1
        raise AssertionError("integer subclass must not be coerced")

    def __le__(self, other: object) -> bool:
        type(self).calls += 1
        raise AssertionError("integer subclass must not be compared")

    def __ge__(self, other: object) -> bool:
        type(self).calls += 1
        raise AssertionError("integer subclass must not be compared")


def test_rank_summary_rejects_bool_nonfinite_and_numeric_subclasses() -> None:
    invalid_rows = (
        FatbeansRankScoreChange(slot=True, before=1200.0, delta=15.0, after=1215.0),
        FatbeansRankScoreChange(slot=8, before=True, delta=15.0, after=16.0),
        FatbeansRankScoreChange(slot=8, before=1200.0, delta=False, after=1200.0),
        FatbeansRankScoreChange(slot=8, before=1200.0, delta=15.0, after=float("nan")),
        FatbeansRankScoreChange(slot=8, before=1200.0, delta=float("inf"), after=1200.0),
        FatbeansRankScoreChange(
            slot=8,
            before=_ExplodingFloat(1200.0),
            delta=15.0,
            after=1215.0,
        ),
    )

    _ExplodingFloat.calls = 0
    for row in invalid_rows:
        _assert_no_self_projection(_rank_summary_from_changes(1200.0, 5, (row,)))
    assert _ExplodingFloat.calls == 0


def test_rank_summary_rejects_invalid_self_rank_points_without_numeric_magic() -> None:
    valid_slot8 = (
        FatbeansRankScoreChange(slot=8, before=1200.0, delta=15.0, after=1215.0),
    )
    invalid_self_points = (
        True,
        -1.0,
        float("nan"),
        float("inf"),
        _ExplodingFloat(1200.0),
    )

    _ExplodingFloat.calls = 0
    for self_points in invalid_self_points:
        summary = _rank_summary_from_changes(self_points, 5, valid_slot8)
        _assert_no_self_projection(summary)
        assert summary["self_rank_points"] is None
    assert _ExplodingFloat.calls == 0


def test_rank_summary_keeps_only_exact_builtin_rank_tier_boundaries() -> None:
    valid_slot8 = (
        FatbeansRankScoreChange(slot=8, before=1200.0, delta=15.0, after=1215.0),
    )

    for tier in (1, 32):
        summary = _rank_summary_from_changes(1200.0, tier, valid_slot8)
        assert summary is not None
        assert summary["self_rank_tier"] == tier
        assert summary["self_slot"] == 8


def test_rank_summary_rejects_invalid_rank_tier_without_magic_or_nonfinite_json() -> None:
    valid_slot8 = (
        FatbeansRankScoreChange(slot=8, before=1200.0, delta=15.0, after=1215.0),
    )
    invalid_tiers = (
        True,
        False,
        -1,
        0,
        33,
        float("nan"),
        float("inf"),
        "5",
        _ExplodingInt(5),
    )

    _ExplodingInt.calls = 0
    for tier in invalid_tiers:
        summary = _rank_summary_from_changes(1200.0, tier, valid_slot8)
        assert summary is not None
        assert summary["self_rank_tier"] is None
        json.dumps(summary, allow_nan=False)
        uploaded = sample_upload.trim_artifact(
            {
                "phase": "settled",
                "session_id": "5601:invalid-rank-tier",
                "final_quality_counts": {"q5": 1},
                "rank_summary": summary,
            }
        )
        assert uploaded is not None
        assert "rank_tier" not in uploaded
    assert _ExplodingInt.calls == 0


def test_rank_summary_product_consumers_use_only_producer_projection(
    tmp_path: Path,
) -> None:
    changes = (
        FatbeansRankScoreChange(slot=7, before=1200.0, delta=90.0, after=1290.0),
        FatbeansRankScoreChange(slot=8, before=1200.0, delta=15.0, after=1215.0),
    )
    rank_summary = _rank_summary_from_changes(1200.0, 5, changes)
    assert rank_summary is not None
    settled = {
        "phase": "settled",
        "session_id": "5601:rank-tier-product-chain",
        "created_at": 101,
        "map_id": 5601,
        "hero": "ahmed",
        "round": 5,
        "final_quality_counts": {"q5": 2},
        "known_value_sum": 100_000,
        "latest_bids": {"self": 120_000},
        "rank_summary": rank_summary,
    }

    uploaded = sample_upload.trim_artifact(settled)
    assert uploaded is not None
    assert uploaded["rank_tier"] == 5
    assert uploaded["rank_delta"] == 15.0
    assert uploaded["rank_after"] == 1215.0
    assert "rank_summary" not in uploaded and "changes" not in uploaded

    sessions = tmp_path / "sessions.jsonl"
    bidding = {
        "phase": "bidding",
        "session_id": settled["session_id"],
        "created_at": 100,
        "map_id": 5601,
        "hero": "ahmed",
        "round": 5,
        "balanced": 90_000,
    }
    sessions.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in (bidding, settled)),
        encoding="utf-8",
    )
    payload = build_match_records.build_payload(sessions)
    observation = payload["records"][0]["rank_observation"]
    assert observation == {
        "before": 1200.0,
        "delta": 15.0,
        "after": 1215.0,
        "latest": 1215.0,
        "tier": 5,
        "slot": 8,
        "source": "settlement_self_slot8",
    }
    rendered = match_records_html.render_html(payload)
    assert "for (const c of rs.changes)" not in rendered
    assert "settlement_self_slot8" in rendered
