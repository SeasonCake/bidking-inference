"""Summarize raw table metadata against Fatbeans archive capture timing."""

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

from bidking_lab.extract.tables import load_table_rows  # noqa: E402

DEFAULT_SAMPLE_ROOT = ROOT / "data" / "samples" / "fatbeans"
DEFAULT_RAW_ROOT = ROOT / "data" / "raw"
_BIDMAP_CURRENT_COLUMN_COUNT = 23
_BIDMAP_COL_ROUND_CAPS = 14
_BIDMAP_COL_V300_FLAG_A = 8
_BIDMAP_COL_UNUSED_PLACEHOLDER = 16
_BIDMAP_COL_DROP_REF = 17
_BIDMAP_TARGET_MAP_IDS = (2401, 2404, 2406, 2501, 2506, 2508, 2601)
_ACTIVITY_MAP_RANGES = ((2521, 2530), (4521, 4530))
_RANKMAP_COL_LABEL = 2
_RANKMAP_COL_ROUND_BUCKET_WEIGHTS = 3
_RANKMAP_COL_CATEGORY_WEIGHTS = 4
_RANKMAP_COL_VALUE_WEIGHTS = 5
_RANKMAP_COL_BID_LADDER = 6
_VERSION_KEY_TOKENS = ("version", "hash", "fileversion", "tableversion")


def _read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8-sig").strip()


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


def _counter_dict(values: Iterable[Any], *, top: int = 12) -> dict[str, int]:
    counts: Counter[str] = Counter(
        str(value) if value not in (None, "") else "none"
        for value in values
    )
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top])


def _parse_json_list(value: Any) -> list[Any] | None:
    try:
        parsed = json.loads(str(value))
    except Exception:
        return None
    return parsed if isinstance(parsed, list) else None


def _looks_like_drop_ref(value: Any) -> bool:
    parsed = _parse_json_list(value)
    if not parsed or len(parsed) < 4:
        return False
    try:
        return int(parsed[0]) == 9999
    except (TypeError, ValueError):
        return False


def _drop_ref_pair(value: Any) -> str | None:
    parsed = _parse_json_list(value)
    if not parsed or len(parsed) < 4:
        return None
    try:
        return f"{int(parsed[2])}-{int(parsed[3])}"
    except (TypeError, ValueError):
        return None


def _drop_ref_pool_id(value: Any) -> int | None:
    parsed = _parse_json_list(value)
    if not parsed or len(parsed) < 2:
        return None
    try:
        return int(parsed[1])
    except (TypeError, ValueError):
        return None


def _canonical_json_cell(value: Any) -> str:
    parsed = _parse_json_list(value)
    if parsed is None:
        return str(value) if value not in (None, "") else "none"
    return json.dumps(
        parsed,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _read_table_rows(path: Path) -> tuple[list[list[str]], str | None]:
    try:
        return load_table_rows(path), None
    except Exception as exc:  # pragma: no cover - retained for CLI diagnostics.
        return [], f"{path}:{exc}"


def _bidmap_semantic_summary(tables_root: Path) -> dict[str, Any]:
    rows, parse_error = _read_table_rows(tables_root / "BidMap.txt")
    target_rows = {
        int(row[0]): row
        for row in rows
        if row and str(row[0]).isdigit() and int(row[0]) in _BIDMAP_TARGET_MAP_IDS
    }
    current_rows = [row for row in rows if len(row) == _BIDMAP_CURRENT_COLUMN_COUNT]
    target_details: list[dict[str, Any]] = []
    for map_id in _BIDMAP_TARGET_MAP_IDS:
        row = target_rows.get(map_id)
        if row is None:
            target_details.append({"map_id": map_id, "status": "missing"})
            continue
        drop_ref = (
            row[_BIDMAP_COL_DROP_REF]
            if len(row) > _BIDMAP_COL_DROP_REF
            else None
        )
        target_details.append(
            {
                "map_id": map_id,
                "status": "ok",
                "raw_column_count": len(row),
                "v300_flag_a": (
                    row[_BIDMAP_COL_V300_FLAG_A]
                    if len(row) > _BIDMAP_COL_V300_FLAG_A
                    else None
                ),
                "round_caps_candidate": (
                    row[_BIDMAP_COL_ROUND_CAPS]
                    if len(row) > _BIDMAP_COL_ROUND_CAPS
                    else None
                ),
                "col16_placeholder": (
                    row[_BIDMAP_COL_UNUSED_PLACEHOLDER]
                    if len(row) > _BIDMAP_COL_UNUSED_PLACEHOLDER
                    else None
                ),
                "drop_ref_col17": drop_ref,
                "drop_ref_pair": _drop_ref_pair(drop_ref),
                "drop_pool_id": _drop_ref_pool_id(drop_ref),
            }
        )
    return {
        "parse_error": parse_error,
        "row_count": len(rows),
        "column_count_counts": _counter_dict(len(row) for row in rows),
        "current_23_column_rows": len(current_rows),
        "col16_value_counts": _counter_dict(
            (
                row[_BIDMAP_COL_UNUSED_PLACEHOLDER]
                for row in current_rows
                if len(row) > _BIDMAP_COL_UNUSED_PLACEHOLDER
            ),
            top=8,
        ),
        "col16_drop_ref_like_rows": sum(
            1
            for row in current_rows
            if len(row) > _BIDMAP_COL_UNUSED_PLACEHOLDER
            and _looks_like_drop_ref(row[_BIDMAP_COL_UNUSED_PLACEHOLDER])
        ),
        "col17_drop_ref_like_rows": sum(
            1
            for row in current_rows
            if len(row) > _BIDMAP_COL_DROP_REF
            and _looks_like_drop_ref(row[_BIDMAP_COL_DROP_REF])
        ),
        "drop_ref_pair_counts": _counter_dict(
            (
                _drop_ref_pair(row[_BIDMAP_COL_DROP_REF])
                for row in current_rows
                if len(row) > _BIDMAP_COL_DROP_REF
            ),
            top=12,
        ),
        "target_maps": target_details,
    }


def _parse_drop_entries(rows: Iterable[list[str]]) -> dict[int, list[dict[str, int]]]:
    pools: dict[int, list[dict[str, int]]] = {}
    for row in rows:
        if len(row) < 5:
            continue
        try:
            pool_id = int(row[0])
            raw_entries = json.loads(row[4]) if row[4] else []
        except Exception:
            continue
        entries: list[dict[str, int]] = []
        for item in raw_entries:
            if not isinstance(item, list) or len(item) != 5:
                continue
            try:
                entries.append(
                    {
                        "category": int(item[0]),
                        "item_id": int(item[1]),
                        "n_min": int(item[2]),
                        "n_max": int(item[3]),
                        "weight": int(item[4]),
                    }
                )
            except (TypeError, ValueError):
                continue
        pools[pool_id] = entries
    return pools


def _range_key(entry: Mapping[str, int]) -> str:
    return f"{entry.get('n_min')}-{entry.get('n_max')}"


def _reachable_leaf_range_summary(
    pool_id: int | None,
    pools: Mapping[int, list[dict[str, int]]],
) -> dict[str, Any]:
    if pool_id is None:
        return {
            "status": "missing_pool_id",
            "visited_pool_count": 0,
            "leaf_entry_count": 0,
            "ref_entry_count": 0,
            "leaf_n_range_counts": {},
            "leaf_n_min_min": None,
            "leaf_n_max_max": None,
        }
    visited: set[int] = set()
    leaf_entries: list[dict[str, int]] = []
    ref_entries = 0

    def walk(current_pool_id: int, depth: int) -> None:
        nonlocal ref_entries
        if depth > 16 or current_pool_id in visited:
            return
        visited.add(current_pool_id)
        for entry in pools.get(current_pool_id, ()):
            if int(entry.get("category", 0)) == 9999:
                ref_entries += 1
                walk(int(entry["item_id"]), depth + 1)
            else:
                leaf_entries.append(entry)

    walk(pool_id, 0)
    n_mins = [entry["n_min"] for entry in leaf_entries]
    n_maxs = [entry["n_max"] for entry in leaf_entries]
    return {
        "status": "ok" if visited else "missing_pool",
        "visited_pool_count": len(visited),
        "leaf_entry_count": len(leaf_entries),
        "ref_entry_count": ref_entries,
        "leaf_n_range_counts": _counter_dict(
            (_range_key(entry) for entry in leaf_entries),
            top=12,
        ),
        "leaf_n_min_min": min(n_mins) if n_mins else None,
        "leaf_n_max_max": max(n_maxs) if n_maxs else None,
    }


def _drop_semantic_summary(
    tables_root: Path,
    *,
    target_maps: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    rows, parse_error = _read_table_rows(tables_root / "Drop.txt")
    pools = _parse_drop_entries(rows)
    all_entries = [entry for entries in pools.values() for entry in entries]
    leaf_entries = [entry for entry in all_entries if entry["category"] != 9999]
    ref_entries = [entry for entry in all_entries if entry["category"] == 9999]
    target_details: list[dict[str, Any]] = []
    for row in target_maps:
        if row.get("status") != "ok":
            target_details.append(
                {
                    "map_id": row.get("map_id"),
                    "status": "missing_bidmap",
                }
            )
            continue
        target_details.append(
            {
                "map_id": row.get("map_id"),
                "drop_pool_id": row.get("drop_pool_id"),
                **_reachable_leaf_range_summary(
                    row.get("drop_pool_id"),
                    pools,
                ),
            }
        )
    return {
        "parse_error": parse_error,
        "pool_count": len(pools),
        "entry_count": len(all_entries),
        "ref_entry_count": len(ref_entries),
        "leaf_entry_count": len(leaf_entries),
        "ref_n_range_counts": _counter_dict(
            (_range_key(entry) for entry in ref_entries),
            top=12,
        ),
        "leaf_n_range_counts": _counter_dict(
            (_range_key(entry) for entry in leaf_entries),
            top=12,
        ),
        "target_maps": target_details,
    }


def _activity_overlay_summary(tables_root: Path) -> list[dict[str, Any]]:
    bidmap_rows, bidmap_error = _read_table_rows(tables_root / "BidMap.txt")
    drop_rows, drop_error = _read_table_rows(tables_root / "Drop.txt")
    bidmap_by_id = {
        int(row[0]): row
        for row in bidmap_rows
        if row and str(row[0]).isdigit()
    }
    drop_ids = {
        int(row[0])
        for row in drop_rows
        if row and str(row[0]).isdigit()
    }
    out: list[dict[str, Any]] = []
    for start, end in _ACTIVITY_MAP_RANGES:
        map_ids = tuple(range(start, end + 1))
        present_rows = {
            map_id: bidmap_by_id[map_id]
            for map_id in map_ids
            if map_id in bidmap_by_id
        }
        drop_ref_pool_ids = {
            map_id: _drop_ref_pool_id(row[_BIDMAP_COL_DROP_REF])
            for map_id, row in present_rows.items()
            if len(row) > _BIDMAP_COL_DROP_REF
        }
        drop_ref_pairs = [
            _drop_ref_pair(row[_BIDMAP_COL_DROP_REF])
            for row in present_rows.values()
            if len(row) > _BIDMAP_COL_DROP_REF
        ]
        out.append(
            {
                "range": f"{start}-{end}",
                "bidmap_present_ids": sorted(present_rows),
                "bidmap_missing_ids": [
                    map_id for map_id in map_ids if map_id not in present_rows
                ],
                "drop_ref_pool_ids": dict(sorted(drop_ref_pool_ids.items())),
                "drop_present_ids": sorted(
                    map_id
                    for map_id, pool_id in drop_ref_pool_ids.items()
                    if pool_id in drop_ids
                ),
                "drop_missing_ids": sorted(
                    map_id
                    for map_id, pool_id in drop_ref_pool_ids.items()
                    if pool_id not in drop_ids
                ),
                "drop_ref_pair_counts": _counter_dict(drop_ref_pairs),
                "parse_error": bidmap_error or drop_error,
            }
        )
    return out


def _activity_rankmap_summary(tables_root: Path) -> list[dict[str, Any]]:
    rankmap_rows, rankmap_error = _read_table_rows(tables_root / "RankMap.txt")
    rankmap_by_id = {
        int(row[0]): row
        for row in rankmap_rows
        if row and str(row[0]).isdigit()
    }
    out: list[dict[str, Any]] = []
    for start, end in _ACTIVITY_MAP_RANGES:
        map_ids = tuple(range(start, end + 1))
        present_rows = {
            map_id: rankmap_by_id[map_id]
            for map_id in map_ids
            if map_id in rankmap_by_id
        }
        out.append(
            {
                "range": f"{start}-{end}",
                "rankmap_present_ids": sorted(present_rows),
                "rankmap_missing_ids": [
                    map_id for map_id in map_ids if map_id not in present_rows
                ],
                "label_counts": _counter_dict(
                    (
                        row[_RANKMAP_COL_LABEL]
                        for row in present_rows.values()
                        if len(row) > _RANKMAP_COL_LABEL
                    )
                ),
                "round_bucket_profile_counts": _counter_dict(
                    (
                        _canonical_json_cell(row[_RANKMAP_COL_ROUND_BUCKET_WEIGHTS])
                        for row in present_rows.values()
                        if len(row) > _RANKMAP_COL_ROUND_BUCKET_WEIGHTS
                    )
                ),
                "category_weight_profile_counts": _counter_dict(
                    (
                        _canonical_json_cell(row[_RANKMAP_COL_CATEGORY_WEIGHTS])
                        for row in present_rows.values()
                        if len(row) > _RANKMAP_COL_CATEGORY_WEIGHTS
                    )
                ),
                "value_weight_profile_counts": _counter_dict(
                    (
                        _canonical_json_cell(row[_RANKMAP_COL_VALUE_WEIGHTS])
                        for row in present_rows.values()
                        if len(row) > _RANKMAP_COL_VALUE_WEIGHTS
                    )
                ),
                "bid_ladder_profile_counts": _counter_dict(
                    (
                        _canonical_json_cell(row[_RANKMAP_COL_BID_LADDER])
                        for row in present_rows.values()
                        if len(row) > _RANKMAP_COL_BID_LADDER
                    )
                ),
                "parse_error": rankmap_error,
            }
        )
    return out


def _filelist_entry(filelist_text: str | None, table_path: str) -> str | None:
    if not filelist_text:
        return None
    prefix = f"{table_path}|"
    for line in filelist_text.splitlines():
        if line.startswith(prefix):
            return line
    return None


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


def _capture_summary(paths: Iterable[Path]) -> dict[str, Any]:
    timestamp_rows: list[tuple[int, str, str]] = []
    version_like_keys: set[str] = set()
    file_mtimes: list[str] = []
    loaded_files = 0
    parse_errors: list[str] = []
    for path in _resolve_paths(paths):
        if path.exists():
            file_mtimes.append(_mtime_iso(path) or "")
        try:
            rows = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # pragma: no cover - retained for CLI diagnostics.
            parse_errors.append(f"{path}:{exc}")
            continue
        if not isinstance(rows, list):
            parse_errors.append(f"{path}:not a JSON list")
            continue
        loaded_files += 1
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in row:
                lowered = str(key).lower()
                if any(token in lowered for token in _VERSION_KEY_TOKENS):
                    version_like_keys.add(str(key))
            timestamp = row.get("CaptureTimestamp")
            if isinstance(timestamp, bool):
                continue
            try:
                parsed_ts = int(timestamp)
            except (TypeError, ValueError):
                continue
            timestamp_rows.append(
                (
                    parsed_ts,
                    str(row.get("CaptureTime") or ""),
                    path.name,
                )
            )
    timestamp_rows.sort(key=lambda item: item[0])
    file_mtimes = sorted(value for value in file_mtimes if value)
    return {
        "sample_file_count": loaded_files,
        "sample_parse_errors": parse_errors[:8],
        "capture_timestamp_rows": len(timestamp_rows),
        "capture_time_min": timestamp_rows[0][1] if timestamp_rows else None,
        "capture_time_min_file": timestamp_rows[0][2] if timestamp_rows else None,
        "capture_time_max": timestamp_rows[-1][1] if timestamp_rows else None,
        "capture_time_max_file": timestamp_rows[-1][2] if timestamp_rows else None,
        "capture_file_mtime_min": file_mtimes[0] if file_mtimes else None,
        "capture_file_mtime_max": file_mtimes[-1] if file_mtimes else None,
        "capture_version_like_keys": sorted(version_like_keys),
    }


def summarize_archive_table_timing(
    paths: Iterable[Path] = (),
    *,
    raw_root: Path = DEFAULT_RAW_ROOT,
) -> dict[str, Any]:
    tables_root = raw_root / "tables"
    filelist_text = _read_text(raw_root / "filelist.txt")
    table_files = {
        "activity": tables_root / "Activity.txt",
        "bidmap": tables_root / "BidMap.txt",
        "drop": tables_root / "Drop.txt",
        "map": tables_root / "Map.txt",
        "rankmap": tables_root / "RankMap.txt",
    }
    bidmap_semantics = _bidmap_semantic_summary(tables_root)
    return {
        "raw_file_version": _read_text(raw_root / "fileVersion"),
        "raw_tables_file_version": _read_text(tables_root / "fileVersion"),
        "raw_filelist_header": (
            filelist_text.splitlines()[0] if filelist_text else None
        ),
        "raw_filelist_activity_entry": _filelist_entry(
            filelist_text,
            "Tables/Activity.txt",
        ),
        "raw_filelist_bidmap_entry": _filelist_entry(filelist_text, "Tables/BidMap.txt"),
        "raw_filelist_drop_entry": _filelist_entry(filelist_text, "Tables/Drop.txt"),
        "raw_files": {
            "file_version": _file_info(raw_root / "fileVersion"),
            "tables_file_version": _file_info(tables_root / "fileVersion"),
            "filelist": _file_info(raw_root / "filelist.txt"),
            "activity": _file_info(table_files["activity"]),
            "bidmap": _file_info(table_files["bidmap"]),
            "drop": _file_info(table_files["drop"]),
            "map": _file_info(table_files["map"]),
            "rankmap": _file_info(table_files["rankmap"]),
        },
        "bidmap_semantics": bidmap_semantics,
        "drop_semantics": _drop_semantic_summary(
            tables_root,
            target_maps=bidmap_semantics["target_maps"],
        ),
        "activity_overlay": _activity_overlay_summary(tables_root),
        "activity_rankmap": _activity_rankmap_summary(tables_root),
        **_capture_summary(paths),
    }


def _print_summary(result: dict[str, Any]) -> None:
    print(
        " ".join(
            (
                f"raw_file_version={result['raw_file_version']}",
                f"raw_tables_file_version={result['raw_tables_file_version']}",
                f"filelist_header={json.dumps(result['raw_filelist_header'], ensure_ascii=False)}",
                f"activity_entry={json.dumps(result['raw_filelist_activity_entry'], ensure_ascii=False)}",
                f"bidmap_entry={json.dumps(result['raw_filelist_bidmap_entry'], ensure_ascii=False)}",
                f"drop_entry={json.dumps(result['raw_filelist_drop_entry'], ensure_ascii=False)}",
            )
        )
    )
    for key, value in result["raw_files"].items():
        print(
            " ".join(
                (
                    f"raw_file={key}",
                    f"exists={value['exists']}",
                    f"length={value['length']}",
                    f"mtime={json.dumps(value['mtime'], ensure_ascii=False)}",
                )
            )
        )
    bidmap = result["bidmap_semantics"]
    print(
        " ".join(
            (
                f"bidmap_rows={bidmap['row_count']}",
                "bidmap_column_counts="
                + _format_counts(bidmap["column_count_counts"]),
                f"bidmap_current_23_rows={bidmap['current_23_column_rows']}",
                "bidmap_col16_values="
                + _format_counts(bidmap["col16_value_counts"]),
                f"bidmap_col16_drop_ref_like={bidmap['col16_drop_ref_like_rows']}",
                f"bidmap_col17_drop_ref_like={bidmap['col17_drop_ref_like_rows']}",
                "bidmap_drop_ref_pairs="
                + _format_counts(bidmap["drop_ref_pair_counts"]),
                f"bidmap_parse_error={json.dumps(bidmap['parse_error'], ensure_ascii=False)}",
            )
        )
    )
    for row in bidmap["target_maps"]:
        print(
            " ".join(
                (
                    f"bidmap_target_map={row['map_id']}",
                    f"status={row['status']}",
                    f"cols={row.get('raw_column_count')}",
                    f"v300_flag_a={row.get('v300_flag_a')}",
                    f"round_caps={json.dumps(row.get('round_caps_candidate'), ensure_ascii=False)}",
                    f"col16={json.dumps(row.get('col16_placeholder'), ensure_ascii=False)}",
                    f"col17={json.dumps(row.get('drop_ref_col17'), ensure_ascii=False)}",
                    f"drop_ref_pair={row.get('drop_ref_pair')}",
                )
            )
        )
    drop = result["drop_semantics"]
    print(
        " ".join(
            (
                f"drop_pools={drop['pool_count']}",
                f"drop_entries={drop['entry_count']}",
                f"drop_ref_entries={drop['ref_entry_count']}",
                f"drop_leaf_entries={drop['leaf_entry_count']}",
                "drop_ref_n_ranges=" + _format_counts(drop["ref_n_range_counts"]),
                "drop_leaf_n_ranges=" + _format_counts(drop["leaf_n_range_counts"]),
                f"drop_parse_error={json.dumps(drop['parse_error'], ensure_ascii=False)}",
            )
        )
    )
    for row in drop["target_maps"]:
        print(
            " ".join(
                (
                    f"drop_target_map={row['map_id']}",
                    f"status={row['status']}",
                    f"drop_pool_id={row.get('drop_pool_id')}",
                    f"visited_pools={row.get('visited_pool_count')}",
                    f"leaf_entries={row.get('leaf_entry_count')}",
                    "leaf_n_ranges="
                    + _format_counts(row.get("leaf_n_range_counts", {})),
                    f"leaf_n_max_max={row.get('leaf_n_max_max')}",
                )
            )
        )
    for row in result["activity_overlay"]:
        print(
            " ".join(
                (
                    f"activity_range={row['range']}",
                    f"bidmap_present={len(row['bidmap_present_ids'])}",
                    f"bidmap_missing={len(row['bidmap_missing_ids'])}",
                    f"drop_present={len(row['drop_present_ids'])}",
                    f"drop_missing={len(row['drop_missing_ids'])}",
                    "drop_ref_pairs="
                    + _format_counts(row["drop_ref_pair_counts"]),
                    f"parse_error={json.dumps(row['parse_error'], ensure_ascii=False)}",
                )
            )
        )
    for row in result["activity_rankmap"]:
        print(
            " ".join(
                (
                    f"rankmap_activity_range={row['range']}",
                    f"rankmap_present={len(row['rankmap_present_ids'])}",
                    f"rankmap_missing={len(row['rankmap_missing_ids'])}",
                    "labels=" + _format_counts(row["label_counts"]),
                    f"round_bucket_profiles={len(row['round_bucket_profile_counts'])}",
                    f"category_weight_profiles={len(row['category_weight_profile_counts'])}",
                    f"value_weight_profiles={len(row['value_weight_profile_counts'])}",
                    f"bid_ladder_profiles={len(row['bid_ladder_profile_counts'])}",
                    f"parse_error={json.dumps(row['parse_error'], ensure_ascii=False)}",
                )
            )
        )
    print(
        " ".join(
            (
                f"sample_files={result['sample_file_count']}",
                f"capture_rows={result['capture_timestamp_rows']}",
                f"capture_min={json.dumps(result['capture_time_min'], ensure_ascii=False)}",
                f"capture_min_file={json.dumps(result['capture_time_min_file'], ensure_ascii=False)}",
                f"capture_max={json.dumps(result['capture_time_max'], ensure_ascii=False)}",
                f"capture_max_file={json.dumps(result['capture_time_max_file'], ensure_ascii=False)}",
                f"capture_file_mtime_min={json.dumps(result['capture_file_mtime_min'], ensure_ascii=False)}",
                f"capture_file_mtime_max={json.dumps(result['capture_file_mtime_max'], ensure_ascii=False)}",
                f"capture_version_like_keys={','.join(result['capture_version_like_keys']) or '-'}",
                f"parse_errors={len(result['sample_parse_errors'])}",
            )
        )
    )


def _format_counts(counts: Mapping[str, int]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize raw table metadata against Fatbeans archive timing.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    result = summarize_archive_table_timing(args.paths, raw_root=args.raw_root)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(result)
    return 1 if result["sample_parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
