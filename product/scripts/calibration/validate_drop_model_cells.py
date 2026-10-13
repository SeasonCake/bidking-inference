# -*- coding: utf-8 -*-
"""CELLS dimension audit: model avg cells-per-item per quality vs empirical.

Mirrors validate_drop_model.py but measures CELLS (grid footprint = shape_w*shape_h)
instead of VALUE. Model cells-per-item[q] = Σ_i P(i|bucket q) * cells(i) over the
within-bucket-normalized drop distribution (same distribution the value VPI uses).
Empirical = reconstructed_map_prior per_quality[q].cells_per_item.mean.

CPI ratio = model cells-per-item / empirical cells-per-item. >1 = model OVER-predicts
footprint for that quality.
"""
from __future__ import annotations
import json, sys, statistics as st
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "calibration"))
import rankmap_drop_model as M  # noqa: E402

QKS = ["q1", "q3", "q4", "q5", "q6"]
# OLD-rate (pre-2026-06-25-noon) empirical reconstructions, era-marked dir. See validate_drop_model.py.
PRIOR_DIR = ROOT / "data/processed/reconstructed_prior_OLD_only_pre_20260625_1200"


def _item_cells():
    items = json.load(open(ROOT / "data/processed/items.json", encoding="utf-8"))
    cells = {}
    for it in (items.values() if isinstance(items, dict) else items):
        if isinstance(it, dict) and it.get("quality") and it.get("value"):
            i = int(it["item_id"])
            cells[i] = (it.get("shape_w") or 0) * (it.get("shape_h") or 0)
    return cells


_CELLS = _item_cells()


def _model_bucket_cpi(map_id):
    """Model cells-per-item[q] = Σ P(i|bucket) × cells(i). Same map-tier => same across maps in tier."""
    rates, _count, iq, _iv, _nm = M.drop_rates(map_id)
    byq = defaultdict(lambda: [0.0, 0.0])
    for i, p in rates.items():
        qk = "q1" if iq[i] in (1, 2) else "q%d" % iq[i]
        byq[qk][0] += p
        byq[qk][1] += p * _CELLS.get(i, 0)
    return {qk: (s[1] / s[0] if s[0] else 0.0) for qk, s in byq.items()}


def _tier_of(map_id):
    bm = {r[0]: r for r in M._load_b64("BidMap.txt")}
    mid = str(map_id)
    return bm[mid][7] if mid in bm else None


def _measured():
    return json.load(open(ROOT / "data/processed/quality_weights_measured.json", encoding="utf-8"))["by_tier"]


def validate_map(map_id):
    mid = str(map_id)
    fn = PRIOR_DIR / f"reconstructed_map_prior_{mid}.json"
    if not fn.exists():
        return None
    pq = json.load(open(fn, encoding="utf-8"))
    n_sess = pq.get("n_sessions", 0)
    pq = pq["per_quality"]
    if not all(q in pq for q in QKS):
        return None
    tier = _tier_of(map_id)
    meas = _measured().get(tier)
    if not meas:
        return None
    mcpi = _model_bucket_cpi(map_id)
    emp_cpi = {q: pq[q]["cells_per_item"].get("mean", 0) for q in QKS}
    emp_cnt = {q: pq[q]["count"]["mean"] for q in QKS}
    emp_n = {q: pq[q]["cells_per_item"].get("n", 0) for q in QKS}  # #sessions with that quality
    cpi_ratio = {q: (mcpi[q] / emp_cpi[q] if emp_cpi[q] else 0) for q in QKS}
    return {
        "map": mid, "tier": tier, "n_sessions": n_sess,
        "emp_cnt": emp_cnt, "emp_cpi": emp_cpi, "mcpi": mcpi,
        "cpi_ratio": cpi_ratio, "emp_n": emp_n,
    }


def main(argv):
    ids = sorted(p.stem.split("_")[-1] for p in PRIOR_DIR.glob("reconstructed_map_prior_*.json"))
    rows = [r for r in (validate_map(m) for m in ids) if r]
    rows.sort(key=lambda r: (r["tier"] or "", r["map"]))
    print(f"{'map':>5} {'tier':>4} {'n':>4} | model cpi q1/q3/q4/q5/q6 | emp cpi q1/q3/q4/q5/q6 | CPI ratio q1/q3/q4/q5/q6")
    for r in rows:
        mc = "/".join(f"{r['mcpi'].get(q,0):.2f}" for q in QKS)
        ec = "/".join(f"{r['emp_cpi'][q]:.2f}" for q in QKS)
        cr = "/".join(f"{r['cpi_ratio'][q]:.2f}" for q in QKS)
        print(f"{r['map']:>5} {r['tier']:>4} {r['n_sessions']:>4} | {mc} | {ec} | {cr}")

    # full-sample systematic bias per quality (weighted by #sessions that have that quality)
    print("\n=== Full-sample per-quality CPI ratio (model/empirical) ===")
    print(f"{'q':>4} {'median':>8} {'mean':>8} {'min':>6} {'max':>6} {'n_maps':>6}  (count-weighted mean across maps)")
    for q in QKS:
        rr = [r["cpi_ratio"][q] for r in rows if r["cpi_ratio"][q] > 0]
        if not rr:
            continue
        # count-weighted: weight each map by avg count of that quality (representativeness)
        wsum = sum(r["emp_cnt"][q] for r in rows if r["cpi_ratio"][q] > 0)
        wmean = sum(r["cpi_ratio"][q] * r["emp_cnt"][q] for r in rows if r["cpi_ratio"][q] > 0) / (wsum or 1)
        print(f"{q:>4} {st.median(rr):>8.3f} {wmean:>8.3f} {min(rr):>6.2f} {max(rr):>6.2f} {len(rr):>6}")

    # restrict to well-sampled maps (n_sessions >= 30) to avoid small-sample noise
    well = [r for r in rows if r["n_sessions"] >= 30]
    print(f"\n=== Well-sampled only (n>=30, {len(well)} maps) per-quality CPI ratio ===")
    for q in QKS:
        rr = [r["cpi_ratio"][q] for r in well if r["cpi_ratio"][q] > 0]
        if rr:
            print(f"{q:>4} median {st.median(rr):>6.3f}  mean {st.mean(rr):>6.3f}  [{min(rr):.2f}-{max(rr):.2f}]  n={len(rr)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
