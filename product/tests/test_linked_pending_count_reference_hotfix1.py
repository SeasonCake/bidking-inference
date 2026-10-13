from __future__ import annotations

import copy
import sys
import time
import tkinter.font as tkfont
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ahmad_live_panel_server import _quality_composition_payload  # noqa: E402
from detail_composition_view import (  # noqa: E402
    detail_composition_view,
    format_compact_quality_count,
    format_quality_dimension_state_badge,
    quality_display_count_triplet,
    quality_dimension_is_exact,
    quality_dimension_triplet,
    quality_paired_candidate_triplet,
)
from test_ahmed_4528_probabilistic_red_display_hotfix2 import _summary  # noqa: E402
from test_live_overlay import _ahmad_overlay_module  # noqa: E402
from test_overlay_card_remap_real_tk import _new_root  # noqa: E402


QUALITY_ORDER = ("q1", "q3", "q4", "q5", "q6")


class _CountIntSubclass(int):
    pass


def _linked_pending_result(*, wide_red_reference: bool = False) -> dict[str, object]:
    """Synthetic complete arms; never a claim about a real match."""
    if wide_red_reference:
        # Deliberately wider than the 2539-shaped mechanism fixture.  This is
        # the visible positive control for the pending prefix at high DPI.
        arms = (
            {"q1": 10, "q3": 9, "q4": 8, "q5": 1, "q6": 14},
            {"q1": 10, "q3": 9, "q4": 8, "q5": 5, "q6": 10},
            {"q1": 10, "q3": 9, "q4": 8, "q5": 12, "q6": 3},
        )
        fixed_counts = {"q1": 10, "q3": 9, "q4": 8}
        fixed_cells = {"q1": 20, "q3": 27, "q4": 24, "q6": 24}
    else:
        # 2539-shaped mechanism fixture; not a claim about a real match.
        arms = (
            {"q1": 23, "q3": 16, "q4": 18, "q5": 1, "q6": 6},
            {"q1": 23, "q3": 16, "q4": 18, "q5": 4, "q6": 3},
            {"q1": 23, "q3": 16, "q4": 18, "q5": 6, "q6": 1},
        )
        fixed_counts = {"q1": 23, "q3": 16, "q4": 18}
        fixed_cells = {"q1": 46, "q3": 48, "q4": 54, "q6": 28}
    probabilities = (0.2, 0.6, 0.2)

    def rows_for(key: str) -> list[dict[str, object]]:
        seen: set[int] = set()
        rows: list[dict[str, object]] = []
        cells = fixed_cells.get(key, 20 if key == "q5" else 28)
        for arm, probability in zip(arms, probabilities, strict=True):
            count = arm[key]
            if count in seen:
                continue
            seen.add(count)
            rows.append({"count": count, "cells": cells, "prob": probability})
        return rows

    candidates = {
        key: rows_for(key)
        for key in QUALITY_ORDER
    }
    # Each fixed quality has one complete joint row; q5/q6 retain all three
    # correlated arms.  Do not infer q6 count certainty from q6's exact cells.
    for key, count in fixed_counts.items():
        candidates[key] = [{"count": count, "cells": fixed_cells[key], "prob": 1.0}]
    count_marginals = {
        key: [
            {"value": row["count"], "prob": row["prob"]}
            for row in rows
        ]
        for key, rows in candidates.items()
    }
    cells_marginals = {
        key: [
            {
                "value": fixed_cells.get(key, 20 if key == "q5" else 28),
                "prob": 1.0,
            }
        ]
        for key in QUALITY_ORDER
    }
    count_ranges = {
        key: [
            min(arm[key] for arm in arms),
            arms[1][key],
            max(arm[key] for arm in arms),
        ]
        for key in QUALITY_ORDER
    }
    cell_ranges = {
        key: [
            fixed_cells.get(key, 20 if key == "q5" else 28),
            fixed_cells.get(key, 20 if key == "q5" else 28),
            fixed_cells.get(key, 20 if key == "q5" else 28),
        ]
        for key in QUALITY_ORDER
    }
    return {
        "status": "ok",
        "quality_count_ranges": count_ranges,
        "quality_cells_ranges": cell_ranges,
        "quality_count_cells_candidates": candidates,
        "quality_count_candidates": count_marginals,
        "quality_cells_candidates": cells_marginals,
        "quality_lock": {
            "q1": {"strength": "exact", "reason": "exact_lock"},
            "q3": {"strength": "exact", "reason": "exact_lock"},
            "q4": {"strength": "exact", "reason": "exact_lock"},
            "q5": {"strength": "probabilistic", "reason": "multiple_arms"},
            "q6": {"strength": "exact", "reason": "derived_single_solution"},
        },
        "quality_count_arms": list(arms),
        "evidence": {
            "total_count": sum(arms[0].values()),
            "total_grid_target": None,
            "fixed_counts": fixed_counts,
            "quality_cells": fixed_cells,
        },
    }


def _q6_view(result: dict[str, object]):
    payload = _quality_composition_payload(result)
    return payload, detail_composition_view({"quality_composition": payload}).quality("q6")


def _summary_for_linked_pending(
    *,
    wide_red_reference: bool = False,
) -> dict[str, object]:
    payload, q6 = _q6_view(
        _linked_pending_result(wide_red_reference=wide_red_reference)
    )
    summary = _summary()
    summary["quality_composition"] = payload
    red = summary["red"]
    assert isinstance(red, dict)
    red["count_range"] = "3 / 10 / 14" if wide_red_reference else "1 / 3 / 6"
    red["count_display"] = format_compact_quality_count(q6, fallback=red["count_range"])
    red["cells_range"] = "24 / 24 / 24" if wide_red_reference else "28 / 28 / 28"
    return summary


def _summary_for_long_red_reference() -> dict[str, object]:
    """Long values exercise the persistent compact surface, not a new quote path."""
    summary = _summary_for_linked_pending(wide_red_reference=True)
    payload = copy.deepcopy(summary["quality_composition"])
    q6 = payload["qualities"]["q6"]
    q5 = payload["qualities"]["q5"]
    arms = [
        {"q1": 10, "q3": 9, "q4": 8, "q5": 84, "q6": 28},
        {"q1": 10, "q3": 9, "q4": 8, "q5": 56, "q6": 56},
        {"q1": 10, "q3": 9, "q4": 8, "q5": 13, "q6": 99},
    ]
    payload["count_correlation_total"] = 139
    payload["authoritative_count_arms"] = copy.deepcopy(arms)
    payload["compact_count_arms"] = copy.deepcopy(arms)
    q6.update(
        {
            "candidates": [
                {"count": 28, "cells": 102, "probability": 0.2},
                {"count": 56, "cells": 196, "probability": 0.6},
                {"count": 99, "cells": 288, "probability": 0.2},
            ],
            "count_candidates": [
                {"value": 56, "probability": 0.6},
                {"value": 28, "probability": 0.2},
                {"value": 99, "probability": 0.2},
            ],
            "cells_candidates": [
                {"value": 196, "probability": 0.6},
                {"value": 102, "probability": 0.2},
                {"value": 288, "probability": 0.2},
            ],
            "compact_count_candidates": [
                {"value": 56, "probability": 0.6},
                {"value": 28, "probability": 0.2},
                {"value": 99, "probability": 0.2},
            ],
            "compact_count_triplet": [28, 56, 99],
            "count_lock_value": None,
            "cells_lock_value": None,
            "count_correlation_state": "complete",
            "dimension_state": "未锁",
            "status": "ready",
        }
    )
    q5.update(
        {
            "candidates": [
                {"count": 84, "cells": 20, "probability": 0.2},
                {"count": 56, "cells": 20, "probability": 0.6},
                {"count": 13, "cells": 20, "probability": 0.2},
            ],
            "count_candidates": [
                {"value": 56, "probability": 0.6},
                {"value": 13, "probability": 0.2},
                {"value": 84, "probability": 0.2},
            ],
            "compact_count_candidates": [
                {"value": 56, "probability": 0.6},
                {"value": 13, "probability": 0.2},
                {"value": 84, "probability": 0.2},
            ],
            "compact_count_triplet": [84, 56, 13],
            "count_correlation_note": "保/均/激完整品质件数臂均满足总件 139",
        }
    )
    summary["quality_composition"] = payload
    return summary


def test_long_red_reference_uses_complete_q6_authority_not_a_display_bypass() -> None:
    q6 = detail_composition_view(_summary_for_long_red_reference()).quality("q6")

    assert q6.status == "ready"
    assert q6.count_correlation_state == "complete"
    assert quality_display_count_triplet(q6) == (28, 56, 99)
    assert quality_dimension_triplet(q6, "cells") == (102, 196, 288)


def test_complete_correlated_nonunique_red_arms_keep_pending_reference_not_count_lock() -> None:
    payload, q6 = _q6_view(_linked_pending_result())

    assert payload["count_correlation_total"] == 64
    assert payload["compact_count_arms"] == [
        {"q1": 23, "q3": 16, "q4": 18, "q5": 1, "q6": 6},
        {"q1": 23, "q3": 16, "q4": 18, "q5": 4, "q6": 3},
        {"q1": 23, "q3": 16, "q4": 18, "q5": 6, "q6": 1},
    ]
    assert q6.count_correlation_state == "complete"
    assert q6.count_lock_value is None
    assert not quality_dimension_is_exact(q6, "count")
    assert q6.cells_lock_value == 28
    assert format_quality_dimension_state_badge(q6) == "格锁"
    assert quality_dimension_triplet(q6, "count") == (1, 3, 6)
    assert format_compact_quality_count(q6, fallback="1 / 3 / 6") == "待定 · 1/3/6件"
    assert "按小/中/大显示" in q6.count_correlation_note


def test_reversed_cross_quality_arms_render_ascending_physical_red_pairs() -> None:
    payload, q6 = _q6_view(_linked_pending_result())
    raw_q6 = payload["qualities"]["q6"]
    raw_q6["candidates"] = [
        {"count": 6, "cells": 18, "probability": 0.2},
        {"count": 3, "cells": 9, "probability": 0.6},
        {"count": 1, "cells": 3, "probability": 0.2},
    ]
    raw_q6["count_candidates"] = [
        {"value": 3, "probability": 0.6},
        {"value": 1, "probability": 0.2},
        {"value": 6, "probability": 0.2},
    ]
    raw_q6["cells_candidates"] = [
        {"value": 9, "probability": 0.6},
        {"value": 3, "probability": 0.2},
        {"value": 18, "probability": 0.2},
    ]
    raw_q6["cells_lock_value"] = None
    raw_q6["dimension_state"] = "未锁"
    q6 = detail_composition_view({"quality_composition": payload}).quality("q6")

    paired = quality_paired_candidate_triplet(q6)

    assert paired is not None
    assert [(row.count, row.cells) for row in paired if row is not None] == [
        (1, 3),
        (3, 9),
        (6, 18),
    ]


@pytest.mark.parametrize("case", ("missing_total", "invalid_total", "no_arms", "cross_authority", "bool"))
def test_linked_pending_reference_fails_closed_without_complete_authority(case: str) -> None:
    result = _linked_pending_result()
    evidence = result["evidence"]
    assert isinstance(evidence, dict)
    if case == "missing_total":
        evidence.pop("total_count")
    elif case == "invalid_total":
        evidence["total_count"] = "64"
    elif case == "no_arms":
        result["quality_count_arms"] = []
    elif case == "cross_authority":
        arms = result["quality_count_arms"]
        assert isinstance(arms, list)
        arms[:] = [
            {"q1": 23, "q3": 16, "q4": 18, "q5": 1, "q6": 6},
            {"q1": 23, "q3": 16, "q4": 18, "q5": 2, "q6": 5},
            {"q1": 23, "q3": 16, "q4": 18, "q5": 5, "q6": 2},
        ]
    else:
        candidates = result["quality_count_cells_candidates"]
        assert isinstance(candidates, dict)
        candidates["q4"][0]["count"] = _CountIntSubclass(18)

    _payload, q6 = _q6_view(result)

    assert q6.count_lock_value is None
    assert quality_dimension_triplet(q6, "count") is None
    assert q6.count_correlation_state == "unknown"
    assert format_compact_quality_count(q6, fallback="1 / 3 / 6") == "联动待定"


@pytest.mark.parametrize("wide_red_reference", (False, True))
@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_real_tk_keeps_pending_reference_and_independent_red_cell_lock(
    tmp_path: Path,
    dpi_scale: float,
    wide_red_reference: bool,
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
        summary = _summary_for_linked_pending(
            wide_red_reference=wide_red_reference
        )
        overlay.render(copy.deepcopy(summary))
        root.update_idletasks()
        root.update()

        expected_triplet = (
            ("保3", "/", "均10", "/", "激14")
            if wide_red_reference
            else ("保1", "/", "均3", "/", "激6")
        )
        expected_cells = "24格" if wide_red_reference else "28格"
        assert overlay.red_count_triplet_frame.winfo_ismapped()
        assert tuple(
            overlay.red_count_triplet_labels[key].cget("text")
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        ) == expected_triplet
        assert overlay.quality_state_badges["红件"].cget("text") == "待"
        assert overlay.red_rows["红格"].cget("text") == expected_cells
        assert overlay.quality_state_badges["红格"].cget("text") == "锁"
        assert overlay.version_label.cget("text") == "v0.3.4"
        assert overlay.version_label.winfo_ismapped()
        assert module.RED_COUNT_INLINE_FONT_SIZE == 8
        fitted_red_font = getattr(overlay, "_red_count_triplet_fit_font", None)
        if not wide_red_reference and dpi_scale < 1.75:
            assert fitted_red_font == round(
                module.RED_COUNT_INLINE_FONT_SIZE * overlay._ui_scale
            )
        else:
            assert isinstance(fitted_red_font, int) and fitted_red_font >= 7
        if dpi_scale == 1.75 and wide_red_reference:
            assert min(
                int(tkfont.Font(root=root, font=label.cget("font")).cget("size"))
                for label in overlay.red_count_triplet_labels.values()
            ) >= module.SUMMARY_COMPACT_MIN_FONT_SIZE
        hover = overlay._red_hover(
            "红件",
            summary["red"],
            summary["reference"],
            quality=_q6_view(
                _linked_pending_result(wide_red_reference=wide_red_reference)
            )[1],
        )
        assert "按小/中/大显示" in hover
        value = overlay.red_count_triplet_frame
        badge = overlay.quality_state_badges["红件"]
        # Reproduce the live lifecycle from the author's screenshot: the triplet
        # is already visible while the pending badge transitions from empty to 待.
        overlay._set_quality_state_badge("红件", "")
        root.update_idletasks()
        assert value.winfo_ismapped()
        assert not badge.winfo_ismapped()
        overlay._set_quality_state_badge("红件", "待")
        root.update_idletasks()
        assert value.winfo_width() >= value.winfo_reqwidth()
        assert value.winfo_x() + value.winfo_width() <= badge.winfo_x()
        for widget in (
            overlay.version_label,
            value,
            badge,
            *(overlay.red_rows[label] for label in ("红格", "紫品", "金品", "蓝品", "白绿")),
        ):
            left = widget.winfo_rootx() - root.winfo_rootx()
            top = widget.winfo_rooty() - root.winfo_rooty()
            assert left >= 0 and top >= 0
            assert left + widget.winfo_width() <= root.winfo_width()
            assert top + widget.winfo_height() <= root.winfo_height()
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except Exception:
            pass


def test_real_tk_red_triplet_respects_saved_mini_ui_scale(tmp_path: Path) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", 96.0 / 72.0)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "saved-scale" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry("693x574+0+0")
        overlay.render(_summary_for_linked_pending(wide_red_reference=False))
        overlay._ui_scale = 1.6
        overlay._apply_ui_scale()
        root.update_idletasks()
        root.update()

        expected_red_size = round(module.RED_COUNT_INLINE_FONT_SIZE * 1.6)
        triplet_fonts = {
            int(tkfont.Font(root=root, font=label.cget("font")).cget("size"))
            for label in overlay.red_count_triplet_labels.values()
        }
        red_cells_font = int(
            tkfont.Font(root=root, font=overlay.red_rows["红格"].cget("font")).cget(
                "size"
            )
        )
        assert triplet_fonts == {expected_red_size}
        assert getattr(overlay, "_red_count_triplet_fit_font", None) == expected_red_size
        assert expected_red_size == red_cells_font
        assert (
            overlay.red_count_triplet_frame.winfo_x()
            + overlay.red_count_triplet_frame.winfo_width()
            <= overlay.quality_state_badges["红件"].winfo_x()
        )
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except Exception:
            pass


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_real_tk_keeps_long_red_count_and_cells_visible_at_dpi(
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
            snapshot_path=tmp_path / f"long-{dpi_scale}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        overlay.render(_summary_for_long_red_reference())
        root.update_idletasks()
        root.update()

        assert tuple(
            overlay.red_count_triplet_labels[key].cget("text")
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        ) == ("保28", "/", "均56", "/", "激99")
        assert overlay.red_rows["红格"].cget("text") == "102/196/288格"
        assert overlay.quality_state_badges["红件"].cget("text") == "待"
        assert overlay.quality_state_badges["红格"].cget("text") == "待"
        row = overlay.red_count_triplet_frame.master
        caption = next(widget for widget in row.winfo_children() if widget.cget("text") == "红件")
        badge = overlay.quality_state_badges["红件"]
        assert caption.cget("text") == "红件"
        assert badge.cget("text") == "待"
        assert badge.winfo_width() >= badge.winfo_reqwidth()
        assert caption.winfo_x() + caption.winfo_width() <= overlay.red_count_triplet_frame.winfo_x()
        assert overlay.red_count_triplet_frame.winfo_x() + overlay.red_count_triplet_frame.winfo_width() <= badge.winfo_x()
        for widget in (
            overlay.red_count_triplet_frame,
            overlay.red_rows["红格"],
            overlay.quality_state_badges["红件"],
            overlay.quality_state_badges["红格"],
            overlay.version_label,
        ):
            assert widget.winfo_width() >= widget.winfo_reqwidth()
            left = widget.winfo_rootx() - root.winfo_rootx()
            top = widget.winfo_rooty() - root.winfo_rooty()
            assert left >= 0 and top >= 0
            assert left + widget.winfo_width() <= root.winfo_width()
            assert top + widget.winfo_height() <= root.winfo_height()
        for label in ("紫品", "金品", "蓝品", "白绿"):
            value = overlay.red_rows[label]
            assert "..." not in value.cget("text")
            assert value.winfo_width() >= value.winfo_reqwidth()
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except Exception:
            pass


def test_real_tk_red_triplet_refit_converges_without_configure_feedback(
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * 1.5)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "converge" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        started = time.perf_counter()
        overlay.render(copy.deepcopy(_summary_for_linked_pending(wide_red_reference=True)))
        for _ in range(6):
            root.update_idletasks()
            root.update()
        elapsed = time.perf_counter() - started
        frame = overlay.red_count_triplet_frame
        fonts = tuple(
            overlay.red_count_triplet_labels[key].cget("font")
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        )
        configure_count = int(
            getattr(overlay, "_red_count_triplet_configure_count", 0)
        )
        for _ in range(6):
            root.update_idletasks()
            root.update()
        assert time.perf_counter() - started < 5.0
        assert frame.winfo_width() >= frame.winfo_reqwidth()
        assert fonts == tuple(
            overlay.red_count_triplet_labels[key].cget("font")
            for key in ("low", "sep_left", "middle", "sep_right", "high")
        )
        assert configure_count <= 12
        assert configure_count == int(
            getattr(overlay, "_red_count_triplet_configure_count", 0)
        )
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except Exception:
            pass


def test_real_tk_pending_175_final_parcel_stays_readable_without_clipping(
    tmp_path: Path,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * 1.75)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "pending-175" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        overlay.render(copy.deepcopy(_summary_for_linked_pending()))
        for _ in range(6):
            root.update_idletasks()
            root.update()
        frame = overlay.red_count_triplet_frame
        configure_count = int(
            getattr(overlay, "_red_count_triplet_configure_count", 0)
        )
        row = frame.master
        caption = next(widget for widget in row.winfo_children() if widget.cget("text") == "红件")
        badge = overlay.quality_state_badges["红件"]
        assert 0 < frame.winfo_width()
        assert caption.winfo_x() + caption.winfo_width() <= frame.winfo_x()
        assert frame.winfo_x() + frame.winfo_width() + 3 <= badge.winfo_x()
        assert frame.winfo_width() >= frame.winfo_reqwidth()
        font_sizes = {
            int(tkfont.Font(root=root, font=segment.cget("font")).cget("size"))
            for segment in overlay.red_count_triplet_labels.values()
        }
        assert len(font_sizes) == 1
        assert next(iter(font_sizes)) >= module.SUMMARY_COMPACT_MIN_FONT_SIZE
        assert configure_count <= 12
        for _ in range(6):
            root.update_idletasks()
            root.update()
        assert frame.winfo_width() >= frame.winfo_reqwidth()
        assert {
            int(tkfont.Font(root=root, font=segment.cget("font")).cget("size"))
            for segment in overlay.red_count_triplet_labels.values()
        } == font_sizes
        assert configure_count == int(
            getattr(overlay, "_red_count_triplet_configure_count", 0)
        )
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except Exception:
            pass
