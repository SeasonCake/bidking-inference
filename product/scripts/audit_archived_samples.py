# -*- coding: utf-8 -*-
"""Hygiene audit for the fatbeans valid sample library: dedup, activity-period segregation,
and settlement validity. Built 2026-06-23 after archiving recordings inboxes.

Three checks:
  1. DEDUP — same game captured twice. Game key = session_id (16-digit, unique per game) from the
     filename. The organizer dedups by sha256 (byte-identical), which MISSES the same game captured
     by two transports (windivert vs npcap) or two passes. Flags every filename group sharing a
     session_id beyond the first.
  2. ACTIVITY PERIOD — the shipwreck activity changed mechanics ~2026-06-18 but the event maps kept
     their numbers (2531-2540 / 4531-4539), so a PRE-18 game on map 2531 must NOT mix with a current
     2531. Watermark CURRENT_ACTIVITY_SESSION_MIN = 1_469_000_000_000_000 (from ingest_playback_samples).
     An EVENT map (family 25xx/45xx with the in-family number >= 31) whose session < watermark is a
     previous-activity capture -> segregate. Base/steady-state maps (24xx, 2501-2510, 4501-4510) have
     stable mechanics across the watermark and are NOT segregated.
  3. SETTLEMENT VALIDITY — reuses the eval's own gate (_strict_sample_exclusion_reason) which already
     excludes mixed / multi-session / observed-settlement-inconsistency. The "upper-space hole" symptom
     the author described is the visual face of an incomplete settlement; it is only reconstructable
     from positioned minimap snapshots (exports), NOT from fatbeans inventory_items (which carry cells +
     local_index but no row/col/shape). So for the fatbeans library the reliable validity signal is the
     settlement-consistency gate, computed by replay. (Requires --check-settlement; it parses each file.)

Default = report only. --apply moves segregated/invalid files to quarantine dirs (never deletes).
"""
from __future__ import annotations

import argparse
import glob
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

FATBEANS = ROOT / "data" / "samples" / "fatbeans"
PREV_ACTIVITY_DIR = ROOT / "data" / "samples" / "fatbeans_prev_activity_event"
DUP_DIR = ROOT / "data" / "samples" / "fatbeans_dupe_same_game"
CURRENT_ACTIVITY_SESSION_MIN = 1_469_000_000_000_000

NAME_RE = re.compile(
    r"fatbeans_(?P<cls>valid|mixed|invalid)_(?P<hero>[a-z]+)_(?P<map>\d+)_(?P<rounds>\d+)rounds_\d+_(?P<session>\d{15,16})_"
)


def _parse(path: Path) -> dict | None:
    m = NAME_RE.search(path.name)
    if not m:
        return None
    d = m.groupdict()
    d["map"] = int(d["map"])
    d["rounds"] = int(d["rounds"])
    d["session"] = int(d["session"])
    d["path"] = path
    return d


def _is_event_map(map_id: int) -> bool:
    # event (mechanic-changed) maps: 25xx/45xx with in-family index >= 31 (2531-2599, 4531-4599)
    fam = map_id // 100
    in_fam = map_id % 100
    return fam in (25, 45) and in_fam >= 31


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-settlement", action="store_true", help="replay each sample for the eval validity gate (slow)")
    ap.add_argument("--apply", action="store_true", help="move segregated/dupe/invalid files to quarantine dirs")
    ap.add_argument("--limit-settlement", type=int, default=0, help="cap settlement replays (0=all)")
    args = ap.parse_args()

    samples = [s for s in (_parse(Path(p)) for p in glob.glob(str(FATBEANS / "*.json"))) if s]
    valids = [s for s in samples if s["cls"] == "valid"]
    print(f"fatbeans/: {len(samples)} named files ({len(valids)} valid)")

    # 1. DEDUP by session id (across valid files)
    by_session: dict[int, list[dict]] = defaultdict(list)
    for s in valids:
        by_session[s["session"]].append(s)
    dupes = {sid: rows for sid, rows in by_session.items() if len(rows) > 1}
    dupe_files: list[dict] = []
    for sid, rows in dupes.items():
        rows.sort(key=lambda r: r["path"].name)
        dupe_files.extend(rows[1:])  # keep first, flag rest
    print(f"\n[1] DEDUP: {len(dupes)} session-ids appear >1x -> {len(dupe_files)} redundant files")
    for sid, rows in list(dupes.items())[:12]:
        print(f"    session {sid}: " + ", ".join(r["path"].name[-40:] for r in rows))

    # 2. ACTIVITY PERIOD segregation
    prev_event = [s for s in valids if _is_event_map(s["map"]) and s["session"] < CURRENT_ACTIVITY_SESSION_MIN]
    cur_event = [s for s in valids if _is_event_map(s["map"]) and s["session"] >= CURRENT_ACTIVITY_SESSION_MIN]
    print(f"\n[2] ACTIVITY: event-map (25xx/45xx >=31) samples = {len([s for s in valids if _is_event_map(s['map'])])}")
    print(f"    PREV-activity (session < watermark) to SEGREGATE: {len(prev_event)}")
    from collections import Counter
    print(f"      prev by map: {dict(sorted(Counter(s['map'] for s in prev_event).items()))}")
    print(f"    CURRENT-activity (session >= watermark) keep: {len(cur_event)}")
    print(f"      cur  by map: {dict(sorted(Counter(s['map'] for s in cur_event).items()))}")

    # 3. SETTLEMENT VALIDITY (optional replay)
    invalid_settlement: list[tuple[dict, str]] = []
    if args.check_settlement:
        from bidking_lab.live.fatbeans import parse_fatbeans_capture
        try:
            from evaluate_fatbeans_v3_samples import _strict_sample_exclusion_reason
        except ModuleNotFoundError:
            from scripts.evaluate_fatbeans_v3_samples import _strict_sample_exclusion_reason
        targets = valids if not args.limit_settlement else valids[: args.limit_settlement]
        print(f"\n[3] SETTLEMENT: replaying {len(targets)} samples through the eval validity gate ...")
        for i, s in enumerate(targets):
            try:
                ev = parse_fatbeans_capture(s["path"])
                reason = _strict_sample_exclusion_reason(s["path"], ev)
            except Exception as exc:  # noqa: BLE001
                reason = f"parse_error:{type(exc).__name__}"
            if reason:
                invalid_settlement.append((s, reason))
            if (i + 1) % 100 == 0:
                print(f"      ...{i+1}/{len(targets)} done, {len(invalid_settlement)} flagged")
        print(f"    settlement-INVALID (eval would exclude): {len(invalid_settlement)}")
        rc = Counter(r for _, r in invalid_settlement)
        print(f"      by reason: {dict(rc)}")
        for s, r in invalid_settlement[:15]:
            print(f"      {r}: {s['path'].name[-50:]}")

    # APPLY
    if args.apply:
        PREV_ACTIVITY_DIR.mkdir(parents=True, exist_ok=True)
        DUP_DIR.mkdir(parents=True, exist_ok=True)
        moved = 0
        for s in prev_event:
            dest = PREV_ACTIVITY_DIR / s["path"].name
            if s["path"].exists():
                shutil.move(str(s["path"]), str(dest)); moved += 1
        dmoved = 0
        prev_names = {s["path"].name for s in prev_event}
        for s in dupe_files:
            if s["path"].name in prev_names:
                continue  # already moved as prev-activity
            dest = DUP_DIR / s["path"].name
            if s["path"].exists():
                shutil.move(str(s["path"]), str(dest)); dmoved += 1
        print(f"\n[APPLY] moved {moved} prev-activity-event -> {PREV_ACTIVITY_DIR.name}/, {dmoved} same-game dupes -> {DUP_DIR.name}/")
        print("        (settlement-invalid NOT auto-moved; eval already excludes by name/gate)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
