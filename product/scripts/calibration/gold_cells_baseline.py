"""金(q5) 格子/件数 标定基线 —— 给"扁平 2.8 格/件"修复做 A/B 的专用体检。

baseline.py 看的是 total grid + value(按红桶)。本脚本把 **金 q5 单独拆出来**,并按
**金仓位(truth 金总格)分桶**,同时区分 **hidden(纯尺寸先验驱动,2.8 在此生效) vs known
(精确格/均格/已知件,2.8 不参与)** —— 因为扁平 2.8 只在 hidden 时影响估格。

口径全走 aisha_calib;尊重 AISHA_* env 开关,故改常数后设 flag 跑第二遍即得 A/B。

用法:
  python scripts/calibration/gold_cells_baseline.py [--round 4] [--limit N] [--clean-only] [--manifest FILE] [--paired-only] [--out FILE]
  --limit N : 只跑前 N 个(先计时估总耗时,别盲跑全量)
"""
import argparse
import io
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aisha_calib as C  # noqa: E402

for _p in ("scripts", "hero_ref/src", "hero_ref/tools", "src"):
    sys.path.insert(0, str(C.ROOT / _p))
from audit_aisha_gap import _map_id_from_events  # noqa: E402
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import AISHA_SKILL_ROUNDS, _skill_ids, _snapshot_at_batch  # noqa: E402
from sample_manifest_inputs import load_sample_paths  # noqa: E402


def _mid(t):
    return int(t[1]) if isinstance(t, (list, tuple)) and len(t) > 1 and t[1] is not None else 0


def _gold_evidence_class(ev: dict) -> str:
    """该样本 R4 时金 q5 的证据态: 决定扁平2.8是否参与估格。"""
    qc = (ev.get("quality_cells") or {}).get("q5")
    av = (ev.get("avg_cells") or {}).get("q5")
    fx = (ev.get("fixed_counts") or {}).get("q5")
    if qc is not None and float(qc) > 0 and abs(float(qc) - round(float(qc))) < 1e-6:
        return "cells_known"      # 极品扫描/揭示精确总格 -> 2.8 不参与
    if av is not None and float(av) > 0:
        return "avg_known"        # 极品均格 -> 2.8 不参与
    if fx is not None and int(fx) > 0:
        return "count_locked"     # 件数已知但格未知 -> 走除数/残差(部分受2.8族影响)
    return "hidden"               # 纯尺寸先验(2.8) 驱动 <- 修复目标


def gbkt(c):
    return "[0,6)" if c < 6 else ("[6,12)" if c < 12 else ("[12,20)" if c < 20 else "[20,+)"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个(计时用)")
    ap.add_argument("--clean-only", action="store_true")
    ap.add_argument("--manifest", type=Path, help="era, desktop-ingest, or focused gold sample manifest")
    ap.add_argument(
        "--paired-only",
        action="store_true",
        help="with a focused gold manifest, use only q5 value-sum + q5 avg-cells paired captures",
    )
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "_gold_cells_baseline.txt"))
    args = ap.parse_args()
    sk_round, sk_id, _ = AISHA_SKILL_ROUNDS[args.round - 1]

    out = io.TextIOWrapper(open(args.out, "wb"), encoding="utf-8")

    def emit(*a):
        line = " ".join(str(x) for x in a)
        out.write(line + "\n")
        out.flush()

    samples = (
        load_sample_paths(args.manifest, hero="aisha", paired_only=args.paired_only)
        if args.manifest
        else C.valid_aisha_samples(rounds=4)
    )
    if args.limit > 0:
        samples = samples[: args.limit]

    rows = []
    t0 = time.time()
    done = 0
    for s in samples:
        try:
            ev = C._events(s)
            nt = C.normalized_truth(ev)
            if nt["category"] == "invalid":
                continue
            if nt["category"] == "normalized" and args.clean_only:
                continue
            bd = C.settlement_breakdown(ev)
            t_gold_cnt = int((bd.get("counts") or {}).get(5, 0))
            t_gold_cell = int((bd.get("cells") or {}).get(5, 0))
            if t_gold_cnt <= 0:
                continue
            pre = [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]
            mp = _map_id_from_events(ev)
            snaps = [_snapshot_at_batch(events=ev, pre_batches=pre, batch_no=i, map_id=mp)
                     for i in range(1, len(pre) + 1)]
            snap = next((x for x in snaps if sk_id in _skill_ids(x)), None)
            if snap is None:
                continue
            snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = sk_round
            r = run_reference_engine(prepare_reference_engine_snapshot(snap),
                                     max_combos=hero_max_combos_for_round("aisha", sk_round)).as_dict()
            e_gold_cell = _mid((r.get("quality_cells_ranges") or {}).get("q5") or [None] * 3)
            e_gold_cnt = _mid((r.get("quality_count_ranges") or {}).get("q5") or [None] * 3)
            e_tot_cell = _mid(r.get("total_grid_range") or [None] * 3)
            gcls = _gold_evidence_class(r.get("evidence") or {})
            rows.append(dict(name=s.name, fam=int(mp) // 100 if mp else 0,
                             t_gc=t_gold_cnt, t_gx=t_gold_cell, e_gc=e_gold_cnt, e_gx=e_gold_cell,
                             t_tot=int(bd.get("total_cells", 0)), e_tot=e_tot_cell,
                             cls=gcls, cat=nt["category"]))
            done += 1
            if done % 10 == 0:
                dt = time.time() - t0
                emit(f"... {done} done, {dt:.0f}s, {dt/done:.1f}s/样本")
        except Exception as e:
            emit(f"[skip] {s.name[:40]} : {type(e).__name__}")

    n = len(rows)
    dt = time.time() - t0
    emit(f"\n金标定基线 R{sk_round}  n={n}  用时{dt:.0f}s ({dt/max(1,n):.1f}s/样本)\n")
    if n == 0:
        emit("无样本")
        out.close()
        Path(args.out).replace(args.out)
        return 0

    # 证据态分布
    cls_count = defaultdict(int)
    for r in rows:
        cls_count[r["cls"]] += 1
    emit("[金证据态分布] " + "  ".join(f"{k}={v}" for k, v in sorted(cls_count.items())))

    def stats(rs, label):
        if not rs:
            return
        m = len(rs)
        gx_bias = sum(r["e_gx"] - r["t_gx"] for r in rs) / m
        gc_bias = sum(r["e_gc"] - r["t_gc"] for r in rs) / m
        gx_mae = sum(abs(r["e_gx"] - r["t_gx"]) for r in rs) / m
        over = sum(1 for r in rs if r["e_gx"] > r["t_gx"]) / m
        under = sum(1 for r in rs if r["e_gx"] < r["t_gx"]) / m
        emit(f"  {label:14s} n={m:>3d} | 金格bias={gx_bias:+5.1f} MAE={gx_mae:4.1f} "
             f"过填={over:4.0%} 欠填={under:4.0%} | 金件bias={gc_bias:+4.1f}")

    emit("\n[金格偏差 — 全部]")
    stats(rows, "ALL")

    emit("\n[金格偏差 — 按证据态] (扁平2.8只在 hidden 生效)")
    for c in ("hidden", "count_locked", "avg_known", "cells_known"):
        stats([r for r in rows if r["cls"] == c], c)

    emit("\n[金格偏差 — hidden 按金仓位分桶] (验证'小仓过填/大仓欠填'两头偏)")
    hid = [r for r in rows if r["cls"] == "hidden"]
    for b in ("[0,6)", "[6,12)", "[12,20)", "[20,+)"):
        stats([r for r in hid if gbkt(r["t_gx"]) == b], b)

    emit("\n[总格偏差 context] " +
         f"bias={sum(r['e_tot']-r['t_tot'] for r in rows)/n:+.1f}  "
         f"MAE={sum(abs(r['e_tot']-r['t_tot']) for r in rows)/n:.1f}")

    out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
