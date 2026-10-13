"""Minimal typed placeholders — extend when real tables are known."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field


class Rarity(IntEnum):
    """Placeholder — align with game enums once extracted."""

    UNKNOWN = 0


class ItemRef(BaseModel):
    """Stable reference to an item type (id from game data when known)."""

    item_id: int
    name_hint: str | None = None


class GridShape(BaseModel):
    """2D footprint for convolution / overlap checks (rows of 0/1)."""

    rows: int = Field(ge=1)
    cols: int = Field(ge=1)
    cells: tuple[tuple[int, ...], ...]  # each row 0/1


class WarehouseContext(BaseModel):
    """Everything needed to describe one warehouse scenario (probabilities TBD)."""

    warehouse_id: str | None = None
    map_id: str | None = None
    collector_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
