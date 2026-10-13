"""Build a file-level manifest for Fatbeans archive samples.

The manifest is classification metadata only. It does not move or mutate the
capture files, and it keeps file counts separate from derived pre-bid windows.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansCaptureEvents,
    parse_fatbeans_capture,
)
from scripts.evaluate_fatbeans_v3_samples import (  # noqa: E402
    _default_paths,
    _iter_paths,
    _round_rows_for_events,
)

EXACT_PUBLIC_INFO_IDS = {
    200009,  # all item total cells
    200010,  # purple total cells
    200011,  # gold total cells
    200012,  # red total cells
    200017,  # all item count
    200018,  # purple count
    200019,  # gold count
    200020,  # red count
}

HARD_OBSERVED_PUBLIC_INFO_IDS = {
    200001,  # all purple outlines
    200002,  # all gold outlines
    200003,  # all red outlines
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


def _relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _counter_dict(counter: Counter[int | str]) -> dict[str, int]:
    def key_sort(item: tuple[int | str, int]) -> tuple[int, str]:
        key = str(item[0])
        return (0, f"{int(key):012d}") if key.isdigit() else (1, key)

    return {str(key): int(value) for key, value in sorted(counter.items(), key=key_sort)}


def _sorted_ints(values: Iterable[int | None]) -> list[int]:
    return sorted({int(value) for value in values if value is not None})


def _window_status_counts(rows: list[dict[str, Any]]) -> Counter[str]:
    return Counter(str(row.get("status") or "unknown") for row in rows)


def _file_status(
    *,
    bid_windows: int,
    ready_windows: int,
    no_state_windows: int,
    constraint_conflict_windows: int,
) -> tuple[str, str, str, bool]:
    if bid_windows == 0:
        return ("invalid", "no_bid_windows", "exclude_no_prebid_windows", False)
    if ready_windows == bid_windows:
        return ("valid", "ready_only", "use_all_windows_for_metrics", True)
    if ready_windows > 0:
        return (
            "mixed",
            "ready_with_gaps_or_conflicts",
            "use_ready_windows_exclude_bad_windows",
            True,
        )
    if no_state_windows:
        return ("invalid", "no_state_only", "exclude_capture_gap", False)
    if constraint_conflict_windows:
        return ("invalid", "constraint_conflict_only", "exclude_constraint_conflict", False)
    return ("invalid", "no_ready_windows", "exclude_no_ready_windows", False)


def _observed_settlement_consistency(events: FatbeansCaptureEvents) -> dict[str, Any]:
    inventory_states = [
        state for state in events.states if getattr(state, "inventory_items", ())
    ]
    if not inventory_states:
        return {
            "observed_settlement_hard_items": 0,
            "observed_settlement_missing_items": 0,
            "observed_settlement_quality_mismatch_items": 0,
            "observed_settlement_missing_examples": [],
            "observed_settlement_quality_mismatch_examples": [],
        }
    final_state = inventory_states[-1]
    final_by_runtime = {
        getattr(item, "runtime_id", None): item
        for item in getattr(final_state, "inventory_items", ()) or ()
        if getattr(item, "runtime_id", None) is not None
    }
    hard_count = 0
    missing_count = 0
    quality_mismatch_count = 0
    missing_examples: list[dict[str, Any]] = []
    quality_mismatch_examples: list[dict[str, Any]] = []

    def record_item(
        *,
        source: str,
        source_id: int,
        sort_id: Any,
        item: Any,
    ) -> None:
        nonlocal hard_count, missing_count, quality_mismatch_count
        runtime_id = getattr(item, "runtime_id", None)
        hard_count += 1
        final_item = final_by_runtime.get(runtime_id)
        if final_item is None:
            missing_count += 1
            if len(missing_examples) < 8:
                missing_examples.append(
                    {
                        "source": source,
                        "source_id": source_id,
                        "sort": sort_id,
                        "runtime_id": runtime_id,
                        "local_index": getattr(item, "local_index", None),
                        "quality": getattr(item, "quality", None),
                        "item_id": getattr(item, "item_id", None),
                    }
                )
            return
        observed_quality = getattr(item, "quality", None)
        final_quality = getattr(final_item, "quality", None)
        if observed_quality is not None and final_quality != observed_quality:
            quality_mismatch_count += 1
            if len(quality_mismatch_examples) < 8:
                quality_mismatch_examples.append(
                    {
                        "source": source,
                        "source_id": source_id,
                        "sort": sort_id,
                        "runtime_id": runtime_id,
                        "observed_quality": observed_quality,
                        "final_quality": final_quality,
                    }
                )

    for state in events.states:
        if state is final_state:
            continue
        sort_id = getattr(state, "sort_id", None)
        for info in getattr(state, "public_infos", ()) or ():
            info_id = int(getattr(info, "info_id", 0) or 0)
            if info_id not in HARD_OBSERVED_PUBLIC_INFO_IDS:
                continue
            for item in getattr(info, "observed_items", ()) or ():
                record_item(
                    source="public_info",
                    source_id=info_id,
                    sort_id=sort_id,
                    item=item,
                )
        for result in getattr(state, "action_results", ()) or ():
            action_id = int(getattr(result, "action_id", 0) or 0)
            for item in getattr(result, "observed_items", ()) or ():
                record_item(
                    source="action_result",
                    source_id=action_id,
                    sort_id=sort_id,
                    item=item,
                )
        for reveal in getattr(state, "skill_reveals", ()) or ():
            skill_id = int(getattr(reveal, "skill_id", 0) or 0)
            for item in getattr(reveal, "observed_items", ()) or ():
                record_item(
                    source="skill_reveal",
                    source_id=skill_id,
                    sort_id=sort_id,
                    item=item,
                )
    return {
        "observed_settlement_hard_items": hard_count,
        "observed_settlement_missing_items": missing_count,
        "observed_settlement_quality_mismatch_items": quality_mismatch_count,
        "observed_settlement_missing_examples": missing_examples,
        "observed_settlement_quality_mismatch_examples": quality_mismatch_examples,
    }


def _file_manifest_for_parse_error(path: Path, exc: Exception) -> dict[str, Any]:
    return {
        "file": _relative_path(path),
        "filename": path.name,
        "parsed": False,
        "sample_class": "invalid",
        "status": "parse_error",
        "cleanup_action": "quarantine_parse_error",
        "usable_for_metrics": False,
        "parse_error": type(exc).__name__,
        "state_count": 0,
        "send_count": 0,
        "bid_send_count": 0,
        "action_send_count": 0,
        "frame_count": 0,
        "packet_count": 0,
        "bid_windows": 0,
        "ready_windows": 0,
        "no_state_windows": 0,
        "constraint_conflict_windows": 0,
        "window_status_counts": {},
        "observed_rounds": [],
        "ready_rounds": [],
        "map_ids": [],
        "session_ids": [],
        "session_count": 0,
        "hero_ids": [],
        "public_info_counts": {},
        "exact_public_info_counts": {},
        "action_result_counts": {},
        "skill_reveal_counts": {},
        "observed_settlement_hard_items": 0,
        "observed_settlement_missing_items": 0,
        "observed_settlement_quality_mismatch_items": 0,
        "observed_settlement_missing_examples": [],
        "observed_settlement_quality_mismatch_examples": [],
    }


def _file_manifest_for_events(path: Path, events: FatbeansCaptureEvents) -> dict[str, Any]:
    rows = _round_rows_for_events(path, events, tables=None, posterior_trials=0)
    status_counts = _window_status_counts(rows)
    bid_sends = [send for send in events.sends if getattr(send, "kind", "") == "bid"]
    action_sends = [send for send in events.sends if getattr(send, "kind", "") == "action"]
    bid_windows = len(rows)
    ready_windows = status_counts.get("ready", 0)
    no_state_windows = status_counts.get("no_state", 0)
    constraint_conflict_windows = status_counts.get("constraint_conflict", 0)
    recovered_first_prebid_windows = sum(
        1 for row in rows if row.get("recovered_first_prebid_state")
    )
    recovered_first_prebid_sources: Counter[str] = Counter(
        str(row.get("recovered_first_prebid_source") or "unknown")
        for row in rows
        if row.get("recovered_first_prebid_state")
    )
    sample_class, status, cleanup_action, usable_for_metrics = _file_status(
        bid_windows=bid_windows,
        ready_windows=ready_windows,
        no_state_windows=no_state_windows,
        constraint_conflict_windows=constraint_conflict_windows,
    )
    path_parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    if "fatbeans_invalid" in path_parts or name.startswith("fatbeans_invalid_"):
        sample_class = "invalid"
        status = "quarantined_sample"
        cleanup_action = "quarantine_prior_invalid_sample"
        usable_for_metrics = False
    observed_consistency = _observed_settlement_consistency(events)
    if sample_class != "invalid" and (
        observed_consistency["observed_settlement_missing_items"] > 0
        or observed_consistency["observed_settlement_quality_mismatch_items"] > 0
    ):
        sample_class = "mixed"
        status = "observed_settlement_inconsistency"
        cleanup_action = "exclude_strict_hard_evidence_metrics"
        usable_for_metrics = False

    public_info_counts: Counter[int] = Counter()
    exact_public_info_counts: Counter[int] = Counter()
    action_result_counts: Counter[int] = Counter()
    skill_reveal_counts: Counter[int] = Counter()
    hero_ids: set[int] = set()
    session_ids: set[str] = set()
    for state in events.states:
        if state.session_id:
            session_ids.add(str(state.session_id))
        for bid in getattr(state, "bids", ()):
            if getattr(bid, "hero_id", None) is not None:
                hero_ids.add(int(bid.hero_id))
        for info in state.public_infos:
            public_info_counts[int(info.info_id)] += 1
            if int(info.info_id) in EXACT_PUBLIC_INFO_IDS:
                exact_public_info_counts[int(info.info_id)] += 1
        for result in state.action_results:
            action_result_counts[int(result.action_id)] += 1
        for reveal in state.skill_reveals:
            skill_reveal_counts[int(reveal.skill_id)] += 1
            if reveal.hero_id is not None:
                hero_ids.add(int(reveal.hero_id))
    for send in events.sends:
        if send.session_id:
            session_ids.add(str(send.session_id))
    for status_event in events.statuses:
        if status_event.session_id:
            session_ids.add(str(status_event.session_id))
    if len(session_ids) > 1 and sample_class != "invalid":
        sample_class = "mixed"
        status = "multi_session_capture"
        cleanup_action = "exclude_strict_session_metrics"
        usable_for_metrics = False

    return {
        "file": _relative_path(path),
        "filename": path.name,
        "parsed": True,
        "sample_class": sample_class,
        "status": status,
        "cleanup_action": cleanup_action,
        "usable_for_metrics": usable_for_metrics,
        "parse_error": None,
        "state_count": len(events.states),
        "send_count": len(events.sends),
        "bid_send_count": len(bid_sends),
        "action_send_count": len(action_sends),
        "frame_count": len(events.frames),
        "packet_count": len(events.packets),
        "bid_windows": bid_windows,
        "ready_windows": ready_windows,
        "no_state_windows": no_state_windows,
        "constraint_conflict_windows": constraint_conflict_windows,
        "recovered_first_prebid_windows": recovered_first_prebid_windows,
        "recovered_first_prebid_sources": _counter_dict(
            recovered_first_prebid_sources
        ),
        "window_status_counts": _counter_dict(status_counts),
        "observed_rounds": _sorted_ints(state.round_index for state in events.states),
        "ready_rounds": _sorted_ints(
            row.get("round") for row in rows if row.get("status") == "ready"
        ),
        "map_ids": _sorted_ints(state.map_id for state in events.states),
        "session_ids": sorted(session_ids),
        "session_count": len(session_ids),
        "hero_ids": sorted(hero_ids),
        "public_info_counts": _counter_dict(public_info_counts),
        "exact_public_info_counts": _counter_dict(exact_public_info_counts),
        "action_result_counts": _counter_dict(action_result_counts),
        "skill_reveal_counts": _counter_dict(skill_reveal_counts),
        **observed_consistency,
    }


def build_manifest(
    paths: Iterable[Path],
    *,
    cohort_role: str | None = None,
    metric_scope: str | None = None,
    cohort_note: str | None = None,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    expanded = _iter_paths(paths)
    for path in expanded:
        try:
            events = parse_fatbeans_capture(path)
        except Exception as exc:
            files.append(_file_manifest_for_parse_error(path, exc))
            continue
        files.append(_file_manifest_for_events(path, events))

    summary_cohort_metadata = {
        key: value
        for key, value in {
            "cohort_role": cohort_role,
            "metric_scope": metric_scope,
            "cohort_note": cohort_note,
        }.items()
        if value
    }
    if summary_cohort_metadata:
        summary_cohort_metadata["affects_bid"] = False
        cohort_metadata = {
            key: value
            for key, value in {
                "cohort_role": cohort_role,
                "affects_bid": False,
            }.items()
            if value is not None
        }
        for row in files:
            row.update(cohort_metadata)

    sample_classes = Counter(str(row["sample_class"]) for row in files)
    file_statuses = Counter(str(row["status"]) for row in files)
    window_statuses: Counter[str] = Counter()
    public_info_counts: Counter[str] = Counter()
    exact_public_info_counts: Counter[str] = Counter()
    recovered_first_prebid_sources: Counter[str] = Counter()
    for row in files:
        window_statuses.update(row["window_status_counts"])
        public_info_counts.update(row["public_info_counts"])
        exact_public_info_counts.update(row["exact_public_info_counts"])
        recovered_first_prebid_sources.update(
            row.get("recovered_first_prebid_sources") or {}
        )

    summary = {
        "source": "fatbeans_archive_v3_manifest",
        "note": (
            "file counts are real capture JSON files; prebid windows are derived "
            "from player bid SEND 0x0022 boundaries and are not generated samples"
        ),
        "files": len(files),
        "parsed_files": sum(1 for row in files if row["parsed"]),
        "parse_errors": sum(1 for row in files if row["status"] == "parse_error"),
        "valid_files": sample_classes.get("valid", 0),
        "mixed_files": sample_classes.get("mixed", 0),
        "invalid_files": sample_classes.get("invalid", 0),
        "usable_metric_files": sum(1 for row in files if row["usable_for_metrics"]),
        "bid_windows": sum(int(row["bid_windows"]) for row in files),
        "ready_windows": sum(int(row["ready_windows"]) for row in files),
        "no_state_windows": sum(int(row["no_state_windows"]) for row in files),
        "constraint_conflict_windows": sum(
            int(row["constraint_conflict_windows"]) for row in files
        ),
        "multi_session_files": sum(
            1 for row in files if int(row.get("session_count") or 0) > 1
        ),
        "recovered_first_prebid_windows": sum(
            int(row.get("recovered_first_prebid_windows") or 0) for row in files
        ),
        "recovered_first_prebid_sources": _counter_dict(
            recovered_first_prebid_sources
        ),
        "sample_class_counts": _counter_dict(sample_classes),
        "file_status_counts": _counter_dict(file_statuses),
        "window_status_counts": _counter_dict(window_statuses),
        "public_info_counts": _counter_dict(public_info_counts),
        "exact_public_info_counts": _counter_dict(exact_public_info_counts),
        "parse_error_files": [
            row["file"] for row in files if row["status"] == "parse_error"
        ],
        "capture_gap_files": [
            row["file"] for row in files if int(row["no_state_windows"]) > 0
        ],
        "multi_session_capture_files": [
            row["file"] for row in files if int(row.get("session_count") or 0) > 1
        ],
    }
    summary.update(summary_cohort_metadata)
    return {"summary": summary, "files": files}


def _print_summary(manifest: dict[str, Any]) -> None:
    summary = manifest["summary"]
    print(
        " ".join(
            (
                f"files={summary['files']}",
                f"parsed_files={summary['parsed_files']}",
                f"parse_errors={summary['parse_errors']}",
                f"valid_files={summary['valid_files']}",
                f"mixed_files={summary['mixed_files']}",
                f"invalid_files={summary['invalid_files']}",
                f"usable_metric_files={summary['usable_metric_files']}",
                f"bid_windows={summary['bid_windows']}",
                f"ready_windows={summary['ready_windows']}",
                f"no_state_windows={summary['no_state_windows']}",
                f"constraint_conflict_windows={summary['constraint_conflict_windows']}",
            )
        )
    )
    if summary["parse_error_files"]:
        print("parse_error_files=" + ";".join(summary["parse_error_files"][:10]))
    if summary["capture_gap_files"]:
        print("capture_gap_files=" + ";".join(summary["capture_gap_files"][:10]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a file-level manifest for Fatbeans archive samples.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument("--json", action="store_true", help="Emit manifest JSON.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write manifest JSON.",
    )
    parser.add_argument(
        "--cohort-role",
        help="Optional cohort role metadata, for example activity_tuning_reference.",
    )
    parser.add_argument(
        "--metric-scope",
        help="Optional metric scope metadata for the whole manifest.",
    )
    parser.add_argument(
        "--cohort-note",
        help="Optional note explaining cohort boundaries.",
    )
    parser.add_argument(
        "--fail-on-invalid",
        action="store_true",
        help="Exit non-zero if any file is invalid. Mixed files do not fail.",
    )
    args = parser.parse_args(argv)

    manifest = build_manifest(
        args.paths or _default_paths(),
        cohort_role=args.cohort_role,
        metric_scope=args.metric_scope,
        cohort_note=args.cohort_note,
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(manifest)
        if args.output is not None:
            print(f"manifest_output={_relative_path(args.output)}")

    if args.fail_on_invalid and manifest["summary"]["invalid_files"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
