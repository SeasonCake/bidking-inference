"""Shadow-only residual target diagnostics for v3 summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bidking_lab.inference.v3.summary import FeasibleSummaryReport


NON_Q6_QUALITIES: tuple[int, ...] = (1, 2, 3, 4, 5)
RESIDUAL_FIELDS: tuple[str, ...] = ("count", "cells", "value")


@dataclass(frozen=True)
class ResidualTargetFieldReport:
    field: str
    status: str
    candidate: bool = False
    value: int | None = None
    total_exact: int | None = None
    non_q6_exact_sum: int | None = None
    q6_explicit_exact: int | None = None
    q6_floor: int = 0
    missing_non_q6_qualities: tuple[int, ...] = ()

    def to_flat_dict(self, *, prefix: str) -> dict[str, Any]:
        return {
            f"{prefix}_status": self.status,
            f"{prefix}_candidate": self.candidate,
            f"{prefix}_value": self.value,
            f"{prefix}_total_exact": self.total_exact,
            f"{prefix}_non_q6_exact_sum": self.non_q6_exact_sum,
            f"{prefix}_q6_explicit_exact": self.q6_explicit_exact,
            f"{prefix}_q6_floor": self.q6_floor,
            f"{prefix}_missing_non_q6_qualities": ",".join(
                str(quality) for quality in self.missing_non_q6_qualities
            ),
        }


@dataclass(frozen=True)
class V3ResidualTargetCandidateReport:
    count: ResidualTargetFieldReport
    cells: ResidualTargetFieldReport
    value: ResidualTargetFieldReport
    feasible: bool
    diagnostics: tuple[str, ...] = ()

    @property
    def candidate(self) -> bool:
        return self.count.candidate or self.cells.candidate or self.value.candidate

    @property
    def active(self) -> bool:
        return False

    @property
    def derived_fields(self) -> tuple[str, ...]:
        return tuple(
            field.field
            for field in (self.count, self.cells, self.value)
            if field.candidate
        )

    def to_flat_dict(self, *, prefix: str = "v3_rtc_") -> dict[str, Any]:
        out: dict[str, Any] = {
            f"{prefix}available": True,
            f"{prefix}ready": self.feasible,
            f"{prefix}affects_bid": False,
            f"{prefix}active": self.active,
            f"{prefix}candidate": self.candidate,
            f"{prefix}derived_fields": ",".join(self.derived_fields),
            f"{prefix}diagnostics": ";".join(self.diagnostics),
        }
        out.update(self.count.to_flat_dict(prefix=f"{prefix}q6_count"))
        out.update(self.cells.to_flat_dict(prefix=f"{prefix}q6_cells"))
        out.update(self.value.to_flat_dict(prefix=f"{prefix}q6_value"))
        return out


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _bucket_field(summary: FeasibleSummaryReport, quality: int, field: str, kind: str) -> int | None:
    bucket = summary.bucket(int(quality))
    if bucket is None:
        return None
    return _int_or_none(getattr(bucket, f"{field}_{kind}", None))


def _session_total_exact(summary: FeasibleSummaryReport, field: str) -> int | None:
    if field == "count":
        return _int_or_none(summary.session_total_count_exact)
    if field == "cells":
        return _int_or_none(summary.session_total_cells_exact)
    return None


def _empty_field(field: str, status: str) -> ResidualTargetFieldReport:
    return ResidualTargetFieldReport(field=field, status=status)


def _field_report(
    summary: FeasibleSummaryReport,
    field: str,
) -> ResidualTargetFieldReport:
    explicit = _bucket_field(summary, 6, field, "exact")
    q6_floor = _bucket_field(summary, 6, field, "floor") or 0
    total = _session_total_exact(summary, field)
    exact_by_quality = {
        quality: _bucket_field(summary, quality, field, "exact")
        for quality in NON_Q6_QUALITIES
    }
    missing = tuple(
        quality
        for quality, value in exact_by_quality.items()
        if value is None
    )
    non_q6_sum = (
        sum(int(value) for value in exact_by_quality.values() if value is not None)
        if not missing
        else None
    )
    base = {
        "field": field,
        "total_exact": total,
        "non_q6_exact_sum": non_q6_sum,
        "q6_explicit_exact": explicit,
        "q6_floor": q6_floor,
        "missing_non_q6_qualities": missing,
    }
    if explicit is not None:
        if total is not None and not missing:
            residual = int(total) - int(non_q6_sum or 0)
            if residual != explicit:
                return ResidualTargetFieldReport(
                    **base,
                    status="explicit_residual_conflict",
                    value=residual,
                )
            return ResidualTargetFieldReport(
                **base,
                status="explicit_matches_residual",
                value=explicit,
            )
        return ResidualTargetFieldReport(
            **base,
            status="already_explicit",
            value=explicit,
        )
    if total is None:
        return ResidualTargetFieldReport(**base, status="missing_total_exact")
    if missing:
        return ResidualTargetFieldReport(**base, status="missing_non_q6_exact")
    residual = int(total) - int(non_q6_sum or 0)
    if residual < 0:
        return ResidualTargetFieldReport(
            **base,
            status="negative_residual",
            value=residual,
        )
    if residual < int(q6_floor):
        return ResidualTargetFieldReport(
            **base,
            status="residual_below_q6_floor",
            value=residual,
        )
    return ResidualTargetFieldReport(
        **base,
        status="derived",
        candidate=True,
        value=residual,
    )


def assess_q6_residual_targets(
    summary: FeasibleSummaryReport | None,
) -> V3ResidualTargetCandidateReport:
    """Report q6 residual exact candidates without mutating hard constraints."""

    if summary is None:
        missing = tuple(_empty_field(field, "missing_summary") for field in RESIDUAL_FIELDS)
        return V3ResidualTargetCandidateReport(
            count=missing[0],
            cells=missing[1],
            value=missing[2],
            feasible=False,
            diagnostics=("missing_summary",),
        )
    if not summary.feasible:
        blocked = tuple(_empty_field(field, "summary_infeasible") for field in RESIDUAL_FIELDS)
        return V3ResidualTargetCandidateReport(
            count=blocked[0],
            cells=blocked[1],
            value=blocked[2],
            feasible=False,
            diagnostics=("summary_infeasible",),
        )
    reports = {field: _field_report(summary, field) for field in RESIDUAL_FIELDS}
    diagnostics = tuple(
        f"q6_{field}_{report.status}"
        for field, report in reports.items()
    )
    return V3ResidualTargetCandidateReport(
        count=reports["count"],
        cells=reports["cells"],
        value=reports["value"],
        feasible=True,
        diagnostics=diagnostics,
    )


def empty_residual_target_candidate_flat_dict(
    *,
    prefix: str = "v3_rtc_",
) -> dict[str, Any]:
    return V3ResidualTargetCandidateReport(
        count=_empty_field("count", "unavailable"),
        cells=_empty_field("cells", "unavailable"),
        value=_empty_field("value", "unavailable"),
        feasible=False,
        diagnostics=("unavailable",),
    ).to_flat_dict(prefix=prefix) | {f"{prefix}available": False}


__all__ = (
    "ResidualTargetFieldReport",
    "V3ResidualTargetCandidateReport",
    "assess_q6_residual_targets",
    "empty_residual_target_candidate_flat_dict",
)
