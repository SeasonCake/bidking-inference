"""Enumerate tier (count, cells) candidates from exact value or avg_value.

Uses map droppable item catalog prices (Item.txt values). Each item may appear
at most once per tier multiset — matches warehouse slot semantics when Drop n_max=1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
import time
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from bidking_lab.extract.bid_map_table import BidMap, load_bid_map_table
from bidking_lab.extract.drop_table import DropPool, load_drop_table
from bidking_lab.extract.item_table import Item, load_item_table
from bidking_lab.inference.ground_truth import prepare_session_sampler
from bidking_lab.simulation.basic_mc import is_physical_loot_item


@dataclass(frozen=True)
class TierPoolItem:
    item_id: int
    value: int
    cells: int


@dataclass(frozen=True)
class TierComboCandidate:
    count: int
    cells: int
    value_sum: int


@dataclass
class TierEnumerationStats:
    pool_size: int
    target_value: int | None
    avg_value: float | None
    candidates: tuple[TierComboCandidate, ...] = ()
    nodes_visited: int = 0
    elapsed_ms: float = 0.0
    method: str = ""

    @property
    def unique_count_cells(self) -> tuple[tuple[int, int], ...]:
        return tuple(sorted({(c.count, c.cells) for c in self.candidates}))

    @property
    def unique_counts(self) -> tuple[int, ...]:
        return tuple(sorted({c.count for c in self.candidates}))


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_tables_dir() -> Path | None:
    env = os.environ.get("BIDKING_TABLES_DIR")
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env).expanduser())
    root = _repo_root()
    candidates.extend(
        [
            root / "data" / "raw" / "tables",
            root / "hero_ref" / "data" / "raw" / "tables",
        ]
    )
    for path in candidates:
        if (path / "Item.txt").is_file() and (path / "Drop.txt").is_file():
            return path
    return None


def load_droppable_items_from_processed(
    path: Path | None = None,
) -> dict[int, TierPoolItem]:
    payload_path = path or (_repo_root() / "data" / "processed" / "items_droppable.json")
    rows = json.loads(payload_path.read_text(encoding="utf-8"))
    out: dict[int, TierPoolItem] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        item_id = int(row["item_id"])
        quality = int(row.get("quality") or 0)
        value = int(row.get("value") or 0)
        w = int(row.get("shape_w") or 0)
        h = int(row.get("shape_h") or 0)
        cells = w * h
        if quality <= 0 or value <= 0 or cells <= 0:
            continue
        out[item_id] = TierPoolItem(item_id=item_id, value=value, cells=cells)
    return out


def _quality_pool_from_sampler(
    sampler: object,
    *,
    quality: int,
) -> list[TierPoolItem]:
    seen: dict[int, TierPoolItem] = {}
    for pool in sampler.pools:  # type: ignore[attr-defined]
        for item, n_max in zip(pool.items, pool.n_max):  # type: ignore[attr-defined]
            if int(item.quality) != int(quality):
                continue
            if int(n_max) <= 0:
                continue
            if not is_physical_loot_item(item):
                continue
            cells = int(item.shape_w * item.shape_h)
            if cells <= 0:
                continue
            seen[int(item.item_id)] = TierPoolItem(
                item_id=int(item.item_id),
                value=int(item.value),
                cells=cells,
            )
    return sorted(seen.values(), key=lambda row: (-row.value, row.item_id))


def quality_pool_for_map(
    map_id: int,
    quality: int,
    *,
    tables_dir: Path | None = None,
) -> tuple[list[TierPoolItem], str]:
    tables_dir = tables_dir or resolve_tables_dir()
    if tables_dir is not None:
        items = load_item_table(tables_dir / "Item.txt")
        maps = load_bid_map_table(tables_dir / "BidMap.txt")
        drops = load_drop_table(tables_dir / "Drop.txt")
        if int(map_id) in maps:
            sampler = prepare_session_sampler(int(map_id), maps=maps, drops=drops, items=items)
            pool = _quality_pool_from_sampler(sampler, quality=int(quality))
            if pool:
                return pool, f"map_drop_pool:{map_id}:q{quality}"
    return quality_pool_catalog(int(quality))


def quality_pool_catalog(
    quality: int,
    *,
    droppable_path: Path | None = None,
) -> tuple[list[TierPoolItem], str]:
    payload_path = droppable_path or (_repo_root() / "data" / "processed" / "items_droppable.json")
    rows = json.loads(payload_path.read_text(encoding="utf-8"))
    pool: list[TierPoolItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if int(row.get("quality") or 0) != int(quality):
            continue
        value = int(row.get("value") or 0)
        w = int(row.get("shape_w") or 0)
        h = int(row.get("shape_h") or 0)
        cells = w * h
        if value <= 0 or cells <= 0:
            continue
        pool.append(
            TierPoolItem(
                item_id=int(row["item_id"]),
                value=value,
                cells=cells,
            )
        )
    pool = sorted({row.item_id: row for row in pool}.values(), key=lambda r: (-r.value, r.item_id))
    return pool, f"catalog_droppable:q{quality}"


def _trim_pool_for_target(
    pool: Sequence[TierPoolItem],
    *,
    target: int,
    max_count: int,
    max_pool: int = 32,
) -> list[TierPoolItem]:
    if not pool or target <= 0:
        return []
    floor_value = max(1, int(target // max(max_count, 1)) - 1)
    filtered = [row for row in pool if floor_value <= row.value <= target]
    if not filtered:
        filtered = list(pool)
    filtered.sort(key=lambda row: (abs(row.value * max_count - target), row.value))
    return filtered[: max(8, min(int(max_pool), len(filtered)))]


def _meet_in_the_middle_exact(
    pool: Sequence[TierPoolItem],
    *,
    target: int,
    max_count: int,
    stats: TierEnumerationStats,
) -> list[TierComboCandidate]:
    mid = len(pool) // 2
    left = list(pool[:mid])
    right = list(pool[mid:])

    def _half_combos(items: Sequence[TierPoolItem], count_limit: int) -> dict[tuple[int, int], set[int]]:
        out: dict[tuple[int, int], set[int]] = {}
        n = len(items)

        def walk(index: int, count: int, value_sum: int, cells: int) -> None:
            stats.nodes_visited += 1
            if count > count_limit or value_sum > target:
                return
            key = (count, value_sum)
            bucket = out.setdefault(key, set())
            bucket.add(cells)
            if index >= n:
                return
            walk(index + 1, count, value_sum, cells)
            item = items[index]
            walk(index + 1, count + 1, value_sum + item.value, cells + item.cells)

        walk(0, 0, 0, 0)
        return out

    left_counts = _half_combos(left, max_count)
    right_counts = _half_combos(right, max_count)
    right_index: dict[tuple[int, int], set[int]] = {}
    for (count, value_sum), cells in right_counts.items():
        right_index.setdefault((count, value_sum), set()).update(cells)
    results: dict[tuple[int, int, int], TierComboCandidate] = {}
    for (left_count, left_sum), left_cells in left_counts.items():
        remaining = target - left_sum
        for right_count in range(0, max_count - left_count + 1):
            r_cells = right_index.get((right_count, remaining))
            if not r_cells:
                continue
            total_count = left_count + right_count
            if total_count <= 0 or total_count > max_count:
                continue
            for lc in left_cells:
                for rc_cells in r_cells:
                    cells = lc + rc_cells
                    key = (total_count, cells, target)
                    results[key] = TierComboCandidate(
                        count=total_count,
                        cells=cells,
                        value_sum=target,
                    )
    return list(results.values())


def _dfs_exact_combos(
    pool: Sequence[TierPoolItem],
    *,
    target: int,
    max_count: int,
    tolerance: int,
    stats: TierEnumerationStats,
) -> list[TierComboCandidate]:
    pool = sorted(pool, key=lambda row: row.value, reverse=True)
    if len(pool) > 24:
        return _meet_in_the_middle_exact(
            pool,
            target=target,
            max_count=max_count,
            stats=stats,
        )
    min_values_suffix = [0] * (len(pool) + 1)
    for index in range(len(pool) - 1, -1, -1):
        min_values_suffix[index] = min_values_suffix[index + 1] + pool[index].value

    results: list[TierComboCandidate] = []

    def visit(index: int, remaining: int, count: int, cells: int, value_sum: int) -> None:
        stats.nodes_visited += 1
        if count > max_count:
            return
        if remaining == 0:
            results.append(
                TierComboCandidate(count=count, cells=cells, value_sum=value_sum)
            )
            return
        if remaining < -tolerance:
            return
        if index >= len(pool):
            return
        if count == max_count:
            return
        # Cannot reach target even taking all remaining items.
        if remaining - min_values_suffix[index] > tolerance:
            return
        item = pool[index]
        # Skip item.
        visit(index + 1, remaining, count, cells, value_sum)
        # Take item.
        if item.value <= remaining + tolerance:
            visit(
                index + 1,
                remaining - item.value,
                count + 1,
                cells + item.cells,
                value_sum + item.value,
            )

    visit(0, target, 0, 0, 0)
    # Deduplicate identical (count, cells, value).
    unique: dict[tuple[int, int, int], TierComboCandidate] = {}
    for row in results:
        key = (row.count, row.cells, row.value_sum)
        unique[key] = row
    return list(unique.values())


def enumerate_exact_value_combos(
    pool: Sequence[TierPoolItem],
    target_value: int,
    *,
    max_count: int = 15,
    tolerance: int = 0,
    max_pool: int = 32,
) -> TierEnumerationStats:
    started = time.perf_counter()
    trimmed = _trim_pool_for_target(
        pool,
        target=int(target_value),
        max_count=int(max_count),
        max_pool=int(max_pool),
    )
    stats = TierEnumerationStats(
        pool_size=len(trimmed),
        target_value=int(target_value),
        avg_value=None,
        method="trimmed_dfs_or_mitm",
    )
    stats.candidates = tuple(
        _dfs_exact_combos(
            trimmed,
            target=int(target_value),
            max_count=int(max_count),
            tolerance=int(tolerance),
            stats=stats,
        )
    )
    stats.elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    return stats


def _avg_value_targets(avg: float, max_count: int, *, tolerance: float) -> list[tuple[int, int]]:
    targets: list[tuple[int, int]] = []
    for count in range(1, int(max_count) + 1):
        product = float(avg) * float(count)
        nearest = int(round(product))
        if abs(product - nearest) <= tolerance:
            targets.append((count, nearest))
    return targets


def enumerate_avg_value_combos(
    pool: Sequence[TierPoolItem],
    avg_value: float,
    *,
    max_count: int = 15,
    product_tolerance: float = 0.51,
    max_pool: int = 32,
) -> TierEnumerationStats:
    started = time.perf_counter()
    stats = TierEnumerationStats(
        pool_size=len(pool),
        target_value=None,
        avg_value=float(avg_value),
        method="avg_x_count_exact_sums",
    )
    merged: dict[tuple[int, int, int], TierComboCandidate] = {}
    for count, target in _avg_value_targets(avg_value, max_count, tolerance=product_tolerance):
        partial = enumerate_exact_value_combos(
            pool,
            target,
            max_count=count,
            tolerance=0,
            max_pool=max_pool,
        )
        stats.nodes_visited += partial.nodes_visited
        for candidate in partial.candidates:
            if candidate.count != count:
                continue
            key = (candidate.count, candidate.cells, candidate.value_sum)
            merged[key] = candidate
    stats.candidates = tuple(merged.values())
    stats.elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    return stats


def filter_candidates_by_presolve_cells(
    candidates: Iterable[TierComboCandidate],
    *,
    presolve_payload: Mapping[str, object],
    map_id: int,
    quality: int,
) -> tuple[TierComboCandidate, ...]:
    maps_payload = presolve_payload.get("maps")
    if not isinstance(maps_payload, Mapping):
        return tuple(candidates)
    map_payload = maps_payload.get(str(map_id))
    if not isinstance(map_payload, Mapping):
        return tuple(candidates)
    quality_payload = map_payload.get(str(quality))
    if not isinstance(quality_payload, Mapping):
        return tuple(candidates)

    kept: list[TierComboCandidate] = []
    for candidate in candidates:
        cells_payload = quality_payload.get(str(candidate.count))
        if not isinstance(cells_payload, list):
            continue
        allowed = {int(value) for value in cells_payload}
        if int(candidate.cells) in allowed:
            kept.append(candidate)
    return tuple(kept)


def load_maps_summary(path: Path | None = None) -> dict[int, dict[str, int]]:
    payload_path = path or (_repo_root() / "data" / "processed" / "maps.json")
    rows = json.loads(payload_path.read_text(encoding="utf-8"))
    out: dict[int, dict[str, int]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        map_id = int(row["map_id"])
        out[map_id] = {
            "items_min": int(row.get("items_per_session_min") or 0),
            "items_max": int(row.get("items_per_session_max") or 40),
        }
    return out


def benchmark_pool_scaling(
    pool: Sequence[TierPoolItem],
    *,
    target_fraction: float = 0.45,
    sizes: Sequence[int] = (20, 40, 60, 80, 100),
    max_count: int = 10,
) -> list[dict[str, object]]:
    if not pool:
        return []
    full_target = sum(row.value for row in pool[:max_count])
    target = int(full_target * float(target_fraction))
    rows: list[dict[str, object]] = []
    for size in sizes:
        subset = list(pool[: int(size)])
        stats = enumerate_exact_value_combos(subset, target, max_count=max_count, tolerance=0)
        rows.append(
            {
                "pool_size": len(subset),
                "target_value": target,
                "max_count": max_count,
                "nodes_visited": stats.nodes_visited,
                "elapsed_ms": stats.elapsed_ms,
                "candidate_count": len(stats.candidates),
                "unique_count_cells": len(stats.unique_count_cells),
            }
        )
    return rows


__all__ = (
    "TierComboCandidate",
    "TierEnumerationStats",
    "TierPoolItem",
    "benchmark_pool_scaling",
    "enumerate_avg_value_combos",
    "enumerate_exact_value_combos",
    "filter_candidates_by_presolve_cells",
    "load_droppable_items_from_processed",
    "load_maps_summary",
    "quality_pool_catalog",
    "quality_pool_for_map",
    "resolve_tables_dir",
)
