"""样本库守门 —— 新样本入校准库前的体检。

两道检查(都走 aisha_calib 核心):
1) 坏真值: 「揭示⊆真值」物理铁律。breakdown_cells < R4 实际揭示格 => 部分结算坏样本
   (mixed 这类，应隔离)。见 memory fatbeans-mixed-samples-bad-truth。
2) 长尾分块: 列出含长尾(金/红)的 normalized 样本，提醒其真值是估计替换、勿入干净归档。
   见 memory long-tail-red-contaminates-truth。

用法: python scripts/calibration/verify_samples.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aisha_calib as C  # noqa: E402

for _p in ("scripts", "hero_ref/src", "hero_ref/tools", "src"):
    sys.path.insert(0, str(C.ROOT / _p))
from audit_aisha_gap import _map_id_from_events  # noqa: E402
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events  # noqa: E402
from regenerate_checkpoint_matrices import _snapshot_at_batch  # noqa: E402
from ahmad_ref_engine import _minimap_items_from_snapshot  # noqa: E402


def _revealed_cells(ev) -> int:
    pre = [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]
    if len(pre) < 2:
        return 0
    mp = _map_id_from_events(ev)
    dec = _snapshot_at_batch(events=ev, pre_batches=pre, batch_no=len(pre), map_id=mp)
    items = _minimap_items_from_snapshot(dec) or []
    return len({(it.get("row"), it.get("col")) for it in items if it.get("row") is not None and it.get("col") is not None})


def main() -> int:
    bad = []
    pubbad = []
    norm = []
    total = 0
    for s in C.valid_aisha_samples(rounds=4):
        try:
            ev = C._events(s)
            total += 1
            # 公开权威值 vs 结算真值(per-品质件数 + 总格 + 总件数): 不符 = 结算不全坏样本。
            # 用 scripts/calibration/quarantine_badtruth_samples.py --apply 隔离。
            mm = [m for m in C.public_exact_count_mismatches(ev) if int(m.get("info_id", -1)) >= 0]
            if mm:
                pubbad.append((s.name, mm))
            rev = _revealed_cells(ev)
            if not C.settlement_is_valid(ev, revealed_cells=rev):
                bd = C.settlement_breakdown(ev)
                bad.append((s.name, int(bd.get("total_cells") or 0), rev))
                continue
            nt = C.normalized_truth(ev)
            if nt["category"] == "normalized":
                norm.append((s.name, nt["raw_value"], nt["norm_value"], nt["longtail"]))
        except Exception as e:  # noqa: BLE001
            bad.append((s.name, -1, -1))
    print(f"体检 {total} 个 valid_aisha 4round 样本\n")
    print(f"① 坏真值(揭示>真值，应隔离): {len(bad)}")
    for nm, c, r in bad:
        print(f"   <<坏 {nm[:50]}  breakdown格={c} R4揭示={r}")
    print(f"\n①b 公开权威值≠结算真值(结算漏件，应隔离): {len(pubbad)}")
    for nm, mm in pubbad:
        ms = "; ".join(f"{m['kind']}:公{m['public']}≠真{m['truth']}" for m in mm)
        print(f"   <<坏 {nm[:50]}  {ms}")
    print(f"\n② 含长尾→normalized(真值=估计替换，勿入干净归档): {len(norm)}/{total}")
    for nm, raw, nv, tl in norm[:12]:
        ts = "; ".join(f"{t['name']}({t['raw']//1000}k→{t['replacement']//1000}k)" for t in tl)
        print(f"   {nm[:46]}  {raw//1000}k→{nv//1000}k | {ts}")
    if len(norm) > 12:
        print(f"   …另 {len(norm)-12} 个，详见 sample_classification.json")
    print(f"\n干净样本(clean, 真值原样): {total - len(bad) - len(norm)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
