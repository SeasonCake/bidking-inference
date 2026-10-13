"""Audit 0x002D payload outer fields for unresolved session-capacity blockers.

This script is diagnostic-only. It checks whether settlement wrapper/payload
numeric fields look like capacity/count sources or ordinary metadata such as
map id, loss units, opaque ids, and timestamps.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAYLOAD_TABLE_GAP = (
    ROOT / ".tmp" / "codex" / "v3_session_capacity_payload_table_gap_2410_latest.json"
)
DEFAULT_SAMPLE_ROOT = ROOT / "data" / "samples" / "fatbeans"
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from bidking_lab.live.fatbeans import _parse_fields, parse_fatbeans_capture  # noqa: E402
from summarize_v3_settlement_payload_audit import _latest_settlement_payload  # noqa: E402


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


def _payload_int_values(payload: bytes | None) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    if not isinstance(payload, bytes):
        return out
    for field_no, _wire_type, value in _parse_fields(payload):
        if isinstance(value, int):
            out.setdefault(str(field_no), []).append(int(value))
    return out


def _latest_settlement_state(path: Path) -> Any | None:
    capture = parse_fatbeans_capture(path)
    states = [
        state
        for state in capture.states
        if state.message_id == 0x002D or state.inventory_items
    ]
    return states[-1] if states else None


def _epoch_delta_seconds(value: int | None, capture_time: str | None) -> int | None:
    if value is None or not capture_time:
        return None
    text = str(capture_time)
    if "." in text:
        prefix, suffix = text.split(".", 1)
        fraction = suffix
        timezone = ""
        for marker in ("+", "-"):
            if marker in suffix:
                fraction, timezone = suffix.split(marker, 1)
                timezone = marker + timezone
                break
        if len(fraction) > 6:
            text = f"{prefix}.{fraction[:6]}{timezone}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return int(round(abs(float(value) - dt.timestamp())))


def _candidate_targets(row: Mapping[str, Any]) -> dict[str, int]:
    table = _as_mapping(row.get("table_delta"))
    payload = _as_mapping(row.get("payload"))
    targets: dict[str, int] = {}
    for name, value in (
        ("inventory_count", table.get("inventory_count")),
        ("unique_non_temp_item_id_count", table.get("unique_non_temp_item_id_count")),
        ("bidmap_items_per_session_max", table.get("bidmap_items_per_session_max")),
        ("bidmap_raw_round_cap_max", table.get("bidmap_raw_round_cap_max")),
        ("inventory_slot_count", payload.get("inventory_slot_count")),
        ("occupied_slot_count", payload.get("occupied_slot_count")),
        ("raw_item_candidate_count", payload.get("raw_item_candidate_count")),
    ):
        parsed = _int_or_none(value)
        if parsed is not None:
            targets[name] = parsed
    return targets


def _target_matches(
    values: Mapping[str, list[int]],
    targets: Mapping[str, int],
    *,
    ignore_fields: Iterable[str] = (),
) -> list[dict[str, Any]]:
    ignored = set(ignore_fields)
    out: list[dict[str, Any]] = []
    for field, seq in values.items():
        if field in ignored:
            continue
        for value in seq:
            for target, target_value in targets.items():
                if int(value) == int(target_value):
                    out.append(
                        {
                            "field": field,
                            "value": value,
                            "target": target,
                        }
                    )
    return out


def _row_audit(row: Mapping[str, Any], *, sample_root: Path) -> dict[str, Any] | None:
    file = str(row.get("file") or "").split("#", 1)[0]
    if not file:
        return None
    path = sample_root / file
    payload, loss_units, frame_meta = _latest_settlement_payload(path)
    state = _latest_settlement_state(path)
    payload_values = _payload_int_values(payload)
    wrapper_values = {
        "3": [int(value) for value in frame_meta.get("settlement_outer_field3_values", ())],
        "4": [int(value) for value in frame_meta.get("settlement_outer_field4_values", ())],
        "5": [int(value) for value in frame_meta.get("settlement_outer_field5_values", ())],
    }
    targets = _candidate_targets(row)
    map_id = _int_or_none(row.get("map_id"))
    payload_map_matches = (
        map_id is not None and payload_values.get("2") == [map_id]
    )
    wrapper_field5_equals_loss = (
        loss_units is not None and wrapper_values.get("5") == [int(loss_units)]
    )
    wrapper_field3_equals_field4 = (
        bool(wrapper_values.get("3"))
        and wrapper_values.get("3") == wrapper_values.get("4")
    )
    field20_values = payload_values.get("20") or []
    field20_delta = _epoch_delta_seconds(
        field20_values[0] if field20_values else None,
        getattr(state, "capture_time", None) if state is not None else None,
    )
    field20_matches_capture_time = (
        field20_delta is not None and field20_delta <= 2
    )
    ignored_payload_fields = {"2", "20"}
    ignored_wrapper_fields = {"5"}
    target_matches = [
        {
            **match,
            "scope": "payload",
        }
        for match in _target_matches(
            payload_values,
            targets,
            ignore_fields=ignored_payload_fields,
        )
    ] + [
        {
            **match,
            "scope": "wrapper",
        }
        for match in _target_matches(
            wrapper_values,
            targets,
            ignore_fields=ignored_wrapper_fields,
        )
    ]
    metadata_matches = {
        "payload_field2_matches_map_id": payload_map_matches,
        "payload_field20_matches_capture_time": field20_matches_capture_time,
        "wrapper_field5_equals_loss_units": wrapper_field5_equals_loss,
        "wrapper_field3_equals_field4": wrapper_field3_equals_field4,
    }
    if target_matches:
        status = "blocked_outer_field_capacity_candidate"
        next_checks = ["decode_outer_field_capacity_candidate"]
    elif (
        payload_map_matches
        and field20_matches_capture_time
        and wrapper_field5_equals_loss
        and wrapper_field3_equals_field4
    ):
        status = "watch_outer_fields_metadata_only"
        next_checks = [
            "check_per_session_table_version_or_external_overlay",
            "inspect_server_side_settlement_expansion_or_source_transform",
        ]
    else:
        status = "watch_outer_field_semantics_partial"
        next_checks = ["inspect_remaining_outer_field_metadata"]
    return {
        "file": file,
        "map_id": row.get("map_id"),
        "status": status,
        "source_status": row.get("status"),
        "targets": targets,
        "metadata_matches": metadata_matches,
        "payload_int_values": payload_values,
        "wrapper_int_values": wrapper_values,
        "target_matches": target_matches,
        "settlement_loss_units": loss_units,
        "settlement_capture_time": getattr(state, "capture_time", None) if state is not None else None,
        "payload_field20_epoch_delta_seconds": field20_delta,
        "next_checks": next_checks,
    }


def summarize_payload_outer_field_semantics(
    *,
    payload_table_gap: Mapping[str, Any],
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
    focus_maps: Iterable[str] = (),
) -> dict[str, Any]:
    focus = {str(item) for item in focus_maps if str(item)}
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in _as_list(payload_table_gap.get("rows")):
        if not isinstance(row, Mapping):
            continue
        if focus and str(row.get("map_id")) not in focus:
            continue
        if not str(row.get("status") or "").startswith("blocked"):
            continue
        try:
            audited = _row_audit(row, sample_root=sample_root)
        except Exception as exc:  # pragma: no cover - defensive for ad hoc captures.
            errors.append(f"{row.get('file')}: {exc}")
            continue
        if audited is not None:
            rows.append(audited)
    status_counts = Counter(str(row.get("status")) for row in rows)
    next_checks = Counter(
        str(check)
        for row in rows
        for check in _as_list(row.get("next_checks"))
    )
    metadata_keys = Counter(
        key
        for row in rows
        for key, value in _as_mapping(row.get("metadata_matches")).items()
        if value is True
    )
    return {
        "status": (
            "blocked_outer_field_capacity_candidate"
            if any(row.get("status") == "blocked_outer_field_capacity_candidate" for row in rows)
            else "watch_payload_outer_fields_metadata_only"
        ),
        "shadow_only": True,
        "affects_bid": False,
        "errors": errors,
        "rows": rows,
        "summary": {
            "rows": len(rows),
            "files": len({row.get("file") for row in rows}),
            "maps": len({str(row.get("map_id")) for row in rows if row.get("map_id") is not None}),
            "metadata_only_rows": status_counts.get(
                "watch_outer_fields_metadata_only",
                0,
            ),
            "capacity_candidate_rows": status_counts.get(
                "blocked_outer_field_capacity_candidate",
                0,
            ),
            "target_match_count": sum(len(row.get("target_matches") or []) for row in rows),
            "status_counts": _counter_dict(status_counts),
            "metadata_match_counts": _counter_dict(metadata_keys),
            "next_check_counts": _counter_dict(next_checks),
        },
    }


def _format_counts(counts: Mapping[str, Any]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def print_summary(result: Mapping[str, Any]) -> None:
    summary = _as_mapping(result.get("summary"))
    print(
        "status={status} rows={rows} metadata_only={metadata_only} "
        "capacity_candidates={capacity_candidates} target_matches={target_matches} "
        "metadata={metadata} next_checks={next_checks}".format(
            status=result.get("status"),
            rows=summary.get("rows"),
            metadata_only=summary.get("metadata_only_rows"),
            capacity_candidates=summary.get("capacity_candidate_rows"),
            target_matches=summary.get("target_match_count"),
            metadata=_format_counts(_as_mapping(summary.get("metadata_match_counts"))),
            next_checks=_format_counts(_as_mapping(summary.get("next_check_counts"))),
        )
    )
    for row in _as_list(result.get("rows"))[:5]:
        if not isinstance(row, Mapping):
            continue
        print(
            "file={file} map={map_id} status={status} loss={loss} "
            "field20_delta={delta} target_matches={matches} next_checks={next_checks}".format(
                file=row.get("file"),
                map_id=row.get("map_id"),
                status=row.get("status"),
                loss=row.get("settlement_loss_units"),
                delta=row.get("payload_field20_epoch_delta_seconds"),
                matches=len(row.get("target_matches") or []),
                next_checks=",".join(str(item) for item in row.get("next_checks") or []),
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize 0x002D outer/payload field semantics for session-capacity blockers.",
    )
    parser.add_argument("--payload-table-gap-json", type=Path, default=DEFAULT_PAYLOAD_TABLE_GAP)
    parser.add_argument("--sample-root", type=Path, default=DEFAULT_SAMPLE_ROOT)
    parser.add_argument("--focus-map", action="append", default=[])
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)
    result = summarize_payload_outer_field_semantics(
        payload_table_gap=_load_json(args.payload_table_gap_json),
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
