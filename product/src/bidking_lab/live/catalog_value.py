"""Pure catalog/value completeness projection for settlement inventory.

``settlement_incomplete`` describes capture-frame completeness.  This module
keeps the independent lookup question explicit: whether every settled item has
a usable catalog value.  A partial sum remains a known lower bound; it is not
promoted to a complete settlement value when any item identity is unresolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class CatalogValueSummary:
    known_value_sum: int | None
    catalog_value_complete: bool | None
    unknown_item_ids: tuple[int, ...]
    settled_item_count: int
    valued_item_count: int


def summarize_catalog_values(
    inventory_items: Iterable[Any],
    catalog: Mapping[int, Any],
) -> CatalogValueSummary:
    """Return a fail-closed catalog lookup summary without inventing values."""

    settled = tuple(inventory_items)
    if not settled:
        return CatalogValueSummary(
            known_value_sum=None,
            catalog_value_complete=None,
            unknown_item_ids=(),
            settled_item_count=0,
            valued_item_count=0,
        )

    known_value = 0
    valued = 0
    unknown: set[int] = set()
    for row in settled:
        raw_item_id = getattr(row, "item_id", None)
        if type(raw_item_id) is not int or raw_item_id <= 0:  # noqa: E721
            unknown.add(0)
            continue
        item = catalog.get(raw_item_id)
        value = getattr(item, "value", None) if item is not None else None
        if type(value) is not int or value < 0:  # noqa: E721
            unknown.add(raw_item_id)
            continue
        known_value += value
        valued += 1

    return CatalogValueSummary(
        known_value_sum=known_value,
        catalog_value_complete=not unknown,
        unknown_item_ids=tuple(sorted(unknown)),
        settled_item_count=len(settled),
        valued_item_count=valued,
    )


__all__ = ("CatalogValueSummary", "summarize_catalog_values")
