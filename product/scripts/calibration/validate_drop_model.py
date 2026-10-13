# -*- coding: utf-8 -*-
"""【权威验证】掉率模型逐图精度 — 一致归一、可复现(2026-06-25)。

替代旧的 per_map_accuracy_0625.md。旧报告的"价值比值"是【伪信号】:它用固定 vault_count
除以逐图不同的经验总件数/价值,导致比值与"经验总件数"corr=-0.60、与"件均价值"corr=+0.68,
把单纯的件数/价值波动当成了"某些图系统性欠估"(2402/2406/2408 的 0.67/0.79/0.80 即此假象)。

本脚本用【两侧一致归一】重算:
  模型仓价值 = 经验总件数 N(m) × Σ_q 实测品质权重[档][q] × 模型桶内价值VPI[q]
  经验仓价值 = Σ_q 经验件数[q] × 经验件均价值[q]   (mean 与 p50 各算一份)
  价值比值 = 模型/经验   (用经验 N 公平: 生产模型同样按经验取每图仓件数)

并输出两个诊断:
  - 品质MAE = |实测品质权重 - 经验count占比| 均值(留出验证, 看权重是否过拟合)。
  - 桶内VPI比值 = 模型桶内价值/经验桶内价值(逐品质)。已知系统残差: 低桶(q1~q4)模型偏高、
    高桶(q5/q6)模型偏低 —— 源于 col6 驼峰(峰在价值1e4、压制>1e5)按桶独立归一所致;
    总价值上两头抵消(headline 准), 但逐件价值形状有偏。

用法:
  python scripts/calibration/validate_drop_model.py                # 全部有样本的图
  python scripts/calibration/validate_drop_model.py --maps 2401 2402 2408
"""
from __future__ import annotations
import argparse, json, sys, os
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "calibration"))
import rankmap_drop_model as M  # noqa: E402

QKS = ["q1", "q3", "q4", "q5", "q6"]
# reconstructed_map_prior_*.json are OLD-rate (pre-2026-06-25-noon) empirical reconstructions; they
# now live in an era-marked dir so they are never confused with the post-noon NEW drop table. See
# docs/HANDOFF_drop_rate_closeout_2026-06-27 §8.4-era.
PRIOR_DIR = ROOT / "data/processed/reconstructed_prior_OLD_only_pre_20260625_1200"


def _model_bucket_vpi(map_id):
    """模型桶内件均价值 VPI[q] = Σ P(i|桶) × value(i)。同一地图档内各图相同(col6/池/档相同)。"""
    rates, _count, iq, iv, _nm = M.drop_rates(map_id)
    byq = defaultdict(lambda: [0.0, 0.0])
    for i, p in rates.items():
        qk = "q1" if iq[i] in (1, 2) else "q%d" % iq[i]
        byq[qk][0] += p
        byq[qk][1] += p * iv[i]
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
    mvpi = _model_bucket_vpi(map_id)

    emp_cnt = {q: pq[q]["count"]["mean"] for q in QKS}
    emp_vpi_mean = {q: pq[q]["value_per_item"].get("mean", 0) for q in QKS}
    emp_vpi_p50 = {q: pq[q]["value_per_item"].get("p50", 0) for q in QKS}
    N = sum(emp_cnt.values())

    emp_val_mean = sum(emp_cnt[q] * emp_vpi_mean[q] for q in QKS)
    emp_val_p50 = sum(emp_cnt[q] * emp_vpi_p50[q] for q in QKS)
    mdl_val = N * sum(meas[q] * mvpi[q] for q in QKS)

    # 品质MAE(留出): 实测权重 vs 本图经验count占比
    emp_share = {q: emp_cnt[q] / N for q in QKS} if N else {q: 0 for q in QKS}
    q_mae = sum(abs(emp_share[q] - meas[q]) for q in QKS) / len(QKS)

    vpi_ratio = {q: (mvpi[q] / emp_vpi_mean[q] if emp_vpi_mean[q] else 0) for q in QKS}
    return {
        "map": mid, "tier": tier, "n_sessions": n_sess, "N": N,
        "ratio_mean": mdl_val / emp_val_mean if emp_val_mean else 0,
        "ratio_p50": mdl_val / emp_val_p50 if emp_val_p50 else 0,
        "q_mae": q_mae, "vpi_ratio": vpi_ratio,
    }


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--maps", nargs="*", type=int, default=None)
    a = ap.parse_args(argv[1:])
    if a.maps:
        ids = [str(m) for m in a.maps]
    else:
        ids = sorted(p.stem.split("_")[-1] for p in PRIOR_DIR.glob("reconstructed_map_prior_*.json"))
    rows = [r for r in (validate_map(m) for m in ids) if r]
    rows.sort(key=lambda r: (r["tier"] or "", r["map"]))
    print(f"{'map':>5} {'tier':>4} {'n':>4} {'N':>5} {'ratio(mean)':>11} {'ratio(p50)':>10} {'qMAE%':>6} | q1/q3/q4/q5/q6 VPI比值")
    for r in rows:
        vr = "/".join(f"{r['vpi_ratio'][q]:.2f}" for q in QKS)
        print(f"{r['map']:>5} {r['tier']:>4} {r['n_sessions']:>4} {r['N']:>5.1f} "
              f"{r['ratio_mean']:>11.3f} {r['ratio_p50']:>10.3f} {r['q_mae']*100:>6.1f} | {vr}")
    if rows:
        import statistics as st
        rm = [r["ratio_mean"] for r in rows]
        qm = [r["q_mae"] for r in rows]
        print(f"\n汇总({len(rows)}图): 价值比值(mean) 中位 {st.median(rm):.3f} "
              f"[{min(rm):.2f}-{max(rm):.2f}], 品质MAE 中位 {st.median(qm)*100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
