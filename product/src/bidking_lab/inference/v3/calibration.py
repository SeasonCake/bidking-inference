"""Empirical prior calibration shadow for v3 posterior reports.

This module is intentionally diagnostic-only. It turns archive-vs-prior
calibration evidence into a bounded shadow transform and never changes formal
bid decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import math
from typing import Any, Mapping

from bidking_lab.inference.map_likelihood import QuantileSummary
from bidking_lab.inference.v3.posterior import (
    V3PosteriorReport,
    empty_posterior_flat_dict,
)

_MIN_ACTIVE_SESSIONS = 20
_HIDDEN_MIN_ACTIVE_SESSIONS = 50
_HIGH_OVER_RATE_GUARD = 0.60
_NEUTRAL_RATIO_LOW = 0.90
_NEUTRAL_RATIO_HIGH = 1.10
_MAX_UPWARD_SCALE = 1.25
_MAX_DOWNWARD_SCALE = 1.00
_MAX_SHRINK = 0.45
_SHRINK_SESSION_HALF_LIFE = 45.0
_SYSTEMIC_UNDER_BIAS_RATIO = 0.50


@dataclass(frozen=True)
class PriorCalibrationEntry:
    map_id: int
    map_name: str = ""
    map_family: str = ""
    archive_sessions: int = 0
    prior_trials: int = 0
    actual_raw_p50: float | None = None
    actual_raw_p90: float | None = None
    prior_raw_p50: float | None = None
    prior_raw_p90: float | None = None
    median_ratio: float | None = None
    p90_ratio: float | None = None
    formal_p50_over_rate: float | None = None
    baseline_formal_p50_mae: float | None = None
    baseline_formal_p50_bias: float | None = None
    status: str = "unscored"
    gate_reason: str = ""
    scale: float = 1.0
    source: str = "archive_prior_shadow"

    @property
    def active(self) -> bool:
        return self.status == "active_shadow" and self.scale != 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "map_id": self.map_id,
            "map_name": self.map_name,
            "map_family": self.map_family,
            "archive_sessions": self.archive_sessions,
            "prior_trials": self.prior_trials,
            "actual_raw_p50": self.actual_raw_p50,
            "actual_raw_p90": self.actual_raw_p90,
            "prior_raw_p50": self.prior_raw_p50,
            "prior_raw_p90": self.prior_raw_p90,
            "median_ratio": self.median_ratio,
            "p90_ratio": self.p90_ratio,
            "formal_p50_over_rate": self.formal_p50_over_rate,
            "baseline_formal_p50_mae": self.baseline_formal_p50_mae,
            "baseline_formal_p50_bias": self.baseline_formal_p50_bias,
            "status": self.status,
            "gate_reason": self.gate_reason,
            "scale": self.scale,
            "source": self.source,
        }


@dataclass(frozen=True)
class V3PriorCalibrationReport:
    posterior: V3PosteriorReport | None
    entry: PriorCalibrationEntry | None
    diagnostics: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.posterior is not None and self.posterior.ready

    @property
    def active(self) -> bool:
        return self.entry is not None and self.entry.active and self.ready

    def to_flat_dict(self, *, prefix: str = "v3_cal_") -> dict[str, Any]:
        if self.posterior is None:
            out = empty_posterior_flat_dict(prefix=prefix)
        else:
            out = self.posterior.to_flat_dict(prefix=prefix)
        entry = self.entry
        out.update(
            {
                f"{prefix}active": self.active,
                f"{prefix}status": entry.status if entry is not None else "missing_entry",
                f"{prefix}gate_reason": (
                    entry.gate_reason if entry is not None else "no_calibration_entry"
                ),
                f"{prefix}scale": round(entry.scale, 6) if entry is not None else 1.0,
                f"{prefix}archive_sessions": (
                    entry.archive_sessions if entry is not None else None
                ),
                f"{prefix}prior_trials": entry.prior_trials if entry is not None else None,
                f"{prefix}median_ratio": (
                    round(entry.median_ratio, 6)
                    if entry is not None and entry.median_ratio is not None
                    else None
                ),
                f"{prefix}p90_ratio": (
                    round(entry.p90_ratio, 6)
                    if entry is not None and entry.p90_ratio is not None
                    else None
                ),
                f"{prefix}formal_p50_over_rate": (
                    round(entry.formal_p50_over_rate, 6)
                    if entry is not None and entry.formal_p50_over_rate is not None
                    else None
                ),
                f"{prefix}baseline_formal_p50_mae": (
                    round(entry.baseline_formal_p50_mae, 6)
                    if entry is not None
                    and entry.baseline_formal_p50_mae is not None
                    else None
                ),
                f"{prefix}baseline_formal_p50_bias": (
                    round(entry.baseline_formal_p50_bias, 6)
                    if entry is not None
                    and entry.baseline_formal_p50_bias is not None
                    else None
                ),
                f"{prefix}source": entry.source if entry is not None else None,
                f"{prefix}diagnostics": ";".join(self.diagnostics),
            }
        )
        return out


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _optional_int(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def propose_prior_calibration(
    *,
    map_id: int,
    map_name: str = "",
    map_family: str = "",
    archive_sessions: int,
    prior_trials: int = 0,
    actual_raw_p50: float | None = None,
    actual_raw_p90: float | None = None,
    prior_raw_p50: float | None = None,
    prior_raw_p90: float | None = None,
    median_ratio: float | None,
    p90_ratio: float | None = None,
    formal_p50_over_rate: float | None = None,
    baseline_formal_p50_mae: float | None = None,
    baseline_formal_p50_bias: float | None = None,
    source: str = "archive_prior_shadow",
) -> PriorCalibrationEntry:
    sessions = max(0, int(archive_sessions))
    ratio = _finite_float(median_ratio)
    p90 = _finite_float(p90_ratio)
    over_rate = _finite_float(formal_p50_over_rate)
    baseline_mae = _finite_float(baseline_formal_p50_mae)
    baseline_bias = _finite_float(baseline_formal_p50_bias)
    family = str(map_family or "")
    status = "inactive"
    reason = "neutral_ratio"
    scale = 1.0

    if ratio is None or ratio <= 0.0:
        status = "inactive"
        reason = "missing_ratio"
    elif family == "hidden" and sessions < _HIDDEN_MIN_ACTIVE_SESSIONS:
        status = "watch_only"
        reason = "hidden_low_sample"
    elif sessions < _MIN_ACTIVE_SESSIONS:
        status = "watch_only"
        reason = "low_sample"
    elif over_rate is not None and over_rate >= _HIGH_OVER_RATE_GUARD:
        status = "inactive"
        reason = "high_over_guard"
    elif _NEUTRAL_RATIO_LOW <= ratio <= _NEUTRAL_RATIO_HIGH:
        status = "inactive"
        reason = "neutral_ratio"
    elif ratio > _NEUTRAL_RATIO_HIGH:
        systemic_under = (
            baseline_mae is not None
            and baseline_mae > 0.0
            and baseline_bias is not None
            and baseline_bias <= -_SYSTEMIC_UNDER_BIAS_RATIO * baseline_mae
        )
        if not systemic_under:
            status = "watch_only"
            reason = "not_systemic_under"
        else:
            shrink = min(
                _MAX_SHRINK,
                sessions / (sessions + _SHRINK_SESSION_HALF_LIFE),
            )
            scale = 1.0 + shrink * (ratio - 1.0)
            if p90 is not None and p90 < 0.95:
                scale = 1.0 + (scale - 1.0) * 0.5
                reason = "p90_low_guard"
            else:
                reason = "upward_prior_shift"
            scale = min(_MAX_UPWARD_SCALE, max(1.0, scale))
            status = "active_shadow" if scale > 1.0 else "inactive"
    elif ratio < _NEUTRAL_RATIO_LOW:
        status = "inactive"
        reason = "downward_shift_not_enabled"
        scale = _MAX_DOWNWARD_SCALE

    return PriorCalibrationEntry(
        map_id=int(map_id),
        map_name=str(map_name or ""),
        map_family=family,
        archive_sessions=sessions,
        prior_trials=max(0, int(prior_trials)),
        actual_raw_p50=_finite_float(actual_raw_p50),
        actual_raw_p90=_finite_float(actual_raw_p90),
        prior_raw_p50=_finite_float(prior_raw_p50),
        prior_raw_p90=_finite_float(prior_raw_p90),
        median_ratio=ratio,
        p90_ratio=p90,
        formal_p50_over_rate=over_rate,
        baseline_formal_p50_mae=baseline_mae,
        baseline_formal_p50_bias=baseline_bias,
        status=status,
        gate_reason=reason,
        scale=float(scale),
        source=str(source or "archive_prior_shadow"),
    )


def entry_from_mapping(row: Mapping[str, Any]) -> PriorCalibrationEntry:
    return PriorCalibrationEntry(
        map_id=int(row["map_id"]),
        map_name=str(row.get("map_name") or ""),
        map_family=str(row.get("map_family") or ""),
        archive_sessions=_optional_int(row.get("archive_sessions")),
        prior_trials=_optional_int(row.get("prior_trials")),
        actual_raw_p50=_finite_float(row.get("actual_raw_p50")),
        actual_raw_p90=_finite_float(row.get("actual_raw_p90")),
        prior_raw_p50=_finite_float(row.get("prior_raw_p50")),
        prior_raw_p90=_finite_float(row.get("prior_raw_p90")),
        median_ratio=_finite_float(row.get("median_ratio")),
        p90_ratio=_finite_float(row.get("p90_ratio")),
        formal_p50_over_rate=_finite_float(row.get("formal_p50_over_rate")),
        baseline_formal_p50_mae=_finite_float(
            row.get("baseline_formal_p50_mae")
        ),
        baseline_formal_p50_bias=_finite_float(
            row.get("baseline_formal_p50_bias")
        ),
        status=str(row.get("status") or "unscored"),
        gate_reason=str(row.get("gate_reason") or ""),
        scale=float(_finite_float(row.get("scale")) or 1.0),
        source=str(row.get("source") or "archive_prior_shadow"),
    )


def load_prior_calibration_entries(path: Path) -> dict[int, PriorCalibrationEntry]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    raw_entries = payload.get("entries", payload) if isinstance(payload, dict) else payload
    entries: dict[int, PriorCalibrationEntry] = {}
    for row in raw_entries or ():
        if not isinstance(row, Mapping):
            continue
        try:
            entry = entry_from_mapping(row)
        except (KeyError, TypeError, ValueError):
            continue
        entries[int(entry.map_id)] = entry
    return entries


def _scale_summary(
    summary: QuantileSummary | None,
    scale: float,
) -> QuantileSummary | None:
    if summary is None:
        return None
    return QuantileSummary(
        p10=summary.p10 * scale,
        p50=summary.p50 * scale,
        p90=summary.p90 * scale,
    )


def _calibrated_posterior(
    posterior: V3PosteriorReport,
    entry: PriorCalibrationEntry,
) -> V3PosteriorReport:
    scale = float(entry.scale) if entry.active else 1.0
    diagnostics = tuple(posterior.diagnostics) + (
        f"prior_calibration_status={entry.status}",
        f"prior_calibration_scale={scale:.6f}",
        f"prior_calibration_gate={entry.gate_reason}",
    )
    return V3PosteriorReport(
        map_id=posterior.map_id,
        map_name=posterior.map_name,
        n_total=posterior.n_total,
        n_matched=posterior.n_matched,
        n_strict_matched=posterior.n_strict_matched,
        match_scope=posterior.match_scope,
        q6_present_rate=posterior.q6_present_rate,
        total_cells=posterior.total_cells,
        total_value=_scale_summary(posterior.total_value, scale),
        formal_decision_value=_scale_summary(
            posterior.formal_decision_value,
            scale,
        ),
        tail_replacement_decision_value=_scale_summary(
            posterior.tail_replacement_decision_value,
            scale,
        ),
        q6_count=posterior.q6_count,
        q6_cells=posterior.q6_cells,
        q6_value=_scale_summary(posterior.q6_value, scale),
        q6_formal_decision_value=_scale_summary(
            posterior.q6_formal_decision_value,
            scale,
        ),
        q6_tail_replacement_decision_value=_scale_summary(
            posterior.q6_tail_replacement_decision_value,
            scale,
        ),
        diagnostics=diagnostics,
    )


def calibrate_posterior_report(
    posterior: V3PosteriorReport | None,
    entry: PriorCalibrationEntry | None,
) -> V3PriorCalibrationReport:
    if posterior is None:
        return V3PriorCalibrationReport(
            posterior=None,
            entry=entry,
            diagnostics=("missing_posterior",),
        )
    if entry is None:
        return V3PriorCalibrationReport(
            posterior=posterior,
            entry=None,
            diagnostics=("missing_calibration_entry",),
        )
    return V3PriorCalibrationReport(
        posterior=_calibrated_posterior(posterior, entry),
        entry=entry,
        diagnostics=(
            f"status={entry.status}",
            f"gate={entry.gate_reason}",
            f"scale={entry.scale:.6f}",
        ),
    )


def empty_prior_calibration_flat_dict(
    *,
    prefix: str = "v3_cal_",
) -> dict[str, Any]:
    return V3PriorCalibrationReport(
        posterior=None,
        entry=None,
        diagnostics=(),
    ).to_flat_dict(prefix=prefix)


__all__ = (
    "PriorCalibrationEntry",
    "V3PriorCalibrationReport",
    "calibrate_posterior_report",
    "empty_prior_calibration_flat_dict",
    "entry_from_mapping",
    "load_prior_calibration_entries",
    "propose_prior_calibration",
)
