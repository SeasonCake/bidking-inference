"""Capacity/source expansion shadow evidence for v3.

This module is diagnostic-only. It turns settlement source-semantics audit
rows into auditable shadow fields and never changes posterior samples or bid
decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping

from bidking_lab.inference.v3.summary import FeasibleSummaryReport

_CANDIDATE_STATUSES = frozenset({"watch_capacity_source_expansion_shadow_only"})


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


def _count_value(counts: Mapping[str, Any] | None, key: str) -> int:
    if not isinstance(counts, Mapping):
        return 0
    return _optional_int(counts.get(key))


def _count_text(counts: Mapping[str, Any] | None) -> str:
    if not isinstance(counts, Mapping):
        return ""
    pairs = [
        (str(key), _optional_int(value))
        for key, value in counts.items()
        if _optional_int(value) > 0
    ]
    return ",".join(f"{key}:{value}" for key, value in sorted(pairs))


def _counts_text(row: Mapping[str, Any], field: str, text_field: str) -> str:
    text = row.get(text_field)
    if text not in (None, ""):
        return str(text)
    value = row.get(field)
    if isinstance(value, str):
        return value
    return _count_text(value if isinstance(value, Mapping) else None)


def _field_or_summary(
    row: Mapping[str, Any],
    compact_field: str,
    summary_field: str,
    key: str,
) -> float | None:
    compact = _finite_float(row.get(compact_field))
    if compact is not None:
        return compact
    return _summary_value(row, summary_field, key)


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
class CapacitySourceExpansionEntry:
    scope: str
    group: str
    status: str
    gate_reason: str = ""
    source: str = "archive_capacity_source_expansion_shadow"
    archive_sessions: int = 0
    mechanism_classes: str = ""
    source_evidence_classes: str = ""
    source_context_classes: str = ""
    unique_round_overflow_rows: int = 0
    server_side_expansion_rows: int = 0
    session_capacity_source_semantics_rows: int = 0
    public_total_match_rows: int = 0
    full_action_rows: int = 0
    direct_full_action_rows: int = 0
    payload_verified_only_rows: int = 0
    payload_inventory_mismatch_rows: int = 0
    non_zodiac_missing_max: float | None = None
    unique_non_temp_p50: float | None = None
    unique_non_temp_p90: float | None = None
    unique_non_temp_p95: float | None = None
    unique_non_temp_max: float | None = None
    unique_round_excess_p50: float | None = None
    unique_round_excess_p90: float | None = None
    unique_round_excess_p95: float | None = None
    unique_round_excess_max: float | None = None
    bidmap_items_per_session_max: float | None = None
    bidmap_raw_round_cap_max: float | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (str(self.scope), str(self.group))

    @property
    def candidate(self) -> bool:
        return self.status in _CANDIDATE_STATUSES

    @property
    def has_external_source_confirmation(self) -> bool:
        return self.public_total_match_rows > 0 or self.full_action_rows > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "group": self.group,
            "status": self.status,
            "gate_reason": self.gate_reason,
            "source": self.source,
            "archive_sessions": self.archive_sessions,
            "mechanism_classes": self.mechanism_classes,
            "source_evidence_classes": self.source_evidence_classes,
            "source_context_classes": self.source_context_classes,
            "unique_round_overflow_rows": self.unique_round_overflow_rows,
            "server_side_expansion_rows": self.server_side_expansion_rows,
            "session_capacity_source_semantics_rows": (
                self.session_capacity_source_semantics_rows
            ),
            "public_total_match_rows": self.public_total_match_rows,
            "full_action_rows": self.full_action_rows,
            "direct_full_action_rows": self.direct_full_action_rows,
            "payload_verified_only_rows": self.payload_verified_only_rows,
            "payload_inventory_mismatch_rows": self.payload_inventory_mismatch_rows,
            "non_zodiac_missing_max": self.non_zodiac_missing_max,
            "unique_non_temp_p50": self.unique_non_temp_p50,
            "unique_non_temp_p90": self.unique_non_temp_p90,
            "unique_non_temp_p95": self.unique_non_temp_p95,
            "unique_non_temp_max": self.unique_non_temp_max,
            "unique_round_excess_p50": self.unique_round_excess_p50,
            "unique_round_excess_p90": self.unique_round_excess_p90,
            "unique_round_excess_p95": self.unique_round_excess_p95,
            "unique_round_excess_max": self.unique_round_excess_max,
            "bidmap_items_per_session_max": self.bidmap_items_per_session_max,
            "bidmap_raw_round_cap_max": self.bidmap_raw_round_cap_max,
        }


@dataclass(frozen=True)
class V3CapacitySourceExpansionReport:
    entry: CapacitySourceExpansionEntry | None
    map_id: int | None = None
    map_family: str | None = None
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
    def status(self) -> str:
        return self.entry.status if self.entry is not None else "missing_entry"

    @property
    def gate_reason(self) -> str:
        if self.entry is None:
            return "no_capacity_source_expansion_entry"
        if self.entry.gate_reason:
            return self.entry.gate_reason
        if self.entry.candidate:
            return "observed_unique_round_over_cap_source_expansion"
        return "capacity_source_expansion_shadow_only"

    def _target_count(self) -> tuple[str, float | None]:
        if self.summary is None:
            return ("none", None)
        return _source_and_target(
            exact=self.summary.session_total_count_exact,
            floor=self.summary.known_count_floor,
        )

    def to_flat_dict(self, *, prefix: str = "v3_cse_") -> dict[str, Any]:
        entry = self.entry
        target_source, target = self._target_count()
        prior_max = _finite_float(
            (self.prior_fields or {}).get("v3_prior_items_per_session_max")
        )
        observed_p95 = entry.unique_non_temp_p95 if entry is not None else None
        observed_max = entry.unique_non_temp_max if entry is not None else None
        target_prior_delta = (
            target - prior_max
            if target is not None and prior_max is not None
            else None
        )
        pressure_candidate = (
            self.candidate
            and target_prior_delta is not None
            and target_prior_delta > 0.0
        )
        flags: list[str] = []
        if entry is None:
            flags.append("missing_entry")
        elif entry.candidate:
            flags.append("source_expansion_candidate")
        if pressure_candidate:
            flags.append("target_count_above_prior_max")
        if entry is not None and entry.has_external_source_confirmation:
            flags.append("external_source_confirmation")
        if (
            entry is not None
            and entry.session_capacity_source_semantics_rows > 0
            and entry.server_side_expansion_rows == 0
        ):
            flags.append("payload_only_source_semantics")
        if entry is not None and (entry.non_zodiac_missing_max or 0.0) > 0.0:
            flags.append("non_zodiac_drop_universe_gap")
        if target is not None and observed_p95 is not None and target < observed_p95:
            flags.append("target_below_source_expansion_p95")
        if prior_max is not None and observed_p95 is not None and prior_max < observed_p95:
            flags.append("prior_max_below_source_expansion_p95")
        if prior_max is not None and observed_max is not None and prior_max < observed_max:
            flags.append("prior_max_below_source_expansion_max")
        return {
            f"{prefix}available": self.available,
            f"{prefix}ready": self.ready,
            f"{prefix}affects_bid": self.affects_bid,
            f"{prefix}active": self.active,
            f"{prefix}candidate": self.candidate,
            f"{prefix}pressure_candidate": pressure_candidate,
            f"{prefix}status": self.status,
            f"{prefix}gate_reason": self.gate_reason,
            f"{prefix}scope": entry.scope if entry is not None else None,
            f"{prefix}group": entry.group if entry is not None else None,
            f"{prefix}source": entry.source if entry is not None else None,
            f"{prefix}archive_sessions": (
                entry.archive_sessions if entry is not None else None
            ),
            f"{prefix}mechanism_classes": (
                entry.mechanism_classes if entry is not None else None
            ),
            f"{prefix}source_evidence_classes": (
                entry.source_evidence_classes if entry is not None else None
            ),
            f"{prefix}source_context_classes": (
                entry.source_context_classes if entry is not None else None
            ),
            f"{prefix}unique_round_overflow_rows": (
                entry.unique_round_overflow_rows if entry is not None else None
            ),
            f"{prefix}server_side_expansion_rows": (
                entry.server_side_expansion_rows if entry is not None else None
            ),
            f"{prefix}session_capacity_source_semantics_rows": (
                entry.session_capacity_source_semantics_rows
                if entry is not None
                else None
            ),
            f"{prefix}public_total_match_rows": (
                entry.public_total_match_rows if entry is not None else None
            ),
            f"{prefix}full_action_rows": (
                entry.full_action_rows if entry is not None else None
            ),
            f"{prefix}direct_full_action_rows": (
                entry.direct_full_action_rows if entry is not None else None
            ),
            f"{prefix}payload_verified_only_rows": (
                entry.payload_verified_only_rows if entry is not None else None
            ),
            f"{prefix}payload_inventory_mismatch_rows": (
                entry.payload_inventory_mismatch_rows if entry is not None else None
            ),
            f"{prefix}non_zodiac_missing_max": (
                _round(entry.non_zodiac_missing_max) if entry is not None else None
            ),
            f"{prefix}unique_non_temp_p50": (
                _round(entry.unique_non_temp_p50) if entry is not None else None
            ),
            f"{prefix}unique_non_temp_p90": (
                _round(entry.unique_non_temp_p90) if entry is not None else None
            ),
            f"{prefix}unique_non_temp_p95": (
                _round(entry.unique_non_temp_p95) if entry is not None else None
            ),
            f"{prefix}unique_non_temp_max": (
                _round(entry.unique_non_temp_max) if entry is not None else None
            ),
            f"{prefix}unique_round_excess_p95": (
                _round(entry.unique_round_excess_p95) if entry is not None else None
            ),
            f"{prefix}unique_round_excess_max": (
                _round(entry.unique_round_excess_max) if entry is not None else None
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
            f"{prefix}target_count_source": target_source,
            f"{prefix}target_count": _round(target),
            f"{prefix}prior_items_per_session_max": _round(prior_max),
            f"{prefix}target_prior_max_delta": _round(target_prior_delta),
            f"{prefix}target_to_unique_non_temp_p95_delta": _round(
                target - observed_p95
                if target is not None and observed_p95 is not None
                else None
            ),
            f"{prefix}prior_max_to_unique_non_temp_p95_delta": _round(
                prior_max - observed_p95
                if prior_max is not None and observed_p95 is not None
                else None
            ),
            f"{prefix}prior_max_to_unique_non_temp_max_delta": _round(
                prior_max - observed_max
                if prior_max is not None and observed_max is not None
                else None
            ),
            f"{prefix}flags": "+".join(flags),
            f"{prefix}diagnostics": ";".join(self.diagnostics),
        }


def _status_for_row(row: Mapping[str, Any]) -> str:
    if _optional_int(row.get("unique_above_round_after_temp_zodiac_rows")) > 0:
        return "watch_capacity_source_expansion_shadow_only"
    if _optional_int(row.get("payload_inventory_mismatch_rows")) > 0:
        return "blocked_payload_mismatch_shadow_only"
    if (
        _summary_value(row, "non_zodiac_missing_from_drop_universe_count", "max")
        or 0.0
    ) > 0.0:
        return "blocked_drop_universe_gap_shadow_only"
    return "within_capacity_source_semantics_shadow_only"


def _gate_reason_for_status(status: str) -> str:
    if status == "watch_capacity_source_expansion_shadow_only":
        return "observed_unique_round_over_cap_source_expansion"
    if status == "blocked_payload_mismatch_shadow_only":
        return "settlement_payload_inventory_mismatch"
    if status == "blocked_drop_universe_gap_shadow_only":
        return "non_zodiac_drop_universe_gap"
    return "capacity_source_expansion_shadow_only"


def entry_from_mapping(row: Mapping[str, Any]) -> CapacitySourceExpansionEntry:
    mechanism_counts = row.get("mechanism_classes")
    evidence_counts = row.get("source_evidence_classes")
    status = str(row.get("status") or _status_for_row(row))
    return CapacitySourceExpansionEntry(
        scope=str(row.get("scope") or row.get("group_by") or "map_id"),
        group=str(row.get("group")),
        status=status,
        gate_reason=str(row.get("gate_reason") or _gate_reason_for_status(status)),
        source=str(row.get("source") or "archive_capacity_source_expansion_shadow"),
        archive_sessions=_optional_int(row.get("archive_sessions", row.get("files"))),
        mechanism_classes=_counts_text(
            row,
            "mechanism_classes",
            "mechanism_classes_text",
        ),
        source_evidence_classes=_counts_text(
            row,
            "source_evidence_classes",
            "source_evidence_classes_text",
        ),
        source_context_classes=_counts_text(
            row,
            "source_context_classes",
            "source_context_classes_text",
        ),
        unique_round_overflow_rows=_optional_int(
            row.get(
                "unique_round_overflow_rows",
                row.get("unique_above_round_after_temp_zodiac_rows"),
            )
        ),
        server_side_expansion_rows=_optional_int(
            row.get(
                "server_side_expansion_rows",
                _count_value(mechanism_counts, "server_side_settlement_expansion"),
            )
        ),
        session_capacity_source_semantics_rows=_optional_int(
            row.get(
                "session_capacity_source_semantics_rows",
                _count_value(mechanism_counts, "session_capacity_source_semantics"),
            )
        ),
        public_total_match_rows=_optional_int(
            row.get("public_total_match_rows", row.get("event_public_total_match_rows"))
        ),
        full_action_rows=_optional_int(
            row.get("full_action_rows", row.get("event_full_action_rows"))
        ),
        direct_full_action_rows=_optional_int(
            row.get(
                "direct_full_action_rows",
                row.get("event_direct_full_action_rows"),
            )
        ),
        payload_verified_only_rows=_optional_int(
            row.get(
                "payload_verified_only_rows",
                _count_value(evidence_counts, "settlement_payload_verified_only"),
            )
        ),
        payload_inventory_mismatch_rows=_optional_int(
            row.get("payload_inventory_mismatch_rows")
        ),
        non_zodiac_missing_max=_field_or_summary(
            row,
            "non_zodiac_missing_max",
            "non_zodiac_missing_from_drop_universe_count",
            "max",
        ),
        unique_non_temp_p50=_field_or_summary(
            row,
            "unique_non_temp_p50",
            "unique_non_temp_item_id_count",
            "p50",
        ),
        unique_non_temp_p90=_field_or_summary(
            row,
            "unique_non_temp_p90",
            "unique_non_temp_item_id_count",
            "p90",
        ),
        unique_non_temp_p95=_field_or_summary(
            row,
            "unique_non_temp_p95",
            "unique_non_temp_item_id_count",
            "p95",
        ),
        unique_non_temp_max=_field_or_summary(
            row,
            "unique_non_temp_max",
            "unique_non_temp_item_id_count",
            "max",
        ),
        unique_round_excess_p50=_field_or_summary(
            row,
            "unique_round_excess_p50",
            "unique_round_cap_excess_after_temp_zodiac_count",
            "p50",
        ),
        unique_round_excess_p90=_field_or_summary(
            row,
            "unique_round_excess_p90",
            "unique_round_cap_excess_after_temp_zodiac_count",
            "p90",
        ),
        unique_round_excess_p95=_field_or_summary(
            row,
            "unique_round_excess_p95",
            "unique_round_cap_excess_after_temp_zodiac_count",
            "p95",
        ),
        unique_round_excess_max=_field_or_summary(
            row,
            "unique_round_excess_max",
            "unique_round_cap_excess_after_temp_zodiac_count",
            "max",
        ),
        bidmap_items_per_session_max=_field_or_summary(
            row,
            "bidmap_items_per_session_max",
            "bidmap_items_per_session_max",
            "max",
        ),
        bidmap_raw_round_cap_max=_field_or_summary(
            row,
            "bidmap_raw_round_cap_max",
            "bidmap_raw_round_cap_max",
            "max",
        ),
    )


def load_capacity_source_expansion_entries(
    path: Path,
) -> dict[tuple[str, str], CapacitySourceExpansionEntry]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    raw_entries = payload.get("entries", payload) if isinstance(payload, dict) else payload
    entries: dict[tuple[str, str], CapacitySourceExpansionEntry] = {}
    for row in raw_entries or ():
        if not isinstance(row, Mapping):
            continue
        try:
            entry = entry_from_mapping(row)
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        entries[entry.key] = entry
    return entries


def capacity_source_expansion_entry_for(
    entries: Mapping[tuple[str, str], CapacitySourceExpansionEntry] | None,
    *,
    map_id: int | None,
    map_family: str | None = None,
) -> CapacitySourceExpansionEntry | None:
    if entries is None:
        return None
    if map_id is not None:
        exact = entries.get(("map_id", str(int(map_id))))
        if exact is not None:
            return exact
    if map_family:
        family = entries.get(("map_family", str(map_family)))
        if family is not None:
            return family
    return None


def assess_capacity_source_expansion(
    *,
    entry: CapacitySourceExpansionEntry | None,
    map_id: int | None,
    map_family: str | None,
    summary: FeasibleSummaryReport | None,
    prior_fields: Mapping[str, Any] | None = None,
) -> V3CapacitySourceExpansionReport:
    if entry is None:
        diagnostics = ("missing_capacity_source_expansion_entry",)
    else:
        diagnostics = (
            f"status={entry.status}",
            f"scope={entry.scope}",
            f"group={entry.group}",
            f"candidate={entry.candidate}",
        )
    return V3CapacitySourceExpansionReport(
        entry=entry,
        map_id=map_id,
        map_family=map_family,
        summary=summary,
        prior_fields=prior_fields,
        diagnostics=diagnostics,
    )


def empty_capacity_source_expansion_flat_dict(
    *,
    prefix: str = "v3_cse_",
) -> dict[str, Any]:
    return V3CapacitySourceExpansionReport(
        entry=None,
        map_id=None,
        map_family=None,
        summary=None,
        prior_fields=None,
    ).to_flat_dict(prefix=prefix)


__all__ = (
    "CapacitySourceExpansionEntry",
    "V3CapacitySourceExpansionReport",
    "assess_capacity_source_expansion",
    "capacity_source_expansion_entry_for",
    "empty_capacity_source_expansion_flat_dict",
    "entry_from_mapping",
    "load_capacity_source_expansion_entries",
)
