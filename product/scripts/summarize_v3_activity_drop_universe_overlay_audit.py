"""Summarize activity drop-universe overlay evidence for v3 guard-loss blockers."""

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
DEFAULT_ACTIVITY_MAPPING = ROOT / ".tmp" / "codex" / "v3_activity_mapping_rankmap_latest.json"
DEFAULT_GUARD_LOSS_CONTEXT = ROOT / ".tmp" / "codex" / "v3_guard_loss_source_context_latest.json"
MAX_EXAMPLES_PER_MAP = 3


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
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


def _compact(row: Mapping[str, Any], keys: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in keys:
        value = row.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, (dict, list)) and not value:
            continue
        out[key] = value
    return out


def _file_sort_key(row: Mapping[str, Any]) -> str:
    return str(row.get("file") or "")


def _index_guard_rows(guard_loss_context: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not isinstance(guard_loss_context, Mapping):
        return {}
    out: dict[str, Mapping[str, Any]] = {}
    for row in _as_list(guard_loss_context.get("rows")):
        if not isinstance(row, Mapping) or row.get("map_id") is None:
            continue
        out[str(row.get("map_id"))] = row
    return out


def _index_activity_maps(activity_mapping: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row.get("map_id")): row
        for row in _as_list(activity_mapping.get("map_results"))
        if isinstance(row, Mapping) and row.get("map_id") is not None
    }


def _activity_files_by_map(activity_mapping: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
    out: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in _as_list(activity_mapping.get("file_results")):
        if not isinstance(row, Mapping) or row.get("map_id") is None:
            continue
        out[str(row.get("map_id"))].append(row)
    return out


def _candidate_example(candidate: Mapping[str, Any]) -> dict[str, Any]:
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


def _file_example(row: Mapping[str, Any]) -> dict[str, Any]:
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
        _candidate_example(candidate)
        for candidate in _as_list(row.get("candidates"))[:MAX_EXAMPLES_PER_MAP]
        if isinstance(candidate, Mapping)
    ]
    if candidates:
        out["candidate_examples"] = candidates
    return out


def _candidate_stats(file_rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    candidate_rows = [
        candidate
        for row in file_rows
        for candidate in _as_list(row.get("candidates"))
        if isinstance(candidate, Mapping)
    ]
    statuses = Counter(str(row.get("status") or "unknown") for row in candidate_rows)
    candidate_map_ids = Counter(
        str(row.get("candidate_map_id") or "unknown") for row in candidate_rows
    )
    drop_pool_ids = Counter(str(row.get("drop_pool_id") or "unknown") for row in candidate_rows)
    missing_rates = [
        value
        for value in (_float(row.get("missing_item_rate")) for row in candidate_rows)
        if value is not None
    ]
    zero_item_rows = sum(
        1 for row in candidate_rows if _int(row.get("zero_item_probability_items")) > 0
    )
    return {
        "candidate_rows": len(candidate_rows),
        "candidate_status_counts": _counter_dict(statuses),
        "candidate_map_ids": _counter_dict(candidate_map_ids),
        "drop_pool_ids": _counter_dict(drop_pool_ids),
        "missing_item_rate_max": max(missing_rates) if missing_rates else None,
        "missing_item_rate_positive_rows": sum(1 for value in missing_rates if value > 0.0),
        "zero_item_probability_positive_rows": zero_item_rows,
        "all_candidates_ok": bool(candidate_rows) and all(key == "ok" for key in statuses),
        "all_candidates_item_covered": (
            bool(candidate_rows)
            and missing_rates
            and max(missing_rates) == 0.0
            and zero_item_rows == 0
        ),
    }


def _row_status(
    *,
    guard_row: Mapping[str, Any],
    candidate_stats: Mapping[str, Any],
    winner_counts: Mapping[str, Any],
    item_winner_counts: Mapping[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    guard_status = str(guard_row.get("status") or "")
    guard_blocked = guard_status == "blocked_drop_universe_or_activity_overlay"
    if guard_blocked:
        reasons.append("guard_loss_drop_universe_overlap")
    if not candidate_stats.get("candidate_rows"):
        return "blocked_no_activity_candidate_evidence", reasons
    if not candidate_stats.get("all_candidates_ok"):
        reasons.append("candidate_status_not_all_ok")
        return "blocked_activity_candidate_status", reasons
    if not candidate_stats.get("all_candidates_item_covered"):
        reasons.append("candidate_item_universe_gap")
        return "blocked_activity_candidate_item_universe_gap", reasons
    reasons.append("candidate_item_universe_covered")
    mixed_winner = len(winner_counts) > 1 or len(item_winner_counts) > 1
    if mixed_winner:
        reasons.append("activity_mapping_mixed_winner")
        if guard_blocked:
            return "blocked_mixed_overlay_source_required", reasons
        return "watch_mixed_overlay_reference_only", reasons
    reasons.append("activity_mapping_single_winner")
    if guard_blocked:
        return "watch_single_overlay_source_required", reasons
    return "watch_overlay_reference_only", reasons


def summarize_activity_drop_universe_overlay(
    *,
    activity_mapping: Mapping[str, Any],
    guard_loss_context: Mapping[str, Any] | None = None,
    focus_maps: list[str] | None = None,
) -> dict[str, Any]:
    guard_by_map = _index_guard_rows(guard_loss_context)
    map_rows = _index_activity_maps(activity_mapping)
    files_by_map = _activity_files_by_map(activity_mapping)
    if focus_maps:
        maps = [str(map_id) for map_id in focus_maps]
    else:
        maps = sorted(map_rows)
    rows: list[dict[str, Any]] = []
    for map_id in maps:
        file_rows = sorted(files_by_map.get(map_id, []), key=_file_sort_key)
        map_row = _as_mapping(map_rows.get(map_id))
        guard_row = _as_mapping(guard_by_map.get(map_id))
        stats = _candidate_stats(file_rows)
        winner_counts = _as_mapping(map_row.get("winner_counts"))
        item_winner_counts = _as_mapping(map_row.get("item_winner_counts"))
        status, reasons = _row_status(
            guard_row=guard_row,
            candidate_stats=stats,
            winner_counts=winner_counts,
            item_winner_counts=item_winner_counts,
        )
        guard = _as_mapping(guard_row.get("guard"))
        cse = _as_mapping(guard_row.get("cse_map_entry"))
        rows.append(
            {
                "map_id": map_id,
                "status": status,
                "reasons": reasons,
                "hard_map_allowed": False,
                "hard_map_blocker": (
                    "mixed_activity_mapping_or_unverified_source_semantics"
                    if status.startswith(("blocked", "watch"))
                    else None
                ),
                "guard_loss_overlap": {
                    "status": guard_row.get("status"),
                    "p90_coverage_lost_rows": guard.get("p90_coverage_lost_rows"),
                    "rows": guard.get("rows"),
                    "reasons": list(guard_row.get("reasons") or []),
                    "cse_status": cse.get("status"),
                    "cse_gate_reason": cse.get("gate_reason"),
                    "non_zodiac_missing_max": cse.get("non_zodiac_missing_max"),
                },
                "activity_mapping": {
                    "files": map_row.get("files") or len(file_rows),
                    "winner_counts": dict(winner_counts),
                    "item_winner_counts": dict(item_winner_counts),
                    "rankmap_labels": dict(_as_mapping(map_row.get("rankmap_labels"))),
                    "rankmap_category_weight_profiles": dict(
                        _as_mapping(map_row.get("rankmap_category_weight_profiles"))
                    ),
                    **stats,
                },
                "file_examples": [
                    _file_example(row) for row in file_rows[:MAX_EXAMPLES_PER_MAP]
                ],
            }
        )
    status_counts = Counter(row["status"] for row in rows)
    candidate_covered_maps = sum(
        1
        for row in rows
        if _as_mapping(row.get("activity_mapping")).get("all_candidates_item_covered")
    )
    guard_overlap_maps = sum(
        1
        for row in rows
        if _as_mapping(row.get("guard_loss_overlap")).get("status") is not None
    )
    hard_map_blocked_maps = sum(
        1 for row in rows if row.get("hard_map_allowed") is False
    )
    overall_status = (
        "blocked_activity_overlay_source_required"
        if any(str(row["status"]).startswith("blocked") for row in rows)
        else "watch_activity_overlay_reference_only"
    )
    return {
        "status": overall_status,
        "shadow_only": True,
        "affects_bid": False,
        "hard_map_allowed": False,
        "rows": rows,
        "summary": {
            "maps": len(rows),
            "files": sum(
                _int(_as_mapping(row.get("activity_mapping")).get("files"))
                for row in rows
            ),
            "guard_loss_overlap_maps": guard_overlap_maps,
            "candidate_item_universe_covered_maps": candidate_covered_maps,
            "hard_map_blocked_maps": hard_map_blocked_maps,
            "status_counts": _counter_dict(status_counts),
        },
    }


def _format_counts(counts: Mapping[str, Any]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def print_summary(result: Mapping[str, Any]) -> None:
    summary = _as_mapping(result.get("summary"))
    print(
        "status={status} maps={maps} files={files} guard_overlap_maps={guard_maps} "
        "covered_maps={covered_maps} hard_map_blocked_maps={blocked_maps} "
        "status_counts={statuses}".format(
            status=result.get("status"),
            maps=summary.get("maps"),
            files=summary.get("files"),
            guard_maps=summary.get("guard_loss_overlap_maps"),
            covered_maps=summary.get("candidate_item_universe_covered_maps"),
            blocked_maps=summary.get("hard_map_blocked_maps"),
            statuses=_format_counts(_as_mapping(summary.get("status_counts"))),
        )
    )
    for row in _as_list(result.get("rows")):
        if not isinstance(row, Mapping):
            continue
        activity = _as_mapping(row.get("activity_mapping"))
        guard = _as_mapping(row.get("guard_loss_overlap"))
        print(
            "map={map_id} status={status} guard_status={guard_status} "
            "loss={loss}/{guard_rows} winners={winners} item_winners={item_winners} "
            "candidate_maps={candidate_maps} drop_pools={drop_pools} "
            "missing_item_rate_max={missing_rate} reasons={reasons}".format(
                map_id=row.get("map_id"),
                status=row.get("status"),
                guard_status=guard.get("status") or "-",
                loss=guard.get("p90_coverage_lost_rows"),
                guard_rows=guard.get("rows"),
                winners=_format_counts(_as_mapping(activity.get("winner_counts"))),
                item_winners=_format_counts(
                    _as_mapping(activity.get("item_winner_counts"))
                ),
                candidate_maps=_format_counts(
                    _as_mapping(activity.get("candidate_map_ids"))
                ),
                drop_pools=_format_counts(_as_mapping(activity.get("drop_pool_ids"))),
                missing_rate=activity.get("missing_item_rate_max"),
                reasons=",".join(str(reason) for reason in _as_list(row.get("reasons")))
                or "-",
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize activity drop-universe overlay evidence for v3 guard-loss blockers.",
    )
    parser.add_argument("--activity-mapping-json", type=Path, default=DEFAULT_ACTIVITY_MAPPING)
    parser.add_argument("--guard-loss-source-context-json", type=Path, default=DEFAULT_GUARD_LOSS_CONTEXT)
    parser.add_argument("--focus-map", action="append", default=[])
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    result = summarize_activity_drop_universe_overlay(
        activity_mapping=_load_json(args.activity_mapping_json),
        guard_loss_context=(
            _load_json(args.guard_loss_source_context_json)
            if args.guard_loss_source_context_json.exists()
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
