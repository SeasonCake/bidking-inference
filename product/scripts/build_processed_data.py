"""Produce derived JSON datasets under ``data/processed/``.

The outputs of this script are **committed to git** so users who clone
the repo can run the simulator without owning the game. They differ
from the raw Tables/*.txt files in:

- column names (our schema, not the game's anonymous indices)
- field selection (only what we've verified)
- format (UTF-8 JSON with stable key ordering)

The script only reads from ``data/raw/tables/`` (which IS gitignored,
because those are byte-for-byte copies of game files). If you do not
own the game, the JSONs already in the repo are what you use.

Outputs:
  data/processed/items.json             all parsed items, full Item schema
  data/processed/items_droppable.json   map-reachable physical loot subset
  data/processed/battle_items.json      all parsed battle items
  data/processed/heroes.json            all parsed heroes
  data/processed/maps.json              all parsed maps (summary form only)
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from bidking_lab.extract.battle_item_table import load_battle_item_table
from bidking_lab.extract.bid_map_table import BidMap, load_bid_map_table
from bidking_lab.extract.drop_table import DropPool, load_drop_table
from bidking_lab.extract.hero_table import load_hero_table
from bidking_lab.extract.item_table import Item, load_item_table
from bidking_lab.extract.update606_package_input_contract import (
    Update606PackageInputError,
    derive_legacy_distribution_scope,
)
from bidking_lab.simulation.basic_mc import POOL_REFERENCE_CATEGORY, is_physical_loot_item


def _ensure_utf8_stdio() -> None:
    if sys.platform != "win32":
        return
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=False)


def _map_reachable_valid_droppable_ids(
    *,
    maps: dict[int, BidMap],
    drops: dict[int, DropPool],
    items: dict[int, Item],
    strict_missing: bool = False,
) -> tuple[set[int], set[int], set[int]]:
    """Return physical item ids reachable from at least one map drop tree."""
    droppable_ids: set[int] = set()
    missing_item_ids: set[int] = set()
    missing_pool_ids: set[int] = set()

    def walk(pool_id: int, seen: set[int]) -> None:
        if pool_id in seen:
            return
        seen.add(pool_id)
        pool = drops.get(pool_id)
        if pool is None:
            missing_pool_ids.add(pool_id)
            return
        for entry in pool.entries:
            if entry.category == POOL_REFERENCE_CATEGORY:
                walk(entry.item_id, seen)
                continue
            item = items.get(entry.item_id)
            if item is None:
                missing_item_ids.add(entry.item_id)
                continue
            if is_physical_loot_item(item):
                droppable_ids.add(entry.item_id)

    for bid_map in maps.values():
        walk(bid_map.drop_pool_id, set())
    if strict_missing and (missing_item_ids or missing_pool_ids):
        raise ValueError(
            "legacy distribution input is incomplete: "
            f"missing_items={sorted(missing_item_ids)!r}, "
            f"missing_pools={sorted(missing_pool_ids)!r}"
        )
    return droppable_ids, missing_item_ids, missing_pool_ids


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--legacy-bid-map",
        type=Path,
        help=(
            "Exact legacy package BidMap required when the current BidMap is "
            "the update606 generation."
        ),
    )
    parser.add_argument(
        "--tables-dir",
        type=Path,
        help="Explicit read-only raw table input directory (default: repo data/raw/tables).",
    )
    parser.add_argument(
        "--processed-dir",
        type=Path,
        help="Explicit derived output directory (default: repo data/processed).",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    tables_in = args.tables_dir or repo_root / "data" / "raw" / "tables"
    processed_dir = args.processed_dir or repo_root / "data" / "processed"

    if not tables_in.is_dir():
        print(f"missing raw tables: {tables_in}", file=sys.stderr)
        return 2

    print(f"reading raw tables: {tables_in}")
    print(f"writing processed : {processed_dir}\n")

    items = load_item_table(tables_in / "Item.txt")
    battle_items = load_battle_item_table(tables_in / "BattleItem.txt")
    heroes = load_hero_table(tables_in / "Hero.txt")
    maps = load_bid_map_table(tables_in / "BidMap.txt")
    drops = load_drop_table(tables_in / "Drop.txt")

    distribution_maps = maps
    is_update606 = bool(maps) and {
        len(bid_map.raw_row) for bid_map in maps.values()
    } == {28}
    if is_update606:
        if args.legacy_bid_map is None:
            print(
                "input-assembly-not-ready: update606 requires exact legacy BidMap scope",
                file=sys.stderr,
            )
            return 2
        try:
            scope = derive_legacy_distribution_scope(
                legacy_bid_map_package=args.legacy_bid_map.read_bytes(),
                update606_bid_map_package=(tables_in / "BidMap.txt").read_bytes(),
                legacy_drop_package=(tables_in / "Drop.txt").read_bytes(),
            )
        except (OSError, Update606PackageInputError) as exc:
            print(f"input-assembly-not-ready: {exc}", file=sys.stderr)
            return 2
        distribution_maps = {
            map_id: maps[map_id]
            for map_id in scope.allowed_map_ids
            if map_id in maps
        }
        if set(distribution_maps) != set(scope.allowed_map_ids):
            print(
                "input-assembly-not-ready: update606 legacy map allowlist incomplete",
                file=sys.stderr,
            )
            return 2

    try:
        droppable_ids, missing_item_ids, missing_pool_ids = (
            _map_reachable_valid_droppable_ids(
                maps=distribution_maps,
                drops=drops,
                items=items,
                strict_missing=is_update606,
            )
        )
    except ValueError as exc:
        print(f"input-assembly-not-ready: {exc}", file=sys.stderr)
        return 2

    # Exclude raw_row from JSON to keep the published dataset compact. Users who
    # want every original game-table column can regenerate the full TSV locally
    # via scripts/dump_processed_tables.py.
    exclude_raw = {"raw_row"}

    items_payload = [items[k].model_dump(exclude=exclude_raw) for k in sorted(items)]
    _write_json(processed_dir / "items.json", items_payload)
    print(f"  items.json              : {len(items_payload)} items")

    droppable_payload = [
        items[k].model_dump(exclude=exclude_raw)
        for k in sorted(items)
        if k in droppable_ids
    ]
    _write_json(processed_dir / "items_droppable.json", droppable_payload)
    print(
        f"  items_droppable.json    : {len(droppable_payload)} items "
        f"(map-reachable physical loot)"
    )
    if missing_item_ids:
        preview = ", ".join(str(i) for i in sorted(missing_item_ids)[:20])
        suffix = " ..." if len(missing_item_ids) > 20 else ""
        print(f"    warning: missing Item rows referenced by map drops: {preview}{suffix}")
    if missing_pool_ids:
        preview = ", ".join(str(i) for i in sorted(missing_pool_ids)[:20])
        suffix = " ..." if len(missing_pool_ids) > 20 else ""
        print(f"    warning: missing Drop pools referenced by maps: {preview}{suffix}")


    battle_payload = [battle_items[k].model_dump() for k in sorted(battle_items)]
    _write_json(processed_dir / "battle_items.json", battle_payload)
    print(f"  battle_items.json       : {len(battle_payload)} battle items")

    heroes_payload = [heroes[k].model_dump(exclude=exclude_raw) for k in sorted(heroes)]
    _write_json(processed_dir / "heroes.json", heroes_payload)
    print(f"  heroes.json             : {len(heroes_payload)} heroes")

    maps_payload = [maps[k].model_dump(exclude=exclude_raw) for k in sorted(maps)]
    _write_json(processed_dir / "maps.json", maps_payload)
    print(f"  maps.json               : {len(maps_payload)} maps (summary)")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
