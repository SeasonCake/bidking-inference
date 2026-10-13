"""Evaluate v3 pre-bid constraint coverage for Fatbeans captures.

This is a shadow-only v3 scaffold. It emits auditable ConstraintSet,
FeasibleSummary, truth, prior, and small summary-conditioned posterior fields.
None of these fields affect live/formal bidding.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from bidking_lab.inference.v3 import (  # noqa: E402
    PriorCalibrationEntry,
    V3CcvOptions,
    compile_feasible_summary,
    compile_hard_constraints,
    decision_truth_from_fatbeans,
    empty_decision_truth_flat_dict,
    empty_feasible_summary_flat_dict,
    empty_capacity_source_expansion_flat_dict,
    empty_formal_value_sampler_flat_dict,
    empty_posterior_flat_dict,
    empty_practical_advisory_flat_dict,
    empty_prior_calibration_flat_dict,
    empty_prior_flat_dict,
    empty_prior_robustness_flat_dict,
    empty_residual_gate_flat_dict,
    empty_residual_target_candidate_flat_dict,
    empty_settlement_count_prior_flat_dict,
    empty_tail_value_review_flat_dict,
    empty_underestimate_repair_flat_dict,
    empty_truth_flat_dict,
    estimate_shadow_pipeline,
    events_from_fatbeans,
    load_capacity_source_expansion_entries,
    load_prior_calibration_entries,
    load_settlement_count_prior_entries,
    load_tail_value_review_entries,
    load_underestimate_repair_entries,
    ordinary_shape_replacement_values,
    sample_truth_bank,
    settlement_truth_from_fatbeans,
    assess_prior_robustness,
    summarize_drop_prior_flat_dict,
    settlement_count_prior_entry_for,
    capacity_source_expansion_entry_for,
    tail_value_review_entry_for,
    underestimate_entry_for,
)
from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansCaptureEvents,
    hero_mode_from_state,
    parse_fatbeans_capture,
)
from bidking_lab.live.monitor import load_monitor_tables  # noqa: E402
from build_clean_eval_set import (  # noqa: E402
    activity_eval_files,
    clean_eval_files_many,
    sample_selection_metadata,
    selection_row_fields,
)

HARD_OBSERVED_PUBLIC_INFO_IDS = {
    200001,
    200002,
    200003,
    200021,
    200022,
    200023,
    200024,
    200025,
    200039,
    200048,
    200049,
    200050,
}


def _default_paths() -> tuple[Path, ...]:
    return tuple(Path(path) for path in clean_eval_files_many())


def _default_calibration_path() -> Path:
    return ROOT / "data" / "processed" / "v3_prior_calibration_shadow.json"


def _default_underestimate_repair_path() -> Path:
    return ROOT / "data" / "processed" / "v3_underestimate_repair_shadow.json"


def _default_tail_value_review_path() -> Path:
    return ROOT / "data" / "processed" / "v3_tail_value_review_shadow.json"


def _default_settlement_count_prior_path() -> Path:
    return ROOT / "data" / "processed" / "v3_settlement_count_prior_shadow.json"


def _default_capacity_source_expansion_path() -> Path:
    return ROOT / "data" / "processed" / "v3_capacity_source_expansion_shadow.json"


def _iter_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    expanded: list[Path] = []
    for path in paths:
        if path.is_dir():
            expanded.extend(path.rglob("*.json"))
        elif path.exists():
            expanded.append(path)
    return tuple(sorted(set(expanded)))


def _session_count(events: FatbeansCaptureEvents) -> int:
    session_ids: set[str] = set()
    for state in events.states:
        if state.session_id:
            session_ids.add(str(state.session_id))
    for send in events.sends:
        if send.session_id:
            session_ids.add(str(send.session_id))
    for status in events.statuses:
        if status.session_id:
            session_ids.add(str(status.session_id))
    return len(session_ids)


def _observed_settlement_inconsistency(events: FatbeansCaptureEvents) -> bool:
    inventory_states = [state for state in events.states if state.inventory_items]
    if not inventory_states:
        return False
    final_state = inventory_states[-1]
    final_by_runtime = {
        item.runtime_id: item
        for item in final_state.inventory_items
        if item.runtime_id is not None
    }

    def mismatches(item: Any) -> bool:
        final_item = final_by_runtime.get(getattr(item, "runtime_id", None))
        if final_item is None:
            return True
        observed_quality = getattr(item, "quality", None)
        return observed_quality is not None and final_item.quality != observed_quality

    for state in events.states:
        if state is final_state:
            continue
        for info in state.public_infos:
            if int(info.info_id) not in HARD_OBSERVED_PUBLIC_INFO_IDS:
                continue
            if any(mismatches(item) for item in info.observed_items):
                return True
        for result in state.action_results:
            if any(mismatches(item) for item in result.observed_items):
                return True
        for reveal in state.skill_reveals:
            if any(mismatches(item) for item in reveal.observed_items):
                return True
    return False


def _strict_sample_exclusion_reason(path: Path, events: FatbeansCaptureEvents) -> str:
    name = path.name.lower()
    parts = {part.lower() for part in path.parts}
    if "fatbeans_invalid" in parts or name.startswith("fatbeans_invalid_"):
        return "quarantined_sample"
    if name.startswith("fatbeans_mixed_"):
        return "mixed_sample"
    if name.startswith("fatbeans_invalid_"):
        return "invalid_sample"
    if _session_count(events) > 1:
        return "multi_session_capture"
    if _observed_settlement_inconsistency(events):
        return "observed_settlement_inconsistency"
    return ""


def _events_before_sort(events: FatbeansCaptureEvents, sort_id: int) -> FatbeansCaptureEvents:
    return FatbeansCaptureEvents(
        packets=tuple(row for row in events.packets if int(row.sort_id) < sort_id),
        frames=tuple(row for row in events.frames if int(row.sort_id) < sort_id),
        sends=tuple(row for row in events.sends if int(row.sort_id) < sort_id),
        states=tuple(row for row in events.states if int(row.sort_id) < sort_id),
        statuses=tuple(row for row in events.statuses if int(row.sort_id) < sort_id),
    )


def _recoverable_first_ahmad_prebid_state(
    events: FatbeansCaptureEvents,
) -> tuple[Any | None, int | None]:
    bid_sends = [send for send in events.sends if getattr(send, "kind", "") == "bid"]
    if not bid_sends:
        return None, None
    first_bid = bid_sends[0]
    first_bid_sort = int(first_bid.sort_id)
    if any(int(state.sort_id) < first_bid_sort for state in events.states):
        return None, None
    session_id = getattr(first_bid, "session_id", None)
    if not session_id:
        return None, None
    next_state = next(
        (
            state
            for state in events.states
            if int(state.sort_id) > first_bid_sort
            and getattr(state, "session_id", None) == session_id
            and getattr(state, "map_id", None) is not None
        ),
        None,
    )
    if next_state is None or hero_mode_from_state(next_state) != "ahmed":
        return None, None
    if getattr(next_state, "round_index", None) not in (2,):
        return None, None

    r1_skill_reveals = tuple(
        reveal
        for reveal in (getattr(next_state, "skill_reveals", ()) or ())
        if getattr(reveal, "hero_id", None) in (None, 204)
        and getattr(reveal, "round_index", None) in (None, 1)
    )
    numeric_public_infos = tuple(
        info
        for info in (getattr(next_state, "public_infos", ()) or ())
        if not (getattr(info, "observed_items", ()) or ())
    )
    if not r1_skill_reveals and not numeric_public_infos:
        return None, None

    stripped_bids = tuple(
        replace(bid, values=())
        for bid in (getattr(next_state, "bids", ()) or ())
    )
    recovered = replace(
        next_state,
        sort_id=max(0, first_bid_sort - 1),
        capture_time=getattr(first_bid, "capture_time", getattr(next_state, "capture_time", "")),
        message_id=0x0025,
        round_index=1,
        bids=stripped_bids,
        action_results=(),
        skill_reveals=r1_skill_reveals,
        public_infos=numeric_public_infos,
        inventory_items=(),
        settlement_loss_units=None,
    )
    return recovered, first_bid_sort


def _with_recovered_first_ahmad_prebid(
    events: FatbeansCaptureEvents,
) -> tuple[FatbeansCaptureEvents, set[int]]:
    recovered, bid_sort_id = _recoverable_first_ahmad_prebid_state(events)
    if recovered is None or bid_sort_id is None:
        return events, set()
    return (
        FatbeansCaptureEvents(
            packets=events.packets,
            frames=events.frames,
            sends=events.sends,
            states=tuple(sorted((*events.states, recovered), key=lambda state: int(state.sort_id))),
            statuses=events.statuses,
        ),
        {bid_sort_id},
    )


def _latest_map_id(events: FatbeansCaptureEvents) -> int | None:
    for state in reversed(events.states):
        map_id = getattr(state, "map_id", None)
        if map_id is not None:
            return int(map_id)
    return None


def _map_family(map_id: int | None) -> str:
    if map_id is None:
        return "unknown"
    family = int(map_id) // 100
    if family in (24, 34, 44):
        return "villa"
    if family in (25, 35, 45):
        return "shipwreck"
    if family in (26, 36, 46):
        return "hidden"
    return "other"


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _evidence_stage(round_no: int | None) -> str:
    if round_no is None:
        return "unknown"
    if int(round_no) <= 2:
        return "early_1_2"
    if int(round_no) <= 4:
        return "mid_3_4"
    return "full_5"


def _latest_hero(events: FatbeansCaptureEvents) -> str:
    for state in reversed(events.states):
        hero = hero_mode_from_state(state)
        if hero:
            return hero
    return "unknown"


def _event_targets(event: Any) -> set[str]:
    return {str(target) for target in (getattr(event, "targets", ()) or ())}


def _category_evidence_count(evidence_events: Iterable[Any]) -> int:
    return sum(
        1
        for event in evidence_events
        if "category_anchors" in _event_targets(event)
        or str(getattr(event, "semantic", "")).startswith("category_")
    )


def _evidence_profile_key(
    evidence_events: Iterable[Any],
    *,
    constraints: Any | None,
    summary: Any | None,
) -> str:
    events = tuple(evidence_events)
    parts: list[str] = []
    public_flags: set[str] = set()
    has_public_random_avg = False
    has_category = False
    has_item = bool(getattr(constraints, "item_anchors", {}) or {})
    has_shape = bool(getattr(constraints, "shape_anchors", {}) or {})
    has_layout = False

    for event in events:
        targets = _event_targets(event)
        semantic = str(getattr(event, "semantic", ""))
        source_kind = str(getattr(event, "source_kind", ""))
        if source_kind == "public_info":
            if "session.total_count" in targets or "session.total_cells" in targets:
                public_flags.add("total")
            if "quality_ceiling" in targets:
                public_flags.add("max_quality")
            if "max_item_cells" in targets:
                public_flags.add("max_item_cells")
            if "random_avg_value" in targets or (
                semantic.startswith("random_") and "avg" in semantic
            ):
                has_public_random_avg = True
        if "category_anchors" in targets or semantic.startswith("category_"):
            has_category = True
        if "item_anchors" in targets:
            has_item = True
        if "shape_anchors" in targets:
            has_shape = True
        if semantic in {"all_outlines", "full_outline_session_total", "ethan_full_outline"}:
            has_layout = True
        if "shape_anchors" in targets and (
            "session.total_cells" in targets or "session.total_count" in targets
        ):
            has_layout = True

    public_parts = [
        label
        for label in ("total", "max_quality", "max_item_cells")
        if label in public_flags
    ]
    if public_parts:
        parts.append("public:" + "+".join(public_parts))
    if has_public_random_avg:
        parts.append("public:random_avg")
    if has_category:
        parts.append("tool:category")
    if has_item:
        parts.append("item")
    if has_shape:
        parts.append("shape")
    if (
        has_layout
        or (
            summary is not None
            and getattr(summary, "session_total_cells_exact", None) is not None
            and has_shape
        )
    ):
        parts.append("layout")
    return "+".join(parts) if parts else "basic"


def _information_density_score(
    *,
    round_no: int | None,
    constraints: Any | None,
    evidence_events: Iterable[Any],
    evidence_profile_key: str,
) -> int:
    round_value = int(round_no or 0)
    item_anchors = len(getattr(constraints, "item_anchors", {}) or {})
    shape_anchors = len(getattr(constraints, "shape_anchors", {}) or {})
    quality_floor_anchors = len(getattr(constraints, "quality_floor_anchors", {}) or {})
    category_targets = _category_evidence_count(evidence_events)
    public_bonus = 2 if "public:" in evidence_profile_key else 0
    return (
        round_value * 2
        + min(item_anchors, 6) * 2
        + min(shape_anchors + category_targets, 6) * 2
        + min(shape_anchors, 8)
        + min(quality_floor_anchors, 4)
        + public_bonus
    )


def _information_density_band(score: int | None) -> str:
    if score is None:
        return "unknown"
    if score < 18:
        return "low"
    if score < 34:
        return "medium"
    return "high"


def _diagnostic_slice_fields(
    *,
    prefix: FatbeansCaptureEvents,
    map_id: int | None,
    map_family: str,
    window_round: int,
    evidence_events: Iterable[Any],
    constraints: Any | None,
    summary: Any | None,
) -> dict[str, Any]:
    events = tuple(evidence_events)
    hero = _latest_hero(prefix)
    stage = _evidence_stage(window_round)
    profile = _evidence_profile_key(
        events,
        constraints=constraints,
        summary=summary,
    )
    density_score = _information_density_score(
        round_no=window_round,
        constraints=constraints,
        evidence_events=events,
        evidence_profile_key=profile,
    )
    density_band = _information_density_band(density_score)
    map_label = str(map_id) if map_id is not None else "unknown"
    return {
        "hero": hero,
        "evidence_stage": stage,
        "evidence_profile_key": profile,
        "information_density_score": density_score,
        "information_density_band": density_band,
        "hero_information_density": f"{hero}|{density_band}",
        "hero_evidence_stage": f"{hero}|{stage}",
        "hero_map_family": f"{hero}|{map_family}",
        "hero_map_id": f"{hero}|{map_label}",
        "hero_map_evidence_stage": f"{hero}|{map_label}|{stage}",
        "hero_map_evidence_profile": f"{hero}|{map_label}|{profile}",
    }


def _empty_prior_flat_dict() -> dict[str, Any]:
    return empty_prior_flat_dict()


def _prior_flat_dict(
    map_id: int | None,
    *,
    tables: Any | None,
    cache: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    if map_id is None or tables is None:
        return _empty_prior_flat_dict()
    if map_id in cache:
        return cache[map_id]
    row = summarize_drop_prior_flat_dict(
        int(map_id),
        maps=tables.maps,
        drops=tables.drops,
        items=tables.items,
    )
    cache[map_id] = row
    return row


def _replacement_values(
    map_id: int | None,
    *,
    tables: Any | None,
    cache: dict[int, dict[tuple[int, int, int], int]],
) -> dict[tuple[int, int, int], int]:
    if map_id is None or tables is None:
        return {}
    if map_id not in cache:
        try:
            cache[map_id] = ordinary_shape_replacement_values(
                int(map_id),
                maps=tables.maps,
                drops=tables.drops,
                items=tables.items,
            )
        except Exception:
            cache[map_id] = {}
    return cache[map_id]


def _truth_bank(
    map_id: int | None,
    *,
    tables: Any | None,
    cache: dict[int, tuple[Any, ...]],
    n_trials: int,
    seed: int,
) -> tuple[Any, ...]:
    if map_id is None or tables is None or n_trials <= 0:
        return ()
    if map_id not in cache:
        try:
            cache[map_id] = sample_truth_bank(
                int(map_id),
                maps=tables.maps,
                drops=tables.drops,
                items=tables.items,
                n_trials=n_trials,
                seed=seed,
            )
        except Exception:
            cache[map_id] = ()
    return cache[map_id]


def _round_rows_for_events(
    path: Path,
    events: FatbeansCaptureEvents,
    *,
    tables: Any | None = None,
    calibration_entries: Mapping[int, PriorCalibrationEntry] | None = None,
    underestimate_repair_entries: Mapping[tuple[str, int], Any] | None = None,
    tail_value_review_entries: Mapping[tuple[str, int], Any] | None = None,
    settlement_count_prior_entries: Mapping[tuple[str, str], Any] | None = None,
    capacity_source_expansion_entries: Mapping[tuple[str, str], Any] | None = None,
    posterior_trials: int = 512,
    posterior_seed: int = 0,
    ccv_options: V3CcvOptions | None = None,
) -> list[dict[str, Any]]:
    events, recovered_first_prebid_sort_ids = _with_recovered_first_ahmad_prebid(events)
    rows: list[dict[str, Any]] = []
    prior_cache: dict[int, dict[str, Any]] = {}
    replacement_cache: dict[int, dict[tuple[int, int, int], int]] = {}
    truth_bank_cache: dict[int, tuple[Any, ...]] = {}
    truth = (
        settlement_truth_from_fatbeans(events, items=tables.items)
        if tables is not None
        else None
    )
    truth_fields = truth.to_flat_dict() if truth is not None else empty_truth_flat_dict()
    empty_decision_truth_fields = empty_decision_truth_flat_dict()
    empty_summary_fields = empty_feasible_summary_flat_dict()
    empty_posterior_fields = empty_posterior_flat_dict()
    empty_ccv_fields = empty_posterior_flat_dict(prefix="v3_ccv_")
    empty_ccvc_fields = empty_posterior_flat_dict(prefix="v3_ccvc_")
    empty_residual_fields = empty_posterior_flat_dict(prefix="v3_resid_")
    empty_residual_gate_fields = empty_residual_gate_flat_dict()
    empty_residual_target_fields = empty_residual_target_candidate_flat_dict()
    empty_calibration_fields = empty_prior_calibration_flat_dict()
    empty_robust_fields = empty_prior_robustness_flat_dict()
    empty_underestimate_fields = empty_underestimate_repair_flat_dict()
    empty_tail_review_fields = empty_tail_value_review_flat_dict()
    empty_formal_value_fields = empty_formal_value_sampler_flat_dict()
    empty_settlement_count_prior_fields = empty_settlement_count_prior_flat_dict()
    empty_capacity_source_expansion_fields = empty_capacity_source_expansion_flat_dict()
    empty_practical_advisory_fields = empty_practical_advisory_flat_dict()
    bid_sends = [send for send in events.sends if getattr(send, "kind", "") == "bid"]
    previous_bid_sort_id = 0
    for window_round, bid_send in enumerate(bid_sends, start=1):
        bid_sort_id = int(bid_send.sort_id)
        prefix = _events_before_sort(events, bid_sort_id)
        map_id = _latest_map_id(prefix)
        map_family = _map_family(map_id)
        prior_fields = _prior_flat_dict(map_id, tables=tables, cache=prior_cache)
        round_states = [
            state
            for state in events.states
            if previous_bid_sort_id < int(state.sort_id) < bid_sort_id
        ]
        round_action_sends = [
            send
            for send in events.sends
            if getattr(send, "kind", "") == "action"
            and previous_bid_sort_id < int(send.sort_id) < bid_sort_id
        ]
        if not prefix.states:
            diagnostic_fields = _diagnostic_slice_fields(
                prefix=prefix,
                map_id=map_id,
                map_family=map_family,
                window_round=window_round,
                evidence_events=(),
                constraints=None,
                summary=None,
            )
            robust_fields = assess_prior_robustness(
                map_id=map_id,
                map_family=map_family,
                summary=None,
                prior_fields=prior_fields,
                posterior_fields=empty_posterior_fields,
            ).to_flat_dict()
            capacity_fields = _capacity_flat_dict(
                prior_fields=prior_fields,
                truth_fields=truth_fields,
                summary_fields=empty_summary_fields,
            )
            rows.append(
                {
                    "file": f"{path.name}#prebid_r{window_round}_sort{bid_sort_id}",
                    "source": "fatbeans_archive_v3_prebid",
                    "status": "no_state",
                    "round": window_round,
                    "session_id": getattr(bid_send, "session_id", None),
                    "bid_sort_id": bid_sort_id,
                    "recovered_first_prebid_state": False,
                    "recovered_first_prebid_source": "",
                    "bid_value": getattr(bid_send, "value", None),
                    "map_id": map_id,
                    "map_family": map_family,
                    **diagnostic_fields,
                    "prior_state_count": 0,
                    "round_state_count": len(round_states),
                    "round_action_send_count": len(round_action_sends),
                    "numeric_constraints": 0,
                    "item_anchors": 0,
                    "shape_anchors": 0,
                    "quality_floor_anchors": 0,
                    "conflicts": 0,
                    "constraint_ok": False,
                    **prior_fields,
                    **capacity_fields,
                    **truth_fields,
                    **empty_decision_truth_fields,
                    **empty_summary_fields,
                    **empty_posterior_fields,
                    **empty_ccv_fields,
                    **empty_ccvc_fields,
                    **empty_residual_fields,
                    **empty_residual_gate_fields,
                    **empty_residual_target_fields,
                    **empty_calibration_fields,
                    **robust_fields,
                    **empty_underestimate_fields,
                    **empty_tail_review_fields,
                    **empty_formal_value_fields,
                    **empty_settlement_count_prior_fields,
                    **empty_capacity_source_expansion_fields,
                    **empty_practical_advisory_fields,
                }
            )
            previous_bid_sort_id = bid_sort_id
            continue
        evidence_events = events_from_fatbeans(prefix)
        constraints = compile_hard_constraints(evidence_events)
        feasible_summary = compile_feasible_summary(constraints)
        feasible_summary_fields = feasible_summary.to_flat_dict()
        diagnostic_fields = _diagnostic_slice_fields(
            prefix=prefix,
            map_id=map_id,
            map_family=map_family,
            window_round=window_round,
            evidence_events=evidence_events,
            constraints=constraints,
            summary=feasible_summary,
        )
        replacement_values = _replacement_values(
            map_id,
            tables=tables,
            cache=replacement_cache,
        )
        decision_truth = (
            decision_truth_from_fatbeans(
                events,
                items=tables.items,
                constraints=constraints,
                replacement_values=replacement_values,
            )
            if tables is not None
            else None
        )
        decision_truth_fields = (
            decision_truth.to_flat_dict()
            if decision_truth is not None
            else empty_decision_truth_fields
        )
        posterior_truths = _truth_bank(
            map_id,
            tables=tables,
            cache=truth_bank_cache,
            n_trials=posterior_trials,
            seed=posterior_seed,
        )
        calibration_entry = (
            calibration_entries.get(int(map_id))
            if calibration_entries is not None and map_id is not None
            else None
        )
        underestimate_entry = underestimate_entry_for(
            underestimate_repair_entries,
            hero=str(diagnostic_fields.get("hero") or "unknown"),
            map_id=map_id,
        )
        tail_review_entry = tail_value_review_entry_for(
            tail_value_review_entries,
            hero=str(diagnostic_fields.get("hero") or "unknown"),
            map_id=map_id,
        )
        settlement_count_prior_entry = settlement_count_prior_entry_for(
            settlement_count_prior_entries,
            map_id=map_id,
        )
        capacity_source_expansion_entry = capacity_source_expansion_entry_for(
            capacity_source_expansion_entries,
            map_id=map_id,
            map_family=map_family,
        )
        pipeline = (
            estimate_shadow_pipeline(
                map_id=int(map_id),
                map_name=str(prior_fields.get("v3_prior_map_name") or ""),
                summary=feasible_summary,
                truths=posterior_truths,
                constraints=constraints,
                replacement_values=replacement_values,
                calibration_entry=calibration_entry,
                underestimate_entry=underestimate_entry,
                tail_review_entry=tail_review_entry,
                settlement_count_prior_entry=settlement_count_prior_entry,
                capacity_source_expansion_entry=capacity_source_expansion_entry,
                hero=str(diagnostic_fields.get("hero") or "unknown"),
                ccv_options=ccv_options,
                prior_fields=prior_fields,
                evidence_events=evidence_events,
                evidence_profile_key=str(
                    diagnostic_fields.get("evidence_profile_key") or ""
                ),
            )
            if map_id is not None and tables is not None and posterior_trials > 0
            else None
        )
        posterior = pipeline.posterior if pipeline is not None else None
        posterior_fields = (
            posterior.to_flat_dict()
            if posterior is not None
            else empty_posterior_fields
        )
        ccv_fields = (
            pipeline.ccv_posterior.to_flat_dict(prefix="v3_ccv_")
            if pipeline is not None
            else empty_ccv_fields
        )
        ccvc_fields = (
            pipeline.ccv_component_posterior.to_flat_dict(prefix="v3_ccvc_")
            if pipeline is not None and pipeline.ccv_component_posterior is not None
            else empty_ccvc_fields
        )
        residual_fields = (
            pipeline.residual_posterior.to_flat_dict(prefix="v3_resid_")
            if pipeline is not None
            else empty_residual_fields
        )
        residual_gate_fields = (
            pipeline.residual_gate.to_flat_dict()
            if pipeline is not None
            else empty_residual_gate_fields
        )
        residual_target_fields = (
            pipeline.residual_targets.to_flat_dict()
            if pipeline is not None
            else empty_residual_target_fields
        )
        calibration_fields = (
            pipeline.calibration.to_flat_dict()
            if pipeline is not None
            else empty_calibration_fields
        )
        robust_fields = (
            assess_prior_robustness(
                map_id=map_id,
                map_family=map_family,
                summary=feasible_summary,
                prior_fields=prior_fields,
                posterior_fields=posterior_fields,
            ).to_flat_dict()
            if map_id is not None
            else empty_robust_fields
        )
        underestimate_fields = (
            pipeline.underestimate.to_flat_dict()
            if pipeline is not None
            else empty_underestimate_fields
        )
        tail_review_fields = (
            pipeline.tail_review.to_flat_dict()
            if pipeline is not None
            else empty_tail_review_fields
        )
        formal_value_fields = (
            pipeline.formal_value.to_flat_dict()
            if pipeline is not None
            else empty_formal_value_fields
        )
        settlement_count_prior_fields = (
            pipeline.settlement_count_prior.to_flat_dict()
            if pipeline is not None
            else empty_settlement_count_prior_fields
        )
        capacity_source_expansion_fields = (
            pipeline.capacity_source_expansion.to_flat_dict()
            if pipeline is not None
            else empty_capacity_source_expansion_fields
        )
        practical_advisory_fields = (
            pipeline.practical_advisory.to_flat_dict()
            if pipeline is not None
            else empty_practical_advisory_fields
        )
        capacity_fields = _capacity_flat_dict(
            prior_fields=prior_fields,
            truth_fields=truth_fields,
            summary_fields=feasible_summary_fields,
        )
        rows.append(
            {
                "file": f"{path.name}#prebid_r{window_round}_sort{bid_sort_id}",
                "source": "fatbeans_archive_v3_prebid",
                "status": "ready" if constraints.feasible else "constraint_conflict",
                "round": window_round,
                "session_id": getattr(bid_send, "session_id", None),
                "bid_sort_id": bid_sort_id,
                "recovered_first_prebid_state": bid_sort_id in recovered_first_prebid_sort_ids,
                "recovered_first_prebid_source": (
                    "ahmad_first_post_bid_state_stable_r1_projection"
                    if bid_sort_id in recovered_first_prebid_sort_ids
                    else ""
                ),
                "bid_value": getattr(bid_send, "value", None),
                "map_id": map_id,
                "map_family": map_family,
                **diagnostic_fields,
                "prior_state_count": len(prefix.states),
                "round_state_count": len(round_states),
                "round_action_send_count": len(round_action_sends),
                "numeric_constraints": len(constraints.numeric),
                "item_anchors": len(constraints.item_anchors),
                "shape_anchors": len(constraints.shape_anchors),
                "quality_floor_anchors": len(constraints.quality_floor_anchors),
                "conflicts": len(constraints.conflicts),
                "constraint_ok": constraints.feasible,
                **prior_fields,
                **capacity_fields,
                **truth_fields,
                **decision_truth_fields,
                **feasible_summary_fields,
                **posterior_fields,
                **ccv_fields,
                **ccvc_fields,
                **residual_fields,
                **residual_gate_fields,
                **residual_target_fields,
                **calibration_fields,
                **robust_fields,
                **underestimate_fields,
                **tail_review_fields,
                **formal_value_fields,
                **settlement_count_prior_fields,
                **capacity_source_expansion_fields,
                **practical_advisory_fields,
            }
        )
        previous_bid_sort_id = bid_sort_id
    return rows


def evaluate_paths(
    paths: Iterable[Path],
    *,
    tables: Any | None = None,
    calibration_entries: Mapping[int, PriorCalibrationEntry] | None = None,
    underestimate_repair_entries: Mapping[tuple[str, int], Any] | None = None,
    tail_value_review_entries: Mapping[tuple[str, int], Any] | None = None,
    settlement_count_prior_entries: Mapping[tuple[str, str], Any] | None = None,
    capacity_source_expansion_entries: Mapping[tuple[str, str], Any] | None = None,
    posterior_trials: int = 512,
    posterior_seed: int = 0,
    ccv_options: V3CcvOptions | None = None,
    include_mixed_samples: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path in _iter_paths(paths):
        try:
            events = parse_fatbeans_capture(path)
        except Exception as exc:
            errors.append({"file": str(path), "error": type(exc).__name__})
            continue
        reason = _strict_sample_exclusion_reason(path, events)
        if reason == "quarantined_sample":
            continue
        if reason and not include_mixed_samples:
            continue
        rows.extend(
            _round_rows_for_events(
                path,
                events,
                tables=tables,
                calibration_entries=calibration_entries,
                underestimate_repair_entries=underestimate_repair_entries,
                tail_value_review_entries=tail_value_review_entries,
                settlement_count_prior_entries=settlement_count_prior_entries,
                capacity_source_expansion_entries=capacity_source_expansion_entries,
                posterior_trials=posterior_trials,
                posterior_seed=posterior_seed,
                ccv_options=ccv_options,
            )
        )
    return rows, errors


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _count_source_and_target(
    *,
    exact: Any,
    floor: Any,
) -> tuple[str, float | None]:
    exact_value = _float_or_none(exact)
    floor_value = _float_or_none(floor)
    if exact_value is not None and exact_value > 0.0:
        if floor_value is not None and floor_value > exact_value:
            return ("floor_over_exact", floor_value)
        return ("exact", exact_value)
    if floor_value is not None and floor_value > 0.0:
        return ("floor", floor_value)
    return ("none", None)


def _numeric_delta(left: Any, right: Any) -> float | None:
    left_value = _float_or_none(left)
    right_value = _float_or_none(right)
    if left_value is None or right_value is None:
        return None
    return left_value - right_value


def _numeric_ratio(left: Any, right: Any) -> float | None:
    left_value = _float_or_none(left)
    right_value = _float_or_none(right)
    if left_value is None or right_value is None or right_value <= 0.0:
        return None
    return left_value / right_value


def _capacity_cases(
    *,
    total_count_source: str,
    target_prior_max_delta: float | None,
    truth_prior_max_delta: float | None,
    target_truth_delta: float | None,
) -> list[str]:
    target_above_prior = (
        target_prior_max_delta is not None and target_prior_max_delta > 0.0
    )
    truth_above_prior = (
        truth_prior_max_delta is not None and truth_prior_max_delta > 0.0
    )
    target_below_truth = target_truth_delta is not None and target_truth_delta < 0.0
    target_matches_truth = target_truth_delta is not None and target_truth_delta == 0.0
    target_above_truth = target_truth_delta is not None and target_truth_delta > 0.0
    cases: list[str] = []
    if target_above_prior and truth_above_prior and target_matches_truth:
        cases.append("direct_prior_max_conflict")
    if truth_above_prior and target_below_truth:
        cases.append("target_lower_bound_truth_above_prior")
    if target_above_prior and target_below_truth:
        cases.append("target_above_prior_but_below_truth")
    if target_above_prior and target_above_truth:
        cases.append("target_over_truth_capacity_risk")
    if truth_above_prior and total_count_source == "none":
        cases.append("truth_above_prior_without_count_target")
    if target_above_prior and not truth_above_prior:
        cases.append("target_above_prior_without_truth_support")
    if truth_above_prior and not target_above_prior and not target_below_truth:
        cases.append("truth_above_prior_without_target_prior_hit")
    if not cases:
        cases.append("no_capacity_prior_max_case")
    return cases


def _capacity_flat_dict(
    *,
    prior_fields: Mapping[str, Any],
    truth_fields: Mapping[str, Any],
    summary_fields: Mapping[str, Any],
) -> dict[str, Any]:
    source, target = _count_source_and_target(
        exact=summary_fields.get("v3_summary_session_total_count_exact"),
        floor=summary_fields.get("v3_summary_known_count_floor"),
    )
    truth_count = _float_or_none(truth_fields.get("v3_truth_item_count"))
    prior_min = _float_or_none(prior_fields.get("v3_prior_items_per_session_min"))
    prior_max = _float_or_none(prior_fields.get("v3_prior_items_per_session_max"))
    target_prior_delta = _numeric_delta(target, prior_max)
    truth_prior_delta = _numeric_delta(truth_count, prior_max)
    target_truth_delta = _numeric_delta(target, truth_count)
    flags: list[str] = []
    if prior_max is not None and target is not None and target > prior_max:
        flags.append("target_count_above_prior_max")
    if prior_max is not None and truth_count is not None and truth_count > prior_max:
        flags.append("truth_count_above_prior_max")
    if prior_min is not None and truth_count is not None and truth_count < prior_min:
        flags.append("truth_count_below_prior_min")
    cases = _capacity_cases(
        total_count_source=source,
        target_prior_max_delta=target_prior_delta,
        truth_prior_max_delta=truth_prior_delta,
        target_truth_delta=target_truth_delta,
    )
    return {
        "v3_capacity_total_count_source": source,
        "v3_capacity_total_count_target": _round_metric(target, 3),
        "v3_capacity_truth_item_count": _round_metric(truth_count, 3),
        "v3_capacity_prior_items_per_session_min": _round_metric(prior_min, 3),
        "v3_capacity_prior_items_per_session_max": _round_metric(prior_max, 3),
        "v3_capacity_target_prior_max_delta": _round_metric(
            target_prior_delta,
            3,
        ),
        "v3_capacity_truth_prior_max_delta": _round_metric(truth_prior_delta, 3),
        "v3_capacity_target_truth_delta": _round_metric(target_truth_delta, 3),
        "v3_capacity_target_prior_max_ratio": _round_metric(
            _numeric_ratio(target, prior_max),
            3,
        ),
        "v3_capacity_truth_prior_max_ratio": _round_metric(
            _numeric_ratio(truth_count, prior_max),
            3,
        ),
        "v3_capacity_flags": "+".join(flags),
        "v3_capacity_cases": "+".join(cases),
    }


def _mean(values: Iterable[float]) -> float | None:
    seq = tuple(values)
    if not seq:
        return None
    return sum(seq) / len(seq)


def _round_metric(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


def _pinball_loss(truth: float, prediction: float, quantile: float) -> float:
    delta = truth - prediction
    if delta >= 0:
        return quantile * delta
    return (1.0 - quantile) * (-delta)


def _normalized_error_denominator(truth: float) -> float:
    return max(100_000.0, abs(truth))


def _paired_metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    paired = [
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_decision_available")
        and row.get("v3_post_ready")
        and _float_or_none(row.get("v3_post_formal_decision_value_p50")) is not None
        and _float_or_none(row.get("v3_truth_formal_decision_value")) is not None
    ]
    calibrated = [
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_decision_available")
        and row.get("v3_cal_ready")
        and _float_or_none(row.get("v3_cal_formal_decision_value_p50")) is not None
        and _float_or_none(row.get("v3_truth_formal_decision_value")) is not None
    ]
    ccv_ready = [
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_available")
        and row.get("v3_ccv_ready")
    ]
    ccvc_ready = [
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_available")
        and row.get("v3_ccvc_ready")
    ]
    residual_ready = [
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_available")
        and row.get("v3_resid_ready")
    ]
    residual_gate_ready = [
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_available")
        and row.get("v3_resid_gate_ready")
    ]
    underestimate_ready = [
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_decision_available")
        and row.get("v3_under_ready")
        and _float_or_none(row.get("v3_under_formal_decision_value_p50")) is not None
        and _float_or_none(row.get("v3_truth_formal_decision_value")) is not None
    ]
    tail_review_ready = [
        row
        for row in rows
        if row.get("status") == "ready" and row.get("v3_tail_review_ready")
    ]
    formal_value_ready = [
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_decision_available")
        and row.get("v3_fv_ready")
        and _float_or_none(row.get("v3_fv_formal_decision_value_p50")) is not None
        and _float_or_none(row.get("v3_truth_formal_decision_value")) is not None
    ]
    settlement_count_prior_ready = [
        row
        for row in rows
        if row.get("status") == "ready" and row.get("v3_scp_ready")
    ]
    capacity_source_expansion_ready = [
        row
        for row in rows
        if row.get("status") == "ready" and row.get("v3_cse_ready")
    ]
    practical_ready = [
        row
        for row in rows
        if row.get("status") == "ready"
        and row.get("v3_truth_decision_available")
        and row.get("v3_practical_ready")
        and _float_or_none(row.get("v3_practical_formal_decision_value_p50"))
        is not None
        and _float_or_none(row.get("v3_truth_formal_decision_value")) is not None
    ]

    def pred_truth(
        pred_key: str,
        truth_key: str,
        *,
        scope: str | None = None,
        source_rows: Iterable[dict[str, Any]] | None = None,
    ) -> tuple[tuple[float, float], ...]:
        pairs: list[tuple[float, float]] = []
        iter_rows = tuple(source_rows) if source_rows is not None else paired
        for row in iter_rows:
            if scope == "strict" and row.get("v3_post_match_scope") != "strict":
                continue
            if scope == "fallback" and row.get("v3_post_match_scope") == "strict":
                continue
            pred = _float_or_none(row.get(pred_key))
            truth = _float_or_none(row.get(truth_key))
            if pred is None or truth is None:
                continue
            pairs.append((pred, truth))
        return tuple(pairs)

    formal_p50 = pred_truth(
        "v3_post_formal_decision_value_p50",
        "v3_truth_formal_decision_value",
    )
    formal_p50_strict = pred_truth(
        "v3_post_formal_decision_value_p50",
        "v3_truth_formal_decision_value",
        scope="strict",
    )
    formal_p50_fallback = pred_truth(
        "v3_post_formal_decision_value_p50",
        "v3_truth_formal_decision_value",
        scope="fallback",
    )
    formal_p90 = pred_truth(
        "v3_post_formal_decision_value_p90",
        "v3_truth_formal_decision_value",
    )
    formal_p90_strict = pred_truth(
        "v3_post_formal_decision_value_p90",
        "v3_truth_formal_decision_value",
        scope="strict",
    )
    formal_p90_fallback = pred_truth(
        "v3_post_formal_decision_value_p90",
        "v3_truth_formal_decision_value",
        scope="fallback",
    )
    q6_p50 = pred_truth(
        "v3_post_q6_formal_decision_value_p50",
        "v3_truth_q6_formal_decision_value",
    )
    q6_p50_strict = pred_truth(
        "v3_post_q6_formal_decision_value_p50",
        "v3_truth_q6_formal_decision_value",
        scope="strict",
    )
    q6_p50_fallback = pred_truth(
        "v3_post_q6_formal_decision_value_p50",
        "v3_truth_q6_formal_decision_value",
        scope="fallback",
    )
    q6_p90 = pred_truth(
        "v3_post_q6_formal_decision_value_p90",
        "v3_truth_q6_formal_decision_value",
    )
    q6_count_p50 = pred_truth(
        "v3_post_q6_count_p50",
        "v3_truth_q6_count",
    )
    q6_count_p90 = pred_truth(
        "v3_post_q6_count_p90",
        "v3_truth_q6_count",
    )
    q6_cells_p50 = pred_truth(
        "v3_post_q6_cells_p50",
        "v3_truth_q6_cells",
    )
    q6_cells_p90 = pred_truth(
        "v3_post_q6_cells_p90",
        "v3_truth_q6_cells",
    )
    q6_value_p50 = pred_truth(
        "v3_post_q6_value_p50",
        "v3_truth_q6_raw_value",
    )
    ccv_q6_count_p50 = pred_truth(
        "v3_ccv_q6_count_p50",
        "v3_truth_q6_count",
        source_rows=ccv_ready,
    )
    ccv_q6_count_p90 = pred_truth(
        "v3_ccv_q6_count_p90",
        "v3_truth_q6_count",
        source_rows=ccv_ready,
    )
    ccv_q6_cells_p50 = pred_truth(
        "v3_ccv_q6_cells_p50",
        "v3_truth_q6_cells",
        source_rows=ccv_ready,
    )
    ccv_q6_cells_p90 = pred_truth(
        "v3_ccv_q6_cells_p90",
        "v3_truth_q6_cells",
        source_rows=ccv_ready,
    )
    ccvc_q6_count_p50 = pred_truth(
        "v3_ccvc_q6_count_p50",
        "v3_truth_q6_count",
        source_rows=ccvc_ready,
    )
    ccvc_q6_count_p90 = pred_truth(
        "v3_ccvc_q6_count_p90",
        "v3_truth_q6_count",
        source_rows=ccvc_ready,
    )
    ccvc_q6_cells_p50 = pred_truth(
        "v3_ccvc_q6_cells_p50",
        "v3_truth_q6_cells",
        source_rows=ccvc_ready,
    )
    ccvc_q6_cells_p90 = pred_truth(
        "v3_ccvc_q6_cells_p90",
        "v3_truth_q6_cells",
        source_rows=ccvc_ready,
    )
    ccvc_q6_value_p50 = pred_truth(
        "v3_ccvc_q6_value_p50",
        "v3_truth_q6_raw_value",
        source_rows=ccvc_ready,
    )
    residual_q6_count_p50 = pred_truth(
        "v3_resid_q6_count_p50",
        "v3_truth_q6_count",
        source_rows=residual_ready,
    )
    residual_q6_count_p90 = pred_truth(
        "v3_resid_q6_count_p90",
        "v3_truth_q6_count",
        source_rows=residual_ready,
    )
    residual_q6_cells_p50 = pred_truth(
        "v3_resid_q6_cells_p50",
        "v3_truth_q6_cells",
        source_rows=residual_ready,
    )
    residual_q6_cells_p90 = pred_truth(
        "v3_resid_q6_cells_p90",
        "v3_truth_q6_cells",
        source_rows=residual_ready,
    )
    residual_q6_value_p50 = pred_truth(
        "v3_resid_q6_value_p50",
        "v3_truth_q6_raw_value",
        source_rows=residual_ready,
    )
    residual_gate_q6_count_p50 = pred_truth(
        "v3_resid_gate_q6_count_p50",
        "v3_truth_q6_count",
        source_rows=residual_gate_ready,
    )
    residual_gate_q6_count_p90 = pred_truth(
        "v3_resid_gate_q6_count_p90",
        "v3_truth_q6_count",
        source_rows=residual_gate_ready,
    )
    residual_gate_q6_cells_p50 = pred_truth(
        "v3_resid_gate_q6_cells_p50",
        "v3_truth_q6_cells",
        source_rows=residual_gate_ready,
    )
    residual_gate_q6_cells_p90 = pred_truth(
        "v3_resid_gate_q6_cells_p90",
        "v3_truth_q6_cells",
        source_rows=residual_gate_ready,
    )
    residual_gate_q6_value_p50 = pred_truth(
        "v3_resid_gate_q6_value_p50",
        "v3_truth_q6_raw_value",
        source_rows=residual_gate_ready,
    )
    cal_formal_p50 = pred_truth(
        "v3_cal_formal_decision_value_p50",
        "v3_truth_formal_decision_value",
        source_rows=calibrated,
    )
    cal_formal_p90 = pred_truth(
        "v3_cal_formal_decision_value_p90",
        "v3_truth_formal_decision_value",
        source_rows=calibrated,
    )
    cal_q6_p50 = pred_truth(
        "v3_cal_q6_formal_decision_value_p50",
        "v3_truth_q6_formal_decision_value",
        source_rows=calibrated,
    )
    cal_q6_p90 = pred_truth(
        "v3_cal_q6_formal_decision_value_p90",
        "v3_truth_q6_formal_decision_value",
        source_rows=calibrated,
    )
    under_formal_p50 = pred_truth(
        "v3_under_formal_decision_value_p50",
        "v3_truth_formal_decision_value",
        source_rows=underestimate_ready,
    )
    under_formal_p90 = pred_truth(
        "v3_under_formal_decision_value_p90",
        "v3_truth_formal_decision_value",
        source_rows=underestimate_ready,
    )
    under_q6_p50 = pred_truth(
        "v3_under_q6_formal_decision_value_p50",
        "v3_truth_q6_formal_decision_value",
        source_rows=underestimate_ready,
    )
    under_q6_p90 = pred_truth(
        "v3_under_q6_formal_decision_value_p90",
        "v3_truth_q6_formal_decision_value",
        source_rows=underestimate_ready,
    )
    fv_formal_p50 = pred_truth(
        "v3_fv_formal_decision_value_p50",
        "v3_truth_formal_decision_value",
        source_rows=formal_value_ready,
    )
    fv_formal_p90 = pred_truth(
        "v3_fv_formal_decision_value_p90",
        "v3_truth_formal_decision_value",
        source_rows=formal_value_ready,
    )
    fv_q6_p50 = pred_truth(
        "v3_fv_q6_formal_decision_value_p50",
        "v3_truth_q6_formal_decision_value",
        source_rows=formal_value_ready,
    )
    fv_q6_p90 = pred_truth(
        "v3_fv_q6_formal_decision_value_p90",
        "v3_truth_q6_formal_decision_value",
        source_rows=formal_value_ready,
    )
    practical_formal_p50 = pred_truth(
        "v3_practical_formal_decision_value_p50",
        "v3_truth_formal_decision_value",
        source_rows=practical_ready,
    )
    practical_formal_p90 = pred_truth(
        "v3_practical_formal_decision_value_p90",
        "v3_truth_formal_decision_value",
        source_rows=practical_ready,
    )
    practical_q6_p50 = pred_truth(
        "v3_practical_q6_formal_decision_value_p50",
        "v3_truth_q6_formal_decision_value",
        source_rows=practical_ready,
    )
    practical_q6_p90 = pred_truth(
        "v3_practical_q6_formal_decision_value_p90",
        "v3_truth_q6_formal_decision_value",
        source_rows=practical_ready,
    )

    def mae(pairs: tuple[tuple[float, float], ...]) -> float | None:
        return _mean(abs(pred - truth) for pred, truth in pairs)

    def bias(pairs: tuple[tuple[float, float], ...]) -> float | None:
        return _mean(pred - truth for pred, truth in pairs)

    def below_rate(pairs: tuple[tuple[float, float], ...]) -> float | None:
        return _mean(1.0 if pred < truth else 0.0 for pred, truth in pairs)

    def over_rate(pairs: tuple[tuple[float, float], ...]) -> float | None:
        return _mean(1.0 if pred > truth else 0.0 for pred, truth in pairs)

    def coverage_rate(pairs: tuple[tuple[float, float], ...]) -> float | None:
        return _mean(1.0 if truth <= pred else 0.0 for pred, truth in pairs)

    def extreme_over_rate(pairs: tuple[tuple[float, float], ...]) -> float | None:
        return _mean(
            1.0
            if pred - truth > _normalized_error_denominator(truth)
            else 0.0
            for pred, truth in pairs
        )

    def pinball(pairs: tuple[tuple[float, float], ...], quantile: float) -> float | None:
        return _mean(_pinball_loss(truth, pred, quantile) for pred, truth in pairs)

    practical_raise_watch_eval: list[dict[str, bool]] = []
    for row in practical_ready:
        if row.get("v3_practical_recommendation") != "raise_watch":
            continue
        truth = _float_or_none(row.get("v3_truth_formal_decision_value"))
        baseline_p90 = _float_or_none(row.get("v3_post_formal_decision_value_p90"))
        practical_p90 = _float_or_none(
            row.get("v3_practical_formal_decision_value_p90")
        )
        if truth is None or baseline_p90 is None or practical_p90 is None:
            continue
        baseline_missed = baseline_p90 < truth
        practical_covered = practical_p90 >= truth
        false_alarm = not baseline_missed
        extreme_over = practical_p90 - truth > _normalized_error_denominator(truth)
        practical_raise_watch_eval.append(
            {
                "hit": baseline_missed and practical_covered,
                "miss": baseline_missed and not practical_covered,
                "false_alarm": false_alarm,
                "extreme_over": extreme_over,
                "misleading": false_alarm and extreme_over,
            }
        )

    def practical_raise_watch_rate(key: str) -> float | None:
        return _mean(
            1.0 if event[key] else 0.0
            for event in practical_raise_watch_eval
        )

    metric_scope_counts = Counter(
        str(row.get("v3_post_match_scope") or "none")
        for row in paired
        if row.get("v3_post_ready")
    )
    formal_p50_mae = mae(formal_p50)
    cal_formal_p50_mae = mae(cal_formal_p50)
    under_formal_p50_mae = mae(under_formal_p50)
    fv_formal_p50_mae = mae(fv_formal_p50)
    practical_formal_p50_mae = mae(practical_formal_p50)
    q6_formal_p50_mae = mae(q6_p50)
    cal_q6_formal_p50_mae = mae(cal_q6_p50)
    under_q6_formal_p50_mae = mae(under_q6_p50)
    fv_q6_formal_p50_mae = mae(fv_q6_p50)
    practical_q6_formal_p50_mae = mae(practical_q6_p50)
    q6_count_p50_mae = mae(q6_count_p50)
    q6_cells_p50_mae = mae(q6_cells_p50)
    q6_value_p50_mae = mae(q6_value_p50)
    ccv_q6_count_p50_mae = mae(ccv_q6_count_p50)
    ccv_q6_cells_p50_mae = mae(ccv_q6_cells_p50)
    ccvc_q6_count_p50_mae = mae(ccvc_q6_count_p50)
    ccvc_q6_cells_p50_mae = mae(ccvc_q6_cells_p50)
    ccvc_q6_value_p50_mae = mae(ccvc_q6_value_p50)
    residual_q6_count_p50_mae = mae(residual_q6_count_p50)
    residual_q6_cells_p50_mae = mae(residual_q6_cells_p50)
    residual_q6_value_p50_mae = mae(residual_q6_value_p50)
    residual_gate_q6_count_p50_mae = mae(residual_gate_q6_count_p50)
    residual_gate_q6_cells_p50_mae = mae(residual_gate_q6_cells_p50)
    residual_gate_q6_value_p50_mae = mae(residual_gate_q6_value_p50)
    return {
        "metric_rows": len(paired),
        "metric_strict_rows": sum(
            1 for row in paired if row.get("v3_post_match_scope") == "strict"
        ),
        "metric_summary_likelihood_rows": metric_scope_counts.get(
            "summary_likelihood",
            0,
        ),
        "metric_q6_projection_rows": metric_scope_counts.get("q6_projection", 0),
        "metric_fallback_rows": sum(
            1
            for row in paired
            if row.get("v3_post_match_scope") != "strict"
        ),
        "formal_p50_mae": _round_metric(formal_p50_mae),
        "formal_p50_mae_strict": _round_metric(mae(formal_p50_strict)),
        "formal_p50_mae_fallback": _round_metric(mae(formal_p50_fallback)),
        "formal_p50_bias": _round_metric(bias(formal_p50)),
        "formal_p50_below_rate": _round_metric(below_rate(formal_p50), 6),
        "formal_p50_over_rate": _round_metric(over_rate(formal_p50), 6),
        "formal_p50_pinball": _round_metric(pinball(formal_p50, 0.5)),
        "formal_p90_coverage": _round_metric(coverage_rate(formal_p90), 6),
        "formal_p90_extreme_over_rate": _round_metric(
            extreme_over_rate(formal_p90),
            6,
        ),
        "formal_p90_coverage_strict": _round_metric(
            coverage_rate(formal_p90_strict),
            6,
        ),
        "formal_p90_coverage_fallback": _round_metric(
            coverage_rate(formal_p90_fallback),
            6,
        ),
        "formal_p90_pinball": _round_metric(pinball(formal_p90, 0.9)),
        "q6_formal_p50_mae": _round_metric(q6_formal_p50_mae),
        "q6_formal_p50_mae_strict": _round_metric(mae(q6_p50_strict)),
        "q6_formal_p50_mae_fallback": _round_metric(mae(q6_p50_fallback)),
        "q6_formal_p50_bias": _round_metric(bias(q6_p50)),
        "q6_formal_p50_below_rate": _round_metric(below_rate(q6_p50), 6),
        "q6_formal_p50_over_rate": _round_metric(over_rate(q6_p50), 6),
        "q6_formal_p90_coverage": _round_metric(coverage_rate(q6_p90), 6),
        "q6_formal_p90_extreme_over_rate": _round_metric(
            extreme_over_rate(q6_p90),
            6,
        ),
        "q6_formal_p90_pinball": _round_metric(pinball(q6_p90, 0.9)),
        "q6_count_p50_mae": _round_metric(q6_count_p50_mae),
        "q6_count_p50_bias": _round_metric(bias(q6_count_p50)),
        "q6_count_p90_coverage": _round_metric(coverage_rate(q6_count_p90), 6),
        "q6_cells_p50_mae": _round_metric(q6_cells_p50_mae),
        "q6_cells_p50_bias": _round_metric(bias(q6_cells_p50)),
        "q6_cells_p90_coverage": _round_metric(coverage_rate(q6_cells_p90), 6),
        "q6_value_p50_mae": _round_metric(q6_value_p50_mae),
        "q6_value_p50_bias": _round_metric(bias(q6_value_p50)),
        "v3_ccv_metric_rows": len(ccv_ready),
        "v3_ccv_likelihood_rows": sum(
            1 for row in ccv_ready if row.get("v3_ccv_match_scope") == "ccv_likelihood"
        ),
        "v3_ccv_q6_count_p50_mae": _round_metric(ccv_q6_count_p50_mae),
        "v3_ccv_q6_count_p50_bias": _round_metric(bias(ccv_q6_count_p50)),
        "v3_ccv_q6_count_p90_coverage": _round_metric(
            coverage_rate(ccv_q6_count_p90),
            6,
        ),
        "v3_ccv_delta_q6_count_p50_mae": _round_metric(
            ccv_q6_count_p50_mae - q6_count_p50_mae
            if ccv_q6_count_p50_mae is not None and q6_count_p50_mae is not None
            else None
        ),
        "v3_ccv_q6_cells_p50_mae": _round_metric(ccv_q6_cells_p50_mae),
        "v3_ccv_q6_cells_p50_bias": _round_metric(bias(ccv_q6_cells_p50)),
        "v3_ccv_q6_cells_p90_coverage": _round_metric(
            coverage_rate(ccv_q6_cells_p90),
            6,
        ),
        "v3_ccv_delta_q6_cells_p50_mae": _round_metric(
            ccv_q6_cells_p50_mae - q6_cells_p50_mae
            if ccv_q6_cells_p50_mae is not None and q6_cells_p50_mae is not None
            else None
        ),
        "v3_ccvc_metric_rows": len(ccvc_ready),
        "v3_ccvc_component_likelihood_rows": sum(
            1
            for row in ccvc_ready
            if row.get("v3_ccvc_match_scope") == "ccv_component_likelihood"
        ),
        "v3_ccvc_q6_count_p50_mae": _round_metric(ccvc_q6_count_p50_mae),
        "v3_ccvc_q6_count_p50_bias": _round_metric(bias(ccvc_q6_count_p50)),
        "v3_ccvc_q6_count_p50_below_rate": _round_metric(
            below_rate(ccvc_q6_count_p50),
            6,
        ),
        "v3_ccvc_q6_count_p90_coverage": _round_metric(
            coverage_rate(ccvc_q6_count_p90),
            6,
        ),
        "v3_ccvc_delta_q6_count_p50_mae": _round_metric(
            ccvc_q6_count_p50_mae - q6_count_p50_mae
            if ccvc_q6_count_p50_mae is not None and q6_count_p50_mae is not None
            else None
        ),
        "v3_ccvc_q6_cells_p50_mae": _round_metric(ccvc_q6_cells_p50_mae),
        "v3_ccvc_q6_cells_p50_bias": _round_metric(bias(ccvc_q6_cells_p50)),
        "v3_ccvc_q6_cells_p50_below_rate": _round_metric(
            below_rate(ccvc_q6_cells_p50),
            6,
        ),
        "v3_ccvc_q6_cells_p90_coverage": _round_metric(
            coverage_rate(ccvc_q6_cells_p90),
            6,
        ),
        "v3_ccvc_delta_q6_cells_p50_mae": _round_metric(
            ccvc_q6_cells_p50_mae - q6_cells_p50_mae
            if ccvc_q6_cells_p50_mae is not None and q6_cells_p50_mae is not None
            else None
        ),
        "v3_ccvc_q6_value_p50_mae": _round_metric(
            ccvc_q6_value_p50_mae,
            1,
        ),
        "v3_ccvc_q6_value_p50_bias": _round_metric(
            bias(ccvc_q6_value_p50),
            1,
        ),
        "v3_ccvc_delta_q6_value_p50_mae": _round_metric(
            ccvc_q6_value_p50_mae - q6_value_p50_mae
            if ccvc_q6_value_p50_mae is not None and q6_value_p50_mae is not None
            else None,
            1,
        ),
        "v3_resid_metric_rows": len(residual_ready),
        "v3_resid_likelihood_rows": sum(
            1
            for row in residual_ready
            if row.get("v3_resid_match_scope") == "residual_likelihood"
        ),
        "v3_resid_q6_count_p50_mae": _round_metric(residual_q6_count_p50_mae),
        "v3_resid_q6_count_p50_bias": _round_metric(bias(residual_q6_count_p50)),
        "v3_resid_q6_count_p90_coverage": _round_metric(
            coverage_rate(residual_q6_count_p90),
            6,
        ),
        "v3_resid_delta_q6_count_p50_mae": _round_metric(
            residual_q6_count_p50_mae - q6_count_p50_mae
            if residual_q6_count_p50_mae is not None and q6_count_p50_mae is not None
            else None
        ),
        "v3_resid_q6_cells_p50_mae": _round_metric(residual_q6_cells_p50_mae),
        "v3_resid_q6_cells_p50_bias": _round_metric(bias(residual_q6_cells_p50)),
        "v3_resid_q6_cells_p90_coverage": _round_metric(
            coverage_rate(residual_q6_cells_p90),
            6,
        ),
        "v3_resid_delta_q6_cells_p50_mae": _round_metric(
            residual_q6_cells_p50_mae - q6_cells_p50_mae
            if residual_q6_cells_p50_mae is not None and q6_cells_p50_mae is not None
            else None
        ),
        "v3_resid_q6_value_p50_mae": _round_metric(residual_q6_value_p50_mae),
        "v3_resid_q6_value_p50_bias": _round_metric(bias(residual_q6_value_p50)),
        "v3_resid_delta_q6_value_p50_mae": _round_metric(
            residual_q6_value_p50_mae - q6_value_p50_mae
            if residual_q6_value_p50_mae is not None and q6_value_p50_mae is not None
            else None
        ),
        "v3_resid_gate_metric_rows": len(residual_gate_ready),
        "v3_resid_gate_active_rows": sum(
            1 for row in residual_gate_ready if row.get("v3_resid_gate_active")
        ),
        "v3_resid_gate_q6_count_p50_mae": _round_metric(
            residual_gate_q6_count_p50_mae
        ),
        "v3_resid_gate_q6_count_p50_bias": _round_metric(
            bias(residual_gate_q6_count_p50)
        ),
        "v3_resid_gate_q6_count_p90_coverage": _round_metric(
            coverage_rate(residual_gate_q6_count_p90),
            6,
        ),
        "v3_resid_gate_delta_q6_count_p50_mae": _round_metric(
            residual_gate_q6_count_p50_mae - q6_count_p50_mae
            if residual_gate_q6_count_p50_mae is not None
            and q6_count_p50_mae is not None
            else None
        ),
        "v3_resid_gate_q6_cells_p50_mae": _round_metric(
            residual_gate_q6_cells_p50_mae
        ),
        "v3_resid_gate_q6_cells_p50_bias": _round_metric(
            bias(residual_gate_q6_cells_p50)
        ),
        "v3_resid_gate_q6_cells_p90_coverage": _round_metric(
            coverage_rate(residual_gate_q6_cells_p90),
            6,
        ),
        "v3_resid_gate_delta_q6_cells_p50_mae": _round_metric(
            residual_gate_q6_cells_p50_mae - q6_cells_p50_mae
            if residual_gate_q6_cells_p50_mae is not None
            and q6_cells_p50_mae is not None
            else None
        ),
        "v3_resid_gate_q6_value_p50_mae": _round_metric(
            residual_gate_q6_value_p50_mae
        ),
        "v3_resid_gate_q6_value_p50_bias": _round_metric(
            bias(residual_gate_q6_value_p50)
        ),
        "v3_resid_gate_delta_q6_value_p50_mae": _round_metric(
            residual_gate_q6_value_p50_mae - q6_value_p50_mae
            if residual_gate_q6_value_p50_mae is not None
            and q6_value_p50_mae is not None
            else None
        ),
        "v3_cal_metric_rows": len(calibrated),
        "v3_cal_active_rows": sum(1 for row in calibrated if row.get("v3_cal_active")),
        "v3_cal_formal_p50_mae": _round_metric(cal_formal_p50_mae),
        "v3_cal_formal_p50_bias": _round_metric(bias(cal_formal_p50)),
        "v3_cal_formal_p50_below_rate": _round_metric(
            below_rate(cal_formal_p50),
            6,
        ),
        "v3_cal_formal_p50_over_rate": _round_metric(
            over_rate(cal_formal_p50),
            6,
        ),
        "v3_cal_formal_p50_pinball": _round_metric(pinball(cal_formal_p50, 0.5)),
        "v3_cal_formal_p90_coverage": _round_metric(
            coverage_rate(cal_formal_p90),
            6,
        ),
        "v3_cal_formal_p90_pinball": _round_metric(pinball(cal_formal_p90, 0.9)),
        "v3_cal_q6_formal_p50_mae": _round_metric(cal_q6_formal_p50_mae),
        "v3_cal_q6_formal_p50_bias": _round_metric(bias(cal_q6_p50)),
        "v3_cal_q6_formal_p50_below_rate": _round_metric(
            below_rate(cal_q6_p50),
            6,
        ),
        "v3_cal_q6_formal_p50_over_rate": _round_metric(
            over_rate(cal_q6_p50),
            6,
        ),
        "v3_cal_q6_formal_p90_coverage": _round_metric(
            coverage_rate(cal_q6_p90),
            6,
        ),
        "v3_cal_delta_formal_p50_mae": _round_metric(
            cal_formal_p50_mae - formal_p50_mae
            if cal_formal_p50_mae is not None and formal_p50_mae is not None
            else None
        ),
        "v3_cal_delta_q6_formal_p50_mae": _round_metric(
            cal_q6_formal_p50_mae - q6_formal_p50_mae
            if cal_q6_formal_p50_mae is not None
            and q6_formal_p50_mae is not None
            else None
        ),
        "v3_under_metric_rows": len(underestimate_ready),
        "v3_under_candidate_rows": sum(
            1 for row in underestimate_ready if row.get("v3_under_candidate")
        ),
        "v3_under_active_rows": sum(
            1 for row in underestimate_ready if row.get("v3_under_active")
        ),
        "v3_under_formal_p50_mae": _round_metric(under_formal_p50_mae),
        "v3_under_formal_p50_bias": _round_metric(bias(under_formal_p50)),
        "v3_under_formal_p50_below_rate": _round_metric(
            below_rate(under_formal_p50),
            6,
        ),
        "v3_under_formal_p50_over_rate": _round_metric(
            over_rate(under_formal_p50),
            6,
        ),
        "v3_under_formal_p50_pinball": _round_metric(
            pinball(under_formal_p50, 0.5)
        ),
        "v3_under_formal_p90_coverage": _round_metric(
            coverage_rate(under_formal_p90),
            6,
        ),
        "v3_under_formal_p90_pinball": _round_metric(
            pinball(under_formal_p90, 0.9)
        ),
        "v3_under_q6_formal_p50_mae": _round_metric(under_q6_formal_p50_mae),
        "v3_under_q6_formal_p50_bias": _round_metric(bias(under_q6_p50)),
        "v3_under_q6_formal_p50_below_rate": _round_metric(
            below_rate(under_q6_p50),
            6,
        ),
        "v3_under_q6_formal_p90_coverage": _round_metric(
            coverage_rate(under_q6_p90),
            6,
        ),
        "v3_under_delta_formal_p50_mae": _round_metric(
            under_formal_p50_mae - formal_p50_mae
            if under_formal_p50_mae is not None and formal_p50_mae is not None
            else None
        ),
        "v3_under_delta_q6_formal_p50_mae": _round_metric(
            under_q6_formal_p50_mae - q6_formal_p50_mae
            if under_q6_formal_p50_mae is not None
            and q6_formal_p50_mae is not None
            else None
        ),
        "v3_tail_review_metric_rows": len(tail_review_ready),
        "v3_tail_review_candidate_rows": sum(
            1 for row in tail_review_ready if row.get("v3_tail_review_candidate")
        ),
        "v3_tail_review_hurt_guard_rows": sum(
            1 for row in tail_review_ready if row.get("v3_tail_review_hurt_guard")
        ),
        "v3_tail_review_active_rows": sum(
            1 for row in tail_review_ready if row.get("v3_tail_review_active")
        ),
        "v3_fv_metric_rows": len(formal_value_ready),
        "v3_fv_candidate_rows": sum(
            1 for row in formal_value_ready if row.get("v3_fv_candidate")
        ),
        "v3_fv_active_rows": sum(
            1 for row in formal_value_ready if row.get("v3_fv_active")
        ),
        "v3_fv_capacity_watch_rows": sum(
            1
            for row in formal_value_ready
            if "capacity_cells_drift" in str(row.get("v3_fv_stress_class") or "")
        ),
        "v3_fv_q6_cells_watch_rows": sum(
            1
            for row in formal_value_ready
            if "q6_cells_floor_stress" in str(row.get("v3_fv_stress_class") or "")
        ),
        "v3_fv_value_floor_candidate_rows": sum(
            1
            for row in formal_value_ready
            if "value_floor_stress" in str(row.get("v3_fv_stress_class") or "")
        ),
        "v3_fv_formal_p50_mae": _round_metric(fv_formal_p50_mae),
        "v3_fv_formal_p50_bias": _round_metric(bias(fv_formal_p50)),
        "v3_fv_formal_p50_below_rate": _round_metric(
            below_rate(fv_formal_p50),
            6,
        ),
        "v3_fv_formal_p50_over_rate": _round_metric(
            over_rate(fv_formal_p50),
            6,
        ),
        "v3_fv_formal_p50_pinball": _round_metric(pinball(fv_formal_p50, 0.5)),
        "v3_fv_formal_p90_coverage": _round_metric(
            coverage_rate(fv_formal_p90),
            6,
        ),
        "v3_fv_formal_p90_pinball": _round_metric(pinball(fv_formal_p90, 0.9)),
        "v3_fv_q6_formal_p50_mae": _round_metric(fv_q6_formal_p50_mae),
        "v3_fv_q6_formal_p50_bias": _round_metric(bias(fv_q6_p50)),
        "v3_fv_q6_formal_p50_below_rate": _round_metric(
            below_rate(fv_q6_p50),
            6,
        ),
        "v3_fv_q6_formal_p90_coverage": _round_metric(
            coverage_rate(fv_q6_p90),
            6,
        ),
        "v3_fv_delta_formal_p50_mae": _round_metric(
            fv_formal_p50_mae - formal_p50_mae
            if fv_formal_p50_mae is not None and formal_p50_mae is not None
            else None
        ),
        "v3_fv_delta_q6_formal_p50_mae": _round_metric(
            fv_q6_formal_p50_mae - q6_formal_p50_mae
            if fv_q6_formal_p50_mae is not None
            and q6_formal_p50_mae is not None
            else None
        ),
        "v3_scp_ready_rows": len(settlement_count_prior_ready),
        "v3_scp_candidate_rows": sum(
            1 for row in settlement_count_prior_ready if row.get("v3_scp_candidate")
        ),
        "v3_scp_missing_table_rows": sum(
            1 for row in settlement_count_prior_ready if row.get("v3_scp_missing_table")
        ),
        "v3_scp_active_rows": sum(
            1 for row in settlement_count_prior_ready if row.get("v3_scp_active")
        ),
        "v3_scp_status_counts": dict(
            sorted(
                Counter(
                    str(row.get("v3_scp_status") or "none")
                    for row in rows
                ).items()
            )
        ),
        "v3_cse_ready_rows": len(capacity_source_expansion_ready),
        "v3_cse_candidate_rows": sum(
            1
            for row in capacity_source_expansion_ready
            if row.get("v3_cse_candidate")
        ),
        "v3_cse_pressure_candidate_rows": sum(
            1
            for row in capacity_source_expansion_ready
            if row.get("v3_cse_pressure_candidate")
        ),
        "v3_cse_active_rows": sum(
            1 for row in capacity_source_expansion_ready if row.get("v3_cse_active")
        ),
        "v3_cse_status_counts": dict(
            sorted(
                Counter(
                    str(row.get("v3_cse_status") or "none")
                    for row in rows
                ).items()
            )
        ),
        "v3_practical_metric_rows": len(practical_ready),
        "v3_practical_candidate_rows": sum(
            1 for row in practical_ready if row.get("v3_practical_candidate")
        ),
        "v3_practical_active_rows": sum(
            1 for row in practical_ready if row.get("v3_practical_active")
        ),
        "v3_practical_raise_watch_rows": sum(
            1
            for row in practical_ready
            if row.get("v3_practical_recommendation") == "raise_watch"
        ),
        "v3_practical_raise_watch_evaluable_rows": len(
            practical_raise_watch_eval
        ),
        "v3_practical_raise_watch_hit_rows": sum(
            1 for event in practical_raise_watch_eval if event["hit"]
        ),
        "v3_practical_raise_watch_miss_rows": sum(
            1 for event in practical_raise_watch_eval if event["miss"]
        ),
        "v3_practical_raise_watch_false_alarm_rows": sum(
            1 for event in practical_raise_watch_eval if event["false_alarm"]
        ),
        "v3_practical_raise_watch_extreme_over_rows": sum(
            1 for event in practical_raise_watch_eval if event["extreme_over"]
        ),
        "v3_practical_raise_watch_misleading_rows": sum(
            1 for event in practical_raise_watch_eval if event["misleading"]
        ),
        "v3_practical_raise_watch_hit_rate": _round_metric(
            practical_raise_watch_rate("hit"),
            6,
        ),
        "v3_practical_raise_watch_miss_rate": _round_metric(
            practical_raise_watch_rate("miss"),
            6,
        ),
        "v3_practical_raise_watch_false_alarm_rate": _round_metric(
            practical_raise_watch_rate("false_alarm"),
            6,
        ),
        "v3_practical_raise_watch_extreme_over_rate": _round_metric(
            practical_raise_watch_rate("extreme_over"),
            6,
        ),
        "v3_practical_raise_watch_misleading_rate": _round_metric(
            practical_raise_watch_rate("misleading"),
            6,
        ),
        "v3_practical_status_counts": dict(
            sorted(
                Counter(
                    str(row.get("v3_practical_status") or "none")
                    for row in rows
                ).items()
            )
        ),
        "v3_practical_mode_counts": dict(
            sorted(
                Counter(
                    str(row.get("v3_practical_mode") or "none")
                    for row in practical_ready
                ).items()
            )
        ),
        "v3_practical_formal_p50_mae": _round_metric(
            practical_formal_p50_mae
        ),
        "v3_practical_formal_p50_bias": _round_metric(
            bias(practical_formal_p50)
        ),
        "v3_practical_formal_p50_below_rate": _round_metric(
            below_rate(practical_formal_p50),
            6,
        ),
        "v3_practical_formal_p50_over_rate": _round_metric(
            over_rate(practical_formal_p50),
            6,
        ),
        "v3_practical_formal_p50_pinball": _round_metric(
            pinball(practical_formal_p50, 0.5)
        ),
        "v3_practical_formal_p90_coverage": _round_metric(
            coverage_rate(practical_formal_p90),
            6,
        ),
        "v3_practical_formal_p90_extreme_over_rate": _round_metric(
            extreme_over_rate(practical_formal_p90),
            6,
        ),
        "v3_practical_formal_p90_pinball": _round_metric(
            pinball(practical_formal_p90, 0.9)
        ),
        "v3_practical_q6_formal_p50_mae": _round_metric(
            practical_q6_formal_p50_mae
        ),
        "v3_practical_q6_formal_p50_bias": _round_metric(
            bias(practical_q6_p50)
        ),
        "v3_practical_q6_formal_p50_below_rate": _round_metric(
            below_rate(practical_q6_p50),
            6,
        ),
        "v3_practical_q6_formal_p90_coverage": _round_metric(
            coverage_rate(practical_q6_p90),
            6,
        ),
        "v3_practical_q6_formal_p90_extreme_over_rate": _round_metric(
            extreme_over_rate(practical_q6_p90),
            6,
        ),
        "v3_practical_delta_formal_p50_mae": _round_metric(
            practical_formal_p50_mae - formal_p50_mae
            if practical_formal_p50_mae is not None and formal_p50_mae is not None
            else None
        ),
        "v3_practical_delta_q6_formal_p50_mae": _round_metric(
            practical_q6_formal_p50_mae - q6_formal_p50_mae
            if practical_q6_formal_p50_mae is not None
            and q6_formal_p50_mae is not None
            else None
        ),
    }


def summarize_rows(rows: list[dict[str, Any]], errors: list[dict[str, str]]) -> dict[str, Any]:
    statuses = Counter(str(row.get("status") or "unknown") for row in rows)
    round_counts = Counter(f"R{row.get('round')}" for row in rows)
    hero_counts = Counter(str(row.get("hero") or "unknown") for row in rows)
    evidence_stage_counts = Counter(
        str(row.get("evidence_stage") or "unknown") for row in rows
    )
    information_density_counts = Counter(
        str(row.get("information_density_band") or "unknown") for row in rows
    )
    evidence_profile_counts = Counter(
        str(row.get("evidence_profile_key") or "unknown") for row in rows
    )
    posterior_scope_counts = Counter(
        str(row.get("v3_post_match_scope") or "none")
        for row in rows
        if row.get("v3_post_ready")
    )
    robust_status_counts = Counter(
        str(row.get("v3_robust_status") or "none")
        for row in rows
    )
    residual_target_derived_field_counts: Counter[str] = Counter()
    for row in rows:
        if not row.get("v3_rtc_available"):
            continue
        for field in str(row.get("v3_rtc_derived_fields") or "").split(","):
            if field:
                residual_target_derived_field_counts[field] += 1
    residual_target_count_status_counts = Counter(
        str(row.get("v3_rtc_q6_count_status") or "none")
        for row in rows
        if row.get("v3_rtc_available")
    )
    residual_target_cells_status_counts = Counter(
        str(row.get("v3_rtc_q6_cells_status") or "none")
        for row in rows
        if row.get("v3_rtc_available")
    )
    residual_target_value_status_counts = Counter(
        str(row.get("v3_rtc_q6_value_status") or "none")
        for row in rows
        if row.get("v3_rtc_available")
    )
    ready_rows = [row for row in rows if row.get("status") == "ready"]
    summary = {
        "windows": len(rows),
        "ready": statuses.get("ready", 0),
        "no_state": statuses.get("no_state", 0),
        "constraint_conflict": statuses.get("constraint_conflict", 0),
        "parse_errors": len(errors),
        "prior_ready": sum(1 for row in rows if row.get("v3_prior_available")),
        "robust_prior_usable": sum(
            1 for row in rows if row.get("v3_robust_prior_usable")
        ),
        "robust_prior_trusted": sum(
            1 for row in rows if row.get("v3_robust_prior_trusted")
        ),
        "robust_activity_candidate": sum(
            1 for row in rows if row.get("v3_robust_activity_candidate")
        ),
        "robust_prior_stressed": sum(
            1
            for row in rows
            if int(row.get("v3_robust_prior_stress_score") or 0) > 0
        ),
        "truth_ready": sum(1 for row in rows if row.get("v3_truth_available")),
        "decision_truth_ready": sum(
            1 for row in rows if row.get("v3_truth_decision_available")
        ),
        "summary_ready": sum(1 for row in rows if row.get("v3_summary_available")),
        "summary_conflict": sum(
            1
            for row in rows
            if int(row.get("v3_summary_conflict_count") or 0) > 0
        ),
        "posterior_ready": sum(1 for row in rows if row.get("v3_post_ready")),
        "posterior_strict_ready": sum(
            1 for row in rows if row.get("v3_post_strict_ready")
        ),
        "posterior_summary_likelihood": posterior_scope_counts.get(
            "summary_likelihood",
            0,
        ),
        "posterior_q6_projection": posterior_scope_counts.get("q6_projection", 0),
        "posterior_fallback": sum(
            1
            for row in rows
            if row.get("v3_post_ready")
            and str(row.get("v3_post_match_scope") or "") != "strict"
        ),
        "posterior_no_match": sum(
            1
            for row in rows
            if row.get("v3_post_available")
            and not row.get("v3_post_ready")
        ),
        "status_counts": dict(sorted(statuses.items())),
        "round_counts": dict(sorted(round_counts.items())),
        "hero_counts": dict(sorted(hero_counts.items())),
        "evidence_stage_counts": dict(sorted(evidence_stage_counts.items())),
        "information_density_counts": dict(sorted(information_density_counts.items())),
        "evidence_profile_counts": dict(sorted(evidence_profile_counts.items())),
        "posterior_scope_counts": dict(sorted(posterior_scope_counts.items())),
        "robust_status_counts": dict(sorted(robust_status_counts.items())),
        "v3_rtc_ready_rows": sum(1 for row in rows if row.get("v3_rtc_ready")),
        "v3_rtc_candidate_rows": sum(
            1 for row in rows if row.get("v3_rtc_candidate")
        ),
        "v3_rtc_active_rows": sum(1 for row in rows if row.get("v3_rtc_active")),
        "v3_rtc_derived_field_counts": dict(
            sorted(residual_target_derived_field_counts.items())
        ),
        "v3_rtc_q6_count_status_counts": dict(
            sorted(residual_target_count_status_counts.items())
        ),
        "v3_rtc_q6_cells_status_counts": dict(
            sorted(residual_target_cells_status_counts.items())
        ),
        "v3_rtc_q6_value_status_counts": dict(
            sorted(residual_target_value_status_counts.items())
        ),
        "numeric_constraints": sum(int(row.get("numeric_constraints") or 0) for row in ready_rows),
        "item_anchors": sum(int(row.get("item_anchors") or 0) for row in ready_rows),
        "shape_anchors": sum(int(row.get("shape_anchors") or 0) for row in ready_rows),
        "quality_floor_anchors": sum(int(row.get("quality_floor_anchors") or 0) for row in ready_rows),
        "errors": errors,
        "constraint_ok": statuses.get("constraint_conflict", 0) == 0,
        "parse_ok": not errors,
    }
    summary.update(_paired_metric_summary(rows))
    return summary


def _print_summary(summary: dict[str, Any]) -> None:
    print(
        " ".join(
            (
                f"windows={summary['windows']}",
                f"ready={summary['ready']}",
                f"no_state={summary['no_state']}",
                f"constraint_conflict={summary['constraint_conflict']}",
                f"parse_errors={summary['parse_errors']}",
                f"prior_ready={summary['prior_ready']}",
                f"robust_prior_usable={summary['robust_prior_usable']}",
                f"robust_prior_trusted={summary['robust_prior_trusted']}",
                f"robust_activity_candidate={summary['robust_activity_candidate']}",
                f"robust_prior_stressed={summary['robust_prior_stressed']}",
                f"truth_ready={summary['truth_ready']}",
                f"decision_truth_ready={summary['decision_truth_ready']}",
                f"summary_ready={summary['summary_ready']}",
                f"summary_conflict={summary['summary_conflict']}",
                f"posterior_ready={summary['posterior_ready']}",
                f"posterior_strict_ready={summary['posterior_strict_ready']}",
                f"posterior_summary_likelihood={summary['posterior_summary_likelihood']}",
                f"posterior_q6_projection={summary['posterior_q6_projection']}",
                f"posterior_fallback={summary['posterior_fallback']}",
                f"posterior_no_match={summary['posterior_no_match']}",
                f"metric_rows={summary['metric_rows']}",
                f"formal_p50_mae={summary['formal_p50_mae']}",
                f"formal_p50_mae_strict={summary['formal_p50_mae_strict']}",
                f"formal_p50_mae_fallback={summary['formal_p50_mae_fallback']}",
                f"formal_p50_below_rate={summary['formal_p50_below_rate']}",
                f"formal_p50_over_rate={summary['formal_p50_over_rate']}",
                f"formal_p90_coverage={summary['formal_p90_coverage']}",
                f"formal_p90_extreme_over_rate={summary['formal_p90_extreme_over_rate']}",
                f"q6_formal_p50_mae={summary['q6_formal_p50_mae']}",
                f"q6_formal_p50_mae_strict={summary['q6_formal_p50_mae_strict']}",
                f"q6_formal_p50_mae_fallback={summary['q6_formal_p50_mae_fallback']}",
                f"q6_formal_p50_below_rate={summary['q6_formal_p50_below_rate']}",
                f"q6_formal_p50_over_rate={summary['q6_formal_p50_over_rate']}",
                f"q6_formal_p90_extreme_over_rate={summary['q6_formal_p90_extreme_over_rate']}",
                f"q6_count_p50_mae={summary['q6_count_p50_mae']}",
                f"q6_cells_p50_mae={summary['q6_cells_p50_mae']}",
                f"v3_ccv_likelihood_rows={summary['v3_ccv_likelihood_rows']}",
                f"v3_ccv_q6_count_p50_mae={summary['v3_ccv_q6_count_p50_mae']}",
                f"v3_ccv_delta_q6_count_p50_mae={summary['v3_ccv_delta_q6_count_p50_mae']}",
                f"v3_ccv_q6_cells_p50_mae={summary['v3_ccv_q6_cells_p50_mae']}",
                f"v3_ccv_delta_q6_cells_p50_mae={summary['v3_ccv_delta_q6_cells_p50_mae']}",
                f"v3_ccvc_component_likelihood_rows={summary['v3_ccvc_component_likelihood_rows']}",
                f"v3_ccvc_q6_count_p50_mae={summary['v3_ccvc_q6_count_p50_mae']}",
                f"v3_ccvc_delta_q6_count_p50_mae={summary['v3_ccvc_delta_q6_count_p50_mae']}",
                f"v3_ccvc_q6_cells_p50_mae={summary['v3_ccvc_q6_cells_p50_mae']}",
                f"v3_ccvc_delta_q6_cells_p50_mae={summary['v3_ccvc_delta_q6_cells_p50_mae']}",
                f"v3_ccvc_q6_value_p50_mae={summary['v3_ccvc_q6_value_p50_mae']}",
                f"v3_ccvc_delta_q6_value_p50_mae={summary['v3_ccvc_delta_q6_value_p50_mae']}",
                f"v3_resid_likelihood_rows={summary['v3_resid_likelihood_rows']}",
                f"v3_resid_q6_count_p50_mae={summary['v3_resid_q6_count_p50_mae']}",
                f"v3_resid_delta_q6_count_p50_mae={summary['v3_resid_delta_q6_count_p50_mae']}",
                f"v3_resid_q6_cells_p50_mae={summary['v3_resid_q6_cells_p50_mae']}",
                f"v3_resid_delta_q6_cells_p50_mae={summary['v3_resid_delta_q6_cells_p50_mae']}",
                f"v3_resid_q6_value_p50_mae={summary['v3_resid_q6_value_p50_mae']}",
                f"v3_resid_delta_q6_value_p50_mae={summary['v3_resid_delta_q6_value_p50_mae']}",
                f"v3_resid_gate_active_rows={summary['v3_resid_gate_active_rows']}",
                f"v3_resid_gate_q6_count_p50_mae={summary['v3_resid_gate_q6_count_p50_mae']}",
                f"v3_resid_gate_delta_q6_count_p50_mae={summary['v3_resid_gate_delta_q6_count_p50_mae']}",
                f"v3_resid_gate_q6_cells_p50_mae={summary['v3_resid_gate_q6_cells_p50_mae']}",
                f"v3_resid_gate_delta_q6_cells_p50_mae={summary['v3_resid_gate_delta_q6_cells_p50_mae']}",
                f"v3_resid_gate_q6_value_p50_mae={summary['v3_resid_gate_q6_value_p50_mae']}",
                f"v3_resid_gate_delta_q6_value_p50_mae={summary['v3_resid_gate_delta_q6_value_p50_mae']}",
                f"v3_rtc_candidate_rows={summary['v3_rtc_candidate_rows']}",
                f"v3_rtc_active_rows={summary['v3_rtc_active_rows']}",
                f"v3_cal_active_rows={summary['v3_cal_active_rows']}",
                f"v3_cal_formal_p50_mae={summary['v3_cal_formal_p50_mae']}",
                f"v3_cal_delta_formal_p50_mae={summary['v3_cal_delta_formal_p50_mae']}",
                f"v3_cal_formal_p50_below_rate={summary['v3_cal_formal_p50_below_rate']}",
                f"v3_cal_formal_p50_over_rate={summary['v3_cal_formal_p50_over_rate']}",
                f"v3_cal_formal_p90_coverage={summary['v3_cal_formal_p90_coverage']}",
                f"v3_under_candidate_rows={summary['v3_under_candidate_rows']}",
                f"v3_under_formal_p50_mae={summary['v3_under_formal_p50_mae']}",
                f"v3_under_delta_formal_p50_mae={summary['v3_under_delta_formal_p50_mae']}",
                f"v3_under_formal_p50_below_rate={summary['v3_under_formal_p50_below_rate']}",
                f"v3_under_formal_p50_over_rate={summary['v3_under_formal_p50_over_rate']}",
                f"v3_under_formal_p90_coverage={summary['v3_under_formal_p90_coverage']}",
                f"v3_tail_review_candidate_rows={summary['v3_tail_review_candidate_rows']}",
                f"v3_tail_review_hurt_guard_rows={summary['v3_tail_review_hurt_guard_rows']}",
                f"v3_tail_review_active_rows={summary['v3_tail_review_active_rows']}",
                f"v3_fv_candidate_rows={summary['v3_fv_candidate_rows']}",
                f"v3_fv_capacity_watch_rows={summary['v3_fv_capacity_watch_rows']}",
                f"v3_fv_value_floor_candidate_rows={summary['v3_fv_value_floor_candidate_rows']}",
                f"v3_fv_formal_p50_mae={summary['v3_fv_formal_p50_mae']}",
                f"v3_fv_delta_formal_p50_mae={summary['v3_fv_delta_formal_p50_mae']}",
                f"v3_fv_formal_p50_below_rate={summary['v3_fv_formal_p50_below_rate']}",
                f"v3_fv_formal_p50_over_rate={summary['v3_fv_formal_p50_over_rate']}",
                f"v3_fv_formal_p90_coverage={summary['v3_fv_formal_p90_coverage']}",
                f"v3_scp_ready_rows={summary['v3_scp_ready_rows']}",
                f"v3_scp_candidate_rows={summary['v3_scp_candidate_rows']}",
                f"v3_scp_missing_table_rows={summary['v3_scp_missing_table_rows']}",
                f"v3_scp_active_rows={summary['v3_scp_active_rows']}",
                f"v3_cse_ready_rows={summary['v3_cse_ready_rows']}",
                f"v3_cse_candidate_rows={summary['v3_cse_candidate_rows']}",
                f"v3_cse_pressure_candidate_rows={summary['v3_cse_pressure_candidate_rows']}",
                f"v3_cse_active_rows={summary['v3_cse_active_rows']}",
                f"v3_practical_candidate_rows={summary['v3_practical_candidate_rows']}",
                f"v3_practical_raise_watch_rows={summary['v3_practical_raise_watch_rows']}",
                f"v3_practical_raise_watch_evaluable_rows={summary['v3_practical_raise_watch_evaluable_rows']}",
                f"v3_practical_raise_watch_hit_rate={summary['v3_practical_raise_watch_hit_rate']}",
                f"v3_practical_raise_watch_miss_rate={summary['v3_practical_raise_watch_miss_rate']}",
                f"v3_practical_raise_watch_false_alarm_rate={summary['v3_practical_raise_watch_false_alarm_rate']}",
                f"v3_practical_raise_watch_extreme_over_rate={summary['v3_practical_raise_watch_extreme_over_rate']}",
                f"v3_practical_raise_watch_misleading_rate={summary['v3_practical_raise_watch_misleading_rate']}",
                f"v3_practical_active_rows={summary['v3_practical_active_rows']}",
                f"v3_practical_formal_p50_mae={summary['v3_practical_formal_p50_mae']}",
                f"v3_practical_delta_formal_p50_mae={summary['v3_practical_delta_formal_p50_mae']}",
                f"v3_practical_formal_p50_below_rate={summary['v3_practical_formal_p50_below_rate']}",
                f"v3_practical_formal_p90_coverage={summary['v3_practical_formal_p90_coverage']}",
                f"v3_practical_formal_p90_extreme_over_rate={summary['v3_practical_formal_p90_extreme_over_rate']}",
                f"numeric_constraints={summary['numeric_constraints']}",
                f"item_anchors={summary['item_anchors']}",
                f"shape_anchors={summary['shape_anchors']}",
                f"quality_floor_anchors={summary['quality_floor_anchors']}",
                f"constraint_ok={summary['constraint_ok']}",
            )
        )
    )
    if summary["errors"]:
        examples = ";".join(
            f"{item['file']}:{item['error']}"
            for item in summary["errors"][:5]
        )
        print("parse_error_examples=" + examples)


def _write_csv(rows: list[dict[str, Any]]) -> None:
    fieldnames = (
        "file",
        "source",
        "status",
        "round",
        "session_id",
        "bid_sort_id",
        "bid_value",
        "map_id",
        "map_family",
        "hero",
        "evidence_stage",
        "evidence_profile_key",
        "information_density_score",
        "information_density_band",
        "hero_information_density",
        "hero_evidence_stage",
        "hero_map_family",
        "hero_map_id",
        "hero_map_evidence_stage",
        "hero_map_evidence_profile",
        "prior_state_count",
        "round_state_count",
        "round_action_send_count",
        "numeric_constraints",
        "item_anchors",
        "shape_anchors",
        "quality_floor_anchors",
        "conflicts",
        "constraint_ok",
        "v3_prior_available",
        "v3_prior_error",
        "v3_prior_map_id",
        "v3_prior_map_name",
        "v3_prior_items_per_session_min",
        "v3_prior_items_per_session_max",
        "v3_capacity_total_count_source",
        "v3_capacity_total_count_target",
        "v3_capacity_truth_item_count",
        "v3_capacity_prior_items_per_session_min",
        "v3_capacity_prior_items_per_session_max",
        "v3_capacity_target_prior_max_delta",
        "v3_capacity_truth_prior_max_delta",
        "v3_capacity_target_truth_delta",
        "v3_capacity_target_prior_max_ratio",
        "v3_capacity_truth_prior_max_ratio",
        "v3_capacity_flags",
        "v3_capacity_cases",
        "v3_prior_pool_count",
        "v3_prior_expected_draws",
        "v3_prior_expected_count",
        "v3_prior_expected_cells",
        "v3_prior_expected_value",
        "v3_prior_q6_draw_probability",
        "v3_prior_q6_session_probability",
        "v3_prior_q6_expected_count",
        "v3_prior_q6_expected_cells",
        "v3_prior_q6_expected_value",
        "v3_robust_available",
        "v3_robust_affects_bid",
        "v3_robust_status",
        "v3_robust_prior_usable",
        "v3_robust_prior_trusted",
        "v3_robust_fallback_mode",
        "v3_robust_activity_candidate",
        "v3_robust_prior_stress_score",
        "v3_robust_reasons",
        "v3_truth_available",
        "v3_truth_session_id",
        "v3_truth_map_id",
        "v3_truth_sort_id",
        "v3_truth_item_count",
        "v3_truth_total_cells",
        "v3_truth_raw_total_value",
        "v3_truth_q6_count",
        "v3_truth_q6_cells",
        "v3_truth_q6_raw_value",
        "v3_truth_decision_available",
        "v3_truth_formal_decision_value",
        "v3_truth_tail_replacement_decision_value",
        "v3_truth_tail_replacement_value",
        "v3_truth_trimmed_tail_value",
        "v3_truth_trimmed_tail_count",
        "v3_truth_q6_formal_decision_value",
        "v3_truth_q6_tail_replacement_decision_value",
        "v3_truth_q6_tail_replacement_value",
        "v3_truth_q6_trimmed_tail_value",
        "v3_truth_q6_trimmed_tail_count",
        "v3_summary_available",
        "v3_summary_feasible",
        "v3_summary_conflict_count",
        "v3_summary_conflicts",
        "v3_summary_session_total_count_exact",
        "v3_summary_session_total_cells_exact",
        "v3_summary_known_count_floor",
        "v3_summary_known_cells_floor",
        "v3_summary_known_value_floor",
        "v3_summary_q6_count_exact",
        "v3_summary_q6_cells_exact",
        "v3_summary_q6_value_exact",
        "v3_summary_q6_count_floor",
        "v3_summary_q6_cells_floor",
        "v3_summary_q6_value_floor",
        "v3_summary_q6_residual_count_exact",
        "v3_summary_q6_residual_cells_exact",
        "v3_summary_q6_residual_value_exact",
        "v3_post_available",
        "v3_post_ready",
        "v3_post_strict_ready",
        "v3_post_affects_bid",
        "v3_post_map_id",
        "v3_post_map_name",
        "v3_post_match_scope",
        "v3_post_n_total",
        "v3_post_n_matched",
        "v3_post_n_strict_matched",
        "v3_post_match_rate",
        "v3_post_strict_match_rate",
        "v3_post_q6_present_rate",
        "v3_post_total_cells_p10",
        "v3_post_total_cells_p50",
        "v3_post_total_cells_p90",
        "v3_post_total_value_p10",
        "v3_post_total_value_p50",
        "v3_post_total_value_p90",
        "v3_post_formal_decision_value_p10",
        "v3_post_formal_decision_value_p50",
        "v3_post_formal_decision_value_p90",
        "v3_post_tail_replacement_decision_value_p10",
        "v3_post_tail_replacement_decision_value_p50",
        "v3_post_tail_replacement_decision_value_p90",
        "v3_post_q6_count_p10",
        "v3_post_q6_count_p50",
        "v3_post_q6_count_p90",
        "v3_post_q6_cells_p10",
        "v3_post_q6_cells_p50",
        "v3_post_q6_cells_p90",
        "v3_post_q6_value_p10",
        "v3_post_q6_value_p50",
        "v3_post_q6_value_p90",
        "v3_post_q6_formal_decision_value_p10",
        "v3_post_q6_formal_decision_value_p50",
        "v3_post_q6_formal_decision_value_p90",
        "v3_post_q6_tail_replacement_decision_value_p10",
        "v3_post_q6_tail_replacement_decision_value_p50",
        "v3_post_q6_tail_replacement_decision_value_p90",
        "v3_post_diagnostics",
        "v3_ccv_available",
        "v3_ccv_ready",
        "v3_ccv_strict_ready",
        "v3_ccv_affects_bid",
        "v3_ccv_map_id",
        "v3_ccv_map_name",
        "v3_ccv_match_scope",
        "v3_ccv_n_total",
        "v3_ccv_n_matched",
        "v3_ccv_n_strict_matched",
        "v3_ccv_match_rate",
        "v3_ccv_strict_match_rate",
        "v3_ccv_q6_present_rate",
        "v3_ccv_q6_count_p10",
        "v3_ccv_q6_count_p50",
        "v3_ccv_q6_count_p90",
        "v3_ccv_q6_cells_p10",
        "v3_ccv_q6_cells_p50",
        "v3_ccv_q6_cells_p90",
        "v3_ccv_q6_value_p10",
        "v3_ccv_q6_value_p50",
        "v3_ccv_q6_value_p90",
        "v3_ccv_q6_formal_decision_value_p10",
        "v3_ccv_q6_formal_decision_value_p50",
        "v3_ccv_q6_formal_decision_value_p90",
        "v3_ccv_q6_tail_replacement_decision_value_p10",
        "v3_ccv_q6_tail_replacement_decision_value_p50",
        "v3_ccv_q6_tail_replacement_decision_value_p90",
        "v3_ccv_diagnostics",
        "v3_ccvc_available",
        "v3_ccvc_ready",
        "v3_ccvc_strict_ready",
        "v3_ccvc_affects_bid",
        "v3_ccvc_map_id",
        "v3_ccvc_map_name",
        "v3_ccvc_match_scope",
        "v3_ccvc_n_total",
        "v3_ccvc_n_matched",
        "v3_ccvc_n_strict_matched",
        "v3_ccvc_match_rate",
        "v3_ccvc_strict_match_rate",
        "v3_ccvc_q6_present_rate",
        "v3_ccvc_q6_count_p10",
        "v3_ccvc_q6_count_p50",
        "v3_ccvc_q6_count_p90",
        "v3_ccvc_q6_cells_p10",
        "v3_ccvc_q6_cells_p50",
        "v3_ccvc_q6_cells_p90",
        "v3_ccvc_q6_value_p10",
        "v3_ccvc_q6_value_p50",
        "v3_ccvc_q6_value_p90",
        "v3_ccvc_q6_formal_decision_value_p10",
        "v3_ccvc_q6_formal_decision_value_p50",
        "v3_ccvc_q6_formal_decision_value_p90",
        "v3_ccvc_q6_tail_replacement_decision_value_p10",
        "v3_ccvc_q6_tail_replacement_decision_value_p50",
        "v3_ccvc_q6_tail_replacement_decision_value_p90",
        "v3_ccvc_diagnostics",
        "v3_resid_available",
        "v3_resid_ready",
        "v3_resid_strict_ready",
        "v3_resid_affects_bid",
        "v3_resid_map_id",
        "v3_resid_map_name",
        "v3_resid_match_scope",
        "v3_resid_n_total",
        "v3_resid_n_matched",
        "v3_resid_n_strict_matched",
        "v3_resid_match_rate",
        "v3_resid_strict_match_rate",
        "v3_resid_q6_present_rate",
        "v3_resid_total_cells_p10",
        "v3_resid_total_cells_p50",
        "v3_resid_total_cells_p90",
        "v3_resid_total_value_p10",
        "v3_resid_total_value_p50",
        "v3_resid_total_value_p90",
        "v3_resid_formal_decision_value_p10",
        "v3_resid_formal_decision_value_p50",
        "v3_resid_formal_decision_value_p90",
        "v3_resid_tail_replacement_decision_value_p10",
        "v3_resid_tail_replacement_decision_value_p50",
        "v3_resid_tail_replacement_decision_value_p90",
        "v3_resid_q6_count_p10",
        "v3_resid_q6_count_p50",
        "v3_resid_q6_count_p90",
        "v3_resid_q6_cells_p10",
        "v3_resid_q6_cells_p50",
        "v3_resid_q6_cells_p90",
        "v3_resid_q6_value_p10",
        "v3_resid_q6_value_p50",
        "v3_resid_q6_value_p90",
        "v3_resid_q6_formal_decision_value_p10",
        "v3_resid_q6_formal_decision_value_p50",
        "v3_resid_q6_formal_decision_value_p90",
        "v3_resid_q6_tail_replacement_decision_value_p10",
        "v3_resid_q6_tail_replacement_decision_value_p50",
        "v3_resid_q6_tail_replacement_decision_value_p90",
        "v3_resid_diagnostics",
        "v3_resid_gate_available",
        "v3_resid_gate_ready",
        "v3_resid_gate_strict_ready",
        "v3_resid_gate_affects_bid",
        "v3_resid_gate_active",
        "v3_resid_gate_status",
        "v3_resid_gate_gate_reason",
        "v3_resid_gate_source",
        "v3_resid_gate_archive_sessions",
        "v3_resid_gate_calibration_status",
        "v3_resid_gate_calibration_gate_reason",
        "v3_resid_gate_q6_count_delta_p50",
        "v3_resid_gate_q6_cells_delta_p50",
        "v3_resid_gate_q6_value_delta_p50",
        "v3_resid_gate_map_id",
        "v3_resid_gate_map_name",
        "v3_resid_gate_match_scope",
        "v3_resid_gate_n_total",
        "v3_resid_gate_n_matched",
        "v3_resid_gate_n_strict_matched",
        "v3_resid_gate_match_rate",
        "v3_resid_gate_strict_match_rate",
        "v3_resid_gate_q6_present_rate",
        "v3_resid_gate_q6_count_p10",
        "v3_resid_gate_q6_count_p50",
        "v3_resid_gate_q6_count_p90",
        "v3_resid_gate_q6_cells_p10",
        "v3_resid_gate_q6_cells_p50",
        "v3_resid_gate_q6_cells_p90",
        "v3_resid_gate_q6_value_p10",
        "v3_resid_gate_q6_value_p50",
        "v3_resid_gate_q6_value_p90",
        "v3_resid_gate_q6_formal_decision_value_p10",
        "v3_resid_gate_q6_formal_decision_value_p50",
        "v3_resid_gate_q6_formal_decision_value_p90",
        "v3_resid_gate_q6_tail_replacement_decision_value_p10",
        "v3_resid_gate_q6_tail_replacement_decision_value_p50",
        "v3_resid_gate_q6_tail_replacement_decision_value_p90",
        "v3_resid_gate_diagnostics",
        "v3_rtc_available",
        "v3_rtc_ready",
        "v3_rtc_affects_bid",
        "v3_rtc_active",
        "v3_rtc_candidate",
        "v3_rtc_derived_fields",
        "v3_rtc_diagnostics",
        "v3_rtc_q6_count_status",
        "v3_rtc_q6_count_candidate",
        "v3_rtc_q6_count_value",
        "v3_rtc_q6_count_total_exact",
        "v3_rtc_q6_count_non_q6_exact_sum",
        "v3_rtc_q6_count_q6_explicit_exact",
        "v3_rtc_q6_count_q6_floor",
        "v3_rtc_q6_count_missing_non_q6_qualities",
        "v3_rtc_q6_cells_status",
        "v3_rtc_q6_cells_candidate",
        "v3_rtc_q6_cells_value",
        "v3_rtc_q6_cells_total_exact",
        "v3_rtc_q6_cells_non_q6_exact_sum",
        "v3_rtc_q6_cells_q6_explicit_exact",
        "v3_rtc_q6_cells_q6_floor",
        "v3_rtc_q6_cells_missing_non_q6_qualities",
        "v3_rtc_q6_value_status",
        "v3_rtc_q6_value_candidate",
        "v3_rtc_q6_value_value",
        "v3_rtc_q6_value_total_exact",
        "v3_rtc_q6_value_non_q6_exact_sum",
        "v3_rtc_q6_value_q6_explicit_exact",
        "v3_rtc_q6_value_q6_floor",
        "v3_rtc_q6_value_missing_non_q6_qualities",
        "v3_cal_available",
        "v3_cal_ready",
        "v3_cal_strict_ready",
        "v3_cal_affects_bid",
        "v3_cal_active",
        "v3_cal_status",
        "v3_cal_gate_reason",
        "v3_cal_scale",
        "v3_cal_archive_sessions",
        "v3_cal_prior_trials",
        "v3_cal_median_ratio",
        "v3_cal_p90_ratio",
        "v3_cal_formal_p50_over_rate",
        "v3_cal_baseline_formal_p50_mae",
        "v3_cal_baseline_formal_p50_bias",
        "v3_cal_source",
        "v3_cal_match_scope",
        "v3_cal_n_total",
        "v3_cal_n_matched",
        "v3_cal_n_strict_matched",
        "v3_cal_match_rate",
        "v3_cal_strict_match_rate",
        "v3_cal_total_value_p10",
        "v3_cal_total_value_p50",
        "v3_cal_total_value_p90",
        "v3_cal_formal_decision_value_p10",
        "v3_cal_formal_decision_value_p50",
        "v3_cal_formal_decision_value_p90",
        "v3_cal_tail_replacement_decision_value_p10",
        "v3_cal_tail_replacement_decision_value_p50",
        "v3_cal_tail_replacement_decision_value_p90",
        "v3_cal_q6_value_p10",
        "v3_cal_q6_value_p50",
        "v3_cal_q6_value_p90",
        "v3_cal_q6_formal_decision_value_p10",
        "v3_cal_q6_formal_decision_value_p50",
        "v3_cal_q6_formal_decision_value_p90",
        "v3_cal_q6_tail_replacement_decision_value_p10",
        "v3_cal_q6_tail_replacement_decision_value_p50",
        "v3_cal_q6_tail_replacement_decision_value_p90",
        "v3_cal_diagnostics",
        "v3_under_available",
        "v3_under_ready",
        "v3_under_strict_ready",
        "v3_under_affects_bid",
        "v3_under_active",
        "v3_under_candidate",
        "v3_under_status",
        "v3_under_gate_reason",
        "v3_under_scale",
        "v3_under_hero",
        "v3_under_hero_map_id",
        "v3_under_source",
        "v3_under_entry_source",
        "v3_under_archive_windows",
        "v3_under_archive_sessions",
        "v3_under_formal_p50_mae",
        "v3_under_formal_p50_bias",
        "v3_under_formal_p50_below_rate",
        "v3_under_formal_p50_over_rate",
        "v3_under_formal_p90_coverage",
        "v3_under_scaled_delta_formal_p50_mae",
        "v3_under_scaled_delta_q6_formal_p50_mae",
        "v3_under_public_total_rate",
        "v3_under_q6_floor_rate",
        "v3_under_flags",
        "v3_under_map_id",
        "v3_under_map_name",
        "v3_under_match_scope",
        "v3_under_n_total",
        "v3_under_n_matched",
        "v3_under_n_strict_matched",
        "v3_under_match_rate",
        "v3_under_strict_match_rate",
        "v3_under_q6_present_rate",
        "v3_under_total_value_p10",
        "v3_under_total_value_p50",
        "v3_under_total_value_p90",
        "v3_under_formal_decision_value_p10",
        "v3_under_formal_decision_value_p50",
        "v3_under_formal_decision_value_p90",
        "v3_under_tail_replacement_decision_value_p10",
        "v3_under_tail_replacement_decision_value_p50",
        "v3_under_tail_replacement_decision_value_p90",
        "v3_under_q6_value_p10",
        "v3_under_q6_value_p50",
        "v3_under_q6_value_p90",
        "v3_under_q6_formal_decision_value_p10",
        "v3_under_q6_formal_decision_value_p50",
        "v3_under_q6_formal_decision_value_p90",
        "v3_under_q6_tail_replacement_decision_value_p10",
        "v3_under_q6_tail_replacement_decision_value_p50",
        "v3_under_q6_tail_replacement_decision_value_p90",
        "v3_under_diagnostics",
        "v3_tail_review_available",
        "v3_tail_review_ready",
        "v3_tail_review_strict_ready",
        "v3_tail_review_affects_bid",
        "v3_tail_review_active",
        "v3_tail_review_candidate",
        "v3_tail_review_hurt_guard",
        "v3_tail_review_status",
        "v3_tail_review_gate_reason",
        "v3_tail_review_hero",
        "v3_tail_review_hero_map_id",
        "v3_tail_review_source",
        "v3_tail_review_entry_source",
        "v3_tail_review_archive_windows",
        "v3_tail_review_archive_sessions",
        "v3_tail_review_tail_delta_p50_mae",
        "v3_tail_review_q6_tail_delta_p50_mae",
        "v3_tail_review_tail_p90_coverage",
        "v3_tail_review_q6_tail_p90_coverage",
        "v3_tail_review_public_total_rate",
        "v3_tail_review_q6_floor_rate",
        "v3_tail_review_flags",
        "v3_tail_review_map_id",
        "v3_tail_review_map_name",
        "v3_tail_review_match_scope",
        "v3_tail_review_n_total",
        "v3_tail_review_n_matched",
        "v3_tail_review_n_strict_matched",
        "v3_tail_review_match_rate",
        "v3_tail_review_strict_match_rate",
        "v3_tail_review_q6_present_rate",
        "v3_tail_review_tail_replacement_decision_value_p10",
        "v3_tail_review_tail_replacement_decision_value_p50",
        "v3_tail_review_tail_replacement_decision_value_p90",
        "v3_tail_review_q6_tail_replacement_decision_value_p10",
        "v3_tail_review_q6_tail_replacement_decision_value_p50",
        "v3_tail_review_q6_tail_replacement_decision_value_p90",
        "v3_tail_review_diagnostics",
        "v3_fv_available",
        "v3_fv_ready",
        "v3_fv_strict_ready",
        "v3_fv_affects_bid",
        "v3_fv_active",
        "v3_fv_candidate",
        "v3_fv_status",
        "v3_fv_gate_reason",
        "v3_fv_source",
        "v3_fv_stress_class",
        "v3_fv_capacity_flags",
        "v3_fv_map_id",
        "v3_fv_map_name",
        "v3_fv_match_scope",
        "v3_fv_n_total",
        "v3_fv_n_matched",
        "v3_fv_n_strict_matched",
        "v3_fv_match_rate",
        "v3_fv_strict_match_rate",
        "v3_fv_q6_present_rate",
        "v3_fv_total_cells_p10",
        "v3_fv_total_cells_p50",
        "v3_fv_total_cells_p90",
        "v3_fv_total_value_p10",
        "v3_fv_total_value_p50",
        "v3_fv_total_value_p90",
        "v3_fv_formal_decision_value_p10",
        "v3_fv_formal_decision_value_p50",
        "v3_fv_formal_decision_value_p90",
        "v3_fv_tail_replacement_decision_value_p10",
        "v3_fv_tail_replacement_decision_value_p50",
        "v3_fv_tail_replacement_decision_value_p90",
        "v3_fv_q6_count_p10",
        "v3_fv_q6_count_p50",
        "v3_fv_q6_count_p90",
        "v3_fv_q6_cells_p10",
        "v3_fv_q6_cells_p50",
        "v3_fv_q6_cells_p90",
        "v3_fv_q6_value_p10",
        "v3_fv_q6_value_p50",
        "v3_fv_q6_value_p90",
        "v3_fv_q6_formal_decision_value_p10",
        "v3_fv_q6_formal_decision_value_p50",
        "v3_fv_q6_formal_decision_value_p90",
        "v3_fv_q6_tail_replacement_decision_value_p10",
        "v3_fv_q6_tail_replacement_decision_value_p50",
        "v3_fv_q6_tail_replacement_decision_value_p90",
        "v3_fv_total_count_source",
        "v3_fv_total_count_target",
        "v3_fv_total_count_prior_expected",
        "v3_fv_total_count_target_prior_ratio",
        "v3_fv_total_cells_source",
        "v3_fv_total_cells_target",
        "v3_fv_total_cells_prior_expected",
        "v3_fv_total_cells_target_prior_ratio",
        "v3_fv_q6_count_source",
        "v3_fv_q6_count_target",
        "v3_fv_q6_count_prior_expected",
        "v3_fv_q6_count_target_prior_ratio",
        "v3_fv_q6_cells_source",
        "v3_fv_q6_cells_target",
        "v3_fv_q6_cells_prior_expected",
        "v3_fv_q6_cells_target_prior_ratio",
        "v3_fv_total_value_source",
        "v3_fv_total_value_target",
        "v3_fv_total_value_prior_expected",
        "v3_fv_total_value_target_prior_ratio",
        "v3_fv_q6_value_source",
        "v3_fv_q6_value_target",
        "v3_fv_q6_value_prior_expected",
        "v3_fv_q6_value_target_prior_ratio",
        "v3_fv_diagnostics",
        "v3_scp_available",
        "v3_scp_ready",
        "v3_scp_affects_bid",
        "v3_scp_active",
        "v3_scp_candidate",
        "v3_scp_missing_table",
        "v3_scp_status",
        "v3_scp_gate_reason",
        "v3_scp_scope",
        "v3_scp_group",
        "v3_scp_source",
        "v3_scp_archive_sessions",
        "v3_scp_inventory_count_p95",
        "v3_scp_inventory_count_max",
        "v3_scp_non_temp_inventory_count_p95",
        "v3_scp_non_temp_inventory_count_max",
        "v3_scp_known_temp_zodiac_count_max",
        "v3_scp_above_drop_after_temp_zodiac_rows",
        "v3_scp_above_round_after_temp_zodiac_rows",
        "v3_scp_payload_inventory_mismatch_rows",
        "v3_scp_missing_table_rows",
        "v3_scp_target_count_source",
        "v3_scp_target_count",
        "v3_scp_prior_items_per_session_max",
        "v3_scp_target_to_observed_p95_delta",
        "v3_scp_prior_max_to_observed_p95_delta",
        "v3_scp_prior_max_to_observed_max_delta",
        "v3_scp_flags",
        "v3_scp_diagnostics",
        "v3_cse_available",
        "v3_cse_ready",
        "v3_cse_affects_bid",
        "v3_cse_active",
        "v3_cse_candidate",
        "v3_cse_pressure_candidate",
        "v3_cse_status",
        "v3_cse_gate_reason",
        "v3_cse_scope",
        "v3_cse_group",
        "v3_cse_source",
        "v3_cse_archive_sessions",
        "v3_cse_mechanism_classes",
        "v3_cse_source_evidence_classes",
        "v3_cse_source_context_classes",
        "v3_cse_unique_round_overflow_rows",
        "v3_cse_server_side_expansion_rows",
        "v3_cse_session_capacity_source_semantics_rows",
        "v3_cse_public_total_match_rows",
        "v3_cse_full_action_rows",
        "v3_cse_payload_verified_only_rows",
        "v3_cse_payload_inventory_mismatch_rows",
        "v3_cse_non_zodiac_missing_max",
        "v3_cse_unique_non_temp_p95",
        "v3_cse_unique_non_temp_max",
        "v3_cse_unique_round_excess_p95",
        "v3_cse_unique_round_excess_max",
        "v3_cse_bidmap_items_per_session_max",
        "v3_cse_bidmap_raw_round_cap_max",
        "v3_cse_target_count_source",
        "v3_cse_target_count",
        "v3_cse_prior_items_per_session_max",
        "v3_cse_target_prior_max_delta",
        "v3_cse_target_to_unique_non_temp_p95_delta",
        "v3_cse_prior_max_to_unique_non_temp_p95_delta",
        "v3_cse_prior_max_to_unique_non_temp_max_delta",
        "v3_cse_flags",
        "v3_cse_diagnostics",
        "v3_practical_available",
        "v3_practical_ready",
        "v3_practical_strict_ready",
        "v3_practical_affects_bid",
        "v3_practical_active",
        "v3_practical_candidate",
        "v3_practical_source",
        "v3_practical_mode",
        "v3_practical_status",
        "v3_practical_recommendation",
        "v3_practical_confidence",
        "v3_practical_source_lanes",
        "v3_practical_risk_flags",
        "v3_practical_reason",
        "v3_practical_map_id",
        "v3_practical_map_name",
        "v3_practical_match_scope",
        "v3_practical_n_total",
        "v3_practical_n_matched",
        "v3_practical_n_strict_matched",
        "v3_practical_match_rate",
        "v3_practical_strict_match_rate",
        "v3_practical_formal_decision_value_p10",
        "v3_practical_formal_decision_value_p50",
        "v3_practical_formal_decision_value_p90",
        "v3_practical_q6_formal_decision_value_p10",
        "v3_practical_q6_formal_decision_value_p50",
        "v3_practical_q6_formal_decision_value_p90",
        "v3_practical_baseline_formal_decision_value_p50",
        "v3_practical_baseline_formal_decision_value_p90",
        "v3_practical_delta_formal_decision_value_p50",
        "v3_practical_delta_formal_decision_value_p90",
        "v3_practical_baseline_q6_formal_decision_value_p50",
        "v3_practical_baseline_q6_formal_decision_value_p90",
        "v3_practical_delta_q6_formal_decision_value_p50",
        "v3_practical_delta_q6_formal_decision_value_p90",
        "v3_practical_diagnostics",
    )
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate v3 pre-bid constraint coverage for Fatbeans captures.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument(
        "--format",
        choices=("summary", "json", "jsonl", "csv"),
        default="summary",
    )
    parser.add_argument(
        "--activity",
        action="store_true",
        help="Use the explicit 20260702 shipwreck activity pool instead of clean-eval",
    )
    parser.add_argument(
        "--skip-table-report",
        action="store_true",
        help="Skip v3 prior/truth fields and only audit pre-bid constraints.",
    )
    parser.add_argument(
        "--posterior-trials",
        type=int,
        default=512,
        help="Prior sample bank size per map for v3 shadow posterior. Use 0 to disable.",
    )
    parser.add_argument("--posterior-seed", type=int, default=0)
    parser.add_argument(
        "--ccv-component-likelihood",
        action="store_true",
        help="Emit optional v3_ccvc_ component-likelihood CCV shadow fields.",
    )
    parser.add_argument(
        "--ccv-component-freeze-cells",
        action="store_true",
        help="Emit v3_ccvc_ with component count/value but baseline q6 cells.",
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        default=_default_calibration_path(),
        help="Optional v3 prior calibration table. Defaults to data/processed/v3_prior_calibration_shadow.json when present.",
    )
    parser.add_argument(
        "--no-calibration",
        action="store_true",
        help="Disable v3 prior calibration shadow fields.",
    )
    parser.add_argument(
        "--underestimate-repair",
        type=Path,
        default=_default_underestimate_repair_path(),
        help="Optional v3 hero/map underestimate repair shadow table.",
    )
    parser.add_argument(
        "--no-underestimate-repair",
        action="store_true",
        help="Disable v3 underestimate repair shadow fields.",
    )
    parser.add_argument(
        "--tail-value-review",
        type=Path,
        default=_default_tail_value_review_path(),
        help="Optional v3 hero/map tail-value review shadow table.",
    )
    parser.add_argument(
        "--no-tail-value-review",
        action="store_true",
        help="Disable v3 tail-value review shadow fields.",
    )
    parser.add_argument(
        "--settlement-count-prior",
        type=Path,
        default=_default_settlement_count_prior_path(),
        help="Optional v3 settlement count prior shadow table.",
    )
    parser.add_argument(
        "--no-settlement-count-prior",
        action="store_true",
        help="Disable v3 settlement count prior shadow fields.",
    )
    parser.add_argument(
        "--capacity-source-expansion",
        type=Path,
        default=_default_capacity_source_expansion_path(),
        help="Optional v3 capacity/source expansion shadow table.",
    )
    parser.add_argument(
        "--no-capacity-source-expansion",
        action="store_true",
        help="Disable v3 capacity/source expansion shadow fields.",
    )
    parser.add_argument("--fail-on-conflicts", action="store_true")
    parser.add_argument("--fail-on-parse-errors", action="store_true")
    parser.add_argument(
        "--include-mixed-samples",
        action="store_true",
        help=(
            "Include mixed or non-strict captures. By default evaluation skips "
            "mixed filenames, multi-session captures, and captures whose hard "
            "observed item evidence cannot be reconciled with settlement truth."
        ),
    )
    args = parser.parse_args(argv)

    if args.paths and args.activity:
        parser.error("--activity cannot be combined with explicit paths")
    if args.paths:
        selected_paths = _iter_paths(args.paths)
        selector = "explicit_paths"
    elif args.activity:
        selected_paths = tuple(Path(path) for path in activity_eval_files())
        selector = "activity_20260702:all"
    else:
        selected_paths = _default_paths()
        selector = "clean_eval:seven_heroes"
    metadata = sample_selection_metadata(selected_paths, selector=selector)
    metadata_fields = selection_row_fields(metadata)
    print("run_metadata=" + json.dumps(metadata, ensure_ascii=False, sort_keys=True), file=sys.stderr)

    tables = None if args.skip_table_report else load_monitor_tables()
    calibration_entries = (
        {}
        if args.no_calibration or args.skip_table_report
        else load_prior_calibration_entries(args.calibration)
    )
    underestimate_repair_entries = (
        {}
        if args.no_underestimate_repair or args.skip_table_report
        else load_underestimate_repair_entries(args.underestimate_repair)
    )
    tail_value_review_entries = (
        {}
        if args.no_tail_value_review or args.skip_table_report
        else load_tail_value_review_entries(args.tail_value_review)
    )
    settlement_count_prior_entries = (
        {}
        if args.no_settlement_count_prior or args.skip_table_report
        else load_settlement_count_prior_entries(args.settlement_count_prior)
    )
    capacity_source_expansion_entries = (
        {}
        if args.no_capacity_source_expansion or args.skip_table_report
        else load_capacity_source_expansion_entries(args.capacity_source_expansion)
    )
    rows, errors = evaluate_paths(
        selected_paths,
        tables=tables,
        calibration_entries=calibration_entries,
        underestimate_repair_entries=underestimate_repair_entries,
        tail_value_review_entries=tail_value_review_entries,
        settlement_count_prior_entries=settlement_count_prior_entries,
        capacity_source_expansion_entries=capacity_source_expansion_entries,
        posterior_trials=args.posterior_trials,
        posterior_seed=args.posterior_seed,
        ccv_options=V3CcvOptions(
            component_likelihood=(
                args.ccv_component_likelihood or args.ccv_component_freeze_cells
            ),
            component_move_cells=not args.ccv_component_freeze_cells,
        ),
        include_mixed_samples=args.include_mixed_samples,
    )
    for row in rows:
        row.update(metadata_fields)
    summary = summarize_rows(rows, errors)
    summary["run_metadata"] = metadata
    if args.format == "json":
        print(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.format == "jsonl":
        for row in rows:
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    elif args.format == "csv":
        _write_csv(rows)
    else:
        _print_summary(summary)

    if args.fail_on_conflicts and not summary["constraint_ok"]:
        return 1
    if args.fail_on_parse_errors and not summary["parse_ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
