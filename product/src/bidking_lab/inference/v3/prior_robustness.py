"""Prior robustness diagnostics for v3 shadow inference.

The report is audit-only. It makes stale/missing/drop-activity priors visible
so downstream samplers can degrade conservatively instead of silently treating
old drop tables as authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from bidking_lab.inference.v3.summary import FeasibleSummaryReport


_ACTIVITY_SHIPWRECK_MAP_SUFFIX_FLOOR = 20


@dataclass(frozen=True)
class V3PriorRobustnessReport:
    status: str
    prior_usable: bool
    prior_trusted: bool
    fallback_mode: str
    activity_candidate: bool
    prior_stress_score: int
    reasons: tuple[str, ...] = ()

    def to_flat_dict(self, *, prefix: str = "v3_robust_") -> dict[str, Any]:
        return {
            f"{prefix}available": True,
            f"{prefix}affects_bid": False,
            f"{prefix}status": self.status,
            f"{prefix}prior_usable": self.prior_usable,
            f"{prefix}prior_trusted": self.prior_trusted,
            f"{prefix}fallback_mode": self.fallback_mode,
            f"{prefix}activity_candidate": self.activity_candidate,
            f"{prefix}prior_stress_score": self.prior_stress_score,
            f"{prefix}reasons": ";".join(self.reasons),
        }


def empty_prior_robustness_flat_dict(
    *,
    prefix: str = "v3_robust_",
) -> dict[str, Any]:
    return {
        f"{prefix}available": False,
        f"{prefix}affects_bid": False,
        f"{prefix}status": None,
        f"{prefix}prior_usable": False,
        f"{prefix}prior_trusted": False,
        f"{prefix}fallback_mode": None,
        f"{prefix}activity_candidate": False,
        f"{prefix}prior_stress_score": None,
        f"{prefix}reasons": None,
    }


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on"}


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _activity_candidate(map_id: int | None, map_family: str) -> bool:
    if map_id is None:
        return False
    if str(map_family) != "shipwreck":
        return False
    return int(map_id) % 100 >= _ACTIVITY_SHIPWRECK_MAP_SUFFIX_FLOOR


def _constraint_target(
    *,
    exact: int | float | None,
    floor: int | float | None,
) -> float | None:
    values = [
        float(value)
        for value in (exact, floor)
        if value is not None and float(value) > 0.0
    ]
    return max(values) if values else None


def _stress_reason(
    *,
    name: str,
    target: float | None,
    expected: float | None,
    absolute_margin: float,
    multiplier: float,
) -> str | None:
    if target is None or expected is None or expected <= 0.0:
        return None
    threshold = max(float(expected) + float(absolute_margin), float(expected) * multiplier)
    if float(target) > threshold:
        return f"{name}_above_prior"
    return None


def _prior_stress_reasons(
    summary: FeasibleSummaryReport | None,
    prior_fields: Mapping[str, Any],
) -> tuple[str, ...]:
    if summary is None:
        return ()
    q6 = summary.bucket(6)
    q6_count_target = _constraint_target(
        exact=q6.count_exact if q6 is not None else None,
        floor=q6.count_floor if q6 is not None else None,
    )
    q6_cells_target = _constraint_target(
        exact=q6.cells_exact if q6 is not None else None,
        floor=q6.cells_floor if q6 is not None else None,
    )
    q6_value_target = _constraint_target(
        exact=q6.value_exact if q6 is not None else None,
        floor=q6.value_floor if q6 is not None else None,
    )
    total_count_target = _constraint_target(
        exact=summary.session_total_count_exact,
        floor=summary.known_count_floor,
    )
    total_cells_target = _constraint_target(
        exact=summary.session_total_cells_exact,
        floor=summary.known_cells_floor,
    )
    total_value_target = _constraint_target(
        exact=None,
        floor=summary.known_value_floor,
    )
    checks = (
        _stress_reason(
            name="q6_count",
            target=q6_count_target,
            expected=_float_or_none(prior_fields.get("v3_prior_q6_expected_count")),
            absolute_margin=2.0,
            multiplier=2.50,
        ),
        _stress_reason(
            name="q6_cells",
            target=q6_cells_target,
            expected=_float_or_none(prior_fields.get("v3_prior_q6_expected_cells")),
            absolute_margin=8.0,
            multiplier=2.00,
        ),
        _stress_reason(
            name="q6_value",
            target=q6_value_target,
            expected=_float_or_none(prior_fields.get("v3_prior_q6_expected_value")),
            absolute_margin=250_000.0,
            multiplier=2.00,
        ),
        _stress_reason(
            name="total_count",
            target=total_count_target,
            expected=_float_or_none(prior_fields.get("v3_prior_expected_count")),
            absolute_margin=4.0,
            multiplier=1.75,
        ),
        _stress_reason(
            name="total_cells",
            target=total_cells_target,
            expected=_float_or_none(prior_fields.get("v3_prior_expected_cells")),
            absolute_margin=20.0,
            multiplier=1.60,
        ),
        _stress_reason(
            name="total_value",
            target=total_value_target,
            expected=_float_or_none(prior_fields.get("v3_prior_expected_value")),
            absolute_margin=300_000.0,
            multiplier=1.75,
        ),
    )
    return tuple(reason for reason in checks if reason is not None)


def assess_prior_robustness(
    *,
    map_id: int | None,
    map_family: str,
    summary: FeasibleSummaryReport | None,
    prior_fields: Mapping[str, Any],
    posterior_fields: Mapping[str, Any],
) -> V3PriorRobustnessReport:
    reasons: list[str] = []
    prior_available = _bool(prior_fields.get("v3_prior_available"))
    prior_error = prior_fields.get("v3_prior_error")
    posterior_available = _bool(posterior_fields.get("v3_post_available"))
    posterior_ready = _bool(posterior_fields.get("v3_post_ready"))
    match_scope = str(posterior_fields.get("v3_post_match_scope") or "")
    activity_candidate = _activity_candidate(map_id, map_family)

    if map_id is None:
        return V3PriorRobustnessReport(
            status="no_map_context",
            prior_usable=False,
            prior_trusted=False,
            fallback_mode="wait_for_map",
            activity_candidate=False,
            prior_stress_score=0,
            reasons=("no_map_id",),
        )

    if summary is not None and not summary.feasible:
        return V3PriorRobustnessReport(
            status="constraint_conflict",
            prior_usable=False,
            prior_trusted=False,
            fallback_mode="constraint_conflict",
            activity_candidate=activity_candidate,
            prior_stress_score=0,
            reasons=tuple(summary.conflicts[:5]),
        )

    if not prior_available:
        if prior_error:
            reasons.append(f"prior_error={prior_error}")
        else:
            reasons.append("prior_unavailable")
        if activity_candidate:
            reasons.append("activity_map_id_candidate")
        return V3PriorRobustnessReport(
            status="prior_unavailable",
            prior_usable=False,
            prior_trusted=False,
            fallback_mode="missing_prior_truth_only",
            activity_candidate=activity_candidate,
            prior_stress_score=0,
            reasons=tuple(reasons),
        )

    stress_reasons = _prior_stress_reasons(summary, prior_fields)
    reasons.extend(stress_reasons)
    stress_score = len(stress_reasons)

    if not posterior_available:
        return V3PriorRobustnessReport(
            status="prior_only",
            prior_usable=True,
            prior_trusted=stress_score == 0,
            fallback_mode="posterior_disabled",
            activity_candidate=activity_candidate,
            prior_stress_score=stress_score,
            reasons=tuple(reasons),
        )

    if not posterior_ready:
        reasons.append("posterior_not_ready")
        return V3PriorRobustnessReport(
            status="posterior_unavailable",
            prior_usable=False,
            prior_trusted=False,
            fallback_mode="evidence_only_no_posterior",
            activity_candidate=activity_candidate,
            prior_stress_score=stress_score,
            reasons=tuple(reasons),
        )

    if match_scope == "strict":
        return V3PriorRobustnessReport(
            status="prior_stressed" if stress_score else "ok",
            prior_usable=True,
            prior_trusted=stress_score == 0,
            fallback_mode="strict_with_prior_stress" if stress_score else "normal_prior",
            activity_candidate=activity_candidate,
            prior_stress_score=stress_score,
            reasons=tuple(reasons),
        )

    if match_scope == "summary_likelihood":
        reasons.append("summary_likelihood_fallback")
        return V3PriorRobustnessReport(
            status="prior_stressed" if stress_score else "weak_prior_fallback",
            prior_usable=True,
            prior_trusted=False,
            fallback_mode="summary_likelihood_conservative",
            activity_candidate=activity_candidate,
            prior_stress_score=stress_score,
            reasons=tuple(reasons),
        )

    if match_scope == "q6_projection":
        reasons.append("q6_projection_fallback")
        return V3PriorRobustnessReport(
            status="weak_prior_fallback",
            prior_usable=False,
            prior_trusted=False,
            fallback_mode="q6_projection_audit_only",
            activity_candidate=activity_candidate,
            prior_stress_score=stress_score,
            reasons=tuple(reasons),
        )

    reasons.append(f"match_scope={match_scope or 'unknown'}")
    return V3PriorRobustnessReport(
        status="weak_prior_fallback",
        prior_usable=True,
        prior_trusted=False,
        fallback_mode="unknown_posterior_scope",
        activity_candidate=activity_candidate,
        prior_stress_score=stress_score,
        reasons=tuple(reasons),
    )


__all__ = (
    "V3PriorRobustnessReport",
    "assess_prior_robustness",
    "empty_prior_robustness_flat_dict",
)
