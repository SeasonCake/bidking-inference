"""Export the generation-bound Jianying red category × shape matrix.

The matrix is derived from the packaged droppable catalog, category memory and
positive map weights.  It intentionally contains no maintained item whitelist.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bidking_lab.inference.v2 import physical_red_shape_rule_matrix  # noqa: E402
from bidking_lab.secure.data_crypt import load_json  # noqa: E402


CATEGORY_LABELS = {
    101: "家具",
    102: "医疗",
    103: "时尚",
    104: "兵装",
    105: "矿物",
    106: "古董",
    107: "数码",
    108: "交通",
    109: "食品",
    110: "书籍",
}


def build_payload(map_id: int) -> dict[str, Any]:
    build = physical_red_shape_rule_matrix(map_id)
    name_by_id: dict[int, str] = {}
    try:
        raw_items = load_json(ROOT / "data" / "processed" / "items_droppable.json")
    except Exception:
        raw_items = []
    if isinstance(raw_items, list):
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            try:
                item_id = int(raw["item_id"])
            except (KeyError, TypeError, ValueError):
                continue
            name_by_id[item_id] = str(raw.get("name") or "")

    rules: list[dict[str, Any]] = []
    for rule in build.rules:
        row = rule.as_dict()
        row["category_label"] = CATEGORY_LABELS.get(rule.category, str(rule.category))
        row["candidate_items"] = [
            {
                "item_id": item_id,
                "name": name_by_id.get(item_id, ""),
                "quality": quality,
                "value": value,
            }
            for item_id, quality, value in zip(
                rule.candidate_item_ids,
                rule.candidate_item_qualities,
                rule.candidate_values,
            )
        ]
        rules.append(row)
    return {
        "schema_version": "jianying-red-shape-matrix-v1",
        "map_id": int(map_id),
        "catalog_status": build.catalog_status,
        "catalog_generation": build.catalog_generation,
        "rule_count": len(rules),
        "without_gold_count": sum(
            1 for rule in build.rules if not rule.gold_candidate_item_ids
        ),
        "gold_conditional_count": sum(
            1 for rule in build.rules if rule.gold_candidate_item_ids
        ),
        "rules": rules,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "map_id",
        "category",
        "category_label",
        "shape_key",
        "cells",
        "candidate_qualities",
        "required_excluded_qualities",
        "gold_candidate_item_ids",
        "gold_min_total_cells",
        "gold_min_total_value",
        "red_candidate_item_ids",
        "candidate_items",
        "catalog_generation",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for raw in payload["rules"]:
            row = dict(raw)
            for key in (
                "candidate_qualities",
                "required_excluded_qualities",
                "gold_candidate_item_ids",
                "red_candidate_item_ids",
                "candidate_items",
            ):
                row[key] = json.dumps(row.get(key), ensure_ascii=False, separators=(",", ":"))
            writer.writerow(row)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map-id", type=int, default=5608)
    parser.add_argument("--json", type=Path, dest="json_path")
    parser.add_argument("--csv", type=Path, dest="csv_path")
    args = parser.parse_args(argv)
    payload = build_payload(args.map_id)
    if args.json_path is not None:
        _write_json(args.json_path, payload)
    if args.csv_path is not None:
        _write_csv(args.csv_path, payload)
    if args.json_path is None and args.csv_path is None:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["catalog_status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
