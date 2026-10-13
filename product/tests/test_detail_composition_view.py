from __future__ import annotations

import math
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "hero_ref" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from detail_composition_view import (  # noqa: E402
    QUALITY_COMPOSITION_SCHEMA_VERSION,
    QUALITY_ORDER,
    detail_composition_view as _detail_composition_view,
    format_probability,
    format_quality_probability,
    quality_is_strict_exact,
)


def _current_contract_fixture(summary: object) -> object:
    """Lift per-quality unit fixtures into the current producer envelope."""
    if type(summary) is not dict:  # noqa: E721 - preserve malformed fixtures
        return summary
    raw_block = dict.get(summary, "quality_composition")
    if type(raw_block) is not dict or "schema_version" in raw_block:  # noqa: E721
        return summary
    block = dict(raw_block)
    if "qualities" in block:
        raw_qualities = dict.get(block, "qualities")
        if type(raw_qualities) is not dict:  # noqa: E721
            block["schema_version"] = QUALITY_COMPOSITION_SCHEMA_VERSION
        else:
            qualities = dict(raw_qualities)
            block["qualities"] = qualities
            block["schema_version"] = QUALITY_COMPOSITION_SCHEMA_VERSION
    elif any(key in block for key in QUALITY_ORDER):
        qualities = {
            key: dict.pop(block, key)
            for key in QUALITY_ORDER
            if key in block
        }
        block["qualities"] = qualities
        block["schema_version"] = QUALITY_COMPOSITION_SCHEMA_VERSION
    else:
        return summary

    raw_qualities = dict.get(block, "qualities")
    if type(raw_qualities) is dict:  # noqa: E721
        for key, raw_quality in tuple(raw_qualities.items()):
            if type(raw_quality) is not dict:  # noqa: E721
                continue
            quality = dict(raw_quality)
            if any(type(raw_key) is not str for raw_key in quality):  # noqa: E721
                raw_qualities[key] = quality
                continue
            status = dict.get(quality, "status")
            if type(status) is str and status in {"ready", "exact"}:  # noqa: E721
                dict.setdefault(quality, "compact_count_candidates", [])
                dict.setdefault(quality, "compact_count_triplet", [])
                dict.setdefault(quality, "count_correlation_state", "independent")
                dict.setdefault(
                    quality,
                    "count_correlation_note",
                    "unit fixture has no total-count authority",
                )
            raw_qualities[key] = quality
    lifted = dict(summary)
    lifted["quality_composition"] = block
    return lifted


def detail_composition_view(summary: object):
    return _detail_composition_view(_current_contract_fixture(summary))


class _BombStr(str):
    def __new__(cls, value: str):
        instance = super().__new__(cls, value)
        instance.magic_calls = []
        return instance

    def _explode(self, name: str):
        self.magic_calls.append(name)
        raise RuntimeError(f"PRIVATE_{name.upper()}")

    def __bool__(self):
        return self._explode("bool")

    def __str__(self):
        return self._explode("str")

    def __eq__(self, other):
        del other
        return self._explode("eq")

    def __lt__(self, other):
        del other
        return self._explode("lt")


class _BombInt(int):
    def __new__(cls, value: int):
        instance = super().__new__(cls, value)
        instance.magic_calls = []
        return instance

    def _explode(self, name: str):
        self.magic_calls.append(name)
        raise RuntimeError(f"PRIVATE_{name.upper()}")

    def __bool__(self):
        return self._explode("bool")

    def __int__(self):
        return self._explode("int")

    def __float__(self):
        return self._explode("float")

    def __eq__(self, other):
        del other
        return self._explode("eq")

    def __lt__(self, other):
        del other
        return self._explode("lt")


class _BombFloat(float):
    def __new__(cls, value: float):
        instance = super().__new__(cls, value)
        instance.magic_calls = []
        return instance

    def _explode(self, name: str):
        self.magic_calls.append(name)
        raise RuntimeError(f"PRIVATE_{name.upper()}")

    def __bool__(self):
        return self._explode("bool")

    def __str__(self):
        return self._explode("str")

    def __float__(self):
        return self._explode("float")

    def __eq__(self, other):
        del other
        return self._explode("eq")

    def __lt__(self, other):
        del other
        return self._explode("lt")


class _BombProtocol:
    def __init__(self) -> None:
        self.magic_calls: list[str] = []

    def _explode(self, name: str):
        self.magic_calls.append(name)
        raise RuntimeError(f"PRIVATE_{name.upper()}")

    def __bool__(self):
        return self._explode("bool")

    def __str__(self):
        return self._explode("str")

    def __float__(self):
        return self._explode("float")

    def __eq__(self, other):
        del other
        return self._explode("eq")

    def __lt__(self, other):
        del other
        return self._explode("lt")


class _BombMapping(Mapping):
    def __init__(self) -> None:
        self.magic_calls: list[str] = []

    def _explode(self, name: str):
        self.magic_calls.append(name)
        raise RuntimeError(f"PRIVATE_{name.upper()}")

    def __getitem__(self, key):
        del key
        return self._explode("getitem")

    def __iter__(self):
        return self._explode("iter")

    def __len__(self):
        return self._explode("len")


class _CollisionKey(str):
    def __new__(cls, value: str):
        instance = super().__new__(cls, value)
        instance.magic_calls = []
        return instance

    def __hash__(self):
        return hash("status")

    def __eq__(self, other):
        del other
        self.magic_calls.append("eq")
        raise RuntimeError("PRIVATE_EQ")


def _quality(
    *,
    status: str = "ready",
    lock_strength: str = "probabilistic",
    candidates: list[dict[str, object]] | None = None,
    shown_probability: float = 0.8,
    other_probability: float = 0.2,
    truncated: bool = True,
    cap_hit: bool = False,
    discrete_count_display: object | None = None,
) -> dict[str, object]:
    quality = {
        "status": status,
        "source": "pricing_posterior",
        "lock_strength": lock_strength,
        "lock_reason": "joint posterior",
        "candidates": candidates
        or [
            {"count": 2, "cells": 12, "probability": 0.6},
            {"count": 1, "cells": 6, "probability": 0.2},
        ],
        "shown_probability": shown_probability,
        "other_probability": other_probability,
        "truncated": truncated,
        "cap_hit": cap_hit,
        "pricing_posterior": True,
        "display_reconciled": False,
    }
    if discrete_count_display is not None:
        quality["discrete_count_display"] = discrete_count_display
    return quality


@pytest.mark.parametrize(
    ("field", "value_factory"),
    (
        ("status", lambda: _BombStr("exact")),
        ("lock_strength", lambda: _BombStr("exact")),
        ("source", lambda: _BombStr("pricing_posterior")),
        ("lock_reason", lambda: _BombStr("joint posterior")),
        ("reason", lambda: _BombStr("ready")),
        ("shown_probability", lambda: _BombFloat(1.0)),
        ("other_probability", lambda: _BombFloat(0.0)),
        ("truncated", _BombProtocol),
        ("cap_hit", _BombProtocol),
        ("pricing_posterior", _BombProtocol),
        ("display_reconciled", _BombProtocol),
        ("discrete_count_display", _BombProtocol),
    ),
)
def test_quality_scalar_projection_never_invokes_user_protocols(
    field: str,
    value_factory,
) -> None:
    raw = _quality(
        status="exact",
        lock_strength="exact",
        candidates=[{"count": 6, "cells": 25, "probability": 1.0}],
        shown_probability=1.0,
        other_probability=0.0,
        truncated=False,
        cap_hit=False,
    )
    value = value_factory()
    raw[field] = value

    view = detail_composition_view({"quality_composition": {"q6": raw}})
    quality = view.quality("q6")

    assert quality.status == "conflict"
    assert not quality_is_strict_exact(quality)
    assert quality.candidates == ()
    assert quality.shown_probability is None
    assert quality.other_probability is None
    assert value.magic_calls == []


@pytest.mark.parametrize(
    ("field", "value_factory"),
    (
        ("count", lambda: _BombInt(6)),
        ("cells", lambda: _BombInt(25)),
        ("probability", lambda: _BombFloat(1.0)),
    ),
)
def test_candidate_scalar_projection_never_invokes_user_protocols(
    field: str,
    value_factory,
) -> None:
    raw = _quality(
        status="exact",
        lock_strength="exact",
        candidates=[{"count": 6, "cells": 25, "probability": 1.0}],
        shown_probability=1.0,
        other_probability=0.0,
        truncated=False,
        cap_hit=False,
    )
    candidate = raw["candidates"][0]
    assert isinstance(candidate, dict)
    value = value_factory()
    candidate[field] = value

    view = detail_composition_view({"quality_composition": {"q6": raw}})
    quality = view.quality("q6")

    assert quality.status == "conflict"
    assert not quality_is_strict_exact(quality)
    assert quality.candidates == ()
    assert value.magic_calls == []


@pytest.mark.parametrize("reverse", (False, True))
def test_quality_projection_is_order_independent_for_invalid_scalar(
    reverse: bool,
) -> None:
    raw = _quality(
        status="exact",
        lock_strength="exact",
        candidates=[{"count": 6, "cells": 25, "probability": 1.0}],
        shown_probability=1.0,
        other_probability=0.0,
        truncated=False,
        cap_hit=False,
    )
    value = _BombStr("exact")
    items = [("status", value), *(item for item in raw.items() if item[0] != "status")]
    if reverse:
        items.reverse()

    quality = detail_composition_view(
        {"quality_composition": {"q6": dict(items)}}
    ).quality("q6")

    assert quality.status == "conflict"
    assert quality.candidates == ()
    assert value.magic_calls == []


def test_mapping_and_collision_key_are_rejected_without_protocol_calls() -> None:
    mapping = _BombMapping()
    mapped_quality = detail_composition_view(
        {"quality_composition": {"q6": mapping}}
    ).quality("q6")

    collision = _CollisionKey("status")
    raw = _quality()
    raw.pop("status")
    raw[collision] = "ready"
    collision_quality = detail_composition_view(
        {"quality_composition": {"q6": raw}}
    ).quality("q6")

    assert mapped_quality.status == "conflict"
    assert collision_quality.status == "conflict"
    assert mapping.magic_calls == []
    assert collision.magic_calls == []


@pytest.mark.parametrize(
    "summary",
    (
        {"quality_composition_reason": _BombStr("waiting")},
        {
            "quality_composition": {},
            "quality_composition_reason": _BombStr("waiting"),
        },
    ),
)
def test_root_reason_projection_is_conflict_without_magic(summary) -> None:
    reason = summary["quality_composition_reason"]

    view = detail_composition_view(summary)

    assert view.status == "conflict"
    assert all(quality.status == "conflict" for quality in view.qualities)
    assert reason.magic_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("shown_probability", math.nan),
        ("shown_probability", math.inf),
        ("other_probability", -math.inf),
        ("other_probability", math.nan),
    ),
)
def test_nonfinite_summary_probability_is_visible_conflict(
    field: str,
    value: float,
) -> None:
    raw = _quality()
    raw[field] = value

    quality = detail_composition_view(
        {"quality_composition": {"q6": raw}}
    ).quality("q6")

    assert quality.status == "conflict"
    assert quality.candidates == ()
    assert quality.shown_probability is None
    assert quality.other_probability is None


def test_absent_contract_is_explicitly_unavailable_and_ignores_legacy_marginals() -> None:
    view = detail_composition_view(
        {
            "quality_count_candidates": {"q6": [(7, 0.9)]},
            "quality_cells_candidates": {"q6": [(48, 0.8)]},
            "notes": ["q6=7/48"],
        }
    )

    assert view.status == "unavailable"
    assert view.reason == "等待引擎提供结构化组合"
    assert tuple(quality.key for quality in view.qualities) == QUALITY_ORDER
    assert all(not quality.candidates for quality in view.qualities)


def test_joint_candidates_keep_original_probability_mass_and_zero_state() -> None:
    view = detail_composition_view(
        {
            "quality_composition": {
                "q6": _quality(
                    candidates=[
                        {"count": 0, "cells": 0, "probability": 0.55},
                        {"count": 1, "cells": 6, "probability": 0.25},
                    ]
                )
            }
        }
    )
    red = view.quality("q6")

    assert view.status == "ready"
    assert [(row.count, row.cells, row.probability) for row in red.candidates] == [
        (0, 0, 0.55),
        (1, 6, 0.25),
    ]
    assert red.shown_probability == pytest.approx(0.8)
    assert red.other_probability == pytest.approx(0.2)
    assert red.truncated is True
    assert red.pricing_posterior is True


def test_only_schema_bound_nested_quality_container_is_ready() -> None:
    quality = _quality()
    flat = _detail_composition_view(
        {"quality_composition": {"status": "ready", "q6": quality}}
    )
    nested = detail_composition_view(
        {
            "quality_composition": {
                "status": "ready",
                "qualities": {"q6": quality},
            }
        }
    )

    assert flat.status == "conflict"
    assert nested.status == "ready"
    assert nested.quality("q6").status == "ready"


def test_explicit_ready_with_non_mapping_qualities_fails_closed() -> None:
    view = detail_composition_view(
        {
            "quality_composition": {
                "status": "ready",
                "qualities": [],
            }
        }
    )

    assert view.status == "conflict"
    assert view.reason == "组合数据存在冲突"
    assert all(quality.status == "conflict" for quality in view.qualities)


@pytest.mark.parametrize("raw_block", [True, 7, 1.5, "quality_composition"])
def test_present_non_mapping_composition_block_fails_closed(
    raw_block: object,
) -> None:
    view = detail_composition_view({"quality_composition": raw_block})

    assert view.status == "conflict"
    assert view.reason == "组合数据存在冲突"
    assert all(quality.status == "conflict" for quality in view.qualities)


@pytest.mark.parametrize("raw_quality", [True, 7, 1.5, "q6"])
def test_explicit_ready_with_malformed_quality_value_fails_closed(
    raw_quality: object,
) -> None:
    view = detail_composition_view(
        {
            "quality_composition": {
                "status": "ready",
                "qualities": {"q6": raw_quality},
            }
        }
    )

    assert view.status == "conflict"
    assert view.reason == "组合数据存在冲突"
    assert view.quality("q6").status == "conflict"


@pytest.mark.parametrize(
    "root_case",
    (
        "invalid_status",
        "invalid_reason",
        "partial_invalid_qualities",
        "explicit_conflict",
    ),
)
def test_root_conflict_atomically_invalidates_strict_children(
    root_case: str,
) -> None:
    strict_q6 = _quality(
        status="exact",
        lock_strength="exact",
        candidates=[{"count": 6, "cells": 25, "probability": 1.0}],
        shown_probability=1.0,
        other_probability=0.0,
        truncated=False,
        cap_hit=False,
    )
    raw_block: dict[str, object] = {
        "status": "ready",
        "q6": strict_q6,
    }
    canary = None
    if root_case == "invalid_status":
        raw_block["status"] = []
    elif root_case == "invalid_reason":
        canary = _BombStr("ready")
        raw_block["reason"] = canary
    elif root_case == "partial_invalid_qualities":
        raw_block = {
            "status": "ready",
            "qualities": {
                "q6": strict_q6,
                "q5": [],
            },
        }
    else:
        raw_block["status"] = "conflict"

    view = detail_composition_view({"quality_composition": raw_block})

    assert view.status == "conflict"
    assert view.reason == "组合数据存在冲突"
    assert all(quality.status == "conflict" for quality in view.qualities)
    assert all(not quality_is_strict_exact(quality) for quality in view.qualities)
    assert all(quality.candidates == () for quality in view.qualities)
    assert all(quality.shown_probability is None for quality in view.qualities)
    assert all(quality.other_probability is None for quality in view.qualities)
    if canary is not None:
        assert canary.magic_calls == []


@pytest.mark.parametrize(
    "declared_status",
    ("unavailable", "loading", "error", "stale", "no_reachable"),
)
def test_declared_nonready_root_cannot_mask_present_invalid_sibling(
    declared_status: str,
) -> None:
    strict_q6 = _quality(
        status="exact",
        lock_strength="exact",
        candidates=[{"count": 6, "cells": 25, "probability": 1.0}],
        shown_probability=1.0,
        other_probability=0.0,
        truncated=False,
        cap_hit=False,
    )
    flat_alias_canary = _BombMapping()
    extra_quality_canary = _BombMapping()

    view = detail_composition_view(
        {
            "quality_composition": {
                "status": declared_status,
                "reason": f"declared {declared_status} must not mask conflict",
                "q6": flat_alias_canary,
                "qualities": {
                    "q6": strict_q6,
                    "q5": [],
                    "q2": extra_quality_canary,
                },
            }
        }
    )

    assert view.status == "conflict"
    assert view.reason == "组合数据存在冲突"
    assert all(quality.status == "conflict" for quality in view.qualities)
    assert all(not quality_is_strict_exact(quality) for quality in view.qualities)
    assert all(quality.candidates == () for quality in view.qualities)
    assert all(quality.shown_probability is None for quality in view.qualities)
    assert all(quality.other_probability is None for quality in view.qualities)
    assert flat_alias_canary.magic_calls == []
    assert extra_quality_canary.magic_calls == []


def test_valid_root_preserves_complete_strict_child() -> None:
    view = detail_composition_view(
        {
            "quality_composition": {
                "status": "ready",
                "qualities": {
                    "q6": _quality(
                        status="exact",
                        lock_strength="exact",
                        candidates=[
                            {"count": 6, "cells": 25, "probability": 1.0}
                        ],
                        shown_probability=1.0,
                        other_probability=0.0,
                        truncated=False,
                        cap_hit=False,
                    )
                },
            }
        }
    )

    assert view.status == "ready"
    assert quality_is_strict_exact(view.quality("q6"))
    assert format_quality_probability(
        view.quality("q6"),
        view.quality("q6").shown_probability,
    ) == "100%"


@pytest.mark.parametrize(
    ("child_status", "expected_root", "expected_reason"),
    [
        ("conflict", "conflict", "组合数据存在冲突"),
        ("error", "error", "组合暂不可用"),
        ("stale", "stale", "显示的是旧组合结果"),
        ("loading", "loading", "组合计算中"),
        ("no_reachable", "no_reachable", "没有可达组合"),
    ],
)
def test_explicit_ready_cannot_override_stronger_child_status(
    child_status: str,
    expected_root: str,
    expected_reason: str,
) -> None:
    view = detail_composition_view(
        {
            "quality_composition": {
                "status": "ready",
                "reason": "normal footer must not survive",
                "qualities": {
                    "q6": _quality(),
                    "q5": {
                        "status": child_status,
                        "lock_strength": "unavailable",
                        "candidates": [],
                    },
                },
            }
        }
    )

    assert view.status == expected_root
    assert view.reason == expected_reason


def test_explicit_ready_with_all_qualities_missing_becomes_unavailable() -> None:
    view = detail_composition_view(
        {
            "quality_composition": {
                "status": "ready",
                "qualities": {},
            }
        }
    )

    assert view.status == "unavailable"
    assert view.reason == "等待引擎提供结构化组合"
    assert all(quality.status == "unavailable" for quality in view.qualities)


def test_exact_requires_one_joint_candidate_at_full_mass() -> None:
    good = detail_composition_view(
        {
            "quality_composition": {
                "q5": _quality(
                    status="exact",
                    lock_strength="exact",
                    candidates=[{"count": 4, "cells": 24, "probability": 1.0}],
                    shown_probability=1.0,
                    other_probability=0.0,
                    truncated=False,
                )
            }
        }
    )
    bad = detail_composition_view(
        {
            "quality_composition": {
                "q5": _quality(
                    status="exact",
                    lock_strength="exact",
                    candidates=[{"count": 4, "cells": 24, "probability": 0.99}],
                    shown_probability=0.99,
                    other_probability=0.01,
                    truncated=False,
                )
            }
        }
    )

    assert good.quality("q5").status == "exact"
    assert quality_is_strict_exact(good.quality("q5"))
    assert bad.quality("q5").status == "conflict"
    assert not quality_is_strict_exact(bad.quality("q5"))
    assert bad.status == "conflict"


def test_ready_singleton_without_completion_flags_never_formats_as_100() -> None:
    raw = _quality(
        status="ready",
        lock_strength="probabilistic",
        candidates=[{"count": 6, "cells": 25, "probability": 1.0}],
        shown_probability=1.0,
        other_probability=0.0,
        truncated=False,
        cap_hit=False,
    )
    raw.pop("truncated")
    raw.pop("cap_hit")

    quality = detail_composition_view(
        {"quality_composition": {"q6": raw}}
    ).quality("q6")

    assert quality.status == "ready"
    assert quality.completion_flags_complete is False
    assert not quality_is_strict_exact(quality)
    assert format_quality_probability(quality, quality.candidates[0].probability) == "<100%"
    assert format_quality_probability(quality, quality.shown_probability) == "<100%"


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
def test_exact_rejects_tail_or_incomplete_candidate_metadata(
    probability: float,
    shown_probability: float,
    other_probability: float,
    truncated: bool,
    cap_hit: bool,
) -> None:
    quality = _quality(
        status="exact",
        lock_strength="exact",
        candidates=[{"count": 4, "cells": 24, "probability": probability}],
        shown_probability=shown_probability,
        other_probability=other_probability,
        truncated=truncated,
        cap_hit=cap_hit,
    )

    view = detail_composition_view({"quality_composition": {"q5": quality}})

    assert view.status == "conflict"
    assert view.quality("q5").status == "conflict"
    assert not quality_is_strict_exact(view.quality("q5"))


@pytest.mark.parametrize(
    "candidate",
    [
        {"count": True, "cells": 6, "probability": 1.0},
        {"count": 1, "cells": -1, "probability": 1.0},
        {"count": 1, "cells": 6, "probability": math.nan},
        {"count": 1, "cells": 6, "probability": math.inf},
        {"count": 1, "cells": 6, "probability": True},
        {"count": 1, "cells": 6, "probability": "1.0"},
    ],
)
def test_malformed_joint_candidate_fails_closed(candidate: dict[str, object]) -> None:
    view = detail_composition_view(
        {
            "quality_composition": {
                "q6": _quality(
                    candidates=[candidate],
                    shown_probability=1.0,
                    other_probability=0.0,
                )
            }
        }
    )

    assert view.quality("q6").status == "conflict"
    assert view.status == "conflict"


def test_duplicate_joint_state_and_inconsistent_mass_fail_closed() -> None:
    duplicate = detail_composition_view(
        {
            "quality_composition": {
                "q4": _quality(
                    candidates=[
                        {"count": 2, "cells": 8, "probability": 0.5},
                        {"count": 2, "cells": 8, "probability": 0.3},
                    ]
                )
            }
        }
    )
    inconsistent = detail_composition_view(
        {
            "quality_composition": {
                "q4": _quality(
                    candidates=[{"count": 2, "cells": 8, "probability": 0.8}],
                    shown_probability=0.7,
                    other_probability=0.3,
                )
            }
        }
    )

    assert duplicate.quality("q4").status == "conflict"
    assert inconsistent.quality("q4").status == "conflict"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("truncated", "false"),
        ("cap_hit", 0),
        ("pricing_posterior", 1),
        ("display_reconciled", []),
    ],
)
def test_malformed_boolean_contract_fields_fail_closed(
    field: str, value: object
) -> None:
    quality = _quality()
    quality[field] = value
    view = detail_composition_view(
        {"quality_composition": {"q6": quality}}
    )

    red = view.quality("q6")
    assert red.status == "conflict"
    assert red.candidates == ()
    assert red.shown_probability is None
    assert red.other_probability is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "-"),
        (0.0, "0%"),
        (0.0004, "<0.1%"),
        (0.1234, "12.3%"),
        (0.9995, "<100%"),
        (math.nextafter(1.0, 0.0), "<100%"),
        (1.0, "100%"),
    ],
)
def test_probability_format_is_stable(value: float | None, expected: str) -> None:
    assert format_probability(value) == expected


def test_compact_quality_state_badge_uses_only_structured_joint_authority() -> None:
    from detail_composition_view import format_quality_state_badge

    view = detail_composition_view(
        {
            "quality_composition": {
                "status": "ready",
                "q1": _quality(
                    candidates=[
                        {"count": 10, "cells": 20, "probability": 0.8},
                        {"count": 12, "cells": 24, "probability": 0.2},
                    ],
                    shown_probability=1.0,
                    other_probability=0.0,
                    truncated=False,
                    cap_hit=False,
                ),
                "q3": _quality(
                    status="exact",
                    lock_strength="exact",
                    candidates=[
                        {"count": 4, "cells": 12, "probability": 1.0}
                    ],
                    shown_probability=1.0,
                    other_probability=0.0,
                    truncated=False,
                    cap_hit=False,
                ),
            }
        }
    )

    assert format_quality_state_badge(view.quality("q3")) == "锁定"
    assert format_quality_state_badge(view.quality("q1")) == "未锁"
    assert format_quality_state_badge(view.quality("q4")) == ""
    assert (
        format_quality_state_badge(view.quality("q1"), view.quality("q3"))
        == "未锁"
    )


def test_compact_quality_state_badge_never_infers_lock_from_collapsed_range() -> None:
    from detail_composition_view import format_quality_state_badge

    quality = detail_composition_view(
        {
            "quality_composition": {
                "q5": _quality(
                    status="ready",
                    lock_strength="probabilistic",
                    candidates=[
                        {"count": 6, "cells": 19, "probability": 0.9733},
                        {"count": 12, "cells": 38, "probability": 0.0267},
                    ],
                    shown_probability=1.0,
                    other_probability=0.0,
                    truncated=False,
                    cap_hit=False,
                )
            }
        }
    ).quality("q5")

    assert format_quality_state_badge(quality) == "未锁"


def test_dimension_state_keeps_exact_count_when_cells_remain_probabilistic() -> None:
    from detail_composition_view import (
        format_compact_quality_footprint,
        format_quality_dimension_state_badge,
        quality_dimension_is_exact,
    )

    quality = detail_composition_view(
        {
            "quality_composition": {
                "q3": {
                    **_quality(
                        candidates=[
                            {"count": 8, "cells": 13, "probability": 0.2},
                            {"count": 8, "cells": 15, "probability": 0.6},
                            {"count": 8, "cells": 17, "probability": 0.2},
                        ],
                        shown_probability=1.0,
                        other_probability=0.0,
                        truncated=False,
                        cap_hit=False,
                    ),
                    "count_candidates": [
                        {"value": 8, "probability": 1.0},
                    ],
                    "cells_candidates": [
                        {"value": 15, "probability": 0.6},
                        {"value": 13, "probability": 0.2},
                        {"value": 17, "probability": 0.2},
                    ],
                }
            }
        }
    ).quality("q3")

    assert quality_dimension_is_exact(quality, "count") is True
    assert quality_dimension_is_exact(quality, "cells") is False
    assert format_quality_dimension_state_badge(quality) == "件锁"
    assert format_compact_quality_footprint(quality) == "8件·13–17格"


def test_compact_footprint_distinguishes_dense_ranges_from_discrete_candidates() -> None:
    from detail_composition_view import format_compact_quality_footprint

    quality = detail_composition_view(
        {
            "quality_composition": {
                "q4": _quality(
                    candidates=[
                        {"count": 7, "cells": 14, "probability": 0.40},
                        {"count": 8, "cells": 16, "probability": 0.35},
                        {"count": 9, "cells": 15, "probability": 0.25},
                    ],
                    shown_probability=1.0,
                    other_probability=0.0,
                    truncated=False,
                    cap_hit=False,
                )
            }
        }
    ).quality("q4")

    # A dense integer domain remains a compact envelope.
    assert format_compact_quality_footprint(quality, compact_range=True) == (
        "7–9件·14–16格"
    )

    gap_of_two = detail_composition_view(
        {
            "quality_composition": {
                "q4": _quality(
                    candidates=[
                        {"count": 7, "cells": 14, "probability": 0.55},
                        {"count": 9, "cells": 18, "probability": 0.45},
                    ],
                    shown_probability=1.0,
                    other_probability=0.0,
                    truncated=False,
                    cap_hit=False,
                )
            }
        }
    ).quality("q4")
    assert format_compact_quality_footprint(
        gap_of_two,
        compact_range=True,
    ) == "7–9件·14–18格"

    long_unsorted = detail_composition_view(
        {
            "quality_composition": {
                "q4": _quality(
                    candidates=[
                        {"count": 14, "cells": 28, "probability": 0.20},
                        {"count": 7, "cells": 14, "probability": 0.20},
                        {"count": 8, "cells": 16, "probability": 0.20},
                        {"count": 22, "cells": 44, "probability": 0.15},
                        {"count": 21, "cells": 42, "probability": 0.15},
                        {"count": 18, "cells": 36, "probability": 0.10},
                    ],
                    shown_probability=1.0,
                    other_probability=0.0,
                    truncated=False,
                    cap_hit=False,
                )
            }
        }
    ).quality("q4")
    assert format_compact_quality_footprint(
        long_unsorted,
        compact_range=True,
    ) == "7–22件·14–44格"

    decimal_constrained = detail_composition_view(
        {
            "quality_composition": {
                "q5": _quality(
                    candidates=[
                        {"count": 8, "cells": 47, "probability": 0.10},
                        {"count": 16, "cells": 94, "probability": 0.90},
                    ],
                    shown_probability=1.0,
                    other_probability=0.0,
                    truncated=False,
                    cap_hit=False,
                    discrete_count_display=True,
                )
            }
        }
    ).quality("q5")
    assert format_compact_quality_footprint(
        decimal_constrained,
        compact_range=True,
    ) == "8/16/16件·47–94格"

    hostile_marker = detail_composition_view(
        {
            "quality_composition": {
                "q5": _quality(
                    candidates=[
                        {"count": 8, "cells": 47, "probability": 0.10},
                        {"count": 16, "cells": 94, "probability": 0.90},
                    ],
                    shown_probability=1.0,
                    other_probability=0.0,
                    truncated=False,
                    cap_hit=False,
                    discrete_count_display=1,
                )
            }
        }
    ).quality("q5")
    assert hostile_marker.status == "conflict"
    assert format_compact_quality_footprint(hostile_marker) == "—件·—格"

    duplicated = detail_composition_view(
        {
            "quality_composition": {
                "q4": _quality(
                    candidates=[
                        {"count": 7, "cells": 14, "probability": 0.50},
                        {"count": 7, "cells": 14, "probability": 0.50},
                    ],
                    shown_probability=1.0,
                    other_probability=0.0,
                    truncated=False,
                    cap_hit=False,
                )
            }
        }
    ).quality("q4")
    assert format_compact_quality_footprint(
        duplicated,
        compact_range=True,
    ) == "—件·—格"


def test_compact_footprint_uses_joint_rows_when_visible_count_arm_hides_tail() -> None:
    from detail_composition_view import (
        CompositionCandidate,
        QualityComposition,
        format_compact_quality_footprint,
    )

    quality = QualityComposition(
        key="q5",
        label="金",
        status="ready",
        source="pricing_posterior",
        lock_strength="probabilistic",
        lock_reason="combo_multi_solution",
        candidates=(
            CompositionCandidate(count=8, cells=47, probability=0.996),
            CompositionCandidate(count=16, cells=94, probability=0.004),
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
        compact_count_candidates=(),
        compact_count_triplet=(8, 8, 8),
        count_correlation_state="partial",
        count_correlation_note="visible total arm keeps the dominant count",
    )

    # The former independent triplet zip produced (8, 94), rejected the whole
    # row, and rendered "待定" despite two valid paired candidates in hover.
    assert format_compact_quality_footprint(quality) == "8–16件·47–94格"


def test_compact_footprint_keeps_same_round_multiple_constraint_visible() -> None:
    from detail_composition_view import (
        CompositionCandidate,
        QualityComposition,
        format_compact_quality_footprint,
    )

    quality = QualityComposition(
        key="q1",
        label="白绿",
        status="ready",
        source="pricing_posterior",
        lock_strength="probabilistic",
        lock_reason="combo_multi_solution",
        candidates=(
            CompositionCandidate(count=10, cells=26, probability=0.4),
            CompositionCandidate(count=15, cells=39, probability=0.35),
            CompositionCandidate(count=20, cells=52, probability=0.25),
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
        count_correlation_state="unknown",
    )

    assert format_compact_quality_footprint(quality, compact_range=True) == (
        "10–20件·26–52格"
    )


def test_compact_red_triplet_centres_canonical_most_likely_without_tail_widening() -> None:
    from detail_composition_view import format_compact_quality_count

    quality = detail_composition_view(
        {
            "quality_composition": {
                "q6": {
                    **_quality(
                        candidates=[
                            {"count": 5, "cells": 17, "probability": 0.60},
                            {"count": 4, "cells": 15, "probability": 0.25},
                            {"count": 6, "cells": 19, "probability": 0.15},
                        ],
                        shown_probability=1.0,
                        other_probability=0.0,
                        truncated=False,
                        cap_hit=False,
                    ),
                    "count_candidates": [
                        {"value": 5, "probability": 0.60},
                        {"value": 4, "probability": 0.25},
                        {"value": 6, "probability": 0.15},
                    ],
                    "cells_candidates": [
                        {"value": 17, "probability": 0.60},
                        {"value": 15, "probability": 0.25},
                        {"value": 19, "probability": 0.15},
                    ],
                }
            }
        }
    ).quality("q6")

    assert format_compact_quality_count(quality, fallback="2 / 4 / 6") == (
        "保 2 / 均 5 / 激 6件"
    )


def test_exact_count_fact_survives_an_unrelated_joint_conflict_without_claiming_cells() -> None:
    from ahmad_live_panel_server import _quality_exact_count_facts

    result = {
        "status": "ok",
        "quality_count_candidates": {
            "q1": [
                {"value": 10, "prob": 0.6},
                {"value": 12, "prob": 0.4},
            ],
            "q3": [{"value": 8, "prob": 1.0}],
        },
        "evidence": {},
    }
    facts = _quality_exact_count_facts(
        result,
        {
            "status": "conflict",
            "reason": "unrelated tier conflict",
            "qualities": {},
        },
    )

    assert facts == {
        "q3": {"count": 8, "source": "count_marginal_exact"},
    }


@pytest.mark.parametrize("status", ("loading", "stale", "conflict", "unavailable"))
def test_compact_quality_state_badge_hides_non_ready_states(status: str) -> None:
    from detail_composition_view import format_quality_state_badge

    quality = detail_composition_view(
        {
            "quality_composition": {
                "q4": _quality(
                    status=status,
                    lock_strength="exact",
                    candidates=[
                        {"count": 8, "cells": 28, "probability": 1.0}
                    ],
                    shown_probability=1.0,
                    other_probability=0.0,
                    truncated=False,
                    cap_hit=False,
                )
            }
        }
    ).quality("q4")

    assert format_quality_state_badge(quality) == (
        "冲突" if status == "conflict" else ""
    )
