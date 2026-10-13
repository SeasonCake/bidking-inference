from __future__ import annotations

import argparse
import json
import sys
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

import summarize_v3_settlement_count_prior_candidates as scp  # noqa: E402


_CAPACITY_COLUMNS = (11, 14, 17)
_COLUMN_ROLES = {
    0: "map_id",
    7: "category_id",
    8: "v300_flag_a",
    9: "sub_pool_weights",
    11: "rounds_total",
    12: "entry_fee_silver",
    13: "entry_requirement",
    14: "round_caps_candidate",
    15: "starting_budget_silver",
    16: "unused_placeholder",
    17: "drop_ref",
    18: "mode_flag",
    19: "bid_price_ladder",
    20: "round_category_hints",
    22: "v300_flag_b",
}
_TARGET_FIELDS = (
    "non_temp_inventory_count",
    "unique_non_temp_item_id_count",
    "non_temp_inventory_cells",
    "unique_non_temp_inventory_cells",
    "unique_q6_non_temp_cells",
)


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _numeric_summary(values: Iterable[Any]) -> dict[str, Any]:
    return scp._numeric_summary(values)


def _counter_dict(values: Iterable[Any], *, top: int = 8) -> dict[str, int]:
    return scp._counter_dict(values, top=top)


def _parse_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text == "":
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        parsed = _safe_int(text)
        return parsed if parsed is not None else text


def _numeric_atoms(value: Any) -> tuple[int, ...]:
    parsed = _parse_jsonish(value)
    out: list[int] = []

    def walk(item: Any) -> None:
        if isinstance(item, bool) or item is None:
            return
        parsed_item = _safe_int(item)
        if parsed_item is not None:
            out.append(parsed_item)
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                walk(child)
            return
        if isinstance(item, Mapping):
            for child in item.values():
                walk(child)

    walk(parsed)
    return tuple(out)


def _capacity_candidate_atoms(value: Any, *, max_candidate_value: int) -> tuple[int, ...]:
    return tuple(
        value
        for value in _numeric_atoms(value)
        if value not in (0, 1, 9999) and 0 < value <= max_candidate_value
    )


def _row_candidate_max(
    row: Mapping[str, Any],
    *,
    tables: Any,
    column: int,
    max_candidate_value: int,
) -> int | None:
    map_id = _safe_int(row.get("map_id"))
    bid_map = (getattr(tables, "maps", {}) or {}).get(int(map_id)) if map_id is not None else None
    raw_row = tuple(getattr(bid_map, "raw_row", ()) or ())
    if len(raw_row) <= column:
        return None
    atoms = _capacity_candidate_atoms(
        raw_row[column],
        max_candidate_value=max_candidate_value,
    )
    return max(atoms) if atoms else None


def _field_coverage(
    rows: tuple[Mapping[str, Any], ...],
    *,
    tables: Any,
    column: int,
    field: str,
    max_candidate_value: int,
    top: int,
) -> dict[str, Any]:
    compared_rows = 0
    covered_rows = 0
    over_rows = 0
    deltas: list[int] = []
    over_examples: list[tuple[int, Mapping[str, Any], int, int]] = []
    over_unique_modes: list[str] = []
    for row in rows:
        candidate = _row_candidate_max(
            row,
            tables=tables,
            column=column,
            max_candidate_value=max_candidate_value,
        )
        target = _safe_int(row.get(field))
        if candidate is None or target is None:
            continue
        compared_rows += 1
        delta = int(candidate) - int(target)
        deltas.append(delta)
        if delta >= 0:
            covered_rows += 1
        else:
            over_rows += 1
            over_unique_modes.append(str(row.get("unique_residual_mode") or "none"))
            over_examples.append((-delta, row, int(candidate), int(target)))
    over_examples = sorted(
        over_examples,
        key=lambda item: (-item[0], str(item[1].get("file") or "")),
    )[:top]
    return {
        "compared_rows": compared_rows,
        "covered_rows": covered_rows,
        "over_rows": over_rows,
        "coverage_rate": (
            round(covered_rows / compared_rows, 6) if compared_rows else None
        ),
        "delta": _numeric_summary(deltas),
        "over_unique_residual_modes": _counter_dict(over_unique_modes, top=top),
        "over_examples": [
            {
                "file": str(row.get("file")),
                "map_id": row.get("map_id"),
                "candidate": candidate,
                "target": target,
                "excess": excess,
                "unique_residual_mode": row.get("unique_residual_mode"),
            }
            for excess, row, candidate, target in over_examples
        ],
    }


def _column_summary(
    rows: tuple[Mapping[str, Any], ...],
    *,
    tables: Any,
    column: int,
    max_candidate_value: int,
    top: int,
) -> dict[str, Any]:
    candidate_values: list[int] = []
    raw_values: list[str] = []
    for row in rows:
        map_id = _safe_int(row.get("map_id"))
        bid_map = (
            (getattr(tables, "maps", {}) or {}).get(int(map_id))
            if map_id is not None
            else None
        )
        raw_row = tuple(getattr(bid_map, "raw_row", ()) or ())
        if len(raw_row) <= column:
            continue
        raw_values.append(str(raw_row[column]))
        candidate = _row_candidate_max(
            row,
            tables=tables,
            column=column,
            max_candidate_value=max_candidate_value,
        )
        if candidate is not None:
            candidate_values.append(candidate)
    return {
        "column": column,
        "role": _COLUMN_ROLES.get(column, "unmapped"),
        "capacity_candidate": column in _CAPACITY_COLUMNS,
        "raw_value_counts": _counter_dict(raw_values, top=top),
        "candidate_value_counts": _counter_dict(candidate_values, top=top),
        "candidate_value": _numeric_summary(candidate_values),
        "coverage": {
            field: _field_coverage(
                rows,
                tables=tables,
                column=column,
                field=field,
                max_candidate_value=max_candidate_value,
                top=top,
            )
            for field in _TARGET_FIELDS
        },
    }


def _ready_rows(
    paths: Iterable[Path],
    *,
    tables: Any,
) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    drop_universe_cache: dict[int, frozenset[int]] = {}
    for path in scp._resolve_paths(paths):
        row = scp._audit_file(
            path,
            tables=tables,
            drop_universe_cache=drop_universe_cache,
        )
        if row.get("status") != "ok":
            continue
        row["residual_mode"] = scp._residual_mode(row)
        row["unique_residual_mode"] = scp._unique_residual_mode(row)
        rows.append(row)
    return tuple(rows)


def summarize_bidmap_raw_capacity_candidates(
    paths: Iterable[Path] = (),
    *,
    tables: Any | None = None,
    max_candidate_value: int = 500,
    top: int = 8,
) -> dict[str, Any]:
    tables = tables or scp.load_monitor_tables()
    rows = _ready_rows(paths, tables=tables)
    raw_column_count = max(
        (
            len(tuple(getattr(bid_map, "raw_row", ()) or ()))
            for bid_map in (getattr(tables, "maps", {}) or {}).values()
        ),
        default=0,
    )
    column_summaries = [
        _column_summary(
            rows,
            tables=tables,
            column=column,
            max_candidate_value=max_candidate_value,
            top=top,
        )
        for column in range(raw_column_count)
    ]
    count_sized_non_capacity = [
        summary
        for summary in column_summaries
        if not summary["capacity_candidate"]
        and summary["candidate_value"]["n"]
    ]
    return {
        "files": len(rows),
        "settlement_rows": len(rows),
        "max_candidate_value": max_candidate_value,
        "unique_residual_modes": _counter_dict(
            (row.get("unique_residual_mode") for row in rows),
            top=top,
        ),
        "capacity_columns": [
            summary for summary in column_summaries if summary["capacity_candidate"]
        ],
        "count_sized_non_capacity_columns": count_sized_non_capacity,
    }


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


def _print_column(summary: Mapping[str, Any]) -> None:
    unique_count = summary["coverage"]["unique_non_temp_item_id_count"]
    unique_cells = summary["coverage"]["unique_non_temp_inventory_cells"]
    q6_cells = summary["coverage"]["unique_q6_non_temp_cells"]
    print(
        " ".join(
            (
                f"col={summary['column']}",
                f"role={summary['role']}",
                f"capacity={summary['capacity_candidate']}",
                f"candidate_values={_format_counts(summary['candidate_value_counts'])}",
                f"candidate={_format_summary(summary['candidate_value'])}",
                "unique_count_cover="
                f"{unique_count['covered_rows']}/{unique_count['compared_rows']}",
                f"unique_count_over={unique_count['over_rows']}",
                "unique_count_over_modes="
                + _format_counts(unique_count["over_unique_residual_modes"]),
                "unique_cells_cover="
                f"{unique_cells['covered_rows']}/{unique_cells['compared_rows']}",
                f"unique_cells_over={unique_cells['over_rows']}",
                "q6_cells_cover="
                f"{q6_cells['covered_rows']}/{q6_cells['compared_rows']}",
                f"q6_cells_over={q6_cells['over_rows']}",
            )
        )
    )
    for example in unique_count["over_examples"][:3]:
        print(
            "  "
            + " ".join(
                (
                    f"example={example['file']}",
                    f"map={example['map_id']}",
                    f"candidate={example['candidate']}",
                    f"target={example['target']}",
                    f"excess={example['excess']}",
                    f"mode={example['unique_residual_mode']}",
                )
            )
        )


def _print_summary(result: Mapping[str, Any], *, include_non_capacity: bool) -> None:
    print(
        " ".join(
            (
                f"files={result['files']}",
                f"settlement_rows={result['settlement_rows']}",
                f"max_candidate_value={result['max_candidate_value']}",
                f"unique_residual_modes={_format_counts(result['unique_residual_modes'])}",
            )
        )
    )
    for summary in result["capacity_columns"]:
        _print_column(summary)
    if include_non_capacity:
        print("non_capacity_count_sized_columns")
        for summary in result["count_sized_non_capacity_columns"]:
            _print_column(summary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit BidMap raw numeric columns against settlement count/cells truth.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--max-candidate-value", type=int, default=500)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument(
        "--include-non-capacity",
        action="store_true",
        help="Also print count-sized schema columns not treated as capacity candidates.",
    )
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    result = summarize_bidmap_raw_capacity_candidates(
        args.paths,
        max_candidate_value=args.max_candidate_value,
        top=args.top,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(result, include_non_capacity=args.include_non_capacity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
