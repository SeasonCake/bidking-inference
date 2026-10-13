"""Audit settlement source semantics for the v3 capacity blocker."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from bidking_lab.live.fatbeans import parse_fatbeans_capture  # noqa: E402
from bidking_lab.live.monitor import load_monitor_tables  # noqa: E402

import summarize_v3_settlement_count_prior_candidates as scp  # noqa: E402


PUBLIC_TOTAL_INFO_ID = 200017
DEFAULT_GROUP_BY = "unique_residual_mode"
GROUP_BY_CHOICES = (
    "unique_residual_mode",
    "map_id",
    "map_family",
    "capture_day",
    "session_token_prefix6",
    "bidmap_rounds_total",
    "source_evidence_class",
    "source_context_class",
    "mechanism_class",
)
DETAIL_FIELDS = (
    "file",
    "path",
    "map_id",
    "map_family",
    "capture_day",
    "session_token_prefix6",
    "bidmap_rounds_total",
    "bidmap_sub_pool_kind",
    "inventory_count",
    "non_temp_inventory_count",
    "unique_non_temp_item_id_count",
    "bidmap_items_per_session_max",
    "bidmap_raw_round_cap_max",
    "non_zodiac_missing_from_drop_universe_count",
    "unique_drop_ref_excess_after_temp_zodiac_count",
    "unique_round_cap_excess_after_temp_zodiac_count",
    "raw_candidate_inventory_delta",
    "occupied_slot_inventory_delta",
    "unique_residual_mode",
    "source_evidence_class",
    "source_context_class",
    "mechanism_class",
    "event_state_count",
    "event_message_id_counts",
    "event_settlement_state_count",
    "event_inventory_state_count",
    "event_latest_inventory_count",
    "event_latest_inventory_count_delta",
    "event_action_result_count_all",
    "event_direct_action_state_count",
    "event_action_observed_item_count_max",
    "event_direct_action_observed_item_count_max",
    "event_action_observed_item_inventory_gap_min",
    "event_direct_action_observed_item_inventory_gap_min",
    "event_action_observed_item_ratio_max",
    "event_direct_action_observed_item_ratio_max",
    "event_full_observed_action_ids",
    "event_direct_full_observed_action_ids",
    "event_public_total_count_values",
    "event_public_total_inventory_delta",
    "event_public_total_match",
)


def _safe_int(value: Any) -> int | None:
    return scp._safe_int(value)


def _numeric_summary(values: Iterable[Any]) -> dict[str, Any]:
    return scp._numeric_summary(values)


def _counter_dict(values: Iterable[Any], *, top: int = 8) -> dict[str, int]:
    return scp._counter_dict(values, top=top)


def _merge_count_mappings(
    mappings: Iterable[Mapping[str, int]],
    *,
    top: int = 8,
) -> dict[str, int]:
    return scp._merge_count_mappings(mappings, top=top)


def _format_counts(counts: Mapping[str, int]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def _format_summary(summary: Mapping[str, Any]) -> str:
    return (
        f"n={summary['n']}"
        f"/avg={summary['avg']}"
        f"/p50={summary['p50']}"
        f"/p90={summary['p90']}"
        f"/p95={summary['p95']}"
        f"/max={summary['max']}"
    )


def _message_id_label(value: Any) -> str:
    parsed = _safe_int(value)
    return f"0x{parsed:04X}" if parsed is not None else "none"


def _read_first_line(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            text = line.strip()
            if text:
                return text
    except OSError:
        return None
    return None


def _filelist_contains(path: Path, needle: str) -> bool:
    if not path.exists():
        return False
    try:
        return needle in path.read_text(encoding="utf-8-sig")
    except OSError:
        return False


def _table_overlay_metadata(root: Path = ROOT) -> dict[str, Any]:
    raw_filelist = root / "data" / "raw" / "filelist.txt"
    table_filelist = root / "data" / "raw" / "tables" / "filelist.txt"
    activity_table = root / "data" / "raw" / "tables" / "Activity.txt"
    activity_listed = _filelist_contains(
        raw_filelist,
        "Tables/Activity.txt",
    ) or _filelist_contains(table_filelist, "Tables/Activity.txt")
    activity_present = activity_table.exists()
    if activity_listed and not activity_present:
        status = "v300_activity_listed_missing_locally"
    elif activity_present:
        status = "activity_table_available_locally"
    else:
        status = "no_activity_table_signal"
    return {
        "raw_file_version": _read_first_line(root / "data" / "raw" / "fileVersion"),
        "raw_tables_file_version": _read_first_line(
            root / "data" / "raw" / "tables" / "fileVersion"
        ),
        "raw_filelist_header": _read_first_line(raw_filelist),
        "raw_tables_filelist_header": _read_first_line(table_filelist),
        "activity_table_present": activity_present,
        "activity_table_listed_in_filelist": activity_listed,
        "local_overlay_status": status,
    }


def _event_public_total_values(events: Any) -> tuple[int | float, ...]:
    values: list[int | float] = []
    for state in tuple(getattr(events, "states", ()) or ()):
        for info in tuple(getattr(state, "public_infos", ()) or ()):
            if getattr(info, "info_id", None) != PUBLIC_TOTAL_INFO_ID:
                continue
            value = getattr(info, "value", None)
            if isinstance(value, bool) or value is None:
                continue
            values.append(value)
    return tuple(values)


def _public_total_deltas(
    values: Iterable[Any],
    *,
    inventory_count: int | None,
) -> tuple[int, ...]:
    if inventory_count is None:
        return ()
    out: list[int] = []
    for value in values:
        parsed = _safe_int(value)
        if parsed is not None:
            out.append(parsed - inventory_count)
    return tuple(out)


def _action_coverage(
    observed_count_max: int,
    *,
    inventory_count: int | None,
) -> tuple[int | None, float | None]:
    if inventory_count is None or inventory_count <= 0:
        return (None, None)
    return (
        int(inventory_count) - int(observed_count_max),
        round(float(observed_count_max) / float(inventory_count), 6),
    )


def _source_diagnostic_for_path(
    path: Path,
    *,
    inventory_count: int | None,
) -> dict[str, Any]:
    events = parse_fatbeans_capture(path)
    states = tuple(getattr(events, "states", ()) or ())
    message_counts = Counter(_message_id_label(getattr(state, "message_id", None)) for state in states)
    settlement_states = tuple(
        state for state in states if getattr(state, "message_id", None) == 0x002D
    )
    inventory_states = tuple(
        state for state in states if tuple(getattr(state, "inventory_items", ()) or ())
    )
    latest_inventory_count = (
        len(tuple(getattr(inventory_states[-1], "inventory_items", ()) or ()))
        if inventory_states
        else None
    )

    all_action_observed: list[tuple[int | None, int, int | None]] = []
    direct_action_observed: list[tuple[int | None, int, int | None]] = []
    for state in states:
        message_id = _safe_int(getattr(state, "message_id", None))
        for action in tuple(getattr(state, "action_results", ()) or ()):
            action_id = _safe_int(getattr(action, "action_id", None))
            observed_count = len(tuple(getattr(action, "observed_items", ()) or ()))
            entry = (action_id, observed_count, message_id)
            all_action_observed.append(entry)
            if message_id == 0x0027:
                direct_action_observed.append(entry)

    full_observed_action_ids = tuple(
        action_id
        for action_id, observed_count, _message_id in all_action_observed
        if action_id is not None and inventory_count is not None and observed_count == inventory_count
    )
    direct_full_observed_action_ids = tuple(
        action_id
        for action_id, observed_count, _message_id in direct_action_observed
        if action_id is not None and inventory_count is not None and observed_count == inventory_count
    )
    public_values = _event_public_total_values(events)
    public_deltas = _public_total_deltas(public_values, inventory_count=inventory_count)
    action_observed_item_count_max = max(
        (count for _action_id, count, _message_id in all_action_observed),
        default=0,
    )
    direct_action_observed_item_count_max = max(
        (count for _action_id, count, _message_id in direct_action_observed),
        default=0,
    )
    action_gap_min, action_ratio_max = _action_coverage(
        action_observed_item_count_max,
        inventory_count=inventory_count,
    )
    direct_action_gap_min, direct_action_ratio_max = _action_coverage(
        direct_action_observed_item_count_max,
        inventory_count=inventory_count,
    )

    return {
        "event_state_count": len(states),
        "event_message_id_counts": dict(
            sorted(message_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "event_settlement_state_count": len(settlement_states),
        "event_inventory_state_count": len(inventory_states),
        "event_latest_inventory_count": latest_inventory_count,
        "event_latest_inventory_count_delta": (
            latest_inventory_count - inventory_count
            if latest_inventory_count is not None and inventory_count is not None
            else None
        ),
        "event_action_result_count_all": len(all_action_observed),
        "event_direct_action_state_count": len(direct_action_observed),
        "event_action_observed_item_count_max": action_observed_item_count_max,
        "event_direct_action_observed_item_count_max": (
            direct_action_observed_item_count_max
        ),
        "event_action_observed_item_inventory_gap_min": action_gap_min,
        "event_direct_action_observed_item_inventory_gap_min": direct_action_gap_min,
        "event_action_observed_item_ratio_max": action_ratio_max,
        "event_direct_action_observed_item_ratio_max": direct_action_ratio_max,
        "event_full_observed_action_ids": full_observed_action_ids,
        "event_direct_full_observed_action_ids": direct_full_observed_action_ids,
        "event_public_total_count_values": public_values,
        "event_public_total_inventory_delta": public_deltas,
        "event_public_total_match": any(delta == 0 for delta in public_deltas),
    }


def _payload_inventory_verified(row: Mapping[str, Any]) -> bool:
    return (
        row.get("raw_candidate_inventory_delta") == 0
        and row.get("occupied_slot_inventory_delta") == 0
    )


def _source_evidence_class(row: Mapping[str, Any], diag: Mapping[str, Any]) -> str:
    if any(delta == 0 for delta in tuple(diag.get("event_public_total_inventory_delta", ()) or ())):
        return "public_total_matches_inventory"
    if tuple(diag.get("event_direct_full_observed_action_ids", ()) or ()):
        return "direct_action_matches_inventory"
    if tuple(diag.get("event_full_observed_action_ids", ()) or ()):
        return "full_action_matches_inventory"
    if _payload_inventory_verified(row):
        return "settlement_payload_verified_only"
    return "source_ambiguous"


def _source_context_class(row: Mapping[str, Any], diag: Mapping[str, Any]) -> str:
    evidence = _source_evidence_class(row, diag)
    if evidence == "public_total_matches_inventory":
        return "public_total_confirmed"
    if evidence == "direct_action_matches_inventory":
        return "direct_action_full_confirmed"
    if evidence == "full_action_matches_inventory":
        return "action_full_confirmed"
    if not _payload_inventory_verified(row):
        return "payload_unverified_or_mismatch"
    if tuple(diag.get("event_public_total_count_values", ()) or ()):
        return "payload_verified_public_total_nonmatch"
    action_count = _safe_int(diag.get("event_action_result_count_all")) or 0
    action_max = _safe_int(diag.get("event_action_observed_item_count_max")) or 0
    if action_max > 0:
        return "payload_verified_partial_action_only"
    if action_count > 0:
        return "payload_verified_empty_action_results"
    return "payload_verified_no_external_source"


def _mechanism_class(row: Mapping[str, Any], diag: Mapping[str, Any]) -> str:
    if row.get("unique_residual_mode") != "unique_round_cap_overflow_after_temp":
        return "not_unique_round_cap_blocker"
    non_zodiac_missing = _safe_int(row.get("non_zodiac_missing_from_drop_universe_count")) or 0
    if non_zodiac_missing > 0:
        return "external_overlay_table"
    evidence = _source_evidence_class(row, diag)
    if evidence in {
        "public_total_matches_inventory",
        "direct_action_matches_inventory",
        "full_action_matches_inventory",
    }:
        return "server_side_settlement_expansion"
    if _payload_inventory_verified(row):
        return "session_capacity_source_semantics"
    return "other_undecidable"


def _base_rows(
    paths: Iterable[Path],
    *,
    tables: Any,
) -> tuple[tuple[dict[str, Any], ...], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    drop_universe_cache: dict[int, frozenset[int]] = {}
    for path in scp._resolve_paths(paths):
        try:
            row = scp._audit_file(
                path,
                tables=tables,
                drop_universe_cache=drop_universe_cache,
            )
        except Exception as exc:  # pragma: no cover - retained for CLI diagnostics.
            errors.append(f"{path}:{type(exc).__name__}:{exc}")
            continue
        if row.get("status") == "ok":
            row["residual_mode"] = scp._residual_mode(row)
            row["unique_residual_mode"] = scp._unique_residual_mode(row)
        rows.append(row)
    return tuple(rows), errors


def _group_key(row: Mapping[str, Any], group_by: str) -> str:
    value = row.get(group_by)
    return str(value) if value not in (None, "") else "none"


def _positive_rows(rows: Iterable[Mapping[str, Any]], field: str) -> int:
    return scp._positive_rows(rows, field)


def _event_public_total_match_rows(rows: Iterable[Mapping[str, Any]]) -> int:
    return sum(1 for row in rows if row.get("event_public_total_match"))


def _event_full_action_rows(rows: Iterable[Mapping[str, Any]]) -> int:
    return sum(1 for row in rows if tuple(row.get("event_full_observed_action_ids", ()) or ()))


def _event_direct_full_action_rows(rows: Iterable[Mapping[str, Any]]) -> int:
    return sum(
        1 for row in rows if tuple(row.get("event_direct_full_observed_action_ids", ()) or ())
    )


def _payload_mismatch_rows(rows: Iterable[Mapping[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if (
            row.get("raw_candidate_inventory_delta") not in (0, None)
            or row.get("occupied_slot_inventory_delta") not in (0, None)
        )
    )


def _examples(rows: Iterable[Mapping[str, Any]], *, top: int) -> list[dict[str, Any]]:
    selected = sorted(
        rows,
        key=lambda row: (
            -int(row.get("unique_round_cap_excess_after_temp_zodiac_count") or 0),
            -int(row.get("unique_non_temp_item_id_count") or 0),
            str(row.get("file") or ""),
        ),
    )[:top]
    return [
        {
            "file": row.get("file"),
            "map_id": row.get("map_id"),
            "inventory_count": row.get("inventory_count"),
            "non_temp_inventory_count": row.get("non_temp_inventory_count"),
            "unique_non_temp_item_id_count": row.get("unique_non_temp_item_id_count"),
            "drop_ref_max": row.get("bidmap_items_per_session_max"),
            "round_cap_max": row.get("bidmap_raw_round_cap_max"),
            "unique_round_excess_after_temp": row.get(
                "unique_round_cap_excess_after_temp_zodiac_count"
            ),
            "source_evidence_class": row.get("source_evidence_class"),
            "source_context_class": row.get("source_context_class"),
            "mechanism_class": row.get("mechanism_class"),
            "event_public_total_count_values": list(
                tuple(row.get("event_public_total_count_values", ()) or ())
            ),
            "event_public_total_inventory_delta": list(
                tuple(row.get("event_public_total_inventory_delta", ()) or ())
            ),
            "event_full_observed_action_ids": list(
                tuple(row.get("event_full_observed_action_ids", ()) or ())
            ),
            "event_direct_full_observed_action_ids": list(
                tuple(row.get("event_direct_full_observed_action_ids", ()) or ())
            ),
            "event_action_observed_item_count_max": row.get(
                "event_action_observed_item_count_max"
            ),
            "event_action_observed_item_inventory_gap_min": row.get(
                "event_action_observed_item_inventory_gap_min"
            ),
            "event_action_observed_item_ratio_max": row.get(
                "event_action_observed_item_ratio_max"
            ),
        }
        for row in selected
    ]


def _jsonable_detail_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable_detail_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _jsonable_detail_value(item) for key, item in value.items()}
    return value


def _detail_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    selected = sorted(
        rows,
        key=lambda row: (
            -int(row.get("unique_round_cap_excess_after_temp_zodiac_count") or 0),
            -int(row.get("unique_non_temp_item_id_count") or 0),
            str(row.get("mechanism_class") or ""),
            str(row.get("unique_residual_mode") or ""),
            str(row.get("map_id") or ""),
            str(row.get("file") or ""),
        ),
    )[:limit]
    return [
        {
            field: _jsonable_detail_value(row.get(field))
            for field in DETAIL_FIELDS
            if field in row
        }
        for row in selected
    ]


def _summarize_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    group_by: str,
    top: int,
) -> dict[str, Any]:
    seq = tuple(rows)
    return {
        "group_by": group_by,
        "group": _group_key(seq[0], group_by) if seq else "overall",
        "files": len(seq),
        "map_ids": _counter_dict((row.get("map_id") for row in seq), top=top),
        "map_families": _counter_dict((row.get("map_family") for row in seq), top=top),
        "capture_days": _counter_dict((row.get("capture_day") for row in seq), top=top),
        "session_token_prefix6_counts": _counter_dict(
            (row.get("session_token_prefix6") for row in seq),
            top=top,
        ),
        "bidmap_rounds_total_counts": _counter_dict(
            (row.get("bidmap_rounds_total") for row in seq),
            top=top,
        ),
        "bidmap_sub_pool_kind_counts": _counter_dict(
            (row.get("bidmap_sub_pool_kind") for row in seq),
            top=top,
        ),
        "source_evidence_classes": _counter_dict(
            (row.get("source_evidence_class") for row in seq),
            top=top,
        ),
        "source_context_classes": _counter_dict(
            (row.get("source_context_class") for row in seq),
            top=top,
        ),
        "mechanism_classes": _counter_dict(
            (row.get("mechanism_class") for row in seq),
            top=top,
        ),
        "unique_residual_modes": _counter_dict(
            (row.get("unique_residual_mode") for row in seq),
            top=top,
        ),
        "inventory_count": _numeric_summary(row.get("inventory_count") for row in seq),
        "non_temp_inventory_count": _numeric_summary(
            row.get("non_temp_inventory_count") for row in seq
        ),
        "unique_non_temp_item_id_count": _numeric_summary(
            row.get("unique_non_temp_item_id_count") for row in seq
        ),
        "bidmap_items_per_session_max": _numeric_summary(
            row.get("bidmap_items_per_session_max") for row in seq
        ),
        "bidmap_raw_round_cap_max": _numeric_summary(
            row.get("bidmap_raw_round_cap_max") for row in seq
        ),
        "non_zodiac_missing_from_drop_universe_count": _numeric_summary(
            row.get("non_zodiac_missing_from_drop_universe_count") for row in seq
        ),
        "unique_drop_ref_excess_after_temp_zodiac_count": _numeric_summary(
            row.get("unique_drop_ref_excess_after_temp_zodiac_count") for row in seq
        ),
        "unique_round_cap_excess_after_temp_zodiac_count": _numeric_summary(
            row.get("unique_round_cap_excess_after_temp_zodiac_count") for row in seq
        ),
        "unique_above_round_after_temp_zodiac_rows": _positive_rows(
            seq,
            "unique_round_cap_excess_after_temp_zodiac_count",
        ),
        "payload_inventory_mismatch_rows": _payload_mismatch_rows(seq),
        "raw_candidate_inventory_delta": _numeric_summary(
            row.get("raw_candidate_inventory_delta") for row in seq
        ),
        "occupied_slot_inventory_delta": _numeric_summary(
            row.get("occupied_slot_inventory_delta") for row in seq
        ),
        "event_state_count": _numeric_summary(row.get("event_state_count") for row in seq),
        "event_settlement_state_count": _numeric_summary(
            row.get("event_settlement_state_count") for row in seq
        ),
        "event_inventory_state_count": _numeric_summary(
            row.get("event_inventory_state_count") for row in seq
        ),
        "event_latest_inventory_count_delta": _numeric_summary(
            row.get("event_latest_inventory_count_delta") for row in seq
        ),
        "event_message_id_counts": _merge_count_mappings(
            (row.get("event_message_id_counts", {}) for row in seq),
            top=top,
        ),
        "event_action_result_count_all": _numeric_summary(
            row.get("event_action_result_count_all") for row in seq
        ),
        "event_direct_action_state_count": _numeric_summary(
            row.get("event_direct_action_state_count") for row in seq
        ),
        "event_action_observed_item_count_max": _numeric_summary(
            row.get("event_action_observed_item_count_max") for row in seq
        ),
        "event_action_observed_item_inventory_gap_min": _numeric_summary(
            row.get("event_action_observed_item_inventory_gap_min") for row in seq
        ),
        "event_action_observed_item_ratio_max": _numeric_summary(
            row.get("event_action_observed_item_ratio_max") for row in seq
        ),
        "event_public_total_rows": sum(
            1 for row in seq if tuple(row.get("event_public_total_count_values", ()) or ())
        ),
        "event_public_total_match_rows": _event_public_total_match_rows(seq),
        "event_public_total_inventory_delta": _numeric_summary(
            delta
            for row in seq
            for delta in tuple(row.get("event_public_total_inventory_delta", ()) or ())
        ),
        "event_full_action_rows": _event_full_action_rows(seq),
        "event_direct_full_action_rows": _event_direct_full_action_rows(seq),
        "examples": _examples(seq, top=min(top, 5)),
    }


def summarize_settlement_source_semantics_audit(
    paths: Iterable[Path] = (),
    *,
    tables: Any | None = None,
    group_by: str = DEFAULT_GROUP_BY,
    top: int = 8,
    details: int = 0,
) -> dict[str, Any]:
    if group_by not in GROUP_BY_CHOICES:
        raise ValueError(f"unsupported group_by: {group_by}")
    tables = tables or load_monitor_tables()
    rows, errors = _base_rows(paths, tables=tables)
    ready: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") != "ok":
            continue
        try:
            diag = _source_diagnostic_for_path(
                Path(str(row.get("path"))),
                inventory_count=_safe_int(row.get("inventory_count")),
            )
        except Exception as exc:  # pragma: no cover - retained for CLI diagnostics.
            errors.append(f"{row.get('path')}:{type(exc).__name__}:{exc}")
            continue
        enriched = {**row, **diag}
        enriched["source_evidence_class"] = _source_evidence_class(enriched, diag)
        enriched["source_context_class"] = _source_context_class(enriched, diag)
        enriched["mechanism_class"] = _mechanism_class(enriched, diag)
        ready.append(enriched)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ready:
        groups[_group_key(row, group_by)].append(row)

    group_rows = [
        _summarize_rows(group_rows, group_by=group_by, top=top)
        for _key, group_rows in groups.items()
    ]
    group_rows.sort(
        key=lambda row: (
            -int(row["unique_above_round_after_temp_zodiac_rows"]),
            -int(row["files"]),
            str(row["group"]),
        )
    )
    result = {
        "errors": errors,
        "files": len(rows),
        "settlement_rows": len(ready),
        "group_by": group_by,
        "table_overlay_metadata": _table_overlay_metadata(ROOT),
        "overall": _summarize_rows(ready, group_by="overall", top=top),
        "rows": group_rows,
    }
    if details > 0:
        result["detail_rows"] = _detail_rows(ready, limit=details)
    return result


def _print_group(prefix: str, row: Mapping[str, Any]) -> None:
    print(
        " ".join(
            (
                prefix,
                f"files={row['files']}",
                f"maps={_format_counts(row['map_ids'])}",
                f"families={_format_counts(row['map_families'])}",
                f"capture_days={_format_counts(row['capture_days'])}",
                f"session_p6={_format_counts(row['session_token_prefix6_counts'])}",
                f"bidmap_rounds_total={_format_counts(row['bidmap_rounds_total_counts'])}",
                f"sub_pool_kinds={_format_counts(row['bidmap_sub_pool_kind_counts'])}",
                f"evidence={_format_counts(row['source_evidence_classes'])}",
                f"context={_format_counts(row['source_context_classes'])}",
                f"mechanisms={_format_counts(row['mechanism_classes'])}",
                f"unique_modes={_format_counts(row['unique_residual_modes'])}",
                f"inventory={_format_summary(row['inventory_count'])}",
                f"non_temp={_format_summary(row['non_temp_inventory_count'])}",
                f"unique_non_temp={_format_summary(row['unique_non_temp_item_id_count'])}",
                f"drop_ref_max={_format_summary(row['bidmap_items_per_session_max'])}",
                f"round_cap={_format_summary(row['bidmap_raw_round_cap_max'])}",
                f"non_zodiac_missing={_format_summary(row['non_zodiac_missing_from_drop_universe_count'])}",
                f"unique_drop_after={_format_summary(row['unique_drop_ref_excess_after_temp_zodiac_count'])}",
                f"unique_round_after={_format_summary(row['unique_round_cap_excess_after_temp_zodiac_count'])}",
                f"unique_round_rows={row['unique_above_round_after_temp_zodiac_rows']}/{row['files']}",
                f"payload_mismatch={row['payload_inventory_mismatch_rows']}/{row['files']}",
                f"raw_candidate_delta={_format_summary(row['raw_candidate_inventory_delta'])}",
                f"occupied_delta={_format_summary(row['occupied_slot_inventory_delta'])}",
                f"states={_format_summary(row['event_state_count'])}",
                f"settlement_states={_format_summary(row['event_settlement_state_count'])}",
                f"inventory_state_delta={_format_summary(row['event_latest_inventory_count_delta'])}",
                f"message_ids={_format_counts(row['event_message_id_counts'])}",
                f"actions={_format_summary(row['event_action_result_count_all'])}",
                f"direct_actions={_format_summary(row['event_direct_action_state_count'])}",
                f"action_max={_format_summary(row['event_action_observed_item_count_max'])}",
                f"action_gap={_format_summary(row['event_action_observed_item_inventory_gap_min'])}",
                f"action_ratio={_format_summary(row['event_action_observed_item_ratio_max'])}",
                f"public_rows={row['event_public_total_rows']}/{row['files']}",
                f"public_match_rows={row['event_public_total_match_rows']}/{row['files']}",
                f"public_delta={_format_summary(row['event_public_total_inventory_delta'])}",
                f"full_action_rows={row['event_full_action_rows']}/{row['files']}",
                f"direct_full_action_rows={row['event_direct_full_action_rows']}/{row['files']}",
            )
        )
    )


def _print_summary(result: Mapping[str, Any], *, top: int) -> None:
    metadata = result["table_overlay_metadata"]
    print(
        " ".join(
            (
                f"table_raw_version={metadata['raw_file_version']}",
                f"tables_version={metadata['raw_tables_file_version']}",
                f"activity_present={metadata['activity_table_present']}",
                f"activity_listed={metadata['activity_table_listed_in_filelist']}",
                f"overlay_status={metadata['local_overlay_status']}",
            )
        )
    )
    print(
        " ".join(
            (
                f"files={result['files']}",
                f"settlement_rows={result['settlement_rows']}",
                f"group_by={result['group_by']}",
                f"groups={len(result['rows'])}",
            )
        )
    )
    _print_group("overall", result["overall"])
    for row in result["rows"][:top]:
        _print_group(f"{row['group_by']}={row['group']}", row)
        for example in row["examples"][: min(3, top)]:
            print(
                "  example="
                + " ".join(
                    (
                        f"file={example['file']}",
                        f"map_id={example['map_id']}",
                        f"inventory={example['inventory_count']}",
                        f"non_temp={example['non_temp_inventory_count']}",
                        f"unique_non_temp={example['unique_non_temp_item_id_count']}",
                        f"drop_ref_max={example['drop_ref_max']}",
                        f"round_cap={example['round_cap_max']}",
                        f"unique_round_after={example['unique_round_excess_after_temp']}",
                        f"evidence={example['source_evidence_class']}",
                        f"context={example['source_context_class']}",
                        f"mechanism={example['mechanism_class']}",
                        f"public={example['event_public_total_count_values']}",
                        f"public_delta={example['event_public_total_inventory_delta']}",
                        f"full_actions={example['event_full_observed_action_ids']}",
                        f"direct_full_actions={example['event_direct_full_observed_action_ids']}",
                        f"action_max={example['event_action_observed_item_count_max']}",
                        f"action_gap={example['event_action_observed_item_inventory_gap_min']}",
                        f"action_ratio={example['event_action_observed_item_ratio_max']}",
                    )
                )
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit settlement source semantics for the v3 capacity blocker.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--group-by", choices=GROUP_BY_CHOICES, default=DEFAULT_GROUP_BY)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument(
        "--details",
        type=int,
        default=0,
        metavar="N",
        help="Include up to N file-level diagnostic detail rows in JSON output.",
    )
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    result = summarize_settlement_source_semantics_audit(
        args.paths,
        group_by=args.group_by,
        top=args.top,
        details=args.details,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result["errors"]:
            print(f"errors={len(result['errors'])}")
        _print_summary(result, top=args.top)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
