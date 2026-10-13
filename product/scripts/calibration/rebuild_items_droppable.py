"""Rebuild data/processed/items_droppable.json from the authoritative droppable set.

WHY THIS EXISTS (2026-06-27 close-out): the OLD items_droppable.json was derived by
build_processed_data.py from the legacy Drop.txt explicit pools, which 6.18 YooAsset
killed — so it was MISSING every new collectible (1916 season / 1006 机甲散件 / 1115-1116
未知藏品箱 / etc.). The new authoritative "what can drop" definition is
``data/processed/item_category_mem_0625.json``: an item is droppable iff its category is
``100`` (特殊藏品 = 机甲散件及常驻龙蛋) /
``101-110`` (10 standard 大类) / ``111`` (未知藏品箱).
The 0.3.0 activity catalog added the eight Summer Party collectibles and the historical
0.3.4 model retained 680 active rows after removing four retired World Cup items.  The
exact September 3 42-column generation closes that event, removes the eight Summer
items from current modeled support, and adds only the explicitly routed 31 season q6,
six non-S2 Autumn q6, and eight S2 shadow-puppet parts.  Their union is 717 rows;
per-map 210x/560x support remains separately bound by the current RankMap contract.
Historical identity remains in items.json / Item.txt and the previous model receipt.

This rebuild backs up the existing file then writes the full droppable set as item
objects (item_id/name/quality/value/shape_w/shape_h/tags/...). tier_value_subset.py only
reads item_id/quality/value/shape_w/shape_h. Re-run after item_category or catalog updates:
  python scripts/calibration/rebuild_items_droppable.py
"""
from __future__ import annotations

import json
import hashlib
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "calibration"))
sys.path.insert(0, str(ROOT / "src"))
import rankmap_drop_model as drop_model  # noqa: E402
from bidking_lab.extract.item_table import load_item_table  # noqa: E402
from bidking_lab.extract.current_rankmap_package_contract import (  # noqa: E402
    CURRENT_ALL_MAP_Q6_ITEM_IDS,
    CURRENT_EXCHANGE_OR_PASS_Q6_ITEM_IDS,
    CURRENT_MODEL_UNION_ITEM_IDS_SHA256,
    CURRENT_MODEL_UNION_ITEM_ROWS,
    CURRENT_NON_S2_AUTUMN_Q6_ITEM_IDS,
    CURRENT_S2_SHADOW_PUPPET_Q6_ITEM_IDS,
    RETIRED_SUMMER_PARTY_2026_ITEM_IDS,
)

PROC = ROOT / "data" / "processed"
OUT = PROC / "items_droppable.json"
_DROPPABLE_SEMANTIC_FIELDS = (
    "item_id",
    "name",
    "description",
    "name_key",
    "desc_key",
    "quality",
    "quality_color",
    "value",
    "shape_w",
    "shape_h",
    "tags",
    "allowed_shelves",
    "icon_name",
    "model_name",
)


def droppable_item(item_id: int, cat: int) -> bool:
    """Apply the current explicit lifecycle on top of the category-qualified set."""
    if (
        item_id in drop_model.RETIRED_WORLD_CUP_Q6_ITEM_IDS
        and os.environ.get(
            "BIDKING_ENABLE_RETIRED_WORLD_CUP_Q6_COLLECTIBLES"
        )
        != "1"
    ):
        return False
    if (
        item_id in drop_model.SUMMER_PARTY_2026_ITEM_IDS
        and os.environ.get("BIDKING_DISABLE_SUMMER_PARTY_2026_COLLECTIBLES")
        == "1"
    ):
        return False
    return cat == 100 or (101 <= cat <= 110) or cat == 111


def _ids_sha256(values: list[int]) -> str:
    return hashlib.sha256(
        json.dumps(values, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def _current_droppable_item_ids(
    *,
    table: dict[int, object],
    legacy_categories: dict[int, int],
) -> list[int]:
    """Return the exact eb1 union support used by all current map models."""

    legacy = {
        item_id
        for item_id, category in legacy_categories.items()
        if droppable_item(item_id, category)
    }
    if (
        len(legacy) != 680
        or _ids_sha256(sorted(legacy))
        != "abdd31dd36d9257b98f0a9c5fbf269ef467cae1ab0f41387be6b5b9985a36264"
    ):
        raise ValueError("previous droppable support identity mismatch")
    current = legacy - set(RETIRED_SUMMER_PARTY_2026_ITEM_IDS)
    current.update(CURRENT_ALL_MAP_Q6_ITEM_IDS)
    current.update(CURRENT_NON_S2_AUTUMN_Q6_ITEM_IDS)
    current.update(CURRENT_S2_SHADOW_PUPPET_Q6_ITEM_IDS)
    ids = sorted(current)
    if (
        len(ids) != CURRENT_MODEL_UNION_ITEM_ROWS
        or _ids_sha256(ids) != CURRENT_MODEL_UNION_ITEM_IDS_SHA256
        or set(CURRENT_EXCHANGE_OR_PASS_Q6_ITEM_IDS) & current
    ):
        raise ValueError("current droppable support identity mismatch")

    expected_routes = {
        tuple(CURRENT_ALL_MAP_Q6_ITEM_IDS): (
            101, 102, 103, 104, 105, 106, 201, 304, 305, 402, 501, 601
        ),
        tuple(CURRENT_NON_S2_AUTUMN_Q6_ITEM_IDS): (
            101, 102, 103, 104, 105, 106, 201, 304, 305, 501, 601
        ),
        tuple(CURRENT_S2_SHADOW_PUPPET_Q6_ITEM_IDS): (402,),
    }
    for item_ids, routes in expected_routes.items():
        for item_id in item_ids:
            item = table.get(item_id)
            if (
                item is None
                or getattr(item, "quality", None) != 6
                or getattr(item, "value", 0) <= 0
                or getattr(item, "shape_w", 0) * getattr(item, "shape_h", 0) <= 0
                or json.loads(item.raw_row[39]) != list(routes)
            ):
                raise ValueError(f"current lifecycle mismatch item_id={item_id}")
    for item_id in (*RETIRED_SUMMER_PARTY_2026_ITEM_IDS, *CURRENT_EXCHANGE_OR_PASS_Q6_ITEM_IDS):
        item = table.get(item_id)
        if item is None or json.loads(item.raw_row[39]) != []:
            raise ValueError(f"current excluded lifecycle mismatch item_id={item_id}")
    return ids


def _build_authoritative_row(
    item_id: int,
    *,
    old_by: dict[int, dict],
    catalog: dict[int, dict],
    table: dict,
) -> dict | None:
    """Build one row from current authority; reuse old bytes only if semantic-equal."""

    src = catalog.get(item_id)
    if src:
        authoritative = dict(src)
        authoritative.setdefault("description", "")
        provenance = "catalog_mem"
    else:
        item = table.get(item_id)
        if item is None:
            return None
        authoritative = {
            "item_id": item_id,
            "name": item.name,
            "description": item.description,
            "name_key": item.name_key,
            "desc_key": item.desc_key,
            "quality": item.quality,
            "quality_color": item.quality_color,
            "value": item.value,
            "shape_w": item.shape_w,
            "shape_h": item.shape_h,
            "tags": item.tags,
            "allowed_shelves": item.allowed_shelves,
            "icon_name": item.icon_name,
            "model_name": item.model_name,
        }
        provenance = "item_txt"

    old = old_by.get(item_id)
    if old is not None and all(
        old.get(field) == authoritative.get(field)
        for field in _DROPPABLE_SEMANTIC_FIELDS
    ):
        row = dict(old)
        row.setdefault("_droppable_src", "orig")
        return row

    authoritative["_droppable_src"] = provenance
    return authoritative


def main() -> int:
    tbl = load_item_table(ROOT / "data" / "raw" / "tables" / "Item.txt")
    if tbl and {len(row.raw_row) for row in tbl.values()} == {45}:
        raise ValueError(
            "76b7ca requires build_official_20261008_inputs.py; "
            "the legacy droppable CLI cannot rebuild its qualified support"
        )
    catalog_raw = json.loads((PROC / "items.json").read_text(encoding="utf-8"))
    catalog = {int(x["item_id"]): x for x in (catalog_raw if isinstance(catalog_raw, list) else catalog_raw.values())}
    cat = {int(k): v for k, v in json.loads((PROC / "item_category_mem_0625.json").read_text(encoding="utf-8")).items()}
    exact_current = bool(tbl) and {len(row.raw_row) for row in tbl.values()} == {42}
    if exact_current:
        drop_ids = _current_droppable_item_ids(
            table=tbl,
            legacy_categories=cat,
        )
    else:
        drop_ids = sorted(
            i for i, c in cat.items() if droppable_item(int(i), int(c))
        )

    old_by: dict[int, dict] = {}
    if OUT.is_file():
        bak = PROC / "items_droppable.pre_rebuild.bak.json"
        bak.write_text(OUT.read_text(encoding="utf-8"), encoding="utf-8")
        for r in json.loads(OUT.read_text(encoding="utf-8")):
            old_by[int(r["item_id"])] = r
        print(f"backed up old {len(old_by)} -> {bak.name}")

    build_old_by = {} if exact_current else old_by
    rows = [
        _build_authoritative_row(
            i,
            old_by=build_old_by,
            catalog=catalog,
            table=tbl,
        )
        for i in drop_ids
    ]
    rows = [r for r in rows if r and int(r.get("quality") or 0) > 0 and int(r.get("value") or 0) > 0
            and int(r.get("shape_w") or 0) * int(r.get("shape_h") or 0) > 0]
    if exact_current and (
        len(rows) != CURRENT_MODEL_UNION_ITEM_ROWS
        or _ids_sha256([int(row["item_id"]) for row in rows])
        != CURRENT_MODEL_UNION_ITEM_IDS_SHA256
    ):
        raise ValueError("current droppable rendered rows mismatch")
    OUT.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(rows)} droppable items -> {OUT.name}  src={dict(Counter(r['_droppable_src'] for r in rows))}")
    if old_by:
        missing = [i for i in old_by if i not in {int(r["item_id"]) for r in rows}]
        print(f"old items preserved: {len(old_by) - len(missing)}/{len(old_by)} (dropped {len(missing)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
