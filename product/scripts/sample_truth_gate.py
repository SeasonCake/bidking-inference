"""Strict, reusable settlement-truth gate for Fatbeans captures.

The archive classifiers historically checked only a subset of the game's exact facts.  This module
keeps one explicit source -> transform -> truth mapping for sample ingestion:

* public numeric facts and complete bucket outlines;
* Aisha scan actions, Ahmed/Victor/Raven skills, and Maria's low-tier total value;
* count/cells/value-sum/average siblings, including white+green merged buckets;
* item-level revealed evidence as a physical subset of the final settlement.

Missing optional facts are reported as absent, not invented.  Every fact that is present must match
the final settlement or the sample is unsuitable as calibration truth.
"""
from __future__ import annotations

import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
for _sub in ("src", "scripts", "hero_ref/src"):
    _path = str(ROOT / _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from ahmad_ref_engine import (  # noqa: E402
    _avg_matches_exact_grid,
    _quality_count_product_tolerance_match,
)
from bidking_lab.live.fatbeans import _observed_item_cells  # noqa: E402
from bidking_lab.live.monitor import load_monitor_tables  # noqa: E402
from summarize_fatbeans_sample_manifest import (  # noqa: E402
    _observed_settlement_consistency,
)


PUBLIC_EXACT_CELLS = {200010: (4,), 200011: (5,), 200012: (6,)}
PUBLIC_EXACT_COUNTS = {200018: (4,), 200019: (5,), 200020: (6,)}
PUBLIC_AVG_CELLS = {
    200013: (4,),
    200014: None,  # all collectibles
    200015: (5,),
    200016: (6,),
}
PUBLIC_AVG_VALUES = {
    200035: None,
    200036: (4,),
    200037: (5,),
    200038: (6,),
}
PUBLIC_BUCKET_OUTLINES = {200001: (4,), 200002: (5,), 200003: (6,)}
PUBLIC_ALL_QUALITY_SCANS = frozenset({200004, 200030})

ACTION_TOTAL_CELLS = {
    100104: (1, 2),
    100105: (3,),
    100106: (4,),
    100107: (5,),
    100108: (6,),
}
ACTION_AVG_CELLS = {
    100110: (1, 2),
    100111: (3,),
    100112: (4,),
    100113: (5,),
    100114: (6,),
}
ACTION_VALUE_SUM = {
    100122: (2,),
    100123: (3,),
    100124: (4,),
    100125: (5,),
    100126: (6,),
}
ACTION_COUNTS = {
    100116: (2,),
    100117: (3,),
    100118: (4,),
    100119: (5,),
    100120: (6,),
}
ACTION_SIZE_AVG_VALUE = {100169: 1, 100170: 2, 100171: 3, 100172: 4, 100173: 6}
FULL_OUTLINE_ACTION_IDS = frozenset({100100})
FULL_OUTLINE_SKILL_IDS = frozenset({1002085})
HIGHEST_QUALITY_ACTION_IDS = frozenset({100162, 100163})
HIGHEST_QUALITY_SKILL_IDS = frozenset({100110})

AHMED_AVG_CELLS = {1002041: (5,), 1002042: (4,), 1002043: (3,)}
AISHA_EXACT_OUTLINES = {1001034: (1,), 1001033: (2,), 1001032: (3,), 1001031: (4,)}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _item_identity(item: Any, index: int) -> tuple[Any, ...]:
    runtime_id = getattr(item, "runtime_id", None)
    if runtime_id is not None:
        return ("runtime", runtime_id)
    local_index = getattr(item, "local_index", None)
    if local_index is not None:
        return ("local", local_index)
    row = getattr(item, "row", None)
    col = getattr(item, "col", None)
    if row is not None and col is not None:
        return ("position", row, col)
    return ("anonymous", index)


def _dedup_observed(items: Iterable[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[tuple[Any, ...]] = set()
    for index, item in enumerate(items):
        identity = _item_identity(item, index)
        if identity in seen:
            continue
        seen.add(identity)
        out.append(item)
    return out


def build_settlement_truth(events: Any, *, tables: Any | None = None) -> dict[str, Any]:
    """Return raw (not long-tail-normalized) settlement truth and recovery diagnostics."""
    tables = tables or load_monitor_tables()
    state = next(
        (row for row in reversed(events.states) if getattr(row, "inventory_items", None)),
        None,
    )
    if state is None:
        return {}

    counts: Counter[int] = Counter()
    cells: Counter[int] = Counter()
    values: Counter[int] = Counter()
    unknown_items: list[int] = []
    invalid_cells: list[int] = []
    collectible_ids: list[int] = []
    item_facts: list[dict[str, int]] = []
    for inv in state.inventory_items:
        item_id = int(getattr(inv, "item_id", 0) or 0)
        catalog = tables.items.get(item_id)
        quality = int(getattr(inv, "quality", 0) or 0)
        if quality <= 0 and catalog is not None:
            quality = int(getattr(catalog, "quality", 0) or 0)
        if quality <= 0:
            # Known q0 rows are titles/avatars/non-collectibles.  An unknown id cannot be proven q0.
            if catalog is None:
                unknown_items.append(item_id)
            continue
        if catalog is None:
            unknown_items.append(item_id)
            continue
        cell_count = int(getattr(inv, "cells", 0) or 0)
        if cell_count <= 0:
            cell_count = max(0, int(getattr(catalog, "shape_w", 0) or 0)) * max(
                0, int(getattr(catalog, "shape_h", 0) or 0)
            )
        if cell_count <= 0:
            invalid_cells.append(item_id)
        value = int(getattr(catalog, "value", 0) or 0)
        counts[quality] += 1
        cells[quality] += cell_count
        values[quality] += value
        collectible_ids.append(item_id)
        item_facts.append(
            {
                "item_id": item_id,
                "quality": quality,
                "cells": cell_count,
                "value": value,
            }
        )

    cell_size_counts: Counter[int] = Counter(row["cells"] for row in item_facts)
    cell_size_values: Counter[int] = Counter()
    for row in item_facts:
        cell_size_values[row["cells"]] += row["value"]

    return {
        "raw_item_count": len(state.inventory_items),
        "collectible_count": sum(counts.values()),
        "total_cells": sum(cells.values()),
        "total_value": sum(values.values()),
        "counts": dict(counts),
        "cells": dict(cells),
        "values": dict(values),
        "unknown_items": sorted(unknown_items),
        "invalid_cell_items": sorted(invalid_cells),
        "item_ids": sorted(collectible_ids),
        "item_facts": item_facts,
        "cell_size_counts": dict(cell_size_counts),
        "cell_size_values": dict(cell_size_values),
        "max_quality": max((row["quality"] for row in item_facts), default=0),
        "max_item_value": max((row["value"] for row in item_facts), default=0),
        "max_item_cells": max((row["cells"] for row in item_facts), default=0),
    }


def _truth_total(truth: dict[str, Any], metric: str, qualities: tuple[int, ...] | None) -> int:
    if metric == "count":
        if qualities is None:
            return int(truth["collectible_count"])
        return sum(int((truth["counts"] or {}).get(q, 0)) for q in qualities)
    if metric == "cells":
        if qualities is None:
            return int(truth["total_cells"])
        return sum(int((truth["cells"] or {}).get(q, 0)) for q in qualities)
    if metric == "value_sum":
        if qualities is None:
            return int(truth["total_value"])
        return sum(int((truth["values"] or {}).get(q, 0)) for q in qualities)
    raise ValueError(f"unsupported truth metric: {metric}")


def _claim(
    *,
    source: str,
    metric: str,
    value: Any,
    qualities: tuple[int, ...] | None,
    round_index: int | None,
    cell_size: int | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "metric": metric,
        "value": value,
        "qualities": list(qualities) if qualities is not None else None,
        "round": round_index,
        "cell_size": cell_size,
    }


def extract_exact_claims(events: Any, *, tables: Any | None = None) -> list[dict[str, Any]]:
    tables = tables or load_monitor_tables()
    claims: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple[int, ...] | None, int | None, str]] = set()

    def add(
        source: str,
        metric: str,
        value: Any,
        qualities: tuple[int, ...] | None,
        round_index: int | None,
        cell_size: int | None = None,
    ) -> None:
        key = (source, metric, qualities, cell_size, repr(value))
        if key in seen:
            return
        seen.add(key)
        claims.append(
            _claim(
                source=source,
                metric=metric,
                value=value,
                qualities=qualities,
                round_index=round_index,
                cell_size=cell_size,
            )
        )

    def add_outline_totals(source: str, items: Iterable[Any], round_no: int | None) -> None:
        observed = _dedup_observed(items)
        if not observed:
            return
        add(source, "count", len(observed), None, round_no)
        observed_cells = [_observed_item_cells(item) for item in observed]
        if all(value is not None for value in observed_cells):
            add(source, "cells", sum(int(value or 0) for value in observed_cells), None, round_no)

    def add_global_item_claim(
        source: str,
        metric: str,
        items: Iterable[Any],
        round_no: int | None,
    ) -> None:
        observed = _dedup_observed(items)
        values: list[int] = []
        for item in observed:
            item_id = int(getattr(item, "item_id", 0) or 0)
            catalog = tables.items.get(item_id)
            if metric == "max_quality":
                value = int(getattr(item, "quality", 0) or 0)
                if value <= 0 and catalog is not None:
                    value = int(getattr(catalog, "quality", 0) or 0)
            elif metric == "max_item_value":
                value = int(getattr(item, "value", 0) or 0)
                if value <= 0 and catalog is not None:
                    value = int(getattr(catalog, "value", 0) or 0)
            elif metric == "max_item_cells":
                parsed = _observed_item_cells(item)
                if parsed is None and catalog is not None:
                    width = int(getattr(catalog, "shape_w", 0) or 0)
                    height = int(getattr(catalog, "shape_h", 0) or 0)
                    parsed = width * height if width > 0 and height > 0 else None
                value = int(parsed or 0)
            else:  # pragma: no cover - call sites use the closed metric set above
                raise ValueError(metric)
            if value > 0:
                values.append(value)
        if values:
            add(source, metric, max(values), None, round_no)

    for state in events.states:
        round_index = getattr(state, "round_index", None)
        round_no = int(round_index) if round_index is not None else None
        for info in getattr(state, "public_infos", ()) or ():
            info_id = int(getattr(info, "info_id", 0) or 0)
            source = f"public_info:{info_id}"
            if info_id == 200009:
                add(source, "cells", info.value, None, round_no)
            elif info_id == 200017:
                add(source, "count", info.value, None, round_no)
            elif info_id in PUBLIC_EXACT_CELLS:
                add(source, "cells", info.value, PUBLIC_EXACT_CELLS[info_id], round_no)
            elif info_id in PUBLIC_EXACT_COUNTS:
                add(source, "count", info.value, PUBLIC_EXACT_COUNTS[info_id], round_no)
            elif info_id in PUBLIC_AVG_CELLS:
                add(source, "avg_cells", info.value, PUBLIC_AVG_CELLS[info_id], round_no)
            elif info_id in PUBLIC_AVG_VALUES:
                add(source, "avg_value", info.value, PUBLIC_AVG_VALUES[info_id], round_no)

            if info_id in PUBLIC_BUCKET_OUTLINES:
                observed = _dedup_observed(getattr(info, "observed_items", ()) or ())
                add(source + ":outline", "count", len(observed), PUBLIC_BUCKET_OUTLINES[info_id], round_no)
                observed_cells = [_observed_item_cells(item) for item in observed]
                if all(value is not None for value in observed_cells):
                    add(
                        source + ":outline",
                        "cells",
                        sum(int(value or 0) for value in observed_cells),
                        PUBLIC_BUCKET_OUTLINES[info_id],
                        round_no,
                    )
            if info_id in PUBLIC_ALL_QUALITY_SCANS:
                observed = _dedup_observed(getattr(info, "observed_items", ()) or ())
                add(source + ":all_quality", "count", len(observed), None, round_no)
                by_quality: Counter[int] = Counter(
                    int(getattr(item, "quality", 0) or 0) for item in observed
                )
                for quality in range(1, 7):
                    add(
                        source + ":all_quality",
                        "count",
                        by_quality.get(quality, 0),
                        (quality,),
                        round_no,
                    )
            if info_id == 200048:
                add_global_item_claim(source, "max_quality", info.observed_items, round_no)
            elif info_id == 200049:
                add_global_item_claim(source, "max_item_value", info.observed_items, round_no)
            elif info_id == 200050:
                add_global_item_claim(source, "max_item_cells", info.observed_items, round_no)

        for result in getattr(state, "action_results", ()) or ():
            action_id = int(getattr(result, "action_id", 0) or 0)
            value = getattr(result, "result", None)
            source = f"action:{action_id}"
            if action_id in FULL_OUTLINE_ACTION_IDS:
                add_outline_totals(source + ":full_outline", result.observed_items, round_no)
            if action_id in HIGHEST_QUALITY_ACTION_IDS:
                add_global_item_claim(source, "max_quality", result.observed_items, round_no)
            if value is None:
                continue
            if action_id == 100103:
                add(source, "cells", value, None, round_no)
            elif action_id == 100115:
                add(source, "count", value, None, round_no)
            elif action_id in ACTION_TOTAL_CELLS:
                add(source, "cells", value, ACTION_TOTAL_CELLS[action_id], round_no)
            elif action_id in ACTION_AVG_CELLS:
                add(source, "avg_cells", value, ACTION_AVG_CELLS[action_id], round_no)
            elif action_id in ACTION_VALUE_SUM:
                add(source, "value_sum", value, ACTION_VALUE_SUM[action_id], round_no)
            elif action_id in ACTION_COUNTS:
                add(source, "count", value, ACTION_COUNTS[action_id], round_no)
            elif action_id in ACTION_SIZE_AVG_VALUE:
                add(
                    source,
                    "avg_value_by_cells",
                    value,
                    None,
                    round_no,
                    cell_size=ACTION_SIZE_AVG_VALUE[action_id],
                )

        for reveal in getattr(state, "skill_reveals", ()) or ():
            skill_id = int(getattr(reveal, "skill_id", 0) or 0)
            result = getattr(reveal, "result", None)
            source = f"skill:{skill_id}"
            if skill_id == 100204 and result is not None:
                add(source, "count", result, None, round_no)
            elif skill_id in AHMED_AVG_CELLS and result is not None:
                add(source, "avg_cells", result, AHMED_AVG_CELLS[skill_id], round_no)
            elif skill_id == 1002044 and result is not None:
                add(source, "count", result, (1, 2), round_no)
            elif skill_id == 100209 and result is not None:
                add(source, "count", result, (4, 5, 6), round_no)
            elif skill_id == 100108 and result is not None:
                # Maria's skill is a single low-tier (white+green+blue) total, not q1 alone.
                add(source, "value_sum", result, (1, 2, 3), round_no)

            if skill_id in AISHA_EXACT_OUTLINES:
                observed = _dedup_observed(getattr(reveal, "observed_items", ()) or ())
                qualities = AISHA_EXACT_OUTLINES[skill_id]
                add(source + ":outline", "count", len(observed), qualities, round_no)
                observed_cells = [_observed_item_cells(item) for item in observed]
                if all(value is not None for value in observed_cells):
                    add(
                        source + ":outline",
                        "cells",
                        sum(int(value or 0) for value in observed_cells),
                        qualities,
                        round_no,
                    )
            if skill_id == 100301:
                observed = _dedup_observed(getattr(reveal, "observed_items", ()) or ())
                add(source + ":all_quality", "count", len(observed), None, round_no)
                by_quality: Counter[int] = Counter(
                    int(getattr(item, "quality", 0) or 0) for item in observed
                )
                for quality in range(1, 7):
                    add(
                        source + ":all_quality",
                        "count",
                        by_quality.get(quality, 0),
                        (quality,),
                        round_no,
                    )
            if skill_id in FULL_OUTLINE_SKILL_IDS:
                add_outline_totals(source + ":full_outline", reveal.observed_items, round_no)
            if skill_id in HIGHEST_QUALITY_SKILL_IDS:
                add_global_item_claim(source, "max_quality", reveal.observed_items, round_no)
    return claims


def compare_claim(claim: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    metric = str(claim["metric"])
    qualities_raw = claim.get("qualities")
    qualities = tuple(int(value) for value in qualities_raw) if qualities_raw is not None else None
    declared = _number(claim.get("value"))
    result = dict(claim)
    if declared is None:
        return {**result, "matched": False, "reason": "invalid_numeric_claim", "truth": None}

    if metric in {"count", "cells", "value_sum"}:
        expected = _truth_total(truth, metric, qualities)
        candidates = [expected]
        # Some protocol eras counted q0 titles/avatars in the all-item declaration.  Preserve the
        # established raw-vs-collectible tolerance only for total item count, never per-quality.
        if metric == "count" and qualities is None:
            candidates.append(int(truth["raw_item_count"]))
        matched = any(abs(declared - value) <= 0.0001 for value in set(candidates))
        return {
            **result,
            "matched": matched,
            "truth": sorted(set(candidates)) if len(set(candidates)) > 1 else expected,
            "reason": None if matched else f"{metric}_mismatch",
        }

    if metric == "avg_value_by_cells":
        cell_size = int(claim.get("cell_size") or 0)
        count = int((truth.get("cell_size_counts") or {}).get(cell_size, 0))
        value_sum = int((truth.get("cell_size_values") or {}).get(cell_size, 0))
        if count == 0:
            matched = abs(declared) <= 0.0001 and value_sum == 0
        else:
            tolerance = max(0.5, 0.005 * count + 0.001)
            matched = _quality_count_product_tolerance_match(
                count,
                declared,
                float(value_sum),
                tolerance=tolerance,
            ) or abs(declared * count - value_sum) <= tolerance
        return {
            **result,
            "matched": bool(matched),
            "truth": (float(value_sum) / count) if count else 0.0,
            "truth_count": count,
            "truth_value_sum": value_sum,
            "reason": None if matched else "avg_value_by_cells_mismatch",
        }

    if metric in {"max_quality", "max_item_value", "max_item_cells"}:
        expected = int(truth.get(metric) or 0)
        matched = abs(declared - expected) <= 0.0001
        return {
            **result,
            "matched": matched,
            "truth": expected,
            "reason": None if matched else f"{metric}_mismatch",
        }

    count = _truth_total(truth, "count", qualities)
    if metric == "avg_cells":
        cells = _truth_total(truth, "cells", qualities)
        matched = _avg_matches_exact_grid(count, declared, cells)
        return {
            **result,
            "matched": bool(matched),
            "truth": (float(cells) / count) if count else 0.0,
            "truth_count": count,
            "truth_cells": cells,
            "reason": None if matched else "avg_cells_mismatch",
        }
    if metric == "avg_value":
        value_sum = _truth_total(truth, "value_sum", qualities)
        if count == 0:
            matched = abs(declared) <= 0.0001 and value_sum == 0
        else:
            tolerance = max(0.5, 0.005 * count + 0.001)
            matched = _quality_count_product_tolerance_match(
                count,
                declared,
                float(value_sum),
                tolerance=tolerance,
            ) or abs(declared * count - value_sum) <= tolerance
        return {
            **result,
            "matched": bool(matched),
            "truth": (float(value_sum) / count) if count else 0.0,
            "truth_count": count,
            "truth_value_sum": value_sum,
            "reason": None if matched else "avg_value_mismatch",
        }
    return {**result, "matched": False, "truth": None, "reason": "unknown_metric"}


def _observation_bounds(events: Any) -> dict[str, int]:
    max_items = 0
    max_cells = 0
    for state in events.states:
        rows: list[Iterable[Any]] = []
        rows.extend(getattr(info, "observed_items", ()) or () for info in getattr(state, "public_infos", ()) or ())
        rows.extend(getattr(row, "observed_items", ()) or () for row in getattr(state, "action_results", ()) or ())
        rows.extend(getattr(row, "observed_items", ()) or () for row in getattr(state, "skill_reveals", ()) or ())
        for items in rows:
            observed = _dedup_observed(items)
            max_items = max(max_items, len(observed))
            row_cells = [_observed_item_cells(item) for item in observed]
            if all(value is not None for value in row_cells):
                max_cells = max(max_cells, sum(int(value or 0) for value in row_cells))
    return {"max_observed_items_in_one_fact": max_items, "max_observed_cells_in_one_fact": max_cells}


def _revealed_lower_bounds(events: Any, *, tables: Any) -> dict[str, Any]:
    """Union distinct revealed entities into per-quality physical lower bounds.

    A random/tool/skill reveal is not automatically a complete warehouse declaration.  It is still
    hard evidence that every distinct revealed entity must exist in settlement.  Runtime id is the
    preferred identity; local index and position are stable fallbacks.  Truly anonymous rows are
    scoped to their source frame so repeated frames cannot silently inflate the bound.
    """
    records: list[tuple[str, Any, Any]] = []
    parents: list[int] = []
    alias_owner: dict[tuple[Any, ...], int] = {}
    sources: Counter[str] = Counter()

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    def aliases_for(item: Any, *, source: str, sort_id: Any, index: int) -> list[tuple[Any, ...]]:
        aliases: list[tuple[Any, ...]] = []
        runtime_id = getattr(item, "runtime_id", None)
        local_index = getattr(item, "local_index", None)
        if runtime_id is not None:
            aliases.append(("runtime", runtime_id))
        if local_index is not None:
            aliases.append(("local", local_index))
        if not aliases:
            row = getattr(item, "row", None)
            col = getattr(item, "col", None)
            if row is not None and col is not None:
                aliases.append(("position", row, col))
        if not aliases:
            aliases.append(("anonymous", source, sort_id, index))
        return aliases

    def add_items(source: str, sort_id: Any, items: Iterable[Any]) -> None:
        for index, item in enumerate(items):
            record_index = len(records)
            records.append((source, item, aliases_for(item, source=source, sort_id=sort_id, index=index)))
            parents.append(record_index)
            sources[source] += 1
            for alias in records[-1][2]:
                previous = alias_owner.get(alias)
                if previous is None:
                    alias_owner[alias] = record_index
                else:
                    union(record_index, previous)

    for state in events.states:
        sort_id = getattr(state, "sort_id", None)
        for info in getattr(state, "public_infos", ()) or ():
            add_items(
                f"public_info:{int(getattr(info, 'info_id', 0) or 0)}",
                sort_id,
                getattr(info, "observed_items", ()) or (),
            )
        for result in getattr(state, "action_results", ()) or ():
            add_items(
                f"action:{int(getattr(result, 'action_id', 0) or 0)}",
                sort_id,
                getattr(result, "observed_items", ()) or (),
            )
        for reveal in getattr(state, "skill_reveals", ()) or ():
            add_items(
                f"skill:{int(getattr(reveal, 'skill_id', 0) or 0)}",
                sort_id,
                getattr(reveal, "observed_items", ()) or (),
            )
    groups: dict[int, list[tuple[str, Any, list[tuple[Any, ...]]]]] = {}
    for index, record in enumerate(records):
        groups.setdefault(find(index), []).append(record)

    counts: Counter[int] = Counter()
    cells: Counter[int] = Counter()
    values: Counter[int] = Counter()
    conflicts: list[dict[str, Any]] = []
    quality_known_entities = 0
    for component in groups.values():
        quality_values: set[int] = set()
        item_ids: set[int] = set()
        cell_values: set[int] = set()
        catalog_values: set[int] = set()
        aliases = sorted({repr(alias) for _source, _item, row_aliases in component for alias in row_aliases})
        for _source, item, _aliases in component:
            item_id = int(getattr(item, "item_id", 0) or 0)
            catalog = tables.items.get(item_id)
            quality = int(getattr(item, "quality", 0) or 0)
            if quality <= 0 and catalog is not None:
                quality = int(getattr(catalog, "quality", 0) or 0)
            if quality > 0:
                quality_values.add(quality)
            if item_id > 0:
                item_ids.add(item_id)
            item_cells = _observed_item_cells(item)
            if item_cells is None and catalog is not None:
                width = int(getattr(catalog, "shape_w", 0) or 0)
                height = int(getattr(catalog, "shape_h", 0) or 0)
                item_cells = width * height if width > 0 and height > 0 else None
            if item_cells is not None and int(item_cells) > 0:
                cell_values.add(int(item_cells))
            if catalog is not None and int(getattr(catalog, "value", 0) or 0) > 0:
                catalog_values.add(int(catalog.value))
        conflict_fields = []
        if len(quality_values) > 1:
            conflict_fields.append("quality")
        if len(item_ids) > 1:
            conflict_fields.append("item_id")
        if len(cell_values) > 1:
            conflict_fields.append("cells")
        if len(catalog_values) > 1:
            conflict_fields.append("value")
        if conflict_fields:
            conflicts.append(
                {
                    "fields": conflict_fields,
                    "aliases": aliases[:8],
                    "qualities": sorted(quality_values),
                    "item_ids": sorted(item_ids),
                    "cells": sorted(cell_values),
                    "values": sorted(catalog_values),
                }
            )
        if not quality_values:
            continue
        quality = min(quality_values)
        quality_known_entities += 1
        counts[quality] += 1
        if cell_values:
            cells[quality] += min(cell_values)
        if catalog_values:
            values[quality] += min(catalog_values)

    return {
        "counts": dict(counts),
        "cells": dict(cells),
        "values": dict(values),
        "distinct_entities": len(groups),
        "quality_known_entities": quality_known_entities,
        "sources": dict(sources),
        "identity_conflicts": conflicts,
    }


def _auxiliary_public_facts(events: Any) -> list[dict[str, Any]]:
    exact_ids = (
        set(PUBLIC_EXACT_CELLS)
        | set(PUBLIC_EXACT_COUNTS)
        | set(PUBLIC_AVG_CELLS)
        | set(PUBLIC_AVG_VALUES)
        | set(PUBLIC_BUCKET_OUTLINES)
        | set(PUBLIC_ALL_QUALITY_SCANS)
        | {200009, 200017, 200048, 200049, 200050}
    )
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, str, int]] = set()
    for state in events.states:
        round_index = getattr(state, "round_index", None)
        for info in getattr(state, "public_infos", ()) or ():
            info_id = int(getattr(info, "info_id", 0) or 0)
            if info_id in exact_ids:
                continue
            observed = _dedup_observed(getattr(info, "observed_items", ()) or ())
            key = (info_id, repr(getattr(info, "value", None)), len(observed))
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "info_id": info_id,
                    "value": getattr(info, "value", None),
                    "observed_items": len(observed),
                    "observed_quality_counts": dict(
                        Counter(
                            int(getattr(item, "quality", 0) or 0)
                            for item in observed
                            if int(getattr(item, "quality", 0) or 0) > 0
                        )
                    ),
                    "round": int(round_index) if round_index is not None else None,
                    "role": "auxiliary_lower_bound_or_diagnostic",
                }
            )
    return rows


def _revealed_lower_bound_mismatches(
    lower_bounds: dict[str, Any], truth: dict[str, Any]
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for quality in range(1, 7):
        for metric in ("counts", "cells", "values"):
            lower = int((lower_bounds.get(metric) or {}).get(quality, 0))
            settled = int((truth.get(metric) or {}).get(quality, 0))
            if lower <= settled:
                continue
            kind = {"counts": "count", "cells": "cells", "values": "value_sum"}[metric]
            mismatches.append(
                {
                    "quality": quality,
                    "metric": kind,
                    "revealed_lower_bound": lower,
                    "settlement": settled,
                    "reason": f"revealed_q{quality}_{kind}_lower_bound_exceeds_settlement",
                }
            )
    return mismatches


def validate_events(events: Any, *, tables: Any | None = None) -> dict[str, Any]:
    tables = tables or load_monitor_tables()
    truth = build_settlement_truth(events, tables=tables)
    if not truth:
        return {
            "verdict": "INVALID",
            "reason": "no_settlement_truth",
            "reasons": ["no_settlement_truth"],
            "truth": {},
            "claims_present": 0,
            "claims_matched": 0,
            "claims": [],
            "mismatches": [],
        }
    structural_reasons: list[str] = []
    if truth["collectible_count"] <= 0 or truth["total_cells"] <= 0 or truth["total_value"] <= 0:
        structural_reasons.append("abnormal_settlement_totals")
    if truth["unknown_items"]:
        structural_reasons.append("unknown_catalog_items")
    if truth["invalid_cell_items"]:
        structural_reasons.append("nonpositive_collectible_cells")

    observed = _observed_settlement_consistency(events)
    if int(observed.get("observed_settlement_missing_items") or 0) > 0:
        structural_reasons.append("revealed_item_missing_from_settlement")
    if int(observed.get("observed_settlement_quality_mismatch_items") or 0) > 0:
        structural_reasons.append("revealed_quality_mismatch")
    bounds = _observation_bounds(events)
    if bounds["max_observed_items_in_one_fact"] > truth["collectible_count"]:
        structural_reasons.append("revealed_count_exceeds_settlement")
    if bounds["max_observed_cells_in_one_fact"] > truth["total_cells"]:
        structural_reasons.append("revealed_cells_exceed_settlement")

    lower_bounds = _revealed_lower_bounds(events, tables=tables)
    if lower_bounds.get("identity_conflicts"):
        structural_reasons.append("revealed_identity_conflict")
    lower_bound_mismatches = _revealed_lower_bound_mismatches(lower_bounds, truth)
    structural_reasons.extend(row["reason"] for row in lower_bound_mismatches)

    claims = [compare_claim(claim, truth) for claim in extract_exact_claims(events, tables=tables)]
    mismatches = [claim for claim in claims if not claim["matched"]]
    reasons = list(dict.fromkeys(structural_reasons + [str(row["reason"]) for row in mismatches]))
    verdict = "valid" if not reasons else "INVALID"
    return {
        "verdict": verdict,
        "reason": reasons[0] if reasons else None,
        "reasons": reasons,
        "truth": truth,
        "truth_signature": {
            "raw_item_count": truth["raw_item_count"],
            "collectible_count": truth["collectible_count"],
            "total_cells": truth["total_cells"],
            "total_value": truth["total_value"],
            "counts": truth["counts"],
            "cells": truth["cells"],
            "values": truth["values"],
            "item_ids": truth["item_ids"],
        },
        "claims_present": len(claims),
        "claims_matched": len(claims) - len(mismatches),
        "claims": claims,
        "mismatches": mismatches,
        "observed_consistency": observed,
        "observation_bounds": bounds,
        "revealed_lower_bounds": lower_bounds,
        "revealed_lower_bound_mismatches": lower_bound_mismatches,
        "auxiliary_public_facts": _auxiliary_public_facts(events),
    }


__all__ = [
    "build_settlement_truth",
    "compare_claim",
    "extract_exact_claims",
    "validate_events",
]
