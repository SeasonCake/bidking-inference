from __future__ import annotations

import math

import pytest

from test_live_overlay import _ahmad_overlay_module


def _quality(
    *,
    status: str,
    lock_strength: str,
    candidates: list[dict[str, float | int]],
    lock_reason: str,
    shown_probability: float | None = None,
    other_probability: float | None = None,
    truncated: bool | None = None,
    cap_hit: bool = False,
) -> dict:
    shown = sum(float(row["probability"]) for row in candidates)
    return {
        "status": status,
        "source": "pricing_posterior",
        "lock_strength": lock_strength,
        "lock_reason": lock_reason,
        "candidates": candidates,
        "shown_probability": shown if shown_probability is None else shown_probability,
        "other_probability": (
            max(0.0, 1.0 - shown)
            if other_probability is None
            else other_probability
        ),
        "truncated": shown < 1.0 if truncated is None else truncated,
        "cap_hit": cap_hit,
        "pricing_posterior": True,
        "display_reconciled": False,
    }


def _quality_view(module, raw_quality: dict):
    return module.detail_composition_view(
        {
            "quality_composition": {
                "status": "ready",
                "q6": raw_quality,
            }
        }
    ).quality("q6")


def test_2510_collapsed_percentile_uses_joint_probability_not_visual_lock() -> None:
    module = _ahmad_overlay_module()
    overlay = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)
    quality = _quality_view(
        module,
        _quality(
            status="ready",
            lock_strength="probabilistic",
            lock_reason="combo_multi_solution",
            candidates=[
                {"count": 6, "cells": 25, "probability": 0.9733},
                {"count": 0, "cells": 0, "probability": 0.0267},
            ],
        ),
    )
    red = {
        "count_range": "6 / 6 / 6",
        "count_probability_hover": "红件候选：6件 97.3% · 0件 2.7%",
    }

    hover = overlay._red_hover("红件", red, {}, quality=quality)

    assert "最可能 6件" in hover
    assert "6件 97.3%" in hover
    assert "0件 2.7%" in hover
    assert "锁定" not in hover
    assert "唯一" not in hover


@pytest.mark.parametrize(
    "lock_reason",
    ("hard_total_cells_120", "direct_q5_cells_19"),
)
def test_exact_joint_singleton_is_the_only_compact_unique_lock(
    lock_reason: str,
) -> None:
    module = _ahmad_overlay_module()
    overlay = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)
    quality = _quality_view(
        module,
        _quality(
            status="exact",
            lock_strength="exact",
            lock_reason=lock_reason,
            candidates=[
                {"count": 6, "cells": 25, "probability": 1.0},
            ],
        ),
    )
    red = {
        "count_range": "6 / 6 / 6",
        "cells_range": "25 / 25 / 25",
        "count_probability_hover": "红件候选：6件 100%",
        "cells_probability_hover": "红格候选：25格 100%",
    }

    count_hover = overlay._red_hover("红件", red, {}, quality=quality)
    cells_hover = overlay._red_hover("红格", red, {}, quality=quality)

    assert "唯一锁定（联合候选 100%）" in count_hover
    assert "唯一锁定（联合候选 100%）" in cells_hover


def test_count_only_collapse_does_not_promote_multi_cell_joint_to_unique() -> None:
    module = _ahmad_overlay_module()
    overlay = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)
    quality = _quality_view(
        module,
        _quality(
            status="ready",
            lock_strength="probabilistic",
            lock_reason="count_exact_joint_multi_solution",
            candidates=[
                {"count": 5, "cells": 15, "probability": 0.34},
                {"count": 5, "cells": 16, "probability": 0.33},
                {"count": 5, "cells": 17, "probability": 0.33},
            ],
        ),
    )
    red = {
        "count_range": "5 / 5 / 5",
        "cells_range": "15 / 16 / 17",
        "count_probability_hover": "红件候选：5件 100%",
        "cells_probability_hover": (
            "红格候选：15格 34% · 16格 33% · 17格 33%"
        ),
    }

    count_hover = overlay._red_hover("红件", red, {}, quality=quality)
    cells_hover = overlay._red_hover("红格", red, {}, quality=quality)

    assert "最可能 5件" in count_hover
    assert "格数仍有多个联合候选" in count_hover
    assert "实际范围 15–17格" in cells_hover
    assert "锁定" not in count_hover + cells_hover
    assert "唯一" not in count_hover + cells_hover


@pytest.mark.parametrize(
    (
        "probability",
        "shown_probability",
        "other_probability",
        "truncated",
        "cap_hit",
    ),
    [
        (
            math.nextafter(1.0, 0.0),
            math.nextafter(1.0, 0.0),
            1.0 - math.nextafter(1.0, 0.0),
            False,
            False,
        ),
        (0.9999995, 0.9999995, 0.0000005, False, False),
        (1.0, 1.0, 0.0, True, False),
        (1.0, 1.0, 0.0, False, True),
    ],
)
def test_claimed_exact_tail_or_incomplete_metadata_fails_closed(
    probability: float,
    shown_probability: float,
    other_probability: float,
    truncated: bool,
    cap_hit: bool,
) -> None:
    module = _ahmad_overlay_module()
    overlay = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)
    quality = _quality_view(
        module,
        _quality(
            status="exact",
            lock_strength="exact",
            lock_reason="claimed_exact",
            candidates=[
                {"count": 6, "cells": 25, "probability": probability},
            ],
            shown_probability=shown_probability,
            other_probability=other_probability,
            truncated=truncated,
            cap_hit=cap_hit,
        ),
    )
    red = {
        "count_range": "6 / 6 / 6",
        "count_probability_hover": "红件候选：6件 100%",
    }

    hover = overlay._red_hover("红件", red, {}, quality=quality)

    assert quality.status == "conflict"
    assert overlay._detail_quality_state_text(quality) == "冲突"
    assert "锁定" not in hover
    assert "唯一" not in hover
    assert "100%" not in hover


@pytest.mark.parametrize("missing_field", ("truncated", "cap_hit"))
def test_claimed_exact_requires_explicit_false_completion_flags(
    missing_field: str,
) -> None:
    module = _ahmad_overlay_module()
    overlay = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)
    raw = _quality(
        status="exact",
        lock_strength="exact",
        lock_reason="claimed_exact_missing_completion_flag",
        candidates=[
            {"count": 6, "cells": 25, "probability": 1.0},
        ],
        shown_probability=1.0,
        other_probability=0.0,
        truncated=False,
        cap_hit=False,
    )
    raw.pop(missing_field)
    quality = _quality_view(module, raw)

    hover = overlay._red_hover(
        "红件",
        {
            "count_range": "6 / 6 / 6",
            "count_probability_hover": "红件候选：6件 100%",
        },
        {},
        quality=quality,
    )

    assert quality.status == "conflict"
    assert overlay._detail_quality_state_text(quality) == "冲突"
    assert "锁定" not in hover
    assert "唯一" not in hover
    assert "100%" not in hover


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        ("truncated", None),
        ("truncated", 0),
        ("truncated", "false"),
        ("cap_hit", None),
        ("cap_hit", 0),
        ("cap_hit", "false"),
    ),
)
def test_claimed_exact_rejects_non_boolean_completion_flags(
    field: str,
    invalid_value: object,
) -> None:
    module = _ahmad_overlay_module()
    overlay = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)
    raw = _quality(
        status="exact",
        lock_strength="exact",
        lock_reason="claimed_exact_invalid_completion_flag",
        candidates=[
            {"count": 6, "cells": 25, "probability": 1.0},
        ],
        shown_probability=1.0,
        other_probability=0.0,
        truncated=False,
        cap_hit=False,
    )
    raw[field] = invalid_value
    quality = _quality_view(module, raw)

    hover = overlay._red_hover(
        "红件",
        {
            "count_range": "6 / 6 / 6",
            "count_probability_hover": "红件候选：6件 100%",
        },
        {},
        quality=quality,
    )

    assert quality.status == "conflict"
    assert overlay._detail_quality_state_text(quality) == "冲突"
    assert "锁定" not in hover
    assert "唯一" not in hover
    assert "100%" not in hover


def test_probabilistic_near_one_mass_is_never_rounded_to_compact_100() -> None:
    module = _ahmad_overlay_module()
    overlay = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)
    probability = math.nextafter(1.0, 0.0)
    quality = _quality_view(
        module,
        _quality(
            status="ready",
            lock_strength="probabilistic",
            lock_reason="epsilon_tail",
            candidates=[
                {"count": 6, "cells": 25, "probability": probability},
            ],
            shown_probability=probability,
            other_probability=1.0 - probability,
            truncated=False,
        ),
    )

    hover = overlay._red_hover(
        "红件",
        {
            "count_range": "6 / 6 / 6",
            "count_probability_hover": "红件候选：6件 100%",
        },
        {},
        quality=quality,
    )

    assert quality.status == "ready"
    assert "6件 <100%" in hover
    assert "其他 <0.1%" in hover
    assert "锁定" not in hover
    assert "唯一" not in hover


def test_probabilistic_singleton_missing_completion_flags_never_shows_100() -> None:
    module = _ahmad_overlay_module()
    overlay = module.AhmadTkOverlay.__new__(module.AhmadTkOverlay)
    raw = _quality(
        status="ready",
        lock_strength="probabilistic",
        lock_reason="missing_completion_flags",
        candidates=[{"count": 6, "cells": 25, "probability": 1.0}],
        shown_probability=1.0,
        other_probability=0.0,
        truncated=False,
        cap_hit=False,
    )
    raw.pop("truncated")
    raw.pop("cap_hit")
    quality = _quality_view(module, raw)

    hover = overlay._red_hover(
        "红件",
        {
            "count_range": "6 / 6 / 6",
            "count_probability_hover": "红件候选：6件 100%",
        },
        {},
        quality=quality,
    )

    assert quality.status == "ready"
    assert overlay._detail_quality_state_text(quality) == "候选"
    assert "6件 <100%" in hover
    assert "锁定" not in hover
    assert "唯一" not in hover
    assert "6件 100%" not in hover
