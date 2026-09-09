"""A tiny public-fact adapter and an extracted historical grid-range helper.

Source: docs/research/PROVENANCE.json, entry aisha-grid-feasibility.
The adapter is independently written teaching code, not the live parser.
"""

from __future__ import annotations

import math


def _apply_total_avg_cells_grid_feasibility(
    total_grid_range: tuple[int | None, int | None, int | None],
    notes: list[str],
    *,
    total_avg_cells: float | None,
    quality_count_ranges: dict[str, tuple[int | None, int | None, int | None]],
) -> tuple[int | None, int | None, int | None]:
    """Clamp the grid range into the public-总均格 feasible band [avg×count_lo, avg×count_hi].
    No-op if 总均格 absent, count range unusable, or the range already lies inside the band."""
    if not total_avg_cells or total_avg_cells <= 0:
        return total_grid_range
    lo_sum = hi_sum = 0
    saw = False
    for rng in quality_count_ranges.values():
        if not rng:
            continue
        c_lo, _c_mid, c_hi = rng
        if c_lo is not None:
            lo_sum += int(c_lo); saw = True
        if c_hi is not None:
            hi_sum += int(c_hi); saw = True
    if not saw or hi_sum <= 0 or lo_sum > hi_sum:
        return total_grid_range
    band_lo = int(round(total_avg_cells * lo_sum))
    band_hi = int(round(total_avg_cells * hi_sum))
    if band_lo > band_hi:
        band_lo, band_hi = band_hi, band_lo
    g_lo, g_mid, g_hi = total_grid_range

    def _clamp(v: int | None) -> int | None:
        if v is None:
            return None
        return min(max(int(v), band_lo), band_hi)

    new = (_clamp(g_lo), _clamp(g_mid), _clamp(g_hi))
    # keep monotonic lo<=mid<=hi after clamping
    vals = [x for x in new if x is not None]
    if vals and new != total_grid_range:
        lo, mid, hi = new
        if lo is not None and mid is not None and lo > mid:
            lo = mid
        if hi is not None and mid is not None and hi < mid:
            hi = mid
        new = (lo, mid, hi)
        notes.append(
            f"total_avg_cells_grid_feasibility_clamp:{band_lo}-{band_hi}"
            f":{total_grid_range}->{new}"
        )
        return new
    return total_grid_range


def normalize_public_facts(rows: list[dict]) -> dict:
    """NEW strict toy adapter; preserve missing vs invalid, including zero.

    Only total_avg_cells is supported. Numeric strings and booleans are not
    numbers here. Equal duplicates coalesce; contradictory duplicates reject
    the entire fact, so row order cannot silently choose a winner.
    """
    def invalid(reason: str) -> dict:
        return {"status": "invalid", "reason": reason, "public_numeric_facts": []}

    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return invalid("rows_must_be_objects")
    values: list[float] = []
    for row in rows:
        if row.get("semantic") != "total_avg_cells":
            continue
        if row.get("unit", "cells_per_item") != "cells_per_item":
            return invalid("wrong_unit")
        value = row.get("value")
        if type(value) not in (int, float):
            return invalid("not_a_number")
        try:
            value = float(value)
        except (OverflowError, ValueError):
            return invalid("not_finite_positive")
        if not math.isfinite(value) or value <= 0:
            return invalid("not_finite_positive")
        values.append(value)
    if not values:
        return {"status": "missing", "public_numeric_facts": []}
    if any(value != values[0] for value in values):
        return invalid("conflicting_values")
    return {
        "status": "present",
        "public_numeric_facts": [
            {"semantic": "total_avg_cells", "value": values[0], "unit": "cells_per_item"}
        ],
    }


def live_teaching_snapshot(rows: list[dict]) -> dict:
    return {"synthetic": True, "constraints": {"public_info": normalize_public_facts(rows)}}


def offline_teaching_snapshot(rows: list[dict], *, legacy_omission: bool = False) -> dict:
    """Demonstrate the old omitted channel without recreating its full parser."""
    if legacy_omission:
        public = {"status": "missing", "public_numeric_facts": []}
    else:
        public = normalize_public_facts(rows)
    return {"synthetic": True, "constraints": {"public_info": public}}


def _valid_range(value: object) -> bool:
    return (
        isinstance(value, (tuple, list))
        and len(value) == 3
        and all(type(x) is int and x >= 0 for x in value)
        and value[0] <= value[1] <= value[2]
    )


def evaluate_grid_example(
    snapshot: dict,
    grid_range: tuple[int, int, int],
    quality_count_ranges: dict[str, tuple[int, int, int]],
) -> dict:
    """NEW wrapper: reject invalid ranges/facts before historical arithmetic.

    No quote model is present. A changed cell range is not a measured quote gain.
    The legacy helper's rounding and partial-input behavior remain untouched;
    this wrapper intentionally accepts only complete, ordered toy ranges.
    """
    if not _valid_range(grid_range):
        raise ValueError("grid_range must contain ordered non-negative integer cells")
    original = tuple(grid_range)
    result = {
        "synthetic": True,
        "kind": "grid_feasibility_example",
        "unit": "cells",
        "before": list(original),
        "after": list(original),
        "changed": False,
        "quote_evaluated": False,
        "notes": [],
    }
    if (
        not isinstance(quality_count_ranges, dict)
        or not quality_count_ranges
        or any(not _valid_range(v) for v in quality_count_ranges.values())
        or sum(v[2] for v in quality_count_ranges.values()) <= 0
    ):
        return {**result, "status": "invalid_count_ranges"}
    if not isinstance(snapshot, dict) or snapshot.get("synthetic") is not True:
        raise ValueError("only explicitly synthetic teaching snapshots are accepted")
    constraints = snapshot.get("constraints")
    public = constraints.get("public_info") if isinstance(constraints, dict) else None
    if not isinstance(public, dict):
        return {**result, "status": "missing"}
    if public.get("status") == "invalid":
        return {**result, "status": "invalid"}
    checked = normalize_public_facts(public.get("public_numeric_facts", []))
    if checked["status"] != "present":
        return {**result, "status": checked["status"]}
    average = checked["public_numeric_facts"][0]["value"]
    try:
        band_end = average * sum(v[2] for v in quality_count_ranges.values())
    except OverflowError:
        band_end = math.inf
    if not math.isfinite(band_end):
        return {**result, "status": "invalid_product"}
    after = _apply_total_avg_cells_grid_feasibility(
        original, result["notes"], total_avg_cells=average,
        quality_count_ranges=quality_count_ranges,
    )
    return {**result, "status": "present", "after": list(after), "changed": after != original}
