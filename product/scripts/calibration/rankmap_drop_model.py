# -*- coding: utf-8 -*-
"""【已验证】RankMap 前向生成模型 → 每件藏品真实掉率(字段对准版, 2026-06-25)。

背景: reconstruct 的 item_empirical_weight.freq = "仓含有率", 被 ×38件/仓 + 小样本夸大,
不是真掉率(作者纠偏)。真掉率必须从对准字段的表算。

字段对齐(用旧表罗塞塔 + 群友样本严谨验证, 经多轮纠正):
- 品质分布 = StaticData DropWeights[地图档](地图档=BidMap col7, 101-106; 值越大图越富)。
- 类内价值权重 = RankMap col6 [价值,权重...] log-log 插值(压低高价件)。
- ⚠️ col3【全局常量~16, 非整仓件数】(全103图相同); 真实整仓件数每图不同(经验~30-40), 用 vault_count 传入。
- **col4【不参与掉率】**(对抗验证 data/reports/col4_role_investigation.md): col4 类别集是【全局常量
  {101-110,201-209,301}】每图相同(只权重不同), 不是 per-map 门控; 且 201-301 类别零物品; 当权重用时
  类别误差62%、当featured时加权类别反被低估。→ col4 疑为 UI/主题/陈列元数据, 掉率模型【完全忽略它】。
- **可掉池 = 有标准类别(101-110)的物品**(item tags 含 101-110)。这是价值修复的真因(并非 col4):
  加此过滤价值比值中位 0.64→1.01。类别分布靠 catalog-count 自然涌现(MAE 1.5%)。
  注: 活动藏品是否可掉不能只由标准类别推断，需经过下面的显式生命周期资格层。
  真正无资格的是无标准类别且未被活动/常驻白名单接入的特殊件。

✅ 正确模型: **P(物品X) = DropWeights[档][品质X] × col6价值权重(价值X)**, 品质桶内归一(桶质量=DropWeights[档]),
   **可掉池 = 有101-110标准类别的物品**, 池内不再按类别加权。每仓期望含X件 = 整仓件数(经验~30-40,非col3) × P(X)。

✅ 验证(群友1900+局, 21图系统验证): **品质误差中位3.6%, 价值比值中位1.01(16/21在0.8-1.25)**。
   2401 品质26/34/24/10/5(经验26/36/24/9/5)。见 data/reports/drop_model_validation_0625.md。

⚠️ 局限/待办:
- 新排位56xx地图档=BidMap col7=402, 不在老 DropWeights(101-106) → 用 --quality-from 从56xx样本反推该档品质权重。
- 2026 夏日限定八件按作者确认的活动期全地图资格接入；活动结束由显式回退/退役提交关闭，
  不使用本机墙钟自动改变同一源码提交的生成结果。
- col6+均匀是近似(价值2501偏1.4x); 更精细需进一步标定。

用法:
  python scripts/calibration/rankmap_drop_model.py --map 2401 --top 20
  python scripts/calibration/rankmap_drop_model.py --map 5605 --quality-from <56xx聚合品质json>  # 新图
"""
from __future__ import annotations
import argparse, base64, json, math, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 2026-07-24 00:52:53（北京时间）实机画面显示剩余 993:07:07，
# 与 2026-07-23 10:00 开始、2026-09-03 10:00 结束的 42 天活动窗精确对齐。
# 时间只作为可审计生命周期记录；派生表生成必须由显式版本/开关控制，不能依赖机器时钟。
SUMMER_PARTY_2026_START_BEIJING = "2026-07-23T10:00:00+08:00"
SUMMER_PARTY_2026_END_BEIJING = "2026-09-03T10:00:00+08:00"
DRAGON_EGG_ID = 1006009
SUMMER_PARTY_2026_ITEM_IDS = frozenset(
    {
        1016008,
        1016009,
        1016010,
        1016011,
        1016012,
        1016013,
        1016014,
        1096011,
    }
)
RETIRED_WORLD_CUP_Q6_ITEM_IDS = frozenset(
    {
        1016007,
        1036006,
        1036007,
        1036008,
    }
)

# 品质权重(地图档 -> [q1..q6])。
# 真值来源: 游戏内不存可提取的权重表(内存搜整数串/float32/64 全0命中); 故用【有效样本实测】为权威
#   (data/processed/quality_weights_measured.json, 1000+局聚合, 比第三方StaticData更权威, 覆盖排位402档)。
# 第三方 StaticData(AuctionAnalyzer 反编译)作 fallback——已验证对经典档是好近似(104/101 与实测吻合)。
_STATICDATA = {
    "101": [300, 320, 300, 80, 20, 2], "102": [200, 300, 330, 150, 40, 8],
    "103": [130, 250, 300, 400, 100, 35], "104": [80, 180, 340, 240, 100, 50],
    "105": [80, 200, 240, 240, 120, 70], "106": [10, 100, 200, 240, 220, 200],
}


def _measured_buckets():
    """实测权威品质权重: 地图档 -> {q1,q3,q4,q5,q6}(占比, q1已含q2)。"""
    p = ROOT / "data/processed/quality_weights_measured.json"
    if p.exists():
        return json.load(open(p, encoding="utf-8")).get("by_tier", {})
    return {}


# 兼容旧调用: DROP_WEIGHTS[tier] 仍返回 6 元 StaticData(fallback 用)
DROP_WEIGHTS = _STATICDATA


def _load_b64(name):
    txt = base64.b64decode(open(ROOT / "data/raw/tables" / name, "rb").read()).decode("utf-8", "replace")
    return [l.split("\t") for l in txt.replace("\r", "").split("\n") if l.strip()]


def _col6_weight(anchors, v):
    pts = [(anchors[i], anchors[i + 1]) for i in range(0, len(anchors), 2)]
    xs = [math.log10(p[0]) for p in pts]
    ys = [math.log10(max(p[1], 0.01)) for p in pts]
    lv = math.log10(max(v, 1))
    if lv <= xs[0]:
        k = (ys[1] - ys[0]) / (xs[1] - xs[0]); return 10 ** (ys[0] + k * (lv - xs[0]))
    if lv >= xs[-1]:
        k = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2]); return 10 ** (ys[-1] + k * (lv - xs[-1]))
    for i in range(len(xs) - 1):
        if xs[i] <= lv <= xs[i + 1]:
            t = (lv - xs[i]) / (xs[i + 1] - xs[i]); return 10 ** (ys[i] + t * (ys[i + 1] - ys[i]))
    return 0.01


def _quality_weights_from_prior(prior_path):
    """从聚合先验 json(reconstruct per_quality)反推 5 桶品质权重(q2并入q1, 与引擎一致)。
    返回 6 元(q1..q6), q2=0(已并入q1)。用于老 DropWeights 没有的新地图档(如56xx的402)。"""
    pr = json.load(open(prior_path, encoding="utf-8"))
    pq = pr.get("per_quality", pr)
    g = {k: o["count"]["mean"] for k, o in pq.items()}
    tot = sum(g.values()) or 1
    return [g.get("q1", 0) / tot, 0.0, g.get("q3", 0) / tot,
            g.get("q4", 0) / tot, g.get("q5", 0) / tot, g.get("q6", 0) / tot]


def drop_rates(map_id, quality_from=None, vault_count=None):
    """返回 (rates: item_id->每抽概率, count: 每仓件数, iq, iv, nm)。
    模型: P(X) = DropWeights[档][品质X] × col6价值权重(价值X), 品质内归一, 全item均匀(不分类别)。"""
    item_path = ROOT / "data/raw/tables/Item.txt"
    if item_path.is_file():
        first = base64.b64decode(item_path.read_bytes()).decode("utf-8-sig").splitlines()[0]
        if len(first.split("\t")) == 45:
            raise ValueError(
                "76b7ca requires build_official_20261008_inputs.py; "
                "the legacy rate CLI cannot substitute its old support"
            )
    rm = {r[0]: r for r in _load_b64("RankMap.txt")}
    bm = {r[0]: r for r in _load_b64("BidMap.txt")}
    mid = str(map_id)
    # 价值带 col6 + 件数 col3: 老图在 RankMap.txt, 新图56xx在内存 dropmap_mem_0625.json
    if mid in rm:
        col6 = json.loads(rm[mid][6]); col3 = json.loads(rm[mid][3]); col5 = json.loads(rm[mid][5])
    else:
        mem = json.load(open(ROOT / "data/processed/dropmap_mem_0625.json", encoding="utf-8")).get(mid)
        if not mem:
            raise SystemExit(f"map {mid} 不在 RankMap.txt 也不在 dropmap_mem_0625.json。")
        col6 = mem["col6"]; col3 = mem["range39"]; col5 = mem["value_dist"]
    # 品质权重(优先级): 实测权威(quality_weights_measured)[地图档] > --quality-from > 第三方StaticData[档]。
    # bucket = 5 桶 {q1(含q2),q3,q4,q5,q6} 占比。地图档 = BidMap col7(经典101-106, 排位55xx=401/56xx=402)。
    tier = bm[mid][7] if mid in bm else None
    measured = _measured_buckets()
    if tier in measured:
        bucket = dict(measured[tier]); src = f"实测档{tier}"
    elif quality_from:
        d = _quality_weights_from_prior(quality_from)
        bucket = {"q1": d[0] + d[1], "q3": d[2], "q4": d[3], "q5": d[4], "q6": d[5]}; src = "quality_from"
    elif tier in _STATICDATA:
        d = _STATICDATA[tier]; s = sum(d)
        bucket = {"q1": (d[0] + d[1]) / s, "q3": d[2] / s, "q4": d[3] / s, "q5": d[4] / s, "q6": d[5] / s}
        src = f"StaticData档{tier}"
    else:
        # §作者"大致几个大地图做好爆率权重区分即可": 缺品质权重的图档(401排位/305/空档)用
        # 同性质邻近档回退 — 401(55xx排位)→402(56xx排位)、305→304、空/None→图族代表档。
        # 品质分布主要随图族/档变, 同族回退是可接受近似(作者认可家族粒度)。
        _fb = {"401": "402", "305": "304"}.get(tier) or {
            "21": "101", "22": "102", "23": "103", "24": "104", "25": "105",
            "26": "106", "44": "104", "45": "105", "55": "402", "56": "402",
        }.get(str(mid)[:2])
        if _fb in measured:
            bucket = dict(measured[_fb]); src = f"回退实测档{_fb}(原档{tier or '空'})"
        elif _fb in _STATICDATA:
            d = _STATICDATA[_fb]; s = sum(d)
            bucket = {"q1": (d[0] + d[1]) / s, "q3": d[2] / s, "q4": d[3] / s, "q5": d[4] / s, "q6": d[5] / s}
            src = f"回退StaticData档{_fb}(原档{tier or '空'})"
        else:
            raise SystemExit(f"map {mid} 地图档={tier} 无品质权重(实测/StaticData/回退都没有)。")
    items = json.load(open(ROOT / "data/processed/items.json", encoding="utf-8"))
    iq, iv, nm = {}, {}, {}
    for it in (items.values() if isinstance(items, dict) else items):
        if isinstance(it, dict) and it.get("quality") and it.get("value"):
            i = int(it["item_id"]); iq[i] = it["quality"]; iv[i] = it["value"]; nm[i] = it.get("name", "")
    # 基础可掉池 = 有【标准物品类别(101-110)】的物品；活动/常驻/退役差异由下方显式
    # 生命周期资格层覆盖。验证: 基础类别过滤后价值比值中位 0.64→1.01。
    # 注(对抗验证 col4_role_investigation.md): col4 类别集是【全局常量{101-110,201-301}】, 不是per-map门控;
    #   201-301 类别零物品。所以这里【不用 col4】, 直接按"有101-110类别"过滤。item→类别见 item_category_mem_0625.json。
    item2cat = {int(k): v for k, v in json.load(open(ROOT / "data/processed/item_category_mem_0625.json", encoding="utf-8")).items()}
    # value_dist(col5)价值带上限 = 本图价值天花板: 价值超过最高带的物品在本图【不掉】(approach2, workflow验证)。
    # 只对【56xx 排位/内存图】用: 它们的可掉池混进了大量经典/活动专属高价件(世界冠军奖杯720万等), 排位不掉 → 价值高估1.28x。
    # col5(内存权威价值带)上限=30万, 剔除带外件 → 56xx 价值比值 1.28→1.02、品质不变(5.1%), 9/10达标。
    # 经典图(RankMap.txt)【不用】此过滤: 它们的高价件是本图合法内容, 套上限反而欠估(1.04→0.87)。env 可回退。
    # §作者 2026-06-27 "先把所有【可掉落物品】的权重做好,之后再说每图筛选":
    #   - 价值天花板 v_ceiling(approach2)是之前为"向价值中心聚拢"设的【筛选阈值】(作者确认非游戏挡位):
    #     它把高价新藏品(磁悬浮云床1M、机甲3-8M)错误剔出排位(样本实测它们在56xx确实掉)。权重阶段【弃用】。
    #   - 每图 placement(机甲=排位专属、赛季藏品在哪些图…)是【后续单独阶段】,这里不做,先让全部可掉物拿到权重。
    #   可掉类别: 100 特殊藏品 / 101-110 标准大类 / 111 未知藏品箱(可与101-110多重交集)。
    #   非掉落(中型典藏箱 tag7 / 1306 / 远古机甲 1006008 / 道具)无这些类别, 天然排除。
    #   env RANKMAP_VALUE_CEILING=1 可临时恢复旧价值天花板筛选(对比用)。
    import os as _os
    _restore_ceiling = _os.environ.get("RANKMAP_VALUE_CEILING") == "1"
    v_ceiling = max((hi for _lo, hi, _w in col5), default=None) if (_restore_ceiling and mid not in rm and col5) else None
    # placement 与生命周期:
    # - category 100 机甲散件仍为排位专属(55xx/56xx)；
    # - 常驻龙蛋虽为纯 tag/category 100，但作者已确认其他地图（含沉船）也有玩家命中，
    #   因此默认全地图；命名回退可精确恢复原 category-100 排位臂；
    # - 夏日八件在官方活动窗内按作者决定全地图接入，活动结束由命名开关/后续退役提交关闭；
    # - 已退役世界杯四件默认退出新时期 weights，保留命名回退供历史 A/B。
    # 其余(1916赛季/未知箱/经典)
    # =全地图(贵件在便宜图自然被 col6 权重压到近 0, 软筛即够, 无需硬价值天花板)。赛季藏品识别字段=items.json `_src=mem_0625`+id前缀。
    is_ranked = str(mid).startswith("55") or str(mid).startswith("56")
    _no_placement = _os.environ.get("RANKMAP_NO_PLACEMENT") == "1"
    _disable_dragon_all_maps = (
        _os.environ.get("BIDKING_DISABLE_DRAGON_EGG_ALL_MAPS") == "1"
    )
    _disable_summer_party = (
        _os.environ.get("BIDKING_DISABLE_SUMMER_PARTY_2026_COLLECTIBLES") == "1"
    )
    _enable_retired_world_cup = (
        _os.environ.get("BIDKING_ENABLE_RETIRED_WORLD_CUP_Q6_COLLECTIBLES") == "1"
    )

    def _eligible(i):
        c = item2cat.get(i) or 0
        if i in RETIRED_WORLD_CUP_Q6_ITEM_IDS and not _enable_retired_world_cup:
            return False
        if i == DRAGON_EGG_ID:
            if c != 100:
                return False
            if _disable_dragon_all_maps:
                return _no_placement or is_ranked
            return v_ceiling is None or iv[i] <= v_ceiling
        if i in SUMMER_PARTY_2026_ITEM_IDS:
            if _disable_summer_party or not (101 <= c <= 110):
                return False
            return v_ceiling is None or iv[i] <= v_ceiling
        if c == 100:  # 机甲散件等其他 category-100 特殊藏品: 排位专属
            return _no_placement or is_ranked
        if not ((101 <= c <= 110) or c == 111):
            return False
        return v_ceiling is None or iv[i] <= v_ceiling

    eligible = {i for i in iq if _eligible(i)}
    # ⚠️ col3 经核实是【全局常量】(~16, 全103图相同), 【不是整仓件数】。真实整仓件数每图不同(经验~30-40,
    # 约 col3 的 2.3x), 需从样本经验取(vault_count 传入)。col3 真实含义待定(疑每区/每轮基数)。
    count = vault_count if vault_count else sum((lo + hi) / 2 * w for lo, hi, w in col3) / sum(w for _, _, w in col3)
    # 品质桶(q2并入q1, 与引擎/reconstruct一致); 桶质量 = 该档品质占比
    bucket_mass = bucket
    byq = defaultdict(list)
    for i in eligible:
        if 1 <= iq[i] <= 6:
            byq["q1" if iq[i] in (1, 2) else "q%d" % iq[i]].append(i)
    # 品质桶内按 col6 价值权重归一
    P = {}
    for qk, its in byq.items():
        ws = [_col6_weight(col6, iv[i]) for i in its]; sw = sum(ws) or 1
        for i, wt in zip(its, ws):
            P[i] = bucket_mass.get(qk, 0) * wt / sw
    Z = sum(P.values()) or 1
    rates = {i: p / Z for i, p in P.items()}
    return rates, count, iq, iv, nm


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", type=int, required=True)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--quality-from", default=None, help="新地图档(如56xx的402)的品质权重来源: reconstruct聚合先验json")
    ap.add_argument("--vault-count", type=float, default=None, help="整仓真实件数(经验, ~30-40); 不传则用col3(=全局常量16, 偏小)")
    a = ap.parse_args(argv[1:])
    rates, count, iq, iv, nm = drop_rates(a.map, quality_from=a.quality_from, vault_count=a.vault_count)
    # 长尾归一化(作者口径): 同(品质,形状)value>=5x floor 的长尾红/金, 价值替成同组非长尾均值,
    # 避免极稀有长尾(豪宅黑盒7.4M/世界冠军奖杯7.2M)撑高【期望价值】。掉率概率 p 仍用原值(col6, 真机制不动)。
    try:
        import aisha_calib as _ac
        norm = lambda i: _ac.normalized_item_value(i) or iv[i]
    except Exception:
        norm = lambda i: iv[i]
    rows = sorted(rates.items(), key=lambda kv: -kv[1])
    out = ROOT / "data" / "reports" / f"drop_rates_model_{a.map}.tsv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        f.write("item_id\tname\tquality\tvalue\tvalue_norm\tp_per_draw\texpected_per_vault\n")
        for iid, p in rows:
            f.write(f"{iid}\t{nm.get(iid,'')}\t{iq[iid]}\t{iv[iid]}\t{norm(iid)}\t{p:.6f}\t{p*count:.4f}\n")
    print(f"map {a.map}: 每仓~{count:.0f}件, {len(rows)}件可掉. 真掉率表 -> {out}")
    for iid, p in rows[: a.top]:
        print(f"  p/draw={p*100:.3f}%  期望/仓={p*count:.3f}  q{iq[iid]} val={iv[iid]} id={iid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
