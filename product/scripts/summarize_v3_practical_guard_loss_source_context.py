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
DEFAULT_READINESS = ROOT / ".tmp" / "codex" / "v3_readiness_guard_loss_context_scp_stability_latest.json"
DEFAULT_SOURCE_SEMANTICS = ROOT / ".tmp" / "codex" / "v3_settlement_source_semantics_details_latest.json"
DEFAULT_CSE = ROOT / "data" / "processed" / "v3_capacity_source_expansion_shadow.json"
DEFAULT_CAPACITY_TABLE = ROOT / ".tmp" / "codex" / "v3_capacity_table_audit_detail_summary_latest.json"
DEFAULT_CAPACITY_ACQUISITION = ROOT / ".tmp" / "codex" / "v3_capacity_table_acquisition_audit_latest.json"
DEFAULT_ACTIVITY_MAPPING = ROOT / ".tmp" / "codex" / "v3_activity_mapping_rankmap_latest.json"
MAX_EXAMPLES_PER_SECTION = 3


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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


def _count_text_to_dict(value: Any) -> dict[str, int]:
    if isinstance(value, Mapping):
        return {str(key): _int(count) for key, count in value.items()}
    text = str(value or "").strip()
    if not text:
        return {}
    result: dict[str, int] = {}
    for chunk in text.split(","):
        if ":" not in chunk:
            continue
        key, raw_count = chunk.split(":", 1)
        key = key.strip()
        if key:
            result[key] = _int(raw_count)
    return result


def _compact(row: Mapping[str, Any], keys: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in keys:
        value = row.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        out[key] = value
    return out


def _file_sort_key(row: Mapping[str, Any]) -> str:
    return str(row.get("file") or row.get("file_ref") or row.get("path") or "")


def _top_examples(
    rows: list[Mapping[str, Any]],
    *,
    score_keys: list[str],
    formatter,
    limit: int = MAX_EXAMPLES_PER_SECTION,
) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (
            -max((_int(row.get(key)) for key in score_keys), default=0),
            _file_sort_key(row),
        ),
    )
    return [formatter(row) for row in ranked[:limit]]


def _source_semantics_example(row: Mapping[str, Any]) -> dict[str, Any]:
    return _compact(
        row,
        [
            "file",
            "map_family",
            "mechanism_class",
            "source_context_class",
            "source_evidence_class",
            "unique_residual_mode",
            "inventory_count",
            "non_temp_inventory_count",
            "unique_non_temp_item_id_count",
            "bidmap_raw_round_cap_max",
            "bidmap_items_per_session_max",
            "unique_round_cap_excess_after_temp_zodiac_count",
            "unique_drop_ref_excess_after_temp_zodiac_count",
            "non_zodiac_missing_from_drop_universe_count",
            "event_action_result_count_all",
            "event_action_observed_item_count_max",
            "event_latest_inventory_count",
            "event_public_total_count_values",
            "event_public_total_match",
        ],
    )


def _capacity_table_example(row: Mapping[str, Any]) -> dict[str, Any]:
    return _compact(
        row,
        [
            "file",
            "file_ref",
            "map_family",
            "semantic_status",
            "residual_mode",
            "total_count_source",
            "total_count_target",
            "truth_item_count",
            "prior_items_per_session_max",
            "latest_item_count",
            "truth_prior_max_delta",
            "round_cap_excess_after_temp_zodiac_count",
            "drop_ref_excess_after_temp_zodiac_count",
            "non_zodiac_missing_from_drop_universe_count",
            "public_total_count_values",
            "full_observed_action_ids",
        ],
    )


def _capacity_acquisition_example(row: Mapping[str, Any]) -> dict[str, Any]:
    return _compact(
        row,
        [
            "file",
            "file_ref",
            "map_family",
            "acquisition_route",
            "next_check",
            "source_strength",
            "mechanism_candidate",
            "source_signal",
            "table_context",
            "semantic_status",
            "residual_mode",
            "total_count_source",
            "truth_item_count",
            "prior_items_per_session_max",
            "latest_item_count",
            "truth_prior_max_delta",
            "round_cap_excess_after_temp_zodiac_count",
            "drop_ref_excess_after_temp_zodiac_count",
            "non_zodiac_missing_from_drop_universe_count",
        ],
    )


def _activity_candidate_example(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return _compact(
        candidate,
        [
            "status",
            "candidate_map_id",
            "drop_pool_id",
            "scheme",
            "missing_item_rate",
            "missing_item_count",
            "zero_item_probability_items",
            "zero_quality_items",
            "log_likelihood_per_item",
            "item_log_likelihood_per_item",
        ],
    )


def _activity_file_example(row: Mapping[str, Any]) -> dict[str, Any]:
    out = _compact(
        row,
        [
            "file",
            "status",
            "inventory_count",
            "best_scheme",
            "best_item_scheme",
            "best_margin_per_item",
            "best_item_margin_per_item",
        ],
    )
    candidates = [
        _activity_candidate_example(candidate)
        for candidate in _as_list(row.get("candidates"))[:MAX_EXAMPLES_PER_SECTION]
        if isinstance(candidate, Mapping)
    ]
    if candidates:
        out["candidate_examples"] = candidates
    return out


def _guard_metrics(doc: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = doc.get("v3_practical_archive_live_guard_metrics")
    if isinstance(nested, Mapping):
        return nested
    return doc


def _guard_loss_rows(stats: Mapping[str, Any]) -> int:
    case_summary = _as_mapping(stats.get("v3_practical_guard_case_summary"))
    return _int(case_summary.get("p90_coverage_lost_rows"))


def _guard_case_rows(stats: Mapping[str, Any]) -> int:
    case_summary = _as_mapping(stats.get("v3_practical_guard_case_summary"))
    return _int(case_summary.get("rows"))


def _guard_context(stats: Mapping[str, Any]) -> Mapping[str, Any]:
    case_summary = _as_mapping(stats.get("v3_practical_guard_case_summary"))
    return _as_mapping(case_summary.get("p90_coverage_loss_context"))


def _focus_maps(
    guard: Mapping[str, Any],
    requested: list[str],
) -> list[str]:
    if requested:
        return [str(map_id) for map_id in requested]
    by_map_id = _as_mapping(guard.get("by_map_id"))
    maps = [
        (str(map_id), _guard_loss_rows(_as_mapping(stats)))
        for map_id, stats in by_map_id.items()
    ]
    return [
        map_id
        for map_id, loss_rows in sorted(
            (item for item in maps if item[1] > 0),
            key=lambda item: (-item[1], item[0]),
        )
    ]


def _index_cse_entries(cse: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    entries: dict[tuple[str, str], Mapping[str, Any]] = {}
    for entry in _as_list(cse.get("entries")):
        if not isinstance(entry, Mapping):
            continue
        scope = str(entry.get("scope") or "")
        group = str(entry.get("group") or "")
        if scope and group:
            entries[(scope, group)] = entry
    return entries


def _rows_by_map(rows: list[Any]) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        map_id = row.get("map_id")
        if map_id is None:
            continue
        grouped[str(map_id)].append(row)
    return grouped


def _aggregate_rows(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    mechanisms = Counter(str(row.get("mechanism_class") or "unknown") for row in rows)
    source_contexts = Counter(
        str(row.get("source_context_class") or "unknown") for row in rows
    )
    source_evidence = Counter(
        str(row.get("source_evidence_class") or "unknown") for row in rows
    )
    residual_modes = Counter(
        str(row.get("unique_residual_mode") or "unknown") for row in rows
    )
    unique_round_rows = sum(
        1
        for row in rows
        if str(row.get("unique_residual_mode") or "")
        == "unique_round_cap_overflow_after_temp"
    )
    instance_round_rows = sum(
        1
        for row in rows
        if str(row.get("unique_residual_mode") or "").startswith("instance_round")
    )
    drop_only_rows = sum(
        1
        for row in rows
        if str(row.get("unique_residual_mode") or "")
        == "unique_drop_ref_only_overflow_after_temp"
    )
    round_excess = [
        _int(row.get("unique_round_cap_excess_after_temp_zodiac_count"))
        for row in rows
    ]
    drop_excess = [
        _int(row.get("unique_drop_ref_excess_after_temp_zodiac_count"))
        for row in rows
    ]
    return {
        "rows": len(rows),
        "mechanism_classes": _counter_dict(mechanisms),
        "source_context_classes": _counter_dict(source_contexts),
        "source_evidence_classes": _counter_dict(source_evidence),
        "residual_modes": _counter_dict(residual_modes),
        "unique_round_overflow_rows": unique_round_rows,
        "instance_round_overflow_rows": instance_round_rows,
        "drop_ref_only_overflow_rows": drop_only_rows,
        "max_round_excess_after_temp": max(round_excess) if round_excess else 0,
        "max_drop_ref_excess_after_temp": max(drop_excess) if drop_excess else 0,
    }


def _aggregate_capacity_table(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "rows": len(rows),
        "semantic_status_counts": _counter_dict(
            Counter(str(row.get("semantic_status") or "unknown") for row in rows)
        ),
        "residual_mode_counts": _counter_dict(
            Counter(str(row.get("residual_mode") or "unknown") for row in rows)
        ),
        "total_count_source_counts": _counter_dict(
            Counter(str(row.get("total_count_source") or "unknown") for row in rows)
        ),
        "max_truth_prior_delta": max(
            (_int(row.get("truth_prior_max_delta")) for row in rows),
            default=0,
        ),
    }


def _aggregate_acquisition(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "rows": len(rows),
        "acquisition_route_counts": _counter_dict(
            Counter(str(row.get("acquisition_route") or "unknown") for row in rows)
        ),
        "next_check_counts": _counter_dict(
            Counter(str(row.get("next_check") or "unknown") for row in rows)
        ),
        "source_strength_counts": _counter_dict(
            Counter(str(row.get("source_strength") or "unknown") for row in rows)
        ),
    }


def _activity_mapping_by_map(
    activity_mapping: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(activity_mapping, Mapping):
        return {}
    map_rows = {
        str(row.get("map_id")): row
        for row in _as_list(activity_mapping.get("map_results"))
        if isinstance(row, Mapping) and row.get("map_id") is not None
    }
    file_rows_by_map: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in _as_list(activity_mapping.get("file_results")):
        if not isinstance(row, Mapping) or row.get("map_id") is None:
            continue
        file_rows_by_map[str(row.get("map_id"))].append(row)

    out: dict[str, dict[str, Any]] = {}
    for map_id in sorted(set(map_rows) | set(file_rows_by_map)):
        map_row = _as_mapping(map_rows.get(map_id))
        file_rows = file_rows_by_map.get(map_id, [])
        candidate_statuses: Counter[str] = Counter()
        candidate_map_ids: Counter[str] = Counter()
        drop_pool_ids: Counter[str] = Counter()
        missing_item_rates: list[float] = []
        for file_row in file_rows:
            for candidate in _as_list(file_row.get("candidates")):
                if not isinstance(candidate, Mapping):
                    continue
                candidate_statuses[str(candidate.get("status") or "unknown")] += 1
                candidate_map_ids[str(candidate.get("candidate_map_id") or "unknown")] += 1
                drop_pool_ids[str(candidate.get("drop_pool_id") or "unknown")] += 1
                missing_rate = _float(candidate.get("missing_item_rate"))
                if missing_rate is not None:
                    missing_item_rates.append(missing_rate)
        winner_counts = _as_mapping(map_row.get("winner_counts"))
        item_winner_counts = _as_mapping(map_row.get("item_winner_counts"))
        if not map_row:
            status = "no_activity_mapping_rows"
        elif any(key != "ok" for key in candidate_statuses):
            status = "blocked_candidate_status"
        elif len(winner_counts) > 1 or len(item_winner_counts) > 1:
            status = "watch_mixed_activity_mapping"
        else:
            status = "watch_single_activity_mapping"
        out[map_id] = {
            "status": status,
            "files": map_row.get("files") or len(file_rows),
            "winner_counts": dict(winner_counts),
            "item_winner_counts": dict(item_winner_counts),
            "candidate_status_counts": _counter_dict(candidate_statuses),
            "candidate_map_ids": _counter_dict(candidate_map_ids),
            "drop_pool_ids": _counter_dict(drop_pool_ids),
            "rankmap_labels": dict(_as_mapping(map_row.get("rankmap_labels"))),
            "rankmap_category_weight_profiles": dict(
                _as_mapping(map_row.get("rankmap_category_weight_profiles"))
            ),
            "missing_item_rate_max": max(missing_item_rates)
            if missing_item_rates
            else None,
            "best_margin_per_item": map_row.get("best_margin_per_item"),
            "best_item_margin_per_item": map_row.get("best_item_margin_per_item"),
            "file_examples": [
                _activity_file_example(row)
                for row in sorted(file_rows, key=_file_sort_key)[:MAX_EXAMPLES_PER_SECTION]
            ],
        }
    return out


def _entry_slice(entry: Mapping[str, Any]) -> dict[str, Any]:
    if not entry:
        return {}
    return {
        "status": entry.get("status"),
        "gate_reason": entry.get("gate_reason"),
        "source": entry.get("source"),
        "archive_sessions": entry.get("archive_sessions"),
        "unique_round_overflow_rows": entry.get("unique_round_overflow_rows"),
        "session_capacity_source_semantics_rows": entry.get(
            "session_capacity_source_semantics_rows"
        ),
        "server_side_expansion_rows": entry.get("server_side_expansion_rows"),
        "non_zodiac_missing_max": entry.get("non_zodiac_missing_max"),
        "mechanism_classes": _count_text_to_dict(entry.get("mechanism_classes")),
        "source_context_classes": _count_text_to_dict(entry.get("source_context_classes")),
        "source_evidence_classes": _count_text_to_dict(entry.get("source_evidence_classes")),
    }


def _classification(
    *,
    guard_loss_rows: int,
    cse_map: Mapping[str, Any],
    source_summary: Mapping[str, Any],
    capacity_summary: Mapping[str, Any],
    acquisition_summary: Mapping[str, Any],
    activity_mapping_summary: Mapping[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if guard_loss_rows <= 0:
        return "no_guard_loss", reasons
    if str(cse_map.get("status") or "") == "blocked_drop_universe_gap_shadow_only":
        reasons.append("cse_exact_drop_universe_gap")
    if _int(cse_map.get("non_zodiac_missing_max")) > 0:
        reasons.append("cse_non_zodiac_drop_universe_gap")
    if _int(cse_map.get("session_capacity_source_semantics_rows")) > 0:
        reasons.append("cse_exact_session_capacity_source_semantics")
    if _int(cse_map.get("server_side_expansion_rows")) > 0:
        reasons.append("cse_exact_server_side_expansion")
    if _int(source_summary.get("unique_round_overflow_rows")) > 0:
        reasons.append("source_semantics_unique_round_overflow")
    if _int(source_summary.get("instance_round_overflow_rows")) > 0:
        reasons.append("source_semantics_instance_round_overflow")
    if _int(source_summary.get("drop_ref_only_overflow_rows")) > 0:
        reasons.append("source_semantics_drop_ref_only_overflow")
    if _int(capacity_summary.get("rows")) > 0:
        reasons.append("capacity_table_detail_overlap")
    if _int(acquisition_summary.get("rows")) > 0:
        reasons.append("capacity_acquisition_example_overlap")
    activity_status = str(activity_mapping_summary.get("status") or "")
    if activity_status == "watch_mixed_activity_mapping":
        reasons.append("activity_mapping_mixed_winner")
    elif activity_status == "watch_single_activity_mapping":
        reasons.append("activity_mapping_single_winner")
    elif activity_status == "blocked_candidate_status":
        reasons.append("activity_mapping_candidate_status_blocked")

    if any("drop_universe_gap" in reason for reason in reasons):
        return "blocked_drop_universe_or_activity_overlay", reasons
    if any(
        reason
        in {
            "cse_exact_session_capacity_source_semantics",
            "cse_exact_server_side_expansion",
            "source_semantics_unique_round_overflow",
        }
        for reason in reasons
    ):
        return "blocked_cse_source_semantics_intersection", reasons
    if reasons:
        return "watch_source_context_intersection", reasons
    return "watch_guard_loss_without_matching_source_detail", reasons


def _add_next_check(
    checks: list[dict[str, str]],
    check: str,
    why: str,
) -> None:
    if any(row.get("check") == check for row in checks):
        return
    checks.append({"check": check, "why": why})


def _next_checks(
    *,
    status: str,
    reasons: list[str],
    source_summary: Mapping[str, Any],
    capacity_summary: Mapping[str, Any],
    acquisition_summary: Mapping[str, Any],
    activity_mapping_summary: Mapping[str, Any],
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    reason_set = set(reasons)
    if {
        "cse_exact_session_capacity_source_semantics",
        "source_semantics_unique_round_overflow",
    } & reason_set:
        _add_next_check(
            checks,
            "build_source_parser_for_session_capacity_or_round_cap_semantics",
            "guard-loss rows intersect unique-round/session-capacity evidence; field combinations alone cannot distinguish source semantics.",
        )
    if "cse_exact_server_side_expansion" in reason_set:
        _add_next_check(
            checks,
            "parse_server_side_settlement_expansion_events",
            "CSE evidence reports server-side expansion rows that need event/payload confirmation.",
        )
    if "source_semantics_drop_ref_only_overflow" in reason_set:
        _add_next_check(
            checks,
            "inspect_drop_ref_payload_source_semantics",
            "drop-ref-only overflow remains after temp/zodiac handling and may be payload/source dependent.",
        )
    if "source_semantics_instance_round_overflow" in reason_set:
        _add_next_check(
            checks,
            "inspect_instance_round_source_semantics_detail",
            "instance-round overflow appears in source semantics details without enough evidence for promotion.",
        )
    if any("drop_universe_gap" in reason for reason in reason_set):
        _add_next_check(
            checks,
            "build_activity_drop_universe_overlay_or_activity_source_parser",
            "drop-universe gap overlaps guard-loss rows; old table backfill is not a verifiable truth source.",
        )
    activity_status = str(activity_mapping_summary.get("status") or "")
    if activity_status == "watch_mixed_activity_mapping":
        _add_next_check(
            checks,
            "treat_activity_mapping_as_reference_not_single_truth_table",
            "activity mapping has mixed winners/candidate maps, so it is useful for tuning context but not as a hard table replacement.",
        )
    elif activity_status == "watch_single_activity_mapping":
        _add_next_check(
            checks,
            "verify_single_activity_mapping_before_table_backfill",
            "activity mapping has a single candidate family but still needs source/table validation before use as truth.",
        )
    elif activity_status == "blocked_candidate_status":
        _add_next_check(
            checks,
            "inspect_activity_mapping_candidate_status_failures",
            "activity mapping candidate rows are not all ok.",
        )

    semantic_counts = _as_mapping(capacity_summary.get("semantic_status_counts"))
    if "watch_activity_extras_explain_drop_ref_gap" in semantic_counts:
        _add_next_check(
            checks,
            "verify_activity_extras_table_against_current_raw_version",
            "capacity table detail says Activity extras may explain the drop-ref gap.",
        )
    if any(str(key).startswith("blocked_drop_ref") for key in semantic_counts):
        _add_next_check(
            checks,
            "resolve_drop_ref_capacity_source_or_overlay",
            "capacity table detail still has blocked drop-ref residuals.",
        )

    for next_check in _as_mapping(acquisition_summary.get("next_check_counts")):
        if str(next_check) in {"", "unknown"}:
            continue
        _add_next_check(
            checks,
            str(next_check),
            "capacity acquisition audit emitted this next_check for overlapping examples.",
        )
    if not checks and status.startswith("watch"):
        _add_next_check(
            checks,
            "collect_more_source_semantics_or_capacity_examples",
            "guard loss exists but current cross-artifact evidence is not decisive.",
        )
    return checks


def summarize_guard_loss_source_context(
    *,
    guard_doc: Mapping[str, Any],
    source_semantics: Mapping[str, Any],
    cse: Mapping[str, Any],
    capacity_table: Mapping[str, Any],
    capacity_acquisition: Mapping[str, Any],
    activity_mapping: Mapping[str, Any] | None = None,
    focus_maps: list[str] | None = None,
) -> dict[str, Any]:
    guard = _guard_metrics(guard_doc)
    by_map_id = _as_mapping(guard.get("by_map_id"))
    requested_maps = [str(map_id) for map_id in (focus_maps or [])]
    maps = _focus_maps(guard, requested_maps)
    cse_entries = _index_cse_entries(cse)
    source_by_map = _rows_by_map(_as_list(source_semantics.get("detail_rows")))
    capacity_by_map = _rows_by_map(_as_list(capacity_table.get("detail_rows")))
    acquisition_examples_by_map = _rows_by_map(
        _as_list(capacity_acquisition.get("top_examples"))
    )
    activity_by_map = _activity_mapping_by_map(activity_mapping)
    rows: list[dict[str, Any]] = []
    for map_id in maps:
        guard_stats = _as_mapping(by_map_id.get(map_id))
        guard_context = _guard_context(guard_stats)
        cse_map_entry = _as_mapping(cse_entries.get(("map_id", map_id)))
        map_family = (
            str(cse_map_entry.get("group_family") or "")
            or str(_entry_slice(cse_map_entry).get("map_family") or "")
        )
        source_rows = source_by_map.get(map_id, [])
        if not map_family and source_rows:
            map_family = str(source_rows[0].get("map_family") or "")
        if not map_family:
            guard_family = _as_mapping(guard_stats.get("formal_policy_comparison"))
            map_family = str(guard_family.get("map_family") or "")
        if not map_family:
            map_no = _int(map_id)
            if 2400 <= map_no < 2500:
                map_family = "villa"
            elif 2500 <= map_no < 2600:
                map_family = "shipwreck"
            elif 2600 <= map_no < 2700:
                map_family = "hidden"
            else:
                map_family = "unknown"
        cse_family_entry = _as_mapping(cse_entries.get(("map_family", map_family)))
        source_summary = _aggregate_rows(source_rows)
        capacity_rows = capacity_by_map.get(map_id, [])
        acquisition_rows = acquisition_examples_by_map.get(map_id, [])
        capacity_summary = _aggregate_capacity_table(capacity_rows)
        acquisition_summary = _aggregate_acquisition(
            acquisition_rows
        )
        activity_summary = activity_by_map.get(map_id, {})
        loss_rows = _guard_loss_rows(guard_stats)
        status, reasons = _classification(
            guard_loss_rows=loss_rows,
            cse_map=cse_map_entry,
            source_summary=source_summary,
            capacity_summary=capacity_summary,
            acquisition_summary=acquisition_summary,
            activity_mapping_summary=activity_summary,
        )
        next_checks = _next_checks(
            status=status,
            reasons=reasons,
            source_summary=source_summary,
            capacity_summary=capacity_summary,
            acquisition_summary=acquisition_summary,
            activity_mapping_summary=activity_summary,
        )
        rows.append(
            {
                "map_id": map_id,
                "map_family": map_family,
                "status": status,
                "reasons": reasons,
                "next_checks": next_checks,
                "guard": {
                    "rows": _guard_case_rows(guard_stats),
                    "p90_coverage_lost_rows": loss_rows,
                    "p90_coverage_lost_rate": (
                        round(loss_rows / _guard_case_rows(guard_stats), 3)
                        if _guard_case_rows(guard_stats)
                        else None
                    ),
                    "p50_worsened_rows": _int(
                        _as_mapping(
                            guard_stats.get("v3_practical_guard_case_summary")
                        ).get("p50_worsened_rows")
                    ),
                    "p90_extreme_over_added_rows": _int(
                        _as_mapping(
                            guard_stats.get("v3_practical_guard_case_summary")
                        ).get("p90_extreme_over_added_rows")
                    ),
                    "loss_context": dict(guard_context),
                },
                "cse_map_entry": _entry_slice(cse_map_entry),
                "cse_family_entry": _entry_slice(cse_family_entry),
                "source_semantics": source_summary,
                "capacity_table": capacity_summary,
                "capacity_acquisition_examples": acquisition_summary,
                "activity_mapping": dict(activity_summary),
                "evidence_examples": {
                    "source_semantics": _top_examples(
                        source_rows,
                        score_keys=[
                            "unique_round_cap_excess_after_temp_zodiac_count",
                            "unique_drop_ref_excess_after_temp_zodiac_count",
                            "non_zodiac_missing_from_drop_universe_count",
                        ],
                        formatter=_source_semantics_example,
                    ),
                    "capacity_table": _top_examples(
                        capacity_rows,
                        score_keys=[
                            "truth_prior_max_delta",
                            "round_cap_excess_after_temp_zodiac_count",
                            "drop_ref_excess_after_temp_zodiac_count",
                            "non_zodiac_missing_from_drop_universe_count",
                        ],
                        formatter=_capacity_table_example,
                    ),
                    "capacity_acquisition": _top_examples(
                        acquisition_rows,
                        score_keys=[
                            "truth_prior_max_delta",
                            "round_cap_excess_after_temp_zodiac_count",
                            "drop_ref_excess_after_temp_zodiac_count",
                            "non_zodiac_missing_from_drop_universe_count",
                        ],
                        formatter=_capacity_acquisition_example,
                    ),
                    "activity_mapping": _as_list(
                        _as_mapping(activity_summary).get("file_examples")
                    ),
                },
            }
        )
    status_counts = Counter(row["status"] for row in rows)
    blocked_rows = [
        row
        for row in rows
        if str(row["status"]).startswith("blocked")
    ]
    overall_status = (
        "blocked_source_semantics_required" if blocked_rows else "watch_audit_only"
    )
    return {
        "status": overall_status,
        "focus_maps": maps,
        "rows": rows,
        "summary": {
            "maps": len(rows),
            "guard_loss_rows": sum(_int(row["guard"]["p90_coverage_lost_rows"]) for row in rows),
            "status_counts": _counter_dict(status_counts),
            "cse_exact_overlap_maps": sum(
                1 for row in rows if row["cse_map_entry"]
            ),
            "source_semantics_detail_maps": sum(
                1 for row in rows if _int(row["source_semantics"]["rows"]) > 0
            ),
            "capacity_table_detail_maps": sum(
                1 for row in rows if _int(row["capacity_table"]["rows"]) > 0
            ),
            "capacity_acquisition_example_maps": sum(
                1
                for row in rows
                if _int(row["capacity_acquisition_examples"]["rows"]) > 0
            ),
            "activity_mapping_maps": sum(
                1 for row in rows if row.get("activity_mapping")
            ),
        },
    }


def _format_counts(counts: Mapping[str, Any]) -> str:
    if not counts:
        return "-"
    return ",".join(f"{key}:{value}" for key, value in counts.items())


def print_summary(result: Mapping[str, Any]) -> None:
    summary = _as_mapping(result.get("summary"))
    print(
        "status={status} maps={maps} guard_loss_rows={loss} "
        "status_counts={status_counts} cse_exact_maps={cse_maps} "
        "source_detail_maps={source_maps} capacity_detail_maps={capacity_maps}"
        .format(
            status=result.get("status"),
            maps=summary.get("maps"),
            loss=summary.get("guard_loss_rows"),
            status_counts=_format_counts(_as_mapping(summary.get("status_counts"))),
            cse_maps=summary.get("cse_exact_overlap_maps"),
            source_maps=summary.get("source_semantics_detail_maps"),
            capacity_maps=summary.get("capacity_table_detail_maps"),
        )
    )
    for row in _as_list(result.get("rows")):
        if not isinstance(row, Mapping):
            continue
        guard = _as_mapping(row.get("guard"))
        context = _as_mapping(guard.get("loss_context"))
        cse = _as_mapping(row.get("cse_map_entry"))
        source = _as_mapping(row.get("source_semantics"))
        capacity = _as_mapping(row.get("capacity_table"))
        acquisition = _as_mapping(row.get("capacity_acquisition_examples"))
        activity = _as_mapping(row.get("activity_mapping"))
        print(
            "map={map_id} family={family} status={status} "
            "loss={loss}/{guard_rows} flags={flags} "
            "cse_status={cse_status} cse_gate={cse_gate} "
            "cse_mechanisms={cse_mechanisms} source_mechanisms={source_mechanisms} "
            "source_contexts={source_contexts} capacity_semantic={capacity_semantic} "
            "acquisition_routes={acq_routes} activity_mapping={activity_status} "
            "activity_winners={activity_winners} reasons={reasons} "
            "next_checks={next_checks}"
            .format(
                map_id=row.get("map_id"),
                family=row.get("map_family"),
                status=row.get("status"),
                loss=guard.get("p90_coverage_lost_rows"),
                guard_rows=guard.get("rows"),
                flags=_format_counts(_as_mapping(context.get("by_guard_flag"))),
                cse_status=cse.get("status") or "-",
                cse_gate=cse.get("gate_reason") or "-",
                cse_mechanisms=_format_counts(
                    _as_mapping(cse.get("mechanism_classes"))
                ),
                source_mechanisms=_format_counts(
                    _as_mapping(source.get("mechanism_classes"))
                ),
                source_contexts=_format_counts(
                    _as_mapping(source.get("source_context_classes"))
                ),
                capacity_semantic=_format_counts(
                    _as_mapping(capacity.get("semantic_status_counts"))
                ),
                acq_routes=_format_counts(
                    _as_mapping(acquisition.get("acquisition_route_counts"))
                ),
                activity_status=activity.get("status") or "-",
                activity_winners=_format_counts(
                    _as_mapping(activity.get("winner_counts"))
                ),
                reasons=",".join(str(reason) for reason in _as_list(row.get("reasons")))
                or "-",
                next_checks=",".join(
                    str(_as_mapping(check).get("check"))
                    for check in _as_list(row.get("next_checks"))
                )
                or "-",
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-audit v3 practical guard coverage loss against CSE/source/capacity evidence.",
    )
    parser.add_argument("--readiness-json", type=Path, default=DEFAULT_READINESS)
    parser.add_argument("--source-semantics-json", type=Path, default=DEFAULT_SOURCE_SEMANTICS)
    parser.add_argument("--capacity-source-expansion-json", type=Path, default=DEFAULT_CSE)
    parser.add_argument("--capacity-table-json", type=Path, default=DEFAULT_CAPACITY_TABLE)
    parser.add_argument("--capacity-acquisition-json", type=Path, default=DEFAULT_CAPACITY_ACQUISITION)
    parser.add_argument("--activity-mapping-json", type=Path, default=DEFAULT_ACTIVITY_MAPPING)
    parser.add_argument("--focus-map", action="append", default=[])
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    result = summarize_guard_loss_source_context(
        guard_doc=_load_json(args.readiness_json),
        source_semantics=_load_json(args.source_semantics_json),
        cse=_load_json(args.capacity_source_expansion_json),
        capacity_table=_load_json(args.capacity_table_json),
        capacity_acquisition=_load_json(args.capacity_acquisition_json),
        activity_mapping=(
            _load_json(args.activity_mapping_json)
            if args.activity_mapping_json.exists()
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
