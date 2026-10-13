# -*- coding: utf-8 -*-
"""Build the truth-anchored per-(map, quality) VALUE table from NEW-era settlements.

Phase 5 (2026-06-27): the drop-table value_fitted (drop-probability-weighted mean over the droppable
catalog) runs ~2x the empirical settled per-quality mean (ahmed 56xx: q3 fitted 4315 vs settled 2082),
so turning it on over-estimates. The engine's item_values[q] should be the EXPECTED VALUE OF A SINGLE
quality-q ITEM IN THE VAULT — i.e. the empirical settled mean of quality-q items, long-tail-replaced.

This computes exactly that from NEW-era samples: for each settled inventory item, normalized_item_value
(aisha_calib long-tail replacement) grouped by (map_id, quality) and (family, quality) and global. A
per-map cell is used when it has >= MIN_ITEMS items; else family; else global. Emitted as a per-map
resolved table consumed by the sidecar (regenerate_drop_weights) / engine value column.

  python scripts/calibration/build_empirical_quality_values.py
Output: data/processed/empirical_quality_values_NEW_0627.json
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for sub in ("src", "scripts/calibration", "hero_ref/src"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from bidking_lab.live import fatbeans as FB  # noqa: E402
import aisha_calib as C  # noqa: E402

MANIFEST = ROOT / "data" / "sample_manifests" / "era_partition_noon_2026-06-27.json"
OUT = ROOT / "data" / "processed" / "empirical_quality_values_NEW_0627.json"
MIN_ITEMS = 40  # per-quality items needed to trust a per-map (or per-family) mean
QMAP = {1: "q1", 3: "q3", 4: "q4", 5: "q5", 6: "q6"}  # q2 folds into q1 below


def main() -> int:
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    new = [r["path"] for r in man["rows"] if r["era"] == "NEW"]
    # collect normalized values by (map,q), (family,q), (q)
    by_map: dict[tuple[str, int], list[float]] = defaultdict(list)
    by_fam: dict[tuple[str, int], list[float]] = defaultdict(list)
    by_glob: dict[int, list[float]] = defaultdict(list)
    n_games = 0
    for fp in new:
        p = ROOT / fp if not Path(fp).is_absolute() else Path(fp)
        if not p.is_file():
            continue
        try:
            ev = FB.parse_fatbeans_capture(p)
        except Exception:
            continue
        st_state = next((s for s in reversed(ev.states) if s.inventory_items), None)
        mid = None
        for s in reversed(ev.states):
            if getattr(s, "map_id", None):
                mid = str(s.map_id)
                break
        if not st_state or not mid:
            continue
        n_games += 1
        fam = mid[:2]
        for it in st_state.inventory_items:
            q = int(it.quality or 0)
            if q == 2:
                q = 1  # fold green into q1
            if q not in QMAP:
                continue
            iid = int(getattr(it, "item_id", 0) or 0)
            nv = C.normalized_item_value(iid)
            if nv is None:
                nv = float(getattr(it, "value", 0) or 0)
            if nv > 0:
                by_map[(mid, q)].append(float(nv))
                by_fam[(fam, q)].append(float(nv))
                by_glob[q].append(float(nv))

    glob_mean = {q: (st.mean(v) if v else 0.0) for q, v in by_glob.items()}
    fam_mean = {k: st.mean(v) for k, v in by_fam.items() if len(v) >= MIN_ITEMS}
    map_mean = {k: st.mean(v) for k, v in by_map.items() if len(v) >= MIN_ITEMS}

    # resolve a value table for every map present in the drop table
    dw = json.loads((ROOT / "data" / "processed" / "drop_weights_all_maps_0627.json").read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    cov = defaultdict(int)
    for mid in dw:
        fam = mid[:2]
        vals = {}
        srcs = {}
        for q, qk in QMAP.items():
            if (mid, q) in map_mean:
                vals[qk] = int(round(map_mean[(mid, q)])); srcs[qk] = "map"
            elif (fam, q) in fam_mean:
                vals[qk] = int(round(fam_mean[(fam, q)])); srcs[qk] = "family"
            else:
                vals[qk] = int(round(glob_mean.get(q, 0.0))); srcs[qk] = "global"
            cov[srcs[qk]] += 1
        out[mid] = {"quality_values": vals, "_src": srcs}
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"games={n_games}  maps={len(out)}  -> {OUT.name}")
    print(f"coverage (map/family/global cells): {dict(cov)}")
    print("global per-quality settled normalized means:", {QMAP[q]: int(v) for q, v in sorted(glob_mean.items())})
    # show a few family means for sanity
    print("\nper-family settled normalized means (q3/q4/q5/q6, where >=MIN_ITEMS):")
    fams = sorted({k[0] for k in fam_mean})
    for fam in fams:
        row = {QMAP[q]: int(fam_mean[(fam, q)]) for q in (3, 4, 5, 6) if (fam, q) in fam_mean}
        print(f"  {fam}xx: {row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
