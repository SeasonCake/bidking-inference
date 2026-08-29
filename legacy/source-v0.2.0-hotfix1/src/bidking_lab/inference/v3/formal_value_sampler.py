"""Shadow-only formal/value sampler candidate for v3.

This first-stage report is intentionally conservative. It does not change the
baseline posterior or formal bid path; it exposes value-floor candidates and
prior-stress diagnostics so archive/live/holdout can evaluate them with the
same fields.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from bidking_lab.inference.map_likelihood import QuantileSummary
from bidking_lab.inference.v3.posterior import (
    V3PosteriorReport,
    empty_posterior_flat_dict,
)
from bidking_lab.inference.v3.summary import FeasibleSummaryReport


@dataclass(frozen=True)
class FormalValueStressDetail:
    source: str
    target: float | None
    prior_expected: float | None

    @property
    def target_prior_ratio(self) -> float | None:
        if self.target is None or self.prior_expected is None or self.prior_expected <= 0.0:
            return None
        return self.target / self.prior_expected

    def to_flat_dict(self, *, prefix: str) -> dict[str, Any]:
        return {
            f"{prefix}_source": self.source,
            f"{prefix}_target": _round(self.target),
            f"{prefix}_prior_expected": _round(self.prior_expected),
            f"{prefix}_target_prior_ratio": _round(self.target_prior_ratio),
        }


@dataclass(frozen=True)
class V3FormalValueSamplerReport:
    baseline: V3PosteriorReport | None
    summary: FeasibleSummaryReport | None
    prior_fields: Mapping[str, Any] | None
    total_count: FormalValueStressDetail
    total_cells: FormalValueStressDetail
    q6_count: FormalValueStressDetail
    q6_cells: FormalValueStressDetail
    total_value: FormalValueStressDetail
    q6_value: FormalValueStressDetail
    stress_classes: tuple[str, ...] = ()
    capacity_flags: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.baseline is not None and self.baseline.ready

    @property
    def active(self) -> bool:
        return False

    @property
    def candidate(self) -> bool:
        return (
            self.ready
            and "value_floor_stress" in self.stress_classes
            and "capacity_cells_drift" not in self.stress_classes
            and "q6_cells_floor_stress" not in self.stress_classes
        )

    @property
    def mixed_value_floor_watch(self) -> bool:
        return self.ready and "value_floor_stress" in self.stress_classes and not self.candidate

    @property
    def stress_class(self) -> str:
        if not self.stress_classes:
            return "none"
        return "+".join(self.stress_classes)

    @property
    def status(self) -> str:
        if self.baseline is None:
            return "missing_baseline"
        if not self.baseline.ready:
            return "not_ready"
        if self.prior_fields is None or not _bool(self.prior_fields.get("v3_prior_available")):
            return "prior_unavailable"
        if self.candidate:
            return "watch_only_value_floor_candidate"
        if self.mixed_value_floor_watch:
            return "watch_mixed_value_floor_guarded"
        if "capacity_cells_drift" in self.stress_classes:
            return "watch_capacity_cells_drift"
        if "q6_cells_floor_stress" in self.stress_classes:
            return "watch_q6_cells_floor"
        return "baseline_passthrough"

    @property
    def gate_reason(self) -> str:
        if self.status == "missing_baseline":
            return "missing_baseline"
        if self.status == "not_ready":
            return "baseline_not_ready"
        if self.status == "prior_unavailable":
            return "missing_or_unavailable_prior"
        if self.candidate:
            return "hard_value_floor_shadow_only"
        if self.stress_classes:
            return "stress_requires_separate_sampler_design"
        return "no_formal_value_candidate"

    @property
    def source(self) -> str:
        return "hard_value_floor_candidate" if self.candidate else "baseline"

    @property
    def posterior(self) -> V3PosteriorReport | None:
        if self.baseline is None:
            return None
        if not self.candidate:
            return self.baseline
        total_floor = self.total_value.target
        q6_floor = self.q6_value.target
        diagnostics = tuple(self.baseline.diagnostics) + (
            f"fv_status={self.status}",
            f"fv_stress_class={self.stress_class}",
            f"fv_gate={self.gate_reason}",
        )
        return V3PosteriorReport(
            map_id=self.baseline.map_id,
            map_name=self.baseline.map_name,
            n_total=self.baseline.n_total,
            n_matched=self.baseline.n_matched,
            n_strict_matched=self.baseline.n_strict_matched,
            match_scope=self.baseline.match_scope,
            q6_present_rate=self.baseline.q6_present_rate,
            total_cells=self.baseline.total_cells,
            total_value=_floor_summary(self.baseline.total_value, total_floor),
            formal_decision_value=_floor_summary(
                self.baseline.formal_decision_value,
                total_floor,
            ),
            tail_replacement_decision_value=self.baseline.tail_replacement_decision_value,
            q6_count=self.baseline.q6_count,
            q6_cells=self.baseline.q6_cells,
            q6_value=_floor_summary(self.baseline.q6_value, q6_floor),
            q6_formal_decision_value=_floor_summary(
                self.baseline.q6_formal_decision_value,
                q6_floor,
            ),
            q6_tail_replacement_decision_value=(
                self.baseline.q6_tail_replacement_decision_value
            ),
            diagnostics=diagnostics,
        )

    def to_flat_dict(self, *, prefix: str = "v3_fv_") -> dict[str, Any]:
        posterior = self.posterior
        out = (
            empty_posterior_flat_dict(prefix=prefix)
            if posterior is None
            else posterior.to_flat_dict(prefix=prefix)
        )
        out.update(
            {
                f"{prefix}active": self.active,
                f"{prefix}candidate": self.candidate,
                f"{prefix}mixed_value_floor_watch": self.mixed_value_floor_watch,
                f"{prefix}status": self.status,
                f"{prefix}gate_reason": self.gate_reason,
                f"{prefix}source": self.source,
                f"{prefix}stress_class": self.stress_class,
                f"{prefix}capacity_flags": "+".join(self.capacity_flags),
                f"{prefix}diagnostics": ";".join(self.diagnostics),
            }
        )
        out.update(self.total_count.to_flat_dict(prefix=f"{prefix}total_count"))
        out.update(self.total_cells.to_flat_dict(prefix=f"{prefix}total_cells"))
        out.update(self.q6_count.to_flat_dict(prefix=f"{prefix}q6_count"))
        out.update(self.q6_cells.to_flat_dict(prefix=f"{prefix}q6_cells"))
        out.update(self.total_value.to_flat_dict(prefix=f"{prefix}total_value"))
        out.update(self.q6_value.to_flat_dict(prefix=f"{prefix}q6_value"))
        return out


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _round(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None else None


def _source_and_target(
    *,
    exact: int | float | None,
    floor: int | float | None,
) -> tuple[str, float | None]:
    exact_value = _finite_float(exact)
    floor_value = _finite_float(floor)
    if exact_value is not None and exact_value > 0.0:
        if floor_value is not None and floor_value > exact_value:
            return ("floor_over_exact", floor_value)
        return ("exact", exact_value)
    if floor_value is not None and floor_value > 0.0:
        return ("floor", floor_value)
    return ("none", None)


def _stress_detail(
    *,
    exact: int | float | None = None,
    floor: int | float | None = None,
    prior_expected: Any = None,
) -> FormalValueStressDetail:
    source, target = _source_and_target(exact=exact, floor=floor)
    return FormalValueStressDetail(
        source=source,
        target=target,
        prior_expected=_finite_float(prior_expected),
    )


def _above_prior(
    detail: FormalValueStressDetail,
    *,
    absolute_margin: float,
    multiplier: float,
) -> bool:
    target = detail.target
    expected = detail.prior_expected
    if target is None or expected is None or expected <= 0.0:
        return False
    threshold = max(expected + float(absolute_margin), expected * float(multiplier))
    return target > threshold


def _floor_summary(
    summary: QuantileSummary | None,
    floor: float | None,
) -> QuantileSummary | None:
    if summary is None or floor is None or floor <= 0.0:
        return summary
    value = float(floor)
    return QuantileSummary(
        p10=max(float(summary.p10), value),
        p50=max(float(summary.p50), value),
        p90=max(float(summary.p90), value),
    )


def _capacity_flags(
    *,
    total_count: FormalValueStressDetail,
    prior_fields: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    if prior_fields is None:
        return ()
    flags: list[str] = []
    prior_max = _finite_float(prior_fields.get("v3_prior_items_per_session_max"))
    if (
        prior_max is not None
        and total_count.target is not None
        and total_count.target > prior_max
    ):
        flags.append("target_count_above_prior_max")
    return tuple(flags)


def sample_formal_value_report(
    posterior: V3PosteriorReport | None,
    *,
    summary: FeasibleSummaryReport | None,
    prior_fields: Mapping[str, Any] | None = None,
) -> V3FormalValueSamplerReport:
    if summary is None:
        empty = FormalValueStressDetail("none", None, None)
        return V3FormalValueSamplerReport(
            baseline=posterior,
            summary=None,
            prior_fields=prior_fields,
            total_count=empty,
            total_cells=empty,
            q6_count=empty,
            q6_cells=empty,
            total_value=empty,
            q6_value=empty,
            diagnostics=("missing_summary",),
        )

    q6 = summary.bucket(6)
    prior_fields = prior_fields or {}
    total_count = _stress_detail(
        exact=summary.session_total_count_exact,
        floor=summary.known_count_floor,
        prior_expected=prior_fields.get("v3_prior_expected_count"),
    )
    total_cells = _stress_detail(
        exact=summary.session_total_cells_exact,
        floor=summary.known_cells_floor,
        prior_expected=prior_fields.get("v3_prior_expected_cells"),
    )
    q6_count = _stress_detail(
        exact=q6.count_exact if q6 is not None else None,
        floor=q6.count_floor if q6 is not None else None,
        prior_expected=prior_fields.get("v3_prior_q6_expected_count"),
    )
    q6_cells = _stress_detail(
        exact=q6.cells_exact if q6 is not None else None,
        floor=q6.cells_floor if q6 is not None else None,
        prior_expected=prior_fields.get("v3_prior_q6_expected_cells"),
    )
    total_value = _stress_detail(
        floor=summary.known_value_floor,
        prior_expected=prior_fields.get("v3_prior_expected_value"),
    )
    q6_value = _stress_detail(
        exact=q6.value_exact if q6 is not None else None,
        floor=q6.value_floor if q6 is not None else None,
        prior_expected=prior_fields.get("v3_prior_q6_expected_value"),
    )

    stress_classes: list[str] = []
    if (
        _above_prior(total_count, absolute_margin=4.0, multiplier=1.75)
        or _above_prior(total_cells, absolute_margin=20.0, multiplier=1.60)
    ):
        stress_classes.append("capacity_cells_drift")
    if _above_prior(q6_cells, absolute_margin=8.0, multiplier=2.00):
        stress_classes.append("q6_cells_floor_stress")
    if (
        _above_prior(total_value, absolute_margin=300_000.0, multiplier=1.75)
        or _above_prior(q6_value, absolute_margin=250_000.0, multiplier=2.00)
    ):
        stress_classes.append("value_floor_stress")
    capacity_flags = _capacity_flags(
        total_count=total_count,
        prior_fields=prior_fields,
    )
    if capacity_flags and "capacity_cells_drift" not in stress_classes:
        stress_classes.append("capacity_cells_drift")

    diagnostics = (
        f"fv_total_cells_ratio={total_cells.target_prior_ratio}",
        f"fv_q6_cells_ratio={q6_cells.target_prior_ratio}",
        f"fv_total_value_ratio={total_value.target_prior_ratio}",
        f"fv_q6_value_ratio={q6_value.target_prior_ratio}",
    )
    return V3FormalValueSamplerReport(
        baseline=posterior,
        summary=summary,
        prior_fields=prior_fields,
        total_count=total_count,
        total_cells=total_cells,
        q6_count=q6_count,
        q6_cells=q6_cells,
        total_value=total_value,
        q6_value=q6_value,
        stress_classes=tuple(stress_classes),
        capacity_flags=capacity_flags,
        diagnostics=diagnostics,
    )


def empty_formal_value_sampler_flat_dict(
    *,
    prefix: str = "v3_fv_",
) -> dict[str, Any]:
    empty = FormalValueStressDetail("none", None, None)
    return V3FormalValueSamplerReport(
        baseline=None,
        summary=None,
        prior_fields=None,
        total_count=empty,
        total_cells=empty,
        q6_count=empty,
        q6_cells=empty,
        total_value=empty,
        q6_value=empty,
    ).to_flat_dict(prefix=prefix)


__all__ = (
    "FormalValueStressDetail",
    "V3FormalValueSamplerReport",
    "empty_formal_value_sampler_flat_dict",
    "sample_formal_value_report",
)
