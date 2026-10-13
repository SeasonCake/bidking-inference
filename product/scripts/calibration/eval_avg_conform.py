"""评估(只测不改): 公开均格已知、未被全库透视硬锁时(=实战早轮),如何用均格把「总格/件数」
绑一致。用 live 导出 zip(fatbeans 烤进了总格、复现不了此条件)。三方案 vs 真值:
  current  : 引擎现状(几何总格 mid + 先验件数; 不符合均格)
  in_range : 用户方案 —— 几何区间[low,high]内挑「整数件×均格」最吻合的总格; 件数=round(grid/avg)
  count_x  : 我的方案 —— 件数(先验)为主, 总格=round(件数×均格)(可超几何区间)
真值 = settled inventory_cells / inventory_count。早轮 R1-R3。
"""
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aisha_calib as C  # noqa: E402

for _p in ("scripts", "hero_ref/src", "hero_ref/tools", "src"):
    sys.path.insert(0, str(C.ROOT / _p))
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402

HARD_NOTES = {"public_total_cells", "structured_ref_bridge_total_cells", "field_update_total_cells"}
EXP_DIRS = [C.ROOT / "data/logs/live/exports", C.ROOT / "data/samples/hero_ref/archive/exports/2026-06-20"]


def in_range_snap(low, high, avg, mid):
    best = None
    cguess = int(round((mid or low or 1) / avg))
    for c in range(max(1, cguess - 8), cguess + 9):
        g = int(round(c * avg))
        if low is not None and high is not None and not (low <= g <= high):
            continue
        score = abs(g - (mid or g))
        if best is None or score < best[0]:
            best = (score, g, c)
    if best is None:
        c = int(round((mid or low or 1) / avg))
        g = min(high, max(low, int(round(c * avg)))) if (low and high) else int(round(c * avg))
        return g, max(1, int(round(g / max(avg, 0.1))))
    return best[1], best[2]


# 收集艾莎导出 zip(去重 session)
seen = set()
zips = []
for d in EXP_DIRS:
    if not d.is_dir():
        continue
    for z in sorted(d.glob("*.zip")):
        zips.append(z)

rows = []
n_zip = 0
for z in zips:
    try:
        with zipfile.ZipFile(z) as zf:
            names = zf.namelist()
            binds = sorted(n for n in names if n.endswith("_bidding.json") and "round_snapshots" in n)
            setn = [n for n in names if n.endswith("_settled.json")]
            if not binds or not setn:
                continue
            first = json.loads(zf.read(binds[0]))
            if str(first.get("hero")) != "aisha":
                continue
            st = json.loads(zf.read(sorted(setn)[-1]))
            T_cells = int(st.get("inventory_cells") or 0)
            T_count = int(st.get("inventory_count") or 0)
            if T_cells <= 0 or T_count <= 0:
                continue
            sid = str(first.get("session_id") or z.name)
            if sid in seen:
                continue
            seen.add(sid)
            n_zip += 1
            for bn in binds[:3]:  # 早轮 R1-R3
                snap = json.loads(zf.read(bn))
                res = run_reference_engine(prepare_reference_engine_snapshot(snap), max_combos=4000).as_dict()
                evd = res.get("evidence") or {}
                avg = (evd.get("avg_cells") or {}).get("total")
                if not avg or avg <= 0:
                    continue
                notes = res.get("source_notes") or res.get("notes") or []
                if any(n in HARD_NOTES for n in notes):
                    continue
                g = (res.get("total_grid_range") or [None, None, None]) + [None, None, None]
                low, mid, high = g[0], g[1], g[2]
                count_est = res.get("total_count_estimate")
                if mid is None or count_est is None:
                    continue
                ir_g, ir_c = in_range_snap(low, high, float(avg), mid)
                rows.append((float(avg), T_cells, T_count, (mid, count_est), (ir_g, ir_c),
                             (int(round(count_est * avg)), count_est)))
    except Exception:
        pass

print(f"艾莎导出 {n_zip} 局; 有公开均格+未硬锁的早轮帧: {len(rows)}\n")
if not rows:
    print("无符合条件帧")
    sys.exit(0)


def report(name, idx):
    n = len(rows)
    g_mae = sum(abs(r[idx][0] - r[1]) for r in rows) / n
    c_mae = sum(abs(r[idx][1] - r[2]) for r in rows) / n
    conf = sum(abs(r[idx][0] - r[idx][1] * r[0]) for r in rows) / n
    g_bias = sum(r[idx][0] - r[1] for r in rows) / n
    c_bias = sum(r[idx][1] - r[2] for r in rows) / n
    print(f"{name:9s} | 总格 MAE={g_mae:5.1f} bias={g_bias:+5.1f} | 件数 MAE={c_mae:4.1f} bias={c_bias:+4.1f} | 符合度|g-c×avg|={conf:4.2f}")


report("current", 3)
report("in_range", 4)
report("count_x", 5)
print("\n(注: 真值是结算全格; 早轮总格本就该欠估[未揭示完], 所以 bias 负是正常的; 看 MAE 谁更小 + 符合度)")
