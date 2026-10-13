"""Evaluate E09's opt-in hidden-red low-CPI tail on strict replayable samples.

The tool always selects through ``clean_eval_files`` and replays each sample's latest bidding
prefix twice in one process: default-OFF HEAD, then ``BIDKING_ENABLE_RED_CPI_LOW_TAIL=1``.
It reports red-cell OVER/UNDER separately, conservative truth breaks, balanced bias, and scope
violations.  Cloud settlement rows are intentionally not accepted here because they are not
tuning-grade engine replays.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "scripts", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "src", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ahmad_ref_engine import RED_CPI_LOW_TAIL_ENABLE, run_reference_engine  # noqa: E402
from audit_aisha_gap import (  # noqa: E402
    _build_snapshot,
    _events_through_sort,
    _map_id_from_events,
    _prefix_sort_id,
    _settlement_breakdown,
)
from bidking_lab.live.fatbeans import (  # noqa: E402
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)
from build_clean_eval_set import ALL_EVAL_HEROES, clean_eval_files  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402


class ReplayNotApplicable(ValueError):
    """A strict-valid capture that has no decision snapshot for this replay metric."""


@contextmanager
def _feature(enabled: bool) -> Iterable[None]:
    previous = os.environ.pop(RED_CPI_LOW_TAIL_ENABLE, None)
    try:
        if enabled:
            os.environ[RED_CPI_LOW_TAIL_ENABLE] = "1"
        yield
    finally:
        os.environ.pop(RED_CPI_LOW_TAIL_ENABLE, None)
        if previous is not None:
            os.environ[RED_CPI_LOW_TAIL_ENABLE] = previous


def _even_sample(paths: list[str], limit: int) -> list[str]:
    if limit <= 0 or len(paths) <= limit:
        return paths
    if limit == 1:
        return [paths[len(paths) // 2]]
    indices = {round(i * (len(paths) - 1) / (limit - 1)) for i in range(limit)}
    return [paths[index] for index in sorted(indices)]


def _truth_quality(settlement: dict[str, Any], quality: int) -> tuple[int, int]:
    counts = settlement.get("counts") or {}
    cells = settlement.get("cells") or {}
    return int(counts.get(quality, 0)), int(cells.get(quality, 0))


def _red_band_class(result: dict[str, Any], truth_cells: int) -> str:
    band = result.get("red_cells_range") or (None, None, None)
    if band[0] is None or band[2] is None:
        return "MISSING"
    if truth_cells < int(band[0]):
        return "OVER"
    if truth_cells > int(band[2]):
        return "UNDER"
    return "COVER"


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid_truth = [row for row in rows if row["truth_total_value"] > 0]
    by_hero: dict[str, Any] = {}
    for hero in sorted({str(row["hero"]) for row in rows}):
        hero_rows = [row for row in rows if row["hero"] == hero]
        hero_truth = [row for row in hero_rows if row["truth_total_value"] > 0]
        by_hero[hero] = {
            "n": len(hero_rows),
            "fired": sum(row["fired"] for row in hero_rows),
            "off_band": dict(Counter(row["off_band"] for row in hero_rows)),
            "on_band": dict(Counter(row["on_band"] for row in hero_rows)),
            "off_conservative_breaks": sum(
                row["off"]["conservative"] > row["truth_total_value"] for row in hero_truth
            ),
            "on_conservative_breaks": sum(
                row["on"]["conservative"] > row["truth_total_value"] for row in hero_truth
            ),
            "off_balanced_bias_median": _median(
                [(row["off"]["balanced"] - row["truth_total_value"]) / row["truth_total_value"] for row in hero_truth]
            ),
            "on_balanced_bias_median": _median(
                [(row["on"]["balanced"] - row["truth_total_value"]) / row["truth_total_value"] for row in hero_truth]
            ),
        }
    return {
        "n": len(rows),
        "fired": sum(row["fired"] for row in rows),
        "off_band": dict(Counter(row["off_band"] for row in rows)),
        "on_band": dict(Counter(row["on_band"] for row in rows)),
        "off_conservative_breaks": sum(
            row["off"]["conservative"] > row["truth_total_value"] for row in valid_truth
        ),
        "on_conservative_breaks": sum(
            row["on"]["conservative"] > row["truth_total_value"] for row in valid_truth
        ),
        "new_conservative_breaks": sum(
            row["on"]["conservative"] > row["truth_total_value"] >= row["off"]["conservative"]
            for row in valid_truth
        ),
        "balanced_changed": sum(row["on"]["balanced"] != row["off"]["balanced"] for row in rows),
        "aggressive_changed": sum(row["on"]["aggressive"] != row["off"]["aggressive"] for row in rows),
        "red_count_changed": sum(row["on"]["red_count_range"] != row["off"]["red_count_range"] for row in rows),
        "known_q6_footprint_fired": sum(row["fired"] and row["known_q6_footprint"] for row in rows),
        "quote_change_over_10pct": sum(
            abs(row["on"]["conservative"] - row["off"]["conservative"])
            > 0.10 * max(1, row["off"]["conservative"])
            for row in rows
        ),
        "off_balanced_bias_median": _median(
            [(row["off"]["balanced"] - row["truth_total_value"]) / row["truth_total_value"] for row in valid_truth]
        ),
        "on_balanced_bias_median": _median(
            [(row["on"]["balanced"] - row["truth_total_value"]) / row["truth_total_value"] for row in valid_truth]
        ),
        "by_hero": by_hero,
    }


def _compact(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: result.get(key)
        for key in (
            "conservative",
            "balanced",
            "aggressive",
            "red_count_range",
            "red_cells_range",
            "red_value_range",
        )
    }


def _evaluate(path: Path, hero: str) -> dict[str, Any]:
    events = parse_fatbeans_capture(path)
    settlement = _settlement_breakdown(events)
    if not settlement:
        raise ValueError("missing settlement truth")
    pre = [batch for batch in live_batches_from_fatbeans_events(events) if batch.phase != "settled"]
    prefix = None
    for end in range(len(pre), 0, -1):
        if not all(batch.phase == "reading" for batch in pre[:end]):
            prefix = pre[:end]
            break
    if not prefix:
        raise ReplayNotApplicable("missing bidding prefix")
    sort_id = _prefix_sort_id(prefix)
    prefix_events = _events_through_sort(events, sort_id) if sort_id else events
    map_id = _map_id_from_events(events)
    snapshot = _build_snapshot(hero=hero, events=prefix_events, prefix_batches=prefix, map_id=map_id)
    round_no = int(((snapshot.get("ui_contract") or {}).get("context") or {}).get("round") or 5)
    max_combos = min(20_000, hero_max_combos_for_round(hero, min(round_no, 5)))

    started = time.perf_counter()
    with _feature(False):
        off = run_reference_engine(snapshot, max_combos=max_combos).as_dict()
    with _feature(True):
        on = run_reference_engine(snapshot, max_combos=max_combos).as_dict()
    elapsed = time.perf_counter() - started
    truth_count, truth_cells = _truth_quality(settlement, 6)
    notes = tuple(str(note) for note in (on.get("notes") or ()))
    evidence = on.get("evidence") or {}
    known_q6 = bool(
        (evidence.get("quality_cells") or {}).get("q6") is not None
        or (evidence.get("avg_cells") or {}).get("q6") is not None
        or float((evidence.get("quality_cell_floors") or {}).get("q6") or 0) > 0
    )
    return {
        "file": str(path.relative_to(ROOT)),
        "hero": hero,
        "map_id": map_id,
        "round": round_no,
        "elapsed_s": round(elapsed, 4),
        "truth_red_count": truth_count,
        "truth_red_cells": truth_cells,
        "truth_total_value": int(settlement.get("total_value") or 0),
        "known_q6_footprint": known_q6,
        "on_evaluated": True,
        "fired": any(note.startswith("red_cpi_low_tail:") for note in notes),
        "off_band": _red_band_class(off, truth_cells),
        "on_band": _red_band_class(on, truth_cells),
        "off": _compact(off),
        "on": _compact(on),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--heroes", default=",".join(ALL_EVAL_HEROES))
    parser.add_argument("--limit-per-hero", type=int, default=0)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    heroes = [hero.strip().lower() for hero in args.heroes.split(",") if hero.strip()]
    selected = [
        (hero, path)
        for hero in heroes
        for path in _even_sample(list(clean_eval_files(hero)), args.limit_per_hero)
    ]
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    started = time.perf_counter()
    for index, (hero, raw_path) in enumerate(selected, start=1):
        try:
            rows.append(_evaluate(Path(raw_path), hero))
        except ReplayNotApplicable as exc:
            skipped.append({"file": str(raw_path), "hero": hero, "reason": str(exc)})
        except Exception as exc:  # noqa: BLE001
            errors.append({"file": str(raw_path), "hero": hero, "error": f"{type(exc).__name__}: {exc}"})
        if not args.quiet and (index == 1 or index % 25 == 0 or index == len(selected)):
            elapsed = time.perf_counter() - started
            eta = elapsed / index * (len(selected) - index)
            print(
                f"[{index}/{len(selected)}] kept={len(rows)} skipped={len(skipped)} errors={len(errors)} "
                f"elapsed={elapsed:.1f}s eta={eta:.1f}s",
                flush=True,
            )
    summary = _summarize(rows)
    summary["errors"] = len(errors)
    summary["skipped"] = len(skipped)
    summary["on_evaluated"] = sum(row["on_evaluated"] for row in rows)
    payload = {
        "selector": "clean_eval",
        "feature_env": RED_CPI_LOW_TAIL_ENABLE,
        "heroes": heroes,
        "limit_per_hero": args.limit_per_hero,
        "elapsed_s": round(time.perf_counter() - started, 3),
        "summary": summary,
        "skipped": skipped,
        "errors": errors,
        "rows": rows,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
