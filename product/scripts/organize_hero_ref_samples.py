"""Organize Hero Ref diagnostic exports into a canonical sample archive.

Default sources:
- data/logs/live/exports/HeroRefDiag-*.zip
- data/logs/live/raw/archive/reset/windivert_live_*_reset.json

Default destination:
- data/samples/hero_ref/archive/exports/{YYYY-MM-DD}/
- data/samples/hero_ref/archive/reset/{YYYY-MM-DD}/
- data/samples/hero_ref/manifest.json

Default mode is dry-run. Use --apply to copy files and refresh the manifest.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bidking_lab.live.fatbeans import (  # noqa: E402
    load_fatbeans_packets_from_rows,
    parse_fatbeans_packets,
)

DEFAULT_EXPORTS = ROOT / "data" / "logs" / "live" / "exports"
DEFAULT_RESET = ROOT / "data" / "logs" / "live" / "raw" / "archive" / "reset"
DEFAULT_ARCHIVE = ROOT / "data" / "samples" / "hero_ref" / "archive"
DEFAULT_MANIFEST = ROOT / "data" / "samples" / "hero_ref" / "manifest.json"

EXPORT_NAME_RE = re.compile(
    r"^HeroRefDiag-(?P<date>\d{8})-(?P<time>\d{6})-(?P<session>.+)\.zip$"
)
RESET_NAME_RE = re.compile(
    r"^windivert_live_(?P<date>\d{4}-\d{2}-\d{2})_.*_(?P<map>\d+)_(?P<token>\d+)_reset\.json$"
)


@dataclass(frozen=True)
class SampleRecord:
    sample_id: str
    session_id: str
    hero: str | None
    map_id: int | None
    phase: str | None
    themes: tuple[str, ...]
    export_zip: str
    raw_reset: str | None
    public_info_ids: tuple[int, ...]
    public_info_rows: dict[int, float | int]
    skill_ids: tuple[int, ...]
    settlement_q5: int | None
    settlement_items: int | None
    priority: str
    notes_zh: str


def _session_from_export_name(name: str) -> tuple[str, str] | None:
    match = EXPORT_NAME_RE.match(name)
    if match is None:
        return None
    raw_date = match.group("date")
    export_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
    return export_date, match.group("session").replace("_manual", "manual")


def _read_export_metadata(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as zf:
        snap = json.loads(zf.read("latest_snapshot.json"))
    context = snap.get("ui_contract", {}).get("context", {})
    truth = snap.get("ui_contract", {}).get("truth", {})
    public_rows = {
        int(row["info_id"]): row.get("value")
        for row in snap.get("public_info_rows") or ()
        if isinstance(row, dict) and row.get("info_id") is not None
    }
    skill_ids = tuple(
        sorted(
            {
                int(row["skill_id"])
                for row in snap.get("skill_reveal_rows") or ()
                if isinstance(row, dict) and row.get("skill_id") is not None
            }
        )
    )
    q5 = truth.get("q5") if isinstance(truth.get("q5"), dict) else {}
    return {
        "session_id": snap.get("session_id") or context.get("session_id"),
        "hero": context.get("hero"),
        "map_id": context.get("map_id"),
        "phase": context.get("phase"),
        "public_info_rows": public_rows,
        "skill_ids": skill_ids,
        "settlement_q5": q5.get("count"),
        "settlement_items": truth.get("total_items"),
    }


def _parsed_public_ids_from_zip(path: Path) -> tuple[int, ...]:
    with zipfile.ZipFile(path) as zf:
        if "raw/windivert_live.jsonl" not in zf.namelist():
            return ()
        rows = [
            json.loads(line)
            for line in zf.read("raw/windivert_live.jsonl").decode("utf-8-sig").splitlines()
            if line.strip()
        ]
    events = parse_fatbeans_packets(load_fatbeans_packets_from_rows(rows))
    ids = sorted({info.info_id for state in events.states for info in state.public_infos})
    return tuple(ids)


def _infer_themes(
    *,
    public_rows: dict[int, float | int],
    parsed_public_ids: tuple[int, ...],
    skill_ids: tuple[int, ...],
    settlement_q5: int | None,
    hero: str | None,
) -> tuple[str, ...]:
    themes: list[str] = []
    if hero == "maria" or skill_ids:
        themes.append("maria_skill")
    zero_public = {200015, 200019, 200037, 200011}
    if zero_public.intersection(parsed_public_ids) or any(
        public_rows.get(info_id) in (0, 0.0) for info_id in zero_public
    ):
        themes.append("gold_zero_public")
    if {200013, 200014, 200015, 200016}.intersection(parsed_public_ids):
        themes.append("public_avg_cells")
    if {200027, 200001, 200022, 200050}.intersection(parsed_public_ids):
        themes.append("public_info_minimap")
    if settlement_q5 == 0:
        themes.append("settlement_q5_zero")
    if not themes:
        themes.append("general")
    return tuple(dict.fromkeys(themes))


def _priority_for(themes: tuple[str, ...]) -> str:
    if "gold_zero_public" in themes:
        return "P0"
    if "maria_skill" in themes or "public_info_minimap" in themes:
        return "P1"
    return "P2"


def _notes_for(
    *,
    themes: tuple[str, ...],
    parsed_public_ids: tuple[int, ...],
    public_rows: dict[int, float | int],
) -> str:
    if "gold_zero_public" in themes:
        ids = sorted(zero_public := {200015, 200019, 200037, 200011} & set(parsed_public_ids))
        if ids:
            return f"公开信息金为零 raw 块：{ids}；parser fix 后应进入 public_info_rows。"
        shown = [
            info_id
            for info_id in sorted(zero_public)
            if public_rows.get(info_id) in (0, 0.0)
        ]
        if shown:
            return f"snapshot 已有零值行 {shown}；若 UI 仍 0/1/1 则是旧 parser/旧 exe。"
    if "maria_skill" in themes:
        return "Maria skill + 公开信息；可用于 minimap marker / maria_skill evidence 回归。"
    if "public_info_minimap" in themes:
        return "公开摇号/轮廓/命名物品；可用于小地图 marker 回归。"
    return "通用 Hero Ref 诊断包。"


def _find_reset_for_session(reset_dir: Path, session_id: str | None) -> Path | None:
    if not session_id or ":" not in session_id:
        return None
    _map, token = session_id.split(":", 1)
    matches = sorted(reset_dir.glob(f"windivert_live_*_{_map}_{token}_reset.json"))
    return matches[-1] if matches else None


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def build_records(
    *,
    exports_dir: Path,
    reset_dir: Path,
    archive_root: Path,
) -> list[SampleRecord]:
    records: list[SampleRecord] = []
    for export_path in sorted(exports_dir.glob("HeroRefDiag-*.zip")):
        parsed_name = _session_from_export_name(export_path.name)
        if parsed_name is None:
            continue
        export_date, _ = parsed_name
        meta = _read_export_metadata(export_path)
        parsed_public_ids = _parsed_public_ids_from_zip(export_path)
        themes = _infer_themes(
            public_rows=meta["public_info_rows"],
            parsed_public_ids=parsed_public_ids,
            skill_ids=meta["skill_ids"],
            settlement_q5=meta["settlement_q5"],
            hero=meta["hero"],
        )
        reset_src = _find_reset_for_session(reset_dir, meta["session_id"])
        reset_dest = None
        if reset_src is not None:
            reset_dest = (
                archive_root / "reset" / export_date / reset_src.name
            )
        sample_id = f"HR-{export_date.replace('-', '')}-{meta['session_id'].split(':')[-1][-4:] if meta.get('session_id') else export_path.stem[-8:]}"
        records.append(
            SampleRecord(
                sample_id=sample_id,
                session_id=str(meta["session_id"] or ""),
                hero=meta["hero"],
                map_id=meta["map_id"],
                phase=meta["phase"],
                themes=themes,
                export_zip=_rel(
                    archive_root / "exports" / export_date / export_path.name
                ),
                raw_reset=_rel(reset_dest) if reset_dest is not None else None,
                public_info_ids=parsed_public_ids or tuple(
                    sorted(meta["public_info_rows"])
                ),
                public_info_rows={
                    int(k): v for k, v in meta["public_info_rows"].items()
                },
                skill_ids=meta["skill_ids"],
                settlement_q5=meta["settlement_q5"],
                settlement_items=meta["settlement_items"],
                priority=_priority_for(themes),
                notes_zh=_notes_for(
                    themes=themes,
                    parsed_public_ids=parsed_public_ids,
                    public_rows=meta["public_info_rows"],
                ),
            )
        )
    return records


def apply_archive(
    *,
    exports_dir: Path,
    reset_dir: Path,
    archive_root: Path,
    records: list[SampleRecord],
) -> list[str]:
    actions: list[str] = []
    seen_exports: set[Path] = set()
    for record in records:
        src = exports_dir / Path(record.export_zip).name
        if not src.exists():
            src = ROOT / record.export_zip
        if src.exists() and src not in seen_exports:
            dest = ROOT / record.export_zip
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists() or dest.stat().st_size != src.stat().st_size:
                shutil.copy2(src, dest)
                actions.append(f"copy export -> {record.export_zip}")
            seen_exports.add(src)
        if record.raw_reset:
            reset_src = _find_reset_for_session(reset_dir, record.session_id)
            if reset_src is not None:
                reset_dest = ROOT / record.raw_reset
                reset_dest.parent.mkdir(parents=True, exist_ok=True)
                if not reset_dest.exists() or reset_dest.stat().st_size != reset_src.stat().st_size:
                    shutil.copy2(reset_src, reset_dest)
                    actions.append(f"copy reset -> {record.raw_reset}")
    return actions


def write_manifest(path: Path, records: list[SampleRecord]) -> None:
    payload = {
        "schema_version": 1,
        "updated_at": date.today().isoformat(),
        "archive_root": "data/samples/hero_ref/archive",
        "source_exports_dir": "data/logs/live/exports",
        "source_reset_dir": "data/logs/live/raw/archive/reset",
        "samples": [asdict(record) for record in records],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exports-dir", type=Path, default=DEFAULT_EXPORTS)
    parser.add_argument("--reset-dir", type=Path, default=DEFAULT_RESET)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    records = build_records(
        exports_dir=args.exports_dir,
        reset_dir=args.reset_dir,
        archive_root=args.archive_root,
    )
    print(f"samples indexed: {len(records)}")
    for record in records:
        print(
            f"  {record.sample_id} {record.session_id} themes={','.join(record.themes)} "
            f"-> {record.export_zip}"
        )
    if args.apply:
        actions = apply_archive(
            exports_dir=args.exports_dir,
            reset_dir=args.reset_dir,
            archive_root=args.archive_root,
            records=records,
        )
        write_manifest(args.manifest, records)
        print(f"manifest written: {args.manifest.relative_to(ROOT)}")
        for action in actions:
            print(action)
    else:
        print("dry-run only; re-run with --apply to copy files and write manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
