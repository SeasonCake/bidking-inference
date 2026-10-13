"""公共大数据聚合(0.2.7)设计门。

⭐第一铁律 = 隐私: /public 是**公开页**(无需登录)。输出里出现任何身份字段(昵称/uid/session/
赢家/出价)就是事故 —— 而且是发出去就收不回的那种。故本套件对【整棵输出树】做递归扫描,
任何禁用 key 或任何看起来像昵称的值出现即红。
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import public_stats as ps  # noqa: E402


def _sample(map_id=2401, *, counts="q1=2;q5=1;q6=1", values="q1=100;q5=5000;q6=90000",
            count=10, cells=30, value=95100, **extra):
    rec = {
        "map_id": map_id,
        "final_quality_counts": counts,
        "final_quality_values": values,
        "inventory_count": count,
        "inventory_cells": cells,
        "known_value_sum": value,
        # 真样本里 client_ts 是 100% 有的(实测 45,693/45,693)。fixture 以前没有它 ——
        # 于是趋势/cube 的测试全在【没有游玩时间】的假数据上跑, 掩盖了口径问题。
        "client_ts": 1783771340.6,
        # 下面这些身份字段是【真实样本里就有的】—— 聚合必须把它们全部丢掉
        "player": "某个昵称",
        "players": ["昵称A", "昵称B"],
        "winner": "昵称A",
        "winner_bid": 600000,
        "self_bid": 610001,
        "winner_is_self": False,
        "settlement_welfare": 720002,
        "session_hash": "deadbeef",
        "uid_hash": "0123456789abcdef",
        "received_ts": 1783771340.6,
    }
    rec.update(extra)
    return rec


def _walk(node):
    """递归遍历输出树, 产出 (key, value) 与所有标量值。"""
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield None, v
            yield from _walk(v)


# --------------------------------------------------------------------------- #
# ⭐隐私红线
# --------------------------------------------------------------------------- #
def test_output_contains_no_identity_keys():
    """整棵输出树里不得出现任何禁用字段名。"""
    agg = ps.aggregate_public([_sample(), _sample(map_id=2402)])
    bad = [k for k, _ in _walk(agg) if k in ps._FORBIDDEN_KEYS]
    assert not bad, f"公开输出里出现了身份字段: {bad}"


def test_output_contains_no_nickname_values():
    """更狠一层: 连【值】里都不能出现昵称/session/uid 的内容(防止被塞进别的 key)。"""
    agg = ps.aggregate_public([_sample(), _sample(map_id=2402)])
    leaked = {"某个昵称", "昵称A", "昵称B", "deadbeef", "0123456789abcdef"}
    vals = {v for _, v in _walk(agg) if isinstance(v, str)}
    hit = vals & leaked
    assert not hit, f"公开输出里泄露了身份值: {hit}"


def test_winner_bid_not_leaked_as_number():
    """出价是身份关联信息(能反推是谁那局), 不能以任何形式出现。"""
    agg = ps.aggregate_public([_sample()])
    nums = {v for _, v in _walk(agg) if isinstance(v, (int, float))}
    assert 600000 not in nums, "赢家出价泄露进了公开输出"
    assert 610001 not in nums, "我的出价泄露进了公开输出"
    assert 720002 not in nums, "逐局福利真值泄露进了公开输出"


def test_self_profit_includes_winner_dividend_and_prefers_loser_truth() -> None:
    winner = _sample(
        known_value_sum=1_000,
        player="我",
        winner="我",
        winner_bid=1_200,
        settlement_welfare=None,
    )
    loser_truth = _sample(
        known_value_sum=1_000,
        player="我",
        winner="别人",
        winner_bid=1_200,
        settlement_welfare=55,
    )
    loser_legacy = dict(loser_truth, settlement_welfare=None)
    loser_bad_truth = dict(loser_truth, settlement_welfare=True)

    assert ps._self_profit(winner, 15) == -195  # 1000 - 1200 + 20 - 15
    assert ps._self_profit(loser_truth, 15) == 40
    assert ps._self_profit(loser_legacy, 15) == 5
    assert ps._self_profit(loser_bad_truth, 15) == 5


def test_public_footer_describes_default_upload_and_public_privacy_boundary():
    html = ps.build_public_html(ps.aggregate_public([_sample() for _ in range(20)]), generated_at=0)
    assert "公版客户端默认上传" in html
    assert "去身份统计聚合" in html
    assert "不展示昵称" in html and "逐局出价" in html
    assert "无昵称、无账号、无逐局明细" not in html


# --------------------------------------------------------------------------- #
# 聚合正确性
# --------------------------------------------------------------------------- #
def test_aggregates_per_map_and_counts_samples():
    agg = ps.aggregate_public([_sample(2401), _sample(2401), _sample(2402)])
    by_id = {m["map_id"]: m for m in agg["maps"]}
    assert by_id[2401]["n"] == 2
    assert by_id[2402]["n"] == 1
    assert agg["totals"]["samples"] == 3
    assert agg["totals"]["maps"] == 2


def test_map_profile_separates_theme_gameplay_mode_activity_and_source_policy():
    from build_match_records import _map_profile

    regular = _map_profile(2401, category=104, auction_mode="open")
    ranked = _map_profile(5601, category=402, auction_mode="sealed")
    historical = _map_profile(4511, category=305, auction_mode="sealed")
    current_red_box = _map_profile(2541)
    next_sealed = _map_profile(4551)
    historical_open = _map_profile(2533)
    historical_sealed = _map_profile(4537)
    auction_hall = _map_profile(6001, category=601, auction_mode="auction_hall")

    assert (regular["map_kind"], regular["auction_mode"]) == ("regular", "open")
    assert regular["warehouse_policy"] == "player_possible"
    assert (ranked["map_kind"], ranked["auction_mode"]) == ("ranked", "sealed")
    assert ranked["warehouse_policy"] == "official_only"
    assert (
        historical["map_kind"],
        historical["activity_status"],
        historical["warehouse_policy"],
    ) == ("activity", "historical", "official_only")
    assert (
        current_red_box["activity_status"],
        current_red_box["activity_cohort"],
        current_red_box["warehouse_policy"],
    ) == ("current", "红箱活动", "official_only")
    assert (
        next_sealed["map_kind"],
        next_sealed["activity_status"],
        next_sealed["auction_mode"],
        next_sealed["warehouse_policy"],
    ) == ("activity", "pending", "sealed", "official_only")
    assert (
        historical_open["map_kind"],
        historical_open["activity_status"],
        historical_open["activity_cohort"],
        historical_open["auction_mode"],
    ) == ("activity", "historical", "活动沉船", "open")
    assert (
        historical_sealed["map_kind"],
        historical_sealed["activity_status"],
        historical_sealed["activity_cohort"],
        historical_sealed["auction_mode"],
    ) == ("activity", "historical", "活动沉船", "sealed")
    assert (
        auction_hall["map_kind"],
        auction_hall["auction_mode"],
        auction_hall["warehouse_policy"],
    ) == ("auction_hall", "auction_hall", "unknown")


def test_current_activity_identity_is_shared_by_meta_cube_and_public_views():
    from build_match_records import _load_map_meta

    meta = _load_map_meta()
    for mid, mode in ((2541, "open"), (2543, "open"), (4543, "sealed"), (4547, "sealed")):
        row = meta[mid]
        assert row["map_kind"] == "activity"
        assert row["activity_status"] == "current"
        assert row["activity_cohort"] == "红箱活动"
        assert row["auction_mode"] == mode
        assert "红箱活动" in row["name"]

    rows = []
    for mid in (2543, 4547):
        rows.extend(_sample(mid) for _ in range(ps.MIN_SAMPLES_TO_SHOW))
    aggregate = ps.aggregate_public(rows)
    by_id = {row["map_id"]: row for row in aggregate["maps"]}
    assert by_id[2543]["activity_status"] == "current"
    assert by_id[4547]["activity_status"] == "current"
    cube = aggregate["cube"]
    cube_by_id = {mid: i for i, mid in enumerate(cube["maps"])}
    assert cube["map_activity"][cube_by_id[2543]] == "current"
    assert cube["map_activity"][cube_by_id[4547]] == "current"
    html = ps.build_public_html(aggregate, generated_at=0)
    assert "军用舰艇保险库(红箱活动)" in html
    assert "皇家御用货舱(红箱活动)" in html
    assert html.count("当前活动") >= 2


def test_trusted_official_only_maps_do_not_inherit_postupdate_mixed_fallback():
    unknown = {
        "warehouse_source_qualification": "postupdate_unknown_warehouse_source",
        "official_distribution_eligible": False,
    }
    rows = [
        _sample(5601, **unknown),
        _sample(4511, **unknown),
        _sample(2541, **unknown),
        _sample(2501, **unknown),
    ]
    by_id = {row["map_id"]: row for row in ps.aggregate_public(rows)["maps"]}

    for map_id in (5601, 4511, 2541):
        assert by_id[map_id]["mixed_source_n"] == 0
        assert by_id[map_id]["source_status"] == "accepted"
        assert by_id[map_id]["source_label"] == "主统计（已接纳）"
    assert by_id[2501]["source_status"] == "accepted"
    unknown_row = ps.aggregate_public(rows)["maps_by_source"]["unknown"][0]
    assert unknown_row["map_id"] == 2501
    assert unknown_row["mixed_source_n"] == 1
    assert unknown_row["source_status"] == "unknown"


def test_explicit_player_source_on_official_only_map_is_visible_conflict():
    row = _sample(
        4511,
        warehouse_source_qualification="player_custom_warehouse",
        official_distribution_eligible=False,
    )
    aggregate = ps.aggregate_public([row])
    assert aggregate["maps"] == []
    got = aggregate["maps_by_source"]["conflict"][0]
    assert got["source_status"] == "conflict"
    assert got["source_scope_counts"] == {
        "official_samples": 0,
        "player_warehouse_samples": 0,
        "unknown_samples": 0,
        "conflict_samples": 1,
    }


def test_main_stats_merge_accepted_sources_but_keep_diagnostics_and_exclude_conflict():
    """主统计合并 accepted 三池；原来源仍可核查，conflict 不能污染主结果。"""
    official = _sample(
        2401,
        counts="q1=10",
        count=10,
        cells=20,
        value=1000,
        warehouse_source_qualification="official_random_warehouse",
        official_distribution_eligible=True,
    )
    official_other_map = _sample(
        2402,
        counts="q1=20",
        count=20,
        cells=40,
        value=2000,
        warehouse_source_qualification="official_random_warehouse",
        official_distribution_eligible=True,
    )
    player = _sample(
        2401,
        counts="q6=100",
        count=100,
        cells=600,
        value=100_000_000,
        warehouse_source_qualification="player_custom_warehouse",
        official_distribution_eligible=False,
    )
    unknown = _sample(
        2401,
        counts="q6=50",
        count=50,
        cells=300,
        value=50_000_000,
        warehouse_source_qualification="postupdate_unknown_warehouse_source",
        official_distribution_eligible=False,
    )
    conflict = _sample(
        2401,
        counts="q6=25",
        count=25,
        cells=150,
        value=25_000_000,
        warehouse_source_qualification="official_random_warehouse",
        official_distribution_eligible=False,
    )

    official_baseline = ps.aggregate_public([official, official_other_map])
    accepted_baseline = ps.aggregate_public(
        [official, official_other_map, player, unknown]
    )
    aggregate = ps.aggregate_public(
        [official, official_other_map, player, unknown, conflict]
    )
    keep = (
        "n",
        "avg_count",
        "avg_cells",
        "avg_value",
        "red_rate",
        "gold_rate",
        "avg_q",
        "sd_q",
        "avg_q_value",
    )
    assert {
        key: next(row for row in aggregate["maps"] if row["map_id"] == 2401)[key]
        for key in keep
    } == {
        key: next(
            row for row in accepted_baseline["maps"] if row["map_id"] == 2401
        )[key]
        for key in keep
    }
    assert [row["map_id"] for row in aggregate["maps"]] == [
        row["map_id"] for row in accepted_baseline["maps"]
    ]
    assert aggregate["trend"] == accepted_baseline["trend"]
    assert aggregate["heroes"] == accepted_baseline["heroes"]
    assert aggregate["tiers"] == accepted_baseline["tiers"]
    assert aggregate["totals"]["samples"] == 4
    assert aggregate["totals"]["received_samples"] == 5
    assert aggregate["totals"]["source_scope_counts"] == {
        "official": 2,
        "player_custom": 1,
        "unknown": 1,
        "conflict": 1,
    }
    assert aggregate["trend_source_counts"] == [
        {
            "day": "2026-07-11",
            "total": 5,
            "accepted": 4,
            "official": 2,
            "player_custom": 1,
            "unknown": 1,
            "conflict": 1,
        }
    ]

    by_scope = aggregate["maps_by_source"]
    assert next(
        row for row in by_scope["official"] if row["map_id"] == 2401
    )["avg_count"] == next(
        row
        for row in official_baseline["maps_by_source"]["official"]
        if row["map_id"] == 2401
    )["avg_count"]
    assert next(
        row for row in by_scope["accepted"] if row["map_id"] == 2401
    )["avg_count"] == 53.3
    assert by_scope["player_custom"][0]["avg_count"] == 100
    assert by_scope["unknown"][0]["avg_count"] == 50
    assert by_scope["conflict"][0]["avg_count"] == 25

    cube = aggregate["cube"]
    cells = {
        (cube["sources"][cube["s"][i]], cube["m"][i]): {
            "n": cube["n"][i],
            "count": cube["c"][i],
            "cells": cube["g"][i],
            "red": cube["red"][i],
        }
        for i in range(len(cube["n"]))
    }
    assert cells == {
        ("accepted", 2401): {"n": 3, "count": 160, "cells": 920, "red": 2},
        ("accepted", 2402): {"n": 1, "count": 20, "cells": 40, "red": 0},
        ("official", 2401): {"n": 1, "count": 10, "cells": 20, "red": 0},
        ("official", 2402): {"n": 1, "count": 20, "cells": 40, "red": 0},
        ("player_custom", 2401): {
            "n": 1,
            "count": 100,
            "cells": 600,
            "red": 1,
        },
        ("unknown", 2401): {"n": 1, "count": 50, "cells": 300, "red": 1},
        ("conflict", 2401): {"n": 1, "count": 25, "cells": 150, "red": 1},
    }

    html = ps.build_public_html(aggregate, generated_at=0)
    assert '<option value="accepted" selected>主统计（已接纳）</option>' in html
    assert '<option value="player_custom">玩家仓</option>' in html
    assert 'var F = {source:"accepted"' in html
    assert "sourceAt(i)!==F.source" in html
    assert "合并官方、来源待确认与已识别玩家仓" in html
    assert "来源冲突与服务端 quarantine 永不纳入" in html
    assert "主统计样本" in html and "其中官方" in html
    assert "来源待确认" in html and "冲突(未计)" in html
    assert 'data-v="4" class="num strong">4</td>' in html
    assert "主统计 4 局（官方 2 / 待确认 1 / 玩家仓 1）" in html
    assert "来源冲突未计 1 局" in html


def test_explicit_server_quarantine_never_enters_public_statistics():
    """即使调用方误把 quarantine 行传进来，公开主统计和来源诊断也必须丢弃它。"""
    class ExplodingReason:
        magic_calls = 0

        @classmethod
        def _explode(cls, operation):
            cls.magic_calls += 1
            raise RuntimeError(f"quarantine reason protocol must not run: {operation}")

        def __bool__(self):
            return type(self)._explode("bool")

        def __str__(self):
            return type(self)._explode("str")

        def __eq__(self, _other):
            return type(self)._explode("eq")

    accepted = _sample(
        2401,
        counts="q6=2",
        warehouse_source_qualification="official_random_warehouse",
        official_distribution_eligible=True,
        server_quarantine_reason=None,
    )
    quarantined = _sample(
        2401,
        counts="q6=99",
        warehouse_source_qualification="official_random_warehouse",
        official_distribution_eligible=True,
        server_quarantine_reason="warehouse_source_pending",
    )
    protocol_quarantined = _sample(
        2401,
        counts="q6=88",
        warehouse_source_qualification="official_random_warehouse",
        official_distribution_eligible=True,
        server_quarantine_reason=ExplodingReason(),
    )

    baseline = ps.aggregate_public([accepted])
    aggregate = ps.aggregate_public([accepted, quarantined, protocol_quarantined])

    assert aggregate == baseline
    assert aggregate["totals"]["samples"] == 1
    assert aggregate["totals"]["received_samples"] == 1
    assert aggregate["trend"][0]["avg_q"]["q6"] == 2.0
    assert {
        aggregate["cube"]["sources"][aggregate["cube"]["s"][i]]
        for i in range(len(aggregate["cube"]["n"]))
    } == {"accepted", "official"}
    assert ExplodingReason.magic_calls == 0


def test_public_map_table_exposes_orthogonal_filters_and_cube_metadata():
    rows = [
        _sample(2401),
        _sample(5601),
        _sample(4511),
        _sample(2541),
    ]
    agg = ps.aggregate_public(rows)
    html = ps.build_public_html(agg, generated_at=0)

    assert 'id="mp-kind"' in html and 'id="mp-mode"' in html
    assert 'id="mp-activity"' in html and 'id="mp-source"' in html
    assert "普通图" in html and "排位" in html and "活动图" in html
    assert "明拍" in html and "暗拍" in html
    assert "往期活动" in html and "当前活动" in html
    assert "仅官方随机仓" in html
    assert "官方池（该图可能有玩家仓）" in html
    assert "主统计（已接纳）" in html
    assert "冲突与服务端 quarantine 永不纳入" in html
    assert "往期或待确认活动统一沉底" in html

    cube = agg["cube"]
    by_id = {map_id: i for i, map_id in enumerate(cube["maps"])}
    assert cube["map_kinds"][by_id[5601]] == "ranked"
    assert cube["map_modes"][by_id[4511]] == "sealed"
    assert cube["map_activity"][by_id[2541]] == "current"


def test_default_map_order_prioritizes_current_activity_then_sample_size():
    rows = [
        _sample(4511, value=9_000_000),
        *[_sample(2401, value=1_000_000) for _ in range(3)],
        *[_sample(2402, value=500_000) for _ in range(5)],
        *[_sample(2541, value=8_000_000) for _ in range(9)],
    ]

    maps = ps.aggregate_public(rows)["maps"]

    assert [row["map_id"] for row in maps] == [2541, 2402, 2401, 4511]
    assert maps[0]["activity_status"] == "current"
    assert all(row["activity_status"] != "current" for row in maps[1:])


def test_default_map_sort_key_only_allows_confirmed_current_activity_to_lead():
    base = {"avg_value": 1, "map_id": 1}
    rows = [
        {**base, "map_kind": "activity", "activity_status": "historical", "n": 999},
        {**base, "map_kind": "regular", "activity_status": "none", "n": 20},
        {**base, "map_kind": "activity", "activity_status": "current", "n": 5},
    ]

    assert [row["activity_status"] for row in sorted(rows, key=ps._default_map_sort_key)] == [
        "current",
        "none",
        "historical",
    ]


def test_red_and_gold_rate():
    """红/金出现率 = 出现过该品质的局数占比(不是件数占比)。"""
    rows = [
        _sample(2401, counts="q1=5"),                 # 无红无金
        _sample(2401, counts="q5=2"),                 # 有金无红
        _sample(2401, counts="q5=1;q6=3"),            # 金+红
        _sample(2401, counts="q6=1"),                 # 有红无金
    ]
    m = ps.aggregate_public(rows)["maps"][0]
    assert m["n"] == 4
    assert m["red_rate"] == 0.5     # 4 局里 2 局有红
    assert m["gold_rate"] == 0.5    # 4 局里 2 局有金


def test_avg_quality_counts():
    rows = [_sample(2401, counts="q6=2"), _sample(2401, counts="q6=4")]
    m = ps.aggregate_public(rows)["maps"][0]
    assert m["avg_q"]["q6"] == 3.0   # (2+4)/2


def test_low_confidence_flag_for_small_samples():
    """小样本会骗人: 3 局就报'红掉率33%'是噪音。必须标记出来, 且排在高置信数据之后。"""
    rows = [_sample(2401) for _ in range(3)] + [_sample(2402) for _ in range(ps.MIN_SAMPLES_TO_SHOW)]
    maps = ps.aggregate_public(rows)["maps"]
    by_id = {m["map_id"]: m for m in maps}
    assert by_id[2401]["low_confidence"] is True
    assert by_id[2402]["low_confidence"] is False
    assert maps[0]["map_id"] == 2402, "高置信的地图必须排在样本不足的前面"


def test_incomplete_settlement_excluded():
    """残缺结算会把掉率算低 -> 不进公开统计。"""
    rows = [_sample(2401), _sample(2401, settlement_incomplete=True)]
    m = ps.aggregate_public(rows)["maps"][0]
    assert m["n"] == 1


def test_item_price_table_dedups_and_counts_rarity():
    rows = [
        _sample(2401, rare_items=[{"n": "神医《人痘术》", "q": 6, "v": 158940}]),
        _sample(2401, rare_items=[{"n": "神医《人痘术》", "q": 6, "v": 158940},
                                  {"n": "某金件", "q": 5, "v": 37920}]),
    ]
    items = {i["name"]: i for i in ps.aggregate_public(rows)["items"]}
    assert items["神医《人痘术》"]["seen"] == 2      # 出现次数 = 稀有度的经验证据
    assert items["神医《人痘术》"]["value"] == 158940
    assert items["某金件"]["seen"] == 1


# --------------------------------------------------------------------------- #
# 健壮性(公开端点不能被一条坏数据搞挂)
# --------------------------------------------------------------------------- #
def test_garbage_rows_never_crash():
    rows = [
        None, "not a dict", 42, {},
        {"map_id": None},
        {"map_id": "not-int", "final_quality_counts": "q1=1"},
        {"map_id": 2401, "final_quality_counts": "垃圾数据"},
        {"map_id": 2401, "final_quality_counts": "q1=1", "rare_items": ["坏项", None, {}]},
        _sample(2401),
    ]
    agg = ps.aggregate_public(rows)          # 不抛异常
    assert agg["totals"]["samples"] >= 1     # 好数据仍被统计到


def test_qmap_parses_and_tolerates_junk():
    assert ps._qmap("q1=7;q2=4;q6=3") == {"q1": 11, "q6": 3}   # 绿(q2) 并入白(q1): 7+4=11
    assert ps._qmap("") == {}
    assert ps._qmap(None) == {}
    assert ps._qmap("q9=1;garbage;q1=2") == {"q1": 2}   # 只认 q1-q6


# --------------------------------------------------------------------------- #
# 绿(q2): 曾经被整档丢掉 —— 解析层原样返回 q2, 而渲染层的列只列了 q1/q3/q4/q5/q6,
# 于是绿从头到尾没人接: "白/局"欠 3 倍, 行内均件与品质列之和对不上, 占比分母也漏了绿。
# --------------------------------------------------------------------------- #
def test_green_is_folded_into_white_not_dropped():
    """白 = q1 + q2。绿【不能】既不单列、又不并入 —— 那就是凭空丢掉一档。"""
    rows = [_sample(2401, counts="q1=3;q2=5;q6=1") for _ in range(ps.MIN_SAMPLES_TO_SHOW)]
    m = ps.aggregate_public(rows)["maps"][0]
    assert m["avg_q"]["q1"] == 8.0, "绿没有并进白 —— 白被少算了"
    assert "q2" not in m["avg_q"], "绿不该再单独成档(展示口径是并入白)"


def test_quality_columns_sum_matches_total_and_denominator():
    """全服占比的分母 = 5 个展示档之和。漏一档 -> 其余档的百分比全部虚高。"""
    rows = [_sample(2401, counts="q1=2;q2=2;q3=4;q6=2") for _ in range(ps.MIN_SAMPLES_TO_SHOW)]
    agg = ps.aggregate_public(rows)
    html = ps.build_public_html(agg, generated_at=0, rebuild_interval=1800)
    # 白 4 / 蓝 4 / 红 2 = 10 -> 40.0% / 40.0% / 20.0%。若绿被丢, 白只剩 2, 分母 8, 蓝会印成 50.0%。
    assert "40.0%" in html and "20.0%" in html
    assert "50.0%" not in html, "分母漏了绿 —— 其余品质的占比被抬高了"


# --------------------------------------------------------------------------- #
# 英雄中文名: 公开页是给群友看的, 他们只认游戏里的中文名, 不认 "wuqilin"/"chenmei"
# --------------------------------------------------------------------------- #
def test_every_hero_slug_has_a_chinese_name():
    """引擎新增一个英雄而没配中文名 -> 公开页会裸露英文 slug。这里直接打红。

    真源 = inference.observation.HeroMode(引擎侧的权威 slug 列表)。本测试【只读】它。
    """
    import typing

    from bidking_lab.inference.observation import HeroMode

    slugs = set(typing.get_args(HeroMode))
    assert slugs, "HeroMode 没取到 slug —— 测试自身坏了, 不是被测代码坏了"
    missing = sorted(s for s in slugs if s not in ps._HERO_CN)
    assert not missing, f"这些英雄没有中文名(公开页会裸露英文 slug): {missing}"


def test_hero_name_handles_unknown_and_missing():
    assert ps.hero_name("ahmed") == "艾哈迈德"
    assert ps.hero_name("wuqilin") == "吴起灵"       # 拼音 slug, 最容易被漏配
    assert ps.hero_name("liss") == "莉丝"
    assert ps.hero_name("?") == "未识别"             # 抓不到英雄时样本里就是 '?'
    assert ps.hero_name("") == "未识别"
    assert ps.hero_name(None) == "未识别"
    assert ps.hero_name("新英雄xyz") == "新英雄xyz"  # 未知 -> 原样回显, 绝不抛


def test_legacy_missing_hero_bucket_is_liss():
    """06-25 后旧客户端无法命名莉丝，0713 快照的 100 局因此是 hero=null。"""
    rows = [_sample(2401, hero=None) for _ in range(3)]
    agg = ps.aggregate_public(rows)
    assert agg["heroes"][0]["hero"] == "liss"
    assert agg["heroes"][0]["name"] == "莉丝"
    assert agg["cube"]["heroes"] == ["liss"]


def test_hero_rows_carry_chinese_name():
    agg = ps.aggregate_public([_sample(2401, hero="ahmed") for _ in range(3)])
    assert agg["heroes"][0]["name"] == "艾哈迈德"
    assert agg["heroes"][0]["hero"] == "ahmed"       # slug 保留(排序/调试用)


# --------------------------------------------------------------------------- #
# 统计诚实: 小样本的天不许伪装成"官方调了掉率"; 均收益必须带标准误
# --------------------------------------------------------------------------- #
def test_small_sample_day_is_flagged_low_confidence():
    """开服头几天 n=1/n=3 的天和 n=1700 的天并排 -> 伪造出一个不存在的"掉率跳变"。

    这张趋势表【唯一的用途】就是发现跳变, 所以小样本必须标出来。
    """
    day_a = 1783771340.6                    # 同一天, 只有 2 局
    day_b = day_a + 86400 * 3               # 三天后, 足量
    rows = [_sample(2401, client_ts=day_a) for _ in range(2)]
    rows += [_sample(2401, client_ts=day_b) for _ in range(ps.MIN_N_FOR_TREND_DAY + 5)]
    trend = {d["day"]: d for d in ps.aggregate_public(rows)["trend"]}
    small = [d for d in trend.values() if d["n"] == 2]
    big = [d for d in trend.values() if d["n"] > ps.MIN_N_FOR_TREND_DAY]
    assert small and small[0]["low_confidence"] is True
    assert big and big[0]["low_confidence"] is False


def test_profit_carries_standard_error():
    """只印均收益不印 ±SEM = 把噪音当结论公告出去(实测 32 个段位档里 29 档的 95% 区间跨 0)。"""
    rows = []
    for i in range(ps.MIN_N_FOR_IDENTITY_DERIVED + 10):
        rows.append(_sample(2401, hero="ahmed", rank_tier=7, rank_points=1250,
                            winner="w", player="w", winner_bid=100_000 + i * 10_000))
    agg = ps.aggregate_public(rows)
    for row in (agg["heroes"][0], agg["tiers"][0]):
        assert row["avg_profit"] is not None
        assert row["profit_sem"] is not None and row["profit_sem"] > 0, "SEM 必须真算出来"


def test_sem_shrinks_with_n():
    """SEM = σ/√n —— 样本翻 4 倍, 误差棒应缩到约一半。防止我把 σ 错当 SEM 印出去。"""
    vals = [100.0, -100.0] * 8
    sem_small = ps._sem(vals)
    sem_big = ps._sem(vals * 4)
    assert sem_small > 0
    assert abs(sem_big - sem_small / 2) < sem_small * 0.05


def test_single_sample_never_reports_zero_error():
    """⭐ n<2 -> "±?"(算不出), 【绝不能是 "±0"】。

    "±0" 读起来像"这张图每局雷打不动开 3 个红" —— 而它其实只有【一个样本】。
    零误差和算不出误差是两回事; 把后者印成前者, 是这一页最隐蔽的一种骗人方式。
    实测云端 96 张图里有 2 张只有 1 局样本(2531 / 4532), 它们六个品质列全是这个坑。
    """
    assert ps._sd([5.0]) is None            # 不是 0.0
    assert ps._sd([]) is None
    assert ps._sem([5.0]) is None
    assert ps._sd([1.0, 3.0]) is not None   # n>=2 才算得出

    agg = ps.aggregate_public([_sample(2401, counts="q1=3;q6=1")])   # 单样本地图
    m = agg["maps"][0]
    assert m["n"] == 1
    assert m["sd_q"]["q6"] is None, "n=1 的图必须报'算不出', 不是 0"

    html = ps.build_public_html(agg, generated_at=0, rebuild_interval=1800)
    assert "±?" in html
    assert "±0.00" not in html, "n=1 却印出 ±0.00 = 假装零误差"


# --------------------------------------------------------------------------- #
# 渲染契约: HTML 用的 class 必须在 web_ui 的 CSS 里【真实存在】。
# 曾经 KPI 卡写的是 .k / .l —— 那两个类名 CSS 里根本没有, 于是打开页面第一眼的
# 6 个大数字全渲染成无样式裸文本, 而任何"能渲染/测试绿"的检查都发现不了。
# --------------------------------------------------------------------------- #
def test_kpi_uses_classes_that_actually_exist_in_css():
    import sys
    sys.path.insert(0, str(_SCRIPTS))
    import web_ui  # noqa: PLC0415

    agg = ps.aggregate_public([_sample(2401) for _ in range(ps.MIN_SAMPLES_TO_SHOW)])
    html = ps.build_public_html(agg, generated_at=0, rebuild_interval=1800)

    assert '<div class="kpi"><b>' in html, "KPI 必须用 .kpi b / .kpi span(CSS 里只有这两条规则)"
    assert '<div class="k">' not in html, "'.k' 在 CSS 里不存在 —— 大数字会变成裸文本"
    assert '<div class="l">' not in html
    assert ".kpi b{" in web_ui._CSS and ".kpi span{" in web_ui._CSS


def test_every_table_is_wrapped_for_horizontal_scroll():
    """宽表(英雄表 12 列)不套 .scroll, 手机上横滚的就是【整个页面】而不是表格。"""
    agg = ps.aggregate_public([_sample(2401) for _ in range(ps.MIN_SAMPLES_TO_SHOW)])
    html = ps.build_public_html(agg, generated_at=0, rebuild_interval=1800)
    n_tbl = html.count("<table")
    n_wrapped = html.count('<div class="scroll"><table')
    assert n_tbl > 0 and n_wrapped == n_tbl, f"{n_tbl} 张表里只有 {n_wrapped} 张套了滚动壳"


def test_map_stats_keeps_red_metrics_primary_and_moves_secondary_metrics_to_details():
    long_name = "非常非常长的活动地图名称用于验证默认布局不会裁掉关键红色指标"
    rows = [
        _sample(2541, counts="q1=12;q3=11;q4=10;q5=5;q6=4")
        for _ in range(ps.MIN_SAMPLES_TO_SHOW)
    ]
    aggregate = ps.aggregate_public(rows)
    aggregate["maps"][0]["name"] = long_name
    html = ps.build_public_html(aggregate, generated_at=0)
    table_at = html.index('<table data-sortable id="mapStatsTable"')
    table_end = html.index("</table>", table_at)
    table = html[table_at:table_end]

    assert 'class="map-stats-table"' in table
    assert table.index("地图与活动") < table.index("样本") < table.index("平均总价值")
    assert table.index("平均总价值") < table.index("红/局") < table.index("出红率")
    assert 'data-label="红/局"' in table and 'data-label="出红率"' in table
    assert "4.00" in table and "100%" in table
    assert long_name in table and "当前活动" in table and "明拍" in table
    assert '<details class="map-secondary-details">' in table
    assert "白/局" in table and "蓝/局" in table and "紫/局" in table and "金/局" in table
    assert "均件" in table and "均格" in table and "来源构成" in table
    # 关键列本身无需依赖横向滚动；移动端改成同一行数据的摘要卡，不复制第二份指标。
    assert ".map-stats-scroll .scroll{overflow:visible}" in html
    assert "@media(max-width:760px)" in html
    assert ".map-stats-table tr{display:grid" in html
    assert ".map-stats-table thead{position:absolute" in html
    assert 'class="map-secondary-toggle"' in table


def test_map_stats_has_filter_aware_empty_state_without_a_second_dataset():
    html = ps.build_public_html(ps.aggregate_public([]), generated_at=0)

    assert 'id="mp-empty-state" class="map-empty-state" hidden' in html
    assert "暂无符合当前筛选条件的地图统计" in html
    assert 'querySelectorAll("tbody tr[data-kind]")' in html
    assert "empty.hidden=shown!==0" in html
    assert "mp-empty-row" not in html


def test_recent_red_uses_latest_play_day_natural_7_day_window():
    """正例必须触发、旧活动必须排除；窗口是最新游玩日 D 的 D-6..D，不是最近 7 个有样本日。"""
    cube = {
        "days": ["2026-07-01", "2026-07-06", "2026-07-12"],
        "maps": [2401, 2402, 2403], "map_names": ["旧活动", "近期甲", "近期乙"],
        "m": [2401, 2402, 2403], "d": [0, 1, 2], "n": [20, 20, 20],
        "qs_q6": [2000, 60, 100], "qq_q6": [200000, 180, 500],
    }
    got = ps.recent_red_maps_from_cube(cube)
    assert (got["start"], got["end"]) == ("2026-07-06", "2026-07-12")
    assert [r["map_id"] for r in got["rows"]] == [2403, 2402]  # 正例: 近期高红确实被检测器排出
    assert all(r["map_id"] != 2401 for r in got["rows"])       # 反例: 旧活动高红不得混入


def test_recent_red_defaults_to_official_source_pool():
    cube = {
        "sources": ["official", "player_custom", "unknown", "conflict"],
        "days": ["2026-07-12"],
        "maps": [2401],
        "map_names": ["普通明拍"],
        "s": [0, 1, 2, 3],
        "m": [2401, 2401, 2401, 2401],
        "d": [0, 0, 0, 0],
        "n": [20, 20, 20, 20],
        "qs_q6": [20, 2_000, 1_000, 500],
        "qq_q6": [20, 200_000, 50_000, 12_500],
    }

    official = ps.recent_red_maps_from_cube(cube)
    player = ps.recent_red_maps_from_cube(cube, source_scope="player_custom")

    assert official["rows"] == [
        {"map_id": 2401, "name": "普通明拍", "n": 20, "avg_q6": 1.0, "sd_q6": 0.0}
    ]
    assert player["rows"] == [
        {
            "map_id": 2401,
            "name": "普通明拍",
            "n": 20,
            "avg_q6": 100.0,
            "sd_q6": 0.0,
        }
    ]


def test_recent_red_main_pool_uses_all_accepted_sources_and_keeps_diagnostics():
    """主统计窗口包含已接纳玩家仓；official 诊断窗口仍独立可复核。"""
    def noon(day):
        return datetime.fromisoformat(day + "T12:00:00").replace(
            tzinfo=ps.SHANGHAI_TZ
        ).timestamp()

    official = [
        _sample(
            2401,
            client_ts=noon("2026-07-10"),
            counts="q6=2",
            warehouse_source_qualification="official_random_warehouse",
            official_distribution_eligible=True,
        )
        for _ in range(ps.MIN_SAMPLES_TO_SHOW)
    ]
    player = [
        _sample(
            2401,
            client_ts=noon("2026-07-29"),
            counts="q6=99",
            warehouse_source_qualification="player_custom_warehouse",
            official_distribution_eligible=False,
        )
        for _ in range(ps.MIN_SAMPLES_TO_SHOW)
    ]

    baseline = ps.aggregate_public(official)
    mixed = ps.aggregate_public(official + player)

    assert (mixed["recent_red"]["start"], mixed["recent_red"]["end"]) == (
        "2026-07-23",
        "2026-07-29",
    )
    assert mixed["recent_red"]["rows"][0]["avg_q6"] == 99.0
    official_recent = ps.recent_red_maps_from_cube(
        mixed["cube"],
        source_scope="official",
    )
    assert official_recent == baseline["recent_red"]
    html = ps.build_public_html(mixed, generated_at=noon("2026-07-29"))
    assert "2026-07-23 ~ 2026-07-29" in html
    assert "该窗口暂无达到 20 局的地图数据" not in html


def test_recent_red_each_source_has_independent_window_after_roundtrip_and_reorder():
    """四池各自取最新日；同日排序稳定，JSON 往返和输入倒序都不能串池。"""
    import json

    def noon(day):
        return datetime.fromisoformat(day + "T12:00:00").replace(
            tzinfo=ps.SHANGHAI_TZ
        ).timestamp()

    def rows(scope, map_id, day, red):
        qualification = {
            "official": "official_random_warehouse",
            "player_custom": "player_custom_warehouse",
            "unknown": "postupdate_unknown_warehouse_source",
            "conflict": "official_random_warehouse",
        }[scope]
        eligible = scope == "official"
        if scope == "conflict":
            eligible = False
        return [
            _sample(
                map_id,
                client_ts=noon(day),
                counts=f"q6={red}",
                warehouse_source_qualification=qualification,
                official_distribution_eligible=eligible,
            )
            for _ in range(ps.MIN_SAMPLES_TO_SHOW)
        ]

    groups = [
        *rows("official", 2401, "2026-07-10", 2),
        *rows("official", 2402, "2026-07-10", 3),
        *rows("player_custom", 2401, "2026-07-29", 99),
        *rows("unknown", 2401, "2026-07-01", 50),
        *rows("conflict", 2401, "2026-07-10", 25),
    ]
    expected = {
        "official": ("2026-07-10", [(2402, 3.0), (2401, 2.0)]),
        "player_custom": ("2026-07-29", [(2401, 99.0)]),
        "unknown": ("2026-07-01", [(2401, 50.0)]),
        "conflict": ("2026-07-10", [(2401, 25.0)]),
    }

    products = []
    for ordered in (groups, list(reversed(groups))):
        aggregate = ps.aggregate_public(ordered)
        roundtripped = json.loads(json.dumps(aggregate, ensure_ascii=False))
        actual = {}
        for scope in expected:
            recent = ps.recent_red_maps_from_cube(
                roundtripped["cube"], source_scope=scope
            )
            actual[scope] = (
                recent["end"],
                [(row["map_id"], row["avg_q6"]) for row in recent["rows"]],
            )
        assert actual == expected
        products.append(actual)

    assert products[0] == products[1]


def test_recent_red_main_accepts_player_while_empty_official_stays_empty():
    """主统计可只由 accepted 玩家仓组成；official 诊断池不能借用它。"""
    def noon(day):
        return datetime.fromisoformat(day + "T12:00:00").replace(
            tzinfo=ps.SHANGHAI_TZ
        ).timestamp()

    player = [
        _sample(
            2401,
            client_ts=noon("2026-07-29"),
            counts="q6=99",
            warehouse_source_qualification="player_custom_warehouse",
            official_distribution_eligible=False,
        )
        for _ in range(ps.MIN_SAMPLES_TO_SHOW)
    ]
    aggregate = ps.aggregate_public(player)

    assert ps.recent_red_maps_from_cube(
        aggregate["cube"],
        source_scope="official",
    ) == {
        "start": None,
        "end": None,
        "days": 7,
        "rows": [],
    }
    assert aggregate["recent_red"]["end"] == "2026-07-29"
    assert aggregate["recent_red"]["rows"][0]["avg_q6"] == 99.0
    player_recent = ps.recent_red_maps_from_cube(
        aggregate["cube"], source_scope="player_custom"
    )
    assert player_recent["end"] == "2026-07-29"
    assert player_recent["rows"][0]["avg_q6"] == 99.0

    legacy_cube = {
        key: value
        for key, value in aggregate["cube"].items()
        if key not in {"sources", "source_labels", "s"}
    }
    assert ps.recent_red_maps_from_cube(
        legacy_cube, source_scope="player_custom"
    ) == {
        "start": None,
        "end": None,
        "days": 7,
        "rows": [],
    }

    html = ps.build_public_html(aggregate, generated_at=noon("2026-07-29"))
    assert "最新游玩日 2026-07-29" in html
    assert ">20</b><span>主统计样本<" in html
    assert "显示 1 / 2 条分池统计" in html


def test_recent_red_keeps_confidence_gate_and_renders_map_filter():
    noon = lambda d: datetime.fromisoformat(d + "T12:00:00").replace(tzinfo=ps.SHANGHAI_TZ).timestamp()
    rows = [_sample(2401, client_ts=noon("2026-07-12"), counts="q6=5") for _ in range(20)]
    rows += [_sample(2402, client_ts=noon("2026-07-12"), counts="q6=9") for _ in range(19)]
    agg = ps.aggregate_public(rows)
    assert [r["map_id"] for r in agg["recent_red"]["rows"]] == [2401]
    html = ps.build_public_html(agg, generated_at=noon("2026-07-15"), rebuild_interval=1800)
    assert "红最多的图 · 最近 7 天" in html
    assert 'id="rrFilterBtn"' in html and "筛选地图" in html
    assert 'onclick="setRedMap(\\\'\\\')"' in html, "地图筛选脚本的内层引号未转义，会让整段探索 JS 失效"
    assert "最新游玩日 2026-07-12" in html and "页面生成于" in html


def test_public_defaults_to_warm_and_all_sortable_tables_use_priority_stack():
    html = ps.build_public_html(ps.aggregate_public([_sample() for _ in range(20)]), generated_at=0)
    assert 'setAttribute("data-theme",(t==="warm"' in html
    assert "matchMedia" not in html
    assert "data-default-sort-col" in html and "keys=keys.concat" in html
    assert "点其他列=联合排序" in html and "data-sort-indicator" in html


def test_time_trend_table_defaults_to_latest_beijing_day_first_without_reversing_cube():
    old = datetime(2026, 7, 15, 12, tzinfo=ps.SHANGHAI_TZ).timestamp()
    new = datetime(2026, 7, 17, 12, tzinfo=ps.SHANGHAI_TZ).timestamp()
    agg = ps.aggregate_public([
        _sample(client_ts=old),
        _sample(client_ts=new),
    ])
    html = ps.build_public_html(agg, generated_at=new)

    assert agg["cube"]["days"] == ["2026-07-15", "2026-07-17"]
    table_at = html.index('<table data-sortable data-default-sort-col="0" data-default-sort-dir="-1">')
    table_end = html.index("</table>", table_at)
    trend_table = html[table_at:table_end]
    assert trend_table.index("2026-07-17") < trend_table.index("2026-07-15")


# --------------------------------------------------------------------------- #
# 🔴 排位分是身份派生量: 聚合到 1 个人身上的"聚合值"就是原始值。
# --------------------------------------------------------------------------- #
def test_small_tier_never_publishes_exact_rank_points():
    """顶段只有一两个人上传 —— 印出 lo/hi/avg 就等于公开某个人的精确排位分。

    pts_lo/pts_hi 是该档【实际观测到】的分数范围, 不是段位的定义边界。n=1 时三者相等。
    """
    rows = [_sample(2401, rank_tier=32, rank_points=2317)]                      # 顶段, n=1
    rows += [_sample(2401, rank_tier=5, rank_points=1200 + i)                   # 常见档, n 够
             for i in range(ps.MIN_N_FOR_IDENTITY_DERIVED)]
    tiers = {t["tier"]: t for t in ps.aggregate_public(rows)["tiers"]}

    top = tiers[32]
    assert top["n"] < ps.MIN_N_FOR_IDENTITY_DERIVED
    assert top["pts_lo"] is None and top["pts_hi"] is None, "n=1 的档印出了分数区间 = 泄漏个人排位分"
    assert top["avg_pts"] is None, "avg_pts 是同一种泄漏(n=1 时它就是那个人的分数)"

    big = tiers[5]
    assert big["pts_lo"] is not None and big["avg_pts"] is not None, "样本够的档应照常出分数"

    html = ps.build_public_html(ps.aggregate_public(rows), generated_at=0, rebuild_interval=1800)
    assert "2,317" not in html and "2317" not in html, "那个人的精确排位分出现在了公开页上"


# --------------------------------------------------------------------------- #
# 🔴 联合筛选的 cube —— 三条命门。
#   1. cube 里【绝不能】有身份字段(它会被【整个】内联进公开页的 HTML)。
#   2. cube 里【绝不能】有收益/胜率: self_profit = 仓库值 − 赢家出价 − 门票, 而仓库值公开、
#      门票是族常量 ⇒ 逐格发 self_profit 就等于【明文发赢家出价】(实测 99.9% 的局带 winner_bid)。
#      cube 能被筛到 n=1, 所以这不是"n 够大就行"的问题 —— 那两个数根本不能进来。
#   3. cube 必须是【聚合格子】而不是逐局行 —— 否则"客户端只渲染 n≥k"就是自欺欺人(行已经发下去了)。
# --------------------------------------------------------------------------- #
def _cube_of(rows):
    return ps.aggregate_public(rows)["cube"]


def test_cube_contains_no_identity_keys_or_values():
    rows = [_sample(2401, hero="aisha", client_ts=1783771340.0) for _ in range(40)]
    cube = _cube_of(rows)
    bad = [k for k, _ in _walk(cube) if k in ps._FORBIDDEN_KEYS]
    assert not bad, f"cube 里出现了身份字段: {bad}"
    leaked = {"某个昵称", "昵称A", "昵称B", "deadbeef", "0123456789abcdef"}
    vals = {v for _, v in _walk(cube) if isinstance(v, str)}
    assert not (vals & leaked), f"cube 里泄露了身份值: {vals & leaked}"
    nums = {v for _, v in _walk(cube) if isinstance(v, (int, float))}
    assert 600000 not in nums, "赢家出价泄露进了 cube"


def test_cube_carries_no_profit_or_winrate():
    """收益/胜率进了 cube = 明文发出价。这道门要在【有人手滑加进去】的当天就红。"""
    rows = [_sample(2401, client_ts=1783771340.0) for _ in range(40)]
    cube = _cube_of(rows)
    banned = ("profit", "win", "bid", "rank", "sem", "player", "uid")
    hits = [k for k, _ in _walk(cube) if isinstance(k, str)
            and any(b in k.lower() for b in banned)]
    assert not hits, f"cube 里出现了身份派生量(收益/胜率/出价/段位): {hits}"


def test_cube_is_aggregated_cells_not_per_match_rows():
    """同一来源视图内 40 局只成一格；accepted 镜像不能退化成逐局行。"""
    rows = [_sample(2401, hero="aisha", client_ts=1783771340.0) for _ in range(40)]
    cube = _cube_of(rows)
    assert len(cube["n"]) == 2
    assert sorted(cube["n"]) == [40, 40]
    assert {
        cube["sources"][cube["s"][i]] for i in range(len(cube["n"]))
    } == {"accepted", "official"}
    # 不同英雄 -> 不同格(筛选要用), 但仍是格子而不是行
    rows += [_sample(2401, hero="ahmed", client_ts=1783771340.0) for _ in range(40)]
    cube = _cube_of(rows)
    assert len(cube["n"]) == 4 and sorted(cube["n"]) == [40, 40, 40, 40]


def test_cube_sd_is_exactly_recoverable_from_sums():
    """σ = sqrt((Σx² − (Σx)²/n)/(n−1))。客户端就是这么算的 —— 必须与逐局直算一致,
    否则筛选后显示的 ± 是错的(而且错得看不出来)。"""
    import math
    counts = [1, 4, 2, 9, 3, 7, 5, 2, 8, 4]
    rows = [_sample(2401, hero="aisha", client_ts=1783771340.0, counts=f"q6={c}") for c in counts]
    cube = _cube_of(rows)
    n, s, ss = cube["n"][0], cube["qs_q6"][0], cube["qq_q6"][0]
    assert n == len(counts) and s == sum(counts)
    from_cube = math.sqrt((ss - s * s / n) / (n - 1))
    direct = ps._sd([float(c) for c in counts])
    assert abs(from_cube - direct) < 1e-9, f"cube 还原的 σ {from_cube} != 直算 {direct}"


def test_explore_gate_and_cube_are_embedded_in_html():
    rows = [_sample(2401, client_ts=1783771340.0) for _ in range(40)]
    html = ps.build_public_html(ps.aggregate_public(rows), generated_at=0, rebuild_interval=1800)
    assert "var CUBE=" in html and f"MIN_N={ps.MIN_SAMPLES_TO_SHOW}" in html
    assert 'id="flt"' in html and 'id="expl"' in html
    # </script> 必须被转义, 否则 cube 里任何一个 "</..." 都会提前闭合 <script> 标签把页面撕开
    assert "</script>" not in html.split("var CUBE=")[1].split("</script>")[0] + ""


def test_day_never_leaks_the_raw_timestamp():
    """client_ts 是精确时间戳(强准标识符, 且在 _FORBIDDEN_KEYS 里) —— 只能取到【日期】粒度。"""
    ts = 1783771340.6
    rows = [_sample(2401, client_ts=ts) for _ in range(40)]
    cube = _cube_of(rows)
    assert len(cube["days"]) == 1
    assert len(cube["days"][0]) == 10 and cube["days"][0].count("-") == 2   # YYYY-MM-DD
    tree = repr(cube)
    assert "1783771340" not in tree, "原始时间戳泄漏进了 cube"


def test_public_calendar_and_generated_time_use_shanghai_timezone():
    """UTC 16:00 是北京时间次日 00:00；统计日和页面生成时间必须跨机器一致。"""
    before = datetime(2026, 7, 15, 15, 59, 59, tzinfo=timezone.utc).timestamp()
    after = datetime(2026, 7, 15, 16, 0, 0, tzinfo=timezone.utc).timestamp()
    assert ps._day_of(_sample(client_ts=before)) == "2026-07-15"
    assert ps._day_of(_sample(client_ts=after)) == "2026-07-16"

    html = ps.build_public_html(
        ps.aggregate_public([_sample(client_ts=after) for _ in range(20)]), generated_at=after
    )
    assert "页面生成于 07-16 00:00(北京时间)" in html
    assert "日期(北京)" in html


def test_public_day_accepts_only_exact_finite_builtin_epoch_without_magic():
    class ExplodingInt(int):
        magic_calls = 0

        def __float__(self):
            type(self).magic_calls += 1
            raise RuntimeError("client timestamp magic must not run")

        def __ge__(self, _other):
            type(self).magic_calls += 1
            raise RuntimeError("client timestamp comparison must not run")

    class ExplodingFloat(float):
        magic_calls = 0

        def __float__(self):
            type(self).magic_calls += 1
            raise RuntimeError("client timestamp magic must not run")

        def __ge__(self, _other):
            type(self).magic_calls += 1
            raise RuntimeError("client timestamp comparison must not run")

    valid = datetime(2026, 7, 29, 12, tzinfo=ps.SHANGHAI_TZ).timestamp()
    assert ps._day_of(_sample(client_ts=int(valid))) == "2026-07-29"
    assert ps._day_of(_sample(client_ts=float(valid))) == "2026-07-29"

    invalid = (
        True,
        False,
        ExplodingInt(int(valid)),
        ExplodingFloat(valid),
        float("nan"),
        float("inf"),
        float("-inf"),
        10**400,
        -(10**400),
        253_402_300_800,  # 10000-01-01T00:00:00Z，超出 datetime 可表示范围。
    )
    assert all(ps._day_of(_sample(client_ts=value)) is None for value in invalid)
    assert ExplodingInt.magic_calls == 0
    assert ExplodingFloat.magic_calls == 0


def test_legacy_source_cutoff_reuses_the_safe_client_ts_projection():
    class NumericIntSubclass(int):
        pass

    class NumericFloatSubclass(float):
        pass

    class ExplodingFloat(float):
        magic_calls = 0

        def __float__(self):
            type(self).magic_calls += 1
            raise RuntimeError("client timestamp conversion must not run")

        def __ge__(self, _other):
            type(self).magic_calls += 1
            raise RuntimeError("client timestamp comparison must not run")

        def __lt__(self, _other):
            type(self).magic_calls += 1
            raise RuntimeError("client timestamp comparison must not run")

        def __eq__(self, _other):
            type(self).magic_calls += 1
            raise RuntimeError("client timestamp comparison must not run")

    cutoff = ps._PLAYER_WAREHOUSE_ACTIVITY_START_UTC_TS
    before = math.nextafter(cutoff, -math.inf)
    after = math.nextafter(cutoff, math.inf)
    legacy_profile = {"warehouse_policy": "player_possible"}
    assert ps._source_scope(_sample(client_ts=before), legacy_profile) == "official"
    assert ps._source_scope(_sample(client_ts=cutoff), legacy_profile) == "unknown"
    assert ps._source_scope(_sample(client_ts=after), legacy_profile) == "unknown"

    baseline_rows = [
        *(_sample(2401, client_ts=before, counts="q6=2") for _ in range(20)),
        *(_sample(2401, client_ts=after, counts="q6=3") for _ in range(20)),
    ]
    baseline = ps.aggregate_public(baseline_rows)
    baseline_html = ps.build_public_html(
        baseline, generated_at=after, rebuild_interval=1800
    )
    invalid = (
        True,
        False,
        NumericIntSubclass(int(after)),
        NumericFloatSubclass(after),
        Decimal(str(after)),
        Fraction(int(after), 1),
        float("nan"),
        float("inf"),
        float("-inf"),
        10**400,
        -(10**400),
        253_402_300_800,
        ExplodingFloat(after),
    )
    for value in invalid:
        assert ps._day_of(_sample(client_ts=value)) is None
        assert ps._source_scope(_sample(client_ts=value), legacy_profile) is None
        rows = [
            *baseline_rows,
            *(
                _sample(
                    2401,
                    client_ts=value,
                    received_ts=after,
                    counts="q6=99",
                )
                for _ in range(20)
            ),
        ]
        for ordered in (rows, list(reversed(rows))):
            aggregate = ps.aggregate_public(ordered)
            assert aggregate == baseline
            assert ps.build_public_html(
                aggregate, generated_at=after, rebuild_interval=1800
            ) == baseline_html

    assert ExplodingFloat.magic_calls == 0


def test_invalid_client_ts_never_advances_any_source_window_after_reorder():
    class NumericSubclass(float):
        pass

    class ExplodingFloat(float):
        magic_calls = 0

        def __float__(self):
            type(self).magic_calls += 1
            raise RuntimeError("client timestamp magic must not run")

        def __ge__(self, _other):
            type(self).magic_calls += 1
            raise RuntimeError("client timestamp comparison must not run")

    def noon(day):
        return datetime.fromisoformat(day + "T12:00:00").replace(
            tzinfo=ps.SHANGHAI_TZ
        ).timestamp()

    scopes = {
        "official": ("official_random_warehouse", True, noon("2026-07-10")),
        "player_custom": ("player_custom_warehouse", False, noon("2026-07-11")),
        "unknown": ("postupdate_unknown_warehouse_source", False, noon("2026-07-12")),
        "conflict": ("official_random_warehouse", False, noon("2026-07-13")),
    }
    valid_rows = []
    for qualification, eligible, timestamp in scopes.values():
        valid_rows.extend(
            _sample(
                2401,
                client_ts=timestamp,
                counts="q6=2",
                warehouse_source_qualification=qualification,
                official_distribution_eligible=eligible,
            )
            for _ in range(ps.MIN_SAMPLES_TO_SHOW)
        )

    invalid_timestamps = (
        True,
        NumericSubclass(noon("2026-08-29")),
        float("inf"),
        ExplodingFloat(noon("2026-08-30")),
    )
    invalid_rows = []
    for (qualification, eligible, _timestamp), invalid_ts in zip(
        scopes.values(), invalid_timestamps
    ):
        invalid_rows.extend(
            _sample(
                2401,
                client_ts=invalid_ts,
                counts="q6=99",
                warehouse_source_qualification=qualification,
                official_distribution_eligible=eligible,
            )
            for _ in range(ps.MIN_SAMPLES_TO_SHOW)
        )

    baseline = ps.aggregate_public(valid_rows)
    baseline_html = ps.build_public_html(
        baseline, generated_at=noon("2026-07-29"), rebuild_interval=1800
    )
    baseline_recent = {
        scope: ps.recent_red_maps_from_cube(
            baseline["cube"], source_scope=scope
        )
        for scope in scopes
    }
    for ordered in (
        valid_rows + invalid_rows,
        list(reversed(invalid_rows + valid_rows)),
    ):
        aggregate = ps.aggregate_public(ordered)
        assert aggregate == baseline
        assert aggregate["cube"] == baseline["cube"]
        assert aggregate["trend"] == baseline["trend"]
        assert {
            scope: ps.recent_red_maps_from_cube(
                aggregate["cube"], source_scope=scope
            )
            for scope in scopes
        } == baseline_recent
        html = ps.build_public_html(aggregate, generated_at=noon("2026-07-29"))
        assert html == baseline_html
        assert "1970-01-01" not in html
        assert "2026-08-29" not in html
        assert "2026-08-30" not in html

    assert ExplodingFloat.magic_calls == 0


def test_public_aggregate_rejects_protocol_rows_before_any_named_lookup():
    class ExplodingRow(dict):
        magic_calls = 0

        @classmethod
        def _explode(cls, operation):
            cls.magic_calls += 1
            raise RuntimeError(f"row protocol must not run: {operation}")

        def get(self, _key, _default=None):
            return type(self)._explode("get")

        def items(self):
            return type(self)._explode("items")

        def __iter__(self):
            return type(self)._explode("iter")

        def __contains__(self, _key):
            return type(self)._explode("contains")

    valid_rows = [
        _sample(2401, client_ts=1783771340.0, counts="q6=2")
        for _ in range(ps.MIN_SAMPLES_TO_SHOW)
    ]
    invalid_rows = [
        ExplodingRow(
            _sample(2401, client_ts=1783771340.0, counts="q6=99")
        )
        for _ in range(ps.MIN_SAMPLES_TO_SHOW)
    ]
    baseline = ps.aggregate_public(valid_rows)
    baseline_html = ps.build_public_html(
        baseline, generated_at=1783771340.0, rebuild_interval=1800
    )

    for ordered in (
        valid_rows + invalid_rows,
        list(reversed(invalid_rows + valid_rows)),
    ):
        aggregate = ps.aggregate_public(ordered)
        assert aggregate == baseline
        assert aggregate["cube"] == baseline["cube"]
        assert aggregate["trend"] == baseline["trend"]
        assert ps.build_public_html(
            aggregate, generated_at=1783771340.0, rebuild_interval=1800
        ) == baseline_html

    assert ExplodingRow.magic_calls == 0


def test_public_aggregate_rejects_colliding_non_plain_keys_without_protocols():
    class CollidingKey(str):
        magic_calls = 0

        def __hash__(self):
            type(self).magic_calls += 1
            return hash("client_ts")

        def __eq__(self, _other):
            type(self).magic_calls += 1
            raise RuntimeError("row key equality must not run")

    valid_rows = [
        _sample(2401, client_ts=1783771340.0, counts="q6=2")
        for _ in range(ps.MIN_SAMPLES_TO_SHOW)
    ]
    colliding_rows = []
    for _ in range(ps.MIN_SAMPLES_TO_SHOW):
        row = _sample(2401, counts="q6=99")
        row.pop("client_ts")
        row[CollidingKey("not_client_ts")] = 1783771340.0
        colliding_rows.append(row)
    CollidingKey.magic_calls = 0

    missing_time = _sample(2401, counts="q6=99")
    missing_time.pop("client_ts")
    invalid_time = _sample(2401, client_ts=True, counts="q6=99")
    baseline = ps.aggregate_public(valid_rows)
    baseline_html = ps.build_public_html(
        baseline, generated_at=1783771340.0, rebuild_interval=1800
    )

    for ordered in (
        valid_rows + colliding_rows + [missing_time, invalid_time],
        list(reversed(colliding_rows + [missing_time, invalid_time] + valid_rows)),
    ):
        aggregate = ps.aggregate_public(ordered)
        assert aggregate == baseline
        assert aggregate["cube"] == baseline["cube"]
        assert aggregate["trend"] == baseline["trend"]
        assert ps.build_public_html(
            aggregate, generated_at=1783771340.0, rebuild_interval=1800
        ) == baseline_html

    assert CollidingKey.magic_calls == 0


def test_source_driving_values_are_stable_before_scope_classification():
    class ExplodingQualification(str):
        magic_calls = 0

        @classmethod
        def _explode(cls, operation):
            cls.magic_calls += 1
            raise RuntimeError(f"qualification protocol must not run: {operation}")

        def __eq__(self, _other):
            return type(self)._explode("eq")

        def __hash__(self):
            return type(self)._explode("hash")

        def __bool__(self):
            return type(self)._explode("bool")

        def __str__(self):
            return type(self)._explode("str")

    class ExplodingEligible:
        magic_calls = 0

        @classmethod
        def _explode(cls, operation):
            cls.magic_calls += 1
            raise RuntimeError(f"eligibility protocol must not run: {operation}")

        def __eq__(self, _other):
            return type(self)._explode("eq")

        def __bool__(self):
            return type(self)._explode("bool")

        def __str__(self):
            return type(self)._explode("str")

    class ExplodingMapId:
        magic_calls = 0

        @classmethod
        def _explode(cls, operation):
            cls.magic_calls += 1
            raise RuntimeError(f"map id protocol must not run: {operation}")

        def __int__(self):
            return type(self)._explode("int")

        def __index__(self):
            return type(self)._explode("index")

        def __str__(self):
            return type(self)._explode("str")

    played = datetime.fromisoformat("2026-07-25T12:00:00").replace(
        tzinfo=ps.SHANGHAI_TZ
    ).timestamp()
    scopes = (
        ("official_random_warehouse", True),
        ("player_custom_warehouse", False),
        ("postupdate_unknown_warehouse_source", False),
        ("official_random_warehouse", False),
    )
    valid_rows = [
        _sample(
            2401,
            client_ts=played,
            counts=f"q6={index + 1}",
            warehouse_source_qualification=qualification,
            official_distribution_eligible=eligible,
        )
        for index, (qualification, eligible) in enumerate(scopes)
        for _ in range(ps.MIN_SAMPLES_TO_SHOW)
    ]
    invalid_rows = [
        *(
            _sample(
                2401,
                client_ts=played,
                counts="q6=9",
                warehouse_source_qualification=ExplodingQualification(
                    "official_random_warehouse"
                ),
                official_distribution_eligible=True,
            )
            for _ in range(ps.MIN_SAMPLES_TO_SHOW)
        ),
        *(
            _sample(
                2401,
                client_ts=played,
                counts="q6=8",
                warehouse_source_qualification="official_random_warehouse",
                official_distribution_eligible=ExplodingEligible(),
            )
            for _ in range(ps.MIN_SAMPLES_TO_SHOW)
        ),
        *(
            _sample(
                2401,
                client_ts=played,
                counts="q6=7",
                warehouse_source_qualification="unsupported_source",
                official_distribution_eligible=False,
            )
            for _ in range(ps.MIN_SAMPLES_TO_SHOW)
        ),
        *(
            _sample(
                2401,
                client_ts=played,
                counts="q6=6",
                warehouse_source_qualification=None,
                official_distribution_eligible=1,
            )
            for _ in range(ps.MIN_SAMPLES_TO_SHOW)
        ),
    ]
    conflict_rows = [
        {
            **row,
            "warehouse_source_qualification": "official_random_warehouse",
            "official_distribution_eligible": False,
        }
        for row in invalid_rows
    ]
    invalid_map_rows = [
        _sample(
            ExplodingMapId(),
            client_ts=played,
            counts="q6=99",
            warehouse_source_qualification="official_random_warehouse",
            official_distribution_eligible=True,
        )
        for _ in range(ps.MIN_SAMPLES_TO_SHOW)
    ]
    baseline = ps.aggregate_public(valid_rows + conflict_rows)
    baseline_html = ps.build_public_html(
        baseline, generated_at=played, rebuild_interval=1800
    )

    for ordered in (
        valid_rows + invalid_rows + invalid_map_rows,
        list(reversed(invalid_map_rows + invalid_rows + valid_rows)),
    ):
        aggregate = ps.aggregate_public(ordered)
        assert aggregate == baseline
        assert aggregate["cube"] == baseline["cube"]
        assert aggregate["trend"] == baseline["trend"]
        assert ps.build_public_html(
            aggregate, generated_at=played, rebuild_interval=1800
        ) == baseline_html

    assert ExplodingQualification.magic_calls == 0
    assert ExplodingEligible.magic_calls == 0
    assert ExplodingMapId.magic_calls == 0


def test_public_map_id_uses_one_bounded_ascii_projection_without_magic():
    class ExplodingInt(int):
        magic_calls = 0

        def __str__(self):
            type(self).magic_calls += 1
            raise RuntimeError("map id str protocol must not run")

        def __int__(self):
            type(self).magic_calls += 1
            raise RuntimeError("map id int protocol must not run")

    class ExplodingStr(str):
        magic_calls = 0

        def __str__(self):
            type(self).magic_calls += 1
            raise RuntimeError("map id str protocol must not run")

        def __eq__(self, _other):
            type(self).magic_calls += 1
            raise RuntimeError("map id comparison protocol must not run")

    invalid = (
        True,
        False,
        0,
        -1,
        ps._PUBLIC_MAP_ID_MAX + 1,
        10**5000,
        "",
        "0",
        "-1",
        "+2401",
        " 2401",
        "2401 ",
        "٢٤٠١",
        "２４０１",
        "1" * 4299,
        "1" * 4300,
        "1" * 4301,
        ExplodingInt(2401),
        ExplodingStr("2401"),
        {"value": 2401},
        Decimal("2401"),
        Fraction(2401, 1),
    )
    assert ps._normalize_public_map_id(2401) == 2401
    assert ps._normalize_public_map_id("2401") == 2401
    assert ps._normalize_public_map_id("00002401") == 2401
    assert (
        ps._normalize_public_map_id(ps._PUBLIC_MAP_ID_MAX)
        == ps._PUBLIC_MAP_ID_MAX
    )
    assert (
        ps._normalize_public_map_id(str(ps._PUBLIC_MAP_ID_MAX))
        == ps._PUBLIC_MAP_ID_MAX
    )
    assert all(
        ps._normalize_public_map_id(value) is ps._SOURCE_VALUE_INVALID
        for value in invalid
    )
    assert ExplodingInt.magic_calls == 0
    assert ExplodingStr.magic_calls == 0


def test_public_map_id_is_consistent_across_direct_json_cache_and_html(tmp_path):
    played = datetime.fromisoformat("2026-07-25T12:00:00").replace(
        tzinfo=ps.SHANGHAI_TZ
    ).timestamp()
    valid_int_rows = [
        _sample(
            9999,
            client_ts=played,
            counts="q6=2",
            warehouse_source_qualification="official_random_warehouse",
            official_distribution_eligible=True,
        )
        for _ in range(ps.MIN_SAMPLES_TO_SHOW)
    ]
    valid_text_rows = [
        {**row, "map_id": "9999"} for row in valid_int_rows
    ]
    invalid_direct_rows = [
        _sample(
            10**5000,
            client_ts=played,
            counts="q6=99",
            warehouse_source_qualification="official_random_warehouse",
            official_distribution_eligible=True,
        ),
        _sample(
            "1" * 5000,
            client_ts=played,
            counts="q6=98",
            warehouse_source_qualification="official_random_warehouse",
            official_distribution_eligible=True,
        ),
    ]
    invalid_json_rows = [
        json.loads(json.dumps({**row, "map_id": "1" * 5000}, ensure_ascii=False))
        for row in valid_int_rows[:2]
    ]

    baseline = ps.aggregate_public(valid_int_rows)
    baseline_html = ps.build_public_html(
        baseline, generated_at=played, rebuild_interval=1800
    )
    cached = json.loads(json.dumps(baseline, ensure_ascii=False))
    assert cached == baseline
    assert ps.build_public_html(
        cached, generated_at=played, rebuild_interval=1800
    ) == baseline_html
    assert baseline["maps"][0]["map_id"] == 9999
    assert baseline["maps"][0]["name"] == "9999"

    jsonl_path = tmp_path / "public-map-id.jsonl"
    jsonl_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in valid_text_rows + invalid_json_rows
        ),
        encoding="utf-8",
    )
    from_jsonl = ps.aggregate_public(ps.load_samples(jsonl_path))
    assert from_jsonl == baseline
    assert ps.build_public_html(
        from_jsonl, generated_at=played, rebuild_interval=1800
    ) == baseline_html

    variants = (
        valid_text_rows,
        valid_int_rows + invalid_direct_rows,
        list(reversed(invalid_direct_rows + valid_int_rows)),
        valid_text_rows + invalid_json_rows,
        list(reversed(invalid_json_rows + valid_text_rows)),
    )
    for rows in variants:
        aggregate = ps.aggregate_public(rows)
        assert aggregate == baseline
        assert aggregate["cube"] == baseline["cube"]
        assert aggregate["trend"] == baseline["trend"]
        assert ps.build_public_html(
            aggregate, generated_at=played, rebuild_interval=1800
        ) == baseline_html


def test_trend_buckets_by_play_time_not_upload_time():
    """🔴 趋势的时间轴必须是【游玩时间 client_ts】, 不是【上传时间 received_ts】。

    这一节唯一的用途是回答"官方哪天调了掉率" —— 掉落发生在打这局的时候, 不是服务器收到上传的时候。
    按上传时间分桶, 一批补传的旧数据会全砸进上传那天, 【正好在这张图该检测跳变的地方伪造出一个跳变】。

    构造: 全部在【7/01 打完】, 但分两批上传(7/01 和 7/08)。
      · 按游玩时间 -> 1 天(正确: 掉率是那天的)
      · 按上传时间 -> 2 天(错误: 凭空多出一个"7/08 掉率变了")
    """
    played = 1783771340.6                        # 某一天打的
    late_upload = played + 86400 * 7             # 一周后才传上来
    rows = [_sample(2401, client_ts=played, received_ts=played) for _ in range(30)]
    rows += [_sample(2401, client_ts=played, received_ts=late_upload) for _ in range(30)]

    agg = ps.aggregate_public(rows)
    days = [d["day"] for d in agg["trend"]]
    assert len(days) == 1, f"趋势按【上传时间】分桶了 —— 同一天打的 60 局被拆成 {len(days)} 天: {days}"
    assert agg["trend"][0]["n"] == 60

    # cube(图) 必须和 trend(表) 是同一批天 —— 同一节里图表日期对不上, 读者只会以为坏了
    assert agg["cube"]["days"] == days, "趋势图和趋势表的日期范围不一致"
