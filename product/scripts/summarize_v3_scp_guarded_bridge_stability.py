"""Audit guarded v3 settlement bridge stability across trials and seeds."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

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
    _default_paths,
    _default_settlement_count_prior_path,
    _default_tail_value_review_path,
    _default_underestimate_repair_path,
    _round_metric,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
    load_settlement_count_prior_entries,
    load_tail_value_review_entries,
    load_underestimate_repair_entries,
)
from summarize_v3_scp_count_value_bridge_holdout import (  # noqa: E402
    DEFAULT_GROUP_FIELD,
)
from summarize_v3_scp_guarded_bridge_holdout import (  # noqa: E402
    summarize_guarded_holdout,
)

DEFAULT_POSTERIOR_TRIALS = (64,)
DEFAULT_POSTERIOR_SEEDS = (0, 1)
DEFAULT_REQUIRED_GROUPS = ("2506",)
DEFAULT_CACHE_DIR = ROOT / ".tmp" / "codex" / "v3_scp_guarded_bridge_stability"
CACHE_SCHEMA_VERSION = 3


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _group_set(counts: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not counts:
        return ()
    return tuple(sorted(str(key) for key in counts if _as_int(counts[key]) > 0))


def _format_groups(groups: Iterable[str]) -> str:
    return ",".join(groups) or "-"


def _format_mapping(mapping: Mapping[str, Any]) -> str:
    return ";".join(f"{key}={value}" for key, value in mapping.items()) or "-"


def _selected_signature(counts: Mapping[str, Any] | None) -> str:
    if not counts:
        return "-"
    parts = [
        f"{key}:{_as_int(value)}"
        for key, value in sorted(counts.items())
        if _as_int(value) > 0
    ]
    return ",".join(parts) or "-"


def _run_row(run: Mapping[str, Any]) -> dict[str, Any]:
    candidate = run.get("candidate_only") or {}
    selected_counts = run.get("selected_group_fold_counts") or {}
    return {
        "posterior_trials": _as_int(run.get("posterior_trials")),
        "posterior_seed": _as_int(run.get("posterior_seed")),
        "overall_status": str(run.get("overall_status") or "unknown"),
        "selected_groups": list(_group_set(selected_counts)),
        "selected_group_fold_counts": dict(sorted(selected_counts.items())),
        "selected_signature": _selected_signature(selected_counts),
        "cache_hit": bool(run.get("cache_hit")),
        "applied_rows": _as_int(candidate.get("applied_rows")),
        "candidate_rows": _as_int(candidate.get("candidate_rows")),
        "delta_formal_p50_mae": candidate.get("delta_formal_p50_mae"),
        "delta_formal_p90_coverage": candidate.get("delta_formal_p90_coverage"),
        "bridge_formal_p50_over_rate": candidate.get(
            "bridge_formal_p50_over_rate"
        ),
        "applied_hurts": list(run.get("applied_hurts") or ()),
        "guard_status_counts": dict(run.get("guard_status_counts") or {}),
        "selected_group_guard_summary": list(
            run.get("selected_group_guard_summary") or ()
        ),
        "selected_group_fold_support": list(
            run.get("selected_group_fold_support") or ()
        ),
        "selected_group_support": list(run.get("selected_group_support") or ()),
    }


def _stable_groups(run_rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    if not run_rows:
        return ()
    groups = set(str(group) for group in run_rows[0].get("selected_groups") or ())
    for row in run_rows[1:]:
        groups &= set(str(group) for group in row.get("selected_groups") or ())
    return tuple(sorted(groups))


def _union_groups(run_rows: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    groups: set[str] = set()
    for row in run_rows:
        groups.update(str(group) for group in row.get("selected_groups") or ())
    return tuple(sorted(groups))


def _selected_support_rows(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    support_rows = list(row.get("selected_group_support") or ())
    if support_rows:
        return [
            {**dict(support), "support_source": "reported"}
            for support in support_rows
        ]
    selected_counts = row.get("selected_group_fold_counts") or {}
    selected_groups = tuple(_group_set(selected_counts))
    if not selected_groups:
        return []
    single_group = len(selected_groups) == 1
    return [
        {
            "group": group,
            "selected_folds": _as_int(selected_counts.get(group)),
            "sessions": None,
            "metric_rows": None,
            "candidate_rows": row.get("candidate_rows") if single_group else None,
            "applied_rows": row.get("applied_rows") if single_group else None,
            "sample_limited_rows": None,
            "support_source": "fallback_single_group" if single_group else "missing",
        }
        for group in selected_groups
    ]


def _has_missing_selected_support(row: Mapping[str, Any]) -> bool:
    return any(
        str(support.get("support_source") or "") == "missing"
        for support in _selected_support_rows(row)
    )


def _status_for_runs(
    run_rows: Sequence[Mapping[str, Any]],
    *,
    required_selected_groups: tuple[str, ...],
    min_applied_rows: int,
    require_all_watch: bool,
) -> tuple[str, list[str]]:
    if not run_rows:
        return "sample_limited", ["no_runs"]

    reasons: list[str] = []
    if any(row.get("applied_hurts") for row in run_rows):
        reasons.append("applied_hurts_present")
    if require_all_watch and any(
        row.get("overall_status") != "watch" for row in run_rows
    ):
        reasons.append("non_watch_run")
    if any(_has_missing_selected_support(row) for row in run_rows):
        reasons.append("selected_group_support_missing")

    required_set = set(required_selected_groups)
    if required_set:
        for row in run_rows:
            selected = set(str(group) for group in row.get("selected_groups") or ())
            if selected != required_set:
                reasons.append("selected_group_drift")
                break
    else:
        signatures = {str(row.get("selected_signature") or "-") for row in run_rows}
        if len(signatures) > 1:
            reasons.append("selected_group_drift")

    if all(_as_int(row.get("applied_rows")) <= 0 for row in run_rows):
        reasons.append("no_applied_rows")
    min_applied = min(_as_int(row.get("applied_rows")) for row in run_rows)
    if min_applied < int(min_applied_rows):
        reasons.append("low_applied_rows")

    if "applied_hurts_present" in reasons:
        return "blocked_applied_hurt", reasons
    if "non_watch_run" in reasons:
        return "blocked_run_status", reasons
    if "selected_group_drift" in reasons:
        return "blocked_selected_group_drift", reasons
    if "selected_group_support_missing" in reasons:
        return "blocked_missing_support", reasons
    if "no_applied_rows" in reasons:
        return "sample_limited", reasons
    if "low_applied_rows" in reasons:
        return "blocked_low_support", reasons
    return "watch", ["all_runs_stable"]


def _selected_group_support_summary(
    run_rows: Sequence[Mapping[str, Any]],
    *,
    min_applied_rows: int,
) -> list[dict[str, Any]]:
    by_group: dict[str, list[dict[str, Any]]] = {}
    for row in run_rows:
        support_rows = _selected_support_rows(row)
        hurt_groups = set(str(group) for group in row.get("applied_hurts") or ())
        for support in support_rows:
            group = str(support.get("group") or "unknown")
            by_group.setdefault(group, []).append(
                {
                    "posterior_trials": row.get("posterior_trials"),
                    "posterior_seed": row.get("posterior_seed"),
                    "run_status": row.get("overall_status"),
                    "selected_signature": row.get("selected_signature"),
                    "selected_folds": _as_int(support.get("selected_folds")),
                    "sessions": support.get("sessions"),
                    "metric_rows": support.get("metric_rows"),
                    "candidate_rows": support.get("candidate_rows"),
                    "applied_rows": support.get("applied_rows"),
                    "support_source": support.get("support_source"),
                    "applied_hurt": group in hurt_groups,
                }
            )
    out: list[dict[str, Any]] = []
    required = int(min_applied_rows)
    for group, runs in sorted(by_group.items()):
        applied_values = [
            _as_int(row.get("applied_rows"))
            for row in runs
            if row.get("applied_rows") is not None
        ]
        candidate_values = [
            _as_int(row.get("candidate_rows"))
            for row in runs
            if row.get("candidate_rows") is not None
        ]
        metric_values = [
            _as_int(row.get("metric_rows"))
            for row in runs
            if row.get("metric_rows") is not None
        ]
        session_values = [
            _as_int(row.get("sessions"))
            for row in runs
            if row.get("sessions") is not None
        ]
        selected_fold_values = [_as_int(row.get("selected_folds")) for row in runs]
        min_applied = min(applied_values) if applied_values else 0
        out.append(
            {
                "group": group,
                "run_count": len(runs),
                "required_applied_rows": required,
                "selected_folds": sum(selected_fold_values),
                "min_applied_rows": min_applied,
                "max_applied_rows": max(applied_values) if applied_values else 0,
                "min_applied_gap": max(0, required - min_applied),
                "min_candidate_rows": (
                    min(candidate_values) if candidate_values else None
                ),
                "min_metric_rows": min(metric_values) if metric_values else None,
                "min_sessions": min(session_values) if session_values else None,
                "hurt_run_count": sum(1 for row in runs if row.get("applied_hurt")),
                "missing_support_runs": sum(
                    1 for row in runs if row.get("support_source") == "missing"
                ),
                "run_status_counts": dict(
                    sorted(
                        Counter(
                            str(row.get("run_status") or "unknown")
                            for row in runs
                        ).items()
                    )
                ),
                "runs": runs,
            }
        )
    return out


def _support_gap_summary(
    support_summary: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in support_summary
        if int(row.get("min_applied_gap") or 0) > 0
        or int(row.get("missing_support_runs") or 0) > 0
    ]


def _selected_group_guard_summary(
    run_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_group: dict[str, list[dict[str, Any]]] = {}
    for row in run_rows:
        guard_rows = list(row.get("selected_group_guard_summary") or ())
        for guard in guard_rows:
            group = str(guard.get("group") or "unknown")
            by_group.setdefault(group, []).append(
                {
                    "posterior_trials": row.get("posterior_trials"),
                    "posterior_seed": row.get("posterior_seed"),
                    "fold": guard.get("fold"),
                    "guard_status": guard.get("guard_status"),
                    "guard_applied_sessions": guard.get(
                        "guard_applied_sessions"
                    ),
                    "guard_delta_formal_p50_mae": guard.get(
                        "guard_delta_formal_p50_mae"
                    ),
                    "guard_delta_formal_p90_coverage": guard.get(
                        "guard_delta_formal_p90_coverage"
                    ),
                    "guard_bridge_formal_p50_over_rate": guard.get(
                        "guard_bridge_formal_p50_over_rate"
                    ),
                    "guard_inner_status_counts": guard.get(
                        "guard_inner_status_counts"
                    ),
                    "holdout_applied_rows": guard.get("applied_rows"),
                    "holdout_sessions": guard.get("sessions"),
                }
            )
    out: list[dict[str, Any]] = []
    for group, runs in sorted(by_group.items()):
        guard_sessions = [
            _as_int(row.get("guard_applied_sessions"))
            for row in runs
            if row.get("guard_applied_sessions") is not None
        ]
        guard_delta = [
            value
            for value in (
                _as_float(row.get("guard_delta_formal_p50_mae"))
                for row in runs
            )
            if value is not None
        ]
        guard_over = [
            value
            for value in (
                _as_float(row.get("guard_bridge_formal_p50_over_rate"))
                for row in runs
            )
            if value is not None
        ]
        status_counts = Counter(
            str(row.get("guard_status") or "unknown") for row in runs
        )
        out.append(
            {
                "group": group,
                "run_count": len({row.get("posterior_seed") for row in runs}),
                "selected_folds": len(runs),
                "guard_status_counts": dict(sorted(status_counts.items())),
                "min_guard_applied_sessions": (
                    min(guard_sessions) if guard_sessions else None
                ),
                "max_guard_delta_formal_p50_mae": (
                    max(guard_delta) if guard_delta else None
                ),
                "max_guard_bridge_formal_p50_over_rate": (
                    max(guard_over) if guard_over else None
                ),
                "runs": runs,
            }
        )
    return out


def _selected_group_instability_summary(
    *,
    support_summary: Iterable[Mapping[str, Any]],
    guard_summary: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    support_by_group = {
        str(row.get("group") or "unknown"): row for row in support_summary
    }
    guard_by_group = {
        str(row.get("group") or "unknown"): row for row in guard_summary
    }
    groups = sorted(set(support_by_group) | set(guard_by_group))
    out: list[dict[str, Any]] = []
    for group in groups:
        support = support_by_group.get(group, {})
        guard = guard_by_group.get(group, {})
        hurt_rows = _as_int(support.get("hurt_run_count"))
        missing_support = _as_int(support.get("missing_support_runs"))
        support_gap = _as_int(support.get("min_applied_gap"))
        guard_status_counts = guard.get("guard_status_counts") or {}
        watch_guard_folds = _as_int(guard_status_counts.get("watch_train_guard"))
        reasons: list[str] = []
        if missing_support > 0:
            status = "blocked_missing_support"
            reasons.append("selected_group_support_missing")
        elif hurt_rows > 0 and watch_guard_folds > 0:
            status = "blocked_train_holdout_instability"
            reasons.append("train_guard_watch_but_holdout_hurt")
        elif hurt_rows > 0:
            status = "blocked_holdout_hurt"
            reasons.append("holdout_hurt")
        elif support_gap > 0:
            status = "blocked_support_depth_gap"
            reasons.append("min_applied_rows_below_required")
        elif watch_guard_folds > 0:
            status = "watch_train_guard_stable"
            reasons.append("train_guard_watch_no_hurt_or_gap")
        else:
            status = "watch_support_only"
            reasons.append("support_ok_without_train_guard_metrics")
        out.append(
            {
                "group": group,
                "status": status,
                "reasons": reasons,
                "support_run_count": support.get("run_count"),
                "support_selected_folds": support.get("selected_folds"),
                "support_min_applied_rows": support.get("min_applied_rows"),
                "support_required_applied_rows": support.get(
                    "required_applied_rows"
                ),
                "support_min_applied_gap": support_gap,
                "support_hurt_run_count": hurt_rows,
                "support_missing_runs": missing_support,
                "guard_run_count": guard.get("run_count"),
                "guard_selected_folds": guard.get("selected_folds"),
                "guard_status_counts": dict(guard_status_counts),
                "guard_min_applied_sessions": guard.get(
                    "min_guard_applied_sessions"
                ),
                "guard_max_delta_formal_p50_mae": guard.get(
                    "max_guard_delta_formal_p50_mae"
                ),
                "guard_max_bridge_formal_p50_over_rate": guard.get(
                    "max_guard_bridge_formal_p50_over_rate"
                ),
            }
        )
    return out


def summarize_stability(
    runs: Iterable[Mapping[str, Any]],
    *,
    required_selected_groups: Iterable[str] = DEFAULT_REQUIRED_GROUPS,
    min_applied_rows: int = 20,
    require_all_watch: bool = True,
) -> dict[str, Any]:
    run_rows = [_run_row(run) for run in runs]
    required = tuple(sorted(str(group) for group in required_selected_groups))
    status, reasons = _status_for_runs(
        run_rows,
        required_selected_groups=required,
        min_applied_rows=min_applied_rows,
        require_all_watch=require_all_watch,
    )
    applied_values = [_as_int(row.get("applied_rows")) for row in run_rows]
    selected_signatures = Counter(
        str(row.get("selected_signature") or "-") for row in run_rows
    )
    status_counts = Counter(str(row.get("overall_status") or "unknown") for row in run_rows)
    hurt_groups = Counter(
        str(group)
        for row in run_rows
        for group in (row.get("applied_hurts") or ())
    )
    selected_support = _selected_group_support_summary(
        run_rows,
        min_applied_rows=min_applied_rows,
    )
    selected_guard = _selected_group_guard_summary(run_rows)
    selected_instability = _selected_group_instability_summary(
        support_summary=selected_support,
        guard_summary=selected_guard,
    )
    return {
        "overall_status": status,
        "status_reasons": reasons,
        "runs": run_rows,
        "run_count": len(run_rows),
        "watch_runs": status_counts.get("watch", 0),
        "status_counts": dict(sorted(status_counts.items())),
        "required_selected_groups": list(required),
        "stable_selected_groups": list(_stable_groups(run_rows)),
        "union_selected_groups": list(_union_groups(run_rows)),
        "selected_signature_counts": dict(sorted(selected_signatures.items())),
        "hurt_group_counts": dict(sorted(hurt_groups.items())),
        "min_applied_rows_required": int(min_applied_rows),
        "min_applied_rows": min(applied_values) if applied_values else 0,
        "max_applied_rows": max(applied_values) if applied_values else 0,
        "selected_group_support_summary": selected_support,
        "selected_group_support_gap": _support_gap_summary(selected_support),
        "selected_group_guard_summary": selected_guard,
        "selected_group_instability_summary": selected_instability,
        "require_all_watch": bool(require_all_watch),
    }


def _cache_key(
    *,
    paths: Sequence[Path],
    posterior_trials: int,
    posterior_seed: int,
    group_field: str,
    folds: int,
    inner_folds: int,
    min_train_sessions: int,
    min_guard_sessions: int,
    min_guard_fold_sessions: int,
    guard_stability: str,
    max_guard_over_increase: float,
    ratio_source: str,
    floor_mode: str,
    formal_lift_cap: float | None,
    top: int,
) -> str:
    payload = {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "paths": [str(path) for path in paths],
        "posterior_trials": int(posterior_trials),
        "posterior_seed": int(posterior_seed),
        "group_field": group_field,
        "folds": int(folds),
        "inner_folds": int(inner_folds),
        "min_train_sessions": int(min_train_sessions),
        "min_guard_sessions": int(min_guard_sessions),
        "min_guard_fold_sessions": int(min_guard_fold_sessions),
        "guard_stability": guard_stability,
        "max_guard_over_increase": float(max_guard_over_increase),
        "ratio_source": ratio_source,
        "floor_mode": floor_mode,
        "formal_lift_cap": _as_float(formal_lift_cap),
        "top": int(top),
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()


def _load_cached_run(cache_dir: Path | None, key: str) -> dict[str, Any] | None:
    if cache_dir is None:
        return None
    path = cache_dir / f"{key}.json"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        result = json.load(handle)
    result["cache_hit"] = True
    return result


def _save_cached_run(
    cache_dir: Path | None,
    key: str,
    result: Mapping[str, Any],
) -> None:
    if cache_dir is None:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{key}.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)


def summarize_trial_seed_matrix(
    paths: Iterable[Path] | None = None,
    *,
    posterior_trials: Iterable[int] = DEFAULT_POSTERIOR_TRIALS,
    posterior_seeds: Iterable[int] = DEFAULT_POSTERIOR_SEEDS,
    group_field: str = DEFAULT_GROUP_FIELD,
    folds: int = 5,
    inner_folds: int = 4,
    min_train_sessions: int = 8,
    min_guard_sessions: int = 8,
    min_guard_fold_sessions: int = 2,
    guard_stability: str = "all_folds",
    max_guard_over_increase: float = 0.0,
    ratio_source: str = "all",
    floor_mode: str = "total",
    formal_lift_cap: float | None = 10_000.0,
    required_selected_groups: Iterable[str] = DEFAULT_REQUIRED_GROUPS,
    min_applied_rows: int = 20,
    require_all_watch: bool = True,
    top: int = 12,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
) -> dict[str, Any]:
    run_results: list[dict[str, Any]] = []
    all_errors: list[dict[str, Any]] = []
    scan_paths = tuple(paths or _default_paths())
    tables = load_monitor_tables()
    calibration_entries = load_prior_calibration_entries(
        _default_calibration_path()
    )
    underestimate_entries = load_underestimate_repair_entries(
        _default_underestimate_repair_path()
    )
    tail_entries = load_tail_value_review_entries(_default_tail_value_review_path())
    scp_entries = load_settlement_count_prior_entries(
        _default_settlement_count_prior_path()
    )
    for trials in posterior_trials:
        for seed in posterior_seeds:
            cache_key = _cache_key(
                paths=scan_paths,
                posterior_trials=int(trials),
                posterior_seed=int(seed),
                group_field=group_field,
                folds=folds,
                inner_folds=inner_folds,
                min_train_sessions=min_train_sessions,
                min_guard_sessions=min_guard_sessions,
                min_guard_fold_sessions=min_guard_fold_sessions,
                guard_stability=guard_stability,
                max_guard_over_increase=max_guard_over_increase,
                ratio_source=ratio_source,
                floor_mode=floor_mode,
                formal_lift_cap=formal_lift_cap,
                top=top,
            )
            cached = _load_cached_run(cache_dir, cache_key)
            if cached is not None:
                run_results.append(cached)
                continue
            rows, errors = evaluate_paths(
                scan_paths,
                tables=tables,
                calibration_entries=calibration_entries,
                underestimate_repair_entries=underestimate_entries,
                tail_value_review_entries=tail_entries,
                settlement_count_prior_entries=scp_entries,
                posterior_trials=int(trials),
                posterior_seed=int(seed),
            )
            all_errors.extend(
                {
                    "posterior_trials": int(trials),
                    "posterior_seed": int(seed),
                    "error": str(error),
                }
                for error in errors
            )
            run_result = {
                "cache_schema_version": CACHE_SCHEMA_VERSION,
                "posterior_trials": int(trials),
                "posterior_seed": int(seed),
                "cache_hit": False,
                **summarize_guarded_holdout(
                    rows,
                    group_field=group_field,
                    folds=folds,
                    inner_folds=inner_folds,
                    min_train_sessions=min_train_sessions,
                    min_guard_sessions=min_guard_sessions,
                    min_guard_fold_sessions=min_guard_fold_sessions,
                    guard_stability=guard_stability,
                    max_guard_over_increase=max_guard_over_increase,
                    ratio_source=ratio_source,
                    floor_mode=floor_mode,
                    formal_lift_cap=formal_lift_cap,
                    top=top,
                ),
            }
            _save_cached_run(cache_dir, cache_key, run_result)
            run_results.append(
                run_result
            )
    stability = summarize_stability(
        run_results,
        required_selected_groups=required_selected_groups,
        min_applied_rows=min_applied_rows,
        require_all_watch=require_all_watch,
    )
    return {
        "errors": all_errors,
        "posterior_trials": [int(value) for value in posterior_trials],
        "posterior_seeds": [int(value) for value in posterior_seeds],
        "group_field": group_field,
        "folds": int(folds),
        "inner_folds": int(inner_folds),
        "min_train_sessions": int(min_train_sessions),
        "min_guard_sessions": int(min_guard_sessions),
        "min_guard_fold_sessions": int(min_guard_fold_sessions),
        "guard_stability": guard_stability,
        "max_guard_over_increase": _round_metric(max_guard_over_increase, 6),
        "ratio_source": ratio_source,
        "floor_mode": floor_mode,
        "formal_lift_cap": _round_metric(formal_lift_cap, 3),
        "cache_dir": str(cache_dir) if cache_dir is not None else None,
        **stability,
    }


def _print_summary(result: Mapping[str, Any]) -> None:
    print(
        " ".join(
            (
                f"overall_status={result['overall_status']}",
                f"reasons={_format_groups(result['status_reasons'])}",
                f"runs={result['run_count']}",
                f"watch_runs={result['watch_runs']}",
                f"trials={_format_groups(str(v) for v in result['posterior_trials'])}",
                f"seeds={_format_groups(str(v) for v in result['posterior_seeds'])}",
                "required_groups="
                f"{_format_groups(result['required_selected_groups'])}",
                f"stable_groups={_format_groups(result['stable_selected_groups'])}",
                f"union_groups={_format_groups(result['union_selected_groups'])}",
                f"min_applied={result['min_applied_rows']}",
                f"min_required={result['min_applied_rows_required']}",
                "signatures="
                + _format_mapping(result["selected_signature_counts"]),
            )
        )
    )
    for row in result["runs"]:
        print(
            " ".join(
                (
                    f"trials={row['posterior_trials']}",
                    f"seed={row['posterior_seed']}",
                    f"status={row['overall_status']}",
                    f"selected={_format_groups(row['selected_groups'])}",
                    f"selected_signature={row['selected_signature']}",
                    f"cache_hit={row['cache_hit']}",
                    f"applied_rows={row['applied_rows']}",
                    f"delta_mae={row['delta_formal_p50_mae']}",
                    f"delta_p90={row['delta_formal_p90_coverage']}",
                    f"bridge_over={row['bridge_formal_p50_over_rate']}",
                    f"applied_hurts={_format_groups(row['applied_hurts'])}",
                )
            )
        )
    if result.get("selected_group_support_summary"):
        print(
            "selected_support="
            + ";".join(
                (
                    f"{row['group']}:runs={row['run_count']}"
                    f"/folds={row['selected_folds']}"
                    f"/min_applied={row['min_applied_rows']}"
                    f"/max_applied={row['max_applied_rows']}"
                    f"/hurts={row['hurt_run_count']}"
                    f"/missing_support={row['missing_support_runs']}"
                )
                for row in result["selected_group_support_summary"]
            )
        )
    if result.get("selected_group_guard_summary"):
        print(
            "selected_guard="
            + ";".join(
                (
                    f"{row['group']}:runs={row['run_count']}"
                    f"/folds={row['selected_folds']}"
                    f"/statuses={_format_mapping(row['guard_status_counts'])}"
                    f"/min_guard_sessions={row['min_guard_applied_sessions']}"
                    "/max_guard_delta="
                    f"{_round_metric(row['max_guard_delta_formal_p50_mae'], 3)}"
                    "/max_guard_over="
                    f"{_round_metric(row['max_guard_bridge_formal_p50_over_rate'], 6)}"
                )
                for row in result["selected_group_guard_summary"]
            )
        )
    if result.get("selected_group_instability_summary"):
        print(
            "selected_instability="
            + ";".join(
                (
                    f"{row['group']}:{row['status']}"
                    f"/gap={row['support_min_applied_gap']}"
                    f"/hurts={row['support_hurt_run_count']}"
                    f"/watch_guard={_as_int((row.get('guard_status_counts') or {}).get('watch_train_guard'))}"
                )
                for row in result["selected_group_instability_summary"]
            )
        )
    if result.get("selected_group_support_gap"):
        print(
            "support_gap="
            + ";".join(
                (
                    f"{row['group']}:min_applied={row['min_applied_rows']}"
                    f"/required={row['required_applied_rows']}"
                    f"/gap={row['min_applied_gap']}"
                )
                for row in result["selected_group_support_gap"]
            )
        )


def _append_int(values: list[int] | None, defaults: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(values) if values else defaults


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit guarded v3 settlement bridge stability across trials and seeds.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--posterior-trials", type=int, action="append")
    parser.add_argument("--posterior-seed", type=int, action="append")
    parser.add_argument("--by", default=DEFAULT_GROUP_FIELD)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--inner-folds", type=int, default=4)
    parser.add_argument("--min-train-sessions", type=int, default=8)
    parser.add_argument("--min-guard-sessions", type=int, default=8)
    parser.add_argument("--min-guard-fold-sessions", type=int, default=2)
    parser.add_argument(
        "--guard-stability",
        choices=("aggregate", "all_folds"),
        default="all_folds",
    )
    parser.add_argument("--max-guard-over-increase", type=float, default=0.0)
    parser.add_argument("--ratio-source", choices=("all", "bridge"), default="all")
    parser.add_argument("--floor-mode", choices=("total", "extra"), default="total")
    parser.add_argument("--formal-lift-cap", type=float, default=10_000.0)
    parser.add_argument(
        "--required-selected-group",
        action="append",
        help="Required exact selected group. Defaults to 2506.",
    )
    parser.add_argument(
        "--allow-any-selected-group",
        action="store_true",
        help="Do not require the current 2506-only selection.",
    )
    parser.add_argument("--min-applied-rows", type=int, default=20)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable .tmp/codex per-run cache.",
    )
    parser.add_argument(
        "--allow-blocked-runs",
        action="store_true",
        help="Do not require every run to have overall_status=watch.",
    )
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    if args.allow_any_selected_group:
        required_groups: tuple[str, ...] = ()
    elif args.required_selected_group:
        required_groups = tuple(args.required_selected_group)
    else:
        required_groups = DEFAULT_REQUIRED_GROUPS

    result = summarize_trial_seed_matrix(
        args.paths or None,
        posterior_trials=_append_int(
            args.posterior_trials,
            DEFAULT_POSTERIOR_TRIALS,
        ),
        posterior_seeds=_append_int(args.posterior_seed, DEFAULT_POSTERIOR_SEEDS),
        group_field=args.by,
        folds=args.folds,
        inner_folds=args.inner_folds,
        min_train_sessions=args.min_train_sessions,
        min_guard_sessions=args.min_guard_sessions,
        min_guard_fold_sessions=args.min_guard_fold_sessions,
        guard_stability=args.guard_stability,
        max_guard_over_increase=args.max_guard_over_increase,
        ratio_source=args.ratio_source,
        floor_mode=args.floor_mode,
        formal_lift_cap=args.formal_lift_cap,
        required_selected_groups=required_groups,
        min_applied_rows=args.min_applied_rows,
        require_all_watch=not args.allow_blocked_runs,
        top=args.top,
        cache_dir=None if args.no_cache else args.cache_dir,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result["errors"]:
            print(f"errors={len(result['errors'])}")
        _print_summary(result)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
