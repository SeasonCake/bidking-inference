"""Shared q6 residual sampler gates and evidence profile helpers."""

from __future__ import annotations

from typing import Any


RANDOM_SAMPLE_AVG_PROFILE_SIGNAL_FLOOR = 20_000.0
AISHA_BOTTOM_ROW_RISK_THRESHOLD = 16
AISHA_SHIPWRECK_DEEP_ROW_THRESHOLD = 13
AISHA_SHIPWRECK_DEEP12_ROW_THRESHOLD = 12
AISHA_SHIPWRECK_DEEP11_ROW_THRESHOLD = 11
AISHA_Q6_QUALITY_ONLY_DEEP_ROW_THRESHOLD = 13


SHIPWRECK_PROFILE_V1_Q6_RESIDUAL_BOOST_PROFILES = frozenset(
    {
        ("aisha", "shipwreck", "shape+layout"),
        ("aisha", "shipwreck", "public:random_avg+shape+layout"),
        ("aisha", "shipwreck", "tool:category+shape+layout"),
        ("aisha", "shipwreck", "public:random_avg+tool:category+shape+layout"),
        ("ethan", "shipwreck", "layout"),
        ("ethan", "shipwreck", "public:random_avg+layout"),
        ("ethan", "shipwreck", "shape+layout"),
        ("ethan", "shipwreck", "public:random_avg+shape+layout"),
    }
)
ETHAN_VILLA_RANDOM_AVG_Q6_RESIDUAL_BOOST_PROFILES = frozenset(
    {
        ("ethan", "villa", "public:random_avg+layout"),
        ("ethan", "villa", "public:random_avg+shape+layout"),
    }
)
ETHAN_VILLA_RANDOM_AVG_Q6_PRIOR_FLOOR_PROFILES = (
    ETHAN_VILLA_RANDOM_AVG_Q6_RESIDUAL_BOOST_PROFILES
)
ETHAN_SHIPWRECK_LAYOUT_Q6_CONDITIONAL_TARGET_PROFILES = frozenset(
    {
        ("ethan", "shipwreck", "layout"),
        ("ethan", "shipwreck", "public:random_avg+layout"),
    }
)
AISHA_SHIPWRECK_PROFILE_V1_Q6_RESIDUAL_PROFILES = frozenset(
    {
        ("aisha", "shipwreck", "shape+layout"),
        ("aisha", "shipwreck", "public:random_avg+shape+layout"),
        ("aisha", "shipwreck", "tool:category+shape+layout"),
        ("aisha", "shipwreck", "public:random_avg+tool:category+shape+layout"),
    }
)
AISHA_HIDDEN_V1_Q6_RESIDUAL_PROFILES = frozenset(
    {
        ("aisha", "hidden", "shape+layout"),
    }
)
AISHA_VILLA_V1_Q6_RESIDUAL_PROFILES = frozenset(
    {
        ("aisha", "villa", "shape+layout"),
    }
)


def actionable_random_sample_avg_values(
    values: Any,
    *,
    signal_floor: float = RANDOM_SAMPLE_AVG_PROFILE_SIGNAL_FLOOR,
) -> tuple[tuple[int, float], ...]:
    """Keep random-sample averages strong enough to affect profile routing."""
    return tuple(
        (int(sample_count), float(value))
        for sample_count, value in (values or ())
        if float(value) >= signal_floor
    )


def aisha_bottom_row_risk(
    *,
    hero: str | None,
    map_family: str,
    bottom_row: int | None,
) -> bool:
    """Flag Aisha shipwreck layouts that extend into the lower grid."""
    return (
        str(hero or "").lower() == "aisha"
        and map_family == "shipwreck"
        and bottom_row is not None
        and int(bottom_row) >= AISHA_BOTTOM_ROW_RISK_THRESHOLD
    )


def q6_quality_only_local_diagnostics(
    store: Any,
    *,
    columns: int = 10,
) -> dict[str, int | None]:
    """Summarize q6 quality observations whose footprint is still unknown."""
    locals_ = [
        int(evidence.local_index)
        for evidence in store.items()
        if getattr(evidence, "quality", None) == 6
        and getattr(evidence, "cells", None) is None
        and getattr(evidence, "local_index", None) is not None
    ]
    deepest_local_index = max(locals_, default=None)
    return {
        "count": len(locals_),
        "deepest_local_index": deepest_local_index,
        "deepest_start_row": (
            deepest_local_index // columns + 1
            if deepest_local_index is not None and columns > 0
            else None
        ),
    }


def aisha_q6_quality_only_deep_local_risk(
    *,
    hero: str | None,
    map_family: str,
    evidence_profile_key: str,
    deepest_start_row: int | None,
) -> bool:
    """Flag review-only Aisha shipwreck risk from a deep q6 quality-only point."""
    key = (str(hero or "").lower(), map_family, evidence_profile_key)
    return (
        str(hero or "").lower() == "aisha"
        and map_family == "shipwreck"
        and key in AISHA_SHIPWRECK_PROFILE_V1_Q6_RESIDUAL_PROFILES
        and deepest_start_row is not None
        and int(deepest_start_row) >= AISHA_Q6_QUALITY_ONLY_DEEP_ROW_THRESHOLD
    )


def evidence_profile_key_from_problem(
    problem: Any,
    *,
    random_sample_avg_signal_floor: float = RANDOM_SAMPLE_AVG_PROFILE_SIGNAL_FLOOR,
) -> str:
    """Return a stable evidence-profile key for batch and live q6 gates."""
    parts: list[str] = []
    diagnostics = ";".join(getattr(problem, "diagnostics", ()) or ())
    public_parts: list[str] = []
    if "public_max_quality:" in diagnostics:
        public_parts.append("max_quality")
    if "public_max_item_cells:" in diagnostics:
        public_parts.append("max_item_cells")
    if public_parts:
        parts.append(f"public:{'+'.join(public_parts)}")
    if actionable_random_sample_avg_values(
        getattr(problem, "random_sample_avg_values", ()),
        signal_floor=random_sample_avg_signal_floor,
    ):
        parts.append("public:random_avg")
    if len(getattr(problem, "category_targets", ()) or ()) > 0:
        parts.append("tool:category")
    if len(getattr(problem, "shape_targets", ()) or ()) > 0:
        parts.append("shape")
    if getattr(getattr(problem, "layout", None), "trusted_footprint_count", 0) > 0:
        parts.append("layout")
    return "+".join(parts) if parts else "basic"


def q6_residual_boost_for_profile(
    *,
    hero: str | None,
    map_family: str,
    evidence_profile_key: str,
    requested_boost: float,
    gate: str,
    bottom_row: int | None = None,
) -> float:
    """Return active q6 residual boost for a named profile gate."""
    if requested_boost <= 1.0:
        return 1.0
    if gate == "all":
        return requested_boost
    key = (str(hero or "").lower(), map_family, evidence_profile_key)
    if gate == "shipwreck_profile_v1":
        return (
            requested_boost
            if key in SHIPWRECK_PROFILE_V1_Q6_RESIDUAL_BOOST_PROFILES
            or key in ETHAN_VILLA_RANDOM_AVG_Q6_RESIDUAL_BOOST_PROFILES
            else 1.0
        )
    if gate == "aisha_shipwreck_bottom_v1":
        return (
            requested_boost
            if key in AISHA_SHIPWRECK_PROFILE_V1_Q6_RESIDUAL_PROFILES
            and bottom_row is not None
            and int(bottom_row) >= AISHA_BOTTOM_ROW_RISK_THRESHOLD
            else 1.0
        )
    if gate == "aisha_shipwreck_deep_v1":
        return (
            requested_boost
            if key in AISHA_SHIPWRECK_PROFILE_V1_Q6_RESIDUAL_PROFILES
            and bottom_row is not None
            and int(bottom_row) >= AISHA_SHIPWRECK_DEEP_ROW_THRESHOLD
            else 1.0
        )
    return 1.0


def _aisha_shipwreck_profile_active(
    key: tuple[str, str, str],
    *,
    bottom_row: int | None,
    threshold: int,
) -> bool:
    return (
        key in AISHA_SHIPWRECK_PROFILE_V1_Q6_RESIDUAL_PROFILES
        and bottom_row is not None
        and int(bottom_row) >= threshold
    )


def q6_residual_prior_floor_ratio_for_profile(
    *,
    hero: str | None,
    map_family: str,
    evidence_profile_key: str,
    requested_ratio: float,
    gate: str,
    bottom_row: int | None = None,
) -> float:
    """Return active q6 prior count/cells floor ratio for a named profile gate."""
    if requested_ratio <= 0:
        return 0.0
    if gate == "all":
        return requested_ratio
    key = (str(hero or "").lower(), map_family, evidence_profile_key)
    if gate == "shipwreck_profile_v1":
        return (
            requested_ratio
            if key in SHIPWRECK_PROFILE_V1_Q6_RESIDUAL_BOOST_PROFILES
            or key in ETHAN_VILLA_RANDOM_AVG_Q6_RESIDUAL_BOOST_PROFILES
            else 0.0
        )
    if gate == "aisha_shipwreck_profile_v1":
        return (
            requested_ratio
            if key in AISHA_SHIPWRECK_PROFILE_V1_Q6_RESIDUAL_PROFILES
            else 0.0
        )
    if gate == "aisha_shipwreck_bottom_v1":
        return (
            requested_ratio
            if _aisha_shipwreck_profile_active(
                key,
                bottom_row=bottom_row,
                threshold=AISHA_BOTTOM_ROW_RISK_THRESHOLD,
            )
            else 0.0
        )
    if gate == "aisha_shipwreck_deep_v1":
        return (
            requested_ratio
            if _aisha_shipwreck_profile_active(
                key,
                bottom_row=bottom_row,
                threshold=AISHA_SHIPWRECK_DEEP_ROW_THRESHOLD,
            )
            else 0.0
        )
    if gate == "aisha_shipwreck_deep12_v1":
        return (
            requested_ratio
            if _aisha_shipwreck_profile_active(
                key,
                bottom_row=bottom_row,
                threshold=AISHA_SHIPWRECK_DEEP12_ROW_THRESHOLD,
            )
            else 0.0
        )
    if gate == "aisha_shipwreck_deep11_v1":
        return (
            requested_ratio
            if _aisha_shipwreck_profile_active(
                key,
                bottom_row=bottom_row,
                threshold=AISHA_SHIPWRECK_DEEP11_ROW_THRESHOLD,
            )
            else 0.0
        )
    if gate == "aisha_hidden_v1":
        return (
            requested_ratio
            if key in AISHA_HIDDEN_V1_Q6_RESIDUAL_PROFILES
            else 0.0
        )
    if gate == "aisha_villa_shape_layout_v1":
        return (
            requested_ratio
            if key in AISHA_VILLA_V1_Q6_RESIDUAL_PROFILES
            else 0.0
        )
    if gate == "ethan_villa_random_avg_v1":
        return (
            requested_ratio
            if key in ETHAN_VILLA_RANDOM_AVG_Q6_PRIOR_FLOOR_PROFILES
            else 0.0
        )
    if gate == "aisha_deep_or_hidden_v1":
        if (
            _aisha_shipwreck_profile_active(
                key,
                bottom_row=bottom_row,
                threshold=AISHA_SHIPWRECK_DEEP_ROW_THRESHOLD,
            )
        ) or key in AISHA_HIDDEN_V1_Q6_RESIDUAL_PROFILES:
            return requested_ratio
        return 0.0
    return 0.0


def q6_residual_value_power_for_profile(
    *,
    hero: str | None,
    map_family: str,
    evidence_profile_key: str,
    requested_power: float,
    gate: str,
    bottom_row: int | None = None,
) -> float:
    """Return active q6 residual value-tilt power for a named profile gate."""
    if requested_power <= 0:
        return 0.0
    if gate == "all":
        return requested_power
    key = (str(hero or "").lower(), map_family, evidence_profile_key)
    if gate == "aisha_shipwreck_profile_v1":
        return (
            requested_power
            if key in AISHA_SHIPWRECK_PROFILE_V1_Q6_RESIDUAL_PROFILES
            else 0.0
        )
    if gate == "aisha_shipwreck_deep_v1":
        return (
            requested_power
            if key in AISHA_SHIPWRECK_PROFILE_V1_Q6_RESIDUAL_PROFILES
            and bottom_row is not None
            and int(bottom_row) >= AISHA_SHIPWRECK_DEEP_ROW_THRESHOLD
            else 0.0
        )
    if gate == "ethan_villa_random_avg_v1":
        return (
            requested_power
            if key in ETHAN_VILLA_RANDOM_AVG_Q6_PRIOR_FLOOR_PROFILES
            else 0.0
        )
    return 0.0


def q6_conditional_target_active_for_profile(
    *,
    hero: str | None,
    map_family: str,
    evidence_profile_key: str,
    gate: str,
) -> bool:
    """Return whether an experimental q6 conditional target gate is active."""

    if gate in {"", "none"}:
        return False
    if gate == "all":
        return True
    key = (str(hero or "").lower(), map_family, evidence_profile_key)
    if gate == "ethan_shipwreck_layout_v1":
        return key in ETHAN_SHIPWRECK_LAYOUT_Q6_CONDITIONAL_TARGET_PROFILES
    return False


__all__ = (
    "AISHA_HIDDEN_V1_Q6_RESIDUAL_PROFILES",
    "AISHA_Q6_QUALITY_ONLY_DEEP_ROW_THRESHOLD",
    "AISHA_SHIPWRECK_PROFILE_V1_Q6_RESIDUAL_PROFILES",
    "AISHA_VILLA_V1_Q6_RESIDUAL_PROFILES",
    "AISHA_BOTTOM_ROW_RISK_THRESHOLD",
    "AISHA_SHIPWRECK_DEEP_ROW_THRESHOLD",
    "AISHA_SHIPWRECK_DEEP12_ROW_THRESHOLD",
    "AISHA_SHIPWRECK_DEEP11_ROW_THRESHOLD",
    "ETHAN_VILLA_RANDOM_AVG_Q6_PRIOR_FLOOR_PROFILES",
    "ETHAN_VILLA_RANDOM_AVG_Q6_RESIDUAL_BOOST_PROFILES",
    "ETHAN_SHIPWRECK_LAYOUT_Q6_CONDITIONAL_TARGET_PROFILES",
    "RANDOM_SAMPLE_AVG_PROFILE_SIGNAL_FLOOR",
    "SHIPWRECK_PROFILE_V1_Q6_RESIDUAL_BOOST_PROFILES",
    "actionable_random_sample_avg_values",
    "aisha_bottom_row_risk",
    "aisha_q6_quality_only_deep_local_risk",
    "evidence_profile_key_from_problem",
    "q6_conditional_target_active_for_profile",
    "q6_quality_only_local_diagnostics",
    "q6_residual_boost_for_profile",
    "q6_residual_prior_floor_ratio_for_profile",
    "q6_residual_value_power_for_profile",
)
