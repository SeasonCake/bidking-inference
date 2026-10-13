"""Batch-compare Aisha layout strategies on the same curated C2-eligible rows.

Variants (set via snapshot audit_aisha_layout_mode):
  off     — B-only baseline
  target  — C2′ raises total_grid_target (legacy C2 path)
  shadow  — diagnostic notes only, no target/band change
  band    — widen total_grid_range high bound only

Local audit only — not shipped in Hero Ref packages.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture  # noqa: E402

from ahmad_ref_engine import (  # noqa: E402
    AISHA_LAYOUT_GRID_HINT_NOTE,
    run_reference_engine,
)

from audit_aisha_gap import (  # noqa: E402
    FilterStats,
    SAMPLE_ROOTS,
    _build_snapshot,
    _events_through_sort,
    _hero_from_events,
    _map_id_from_events,
    _mid_gap,
    _passes_filters,
    _prefix_sort_id,
    _settlement_breakdown,
    _triplet,
    _truth_in_range,
)

VARIANTS = (
    ("b_only", "off"),
    ("c2_target", "target"),
    ("c2_shadow", "shadow"),
    ("c2_band", "band"),
)


@dataclass(frozen=True)
class VariantMetrics:
    name: str
    hit_rate: float
    avg_abs_gap: float
    improved_hit: int
    narrowed_gap: int
    widened_gap: int
    worsened_miss: int
    unchanged_hit: int


@dataclass(frozen=True)
class BatchRow:
    file: str
    audit_round: int
    truth_cells: int
    b_low: int | None
    b_mid: int | None
    b_high: int | None
    b_hit: bool
    b_gap: int | None
    by_variant: dict[str, tuple[int | None, int | None, int | None, bool, int | None]]


def _evaluate(snapshot: dict[str, Any], *, mode: str) -> dict[str, Any]:
    payload = dict(snapshot)
    payload["audit_aisha_layout_mode"] = mode
    return run_reference_engine(payload, max_combos=50_000).as_dict()


def _layout_eligible(snapshot: dict[str, Any]) -> bool:
    result = _evaluate(snapshot, mode="target")
    notes = result.get("notes") or []
    return any(AISHA_LAYOUT_GRID_HINT_NOTE in str(note) for note in notes)


def _variant_direction(
    *,
    truth: int,
    b_hit: bool,
    b_gap: int | None,
    v_hit: bool,
    v_gap: int | None,
) -> str:
    if not b_hit and v_hit:
        return "improved_hit"
    if b_hit and not v_hit:
        return "worsened_miss"
    if b_hit and v_hit:
        return "unchanged_hit"
    if b_gap is None or v_gap is None:
        return "unchanged_miss"
    if abs(int(v_gap)) < abs(int(b_gap)):
        return "narrowed_gap"
    if abs(int(v_gap)) > abs(int(b_gap)):
        return "widened_gap"
    return "unchanged_miss"


def collect_batch_rows(
    *,
    sample_roots: tuple[Path, ...],
    min_rounds: int,
    min_evidence_score: int,
    max_q6_value: int,
    max_q6_count: int,
    audit_round_override: int,
    limit: int,
    cohort: str = "eligible",
) -> list[BatchRow]:
    rows: list[BatchRow] = []
    stats_filter = FilterStats()
    for root in sample_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("fatbeans*aisha*.json")):
            try:
                events = parse_fatbeans_capture(path)
            except OSError:
                continue
            hero = _hero_from_events(events)
            pre_batches = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
            settlement = _settlement_breakdown(events)
            ok, audit_round = _passes_filters(
                path=path,
                events=events,
                hero=hero,
                pre_batches=pre_batches,
                settlement=settlement,
                min_rounds=min_rounds,
                min_evidence_score=min_evidence_score,
                max_q6_value=max_q6_value,
                max_q6_count=max_q6_count,
                audit_round_override=audit_round_override,
                stats=stats_filter,
            )
            if not ok or not settlement:
                continue
            prefix_batches = pre_batches[:audit_round]
            sort_id = _prefix_sort_id(prefix_batches)
            prefix_events = _events_through_sort(events, sort_id) if sort_id else events
            snapshot = _build_snapshot(
                hero="aisha",
                events=prefix_events,
                prefix_batches=prefix_batches,
                map_id=_map_id_from_events(events),
            )
            if cohort == "eligible" and not _layout_eligible(snapshot):
                continue
            truth_cells = int(settlement["total_cells"])
            b_result = _evaluate(snapshot, mode="off")
            b_low, b_mid, b_high = _triplet(b_result, "total_grid_range")
            b_hit = _truth_in_range(truth_cells, b_low, b_mid, b_high)
            b_gap = _mid_gap(truth_cells, b_low, b_mid, b_high)
            by_variant: dict[str, tuple[int | None, int | None, int | None, bool, int | None]] = {}
            for variant_name, mode in VARIANTS:
                if mode == "off":
                    by_variant[variant_name] = (b_low, b_mid, b_high, b_hit, b_gap)
                    continue
                result = _evaluate(snapshot, mode=mode)
                low, mid, high = _triplet(result, "total_grid_range")
                hit = _truth_in_range(truth_cells, low, mid, high)
                gap = _mid_gap(truth_cells, low, mid, high)
                by_variant[variant_name] = (low, mid, high, hit, gap)
            rows.append(
                BatchRow(
                    file=path.name,
                    audit_round=audit_round,
                    truth_cells=truth_cells,
                    b_low=b_low,
                    b_mid=b_mid,
                    b_high=b_high,
                    b_hit=b_hit,
                    b_gap=b_gap,
                    by_variant=by_variant,
                )
            )
            if limit and len(rows) >= limit:
                return rows
    return rows


def _summarize_variant(rows: list[BatchRow], variant_name: str) -> VariantMetrics:
    hits = 0
    gaps: list[int] = []
    directions: Counter[str] = Counter()
    for row in rows:
        low, mid, high, hit, gap = row.by_variant[variant_name]
        if hit:
            hits += 1
        if gap is not None:
            gaps.append(abs(int(gap)))
        if variant_name == "b_only":
            continue
        directions[_variant_direction(
            truth=row.truth_cells,
            b_hit=row.b_hit,
            b_gap=row.b_gap,
            v_hit=hit,
            v_gap=gap,
        )] += 1
    return VariantMetrics(
        name=variant_name,
        hit_rate=hits / len(rows) if rows else 0.0,
        avg_abs_gap=sum(gaps) / len(gaps) if gaps else 0.0,
        improved_hit=directions.get("improved_hit", 0),
        narrowed_gap=directions.get("narrowed_gap", 0),
        widened_gap=directions.get("widened_gap", 0),
        worsened_miss=directions.get("worsened_miss", 0),
        unchanged_hit=directions.get("unchanged_hit", 0),
    )


def format_report(rows: list[BatchRow], *, cohort: str) -> str:
    cohort_label = (
        "C2-eligible curated rows (layout footroom fires)"
        if cohort == "eligible"
        else "all curated gap rows (same filters as audit_aisha_gap)"
    )
    lines = [
        f"Aisha layout strategy batch audit ({cohort_label})",
        f"rows={len(rows)}",
        "",
    ]
    if not rows:
        lines.append("No eligible rows.")
        return "\n".join(lines)

    metrics = [_summarize_variant(rows, name) for name, _ in VARIANTS]
    lines.append("aggregate vs settlement total_cells:")
    lines.append("variant        | hit_rate | avg|mid_gap| | improved | narrowed | widened | worsened | u_hit")
    lines.append("-" * 95)
    for item in metrics:
        lines.append(
            f"{item.name:<14} | {item.hit_rate:7.1%} | {item.avg_abs_gap:9.1f} | "
            f"{item.improved_hit:8d} | {item.narrowed_gap:8d} | {item.widened_gap:7d} | "
            f"{item.worsened_miss:8d} | {item.unchanged_hit:5d}"
        )
    lines.extend(
        [
            "",
            "recommendation heuristics:",
            "  - prefer hit_rate >= b_only with narrowed >= widened",
            "  - shadow is diagnostic-only baseline for UI hints",
            "  - band avoids target collapse; compare vs target on worsened_miss",
            "",
            "sample rows (first 12): file | truth | B band | target | shadow | band",
            "-" * 110,
        ]
    )
    for row in rows[:12]:
        parts = [f"{row.file} | R{row.audit_round} | T={row.truth_cells} | B=[{row.b_low},{row.b_mid},{row.b_high}]"]
        for variant_name, _mode in VARIANTS[1:]:
            low, mid, high, hit, _gap = row.by_variant[variant_name]
            parts.append(f"{variant_name}=[{low},{mid},{high}]{'Y' if hit else 'N'}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-rounds", type=int, default=3)
    parser.add_argument("--min-evidence-score", type=int, default=4)
    parser.add_argument("--max-q6-value", type=int, default=2_000_000)
    parser.add_argument("--max-q6-count", type=int, default=3)
    parser.add_argument("--audit-round", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--cohort",
        choices=("eligible", "all"),
        default="eligible",
        help="eligible=layout footroom fires; all=full curated gap set",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
    )
    args = parser.parse_args()

    report_path = args.report
    if report_path is None:
        report_path = (
            ROOT / "data/reports/audit_aisha_c2_batch_full.txt"
            if args.cohort == "all"
            else ROOT / "data/reports/audit_aisha_c2_batch.txt"
        )

    rows = collect_batch_rows(
        sample_roots=SAMPLE_ROOTS,
        min_rounds=args.min_rounds,
        min_evidence_score=args.min_evidence_score,
        max_q6_value=args.max_q6_value,
        max_q6_count=args.max_q6_count,
        audit_round_override=args.audit_round,
        limit=args.limit,
        cohort=args.cohort,
    )
    report = format_report(rows, cohort=args.cohort)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"\nwrote {report_path}")


if __name__ == "__main__":
    main()
