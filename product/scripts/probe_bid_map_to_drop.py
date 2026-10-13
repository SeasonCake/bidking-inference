"""Verify BidMap → Drop.txt linkage and decode the ticket / item-count fields."""

from __future__ import annotations

import io
import json
import sys
from collections import Counter
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from bidking_lab.extract.drop_table import load_drop_table
from bidking_lab.extract.bid_map_table import (
    BID_MAP_TABLE_COLUMN_COUNT,
    BID_MAP_TABLE_COLUMN_COUNT_V2,
)
from bidking_lab.extract.tables import load_table_rows


def _bid_map_indices(row: list[str]) -> dict[str, int]:
    if len(row) == BID_MAP_TABLE_COLUMN_COUNT:
        return {
            "sub_pool_weights": 8,
            "value_tier_ui": 9,
            "rounds_total": 10,
            "entry_fee": 11,
            "entry_requirement": 12,
            "round_caps": 13,
            "starting_budget": 14,
            "drop_ref": 16,
            "mode_flag": 17,
            "bid_price_ladder": 18,
            "round_category_hints": 19,
        }
    if len(row) == BID_MAP_TABLE_COLUMN_COUNT_V2:
        return {
            "sub_pool_weights": 9,
            "value_tier_ui": 10,
            "rounds_total": 11,
            "entry_fee": 12,
            "entry_requirement": 13,
            "round_caps": 14,
            "starting_budget": 15,
            "drop_ref": 17,
            "mode_flag": 18,
            "bid_price_ladder": 19,
            "round_category_hints": 20,
        }
    raise ValueError(f"unexpected BidMap column count {len(row)} for map {row[0]}")


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    tables_in = repo_root / "data" / "raw" / "tables"

    drops = load_drop_table(tables_in / "Drop.txt")
    map_rows = load_table_rows(tables_in / "BidMap.txt")

    map_ids = {int(r[0]) for r in map_rows}
    drop_ids = set(drops.keys())

    overlap = map_ids & drop_ids
    print(f"BidMap ids                 : {len(map_ids)}")
    print(f"Drop.txt pool ids          : {len(drop_ids)}")
    print(f"intersection (map ↔ drop)  : {len(overlap)}")
    print(f"  sample overlap           : {sorted(overlap)[:10]}")
    print(f"  maps without same-id drop: {sorted(map_ids - drop_ids)[:10]}")
    print()

    first_idx = _bid_map_indices(map_rows[0])
    drop_ref_idx = first_idx["drop_ref"]
    print(
        "=== drop_ref decode "
        f"(col[{drop_ref_idx}] for current file) "
        "`[meta_cat, drop_pool_id, min_items?, max_items?]` ==="
    )
    drop_ref_samples = Counter()
    for r in map_rows[:10]:
        idx = _bid_map_indices(r)
        drop_ref = json.loads(r[idx["drop_ref"]])
        print(
            f"  map={r[0]:>4}  col[{idx['drop_ref']}]={drop_ref}  "
            f"→ drop pool {drop_ref[1]} (exists in Drop.txt: {drop_ref[1] in drops})"
        )
        drop_ref_samples[tuple(drop_ref[2:])] += 1
    print(f"  drop_ref trailing pair histogram (across first 10): {dict(drop_ref_samples)}")

    # full histogram of trailing pair
    full_hist = Counter()
    for r in map_rows:
        idx = _bid_map_indices(r)
        drop_ref = json.loads(r[idx["drop_ref"]])
        full_hist[tuple(drop_ref[2:])] += 1
    print(f"  full histogram across all {len(map_rows)} maps: {dict(full_hist)}")
    print()

    print("=== entry_fee decode `[?, ?, value]` (silver) ===")
    for r in map_rows[:6]:
        idx = _bid_map_indices(r)
        entry_fee = json.loads(r[idx["entry_fee"]]) if r[idx["entry_fee"]] not in ("[]", "") else None
        tier_ui = r[idx["value_tier_ui"]]
        print(f"  map={r[0]:>4}  tier_ui={tier_ui:>16}  col[{idx['entry_fee']}]={entry_fee}")
    print()

    print("=== round_caps and starting_budget decode ===")
    for r in map_rows[:6]:
        idx = _bid_map_indices(r)
        round_caps = json.loads(r[idx["round_caps"]]) if r[idx["round_caps"]] not in ("[]", "") else None
        budget = (
            json.loads(r[idx["starting_budget"]])
            if r[idx["starting_budget"]] not in ("[]", "")
            else None
        )
        tier_ui = r[idx["value_tier_ui"]]
        print(
            f"  map={r[0]:>4}  tier_ui={tier_ui:>16}  "
            f"col[{idx['round_caps']}]={round_caps}  "
            f"col[{idx['starting_budget']}]={budget}"
        )
    print()

    print("=== rounds / requirement / mode / ladder / hints for the same first 6 maps ===")
    for r in map_rows[:6]:
        idx = _bid_map_indices(r)
        print(
            f"  map={r[0]:>4}  "
            f"rounds(col[{idx['rounds_total']}])={r[idx['rounds_total']]:>3}  "
            f"requirement(col[{idx['entry_requirement']}])={r[idx['entry_requirement']]:<22}  "
            f"mode(col[{idx['mode_flag']}])={r[idx['mode_flag']]:<3}  "
            f"ladder(col[{idx['bid_price_ladder']}])={r[idx['bid_price_ladder']]:<26}  "
            f"hints(col[{idx['round_category_hints']}])={r[idx['round_category_hints']]}"
        )

    # Same-name maps across tiers: check if their cols differ
    print()
    print("=== Same-name across tiers: 'X 别墅' tier-by-tier ===")
    names_by_tier: dict[str, list[list[str]]] = {}
    for r in map_rows:
        name = r[1].strip()
        names_by_tier.setdefault(name, []).append(r)
    for name, rs in names_by_tier.items():
        if len(rs) >= 2 and "别墅" in name:
            print(f"  {name!r}: {len(rs)} tiers")
            for r in rs:
                idx = _bid_map_indices(r)
                print(
                    f"    id={r[0]:>4}  tier={r[idx['value_tier_ui']]:>16}  "
                    f"rounds={r[idx['rounds_total']]:>3}  "
                    f"entry_fee={r[idx['entry_fee']]:<22}  "
                    f"round_caps={r[idx['round_caps']]:<22}  "
                    f"budget={r[idx['starting_budget']]:<22}  "
                    f"drop_ref={r[idx['drop_ref']]}"
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
