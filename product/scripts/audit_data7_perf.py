"""Replay early bidding windows and classify ref_v0 route + latency.

Designed for data7-style archives; defaults to local fatbeans samples when
data7 is not available. Local audit only — not shipped in Hero Ref packages.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.live.fatbeans import (  # noqa: E402
    _hero_mode_from_state,
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)
from bidking_lab.live.monitor import (  # noqa: E402
    _ahmad_ref_inputs_from_batches,
    _public_info_rows,
    _skill_reveal_rows,
)
from ahmad_ref_engine import extract_evidence, normalize_hero_key, run_reference_engine  # noqa: E402

DEFAULT_SAMPLE_ROOTS = (
    ROOT / "data/samples/fatbeans",
    ROOT / "data/samples/fatbeans_activity_20260605_shipwreck",
)
DEFAULT_REPORT = ROOT / "data/reports/audit_data7_perf.txt"


@dataclass(frozen=True)
class PerfRow:
    file: str
    hero: str
    round: int
    status: str
    route: str
    elapsed_ms: float
    combo_count: int
    total_count: int | None
    has_q5_avg_cells: bool
    sparse_prior: bool
    deferred: bool


def _notes_list(result: dict[str, Any]) -> list[str]:
    notes = result.get("notes")
    if isinstance(notes, str):
        return [part.strip() for part in notes.split(";") if part.strip()]
    if isinstance(notes, (list, tuple)):
        return [str(part) for part in notes]
    return []


def classify_route(notes: list[str], *, status: str) -> str:
    if any(note == "waiting_total_count:grid_only" for note in notes):
        return "deferred_grid_only"
    if status == "missing_total_count":
        return "missing_total_count"
    if "exact_total_avg_cells_fast_path" in notes:
        return "exact_total_avg_cells_fast_path"
    if "sparse_exact_total_prior_enumeration" in notes:
        return "sparse_exact_prior"
    if "count_prior_enumerated" in notes:
        return "count_prior"
    if "total_count_from_ref_count_prior" in notes:
        return "total_count_prior"
    if any(note.startswith("grid_conditioned") for note in notes):
        return "full_enumeration"
    return status or "unknown"


def _build_snapshot(*, hero: str, events, structured_ref_inputs: dict | None) -> dict:
    return {
        "ui_contract": {
            "context": {"hero": hero, "phase": "bidding"},
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": structured_ref_inputs or {},
        "public_info_rows": _public_info_rows(events, {}),
        "skill_reveals": _skill_reveal_rows(events, {}),
        "skill_reveal_rows": _skill_reveal_rows(events, {}),
        "action_result_rows": [],
    }


def _iter_sample_files(roots: tuple[Path, ...], *, hero_filter: str = "") -> list[Path]:
    files: list[Path] = []
    hero_token = hero_filter.lower()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("fatbeans*.json")):
            if hero_token and hero_token not in path.name.lower():
                continue
            files.append(path)
    return files


def _early_windows(events, *, max_round: int) -> list[tuple[int, list]]:
    batches = live_batches_from_fatbeans_events(events)
    pre = [b for b in batches if b.phase != "settled"]
    if not pre:
        return []
    windows: list[tuple[int, list]] = []
    for idx in range(1, min(len(pre), max_round) + 1):
        windows.append((idx, pre[:idx]))
    return windows


def _hero_from_events(events) -> str:
    for state in reversed(events.states):
        hero = _hero_mode_from_state(state)
        if hero:
            return normalize_hero_key(hero)
    return ""


def audit_samples(
    *,
    sample_roots: tuple[Path, ...],
    max_round: int,
    max_combos: int,
    hero_filter: str,
) -> list[PerfRow]:
    rows: list[PerfRow] = []
    for path in _iter_sample_files(sample_roots, hero_filter=hero_filter):
        events = parse_fatbeans_capture(path)
        hero = _hero_from_events(events)
        if hero_filter and hero != hero_filter:
            continue
        for round_idx, prefix_batches in _early_windows(events, max_round=max_round):
            bridge = _ahmad_ref_inputs_from_batches(prefix_batches, hero=hero) or {}
            snapshot = _build_snapshot(hero=hero, events=events, structured_ref_inputs=bridge)
            started = time.perf_counter()
            result = run_reference_engine(snapshot, max_combos=max_combos).as_dict()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            notes = _notes_list(result)
            evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
            avg_cells = evidence.get("avg_cells") if isinstance(evidence.get("avg_cells"), dict) else {}
            total_count = evidence.get("total_count")
            try:
                parsed_total = int(total_count) if total_count not in (None, "") else None
            except (TypeError, ValueError):
                parsed_total = None
            rows.append(
                PerfRow(
                    file=path.name,
                    hero=hero or "?",
                    round=round_idx,
                    status=str(result.get("status") or ""),
                    route=classify_route(notes, status=str(result.get("status") or "")),
                    elapsed_ms=round(elapsed_ms, 1),
                    combo_count=int(result.get("combo_count") or 0),
                    total_count=parsed_total,
                    has_q5_avg_cells="q5" in avg_cells and avg_cells.get("q5") not in (None, ""),
                    sparse_prior="sparse_exact_total_prior_enumeration" in notes,
                    deferred="waiting_total_count:grid_only" in notes,
                )
            )
    return rows


def _format_report(rows: list[PerfRow]) -> str:
    lines: list[str] = []
    lines.append("audit_data7_perf")
    lines.append(f"rows={len(rows)}")
    if not rows:
        lines.append("(no rows)")
        return "\n".join(lines)

    by_route: Counter[str] = Counter(row.route for row in rows)
    lines.append("")
    lines.append("route_counts:")
    for route, count in by_route.most_common():
        subset = [row for row in rows if row.route == route]
        elapsed = [row.elapsed_ms for row in subset]
        avg_ms = sum(elapsed) / len(elapsed)
        p95 = sorted(elapsed)[max(0, int(len(elapsed) * 0.95) - 1)]
        lines.append(f"  {route}: n={count} avg_ms={avg_ms:.1f} p95_ms={p95:.1f}")

    slow = sorted(rows, key=lambda row: row.elapsed_ms, reverse=True)[:20]
    lines.append("")
    lines.append("slowest_20:")
    for row in slow:
        lines.append(
            f"  {row.elapsed_ms:8.1f}ms r{row.round} {row.hero:6} {row.route:22} "
            f"combos={row.combo_count:6} total={row.total_count} q5_avg={row.has_q5_avg_cells} "
            f"{row.file}"
        )

    lines.append("")
    lines.append("§50-2 candidates (total_count + q5 avg_cells, not sparse/deferred):")
    candidates = [
        row
        for row in rows
        if row.total_count is not None
        and row.has_q5_avg_cells
        and not row.sparse_prior
        and not row.deferred
        and row.elapsed_ms >= 500.0
    ]
    if not candidates:
        lines.append("  (none >= 500ms in scanned samples)")
    for row in sorted(candidates, key=lambda item: item.elapsed_ms, reverse=True)[:15]:
        lines.append(
            f"  {row.elapsed_ms:8.1f}ms r{row.round} {row.hero} combos={row.combo_count} {row.file}"
        )

    ethan_rows = [row for row in rows if row.hero == "ethan"]
    lines.append("")
    lines.append(f"ethan_rows={len(ethan_rows)} (generic ref hero — §50-2 Ahmed structured path does not apply)")
    if ethan_rows:
        ethan_routes = Counter(row.route for row in ethan_rows)
        lines.append(f"  routes={dict(ethan_routes)}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample-root",
        action="append",
        default=[],
        help="Sample directory (repeatable). Defaults to fatbeans sample roots.",
    )
    parser.add_argument("--max-round", type=int, default=3, help="Early bidding prefix rounds per sample.")
    parser.add_argument("--max-combos", type=int, default=50_000)
    parser.add_argument("--hero", default="", help="Optional hero filter, e.g. ahmed")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--no-report-file", action="store_true")
    args = parser.parse_args(argv)

    roots = tuple(Path(p) for p in args.sample_root) if args.sample_root else DEFAULT_SAMPLE_ROOTS
    rows = audit_samples(
        sample_roots=roots,
        max_round=max(1, int(args.max_round)),
        max_combos=int(args.max_combos),
        hero_filter=normalize_hero_key(args.hero),
    )
    report = _format_report(rows)
    print(report)
    if not args.no_report_file:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report + "\n", encoding="utf-8")
        print(f"\nwrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
