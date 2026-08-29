"""Precompute reachable per-quality count/cell combinations."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import prepare_session_sampler


@dataclass(frozen=True)
class QualityComboPresolve:
    """Reachable total-cells values for one quality/count pair."""

    quality: int
    count: int
    cells: tuple[int, ...]


def quality_combo_presolve_for_map(
    map_id: int,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    qualities: tuple[int, ...] = (4, 5, 6),
    max_count: int | None = None,
) -> dict[int, dict[int, tuple[int, ...]]]:
    """Return reachable cells by quality and exact item count.

    This is deliberately shape/value agnostic. It answers the fast feasibility
    question used by exact bucket constraints: can this map pool produce
    ``count`` items of quality ``q`` occupying ``cells`` total cells?
    """

    sampler = prepare_session_sampler(map_id, maps=maps, drops=drops, items=items)
    count_limit = max_count or sampler.items_per_session_max
    options: dict[int, set[tuple[int, int]]] = {quality: set() for quality in qualities}
    for pool in sampler.pools:
        for item, n_min, n_max in zip(pool.items, pool.n_min, pool.n_max):
            quality = int(item.quality)
            if quality not in options:
                continue
            area = int(item.shape_w * item.shape_h)
            for count in range(int(n_min), int(n_max) + 1):
                if count <= 0 or count > count_limit:
                    continue
                options[quality].add((count, area * count))

    out: dict[int, dict[int, set[int]]] = {quality: {} for quality in qualities}
    for quality in qualities:
        dp: list[set[int]] = [set() for _ in range(count_limit + 1)]
        dp[0].add(0)
        for total_count in range(1, count_limit + 1):
            cells: set[int] = set()
            for option_count, option_cells in options[quality]:
                if option_count > total_count:
                    continue
                for previous_cells in dp[total_count - option_count]:
                    cells.add(previous_cells + option_cells)
            if cells:
                dp[total_count] = cells
                out[quality][total_count] = cells

    return {
        quality: {
            count: tuple(sorted(cells))
            for count, cells in sorted(by_count.items())
        }
        for quality, by_count in sorted(out.items())
    }


def quality_combo_presolve_payload(
    map_ids: list[int],
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    qualities: tuple[int, ...] = (4, 5, 6),
    max_count: int | None = None,
) -> dict[str, object]:
    """Build a JSON-serializable presolve payload."""

    return {
        "version": 1,
        "qualities": list(qualities),
        "maps": {
            str(map_id): {
                str(quality): {
                    str(count): list(cells)
                    for count, cells in by_count.items()
                }
                for quality, by_count in quality_combo_presolve_for_map(
                    map_id,
                    maps=maps,
                    drops=drops,
                    items=items,
                    qualities=qualities,
                    max_count=max_count,
                ).items()
            }
            for map_id in map_ids
        },
    }


def load_quality_combo_presolve(path: str | Path) -> dict[str, object]:
    """Load a JSON presolve payload."""

    return json.loads(Path(path).read_text(encoding="utf-8"))


def is_quality_combo_reachable(
    payload: Mapping[str, object],
    *,
    map_id: int,
    quality: int,
    count: int | None,
    cells: int | None,
) -> bool | None:
    """Return whether an exact quality count/cells pair is reachable.

    ``None`` means the query is not exact enough or the map/quality is absent
    from the payload. Callers should treat that as "unknown", not impossible.
    """

    if count is None or cells is None:
        return None
    maps_payload = payload.get("maps")
    if not isinstance(maps_payload, Mapping):
        return None
    map_payload = maps_payload.get(str(map_id))
    if not isinstance(map_payload, Mapping):
        return None
    quality_payload = map_payload.get(str(quality))
    if not isinstance(quality_payload, Mapping):
        return None
    cells_payload = quality_payload.get(str(count))
    if not isinstance(cells_payload, list):
        return None
    return int(cells) in {int(value) for value in cells_payload}


__all__ = (
    "QualityComboPresolve",
    "is_quality_combo_reachable",
    "load_quality_combo_presolve",
    "quality_combo_presolve_for_map",
    "quality_combo_presolve_payload",
)
