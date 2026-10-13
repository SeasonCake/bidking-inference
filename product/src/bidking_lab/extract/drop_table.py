"""Parse Tables/Drop.txt into typed drop pools.

A *drop pool* is a weighted list of items the game may grant when a player
opens / draws from a certain context (map sub-pool, gift box, hero
unlock, ...). It maps the 5-column TSV row to:

    pool_id, name, description, pool_type, entries

where ``entries[i] = (category, item_id, n_min, n_max, weight)``.

We do *not* normalize weights here — keep raw weights so callers (Monte
Carlo, conditional probability) can decide how to combine pools.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

from pydantic import BaseModel, Field

from bidking_lab.extract.tables import load_table_rows

DROP_TABLE_COLUMN_COUNT = 5


class DropEntry(BaseModel):
    """One row of the inner JSON array on Drop.txt col[4]."""

    category: int
    item_id: int
    n_min: int = Field(ge=0)
    n_max: int = Field(ge=0)
    weight: int = Field(ge=0)


class DropPool(BaseModel):
    """One Drop.txt row, fully typed."""

    pool_id: int
    name: str
    description: str
    pool_type: int
    entries: list[DropEntry]

    @property
    def total_weight(self) -> int:
        return sum(e.weight for e in self.entries)


def _parse_entries_blob(blob: str) -> list[DropEntry]:
    """Parse the col[4] JSON array. Tolerates empty list and `[[]]`."""
    if not blob or blob == "[]":
        return []
    raw = json.loads(blob)
    if not isinstance(raw, list):
        raise ValueError(f"entries blob is not a JSON array: {blob!r}")
    entries: list[DropEntry] = []
    for i, item in enumerate(raw):
        if not isinstance(item, list):
            raise ValueError(f"entries[{i}] is not a list: {item!r}")
        if len(item) == 0:
            continue
        if len(item) != 5:
            raise ValueError(
                f"entries[{i}] has {len(item)} fields, expected 5 "
                f"[category, item_id, n_min, n_max, weight]: {item!r}"
            )
        entries.append(
            DropEntry(
                category=int(item[0]),
                item_id=int(item[1]),
                n_min=int(item[2]),
                n_max=int(item[3]),
                weight=int(item[4]),
            )
        )
    return entries


def parse_drop_row(row: Sequence[str]) -> DropPool:
    if len(row) != DROP_TABLE_COLUMN_COUNT:
        raise ValueError(
            f"drop row must have {DROP_TABLE_COLUMN_COUNT} columns, got {len(row)}: {row!r}"
        )
    pool_id_raw, name, desc, pool_type_raw, entries_blob = row
    return DropPool(
        pool_id=int(pool_id_raw),
        name=name,
        description=desc,
        pool_type=int(pool_type_raw) if pool_type_raw else 0,
        entries=_parse_entries_blob(entries_blob),
    )


def parse_drop_table(rows: Iterable[Sequence[str]]) -> dict[int, DropPool]:
    """Convert the full Drop.txt TSV rows into ``{pool_id: DropPool}``."""
    out: dict[int, DropPool] = {}
    for i, row in enumerate(rows):
        try:
            pool = parse_drop_row(row)
        except Exception as exc:
            raise ValueError(f"failed to parse drop row index {i}: {exc}") from exc
        if pool.pool_id in out:
            raise ValueError(f"duplicate pool_id={pool.pool_id} at row index {i}")
        out[pool.pool_id] = pool
    return out


def load_drop_table(path: Path) -> dict[int, DropPool]:
    """Convenience: decode Drop.txt and return ``{pool_id: DropPool}``."""
    rows = load_table_rows(path)
    return parse_drop_table(rows)


__all__: tuple[str, ...] = (
    "DROP_TABLE_COLUMN_COUNT",
    "DropEntry",
    "DropPool",
    "load_drop_table",
    "parse_drop_row",
    "parse_drop_table",
)
