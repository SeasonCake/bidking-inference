from __future__ import annotations

import json
import sys
import tkinter.font as tkfont
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (
    ROOT / "hero_ref" / "src",
    ROOT / "hero_ref" / "tools",
    ROOT / "tests",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ahmad_live_panel_server import _quality_composition_payload  # noqa: E402
from detail_composition_view import (  # noqa: E402
    CompositionCandidate,
    QualityComposition,
    detail_composition_view,
    format_compact_quality_count,
    format_compact_quality_footprint,
    format_quality_dimension_state_badge,
    quality_dimension_is_exact,
    quality_dimension_triplet,
    quality_paired_candidate_triplet,
)
from test_ahmed_4528_probabilistic_red_display_hotfix2 import (  # noqa: E402
    _summary,
)
from test_live_overlay import _ahmad_overlay_module  # noqa: E402
from test_overlay_card_remap_real_tk import _new_root  # noqa: E402


QUALITY_ORDER = ("q1", "q3", "q4", "q5", "q6")


def _2409_r4_result() -> dict[str, object]:
    """Privacy-safe reduction of the exact 2409 R4 pre-settlement evidence."""

    counts = {"q1": 8, "q3": 25, "q4": 11, "q5": 5}
    cells = {"q1": 16, "q3": 76, "q4": 44, "q5": 13}
    joint = {
        key: [{"count": count, "cells": cells[key], "prob": 1.0}]
        for key, count in counts.items()
    }
    joint["q6"] = [
        {"count": 1, "cells": 2, "prob": 0.2},
        {"count": 1, "cells": 3, "prob": 0.5},
        {"count": 1, "cells": 8, "prob": 0.3},
    ]
    return {
        "status": "ok",
        "conservative": 381_649,
        "balanced": 422_312,
        "aggressive": 462_975,
        "quality_count_ranges": {
            **{key: [value, value, value] for key, value in counts.items()},
            "q6": [1, 1, 1],
        },
        "quality_cells_ranges": {
            **{key: [value, value, value] for key, value in cells.items()},
            "q6": [2, 3, 8],
        },
        "quality_count_cells_candidates": joint,
        "quality_lock": {
            **{
                key: {"strength": "exact", "reason": "exact_lock"}
                for key in counts
            },
            "q6": {"strength": "exact", "reason": "derived_single_solution"},
        },
        "quality_count_arms": [
            {"q1": 8, "q3": 25, "q4": 11, "q5": 5, "q6": 1}
        ],
        "evidence": {
            "total_count": 50,
            "total_grid_target": None,
            "fixed_counts": counts,
            "quality_cells": cells,
        },
    }


def _q6_view(result: dict[str, object]):
    payload = _quality_composition_payload(result)
    return payload, detail_composition_view({"quality_composition": payload}).quality(
        "q6"
    )


def _paired_view(key: str, candidates: list[dict[str, object]]):
    return QualityComposition(
        key=key,
        label=key,
        status="ready",
        source="pricing_posterior",
        lock_strength="probabilistic",
        lock_reason="posterior",
        candidates=tuple(
            CompositionCandidate(
                count=int(row["count"]),
                cells=int(row["cells"]),
                probability=float(row["probability"]),
            )
            for row in candidates
        ),
        count_candidates=(),
        cells_candidates=(),
        shown_probability=1.0,
        other_probability=0.0,
        truncated=False,
        cap_hit=False,
        completion_flags_complete=True,
        pricing_posterior=True,
        display_reconciled=False,
        reason="",
    )


def test_2402_gold_intermediate_uses_three_physical_pairs() -> None:
    quality = _paired_view(
        "q5",
        [
            {"count": 6, "cells": 17, "probability": 0.7},
            {"count": 18, "cells": 51, "probability": 0.3},
        ],
    )
    paired = quality_paired_candidate_triplet(quality)
    assert paired is not None
    assert [(row.count, row.cells) for row in paired if row is not None] == [
        (6, 17),
        (6, 17),
        (18, 51),
    ]
    assert format_compact_quality_footprint(quality) == "6–18件·17–51格"
    assert "12件" not in format_compact_quality_footprint(quality)


def test_2404_red_positive_count_zero_cells_is_pending() -> None:
    quality = _paired_view(
        "q6", [{"count": 1, "cells": 0, "probability": 1.0}]
    )
    assert format_compact_quality_footprint(quality) == "—件·—格"


def test_impossible_red_joint_rows_make_only_red_quality_pending() -> None:
    result = _2409_r4_result()
    candidates = result["quality_count_cells_candidates"]
    assert isinstance(candidates, dict)
    candidates["q6"] = [
        {"count": 12, "cells": 0, "prob": 0.2},
        {"count": 1, "cells": 0, "prob": 0.5},
        {"count": 0, "cells": 53, "prob": 0.3},
    ]

    payload = _quality_composition_payload(result)
    view = detail_composition_view({"quality_composition": payload})

    assert payload["status"] == "ready"
    assert view.quality("q6").status == "unavailable"
    assert view.quality("q5").status in {"ready", "exact"}


def test_real_tk_rejects_12_1_0_count_vs_0_0_53_cells(
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "invalid-pair" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        quality = _paired_view(
            "q6",
            [
                {"count": 12, "cells": 0, "probability": 0.2},
                {"count": 1, "cells": 0, "probability": 0.5},
                {"count": 0, "cells": 53, "probability": 0.3},
            ],
        )

        overlay._set_red_count_display(quality, fallback="12 / 1 / 0", hover="")
        root.update_idletasks()
        root.update()

        assert tuple(
            overlay.red_count_triplet_labels[key].cget("text")
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        ) == ("保—", "/", "均—", "/", "激—")
        assert overlay._red_cells_display(quality) == "—格"
    finally:
        root.destroy()


def test_total_and_four_exact_counts_lock_only_the_red_count_residual() -> None:
    result = _2409_r4_result()
    quotes_before = tuple(result[key] for key in ("conservative", "balanced", "aggressive"))

    payload, q6 = _q6_view(result)

    assert payload["count_correlation_total"] == 50
    assert payload["authoritative_count_arms"] == [
        {"q1": 8, "q3": 25, "q4": 11, "q5": 5, "q6": 1}
    ]
    assert q6.count_lock_value == 1
    assert q6.cells_lock_value is None
    assert q6.dimension_state == "件锁"
    assert format_quality_dimension_state_badge(q6) == "件锁"
    assert quality_dimension_is_exact(q6, "count")
    assert not quality_dimension_is_exact(q6, "cells")
    assert quality_dimension_triplet(q6, "count") == (1, 1, 1)
    assert quality_dimension_triplet(q6, "cells") == (2, 3, 8)
    assert format_compact_quality_count(q6, fallback="1 / 1 / 1") == "1件"
    assert format_compact_quality_footprint(q6) == "1件·2–8格"
    assert tuple(
        result[key] for key in ("conservative", "balanced", "aggressive")
    ) == quotes_before


class _CountIntSubclass(int):
    pass


@pytest.mark.parametrize(
    "case",
    (
        "missing_total",
        "missing_non_red_count",
        "bool_total",
        "wrong_count_type",
        "count_subclass",
        "negative_residual",
        "count_over_total",
    ),
)
def test_residual_count_lock_fails_closed_without_exact_atomic_inputs(case: str) -> None:
    result = _2409_r4_result()
    evidence = result["evidence"]
    assert isinstance(evidence, dict)
    if case == "missing_total":
        evidence.pop("total_count")
    elif case == "missing_non_red_count":
        candidates = result["quality_count_cells_candidates"]
        locks = result["quality_lock"]
        assert isinstance(candidates, dict) and isinstance(locks, dict)
        candidates.pop("q4")
        locks.pop("q4")
    elif case == "bool_total":
        evidence["total_count"] = True
    elif case == "wrong_count_type":
        ranges = result["quality_count_ranges"]
        assert isinstance(ranges, dict)
        ranges["q4"] = ["11", "11", "11"]
    elif case == "count_subclass":
        candidates = result["quality_count_cells_candidates"]
        assert isinstance(candidates, dict)
        candidates["q4"][0]["count"] = _CountIntSubclass(11)
    elif case == "negative_residual":
        evidence["total_count"] = 40
        arms = result["quality_count_arms"]
        assert isinstance(arms, list)
        arms[0] = {"q1": 8, "q3": 15, "q4": 11, "q5": 5, "q6": 1}
    else:
        ranges = result["quality_count_ranges"]
        candidates = result["quality_count_cells_candidates"]
        fixed = evidence["fixed_counts"]
        assert isinstance(ranges, dict)
        assert isinstance(candidates, dict)
        assert isinstance(fixed, dict)
        ranges["q1"] = [51, 51, 51]
        candidates["q1"] = [{"count": 51, "cells": 102, "prob": 1.0}]
        fixed["q1"] = 51

    payload, q6 = _q6_view(result)

    assert q6.count_lock_value is None
    assert not quality_dimension_is_exact(q6, "count")
    assert quality_dimension_triplet(q6, "count") is None
    assert q6.count_correlation_state == "unknown"
    assert format_compact_quality_count(q6, fallback="1 / 1 / 1") == "联动待定"
    assert format_quality_dimension_state_badge(q6) == "未锁"
    if case == "missing_total":
        assert "count_correlation_total" not in payload
        q1 = detail_composition_view({"quality_composition": payload}).quality("q1")
        assert q1.count_lock_value == 8


@pytest.mark.parametrize("bad_count", (True, _CountIntSubclass(11)))
def test_non_red_wrong_type_keeps_red_as_pending_not_composition_conflict(
    bad_count: object,
) -> None:
    result = _2409_r4_result()
    candidates = result["quality_count_cells_candidates"]
    assert isinstance(candidates, dict)
    candidates["q4"][0]["count"] = bad_count

    payload, q6 = _q6_view(result)

    assert payload["status"] == "ready"
    assert q6.count_lock_value is None
    assert q6.count_correlation_state == "unknown"
    assert quality_dimension_triplet(q6, "count") is None
    assert format_compact_quality_count(q6, fallback="1 / 1 / 1") == "联动待定"
    assert format_quality_dimension_state_badge(q6) == "未锁"


def test_probabilistic_lock_metadata_does_not_erase_equivalent_exact_authority() -> None:
    result = _2409_r4_result()
    locks = result["quality_lock"]
    assert isinstance(locks, dict)
    locks["q4"] = {"strength": "probabilistic", "reason": "not_exact"}

    _payload, q6 = _q6_view(result)

    assert q6.count_lock_value == 1
    assert quality_dimension_is_exact(q6, "count")


def test_multiple_complete_arms_with_different_red_residuals_stay_pending() -> None:
    result = _2409_r4_result()
    arms = result["quality_count_arms"]
    assert isinstance(arms, list)
    arms.append({"q1": 7, "q3": 25, "q4": 11, "q5": 5, "q6": 2})

    _payload, q6 = _q6_view(result)

    assert q6.count_lock_value is None
    assert q6.count_correlation_state == "unknown"
    assert quality_dimension_triplet(q6, "count") is None
    assert format_compact_quality_count(q6, fallback="1 / 1 / 1") == "联动待定"


def test_total_count_unknown_cross_quality_correlation_keeps_physical_red_pairs() -> None:
    """Unknown cross-tier linkage must not erase valid q6 count/cell pairs."""

    result = _2409_r4_result()
    arms = result["quality_count_arms"]
    assert isinstance(arms, list)
    arms.append({"q1": 7, "q3": 25, "q4": 11, "q5": 5, "q6": 2})

    _payload, q6 = _q6_view(result)
    triplet = quality_paired_candidate_triplet(q6)

    assert q6.count_correlation_state == "unknown"
    assert triplet is not None
    assert all(candidate is not None for candidate in triplet)
    assert [(candidate.count, candidate.cells) for candidate in triplet if candidate] == [
        (1, 2),
        (1, 3),
        (1, 8),
    ]


def test_probability_roundoff_at_one_does_not_blank_unrelated_red_pairs() -> None:
    """Package-shaped q5 mass must not turn a valid q6 view into root conflict."""

    result = _2409_r4_result()
    probabilities = (
        0.2706,
        0.2690,
        0.1814,
        0.1601,
        0.0845,
        0.0275,
        0.0059,
        0.0007,
        0.0001,
        0.0001,
        0.0001,
    )
    joint = result["quality_count_cells_candidates"]
    assert isinstance(joint, dict)
    joint["q5"] = [
        {"count": index, "cells": index, "prob": probability}
        for index, probability in enumerate(probabilities, 1)
    ]

    payload = _quality_composition_payload(result)
    view = detail_composition_view({"quality_composition": payload})
    q5_payload = payload["qualities"]["q5"]
    q6 = view.quality("q6")
    triplet = quality_paired_candidate_triplet(q6)

    assert q5_payload["shown_probability"] == 1.0
    assert q5_payload["other_probability"] == 0.0
    assert view.status == "ready"
    assert q6.status == "ready"
    assert triplet is not None
    assert all(candidate is not None for candidate in triplet)


def test_safe_telemetry_marks_unknown_cross_quality_without_logging_total(
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    result = _2409_r4_result()
    arms = result["quality_count_arms"]
    assert isinstance(arms, list)
    arms.append({"q1": 7, "q3": 25, "q4": 11, "q5": 5, "q6": 2})
    payload, _q6 = _q6_view(result)
    summary = _summary()
    summary["quality_composition"] = payload
    result["notes"] = ["action_100115_total_count"]
    summary["ahmed_ref"] = result

    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    overlay.red_count_triplet_labels = {}
    overlay._summary_worker_seq = 12
    overlay._last_live_snapshot = {}
    module.AhmadTkOverlay._record_safe_frame_telemetry(
        overlay,
        summary,
        paired_presence=True,
    )

    path = tmp_path / module.SAFE_FRAME_TELEMETRY_LOG
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["schema"] == 3
    assert row["q6_status"] == "ready"
    assert row["q6_correlation_state"] == "unknown"
    assert row["q6_reason_code"] == "unknown_correlation"
    assert row["q6_candidate_count"] == 3
    assert row["q6_candidates_physical"] is True
    assert row["q6_triplet_complete"] is True
    assert row["total_count_present"] is True
    assert row["total_count_sources"] == ["inventory_census"]
    assert "50" not in path.read_text(encoding="utf-8")


def _2409_ui_summary() -> dict[str, object]:
    payload, q6 = _q6_view(_2409_r4_result())
    summary = _summary()
    summary["quality_composition"] = payload
    red = summary["red"]
    assert isinstance(red, dict)
    red["count_range"] = "1 / 1 / 1"
    red["count_display"] = format_compact_quality_count(q6, fallback="1 / 1 / 1")
    red["cells_range"] = "2 / 3 / 8"
    return summary


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_real_tk_total_count_unknown_cross_quality_keeps_red_pairs_visible(
    tmp_path: Path,
    dpi_scale: float,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"total-count-q6-{dpi_scale}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        result = _2409_r4_result()
        arms = result["quality_count_arms"]
        assert isinstance(arms, list)
        arms.append({"q1": 7, "q3": 25, "q4": 11, "q5": 5, "q6": 2})
        payload, q6 = _q6_view(result)
        assert q6.count_correlation_state == "unknown"
        summary = _summary()
        summary["quality_composition"] = payload
        summary.pop("quote_receipt", None)
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")

        overlay.render(summary)
        root.update_idletasks()
        root.update()

        assert tuple(
            overlay.red_count_triplet_labels[key].cget("text")
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        ) == ("保1", "/", "均1", "/", "激1")
        assert overlay.red_rows["红格"].cget("text") == "2/3/8格"
        assert overlay.quality_state_badges["红件"].cget("text") == "待"
        assert overlay.quality_state_badges["红格"].cget("text") == "待"
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except Exception:
            pass


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_real_tk_shows_red_count_lock_without_locking_red_cells(
    tmp_path: Path,
    dpi_scale: float,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"tk-{dpi_scale}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        overlay.render(_2409_ui_summary())
        root.update_idletasks()
        root.update()

        assert overlay.quality_state_badges["红件"].cget("text") == "锁"
        assert overlay.red_count_triplet_frame.winfo_ismapped()
        assert tuple(
            overlay.red_count_triplet_labels[key].cget("text")
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        ) == ("保1", "/", "均1", "/", "激1")
        triplet_fonts = tuple(
            int(tkfont.Font(root=root, font=label.cget("font")).cget("size"))
            for label in overlay.red_count_triplet_labels.values()
        )
        red_cells_font = int(
            tkfont.Font(root=root, font=overlay.red_rows["红格"].cget("font")).cget(
                "size"
            )
        )
        expected_floor = (
            module.RED_COUNT_INLINE_FONT_SIZE - 1
            if dpi_scale == 1.75
            else module.RED_COUNT_INLINE_FONT_SIZE
        )
        assert min(triplet_fonts) >= expected_floor
        assert len(set(triplet_fonts)) == 1
        assert red_cells_font >= module.SUMMARY_VALUE_FONT_SIZE
        assert triplet_fonts[0] >= red_cells_font
        assert all(
            overlay.red_count_triplet_labels[key].winfo_manager() == "pack"
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        )
        assert len(
            {
                overlay.red_count_triplet_labels[key].winfo_y()
                for key in ("low", "sep_left", "middle", "sep_right", "high")
            }
        ) == 1
        assert overlay.red_rows["红格"].cget("text") == "2/3/8格"
        assert overlay.quality_state_badges["红格"].cget("text") == "待"
        count_value = overlay.red_count_triplet_frame
        count_badge = overlay.quality_state_badges["红件"]
        assert count_value.winfo_x() + count_value.winfo_width() <= count_badge.winfo_x()

        long_quality = _paired_view(
            "q6",
            [{"count": 100, "cells": 300, "probability": 1.0}],
        )
        overlay._set_red_count_display(
            long_quality,
            fallback=None,
            hover="完整候选保留在详情",
        )
        root.update_idletasks()
        root.update()
        assert tuple(
            overlay.red_count_triplet_labels[key].cget("text")
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        ) in {
            ("保—", "/", "均—", "/", "激—"),
            ("保–", "/", "均–", "/", "激–"),
        }
        assert all(
            int(tkfont.Font(root=root, font=label.cget("font")).cget("size"))
            >= module.RED_COUNT_INLINE_FONT_SIZE
            for label in overlay.red_count_triplet_labels.values()
        )
        assert (
            overlay.red_count_triplet_labels["low"]._hover_tip.text
            == "完整候选保留在详情"
        )
        assert count_value.winfo_x() + count_value.winfo_width() <= count_badge.winfo_x()

        overlay._set_red_count_display(None, fallback=None, hover="")
        root.update_idletasks()
        root.update()
        assert tuple(
            overlay.red_count_triplet_labels[key].cget("text")
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        ) in {
            ("保—", "/", "均—", "/", "激—"),
            ("保–", "/", "均–", "/", "激–"),
        }
        assert count_value.winfo_x() + count_value.winfo_width() <= count_badge.winfo_x()
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except Exception:
            pass


def test_real_tk_new_round_without_current_pair_keeps_three_visible_pending_values(
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "freshness" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        summary = _2409_ui_summary()
        context = summary["context"]
        assert isinstance(context, dict)
        context["session_id"] = "fresh-session"
        context["round"] = 2
        summary["continuity"] = {"source_round": "1"}
        summary.pop("quote_receipt", None)

        overlay.render(summary)
        root.update_idletasks()
        root.update()

        assert overlay.red_count_triplet_frame.winfo_ismapped()
        assert tuple(
            overlay.red_count_triplet_labels[key].cget("text")
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        ) == ("保—", "/", "均—", "/", "激—")
        assert overlay.red_rows["紫品"].cget("text") == "—件·—格"
        assert overlay.red_rows["金品"].cget("text") == "—件·—格"
    finally:
        root.destroy()


def test_real_tk_current_paired_gold_and_purple_ranges_never_collapse_to_pending(
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * 1.75)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "paired-ranges" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        result = {
            "status": "ok",
            "quality_count_cells_candidates": {
                "q6": [{"count": 4, "cells": 13, "prob": 1.0}],
                "q5": [
                    {"count": 8, "cells": 47, "prob": 0.996},
                    {"count": 16, "cells": 94, "prob": 0.004},
                ],
                "q4": [
                    {"count": 11, "cells": 37, "prob": 0.846},
                    {"count": 22, "cells": 74, "prob": 0.154},
                ],
                "q3": [{"count": 13, "cells": 29, "prob": 1.0}],
                "q1": [{"count": 11, "cells": 25, "prob": 1.0}],
            },
            "quality_lock": {
                key: {
                    "strength": "probabilistic" if key in {"q4", "q5"} else "exact",
                    "reason": "combo_multi_solution" if key in {"q4", "q5"} else "exact_lock",
                }
                for key in ("q6", "q5", "q4", "q3", "q1")
            },
            "evidence": {},
        }
        summary = _summary()
        summary["quality_composition"] = _quality_composition_payload(result)
        summary.pop("quote_receipt", None)
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        overlay.render(summary)
        root.update_idletasks()
        root.update()

        assert overlay.red_rows["金品"].cget("text") == "8–16件·47–94格"
        assert overlay.red_rows["紫品"].cget("text") == "11–22件·37–74格"
        assert overlay.quality_state_badges["金品"].cget("text") == "未锁"
        assert overlay.quality_state_badges["紫品"].cget("text") == "未锁"

        context = summary["context"]
        assert isinstance(context, dict)
        context["round"] = 3
        summary["continuity"] = {
            "reason": "quality_composition_conflict",
            "source_round": "2",
        }
        overlay.render(summary)
        root.update_idletasks()
        assert overlay.red_rows["金品"].cget("text") == "8–16件·47–94格"
        assert overlay.red_rows["紫品"].cget("text") == "11–22件·37–74格"
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except Exception:
            pass


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_real_tk_fractional_average_uses_discrete_physical_gold_values(
    tmp_path: Path,
    dpi_scale: float,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"fractional-average-{dpi_scale}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        result = {
            "status": "ok",
            "quality_count_cells_candidates": {
                "q6": [{"count": 4, "cells": 13, "prob": 1.0}],
                "q5": [
                    {"count": 8, "cells": 47, "prob": 0.10},
                    {"count": 16, "cells": 94, "prob": 0.90},
                ],
                "q4": [{"count": 11, "cells": 37, "prob": 1.0}],
                "q3": [{"count": 13, "cells": 29, "prob": 1.0}],
                "q1": [{"count": 11, "cells": 25, "prob": 1.0}],
            },
            "quality_lock": {
                key: {
                    "strength": "probabilistic" if key == "q5" else "exact",
                    "reason": "combo_multi_solution" if key == "q5" else "exact_lock",
                }
                for key in ("q6", "q5", "q4", "q3", "q1")
            },
            "evidence": {"avg_values": {"q5": 37_494.125}},
        }
        payload = _quality_composition_payload(result)
        assert payload["qualities"]["q5"]["discrete_count_display"] is True
        assert payload["qualities"]["q4"]["discrete_count_display"] is False
        summary = _summary()
        summary["quality_composition"] = payload
        summary.pop("quote_receipt", None)
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")

        overlay.render(summary)
        root.update_idletasks()
        root.update()

        gold = overlay.red_rows["金品"]
        assert gold.cget("text") == "8/16/16件·47–94格"
        assert "..." not in str(gold.cget("text"))
        assert overlay.quality_state_badges["金品"].cget("text") == "未锁"
        assert gold.winfo_x() + gold.winfo_width() <= gold.master.winfo_width()
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except Exception:
            pass


def test_real_tk_total_count_keeps_main_rows_compact_and_paired(
    tmp_path: Path,
) -> None:
    """2409 R2 shape: total-count evidence must not turn the card into hover."""

    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * 1.75)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "total-count-envelope" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        result = {
            "status": "ok",
            "quality_count_cells_candidates": {
                "q6": [{"count": 3, "cells": 9, "prob": 1.0}],
                "q5": [
                    {"count": 0, "cells": 0, "prob": 0.2},
                    {"count": 6, "cells": 16, "prob": 0.5},
                    {"count": 8, "cells": 21, "prob": 0.3},
                ],
                "q4": [
                    {"count": 10, "cells": 34, "prob": 0.3},
                    {"count": 15, "cells": 51, "prob": 0.5},
                    {"count": 40, "cells": 136, "prob": 0.2},
                ],
                "q3": [
                    {"count": 19, "cells": 41, "prob": 0.3},
                    {"count": 20, "cells": 43, "prob": 0.5},
                    {"count": 21, "cells": 47, "prob": 0.2},
                ],
                "q1": [{"count": 10, "cells": 19, "prob": 1.0}],
            },
            "quality_lock": {
                key: {
                    "strength": "exact" if key in {"q1", "q6"} else "probabilistic",
                    "reason": "exact_lock" if key in {"q1", "q6"} else "combo_multi_solution",
                }
                for key in ("q6", "q5", "q4", "q3", "q1")
            },
            "evidence": {"total_count": 53},
        }
        summary = _summary()
        summary["quality_composition"] = _quality_composition_payload(result)
        summary.pop("quote_receipt", None)
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")

        overlay.render(summary)
        root.update_idletasks()
        root.update()

        assert overlay.red_rows["金品"].cget("text") == "0–8件·0–21格"
        assert overlay.red_rows["紫品"].cget("text") == "10–40件·34–136格"
        assert overlay.red_rows["蓝品"].cget("text") == "19–21件·41–47格"
        assert overlay.red_rows["白绿"].cget("text") == "10件·19格"
        for key in ("金品", "紫品", "蓝品", "白绿"):
            text = str(overlay.red_rows[key].cget("text"))
            assert "/" not in text
            assert overlay.red_rows[key].winfo_width() <= overlay.red_rows[key].master.winfo_width()
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except Exception:
            pass


def test_quality_row_fallback_is_limited_to_verified_nonpending_text() -> None:
    module = _ahmad_overlay_module()
    unavailable = QualityComposition(
        key="q5",
        label="金品",
        status="unavailable",
        source="",
        lock_strength="",
        lock_reason="",
        candidates=(),
        count_candidates=(),
        cells_candidates=(),
        shown_probability=0.0,
        other_probability=0.0,
        truncated=False,
        cap_hit=False,
        completion_flags_complete=False,
        pricing_posterior=False,
        display_reconciled=False,
        reason="missing_current_pair",
    )

    assert module.format_quality_row_with_verified_fallback(
        unavailable,
        "8/16件 · 47/94格",
        allow_fallback=False,
    ) == "—件·—格"
    assert module.format_quality_row_with_verified_fallback(
        unavailable,
        "待定件 · 47/94格",
        allow_fallback=True,
    ) == "—件·—格"
    assert module.format_quality_row_with_verified_fallback(
        unavailable,
        "8件 · 47格",
        allow_fallback=True,
    ) == "8件 · 47格"
    for impossible in (
        "12件 · 0格",
        "0件 · 10格",
        "8/16件 · 47/94格",
    ):
        assert module.format_quality_row_with_verified_fallback(
            unavailable,
            impossible,
            allow_fallback=True,
        ) == "—件·—格"
    assert module.format_quality_row_with_verified_fallback(
        unavailable,
        "8/16件 · 47格",
        allow_fallback=True,
    ) == "8/16件 · 47格"


def test_real_tk_only_verified_continuity_may_retain_legacy_quality_rows(
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    try:
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "verified-fallback" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        summary = _summary()
        summary["quality_composition"] = {
            "schema_version": 1,
            "status": "conflict",
            "reason": "quality_composition_conflict",
            "qualities": {},
        }
        red = summary["red"]
        assert isinstance(red, dict)
        red["quality_count_summary"] = "紫8件/28格 · 金6件/19格"

        overlay.render(summary)
        root.update_idletasks()
        assert overlay.red_rows["紫品"].cget("text") == "—件·—格"
        assert overlay.red_rows["金品"].cget("text") == "—件·—格"

        summary["continuity"] = {
            "reason": "quality_composition_conflict",
            "source_round": "1",
        }
        overlay.render(summary)
        root.update_idletasks()
        assert overlay.red_rows["紫品"].cget("text") == "8件 · 28格"
        assert overlay.red_rows["金品"].cget("text") == "6件 · 19格"

        red["quality_count_summary"] = "紫12件/0格 · 金0件/10格"
        overlay.render(summary)
        root.update_idletasks()
        assert overlay.red_rows["紫品"].cget("text") == "—件·—格"
        assert overlay.red_rows["金品"].cget("text") == "—件·—格"
    finally:
        root.destroy()
