"""Audit v3 prior-stress capacity cases against raw BidMap/Drop tables."""

from __future__ import annotations

import argparse
import json
import re
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

from evaluate_fatbeans_v3_samples import (  # noqa: E402
    _default_calibration_path,
    _default_paths,
    _default_tail_value_review_path,
    _default_underestimate_repair_path,
    _float_or_none,
    _mean,
    _round_metric,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
    load_tail_value_review_entries,
    load_underestimate_repair_entries,
)
from summarize_v3_prior_robustness_audit import (  # noqa: E402
    summarize_prior_stress_details,
)
from summarize_v3_settlement_payload_audit import (  # noqa: E402
    _inventory_block_metrics,
    _latest_settlement_payload,
)
from bidking_lab.inference.ground_truth import prepare_session_sampler  # noqa: E402
from bidking_lab.inference.v3.truth import settlement_truth_from_fatbeans  # noqa: E402
from bidking_lab.live.fatbeans import _first, _parse_fields, parse_fatbeans_capture  # noqa: E402
from bidking_lab.simulation.basic_mc import flatten_pool  # noqa: E402


DEFAULT_CASE = "direct_prior_max_conflict"
DEFAULT_BUCKET = "all"
DEFAULT_SAMPLE_ROOT = ROOT / "data" / "samples" / "fatbeans"
_TEMPORARY_BLUE_ZODIAC_ITEM_IDS = frozenset(range(1306003, 1306015))
_DATE_TOKEN_RE = re.compile(r"(20\d{6})")


def _numeric_values(values: Iterable[Any]) -> tuple[float, ...]:
    out: list[float] = []
    for value in values:
        parsed = _float_or_none(value)
        if parsed is not None:
            out.append(parsed)
    return tuple(out)


def _numeric_summary(values: Iterable[Any], *, digits: int = 3) -> dict[str, Any]:
    seq = _numeric_values(values)
    if not seq:
        return {"n": 0, "avg": None, "p90": None, "max": None}
    ordered = sorted(seq)
    p90_index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.9)))
    return {
        "n": len(seq),
        "avg": _round_metric(_mean(seq), digits),
        "p90": _round_metric(ordered[p90_index], digits),
        "max": _round_metric(max(seq), digits),
    }


def _delta_counts(values: Iterable[Any]) -> dict[str, int]:
    counts = {"below": 0, "match": 0, "above": 0}
    for value in _numeric_values(values):
        if value < 0.0:
            counts["below"] += 1
        elif value > 0.0:
            counts["above"] += 1
        else:
            counts["match"] += 1
    return counts


def _counter_dict(values: Iterable[Any], *, top: int = 8) -> dict[str, int]:
    counts: Counter[str] = Counter(
        str(value) if value not in (None, "") else "none"
        for value in values
    )
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top])


def _int_list_from_json_blob(blob: Any) -> tuple[int, ...]:
    if not isinstance(blob, str) or blob in ("", "[]", "[[]]"):
        return ()
    try:
        parsed = json.loads(blob)
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    out: list[int] = []
    for value in parsed:
        if isinstance(value, bool):
            continue
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def _bidmap_drop_ref_column_index(raw_column_count: int) -> int:
    return 17 if raw_column_count == 23 else 16


def _bidmap_round_cap_column_index(raw_column_count: int) -> int:
    return 14 if raw_column_count == 23 else 13


def _bidmap_round_caps(bid_map: Any) -> tuple[int, ...]:
    raw_row = tuple(getattr(bid_map, "raw_row", ()) or ())
    if not raw_row:
        return ()
    index = _bidmap_round_cap_column_index(len(raw_row))
    return _int_list_from_json_blob(raw_row[index] if len(raw_row) > index else None)


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _map_prefix3(map_id: Any) -> int | None:
    parsed = _safe_int(map_id)
    return int(parsed) // 10 if parsed is not None else None


def _map_family(map_id: Any) -> str:
    parsed = _safe_int(map_id)
    if parsed is None:
        return "unknown"
    family = int(parsed) // 100
    if family in (24, 34, 44):
        return "villa"
    if family in (25, 35, 45):
        return "shipwreck"
    if family in (26, 36, 46):
        return "hidden"
    return "other"


def _strip_row_file_ref(file_ref: Any) -> str:
    return str(file_ref or "").split("#", 1)[0]


def _capture_day(file_ref: Any) -> str | None:
    match = _DATE_TOKEN_RE.search(Path(_strip_row_file_ref(file_ref)).name)
    return match.group(1) if match is not None else None


def _resolve_capture_path(file_ref: Any, sample_root: Path) -> Path | None:
    raw = _strip_row_file_ref(file_ref)
    if not raw:
        return None
    path = Path(raw)
    candidates = (
        path,
        ROOT / path,
        sample_root / path.name,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _reachable_drop_item_ids_for_map(
    map_id: int | None,
    *,
    tables: Any,
    cache: dict[int, frozenset[int]],
) -> frozenset[int]:
    if map_id is None:
        return frozenset()
    map_key = int(map_id)
    if map_key in cache:
        return cache[map_key]
    bid_map = tables.maps.get(map_key)
    if bid_map is None:
        cache[map_key] = frozenset()
        return cache[map_key]
    item_ids: set[int] = set()
    if getattr(bid_map, "sub_pool_weights", None):
        for sub_map_id, _weight in bid_map.sub_pool_weights:
            sub_map = tables.maps.get(int(sub_map_id))
            if sub_map is None:
                continue
            item_ids.update(
                flatten_pool(
                    int(sub_map.drop_pool_id),
                    tables.drops,
                    tables.items,
                ).item_ids
            )
    item_ids.update(
        flatten_pool(
            int(bid_map.drop_pool_id),
            tables.drops,
            tables.items,
        ).item_ids
    )
    cache[map_key] = frozenset(item_ids)
    return cache[map_key]


def _inventory_diagnostic_for_path(
    path: Path,
    *,
    tables: Any,
    drop_universe_cache: dict[int, frozenset[int]],
) -> dict[str, Any]:
    events = parse_fatbeans_capture(path)
    inventory_states = [
        state
        for state in tuple(getattr(events, "states", ()) or ())
        if tuple(getattr(state, "inventory_items", ()) or ())
    ]
    truth = settlement_truth_from_fatbeans(events, items=tables.items)
    if not inventory_states:
        return {
            "path": str(path),
            "file": path.name,
            "status": "no_inventory_state",
            "inventory_state_count": 0,
            "latest_sort_id": None,
            "latest_message_id": None,
            "latest_round_index": None,
            "latest_map_id": None,
            "latest_item_count": None,
            "latest_total_cells": None,
            "truth_item_count": getattr(truth, "item_count", None),
            "truth_matches_latest": False,
            "unique_runtime_id_count": None,
            "duplicate_runtime_id_count": None,
            "unique_item_id_count": None,
            "duplicate_item_id_count": None,
            "unique_runtime_item_pair_count": None,
            "duplicate_runtime_item_pair_count": None,
            "quality_counts": {},
            "cell_counts": {},
            "missing_from_drop_universe_count": None,
            "known_temp_zodiac_count": None,
            "non_zodiac_missing_from_drop_universe_count": None,
            "missing_from_drop_universe_examples": {},
            "inventory_slot_count": None,
            "occupied_slot_count": None,
            "raw_item_candidate_count": None,
            "raw_candidate_inventory_delta": None,
            "occupied_slot_inventory_delta": None,
            "inventory_slot_headroom": None,
            "inventory_slot_headroom_after_temp_zodiac": None,
            "action_result_count": None,
            "action_observed_item_count_max": None,
            "full_observed_action_ids": [],
            "public_total_count_values": [],
            "drop_ref_excess_item_count": None,
            "drop_ref_excess_after_temp_zodiac_count": None,
            "round_cap_excess_item_count": None,
            "round_cap_excess_after_temp_zodiac_count": None,
        }

    latest = inventory_states[-1]
    items = tuple(getattr(latest, "inventory_items", ()) or ())
    runtime_ids = [getattr(item, "runtime_id", None) for item in items]
    item_ids = [getattr(item, "item_id", None) for item in items]
    runtime_item_pairs = [
        (getattr(item, "runtime_id", None), getattr(item, "item_id", None))
        for item in items
    ]
    quality_counts = Counter(str(getattr(item, "quality", None)) for item in items)
    cell_counts = Counter(str(getattr(item, "cells", None)) for item in items)
    truth_count = getattr(truth, "item_count", None)
    latest_count = len(items)
    latest_map_id = getattr(latest, "map_id", None)
    reachable_item_ids = _reachable_drop_item_ids_for_map(
        _safe_int(latest_map_id),
        tables=tables,
        cache=drop_universe_cache,
    )
    missing_item_ids = [
        int(item_id)
        for item_id in item_ids
        if item_id is not None and int(item_id) not in reachable_item_ids
    ]
    known_temp_zodiac_count = sum(
        1 for item_id in missing_item_ids if int(item_id) in _TEMPORARY_BLUE_ZODIAC_ITEM_IDS
    )
    bid_map = tables.maps.get(_safe_int(latest_map_id)) if latest_map_id is not None else None
    drop_ref_max = _safe_int(getattr(bid_map, "items_per_session_max", None))
    round_caps = _bidmap_round_caps(bid_map) if bid_map is not None else ()
    round_cap_max = max(round_caps) if round_caps else None
    non_temp_latest_count = latest_count - known_temp_zodiac_count
    payload, _loss_units, _frame_meta = _latest_settlement_payload(path)
    payload_fields = _parse_fields(payload) if isinstance(payload, bytes) else []
    inventory_block = _first(payload_fields, 4) if payload_fields else None
    inventory_metrics = _inventory_block_metrics(inventory_block)
    occupied_slot_count = _safe_int(inventory_metrics.get("occupied_slot_count"))
    raw_candidate_count = _safe_int(inventory_metrics.get("raw_item_candidate_count"))
    inventory_slot_count = _safe_int(inventory_metrics.get("inventory_slot_count"))
    action_observed_counts = [
        (
            _safe_int(getattr(action, "action_id", None)),
            len(tuple(getattr(action, "observed_items", ()) or ())),
        )
        for action in tuple(getattr(latest, "action_results", ()) or ())
    ]
    full_observed_action_ids = [
        action_id
        for action_id, observed_count in action_observed_counts
        if action_id is not None and latest_count > 0 and observed_count == latest_count
    ]
    public_total_count_values = [
        getattr(info, "value", None)
        for info in tuple(getattr(latest, "public_infos", ()) or ())
        if getattr(info, "info_id", None) == 200017
    ]
    return {
        "path": str(path),
        "file": path.name,
        "status": "ok",
        "inventory_state_count": len(inventory_states),
        "latest_sort_id": getattr(latest, "sort_id", None),
        "latest_message_id": getattr(latest, "message_id", None),
        "latest_round_index": getattr(latest, "round_index", None),
        "latest_map_id": latest_map_id,
        "latest_item_count": latest_count,
        "latest_total_cells": sum(_safe_int(getattr(item, "cells", None)) or 0 for item in items),
        "truth_item_count": truth_count,
        "truth_matches_latest": (
            truth_count is not None and int(truth_count) == latest_count
        ),
        "unique_runtime_id_count": len(set(runtime_ids)),
        "duplicate_runtime_id_count": len(runtime_ids) - len(set(runtime_ids)),
        "unique_item_id_count": len(set(item_ids)),
        "duplicate_item_id_count": len(item_ids) - len(set(item_ids)),
        "unique_runtime_item_pair_count": len(set(runtime_item_pairs)),
        "duplicate_runtime_item_pair_count": (
            len(runtime_item_pairs) - len(set(runtime_item_pairs))
        ),
        "quality_counts": dict(sorted(quality_counts.items())),
        "cell_counts": dict(sorted(cell_counts.items())),
        "missing_from_drop_universe_count": len(missing_item_ids),
        "known_temp_zodiac_count": known_temp_zodiac_count,
        "non_zodiac_missing_from_drop_universe_count": (
            len(missing_item_ids) - known_temp_zodiac_count
        ),
        "missing_from_drop_universe_examples": dict(
            sorted(
                Counter(str(item_id) for item_id in missing_item_ids).items(),
                key=lambda item: (-item[1], item[0]),
            )[:8]
        ),
        "inventory_slot_count": inventory_slot_count,
        "occupied_slot_count": occupied_slot_count,
        "raw_item_candidate_count": raw_candidate_count,
        "raw_candidate_inventory_delta": (
            raw_candidate_count - latest_count
            if raw_candidate_count is not None
            else None
        ),
        "occupied_slot_inventory_delta": (
            occupied_slot_count - latest_count
            if occupied_slot_count is not None
            else None
        ),
        "inventory_slot_headroom": (
            inventory_slot_count - latest_count
            if inventory_slot_count is not None
            else None
        ),
        "inventory_slot_headroom_after_temp_zodiac": (
            inventory_slot_count - non_temp_latest_count
            if inventory_slot_count is not None
            else None
        ),
        "action_result_count": len(action_observed_counts),
        "action_observed_item_count_max": (
            max((count for _action_id, count in action_observed_counts), default=0)
        ),
        "full_observed_action_ids": full_observed_action_ids,
        "public_total_count_values": public_total_count_values,
        "drop_ref_excess_item_count": (
            max(0, latest_count - drop_ref_max)
            if drop_ref_max is not None
            else None
        ),
        "drop_ref_excess_after_temp_zodiac_count": (
            max(0, non_temp_latest_count - drop_ref_max)
            if drop_ref_max is not None
            else None
        ),
        "round_cap_excess_item_count": (
            max(0, latest_count - round_cap_max)
            if round_cap_max is not None
            else None
        ),
        "round_cap_excess_after_temp_zodiac_count": (
            max(0, non_temp_latest_count - round_cap_max)
            if round_cap_max is not None
            else None
        ),
    }


def _merge_counter_dicts(values: Iterable[Mapping[str, int]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for value in values:
        counts.update({str(key): int(count) for key, count in value.items()})
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def _positive_file_count(
    diagnostics: Iterable[Mapping[str, Any]],
    field: str,
) -> int:
    return sum(
        1
        for diag in diagnostics
        if (_float_or_none(diag.get(field)) or 0.0) > 0.0
    )


def _residual_mode(diag: Mapping[str, Any]) -> str:
    status = str(diag.get("status") or "")
    if status and status != "ok":
        return status
    if (
        _float_or_none(diag.get("non_zodiac_missing_from_drop_universe_count"))
        or 0.0
    ) > 0.0:
        return "drop_universe_gap"
    if (
        _float_or_none(diag.get("round_cap_excess_after_temp_zodiac_count"))
        or 0.0
    ) > 0.0:
        return "round_cap_overflow"
    if (
        _float_or_none(diag.get("drop_ref_excess_after_temp_zodiac_count"))
        or 0.0
    ) > 0.0:
        return "drop_ref_only_overflow"
    return "within_drop_ref"


def _diagnostics_by_residual_mode(
    diagnostics: Iterable[Mapping[str, Any]],
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for diag in diagnostics:
        grouped[_residual_mode(diag)].append(diag)
    return {
        key: tuple(value)
        for key, value in sorted(grouped.items(), key=lambda item: item[0])
    }


def _residual_mode_summary(
    diagnostics: Iterable[Mapping[str, Any]],
    *,
    top: int,
) -> dict[str, Any]:
    seq = tuple(diagnostics)
    grouped = _diagnostics_by_residual_mode(seq)
    return {
        "file_count": len(seq),
        "mode_counts": _counter_dict((_residual_mode(diag) for diag in seq), top=top),
        "by_mode": [
            {
                "mode": mode,
                "file_count": len(rows),
                "latest_item_count": _numeric_summary(
                    diag.get("latest_item_count") for diag in rows
                ),
                "drop_ref_excess_after_temp_zodiac_count": _numeric_summary(
                    diag.get("drop_ref_excess_after_temp_zodiac_count")
                    for diag in rows
                ),
                "round_cap_excess_after_temp_zodiac_count": _numeric_summary(
                    diag.get("round_cap_excess_after_temp_zodiac_count")
                    for diag in rows
                ),
                "non_zodiac_missing_from_drop_universe_count": _numeric_summary(
                    diag.get("non_zodiac_missing_from_drop_universe_count")
                    for diag in rows
                ),
                "latest_message_id_counts": _counter_dict(
                    (
                        f"0x{int(message_id):04X}" if message_id is not None else None
                        for message_id in (
                            diag.get("latest_message_id") for diag in rows
                        )
                    ),
                    top=top,
                ),
                "full_observed_action_counts": _counter_dict(
                    action_id
                    for diag in rows
                    for action_id in diag.get("full_observed_action_ids", ())
                ),
                "public_total_count_values": _counter_dict(
                    value
                    for diag in rows
                    for value in diag.get("public_total_count_values", ())
                ),
            }
            for mode, rows in grouped.items()
        ],
    }


_MATRIX_FIELDS = (
    "consistency_bucket",
    "residual_mode",
    "map_family",
    "total_count_source",
    "full_action_signal",
    "public_total_signal",
    "capture_day",
)


def _matrix_signal(value: bool, *, positive: str, negative: str) -> str:
    return positive if value else negative


def _diag_for_row(
    row: Mapping[str, Any],
    *,
    resolved_by_ref: Mapping[str, Path | None],
    diagnostics_by_path: Mapping[Path, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    ref = _strip_row_file_ref(row.get("file"))
    path = resolved_by_ref.get(ref)
    if path is None:
        return None
    return diagnostics_by_path.get(path)


def _file_label(row: Mapping[str, Any]) -> str:
    ref = _strip_row_file_ref(row.get("file"))
    return Path(ref).name if ref else "none"


def _matrix_cell_status(
    *,
    capacity_rows: Iterable[Mapping[str, Any]],
    diagnostics: Iterable[Mapping[str, Any]],
) -> str:
    capacity_seq = tuple(capacity_rows)
    diag_seq = tuple(diagnostics)
    if not diag_seq:
        return "needs_raw_inventory_verification"
    non_zodiac_missing = max(
        (
            _float_or_none(diag.get("non_zodiac_missing_from_drop_universe_count"))
            or 0.0
        )
        for diag in diag_seq
    )
    if non_zodiac_missing > 0.0:
        return "blocked_drop_universe_gap_after_temp"
    round_after_temp = max(
        (
            _float_or_none(diag.get("round_cap_excess_after_temp_zodiac_count"))
            or 0.0
        )
        for diag in diag_seq
    )
    if round_after_temp > 0.0:
        return "blocked_round_cap_overflow_after_temp"
    drop_after_temp = max(
        (
            _float_or_none(diag.get("drop_ref_excess_after_temp_zodiac_count"))
            or 0.0
        )
        for diag in diag_seq
    )
    if drop_after_temp > 0.0:
        return "blocked_drop_ref_overflow_after_temp"
    temp_zodiac = max(
        (_float_or_none(diag.get("known_temp_zodiac_count")) or 0.0)
        for diag in diag_seq
    )
    has_capacity_gap = any(
        (_float_or_none(row.get("truth_prior_max_delta")) or 0.0) > 0.0
        for row in capacity_seq
    )
    if has_capacity_gap and temp_zodiac > 0.0:
        return "watch_activity_extras_explain_drop_ref_gap"
    return "pass_table_caps_cover_verified_inventory"


def _capacity_detail_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    resolved_by_ref: Mapping[str, Path | None],
    diagnostics_by_path: Mapping[Path, Mapping[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: str(item.get("file") or "")):
        if len(out) >= limit:
            break
        capacity = row.get("item_count_capacity", {})
        if not isinstance(capacity, Mapping):
            capacity = {}
        diag = _diag_for_row(
            row,
            resolved_by_ref=resolved_by_ref,
            diagnostics_by_path=diagnostics_by_path,
        )
        diag_seq = [diag] if diag is not None else []
        semantic_status = _matrix_cell_status(
            capacity_rows=[capacity],
            diagnostics=diag_seq,
        )
        out.append(
            {
                "file": _file_label(row),
                "file_ref": _strip_row_file_ref(row.get("file")),
                "map_id": row.get("map_id"),
                "map_family": _map_family(row.get("map_id")),
                "capture_day": _capture_day(row.get("file")) or "none",
                "hero_map_evidence_profile": row.get("hero_map_evidence_profile"),
                "consistency_bucket": row.get("consistency_bucket"),
                "consistency_classes": list(row.get("consistency_classes") or []),
                "capacity_cases": list(_capacity_cases(row)),
                "total_count_source": capacity.get("total_count_source"),
                "total_count_target": capacity.get("total_count_target"),
                "truth_item_count": capacity.get("truth_item_count"),
                "prior_items_per_session_max": capacity.get(
                    "prior_items_per_session_max"
                ),
                "target_prior_max_delta": capacity.get("target_prior_max_delta"),
                "truth_prior_max_delta": capacity.get("truth_prior_max_delta"),
                "target_truth_delta": capacity.get("target_truth_delta"),
                "inventory_status": (diag or {}).get("status"),
                "residual_mode": (
                    _residual_mode(diag) if diag is not None else "not_checked"
                ),
                "semantic_status": semantic_status,
                "latest_item_count": (diag or {}).get("latest_item_count"),
                "latest_message_id": (
                    f"0x{int((diag or {}).get('latest_message_id')):04X}"
                    if (diag or {}).get("latest_message_id") is not None
                    else None
                ),
                "known_temp_zodiac_count": (diag or {}).get(
                    "known_temp_zodiac_count"
                ),
                "drop_ref_excess_after_temp_zodiac_count": (diag or {}).get(
                    "drop_ref_excess_after_temp_zodiac_count"
                ),
                "round_cap_excess_after_temp_zodiac_count": (diag or {}).get(
                    "round_cap_excess_after_temp_zodiac_count"
                ),
                "non_zodiac_missing_from_drop_universe_count": (diag or {}).get(
                    "non_zodiac_missing_from_drop_universe_count"
                ),
                "full_observed_action_ids": list(
                    (diag or {}).get("full_observed_action_ids") or []
                ),
                "public_total_count_values": list(
                    (diag or {}).get("public_total_count_values") or []
                ),
            }
        )
    return out


def _capacity_semantic_matrix(
    rows: Iterable[Mapping[str, Any]],
    *,
    diagnostics_by_path: Mapping[Path, Mapping[str, Any]],
    resolved_by_ref: Mapping[str, Path | None],
    top: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[tuple[Mapping[str, Any], Mapping[str, Any] | None]]] = (
        defaultdict(list)
    )
    for row in rows:
        capacity = row.get("item_count_capacity", {})
        diag = _diag_for_row(
            row,
            resolved_by_ref=resolved_by_ref,
            diagnostics_by_path=diagnostics_by_path,
        )
        residual_mode = _residual_mode(diag) if diag is not None else "not_checked"
        full_action_signal = _matrix_signal(
            bool(tuple((diag or {}).get("full_observed_action_ids", ()) or ())),
            positive="has_full_action",
            negative="no_full_action",
        )
        public_total_signal = _matrix_signal(
            bool(tuple((diag or {}).get("public_total_count_values", ()) or ())),
            positive="has_public_total",
            negative="no_public_total",
        )
        key = (
            str(row.get("consistency_bucket") or "none"),
            residual_mode,
            _map_family(row.get("map_id")),
            str(capacity.get("total_count_source") or "none"),
            full_action_signal,
            public_total_signal,
            _capture_day(row.get("file")) or "none",
        )
        grouped[key].append((row, diag))

    matrix: list[dict[str, Any]] = []
    for key, entries in grouped.items():
        source_rows = [entry[0] for entry in entries]
        diags = [entry[1] for entry in entries if entry[1] is not None]
        capacity_rows = [row.get("item_count_capacity", {}) for row in source_rows]
        unique_files = sorted({_file_label(row) for row in source_rows})
        cell_status = _matrix_cell_status(
            capacity_rows=capacity_rows,
            diagnostics=diags,
        )
        matrix.append(
            {
                **dict(zip(_MATRIX_FIELDS, key)),
                "rows": len(source_rows),
                "files": len(unique_files),
                "semantic_status_counts": {cell_status: len(source_rows)},
                "map_ids": _counter_dict(
                    (row.get("map_id") for row in source_rows),
                    top=top,
                ),
                "capacity_cases": _counter_dict(
                    case
                    for row in source_rows
                    for case in _capacity_cases(row)
                ),
                "target_truth_delta_counts": _delta_counts(
                    row.get("target_truth_delta") for row in capacity_rows
                ),
                "truth_prior_max_delta": _numeric_summary(
                    row.get("truth_prior_max_delta") for row in capacity_rows
                ),
                "latest_item_count": _numeric_summary(
                    diag.get("latest_item_count") for diag in diags
                ),
                "drop_ref_excess_after_temp_zodiac_count": _numeric_summary(
                    diag.get("drop_ref_excess_after_temp_zodiac_count")
                    for diag in diags
                ),
                "round_cap_excess_after_temp_zodiac_count": _numeric_summary(
                    diag.get("round_cap_excess_after_temp_zodiac_count")
                    for diag in diags
                ),
                "non_zodiac_missing_from_drop_universe_count": _numeric_summary(
                    diag.get("non_zodiac_missing_from_drop_universe_count")
                    for diag in diags
                ),
                "known_temp_zodiac_count": _numeric_summary(
                    diag.get("known_temp_zodiac_count") for diag in diags
                ),
                "full_observed_action_counts": _counter_dict(
                    action_id
                    for diag in diags
                    for action_id in diag.get("full_observed_action_ids", ())
                ),
                "public_total_count_values": _counter_dict(
                    value
                    for diag in diags
                    for value in diag.get("public_total_count_values", ())
                ),
                "example_files": unique_files[:top],
            }
        )
    return sorted(
        matrix,
        key=lambda row: (
            -int(row["rows"]),
            -int(row["files"]),
            str(row["consistency_bucket"]),
            str(row["residual_mode"]),
            str(row["map_family"]),
            str(row["total_count_source"]),
            str(row["capture_day"]),
        ),
    )[:top]


def _summary_max(summary: Any) -> float | None:
    if not isinstance(summary, Mapping):
        return None
    return _float_or_none(summary.get("max"))


def _summary_all_zero(summary: Any) -> bool:
    if not isinstance(summary, Mapping):
        return False
    return _float_or_none(summary.get("max")) == 0.0 and _float_or_none(
        summary.get("avg")
    ) == 0.0


def _capacity_semantic_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    findings: list[str] = []
    blockers: list[str] = []

    raw_verified = (
        row.get("raw_inventory_status") == "verified_latest_inventory"
        and int(row.get("raw_detail_truth_latest_match_rows") or 0)
        == int(row.get("rows") or 0)
        and int(row.get("raw_detail_truth_latest_mismatch_rows") or 0) == 0
    )
    if raw_verified:
        findings.append("raw_latest_inventory_matches_detail_truth")
    else:
        blockers.append("raw_inventory_not_fully_verified")

    if (
        int(row.get("bidmap_raw_column_count") or 0) == 23
        and int(row.get("bidmap_drop_ref_column_index") or -1) == 17
    ):
        findings.append("current_v300_drop_ref_col17")
        if row.get("bidmap_raw_col16") in (None, "", "[]", "[[]]"):
            findings.append("current_v300_col16_unused")

    leaf_nmax_max = _summary_max(row.get("sampler_leaf_n_max"))
    if int(row.get("sampler_entries_nmax_gt1") or 0) == 0 and (
        leaf_nmax_max is None or leaf_nmax_max <= 1.0
    ):
        findings.append("drop_entry_nmax_not_multi_count_driver")

    if _summary_all_zero(row.get("raw_candidate_inventory_delta")):
        findings.append("raw_candidate_count_matches_parsed_inventory")
    if _summary_all_zero(row.get("raw_occupied_slot_inventory_delta")):
        findings.append("occupied_slot_count_matches_parsed_inventory")
    if _summary_max(row.get("raw_non_zodiac_missing_from_drop_universe_count")) == 0.0:
        findings.append("drop_universe_covered_after_temp_zodiac")

    drop_after_temp = _summary_max(
        row.get("raw_drop_ref_excess_after_temp_zodiac_count")
    )
    round_after_temp = _summary_max(
        row.get("raw_round_cap_excess_after_temp_zodiac_count")
    )
    non_zodiac_missing = _summary_max(
        row.get("raw_non_zodiac_missing_from_drop_universe_count")
    )
    temp_zodiac = _summary_max(row.get("raw_known_temp_zodiac_count"))

    if non_zodiac_missing is not None and non_zodiac_missing > 0.0:
        status = "blocked_drop_universe_gap_after_temp"
        blockers.append("non_zodiac_items_missing_from_drop_universe")
    elif round_after_temp is not None and round_after_temp > 0.0:
        status = "blocked_round_cap_overflow_after_temp"
        blockers.append("round_cap_candidate_below_verified_inventory")
    elif drop_after_temp is not None and drop_after_temp > 0.0:
        status = "blocked_drop_ref_overflow_after_temp"
        blockers.append("drop_ref_max_below_verified_inventory")
    elif int(row.get("table_impossible_rows") or 0) > 0 and (
        temp_zodiac is not None and temp_zodiac > 0.0
    ):
        status = "watch_activity_extras_explain_drop_ref_gap"
        findings.append("temporary_zodiac_explains_drop_ref_gap")
    elif int(row.get("table_impossible_rows") or 0) > 0:
        status = "blocked_sampler_possible_max_below_truth"
        blockers.append("sampler_possible_max_below_detail_truth")
    elif blockers:
        status = "needs_raw_inventory_verification"
    else:
        status = "pass_table_caps_cover_verified_inventory"

    return {
        "status": status,
        "blockers": blockers,
        "findings": findings,
        "drop_ref_excess_after_temp_zodiac_max": drop_after_temp,
        "round_cap_excess_after_temp_zodiac_max": round_after_temp,
        "non_zodiac_missing_from_drop_universe_max": non_zodiac_missing,
        "known_temp_zodiac_max": temp_zodiac,
    }


def _source_split_summary(
    rows: Iterable[Mapping[str, Any]],
    diagnostics: Iterable[Mapping[str, Any]],
    *,
    top: int,
) -> dict[str, Any]:
    seq = tuple(rows)
    diag_seq = tuple(diagnostics)
    capacity = tuple(row.get("item_count_capacity", {}) for row in seq)
    return {
        "rows": len(seq),
        "map_prefix3_counts": _counter_dict(
            (_map_prefix3(row.get("map_id")) for row in seq),
            top=top,
        ),
        "map_family_counts": _counter_dict(
            (_map_family(row.get("map_id")) for row in seq),
            top=top,
        ),
        "capture_day_counts": _counter_dict(
            (_capture_day(row.get("file")) for row in seq),
            top=top,
        ),
        "total_count_source_counts": _counter_dict(
            (row.get("total_count_source") for row in capacity),
            top=top,
        ),
        "target_truth_delta_counts": _delta_counts(
            row.get("target_truth_delta") for row in capacity
        ),
        "truth_prior_max_delta": _numeric_summary(
            row.get("truth_prior_max_delta") for row in capacity
        ),
        "inventory_status_counts": _counter_dict(
            (diag.get("status") for diag in diag_seq),
            top=top,
        ),
        "latest_message_id_counts": _counter_dict(
            (
                f"0x{int(message_id):04X}" if message_id is not None else None
                for message_id in (
                    diag.get("latest_message_id") for diag in diag_seq
                )
            ),
            top=top,
        ),
        "drop_ref_excess_after_temp_zodiac_count": _numeric_summary(
            diag.get("drop_ref_excess_after_temp_zodiac_count")
            for diag in diag_seq
        ),
        "drop_ref_excess_after_temp_positive_files": _positive_file_count(
            diag_seq,
            "drop_ref_excess_after_temp_zodiac_count",
        ),
        "round_cap_excess_after_temp_zodiac_count": _numeric_summary(
            diag.get("round_cap_excess_after_temp_zodiac_count")
            for diag in diag_seq
        ),
        "round_cap_excess_after_temp_positive_files": _positive_file_count(
            diag_seq,
            "round_cap_excess_after_temp_zodiac_count",
        ),
        "non_zodiac_missing_from_drop_universe_count": _numeric_summary(
            diag.get("non_zodiac_missing_from_drop_universe_count")
            for diag in diag_seq
        ),
        "non_zodiac_missing_positive_files": _positive_file_count(
            diag_seq,
            "non_zodiac_missing_from_drop_universe_count",
        ),
        "full_observed_action_counts": _counter_dict(
            action_id
            for diag in diag_seq
            for action_id in diag.get("full_observed_action_ids", ())
        ),
        "public_total_count_values": _counter_dict(
            value
            for diag in diag_seq
            for value in diag.get("public_total_count_values", ())
        ),
    }


def _inventory_diagnostics_for_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    tables: Any,
    sample_root: Path,
    top: int,
    detail_rows: int = 0,
) -> dict[str, Any]:
    seq = tuple(rows)
    resolved_by_ref: dict[str, Path | None] = {}
    diagnostics_by_path: dict[Path, dict[str, Any]] = {}
    drop_universe_cache: dict[int, frozenset[int]] = {}
    missing_refs: list[str] = []
    parse_errors: list[str] = []

    for row in seq:
        ref = _strip_row_file_ref(row.get("file"))
        if not ref or ref in resolved_by_ref:
            continue
        path = _resolve_capture_path(ref, sample_root)
        resolved_by_ref[ref] = path
        if path is None:
            missing_refs.append(ref)
            continue
        if path in diagnostics_by_path:
            continue
        try:
            diagnostics_by_path[path] = _inventory_diagnostic_for_path(
                path,
                tables=tables,
                drop_universe_cache=drop_universe_cache,
            )
        except Exception as exc:  # pragma: no cover - retained for CLI diagnostics.
            parse_errors.append(f"{path}:{exc}")

    detail_truth_match_rows = 0
    detail_truth_mismatch_rows = 0
    detail_truth_unchecked_rows = 0
    for row in seq:
        ref = _strip_row_file_ref(row.get("file"))
        path = resolved_by_ref.get(ref)
        diag = diagnostics_by_path.get(path) if path is not None else None
        latest_count = _float_or_none((diag or {}).get("latest_item_count"))
        capacity = row.get("item_count_capacity", {})
        truth_count = _float_or_none(capacity.get("truth_item_count"))
        if latest_count is None or truth_count is None:
            detail_truth_unchecked_rows += 1
        elif float(latest_count) == float(truth_count):
            detail_truth_match_rows += 1
        else:
            detail_truth_mismatch_rows += 1

    diagnostics = tuple(diagnostics_by_path.values())
    status = "verified_latest_inventory"
    if missing_refs:
        status = "missing_capture_file"
    elif parse_errors:
        status = "parse_error"
    elif any(diag.get("status") == "no_inventory_state" for diag in diagnostics):
        status = "no_inventory_state"
    elif detail_truth_mismatch_rows:
        status = "detail_truth_latest_mismatch"
    elif any(
        int(diag.get("duplicate_runtime_item_pair_count") or 0) > 0
        for diag in diagnostics
    ):
        status = "duplicate_runtime_item_pair"
    elif not diagnostics:
        status = "not_checked"

    out = {
        "raw_inventory_status": status,
        "raw_inventory_file_count": len(diagnostics),
        "raw_inventory_missing_file_count": len(missing_refs),
        "raw_inventory_parse_error_count": len(parse_errors),
        "raw_inventory_state_count": _numeric_summary(
            diag.get("inventory_state_count") for diag in diagnostics
        ),
        "raw_latest_inventory_item_count": _numeric_summary(
            diag.get("latest_item_count") for diag in diagnostics
        ),
        "raw_latest_inventory_total_cells": _numeric_summary(
            diag.get("latest_total_cells") for diag in diagnostics
        ),
        "raw_latest_inventory_truth_item_count": _numeric_summary(
            diag.get("truth_item_count") for diag in diagnostics
        ),
        "raw_latest_inventory_truth_match_files": sum(
            1 for diag in diagnostics if diag.get("truth_matches_latest")
        ),
        "raw_detail_truth_latest_match_rows": detail_truth_match_rows,
        "raw_detail_truth_latest_mismatch_rows": detail_truth_mismatch_rows,
        "raw_detail_truth_latest_unchecked_rows": detail_truth_unchecked_rows,
        "raw_duplicate_runtime_id_count": _numeric_summary(
            diag.get("duplicate_runtime_id_count") for diag in diagnostics
        ),
        "raw_duplicate_item_id_count": _numeric_summary(
            diag.get("duplicate_item_id_count") for diag in diagnostics
        ),
        "raw_duplicate_runtime_item_pair_count": _numeric_summary(
            diag.get("duplicate_runtime_item_pair_count") for diag in diagnostics
        ),
        "raw_missing_from_drop_universe_count": _numeric_summary(
            diag.get("missing_from_drop_universe_count") for diag in diagnostics
        ),
        "raw_known_temp_zodiac_count": _numeric_summary(
            diag.get("known_temp_zodiac_count") for diag in diagnostics
        ),
        "raw_non_zodiac_missing_from_drop_universe_count": _numeric_summary(
            diag.get("non_zodiac_missing_from_drop_universe_count")
            for diag in diagnostics
        ),
        "raw_drop_ref_excess_item_count": _numeric_summary(
            diag.get("drop_ref_excess_item_count") for diag in diagnostics
        ),
        "raw_drop_ref_excess_after_temp_zodiac_count": _numeric_summary(
            diag.get("drop_ref_excess_after_temp_zodiac_count")
            for diag in diagnostics
        ),
        "raw_round_cap_excess_item_count": _numeric_summary(
            diag.get("round_cap_excess_item_count") for diag in diagnostics
        ),
        "raw_round_cap_excess_after_temp_zodiac_count": _numeric_summary(
            diag.get("round_cap_excess_after_temp_zodiac_count")
            for diag in diagnostics
        ),
        "raw_latest_inventory_message_ids": _counter_dict(
            f"0x{int(message_id):04X}" if message_id is not None else None
            for message_id in (diag.get("latest_message_id") for diag in diagnostics)
        ),
        "raw_latest_inventory_rounds": _counter_dict(
            diag.get("latest_round_index") for diag in diagnostics
        ),
        "raw_latest_inventory_quality_counts": _merge_counter_dicts(
            diag.get("quality_counts", {}) for diag in diagnostics
        ),
        "raw_missing_from_drop_universe_examples": _merge_counter_dicts(
            diag.get("missing_from_drop_universe_examples", {})
            for diag in diagnostics
        ),
        "raw_inventory_slot_count": _numeric_summary(
            diag.get("inventory_slot_count") for diag in diagnostics
        ),
        "raw_occupied_slot_count": _numeric_summary(
            diag.get("occupied_slot_count") for diag in diagnostics
        ),
        "raw_item_candidate_count": _numeric_summary(
            diag.get("raw_item_candidate_count") for diag in diagnostics
        ),
        "raw_candidate_inventory_delta": _numeric_summary(
            diag.get("raw_candidate_inventory_delta") for diag in diagnostics
        ),
        "raw_occupied_slot_inventory_delta": _numeric_summary(
            diag.get("occupied_slot_inventory_delta") for diag in diagnostics
        ),
        "raw_inventory_slot_headroom": _numeric_summary(
            diag.get("inventory_slot_headroom") for diag in diagnostics
        ),
        "raw_inventory_slot_headroom_after_temp_zodiac": _numeric_summary(
            diag.get("inventory_slot_headroom_after_temp_zodiac")
            for diag in diagnostics
        ),
        "raw_action_result_count": _numeric_summary(
            diag.get("action_result_count") for diag in diagnostics
        ),
        "raw_action_observed_item_count_max": _numeric_summary(
            diag.get("action_observed_item_count_max") for diag in diagnostics
        ),
        "raw_full_observed_actions": _counter_dict(
            action_id
            for diag in diagnostics
            for action_id in diag.get("full_observed_action_ids", ())
        ),
        "raw_public_total_count_values": _counter_dict(
            value
            for diag in diagnostics
            for value in diag.get("public_total_count_values", ())
        ),
        "raw_inventory_example_files": [
            str(diag.get("file")) for diag in sorted(
                diagnostics,
                key=lambda item: str(item.get("file") or ""),
            )[:top]
        ],
        "raw_inventory_error_examples": (
            [*missing_refs[:top], *parse_errors[:top]][:top]
        ),
        "source_split_summary": _source_split_summary(
            seq,
            diagnostics,
            top=top,
        ),
        "residual_mode_summary": _residual_mode_summary(
            diagnostics,
            top=top,
        ),
        "capacity_semantic_matrix": _capacity_semantic_matrix(
            seq,
            diagnostics_by_path=diagnostics_by_path,
            resolved_by_ref=resolved_by_ref,
            top=top,
        ),
    }
    if detail_rows > 0:
        out["raw_detail_rows"] = _capacity_detail_rows(
            seq,
            resolved_by_ref=resolved_by_ref,
            diagnostics_by_path=diagnostics_by_path,
            limit=detail_rows,
        )
    return out


def _capacity_cases(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(case) for case in row.get("item_count_capacity", {}).get("cases", ()))


def _case_match(row: Mapping[str, Any], selected_case: str) -> bool:
    if selected_case == "all":
        return True
    return selected_case in _capacity_cases(row)


def _bucket_match(row: Mapping[str, Any], selected_bucket: str) -> bool:
    if selected_bucket == "all":
        return True
    value = row.get("consistency_bucket")
    bucket = str(value) if value not in (None, "") else "none"
    return bucket == selected_bucket


def _consistency_class_counts(
    rows: Iterable[Mapping[str, Any]],
    *,
    top: int,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        classes = row.get("consistency_classes") or ()
        if isinstance(classes, str):
            classes = tuple(token for token in classes.split(";") if token)
        for value in classes:
            counts[str(value) if value not in (None, "") else "none"] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top])


def _sampler_table_summary(map_id: int, tables: Any) -> dict[str, Any]:
    bid_map = tables.maps.get(int(map_id))
    if bid_map is None:
        return {
            "map_name": None,
            "drop_pool_id": None,
            "bidmap_items_per_session_min": None,
            "bidmap_items_per_session_max": None,
            "sub_pool_count": 0,
            "sampler_pool_count": 0,
            "sampler_max_count_per_draw": None,
            "sampler_possible_item_count_max": None,
            "sampler_entries_nmax_gt1": 0,
            "sampler_leaf_n_min": {"n": 0, "avg": None, "p90": None, "max": None},
            "sampler_leaf_n_max": {"n": 0, "avg": None, "p90": None, "max": None},
            "bidmap_raw_column_count": None,
            "bidmap_drop_ref_column_index": None,
            "bidmap_raw_col8": None,
            "bidmap_v300_flag_a": None,
            "bidmap_raw_col14": None,
            "bidmap_raw_col16": None,
            "bidmap_raw_col17": None,
            "bidmap_raw_drop_ref": None,
            "bidmap_raw_round_cap_min": None,
            "bidmap_raw_round_cap_max": None,
            "table_status": "missing_bidmap",
        }
    raw_column_count = len(getattr(bid_map, "raw_row", ()) or ())
    drop_ref_index = _bidmap_drop_ref_column_index(raw_column_count)
    round_caps = _bidmap_round_caps(bid_map)
    sampler = prepare_session_sampler(
        int(map_id),
        maps=tables.maps,
        drops=tables.drops,
        items=tables.items,
    )
    max_count_per_draw = 0
    entries_nmax_gt1 = 0
    pool_item_counts: list[int] = []
    leaf_n_min_values: list[int] = []
    leaf_n_max_values: list[int] = []
    for pool in sampler.pools:
        pool_item_counts.append(len(pool.items))
        leaf_n_min_values.extend(int(value) for value in pool.n_min)
        leaf_n_max_values.extend(int(value) for value in pool.n_max)
        n_max_values = [int(value) for value in pool.n_max]
        if n_max_values:
            max_count_per_draw = max(max_count_per_draw, max(n_max_values))
            entries_nmax_gt1 += sum(1 for value in n_max_values if value > 1)
    possible_max = (
        int(sampler.items_per_session_max) * int(max_count_per_draw)
        if max_count_per_draw > 0
        else None
    )
    return {
        "map_name": bid_map.name,
        "drop_pool_id": bid_map.drop_pool_id,
        "bidmap_items_per_session_min": bid_map.items_per_session_min,
        "bidmap_items_per_session_max": bid_map.items_per_session_max,
        "sub_pool_count": len(bid_map.sub_pool_weights),
        "sub_pool_weights": ",".join(
            f"{sub_map_id}:{weight}" for sub_map_id, weight in bid_map.sub_pool_weights[:8]
        ),
        "sampler_pool_count": len(sampler.pools),
        "sampler_pool_item_count_summary": _numeric_summary(pool_item_counts),
        "sampler_max_count_per_draw": max_count_per_draw or None,
        "sampler_possible_item_count_max": possible_max,
        "sampler_entries_nmax_gt1": entries_nmax_gt1,
        "sampler_leaf_n_min": _numeric_summary(leaf_n_min_values),
        "sampler_leaf_n_max": _numeric_summary(leaf_n_max_values),
        "bidmap_raw_column_count": raw_column_count,
        "bidmap_drop_ref_column_index": drop_ref_index,
        "bidmap_raw_col8": (
            bid_map.raw_row[8] if len(bid_map.raw_row) > 8 else None
        ),
        "bidmap_v300_flag_a": (
            _safe_int(bid_map.raw_row[8]) if raw_column_count == 23 else None
        ),
        "bidmap_raw_col14": (
            bid_map.raw_row[14] if len(bid_map.raw_row) > 14 else None
        ),
        "bidmap_raw_col16": (
            bid_map.raw_row[16] if len(bid_map.raw_row) > 16 else None
        ),
        "bidmap_raw_col17": (
            bid_map.raw_row[17] if len(bid_map.raw_row) > 17 else None
        ),
        "bidmap_raw_drop_ref": (
            bid_map.raw_row[drop_ref_index]
            if len(bid_map.raw_row) > drop_ref_index
            else None
        ),
        "bidmap_raw_round_cap_min": min(round_caps) if round_caps else None,
        "bidmap_raw_round_cap_max": max(round_caps) if round_caps else None,
        "table_status": "ok",
    }


def summarize_capacity_table_audit(
    details: Iterable[dict[str, Any]],
    *,
    tables: Any,
    selected_case: str = DEFAULT_CASE,
    selected_bucket: str = DEFAULT_BUCKET,
    top: int = 8,
    detail_rows: int = 0,
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in details:
        if not _case_match(row, selected_case):
            continue
        if not _bucket_match(row, selected_bucket):
            continue
        groups[str(row.get("map_id") if row.get("map_id") not in (None, "") else "none")].append(row)

    out: list[dict[str, Any]] = []
    for map_value, rows in groups.items():
        if map_value == "none":
            table = _sampler_table_summary(-1, tables)
        else:
            table = _sampler_table_summary(int(map_value), tables)
        capacity = tuple(row["item_count_capacity"] for row in rows)
        truth_counts = _numeric_values(row.get("truth_item_count") for row in capacity)
        possible_max = _float_or_none(table.get("sampler_possible_item_count_max"))
        round_cap_max = _float_or_none(table.get("bidmap_raw_round_cap_max"))
        truth_max = max(truth_counts) if truth_counts else None
        table_impossible_rows = 0
        if possible_max is not None:
            table_impossible_rows = sum(
                1
                for value in truth_counts
                if float(value) > possible_max
            )
        round_cap_impossible_rows = 0
        if round_cap_max is not None:
            round_cap_impossible_rows = sum(
                1
                for value in truth_counts
                if float(value) > round_cap_max
            )
        status = "pass"
        if table_impossible_rows:
            status = "table_possible_max_below_truth"
        elif any(
            float(row.get("truth_prior_max_delta") or 0.0) > 0.0
            for row in capacity
        ):
            status = "bidmap_prior_max_below_truth"
        case_counts = Counter(case for row in rows for case in _capacity_cases(row))
        row_out = {
            "map_id": map_value,
            "rows": len(rows),
            "status": status,
            "table_impossible_rows": table_impossible_rows,
            "round_cap_impossible_rows": round_cap_impossible_rows,
            "capacity_cases": dict(
                sorted(case_counts.items(), key=lambda item: (-item[1], item[0]))[:top]
            ),
            "consistency_bucket_counts": _counter_dict(
                (row.get("consistency_bucket") for row in rows),
                top=top,
            ),
            "consistency_class_counts": _consistency_class_counts(
                rows,
                top=top,
            ),
            "target_count": _numeric_summary(
                row.get("total_count_target") for row in capacity
            ),
            "truth_item_count": _numeric_summary(
                row.get("truth_item_count") for row in capacity
            ),
            "target_prior_max_delta": _numeric_summary(
                row.get("target_prior_max_delta") for row in capacity
            ),
            "truth_prior_max_delta": _numeric_summary(
                row.get("truth_prior_max_delta") for row in capacity
            ),
            "target_truth_delta_counts": _delta_counts(
                row.get("target_truth_delta") for row in capacity
            ),
            "source_counts": _counter_dict(
                (row.get("total_count_source") for row in capacity),
                top=top,
            ),
            "top_profiles": _counter_dict(
                (row.get("hero_map_evidence_profile") for row in rows),
                top=top,
            ),
            "example_files": [
                str(row.get("file"))
                for row in sorted(rows, key=lambda item: str(item.get("file") or ""))[:3]
            ],
            **_inventory_diagnostics_for_rows(
                rows,
                tables=tables,
                sample_root=sample_root,
                top=top,
                detail_rows=detail_rows,
            ),
            **table,
        }
        row_out["capacity_semantic_summary"] = _capacity_semantic_summary(row_out)
        out.append(row_out)
    return sorted(
        out,
        key=lambda row: (
            0 if row["status"] == "table_possible_max_below_truth" else 1,
            -int(row["table_impossible_rows"]),
            -int(row["rows"]),
            -float(row["truth_item_count"]["max"] or 0.0),
            str(row["map_id"]),
        ),
    )


def _format_counts(counts: Mapping[str, int]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def _format_summary(summary: Mapping[str, Any]) -> str:
    return (
        f"n={summary['n']}"
        f"/avg={summary['avg']}"
        f"/p90={summary['p90']}"
        f"/max={summary['max']}"
    )


def _format_residual_mode_rows(summary: Mapping[str, Any]) -> str:
    parts = []
    for row in summary.get("by_mode", ()):
        parts.append(
            (
                f"{row['mode']}:files={row['file_count']}"
                f"/drop={_format_summary(row['drop_ref_excess_after_temp_zodiac_count'])}"
                f"/round={_format_summary(row['round_cap_excess_after_temp_zodiac_count'])}"
                f"/missing={_format_summary(row['non_zodiac_missing_from_drop_universe_count'])}"
            )
        )
    return "|".join(parts) or "-"


def _merge_capacity_semantic_matrix(
    rows: Iterable[Mapping[str, Any]],
    *,
    top: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        for item in row.get("capacity_semantic_matrix", ()):
            key = tuple(str(item.get(field) or "none") for field in _MATRIX_FIELDS)
            if key not in grouped:
                grouped[key] = {
                    **dict(zip(_MATRIX_FIELDS, key)),
                    "rows": 0,
                    "files": 0,
                    "map_ids": Counter(),
                    "capacity_cases": Counter(),
                    "semantic_status_counts": Counter(),
                    "example_files": [],
                }
            acc = grouped[key]
            acc["rows"] += int(item.get("rows") or 0)
            acc["files"] += int(item.get("files") or 0)
            acc["map_ids"].update(
                {str(key): int(value) for key, value in item.get("map_ids", {}).items()}
            )
            acc["capacity_cases"].update(
                {
                    str(key): int(value)
                    for key, value in item.get("capacity_cases", {}).items()
                }
            )
            acc["semantic_status_counts"].update(
                {
                    str(key): int(value)
                    for key, value in item.get("semantic_status_counts", {}).items()
                }
            )
            for example in item.get("example_files", ()):
                if example not in acc["example_files"]:
                    acc["example_files"].append(example)

    out: list[dict[str, Any]] = []
    for acc in grouped.values():
        out.append(
            {
                **{field: acc[field] for field in _MATRIX_FIELDS},
                "rows": acc["rows"],
                "files": acc["files"],
                "map_ids": dict(
                    sorted(
                        acc["map_ids"].items(),
                        key=lambda item: (-item[1], item[0]),
                    )[:top]
                ),
                "capacity_cases": dict(
                    sorted(
                        acc["capacity_cases"].items(),
                        key=lambda item: (-item[1], item[0]),
                    )[:top]
                ),
                "semantic_status_counts": dict(
                    sorted(
                        acc["semantic_status_counts"].items(),
                        key=lambda item: (-item[1], item[0]),
                    )[:top]
                ),
                "example_files": acc["example_files"][:top],
            }
        )
    return sorted(
        out,
        key=lambda row: (
            -int(row["rows"]),
            -int(row["files"]),
            str(row["consistency_bucket"]),
            str(row["residual_mode"]),
            str(row["map_family"]),
            str(row["total_count_source"]),
            str(row["capture_day"]),
        ),
    )[:top]


def _format_capacity_semantic_matrix(rows: Iterable[Mapping[str, Any]]) -> str:
    parts = []
    for row in rows:
        key = "/".join(str(row.get(field) or "none") for field in _MATRIX_FIELDS)
        parts.append(
            (
                f"{key}:rows={row['rows']}"
                f"/files={row['files']}"
                f"/maps={_format_counts(row['map_ids'])}"
                f"/statuses={_format_counts(row['semantic_status_counts'])}"
            )
        )
    return "|".join(parts) or "-"


def _collect_capacity_detail_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        map_id = row.get("map_id")
        map_status = row.get("status")
        semantic = row.get("capacity_semantic_summary")
        semantic_status = (
            semantic.get("status") if isinstance(semantic, Mapping) else None
        )
        for item in row.get("raw_detail_rows") or ():
            if not isinstance(item, Mapping):
                continue
            out.append(
                {
                    "map_id": map_id,
                    "map_status": map_status,
                    "map_semantic_status": semantic_status,
                    **dict(item),
                }
            )
            if len(out) >= limit:
                return out
    return out


def _format_capacity_detail_rows(rows: Iterable[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for row in rows:
        parts.append(
            (
                f"{row.get('map_id')}:{row.get('file')}"
                f"/residual={row.get('residual_mode')}"
                f"/semantic={row.get('semantic_status')}"
                f"/truth_delta={row.get('truth_prior_max_delta')}"
                f"/drop_after_temp={row.get('drop_ref_excess_after_temp_zodiac_count')}"
                f"/round_after_temp={row.get('round_cap_excess_after_temp_zodiac_count')}"
            )
        )
    return "|".join(parts) or "-"


def _dedupe_capacity_detail_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        key = (
            str(row.get("file_ref") or row.get("file") or ""),
            str(row.get("map_id") or ""),
            str(row.get("residual_mode") or ""),
            str(row.get("semantic_status") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(row))
    return out


def _capacity_detail_source_signal(row: Mapping[str, Any]) -> str:
    has_full_action = bool(row.get("full_observed_action_ids") or ())
    has_public_total = bool(row.get("public_total_count_values") or ())
    return (
        ("has_full_action" if has_full_action else "no_full_action")
        + "/"
        + ("has_public_total" if has_public_total else "no_public_total")
    )


def _capacity_detail_mechanism_candidate(row: Mapping[str, Any]) -> str:
    semantic = str(row.get("semantic_status") or "")
    residual = str(row.get("residual_mode") or "")
    non_zodiac_missing = _float_or_none(
        row.get("non_zodiac_missing_from_drop_universe_count")
    )
    if non_zodiac_missing is not None and non_zodiac_missing > 0:
        return "drop_universe_gap"
    if semantic == "watch_activity_extras_explain_drop_ref_gap":
        return "activity_extras_candidate"
    if residual == "round_cap_overflow" or semantic.startswith("blocked_round_cap"):
        return "round_cap_candidate_gap"
    if residual == "drop_ref_only_overflow" or semantic.startswith("blocked_drop_ref"):
        return "drop_ref_candidate_gap"
    if residual == "within_drop_ref":
        return "activity_extras_candidate"
    return "unclassified_capacity_detail"


def _capacity_detail_next_check(row: Mapping[str, Any]) -> str:
    candidate = _capacity_detail_mechanism_candidate(row)
    if candidate == "round_cap_candidate_gap":
        return "check_per_session_table_version_or_external_overlay"
    if candidate == "drop_ref_candidate_gap":
        return "check_drop_ref_source_semantics_or_activity_overlay"
    if candidate == "activity_extras_candidate":
        return "verify_activity_extras_table"
    if candidate == "drop_universe_gap":
        return "check_source_parser_drop_universe"
    return "manual_review"


def _capacity_detail_summary(
    rows: Iterable[Mapping[str, Any]],
    *,
    top: int,
) -> dict[str, Any]:
    seq = tuple(row for row in rows if isinstance(row, Mapping))
    unique = _dedupe_capacity_detail_rows(seq)
    for row in unique:
        row["mechanism_candidate"] = _capacity_detail_mechanism_candidate(row)
        row["next_check"] = _capacity_detail_next_check(row)
        row["source_signal"] = _capacity_detail_source_signal(row)
    return {
        "rows": len(seq),
        "unique_file_map_residual_rows": len(unique),
        "unique_files": len(
            {str(row.get("file_ref") or row.get("file")) for row in unique}
        ),
        "semantic_status_counts": _counter_dict(
            (row.get("semantic_status") for row in unique),
            top=top,
        ),
        "residual_mode_counts": _counter_dict(
            (row.get("residual_mode") for row in unique),
            top=top,
        ),
        "mechanism_candidate_counts": _counter_dict(
            (row.get("mechanism_candidate") for row in unique),
            top=top,
        ),
        "next_check_counts": _counter_dict(
            (row.get("next_check") for row in unique),
            top=top,
        ),
        "source_signal_counts": _counter_dict(
            (row.get("source_signal") for row in unique),
            top=top,
        ),
        "map_counts": _counter_dict((row.get("map_id") for row in unique), top=top),
        "map_family_counts": _counter_dict(
            (row.get("map_family") for row in unique),
            top=top,
        ),
        "truth_prior_delta": _numeric_summary(
            (row.get("truth_prior_max_delta") for row in unique),
            digits=3,
        ),
        "drop_ref_excess_after_temp": _numeric_summary(
            (
                row.get("drop_ref_excess_after_temp_zodiac_count")
                for row in unique
            ),
            digits=3,
        ),
        "round_cap_excess_after_temp": _numeric_summary(
            (
                row.get("round_cap_excess_after_temp_zodiac_count")
                for row in unique
            ),
            digits=3,
        ),
        "top_examples": unique[:top],
    }


def _format_capacity_detail_summary(summary: Mapping[str, Any]) -> str:
    return ";".join(
        (
            f"rows={summary.get('rows')}",
            f"unique={summary.get('unique_file_map_residual_rows')}",
            "mechanisms="
            + _format_counts(summary.get("mechanism_candidate_counts") or {}),
            "next_checks="
            + _format_counts(summary.get("next_check_counts") or {}),
            "source_signals="
            + _format_counts(summary.get("source_signal_counts") or {}),
            "maps=" + _format_counts(summary.get("map_counts") or {}),
        )
    )


def _print_summary(rows: Iterable[Mapping[str, Any]], *, top: int) -> None:
    for row in tuple(rows)[:top]:
        print(
            " ".join(
                (
                    f"map_id={row['map_id']}",
                    f"map_name={json.dumps(row['map_name'], ensure_ascii=False)}",
                    f"status={row['status']}",
                    f"rows={row['rows']}",
                    f"table_impossible_rows={row['table_impossible_rows']}",
                    f"bidmap_items={row['bidmap_items_per_session_min']}-{row['bidmap_items_per_session_max']}",
                    f"bidmap_raw_cols={row['bidmap_raw_column_count']}",
                    f"drop_ref_col={row['bidmap_drop_ref_column_index']}",
                    f"raw_col8={json.dumps(row['bidmap_raw_col8'], ensure_ascii=False)}",
                    f"v300_flag_a={row['bidmap_v300_flag_a']}",
                    f"round_cap={row['bidmap_raw_round_cap_min']}-{row['bidmap_raw_round_cap_max']}",
                    f"raw_col14={json.dumps(row['bidmap_raw_col14'], ensure_ascii=False)}",
                    f"raw_col16={json.dumps(row['bidmap_raw_col16'], ensure_ascii=False)}",
                    f"raw_col17={json.dumps(row['bidmap_raw_col17'], ensure_ascii=False)}",
                    f"round_cap_impossible_rows={row['round_cap_impossible_rows']}",
                    f"sampler_possible_max={row['sampler_possible_item_count_max']}",
                    f"sampler_max_count_per_draw={row['sampler_max_count_per_draw']}",
                    f"sampler_nmax_gt1={row['sampler_entries_nmax_gt1']}",
                    f"sampler_leaf_nmax={_format_summary(row['sampler_leaf_n_max'])}",
                    f"sub_pool_count={row['sub_pool_count']}",
                    f"raw_inventory={row['raw_inventory_status']}",
                    f"raw_files={row['raw_inventory_file_count']}",
                    f"raw_states={_format_summary(row['raw_inventory_state_count'])}",
                    f"raw_slots={_format_summary(row['raw_inventory_slot_count'])}",
                    f"raw_slot_headroom={_format_summary(row['raw_inventory_slot_headroom'])}",
                    f"raw_slot_headroom_after_temp={_format_summary(row['raw_inventory_slot_headroom_after_temp_zodiac'])}",
                    f"raw_latest_count={_format_summary(row['raw_latest_inventory_item_count'])}",
                    f"raw_truth_match_rows={row['raw_detail_truth_latest_match_rows']}/{row['rows']}",
                    f"raw_candidate_delta={_format_summary(row['raw_candidate_inventory_delta'])}",
                    f"raw_occupied_delta={_format_summary(row['raw_occupied_slot_inventory_delta'])}",
                    f"raw_dup_runtime={_format_summary(row['raw_duplicate_runtime_id_count'])}",
                    f"raw_dup_pair={_format_summary(row['raw_duplicate_runtime_item_pair_count'])}",
                    f"raw_dup_item={_format_summary(row['raw_duplicate_item_id_count'])}",
                    f"raw_missing_drop={_format_summary(row['raw_missing_from_drop_universe_count'])}",
                    f"raw_temp_zodiac={_format_summary(row['raw_known_temp_zodiac_count'])}",
                    f"raw_non_zodiac_missing={_format_summary(row['raw_non_zodiac_missing_from_drop_universe_count'])}",
                    f"raw_drop_excess={_format_summary(row['raw_drop_ref_excess_item_count'])}",
                    f"raw_drop_excess_after_temp={_format_summary(row['raw_drop_ref_excess_after_temp_zodiac_count'])}",
                    f"raw_round_excess={_format_summary(row['raw_round_cap_excess_item_count'])}",
                    f"raw_round_excess_after_temp={_format_summary(row['raw_round_cap_excess_after_temp_zodiac_count'])}",
                    f"raw_msg={_format_counts(row['raw_latest_inventory_message_ids'])}",
                    f"raw_rounds={_format_counts(row['raw_latest_inventory_rounds'])}",
                    f"raw_q={_format_counts(row['raw_latest_inventory_quality_counts'])}",
                    f"raw_actions={_format_summary(row['raw_action_result_count'])}",
                    f"raw_action_observed_max={_format_summary(row['raw_action_observed_item_count_max'])}",
                    f"raw_full_actions={_format_counts(row['raw_full_observed_actions'])}",
                    f"raw_public_count={_format_counts(row['raw_public_total_count_values'])}",
                    f"raw_missing_items={_format_counts(row['raw_missing_from_drop_universe_examples'])}",
                    "source_split_prefix3="
                    + _format_counts(
                        row["source_split_summary"]["map_prefix3_counts"]
                    ),
                    "source_split_family="
                    + _format_counts(
                        row["source_split_summary"]["map_family_counts"]
                    ),
                    "source_split_days="
                    + _format_counts(
                        row["source_split_summary"]["capture_day_counts"]
                    ),
                    "source_split_target_source="
                    + _format_counts(
                        row["source_split_summary"]["total_count_source_counts"]
                    ),
                    "source_split_drop_after_temp="
                    + _format_summary(
                        row["source_split_summary"][
                            "drop_ref_excess_after_temp_zodiac_count"
                        ]
                    ),
                    "source_split_round_after_temp="
                    + _format_summary(
                        row["source_split_summary"][
                            "round_cap_excess_after_temp_zodiac_count"
                        ]
                    ),
                    "source_split_non_zodiac_missing="
                    + _format_summary(
                        row["source_split_summary"][
                            "non_zodiac_missing_from_drop_universe_count"
                        ]
                    ),
                    "residual_modes="
                    + _format_counts(row["residual_mode_summary"]["mode_counts"]),
                    "residual_mode_detail="
                    + _format_residual_mode_rows(row["residual_mode_summary"]),
                    "semantic_status="
                    + str(row["capacity_semantic_summary"]["status"]),
                    "semantic_blockers="
                    + (
                        ",".join(row["capacity_semantic_summary"]["blockers"])
                        or "-"
                    ),
                    "semantic_findings="
                    + (
                        ",".join(row["capacity_semantic_summary"]["findings"])
                        or "-"
                    ),
                    "semantic_matrix="
                    + _format_capacity_semantic_matrix(
                        row["capacity_semantic_matrix"]
                    ),
                    f"cases={_format_counts(row['capacity_cases'])}",
                    f"buckets={_format_counts(row['consistency_bucket_counts'])}",
                    f"classes={_format_counts(row['consistency_class_counts'])}",
                    f"sources={_format_counts(row['source_counts'])}",
                    f"target_count={_format_summary(row['target_count'])}",
                    f"truth_count={_format_summary(row['truth_item_count'])}",
                    f"truth_prior_delta={_format_summary(row['truth_prior_max_delta'])}",
                    f"target_truth_counts={_format_counts(row['target_truth_delta_counts'])}",
                    f"profiles={_format_counts(row['top_profiles'])}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit prior-stress capacity cases against raw BidMap/Drop tables.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--case", default=DEFAULT_CASE, help="Capacity case to audit, or all.")
    parser.add_argument(
        "--bucket",
        default=DEFAULT_BUCKET,
        help="Consistency bucket to audit, or all.",
    )
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument(
        "--details",
        type=int,
        default=0,
        help=(
            "Include up to N file-level semantic residual detail rows "
            "in JSON/summary output."
        ),
    )
    parser.add_argument("--posterior-trials", type=int, default=64)
    parser.add_argument("--posterior-seed", type=int, default=0)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    tables = load_monitor_tables()
    rows, errors = evaluate_paths(
        args.paths or _default_paths(),
        tables=tables,
        calibration_entries=load_prior_calibration_entries(
            _default_calibration_path()
        ),
        underestimate_repair_entries=load_underestimate_repair_entries(
            _default_underestimate_repair_path()
        ),
        tail_value_review_entries=load_tail_value_review_entries(
            _default_tail_value_review_path()
        ),
        posterior_trials=args.posterior_trials,
        posterior_seed=args.posterior_seed,
    )
    details = summarize_prior_stress_details(rows)
    audit = summarize_capacity_table_audit(
        details,
        tables=tables,
        selected_case=args.case,
        selected_bucket=args.bucket,
        top=args.top,
        detail_rows=args.details,
    )
    detail_rows = _collect_capacity_detail_rows(audit, limit=args.details)
    detail_summary = _capacity_detail_summary(detail_rows, top=args.top)
    result = {
        "errors": errors,
        "case": args.case,
        "bucket": args.bucket,
        "semantic_matrix": _merge_capacity_semantic_matrix(audit, top=args.top),
        "detail_summary": detail_summary,
        "detail_rows": detail_rows,
        "rows": audit,
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if errors:
            print(f"errors={len(errors)}")
        print(f"case={args.case} bucket={args.bucket} groups={len(audit)}")
        print(
            "semantic_matrix_all="
            + _format_capacity_semantic_matrix(result["semantic_matrix"])
        )
        if args.details:
            print(
                "detail_summary="
                + _format_capacity_detail_summary(result["detail_summary"])
            )
            print(
                "detail_rows="
                + _format_capacity_detail_rows(result["detail_rows"])
            )
        _print_summary(audit, top=args.top)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
