"""Monte-Carlo simulation of game-displayed 均格 / 均价 readings.

Samples plausible warehouse buckets from Item.txt (and optional map MC
sessions), classifies decimal tails (``.17``, ``.43``, integers, ``.5``,
``.25``/``.75``), and measures how many ``(total_cells, count)`` pairs
survive under a warehouse budget — the same pre-filter
``candidates_for_bucket`` uses via ``warehouse_capacity - other_known_cells``.

This module is research tooling for tightening enumeration; it does not
change runtime inference until we promote findings into ``observation.py``.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Literal, Sequence

import numpy as np

from bidking_lab.extract.item_table import Item
from bidking_lab.inference.display import (
    Reading,
    avg_value_shows_fractional_cents,
    best_count_for_avg_value_integer_leak,
    enumerate_candidates,
    filter_by_warehouse_size,
    format_value,
    integer_total_leak_distance,
    is_compatible,
    parse_reading,
    reading_info_bits,
)
from bidking_lab.inference.observation import (
    QualityBucketObs,
    candidates_for_bucket,
)

ReadingKind = Literal[
    "integer",
    "one_decimal_exact",
    "half",
    "quarter",
    "trailing_zero",
    "tight_fraction",
    "other",
]

QUALITY_NAMES: dict[int, str] = {
    1: "白绿",
    2: "绿",
    3: "蓝",
    4: "紫",
    5: "金",
    6: "红",
}


def format_silver_avg(value_sum: int, count: int, *, max_decimals: int = 2) -> str:
    """Game display for per-item average silver (same truncate rule as 均格)."""
    if count <= 0:
        raise ValueError("count must be positive")
    return format_value(value_sum, count, max_decimals=max_decimals)


def classify_display_suffix(display: str) -> ReadingKind:
    """Coarse bucket for how informative a displayed average is."""
    r = parse_reading(display)
    if r.is_integer:
        return "integer"
    frac = display.split(".", 1)[1] if "." in display else ""
    if r.n_decimals == 1:
        return "one_decimal_exact"
    if r.trailing_zero:
        return "trailing_zero"
    if frac in ("5", "50"):
        return "half"
    if frac in ("25", "75"):
        return "quarter"
    if r.n_decimals == 2 and not r.trailing_zero:
        return "tight_fraction"
    return "other"


def _cent_suffix(display: str) -> str:
    if "." not in display:
        return "int"
    frac = display.split(".", 1)[1]
    if len(frac) >= 2:
        return frac[-2:]
    return frac.ljust(2, "0")


@dataclass(frozen=True)
class BucketSample:
    """One synthetic or sampled quality bucket."""

    quality: int
    total_cells: int
    count: int
    value_sum: int
    avg_cells_display: str
    avg_value_display: str
    avg_cells_kind: ReadingKind
    avg_value_kind: ReadingKind
    avg_value_cent_suffix: str


@dataclass
class AmbiguityReport:
    """How many (cells, count) pairs explain a reading under a budget."""

    quality: int
    reading_type: Literal["avg_cells", "avg_value"]
    display: str
    kind: ReadingKind
    warehouse_capacity: int
    other_known_cells: int
    budget_for_bucket: int
    n_candidates_unbounded: int
    n_candidates_budget: int
    info_bits_unbounded: float
    info_bits_budget: float
    truth_in_unbounded: bool
    truth_in_budget: bool
    truth_rank_budget: int | None
    integer_leak_counts: tuple[int, ...] = ()
    engine_n_candidates: int = 0
    engine_top: tuple[int, int] | None = None
    engine_respects_budget: bool = True


@dataclass
class SimSummary:
    trials: int
    by_cells_kind: Counter[str] = field(default_factory=Counter)
    by_value_kind: Counter[str] = field(default_factory=Counter)
    by_value_cent: Counter[str] = field(default_factory=Counter)
    ambiguity: list[AmbiguityReport] = field(default_factory=list)
    budget_violations: int = 0
    truth_miss_budget: int = 0
    truth_miss_engine: int = 0


def sample_bucket_from_items(
    items: Sequence[Item],
    count: int,
    rng: np.random.Generator,
) -> BucketSample | None:
    """Draw ``count`` items with replacement from a quality's item pool."""
    if count < 1 or not items:
        return None
    idx = rng.integers(0, len(items), size=count)
    picked = [items[int(i)] for i in idx]
    total_cells = sum(it.shape_w * it.shape_h for it in picked)
    value_sum = sum(it.value for it in picked)
    q = picked[0].quality
    ac_disp = format_value(total_cells, count)
    av_disp = format_silver_avg(value_sum, count)
    return BucketSample(
        quality=q,
        total_cells=total_cells,
        count=count,
        value_sum=value_sum,
        avg_cells_display=ac_disp,
        avg_value_display=av_disp,
        avg_cells_kind=classify_display_suffix(ac_disp),
        avg_value_kind=classify_display_suffix(av_disp),
        avg_value_cent_suffix=_cent_suffix(av_disp),
    )


def sample_bucket_from_truth(
    quality: int,
    total_cells: int,
    count: int,
    value_sum: int,
) -> BucketSample:
    ac_disp = format_value(total_cells, count)
    av_disp = format_silver_avg(value_sum, count)
    return BucketSample(
        quality=quality,
        total_cells=total_cells,
        count=count,
        value_sum=value_sum,
        avg_cells_display=ac_disp,
        avg_value_display=av_disp,
        avg_cells_kind=classify_display_suffix(ac_disp),
        avg_value_kind=classify_display_suffix(av_disp),
        avg_value_cent_suffix=_cent_suffix(av_disp),
    )


def integer_leak_matching_counts(
    avg_value: float,
    *,
    max_count: int = 35,
    max_distance: float = 0.05,
) -> tuple[int, ...]:
    return tuple(
        c
        for c in range(1, max_count + 1)
        if integer_total_leak_distance(avg_value, c) <= max_distance
    )


def analyze_avg_cells_ambiguity(
    sample: BucketSample,
    *,
    warehouse_capacity: int,
    other_known_cells: int = 0,
    max_count: int = 50,
) -> AmbiguityReport:
    reading = parse_reading(sample.avg_cells_display)
    budget = max(0, warehouse_capacity - other_known_cells)
    unbounded = enumerate_candidates(
        reading, max_count=max_count, max_total_cells=252,
    )
    bounded = filter_by_warehouse_size(
        unbounded, warehouse_size=budget,
    )
    truth_pair = (sample.total_cells, sample.count)
    rank = None
    if truth_pair in bounded:
        rank = bounded.index(truth_pair) + 1

    bucket = QualityBucketObs(
        quality=sample.quality,
        avg_cells=reading,
    )
    engine = candidates_for_bucket(
        bucket,
        warehouse_capacity=warehouse_capacity,
        other_known_cells=other_known_cells,
        max_count=max_count,
    )
    engine_top = (
        (engine[0].total_cells, engine[0].count) if engine else None
    )
    respects = all(c.total_cells <= budget for c in engine)

    return AmbiguityReport(
        quality=sample.quality,
        reading_type="avg_cells",
        display=sample.avg_cells_display,
        kind=sample.avg_cells_kind,
        warehouse_capacity=warehouse_capacity,
        other_known_cells=other_known_cells,
        budget_for_bucket=budget,
        n_candidates_unbounded=len(unbounded),
        n_candidates_budget=len(bounded),
        info_bits_unbounded=reading_info_bits(
            reading, max_count=max_count, max_total_cells=252,
        ),
        info_bits_budget=(
            math.log2(len(bounded)) if bounded else math.inf
        ),
        truth_in_unbounded=truth_pair in unbounded,
        truth_in_budget=truth_pair in bounded,
        truth_rank_budget=rank,
        engine_n_candidates=len(engine),
        engine_top=engine_top,
        engine_respects_budget=respects,
    )


def analyze_avg_value_ambiguity(
    sample: BucketSample,
    *,
    warehouse_capacity: int,
    other_known_cells: int = 0,
    max_count: int = 35,
) -> AmbiguityReport:
    """Count / value-side leakage (integer total silver), not (cells, count)."""
    av = sample.value_sum / sample.count
    leak_counts = integer_leak_matching_counts(av, max_count=max_count)
    best = best_count_for_avg_value_integer_leak(av, max_count=max_count)
    bucket = QualityBucketObs(quality=sample.quality, avg_value=av)
    engine = candidates_for_bucket(
        bucket,
        warehouse_capacity=warehouse_capacity,
        other_known_cells=other_known_cells,
        max_count=50,
    )
    budget = max(0, warehouse_capacity - other_known_cells)
    respects = all(c.total_cells <= budget for c in engine)
    return AmbiguityReport(
        quality=sample.quality,
        reading_type="avg_value",
        display=sample.avg_value_display,
        kind=sample.avg_value_kind,
        warehouse_capacity=warehouse_capacity,
        other_known_cells=other_known_cells,
        budget_for_bucket=budget,
        n_candidates_unbounded=0,
        n_candidates_budget=0,
        info_bits_unbounded=0.0,
        info_bits_budget=0.0,
        truth_in_unbounded=sample.count in leak_counts,
        truth_in_budget=sample.count in leak_counts,
        truth_rank_budget=(
            leak_counts.index(sample.count) + 1
            if sample.count in leak_counts
            else None
        ),
        integer_leak_counts=leak_counts,
        engine_n_candidates=len(engine),
        engine_top=(
            (engine[0].total_cells, engine[0].count) if engine else None
        ),
        engine_respects_budget=respects,
    )


def run_item_pool_simulation(
    items_by_quality: dict[int, list[Item]],
    *,
    trials_per_quality: int = 2000,
    count_range: tuple[int, int] = (1, 12),
    warehouse_capacity: int = 120,
    other_known_cells: int = 40,
    seed: int = 20260519,
    sample_fraction_for_ambiguity: float = 0.15,
) -> SimSummary:
    """Random item draws per quality — explores decimal tail distribution."""
    rng = np.random.default_rng(seed)
    summary = SimSummary(trials=0)
    amb_budget = int(trials_per_quality * sample_fraction_for_ambiguity)

    for q, pool in sorted(items_by_quality.items()):
        if q not in QUALITY_NAMES or len(pool) < 2:
            continue
        c_lo, c_hi = count_range
        for _ in range(trials_per_quality):
            count = int(rng.integers(c_lo, c_hi + 1))
            sample = sample_bucket_from_items(pool, count, rng)
            if sample is None:
                continue
            summary.trials += 1
            summary.by_cells_kind[sample.avg_cells_kind] += 1
            summary.by_value_kind[sample.avg_value_kind] += 1
            summary.by_value_cent[sample.avg_value_cent_suffix] += 1

        for _ in range(amb_budget):
            count = int(rng.integers(max(2, c_lo), c_hi + 1))
            sample = sample_bucket_from_items(pool, count, rng)
            if sample is None:
                continue
            ac_rep = analyze_avg_cells_ambiguity(
                sample,
                warehouse_capacity=warehouse_capacity,
                other_known_cells=other_known_cells,
            )
            av_rep = analyze_avg_value_ambiguity(
                sample,
                warehouse_capacity=warehouse_capacity,
                other_known_cells=other_known_cells,
            )
            summary.ambiguity.extend((ac_rep, av_rep))
            if not ac_rep.engine_respects_budget:
                summary.budget_violations += 1
            if not av_rep.engine_respects_budget:
                summary.budget_violations += 1
            if not ac_rep.truth_in_budget:
                summary.truth_miss_budget += 1
            if ac_rep.engine_top and ac_rep.engine_top != (
                sample.total_cells,
                sample.count,
            ):
                summary.truth_miss_engine += 1

    return summary


def run_session_simulation(
    *,
    sample_session_truth,
    maps,
    drops,
    items,
    map_ids: Sequence[int],
    n_sessions: int = 400,
    seed: int = 20260519,
) -> SimSummary:
    """Sample full MC sessions — realistic joint (cells, count, value)."""
    rng = np.random.default_rng(seed)
    summary = SimSummary(trials=0)
    for _ in range(n_sessions):
        map_id = int(rng.choice(list(map_ids)))
        truth = sample_session_truth(map_id, maps=maps, drops=drops, items=items, rng=rng)
        wh = truth.warehouse_total_cells
        if wh <= 0:
            continue
        other = 0
        for q in sorted(truth.buckets.keys()):
            b = truth.buckets[q]
            if b.count < 1:
                continue
            sample = sample_bucket_from_truth(
                q, b.total_cells, b.count, b.value_sum,
            )
            summary.trials += 1
            summary.by_cells_kind[sample.avg_cells_kind] += 1
            summary.by_value_kind[sample.avg_value_kind] += 1
            summary.by_value_cent[sample.avg_value_cent_suffix] += 1

            ac_rep = analyze_avg_cells_ambiguity(
                sample,
                warehouse_capacity=wh,
                other_known_cells=other,
            )
            av_rep = analyze_avg_value_ambiguity(
                sample,
                warehouse_capacity=wh,
                other_known_cells=other,
            )
            summary.ambiguity.extend((ac_rep, av_rep))
            other += b.total_cells
            if not ac_rep.truth_in_budget:
                summary.truth_miss_budget += 1
            if not ac_rep.engine_respects_budget or not av_rep.engine_respects_budget:
                summary.budget_violations += 1

    return summary


def top_fractional_suffixes(
    counter: Counter[str],
    *,
    min_count: int = 5,
    top_n: int = 20,
) -> list[tuple[str, int]]:
    items = [(k, v) for k, v in counter.items() if k not in ("int",) and v >= min_count]
    items.sort(key=lambda x: (-x[1], x[0]))
    return items[:top_n]


def format_summary_report(summary: SimSummary) -> str:
    lines: list[str] = []
    lines.append(f"=== Decimal reading simulation ({summary.trials} bucket samples) ===")
    lines.append("")
    lines.append("均格 display kind (from Item combinations / sessions):")
    for kind, n in summary.by_cells_kind.most_common():
        pct = 100.0 * n / max(1, summary.trials)
        lines.append(f"  {kind:18s} {n:6d}  ({pct:5.1f}%)")
    lines.append("")
    lines.append("均价 display kind:")
    for kind, n in summary.by_value_kind.most_common():
        pct = 100.0 * n / max(1, summary.trials)
        lines.append(f"  {kind:18s} {n:6d}  ({pct:5.1f}%)")
    lines.append("")
    lines.append("均价 last-2-digit suffix (fractional cents leak):")
    for suf, n in top_fractional_suffixes(summary.by_value_cent):
        pct = 100.0 * n / max(1, summary.trials)
        lines.append(f"  .{suf:2s}              {n:6d}  ({pct:5.1f}%)")
    lines.append("")

  # Ambiguity stats
    ac_reports = [r for r in summary.ambiguity if r.reading_type == "avg_cells"]
    if ac_reports:
        lines.append("均格 — candidate counts (warehouse budget applied):")
        by_kind: dict[str, list[int]] = defaultdict(list)
        for r in ac_reports:
            by_kind[r.kind].append(r.n_candidates_budget)
        for kind in sorted(by_kind.keys()):
            vals = by_kind[kind]
            med = sorted(vals)[len(vals) // 2]
            lines.append(
                f"  {kind:18s} median={med:4d}  "
                f"mean={sum(vals)/len(vals):.1f}  max={max(vals)}  n={len(vals)}"
            )
        tight = [r for r in ac_reports if r.kind == "tight_fraction"]
        if tight:
            lines.append(
                f"  tight_fraction truth recovered in budget: "
                f"{sum(r.truth_in_budget for r in tight)}/{len(tight)}"
            )
        lines.append(
            f"  engine budget violations: {summary.budget_violations}  "
            f"truth missed after warehouse cap: {summary.truth_miss_budget}"
        )
    lines.append("")

    av_reports = [r for r in summary.ambiguity if r.reading_type == "avg_value"]
    if av_reports:
        lines.append("均价 — integer-leak matching counts:")
        frac_only = [
            r for r in av_reports
            if r.kind in ("tight_fraction", "trailing_zero", "other")
        ]
        for r in frac_only[:8]:
            qn = QUALITY_NAMES.get(r.quality, f"q{r.quality}")
            lines.append(
                f"  {qn} display={r.display:>12s}  "
                f"leak_counts={list(r.integer_leak_counts)[:8]}  "
                f"truth_in={r.truth_in_budget}  engine_top={r.engine_top}"
            )
        if frac_only:
            hit = sum(1 for r in frac_only if r.truth_in_budget)
            lines.append(
                f"  fractional avg_value: truth count in leak set "
                f"{hit}/{len(frac_only)}"
            )
    return "\n".join(lines)


def items_by_quality(items: dict[int, Item]) -> dict[int, list[Item]]:
    out: dict[int, list[Item]] = defaultdict(list)
    for it in items.values():
        if it.value > 0 and it.shape_w * it.shape_h > 0:
            out[it.quality].append(it)
    return dict(out)


__all__ = (
    "AmbiguityReport",
    "BucketSample",
    "SimSummary",
    "analyze_avg_cells_ambiguity",
    "analyze_avg_value_ambiguity",
    "classify_display_suffix",
    "format_silver_avg",
    "format_summary_report",
    "items_by_quality",
    "run_item_pool_simulation",
    "run_session_simulation",
    "sample_bucket_from_items",
    "top_fractional_suffixes",
)
