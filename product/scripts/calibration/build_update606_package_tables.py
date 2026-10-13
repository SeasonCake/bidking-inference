#!/usr/bin/env python3
"""Build exact canonical package BidMap/Item from official update606 bytes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.extract.update606_package_input_contract import (  # noqa: E402
    Update606PackageInputError,
    build_update606_items_catalog,
    build_update606_package_table,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-bid-map", type=Path, required=True)
    parser.add_argument("--official-item", type=Path, required=True)
    parser.add_argument("--legacy-items-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--items-json-out", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    args = parser.parse_args(argv)
    inputs = {"BidMap": args.official_bid_map, "Item": args.official_item}
    input_bytes: dict[str, bytes] = {}
    outputs: dict[str, bytes] = {}
    receipts: dict[str, object] = {}
    try:
        for name, path in inputs.items():
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise Update606PackageInputError(
                    f"missing/unreadable official {name} TextAsset"
                ) from exc
            input_bytes[name] = raw
            outputs[f"{name}.txt"], receipts[name] = build_update606_package_table(
                name,
                raw,
            )
    except Update606PackageInputError as exc:
        print(f"input-assembly-not-ready: {exc}", file=sys.stderr)
        return 2
    try:
        legacy_items = args.legacy_items_json.read_bytes()
        items_json, receipts["items.json"] = build_update606_items_catalog(
            input_bytes["Item"],
            legacy_items,
        )
    except OSError:
        print(
            "input-assembly-not-ready: missing/unreadable approved legacy items catalog",
            file=sys.stderr,
        )
        return 2
    except Update606PackageInputError as exc:
        print(f"input-assembly-not-ready: {exc}", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    args.items_json_out.parent.mkdir(parents=True, exist_ok=True)
    for name, payload in outputs.items():
        (args.output_dir / name).write_bytes(payload)
    args.items_json_out.write_bytes(items_json)
    args.receipt_out.write_text(
        json.dumps(
            {
                "schema": "bidking_update606_package_tables_build_receipt_v1",
                "tables": receipts,
            },
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("wrote exact update606 package tables and items catalog")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
