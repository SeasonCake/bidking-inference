"""Audit raw 0x002D Fatbeans settlement payload structure."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE_ROOT = ROOT / "data" / "samples" / "fatbeans"

from bidking_lab.live.fatbeans import (  # noqa: E402
    _first,
    _parse_fields,
    _state_payload,
    load_fatbeans_packets,
    parse_fatbeans_capture,
    reconstruct_fatbeans_frames,
)


def _numeric_values(values: Iterable[Any]) -> tuple[float, ...]:
    out: list[float] = []
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        try:
            out.append(float(value))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def _numeric_summary(values: Iterable[Any], *, digits: int = 3) -> dict[str, Any]:
    seq = _numeric_values(values)
    if not seq:
        return {"n": 0, "avg": None, "p90": None, "max": None}
    ordered = sorted(seq)
    p90_index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.9)))
    return {
        "n": len(seq),
        "avg": round(sum(seq) / len(seq), digits),
        "p90": round(ordered[p90_index], digits),
        "max": round(max(seq), digits),
    }


def _counter_dict(values: Iterable[Any], *, top: int = 8) -> dict[str, int]:
    counts: Counter[str] = Counter(
        str(value) if value not in (None, "") else "none"
        for value in values
    )
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top])


def _format_counts(counts: Mapping[str, int]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def _format_summary(summary: Mapping[str, Any]) -> str:
    return (
        f"n={summary['n']}"
        f"/avg={summary['avg']}"
        f"/p90={summary['p90']}"
        f"/max={summary['max']}"
    )


def _is_inventory_item(ints: Mapping[int, Sequence[int]]) -> bool:
    runtime_id = ints.get(1, [None])[0]
    item_id = ints.get(2, [None])[0]
    return (
        isinstance(runtime_id, int)
        and isinstance(item_id, int)
        and 1_000_000 <= item_id < 2_000_000
    )


def _field_signature(fields: Sequence[tuple[int, int, Any]]) -> str:
    pieces: list[str] = []
    for field_no, wire_type, value in fields:
        kind = "b" if isinstance(value, bytes) else "i"
        pieces.append(f"{field_no}:{wire_type}:{kind}")
    return ",".join(pieces)


def _item_candidate_from_bytes(data: bytes, *, path: tuple[int, ...]) -> dict[str, Any] | None:
    fields = _parse_fields(data)
    ints: dict[int, list[int]] = defaultdict(list)
    byte_fields: dict[int, list[bytes]] = defaultdict(list)
    for field_no, _wire_type, value in fields:
        if isinstance(value, int):
            ints[field_no].append(value)
        elif isinstance(value, bytes):
            byte_fields[field_no].append(value)
    if not _is_inventory_item(ints):
        return None
    return {
        "runtime_id": ints[1][0],
        "item_id": ints[2][0],
        "quality": ints.get(9, [None])[0],
        "cells": len(byte_fields.get(4, ())),
        "signature": _field_signature(fields),
        "path": ".".join(str(item) for item in path) or "root",
    }


def _int_field_counts(fields: Sequence[tuple[int, int, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter(
        str(field_no)
        for field_no, _wire_type, value in fields
        if isinstance(value, int)
    )
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:8])


def _int_field_value_counts(fields: Sequence[tuple[int, int, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter(
        f"{field_no}={value}"
        for field_no, _wire_type, value in fields
        if isinstance(value, int)
    )
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:8])


def _merge_count_mappings(mappings: Iterable[Mapping[str, int]], *, top: int = 8) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for mapping in mappings:
        counts.update({str(key): int(value) for key, value in mapping.items()})
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top])


def _typed_field_counts(fields: Sequence[tuple[int, int, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter(
        f"{field_no}:{wire_type}:{'b' if isinstance(value, bytes) else 'i'}"
        for field_no, wire_type, value in fields
    )

    def sort_key(item: tuple[str, int]) -> tuple[int, int, str]:
        field_text, wire_text, kind = item[0].split(":", 2)
        return (int(field_text), int(wire_text), kind)

    return dict(sorted(counts.items(), key=sort_key))


def _typed_field_shape(fields: Sequence[tuple[int, int, Any]]) -> str:
    counts = _typed_field_counts(fields)
    return ",".join(f"{key}x{count}" for key, count in counts.items()) or "none"


def _int_values_for_field(
    fields: Sequence[tuple[int, int, Any]],
    field_no: int,
) -> tuple[int, ...]:
    return tuple(
        int(value)
        for item_field_no, _wire_type, value in fields
        if item_field_no == field_no and isinstance(value, int)
    )


def _settlement_wrapper_metrics(body: bytes | None) -> dict[str, Any]:
    if not isinstance(body, bytes):
        return {
            "settlement_outer_field_shape": "none",
            "settlement_outer_field3_present": False,
            "settlement_outer_field4_present": False,
            "settlement_outer_field5_present": False,
            "settlement_outer_field3_values": (),
            "settlement_outer_field4_values": (),
            "settlement_outer_field5_values": (),
            "settlement_outer_field6_count": None,
        }
    fields = _parse_fields(body)
    field3_values = _int_values_for_field(fields, 3)
    field4_values = _int_values_for_field(fields, 4)
    field5_values = _int_values_for_field(fields, 5)
    return {
        "settlement_outer_field_shape": _typed_field_shape(fields),
        "settlement_outer_field3_present": bool(field3_values),
        "settlement_outer_field4_present": bool(field4_values),
        "settlement_outer_field5_present": bool(field5_values),
        "settlement_outer_field3_values": field3_values,
        "settlement_outer_field4_values": field4_values,
        "settlement_outer_field5_values": field5_values,
        "settlement_outer_field6_count": sum(
            1
            for field_no, _wire_type, value in fields
            if field_no == 6 and isinstance(value, bytes)
        ),
    }


def _collect_item_candidates(
    data: bytes,
    *,
    depth: int = 0,
    path: tuple[int, ...] = (),
) -> list[dict[str, Any]]:
    if depth > 8:
        return []
    direct = _item_candidate_from_bytes(data, path=path)
    if direct is not None:
        return [direct]
    out: list[dict[str, Any]] = []
    for field_no, _wire_type, value in _parse_fields(data):
        if isinstance(value, bytes):
            out.extend(
                _collect_item_candidates(
                    value,
                    depth=depth + 1,
                    path=(*path, field_no),
                )
            )
    return out


def _inventory_block_metrics(block: Any) -> dict[str, Any]:
    if not isinstance(block, bytes):
        return {
            "inventory_slot_count": None,
            "occupied_slot_count": None,
            "raw_item_candidate_count": None,
            "raw_duplicate_runtime_item_pair_count": None,
            "slot_field_shapes": {},
            "occupied_slot_field_shapes": {},
            "empty_slot_field_shapes": {},
            "occupied_slot_int_field_counts": {},
            "empty_slot_int_field_counts": {},
            "occupied_slot_int_value_counts": {},
            "empty_slot_int_value_counts": {},
            "candidate_path_counts": {},
            "item_field_signatures": {},
        }
    fields = _parse_fields(block)
    slots = [
        value
        for field_no, _wire_type, value in fields
        if field_no == 3 and isinstance(value, bytes)
    ]
    occupied_slots = 0
    candidates: list[dict[str, Any]] = []
    slot_shapes: Counter[str] = Counter()
    occupied_slot_shapes: Counter[str] = Counter()
    empty_slot_shapes: Counter[str] = Counter()
    occupied_int_fields: Counter[str] = Counter()
    empty_int_fields: Counter[str] = Counter()
    occupied_int_values: Counter[str] = Counter()
    empty_int_values: Counter[str] = Counter()
    for slot in slots:
        slot_fields = _parse_fields(slot)
        slot_signature = _field_signature(slot_fields)
        slot_shapes[slot_signature] += 1
        slot_candidates = _collect_item_candidates(slot)
        if slot_candidates:
            occupied_slots += 1
            occupied_slot_shapes[slot_signature] += 1
            occupied_int_fields.update(_int_field_counts(slot_fields))
            occupied_int_values.update(_int_field_value_counts(slot_fields))
            candidates.extend(slot_candidates)
        else:
            empty_slot_shapes[slot_signature] += 1
            empty_int_fields.update(_int_field_counts(slot_fields))
            empty_int_values.update(_int_field_value_counts(slot_fields))
    pair_counts = Counter(
        (candidate["runtime_id"], candidate["item_id"])
        for candidate in candidates
    )
    duplicate_pairs = sum(count - 1 for count in pair_counts.values() if count > 1)
    return {
        "inventory_slot_count": len(slots),
        "occupied_slot_count": occupied_slots,
        "raw_item_candidate_count": len(candidates),
        "raw_duplicate_runtime_item_pair_count": duplicate_pairs,
        "slot_field_shapes": dict(slot_shapes.most_common(8)),
        "occupied_slot_field_shapes": dict(occupied_slot_shapes.most_common(8)),
        "empty_slot_field_shapes": dict(empty_slot_shapes.most_common(8)),
        "occupied_slot_int_field_counts": dict(occupied_int_fields.most_common(8)),
        "empty_slot_int_field_counts": dict(empty_int_fields.most_common(8)),
        "occupied_slot_int_value_counts": dict(occupied_int_values.most_common(8)),
        "empty_slot_int_value_counts": dict(empty_int_values.most_common(8)),
        "candidate_path_counts": dict(
            Counter(candidate["path"] for candidate in candidates).most_common(8)
        ),
        "item_field_signatures": dict(
            Counter(candidate["signature"] for candidate in candidates).most_common(8)
        ),
    }


def _resolve_paths(paths: Iterable[Path]) -> list[Path]:
    seq = list(paths)
    if not seq:
        seq = [DEFAULT_SAMPLE_ROOT]
    out: list[Path] = []
    for path in seq:
        if path.is_dir():
            out.extend(sorted(path.glob("*.json")))
        elif path.exists():
            out.append(path)
    return out


def _latest_settlement_payload(path: Path) -> tuple[bytes | None, int | None, dict[str, Any]]:
    packets = load_fatbeans_packets(path)
    frames = [
        frame
        for frame in reconstruct_fatbeans_frames(packets, "REV")
        if frame.message_id == 0x002D
    ]
    if not frames:
        return None, None, {"settlement_frame_count": 0}
    frame = frames[-1]
    payload, loss_units = _state_payload(frame)
    return payload, loss_units, {
        "settlement_frame_count": len(frames),
        "settlement_sort_id": frame.sort_id,
        "settlement_frame_length": len(frame.raw),
        **_settlement_wrapper_metrics(frame.body),
    }


def _payload_field_counts(payload: bytes | None) -> dict[str, int]:
    if not isinstance(payload, bytes):
        return {}
    counts = Counter(
        f"{field_no}:{wire_type}"
        for field_no, wire_type, _value in _parse_fields(payload)
    )
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def _audit_file(path: Path) -> dict[str, Any]:
    events = parse_fatbeans_capture(path)
    states = [
        state
        for state in events.states
        if state.message_id == 0x002D or state.inventory_items
    ]
    if not states:
        return {
            "file": path.name,
            "status": "no_settlement_state",
        }
    state = states[-1]
    payload, loss_units, frame_meta = _latest_settlement_payload(path)
    payload_fields = _parse_fields(payload) if isinstance(payload, bytes) else []
    inventory_block = _first(payload_fields, 4) if payload_fields else None
    inventory_metrics = _inventory_block_metrics(inventory_block)
    inventory_count = len(tuple(getattr(state, "inventory_items", ()) or ()))
    action_counts = [
        (int(action.action_id), len(tuple(action.observed_items or ())))
        for action in tuple(getattr(state, "action_results", ()) or ())
    ]
    full_action_ids = [
        action_id
        for action_id, observed_count in action_counts
        if inventory_count > 0 and observed_count == inventory_count
    ]
    raw_candidate_count = inventory_metrics["raw_item_candidate_count"]
    occupied_slot_count = inventory_metrics["occupied_slot_count"]
    return {
        "file": path.name,
        "status": "ok",
        "map_id": getattr(state, "map_id", None),
        "round_index": getattr(state, "round_index", None),
        "inventory_count": inventory_count,
        "inventory_cells": sum(
            int(getattr(item, "cells", 0) or 0)
            for item in tuple(getattr(state, "inventory_items", ()) or ())
        ),
        "settlement_loss_units": loss_units,
        "payload_field_counts": _payload_field_counts(payload),
        "payload_field5_count": sum(1 for field_no, _wt, _v in payload_fields if field_no == 5),
        "payload_field6_count": sum(1 for field_no, _wt, _v in payload_fields if field_no == 6),
        "payload_field7_count": sum(1 for field_no, _wt, _v in payload_fields if field_no == 7),
        "payload_field8_count": sum(1 for field_no, _wt, _v in payload_fields if field_no == 8),
        "payload_field20_present": any(field_no == 20 for field_no, _wt, _v in payload_fields),
        "action_counts": action_counts,
        "full_observed_action_ids": full_action_ids,
        "raw_candidate_inventory_delta": (
            raw_candidate_count - inventory_count
            if raw_candidate_count is not None
            else None
        ),
        "occupied_slot_inventory_delta": (
            occupied_slot_count - inventory_count
            if occupied_slot_count is not None
            else None
        ),
        **frame_meta,
        **inventory_metrics,
    }


def summarize_settlement_payload_audit(
    paths: Iterable[Path] = (),
    *,
    top: int = 12,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in _resolve_paths(paths):
        try:
            rows.append(_audit_file(path))
        except Exception as exc:  # pragma: no cover - retained for CLI diagnostics.
            errors.append(f"{path}:{exc}")
    ready = [row for row in rows if row.get("status") == "ok"]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ready:
        groups[str(row.get("map_id") if row.get("map_id") is not None else "none")].append(row)
    group_rows = []
    for map_id, seq in groups.items():
        action_counts = Counter(
            str(action_id)
            for row in seq
            for action_id in row.get("full_observed_action_ids", ())
        )
        none_full_actions = sum(1 for row in seq if not row.get("full_observed_action_ids"))
        if none_full_actions:
            action_counts["none"] += none_full_actions
        group_rows.append(
            {
                "map_id": map_id,
                "files": len(seq),
                "inventory_count": _numeric_summary(row.get("inventory_count") for row in seq),
                "inventory_cells": _numeric_summary(row.get("inventory_cells") for row in seq),
                "inventory_slot_count": _counter_dict(
                    (row.get("inventory_slot_count") for row in seq),
                    top=top,
                ),
                "occupied_slot_count": _numeric_summary(row.get("occupied_slot_count") for row in seq),
                "raw_item_candidate_count": _numeric_summary(row.get("raw_item_candidate_count") for row in seq),
                "raw_candidate_inventory_delta": _numeric_summary(
                    row.get("raw_candidate_inventory_delta") for row in seq
                ),
                "occupied_slot_inventory_delta": _numeric_summary(
                    row.get("occupied_slot_inventory_delta") for row in seq
                ),
                "raw_duplicate_runtime_item_pair_count": _numeric_summary(
                    row.get("raw_duplicate_runtime_item_pair_count") for row in seq
                ),
                "raw_candidate_match_rows": sum(
                    1 for row in seq if row.get("raw_candidate_inventory_delta") == 0
                ),
                "occupied_slot_match_rows": sum(
                    1 for row in seq if row.get("occupied_slot_inventory_delta") == 0
                ),
                "payload_field5_count": _numeric_summary(row.get("payload_field5_count") for row in seq),
                "payload_field6_count": _numeric_summary(row.get("payload_field6_count") for row in seq),
                "payload_field7_count": _numeric_summary(row.get("payload_field7_count") for row in seq),
                "payload_field8_count": _numeric_summary(row.get("payload_field8_count") for row in seq),
                "payload_field20_present_rows": sum(1 for row in seq if row.get("payload_field20_present")),
                "settlement_outer_field_shapes": _counter_dict(
                    (row.get("settlement_outer_field_shape") for row in seq),
                    top=top,
                ),
                "settlement_outer_field3_present_rows": sum(
                    1 for row in seq if row.get("settlement_outer_field3_present")
                ),
                "settlement_outer_field4_present_rows": sum(
                    1 for row in seq if row.get("settlement_outer_field4_present")
                ),
                "settlement_outer_field5_present_rows": sum(
                    1 for row in seq if row.get("settlement_outer_field5_present")
                ),
                "settlement_outer_field6_count": _numeric_summary(
                    row.get("settlement_outer_field6_count") for row in seq
                ),
                "occupied_slot_field_shapes": _merge_count_mappings(
                    (row.get("occupied_slot_field_shapes", {}) for row in seq),
                    top=top,
                ),
                "empty_slot_field_shapes": _merge_count_mappings(
                    (row.get("empty_slot_field_shapes", {}) for row in seq),
                    top=top,
                ),
                "occupied_slot_int_field_counts": _merge_count_mappings(
                    (row.get("occupied_slot_int_field_counts", {}) for row in seq),
                    top=top,
                ),
                "empty_slot_int_field_counts": _merge_count_mappings(
                    (row.get("empty_slot_int_field_counts", {}) for row in seq),
                    top=top,
                ),
                "candidate_path_counts": _merge_count_mappings(
                    (row.get("candidate_path_counts", {}) for row in seq),
                    top=top,
                ),
                "full_observed_actions": dict(
                    sorted(action_counts.items(), key=lambda item: (-item[1], item[0]))[:top]
                ),
                "item_field_signatures": _counter_dict(
                    (
                        signature
                        for row in seq
                        for signature in row.get("item_field_signatures", {}).keys()
                    ),
                    top=top,
                ),
                "examples": [
                    str(row.get("file"))
                    for row in sorted(
                        seq,
                        key=lambda item: -int(item.get("inventory_count") or 0),
                    )[:3]
                ],
            }
        )
    return {
        "errors": errors,
        "files": len(rows),
        "settlement_rows": len(ready),
        "overall": {
            "raw_candidate_match_rows": sum(
                1 for row in ready if row.get("raw_candidate_inventory_delta") == 0
            ),
            "occupied_slot_match_rows": sum(
                1 for row in ready if row.get("occupied_slot_inventory_delta") == 0
            ),
            "payload_field20_present_rows": sum(
                1 for row in ready if row.get("payload_field20_present")
            ),
            "full_observed_action_rows": sum(
                1 for row in ready if row.get("full_observed_action_ids")
            ),
            "inventory_slot_count": _counter_dict(
                (row.get("inventory_slot_count") for row in ready),
                top=top,
            ),
            "raw_candidate_inventory_delta": _numeric_summary(
                row.get("raw_candidate_inventory_delta") for row in ready
            ),
            "occupied_slot_inventory_delta": _numeric_summary(
                row.get("occupied_slot_inventory_delta") for row in ready
            ),
            "raw_duplicate_runtime_item_pair_count": _numeric_summary(
                row.get("raw_duplicate_runtime_item_pair_count") for row in ready
            ),
            "settlement_outer_field_shapes": _counter_dict(
                (row.get("settlement_outer_field_shape") for row in ready),
                top=top,
            ),
            "settlement_outer_field3_present_rows": sum(
                1 for row in ready if row.get("settlement_outer_field3_present")
            ),
            "settlement_outer_field4_present_rows": sum(
                1 for row in ready if row.get("settlement_outer_field4_present")
            ),
            "settlement_outer_field5_present_rows": sum(
                1 for row in ready if row.get("settlement_outer_field5_present")
            ),
            "settlement_outer_field6_count": _numeric_summary(
                row.get("settlement_outer_field6_count") for row in ready
            ),
            "occupied_slot_field_shapes": _merge_count_mappings(
                (row.get("occupied_slot_field_shapes", {}) for row in ready),
                top=top,
            ),
            "empty_slot_field_shapes": _merge_count_mappings(
                (row.get("empty_slot_field_shapes", {}) for row in ready),
                top=top,
            ),
            "occupied_slot_int_field_counts": _merge_count_mappings(
                (row.get("occupied_slot_int_field_counts", {}) for row in ready),
                top=top,
            ),
            "empty_slot_int_field_counts": _merge_count_mappings(
                (row.get("empty_slot_int_field_counts", {}) for row in ready),
                top=top,
            ),
            "candidate_path_counts": _merge_count_mappings(
                (row.get("candidate_path_counts", {}) for row in ready),
                top=top,
            ),
        },
        "rows": sorted(
            group_rows,
            key=lambda row: (
                -float(row["inventory_count"]["max"] or 0.0),
                str(row["map_id"]),
            ),
        ),
    }


def _print_summary(result: Mapping[str, Any], *, top: int) -> None:
    overall = result["overall"]
    print(
        " ".join(
            (
                f"files={result['files']}",
                f"settlement_rows={result['settlement_rows']}",
                f"raw_candidate_match_rows={overall['raw_candidate_match_rows']}",
                f"occupied_slot_match_rows={overall['occupied_slot_match_rows']}",
                f"payload_f20_rows={overall['payload_field20_present_rows']}",
                f"full_observed_action_rows={overall['full_observed_action_rows']}",
                f"slot_counts={_format_counts(overall['inventory_slot_count'])}",
                f"raw_candidate_delta={_format_summary(overall['raw_candidate_inventory_delta'])}",
                f"occupied_slot_delta={_format_summary(overall['occupied_slot_inventory_delta'])}",
                f"raw_dup_pair={_format_summary(overall['raw_duplicate_runtime_item_pair_count'])}",
                f"outer_shapes={_format_counts(overall['settlement_outer_field_shapes'])}",
                f"outer_f3_rows={overall['settlement_outer_field3_present_rows']}",
                f"outer_f4_rows={overall['settlement_outer_field4_present_rows']}",
                f"outer_f5_rows={overall['settlement_outer_field5_present_rows']}",
                f"outer_f6={_format_summary(overall['settlement_outer_field6_count'])}",
                f"occupied_slot_shapes={_format_counts(overall['occupied_slot_field_shapes'])}",
                f"empty_slot_shapes={_format_counts(overall['empty_slot_field_shapes'])}",
                f"occupied_slot_int_fields={_format_counts(overall['occupied_slot_int_field_counts'])}",
                f"empty_slot_int_fields={_format_counts(overall['empty_slot_int_field_counts'])}",
                f"candidate_paths={_format_counts(overall['candidate_path_counts'])}",
            )
        )
    )
    for row in result["rows"][:top]:
        print(
            " ".join(
                (
                    f"map_id={row['map_id']}",
                    f"files={row['files']}",
                    f"inventory_count={_format_summary(row['inventory_count'])}",
                    f"inventory_cells={_format_summary(row['inventory_cells'])}",
                    f"slot_counts={_format_counts(row['inventory_slot_count'])}",
                    f"occupied_slots={_format_summary(row['occupied_slot_count'])}",
                    f"raw_candidates={_format_summary(row['raw_item_candidate_count'])}",
                    f"raw_candidate_delta={_format_summary(row['raw_candidate_inventory_delta'])}",
                    f"occupied_delta={_format_summary(row['occupied_slot_inventory_delta'])}",
                    f"raw_dup_pair={_format_summary(row['raw_duplicate_runtime_item_pair_count'])}",
                    f"raw_candidate_match_rows={row['raw_candidate_match_rows']}/{row['files']}",
                    f"occupied_slot_match_rows={row['occupied_slot_match_rows']}/{row['files']}",
                    f"payload_f5={_format_summary(row['payload_field5_count'])}",
                    f"payload_f6={_format_summary(row['payload_field6_count'])}",
                    f"payload_f7={_format_summary(row['payload_field7_count'])}",
                    f"payload_f8={_format_summary(row['payload_field8_count'])}",
                    f"payload_f20_rows={row['payload_field20_present_rows']}/{row['files']}",
                    f"outer_shapes={_format_counts(row['settlement_outer_field_shapes'])}",
                    f"outer_f3_rows={row['settlement_outer_field3_present_rows']}/{row['files']}",
                    f"outer_f4_rows={row['settlement_outer_field4_present_rows']}/{row['files']}",
                    f"outer_f5_rows={row['settlement_outer_field5_present_rows']}/{row['files']}",
                    f"outer_f6={_format_summary(row['settlement_outer_field6_count'])}",
                    f"occupied_slot_shapes={_format_counts(row['occupied_slot_field_shapes'])}",
                    f"empty_slot_shapes={_format_counts(row['empty_slot_field_shapes'])}",
                    f"occupied_slot_int_fields={_format_counts(row['occupied_slot_int_field_counts'])}",
                    f"empty_slot_int_fields={_format_counts(row['empty_slot_int_field_counts'])}",
                    f"candidate_paths={_format_counts(row['candidate_path_counts'])}",
                    f"full_actions={_format_counts(row['full_observed_actions'])}",
                    f"examples={','.join(row['examples'])}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit raw 0x002D Fatbeans settlement payload structure.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    result = summarize_settlement_payload_audit(args.paths, top=args.top)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result["errors"]:
            print(f"errors={len(result['errors'])}")
        _print_summary(result, top=args.top)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
