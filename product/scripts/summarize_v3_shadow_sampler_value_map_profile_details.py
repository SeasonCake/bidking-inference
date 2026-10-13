"""Replay q6_value map-only hurt labels into row-level source/profile details."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evaluate_fatbeans_v3_samples import (  # noqa: E402
    V3CcvOptions,
    _default_calibration_path,
    _default_paths,
    _float_or_none,
    _round_metric,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
)
from summarize_v3_ccv_direction_audit import (  # noqa: E402
    apply_movement_policy,
    component_fields,
    summarize_direction,
)
from summarize_v3_ccv_direction_holdout import (  # noqa: E402
    _stable_fold,
)
from summarize_v3_shadow_sampler_value_source_profile_audit import (  # noqa: E402
    _parse_evidence_profile_key,
    _parse_watch_label,
)

COMPONENT = "q6_value"
INTERFACE = "v3_ccvc_q6_value_map_profile_details"


def _counter_dict(values: Iterable[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items()))


def _session_id(row: Mapping[str, Any]) -> str:
    value = row.get("session_id")
    if value not in (None, ""):
        return str(value)
    return str(row.get("file") or "unknown")


def _group_value(row: Mapping[str, Any], group_field: str) -> str:
    parts = tuple(part.strip() for part in str(group_field).split(",") if part.strip())
    if len(parts) > 1:
        return "|".join(f"{part}={_group_value(row, part)}" for part in parts)
    value = row.get(group_field)
    return str(value) if value not in (None, "") else "unknown"


def _as_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _round(value: float | None) -> float | None:
    return _round_metric(value, 3)


def _label_metrics_from_audit(
    audit: Mapping[str, Any],
    *,
    run: str = "latest",
) -> list[dict[str, Any]]:
    runs = _as_list(audit.get("runs"))
    if not runs:
        return []
    selected = runs[-1] if run == "latest" else next(
        (row for row in runs if str(row.get("label") or "") == run),
        runs[-1],
    )
    out: list[dict[str, Any]] = []
    for metric in _as_list(selected.get("top_hurt_metrics")):
        parsed = _parse_watch_label(str(metric.get("watch_label") or ""))
        if (
            parsed.get("component") != COMPONENT
            or parsed.get("group_field") != "map_id"
            or parsed.get("evidence_profile_key")
        ):
            continue
        group = str(parsed.get("group") or parsed.get("map_id") or "")
        if not group:
            continue
        out.append(
            {
                "watch_label": parsed["watch_label"],
                "component": COMPONENT,
                "group_field": "map_id",
                "group": group,
                "movement_policy": parsed.get("movement_policy") or "all",
                "posterior_seed": metric.get("posterior_seed"),
                "source_metric": dict(metric),
            }
        )
    return out


def _dedupe_label_metrics(metrics: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for metric in metrics:
        watch_label = str(metric.get("watch_label") or "")
        seed = str(metric.get("posterior_seed") or "")
        key = (watch_label, seed)
        if key not in merged:
            merged[key] = dict(metric)
            merged[key]["duplicate_count"] = 1
        else:
            merged[key]["duplicate_count"] = int(merged[key]["duplicate_count"]) + 1
    return list(merged.values())


def _candidate_row_detail(
    row: Mapping[str, Any],
    *,
    fold: int,
    group: str,
    movement_policy: str,
    fields: Mapping[str, tuple[str, str, str]],
) -> dict[str, Any] | None:
    if _group_value(row, "map_id") != group:
        return None
    baseline_key, candidate_key, truth_key = fields[COMPONENT]
    baseline = _float_or_none(row.get(baseline_key))
    raw_candidate = _float_or_none(row.get(candidate_key))
    truth = _float_or_none(row.get(truth_key))
    if baseline is None or raw_candidate is None or truth is None:
        return None
    candidate = apply_movement_policy(
        baseline,
        raw_candidate,
        movement_policy=movement_policy,
    )
    prediction_delta = candidate - baseline
    baseline_error = baseline - truth
    candidate_error = candidate - truth
    baseline_abs = abs(baseline_error)
    candidate_abs = abs(candidate_error)
    if abs(prediction_delta) <= 1e-9:
        effect = "unchanged"
    elif candidate_abs < baseline_abs - 1e-9:
        effect = "helped"
    elif candidate_abs > baseline_abs + 1e-9:
        effect = "hurt"
    else:
        effect = "neutral"
    if baseline_error < 0:
        baseline_side = "under"
    elif baseline_error > 0:
        baseline_side = "over"
    else:
        baseline_side = "exact"
    if prediction_delta > 0:
        move = "up"
    elif prediction_delta < 0:
        move = "down"
    else:
        move = "flat"
    profile_key = str(row.get("evidence_profile_key") or "basic")
    profile = _parse_evidence_profile_key(profile_key)
    return {
        "fold": int(fold),
        "file": row.get("file"),
        "session_id": _session_id(row),
        "hero": row.get("hero"),
        "map_id": row.get("map_id"),
        "map_family": row.get("map_family"),
        "evidence_profile_key": profile_key,
        "hero_map_evidence_profile": row.get("hero_map_evidence_profile"),
        "profile_semantic_class": profile["semantic_class"],
        "profile_source_class": profile["source_class"],
        "profile_anchor_class": profile["anchor_class"],
        "profile_public_sources": profile["public_sources"],
        "profile_anchors": profile["anchors"],
        "truth": _round(truth),
        "baseline": _round(baseline),
        "candidate": _round(candidate),
        "raw_candidate": _round(raw_candidate),
        "prediction_delta": _round(prediction_delta),
        "baseline_abs_error": _round(baseline_abs),
        "candidate_abs_error": _round(candidate_abs),
        "baseline_side": baseline_side,
        "move": move,
        "effect": effect,
        "directional_error": bool(
            (baseline_side == "under" and move == "down")
            or (baseline_side == "over" and move == "up")
        ),
    }


def _profile_counts(details: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = tuple(details)
    return {
        "evidence_profile_counts": _counter_dict(
            row.get("evidence_profile_key") for row in rows
        ),
        "profile_semantic_class_counts": _counter_dict(
            row.get("profile_semantic_class") for row in rows
        ),
        "hero_map_evidence_profile_counts": _counter_dict(
            row.get("hero_map_evidence_profile") for row in rows
        ),
        "profile_public_source_counts": _counter_dict(
            source
            for row in rows
            for source in row.get("profile_public_sources") or ()
        ),
        "profile_anchor_counts": _counter_dict(
            anchor
            for row in rows
            for anchor in row.get("profile_anchors") or ()
        ),
    }


def summarize_label_details(
    rows: Iterable[dict[str, Any]],
    metric: Mapping[str, Any],
    *,
    folds: int = 5,
    min_windows: int = 20,
    min_sessions: int = 8,
    min_changed: int = 5,
    max_hurt_rate: float = 0.45,
    max_directional_error_rate: float = 0.35,
    candidate_prefix: str = "v3_ccvc_",
    example_limit: int = 8,
) -> dict[str, Any]:
    source_rows = tuple(rows)
    group = str(metric.get("group") or "")
    movement_policy = str(metric.get("movement_policy") or "all")
    fields = component_fields(candidate_prefix)
    fold_count = max(1, int(folds))
    details: list[dict[str, Any]] = []
    train_candidate_status_counts: Counter[str] = Counter()
    fold_candidate_statuses: list[dict[str, Any]] = []
    for fold in range(fold_count):
        train_rows = [
            row
            for row in source_rows
            if _stable_fold(_session_id(row), fold_count) != fold
        ]
        holdout_rows = [
            row
            for row in source_rows
            if _stable_fold(_session_id(row), fold_count) == fold
        ]
        train_candidates = summarize_direction(
            train_rows,
            group_field="map_id",
            components=(COMPONENT,),
            min_windows=min_windows,
            min_sessions=min_sessions,
            min_changed=min_changed,
            max_hurt_rate=max_hurt_rate,
            max_directional_error_rate=max_directional_error_rate,
            candidate_prefix=candidate_prefix,
            movement_policy=movement_policy,
        )
        train_candidate_status_counts.update(
            str(row.get("status") or "unknown") for row in train_candidates
        )
        candidate = next(
            (
                row
                for row in train_candidates
                if str(row.get("component") or "") == COMPONENT
                and str(row.get("group") or "") == group
            ),
            None,
        )
        candidate_status = str(
            candidate.get("status") if isinstance(candidate, Mapping) else "missing"
        )
        applied = candidate_status == "watch_directional_candidate"
        fold_candidate_statuses.append(
            {
                "fold": fold,
                "status": candidate_status,
                "train_rows": len(train_rows),
                "holdout_rows": len(holdout_rows),
            }
        )
        if not applied:
            continue
        for row in holdout_rows:
            if (
                row.get("status") != "ready"
                or not row.get("v3_truth_available")
                or not row.get("v3_post_ready")
                or not row.get(f"{candidate_prefix}ready")
            ):
                continue
            detail = _candidate_row_detail(
                row,
                fold=fold,
                group=group,
                movement_policy=movement_policy,
                fields=fields,
            )
            if detail is not None:
                details.append(detail)
    sessions = {str(row["session_id"]) for row in details}
    effects = Counter(str(row["effect"]) for row in details)
    directional_errors = sum(1 for row in details if row.get("directional_error"))
    source_metric = metric.get("source_metric")
    if not isinstance(source_metric, Mapping):
        source_metric = {}
    out = {
        "watch_label": metric.get("watch_label"),
        "posterior_seed": metric.get("posterior_seed"),
        "component": COMPONENT,
        "group_field": "map_id",
        "group": group,
        "movement_policy": movement_policy,
        "duplicate_count": metric.get("duplicate_count", 1),
        "candidate_rows": len(details),
        "candidate_sessions": len(sessions),
        "effect_counts": dict(sorted(effects.items())),
        "hurt_rows": effects.get("hurt", 0),
        "helped_rows": effects.get("helped", 0),
        "directional_error_rows": directional_errors,
        "source_metric_candidate_rows": source_metric.get("candidate_rows"),
        "source_metric_candidate_sessions": source_metric.get("candidate_sessions"),
        "source_metric_hurt_rate": source_metric.get("candidate_hurt_rate"),
        "row_count_matches_source_metric": (
            source_metric.get("candidate_rows") is not None
            and int(source_metric.get("candidate_rows") or 0) == len(details)
        ),
        "train_candidate_status_counts": dict(sorted(train_candidate_status_counts.items())),
        "fold_candidate_statuses": fold_candidate_statuses,
        **_profile_counts(details),
        "detail_rows": details,
        "example_rows": details[:example_limit],
    }
    return out


def summarize_map_profile_details(
    rows_by_seed: Mapping[int, Iterable[dict[str, Any]]],
    audit: Mapping[str, Any],
    *,
    run: str = "latest",
    folds: int = 5,
    min_windows: int = 20,
    min_sessions: int = 8,
    min_changed: int = 5,
    max_hurt_rate: float = 0.45,
    max_directional_error_rate: float = 0.35,
    example_limit: int = 8,
) -> dict[str, Any]:
    label_metrics = _dedupe_label_metrics(_label_metrics_from_audit(audit, run=run))
    labels: list[dict[str, Any]] = []
    for metric in label_metrics:
        seed = int(metric.get("posterior_seed") or 0)
        rows = tuple(rows_by_seed.get(seed) or ())
        labels.append(
            summarize_label_details(
                rows,
                metric,
                folds=folds,
                min_windows=min_windows,
                min_sessions=min_sessions,
                min_changed=min_changed,
                max_hurt_rate=max_hurt_rate,
                max_directional_error_rate=max_directional_error_rate,
                example_limit=example_limit,
            )
        )
    total_rows = sum(int(row.get("candidate_rows") or 0) for row in labels)
    labels_with_mismatch = [
        str(row.get("watch_label") or "")
        for row in labels
        if row.get("row_count_matches_source_metric") is False
    ]
    status = (
        "blocked_map_only_details_ready"
        if labels and total_rows > 0
        else "blocked_no_map_only_details"
    )
    return {
        "interface": INTERFACE,
        "status": status,
        "shadow_only": True,
        "affects_bid": False,
        "active": False,
        "can_promote": False,
        "component": COMPONENT,
        "source_audit_status": audit.get("status"),
        "source_profile_parser_status": (
            (audit.get("source_profile_parser") or {}).get("status")
            if isinstance(audit.get("source_profile_parser"), Mapping)
            else None
        ),
        "label_count": len(labels),
        "candidate_rows": total_rows,
        "candidate_sessions_sum": sum(
            int(row.get("candidate_sessions") or 0) for row in labels
        ),
        "labels_with_row_count_mismatch": labels_with_mismatch,
        "labels": labels,
        "next_action": (
            "review q6_value map-only row/source clusters before designing "
            "any value guard"
        ),
        "blocked_actions": [
            "change formal bid path",
            "wire q6 value map/profile details into live decisions",
            "treat map-only labels as profile guards without row-level review",
            "archive v2 fallback",
            "relax readiness or promotion gates",
        ],
    }


def _print_summary(result: Mapping[str, Any], *, top: int) -> None:
    print(
        " ".join(
            (
                f"status={result.get('status')}",
                f"component={result.get('component')}",
                f"labels={result.get('label_count')}",
                f"candidate_rows={result.get('candidate_rows')}",
                f"source_parser={result.get('source_profile_parser_status')}",
                "mismatch="
                + ",".join(result.get("labels_with_row_count_mismatch") or ()),
                f"next_action=\"{result.get('next_action')}\"",
            )
        )
    )
    for row in (result.get("labels") or ())[:top]:
        print(
            " ".join(
                (
                    f"label={row.get('watch_label')}",
                    f"seed={row.get('posterior_seed')}",
                    f"rows={row.get('candidate_rows')}",
                    f"sessions={row.get('candidate_sessions')}",
                    f"hurt={row.get('hurt_rows')}",
                    f"helped={row.get('helped_rows')}",
                    "profiles="
                    + ",".join(
                        (
                            row.get("evidence_profile_counts")
                            or {}
                        ).keys()
                    ),
                    "profile_classes="
                    + ",".join(
                        (
                            row.get("profile_semantic_class_counts")
                            or {}
                        ).keys()
                    ),
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay q6_value map-only hurt labels into row-level details.",
    )
    parser.add_argument("--audit-json", type=Path, required=True)
    parser.add_argument("--guarded-trial-json", type=Path)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--run", default="latest")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-windows", type=int, default=20)
    parser.add_argument("--min-sessions", type=int, default=8)
    parser.add_argument("--min-changed", type=int, default=5)
    parser.add_argument("--max-hurt-rate", type=float, default=0.45)
    parser.add_argument("--max-directional-error-rate", type=float, default=0.35)
    parser.add_argument("--posterior-trials", type=int, default=64)
    parser.add_argument("--example-limit", type=int, default=8)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    with args.audit_json.open("r", encoding="utf-8-sig") as handle:
        audit = json.load(handle)
    label_metrics = _dedupe_label_metrics(
        _label_metrics_from_audit(audit, run=args.run)
    )
    seeds = sorted(
        {
            int(metric.get("posterior_seed") or 0)
            for metric in label_metrics
        }
    ) or [0]
    component_move_cells = False
    if args.guarded_trial_json is not None:
        with args.guarded_trial_json.open("r", encoding="utf-8-sig") as handle:
            trial = json.load(handle)
        options = trial.get("trial_options")
        if isinstance(options, Mapping):
            component_move_cells = bool(options.get("component_move_cells"))
    tables = load_monitor_tables()
    calibration_entries = load_prior_calibration_entries(
        _default_calibration_path()
    )
    rows_by_seed: dict[int, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for seed in seeds:
        rows, seed_errors = evaluate_paths(
            args.paths or _default_paths(),
            tables=tables,
            calibration_entries=calibration_entries,
            posterior_trials=args.posterior_trials,
            posterior_seed=seed,
            ccv_options=V3CcvOptions(
                component_likelihood=True,
                component_move_cells=component_move_cells,
            ),
        )
        rows_by_seed[int(seed)] = rows
        errors.extend(str(error) for error in seed_errors)
    result = summarize_map_profile_details(
        rows_by_seed,
        audit,
        run=args.run,
        folds=args.folds,
        min_windows=args.min_windows,
        min_sessions=args.min_sessions,
        min_changed=args.min_changed,
        max_hurt_rate=args.max_hurt_rate,
        max_directional_error_rate=args.max_directional_error_rate,
        example_limit=args.example_limit,
    )
    result["errors"] = errors
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if errors:
            print(f"errors={len(errors)}")
        _print_summary(result, top=args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
