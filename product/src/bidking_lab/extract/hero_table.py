"""Parse Tables/Hero.txt into typed ``Hero`` records.

21 columns; only col[0] (id), col[1] (name) and col[2] (skill description)
are confirmed enough to name. The rest are kept on ``raw_row``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from pydantic import BaseModel

from bidking_lab.extract.tables import load_table_rows

HERO_TABLE_COLUMN_COUNT = 21


class Hero(BaseModel):
    hero_id: int
    name: str
    skill_description: str
    raw_row: list[str]


def parse_hero_row(row: Sequence[str]) -> Hero:
    if len(row) != HERO_TABLE_COLUMN_COUNT:
        raise ValueError(
            f"hero row must have {HERO_TABLE_COLUMN_COUNT} columns, got {len(row)}"
        )
    return Hero(
        hero_id=int(row[0]),
        name=row[1],
        skill_description=row[2],
        raw_row=list(row),
    )


def parse_hero_table(rows: Iterable[Sequence[str]]) -> dict[int, Hero]:
    out: dict[int, Hero] = {}
    for i, row in enumerate(rows):
        try:
            hero = parse_hero_row(row)
        except Exception as exc:
            raise ValueError(f"failed to parse hero row index {i}: {exc}") from exc
        if hero.hero_id in out:
            raise ValueError(f"duplicate hero_id={hero.hero_id} at row {i}")
        out[hero.hero_id] = hero
    return out


def load_hero_table(path: Path) -> dict[int, Hero]:
    return parse_hero_table(load_table_rows(path))


__all__ = (
    "HERO_TABLE_COLUMN_COUNT",
    "Hero",
    "load_hero_table",
    "parse_hero_row",
    "parse_hero_table",
)
