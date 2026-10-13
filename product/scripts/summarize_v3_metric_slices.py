"""Summarize v3 posterior metrics by round/map/scope slices."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evaluate_fatbeans_v3_samples import (  # noqa: E402
    _default_underestimate_repair_path,
    _default_paths,
    _float_or_none,
    _mean,
    _round_metric,
    evaluate_paths,
    load_underestimate_repair_entries,
    load_monitor_tables,
)


DEFAULT_SLICE_FIELDS: tuple[str, ...] = (
    "round",
    "map_id",
    "hero",
    "hero_map_id",
    "evidence_stage",
    "information_density_band",
    "evidence_profile_key",
    "hero_map_evidence_stage",
    "hero_map_evidence_profile",
    "v3_post_match_scope",
)


def _paired_rows(rows: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_decision_available")
        and row.get("v3_post_ready")
        and _float_or_none(row.get("v3_post_formal_decision_value_p50")) is not None
        and _float_or_none(row.get("v3_truth_formal_decision_value")) is not None
    )


def _below_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(1.0 if pred < truth else 0.0 for pred, truth in pairs)


def _over_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(1.0 if pred > truth else 0.0 for pred, truth in pairs)


def _coverage_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(1.0 if truth <= pred else 0.0 for pred, truth in pairs)


def _pred_truth(
    rows: Iterable[dict[str, Any]],
    pred_key: str,
    truth_key: str,
) -> tuple[tuple[float, float], ...]:
    pairs: list[tuple[float, float]] = []
    for row in rows:
        pred = _float_or_none(row.get(pred_key))
        truth = _float_or_none(row.get(truth_key))
        if pred is not None and truth is not None:
            pairs.append((pred, truth))
    return tuple(pairs)


def _mae(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(abs(pred - truth) for pred, truth in pairs)


def _bias(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(pred - truth for pred, truth in pairs)


def summarize_slice(
    rows: Iterable[dict[str, Any]],
    field: str,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _paired_rows(rows):
        groups[str(row.get(field) if row.get(field) not in (None, "") else "none")].append(row)

    out: list[dict[str, Any]] = []
    for value, group in groups.items():
        scope_counts = Counter(str(row.get("v3_post_match_scope") or "none") for row in group)
        formal_p50 = _pred_truth(
            group,
            "v3_post_formal_decision_value_p50",
            "v3_truth_formal_decision_value",
        )
        formal_p90 = _pred_truth(
            group,
            "v3_post_formal_decision_value_p90",
            "v3_truth_formal_decision_value",
        )
        q6_p50 = _pred_truth(
            group,
            "v3_post_q6_formal_decision_value_p50",
            "v3_truth_q6_formal_decision_value",
        )
        q6_p90 = _pred_truth(
            group,
            "v3_post_q6_formal_decision_value_p90",
            "v3_truth_q6_formal_decision_value",
        )
        under_formal_p50 = _pred_truth(
            group,
            "v3_under_formal_decision_value_p50",
            "v3_truth_formal_decision_value",
        )
        under_formal_p90 = _pred_truth(
            group,
            "v3_under_formal_decision_value_p90",
            "v3_truth_formal_decision_value",
        )
        under_q6_p50 = _pred_truth(
            group,
            "v3_under_q6_formal_decision_value_p50",
            "v3_truth_q6_formal_decision_value",
        )
        q6_count_p50 = _pred_truth(
            group,
            "v3_post_q6_count_p50",
            "v3_truth_q6_count",
        )
        q6_cells_p50 = _pred_truth(
            group,
            "v3_post_q6_cells_p50",
            "v3_truth_q6_cells",
        )
        q6_value_p50 = _pred_truth(
            group,
            "v3_post_q6_value_p50",
            "v3_truth_q6_raw_value",
        )
        ccv_q6_count_p50 = _pred_truth(
            group,
            "v3_ccv_q6_count_p50",
            "v3_truth_q6_count",
        )
        ccv_q6_cells_p50 = _pred_truth(
            group,
            "v3_ccv_q6_cells_p50",
            "v3_truth_q6_cells",
        )
        resid_q6_count_p50 = _pred_truth(
            group,
            "v3_resid_q6_count_p50",
            "v3_truth_q6_count",
        )
        resid_q6_cells_p50 = _pred_truth(
            group,
            "v3_resid_q6_cells_p50",
            "v3_truth_q6_cells",
        )
        resid_q6_value_p50 = _pred_truth(
            group,
            "v3_resid_q6_value_p50",
            "v3_truth_q6_raw_value",
        )
        resid_gate_q6_count_p50 = _pred_truth(
            group,
            "v3_resid_gate_q6_count_p50",
            "v3_truth_q6_count",
        )
        resid_gate_q6_cells_p50 = _pred_truth(
            group,
            "v3_resid_gate_q6_cells_p50",
            "v3_truth_q6_cells",
        )
        resid_gate_q6_value_p50 = _pred_truth(
            group,
            "v3_resid_gate_q6_value_p50",
            "v3_truth_q6_raw_value",
        )
        q6_count_mae = _mae(q6_count_p50)
        q6_cells_mae = _mae(q6_cells_p50)
        q6_value_mae = _mae(q6_value_p50)
        formal_mae = _mae(formal_p50)
        q6_formal_mae = _mae(q6_p50)
        under_formal_mae = _mae(under_formal_p50)
        under_q6_formal_mae = _mae(under_q6_p50)
        ccv_count_mae = _mae(ccv_q6_count_p50)
        ccv_cells_mae = _mae(ccv_q6_cells_p50)
        resid_count_mae = _mae(resid_q6_count_p50)
        resid_cells_mae = _mae(resid_q6_cells_p50)
        resid_value_mae = _mae(resid_q6_value_p50)
        resid_gate_count_mae = _mae(resid_gate_q6_count_p50)
        resid_gate_cells_mae = _mae(resid_gate_q6_cells_p50)
        resid_gate_value_mae = _mae(resid_gate_q6_value_p50)
        out.append(
            {
                "field": field,
                "value": value,
                "n": len(group),
                "strict": scope_counts.get("strict", 0),
                "summary_likelihood": scope_counts.get("summary_likelihood", 0),
                "q6_projection": scope_counts.get("q6_projection", 0),
                "formal_p50_mae": _round_metric(formal_mae, 1),
                "formal_p50_bias": _round_metric(_bias(formal_p50), 1),
                "formal_p50_below_rate": _round_metric(_below_rate(formal_p50), 6),
                "formal_p50_over_rate": _round_metric(_over_rate(formal_p50), 6),
                "formal_p90_coverage": _round_metric(_coverage_rate(formal_p90), 6),
                "q6_formal_p50_mae": _round_metric(q6_formal_mae, 1),
                "q6_formal_p50_bias": _round_metric(_bias(q6_p50), 1),
                "q6_formal_p50_below_rate": _round_metric(_below_rate(q6_p50), 6),
                "q6_formal_p50_over_rate": _round_metric(_over_rate(q6_p50), 6),
                "q6_formal_p90_coverage": _round_metric(_coverage_rate(q6_p90), 6),
                "q6_count_p50_mae": _round_metric(q6_count_mae, 2),
                "q6_cells_p50_mae": _round_metric(q6_cells_mae, 2),
                "q6_value_p50_mae": _round_metric(q6_value_mae, 1),
                "v3_under_candidate_rate": _round_metric(
                    _mean(1.0 if row.get("v3_under_candidate") else 0.0 for row in group),
                    6,
                ),
                "v3_under_delta_formal_p50_mae": _round_metric(
                    under_formal_mae - formal_mae
                    if under_formal_mae is not None and formal_mae is not None
                    else None,
                    1,
                ),
                "v3_under_formal_p50_below_rate": _round_metric(
                    _below_rate(under_formal_p50),
                    6,
                ),
                "v3_under_formal_p50_over_rate": _round_metric(
                    _over_rate(under_formal_p50),
                    6,
                ),
                "v3_under_formal_p90_coverage": _round_metric(
                    _coverage_rate(under_formal_p90),
                    6,
                ),
                "v3_under_delta_q6_formal_p50_mae": _round_metric(
                    under_q6_formal_mae - q6_formal_mae
                    if under_q6_formal_mae is not None
                    and q6_formal_mae is not None
                    else None,
                    1,
                ),
                "v3_ccv_delta_q6_count_p50_mae": _round_metric(
                    ccv_count_mae - q6_count_mae
                    if ccv_count_mae is not None and q6_count_mae is not None
                    else None,
                    2,
                ),
                "v3_ccv_delta_q6_cells_p50_mae": _round_metric(
                    ccv_cells_mae - q6_cells_mae
                    if ccv_cells_mae is not None and q6_cells_mae is not None
                    else None,
                    2,
                ),
                "v3_resid_delta_q6_count_p50_mae": _round_metric(
                    resid_count_mae - q6_count_mae
                    if resid_count_mae is not None and q6_count_mae is not None
                    else None,
                    2,
                ),
                "v3_resid_delta_q6_cells_p50_mae": _round_metric(
                    resid_cells_mae - q6_cells_mae
                    if resid_cells_mae is not None and q6_cells_mae is not None
                    else None,
                    2,
                ),
                "v3_resid_delta_q6_value_p50_mae": _round_metric(
                    resid_value_mae - q6_value_mae
                    if resid_value_mae is not None and q6_value_mae is not None
                    else None,
                    1,
                ),
                "v3_resid_gate_active_rate": _round_metric(
                    _mean(1.0 if row.get("v3_resid_gate_active") else 0.0 for row in group),
                    6,
                ),
                "v3_resid_gate_delta_q6_count_p50_mae": _round_metric(
                    resid_gate_count_mae - q6_count_mae
                    if resid_gate_count_mae is not None and q6_count_mae is not None
                    else None,
                    2,
                ),
                "v3_resid_gate_delta_q6_cells_p50_mae": _round_metric(
                    resid_gate_cells_mae - q6_cells_mae
                    if resid_gate_cells_mae is not None and q6_cells_mae is not None
                    else None,
                    2,
                ),
                "v3_resid_gate_delta_q6_value_p50_mae": _round_metric(
                    resid_gate_value_mae - q6_value_mae
                    if resid_gate_value_mae is not None and q6_value_mae is not None
                    else None,
                    1,
                ),
            }
        )
    return sorted(
        out,
        key=lambda item: (-(item["formal_p50_mae"] or 0), str(item["value"])),
    )


def _print_table(rows: list[dict[str, Any]], *, top: int) -> None:
    for row in rows[:top]:
        print(
            " ".join(
                (
                    f"{row['field']}={row['value']}",
                    f"n={row['n']}",
                    f"strict={row['strict']}",
                    f"summary_likelihood={row['summary_likelihood']}",
                    f"formal_mae={row['formal_p50_mae']}",
                    f"formal_bias={row['formal_p50_bias']}",
                    f"formal_below={row['formal_p50_below_rate']}",
                    f"formal_over={row['formal_p50_over_rate']}",
                    f"p90_cover={row['formal_p90_coverage']}",
                    f"under_candidate={row['v3_under_candidate_rate']}",
                    f"under_delta_mae={row['v3_under_delta_formal_p50_mae']}",
                    f"under_below={row['v3_under_formal_p50_below_rate']}",
                    f"under_p90_cover={row['v3_under_formal_p90_coverage']}",
                    f"q6_mae={row['q6_formal_p50_mae']}",
                    f"q6_bias={row['q6_formal_p50_bias']}",
                    f"q6_count_mae={row['q6_count_p50_mae']}",
                    f"q6_cells_mae={row['q6_cells_p50_mae']}",
                    f"ccv_count_delta={row['v3_ccv_delta_q6_count_p50_mae']}",
                    f"ccv_cells_delta={row['v3_ccv_delta_q6_cells_p50_mae']}",
                    f"resid_count_delta={row['v3_resid_delta_q6_count_p50_mae']}",
                    f"resid_cells_delta={row['v3_resid_delta_q6_cells_p50_mae']}",
                    f"resid_value_delta={row['v3_resid_delta_q6_value_p50_mae']}",
                    f"resid_gate_active={row['v3_resid_gate_active_rate']}",
                    f"resid_gate_value_delta={row['v3_resid_gate_delta_q6_value_p50_mae']}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize v3 posterior paired metrics by diagnostic slices.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument(
        "--by",
        action="append",
        default=None,
        help="Row field to group by. Can be repeated. Defaults to round/map/scope.",
    )
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    parser.add_argument("--posterior-trials", type=int, default=512)
    parser.add_argument("--posterior-seed", type=int, default=0)
    args = parser.parse_args(argv)

    tables = load_monitor_tables()
    rows, errors = evaluate_paths(
        args.paths or _default_paths(),
        tables=tables,
        underestimate_repair_entries=load_underestimate_repair_entries(
            _default_underestimate_repair_path()
        ),
        posterior_trials=args.posterior_trials,
        posterior_seed=args.posterior_seed,
    )
    fields = tuple(args.by or DEFAULT_SLICE_FIELDS)
    slices = [item for field in fields for item in summarize_slice(rows, field)]
    result = {"errors": errors, "slices": slices}
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if errors:
            print(f"errors={len(errors)}")
        for field in fields:
            print(f"== {field} ==")
            _print_table([row for row in slices if row["field"] == field], top=args.top)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
