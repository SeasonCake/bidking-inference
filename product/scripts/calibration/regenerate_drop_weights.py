"""Regenerate the fitted per-map drop-weight table: data/processed/drop_weights_all_maps_0627.json.

Each map -> {count(真实仓件数·样本中位), n_items, items: {item_id: [p_per_draw, value_raw, value_fitted]}}.
- p_per_draw: 每抽概率 = rankmap_drop_model.drop_rates() (品质权重 × col6价值权重, 真机制不动)。
- value_raw: 目录原值。
- value_fitted: **长尾红/金拟合替代值**(aisha_calib.normalized_item_value: 同(品质,形状) value>=5x floor
  的长尾, 价值替成同组非长尾均值)。期望价值算用 value_fitted, 避免极稀有长尾(世界冠军奖杯720万→31万)
  撑高期望 -> 假欠估。见 memory long-tail-red-contaminates-truth / drop-rate-system-closeout。

Re-run after item_category / RankMap / 样本 更新:
  python scripts/calibration/regenerate_drop_weights.py
"""
from __future__ import annotations

import glob
import json
import re
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "scripts" / "calibration", ROOT / "src", ROOT / "hero_ref" / "src"):
    sys.path.insert(0, str(p))

import aisha_calib as ac  # noqa: E402
import rankmap_drop_model as M  # noqa: E402
from bidking_lab.live.fatbeans import parse_fatbeans_capture  # noqa: E402

OUT = ROOT / "data" / "processed" / "drop_weights_all_maps_0627.json"
# Per-map prior sidecar consumed by the live engine (ahmad_ref_engine._load_drop_table_priors):
# {map_id: {quality_probs:{q1,q3,q4,q5,q6 normalized}, quality_values:{q1,q3,q4,q5,q6 fitted}}}.
# Derived purely from OUT + items.json quality, so it stays in lockstep with the drop table.
PRIORS_OUT = ROOT / "data" / "processed" / "drop_table_priors_0627.json"
# {item_id: normalized_value} for q6 long-tail reds. The LIVE engine loads this to override the RAW
# catalog value of a REVEALED long-tail red (else _revealed_red_minimap_value / _coarse_quality_reveal
# _floors price 世界冠军/非洲之心/新赛季大红 at full value in the headline bid). Single source = aisha_calib.
RED_LT_OUT = ROOT / "data" / "processed" / "red_longtail_normalized_0627.json"
SID = re.compile(r"_(\d{16})")
# 真实仓件数: col3 是全局常量 ~16, 非真件数; 取样本结算件数中位(~37-50)。
SAMPLE_GROUPS = ("group_0625_56xx", "group_post0625_newrate_classic", "group_0628_classic",
                 "group_pre0625noon_oldrate_classic", "group_0625_other")
RECOGNIZED_UNMODELED_RANKMAP_IDS = frozenset({6001})


def _generation_map_ids(
    rankmap_ids: list[str],
    legacy_memory_ids: list[str],
) -> list[str]:
    """Keep static-but-unmodeled RankMap rows out of generated distributions."""

    return sorted(
        {
            raw_map_id
            for raw_map_id in set(rankmap_ids) | set(legacy_memory_ids)
            if int(raw_map_id) not in RECOGNIZED_UNMODELED_RANKMAP_IDS
        },
        key=lambda value: int(value),
    )


def _real_vault_counts() -> tuple[dict, dict]:
    permap: dict[str, list[int]] = defaultdict(list)
    seen: set[str] = set()
    for grp in SAMPLE_GROUPS:
        for f in glob.glob(str(ROOT / "data" / "samples" / grp / "*.json")):
            m = SID.search(Path(f).name)
            sid = m.group(1) if m else Path(f).name
            if sid in seen:
                continue
            seen.add(sid)
            try:
                ev = parse_fatbeans_capture(Path(f))
            except Exception:  # noqa: BLE001
                continue
            s2 = next((s for s in reversed(ev.states) if s.inventory_items), None)
            if not s2:
                continue
            mid = getattr(next((s for s in reversed(ev.states) if getattr(s, "map_id", None)), None), "map_id", None)
            if mid:
                permap[str(mid)].append(len(s2.inventory_items))
    real = {m: st.median(v) for m, v in permap.items() if len(v) >= 5}
    fam = defaultdict(list)
    for m, v in real.items():
        fam[m[:2]].append(v)
    return real, {k: st.median(v) for k, v in fam.items()}


def _item_quality_map() -> dict[int, int]:
    cat = json.loads((ROOT / "data" / "processed" / "items.json").read_text(encoding="utf-8"))
    items = cat if isinstance(cat, list) else list(cat.values())
    return {int(x["item_id"]): int(x.get("quality") or 0) for x in items}


def _load_empirical_values() -> dict:
    """Truth-anchored per-(map,quality) settled normalized values (build_empirical_quality_values.py).
    These REPLACE the drop-prob-weighted fitted means as the sidecar's quality_values: the engine's
    item_values[q] must be the expected value of a single quality-q item IN THE VAULT = the empirical
    settled mean, NOT the droppable-catalog prob-weighted mean (which ran ~2x high -> over-estimate;
    ahmed A/B 2026-06-27). Missing file -> {} -> _derive_priors falls back to the fitted means."""
    fp = ROOT / "data" / "processed" / "empirical_quality_values_NEW_0627.json"
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _derive_priors(out: dict, qmap: dict[int, int]) -> dict:
    """Per-map collapsed quality distribution (probs) + per-quality VALUE, q2 folded into q1.

    quality_probs match the engine's _quality_probabilities return shape (collapsed, normalized).
    quality_values = empirical settled normalized means (truth-anchored, build_empirical_quality_values)
    when available, else the drop-prob-weighted value_fitted as a fallback."""
    emp = _load_empirical_values()
    priors: dict = {}
    for mid, d in out.items():
        # Family 45 (深海暗拍 deep-sea blind auction) is mechanically a 25xx sealed-cabin variant
        # (differs only in hiding rival bids). Its memory dropmap carries a classic-104-like quality
        # mix (q6≈5.1%) that CONTRADICTS settled truth — 21 real 45xx games settle q6=7.64%, matching
        # the 25xx sealed cabin (105, q6≈7.4%), NOT classic. The engine already maps 45xx→tier105
        # (_map_tier) for quality and →2501-2510 for value, both empirically correct. So we OMIT 45xx
        # from the sidecar and let the engine's existing 105/sealed-cabin alias own it. (§5: settled
        # truth overrides a memory-derived interpretation. See drop-rate-system-closeout memory.)
        if int(mid) // 100 == 45:
            continue
        qp: dict[int, float] = defaultdict(float)
        vnum: dict[int, float] = defaultdict(float)
        vden: dict[int, float] = defaultdict(float)
        for sid, triple in d["items"].items():
            p, _raw, fit = triple
            q = qmap.get(int(sid), 0)
            if not q:
                continue
            qp[q] += p
            vnum[q] += p * fit
            vden[q] += p
        col = {"q1": qp[1] + qp[2], "q3": qp[3], "q4": qp[4], "q5": qp[5], "q6": qp[6]}
        tot = sum(col.values()) or 1.0
        probs = {k: round(v / tot, 7) for k, v in col.items()}

        def _val(qn: int) -> int:
            num = vnum[qn] + (vnum[2] if qn == 1 else 0.0)
            den = vden[qn] + (vden[2] if qn == 1 else 0.0)
            return int(round(num / den)) if den else 0

        fitted_vals = {"q1": _val(1), "q3": _val(3), "q4": _val(4), "q5": _val(5), "q6": _val(6)}
        # Prefer truth-anchored empirical settled values; fall back to fitted per-quality where absent.
        emp_vals = (emp.get(str(mid)) or {}).get("quality_values") or {}
        vals = {qk: int(emp_vals.get(qk, fitted_vals[qk])) for qk in ("q1", "q3", "q4", "q5", "q6")}
        priors[mid] = {"quality_probs": probs, "quality_values": vals,
                       "_value_src": (emp.get(str(mid)) or {}).get("_src", {"all": "fitted_fallback"})}
    return priors


def main() -> int:
    real, fam = _real_vault_counts()
    rm_maps = [r[0] for r in M._load_b64("RankMap.txt")]
    mem = json.loads((ROOT / "data" / "processed" / "dropmap_mem_0625.json").read_text(encoding="utf-8"))
    all_maps = _generation_map_ids(rm_maps, list(mem))
    out: dict = {}
    for mid in all_maps:
        rc = real.get(mid) or fam.get(mid[:2])
        try:
            rates, count, iq, iv, nm = M.drop_rates(int(mid), vault_count=rc)
        except SystemExit:
            continue
        items = {}
        for i, r in rates.items():
            vnorm = ac.normalized_item_value(i)
            if vnorm is None:
                vnorm = iv.get(i, 0)
            items[str(i)] = [round(r, 7), iv.get(i, 0), int(vnorm)]
        out[mid] = {"count": round(count, 1), "n_items": len(items), "items": items}
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(out)} maps -> {OUT.name}  (items: [p_per_draw, value_raw, value_fitted])")
    priors = _derive_priors(out, _item_quality_map())
    PRIORS_OUT.write_text(json.dumps(priors, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(priors)} maps -> {PRIORS_OUT.name}  (quality_probs + fitted quality_values)")
    ac._catalog()
    red_lt = {str(iid): ac.normalized_item_value(iid)
              for iid, (v, shp, q, nm) in ac._CATALOG.items() if q == 6 and ac.is_longtail(iid)}
    RED_LT_OUT.write_text(json.dumps(red_lt, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(red_lt)} q6 long-tail reds -> {RED_LT_OUT.name}  (item_id -> normalized_value)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
