"""Audit session-capacity source gaps for v3 settlement over-cap blockers.

This script is diagnostic-only. It takes settlement source-semantics detail
rows, reparses the referenced captures, and separates exact session sources
from bucket/action/public signals that do not explain item-count capacity.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

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
DEFAULT_SAMPLE_ROOT = ROOT / "data" / "samples" / "fatbeans"
MAX_EXAMPLES = 5

from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansInventoryItem,
    FatbeansStateEvent,
    _ACTION_AVG_CELLS,
    _ACTION_COUNT,
    _ACTION_SESSION_FIELDS,
    _ACTION_TOTAL_CELLS,
    _ACTION_VALUE_SUM,
    _AHMAD_SKILL_SESSION_FIELDS,
    _PUBLIC_INFO_EXACT_UPDATE_PATHS,
    parse_fatbeans_capture,
)


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


def _same_int(left: Any, right: Any) -> bool:
    try:
        return int(left) == int(right) and float(left) == float(right)
    except (TypeError, ValueError):
        return False


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {
        key: count
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    }


def _base_file(value: Any) -> str:
    return str(value or "").split("#", 1)[0]


def _bucket_action_path(action_id: int) -> tuple[str, ...] | None:
    if action_id in _ACTION_TOTAL_CELLS:
        return ("bucket", str(_ACTION_TOTAL_CELLS[action_id]), "total_cells")
    if action_id in _ACTION_COUNT:
        return ("bucket", str(_ACTION_COUNT[action_id]), "count")
    if action_id in _ACTION_AVG_CELLS:
        return ("bucket", str(_ACTION_AVG_CELLS[action_id]), "avg_cells")
    if action_id in _ACTION_VALUE_SUM:
        return ("bucket", str(_ACTION_VALUE_SUM[action_id]), "value_sum")
    return None


def _inventory_summary(items: Sequence[FatbeansInventoryItem]) -> dict[str, int]:
    return {
        "total_item_count": len(items),
        "warehouse_total_cells": sum(int(item.cells) for item in items),
    }


def _latest_inventory_state(states: Sequence[FatbeansStateEvent], map_id: Any) -> FatbeansStateEvent | None:
    candidates = [
        state
        for state in states
        if state.inventory_items and str(state.map_id) == str(map_id)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda state: (state.sort_id, state.message_id))


def _source_event_digest(
    *,
    states: Sequence[FatbeansStateEvent],
    map_id: Any,
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    action_ids: Counter[str] = Counter()
    public_info_ids: Counter[str] = Counter()
    skill_ids: Counter[str] = Counter()
    session_sources: list[dict[str, Any]] = []
    warehouse_sources: list[dict[str, Any]] = []
    bucket_sources: list[dict[str, Any]] = []
    observed_action_payloads: list[dict[str, Any]] = []
    unknown_numeric_actions: list[dict[str, Any]] = []
    total_item_count = inventory.get("total_item_count")
    warehouse_total_cells = inventory.get("warehouse_total_cells")

    def add_session_source(source: Mapping[str, Any]) -> None:
        path = tuple(source.get("path") or ())
        value = source.get("value")
        if path == ("session", "total_item_count"):
            session_sources.append(
                {
                    **dict(source),
                    "matches_inventory": _same_int(value, total_item_count),
                }
            )
        elif path == ("session", "warehouse_total_cells"):
            warehouse_sources.append(
                {
                    **dict(source),
                    "matches_inventory": _same_int(value, warehouse_total_cells),
                }
            )

    for state in states:
        if str(state.map_id) != str(map_id):
            continue
        for result in state.action_results:
            action_ids[str(result.action_id)] += 1
            if result.observed_items:
                observed_action_payloads.append(
                    {
                        "source": "action_result",
                        "sort_id": state.sort_id,
                        "message_id": f"0x{state.message_id:04x}",
                        "action_id": result.action_id,
                        "observed_item_count": len(result.observed_items),
                        "matches_inventory_count": (
                            len(result.observed_items) == total_item_count
                        ),
                    }
                )
            if result.result is None:
                continue
            session_path = _ACTION_SESSION_FIELDS.get(result.action_id)
            if session_path is not None:
                add_session_source(
                    {
                        "source": "action_result",
                        "sort_id": state.sort_id,
                        "message_id": f"0x{state.message_id:04x}",
                        "action_id": result.action_id,
                        "result_field": result.result_field,
                        "path": list(session_path),
                        "value": result.result,
                    }
                )
                continue
            bucket_path = _bucket_action_path(result.action_id)
            if bucket_path is not None:
                bucket_sources.append(
                    {
                        "source": "action_result",
                        "sort_id": state.sort_id,
                        "message_id": f"0x{state.message_id:04x}",
                        "action_id": result.action_id,
                        "result_field": result.result_field,
                        "path": list(bucket_path),
                        "value": result.result,
                    }
                )
            elif isinstance(result.result, (int, float)):
                unknown_numeric_actions.append(
                    {
                        "source": "action_result",
                        "sort_id": state.sort_id,
                        "message_id": f"0x{state.message_id:04x}",
                        "action_id": result.action_id,
                        "result_field": result.result_field,
                        "value": result.result,
                    }
                )
        for info in state.public_infos:
            public_info_ids[str(info.info_id)] += 1
            path = _PUBLIC_INFO_EXACT_UPDATE_PATHS.get(info.info_id)
            if path is None:
                continue
            source = {
                "source": "public_info",
                "sort_id": state.sort_id,
                "message_id": f"0x{state.message_id:04x}",
                "info_id": info.info_id,
                "value_field": info.value_field,
                "path": list(path),
                "value": info.value,
            }
            if path[0] == "session":
                add_session_source(source)
            else:
                bucket_sources.append(source)
        for reveal in state.skill_reveals:
            skill_ids[str(reveal.skill_id)] += 1
            path = _AHMAD_SKILL_SESSION_FIELDS.get(reveal.skill_id)
            if path is None or reveal.result is None:
                continue
            add_session_source(
                {
                    "source": "skill_reveal",
                    "sort_id": state.sort_id,
                    "message_id": f"0x{state.message_id:04x}",
                    "skill_id": reveal.skill_id,
                    "hero_id": reveal.hero_id,
                    "result_field": reveal.result_field,
                    "path": list(path),
                    "value": reveal.result,
                }
            )

    exact_session_count = [
        row
        for row in session_sources
        if tuple(row.get("path") or ()) == ("session", "total_item_count")
        and row.get("matches_inventory") is True
    ]
    exact_warehouse_cells = [
        row
        for row in warehouse_sources
        if row.get("matches_inventory") is True
    ]
    full_action_payloads = [
        row
        for row in observed_action_payloads
        if row.get("matches_inventory_count") is True
    ]
    return {
        "inventory": dict(inventory),
        "action_id_counts": _counter_dict(action_ids),
        "public_info_id_counts": _counter_dict(public_info_ids),
        "skill_id_counts": _counter_dict(skill_ids),
        "session_count_sources": exact_session_count,
        "warehouse_cells_sources": exact_warehouse_cells,
        "bucket_sources": bucket_sources[:MAX_EXAMPLES],
        "observed_action_payloads": observed_action_payloads[:MAX_EXAMPLES],
        "unknown_numeric_actions": unknown_numeric_actions[:MAX_EXAMPLES],
        "session_count_source_count": len(exact_session_count),
        "warehouse_cells_source_count": len(exact_warehouse_cells),
        "full_action_payload_count": len(full_action_payloads),
        "bucket_source_count": len(bucket_sources),
        "observed_action_payload_count": len(observed_action_payloads),
        "unknown_numeric_action_count": len(unknown_numeric_actions),
    }


def _row_status(row: Mapping[str, Any], digest: Mapping[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if _int(digest.get("session_count_source_count")) > 0:
        reasons.append("exact_session_total_item_count_observed")
        return "watch_exact_session_count_source_observed", reasons
    if _int(digest.get("full_action_payload_count")) > 0:
        reasons.append("full_action_payload_matches_inventory_count")
        return "watch_full_action_payload_source_observed", reasons
    if _int(digest.get("warehouse_cells_source_count")) > 0:
        reasons.append("warehouse_cells_source_observed_without_item_count")
        return "watch_warehouse_cells_only_no_session_count", reasons
    if (
        str(row.get("mechanism_class") or "") == "session_capacity_source_semantics"
        and _int(digest.get("bucket_source_count")) > 0
        and _int(digest.get("unknown_numeric_action_count")) == 0
    ):
        reasons.append("only_bucket_or_non_session_numeric_sources_observed")
        return "blocked_session_capacity_source_gap_bucket_only", reasons
    if str(row.get("mechanism_class") or "") == "session_capacity_source_semantics":
        reasons.append("no_exact_session_capacity_source_observed")
        return "blocked_session_capacity_source_gap_unresolved", reasons
    if _int(digest.get("bucket_source_count")) > 0 or _int(digest.get("observed_action_payload_count")) > 0:
        reasons.append("non_session_sources_observed_for_non_session_blocker")
        return "watch_non_session_source_context", reasons
    reasons.append("no_mapped_event_source_observed")
    return "watch_no_mapped_event_source", reasons


def _source_rows(
    source_semantics: Mapping[str, Any],
    *,
    focus_maps: Iterable[str],
) -> list[Mapping[str, Any]]:
    focus = {str(item) for item in focus_maps if str(item)}
    rows = [
        row
        for row in _as_list(source_semantics.get("detail_rows"))
        if isinstance(row, Mapping) and row.get("file") and row.get("map_id") is not None
    ]
    if focus:
        return [row for row in rows if str(row.get("map_id")) in focus]
    session_maps = {
        str(row.get("map_id"))
        for row in rows
        if row.get("mechanism_class") == "session_capacity_source_semantics"
    }
    return [row for row in rows if str(row.get("map_id")) in session_maps]


def summarize_session_capacity_source_gap(
    *,
    source_semantics: Mapping[str, Any],
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
    focus_maps: Iterable[str] = (),
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    parsed_cache: dict[str, tuple[FatbeansStateEvent, ...]] = {}
    for source_row in _source_rows(source_semantics, focus_maps=focus_maps):
        file = _base_file(source_row.get("file"))
        path = sample_root / file
        if file not in parsed_cache:
            try:
                parsed_cache[file] = tuple(parse_fatbeans_capture(path).states)
            except Exception as exc:  # pragma: no cover - defensive for ad hoc captures.
                errors.append(f"{file}: {exc}")
                continue
        states = parsed_cache[file]
        inventory_state = _latest_inventory_state(states, source_row.get("map_id"))
        inventory = (
            _inventory_summary(inventory_state.inventory_items)
            if inventory_state is not None
            else {}
        )
        digest = _source_event_digest(
            states=states,
            map_id=source_row.get("map_id"),
            inventory=inventory,
        )
        status, reasons = _row_status(source_row, digest)
        rows.append(
            {
                "file": file,
                "map_id": source_row.get("map_id"),
                "map_family": source_row.get("map_family"),
                "unique_residual_mode": source_row.get("unique_residual_mode"),
                "mechanism_class": source_row.get("mechanism_class"),
                "source_context_class": source_row.get("source_context_class"),
                "source_evidence_class": source_row.get("source_evidence_class"),
                "inventory_count": source_row.get("inventory_count"),
                "unique_non_temp_item_id_count": source_row.get(
                    "unique_non_temp_item_id_count"
                ),
                "bidmap_items_per_session_max": source_row.get(
                    "bidmap_items_per_session_max"
                ),
                "bidmap_raw_round_cap_max": source_row.get(
                    "bidmap_raw_round_cap_max"
                ),
                "event_public_total_match": source_row.get("event_public_total_match"),
                "event_action_result_count_all": source_row.get(
                    "event_action_result_count_all"
                ),
                "status": status,
                "reasons": reasons,
                "event_source_digest": digest,
            }
        )

    status_counts = Counter(str(row.get("status")) for row in rows)
    mechanism_counts = Counter(str(row.get("mechanism_class")) for row in rows)
    residual_counts = Counter(str(row.get("unique_residual_mode")) for row in rows)
    context_counts = Counter(str(row.get("source_context_class")) for row in rows)
    blocked_examples = [
        {
            "file": row.get("file"),
            "map_id": row.get("map_id"),
            "status": row.get("status"),
            "unique_residual_mode": row.get("unique_residual_mode"),
            "mechanism_class": row.get("mechanism_class"),
            "source_context_class": row.get("source_context_class"),
            "inventory": _as_mapping(row.get("event_source_digest")).get("inventory"),
            "action_id_counts": _as_mapping(row.get("event_source_digest")).get(
                "action_id_counts"
            ),
            "public_info_id_counts": _as_mapping(row.get("event_source_digest")).get(
                "public_info_id_counts"
            ),
            "skill_id_counts": _as_mapping(row.get("event_source_digest")).get(
                "skill_id_counts"
            ),
            "bucket_source_count": _as_mapping(row.get("event_source_digest")).get(
                "bucket_source_count"
            ),
            "session_count_source_count": _as_mapping(row.get("event_source_digest")).get(
                "session_count_source_count"
            ),
        }
        for row in rows
        if str(row.get("status")).startswith("blocked")
    ][:MAX_EXAMPLES]
    return {
        "status": (
            "blocked_session_capacity_source_gap"
            if any(str(row.get("status")).startswith("blocked") for row in rows)
            else "watch_session_capacity_source_gap_audit_only"
        ),
        "shadow_only": True,
        "affects_bid": False,
        "errors": errors,
        "rows": rows,
        "summary": {
            "rows": len(rows),
            "files": len({row.get("file") for row in rows}),
            "maps": len({str(row.get("map_id")) for row in rows if row.get("map_id") is not None}),
            "session_capacity_rows": mechanism_counts.get(
                "session_capacity_source_semantics",
                0,
            ),
            "unique_round_overflow_rows": residual_counts.get(
                "unique_round_cap_overflow_after_temp",
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
            "warehouse_cells_only_rows": status_counts.get(
                "watch_warehouse_cells_only_no_session_count",
                0,
            ),
            "bucket_only_blocked_rows": status_counts.get(
                "blocked_session_capacity_source_gap_bucket_only",
                0,
            ),
            "unresolved_session_capacity_rows": sum(
                count
                for status, count in status_counts.items()
                if status.startswith("blocked_session_capacity_source_gap")
            ),
            "status_counts": _counter_dict(status_counts),
            "mechanism_counts": _counter_dict(mechanism_counts),
            "unique_residual_mode_counts": _counter_dict(residual_counts),
            "source_context_counts": _counter_dict(context_counts),
            "top_blocked_examples": blocked_examples,
        },
    }


def _format_counts(counts: Mapping[str, Any]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def print_summary(result: Mapping[str, Any]) -> None:
    summary = _as_mapping(result.get("summary"))
    print(
        "status={status} rows={rows} files={files} maps={maps} "
        "session_capacity_rows={session_rows} exact_session_count_rows={exact_rows} "
        "warehouse_only_rows={warehouse_rows} bucket_only_blocked={bucket_rows} "
        "unresolved_session_rows={unresolved} statuses={statuses}".format(
            status=result.get("status"),
            rows=summary.get("rows"),
            files=summary.get("files"),
            maps=summary.get("maps"),
            session_rows=summary.get("session_capacity_rows"),
            exact_rows=summary.get("exact_session_count_source_rows"),
            warehouse_rows=summary.get("warehouse_cells_only_rows"),
            bucket_rows=summary.get("bucket_only_blocked_rows"),
            unresolved=summary.get("unresolved_session_capacity_rows"),
            statuses=_format_counts(_as_mapping(summary.get("status_counts"))),
        )
    )
    for row in _as_list(result.get("rows"))[:8]:
        if not isinstance(row, Mapping):
            continue
        digest = _as_mapping(row.get("event_source_digest"))
        print(
            "file={file} map={map_id} status={status} residual={residual} "
            "mechanism={mechanism} context={context} inventory={inventory} "
            "session_count_sources={session_sources} warehouse_sources={warehouse_sources} "
            "bucket_sources={bucket_sources} actions={actions} public={public}".format(
                file=row.get("file"),
                map_id=row.get("map_id"),
                status=row.get("status"),
                residual=row.get("unique_residual_mode"),
                mechanism=row.get("mechanism_class"),
                context=row.get("source_context_class"),
                inventory=digest.get("inventory"),
                session_sources=digest.get("session_count_source_count"),
                warehouse_sources=digest.get("warehouse_cells_source_count"),
                bucket_sources=digest.get("bucket_source_count"),
                actions=_format_counts(_as_mapping(digest.get("action_id_counts"))),
                public=_format_counts(_as_mapping(digest.get("public_info_id_counts"))),
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize v3 session-capacity source gaps from settlement source details.",
    )
    parser.add_argument("--source-semantics-json", type=Path, default=None)
    parser.add_argument("--sample-root", type=Path, default=DEFAULT_SAMPLE_ROOT)
    parser.add_argument("--focus-map", action="append", default=[])
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    source_path = args.source_semantics_json or _default_source_path()
    result = summarize_session_capacity_source_gap(
        source_semantics=_load_json(source_path),
        sample_root=args.sample_root,
        focus_maps=args.focus_map,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_summary(result)
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
