"""Settlement occupancy count-prior shadow evidence for v3.

This module is diagnostic-only. It exposes cohort-gated settlement count
statistics as auditable shadow fields and never changes posterior samples or
formal bid decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping

from bidking_lab.inference.v3.summary import FeasibleSummaryReport

_CANDIDATE_STATUSES = frozenset({"observed_exceeds_table_caps_shadow_only"})
_MISSING_TABLE_STATUSES = frozenset({"missing_table_shadow_only"})


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


def _round(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None else None


def _summary_value(row: Mapping[str, Any], field: str, key: str) -> float | None:
    value = row.get(field)
    if isinstance(value, Mapping):
        return _finite_float(value.get(key))
    return _finite_float(row.get(f"{field}_{key}"))


def _source_and_target(
    *,
    exact: Any,
    floor: Any,
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


@dataclass(frozen=True)
class SettlementCountPriorEntry:
    scope: str
    group: str
    status: str
    gate_reason: str = ""
    source: str = "archive_settlement_count_prior_shadow"
    archive_sessions: int = 0
    inventory_count_p50: float | None = None
    inventory_count_p90: float | None = None
    inventory_count_p95: float | None = None
    inventory_count_max: float | None = None
    non_temp_inventory_count_p50: float | None = None
    non_temp_inventory_count_p90: float | None = None
    non_temp_inventory_count_p95: float | None = None
    non_temp_inventory_count_max: float | None = None
    known_temp_zodiac_count_max: float | None = None
    bidmap_items_per_session_max: float | None = None
    bidmap_raw_round_cap_max: float | None = None
    above_drop_ref_after_temp_zodiac_rows: int = 0
    above_round_cap_after_temp_zodiac_rows: int = 0
    payload_inventory_mismatch_rows: int = 0
    missing_table_rows: int = 0

    @property
    def key(self) -> tuple[str, str]:
        return (str(self.scope), str(self.group))

    @property
    def candidate(self) -> bool:
        return self.status in _CANDIDATE_STATUSES

    @property
    def missing_table(self) -> bool:
        return self.status in _MISSING_TABLE_STATUSES or self.missing_table_rows > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "group": self.group,
            "status": self.status,
            "gate_reason": self.gate_reason,
            "source": self.source,
            "archive_sessions": self.archive_sessions,
            "inventory_count_p50": self.inventory_count_p50,
            "inventory_count_p90": self.inventory_count_p90,
            "inventory_count_p95": self.inventory_count_p95,
            "inventory_count_max": self.inventory_count_max,
            "non_temp_inventory_count_p50": self.non_temp_inventory_count_p50,
            "non_temp_inventory_count_p90": self.non_temp_inventory_count_p90,
            "non_temp_inventory_count_p95": self.non_temp_inventory_count_p95,
            "non_temp_inventory_count_max": self.non_temp_inventory_count_max,
            "known_temp_zodiac_count_max": self.known_temp_zodiac_count_max,
            "bidmap_items_per_session_max": self.bidmap_items_per_session_max,
            "bidmap_raw_round_cap_max": self.bidmap_raw_round_cap_max,
            "above_drop_ref_after_temp_zodiac_rows": (
                self.above_drop_ref_after_temp_zodiac_rows
            ),
            "above_round_cap_after_temp_zodiac_rows": (
                self.above_round_cap_after_temp_zodiac_rows
            ),
            "payload_inventory_mismatch_rows": self.payload_inventory_mismatch_rows,
            "missing_table_rows": self.missing_table_rows,
        }


@dataclass(frozen=True)
class V3SettlementCountPriorReport:
    entry: SettlementCountPriorEntry | None
    map_id: int | None = None
    summary: FeasibleSummaryReport | None = None
    prior_fields: Mapping[str, Any] | None = None
    diagnostics: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return True

    @property
    def ready(self) -> bool:
        return self.entry is not None

    @property
    def active(self) -> bool:
        return False

    @property
    def affects_bid(self) -> bool:
        return False

    @property
    def candidate(self) -> bool:
        return self.entry is not None and self.entry.candidate

    @property
    def missing_table(self) -> bool:
        return self.entry is not None and self.entry.missing_table

    @property
    def status(self) -> str:
        return self.entry.status if self.entry is not None else "missing_entry"

    @property
    def gate_reason(self) -> str:
        if self.entry is None:
            return "no_settlement_count_prior_entry"
        if self.entry.gate_reason:
            return self.entry.gate_reason
        if self.entry.candidate:
            return "observed_settlement_count_exceeds_current_table_caps"
        if self.entry.missing_table:
            return "missing_current_bidmap_for_activity_cohort"
        return "settlement_count_prior_shadow_only"

    def _target_count(self) -> tuple[str, float | None]:
        if self.summary is None:
            return ("none", None)
        return _source_and_target(
            exact=self.summary.session_total_count_exact,
            floor=self.summary.known_count_floor,
        )

    def to_flat_dict(self, *, prefix: str = "v3_scp_") -> dict[str, Any]:
        entry = self.entry
        target_source, target = self._target_count()
        prior_max = _finite_float(
            (self.prior_fields or {}).get("v3_prior_items_per_session_max")
        )
        observed_p95 = (
            entry.non_temp_inventory_count_p95 if entry is not None else None
        )
        observed_max = (
            entry.non_temp_inventory_count_max if entry is not None else None
        )
        flags: list[str] = []
        if entry is None:
            flags.append("missing_entry")
        elif entry.candidate:
            flags.append("observed_exceeds_table_caps")
        if entry is not None and entry.missing_table:
            flags.append("missing_bidmap")
        if prior_max is not None and observed_p95 is not None and observed_p95 > prior_max:
            flags.append("observed_p95_above_prior_max")
        if prior_max is not None and observed_max is not None and observed_max > prior_max:
            flags.append("observed_max_above_prior_max")
        if target is not None and observed_p95 is not None and target < observed_p95:
            flags.append("target_below_observed_p95")
        return {
            f"{prefix}available": self.available,
            f"{prefix}ready": self.ready,
            f"{prefix}affects_bid": self.affects_bid,
            f"{prefix}active": self.active,
            f"{prefix}candidate": self.candidate,
            f"{prefix}missing_table": self.missing_table,
            f"{prefix}status": self.status,
            f"{prefix}gate_reason": self.gate_reason,
            f"{prefix}scope": entry.scope if entry is not None else None,
            f"{prefix}group": entry.group if entry is not None else None,
            f"{prefix}source": entry.source if entry is not None else None,
            f"{prefix}archive_sessions": (
                entry.archive_sessions if entry is not None else None
            ),
            f"{prefix}inventory_count_p50": (
                _round(entry.inventory_count_p50) if entry is not None else None
            ),
            f"{prefix}inventory_count_p90": (
                _round(entry.inventory_count_p90) if entry is not None else None
            ),
            f"{prefix}inventory_count_p95": (
                _round(entry.inventory_count_p95) if entry is not None else None
            ),
            f"{prefix}inventory_count_max": (
                _round(entry.inventory_count_max) if entry is not None else None
            ),
            f"{prefix}non_temp_inventory_count_p50": (
                _round(entry.non_temp_inventory_count_p50)
                if entry is not None
                else None
            ),
            f"{prefix}non_temp_inventory_count_p90": (
                _round(entry.non_temp_inventory_count_p90)
                if entry is not None
                else None
            ),
            f"{prefix}non_temp_inventory_count_p95": (
                _round(entry.non_temp_inventory_count_p95)
                if entry is not None
                else None
            ),
            f"{prefix}non_temp_inventory_count_max": (
                _round(entry.non_temp_inventory_count_max)
                if entry is not None
                else None
            ),
            f"{prefix}known_temp_zodiac_count_max": (
                _round(entry.known_temp_zodiac_count_max)
                if entry is not None
                else None
            ),
            f"{prefix}bidmap_items_per_session_max": (
                _round(entry.bidmap_items_per_session_max)
                if entry is not None
                else None
            ),
            f"{prefix}bidmap_raw_round_cap_max": (
                _round(entry.bidmap_raw_round_cap_max)
                if entry is not None
                else None
            ),
            f"{prefix}above_drop_after_temp_zodiac_rows": (
                entry.above_drop_ref_after_temp_zodiac_rows
                if entry is not None
                else None
            ),
            f"{prefix}above_round_after_temp_zodiac_rows": (
                entry.above_round_cap_after_temp_zodiac_rows
                if entry is not None
                else None
            ),
            f"{prefix}payload_inventory_mismatch_rows": (
                entry.payload_inventory_mismatch_rows
                if entry is not None
                else None
            ),
            f"{prefix}missing_table_rows": (
                entry.missing_table_rows if entry is not None else None
            ),
            f"{prefix}target_count_source": target_source,
            f"{prefix}target_count": _round(target),
            f"{prefix}prior_items_per_session_max": _round(prior_max),
            f"{prefix}target_to_observed_p95_delta": _round(
                target - observed_p95
                if target is not None and observed_p95 is not None
                else None
            ),
            f"{prefix}prior_max_to_observed_p95_delta": _round(
                prior_max - observed_p95
                if prior_max is not None and observed_p95 is not None
                else None
            ),
            f"{prefix}prior_max_to_observed_max_delta": _round(
                prior_max - observed_max
                if prior_max is not None and observed_max is not None
                else None
            ),
            f"{prefix}flags": "+".join(flags),
            f"{prefix}diagnostics": ";".join(self.diagnostics),
        }


def entry_from_mapping(row: Mapping[str, Any]) -> SettlementCountPriorEntry:
    return SettlementCountPriorEntry(
        scope=str(row.get("scope") or row.get("group_by") or "map_id"),
        group=str(row.get("group")),
        status=str(row.get("status") or row.get("candidate_status") or "unscored"),
        gate_reason=str(row.get("gate_reason") or ""),
        source=str(row.get("source") or "archive_settlement_count_prior_shadow"),
        archive_sessions=_optional_int(row.get("archive_sessions", row.get("files"))),
        inventory_count_p50=_summary_value(row, "inventory_count", "p50"),
        inventory_count_p90=_summary_value(row, "inventory_count", "p90"),
        inventory_count_p95=_summary_value(row, "inventory_count", "p95"),
        inventory_count_max=_summary_value(row, "inventory_count", "max"),
        non_temp_inventory_count_p50=_summary_value(
            row,
            "non_temp_inventory_count",
            "p50",
        ),
        non_temp_inventory_count_p90=_summary_value(
            row,
            "non_temp_inventory_count",
            "p90",
        ),
        non_temp_inventory_count_p95=_summary_value(
            row,
            "non_temp_inventory_count",
            "p95",
        ),
        non_temp_inventory_count_max=_summary_value(
            row,
            "non_temp_inventory_count",
            "max",
        ),
        known_temp_zodiac_count_max=_summary_value(
            row,
            "known_temp_zodiac_count",
            "max",
        ),
        bidmap_items_per_session_max=_summary_value(
            row,
            "bidmap_items_per_session_max",
            "max",
        ),
        bidmap_raw_round_cap_max=_summary_value(
            row,
            "bidmap_raw_round_cap_max",
            "max",
        ),
        above_drop_ref_after_temp_zodiac_rows=_optional_int(
            row.get("above_drop_ref_after_temp_zodiac_rows")
        ),
        above_round_cap_after_temp_zodiac_rows=_optional_int(
            row.get("above_round_cap_after_temp_zodiac_rows")
        ),
        payload_inventory_mismatch_rows=_optional_int(
            row.get("payload_inventory_mismatch_rows")
        ),
        missing_table_rows=_optional_int(row.get("missing_table_rows")),
    )


def load_settlement_count_prior_entries(
    path: Path,
) -> dict[tuple[str, str], SettlementCountPriorEntry]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    raw_entries = payload.get("entries", payload) if isinstance(payload, dict) else payload
    entries: dict[tuple[str, str], SettlementCountPriorEntry] = {}
    for row in raw_entries or ():
        if not isinstance(row, Mapping):
            continue
        try:
            entry = entry_from_mapping(row)
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        entries[entry.key] = entry
    return entries


def settlement_count_prior_entry_for(
    entries: Mapping[tuple[str, str], SettlementCountPriorEntry] | None,
    *,
    map_id: int | None,
) -> SettlementCountPriorEntry | None:
    if entries is None or map_id is None:
        return None
    exact = entries.get(("map_id", str(int(map_id))))
    if exact is not None:
        return exact
    return entries.get(("map_prefix3", str(int(map_id) // 10)))


def assess_settlement_count_prior(
    *,
    entry: SettlementCountPriorEntry | None,
    map_id: int | None,
    summary: FeasibleSummaryReport | None,
    prior_fields: Mapping[str, Any] | None = None,
) -> V3SettlementCountPriorReport:
    diagnostics: tuple[str, ...]
    if entry is None:
        diagnostics = ("missing_settlement_count_prior_entry",)
    else:
        diagnostics = (
            f"status={entry.status}",
            f"scope={entry.scope}",
            f"group={entry.group}",
            f"candidate={entry.candidate}",
        )
    return V3SettlementCountPriorReport(
        entry=entry,
        map_id=map_id,
        summary=summary,
        prior_fields=prior_fields,
        diagnostics=diagnostics,
    )


def empty_settlement_count_prior_flat_dict(
    *,
    prefix: str = "v3_scp_",
) -> dict[str, Any]:
    return V3SettlementCountPriorReport(
        entry=None,
        map_id=None,
        summary=None,
        prior_fields=None,
    ).to_flat_dict(prefix=prefix)


__all__ = (
    "SettlementCountPriorEntry",
    "V3SettlementCountPriorReport",
    "assess_settlement_count_prior",
    "empty_settlement_count_prior_flat_dict",
    "entry_from_mapping",
    "load_settlement_count_prior_entries",
    "settlement_count_prior_entry_for",
)
