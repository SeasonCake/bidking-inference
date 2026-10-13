"""Audit v3 posterior quality by map, sample coverage, and evidence density."""

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
    _default_calibration_path,
    _default_underestimate_repair_path,
    _default_paths,
    _float_or_none,
    _round_metric,
    evaluate_paths,
    load_prior_calibration_entries,
    load_underestimate_repair_entries,
    load_monitor_tables,
)


def _mean(values: Iterable[float]) -> float | None:
    seq = tuple(values)
    if not seq:
        return None
    return sum(seq) / len(seq)


def _quantile(values: Iterable[float], probability: float) -> float | None:
    seq = sorted(values)
    if not seq:
        return None
    if len(seq) == 1:
        return seq[0]
    position = (len(seq) - 1) * float(probability)
    low = int(position)
    high = min(low + 1, len(seq) - 1)
    fraction = position - low
    return seq[low] * (1.0 - fraction) + seq[high] * fraction


def _coverage_rate(pairs: Iterable[tuple[float, float]]) -> float | None:
    return _mean(1.0 if truth <= pred else 0.0 for pred, truth in pairs)


def _map_key(row: dict[str, Any]) -> int | str:
    for key in ("map_id", "v3_truth_map_id", "v3_prior_map_id"):
        value = row.get(key)
        if value not in (None, ""):
            try:
                return int(value)
            except (TypeError, ValueError, OverflowError):
                return str(value)
    return "unknown"


def _map_name(row: dict[str, Any]) -> str:
    for key in ("v3_prior_map_name", "v3_post_map_name"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _round_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[int] = Counter()
    for row in rows:
        value = row.get("round")
        if value is None:
            continue
        try:
            counts[int(value)] += 1
        except (TypeError, ValueError, OverflowError):
            continue
    return {f"R{round_no}": counts[round_no] for round_no in sorted(counts)}


def _top_counts(
    rows: Iterable[dict[str, Any]],
    field: str,
    *,
    top: int = 5,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        value = row.get(field)
        label = str(value) if value not in (None, "") else "unknown"
        counts[label] += 1
    return {
        label: count
        for label, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:top]
    }


def _paired_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_decision_available")
        and row.get("v3_post_ready")
        and _float_or_none(row.get("v3_post_formal_decision_value_p50")) is not None
        and _float_or_none(row.get("v3_truth_formal_decision_value")) is not None
    ]


def _metric_pairs(
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


def _rate(rows: Iterable[dict[str, Any]], predicate: Any) -> float | None:
    seq = tuple(rows)
    if not seq:
        return None
    return sum(1 for row in seq if predicate(row)) / len(seq)


def _avg_numeric(rows: Iterable[dict[str, Any]], key: str) -> float | None:
    values = tuple(_float_or_none(row.get(key)) or 0.0 for row in rows)
    return _mean(values)


def _audit_flags(row: dict[str, Any]) -> tuple[str, ...]:
    flags: list[str] = []
    if int(row["sessions"]) < 15:
        flags.append("few_sessions")
    if int(row["paired_windows"]) < 50:
        flags.append("few_windows")
    if float(row["top3_abs_error_share"] or 0.0) >= 0.25:
        flags.append("top3_heavy")
    if float(row["strict_rate"] or 0.0) < 0.30:
        flags.append("mostly_fallback")
    if float(row["public_total_rate"] or 0.0) < 0.20:
        flags.append("little_public_total")
    if (
        row["formal_p50_bias"] is not None
        and row["formal_p50_mae"] is not None
        and float(row["formal_p50_bias"]) <= -0.50 * float(row["formal_p50_mae"])
    ):
        flags.append("systemic_under")
    if float(row["formal_p50_over_rate"] or 0.0) >= 0.60:
        flags.append("high_over_rate")
    if float(row["q6_floor_rate"] or 0.0) < 0.20:
        flags.append("weak_q6_evidence")
    return tuple(flags)


def summarize_maps(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[int | str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_map_key(row)].append(row)

    out: list[dict[str, Any]] = []
    for map_id, group in groups.items():
        ready = [row for row in group if row.get("status") == "ready"]
        paired = _paired_rows(group)
        if not paired:
            continue
        scope_counts = Counter(str(row.get("v3_post_match_scope") or "none") for row in paired)
        status_counts = Counter(str(row.get("status") or "none") for row in group)
        formal_p50 = _metric_pairs(
            paired,
            "v3_post_formal_decision_value_p50",
            "v3_truth_formal_decision_value",
        )
        formal_p90 = _metric_pairs(
            paired,
            "v3_post_formal_decision_value_p90",
            "v3_truth_formal_decision_value",
        )
        cal_p50 = _metric_pairs(
            paired,
            "v3_cal_formal_decision_value_p50",
            "v3_truth_formal_decision_value",
        )
        cal_p90 = _metric_pairs(
            paired,
            "v3_cal_formal_decision_value_p90",
            "v3_truth_formal_decision_value",
        )
        under_p50 = _metric_pairs(
            paired,
            "v3_under_formal_decision_value_p50",
            "v3_truth_formal_decision_value",
        )
        under_p90 = _metric_pairs(
            paired,
            "v3_under_formal_decision_value_p90",
            "v3_truth_formal_decision_value",
        )
        under_q6_p50 = _metric_pairs(
            paired,
            "v3_under_q6_formal_decision_value_p50",
            "v3_truth_q6_formal_decision_value",
        )
        q6_p50 = _metric_pairs(
            paired,
            "v3_post_q6_formal_decision_value_p50",
            "v3_truth_q6_formal_decision_value",
        )
        q6_count_p50 = _metric_pairs(
            paired,
            "v3_post_q6_count_p50",
            "v3_truth_q6_count",
        )
        q6_cells_p50 = _metric_pairs(
            paired,
            "v3_post_q6_cells_p50",
            "v3_truth_q6_cells",
        )
        ccv_q6_count_p50 = _metric_pairs(
            paired,
            "v3_ccv_q6_count_p50",
            "v3_truth_q6_count",
        )
        ccv_q6_cells_p50 = _metric_pairs(
            paired,
            "v3_ccv_q6_cells_p50",
            "v3_truth_q6_cells",
        )
        q6_value_p50 = _metric_pairs(
            paired,
            "v3_post_q6_value_p50",
            "v3_truth_q6_raw_value",
        )
        resid_q6_count_p50 = _metric_pairs(
            paired,
            "v3_resid_q6_count_p50",
            "v3_truth_q6_count",
        )
        resid_q6_cells_p50 = _metric_pairs(
            paired,
            "v3_resid_q6_cells_p50",
            "v3_truth_q6_cells",
        )
        resid_q6_value_p50 = _metric_pairs(
            paired,
            "v3_resid_q6_value_p50",
            "v3_truth_q6_raw_value",
        )
        resid_gate_q6_count_p50 = _metric_pairs(
            paired,
            "v3_resid_gate_q6_count_p50",
            "v3_truth_q6_count",
        )
        resid_gate_q6_cells_p50 = _metric_pairs(
            paired,
            "v3_resid_gate_q6_cells_p50",
            "v3_truth_q6_cells",
        )
        resid_gate_q6_value_p50 = _metric_pairs(
            paired,
            "v3_resid_gate_q6_value_p50",
            "v3_truth_q6_raw_value",
        )
        errors = [pred - truth for pred, truth in formal_p50]
        abs_errors = sorted((abs(error) for error in errors), reverse=True)
        abs_error_sum = sum(abs_errors)
        top3_share = (
            sum(abs_errors[:3]) / abs_error_sum if abs_error_sum > 0.0 else None
        )
        sessions = {
            str(row.get("session_id"))
            for row in group
            if row.get("session_id") not in (None, "")
        }
        formal_mae = _mean(abs(error) for error in errors)
        formal_bias = _mean(errors)
        formal_below = _mean(1.0 if error < 0 else 0.0 for error in errors)
        formal_over = _mean(1.0 if error > 0 else 0.0 for error in errors)
        cal_errors = [pred - truth for pred, truth in cal_p50]
        cal_mae = _mean(abs(error) for error in cal_errors)
        under_errors = [pred - truth for pred, truth in under_p50]
        under_mae = _mean(abs(error) for error in under_errors)
        under_q6_errors = [pred - truth for pred, truth in under_q6_p50]
        under_q6_mae = _mean(abs(error) for error in under_q6_errors)
        q6_errors = [pred - truth for pred, truth in q6_p50]
        q6_count_errors = [pred - truth for pred, truth in q6_count_p50]
        q6_cells_errors = [pred - truth for pred, truth in q6_cells_p50]
        ccv_q6_count_errors = [pred - truth for pred, truth in ccv_q6_count_p50]
        ccv_q6_cells_errors = [pred - truth for pred, truth in ccv_q6_cells_p50]
        q6_value_errors = [pred - truth for pred, truth in q6_value_p50]
        resid_q6_count_errors = [pred - truth for pred, truth in resid_q6_count_p50]
        resid_q6_cells_errors = [pred - truth for pred, truth in resid_q6_cells_p50]
        resid_q6_value_errors = [pred - truth for pred, truth in resid_q6_value_p50]
        resid_gate_q6_count_errors = [
            pred - truth for pred, truth in resid_gate_q6_count_p50
        ]
        resid_gate_q6_cells_errors = [
            pred - truth for pred, truth in resid_gate_q6_cells_p50
        ]
        resid_gate_q6_value_errors = [
            pred - truth for pred, truth in resid_gate_q6_value_p50
        ]
        q6_count_mae = _mean(abs(error) for error in q6_count_errors)
        q6_cells_mae = _mean(abs(error) for error in q6_cells_errors)
        q6_value_mae = _mean(abs(error) for error in q6_value_errors)
        ccv_q6_count_mae = _mean(abs(error) for error in ccv_q6_count_errors)
        ccv_q6_cells_mae = _mean(abs(error) for error in ccv_q6_cells_errors)
        resid_q6_count_mae = _mean(abs(error) for error in resid_q6_count_errors)
        resid_q6_cells_mae = _mean(abs(error) for error in resid_q6_cells_errors)
        resid_q6_value_mae = _mean(abs(error) for error in resid_q6_value_errors)
        resid_gate_q6_count_mae = _mean(
            abs(error) for error in resid_gate_q6_count_errors
        )
        resid_gate_q6_cells_mae = _mean(
            abs(error) for error in resid_gate_q6_cells_errors
        )
        resid_gate_q6_value_mae = _mean(
            abs(error) for error in resid_gate_q6_value_errors
        )
        result = {
            "map_id": map_id,
            "map_name": _map_name(paired[0]),
            "map_family": paired[0].get("map_family"),
            "sessions": len(sessions),
            "windows": len(group),
            "ready_windows": len(ready),
            "no_state_windows": status_counts.get("no_state", 0),
            "paired_windows": len(paired),
            "rounds": _round_counts(ready),
            "heroes": _top_counts(ready, "hero"),
            "evidence_stages": _top_counts(ready, "evidence_stage"),
            "information_density": _top_counts(ready, "information_density_band"),
            "evidence_profiles": _top_counts(ready, "evidence_profile_key"),
            "hero_map_evidence_profiles": _top_counts(
                ready,
                "hero_map_evidence_profile",
            ),
            "strict_rate": _round_metric(scope_counts.get("strict", 0) / len(paired), 6),
            "summary_likelihood_rate": _round_metric(
                scope_counts.get("summary_likelihood", 0) / len(paired),
                6,
            ),
            "formal_p50_mae": _round_metric(formal_mae, 1),
            "formal_p50_bias": _round_metric(formal_bias, 1),
            "formal_p50_below_rate": _round_metric(formal_below, 6),
            "formal_p50_over_rate": _round_metric(formal_over, 6),
            "formal_p90_coverage": _round_metric(_coverage_rate(formal_p90), 6),
            "v3_cal_active_rate": _round_metric(
                _rate(paired, lambda row: bool(row.get("v3_cal_active"))),
                6,
            ),
            "v3_cal_scale": _round_metric(
                _mean(
                    _float_or_none(row.get("v3_cal_scale")) or 1.0
                    for row in paired
                ),
                6,
            ),
            "v3_cal_formal_p50_mae": _round_metric(cal_mae, 1),
            "v3_cal_delta_formal_p50_mae": _round_metric(
                cal_mae - formal_mae
                if cal_mae is not None and formal_mae is not None
                else None,
                1,
            ),
            "v3_cal_formal_p50_bias": _round_metric(_mean(cal_errors), 1),
            "v3_cal_formal_p50_below_rate": _round_metric(
                _mean(1.0 if error < 0 else 0.0 for error in cal_errors),
                6,
            ),
            "v3_cal_formal_p90_coverage": _round_metric(
                _coverage_rate(cal_p90),
                6,
            ),
            "v3_under_candidate_rate": _round_metric(
                _rate(paired, lambda row: bool(row.get("v3_under_candidate"))),
                6,
            ),
            "v3_under_scale": _round_metric(
                _mean(
                    _float_or_none(row.get("v3_under_scale")) or 1.0
                    for row in paired
                ),
                6,
            ),
            "v3_under_formal_p50_mae": _round_metric(under_mae, 1),
            "v3_under_delta_formal_p50_mae": _round_metric(
                under_mae - formal_mae
                if under_mae is not None and formal_mae is not None
                else None,
                1,
            ),
            "v3_under_formal_p50_bias": _round_metric(_mean(under_errors), 1),
            "v3_under_formal_p50_below_rate": _round_metric(
                _mean(1.0 if error < 0 else 0.0 for error in under_errors),
                6,
            ),
            "v3_under_formal_p90_coverage": _round_metric(
                _coverage_rate(under_p90),
                6,
            ),
            "v3_under_q6_formal_p50_mae": _round_metric(under_q6_mae, 1),
            "v3_under_delta_q6_formal_p50_mae": _round_metric(
                under_q6_mae - _mean(abs(error) for error in q6_errors)
                if under_q6_mae is not None and q6_errors
                else None,
                1,
            ),
            "q6_formal_p50_mae": _round_metric(_mean(abs(error) for error in q6_errors), 1),
            "q6_formal_p50_bias": _round_metric(_mean(q6_errors), 1),
            "q6_count_p50_mae": _round_metric(q6_count_mae, 2),
            "q6_cells_p50_mae": _round_metric(q6_cells_mae, 2),
            "v3_ccv_likelihood_rate": _round_metric(
                _rate(
                    paired,
                    lambda row: row.get("v3_ccv_match_scope") == "ccv_likelihood",
                ),
                6,
            ),
            "v3_ccv_q6_count_p50_mae": _round_metric(ccv_q6_count_mae, 2),
            "v3_ccv_delta_q6_count_p50_mae": _round_metric(
                ccv_q6_count_mae - q6_count_mae
                if ccv_q6_count_mae is not None and q6_count_mae is not None
                else None,
                2,
            ),
            "v3_ccv_q6_cells_p50_mae": _round_metric(ccv_q6_cells_mae, 2),
            "v3_ccv_delta_q6_cells_p50_mae": _round_metric(
                ccv_q6_cells_mae - q6_cells_mae
                if ccv_q6_cells_mae is not None and q6_cells_mae is not None
                else None,
                2,
            ),
            "q6_value_p50_mae": _round_metric(q6_value_mae, 1),
            "v3_resid_likelihood_rate": _round_metric(
                _rate(
                    paired,
                    lambda row: row.get("v3_resid_match_scope")
                    == "residual_likelihood",
                ),
                6,
            ),
            "v3_resid_q6_count_p50_mae": _round_metric(resid_q6_count_mae, 2),
            "v3_resid_delta_q6_count_p50_mae": _round_metric(
                resid_q6_count_mae - q6_count_mae
                if resid_q6_count_mae is not None and q6_count_mae is not None
                else None,
                2,
            ),
            "v3_resid_q6_cells_p50_mae": _round_metric(resid_q6_cells_mae, 2),
            "v3_resid_delta_q6_cells_p50_mae": _round_metric(
                resid_q6_cells_mae - q6_cells_mae
                if resid_q6_cells_mae is not None and q6_cells_mae is not None
                else None,
                2,
            ),
            "v3_resid_q6_value_p50_mae": _round_metric(resid_q6_value_mae, 1),
            "v3_resid_delta_q6_value_p50_mae": _round_metric(
                resid_q6_value_mae - q6_value_mae
                if resid_q6_value_mae is not None and q6_value_mae is not None
                else None,
                1,
            ),
            "v3_resid_gate_active_rate": _round_metric(
                _rate(paired, lambda row: bool(row.get("v3_resid_gate_active"))),
                6,
            ),
            "v3_resid_gate_q6_count_p50_mae": _round_metric(
                resid_gate_q6_count_mae,
                2,
            ),
            "v3_resid_gate_delta_q6_count_p50_mae": _round_metric(
                resid_gate_q6_count_mae - q6_count_mae
                if resid_gate_q6_count_mae is not None and q6_count_mae is not None
                else None,
                2,
            ),
            "v3_resid_gate_q6_cells_p50_mae": _round_metric(
                resid_gate_q6_cells_mae,
                2,
            ),
            "v3_resid_gate_delta_q6_cells_p50_mae": _round_metric(
                resid_gate_q6_cells_mae - q6_cells_mae
                if resid_gate_q6_cells_mae is not None and q6_cells_mae is not None
                else None,
                2,
            ),
            "v3_resid_gate_q6_value_p50_mae": _round_metric(
                resid_gate_q6_value_mae,
                1,
            ),
            "v3_resid_gate_delta_q6_value_p50_mae": _round_metric(
                resid_gate_q6_value_mae - q6_value_mae
                if resid_gate_q6_value_mae is not None and q6_value_mae is not None
                else None,
                1,
            ),
            "truth_p50": _round_metric(
                _quantile((truth for _, truth in formal_p50), 0.50),
                1,
            ),
            "truth_p90": _round_metric(
                _quantile((truth for _, truth in formal_p50), 0.90),
                1,
            ),
            "truth_max": _round_metric(
                max((truth for _, truth in formal_p50), default=None),
                1,
            ),
            "top3_abs_error_share": _round_metric(top3_share, 6),
            "avg_prior_state_count": _round_metric(_avg_numeric(ready, "prior_state_count"), 2),
            "avg_numeric_constraints": _round_metric(_avg_numeric(ready, "numeric_constraints"), 2),
            "avg_item_anchors": _round_metric(_avg_numeric(ready, "item_anchors"), 2),
            "avg_shape_anchors": _round_metric(_avg_numeric(ready, "shape_anchors"), 2),
            "avg_quality_floor_anchors": _round_metric(
                _avg_numeric(ready, "quality_floor_anchors"),
                2,
            ),
            "public_total_rate": _round_metric(
                _rate(
                    ready,
                    lambda row: row.get("v3_summary_session_total_cells_exact")
                    is not None,
                ),
                6,
            ),
            "q6_exact_rate": _round_metric(
                _rate(
                    ready,
                    lambda row: row.get("v3_summary_q6_count_exact") is not None
                    or row.get("v3_summary_q6_cells_exact") is not None
                    or row.get("v3_summary_q6_value_exact") is not None,
                ),
                6,
            ),
            "q6_floor_rate": _round_metric(
                _rate(
                    ready,
                    lambda row: bool(row.get("v3_summary_q6_count_floor"))
                    or bool(row.get("v3_summary_q6_cells_floor"))
                    or bool(row.get("v3_summary_q6_value_floor")),
                ),
                6,
            ),
        }
        result["flags"] = list(_audit_flags(result))
        out.append(result)
    return sorted(
        out,
        key=lambda item: (-(item["formal_p50_mae"] or 0.0), str(item["map_id"])),
    )


def _print_table(rows: list[dict[str, Any]], *, top: int) -> None:
    for row in rows[:top]:
        rounds = ",".join(f"{key}:{value}" for key, value in row["rounds"].items())
        heroes = ",".join(f"{key}:{value}" for key, value in row["heroes"].items())
        density = ",".join(
            f"{key}:{value}" for key, value in row["information_density"].items()
        )
        profiles = ",".join(
            f"{key}:{value}" for key, value in row["evidence_profiles"].items()
        )
        flags = "+".join(row["flags"]) if row["flags"] else "normalish"
        print(
            " ".join(
                (
                    f"map_id={row['map_id']}",
                    f"name={row['map_name'] or '-'}",
                    f"family={row['map_family']}",
                    f"sessions={row['sessions']}",
                    f"ready={row['ready_windows']}/{row['windows']}",
                    f"paired={row['paired_windows']}",
                    f"rounds={rounds or '-'}",
                    f"heroes={heroes or '-'}",
                    f"density={density or '-'}",
                    f"profiles={profiles or '-'}",
                    f"strict={row['strict_rate']}",
                    f"mae={row['formal_p50_mae']}",
                    f"bias={row['formal_p50_bias']}",
                    f"below={row['formal_p50_below_rate']}",
                    f"p90_cover={row['formal_p90_coverage']}",
                    f"cal_active={row['v3_cal_active_rate']}",
                    f"cal_mae={row['v3_cal_formal_p50_mae']}",
                    f"cal_delta={row['v3_cal_delta_formal_p50_mae']}",
                    f"under_candidate={row['v3_under_candidate_rate']}",
                    f"under_delta={row['v3_under_delta_formal_p50_mae']}",
                    f"under_below={row['v3_under_formal_p50_below_rate']}",
                    f"under_p90_cover={row['v3_under_formal_p90_coverage']}",
                    f"q6_mae={row['q6_formal_p50_mae']}",
                    f"q6_count_mae={row['q6_count_p50_mae']}",
                    f"q6_cells_mae={row['q6_cells_p50_mae']}",
                    f"ccv_rate={row['v3_ccv_likelihood_rate']}",
                    f"ccv_count_delta={row['v3_ccv_delta_q6_count_p50_mae']}",
                    f"ccv_cells_delta={row['v3_ccv_delta_q6_cells_p50_mae']}",
                    f"resid_rate={row['v3_resid_likelihood_rate']}",
                    f"resid_count_delta={row['v3_resid_delta_q6_count_p50_mae']}",
                    f"resid_cells_delta={row['v3_resid_delta_q6_cells_p50_mae']}",
                    f"resid_value_delta={row['v3_resid_delta_q6_value_p50_mae']}",
                    f"resid_gate_active={row['v3_resid_gate_active_rate']}",
                    f"resid_gate_count_delta={row['v3_resid_gate_delta_q6_count_p50_mae']}",
                    f"resid_gate_cells_delta={row['v3_resid_gate_delta_q6_cells_p50_mae']}",
                    f"resid_gate_value_delta={row['v3_resid_gate_delta_q6_value_p50_mae']}",
                    f"top3_abs={row['top3_abs_error_share']}",
                    f"public_total={row['public_total_rate']}",
                    f"q6_floor={row['q6_floor_rate']}",
                    f"flags={flags}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit v3 posterior map quality and sample/evidence coverage.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    parser.add_argument("--posterior-trials", type=int, default=512)
    parser.add_argument("--posterior-seed", type=int, default=0)
    args = parser.parse_args(argv)

    rows, errors = evaluate_paths(
        args.paths or _default_paths(),
        tables=load_monitor_tables(),
        calibration_entries=load_prior_calibration_entries(
            _default_calibration_path()
        ),
        underestimate_repair_entries=load_underestimate_repair_entries(
            _default_underestimate_repair_path()
        ),
        posterior_trials=args.posterior_trials,
        posterior_seed=args.posterior_seed,
    )
    result = {"errors": errors, "maps": summarize_maps(rows)}
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if errors:
            print(f"errors={len(errors)}")
        _print_table(result["maps"], top=args.top)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
