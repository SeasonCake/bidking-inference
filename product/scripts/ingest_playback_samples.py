"""Ingest the game's local `.playback` activity recordings into the sample lib.

Source: %LOCALAPPDATA%Low\\laolin\\BidKing\\<map>_<session>.playback
Dest:   data/samples/playback_prev_activity/<map>_<session>.playback (copied)
Manifest: data/sample_manifests/playback_prev_activity_<date>.json

VALIDITY CAVEAT (stamped into the manifest): the local trove only holds PREVIOUS
activity runs (sealed-cabin 2521-2530), NOT the current activity (2531+/4531+).
These are LOW-validity prev-activity references -- the shipwreck item pool / base
pricing is shared with 2501-2510, so they help validate the alias-borrowed pricing
and the warehouse-boost mechanism, but they are NOT current-activity ground truth.
Also: most overlap (same session id) with data/samples/fatbeans_activity_20260605_*.

Usage: python scripts/ingest_playback_samples.py [--apply] [--all-maps]
Default is dry-run (counts only). --apply copies files + writes the manifest.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from playback_loader import parse_playback_capture, settlement_truth, map_id_of  # noqa: E402

PLAYBACK_DIR = Path.home() / "AppData" / "LocalLow" / "laolin" / "BidKing"
DEST = ROOT / "data" / "samples" / "playback_prev_activity"
INGEST_DATE = "2026-06-19"
MANIFEST = ROOT / "data" / "sample_manifests" / f"playback_prev_activity_{INGEST_DATE}.json"

# Current-activity session-id watermark (user's 2531/4532 games, mid-June 2026).
# Anything below this is a previous activity run.
CURRENT_ACTIVITY_SESSION_MIN = 1_469_000_000_000_000

# Existing fatbeans activity sample session ids (overlap detection).
FATBEANS_ACTIVITY_DIR = ROOT / "data" / "samples" / "fatbeans_activity_20260605_shipwreck"


def _fatbeans_session_ids() -> set[str]:
    out: set[str] = set()
    if not FATBEANS_ACTIVITY_DIR.exists():
        return out
    for fp in FATBEANS_ACTIVITY_DIR.glob("*.json"):
        parts = fp.stem.split("_")
        for p in parts:
            if p.isdigit() and len(p) >= 15:
                out.add(p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--all-maps", action="store_true",
                    help="ingest every .playback, not just activity sealed-cabin 25xx")
    args = ap.parse_args()

    overlap = _fatbeans_session_ids()
    records: list[dict] = []
    skipped = 0
    for fp in sorted(PLAYBACK_DIR.glob("*.playback")):
        name = fp.stem  # <map>_<session>
        if "_" not in name:
            continue
        map_str, _, sess = name.partition("_")
        if not map_str.isdigit():
            continue
        map_id = int(map_str)
        is_activity_cabin = 2521 <= map_id <= 2540
        if not args.all_maps and not is_activity_cabin:
            skipped += 1
            continue
        try:
            ev = parse_playback_capture(fp)
        except Exception as e:  # noqa: BLE001
            print(f"  ! parse fail {fp.name}: {e}")
            continue
        truth = settlement_truth(ev)
        mid = map_id_of(ev) or map_id
        try:
            sess_int = int(sess)
        except ValueError:
            sess_int = 0
        records.append({
            "file": f"data/samples/playback_prev_activity/{fp.name}",
            "source_file": str(fp),
            "map_id": mid,
            "session_id": sess,
            "rounds_observed": sorted({s.round_index for s in ev.states if s.round_index}),
            "state_frames": len(ev.states),
            "settlement": truth,
            "activity_generation": "previous"
            if sess_int < CURRENT_ACTIVITY_SESSION_MIN else "current_or_unknown",
            "validity": "low_prev_activity",
            "overlaps_fatbeans": sess in overlap,
            "cohort_role": "prev_activity_low_validity_reference",
        })

    # summary
    by_map: dict[int, int] = {}
    overlap_n = sum(1 for r in records if r["overlaps_fatbeans"])
    with_truth = sum(1 for r in records if r["settlement"].get("total_cells"))
    for r in records:
        by_map[r["map_id"]] = by_map.get(r["map_id"], 0) + 1
    print(f"playback dir: {PLAYBACK_DIR}")
    print(f"candidates: {len(records)} (skipped non-activity: {skipped})")
    print(f"  by map: {dict(sorted(by_map.items()))}")
    print(f"  with settlement truth: {with_truth}/{len(records)}")
    print(f"  overlap w/ fatbeans_activity: {overlap_n}")
    gens = {}
    for r in records:
        gens[r["activity_generation"]] = gens.get(r["activity_generation"], 0) + 1
    print(f"  generation: {gens}")

    if not args.apply:
        print("\ndry-run; re-run with --apply to copy files + write manifest")
        return

    DEST.mkdir(parents=True, exist_ok=True)
    copied = 0
    for r in records:
        src = Path(r["source_file"])
        dst = DEST / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
            copied += 1
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "ingest_date": INGEST_DATE,
        "source": str(PLAYBACK_DIR),
        "validity_note": (
            "PREVIOUS-activity sealed-cabin recordings (2521-2530). NOT current "
            "activity (2531+). Low-validity: shipwreck pool/base pricing shared with "
            "2501-2510 so useful for alias-borrowed-pricing + boost-mechanism checks, "
            "but not current-activity ground truth. Many overlap fatbeans_activity_20260605."
        ),
        "current_activity_session_min": CURRENT_ACTIVITY_SESSION_MIN,
        "count": len(records),
        "files": records,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\napplied: copied {copied} new files to {DEST}")
    print(f"manifest: {MANIFEST}")


if __name__ == "__main__":
    main()
