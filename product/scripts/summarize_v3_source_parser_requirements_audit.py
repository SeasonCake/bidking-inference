"""Summarize source-parser requirements for v3 capacity/source blockers."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_SEMANTICS = (
    ROOT / ".tmp" / "codex" / "v3_settlement_source_semantics_details_1000_latest.json"
)
FALLBACK_SOURCE_SEMANTICS = (
    ROOT / ".tmp" / "codex" / "v3_settlement_source_semantics_details_latest.json"
)
DEFAULT_GUARD_LOSS_CONTEXT = ROOT / ".tmp" / "codex" / "v3_guard_loss_source_context_latest.json"
DEFAULT_CSE = ROOT / "data" / "processed" / "v3_capacity_source_expansion_shadow.json"
DEFAULT_PAYLOAD_ONLY = ROOT / ".tmp" / "codex" / "v3_capacity_source_expansion_payload_only_latest.json"
DEFAULT_NUMERIC_ACTION_SEMANTICS = (
    ROOT / ".tmp" / "codex" / "v3_numeric_action_result_semantics_2410_latest.json"
)
DEFAULT_SESSION_CAPACITY_SOURCE_GAP = (
    ROOT / ".tmp" / "codex" / "v3_session_capacity_source_gap_2410_latest.json"
)
DEFAULT_PAYLOAD_TABLE_GAP = (
    ROOT / ".tmp" / "codex" / "v3_session_capacity_payload_table_gap_2410_latest.json"
)
DEFAULT_PAYLOAD_OUTER_FIELDS = (
    ROOT / ".tmp" / "codex" / "v3_payload_outer_field_semantics_2410_latest.json"
)
DEFAULT_TABLE_OVERLAY_RESIDUAL = (
    ROOT
    / ".tmp"
    / "codex"
    / "v3_session_capacity_table_overlay_residual_2410_latest.json"
)
MAX_EXAMPLES = 3


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _default_source_path() -> Path:
    return DEFAULT_SOURCE_SEMANTICS if DEFAULT_SOURCE_SEMANTICS.exists() else FALLBACK_SOURCE_SEMANTICS


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {
        key: count
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    }


def _rows_by_map(rows: list[Any]) -> dict[str, list[Mapping[str, Any]]]:
    out: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, Mapping) or row.get("map_id") is None:
            continue
        out[str(row.get("map_id"))].append(row)
    return out


def _index_guard_rows(guard_loss_context: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not isinstance(guard_loss_context, Mapping):
        return {}
    return {
        str(row.get("map_id")): row
        for row in _as_list(guard_loss_context.get("rows"))
        if isinstance(row, Mapping) and row.get("map_id") is not None
    }


def _index_cse_entries(cse: Mapping[str, Any] | None) -> dict[tuple[str, str], Mapping[str, Any]]:
    if not isinstance(cse, Mapping):
        return {}
    out: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in _as_list(cse.get("entries")):
        if not isinstance(row, Mapping):
            continue
        scope = str(row.get("scope") or "")
        group = str(row.get("group") or "")
        if scope and group:
            out[(scope, group)] = row
    return out


def _payload_rows_by_map(payload_only: Mapping[str, Any] | None) -> dict[str, list[Mapping[str, Any]]]:
    if not isinstance(payload_only, Mapping):
        return {}
    return _rows_by_map(_as_list(payload_only.get("rows")))


def _numeric_action_rows_by_map(
    numeric_action_semantics: Mapping[str, Any] | None,
) -> dict[str, list[Mapping[str, Any]]]:
    if not isinstance(numeric_action_semantics, Mapping):
        return {}
    return _rows_by_map(_as_list(numeric_action_semantics.get("rows")))


def _session_gap_rows_by_map(
    session_capacity_source_gap: Mapping[str, Any] | None,
) -> dict[str, list[Mapping[str, Any]]]:
    if not isinstance(session_capacity_source_gap, Mapping):
        return {}
    return _rows_by_map(_as_list(session_capacity_source_gap.get("rows")))


def _payload_table_gap_rows_by_map(
    payload_table_gap: Mapping[str, Any] | None,
) -> dict[str, list[Mapping[str, Any]]]:
    if not isinstance(payload_table_gap, Mapping):
        return {}
    return _rows_by_map(_as_list(payload_table_gap.get("rows")))


def _payload_outer_field_rows_by_map(
    payload_outer_fields: Mapping[str, Any] | None,
) -> dict[str, list[Mapping[str, Any]]]:
    if not isinstance(payload_outer_fields, Mapping):
        return {}
    return _rows_by_map(_as_list(payload_outer_fields.get("rows")))


def _table_overlay_residual_rows_by_map(
    table_overlay_residual: Mapping[str, Any] | None,
) -> dict[str, list[Mapping[str, Any]]]:
    if not isinstance(table_overlay_residual, Mapping):
        return {}
    return _rows_by_map(_as_list(table_overlay_residual.get("rows")))


def _count_text_to_dict(value: Any) -> dict[str, int]:
    if isinstance(value, Mapping):
        return {str(key): _int(count) for key, count in value.items()}
    text = str(value or "").strip()
    if not text:
        return {}
    out: dict[str, int] = {}
    for chunk in text.split(","):
        if ":" not in chunk:
            continue
        key, count = chunk.split(":", 1)
        key = key.strip()
        if key:
            out[key] = _int(count)
    return out


def _numeric_max(rows: list[Mapping[str, Any]], field: str) -> float | None:
    values = [
        value
        for value in (_float(row.get(field)) for row in rows)
        if value is not None
    ]
    return max(values) if values else None


def _numeric_sum(rows: list[Mapping[str, Any]], field: str) -> float:
    return sum(
        value
        for value in (_float(row.get(field)) for row in rows)
        if value is not None
    )


def _source_summary(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    residuals = Counter(str(row.get("unique_residual_mode") or "unknown") for row in rows)
    mechanisms = Counter(str(row.get("mechanism_class") or "unknown") for row in rows)
    contexts = Counter(str(row.get("source_context_class") or "unknown") for row in rows)
    evidence = Counter(str(row.get("source_evidence_class") or "unknown") for row in rows)
    return {
        "rows": len(rows),
        "residual_mode_counts": _counter_dict(residuals),
        "mechanism_counts": _counter_dict(mechanisms),
        "source_context_counts": _counter_dict(contexts),
        "source_evidence_counts": _counter_dict(evidence),
        "session_capacity_source_semantics_rows": mechanisms.get(
            "session_capacity_source_semantics",
            0,
        ),
        "unique_round_overflow_rows": residuals.get(
            "unique_round_cap_overflow_after_temp",
            0,
        ),
        "instance_round_overflow_rows": residuals.get(
            "instance_round_cap_overflow_after_temp",
            0,
        ),
        "unique_drop_ref_only_overflow_rows": residuals.get(
            "unique_drop_ref_only_overflow_after_temp",
            0,
        ),
        "instance_drop_ref_only_overflow_rows": residuals.get(
            "instance_drop_ref_only_overflow_after_temp",
            0,
        ),
        "activity_extras_only_rows": residuals.get(
            "activity_extras_only_drop_ref_gap",
            0,
        ),
        "public_total_confirmed_rows": contexts.get("public_total_confirmed", 0),
        "payload_only_rows": sum(
            count
            for key, count in contexts.items()
            if str(key).startswith("payload_verified_")
        ),
        "non_zodiac_missing_max": _numeric_max(
            rows,
            "non_zodiac_missing_from_drop_universe_count",
        ),
        "unique_round_excess_max": _numeric_max(
            rows,
            "unique_round_cap_excess_after_temp_zodiac_count",
        ),
        "unique_drop_ref_excess_max": _numeric_max(
            rows,
            "unique_drop_ref_excess_after_temp_zodiac_count",
        ),
        "event_action_observed_item_count_max": _numeric_max(
            rows,
            "event_action_observed_item_count_max",
        ),
        "event_action_result_count_all_sum": _numeric_sum(
            rows,
            "event_action_result_count_all",
        ),
        "event_full_action_rows": sum(
            1 for row in rows if _as_list(row.get("event_full_observed_action_ids"))
        ),
        "event_public_total_match_rows": sum(
            1 for row in rows if row.get("event_public_total_match")
        ),
    }


def _payload_summary(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    shapes = Counter(str(row.get("source_action_payload_shape_class") or "unknown") for row in rows)
    return {
        "rows": len(rows),
        "source_action_payload_shape_counts": _counter_dict(shapes),
        "numeric_only_result_rows": shapes.get("numeric_only_result", 0),
        "item_reveal_payload_rows": shapes.get("item_reveal_payload", 0),
        "map_id_holdout_missed_rows": sum(
            1 for row in rows if row.get("map_id_holdout_covered") is False
        ),
        "prebid_candidate_rows": sum(
            1 for row in rows if _int(row.get("prebid_cse_candidate_windows")) > 0
        ),
        "prebid_pressure_rows": sum(
            1 for row in rows if _int(row.get("prebid_pressure_windows")) > 0
        ),
        "source_action_result_fields": _counter_dict(
            Counter(
                str(key)
                for row in rows
                for key, count in _as_mapping(row.get("source_action_result_fields")).items()
                for _ in range(max(_int(count), 0))
            )
        ),
        "source_action_ids": _counter_dict(
            Counter(
                str(key)
                for row in rows
                for key, count in _as_mapping(row.get("source_action_ids")).items()
                for _ in range(max(_int(count), 0))
            )
        ),
        "source_action_item_payload_blocks_max": _numeric_max(
            rows,
            "source_action_item_payload_blocks",
        ),
        "source_action_observed_item_count_max": _numeric_max(
            rows,
            "source_action_observed_item_count",
        ),
    }


def _numeric_action_semantics_summary(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(row.get("status") or "unknown") for row in rows)
    actions = Counter(str(row.get("action_id") or "unknown") for row in rows)
    semantics = Counter(str(row.get("expected_semantic") or "unknown") for row in rows)
    implications = Counter(str(row.get("parser_implication") or "unknown") for row in rows)
    return {
        "rows": len(rows),
        "status_counts": _counter_dict(statuses),
        "action_id_counts": _counter_dict(actions),
        "expected_semantic_counts": _counter_dict(semantics),
        "parser_implication_counts": _counter_dict(implications),
        "session_capacity_signal_rows": sum(
            1
            for row in rows
            if row.get("parser_implication") == "session_capacity_signal"
            and row.get("expected_match") is True
        ),
        "non_session_expected_rows": sum(
            1
            for row in rows
            if row.get("parser_implication") == "not_session_capacity_signal"
            and row.get("expected_match") is True
        ),
        "unknown_semantic_rows": statuses.get(
            "blocked_unknown_numeric_action_semantics",
            0,
        ),
        "expected_mismatch_rows": statuses.get(
            "blocked_expected_semantic_mismatch",
            0,
        ),
    }


def _session_gap_summary(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(row.get("status") or "unknown") for row in rows)
    mechanisms = Counter(str(row.get("mechanism_class") or "unknown") for row in rows)
    return {
        "rows": len(rows),
        "status_counts": _counter_dict(statuses),
        "mechanism_counts": _counter_dict(mechanisms),
        "session_capacity_rows": mechanisms.get(
            "session_capacity_source_semantics",
            0,
        ),
        "exact_session_count_source_rows": sum(
            1
            for row in rows
            if _int(
                _as_mapping(row.get("event_source_digest")).get(
                    "session_count_source_count"
                )
            )
            > 0
        ),
        "warehouse_cells_only_rows": statuses.get(
            "watch_warehouse_cells_only_no_session_count",
            0,
        ),
        "bucket_only_blocked_rows": statuses.get(
            "blocked_session_capacity_source_gap_bucket_only",
            0,
        ),
        "unresolved_session_capacity_rows": sum(
            count
            for status, count in statuses.items()
            if status.startswith("blocked_session_capacity_source_gap")
        ),
    }


def _payload_table_gap_summary(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(row.get("status") or "unknown") for row in rows)
    reasons = Counter(
        str(reason)
        for row in rows
        for reason in _as_list(row.get("reasons"))
    )
    next_checks = Counter(
        str(check)
        for row in rows
        for check in _as_list(row.get("next_checks"))
    )
    return {
        "rows": len(rows),
        "status_counts": _counter_dict(statuses),
        "reason_counts": _counter_dict(reasons),
        "next_check_counts": _counter_dict(next_checks),
        "blocked_rows": sum(
            count for status, count in statuses.items() if status.startswith("blocked")
        ),
        "payload_verified_rows": reasons.get("payload_inventory_verified", 0),
        "no_full_event_payload_rows": reasons.get("no_full_event_payload_source", 0),
        "table_or_server_side_next_check_rows": sum(
            count
            for check, count in next_checks.items()
            if check
            in {
                "check_per_session_table_version_or_external_overlay",
                "inspect_server_side_settlement_expansion_or_source_transform",
            }
        ),
    }


def _payload_outer_field_summary(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(row.get("status") or "unknown") for row in rows)
    return {
        "rows": len(rows),
        "status_counts": _counter_dict(statuses),
        "metadata_only_rows": statuses.get("watch_outer_fields_metadata_only", 0),
        "capacity_candidate_rows": statuses.get(
            "blocked_outer_field_capacity_candidate",
            0,
        ),
        "target_match_count": sum(
            len(_as_list(row.get("target_matches")))
            for row in rows
        ),
    }


def _table_overlay_residual_summary(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(row.get("status") or "unknown") for row in rows)
    hypotheses = Counter(
        str(item)
        for row in rows
        for item in _as_list(row.get("remaining_minimal_hypotheses"))
    )
    weak_paths = Counter(
        str(item)
        for row in rows
        for item in _as_list(row.get("disproven_or_weak_paths"))
    )
    return {
        "rows": len(rows),
        "status_counts": _counter_dict(statuses),
        "remaining_hypothesis_counts": _counter_dict(hypotheses),
        "weak_path_counts": _counter_dict(weak_paths),
        "blocked_rows": sum(
            count for status, count in statuses.items() if status.startswith("blocked")
        ),
        "local_table_cap_gap_rows": sum(
            1 for row in rows if row.get("local_table_cap_gap") is True
        ),
        "current_table_cap_matches_payload_delta_rows": sum(
            1
            for row in rows
            if row.get("current_table_cap_matches_payload_delta") is True
        ),
        "raw_table_newer_than_capture_rows": sum(
            1 for row in rows if row.get("raw_table_newer_than_capture") is True
        ),
        "capture_version_or_hash_rows": sum(
            1 for row in rows if row.get("capture_has_table_version_or_hash") is True
        ),
        "drop_multiplicity_candidate_rows": sum(
            1 for row in rows if row.get("drop_multiplicity_candidate") is True
        ),
        "activity_overlay_direct_candidate_rows": sum(
            1 for row in rows if row.get("activity_overlay_direct_candidate") is True
        ),
        "server_transform_open_rows": sum(
            1
            for row in rows
            if "server_side_settlement_expansion_or_source_transform"
            in _as_list(row.get("remaining_minimal_hypotheses"))
        ),
    }


def _add_requirement(
    requirements: list[dict[str, str]],
    requirement: str,
    why: str,
) -> None:
    if any(row.get("requirement") == requirement for row in requirements):
        return
    requirements.append({"requirement": requirement, "why": why})


def _requirements(
    *,
    source_summary: Mapping[str, Any],
    payload_summary: Mapping[str, Any],
    numeric_action_summary: Mapping[str, Any],
    session_gap_summary: Mapping[str, Any],
    payload_table_gap_summary: Mapping[str, Any],
    payload_outer_field_summary: Mapping[str, Any],
    table_overlay_residual_summary: Mapping[str, Any],
    guard_row: Mapping[str, Any],
) -> list[dict[str, str]]:
    reqs: list[dict[str, str]] = []
    if _int(source_summary.get("session_capacity_source_semantics_rows")) > 0:
        _add_requirement(
            reqs,
            "parse_numeric_action_result_for_session_capacity_semantics",
            "unique round overflow is payload-verified but lacks public-total/full-action confirmation.",
        )
    if _int(payload_summary.get("numeric_only_result_rows")) > 0:
        _add_requirement(
            reqs,
            "decode_numeric_only_action_result_fields",
            "numeric-only action result rows expose result fields/action ids but no item payload.",
        )
    if (
        _int(source_summary.get("session_capacity_source_semantics_rows")) > 0
        and _int(numeric_action_summary.get("rows")) > 0
        and _int(numeric_action_summary.get("session_capacity_signal_rows")) == 0
        and _int(numeric_action_summary.get("non_session_expected_rows")) > 0
    ):
        _add_requirement(
            reqs,
            "find_session_capacity_source_beyond_numeric_bucket_cells",
            "numeric action results match bucket semantics and do not explain session-capacity overflow.",
        )
    if (
        _int(source_summary.get("session_capacity_source_semantics_rows")) > 0
        and _int(session_gap_summary.get("unresolved_session_capacity_rows")) > 0
        and _int(session_gap_summary.get("bucket_only_blocked_rows")) > 0
    ):
        _add_requirement(
            reqs,
            "resolve_session_capacity_without_exact_event_source",
            "event-source audit finds no exact session total-count source for the session-capacity blocker.",
        )
    if _int(payload_table_gap_summary.get("blocked_rows")) > 0:
        _add_requirement(
            reqs,
            "resolve_payload_verified_table_cap_gap_without_full_source",
            "settlement payload verifies inventory while table caps and event sources do not explain the over-cap row.",
        )
    if _int(payload_outer_field_summary.get("capacity_candidate_rows")) > 0:
        _add_requirement(
            reqs,
            "decode_outer_field_capacity_candidate",
            "outer/payload numeric fields match count or capacity targets and need decoding.",
        )
    elif (
        _int(payload_table_gap_summary.get("blocked_rows")) > 0
        and _int(payload_outer_field_summary.get("metadata_only_rows")) > 0
    ):
        _add_requirement(
            reqs,
            "check_table_overlay_or_server_side_after_outer_fields_metadata_only",
            "outer/payload fields look like metadata, leaving table overlay or server-side source transform as the remaining path.",
        )
    if _int(table_overlay_residual_summary.get("blocked_rows")) > 0:
        _add_requirement(
            reqs,
            "resolve_current_raw_table_overlay_or_server_transform_residual",
            "current raw table/drop/activity context still cannot explain payload-verified over-cap settlement inventory.",
        )
    if (
        _int(source_summary.get("unique_drop_ref_only_overflow_rows")) > 0
        or _int(source_summary.get("instance_drop_ref_only_overflow_rows")) > 0
    ):
        _add_requirement(
            reqs,
            "inspect_drop_ref_source_semantics_or_overlay",
            "drop-ref-only residuals remain after temp/zodiac and drop-universe coverage.",
        )
    if _int(source_summary.get("instance_round_overflow_rows")) > 0:
        _add_requirement(
            reqs,
            "inspect_instance_round_source_semantics_detail",
            "instance-level round overflow exists without unique-round promotion evidence.",
        )
    if _int(source_summary.get("activity_extras_only_rows")) > 0:
        _add_requirement(
            reqs,
            "separate_activity_extras_from_capacity_overflow",
            "activity-extras-only rows explain some drop-ref gaps but not the session-capacity row.",
        )
    if _as_mapping(guard_row):
        _add_requirement(
            reqs,
            "link_parser_result_back_to_guard_loss_cases",
            "this map overlaps v3 practical guard coverage loss and must be verified before guard tuning.",
        )
    return reqs


def _status(requirements: list[Mapping[str, Any]], source_summary: Mapping[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    req_names = {str(row.get("requirement")) for row in requirements}
    if "parse_numeric_action_result_for_session_capacity_semantics" in req_names:
        reasons.append("session_capacity_source_parser_required")
        return "blocked_session_capacity_source_parser_required", reasons
    if "inspect_drop_ref_source_semantics_or_overlay" in req_names:
        reasons.append("drop_ref_source_parser_required")
        return "blocked_drop_ref_source_parser_required", reasons
    if _int(source_summary.get("activity_extras_only_rows")) > 0:
        reasons.append("activity_extras_parser_watch")
        return "watch_activity_extras_source_parser_required", reasons
    if requirements:
        reasons.append("source_parser_watch")
        return "watch_source_parser_required", reasons
    return "watch_no_parser_requirement_detected", reasons


def _source_example(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "file": row.get("file"),
        "map_id": row.get("map_id"),
        "unique_residual_mode": row.get("unique_residual_mode"),
        "mechanism_class": row.get("mechanism_class"),
        "source_context_class": row.get("source_context_class"),
        "source_evidence_class": row.get("source_evidence_class"),
        "inventory_count": row.get("inventory_count"),
        "unique_non_temp_item_id_count": row.get("unique_non_temp_item_id_count"),
        "bidmap_items_per_session_max": row.get("bidmap_items_per_session_max"),
        "bidmap_raw_round_cap_max": row.get("bidmap_raw_round_cap_max"),
        "unique_round_cap_excess_after_temp_zodiac_count": row.get(
            "unique_round_cap_excess_after_temp_zodiac_count"
        ),
        "unique_drop_ref_excess_after_temp_zodiac_count": row.get(
            "unique_drop_ref_excess_after_temp_zodiac_count"
        ),
        "non_zodiac_missing_from_drop_universe_count": row.get(
            "non_zodiac_missing_from_drop_universe_count"
        ),
        "event_action_result_count_all": row.get("event_action_result_count_all"),
        "event_action_observed_item_count_max": row.get(
            "event_action_observed_item_count_max"
        ),
        "event_public_total_count_values": row.get("event_public_total_count_values"),
        "event_public_total_match": row.get("event_public_total_match"),
    }


def _payload_example(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "file": row.get("file"),
        "map_id": row.get("map_id"),
        "source_context_class": row.get("source_context_class"),
        "mechanism_class": row.get("mechanism_class"),
        "unique_round_excess_after_temp": row.get("unique_round_excess_after_temp"),
        "map_id_holdout_covered": row.get("map_id_holdout_covered"),
        "prebid_cse_candidate_windows": row.get("prebid_cse_candidate_windows"),
        "prebid_pressure_windows": row.get("prebid_pressure_windows"),
        "source_action_payload_shape_class": row.get(
            "source_action_payload_shape_class"
        ),
        "source_action_ids": row.get("source_action_ids"),
        "source_action_result_fields": row.get("source_action_result_fields"),
        "source_action_result_blocks": row.get("source_action_result_blocks"),
        "source_action_item_payload_blocks": row.get(
            "source_action_item_payload_blocks"
        ),
        "source_action_observed_item_count": row.get(
            "source_action_observed_item_count"
        ),
    }


def _numeric_action_example(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "file": row.get("file"),
        "map_id": row.get("map_id"),
        "sort_id": row.get("sort_id"),
        "message_id": row.get("message_id"),
        "block_source": row.get("block_source"),
        "action_id": row.get("action_id"),
        "result": row.get("result"),
        "result_field": row.get("result_field"),
        "expected_semantic": row.get("expected_semantic"),
        "expected_path": row.get("expected_path"),
        "expected_value": row.get("expected_value"),
        "expected_match": row.get("expected_match"),
        "parser_implication": row.get("parser_implication"),
        "inventory_total_item_count": _as_mapping(row.get("inventory")).get(
            "total_item_count"
        ),
        "inventory_warehouse_total_cells": _as_mapping(row.get("inventory")).get(
            "warehouse_total_cells"
        ),
    }


def _session_gap_example(row: Mapping[str, Any]) -> dict[str, Any]:
    digest = _as_mapping(row.get("event_source_digest"))
    return {
        "file": row.get("file"),
        "map_id": row.get("map_id"),
        "status": row.get("status"),
        "unique_residual_mode": row.get("unique_residual_mode"),
        "mechanism_class": row.get("mechanism_class"),
        "source_context_class": row.get("source_context_class"),
        "inventory": digest.get("inventory"),
        "session_count_source_count": digest.get("session_count_source_count"),
        "warehouse_cells_source_count": digest.get("warehouse_cells_source_count"),
        "bucket_source_count": digest.get("bucket_source_count"),
        "action_id_counts": digest.get("action_id_counts"),
        "public_info_id_counts": digest.get("public_info_id_counts"),
        "skill_id_counts": digest.get("skill_id_counts"),
    }


def _payload_table_gap_example(row: Mapping[str, Any]) -> dict[str, Any]:
    table = _as_mapping(row.get("table_delta"))
    payload = _as_mapping(row.get("payload"))
    event_payload = _as_mapping(row.get("event_payload"))
    return {
        "file": row.get("file"),
        "map_id": row.get("map_id"),
        "status": row.get("status"),
        "reasons": list(row.get("reasons") or []),
        "next_checks": list(row.get("next_checks") or []),
        "inventory_count": table.get("inventory_count"),
        "unique_non_temp_item_id_count": table.get("unique_non_temp_item_id_count"),
        "bidmap_items_per_session_max": table.get("bidmap_items_per_session_max"),
        "bidmap_raw_round_cap_max": table.get("bidmap_raw_round_cap_max"),
        "unique_non_temp_minus_bidmap_raw_round_cap": table.get(
            "unique_non_temp_minus_bidmap_raw_round_cap"
        ),
        "inventory_slot_count": payload.get("inventory_slot_count"),
        "occupied_slot_count": payload.get("occupied_slot_count"),
        "raw_item_candidate_count": payload.get("raw_item_candidate_count"),
        "raw_candidate_inventory_delta": payload.get(
            "raw_candidate_inventory_delta"
        ),
        "occupied_slot_inventory_delta": payload.get(
            "occupied_slot_inventory_delta"
        ),
        "full_action_payload_count": event_payload.get("full_action_payload_count"),
        "full_skill_payload_count": event_payload.get("full_skill_payload_count"),
        "skill_observed_item_count_max": event_payload.get(
            "skill_observed_item_count_max"
        ),
    }


def _payload_outer_field_example(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "file": row.get("file"),
        "map_id": row.get("map_id"),
        "status": row.get("status"),
        "metadata_matches": dict(_as_mapping(row.get("metadata_matches"))),
        "target_matches": list(row.get("target_matches") or []),
        "settlement_loss_units": row.get("settlement_loss_units"),
        "payload_field20_epoch_delta_seconds": row.get(
            "payload_field20_epoch_delta_seconds"
        ),
        "next_checks": list(row.get("next_checks") or []),
    }


def _table_overlay_residual_example(row: Mapping[str, Any]) -> dict[str, Any]:
    context = _as_mapping(row.get("current_table_context"))
    bidmap = _as_mapping(context.get("bidmap"))
    drop = _as_mapping(context.get("drop"))
    activity = _as_mapping(context.get("activity_overlay"))
    return {
        "file": row.get("file"),
        "map_id": row.get("map_id"),
        "status": row.get("status"),
        "mechanism_class": row.get("mechanism_class"),
        "local_table_cap_gap": row.get("local_table_cap_gap"),
        "current_table_cap_matches_payload_delta": row.get(
            "current_table_cap_matches_payload_delta"
        ),
        "raw_tables_file_version": context.get("raw_tables_file_version"),
        "raw_table_newer_than_capture": row.get("raw_table_newer_than_capture"),
        "capture_has_table_version_or_hash": row.get(
            "capture_has_table_version_or_hash"
        ),
        "bidmap_items_per_session_max": bidmap.get("items_per_session_max"),
        "bidmap_raw_round_cap_max": bidmap.get("round_cap_max"),
        "drop_leaf_n_max_max": drop.get("leaf_n_max_max"),
        "activity_overlay_direct_candidate": row.get(
            "activity_overlay_direct_candidate"
        ),
        "activity_map_range": activity.get("map_activity_range"),
        "remaining_minimal_hypotheses": list(
            row.get("remaining_minimal_hypotheses") or []
        ),
        "disproven_or_weak_paths": list(row.get("disproven_or_weak_paths") or []),
    }


def _top_source_examples(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (
            str(row.get("mechanism_class") or "") != "session_capacity_source_semantics",
            -_int(row.get("unique_round_cap_excess_after_temp_zodiac_count")),
            -_int(row.get("unique_drop_ref_excess_after_temp_zodiac_count")),
            str(row.get("file") or ""),
        ),
    )
    return [_source_example(row) for row in ranked[:MAX_EXAMPLES]]


def _top_payload_examples(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (
            str(row.get("source_action_payload_shape_class") or "") != "numeric_only_result",
            row.get("map_id_holdout_covered") is not False,
            -_int(row.get("prebid_cse_candidate_windows")),
            str(row.get("file") or ""),
        ),
    )
    return [_payload_example(row) for row in ranked[:MAX_EXAMPLES]]


def _top_numeric_action_examples(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (
            row.get("parser_implication") != "not_session_capacity_signal",
            row.get("expected_match") is not True,
            str(row.get("file") or ""),
            _int(row.get("sort_id")),
        ),
    )
    return [_numeric_action_example(row) for row in ranked[:MAX_EXAMPLES]]


def _top_session_gap_examples(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (
            not str(row.get("status") or "").startswith("blocked"),
            row.get("mechanism_class") != "session_capacity_source_semantics",
            str(row.get("file") or ""),
        ),
    )
    return [_session_gap_example(row) for row in ranked[:MAX_EXAMPLES]]


def _top_payload_table_gap_examples(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (
            not str(row.get("status") or "").startswith("blocked"),
            str(row.get("file") or ""),
        ),
    )
    return [_payload_table_gap_example(row) for row in ranked[:MAX_EXAMPLES]]


def _top_payload_outer_field_examples(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (
            row.get("status") != "watch_outer_fields_metadata_only",
            str(row.get("file") or ""),
        ),
    )
    return [_payload_outer_field_example(row) for row in ranked[:MAX_EXAMPLES]]


def _top_table_overlay_residual_examples(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (
            not str(row.get("status") or "").startswith("blocked"),
            str(row.get("file") or ""),
        ),
    )
    return [_table_overlay_residual_example(row) for row in ranked[:MAX_EXAMPLES]]


def summarize_source_parser_requirements(
    *,
    source_semantics: Mapping[str, Any],
    guard_loss_context: Mapping[str, Any] | None = None,
    cse: Mapping[str, Any] | None = None,
    payload_only: Mapping[str, Any] | None = None,
    numeric_action_semantics: Mapping[str, Any] | None = None,
    session_capacity_source_gap: Mapping[str, Any] | None = None,
    payload_table_gap: Mapping[str, Any] | None = None,
    payload_outer_fields: Mapping[str, Any] | None = None,
    table_overlay_residual: Mapping[str, Any] | None = None,
    focus_maps: list[str] | None = None,
) -> dict[str, Any]:
    source_by_map = _rows_by_map(_as_list(source_semantics.get("detail_rows")))
    guard_by_map = _index_guard_rows(guard_loss_context)
    cse_entries = _index_cse_entries(cse)
    payload_by_map = _payload_rows_by_map(payload_only)
    numeric_action_by_map = _numeric_action_rows_by_map(numeric_action_semantics)
    session_gap_by_map = _session_gap_rows_by_map(session_capacity_source_gap)
    payload_table_gap_by_map = _payload_table_gap_rows_by_map(payload_table_gap)
    payload_outer_by_map = _payload_outer_field_rows_by_map(payload_outer_fields)
    table_overlay_residual_by_map = _table_overlay_residual_rows_by_map(
        table_overlay_residual
    )
    maps = [str(map_id) for map_id in (focus_maps or [])]
    if not maps:
        maps = sorted(
            {
                *(
                    str(row.get("map_id"))
                    for row in _as_list(guard_loss_context.get("rows") if isinstance(guard_loss_context, Mapping) else [])
                    if isinstance(row, Mapping) and row.get("map_id") is not None
                ),
                *(
                    str(key)
                    for key, rows in source_by_map.items()
                    if any(
                        str(row.get("mechanism_class") or "")
                        == "session_capacity_source_semantics"
                        for row in rows
                    )
                ),
            }
        )
    rows: list[dict[str, Any]] = []
    for map_id in maps:
        source_rows = source_by_map.get(map_id, [])
        payload_rows = payload_by_map.get(map_id, [])
        numeric_action_rows = numeric_action_by_map.get(map_id, [])
        session_gap_rows = session_gap_by_map.get(map_id, [])
        payload_table_gap_rows = payload_table_gap_by_map.get(map_id, [])
        payload_outer_rows = payload_outer_by_map.get(map_id, [])
        table_overlay_residual_rows = table_overlay_residual_by_map.get(map_id, [])
        guard_row = _as_mapping(guard_by_map.get(map_id))
        cse_entry = _as_mapping(cse_entries.get(("map_id", map_id)))
        source_summary = _source_summary(source_rows)
        payload_summary = _payload_summary(payload_rows)
        numeric_action_summary = _numeric_action_semantics_summary(numeric_action_rows)
        session_gap = _session_gap_summary(session_gap_rows)
        payload_table_gap_summary = _payload_table_gap_summary(payload_table_gap_rows)
        payload_outer_summary = _payload_outer_field_summary(payload_outer_rows)
        table_overlay_residual_summary = _table_overlay_residual_summary(
            table_overlay_residual_rows
        )
        requirements = _requirements(
            source_summary=source_summary,
            payload_summary=payload_summary,
            numeric_action_summary=numeric_action_summary,
            session_gap_summary=session_gap,
            payload_table_gap_summary=payload_table_gap_summary,
            payload_outer_field_summary=payload_outer_summary,
            table_overlay_residual_summary=table_overlay_residual_summary,
            guard_row=guard_row,
        )
        status, reasons = _status(requirements, source_summary)
        rows.append(
            {
                "map_id": map_id,
                "map_family": (
                    str(source_rows[0].get("map_family"))
                    if source_rows and source_rows[0].get("map_family") is not None
                    else None
                ),
                "status": status,
                "reasons": reasons,
                "requirements": requirements,
                "guard_loss_overlap": {
                    "status": guard_row.get("status"),
                    "p90_coverage_lost_rows": _as_mapping(guard_row.get("guard")).get(
                        "p90_coverage_lost_rows"
                    ),
                    "rows": _as_mapping(guard_row.get("guard")).get("rows"),
                    "reasons": list(guard_row.get("reasons") or []),
                },
                "cse_entry": {
                    "status": cse_entry.get("status"),
                    "gate_reason": cse_entry.get("gate_reason"),
                    "archive_sessions": cse_entry.get("archive_sessions"),
                    "unique_round_overflow_rows": cse_entry.get(
                        "unique_round_overflow_rows"
                    ),
                    "session_capacity_source_semantics_rows": cse_entry.get(
                        "session_capacity_source_semantics_rows"
                    ),
                    "server_side_expansion_rows": cse_entry.get(
                        "server_side_expansion_rows"
                    ),
                    "non_zodiac_missing_max": cse_entry.get("non_zodiac_missing_max"),
                    "mechanism_classes": _count_text_to_dict(
                        cse_entry.get("mechanism_classes")
                    ),
                    "source_context_classes": _count_text_to_dict(
                        cse_entry.get("source_context_classes")
                    ),
                    "source_evidence_classes": _count_text_to_dict(
                        cse_entry.get("source_evidence_classes")
                    ),
                },
                "source_semantics": source_summary,
                "payload_only": payload_summary,
                "numeric_action_semantics": numeric_action_summary,
                "session_capacity_source_gap": session_gap,
                "payload_table_gap": payload_table_gap_summary,
                "payload_outer_fields": payload_outer_summary,
                "table_overlay_residual": table_overlay_residual_summary,
                "evidence_examples": {
                    "source_semantics": _top_source_examples(source_rows),
                    "payload_only": _top_payload_examples(payload_rows),
                    "numeric_action_semantics": _top_numeric_action_examples(
                        numeric_action_rows
                    ),
                    "session_capacity_source_gap": _top_session_gap_examples(
                        session_gap_rows
                    ),
                    "payload_table_gap": _top_payload_table_gap_examples(
                        payload_table_gap_rows
                    ),
                    "payload_outer_fields": _top_payload_outer_field_examples(
                        payload_outer_rows
                    ),
                    "table_overlay_residual": _top_table_overlay_residual_examples(
                        table_overlay_residual_rows
                    ),
                },
            }
        )
    status_counts = Counter(str(row.get("status")) for row in rows)
    requirement_counts = Counter(
        str(req.get("requirement"))
        for row in rows
        for req in _as_list(row.get("requirements"))
        if isinstance(req, Mapping)
    )
    overall_status = (
        "blocked_source_parser_required"
        if any(str(row.get("status")).startswith("blocked") for row in rows)
        else "watch_source_parser_audit_only"
    )
    return {
        "status": overall_status,
        "shadow_only": True,
        "affects_bid": False,
        "parser_required": bool(requirement_counts),
        "focus_maps": maps,
        "rows": rows,
        "summary": {
            "maps": len(rows),
            "blocked_maps": sum(
                1 for row in rows if str(row.get("status")).startswith("blocked")
            ),
            "guard_loss_overlap_maps": sum(
                1
                for row in rows
                if _as_mapping(row.get("guard_loss_overlap")).get("status") is not None
            ),
            "session_capacity_maps": sum(
                1
                for row in rows
                if _int(
                    _as_mapping(row.get("source_semantics")).get(
                        "session_capacity_source_semantics_rows"
                    )
                )
                > 0
            ),
            "drop_ref_residual_maps": sum(
                1
                for row in rows
                if _int(
                    _as_mapping(row.get("source_semantics")).get(
                        "unique_drop_ref_only_overflow_rows"
                    )
                )
                + _int(
                    _as_mapping(row.get("source_semantics")).get(
                        "instance_drop_ref_only_overflow_rows"
                    )
                )
                > 0
            ),
            "activity_extras_maps": sum(
                1
                for row in rows
                if _int(
                    _as_mapping(row.get("source_semantics")).get(
                        "activity_extras_only_rows"
                    )
                )
                > 0
            ),
            "numeric_action_semantics_maps": sum(
                1
                for row in rows
                if _int(
                    _as_mapping(row.get("numeric_action_semantics")).get("rows")
                )
                > 0
            ),
            "numeric_action_rows": sum(
                _int(_as_mapping(row.get("numeric_action_semantics")).get("rows"))
                for row in rows
            ),
            "numeric_session_capacity_signal_rows": sum(
                _int(
                    _as_mapping(row.get("numeric_action_semantics")).get(
                        "session_capacity_signal_rows"
                    )
                )
                for row in rows
            ),
            "numeric_non_session_expected_rows": sum(
                _int(
                    _as_mapping(row.get("numeric_action_semantics")).get(
                        "non_session_expected_rows"
                    )
                )
                for row in rows
            ),
            "numeric_unknown_semantic_rows": sum(
                _int(
                    _as_mapping(row.get("numeric_action_semantics")).get(
                        "unknown_semantic_rows"
                    )
                )
                for row in rows
            ),
            "session_capacity_source_gap_maps": sum(
                1
                for row in rows
                if _int(
                    _as_mapping(row.get("session_capacity_source_gap")).get("rows")
                )
                > 0
            ),
            "session_capacity_source_gap_rows": sum(
                _int(_as_mapping(row.get("session_capacity_source_gap")).get("rows"))
                for row in rows
            ),
            "session_gap_exact_session_count_source_rows": sum(
                _int(
                    _as_mapping(row.get("session_capacity_source_gap")).get(
                        "exact_session_count_source_rows"
                    )
                )
                for row in rows
            ),
            "session_gap_bucket_only_blocked_rows": sum(
                _int(
                    _as_mapping(row.get("session_capacity_source_gap")).get(
                        "bucket_only_blocked_rows"
                    )
                )
                for row in rows
            ),
            "session_gap_unresolved_session_capacity_rows": sum(
                _int(
                    _as_mapping(row.get("session_capacity_source_gap")).get(
                        "unresolved_session_capacity_rows"
                    )
                )
                for row in rows
            ),
            "payload_table_gap_maps": sum(
                1
                for row in rows
                if _int(_as_mapping(row.get("payload_table_gap")).get("rows")) > 0
            ),
            "payload_table_gap_rows": sum(
                _int(_as_mapping(row.get("payload_table_gap")).get("rows"))
                for row in rows
            ),
            "payload_table_gap_blocked_rows": sum(
                _int(_as_mapping(row.get("payload_table_gap")).get("blocked_rows"))
                for row in rows
            ),
            "payload_table_gap_payload_verified_rows": sum(
                _int(
                    _as_mapping(row.get("payload_table_gap")).get(
                        "payload_verified_rows"
                    )
                )
                for row in rows
            ),
            "payload_table_gap_no_full_event_payload_rows": sum(
                _int(
                    _as_mapping(row.get("payload_table_gap")).get(
                        "no_full_event_payload_rows"
                    )
                )
                for row in rows
            ),
            "payload_outer_field_maps": sum(
                1
                for row in rows
                if _int(_as_mapping(row.get("payload_outer_fields")).get("rows")) > 0
            ),
            "payload_outer_field_rows": sum(
                _int(_as_mapping(row.get("payload_outer_fields")).get("rows"))
                for row in rows
            ),
            "payload_outer_field_metadata_only_rows": sum(
                _int(
                    _as_mapping(row.get("payload_outer_fields")).get(
                        "metadata_only_rows"
                    )
                )
                for row in rows
            ),
            "payload_outer_field_capacity_candidate_rows": sum(
                _int(
                    _as_mapping(row.get("payload_outer_fields")).get(
                        "capacity_candidate_rows"
                    )
                )
                for row in rows
            ),
            "table_overlay_residual_maps": sum(
                1
                for row in rows
                if _int(_as_mapping(row.get("table_overlay_residual")).get("rows")) > 0
            ),
            "table_overlay_residual_rows": sum(
                _int(_as_mapping(row.get("table_overlay_residual")).get("rows"))
                for row in rows
            ),
            "table_overlay_residual_blocked_rows": sum(
                _int(
                    _as_mapping(row.get("table_overlay_residual")).get(
                        "blocked_rows"
                    )
                )
                for row in rows
            ),
            "table_overlay_residual_local_cap_gap_rows": sum(
                _int(
                    _as_mapping(row.get("table_overlay_residual")).get(
                        "local_table_cap_gap_rows"
                    )
                )
                for row in rows
            ),
            "table_overlay_residual_current_table_match_rows": sum(
                _int(
                    _as_mapping(row.get("table_overlay_residual")).get(
                        "current_table_cap_matches_payload_delta_rows"
                    )
                )
                for row in rows
            ),
            "table_overlay_residual_activity_direct_rows": sum(
                _int(
                    _as_mapping(row.get("table_overlay_residual")).get(
                        "activity_overlay_direct_candidate_rows"
                    )
                )
                for row in rows
            ),
            "table_overlay_residual_server_transform_open_rows": sum(
                _int(
                    _as_mapping(row.get("table_overlay_residual")).get(
                        "server_transform_open_rows"
                    )
                )
                for row in rows
            ),
            "status_counts": _counter_dict(status_counts),
            "requirement_counts": _counter_dict(requirement_counts),
        },
    }


def _format_counts(counts: Mapping[str, Any]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def print_summary(result: Mapping[str, Any]) -> None:
    summary = _as_mapping(result.get("summary"))
    print(
        "status={status} maps={maps} blocked_maps={blocked} guard_maps={guard_maps} "
        "session_capacity_maps={session_maps} drop_ref_maps={drop_maps} "
        "activity_extras_maps={activity_maps} requirements={requirements} "
        "statuses={statuses}".format(
            status=result.get("status"),
            maps=summary.get("maps"),
            blocked=summary.get("blocked_maps"),
            guard_maps=summary.get("guard_loss_overlap_maps"),
            session_maps=summary.get("session_capacity_maps"),
            drop_maps=summary.get("drop_ref_residual_maps"),
            activity_maps=summary.get("activity_extras_maps"),
            requirements=_format_counts(_as_mapping(summary.get("requirement_counts"))),
            statuses=_format_counts(_as_mapping(summary.get("status_counts"))),
        )
    )
    for row in _as_list(result.get("rows")):
        if not isinstance(row, Mapping):
            continue
        source = _as_mapping(row.get("source_semantics"))
        payload = _as_mapping(row.get("payload_only"))
        numeric_action = _as_mapping(row.get("numeric_action_semantics"))
        session_gap = _as_mapping(row.get("session_capacity_source_gap"))
        payload_table_gap = _as_mapping(row.get("payload_table_gap"))
        payload_outer = _as_mapping(row.get("payload_outer_fields"))
        table_overlay = _as_mapping(row.get("table_overlay_residual"))
        print(
            "map={map_id} family={family} status={status} "
            "guard_loss={guard_loss}/{guard_rows} source_rows={source_rows} "
            "session_capacity_rows={session_rows} drop_ref_unique={drop_unique} "
            "drop_ref_instance={drop_instance} activity_extras={activity_rows} "
            "payload_shapes={payload_shapes} payload_numeric={payload_numeric} "
            "numeric_action_rows={numeric_rows} numeric_session_signals={numeric_session} "
            "numeric_non_session={numeric_non_session} "
            "session_gap_rows={session_gap_rows} session_gap_exact_count={session_gap_exact} "
            "session_gap_bucket_blocked={session_gap_bucket} "
            "payload_table_gap_rows={payload_gap_rows} payload_table_gap_blocked={payload_gap_blocked} "
            "payload_outer_rows={outer_rows} payload_outer_metadata={outer_metadata} "
            "table_overlay_residual_rows={table_overlay_rows} "
            "table_overlay_residual_blocked={table_overlay_blocked} "
            "table_overlay_server_open={table_overlay_server_open} "
            "requirements={requirements}".format(
                map_id=row.get("map_id"),
                family=row.get("map_family"),
                status=row.get("status"),
                guard_loss=_as_mapping(row.get("guard_loss_overlap")).get(
                    "p90_coverage_lost_rows"
                ),
                guard_rows=_as_mapping(row.get("guard_loss_overlap")).get("rows"),
                source_rows=source.get("rows"),
                session_rows=source.get("session_capacity_source_semantics_rows"),
                drop_unique=source.get("unique_drop_ref_only_overflow_rows"),
                drop_instance=source.get("instance_drop_ref_only_overflow_rows"),
                activity_rows=source.get("activity_extras_only_rows"),
                payload_shapes=_format_counts(
                    _as_mapping(payload.get("source_action_payload_shape_counts"))
                ),
                payload_numeric=payload.get("numeric_only_result_rows"),
                numeric_rows=numeric_action.get("rows"),
                numeric_session=numeric_action.get("session_capacity_signal_rows"),
                numeric_non_session=numeric_action.get("non_session_expected_rows"),
                session_gap_rows=session_gap.get("rows"),
                session_gap_exact=session_gap.get("exact_session_count_source_rows"),
                session_gap_bucket=session_gap.get("bucket_only_blocked_rows"),
                payload_gap_rows=payload_table_gap.get("rows"),
                payload_gap_blocked=payload_table_gap.get("blocked_rows"),
                outer_rows=payload_outer.get("rows"),
                outer_metadata=payload_outer.get("metadata_only_rows"),
                table_overlay_rows=table_overlay.get("rows"),
                table_overlay_blocked=table_overlay.get("blocked_rows"),
                table_overlay_server_open=table_overlay.get(
                    "server_transform_open_rows"
                ),
                requirements=",".join(
                    str(_as_mapping(req).get("requirement"))
                    for req in _as_list(row.get("requirements"))
                )
                or "-",
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize source-parser requirements for v3 capacity/source blockers.",
    )
    parser.add_argument("--source-semantics-json", type=Path, default=None)
    parser.add_argument("--guard-loss-source-context-json", type=Path, default=DEFAULT_GUARD_LOSS_CONTEXT)
    parser.add_argument("--capacity-source-expansion-json", type=Path, default=DEFAULT_CSE)
    parser.add_argument("--payload-only-json", type=Path, default=DEFAULT_PAYLOAD_ONLY)
    parser.add_argument("--numeric-action-semantics-json", type=Path, default=DEFAULT_NUMERIC_ACTION_SEMANTICS)
    parser.add_argument("--session-capacity-source-gap-json", type=Path, default=DEFAULT_SESSION_CAPACITY_SOURCE_GAP)
    parser.add_argument("--payload-table-gap-json", type=Path, default=DEFAULT_PAYLOAD_TABLE_GAP)
    parser.add_argument("--payload-outer-fields-json", type=Path, default=DEFAULT_PAYLOAD_OUTER_FIELDS)
    parser.add_argument("--table-overlay-residual-json", type=Path, default=DEFAULT_TABLE_OVERLAY_RESIDUAL)
    parser.add_argument("--focus-map", action="append", default=[])
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    source_path = args.source_semantics_json or _default_source_path()
    result = summarize_source_parser_requirements(
        source_semantics=_load_json(source_path),
        guard_loss_context=(
            _load_json(args.guard_loss_source_context_json)
            if args.guard_loss_source_context_json.exists()
            else None
        ),
        cse=(
            _load_json(args.capacity_source_expansion_json)
            if args.capacity_source_expansion_json.exists()
            else None
        ),
        payload_only=(
            _load_json(args.payload_only_json)
            if args.payload_only_json.exists()
            else None
        ),
        numeric_action_semantics=(
            _load_json(args.numeric_action_semantics_json)
            if args.numeric_action_semantics_json.exists()
            else None
        ),
        session_capacity_source_gap=(
            _load_json(args.session_capacity_source_gap_json)
            if args.session_capacity_source_gap_json.exists()
            else None
        ),
        payload_table_gap=(
            _load_json(args.payload_table_gap_json)
            if args.payload_table_gap_json.exists()
            else None
        ),
        payload_outer_fields=(
            _load_json(args.payload_outer_fields_json)
            if args.payload_outer_fields_json.exists()
            else None
        ),
        table_overlay_residual=(
            _load_json(args.table_overlay_residual_json)
            if args.table_overlay_residual_json.exists()
            else None
        ),
        focus_maps=args.focus_map,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
