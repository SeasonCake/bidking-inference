"""Audit payload/table gaps for unresolved v3 session-capacity blockers.

This script is diagnostic-only. It joins session-capacity source-gap rows with
raw 0x002D settlement payload metrics and table-cap deltas, so payload-verified
inventory lists are not mistaken for a capacity source.
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
DEFAULT_SESSION_GAP = (
    ROOT / ".tmp" / "codex" / "v3_session_capacity_source_gap_2410_latest.json"
)
DEFAULT_SAMPLE_ROOT = ROOT / "data" / "samples" / "fatbeans"
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from summarize_v3_settlement_payload_audit import (  # noqa: E402
    _audit_file,
    _latest_settlement_payload,
)
from bidking_lab.live.fatbeans import _parse_fields, parse_fatbeans_capture  # noqa: E402


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {
        key: count
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    }


def _is_blocked_session_gap(row: Mapping[str, Any]) -> bool:
    return str(row.get("status") or "").startswith(
        "blocked_session_capacity_source_gap"
    )


def _event_payload_digest(path: Path, *, map_id: Any, inventory_count: int | None) -> dict[str, Any]:
    capture = parse_fatbeans_capture(path)
    action_counts: Counter[str] = Counter()
    skill_counts: Counter[str] = Counter()
    public_counts: Counter[str] = Counter()
    action_observed: list[dict[str, Any]] = []
    skill_observed: list[dict[str, Any]] = []
    public_observed: list[dict[str, Any]] = []
    for state in capture.states:
        if str(state.map_id) != str(map_id):
            continue
        for action in state.action_results:
            action_counts[str(action.action_id)] += 1
            observed_count = len(tuple(action.observed_items or ()))
            if observed_count:
                action_observed.append(
                    {
                        "sort_id": state.sort_id,
                        "message_id": f"0x{state.message_id:04x}",
                        "action_id": action.action_id,
                        "observed_item_count": observed_count,
                        "matches_inventory_count": (
                            inventory_count is not None
                            and observed_count == inventory_count
                        ),
                    }
                )
        for skill in state.skill_reveals:
            skill_counts[str(skill.skill_id)] += 1
            observed_count = len(tuple(skill.observed_items or ()))
            if observed_count:
                skill_observed.append(
                    {
                        "sort_id": state.sort_id,
                        "message_id": f"0x{state.message_id:04x}",
                        "skill_id": skill.skill_id,
                        "hero_id": skill.hero_id,
                        "observed_item_count": observed_count,
                        "matches_inventory_count": (
                            inventory_count is not None
                            and observed_count == inventory_count
                        ),
                    }
                )
        for info in state.public_infos:
            public_counts[str(info.info_id)] += 1
            observed_count = len(tuple(info.observed_items or ()))
            if observed_count:
                public_observed.append(
                    {
                        "sort_id": state.sort_id,
                        "message_id": f"0x{state.message_id:04x}",
                        "info_id": info.info_id,
                        "observed_item_count": observed_count,
                        "matches_inventory_count": (
                            inventory_count is not None
                            and observed_count == inventory_count
                        ),
                    }
                )

    def full_count(rows: Iterable[Mapping[str, Any]]) -> int:
        return sum(1 for row in rows if row.get("matches_inventory_count") is True)

    return {
        "action_id_counts": _counter_dict(action_counts),
        "skill_id_counts": _counter_dict(skill_counts),
        "public_info_id_counts": _counter_dict(public_counts),
        "action_observed_item_count_max": max(
            (int(row["observed_item_count"]) for row in action_observed),
            default=0,
        ),
        "skill_observed_item_count_max": max(
            (int(row["observed_item_count"]) for row in skill_observed),
            default=0,
        ),
        "public_observed_item_count_max": max(
            (int(row["observed_item_count"]) for row in public_observed),
            default=0,
        ),
        "full_action_payload_count": full_count(action_observed),
        "full_skill_payload_count": full_count(skill_observed),
        "full_public_payload_count": full_count(public_observed),
        "action_observed_examples": action_observed[:3],
        "skill_observed_examples": skill_observed[:3],
        "public_observed_examples": public_observed[:3],
    }


def _payload_values(path: Path) -> dict[str, Any]:
    payload, loss_units, frame_meta = _latest_settlement_payload(path)
    fields = _parse_fields(payload) if isinstance(payload, bytes) else []
    int_values: dict[str, list[int]] = {}
    for field_no, _wire_type, value in fields:
        if isinstance(value, int):
            int_values.setdefault(str(field_no), []).append(int(value))
    return {
        "settlement_loss_units": loss_units,
        "payload_int_values": int_values,
        "settlement_frame": frame_meta,
    }


def _table_delta(row: Mapping[str, Any]) -> dict[str, Any]:
    inventory_count = _int_or_none(row.get("inventory_count"))
    unique_non_temp = _int_or_none(row.get("unique_non_temp_item_id_count"))
    bidmap_session = _int_or_none(row.get("bidmap_items_per_session_max"))
    bidmap_round = _int_or_none(row.get("bidmap_raw_round_cap_max"))
    return {
        "inventory_count": inventory_count,
        "unique_non_temp_item_id_count": unique_non_temp,
        "bidmap_items_per_session_max": bidmap_session,
        "bidmap_raw_round_cap_max": bidmap_round,
        "inventory_minus_bidmap_items_per_session": (
            inventory_count - bidmap_session
            if inventory_count is not None and bidmap_session is not None
            else None
        ),
        "inventory_minus_bidmap_raw_round_cap": (
            inventory_count - bidmap_round
            if inventory_count is not None and bidmap_round is not None
            else None
        ),
        "unique_non_temp_minus_bidmap_raw_round_cap": (
            unique_non_temp - bidmap_round
            if unique_non_temp is not None and bidmap_round is not None
            else None
        ),
    }


def _payload_verified(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("status") == "ok"
        and payload.get("raw_candidate_inventory_delta") == 0
        and payload.get("occupied_slot_inventory_delta") == 0
    )


def _positive(value: Any) -> bool:
    parsed = _int_or_none(value)
    return parsed is not None and parsed > 0


def _status(
    *,
    source_gap_row: Mapping[str, Any],
    payload: Mapping[str, Any],
    event_payload: Mapping[str, Any],
    table_delta: Mapping[str, Any],
) -> tuple[str, list[str], list[str]]:
    reasons: list[str] = []
    next_checks: list[str] = []
    if not _is_blocked_session_gap(source_gap_row):
        return "watch_non_blocker_context", ["not_unresolved_session_capacity_gap"], []
    if _payload_verified(payload):
        reasons.append("payload_inventory_verified")
    else:
        reasons.append("payload_inventory_not_verified")
        next_checks.append("verify_raw_settlement_payload_inventory")
        return "blocked_payload_inventory_unverified", reasons, next_checks
    if not any(
        _positive(event_payload.get(field))
        for field in (
            "full_action_payload_count",
            "full_skill_payload_count",
            "full_public_payload_count",
        )
    ):
        reasons.append("no_full_event_payload_source")
    else:
        reasons.append("full_event_payload_source_observed")
        return "watch_full_event_payload_source_observed", reasons, next_checks
    if _positive(table_delta.get("unique_non_temp_minus_bidmap_raw_round_cap")):
        reasons.append("unique_non_temp_exceeds_bidmap_round_cap")
    if _positive(table_delta.get("inventory_minus_bidmap_items_per_session")):
        reasons.append("inventory_exceeds_bidmap_session_cap")
    if payload.get("inventory_slot_count") and payload.get("inventory_slot_count") != payload.get(
        "occupied_slot_count"
    ):
        reasons.append("payload_slot_envelope_not_item_count_cap")
    next_checks.extend(
        [
            "check_per_session_table_version_or_external_overlay",
            "inspect_server_side_settlement_expansion_or_source_transform",
            "decode_payload_outer_fields_as_metadata_not_capacity",
        ]
    )
    return "blocked_payload_verified_table_cap_gap_without_full_source", reasons, next_checks


def summarize_session_capacity_payload_table_gap(
    *,
    session_capacity_source_gap: Mapping[str, Any],
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
    focus_maps: Iterable[str] = (),
) -> dict[str, Any]:
    focus = {str(item) for item in focus_maps if str(item)}
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for source_row in _as_list(session_capacity_source_gap.get("rows")):
        if not isinstance(source_row, Mapping):
            continue
        if focus and str(source_row.get("map_id")) not in focus:
            continue
        if not _is_blocked_session_gap(source_row):
            continue
        file = str(source_row.get("file") or "").split("#", 1)[0]
        if not file:
            continue
        path = sample_root / file
        try:
            payload = _audit_file(path)
            event_payload = _event_payload_digest(
                path,
                map_id=source_row.get("map_id"),
                inventory_count=_int_or_none(source_row.get("inventory_count")),
            )
            payload_values = _payload_values(path)
        except Exception as exc:  # pragma: no cover - defensive for ad hoc captures.
            errors.append(f"{file}: {exc}")
            continue
        table = _table_delta(source_row)
        status, reasons, next_checks = _status(
            source_gap_row=source_row,
            payload=payload,
            event_payload=event_payload,
            table_delta=table,
        )
        rows.append(
            {
                "file": file,
                "map_id": source_row.get("map_id"),
                "map_family": source_row.get("map_family"),
                "status": status,
                "reasons": reasons,
                "next_checks": next_checks,
                "source_gap_status": source_row.get("status"),
                "unique_residual_mode": source_row.get("unique_residual_mode"),
                "mechanism_class": source_row.get("mechanism_class"),
                "source_context_class": source_row.get("source_context_class"),
                "source_evidence_class": source_row.get("source_evidence_class"),
                "table_delta": table,
                "payload": {
                    key: payload.get(key)
                    for key in (
                        "status",
                        "inventory_count",
                        "inventory_cells",
                        "inventory_slot_count",
                        "occupied_slot_count",
                        "raw_item_candidate_count",
                        "raw_candidate_inventory_delta",
                        "occupied_slot_inventory_delta",
                        "raw_duplicate_runtime_item_pair_count",
                        "payload_field_counts",
                        "payload_field5_count",
                        "payload_field6_count",
                        "payload_field7_count",
                        "payload_field8_count",
                        "payload_field20_present",
                        "settlement_outer_field_shape",
                        "settlement_outer_field3_values",
                        "settlement_outer_field4_values",
                        "settlement_outer_field5_values",
                        "settlement_outer_field6_count",
                        "candidate_path_counts",
                        "occupied_slot_int_field_counts",
                        "empty_slot_int_field_counts",
                        "full_observed_action_ids",
                    )
                },
                "payload_values": payload_values,
                "event_payload": event_payload,
            }
        )
    status_counts = Counter(str(row.get("status")) for row in rows)
    reason_counts = Counter(
        str(reason)
        for row in rows
        for reason in _as_list(row.get("reasons"))
    )
    next_check_counts = Counter(
        str(check)
        for row in rows
        for check in _as_list(row.get("next_checks"))
    )
    return {
        "status": (
            "blocked_payload_table_gap_required"
            if any(str(row.get("status")).startswith("blocked") for row in rows)
            else "watch_payload_table_gap_audit_only"
        ),
        "shadow_only": True,
        "affects_bid": False,
        "errors": errors,
        "rows": rows,
        "summary": {
            "rows": len(rows),
            "files": len({row.get("file") for row in rows}),
            "maps": len({str(row.get("map_id")) for row in rows if row.get("map_id") is not None}),
            "blocked_rows": sum(
                1 for row in rows if str(row.get("status")).startswith("blocked")
            ),
            "payload_verified_rows": sum(
                1 for row in rows if _payload_verified(_as_mapping(row.get("payload")))
            ),
            "no_full_event_payload_rows": sum(
                1
                for row in rows
                if not any(
                    _positive(_as_mapping(row.get("event_payload")).get(field))
                    for field in (
                        "full_action_payload_count",
                        "full_skill_payload_count",
                        "full_public_payload_count",
                    )
                )
            ),
            "status_counts": _counter_dict(status_counts),
            "reason_counts": _counter_dict(reason_counts),
            "next_check_counts": _counter_dict(next_check_counts),
            "top_examples": rows[:3],
        },
    }


def _format_counts(counts: Mapping[str, Any]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def print_summary(result: Mapping[str, Any]) -> None:
    summary = _as_mapping(result.get("summary"))
    print(
        "status={status} rows={rows} files={files} maps={maps} blocked={blocked} "
        "payload_verified={verified} no_full_event={no_full} statuses={statuses} "
        "next_checks={next_checks}".format(
            status=result.get("status"),
            rows=summary.get("rows"),
            files=summary.get("files"),
            maps=summary.get("maps"),
            blocked=summary.get("blocked_rows"),
            verified=summary.get("payload_verified_rows"),
            no_full=summary.get("no_full_event_payload_rows"),
            statuses=_format_counts(_as_mapping(summary.get("status_counts"))),
            next_checks=_format_counts(_as_mapping(summary.get("next_check_counts"))),
        )
    )
    for row in _as_list(result.get("rows"))[:5]:
        if not isinstance(row, Mapping):
            continue
        table = _as_mapping(row.get("table_delta"))
        payload = _as_mapping(row.get("payload"))
        event_payload = _as_mapping(row.get("event_payload"))
        print(
            "file={file} map={map_id} status={status} inventory={inventory} "
            "unique_non_temp={unique_non_temp} bidmap_session={bidmap_session} "
            "bidmap_round={bidmap_round} unique_minus_round={unique_minus_round} "
            "slots={slots} occupied={occupied} raw_candidates={raw_candidates} "
            "raw_delta={raw_delta} occupied_delta={occupied_delta} "
            "full_action={full_action} full_skill={full_skill} skill_max={skill_max} "
            "next_checks={next_checks}".format(
                file=row.get("file"),
                map_id=row.get("map_id"),
                status=row.get("status"),
                inventory=table.get("inventory_count"),
                unique_non_temp=table.get("unique_non_temp_item_id_count"),
                bidmap_session=table.get("bidmap_items_per_session_max"),
                bidmap_round=table.get("bidmap_raw_round_cap_max"),
                unique_minus_round=table.get(
                    "unique_non_temp_minus_bidmap_raw_round_cap"
                ),
                slots=payload.get("inventory_slot_count"),
                occupied=payload.get("occupied_slot_count"),
                raw_candidates=payload.get("raw_item_candidate_count"),
                raw_delta=payload.get("raw_candidate_inventory_delta"),
                occupied_delta=payload.get("occupied_slot_inventory_delta"),
                full_action=event_payload.get("full_action_payload_count"),
                full_skill=event_payload.get("full_skill_payload_count"),
                skill_max=event_payload.get("skill_observed_item_count_max"),
                next_checks=",".join(str(item) for item in row.get("next_checks") or []),
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize payload/table gaps for unresolved session-capacity blockers.",
    )
    parser.add_argument("--session-capacity-source-gap-json", type=Path, default=DEFAULT_SESSION_GAP)
    parser.add_argument("--sample-root", type=Path, default=DEFAULT_SAMPLE_ROOT)
    parser.add_argument("--focus-map", action="append", default=[])
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    result = summarize_session_capacity_payload_table_gap(
        session_capacity_source_gap=_load_json(args.session_capacity_source_gap_json),
        sample_root=args.sample_root,
        focus_maps=args.focus_map,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_summary(result)
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
