from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ahmad_live_panel_server import _quality_composition_payload  # noqa: E402
from detail_composition_view import (  # noqa: E402
    detail_composition_view,
    format_quality_dimension_state_badge,
)
from test_live_overlay import _ahmad_overlay_module  # noqa: E402
from test_overlay_card_remap_real_tk import _new_root  # noqa: E402
from test_quality_composition_producer_0_3_1 import _native_2401_summary  # noqa: E402


def _quality_rows(
    *,
    count_values: tuple[int, ...],
    cells_values: tuple[int, ...],
    probability_total: float = 0.9,
) -> list[dict[str, float | int]]:
    pairs = tuple(dict.fromkeys(zip(count_values, cells_values, strict=False)))
    probability = probability_total / len(pairs)
    return [
        {"count": count, "cells": cells, "prob": probability}
        for count, cells in pairs
    ]


def _ref_result(
    *,
    key: str,
    count_range: list[object],
    cells_range: list[object],
    fixed_count: object = None,
    fixed_cells: object = None,
    count_values: tuple[int, ...] = (3, 3),
    cells_values: tuple[int, ...] = (7, 9),
    status: object = "ok",
    total_count: object = 40,
    total_grid_target: object = 120,
    lock_strength: object = None,
    lock_reason: object = None,
    candidate_probability_total: float = 0.9,
) -> dict[str, object]:
    evidence: dict[str, object] = {
        "fixed_counts": {},
        "quality_cells": {},
        "total_count": total_count,
        "total_grid_target": total_grid_target,
    }
    if fixed_count is not None:
        evidence["fixed_counts"] = {key: fixed_count}
    if fixed_cells is not None:
        evidence["quality_cells"] = {key: fixed_cells}
    return {
        "status": status,
        "quality_count_ranges": {key: count_range},
        "quality_cells_ranges": {key: cells_range},
        "quality_count_cells_candidates": {
            key: _quality_rows(
                count_values=count_values,
                cells_values=cells_values,
                probability_total=candidate_probability_total,
            )
        },
        "quality_lock": {
            key: {
                "strength": (
                    lock_strength
                    if lock_strength is not None
                    else "exact" if fixed_count is not None else "probabilistic"
                ),
                "reason": (
                    lock_reason
                    if lock_reason is not None
                    else "exact_lock" if fixed_count is not None else "combo_multi_solution"
                ),
            }
        },
        "evidence": evidence,
    }


@pytest.mark.parametrize("key", ("q1", "q3", "q4", "q5", "q6"))
@pytest.mark.parametrize(
    ("fixed_count", "fixed_cells", "count_range", "cells_range", "count_values", "cells_values", "expected"),
    (
        (3, None, [3, 3, 3], [7, 9, 11], (3, 3, 3), (7, 9, 11), "件锁"),
        (None, 12.0, [4, 6, 8], [12, 12, 12], (4, 6, 8), (12, 12, 12), "格锁"),
        (2, 5.0, [2, 2, 2], [5, 5, 5], (2,), (5,), "锁定"),
        (None, None, [2, 3, 4], [5, 7, 9], (2, 3, 4), (5, 7, 9), "未锁"),
        (0, 0.0, [0, 0, 0], [0, 0, 0], (0,), (0,), "锁定"),
    ),
)
def test_panel_projects_authoritative_dimension_state_for_every_quality(
    key: str,
    fixed_count: object,
    fixed_cells: object,
    count_range: list[object],
    cells_range: list[object],
    count_values: tuple[int, ...],
    cells_values: tuple[int, ...],
    expected: str,
) -> None:
    """同一份 Panel/API payload 必须独立表达件与格，Tk 不从显示文字猜。"""
    payload = _quality_composition_payload(
        _ref_result(
            key=key,
            fixed_count=fixed_count,
            fixed_cells=fixed_cells,
            count_range=count_range,
            cells_range=cells_range,
            count_values=count_values,
            cells_values=cells_values,
            total_count=None,
            total_grid_target=None,
        )
    )
    quality = detail_composition_view(
        {"quality_composition": payload}
    ).quality(key)

    assert payload["qualities"][key]["dimension_state"] == expected
    assert format_quality_dimension_state_badge(quality) == expected


def test_aisha_4501_public_gold_count_is_count_locked_despite_joint_tail() -> None:
    """作者实测正控：200019=3，金3件唯一、7/7/9格未唯一，必须显示件锁。"""
    payload = _quality_composition_payload(
        _ref_result(
            key="q5",
            fixed_count=3,
            count_range=[3, 3, 3],
            cells_range=[7, 7, 9],
            count_values=(3, 3),
            cells_values=(7, 9),
        )
    )
    quality = detail_composition_view(
        {"quality_composition": payload}
    ).quality("q5")

    assert payload["qualities"]["q5"]["count_lock_value"] == 3
    assert payload["qualities"]["q5"]["cells_lock_value"] is None
    assert format_quality_dimension_state_badge(quality) == "件锁"


def test_ahmed_2403_round2_null_total_grid_keeps_gold_count_and_cells_locked() -> None:
    """作者实测正控：金5件·34格唯一；未知总格不是坏总格。"""
    payload = _quality_composition_payload(
        _ref_result(
            key="q5",
            fixed_count=None,
            fixed_cells=None,
            count_range=[5, 5, 5],
            cells_range=[34, 34, 34],
            count_values=(5,),
            cells_values=(34,),
            total_count=33,
            total_grid_target=None,
            lock_strength="exact",
            lock_reason="derived_single_solution",
            candidate_probability_total=1.0,
        )
    )
    quality = detail_composition_view(
        {"quality_composition": payload}
    ).quality("q5")

    assert payload["qualities"]["q5"]["count_lock_value"] == 5
    assert payload["qualities"]["q5"]["cells_lock_value"] == 34
    assert format_quality_dimension_state_badge(quality) == "锁定"


@pytest.mark.parametrize(
    ("total_count", "total_grid_target", "expected"),
    (
        ("33", None, "格锁"),
        (33, "34", "件锁"),
        ([], {}, "未锁"),
    ),
)
def test_non_null_malformed_optional_totals_still_fail_closed_per_dimension(
    total_count: object,
    total_grid_target: object,
    expected: str,
) -> None:
    payload = _quality_composition_payload(
        _ref_result(
            key="q5",
            count_range=[5, 5, 5],
            cells_range=[34, 34, 34],
            count_values=(5,),
            cells_values=(34,),
            total_count=total_count,
            total_grid_target=total_grid_target,
            lock_strength="exact",
            lock_reason="derived_single_solution",
            candidate_probability_total=1.0,
        )
    )
    quality = detail_composition_view(
        {"quality_composition": payload}
    ).quality("q5")

    assert format_quality_dimension_state_badge(quality) == expected


def test_hostile_non_null_optional_totals_fail_closed_without_protocol_calls() -> None:
    calls: list[str] = []

    class HostileTotal:
        def __int__(self) -> int:
            calls.append("int")
            raise AssertionError("foreign int protocol must not execute")

        def __float__(self) -> float:
            calls.append("float")
            raise AssertionError("foreign float protocol must not execute")

        def __eq__(self, _other: object) -> bool:
            calls.append("eq")
            raise AssertionError("foreign equality protocol must not execute")

    payload = _quality_composition_payload(
        _ref_result(
            key="q5",
            count_range=[5, 5, 5],
            cells_range=[34, 34, 34],
            count_values=(5,),
            cells_values=(34,),
            total_count=HostileTotal(),
            total_grid_target=HostileTotal(),
            lock_strength="exact",
            lock_reason="derived_single_solution",
            candidate_probability_total=1.0,
        )
    )

    assert calls == []
    assert payload["qualities"]["q5"]["dimension_state"] == "未锁"


def test_ahmed_4510_blue_count_eight_is_count_locked_with_cells_range() -> None:
    """作者截图正控：蓝8件唯一、17–19格未唯一，必须显示件锁。"""
    payload = _quality_composition_payload(
        _ref_result(
            key="q3",
            fixed_count=8,
            count_range=[8, 8, 8],
            cells_range=[17, 18, 19],
            count_values=(8, 8, 8),
            cells_values=(17, 18, 19),
        )
    )
    quality = detail_composition_view(
        {"quality_composition": payload}
    ).quality("q3")

    assert payload["qualities"]["q3"]["count_lock_value"] == 8
    assert payload["qualities"]["q3"]["cells_lock_value"] is None
    assert format_quality_dimension_state_badge(quality) == "件锁"


@pytest.mark.parametrize(
    "result",
    (
        _ref_result(
            key="q5",
            fixed_count=True,
            count_range=[1, 1, 1],
            cells_range=[7, 9, 11],
            count_values=(1, 1, 1),
            cells_values=(7, 9, 11),
        ),
        _ref_result(
            key="q5",
            fixed_count=3.5,
            count_range=[3, 3, 3],
            cells_range=[7, 9, 11],
        ),
        _ref_result(
            key="q5",
            fixed_count=3,
            count_range=[3, 3, 4],
            cells_range=[7, 9, 11],
        ),
        _ref_result(
            key="q5",
            fixed_count=41,
            count_range=[41, 41, 41],
            cells_range=[41, 50, 60],
            count_values=(41, 41),
            cells_values=(41, 60),
            total_count=40,
        ),
        _ref_result(
            key="q5",
            fixed_cells=121,
            count_range=[2, 3, 4],
            cells_range=[121, 121, 121],
            count_values=(2, 3, 4),
            cells_values=(121, 121, 121),
            total_grid_target=120,
        ),
        _ref_result(
            key="q5",
            fixed_count=3,
            count_range=[3, 3, 3],
            cells_range=[7, 9, 11],
            status="conflict",
        ),
    ),
)
def test_invalid_missing_conflicting_or_out_of_bounds_authority_never_locks(
    result: dict[str, object],
) -> None:
    payload = _quality_composition_payload(result)
    quality = detail_composition_view(
        {"quality_composition": payload}
    ).quality("q5")
    assert format_quality_dimension_state_badge(quality) not in {
        "件锁",
        "格锁",
        "锁定",
    }


def test_client_rejects_lock_value_that_disagrees_with_server_candidates() -> None:
    payload = _quality_composition_payload(
        _ref_result(
            key="q5",
            fixed_count=3,
            count_range=[3, 3, 3],
            cells_range=[7, 7, 9],
            count_values=(3, 3),
            cells_values=(7, 9),
        )
    )
    payload["qualities"]["q5"]["count_lock_value"] = 4

    quality = detail_composition_view(
        {"quality_composition": payload}
    ).quality("q5")

    assert quality is not None
    assert quality.status == "conflict"
    assert format_quality_dimension_state_badge(quality) == "冲突"


def test_nested_hostile_evidence_key_fails_closed_without_protocol_calls() -> None:
    calls: list[str] = []

    class HostileKey:
        def __hash__(self) -> int:
            return hash("q5")

        def __eq__(self, _other: object) -> bool:
            calls.append("eq")
            raise AssertionError("foreign key equality must not execute")

    result = _ref_result(
        key="q5",
        fixed_count=3,
        count_range=[3, 3, 3],
        cells_range=[7, 7, 9],
    )
    result["evidence"]["fixed_counts"] = {HostileKey(): 3}

    payload = _quality_composition_payload(result)

    assert calls == []
    assert payload["qualities"]["q5"]["dimension_state"] == "未锁"


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_real_tk_uses_count_lock_and_typographically_aligned_record_action(
    tmp_path: Path,
    dpi_scale: float,
) -> None:
    tkfont = pytest.importorskip("tkinter.font")
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"locks-records-{dpi_scale}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        summary = _native_2401_summary(
            tmp_path / f"summary-{dpi_scale}",
            phase="bidding",
        )
        summary["quality_composition"] = _quality_composition_payload(
            _ref_result(
                key="q5",
                fixed_count=3,
                count_range=[3, 3, 3],
                cells_range=[7, 7, 9],
                count_values=(3, 3),
                cells_values=(7, 9),
            )
        )
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        overlay.render(summary)
        root.update_idletasks()
        root.update()

        assert overlay.quality_state_badges["金品"].cget("text") == "件锁"

        activated: list[str] = []

        def _activate(_event=None) -> str:
            activated.append("open")
            return "break"

        overlay._open_match_records = _activate
        action = overlay.action_rows["动作"]
        normal_font = tkfont.Font(font=action.cget("font"))
        normal_height = action.winfo_height()
        normal_padx = action.cget("padx")
        normal_pady = action.cget("pady")
        overlay._apply_action_cell("结算完成", next_step="复核结算")
        root.update_idletasks()
        root.update()

        record_font = tkfont.Font(font=action.cget("font"))
        assert abs(record_font.actual("size")) == abs(normal_font.actual("size"))
        assert str(record_font.actual("weight")) == str(normal_font.actual("weight"))
        assert action.winfo_height() == normal_height
        assert int(action.cget("takefocus")) == 1
        assert int(action.cget("highlightthickness")) == 0
        assert action.cget("padx") == normal_padx
        assert action.cget("pady") == normal_pady
        assert str(action.cget("relief")) == "flat"
        assert action.bind("<Button-1>")
        assert action.bind("<Return>")
        assert action.bind("<space>")
        assert "复核结算" in action._hover_tip.text
        assert record_font.measure(str(action.cget("text"))) <= action.winfo_width()
        assert action.winfo_x() + action.winfo_width() <= action.master.winfo_width()

        action.focus_force()
        root.update()
        assert root.focus_get() is action
        action.event_generate("<Return>")
        root.update()
        action.event_generate("<space>")
        root.update()
        action.event_generate("<Button-1>", x=2, y=2)
        root.update()
        assert activated == ["open", "open", "open"]

        overlay._apply_action_cell("可出价", next_step="补紫/金件数或均格")
        root.update_idletasks()
        assert not action.bind("<Return>")
        assert not action.bind("<space>")
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except Exception:
            pass
