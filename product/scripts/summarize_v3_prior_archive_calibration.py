"""Compare v3 table prior distributions with canonical archive truth."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SRC = ROOT / "src"
for path in (SCRIPTS, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from evaluate_fatbeans_v3_samples import (  # noqa: E402
    _default_paths,
    _float_or_none,
    _round_metric,
    evaluate_paths,
    load_monitor_tables,
)
from bidking_lab.inference.v3.posterior import sample_truth_bank  # noqa: E402


def _quantile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    return float(np.quantile(np.asarray(values, dtype=np.float64), probability))


def _archive_raw_truth_by_map(rows: Iterable[dict[str, Any]]) -> dict[int, list[float]]:
    by_session: dict[tuple[int, str], float] = {}
    for row in rows:
        map_id = row.get("v3_truth_map_id")
        session_id = row.get("session_id")
        raw_value = _float_or_none(row.get("v3_truth_raw_total_value"))
        if map_id in (None, "") or session_id in (None, "") or raw_value is None:
            continue
        by_session[(int(map_id), str(session_id))] = raw_value
    out: dict[int, list[float]] = defaultdict(list)
    for (map_id, _session_id), raw_value in by_session.items():
        out[int(map_id)].append(float(raw_value))
    return dict(out)


def summarize_calibration_from_values(
    actual_by_map: Mapping[int, Sequence[float]],
    prior_by_map: Mapping[int, Sequence[float]],
    *,
    map_names: Mapping[int, str] | None = None,
    min_sessions: int = 5,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for map_id, actual_values in sorted(actual_by_map.items()):
        if len(actual_values) < int(min_sessions):
            continue
        prior_values = tuple(float(value) for value in prior_by_map.get(int(map_id), ()))
        if not prior_values:
            continue
        actual = tuple(float(value) for value in actual_values)
        actual_p50 = _quantile(actual, 0.50)
        actual_p90 = _quantile(actual, 0.90)
        prior_p50 = _quantile(prior_values, 0.50)
        prior_p90 = _quantile(prior_values, 0.90)
        median_ratio = (
            actual_p50 / prior_p50
            if actual_p50 is not None and prior_p50 not in (None, 0.0)
            else None
        )
        p90_ratio = (
            actual_p90 / prior_p90
            if actual_p90 is not None and prior_p90 not in (None, 0.0)
            else None
        )
        out.append(
            {
                "map_id": int(map_id),
                "map_name": (map_names or {}).get(int(map_id), ""),
                "archive_sessions": len(actual),
                "prior_trials": len(prior_values),
                "actual_raw_p50": _round_metric(actual_p50, 1),
                "actual_raw_p90": _round_metric(actual_p90, 1),
                "prior_raw_p50": _round_metric(prior_p50, 1),
                "prior_raw_p90": _round_metric(prior_p90, 1),
                "median_ratio": _round_metric(median_ratio, 6),
                "p90_ratio": _round_metric(p90_ratio, 6),
            }
        )
    return sorted(
        out,
        key=lambda row: (
            -abs(float(row["median_ratio"] or 1.0) - 1.0),
            str(row["map_id"]),
        ),
    )


def _prior_values_for_map(
    map_id: int,
    *,
    tables: Any,
    prior_trials: int,
    seed: int,
) -> tuple[float, ...]:
    truths = sample_truth_bank(
        int(map_id),
        maps=tables.maps,
        drops=tables.drops,
        items=tables.items,
        n_trials=int(prior_trials),
        seed=int(seed),
    )
    return tuple(float(truth.total_value()) for truth in truths)


def _print_table(rows: list[dict[str, Any]], *, top: int) -> None:
    for row in rows[:top]:
        print(
            " ".join(
                (
                    f"map_id={row['map_id']}",
                    f"name={row['map_name'] or '-'}",
                    f"sessions={row['archive_sessions']}",
                    f"prior_trials={row['prior_trials']}",
                    f"actual_p50={row['actual_raw_p50']}",
                    f"prior_p50={row['prior_raw_p50']}",
                    f"median_ratio={row['median_ratio']}",
                    f"actual_p90={row['actual_raw_p90']}",
                    f"prior_p90={row['prior_raw_p90']}",
                    f"p90_ratio={row['p90_ratio']}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare archive settlement raw truth with v3 table prior samples.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--prior-trials", type=int, default=10_000)
    parser.add_argument("--prior-seed", type=int, default=0)
    parser.add_argument("--min-sessions", type=int, default=5)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    tables = load_monitor_tables()
    rows, errors = evaluate_paths(
        args.paths or _default_paths(),
        tables=tables,
        posterior_trials=0,
    )
    actual_by_map = _archive_raw_truth_by_map(rows)
    prior_by_map = {
        map_id: _prior_values_for_map(
            map_id,
            tables=tables,
            prior_trials=args.prior_trials,
            seed=args.prior_seed,
        )
        for map_id in actual_by_map
        if len(actual_by_map[map_id]) >= int(args.min_sessions)
    }
    map_names = {
        int(map_id): str(getattr(bid_map, "name", ""))
        for map_id, bid_map in tables.maps.items()
    }
    result = {
        "errors": errors,
        "maps": summarize_calibration_from_values(
            actual_by_map,
            prior_by_map,
            map_names=map_names,
            min_sessions=args.min_sessions,
        ),
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if errors:
            print(f"errors={len(errors)}")
        _print_table(result["maps"], top=args.top)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
