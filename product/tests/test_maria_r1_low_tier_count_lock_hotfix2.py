from __future__ import annotations

import copy
from http.server import ThreadingHTTPServer
import json
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from ahmad_ref_engine import (  # noqa: E402
    extract_evidence,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from bidking_lab.live.live_refresh_signatures import (  # noqa: E402
    ref_input_signature_from_snapshot,
)
from hero_ref.tools.ahmad_live_panel_server import (  # noqa: E402
    MARIA_R1_LOW_TIER_COUNT_DISPLAY_DISABLE,
    ServerConfig,
    _quality_composition_payload,
    _quality_uncertainty_summary,
    make_handler,
    summarize_snapshot,
)
from hero_ref.tools.ahmad_tk_overlay import (  # noqa: E402
    AhmadTkOverlay,
    _summary_diagnostic_row,
)
from hero_ref.tools.detail_composition_view import (  # noqa: E402
    detail_composition_view,
)


EXPECTED_MARIA_LOW_TIER_SUMMARY = (
    "清点已知 白绿3件 蓝1件 · 格数待全库透视"
)
MARIA_COARSE_SOURCE_NOTE = (
    "maria_skill_coarse_quality_source:maria_skill_10010801"
)


def _maria_snapshot(
    *,
    total_count: int | None = 20,
    action_round: int = 1,
    qualification: str | None = None,
) -> dict[str, Any]:
    structured = {} if total_count is None else {"total_count": total_count}
    context: dict[str, Any] = {
        "hero": "maria",
        "hero_id": 108,
        "map_id": 4521,
        "phase": "bidding",
        "action_round": action_round,
    }
    if qualification is not None:
        context["warehouse_source_qualification"] = qualification
    return {
        "ui_contract": {
            "context": context,
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": structured,
        "skill_reveal_rows": [
            {
                "skill_id": 10010801,
                "hero_id": 108,
                "observed_items": [
                    {"runtime_id": 101, "quality": 1},
                    {"runtime_id": 102, "quality": 1},
                    {"runtime_id": 103, "quality": 2},
                    {"runtime_id": 104, "quality": 3},
                ],
            }
        ],
    }


def _maria_result() -> dict[str, Any]:
    return run_reference_engine(_maria_snapshot(), max_combos=8000).as_dict()


def _maria_full_outline_snapshot() -> dict[str, Any]:
    snapshot = _maria_snapshot(total_count=6)
    session_id = "4521:maria-full-outline-low-tier"
    snapshot["ui_contract"]["context"].update(
        {
            "session_id": session_id,
            "round": 2,
            "action_round": 2,
            "observed_round": 1,
        }
    )
    low_items = [
        {"runtime_id": 101, "quality": 1, "local_index": 1},
        {"runtime_id": 102, "quality": 1, "local_index": 2},
        {"runtime_id": 103, "quality": 2, "local_index": 4},
        {"runtime_id": 104, "quality": 3, "local_index": 6},
    ]
    snapshot["skill_reveal_rows"][0].update(
        {
            "sort": 9,
            "revealed_items": len(low_items),
            "observed_items": copy.deepcopy(low_items),
            "revealed_items_detail": copy.deepcopy(low_items),
        }
    )
    snapshot["action_result_rows"] = [
        {
            "sort": 11,
            "action_id": 100100,
            "session_id": session_id,
            "source_kind": "direct_action_response",
            "revealed_items": 6,
            "revealed_items_detail": [
                {"runtime_id": 101, "shape_code": 11, "local_index": 0},
                {"runtime_id": 102, "shape_code": 12, "local_index": 1},
                {"runtime_id": 103, "shape_code": 21, "local_index": 3},
                {"runtime_id": 104, "shape_code": 22, "local_index": 5},
                {"runtime_id": 201, "shape_code": 33, "local_index": 9},
                {"runtime_id": 202, "shape_code": 11, "local_index": 18},
            ],
        }
    ]
    return snapshot


def _maria_complete_low_tier_count_snapshot(
    *,
    phase: str = "bidding",
) -> dict[str, Any]:
    snapshot = _maria_snapshot(total_count=20)
    session_id = "4521:maria-complete-low-tier-counts"
    snapshot["ui_contract"]["context"].update(
        {
            "session_id": session_id,
            "phase": phase,
            "round": 1,
            "action_round": 1,
            "observed_round": 0,
        }
    )
    items = copy.deepcopy(snapshot["skill_reveal_rows"][0]["observed_items"])
    snapshot["skill_reveal_rows"][0].update(
        {
            "sort": 9,
            "revealed_items": len(items),
            "revealed_items_detail": copy.deepcopy(items),
        }
    )
    return snapshot


def _maria_summary(
    result: dict[str, Any],
    *,
    quality_composition: Any = None,
) -> str:
    if quality_composition is None:
        quality_composition = _quality_composition_payload(result)
    return _quality_uncertainty_summary(
        result,
        quality_composition=quality_composition,
    )


def test_maria_r1_summary_marks_observed_counts_without_locking_cells() -> None:
    result = run_reference_engine(_maria_snapshot(), max_combos=8000).as_dict()
    evidence = result["evidence"]

    assert evidence["fixed_counts"].get("q1") is None
    assert evidence["fixed_counts"].get("q3") is None
    assert evidence["min_counts"]["q1"] == 3
    assert evidence["min_counts"]["q3"] == 1
    assert result["quality_cells_ranges"]["q1"][0] < result["quality_cells_ranges"]["q1"][-1]

    assert _maria_summary(result) == EXPECTED_MARIA_LOW_TIER_SUMMARY


def test_maria_r1_product_summary_exposes_display_only_evidence() -> None:
    summary = summarize_snapshot(
        _maria_snapshot(),
        snapshot_path=Path("maria-r1-low-tier-count-display.json"),
    )

    assert summary["status"] == "ok"
    assert summary["red"]["uncertainty_summary"] == EXPECTED_MARIA_LOW_TIER_SUMMARY
    assert summary["red"]["quality_count_facts"] == {
        "q1": {"count": 3, "source": "maria_skill_count_exact"},
        "q3": {"count": 1, "source": "maria_skill_count_exact"},
    }
    assert summary["quality_composition"]["qualities"]["q1"][
        "dimension_state"
    ] == "件锁"
    assert summary["quality_composition"]["qualities"]["q3"][
        "dimension_state"
    ] == "件锁"


def test_maria_complete_low_tier_skill_locks_counts_before_outline() -> None:
    snapshot = _maria_complete_low_tier_count_snapshot()

    evidence = extract_evidence(snapshot)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=Path("maria-complete-low-tier-counts.json"),
    )

    assert evidence.fixed_counts["q1"] == 3
    assert evidence.fixed_counts["q3"] == 1
    assert evidence.quality_cells.get("q1") is None
    assert evidence.quality_cells.get("q3") is None
    assert "maria_skill_low_quality_counts_exact" in evidence.source_notes
    q1 = summary["quality_composition"]["qualities"]["q1"]
    q3 = summary["quality_composition"]["qualities"]["q3"]
    assert q1["count_lock_value"] == 3
    assert q3["count_lock_value"] == 1
    assert q1["cells_lock_value"] is None
    assert q3["cells_lock_value"] is None
    assert q1["dimension_state"] == "件锁"
    assert q3["dimension_state"] == "件锁"


def test_maria_complete_low_tier_skill_locks_initial_reading_quote() -> None:
    """The session-opening skill must change engine inputs, not only UI text."""
    snapshot = _maria_complete_low_tier_count_snapshot(phase="reading")
    snapshot["ui_contract"]["context"]["map_id"] = 2541
    snapshot["structured_ref_inputs"] = {}
    items = [
        *(
            {"runtime_id": 1_000 + index, "quality": 1 if index < 7 else 2}
            for index in range(18)
        ),
        *(
            {"runtime_id": 2_000 + index, "quality": 3}
            for index in range(9)
        ),
    ]
    snapshot["skill_reveal_rows"][0].update(
        {
            "revealed_items": len(items),
            "observed_items": copy.deepcopy(items),
            "revealed_items_detail": copy.deepcopy(items),
        }
    )
    unproven = copy.deepcopy(snapshot)
    unproven["skill_reveal_rows"][0].pop("revealed_items")

    direct = extract_evidence(copy.deepcopy(snapshot))
    prepared_snapshot = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    prepared = extract_evidence(copy.deepcopy(prepared_snapshot))
    exact_result = run_reference_engine(
        copy.deepcopy(prepared_snapshot),
        max_combos=8000,
    ).as_dict()
    unproven_result = run_reference_engine(
        prepare_reference_engine_snapshot(copy.deepcopy(unproven)),
        max_combos=8000,
    ).as_dict()
    exact_summary = summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=Path("maria-initial-reading-exact.json"),
    )
    unproven_summary = summarize_snapshot(
        copy.deepcopy(unproven),
        snapshot_path=Path("maria-initial-reading-unproven.json"),
    )

    for evidence in (direct, prepared):
        assert evidence.fixed_counts["q1"] == 18
        assert evidence.fixed_counts["q3"] == 9
        assert evidence.quality_cells.get("q1") is None
        assert evidence.quality_cells.get("q3") is None
        assert "maria_skill_low_quality_counts_exact" in evidence.source_notes
    assert exact_result["evidence"]["fixed_counts"] == {"q1": 18, "q3": 9}
    assert unproven_result["evidence"]["fixed_counts"].get("q1") is None
    assert unproven_result["evidence"]["fixed_counts"].get("q3") is None
    assert exact_summary["reference"]["decision_range"] != (
        unproven_summary["reference"]["decision_range"]
    )
    for key, count in (("q1", 18), ("q3", 9)):
        quality = exact_summary["quality_composition"]["qualities"][key]
        assert quality["count_lock_value"] == count
        assert quality["cells_lock_value"] is None
        assert quality["dimension_state"] == "件锁"


def test_maria_initial_empty_window_recomputes_when_skill_arrives() -> None:
    recovered = _maria_complete_low_tier_count_snapshot(phase="reading")
    empty = copy.deepcopy(recovered)
    empty["skill_reveal_rows"] = []

    empty_signature = ref_input_signature_from_snapshot(empty)
    recovered_signature = ref_input_signature_from_snapshot(recovered)
    empty_evidence = extract_evidence(empty)
    recovered_evidence = extract_evidence(recovered)

    assert empty_signature != recovered_signature
    assert empty_evidence.fixed_counts.get("q1") is None
    assert empty_evidence.fixed_counts.get("q3") is None
    assert recovered_evidence.fixed_counts["q1"] == 3
    assert recovered_evidence.fixed_counts["q3"] == 1


def test_maria_completeness_and_session_changes_bypass_pending_ref_cache() -> None:
    exact = _maria_complete_low_tier_count_snapshot(phase="reading")
    current_session = exact["ui_contract"]["context"]["session_id"]
    exact["skill_reveal_rows"][0]["session_id"] = current_session

    unproven = copy.deepcopy(exact)
    unproven["skill_reveal_rows"][0].pop("revealed_items")
    foreign = copy.deepcopy(exact)
    foreign["skill_reveal_rows"][0]["session_id"] = "4521:foreign-session"

    assert ref_input_signature_from_snapshot(unproven) != (
        ref_input_signature_from_snapshot(exact)
    )
    assert ref_input_signature_from_snapshot(foreign) != (
        ref_input_signature_from_snapshot(exact)
    )

    for cached_snapshot, pending_snapshot in (
        (unproven, exact),
        (exact, foreign),
    ):
        overlay = object.__new__(AhmadTkOverlay)
        overlay._summary_worker_pending = {
            "snapshot": pending_snapshot,
            "signature": (10, 10),
            "started_at": 2.0,
        }
        overlay._ref_cache_signature = ref_input_signature_from_snapshot(
            cached_snapshot
        )
        overlay._ref_cache_summary = {
            "status": "ok",
            "reference": {"balanced": "456,000"},
        }
        started: list[dict[str, Any]] = []
        overlay._start_live_summary_worker = lambda **kwargs: started.append(kwargs)

        AhmadTkOverlay._maybe_start_pending_summary_worker(overlay)

        assert overlay._summary_worker_pending is None
        assert started == [
            {
                "snapshot": pending_snapshot,
                "signature": (10, 10),
                "started_at": 2.0,
            }
        ]


def test_maria_complete_low_tier_counts_survive_next_round() -> None:
    snapshot = _maria_complete_low_tier_count_snapshot(phase="bidding")
    snapshot["ui_contract"]["context"].update(
        {"round": 2, "action_round": 2, "observed_round": 1}
    )

    evidence = extract_evidence(snapshot)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=Path("maria-next-round-count-lock.json"),
    )

    assert evidence.fixed_counts["q1"] == 3
    assert evidence.fixed_counts["q3"] == 1
    assert summary["quality_composition"]["qualities"]["q1"][
        "count_lock_value"
    ] == 3
    assert summary["quality_composition"]["qualities"]["q3"][
        "count_lock_value"
    ] == 1


def test_maria_late_wrong_session_marker_cannot_override_current_counts() -> None:
    snapshot = _maria_complete_low_tier_count_snapshot(phase="reading")
    current = snapshot["skill_reveal_rows"][0]
    current["session_id"] = snapshot["ui_contract"]["context"]["session_id"]
    older_foreign = copy.deepcopy(current)
    older_foreign.update({"sort": 8, "session_id": "4521:older-session"})
    snapshot["skill_reveal_rows"] = [older_foreign, current]

    current_evidence = extract_evidence(copy.deepcopy(snapshot))
    assert current_evidence.fixed_counts["q1"] == 3
    assert current_evidence.fixed_counts["q3"] == 1

    late_foreign = copy.deepcopy(current)
    late_foreign.update({"sort": 10, "session_id": "4521:late-session"})
    snapshot["skill_reveal_rows"].append(late_foreign)
    rejected = extract_evidence(snapshot)

    assert rejected.fixed_counts.get("q1") is None
    assert rejected.fixed_counts.get("q3") is None
    assert "maria_skill_low_quality_counts_exact" not in rejected.source_notes


def test_maria_initial_reading_count_promotion_is_hero_scoped() -> None:
    snapshot = _maria_complete_low_tier_count_snapshot(phase="reading")
    snapshot["ui_contract"]["context"].update(
        {"hero": "aisha", "hero_id": 103}
    )

    evidence = extract_evidence(snapshot)

    assert evidence.fixed_counts.get("q1") is None
    assert evidence.fixed_counts.get("q3") is None
    assert "maria_skill_low_quality_counts_exact" not in evidence.source_notes


def test_maria_complete_low_tier_skill_renders_count_lock_in_tk(
    tmp_path: Path,
) -> None:
    tk = pytest.importorskip("tkinter")
    root = None
    last_error = None
    for _attempt in range(3):
        try:
            root = tk.Tk()
            break
        except tk.TclError as exc:
            last_error = exc
    if root is None:
        pytest.skip(f"真 Tk 不可用(环境层): {last_error}")
    try:
        root.attributes("-alpha", 0.0)
        overlay = AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "maria-count-lock" / "snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        summary = summarize_snapshot(
            _maria_complete_low_tier_count_snapshot(phase="reading"),
            snapshot_path=tmp_path / "maria-count-lock-summary.json",
        )

        overlay.render(summary)
        root.update_idletasks()
        root.update()

        white_green = overlay.red_rows["白绿"]
        blue = overlay.red_rows["蓝品"]
        assert str(white_green.cget("text")).startswith("3件·")
        assert str(blue.cget("text")).startswith("1件·")
        assert overlay.quality_state_badges["白绿"].cget("text") == "件锁"
        assert overlay.quality_state_badges["蓝品"].cget("text") == "件锁"
        assert white_green.winfo_ismapped()
        assert blue.winfo_ismapped()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize(
    "mutation",
    (
        "revealed_count_mismatch",
        "duplicate_runtime",
        "high_quality_marker",
        "bool_quality",
        "foreign_session",
    ),
)
def test_maria_incomplete_low_tier_skill_does_not_lock_counts(
    mutation: str,
) -> None:
    snapshot = _maria_complete_low_tier_count_snapshot()
    row = snapshot["skill_reveal_rows"][0]
    if mutation == "revealed_count_mismatch":
        row["revealed_items"] = 3
    elif mutation == "duplicate_runtime":
        row["observed_items"][-1]["runtime_id"] = 101
        row["revealed_items_detail"][-1]["runtime_id"] = 101
    elif mutation == "high_quality_marker":
        row["observed_items"][-1]["quality"] = 4
        row["revealed_items_detail"][-1]["quality"] = 4
    elif mutation == "bool_quality":
        row["observed_items"][-1]["quality"] = True
        row["revealed_items_detail"][-1]["quality"] = True
    elif mutation == "foreign_session":
        row["session_id"] = "4521:foreign-session"
    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(mutation)

    evidence = extract_evidence(snapshot)

    assert evidence.fixed_counts.get("q1") is None
    assert evidence.fixed_counts.get("q3") is None
    assert "maria_skill_low_quality_counts_exact" not in evidence.source_notes


def test_maria_r1_display_has_independent_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run_reference_engine(_maria_snapshot(), max_combos=8000).as_dict()
    monkeypatch.setenv(MARIA_R1_LOW_TIER_COUNT_DISPLAY_DISABLE, "1")

    summary = _maria_summary(result)

    assert summary.startswith("未锁 ")


def test_maria_display_switch_does_not_change_engine_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = run_reference_engine(_maria_snapshot(), max_combos=8000).as_dict()
    monkeypatch.setenv(MARIA_R1_LOW_TIER_COUNT_DISPLAY_DISABLE, "1")
    rollback = run_reference_engine(_maria_snapshot(), max_combos=8000).as_dict()

    assert default == rollback


@pytest.mark.parametrize("hero", ["aisha", "ethan", "ahmed", "raven"])
def test_maria_count_display_is_hero_scoped(hero: str) -> None:
    result = run_reference_engine(_maria_snapshot(), max_combos=8000).as_dict()
    result["evidence"]["hero"] = hero

    summary = _maria_summary(result)

    assert not summary.startswith("清点已知 ")


def test_maria_count_display_requires_complete_white_green_split() -> None:
    result = run_reference_engine(_maria_snapshot(), max_combos=8000).as_dict()
    result["evidence"]["split_counts"].pop("green")

    summary = _maria_summary(result)

    assert not summary.startswith("清点已知 ")


def test_maria_total_count_never_replaces_visible_low_tier_counts() -> None:
    without_total = extract_evidence(_maria_snapshot(total_count=None))
    with_total = extract_evidence(_maria_snapshot(total_count=20))

    assert without_total.min_counts["q1"] == with_total.min_counts["q1"] == 3
    assert without_total.min_counts["q3"] == with_total.min_counts["q3"] == 1
    assert without_total.split_counts == with_total.split_counts == {
        "white": 2,
        "green": 1,
    }
    assert "q4q5q6" not in without_total.count_sums
    assert with_total.count_sums["q4q5q6"] == 16


def test_maria_full_outline_keeps_existing_exact_count_and_cells_display() -> None:
    result = run_reference_engine(_maria_snapshot(), max_combos=8000).as_dict()
    composition = _quality_composition_payload(
        {
            "status": "ok",
            "quality_count_cells_candidates": {
                "q1": [{"count": 3, "cells": 7, "prob": 1.0}],
                "q3": [{"count": 1, "cells": 2, "prob": 1.0}],
            },
            "quality_lock": {
                "q1": {"strength": "exact", "reason": "full_outline"},
                "q3": {"strength": "exact", "reason": "full_outline"},
            },
        }
    )

    summary = _quality_uncertainty_summary(
        result,
        quality_composition=composition,
    )

    assert summary == "已锁 白绿3件/7格 蓝1件/2格"


def test_maria_full_outline_joins_low_tier_runtime_quality_to_exact_cells() -> None:
    snapshot = _maria_full_outline_snapshot()

    evidence = extract_evidence(snapshot)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=Path("maria-full-outline-low-tier.json"),
    )

    assert evidence.fixed_counts["q1"] == 3
    assert evidence.fixed_counts["q3"] == 1
    assert evidence.quality_cells["q1"] == 5.0
    assert evidence.quality_cells["q3"] == 4.0
    assert evidence.split_counts == {"white": 2, "green": 1}
    assert evidence.split_quality_cells == {"white": 3.0, "green": 2.0}
    assert "maria_full_warehouse_low_quality_cells_exact" in evidence.source_notes
    assert summary["quality_composition"]["qualities"]["q1"][
        "dimension_state"
    ] == "锁定"
    assert summary["quality_composition"]["qualities"]["q3"][
        "dimension_state"
    ] == "锁定"
    assert summary["red"]["uncertainty_summary"] == (
        "已锁 白绿3件/5格 蓝1件/4格"
    )


def test_maria_full_outline_renders_exact_low_tier_cells_in_tk(
    tmp_path: Path,
) -> None:
    tk = pytest.importorskip("tkinter")
    root = None
    last_error = None
    for _attempt in range(3):
        try:
            root = tk.Tk()
            break
        except tk.TclError as exc:
            last_error = exc
    if root is None:
        pytest.skip(f"真 Tk 不可用(环境层): {last_error}")
    try:
        root.attributes("-alpha", 0.0)
        overlay = AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "maria-full-outline" / "snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        summary = summarize_snapshot(
            _maria_full_outline_snapshot(),
            snapshot_path=tmp_path / "maria-full-outline-summary.json",
        )

        overlay.render(summary)
        root.update_idletasks()
        root.update()

        white_green = overlay.red_rows["白绿"]
        blue = overlay.red_rows["蓝品"]
        assert white_green.cget("text") == "3件·5格"
        assert blue.cget("text") == "1件·4格"
        assert overlay.quality_state_badges["白绿"].cget("text") == "锁定"
        assert overlay.quality_state_badges["蓝品"].cget("text") == "锁定"
        for value in (white_green, blue):
            assert value.winfo_ismapped()
            assert "\n" not in str(value.cget("text"))
            assert (
                value.winfo_rootx() + value.winfo_width()
                <= root.winfo_rootx() + root.winfo_width()
            )
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.mark.parametrize(
    "mutation",
    (
        "wrong_outline_session",
        "revealed_count_mismatch",
        "duplicate_outline_runtime",
        "malformed_outline_shape",
        "marker_outside_outline",
    ),
)
def test_maria_full_outline_low_tier_join_fails_closed(mutation: str) -> None:
    snapshot = _maria_full_outline_snapshot()
    outline = snapshot["action_result_rows"][0]
    markers = snapshot["skill_reveal_rows"][0]
    if mutation == "wrong_outline_session":
        outline["session_id"] = "4521:other-session"
    elif mutation == "revealed_count_mismatch":
        outline["revealed_items"] = 5
    elif mutation == "duplicate_outline_runtime":
        outline["revealed_items_detail"][-1]["runtime_id"] = 101
    elif mutation == "malformed_outline_shape":
        outline["revealed_items_detail"][0]["shape_code"] = True
    elif mutation == "marker_outside_outline":
        markers["observed_items"][0]["runtime_id"] = 999
        markers["revealed_items_detail"][0]["runtime_id"] = 999
    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(mutation)

    evidence = extract_evidence(snapshot)

    # The independently complete Maria skill still locks counts.  A bad or
    # unrelated outline may only block the cells join; it cannot revoke the
    # already observed q1/q3 item totals.
    assert evidence.fixed_counts.get("q1") == 3
    assert evidence.fixed_counts.get("q3") == 1
    assert evidence.quality_cells.get("q1") is None
    assert evidence.quality_cells.get("q3") is None
    assert "maria_skill_low_quality_counts_exact" in evidence.source_notes
    assert "maria_full_warehouse_low_quality_cells_exact" not in evidence.source_notes


def test_maria_full_outline_low_tier_join_is_maria_scoped() -> None:
    snapshot = _maria_full_outline_snapshot()
    snapshot["ui_contract"]["context"].update(
        {"hero": "aisha", "hero_id": 103}
    )

    evidence = extract_evidence(snapshot)

    assert evidence.fixed_counts.get("q1") is None
    assert evidence.fixed_counts.get("q3") is None
    assert evidence.quality_cells.get("q1") is None
    assert evidence.quality_cells.get("q3") is None


class _MariaIntSubclass(int):
    pass


class _MariaFloatSubclass(float):
    pass


class _MariaDictSubclass(dict):
    pass


class _MariaListSubclass(list):
    pass


class _MariaStrSubclass(str):
    pass


def _invalid_count(kind: str, value: int) -> object:
    return {
        "string": str(value),
        "fractional": value + 0.5,
        "int-subclass": _MariaIntSubclass(value),
        "float-subclass": _MariaFloatSubclass(float(value)),
    }[kind]


@pytest.mark.parametrize("field", ("white", "green", "q1", "q3"))
@pytest.mark.parametrize(
    "kind",
    ("string", "fractional", "int-subclass", "float-subclass"),
)
def test_maria_display_rejects_non_exact_count_scalars(
    field: str,
    kind: str,
) -> None:
    result = _maria_result()
    container = (
        result["evidence"]["split_counts"]
        if field in {"white", "green"}
        else result["evidence"]["min_counts"]
    )
    container[field] = _invalid_count(kind, int(container[field]))

    assert not _maria_summary(result).startswith("清点已知 ")


@pytest.mark.parametrize(
    ("field", "replacement", "paired"),
    (
        ("white", -1, ("q1", 0)),
        ("green", -1, ("q1", 1)),
        ("q1", -1, ("white", -2)),
        ("q3", -1, None),
    ),
)
def test_maria_display_rejects_negative_counts(
    field: str,
    replacement: int,
    paired: tuple[str, int] | None,
) -> None:
    result = _maria_result()
    evidence = result["evidence"]
    container = (
        evidence["split_counts"]
        if field in {"white", "green"}
        else evidence["min_counts"]
    )
    container[field] = replacement
    if paired is not None:
        paired_field, paired_value = paired
        paired_container = (
            evidence["split_counts"]
            if paired_field in {"white", "green"}
            else evidence["min_counts"]
        )
        paired_container[paired_field] = paired_value

    assert not _maria_summary(result).startswith("清点已知 ")


@pytest.mark.parametrize("field", ("min_counts", "split_counts"))
def test_maria_display_rejects_mapping_subclasses(field: str) -> None:
    result = _maria_result()
    result["evidence"][field] = _MariaDictSubclass(result["evidence"][field])

    assert not _maria_summary(result).startswith("清点已知 ")


def test_maria_display_rejects_source_note_container_subclass() -> None:
    result = _maria_result()
    result["evidence"]["source_notes"] = _MariaListSubclass(
        result["evidence"]["source_notes"]
    )

    assert not _maria_summary(result).startswith("清点已知 ")


@pytest.mark.parametrize(
    "missing_note",
    (
        "maria_skill_coarse_quality_min_counts",
        "maria_skill_coarse_quality_split_counts",
        MARIA_COARSE_SOURCE_NOTE,
    ),
)
def test_maria_display_requires_all_exact_coarse_source_notes(
    missing_note: str,
) -> None:
    result = _maria_result()
    result["evidence"]["source_notes"].remove(missing_note)

    assert not _maria_summary(result).startswith("清点已知 ")


def test_maria_display_rejects_other_coarse_source_identity() -> None:
    result = _maria_result()
    notes = result["evidence"]["source_notes"]
    notes[notes.index(MARIA_COARSE_SOURCE_NOTE)] = (
        "maria_skill_coarse_quality_source:other_source"
    )

    assert not _maria_summary(result).startswith("清点已知 ")


def test_maria_display_accepts_exact_tuple_source_notes() -> None:
    result = _maria_result()
    result["evidence"]["source_notes"] = tuple(
        result["evidence"]["source_notes"]
    )

    assert _maria_summary(result) == EXPECTED_MARIA_LOW_TIER_SUMMARY


@pytest.mark.parametrize(
    "status",
    ("loading", "stale", "error", "conflict", "unavailable", "no_reachable"),
)
def test_maria_display_rejects_non_authoritative_ref_status(status: str) -> None:
    result = _maria_result()
    result["status"] = status

    assert not _maria_summary(result).startswith("清点已知 ")


def test_maria_display_accepts_exact_count_prior_ref_status() -> None:
    result = _maria_result()
    result["status"] = "count_prior"

    assert _maria_summary(result) == EXPECTED_MARIA_LOW_TIER_SUMMARY


def test_maria_display_rejects_count_prior_str_subclass() -> None:
    result = _maria_result()
    result["status"] = _MariaStrSubclass("count_prior")

    assert not _maria_summary(result).startswith("清点已知 ")


@pytest.mark.parametrize(
    "status",
    ("loading", "stale", "error", "conflict", "unavailable", "no_reachable"),
)
def test_maria_display_rejects_nonready_composition_root(status: str) -> None:
    result = _maria_result()
    composition = _quality_composition_payload(result)
    composition["status"] = status

    assert not _maria_summary(
        result,
        quality_composition=composition,
    ).startswith("清点已知 ")


def test_maria_display_accepts_exact_composition_root_status() -> None:
    result = _maria_result()
    composition = _quality_composition_payload(result)
    composition["status"] = "exact"

    assert _maria_summary(
        result,
        quality_composition=composition,
    ) == EXPECTED_MARIA_LOW_TIER_SUMMARY


@pytest.mark.parametrize("tier", ("q1", "q3"))
@pytest.mark.parametrize(
    "state",
    ("missing", "unavailable", "conflict", "malformed"),
)
def test_maria_display_requires_each_canonical_tier_ready(
    tier: str,
    state: str,
) -> None:
    result = _maria_result()
    composition = _quality_composition_payload(result)
    assert composition["status"] == "ready"
    if state == "missing":
        composition["qualities"].pop(tier)
    elif state == "malformed":
        composition["qualities"][tier] = {"status": object()}
    else:
        composition["qualities"][tier]["status"] = state

    assert not _maria_summary(
        result,
        quality_composition=composition,
    ).startswith("清点已知 ")


def test_maria_unrelated_high_tier_conflict_does_not_block_low_tier_text() -> None:
    result = _maria_result()
    composition = _quality_composition_payload(result)

    # q4-q6 are unavailable rather than conflicted, while q1/q3 are independently
    # canonical-ready.  The source-scoped Maria row must consume only those tiers.
    assert detail_composition_view(
        {"quality_composition": composition}
    ).status == "ready"
    assert _maria_summary(
        result,
        quality_composition=composition,
    ) == EXPECTED_MARIA_LOW_TIER_SUMMARY


def test_maria_joint_candidates_survive_independent_marginal_rounding() -> None:
    result = _maria_result()
    composition = _quality_composition_payload(result)
    q1 = composition["qualities"]["q1"]
    q1["count_candidates"] = [
        {"value": 3, "probability": 0.9998},
    ]

    full_q1 = detail_composition_view(
        {
            "quality_composition": {
                "status": "ready",
                "qualities": {"q1": q1},
            }
        }
    ).quality("q1")
    assert full_q1.status == "conflict"
    assert _maria_summary(
        result,
        quality_composition=composition,
    ) == EXPECTED_MARIA_LOW_TIER_SUMMARY


@pytest.mark.parametrize("tier", ("q1", "q3"))
def test_maria_malicious_joint_candidate_is_zero_magic_fail_closed(
    tier: str,
) -> None:
    magic_calls: list[str] = []

    class ScalarBomb:
        def __bool__(self) -> bool:
            magic_calls.append("bool")
            return True

        def __eq__(self, _other: object) -> bool:
            magic_calls.append("eq")
            return False

        def __int__(self) -> int:
            magic_calls.append("int")
            return 3

        def __str__(self) -> str:
            magic_calls.append("str")
            return "3"

    result = _maria_result()
    composition = _quality_composition_payload(result)
    composition["qualities"][tier]["candidates"][0]["count"] = ScalarBomb()

    assert not _maria_summary(
        result,
        quality_composition=composition,
    ).startswith("清点已知 ")
    assert magic_calls == []


@pytest.mark.parametrize("tier", ("q1", "q3"))
def test_maria_malicious_low_tier_container_is_zero_magic_fail_closed(
    tier: str,
) -> None:
    magic_calls: list[str] = []

    class DictBomb(dict):
        def get(self, *_args: object, **_kwargs: object) -> object:
            magic_calls.append("get")
            return object()

        def __iter__(self):
            magic_calls.append("iter")
            return super().__iter__()

        def __str__(self) -> str:
            magic_calls.append("str")
            return "ready"

    result = _maria_result()
    composition = _quality_composition_payload(result)
    composition["qualities"][tier] = DictBomb(
        composition["qualities"][tier]
    )

    assert not _maria_summary(
        result,
        quality_composition=composition,
    ).startswith("清点已知 ")
    assert magic_calls == []


def test_maria_malicious_unrelated_tier_is_ignored_without_magic() -> None:
    magic_calls: list[str] = []

    class TierBomb:
        def __bool__(self) -> bool:
            magic_calls.append("bool")
            return True

        def __eq__(self, _other: object) -> bool:
            magic_calls.append("eq")
            return False

        def __str__(self) -> str:
            magic_calls.append("str")
            return "conflict"

    result = _maria_result()
    composition = _quality_composition_payload(result)
    composition["qualities"]["q4"] = TierBomb()

    assert _maria_summary(
        result,
        quality_composition=composition,
    ) == EXPECTED_MARIA_LOW_TIER_SUMMARY
    assert magic_calls == []


def _low_tier_composition(
    *,
    q1_exact: bool,
    q3_exact: bool,
) -> dict[str, Any]:
    return _quality_composition_payload(
        {
            "status": "ok",
            "quality_count_cells_candidates": {
                "q1": (
                    [{"count": 3, "cells": 7, "prob": 1.0}]
                    if q1_exact
                    else [
                        {"count": 3, "cells": 7, "prob": 0.5},
                        {"count": 3, "cells": 8, "prob": 0.5},
                    ]
                ),
                "q3": (
                    [{"count": 1, "cells": 2, "prob": 1.0}]
                    if q3_exact
                    else [
                        {"count": 1, "cells": 2, "prob": 0.5},
                        {"count": 1, "cells": 3, "prob": 0.5},
                    ]
                ),
            },
            "quality_lock": {
                "q1": {
                    "strength": "exact" if q1_exact else "probabilistic",
                    "reason": "test",
                },
                "q3": {
                    "strength": "exact" if q3_exact else "probabilistic",
                    "reason": "test",
                },
            },
        }
    )


@pytest.mark.parametrize(
    ("q1_exact", "q3_exact", "locked_text", "unlocked_text"),
    (
        (True, False, "已锁 白绿3件/7格", "未锁 蓝1"),
        (False, True, "已锁 蓝1件/2格", "未锁 白绿3"),
    ),
)
def test_maria_mixed_strict_state_uses_canonical_summary(
    q1_exact: bool,
    q3_exact: bool,
    locked_text: str,
    unlocked_text: str,
) -> None:
    summary = _maria_summary(
        _maria_result(),
        quality_composition=_low_tier_composition(
            q1_exact=q1_exact,
            q3_exact=q3_exact,
        ),
    )

    assert not summary.startswith("清点已知 ")
    assert locked_text in summary
    assert unlocked_text in summary


def test_maria_full_strict_tier_blocks_joint_only_count_known_branch() -> None:
    result = _maria_result()
    composition = _low_tier_composition(q1_exact=True, q3_exact=False)
    composition["qualities"]["q3"]["count_candidates"] = [
        {"value": 1, "probability": 0.9998},
    ]

    summary = _maria_summary(result, quality_composition=composition)

    assert not summary.startswith("清点已知 ")


def test_maria_joint_only_exact_tier_never_authorizes_count_known_branch() -> None:
    summary = _maria_summary(
        _maria_result(),
        quality_composition=_low_tier_composition(
            q1_exact=False,
            q3_exact=True,
        ),
    )

    assert not summary.startswith("清点已知 ")
    assert "已锁 蓝1件/2格" in summary


@pytest.mark.parametrize(
    ("action_round", "qualification"),
    (
        (1, "official_random_warehouse"),
        (2, "official_random_warehouse"),
        (1, "postupdate_unknown_warehouse_source"),
        (2, "postupdate_unknown_warehouse_source"),
    ),
)
def test_maria_r1_evidence_survives_r2_without_qualification_gate(
    action_round: int,
    qualification: str,
) -> None:
    summary = summarize_snapshot(
        _maria_snapshot(
            action_round=action_round,
            qualification=qualification,
        ),
        snapshot_path=Path(f"maria-{action_round}-{qualification}.json"),
    )

    assert summary["red"]["uncertainty_summary"] == (
        EXPECTED_MARIA_LOW_TIER_SUMMARY
    )


def test_maria_malformed_scalars_and_notes_are_zero_magic_fail_closed() -> None:
    magic_calls: list[str] = []

    class EqBomb:
        def __eq__(self, _other: object) -> bool:
            magic_calls.append("eq")
            return False

        def __str__(self) -> str:
            magic_calls.append("str")
            return "3"

    class StrBomb(str):
        def __bool__(self) -> bool:
            magic_calls.append("bool")
            return True

        def __eq__(self, _other: object) -> bool:
            magic_calls.append("eq")
            return False

        def __str__(self) -> str:
            magic_calls.append("str")
            return "maria"

    cases: list[tuple[str, Any]] = []

    hero_bomb = _maria_result()
    hero_bomb["evidence"]["hero"] = StrBomb("maria")
    cases.append(("hero", hero_bomb))

    note_bomb = _maria_result()
    note_bomb["evidence"]["source_notes"] = [
        EqBomb(),
        *note_bomb["evidence"]["source_notes"],
    ]
    cases.append(("source-note", note_bomb))

    count_bomb = _maria_result()
    count_bomb["evidence"]["min_counts"]["q1"] = EqBomb()
    cases.append(("count", count_bomb))

    for label, result in cases:
        assert not _maria_summary(result).startswith("清点已知 "), label

    assert magic_calls == []


def test_maria_mapping_and_sequence_subclasses_are_zero_magic_fail_closed() -> None:
    magic_calls: list[str] = []

    class DictBomb(dict):
        def get(self, *_args: object, **_kwargs: object) -> object:
            magic_calls.append("get")
            return 3

    class ListBomb(list):
        def __contains__(self, _item: object) -> bool:
            magic_calls.append("contains")
            return True

    cases: list[tuple[str, dict[str, Any]]] = []
    min_counts = _maria_result()
    min_counts["evidence"]["min_counts"] = DictBomb(
        min_counts["evidence"]["min_counts"]
    )
    cases.append(("min-counts", min_counts))

    split_counts = _maria_result()
    split_counts["evidence"]["split_counts"] = DictBomb(
        split_counts["evidence"]["split_counts"]
    )
    cases.append(("split-counts", split_counts))

    notes = _maria_result()
    notes["evidence"]["source_notes"] = ListBomb(
        notes["evidence"]["source_notes"]
    )
    cases.append(("source-notes", notes))

    for label, result in cases:
        assert not _maria_summary(result).startswith("清点已知 "), label

    assert magic_calls == []


@pytest.mark.parametrize("location", ("root", "evidence"))
def test_maria_root_and_evidence_dict_subclasses_are_zero_magic_fail_closed(
    location: str,
) -> None:
    magic_calls: list[str] = []

    class DictBomb(dict):
        def get(self, *_args: object, **_kwargs: object) -> object:
            magic_calls.append("get")
            raise AssertionError("dict subclass get executed")

        def __iter__(self):
            magic_calls.append("iter")
            raise AssertionError("dict subclass iter executed")

        def __bool__(self) -> bool:
            magic_calls.append("bool")
            raise AssertionError("dict subclass bool executed")

        def __str__(self) -> str:
            magic_calls.append("str")
            raise AssertionError("dict subclass str executed")

        def __eq__(self, _other: object) -> bool:
            magic_calls.append("eq")
            raise AssertionError("dict subclass eq executed")

    result = _maria_result()
    if location == "root":
        result = DictBomb(result)
    else:
        result["evidence"] = DictBomb(result["evidence"])

    assert _maria_summary(result) == "-"
    assert magic_calls == []


@pytest.mark.parametrize(
    ("owner", "field", "allows_count_known"),
    (
        ("root", "quality_count_ranges", True),
        ("root", "quality_cells_ranges", True),
        ("evidence", "min_counts", False),
        ("evidence", "fixed_counts", True),
        ("evidence", "split_counts", False),
        ("evidence", "split_quality_cells", True),
    ),
)
def test_maria_auxiliary_dict_subclasses_are_zero_magic_fail_closed(
    owner: str,
    field: str,
    allows_count_known: bool,
) -> None:
    magic_calls: list[str] = []

    class DictBomb(dict):
        def get(self, *_args: object, **_kwargs: object) -> object:
            magic_calls.append("get")
            raise AssertionError("dict subclass get executed")

        def __iter__(self):
            magic_calls.append("iter")
            raise AssertionError("dict subclass iter executed")

        def __bool__(self) -> bool:
            magic_calls.append("bool")
            raise AssertionError("dict subclass bool executed")

        def __str__(self) -> str:
            magic_calls.append("str")
            raise AssertionError("dict subclass str executed")

        def __eq__(self, _other: object) -> bool:
            magic_calls.append("eq")
            raise AssertionError("dict subclass eq executed")

    result = _maria_result()
    container = result if owner == "root" else result["evidence"]
    container[field] = DictBomb(container.get(field, {}))

    assert _maria_summary(result).startswith("清点已知 ") is allows_count_known
    assert magic_calls == []


def test_maria_summary_api_and_tk_projection_match(tmp_path: Path) -> None:
    snapshot = _maria_snapshot()
    original = copy.deepcopy(snapshot)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(
            ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - self-created loopback product contract
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=30,
        ) as response:
            assert response.status == 200
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)

    tk_row = _summary_diagnostic_row(
        payload,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )

    assert snapshot == original
    assert summary["red"]["uncertainty_summary"] == (
        EXPECTED_MARIA_LOW_TIER_SUMMARY
    )
    assert payload["red"]["uncertainty_summary"] == (
        EXPECTED_MARIA_LOW_TIER_SUMMARY
    )
    assert tk_row["red"]["uncertainty_summary"] == (
        EXPECTED_MARIA_LOW_TIER_SUMMARY
    )


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_maria_summary_stays_visible_at_510_width_all_dpi(
    tmp_path: Path,
    dpi_scale: float,
) -> None:
    tk = pytest.importorskip("tkinter")
    root = None
    last_error = None
    for _attempt in range(3):
        try:
            root = tk.Tk()
            break
        except tk.TclError as exc:
            last_error = exc
    if root is None:
        pytest.skip(f"真 Tk 不可用(环境层): {last_error}")
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.attributes("-alpha", 0.0)
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        overlay = AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"maria-{dpi_scale}" / "snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        summary = summarize_snapshot(
            _maria_snapshot(),
            snapshot_path=tmp_path / f"maria-summary-{dpi_scale}.json",
        )
        overlay.render(summary)
        root.update_idletasks()
        root.update()

        white_green = overlay.red_rows["白绿"]
        blue = overlay.red_rows["蓝品"]
        assert root.winfo_width() == 510
        assert white_green.winfo_ismapped()
        assert blue.winfo_ismapped()
        assert white_green.cget("text") == "3件·6–8格"
        assert blue.cget("text") == "1件·1–3格"
        assert overlay.quality_state_badges["白绿"].cget("text") == "件锁"
        assert overlay.quality_state_badges["蓝品"].cget("text") == "件锁"
        assert "\n" not in str(white_green.cget("text"))
        assert "\n" not in str(blue.cget("text"))
        assert EXPECTED_MARIA_LOW_TIER_SUMMARY in overlay._red_hover(
            "低品件",
            summary["red"],
            {},
        )
        for value in (white_green, blue):
            assert (
                value.winfo_rootx() + value.winfo_width()
                <= root.winfo_rootx() + root.winfo_width()
            )
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass
