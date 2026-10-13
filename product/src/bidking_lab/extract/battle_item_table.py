"""Parse Tables/BattleItem.txt into typed ``BattleItem`` records.

Battle items are the consumables the player can bring into a round to
reveal information. Schema (6 columns) inferred from descriptions and
cross-verified against Item.txt category 11 (the ID space `100100+`).

Columns:
  0  battle_item_id  (int, PK)
  1  name            (zh)
  2  description     (zh — describes the reveal effect)
  3  tags            (json-list of ints; always ``[11]``)
  4  quality         (1..6, same scale as Item.txt col[8])
  5  effect_type     (int, our naming below)

Effect types (derived from description patterns, may need tuning):
  1 → show full items
  2 → show outlines / shapes
  3 → show total cell count (of a quality slice)
  4 → show average cell count
  5 → show count
  6 → show value
  7 → show quality
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

from pydantic import BaseModel, Field

from bidking_lab.data.quality import color_name
from bidking_lab.extract.tables import load_table_rows

BATTLE_ITEM_TABLE_COLUMN_COUNT = 6

EFFECT_TYPE_LABELS: dict[int, str] = {
    1: "reveal_items",
    2: "reveal_outlines",
    3: "show_total_cells",
    4: "show_avg_cells",
    5: "show_count",
    6: "show_value",
    7: "show_quality",
}


class BattleItem(BaseModel):
    battle_item_id: int
    name: str
    description: str
    tags: list[int]
    quality: int = Field(ge=0, le=6)
    quality_color: str
    effect_type: int
    effect_type_label: str


def _parse_int_list(blob: str) -> list[int]:
    if not blob or blob == "[]":
        return []
    data = json.loads(blob)
    return [int(x) for x in data if isinstance(x, (int, float))]


def parse_battle_item_row(row: Sequence[str]) -> BattleItem:
    if len(row) != BATTLE_ITEM_TABLE_COLUMN_COUNT:
        raise ValueError(
            f"battle item row must have {BATTLE_ITEM_TABLE_COLUMN_COUNT} columns, "
            f"got {len(row)}"
        )
    q = int(row[4])
    et = int(row[5])
    return BattleItem(
        battle_item_id=int(row[0]),
        name=row[1],
        description=row[2],
        tags=_parse_int_list(row[3]),
        quality=q,
        quality_color=color_name(q),
        effect_type=et,
        effect_type_label=EFFECT_TYPE_LABELS.get(et, f"unknown_{et}"),
    )


def parse_battle_item_table(rows: Iterable[Sequence[str]]) -> dict[int, BattleItem]:
    out: dict[int, BattleItem] = {}
    for i, row in enumerate(rows):
        try:
            bi = parse_battle_item_row(row)
        except Exception as exc:
            raise ValueError(f"failed to parse battle item row index {i}: {exc}") from exc
        if bi.battle_item_id in out:
            raise ValueError(f"duplicate battle_item_id={bi.battle_item_id} at row {i}")
        out[bi.battle_item_id] = bi
    return out


def load_battle_item_table(path: Path) -> dict[int, BattleItem]:
    return parse_battle_item_table(load_table_rows(path))


__all__ = (
    "BATTLE_ITEM_TABLE_COLUMN_COUNT",
    "BattleItem",
    "EFFECT_TYPE_LABELS",
    "load_battle_item_table",
    "parse_battle_item_row",
    "parse_battle_item_table",
)
