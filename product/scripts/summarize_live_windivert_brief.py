"""Brief WinDivert live session summary: rounds, match count, bid error."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bidking_lab.live.monitor import (  # noqa: E402
    _model_eval_row,
    build_monitor_artifact_from_events,
    build_monitor_artifact_from_file,
    load_monitor_tables,
)
from bidking_lab.inference.q6_residual import (  # noqa: E402
    AISHA_BOTTOM_ROW_RISK_THRESHOLD,
    AISHA_SHIPWRECK_DEEP_ROW_THRESHOLD,
)
from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansCaptureEvents,
    parse_fatbeans_capture,
)


def _num(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _range_num(value: Any, index: int) -> float | None:
    parts = [part.strip().replace(",", "") for part in str(value or "").split("/")]
    if len(parts) <= index or parts[index] in ("", "?"):
        return None
    try:
        return float(parts[index])
    except (TypeError, ValueError):
        return None


def _is_windivert_row(row: dict[str, Any]) -> bool:
    if str(row.get("source") or "").lower() == "windivert":
        return True
    file_name = str(row.get("file") or "")
    return "windivert" in file_name.lower()


def _load_rows(path: Path, *, since_ts: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or not _is_windivert_row(row):
            continue
        if float(row.get("ts") or 0) < since_ts:
            continue
        rows.append(row)
    return rows


def _archive_complete_dir(archive_dir: Path) -> Path:
    if archive_dir.name == "complete":
        return archive_dir
    complete = archive_dir / "complete"
    return complete if complete.exists() else archive_dir


def _load_archive_rows(
    archive_dir: Path,
    *,
    since_ts: float,
    n_trials: int,
    roi_trials: int,
    shadow_trials: int | None,
    run_debug_shadows: bool,
    window: str,
    formal_mode: str | None = None,
) -> list[dict[str, Any]]:
    if not archive_dir.exists():
        return []
    paths = sorted(
        archive_dir.rglob("*.json"),
        key=lambda path: (path.stat().st_mtime, path.name),
    )
    selected = [
        path
        for path in paths
        if path.stat().st_mtime >= since_ts
        and "windivert" in path.name.lower()
    ]
    if not selected:
        return []

    tables = load_monitor_tables()
    rows: list[dict[str, Any]] = []
    for path in selected:
        if window == "full":
            rows.extend(
                _load_archive_full_rows(
                    path,
                    tables=tables,
                    n_trials=n_trials,
                    roi_trials=roi_trials,
                    shadow_trials=shadow_trials,
                    run_debug_shadows=run_debug_shadows,
                    formal_mode=formal_mode,
                )
            )
        else:
            rows.extend(
                _load_archive_prebid_rows(
                    path,
                    tables=tables,
                    n_trials=n_trials,
                    roi_trials=roi_trials,
                    shadow_trials=shadow_trials,
                    run_debug_shadows=run_debug_shadows,
                    formal_mode=formal_mode,
                )
            )
    return rows


def _annotate_archive_row(
    row: dict[str, Any],
    *,
    path: Path,
    artifact: dict[str, Any],
    session_id: str | None = None,
    n_trials: int,
    roi_trials: int,
    shadow_trials: int | None,
    source: str,
) -> dict[str, Any]:
    row["ts"] = path.stat().st_mtime
    row["source"] = row.get("source") or source
    row["archive_path"] = str(path)
    row["session_id"] = row.get("session_id") or session_id or artifact.get("session_id")
    row["snapshot_mode"] = row.get("snapshot_mode") or "archive_fast"
    row["replay_n_trials"] = n_trials
    row["replay_roi_trials"] = roi_trials
    row["replay_shadow_trials"] = shadow_trials
    row["replay_formal_mode_requested"] = artifact.get("formal_mode_requested")
    row["replay_formal_mode"] = artifact.get("formal_mode")
    row["replay_formal_mode_reason"] = artifact.get("formal_mode_reason")
    bid_row = next(
        (
            item
            for item in artifact.get("bid_rows", [])
            if isinstance(item, dict)
        ),
        {},
    )
    for key in (
        "v3_practical_live_guard",
        "v3_practical_live_guard_reason",
        "v3_practical_unguarded_decision_value",
    ):
        if row.get(key) is None:
            row[key] = bid_row.get(key)
    if row.get("formal_mode_requested") is None:
        row["formal_mode_requested"] = artifact.get("formal_mode_requested")
    if row.get("formal_mode") is None:
        row["formal_mode"] = artifact.get("formal_mode")
    if row.get("formal_mode_reason") is None:
        row["formal_mode_reason"] = artifact.get("formal_mode_reason")
    return row


def _load_archive_full_rows(
    path: Path,
    *,
    tables: Any,
    n_trials: int,
    roi_trials: int,
    shadow_trials: int | None,
    run_debug_shadows: bool,
    formal_mode: str | None = None,
) -> list[dict[str, Any]]:
    artifact = build_monitor_artifact_from_file(
        path,
        tables=tables,
        n_trials=n_trials,
        roi_trials=roi_trials,
        shadow_trials=shadow_trials,
        run_debug_shadows=run_debug_shadows,
        formal_mode=formal_mode,
    )
    eval_row = artifact.get("model_eval")
    if not isinstance(eval_row, dict):
        return []
    row = _annotate_archive_row(
        dict(eval_row),
        path=path,
        artifact=artifact,
        n_trials=n_trials,
        roi_trials=roi_trials,
        shadow_trials=shadow_trials,
        source="windivert_archive",
    )
    row["eval_window"] = "full"
    return [row]


def _events_before_sort(
    events: FatbeansCaptureEvents,
    sort_id: int,
) -> FatbeansCaptureEvents:
    return FatbeansCaptureEvents(
        packets=tuple(row for row in events.packets if int(row.sort_id) < sort_id),
        frames=tuple(row for row in events.frames if int(row.sort_id) < sort_id),
        sends=tuple(row for row in events.sends if int(row.sort_id) < sort_id),
        states=tuple(row for row in events.states if int(row.sort_id) < sort_id),
        statuses=tuple(row for row in events.statuses if int(row.sort_id) < sort_id),
    )


def _truth_breakdown_from_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in artifact.items()
        if key.startswith("final_")
    }


def _load_archive_prebid_rows(
    path: Path,
    *,
    tables: Any,
    n_trials: int,
    roi_trials: int,
    shadow_trials: int | None,
    run_debug_shadows: bool,
    formal_mode: str | None = None,
) -> list[dict[str, Any]]:
    events = parse_fatbeans_capture(path)
    full_artifact = build_monitor_artifact_from_events(
        events,
        file=path.name,
        tables=tables,
        n_trials=n_trials,
        roi_trials=roi_trials,
        shadow_trials=shadow_trials,
        run_debug_shadows=run_debug_shadows,
        formal_mode=formal_mode,
    )
    final_value = full_artifact.get("known_value_sum")
    final_cells = full_artifact.get("inventory_cells")
    if final_value is None and final_cells is None:
        return []
    truth_breakdown = _truth_breakdown_from_artifact(full_artifact)
    rows: list[dict[str, Any]] = []
    bid_sends = [send for send in events.sends if getattr(send, "kind", "") == "bid"]
    previous_bid_sort_id = 0
    for window_round, bid_send in enumerate(bid_sends, start=1):
        bid_sort_id = int(bid_send.sort_id)
        prefix_events = _events_before_sort(events, bid_sort_id)
        round_action_sends = [
            send
            for send in events.sends
            if getattr(send, "kind", "") == "action"
            and previous_bid_sort_id < int(send.sort_id) < bid_sort_id
        ]
        round_states = [
            state
            for state in events.states
            if previous_bid_sort_id < int(state.sort_id) < bid_sort_id
        ]
        last_action_sort_id = max(
            (int(send.sort_id) for send in round_action_sends),
            default=None,
        )
        last_state_sort_id = max(
            (int(state.sort_id) for state in round_states),
            default=None,
        )
        prefix_artifact = build_monitor_artifact_from_events(
            prefix_events,
            file=f"{path.name}#prebid_r{window_round}_sort{bid_send.sort_id}",
            tables=tables,
            n_trials=n_trials,
            roi_trials=roi_trials,
            shadow_trials=shadow_trials,
            run_debug_shadows=run_debug_shadows,
            formal_mode=formal_mode,
        )
        eval_row = _model_eval_row(
            file=f"{path.name}#prebid_r{window_round}_sort{bid_send.sort_id}",
            artifact=prefix_artifact,
            final_value=final_value,
            final_cells=final_cells,
            truth_breakdown=truth_breakdown,
        )
        if not isinstance(eval_row, dict):
            continue
        row = _annotate_archive_row(
            dict(eval_row),
            path=path,
            artifact=prefix_artifact,
            session_id=full_artifact.get("session_id"),
            n_trials=n_trials,
            roi_trials=roi_trials,
            shadow_trials=shadow_trials,
            source="windivert_archive_prebid",
        )
        row["eval_window"] = "pre_bid"
        row["eval_window_round"] = window_round
        row["window_bid_sort_id"] = bid_sort_id
        row["window_bid_value"] = getattr(bid_send, "value", None)
        row["artifact_round"] = eval_row.get("round")
        row["artifact_action_round"] = eval_row.get("action_round")
        row["window_prior_state_count"] = len(prefix_events.states)
        row["window_round_state_count"] = len(round_states)
        row["window_round_action_send_count"] = len(round_action_sends)
        row["window_round_last_action_sort_id"] = last_action_sort_id
        row["window_round_last_state_sort_id"] = last_state_sort_id
        row["window_action_result_ready"] = (
            last_action_sort_id is None
            or (
                last_state_sort_id is not None
                and last_state_sort_id > last_action_sort_id
            )
        )
        row["window_has_prebid_state"] = bool(prefix_events.states)
        row["window_has_estimate"] = (
            _first_num(row, "decision_value_p50", "decision_value_p90") is not None
        )
        row["window_round_matches_artifact"] = (
            row["artifact_action_round"] is None
            or int(row["artifact_action_round"]) == window_round
        )
        row["window_ready_for_accuracy"] = (
            row["window_has_prebid_state"]
            and row["window_has_estimate"]
            and row["window_action_result_ready"]
            and row["window_round_matches_artifact"]
        )
        row["round"] = window_round
        row["action_round"] = window_round
        rows.append(row)
        previous_bid_sort_id = bid_sort_id
    return rows


def _row_key(row: dict[str, Any]) -> tuple[str, ...]:
    window = str(row.get("eval_window") or "")
    window_round = str(row.get("eval_window_round") or "")
    session_id = row.get("session_id")
    if session_id:
        return ("session", str(session_id), window, window_round)
    archive_path = row.get("archive_path")
    if archive_path:
        return ("archive", Path(str(archive_path)).name, window, window_round)
    file_name = row.get("file")
    if file_name:
        return ("file", Path(str(file_name)).name, window, window_round)
    return ("id", str(id(row)), window, window_round)


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = _row_key(row)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _source_label(row: dict[str, Any]) -> str:
    source = str(row.get("source") or "")
    if source:
        return source
    if row.get("archive_path"):
        return "windivert_archive"
    return "model_eval"


def _formal_mode_label(row: dict[str, Any]) -> str:
    value = row.get("formal_mode")
    if value is None:
        value = row.get("replay_formal_mode")
    text = str(value or "").strip()
    return text or "unknown"


def _formal_mode_reason_label(row: dict[str, Any]) -> str:
    value = row.get("formal_mode_reason")
    if value is None:
        value = row.get("replay_formal_mode_reason")
    text = str(value or "").strip()
    return text or "unknown"


def _v3_practical_guard_reason_label(row: dict[str, Any]) -> str:
    text = str(row.get("v3_practical_live_guard_reason") or "").strip()
    if not text and _formal_mode_reason_label(row) == "v3_practical_ready_live_guarded":
        text = "v3_practical_ready_live_guarded"
    return text or "unknown"


def _is_v3_practical_live_guarded(row: dict[str, Any]) -> bool:
    if _truthy(row.get("v3_practical_live_guard")):
        return True
    return _formal_mode_reason_label(row) == "v3_practical_ready_live_guarded"


def _round_bucket(row: dict[str, Any], key: str) -> str:
    try:
        round_no = int(row.get(key) or 0)
    except (TypeError, ValueError):
        return "?"
    if round_no <= 0:
        return "?"
    if round_no <= 5:
        return f"R{round_no}"
    return "R5+"


def _q6_truth_bucket(row: dict[str, Any]) -> str:
    value = _num(row, "final_q6_value")
    count = _num(row, "final_q6_count")
    if (value is not None and value > 0) or (count is not None and count > 0):
        return "q6>0"
    if value == 0 or count == 0:
        return "q6=0"
    return "unknown"


def _random_avg_bucket(row: dict[str, Any]) -> str:
    if str(row.get("random_sample_avg_signal_values") or "").strip():
        return "signal"
    if str(row.get("random_sample_avg_values") or "").strip():
        return "retained_only"
    return "none"


def _warehouse_error_bucket(row: dict[str, Any]) -> str:
    error = _num(row, "warehouse_p50_error")
    if error is None:
        return "unknown"
    if error < -20:
        return "under<-20"
    if error > 20:
        return "over>20"
    return "within±20"


def _random_floor_mode_bucket(row: dict[str, Any]) -> str:
    diagnostics = str(row.get("posterior_diagnostics") or "")
    for chunk in diagnostics.split(";"):
        token = chunk.strip()
        prefix = "public_random_sample_value_floor_mode:"
        if token.startswith(prefix):
            mode = token[len(prefix) :].split(":", 1)[0].strip()
            return mode or "unknown"
    return "none"


def _hero_bucket(row: dict[str, Any]) -> str:
    hero = str(row.get("hero") or row.get("local_hero") or "").strip()
    return hero or "unknown"


def _map_family_bucket(row: dict[str, Any]) -> str:
    family = str(row.get("map_family") or "").strip()
    if family:
        return family
    map_id = _num(row, "map_id")
    if map_id is None:
        return "unknown"
    map_no = int(map_id)
    if 2400 <= map_no < 2500:
        return "villa"
    if 2500 <= map_no < 2600:
        return "shipwreck"
    if 2600 <= map_no < 2700:
        return "hidden"
    return "unknown"


def _map_id_bucket(row: dict[str, Any]) -> str:
    map_id = _num(row, "map_id")
    if map_id is None:
        return "unknown"
    return str(int(map_id))


def _evidence_profile_bucket(row: dict[str, Any]) -> str:
    profile = str(row.get("evidence_profile_key") or "").strip()
    return profile or "unknown"


def _public_constraint_bucket(row: dict[str, Any]) -> str:
    public = str(row.get("public_constraint_key") or "").strip()
    if public and public != "none":
        return public
    return "none"


def _information_density_bucket(row: dict[str, Any]) -> str:
    band = str(row.get("information_density_band") or "").strip()
    return band or "unknown"


def _constraint_density_bucket(row: dict[str, Any]) -> str:
    count = 0
    for key in (
        "anchor_count",
        "shape_target_count",
        "category_target_count",
        "category_exclusion_count",
    ):
        value = _num(row, key)
        if value is not None:
            count += int(value)
    if _random_avg_bucket(row) == "signal":
        count += 1
    if _public_constraint_bucket(row) != "none":
        count += 1
    if row.get("size_bucket_active"):
        count += 1
    if count <= 0:
        return "none"
    if count <= 2:
        return "low_1_2"
    if count <= 5:
        return "medium_3_5"
    return "high_6_plus"


def _sample_space_bucket(row: dict[str, Any]) -> str:
    matched = _first_num(row, "posterior_samples", "matched_samples", "v2_n_matched")
    total = _first_num(row, "posterior_total_samples", "n_trials", "monitor_n_trials")
    if matched is None:
        return "unknown"
    if matched <= 0:
        return "zero_match"
    if matched <= 3:
        return "sparse_1_3"
    if total is not None and total > 0 and matched / total < 0.20:
        return "low_rate_lt20pct"
    if matched <= 10:
        return "limited_4_10"
    return "enough_gt10"


def _space_pressure_bucket(row: dict[str, Any]) -> str:
    if _q6_truth_bucket(row) != "q6>0":
        return "no_q6"
    pressure = _num(row, "v2_q6_space_pressure_p90")
    overflow = _num(row, "v2_q6_space_overflow_rate")
    if pressure is None and overflow is None:
        return "unknown"
    if (pressure is not None and pressure >= 1.0) or (
        overflow is not None and overflow > 0.10
    ):
        return "space_overflow"
    if pressure is not None and pressure < 0.50 and (overflow or 0.0) <= 0.0:
        return "low_space_pressure"
    return "space_constrained"


def _tail_bucket(row: dict[str, Any]) -> str:
    if _q6_truth_bucket(row) != "q6>0":
        if (_num(row, "final_trimmed_tail_value") or 0) > 0:
            return "non_q6_tail"
        return "no_q6_tail"
    if (_num(row, "final_q6_tail_replacement_value") or 0) > 0:
        return "q6_tail_replacement"
    top_band = str(row.get("q6_top_size_band") or "").strip()
    if top_band in {"q6_top_large", "q6_top_huge"}:
        return top_band
    if top_band:
        return "q6_non_large"
    return "q6_tail_unknown"


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "是"}


_Q6_SHADOW_PREFIXES = (
    "q6_residual_boost_shadow",
    "q6_residual_deep_floor_shadow",
    "q6_residual_deep11_floor_shadow",
    "q6_residual_hidden_floor_shadow",
    "q6_residual_villa_floor_shadow",
    "q6_residual_ethan_villa_random_floor_shadow",
    "q6_residual_ethan_shipwreck_layout_conditional_shadow",
)


def _q6_shadow_bucket(row: dict[str, Any]) -> str:
    if any(
        _truthy(row.get(f"{prefix}_covered_after"))
        for prefix in _Q6_SHADOW_PREFIXES
    ):
        return "covered"
    if any(
        _truthy(row.get(f"{prefix}_active"))
        for prefix in _Q6_SHADOW_PREFIXES
    ):
        return "active_miss"
    if _q6_truth_bucket(row) == "q6>0":
        return "inactive_q6"
    return "none"


def _q6_shadow_labels(row: dict[str, Any], status: str) -> str:
    labels: list[str] = []
    for prefix in _Q6_SHADOW_PREFIXES:
        active = _truthy(row.get(f"{prefix}_active"))
        covered = _truthy(row.get(f"{prefix}_covered_after"))
        if status == "covered" and not covered:
            continue
        if status == "active" and not active:
            continue
        if status == "active_miss" and not (active and not covered):
            continue
        label = str(row.get(f"{prefix}_label") or prefix).strip()
        if label:
            labels.append(label)
    return ";".join(dict.fromkeys(labels))


def _truth_value(row: dict[str, Any]) -> float | None:
    return _first_num(
        row,
        "decision_value_truth",
        "final_decision_value_with_tail_replacement",
        "final_decision_value",
        "final_value",
    )


def _decision_p50_error(row: dict[str, Any]) -> float | None:
    p50 = _num(row, "decision_value_p50")
    truth = _truth_value(row)
    if p50 is not None and truth is not None:
        return p50 - truth
    return _num(row, "decision_value_p50_error")


def _practical_p50_error(row: dict[str, Any]) -> float | None:
    p50 = _num(row, "v3_practical_formal_decision_value_p50")
    truth = _first_num(
        row,
        "final_formal_decision_value",
        "decision_value_truth",
        "final_decision_value",
        "final_value",
    )
    if p50 is not None and truth is not None:
        return p50 - truth
    return _num(row, "v3_practical_formal_decision_value_p50_error_vs_formal")


def _practical_truth_value(row: dict[str, Any]) -> float | None:
    return _first_num(
        row,
        "final_formal_decision_value",
        "decision_value_truth",
        "final_decision_value",
        "final_value",
    )


def _practical_unguarded_value(row: dict[str, Any], quantile: str) -> float | None:
    index = {"p10": 0, "p50": 1, "p90": 2}[quantile]
    value = _num(row, f"v3_practical_unguarded_decision_value_{quantile}")
    if value is not None:
        return value
    return _range_num(row.get("v3_practical_unguarded_decision_value"), index)


def _v2_reference_value(row: dict[str, Any], quantile: str) -> float | None:
    value = _num(row, f"v3_practical_baseline_formal_decision_value_{quantile}")
    if value is not None:
        return value
    value = _num(row, f"v2_decision_value_{quantile}")
    if value is not None:
        return value
    if _formal_mode_label(row) == "v2":
        return _num(row, f"decision_value_{quantile}")
    return None


def _policy_metric_block(
    *,
    p50_values: list[float],
    p90_values: list[float],
    truths: list[float],
) -> dict[str, Any]:
    p50_errors = [p50 - truth for p50, truth in zip(p50_values, truths)]
    p90_errors = [p90 - truth for p90, truth in zip(p90_values, truths)]
    return {
        "rows": len(truths),
        "p50_mae": (
            round(statistics.mean(abs(value) for value in p50_errors), 1)
            if p50_errors
            else None
        ),
        "p50_under_rate": (
            round(
                statistics.mean(1.0 if value < 0 else 0.0 for value in p50_errors),
                2,
            )
            if p50_errors
            else None
        ),
        "median_p50_error": (
            round(statistics.median(p50_errors), 1) if p50_errors else None
        ),
        "p90_coverage": (
            round(statistics.mean(1.0 if value >= 0 else 0.0 for value in p90_errors), 2)
            if p90_errors
            else None
        ),
        "p90_extreme_over_rate": (
            round(
                statistics.mean(
                    1.0 if error > _normalized_error_denominator(truth) else 0.0
                    for error, truth in zip(p90_errors, truths)
                ),
                2,
            )
            if p90_errors
            else None
        ),
        "median_p90_error": (
            round(statistics.median(p90_errors), 1) if p90_errors else None
        ),
    }


def _formal_policy_comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    policies = ("v2", "v3_guarded", "v3_unguarded")
    p50_values: dict[str, list[float]] = {policy: [] for policy in policies}
    p90_values: dict[str, list[float]] = {policy: [] for policy in policies}
    truths: list[float] = []
    for row in rows:
        truth = _practical_truth_value(row)
        v2_p50 = _v2_reference_value(row, "p50")
        v2_p90 = _v2_reference_value(row, "p90")
        guarded_p50 = _num(row, "decision_value_p50")
        guarded_p90 = _num(row, "decision_value_p90")
        unguarded_p50 = _practical_unguarded_value(row, "p50")
        unguarded_p90 = _practical_unguarded_value(row, "p90")
        if None in (
            truth,
            v2_p50,
            v2_p90,
            guarded_p50,
            guarded_p90,
            unguarded_p50,
            unguarded_p90,
        ):
            continue
        truths.append(float(truth))
        p50_values["v2"].append(float(v2_p50))
        p90_values["v2"].append(float(v2_p90))
        p50_values["v3_guarded"].append(float(guarded_p50))
        p90_values["v3_guarded"].append(float(guarded_p90))
        p50_values["v3_unguarded"].append(float(unguarded_p50))
        p90_values["v3_unguarded"].append(float(unguarded_p90))

    policy_metrics = {
        policy: _policy_metric_block(
            p50_values=p50_values[policy],
            p90_values=p90_values[policy],
            truths=truths,
        )
        for policy in policies
    }
    deltas_vs_v2: dict[str, dict[str, Any]] = {}
    for policy in ("v3_guarded", "v3_unguarded"):
        deltas_vs_v2[policy] = {
            "p50_mae_delta": (
                round(policy_metrics[policy]["p50_mae"] - policy_metrics["v2"]["p50_mae"], 1)
                if policy_metrics[policy]["p50_mae"] is not None
                and policy_metrics["v2"]["p50_mae"] is not None
                else None
            ),
            "p50_under_rate_delta": (
                round(
                    policy_metrics[policy]["p50_under_rate"]
                    - policy_metrics["v2"]["p50_under_rate"],
                    2,
                )
                if policy_metrics[policy]["p50_under_rate"] is not None
                and policy_metrics["v2"]["p50_under_rate"] is not None
                else None
            ),
            "p90_coverage_delta": (
                round(
                    policy_metrics[policy]["p90_coverage"]
                    - policy_metrics["v2"]["p90_coverage"],
                    2,
                )
                if policy_metrics[policy]["p90_coverage"] is not None
                and policy_metrics["v2"]["p90_coverage"] is not None
                else None
            ),
            "p90_extreme_over_delta": (
                round(
                    policy_metrics[policy]["p90_extreme_over_rate"]
                    - policy_metrics["v2"]["p90_extreme_over_rate"],
                    2,
                )
                if policy_metrics[policy]["p90_extreme_over_rate"] is not None
                and policy_metrics["v2"]["p90_extreme_over_rate"] is not None
                else None
            ),
            "median_p50_delta": (
                round(
                    statistics.median(
                        value - base
                        for value, base in zip(p50_values[policy], p50_values["v2"])
                    ),
                    1,
                )
                if p50_values[policy] and p50_values["v2"]
                else None
            ),
            "median_p90_delta": (
                round(
                    statistics.median(
                        value - base
                        for value, base in zip(p90_values[policy], p90_values["v2"])
                    ),
                    1,
                )
                if p90_values[policy] and p90_values["v2"]
                else None
            ),
        }
    return {
        "status": "watch" if truths else "missing_comparable_rows",
        "comparison_rows": len(truths),
        "policies": policy_metrics,
        "deltas_vs_v2": deltas_vs_v2,
    }


def _normalized_error_denominator(truth: float) -> float:
    return max(100_000.0, abs(truth))


def _p90_under_by(row: dict[str, Any]) -> float | None:
    p90 = _num(row, "decision_value_p90")
    truth = _truth_value(row)
    if p90 is None or truth is None:
        return None
    return max(0.0, truth - p90)


def _practical_p90_under_by(row: dict[str, Any]) -> float | None:
    p90 = _num(row, "v3_practical_formal_decision_value_p90")
    truth = _practical_truth_value(row)
    if p90 is None or truth is None:
        return None
    return max(0.0, truth - p90)


def _p90_covers(row: dict[str, Any]) -> bool | None:
    under = _p90_under_by(row)
    if under is None:
        return None
    return under <= 0


def _practical_raise_watch_eval(rows: list[dict[str, Any]]) -> list[dict[str, bool]]:
    events: list[dict[str, bool]] = []
    for row in rows:
        if row.get("v3_practical_recommendation") != "raise_watch":
            continue
        truth = _truth_value(row)
        baseline_p90 = _first_num(
            row,
            "decision_value_p90",
            "v3_practical_baseline_formal_decision_value_p90",
            "v3_post_formal_decision_value_p90",
        )
        practical_p90 = _num(row, "v3_practical_formal_decision_value_p90")
        if truth is None or baseline_p90 is None or practical_p90 is None:
            continue
        baseline_missed = baseline_p90 < truth
        practical_covered = practical_p90 >= truth
        false_alarm = not baseline_missed
        extreme_over = practical_p90 - truth > _normalized_error_denominator(truth)
        events.append(
            {
                "hit": baseline_missed and practical_covered,
                "miss": baseline_missed and not practical_covered,
                "false_alarm": false_alarm,
                "extreme_over": extreme_over,
                "misleading": false_alarm and extreme_over,
            }
        )
    return events


def _q6_p90_under(row: dict[str, Any]) -> bool:
    if _truthy(row.get("q6_plannable_p90_misses_truth")):
        return True
    q6_truth = _first_num(row, "final_q6_decision_value", "final_q6_value")
    q6_p90 = _num(row, "v2_q6_decision_value_p90")
    return q6_truth is not None and q6_truth > 0 and q6_p90 is not None and q6_p90 < q6_truth


def _primary_error_bucket(row: dict[str, Any]) -> str:
    covered = _p90_covers(row)
    if covered is None:
        return "no_estimate"
    if covered:
        return "p90_covered"
    if _hero_bucket(row) == "unknown" or row.get("map_id") in (None, ""):
        return "missing_opening_context"
    q6_shadow = _q6_shadow_bucket(row)
    if _q6_truth_bucket(row) == "q6>0" and _q6_p90_under(row):
        if q6_shadow == "inactive_q6":
            return "q6_gate_inactive"
        space = _space_pressure_bucket(row)
        if space == "space_overflow":
            return "q6_space_constrained"
        if _tail_bucket(row) in {"q6_tail_replacement", "q6_top_large", "q6_top_huge"}:
            return "q6_tail_value"
        if space == "low_space_pressure":
            return "q6_value_distribution"
        return "q6_undercovered"
    if _random_avg_bucket(row) == "signal":
        return "random_avg_floor_insufficient"
    if _warehouse_error_bucket(row) == "under<-20":
        return "warehouse_underestimated"
    if _sample_space_bucket(row) in {"zero_match", "sparse_1_3", "low_rate_lt20pct"}:
        return "sample_space_sparse"
    if row.get("layout_conflict") or _constraint_density_bucket(row) == "high_6_plus":
        return "constraint_or_layout_conflict"
    if _information_density_bucket(row) == "low" or _round_bucket(row, "action_round") in {"R1", "R2"}:
        return "low_information_window"
    if _public_constraint_bucket(row) != "none":
        return "public_constraint_interaction"
    return "other_value_under"


def _diagnostic_tags(row: dict[str, Any]) -> tuple[str, ...]:
    tags: list[str] = []
    covered = _p90_covers(row)
    if covered is None:
        tags.append("no_estimate")
    elif covered:
        tags.append("p90_covered")
    else:
        tags.append("p90_under")
    if _hero_bucket(row) == "unknown" or row.get("map_id") in (None, ""):
        tags.append("missing_opening_context")
    if _round_bucket(row, "action_round") in {"R1", "R2"}:
        tags.append("early_round")
    if _information_density_bucket(row) in {"low", "unknown"}:
        tags.append(f"info_{_information_density_bucket(row)}")
    if _q6_truth_bucket(row) == "q6>0":
        tags.append("q6_truth")
        if _q6_p90_under(row):
            tags.append("q6_p90_under")
        tags.append(f"q6_shadow_{_q6_shadow_bucket(row)}")
        tags.append(f"q6_space_{_space_pressure_bucket(row)}")
        tail = _tail_bucket(row)
        if tail not in {"q6_non_large", "q6_tail_unknown"}:
            tags.append(tail)
    else:
        tags.append("q6_absent_or_unknown")
    random_avg = _random_avg_bucket(row)
    if random_avg != "none":
        tags.append(f"random_avg_{random_avg}")
        tags.append(f"random_floor_{_random_floor_mode_bucket(row)}")
    public = _public_constraint_bucket(row)
    if public != "none":
        tags.append(f"public_{public}")
    warehouse = _warehouse_error_bucket(row)
    if warehouse != "within±20":
        tags.append(f"warehouse_{warehouse}")
    sample_space = _sample_space_bucket(row)
    if sample_space != "enough_gt10":
        tags.append(f"sample_{sample_space}")
    constraint_density = _constraint_density_bucket(row)
    if constraint_density != "none":
        tags.append(f"constraints_{constraint_density}")
    if row.get("layout_conflict"):
        tags.append("layout_conflict")
    if row.get("q6_quality_only_deep_local_risk"):
        tags.append("quality_only_deep_local_risk")
    if row.get("size_bucket_active"):
        tags.append("size_bucket_active")
    if (_num(row, "shape_target_count") or 0) > 0:
        tags.append("shape_constraint")
    if (
        (_num(row, "category_target_count") or 0) > 0
        or (_num(row, "category_exclusion_count") or 0) > 0
    ):
        tags.append("category_tool_constraint")
    return tuple(dict.fromkeys(tags))


def _q6_component_gaps(row: dict[str, Any]) -> dict[str, Any]:
    q6_truth = _first_num(row, "final_q6_decision_value", "final_q6_value")
    q6_replacement_truth = _first_num(
        row,
        "final_q6_decision_value_with_tail_replacement",
        "final_q6_decision_value",
        "final_q6_value",
    )
    q6_p90 = _num(row, "v2_q6_decision_value_p90")
    q6_tail_estimate_p90 = _num(row, "v2_q6_tail_replacement_estimate_p90")
    q6_count_truth = _num(row, "final_q6_count")
    q6_count_p90 = _num(row, "v2_q6_count_p90")
    q6_cells_truth = _num(row, "final_q6_cells")
    q6_cells_p90 = _num(row, "v2_q6_cells_p90")
    q6_tail_replacement_value = _num(row, "final_q6_tail_replacement_value") or 0.0
    return {
        "q6_truth": q6_truth,
        "q6_replacement_truth": q6_replacement_truth,
        "q6_p90": q6_p90,
        "q6_value_under_by": (
            max(0.0, q6_truth - q6_p90)
            if q6_truth is not None and q6_p90 is not None
            else None
        ),
        "q6_replacement_under_by": (
            max(0.0, q6_replacement_truth - q6_p90)
            if q6_replacement_truth is not None and q6_p90 is not None
            else None
        ),
        "q6_tail_estimate_under_by": (
            max(0.0, q6_replacement_truth - q6_tail_estimate_p90)
            if q6_replacement_truth is not None
            and q6_tail_estimate_p90 is not None
            and q6_tail_replacement_value > 0
            else None
        ),
        "q6_count_truth": q6_count_truth,
        "q6_count_p90": q6_count_p90,
        "q6_count_under_by": (
            max(0.0, q6_count_truth - q6_count_p90)
            if q6_count_truth is not None and q6_count_p90 is not None
            else None
        ),
        "q6_cells_truth": q6_cells_truth,
        "q6_cells_p90": q6_cells_p90,
        "q6_cells_under_by": (
            max(0.0, q6_cells_truth - q6_cells_p90)
            if q6_cells_truth is not None and q6_cells_p90 is not None
            else None
        ),
        "q6_tail_replacement_value": q6_tail_replacement_value,
        "q6_tail_estimate_p90": q6_tail_estimate_p90,
    }


def _q6_component_tags(row: dict[str, Any]) -> tuple[str, ...]:
    gaps = _q6_component_gaps(row)
    q6_truth = gaps["q6_truth"]
    q6_p90 = gaps["q6_p90"]
    if q6_truth is None or q6_truth <= 0:
        return ("no_q6_truth",)

    tags: list[str] = ["q6_truth"]
    if q6_p90 is None:
        tags.append("q6_no_p90")
    elif q6_p90 <= 0:
        tags.append("q6_presence_zero_p90")

    if (gaps["q6_value_under_by"] or 0.0) > 0:
        tags.append("q6_value_under")
    if (gaps["q6_count_under_by"] or 0.0) > 0:
        tags.append("q6_count_under_truth")
    if (gaps["q6_cells_under_by"] or 0.0) > 0:
        tags.append("q6_cells_under_truth")
    if (_num(row, "v2_q6_count_p90_under_prior_by") or 0.0) > 0:
        tags.append("q6_count_below_prior")
    if (_num(row, "v2_q6_cells_p90_under_prior_by") or 0.0) > 0:
        tags.append("q6_cells_below_prior")

    if (gaps["q6_tail_replacement_value"] or 0.0) > 0:
        tags.append("q6_tail_replacement_truth")
        if gaps["q6_tail_estimate_p90"] is None:
            tags.append("q6_tail_estimate_missing")
        elif (gaps["q6_tail_estimate_under_by"] or 0.0) > 0:
            tags.append("q6_tail_estimate_under")

    if _space_pressure_bucket(row) in {"space_overflow", "space_constrained"}:
        tags.append(f"q6_{_space_pressure_bucket(row)}")
    if _random_avg_bucket(row) == "signal":
        tags.append("random_avg_signal")
    if _q6_shadow_bucket(row) == "inactive_q6":
        tags.append("q6_gate_inactive")

    if "q6_value_under" in tags and not any(
        tag in tags
        for tag in (
            "q6_count_under_truth",
            "q6_cells_under_truth",
            "q6_tail_replacement_truth",
            "q6_gate_inactive",
        )
    ):
        tags.append("q6_value_distribution_under")
    if "q6_value_under" not in tags:
        tags.append("q6_value_p90_covers_truth")
    return tuple(dict.fromkeys(tags))


def _first_num(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _num(row, key)
        if value is not None:
            return value
    return None


def _top_p90_miss_examples(
    rows: list[dict[str, Any]],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    misses = [
        (under, row)
        for row in rows
        if (under := _p90_under_by(row)) is not None and under > 0
    ]
    misses.sort(key=lambda item: item[0], reverse=True)
    examples: list[dict[str, Any]] = []
    for under, row in misses[:limit]:
        q6_gaps = _q6_component_gaps(row)
        layout_bottom_row = _first_num(row, "layout_bottom_row", "footprint_bottom_row")
        baseline_p90 = _num(row, "decision_value_p90")
        practical_p90 = _num(row, "v3_practical_formal_decision_value_p90")
        practical_delta_p90 = _num(
            row,
            "v3_practical_delta_formal_decision_value_p90",
        )
        if (
            practical_delta_p90 is None
            and practical_p90 is not None
            and baseline_p90 is not None
        ):
            practical_delta_p90 = practical_p90 - baseline_p90
        aisha_deep_gap = (
            max(0, AISHA_SHIPWRECK_DEEP_ROW_THRESHOLD - int(layout_bottom_row))
            if layout_bottom_row is not None and _hero_bucket(row) == "aisha"
            else None
        )
        examples.append(
            {
                "under_by": int(under),
                "primary_error": _primary_error_bucket(row),
                "q6_components": ";".join(_q6_component_tags(row)),
                "hero": _hero_bucket(row),
                "map_id": row.get("map_id"),
                "map_family": _map_family_bucket(row),
                "round": row.get("action_round") or row.get("round"),
                "truth": _truth_value(row),
                "p90": baseline_p90,
                "v3_practical_p90": practical_p90,
                "v3_practical_under_by": _practical_p90_under_by(row),
                "v3_practical_delta_p90": practical_delta_p90,
                "v3_practical_recommendation": row.get(
                    "v3_practical_recommendation"
                ),
                "v3_practical_source": (
                    row.get("v3_practical_source_lanes")
                    or row.get("v3_practical_source")
                ),
                "v3_practical_risk_flags": row.get("v3_practical_risk_flags"),
                "layout_bottom_row": layout_bottom_row,
                "aisha_deep_threshold": (
                    AISHA_SHIPWRECK_DEEP_ROW_THRESHOLD
                    if _hero_bucket(row) == "aisha"
                    else None
                ),
                "aisha_deep_row_gap": aisha_deep_gap,
                "aisha_bottom_row_risk_threshold": (
                    AISHA_BOTTOM_ROW_RISK_THRESHOLD
                    if _hero_bucket(row) == "aisha"
                    else None
                ),
                "q6_residual_prior_floor_ratio": _num(
                    row,
                    "q6_residual_prior_floor_ratio",
                )
                or _num(
                    row,
                    "q6_formal_prior_floor_active_prior_floor_ratio",
                ),
                "q6_formal_prior_floor_active": row.get(
                    "q6_formal_prior_floor_active"
                ),
                "q6_formal_prior_floor_gate": row.get(
                    "q6_formal_prior_floor_gate"
                ),
                "q6_residual_boost": _num(row, "q6_residual_boost"),
                "q6_residual_value_power": _num(row, "q6_residual_value_power"),
                "random_sample_avg_values": row.get("random_sample_avg_values"),
                "random_sample_avg_signal_values": row.get(
                    "random_sample_avg_signal_values"
                ),
                "random_floor_mode": _random_floor_mode_bucket(row),
                "q6_truth": q6_gaps["q6_truth"],
                "q6_replacement_truth": q6_gaps["q6_replacement_truth"],
                "q6_p90": q6_gaps["q6_p90"],
                "q6_value_under_by": q6_gaps["q6_value_under_by"],
                "q6_replacement_under_by": q6_gaps["q6_replacement_under_by"],
                "q6_tail_estimate_under_by": q6_gaps["q6_tail_estimate_under_by"],
                "q6_count_truth": q6_gaps["q6_count_truth"],
                "q6_count_p90": q6_gaps["q6_count_p90"],
                "q6_count_under_by": q6_gaps["q6_count_under_by"],
                "q6_cells_truth": q6_gaps["q6_cells_truth"],
                "q6_cells_p90": q6_gaps["q6_cells_p90"],
                "q6_cells_under_by": q6_gaps["q6_cells_under_by"],
                "q6_tail_replacement_value": q6_gaps["q6_tail_replacement_value"],
                "q6_tail_estimate_p90": q6_gaps["q6_tail_estimate_p90"],
                "q6_shadow": _q6_shadow_bucket(row),
                "q6_shadow_covered_labels": _q6_shadow_labels(row, "covered"),
                "q6_shadow_active_labels": _q6_shadow_labels(row, "active"),
                "q6_shadow_active_miss_labels": _q6_shadow_labels(
                    row,
                    "active_miss",
                ),
                "space": _space_pressure_bucket(row),
                "warehouse": _warehouse_error_bucket(row),
                "sample": _sample_space_bucket(row),
                "evidence_profile": _evidence_profile_bucket(row),
                "file": row.get("file"),
                "session_id": row.get("session_id"),
            }
        )
    return examples


def _v3_practical_guard_case(row: dict[str, Any]) -> dict[str, Any] | None:
    truth = _practical_truth_value(row)
    guarded_p50 = _num(row, "decision_value_p50")
    guarded_p90 = _num(row, "decision_value_p90")
    unguarded_p50 = _practical_unguarded_value(row, "p50")
    unguarded_p90 = _practical_unguarded_value(row, "p90")
    if None in (truth, guarded_p50, guarded_p90, unguarded_p50, unguarded_p90):
        return None
    assert truth is not None
    assert guarded_p50 is not None
    assert guarded_p90 is not None
    assert unguarded_p50 is not None
    assert unguarded_p90 is not None
    denominator = _normalized_error_denominator(truth)
    guarded_p50_abs_error = abs(guarded_p50 - truth)
    unguarded_p50_abs_error = abs(unguarded_p50 - truth)
    p50_abs_error_delta = guarded_p50_abs_error - unguarded_p50_abs_error
    guarded_p90_error = guarded_p90 - truth
    unguarded_p90_error = unguarded_p90 - truth
    guarded_p90_covers = guarded_p90_error >= 0
    unguarded_p90_covers = unguarded_p90_error >= 0
    guarded_p90_extreme = guarded_p90_error > denominator
    unguarded_p90_extreme = unguarded_p90_error > denominator
    if p50_abs_error_delta < 0:
        p50_case = "p50_improved"
    elif p50_abs_error_delta > 0:
        p50_case = "p50_worsened"
    else:
        p50_case = "p50_unchanged"
    if guarded_p90_covers and not unguarded_p90_covers:
        p90_coverage_case = "p90_coverage_gained"
    elif not guarded_p90_covers and unguarded_p90_covers:
        p90_coverage_case = "p90_coverage_lost"
    else:
        p90_coverage_case = "p90_coverage_unchanged"
    if not guarded_p90_extreme and unguarded_p90_extreme:
        p90_extreme_case = "p90_extreme_over_reduced"
    elif guarded_p90_extreme and not unguarded_p90_extreme:
        p90_extreme_case = "p90_extreme_over_added"
    else:
        p90_extreme_case = "p90_extreme_over_unchanged"
    return {
        "file": row.get("file"),
        "session_id": row.get("session_id"),
        "source": _source_label(row),
        "eval_window": row.get("eval_window"),
        "round": row.get("action_round") or row.get("round"),
        "hero": _hero_bucket(row),
        "map_id": row.get("map_id"),
        "map_family": _map_family_bucket(row),
        "truth": truth,
        "guard_reason": _v3_practical_guard_reason_label(row),
        "guarded_p50": guarded_p50,
        "unguarded_p50": unguarded_p50,
        "guarded_p50_abs_error": guarded_p50_abs_error,
        "unguarded_p50_abs_error": unguarded_p50_abs_error,
        "p50_abs_error_delta": p50_abs_error_delta,
        "p50_case": p50_case,
        "guarded_p90": guarded_p90,
        "unguarded_p90": unguarded_p90,
        "guarded_p90_error": guarded_p90_error,
        "unguarded_p90_error": unguarded_p90_error,
        "p90_coverage_case": p90_coverage_case,
        "p90_extreme_over_case": p90_extreme_case,
        "evidence_profile": _evidence_profile_bucket(row),
    }


def _rate(count: int, total: int) -> float | None:
    return round(count / total, 3) if total else None


def _guard_reason_kind(reason: Any) -> str:
    text = str(reason or "").strip()
    if not text:
        return "unknown"
    return text.split(":", 1)[0].strip() or "unknown"


def _guard_reason_flags(reason: Any) -> list[str]:
    text = str(reason or "").strip()
    if not text:
        return ["none"]
    for chunk in text.split(";"):
        token = chunk.strip()
        if not token.startswith("flags="):
            continue
        flags = [
            flag.strip()
            for flag in token[len("flags=") :].split("+")
            if flag.strip()
        ]
        return flags or ["none"]
    return ["none"]


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {
        label: count
        for label, count in sorted(
            counter.items(),
            key=lambda item: (-item[1], item[0]),
        )
    }


def _v3_practical_p90_coverage_loss_context(
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    losses = [
        case for case in cases if case["p90_coverage_case"] == "p90_coverage_lost"
    ]
    shortfalls = [abs(float(case["guarded_p90_error"])) for case in losses]
    unguarded_excess = [
        float(case["unguarded_p90_error"])
        for case in losses
        if float(case["unguarded_p90_error"]) >= 0
    ]
    p90_cut = [
        float(case["unguarded_p90"]) - float(case["guarded_p90"])
        for case in losses
    ]
    flag_counts: Counter[str] = Counter()
    for case in losses:
        flag_counts.update(_guard_reason_flags(case.get("guard_reason")))
    return {
        "rows": len(losses),
        "by_guard_kind": _counter_dict(
            Counter(_guard_reason_kind(case.get("guard_reason")) for case in losses)
        ),
        "by_guard_flag": _counter_dict(flag_counts),
        "by_evidence_profile": _counter_dict(
            Counter(str(case.get("evidence_profile") or "unknown") for case in losses)
        ),
        "median_guarded_p90_shortfall": (
            round(statistics.median(shortfalls), 1) if shortfalls else None
        ),
        "median_unguarded_p90_excess": (
            round(statistics.median(unguarded_excess), 1)
            if unguarded_excess
            else None
        ),
        "median_p90_guard_cut": (
            round(statistics.median(p90_cut), 1) if p90_cut else None
        ),
    }


def _v3_practical_guard_case_summary(
    rows: list[dict[str, Any]],
    *,
    example_limit: int = 0,
) -> dict[str, Any]:
    cases = [
        case
        for row in rows
        if (case := _v3_practical_guard_case(row)) is not None
    ]
    total = len(cases)
    p50_counts = Counter(str(case["p50_case"]) for case in cases)
    coverage_counts = Counter(str(case["p90_coverage_case"]) for case in cases)
    extreme_counts = Counter(str(case["p90_extreme_over_case"]) for case in cases)

    def examples(key: str) -> list[dict[str, Any]]:
        if example_limit <= 0:
            return []
        if key == "top_p50_hurts":
            selected = sorted(
                (
                    case
                    for case in cases
                    if float(case["p50_abs_error_delta"]) > 0
                ),
                key=lambda case: float(case["p50_abs_error_delta"]),
                reverse=True,
            )
        elif key == "top_p90_coverage_losses":
            selected = sorted(
                (
                    case
                    for case in cases
                    if case["p90_coverage_case"] == "p90_coverage_lost"
                ),
                key=lambda case: abs(float(case["guarded_p90_error"])),
                reverse=True,
            )
        elif key == "top_p90_extreme_over_added":
            selected = sorted(
                (
                    case
                    for case in cases
                    if case["p90_extreme_over_case"] == "p90_extreme_over_added"
                ),
                key=lambda case: float(case["guarded_p90_error"]),
                reverse=True,
            )
        else:
            selected = []
        return selected[:example_limit]

    return {
        "rows": total,
        "p50_improved_rows": p50_counts.get("p50_improved", 0),
        "p50_worsened_rows": p50_counts.get("p50_worsened", 0),
        "p50_unchanged_rows": p50_counts.get("p50_unchanged", 0),
        "p50_improved_rate": _rate(p50_counts.get("p50_improved", 0), total),
        "p50_worsened_rate": _rate(p50_counts.get("p50_worsened", 0), total),
        "p90_coverage_gained_rows": coverage_counts.get(
            "p90_coverage_gained",
            0,
        ),
        "p90_coverage_lost_rows": coverage_counts.get(
            "p90_coverage_lost",
            0,
        ),
        "p90_coverage_unchanged_rows": coverage_counts.get(
            "p90_coverage_unchanged",
            0,
        ),
        "p90_coverage_lost_rate": _rate(
            coverage_counts.get("p90_coverage_lost", 0),
            total,
        ),
        "p90_extreme_over_reduced_rows": extreme_counts.get(
            "p90_extreme_over_reduced",
            0,
        ),
        "p90_extreme_over_added_rows": extreme_counts.get(
            "p90_extreme_over_added",
            0,
        ),
        "p90_extreme_over_unchanged_rows": extreme_counts.get(
            "p90_extreme_over_unchanged",
            0,
        ),
        "p90_extreme_over_added_rate": _rate(
            extreme_counts.get("p90_extreme_over_added", 0),
            total,
        ),
        "p90_coverage_loss_context": _v3_practical_p90_coverage_loss_context(cases),
        "top_p50_hurts": examples("top_p50_hurts"),
        "top_p90_coverage_losses": examples("top_p90_coverage_losses"),
        "top_p90_extreme_over_added": examples("top_p90_extreme_over_added"),
    }


def _group_stats(
    rows: list[dict[str, Any]],
    *,
    guard_case_examples: int = 0,
) -> dict[str, Any]:
    errors = [_decision_p50_error(row) for row in rows]
    signed = [v for v in errors if v is not None]
    clean = [abs(v) for v in signed]
    practical_errors = [_practical_p50_error(row) for row in rows]
    practical_signed = [v for v in practical_errors if v is not None]
    practical_clean = [abs(v) for v in practical_signed]
    baseline_mae = statistics.mean(clean) if clean else None
    practical_mae = statistics.mean(practical_clean) if practical_clean else None
    practical_watch_eval = _practical_raise_watch_eval(rows)
    formal_policy_comparison = _formal_policy_comparison(rows)
    guard_case_summary = _v3_practical_guard_case_summary(
        rows,
        example_limit=guard_case_examples,
    )

    def practical_watch_rate(key: str) -> float | None:
        if not practical_watch_eval:
            return None
        return statistics.mean(
            1.0 if event[key] else 0.0
            for event in practical_watch_eval
        )

    normalized_abs_errors = [
        abs(error) / _normalized_error_denominator(truth)
        for row in rows
        if (error := _decision_p50_error(row)) is not None
        and (truth := _truth_value(row)) is not None
    ]
    matched = [
        _first_num(row, "posterior_samples", "matched_samples", "v2_n_matched")
        for row in rows
    ]
    matched_clean = [v for v in matched if v is not None]
    trials = []
    for row in rows:
        prof = row.get("inference_profile") or {}
        if isinstance(prof, dict) and prof.get("n_trials") is not None:
            trials.append(float(prof["n_trials"]))
        elif row.get("monitor_n_trials") is not None:
            trials.append(float(row["monitor_n_trials"]))
        elif row.get("n_trials") is not None:
            trials.append(float(row["n_trials"]))
    p90_coverage_values: list[float] = []
    p90_signed_errors: list[float] = []
    p90_covered_excess_ratios: list[float] = []
    p90_extreme_over_values: list[float] = []
    practical_p90_coverage_values: list[float] = []
    practical_p90_signed_errors: list[float] = []
    practical_p90_covered_excess_ratios: list[float] = []
    practical_p90_extreme_over_values: list[float] = []
    unguarded_p50_errors: list[float] = []
    unguarded_p90_coverage_values: list[float] = []
    unguarded_p90_signed_errors: list[float] = []
    unguarded_p90_covered_excess_ratios: list[float] = []
    unguarded_p90_extreme_over_values: list[float] = []
    guard_compare_guarded_p50_errors: list[float] = []
    guard_compare_unguarded_p50_errors: list[float] = []
    guard_compare_p50_deltas: list[float] = []
    guard_compare_guarded_p90_coverage_values: list[float] = []
    guard_compare_unguarded_p90_coverage_values: list[float] = []
    guard_compare_guarded_p90_extreme_over_values: list[float] = []
    guard_compare_unguarded_p90_extreme_over_values: list[float] = []
    guard_compare_p90_deltas: list[float] = []
    for row in rows:
        p90 = _num(row, "decision_value_p90")
        truth = _truth_value(row)
        if p90 is not None and truth is not None:
            covered = p90 >= truth
            denominator = _normalized_error_denominator(truth)
            signed_error = p90 - truth
            p90_coverage_values.append(1.0 if covered else 0.0)
            p90_signed_errors.append(signed_error)
            if covered:
                p90_covered_excess_ratios.append(signed_error / denominator)
            p90_extreme_over_values.append(1.0 if signed_error > denominator else 0.0)
        practical_p90 = _num(row, "v3_practical_formal_decision_value_p90")
        practical_truth = _practical_truth_value(row)
        if practical_p90 is not None and practical_truth is not None:
            practical_covered = practical_p90 >= practical_truth
            practical_denominator = _normalized_error_denominator(practical_truth)
            practical_signed_error = practical_p90 - practical_truth
            practical_p90_coverage_values.append(
                1.0 if practical_covered else 0.0
            )
            practical_p90_signed_errors.append(practical_signed_error)
            if practical_covered:
                practical_p90_covered_excess_ratios.append(
                    practical_signed_error / practical_denominator
                )
            practical_p90_extreme_over_values.append(
                1.0 if practical_signed_error > practical_denominator else 0.0
            )
        unguarded_p50 = _practical_unguarded_value(row, "p50")
        unguarded_p90 = _practical_unguarded_value(row, "p90")
        guarded_p50 = _num(row, "decision_value_p50")
        guarded_p90 = _num(row, "decision_value_p90")
        if unguarded_p50 is not None and practical_truth is not None:
            unguarded_p50_errors.append(unguarded_p50 - practical_truth)
        if unguarded_p90 is not None and practical_truth is not None:
            unguarded_covered = unguarded_p90 >= practical_truth
            unguarded_denominator = _normalized_error_denominator(practical_truth)
            unguarded_signed_error = unguarded_p90 - practical_truth
            unguarded_p90_coverage_values.append(
                1.0 if unguarded_covered else 0.0
            )
            unguarded_p90_signed_errors.append(unguarded_signed_error)
            if unguarded_covered:
                unguarded_p90_covered_excess_ratios.append(
                    unguarded_signed_error / unguarded_denominator
                )
            unguarded_p90_extreme_over_values.append(
                1.0 if unguarded_signed_error > unguarded_denominator else 0.0
            )
        if (
            unguarded_p50 is not None
            and guarded_p50 is not None
            and practical_truth is not None
        ):
            guard_compare_guarded_p50_errors.append(
                guarded_p50 - practical_truth
            )
            guard_compare_unguarded_p50_errors.append(
                unguarded_p50 - practical_truth
            )
            guard_compare_p50_deltas.append(guarded_p50 - unguarded_p50)
        if (
            unguarded_p90 is not None
            and guarded_p90 is not None
            and practical_truth is not None
        ):
            guarded_p90_signed_error = guarded_p90 - practical_truth
            unguarded_p90_signed_error = unguarded_p90 - practical_truth
            guard_compare_guarded_p90_coverage_values.append(
                1.0 if guarded_p90_signed_error >= 0 else 0.0
            )
            guard_compare_unguarded_p90_coverage_values.append(
                1.0 if unguarded_p90_signed_error >= 0 else 0.0
            )
            denominator = _normalized_error_denominator(practical_truth)
            guard_compare_guarded_p90_extreme_over_values.append(
                1.0 if guarded_p90_signed_error > denominator else 0.0
            )
            guard_compare_unguarded_p90_extreme_over_values.append(
                1.0 if unguarded_p90_signed_error > denominator else 0.0
            )
            guard_compare_p90_deltas.append(guarded_p90 - unguarded_p90)
    formal_mode_counts = Counter(_formal_mode_label(row) for row in rows)
    formal_mode_reason_counts = Counter(
        _formal_mode_reason_label(row) for row in rows
    )
    v3_formal_rows = [
        row for row in rows if _formal_mode_label(row) == "v3_practical"
    ]
    v3_live_guard_rows = [
        row for row in v3_formal_rows if _is_v3_practical_live_guarded(row)
    ]
    v3_live_guard_reason_counts = Counter(
        _v3_practical_guard_reason_label(row) for row in v3_live_guard_rows
    )
    unguarded_p50_abs_errors = [abs(value) for value in unguarded_p50_errors]
    guard_compare_guarded_abs_errors = [
        abs(value) for value in guard_compare_guarded_p50_errors
    ]
    guard_compare_unguarded_abs_errors = [
        abs(value) for value in guard_compare_unguarded_p50_errors
    ]
    guard_compare_guarded_mae = (
        statistics.mean(guard_compare_guarded_abs_errors)
        if guard_compare_guarded_abs_errors
        else None
    )
    guard_compare_unguarded_mae = (
        statistics.mean(guard_compare_unguarded_abs_errors)
        if guard_compare_unguarded_abs_errors
        else None
    )
    return {
        "rows": len(rows),
        "estimate_rows": len(signed),
        "formal_mode_counts": dict(sorted(formal_mode_counts.items())),
        "formal_mode_reason_counts": dict(sorted(formal_mode_reason_counts.items())),
        "v3_practical_formal_rows": len(v3_formal_rows),
        "v3_practical_live_guard_rows": len(v3_live_guard_rows),
        "v3_practical_live_guard_rate": (
            round(len(v3_live_guard_rows) / len(v3_formal_rows), 2)
            if v3_formal_rows
            else None
        ),
        "v3_practical_live_guard_reason_counts": dict(
            sorted(v3_live_guard_reason_counts.items())
        ),
        "v3_practical_unguarded_rows": len(unguarded_p50_errors),
        "v3_practical_unguarded_mae": (
            round(statistics.mean(unguarded_p50_abs_errors), 1)
            if unguarded_p50_abs_errors
            else None
        ),
        "v3_practical_unguarded_under_rate": (
            round(
                statistics.mean(
                    1.0 if value < 0 else 0.0 for value in unguarded_p50_errors
                ),
                2,
            )
            if unguarded_p50_errors
            else None
        ),
        "v3_practical_unguarded_p90_coverage": (
            round(statistics.mean(unguarded_p90_coverage_values), 2)
            if unguarded_p90_coverage_values
            else None
        ),
        "v3_practical_unguarded_p90_extreme_over_rate": (
            round(statistics.mean(unguarded_p90_extreme_over_values), 2)
            if unguarded_p90_extreme_over_values
            else None
        ),
        "v3_practical_guard_comparison_rows": len(
            guard_compare_guarded_p50_errors
        ),
        "v3_practical_guarded_mae_on_comparison": (
            round(guard_compare_guarded_mae, 1)
            if guard_compare_guarded_mae is not None
            else None
        ),
        "v3_practical_unguarded_mae_on_comparison": (
            round(guard_compare_unguarded_mae, 1)
            if guard_compare_unguarded_mae is not None
            else None
        ),
        "v3_practical_guarded_minus_unguarded_mae": (
            round(guard_compare_guarded_mae - guard_compare_unguarded_mae, 1)
            if guard_compare_guarded_mae is not None
            and guard_compare_unguarded_mae is not None
            else None
        ),
        "v3_practical_guarded_minus_unguarded_median_p50": (
            round(statistics.median(guard_compare_p50_deltas), 1)
            if guard_compare_p50_deltas
            else None
        ),
        "v3_practical_guarded_minus_unguarded_median_p90": (
            round(statistics.median(guard_compare_p90_deltas), 1)
            if guard_compare_p90_deltas
            else None
        ),
        "v3_practical_guarded_p90_coverage_on_comparison": (
            round(statistics.mean(guard_compare_guarded_p90_coverage_values), 2)
            if guard_compare_guarded_p90_coverage_values
            else None
        ),
        "v3_practical_unguarded_p90_coverage_on_comparison": (
            round(statistics.mean(guard_compare_unguarded_p90_coverage_values), 2)
            if guard_compare_unguarded_p90_coverage_values
            else None
        ),
        "v3_practical_guarded_minus_unguarded_p90_coverage": (
            round(
                statistics.mean(guard_compare_guarded_p90_coverage_values)
                - statistics.mean(guard_compare_unguarded_p90_coverage_values),
                2,
            )
            if guard_compare_guarded_p90_coverage_values
            and guard_compare_unguarded_p90_coverage_values
            else None
        ),
        "v3_practical_guarded_p90_extreme_over_on_comparison": (
            round(
                statistics.mean(guard_compare_guarded_p90_extreme_over_values),
                2,
            )
            if guard_compare_guarded_p90_extreme_over_values
            else None
        ),
        "v3_practical_unguarded_p90_extreme_over_on_comparison": (
            round(
                statistics.mean(guard_compare_unguarded_p90_extreme_over_values),
                2,
            )
            if guard_compare_unguarded_p90_extreme_over_values
            else None
        ),
        "v3_practical_guarded_minus_unguarded_p90_extreme_over": (
            round(
                statistics.mean(guard_compare_guarded_p90_extreme_over_values)
                - statistics.mean(guard_compare_unguarded_p90_extreme_over_values),
                2,
            )
            if guard_compare_guarded_p90_extreme_over_values
            and guard_compare_unguarded_p90_extreme_over_values
            else None
        ),
        "v3_practical_guard_case_summary": guard_case_summary,
        "formal_policy_comparison": formal_policy_comparison,
        "median_matched": int(statistics.median(matched_clean)) if matched_clean else None,
        "decision_value_mae": round(baseline_mae, 1) if baseline_mae is not None else None,
        "median_p50_error": round(statistics.median(signed), 1) if signed else None,
        "median_abs_p50_error": round(statistics.median(clean), 1) if clean else None,
        "median_normalized_abs_p50_error": (
            round(statistics.median(normalized_abs_errors), 3)
            if normalized_abs_errors
            else None
        ),
        "p50_under_rate": round(
            statistics.mean(1.0 if value < 0 else 0.0 for value in signed),
            2,
        )
        if signed
        else None,
        "p90_coverage": round(statistics.mean(p90_coverage_values), 2)
        if p90_coverage_values
        else None,
        "median_p90_signed_error": (
            round(statistics.median(p90_signed_errors), 1)
            if p90_signed_errors
            else None
        ),
        "median_p90_covered_excess_ratio": (
            round(statistics.median(p90_covered_excess_ratios), 3)
            if p90_covered_excess_ratios
            else None
        ),
        "p90_extreme_over_rate": (
            round(statistics.mean(p90_extreme_over_values), 2)
            if p90_extreme_over_values
            else None
        ),
        "median_n_trials": int(statistics.median(trials)) if trials else None,
        "size_bucket_active_rate": round(
            statistics.mean(1.0 if row.get("size_bucket_active") else 0.0 for row in rows),
            2,
        )
        if rows
        else None,
        "v3_practical_candidate_rate": round(
            statistics.mean(
                1.0 if row.get("v3_practical_candidate") else 0.0
                for row in rows
            ),
            2,
        )
        if rows
        else None,
        "v3_practical_raise_watch_rate": round(
            statistics.mean(
                1.0
                if row.get("v3_practical_recommendation") == "raise_watch"
                else 0.0
                for row in rows
            ),
            2,
        )
        if rows
        else None,
        "v3_practical_mae": (
            round(practical_mae, 1) if practical_mae is not None else None
        ),
        "v3_practical_delta_mae": (
            round(practical_mae - baseline_mae, 1)
            if practical_mae is not None and baseline_mae is not None
            else None
        ),
        "v3_practical_under_rate": round(
            statistics.mean(1.0 if value < 0 else 0.0 for value in practical_signed),
            2,
        )
        if practical_signed
        else None,
        "v3_practical_p90_coverage": (
            round(statistics.mean(practical_p90_coverage_values), 2)
            if practical_p90_coverage_values
            else None
        ),
        "v3_practical_median_p90_signed_error": (
            round(statistics.median(practical_p90_signed_errors), 1)
            if practical_p90_signed_errors
            else None
        ),
        "v3_practical_p90_covered_excess_ratio": (
            round(statistics.median(practical_p90_covered_excess_ratios), 3)
            if practical_p90_covered_excess_ratios
            else None
        ),
        "v3_practical_p90_extreme_over_rate": (
            round(statistics.mean(practical_p90_extreme_over_values), 2)
            if practical_p90_extreme_over_values
            else None
        ),
        "v3_practical_raise_watch_evaluable_rows": len(practical_watch_eval),
        "v3_practical_raise_watch_hit_rate": (
            round(rate, 2)
            if (rate := practical_watch_rate("hit")) is not None
            else None
        ),
        "v3_practical_raise_watch_miss_rate": (
            round(rate, 2)
            if (rate := practical_watch_rate("miss")) is not None
            else None
        ),
        "v3_practical_raise_watch_false_alarm_rate": (
            round(rate, 2)
            if (rate := practical_watch_rate("false_alarm")) is not None
            else None
        ),
        "v3_practical_raise_watch_extreme_over_rate": (
            round(rate, 2)
            if (rate := practical_watch_rate("extreme_over")) is not None
            else None
        ),
        "v3_practical_raise_watch_misleading_rate": (
            round(rate, 2)
            if (rate := practical_watch_rate("misleading")) is not None
            else None
        ),
    }


def _prebid_window_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    session_rounds: dict[str, set[int]] = defaultdict(set)
    round_counts: Counter[str] = Counter()
    ready_round_counts: Counter[str] = Counter()
    for row in rows:
        label = _round_bucket(row, "eval_window_round")
        round_counts[label] += 1
        if _truthy(row.get("window_ready_for_accuracy")):
            ready_round_counts[label] += 1
        session_key = _prebid_session_key(row)
        try:
            round_no = int(row.get("eval_window_round") or 0)
        except (TypeError, ValueError):
            round_no = 0
        if round_no > 0:
            session_rounds[session_key].add(round_no)
    return {
        "windows": len(rows),
        "sessions": len(session_rounds),
        "five_window_sessions": sum(
            1 for rounds in session_rounds.values() if set(range(1, 6)).issubset(rounds)
        ),
        "round_counts": dict(sorted(round_counts.items())),
        "ready_round_counts": dict(sorted(ready_round_counts.items())),
        "ready_windows": sum(
            1 for row in rows if _truthy(row.get("window_ready_for_accuracy"))
        ),
        "no_prebid_state_windows": sum(
            1 for row in rows if not _truthy(row.get("window_has_prebid_state"))
        ),
        "no_estimate_windows": sum(
            1 for row in rows if not _truthy(row.get("window_has_estimate"))
        ),
        "action_result_not_ready_windows": sum(
            1 for row in rows if not _truthy(row.get("window_action_result_ready"))
        ),
        "round_mismatch_windows": sum(
            1 for row in rows if not _truthy(row.get("window_round_matches_artifact"))
        ),
    }


def _prebid_session_key(row: dict[str, Any]) -> str:
    return str(
        row.get("session_id")
        or row.get("archive_path")
        or row.get("file")
        or id(row)
    )


def _prebid_session_progression(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if _decision_p50_error(row) is not None:
            sessions[_prebid_session_key(row)].append(row)
    transitions: list[dict[str, Any]] = []
    for session_rows in sessions.values():
        ordered = sorted(
            session_rows,
            key=lambda row: int(row.get("eval_window_round") or 0),
        )
        for previous, current in zip(ordered, ordered[1:]):
            previous_round = int(previous.get("eval_window_round") or 0)
            current_round = int(current.get("eval_window_round") or 0)
            if previous_round <= 0 or current_round != previous_round + 1:
                continue
            previous_error = _decision_p50_error(previous)
            current_error = _decision_p50_error(current)
            if previous_error is None or current_error is None:
                continue
            previous_p50 = _num(previous, "decision_value_p50")
            current_p50 = _num(current, "decision_value_p50")
            previous_p90 = _num(previous, "decision_value_p90")
            current_p90 = _num(current, "decision_value_p90")
            previous_width = (
                previous_p90 - previous_p50
                if previous_p90 is not None and previous_p50 is not None
                else None
            )
            current_width = (
                current_p90 - current_p50
                if current_p90 is not None and current_p50 is not None
                else None
            )
            transitions.append(
                {
                    "label": f"R{previous_round}->R{current_round}",
                    "p50_abs_error_delta": abs(current_error) - abs(previous_error),
                    "p90_coverage_lost": (
                        _p90_covers(previous) is True and _p90_covers(current) is False
                    ),
                    "p90_width_delta": (
                        current_width - previous_width
                        if current_width is not None and previous_width is not None
                        else None
                    ),
                }
            )

    def summarize_transitions(group: list[dict[str, Any]]) -> dict[str, Any]:
        error_deltas = [float(row["p50_abs_error_delta"]) for row in group]
        width_deltas = [
            float(row["p90_width_delta"])
            for row in group
            if row.get("p90_width_delta") is not None
        ]
        return {
            "transitions": len(group),
            "p50_abs_error_improved_or_equal_rate": (
                round(
                    statistics.mean(1.0 if delta <= 0 else 0.0 for delta in error_deltas),
                    2,
                )
                if error_deltas
                else None
            ),
            "p50_abs_error_worsened_transitions": sum(
                1 for delta in error_deltas if delta > 0
            ),
            "median_p50_abs_error_delta": (
                round(statistics.median(error_deltas), 1)
                if error_deltas
                else None
            ),
            "p90_coverage_lost_transitions": sum(
                1 for row in group if row.get("p90_coverage_lost")
            ),
            "p90_width_shrunk_or_equal_rate": (
                round(
                    statistics.mean(1.0 if delta <= 0 else 0.0 for delta in width_deltas),
                    2,
                )
                if width_deltas
                else None
            ),
        }

    by_transition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for transition in transitions:
        by_transition[str(transition["label"])].append(transition)
    return {
        **summarize_transitions(transitions),
        "by_transition": {
            label: summarize_transitions(group)
            for label, group in sorted(by_transition.items())
        },
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_observed_round: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_action_round: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_q6_truth: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_random_avg: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_random_floor_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_q6_shadow: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_warehouse_error: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_primary_error: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_diagnostic_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_q6_component_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_hero: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_map_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_map_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_evidence_profile: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_public_constraint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_information_density: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_constraint_density: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_sample_space: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_space_pressure: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_tail: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_observed_round[_round_bucket(row, "round")].append(row)
        by_action_round[_round_bucket(row, "action_round")].append(row)
        by_q6_truth[_q6_truth_bucket(row)].append(row)
        by_random_avg[_random_avg_bucket(row)].append(row)
        by_random_floor_mode[_random_floor_mode_bucket(row)].append(row)
        by_q6_shadow[_q6_shadow_bucket(row)].append(row)
        by_warehouse_error[_warehouse_error_bucket(row)].append(row)
        by_primary_error[_primary_error_bucket(row)].append(row)
        for tag in _diagnostic_tags(row):
            by_diagnostic_tag[tag].append(row)
        for tag in _q6_component_tags(row):
            by_q6_component_tag[tag].append(row)
        by_hero[_hero_bucket(row)].append(row)
        by_map_id[_map_id_bucket(row)].append(row)
        by_map_family[_map_family_bucket(row)].append(row)
        by_evidence_profile[_evidence_profile_bucket(row)].append(row)
        by_public_constraint[_public_constraint_bucket(row)].append(row)
        by_information_density[_information_density_bucket(row)].append(row)
        by_constraint_density[_constraint_density_bucket(row)].append(row)
        by_sample_space[_sample_space_bucket(row)].append(row)
        by_space_pressure[_space_pressure_bucket(row)].append(row)
        by_tail[_tail_bucket(row)].append(row)
    prebid_rows = [row for row in rows if row.get("eval_window") == "pre_bid"]
    prebid_by_round: dict[str, list[dict[str, Any]]] = defaultdict(list)
    prebid_by_round_q6_truth: dict[str, dict[str, list[dict[str, Any]]]] = (
        defaultdict(lambda: defaultdict(list))
    )
    for row in prebid_rows:
        round_label = _round_bucket(row, "eval_window_round")
        prebid_by_round[round_label].append(row)
        prebid_by_round_q6_truth[round_label][_q6_truth_bucket(row)].append(row)
    return {
        "total_rows": len(rows),
        "source_counts": dict(Counter(_source_label(row) for row in rows)),
        "overall": _group_stats(rows, guard_case_examples=5),
        "prebid_overall": _group_stats(prebid_rows, guard_case_examples=5),
        "prebid_window_audit": _prebid_window_audit(prebid_rows),
        "prebid_session_progression": _prebid_session_progression(prebid_rows),
        "prebid_by_round": {
            label: _group_stats(group)
            for label, group in sorted(prebid_by_round.items())
        },
        "prebid_by_round_q6_truth": {
            round_label: {
                q6_label: _group_stats(group)
                for q6_label, group in sorted(q6_groups.items())
            }
            for round_label, q6_groups in sorted(prebid_by_round_q6_truth.items())
        },
        "by_observed_round": {
            label: _group_stats(group)
            for label, group in sorted(by_observed_round.items())
        },
        "by_action_round": {
            label: _group_stats(group)
            for label, group in sorted(by_action_round.items())
        },
        "by_round": {
            label: _group_stats(group)
            for label, group in sorted(by_action_round.items())
        },
        "by_q6_truth": {
            label: _group_stats(group)
            for label, group in sorted(by_q6_truth.items())
        },
        "by_random_avg": {
            label: _group_stats(group)
            for label, group in sorted(by_random_avg.items())
        },
        "by_random_floor_mode": {
            label: _group_stats(group)
            for label, group in sorted(by_random_floor_mode.items())
        },
        "by_q6_shadow": {
            label: _group_stats(group)
            for label, group in sorted(by_q6_shadow.items())
        },
        "by_warehouse_error": {
            label: _group_stats(group)
            for label, group in sorted(by_warehouse_error.items())
        },
        "by_primary_error": {
            label: _group_stats(group)
            for label, group in sorted(by_primary_error.items())
        },
        "by_diagnostic_tag": {
            label: _group_stats(group)
            for label, group in sorted(by_diagnostic_tag.items())
        },
        "by_q6_component_tag": {
            label: _group_stats(group)
            for label, group in sorted(by_q6_component_tag.items())
        },
        "by_hero": {
            label: _group_stats(group)
            for label, group in sorted(by_hero.items())
        },
        "by_map_id": {
            label: _group_stats(group)
            for label, group in sorted(by_map_id.items())
        },
        "by_map_family": {
            label: _group_stats(group)
            for label, group in sorted(by_map_family.items())
        },
        "by_evidence_profile": {
            label: _group_stats(group)
            for label, group in sorted(by_evidence_profile.items())
        },
        "by_public_constraint": {
            label: _group_stats(group)
            for label, group in sorted(by_public_constraint.items())
        },
        "by_information_density": {
            label: _group_stats(group)
            for label, group in sorted(by_information_density.items())
        },
        "by_constraint_density": {
            label: _group_stats(group)
            for label, group in sorted(by_constraint_density.items())
        },
        "by_sample_space": {
            label: _group_stats(group)
            for label, group in sorted(by_sample_space.items())
        },
        "by_space_pressure": {
            label: _group_stats(group)
            for label, group in sorted(by_space_pressure.items())
        },
        "by_tail": {
            label: _group_stats(group)
            for label, group in sorted(by_tail.items())
        },
        "top_p90_misses": _top_p90_miss_examples(rows),
    }


def _print_report(
    summary: dict[str, Any],
    *,
    detail_groups: bool = False,
) -> None:
    print(f"windivert_rows={summary['total_rows']}")
    source_counts = summary.get("source_counts") or {}
    if source_counts:
        print(
            "sources: "
            + " ".join(
                f"{label}={count}"
                for label, count in sorted(source_counts.items())
            )
        )
    overall = summary["overall"]
    overall_policy = overall.get("formal_policy_comparison") or {}
    overall_policy_deltas = overall_policy.get("deltas_vs_v2") or {}
    overall_guarded_delta = overall_policy_deltas.get("v3_guarded") or {}
    overall_unguarded_delta = overall_policy_deltas.get("v3_unguarded") or {}
    overall_guard_cases = overall.get("v3_practical_guard_case_summary") or {}
    print(
        "overall: "
        f"estimate_rows={overall.get('estimate_rows')} "
        f"median_matched={overall.get('median_matched')} "
        f"decision_mae={overall.get('decision_value_mae')} "
        f"median_p50_err={overall.get('median_p50_error')} "
        f"median_abs_p50_err={overall.get('median_abs_p50_error')} "
        f"median_norm_abs_p50_err={overall.get('median_normalized_abs_p50_error')} "
        f"p50_under_rate={overall.get('p50_under_rate')} "
        f"p90_coverage={overall.get('p90_coverage')} "
        f"p90_covered_excess_ratio={overall.get('median_p90_covered_excess_ratio')} "
        f"p90_extreme_over_rate={overall.get('p90_extreme_over_rate')} "
        f"median_n_trials={overall.get('median_n_trials')} "
        f"formal_modes={overall.get('formal_mode_counts')} "
        f"v3_practical_formal_rows={overall.get('v3_practical_formal_rows')} "
        f"v3_practical_live_guard_rows={overall.get('v3_practical_live_guard_rows')} "
        f"v3_practical_live_guard_rate={overall.get('v3_practical_live_guard_rate')} "
        f"v3_practical_unguarded_rows={overall.get('v3_practical_unguarded_rows')} "
        "v3_practical_guarded_minus_unguarded_mae="
        f"{overall.get('v3_practical_guarded_minus_unguarded_mae')} "
        "v3_practical_guarded_minus_unguarded_p90_coverage="
        f"{overall.get('v3_practical_guarded_minus_unguarded_p90_coverage')} "
        "v3_practical_guarded_minus_unguarded_p90_extreme_over="
        f"{overall.get('v3_practical_guarded_minus_unguarded_p90_extreme_over')} "
        "v3_practical_guard_p50_worsened_rows="
        f"{overall_guard_cases.get('p50_worsened_rows')} "
        "v3_practical_guard_p90_coverage_lost_rows="
        f"{overall_guard_cases.get('p90_coverage_lost_rows')} "
        "v3_practical_guard_p90_extreme_over_added_rows="
        f"{overall_guard_cases.get('p90_extreme_over_added_rows')} "
        f"formal_policy_rows={overall_policy.get('comparison_rows')} "
        f"v3_guarded_vs_v2_mae_delta={overall_guarded_delta.get('p50_mae_delta')} "
        "v3_guarded_vs_v2_p90_coverage_delta="
        f"{overall_guarded_delta.get('p90_coverage_delta')} "
        f"v3_unguarded_vs_v2_mae_delta={overall_unguarded_delta.get('p50_mae_delta')} "
        "v3_unguarded_vs_v2_p90_coverage_delta="
        f"{overall_unguarded_delta.get('p90_coverage_delta')} "
        f"size_bucket_rate={overall.get('size_bucket_active_rate')} "
        f"v3_practical_p90_coverage={overall.get('v3_practical_p90_coverage')} "
        "v3_practical_p90_extreme_over_rate="
        f"{overall.get('v3_practical_p90_extreme_over_rate')}"
    )
    print()
    prebid = summary.get("prebid_overall") or {}
    prebid_policy = prebid.get("formal_policy_comparison") or {}
    prebid_policy_deltas = prebid_policy.get("deltas_vs_v2") or {}
    prebid_guarded_delta = prebid_policy_deltas.get("v3_guarded") or {}
    prebid_unguarded_delta = prebid_policy_deltas.get("v3_unguarded") or {}
    prebid_guard_cases = prebid.get("v3_practical_guard_case_summary") or {}
    print(
        "prebid_overall: "
        f"rows={prebid.get('rows')} "
        f"estimate_rows={prebid.get('estimate_rows')} "
        f"decision_mae={prebid.get('decision_value_mae')} "
        f"median_abs_p50_err={prebid.get('median_abs_p50_error')} "
        f"median_norm_abs_p50_err={prebid.get('median_normalized_abs_p50_error')} "
        f"p50_under_rate={prebid.get('p50_under_rate')} "
        f"p90_coverage={prebid.get('p90_coverage')} "
        f"p90_covered_excess_ratio={prebid.get('median_p90_covered_excess_ratio')} "
        f"p90_extreme_over_rate={prebid.get('p90_extreme_over_rate')} "
        f"formal_modes={prebid.get('formal_mode_counts')} "
        f"v3_practical_formal_rows={prebid.get('v3_practical_formal_rows')} "
        f"v3_practical_live_guard_rows={prebid.get('v3_practical_live_guard_rows')} "
        f"v3_practical_live_guard_rate={prebid.get('v3_practical_live_guard_rate')} "
        f"v3_practical_unguarded_rows={prebid.get('v3_practical_unguarded_rows')} "
        "v3_practical_guarded_minus_unguarded_mae="
        f"{prebid.get('v3_practical_guarded_minus_unguarded_mae')} "
        "v3_practical_guarded_minus_unguarded_p90_coverage="
        f"{prebid.get('v3_practical_guarded_minus_unguarded_p90_coverage')} "
        "v3_practical_guarded_minus_unguarded_p90_extreme_over="
        f"{prebid.get('v3_practical_guarded_minus_unguarded_p90_extreme_over')} "
        "v3_practical_guard_p50_worsened_rows="
        f"{prebid_guard_cases.get('p50_worsened_rows')} "
        "v3_practical_guard_p90_coverage_lost_rows="
        f"{prebid_guard_cases.get('p90_coverage_lost_rows')} "
        "v3_practical_guard_p90_extreme_over_added_rows="
        f"{prebid_guard_cases.get('p90_extreme_over_added_rows')} "
        f"formal_policy_rows={prebid_policy.get('comparison_rows')} "
        f"v3_guarded_vs_v2_mae_delta={prebid_guarded_delta.get('p50_mae_delta')} "
        "v3_guarded_vs_v2_p90_coverage_delta="
        f"{prebid_guarded_delta.get('p90_coverage_delta')} "
        f"v3_unguarded_vs_v2_mae_delta={prebid_unguarded_delta.get('p50_mae_delta')} "
        "v3_unguarded_vs_v2_p90_coverage_delta="
        f"{prebid_unguarded_delta.get('p90_coverage_delta')} "
        f"v3_practical_p90_coverage={prebid.get('v3_practical_p90_coverage')} "
        "v3_practical_p90_extreme_over_rate="
        f"{prebid.get('v3_practical_p90_extreme_over_rate')}"
    )
    print()
    _print_prebid_audit(summary.get("prebid_window_audit") or {})
    print()
    _print_prebid_progression(summary.get("prebid_session_progression") or {})
    print()
    _print_round_groups("prebid_round", summary["prebid_by_round"])
    print()
    _print_prebid_round_q6_groups(summary["prebid_by_round_q6_truth"])
    print()
    _print_round_groups("observed_round", summary["by_observed_round"])
    print()
    _print_round_groups("action_round", summary["by_action_round"])
    print()
    _print_round_groups("q6_truth", summary["by_q6_truth"])
    print()
    _print_round_groups("random_avg", summary["by_random_avg"])
    print()
    _print_round_groups("random_floor_mode", summary["by_random_floor_mode"])
    print()
    _print_round_groups("q6_shadow", summary["by_q6_shadow"])
    print()
    _print_round_groups("warehouse_p50_error", summary["by_warehouse_error"])
    print()
    _print_round_groups("primary_error", summary["by_primary_error"])
    print()
    _print_round_groups("q6_component", summary["by_q6_component_tag"])
    print()
    if detail_groups:
        _print_round_groups("diagnostic_tag_multi", summary["by_diagnostic_tag"])
        print()
        _print_round_groups("hero", summary["by_hero"])
        print()
        _print_round_groups("map_id", summary["by_map_id"])
        print()
        _print_round_groups("map_family", summary["by_map_family"])
        print()
        _print_round_groups("evidence_profile", summary["by_evidence_profile"])
        print()
        _print_round_groups("public_constraint", summary["by_public_constraint"])
        print()
        _print_round_groups("information_density", summary["by_information_density"])
        print()
        _print_round_groups("constraint_density", summary["by_constraint_density"])
        print()
        _print_round_groups("sample_space", summary["by_sample_space"])
        print()
        _print_round_groups("q6_space_pressure", summary["by_space_pressure"])
        print()
        _print_round_groups("tail", summary["by_tail"])
        print()
    _print_miss_examples(summary.get("top_p90_misses") or [])


def _print_round_groups(label: str, groups: dict[str, Any]) -> None:
    print(
        f"{label},rows,estimate_rows,median_matched,decision_mae,median_p50_err,"
        "median_abs_p50_err,median_norm_abs_p50_err,p50_under_rate,p90_coverage,"
        "median_p90_signed_err,p90_covered_excess_ratio,p90_extreme_over_rate,"
        "median_n_trials,v3_practical_formal_rows,v3_practical_live_guard_rows,"
        "v3_practical_live_guard_rate,v3_practical_unguarded_rows,"
        "v3_practical_guarded_minus_unguarded_mae,"
        "v3_practical_guarded_minus_unguarded_median_p50,"
        "v3_practical_guarded_minus_unguarded_median_p90,"
        "v3_practical_guarded_minus_unguarded_p90_coverage,"
        "v3_practical_guarded_minus_unguarded_p90_extreme_over,"
        "size_bucket_rate,v3_practical_candidate_rate,"
        "v3_practical_raise_watch_rate,v3_practical_mae,"
        "v3_practical_delta_mae,v3_practical_under_rate,"
        "v3_practical_p90_coverage,v3_practical_p90_extreme_over_rate,"
        "v3_practical_raise_watch_evaluable_rows,"
        "v3_practical_raise_watch_hit_rate,"
        "v3_practical_raise_watch_miss_rate,"
        "v3_practical_raise_watch_false_alarm_rate,"
        "v3_practical_raise_watch_extreme_over_rate,"
        "v3_practical_raise_watch_misleading_rate,"
        "v3_practical_guard_p50_worsened_rows,"
        "v3_practical_guard_p90_coverage_lost_rows,"
        "v3_practical_guard_p90_extreme_over_added_rows"
    )
    for round_label, group in groups.items():
        guard_cases = group.get("v3_practical_guard_case_summary") or {}
        print(
            f"{round_label},"
            f"{group['rows']},"
            f"{group.get('estimate_rows')},"
            f"{group.get('median_matched')},"
            f"{group.get('decision_value_mae')},"
            f"{group.get('median_p50_error')},"
            f"{group.get('median_abs_p50_error')},"
            f"{group.get('median_normalized_abs_p50_error')},"
            f"{group.get('p50_under_rate')},"
            f"{group.get('p90_coverage')},"
            f"{group.get('median_p90_signed_error')},"
            f"{group.get('median_p90_covered_excess_ratio')},"
            f"{group.get('p90_extreme_over_rate')},"
            f"{group.get('median_n_trials')},"
            f"{group.get('v3_practical_formal_rows')},"
            f"{group.get('v3_practical_live_guard_rows')},"
            f"{group.get('v3_practical_live_guard_rate')},"
            f"{group.get('v3_practical_unguarded_rows')},"
            f"{group.get('v3_practical_guarded_minus_unguarded_mae')},"
            f"{group.get('v3_practical_guarded_minus_unguarded_median_p50')},"
            f"{group.get('v3_practical_guarded_minus_unguarded_median_p90')},"
            f"{group.get('v3_practical_guarded_minus_unguarded_p90_coverage')},"
            f"{group.get('v3_practical_guarded_minus_unguarded_p90_extreme_over')},"
            f"{group.get('size_bucket_active_rate')},"
            f"{group.get('v3_practical_candidate_rate')},"
            f"{group.get('v3_practical_raise_watch_rate')},"
            f"{group.get('v3_practical_mae')},"
            f"{group.get('v3_practical_delta_mae')},"
            f"{group.get('v3_practical_under_rate')},"
            f"{group.get('v3_practical_p90_coverage')},"
            f"{group.get('v3_practical_p90_extreme_over_rate')},"
            f"{group.get('v3_practical_raise_watch_evaluable_rows')},"
            f"{group.get('v3_practical_raise_watch_hit_rate')},"
            f"{group.get('v3_practical_raise_watch_miss_rate')},"
            f"{group.get('v3_practical_raise_watch_false_alarm_rate')},"
            f"{group.get('v3_practical_raise_watch_extreme_over_rate')},"
            f"{group.get('v3_practical_raise_watch_misleading_rate')},"
            f"{guard_cases.get('p50_worsened_rows')},"
            f"{guard_cases.get('p90_coverage_lost_rows')},"
            f"{guard_cases.get('p90_extreme_over_added_rows')}"
        )


def _print_prebid_audit(audit: dict[str, Any]) -> None:
    print(
        "prebid_window_audit: "
        f"sessions={audit.get('sessions')} "
        f"five_window_sessions={audit.get('five_window_sessions')} "
        f"windows={audit.get('windows')} "
        f"ready={audit.get('ready_windows')} "
        f"no_state={audit.get('no_prebid_state_windows')} "
        f"no_estimate={audit.get('no_estimate_windows')} "
        f"action_result_not_ready={audit.get('action_result_not_ready_windows')} "
        f"round_mismatch={audit.get('round_mismatch_windows')} "
        f"round_counts={audit.get('round_counts')} "
        f"ready_round_counts={audit.get('ready_round_counts')}"
    )


def _print_prebid_progression(progression: dict[str, Any]) -> None:
    print(
        "prebid_session_progression: "
        f"transitions={progression.get('transitions')} "
        f"p50_improved_or_equal_rate={progression.get('p50_abs_error_improved_or_equal_rate')} "
        f"p50_worsened={progression.get('p50_abs_error_worsened_transitions')} "
        f"median_p50_abs_error_delta={progression.get('median_p50_abs_error_delta')} "
        f"p90_coverage_lost={progression.get('p90_coverage_lost_transitions')} "
        f"p90_width_shrunk_or_equal_rate={progression.get('p90_width_shrunk_or_equal_rate')} "
        f"by_transition={progression.get('by_transition')}"
    )


def _print_prebid_round_q6_groups(groups: dict[str, dict[str, Any]]) -> None:
    flat = {
        f"{round_label}|{q6_label}": stats
        for round_label, q6_groups in groups.items()
        for q6_label, stats in q6_groups.items()
    }
    _print_round_groups("prebid_round_q6", flat)


def _print_miss_examples(examples: list[dict[str, Any]]) -> None:
    print(
        "top_p90_misses,under_by,primary_error,q6_components,hero,map_id,"
        "map_family,round,"
        "truth,p90,v3_practical_p90,v3_practical_under_by,"
        "v3_practical_delta_p90,v3_practical_recommendation,"
        "v3_practical_source,v3_practical_risk_flags,"
        "layout_bottom_row,aisha_deep_threshold,aisha_deep_row_gap,"
        "q6_prior_floor_ratio,q6_formal_floor_active,q6_formal_floor_gate,"
        "random_floor_mode,q6_truth,q6_replacement_truth,"
        "q6_p90,q6_value_under_by,"
        "q6_replacement_under_by,q6_tail_estimate_under_by,q6_count_truth,"
        "q6_count_p90,q6_count_under_by,q6_cells_truth,q6_cells_p90,"
        "q6_cells_under_by,q6_tail_replacement_value,q6_tail_estimate_p90,"
        "q6_shadow,q6_shadow_covered_labels,q6_shadow_active_labels,"
        "q6_shadow_active_miss_labels,space,warehouse,sample,evidence_profile,file"
    )
    for index, row in enumerate(examples, start=1):
        print(
            f"{index},"
            f"{row.get('under_by')},"
            f"{row.get('primary_error')},"
            f"{row.get('q6_components')},"
            f"{row.get('hero')},"
            f"{row.get('map_id')},"
            f"{row.get('map_family')},"
            f"{row.get('round')},"
            f"{row.get('truth')},"
            f"{row.get('p90')},"
            f"{row.get('v3_practical_p90')},"
            f"{row.get('v3_practical_under_by')},"
            f"{row.get('v3_practical_delta_p90')},"
            f"{row.get('v3_practical_recommendation')},"
            f"{row.get('v3_practical_source')},"
            f"{row.get('v3_practical_risk_flags')},"
            f"{row.get('layout_bottom_row')},"
            f"{row.get('aisha_deep_threshold')},"
            f"{row.get('aisha_deep_row_gap')},"
            f"{row.get('q6_residual_prior_floor_ratio')},"
            f"{row.get('q6_formal_prior_floor_active')},"
            f"{row.get('q6_formal_prior_floor_gate')},"
            f"{row.get('random_floor_mode')},"
            f"{row.get('q6_truth')},"
            f"{row.get('q6_replacement_truth')},"
            f"{row.get('q6_p90')},"
            f"{row.get('q6_value_under_by')},"
            f"{row.get('q6_replacement_under_by')},"
            f"{row.get('q6_tail_estimate_under_by')},"
            f"{row.get('q6_count_truth')},"
            f"{row.get('q6_count_p90')},"
            f"{row.get('q6_count_under_by')},"
            f"{row.get('q6_cells_truth')},"
            f"{row.get('q6_cells_p90')},"
            f"{row.get('q6_cells_under_by')},"
            f"{row.get('q6_tail_replacement_value')},"
            f"{row.get('q6_tail_estimate_p90')},"
            f"{row.get('q6_shadow')},"
            f"{row.get('q6_shadow_covered_labels')},"
            f"{row.get('q6_shadow_active_labels')},"
            f"{row.get('q6_shadow_active_miss_labels')},"
            f"{row.get('space')},"
            f"{row.get('warehouse')},"
            f"{row.get('sample')},"
            f"{row.get('evidence_profile')},"
            f"{Path(str(row.get('file') or '')).name}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        type=Path,
        default=ROOT / "data" / "logs" / "live" / "model_eval.jsonl",
    )
    parser.add_argument(
        "--since-hours",
        type=float,
        default=24.0,
        help="Only include rows newer than this many hours (default 24)",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=ROOT / "data" / "logs" / "live" / "raw" / "archive",
        help="Also replay recent WinDivert archives from this archive root/complete dir",
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Only read model_eval.jsonl; do not replay archived captures",
    )
    parser.add_argument("--archive-n-trials", type=int, default=10)
    parser.add_argument("--archive-roi-trials", type=int, default=0)
    parser.add_argument("--archive-shadow-trials", type=int, default=1)
    parser.add_argument(
        "--archive-debug-shadows",
        action="store_true",
        help="Enable debug shadows while replaying archives for this brief summary",
    )
    parser.add_argument(
        "--archive-window",
        choices=("prebid", "full"),
        default="prebid",
        help=(
            "Archive replay window: prebid evaluates each round immediately before "
            "the user's bid SEND 0x0022; full keeps the previous one-row whole-game view"
        ),
    )
    parser.add_argument(
        "--archive-formal-mode",
        choices=("v2", "v3_practical"),
        default=None,
        help=(
            "Formal mode used when replaying archives. Default keeps the artifact "
            "builder default (v2); use v3_practical to match current live runner."
        ),
    )
    parser.add_argument(
        "--detail-groups",
        action="store_true",
        help=(
            "Print detailed diagnostic groups such as hero, evidence profile, "
            "sample space, q6 space pressure, and multi-tag buckets."
        ),
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    if not args.log.exists():
        print(f"Missing log: {args.log}", file=sys.stderr)
        return 1
    since_ts = time.time() - max(0.0, args.since_hours) * 3600.0
    rows = _load_rows(args.log, since_ts=since_ts)
    if not args.no_archive:
        rows.extend(
            _load_archive_rows(
                args.archive_dir,
                since_ts=since_ts,
                n_trials=max(1, int(args.archive_n_trials)),
                roi_trials=max(0, int(args.archive_roi_trials)),
                shadow_trials=max(1, int(args.archive_shadow_trials)),
                run_debug_shadows=bool(args.archive_debug_shadows),
                window=args.archive_window,
                formal_mode=args.archive_formal_mode,
            )
        )
    rows = _dedupe_rows(rows)
    summary = summarize(rows)
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=True, indent=2))
    else:
        _print_report(summary, detail_groups=bool(args.detail_groups))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
