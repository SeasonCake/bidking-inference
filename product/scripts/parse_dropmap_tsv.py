#!/usr/bin/env python3
"""Parse a memory-extracted RankMap TSV into a review-only JSON candidate.

The historical filename is retained only for import compatibility. The seven
tab-separated columns belong to RankMap; no Drop/DropMap TextAsset has been
verified in the official package.

``map_id, name, description, range39, area_w, value_dist, col6``

The last four columns are JSON arrays.  This converter is deliberately
fail-closed: malformed rows, duplicate map ids, and invalid weight structures
abort the whole conversion.  It also refuses to overwrite an existing output,
so a fresh game-update scan cannot silently replace an approved data file.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


def _positive_int(value: Any, *, field: str, row_number: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"row {row_number}: {field} must be a positive integer")
    return value


def _weighted_ranges(value: Any, *, field: str, row_number: int) -> list[list[int]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"row {row_number}: {field} must be a non-empty list")
    result: list[list[int]] = []
    previous_high: int | None = None
    for index, entry in enumerate(value):
        if not isinstance(entry, list) or len(entry) != 3:
            raise ValueError(f"row {row_number}: {field}[{index}] must be [low, high, weight]")
        low = _positive_int(entry[0], field=f"{field}[{index}].low", row_number=row_number)
        high = _positive_int(entry[1], field=f"{field}[{index}].high", row_number=row_number)
        weight = _positive_int(entry[2], field=f"{field}[{index}].weight", row_number=row_number)
        if low > high:
            raise ValueError(f"row {row_number}: {field}[{index}] low exceeds high")
        # The live table sometimes reuses the previous upper boundary as the
        # next lower boundary (for example 5001-10000 then 10000-20000).
        # Preserve that source contract; only a true backwards overlap fails.
        if previous_high is not None and low < previous_high:
            raise ValueError(f"row {row_number}: {field} ranges overlap or are unsorted")
        previous_high = high
        result.append([low, high, weight])
    return result


def _weighted_ids(value: Any, *, field: str, row_number: int) -> list[list[int]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"row {row_number}: {field} must be a non-empty list")
    result: list[list[int]] = []
    seen: set[int] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, list) or len(entry) != 2:
            raise ValueError(f"row {row_number}: {field}[{index}] must be [id, weight]")
        item_id = _positive_int(entry[0], field=f"{field}[{index}].id", row_number=row_number)
        weight = _positive_int(entry[1], field=f"{field}[{index}].weight", row_number=row_number)
        if item_id in seen:
            raise ValueError(f"row {row_number}: duplicate {field} id {item_id}")
        seen.add(item_id)
        result.append([item_id, weight])
    return result


def _anchor_pairs(value: Any, *, field: str, row_number: int) -> list[int]:
    if not isinstance(value, list) or len(value) < 4 or len(value) % 2:
        raise ValueError(f"row {row_number}: {field} must contain value/weight pairs")
    result = [
        _positive_int(item, field=f"{field}[{index}]", row_number=row_number)
        for index, item in enumerate(value)
    ]
    anchors = result[::2]
    if anchors != sorted(set(anchors)):
        raise ValueError(f"row {row_number}: {field} value anchors must be unique and increasing")
    return result


def parse_rankmap_memory_tsv(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.reader(handle, delimiter="\t")
        for row_number, row in enumerate(rows, 1):
            if not row or not any(cell.strip() for cell in row):
                continue
            if len(row) != 7:
                raise ValueError(f"row {row_number}: expected 7 columns, found {len(row)}")
            map_id, name, description, range_raw, area_raw, value_raw, col6_raw = row
            if not map_id.isascii() or not map_id.isdigit() or str(int(map_id)) != map_id:
                raise ValueError(f"row {row_number}: invalid map id")
            if map_id in result:
                raise ValueError(f"row {row_number}: duplicate map id {map_id}")
            try:
                range39 = json.loads(range_raw)
                area_w = json.loads(area_raw)
                value_dist = json.loads(value_raw)
                col6 = json.loads(col6_raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"row {row_number}: invalid JSON in RankMap columns") from exc
            result[map_id] = {
                "name": name,
                "range39": _weighted_ranges(range39, field="range39", row_number=row_number),
                "area_w": _weighted_ids(area_w, field="area_w", row_number=row_number),
                "value_dist": _weighted_ranges(value_dist, field="value_dist", row_number=row_number),
                "col6": _anchor_pairs(col6, field="col6", row_number=row_number),
            }
    if not result:
        raise ValueError("RankMap TSV contains no records")
    return result


def parse_dropmap_tsv(path: Path) -> dict[str, dict[str, Any]]:
    """Compatibility alias for the legacy misnamed parser; semantics are RankMap."""

    return parse_rankmap_memory_tsv(path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="memory-extracted RankMap.tsv")
    parser.add_argument("--out", type=Path, required=True, help="new review-only JSON path")
    args = parser.parse_args(argv[1:])
    if args.input.name.casefold() == "dropmap.tsv":
        parser.error(
            "DropMap.tsv is a legacy-mislabeled RankMap; rename the reviewed input "
            "to RankMap.tsv before conversion"
        )
    if args.out.exists():
        parser.error(f"refusing to overwrite existing output: {args.out}")
    parsed = parse_rankmap_memory_tsv(args.input)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(parsed, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"parsed {len(parsed)} maps -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
