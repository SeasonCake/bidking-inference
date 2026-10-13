# -*- coding: utf-8 -*-
"""【WIP/未验证】用 RankMap 表 Monte-Carlo 模拟每件藏品爆率 + 对经验样本验证。

动机(作者2026-06-25建议):用N次重复模拟,从【内存导出的爆率表】测出特定藏品(世界杯/球衣等)
的爆率分布,再和【我们记录的结算样本】对应核对,逼近真实每件爆率——尤其对【没有样本的新图56xx】。

数据来源(已核对):
- 内存"DropMap"= RankMap(排位图)格式表, 旧 data/raw/tables/RankMap.txt 是解码器(同格式)。
  2401/2501 内存(Jun25) vs 磁盘(Jun5) 逐列字节一致 → 客户端不动态改爆率, 静态表原样加载。
- RankMap 7列: col3=计数区间[[lo,hi,w]](跨图常量~16); col4=[[键,权重]] 键=物品类型(=item_id前3位
  101-110, +201-209/301 疑似更高价值档); col5=高档/头奖价值带; col6=[价值,权重...]粗价值曲线。

⚠️ 验证状态(诚实):单列模型都【对不上经验品质分布】——
  - col6 加权均值≈经验(2401: 13.5k vs 16.7k)但分布形状不同(col6堆在10k, 产生不了经验25%的低价q1) → 均值吻合是巧合, col6≠每件价值分布。
  - 纯 col6/价值驱动模型 q6 过产(预测18-25% vs 经验5%)。
  → 还缺【品质/类型维度】: col4 键=item_id[:3]=类型(101-110), 1xx/2xx/3xx 疑似价值档。
    正解需 col4(类型/档权重) × 该类型该档catalog物品 联合采样, 再对经验校验。未钉死前【勿信表算爆率/勿喂引擎】。

✅ 现在可信的每件爆率来源 = 有样本的图用 reconstruct_map_prior 的 item_empirical_weight(freq=该局含此件概率)。
   全目录补齐后 value_coverage 0.10→1.00, 经验路径已完整。本表模拟仅对【无样本新图】才需要, 且需先钉公式。

用法(诊断):
  python scripts/calibration/rankmap_drop_montecarlo.py --map 2401
"""
from __future__ import annotations
import argparse, base64, json, math, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_b64_table(name):
    raw = open(ROOT / "data" / "raw" / "tables" / name, "rb").read()
    txt = base64.b64decode(raw).decode("utf-8", "replace").replace("\r", "")
    return [l.split("\t") for l in txt.split("\n") if l.strip()]


def _catalog():
    items = json.load(open(ROOT / "data" / "processed" / "items.json", encoding="utf-8"))
    out = []
    for it in (items.values() if isinstance(items, dict) else items):
        if not isinstance(it, dict):
            continue
        v, q, iid = it.get("value"), it.get("quality"), it.get("item_id")
        if isinstance(v, (int, float)) and v > 0 and q:
            # item_id 前3位 = 类型类别(101-110), 与 RankMap col4 键对应
            typ = int(str(iid)[:3]) if str(iid)[:3].isdigit() else None
            out.append({"id": iid, "value": v, "quality": q, "type": typ, "name": it.get("name", "")})
    return out


def col6_value_weight(anchors, v):
    """col6 = [价值,权重...] 锚点, log-log 分段插值+外推。"""
    pts = [(anchors[i], anchors[i + 1]) for i in range(0, len(anchors), 2)]
    xs = [math.log10(p[0]) for p in pts]
    ys = [math.log10(max(p[1], 0.01)) for p in pts]
    lv = math.log10(max(v, 1))
    if lv <= xs[0]:
        k = (ys[1] - ys[0]) / (xs[1] - xs[0])
        return 10 ** (ys[0] + k * (lv - xs[0]))
    if lv >= xs[-1]:
        k = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
        return 10 ** (ys[-1] + k * (lv - xs[-1]))
    for i in range(len(xs) - 1):
        if xs[i] <= lv <= xs[i + 1]:
            t = (lv - xs[i]) / (xs[i + 1] - xs[i])
            return 10 ** (ys[i] + t * (ys[i + 1] - ys[i]))
    return 0.01


def simulate(map_id):
    rm = {r[0]: r for r in _load_b64_table("RankMap.txt")}
    if str(map_id) not in rm:
        # 新图(56xx)只在内存 dropmap_mem_0625.json
        mem = json.load(open(ROOT / "data" / "processed" / "dropmap_mem_0625.json", encoding="utf-8"))
        m = mem.get(str(map_id))
        if not m:
            print(f"map {map_id} 不在 RankMap.txt 也不在 dropmap_mem_0625.json")
            return 1
        col4, col6 = m["area_w"], m["col6"]
    else:
        r = rm[str(map_id)]
        col4, col6 = json.loads(r[4]), json.loads(r[6])
    cat = _catalog()
    # 当前最佳候选模型(仍未通过品质验证, 见模块docstring): col4 类型权重 × col6 价值边际, 桶内均匀。
    type_w = {k: w for k, w in col4}
    # 价值桶 = col6 锚点; 物品归最近锚点, 桶质量= col6 权重, 桶内按 col4 类型权重分配
    pts = [(col6[i], col6[i + 1]) for i in range(0, len(col6), 2)]
    tw6 = sum(p[1] for p in pts)
    buckets = defaultdict(list)
    for it in cat:
        bi = min(range(len(pts)), key=lambda i: abs(math.log10(pts[i][0]) - math.log10(it["value"])))
        buckets[bi].append(it)
    item_p = {}
    qd = defaultdict(float)
    for bi, grp in buckets.items():
        pb = pts[bi][1] / tw6
        twt = sum(type_w.get(it["type"], 0) or 1 for it in grp) or 1
        for it in grp:
            p = pb * (type_w.get(it["type"], 0) or 1) / twt
            item_p[it["id"]] = (p, it)
            qd["q1" if it["quality"] in (1, 2) else "q%d" % it["quality"]] += p
    print(f"map {map_id}: predicted quality% = " + str({k: round(100 * v, 1) for k, v in sorted(qd.items())}))
    print("  (对比经验请跑 reconstruct_map_prior; 本模型未验证)")
    top = sorted(item_p.values(), key=lambda x: -x[0])[:12]
    print("  top drop-prob items (未验证):")
    for p, it in top:
        print(f"    p={100*p:.3f}%  q{it['quality']} val={it['value']} {it['name'][:14]}")
    return 0


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", type=int, required=True)
    a = ap.parse_args(argv[1:])
    return simulate(a.map)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
