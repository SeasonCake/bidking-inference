"""Audit the v3 evidence-driven CCV shadow sampler prototype.

This script treats the existing ``v3_ccvc_`` component-likelihood report as the
current count/cell/value prototype. It does not change posterior, live, formal,
or UI behavior; it only aggregates archive/session/group/policy/seed evidence
into a single promotion-hardening report.
"""

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
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
)
from summarize_v3_ccv_direction_audit import (  # noqa: E402
    MOVEMENT_POLICIES,
    component_fields,
)
from summarize_v3_ccvc_count_policy_matrix import (  # noqa: E402
    DEFAULT_GROUP_FIELDS,
    DEFAULT_POLICIES,
    summarize_matrix,
)
from summarize_v3_ccvc_evidence_contribution import (  # noqa: E402
    DEFAULT_FEATURES,
    summarize_contributions,
)

DEFAULT_COMPONENTS = ("q6_count", "q6_cells", "q6_value")
SHADOW_SAFETY_PREFIXES = ("v3_ccvc_", "v3_fv_", "v3_scp_", "v3_cse_")
DEFAULT_MIN_WATCH_SUPPORT_ROWS = 20
DEFAULT_MIN_WATCH_SUPPORT_SESSIONS = 8


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _counter_dict(values: Iterable[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items()))


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _shadow_safety_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    seq = tuple(rows)
    prefix_counts: dict[str, dict[str, int]] = {}
    affects_rows = 0
    active_rows = 0
    for prefix in SHADOW_SAFETY_PREFIXES:
        affects_key = f"{prefix}affects_bid"
        active_key = f"{prefix}active"
        prefix_affects = sum(1 for row in seq if _bool(row.get(affects_key)))
        prefix_active = sum(1 for row in seq if _bool(row.get(active_key)))
        prefix_counts[prefix.rstrip("_")] = {
            "affects_bid_rows": prefix_affects,
            "active_rows": prefix_active,
        }
        affects_rows += prefix_affects
        active_rows += prefix_active
    return {
        "shadow_affects_bid_rows": affects_rows,
        "shadow_active_rows": active_rows,
        "by_prefix": prefix_counts,
    }


def _row_contract(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    seq = tuple(rows)
    ccvc_ready = [
        row
        for row in seq
        if row.get("status") == "ready"
        and row.get("v3_truth_available")
        and row.get("v3_post_ready")
        and row.get("v3_ccvc_ready")
    ]
    component_rows = [
        row
        for row in ccvc_ready
        if str(row.get("v3_ccvc_match_scope") or "") == "ccv_component_likelihood"
    ]
    return {
        "rows": len(seq),
        "ready_rows": sum(1 for row in seq if row.get("status") == "ready"),
        "ccvc_ready_rows": len(ccvc_ready),
        "ccvc_component_likelihood_rows": len(component_rows),
        "ccvc_match_scope_counts": _counter_dict(
            row.get("v3_ccvc_match_scope") or "none" for row in ccvc_ready
        ),
        **_shadow_safety_counts(seq),
    }


def _matrix_status_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    return _counter_dict(row.get("overall_status") for row in rows)


def _candidate_label(row: Mapping[str, Any]) -> str:
    return "|".join(
        (
            str(row.get("component") or "unknown"),
            str(row.get("group_field") or "unknown"),
            str(row.get("movement_policy") or "unknown"),
        )
    )


def _watch_candidates(
    matrix_rows: Iterable[Mapping[str, Any]],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    rows = [
        row
        for row in matrix_rows
        if str(row.get("overall_status") or "") == "watch"
        and int(row.get("candidate_rows") or 0) > 0
    ]
    rows.sort(
        key=lambda row: (
            float(row.get("candidate_delta_p50_mae") or 0.0),
            float(row.get("candidate_hurt_rate") or 0.0),
            str(row.get("component") or ""),
            str(row.get("group_field") or ""),
            str(row.get("movement_policy") or ""),
        )
    )
    return [
        {
            "label": _candidate_label(row),
            "component": row.get("component"),
            "group_field": row.get("group_field"),
            "movement_policy": row.get("movement_policy"),
            "candidate_rows": row.get("candidate_rows"),
            "candidate_groups": row.get("candidate_groups") or [],
            "candidate_group_results": row.get("candidate_group_results") or [],
            "applied_hurt_group_results": row.get(
                "applied_hurt_group_results"
            )
            or [],
            "candidate_delta_p50_mae": row.get("candidate_delta_p50_mae"),
            "candidate_hurt_rate": row.get("candidate_hurt_rate"),
            "candidate_directional_error_rate": row.get(
                "candidate_directional_error_rate"
            ),
            "candidate_baseline_below_rate": row.get(
                "candidate_baseline_below_rate"
            ),
            "candidate_below_rate": row.get("candidate_below_rate"),
        }
        for row in rows[:limit]
    ]


def _applied_hurt_labels(matrix_rows: Iterable[Mapping[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in matrix_rows:
        for group in row.get("applied_hurts") or ():
            out.append(f"{_candidate_label(row)}:{group}")
    return sorted(set(out))


def _applied_hurt_metric_rows(
    matrix_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in matrix_rows:
        label = _candidate_label(row)
        for item in row.get("applied_hurt_group_results") or ():
            if not isinstance(item, Mapping):
                continue
            group_label = str(item.get("label") or "")
            out.append(
                {
                    "watch_label": f"{label}:{group_label}",
                    "matrix_label": label,
                    "component": row.get("component"),
                    "group_field": row.get("group_field"),
                    "movement_policy": row.get("movement_policy"),
                    **dict(item),
                }
            )
    out.sort(
        key=lambda item: (
            str(item.get("component") or ""),
            -float(item.get("candidate_hurt_rate") or 0.0),
            -int(item.get("candidate_hurt_rows") or 0),
            str(item.get("watch_label") or ""),
        )
    )
    return out


def _seed_status(
    *,
    row_contract: Mapping[str, Any],
    watch_candidates: Iterable[Mapping[str, Any]],
    applied_hurts: Iterable[str],
) -> str:
    if int(row_contract.get("shadow_affects_bid_rows") or 0) > 0:
        return "blocked_shadow_affects_bid"
    if int(row_contract.get("shadow_active_rows") or 0) > 0:
        return "blocked_shadow_active"
    if int(row_contract.get("ccvc_component_likelihood_rows") or 0) <= 0:
        return "blocked_no_component_likelihood"
    watch_seq = tuple(watch_candidates)
    hurt_seq = tuple(applied_hurts)
    if watch_seq and hurt_seq:
        return "watch_with_hurt_alternatives"
    if watch_seq:
        return "watch_shadow_candidate"
    if hurt_seq:
        return "blocked_holdout_hurt"
    return "sample_limited"


def summarize_seed_run(
    rows: Iterable[dict[str, Any]],
    *,
    posterior_seed: int,
    components: Iterable[str] = DEFAULT_COMPONENTS,
    group_fields: Iterable[str] = DEFAULT_GROUP_FIELDS,
    movement_policies: Iterable[str] = DEFAULT_POLICIES,
    features: Iterable[str] = DEFAULT_FEATURES,
    folds: int = 5,
    min_windows: int = 20,
    min_sessions: int = 8,
    min_changed: int = 5,
    max_hurt_rate: float = 0.45,
    max_directional_error_rate: float = 0.35,
    candidate_include_pattern: str | None = None,
    candidate_exclude_pattern: str | None = None,
) -> dict[str, Any]:
    source_rows = tuple(rows)
    selected_components = tuple(components)
    matrix_rows: list[dict[str, Any]] = []
    for component in selected_components:
        matrix_rows.extend(
            summarize_matrix(
                source_rows,
                group_fields=group_fields,
                movement_policies=movement_policies,
                candidate_prefix="v3_ccvc_",
                component=component,
                folds=folds,
                min_windows=min_windows,
                min_sessions=min_sessions,
                min_changed=min_changed,
                max_hurt_rate=max_hurt_rate,
                max_directional_error_rate=max_directional_error_rate,
                candidate_include_pattern=candidate_include_pattern,
                candidate_exclude_pattern=candidate_exclude_pattern,
            )
        )
    contribution = summarize_contributions(
        source_rows,
        components=selected_components,
        features=features,
        candidate_prefix="v3_ccvc_",
        min_changed=min_changed,
        max_hurt_rate=max_hurt_rate,
        max_directional_error_rate=max_directional_error_rate,
    )
    contract = _row_contract(source_rows)
    watch = _watch_candidates(matrix_rows)
    applied_hurts = _applied_hurt_labels(matrix_rows)
    return {
        "posterior_seed": int(posterior_seed),
        "status": _seed_status(
            row_contract=contract,
            watch_candidates=watch,
            applied_hurts=applied_hurts,
        ),
        "row_contract": contract,
        "matrix_status_counts": _matrix_status_counts(matrix_rows),
        "watch_candidates": watch,
        "applied_hurts": applied_hurts,
        "applied_hurt_metrics": _applied_hurt_metric_rows(matrix_rows),
        "matrix": matrix_rows,
        "evidence_contribution": {
            "status_counts": contribution["status_counts"],
            "component_results": contribution["component_results"],
            "top_feature_results": contribution["feature_results"][:12],
        },
    }


def _stable_watch_labels(seed_results: Iterable[Mapping[str, Any]]) -> list[str]:
    label_sets: list[set[str]] = []
    for result in seed_results:
        label_sets.append(_watch_label_set(result))
    if not label_sets or any(not labels for labels in label_sets):
        return []
    stable = set.intersection(*label_sets)
    return sorted(stable)


def _watch_label_set(
    result: Mapping[str, Any],
    *,
    component: str | None = None,
) -> set[str]:
    labels: set[str] = set()
    for row in result.get("watch_candidates") or ():
        if not isinstance(row, Mapping):
            continue
        if component is not None and str(row.get("component") or "") != component:
            continue
        label = str(row.get("label"))
        groups = tuple(row.get("candidate_groups") or ())
        if groups:
            labels.update(f"{label}:{group}" for group in groups)
        else:
            labels.add(label)
    return labels


def _stable_watch_labels_for_component(
    seed_results: Iterable[Mapping[str, Any]],
    *,
    component: str,
) -> list[str]:
    label_sets = [
        _watch_label_set(result, component=component)
        for result in seed_results
    ]
    if not label_sets or any(not labels for labels in label_sets):
        return []
    return sorted(set.intersection(*label_sets))


def _component_names(seed_results: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    names: set[str] = set()
    for result in seed_results:
        for row in result.get("matrix") or ():
            if isinstance(row, Mapping) and row.get("component"):
                names.add(str(row["component"]))
        for row in result.get("watch_candidates") or ():
            if isinstance(row, Mapping) and row.get("component"):
                names.add(str(row["component"]))
    return tuple(sorted(names))


def _component_applied_hurts(
    seed_results: Iterable[Mapping[str, Any]],
    *,
    component: str,
) -> list[str]:
    prefix = f"{component}|"
    out: list[str] = []
    for result in seed_results:
        for label in result.get("applied_hurts") or ():
            text = str(label)
            if text.startswith(prefix):
                out.append(text)
    return sorted(set(out))


def _component_applied_hurt_metrics(
    seed_results: Iterable[Mapping[str, Any]],
    *,
    component: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for result in seed_results:
        seed = result.get("posterior_seed")
        for row in result.get("applied_hurt_metrics") or ():
            if not isinstance(row, Mapping):
                continue
            if str(row.get("component") or "") != component:
                continue
            item = dict(row)
            item["posterior_seed"] = seed
            out.append(item)
    out.sort(
        key=lambda item: (
            -float(item.get("candidate_hurt_rate") or 0.0),
            -int(item.get("candidate_hurt_rows") or 0),
            str(item.get("watch_label") or ""),
            str(item.get("posterior_seed") or ""),
        )
    )
    return out


def _applied_hurt_metrics_by_seed(
    seed_results: Iterable[Mapping[str, Any]],
    *,
    component: str,
) -> list[dict[str, Any]]:
    return [
        {
            "posterior_seed": result.get("posterior_seed"),
            "applied_hurt_metrics": [
                dict(row)
                for row in result.get("applied_hurt_metrics") or ()
                if isinstance(row, Mapping)
                and str(row.get("component") or "") == component
            ],
        }
        for result in seed_results
    ]


def _component_matrix_status_counts(
    seed_results: Iterable[Mapping[str, Any]],
    *,
    component: str,
) -> dict[str, int]:
    statuses: list[str] = []
    for result in seed_results:
        for row in result.get("matrix") or ():
            if isinstance(row, Mapping) and str(row.get("component") or "") == component:
                statuses.append(str(row.get("overall_status") or "unknown"))
    return _counter_dict(statuses)


def _watch_labels_by_seed(
    seed_results: Iterable[Mapping[str, Any]],
    *,
    component: str,
) -> list[dict[str, Any]]:
    return [
        {
            "posterior_seed": result.get("posterior_seed"),
            "watch_labels": sorted(_watch_label_set(result, component=component)),
        }
        for result in seed_results
    ]


def _metric_value(
    group_result: Mapping[str, Any],
    candidate: Mapping[str, Any],
    group_key: str,
    candidate_key: str,
) -> Any:
    value = group_result.get(group_key)
    return candidate.get(candidate_key) if value is None else value


def _watch_label_metric_rows(
    result: Mapping[str, Any],
    *,
    component: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seed = result.get("posterior_seed")
    for row in result.get("watch_candidates") or ():
        if not isinstance(row, Mapping):
            continue
        if str(row.get("component") or "") != component:
            continue
        base_label = str(row.get("label"))
        group_metrics = {
            str(item.get("label")): item
            for item in row.get("candidate_group_results") or ()
            if isinstance(item, Mapping)
        }
        groups = tuple(row.get("candidate_groups") or ())
        if not groups:
            groups = ("",)
        for group in groups:
            metrics = group_metrics.get(str(group), {})
            watch_label = f"{base_label}:{group}" if group else base_label
            out.append(
                {
                    "posterior_seed": seed,
                    "watch_label": watch_label,
                    "component": row.get("component"),
                    "group_field": row.get("group_field"),
                    "movement_policy": row.get("movement_policy"),
                    "group": group or None,
                    "support_rows": _metric_value(
                        metrics,
                        row,
                        "candidate_rows",
                        "candidate_rows",
                    ),
                    "support_sessions": metrics.get("candidate_sessions"),
                    "delta_p50_mae": _metric_value(
                        metrics,
                        row,
                        "candidate_delta_p50_mae",
                        "candidate_delta_p50_mae",
                    ),
                    "hurt_rate": _metric_value(
                        metrics,
                        row,
                        "candidate_hurt_rate",
                        "candidate_hurt_rate",
                    ),
                    "hurt_rows": metrics.get("candidate_hurt_rows"),
                    "helped_rows": metrics.get("candidate_helped_rows"),
                    "directional_error_rate": _metric_value(
                        metrics,
                        row,
                        "candidate_directional_error_rate",
                        "candidate_directional_error_rate",
                    ),
                    "directional_error_rows": metrics.get(
                        "candidate_directional_error_rows"
                    ),
                    "baseline_below_rate": _metric_value(
                        metrics,
                        row,
                        "candidate_baseline_below_rate",
                        "candidate_baseline_below_rate",
                    ),
                    "candidate_below_rate": _metric_value(
                        metrics,
                        row,
                        "candidate_below_rate",
                        "candidate_below_rate",
                    ),
                }
            )
    return out


def _watch_label_metrics_by_seed(
    seed_results: Iterable[Mapping[str, Any]],
    *,
    component: str,
) -> list[dict[str, Any]]:
    return [
        {
            "posterior_seed": result.get("posterior_seed"),
            "watch_label_metrics": _watch_label_metric_rows(
                result,
                component=component,
            ),
        }
        for result in seed_results
    ]


def _unstable_watch_labels(
    labels_by_seed: Iterable[Mapping[str, Any]],
    *,
    stable: Iterable[str],
) -> list[str]:
    stable_set = set(stable)
    label_sets = [
        set(row.get("watch_labels") or ())
        for row in labels_by_seed
        if isinstance(row, Mapping)
    ]
    if not label_sets:
        return []
    union = set.union(*label_sets) if label_sets else set()
    return sorted(union - stable_set)


def _unstable_watch_metrics(
    metrics_by_seed: Iterable[Mapping[str, Any]],
    *,
    stable: Iterable[str],
) -> list[dict[str, Any]]:
    stable_set = set(stable)
    out: list[dict[str, Any]] = []
    for row in metrics_by_seed:
        if not isinstance(row, Mapping):
            continue
        for item in row.get("watch_label_metrics") or ():
            if not isinstance(item, Mapping):
                continue
            if str(item.get("watch_label") or "") not in stable_set:
                out.append(dict(item))
    out.sort(
        key=lambda item: (
            str(item.get("watch_label") or ""),
            str(item.get("posterior_seed") or ""),
        )
    )
    return out


def _support_fail_reasons(
    row: Mapping[str, Any],
    *,
    min_rows: int,
    min_sessions: int,
) -> list[str]:
    reasons: list[str] = []
    if int(min_rows) > 0 and _int_value(row.get("support_rows")) < int(min_rows):
        reasons.append("rows")
    if int(min_sessions) > 0 and _int_value(row.get("support_sessions")) < int(
        min_sessions
    ):
        reasons.append("sessions")
    return reasons


def _watch_support_gate(
    metrics_by_seed: Iterable[Mapping[str, Any]],
    *,
    stable: Iterable[str],
    min_rows: int,
    min_sessions: int,
) -> dict[str, Any]:
    stable_set = set(stable)
    low_support: list[dict[str, Any]] = []
    stable_low_support: list[dict[str, Any]] = []
    metric_count = 0
    for row in metrics_by_seed:
        if not isinstance(row, Mapping):
            continue
        for item in row.get("watch_label_metrics") or ():
            if not isinstance(item, Mapping):
                continue
            metric_count += 1
            reasons = _support_fail_reasons(
                item,
                min_rows=min_rows,
                min_sessions=min_sessions,
            )
            if not reasons:
                continue
            enriched = dict(item)
            enriched["support_fail_reasons"] = reasons
            enriched["min_support_rows"] = int(min_rows)
            enriched["min_support_sessions"] = int(min_sessions)
            low_support.append(enriched)
            if str(item.get("watch_label") or "") in stable_set:
                stable_low_support.append(enriched)
    low_support.sort(
        key=lambda item: (
            str(item.get("watch_label") or ""),
            str(item.get("posterior_seed") or ""),
        )
    )
    stable_low_support.sort(
        key=lambda item: (
            str(item.get("watch_label") or ""),
            str(item.get("posterior_seed") or ""),
        )
    )
    if stable_low_support:
        status = "blocked_low_support"
    elif low_support:
        status = "watch_low_support"
    elif metric_count <= 0:
        status = "no_watch"
    else:
        status = "pass"
    return {
        "status": status,
        "min_support_rows": int(min_rows),
        "min_support_sessions": int(min_sessions),
        "watch_metric_count": metric_count,
        "low_support_watch_metrics": low_support,
        "stable_low_support_watch_metrics": stable_low_support,
    }


def _component_next_action(status: str, component: str) -> str:
    if status == "watch_shadow_candidate":
        return "keep as diagnostic candidate; require readiness and live replay before promotion"
    if status == "watch_with_hurt_alternatives":
        return "narrow candidate groups and exclude hurt alternatives before more tuning"
    if status == "blocked_low_support":
        return "do not tune; require minimum support on every seed before retesting"
    if status == "blocked_seed_instability":
        return "do not tune; collect support or add source/evidence filters before retesting"
    if status == "blocked_holdout_hurt" and component == "q6_cells":
        return "freeze q6 cells or add a stronger cells guard before retesting"
    if status == "blocked_holdout_hurt":
        return "keep component inactive and isolate hurt groups before retesting"
    if status.startswith("blocked_shadow"):
        return "fix shadow safety before any sampler work"
    return "keep inactive until movement/support appears"


def _limited_metric_rows(
    rows: Iterable[Any],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, Mapping):
            out.append(dict(row))
        if len(out) >= limit:
            break
    return out


def _component_guard_trial_action(row: Mapping[str, Any]) -> dict[str, Any]:
    component = str(row.get("component") or "unknown")
    status = str(row.get("status") or "unknown")
    support_gate = row.get("support_gate")
    support_status = (
        str(support_gate.get("status") or "unknown")
        if isinstance(support_gate, Mapping)
        else "missing"
    )
    top_hurts = _limited_metric_rows(
        row.get("top_applied_hurt_metrics") or (),
    )
    low_support = (
        support_gate.get("low_support_watch_metrics") or ()
        if isinstance(support_gate, Mapping)
        else ()
    )
    low_support_metrics = _limited_metric_rows(low_support)
    stable_watch = list(row.get("stable_watch_candidate_labels") or ())
    unstable_watch = list(row.get("unstable_watch_candidate_labels") or ())

    if status.startswith("blocked_shadow"):
        trial_action = "fix_shadow_safety"
        rationale = "shadow rows must remain inactive and affects_bid=false"
        can_run_shadow_trial = False
        requires_source_parser = False
    elif status == "blocked_no_component_likelihood":
        trial_action = "fix_component_likelihood_contract"
        rationale = "component likelihood rows are missing"
        can_run_shadow_trial = False
        requires_source_parser = False
    elif component == "q6_cells" and top_hurts:
        trial_action = "freeze_component"
        rationale = "q6_cells has holdout hurt; freeze cells before retesting"
        can_run_shadow_trial = True
        requires_source_parser = False
    elif component == "q6_value" and top_hurts:
        trial_action = "guard_hurt_groups_keep_component_inactive"
        rationale = "q6_value has holdout hurt; isolate hurt groups first"
        can_run_shadow_trial = True
        requires_source_parser = False
    elif component == "q6_count" and (
        status in {"blocked_seed_instability", "blocked_low_support"}
        or support_status in {"watch_low_support", "blocked_low_support"}
    ):
        trial_action = "require_source_support_gate"
        rationale = "q6_count watch labels are seed-unstable or under-supported"
        can_run_shadow_trial = False
        requires_source_parser = True
    elif top_hurts:
        trial_action = "guard_hurt_groups"
        rationale = "component has holdout hurt groups that must be excluded"
        can_run_shadow_trial = True
        requires_source_parser = False
    elif stable_watch and support_status == "pass":
        trial_action = "watch_only_candidate_holdout"
        rationale = "stable supported watch labels can be held out as diagnostics"
        can_run_shadow_trial = True
        requires_source_parser = False
    elif low_support_metrics:
        trial_action = "collect_support_before_trial"
        rationale = "watch labels exist but fail support requirements"
        can_run_shadow_trial = False
        requires_source_parser = component == "q6_count"
    else:
        trial_action = "keep_inactive_sample_limited"
        rationale = "no supported movement candidate is available"
        can_run_shadow_trial = False
        requires_source_parser = False

    return {
        "component": component,
        "component_status": status,
        "support_gate": support_status,
        "trial_action": trial_action,
        "rationale": rationale,
        "can_run_shadow_trial": can_run_shadow_trial,
        "requires_source_parser": requires_source_parser,
        "stable_watch_candidate_labels": stable_watch[:8],
        "unstable_watch_candidate_labels": unstable_watch[:8],
        "top_hurt_labels": [
            str(item.get("watch_label") or "")
            for item in top_hurts
            if item.get("watch_label")
        ],
        "candidate_exclude_labels": [
            str(item.get("label") or "")
            for item in top_hurts
            if item.get("label")
        ],
        "top_applied_hurt_metrics": top_hurts,
        "low_support_watch_metrics": low_support_metrics,
    }


def _guard_trial_contract(
    component_statuses: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    actions = [
        _component_guard_trial_action(row)
        for row in component_statuses
        if isinstance(row, Mapping)
    ]
    fix_actions = {
        "fix_shadow_safety",
        "fix_component_likelihood_contract",
    }
    design_actions = {
        "freeze_component",
        "guard_hurt_groups_keep_component_inactive",
        "guard_hurt_groups",
        "watch_only_candidate_holdout",
    }
    if any(row["trial_action"] in fix_actions for row in actions):
        status = "blocked_shadow_prerequisite"
    elif any(row["trial_action"] in design_actions for row in actions):
        status = "shadow_guard_trial_design"
    elif any(row["trial_action"] == "require_source_support_gate" for row in actions):
        status = "requires_source_support_gate"
    elif actions:
        status = "sample_limited"
    else:
        status = "missing_component_statuses"
    return {
        "interface": "v3_ccvc_shadow_sampler_guard_trial_contract",
        "status": status,
        "shadow_only": True,
        "affects_bid": False,
        "active": False,
        "can_promote": False,
        "component_actions": actions,
        "action_counts": _counter_dict(row["trial_action"] for row in actions),
        "requires_source_parser": any(
            row.get("requires_source_parser") for row in actions
        ),
        "can_run_shadow_trial_components": [
            row["component"]
            for row in actions
            if row.get("can_run_shadow_trial")
        ],
        "required_verification": [
            "archive",
            "session",
            "map_family",
            "map_id",
            "evidence_profile",
            "posterior_seed",
            "readiness_attachment",
        ],
        "blocked_actions": [
            "change formal bid path",
            "wire guard trial into live decisions",
            "archive v2 fallback",
            "relax readiness or promotion gates",
        ],
    }


def _component_statuses(
    seed_results: Iterable[Mapping[str, Any]],
    *,
    min_watch_support_rows: int,
    min_watch_support_sessions: int,
) -> list[dict[str, Any]]:
    results = tuple(seed_results)
    statuses = {str(row.get("status") or "") for row in results}
    out: list[dict[str, Any]] = []
    for component in _component_names(results):
        stable = _stable_watch_labels_for_component(results, component=component)
        hurts = _component_applied_hurts(results, component=component)
        has_watch = any(_watch_label_set(result, component=component) for result in results)
        labels_by_seed = _watch_labels_by_seed(results, component=component)
        metrics_by_seed = _watch_label_metrics_by_seed(results, component=component)
        applied_hurt_metrics = _component_applied_hurt_metrics(
            results,
            component=component,
        )
        support_gate = _watch_support_gate(
            metrics_by_seed,
            stable=stable,
            min_rows=min_watch_support_rows,
            min_sessions=min_watch_support_sessions,
        )
        if "blocked_shadow_affects_bid" in statuses:
            status = "blocked_shadow_affects_bid"
        elif "blocked_shadow_active" in statuses:
            status = "blocked_shadow_active"
        elif len(results) > 1 and has_watch and not stable:
            status = "blocked_seed_instability"
        elif stable and support_gate["stable_low_support_watch_metrics"]:
            status = "blocked_low_support"
        elif stable and hurts:
            status = "watch_with_hurt_alternatives"
        elif stable:
            status = "watch_shadow_candidate"
        elif hurts:
            status = "blocked_holdout_hurt"
        else:
            status = "sample_limited"
        out.append(
            {
                "component": component,
                "status": status,
                "stable_watch_candidate_labels": stable,
                "unstable_watch_candidate_labels": _unstable_watch_labels(
                    labels_by_seed,
                    stable=stable,
                ),
                "watch_labels_by_seed": labels_by_seed,
                "watch_label_metrics_by_seed": metrics_by_seed,
                "unstable_watch_candidate_metrics": _unstable_watch_metrics(
                    metrics_by_seed,
                    stable=stable,
                ),
                "support_gate": support_gate,
                "applied_hurts": hurts,
                "applied_hurt_metrics_by_seed": _applied_hurt_metrics_by_seed(
                    results,
                    component=component,
                ),
                "top_applied_hurt_metrics": applied_hurt_metrics[:12],
                "matrix_status_counts": _component_matrix_status_counts(
                    results,
                    component=component,
                ),
                "next_action": _component_next_action(status, component),
            }
        )
    return out


def _overall_status(
    seed_results: Iterable[Mapping[str, Any]],
    stable_labels: Iterable[str],
    *,
    component_statuses: Iterable[Mapping[str, Any]],
) -> str:
    results = tuple(seed_results)
    statuses = {str(row.get("status") or "") for row in results}
    if "blocked_shadow_affects_bid" in statuses:
        return "blocked_shadow_affects_bid"
    if "blocked_shadow_active" in statuses:
        return "blocked_shadow_active"
    if "blocked_no_component_likelihood" in statuses:
        return "blocked_no_component_likelihood"
    if any(
        str(row.get("status") or "") == "blocked_low_support"
        for row in component_statuses
    ):
        return "blocked_low_support"
    if any(
        str(row.get("status") or "") == "blocked_seed_instability"
        for row in component_statuses
    ):
        return "blocked_seed_instability"
    if any(status.startswith("watch") for status in statuses):
        if "watch_with_hurt_alternatives" in statuses:
            return "watch_with_hurt_alternatives"
        return "watch_shadow_candidate"
    if "blocked_holdout_hurt" in statuses:
        return "blocked_holdout_hurt"
    return "sample_limited"


def summarize_prototype_runs(
    seed_results: Iterable[Mapping[str, Any]],
    *,
    posterior_trials: int,
    component_move_cells: bool,
    min_watch_support_rows: int = DEFAULT_MIN_WATCH_SUPPORT_ROWS,
    min_watch_support_sessions: int = DEFAULT_MIN_WATCH_SUPPORT_SESSIONS,
) -> dict[str, Any]:
    results = tuple(seed_results)
    stable_labels = _stable_watch_labels(results)
    component_statuses = _component_statuses(
        results,
        min_watch_support_rows=min_watch_support_rows,
        min_watch_support_sessions=min_watch_support_sessions,
    )
    guard_trial_contract = _guard_trial_contract(component_statuses)
    return {
        "interface": "v3_ccvc_evidence_driven_count_cell_value_sampler",
        "shadow_only": True,
        "affects_bid": False,
        "active": False,
        "can_promote": False,
        "posterior_trials": int(posterior_trials),
        "component_move_cells": bool(component_move_cells),
        "min_watch_support_rows": int(min_watch_support_rows),
        "min_watch_support_sessions": int(min_watch_support_sessions),
        "posterior_seeds": [row.get("posterior_seed") for row in results],
        "status": _overall_status(
            results,
            stable_labels,
            component_statuses=component_statuses,
        ),
        "seed_status_counts": _counter_dict(row.get("status") for row in results),
        "stable_watch_candidate_labels": stable_labels,
        "component_statuses": component_statuses,
        "guard_trial_contract": guard_trial_contract,
        "required_holdouts": [
            "archive",
            "session",
            "map_family",
            "map_id",
            "evidence_profile",
            "posterior_seed",
        ],
        "blocked_actions": [
            "change formal bid path",
            "wire prototype into live decisions",
            "archive v2 fallback",
            "relax readiness or promotion gates",
        ],
        "seed_results": list(results),
    }


def _print_summary(result: Mapping[str, Any], *, top: int) -> None:
    guard_contract = result.get("guard_trial_contract") or {}
    guard_actions = (
        guard_contract.get("action_counts")
        if isinstance(guard_contract, Mapping)
        else {}
    ) or {}
    print(
        " ".join(
            (
                f"status={result.get('status')}",
                f"interface={result.get('interface')}",
                f"shadow_only={result.get('shadow_only')}",
                f"affects_bid={result.get('affects_bid')}",
                f"trials={result.get('posterior_trials')}",
                "seeds=" + ",".join(str(seed) for seed in result.get("posterior_seeds") or ()),
                "stable_watch="
                + ",".join(result.get("stable_watch_candidate_labels") or ()),
                "seed_status_counts="
                + ",".join(
                    f"{status}:{count}"
                    for status, count in (result.get("seed_status_counts") or {}).items()
                ),
                f"guard_trial={guard_contract.get('status') if isinstance(guard_contract, Mapping) else None}",
                "guard_actions="
                + ",".join(
                    f"{action}:{count}"
                    for action, count in guard_actions.items()
                ),
            )
        )
    )
    for row in result.get("component_statuses") or ():
        support_gate = row.get("support_gate") or {}
        unstable_support = [
            (
                f"{item.get('watch_label')}@seed{item.get('posterior_seed')}:"
                f"{item.get('support_rows')}/{item.get('support_sessions')}"
            )
            for item in (row.get("unstable_watch_candidate_metrics") or ())[:top]
        ]
        low_support = [
            (
                f"{item.get('watch_label')}@seed{item.get('posterior_seed')}:"
                f"{item.get('support_rows')}/{item.get('support_sessions')}"
            )
            for item in (support_gate.get("low_support_watch_metrics") or ())[:top]
        ]
        top_hurts = [
            (
                f"{item.get('watch_label')}@seed{item.get('posterior_seed')}:"
                f"{item.get('candidate_rows')}/{item.get('candidate_sessions')}"
                f"/hurt={item.get('candidate_hurt_rate')}"
            )
            for item in (row.get("top_applied_hurt_metrics") or ())[:top]
        ]
        print(
            " ".join(
                (
                    f"component={row.get('component')}",
                    f"status={row.get('status')}",
                    f"support_gate={support_gate.get('status')}",
                    "stable_watch="
                    + ",".join(row.get("stable_watch_candidate_labels") or ()),
                    "unstable_watch="
                    + ",".join((row.get("unstable_watch_candidate_labels") or ())[:top]),
                    "unstable_support=" + ",".join(unstable_support),
                    "low_support=" + ",".join(low_support),
                    "top_hurts=" + ",".join(top_hurts),
                    "applied_hurts="
                    + ",".join((row.get("applied_hurts") or ())[:top]),
                    f"next_action=\"{row.get('next_action')}\"",
                )
            )
        )
    for seed_result in result.get("seed_results") or ():
        contract = seed_result.get("row_contract") or {}
        print(
            " ".join(
                (
                    f"seed={seed_result.get('posterior_seed')}",
                    f"status={seed_result.get('status')}",
                    f"rows={contract.get('rows')}",
                    "component_rows="
                    f"{contract.get('ccvc_component_likelihood_rows')}",
                    "matrix_status_counts="
                    + ",".join(
                        f"{status}:{count}"
                        for status, count in (
                            seed_result.get("matrix_status_counts") or {}
                        ).items()
                    ),
                    "applied_hurts="
                    + ",".join((seed_result.get("applied_hurts") or ())[:top]),
                )
            )
        )
        for row in (seed_result.get("watch_candidates") or ())[:top]:
            print(
                " ".join(
                    (
                        f"candidate={row.get('label')}",
                        "groups=" + ",".join(row.get("candidate_groups") or ()),
                        f"rows={row.get('candidate_rows')}",
                        f"delta={row.get('candidate_delta_p50_mae')}",
                        f"hurt_rate={row.get('candidate_hurt_rate')}",
                        "directional_error="
                        f"{row.get('candidate_directional_error_rate')}",
                    )
                )
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit the v3 CCVC shadow sampler prototype.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument(
        "--component",
        action="append",
        choices=tuple(component_fields("v3_ccvc_")),
        help="Component to audit. Can be passed more than once.",
    )
    parser.add_argument(
        "--group-field",
        action="append",
        help="Holdout group field. Can be passed more than once.",
    )
    parser.add_argument(
        "--movement-policy",
        action="append",
        choices=MOVEMENT_POLICIES,
        help="Movement policy to audit. Can be passed more than once.",
    )
    parser.add_argument(
        "--feature",
        action="append",
        choices=DEFAULT_FEATURES,
        help="Evidence feature to audit. Can be passed more than once.",
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-windows", type=int, default=20)
    parser.add_argument("--min-sessions", type=int, default=8)
    parser.add_argument("--min-changed", type=int, default=5)
    parser.add_argument("--max-hurt-rate", type=float, default=0.45)
    parser.add_argument("--max-directional-error-rate", type=float, default=0.35)
    parser.add_argument(
        "--candidate-include-pattern",
        help="Regex over 'component:group' labels allowed into holdout candidates.",
    )
    parser.add_argument(
        "--candidate-exclude-pattern",
        help="Regex over 'component:group' labels removed from holdout candidates.",
    )
    parser.add_argument("--posterior-trials", type=int, default=64)
    parser.add_argument(
        "--posterior-seed",
        action="append",
        type=int,
        help="Posterior seed to audit. Can be passed more than once.",
    )
    parser.add_argument(
        "--ccv-component-freeze-cells",
        action="store_true",
        help="Keep q6 cells at baseline when emitting v3_ccvc_ fields.",
    )
    parser.add_argument(
        "--min-watch-support-rows",
        type=int,
        default=DEFAULT_MIN_WATCH_SUPPORT_ROWS,
        help="Minimum candidate rows required for a watch label support gate.",
    )
    parser.add_argument(
        "--min-watch-support-sessions",
        type=int,
        default=DEFAULT_MIN_WATCH_SUPPORT_SESSIONS,
        help="Minimum candidate sessions required for a watch label support gate.",
    )
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    tables = load_monitor_tables()
    calibration_entries = load_prior_calibration_entries(
        _default_calibration_path()
    )
    seed_results: list[dict[str, Any]] = []
    all_errors: list[str] = []
    seeds = args.posterior_seed or [0]
    for seed in seeds:
        rows, errors = evaluate_paths(
            args.paths or _default_paths(),
            tables=tables,
            calibration_entries=calibration_entries,
            posterior_trials=args.posterior_trials,
            posterior_seed=seed,
            ccv_options=V3CcvOptions(
                component_likelihood=True,
                component_move_cells=not args.ccv_component_freeze_cells,
            ),
        )
        all_errors.extend(str(error) for error in errors)
        seed_results.append(
            summarize_seed_run(
                rows,
                posterior_seed=seed,
                components=args.component or DEFAULT_COMPONENTS,
                group_fields=args.group_field or DEFAULT_GROUP_FIELDS,
                movement_policies=args.movement_policy or DEFAULT_POLICIES,
                features=args.feature or DEFAULT_FEATURES,
                folds=args.folds,
                min_windows=args.min_windows,
                min_sessions=args.min_sessions,
                min_changed=args.min_changed,
                max_hurt_rate=args.max_hurt_rate,
                max_directional_error_rate=args.max_directional_error_rate,
                candidate_include_pattern=args.candidate_include_pattern,
                candidate_exclude_pattern=args.candidate_exclude_pattern,
            )
        )
    result = {
        "errors": all_errors,
        **summarize_prototype_runs(
            seed_results,
            posterior_trials=args.posterior_trials,
            component_move_cells=not args.ccv_component_freeze_cells,
            min_watch_support_rows=args.min_watch_support_rows,
            min_watch_support_sessions=args.min_watch_support_sessions,
        ),
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if all_errors:
            print(f"errors={len(all_errors)}")
        _print_summary(result, top=args.top)
    return 1 if all_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
