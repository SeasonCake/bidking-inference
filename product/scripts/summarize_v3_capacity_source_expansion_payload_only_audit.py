"""Audit payload-only v3 capacity/source expansion truth rows.

This script is diagnostic-only. It explains settlement source-semantics truth
rows that lack full public-total/direct-action confirmation, and joins them
with map-id holdout coverage plus pre-bid CSE pressure windows.
"""

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

from evaluate_fatbeans_v3_samples import (  # noqa: E402
    _default_calibration_path,
    _default_capacity_source_expansion_path,
    _default_paths,
    _default_settlement_count_prior_path,
    _default_tail_value_review_path,
    _default_underestimate_repair_path,
    evaluate_paths,
    load_capacity_source_expansion_entries,
    load_monitor_tables,
    load_prior_calibration_entries,
    load_settlement_count_prior_entries,
    load_tail_value_review_entries,
    load_underestimate_repair_entries,
)
from summarize_v3_capacity_source_expansion_holdout import (  # noqa: E402
    _eval_rows,
    _float_or_none,
    _format_counts,
    _format_summary,
    _numeric_summary,
    _rows_for_paths,
    _source_semantics_truth,
)
from bidking_lab.live.fatbeans import (  # noqa: E402
    load_fatbeans_packets,
    reconstruct_fatbeans_frames,
    _all,
    _first,
    _parse_action_result,
    _parse_fields,
)

PAYLOAD_CONTEXT_PREFIXES = ("payload_verified_", "payload_unverified_")


def _base_file(row: Mapping[str, Any]) -> str:
    return str(row.get("file") or "").split("#", 1)[0]


def _is_payload_context(row: Mapping[str, Any]) -> bool:
    context = str(row.get("source_context_class") or "")
    return context.startswith(PAYLOAD_CONTEXT_PREFIXES)


def _is_payload_truth(row: Mapping[str, Any]) -> bool:
    return _source_semantics_truth(row) and _is_payload_context(row)


def _counter_dict(values: Iterable[Any], *, top: int) -> dict[str, int]:
    counter = Counter(str(value) for value in values if value not in (None, ""))
    return dict(counter.most_common(top))


def _expand_counter_values(rows: Iterable[Mapping[str, Any]], key: str) -> Iterable[str]:
    for row in rows:
        value = row.get(key)
        if not isinstance(value, Mapping):
            continue
        for item, count in value.items():
            try:
                repeat = int(count)
            except (TypeError, ValueError):
                repeat = 0
            for _ in range(max(repeat, 0)):
                yield str(item)


def _action_payload_shape_class(
    *,
    action_result_blocks: int,
    parsed_action_results: int,
    result_value_blocks: int,
    item_payload_blocks: int,
    observed_item_count: int,
    unknown_byte_fields: Mapping[str, int],
) -> str:
    if action_result_blocks <= 0:
        return "no_action_results"
    if item_payload_blocks > 0 and observed_item_count > 0:
        return "item_reveal_payload"
    if item_payload_blocks > 0:
        return "unparsed_item_payload"
    if result_value_blocks > 0 and not unknown_byte_fields:
        return "numeric_only_result"
    if result_value_blocks > 0:
        return "numeric_plus_unknown_bytes"
    if parsed_action_results > 0:
        return "parsed_without_result_or_items"
    return "unparsed_action_result"


def _iter_action_result_blocks(path: Path) -> Iterable[tuple[int, int, bytes, str]]:
    packets = load_fatbeans_packets(path)
    frames = [
        *reconstruct_fatbeans_frames(packets, "SEND"),
        *reconstruct_fatbeans_frames(packets, "REV"),
    ]
    frames.sort(key=lambda frame: (frame.sort_id, frame.index))
    for frame in frames:
        if frame.direction != "REV" or frame.packet_tag != 0:
            continue
        fields = _parse_fields(frame.body)
        if frame.message_id == 0x0027:
            payload = _first(fields, 2)
            if isinstance(payload, bytes):
                yield frame.sort_id, frame.message_id, payload, "direct_action"
            continue
        if frame.message_id in (0x0021, 0x0025):
            payload = _first(fields, 1)
        elif frame.message_id == 0x002D:
            payload = _first(fields, 2)
        else:
            continue
        if not isinstance(payload, bytes):
            continue
        state_fields = _parse_fields(payload)
        for raw in _all(state_fields, 8):
            if isinstance(raw, bytes):
                yield frame.sort_id, frame.message_id, raw, "state_snapshot"


def _action_payload_shape_for_path(path: Path) -> dict[str, Any]:
    action_ids: Counter[str] = Counter()
    result_fields: Counter[str] = Counter()
    message_ids: Counter[str] = Counter()
    block_sources: Counter[str] = Counter()
    unknown_byte_fields: Counter[str] = Counter()
    action_result_blocks = 0
    parsed_action_results = 0
    result_value_blocks = 0
    item_payload_blocks = 0
    item_payload_block_max = 0
    observed_item_count = 0
    observed_item_count_max = 0

    for _sort_id, message_id, raw, block_source in _iter_action_result_blocks(path):
        action_result_blocks += 1
        message_ids[f"0x{message_id:04x}"] += 1
        block_sources[block_source] += 1
        fields = _parse_fields(raw)
        block_item_payloads = 0
        for field_no, _wire_type, value in fields:
            if field_no == 8 and isinstance(value, bytes):
                item_payload_blocks += 1
                block_item_payloads += 1
            elif isinstance(value, bytes):
                unknown_byte_fields[str(field_no)] += 1
        item_payload_block_max = max(item_payload_block_max, block_item_payloads)
        parsed = _parse_action_result(raw)
        if parsed is None:
            continue
        parsed_action_results += 1
        action_ids[str(parsed.action_id)] += 1
        if parsed.result_field is not None:
            result_fields[str(parsed.result_field)] += 1
            result_value_blocks += 1
        parsed_observed_items = len(tuple(parsed.observed_items or ()))
        observed_item_count += parsed_observed_items
        observed_item_count_max = max(observed_item_count_max, parsed_observed_items)

    return {
        "source_action_payload_shape_class": _action_payload_shape_class(
            action_result_blocks=action_result_blocks,
            parsed_action_results=parsed_action_results,
            result_value_blocks=result_value_blocks,
            item_payload_blocks=item_payload_blocks,
            observed_item_count=observed_item_count,
            unknown_byte_fields=unknown_byte_fields,
        ),
        "source_action_result_blocks": action_result_blocks,
        "source_action_parsed_results": parsed_action_results,
        "source_action_result_value_blocks": result_value_blocks,
        "source_action_item_payload_blocks": item_payload_blocks,
        "source_action_item_payload_block_max": item_payload_block_max,
        "source_action_observed_item_count": observed_item_count,
        "source_action_observed_item_count_max": observed_item_count_max,
        "source_action_ids": dict(action_ids.most_common()),
        "source_action_result_fields": dict(result_fields.most_common()),
        "source_action_message_ids": dict(message_ids.most_common()),
        "source_action_block_sources": dict(block_sources.most_common()),
        "source_action_unknown_byte_fields": dict(unknown_byte_fields.most_common()),
    }


def _iter_json_paths(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(path.rglob("*.json"))
        elif path.exists():
            yield path


def _action_payload_shapes_by_file(
    paths: Iterable[Path],
    *,
    files: Iterable[str],
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    wanted = {str(file) for file in files if file}
    out: dict[str, dict[str, Any]] = {}
    for path in _iter_json_paths(paths):
        name = path.name
        if wanted and name not in wanted:
            continue
        try:
            out[name] = _action_payload_shape_for_path(path)
        except Exception as exc:  # pragma: no cover - defensive for ad hoc captures.
            errors.append(f"{name}: {exc}")
    return out


def _pressure_by_file(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "candidate_windows": 0,
            "pressure_windows": 0,
            "pressure_rounds": Counter(),
            "pressure_target_sources": Counter(),
            "pressure_target_prior_max_delta": [],
            "pressure_target_to_source_p95_delta": [],
        }
    )
    for row in rows:
        if row.get("status") != "ready" or not row.get("v3_cse_ready"):
            continue
        file = _base_file(row)
        if not file:
            continue
        if row.get("v3_cse_candidate"):
            out[file]["candidate_windows"] += 1
        if row.get("v3_cse_pressure_candidate"):
            out[file]["pressure_windows"] += 1
            out[file]["pressure_rounds"][str(row.get("round") or "none")] += 1
            out[file]["pressure_target_sources"][
                str(row.get("v3_cse_target_count_source") or "none")
            ] += 1
            target_prior = _float_or_none(row.get("v3_cse_target_prior_max_delta"))
            if target_prior is not None:
                out[file]["pressure_target_prior_max_delta"].append(target_prior)
            target_p95 = _float_or_none(
                row.get("v3_cse_target_to_unique_non_temp_p95_delta")
            )
            if target_p95 is not None:
                out[file]["pressure_target_to_source_p95_delta"].append(target_p95)
    return dict(out)


def _holdout_by_file(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not row.get("truth_unique_round_overflow"):
            continue
        file = str(row.get("example_file") or "")
        if file:
            out[file] = row
    return out


def _row_details(
    row: Mapping[str, Any],
    *,
    holdout: Mapping[str, Any] | None,
    pressure: Mapping[str, Any] | None,
    action_shape: Mapping[str, Any] | None,
) -> dict[str, Any]:
    pressure = pressure or {}
    holdout = holdout or {}
    action_shape = action_shape or {}
    pressure_rounds = pressure.get("pressure_rounds") or Counter()
    pressure_target_sources = pressure.get("pressure_target_sources") or Counter()
    return {
        "file": row.get("file"),
        "map_id": row.get("map_id"),
        "map_family": row.get("map_family"),
        "source_context_class": row.get("source_context_class"),
        "source_evidence_class": row.get("source_evidence_class"),
        "mechanism_class": row.get("mechanism_class"),
        "unique_round_excess_after_temp": _float_or_none(
            row.get("unique_round_cap_excess_after_temp_zodiac_count")
        ),
        "event_action_result_count_all": row.get("event_action_result_count_all"),
        "event_action_observed_item_count_max": row.get(
            "event_action_observed_item_count_max"
        ),
        "event_action_observed_item_inventory_gap_min": row.get(
            "event_action_observed_item_inventory_gap_min"
        ),
        "event_action_observed_item_ratio_max": row.get(
            "event_action_observed_item_ratio_max"
        ),
        "map_id_holdout_covered": bool(
            holdout.get("covered_unique_round_overflow")
        ),
        "map_id_holdout_candidate_source": holdout.get("candidate_source"),
        "map_id_holdout_train_source_semantics_rows": holdout.get(
            "train_source_semantics_rows"
        ),
        "map_id_holdout_fold": holdout.get("fold"),
        "prebid_cse_candidate_windows": int(pressure.get("candidate_windows") or 0),
        "prebid_pressure_windows": int(pressure.get("pressure_windows") or 0),
        "prebid_pressure_rounds": dict(sorted(pressure_rounds.items())),
        "prebid_pressure_target_sources": dict(
            sorted(pressure_target_sources.items())
        ),
        "prebid_pressure_target_prior_max_delta": _numeric_summary(
            pressure.get("pressure_target_prior_max_delta") or ()
        ),
        "prebid_pressure_target_to_source_p95_delta": _numeric_summary(
            pressure.get("pressure_target_to_source_p95_delta") or ()
        ),
        "source_action_payload_shape_class": action_shape.get(
            "source_action_payload_shape_class"
        ),
        "source_action_result_blocks": action_shape.get("source_action_result_blocks"),
        "source_action_parsed_results": action_shape.get("source_action_parsed_results"),
        "source_action_result_value_blocks": action_shape.get(
            "source_action_result_value_blocks"
        ),
        "source_action_item_payload_blocks": action_shape.get(
            "source_action_item_payload_blocks"
        ),
        "source_action_item_payload_block_max": action_shape.get(
            "source_action_item_payload_block_max"
        ),
        "source_action_observed_item_count": action_shape.get(
            "source_action_observed_item_count"
        ),
        "source_action_observed_item_count_max": action_shape.get(
            "source_action_observed_item_count_max"
        ),
        "source_action_ids": action_shape.get("source_action_ids") or {},
        "source_action_result_fields": action_shape.get(
            "source_action_result_fields"
        )
        or {},
        "source_action_message_ids": action_shape.get("source_action_message_ids") or {},
        "source_action_block_sources": action_shape.get("source_action_block_sources")
        or {},
        "source_action_unknown_byte_fields": action_shape.get(
            "source_action_unknown_byte_fields"
        )
        or {},
    }


def _summarize_group(
    rows: Iterable[Mapping[str, Any]],
    *,
    top: int,
) -> dict[str, Any]:
    seq = tuple(rows)
    return {
        "rows": len(seq),
        "map_ids": _counter_dict((row.get("map_id") for row in seq), top=top),
        "contexts": _counter_dict(
            (row.get("source_context_class") for row in seq),
            top=top,
        ),
        "map_id_missed_rows": sum(
            1 for row in seq if not row.get("map_id_holdout_covered")
        ),
        "prebid_candidate_rows": sum(
            1 for row in seq if int(row.get("prebid_cse_candidate_windows") or 0) > 0
        ),
        "prebid_pressure_rows": sum(
            1 for row in seq if int(row.get("prebid_pressure_windows") or 0) > 0
        ),
        "source_shape_classes": _counter_dict(
            (row.get("source_action_payload_shape_class") for row in seq),
            top=top,
        ),
        "source_action_ids": _counter_dict(
            _expand_counter_values(seq, "source_action_ids"),
            top=top,
        ),
        "source_action_result_fields": _counter_dict(
            _expand_counter_values(seq, "source_action_result_fields"),
            top=top,
        ),
        "source_action_item_payload_blocks": _numeric_summary(
            row.get("source_action_item_payload_blocks") for row in seq
        ),
        "source_action_item_payload_block_max": _numeric_summary(
            row.get("source_action_item_payload_block_max") for row in seq
        ),
        "source_action_observed_item_count": _numeric_summary(
            row.get("source_action_observed_item_count") for row in seq
        ),
        "source_action_observed_item_count_max": _numeric_summary(
            row.get("source_action_observed_item_count_max") for row in seq
        ),
        "action_max": _numeric_summary(
            row.get("event_action_observed_item_count_max") for row in seq
        ),
        "action_gap": _numeric_summary(
            row.get("event_action_observed_item_inventory_gap_min") for row in seq
        ),
        "action_ratio": _numeric_summary(
            row.get("event_action_observed_item_ratio_max") for row in seq
        ),
        "unique_round_excess": _numeric_summary(
            row.get("unique_round_excess_after_temp") for row in seq
        ),
        "examples": [
            {
                "file": row.get("file"),
                "map_id": row.get("map_id"),
                "context": row.get("source_context_class"),
                "covered": row.get("map_id_holdout_covered"),
                "train_source": row.get("map_id_holdout_train_source_semantics_rows"),
                "pressure_windows": row.get("prebid_pressure_windows"),
                "action_max": row.get("event_action_observed_item_count_max"),
                "action_gap": row.get("event_action_observed_item_inventory_gap_min"),
                "excess": row.get("unique_round_excess_after_temp"),
                "source_shape": row.get("source_action_payload_shape_class"),
                "source_action_ids": row.get("source_action_ids"),
                "source_item_payload_blocks": row.get(
                    "source_action_item_payload_blocks"
                ),
                "source_item_payload_block_max": row.get(
                    "source_action_item_payload_block_max"
                ),
                "source_observed_items": row.get("source_action_observed_item_count"),
                "source_observed_item_max": row.get(
                    "source_action_observed_item_count_max"
                ),
            }
            for row in sorted(
                seq,
                key=lambda item: (
                    not bool(item.get("prebid_pressure_windows")),
                    bool(item.get("map_id_holdout_covered")),
                    -float(item.get("unique_round_excess_after_temp") or 0.0),
                    str(item.get("file") or ""),
                ),
            )[:top]
        ],
    }


def summarize_payload_only(
    paths: Iterable[Path] = (),
    *,
    rows: Iterable[Mapping[str, Any]] | None = None,
    eval_rows: Iterable[Mapping[str, Any]] | None = None,
    prebid_rows: Iterable[Mapping[str, Any]] | None = None,
    source_shapes_by_file: Mapping[str, Mapping[str, Any]] | None = None,
    folds: int = 5,
    min_train_sessions: int = 4,
    posterior_trials: int = 64,
    top: int = 8,
) -> dict[str, Any]:
    selected_paths = tuple(paths) or _default_paths()
    if rows is None:
        rows = _rows_for_paths(selected_paths)
    settlement_rows = tuple(row for row in rows if row.get("status") == "ok")
    if eval_rows is None:
        eval_rows = _eval_rows(
            settlement_rows,
            group_by="map_id",
            fallback_group_by=None,
            folds=folds,
            min_train_sessions=min_train_sessions,
        )
    if prebid_rows is None:
        tables = load_monitor_tables()
        prebid_rows, errors = evaluate_paths(
            selected_paths,
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
            settlement_count_prior_entries=load_settlement_count_prior_entries(
                _default_settlement_count_prior_path()
            ),
            capacity_source_expansion_entries=load_capacity_source_expansion_entries(
                _default_capacity_source_expansion_path()
            ),
            posterior_trials=posterior_trials,
        )
    else:
        errors = []
    holdout_by_file = _holdout_by_file(eval_rows)
    pressure_by_file = _pressure_by_file(prebid_rows)
    truth_rows = [row for row in settlement_rows if _source_semantics_truth(row)]
    payload_rows = [row for row in truth_rows if _is_payload_context(row)]
    external_rows = [row for row in truth_rows if not _is_payload_context(row)]
    source_shape_errors: list[str] = []
    if source_shapes_by_file is None:
        source_shapes_by_file = _action_payload_shapes_by_file(
            selected_paths,
            files=(_base_file(row) for row in payload_rows),
            errors=source_shape_errors,
        )
    detailed = [
        _row_details(
            row,
            holdout=holdout_by_file.get(str(row.get("file") or "")),
            pressure=pressure_by_file.get(str(row.get("file") or "")),
            action_shape=source_shapes_by_file.get(_base_file(row)),
        )
        for row in payload_rows
    ]
    by_context: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in detailed:
        by_context[str(row.get("source_context_class") or "none")].append(row)
    return {
        "settlement_rows": len(settlement_rows),
        "truth_rows": len(truth_rows),
        "payload_truth_rows": len(payload_rows),
        "external_truth_rows": len(external_rows),
        "payload_contexts": _counter_dict(
            (row.get("source_context_class") for row in payload_rows),
            top=top,
        ),
        "payload_map_id_missed_rows": sum(
            1 for row in detailed if not row.get("map_id_holdout_covered")
        ),
        "payload_prebid_candidate_rows": sum(
            1 for row in detailed if int(row.get("prebid_cse_candidate_windows") or 0) > 0
        ),
        "payload_prebid_pressure_rows": sum(
            1 for row in detailed if int(row.get("prebid_pressure_windows") or 0) > 0
        ),
        "payload_source_shape_classes": _counter_dict(
            (row.get("source_action_payload_shape_class") for row in detailed),
            top=top,
        ),
        "parse_errors": len(errors),
        "source_shape_parse_errors": len(source_shape_errors),
        "source_shape_error_examples": source_shape_errors[:top],
        "groups": {
            context: _summarize_group(group_rows, top=top)
            for context, group_rows in sorted(by_context.items())
        },
        "rows": sorted(
            detailed,
            key=lambda item: (
                str(item.get("source_context_class") or ""),
                not bool(item.get("prebid_pressure_windows")),
                bool(item.get("map_id_holdout_covered")),
                -float(item.get("unique_round_excess_after_temp") or 0.0),
                str(item.get("file") or ""),
            ),
        ),
    }


def _print_summary(result: Mapping[str, Any], *, top: int) -> None:
    print(
        " ".join(
            (
                f"settlement_rows={result['settlement_rows']}",
                f"truth_rows={result['truth_rows']}",
                f"payload_truth_rows={result['payload_truth_rows']}",
                f"external_truth_rows={result['external_truth_rows']}",
                f"payload_contexts={_format_counts(result['payload_contexts'])}",
                f"payload_map_id_missed_rows={result['payload_map_id_missed_rows']}",
                f"payload_prebid_candidate_rows={result['payload_prebid_candidate_rows']}",
                f"payload_prebid_pressure_rows={result['payload_prebid_pressure_rows']}",
                f"payload_source_shapes={_format_counts(result['payload_source_shape_classes'])}",
                f"parse_errors={result['parse_errors']}",
                f"source_shape_parse_errors={result['source_shape_parse_errors']}",
            )
        )
    )
    for context, row in result["groups"].items():
        print(
            " ".join(
                (
                    f"context={context}",
                    f"rows={row['rows']}",
                    f"maps={_format_counts(row['map_ids'])}",
                    f"missed={row['map_id_missed_rows']}",
                    f"prebid_candidate={row['prebid_candidate_rows']}",
                    f"prebid_pressure={row['prebid_pressure_rows']}",
                    f"source_shapes={_format_counts(row['source_shape_classes'])}",
                    f"source_action_ids={_format_counts(row['source_action_ids'])}",
                    f"source_result_fields={_format_counts(row['source_action_result_fields'])}",
                    f"source_item_payload_blocks={_format_summary(row['source_action_item_payload_blocks'])}",
                    f"source_item_payload_block_max={_format_summary(row['source_action_item_payload_block_max'])}",
                    f"source_observed_items={_format_summary(row['source_action_observed_item_count'])}",
                    f"source_observed_item_max={_format_summary(row['source_action_observed_item_count_max'])}",
                    f"action_max={_format_summary(row['action_max'])}",
                    f"action_gap={_format_summary(row['action_gap'])}",
                    f"action_ratio={_format_summary(row['action_ratio'])}",
                    f"excess={_format_summary(row['unique_round_excess'])}",
                )
            )
        )
        for example in row["examples"][:top]:
            print(
                " ".join(
                    (
                        "  example",
                        f"file={example['file']}",
                        f"map_id={example['map_id']}",
                        f"covered={example['covered']}",
                        f"train_source={example['train_source']}",
                        f"pressure={example['pressure_windows']}",
                        f"action_max={example['action_max']}",
                        f"action_gap={example['action_gap']}",
                        f"excess={example['excess']}",
                        f"source_shape={example['source_shape']}",
                        f"source_action_ids={_format_counts(example['source_action_ids'])}",
                        f"source_item_payload_blocks={example['source_item_payload_blocks']}",
                        f"source_item_payload_block_max={example['source_item_payload_block_max']}",
                        f"source_observed_items={example['source_observed_items']}",
                        f"source_observed_item_max={example['source_observed_item_max']}",
                    )
                )
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit payload-only v3 CSE truth rows.",
    )
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-train-sessions", type=int, default=4)
    parser.add_argument("--posterior-trials", type=int, default=64)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)
    result = summarize_payload_only(
        args.paths,
        folds=args.folds,
        min_train_sessions=args.min_train_sessions,
        posterior_trials=args.posterior_trials,
        top=args.top,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(result, top=args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
