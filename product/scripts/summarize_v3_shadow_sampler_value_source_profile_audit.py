"""Audit q6_value residual source/profile blockers from guarded trial artifacts."""

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

COMPONENT = "q6_value"
BLOCKING_COMPONENT_STATUSES = {
    "blocked_seed_instability",
    "blocked_low_support",
    "blocked_holdout_hurt",
    "watch_with_hurt_alternatives",
}
PUBLIC_PROFILE_TOKENS = {
    "public:total",
    "public:max_quality",
    "public:max_item_cells",
    "public:random_avg",
}
ANCHOR_PROFILE_TOKENS = {
    "tool:category",
    "item",
    "shape",
    "layout",
}


def _counter_dict(values: Iterable[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items()))


def _as_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _component_status(trial: Mapping[str, Any], component: str) -> Mapping[str, Any]:
    sampler = trial.get("guarded_sampler_result")
    if not isinstance(sampler, Mapping):
        return {}
    for row in _as_list(sampler.get("component_statuses")):
        if str(row.get("component") or "") == component:
            return row
    return {}


def _parse_group_parts(group: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for segment in str(group).split("|"):
        if "=" not in segment:
            continue
        key, value = segment.split("=", 1)
        parts[key] = value
    return parts


def _parse_watch_label(label: str) -> dict[str, str]:
    text = str(label or "")
    matrix, _, grouped = text.partition(":")
    component, group_field, movement_policy = (
        [*matrix.split("|"), "", ""][:3] if matrix else ("", "", "")
    )
    grouped_component, _, group = grouped.partition(":")
    parts = _parse_group_parts(group)
    if group_field == "map_id" and group:
        parts.setdefault("map_id", group)
    elif group_field == "evidence_profile_key" and group:
        parts.setdefault("evidence_profile_key", group)
    return {
        "watch_label": text,
        "matrix_label": matrix,
        "component": component,
        "grouped_component": grouped_component,
        "group_field": group_field,
        "movement_policy": movement_policy,
        "group": group,
        "map_id": parts.get("map_id") or "",
        "evidence_profile_key": parts.get("evidence_profile_key") or "",
    }


def _split_evidence_profile_key(profile: str) -> list[str]:
    text = str(profile or "").strip()
    if not text or text == "basic":
        return []
    return [part for part in text.split("+") if part]


def _parse_evidence_profile_key(profile: str) -> dict[str, Any]:
    tokens = _split_evidence_profile_key(profile)
    public_sources = [token for token in tokens if token in PUBLIC_PROFILE_TOKENS]
    anchors = [token for token in tokens if token in ANCHOR_PROFILE_TOKENS]
    unknown = [
        token
        for token in tokens
        if token not in PUBLIC_PROFILE_TOKENS
        and token not in ANCHOR_PROFILE_TOKENS
    ]
    source_class = "+".join(public_sources) if public_sources else "no_public"
    anchor_class = "+".join(anchors) if anchors else "no_anchor"
    semantic_class = f"{source_class}|{anchor_class}"
    return {
        "tokens": tokens,
        "public_sources": public_sources,
        "anchors": anchors,
        "unknown_tokens": unknown,
        "source_class": source_class,
        "anchor_class": anchor_class,
        "semantic_class": semantic_class,
        "has_public_total": "public:total" in public_sources,
        "has_public_max_item_cells": "public:max_item_cells" in public_sources,
        "has_public_random_avg": "public:random_avg" in public_sources,
        "has_item_anchor": "item" in anchors,
        "has_shape_anchor": "shape" in anchors,
        "has_layout_anchor": "layout" in anchors,
    }


def _metric_rows(component_status: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _as_list(component_status.get("top_applied_hurt_metrics")):
        item = dict(row)
        parsed = _parse_watch_label(str(item.get("watch_label") or ""))
        if not item.get("group") and parsed.get("group"):
            item["group"] = parsed["group"]
        profile = _parse_evidence_profile_key(parsed.get("evidence_profile_key") or "")
        item.update({f"parsed_{key}": value for key, value in parsed.items()})
        item.update({f"parsed_profile_{key}": value for key, value in profile.items()})
        out.append(item)
    return out


def _low_support_rows(component_status: Mapping[str, Any]) -> list[dict[str, Any]]:
    gate = component_status.get("support_gate")
    if not isinstance(gate, Mapping):
        return []
    out: list[dict[str, Any]] = []
    for row in _as_list(gate.get("low_support_watch_metrics")):
        item = dict(row)
        parsed = _parse_watch_label(str(item.get("watch_label") or ""))
        profile = _parse_evidence_profile_key(parsed.get("evidence_profile_key") or "")
        item.update({f"parsed_{key}": value for key, value in parsed.items()})
        item.update({f"parsed_profile_{key}": value for key, value in profile.items()})
        out.append(item)
    return out


def summarize_trial(
    trial: Mapping[str, Any],
    *,
    label: str,
    top: int = 12,
) -> dict[str, Any]:
    component_status = _component_status(trial, COMPONENT)
    support_gate = component_status.get("support_gate")
    support_gate_status = (
        str(support_gate.get("status") or "unknown")
        if isinstance(support_gate, Mapping)
        else "missing"
    )
    hurt_rows = _metric_rows(component_status)
    low_support_rows = _low_support_rows(component_status)
    group_fields = _counter_dict(
        row.get("parsed_group_field") for row in hurt_rows
    )
    movement_policies = _counter_dict(
        row.get("parsed_movement_policy") for row in hurt_rows
    )
    map_ids = sorted(
        {
            str(row.get("parsed_map_id") or "")
            for row in hurt_rows
            if row.get("parsed_map_id")
        }
    )
    evidence_profiles = sorted(
        {
            str(row.get("parsed_evidence_profile_key") or "")
            for row in hurt_rows
            if row.get("parsed_evidence_profile_key")
        }
    )
    profile_token_counts = _counter_dict(
        token
        for row in hurt_rows
        for token in row.get("parsed_profile_tokens") or ()
    )
    profile_public_source_counts = _counter_dict(
        token
        for row in hurt_rows
        for token in row.get("parsed_profile_public_sources") or ()
    )
    profile_anchor_counts = _counter_dict(
        token
        for row in hurt_rows
        for token in row.get("parsed_profile_anchors") or ()
    )
    profile_semantic_class_counts = _counter_dict(
        row.get("parsed_profile_semantic_class")
        for row in hurt_rows
        if row.get("parsed_evidence_profile_key")
    )
    profile_hurt_label_count = sum(
        1 for row in hurt_rows if row.get("parsed_evidence_profile_key")
    )
    map_only_hurt_label_count = sum(
        1
        for row in hurt_rows
        if row.get("parsed_group_field") == "map_id"
        and not row.get("parsed_evidence_profile_key")
    )
    component_state = str(component_status.get("status") or "missing")
    source_profile_parser_required = (
        component_state in BLOCKING_COMPONENT_STATUSES
        and (
            len(group_fields) > 1
            or len(map_ids) > 1
            or len(evidence_profiles) > 1
            or len(profile_semantic_class_counts) > 1
            or map_only_hurt_label_count > 0
            or support_gate_status in {"watch_low_support", "blocked_low_support"}
        )
    )
    return {
        "label": label,
        "trial_status": trial.get("status"),
        "sampler_status": trial.get("sampler_status"),
        "audit_probe": bool(trial.get("audit_probe")),
        "component": COMPONENT,
        "component_status": component_state,
        "support_gate": support_gate_status,
        "hurt_label_count": len(hurt_rows),
        "low_support_label_count": len(low_support_rows),
        "hurt_group_field_counts": group_fields,
        "hurt_movement_policy_counts": movement_policies,
        "hurt_map_ids": map_ids,
        "hurt_evidence_profiles": evidence_profiles,
        "profile_token_counts": profile_token_counts,
        "profile_public_source_counts": profile_public_source_counts,
        "profile_anchor_counts": profile_anchor_counts,
        "profile_semantic_class_counts": profile_semantic_class_counts,
        "profile_hurt_label_count": profile_hurt_label_count,
        "map_only_hurt_label_count": map_only_hurt_label_count,
        "top_hurt_labels": [
            str(row.get("watch_label") or "") for row in hurt_rows[:top]
        ],
        "top_hurt_metrics": hurt_rows[:top],
        "low_support_metrics": low_support_rows[:top],
        "source_profile_parser_required": source_profile_parser_required,
    }


def _semantic_classes(run: Mapping[str, Any]) -> set[str]:
    return {
        str(key)
        for key in (run.get("profile_semantic_class_counts") or {}).keys()
        if key
    }


def _source_profile_parser_summary(
    runs: list[dict[str, Any]],
    migration: Mapping[str, Any],
    *,
    top: int,
) -> dict[str, Any]:
    latest = runs[-1] if runs else {}
    latest_classes = _semantic_classes(latest)
    baseline_classes = _semantic_classes(runs[0]) if runs else set()
    introduced_classes = sorted(latest_classes - baseline_classes)
    removed_classes = sorted(baseline_classes - latest_classes)
    map_only_count = int(latest.get("map_only_hurt_label_count") or 0)
    profile_count = int(latest.get("profile_hurt_label_count") or 0)
    parser_required = bool(latest.get("source_profile_parser_required"))
    semantic_migration = bool(
        migration.get("risk_migration_detected")
        and (introduced_classes or map_only_count > 0)
    )
    if map_only_count > 0 and profile_count > 0 and parser_required:
        status = "blocked_mixed_map_profile_risk"
        next_action = (
            "join map-only q6_value hurt labels back to row-level evidence "
            "profiles before designing a value guard"
        )
    elif semantic_migration:
        status = "blocked_profile_semantic_migration"
        next_action = (
            "validate public-source and anchor classes across seed/session "
            "holdouts before any q6_value guard"
        )
    elif parser_required:
        status = "requires_profile_semantics"
        next_action = "classify q6_value source/profile semantics before retesting"
    else:
        status = "watch_diagnostic_only"
        next_action = "keep parser diagnostic shadow-only"
    return {
        "status": status,
        "profile_semantic_migration_detected": semantic_migration,
        "introduced_profile_semantic_classes": introduced_classes[:top],
        "removed_profile_semantic_classes": removed_classes[:top],
        "latest_profile_semantic_class_counts": dict(
            latest.get("profile_semantic_class_counts") or {}
        ),
        "latest_profile_public_source_counts": dict(
            latest.get("profile_public_source_counts") or {}
        ),
        "latest_profile_anchor_counts": dict(
            latest.get("profile_anchor_counts") or {}
        ),
        "latest_map_only_hurt_label_count": map_only_count,
        "latest_profile_hurt_label_count": profile_count,
        "minimum_required_inputs": [
            "map_id",
            "evidence_profile_key",
            "movement_policy",
            "candidate_rows",
            "candidate_sessions",
            "candidate_hurt_rate",
            "candidate_directional_error_rate",
            "support_gate_status",
        ],
        "blocked_actions": [
            "convert parser classes into promotion excludes without holdout",
            "treat map-only q6_value hurt labels as source-profile evidence",
            "resume formal/value sampler parameter tuning",
        ],
        "next_action": next_action,
    }


def summarize_value_source_profile_audit(
    trials: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    top: int = 12,
) -> dict[str, Any]:
    runs = [
        summarize_trial(trial, label=label, top=top)
        for label, trial in trials
    ]
    latest = runs[-1] if runs else {}
    migration: dict[str, Any] = {
        "status": "not_evaluated",
        "risk_migration_detected": False,
        "introduced_hurt_labels": [],
        "removed_hurt_labels": [],
    }
    if len(runs) >= 2:
        baseline = set(runs[0].get("top_hurt_labels") or ())
        current = set(runs[-1].get("top_hurt_labels") or ())
        introduced = sorted(current - baseline)
        removed = sorted(baseline - current)
        migration = {
            "status": "evaluated",
            "risk_migration_detected": bool(
                introduced
                and str(latest.get("component_status") or "")
                in BLOCKING_COMPONENT_STATUSES
            ),
            "introduced_hurt_labels": introduced[:top],
            "removed_hurt_labels": removed[:top],
        }
    source_profile_parser = _source_profile_parser_summary(
        runs,
        migration,
        top=top,
    )
    if migration.get("risk_migration_detected"):
        status = "blocked_risk_migration"
        next_action = str(source_profile_parser.get("next_action") or "")
    elif latest.get("source_profile_parser_required"):
        status = "requires_source_profile_parser"
        next_action = str(source_profile_parser.get("next_action") or "")
    elif latest.get("component_status") in BLOCKING_COMPONENT_STATUSES:
        status = "blocked_q6_value_component"
        next_action = "keep q6_value inactive and collect stronger evidence"
    else:
        status = "watch_diagnostic_only"
        next_action = "keep as shadow diagnostic; do not promote from this audit"
    return {
        "interface": "v3_ccvc_q6_value_source_profile_audit",
        "status": status,
        "shadow_only": True,
        "affects_bid": False,
        "active": False,
        "can_promote": False,
        "component": COMPONENT,
        "run_count": len(runs),
        "runs": runs,
        "migration": migration,
        "source_profile_parser": source_profile_parser,
        "next_action": next_action,
        "blocked_actions": [
            "change formal bid path",
            "wire q6 value audit into live decisions",
            "treat manual excludes as promotion guard",
            "archive v2 fallback",
            "relax readiness or promotion gates",
        ],
    }


def _print_summary(result: Mapping[str, Any], *, top: int) -> None:
    migration = result.get("migration") or {}
    parser = result.get("source_profile_parser") or {}
    print(
        " ".join(
            (
                f"status={result.get('status')}",
                f"component={result.get('component')}",
                f"runs={result.get('run_count')}",
                f"migration={migration.get('risk_migration_detected')}",
                f"parser={parser.get('status')}",
                f"semantic_migration={parser.get('profile_semantic_migration_detected')}",
                f"map_only_hurts={parser.get('latest_map_only_hurt_label_count')}",
                "introduced="
                + ",".join((migration.get("introduced_hurt_labels") or ())[:top]),
                f"next_action=\"{result.get('next_action')}\"",
            )
        )
    )
    for run in result.get("runs") or ():
        print(
            " ".join(
                (
                    f"run={run.get('label')}",
                    f"trial={run.get('trial_status')}",
                    f"component_status={run.get('component_status')}",
                    f"support_gate={run.get('support_gate')}",
                    f"audit_probe={run.get('audit_probe')}",
                    "map_ids=" + ",".join(run.get("hurt_map_ids") or ()),
                    "profiles="
                    + ",".join((run.get("hurt_evidence_profiles") or ())[:top]),
                    "profile_classes="
                    + ",".join(
                        (
                            run.get("profile_semantic_class_counts")
                            or {}
                        ).keys()
                    ),
                    "top_hurts="
                    + ",".join((run.get("top_hurt_labels") or ())[:top]),
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit q6_value source/profile residual blockers from guarded "
            "sampler trial JSON artifacts."
        ),
    )
    parser.add_argument(
        "--guarded-trial-json",
        action="append",
        type=Path,
        required=True,
        help="Guarded trial JSON. Pass more than once to compare migration.",
    )
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    trials: list[tuple[str, Mapping[str, Any]]] = []
    for path in args.guarded_trial_json:
        with path.open("r", encoding="utf-8-sig") as handle:
            trials.append((path.stem, json.load(handle)))
    result = summarize_value_source_profile_audit(trials, top=args.top)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(result, top=args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
