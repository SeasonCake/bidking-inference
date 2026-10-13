"""Isolate the shipwreck ACTIVITY samples out of the calibration library — by MAP NUMBER.

⭐ RE-CLASSIFIED 2026-07-06 (author + full-pool census shipwreck_by_mapnum): the activity is
distinguished by MAP NUMBER X511-X520 (2511-2520 / 4511-4520), NOT by time. Those ids are
activity-exclusive (ZERO pre-2026-07-02 samples — the activity introduced them) with settled red
12.1% vs normal X501-X510 wreckage 7.3%. The OLD time+family discriminator WRONGLY isolated normal
X501-X510 games played during the window (they stay 7.3% red) — that diluted the activity-red
calibration (1.48x measured vs the true 1.65x) and thinned the normal calibration pool. Now we
isolate ONLY the activity-exclusive map numbers via `_is_activity_shipwreck_map`.

Modes: (default dry-run) plan the isolation; `--apply` isolate X511-X520 out of fatbeans/;
`--recover [--apply]` move wrongly-isolated NORMAL X501-X510 back to fatbeans/. Idempotent.
"""
from __future__ import annotations
import os, re, glob, shutil, sys
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bidking_lab.live.monitor import _is_activity_shipwreck_map  # noqa: E402

DEST = ROOT / "data" / "samples" / "fatbeans_activity_20260702_shipwreck"
FATBEANS = ROOT / "data" / "samples" / "fatbeans"
SRC_GLOB = str(FATBEANS / "**" / "*.json")
MAP = re.compile(r"_(?:valid|mixed)_[a-z]+_(\d{3,4})_")


def _map_of(bn: str) -> int | None:
    m = MAP.search(bn)
    return int(m.group(1)) if m else None


def _load_session_dates() -> dict[str, str]:
    import json, glob as _glob
    idx: dict[str, str] = {}
    for p in sorted(_glob.glob(str(ROOT / "data" / "sample_manifests" / "session_dates_*.json"))):
        try:
            idx.update(json.load(open(p, encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    return idx


def main() -> int:
    apply = "--apply" in sys.argv
    recover = "--recover" in sys.argv
    DEST.mkdir(parents=True, exist_ok=True)

    if recover:
        # Move wrongly-isolated NORMAL wreckage (NOT X511-X520) back to fatbeans/.
        moved, plan = 0, []
        for f in glob.glob(str(DEST / "*.json")):
            bn = os.path.basename(f)
            mid = _map_of(bn)
            if mid is None or _is_activity_shipwreck_map(mid):
                continue  # keep genuine activity (X511-X520) isolated
            plan.append((f, mid))
            if apply:
                shutil.move(f, str(FATBEANS / bn))
                moved += 1
        print(f"RECOVER normal wreckage from activity dir: {len(plan)} files  by map: {dict(sorted(Counter(m for _f, m in plan).items()))}")
        print(f"MOVED {moved} -> {FATBEANS}" if apply else "(dry-run; pass --apply to recover)")
        return 0

    # Forward isolation: move activity-exclusive X511-X520 games out of fatbeans/.
    moved, plan = 0, []
    for f in glob.glob(SRC_GLOB, recursive=True):
        bn = os.path.basename(f)
        mid = _map_of(bn)
        if mid is None or not _is_activity_shipwreck_map(mid):
            continue
        plan.append((f, mid))
        if apply:
            shutil.move(f, str(DEST / bn))
            moved += 1
    print(f"activity-map (X511-X520) in fatbeans/: {len(plan)}  by map: {dict(sorted(Counter(m for _f, m in plan).items()))}")
    print(f"MOVED {moved} -> {DEST}" if apply else "(dry-run; --apply to isolate, or --recover --apply to un-isolate normal)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
