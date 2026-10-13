"""Organize real Fatbeans samples into a canonical local archive.

The default plan consolidates:
- data/samples/fatbeans
- data/samples/fatbeans_manual_inbox
- data/logs/live/raw/archive/complete

Valid and mixed captures are placed in data/samples/fatbeans with canonical
names. Parse-error captures are moved to data/samples/fatbeans_invalid.
Live raw archives are copied into the sample archive so operational logs remain
available. Default mode is dry-run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansCaptureEvents,
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)
from bidking_lab.live.state import (  # noqa: E402
    LiveSessionState,
    apply_observation_batch,
    live_state_to_session_obs,
)
from scripts.evaluate_fatbeans_v3_samples import _round_rows_for_events  # noqa: E402
from scripts.summarize_fatbeans_sample_manifest import (  # noqa: E402
    _observed_settlement_consistency,
)

DEFAULT_ARCHIVE = ROOT / "data" / "samples" / "fatbeans"
DEFAULT_INBOX = ROOT / "data" / "samples" / "fatbeans_manual_inbox"
DEFAULT_LIVE_COMPLETE = ROOT / "data" / "logs" / "live" / "raw" / "archive" / "complete"
DEFAULT_INVALID = ROOT / "data" / "samples" / "fatbeans_invalid"
DEFAULT_ACTIVITY = ROOT / "data" / "samples" / "fatbeans_activity_20260605_shipwreck"


@dataclass(frozen=True)
class SampleMeta:
    source_path: str
    source_kind: str
    parsed: bool
    sample_class: str
    session_token: str | None
    hero: str | None
    map_id: int | None
    rounds: int
    bid_windows: int
    ready_windows: int
    no_state_windows: int
    constraint_conflict_windows: int
    parse_error: str | None
    sha256: str


@dataclass(frozen=True)
class OrganizeAction:
    action: str
    source: str
    destination: str | None
    sample_class: str
    source_kind: str
    reason: str
    duplicate_of: str | None
    session_token: str | None
    hero: str | None
    map_id: int | None
    rounds: int
    ready_windows: int
    no_state_windows: int
    parse_error: str | None
    sha256: str


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _safe_token(value: Any, *, fallback: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    if not text:
        return fallback
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_") or fallback


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hero_from_events(events: FatbeansCaptureEvents) -> str | None:
    state = LiveSessionState()
    for batch in live_batches_from_fatbeans_events(events):
        state = apply_observation_batch(state, batch)
    session = live_state_to_session_obs(state)
    return session.hero if session is not None else None


def _latest_map_id(events: FatbeansCaptureEvents) -> int | None:
    for state in reversed(events.states):
        if state.map_id is not None:
            return int(state.map_id)
    for send in reversed(events.sends):
        if send.session_id:
            prefix = str(send.session_id).split(":", 1)[0]
            try:
                return int(prefix)
            except ValueError:
                return None
    return None


def _session_token(events: FatbeansCaptureEvents) -> str | None:
    for send in reversed(events.sends):
        if send.session_id:
            return _safe_token(send.session_id)
    for state in reversed(events.states):
        if state.session_id:
            return _safe_token(state.session_id)
    for status in reversed(events.statuses):
        if status.session_id:
            return _safe_token(status.session_id)
    return None


def _rounds(events: FatbeansCaptureEvents) -> int:
    bid_windows = sum(1 for send in events.sends if getattr(send, "kind", "") == "bid")
    observed_rounds = [
        int(state.round_index)
        for state in events.states
        if state.round_index is not None
    ]
    return max([bid_windows, *observed_rounds], default=0)


def _source_kind(
    path: Path,
    *,
    archive_dir: Path,
    inbox_dir: Path,
    live_dir: Path,
    activity_dir: Path = DEFAULT_ACTIVITY,
    invalid_dir: Path = DEFAULT_INVALID,
) -> str:
    resolved = path.resolve()
    for name, root in (
        ("archive", archive_dir),
        ("activity", activity_dir),
        ("manual", inbox_dir),
        ("live_complete", live_dir),
        ("invalid", invalid_dir),
    ):
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        return name
    return "external"


def _iter_json(paths: Iterable[Path]) -> list[Path]:
    expanded: list[Path] = []
    for path in paths:
        if not path.exists():
            continue
        if path.is_dir():
            expanded.extend(sorted(path.rglob("*.json")))
        elif path.suffix.lower() == ".json":
            expanded.append(path)
    return sorted(set(expanded), key=lambda path: (str(path.parent), path.stat().st_mtime, path.name.lower()))


def _source_priority(source_kind: str) -> int:
    return {
        "activity": 0,
        "archive": 1,
        "invalid": 2,
        "manual": 3,
        "live_complete": 4,
        "external": 5,
    }.get(source_kind, 9)


def _classify_rows(rows: list[dict[str, Any]]) -> tuple[str, int, int, int, int]:
    bid_windows = len(rows)
    ready_windows = sum(1 for row in rows if row.get("status") == "ready")
    no_state_windows = sum(1 for row in rows if row.get("status") == "no_state")
    conflict_windows = sum(1 for row in rows if row.get("status") == "constraint_conflict")
    if bid_windows == 0:
        return ("invalid_no_bid", bid_windows, ready_windows, no_state_windows, conflict_windows)
    if ready_windows == bid_windows:
        return ("valid", bid_windows, ready_windows, no_state_windows, conflict_windows)
    if ready_windows > 0:
        return ("mixed", bid_windows, ready_windows, no_state_windows, conflict_windows)
    if no_state_windows:
        return ("invalid_no_state", bid_windows, ready_windows, no_state_windows, conflict_windows)
    if conflict_windows:
        return ("invalid_conflict", bid_windows, ready_windows, no_state_windows, conflict_windows)
    return ("invalid_no_ready", bid_windows, ready_windows, no_state_windows, conflict_windows)


def _session_count(events: FatbeansCaptureEvents) -> int:
    session_ids: set[str] = set()
    for state in events.states:
        if state.session_id:
            session_ids.add(str(state.session_id))
    for send in events.sends:
        if send.session_id:
            session_ids.add(str(send.session_id))
    for status_event in events.statuses:
        if status_event.session_id:
            session_ids.add(str(status_event.session_id))
    return len(session_ids)


def _declared_settled_mismatch(events: FatbeansCaptureEvents) -> bool:
    """True when the game DECLARED an exact total item count (skill/state field, e.g. ahmed 声明件数)
    that matches NEITHER the settled raw count NOR the settled q>0 count (称号/头像 counting-base
    tolerance). Declared>settled = the capture lost part of the settlement stream -> settlement is
    not usable ground truth. Returns False when no declaration or no settlement exists."""
    try:
        pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
        if not pre:
            return False
        state = LiveSessionState()
        for batch in pre:
            state = apply_observation_batch(state, batch)
        obs = live_state_to_session_obs(state)
        declared = getattr(obs, "total_item_count", None) if obs else None
        if declared is None:
            return False
        settled = next(
            (s for s in reversed(events.states) if getattr(s, "inventory_items", None)), None
        )
        if settled is None:
            return False
        items = settled.inventory_items or ()
        raw_n = len(items)
        q_n = sum(1 for it in items if int(getattr(it, "quality", 0) or 0) > 0)
        return int(declared) not in (raw_n, q_n)
    except Exception:
        return False


def sample_meta(
    path: Path,
    *,
    archive_dir: Path = DEFAULT_ARCHIVE,
    inbox_dir: Path = DEFAULT_INBOX,
    live_dir: Path = DEFAULT_LIVE_COMPLETE,
    activity_dir: Path = DEFAULT_ACTIVITY,
    invalid_dir: Path = DEFAULT_INVALID,
) -> SampleMeta:
    file_hash = _sha256(path)
    source_kind = _source_kind(
        path,
        archive_dir=archive_dir,
        inbox_dir=inbox_dir,
        live_dir=live_dir,
        activity_dir=activity_dir,
        invalid_dir=invalid_dir,
    )
    path_parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    force_quarantine = "fatbeans_invalid" in path_parts or name.startswith("fatbeans_invalid_")
    try:
        events = parse_fatbeans_capture(path)
    except Exception as exc:  # noqa: BLE001 - operational archive audit reports bad files
        return SampleMeta(
            source_path=_rel(path),
            source_kind=source_kind,
            parsed=False,
            sample_class="invalid_parse_error",
            session_token=None,
            hero=None,
            map_id=None,
            rounds=0,
            bid_windows=0,
            ready_windows=0,
            no_state_windows=0,
            constraint_conflict_windows=0,
            parse_error=type(exc).__name__,
            sha256=file_hash,
        )
    rows = _round_rows_for_events(path, events, tables=None, posterior_trials=0)
    sample_class, bid_windows, ready_windows, no_state_windows, conflict_windows = _classify_rows(rows)
    if force_quarantine:
        sample_class = "invalid_quarantined_sample"
    observed_consistency = _observed_settlement_consistency(events)
    if not sample_class.startswith("invalid") and (
        _session_count(events) > 1
        or observed_consistency["observed_settlement_missing_items"] > 0
        or observed_consistency["observed_settlement_quality_mismatch_items"] > 0
        # DECLARED-vs-settled guard (2026-07-04 audit: 82 ahmed "valid" samples had skill/state-declared
        # total count > settled count by up to 30% = settlement lost items; the per-item observed check
        # above can't see them because the declaration is an aggregate number, not item-level).
        or _declared_settled_mismatch(events)
    ):
        sample_class = "mixed"
    return SampleMeta(
        source_path=_rel(path),
        source_kind=source_kind,
        parsed=True,
        sample_class=sample_class,
        session_token=_session_token(events),
        hero=_hero_from_events(events),
        map_id=_latest_map_id(events),
        rounds=_rounds(events),
        bid_windows=bid_windows,
        ready_windows=ready_windows,
        no_state_windows=no_state_windows,
        constraint_conflict_windows=conflict_windows,
        parse_error=None,
        sha256=file_hash,
    )


def _dedupe_key(meta: SampleMeta) -> str:
    if meta.parsed and meta.session_token:
        return f"session:{meta.session_token}"
    return f"sha256:{meta.sha256}"


def _canonical_prefix(meta: SampleMeta) -> str:
    if meta.sample_class.startswith("invalid"):
        old_stem = _safe_token(Path(meta.source_path).stem)
        return f"fatbeans_{meta.sample_class}_{old_stem}_{meta.sha256[:10]}"
    return (
        f"fatbeans_{meta.sample_class}_{_safe_token(meta.hero)}_"
        f"{_safe_token(meta.map_id)}_{meta.rounds}rounds_"
        f"{_safe_token(meta.session_token or meta.sha256[:10])}"
    )


def _canonical_name(meta: SampleMeta, index: int) -> str:
    return f"{_canonical_prefix(meta)}_{index:04d}.json"


def _has_canonical_archive_name(source: Path, meta: SampleMeta) -> bool:
    if meta.sample_class.startswith("invalid"):
        return False
    stem = source.stem
    prefix = _canonical_prefix(meta)
    if not stem.startswith(prefix + "_"):
        return False
    suffix = stem[len(prefix) + 1 :]
    return len(suffix) == 4 and suffix.isdigit()


def _destination_for(meta: SampleMeta, *, archive_dir: Path, invalid_dir: Path, index: int) -> Path:
    if meta.sample_class.startswith("invalid"):
        reason = meta.sample_class.replace("invalid_", "") or "unknown"
        return invalid_dir / reason / _canonical_name(meta, index)
    return archive_dir / _canonical_name(meta, index)


def build_plan(
    paths: Iterable[Path],
    *,
    archive_dir: Path = DEFAULT_ARCHIVE,
    inbox_dir: Path = DEFAULT_INBOX,
    live_dir: Path = DEFAULT_LIVE_COMPLETE,
    invalid_dir: Path = DEFAULT_INVALID,
    activity_dir: Path = DEFAULT_ACTIVITY,
) -> dict[str, Any]:
    source_paths = _iter_json(paths)
    metas = [
        sample_meta(
            path,
            archive_dir=archive_dir,
            inbox_dir=inbox_dir,
            live_dir=live_dir,
            activity_dir=activity_dir,
            invalid_dir=invalid_dir,
        )
        for path in source_paths
    ]
    metas.sort(key=lambda meta: (_source_priority(meta.source_kind), meta.source_path))
    path_by_rel = {_rel(path): path for path in source_paths}
    source_path_set = {path.resolve() for path in source_paths}
    chosen_by_key: dict[str, SampleMeta] = {}
    duplicates: list[tuple[SampleMeta, SampleMeta]] = []
    for meta in metas:
        key = _dedupe_key(meta)
        existing = chosen_by_key.get(key)
        if existing is None:
            chosen_by_key[key] = meta
        else:
            duplicates.append((meta, existing))

    chosen = sorted(
        chosen_by_key.values(),
        key=lambda meta: (
            meta.sample_class.startswith("invalid"),
            meta.sample_class,
            _safe_token(meta.hero),
            _safe_token(meta.map_id),
            meta.rounds,
            meta.session_token or meta.sha256,
            meta.source_path,
        ),
    )
    actions: list[OrganizeAction] = []
    destination_set: set[Path] = set()
    for index, meta in enumerate(chosen, start=1):
        source = path_by_rel[meta.source_path]
        keep_existing_archive_name = (
            meta.source_kind == "archive"
            and _has_canonical_archive_name(source, meta)
        )
        if meta.source_kind in {"activity", "invalid"}:
            destination = source
        elif keep_existing_archive_name:
            destination = source
        else:
            destination = _destination_for(
                meta,
                archive_dir=archive_dir,
                invalid_dir=invalid_dir,
                index=index,
            )
        if destination.resolve() in destination_set:
            raise ValueError(f"duplicate destination planned: {destination}")
        destination_set.add(destination.resolve())
        if meta.source_kind == "activity":
            action = "keep"
            reason = "already_activity_reference"
        elif meta.source_kind == "invalid":
            action = "keep"
            reason = "already_quarantined_invalid"
        elif source.resolve() == destination.resolve():
            action = "keep"
            reason = "already_canonical"
        elif meta.source_kind == "live_complete":
            action = "copy"
            reason = "copy_live_complete_into_sample_archive"
        else:
            action = "move"
            reason = "canonicalize_local_sample"
        if destination.exists() and destination.resolve() not in source_path_set:
            action = "error"
            reason = "destination_exists"
        actions.append(
            OrganizeAction(
                action=action,
                source=meta.source_path,
                destination=_rel(destination),
                sample_class=meta.sample_class,
                source_kind=meta.source_kind,
                reason=reason,
                duplicate_of=None,
                session_token=meta.session_token,
                hero=meta.hero,
                map_id=meta.map_id,
                rounds=meta.rounds,
                ready_windows=meta.ready_windows,
                no_state_windows=meta.no_state_windows,
                parse_error=meta.parse_error,
                sha256=meta.sha256,
            )
        )

    for duplicate, existing in duplicates:
        actions.append(
            OrganizeAction(
                action="skip_duplicate",
                source=duplicate.source_path,
                destination=None,
                sample_class=duplicate.sample_class,
                source_kind=duplicate.source_kind,
                reason="duplicate_session_or_hash",
                duplicate_of=existing.source_path,
                session_token=duplicate.session_token,
                hero=duplicate.hero,
                map_id=duplicate.map_id,
                rounds=duplicate.rounds,
                ready_windows=duplicate.ready_windows,
                no_state_windows=duplicate.no_state_windows,
                parse_error=duplicate.parse_error,
                sha256=duplicate.sha256,
            )
        )

    summary = {
        "source": "fatbeans_real_sample_organizer",
        "input_files": len(metas),
        "unique_files": len(chosen),
        "duplicates": len(duplicates),
        "move": sum(1 for action in actions if action.action == "move"),
        "copy": sum(1 for action in actions if action.action == "copy"),
        "keep": sum(1 for action in actions if action.action == "keep"),
        "skip_duplicate": sum(1 for action in actions if action.action == "skip_duplicate"),
        "errors": sum(1 for action in actions if action.action == "error"),
        "valid": sum(1 for meta in chosen if meta.sample_class == "valid"),
        "mixed": sum(1 for meta in chosen if meta.sample_class == "mixed"),
        "invalid": sum(1 for meta in chosen if meta.sample_class.startswith("invalid")),
        "ready_windows": sum(meta.ready_windows for meta in chosen),
        "no_state_windows": sum(meta.no_state_windows for meta in chosen),
    }
    return {
        "summary": summary,
        "actions": [asdict(action) for action in actions],
    }


def _absolute(rel_or_abs: str) -> Path:
    path = Path(rel_or_abs)
    return path if path.is_absolute() else ROOT / path


def apply_plan(plan: dict[str, Any]) -> None:
    actions = [
        action for action in plan["actions"]
        if action["action"] in {"move", "copy"}
    ]
    move_actions = [action for action in actions if action["action"] == "move"]
    destination_paths = [_absolute(action["destination"]) for action in actions if action.get("destination")]
    if len(destination_paths) != len(set(path.resolve() for path in destination_paths)):
        raise ValueError("duplicate destinations in organize plan")

    staged: list[tuple[Path, Path]] = []
    for index, action in enumerate(move_actions, start=1):
        source = _absolute(action["source"])
        destination = _absolute(action["destination"])
        if destination.exists() and destination.resolve() != source.resolve():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = source.with_name(f".fatbeans_organize_tmp_{index:04d}_{source.name}")
        if temp.exists():
            raise FileExistsError(temp)
        source.rename(temp)
        staged.append((temp, destination))

    for temp, destination in staged:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp.rename(destination)

    for action in actions:
        if action["action"] != "copy":
            continue
        source = _absolute(action["source"])
        destination = _absolute(action["destination"])
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _print_summary(plan: dict[str, Any], *, dry_run: bool) -> None:
    summary = plan["summary"]
    mode = "dry_run" if dry_run else "apply"
    print(
        " ".join(
            (
                f"organize_fatbeans_real_samples: mode={mode}",
                f"input_files={summary['input_files']}",
                f"unique_files={summary['unique_files']}",
                f"duplicates={summary['duplicates']}",
                f"move={summary['move']}",
                f"copy={summary['copy']}",
                f"keep={summary['keep']}",
                f"skip_duplicate={summary['skip_duplicate']}",
                f"errors={summary['errors']}",
                f"valid={summary['valid']}",
                f"mixed={summary['mixed']}",
                f"invalid={summary['invalid']}",
                f"ready_windows={summary['ready_windows']}",
                f"no_state_windows={summary['no_state_windows']}",
            )
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Input files/directories. Defaults to archive, manual inbox, and live complete.",
    )
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--manual-inbox", type=Path, default=DEFAULT_INBOX)
    parser.add_argument("--live-complete-dir", type=Path, default=DEFAULT_LIVE_COMPLETE)
    parser.add_argument("--invalid-dir", type=Path, default=DEFAULT_INVALID)
    parser.add_argument("--activity-dir", type=Path, default=DEFAULT_ACTIVITY)
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    paths = args.paths or [args.archive_dir, args.activity_dir, args.manual_inbox, args.live_complete_dir]
    plan = build_plan(
        paths,
        archive_dir=args.archive_dir,
        inbox_dir=args.manual_inbox,
        live_dir=args.live_complete_dir,
        invalid_dir=args.invalid_dir,
        activity_dir=args.activity_dir,
    )
    if args.apply and plan["summary"]["errors"]:
        print("Refusing to apply plan with errors.", file=sys.stderr)
        return 2
    if args.apply:
        apply_plan(plan)
    if args.manifest_output is not None:
        args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_output.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(plan, dry_run=not args.apply)
        if args.manifest_output is not None:
            print(f"manifest_output={_rel(args.manifest_output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
