"""Build one capability-aware index over local raw captures and cloud settlement exports.

Cloud records remain cloud truth/replay records; they are never renamed or represented as Fatbeans
raw captures.  Multiple cumulative cloud snapshots are unioned by ``session_hash``.  Local raw
captures reconstruct the uploader's exact ``map_id:numeric_session`` wire token before applying
SHA1(token)[:16], so overlap is deduplicated across sources.

The output records source, time/version era and explicit capabilities:

* ``truth_eligible``: settlement truth is present, capture-complete, and not
  explicitly marked as a partial catalog-value lookup;
* ``local_raw_replay``: a strict-valid original Fatbeans capture exists;
* ``local_settlement_truth``: current-gate-valid local settlement exists, including truth-only raw;
* ``cloud_replay_grade``: none / approximate / red-directional only;
* ``tuning_candidate``: strict-valid local raw capture only.  Cloud rows are never tuning-grade.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, TextIO
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
for _sub in ("scripts", "src", "hero_ref/src", "hero_ref/tools"):
    _path = str(ROOT / _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from build_clean_eval_set import (  # noqa: E402
    TRUTH_REGISTRY,
    _load_truth_registry,
    _sha256_file,
    _truth_gate_context_sha256,
)
from census_cloud_sample_fields import (  # noqa: E402
    REF_CORE_FIELDS,
    TRUTH_CORE_FIELDS,
    _normalise_hero,
    _open_text,
)
from cloud_ref_replay import replayable_reason  # noqa: E402


SHANGHAI = ZoneInfo("Asia/Shanghai")
DROP_RATE_CUTOFF = datetime(2026, 6, 25, 12, 0, 0, tzinfo=SHANGHAI)
TRUTH_FIELDS = (*TRUTH_CORE_FIELDS, "total_item_count", "catalog_item_count")


def _hash_session(session_id: Any, map_id: Any = None) -> str | None:
    if not session_id:
        return None
    token = str(session_id)
    # monitor uploads the raw state session id, whose wire form is ``map_id:numeric_session``.
    # Canonical Fatbeans filenames retain only the numeric part, so reconstruct the exact token.
    if ":" not in token and map_id not in (None, ""):
        token = f"{map_id}:{token}"
    return hashlib.sha1(token.encode("utf-8")).hexdigest()[:16]


def _is_present(value: Any) -> bool:
    return value is not None and value != "" and value != {} and value != []


def _truth_eligible(record: Mapping[str, Any]) -> bool:
    return (
        all(_is_present(record.get(field)) for field in TRUTH_CORE_FIELDS)
        and not bool(record.get("settlement_incomplete"))
        and record.get("catalog_value_complete") is not False
    )


def _cloud_replay_grade(record: Mapping[str, Any]) -> tuple[str, str | None]:
    ref_inputs = record.get("ref_inputs")
    if not isinstance(ref_inputs, Mapping) or not ref_inputs:
        return "none", "no_ref_inputs"
    if _is_present(record.get("snapshot")):
        # No current collector export reaches this branch, but preserve the distinction if schema grows.
        return "snapshot_present_unverified", None
    reason = replayable_reason(dict(record))
    if reason is None:
        return "red_directional_only", None
    return "approximate_ref_only", reason


def _version_generation(value: Any) -> str:
    version = str(value or "").strip().lower()
    if version.startswith(("0.2.6", "0.2.7")):
        return "0.2.6_plus_capture_stable_generation"
    if version.startswith("0.2.5"):
        return "0.2.5_partial_replay_generation"
    if version.startswith(("0.2.4-hotfix2", "0.2.4-hotfix3")):
        return "early_partial_ref_generation"
    if version:
        return "legacy_truth_generation"
    return "unknown_version"


def _epoch_or_iso(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone(SHANGHAI)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else parsed.astimezone(SHANGHAI)


def _drop_rate_era(*values: Any) -> str:
    for value in values:
        parsed = _epoch_or_iso(value)
        if parsed is not None:
            return "NEW_post_20260625_1200" if parsed >= DROP_RATE_CUTOFF else "OLD_pre_20260625_1200"
    return "UNKNOWN_time"


def _payload_signature(record: Mapping[str, Any]) -> str:
    selected = {
        key: record.get(key)
        for key in (
            "session_hash",
            "hero",
            "map_id",
            "app_version",
            *TRUTH_FIELDS,
            "settlement_incomplete",
            "ref_inputs",
            "snapshot",
        )
    }
    raw = json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cloud_row(record: Mapping[str, Any], *, source: Path, line: int) -> dict[str, Any]:
    grade, replay_reason = _cloud_replay_grade(record)
    ref_inputs = record.get("ref_inputs") if isinstance(record.get("ref_inputs"), Mapping) else {}
    raw_minimap = ref_inputs.get("minimap_items") if ref_inputs else None
    minimap_items = (
        [item for item in raw_minimap if isinstance(item, Mapping)]
        if isinstance(raw_minimap, list)
        else []
    )
    return {
        "session_hash": str(record.get("session_hash") or ""),
        "source": str(source.relative_to(ROOT)).replace("\\", "/"),
        "line": line,
        "hero": _normalise_hero(record.get("hero")),
        "map_id": record.get("map_id"),
        "app_version": record.get("app_version"),
        "client_version": record.get("client_version"),
        "client_ts": record.get("client_ts"),
        "received_ts": record.get("received_ts"),
        "truth_eligible": _truth_eligible(record),
        "settlement_incomplete": bool(record.get("settlement_incomplete")),
        "truth": {field: record.get(field) for field in TRUTH_FIELDS if field in record},
        "ref_inputs_present": bool(ref_inputs),
        "ref_core_complete": bool(ref_inputs)
        and all(field in ref_inputs for field in REF_CORE_FIELDS),
        "ref_minimap_nonempty": bool(minimap_items),
        "ref_minimap_quality_complete": bool(minimap_items)
        and len(minimap_items) == len(raw_minimap)
        and all(item.get("quality") is not None for item in minimap_items),
        "ref_minimap_all_positioned": bool(minimap_items)
        and len(minimap_items) == len(raw_minimap)
        and all(item.get("row") is not None and item.get("col") is not None for item in minimap_items),
        "snapshot_present": _is_present(record.get("snapshot")),
        "cloud_replay_grade": grade,
        "cloud_replay_reason": replay_reason,
        "uid_hash_present": _is_present(record.get("uid_hash")),
        "catalog_item_count_present": _is_present(record.get("catalog_item_count")),
        "payload_signature": _payload_signature(record),
    }


def _load_cloud(paths: list[Path]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    sessions: dict[str, dict[str, Any]] = {}
    source_summaries: list[dict[str, Any]] = []
    duplicate_snapshot_rows = 0
    payload_conflicts: list[dict[str, Any]] = []
    for path in paths:
        scanned = bad_json = missing_session = 0
        with _open_text(path) as handle:
            for line_no, line in enumerate(handle, 1):
                scanned += 1
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    bad_json += 1
                    continue
                if not isinstance(record, Mapping):
                    bad_json += 1
                    continue
                session_hash = str(record.get("session_hash") or "")
                if not session_hash:
                    missing_session += 1
                    continue
                row = _cloud_row(record, source=path, line=line_no)
                prior = sessions.get(session_hash)
                if prior is not None:
                    duplicate_snapshot_rows += 1
                    if (
                        prior.get("payload_signature") != row.get("payload_signature")
                        and len(payload_conflicts) < 20
                    ):
                        payload_conflicts.append(
                            {
                                "session_hash": session_hash,
                                "prior_source": prior["source"],
                                "latest_source": row["source"],
                            }
                        )
                sessions[session_hash] = row
        source_summaries.append(
            {
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
                "scanned_rows": scanned,
                "bad_json": bad_json,
                "missing_session_hash": missing_session,
            }
        )
        if bad_json or missing_session:
            raise RuntimeError(
                f"cloud source failed strict structure gate: {path} "
                f"bad_json={bad_json} missing_session_hash={missing_session}"
            )
    return sessions, {
        "sources": source_summaries,
        "unique_sessions": len(sessions),
        "duplicate_snapshot_rows": duplicate_snapshot_rows,
        "payload_conflict_examples": payload_conflicts,
    }


def _load_local() -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    by_name = _load_truth_registry()
    payload = json.loads(TRUTH_REGISTRY.read_text(encoding="utf-8"))
    local: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    skipped_mixed = 0
    for row in payload["samples"]:
        if row.get("verdict") != "valid":
            continue
        replay_eligible = bool(row.get("replay_eligible", row.get("active")))
        truth_eligible = bool(row.get("truth_eligible", row.get("active")))
        if row.get("catalog_value_complete") is False:
            truth_eligible = False
        if not truth_eligible:
            continue
        name = str(row.get("file") or "")
        if replay_eligible and not name.startswith("fatbeans_valid_"):
            skipped_mixed += 1
            continue
        if not replay_eligible and not name.startswith("fatbeans_truth_"):
            continue
        if by_name.get(str(row["file"])) is None:
            raise RuntimeError(f"registry lookup lost local row: {row['file']}")
        session_hash = _hash_session(row.get("session_id"), row.get("map_id"))
        if session_hash is None:
            raise RuntimeError(f"truth-valid local row has no session id: {row['file']}")
        local[session_hash].append(row)
    replay_rows = [
        row
        for rows in local.values()
        for row in rows
        if bool(row.get("replay_eligible", row.get("active")))
    ]
    truth_only_rows = [
        row
        for rows in local.values()
        for row in rows
        if not bool(row.get("replay_eligible", row.get("active")))
    ]
    replay_sessions = {
        session_hash
        for session_hash, rows in local.items()
        if any(bool(row.get("replay_eligible", row.get("active"))) for row in rows)
    }
    truth_only_sessions = {
        session_hash
        for session_hash, rows in local.items()
        if any(not bool(row.get("replay_eligible", row.get("active"))) for row in rows)
    }
    return dict(local), {
        "registry": str(TRUTH_REGISTRY.relative_to(ROOT)).replace("\\", "/"),
        "registry_sha256": _sha256_file(TRUTH_REGISTRY),
        "gate_context_sha256": _truth_gate_context_sha256(),
        "strict_valid_files": len(replay_rows),
        "unique_sessions": len(replay_sessions),
        "extra_duplicate_files": len(replay_rows) - len(replay_sessions),
        "truth_only_files": len(truth_only_rows),
        "truth_only_unique_sessions": len(truth_only_sessions),
        "truth_unique_sessions": len(local),
        "skipped_active_mixed_files": skipped_mixed,
    }


def build_catalog(cloud_paths: list[Path]) -> dict[str, Any]:
    cloud, cloud_summary = _load_cloud(cloud_paths)
    local, local_summary = _load_local()
    replay_sessions = {
        session_hash
        for session_hash, rows in local.items()
        if any(bool(row.get("replay_eligible", row.get("active"))) for row in rows)
    }
    truth_only_sessions = set(local) - replay_sessions
    session_hashes = sorted(set(cloud) | set(local))
    records: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    hero_summary: defaultdict[str, Counter[str]] = defaultdict(Counter)
    version_summary: defaultdict[str, Counter[str]] = defaultdict(Counter)
    era_summary: Counter[str] = Counter()

    for session_hash in session_hashes:
        cloud_row = cloud.get(session_hash)
        local_rows = local.get(session_hash, [])
        replay_rows = [
            row
            for row in local_rows
            if bool(row.get("replay_eligible", row.get("active")))
        ]
        truth_only_rows = [
            row
            for row in local_rows
            if not bool(row.get("replay_eligible", row.get("active")))
        ]
        # Prefer the richer replay row for shared metadata when both representations exist, while
        # retaining every truth-only path for provenance.
        local_row = (replay_rows or truth_only_rows)[0] if local_rows else None
        hero = str((cloud_row or {}).get("hero") or (local_row or {}).get("hero") or "unknown")
        map_id = (cloud_row or {}).get("map_id") or (local_row or {}).get("map_id")
        if cloud_row and local_row:
            if str(cloud_row.get("hero") or "") != str(local_row.get("hero") or "") or int(
                cloud_row.get("map_id") or 0
            ) != int(local_row.get("map_id") or 0):
                if len(conflicts) < 20:
                    conflicts.append(
                        {
                            "session_hash": session_hash,
                            "cloud": {"hero": cloud_row.get("hero"), "map_id": cloud_row.get("map_id")},
                            "local": {"hero": local_row.get("hero"), "map_id": local_row.get("map_id")},
                        }
                    )
        local_settlement_truth = bool(local_rows)
        truth_eligible = bool(
            (cloud_row or {}).get("truth_eligible") or local_settlement_truth
        )
        local_raw_replay = bool(replay_rows)
        app_version = (cloud_row or {}).get("app_version")
        drop_rate_era = _drop_rate_era(
            (cloud_row or {}).get("client_ts"),
            (local_row or {}).get("capture_date"),
            (cloud_row or {}).get("received_ts"),
        )
        record = {
            "session_hash": session_hash,
            "hero": hero,
            "map_id": map_id,
            "source_kinds": [
                kind
                for kind, present in (
                    ("cloud_settlement", bool(cloud_row)),
                    ("local_fatbeans_raw", local_raw_replay),
                    ("local_settlement_truth", bool(truth_only_rows)),
                )
                if present
            ],
            "truth_eligible": truth_eligible,
            "local_raw_replay": local_raw_replay,
            "local_settlement_truth": local_settlement_truth,
            "cloud_replay_grade": (cloud_row or {}).get("cloud_replay_grade", "not_cloud"),
            "cloud_replay_reason": (cloud_row or {}).get("cloud_replay_reason"),
            "tuning_candidate": local_raw_replay,
            "app_version": app_version,
            "version_generation": _version_generation(app_version),
            "drop_rate_era": drop_rate_era,
            "cloud": (
                {
                    key: value
                    for key, value in cloud_row.items()
                    if key != "payload_signature"
                }
                if cloud_row
                else None
            ),
            "local": (
                {
                    "paths": [row["path"] for row in local_rows],
                    "replay_paths": [row["path"] for row in replay_rows],
                    "truth_only_paths": [row["path"] for row in truth_only_rows],
                    "pools": sorted({str(row.get("pool") or "") for row in local_rows}),
                    "capture_date": local_row.get("capture_date"),
                    "truth": local_row.get("truth"),
                    "sha256": [row["sha256"] for row in local_rows],
                }
                if local_row
                else None
            ),
        }
        records.append(record)

        hs = hero_summary[hero]
        hs["union_sessions"] += 1
        hs["truth_eligible"] += truth_eligible
        hs["cloud_truth"] += bool(cloud_row and cloud_row.get("truth_eligible"))
        hs["local_truth"] += local_settlement_truth
        hs["local_raw"] += local_raw_replay
        hs["local_truth_only"] += bool(truth_only_rows and not replay_rows)
        hs["overlap"] += bool(cloud_row and replay_rows)
        hs["truth_overlap"] += bool(cloud_row and local_rows)
        hs["cloud_ref"] += bool(cloud_row and cloud_row.get("ref_inputs_present"))
        hs["cloud_red_directional"] += bool(
            cloud_row and cloud_row.get("cloud_replay_grade") == "red_directional_only"
        )
        era_summary[drop_rate_era] += 1
        if cloud_row:
            version = str(app_version or "<missing>")
            vs = version_summary[version]
            vs["rows"] += 1
            vs["truth_eligible"] += bool(cloud_row.get("truth_eligible"))
            vs["ref_inputs"] += bool(cloud_row.get("ref_inputs_present"))
            vs["ref_core_complete"] += bool(cloud_row.get("ref_core_complete"))
            vs["snapshot"] += bool(cloud_row.get("snapshot_present"))
            vs["red_directional"] += cloud_row.get("cloud_replay_grade") == "red_directional_only"
            vs["uid_hash"] += bool(cloud_row.get("uid_hash_present"))
            vs["catalog_item_count"] += bool(cloud_row.get("catalog_item_count_present"))

    cloud_truth = sum(bool(row.get("truth_eligible")) for row in cloud.values())
    cloud_ref = sum(bool(row.get("ref_inputs_present")) for row in cloud.values())
    cloud_directional = sum(row.get("cloud_replay_grade") == "red_directional_only" for row in cloud.values())
    cloud_approximate = sum(row.get("cloud_replay_grade") == "approximate_ref_only" for row in cloud.values())
    replay_overlap = len(set(cloud) & replay_sessions)
    truth_overlap = len(set(cloud) & set(local))
    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": "scripts/build_unified_sample_catalog.py",
        "rules": {
            "dedupe": (
                "cloud session_hash; local SHA1(map_id:numeric_session)[:16] "
                "reconstructs the uploader wire token"
            ),
            "cloud_truth": (
                "all truth core fields present, settlement_incomplete is false, and "
                "catalog_value_complete is not explicitly false"
            ),
            "local_truth": (
                "current sample truth registry verdict valid and truth_eligible true; "
                "replay eligibility is tracked independently"
            ),
            "source_separation": "cloud records are never represented as Fatbeans raw captures",
            "truth_only_separation": (
                "workflow-incomplete local settlement truth may feed priors/statistics but never "
                "engine replay, clean-eval or tuning_candidate"
            ),
            "cloud_tuning": "always false; ref_inputs without full snapshot is not tuning-grade",
            "drop_rate_cutoff": "2026-06-25 12:00:00 Asia/Shanghai",
            "version_generation_note": (
                "0.2.4-hotfix2/3 is early partial ref; 0.2.5 is broad partial replay; "
                "0.2.6+ is the capture-stable generation. Eligibility still follows actual fields."
            ),
        },
        "summary": {
            "union_sessions": len(records),
            "truth_eligible_union": sum(row["truth_eligible"] for row in records),
            "cloud_unique_sessions": len(cloud),
            "cloud_truth_eligible": cloud_truth,
            "local_raw_unique_sessions": len(replay_sessions),
            "local_truth_unique_sessions": len(local),
            "local_truth_only_unique_sessions": len(truth_only_sessions),
            "local_cloud_overlap": replay_overlap,
            "local_truth_cloud_overlap": truth_overlap,
            "local_only_sessions": len(replay_sessions - set(cloud)),
            "local_truth_without_cloud_sessions": len(set(local) - set(cloud)),
            "cloud_only_sessions": len(set(cloud) - set(local)),
            "cloud_ref_inputs": cloud_ref,
            "cloud_red_directional_only": cloud_directional,
            "cloud_approximate_ref_only": cloud_approximate,
            "cloud_minimap_quality_complete": sum(
                bool(row.get("ref_minimap_quality_complete")) for row in cloud.values()
            ),
            "cloud_minimap_all_positioned": sum(
                bool(row.get("ref_minimap_all_positioned")) for row in cloud.values()
            ),
            "cloud_snapshot_present": sum(bool(row.get("snapshot_present")) for row in cloud.values()),
            "cloud_tuning_grade": 0,
            "hero_map_conflict_examples": conflicts,
        },
        "cloud_sources": cloud_summary,
        "local_source": local_summary,
        "by_hero": {hero: dict(sorted(counts.items())) for hero, counts in sorted(hero_summary.items())},
        "by_app_version": {
            version: {
                **dict(sorted(counts.items())),
                "generation": _version_generation(version if version != "<missing>" else None),
            }
            for version, counts in sorted(version_summary.items())
        },
        "by_drop_rate_era": dict(sorted(era_summary.items())),
        "records": records,
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _summary_payload(payload: Mapping[str, Any], full_catalog: Path) -> dict[str, Any]:
    """Return a tracked, reproducible summary without the per-session records."""
    try:
        catalog_path = str(full_catalog.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        catalog_path = str(full_catalog.resolve())
    return {
        "artifact_kind": "unified_sample_capability_summary",
        **{key: value for key, value in payload.items() if key != "records"},
        "full_catalog": {
            "path": catalog_path,
            "size_bytes": full_catalog.stat().st_size,
            "sha256": _sha256_file(full_catalog),
            "tracked": False,
            "rebuild_note": (
                "The per-session catalog is local-only and regenerable from the listed cloud "
                "snapshots plus the content-hashed local truth registry."
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cloud", nargs="+", type=Path, required=True, help="oldest to newest snapshots")
    parser.add_argument("--write", type=Path, help="atomically write the unified catalog")
    parser.add_argument(
        "--summary-write",
        type=Path,
        help="write a tracked capability summary bound to the full catalog hash",
    )
    parser.add_argument("--replace", action="store_true", help="replace only the requested output path")
    args = parser.parse_args(argv)
    if args.summary_write and not args.write:
        parser.error("--summary-write requires --write so the summary can bind the full catalog hash")
    for path in args.cloud:
        if not path.is_file():
            parser.error(f"cloud snapshot not found: {path}")
    for output in (args.write, args.summary_write):
        if output and output.exists() and not args.replace:
            raise FileExistsError(f"refusing to overwrite generated artifact: {output}")
    payload = build_catalog([path.resolve() for path in args.cloud])
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    if args.write:
        _atomic_write(args.write, payload)
        print(f"wrote {args.write}")
        if args.summary_write:
            _atomic_write(args.summary_write, _summary_payload(payload, args.write))
            print(f"wrote {args.summary_write}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
