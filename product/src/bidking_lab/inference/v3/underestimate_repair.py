"""Bounded underestimate-repair shadow for v3 posterior reports.

This module is diagnostic-only. It exposes hero/map-specific upshift candidates
as auditable shadow fields and never changes formal bid decisions.
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

_CANDIDATE_STATUSES = frozenset({"watch_only_upshift_candidate"})


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


def _normal_hero(value: Any) -> str:
    hero = str(value or "").strip().lower()
    return hero or "unknown"


@dataclass(frozen=True)
class UnderestimateRepairEntry:
    hero: str
    map_id: int
    map_name: str = ""
    map_family: str = ""
    archive_windows: int = 0
    archive_sessions: int = 0
    status: str = "unscored"
    gate_reason: str = ""
    scale: float = 1.0
    formal_p50_mae: float | None = None
    formal_p50_bias: float | None = None
    formal_p50_below_rate: float | None = None
    formal_p50_over_rate: float | None = None
    formal_p90_coverage: float | None = None
    scaled_delta_formal_p50_mae: float | None = None
    scaled_delta_q6_formal_p50_mae: float | None = None
    public_total_rate: float | None = None
    q6_floor_rate: float | None = None
    flags: tuple[str, ...] = ()
    source: str = "archive_underestimate_repair_shadow"

    @property
    def key(self) -> tuple[str, int]:
        return (_normal_hero(self.hero), int(self.map_id))

    @property
    def hero_map_id(self) -> str:
        return f"{_normal_hero(self.hero)}|{int(self.map_id)}"

    @property
    def candidate(self) -> bool:
        return self.status in _CANDIDATE_STATUSES and self.scale > 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "hero": _normal_hero(self.hero),
            "map_id": int(self.map_id),
            "map_name": self.map_name,
            "map_family": self.map_family,
            "archive_windows": self.archive_windows,
            "archive_sessions": self.archive_sessions,
            "status": self.status,
            "gate_reason": self.gate_reason,
            "scale": self.scale,
            "formal_p50_mae": self.formal_p50_mae,
            "formal_p50_bias": self.formal_p50_bias,
            "formal_p50_below_rate": self.formal_p50_below_rate,
            "formal_p50_over_rate": self.formal_p50_over_rate,
            "formal_p90_coverage": self.formal_p90_coverage,
            "scaled_delta_formal_p50_mae": self.scaled_delta_formal_p50_mae,
            "scaled_delta_q6_formal_p50_mae": (
                self.scaled_delta_q6_formal_p50_mae
            ),
            "public_total_rate": self.public_total_rate,
            "q6_floor_rate": self.q6_floor_rate,
            "flags": list(self.flags),
            "source": self.source,
        }


@dataclass(frozen=True)
class V3UnderestimateRepairReport:
    baseline: V3PosteriorReport | None
    entry: UnderestimateRepairEntry | None
    hero: str = "unknown"
    diagnostics: tuple[str, ...] = ()

    @property
    def candidate(self) -> bool:
        return (
            self.baseline is not None
            and self.baseline.ready
            and self.entry is not None
            and self.entry.candidate
        )

    @property
    def active(self) -> bool:
        return False

    @property
    def posterior(self) -> V3PosteriorReport | None:
        if self.baseline is None:
            return None
        if self.candidate and self.entry is not None:
            return _scaled_posterior(self.baseline, self.entry.scale, self.entry)
        return self.baseline

    def to_flat_dict(self, *, prefix: str = "v3_under_") -> dict[str, Any]:
        if self.posterior is None:
            out = empty_posterior_flat_dict(prefix=prefix)
        else:
            out = self.posterior.to_flat_dict(prefix=prefix)
        entry = self.entry
        out.update(
            {
                f"{prefix}active": self.active,
                f"{prefix}candidate": self.candidate,
                f"{prefix}status": entry.status if entry is not None else "missing_entry",
                f"{prefix}gate_reason": (
                    entry.gate_reason if entry is not None else "no_underestimate_entry"
                ),
                f"{prefix}scale": round(entry.scale, 6) if entry is not None else 1.0,
                f"{prefix}hero": _normal_hero(self.hero),
                f"{prefix}hero_map_id": (
                    entry.hero_map_id
                    if entry is not None
                    else f"{_normal_hero(self.hero)}|unknown"
                ),
                f"{prefix}source": (
                    "bounded_upshift" if self.candidate else "baseline"
                ),
                f"{prefix}entry_source": entry.source if entry is not None else None,
                f"{prefix}archive_windows": (
                    entry.archive_windows if entry is not None else None
                ),
                f"{prefix}archive_sessions": (
                    entry.archive_sessions if entry is not None else None
                ),
                f"{prefix}formal_p50_mae": (
                    round(entry.formal_p50_mae, 6)
                    if entry is not None and entry.formal_p50_mae is not None
                    else None
                ),
                f"{prefix}formal_p50_bias": (
                    round(entry.formal_p50_bias, 6)
                    if entry is not None and entry.formal_p50_bias is not None
                    else None
                ),
                f"{prefix}formal_p50_below_rate": (
                    round(entry.formal_p50_below_rate, 6)
                    if entry is not None
                    and entry.formal_p50_below_rate is not None
                    else None
                ),
                f"{prefix}formal_p50_over_rate": (
                    round(entry.formal_p50_over_rate, 6)
                    if entry is not None and entry.formal_p50_over_rate is not None
                    else None
                ),
                f"{prefix}formal_p90_coverage": (
                    round(entry.formal_p90_coverage, 6)
                    if entry is not None and entry.formal_p90_coverage is not None
                    else None
                ),
                f"{prefix}scaled_delta_formal_p50_mae": (
                    round(entry.scaled_delta_formal_p50_mae, 6)
                    if entry is not None
                    and entry.scaled_delta_formal_p50_mae is not None
                    else None
                ),
                f"{prefix}scaled_delta_q6_formal_p50_mae": (
                    round(entry.scaled_delta_q6_formal_p50_mae, 6)
                    if entry is not None
                    and entry.scaled_delta_q6_formal_p50_mae is not None
                    else None
                ),
                f"{prefix}public_total_rate": (
                    round(entry.public_total_rate, 6)
                    if entry is not None and entry.public_total_rate is not None
                    else None
                ),
                f"{prefix}q6_floor_rate": (
                    round(entry.q6_floor_rate, 6)
                    if entry is not None and entry.q6_floor_rate is not None
                    else None
                ),
                f"{prefix}flags": (
                    "+".join(entry.flags) if entry is not None else ""
                ),
                f"{prefix}diagnostics": ";".join(self.diagnostics),
            }
        )
        return out


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


def _scaled_posterior(
    posterior: V3PosteriorReport,
    scale: float,
    entry: UnderestimateRepairEntry,
) -> V3PosteriorReport:
    diagnostics = tuple(posterior.diagnostics) + (
        f"underestimate_repair_status={entry.status}",
        f"underestimate_repair_scale={scale:.6f}",
        f"underestimate_repair_gate={entry.gate_reason}",
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


def entry_from_mapping(row: Mapping[str, Any]) -> UnderestimateRepairEntry:
    raw_flags = row.get("flags") or ()
    raw_flag_values = raw_flags.split("+") if isinstance(raw_flags, str) else raw_flags
    flags = tuple(str(item) for item in raw_flag_values if str(item))
    return UnderestimateRepairEntry(
        hero=_normal_hero(row.get("hero") or str(row.get("group") or "").split("|", 1)[0]),
        map_id=int(row.get("map_id") or str(row.get("group") or "").split("|", 1)[-1]),
        map_name=str(row.get("map_name") or ""),
        map_family=str(row.get("map_family") or ""),
        archive_windows=_optional_int(row.get("archive_windows", row.get("n"))),
        archive_sessions=_optional_int(row.get("archive_sessions", row.get("sessions"))),
        status=str(row.get("status") or row.get("candidate_status") or "unscored"),
        gate_reason=str(row.get("gate_reason") or ""),
        scale=float(_finite_float(row.get("scale", row.get("proposed_scale"))) or 1.0),
        formal_p50_mae=_finite_float(row.get("formal_p50_mae")),
        formal_p50_bias=_finite_float(row.get("formal_p50_bias")),
        formal_p50_below_rate=_finite_float(row.get("formal_p50_below_rate")),
        formal_p50_over_rate=_finite_float(row.get("formal_p50_over_rate")),
        formal_p90_coverage=_finite_float(row.get("formal_p90_coverage")),
        scaled_delta_formal_p50_mae=_finite_float(
            row.get("scaled_delta_formal_p50_mae")
        ),
        scaled_delta_q6_formal_p50_mae=_finite_float(
            row.get("scaled_delta_q6_formal_p50_mae")
        ),
        public_total_rate=_finite_float(row.get("public_total_rate")),
        q6_floor_rate=_finite_float(row.get("q6_floor_rate")),
        flags=flags,
        source=str(row.get("source") or "archive_underestimate_repair_shadow"),
    )


def load_underestimate_repair_entries(
    path: Path,
) -> dict[tuple[str, int], UnderestimateRepairEntry]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    raw_entries = payload.get("entries", payload) if isinstance(payload, dict) else payload
    entries: dict[tuple[str, int], UnderestimateRepairEntry] = {}
    for row in raw_entries or ():
        if not isinstance(row, Mapping):
            continue
        try:
            entry = entry_from_mapping(row)
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        entries[entry.key] = entry
    return entries


def underestimate_entry_for(
    entries: Mapping[tuple[str, int], UnderestimateRepairEntry] | None,
    *,
    hero: str | None,
    map_id: int | None,
) -> UnderestimateRepairEntry | None:
    if entries is None or map_id is None:
        return None
    return entries.get((_normal_hero(hero), int(map_id)))


def repair_underestimate_posterior_report(
    posterior: V3PosteriorReport | None,
    entry: UnderestimateRepairEntry | None,
    *,
    hero: str | None = None,
) -> V3UnderestimateRepairReport:
    diagnostics: tuple[str, ...]
    if posterior is None:
        diagnostics = ("missing_posterior",)
    elif entry is None:
        diagnostics = ("missing_underestimate_entry",)
    elif not entry.candidate:
        diagnostics = (f"status={entry.status}", f"gate={entry.gate_reason}")
    else:
        diagnostics = (
            f"status={entry.status}",
            f"gate={entry.gate_reason}",
            f"scale={entry.scale:.6f}",
        )
    return V3UnderestimateRepairReport(
        baseline=posterior,
        entry=entry,
        hero=_normal_hero(hero or (entry.hero if entry is not None else "")),
        diagnostics=diagnostics,
    )


def empty_underestimate_repair_flat_dict(
    *,
    prefix: str = "v3_under_",
) -> dict[str, Any]:
    return V3UnderestimateRepairReport(
        baseline=None,
        entry=None,
        hero="unknown",
    ).to_flat_dict(prefix=prefix)


__all__ = (
    "UnderestimateRepairEntry",
    "V3UnderestimateRepairReport",
    "empty_underestimate_repair_flat_dict",
    "entry_from_mapping",
    "load_underestimate_repair_entries",
    "repair_underestimate_posterior_report",
    "underestimate_entry_for",
)
