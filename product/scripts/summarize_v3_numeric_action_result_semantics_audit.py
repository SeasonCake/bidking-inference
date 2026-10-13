"""Audit numeric Fatbeans action-result semantics for v3 source blockers.

This script is diagnostic-only. It compares parsed numeric action results
against the nearest settlement inventory so bucket count/cell actions are not
misread as session-capacity evidence.
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
DEFAULT_SAMPLE_ROOT = ROOT / "data" / "samples" / "fatbeans"

from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansActionResult,
    FatbeansInventoryItem,
    FatbeansStateEvent,
    _ACTION_AVG_CELLS,
    _ACTION_COUNT,
    _ACTION_SESSION_FIELDS,
    _ACTION_TOTAL_CELLS,
    _ACTION_VALUE_SUM,
    parse_fatbeans_capture,
)


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bucket_qualities(quality: int) -> tuple[int, ...]:
    if quality == 1:
        return (1, 2)
    return (quality,)


def _same_number(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) <= 1e-9
    except (TypeError, ValueError):
        return False


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {
        key: count
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    }


def _iter_json_paths(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(path.rglob("*.json"))
        elif path.exists():
            yield path


def _inventory_summary(items: Sequence[FatbeansInventoryItem]) -> dict[str, Any]:
    count_by_quality: Counter[str] = Counter()
    cells_by_quality: Counter[str] = Counter()
    for item in items:
        quality = str(item.quality)
        count_by_quality[quality] += 1
        cells_by_quality[quality] += int(item.cells)
    bucket_summary: dict[str, dict[str, int]] = {}
    for quality in (1, 3, 4, 5, 6):
        qualities = _bucket_qualities(quality)
        bucket_summary[str(quality)] = {
            "count": sum(count_by_quality.get(str(item), 0) for item in qualities),
            "total_cells": sum(cells_by_quality.get(str(item), 0) for item in qualities),
        }
    return {
        "total_item_count": len(items),
        "warehouse_total_cells": sum(cells_by_quality.values()),
        "count_by_quality": _counter_dict(count_by_quality),
        "cells_by_quality": _counter_dict(cells_by_quality),
        "buckets": bucket_summary,
    }


def _nearest_inventory_state(
    states: Sequence[FatbeansStateEvent],
    action_state: FatbeansStateEvent,
) -> FatbeansStateEvent | None:
    same_context = [
        state
        for state in states
        if state.inventory_items
        and state.map_id == action_state.map_id
        and (
            action_state.session_id is None
            or state.session_id is None
            or state.session_id == action_state.session_id
        )
    ]
    after = [state for state in same_context if state.sort_id >= action_state.sort_id]
    if after:
        return min(after, key=lambda state: (state.sort_id, state.message_id))
    before = [state for state in same_context if state.sort_id < action_state.sort_id]
    if before:
        return max(before, key=lambda state: (state.sort_id, state.message_id))
    return None


def _action_expected_semantic(
    action_id: int,
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    if action_id in _ACTION_SESSION_FIELDS:
        path = tuple(_ACTION_SESSION_FIELDS[action_id])
        field = path[-1]
        return {
            "expected_semantic": f"session_{field}",
            "expected_path": list(path),
            "expected_quality": None,
            "expected_value": inventory.get(field),
            "parser_implication": "session_capacity_signal",
        }
    if action_id in _ACTION_TOTAL_CELLS:
        quality = _ACTION_TOTAL_CELLS[action_id]
        bucket = _as_mapping(_as_mapping(inventory.get("buckets")).get(str(quality)))
        return {
            "expected_semantic": "bucket_total_cells",
            "expected_path": ["bucket", str(quality), "total_cells"],
            "expected_quality": quality,
            "expected_value": bucket.get("total_cells"),
            "parser_implication": "not_session_capacity_signal",
        }
    if action_id in _ACTION_COUNT:
        quality = _ACTION_COUNT[action_id]
        bucket = _as_mapping(_as_mapping(inventory.get("buckets")).get(str(quality)))
        return {
            "expected_semantic": "bucket_count",
            "expected_path": ["bucket", str(quality), "count"],
            "expected_quality": quality,
            "expected_value": bucket.get("count"),
            "parser_implication": "not_session_capacity_signal",
        }
    if action_id in _ACTION_AVG_CELLS:
        quality = _ACTION_AVG_CELLS[action_id]
        bucket = _as_mapping(_as_mapping(inventory.get("buckets")).get(str(quality)))
        count = _int_or_none(bucket.get("count"))
        total_cells = _int_or_none(bucket.get("total_cells"))
        expected = None
        if count and total_cells is not None:
            expected = total_cells / count
        return {
            "expected_semantic": "bucket_avg_cells",
            "expected_path": ["bucket", str(quality), "avg_cells"],
            "expected_quality": quality,
            "expected_value": expected,
            "parser_implication": "not_session_capacity_signal",
        }
    if action_id in _ACTION_VALUE_SUM:
        quality = _ACTION_VALUE_SUM[action_id]
        return {
            "expected_semantic": "bucket_value_sum",
            "expected_path": ["bucket", str(quality), "value_sum"],
            "expected_quality": quality,
            "expected_value": None,
            "parser_implication": "not_session_capacity_signal",
        }
    return {
        "expected_semantic": "unknown_action_result",
        "expected_path": [],
        "expected_quality": None,
        "expected_value": None,
        "parser_implication": "unknown_numeric_action_semantics",
    }


def classify_numeric_action_result(
    *,
    action_id: int,
    result: int | float,
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    expected = _action_expected_semantic(action_id, inventory)
    expected_value = expected.get("expected_value")
    expected_match = (
        expected_value is not None and _same_number(result, expected_value)
    )
    matches: list[dict[str, Any]] = []
    for label, value in (
        ("session_total_item_count", inventory.get("total_item_count")),
        ("session_warehouse_total_cells", inventory.get("warehouse_total_cells")),
    ):
        if value is not None and _same_number(result, value):
            matches.append({"candidate": label, "value": value})
    buckets = _as_mapping(inventory.get("buckets"))
    for quality, bucket_value in buckets.items():
        bucket = _as_mapping(bucket_value)
        for field in ("count", "total_cells"):
            value = bucket.get(field)
            if value is not None and _same_number(result, value):
                matches.append(
                    {
                        "candidate": f"bucket_{quality}_{field}",
                        "value": value,
                    }
                )
    if expected.get("expected_semantic") == "unknown_action_result":
        status = "blocked_unknown_numeric_action_semantics"
    elif expected_match:
        status = "watch_expected_semantic_match"
    else:
        status = "blocked_expected_semantic_mismatch"
    return {
        **expected,
        "expected_match": expected_match,
        "matched_candidate_values": matches,
        "status": status,
    }


def _action_row(
    *,
    file: str,
    state: FatbeansStateEvent,
    result: FatbeansActionResult,
    inventory_state: FatbeansStateEvent | None,
    source_parser_status_by_map: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    inventory = (
        _inventory_summary(inventory_state.inventory_items)
        if inventory_state is not None
        else {}
    )
    classification = classify_numeric_action_result(
        action_id=result.action_id,
        result=result.result,  # type: ignore[arg-type]
        inventory=inventory,
    )
    parser_overlap = _as_mapping(source_parser_status_by_map.get(str(state.map_id)))
    return {
        "file": file,
        "map_id": state.map_id,
        "session_id": state.session_id,
        "sort_id": state.sort_id,
        "message_id": f"0x{state.message_id:04x}",
        "block_source": (
            "direct_action" if state.message_id == 0x0027 else "state_snapshot"
        ),
        "round_index": state.round_index,
        "action_id": result.action_id,
        "result": result.result,
        "result_field": result.result_field,
        "observed_item_count": len(result.observed_items),
        "numeric_only": len(result.observed_items) == 0,
        "inventory_state_sort_id": (
            inventory_state.sort_id if inventory_state is not None else None
        ),
        "inventory": inventory,
        "source_parser_overlap": {
            "status": parser_overlap.get("status"),
            "session_capacity_source_required": bool(
                _int_or_none(
                    _as_mapping(parser_overlap.get("source_semantics")).get(
                        "session_capacity_source_semantics_rows"
                    )
                )
            ),
        },
        **classification,
    }


def _index_source_parser_rows(
    source_parser_requirements: Mapping[str, Any] | None,
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(source_parser_requirements, Mapping):
        return {}
    out: dict[str, Mapping[str, Any]] = {}
    for row in _as_list(source_parser_requirements.get("rows")):
        if isinstance(row, Mapping) and row.get("map_id") is not None:
            out[str(row.get("map_id"))] = row
    return out


def summarize_numeric_action_result_semantics(
    paths: Iterable[Path],
    *,
    focus_maps: Iterable[str] = (),
    files: Iterable[str] = (),
    numeric_only: bool = True,
    source_parser_requirements: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    focus_map_set = {str(item) for item in focus_maps if str(item)}
    file_set = {str(item) for item in files if str(item)}
    parser_by_map = _index_source_parser_rows(source_parser_requirements)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in _iter_json_paths(paths):
        if file_set and path.name not in file_set:
            continue
        try:
            capture = parse_fatbeans_capture(path)
        except Exception as exc:  # pragma: no cover - defensive for ad hoc captures.
            errors.append(f"{path.name}: {exc}")
            continue
        states = tuple(capture.states)
        for state in states:
            if focus_map_set and str(state.map_id) not in focus_map_set:
                continue
            for result in state.action_results:
                if result.result is None or result.result_field is None:
                    continue
                if not isinstance(result.result, (int, float)):
                    continue
                if numeric_only and result.observed_items:
                    continue
                inventory_state = _nearest_inventory_state(states, state)
                rows.append(
                    _action_row(
                        file=path.name,
                        state=state,
                        result=result,
                        inventory_state=inventory_state,
                        source_parser_status_by_map=parser_by_map,
                    )
                )
    status_counts = Counter(str(row.get("status")) for row in rows)
    action_id_counts = Counter(str(row.get("action_id")) for row in rows)
    result_field_counts = Counter(str(row.get("result_field")) for row in rows)
    semantic_counts = Counter(str(row.get("expected_semantic")) for row in rows)
    implication_counts = Counter(str(row.get("parser_implication")) for row in rows)
    session_signal_rows = sum(
        1
        for row in rows
        if row.get("parser_implication") == "session_capacity_signal"
        and row.get("expected_match") is True
    )
    non_session_expected_rows = sum(
        1
        for row in rows
        if row.get("parser_implication") == "not_session_capacity_signal"
        and row.get("expected_match") is True
    )
    source_required_rows = [
        row
        for row in rows
        if _as_mapping(row.get("source_parser_overlap")).get(
            "session_capacity_source_required"
        )
    ]
    if errors:
        status = "blocked_parse_errors"
    elif any(row.get("status") == "blocked_unknown_numeric_action_semantics" for row in rows):
        status = "blocked_unknown_numeric_action_semantics"
    elif any(row.get("status") == "blocked_expected_semantic_mismatch" for row in rows):
        status = "blocked_expected_semantic_mismatch"
    elif source_required_rows and session_signal_rows == 0:
        status = "blocked_session_capacity_still_unexplained"
    else:
        status = "watch_numeric_action_semantics_audit_only"
    return {
        "status": status,
        "shadow_only": True,
        "affects_bid": False,
        "numeric_only": numeric_only,
        "focus_maps": sorted(focus_map_set),
        "files_filter": sorted(file_set),
        "errors": errors,
        "rows": rows,
        "summary": {
            "files": len({row.get("file") for row in rows}),
            "maps": len({str(row.get("map_id")) for row in rows if row.get("map_id") is not None}),
            "numeric_action_rows": len(rows),
            "source_required_rows": len(source_required_rows),
            "session_capacity_signal_rows": session_signal_rows,
            "non_session_expected_rows": non_session_expected_rows,
            "unknown_semantic_rows": status_counts.get(
                "blocked_unknown_numeric_action_semantics",
                0,
            ),
            "expected_mismatch_rows": status_counts.get(
                "blocked_expected_semantic_mismatch",
                0,
            ),
            "status_counts": _counter_dict(status_counts),
            "action_id_counts": _counter_dict(action_id_counts),
            "result_field_counts": _counter_dict(result_field_counts),
            "expected_semantic_counts": _counter_dict(semantic_counts),
            "parser_implication_counts": _counter_dict(implication_counts),
        },
    }


def _format_counts(counts: Mapping[str, Any]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def print_summary(result: Mapping[str, Any]) -> None:
    summary = _as_mapping(result.get("summary"))
    print(
        "status={status} rows={rows} maps={maps} files={files} "
        "source_required_rows={source_rows} session_signals={session_signals} "
        "non_session_expected={non_session} statuses={statuses} "
        "actions={actions} semantics={semantics}".format(
            status=result.get("status"),
            rows=summary.get("numeric_action_rows"),
            maps=summary.get("maps"),
            files=summary.get("files"),
            source_rows=summary.get("source_required_rows"),
            session_signals=summary.get("session_capacity_signal_rows"),
            non_session=summary.get("non_session_expected_rows"),
            statuses=_format_counts(_as_mapping(summary.get("status_counts"))),
            actions=_format_counts(_as_mapping(summary.get("action_id_counts"))),
            semantics=_format_counts(
                _as_mapping(summary.get("expected_semantic_counts"))
            ),
        )
    )
    for row in _as_list(result.get("rows"))[:8]:
        if not isinstance(row, Mapping):
            continue
        inventory = _as_mapping(row.get("inventory"))
        print(
            "file={file} map={map_id} sort={sort_id} msg={message_id} "
            "source={source} action={action_id} field={field} result={result} "
            "semantic={semantic} expected={expected} match={match} "
            "implication={implication} inventory_items={items} "
            "inventory_cells={cells}".format(
                file=row.get("file"),
                map_id=row.get("map_id"),
                sort_id=row.get("sort_id"),
                message_id=row.get("message_id"),
                source=row.get("block_source"),
                action_id=row.get("action_id"),
                field=row.get("result_field"),
                result=row.get("result"),
                semantic=row.get("expected_semantic"),
                expected=row.get("expected_value"),
                match=row.get("expected_match"),
                implication=row.get("parser_implication"),
                items=inventory.get("total_item_count"),
                cells=inventory.get("warehouse_total_cells"),
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize numeric action-result semantics for v3 source blockers.",
    )
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--focus-map", action="append", default=[])
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--include-item-payload", action="store_true")
    parser.add_argument("--source-parser-requirements-json", type=Path)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    source_parser_requirements = None
    if args.source_parser_requirements_json is not None:
        source_parser_requirements = _load_json(args.source_parser_requirements_json)
    result = summarize_numeric_action_result_semantics(
        args.paths or [DEFAULT_SAMPLE_ROOT],
        focus_maps=args.focus_map,
        files=args.file,
        numeric_only=not args.include_item_payload,
        source_parser_requirements=source_parser_requirements,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_summary(result)
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
