"""Build pre-extracted Aisha eval slices for fast regression (no fatbeans scan in pytest).

Sources: representative 20 + batch-B 14 + recordings inbox (aisha only).
Output: data/fixtures/aisha_eval_slices/manifest.json + slices/*.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture  # noqa: E402

from audit_aisha_gap import (  # noqa: E402
    SAMPLE_ROOTS,
    _build_snapshot,
    _events_through_sort,
    _hero_from_events,
    _map_id_from_events,
    _prefix_sort_id,
    _settlement_breakdown,
)
from scripts.organize_fatbeans_real_samples import sample_meta  # noqa: E402

REPRESENTATIVE_STEMS = (
    "fatbeans_valid_aisha_2410_2rounds_2410_129501857",
    "fatbeans_valid_aisha_2510_3rounds_2510_129501857",
    "fatbeans_mixed_aisha_2403_3rounds_2403_129501899",
    "fatbeans_valid_aisha_2503_2rounds_2503_129501865",
    "fatbeans_valid_aisha_2410_2rounds_2410_129501866",
    "fatbeans_valid_aisha_2408_2rounds_2408_136751777",
    "fatbeans_mixed_aisha_2504_4rounds_2504_127412812",
    "fatbeans_valid_aisha_2502_4rounds_2502_129501870",
    "fatbeans_valid_aisha_2406_5rounds_2406_127412813",
    "fatbeans_valid_aisha_2401_4rounds_2401_129501856",
    "fatbeans_valid_aisha_2501_5rounds_2501_129501866",
    "fatbeans_valid_aisha_2501_4rounds_2501_129501867",
    "fatbeans_valid_aisha_2407_2rounds_2407_129501857",
    "fatbeans_valid_aisha_2507_5rounds_2507_129501859",
    "fatbeans_mixed_aisha_2401_4rounds_2401_129501899",
    "fatbeans_valid_aisha_2502_3rounds_2502_129501867",
    "fatbeans_valid_aisha_2501_3rounds_2501_129501901",
    "fatbeans_valid_aisha_2501_4rounds_2501_129501862",
    "fatbeans_valid_aisha_2506_2rounds_2506_129501862",
    "fatbeans_valid_aisha_2405_3rounds_2405_129501857",
)
BATCH_B_FILENAMES = (
    "fatbeans_valid_aisha_2410_2rounds_2410_1295018574148404_0093.json",
    "fatbeans_valid_aisha_2510_3rounds_2510_1295018576112447_0210.json",
    "fatbeans_mixed_aisha_2403_3rounds_2403_1295018990047075_0002.json",
    "fatbeans_valid_aisha_2401_3rounds_2401_1295019017806948_0032.json",
    "fatbeans_valid_aisha_2501_5rounds_2501_1295018669960456_0139.json",
    "fatbeans_valid_aisha_2401_4rounds_2401_1295018573249457_0039.json",
    "fatbeans_valid_aisha_2401_5rounds_2401_1295018668661353_0045.json",
    "fatbeans_valid_aisha_2507_5rounds_2507_1295018647628127_0196.json",
    "fatbeans_valid_aisha_2401_5rounds_2401_1367517779389841_0050.json",
    "fatbeans_valid_aisha_2403_3rounds_2403_1295019017648392_0051.json",
    "fatbeans_valid_aisha_2404_3rounds_2404_1295018992793264_0056.json",
    "fatbeans_valid_aisha_2504_5rounds_2504_1295018708289152_0166.json",
    "fatbeans_valid_aisha_2401_3rounds_2401_1295018668515929_0030.json",
    "fatbeans_valid_aisha_2510_3rounds_2510_1274128128479934_0209.json",
)
INBOX_ROOT = ROOT / "data" / "samples" / "recordings_inbox_20260614"
INVALID_SESSIONS = INBOX_ROOT / "invalid_sessions.json"


def _load_invalid_inbox_sources() -> set[str]:
    if not INVALID_SESSIONS.is_file():
        return set()
    payload = json.loads(INVALID_SESSIONS.read_text(encoding="utf-8"))
    out: set[str] = set()
    for row in payload.get("sessions") or ():
        if not isinstance(row, dict):
            continue
        source_file = str(row.get("source_file") or "").strip()
        if source_file:
            out.add(source_file)
    return out


def _resolve_by_stem(stem: str) -> Path | None:
    for root in SAMPLE_ROOTS:
        if not root.is_dir():
            continue
        matches = sorted(root.glob(f"{stem}*.json"))
        if matches:
            return matches[0]
    return None


def _resolve_by_name(name: str) -> Path | None:
    for root in SAMPLE_ROOTS:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _inbox_aisha_paths() -> list[Path]:
    manifest_path = INBOX_ROOT / "manifest.json"
    if not manifest_path.is_file():
        return []
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_to_dest: dict[str, Path] = {}
    for row in payload.get("copied_files") or ():
        dest = row.get("dest")
        source = row.get("source")
        if dest and source:
            source_to_dest[str(Path(source).resolve()).lower()] = ROOT / dest
    invalid_sources = _load_invalid_inbox_sources()
    out: list[Path] = []
    seen: set[str] = set()
    for row in payload.get("accepted") or ():
        if row.get("kind") != "json" or row.get("verdict") != "accept":
            continue
        meta = row.get("meta") or {}
        if meta.get("hero") != "aisha":
            continue
        source_name = Path(str(row.get("path") or "")).name
        if source_name in invalid_sources:
            continue
        src_key = str(Path(row["path"]).resolve()).lower()
        path = source_to_dest.get(src_key)
        if path is None or not path.is_file():
            name = Path(row["path"]).name
            matches = sorted((INBOX_ROOT / "captures").rglob(name))
            path = matches[0] if matches else None
        if path is None or not path.is_file():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out

DEFAULT_OUT = ROOT / "data" / "fixtures" / "aisha_eval_slices"
MAX_EVAL_ROUND = 5


def _collect_source_paths() -> list[tuple[str, Path]]:
    out: dict[str, tuple[str, Path]] = {}
    for stem in REPRESENTATIVE_STEMS:
        path = _resolve_by_stem(stem)
        if path is not None:
            out[str(path.resolve())] = ("fatbeans_repr", path)
    for name in BATCH_B_FILENAMES:
        path = _resolve_by_name(name)
        if path is not None:
            out[str(path.resolve())] = ("fatbeans_batch_b", path)
    for path in _inbox_aisha_paths():
        out[str(path.resolve())] = ("recordings_inbox", path)
    return sorted(out.values(), key=lambda item: str(item[1]).lower())


def _truth_from_settlement(settlement: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_items": int(settlement["total_items"]),
        "total_cells": int(settlement["total_cells"]),
        "total_value": int(settlement["total_value"]),
        "counts": {
            "q1": int(settlement["counts"].get(1, 0)),
            "q3": int(settlement["counts"].get(3, 0)),
            "q4": int(settlement["counts"].get(4, 0)),
            "q5": int(settlement["counts"].get(5, 0)),
            "q6": int(settlement["counts"].get(6, 0)),
        },
        "cells": {
            "q1": int(settlement["cells"].get(1, 0)),
            "q3": int(settlement["cells"].get(3, 0)),
            "q4": int(settlement["cells"].get(4, 0)),
            "q5": int(settlement["cells"].get(5, 0)),
            "q6": int(settlement["cells"].get(6, 0)),
        },
        "values": {
            "q6": int(settlement.get("values", {}).get(6, 0) or 0),
        },
    }


def build_slices(
    *,
    out_dir: Path,
    max_round: int,
    skip_invalid: bool,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    slices_dir = out_dir / "slices"
    slices_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for source_kind, path in _collect_source_paths():
        try:
            events = parse_fatbeans_capture(path)
        except OSError as exc:
            skipped.append({"path": str(path), "reason": f"parse:{exc}"})
            continue
        hero = _hero_from_events(events)
        if hero != "aisha":
            skipped.append({"path": str(path), "reason": "not_aisha"})
            continue
        settlement = _settlement_breakdown(events)
        if not settlement:
            skipped.append({"path": str(path), "reason": "no_settlement"})
            continue
        if skip_invalid:
            meta = sample_meta(path)
            if not meta.parsed or meta.sample_class != "valid":
                skipped.append({"path": str(path), "reason": f"class:{meta.sample_class}"})
                continue
        pre_batches = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
        if len(pre_batches) < 2:
            skipped.append({"path": str(path), "reason": "rounds_lt_2"})
            continue
        map_id = _map_id_from_events(events)
        truth = _truth_from_settlement(settlement)
        session = path.stem
        for eval_round in range(1, min(max_round, len(pre_batches)) + 1):
            prefix_batches = pre_batches[:eval_round]
            sort_id = _prefix_sort_id(prefix_batches)
            prefix_events = _events_through_sort(events, sort_id) if sort_id else events
            snapshot = _build_snapshot(
                hero="aisha",
                events=prefix_events,
                prefix_batches=prefix_batches,
                map_id=map_id,
            )
            slice_id = f"{session}_r{eval_round:02d}"
            slice_path = slices_dir / f"{slice_id}.json"
            payload = {
                "slice_id": slice_id,
                "source_kind": source_kind,
                "source_file": path.name,
                "eval_round": eval_round,
                "file_rounds": len(pre_batches),
                "map_id": map_id,
                "truth": truth,
                "snapshot": snapshot,
            }
            slice_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            entries.append(
                {
                    "slice_id": slice_id,
                    "path": str(slice_path.relative_to(ROOT)).replace("\\", "/"),
                    "source_kind": source_kind,
                    "source_file": path.name,
                    "eval_round": eval_round,
                    "file_rounds": len(pre_batches),
                    "map_id": map_id,
                }
            )

    manifest = {
        "schema_version": 1,
        "hero": "aisha",
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "slice_count": len(entries),
        "skipped_sources": len(skipped),
        "slices": entries,
        "skipped": skipped[:50],
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--max-round", type=int, default=MAX_EVAL_ROUND)
    parser.add_argument("--include-mixed", action="store_true", help="Do not require sample_class=valid")
    args = parser.parse_args()
    manifest = build_slices(
        out_dir=args.out_dir,
        max_round=args.max_round,
        skip_invalid=not args.include_mixed,
    )
    print(
        f"built {manifest['slice_count']} slices -> {args.out_dir} "
        f"(skipped_sources={manifest['skipped_sources']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
