"""Summarize v3 readiness blockers across archive/live shadow gates."""

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
    _default_calibration_path,
    _default_capacity_source_expansion_path,
    _default_paths,
    _default_settlement_count_prior_path,
    _default_tail_value_review_path,
    _default_underestimate_repair_path,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
    load_capacity_source_expansion_entries,
    load_settlement_count_prior_entries,
    load_tail_value_review_entries,
    load_underestimate_repair_entries,
    summarize_rows,
)
from summarize_v3_ccv_profile_candidates import (  # noqa: E402
    summarize_candidates as summarize_ccv_candidates,
)
from summarize_v3_ccv_holdout import (  # noqa: E402
    summarize_holdout as summarize_ccv_holdout,
)
from summarize_v3_ccv_direction_audit import (  # noqa: E402
    summarize_direction as summarize_ccv_direction,
)
from summarize_v3_ccv_direction_holdout import (  # noqa: E402
    summarize_holdout as summarize_ccv_direction_holdout,
)
from summarize_v3_residual_profile_candidates import (  # noqa: E402
    summarize_candidates as summarize_residual_candidates,
)
from summarize_v3_tail_value_candidates import (  # noqa: E402
    summarize_candidates as summarize_tail_candidates,
)
from summarize_v3_tail_value_holdout import (  # noqa: E402
    summarize_holdout as summarize_tail_holdout,
)
from summarize_v3_tail_under_holdout import (  # noqa: E402
    summarize_holdout as summarize_tail_under_holdout,
)
from summarize_v3_formal_value_sampler_holdout import (  # noqa: E402
    summarize_holdout as summarize_formal_value_sampler_holdout,
)
from summarize_v3_scp_formal_value_link import (  # noqa: E402
    summarize_link as summarize_scp_formal_value_link,
)
from summarize_v3_scp_count_value_bridge import (  # noqa: E402
    summarize_bridge as summarize_scp_count_value_bridge,
)
from summarize_v3_scp_count_value_bridge_holdout import (  # noqa: E402
    summarize_holdout as summarize_scp_count_value_bridge_holdout,
)
from summarize_v3_scp_guarded_bridge_holdout import (  # noqa: E402
    summarize_guarded_holdout as summarize_scp_guarded_bridge_holdout,
)
from summarize_v3_prior_robustness_audit import (  # noqa: E402
    summarize_prior_stress_details,
    summarize_prior_stress_detail_summary,
)
from summarize_v3_underestimate_holdout import (  # noqa: E402
    summarize_holdout as summarize_under_holdout,
)


def _status_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get("candidate_status")) for row in rows).items()))


def _top_groups(
    rows: Iterable[dict[str, Any]],
    status: str,
    *,
    limit: int = 5,
) -> list[str]:
    return [
        str(row.get("group"))
        for row in rows
        if row.get("candidate_status") == status
    ][:limit]


def _gate(
    name: str,
    status: str,
    reason: str,
    **fields: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "reason": reason,
        **fields,
    }


def _blocked_count(gates: Iterable[dict[str, Any]]) -> int:
    return sum(1 for gate in gates if str(gate.get("status")) == "blocked")


_GATE_DEPENDENCY_META: dict[str, tuple[str, str]] = {
    "archive_data_quality": (
        "archive_pipeline_quality",
        "fix parse/constraint issues before trusting archive metrics",
    ),
    "shared_shadow_pipeline": (
        "archive_pipeline_quality",
        "keep v3 posterior shadow coverage complete and inactive",
    ),
    "v3_practical_archive_live_guard_metrics": (
        "archive_live_guard_metrics",
        "attach paired guarded/unguarded v3 practical archive-live evidence",
    ),
    "prior_robustness": (
        "table_activity_capacity",
        "resolve missing/activity/prior-drift rows before default prior use",
    ),
    "prior_stress_capacity_table_drift": (
        "table_activity_capacity",
        "audit cells/capacity/evidence semantics by map/profile",
    ),
    "settlement_count_prior_shadow": (
        "table_activity_capacity",
        "keep settlement count prior visible and inactive while table gaps remain",
    ),
    "capacity_source_expansion_shadow": (
        "table_activity_capacity",
        "keep capacity/source expansion evidence visible and inactive until the capacity blocker is fully closed",
    ),
    "settlement_count_formal_value_link": (
        "formal_value_shadow_sampler",
        "prove settlement count candidates overlap value/formal stress",
    ),
    "settlement_count_cells_value_bridge": (
        "settlement_bridge_support",
        "find count->cells/value bridge candidates before holdout",
    ),
    "settlement_count_cells_value_bridge_holdout": (
        "settlement_bridge_support",
        "hold out count->cells/value bridge before sampler use",
    ),
    "settlement_count_guarded_bridge_holdout": (
        "settlement_bridge_support",
        "collect support and seed-stability for guarded settlement bridge",
    ),
    "settlement_count_guarded_bridge_stability": (
        "settlement_bridge_support",
        "prove guarded settlement bridge selection is stable across posterior seeds",
    ),
    "formal_baseline_metrics": (
        "formal_value_shadow_sampler",
        "formal baseline must enter promotion band or be safely bridged",
    ),
    "underestimate_repair_holdout": (
        "sampler_safety_holdout",
        "keep bounded upshift inactive until holdout remains safe",
    ),
    "ccv_sampler": (
        "sampler_safety_holdout",
        "prove CCV candidate signal without global/map-layer hurt",
    ),
    "ccv_directionality": (
        "sampler_safety_holdout",
        "remove directional p50 hurt before CCV promotion",
    ),
    "ccv_direction_holdout": (
        "sampler_safety_holdout",
        "hold out directionally selected CCV movements",
    ),
    "shadow_sampler_prototype": (
        "sampler_safety_holdout",
        "attach support-aware CCVC shadow sampler prototype evidence",
    ),
    "shadow_sampler_guard_trial": (
        "sampler_safety_holdout",
        "attach guarded CCVC shadow sampler trial holdout evidence",
    ),
    "shadow_sampler_value_source_profile_audit": (
        "sampler_safety_holdout",
        "resolve q6_value source/profile risk migration before sampler tuning",
    ),
    "shadow_sampler_value_map_profile_details": (
        "sampler_safety_holdout",
        "review q6_value map-only row/source clusters before value guard design",
    ),
    "shadow_sampler_value_profile_guardability": (
        "sampler_safety_holdout",
        "keep q6_value inactive unless source/profile guardability passes holdout",
    ),
    "tail_value_review": (
        "sampler_safety_holdout",
        "keep tail/value review non-formal until holdout is stable",
    ),
    "tail_under_combined_holdout": (
        "sampler_safety_holdout",
        "hold out combined under/tail policy before formal use",
    ),
    "formal_value_sampler_holdout": (
        "formal_value_shadow_sampler",
        "prove formal/value sampler support without over/under regressions",
    ),
    "residual_gate": (
        "sampler_safety_holdout",
        "residual remains watch-only with active rows at zero",
    ),
    "profile_sample_depth": (
        "profile_sample_depth",
        "collect enough profile-level samples before profile promotion",
    ),
    "v2_archive_readiness": (
        "v2_archive_after_promotion",
        "archive v2 only after v3 formal path is promoted and verified",
    ),
}


def _selected_group_instability_text(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for row in value:
        if not isinstance(row, Mapping):
            continue
        group = str(row.get("group") or "unknown")
        status = str(row.get("status") or "unknown")
        parts.append(f"{group}:{status}")
    return ",".join(parts)


def _counter_from_count_text(value: Any) -> Counter[str]:
    counts: Counter[str] = Counter()
    if isinstance(value, Mapping):
        for key, count in value.items():
            try:
                counts[str(key)] += int(count or 0)
            except (TypeError, ValueError):
                continue
        return counts
    if not isinstance(value, str):
        return counts
    for part in value.split(","):
        token = part.strip()
        if not token or ":" not in token:
            continue
        key, raw_count = token.rsplit(":", 1)
        try:
            counts[key] += int(raw_count)
        except ValueError:
            continue
    return counts


def _counter_text(value: Mapping[str, Any] | None, *, limit: int = 4) -> str:
    if not isinstance(value, Mapping):
        return ""
    return ",".join(
        f"{key}:{count}"
        for key, count in sorted(
            value.items(),
            key=lambda item: (-int(item[1] or 0), str(item[0])),
        )[:limit]
    )


def _top_counter_text(value: Any, *, limit: int = 3) -> str:
    return _counter_text(
        value if isinstance(value, Mapping) else None,
        limit=limit,
    )


def _numeric_max(value: Any) -> float | None:
    if not isinstance(value, Mapping):
        return None
    raw = value.get("max")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _gate_focus(gate: Mapping[str, Any]) -> str:
    name = str(gate.get("name") or "")
    if name == "prior_robustness":
        activity = int(gate.get("robust_activity_candidate") or 0)
        stressed = int(gate.get("robust_prior_stressed") or 0)
        untrusted = int(gate.get("ready") or 0) - int(
            gate.get("robust_prior_trusted") or 0
        )
        parts = []
        if activity:
            parts.append(f"activity_candidate_rows={activity}")
        if stressed:
            parts.append(f"prior_stressed_rows={stressed}")
        if untrusted > 0:
            parts.append(f"untrusted_ready_rows={untrusted}")
        return ";".join(parts)
    if name == "prior_stress_capacity_table_drift":
        hits = int(gate.get("capacity_flag_hits") or 0)
        rows = int(gate.get("detail_rows") or 0)
        contract = gate.get("detail_contract")
        case_counts = (
            contract.get("case_counts")
            if isinstance(contract, Mapping)
            else None
        )
        parts = [f"detail_rows={rows}", f"capacity_flag_hits={hits}"]
        if isinstance(case_counts, Mapping) and case_counts:
            top_cases = ",".join(
                f"{key}:{value}"
                for key, value in sorted(
                    case_counts.items(),
                    key=lambda item: (-int(item[1] or 0), str(item[0])),
                )[:3]
            )
            parts.append(f"top_cases={top_cases}")
        capacity_table = gate.get("capacity_table_audit_contract")
        if isinstance(capacity_table, Mapping):
            semantic_status = _counter_text(
                capacity_table.get("semantic_status_counts"),
                limit=3,
            )
            residual_modes = _counter_text(
                capacity_table.get("residual_mode_counts"),
                limit=3,
            )
            detail_mechanisms = _counter_text(
                capacity_table.get("detail_mechanism_candidate_counts"),
                limit=3,
            )
            detail_next_checks = _counter_text(
                capacity_table.get("detail_next_check_counts"),
                limit=3,
            )
            top_maps = [
                str(row.get("map_id"))
                for row in capacity_table.get("top_blocked_maps") or []
                if isinstance(row, Mapping) and row.get("map_id") is not None
            ][:3]
            if semantic_status:
                parts.append(f"semantic_status={semantic_status}")
            if residual_modes:
                parts.append(f"residual_modes={residual_modes}")
            if detail_mechanisms:
                parts.append(f"detail_mechanisms={detail_mechanisms}")
            if detail_next_checks:
                parts.append(f"detail_next_checks={detail_next_checks}")
            if top_maps:
                parts.append(f"top_maps={','.join(top_maps)}")
        acquisition = gate.get("capacity_table_acquisition_contract")
        if isinstance(acquisition, Mapping):
            routes = _counter_text(acquisition.get("route_counts"), limit=3)
            overlay = acquisition.get("table_overlay_metadata")
            overlay_status = (
                overlay.get("local_overlay_status")
                if isinstance(overlay, Mapping)
                else None
            )
            if routes:
                parts.append(f"acquisition_routes={routes}")
            if overlay_status:
                parts.append(f"overlay={overlay_status}")
        return ";".join(parts)
    if name == "settlement_count_prior_shadow":
        missing = int(gate.get("missing_table_rows") or 0)
        candidates = int(gate.get("candidate_rows") or 0)
        return f"candidate_rows={candidates};missing_table_rows={missing}"
    if name == "capacity_source_expansion_shadow":
        candidates = int(gate.get("candidate_rows") or 0)
        pressure = int(gate.get("pressure_candidate_rows") or 0)
        active = int(gate.get("active_rows") or 0)
        parts = [
            f"candidate_rows={candidates}",
            f"pressure_candidate_rows={pressure}",
            f"active_rows={active}",
        ]
        contract = gate.get("artifact_contract")
        if isinstance(contract, Mapping):
            overflow = int(contract.get("unique_round_overflow_rows") or 0)
            session_semantics = int(
                contract.get("session_capacity_source_semantics_rows") or 0
            )
            server_expansion = int(
                contract.get("server_side_expansion_rows") or 0
            )
            if overflow:
                parts.append(f"unique_round_overflow_rows={overflow}")
            if session_semantics:
                parts.append(
                    "session_capacity_source_semantics_rows="
                    f"{session_semantics}"
                )
            if server_expansion:
                parts.append(
                    f"server_side_expansion_rows={server_expansion}"
                )
            source_split_status = str(
                contract.get("source_split_status") or ""
            )
            if source_split_status:
                parts.append(f"source_split={source_split_status}")
        return ";".join(parts)
    if name in {
        "settlement_count_cells_value_bridge_holdout",
        "settlement_count_guarded_bridge_holdout",
        "settlement_count_guarded_bridge_stability",
    }:
        applied = gate.get("applied_rows")
        selected = gate.get("selected_group_fold_counts") or {}
        if not selected:
            selected = gate.get("selected_signature_counts") or {}
        parts = [f"applied_rows={applied}"]
        if selected:
            parts.append(
                "selected_groups="
                + ",".join(
                    f"{key}:{selected[key]}"
                    for key in sorted(selected)
                )
            )
        if name == "settlement_count_guarded_bridge_stability":
            instability = _selected_group_instability_text(
                gate.get("selected_group_instability_summary")
            )
            if instability:
                parts.append(f"instability={instability}")
        return ";".join(parts)
    if name == "settlement_count_formal_value_link":
        return (
            f"formal_rows={gate.get('scp_candidate_formal_rows')};"
            f"value_floor_rows={gate.get('scp_candidate_value_floor_rows')};"
            f"capacity_watch_rows={gate.get('scp_candidate_capacity_watch_rows')}"
        )
    if name == "formal_value_sampler_holdout":
        return (
            f"candidate_rows={gate.get('candidate_rows')};"
            f"candidate_groups={','.join(gate.get('candidate_groups') or [])}"
        )
    if name == "shadow_sampler_prototype":
        return (
            f"prototype_status={gate.get('prototype_status')};"
            f"contract={gate.get('contract_status')};"
            f"component_statuses={gate.get('component_status_counts')};"
            f"support_gates={gate.get('support_gate_status_counts')}"
        )
    if name == "shadow_sampler_guard_trial":
        return (
            f"trial_status={gate.get('trial_status')};"
            f"contract={gate.get('contract_status')};"
            f"sampler_status={gate.get('sampler_status')};"
            f"component_statuses={gate.get('component_status_counts')};"
            f"support_gates={gate.get('support_gate_status_counts')}"
        )
    if name == "shadow_sampler_value_source_profile_audit":
        return (
            f"audit_status={gate.get('audit_status')};"
            f"contract={gate.get('contract_status')};"
            f"component={gate.get('component')};"
            f"risk_migration={gate.get('risk_migration_detected')};"
            f"parser={gate.get('source_profile_parser_status')};"
            f"run_count={gate.get('run_count')}"
        )
    if name == "shadow_sampler_value_map_profile_details":
        return (
            f"details_status={gate.get('details_status')};"
            f"contract={gate.get('contract_status')};"
            f"labels={gate.get('label_count')};"
            f"candidate_rows={gate.get('candidate_rows')};"
            f"mismatches={len(gate.get('labels_with_row_count_mismatch') or [])}"
        )
    if name == "shadow_sampler_value_profile_guardability":
        return (
            f"guardability_status={gate.get('guardability_status')};"
            f"contract={gate.get('contract_status')};"
            f"clusters={gate.get('cluster_count')};"
            f"candidates={gate.get('candidate_cluster_count')};"
            f"rows={gate.get('detail_rows')}"
        )
    if name == "formal_baseline_metrics":
        return (
            f"below_rate={gate.get('formal_p50_below_rate')};"
            f"p90_coverage={gate.get('formal_p90_coverage')}"
        )
    if name == "profile_sample_depth":
        return (
            f"ccv_profile_rows={gate.get('ccv_profile_holdout_candidate_rows')};"
            f"tail_profile_rows={gate.get('tail_profile_holdout_candidate_rows')}"
        )
    return ""


def summarize_gate_dependencies(gates: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    lane_status_counts: dict[str, Counter[str]] = {}
    for gate in gates:
        name = str(gate.get("name") or "")
        lane, action = _GATE_DEPENDENCY_META.get(
            name,
            ("other", "review gate-specific evidence"),
        )
        status = str(gate.get("status") or "unknown")
        lane_status_counts.setdefault(lane, Counter())[status] += 1
        rows.append(
            {
                "gate": name,
                "status": status,
                "lane": lane,
                "blocking": status in {"blocked", "pending"},
                "focus": _gate_focus(gate),
                "action": action,
            }
        )
    blocked_or_pending = [
        row for row in rows if row["status"] in {"blocked", "pending"}
    ]
    watch = [row for row in rows if row["status"] == "watch"]
    return {
        "lane_status_counts": {
            lane: dict(sorted(counts.items()))
            for lane, counts in sorted(lane_status_counts.items())
        },
        "blocked_or_pending_lanes": sorted(
            {row["lane"] for row in blocked_or_pending}
        ),
        "blocked_or_pending_gates": blocked_or_pending,
        "watch_gates": watch,
    }


def _ccv_applied_hurt_groups(result: dict[str, Any]) -> list[str]:
    return [
        str(row.get("group"))
        for row in result.get("group_results", ())
        if int(row.get("candidate_rows") or 0) > 0
        and (
            (
                row.get("delta_q6_count_p50_mae") is not None
                and float(row["delta_q6_count_p50_mae"]) > 0.05
            )
            or (
                row.get("delta_q6_cells_p50_mae") is not None
                and float(row["delta_q6_cells_p50_mae"]) > 0.25
            )
            or (
                row.get("delta_q6_value_p50_mae") is not None
                and float(row["delta_q6_value_p50_mae"]) > 10_000.0
            )
            or (
                row.get("delta_q6_formal_p50_mae") is not None
                and float(row["delta_q6_formal_p50_mae"]) > 10_000.0
            )
        )
    ][:5]


def _ccv_directional_hurts(rows: Iterable[dict[str, Any]]) -> list[str]:
    return [
        f"{row.get('component')}:{row.get('group')}"
        for row in rows
        if row.get("status") == "blocked_directional_hurt"
    ][:8]


def _top_prior_stress_groups(
    detail_summary: dict[str, Any],
    field: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in detail_summary.get("by_group", ()):
        if row.get("field") != field:
            continue
        out.append(
            {
                "value": row.get("value"),
                "rows": row.get("rows"),
                "capacity_flag_hits": row.get("capacity_flag_hits"),
                "max_cells_ratio": row.get("max_cells_ratio"),
                "max_value_ratio": row.get("max_value_ratio"),
                "capacity_flag_counts": row.get("capacity_flag_counts"),
                "capacity_count_summary": row.get("capacity_count_summary"),
                "consistency_bucket_counts": row.get(
                    "consistency_bucket_counts"
                ),
                "consistency_class_counts": row.get(
                    "consistency_class_counts"
                ),
                "reason_counts": row.get("reason_counts"),
                "source_counts": row.get("source_counts"),
            }
        )
    return out[:limit]


def summarize_prior_stress_detail_contract(
    detail_summary: Mapping[str, Any],
    *,
    top_map_groups: list[dict[str, Any]] | None = None,
    top_profile_groups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    overall = detail_summary.get("overall")
    if not isinstance(overall, Mapping):
        return {
            "status": "blocked",
            "reason": "prior-stress detail summary is missing overall block",
            "missing_keys": ["overall"],
            "rows": 0,
            "capacity_flag_hits": 0,
            "case_counts": {},
            "consistency_bucket_counts": {},
            "top_map_groups": [],
            "top_profile_groups": [],
        }
    required_keys = (
        "rows",
        "capacity_flag_counts",
        "capacity_count_summary",
        "consistency_bucket_counts",
        "consistency_class_counts",
        "source_counts",
        "ratio_summary",
    )
    missing_keys = [key for key in required_keys if key not in overall]
    capacity_count_summary = overall.get("capacity_count_summary")
    case_counts = (
        capacity_count_summary.get("case_counts")
        if isinstance(capacity_count_summary, Mapping)
        else {}
    )
    consistency_bucket_counts = overall.get("consistency_bucket_counts")
    capacity_flag_counts = overall.get("capacity_flag_counts")
    rows = int(overall.get("rows") or 0)
    capacity_flag_hits = (
        sum(int(value or 0) for value in capacity_flag_counts.values())
        if isinstance(capacity_flag_counts, Mapping)
        else 0
    )
    blockers: list[str] = []
    if missing_keys:
        blockers.append("missing prior-stress detail fields")
    if rows > 0 and not isinstance(case_counts, Mapping):
        blockers.append("capacity_count_summary.case_counts is missing")
    if rows > 0 and not isinstance(consistency_bucket_counts, Mapping):
        blockers.append("consistency_bucket_counts is missing")
    if rows > 0 and not top_map_groups:
        blockers.append("top map groups are missing for non-empty detail summary")
    if rows > 0 and not top_profile_groups:
        blockers.append("top profile groups are missing for non-empty detail summary")
    if blockers:
        status = "blocked"
        reason = "; ".join(blockers)
    elif rows > 0 or capacity_flag_hits > 0:
        status = "watch"
        reason = "prior-stress drift evidence is classified but still blocks promotion"
    else:
        status = "pass"
        reason = "no prior-stress drift detail rows"
    return {
        "status": status,
        "reason": reason,
        "missing_keys": missing_keys,
        "rows": rows,
        "capacity_flag_hits": capacity_flag_hits,
        "case_counts": dict(case_counts) if isinstance(case_counts, Mapping) else {},
        "consistency_bucket_counts": (
            dict(consistency_bucket_counts)
            if isinstance(consistency_bucket_counts, Mapping)
            else {}
        ),
        "capacity_flag_counts": (
            dict(capacity_flag_counts)
            if isinstance(capacity_flag_counts, Mapping)
            else {}
        ),
        "top_map_groups": [
            {
                "value": row.get("value"),
                "rows": row.get("rows"),
                "capacity_flag_hits": row.get("capacity_flag_hits"),
            }
            for row in (top_map_groups or [])[:5]
        ],
        "top_profile_groups": [
            {
                "value": row.get("value"),
                "rows": row.get("rows"),
                "capacity_flag_hits": row.get("capacity_flag_hits"),
            }
            for row in (top_profile_groups or [])[:5]
        ],
    }


def summarize_capacity_table_audit_contract(
    audit: Mapping[str, Any] | None,
    *,
    top: int = 5,
) -> dict[str, Any]:
    if audit is None:
        return {
            "status": "not_supplied",
            "reason": "capacity table audit JSON was not supplied",
            "missing_keys": ["rows", "semantic_matrix"],
            "map_rows": 0,
            "semantic_matrix_cells": 0,
            "semantic_status_counts": {},
            "residual_mode_counts": {},
            "detail_mechanism_candidate_counts": {},
            "detail_next_check_counts": {},
            "detail_source_signal_counts": {},
            "top_blocked_maps": [],
            "top_semantic_matrix_cells": [],
            "top_detail_examples": [],
        }
    if not isinstance(audit, Mapping):
        return {
            "status": "blocked",
            "reason": "capacity table audit JSON must be an object",
            "missing_keys": ["rows", "semantic_matrix"],
            "map_rows": 0,
            "semantic_matrix_cells": 0,
            "semantic_status_counts": {},
            "residual_mode_counts": {},
            "detail_mechanism_candidate_counts": {},
            "detail_next_check_counts": {},
            "detail_source_signal_counts": {},
            "top_blocked_maps": [],
            "top_semantic_matrix_cells": [],
            "top_detail_examples": [],
        }

    rows_value = audit.get("rows")
    matrix_value = audit.get("semantic_matrix")
    detail_summary_value = audit.get("detail_summary")
    errors_value = audit.get("errors")
    rows = rows_value if isinstance(rows_value, list) else []
    matrix = matrix_value if isinstance(matrix_value, list) else []
    detail_summary = (
        detail_summary_value if isinstance(detail_summary_value, Mapping) else {}
    )
    errors = errors_value if isinstance(errors_value, list) else []
    missing_keys = [
        key
        for key, value in (
            ("rows", rows_value),
            ("semantic_matrix", matrix_value),
        )
        if not isinstance(value, list)
    ]
    semantic_status_counts: Counter[str] = Counter()
    residual_mode_counts: Counter[str] = Counter()
    for cell in matrix:
        if not isinstance(cell, Mapping):
            continue
        row_count = int(cell.get("rows") or 0)
        residual_mode = str(cell.get("residual_mode") or "unknown")
        if row_count:
            residual_mode_counts[residual_mode] += row_count
        semantic_status_counts.update(
            _counter_from_count_text(cell.get("semantic_status_counts"))
        )
    if not semantic_status_counts:
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            summary = row.get("capacity_semantic_summary")
            if not isinstance(summary, Mapping):
                continue
            status = str(summary.get("status") or "unknown")
            semantic_status_counts[status] += int(row.get("rows") or 0)
    if not residual_mode_counts:
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            residual_summary = row.get("residual_mode_summary")
            if not isinstance(residual_summary, Mapping):
                continue
            residual_mode_counts.update(
                _counter_from_count_text(residual_summary.get("mode_counts"))
            )

    top_maps: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        summary = row.get("capacity_semantic_summary")
        if not isinstance(summary, Mapping):
            summary = {}
        source_split = row.get("source_split_summary")
        if not isinstance(source_split, Mapping):
            source_split = {}
        residual_summary = row.get("residual_mode_summary")
        residual_modes = (
            residual_summary.get("mode_counts")
            if isinstance(residual_summary, Mapping)
            else {}
        )
        top_maps.append(
            {
                "map_id": row.get("map_id"),
                "map_name": row.get("map_name"),
                "rows": row.get("rows"),
                "status": row.get("status"),
                "semantic_status": summary.get("status"),
                "capacity_cases": dict(row.get("capacity_cases") or {}),
                "residual_modes": dict(residual_modes or {}),
                "drop_ref_excess_after_temp_zodiac_max": summary.get(
                    "drop_ref_excess_after_temp_zodiac_max"
                ),
                "round_cap_excess_after_temp_zodiac_max": summary.get(
                    "round_cap_excess_after_temp_zodiac_max"
                ),
                "non_zodiac_missing_from_drop_universe_max": summary.get(
                    "non_zodiac_missing_from_drop_universe_max"
                ),
                "truth_prior_max_delta_max": _numeric_max(
                    source_split.get("truth_prior_max_delta")
                ),
            }
        )
    top_maps.sort(
        key=lambda row: (
            -int(row.get("rows") or 0),
            str(row.get("map_id") or ""),
        )
    )

    top_cells: list[dict[str, Any]] = []
    for cell in matrix:
        if not isinstance(cell, Mapping):
            continue
        top_cells.append(
            {
                "rows": cell.get("rows"),
                "files": cell.get("files"),
                "map_ids": dict(cell.get("map_ids") or {}),
                "map_family": cell.get("map_family"),
                "residual_mode": cell.get("residual_mode"),
                "semantic_status_counts": dict(
                    cell.get("semantic_status_counts") or {}
                ),
                "capacity_cases": dict(cell.get("capacity_cases") or {}),
                "total_count_source": cell.get("total_count_source"),
                "full_action_signal": cell.get("full_action_signal"),
                "public_total_signal": cell.get("public_total_signal"),
            }
        )
    top_cells.sort(
        key=lambda row: (
            -int(row.get("rows") or 0),
            str(row.get("residual_mode") or ""),
            str(row.get("map_family") or ""),
        )
    )

    blockers: list[str] = []
    if missing_keys:
        blockers.append("capacity table audit is missing contract fields")
    if errors:
        blockers.append("capacity table audit reported errors")
    return {
        "status": "blocked" if blockers else "watch",
        "reason": (
            "; ".join(blockers)
            if blockers
            else "capacity table semantic residual audit is attached"
        ),
        "missing_keys": missing_keys,
        "errors": errors[:top],
        "case": audit.get("case"),
        "bucket": audit.get("bucket"),
        "map_rows": len(rows),
        "semantic_matrix_cells": len(matrix),
        "semantic_status_counts": dict(sorted(semantic_status_counts.items())),
        "residual_mode_counts": dict(sorted(residual_mode_counts.items())),
        "detail_rows": detail_summary.get("rows"),
        "detail_unique_file_map_residual_rows": detail_summary.get(
            "unique_file_map_residual_rows"
        ),
        "detail_unique_files": detail_summary.get("unique_files"),
        "detail_mechanism_candidate_counts": dict(
            detail_summary.get("mechanism_candidate_counts") or {}
        ),
        "detail_next_check_counts": dict(
            detail_summary.get("next_check_counts") or {}
        ),
        "detail_source_signal_counts": dict(
            detail_summary.get("source_signal_counts") or {}
        ),
        "detail_map_counts": dict(detail_summary.get("map_counts") or {}),
        "top_blocked_maps": top_maps[:top],
        "top_semantic_matrix_cells": top_cells[:top],
        "top_detail_examples": list(detail_summary.get("top_examples") or [])[:top],
    }


def summarize_capacity_table_acquisition_contract(
    audit: Mapping[str, Any] | None,
    *,
    top: int = 5,
) -> dict[str, Any]:
    empty = {
        "route_counts": {},
        "source_strength_counts": {},
        "map_counts": {},
        "table_overlay_metadata": {},
        "current_table_overlay_metadata": {},
        "artifact_table_overlay_metadata": {},
        "table_overlay_metadata_stale": False,
        "table_overlay_metadata_delta": [],
        "top_examples": [],
    }
    if audit is None:
        return {
            "status": "not_supplied",
            "reason": "capacity table acquisition audit JSON was not supplied",
            "missing_keys": ["status", "acquisition_route_counts"],
            "acquisition_status": None,
            "detail_rows": 0,
            "unique_detail_rows": 0,
            "unique_files": 0,
            **empty,
        }
    if not isinstance(audit, Mapping):
        return {
            "status": "blocked",
            "reason": "capacity table acquisition audit JSON must be an object",
            "missing_keys": ["status", "acquisition_route_counts"],
            "acquisition_status": None,
            "detail_rows": 0,
            "unique_detail_rows": 0,
            "unique_files": 0,
            **empty,
        }

    route_counts = audit.get("acquisition_route_counts")
    source_counts = audit.get("source_strength_counts")
    map_counts = audit.get("map_counts")
    overlay = audit.get("table_overlay_metadata")
    missing_keys = [
        key
        for key, value, expected_type in (
            ("status", audit.get("status"), str),
            ("acquisition_route_counts", route_counts, Mapping),
            ("source_strength_counts", source_counts, Mapping),
            ("map_counts", map_counts, Mapping),
            ("table_overlay_metadata", overlay, Mapping),
        )
        if not isinstance(value, expected_type)
    ]
    route_counts = route_counts if isinstance(route_counts, Mapping) else {}
    source_counts = source_counts if isinstance(source_counts, Mapping) else {}
    map_counts = map_counts if isinstance(map_counts, Mapping) else {}
    overlay = overlay if isinstance(overlay, Mapping) else {}
    current_overlay = audit.get("current_table_overlay_metadata")
    current_overlay = (
        current_overlay if isinstance(current_overlay, Mapping) else {}
    )
    artifact_overlay = audit.get("artifact_table_overlay_metadata")
    artifact_overlay = (
        artifact_overlay if isinstance(artifact_overlay, Mapping) else {}
    )
    overlay_delta = audit.get("table_overlay_metadata_delta")
    overlay_delta = overlay_delta if isinstance(overlay_delta, list) else []
    acquisition_status = str(audit.get("status") or "unknown")
    blockers: list[str] = []
    if missing_keys:
        blockers.append("capacity table acquisition audit is missing contract fields")
    if acquisition_status == "blocked_acquisition_required":
        blockers.append("capacity table acquisition is still required")
    if str(overlay.get("local_overlay_status") or "") in {
        "activity_listed_missing_locally",
        "v300_activity_listed_missing_locally",
    }:
        blockers.append("activity overlay table is listed but missing locally")
    if audit.get("table_overlay_metadata_stale") is True:
        blockers.append("capacity table overlay metadata drifted from source artifact")
    return {
        "status": "blocked" if blockers else "watch",
        "reason": (
            "; ".join(blockers)
            if blockers
            else "capacity table acquisition routes are attached"
        ),
        "missing_keys": missing_keys,
        "acquisition_status": acquisition_status,
        "detail_rows": audit.get("detail_rows"),
        "unique_detail_rows": audit.get("unique_detail_rows"),
        "unique_files": audit.get("unique_files"),
        "route_counts": dict(route_counts),
        "source_strength_counts": dict(source_counts),
        "map_counts": dict(map_counts),
        "table_overlay_metadata": dict(overlay),
        "current_table_overlay_metadata": dict(current_overlay),
        "artifact_table_overlay_metadata": dict(artifact_overlay),
        "table_overlay_metadata_stale": bool(
            audit.get("table_overlay_metadata_stale")
        ),
        "table_overlay_metadata_delta": list(overlay_delta)[:top],
        "top_examples": list(audit.get("top_examples") or [])[:top],
    }


_LIVE_PRACTICAL_GUARD_KEYS = (
    "rows",
    "estimate_rows",
    "formal_mode_counts",
    "formal_mode_reason_counts",
    "v3_practical_formal_rows",
    "v3_practical_live_guard_rows",
    "v3_practical_live_guard_rate",
    "v3_practical_live_guard_reason_counts",
    "v3_practical_unguarded_rows",
    "v3_practical_unguarded_mae",
    "v3_practical_unguarded_under_rate",
    "v3_practical_unguarded_p90_coverage",
    "v3_practical_unguarded_p90_extreme_over_rate",
    "v3_practical_guard_comparison_rows",
    "v3_practical_guarded_mae_on_comparison",
    "v3_practical_unguarded_mae_on_comparison",
    "v3_practical_guarded_minus_unguarded_mae",
    "v3_practical_guarded_minus_unguarded_median_p50",
    "v3_practical_guarded_minus_unguarded_median_p90",
    "v3_practical_guarded_p90_coverage_on_comparison",
    "v3_practical_unguarded_p90_coverage_on_comparison",
    "v3_practical_guarded_minus_unguarded_p90_coverage",
    "v3_practical_guarded_p90_extreme_over_on_comparison",
    "v3_practical_unguarded_p90_extreme_over_on_comparison",
    "v3_practical_guarded_minus_unguarded_p90_extreme_over",
    "v3_practical_guard_case_summary",
    "formal_policy_comparison",
)
_LIVE_PRACTICAL_GUARD_CONTRACT_KEYS = (
    "rows",
    "formal_mode_counts",
    "formal_mode_reason_counts",
    "v3_practical_formal_rows",
    "v3_practical_live_guard_rows",
    "v3_practical_live_guard_reason_counts",
    "v3_practical_unguarded_rows",
    "v3_practical_guard_comparison_rows",
    "v3_practical_guard_case_summary",
    "formal_policy_comparison",
)
_LIVE_PRACTICAL_GUARD_TRADEOFF_KEYS = (
    "v3_practical_guarded_mae_on_comparison",
    "v3_practical_unguarded_mae_on_comparison",
    "v3_practical_guarded_minus_unguarded_mae",
    "v3_practical_guarded_minus_unguarded_median_p50",
    "v3_practical_guarded_minus_unguarded_median_p90",
    "v3_practical_guarded_p90_coverage_on_comparison",
    "v3_practical_unguarded_p90_coverage_on_comparison",
    "v3_practical_guarded_minus_unguarded_p90_coverage",
    "v3_practical_guarded_p90_extreme_over_on_comparison",
    "v3_practical_unguarded_p90_extreme_over_on_comparison",
    "v3_practical_guarded_minus_unguarded_p90_extreme_over",
)
_SCP_GUARDED_STABILITY_CONTRACT_KEYS = (
    "overall_status",
    "status_reasons",
    "posterior_trials",
    "posterior_seeds",
    "run_count",
    "watch_runs",
    "required_selected_groups",
    "stable_selected_groups",
    "union_selected_groups",
    "selected_signature_counts",
    "hurt_group_counts",
    "min_applied_rows",
    "min_applied_rows_required",
    "selected_group_support_summary",
    "selected_group_support_gap",
    "selected_group_guard_summary",
    "selected_group_instability_summary",
)
_CSE_ARTIFACT_CONTRACT_KEYS = (
    "affects_bid",
    "active",
    "generated_at",
    "source",
    "group_bys",
    "table_overlay_metadata",
    "cohorts",
    "entries",
)
_CSE_ENTRY_CONTRACT_KEYS = (
    "scope",
    "group",
    "status",
    "gate_reason",
    "source",
    "archive_sessions",
    "mechanism_classes",
    "source_evidence_classes",
    "source_context_classes",
    "unique_round_overflow_rows",
    "server_side_expansion_rows",
    "session_capacity_source_semantics_rows",
    "public_total_match_rows",
    "full_action_rows",
    "payload_verified_only_rows",
    "payload_inventory_mismatch_rows",
    "non_zodiac_missing_max",
)
_CSE_DIGEST_NUMERIC_KEYS = (
    "unique_round_overflow_rows",
    "session_capacity_source_semantics_rows",
    "server_side_expansion_rows",
    "payload_verified_only_rows",
    "public_total_match_rows",
    "full_action_rows",
    "direct_full_action_rows",
    "payload_inventory_mismatch_rows",
)
_SHADOW_SAMPLER_PROTOTYPE_CONTRACT_KEYS = (
    "interface",
    "shadow_only",
    "affects_bid",
    "active",
    "status",
    "posterior_seeds",
    "stable_watch_candidate_labels",
    "component_statuses",
    "min_watch_support_rows",
    "min_watch_support_sessions",
)
_SHADOW_SAMPLER_PROTOTYPE_BLOCKING_STATUSES = {
    "blocked_shadow_affects_bid",
    "blocked_shadow_active",
    "blocked_no_component_likelihood",
    "blocked_seed_instability",
    "blocked_low_support",
    "blocked_holdout_hurt",
    "watch_with_hurt_alternatives",
}
_SHADOW_SAMPLER_GUARD_TRIAL_CONTRACT_KEYS = (
    "interface",
    "shadow_only",
    "affects_bid",
    "active",
    "can_promote",
    "status",
    "sampler_status",
    "trial_options",
    "component_status_counts",
    "support_gate_status_counts",
    "guarded_sampler_result",
)
_SHADOW_SAMPLER_GUARD_TRIAL_BLOCKING_STATUSES = {
    "audit_probe_guarded_shadow_trial",
    "blocked_guarded_shadow_trial",
    "sample_limited_guarded_shadow_trial",
}
_SHADOW_SAMPLER_VALUE_SOURCE_PROFILE_CONTRACT_KEYS = (
    "interface",
    "shadow_only",
    "affects_bid",
    "active",
    "can_promote",
    "status",
    "component",
    "run_count",
    "runs",
    "migration",
    "next_action",
)
_SHADOW_SAMPLER_VALUE_SOURCE_PROFILE_BLOCKING_STATUSES = {
    "blocked_risk_migration",
    "requires_source_profile_parser",
    "blocked_q6_value_component",
}
_SHADOW_SAMPLER_VALUE_MAP_PROFILE_DETAILS_CONTRACT_KEYS = (
    "interface",
    "shadow_only",
    "affects_bid",
    "active",
    "can_promote",
    "status",
    "component",
    "source_audit_status",
    "source_profile_parser_status",
    "label_count",
    "candidate_rows",
    "labels",
    "next_action",
)
_SHADOW_SAMPLER_VALUE_MAP_PROFILE_DETAILS_BLOCKING_STATUSES = {
    "blocked_map_only_details_ready",
    "blocked_no_map_only_details",
}
_SHADOW_SAMPLER_VALUE_PROFILE_GUARDABILITY_CONTRACT_KEYS = (
    "interface",
    "shadow_only",
    "affects_bid",
    "active",
    "can_promote",
    "status",
    "component",
    "source_details_status",
    "detail_rows",
    "cluster_count",
    "candidate_cluster_count",
    "next_action",
)
_SHADOW_SAMPLER_VALUE_PROFILE_GUARDABILITY_BLOCKING_STATUSES = {
    "blocked_no_stable_profile_guard",
    "blocked_profile_guard_candidates_need_holdout",
}
_GUARD_LOSS_SOURCE_CONTEXT_CONTRACT_KEYS = (
    "status",
    "summary",
    "rows",
)
_ACTIVITY_DROP_UNIVERSE_OVERLAY_CONTRACT_KEYS = (
    "status",
    "shadow_only",
    "affects_bid",
    "hard_map_allowed",
    "summary",
    "rows",
)
_SOURCE_PARSER_REQUIREMENTS_CONTRACT_KEYS = (
    "status",
    "shadow_only",
    "affects_bid",
    "parser_required",
    "summary",
    "rows",
)


def _live_practical_guard_slice(
    stats: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(stats, Mapping):
        return {}
    return {key: stats.get(key) for key in _LIVE_PRACTICAL_GUARD_KEYS}


def _live_practical_guard_group_slices(
    groups: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(groups, Mapping):
        return {}
    return {
        str(label): _live_practical_guard_slice(stats)
        for label, stats in sorted(groups.items())
        if isinstance(stats, Mapping)
    }


def _live_practical_guard_contract_slice(
    name: str,
    stats: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(stats, Mapping):
        return {
            "status": "blocked",
            "reason": f"{name} guard metrics are missing",
            "missing_keys": list(_LIVE_PRACTICAL_GUARD_CONTRACT_KEYS),
            "null_tradeoff_keys": list(_LIVE_PRACTICAL_GUARD_TRADEOFF_KEYS),
            "formal_rows": 0,
            "comparison_rows": 0,
        }
    missing_keys = [
        key for key in _LIVE_PRACTICAL_GUARD_CONTRACT_KEYS if key not in stats
    ]
    formal_rows = int(stats.get("v3_practical_formal_rows") or 0)
    comparison_rows = int(stats.get("v3_practical_guard_comparison_rows") or 0)
    guard_case_summary = stats.get("v3_practical_guard_case_summary")
    if not isinstance(guard_case_summary, Mapping):
        guard_case_summary = {}
    guard_case_summary_rows = int(guard_case_summary.get("rows") or 0)
    policy_matrix = stats.get("formal_policy_comparison")
    if not isinstance(policy_matrix, Mapping):
        policy_matrix = {}
    policy_matrix_rows = int(policy_matrix.get("comparison_rows") or 0)
    policy_matrix_policies = policy_matrix.get("policies")
    if not isinstance(policy_matrix_policies, Mapping):
        policy_matrix_policies = {}
    required_policy_keys = {"v2", "v3_guarded", "v3_unguarded"}
    missing_policy_keys = sorted(required_policy_keys - set(policy_matrix_policies))
    null_tradeoff_keys = [
        key
        for key in _LIVE_PRACTICAL_GUARD_TRADEOFF_KEYS
        if comparison_rows > 0 and stats.get(key) is None
    ]
    if missing_keys:
        status = "blocked"
        reason = f"{name} is missing guard contract fields"
    elif formal_rows <= 0:
        status = "blocked"
        reason = f"{name} has no v3_practical formal rows"
    elif comparison_rows <= 0:
        status = "blocked"
        reason = f"{name} has no paired guarded/unguarded comparison rows"
    elif guard_case_summary_rows < comparison_rows:
        status = "blocked"
        reason = f"{name} guard case summary does not cover paired rows"
    elif policy_matrix_rows <= 0:
        status = "blocked"
        reason = f"{name} has no comparable v2/guarded/unguarded policy rows"
    elif missing_policy_keys:
        status = "blocked"
        reason = f"{name} formal policy comparison is missing policies"
    elif null_tradeoff_keys:
        status = "blocked"
        reason = f"{name} has paired rows but missing guard tradeoff metrics"
    else:
        status = "watch"
        reason = f"{name} guard contract is evaluable"
    return {
        "status": status,
        "reason": reason,
        "missing_keys": missing_keys,
        "null_tradeoff_keys": null_tradeoff_keys,
        "formal_rows": formal_rows,
        "comparison_rows": comparison_rows,
        "guard_case_summary_rows": guard_case_summary_rows,
        "guard_case_summary_p50_worsened_rows": guard_case_summary.get(
            "p50_worsened_rows"
        ),
        "guard_case_summary_p90_coverage_lost_rows": guard_case_summary.get(
            "p90_coverage_lost_rows"
        ),
        "guard_case_summary_p90_extreme_over_added_rows": (
            guard_case_summary.get("p90_extreme_over_added_rows")
        ),
        "formal_policy_comparison_status": policy_matrix.get("status"),
        "formal_policy_comparison_rows": policy_matrix_rows,
        "formal_policy_comparison_missing_policies": missing_policy_keys,
        "formal_mode_counts": stats.get("formal_mode_counts"),
        "formal_mode_reason_counts": stats.get("formal_mode_reason_counts"),
    }


def summarize_live_practical_guard_brief(
    brief: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if brief is None:
        return {
            "status": "not_supplied",
            "reason": "live/archive v3 practical brief JSON was not supplied",
            "overall": {},
            "prebid_overall": {},
            "contract_checks": {},
        }
    if not isinstance(brief, Mapping):
        return {
            "status": "blocked",
            "reason": "live/archive v3 practical brief JSON must be an object",
            "overall": {},
            "prebid_overall": {},
            "contract_checks": {},
        }
    overall = _live_practical_guard_slice(brief.get("overall"))
    prebid = _live_practical_guard_slice(brief.get("prebid_overall"))
    by_map_id = _live_practical_guard_group_slices(brief.get("by_map_id"))
    by_map_family = _live_practical_guard_group_slices(brief.get("by_map_family"))
    contract_checks = {
        "overall": _live_practical_guard_contract_slice(
            "overall",
            brief.get("overall"),
        ),
        "prebid_overall": _live_practical_guard_contract_slice(
            "prebid_overall",
            brief.get("prebid_overall"),
        ),
    }
    blocked_checks = [
        check for check in contract_checks.values() if check.get("status") == "blocked"
    ]
    if blocked_checks:
        status = "blocked"
        reason = "; ".join(str(check.get("reason")) for check in blocked_checks)
    else:
        status = "watch"
        reason = "paired v3 practical guard tradeoff is available for overall and prebid review"
    return {
        "status": status,
        "reason": reason,
        "total_rows": brief.get("total_rows"),
        "source_counts": brief.get("source_counts"),
        "overall": overall,
        "prebid_overall": prebid,
        "by_map_id": by_map_id,
        "by_map_family": by_map_family,
        "contract_checks": contract_checks,
    }


def summarize_guard_loss_source_context_contract(
    audit: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if audit is None:
        return {
            "status": "not_supplied",
            "reason": "guard loss source-context audit JSON was not supplied",
            "audit_status": None,
            "maps": None,
            "guard_loss_rows": None,
            "status_counts": {},
        }
    if not isinstance(audit, Mapping):
        return {
            "status": "blocked",
            "reason": "guard loss source-context audit JSON must be an object",
            "audit_status": None,
            "maps": None,
            "guard_loss_rows": None,
            "status_counts": {},
        }
    missing = [
        key for key in _GUARD_LOSS_SOURCE_CONTEXT_CONTRACT_KEYS if key not in audit
    ]
    summary = audit.get("summary")
    if not isinstance(summary, Mapping):
        summary = {}
    rows = audit.get("rows")
    row_count = len(rows) if isinstance(rows, list) else 0
    if missing:
        status = "blocked"
        reason = "guard loss source-context audit is missing contract fields"
    elif row_count <= 0:
        status = "blocked"
        reason = "guard loss source-context audit has no map rows"
    else:
        status = "watch"
        reason = "guard loss source-context audit is evaluable"
    return {
        "status": status,
        "reason": reason,
        "missing_keys": missing,
        "audit_status": audit.get("status"),
        "maps": summary.get("maps"),
        "guard_loss_rows": summary.get("guard_loss_rows"),
        "status_counts": dict(summary.get("status_counts") or {}),
        "cse_exact_overlap_maps": summary.get("cse_exact_overlap_maps"),
        "source_semantics_detail_maps": summary.get(
            "source_semantics_detail_maps"
        ),
        "capacity_table_detail_maps": summary.get("capacity_table_detail_maps"),
        "capacity_acquisition_example_maps": summary.get(
            "capacity_acquisition_example_maps"
        ),
    }


def summarize_activity_drop_universe_overlay_contract(
    audit: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if audit is None:
        return {
            "status": "not_supplied",
            "reason": "activity drop-universe overlay audit JSON was not supplied",
            "audit_status": None,
            "maps": None,
            "files": None,
            "guard_loss_overlap_maps": None,
            "candidate_item_universe_covered_maps": None,
            "hard_map_blocked_maps": None,
            "status_counts": {},
        }
    if not isinstance(audit, Mapping):
        return {
            "status": "blocked",
            "reason": "activity drop-universe overlay audit JSON must be an object",
            "audit_status": None,
            "maps": None,
            "files": None,
            "guard_loss_overlap_maps": None,
            "candidate_item_universe_covered_maps": None,
            "hard_map_blocked_maps": None,
            "status_counts": {},
        }
    missing = [
        key
        for key in _ACTIVITY_DROP_UNIVERSE_OVERLAY_CONTRACT_KEYS
        if key not in audit
    ]
    summary = audit.get("summary")
    if not isinstance(summary, Mapping):
        summary = {}
    rows = audit.get("rows")
    row_count = len(rows) if isinstance(rows, list) else 0
    status = "watch"
    reason = "activity drop-universe overlay audit is evaluable"
    if missing:
        status = "blocked"
        reason = "activity drop-universe overlay audit is missing contract fields"
    elif row_count <= 0:
        status = "blocked"
        reason = "activity drop-universe overlay audit has no map rows"
    elif audit.get("shadow_only") is not True or audit.get("affects_bid") is not False:
        status = "blocked"
        reason = "activity drop-universe overlay audit must be shadow-only and inactive"
    return {
        "status": status,
        "reason": reason,
        "missing_keys": missing,
        "audit_status": audit.get("status"),
        "shadow_only": audit.get("shadow_only"),
        "affects_bid": audit.get("affects_bid"),
        "hard_map_allowed": audit.get("hard_map_allowed"),
        "maps": summary.get("maps"),
        "files": summary.get("files"),
        "guard_loss_overlap_maps": summary.get("guard_loss_overlap_maps"),
        "candidate_item_universe_covered_maps": summary.get(
            "candidate_item_universe_covered_maps"
        ),
        "hard_map_blocked_maps": summary.get("hard_map_blocked_maps"),
        "status_counts": dict(summary.get("status_counts") or {}),
    }


def summarize_source_parser_requirements_contract(
    audit: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if audit is None:
        return {
            "status": "not_supplied",
            "reason": "source parser requirements audit JSON was not supplied",
            "audit_status": None,
            "maps": None,
            "blocked_maps": None,
            "guard_loss_overlap_maps": None,
            "session_capacity_maps": None,
            "drop_ref_residual_maps": None,
            "activity_extras_maps": None,
            "numeric_action_semantics_maps": None,
            "numeric_action_rows": None,
            "numeric_session_capacity_signal_rows": None,
            "numeric_non_session_expected_rows": None,
            "numeric_unknown_semantic_rows": None,
            "session_capacity_source_gap_maps": None,
            "session_capacity_source_gap_rows": None,
            "session_gap_exact_session_count_source_rows": None,
            "session_gap_bucket_only_blocked_rows": None,
            "session_gap_unresolved_session_capacity_rows": None,
            "payload_table_gap_maps": None,
            "payload_table_gap_rows": None,
            "payload_table_gap_blocked_rows": None,
            "payload_table_gap_payload_verified_rows": None,
            "payload_table_gap_no_full_event_payload_rows": None,
            "payload_outer_field_maps": None,
            "payload_outer_field_rows": None,
            "payload_outer_field_metadata_only_rows": None,
            "payload_outer_field_capacity_candidate_rows": None,
            "table_overlay_residual_maps": None,
            "table_overlay_residual_rows": None,
            "table_overlay_residual_blocked_rows": None,
            "table_overlay_residual_local_cap_gap_rows": None,
            "table_overlay_residual_current_table_match_rows": None,
            "table_overlay_residual_activity_direct_rows": None,
            "table_overlay_residual_server_transform_open_rows": None,
            "requirement_counts": {},
            "status_counts": {},
        }
    if not isinstance(audit, Mapping):
        return {
            "status": "blocked",
            "reason": "source parser requirements audit JSON must be an object",
            "audit_status": None,
            "maps": None,
            "blocked_maps": None,
            "guard_loss_overlap_maps": None,
            "session_capacity_maps": None,
            "drop_ref_residual_maps": None,
            "activity_extras_maps": None,
            "numeric_action_semantics_maps": None,
            "numeric_action_rows": None,
            "numeric_session_capacity_signal_rows": None,
            "numeric_non_session_expected_rows": None,
            "numeric_unknown_semantic_rows": None,
            "session_capacity_source_gap_maps": None,
            "session_capacity_source_gap_rows": None,
            "session_gap_exact_session_count_source_rows": None,
            "session_gap_bucket_only_blocked_rows": None,
            "session_gap_unresolved_session_capacity_rows": None,
            "payload_table_gap_maps": None,
            "payload_table_gap_rows": None,
            "payload_table_gap_blocked_rows": None,
            "payload_table_gap_payload_verified_rows": None,
            "payload_table_gap_no_full_event_payload_rows": None,
            "payload_outer_field_maps": None,
            "payload_outer_field_rows": None,
            "payload_outer_field_metadata_only_rows": None,
            "payload_outer_field_capacity_candidate_rows": None,
            "table_overlay_residual_maps": None,
            "table_overlay_residual_rows": None,
            "table_overlay_residual_blocked_rows": None,
            "table_overlay_residual_local_cap_gap_rows": None,
            "table_overlay_residual_current_table_match_rows": None,
            "table_overlay_residual_activity_direct_rows": None,
            "table_overlay_residual_server_transform_open_rows": None,
            "requirement_counts": {},
            "status_counts": {},
        }
    missing = [
        key for key in _SOURCE_PARSER_REQUIREMENTS_CONTRACT_KEYS if key not in audit
    ]
    summary = audit.get("summary")
    if not isinstance(summary, Mapping):
        summary = {}
    rows = audit.get("rows")
    row_count = len(rows) if isinstance(rows, list) else 0
    status = "watch"
    reason = "source parser requirements audit is evaluable"
    if missing:
        status = "blocked"
        reason = "source parser requirements audit is missing contract fields"
    elif row_count <= 0:
        status = "blocked"
        reason = "source parser requirements audit has no map rows"
    elif audit.get("shadow_only") is not True or audit.get("affects_bid") is not False:
        status = "blocked"
        reason = "source parser requirements audit must be shadow-only and inactive"
    return {
        "status": status,
        "reason": reason,
        "missing_keys": missing,
        "audit_status": audit.get("status"),
        "shadow_only": audit.get("shadow_only"),
        "affects_bid": audit.get("affects_bid"),
        "parser_required": audit.get("parser_required"),
        "maps": summary.get("maps"),
        "blocked_maps": summary.get("blocked_maps"),
        "guard_loss_overlap_maps": summary.get("guard_loss_overlap_maps"),
        "session_capacity_maps": summary.get("session_capacity_maps"),
        "drop_ref_residual_maps": summary.get("drop_ref_residual_maps"),
        "activity_extras_maps": summary.get("activity_extras_maps"),
        "numeric_action_semantics_maps": summary.get("numeric_action_semantics_maps"),
        "numeric_action_rows": summary.get("numeric_action_rows"),
        "numeric_session_capacity_signal_rows": summary.get(
            "numeric_session_capacity_signal_rows"
        ),
        "numeric_non_session_expected_rows": summary.get(
            "numeric_non_session_expected_rows"
        ),
        "numeric_unknown_semantic_rows": summary.get("numeric_unknown_semantic_rows"),
        "session_capacity_source_gap_maps": summary.get(
            "session_capacity_source_gap_maps"
        ),
        "session_capacity_source_gap_rows": summary.get(
            "session_capacity_source_gap_rows"
        ),
        "session_gap_exact_session_count_source_rows": summary.get(
            "session_gap_exact_session_count_source_rows"
        ),
        "session_gap_bucket_only_blocked_rows": summary.get(
            "session_gap_bucket_only_blocked_rows"
        ),
        "session_gap_unresolved_session_capacity_rows": summary.get(
            "session_gap_unresolved_session_capacity_rows"
        ),
        "payload_table_gap_maps": summary.get("payload_table_gap_maps"),
        "payload_table_gap_rows": summary.get("payload_table_gap_rows"),
        "payload_table_gap_blocked_rows": summary.get(
            "payload_table_gap_blocked_rows"
        ),
        "payload_table_gap_payload_verified_rows": summary.get(
            "payload_table_gap_payload_verified_rows"
        ),
        "payload_table_gap_no_full_event_payload_rows": summary.get(
            "payload_table_gap_no_full_event_payload_rows"
        ),
        "payload_outer_field_maps": summary.get("payload_outer_field_maps"),
        "payload_outer_field_rows": summary.get("payload_outer_field_rows"),
        "payload_outer_field_metadata_only_rows": summary.get(
            "payload_outer_field_metadata_only_rows"
        ),
        "payload_outer_field_capacity_candidate_rows": summary.get(
            "payload_outer_field_capacity_candidate_rows"
        ),
        "table_overlay_residual_maps": summary.get("table_overlay_residual_maps"),
        "table_overlay_residual_rows": summary.get("table_overlay_residual_rows"),
        "table_overlay_residual_blocked_rows": summary.get(
            "table_overlay_residual_blocked_rows"
        ),
        "table_overlay_residual_local_cap_gap_rows": summary.get(
            "table_overlay_residual_local_cap_gap_rows"
        ),
        "table_overlay_residual_current_table_match_rows": summary.get(
            "table_overlay_residual_current_table_match_rows"
        ),
        "table_overlay_residual_activity_direct_rows": summary.get(
            "table_overlay_residual_activity_direct_rows"
        ),
        "table_overlay_residual_server_transform_open_rows": summary.get(
            "table_overlay_residual_server_transform_open_rows"
        ),
        "requirement_counts": dict(summary.get("requirement_counts") or {}),
        "status_counts": dict(summary.get("status_counts") or {}),
    }


def summarize_scp_guarded_bridge_stability_contract(
    stability: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if stability is None:
        return {
            "status": "not_supplied",
            "reason": "guarded bridge stability JSON was not supplied",
            "missing_keys": list(_SCP_GUARDED_STABILITY_CONTRACT_KEYS),
            "overall_status": "not_evaluated",
            "posterior_trials": None,
            "posterior_seeds": [],
            "run_count": 0,
        }
    if not isinstance(stability, Mapping):
        return {
            "status": "blocked",
            "reason": "guarded bridge stability JSON must be an object",
            "missing_keys": list(_SCP_GUARDED_STABILITY_CONTRACT_KEYS),
            "overall_status": "unknown",
            "posterior_trials": None,
            "posterior_seeds": [],
            "run_count": 0,
        }
    missing_keys = [
        key for key in _SCP_GUARDED_STABILITY_CONTRACT_KEYS if key not in stability
    ]
    overall_status = str(stability.get("overall_status") or "unknown")
    def int_values(value: Any, *, min_value: int) -> list[int]:
        values = value if isinstance(value, list) else [value]
        out: list[int] = []
        for item in values:
            try:
                parsed = int(item)
            except (TypeError, ValueError):
                continue
            if parsed >= min_value:
                out.append(parsed)
        return out

    posterior_trials = int_values(stability.get("posterior_trials"), min_value=1)
    seeds_value = stability.get("posterior_seeds")
    posterior_seeds = (
        int_values(seeds_value, min_value=0) if isinstance(seeds_value, list) else []
    )
    try:
        run_count = int(stability.get("run_count") or 0)
    except (TypeError, ValueError):
        run_count = 0
    required_selected_groups = stability.get("required_selected_groups")
    stable_selected_groups = stability.get("stable_selected_groups")
    union_selected_groups = stability.get("union_selected_groups")
    status_reasons = stability.get("status_reasons")
    instability_summary = stability.get("selected_group_instability_summary")

    blockers: list[str] = []
    if missing_keys:
        blockers.append("missing stability contract fields")
    if overall_status == "unknown":
        blockers.append("overall_status is missing")
    if not posterior_trials:
        blockers.append("posterior_trials must be positive")
    if not posterior_seeds:
        blockers.append("posterior_seeds are missing")
    if run_count <= 0:
        blockers.append("run_count must be positive")
    if not isinstance(required_selected_groups, list):
        blockers.append("required_selected_groups must be a list")
    if not isinstance(stable_selected_groups, list):
        blockers.append("stable_selected_groups must be a list")
    if not isinstance(union_selected_groups, list):
        blockers.append("union_selected_groups must be a list")
    if not isinstance(status_reasons, list):
        blockers.append("status_reasons must be a list")
    if not isinstance(instability_summary, list):
        blockers.append("selected_group_instability_summary must be a list")
    if overall_status == "watch":
        required = set(required_selected_groups or [])
        stable = set(stable_selected_groups or [])
        if not required.issubset(stable):
            blockers.append("watch stability must cover all required selected groups")
    elif overall_status != "unknown" and not status_reasons:
        blockers.append("blocked stability must include status_reasons")
    if overall_status != "watch" and union_selected_groups and not instability_summary:
        blockers.append("blocked stability must include group instability summary")

    return {
        "status": "blocked" if blockers else "watch",
        "reason": "; ".join(blockers) if blockers else "guarded bridge stability contract is evaluable",
        "missing_keys": missing_keys,
        "overall_status": overall_status,
        "posterior_trials": posterior_trials,
        "posterior_seeds": posterior_seeds,
        "run_count": run_count,
        "required_selected_groups": required_selected_groups
        if isinstance(required_selected_groups, list)
        else [],
        "stable_selected_groups": stable_selected_groups
        if isinstance(stable_selected_groups, list)
        else [],
        "union_selected_groups": union_selected_groups
        if isinstance(union_selected_groups, list)
        else [],
    }


def summarize_capacity_source_expansion_artifact_contract(
    artifact: Mapping[str, Any] | None,
) -> dict[str, Any]:
    empty_digest = {
        "digest_scope": None,
        "mechanism_classes": {},
        "source_evidence_classes": {},
        "source_context_classes": {},
        "source_split_status": "missing",
        **{key: 0 for key in _CSE_DIGEST_NUMERIC_KEYS},
    }
    if artifact is None:
        return {
            "status": "not_supplied",
            "reason": "capacity/source expansion artifact was not supplied",
            "missing_keys": list(_CSE_ARTIFACT_CONTRACT_KEYS),
            "entry_missing_key_counts": {},
            "entries": 0,
            "group_bys": [],
            "cohorts": 0,
            "candidate_entries": 0,
            "active": None,
            "affects_bid": None,
            **empty_digest,
        }
    if not isinstance(artifact, Mapping):
        return {
            "status": "blocked",
            "reason": "capacity/source expansion artifact must be an object",
            "missing_keys": list(_CSE_ARTIFACT_CONTRACT_KEYS),
            "entry_missing_key_counts": {},
            "entries": 0,
            "group_bys": [],
            "cohorts": 0,
            "candidate_entries": 0,
            "active": None,
            "affects_bid": None,
            **empty_digest,
        }
    missing_keys = [
        key for key in _CSE_ARTIFACT_CONTRACT_KEYS if key not in artifact
    ]
    entries_value = artifact.get("entries")
    entries = entries_value if isinstance(entries_value, list) else []
    group_bys_value = artifact.get("group_bys")
    group_bys = [str(value) for value in group_bys_value] if isinstance(group_bys_value, list) else []
    cohorts_value = artifact.get("cohorts")
    cohorts = cohorts_value if isinstance(cohorts_value, list) else []
    entry_missing_key_counts: Counter[str] = Counter()
    candidate_entries = 0
    blocked_entries = 0
    rows_by_scope: dict[str, list[Mapping[str, Any]]] = {}
    for row in entries:
        if not isinstance(row, Mapping):
            entry_missing_key_counts["non_object_entry"] += 1
            continue
        scope = str(row.get("scope") or "")
        if scope:
            rows_by_scope.setdefault(scope, []).append(row)
        for key in _CSE_ENTRY_CONTRACT_KEYS:
            if key not in row:
                entry_missing_key_counts[key] += 1
        status = str(row.get("status") or "")
        if status == "watch_capacity_source_expansion_shadow_only":
            candidate_entries += 1
        if status.startswith("blocked_"):
            blocked_entries += 1
    blockers: list[str] = []
    if missing_keys:
        blockers.append("missing CSE artifact contract fields")
    if artifact.get("affects_bid") is not False:
        blockers.append("artifact affects_bid must be false")
    if artifact.get("active") is not False:
        blockers.append("artifact active must be false")
    if not entries:
        blockers.append("artifact has no entries")
    if not group_bys:
        blockers.append("artifact group_bys are missing")
    if not cohorts:
        blockers.append("artifact cohorts are missing")
    if entry_missing_key_counts:
        blockers.append("artifact entries are missing CSE evidence fields")
    digest_scope = next(
        (scope for scope in group_bys if rows_by_scope.get(scope)),
        None,
    )
    digest_rows = rows_by_scope.get(digest_scope or "", []) if digest_scope else []
    if not digest_rows:
        digest_rows = [row for row in entries if isinstance(row, Mapping)]
        digest_scope = "all_entries" if digest_rows else None
    mechanism_classes: Counter[str] = Counter()
    source_evidence_classes: Counter[str] = Counter()
    source_context_classes: Counter[str] = Counter()
    numeric_totals = {key: 0 for key in _CSE_DIGEST_NUMERIC_KEYS}
    for row in digest_rows:
        mechanism_classes.update(_counter_from_count_text(row.get("mechanism_classes")))
        source_evidence_classes.update(
            _counter_from_count_text(row.get("source_evidence_classes"))
        )
        source_context_classes.update(
            _counter_from_count_text(row.get("source_context_classes"))
        )
        for key in _CSE_DIGEST_NUMERIC_KEYS:
            try:
                numeric_totals[key] += int(row.get(key) or 0)
            except (TypeError, ValueError):
                continue
    if numeric_totals["session_capacity_source_semantics_rows"] > 0:
        source_split_status = "blocked_payload_only_source_split_unresolved"
    elif numeric_totals["unique_round_overflow_rows"] > 0:
        source_split_status = "watch_external_or_server_side_expansion_only"
    else:
        source_split_status = "watch_no_unique_round_overflow"
    return {
        "status": "blocked" if blockers else "watch",
        "reason": "; ".join(blockers) if blockers else "capacity/source expansion artifact contract is evaluable",
        "missing_keys": missing_keys,
        "entry_missing_key_counts": dict(sorted(entry_missing_key_counts.items())),
        "entries": len(entries),
        "group_bys": group_bys,
        "cohorts": len(cohorts),
        "candidate_entries": candidate_entries,
        "blocked_entries": blocked_entries,
        "active": artifact.get("active"),
        "affects_bid": artifact.get("affects_bid"),
        "generated_at": artifact.get("generated_at"),
        "source": artifact.get("source"),
        "digest_scope": digest_scope,
        "mechanism_classes": dict(sorted(mechanism_classes.items())),
        "source_evidence_classes": dict(sorted(source_evidence_classes.items())),
        "source_context_classes": dict(sorted(source_context_classes.items())),
        "source_split_status": source_split_status,
        **numeric_totals,
    }


def _shadow_sampler_component_statuses(
    prototype: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    value = prototype.get("component_statuses")
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _shadow_sampler_support_gate_status(row: Mapping[str, Any]) -> str:
    gate = row.get("support_gate")
    if not isinstance(gate, Mapping):
        return "missing"
    return str(gate.get("status") or "unknown")


def _shadow_sampler_low_support_metrics(
    component_statuses: Iterable[Mapping[str, Any]],
    *,
    stable_only: bool,
    limit: int = 8,
) -> list[dict[str, Any]]:
    key = (
        "stable_low_support_watch_metrics"
        if stable_only
        else "low_support_watch_metrics"
    )
    out: list[dict[str, Any]] = []
    for row in component_statuses:
        gate = row.get("support_gate")
        if not isinstance(gate, Mapping):
            continue
        for item in gate.get(key) or ():
            if isinstance(item, Mapping):
                out.append(dict(item))
    out.sort(
        key=lambda item: (
            str(item.get("watch_label") or ""),
            str(item.get("posterior_seed") or ""),
        )
    )
    return out[:limit]


def summarize_shadow_sampler_prototype_contract(
    prototype: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if prototype is None:
        return {
            "status": "not_supplied",
            "reason": "shadow sampler prototype JSON was not supplied",
            "missing_keys": [],
            "required_keys": list(_SHADOW_SAMPLER_PROTOTYPE_CONTRACT_KEYS),
        }
    if not isinstance(prototype, Mapping):
        return {
            "status": "blocked",
            "reason": "shadow sampler prototype JSON must be an object",
            "missing_keys": list(_SHADOW_SAMPLER_PROTOTYPE_CONTRACT_KEYS),
            "required_keys": list(_SHADOW_SAMPLER_PROTOTYPE_CONTRACT_KEYS),
        }
    missing = [
        key for key in _SHADOW_SAMPLER_PROTOTYPE_CONTRACT_KEYS if key not in prototype
    ]
    component_statuses = _shadow_sampler_component_statuses(prototype)
    component_status_counts = dict(
        sorted(
            Counter(
                str(row.get("status") or "unknown")
                for row in component_statuses
            ).items()
        )
    )
    support_gate_status_counts = dict(
        sorted(
            Counter(
                _shadow_sampler_support_gate_status(row)
                for row in component_statuses
            ).items()
        )
    )
    blocking_components = [
        {
            "component": row.get("component"),
            "status": row.get("status"),
            "support_gate": _shadow_sampler_support_gate_status(row),
        }
        for row in component_statuses
        if str(row.get("status") or "")
        in _SHADOW_SAMPLER_PROTOTYPE_BLOCKING_STATUSES
    ]
    shadow_safe = (
        prototype.get("shadow_only") is True
        and prototype.get("affects_bid") is False
        and prototype.get("active") is False
    )
    prototype_status = str(prototype.get("status") or "unknown")
    guard_trial_contract = prototype.get("guard_trial_contract")
    if not isinstance(guard_trial_contract, Mapping):
        guard_trial_contract = {}
    if missing:
        status = "blocked"
        reason = "shadow sampler prototype is missing contract fields"
    elif not shadow_safe:
        status = "blocked"
        reason = "shadow sampler prototype is not shadow-safe"
    elif (
        prototype_status in _SHADOW_SAMPLER_PROTOTYPE_BLOCKING_STATUSES
        or blocking_components
    ):
        status = "blocked"
        reason = "shadow sampler prototype still has seed/support/holdout blockers"
    else:
        status = "watch"
        reason = "shadow sampler prototype contract is attached and shadow-only"
    return {
        "status": status,
        "reason": reason,
        "missing_keys": missing,
        "required_keys": list(_SHADOW_SAMPLER_PROTOTYPE_CONTRACT_KEYS),
        "interface": prototype.get("interface"),
        "prototype_status": prototype_status,
        "shadow_safe": shadow_safe,
        "posterior_seeds": prototype.get("posterior_seeds") or [],
        "stable_watch_candidate_labels": prototype.get(
            "stable_watch_candidate_labels"
        )
        or [],
        "min_watch_support_rows": prototype.get("min_watch_support_rows"),
        "min_watch_support_sessions": prototype.get("min_watch_support_sessions"),
        "component_status_counts": component_status_counts,
        "support_gate_status_counts": support_gate_status_counts,
        "blocking_component_statuses": blocking_components,
        "guard_trial_status": guard_trial_contract.get("status"),
        "guard_trial_action_counts": dict(
            guard_trial_contract.get("action_counts") or {}
        ),
        "guard_trial_contract": dict(guard_trial_contract),
        "low_support_watch_metrics": _shadow_sampler_low_support_metrics(
            component_statuses,
            stable_only=False,
        ),
        "stable_low_support_watch_metrics": _shadow_sampler_low_support_metrics(
            component_statuses,
            stable_only=True,
        ),
    }


def _shadow_sampler_guard_trial_component_statuses(
    trial: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    sampler = trial.get("guarded_sampler_result")
    if not isinstance(sampler, Mapping):
        return []
    value = sampler.get("component_statuses")
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def summarize_shadow_sampler_guard_trial_contract(
    trial: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if trial is None:
        return {
            "status": "not_supplied",
            "reason": "shadow sampler guarded trial JSON was not supplied",
            "missing_keys": [],
            "required_keys": list(_SHADOW_SAMPLER_GUARD_TRIAL_CONTRACT_KEYS),
        }
    if not isinstance(trial, Mapping):
        return {
            "status": "blocked",
            "reason": "shadow sampler guarded trial JSON must be an object",
            "missing_keys": list(_SHADOW_SAMPLER_GUARD_TRIAL_CONTRACT_KEYS),
            "required_keys": list(_SHADOW_SAMPLER_GUARD_TRIAL_CONTRACT_KEYS),
        }
    missing = [
        key for key in _SHADOW_SAMPLER_GUARD_TRIAL_CONTRACT_KEYS if key not in trial
    ]
    component_statuses = _shadow_sampler_guard_trial_component_statuses(trial)
    blocking_components = [
        {
            "component": row.get("component"),
            "status": row.get("status"),
            "support_gate": _shadow_sampler_support_gate_status(row),
        }
        for row in component_statuses
        if str(row.get("status") or "")
        in _SHADOW_SAMPLER_PROTOTYPE_BLOCKING_STATUSES
    ]
    shadow_safe = (
        trial.get("shadow_only") is True
        and trial.get("affects_bid") is False
        and trial.get("active") is False
        and trial.get("can_promote") is False
    )
    trial_status = str(trial.get("status") or "unknown")
    if missing:
        status = "blocked"
        reason = "shadow sampler guarded trial is missing contract fields"
    elif not shadow_safe:
        status = "blocked"
        reason = "shadow sampler guarded trial is not shadow-safe"
    elif trial_status in _SHADOW_SAMPLER_GUARD_TRIAL_BLOCKING_STATUSES:
        status = "blocked"
        reason = "shadow sampler guarded trial still has holdout blockers"
    elif trial_status == "watch_guarded_shadow_trial":
        status = "watch"
        reason = "shadow sampler guarded trial is attached and shadow-only"
    else:
        status = "blocked"
        reason = "shadow sampler guarded trial has unknown status"
    trial_options = trial.get("trial_options")
    if not isinstance(trial_options, Mapping):
        trial_options = {}
    return {
        "status": status,
        "reason": reason,
        "missing_keys": missing,
        "required_keys": list(_SHADOW_SAMPLER_GUARD_TRIAL_CONTRACT_KEYS),
        "interface": trial.get("interface"),
        "trial_status": trial_status,
        "sampler_status": trial.get("sampler_status"),
        "shadow_safe": shadow_safe,
        "source_prototype_status": trial.get("source_prototype_status"),
        "source_guard_trial_status": trial.get("source_guard_trial_status"),
        "trial_options": dict(trial_options),
        "component_status_counts": dict(
            trial.get("component_status_counts") or {}
        ),
        "support_gate_status_counts": dict(
            trial.get("support_gate_status_counts") or {}
        ),
        "blocking_component_statuses": blocking_components,
        "required_verification": list(trial.get("required_verification") or []),
    }


def _shadow_sampler_value_source_profile_run_summaries(
    audit: Mapping[str, Any],
) -> list[dict[str, Any]]:
    runs = audit.get("runs")
    if not isinstance(runs, list):
        return []
    out: list[dict[str, Any]] = []
    for row in runs:
        if not isinstance(row, Mapping):
            continue
        out.append(
            {
                "label": row.get("label"),
                "audit_probe": row.get("audit_probe"),
                "component_status": row.get("component_status"),
                "sampler_status": row.get("sampler_status"),
                "support_gate": row.get("support_gate"),
                "source_profile_parser_required": row.get(
                    "source_profile_parser_required"
                ),
                "hurt_label_count": row.get("hurt_label_count"),
                "hurt_map_ids": list(row.get("hurt_map_ids") or []),
                "hurt_evidence_profiles": list(
                    row.get("hurt_evidence_profiles") or []
                ),
                "hurt_group_field_counts": dict(
                    row.get("hurt_group_field_counts") or {}
                ),
            }
        )
    return out


def summarize_shadow_sampler_value_source_profile_contract(
    audit: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if audit is None:
        return {
            "status": "not_supplied",
            "reason": "q6_value source/profile audit JSON was not supplied",
            "missing_keys": [],
            "required_keys": list(
                _SHADOW_SAMPLER_VALUE_SOURCE_PROFILE_CONTRACT_KEYS
            ),
        }
    if not isinstance(audit, Mapping):
        return {
            "status": "blocked",
            "reason": "q6_value source/profile audit JSON must be an object",
            "missing_keys": list(
                _SHADOW_SAMPLER_VALUE_SOURCE_PROFILE_CONTRACT_KEYS
            ),
            "required_keys": list(
                _SHADOW_SAMPLER_VALUE_SOURCE_PROFILE_CONTRACT_KEYS
            ),
        }
    missing = [
        key
        for key in _SHADOW_SAMPLER_VALUE_SOURCE_PROFILE_CONTRACT_KEYS
        if key not in audit
    ]
    shadow_safe = (
        audit.get("shadow_only") is True
        and audit.get("affects_bid") is False
        and audit.get("active") is False
        and audit.get("can_promote") is False
    )
    audit_status = str(audit.get("status") or "unknown")
    migration = audit.get("migration")
    if not isinstance(migration, Mapping):
        migration = {}
    parser = audit.get("source_profile_parser")
    if not isinstance(parser, Mapping):
        parser = {}
    risk_migration = bool(migration.get("risk_migration_detected"))
    component = str(audit.get("component") or "")
    if missing:
        status = "blocked"
        reason = "q6_value source/profile audit is missing contract fields"
    elif not shadow_safe:
        status = "blocked"
        reason = "q6_value source/profile audit is not shadow-safe"
    elif component != "q6_value":
        status = "blocked"
        reason = "q6_value source/profile audit component mismatch"
    elif audit_status in _SHADOW_SAMPLER_VALUE_SOURCE_PROFILE_BLOCKING_STATUSES:
        status = "blocked"
        reason = "q6_value source/profile audit still blocks sampler promotion"
    elif audit_status == "watch_diagnostic_only":
        status = "watch"
        reason = "q6_value source/profile audit is attached as shadow diagnostic"
    else:
        status = "blocked"
        reason = "q6_value source/profile audit has unknown status"
    return {
        "status": status,
        "reason": reason,
        "missing_keys": missing,
        "required_keys": list(
            _SHADOW_SAMPLER_VALUE_SOURCE_PROFILE_CONTRACT_KEYS
        ),
        "interface": audit.get("interface"),
        "audit_status": audit_status,
        "shadow_safe": shadow_safe,
        "component": component,
        "run_count": audit.get("run_count"),
        "risk_migration_detected": risk_migration,
        "source_profile_parser_status": parser.get("status"),
        "profile_semantic_migration_detected": parser.get(
            "profile_semantic_migration_detected"
        ),
        "latest_map_only_hurt_label_count": parser.get(
            "latest_map_only_hurt_label_count"
        ),
        "latest_profile_hurt_label_count": parser.get(
            "latest_profile_hurt_label_count"
        ),
        "introduced_hurt_labels": list(
            migration.get("introduced_hurt_labels") or []
        ),
        "removed_hurt_labels": list(migration.get("removed_hurt_labels") or []),
        "run_summaries": _shadow_sampler_value_source_profile_run_summaries(
            audit
        ),
        "next_action": audit.get("next_action"),
        "blocked_actions": list(audit.get("blocked_actions") or []),
    }


def summarize_shadow_sampler_value_map_profile_details_contract(
    details: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if details is None:
        return {
            "status": "not_supplied",
            "reason": "q6_value map/profile details JSON was not supplied",
            "missing_keys": [],
            "required_keys": list(
                _SHADOW_SAMPLER_VALUE_MAP_PROFILE_DETAILS_CONTRACT_KEYS
            ),
        }
    if not isinstance(details, Mapping):
        return {
            "status": "blocked",
            "reason": "q6_value map/profile details JSON must be an object",
            "missing_keys": list(
                _SHADOW_SAMPLER_VALUE_MAP_PROFILE_DETAILS_CONTRACT_KEYS
            ),
            "required_keys": list(
                _SHADOW_SAMPLER_VALUE_MAP_PROFILE_DETAILS_CONTRACT_KEYS
            ),
        }
    missing = [
        key
        for key in _SHADOW_SAMPLER_VALUE_MAP_PROFILE_DETAILS_CONTRACT_KEYS
        if key not in details
    ]
    shadow_safe = (
        details.get("shadow_only") is True
        and details.get("affects_bid") is False
        and details.get("active") is False
        and details.get("can_promote") is False
    )
    details_status = str(details.get("status") or "unknown")
    component = str(details.get("component") or "")
    if missing:
        status = "blocked"
        reason = "q6_value map/profile details are missing contract fields"
    elif not shadow_safe:
        status = "blocked"
        reason = "q6_value map/profile details are not shadow-safe"
    elif component != "q6_value":
        status = "blocked"
        reason = "q6_value map/profile details component mismatch"
    elif details_status in _SHADOW_SAMPLER_VALUE_MAP_PROFILE_DETAILS_BLOCKING_STATUSES:
        status = "blocked"
        reason = "q6_value map/profile details still block value guard design"
    elif details_status == "watch_diagnostic_only":
        status = "watch"
        reason = "q6_value map/profile details are attached as shadow diagnostic"
    else:
        status = "blocked"
        reason = "q6_value map/profile details have unknown status"
    return {
        "status": status,
        "reason": reason,
        "missing_keys": missing,
        "required_keys": list(
            _SHADOW_SAMPLER_VALUE_MAP_PROFILE_DETAILS_CONTRACT_KEYS
        ),
        "interface": details.get("interface"),
        "details_status": details_status,
        "shadow_safe": shadow_safe,
        "component": component,
        "source_audit_status": details.get("source_audit_status"),
        "source_profile_parser_status": details.get(
            "source_profile_parser_status"
        ),
        "label_count": details.get("label_count"),
        "candidate_rows": details.get("candidate_rows"),
        "candidate_sessions_sum": details.get("candidate_sessions_sum"),
        "labels_with_row_count_mismatch": list(
            details.get("labels_with_row_count_mismatch") or []
        ),
        "next_action": details.get("next_action"),
        "blocked_actions": list(details.get("blocked_actions") or []),
    }


def summarize_shadow_sampler_value_profile_guardability_contract(
    audit: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if audit is None:
        return {
            "status": "not_supplied",
            "reason": "q6_value profile guardability JSON was not supplied",
            "missing_keys": [],
            "required_keys": list(
                _SHADOW_SAMPLER_VALUE_PROFILE_GUARDABILITY_CONTRACT_KEYS
            ),
        }
    if not isinstance(audit, Mapping):
        return {
            "status": "blocked",
            "reason": "q6_value profile guardability JSON must be an object",
            "missing_keys": list(
                _SHADOW_SAMPLER_VALUE_PROFILE_GUARDABILITY_CONTRACT_KEYS
            ),
            "required_keys": list(
                _SHADOW_SAMPLER_VALUE_PROFILE_GUARDABILITY_CONTRACT_KEYS
            ),
        }
    missing = [
        key
        for key in _SHADOW_SAMPLER_VALUE_PROFILE_GUARDABILITY_CONTRACT_KEYS
        if key not in audit
    ]
    shadow_safe = (
        audit.get("shadow_only") is True
        and audit.get("affects_bid") is False
        and audit.get("active") is False
        and audit.get("can_promote") is False
    )
    guardability_status = str(audit.get("status") or "unknown")
    component = str(audit.get("component") or "")
    if missing:
        status = "blocked"
        reason = "q6_value profile guardability is missing contract fields"
    elif not shadow_safe:
        status = "blocked"
        reason = "q6_value profile guardability is not shadow-safe"
    elif component != "q6_value":
        status = "blocked"
        reason = "q6_value profile guardability component mismatch"
    elif guardability_status in _SHADOW_SAMPLER_VALUE_PROFILE_GUARDABILITY_BLOCKING_STATUSES:
        status = "blocked"
        reason = "q6_value profile guardability still blocks value guard design"
    elif guardability_status == "watch_diagnostic_only":
        status = "watch"
        reason = "q6_value profile guardability is attached as shadow diagnostic"
    else:
        status = "blocked"
        reason = "q6_value profile guardability has unknown status"
    return {
        "status": status,
        "reason": reason,
        "missing_keys": missing,
        "required_keys": list(
            _SHADOW_SAMPLER_VALUE_PROFILE_GUARDABILITY_CONTRACT_KEYS
        ),
        "interface": audit.get("interface"),
        "guardability_status": guardability_status,
        "shadow_safe": shadow_safe,
        "component": component,
        "source_details_status": audit.get("source_details_status"),
        "detail_rows": audit.get("detail_rows"),
        "cluster_count": audit.get("cluster_count"),
        "candidate_cluster_count": audit.get("candidate_cluster_count"),
        "overfit_risk_cluster_count": audit.get("overfit_risk_cluster_count"),
        "mixed_cluster_count": audit.get("mixed_cluster_count"),
        "next_action": audit.get("next_action"),
        "blocked_actions": list(audit.get("blocked_actions") or []),
    }


def summarize_readiness(
    rows: list[dict[str, Any]],
    errors: list[dict[str, str]],
    *,
    group_field: str = "hero_map_id",
    profile_field: str = "hero_map_evidence_profile",
    min_windows: int = 20,
    min_sessions: int = 8,
    folds: int = 5,
    scp_guarded_bridge_stability: Mapping[str, Any] | None = None,
    capacity_table_audit: Mapping[str, Any] | None = None,
    capacity_table_acquisition_audit: Mapping[str, Any] | None = None,
    capacity_source_expansion_artifact: Mapping[str, Any] | None = None,
    live_practical_guard_brief: Mapping[str, Any] | None = None,
    guard_loss_source_context: Mapping[str, Any] | None = None,
    activity_drop_universe_overlay: Mapping[str, Any] | None = None,
    source_parser_requirements: Mapping[str, Any] | None = None,
    shadow_sampler_prototype: Mapping[str, Any] | None = None,
    shadow_sampler_guard_trial: Mapping[str, Any] | None = None,
    shadow_sampler_value_source_profile_audit: Mapping[str, Any] | None = None,
    shadow_sampler_value_map_profile_details: Mapping[str, Any] | None = None,
    shadow_sampler_value_profile_guardability: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    summary = summarize_rows(rows, errors)
    live_guard_brief = summarize_live_practical_guard_brief(
        live_practical_guard_brief
    )
    guard_loss_source_context_contract = (
        summarize_guard_loss_source_context_contract(guard_loss_source_context)
    )
    activity_drop_universe_overlay_contract = (
        summarize_activity_drop_universe_overlay_contract(
            activity_drop_universe_overlay
        )
    )
    source_parser_requirements_contract = (
        summarize_source_parser_requirements_contract(source_parser_requirements)
    )
    cse_artifact_contract = summarize_capacity_source_expansion_artifact_contract(
        capacity_source_expansion_artifact
    )
    capacity_table_audit_contract = summarize_capacity_table_audit_contract(
        capacity_table_audit
    )
    capacity_table_acquisition_contract = (
        summarize_capacity_table_acquisition_contract(
            capacity_table_acquisition_audit
        )
    )
    shadow_sampler_prototype_contract = summarize_shadow_sampler_prototype_contract(
        shadow_sampler_prototype
    )
    shadow_sampler_guard_trial_contract = (
        summarize_shadow_sampler_guard_trial_contract(shadow_sampler_guard_trial)
    )
    shadow_sampler_value_source_profile_contract = (
        summarize_shadow_sampler_value_source_profile_contract(
            shadow_sampler_value_source_profile_audit
        )
    )
    shadow_sampler_value_map_profile_details_contract = (
        summarize_shadow_sampler_value_map_profile_details_contract(
            shadow_sampler_value_map_profile_details
        )
    )
    shadow_sampler_value_profile_guardability_contract = (
        summarize_shadow_sampler_value_profile_guardability_contract(
            shadow_sampler_value_profile_guardability
        )
    )
    prior_stress_details = summarize_prior_stress_details(rows)
    prior_stress_detail_summary = summarize_prior_stress_detail_summary(
        prior_stress_details,
        top=8,
        group_fields=("map_id", "hero_map_evidence_profile"),
    )
    under = summarize_under_holdout(
        rows,
        group_field=group_field,
        folds=folds,
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    under_profile = summarize_under_holdout(
        rows,
        group_field=profile_field,
        folds=folds,
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    ccv = summarize_ccv_candidates(
        rows,
        group_field=group_field,
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    ccv_profile = summarize_ccv_candidates(
        rows,
        group_field=profile_field,
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    ccv_holdout = summarize_ccv_holdout(
        rows,
        group_field=group_field,
        folds=folds,
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    ccv_profile_holdout = summarize_ccv_holdout(
        rows,
        group_field=profile_field,
        folds=folds,
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    ccv_map_holdout = summarize_ccv_holdout(
        rows,
        group_field="map_id",
        folds=folds,
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    ccv_map_direction = summarize_ccv_direction(
        rows,
        group_field="map_id",
        components=("q6_count", "q6_cells"),
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    ccv_profile_direction = summarize_ccv_direction(
        rows,
        group_field="evidence_profile_key",
        components=("q6_count", "q6_cells"),
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    ccv_map_direction_holdout = summarize_ccv_direction_holdout(
        rows,
        group_field="map_id",
        components=("q6_count", "q6_cells"),
        folds=folds,
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    ccv_profile_direction_holdout = summarize_ccv_direction_holdout(
        rows,
        group_field="evidence_profile_key",
        components=("q6_count", "q6_cells"),
        folds=folds,
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    residual = summarize_residual_candidates(
        rows,
        group_field=group_field,
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    tail = summarize_tail_candidates(
        rows,
        group_field=group_field,
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    tail_holdout = summarize_tail_holdout(
        rows,
        group_field=group_field,
        folds=folds,
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    tail_profile_holdout = summarize_tail_holdout(
        rows,
        group_field=profile_field,
        folds=folds,
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    tail_profile = summarize_tail_candidates(
        rows,
        group_field=profile_field,
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    tail_under_holdout = summarize_tail_under_holdout(
        rows,
        group_field=group_field,
        folds=folds,
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    formal_value_sampler_holdout = summarize_formal_value_sampler_holdout(
        rows,
        group_field=group_field,
        folds=folds,
        min_windows=min_windows,
        min_sessions=min_sessions,
    )
    scp_formal_value_link = summarize_scp_formal_value_link(
        rows,
        group_field="v3_scp_group",
    )
    scp_count_value_bridge = summarize_scp_count_value_bridge(
        rows,
        group_field="v3_scp_group",
    )
    scp_count_value_bridge_holdout = summarize_scp_count_value_bridge_holdout(
        rows,
        group_field="v3_scp_group",
        folds=folds,
        min_train_sessions=min_sessions,
    )
    scp_guarded_bridge_holdout = summarize_scp_guarded_bridge_holdout(
        rows,
        group_field="v3_scp_group",
        folds=folds,
        inner_folds=max(2, folds - 1),
        min_train_sessions=min_sessions,
        min_guard_sessions=min_sessions,
        min_guard_fold_sessions=max(1, min_sessions // 4),
        formal_lift_cap=10_000.0,
        max_guard_over_increase=0.0,
    )

    gates: list[dict[str, Any]] = []
    data_status = "pass" if not errors and not summary.get("constraint_conflict") else "blocked"
    if data_status == "pass" and int(summary.get("no_state") or 0) > 0:
        data_status = "watch"
    gates.append(
        _gate(
            "archive_data_quality",
            data_status,
            "parse/constraint clean; no_state rows are separated from metric rows"
            if data_status != "blocked"
            else "parse errors or constraint conflicts remain",
            parse_errors=summary.get("parse_errors"),
            constraint_conflict=summary.get("constraint_conflict"),
            no_state=summary.get("no_state"),
            ready=summary.get("ready"),
        )
    )
    pipeline_ready = (
        int(summary.get("ready") or 0) > 0
        and int(summary.get("posterior_ready") or 0) == int(summary.get("ready") or 0)
        and int(summary.get("posterior_no_match") or 0) == 0
    )
    gates.append(
        _gate(
            "shared_shadow_pipeline",
            "pass" if pipeline_ready else "blocked",
            "archive/live v3 shadow pipeline produces ready posterior rows"
            if pipeline_ready
            else "posterior readiness does not cover all ready rows",
            posterior_ready=summary.get("posterior_ready"),
            ready=summary.get("ready"),
            posterior_no_match=summary.get("posterior_no_match"),
        )
    )
    live_guard_status = str(live_guard_brief.get("status") or "not_supplied")
    gates.append(
        _gate(
            "v3_practical_archive_live_guard_metrics",
            "pending" if live_guard_status == "not_supplied" else live_guard_status,
            str(live_guard_brief.get("reason") or ""),
            total_rows=live_guard_brief.get("total_rows"),
            source_counts=live_guard_brief.get("source_counts"),
            overall=live_guard_brief.get("overall"),
            prebid_overall=live_guard_brief.get("prebid_overall"),
            contract_checks=live_guard_brief.get("contract_checks"),
        )
    )
    ready_count = int(summary.get("ready") or 0)
    robust_prior_usable = int(summary.get("robust_prior_usable") or 0)
    robust_prior_trusted = int(summary.get("robust_prior_trusted") or 0)
    robust_activity_candidate = int(summary.get("robust_activity_candidate") or 0)
    robust_prior_stressed = int(summary.get("robust_prior_stressed") or 0)
    robust_ready = (
        ready_count > 0
        and robust_prior_usable == ready_count
        and robust_prior_trusted == ready_count
        and robust_activity_candidate == 0
        and robust_prior_stressed == 0
    )
    gates.append(
        _gate(
            "prior_robustness",
            "pass" if robust_ready else "blocked",
            "all ready rows have trusted priors and no activity/drift signal"
            if robust_ready
            else "prior trust is incomplete or activity/drift candidates are present",
            ready=ready_count,
            robust_prior_usable=robust_prior_usable,
            robust_prior_trusted=robust_prior_trusted,
            robust_activity_candidate=robust_activity_candidate,
            robust_prior_stressed=robust_prior_stressed,
            robust_status_counts=summary.get("robust_status_counts"),
        )
    )
    prior_stress_overall = prior_stress_detail_summary["overall"]
    prior_stress_capacity_flags = prior_stress_overall.get(
        "capacity_flag_counts",
        {},
    )
    prior_stress_capacity_hits = sum(prior_stress_capacity_flags.values())
    prior_stress_group_rows = int(prior_stress_overall.get("rows") or 0)
    prior_stress_drift_ready = (
        prior_stress_group_rows == 0 and prior_stress_capacity_hits == 0
    )
    prior_stress_top_map_groups = _top_prior_stress_groups(
        prior_stress_detail_summary,
        "map_id",
    )
    prior_stress_top_profile_groups = _top_prior_stress_groups(
        prior_stress_detail_summary,
        "hero_map_evidence_profile",
    )
    prior_stress_detail_contract = summarize_prior_stress_detail_contract(
        prior_stress_detail_summary,
        top_map_groups=prior_stress_top_map_groups,
        top_profile_groups=prior_stress_top_profile_groups,
    )
    gates.append(
        _gate(
            "prior_stress_capacity_table_drift",
            "pass" if prior_stress_drift_ready else "blocked",
            "no prior-stressed capacity/table/evidence drift rows"
            if prior_stress_drift_ready
            else "prior-stressed capacity/table/evidence drift requires targeted map/profile audit",
            detail_rows=prior_stress_group_rows,
            capacity_flag_hits=prior_stress_capacity_hits,
            capacity_flag_counts=prior_stress_capacity_flags,
            capacity_count_summary=prior_stress_overall.get("capacity_count_summary"),
            consistency_bucket_counts=prior_stress_overall.get(
                "consistency_bucket_counts"
            ),
            consistency_class_counts=prior_stress_overall.get(
                "consistency_class_counts"
            ),
            source_counts=prior_stress_overall.get("source_counts"),
            ratio_summary=prior_stress_overall.get("ratio_summary"),
            detail_contract=prior_stress_detail_contract,
            capacity_table_audit_contract=capacity_table_audit_contract,
            capacity_table_acquisition_contract=(
                capacity_table_acquisition_contract
            ),
            top_map_groups=prior_stress_top_map_groups,
            top_profile_groups=prior_stress_top_profile_groups,
        )
    )
    scp_ready = int(summary.get("v3_scp_ready_rows") or 0)
    scp_candidate = int(summary.get("v3_scp_candidate_rows") or 0)
    scp_missing_table = int(summary.get("v3_scp_missing_table_rows") or 0)
    scp_active = int(summary.get("v3_scp_active_rows") or 0)
    scp_status = "watch" if scp_ready > 0 and scp_active == 0 else "blocked"
    gates.append(
        _gate(
            "settlement_count_prior_shadow",
            scp_status,
            "settlement count prior evidence is visible and inactive"
            if scp_status == "watch"
            else "settlement count prior shadow fields are missing or active",
            ready_rows=scp_ready,
            candidate_rows=scp_candidate,
            missing_table_rows=scp_missing_table,
            active_rows=scp_active,
            status_counts=summary.get("v3_scp_status_counts"),
        )
    )
    cse_ready = int(summary.get("v3_cse_ready_rows") or 0)
    cse_candidate = int(summary.get("v3_cse_candidate_rows") or 0)
    cse_pressure_candidate = int(
        summary.get("v3_cse_pressure_candidate_rows") or 0
    )
    cse_active = int(summary.get("v3_cse_active_rows") or 0)
    cse_contract_status = str(cse_artifact_contract.get("status") or "blocked")
    cse_row_status = "watch" if cse_ready > 0 and cse_active == 0 else "blocked"
    cse_status = (
        "watch"
        if cse_row_status == "watch" and cse_contract_status != "blocked"
        else "blocked"
    )
    if cse_contract_status == "blocked":
        cse_reason = (
            "capacity/source expansion artifact is not audit-ready: "
            f"{cse_artifact_contract.get('reason')}"
        )
    elif cse_row_status == "watch":
        cse_reason = "capacity/source expansion evidence is visible, inactive, and artifact contract is evaluable"
    else:
        cse_reason = "capacity/source expansion shadow fields are missing or active"
    gates.append(
        _gate(
            "capacity_source_expansion_shadow",
            cse_status,
            cse_reason,
            ready_rows=cse_ready,
            candidate_rows=cse_candidate,
            pressure_candidate_rows=cse_pressure_candidate,
            active_rows=cse_active,
            status_counts=summary.get("v3_cse_status_counts"),
            artifact_contract=cse_artifact_contract,
        )
    )
    scp_link_overall = scp_formal_value_link["overall"]
    scp_link_value_rows = int(
        scp_link_overall.get("scp_candidate_value_floor_rows") or 0
    )
    scp_link_candidate_rows = int(
        scp_link_overall.get("scp_candidate_formal_rows") or 0
    )
    scp_link_delta = scp_link_overall.get("formal_fv_delta_p50_mae")
    scp_link_ready = (
        scp_link_candidate_rows > 0
        and scp_link_value_rows > 0
        and scp_link_delta is not None
        and float(scp_link_delta) < 0.0
    )
    gates.append(
        _gate(
            "settlement_count_formal_value_link",
            "watch" if scp_link_ready else "blocked",
            "settlement count-prior candidates have value-floor overlap and improve formal shadow metrics"
            if scp_link_ready
            else "settlement count-prior candidates are mostly capacity/no-value bridge or do not improve formal shadow metrics",
            formal_rows=scp_link_overall.get("formal_rows"),
            scp_candidate_formal_rows=scp_link_candidate_rows,
            scp_candidate_value_floor_rows=scp_link_value_rows,
            scp_candidate_capacity_watch_rows=scp_link_overall.get(
                "scp_candidate_capacity_watch_rows"
            ),
            formal_fv_delta_p50_mae=scp_link_delta,
            formal_baseline_p50_below_rate=scp_link_overall.get(
                "formal_baseline_p50_below_rate"
            ),
            formal_baseline_p90_coverage=scp_link_overall.get(
                "formal_baseline_p90_coverage"
            ),
            status_counts=scp_formal_value_link.get("status_counts"),
            top_groups=[
                {
                    "group": row.get("group"),
                    "link_status": row.get("link_status"),
                    "scp_candidate_formal_rows": row.get(
                        "scp_candidate_formal_rows"
                    ),
                    "scp_candidate_value_floor_rows": row.get(
                        "scp_candidate_value_floor_rows"
                    ),
                    "scp_candidate_capacity_watch_rows": row.get(
                        "scp_candidate_capacity_watch_rows"
                    ),
                    "formal_baseline_p50_mae": row.get(
                        "formal_baseline_p50_mae"
                    ),
                    "formal_baseline_p50_below_rate": row.get(
                        "formal_baseline_p50_below_rate"
                    ),
                    "formal_baseline_p90_coverage": row.get(
                        "formal_baseline_p90_coverage"
                    ),
                }
                for row in scp_formal_value_link.get("rows", ())[:5]
            ],
        )
    )
    scp_bridge_overall = scp_count_value_bridge["overall"]
    scp_bridge_rows = int(
        scp_bridge_overall.get("count_cells_value_bridge_rows") or 0
    )
    scp_bridge_status = (
        "watch"
        if scp_bridge_rows > 0
        and int(scp_bridge_overall.get("scp_active_rows") or 0) == 0
        else "blocked"
    )
    gates.append(
        _gate(
            "settlement_count_cells_value_bridge",
            scp_bridge_status,
            "settlement count-prior has archive count->cells/value bridge candidates; holdout is still required"
            if scp_bridge_status == "watch"
            else "settlement count-prior lacks a usable count->cells/value bridge or has active rows",
            metric_rows=scp_bridge_overall.get("metric_rows"),
            scp_candidate_metric_rows=scp_bridge_overall.get(
                "scp_candidate_metric_rows"
            ),
            scp_p95_above_target_rows=scp_bridge_overall.get(
                "scp_p95_above_target_rows"
            ),
            cells_p90_under_rows=scp_bridge_overall.get("cells_p90_under_rows"),
            formal_p90_under_rows=scp_bridge_overall.get("formal_p90_under_rows"),
            count_cells_bridge_rows=scp_bridge_overall.get(
                "count_cells_bridge_rows"
            ),
            count_value_bridge_rows=scp_bridge_overall.get(
                "count_value_bridge_rows"
            ),
            count_cells_value_bridge_rows=scp_bridge_rows,
            cells_per_item=scp_bridge_overall.get("truth_cells_per_item"),
            formal_per_item=scp_bridge_overall.get("truth_formal_per_item"),
            status_counts=scp_count_value_bridge.get("status_counts"),
            top_groups=[
                {
                    "group": row.get("group"),
                    "bridge_status": row.get("bridge_status"),
                    "count_cells_value_bridge_rows": row.get(
                        "count_cells_value_bridge_rows"
                    ),
                    "count_cells_bridge_rows": row.get("count_cells_bridge_rows"),
                    "count_value_bridge_rows": row.get("count_value_bridge_rows"),
                    "truth_cells_per_item": row.get("truth_cells_per_item"),
                    "truth_formal_per_item": row.get("truth_formal_per_item"),
                }
                for row in scp_count_value_bridge.get("rows", ())[:5]
            ],
        )
    )
    scp_bridge_holdout_candidate = scp_count_value_bridge_holdout["candidate_only"]
    scp_bridge_holdout_status = scp_count_value_bridge_holdout.get("overall_status")
    gates.append(
        _gate(
            "settlement_count_cells_value_bridge_holdout",
            "watch" if scp_bridge_holdout_status == "watch" else "blocked",
            "settlement count->cells/value bridge passes session holdout but remains shadow-only"
            if scp_bridge_holdout_status == "watch"
            else "settlement count->cells/value bridge does not pass session holdout",
            overall_status=scp_bridge_holdout_status,
            candidate_rows=scp_bridge_holdout_candidate.get("candidate_rows"),
            applied_rows=scp_bridge_holdout_candidate.get("applied_rows"),
            candidate_delta_formal_p50_mae=scp_bridge_holdout_candidate.get(
                "delta_formal_p50_mae"
            ),
            candidate_delta_formal_p90_coverage=scp_bridge_holdout_candidate.get(
                "delta_formal_p90_coverage"
            ),
            candidate_bridge_formal_p50_over_rate=scp_bridge_holdout_candidate.get(
                "bridge_formal_p50_over_rate"
            ),
            applied_hurts_groups=scp_count_value_bridge_holdout.get("applied_hurts"),
            status_counts=scp_count_value_bridge_holdout.get("status_counts"),
        )
    )
    scp_guarded_candidate = scp_guarded_bridge_holdout["candidate_only"]
    scp_guarded_status = scp_guarded_bridge_holdout.get("overall_status")
    gates.append(
        _gate(
            "settlement_count_guarded_bridge_holdout",
            "watch" if scp_guarded_status == "watch" else "blocked",
            "nested train-only guard has a shadow holdout candidate; sample depth and seed stability remain required"
            if scp_guarded_status == "watch"
            else "nested train-only settlement bridge guard does not pass holdout",
            overall_status=scp_guarded_status,
            formal_lift_cap=scp_guarded_bridge_holdout.get("formal_lift_cap"),
            max_guard_over_increase=scp_guarded_bridge_holdout.get(
                "max_guard_over_increase"
            ),
            candidate_rows=scp_guarded_candidate.get("candidate_rows"),
            applied_rows=scp_guarded_candidate.get("applied_rows"),
            candidate_delta_formal_p50_mae=scp_guarded_candidate.get(
                "delta_formal_p50_mae"
            ),
            candidate_delta_formal_p90_coverage=scp_guarded_candidate.get(
                "delta_formal_p90_coverage"
            ),
            candidate_bridge_formal_p50_over_rate=scp_guarded_candidate.get(
                "bridge_formal_p50_over_rate"
            ),
            selected_group_fold_counts=scp_guarded_bridge_holdout.get(
                "selected_group_fold_counts"
            ),
            applied_hurts_groups=scp_guarded_bridge_holdout.get("applied_hurts"),
            guard_status_counts=scp_guarded_bridge_holdout.get(
                "guard_status_counts"
            ),
        )
    )
    stability_status = (
        str(scp_guarded_bridge_stability.get("overall_status") or "unknown")
        if scp_guarded_bridge_stability is not None
        else "not_evaluated"
    )
    stability_contract = summarize_scp_guarded_bridge_stability_contract(
        scp_guarded_bridge_stability
    )
    stability_contract_status = str(stability_contract.get("status") or "blocked")
    stability_gate_status = (
        "watch"
        if stability_contract_status == "watch" and stability_status == "watch"
        else "blocked"
    )
    if scp_guarded_bridge_stability is None:
        stability_reason = (
            "guarded settlement bridge seed stability has not been evaluated"
            if scp_guarded_status == "watch"
            else "guarded settlement bridge holdout must pass before seed stability can promote"
        )
        stability_fields: dict[str, Any] = {
            "overall_status": stability_status,
            "status_reasons": ["missing_stability_matrix"],
            "run_count": 0,
            "watch_runs": 0,
            "required_selected_groups": [],
            "stable_selected_groups": [],
            "union_selected_groups": [],
            "selected_signature_counts": {},
            "hurt_group_counts": {},
            "min_applied_rows": 0,
            "min_applied_rows_required": None,
            "selected_group_support_summary": [],
            "selected_group_support_gap": [],
            "selected_group_guard_summary": [],
            "selected_group_instability_summary": [],
            "contract_check": stability_contract,
        }
    else:
        if stability_contract_status != "watch":
            stability_reason = (
                "guarded settlement bridge stability JSON is not audit-ready: "
                f"{stability_contract.get('reason')}"
            )
        else:
            stability_reason = (
                "guarded settlement bridge is stable across posterior seeds"
                if stability_gate_status == "watch"
                else "guarded settlement bridge is not stable across posterior seeds"
            )
        stability_fields = {
            "overall_status": stability_status,
            "contract_check": stability_contract,
            "status_reasons": scp_guarded_bridge_stability.get(
                "status_reasons"
            ),
            "posterior_trials": scp_guarded_bridge_stability.get(
                "posterior_trials"
            ),
            "posterior_seeds": scp_guarded_bridge_stability.get(
                "posterior_seeds"
            ),
            "run_count": scp_guarded_bridge_stability.get("run_count"),
            "watch_runs": scp_guarded_bridge_stability.get("watch_runs"),
            "required_selected_groups": scp_guarded_bridge_stability.get(
                "required_selected_groups"
            ),
            "stable_selected_groups": scp_guarded_bridge_stability.get(
                "stable_selected_groups"
            ),
            "union_selected_groups": scp_guarded_bridge_stability.get(
                "union_selected_groups"
            ),
            "selected_signature_counts": scp_guarded_bridge_stability.get(
                "selected_signature_counts"
            ),
            "hurt_group_counts": scp_guarded_bridge_stability.get(
                "hurt_group_counts"
            ),
            "min_applied_rows": scp_guarded_bridge_stability.get(
                "min_applied_rows"
            ),
            "min_applied_rows_required": scp_guarded_bridge_stability.get(
                "min_applied_rows_required"
            ),
            "selected_group_support_summary": scp_guarded_bridge_stability.get(
                "selected_group_support_summary"
            ),
            "selected_group_support_gap": scp_guarded_bridge_stability.get(
                "selected_group_support_gap"
            ),
            "selected_group_guard_summary": scp_guarded_bridge_stability.get(
                "selected_group_guard_summary"
            ),
            "selected_group_instability_summary": scp_guarded_bridge_stability.get(
                "selected_group_instability_summary"
            ),
        }
    gates.append(
        _gate(
            "settlement_count_guarded_bridge_stability",
            stability_gate_status,
            stability_reason,
            **stability_fields,
        )
    )
    formal_below = float(summary.get("formal_p50_below_rate") or 0.0)
    formal_p90 = float(summary.get("formal_p90_coverage") or 0.0)
    formal_status = "blocked" if formal_below > 0.50 or formal_p90 < 0.80 else "watch"
    gates.append(
        _gate(
            "formal_baseline_metrics",
            formal_status,
            "formal baseline is still too low for promotion"
            if formal_status == "blocked"
            else "formal baseline is near promotion band but still shadow-only",
            formal_p50_mae=summary.get("formal_p50_mae"),
            formal_p50_below_rate=summary.get("formal_p50_below_rate"),
            formal_p90_coverage=summary.get("formal_p90_coverage"),
            q6_formal_p50_mae=summary.get("q6_formal_p50_mae"),
        )
    )

    under_candidate_only = under["candidate_only"]
    under_candidate_rows = int(under_candidate_only.get("n") or 0)
    under_delta = under_candidate_only.get("delta_formal_p50_mae")
    gates.append(
        _gate(
            "underestimate_repair_holdout",
            "watch" if under_candidate_rows else "blocked",
            "bounded upshift has holdout candidates but remains inactive"
            if under_candidate_rows
            else "no holdout candidate at current sample gate",
            candidate_rows=under_candidate_rows,
            candidate_groups=under_candidate_only.get("candidate_groups"),
            candidate_delta_formal_p50_mae=under_delta,
            overall_delta_formal_p50_mae=under["overall"].get(
                "delta_formal_p50_mae"
            ),
        )
    )

    ccv_counts = _status_counts(ccv)
    ccv_candidate_only = ccv_holdout["candidate_only"]
    ccv_holdout_rows = int(ccv_candidate_only.get("n") or 0)
    ccv_count_delta = ccv_candidate_only.get("delta_q6_count_p50_mae")
    ccv_cells_delta = ccv_candidate_only.get("delta_q6_cells_p50_mae")
    ccv_q6_formal_delta = ccv_candidate_only.get("delta_q6_formal_p50_mae")
    ccv_applied_hurts_groups = _ccv_applied_hurt_groups(ccv_holdout)
    ccv_map_applied_hurts_groups = _ccv_applied_hurt_groups(ccv_map_holdout)
    ccv_holdout_improves = (
        ccv_holdout_rows > 0
        and ccv_count_delta is not None
        and ccv_cells_delta is not None
        and ccv_q6_formal_delta is not None
        and float(ccv_count_delta) <= 0.0
        and float(ccv_cells_delta) < 0.0
        and float(ccv_q6_formal_delta) <= 0.0
        and not ccv_applied_hurts_groups
        and not ccv_map_applied_hurts_groups
    )
    ccv_global_cells_delta = summary.get("v3_ccv_delta_q6_cells_p50_mae")
    ccv_status = (
        "watch"
        if ccv_holdout_improves
        and (ccv_global_cells_delta is None or float(ccv_global_cells_delta) <= 0.0)
        else "blocked"
    )
    gates.append(
        _gate(
            "ccv_sampler",
            ccv_status,
            "CCV holdout is locally positive but remains shadow-only"
            if ccv_status == "watch"
            else "CCV candidate signal does not pass session holdout/global gates",
            status_counts=ccv_counts,
            watch_groups=_top_groups(ccv, "watch_only_count_cell_candidate"),
            global_count_delta=summary.get("v3_ccv_delta_q6_count_p50_mae"),
            global_cells_delta=ccv_global_cells_delta,
            holdout_candidate_rows=ccv_holdout_rows,
            holdout_candidate_groups=ccv_candidate_only.get("candidate_groups"),
            holdout_count_delta=ccv_count_delta,
            holdout_cells_delta=ccv_cells_delta,
            holdout_q6_formal_delta=ccv_q6_formal_delta,
            applied_ccv_hurts_groups=ccv_applied_hurts_groups,
            map_holdout_candidate_rows=ccv_map_holdout["candidate_only"].get("n"),
            map_holdout_candidate_groups=ccv_map_holdout["candidate_only"].get(
                "candidate_groups"
            ),
            map_applied_ccv_hurts_groups=ccv_map_applied_hurts_groups,
        )
    )
    ccv_direction_hurts = _ccv_directional_hurts(ccv_map_direction)
    ccv_profile_direction_hurts = _ccv_directional_hurts(ccv_profile_direction)
    gates.append(
        _gate(
            "ccv_directionality",
            "blocked" if ccv_direction_hurts or ccv_profile_direction_hurts else "watch",
            "CCV p50 movements have map/profile directional hurt"
            if ccv_direction_hurts or ccv_profile_direction_hurts
            else "CCV p50 movement direction is not currently blocking",
            map_direction_hurts=ccv_direction_hurts,
            profile_direction_hurts=ccv_profile_direction_hurts,
        )
    )
    ccv_direction_holdout_hurts = (
        ccv_map_direction_holdout.get("applied_direction_hurts_groups") or []
    )
    ccv_profile_direction_holdout_hurts = (
        ccv_profile_direction_holdout.get("applied_direction_hurts_groups") or []
    )
    ccv_direction_holdout_blocked = (
        ccv_map_direction_holdout.get("overall_status") != "watch"
        or ccv_profile_direction_holdout.get("overall_status") != "watch"
    )
    gates.append(
        _gate(
            "ccv_direction_holdout",
            "blocked" if ccv_direction_holdout_blocked else "watch",
            "directionally selected CCV movements fail session holdout"
            if ccv_direction_holdout_blocked
            else "directionally selected CCV movements pass current holdout but remain shadow-only",
            map_status=ccv_map_direction_holdout.get("overall_status"),
            map_candidate_rows=ccv_map_direction_holdout["candidate_only"].get(
                "candidate_rows"
            ),
            map_candidate_delta=ccv_map_direction_holdout["candidate_only"].get(
                "candidate_only_delta_p50_mae"
            ),
            map_applied_hurts_groups=ccv_direction_holdout_hurts,
            profile_status=ccv_profile_direction_holdout.get("overall_status"),
            profile_candidate_rows=ccv_profile_direction_holdout[
                "candidate_only"
            ].get("candidate_rows"),
            profile_candidate_delta=ccv_profile_direction_holdout[
                "candidate_only"
            ].get("candidate_only_delta_p50_mae"),
            profile_applied_hurts_groups=ccv_profile_direction_holdout_hurts,
        )
    )
    if shadow_sampler_prototype is not None:
        sampler_contract_status = str(
            shadow_sampler_prototype_contract.get("status") or "blocked"
        )
        gates.append(
            _gate(
                "shadow_sampler_prototype",
                "watch" if sampler_contract_status == "watch" else "blocked",
                str(shadow_sampler_prototype_contract.get("reason") or ""),
                contract_status=sampler_contract_status,
                contract_check=shadow_sampler_prototype_contract,
                prototype_status=shadow_sampler_prototype_contract.get(
                    "prototype_status"
                ),
                posterior_seeds=shadow_sampler_prototype_contract.get(
                    "posterior_seeds"
                ),
                stable_watch_candidate_labels=shadow_sampler_prototype_contract.get(
                    "stable_watch_candidate_labels"
                ),
                min_watch_support_rows=shadow_sampler_prototype_contract.get(
                    "min_watch_support_rows"
                ),
                min_watch_support_sessions=shadow_sampler_prototype_contract.get(
                    "min_watch_support_sessions"
                ),
                component_status_counts=shadow_sampler_prototype_contract.get(
                    "component_status_counts"
                ),
                support_gate_status_counts=shadow_sampler_prototype_contract.get(
                    "support_gate_status_counts"
                ),
                blocking_component_statuses=shadow_sampler_prototype_contract.get(
                    "blocking_component_statuses"
                ),
                guard_trial_status=shadow_sampler_prototype_contract.get(
                    "guard_trial_status"
                ),
                guard_trial_action_counts=shadow_sampler_prototype_contract.get(
                    "guard_trial_action_counts"
                ),
                low_support_watch_metrics=shadow_sampler_prototype_contract.get(
                    "low_support_watch_metrics"
                ),
                stable_low_support_watch_metrics=(
                    shadow_sampler_prototype_contract.get(
                        "stable_low_support_watch_metrics"
                    )
                ),
            )
        )
    if shadow_sampler_guard_trial is not None:
        guard_trial_contract_status = str(
            shadow_sampler_guard_trial_contract.get("status") or "blocked"
        )
        gates.append(
            _gate(
                "shadow_sampler_guard_trial",
                "watch" if guard_trial_contract_status == "watch" else "blocked",
                str(shadow_sampler_guard_trial_contract.get("reason") or ""),
                contract_status=guard_trial_contract_status,
                contract_check=shadow_sampler_guard_trial_contract,
                trial_status=shadow_sampler_guard_trial_contract.get(
                    "trial_status"
                ),
                sampler_status=shadow_sampler_guard_trial_contract.get(
                    "sampler_status"
                ),
                source_prototype_status=shadow_sampler_guard_trial_contract.get(
                    "source_prototype_status"
                ),
                source_guard_trial_status=shadow_sampler_guard_trial_contract.get(
                    "source_guard_trial_status"
                ),
                trial_options=shadow_sampler_guard_trial_contract.get(
                    "trial_options"
                ),
                component_status_counts=shadow_sampler_guard_trial_contract.get(
                    "component_status_counts"
                ),
                support_gate_status_counts=shadow_sampler_guard_trial_contract.get(
                    "support_gate_status_counts"
                ),
                blocking_component_statuses=(
                    shadow_sampler_guard_trial_contract.get(
                        "blocking_component_statuses"
                    )
                ),
                required_verification=shadow_sampler_guard_trial_contract.get(
                    "required_verification"
                ),
            )
        )
    if shadow_sampler_value_source_profile_audit is not None:
        value_source_profile_contract_status = str(
            shadow_sampler_value_source_profile_contract.get("status")
            or "blocked"
        )
        gates.append(
            _gate(
                "shadow_sampler_value_source_profile_audit",
                (
                    "watch"
                    if value_source_profile_contract_status == "watch"
                    else "blocked"
                ),
                str(
                    shadow_sampler_value_source_profile_contract.get("reason")
                    or ""
                ),
                contract_status=value_source_profile_contract_status,
                contract_check=shadow_sampler_value_source_profile_contract,
                audit_status=shadow_sampler_value_source_profile_contract.get(
                    "audit_status"
                ),
                component=shadow_sampler_value_source_profile_contract.get(
                    "component"
                ),
                run_count=shadow_sampler_value_source_profile_contract.get(
                    "run_count"
                ),
                risk_migration_detected=(
                    shadow_sampler_value_source_profile_contract.get(
                        "risk_migration_detected"
                    )
                ),
                source_profile_parser_status=(
                    shadow_sampler_value_source_profile_contract.get(
                        "source_profile_parser_status"
                    )
                ),
                profile_semantic_migration_detected=(
                    shadow_sampler_value_source_profile_contract.get(
                        "profile_semantic_migration_detected"
                    )
                ),
                latest_map_only_hurt_label_count=(
                    shadow_sampler_value_source_profile_contract.get(
                        "latest_map_only_hurt_label_count"
                    )
                ),
                latest_profile_hurt_label_count=(
                    shadow_sampler_value_source_profile_contract.get(
                        "latest_profile_hurt_label_count"
                    )
                ),
                introduced_hurt_labels=(
                    shadow_sampler_value_source_profile_contract.get(
                        "introduced_hurt_labels"
                    )
                ),
                removed_hurt_labels=(
                    shadow_sampler_value_source_profile_contract.get(
                        "removed_hurt_labels"
                    )
                ),
                run_summaries=shadow_sampler_value_source_profile_contract.get(
                    "run_summaries"
                ),
                next_action=shadow_sampler_value_source_profile_contract.get(
                    "next_action"
                ),
            )
        )
    if shadow_sampler_value_map_profile_details is not None:
        map_profile_details_contract_status = str(
            shadow_sampler_value_map_profile_details_contract.get("status")
            or "blocked"
        )
        gates.append(
            _gate(
                "shadow_sampler_value_map_profile_details",
                (
                    "watch"
                    if map_profile_details_contract_status == "watch"
                    else "blocked"
                ),
                str(
                    shadow_sampler_value_map_profile_details_contract.get("reason")
                    or ""
                ),
                contract_status=map_profile_details_contract_status,
                contract_check=shadow_sampler_value_map_profile_details_contract,
                details_status=shadow_sampler_value_map_profile_details_contract.get(
                    "details_status"
                ),
                component=shadow_sampler_value_map_profile_details_contract.get(
                    "component"
                ),
                source_audit_status=(
                    shadow_sampler_value_map_profile_details_contract.get(
                        "source_audit_status"
                    )
                ),
                source_profile_parser_status=(
                    shadow_sampler_value_map_profile_details_contract.get(
                        "source_profile_parser_status"
                    )
                ),
                label_count=shadow_sampler_value_map_profile_details_contract.get(
                    "label_count"
                ),
                candidate_rows=shadow_sampler_value_map_profile_details_contract.get(
                    "candidate_rows"
                ),
                candidate_sessions_sum=(
                    shadow_sampler_value_map_profile_details_contract.get(
                        "candidate_sessions_sum"
                    )
                ),
                labels_with_row_count_mismatch=(
                    shadow_sampler_value_map_profile_details_contract.get(
                        "labels_with_row_count_mismatch"
                    )
                ),
                next_action=shadow_sampler_value_map_profile_details_contract.get(
                    "next_action"
                ),
            )
        )
    if shadow_sampler_value_profile_guardability is not None:
        guardability_contract_status = str(
            shadow_sampler_value_profile_guardability_contract.get("status")
            or "blocked"
        )
        gates.append(
            _gate(
                "shadow_sampler_value_profile_guardability",
                "watch" if guardability_contract_status == "watch" else "blocked",
                str(
                    shadow_sampler_value_profile_guardability_contract.get("reason")
                    or ""
                ),
                contract_status=guardability_contract_status,
                contract_check=shadow_sampler_value_profile_guardability_contract,
                guardability_status=(
                    shadow_sampler_value_profile_guardability_contract.get(
                        "guardability_status"
                    )
                ),
                component=shadow_sampler_value_profile_guardability_contract.get(
                    "component"
                ),
                source_details_status=(
                    shadow_sampler_value_profile_guardability_contract.get(
                        "source_details_status"
                    )
                ),
                detail_rows=shadow_sampler_value_profile_guardability_contract.get(
                    "detail_rows"
                ),
                cluster_count=shadow_sampler_value_profile_guardability_contract.get(
                    "cluster_count"
                ),
                candidate_cluster_count=(
                    shadow_sampler_value_profile_guardability_contract.get(
                        "candidate_cluster_count"
                    )
                ),
                overfit_risk_cluster_count=(
                    shadow_sampler_value_profile_guardability_contract.get(
                        "overfit_risk_cluster_count"
                    )
                ),
                mixed_cluster_count=(
                    shadow_sampler_value_profile_guardability_contract.get(
                        "mixed_cluster_count"
                    )
                ),
                next_action=shadow_sampler_value_profile_guardability_contract.get(
                    "next_action"
                ),
            )
        )

    tail_counts = _status_counts(tail)
    tail_watch = (
        tail_counts.get("watch_only_q6_tail_value_candidate", 0)
        + tail_counts.get("watch_only_tail_value_candidate", 0)
    )
    tail_candidate_only = tail_holdout["candidate_only"]
    tail_holdout_rows = int(tail_candidate_only.get("n") or 0)
    tail_delta = tail_candidate_only.get("delta_tail_p50_mae")
    q6_tail_delta = tail_candidate_only.get("delta_q6_tail_p50_mae")
    tail_holdout_improves = (
        tail_holdout_rows > 0
        and (
            (tail_delta is not None and float(tail_delta) < 0.0)
            or (q6_tail_delta is not None and float(q6_tail_delta) < 0.0)
        )
    )
    tail_holdout_hurts = [
        str(row.get("group"))
        for row in tail_holdout.get("group_results", ())
        if (
            row.get("delta_tail_p50_mae") is not None
            and float(row["delta_tail_p50_mae"]) > 10_000.0
        )
        or (
            row.get("delta_q6_tail_p50_mae") is not None
            and float(row["delta_q6_tail_p50_mae"]) > 10_000.0
        )
    ][:5]
    gates.append(
        _gate(
            "tail_value_review",
            "watch" if tail_watch and tail_holdout_improves else "blocked",
            "tail/value review has holdout signal but remains non-formal"
            if tail_watch and tail_holdout_improves
            else "tail/value review lacks stable holdout support",
            status_counts=tail_counts,
            q6_tail_groups=_top_groups(tail, "watch_only_q6_tail_value_candidate"),
            tail_groups=_top_groups(tail, "watch_only_tail_value_candidate"),
            tail_hurts_groups=_top_groups(tail, "blocked_tail_estimate_hurts"),
            holdout_candidate_rows=tail_holdout_rows,
            holdout_candidate_groups=tail_candidate_only.get("candidate_groups"),
            holdout_tail_delta=tail_delta,
            holdout_q6_tail_delta=q6_tail_delta,
            holdout_hurts_groups=tail_holdout_hurts,
        )
    )

    tail_under_candidate = tail_under_holdout["candidate_only"]
    tail_under_rows = int(tail_under_candidate.get("n") or 0)
    tail_under_delta = tail_under_candidate.get("delta_formal_p50_mae")
    tail_under_p90_delta = tail_under_candidate.get("delta_formal_p90_coverage")
    tail_under_extreme_delta = tail_under_candidate.get(
        "delta_formal_p90_extreme_over_rate"
    )
    tail_under_q6_miss = tail_under_candidate.get(
        "candidate_q6_formal_p90_miss_rate"
    )
    tail_under_hurts_groups = [
        str(row.get("group"))
        for row in tail_under_holdout.get("group_results", ())
        if int(row.get("tail_candidate_rows") or 0) > 0
        and (
            (
                row.get("delta_tail_p50_mae") is not None
                and float(row["delta_tail_p50_mae"]) > 10_000.0
            )
            or (
                row.get("delta_q6_tail_p50_mae") is not None
                and float(row["delta_q6_tail_p50_mae"]) > 10_000.0
            )
        )
    ][:5]
    tail_under_improves = (
        tail_under_rows > 0
        and tail_under_delta is not None
        and float(tail_under_delta) <= 0.0
        and (
            tail_under_p90_delta is None
            or float(tail_under_p90_delta) >= 0.0
        )
        and (
            tail_under_extreme_delta is None
            or float(tail_under_extreme_delta) <= 0.02
        )
        and not tail_under_hurts_groups
    )
    gates.append(
        _gate(
            "tail_under_combined_holdout",
            "watch" if tail_under_improves else "blocked",
            "guarded under/tail combination improves holdout without excessive P90 over"
            if tail_under_improves
            else "guarded under/tail combination is not promotion-ready",
            candidate_rows=tail_under_rows,
            under_candidate_groups=tail_under_candidate.get(
                "under_candidate_groups"
            ),
            tail_candidate_groups=tail_under_candidate.get("tail_candidate_groups"),
            tail_hurt_guard_groups=tail_under_holdout["overall"].get(
                "tail_hurt_guard_groups"
            ),
            applied_tail_hurts_groups=tail_under_hurts_groups,
            candidate_delta_formal_p50_mae=tail_under_delta,
            candidate_delta_formal_p50_below_rate=tail_under_candidate.get(
                "delta_formal_p50_below_rate"
            ),
            candidate_delta_formal_p90_coverage=tail_under_p90_delta,
            candidate_delta_formal_p90_extreme_over_rate=tail_under_extreme_delta,
            candidate_q6_formal_p90_miss_rate=tail_under_q6_miss,
            overall_delta_formal_p50_mae=tail_under_holdout["overall"].get(
                "delta_formal_p50_mae"
            ),
        )
    )

    formal_value_sampler_candidate = formal_value_sampler_holdout["candidate_only"]
    formal_value_sampler_rows = int(
        formal_value_sampler_candidate.get("candidate_rows") or 0
    )
    formal_value_sampler_delta = formal_value_sampler_candidate.get(
        "delta_formal_p50_mae"
    )
    formal_value_sampler_q6_delta = formal_value_sampler_candidate.get(
        "delta_q6_formal_p50_mae"
    )
    formal_value_sampler_hurts = formal_value_sampler_holdout.get(
        "applied_hurts",
        [],
    )
    formal_value_sampler_mixed_watch_rows = int(
        formal_value_sampler_holdout["overall"].get("mixed_value_floor_watch_rows")
        or 0
    )
    formal_value_sampler_watch = (
        formal_value_sampler_holdout.get("overall_status") == "watch"
        and formal_value_sampler_rows > 0
        and not formal_value_sampler_hurts
    )
    gates.append(
        _gate(
            "formal_value_sampler_holdout",
            "watch" if formal_value_sampler_watch else "blocked",
            "formal/value sampler has holdout signal but remains shadow-only"
            if formal_value_sampler_watch
            else "formal/value sampler lacks enough safe holdout support",
            overall_status=formal_value_sampler_holdout.get("overall_status"),
            candidate_rows=formal_value_sampler_rows,
            candidate_groups=formal_value_sampler_candidate.get(
                "candidate_groups"
            ),
            candidate_delta_formal_p50_mae=formal_value_sampler_delta,
            candidate_delta_q6_formal_p50_mae=formal_value_sampler_q6_delta,
            candidate_formal_below_rate=formal_value_sampler_candidate.get(
                "candidate_formal_p50_below_rate"
            ),
            candidate_formal_over_rate=formal_value_sampler_candidate.get(
                "candidate_formal_p50_over_rate"
            ),
            candidate_formal_p90_coverage=formal_value_sampler_candidate.get(
                "candidate_formal_p90_coverage"
            ),
            mixed_value_floor_watch_rows=formal_value_sampler_mixed_watch_rows,
            applied_hurts_groups=formal_value_sampler_hurts,
        )
    )

    residual_counts = _status_counts(residual)
    gates.append(
        _gate(
            "residual_gate",
            "blocked",
            "residual remains watch-only; active rows must stay zero",
            status_counts=residual_counts,
            active_rows=summary.get("v3_resid_gate_active_rows"),
        )
    )

    profile_blocked = (
        under_profile["overall"].get("candidate_rows") == 0
        and ccv_profile_holdout["overall"].get("candidate_rows") == 0
        and tail_profile_holdout["overall"].get("candidate_rows") == 0
        and _status_counts(ccv_profile).get("blocked_low_sample", 0) > 0
        and _status_counts(tail_profile).get("blocked_low_sample", 0) > 0
    )
    gates.append(
        _gate(
            "profile_sample_depth",
            "blocked" if profile_blocked else "watch",
            "profile-level promotion remains sample-limited"
            if profile_blocked
            else "some profile-level candidates need review",
            under_profile_candidate_rows=under_profile["overall"].get(
                "candidate_rows"
            ),
            ccv_profile_holdout_candidate_rows=ccv_profile_holdout["overall"].get(
                "candidate_rows"
            ),
            tail_profile_holdout_candidate_rows=tail_profile_holdout["overall"].get(
                "candidate_rows"
            ),
            ccv_profile_status_counts=_status_counts(ccv_profile),
            tail_profile_status_counts=_status_counts(tail_profile),
        )
    )

    gates.append(
        _gate(
            "v2_archive_readiness",
            "pending",
            "v2 cannot be archived until v3 formal path is promoted and verified",
        )
    )

    next_actions = []
    if any(gate["name"] == "formal_baseline_metrics" and gate["status"] == "blocked" for gate in gates):
        next_actions.append("continue 2506 bounded upshift/tail-value shadow validation")
    if profile_blocked:
        next_actions.append("collect targeted profile samples before profile-level promotion")
    if tail_counts.get("blocked_tail_estimate_hurts", 0):
        next_actions.append("keep tail-hurts guard before any tail/value sampler")
    if tail_under_hurts_groups:
        next_actions.append("tighten tail guard; holdout still applies hurting groups")
    if not tail_under_improves:
        next_actions.append("keep under/tail combination in audit until holdout improves")
    if ccv_applied_hurts_groups:
        next_actions.append("tighten CCV guard; holdout still applies hurting groups")
    if ccv_map_applied_hurts_groups:
        next_actions.append("tighten CCV map-layer guard; map holdout applies hurting groups")
    if ccv_direction_hurts or ccv_profile_direction_hurts:
        next_actions.append("redesign CCV likelihood; p50 movement direction is unstable")
    if ccv_direction_holdout_blocked:
        next_actions.append("keep CCV direction selection in audit; holdout is not stable")
    if ccv_counts.get("watch_only_count_cell_candidate", 0):
        next_actions.append("redesign CCV likelihood; current holdout is not promotion-ready")
    if (
        shadow_sampler_guard_trial is not None
        and shadow_sampler_guard_trial_contract.get("status") != "watch"
    ):
        next_actions.append(
            "keep guarded shadow sampler trial blocked; resolve remaining q6 value/source guard"
        )
    if not formal_value_sampler_watch:
        next_actions.append("keep formal/value sampler shadow-only until holdout has safe support")
    if not scp_link_ready:
        next_actions.append("bridge settlement count-prior to cells/value before formal/value sampler promotion")
    if scp_bridge_rows:
        next_actions.append("hold out settlement count->cells/value bridge candidates before sampler use")
    if scp_bridge_holdout_status != "watch":
        next_actions.append("guard or redesign settlement count->cells/value bridge; holdout over-risk is high")
    if scp_guarded_status == "watch":
        next_actions.append("collect more 2506 guarded-bridge holdout support across posterior seeds")
    else:
        next_actions.append("keep nested settlement bridge guard in audit until holdout stabilizes")
    if stability_gate_status != "watch":
        if stability_status == "not_evaluated":
            next_actions.append(
                "run guarded settlement bridge stability matrix before treating seed-0 watch as support"
            )
        else:
            next_actions.append(
                "keep guarded settlement bridge out of promotion; multi-seed stability failed"
            )
    if not prior_stress_drift_ready:
        next_actions.append("audit prior-stressed capacity/table drift by map/profile before promotion")
    if not robust_ready:
        next_actions.append("separate activity/prior-drift rows before formal promotion")
    if live_guard_status == "not_supplied":
        next_actions.append(
            "attach v3 practical archive-live guard brief JSON before promotion review"
        )
    elif live_guard_status == "blocked":
        next_actions.append(
            "regenerate v3 practical brief with paired guarded/unguarded rows"
        )
    else:
        next_actions.append(
            "review v3 practical guard coverage/extreme-over tradeoff by slice before promotion"
        )

    blocked = _blocked_count(gates)
    gate_dependencies = summarize_gate_dependencies(gates)
    return {
        "overall_status": "not_ready" if blocked else "watch_only",
        "blocked_gates": blocked,
        "group_field": group_field,
        "profile_field": profile_field,
        "min_windows": int(min_windows),
        "min_sessions": int(min_sessions),
        "folds": int(folds),
        "summary": {
            key: summary.get(key)
            for key in (
                "windows",
                "ready",
                "no_state",
                "constraint_conflict",
                "parse_errors",
                "posterior_ready",
                "posterior_strict_ready",
                "posterior_summary_likelihood",
                "robust_prior_usable",
                "robust_prior_trusted",
                "robust_activity_candidate",
                "robust_prior_stressed",
                "formal_p50_mae",
                "formal_p50_below_rate",
                "formal_p90_coverage",
                "q6_formal_p50_mae",
                "v3_cal_delta_formal_p50_mae",
                "v3_under_delta_formal_p50_mae",
                "v3_ccv_delta_q6_count_p50_mae",
                "v3_ccv_delta_q6_cells_p50_mae",
                "v3_tail_review_candidate_rows",
                "v3_tail_review_hurt_guard_rows",
                "v3_tail_review_active_rows",
                "v3_fv_candidate_rows",
                "v3_fv_capacity_watch_rows",
                "v3_fv_value_floor_candidate_rows",
                "v3_fv_delta_formal_p50_mae",
                "v3_cse_ready_rows",
                "v3_cse_candidate_rows",
                "v3_cse_pressure_candidate_rows",
                "v3_cse_active_rows",
                "v3_resid_gate_active_rows",
            )
        },
        "gates": gates,
        "gate_dependencies": gate_dependencies,
        "prior_stress_detail_summary": {
            "rows": prior_stress_group_rows,
            "capacity_flag_hits": prior_stress_capacity_hits,
            "detail_contract": prior_stress_detail_contract,
            "capacity_table_audit_contract": capacity_table_audit_contract,
            "capacity_table_acquisition_contract": (
                capacity_table_acquisition_contract
            ),
            "capacity_flag_counts": prior_stress_capacity_flags,
            "capacity_count_summary": prior_stress_overall.get(
                "capacity_count_summary"
            ),
            "consistency_bucket_counts": prior_stress_overall.get(
                "consistency_bucket_counts"
            ),
            "consistency_class_counts": prior_stress_overall.get(
                "consistency_class_counts"
            ),
            "source_counts": prior_stress_overall.get("source_counts"),
            "ratio_summary": prior_stress_overall.get("ratio_summary"),
            "top_map_groups": prior_stress_top_map_groups,
            "top_profile_groups": prior_stress_top_profile_groups,
        },
        "v3_practical_archive_live_guard_metrics": live_guard_brief,
        "v3_practical_guard_loss_source_context_contract": (
            guard_loss_source_context_contract
        ),
        "v3_practical_guard_loss_source_context": guard_loss_source_context or {},
        "activity_drop_universe_overlay_contract": (
            activity_drop_universe_overlay_contract
        ),
        "activity_drop_universe_overlay": activity_drop_universe_overlay or {},
        "source_parser_requirements_contract": source_parser_requirements_contract,
        "source_parser_requirements": source_parser_requirements or {},
        "capacity_table_audit_contract": capacity_table_audit_contract,
        "capacity_table_acquisition_contract": capacity_table_acquisition_contract,
        "capacity_source_expansion_artifact_contract": cse_artifact_contract,
        "shadow_sampler_prototype_contract": shadow_sampler_prototype_contract,
        "shadow_sampler_prototype": shadow_sampler_prototype or {},
        "shadow_sampler_guard_trial_contract": shadow_sampler_guard_trial_contract,
        "shadow_sampler_guard_trial": shadow_sampler_guard_trial or {},
        "shadow_sampler_value_source_profile_contract": (
            shadow_sampler_value_source_profile_contract
        ),
        "shadow_sampler_value_source_profile_audit": (
            shadow_sampler_value_source_profile_audit or {}
        ),
        "shadow_sampler_value_map_profile_details_contract": (
            shadow_sampler_value_map_profile_details_contract
        ),
        "shadow_sampler_value_map_profile_details": (
            shadow_sampler_value_map_profile_details or {}
        ),
        "shadow_sampler_value_profile_guardability_contract": (
            shadow_sampler_value_profile_guardability_contract
        ),
        "shadow_sampler_value_profile_guardability": (
            shadow_sampler_value_profile_guardability or {}
        ),
        "underestimate_holdout": {
            "candidate_rows": under_candidate_rows,
            "candidate_groups": under_candidate_only.get("candidate_groups"),
            "candidate_delta_formal_p50_mae": under_delta,
            "overall_delta_formal_p50_mae": under["overall"].get(
                "delta_formal_p50_mae"
            ),
        },
        "ccv_holdout": {
            "candidate_rows": ccv_holdout_rows,
            "candidate_groups": ccv_candidate_only.get("candidate_groups"),
            "candidate_delta_q6_count_p50_mae": ccv_count_delta,
            "candidate_delta_q6_cells_p50_mae": ccv_cells_delta,
            "candidate_delta_q6_formal_p50_mae": ccv_q6_formal_delta,
            "applied_ccv_hurts_groups": ccv_applied_hurts_groups,
            "map_candidate_rows": ccv_map_holdout["candidate_only"].get("n"),
            "map_candidate_groups": ccv_map_holdout["candidate_only"].get(
                "candidate_groups"
            ),
            "map_applied_ccv_hurts_groups": ccv_map_applied_hurts_groups,
            "map_candidate_delta_q6_count_p50_mae": ccv_map_holdout[
                "candidate_only"
            ].get("delta_q6_count_p50_mae"),
            "map_candidate_delta_q6_cells_p50_mae": ccv_map_holdout[
                "candidate_only"
            ].get("delta_q6_cells_p50_mae"),
            "map_candidate_delta_q6_formal_p50_mae": ccv_map_holdout[
                "candidate_only"
            ].get("delta_q6_formal_p50_mae"),
            "overall_delta_q6_cells_p50_mae": ccv_holdout["overall"].get(
                "delta_q6_cells_p50_mae"
            ),
        },
        "ccv_directionality": {
            "map_direction_hurts": ccv_direction_hurts,
            "profile_direction_hurts": ccv_profile_direction_hurts,
            "map_status_counts": dict(
                sorted(Counter(str(row.get("status")) for row in ccv_map_direction).items())
            ),
            "profile_status_counts": dict(
                sorted(
                    Counter(
                        str(row.get("status")) for row in ccv_profile_direction
                    ).items()
                )
            ),
        },
        "ccv_direction_holdout": {
            "map_status": ccv_map_direction_holdout.get("overall_status"),
            "map_candidate_rows": ccv_map_direction_holdout["candidate_only"].get(
                "candidate_rows"
            ),
            "map_candidate_delta": ccv_map_direction_holdout["candidate_only"].get(
                "candidate_only_delta_p50_mae"
            ),
            "map_applied_hurts_groups": ccv_direction_holdout_hurts,
            "profile_status": ccv_profile_direction_holdout.get("overall_status"),
            "profile_candidate_rows": ccv_profile_direction_holdout[
                "candidate_only"
            ].get("candidate_rows"),
            "profile_candidate_delta": ccv_profile_direction_holdout[
                "candidate_only"
            ].get("candidate_only_delta_p50_mae"),
            "profile_applied_hurts_groups": ccv_profile_direction_holdout_hurts,
        },
        "tail_holdout": {
            "candidate_rows": tail_holdout_rows,
            "candidate_groups": tail_candidate_only.get("candidate_groups"),
            "candidate_delta_tail_p50_mae": tail_delta,
            "candidate_delta_q6_tail_p50_mae": q6_tail_delta,
            "overall_delta_tail_p50_mae": tail_holdout["overall"].get(
                "delta_tail_p50_mae"
            ),
            "overall_delta_q6_tail_p50_mae": tail_holdout["overall"].get(
                "delta_q6_tail_p50_mae"
            ),
            "holdout_hurts_groups": tail_holdout_hurts,
        },
        "tail_under_holdout": {
            "candidate_rows": tail_under_rows,
            "under_candidate_groups": tail_under_candidate.get(
                "under_candidate_groups"
            ),
            "tail_candidate_groups": tail_under_candidate.get(
                "tail_candidate_groups"
            ),
            "tail_hurt_guard_groups": tail_under_holdout["overall"].get(
                "tail_hurt_guard_groups"
            ),
            "applied_tail_hurts_groups": tail_under_hurts_groups,
            "candidate_delta_formal_p50_mae": tail_under_delta,
            "candidate_delta_formal_p50_below_rate": tail_under_candidate.get(
                "delta_formal_p50_below_rate"
            ),
            "candidate_delta_formal_p90_coverage": tail_under_p90_delta,
            "candidate_delta_formal_p90_extreme_over_rate": tail_under_extreme_delta,
            "candidate_q6_formal_p90_miss_rate": tail_under_q6_miss,
            "overall_delta_formal_p50_mae": tail_under_holdout["overall"].get(
                "delta_formal_p50_mae"
            ),
        },
        "formal_value_sampler_holdout": {
            "status": formal_value_sampler_holdout.get("overall_status"),
            "candidate_rows": formal_value_sampler_rows,
            "candidate_groups": formal_value_sampler_candidate.get(
                "candidate_groups"
            ),
            "candidate_delta_formal_p50_mae": formal_value_sampler_delta,
            "candidate_delta_q6_formal_p50_mae": formal_value_sampler_q6_delta,
            "candidate_formal_below_rate": formal_value_sampler_candidate.get(
                "candidate_formal_p50_below_rate"
            ),
            "candidate_formal_over_rate": formal_value_sampler_candidate.get(
                "candidate_formal_p50_over_rate"
            ),
            "candidate_formal_p90_coverage": formal_value_sampler_candidate.get(
                "candidate_formal_p90_coverage"
            ),
            "mixed_value_floor_watch_rows": formal_value_sampler_mixed_watch_rows,
            "applied_hurts_groups": formal_value_sampler_hurts,
            "train_candidate_status_counts": formal_value_sampler_holdout.get(
                "train_candidate_status_counts"
            ),
        },
        "settlement_count_formal_value_link": {
            "formal_rows": scp_link_overall.get("formal_rows"),
            "scp_candidate_formal_rows": scp_link_candidate_rows,
            "scp_candidate_value_floor_rows": scp_link_value_rows,
            "scp_candidate_capacity_watch_rows": scp_link_overall.get(
                "scp_candidate_capacity_watch_rows"
            ),
            "formal_fv_delta_p50_mae": scp_link_delta,
            "formal_baseline_p50_below_rate": scp_link_overall.get(
                "formal_baseline_p50_below_rate"
            ),
            "formal_baseline_p90_coverage": scp_link_overall.get(
                "formal_baseline_p90_coverage"
            ),
            "status_counts": scp_formal_value_link.get("status_counts"),
        },
        "settlement_count_cells_value_bridge": {
            "status": scp_bridge_status,
            "metric_rows": scp_bridge_overall.get("metric_rows"),
            "scp_candidate_metric_rows": scp_bridge_overall.get(
                "scp_candidate_metric_rows"
            ),
            "scp_p95_above_target_rows": scp_bridge_overall.get(
                "scp_p95_above_target_rows"
            ),
            "cells_p90_under_rows": scp_bridge_overall.get("cells_p90_under_rows"),
            "formal_p90_under_rows": scp_bridge_overall.get("formal_p90_under_rows"),
            "count_cells_bridge_rows": scp_bridge_overall.get(
                "count_cells_bridge_rows"
            ),
            "count_value_bridge_rows": scp_bridge_overall.get(
                "count_value_bridge_rows"
            ),
            "count_cells_value_bridge_rows": scp_bridge_rows,
            "cells_per_item": scp_bridge_overall.get("truth_cells_per_item"),
            "formal_per_item": scp_bridge_overall.get("truth_formal_per_item"),
            "status_counts": scp_count_value_bridge.get("status_counts"),
        },
        "settlement_count_cells_value_bridge_holdout": {
            "status": scp_bridge_holdout_status,
            "candidate_rows": scp_bridge_holdout_candidate.get("candidate_rows"),
            "applied_rows": scp_bridge_holdout_candidate.get("applied_rows"),
            "candidate_delta_formal_p50_mae": scp_bridge_holdout_candidate.get(
                "delta_formal_p50_mae"
            ),
            "candidate_delta_formal_p90_coverage": scp_bridge_holdout_candidate.get(
                "delta_formal_p90_coverage"
            ),
            "candidate_bridge_formal_p50_over_rate": scp_bridge_holdout_candidate.get(
                "bridge_formal_p50_over_rate"
            ),
            "applied_hurts_groups": scp_count_value_bridge_holdout.get(
                "applied_hurts"
            ),
            "status_counts": scp_count_value_bridge_holdout.get("status_counts"),
        },
        "settlement_count_guarded_bridge_holdout": {
            "status": scp_guarded_status,
            "formal_lift_cap": scp_guarded_bridge_holdout.get("formal_lift_cap"),
            "max_guard_over_increase": scp_guarded_bridge_holdout.get(
                "max_guard_over_increase"
            ),
            "candidate_rows": scp_guarded_candidate.get("candidate_rows"),
            "applied_rows": scp_guarded_candidate.get("applied_rows"),
            "candidate_delta_formal_p50_mae": scp_guarded_candidate.get(
                "delta_formal_p50_mae"
            ),
            "candidate_delta_formal_p90_coverage": scp_guarded_candidate.get(
                "delta_formal_p90_coverage"
            ),
            "candidate_bridge_formal_p50_over_rate": scp_guarded_candidate.get(
                "bridge_formal_p50_over_rate"
            ),
            "selected_group_fold_counts": scp_guarded_bridge_holdout.get(
                "selected_group_fold_counts"
            ),
            "applied_hurts_groups": scp_guarded_bridge_holdout.get(
                "applied_hurts"
            ),
            "guard_status_counts": scp_guarded_bridge_holdout.get(
                "guard_status_counts"
            ),
        },
        "settlement_count_guarded_bridge_stability": {
            "status": stability_status,
            **stability_fields,
        },
        "ccv_status_counts": ccv_counts,
        "tail_status_counts": tail_counts,
        "residual_status_counts": residual_counts,
        "next_actions": next_actions,
    }


def _print_summary(result: dict[str, Any]) -> None:
    summary = result["summary"]
    live_guard = result["v3_practical_archive_live_guard_metrics"]
    live_guard_overall = live_guard.get("overall") or {}
    live_guard_prebid = live_guard.get("prebid_overall") or {}
    live_guard_cases = (
        live_guard_overall.get("v3_practical_guard_case_summary") or {}
    )
    live_guard_prebid_cases = (
        live_guard_prebid.get("v3_practical_guard_case_summary") or {}
    )
    cse_artifact_contract = result["capacity_source_expansion_artifact_contract"]
    cse_mechanisms = _counter_text(
        cse_artifact_contract.get("mechanism_classes")
        if isinstance(cse_artifact_contract, Mapping)
        else None
    )
    cse_contexts = _counter_text(
        cse_artifact_contract.get("source_context_classes")
        if isinstance(cse_artifact_contract, Mapping)
        else None
    )
    shadow_sampler_prototype_contract = result["shadow_sampler_prototype_contract"]
    shadow_sampler_guard_trial_contract = result[
        "shadow_sampler_guard_trial_contract"
    ]
    shadow_sampler_value_source_profile_contract = result[
        "shadow_sampler_value_source_profile_contract"
    ]
    shadow_sampler_value_map_profile_details_contract = result[
        "shadow_sampler_value_map_profile_details_contract"
    ]
    shadow_sampler_value_profile_guardability_contract = result[
        "shadow_sampler_value_profile_guardability_contract"
    ]
    guard_loss_source_context_contract = result[
        "v3_practical_guard_loss_source_context_contract"
    ]
    guard_loss_source_context_status_counts = _counter_text(
        guard_loss_source_context_contract.get("status_counts")
        if isinstance(guard_loss_source_context_contract, Mapping)
        else None
    )
    activity_drop_universe_overlay_contract = result[
        "activity_drop_universe_overlay_contract"
    ]
    activity_drop_universe_overlay_status_counts = _counter_text(
        activity_drop_universe_overlay_contract.get("status_counts")
        if isinstance(activity_drop_universe_overlay_contract, Mapping)
        else None
    )
    source_parser_requirements_contract = result[
        "source_parser_requirements_contract"
    ]
    source_parser_requirement_counts = _counter_text(
        source_parser_requirements_contract.get("requirement_counts")
        if isinstance(source_parser_requirements_contract, Mapping)
        else None
    )
    scp_stability = result["settlement_count_guarded_bridge_stability"]
    scp_stability_contract = scp_stability.get("contract_check") or {}
    scp_stability_trials = scp_stability_contract.get("posterior_trials") or []
    scp_stability_seeds = scp_stability_contract.get("posterior_seeds") or []
    scp_stability_instability = _selected_group_instability_text(
        scp_stability.get("selected_group_instability_summary")
    )
    prior_stress_contract = (
        result["prior_stress_detail_summary"].get("detail_contract") or {}
    )
    prior_stress_case_counts = prior_stress_contract.get("case_counts") or {}
    prior_stress_top_cases = ",".join(
        f"{key}:{value}"
        for key, value in sorted(
            prior_stress_case_counts.items(),
            key=lambda item: (-int(item[1] or 0), str(item[0])),
        )[:3]
    )
    capacity_table_contract = (
        result["prior_stress_detail_summary"].get(
            "capacity_table_audit_contract"
        )
        or {}
    )
    capacity_table_acquisition_contract = (
        result["prior_stress_detail_summary"].get(
            "capacity_table_acquisition_contract"
        )
        or {}
    )
    capacity_table_semantic_status = _counter_text(
        capacity_table_contract.get("semantic_status_counts")
        if isinstance(capacity_table_contract, Mapping)
        else None
    )
    capacity_table_residual_modes = _counter_text(
        capacity_table_contract.get("residual_mode_counts")
        if isinstance(capacity_table_contract, Mapping)
        else None
    )
    capacity_table_detail_mechanisms = _counter_text(
        capacity_table_contract.get("detail_mechanism_candidate_counts")
        if isinstance(capacity_table_contract, Mapping)
        else None
    )
    capacity_table_detail_next_checks = _counter_text(
        capacity_table_contract.get("detail_next_check_counts")
        if isinstance(capacity_table_contract, Mapping)
        else None
    )
    capacity_table_top_maps = ",".join(
        str(row.get("map_id"))
        for row in capacity_table_contract.get("top_blocked_maps") or []
        if isinstance(row, Mapping) and row.get("map_id") is not None
    )
    capacity_table_acquisition_routes = _counter_text(
        capacity_table_acquisition_contract.get("route_counts")
        if isinstance(capacity_table_acquisition_contract, Mapping)
        else None
    )
    capacity_table_acquisition_sources = _counter_text(
        capacity_table_acquisition_contract.get("source_strength_counts")
        if isinstance(capacity_table_acquisition_contract, Mapping)
        else None
    )
    capacity_table_overlay = (
        capacity_table_acquisition_contract.get("table_overlay_metadata")
        if isinstance(capacity_table_acquisition_contract, Mapping)
        else {}
    )
    capacity_table_overlay_status = (
        capacity_table_overlay.get("local_overlay_status")
        if isinstance(capacity_table_overlay, Mapping)
        else None
    )
    print(
        " ".join(
            (
                f"overall_status={result['overall_status']}",
                f"blocked_gates={result['blocked_gates']}",
                f"windows={summary['windows']}",
                f"ready={summary['ready']}",
                f"formal_mae={summary['formal_p50_mae']}",
                f"formal_below={summary['formal_p50_below_rate']}",
                f"formal_p90_cover={summary['formal_p90_coverage']}",
                f"v3_practical_guard_status={live_guard['status']}",
                "v3_practical_guard_contract_checks="
                f"{','.join(sorted((live_guard.get('contract_checks') or {}).keys()))}",
                "v3_practical_guard_formal_rows="
                f"{live_guard_overall.get('v3_practical_formal_rows')}",
                "v3_practical_guard_comparison_rows="
                f"{live_guard_overall.get('v3_practical_guard_comparison_rows')}",
                "v3_practical_guard_prebid_comparison_rows="
                f"{live_guard_prebid.get('v3_practical_guard_comparison_rows')}",
                "v3_practical_policy_rows="
                f"{(live_guard_overall.get('formal_policy_comparison') or {}).get('comparison_rows')}",
                "v3_practical_policy_prebid_rows="
                f"{(live_guard_prebid.get('formal_policy_comparison') or {}).get('comparison_rows')}",
                "v3_practical_guard_delta_mae="
                f"{live_guard_overall.get('v3_practical_guarded_minus_unguarded_mae')}",
                "v3_practical_guard_delta_p90_coverage="
                f"{live_guard_overall.get('v3_practical_guarded_minus_unguarded_p90_coverage')}",
                "v3_practical_guard_delta_p90_extreme_over="
                f"{live_guard_overall.get('v3_practical_guarded_minus_unguarded_p90_extreme_over')}",
                "v3_practical_guard_case_rows="
                f"{live_guard_cases.get('rows')}",
                "v3_practical_guard_p50_worsened_rows="
                f"{live_guard_cases.get('p50_worsened_rows')}",
                "v3_practical_guard_p90_coverage_lost_rows="
                f"{live_guard_cases.get('p90_coverage_lost_rows')}",
                "v3_practical_guard_p90_extreme_over_added_rows="
                f"{live_guard_cases.get('p90_extreme_over_added_rows')}",
                "v3_practical_guard_prebid_case_rows="
                f"{live_guard_prebid_cases.get('rows')}",
                "v3_practical_guard_prebid_p90_coverage_lost_rows="
                f"{live_guard_prebid_cases.get('p90_coverage_lost_rows')}",
                f"under_delta={summary['v3_under_delta_formal_p50_mae']}",
                f"ccv_cells_delta={summary['v3_ccv_delta_q6_cells_p50_mae']}",
                f"ccv_holdout_rows={result['ccv_holdout']['candidate_rows']}",
                "ccv_applied_hurts="
                + ",".join(result["ccv_holdout"]["applied_ccv_hurts_groups"]),
                f"ccv_map_rows={result['ccv_holdout']['map_candidate_rows']}",
                "ccv_map_applied_hurts="
                + ",".join(result["ccv_holdout"]["map_applied_ccv_hurts_groups"]),
                "ccv_direction_hurts="
                + ",".join(result["ccv_directionality"]["map_direction_hurts"]),
                "ccv_direction_holdout="
                f"{result['ccv_direction_holdout']['map_status']}",
                f"tail_review_candidate_rows={summary['v3_tail_review_candidate_rows']}",
                f"tail_review_hurt_guard_rows={summary['v3_tail_review_hurt_guard_rows']}",
                f"tail_holdout_q6_delta={result['tail_holdout']['candidate_delta_q6_tail_p50_mae']}",
                f"tail_under_rows={result['tail_under_holdout']['candidate_rows']}",
                f"tail_under_formal_delta={result['tail_under_holdout']['candidate_delta_formal_p50_mae']}",
                f"tail_under_p90_extreme_delta={result['tail_under_holdout']['candidate_delta_formal_p90_extreme_over_rate']}",
                "tail_under_applied_hurts="
                + ",".join(result["tail_under_holdout"]["applied_tail_hurts_groups"]),
                f"formal_value_rows={result['formal_value_sampler_holdout']['candidate_rows']}",
                f"formal_value_delta={result['formal_value_sampler_holdout']['candidate_delta_formal_p50_mae']}",
                "formal_value_applied_hurts="
                + ",".join(
                    result["formal_value_sampler_holdout"]["applied_hurts_groups"]
                ),
                f"scp_value_link_rows={result['settlement_count_formal_value_link']['scp_candidate_value_floor_rows']}",
                f"scp_capacity_link_rows={result['settlement_count_formal_value_link']['scp_candidate_capacity_watch_rows']}",
                f"scp_value_link_delta={result['settlement_count_formal_value_link']['formal_fv_delta_p50_mae']}",
                f"scp_count_cells_value_bridge_rows={result['settlement_count_cells_value_bridge']['count_cells_value_bridge_rows']}",
                f"scp_count_cells_bridge_rows={result['settlement_count_cells_value_bridge']['count_cells_bridge_rows']}",
                f"scp_count_value_bridge_rows={result['settlement_count_cells_value_bridge']['count_value_bridge_rows']}",
                f"scp_bridge_holdout_status={result['settlement_count_cells_value_bridge_holdout']['status']}",
                f"scp_bridge_holdout_delta={result['settlement_count_cells_value_bridge_holdout']['candidate_delta_formal_p50_mae']}",
                f"scp_bridge_holdout_over={result['settlement_count_cells_value_bridge_holdout']['candidate_bridge_formal_p50_over_rate']}",
                f"scp_guarded_status={result['settlement_count_guarded_bridge_holdout']['status']}",
                f"scp_guarded_rows={result['settlement_count_guarded_bridge_holdout']['applied_rows']}",
                f"scp_guarded_delta={result['settlement_count_guarded_bridge_holdout']['candidate_delta_formal_p50_mae']}",
                f"scp_guarded_over={result['settlement_count_guarded_bridge_holdout']['candidate_bridge_formal_p50_over_rate']}",
                f"scp_guarded_stability={scp_stability['status']}",
                "scp_guarded_stability_contract="
                f"{scp_stability_contract.get('status')}",
                "scp_guarded_stability_trials="
                f"{','.join(str(trial) for trial in scp_stability_trials)}",
                "scp_guarded_stability_seeds="
                f"{','.join(str(seed) for seed in scp_stability_seeds)}",
                "scp_guarded_stable_groups="
                + ",".join(
                    scp_stability.get("stable_selected_groups")
                    or []
                ),
                f"scp_guarded_instability={scp_stability_instability}",
                "cse_artifact_contract="
                f"{cse_artifact_contract.get('status')}",
                "cse_artifact_entries="
                f"{cse_artifact_contract.get('entries')}",
                "cse_artifact_candidates="
                f"{cse_artifact_contract.get('candidate_entries')}",
                "cse_artifact_group_bys="
                f"{','.join(cse_artifact_contract.get('group_bys') or [])}",
                "cse_source_split="
                f"{cse_artifact_contract.get('source_split_status')}",
                "cse_unique_round_overflow="
                f"{cse_artifact_contract.get('unique_round_overflow_rows')}",
                "cse_session_capacity_semantics="
                f"{cse_artifact_contract.get('session_capacity_source_semantics_rows')}",
                "cse_server_side_expansion="
                f"{cse_artifact_contract.get('server_side_expansion_rows')}",
                f"cse_mechanisms={cse_mechanisms}",
                f"cse_source_contexts={cse_contexts}",
                "guard_loss_source_context="
                f"{guard_loss_source_context_contract.get('status')}",
                "guard_loss_source_context_audit="
                f"{guard_loss_source_context_contract.get('audit_status')}",
                "guard_loss_source_context_maps="
                f"{guard_loss_source_context_contract.get('maps')}",
                "guard_loss_source_context_rows="
                f"{guard_loss_source_context_contract.get('guard_loss_rows')}",
                "guard_loss_source_context_statuses="
                f"{guard_loss_source_context_status_counts}",
                "activity_drop_overlay="
                f"{activity_drop_universe_overlay_contract.get('status')}",
                "activity_drop_overlay_audit="
                f"{activity_drop_universe_overlay_contract.get('audit_status')}",
                "activity_drop_overlay_maps="
                f"{activity_drop_universe_overlay_contract.get('maps')}",
                "activity_drop_overlay_guard_maps="
                f"{activity_drop_universe_overlay_contract.get('guard_loss_overlap_maps')}",
                "activity_drop_overlay_covered_maps="
                f"{activity_drop_universe_overlay_contract.get('candidate_item_universe_covered_maps')}",
                "activity_drop_overlay_hard_map_blocked="
                f"{activity_drop_universe_overlay_contract.get('hard_map_blocked_maps')}",
                "activity_drop_overlay_statuses="
                f"{activity_drop_universe_overlay_status_counts}",
                "source_parser_requirements="
                f"{source_parser_requirements_contract.get('status')}",
                "source_parser_requirements_audit="
                f"{source_parser_requirements_contract.get('audit_status')}",
                "source_parser_maps="
                f"{source_parser_requirements_contract.get('maps')}",
                "source_parser_blocked_maps="
                f"{source_parser_requirements_contract.get('blocked_maps')}",
                "source_parser_session_maps="
                f"{source_parser_requirements_contract.get('session_capacity_maps')}",
                "source_parser_drop_ref_maps="
                f"{source_parser_requirements_contract.get('drop_ref_residual_maps')}",
                "source_parser_numeric_action_rows="
                f"{source_parser_requirements_contract.get('numeric_action_rows')}",
                "source_parser_numeric_session_signals="
                f"{source_parser_requirements_contract.get('numeric_session_capacity_signal_rows')}",
                "source_parser_numeric_non_session="
                f"{source_parser_requirements_contract.get('numeric_non_session_expected_rows')}",
                "source_parser_session_gap_rows="
                f"{source_parser_requirements_contract.get('session_capacity_source_gap_rows')}",
                "source_parser_session_gap_bucket_blocked="
                f"{source_parser_requirements_contract.get('session_gap_bucket_only_blocked_rows')}",
                "source_parser_session_gap_unresolved="
                f"{source_parser_requirements_contract.get('session_gap_unresolved_session_capacity_rows')}",
                "source_parser_payload_table_gap_rows="
                f"{source_parser_requirements_contract.get('payload_table_gap_rows')}",
                "source_parser_payload_table_gap_blocked="
                f"{source_parser_requirements_contract.get('payload_table_gap_blocked_rows')}",
                "source_parser_payload_table_gap_verified="
                f"{source_parser_requirements_contract.get('payload_table_gap_payload_verified_rows')}",
                "source_parser_payload_outer_rows="
                f"{source_parser_requirements_contract.get('payload_outer_field_rows')}",
                "source_parser_payload_outer_metadata="
                f"{source_parser_requirements_contract.get('payload_outer_field_metadata_only_rows')}",
                "source_parser_payload_outer_capacity_candidates="
                f"{source_parser_requirements_contract.get('payload_outer_field_capacity_candidate_rows')}",
                "source_parser_table_overlay_rows="
                f"{source_parser_requirements_contract.get('table_overlay_residual_rows')}",
                "source_parser_table_overlay_blocked="
                f"{source_parser_requirements_contract.get('table_overlay_residual_blocked_rows')}",
                "source_parser_table_overlay_server_open="
                f"{source_parser_requirements_contract.get('table_overlay_residual_server_transform_open_rows')}",
                "source_parser_requirements_counts="
                f"{source_parser_requirement_counts}",
                "shadow_sampler_prototype_contract="
                f"{shadow_sampler_prototype_contract.get('status')}",
                "shadow_sampler_prototype_status="
                f"{shadow_sampler_prototype_contract.get('prototype_status')}",
                "shadow_sampler_guard_trial="
                f"{shadow_sampler_prototype_contract.get('guard_trial_status')}",
                "shadow_sampler_guard_actions="
                + ",".join(
                    f"{key}:{value}"
                    for key, value in (
                        shadow_sampler_prototype_contract.get(
                            "guard_trial_action_counts"
                        )
                        or {}
                    ).items()
                ),
                "shadow_sampler_guarded_trial="
                f"{shadow_sampler_guard_trial_contract.get('status')}",
                "shadow_sampler_guarded_trial_status="
                f"{shadow_sampler_guard_trial_contract.get('trial_status')}",
                "shadow_sampler_guarded_trial_sampler="
                f"{shadow_sampler_guard_trial_contract.get('sampler_status')}",
                "shadow_sampler_value_source_profile="
                f"{shadow_sampler_value_source_profile_contract.get('status')}",
                "shadow_sampler_value_source_profile_status="
                f"{shadow_sampler_value_source_profile_contract.get('audit_status')}",
                "shadow_sampler_value_source_profile_migration="
                f"{shadow_sampler_value_source_profile_contract.get('risk_migration_detected')}",
                "shadow_sampler_value_source_profile_parser="
                f"{shadow_sampler_value_source_profile_contract.get('source_profile_parser_status')}",
                "shadow_sampler_value_map_profile_details="
                f"{shadow_sampler_value_map_profile_details_contract.get('status')}",
                "shadow_sampler_value_map_profile_details_status="
                f"{shadow_sampler_value_map_profile_details_contract.get('details_status')}",
                "shadow_sampler_value_map_profile_details_rows="
                f"{shadow_sampler_value_map_profile_details_contract.get('candidate_rows')}",
                "shadow_sampler_value_profile_guardability="
                f"{shadow_sampler_value_profile_guardability_contract.get('status')}",
                "shadow_sampler_value_profile_guardability_status="
                f"{shadow_sampler_value_profile_guardability_contract.get('guardability_status')}",
                "shadow_sampler_value_profile_guardability_candidates="
                f"{shadow_sampler_value_profile_guardability_contract.get('candidate_cluster_count')}",
                f"cse_candidate_rows={summary['v3_cse_candidate_rows']}",
                f"cse_pressure_candidate_rows={summary['v3_cse_pressure_candidate_rows']}",
                f"cse_active_rows={summary['v3_cse_active_rows']}",
                f"prior_stress_detail_rows={result['prior_stress_detail_summary']['rows']}",
                f"prior_stress_capacity_hits={result['prior_stress_detail_summary']['capacity_flag_hits']}",
                "prior_stress_contract="
                f"{prior_stress_contract.get('status')}",
                f"prior_stress_top_cases={prior_stress_top_cases}",
                "capacity_table_audit_contract="
                f"{capacity_table_contract.get('status')}",
                "capacity_table_semantic_status="
                f"{capacity_table_semantic_status}",
                "capacity_table_residual_modes="
                f"{capacity_table_residual_modes}",
                "capacity_table_detail_mechanisms="
                f"{capacity_table_detail_mechanisms}",
                "capacity_table_detail_next_checks="
                f"{capacity_table_detail_next_checks}",
                "capacity_table_top_maps="
                f"{capacity_table_top_maps}",
                "capacity_table_acquisition_contract="
                f"{capacity_table_acquisition_contract.get('status')}",
                "capacity_table_acquisition_routes="
                f"{capacity_table_acquisition_routes}",
                "capacity_table_acquisition_sources="
                f"{capacity_table_acquisition_sources}",
                "capacity_table_overlay="
                f"{capacity_table_overlay_status}",
                f"resid_gate_active={summary['v3_resid_gate_active_rows']}",
            )
        )
    )
    for gate in result["gates"]:
        print(
            " ".join(
                (
                    f"gate={gate['name']}",
                    f"status={gate['status']}",
                    f"reason={json.dumps(gate['reason'], ensure_ascii=False)}",
                )
            )
        )
    dependencies = result["gate_dependencies"]
    if dependencies["blocked_or_pending_lanes"]:
        print(
            "gate_dependency_lanes="
            + ",".join(dependencies["blocked_or_pending_lanes"])
        )
    if result["next_actions"]:
        print("next_actions=" + " | ".join(result["next_actions"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize v3 formal-promotion readiness blockers.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--by", default="hero_map_id")
    parser.add_argument("--profile-by", default="hero_map_evidence_profile")
    parser.add_argument("--min-windows", type=int, default=20)
    parser.add_argument("--min-sessions", type=int, default=8)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    parser.add_argument("--posterior-trials", type=int, default=512)
    parser.add_argument("--posterior-seed", type=int, default=0)
    parser.add_argument(
        "--tail-value-review",
        type=Path,
        default=_default_tail_value_review_path(),
    )
    parser.add_argument("--no-tail-value-review", action="store_true")
    parser.add_argument(
        "--settlement-count-prior",
        type=Path,
        default=_default_settlement_count_prior_path(),
    )
    parser.add_argument("--no-settlement-count-prior", action="store_true")
    parser.add_argument(
        "--capacity-source-expansion",
        type=Path,
        default=_default_capacity_source_expansion_path(),
    )
    parser.add_argument("--no-capacity-source-expansion", action="store_true")
    parser.add_argument(
        "--capacity-table-audit-json",
        type=Path,
        help=(
            "Optional JSON output from summarize_v3_capacity_table_audit.py "
            "--format json. This attaches capacity semantic residual modes "
            "to the prior-stress capacity/table drift gate."
        ),
    )
    parser.add_argument(
        "--capacity-table-acquisition-json",
        type=Path,
        help=(
            "Optional JSON output from "
            "summarize_v3_capacity_table_acquisition_audit.py --format json. "
            "This attaches table/source acquisition routes to the "
            "prior-stress capacity/table drift gate."
        ),
    )
    parser.add_argument(
        "--guarded-bridge-stability-json",
        type=Path,
        help=(
            "Optional JSON output from summarize_v3_scp_guarded_bridge_stability.py "
            "to attach multi-seed stability evidence to readiness."
        ),
    )
    parser.add_argument(
        "--live-practical-brief-json",
        type=Path,
        help=(
            "Optional JSON output from summarize_live_windivert_brief.py "
            "--archive-formal-mode v3_practical --format json. This attaches "
            "paired guarded/unguarded live-practical guard evidence to readiness."
        ),
    )
    parser.add_argument(
        "--guard-loss-source-context-json",
        type=Path,
        help=(
            "Optional JSON output from "
            "summarize_v3_practical_guard_loss_source_context.py --format json. "
            "This attaches map-level guard coverage-loss CSE/SCP/capacity "
            "source-context evidence to readiness without changing gates."
        ),
    )
    parser.add_argument(
        "--activity-drop-universe-overlay-json",
        type=Path,
        help=(
            "Optional JSON output from "
            "summarize_v3_activity_drop_universe_overlay_audit.py --format json. "
            "This attaches 252x activity/drop-universe overlay coverage and "
            "hard-map blocker evidence to readiness without changing gates."
        ),
    )
    parser.add_argument(
        "--source-parser-requirements-json",
        type=Path,
        help=(
            "Optional JSON output from "
            "summarize_v3_source_parser_requirements_audit.py --format json. "
            "This attaches source parser requirement evidence without changing gates."
        ),
    )
    parser.add_argument(
        "--shadow-sampler-prototype-json",
        type=Path,
        help=(
            "Optional JSON output from summarize_v3_shadow_sampler_prototype.py "
            "--format json. This attaches support-aware sampler prototype "
            "seed/support/holdout evidence to readiness."
        ),
    )
    parser.add_argument(
        "--shadow-sampler-guard-trial-json",
        type=Path,
        help=(
            "Optional JSON output from summarize_v3_shadow_sampler_guard_trial.py "
            "--format json. This attaches guarded sampler trial holdout "
            "evidence to readiness."
        ),
    )
    parser.add_argument(
        "--shadow-sampler-value-source-profile-json",
        type=Path,
        help=(
            "Optional JSON output from "
            "summarize_v3_shadow_sampler_value_source_profile_audit.py "
            "--format json. This attaches q6_value source/profile residual "
            "migration evidence to readiness."
        ),
    )
    parser.add_argument(
        "--shadow-sampler-value-map-profile-details-json",
        type=Path,
        help=(
            "Optional JSON output from "
            "summarize_v3_shadow_sampler_value_map_profile_details.py "
            "--format json. This attaches q6_value map-only row/source "
            "details to readiness."
        ),
    )
    parser.add_argument(
        "--shadow-sampler-value-profile-guardability-json",
        type=Path,
        help=(
            "Optional JSON output from "
            "summarize_v3_shadow_sampler_value_profile_guardability.py "
            "--format json. This attaches q6_value source/profile guardability "
            "evidence to readiness."
        ),
    )
    args = parser.parse_args(argv)

    capacity_source_expansion_artifact = None
    if not args.no_capacity_source_expansion:
        if args.capacity_source_expansion.exists():
            with args.capacity_source_expansion.open("r", encoding="utf-8") as handle:
                capacity_source_expansion_artifact = json.load(handle)
        else:
            capacity_source_expansion_artifact = {}
    capacity_table_audit = None
    if args.capacity_table_audit_json is not None:
        with args.capacity_table_audit_json.open("r", encoding="utf-8") as handle:
            capacity_table_audit = json.load(handle)
    capacity_table_acquisition_audit = None
    if args.capacity_table_acquisition_json is not None:
        with args.capacity_table_acquisition_json.open(
            "r",
            encoding="utf-8",
        ) as handle:
            capacity_table_acquisition_audit = json.load(handle)
    rows, errors = evaluate_paths(
        args.paths or _default_paths(),
        tables=load_monitor_tables(),
        calibration_entries=load_prior_calibration_entries(
            _default_calibration_path()
        ),
        underestimate_repair_entries=load_underestimate_repair_entries(
            _default_underestimate_repair_path()
        ),
        tail_value_review_entries=(
            {}
            if args.no_tail_value_review
            else load_tail_value_review_entries(args.tail_value_review)
        ),
        settlement_count_prior_entries=(
            {}
            if args.no_settlement_count_prior
            else load_settlement_count_prior_entries(args.settlement_count_prior)
        ),
        capacity_source_expansion_entries=(
            {}
            if args.no_capacity_source_expansion
            else load_capacity_source_expansion_entries(args.capacity_source_expansion)
        ),
        posterior_trials=args.posterior_trials,
        posterior_seed=args.posterior_seed,
    )
    guarded_bridge_stability = None
    if args.guarded_bridge_stability_json is not None:
        with args.guarded_bridge_stability_json.open("r", encoding="utf-8") as handle:
            guarded_bridge_stability = json.load(handle)
    live_practical_guard_brief = None
    if args.live_practical_brief_json is not None:
        with args.live_practical_brief_json.open("r", encoding="utf-8") as handle:
            live_practical_guard_brief = json.load(handle)
    guard_loss_source_context = None
    if args.guard_loss_source_context_json is not None:
        with args.guard_loss_source_context_json.open("r", encoding="utf-8") as handle:
            guard_loss_source_context = json.load(handle)
    activity_drop_universe_overlay = None
    if args.activity_drop_universe_overlay_json is not None:
        with args.activity_drop_universe_overlay_json.open(
            "r",
            encoding="utf-8",
        ) as handle:
            activity_drop_universe_overlay = json.load(handle)
    source_parser_requirements = None
    if args.source_parser_requirements_json is not None:
        with args.source_parser_requirements_json.open(
            "r",
            encoding="utf-8",
        ) as handle:
            source_parser_requirements = json.load(handle)
    shadow_sampler_prototype = None
    if args.shadow_sampler_prototype_json is not None:
        with args.shadow_sampler_prototype_json.open("r", encoding="utf-8") as handle:
            shadow_sampler_prototype = json.load(handle)
    shadow_sampler_guard_trial = None
    if args.shadow_sampler_guard_trial_json is not None:
        with args.shadow_sampler_guard_trial_json.open("r", encoding="utf-8") as handle:
            shadow_sampler_guard_trial = json.load(handle)
    shadow_sampler_value_source_profile_audit = None
    if args.shadow_sampler_value_source_profile_json is not None:
        with args.shadow_sampler_value_source_profile_json.open(
            "r",
            encoding="utf-8",
        ) as handle:
            shadow_sampler_value_source_profile_audit = json.load(handle)
    shadow_sampler_value_map_profile_details = None
    if args.shadow_sampler_value_map_profile_details_json is not None:
        with args.shadow_sampler_value_map_profile_details_json.open(
            "r",
            encoding="utf-8",
        ) as handle:
            shadow_sampler_value_map_profile_details = json.load(handle)
    shadow_sampler_value_profile_guardability = None
    if args.shadow_sampler_value_profile_guardability_json is not None:
        with args.shadow_sampler_value_profile_guardability_json.open(
            "r",
            encoding="utf-8",
        ) as handle:
            shadow_sampler_value_profile_guardability = json.load(handle)
    result = summarize_readiness(
        rows,
        errors,
        group_field=args.by,
        profile_field=args.profile_by,
        min_windows=args.min_windows,
        min_sessions=args.min_sessions,
        folds=args.folds,
        scp_guarded_bridge_stability=guarded_bridge_stability,
        capacity_table_audit=capacity_table_audit,
        capacity_table_acquisition_audit=capacity_table_acquisition_audit,
        capacity_source_expansion_artifact=capacity_source_expansion_artifact,
        live_practical_guard_brief=live_practical_guard_brief,
        guard_loss_source_context=guard_loss_source_context,
        activity_drop_universe_overlay=activity_drop_universe_overlay,
        source_parser_requirements=source_parser_requirements,
        shadow_sampler_prototype=shadow_sampler_prototype,
        shadow_sampler_guard_trial=shadow_sampler_guard_trial,
        shadow_sampler_value_source_profile_audit=(
            shadow_sampler_value_source_profile_audit
        ),
        shadow_sampler_value_map_profile_details=(
            shadow_sampler_value_map_profile_details
        ),
        shadow_sampler_value_profile_guardability=(
            shadow_sampler_value_profile_guardability
        ),
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(result)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
