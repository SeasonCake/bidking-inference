"""Per-footprint (1/2/3/4/6 cell) average-value evidence for v2.

Interprets size-avg tool readings (100169-100173) with optional exact counts from
full outline (100100 / Ethan 1002085) or shaped runtime evidence. Low readings stay
soft-only; high readings with reliable counts can apply value-sum floors on samples.

See docs/size_avg_interpretation.zh-CN.md for human-readable tier tables.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Sequence

import numpy as np

from bidking_lab.inference.ground_truth import BucketTruth, SessionTruth

EvidenceStrength = Literal["soft", "hard_floor"]

# Signal floors (tool noise gate) — settlement p10 tuned; see analyze_size_avg_value_thresholds.py
SIZE_AVG_VALUE_SIGNAL_FLOORS: dict[int, float] = {
    1: 2_000.0,
    2: 2_000.0,
    3: 3_000.0,
    4: 5_000.0,
    6: 4_000.0,
}
SIZE_AVG_VALUE_SIGNAL_FLOOR = min(SIZE_AVG_VALUE_SIGNAL_FLOORS.values())

# Warehouse bands from 333 settlement sessions (total occupied cells).
WAREHOUSE_SMALL_MAX = 94
WAREHOUSE_MID_MAX = 127

# Anchor unit prices (silver) for dilution checks — 羊脂白玉籽 is 2-cell, not 羊脂玉璧.
SIZE_AVG_ANCHORS: tuple[tuple[str, int, int], ...] = (
    ("black_prince", 1, 3_000_000),
    ("yangzhi_jade", 2, 2_516_000),
    ("renshen", 2, 1_039_000),
    ("plane_box", 4, 1_688_400),
    ("yongle", 4, 1_500_000),
    ("black_box", 4, 7_402_320),
)

# Below these avg readings we only soft-score (typical filler / diluted pools).
FILLER_MAX_AVG_BY_CELLS: dict[int, float] = {
    1: 25_000.0,
    2: 30_000.0,
    3: 30_000.0,
    4: 30_000.0,
    6: 50_000.0,
}

# Minimum avg before we consider hard value-sum floor (needs count_exact too).
HIGH_VALUE_FLOOR_MIN_AVG: dict[int, float] = {
    1: 150_000.0,
    2: 80_000.0,
    4: 80_000.0,
    6: 100_000.0,
}

# Typical 4-cell item counts by warehouse band (settlement p50).
TYPICAL_SIZE_COUNT_BY_BAND: dict[str, dict[int, int]] = {
    "small": {1: 19, 2: 5, 4: 7, 6: 2},
    "mid": {1: 19, 2: 7, 4: 10, 6: 2},
    "large": {1: 19, 2: 9, 4: 13, 6: 2},
}

_FULL_OUTLINE_SOURCES = ("action:100100", "skill:1002085")

# Tool action ids -> footprint cells (100169-100173).
SIZE_AVG_ACTION_FOOTPRINT: dict[int, int] = {
    100169: 1,
    100170: 2,
    100171: 3,
    100172: 4,
    100173: 6,
}
SIZE_AVG_ACTION_IDS = frozenset(SIZE_AVG_ACTION_FOOTPRINT)


@dataclass(frozen=True)
class SizeBucketEvidence:
    """Combined count + avg evidence for one footprint size."""

    cells: int
    avg_value: float
    count_exact: int | None = None
    count_partial: int | None = None
    value_floor: int | None = None
    strength: EvidenceStrength = "soft"
    anchor_hint: str | None = None
    tier: str | None = None
    warehouse_band: str | None = None


def warehouse_size_band(warehouse_total_cells: int | None) -> str | None:
    if warehouse_total_cells is None or warehouse_total_cells <= 0:
        return None
    cells = int(warehouse_total_cells)
    if cells <= WAREHOUSE_SMALL_MAX:
        return "small"
    if cells <= WAREHOUSE_MID_MAX:
        return "mid"
    return "large"


def actionable_size_avg_value_targets(
    values: Any,
    *,
    signal_floors: dict[int, float] | None = None,
) -> tuple[tuple[int, float], ...]:
    floors = signal_floors or SIZE_AVG_VALUE_SIGNAL_FLOORS
    return tuple(
        (int(cells), float(value))
        for cells, value in (values or ())
        if float(value) >= floors.get(int(cells), SIZE_AVG_VALUE_SIGNAL_FLOOR)
    )


def _revealed_counts_by_cells(store: Any) -> dict[int, int]:
    counts: Counter[int] = Counter()
    for evidence in store.items():
        if evidence.cells is None:
            continue
        counts[int(evidence.cells)] += 1
    return dict(counts)


def _has_full_outline_coverage(store: Any, total_item_count: int | None) -> bool:
    if total_item_count is None or total_item_count <= 0:
        return False
    shaped = 0
    outlined = 0
    for evidence in store.items():
        if evidence.cells is None:
            continue
        shaped += 1
        if any(source in _FULL_OUTLINE_SOURCES for source in evidence.sources):
            outlined += 1
    return shaped > 0 and outlined == shaped and shaped == int(total_item_count)


def size_bucket_counts_from_context(
    store: Any,
    *,
    total_item_count: int | None,
    layout_footprint_count: int = 0,
    trusted_footprint_count: int = 0,
) -> tuple[dict[int, int], bool]:
    """Return per-footprint counts and whether the partition is exact for the warehouse."""
    revealed = _revealed_counts_by_cells(store)
    if not revealed:
        return {}, False
    if _has_full_outline_coverage(store, total_item_count):
        return revealed, True
    if (
        total_item_count is not None
        and trusted_footprint_count > 0
        and trusted_footprint_count == int(total_item_count)
        and sum(revealed.values()) == int(total_item_count)
    ):
        return revealed, True
    if (
        layout_footprint_count > 0
        and sum(revealed.values()) == layout_footprint_count
        and (
            total_item_count is None
            or layout_footprint_count == int(total_item_count)
        )
    ):
        return revealed, True
    return revealed, False


def _classify_avg_tier(
    avg_value: float,
    cells: int,
    *,
    warehouse_band: str | None,
    count_exact: int | None,
) -> str:
    if avg_value < FILLER_MAX_AVG_BY_CELLS.get(cells, 30_000.0):
        return "filler"
    if cells == 4:
        if avg_value >= 650_000:
            return "black_box_likely"
        if avg_value >= 500_000:
            return "ultra_4cell"
        if avg_value >= 150_000:
            return "plane_yongle_singleton"
        if avg_value >= 80_000:
            return "rich_4cell"
        if avg_value >= 30_000 and warehouse_band == "small":
            return "possible_plane_diluted"
        return "elevated_4cell"
    if cells == 2:
        if avg_value >= 200_000:
            return "yangzhi_likely"
        if avg_value >= 80_000:
            return "renshen_or_yangzhi_diluted"
        return "elevated_2cell"
    if cells == 1:
        if avg_value >= 250_000:
            return "black_prince_likely"
        if avg_value >= 80_000:
            return "rich_1cell"
        return "elevated_1cell"
    if cells == 6 and avg_value >= 100_000:
        return "rich_6cell"
    return "elevated"


def _match_anchor_hint(
    avg_value: float,
    cells: int,
    count_exact: int | None,
) -> str | None:
    if count_exact is None or count_exact <= 0:
        return None
    implied_total = avg_value * count_exact
    best_hint: str | None = None
    best_err = 1.0
    for hint, anchor_cells, price in SIZE_AVG_ANCHORS:
        if anchor_cells != cells:
            continue
        err = abs(implied_total - price) / max(1.0, float(price))
        if err < best_err:
            best_err = err
            best_hint = hint
    if best_hint is not None and best_err <= 0.35:
        return best_hint
    return None


def _should_apply_value_floor(
    *,
    avg_value: float,
    cells: int,
    count_exact: int | None,
    anchor_hint: str | None,
    tier: str,
) -> bool:
    if count_exact is None or count_exact <= 0:
        return False
    if tier == "filler":
        return False
    min_avg = HIGH_VALUE_FLOOR_MIN_AVG.get(cells)
    if min_avg is not None and avg_value < min_avg:
        return False
    if anchor_hint is not None:
        return True
    if cells == 4:
        if avg_value >= 500_000:
            return count_exact <= 6
        if avg_value >= 150_000:
            return count_exact <= 4
    if cells == 2 and avg_value >= 200_000:
        return count_exact <= 4
    if cells == 1 and avg_value >= 250_000:
        return count_exact <= 3
    return False


def _value_floor_for_target(
    *,
    avg_value: float,
    count_exact: int,
    anchor_hint: str | None,
    cells: int,
) -> int:
    base = int(avg_value * count_exact * 0.85)
    if anchor_hint is not None:
        for hint, anchor_cells, price in SIZE_AVG_ANCHORS:
            if hint == anchor_hint and anchor_cells == cells:
                return max(base, int(price * 0.70))
    return base


def build_size_bucket_evidence(
    size_avg_value_targets: tuple[tuple[int, float], ...],
    *,
    store: Any,
    warehouse_total_cells: int | None = None,
    total_item_count: int | None = None,
    layout_footprint_count: int = 0,
    trusted_footprint_count: int = 0,
) -> tuple[SizeBucketEvidence, ...]:
    counts, counts_exact = size_bucket_counts_from_context(
        store,
        total_item_count=total_item_count,
        layout_footprint_count=layout_footprint_count,
        trusted_footprint_count=trusted_footprint_count,
    )
    band = warehouse_size_band(warehouse_total_cells)
    targets: list[SizeBucketEvidence] = []
    for cells, avg_value in size_avg_value_targets:
        count_exact = counts.get(cells) if counts_exact else None
        count_partial = counts.get(cells) if not counts_exact else None
        anchor_hint = _match_anchor_hint(avg_value, cells, count_exact)
        tier = _classify_avg_tier(
            avg_value,
            cells,
            warehouse_band=band,
            count_exact=count_exact,
        )
        strength: EvidenceStrength = "soft"
        value_floor: int | None = None
        if _should_apply_value_floor(
            avg_value=avg_value,
            cells=cells,
            count_exact=count_exact,
            anchor_hint=anchor_hint,
            tier=tier,
        ):
            assert count_exact is not None
            value_floor = _value_floor_for_target(
                avg_value=avg_value,
                count_exact=count_exact,
                anchor_hint=anchor_hint,
                cells=cells,
            )
            strength = "hard_floor"
        targets.append(
            SizeBucketEvidence(
                cells=cells,
                avg_value=avg_value,
                count_exact=count_exact,
                count_partial=count_partial,
                value_floor=value_floor,
                strength=strength,
                anchor_hint=anchor_hint,
                tier=tier,
                warehouse_band=band,
            )
        )
    return tuple(targets)


def footprint_count_in_buckets(buckets: Any, cells: int) -> int:
    total = 0
    for bucket in buckets.values():
        for item in bucket.items:
            if item.shape_w * item.shape_h == cells:
                total += 1
    return total


def residual_allowed_for_footprint(
    item: Any,
    buckets: Any,
    targets: tuple[SizeBucketEvidence, ...],
    *,
    add_count: int = 1,
) -> bool:
    area = item.shape_w * item.shape_h
    for target in targets:
        if target.cells != area or target.count_exact is None:
            continue
        current = footprint_count_in_buckets(buckets, area)
        if current + max(0, int(add_count)) > target.count_exact:
            return False
    return True


def prefill_size_bucket_targets(
    pool: Any,
    buckets: dict,
    targets: tuple[SizeBucketEvidence, ...],
    *,
    rng: Any,
    item_allowed: Any,
    add_item: Callable[[dict, Any, int], None],
    max_attempts: int = 400,
) -> None:
    """Place exact-count footprint items before residual sampling (experimental)."""

    for target in targets:
        if target.count_exact is None or target.count_exact <= 0:
            continue
        need = int(target.count_exact) - footprint_count_in_buckets(buckets, target.cells)
        if need <= 0:
            continue
        indexes = [
            idx
            for idx, item in enumerate(pool.items)
            if item.shape_w * item.shape_h == target.cells
            and item_allowed(item)
        ]
        if not indexes:
            continue
        min_sum = int(target.value_floor or target.avg_value * target.count_exact * 0.85)
        placed = False
        for _ in range(max_attempts):
            pick = rng.choice(indexes, size=min(need, len(indexes)), replace=len(indexes) < need)
            chosen = [int(i) for i in pick]
            trial_sum = sum(int(pool.items[i].value) for i in chosen)
            if trial_sum >= min_sum or min_sum <= 0:
                for pool_i in chosen:
                    item = pool.items[pool_i]
                    if residual_allowed_for_footprint(
                        item,
                        buckets,
                        targets,
                        add_count=1,
                    ):
                        add_item(buckets, item)
                placed = footprint_count_in_buckets(buckets, target.cells) >= target.count_exact
                if placed:
                    break
            probs = np.asarray(
                [float(pool.probabilities[i]) for i in indexes],
                dtype=np.float64,
            )
            if float(probs.sum()) <= 0:
                probs = np.ones(len(indexes), dtype=np.float64)
            probs = probs / probs.sum()
            chosen_idx = int(rng.choice(len(indexes), p=probs))
            item = pool.items[indexes[chosen_idx]]
            add_item(buckets, item)
            if footprint_count_in_buckets(buckets, target.cells) >= target.count_exact:
                placed = True
                break


def size_bucket_value_stats(truth: SessionTruth, cells: int) -> tuple[int, int]:
    count = 0
    value_sum = 0
    for bucket in truth.buckets.values():
        for item in bucket.items:
            area = item.shape_w * item.shape_h
            if area == cells:
                count += 1
                value_sum += int(item.value)
    return count, value_sum


def _avg_match_factor(actual: float, target: float) -> float:
    rel_err = abs(actual - target) / max(1.0, target)
    if rel_err <= 0.10:
        return 1.0
    if rel_err <= 0.50:
        return max(0.20, 1.0 - rel_err)
    return 0.10


def size_bucket_evidence_score(
    truth: SessionTruth,
    problem: Any,
) -> float:
    """Score per-footprint avg (+ optional exact count / value floor)."""
    targets = getattr(problem, "size_bucket_evidence", ()) or ()
    if not targets:
        return size_avg_value_evidence_score_legacy(truth, problem)
    score = 1.0
    for target in targets:
        count, value_sum = size_bucket_value_stats(truth, target.cells)
        if target.count_exact is not None:
            if count != target.count_exact:
                score *= 0.05
                continue
        elif target.count_partial is not None and count < target.count_partial:
            score *= 0.20
        if target.value_floor is not None and value_sum < target.value_floor:
            return 0.0
        if count <= 0:
            score *= 0.10
            continue
        score *= _avg_match_factor(value_sum / count, target.avg_value)
    return score


def size_avg_value_evidence_score_legacy(
    truth: SessionTruth,
    problem: Any,
) -> float:
    """Fallback when only size_avg_value_targets are populated."""
    values = getattr(problem, "size_avg_value_targets", ()) or ()
    if not values:
        return 1.0
    score = 1.0
    for cells, target_avg in values:
        count, value_sum = size_bucket_value_stats(truth, cells)
        if count <= 0:
            score *= 0.10
            continue
        score *= _avg_match_factor(value_sum / count, target_avg)
    return score


def size_avg_value_evidence_score(truth: SessionTruth, problem: Any) -> float:
    return size_bucket_evidence_score(truth, problem)


def parse_size_bucket_diagnostics(
    diagnostics: str,
) -> tuple[dict[str, Any], ...]:
    """Parse ``size_bucket:*`` tokens from a semicolon-joined diagnostics string."""
    if not diagnostics:
        return ()
    rows: list[dict[str, Any]] = []
    for chunk in diagnostics.split(";"):
        token = chunk.strip()
        if not token.startswith("size_bucket:"):
            continue
        parts = token.split(":")
        if len(parts) < 3:
            continue
        try:
            cells = int(parts[1])
        except ValueError:
            continue
        fields: dict[str, Any] = {"cells": cells}
        for segment in parts[2:]:
            if "=" not in segment:
                continue
            key, value = segment.split("=", 1)
            if key == "avg":
                try:
                    fields["avg_value"] = float(value)
                except ValueError:
                    fields["avg_value"] = value
            elif key == "count_exact":
                try:
                    fields["count_exact"] = int(value)
                except ValueError:
                    pass
            elif key == "value_floor":
                try:
                    fields["value_floor"] = int(float(value))
                except ValueError:
                    pass
            else:
                fields[key] = value
        if "avg_value" in fields:
            rows.append(fields)
    return tuple(rows)


def format_size_bucket_target_label(target: Mapping[str, Any]) -> str:
    """Human label for overlay / logs."""
    cells = int(target.get("cells") or 0)
    avg = target.get("avg_value")
    tier = str(target.get("tier") or "-")
    strength = str(target.get("strength") or "soft")
    parts = [f"{cells}格均价 {_fmt_silver(avg)}" if avg is not None else f"{cells}格均价"]
    parts.append(f"tier={tier}")
    parts.append(strength)
    if target.get("count_exact") is not None:
        parts.append(f"件数={target['count_exact']}")
    if target.get("anchor"):
        parts.append(f"anchor={target['anchor']}")
    return " · ".join(parts)


def _fmt_silver(value: Any) -> str:
    try:
        amount = int(float(value))
    except (TypeError, ValueError):
        return str(value)
    return f"{amount:,}"


def size_avg_readings_from_action_rows(
    rows: Sequence[Mapping[str, Any]] | None,
) -> tuple[dict[str, Any], ...]:
    """Latest-first readings from live ``action_result_rows``."""
    if not rows:
        return ()
    readings: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        action_id = row.get("action_id")
        if action_id is None:
            continue
        try:
            aid = int(action_id)
        except (TypeError, ValueError):
            continue
        cells = SIZE_AVG_ACTION_FOOTPRINT.get(aid)
        if cells is None:
            continue
        raw = row.get("result")
        try:
            avg_value = float(raw)
        except (TypeError, ValueError):
            continue
        readings.append(
            {
                "action_id": aid,
                "tool": str(row.get("tool") or f"action:{aid}"),
                "footprint_cells": cells,
                "avg_value": avg_value,
                "avg_label": _fmt_silver(avg_value),
                "sort": row.get("sort"),
            }
        )
    return tuple(readings)


def size_bucket_eval_fields(
    *,
    posterior_diagnostics: str,
    action_result_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Flatten size-bucket diagnostics + tool usage for ``model_eval.jsonl``."""
    targets = parse_size_bucket_diagnostics(posterior_diagnostics)
    readings = size_avg_readings_from_action_rows(action_result_rows)
    action_ids = sorted({int(r["action_id"]) for r in readings})
    four_cell = next((t for t in targets if int(t.get("cells") or 0) == 4), None)
    latest_four = next(
        (r for r in readings if int(r.get("footprint_cells") or 0) == 4),
        None,
    )
    summary_parts: list[str] = []
    for target in targets[:3]:
        summary_parts.append(format_size_bucket_target_label(target))
    return {
        "size_bucket_active": bool(targets),
        "size_bucket_target_count": len(targets),
        "size_bucket_summary": "; ".join(summary_parts),
        "size_bucket_4cell_avg": (
            float(four_cell["avg_value"]) if four_cell and four_cell.get("avg_value") is not None else None
        ),
        "size_bucket_4cell_tier": (
            str(four_cell.get("tier") or "") if four_cell else ""
        ),
        "size_bucket_4cell_strength": (
            str(four_cell.get("strength") or "") if four_cell else ""
        ),
        "size_bucket_reading_4cell_avg": (
            float(latest_four["avg_value"])
            if latest_four and latest_four.get("avg_value") is not None
            else None
        ),
        "action_size_avg_tool_ids": ",".join(str(aid) for aid in action_ids),
        "action_100172_used": 100172 in action_ids,
        "action_size_avg_tool_count": len(action_ids),
    }


def size_bucket_evidence_diagnostics(
    evidence: tuple[SizeBucketEvidence, ...],
) -> tuple[str, ...]:
    lines: list[str] = []
    for target in evidence:
        parts = [
            f"size_bucket:{target.cells}",
            f"avg={target.avg_value:.0f}",
            f"tier={target.tier or '-'}",
            f"strength={target.strength}",
        ]
        if target.warehouse_band:
            parts.append(f"wh={target.warehouse_band}")
        if target.count_exact is not None:
            parts.append(f"count_exact={target.count_exact}")
        elif target.count_partial is not None:
            parts.append(f"count_partial>={target.count_partial}")
        if target.anchor_hint:
            parts.append(f"anchor={target.anchor_hint}")
        if target.value_floor is not None:
            parts.append(f"value_floor={target.value_floor}")
        lines.append(":".join(parts))
    return tuple(lines)


__all__ = [
    "FILLER_MAX_AVG_BY_CELLS",
    "HIGH_VALUE_FLOOR_MIN_AVG",
    "SIZE_AVG_ANCHORS",
    "SIZE_AVG_VALUE_SIGNAL_FLOORS",
    "SIZE_AVG_ACTION_FOOTPRINT",
    "SIZE_AVG_ACTION_IDS",
    "SIZE_AVG_VALUE_SIGNAL_FLOOR",
    "SizeBucketEvidence",
    "TYPICAL_SIZE_COUNT_BY_BAND",
    "WAREHOUSE_MID_MAX",
    "WAREHOUSE_SMALL_MAX",
    "actionable_size_avg_value_targets",
    "build_size_bucket_evidence",
    "format_size_bucket_target_label",
    "parse_size_bucket_diagnostics",
    "size_avg_readings_from_action_rows",
    "size_bucket_eval_fields",
    "size_avg_value_evidence_score",
    "size_bucket_counts_from_context",
    "size_bucket_evidence_diagnostics",
    "size_bucket_evidence_score",
    "size_bucket_value_stats",
    "warehouse_size_band",
]
