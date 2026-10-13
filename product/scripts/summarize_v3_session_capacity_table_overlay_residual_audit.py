"""Classify table/overlay/server-side residuals for session-capacity blockers.

This script is audit-only. It joins payload-verified session-capacity blockers
with the current raw table context so a settlement inventory over cap is not
silently converted into a sampler parameter problem.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
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

from bidking_lab.extract.bid_map_table import load_bid_map_table  # noqa: E402
from bidking_lab.extract.drop_table import DropEntry, load_drop_table  # noqa: E402
from bidking_lab.extract.tables import assert_uniform_columns, load_table_rows  # noqa: E402

DEFAULT_PAYLOAD_TABLE_GAP = (
    ROOT / ".tmp" / "codex" / "v3_session_capacity_payload_table_gap_2410_latest.json"
)
DEFAULT_PAYLOAD_OUTER_FIELDS = (
    ROOT / ".tmp" / "codex" / "v3_payload_outer_field_semantics_2410_latest.json"
)
DEFAULT_CSE = ROOT / "data" / "processed" / "v3_capacity_source_expansion_shadow.json"
DEFAULT_SAMPLE_ROOT = ROOT / "data" / "samples" / "fatbeans"
DEFAULT_RAW_ROOT = ROOT / "data" / "raw"
_VERSION_KEY_TOKENS = ("version", "hash", "fileversion", "tableversion")
_ACTIVITY_RANGES = ((2521, 2530), (4521, 4530))


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _counter_dict(values: Iterable[Any]) -> dict[str, int]:
    counts = Counter(
        str(value) if value not in (None, "") else "none"
        for value in values
    )
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _read_first_line(path: Path) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if text:
            return text
    return None


def _mtime_iso(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(
        path.stat().st_mtime,
        tz=timezone.utc,
    ).astimezone().isoformat()


def _file_info(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "length": path.stat().st_size if path.exists() else None,
        "mtime": _mtime_iso(path),
    }


def _max_timestamp_datetime(rows: Iterable[Mapping[str, Any]]) -> datetime | None:
    timestamps: list[int] = []
    for row in rows:
        value = row.get("CaptureTimestamp")
        if isinstance(value, bool):
            continue
        parsed = _int_or_none(value)
        if parsed is not None:
            timestamps.append(parsed)
    if not timestamps:
        return None
    return datetime.fromtimestamp(max(timestamps) / 1000, tz=timezone.utc).astimezone()


def _capture_context(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "rows": 0,
            "capture_time_min": None,
            "capture_time_max": None,
            "capture_timestamp_rows": 0,
            "version_like_keys": [],
            "parse_error": "missing",
        }
    try:
        raw = _load_json(path)
    except Exception as exc:  # pragma: no cover - retained for CLI diagnostics.
        return {
            "path": str(path),
            "exists": True,
            "rows": 0,
            "capture_time_min": None,
            "capture_time_max": None,
            "capture_timestamp_rows": 0,
            "version_like_keys": [],
            "parse_error": f"{type(exc).__name__}: {exc}",
        }
    if not isinstance(raw, list):
        return {
            "path": str(path),
            "exists": True,
            "rows": 0,
            "capture_time_min": None,
            "capture_time_max": None,
            "capture_timestamp_rows": 0,
            "version_like_keys": [],
            "parse_error": "not_a_json_list",
        }
    version_like_keys: set[str] = set()
    time_rows: list[tuple[int, str]] = []
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        for key in row:
            lowered = str(key).lower()
            if any(token in lowered for token in _VERSION_KEY_TOKENS):
                version_like_keys.add(str(key))
        ts = _int_or_none(row.get("CaptureTimestamp"))
        if ts is None:
            continue
        time_rows.append((ts, str(row.get("CaptureTime") or "")))
    time_rows.sort(key=lambda item: item[0])
    return {
        "path": str(path),
        "exists": True,
        "rows": len(raw),
        "capture_time_min": time_rows[0][1] if time_rows else None,
        "capture_time_max": time_rows[-1][1] if time_rows else None,
        "capture_timestamp_rows": len(time_rows),
        "capture_max_datetime": (
            _max_timestamp_datetime(row for row in raw if isinstance(row, Mapping))
        ),
        "version_like_keys": sorted(version_like_keys),
        "parse_error": None,
    }


def _parse_json_list(value: Any) -> list[Any] | None:
    try:
        parsed = json.loads(str(value))
    except Exception:
        return None
    return parsed if isinstance(parsed, list) else None


def _round_caps(raw_row: list[str]) -> list[int]:
    if len(raw_row) < 15:
        return []
    parsed = _parse_json_list(raw_row[14])
    if not parsed:
        return []
    out: list[int] = []
    for value in parsed:
        parsed_int = _int_or_none(value)
        if parsed_int is not None:
            out.append(parsed_int)
    return out


def _activity_range_for_map(map_id: int | None) -> str | None:
    if map_id is None:
        return None
    for start, end in _ACTIVITY_RANGES:
        if start <= map_id <= end:
            return f"{start}-{end}"
    return None


def _load_table_ids(path: Path) -> set[int]:
    if not path.exists():
        return set()
    try:
        rows = load_table_rows(path)
    except Exception:
        return set()
    out: set[int] = set()
    for row in rows:
        if row and str(row[0]).isdigit():
            out.add(int(row[0]))
    return out


def _activity_table_context(raw_root: Path, map_id: int | None) -> dict[str, Any]:
    tables_root = raw_root / "tables"
    activity_path = tables_root / "Activity.txt"
    rankmap_path = tables_root / "RankMap.txt"
    try:
        activity_rows = load_table_rows(activity_path) if activity_path.exists() else []
        activity_columns = assert_uniform_columns(activity_rows)
        activity_parse_status = "ok" if activity_path.exists() else "missing"
    except Exception as exc:  # pragma: no cover - retained for CLI diagnostics.
        activity_rows = []
        activity_columns = 0
        activity_parse_status = f"error:{type(exc).__name__}"
    activity_range = _activity_range_for_map(map_id)
    rankmap_ids = _load_table_ids(rankmap_path)
    return {
        "activity_table_present": activity_path.exists(),
        "activity_table_parse_status": activity_parse_status,
        "activity_table_rows": len(activity_rows),
        "activity_table_columns": activity_columns,
        "map_activity_range": activity_range,
        "map_in_activity_range": activity_range is not None,
        "rankmap_row_present_for_map": (
            map_id in rankmap_ids if map_id is not None else False
        ),
    }


def _walk_drop_entries(
    pool_id: int | None,
    pools: Mapping[int, Any],
) -> dict[str, Any]:
    if pool_id is None:
        return {
            "status": "missing_pool_id",
            "visited_pool_count": 0,
            "leaf_entry_count": 0,
            "ref_entry_count": 0,
            "leaf_n_min_min": None,
            "leaf_n_max_max": None,
            "leaf_n_range_counts": {},
        }
    visited: set[int] = set()
    leaf_entries: list[DropEntry] = []
    ref_entries = 0

    def walk(current_pool_id: int, depth: int) -> None:
        nonlocal ref_entries
        if depth > 16 or current_pool_id in visited:
            return
        visited.add(current_pool_id)
        pool = pools.get(current_pool_id)
        if pool is None:
            return
        for entry in pool.entries:
            if int(entry.category) == 9999:
                ref_entries += 1
                walk(int(entry.item_id), depth + 1)
            else:
                leaf_entries.append(entry)

    walk(pool_id, 0)
    n_mins = [int(entry.n_min) for entry in leaf_entries]
    n_maxs = [int(entry.n_max) for entry in leaf_entries]
    return {
        "status": "ok" if visited else "missing_pool",
        "visited_pool_count": len(visited),
        "leaf_entry_count": len(leaf_entries),
        "ref_entry_count": ref_entries,
        "leaf_n_min_min": min(n_mins) if n_mins else None,
        "leaf_n_max_max": max(n_maxs) if n_maxs else None,
        "leaf_n_range_counts": _counter_dict(
            f"{entry.n_min}-{entry.n_max}" for entry in leaf_entries
        ),
    }


def _raw_table_context(raw_root: Path, map_id: int | None) -> dict[str, Any]:
    tables_root = raw_root / "tables"
    bidmaps = load_bid_map_table(tables_root / "BidMap.txt")
    drops = load_drop_table(tables_root / "Drop.txt")
    bidmap = bidmaps.get(map_id) if map_id is not None else None
    raw_row = list(bidmap.raw_row) if bidmap is not None else []
    caps = _round_caps(raw_row)
    drop_context = _walk_drop_entries(
        int(bidmap.drop_pool_id) if bidmap is not None else None,
        drops,
    )
    activity_context = _activity_table_context(raw_root, map_id)
    activity_context["bidmap_row_present_for_map"] = map_id in bidmaps if map_id else False
    activity_context["drop_pool_present_for_map"] = (
        int(bidmap.drop_pool_id) in drops if bidmap is not None else False
    )
    return {
        "raw_file_version": _read_first_line(raw_root / "fileVersion"),
        "raw_tables_file_version": _read_first_line(tables_root / "fileVersion"),
        "raw_filelist_header": _read_first_line(raw_root / "filelist.txt"),
        "raw_files": {
            "bidmap": _file_info(tables_root / "BidMap.txt"),
            "drop": _file_info(tables_root / "Drop.txt"),
            "activity": _file_info(tables_root / "Activity.txt"),
            "rankmap": _file_info(tables_root / "RankMap.txt"),
        },
        "bidmap": {
            "present": bidmap is not None,
            "map_id": map_id,
            "name": bidmap.name if bidmap is not None else None,
            "raw_column_count": len(raw_row),
            "v300_flag_a": raw_row[8] if len(raw_row) > 8 else None,
            "raw_col14": raw_row[14] if len(raw_row) > 14 else None,
            "raw_col16": raw_row[16] if len(raw_row) > 16 else None,
            "raw_col17": raw_row[17] if len(raw_row) > 17 else None,
            "drop_pool_id": bidmap.drop_pool_id if bidmap is not None else None,
            "items_per_session_min": (
                bidmap.items_per_session_min if bidmap is not None else None
            ),
            "items_per_session_max": (
                bidmap.items_per_session_max if bidmap is not None else None
            ),
            "round_caps": caps,
            "round_cap_min": min(caps) if caps else None,
            "round_cap_max": max(caps) if caps else None,
        },
        "drop": drop_context,
        "activity_overlay": activity_context,
    }


def _payload_outer_rows_by_file(
    payload_outer_fields: Mapping[str, Any] | None,
) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    if not isinstance(payload_outer_fields, Mapping):
        return out
    for row in _as_list(payload_outer_fields.get("rows")):
        if not isinstance(row, Mapping):
            continue
        file = str(row.get("file") or "")
        if file:
            out[file] = row
    return out


def _index_cse_entries(cse: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    if not isinstance(cse, Mapping):
        return out
    for row in _as_list(cse.get("entries")):
        if not isinstance(row, Mapping):
            continue
        if row.get("scope") == "map_id" and row.get("group") is not None:
            out[str(row.get("group"))] = row
    return out


def _positive(value: Any) -> bool:
    parsed = _int_or_none(value)
    return parsed is not None and parsed > 0


def _current_table_cap_matches_payload_delta(
    table_delta: Mapping[str, Any],
    current_bidmap: Mapping[str, Any],
) -> bool:
    return (
        table_delta.get("bidmap_items_per_session_max")
        == current_bidmap.get("items_per_session_max")
        and table_delta.get("bidmap_raw_round_cap_max")
        == current_bidmap.get("round_cap_max")
    )


def _raw_table_newer_than_capture(
    capture_context: Mapping[str, Any],
    current_table_context: Mapping[str, Any],
) -> bool | None:
    capture_max = capture_context.get("capture_max_datetime")
    if not isinstance(capture_max, datetime):
        return None
    raw_files = _as_mapping(current_table_context.get("raw_files"))
    for key in ("bidmap", "activity", "rankmap"):
        mtime_text = _as_mapping(raw_files.get(key)).get("mtime")
        if not mtime_text:
            continue
        try:
            if datetime.fromisoformat(str(mtime_text)) > capture_max:
                return True
        except ValueError:
            continue
    return False


def _classify_row(
    row: Mapping[str, Any],
    *,
    capture_context: Mapping[str, Any],
    table_context: Mapping[str, Any],
    payload_outer_row: Mapping[str, Any],
    cse_entry: Mapping[str, Any],
) -> dict[str, Any]:
    table_delta = _as_mapping(row.get("table_delta"))
    bidmap = _as_mapping(table_context.get("bidmap"))
    drop = _as_mapping(table_context.get("drop"))
    activity = _as_mapping(table_context.get("activity_overlay"))
    payload = _as_mapping(row.get("payload"))
    event_payload = _as_mapping(row.get("event_payload"))
    local_cap_gap = (
        _positive(table_delta.get("inventory_minus_bidmap_items_per_session"))
        or _positive(table_delta.get("unique_non_temp_minus_bidmap_raw_round_cap"))
    )
    payload_verified = (
        payload.get("status") == "ok"
        and payload.get("raw_candidate_inventory_delta") == 0
        and payload.get("occupied_slot_inventory_delta") == 0
    )
    no_full_event_source = not any(
        _positive(event_payload.get(key))
        for key in (
            "full_action_payload_count",
            "full_skill_payload_count",
            "full_public_payload_count",
        )
    )
    drop_leaf_nmax = _int_or_none(drop.get("leaf_n_max_max"))
    drop_multiplicity_candidate = drop_leaf_nmax is not None and drop_leaf_nmax > 1
    activity_direct_candidate = (
        bool(activity.get("map_in_activity_range"))
        and bool(activity.get("rankmap_row_present_for_map"))
        and bool(activity.get("bidmap_row_present_for_map"))
    )
    outer_status = str(payload_outer_row.get("status") or "")
    outer_metadata_only = outer_status == "watch_outer_fields_metadata_only"
    cse_server_rows = _int_or_none(cse_entry.get("server_side_expansion_rows")) or 0
    raw_table_newer = _raw_table_newer_than_capture(capture_context, table_context)
    capture_version_keys = _as_list(capture_context.get("version_like_keys"))

    hypotheses: list[str] = []
    disproven_or_weak: list[str] = []
    if local_cap_gap:
        if not capture_version_keys:
            hypotheses.append("per_session_or_historical_table_version")
        hypotheses.append("external_overlay_table_not_in_current_raw_tables")
    if (
        payload_verified
        and no_full_event_source
        and (outer_metadata_only or not payload_outer_row)
    ):
        hypotheses.append("server_side_settlement_expansion_or_source_transform")
    if drop_multiplicity_candidate:
        hypotheses.append("drop_entry_multiplicity_semantics")
    else:
        disproven_or_weak.append("current_drop_leaf_nmax_not_count_expansion")
    if activity_direct_candidate:
        hypotheses.append("activity_overlay_direct_candidate")
    else:
        disproven_or_weak.append("current_activity_rankmap_not_direct_2410_source")
    if cse_server_rows <= 0:
        disproven_or_weak.append("current_cse_server_side_rows_not_confirmed")

    if local_cap_gap and payload_verified and no_full_event_source:
        status = "blocked_table_overlay_or_server_side_residual"
    else:
        status = "watch_table_overlay_residual_context"
    return {
        "file": row.get("file"),
        "map_id": row.get("map_id"),
        "map_family": row.get("map_family"),
        "status": status,
        "mechanism_class": (
            "table_version_or_external_overlay_or_server_side_transform_required"
            if status.startswith("blocked")
            else "table_overlay_residual_context"
        ),
        "payload_verified": payload_verified,
        "no_full_event_source": no_full_event_source,
        "local_table_cap_gap": local_cap_gap,
        "current_table_cap_matches_payload_delta": (
            _current_table_cap_matches_payload_delta(table_delta, bidmap)
        ),
        "raw_table_newer_than_capture": raw_table_newer,
        "capture_has_table_version_or_hash": bool(capture_version_keys),
        "drop_multiplicity_candidate": drop_multiplicity_candidate,
        "activity_overlay_direct_candidate": activity_direct_candidate,
        "outer_fields_metadata_only": outer_metadata_only,
        "cse_server_side_expansion_rows": cse_server_rows,
        "remaining_minimal_hypotheses": sorted(set(hypotheses)),
        "disproven_or_weak_paths": sorted(set(disproven_or_weak)),
        "table_delta": dict(table_delta),
        "capture_context": {
            key: value
            for key, value in capture_context.items()
            if key != "capture_max_datetime"
        },
        "current_table_context": {
            "raw_file_version": table_context.get("raw_file_version"),
            "raw_tables_file_version": table_context.get("raw_tables_file_version"),
            "raw_filelist_header": table_context.get("raw_filelist_header"),
            "raw_files": table_context.get("raw_files"),
            "bidmap": dict(bidmap),
            "drop": dict(drop),
            "activity_overlay": dict(activity),
        },
        "payload_outer_field_status": payload_outer_row.get("status"),
        "payload_outer_target_match_count": len(
            _as_list(payload_outer_row.get("target_matches"))
        ),
        "cse_entry": {
            "status": cse_entry.get("status"),
            "gate_reason": cse_entry.get("gate_reason"),
            "server_side_expansion_rows": cse_entry.get("server_side_expansion_rows"),
            "session_capacity_source_semantics_rows": cse_entry.get(
                "session_capacity_source_semantics_rows"
            ),
        },
    }


def summarize_session_capacity_table_overlay_residual(
    *,
    payload_table_gap: Mapping[str, Any],
    payload_outer_fields: Mapping[str, Any] | None = None,
    cse: Mapping[str, Any] | None = None,
    sample_root: Path = DEFAULT_SAMPLE_ROOT,
    raw_root: Path = DEFAULT_RAW_ROOT,
    focus_maps: Iterable[str] = (),
) -> dict[str, Any]:
    focus = {str(item) for item in focus_maps if str(item)}
    outer_by_file = _payload_outer_rows_by_file(payload_outer_fields)
    cse_by_map = _index_cse_entries(cse)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    table_context_cache: dict[str, Mapping[str, Any]] = {}
    for source_row in _as_list(payload_table_gap.get("rows")):
        if not isinstance(source_row, Mapping):
            continue
        map_id_text = str(source_row.get("map_id"))
        if focus and map_id_text not in focus:
            continue
        if not str(source_row.get("status") or "").startswith("blocked"):
            continue
        file = str(source_row.get("file") or "")
        if not file:
            continue
        try:
            map_id = _int_or_none(source_row.get("map_id"))
            if map_id_text not in table_context_cache:
                table_context_cache[map_id_text] = _raw_table_context(raw_root, map_id)
            capture_context = _capture_context(sample_root / file)
            rows.append(
                _classify_row(
                    source_row,
                    capture_context=capture_context,
                    table_context=table_context_cache[map_id_text],
                    payload_outer_row=_as_mapping(outer_by_file.get(file)),
                    cse_entry=_as_mapping(cse_by_map.get(map_id_text)),
                )
            )
        except Exception as exc:  # pragma: no cover - retained for CLI diagnostics.
            errors.append(f"{file}: {type(exc).__name__}: {exc}")
    blocked_rows = [row for row in rows if str(row.get("status")).startswith("blocked")]
    return {
        "status": (
            "blocked_table_overlay_or_server_side_residual_required"
            if blocked_rows
            else "watch_table_overlay_residual_audit_only"
        ),
        "shadow_only": True,
        "affects_bid": False,
        "errors": errors,
        "rows": rows,
        "summary": {
            "rows": len(rows),
            "files": len({row.get("file") for row in rows}),
            "maps": len({str(row.get("map_id")) for row in rows if row.get("map_id") is not None}),
            "blocked_rows": len(blocked_rows),
            "local_table_cap_gap_rows": sum(
                1 for row in rows if row.get("local_table_cap_gap") is True
            ),
            "current_table_cap_matches_payload_delta_rows": sum(
                1
                for row in rows
                if row.get("current_table_cap_matches_payload_delta") is True
            ),
            "raw_table_newer_than_capture_rows": sum(
                1 for row in rows if row.get("raw_table_newer_than_capture") is True
            ),
            "capture_version_or_hash_rows": sum(
                1 for row in rows if row.get("capture_has_table_version_or_hash") is True
            ),
            "drop_multiplicity_candidate_rows": sum(
                1 for row in rows if row.get("drop_multiplicity_candidate") is True
            ),
            "activity_overlay_direct_candidate_rows": sum(
                1 for row in rows if row.get("activity_overlay_direct_candidate") is True
            ),
            "outer_fields_metadata_only_rows": sum(
                1 for row in rows if row.get("outer_fields_metadata_only") is True
            ),
            "server_transform_open_rows": sum(
                1
                for row in rows
                if "server_side_settlement_expansion_or_source_transform"
                in _as_list(row.get("remaining_minimal_hypotheses"))
            ),
            "status_counts": _counter_dict(row.get("status") for row in rows),
            "mechanism_class_counts": _counter_dict(
                row.get("mechanism_class") for row in rows
            ),
            "remaining_hypothesis_counts": _counter_dict(
                hypothesis
                for row in rows
                for hypothesis in _as_list(row.get("remaining_minimal_hypotheses"))
            ),
            "weak_path_counts": _counter_dict(
                path
                for row in rows
                for path in _as_list(row.get("disproven_or_weak_paths"))
            ),
            "top_examples": rows[:3],
        },
    }


def _format_counts(counts: Mapping[str, Any]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def print_summary(result: Mapping[str, Any]) -> None:
    summary = _as_mapping(result.get("summary"))
    print(
        "status={status} rows={rows} maps={maps} blocked={blocked} "
        "local_cap_gap={local_cap_gap} current_table_match={table_match} "
        "raw_newer={raw_newer} capture_versions={capture_versions} "
        "drop_mult={drop_mult} activity_direct={activity_direct} "
        "outer_metadata={outer_metadata} server_open={server_open} "
        "hypotheses={hypotheses}".format(
            status=result.get("status"),
            rows=summary.get("rows"),
            maps=summary.get("maps"),
            blocked=summary.get("blocked_rows"),
            local_cap_gap=summary.get("local_table_cap_gap_rows"),
            table_match=summary.get("current_table_cap_matches_payload_delta_rows"),
            raw_newer=summary.get("raw_table_newer_than_capture_rows"),
            capture_versions=summary.get("capture_version_or_hash_rows"),
            drop_mult=summary.get("drop_multiplicity_candidate_rows"),
            activity_direct=summary.get("activity_overlay_direct_candidate_rows"),
            outer_metadata=summary.get("outer_fields_metadata_only_rows"),
            server_open=summary.get("server_transform_open_rows"),
            hypotheses=_format_counts(
                _as_mapping(summary.get("remaining_hypothesis_counts"))
            ),
        )
    )
    for row in _as_list(result.get("rows"))[:5]:
        if not isinstance(row, Mapping):
            continue
        table_context = _as_mapping(row.get("current_table_context"))
        bidmap = _as_mapping(table_context.get("bidmap"))
        drop = _as_mapping(table_context.get("drop"))
        activity = _as_mapping(table_context.get("activity_overlay"))
        print(
            "file={file} map={map_id} status={status} mechanism={mechanism} "
            "table_match={table_match} raw_version={raw_version} "
            "capture_versions={capture_versions} raw_newer={raw_newer} "
            "items={items_min}-{items_max} round_cap={round_cap} "
            "drop_leaf_nmax={drop_nmax} activity_direct={activity_direct} "
            "activity_range={activity_range} hypotheses={hypotheses}".format(
                file=row.get("file"),
                map_id=row.get("map_id"),
                status=row.get("status"),
                mechanism=row.get("mechanism_class"),
                table_match=row.get("current_table_cap_matches_payload_delta"),
                raw_version=table_context.get("raw_tables_file_version"),
                capture_versions=row.get("capture_has_table_version_or_hash"),
                raw_newer=row.get("raw_table_newer_than_capture"),
                items_min=bidmap.get("items_per_session_min"),
                items_max=bidmap.get("items_per_session_max"),
                round_cap=bidmap.get("round_cap_max"),
                drop_nmax=drop.get("leaf_n_max_max"),
                activity_direct=row.get("activity_overlay_direct_candidate"),
                activity_range=activity.get("map_activity_range"),
                hypotheses=",".join(
                    str(item)
                    for item in _as_list(row.get("remaining_minimal_hypotheses"))
                )
                or "-",
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Classify table/overlay/server-side residuals for session-capacity blockers.",
    )
    parser.add_argument("--payload-table-gap-json", type=Path, default=DEFAULT_PAYLOAD_TABLE_GAP)
    parser.add_argument("--payload-outer-fields-json", type=Path, default=DEFAULT_PAYLOAD_OUTER_FIELDS)
    parser.add_argument("--capacity-source-expansion-json", type=Path, default=DEFAULT_CSE)
    parser.add_argument("--sample-root", type=Path, default=DEFAULT_SAMPLE_ROOT)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--focus-map", action="append", default=[])
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    result = summarize_session_capacity_table_overlay_residual(
        payload_table_gap=_load_json(args.payload_table_gap_json),
        payload_outer_fields=(
            _load_json(args.payload_outer_fields_json)
            if args.payload_outer_fields_json.exists()
            else None
        ),
        cse=(
            _load_json(args.capacity_source_expansion_json)
            if args.capacity_source_expansion_json.exists()
            else None
        ),
        sample_root=args.sample_root,
        raw_root=args.raw_root,
        focus_maps=args.focus_map,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_summary(result)
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
