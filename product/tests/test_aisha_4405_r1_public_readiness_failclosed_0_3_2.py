"""Aisha R1 public provisional requires current, conflict-free parsed evidence."""

from __future__ import annotations

import copy
import sys
import time
from pathlib import Path
from typing import Any, Callable

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_live_panel_server as panel  # noqa: E402


def _r1_snapshot(*, round_no: int = 1) -> dict[str, Any]:
    now = time.time()
    return {
        "created_at": now,
        "round": round_no,
        "public_info_rows": [
            {
                "sort": 1,
                "info_id": 200001,
                "map_id": 4405,
                "value": 9,
                "revealed_items": 9,
                "revealed_summary": "Q4x9",
            }
        ],
        "structured_ref_inputs": {
            "source": "monitor_pre_settlement_field_updates",
            "hero": "aisha",
            "counts": {"q4": 9},
            "quality_cells": {"q4": 30},
        },
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "hero_id": 103,
                "map_id": 4405,
                "phase": "bidding",
                "round": round_no,
                "session_id": f"4405:r{round_no}-public-before-skill",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {"public_info": {}},
            "source": {"created_at": now},
        },
    }


def _summary(snapshot: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    return panel.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )


def _assert_waiting(summary: dict[str, Any]) -> None:
    ref = summary["ahmed_ref"]
    assert summary["reference"]["balanced"] == "-"
    assert ref["balanced"] is None
    assert "aisha_early_round_waiting" in ref["notes"]
    assert "aisha_quote_pass:public" not in ref["notes"]
    assert "hero_ref_provisional" not in ref["notes"]


def test_current_public_row_and_parsed_q4_quote_before_skill(tmp_path: Path) -> None:
    summary = _summary(_r1_snapshot(), tmp_path)

    ref = summary["ahmed_ref"]
    assert ref["status"] in {"ok", "count_prior"}
    assert 0 < ref["conservative"] <= ref["balanced"] <= ref["aggressive"]
    assert summary["reference"]["balanced"] != "-"
    assert ref["quality_count_ranges"]["q4"] == [9, 9, 9]
    assert ref["quality_count_ranges"]["q1"][0] > 0
    assert "aisha_quote_pass:public" in ref["notes"]
    assert "aisha_public_before_skill_provisional" in ref["notes"]
    assert "hero_ref_provisional" in ref["notes"]
    assert "aisha_absent_white_inferred_zero" not in ref["notes"]
    assert any(
        flag["label"].startswith("暂估·等待白品技能帧")
        for flag in summary["flags"]
    )


def _malformed_count(snapshot: dict[str, Any]) -> None:
    snapshot["structured_ref_inputs"]["counts"]["q4"] = "not-an-integer"


def _aggregate_only(snapshot: dict[str, Any]) -> None:
    snapshot.pop("public_info_rows")
    snapshot["ui_contract"]["constraints"]["public_info"] = {
        "input_constraints": {"legacy": "stale"}
    }


def _missing_scalar(snapshot: dict[str, Any]) -> None:
    snapshot["structured_ref_inputs"] = {
        "source": "monitor_pre_settlement_field_updates",
        "hero": "aisha",
    }


def _stale_row_session(snapshot: dict[str, Any]) -> None:
    snapshot["public_info_rows"][0]["session_id"] = "4405:stale"


def _wrong_row_map(snapshot: dict[str, Any]) -> None:
    snapshot["public_info_rows"][0]["map_id"] = 5609


def _coercible_public_value(snapshot: dict[str, Any]) -> None:
    snapshot["public_info_rows"][0]["value"] = "9"


def _partial_update(snapshot: dict[str, Any]) -> None:
    snapshot["structured_ref_inputs"]["field_updates"] = [
        {
            "path": ["bucket", "4", "count"],
            "value": 9,
            "source": "packet",
            "confidence": "partial",
            "sequence": 1,
        }
    ]


def _cross_session_update(snapshot: dict[str, Any]) -> None:
    snapshot["structured_ref_inputs"]["field_updates"] = [
        {
            "path": ["bucket", "4", "count"],
            "value": 9,
            "source": "packet",
            "confidence": "exact",
            "sequence": 1,
            "session_id": "4405:stale",
        }
    ]


def _conflicting_mirror(snapshot: dict[str, Any]) -> None:
    snapshot["ui_contract"]["constraints"]["structured_ref_inputs"] = {
        "source": "monitor_pre_settlement_field_updates",
        "hero": "aisha",
        "counts": {"q4": 8},
    }


def _malformed_sibling_scalar(snapshot: dict[str, Any]) -> None:
    snapshot["structured_ref_inputs"]["quality_cells"] = {
        "q5": "not-a-number",
        "q4": 30,
    }


def _conflicting_top_level_alias(snapshot: dict[str, Any]) -> None:
    snapshot["aisha_ref_inputs"] = {
        "hero": "aisha",
        "counts": {"q4": 8},
    }


def _conflicting_public_duplicate(snapshot: dict[str, Any]) -> None:
    duplicate = copy.deepcopy(snapshot["public_info_rows"][0])
    duplicate["value"] = 8
    duplicate["revealed_items"] = 8
    snapshot["public_info_rows"].append(duplicate)


@pytest.mark.parametrize(
    "mutate",
    [
        _malformed_count,
        _aggregate_only,
        _missing_scalar,
        _stale_row_session,
        _wrong_row_map,
        _coercible_public_value,
        _partial_update,
        _cross_session_update,
        _conflicting_mirror,
        _malformed_sibling_scalar,
        _conflicting_top_level_alias,
        _conflicting_public_duplicate,
    ],
    ids=[
        "malformed_count",
        "aggregate_only",
        "missing_scalar",
        "stale_row_session",
        "wrong_row_map",
        "coercible_public_value",
        "partial_update",
        "cross_session_update",
        "conflicting_mirror",
        "malformed_sibling_scalar",
        "conflicting_top_level_alias",
        "conflicting_public_duplicate",
    ],
)
def test_malformed_stale_or_aggregate_evidence_waits(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    snapshot = _r1_snapshot()
    mutate(snapshot)

    _assert_waiting(_summary(snapshot, tmp_path))


@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
def test_conflicting_field_updates_wait_regardless_of_order(
    tmp_path: Path,
    reverse: bool,
) -> None:
    snapshot = _r1_snapshot()
    rows = [
        {
            "path": ["bucket", "4", "count"],
            "value": 9,
            "source": "packet",
            "confidence": "exact",
            "sequence": 1,
        },
        {
            "path": ["bucket", "4", "count"],
            "value": 8,
            "source": "packet",
            "confidence": "exact",
            "sequence": 2,
        },
    ]
    snapshot["structured_ref_inputs"]["field_updates"] = (
        list(reversed(rows)) if reverse else rows
    )

    _assert_waiting(_summary(snapshot, tmp_path))


def test_matching_exact_packet_updates_remain_eligible(tmp_path: Path) -> None:
    snapshot = _r1_snapshot()
    snapshot["structured_ref_inputs"]["field_updates"] = [
        {
            "path": ["bucket", "4", "total_cells"],
            "value": 30,
            "source": "packet",
            "confidence": "exact",
            "sequence": 1,
        },
        {
            "path": ["bucket", "4", "count"],
            "value": 9,
            "source": "packet",
            "confidence": "exact",
            "sequence": 1,
        },
    ]

    summary = _summary(snapshot, tmp_path)

    assert summary["reference"]["balanced"] != "-"
    assert "aisha_quote_pass:public" in summary["ahmed_ref"]["notes"]


@pytest.mark.parametrize(
    "bad_value",
    [float("nan"), float("inf")],
    ids=["nan", "infinity"],
)
def test_raw_nonfinite_mirror_waits_before_snapshot_sanitizer(
    tmp_path: Path,
    bad_value: float,
) -> None:
    snapshot = _r1_snapshot()
    context = snapshot["ui_contract"]["context"]
    snapshot.update(
        hero=context["hero"],
        map_id=context["map_id"],
        phase=context["phase"],
        session_id=context["session_id"],
    )
    snapshot["ui_contract"]["constraints"]["structured_ref_inputs"] = {
        "source": "monitor_pre_settlement_field_updates",
        "hero": "aisha",
        "counts": {"q4": bad_value},
    }

    _assert_waiting(_summary(snapshot, tmp_path))


def test_matching_raw_mirror_remains_eligible(tmp_path: Path) -> None:
    snapshot = _r1_snapshot()
    context = snapshot["ui_contract"]["context"]
    snapshot.update(
        hero=context["hero"],
        map_id=context["map_id"],
        phase=context["phase"],
        session_id=context["session_id"],
    )
    snapshot["ui_contract"]["constraints"]["structured_ref_inputs"] = {
        "source": "monitor_pre_settlement_field_updates",
        "hero": "aisha",
        "counts": {"q4": 9},
        "quality_cells": {"q4": 30},
    }

    summary = _summary(snapshot, tmp_path)

    assert summary["reference"]["balanced"] != "-"
    assert "aisha_quote_pass:public" in summary["ahmed_ref"]["notes"]


@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
def test_conflicting_count_sum_aliases_wait_regardless_of_order(
    tmp_path: Path,
    reverse: bool,
) -> None:
    snapshot = _r1_snapshot()
    rows = [("q4q5", 9), ("45", 8)]
    snapshot["structured_ref_inputs"]["count_sums"] = dict(
        reversed(rows) if reverse else rows
    )

    _assert_waiting(_summary(snapshot, tmp_path))


def test_matching_count_sum_aliases_remain_eligible(tmp_path: Path) -> None:
    snapshot = _r1_snapshot()
    snapshot["structured_ref_inputs"]["count_sums"] = {
        "q4q5": 9,
        "45": 9,
    }

    summary = _summary(snapshot, tmp_path)

    assert summary["reference"]["balanced"] != "-"
    assert "aisha_quote_pass:public" in summary["ahmed_ref"]["notes"]


@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
def test_conflicting_count_sum_update_aliases_wait_regardless_of_order(
    tmp_path: Path,
    reverse: bool,
) -> None:
    snapshot = _r1_snapshot()
    rows = [
        {
            "path": ["bucket_group", "q4q5", "count"],
            "value": 9,
            "source": "packet",
            "confidence": "exact",
            "sequence": 1,
        },
        {
            "path": ["bucket_group", "45", "count"],
            "value": 8,
            "source": "packet",
            "confidence": "exact",
            "sequence": 2,
        },
    ]
    snapshot["structured_ref_inputs"]["field_updates"] = (
        list(reversed(rows)) if reverse else rows
    )

    _assert_waiting(_summary(snapshot, tmp_path))


def test_matching_count_sum_update_aliases_remain_eligible(tmp_path: Path) -> None:
    snapshot = _r1_snapshot()
    snapshot["structured_ref_inputs"]["field_updates"] = [
        {
            "path": ["bucket_group", group, "count"],
            "value": 9,
            "source": "packet",
            "confidence": "exact",
            "sequence": sequence,
        }
        for sequence, group in enumerate(("q4q5", "45"), start=1)
    ]

    summary = _summary(snapshot, tmp_path)

    assert summary["reference"]["balanced"] != "-"
    assert "aisha_quote_pass:public" in summary["ahmed_ref"]["notes"]


@pytest.mark.parametrize(
    "switch",
    [
        "AISHA_DISABLE_R1_PUBLIC_PROVISIONAL",
        "DRAW_HERO_DISABLE_PROVISIONAL_QUOTE",
    ],
)
def test_public_provisional_rollback_switches_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    switch: str,
) -> None:
    monkeypatch.setenv(switch, "1")

    _assert_waiting(_summary(_r1_snapshot(), tmp_path))


def test_later_round_does_not_reuse_r1_public_provisional(tmp_path: Path) -> None:
    _assert_waiting(_summary(_r1_snapshot(round_no=2), tmp_path))


def test_r1_skill_frame_keeps_normal_skill_pass(tmp_path: Path) -> None:
    snapshot = _r1_snapshot()
    snapshot["skill_reveal_rows"] = [
        {
            "sort": 2,
            "hero_id": 103,
            "skill_id": 1001034,
            "revealed_items": 5,
            "result": 5,
        }
    ]

    summary = _summary(snapshot, tmp_path)

    notes = summary["ahmed_ref"]["notes"]
    assert summary["reference"]["balanced"] != "-"
    assert "aisha_quote_pass:skill" in notes
    assert "aisha_quote_pass:public" not in notes
    assert "hero_ref_provisional" not in notes
