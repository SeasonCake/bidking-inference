"""Tail/value review shadow for v3 posterior reports.

This module is diagnostic-only. It flags hero/map tail-review candidates and
hurt guards, but never changes formal bid decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping

from bidking_lab.inference.v3.posterior import (
    V3PosteriorReport,
    empty_posterior_flat_dict,
)

_CANDIDATE_STATUSES = frozenset(
    (
        "watch_only_q6_tail_value_candidate",
        "watch_only_tail_value_candidate",
    )
)
_HURT_STATUSES = frozenset(("blocked_tail_estimate_hurts",))


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


def _map_id_from_row(row: Mapping[str, Any]) -> int:
    raw = row.get("map_id")
    if raw not in (None, ""):
        return int(raw)
    return int(str(row.get("group") or "").split("|", 1)[-1])


@dataclass(frozen=True)
class TailValueReviewEntry:
    hero: str
    map_id: int
    map_name: str = ""
    map_family: str = ""
    archive_windows: int = 0
    archive_sessions: int = 0
    status: str = "unscored"
    gate_reason: str = ""
    tail_delta_p50_mae: float | None = None
    q6_tail_delta_p50_mae: float | None = None
    tail_p90_coverage: float | None = None
    q6_tail_p90_coverage: float | None = None
    public_total_rate: float | None = None
    q6_floor_rate: float | None = None
    flags: tuple[str, ...] = ()
    source: str = "archive_tail_value_review_shadow"

    @property
    def key(self) -> tuple[str, int]:
        return (_normal_hero(self.hero), int(self.map_id))

    @property
    def hero_map_id(self) -> str:
        return f"{_normal_hero(self.hero)}|{int(self.map_id)}"

    @property
    def candidate(self) -> bool:
        return self.status in _CANDIDATE_STATUSES and not self.hurt_guard

    @property
    def hurt_guard(self) -> bool:
        return self.status in _HURT_STATUSES or any(
            flag in self.flags
            for flag in (
                "tail_estimate_hurts_total",
                "tail_estimate_hurts_q6",
                "tail_holdout_hurts_total",
                "tail_holdout_hurts_q6",
            )
        )

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
            "tail_delta_p50_mae": self.tail_delta_p50_mae,
            "q6_tail_delta_p50_mae": self.q6_tail_delta_p50_mae,
            "tail_p90_coverage": self.tail_p90_coverage,
            "q6_tail_p90_coverage": self.q6_tail_p90_coverage,
            "public_total_rate": self.public_total_rate,
            "q6_floor_rate": self.q6_floor_rate,
            "flags": list(self.flags),
            "source": self.source,
        }


@dataclass(frozen=True)
class V3TailValueReviewReport:
    baseline: V3PosteriorReport | None
    entry: TailValueReviewEntry | None
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
    def hurt_guard(self) -> bool:
        return self.entry is not None and self.entry.hurt_guard

    @property
    def active(self) -> bool:
        return False

    def to_flat_dict(self, *, prefix: str = "v3_tail_review_") -> dict[str, Any]:
        if self.baseline is None:
            out = empty_posterior_flat_dict(prefix=prefix)
        else:
            out = self.baseline.to_flat_dict(prefix=prefix)
        entry = self.entry
        out.update(
            {
                f"{prefix}active": self.active,
                f"{prefix}candidate": self.candidate,
                f"{prefix}hurt_guard": self.hurt_guard,
                f"{prefix}status": entry.status if entry is not None else "missing_entry",
                f"{prefix}gate_reason": (
                    entry.gate_reason if entry is not None else "no_tail_review_entry"
                ),
                f"{prefix}hero": _normal_hero(self.hero),
                f"{prefix}hero_map_id": (
                    entry.hero_map_id
                    if entry is not None
                    else f"{_normal_hero(self.hero)}|unknown"
                ),
                f"{prefix}source": _report_source(self),
                f"{prefix}entry_source": entry.source if entry is not None else None,
                f"{prefix}archive_windows": (
                    entry.archive_windows if entry is not None else None
                ),
                f"{prefix}archive_sessions": (
                    entry.archive_sessions if entry is not None else None
                ),
                f"{prefix}tail_delta_p50_mae": (
                    round(entry.tail_delta_p50_mae, 6)
                    if entry is not None and entry.tail_delta_p50_mae is not None
                    else None
                ),
                f"{prefix}q6_tail_delta_p50_mae": (
                    round(entry.q6_tail_delta_p50_mae, 6)
                    if entry is not None and entry.q6_tail_delta_p50_mae is not None
                    else None
                ),
                f"{prefix}tail_p90_coverage": (
                    round(entry.tail_p90_coverage, 6)
                    if entry is not None and entry.tail_p90_coverage is not None
                    else None
                ),
                f"{prefix}q6_tail_p90_coverage": (
                    round(entry.q6_tail_p90_coverage, 6)
                    if entry is not None and entry.q6_tail_p90_coverage is not None
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


def _report_source(report: V3TailValueReviewReport) -> str:
    if report.hurt_guard:
        return "tail_value_hurt_guard"
    if report.candidate:
        return "tail_value_review_candidate"
    return "baseline"


def entry_from_mapping(row: Mapping[str, Any]) -> TailValueReviewEntry:
    raw_flags = row.get("flags") or ()
    raw_flag_values = raw_flags.split("+") if isinstance(raw_flags, str) else raw_flags
    flags = tuple(str(item) for item in raw_flag_values if str(item))
    return TailValueReviewEntry(
        hero=_normal_hero(row.get("hero") or str(row.get("group") or "").split("|", 1)[0]),
        map_id=_map_id_from_row(row),
        map_name=str(row.get("map_name") or ""),
        map_family=str(row.get("map_family") or ""),
        archive_windows=_optional_int(row.get("archive_windows", row.get("n"))),
        archive_sessions=_optional_int(row.get("archive_sessions", row.get("sessions"))),
        status=str(row.get("status") or row.get("candidate_status") or "unscored"),
        gate_reason=str(row.get("gate_reason") or ""),
        tail_delta_p50_mae=_finite_float(
            row.get("tail_delta_p50_mae", row.get("tail_delta"))
        ),
        q6_tail_delta_p50_mae=_finite_float(
            row.get("q6_tail_delta_p50_mae", row.get("q6_tail_delta"))
        ),
        tail_p90_coverage=_finite_float(row.get("tail_p90_coverage")),
        q6_tail_p90_coverage=_finite_float(row.get("q6_tail_p90_coverage")),
        public_total_rate=_finite_float(row.get("public_total_rate")),
        q6_floor_rate=_finite_float(row.get("q6_floor_rate")),
        flags=flags,
        source=str(row.get("source") or "archive_tail_value_review_shadow"),
    )


def load_tail_value_review_entries(
    path: Path,
) -> dict[tuple[str, int], TailValueReviewEntry]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    raw_entries = payload.get("entries", payload) if isinstance(payload, dict) else payload
    entries: dict[tuple[str, int], TailValueReviewEntry] = {}
    for row in raw_entries or ():
        if not isinstance(row, Mapping):
            continue
        try:
            entry = entry_from_mapping(row)
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        entries[entry.key] = entry
    return entries


def tail_value_review_entry_for(
    entries: Mapping[tuple[str, int], TailValueReviewEntry] | None,
    *,
    hero: str | None,
    map_id: int | None,
) -> TailValueReviewEntry | None:
    if entries is None or map_id is None:
        return None
    return entries.get((_normal_hero(hero), int(map_id)))


def review_tail_value_posterior_report(
    posterior: V3PosteriorReport | None,
    entry: TailValueReviewEntry | None,
    *,
    hero: str | None = None,
) -> V3TailValueReviewReport:
    diagnostics: tuple[str, ...]
    if posterior is None:
        diagnostics = ("missing_posterior",)
    elif entry is None:
        diagnostics = ("missing_tail_review_entry",)
    elif entry.hurt_guard:
        diagnostics = (f"status={entry.status}", f"gate={entry.gate_reason}")
    elif not entry.candidate:
        diagnostics = (f"status={entry.status}", f"gate={entry.gate_reason}")
    else:
        diagnostics = (
            f"status={entry.status}",
            f"gate={entry.gate_reason}",
            f"q6_tail_delta={entry.q6_tail_delta_p50_mae}",
        )
    return V3TailValueReviewReport(
        baseline=posterior,
        entry=entry,
        hero=_normal_hero(hero or (entry.hero if entry is not None else "")),
        diagnostics=diagnostics,
    )


def empty_tail_value_review_flat_dict(
    *,
    prefix: str = "v3_tail_review_",
) -> dict[str, Any]:
    return V3TailValueReviewReport(
        baseline=None,
        entry=None,
        hero="unknown",
    ).to_flat_dict(prefix=prefix)


__all__ = (
    "TailValueReviewEntry",
    "V3TailValueReviewReport",
    "empty_tail_value_review_flat_dict",
    "entry_from_mapping",
    "load_tail_value_review_entries",
    "review_tail_value_posterior_report",
    "tail_value_review_entry_for",
)
