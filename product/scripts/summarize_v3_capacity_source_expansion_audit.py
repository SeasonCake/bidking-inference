"""Drill into v3 capacity semantic matrix cells at file level."""

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

import summarize_v3_capacity_table_audit as capacity_audit  # noqa: E402


DEFAULT_CASE = "all"
DEFAULT_BUCKET = "all"


def _float_or_none(value: Any) -> float | None:
    return capacity_audit._float_or_none(value)


def _format_counts(counts: Mapping[str, int]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def _format_summary(summary: Mapping[str, Any]) -> str:
    return (
        f"n={summary['n']}"
        f"/avg={summary['avg']}"
        f"/p90={summary['p90']}"
        f"/max={summary['max']}"
    )


def _path_label(row: Mapping[str, Any]) -> str:
    ref = capacity_audit._strip_row_file_ref(row.get("file"))
    return Path(ref).name if ref else "none"


def _resolve_diag(
    row: Mapping[str, Any],
    *,
    tables: Any,
    sample_root: Path,
    diagnostics_by_path: dict[Path, dict[str, Any]],
    resolved_by_ref: dict[str, Path | None],
    drop_universe_cache: dict[int, frozenset[int]],
    errors: list[str],
) -> dict[str, Any] | None:
    ref = capacity_audit._strip_row_file_ref(row.get("file"))
    if not ref:
        errors.append("missing_file_ref")
        return None
    if ref not in resolved_by_ref:
        resolved_by_ref[ref] = capacity_audit._resolve_capture_path(ref, sample_root)
    path = resolved_by_ref[ref]
    if path is None:
        errors.append(f"missing_capture_file:{ref}")
        return None
    if path not in diagnostics_by_path:
        try:
            diagnostics_by_path[path] = capacity_audit._inventory_diagnostic_for_path(
                path,
                tables=tables,
                drop_universe_cache=drop_universe_cache,
            )
        except Exception as exc:  # pragma: no cover - CLI diagnostics.
            errors.append(f"parse_error:{path}:{exc}")
            diagnostics_by_path[path] = {
                "path": str(path),
                "file": path.name,
                "status": "parse_error",
            }
    return diagnostics_by_path[path]


def _cell_key(row: Mapping[str, Any], diag: Mapping[str, Any] | None) -> tuple[str, ...]:
    capacity = row.get("item_count_capacity", {})
    full_action_signal = (
        "has_full_action"
        if bool(tuple((diag or {}).get("full_observed_action_ids", ()) or ()))
        else "no_full_action"
    )
    public_total_signal = (
        "has_public_total"
        if bool(tuple((diag or {}).get("public_total_count_values", ()) or ()))
        else "no_public_total"
    )
    residual_mode = (
        capacity_audit._residual_mode(diag) if diag is not None else "not_checked"
    )
    return (
        str(row.get("consistency_bucket") or "none"),
        residual_mode,
        capacity_audit._map_family(row.get("map_id")),
        str(capacity.get("total_count_source") or "none"),
        full_action_signal,
        public_total_signal,
        capacity_audit._capture_day(row.get("file")) or "none",
    )


def _unique_diags(
    entries: Iterable[tuple[Mapping[str, Any], Mapping[str, Any] | None]],
) -> tuple[Mapping[str, Any], ...]:
    out: dict[str, Mapping[str, Any]] = {}
    for _row, diag in entries:
        if diag is None:
            continue
        key = str(diag.get("path") or diag.get("file") or id(diag))
        out[key] = diag
    return tuple(out.values())


def _non_temp_count(diag: Mapping[str, Any]) -> float | None:
    latest = _float_or_none(diag.get("latest_item_count"))
    temp = _float_or_none(diag.get("known_temp_zodiac_count")) or 0.0
    return latest - temp if latest is not None else None


def _public_total_latest_deltas(diag: Mapping[str, Any]) -> tuple[float, ...]:
    latest = _float_or_none(diag.get("latest_item_count"))
    if latest is None:
        return ()
    out: list[float] = []
    for value in tuple(diag.get("public_total_count_values", ()) or ()):
        parsed = _float_or_none(value)
        if parsed is not None:
            out.append(parsed - latest)
    return tuple(out)


def _action_latest_delta(diag: Mapping[str, Any]) -> float | None:
    latest = _float_or_none(diag.get("latest_item_count"))
    observed = _float_or_none(diag.get("action_observed_item_count_max"))
    if latest is None or observed is None:
        return None
    return observed - latest


def _cell_status(
    *,
    capacity_rows: Iterable[Mapping[str, Any]],
    diagnostics: Iterable[Mapping[str, Any]],
) -> str:
    diag_seq = tuple(diagnostics)
    if not diag_seq or any(diag.get("status") != "ok" for diag in diag_seq):
        return "needs_raw_inventory_verification"
    return capacity_audit._matrix_cell_status(
        capacity_rows=capacity_rows,
        diagnostics=diag_seq,
    )


def _examples(
    entries: Iterable[tuple[Mapping[str, Any], Mapping[str, Any] | None]],
    *,
    top: int,
) -> list[dict[str, Any]]:
    rows_by_file: Counter[str] = Counter(_path_label(row) for row, _diag in entries)
    diag_by_file: dict[str, Mapping[str, Any]] = {}
    row_by_file: dict[str, Mapping[str, Any]] = {}
    for row, diag in entries:
        label = _path_label(row)
        row_by_file.setdefault(label, row)
        if diag is not None:
            diag_by_file[label] = diag
    out: list[dict[str, Any]] = []
    for label, row_count in rows_by_file.most_common(top):
        row = row_by_file[label]
        diag = diag_by_file.get(label, {})
        public_deltas = _public_total_latest_deltas(diag)
        out.append(
            {
                "file": label,
                "rows": row_count,
                "map_id": diag.get("latest_map_id", row.get("map_id")),
                "latest_item_count": diag.get("latest_item_count"),
                "known_temp_zodiac_count": diag.get("known_temp_zodiac_count"),
                "non_temp_item_count": _non_temp_count(diag),
                "drop_ref_excess_after_temp_zodiac_count": diag.get(
                    "drop_ref_excess_after_temp_zodiac_count"
                ),
                "round_cap_excess_after_temp_zodiac_count": diag.get(
                    "round_cap_excess_after_temp_zodiac_count"
                ),
                "public_total_count_values": list(
                    tuple(diag.get("public_total_count_values", ()) or ())
                ),
                "public_total_latest_delta": list(public_deltas),
                "full_observed_action_ids": list(
                    tuple(diag.get("full_observed_action_ids", ()) or ())
                ),
                "action_observed_item_count_max": diag.get(
                    "action_observed_item_count_max"
                ),
                "action_latest_delta": _action_latest_delta(diag),
            }
        )
    return out


def summarize_capacity_source_expansion(
    details: Iterable[Mapping[str, Any]],
    *,
    tables: Any,
    selected_case: str = DEFAULT_CASE,
    selected_bucket: str = DEFAULT_BUCKET,
    sample_root: Path = capacity_audit.DEFAULT_SAMPLE_ROOT,
    top: int = 8,
) -> dict[str, Any]:
    diagnostics_by_path: dict[Path, dict[str, Any]] = {}
    resolved_by_ref: dict[str, Path | None] = {}
    drop_universe_cache: dict[int, frozenset[int]] = {}
    errors: list[str] = []
    grouped: dict[
        tuple[str, ...],
        list[tuple[Mapping[str, Any], Mapping[str, Any] | None]],
    ] = defaultdict(list)

    for row in details:
        if not capacity_audit._case_match(row, selected_case):
            continue
        if not capacity_audit._bucket_match(row, selected_bucket):
            continue
        diag = _resolve_diag(
            row,
            tables=tables,
            sample_root=sample_root,
            diagnostics_by_path=diagnostics_by_path,
            resolved_by_ref=resolved_by_ref,
            drop_universe_cache=drop_universe_cache,
            errors=errors,
        )
        grouped[_cell_key(row, diag)].append((row, diag))

    cells: list[dict[str, Any]] = []
    for key, entries in grouped.items():
        rows = [entry[0] for entry in entries]
        capacity_rows = [row.get("item_count_capacity", {}) for row in rows]
        diags = _unique_diags(entries)
        status = _cell_status(capacity_rows=capacity_rows, diagnostics=diags)
        cells.append(
            {
                **dict(zip(capacity_audit._MATRIX_FIELDS, key)),
                "rows": len(rows),
                "files": len(diags),
                "semantic_status": status,
                "map_ids": capacity_audit._counter_dict(
                    (row.get("map_id") for row in rows),
                    top=top,
                ),
                "capacity_cases": capacity_audit._counter_dict(
                    case for row in rows for case in capacity_audit._capacity_cases(row)
                ),
                "target_truth_delta_counts": capacity_audit._delta_counts(
                    row.get("target_truth_delta") for row in capacity_rows
                ),
                "truth_prior_max_delta": capacity_audit._numeric_summary(
                    row.get("truth_prior_max_delta") for row in capacity_rows
                ),
                "latest_item_count": capacity_audit._numeric_summary(
                    diag.get("latest_item_count") for diag in diags
                ),
                "non_temp_item_count": capacity_audit._numeric_summary(
                    _non_temp_count(diag) for diag in diags
                ),
                "known_temp_zodiac_count": capacity_audit._numeric_summary(
                    diag.get("known_temp_zodiac_count") for diag in diags
                ),
                "drop_ref_excess_after_temp_zodiac_count": capacity_audit._numeric_summary(
                    diag.get("drop_ref_excess_after_temp_zodiac_count")
                    for diag in diags
                ),
                "round_cap_excess_after_temp_zodiac_count": capacity_audit._numeric_summary(
                    diag.get("round_cap_excess_after_temp_zodiac_count")
                    for diag in diags
                ),
                "non_zodiac_missing_from_drop_universe_count": capacity_audit._numeric_summary(
                    diag.get("non_zodiac_missing_from_drop_universe_count")
                    for diag in diags
                ),
                "public_total_count_values": capacity_audit._counter_dict(
                    value
                    for diag in diags
                    for value in tuple(diag.get("public_total_count_values", ()) or ())
                ),
                "public_total_latest_delta": capacity_audit._numeric_summary(
                    value
                    for diag in diags
                    for value in _public_total_latest_deltas(diag)
                ),
                "full_observed_action_counts": capacity_audit._counter_dict(
                    action_id
                    for diag in diags
                    for action_id in tuple(diag.get("full_observed_action_ids", ()) or ())
                ),
                "action_observed_item_count_max": capacity_audit._numeric_summary(
                    diag.get("action_observed_item_count_max") for diag in diags
                ),
                "action_latest_delta": capacity_audit._numeric_summary(
                    _action_latest_delta(diag) for diag in diags
                ),
                "examples": _examples(entries, top=top),
            }
        )

    cells.sort(
        key=lambda row: (
            -int(row["rows"]),
            -int(row["files"]),
            str(row["consistency_bucket"]),
            str(row["residual_mode"]),
            str(row["map_family"]),
            str(row["total_count_source"]),
        )
    )
    return {
        "errors": errors,
        "case": selected_case,
        "bucket": selected_bucket,
        "cells": cells[:top],
    }


def _cell_key_text(row: Mapping[str, Any]) -> str:
    return "/".join(str(row.get(field) or "none") for field in capacity_audit._MATRIX_FIELDS)


def _print_summary(result: Mapping[str, Any], *, top: int) -> None:
    print(
        f"case={result['case']} bucket={result['bucket']} cells={len(result['cells'])}"
    )
    for cell in result["cells"][:top]:
        print(
            " ".join(
                (
                    f"cell={_cell_key_text(cell)}",
                    f"rows={cell['rows']}",
                    f"files={cell['files']}",
                    f"status={cell['semantic_status']}",
                    f"maps={_format_counts(cell['map_ids'])}",
                    f"cases={_format_counts(cell['capacity_cases'])}",
                    f"latest={_format_summary(cell['latest_item_count'])}",
                    f"non_temp={_format_summary(cell['non_temp_item_count'])}",
                    f"drop_after={_format_summary(cell['drop_ref_excess_after_temp_zodiac_count'])}",
                    f"round_after={_format_summary(cell['round_cap_excess_after_temp_zodiac_count'])}",
                    f"public={_format_counts(cell['public_total_count_values'])}",
                    f"public_delta={_format_summary(cell['public_total_latest_delta'])}",
                    f"full_actions={_format_counts(cell['full_observed_action_counts'])}",
                    f"action_max={_format_summary(cell['action_observed_item_count_max'])}",
                    f"action_delta={_format_summary(cell['action_latest_delta'])}",
                )
            )
        )
        for example in cell["examples"][: min(3, top)]:
            print(
                "  example="
                + " ".join(
                    (
                        f"file={example['file']}",
                        f"rows={example['rows']}",
                        f"map_id={example['map_id']}",
                        f"latest={example['latest_item_count']}",
                        f"non_temp={example['non_temp_item_count']}",
                        f"drop_after={example['drop_ref_excess_after_temp_zodiac_count']}",
                        f"round_after={example['round_cap_excess_after_temp_zodiac_count']}",
                        f"public={example['public_total_count_values']}",
                        f"public_delta={example['public_total_latest_delta']}",
                        f"full_actions={example['full_observed_action_ids']}",
                        f"action_max={example['action_observed_item_count_max']}",
                        f"action_delta={example['action_latest_delta']}",
                    )
                )
            )


def _evaluate_default_details(paths: Iterable[Path], *, posterior_trials: int, posterior_seed: int) -> tuple[list[dict[str, Any]], Any, list[str]]:
    tables = capacity_audit.load_monitor_tables()
    rows, errors = capacity_audit.evaluate_paths(
        paths or capacity_audit._default_paths(),
        tables=tables,
        calibration_entries=capacity_audit.load_prior_calibration_entries(
            capacity_audit._default_calibration_path()
        ),
        underestimate_repair_entries=capacity_audit.load_underestimate_repair_entries(
            capacity_audit._default_underestimate_repair_path()
        ),
        tail_value_review_entries=capacity_audit.load_tail_value_review_entries(
            capacity_audit._default_tail_value_review_path()
        ),
        posterior_trials=posterior_trials,
        posterior_seed=posterior_seed,
    )
    return capacity_audit.summarize_prior_stress_details(rows), tables, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drill into v3 capacity semantic matrix cells at file level.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--case", default=DEFAULT_CASE)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--posterior-trials", type=int, default=64)
    parser.add_argument("--posterior-seed", type=int, default=0)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    details, tables, eval_errors = _evaluate_default_details(
        args.paths,
        posterior_trials=args.posterior_trials,
        posterior_seed=args.posterior_seed,
    )
    result = summarize_capacity_source_expansion(
        details,
        tables=tables,
        selected_case=args.case,
        selected_bucket=args.bucket,
        top=args.top,
    )
    result["errors"] = [*eval_errors, *result["errors"]]
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result["errors"]:
            print(f"errors={len(result['errors'])}")
        _print_summary(result, top=args.top)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
