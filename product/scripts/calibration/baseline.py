"""引擎偏差基线 (纯 valid + 长尾规范化) —— aisha 校准的标准体检。

对 valid_aisha 样本，在指定轮次输出引擎 displayed 偏差，按 family / 红桶分组，并给出
**raw 真值** 与 **规范化真值**(长尾金/红→同形状期望) 双轨，外加 clean/normalized 分块统计。
全部口径走 aisha_calib 核心，不再各写一份。

用法: python scripts/calibration/baseline.py [--round 4] [--clean-only]
  --clean-only: 只用 clean 样本(真值原样、无长尾替换)，给最干净的偏差读数。
"""
import argparse
import sys
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

QKEYS = ("q1", "q3", "q4", "q5", "q6")


def _mid(t):
    return int(t[1]) if isinstance(t, (list, tuple)) and len(t) > 1 and t[1] is not None else 0


def rbkt(c):
    return "0" if c <= 0 else ("1" if c <= 1 else ("2-3" if c <= 3 else "4+"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=4)
    ap.add_argument("--clean-only", action="store_true", help="只用无长尾的 clean 样本")
    args = ap.parse_args()
    sk_round, sk_id, _ = AISHA_SKILL_ROUNDS[args.round - 1]

    rows = []
    n_clean = n_norm = 0
    for s in C.valid_aisha_samples(rounds=4):
        try:
            ev = C._events(s)
            nt = C.normalized_truth(ev)
            if nt["category"] == "invalid":
                continue
            if nt["category"] == "normalized":
                n_norm += 1
                if args.clean_only:
                    continue
            else:
                n_clean += 1
            bd = C.settlement_breakdown(ev)
            t_red = int((bd.get("counts") or {}).get(6, 0))
            raw_v = nt["raw_value"]
            norm_v = nt["norm_value"]
            pre = [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]
            mp = _map_id_from_events(ev)
            snaps = [_snapshot_at_batch(events=ev, pre_batches=pre, batch_no=i, map_id=mp) for i in range(1, len(pre) + 1)]
            snap = next((x for x in snaps if sk_id in _skill_ids(x)), None)
            if snap is None:
                continue
            snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = sk_round
            r = run_reference_engine(prepare_reference_engine_snapshot(snap), max_combos=hero_max_combos_for_round("aisha", sk_round)).as_dict()
            disp = int(r.get("balanced") or 0)
            qr = r.get("quality_count_ranges") or {}
            e_cnt = sum(_mid(qr.get(q) or [None] * 3) for q in QKEYS)
            e_cell = _mid(r.get("total_grid_range") or [None] * 3)
            rows.append(dict(fam=int(mp) // 100 if mp else 0, t_red=t_red, raw=raw_v, norm=norm_v,
                             disp=disp, t_cnt=bd.get("total_items", 0), t_cell=bd.get("total_cells", 0),
                             e_cnt=e_cnt, e_cell=e_cell, cat=nt["category"]))
        except Exception:
            pass

    n = len(rows)
    print(f"R{sk_round} 基线  n={n}  (clean={n_clean} normalized={n_norm}{' — 已排除normalized' if args.clean_only else ''})\n")
    cb = sum(r["e_cnt"] - r["t_cnt"] for r in rows) / n
    gb = sum(r["e_cell"] - r["t_cell"] for r in rows) / n
    print(f"件数 bias={cb:+.1f}  grid bias={gb:+.1f}")
    print(f"价值 bias: 对 raw 真值 {sum(r['disp']-r['raw'] for r in rows)/n/1000:+.0f}k  |  对规范化真值 {sum(r['disp']-r['norm'] for r in rows)/n/1000:+.0f}k\n")

    print(f"{'红桶':>5s} {'n':>3s} | {'raw偏差k':>8s} {'rawOver':>7s} | {'norm偏差k':>9s} {'normOver':>8s}")
    g = defaultdict(list)
    for r in rows:
        g[rbkt(r["t_red"])].append(r)
    for b in ("0", "1", "2-3", "4+"):
        rs = g.get(b) or []
        if not rs:
            continue
        m = len(rs)
        rb = sum(r["disp"] - r["raw"] for r in rs) / m / 1000
        ro = sum(1 for r in rs if r["raw"] > 0 and r["disp"] > r["raw"] * 1.1) / m
        nb = sum(r["disp"] - r["norm"] for r in rs) / m / 1000
        no = sum(1 for r in rs if r["norm"] > 0 and r["disp"] > r["norm"] * 1.1) / m
        print(f"{b:>5s} {m:>3d} | {rb:>+8.0f} {ro:>6.0%} | {nb:>+9.0f} {no:>7.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
