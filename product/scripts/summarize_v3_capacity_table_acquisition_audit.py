"""Summarize acquisition routes for v3 capacity-table detail blockers."""

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
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bidking_lab.extract.tables import assert_uniform_columns, load_table_rows  # noqa: E402

DEFAULT_CAPACITY_TABLE_AUDIT = (
    ROOT / ".tmp" / "codex" / "v3_capacity_table_audit_detail_summary_latest.json"
)
DEFAULT_CSE_ARTIFACT = ROOT / "data" / "processed" / "v3_capacity_source_expansion_shadow.json"
OVERLAY_METADATA_CORE_KEYS = (
    "raw_file_version",
    "raw_tables_file_version",
    "raw_filelist_header",
    "raw_tables_filelist_header",
    "activity_table_present",
    "activity_table_listed_in_filelist",
    "local_overlay_status",
)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _counter_dict(values: Iterable[Any], *, top: int) -> dict[str, int]:
    counts = Counter(
        str(value) if value not in (None, "") else "none"
        for value in values
    )
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:top])


def _numeric_summary(values: Iterable[Any]) -> dict[str, Any]:
    seq = tuple(value for value in (_float_or_none(value) for value in values) if value is not None)
    if not seq:
        return {"n": 0, "avg": None, "max": None}
    return {
        "n": len(seq),
        "avg": round(sum(seq) / len(seq), 3),
        "max": round(max(seq), 3),
    }


def _format_counts(counts: Mapping[str, int]) -> str:
    return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_first_line(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            text = line.strip()
            if text:
                return text
    except OSError:
        return None
    return None


def _filelist_contains(path: Path, needle: str) -> bool:
    if not path.exists():
        return False
    try:
        return needle in path.read_text(encoding="utf-8-sig")
    except OSError:
        return False


def _activity_table_parse_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "activity_table_parse_status": "missing",
            "activity_table_rows": 0,
            "activity_table_columns": 0,
            "activity_table_first_ids": [],
        }
    try:
        rows = load_table_rows(path)
        columns = assert_uniform_columns(rows)
    except Exception as exc:  # pragma: no cover - retained for CLI diagnostics.
        return {
            "activity_table_parse_status": f"error:{type(exc).__name__}",
            "activity_table_rows": 0,
            "activity_table_columns": 0,
            "activity_table_first_ids": [],
        }
    return {
        "activity_table_parse_status": "ok",
        "activity_table_rows": len(rows),
        "activity_table_columns": columns,
        "activity_table_first_ids": [row[0] for row in rows[:5] if row],
    }


def _current_table_overlay_metadata(root: Path = ROOT) -> dict[str, Any]:
    raw_filelist = root / "data" / "raw" / "filelist.txt"
    table_filelist = root / "data" / "raw" / "tables" / "filelist.txt"
    activity_table = root / "data" / "raw" / "tables" / "Activity.txt"
    activity_listed = _filelist_contains(
        raw_filelist,
        "Tables/Activity.txt",
    ) or _filelist_contains(table_filelist, "Tables/Activity.txt")
    activity_present = activity_table.exists()
    if activity_listed and not activity_present:
        status = "activity_listed_missing_locally"
    elif activity_present:
        status = "activity_table_available_locally"
    else:
        status = "no_activity_table_signal"
    return {
        "raw_file_version": _read_first_line(root / "data" / "raw" / "fileVersion"),
        "raw_tables_file_version": _read_first_line(
            root / "data" / "raw" / "tables" / "fileVersion"
        ),
        "raw_filelist_header": _read_first_line(raw_filelist),
        "raw_tables_filelist_header": _read_first_line(table_filelist),
        "activity_table_present": activity_present,
        "activity_table_listed_in_filelist": activity_listed,
        "local_overlay_status": status,
        **_activity_table_parse_metadata(activity_table),
    }


def _overlay_metadata_delta(
    artifact_overlay: Mapping[str, Any],
    current_overlay: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not artifact_overlay:
        return []
    out: list[dict[str, Any]] = []
    for key in OVERLAY_METADATA_CORE_KEYS:
        artifact_value = artifact_overlay.get(key)
        current_value = current_overlay.get(key)
        if artifact_value != current_value:
            out.append(
                {
                    "key": key,
                    "artifact": artifact_value,
                    "current": current_value,
                }
            )
    return out


def _dedupe_detail_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        key = (
            str(row.get("file_ref") or row.get("file") or ""),
            str(row.get("map_id") or ""),
            str(row.get("residual_mode") or ""),
            str(row.get("semantic_status") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(row))
    return out


def _map_table_contexts(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    for row in rows:
        map_id = str(row.get("map_id") if row.get("map_id") not in (None, "") else "none")
        contexts[map_id] = {
            "map_id": map_id,
            "map_name": row.get("map_name"),
            "status": row.get("status"),
            "bidmap_items_per_session_min": row.get("bidmap_items_per_session_min"),
            "bidmap_items_per_session_max": row.get("bidmap_items_per_session_max"),
            "bidmap_raw_column_count": row.get("bidmap_raw_column_count"),
            "bidmap_v300_flag_a": row.get("bidmap_v300_flag_a"),
            "bidmap_raw_col8": row.get("bidmap_raw_col8"),
            "bidmap_raw_col14": row.get("bidmap_raw_col14"),
            "bidmap_raw_col16": row.get("bidmap_raw_col16"),
            "bidmap_raw_col17": row.get("bidmap_raw_col17"),
            "bidmap_raw_round_cap_min": row.get("bidmap_raw_round_cap_min"),
            "bidmap_raw_round_cap_max": row.get("bidmap_raw_round_cap_max"),
            "sampler_possible_item_count_max": row.get(
                "sampler_possible_item_count_max"
            ),
            "sampler_max_count_per_draw": row.get("sampler_max_count_per_draw"),
            "sampler_entries_nmax_gt1": row.get("sampler_entries_nmax_gt1"),
            "sub_pool_count": row.get("sub_pool_count"),
        }
    return contexts


def _mechanism_from_detail(row: Mapping[str, Any]) -> str:
    value = row.get("mechanism_candidate")
    if value not in (None, ""):
        return str(value)
    residual = str(row.get("residual_mode") or "")
    semantic = str(row.get("semantic_status") or "")
    missing = _float_or_none(row.get("non_zodiac_missing_from_drop_universe_count"))
    if missing is not None and missing > 0:
        return "drop_universe_gap"
    if semantic == "watch_activity_extras_explain_drop_ref_gap":
        return "activity_extras_candidate"
    if residual == "round_cap_overflow" or semantic.startswith("blocked_round_cap"):
        return "round_cap_candidate_gap"
    if residual == "drop_ref_only_overflow" or semantic.startswith("blocked_drop_ref"):
        return "drop_ref_candidate_gap"
    if residual == "within_drop_ref":
        return "activity_extras_candidate"
    return "unclassified_capacity_detail"


def _next_check_from_detail(row: Mapping[str, Any]) -> str:
    value = row.get("next_check")
    if value not in (None, ""):
        return str(value)
    mechanism = _mechanism_from_detail(row)
    if mechanism == "round_cap_candidate_gap":
        return "check_per_session_table_version_or_external_overlay"
    if mechanism == "drop_ref_candidate_gap":
        return "check_drop_ref_source_semantics_or_activity_overlay"
    if mechanism == "activity_extras_candidate":
        return "verify_activity_extras_table"
    if mechanism == "drop_universe_gap":
        return "check_source_parser_drop_universe"
    return "manual_review"


def _source_signal(row: Mapping[str, Any]) -> str:
    value = row.get("source_signal")
    if value not in (None, ""):
        return str(value)
    has_full_action = bool(row.get("full_observed_action_ids") or ())
    has_public_total = bool(row.get("public_total_count_values") or ())
    return (
        ("has_full_action" if has_full_action else "no_full_action")
        + "/"
        + ("has_public_total" if has_public_total else "no_public_total")
    )


def _source_strength(source_signal: str) -> str:
    if "has_public_total" in source_signal:
        return "public_total_confirmed"
    if "has_full_action" in source_signal:
        return "full_action_confirmed"
    return "payload_only_or_unconfirmed"


def _activity_table_missing(overlay: Mapping[str, Any]) -> bool:
    return str(overlay.get("local_overlay_status") or "") in {
        "activity_listed_missing_locally",
        "v300_activity_listed_missing_locally",
    }


def _acquisition_route(
    row: Mapping[str, Any],
    *,
    table_context: Mapping[str, Any],
    overlay: Mapping[str, Any],
) -> str:
    mechanism = _mechanism_from_detail(row)
    source_strength = _source_strength(_source_signal(row))
    activity_missing = _activity_table_missing(overlay)
    v300_flag = _safe_int(table_context.get("bidmap_v300_flag_a"))
    col16 = table_context.get("bidmap_raw_col16")
    if mechanism == "round_cap_candidate_gap":
        if v300_flag == 1 and col16 in ("[[]]", None, ""):
            return "table_version_or_external_overlay_required"
        return "round_cap_semantics_review_required"
    if mechanism == "drop_ref_candidate_gap":
        if source_strength in {"public_total_confirmed", "full_action_confirmed"}:
            return "drop_ref_overlay_or_source_semantics_required"
        return "payload_only_drop_ref_source_semantics_required"
    if mechanism == "activity_extras_candidate":
        if activity_missing:
            return "missing_activity_table_overlay_required"
        return "activity_extras_table_verification_required"
    if mechanism == "drop_universe_gap":
        return "source_parser_drop_universe_required"
    return "manual_acquisition_review_required"


def summarize_acquisition_audit(
    capacity_audit: Mapping[str, Any],
    *,
    cse_artifact: Mapping[str, Any] | None = None,
    current_table_overlay_metadata: Mapping[str, Any] | None = None,
    top: int = 12,
) -> dict[str, Any]:
    rows = [_as_mapping(row) for row in _as_list(capacity_audit.get("rows"))]
    detail_rows = [
        _as_mapping(row) for row in _as_list(capacity_audit.get("detail_rows"))
    ]
    unique_details = _dedupe_detail_rows(detail_rows)
    contexts = _map_table_contexts(rows)
    artifact_overlay = _as_mapping(
        _as_mapping(cse_artifact).get("table_overlay_metadata")
    )
    current_overlay = (
        _as_mapping(current_table_overlay_metadata)
        if current_table_overlay_metadata is not None
        else _current_table_overlay_metadata(ROOT)
    )
    overlay_delta = _overlay_metadata_delta(artifact_overlay, current_overlay)

    enriched: list[dict[str, Any]] = []
    for row in unique_details:
        map_id = str(row.get("map_id") if row.get("map_id") not in (None, "") else "none")
        context = contexts.get(map_id, {})
        source_signal = _source_signal(row)
        item = {
            **dict(row),
            "mechanism_candidate": _mechanism_from_detail(row),
            "next_check": _next_check_from_detail(row),
            "source_signal": source_signal,
            "source_strength": _source_strength(source_signal),
            "acquisition_route": _acquisition_route(
                row,
                table_context=context,
                overlay=current_overlay,
            ),
            "table_context": dict(context),
        }
        enriched.append(item)

    blockers: list[str] = []
    if not detail_rows:
        blockers.append("capacity table detail rows are missing")
    if any(
        row.get("acquisition_route") == "missing_activity_table_overlay_required"
        for row in enriched
    ):
        blockers.append("activity overlay table is listed in filelist but missing locally")
    if overlay_delta:
        blockers.append("CSE artifact table overlay metadata is stale versus current raw tables")
    if any(
        row.get("acquisition_route") == "table_version_or_external_overlay_required"
        for row in enriched
    ):
        blockers.append("round-cap detail rows require per-session table version or overlay acquisition")
    if any(
        row.get("acquisition_route")
        in {
            "drop_ref_overlay_or_source_semantics_required",
            "payload_only_drop_ref_source_semantics_required",
        }
        for row in enriched
    ):
        blockers.append("drop-ref detail rows require source semantics or overlay acquisition")

    return {
        "status": "blocked_acquisition_required" if blockers else "watch",
        "reason": "; ".join(blockers) if blockers else "capacity detail rows are routable",
        "capacity_case": capacity_audit.get("case"),
        "capacity_bucket": capacity_audit.get("bucket"),
        "detail_rows": len(detail_rows),
        "unique_detail_rows": len(enriched),
        "unique_files": len({str(row.get("file_ref") or row.get("file")) for row in enriched}),
        "table_overlay_metadata": dict(current_overlay),
        "current_table_overlay_metadata": dict(current_overlay),
        "artifact_table_overlay_metadata": dict(artifact_overlay),
        "table_overlay_metadata_stale": bool(overlay_delta),
        "table_overlay_metadata_delta": overlay_delta,
        "mechanism_candidate_counts": _counter_dict(
            (row.get("mechanism_candidate") for row in enriched),
            top=top,
        ),
        "next_check_counts": _counter_dict(
            (row.get("next_check") for row in enriched),
            top=top,
        ),
        "acquisition_route_counts": _counter_dict(
            (row.get("acquisition_route") for row in enriched),
            top=top,
        ),
        "source_strength_counts": _counter_dict(
            (row.get("source_strength") for row in enriched),
            top=top,
        ),
        "map_counts": _counter_dict((row.get("map_id") for row in enriched), top=top),
        "map_family_counts": _counter_dict(
            (row.get("map_family") for row in enriched),
            top=top,
        ),
        "truth_prior_delta": _numeric_summary(
            row.get("truth_prior_max_delta") for row in enriched
        ),
        "drop_ref_excess_after_temp": _numeric_summary(
            row.get("drop_ref_excess_after_temp_zodiac_count") for row in enriched
        ),
        "round_cap_excess_after_temp": _numeric_summary(
            row.get("round_cap_excess_after_temp_zodiac_count") for row in enriched
        ),
        "top_examples": sorted(
            enriched,
            key=lambda row: (
                str(row.get("acquisition_route") or ""),
                str(row.get("map_id") or ""),
                str(row.get("file") or ""),
            ),
        )[:top],
    }


def _print_summary(result: Mapping[str, Any]) -> None:
    overlay = _as_mapping(result.get("table_overlay_metadata"))
    artifact_overlay = _as_mapping(result.get("artifact_table_overlay_metadata"))
    print(
        " ".join(
            (
                f"status={result.get('status')}",
                f"detail_rows={result.get('detail_rows')}",
                f"unique_detail_rows={result.get('unique_detail_rows')}",
                "overlay_status="
                f"{overlay.get('local_overlay_status')}",
                "artifact_overlay_status="
                f"{artifact_overlay.get('local_overlay_status')}",
                "metadata_stale="
                f"{result.get('table_overlay_metadata_stale')}",
                "activity_table_present="
                f"{overlay.get('activity_table_present')}",
                "activity_parse="
                f"{overlay.get('activity_table_parse_status')}",
                "activity_rows="
                f"{overlay.get('activity_table_rows')}",
                "activity_cols="
                f"{overlay.get('activity_table_columns')}",
                "routes="
                f"{_format_counts(_as_mapping(result.get('acquisition_route_counts')))}",
                "next_checks="
                f"{_format_counts(_as_mapping(result.get('next_check_counts')))}",
                "source_strength="
                f"{_format_counts(_as_mapping(result.get('source_strength_counts')))}",
                "maps="
                f"{_format_counts(_as_mapping(result.get('map_counts')))}",
            )
        )
    )
    for row in result.get("top_examples") or ():
        if not isinstance(row, Mapping):
            continue
        context = _as_mapping(row.get("table_context"))
        print(
            " ".join(
                (
                    f"example={row.get('map_id')}:{row.get('file')}",
                    f"route={row.get('acquisition_route')}",
                    f"next_check={row.get('next_check')}",
                    f"source={row.get('source_strength')}",
                    f"truth_delta={row.get('truth_prior_max_delta')}",
                    f"drop_after_temp={row.get('drop_ref_excess_after_temp_zodiac_count')}",
                    f"round_after_temp={row.get('round_cap_excess_after_temp_zodiac_count')}",
                    f"raw_col14={json.dumps(context.get('bidmap_raw_col14'), ensure_ascii=False)}",
                    f"raw_col17={json.dumps(context.get('bidmap_raw_col17'), ensure_ascii=False)}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Route v3 capacity-table detail blockers to acquisition checks.",
    )
    parser.add_argument(
        "--capacity-table-audit-json",
        type=Path,
        default=DEFAULT_CAPACITY_TABLE_AUDIT,
    )
    parser.add_argument(
        "--cse-json",
        type=Path,
        default=DEFAULT_CSE_ARTIFACT,
    )
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    capacity_audit = _load_json(args.capacity_table_audit_json)
    cse_artifact = _load_json(args.cse_json) if args.cse_json.exists() else {}
    result = summarize_acquisition_audit(
        _as_mapping(capacity_audit),
        cse_artifact=_as_mapping(cse_artifact),
        top=args.top,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
