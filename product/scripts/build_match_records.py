# -*- coding: utf-8 -*-
"""铺路·数据层:把 sessions.jsonl 的结算帧整理成「对局记录表」(给用户界面离线读)。

用户界面(独立窗 或 离线 HTML)读这份 JSON 渲染表格 + 导出样本,不依赖运行中的客户端。
对齐截图1的列:时间/回合/地图/赢家/道具/拍品价值/估价/各家出价/赢家出价/展示盈亏。

「我的出价/我的盈亏/是否我拍下」: monitor 已在 artifact 标 self_bid/self_name/winner_is_self
(uid 来自 fatbeans 推断的 local_player_id),本工具直接消费。老样本未带这些字段时 my_* 为 None。

用法:
  python scripts/build_match_records.py --sessions data/logs/live/sessions.jsonl --out data/logs/live/match_records.json
"""
from __future__ import annotations
import argparse, json, math, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 地图族(顶部筛选 tab): 按 map_id 前2位归族 —— 覆盖全部 tier + 活动图变体, 与引擎 family-by-prefix
# 口径一致。注意 family 只表示【题材/底图】；普通/排位/活动、明拍/暗拍是正交维度，不能再
# 塞回 family。核对 maps.json(165 张)编号规律:
#   21快递 22仓库 23集装箱 24别墅 25沉船 26拍卖会(隐秘拍卖会) | 31-35 活动(对应基础类型) |
#   44别墅 45沉船 | 55/56=排位赛(cat401/402, 独立模式)。"沉船"=游戏 cat105/305(旧称残骸, 名多为
#   远洋客轮/军用舰艇/冷链货船…)。
_FAMILY_BY_PREFIX2 = {
    "21": "快递", "31": "快递",
    "22": "仓库", "32": "仓库",
    "23": "集装箱", "33": "集装箱",
    "24": "别墅", "34": "别墅", "44": "别墅",
    "25": "沉船", "35": "沉船", "45": "沉船",
    # 55/56 = 排位赛(cat401/402, tier402), 是独立游戏模式; 虽名多为舰艇/货轮(残骸主题)但
    # 不归入 沉船 族, 单列"排位"(否则 沉船 筛选会混入排位局)。作者确认 2026-06-26。
    "55": "排位", "56": "排位",
    "26": "拍卖会",
    "60": "竞拍大厅",
}
# category 回退映射(老数据无标准 map_id 时)。201 活动跨多类型, 由 map_id 前缀决定, 不在此固定。
_FAMILY_BY_CATEGORY = {
    101: "快递", 102: "仓库", 103: "集装箱", 104: "别墅", 105: "沉船", 106: "拍卖会",
    304: "别墅", 305: "沉船", 401: "排位", 402: "排位", 601: "竞拍大厅",
}


def _family_from_id(map_id) -> str:
    """map_id 前2位 -> 族(覆盖基础图/活动图/全 tier 变体)。"""
    return _FAMILY_BY_PREFIX2.get(str(map_id or "")[:2], "")


def _family_from(name: str, category, map_id=None) -> str:
    # 优先 map_id 前缀(最稳, 覆盖活动图); 再 category; 最后名称关键词。
    fam = _family_from_id(map_id) or _FAMILY_BY_CATEGORY.get(category)
    if fam:
        return fam
    for kw, f in (("快递", "快递"), ("集装箱", "集装箱"), ("拍卖", "拍卖会"), ("别墅", "别墅"),
                  ("残骸", "沉船"), ("货船", "沉船"), ("舰艇", "沉船"), ("客轮", "沉船"),
                  ("仓库", "仓库")):
        if kw in (name or ""):
            return f
    return "其他"


# 门票(入场费,银两)按族回退:活动图(2531/45xx)不在 maps.json 时用(表内图直接读 entry_fee_silver)。
# 与 maps.json 核对:快递0/仓库2000/集装箱5000/别墅1w/沉船2.5w/拍卖会10w。
_FEE_BY_FAMILY = {"快递": 0, "仓库": 2000, "集装箱": 5000, "别墅": 10000,
                  "沉船": 25000, "拍卖会": 100000,
                  # 排位赛(56xx)门票=1.5w(作者实测 2026-06-26)。必须在此显式给:5601-5610 maps.json
                  # 写的是 25000(陈旧/沿用沉船), 新图 5611+ 不在 maps.json 会回退 0(免门票)——都不对, 统一 15000。
                  "排位": 15000, "竞拍大厅": 25000}

# 亏损分红率:赢家拍下亏损时, 每位玩家(包括拿仓者本人)都收赢家亏损的 10%。
# 作者 2026-08-02 再次确认该规则适用于所有地图；未拿仓的本机玩家优先消费结算帧 f5
# 直接值，拿仓者本人及旧记录按本规则计算。计算值统一四舍五入到游戏展示的整数银两。
_LOSS_SHARE_RATE = 0.10


def _number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _loss_share_units(winner_bid, lot_value) -> int | None:
    """Return the confirmed per-player 10% loss dividend in displayed silver units."""
    if not (_number(winner_bid) and _number(lot_value)):
        return None
    raw = _LOSS_SHARE_RATE * max(0.0, float(winner_bid) - float(lot_value))
    return int(math.floor(raw + 0.5))


def _rank_observation(rank_summary) -> dict | None:
    """Project one match's already-selected local rank observation.

    The monitor owns slot-8 selection.  Records/UI must not rescan ``changes`` because
    slot 7 is a different score system and can occasionally share the same ``before``.
    """
    if type(rank_summary) is not dict:
        return None
    before = rank_summary.get("self_before")
    delta = rank_summary.get("self_delta")
    after = rank_summary.get("self_after")
    if _number(before) and _number(delta) and _number(after):
        before_f, delta_f, after_f = float(before), float(delta), float(after)
        if abs((before_f + delta_f) - after_f) < 0.5:
            return {
                "before": before_f,
                "delta": delta_f,
                "after": after_f,
                "latest": after_f,
                "tier": rank_summary.get("self_rank_tier"),
                "slot": 8,
                "source": "settlement_self_slot8",
            }
    points = rank_summary.get("self_rank_points")
    if _number(points):
        points_f = float(points)
        return {
            "before": points_f,
            "delta": None,
            "after": None,
            "latest": points_f,
            "tier": rank_summary.get("self_rank_tier"),
            "slot": None,
            "source": "bidding_self_points",
        }
    return None


def _mark_rank_observation_gaps(records: list[dict]) -> None:
    """Mark unobserved ranked intervals without inventing a cross-match delta."""
    previous_latest = None
    for record in reversed(records):  # oldest -> newest
        observation = record.get("rank_observation")
        if type(observation) is not dict:
            continue
        before = observation.get("before")
        if _number(previous_latest) and _number(before) and abs(float(before) - float(previous_latest)) >= 0.5:
            observation.update(
                gap_from_previous=True,
                previous_observed_rank=float(previous_latest),
                current_observed_before=float(before),
            )
        latest = observation.get("latest")
        if _number(latest):
            previous_latest = float(latest)


# 排位赛地图名(56xx)覆盖。游戏 06-18 YooAsset 改版后排位图改名,但新名【不在】6.25 内存抽取的
# BidMap/DropMap 里(bid_map_N_56xx 不存在), maps.json(改版前)给的是错配的【沉船名】(5607 抄了 2507=皇家御用货舱)。
# 作者按游戏内排位顺序手报覆盖(2026-06-26):5601=百物杂拍 是排位"默认/混合"首图(同别墅 2401=未知别墅、
# 沉船 2501=未知残骸 的"首位=未知/默认"规律),5602-5611=10 个主题按序。5611=翰墨典藏 maps.json 无、在此补。
_RANK_MAP_NAMES = {
    5601: "百物杂拍", 5602: "居家陈设", 5603: "秘药器械", 5604: "奢品风尚",
    5605: "军械私拍", 5606: "珍宝原石", 5607: "古物珍玩", 5608: "尖端数码",
    5609: "载具机械", 5610: "珍馐食材", 5611: "翰墨典藏",
}

# 沉船【活动】图: 25xx/45xx 的连续十图块按位置复用 2501-2510 的名称。
# …31–…40 是 2026-08-13 复用的“活动沉船”数量 UP cohort；2026-07-16
# 的 …41–…50 是红箱活动。二者都必须归入沉船族，但名称标签分开，避免统计/
# 对局记录把不同机制混成同一 cohort。
_SUNKEN_BASE_NAMES = (
    "未知残骸", "远洋客轮舱房", "军用舰艇保险库", "冷链货船隔离舱", "殖民商船宝库",
    "探险家座舰资料库", "皇家御用货舱", "生物实验室样本库", "私掠船军火舱", "现代货轮娱乐库",
)
_SUNKEN_ACTIVITY_BLOCKS = (
    (21, "近期活动"),
    (31, "活动沉船"),
    (41, "红箱活动"),
)
_SUNKEN_PENDING_ACTIVITY_SUFFIX_START = 51
_SUNKEN_PENDING_ACTIVITY_SUFFIX_END = 99

# 公共统计的地图玩法元数据。活动批次不能靠地图名字猜；这里只登记已经由表/作者事实确认的
# 生命周期。作者 2026-08-27 实机与 current-generation 结算已确认 41–50 红箱活动开放；
# ActivityMap 仍保留 31–40 路由，因此 current 生命周期不是从静态 ActivityMap 外推所得。
# 未来尚未登记、但落在活动编号域的地图继续稳定显示为“活动待确认”。
_ACTIVITY_COHORTS = (
    (11, 20, "早期活动", "historical"),
    # 2026-08-13 更新的 ActivityMap 10003/10103：2531–2540 是明拍、
    # 4531–4540 是暗拍，两臂均为 ui_activity_desc3（藏品数量概率提升）。
    # 玩法维度仍由 25xx/45xx 真源分别判 open/sealed；这里只负责活动身份与
    # 生命周期，不能把两臂混成一种拍卖方式。
    (21, 30, "近期活动", "historical"),
    (31, 40, "活动沉船", "historical"),
    (41, 50, "红箱活动", "current"),
)
_MAP_KIND_LABELS = {
    "regular": "普通图",
    "ranked": "排位",
    "activity": "活动图",
    "auction_hall": "竞拍大厅",
}
_AUCTION_MODE_LABELS = {
    "open": "明拍",
    "sealed": "暗拍",
    "training": "训练",
    "auction_hall": "竞拍大厅",
    "unknown": "方式待确认",
}
_ACTIVITY_STATUS_LABELS = {
    "none": "非活动",
    "historical": "往期活动",
    "current": "当前活动",
    "pending": "活动待确认",
}
_WAREHOUSE_POLICY_LABELS = {
    "official_only": "仅官方随机仓",
    "player_possible": "可能含玩家仓",
    "unknown": "来源待确认",
}


def _map_profile(map_id, *, category=None, auction_mode=None) -> dict:
    """Return orthogonal, privacy-safe gameplay metadata for one map.

    `family` answers “what base map theme is this”; this function answers four
    different questions: regular/ranked/activity, open/sealed/training, activity
    lifecycle, and whether a player-created warehouse is possible.  Keeping these
    dimensions separate prevents ranked/sealed/activity rows from inheriting the
    post-update open-map “source mixed” fallback.
    """
    try:
        mid = int(map_id)
    except (TypeError, ValueError):
        mid = 0
    prefix = mid // 100 if 1000 <= mid <= 9999 else 0
    suffix = mid % 100 if prefix else -1

    if prefix == 60 or category == 601:
        kind = "auction_hall"
    elif prefix in {55, 56} or category in {401, 402}:
        kind = "ranked"
    elif category == 201 or prefix in {31, 32, 33, 34, 35}:
        kind = "activity"
    elif prefix in {25, 45} and suffix >= 11:
        kind = "activity"
    else:
        kind = "regular"

    mode = (
        auction_mode
        if auction_mode in {"open", "sealed", "training", "auction_hall"}
        else None
    )
    if mode is None:
        if prefix == 60:
            mode = "auction_hall"
        elif prefix in {21, 22, 23, 24, 25, 26}:
            mode = "open"
        elif prefix in {31, 32, 33, 34, 35}:
            mode = "training"
        elif prefix in {44, 45, 55, 56}:
            mode = "sealed"
        else:
            mode = "unknown"

    activity_status = "none"
    activity_cohort = ""
    if kind == "activity":
        if category == 201 or prefix in {31, 32, 33, 34, 35}:
            activity_status = "historical"
            activity_cohort = "旧活动训练图"
        else:
            activity_status = "pending"
            activity_cohort = "未登记活动批次"
            for start, end, label, status in _ACTIVITY_COHORTS:
                if start <= suffix <= end:
                    activity_status = status
                    activity_cohort = label
                    break

    # 作者确认：排位、可信暗拍、已登记的往期/当前活动都没有玩家仓。未来未登记活动
    # 只在“暗拍”臂可据玩法规则判 official-only；未知明拍活动继续 fail-closed。
    if (
        kind == "ranked"
        or mode in {"sealed", "training"}
        or (kind == "activity" and activity_status in {"historical", "current"})
    ):
        warehouse_policy = "official_only"
    elif kind == "regular" and mode == "open":
        warehouse_policy = "player_possible"
    else:
        warehouse_policy = "unknown"

    return {
        "map_kind": kind,
        "map_kind_label": _MAP_KIND_LABELS[kind],
        "auction_mode": mode,
        "auction_mode_label": _AUCTION_MODE_LABELS[mode],
        "activity_status": activity_status,
        "activity_status_label": _ACTIVITY_STATUS_LABELS[activity_status],
        "activity_cohort": activity_cohort,
        "warehouse_policy": warehouse_policy,
        "warehouse_policy_label": _WAREHOUSE_POLICY_LABELS[warehouse_policy],
    }


def _load_map_meta() -> dict:
    """map_id -> map name/family/fee plus orthogonal gameplay classification.

    排位图 56xx 名用 _RANK_MAP_NAMES 覆盖(maps.json 改版前给的是错配沉船名)。"""
    p = ROOT / "data" / "processed" / "maps.json"
    if not p.exists():
        return {}
    try:
        out = {}
        for m in json.loads(p.read_text(encoding="utf-8")):
            mid = int(m["map_id"])
            nm = _RANK_MAP_NAMES.get(mid) or m.get("name", "")          # 56xx 排位名覆盖错配的沉船名
            nm = nm.replace("​", "").strip()                        # 去零宽空格(maps.json 沉船名带)
            # 活动沉船图号 X511-X520(2511-2520 / 4511-4520): maps.json 里已有真名但无"(活动)"后缀。
            # census(2026-07-06)证实这批是活动专属图(0702前零样本·结算红率12% vs 常规7%)→ 补后缀。
            if (2511 <= mid <= 2520 or 4511 <= mid <= 4520) and "(活动)" not in nm:
                nm = nm + "(活动)"
            profile = _map_profile(
                mid,
                category=m.get("category"),
                auction_mode=m.get("auction_mode"),
            )
            # 已登记的活动批次同样可能已经存在于 maps.json。名称也必须携带活动身份，
            # 让地图统计、红榜和“我的记录”复用同一 _load_map_meta 时不会显示成普通图。
            if profile["activity_cohort"] == "近期活动" and "近期活动" not in nm:
                nm = nm + "(近期活动)"
            if profile["activity_cohort"] in {"活动沉船", "红箱活动"}:
                cohort_suffix = f"({profile['activity_cohort']})"
                if cohort_suffix not in nm:
                    nm = nm + cohort_suffix
            out[mid] = {
                "name": nm,
                "family": _family_from(nm, m.get("category"), mid),
                "entry_fee": m.get("entry_fee_silver"),
                **profile,
            }
        for mid, nm in _RANK_MAP_NAMES.items():                         # maps.json 没有的排位图(如 5611)补进来
            if mid not in out:
                out[mid] = {
                    "name": nm,
                    "family": "排位",
                    "entry_fee": None,
                    **_map_profile(mid),
                }
        for base in (2500, 4500):
            for block_start, activity_label in _SUNKEN_ACTIVITY_BLOCKS:
                for offset, nm in enumerate(_SUNKEN_BASE_NAMES):
                    mid = base + block_start + offset
                    if mid not in out:
                        out[mid] = {
                            "name": f"{nm}({activity_label})",
                            "family": "沉船",
                            "entry_fee": None,
                            **_map_profile(mid),
                        }
            # The existing activity authority defines 25xx/45xx suffix >=11 as
            # the shipwreck activity subdomain.  Known 11/21/31/41 blocks above
            # keep their historical labels.  The remaining finite continuation
            # gets a human-readable pending label derived from the same ten-cabin
            # cycle, so public and private server views never fall back to a bare
            # raw id for a newly observed activity map.
            for suffix in range(
                _SUNKEN_PENDING_ACTIVITY_SUFFIX_START,
                _SUNKEN_PENDING_ACTIVITY_SUFFIX_END + 1,
            ):
                mid = base + suffix
                if mid not in out:
                    nm = _SUNKEN_BASE_NAMES[(mid - 1) % len(_SUNKEN_BASE_NAMES)]
                    out[mid] = {
                        "name": f"{nm}(活动沉船·待确认)",
                        "family": "沉船",
                        "entry_fee": None,
                        **_map_profile(mid),
                    }
        return out
    except Exception:  # noqa: BLE001
        return {}


_Q_NAMES = {1: "白", 2: "绿", 3: "蓝", 4: "紫", 5: "金", 6: "红"}

# 6 位英雄基础技能 id(在 100xxx 道具号段, 但属英雄技能非道具; 与 monitor._skill_reveal_label 一致)。
_HERO_BASE_SKILLS = {100107, 100108, 100204, 100209, 100301}
_BATTLE_ITEMS: dict | None = None


def _battle_items() -> dict:
    """道具 id -> 名(battle_items.json, 缓存)。"""
    global _BATTLE_ITEMS
    if _BATTLE_ITEMS is None:
        _BATTLE_ITEMS = {}
        try:
            for x in json.loads((ROOT / "data" / "processed" / "battle_items.json").read_text(encoding="utf-8")):
                bid = x.get("battle_item_id")
                if isinstance(bid, int) and x.get("name"):
                    _BATTLE_ITEMS[bid] = str(x["name"])
        except Exception:  # noqa: BLE001
            _BATTLE_ITEMS = {}
    return _BATTLE_ITEMS


def categorize_skill(skill_id, tool: str, hero: str) -> tuple[str, str]:
    """(类别, 名) —— 英雄技能映射优先(monitor 已解析的 tool 名), 剩下查道具表。
    类别 ∈ {道具, 英雄技能}。已是「道具·名」的 tool 直接归道具。"""
    t = str(tool or "").strip()
    if t.startswith("道具·"):
        return "道具", t[3:]
    its = _battle_items()
    if isinstance(skill_id, int) and skill_id in its and skill_id not in _HERO_BASE_SKILLS:
        return "道具", its[skill_id]
    name = re.sub(r"^.*?R\d\s*", "", t) or t           # 去「英雄·Rn 」前缀
    if not name or name.startswith("技能 "):
        name = f"{hero or '英雄'}技能"
    return "英雄技能", name


def paid_items(artifact: dict) -> list[str]:
    """玩家用的【付费道具】名列表(来自 action_result_rows, action_id 即 battle_item_id)。
    付费道具(极品扫描/随机抽检/宝光四鉴…)记在 action_result_rows, **不在 skill_reveal_rows**
    (后者是英雄技能揭示)。100107/100108 既是道具又是英雄基础技能, 但游戏把英雄技能放 skill_reveal、
    付费道具用记 action_result, 通道天然区分(实测 sophie 不用极品扫描、aisha 才用)。"""
    bi = _battle_items()
    out: list[str] = []
    for r in artifact.get("action_result_rows") or ():
        if not isinstance(r, dict):
            continue
        aid = r.get("action_id")
        nm = bi.get(aid) if isinstance(aid, int) else None
        if not nm:                                   # 不在道具表 -> 回退 monitor 已解析名
            nm = str(r.get("tool") or "").strip() or None
        if nm and nm not in out:
            out.append(nm)
    return out


_ITEM_CATALOG: dict | None = None


def _item_catalog() -> dict:
    """藏品 item_id -> {'name','w','h'}(items.json, 缓存)。供小地图为【采样时目录缺、现已补】的藏品
    补名/补形/补位(见 _trim_minimap 的 C 恢复)。服务器无 items.json 时返回空(看板不画小地图, 无影响)。"""
    global _ITEM_CATALOG
    if _ITEM_CATALOG is None:
        _ITEM_CATALOG = {}
        try:
            p = ROOT / "data" / "processed" / "items.json"
            for x in json.loads(p.read_text(encoding="utf-8")):
                iid = x.get("item_id")
                if isinstance(iid, int):
                    _ITEM_CATALOG[iid] = {"name": x.get("name") or "",
                                          "w": x.get("shape_w"), "h": x.get("shape_h")}
        except Exception:  # noqa: BLE001
            _ITEM_CATALOG = {}
    return _ITEM_CATALOG


def _trim_minimap(artifact: dict) -> dict:
    """结算小地图:从 settled 帧 minimap_grid_items 裁剪渲染必需字段(紧凑键)。
    游戏网格 col 1-10、row 1-based、左上锚 footprint(width×height)、quality 1-6。
    另回收【锚点-only 品质件】(揭示行里只有 local_index、无 row/col 的, 与 live overlay
    snapshot._ui_minimap_quality_markers 对齐): 按 local_index 解码位置画成位置近似【标记】(m=True),
    §5 别静默丢/别让两条小地图路径不一致。"""
    items = artifact.get("minimap_grid_items") or []
    cells = []
    max_row = 0
    drawn_li: set[int] = set()
    for it in items:
        r, c = it.get("row"), it.get("col")
        li = it.get("local_index")
        if isinstance(li, int):
            drawn_li.add(li)
        if not r or not c:
            # C 恢复: 采样时目录缺该藏品 -> 冻结无位置/名/形(如 6.26 才补的填充件在更早局)。现目录已补
            # -> 按 local_index 解码位置(同 monitor: row=li//10+1)+ 目录补形/名, 全量画出(非丢弃, §5)。
            cat = _item_catalog().get(it.get("item_id")) if isinstance(li, int) else None
            if cat and cat.get("w") and cat.get("h"):
                r, c = li // 10 + 1, li % 10 + 1
                w, h = int(cat["w"]), int(cat["h"])
                max_row = max(max_row, r + h - 1)
                cells.append({
                    "r": r, "c": c, "w": w, "h": h, "q": it.get("quality"),
                    "n": it.get("item_name") or cat.get("name") or "",
                    "cells": it.get("cells"), "v": it.get("value"),
                })
            continue  # 无 row/col: 能恢复的上面已加; 不能恢复的留给下面 reveal-row 锚点标记
        w = int(it.get("width") or 1); h = int(it.get("height") or 1)
        max_row = max(max_row, int(r) + h - 1)
        cells.append({
            "r": int(r), "c": int(c), "w": w, "h": h,
            "q": it.get("quality"),
            "n": it.get("item_name") or "",
            "cells": it.get("cells"),
            "v": it.get("value"),
        })
    # 锚点-only 品质件: 揭示行(包裹/公共信息/技能)的 revealed_items_detail 里只有 local_index 的品质件,
    # minimap_grid_items 没收。按 local_index 解码(row=li//10+1, col=li%10+1, 同 monitor/overlay)画成标记。
    seen: set[tuple] = set()
    for key in ("action_result_rows", "public_info_rows", "skill_reveal_rows"):
        for row in artifact.get(key) or []:
            if not isinstance(row, dict):
                continue
            det = row.get("revealed_items_detail")
            if not isinstance(det, list):
                continue
            for d in det:
                if not isinstance(d, dict):
                    continue
                li, q, v = d.get("local_index"), d.get("quality"), d.get("value")
                if not isinstance(li, int) or li in drawn_li:
                    continue
                if q is None and v is None:
                    continue                                  # 既无品质又无价值=无可显信息, 跳
                mk = (li, q, v)
                if mk in seen:
                    continue
                seen.add(mk)
                rr, cc = li // 10 + 1, li % 10 + 1
                max_row = max(max_row, rr)
                cells.append({
                    "r": rr, "c": cc, "w": 1, "h": 1, "m": True,   # m=锚点-only 标记(位置近似)
                    "q": q, "n": d.get("item_name") or d.get("name") or "",
                    "cells": d.get("cells"), "v": v,
                })
    return {"cells": cells, "rows": max_row, "cols": 10}


def _skills_summary(artifact: dict) -> str:
    """道具/技能逐轮摘要。英雄技能按轮归并 →「R1 白品轮廓 → R2 绿品轮廓 → …」;付费道具单列
    「｜道具: …」。用 categorize_skill 解析名(旧局的「技能 NNN」也解析/分类), 不再混在一起。"""
    pre = artifact.get("round_skill_summary")
    if pre:
        return pre
    hero = artifact.get("hero") or ""
    by_round: dict[int, list] = {}
    items: list[str] = list(paid_items(artifact))   # 付费道具←action_result_rows(英雄技能←下方 skill_reveal)
    for r in artifact.get("skill_reveal_rows") or []:
        tool = str(r.get("tool") or "").strip()
        if not tool:
            continue
        cat, name = categorize_skill(r.get("skill_id"), tool, hero)
        if cat == "道具":
            if name not in items:
                items.append(name)
            continue
        m = re.search(r"R(\d)", tool)
        rd = int(m.group(1)) if m else 0
        by_round.setdefault(rd, [])
        if name not in by_round[rd]:
            by_round[rd].append(name)
    parts = [f"R{rd} {' '.join(by_round[rd])}" if rd else " ".join(by_round[rd])
             for rd in sorted(by_round)]
    out = " → ".join(parts)
    if items:
        out = (out + " ｜ 道具: " + " ".join(items)) if out else ("道具: " + " ".join(items))
    return out


def build_records(sessions_path: Path, self_id: int | None) -> list[dict]:
    map_meta = _load_map_meta()
    lines = sessions_path.read_text(encoding="utf-8").splitlines()
    # 第一遍: 每 session 收「结算前最后一个 bidding 帧的估价(balanced=原始价值 P50)」。
    # settled 帧本身没有 balanced(结算用真值), 必须从 bidding 帧配对。sessions.jsonl 按
    # 时间追加 -> last-wins = 最深轮 bidding 帧, 信息最全。老样本无 balanced 字段 -> 不收。
    est_by_session: dict = {}
    self_name_global: str | None = None     # 本机昵称(老样本回填「我的出价」用): 任一帧带 self_name 即可
    for ln in lines:
        if not ln.strip():
            continue
        try:
            a = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        if self_name_global is None and a.get("self_name"):
            self_name_global = a.get("self_name")
        if a.get("phase") != "bidding":
            continue
        bal = a.get("balanced")
        sid = a.get("session_id") or a.get("session_hash")
        if bal is not None and sid is not None:
            est_by_session[sid] = bal
    # 第二遍: settled 帧出一条记录, 配对估价。
    seen, records = set(), []
    for ln in lines:
        if not ln.strip():
            continue
        try:
            a = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        if a.get("phase") not in (None, "settled"):
            continue
        sid = a.get("session_id") or a.get("session_hash")
        if sid in seen:
            continue
        value = a.get("known_value_sum")
        # 异常样本不收录: 抓包不完整、目录价值未闭合或无结算价值 -> 跳过, 且不占用 sid
        # (目前每次抓包不保证结算帧完整, 宁缺毋滥; 同 session 后续完整帧仍可被收录)。
        if (
            a.get("settlement_incomplete")
            or a.get("catalog_value_complete") is False
            or not value
            or value <= 0
        ):
            continue
        seen.add(sid)
        bids = a.get("latest_bids") or {}
        bids = {k: int(v) for k, v in bids.items() if isinstance(v, (int, float))}
        winner_name, winner_bid = (max(bids.items(), key=lambda x: x[1]) if bids else (None, None))
        # 估价: 优先用「结算前最后一个 bidding 帧」的估价(=用户出价时实际看到的),
        # settled 帧自身重估(若有)只作回退——结算态重估用户从未据此决策。
        est = est_by_session.get(sid)
        if est is None:
            est = a.get("balanced")
        # 我的出价/盈亏/是否我拍下: monitor 已在 artifact 标 self_bid(uid 解析)。
        # winner_is_self 为 False = 我出价但没拍下; None = 老样本未带(未知)。
        my_bid = a.get("self_bid")
        won_by_me = a.get("winner_is_self")
        # 老样本回填: 那时 local_player_id 未解析 -> self_bid/winner_is_self 没标, 但本机昵称在
        # latest_bids 里 -> 用全局本机昵称取回我的出价 + 判断是否我拍下(赢家昵称==本机昵称)。
        # 这样 6-23 及更早的局也能显示真实的我的出价/盈亏(数据一直在, 只是当时没认出是本机)。
        if self_name_global and self_name_global in bids:
            if my_bid is None:
                my_bid = bids[self_name_global]
            if won_by_me is None and winner_name is not None:
                won_by_me = (winner_name == self_name_global)
        meta = map_meta.get(a.get("map_id")) or {}
        family = meta.get("family") or _family_from_id(a.get("map_id"))  # 活动图不在表 -> id 前缀回退
        # 门票(入场费): 统一按【族】取标准门票(_FEE_BY_FAMILY)。规则(作者确认): 活动变体图
        #  (cat201, 表内 entry_fee_silver 写的是 0)门票与对应原地图一致(沉船活动=沉船2.5w、
        #  别墅活动=别墅1w…), 故【必须】按族取、不能用表内的 0。基础图/tier 图表内门票与族门票本就
        #  一致(取哪个都一样)。除快递(族门票=0)外没有免费图; 仅未知族("其他")才回退表内 entry_fee。
        fee = _FEE_BY_FAMILY.get(family)
        if fee is None:
            fee = meta.get("entry_fee") or 0
        # 展示盈亏 = 按我们估价买入能赚多少 = 拍品价值 − 估价 − 门票(决策辅助, 假设以估价拍下)。
        display_pl = (value - est - fee) if (est is not None and value is not None) else None
        # 我的盈亏 = 真实结算结果(含门票 + 亏损分红机制):
        #  · 每位玩家(包括拿仓者本人)都收 10%×max(0,赢家出价−拍品真值)。
        #  · 我拍下: 拍品价值 − 我的出价 + 分红 − 门票；拿仓者的分红按确认规则计算。
        #  · 别人拍下: 我拿不到货；本机分红优先用结算帧 f5 直接值，仅旧样本缺 f5 时按规则计算。
        #  · 老样本 won_by_me=None(无 self 数据)-> 无法判定, None。
        welfare = a.get("settlement_welfare")  # 本场福利(游戏地面真值)
        loss_share = None
        profit_source = None
        if won_by_me is True and my_bid is not None and value is not None:
            loss_share = _loss_share_units(my_bid, value)
            my_pl = value - my_bid + (loss_share or 0) - fee
            profit_source = "mechanic_10pct"
        elif won_by_me is False:
            if _number(welfare):
                loss_share = welfare                                  # 地面真值优先
                profit_source = "settlement_direct"
            elif value is not None and winner_bid is not None:
                loss_share = _loss_share_units(winner_bid, value)
                profit_source = "mechanic_10pct"
            my_pl = (loss_share - fee) if loss_share is not None else None
        else:
            my_pl = None
        rec = {
            # Keep the stable local session identity so the overlay can open the exact
            # settlement that was clicked instead of merely showing the newest-first list.
            "session_id": str(sid) if sid is not None else "",
            "time": a.get("created_at"),
            "round": a.get("round"),
            "map_id": a.get("map_id"),
            "map_name": meta.get("name", ""),
            "family": family,                               # 顶部筛选: 快递/仓库/集装箱/别墅/沉船/拍卖会
            "entry_fee": fee,                               # 门票(银两), 已计入盈亏
            "hero": a.get("hero"),
            "skills": _skills_summary(a),
            "lot_value": value,                                   # 拍品价值=结算 known_value_sum
            "estimate": est,                                      # 我们的估价
            "display_pl": display_pl,                             # 展示盈亏=真值−估价−门票(正=赚)
            "winner_name": winner_name,
            "winner_bid": winner_bid,
            "all_bids": bids,
            "settlement_incomplete": a.get("settlement_incomplete"),
            "self_name": a.get("self_name"),
            "my_bid": my_bid,
            "my_pl": my_pl,
            "loss_share": loss_share,                       # 每位玩家分红=赢家亏损×10%
            "welfare_is_truth": _number(welfare),           # True=结算帧 f5 直接值
            "profit_source": profit_source,                 # settlement_direct / mechanic_10pct
            "won_by_me": won_by_me,
            "rank_summary": a.get("rank_summary"),          # 排位赛排位分(纯展示; 非排位为 None)
            "rank_observation": _rank_observation(a.get("rank_summary")),
            "minimap": _trim_minimap(a),                    # 结算小地图(展开详情渲染)
        }
        records.append(rec)
    records.sort(key=lambda r: r.get("time") or 0, reverse=True)
    _mark_rank_observation_gaps(records)
    return records


def load_self_id() -> int | None:
    cache = ROOT / "data" / "logs" / "live" / "local_player_cache.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8")).get("local_player_id")
        except Exception:  # noqa: BLE001
            pass
    return None


def build_payload(sessions_path: Path, self_id: int | None = None) -> dict:
    """完整对局记录 payload(CLI 写 json + HTML 生成器共用)。"""
    if self_id is None:
        self_id = load_self_id()
    recs = build_records(sessions_path, self_id)
    # 全局本机昵称: 老样本逐条无 self_name(只新样本带), 但同一份 sessions.jsonl 是同一玩家 ->
    # 取任一条带 self_name 的作全局, 供 HTML 标「赢家=我方」(老样本 winner 命中此名即我拍下)。
    self_name = next((r.get("self_name") for r in recs if r.get("self_name")), None)
    return {"schema": "match_records_v0", "self_id": self_id, "self_name": self_name,
            "n": len(recs), "records": recs}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", default=str(ROOT / "data" / "logs" / "live" / "sessions.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "data" / "logs" / "live" / "match_records.json"))
    args = ap.parse_args(argv[1:])
    sp = Path(args.sessions)
    if not sp.exists():
        print(f"无 {sp}"); return 1
    payload = build_payload(sp)
    recs = payload["records"]
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"对局记录: {len(recs)} 条 -> {args.out}")
    for r in recs[:5]:
        print("  map=%s r=%s 估价=%s 拍品值=%s 展示盈亏=%s 赢家=%s/%s" % (
            r["map_name"] or r["map_id"], r["round"], r["estimate"], r["lot_value"],
            r["display_pl"], r["winner_name"], r["winner_bid"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
