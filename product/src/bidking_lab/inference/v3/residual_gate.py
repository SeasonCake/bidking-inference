"""Gated residual shadow candidate for v3 posterior diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bidking_lab.inference.map_likelihood import QuantileSummary
from bidking_lab.inference.v3.calibration import PriorCalibrationEntry
from bidking_lab.inference.v3.posterior import (
    V3PosteriorReport,
    empty_posterior_flat_dict,
)

_RESIDUAL_GATE_ACTIVE_ENABLED = False


def _p50(summary: QuantileSummary | None) -> float | None:
    return float(summary.p50) if summary is not None else None


def _delta_p50(
    residual: QuantileSummary | None,
    baseline: QuantileSummary | None,
) -> float | None:
    left = _p50(residual)
    right = _p50(baseline)
    if left is None or right is None:
        return None
    return left - right


@dataclass(frozen=True)
class V3ResidualGateReport:
    baseline: V3PosteriorReport | None
    residual: V3PosteriorReport | None
    entry: PriorCalibrationEntry | None
    active: bool = False
    status: str = "inactive"
    gate_reason: str = ""
    diagnostics: tuple[str, ...] = ()

    @property
    def posterior(self) -> V3PosteriorReport | None:
        if self.active and self.residual is not None:
            return self.residual
        return self.baseline

    def to_flat_dict(self, *, prefix: str = "v3_resid_gate_") -> dict[str, Any]:
        if self.posterior is None:
            out = empty_posterior_flat_dict(prefix=prefix)
        else:
            out = self.posterior.to_flat_dict(prefix=prefix)
        out.update(
            {
                f"{prefix}active": self.active,
                f"{prefix}status": self.status,
                f"{prefix}gate_reason": self.gate_reason,
                f"{prefix}source": "residual" if self.active else "baseline",
                f"{prefix}archive_sessions": (
                    self.entry.archive_sessions if self.entry is not None else None
                ),
                f"{prefix}calibration_status": (
                    self.entry.status if self.entry is not None else "missing_entry"
                ),
                f"{prefix}calibration_gate_reason": (
                    self.entry.gate_reason if self.entry is not None else None
                ),
                f"{prefix}q6_count_delta_p50": _delta_p50(
                    self.residual.q6_count if self.residual is not None else None,
                    self.baseline.q6_count if self.baseline is not None else None,
                ),
                f"{prefix}q6_cells_delta_p50": _delta_p50(
                    self.residual.q6_cells if self.residual is not None else None,
                    self.baseline.q6_cells if self.baseline is not None else None,
                ),
                f"{prefix}q6_value_delta_p50": _delta_p50(
                    self.residual.q6_value if self.residual is not None else None,
                    self.baseline.q6_value if self.baseline is not None else None,
                ),
                f"{prefix}diagnostics": ";".join(self.diagnostics),
            }
        )
        return out


def gate_residual_posterior_report(
    baseline: V3PosteriorReport | None,
    residual: V3PosteriorReport | None,
    entry: PriorCalibrationEntry | None,
) -> V3ResidualGateReport:
    diagnostics: list[str] = []
    if baseline is None:
        return V3ResidualGateReport(
            baseline=None,
            residual=residual,
            entry=entry,
            status="inactive",
            gate_reason="missing_baseline",
            diagnostics=("missing_baseline",),
        )
    if not baseline.ready:
        return V3ResidualGateReport(
            baseline=baseline,
            residual=residual,
            entry=entry,
            status="inactive",
            gate_reason="baseline_not_ready",
            diagnostics=("baseline_not_ready",),
        )
    if residual is None or not residual.ready:
        return V3ResidualGateReport(
            baseline=baseline,
            residual=residual,
            entry=entry,
            status="inactive",
            gate_reason="residual_not_ready",
            diagnostics=("residual_not_ready",),
        )
    if entry is None:
        return V3ResidualGateReport(
            baseline=baseline,
            residual=residual,
            entry=None,
            status="inactive",
            gate_reason="missing_calibration_entry",
            diagnostics=("missing_calibration_entry",),
        )
    if not entry.active:
        return V3ResidualGateReport(
            baseline=baseline,
            residual=residual,
            entry=entry,
            status="watch_only",
            gate_reason=f"calibration_{entry.gate_reason or entry.status}",
            diagnostics=(f"calibration_status={entry.status}",),
        )
    if residual.match_scope != "residual_likelihood":
        return V3ResidualGateReport(
            baseline=baseline,
            residual=residual,
            entry=entry,
            status="inactive",
            gate_reason=f"residual_scope_{residual.match_scope}",
            diagnostics=(f"residual_scope={residual.match_scope}",),
        )
    baseline_count = _p50(baseline.q6_count)
    residual_count = _p50(residual.q6_count)
    if baseline_count is None or residual_count is None:
        return V3ResidualGateReport(
            baseline=baseline,
            residual=residual,
            entry=entry,
            status="inactive",
            gate_reason="missing_q6_count",
            diagnostics=("missing_q6_count",),
        )
    if residual_count > baseline_count:
        return V3ResidualGateReport(
            baseline=baseline,
            residual=residual,
            entry=entry,
            status="watch_only",
            gate_reason="q6_count_p50_increase",
            diagnostics=(
                f"q6_count_delta_p50={residual_count - baseline_count:.6f}",
            ),
        )
    baseline_cells = _p50(baseline.q6_cells)
    residual_cells = _p50(residual.q6_cells)
    if baseline_cells is None or residual_cells is None:
        return V3ResidualGateReport(
            baseline=baseline,
            residual=residual,
            entry=entry,
            status="inactive",
            gate_reason="missing_q6_cells",
            diagnostics=("missing_q6_cells",),
        )
    if residual_cells > baseline_cells:
        return V3ResidualGateReport(
            baseline=baseline,
            residual=residual,
            entry=entry,
            status="watch_only",
            gate_reason="q6_cells_p50_increase",
            diagnostics=(
                f"q6_cells_delta_p50={residual_cells - baseline_cells:.6f}",
            ),
        )
    baseline_value = _p50(baseline.q6_value)
    residual_value = _p50(residual.q6_value)
    if baseline_value is None or residual_value is None:
        return V3ResidualGateReport(
            baseline=baseline,
            residual=residual,
            entry=entry,
            status="inactive",
            gate_reason="missing_q6_value",
            diagnostics=("missing_q6_value",),
        )
    if residual_value > baseline_value:
        return V3ResidualGateReport(
            baseline=baseline,
            residual=residual,
            entry=entry,
            status="watch_only",
            gate_reason="q6_value_p50_increase",
            diagnostics=(
                f"q6_value_delta_p50={residual_value - baseline_value:.6f}",
            ),
        )
    if not _RESIDUAL_GATE_ACTIVE_ENABLED:
        return V3ResidualGateReport(
            baseline=baseline,
            residual=residual,
            entry=entry,
            status="watch_only",
            gate_reason="residual_gate_unproven",
            diagnostics=(
                "residual_gate_active_disabled",
                f"q6_count_delta_p50={residual_count - baseline_count:.6f}",
                f"q6_cells_delta_p50={residual_cells - baseline_cells:.6f}",
                f"q6_value_delta_p50={residual_value - baseline_value:.6f}",
            ),
        )
    diagnostics.append("calibration_active")
    diagnostics.append("residual_scope=residual_likelihood")
    diagnostics.append(f"q6_count_delta_p50={residual_count - baseline_count:.6f}")
    diagnostics.append(f"q6_cells_delta_p50={residual_cells - baseline_cells:.6f}")
    diagnostics.append(f"q6_value_delta_p50={residual_value - baseline_value:.6f}")
    return V3ResidualGateReport(
        baseline=baseline,
        residual=residual,
        entry=entry,
        active=True,
        status="active_shadow",
        gate_reason="systemic_under_residual_value_shadow",
        diagnostics=tuple(diagnostics),
    )


def empty_residual_gate_flat_dict(
    *,
    prefix: str = "v3_resid_gate_",
) -> dict[str, Any]:
    out = empty_posterior_flat_dict(prefix=prefix)
    out.update(
        {
            f"{prefix}active": False,
            f"{prefix}status": "missing",
            f"{prefix}gate_reason": "not_evaluated",
            f"{prefix}source": None,
            f"{prefix}archive_sessions": None,
            f"{prefix}calibration_status": None,
            f"{prefix}calibration_gate_reason": None,
            f"{prefix}q6_count_delta_p50": None,
            f"{prefix}q6_cells_delta_p50": None,
            f"{prefix}q6_value_delta_p50": None,
        }
    )
    return out


__all__ = (
    "V3ResidualGateReport",
    "empty_residual_gate_flat_dict",
    "gate_residual_posterior_report",
)
