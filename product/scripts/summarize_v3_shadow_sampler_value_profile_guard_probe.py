"""Run audit-only q6_value profile guard probes from guardability candidates."""

from __future__ import annotations

import argparse
import json
import sys
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
from summarize_v3_shadow_sampler_guard_trial import (  # noqa: E402
    DEFAULT_TRIAL_GROUP_FIELDS,
    summarize_guard_trial_rows,
)
from summarize_v3_shadow_sampler_prototype import (  # noqa: E402
    DEFAULT_COMPONENTS,
    DEFAULT_FEATURES,
    DEFAULT_MIN_WATCH_SUPPORT_ROWS,
    DEFAULT_MIN_WATCH_SUPPORT_SESSIONS,
)
from summarize_v3_ccvc_count_policy_matrix import DEFAULT_POLICIES  # noqa: E402

INTERFACE = "v3_ccvc_q6_value_profile_guard_probe"
BLOCKING_SAMPLER_STATUSES = {
    "blocked_shadow_affects_bid",
    "blocked_shadow_active",
    "blocked_no_component_likelihood",
    "blocked_seed_instability",
    "blocked_low_support",
    "blocked_holdout_hurt",
    "watch_with_hurt_alternatives",
}


def _dedupe(seq: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in seq:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
    return out


def _candidate_profiles(guardability: Mapping[str, Any]) -> list[str]:
    profiles: list[str] = []
    for row in guardability.get("candidate_clusters") or ():
        if not isinstance(row, Mapping):
            continue
        if str(row.get("dimension") or "") != "evidence_profile_key":
            continue
        group = str(row.get("group") or "").strip()
        if group and group != "unknown":
            profiles.append(group)
    return _dedupe(profiles)


def _component_statuses(trial: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    sampler = trial.get("guarded_sampler_result")
    if not isinstance(sampler, Mapping):
        return []
    rows = sampler.get("component_statuses")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _q6_value_component(trial: Mapping[str, Any]) -> Mapping[str, Any]:
    for row in _component_statuses(trial):
        if str(row.get("component") or "") == "q6_value":
            return row
    return {}


def _support_gate_status(component: Mapping[str, Any]) -> str:
    support = component.get("support_gate")
    if isinstance(support, Mapping):
        return str(support.get("status") or "unknown")
    return "missing"


def _q6_value_probe_summary(
    trial: Mapping[str, Any],
    *,
    baseline_hurt_count: int | None,
) -> dict[str, Any]:
    component = _q6_value_component(trial)
    hurts = list(component.get("applied_hurts") or ())
    hurt_count = len(hurts)
    stable = list(component.get("stable_watch_candidate_labels") or ())
    unstable = list(component.get("unstable_watch_candidate_labels") or ())
    top_hurts = [
        str(row.get("watch_label") or "")
        for row in component.get("top_applied_hurt_metrics") or ()
        if isinstance(row, Mapping) and row.get("watch_label")
    ][:8]
    return {
        "trial_status": trial.get("status"),
        "sampler_status": trial.get("sampler_status"),
        "audit_probe": trial.get("audit_probe"),
        "q6_value_status": component.get("status"),
        "q6_value_support_gate": _support_gate_status(component),
        "q6_value_stable_watch_count": len(stable),
        "q6_value_unstable_watch_count": len(unstable),
        "q6_value_hurt_count": hurt_count,
        "baseline_q6_value_hurt_count": baseline_hurt_count,
        "q6_value_hurt_delta": (
            hurt_count - baseline_hurt_count
            if baseline_hurt_count is not None
            else None
        ),
        "q6_value_top_hurts": top_hurts,
    }


def _baseline_hurt_count(baseline_trial: Mapping[str, Any] | None) -> int | None:
    if not isinstance(baseline_trial, Mapping):
        return None
    return len(list(_q6_value_component(baseline_trial).get("applied_hurts") or ()))


def _overall_status(probes: Iterable[Mapping[str, Any]]) -> str:
    rows = list(probes)
    if not rows:
        return "blocked_no_profile_candidates"
    hurt_counts = [int(row.get("q6_value_hurt_count") or 0) for row in rows]
    sampler_statuses = {str(row.get("sampler_status") or "") for row in rows}
    deltas = [
        int(row.get("q6_value_hurt_delta"))
        for row in rows
        if row.get("q6_value_hurt_delta") is not None
    ]
    if min(hurt_counts) <= 0 and not (sampler_statuses & BLOCKING_SAMPLER_STATUSES):
        return "watch_audit_probe_holdout_clean"
    if deltas and min(deltas) < 0:
        return "blocked_profile_probe_hurts_remain"
    return "blocked_profile_probe_no_improvement"


def summarize_profile_guard_probes(
    rows_by_seed: Mapping[int, Iterable[dict[str, Any]]],
    *,
    prototype: Mapping[str, Any],
    guardability: Mapping[str, Any],
    baseline_trial: Mapping[str, Any] | None = None,
    profiles: Iterable[str] = (),
    posterior_trials: int,
    components: Iterable[str] = DEFAULT_COMPONENTS,
    group_fields: Iterable[str] = DEFAULT_TRIAL_GROUP_FIELDS,
    movement_policies: Iterable[str] = DEFAULT_POLICIES,
    features: Iterable[str] = DEFAULT_FEATURES,
    folds: int = 5,
    min_windows: int = 20,
    min_sessions: int = 8,
    min_changed: int = 5,
    max_hurt_rate: float = 0.45,
    max_directional_error_rate: float = 0.35,
    min_watch_support_rows: int = DEFAULT_MIN_WATCH_SUPPORT_ROWS,
    min_watch_support_sessions: int = DEFAULT_MIN_WATCH_SUPPORT_SESSIONS,
) -> dict[str, Any]:
    selected_profiles = _dedupe(profiles) or _candidate_profiles(guardability)
    baseline_hurts = _baseline_hurt_count(baseline_trial)
    probes: list[dict[str, Any]] = []
    for profile in selected_profiles:
        trial = summarize_guard_trial_rows(
            rows_by_seed,
            prototype=prototype,
            posterior_trials=posterior_trials,
            components=components,
            group_fields=group_fields,
            movement_policies=movement_policies,
            features=features,
            folds=folds,
            min_windows=min_windows,
            min_sessions=min_sessions,
            min_changed=min_changed,
            max_hurt_rate=max_hurt_rate,
            max_directional_error_rate=max_directional_error_rate,
            min_watch_support_rows=min_watch_support_rows,
            min_watch_support_sessions=min_watch_support_sessions,
            extra_exclude_q6_value_profiles=(profile,),
        )
        probes.append(
            {
                "profile": profile,
                "trial_options": trial.get("trial_options"),
                **_q6_value_probe_summary(
                    trial,
                    baseline_hurt_count=baseline_hurts,
                ),
            }
        )
    probes.sort(
        key=lambda row: (
            int(row.get("q6_value_hurt_count") or 0),
            str(row.get("profile") or ""),
        )
    )
    return {
        "interface": INTERFACE,
        "status": _overall_status(probes),
        "shadow_only": True,
        "affects_bid": False,
        "active": False,
        "can_promote": False,
        "component": "q6_value",
        "guardability_status": guardability.get("status"),
        "source_guardability_candidate_cluster_count": guardability.get(
            "candidate_cluster_count"
        ),
        "profile_count": len(selected_profiles),
        "profiles": selected_profiles,
        "baseline_q6_value_hurt_count": baseline_hurts,
        "min_probe_q6_value_hurt_count": (
            min(int(row.get("q6_value_hurt_count") or 0) for row in probes)
            if probes
            else None
        ),
        "probes": probes,
        "best_probe": probes[0] if probes else None,
        "required_holdouts": [
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
            "wire profile guard into live decisions",
            "promote q6 value guard from audit probe",
            "archive v2 fallback",
            "relax readiness or promotion gates",
        ],
        "next_action": (
            "profile probes still have q6_value hurt alternatives; keep q6_value "
            "inactive and design stronger source/profile guard"
            if probes
            else "no evidence_profile_key guard candidates were available"
        ),
    }


def _print_summary(result: Mapping[str, Any], *, top: int) -> None:
    print(
        " ".join(
            (
                f"status={result.get('status')}",
                f"component={result.get('component')}",
                f"profiles={result.get('profile_count')}",
                f"baseline_hurts={result.get('baseline_q6_value_hurt_count')}",
                f"min_probe_hurts={result.get('min_probe_q6_value_hurt_count')}",
                f"shadow_only={result.get('shadow_only')}",
                f"affects_bid={result.get('affects_bid')}",
            )
        )
    )
    for row in list(result.get("probes") or [])[:top]:
        print(
            " ".join(
                (
                    f"profile={row.get('profile')}",
                    f"trial_status={row.get('trial_status')}",
                    f"sampler_status={row.get('sampler_status')}",
                    f"q6_value_status={row.get('q6_value_status')}",
                    f"q6_value_support={row.get('q6_value_support_gate')}",
                    f"hurts={row.get('q6_value_hurt_count')}",
                    f"hurt_delta={row.get('q6_value_hurt_delta')}",
                    "top_hurts="
                    + ",".join(row.get("q6_value_top_hurts") or ()),
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prototype-json", type=Path, required=True)
    parser.add_argument("--guardability-json", type=Path, required=True)
    parser.add_argument("--baseline-guarded-trial-json", type=Path)
    parser.add_argument(
        "--profile",
        action="append",
        help=(
            "Explicit evidence_profile_key to probe. Defaults to "
            "guardability candidate_clusters with dimension=evidence_profile_key."
        ),
    )
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--component", action="append")
    parser.add_argument("--group-field", action="append")
    parser.add_argument("--movement-policy", action="append", choices=DEFAULT_POLICIES)
    parser.add_argument("--feature", action="append", choices=DEFAULT_FEATURES)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-windows", type=int, default=20)
    parser.add_argument("--min-sessions", type=int, default=8)
    parser.add_argument("--min-changed", type=int, default=5)
    parser.add_argument("--max-hurt-rate", type=float, default=0.45)
    parser.add_argument("--max-directional-error-rate", type=float, default=0.35)
    parser.add_argument("--posterior-trials", type=int, default=64)
    parser.add_argument("--posterior-seed", action="append", type=int)
    parser.add_argument(
        "--min-watch-support-rows",
        type=int,
        default=DEFAULT_MIN_WATCH_SUPPORT_ROWS,
    )
    parser.add_argument(
        "--min-watch-support-sessions",
        type=int,
        default=DEFAULT_MIN_WATCH_SUPPORT_SESSIONS,
    )
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    with args.prototype_json.open("r", encoding="utf-8-sig") as handle:
        prototype = json.load(handle)
    with args.guardability_json.open("r", encoding="utf-8-sig") as handle:
        guardability = json.load(handle)
    baseline_trial = None
    if args.baseline_guarded_trial_json is not None:
        with args.baseline_guarded_trial_json.open("r", encoding="utf-8-sig") as handle:
            baseline_trial = json.load(handle)

    tables = load_monitor_tables()
    calibration_entries = load_prior_calibration_entries(
        _default_calibration_path()
    )
    rows_by_seed: dict[int, list[dict[str, Any]]] = {}
    all_errors: list[str] = []
    for seed in args.posterior_seed or [0]:
        rows, errors = evaluate_paths(
            args.paths or _default_paths(),
            tables=tables,
            calibration_entries=calibration_entries,
            posterior_trials=args.posterior_trials,
            posterior_seed=int(seed),
            ccv_options=V3CcvOptions(
                component_likelihood=True,
                component_move_cells=False,
            ),
        )
        rows_by_seed[int(seed)] = rows
        all_errors.extend(str(error) for error in errors)

    result = {
        "errors": all_errors,
        **summarize_profile_guard_probes(
            rows_by_seed,
            prototype=prototype,
            guardability=guardability,
            baseline_trial=baseline_trial,
            profiles=args.profile or (),
            posterior_trials=args.posterior_trials,
            components=args.component or DEFAULT_COMPONENTS,
            group_fields=args.group_field or DEFAULT_TRIAL_GROUP_FIELDS,
            movement_policies=args.movement_policy or DEFAULT_POLICIES,
            features=args.feature or DEFAULT_FEATURES,
            folds=args.folds,
            min_windows=args.min_windows,
            min_sessions=args.min_sessions,
            min_changed=args.min_changed,
            max_hurt_rate=args.max_hurt_rate,
            max_directional_error_rate=args.max_directional_error_rate,
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
