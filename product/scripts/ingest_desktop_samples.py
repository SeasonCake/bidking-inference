"""Copy validated, unique Desktop captures into the canonical sample pools.

Default mode is a read-only census.  ``--apply`` performs copy-only archival: source folders are
never moved or deleted.  Every candidate is grouped by session, checked by ``sample_truth_gate``,
and deduplicated against the main and activity pools before one canonical representative is chosen.
Npcap, WinDivert, and WSS reset captures share the same qualification path.

The script also:

* keeps activity-exclusive X511-X520 shipwreck maps in the 2026-07-02 activity pool;
* keeps 2541-2550 / 4541-4550 red-crate shipwreck maps in a distinct 2026-07-16 pool;
* keeps settlement-valid captures with incomplete bidding windows in non-replay truth-only pools;
* copies truth-mismatch controls into a non-globbed INVALID quarantine;
* writes a full decision manifest and a merged session-date index;
* archives a matching HeroRef diagnostic package when one exists;
* writes a focused q5 value/avg-cells manifest for gold calibration scripts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
for _sub in ("src", "scripts", "hero_ref/src"):
    _path = str(ROOT / _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from bidking_lab.live.fatbeans import (  # noqa: E402
    load_fatbeans_packets_from_rows,
    parse_fatbeans_packets,
)
from bidking_lab.live.monitor import _is_activity_shipwreck_map, load_monitor_tables  # noqa: E402
from bidking_lab.live.warehouse_source import (  # noqa: E402
    OFFICIAL_RANDOM_WAREHOUSE,
    PLAYER_CUSTOM_WAREHOUSE,
    POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
    classify_from_map_table,
    warehouse_generation_from_capture_time,
)
from ahmad_ref_engine import _is_red_crate_activity_shipwreck_map  # noqa: E402
from organize_fatbeans_real_samples import (  # noqa: E402
    _classify_rows,
    _hero_from_events,
    _latest_map_id,
    _rounds,
    _session_count,
    _session_token,
)
from scripts.evaluate_fatbeans_v3_samples import _round_rows_for_events  # noqa: E402
from sample_truth_gate import validate_events  # noqa: E402


DEFAULT_ROOTS = (
    Path.home() / "Desktop" / "new_recordings",
    Path.home() / "Desktop" / "bidking-live-v0.2.7-encrypted" / "data",
)
MAIN_POOL = ROOT / "data/samples/fatbeans"
ACTIVITY_POOL = ROOT / "data/samples/fatbeans_activity_20260702_shipwreck"
RED_CRATE_ACTIVITY_POOL = ROOT / "data/samples/fatbeans_activity_20260716_red_crate_shipwreck"
TRUTH_ONLY_ROOT = ROOT / "data/samples/fatbeans_truth_only_settlement"
TRUTH_ONLY_MAIN_POOL = TRUTH_ONLY_ROOT / "normal"
TRUTH_ONLY_ACTIVITY_POOL = TRUTH_ONLY_ROOT / "activity_20260702_shipwreck"
TRUTH_ONLY_RED_CRATE_ACTIVITY_POOL = TRUTH_ONLY_ROOT / "activity_20260716_red_crate_shipwreck"
PLAYER_CUSTOM_WAREHOUSE_POOL = ROOT / "data/samples/player_custom_warehouse"
POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE_POOL = (
    ROOT / "data/samples/postupdate_unknown_warehouse_source"
)
QUARANTINE = MAIN_POOL / "ZZZ_INVALID_DO_NOT_USE_ingest_20260714"
DIAG_ARCHIVE = ROOT / "data/samples/hero_ref/archive/exports"
MANIFEST = ROOT / "data/sample_manifests/desktop_ingest_20260714.json"
SESSION_DATES = ROOT / "data/sample_manifests/session_dates_20260714.json"
GOLD_MANIFEST = ROOT / "data/sample_manifests/gold_value_avg_cells_20260714.json"
EXCLUDE = ROOT / "data/sample_manifests/declared_mismatch_exclude.json"

SESSION_JSON_RE = re.compile(r"_(?P<sid>\d{16})(?:_reset)?\.json$", re.IGNORECASE)
SESSION_ANY_RE = re.compile(r"_(?P<sid>\d{16})(?:_|\.)")
DIAG_RE = re.compile(
    r"^HeroRefDiag-(?P<date>\d{8})-(?P<time>\d{6})-(?P<map>\d{3,4})_(?P<sid>\d{16})"
)
CAPTURE_DATE_RE = re.compile(
    r"(?:npcap|windivert|wss)_live_"
    r"(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{6})_",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Candidate:
    root_label: str
    path: Path
    session_hint: str
    member: str | None = None
    diag_container: Path | None = None

    @property
    def display(self) -> str:
        suffix = f"!{self.member}" if self.member else ""
        return f"{_manifest_source_path(self.path)}{suffix}"

    def raw_bytes(self) -> bytes:
        if self.member is None:
            return self.path.read_bytes()
        with zipfile.ZipFile(self.path) as zf:
            return zf.read(self.member)

    def rows(self) -> list[dict[str, Any]]:
        raw = self.raw_bytes()
        if (self.member or self.path.name).lower().endswith(".jsonl"):
            rows = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
        else:
            rows = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("capture_rows_not_list_of_objects")
        return rows


def _root_label(path: Path) -> str:
    if path.name.lower() == "new_recordings":
        return "new_recordings"
    parent_name = path.parent.name
    parent_lower = parent_name.lower()
    if (
        path.name.lower() == "data"
        and parent_lower.startswith("bidking-live-v")
        and parent_lower.endswith("-encrypted")
    ):
        return f"{parent_name}/data"
    return path.name


def _repo_output_path(path: Path) -> Path:
    """Resolve CLI output paths consistently before copy/apply bookkeeping."""
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def _manifest_source_path(path: Path) -> str:
    """Return a stable source locator without publishing the workstation's absolute path."""
    parts = path.parts
    lowered = [part.lower() for part in parts]
    for index, part in enumerate(lowered):
        if part in {"new_recordings", "archive"} or (
            part.startswith("bidking-live-v") and part.endswith("-encrypted")
        ):
            return Path(*parts[index:]).as_posix()
    return path.name


def _diag_parts(path: Path) -> tuple[str, str, str] | None:
    match = DIAG_RE.match(path.name)
    if match is None:
        return None
    raw_date = match.group("date")
    return (
        match.group("sid"),
        f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}",
        match.group("time"),
    )


def _diag_current_candidate(root_label: str, container: Path, sid: str) -> Candidate | None:
    members = (
        "raw/wss_live.json",
        "raw/wss_live.jsonl",
        "raw/npcap_live.json",
        "raw/npcap_live.jsonl",
    )
    if container.is_dir():
        for member in members:
            path = container / Path(member)
            if path.is_file():
                return Candidate(root_label, path, sid, diag_container=container)
        return None
    if container.suffix.lower() != ".zip":
        return None
    try:
        with zipfile.ZipFile(container) as zf:
            names = set(zf.namelist())
    except (OSError, zipfile.BadZipFile):
        return None
    for member in members:
        if member in names:
            return Candidate(root_label, container, sid, member=member, diag_container=container)
    return None


def collect_candidates(roots: Iterable[Path]) -> tuple[dict[str, list[Candidate]], dict[str, list[Path]]]:
    by_session: dict[str, list[Candidate]] = defaultdict(list)
    diagnostics: dict[str, list[Path]] = defaultdict(list)
    for root in roots:
        if not root.is_dir():
            continue
        label = _root_label(root)
        for path in root.rglob("*.json"):
            capture_name = path.name.lower()
            if not any(
                marker in capture_name
                for marker in ("npcap_live", "windivert_live", "wss_live")
            ):
                continue
            match = SESSION_JSON_RE.search(path.name)
            if match is None:
                continue
            sid = match.group("sid")
            by_session[sid].append(Candidate(label, path, sid))

        diagnostic_containers = list(root.rglob("HeroRefDiag-*"))
        if root.name.startswith("HeroRefDiag-"):
            diagnostic_containers.insert(0, root)
        for container in diagnostic_containers:
            if not (container.is_dir() or container.suffix.lower() == ".zip"):
                continue
            parts = _diag_parts(container)
            if parts is None:
                continue
            sid, _date, _clock = parts
            diagnostics[sid].append(container)

    # Reset JSON is the canonical source. A diagnostic's current JSON/JSONL only fills sessions
    # that do not have a named reset anywhere in the supplied roots.
    for sid, containers in diagnostics.items():
        if sid in by_session:
            continue
        for container in sorted(containers, key=lambda value: (value.suffix.lower() != ".zip", str(value))):
            candidate = _diag_current_candidate(_root_label(next(root for root in roots if container.is_relative_to(root))), container, sid)
            if candidate is not None:
                by_session[sid].append(candidate)
    return by_session, diagnostics


def _existing_sessions() -> dict[str, Path]:
    out: dict[str, Path] = {}
    standard_directories = (
        MAIN_POOL,
        ACTIVITY_POOL,
        RED_CRATE_ACTIVITY_POOL,
        TRUTH_ONLY_MAIN_POOL,
        TRUTH_ONLY_ACTIVITY_POOL,
        TRUTH_ONLY_RED_CRATE_ACTIVITY_POOL,
        PLAYER_CUSTOM_WAREHOUSE_POOL,
        POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE_POOL,
    )
    inbox_directories = sorted(
        ROOT.glob("data/samples/recordings_inbox_*/captures")
    )
    for directory in (*standard_directories, *inbox_directories):
        for path in directory.glob("*.json"):
            match = SESSION_ANY_RE.search(path.name)
            if match is not None:
                out.setdefault(match.group("sid"), path)
    return out


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _rows_semantic_sha256(rows: list[dict[str, Any]]) -> str:
    """Hash parsed capture content, independent of JSON formatting and line endings."""
    payload = json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(payload)


def _existing_quarantine_controls(
    target_dir: Path,
) -> tuple[dict[tuple[str, str], Path], set[str]]:
    """Index semantic controls so repeated ingests reuse an existing quarantine row."""
    directories = [
        path
        for path in MAIN_POOL.iterdir()
        if path.is_dir()
        and (
            path.name.startswith("ZZZ_INVALID_DO_NOT_USE")
            or path.name.endswith("_quarantine")
        )
    ]
    directories.extend(
        sorted(
            ROOT.glob(
                "data/samples/recordings_inbox_*/quarantine_positive_controls"
            )
        )
    )
    if target_dir.is_dir() and target_dir not in directories:
        directories.append(target_dir)
    # Prefer a prior quarantine over the current ingest target when both already exist.
    directories.sort(key=lambda path: (path.resolve() == target_dir.resolve(), path.name.lower()))

    by_semantics: dict[tuple[str, str], Path] = {}
    used_names: set[str] = set()
    for directory in directories:
        for path in sorted(directory.glob("fatbeans_*.json")):
            used_names.add(path.name)
            match = SESSION_ANY_RE.search(path.name)
            if match is None:
                continue
            try:
                rows = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                continue
            by_semantics.setdefault(
                (match.group("sid"), _rows_semantic_sha256(rows)),
                path,
            )
    return by_semantics, used_names


def _unique_quarantine_name(name: str, semantic_sha256: str, used_names: set[str]) -> str:
    if name not in used_names:
        return name
    path = Path(name)
    candidate = f"{path.stem}_{semantic_sha256[:8]}{path.suffix}"
    suffix = 2
    while candidate in used_names:
        candidate = f"{path.stem}_{semantic_sha256[:8]}_{suffix}{path.suffix}"
        suffix += 1
    return candidate


def _source_date(candidate: Candidate) -> str | None:
    match = CAPTURE_DATE_RE.search(candidate.path.name)
    if match is not None:
        clock = match.group("time")
        return f"{match.group('date')} {clock[:2]}:{clock[2:4]}:{clock[4:]}"
    container = candidate.diag_container
    parts = _diag_parts(container) if container is not None else None
    if parts is None:
        return None
    _sid, date, clock = parts
    return f"{date} {clock[:2]}:{clock[2:4]}:{clock[4:]}"


def _packet_capture_date(rows: Iterable[dict[str, Any]]) -> str | None:
    stamps = [
        str(row["CaptureTime"])
        for row in rows
        if isinstance(row, dict) and row.get("CaptureTime")
    ]
    if not stamps:
        return None
    return min(stamps)[:19].replace("/", "-").replace("T", " ")


def _truth_key(record: dict[str, Any]) -> str:
    return json.dumps(record.get("truth_signature") or {}, ensure_ascii=False, sort_keys=True)


def _canonical_name(
    record: dict[str, Any], *, invalid: bool = False, truth_only: bool = False
) -> str:
    hero = re.sub(r"[^a-z0-9]+", "-", str(record.get("hero") or "unknown").lower()).strip("-")
    map_id = int(record.get("map_id") or 0)
    rounds = max(1, int(record.get("rounds") or 0))
    token = str(record.get("session_token") or f"{map_id}_{record['session_id']}")
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", token).strip("_")
    prefix = "fatbeans_INVALID" if invalid else "fatbeans_truth" if truth_only else "fatbeans_valid"
    return f"{prefix}_{hero}_{map_id}_{rounds}rounds_{token}_{record['session_id'][-4:]}.json"


def _candidate_record(candidate: Candidate, *, tables: Any) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    started = time.perf_counter()
    raw = candidate.raw_bytes()
    base: dict[str, Any] = {
        "source": candidate.display,
        "source_root": candidate.root_label,
        "source_path": _manifest_source_path(candidate.path),
        "source_member": candidate.member,
        "session_id": candidate.session_hint,
        "sha256": _sha256(raw),
        "bytes": len(raw),
        "capture_date": _source_date(candidate),
    }
    try:
        rows = candidate.rows()
        events = parse_fatbeans_packets(load_fatbeans_packets_from_rows(rows))
    except Exception as exc:  # noqa: BLE001 - manifest must retain operational parse failures
        return (
            {
                **base,
                "verdict": "INVALID",
                "reason": f"parse_failed:{type(exc).__name__}",
                "reasons": [f"parse_failed:{type(exc).__name__}"],
                "replay_verdict": "INVALID",
                "replay_reasons": [f"parse_failed:{type(exc).__name__}"],
                "truth_verdict": "INVALID",
                "truth_reason": f"parse_failed:{type(exc).__name__}",
                "truth_reasons": [f"parse_failed:{type(exc).__name__}"],
                "elapsed_seconds": round(time.perf_counter() - started, 4),
            },
            None,
        )

    packet_capture_date = _packet_capture_date(rows)
    if packet_capture_date:
        base["capture_date"] = packet_capture_date
        base["capture_date_source"] = "packet_capture_time"
    elif base["capture_date"]:
        base["capture_date_source"] = "source_filename_or_diagnostic"

    hero = _hero_from_events(events)
    map_id = _latest_map_id(events)
    rounds = _rounds(events)
    session_token = _session_token(events)
    base.update(
        hero=str(hero).lower() if hero else None,
        map_id=map_id,
        rounds=rounds,
        session_token=session_token,
        state_count=len(events.states),
        send_count=len(events.sends),
        packet_count=len(events.packets),
    )
    warehouse_source = classify_from_map_table(
        map_id,
        getattr(tables, "maps", None),
        source_generation=warehouse_generation_from_capture_time(
            base.get("capture_date")
        ),
    )
    base.update(
        warehouse_source=warehouse_source.as_dict(),
        warehouse_source_qualification=warehouse_source.qualification,
        warehouse_source_generation=warehouse_source.source_generation,
        official_distribution_eligible=(
            warehouse_source.official_distribution_eligible
        ),
    )
    # Replay eligibility and settlement-truth eligibility are deliberately orthogonal. A capture
    # may miss one bidding window yet still contain a complete, internally consistent settlement;
    # such a file is useful for priors/rates but must never enter engine replay or clean-eval.
    truth_structural: list[str] = []
    if _session_count(events) > 1:
        truth_structural.append("multiple_sessions")
    if session_token and candidate.session_hint not in session_token:
        truth_structural.append("session_hint_mismatch")
    if not hero:
        truth_structural.append("no_hero")
    if not map_id:
        truth_structural.append("no_map_id")

    workflow: dict[str, Any] = {}
    workflow_reasons: list[str] = []
    try:
        round_rows = _round_rows_for_events(
            candidate.path,
            events,
            tables=None,
            posterior_trials=0,
        )
        sample_class, bid_windows, ready_windows, no_state_windows, conflict_windows = _classify_rows(round_rows)
        workflow = {
            "sample_class": sample_class,
            "bid_windows": bid_windows,
            "ready_windows": ready_windows,
            "no_state_windows": no_state_windows,
            "constraint_conflict_windows": conflict_windows,
        }
        if sample_class != "valid":
            workflow_reasons.append(f"workflow_{sample_class}")
    except Exception as exc:  # noqa: BLE001
        workflow = {"sample_class": "check_failed", "workflow_error": type(exc).__name__}
        workflow_reasons.append(f"workflow_check_failed:{type(exc).__name__}")

    gate = validate_events(events, tables=tables)
    gate_reasons = list(gate.get("reasons") or [])
    truth_reasons = list(dict.fromkeys(truth_structural + gate_reasons))
    replay_reasons = list(dict.fromkeys(truth_structural + workflow_reasons + gate_reasons))
    record = {
        **base,
        **workflow,
        # `verdict` remains the legacy replay verdict consumed by existing manifests/tools.
        "verdict": "valid" if not replay_reasons else "INVALID",
        "reason": replay_reasons[0] if replay_reasons else None,
        "reasons": replay_reasons,
        "replay_verdict": "valid" if not replay_reasons else "INVALID",
        "replay_reasons": replay_reasons,
        "truth_verdict": "valid" if not truth_reasons else "INVALID",
        "truth_reason": truth_reasons[0] if truth_reasons else None,
        "truth_reasons": truth_reasons,
        "truth": gate.get("truth") or {},
        "truth_signature": gate.get("truth_signature") or {},
        "claims_present": int(gate.get("claims_present") or 0),
        "claims_matched": int(gate.get("claims_matched") or 0),
        "claim_sources": sorted(
            {
                f"{row.get('source')}:{row.get('metric')}"
                for row in gate.get("claims") or []
                if row.get("matched")
            }
        ),
        "mismatches": gate.get("mismatches") or [],
        "observed_consistency": gate.get("observed_consistency") or {},
        "observation_bounds": gate.get("observation_bounds") or {},
        "revealed_lower_bounds": gate.get("revealed_lower_bounds") or {},
        "revealed_lower_bound_mismatches": gate.get("revealed_lower_bound_mismatches") or [],
        "auxiliary_public_facts": gate.get("auxiliary_public_facts") or [],
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }
    return record, rows


def _choose_session(
    sid: str,
    candidates: list[Candidate],
    *,
    tables: Any,
) -> tuple[dict[str, Any], Candidate | None, list[dict[str, Any]] | None]:
    variants: list[tuple[dict[str, Any], Candidate, list[dict[str, Any]] | None]] = []
    for candidate in sorted(candidates, key=lambda value: (-len(value.raw_bytes()), value.display)):
        record, rows = _candidate_record(candidate, tables=tables)
        variants.append((record, candidate, rows))

    truth_valid = [entry for entry in variants if entry[0].get("truth_verdict") == "valid"]
    truth_keys = {_truth_key(entry[0]) for entry in truth_valid}
    if len(truth_keys) > 1:
        return (
            {
                "session_id": sid,
                "decision": "quarantine_session_truth_conflict",
                "reason": "same_session_multiple_valid_truth_signatures",
                "variants": [entry[0] for entry in variants],
            },
            None,
            None,
        )
    replay_valid = [entry for entry in truth_valid if entry[0].get("verdict") == "valid"]
    if not truth_valid:
        richest = max(
            variants,
            key=lambda entry: (
                bool(entry[0].get("truth")),
                int(entry[0].get("claims_present") or 0),
                int(entry[0].get("state_count") or 0),
                int(entry[0].get("bytes") or 0),
            ),
        )
        return (
            {
                "session_id": sid,
                "decision": "reject",
                "reason": richest[0].get("reason"),
                "variants": [entry[0] for entry in variants],
            },
            richest[1],
            richest[2],
        )

    candidates_for_role = replay_valid or truth_valid
    chosen = max(
        candidates_for_role,
        key=lambda entry: (
            int(entry[0].get("claims_present") or 0),
            int(entry[0].get("ready_windows") or 0),
            int(entry[0].get("state_count") or 0),
            int(entry[0].get("bytes") or 0),
        ),
    )
    record = chosen[0]
    truth_only = not replay_valid
    warehouse_qualification = record.get("warehouse_source_qualification")
    if warehouse_qualification == PLAYER_CUSTOM_WAREHOUSE:
        destination_dir = PLAYER_CUSTOM_WAREHOUSE_POOL
        decision_name = "accept_player_custom_warehouse"
    elif warehouse_qualification == POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE:
        destination_dir = POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE_POOL
        decision_name = "accept_postupdate_unknown_warehouse_source"
    elif warehouse_qualification in (None, OFFICIAL_RANDOM_WAREHOUSE) and truth_only:
        destination_dir, decision_name = _truth_only_destination_for_map(int(record["map_id"]))
    else:
        destination_dir, decision_name = _destination_for_map(int(record["map_id"]))
    name = _canonical_name(record, truth_only=truth_only)
    return (
        {
            "session_id": sid,
            "decision": decision_name,
            "destination": str((destination_dir / name).relative_to(ROOT)).replace("\\", "/"),
            "selected": record,
            "variants": [entry[0] for entry in variants],
        },
        chosen[1],
        chosen[2],
    )


def _destination_for_map(map_id: int) -> tuple[Path, str]:
    """Route each activity generation to its own pool without changing engine map semantics."""
    if _is_red_crate_activity_shipwreck_map(map_id):
        return RED_CRATE_ACTIVITY_POOL, "accept_red_crate_activity"
    if _is_activity_shipwreck_map(map_id):
        return ACTIVITY_POOL, "accept_activity"
    return MAIN_POOL, "accept_main"


def _truth_only_destination_for_map(map_id: int) -> tuple[Path, str]:
    """Keep settlement-only truth separated by the same activity-generation boundaries."""
    if _is_red_crate_activity_shipwreck_map(map_id):
        return TRUTH_ONLY_RED_CRATE_ACTIVITY_POOL, "accept_red_crate_activity_truth_only"
    if _is_activity_shipwreck_map(map_id):
        return TRUTH_ONLY_ACTIVITY_POOL, "accept_activity_truth_only"
    return TRUTH_ONLY_MAIN_POOL, "accept_truth_only"


def _write_rows(candidate: Candidate, rows: list[dict[str, Any]], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if candidate.member is None and candidate.path.suffix.lower() == ".json":
        shutil.copy2(candidate.path, destination)
        return
    destination.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _read_diag_snapshot(container: Path) -> dict[str, Any] | None:
    try:
        if container.is_dir():
            path = container / "latest_snapshot.json"
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        with zipfile.ZipFile(container) as zf:
            return json.loads(zf.read("latest_snapshot.json"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None


def _diag_matches(container: Path, selected: dict[str, Any]) -> tuple[bool, str | None]:
    snapshot = _read_diag_snapshot(container)
    if snapshot is None:
        return False, "missing_or_invalid_latest_snapshot"
    contract = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {}
    context = contract.get("context") if isinstance(contract.get("context"), dict) else {}
    truth = contract.get("truth") if isinstance(contract.get("truth"), dict) else {}
    session = str(snapshot.get("session_id") or context.get("session_id") or "")
    if selected["session_id"] not in session:
        return False, "diagnostic_session_mismatch"
    if context.get("map_id") is not None and int(context["map_id"]) != int(selected["map_id"]):
        return False, "diagnostic_map_mismatch"
    if context.get("hero") and str(context["hero"]).lower() != str(selected["hero"]).lower():
        return False, "diagnostic_hero_mismatch"
    total_items = truth.get("total_items")
    if total_items is not None:
        allowed = {
            int(selected["truth"]["raw_item_count"]),
            int(selected["truth"]["collectible_count"]),
        }
        if int(total_items) not in allowed:
            return False, "diagnostic_truth_count_mismatch"
    return True, None


def _copy_diag(container: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if container.is_file():
        shutil.copy2(container, destination)
        return
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(container.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(container).as_posix())


def _archive_diagnostics(
    diagnostics: dict[str, list[Path]],
    accepted: dict[str, dict[str, Any]],
    *,
    apply: bool,
) -> list[dict[str, Any]]:
    existing = {
        match.group("sid")
        for path in DIAG_ARCHIVE.rglob("HeroRefDiag-*")
        if (match := DIAG_RE.match(path.name)) is not None
    }
    records: list[dict[str, Any]] = []
    for sid, selected in sorted(accepted.items()):
        containers = diagnostics.get(sid) or []
        if not containers:
            continue
        if sid in existing:
            records.append({"session_id": sid, "decision": "duplicate_existing_diagnostic"})
            continue
        candidates: list[tuple[Path, str]] = []
        for container in containers:
            matched, reason = _diag_matches(container, selected)
            if matched:
                candidates.append((container, ""))
            else:
                records.append(
                    {
                        "session_id": sid,
                        "source": _manifest_source_path(container),
                        "decision": "reject_diagnostic",
                        "reason": reason,
                    }
                )
        if not candidates:
            continue
        container = min(candidates, key=lambda entry: (entry[0].suffix.lower() != ".zip", len(str(entry[0]))))[0]
        parts = _diag_parts(container)
        if parts is None:
            continue
        _sid, date, _clock = parts
        name = container.name if container.suffix.lower() == ".zip" else container.name + ".zip"
        destination = DIAG_ARCHIVE / date / name
        record = {
            "session_id": sid,
            "source": _manifest_source_path(container),
            "decision": "archive_diagnostic",
            "destination": str(destination.relative_to(ROOT)).replace("\\", "/"),
        }
        if apply:
            _copy_diag(container, destination)
        records.append(record)
    return records


def _merged_session_dates(
    accepted: dict[str, dict[str, Any]], *, destination: Path = SESSION_DATES
) -> dict[str, str]:
    old_paths = sorted((ROOT / "data/sample_manifests").glob("session_dates_*.json"), reverse=True)
    merged: dict[str, str] = {}
    for path in old_paths:
        if path.resolve() == destination.resolve():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            merged.update({str(key): str(value) for key, value in payload.items()})
            break
    for sid, selected in accepted.items():
        if selected.get("capture_date"):
            merged[sid] = str(selected["capture_date"])
    return dict(sorted(merged.items()))


def _gold_records(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def qtruth(selected: dict[str, Any], field: str, quality: int) -> int:
        bucket = (selected.get("truth") or {}).get(field) or {}
        return int(bucket.get(quality, bucket.get(str(quality), 0)) or 0)

    records: list[dict[str, Any]] = []
    for decision in decisions:
        decision_name = str(decision.get("decision", ""))
        if not decision_name.startswith("accept_"):
            continue
        selected = decision["selected"]
        if decision_name in {
            "accept_player_custom_warehouse",
            "accept_postupdate_unknown_warehouse_source",
        } or selected.get("official_distribution_eligible") is False:
            continue
        sources = set(selected.get("claim_sources") or [])
        has_value = "action:100125:value_sum" in sources
        has_avg_cells = any(
            source.endswith(":avg_cells")
            and source.startswith(("action:100113", "skill:1002041", "public_info:200015"))
            for source in sources
        )
        if not (has_value or has_avg_cells):
            continue
        records.append(
            {
                "session_id": decision["session_id"],
                "path": decision["destination"],
                "hero": selected.get("hero"),
                "map_id": selected.get("map_id"),
                "capture_date": selected.get("capture_date"),
                "q5_value_sum": has_value,
                "q5_avg_cells": has_avg_cells,
                "paired": has_value and has_avg_cells,
                "truth_q5_count": qtruth(selected, "counts", 5),
                "truth_q5_cells": qtruth(selected, "cells", 5),
                "truth_q5_value": qtruth(selected, "values", 5),
            }
        )
    return records


def _write_gold_manifest(
    decisions: list[dict[str, Any]],
    *,
    source_manifest: Path,
    generated_at: str,
    output_path: Path = GOLD_MANIFEST,
) -> list[dict[str, Any]]:
    records = _gold_records(decisions)
    try:
        source_label = str(source_manifest.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        source_label = str(source_manifest.resolve())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": generated_at,
                "source_manifest": source_label,
                "description": (
                    "Settlement-truth-valid q5 value-sum and/or q5 avg-cells captures from the "
                    "referenced local ingest; replay validity is recorded separately."
                ),
                "samples": records,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="*", type=Path, default=list(DEFAULT_ROOTS))
    parser.add_argument("--apply", action="store_true", help="copy accepted/quarantine/diagnostic files and write manifests")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--session-dates", type=Path, default=SESSION_DATES)
    parser.add_argument("--gold-manifest", type=Path, default=GOLD_MANIFEST)
    parser.add_argument("--quarantine-dir", type=Path, default=QUARANTINE)
    parser.add_argument("--ingest-label", default="20260714")
    parser.add_argument(
        "--report",
        type=Path,
        help="write the full decision report in dry-run or apply mode without changing the source folders",
    )
    parser.add_argument(
        "--rebuild-gold-manifest",
        action="store_true",
        help="rebuild the q5 value/avg manifest from an existing ingest manifest and exit",
    )
    parser.add_argument("--progress-every", type=int, default=100)
    args = parser.parse_args(argv)
    for attribute in (
        "manifest",
        "session_dates",
        "gold_manifest",
        "quarantine_dir",
        "report",
    ):
        value = getattr(args, attribute, None)
        if value is not None:
            setattr(args, attribute, _repo_output_path(value))

    if args.rebuild_gold_manifest:
        payload = json.loads(args.manifest.read_text(encoding="utf-8"))
        records = _write_gold_manifest(
            list(payload.get("decisions") or []),
            source_manifest=args.manifest,
            generated_at=datetime.now().isoformat(timespec="seconds"),
            output_path=args.gold_manifest,
        )
        print(
            f"gold_manifest={args.gold_manifest.relative_to(ROOT)} samples={len(records)} "
            f"paired={sum(row['paired'] for row in records)}"
        )
        return 0
    if args.apply and args.manifest.exists():
        raise FileExistsError(
            f"refusing to overwrite completed ingest manifest: {args.manifest}; "
            "use --rebuild-gold-manifest for the derived q5 index"
        )

    roots = [path.resolve() for path in (args.roots or list(DEFAULT_ROOTS))]
    print("sources:")
    for path in roots:
        print(f"  {path} ({'present' if path.is_dir() else 'MISSING'})")
    by_session, diagnostics = collect_candidates(roots)
    existing = _existing_sessions()
    print(
        f"candidate sessions={len(by_session)} variants={sum(len(rows) for rows in by_session.values())} "
        f"diagnostic sessions={len(diagnostics)} existing-session duplicates={sum(sid in existing for sid in by_session)}"
    )

    tables = load_monitor_tables()
    decisions: list[dict[str, Any]] = []
    selected_sources: dict[str, tuple[Candidate, list[dict[str, Any]]]] = {}
    accepted: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()
    new_sessions = [(sid, rows) for sid, rows in sorted(by_session.items()) if sid not in existing]
    duplicate_sessions = [(sid, rows) for sid, rows in sorted(by_session.items()) if sid in existing]
    for sid, candidates in duplicate_sessions:
        exact = False
        existing_hash: str | None = None
        try:
            existing_hash = _sha256(existing[sid].read_bytes())
            exact = any(_sha256(candidate.raw_bytes()) == existing_hash for candidate in candidates)
        except OSError:
            pass
        decisions.append(
            {
                "session_id": sid,
                "decision": "duplicate_existing_content" if exact else "duplicate_existing_session",
                "existing": str(existing[sid].relative_to(ROOT)).replace("\\", "/"),
                "existing_sha256": existing_hash,
                "sources": [candidate.display for candidate in candidates],
            }
        )

    for index, (sid, candidates) in enumerate(new_sessions, 1):
        decision, source, rows = _choose_session(sid, candidates, tables=tables)
        decisions.append(decision)
        if decision["decision"].startswith("accept_") and source is not None and rows is not None:
            selected_sources[sid] = (source, rows)
            accepted[sid] = {**decision["selected"], "session_id": sid}
        if args.progress_every > 0 and index % args.progress_every == 0:
            elapsed = time.perf_counter() - started
            rate = elapsed / index
            eta = rate * (len(new_sessions) - index)
            counts = Counter(row["decision"] for row in decisions)
            print(
                f"  {index}/{len(new_sessions)} elapsed={elapsed:.0f}s eta={eta:.0f}s "
                f"main={counts['accept_main']} activity0702={counts['accept_activity']} "
                f"activity0716={counts['accept_red_crate_activity']} "
                f"truth_only={sum(value for key, value in counts.items() if key.endswith('truth_only'))} "
                f"reject={counts['reject']}",
                flush=True,
            )

    # Apply copy-only actions after the complete plan is known.  A failure cannot leave source data
    # missing because sources are never moved.
    quarantine_records: list[dict[str, Any]] = []
    if args.apply:
        quarantine_by_semantics, used_quarantine_names = _existing_quarantine_controls(
            args.quarantine_dir
        )
        for decision in decisions:
            sid = decision["session_id"]
            if decision["decision"].startswith("accept_"):
                source, rows = selected_sources[sid]
                destination = ROOT / decision["destination"]
                if destination.exists():
                    raise FileExistsError(f"refusing to overwrite existing destination: {destination}")
                _write_rows(source, rows, destination)
            elif decision["decision"] == "reject":
                variants = decision.get("variants") or []
                richest = max(
                    (row for row in variants if row.get("truth")),
                    key=lambda row: (int(row.get("claims_present") or 0), int(row.get("bytes") or 0)),
                    default=None,
                )
                if richest is None:
                    continue
                observed = richest.get("observed_consistency") or {}
                has_truth_evidence = bool(
                    richest.get("mismatches")
                    or richest.get("revealed_lower_bound_mismatches")
                    or int(observed.get("observed_settlement_missing_items") or 0) > 0
                    or int(observed.get("observed_settlement_quality_mismatch_items") or 0) > 0
                )
                if not has_truth_evidence:
                    continue
                source_candidates = by_session[sid]
                source = next((row for row in source_candidates if row.display == richest["source"]), None)
                if source is None:
                    continue
                rows = source.rows()
                invalid_record = {**richest, "session_id": sid}
                name = _canonical_name(invalid_record, invalid=True)
                semantic_sha256 = _rows_semantic_sha256(rows)
                existing_control = quarantine_by_semantics.get((sid, semantic_sha256))
                if existing_control is not None:
                    destination = existing_control
                    quarantine_decision = "duplicate_existing_quarantine_content"
                else:
                    name = _unique_quarantine_name(name, semantic_sha256, used_quarantine_names)
                    destination = args.quarantine_dir / name
                    _write_rows(source, rows, destination)
                    used_quarantine_names.add(name)
                    quarantine_by_semantics[(sid, semantic_sha256)] = destination
                    quarantine_decision = "archive_quarantine_positive"
                quarantine_records.append(
                    {
                        "session_id": sid,
                        "decision": quarantine_decision,
                        "destination": str(destination.relative_to(ROOT)).replace("\\", "/"),
                        "semantic_sha256": semantic_sha256,
                        "reasons": richest.get("reasons") or [],
                        "truth": richest.get("truth") or {},
                        "exact_claim_mismatches": richest.get("mismatches") or [],
                        "observed_consistency": observed,
                        "revealed_lower_bounds": richest.get("revealed_lower_bounds") or {},
                        "revealed_lower_bound_mismatches": richest.get("revealed_lower_bound_mismatches") or [],
                        "auxiliary_public_facts": richest.get("auxiliary_public_facts") or [],
                    }
                )

    diag_records = _archive_diagnostics(diagnostics, accepted, apply=args.apply)
    gold_records = _gold_records(decisions)
    summary = Counter(decision["decision"] for decision in decisions)
    payload = {
        "schema_version": 2,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "apply_copy_only" if args.apply else "dry_run_read_only",
        # The manifest is durable project evidence; keep stable source labels instead of
        # publishing machine-specific absolute Desktop paths.
        "source_roots": [_root_label(path) for path in roots],
        "rules": {
            "capture_sources": (
                "npcap_live, windivert_live, and wss_live reset JSON share one parser, "
                "truth, dedupe, and warehouse-source qualification path"
            ),
            "dedupe": "session id first; SHA-256 distinguishes exact vs same-session duplicates",
            "truth_gate": "all present public/action/skill count/cells/value/average facts must match raw settlement",
            "validity_split": (
                "replay_valid requires complete bidding windows; truth_valid requires a single "
                "identified session plus a valid settlement truth gate. Workflow-incomplete "
                "truth is isolated from replay/clean-eval."
            ),
            "partial_reveals": "distinct revealed entities are lower-bound evidence; any per-quality count/cells/value bound above settlement is invalid",
            "activity_route": (
                "shipwreck X511-X520 -> fatbeans_activity_20260702_shipwreck; "
                "red-crate 2541-2550/4541-4550 -> "
                "fatbeans_activity_20260716_red_crate_shipwreck; truth-only variants use "
                "fatbeans_truth_only_settlement/{normal,activity_20260702_shipwreck,"
                "activity_20260716_red_crate_shipwreck}"
            ),
            "warehouse_source_route": (
                "post-2026-07-23 non-ranked captures without a stable explicit source -> "
                "postupdate_unknown_warehouse_source; explicit player-custom captures -> "
                "player_custom_warehouse; neither pool enters official drop-rate or clean-eval"
            ),
            "source_mutation": "none",
        },
        "summary": dict(sorted(summary.items())),
        "quarantine_positive_controls": quarantine_records,
        "diagnostics": diag_records,
        "decisions": decisions,
    }

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.apply:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        merged_dates = _merged_session_dates(accepted, destination=args.session_dates)
        args.session_dates.parent.mkdir(parents=True, exist_ok=True)
        args.session_dates.write_text(
            json.dumps(merged_dates, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_gold_manifest(
            decisions,
            source_manifest=args.manifest,
            generated_at=payload["generated_at"],
            output_path=args.gold_manifest,
        )
        if quarantine_records:
            # Re-read immediately before merge: this shared ledger may have changed in another window.
            try:
                old = json.loads(EXCLUDE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                old = []
            by_name = {Path(row.get("file", "")).name: row for row in old if isinstance(row, dict)}
            for row in quarantine_records:
                name = Path(row["destination"]).name
                by_name.setdefault(
                    name,
                    {
                        "file": row["destination"],
                        "reason": ";".join(row["reasons"]),
                        "ingest": args.ingest_label,
                    },
                )
            EXCLUDE.write_text(
                json.dumps(list(by_name.values()), ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8",
            )

    elapsed = time.perf_counter() - started
    print("summary:")
    for key, value in sorted(summary.items()):
        print(f"  {key}: {value}")
    print(f"  q5 value/avg samples: {len(gold_records)} (paired={sum(row['paired'] for row in gold_records)})")
    print(
        f"  diagnostic archive actions: {sum(row.get('decision') == 'archive_diagnostic' for row in diag_records)}"
    )
    print(f"elapsed={elapsed:.1f}s")
    if args.apply:
        print(f"manifest={args.manifest.relative_to(ROOT)}")
        print(f"session_dates={args.session_dates.relative_to(ROOT)}")
        print(f"gold_manifest={args.gold_manifest.relative_to(ROOT)}")
    else:
        print("dry-run only; re-run with --apply after reviewing the summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
