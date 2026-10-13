"""Archive the current live raw capture with a small classification summary."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bidking_lab.live.fatbeans import (  # noqa: E402
    parse_fatbeans_packets,
    load_fatbeans_packets_from_rows,
    live_batches_from_fatbeans_events,
)
from bidking_lab.live.state import (  # noqa: E402
    LiveSessionState,
    apply_observation_batch,
    live_state_to_session_obs,
)


@dataclass(frozen=True)
class ArchiveSummary:
    source: str
    archive_path: str
    classification: str
    archived: bool
    duplicate: bool
    rows: int
    frames: int
    sends: int
    states: int
    session_id: str
    map_id: int | None
    hero: str | None
    message_ids: tuple[str, ...]
    reason: str


def _load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    stripped = text.lstrip()
    if stripped.startswith("["):
        value = json.loads(text)
        if not isinstance(value, list):
            return []
        return [row for row in value if isinstance(row, dict)]

    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _rows_to_json_array_text(rows: list[Mapping[str, Any]]) -> str:
    return json.dumps(rows, ensure_ascii=False, indent=2) + "\n"


def _latest_session_id(rows: list[Mapping[str, Any]]) -> str:
    for row in reversed(rows):
        session_id = row.get("SessionID")
        if session_id:
            return str(session_id)
    return ""


def _safe_token(value: Any, *, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_") or fallback


def _classify_capture(events: Any) -> tuple[str, str]:
    has_settlement = any(
        state.message_id == 0x002D or state.inventory_items
        for state in events.states
    )
    if has_settlement:
        return "complete", "settlement frame present"
    if events.states:
        return "partial", "state frames present without settlement"
    if events.sends or events.frames:
        return "diagnostic", "frames present but no parsed state"
    return "diagnostic", "no game frames parsed"


def _hero_from_events(events: Any) -> str | None:
    state = LiveSessionState()
    for batch in live_batches_from_fatbeans_events(events):
        state = apply_observation_batch(state, batch)
    session = live_state_to_session_obs(state)
    return session.hero if session is not None else None


def _latest_map_id(events: Any) -> int | None:
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


def _message_ids(rows: list[Mapping[str, Any]]) -> tuple[str, ...]:
    values = {
        str(row.get("MessageID"))
        for row in rows
        if row.get("MessageID") not in (None, "")
    }
    return tuple(sorted(values))


def _capture_start_token(rows: list[Mapping[str, Any]]) -> str:
    capture_time = str((rows[0] if rows else {}).get("CaptureTime") or "")
    if len(capture_time) >= 19:
        date = capture_time[:10]
        time = capture_time[11:19].replace(":", "")
        return f"{date}_{time}"
    return "unknown_time"


def _find_existing_archive(archive_dir: Path, session_id: str) -> Path | None:
    if not session_id:
        return None
    token = _safe_token(session_id)
    matches = sorted(archive_dir.rglob(f"*{token}.json"))
    return matches[0] if matches else None


def archive_live_raw(
    source: Path,
    *,
    archive_dir: Path,
    force: bool = False,
    dry_run: bool = False,
    classified_dirs: bool = True,
) -> ArchiveSummary:
    rows = _load_jsonl_rows(source)
    packets = load_fatbeans_packets_from_rows(rows)
    events = parse_fatbeans_packets(packets)
    classification, reason = _classify_capture(events)
    session_id = _latest_session_id(rows)
    map_id = _latest_map_id(events)
    hero = _hero_from_events(events)
    existing = _find_existing_archive(archive_dir, session_id)

    if existing is not None and not force:
        return ArchiveSummary(
            source=str(source),
            archive_path=str(existing),
            classification=classification,
            archived=False,
            duplicate=True,
            rows=len(rows),
            frames=len(events.frames),
            sends=len(events.sends),
            states=len(events.states),
            session_id=session_id,
            map_id=map_id,
            hero=hero,
            message_ids=_message_ids(rows),
            reason=f"{reason}; duplicate session already archived",
        )

    name = (
        f"windivert_{_capture_start_token(rows)}_{classification}_"
        f"{_safe_token(hero)}_{_safe_token(map_id)}_{_safe_token(session_id)}.json"
    )
    destination_dir = archive_dir / classification if classified_dirs else archive_dir
    destination = destination_dir / name
    if destination.exists() and force:
        stem = destination.stem
        suffix = destination.suffix
        index = 2
        while destination.exists():
            destination = archive_dir / f"{stem}_{index}{suffix}"
            index += 1

    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_rows_to_json_array_text(rows), encoding="utf-8")

    return ArchiveSummary(
        source=str(source),
        archive_path=str(destination),
        classification=classification,
        archived=not dry_run,
        duplicate=False,
        rows=len(rows),
        frames=len(events.frames),
        sends=len(events.sends),
        states=len(events.states),
        session_id=session_id,
        map_id=map_id,
        hero=hero,
        message_ids=_message_ids(rows),
        reason=reason,
    )


def _print_text(summary: ArchiveSummary) -> None:
    status = "archived" if summary.archived else "skipped"
    if summary.duplicate:
        status = "duplicate"
    print(
        f"archive_live_raw: {status} {summary.classification} "
        f"rows={summary.rows} frames={summary.frames} states={summary.states}"
    )
    print(
        f"session={summary.session_id or '-'} "
        f"hero={summary.hero or '?'} map={summary.map_id or '?'}"
    )
    print(f"messages={','.join(summary.message_ids) or '-'}")
    print(f"path={summary.archive_path}")
    print(f"reason={summary.reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "data" / "logs" / "live" / "raw" / "windivert_live.jsonl",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=ROOT / "data" / "logs" / "live" / "raw" / "archive",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--flat",
        action="store_true",
        help="Write directly under archive-dir instead of complete/partial/diagnostic subdirs.",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

    if not args.source.exists():
        print(f"Missing source: {args.source}", file=sys.stderr)
        return 1
    try:
        summary = archive_live_raw(
            args.source,
            archive_dir=args.archive_dir,
            force=args.force,
            dry_run=args.dry_run,
            classified_dirs=not args.flat,
        )
    except Exception as exc:  # noqa: BLE001 - operational CLI should report
        print(f"archive_live_raw failed: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    else:
        _print_text(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
