# -*- coding: utf-8 -*-
"""公共大数据(0.2.7)—— 从上传样本聚合出的【去标识化】客观统计,公开给所有人看。

与 /dashboard 的根本区别:
  · /dashboard = 内部分析看板,逐玩家昵称、逐局明细(作者自己用,Basic Auth 锁死)。
  · /public    = 公开页,**只有聚合数字,绝不出现任何身份**(昵称/uid/session/赢家/出价)。
    这是"你传数据 → 你得到更准的表"的回馈闭环,也是让更多人愿意开上传的理由。

⚠️【隐私红线】aggregate_public() 的输出**只能是聚合数字**。任何身份字段(player/players/
winner/winner_bid/uid_hash/session_hash/client_ts/received_ts)都不得出现在结果里 —— 哪怕是
间接的(如"只有 1 个样本的地图"会让该局可被反推)。设计门 test_public_stats.py 有硬断言守着。

小样本会骗人: 一张图只有 3 局样本时,"红掉率 33%"毫无意义。故每行都带样本量 n,且低于
MIN_SAMPLES_TO_SHOW 的地图单独归入"样本不足"区,不与高置信数据混排。
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, NamedTuple

_SCRIPTS = Path(__file__).resolve().parent

# 产品统计的统一日历口径。原始时间戳仍保存 Unix epoch；这里只在展示和按自然日聚合时
# 固定转成北京时间，避免 VPS 系统时区或访问者浏览器时区改变同一局所属的日期。
SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
_PLAYER_WAREHOUSE_ACTIVITY_START_UTC_TS = datetime(
    2026, 7, 22, 16, tzinfo=timezone.utc
).timestamp()
_OFFICIAL_RANDOM_WAREHOUSE = "official_random_warehouse"
_PLAYER_CUSTOM_WAREHOUSE = "player_custom_warehouse"
_UNKNOWN_WAREHOUSE_SOURCE = "postupdate_unknown_warehouse_source"
_MAIN_SOURCE_SCOPE = "accepted"
_SOURCE_SCOPES = ("official", "player_custom", "unknown", "conflict")
_MAIN_INCLUDED_SOURCE_SCOPES = frozenset({"official", "player_custom", "unknown"})
_PUBLIC_SOURCE_SCOPES = (_MAIN_SOURCE_SCOPE, *_SOURCE_SCOPES)
_SOURCE_SCOPE_LABELS = {
    _MAIN_SOURCE_SCOPE: "主统计（已接纳）",
    "official": "官方随机仓",
    "player_custom": "玩家仓",
    "unknown": "来源待确认",
    "conflict": "来源冲突",
}
_CLIENT_TS_UNSET = object()
_SOURCE_VALUE_INVALID = object()
# Public payloads are JSON and the map id is also consumed as a JavaScript number.
# Keep unknown future ids, but never emit a value that cannot round-trip exactly
# through that product path.
_PUBLIC_MAP_ID_MAX = (1 << 53) - 1
_PUBLIC_MAP_ID_MAX_TEXT_DIGITS = len(str(_PUBLIC_MAP_ID_MAX))
_SOURCE_QUALIFICATIONS = frozenset(
    {
        _OFFICIAL_RANDOM_WAREHOUSE,
        _PLAYER_CUSTOM_WAREHOUSE,
        _UNKNOWN_WAREHOUSE_SOURCE,
    }
)


class _StablePublicRow(NamedTuple):
    row: dict
    client_ts_epoch: float
    map_id: int | None | object
    source_qualification: str | None | object
    source_eligible: bool | None | object


def _shanghai_datetime(ts: float) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=SHANGHAI_TZ)
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def _shanghai_day(ts: float) -> str | None:
    dt = _shanghai_datetime(ts)
    return dt.strftime("%Y-%m-%d") if dt else None


def _shanghai_format(ts: float, fmt: str) -> str | None:
    dt = _shanghai_datetime(ts)
    return dt.strftime(fmt) if dt else None


def _normalize_client_ts_scalar(value: object) -> float | None:
    """Normalize an already extracted play timestamp without value protocols."""
    if type(value) not in (int, float):
        return None
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(normalized):
        return None
    if _shanghai_datetime(normalized) is None:
        return None
    return normalized


def _normalize_public_map_id(value: object) -> int | None | object:
    if value is None:
        return None
    if type(value) is int:  # noqa: E721 - reject bool and protocol-bearing subclasses
        return value if 0 < value <= _PUBLIC_MAP_ID_MAX else _SOURCE_VALUE_INVALID
    if type(value) is not str:  # noqa: E721 - only a plain JSON string is stable
        return _SOURCE_VALUE_INVALID
    if (
        not value
        or len(value) > _PUBLIC_MAP_ID_MAX_TEXT_DIGITS
        or re.fullmatch(r"[0-9]+", value, flags=re.ASCII) is None
    ):
        return _SOURCE_VALUE_INVALID
    try:
        normalized = int(value, 10)
    except (OverflowError, TypeError, ValueError):
        return _SOURCE_VALUE_INVALID
    return (
        normalized
        if 0 < normalized <= _PUBLIC_MAP_ID_MAX
        else _SOURCE_VALUE_INVALID
    )


def _normalize_source_qualification(value: object) -> str | None | object:
    if value is None:
        return None
    if type(value) is not str or value not in _SOURCE_QUALIFICATIONS:
        return _SOURCE_VALUE_INVALID
    return value


def _normalize_source_eligible(value: object) -> bool | None | object:
    if value is None:
        return None
    if type(value) is not bool:
        return _SOURCE_VALUE_INVALID
    return value


def _stable_public_row(rec: object) -> _StablePublicRow | None:
    """Validate one public row before any named lookup.

    An exact ``dict`` can still contain a ``str`` subclass whose hash collides
    with a field name.  Calling ``row.get("client_ts")`` first would then invoke
    user ``__eq__``.  Iterate the builtin storage first and reject every
    non-exact key; only afterwards is a named lookup safe.
    """
    if type(rec) is not dict:
        return None
    for key in dict.__iter__(rec):
        if type(key) is not str:
            return None
    # 主库调用方通常不会传入 quarantine 行，但公开聚合必须对显式隔离标记
    # fail-closed；只做 identity 判断，不执行 reason 值的字符串/布尔协议。
    if dict.get(rec, "server_quarantine_reason") is not None:
        return None
    normalized = _normalize_client_ts_scalar(dict.get(rec, "client_ts"))
    if normalized is None:
        return None
    return _StablePublicRow(
        row=rec,
        client_ts_epoch=normalized,
        map_id=_normalize_public_map_id(dict.get(rec, "map_id")),
        source_qualification=_normalize_source_qualification(
            dict.get(rec, "warehouse_source_qualification")
        ),
        source_eligible=_normalize_source_eligible(
            dict.get(rec, "official_distribution_eligible")
        ),
    )


def _client_ts_epoch(rec: object) -> float | None:
    """Project the play timestamp once after stabilizing the whole row.

    Public source qualification and calendar windows must agree on the exact same
    scalar.  In particular, bool/numeric subclasses/Decimal-like objects must not
    be accepted by one consumer after the other consumer has rejected them.
    """
    stable = _stable_public_row(rec)
    return stable.client_ts_epoch if stable is not None else None


def _source_scope(
    rec: object,
    map_meta: dict | None = None,
    *,
    client_ts_epoch: float | object = _CLIENT_TS_UNSET,
    stable_row: _StablePublicRow | None = None,
) -> str | None:
    """Classify one row without confusing map policy with row provenance.

    Trusted map metadata can prove that ranked, sealed, and registered activity
    maps have no player warehouse even when an old client omitted source fields.
    It must never silently bless a contradictory explicit player declaration.
    """
    stable = stable_row if stable_row is not None else _stable_public_row(rec)
    if stable is None:
        return None
    if client_ts_epoch is _CLIENT_TS_UNSET:
        client_ts_epoch = stable.client_ts_epoch
    if type(client_ts_epoch) is not float:
        return None
    qualification = stable.source_qualification
    eligible = stable.source_eligible
    if qualification is _SOURCE_VALUE_INVALID or eligible is _SOURCE_VALUE_INVALID:
        return "conflict"
    if qualification == _OFFICIAL_RANDOM_WAREHOUSE and eligible is True:
        return "official"
    policy = (map_meta or {}).get("warehouse_policy")
    if qualification == _PLAYER_CUSTOM_WAREHOUSE and eligible is False:
        return "conflict" if policy == "official_only" else "player_custom"
    if qualification == _UNKNOWN_WAREHOUSE_SOURCE and eligible is False:
        return "official" if policy == "official_only" else "unknown"
    if qualification is not None or eligible is not None:
        return "conflict"
    if policy == "official_only":
        return "official"
    return (
        "unknown"
        if client_ts_epoch >= _PLAYER_WAREHOUSE_ACTIVITY_START_UTC_TS
        else "official"
    )


def _source_is_mixed_sample(rec: object, map_meta: dict | None = None) -> bool:
    """Compatibility predicate: any non-official scope needs a visible warning."""
    return _source_scope(rec, map_meta) != "official"

# 样本量低于此值的地图 -> 归入"样本不足", 不参与主排行(避免 3 局就报"红掉率 33%"这种噪音)
MIN_SAMPLES_TO_SHOW = 20

# 【身份派生统计】的最小样本量。胜率/收益是从 winner/player/winner_bid 算出来的 ——
# 单看聚合数没有身份, 但 n 太小时"某英雄 3 局 0 胜"几乎就等于把某个人的战绩公开了,
# 且统计上也纯是噪音。低于此值 -> 该行的胜率/收益一律不出(显示 —), 只留掉率类客观统计。
MIN_N_FOR_IDENTITY_DERIVED = 30

# 时间趋势表里, 一天要有多少局才配支撑"官方调掉率了"这种判断。
# 依据: 红的单局标准差 σ≈2.7 件。要在 ±0.5 件的分辨率上分辨真跳变, 需要 SEM≤0.25
# ⇒ n ≥ (2.7/0.25)² ≈ 117。取 100 —— 低于此的天照常显示但【置灰+标"样本少"】。
# 血泪: 上线首版没设这个门, 开服头几天的 n=1 / n=3 的天(红 2.00 / 3.00)和 n=659 的天
# (红 3.60)并排列着, 制造出一个根本不存在的"6/28 掉率跳变" —— 而这张表【唯一的用途】
# 就是发现跳变。不设门 = 这张表主动骗人。
MIN_N_FOR_TREND_DAY = 100

# 绝不允许出现在公开输出里的字段(隐私红线; 设计门会扫描整个输出树)。
# ⚠️注意区分【用】和【出】: 胜率/收益/段位分布是【用】winner/player/rank_tier 算出来的聚合数,
# 这是允许的; 但这些【原始字段本身】绝不能出现在输出树里。设计门扫的是输出, 不是输入。
_FORBIDDEN_KEYS = frozenset({
    "player", "players", "winner", "winner_bid", "self_bid", "winner_is_self",
    "settlement_welfare", "uid_hash", "session_hash",
    "player_ranks", "rank_points", "rank_tier", "rank_delta", "rank_after",
    "client_ts", "received_ts", "ref_inputs", "tools", "hskills",
})

# 展示口径: 【绿(q2) 并入白(q1)】—— 与 /dashboard 的 _QKEY 同一套, 游戏里也是这么分的。
# ⚠️ 所以这里【故意没有 q2】。绿不是被丢掉, 是在 _qmap 里就已经加进 q1 了。
_QUALITY_LABELS = {
    "q1": "白", "q3": "蓝", "q4": "紫", "q5": "金", "q6": "红",
}

# 线上字段 -> 展示档。绿并入白。改这里前先看 sample_dashboard._QKEY —— 两边必须一致。
_QKEY = {"q1": "q1", "q2": "q1", "q3": "q3", "q4": "q4", "q5": "q5", "q6": "q6"}

# 段位: rank_tier(1..32) -> 名字。结构从 4.5 万条真实数据反推(见 sample_dashboard.tierName 的注释):
# 档位 1~5 = 3~1200 分(每档 ~100 宽; 档位5 中位 = 1200 = 账号初始积分, 故占比特别高, 属正常),
# 档位 6~30 = 1201~2200(每档正好 40 宽), 档位 31 = 竞拍大师, 档位 32 = 竞拍之王。
# 32 = 6 大段位 × 5 小段 + 2 个无小段的顶段, 与游戏公示的 8 大段位严丝合缝。
# 小段位【1 = 该大段位最高】(和英雄联盟对齐)。
_RANK_BIG = ("黑铁", "青铜", "白银", "黄金", "白金", "钻石")


def tier_name(t: int) -> str:
    t = int(t)
    if t == 31:
        return "竞拍大师"
    if t >= 32:
        return "竞拍之王"
    if 1 <= t <= 30:
        return f"{_RANK_BIG[(t - 1) // 5]}{5 - ((t - 1) % 5)}"
    return f"段位 {t}"


# 英雄 slug -> 中文名。样本里的 `hero` 存的是英文 slug(inference.observation.HeroMode),
# 但公开页是给群友看的 —— 他们只认游戏里的中文名, 不认 "wuqilin"/"chenmei"。
# ⚠️不要用"HeroMode 的顺序恰好和 hero_skills 的 id 顺序一致"来 zip 生成这张表 ——
# 那是巧合, 不是契约。这里显式写死, 并由 test_public_hero_names 逐个盯住:
# 每个 HeroMode slug 都必须在这里有名字, 新增英雄漏配会直接把测试打红。
_HERO_CN = {
    "fatima": "法蒂玛", "chenmei": "陈美", "aisha": "艾莎", "gabriela": "加布里埃拉",
    "tatiana": "塔蒂安娜", "naomi": "娜奥米", "sophie": "索菲", "maria": "玛丽亚",
    "helena": "海琳娜", "isabella": "伊莎贝拉", "george": "乔治", "carlos": "卡洛斯",
    "leonard": "莱昂纳德", "ahmed": "艾哈迈德", "ivan": "伊万", "takeda": "武田宏志",
    "wuqilin": "吴起灵", "ethan": "伊森", "victor": "维克托", "raven": "拉文",
    "liss": "莉丝", "indiana": "印第安纳",
}


def hero_name(slug: str) -> str:
    """英文 slug -> 中文名。未知/缺失 -> "未识别"(样本里 hero 抓不到时会落成 '?')。"""
    s = str(slug or "").strip()
    if not s or s == "?":
        return "未识别"
    return _HERO_CN.get(s, s)


def _sample_hero_slug(value: Any) -> str:
    """把采集样本英雄值规范成公开/私版共用 slug。

    06-25 新增的莉丝尚未进入旧客户端英雄表，因此 0713 云快照中的 100 局莉丝均落成
    ``null``（聚合后原显示 ``? / 未识别``）。作者 07-15 确认该整桶即莉丝；在采集端拿到
    可验证的 wire hero_id 前，保留原始样本不改写，只在展示聚合边界规范成 ``liss``。
    """
    s = str(value or "").strip()
    return "liss" if not s or s == "?" else s


def _sd(vals: list[float]) -> float | None:
    """样本标准差(n-1)。均值只说"平均开几个红", 方差才说"这图稳不稳" ——
    均红 2.0±0.4 和 2.0±2.5 是完全不同的图(后者要么空要么爆)。

    ⚠️ n<2 -> None(【算不出】), 不是 0.0。返回 0 会在页面上印成 "±0.00" ——
    读起来像"这张图每局雷打不动开 3 个红", 实际只是【只有一个样本】。渲染层把 None 印成 "±?"。
    """
    n = len(vals)
    if n < 2:
        return None
    m = sum(vals) / n
    return (sum((v - m) ** 2 for v in vals) / (n - 1)) ** 0.5


def _rsd(vals: list[float], nd: int = 2) -> float | None:
    """_sd 的四舍五入版(None 透传 —— round(None) 会抛)。"""
    sd = _sd(vals)
    return None if sd is None else round(sd, nd)


def _sem(vals: list[float]) -> float | None:
    """均值的标准误 = σ/√n。n<2 -> None(算不出)。

    收益是重尾的(单局盈亏几十万级), 均值本身带着很大的不确定度。只印一个 "均收益 +38,635"
    会被读成"这一档真的赚" —— 而它可能只是噪音。印上 ±SEM, 读者一眼能看出区间是否跨 0。
    """
    sd = _sd(vals)
    return None if sd is None else sd / (len(vals) ** 0.5)


def _rsem(vals: list[float]) -> int | None:
    """_sem 的四舍五入版。⚠️样本行数 ≥30 不代表【能算出收益】的局数 ≥2 —— round(None) 会抛。"""
    s = _sem(vals)
    return None if s is None else round(s)


def _qmap(s: Any) -> dict[str, int]:
    """'q1=7;q2=4;q6=3' -> {'q1':11,'q6':3}(绿并入白)。坏数据 -> {}(绝不抛)。

    ⚠️【折叠必须发生在这里, 不能只在渲染层挑列】。曾经这里原样返回 q2, 而渲染层的列只列了
    q1/q3/q4/q5/q6 —— 绿于是从头到尾没人接: "白/局" 只算 q1 印成 3.84(真实白+绿 = 11.59,
    欠了 3 倍), 地图行的"均件"与 5 个品质列之和对不上账(缺口正好是绿), "全服占比"的分母也漏了绿
    (蓝被印成 32.0%, 真实 25.9%)。同名的 sample_dashboard._qmap 一直是折叠的 —— 两个解析器
    同名、口径相反, 这就是 bug 的全部。改这里前先对齐 _QKEY。
    """
    out: dict[str, int] = {}
    if not isinstance(s, str):
        return out
    for part in s.split(";"):
        m = re.fullmatch(r"\s*(q[1-6])\s*=\s*(-?\d+)\s*", part)
        if m:
            k = _QKEY[m.group(1)]
            out[k] = out.get(k, 0) + int(m.group(2))
    return out


def _map_meta() -> dict:
    """map_id -> {'name','family',...}。复用 build_match_records(唯一真源, 含 56xx 排位名覆盖)。"""
    try:
        import sys
        if str(_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS))
        from build_match_records import _load_map_meta  # noqa: PLC0415
        return _load_map_meta() or {}
    except Exception:  # noqa: BLE001 - 没有地图表也要能出统计(只是显示数字 id)
        return {}


def _map_profile_for(map_id: int, meta_row: dict | None = None) -> dict:
    """Return complete classification even for a just-released map absent from maps.json."""
    row = meta_row if isinstance(meta_row, dict) else {}
    keys = {
        "map_kind",
        "map_kind_label",
        "auction_mode",
        "auction_mode_label",
        "activity_status",
        "activity_status_label",
        "activity_cohort",
        "warehouse_policy",
        "warehouse_policy_label",
    }
    if keys.issubset(row):
        return {key: row[key] for key in keys}
    try:
        import sys
        if str(_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS))
        from build_match_records import _map_profile  # noqa: PLC0415
        return _map_profile(
            map_id,
            category=row.get("category"),
            auction_mode=row.get("auction_mode"),
        )
    except Exception:  # noqa: BLE001 - public page must still render fail-closed
        return {
            "map_kind": "regular",
            "map_kind_label": "普通图",
            "auction_mode": "unknown",
            "auction_mode_label": "方式待确认",
            "activity_status": "none",
            "activity_status_label": "非活动",
            "activity_cohort": "",
            "warehouse_policy": "unknown",
            "warehouse_policy_label": "来源待确认",
        }


def _record_source_scope(
    rec: object,
    meta: dict,
    profile_cache: dict[int, dict] | None = None,
    *,
    client_ts_epoch: float | object = _CLIENT_TS_UNSET,
    stable_row: _StablePublicRow | None = None,
) -> tuple[int, dict, str] | None:
    """Return the normalized map/profile/scope tuple used by every public view."""
    stable = stable_row if stable_row is not None else _stable_public_row(rec)
    if stable is None:
        return None
    if client_ts_epoch is _CLIENT_TS_UNSET:
        client_ts_epoch = stable.client_ts_epoch
    if type(client_ts_epoch) is not float:
        return None
    mid = stable.map_id
    if type(mid) is not int:
        return None
    profile = profile_cache.get(mid) if profile_cache is not None else None
    if profile is None:
        map_row = meta.get(mid) or meta.get(str(mid)) or {}
        profile = _map_profile_for(mid, map_row)
        if profile_cache is not None:
            profile_cache[mid] = profile
    source_scope = _source_scope(
        rec,
        profile,
        client_ts_epoch=client_ts_epoch,
        stable_row=stable,
    )
    if source_scope is None:
        return None
    return mid, profile, source_scope


def _default_map_sort_key(row: dict) -> tuple:
    """Public map table order: live activity, normal maps by sample, stale activity last.

    Historical/pending activity variants often have striking values and used to rise
    above ordinary maps when the default key was average value. That reads like a
    recommendation for an unavailable event. Only a separately confirmed `current`
    cohort may lead the table; otherwise sample size is the primary signal and every
    non-current activity cohort stays below regular/ranked maps.
    """
    status = row.get("activity_status")
    kind = row.get("map_kind")
    if status == "current":
        lifecycle_rank = 0
    elif kind == "activity":
        lifecycle_rank = 2
    else:
        lifecycle_rank = 1
    return (
        lifecycle_rank,
        -int(row.get("n") or 0),
        -float(row.get("avg_value") or 0),
        int(row.get("map_id") or 0),
    )


def _iter_source_scope(
    samples: Iterable[dict],
    meta: dict,
    scope: str,
) -> Iterator[dict]:
    """Replay one source pool without materializing the production JSONL."""
    profile_cache: dict[int, dict] = {}
    for rec in samples:
        stable = _stable_public_row(rec)
        if stable is None:
            continue
        rec = stable.row
        client_ts_epoch = stable.client_ts_epoch
        context = _record_source_scope(
            rec,
            meta,
            profile_cache,
            client_ts_epoch=client_ts_epoch,
            stable_row=stable,
        )
        if context is not None and context[2] == scope:
            yield rec


def _iter_main_samples(samples: Iterable[dict], meta: dict) -> Iterator[dict]:
    """Replay accepted main-library rows while retaining diagnostic source labels."""
    profile_cache: dict[int, dict] = {}
    for rec in samples:
        stable = _stable_public_row(rec)
        if stable is None:
            continue
        rec = stable.row
        context = _record_source_scope(
            rec,
            meta,
            profile_cache,
            client_ts_epoch=stable.client_ts_epoch,
            stable_row=stable,
        )
        if (
            context is not None
            and context[2] in _MAIN_INCLUDED_SOURCE_SCOPES
        ):
            yield rec


def aggregate_public(samples: Iterable[dict]) -> dict:
    """把原始样本聚合成【可公开】的统计。输出里绝不含任何身份字段(见模块 docstring 红线)。

    产出:
      maps:  每张图一行 —— 样本量 / 平均总件·总格·总价值 / 各品质平均件数 / 红金出现率
      items: 藏品价格表 —— 名称 / 品质 / 价值 / 出现次数(= 稀有度的经验证据)
      totals: 全局(总样本量 / 覆盖地图数 / 藏品种类数)
    """
    meta = _map_meta()
    # 下面的 _agg_heroes / _agg_tiers / _agg_trend / cube 需要多轮扫描。生产传入的是
    # JsonlSampleSource（每轮重新逐行打开文件），绝不能先 list() 展开 200+ MiB 主库。
    # 对一次性迭代器保留兼容：只在调用方明确传 generator 时物化为 tuple；服务端路径不走这里。
    iterator = iter(samples)
    samples_source: Iterable[dict] = tuple(iterator) if iterator is samples else samples

    # 保留四类诊断来源池；同时按作者 2026-07-29 的产品口径建立“已接纳主统计”并集。
    # collector 已将 malformed/conflict 写入 quarantine；主库里的 official、unknown 与
    # player_custom 暂时共同支撑公开统计，以免来源待确认的普通明拍样本把分母砍半。
    # 原始来源仍进入聚合 key，不能因主统计合并而丢掉诊断和未来重新分池的能力。
    agg: dict[tuple[str, int], dict] = defaultdict(lambda: {
        "n": 0, "count": 0, "cells": 0, "value": 0,
        "q_counts": defaultdict(int),        # 各品质【件数】累计
        "q_values": defaultdict(int),        # 各品质【价值】累计
        "q_series": defaultdict(list),       # 各品质【每局件数】明细 -> 算标准差(波动)
        "with_red": 0, "with_gold": 0,       # 出现过红/金的样本数
    })
    scope_counts_by_map: dict[int, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    items_by_scope: dict[str, dict[str, dict]] = {
        scope: {} for scope in _PUBLIC_SOURCE_SCOPES
    }
    n_by_scope: dict[str, int] = defaultdict(int)
    daily_scope_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    profile_cache: dict[int, dict] = {}

    for rec in samples_source:
        stable = _stable_public_row(rec)
        if stable is None:
            continue
        rec = stable.row
        client_ts_epoch = stable.client_ts_epoch
        mid = stable.map_id
        if type(mid) is not int:
            continue
        # 抓包或目录价值不完整的结算不进公开统计。collector 已隔离, 这里再兜一层。
        if (
            rec.get("settlement_incomplete")
            or rec.get("catalog_value_complete") is False
        ):
            continue

        counts = _qmap(rec.get("final_quality_counts"))
        if not counts:
            continue
        values = _qmap(rec.get("final_quality_values"))

        context = _record_source_scope(
            rec,
            meta,
            profile_cache,
            client_ts_epoch=client_ts_epoch,
            stable_row=stable,
        )
        if context is None:
            continue
        _, _, source_scope = context
        scope_counts_by_map[mid][source_scope] += 1
        n_by_scope[source_scope] += 1
        day = _shanghai_day(client_ts_epoch)
        if day is not None:
            daily_scope_counts[day][source_scope] += 1
        aggregate_scopes = [source_scope]
        if source_scope in _MAIN_INCLUDED_SOURCE_SCOPES:
            aggregate_scopes.append(_MAIN_SOURCE_SCOPE)
        for aggregate_scope in aggregate_scopes:
            a = agg[(aggregate_scope, mid)]
            a["n"] += 1
            for k in ("inventory_count", "inventory_cells", "known_value_sum"):
                v = rec.get(k)
                if isinstance(v, (int, float)):
                    a[{"inventory_count": "count", "inventory_cells": "cells",
                       "known_value_sum": "value"}[k]] += v
            for q in _QUALITY_LABELS:
                a["q_counts"][q] += counts.get(q, 0)
                a["q_series"][q].append(counts.get(q, 0))   # 逐局明细 -> 标准差
            for q, v in values.items():
                a["q_values"][q] += v
            if counts.get("q6", 0) > 0:
                a["with_red"] += 1
            if counts.get("q5", 0) > 0:
                a["with_gold"] += 1

            # 藏品价格表: rare_items = [{'n':名,'q':品质,'v':价值}, ...]
            scope_items = items_by_scope[aggregate_scope]
            for it in (rec.get("rare_items") or []):
                if not isinstance(it, dict):
                    continue
                name = str(it.get("n") or "").strip()
                if not name:
                    continue
                row = scope_items.setdefault(
                    name,
                    {
                        "name": name,
                        "quality": it.get("q"),
                        "value": it.get("v"),
                        "seen": 0,
                    },
                )
                row["seen"] += 1
                # 价值以出现过的最大值为准(同名藏品价值固定; 取 max 兜住偶发解析噪音)
                v = it.get("v")
                if isinstance(v, (int, float)) and (
                    not isinstance(row["value"], (int, float))
                    or v > row["value"]
                ):
                    row["value"] = v

    maps_by_source: dict[str, list[dict]] = {
        scope: [] for scope in _PUBLIC_SOURCE_SCOPES
    }
    for (source_scope, mid), a in agg.items():
        n = a["n"]
        if n <= 0:
            continue
        m = meta.get(mid) or meta.get(str(mid)) or {}
        profile = _map_profile_for(mid, m)
        map_scope_counts = scope_counts_by_map[mid]
        source_counts = {
            "official_samples": int(map_scope_counts.get("official", 0)),
            "player_warehouse_samples": int(
                map_scope_counts.get("player_custom", 0)
            ),
            "unknown_samples": int(map_scope_counts.get("unknown", 0)),
            "conflict_samples": int(map_scope_counts.get("conflict", 0)),
        }
        if source_scope == _MAIN_SOURCE_SCOPE:
            source_label = _SOURCE_SCOPE_LABELS[_MAIN_SOURCE_SCOPE]
        elif source_scope == "official":
            source_label = (
                "仅官方随机仓"
                if profile["warehouse_policy"] == "official_only"
                else "官方池（该图可能有玩家仓）"
            )
        else:
            source_label = _SOURCE_SCOPE_LABELS[source_scope]
        nonofficial_n = sum(
            map_scope_counts.get(scope, 0)
            for scope in ("player_custom", "unknown", "conflict")
        )
        all_map_n = sum(map_scope_counts.values())
        row = {
            "map_id": mid,
            "name": (m.get("name") if isinstance(m, dict) else None) or str(mid),
            "family": (m.get("family") if isinstance(m, dict) else None) or "",
            **profile,
            "n": n,
            "mixed_source_n": nonofficial_n,
            "mixed_source_rate": (
                round(nonofficial_n / all_map_n, 4) if all_map_n else 0.0
            ),
            "source_scope_counts": source_counts,
            "source_scope": source_scope,
            "source_status": source_scope,
            "source_label": source_label,
            "avg_count": round(a["count"] / n, 1),
            "avg_cells": round(a["cells"] / n, 1),
            "avg_value": round(a["value"] / n),
            "red_rate": round(a["with_red"] / n, 4),      # 出现红的局数占比
            "gold_rate": round(a["with_gold"] / n, 4),
            "avg_q": {q: round(a["q_counts"].get(q, 0) / n, 2) for q in _QUALITY_LABELS},
            # ⭐σ = 每局件数的标准差(波动)。均红 2.0±0.4 和 2.0±2.5 是完全不同的两张图 ——
            # 前者稳定开 2 个红, 后者要么空要么爆。波动大 = 该图更该按保守档出价。
            "sd_q": {q: _rsd(a["q_series"].get(q, [])) for q in _QUALITY_LABELS},
            "avg_q_value": {q: round(a["q_values"].get(q, 0) / n) for q in _QUALITY_LABELS},
            "low_confidence": n < MIN_SAMPLES_TO_SHOW,
        }
        maps_by_source[source_scope].append(row)

    for source_maps in maps_by_source.values():
        source_maps.sort(key=_default_map_sort_key)
    item_rows_by_source = {
        scope: sorted(
            items_by_scope[scope].values(),
            key=lambda r: (-(r["quality"] or 0), -(r["value"] or 0)),
        )
        for scope in _PUBLIC_SOURCE_SCOPES
    }
    main_samples = lambda: _iter_main_samples(samples_source, meta)
    cube = build_filter_cube(samples_source, meta)
    maps = maps_by_source[_MAIN_SOURCE_SCOPE]
    item_rows = item_rows_by_source[_MAIN_SOURCE_SCOPE]
    return {
        "maps": maps,
        "maps_by_source": maps_by_source,
        "items": item_rows,
        "items_by_source": item_rows_by_source,
        "heroes": _agg_heroes(main_samples(), meta),
        "tiers": _agg_tiers(main_samples(), meta),
        "trend": _agg_trend(main_samples()),
        "trend_source_counts": [
            {
                "day": day,
                "total": int(sum(counts.values())),
                _MAIN_SOURCE_SCOPE: int(
                    sum(
                        counts.get(scope, 0)
                        for scope in _MAIN_INCLUDED_SOURCE_SCOPES
                    )
                ),
                **{
                    scope: int(counts.get(scope, 0))
                    for scope in _SOURCE_SCOPES
                },
            }
            for day, counts in sorted(daily_scope_counts.items())
        ],
        "cube": cube,
        "recent_red": recent_red_maps_from_cube(
            cube,
            source_scope=_MAIN_SOURCE_SCOPE,
        ),
        "totals": {
            "samples": int(
                sum(
                    n_by_scope.get(scope, 0)
                    for scope in _MAIN_INCLUDED_SOURCE_SCOPES
                )
            ),
            "received_samples": int(sum(n_by_scope.values())),
            "source_scope_counts": {
                scope: int(n_by_scope.get(scope, 0)) for scope in _SOURCE_SCOPES
            },
            "mixed_source_samples": int(
                sum(n_by_scope.get(scope, 0) for scope in _SOURCE_SCOPES[1:])
            ),
            "maps": len(maps),
            "items": len(item_rows),
            "min_samples_to_show": MIN_SAMPLES_TO_SHOW,
            "min_n_identity": MIN_N_FOR_IDENTITY_DERIVED,
        },
    }


def _day_of(
    rec: object,
    *,
    client_ts_epoch: float | object = _CLIENT_TS_UNSET,
) -> str | None:
    """client_ts -> 北京时间 'YYYY-MM-DD'。⚠️【只取日期, 丢掉时刻】—— 精确时间戳是很强的准标识符
    (能把一局锁到某个人的某一次游玩); 日期粒度才是趋势图需要的, 也是安全的。
    client_ts 本身在 _FORBIDDEN_KEYS 里, 它【绝不能】原样进输出树。"""
    if client_ts_epoch is _CLIENT_TS_UNSET:
        stable = _stable_public_row(rec)
        if stable is None:
            return None
        client_ts_epoch = stable.client_ts_epoch
    if type(client_ts_epoch) is not float:
        return None
    return _shanghai_day(client_ts_epoch)


def build_filter_cube(samples: Iterable[dict], meta: dict) -> dict:
    """(来源 × 地图 × 英雄 × 日期) 的最细聚合格子 —— 联合筛选的数据源。

    🔴【绝不发逐局行】。这是本页最重要的架构决定, 理由:

    1) 逐局行发出去就收不回。哪怕剥光身份字段, 那也等于把整个数据集公开送人。
       而且"客户端只渲染 n≥k"这种护栏是【假的】—— 行已经随 HTML 发下去了, 谁都能自己算。

    2) 【根本不需要】逐局行。筛选后的均值和标准差都是【和】的函数:
           mean = Σx / n
           σ    = sqrt( (Σx² − (Σx)²/n) / (n−1) )
       所以客户端把命中的格子【相加】, 就能算出任意筛选组合的统计量, 与逐局直算【逐位等价】
       (实测 765 次随机组合对比, 最大偏差 3e-15 = float64 噪声)。

    实测(45,693 局): 逐局行 gz 0.572 MB -> cube 3,753 格 gz 0.111 MB, 小 5 倍;
    且【不随样本量线性增长】(只随 地图×英雄×天 增长) —— 样本翻十倍, cube 几乎不变。

    ⚠️诚实保留: 3,753 格里约 22% 是 n=1(占总样本 1.8%), 这些格子确实等价于一条逐局行。
    但它们不含任何身份/出价/胜负, 只有那一局的仓库构成 —— 而仓库是同局所有人都看得见的。

    🔴 cube 里【没有任何身份派生量】(收益/胜率/段位)。那些必须走 n≥30 的预聚合表(见 _agg_heroes /
    _agg_tiers), 绝不能进这个能被筛到 n=1 的格子里 —— self_profit 仍可由仓库值、赢家出价、
    10%分红与门票反推,
    而仓库值公开、门票是族常量 ⇒ 逐格发 self_profit 就等于【明文发赢家出价】(实测 99.9% 的局
    带 winner_bid, 一减就出来)。这也是为什么"探索"视图只做掉率/件数/格数/价值。
    """
    QORD = list(_QUALITY_LABELS)
    cells: dict[tuple, dict] = defaultdict(lambda: {
        "n": 0, "v": 0, "c": 0, "g": 0, "red": 0, "gold": 0,
        "qs": [0] * len(QORD), "qq": [0] * len(QORD), "ks": [0] * len(QORD),
    })
    fams: dict[int, str] = {}
    names: dict[int, str] = {}
    profiles: dict[int, dict] = {}
    profile_cache: dict[int, dict] = {}

    for rec in samples:
        stable = _stable_public_row(rec)
        if stable is None:
            continue
        rec = stable.row
        client_ts_epoch = stable.client_ts_epoch
        context = _record_source_scope(
            rec,
            meta,
            profile_cache,
            client_ts_epoch=client_ts_epoch,
            stable_row=stable,
        )
        if context is None:
            continue
        mid, profile, source_scope = context
        if not _usable(rec):
            continue
        counts = _qmap(rec.get("final_quality_counts"))
        if not counts:
            continue
        day = _day_of(rec, client_ts_epoch=client_ts_epoch)
        if day is None:
            continue
        hero = _sample_hero_slug(rec.get("hero"))
        kcells = _qmap(rec.get("final_quality_cells"))

        aggregate_scopes = [source_scope]
        if source_scope in _MAIN_INCLUDED_SOURCE_SCOPES:
            aggregate_scopes.append(_MAIN_SOURCE_SCOPE)
        for aggregate_scope in aggregate_scopes:
            a = cells[(aggregate_scope, mid, hero, day)]
            a["n"] += 1
            a["v"] += int(rec.get("known_value_sum") or 0)
            a["c"] += int(rec.get("inventory_count") or 0)
            a["g"] += int(rec.get("inventory_cells") or 0)
            if counts.get("q6", 0) > 0:
                a["red"] += 1
            if counts.get("q5", 0) > 0:
                a["gold"] += 1
            for i, q in enumerate(QORD):
                x = counts.get(q, 0)
                a["qs"][i] += x
                a["qq"][i] += x * x          # Σx² -> 客户端据此还原 σ
                a["ks"][i] += kcells.get(q, 0)

        if mid not in names:
            m = meta.get(mid) or meta.get(str(mid)) or {}
            names[mid] = (m.get("name") if isinstance(m, dict) else None) or str(mid)
            fams[mid] = (m.get("family") if isinstance(m, dict) else None) or ""
            profiles[mid] = profile

    sources = [
        scope
        for scope in _PUBLIC_SOURCE_SCOPES
        if scope in {_MAIN_SOURCE_SCOPE, "official"} or any(k[0] == scope for k in cells)
    ]
    heroes = sorted({k[2] for k in cells})
    days = sorted({k[3] for k in cells})
    sidx = {scope: i for i, scope in enumerate(sources)}
    hidx = {h: i for i, h in enumerate(heroes)}
    didx = {d: i for i, d in enumerate(days)}
    # 按 (日期, 来源, 地图, 英雄) 排序: 相邻格子高度相似 -> gzip 更小。
    keys = sorted(
        cells,
        key=lambda t: (t[3], _PUBLIC_SOURCE_SCOPES.index(t[0]), t[1], t[2]),
    )

    cube: dict[str, Any] = {
        "qord": QORD,
        "qlab": [_QUALITY_LABELS[q] for q in QORD],
        "sources": sources,
        "source_labels": [_SOURCE_SCOPE_LABELS[scope] for scope in sources],
        "heroes": heroes,
        "hero_cn": [hero_name(h) for h in heroes],
        "days": days,
        "maps": sorted(names),
        "map_names": [names[m] for m in sorted(names)],
        "map_fams": [fams[m] for m in sorted(names)],
        "map_kinds": [profiles[m]["map_kind"] for m in sorted(names)],
        "map_kind_labels": [profiles[m]["map_kind_label"] for m in sorted(names)],
        "map_modes": [profiles[m]["auction_mode"] for m in sorted(names)],
        "map_mode_labels": [profiles[m]["auction_mode_label"] for m in sorted(names)],
        "map_activity": [profiles[m]["activity_status"] for m in sorted(names)],
        "map_activity_labels": [
            profiles[m]["activity_status_label"] for m in sorted(names)
        ],
        "s": [sidx[k[0]] for k in keys],
        "m": [k[1] for k in keys],
        "h": [hidx[k[2]] for k in keys],
        "d": [didx[k[3]] for k in keys],
        "n": [cells[k]["n"] for k in keys],
        # 仓库价值以【百】为单位存(显示层本来就 money() 取整, 精度绰绰有余, payload 更小)
        "v": [round(cells[k]["v"] / 100) for k in keys],
        "c": [cells[k]["c"] for k in keys],
        "g": [cells[k]["g"] for k in keys],
        "red": [cells[k]["red"] for k in keys],
        "gold": [cells[k]["gold"] for k in keys],
    }
    for i, q in enumerate(QORD):
        cube[f"qs_{q}"] = [cells[k]["qs"][i] for k in keys]
        cube[f"qq_{q}"] = [cells[k]["qq"][i] for k in keys]
        cube[f"ks_{q}"] = [cells[k]["ks"][i] for k in keys]
    return cube


def recent_red_maps_from_cube(
    cube: dict,
    *,
    days: int = 7,
    min_n: int = MIN_SAMPLES_TO_SHOW,
    source_scope: str = "official",
) -> dict:
    """按数据中的最新游玩日回看自然日窗口，聚合高置信地图红件数。

    以最新游玩日而不是服务器生成日为锚点：历史快照即使今天重建，也不会凭空把窗口
    推到今天并得到“最近 7 天零样本”。窗口是 D-6..D，空白自然日照常占窗口。
    只输出 n>=min_n 的地图，和公开页其余排行共用同一道样本门。
    """
    if days <= 0:
        return {"start": None, "end": None, "days": max(0, days), "rows": []}
    day_values = cube.get("days") or []
    maps = cube.get("maps") or []
    names = cube.get("map_names") or []
    sources = cube.get("sources") or []
    source_indexes = cube.get("s")
    map_indexes = cube.get("m") or []
    day_indexes = cube.get("d") or []
    sample_counts = cube.get("n") or []
    red_totals = cube.get("qs_q6") or []
    red_squares = cube.get("qq_q6") or []

    def _source_cell(i: int) -> tuple[int, date, int, float, float] | None:
        """Parse one complete cell and bind legacy cubes to official only."""
        try:
            if source_indexes is None:
                if source_scope != "official":
                    return None
            elif sources[source_indexes[i]] != source_scope:
                return None
            cell_day = date.fromisoformat(day_values[day_indexes[i]])
            mid = int(map_indexes[i])
            n = int(sample_counts[i])
            total = float(red_totals[i])
            total_sq = float(red_squares[i])
        except (IndexError, OverflowError, TypeError, ValueError):
            return None
        if n <= 0:
            return None
        return mid, cell_day, n, total, total_sq

    # 窗口锚点和后续聚合必须来自同一个来源池、同一批完整有效的 cell。否则一个更新更晚
    # 的玩家仓/待确认样本虽不直接计入 official，却仍能把 official 的七天窗口整体推后。
    source_cells = [
        cell
        for i in range(len(map_indexes))
        if (cell := _source_cell(i)) is not None
    ]
    if not source_cells:
        return {"start": None, "end": None, "days": days, "rows": []}
    end = max(cell[1] for cell in source_cells)
    start = end - timedelta(days=days - 1)

    name_by_map = {m: names[i] if i < len(names) else str(m) for i, m in enumerate(maps)}
    by_map: dict[int, dict[str, float]] = defaultdict(lambda: {"n": 0, "sum": 0, "sumsq": 0})
    for mid, cell_day, n, total, total_sq in source_cells:
        if start <= cell_day <= end:
            a = by_map[mid]
            a["n"] += n
            a["sum"] += total
            a["sumsq"] += total_sq
    rows = []
    for mid, a in by_map.items():
        n = int(a["n"])
        if n < min_n:
            continue
        avg = a["sum"] / n
        sd = None if n < 2 else ((max(0.0, a["sumsq"] - a["sum"] ** 2 / n) / (n - 1)) ** 0.5)
        rows.append({"map_id": mid, "name": name_by_map.get(mid, str(mid)), "n": n,
                     "avg_q6": round(avg, 2), "sd_q6": None if sd is None else round(sd, 2)})
    rows.sort(key=lambda r: (-r["avg_q6"], -r["n"], r["map_id"]))
    return {"start": start.isoformat(), "end": end.isoformat(), "days": days, "rows": rows}


# --------------------------------------------------------------------------- #
# 以下三个聚合【读取】身份字段(winner / player / winner_bid / rank_tier)来算统计,
# 但【输出树里只有聚合数字】—— 没有任何昵称、uid、session。隐私门扫的是输出, 见模块 docstring。
# --------------------------------------------------------------------------- #
def _usable(rec: dict) -> bool:
    """能用于聚合吗。⚠️必须把 map_id 真正转成 int 试一遍 —— /public 是【公网可访问】的,
    一行垃圾数据(map_id='not-int')就能让整页 500。设计门 test_garbage_rows_never_crash 守着。"""
    if (
        not isinstance(rec, dict)
        or rec.get("settlement_incomplete")
        or rec.get("catalog_value_complete") is False
    ):
        return False
    try:
        int(rec.get("map_id"))          # noqa: PLW0133 - 只为验证可转换
    except (TypeError, ValueError):
        return False
    return True


def _mid(rec: dict) -> int:
    return int(rec["map_id"])


def _self_profit(rec: dict, fee: int) -> float | None:
    """本机(上传者)这一局的盈亏。与 /dashboard 的「玩家」/「英雄」视图【同一套口径】, 不另立:
         每位玩家(含拿仓者)都收赢家亏损 × 10%的分红
         拍下   = 藏品真值 − 自己出价 + 分红 − 门票
         没拍下 = 结算帧福利真值(优先)或规则分红 − 门票
    缺 winner/player/winner_bid 任一 -> None(算不了, 不猜)。"""
    winner, player = rec.get("winner"), rec.get("player")
    wbid, lot = rec.get("winner_bid"), rec.get("known_value_sum")
    if not winner or not player or not isinstance(wbid, (int, float)) or not isinstance(lot, (int, float)):
        return None
    from build_match_records import _loss_share_units  # noqa: PLC0415

    dividend = _loss_share_units(wbid, lot)
    if dividend is None:
        return None
    if winner == player:
        return float(lot) - float(wbid) + dividend - fee
    welfare = rec.get("settlement_welfare")
    if (
        isinstance(welfare, (int, float))
        and not isinstance(welfare, bool)
        and math.isfinite(float(welfare))
    ):
        return float(welfare) - fee
    return dividend - fee


def _fee_of(mid: int, meta: dict) -> int:
    try:
        import sys
        if str(_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS))
        from build_match_records import _FEE_BY_FAMILY, _family_from_id  # noqa: PLC0415
        m = meta.get(mid) or meta.get(str(mid)) or {}
        fam = (m.get("family") if isinstance(m, dict) else None) or _family_from_id(mid)
        f = _FEE_BY_FAMILY.get(fam)
        return int(f if f is not None else (m.get("entry_fee") or 0))
    except Exception:  # noqa: BLE001
        return 0


def _agg_heroes(samples: list, meta: dict) -> list[dict]:
    """各英雄: 出场率 / 胜率 / 均收益 / 每局各品质均件±σ / 红率金率。"""
    by: dict[str, dict] = defaultdict(lambda: {"n": 0, "q": defaultdict(list), "lot": 0.0,
                                               "wins": 0, "wn": 0, "prof": [],
                                               "red": 0, "gold": 0})
    total = 0
    for rec in samples:
        if not _usable(rec):
            continue
        counts = _qmap(rec.get("final_quality_counts"))
        if not counts:
            continue
        hero = _sample_hero_slug(rec.get("hero"))
        a = by[hero]
        a["n"] += 1
        total += 1
        for q in _QUALITY_LABELS:
            a["q"][q].append(counts.get(q, 0))
        v = rec.get("known_value_sum")
        if isinstance(v, (int, float)):
            a["lot"] += v
        if counts.get("q6", 0) > 0:
            a["red"] += 1
        if counts.get("q5", 0) > 0:
            a["gold"] += 1
        winner, player = rec.get("winner"), rec.get("player")
        if winner and player:
            a["wn"] += 1
            if winner == player:
                a["wins"] += 1
        p = _self_profit(rec, _fee_of(_mid(rec), meta))
        if p is not None:
            a["prof"].append(p)

    out = []
    for hero, a in by.items():
        n = a["n"]
        enough = n >= MIN_N_FOR_IDENTITY_DERIVED          # 身份派生统计的最小样本量
        prof = a["prof"]
        out.append({
            "hero": hero, "name": hero_name(hero), "n": n,
            "pick_rate": round(n / total, 4) if total else 0,
            # 胜率/均收益: 样本不足时【不出】(既是隐私卫生, 也是统计诚实) -> None
            "win_rate": (round(a["wins"] / a["wn"], 4) if (enough and a["wn"]) else None),
            "avg_profit": (round(sum(prof) / len(prof)) if (enough and prof) else None),
            "profit_sem": (_rsem(prof) if enough else None),
            "avg_value": round(a["lot"] / n) if n else 0,
            "avg_q": {q: round(sum(a["q"][q]) / n, 2) for q in _QUALITY_LABELS},
            "sd_q": {q: _rsd(a["q"][q]) for q in _QUALITY_LABELS},
            "red_rate": round(a["red"] / n, 4),
            "gold_rate": round(a["gold"] / n, 4),
            "low_confidence": not enough,
        })
    out.sort(key=lambda r: -r["n"])
    return out


def _agg_tiers(samples: list, meta: dict) -> list[dict]:
    """段位分布: 每段多少局 / 分数区间 / 均拍品值 / 均收益。"""
    by: dict[int, dict] = defaultdict(lambda: {"n": 0, "lot": 0.0, "prof": [],
                                               "pts": 0.0, "ptn": 0, "lo": None, "hi": None})
    total = 0
    for rec in samples:
        if not _usable(rec):
            continue
        t = rec.get("rank_tier")
        if not isinstance(t, int):
            continue
        a = by[t]
        a["n"] += 1
        total += 1
        v = rec.get("known_value_sum")
        if isinstance(v, (int, float)):
            a["lot"] += v
        p = _self_profit(rec, _fee_of(_mid(rec), meta))
        if p is not None:
            a["prof"].append(p)
        pts = rec.get("rank_points")
        if isinstance(pts, (int, float)):
            a["pts"] += pts
            a["ptn"] += 1
            a["lo"] = pts if a["lo"] is None else min(a["lo"], pts)
            a["hi"] = pts if a["hi"] is None else max(a["hi"], pts)

    out = []
    for t, a in sorted(by.items()):
        n = a["n"]
        enough = n >= MIN_N_FOR_IDENTITY_DERIVED
        prof = a["prof"]
        # 🔴 pts_lo/pts_hi/avg_pts 【和收益一样受 n 下限约束】—— 这是【预防性】的门, 不是在堵一个现存泄漏。
        # 它们是该档【实际观测到】的 rank_points 最小/最大/均值, 不是段位的定义边界。n 小到 1 时
        # lo==hi==avg== 那个人的精确排位分, 而 rank_points 本身就在 _FORBIDDEN_KEYS 里 ——
        # 聚合到一个人身上的"聚合值"就是原始值。
        # ⚠️口径: 2026-07-12 实测 4.5 万条云端样本, 32 档【最小 n=122】、无一档 lo==hi ⇒ 当前页面
        # 【没有】这个泄漏。这道门是防以后出现罕见档(新赛季/新顶段/小样本期)时静默泄漏, 别把它
        # 当成"修好了一个线上 bug"。(⚠️n 数的是【对局】不是【人】—— 极端情况下 n=30 也可能只有 2 个人。)
        out.append({
            "tier": t, "name": tier_name(t), "n": n,
            "share": round(n / total, 4) if total else 0,
            "pts_lo": int(a["lo"]) if (enough and a["lo"] is not None) else None,
            "pts_hi": int(a["hi"]) if (enough and a["hi"] is not None) else None,
            "avg_pts": round(a["pts"] / a["ptn"]) if (enough and a["ptn"]) else None,
            "avg_value": round(a["lot"] / n) if n else 0,
            "avg_profit": (round(sum(prof) / len(prof)) if (enough and prof) else None),
            "profit_sem": (_rsem(prof) if enough else None),
            "low_confidence": not enough,
        })
    return out


def _agg_trend(samples: list) -> list[dict]:
    """按天的掉率趋势(佐证掉率改版)。日期只到【天】粒度 —— 原始时间戳绝不出现在输出里。

    ⚠️口径 = 【游玩时间 client_ts】, 不是【上传时间 received_ts】。这一节的唯一用途是回答
    "官方哪天调了掉率" —— 掉落发生在【打这局的时候】, 不是服务器收到上传的时候。按上传时间分桶,
    一批补传的旧数据会全砸进上传那天, 正好在这张图该检测跳变的地方【伪造出一个跳变】。
    /dashboard 一直用的也是游玩时间。

    曾经这里用 received_ts, 于是同一节里【图和表的日期范围都不一样】(表 18 天 / 图 23 天) ——
    现在两边都走 _day_of(), 唯一真源, 不会再漂。
    (实测 45,693 局: 两个口径 99.97% 一致, 只有 13 局的游玩日 != 上传日, 且 client_ts 无一离谱。
     危害当前很小, 但口径错了就是错了 —— 等真出现一次大补传, 这张图会当场骗人。)
    """
    by: dict[str, dict] = defaultdict(lambda: {"n": 0, "q": defaultdict(list), "lot": 0.0})
    for rec in samples:
        if not _usable(rec):
            continue
        counts = _qmap(rec.get("final_quality_counts"))
        if not counts:
            continue
        day = _day_of(rec)
        if day is None:
            continue
        a = by[day]
        a["n"] += 1
        for q in _QUALITY_LABELS:
            a["q"][q].append(counts.get(q, 0))
        v = rec.get("known_value_sum")
        if isinstance(v, (int, float)):
            a["lot"] += v
    out = []
    for day, a in sorted(by.items()):
        n = a["n"]
        out.append({
            "day": day, "n": n,
            "avg_q": {q: round(sum(a["q"][q]) / n, 2) for q in _QUALITY_LABELS},
            "avg_value": round(a["lot"] / n) if n else 0,
            # 样本太少的天照常列出(不删数据), 但置灰标记 —— 否则开服头几天的 n=1/n=3
            # 会和 n=1700 的天并排, 制造出假的"掉率跳变"。见 MIN_N_FOR_TREND_DAY。
            "low_confidence": n < MIN_N_FOR_TREND_DAY,
        })
    return out


def _pm(sd: float | None) -> str:
    """σ 标记。⚠️别把它做小做淡 —— dashboard 上踩过两次坑, 作者实测"根本没看到"。

    sd=None(n<2)-> "±?" 而不是 "±0.00": 后者读起来像"这图每局雷打不动开 3 个红",
    实际只是【只有一个样本】。零误差和算不出误差是两回事。
    """
    return f'<span class="pm">±{"?" if sd is None else f"{sd:.2f}"}</span>'


def _stack_bar(avg_q: dict, order: list[str]) -> str:
    """一行里的品质构成条(地图表 / 英雄表用)。纯占比 —— 长度已归一, 看的是【构成】不是【数量】。

    ⚠️ 别把它当"哪张图东西多"来读: 每根条都被拉成一样长。数量看旁边的"均件"列。
    """
    tot = sum(avg_q.get(q, 0) for q in order) or 1
    segs = "".join(
        f'<i style="width:{100 * avg_q.get(q, 0) / tot:.2f}%;background:var(--{q})"'
        f' title="{_QUALITY_LABELS[q]} {avg_q.get(q, 0):.2f} 件"></i>'
        for q in order if avg_q.get(q, 0) > 0)
    return f'<div class="stk sm">{segs}</div>'


def _scroll(table_html: str) -> str:
    """给表格套横向滚动壳。【每张表都要套】—— 不套的话宽表(英雄表 12 列)在手机上会顶得
    【整个页面】横滚, 而不是表格自己滚。web_ui 的 .scroll 里有配套的 `.scroll th{position:static}`,
    那条不是美化: 滚动壳会夺走 sticky 表头的定位基准, 不降回 static 表头就会压在头几行数据上。
    """
    return f'<div class="scroll">{table_html}</div>'


def _qcell(mean: float, sd: float | None, color: str, strong: bool = False) -> str:
    st = f' style="color:var(--{color});font-weight:600"' if strong else f' style="color:var(--{color})"'
    return f'<td data-v="{mean}" class="num"><span{st}>{mean:.2f}</span>{_pm(sd)}</td>'


def _profit_cell(avg: int | None, sem: int | None, money) -> str:
    """均收益 ± 标准误。

    ⚠️ 没有 ±SEM 的均收益是【会骗人的】: 单局盈亏几十万级, 一个 n=200 的段位档
    印出 "+38,635" 看着像"这一档真赚", 其实 ±SEM 可能有 ±30,000 —— 区间跨 0,
    和"不赚不亏"根本区分不开。印上 ± 让读者自己看出哪些差异是真的。
    """
    if avg is None:
        return '<td data-v="-9e9" class="num">—</td>'
    c = "gold" if avg >= 0 else "red"
    sign = "+" if avg >= 0 else ""
    body = (f'<span style="color:var(--{c});font-weight:600">{sign}{money(avg)}</span>')
    # sem=None(能算收益的局数<2)-> "±?" 而不是省略 —— 省略会让这个数看着和旁边带 ± 的一样可信。
    body += f'<span class="pm">±{"?" if sem is None else money(sem)}</span>'
    return f'<td data-v="{avg}" class="num">{body}</td>'


def _sec(sid: str, title: str, sub: str, body: str) -> str:
    return (f'<section class="sect" id="{sid}"><div class="head"><h1>{title}</h1>'
            f'<div class="sub">{sub}</div></div>{body}</section>')


# --------------------------------------------------------------------------- #
# 「探索」= 联合筛选 + 图表。全部在客户端算, 数据源是 cube(见 build_filter_cube)。
#
# 🔴 三条不能碰的线:
#   1. 只吃 cube 的【格子】, 永远拿不到逐局行 —— 页面上根本没有那个数据。
#   2. 只做【掉率/件数/格数/价值】。收益/胜率/段位是身份派生量, 不进这个能筛到 n=1 的视图。
#   3. k-匿名门: 筛完 n < MIN_SAMPLES_TO_SHOW 就【整块不出统计】。没有这道门, 联合筛选就变成
#      "把任意单局挑出来看"的工具 —— 那正是这一页最不该做的事。
# --------------------------------------------------------------------------- #
_EXPLORE_JS = """
var C = CUBE, NC = C.n.length, QO = C.qord, QL = C.qlab;
var F = {source:"accepted", kind:"", mode:"", activity:"", fam:"", map:"", hero:"", dfrom:"", dto:""};
var RRMAP = "";

function esc(s){ return String(s).replace(/[&<>"]/g, function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"}[c]; }); }
function fmt1(v){ return (Math.round(v*100)/100).toFixed(2); }
function money(v){ v = Math.round(v);
  return v >= 1e8 ? (v/1e8).toFixed(2)+"亿" : v >= 1e4 ? (v/1e4).toFixed(1)+"万" : String(v); }

// 地图 id -> 下标(取名字/族)
var MIDX = {}; C.maps.forEach(function(m,i){ MIDX[m]=i; });
function mapName(m){ var i=MIDX[m]; return i==null ? String(m) : C.map_names[i]; }
function mapFam(m){ var i=MIDX[m]; return i==null ? "" : C.map_fams[i]; }
function mapKind(m){ var i=MIDX[m]; return i==null ? "" : C.map_kinds[i]; }
function mapMode(m){ var i=MIDX[m]; return i==null ? "" : C.map_modes[i]; }
function mapActivity(m){ var i=MIDX[m]; return i==null ? "" : C.map_activity[i]; }
function sourceAt(i){
  return C.s && C.sources ? C.sources[C.s[i]] : "official";
}

function buildRedFilter(){
  var box=document.getElementById("rrFilter"); if(!box) return;
  var rows=(RECENT_RED.rows||[]).slice().sort(function(a,b){return a.name.localeCompare(b.name,"zh")||a.map_id-b.map_id;});
  box.innerHTML='<span class="fl">地图</span><select id="rr-map" onchange="setRedMap(this.value)">'
    + '<option value="">全部高置信地图</option>'
    + rows.map(function(r){return '<option value="'+r.map_id+'"'+(String(r.map_id)===RRMAP?' selected':'')+'>'
        +esc(r.name)+' '+r.map_id+'</option>';}).join('')+'</select>'
    + '<button class="rst" onclick="setRedMap(\\'\\')">重置</button>';
}
function toggleRedFilter(){
  var box=document.getElementById("rrFilter"), btn=document.getElementById("rrFilterBtn"); if(!box) return;
  var open=box.style.display==="none"; box.style.display=open?"flex":"none";
  if(btn){btn.textContent=open?"收起筛选":"筛选地图";btn.setAttribute("aria-expanded",open?"true":"false");}
}
function setRedMap(v){ RRMAP=String(v||""); buildRedFilter(); renderRecentRed(); }
function renderRecentRed(){
  var body=document.getElementById("rrBody"); if(!body) return;
  var rows=(RECENT_RED.rows||[]).filter(function(r){return !RRMAP||String(r.map_id)===RRMAP;});
  if(!RRMAP) rows=rows.slice(0,5);
  if(!rows.length){ body.innerHTML='<tr><td colspan="3" style="text-align:center;color:var(--dim)">该窗口暂无达到 '+MIN_N+' 局的地图数据</td></tr>'; return; }
  body.innerHTML=rows.map(function(r){return '<tr><td class="name">'+esc(r.name)+'<span class="mid">'+r.map_id+'</span></td>'
    +'<td data-v="'+r.avg_q6+'" class="num" style="color:var(--red);font-weight:600">'+fmt1(r.avg_q6)
    +'<span class="pm">±'+(r.sd_q6==null?'?':fmt1(r.sd_q6))+'</span></td>'
    +'<td data-v="'+r.n+'" class="num">'+r.n.toLocaleString()+'</td></tr>';}).join('');
}

// 命中的【格子下标】。注意: 命中的是格子, 不是对局 —— 页面上从来没有逐局数据。
function hits(){
  var out=[];
  for(var i=0;i<NC;i++){
    var m=C.m[i];
    if(sourceAt(i)!==F.source) continue;
    if(F.map && String(m)!==F.map) continue;
    if(F.kind && mapKind(m)!==F.kind) continue;
    if(F.mode && mapMode(m)!==F.mode) continue;
    if(F.activity && mapActivity(m)!==F.activity) continue;
    if(F.fam && mapFam(m)!==F.fam) continue;
    if(F.hero && C.heroes[C.h[i]]!==F.hero) continue;
    var d=C.days[C.d[i]];
    if(F.dfrom && d<F.dfrom) continue;
    if(F.dto && d>F.dto) continue;
    out.push(i);
  }
  return out;
}

// 把格子相加 -> 该筛选组合的统计量。
// σ 从 Σx² 还原: var = (Σx² − (Σx)²/n)/(n−1)。与逐局直算【逐位等价】(实测偏差 4e-15)。
function agg(idx){
  var a={n:0, v:0, c:0, g:0, red:0, gold:0, qs:{}, qq:{}, ks:{}};
  QO.forEach(function(q){ a.qs[q]=0; a.qq[q]=0; a.ks[q]=0; });
  for(var j=0;j<idx.length;j++){ var i=idx[j];
    a.n+=C.n[i]; a.v+=C.v[i]; a.c+=C.c[i]; a.g+=C.g[i]; a.red+=C.red[i]; a.gold+=C.gold[i];
    QO.forEach(function(q){ a.qs[q]+=C["qs_"+q][i]; a.qq[q]+=C["qq_"+q][i]; a.ks[q]+=C["ks_"+q][i]; });
  }
  return a;
}
function mean(a,q){ return a.n ? a.qs[q]/a.n : 0; }
function sd(a,q){                      // n<2 -> null(【算不出】), 绝不返回 0 —— "±0" 会被读成"零误差"
  if(a.n<2) return null;
  var s=a.qs[q], v=(a.qq[q] - s*s/a.n)/(a.n-1);
  return Math.sqrt(Math.max(v,0));
}

// ---- 图表(纯 SVG 字符串; fill 用 CSS 变量 -> 自动跟主题) ----
function donut(a, size){
  size=size||170; var r=size/2-3, cx=size/2, cy=size/2, inner=r*0.58;
  var tot=QO.reduce(function(s,q){ return s+mean(a,q); },0);
  if(tot<=0) return '<svg width="'+size+'" height="'+size+'"></svg>';
  var a0=-Math.PI/2, segs="";
  QO.forEach(function(q){
    var val=mean(a,q); if(val<=0) return;
    var f=val/tot, a1=a0+f*2*Math.PI, big=f>0.5?1:0;
    var p=function(rad,ang){ return [cx+rad*Math.cos(ang), cy+rad*Math.sin(ang)]; };
    var A=p(r,a0), B=p(r,a1), Ci=p(inner,a1), D=p(inner,a0);
    segs+='<path d="M'+A[0].toFixed(1)+' '+A[1].toFixed(1)+' A'+r+' '+r+' 0 '+big+' 1 '
        + B[0].toFixed(1)+' '+B[1].toFixed(1)+' L'+Ci[0].toFixed(1)+' '+Ci[1].toFixed(1)
        + ' A'+inner+' '+inner+' 0 '+big+' 0 '+D[0].toFixed(1)+' '+D[1].toFixed(1)+' Z" '
        + 'fill="var(--'+q+')" stroke="var(--card)" stroke-width="1">'
        + '<title>'+QL[QO.indexOf(q)]+' '+fmt1(val)+' 件 ('+(100*f).toFixed(1)+'%)</title></path>';
    a0=a1;
  });
  return '<svg viewBox="0 0 '+size+' '+size+'" width="'+size+'" height="'+size+'">'+segs
    + '<text x="'+cx+'" y="'+(cy-1)+'" text-anchor="middle" font-size="19" font-weight="700" '
    + 'fill="var(--fg)">'+fmt1(tot)+'</text>'
    + '<text x="'+cx+'" y="'+(cy+15)+'" text-anchor="middle" font-size="10" fill="var(--dim)">件/局</text></svg>';
}

function stack(a){
  var tot=QO.reduce(function(s,q){ return s+mean(a,q); },0)||1;
  return '<div class="stk">'+QO.map(function(q){
    var p=100*mean(a,q)/tot;
    return p>0 ? '<i style="width:'+p+'%;background:var(--'+q+')" title="'+QL[QO.indexOf(q)]+' '
               + fmt1(mean(a,q))+' ('+p.toFixed(1)+'%)"></i>' : "";
  }).join("")+'</div>';
}

// 折线图: 每日、每品质的【每局均件】。这是这一页的旗舰用途 —— 官方悄悄调掉率, 就是在这里露馅。
// thin[i] = 第 i 天样本不足 -> 该点画成【空心小点】。这一步不能省: 小样本天的数值是噪音,
// 但在折线上它和正常天长得【一模一样】—— 而这张图的全部意义就是"看跳变", 于是噪音天正好
// 伪造出一个根本不存在的跳变。光在下面写一句注释是拦不住的, 必须在图上就能看出来。
function lineChart(days, series, thin, w, h){
  w=w||900; h=h||230; var pad={l:36,r:14,t:14,b:28};
  var iw=w-pad.l-pad.r, ih=h-pad.t-pad.b;
  var mx=0; QO.forEach(function(q){ series[q].forEach(function(v){ if(v>mx) mx=v; }); });
  mx=Math.max(mx,1);
  var X=function(i){ return pad.l+(days.length<=1 ? iw/2 : iw*i/(days.length-1)); };
  var Y=function(v){ return pad.t+ih*(1-v/mx); };
  var g='<line x1="'+pad.l+'" y1="'+pad.t+'" x2="'+pad.l+'" y2="'+(pad.t+ih)+'" stroke="var(--line)"/>'
      + '<line x1="'+pad.l+'" y1="'+(pad.t+ih)+'" x2="'+(w-pad.r)+'" y2="'+(pad.t+ih)+'" stroke="var(--line)"/>'
      + '<text x="'+(pad.l-4)+'" y="'+(pad.t+8)+'" text-anchor="end" font-size="9" fill="var(--dim)">'+fmt1(mx)+'</text>';
  // 样本不足的天: 背景画一条竖向浅带, 一眼看出"这一段别信"
  g+=days.map(function(d,i){ return !thin[i] ? "" :
      '<rect x="'+(X(i)-3).toFixed(1)+'" y="'+pad.t+'" width="6" height="'+ih+'" '
    + 'fill="var(--dim)" opacity="0.07"><title>'+esc(d)+' 样本不足, 数值是噪音</title></rect>'; }).join("");
  QO.forEach(function(q){
    var s=series[q];
    if(!s.some(function(v){ return v>0; })) return;
    g+='<polyline points="'+s.map(function(v,i){ return X(i).toFixed(1)+","+Y(v).toFixed(1); }).join(" ")
     + '" fill="none" stroke="var(--'+q+')" stroke-width="2"/>';
    g+=s.map(function(v,i){
      var t=QL[QO.indexOf(q)]+" "+days[i]+": "+fmt1(v)+(thin[i] ? " ⚠️样本不足, 是噪音" : "");
      return thin[i]
        ? '<circle cx="'+X(i).toFixed(1)+'" cy="'+Y(v).toFixed(1)+'" r="2.2" fill="var(--card)" '
          + 'stroke="var(--'+q+')" stroke-width="1.2"><title>'+t+'</title></circle>'
        : '<circle cx="'+X(i).toFixed(1)+'" cy="'+Y(v).toFixed(1)+'" r="2.8" fill="var(--'+q+')">'
          + '<title>'+t+'</title></circle>'; }).join("");
  });
  // x 轴标签: 天多了就抽稀, 否则糊成一坨
  var step=Math.ceil(days.length/10);
  g+=days.map(function(d,i){ return (i%step) ? "" :
      '<text x="'+X(i).toFixed(1)+'" y="'+(h-9)+'" text-anchor="middle" font-size="9" '
    + 'fill="var(--dim)">'+esc(d.slice(5))+'</text>'; }).join("");
  // ⚠️ 只给 width:100%, 【不写 height 属性】—— 写了 height 就会按 viewBox 做信箱化,
  // 图被缩在容器中间一小条。让高度由 viewBox 的宽高比自己撑出来。
  return '<svg viewBox="0 0 '+w+' '+h+'" width="100%" style="height:auto;display:block">'+g+'</svg>';
}

function legend(){
  return '<div class="lgd">'+QO.map(function(q,i){
    return '<span><b style="background:var(--'+q+')"></b>'+QL[i]+'</span>'; }).join("")+'</div>';
}

// ---- 筛选 UI ----
function opts(list, cur, labels){
  return '<option value="">全部</option>'+list.map(function(x,i){
    return '<option value="'+esc(x)+'"'+(String(x)===String(cur)?" selected":"")+'>'
         + esc(labels ? labels[i] : x)+'</option>'; }).join("");
}
function uniqueOpts(values, labels, cur){
  var seen={}, out=[];
  values.forEach(function(v,i){
    if(v && !seen[v]){seen[v]=true;out.push([v,(labels||[])[i]||v]);}
  });
  return '<option value="">全部</option>'+out.map(function(pair){
    return '<option value="'+esc(pair[0])+'"'+(pair[0]===cur?' selected':'')+'>'+esc(pair[1])+'</option>';
  }).join('');
}
function sourceOpts(){
  return (C.sources||["official"]).map(function(source,i){
    var label=(C.source_labels||["官方随机仓"])[i]||source;
    return '<option value="'+esc(source)+'"'+(source===F.source?' selected':'')+'>'+esc(label)+'</option>';
  }).join('');
}
function buildFilters(){
  var fams=[]; C.map_fams.forEach(function(f){ if(f && fams.indexOf(f)<0) fams.push(f); }); fams.sort();
  var sourceMaps={};
  for(var i=0;i<NC;i++){ if(sourceAt(i)===F.source) sourceMaps[C.m[i]]=true; }
  // 地图下拉跟着玩法/方式/活动/地图族共同收窄。
  var ms=C.maps.filter(function(m){
    return sourceMaps[m] && (!F.kind||mapKind(m)===F.kind) && (!F.mode||mapMode(m)===F.mode)
      && (!F.activity||mapActivity(m)===F.activity) && (!F.fam||mapFam(m)===F.fam);
  });
  var mlab=ms.map(function(m){ return mapName(m)+" "+m; });
  document.getElementById("flt").innerHTML =
      '<span class="fl">样本来源</span>'
    + '<select id="f-source" onchange="setF(\\'source\\',this.value)">'+sourceOpts()+'</select>'
    + '<span class="fl">日期</span>'
    + '<select id="f-dfrom" onchange="setF(\\'dfrom\\',this.value)">'+opts(C.days,F.dfrom)+'</select>'
    + '<span class="fl">~</span>'
    + '<select id="f-dto" onchange="setF(\\'dto\\',this.value)">'+opts(C.days,F.dto)+'</select>'
    + '<span class="fl">玩法</span>'
    + '<select id="f-kind" onchange="setF(\\'kind\\',this.value)">'+uniqueOpts(C.map_kinds,C.map_kind_labels,F.kind)+'</select>'
    + '<span class="fl">拍卖方式</span>'
    + '<select id="f-mode" onchange="setF(\\'mode\\',this.value)">'+uniqueOpts(C.map_modes,C.map_mode_labels,F.mode)+'</select>'
    + '<span class="fl">活动</span>'
    + '<select id="f-activity" onchange="setF(\\'activity\\',this.value)">'+uniqueOpts(C.map_activity,C.map_activity_labels,F.activity)+'</select>'
    + '<span class="fl">地图族</span>'
    + '<select id="f-fam" onchange="setF(\\'fam\\',this.value)">'+opts(fams,F.fam)+'</select>'
    + '<span class="fl">地图</span>'
    + '<select id="f-map" onchange="setF(\\'map\\',this.value)">'+opts(ms,F.map,mlab)+'</select>'
    + '<span class="fl">英雄</span>'
    + '<select id="f-hero" onchange="setF(\\'hero\\',this.value)">'+opts(C.heroes,F.hero,C.hero_cn)+'</select>'
    + '<button class="rst" onclick="resetF()">重置</button>';
}
function setF(k,v){
  F[k]=v;
  if(["source","kind","mode","activity","fam"].indexOf(k)>=0) F.map="";
  if(k==="map" && v){
    var m=Number(v);
    F.kind=mapKind(m);F.mode=mapMode(m);F.activity=mapActivity(m);F.fam=mapFam(m);
  }
  buildFilters(); renderExplore();
}
function resetF(){ F={source:"accepted",kind:"",mode:"",activity:"",fam:"",map:"",hero:"",dfrom:"",dto:""}; buildFilters(); renderExplore(); }

function filterMapStats(){
  var table=document.getElementById("mapStatsTable"); if(!table) return;
  var kind=(document.getElementById("mp-kind")||{}).value||"";
  var mode=(document.getElementById("mp-mode")||{}).value||"";
  var activity=(document.getElementById("mp-activity")||{}).value||"";
  var source=(document.getElementById("mp-source")||{}).value||"";
  var shown=0, total=0;
  table.querySelectorAll("tbody tr[data-kind]").forEach(function(row){
    total++;
    var ok=(!kind||row.dataset.kind===kind)&&(!mode||row.dataset.mode===mode)
      &&(!activity||row.dataset.activity===activity)&&(!source||row.dataset.source===source);
    row.style.display=ok?"":"none"; if(ok) shown++;
  });
  var count=document.getElementById("mp-filter-count");
  if(count) count.textContent="显示 "+shown+" / "+total+" 条分池统计";
  var empty=document.getElementById("mp-empty-state");
  if(empty) empty.hidden=shown!==0;
}

// ---- 主渲染 ----
function renderExplore(){
  var idx=hits(), a=agg(idx), K=MIN_N;
  var box=document.getElementById("expl");

  // 🔴 k-匿名门。没有这道门, 联合筛选 = "把任意单局挑出来看"。
  if(a.n < K){
    box.innerHTML = '<div class="gate"><b>样本 '+a.n+' 局 &lt; '+K+' 局</b><br>'
      + '这个筛选组合样本太少, <b>不出统计</b> —— 既是统计诚实(几局的"掉率"是噪音, 不是规律), '
      + '也是隐私底线(样本越少, 越接近把某一局单独挑出来看)。<br>放宽筛选条件再试。</div>';
    return;
  }

  var kpi = '<div class="kpis">'
    + '<div class="kpi"><b>'+a.n.toLocaleString()+'</b><span>对局</span></div>'
    + '<div class="kpi"><b>'+fmt1(a.c/a.n)+'</b><span>均件</span></div>'
    + '<div class="kpi"><b>'+fmt1(a.g/a.n)+'</b><span>均格</span></div>'
    + '<div class="kpi"><b>'+money(a.v*100/a.n)+'</b><span>均仓库值</span></div>'
    + '<div class="kpi"><b>'+(100*a.red/a.n).toFixed(0)+'%</b><span>出红率</span></div>'
    + '<div class="kpi"><b>'+(100*a.gold/a.n).toFixed(0)+'%</b><span>出金率</span></div></div>';

  // 品质表: 均件 ± σ / 均格 / 占比
  var tot=QO.reduce(function(s,q){ return s+mean(a,q); },0)||1;
  var rows=QO.map(function(q,i){
    var s=sd(a,q);
    return '<tr><td><span class="q '+q+'">'+QL[i]+'</span></td>'
      + '<td class="num" style="color:var(--'+q+');font-weight:600">'+fmt1(mean(a,q))
      + '<span class="pm">±'+(s==null?"?":fmt1(s))+'</span></td>'
      + '<td class="num">'+fmt1(a.ks[q]/a.n)+'</td>'
      + '<td class="num">'+(100*mean(a,q)/tot).toFixed(1)+'%</td></tr>';
  }).join("");
  var tbl='<div class="scroll"><table><thead><tr><th>品质</th><th class="num">每局均件</th>'
    + '<th class="num">每局均格</th><th class="num">占比</th></tr></thead><tbody>'+rows+'</tbody></table></div>';

  // 每日折线: 只画【本次筛选】命中的天, 且每天单独过 n 门(小样本天不画点, 否则又在造假跳变)
  var byDay={};
  idx.forEach(function(i){
    var d=C.days[C.d[i]];
    if(!byDay[d]) byDay[d]=[];
    byDay[d].push(i);
  });
  var days=Object.keys(byDay).sort();
  var series={}; QO.forEach(function(q){ series[q]=[]; });
  var thin=[], nthin=0;
  days.forEach(function(d){
    var da=agg(byDay[d]);
    var low = da.n < MIN_DAY;
    thin.push(low); if(low) nthin++;
    QO.forEach(function(q){ series[q].push(da.n ? mean(da,q) : 0); });
  });
  var lc = days.length>1
    ? '<div class="pad">'+legend()+lineChart(days,series,thin)+'</div>'
      + (nthin ? '<p class="note">⚠️ 这 '+days.length+' 天里有 <b>'+nthin+' 天</b>样本不足 '+MIN_DAY
              + ' 局(图上是<b>空心点 + 浅色竖带</b>)。那几天的数值是<b>噪音不是信号</b> —— '
              + '它们最容易伪造出一个根本不存在的跳变, <b>别拿它当"官方调掉率"的证据</b>。</p>' : "")
    : '<div class="pad" style="color:var(--dim);font-size:13px">只命中 1 天, 画不了趋势。</div>';

  box.innerHTML =
      kpi
    + '<div class="card"><h2>品质构成 <small>'+a.n.toLocaleString()+' 局</small></h2>'
    + '<div class="pad dsplit"><div>'+donut(a)+'</div><div class="dtbl">'+legend()+stack(a)+tbl+'</div></div></div>'
    + '<div class="card"><h2>每日趋势 <small>每局各品质均件</small></h2>'+lc+'</div>';
}

// ---- 静态分区的图(总览环图 / 时间趋势折线) ----
// 【复用上面同一套 donut / lineChart】。不另写服务端版本 —— 两份实现迟早会漂, 而图表漂了
// 是最难发现的一类错(表和图对不上, 但两边都"看起来对")。这两张图的数据也来自同一个 cube。
function renderStatic(){
  buildRedFilter(); renderRecentRed();
  var accepted=hits(), all=agg(accepted);   // 无筛选 = 已接纳主统计
  var d1=document.getElementById("ovDonut");
  if(d1){ d1.innerHTML=donut(all,180);
          document.getElementById("ovLgd").innerHTML=legend()+stack(all); }

  var d2=document.getElementById("tdChart");
  if(d2){
    var byDay={};
    accepted.forEach(function(i){
      var d=C.days[C.d[i]]; (byDay[d]=byDay[d]||[]).push(i);
    });
    var days=Object.keys(byDay).sort(), series={}, thin=[];
    QO.forEach(function(q){ series[q]=[]; });
    days.forEach(function(d){
      var da=agg(byDay[d]);
      thin.push(da.n < MIN_DAY);
      QO.forEach(function(q){ series[q].push(da.n ? mean(da,q) : 0); });
    });
    d2.innerHTML = days.length>1
      ? lineChart(days,series,thin)
      : days.length===1
        ? '<div style="color:var(--dim);font-size:13px">只有 1 天数据, 画不了趋势。</div>'
        : '<div style="color:var(--dim);font-size:13px">暂无已接纳趋势数据。</div>';
    document.getElementById("tdLgd").innerHTML=legend();
  }
}
"""


def build_public_html(agg: dict, *, generated_at: float | None = None,
                      rebuild_interval: float | None = None) -> str:
    """公开页(侧边栏 + 分区; 用 web_ui 的统一外壳)。

    与 /dashboard 的区别只在【隐私】: 这里没有任何昵称/uid/逐局明细, 只有聚合数字。
    胜率/均收益虽然是从 winner/player/winner_bid 算出来的, 但输出的只是聚合数, 且样本量
    低于 MIN_N_FOR_IDENTITY_DERIVED 的行一律不出这两列(既是隐私卫生, 也是统计诚实)。

    页面要【明说数据不是实时的】—— 公共统计每 30 分钟才重算一次(collector 的 PUBLIC_REBUILD_INTERVAL);
    不说清楚的话, 用户打完一局来看发现没变会以为坏了。
    """
    import sys
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    import web_ui as ui

    t = agg.get("totals") or {}
    maps = agg.get("maps") or []
    maps_by_source = agg.get("maps_by_source") or {_MAIN_SOURCE_SCOPE: maps}
    map_stats_rows = [
        row
        for scope in _PUBLIC_SOURCE_SCOPES
        for row in (maps_by_source.get(scope) or [])
    ]
    items = agg.get("items") or []
    heroes = agg.get("heroes") or []
    tiers = agg.get("tiers") or []
    trend = agg.get("trend") or []
    recent_red = agg.get("recent_red") or {"start": None, "end": None, "days": 7, "rows": []}
    window = agg.get("window") or {}

    gen_txt = _shanghai_format(generated_at, "%m-%d %H:%M") if generated_at else "-"
    latest_play = recent_red.get("end") or "-"
    mins = int((rebuild_interval or 1800) // 60)
    min_id = t.get("min_n_identity", MIN_N_FOR_IDENTITY_DERIVED)
    window_start = window.get("start") or "-"
    window_end = window.get("end") or "-"
    window_label = window.get("label") or "当前统计窗口"
    source_totals = t.get("source_scope_counts") or {}
    window_note = (
        f'{window_label} {window_start} ~ {window_end}（北京时间、两端包含） · '
        f'主统计 {int(t.get("samples") or 0):,} 局'
        f'（官方 {int(source_totals.get("official") or 0):,} / '
        f'待确认 {int(source_totals.get("unknown") or 0):,} / '
        f'玩家仓 {int(source_totals.get("player_custom") or 0):,}） · '
        f'来源冲突未计 {int(source_totals.get("conflict") or 0):,} 局 · '
        f'未知日期 {int(window.get("unknown_date_count") or 0):,} 局已排除'
    )

    # ⚠️ 品质色一律走【专用变量 --q1..--q6】, 【绝不】借语义变量(--acc/--gold/...)。
    # 曾经这里 q3->acc、q5->gold, 换暖色皮肤后 --acc=陶土橙, 于是【蓝品质被染成橙色】。
    # 现在 web_ui 里 --q* 是品质专用色、跟着明暗走, 与强调色 --acc 彻底解耦。
    # 名字/顺序都从 _QUALITY_LABELS 取(唯一真源, 已含"绿并入白")—— 别在这儿再抄一份, 会漂。
    QN = _QUALITY_LABELS
    QORD = list(_QUALITY_LABELS)

    # ---------- 总览 ----------
    all_n = sum(m["n"] for m in maps) or 1
    glob = {q: sum(m["avg_q"][q] * m["n"] for m in maps) / all_n for q in QORD}
    # ⚠️ 必须是 <b>值</b><span>标签</span>: web_ui 的 _CSS 里只有 .kpi b / .kpi span 两条规则。
    # 曾经这里写的是 <div class="k"> / <div class="l"> —— 那两个类名【CSS 里根本不存在】, 于是
    # 打开页面第一眼的 6 个大数字全渲染成无样式裸文本。/my 的 KPI 一直用的是 b/span, 是对的。
    kpis = "".join(
        f'<div class="kpi"><b>{v}</b><span>{k}</span></div>'
        for k, v in (("主统计样本", f'{t.get("samples", 0):,}'), ("覆盖地图", t.get("maps", 0)),
                     ("藏品种类", t.get("items", 0)), ("英雄", len(heroes)),
                     ("段位档", len(tiers)), ("统计天数", len(trend))))
    gtot = sum(glob.values()) or 1
    qrows = "".join(
        f'<tr><td><span class="q {q}">{QN[q]}</span></td>'
        f'<td class="num" style="color:var(--{q});font-weight:600">{glob[q]:.2f}</td>'
        f'<td class="num">{glob[q] / gtot * 100:.1f}%</td></tr>' for q in QORD)
    # ⚠️map_id 必须带上: 同名不同 id 的图真实存在(如"现代货轮娱乐库(活动)" 2520 与 4520),
    # 不带 id 的话这张表会出现两行看起来一模一样的地图, 读者只会以为是 bug。
    rrows = "".join(
        f'<tr><td class="name">{m["name"]}<span class="mid">{m["map_id"]}</span></td>'
        f'<td data-v="{m["avg_q6"]}" class="num" style="color:var(--red);font-weight:600">'
        f'{m["avg_q6"]:.2f}{_pm(m["sd_q6"])}</td><td data-v="{m["n"]}" class="num">{m["n"]}</td></tr>'
        for m in recent_red.get("rows", [])[:5])
    if not rrows:
        rrows = (f'<tr><td colspan="3" style="text-align:center;color:var(--dim)">'
                 f'该窗口暂无达到 {MIN_SAMPLES_TO_SHOW} 局的地图数据</td></tr>')
    ov = f'<div class="kpis">{kpis}</div>'
    # 环图和下面的表是【同一组数】的两种看法 —— 图给形状, 表给数字。图由 JS 从 cube 现画
    # (与"探索"是同一个 donut 实现, 不另写一份 —— 两份实现迟早会漂)。
    ov += ('<div class="card"><h2>全服平均每局品质构成</h2>'
           '<div class="pad dsplit"><div id="ovDonut"></div><div class="dtbl">'
           + '<div id="ovLgd"></div>' + _scroll(
               '<table><thead><tr><th>品质</th><th class="num">每局均件</th>'
               f'<th class="num">全服占比</th></tr></thead><tbody>{qrows}</tbody></table>')
           + '</div></div></div>')
    rr_window = (f'{recent_red.get("start") or "-"} ~ {recent_red.get("end") or "-"}')
    ov += ('<div class="card"><h2>红最多的图 · 最近 7 天'
           f'<small style="margin-left:8px">{rr_window} · 高置信</small>'
           '<button id="rrFilterBtn" class="filter-toggle" type="button" aria-expanded="false" '
           'onclick="toggleRedFilter()">筛选地图</button></h2>'
           '<div class="fltbar rrfilter" id="rrFilter" style="display:none"></div>'
           + _scroll('<table data-sortable id="rrTable"><thead><tr><th>地图</th><th class="num">红/局</th>'
                     f'<th class="num">样本</th></tr></thead><tbody id="rrBody">{rrows}</tbody></table>')
           + '</div>')

    # ---------- 地图 ----------
    vmax_by_source = {
        scope: max(
            (m["avg_value"] for m in (maps_by_source.get(scope) or [])),
            default=0,
        ) or 1
        for scope in _PUBLIC_SOURCE_SCOPES
    }
    mrows = []
    for m in map_stats_rows:
        low = m.get("low_confidence")
        cls = ' class="lowconf"' if low else ""
        tag = ' <span class="tag">样本少</span>' if low else ""
        activity_tags = [
            f'<span class="tag">{m["map_kind_label"]}</span>',
            f'<span class="tag">{m["auction_mode_label"]}</span>',
        ]
        if m["map_kind"] == "activity":
            activity_tags.append(
                f'<span class="tag activity-{m["activity_status"]}">'
                f'{m["activity_status_label"]}</span>'
            )
            if m.get("activity_cohort"):
                activity_tags.append(f'<span class="map-cohort">{m["activity_cohort"]}</span>')
        source_counts = m["source_scope_counts"]
        source_title = (
            f'官方 {source_counts["official_samples"]} / '
            f'玩家仓 {source_counts["player_warehouse_samples"]} / '
            f'待确认 {source_counts["unknown_samples"]} / '
            f'冲突 {source_counts["conflict_samples"]}'
        )
        vw = min(
            100,
            round(m["avg_value"] / vmax_by_source[m["source_scope"]] * 100),
        )
        source_breakdown = (
            f'官方 {source_counts["official_samples"]} · '
            f'玩家仓 {source_counts["player_warehouse_samples"]} · '
            f'待确认 {source_counts["unknown_samples"]} · '
            f'冲突 {source_counts["conflict_samples"]}'
        )
        secondary_quality = "".join(
            f'<div class="map-secondary-item"><span>{_QUALITY_LABELS[q]}/局</span>'
            f'<b style="color:var(--{q})">{m["avg_q"][q]:.2f}</b>{_pm(m["sd_q"][q])}</div>'
            for q in ("q1", "q3", "q4", "q5")
        )
        secondary = (
            '<details class="map-secondary-details">'
            '<summary class="map-secondary-toggle">查看品质与来源</summary>'
            '<div class="map-secondary-grid">'
            + secondary_quality
            + f'<div class="map-secondary-item"><span>均件</span><b>{m["avg_count"]:.0f}</b></div>'
            f'<div class="map-secondary-item"><span>均格</span><b>{m["avg_cells"]:.0f}</b></div>'
            f'<div class="map-secondary-item map-secondary-wide"><span>品质构成</span>'
            f'{_stack_bar(m["avg_q"], QORD)}</div>'
            f'<div class="map-secondary-item map-secondary-wide"><span>来源构成</span>'
            f'<b title="{source_title}">{m["source_label"]}</b>'
            f'<small>{source_breakdown}</small></div>'
            '</div></details>'
        )
        mrows.append(
            f'<tr{cls} data-kind="{m["map_kind"]}" data-mode="{m["auction_mode"]}" '
            f'data-activity="{m["activity_status"]}" data-source="{m["source_scope"]}">'
            f'<td class="name map-identity" data-label="地图与活动">'
            f'<div class="map-title">{m["name"]}{tag}<span class="mid">{m["map_id"]}</span></div>'
            f'<div class="map-tags">{"".join(activity_tags)}</div>{secondary}</td>'
            f'<td data-v="{m["n"]}" class="num map-primary-metric" data-label="样本">{m["n"]}</td>'
            f'<td data-v="{m["avg_value"]}" class="num strong map-primary-metric" data-label="平均总价值">'
            f'<div class="inbar"><span>{ui.money(m["avg_value"])}</span>'
            f'<span class="track"><i style="width:{vw}%;background:var(--acc)"></i></span></div></td>'
            f'<td data-v="{m["avg_q"]["q6"]}" class="num map-primary-metric" data-label="红/局">'
            f'<span style="color:var(--q6);font-weight:650">{m["avg_q"]["q6"]:.2f}</span>'
            f'{_pm(m["sd_q"]["q6"])}</td>'
            f'<td data-v="{m["red_rate"]}" class="num map-primary-metric" data-label="出红率">'
            f'<span style="color:var(--q6);font-weight:650">{m["red_rate"] * 100:.0f}%</span></td>'
            '</tr>')
    def _map_filter_options(key: str, label_key: str, order: tuple[str, ...]) -> str:
        labels = {
            m[key]: m[label_key] for m in map_stats_rows
            if m.get(key) and m.get(label_key)
        }
        return '<option value="">全部</option>' + "".join(
            f'<option value="{value}">{labels[value]}</option>'
            for value in order if value in labels
        )

    kind_options = _map_filter_options(
        "map_kind", "map_kind_label", ("regular", "ranked", "activity")
    )
    mode_options = _map_filter_options(
        "auction_mode", "auction_mode_label", ("open", "sealed", "training", "unknown")
    )
    activity_options = _map_filter_options(
        "activity_status",
        "activity_status_label",
        ("none", "current", "historical", "pending"),
    )
    present_source = {m["source_scope"] for m in map_stats_rows}
    source_options = "".join(
        f'<option value="{value}"'
        f'{" selected" if value == _MAIN_SOURCE_SCOPE else ""}>'
        f'{_SOURCE_SCOPE_LABELS[value]}</option>'
        for value in _PUBLIC_SOURCE_SCOPES
        if value in {_MAIN_SOURCE_SCOPE, "official"} or value in present_source
    )
    default_map_count = len(maps_by_source.get(_MAIN_SOURCE_SCOPE) or [])
    mp_filters = (
        '<div class="fltbar" id="mapStatsFilter">'
        '<span class="fl">玩法</span>'
        f'<select id="mp-kind" onchange="filterMapStats()">{kind_options}</select>'
        '<span class="fl">拍卖方式</span>'
        f'<select id="mp-mode" onchange="filterMapStats()">{mode_options}</select>'
        '<span class="fl">活动批次</span>'
        f'<select id="mp-activity" onchange="filterMapStats()">{activity_options}</select>'
        '<span class="fl">来源</span>'
        f'<select id="mp-source" onchange="filterMapStats()">{source_options}</select>'
        f'<span class="fl" id="mp-filter-count">显示 {default_map_count} / '
        f'{len(map_stats_rows)} 条分池统计</span>'
        '</div>'
    )
    map_table_css = """
<style>
.map-stats-scroll .scroll{overflow:visible}
.map-stats-table{table-layout:fixed}
.map-stats-table th:nth-child(1){width:40%;text-align:left}
.map-stats-table th:nth-child(2){width:10%}
.map-stats-table th:nth-child(3){width:20%}
.map-stats-table th:nth-child(4){width:17%}
.map-stats-table th:nth-child(5){width:13%}
.map-stats-table td{white-space:normal;vertical-align:top}
.map-stats-table .map-primary-metric{white-space:nowrap;vertical-align:top}
.map-empty-state{margin:0;padding:28px 16px;border:1px solid var(--line);border-top:0;text-align:center;color:var(--dim)}
.map-empty-state[hidden]{display:none!important}
.map-title{font-weight:600;line-height:1.45;overflow-wrap:anywhere}
.map-tags{display:flex;flex-wrap:wrap;align-items:center;gap:5px;margin-top:5px}
.map-tags .tag{margin-left:0}
.map-cohort{color:var(--dim);font-size:11px}
.activity-current{color:var(--pos);border:1px solid color-mix(in srgb,var(--pos) 35%,transparent);background:transparent}
.map-secondary-details{display:block;width:100%;margin-top:7px;color:var(--dim);font-size:11px}
.map-secondary-toggle{display:inline-flex;align-items:center;min-height:30px;cursor:pointer;color:var(--acc);font-weight:600;list-style:none}
.map-secondary-toggle::-webkit-details-marker{display:none}
.map-secondary-toggle::after{content:" ▸";margin-left:4px}
.map-secondary-details[open] .map-secondary-toggle::after{content:" ▾"}
.map-secondary-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;margin-top:6px;padding:9px;border-radius:8px;background:var(--card2)}
.map-secondary-item{min-width:0}
.map-secondary-item>span{display:block;color:var(--dim);font-size:10px}
.map-secondary-item>b{display:block;color:var(--fg);font-size:11px;overflow-wrap:anywhere}
.map-secondary-item>small{display:block;color:var(--dim);font-size:10px;overflow-wrap:anywhere}
.map-secondary-item .pm{margin:2px 0 0}
.map-secondary-wide{grid-column:1/-1}
.map-secondary-wide .stk.sm{width:100%;margin-top:4px}
@media(max-width:760px){
  .map-stats-table,.map-stats-table tbody{display:block;width:100%}
  .map-stats-table thead{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap}
  .map-stats-table tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));margin:0 0 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);overflow:hidden}
  .map-stats-table td{display:flex;flex-direction:column;align-items:flex-start;justify-content:flex-start;min-width:0;padding:10px 12px;border-bottom:1px solid var(--line);text-align:left}
  .map-stats-table td::before{content:attr(data-label);color:var(--dim);font-size:10px;font-weight:600;margin-bottom:3px}
  .map-stats-table td.map-identity{grid-column:1/-1}
  .map-stats-table td.map-identity::before{display:none}
  .map-stats-table td:nth-child(4),.map-stats-table td:nth-child(5){border-bottom:0}
  .map-stats-table .inbar{justify-content:flex-start;flex-wrap:wrap}
  .map-stats-table .map-secondary-toggle{min-height:44px}
  .map-secondary-grid{grid-template-columns:repeat(2,minmax(0,1fr));width:100%}
}
</style>
"""
    mp_table = (
        '<table data-sortable id="mapStatsTable" class="map-stats-table"><thead><tr>'
        '<th>地图与活动</th><th class="num">样本</th><th class="num">平均总价值</th>'
        '<th class="num">红/局</th><th class="num">出红率</th>'
        '</tr></thead><tbody>' + "".join(mrows) + '</tbody></table>'
    )
    mp = (mp_filters + map_table_css + '<div class="map-stats-scroll">' + _scroll(mp_table)
          + '</div><p id="mp-empty-state" class="map-empty-state" hidden>'
          + '暂无符合当前筛选条件的地图统计，请调整筛选或等待更多样本。</p>')
    mp += (f'<p class="note">⭐ <b>±是标准差(波动)</b> —— 均红 <b>2.0±0.4</b> 和 <b>2.0±2.5</b> 是完全不同的两张图:'
           f'前者稳定开 2 个红, 后者要么空要么爆。<b>波动大的图更该按保守档出价。</b><br>'
           f'"出红率"=出现过红的局占比。样本 &lt; {t.get("min_samples_to_show", 20)} 的图标"样本少"并置灰 —— 小样本会骗人。<br>'
           f'<b>地图族</b>只表示底图题材；<b>玩法、明拍/暗拍、活动批次、来源</b>分别独立分类。'
           f'默认展示<b>主统计（已接纳）</b>：合并官方、来源待确认与已识别玩家仓，'
           f'以保留当前有效样本量；来源冲突与服务端 quarantine 永不纳入。'
           f'四类原始来源仍可在“来源”中单独查看，便于诊断和以后恢复严格分池。'
           f'默认排序以<b>样本量</b>为主；仅已确认正在开放的活动可置前，往期或待确认活动统一沉底。'
           f'排位、可信暗拍和已登记活动仍按规则识别为官方来源；任何与地图规则冲突的显式来源'
           f'只进入冲突池，永不影响主统计指标。</p>')

    # ---------- 英雄 ----------
    hrows = []
    for h in heroes:
        cls = ' class="lowconf"' if h["low_confidence"] else ""
        wr = f'{h["win_rate"] * 100:.0f}%' if h["win_rate"] is not None else "—"
        hrows.append(
            f'<tr{cls}><td class="name">{h["name"]}<span class="mid">{h["hero"]}</span></td>'
            f'<td data-v="{h["n"]}" class="num">{h["n"]:,}</td>'
            f'<td data-v="{h["pick_rate"]}" class="num">{h["pick_rate"] * 100:.1f}%</td>'
            f'<td data-v="{h["win_rate"] or -1}" class="num">{wr}</td>'
            + _profit_cell(h["avg_profit"], h.get("profit_sem"), ui.money)
            + f'<td data-v="{h["avg_value"]}" class="num">{ui.money(h["avg_value"])}</td>'
            + f'<td class="stkcell">{_stack_bar(h["avg_q"], QORD)}</td>'
            + "".join(_qcell(h["avg_q"][q], h["sd_q"][q], q, q in ("q5", "q6")) for q in QORD)
            + f'<td data-v="{h["red_rate"]}" class="num">{h["red_rate"] * 100:.0f}%</td></tr>')
    hr = _scroll('<table data-sortable><thead><tr><th>英雄</th><th class="num">样本</th>'
          '<th class="num">出场率</th><th class="num">胜率</th><th class="num">均收益</th>'
          '<th class="num">均拍品值</th><th class="nosort">品质构成</th>'
          + "".join(f'<th class="num">{QN[q]}/局</th>' for q in QORD)
          + '<th class="num">出红率</th></tr></thead><tbody>' + "".join(hrows) + "</tbody></table>")
    # ⚠️必须说清"掉率列 ≠ 英雄属性"。掉落只由【地图】决定, 英雄技能只改变你【看到】多少信息,
    # 不改变箱子里有什么。所以这几列的英雄间差异, 全部是"玩这个英雄的人爱打哪张图"的投影
    # (陈美红 8.89/局 ≈ 她的玩家扎堆打隐秘拍卖会, 那张图本身红 9.31)。不写这句, 群友就会
    # 照着这张表"选英雄刷红" —— 那是这一页最容易造成的实际误导。
    hr += (f'<p class="note">🔴 <b>掉率列不是英雄属性!</b> 掉落只由<b>地图</b>决定 —— 英雄技能只改变你'
           f'<b>看到</b>多少信息,不改变箱子里有什么。这几列的英雄间差异,反映的是'
           f'<b>"玩该英雄的人爱打哪张图"</b>,不是"选他能多出红"。想看真掉率请去'
           f'<a href="#mp">地图掉率</a>。<br>'
           f'⚠️ <b>「均拍品值」同理</b>:仓库总值也是地图决定的,所以这一列同样是<b>选图偏好的投影</b>,'
           f'不是"选他能开出更值钱的仓"。<b>「均收益」则更进一步</b> —— 它还叠了一层'
           f'<b>"玩该英雄的是谁、出价习惯如何"</b>,英雄本身的贡献混在里面分不出来。<br>'
           f'<b>均收益</b> = 每局平均盈亏:每位玩家(含拿仓者)均获赢家亏损×10%的分红;'
           f'拍下 = 藏品真值 − 自己出价 + 分红 − 门票;没拍下 = 分红 − 门票。<b>正=金 / 负=红</b>,<b>±是均值的标准误</b> —— '
           f'区间跨 0 就说明该英雄和"不赚不亏"分不开(实测<b>大多数英雄都跨 0</b>)。<br>'
           f'⚠️ 胜率/均收益是<b>全服所有上传者的聚合</b>,不是任何个人的战绩;样本 &lt; {min_id} 的英雄'
           f'<b>不出这两列</b>(小样本既没有统计意义, 也不该把少数人的战绩当公开数据)。</p>')

    # ---------- 段位 ----------
    tmax = max((x["n"] for x in tiers), default=1) or 1
    ttot = sum(x["n"] for x in tiers) or 1
    trows = []
    for x in tiers:
        cls = ' class="lowconf"' if x["low_confidence"] else ""
        rg = f'{x["pts_lo"]:,} ~ {x["pts_hi"]:,}' if x["pts_lo"] is not None else "—"
        bw = round(x["n"] / tmax * 100)
        trows.append(
            f'<tr{cls}><td class="name"><b>{x["name"]}</b><span class="mid">#{x["tier"]}</span></td>'
            f'<td class="num" style="color:var(--dim)">{rg}</td>'
            f'<td data-v="{x["n"]}" class="num">{x["n"]:,}</td>'
            f'<td data-v="{x["share"]}" class="num strong"><div class="inbar">'
            f'<span>{x["share"] * 100:.1f}%</span>'
            f'<span class="track"><i style="width:{bw}%;background:var(--gold)"></i></span></div></td>'
            f'<td data-v="{x["avg_pts"] or 0}" class="num">{x["avg_pts"] or "—"}</td>'
            f'<td data-v="{x["avg_value"]}" class="num">{ui.money(x["avg_value"])}</td>'
            + _profit_cell(x["avg_profit"], x.get("profit_sem"), ui.money) + "</tr>")
    tr = _scroll('<table data-sortable><thead><tr><th>段位</th><th>分数区间</th><th class="num">局数</th>'
          '<th class="num">占比</th><th class="num">均排位分</th><th class="num">均拍品值</th>'
          '<th class="num">均收益</th></tr></thead><tbody>' + "".join(trows) + "</tbody></table>")
    tr += (f'<p class="note">共 <b>{ttot:,}</b> 局排位样本。段位名按<b>英雄联盟式</b>映射'
           f'(小段位 <b>1 = 该大段位最高</b>),<b>分数区间</b>一并列出以便核对 —— 但样本 '
           f'&lt; {min_id} 的档<b>不出分数</b>:那是该档<b>实际观测到</b>的分数范围而非段位边界,'
           f'顶段只有一两个人上传数据,印出来就等于公开某个人的精确排位分。<br>'
           f'账号<b>初始积分 1200 = 黑铁1</b> —— 所以那一档占比特别高(大多是没打过排位/刚重置的号),'
           f'<b>属正常</b>,不是数据异常。<br>'
           f'⚠️ <b>均收益的 ± 是均值的标准误</b>。单局盈亏是几十万级的重尾分布,而每档只有几百局 ——'
           f'实测 <b>32 档里有 29 档的 95% 区间跨过 0</b>,也就是说<b>段位之间的收益差异基本分不出来</b>。'
           f'而且在 32 个档上比较,哪怕真实差异全为 0,也会有一两档纯靠运气显得"显著"。'
           f'<b>别拿某一档的正负号当结论</b>(比如"白银2最亏")。</p>')

    # ---------- 趋势 ----------
    trrows = []
    trend_source_counts = {
        row["day"]: row
        for row in (agg.get("trend_source_counts") or [])
        if isinstance(row, dict) and isinstance(row.get("day"), str)
    }
    # 聚合/cube 仍按日期正序供折线图绘制；只把阅读型表格默认排成最新日期在前。
    for d in reversed(trend):
        low = d.get("low_confidence")
        cls = ' class="lowconf"' if low else ""
        tag = ' <span class="tag">样本少</span>' if low else ""
        source_counts = trend_source_counts.get(d["day"]) or {}
        accepted_n = int(source_counts.get(_MAIN_SOURCE_SCOPE) or d["n"])
        official_n = int(source_counts.get("official") or 0)
        unknown_n = int(source_counts.get("unknown") or 0)
        player_n = int(source_counts.get("player_custom") or 0)
        conflict_n = int(source_counts.get("conflict") or 0)
        trrows.append(
            f'<tr{cls}><td class="name">{d["day"]}{tag}</td>'
            f'<td data-v="{accepted_n}" class="num strong">{accepted_n:,}</td>'
            f'<td data-v="{official_n}" class="num">{official_n:,}</td>'
            f'<td data-v="{unknown_n}" class="num">{unknown_n:,}</td>'
            f'<td data-v="{player_n}" class="num">{player_n:,}</td>'
            f'<td data-v="{conflict_n}" class="num">{conflict_n:,}</td>'
            + "".join(f'<td data-v="{d["avg_q"][q]}" class="num" style="color:var(--{q})">'
                      f'{d["avg_q"][q]:.2f}</td>' for q in QORD)
            + f'<td data-v="{d["avg_value"]}" class="num">{ui.money(d["avg_value"])}</td></tr>')
    # 折线图放在表【上面】—— 这一节的用途是"看跳变", 而跳变是一眼看图看出来的, 不是逐行读表读出来的。
    # 图由 JS 从 cube 现画(与"探索"同一个 lineChart 实现, 不另写一份)。
    td = ('<div class="card"><h2>每日掉率 <small>每局各品质均件</small></h2>'
          '<div class="pad"><div id="tdLgd"></div><div id="tdChart"></div></div></div>'
          + _scroll('<table data-sortable data-default-sort-col="0" data-default-sort-dir="-1"><thead><tr><th>日期(北京)</th><th class="num">主统计样本</th>'
                    '<th class="num">其中官方</th><th class="num">来源待确认</th>'
                    '<th class="num">玩家仓</th><th class="num">冲突(未计)</th>'
                    + "".join(f'<th class="num">{QN[q]}/局</th>' for q in QORD)
                    + '<th class="num">均拍品值</th></tr></thead><tbody>'
                    + "".join(trrows) + "</tbody></table>"))
    td += (f'<p class="note">每天的<b>每局平均件数</b>。掉率若在某天前后整体跳变, 说明官方调过掉率 —— '
           f'这也是本节最有价值的用途(拿它去对照自己的手感)。<br>'
           f'<b>掉率折线、均值和主排行统一使用“主统计样本”</b>：官方、来源待确认和'
           f'已识别玩家仓均来自服务端 accepted 主库，当前合并使用以保留样本量。'
           f'来源标签仍保留供诊断；来源冲突和 quarantine 样本不进入主统计。<br>'
           f'⚠️ 样本 &lt; {MIN_N_FOR_TREND_DAY} 的天:表里<b>置灰标"样本少"</b>, 图上是'
           f'<b>空心点 + 浅色竖带</b>。那些天的数字是噪音, <b>不要把它们的高低当成"官方调了掉率"</b> —— '
           f'恰恰相反, 它们最容易伪造出一个根本不存在的跳变。<br>'
           f'想看<b>某张图 / 某个英雄</b>的掉率随时间怎么走 → 去<a href="#ex">探索</a>筛。</p>')

    # ---------- 藏品 ----------
    irows = []
    for i in items:
        q = i.get("quality") or 0
        irows.append(
            f'<tr><td class="name">{i["name"]}</td>'
            f'<td><span class="q q{q}">{_QUALITY_LABELS.get(f"q{q}", "?")}</span></td>'
            f'<td data-v="{i["value"] or 0}" class="num strong">{ui.money(i["value"] or 0)}</td>'
            f'<td data-v="{i["seen"]}" class="num">{i["seen"]}</td></tr>')
    it = _scroll('<table data-sortable><thead><tr><th>藏品</th><th>品质</th>'
          '<th class="num">价值</th><th class="num">出现次数</th></tr></thead><tbody>'
          + "".join(irows) + "</tbody></table>")
    it += ('<p class="note">只统计<b>金/红</b>(稀有藏品)。"出现次数"= 在样本里被开出的局数, '
           '是稀有度的经验证据(次数越少越稀有)。</p>')

    # ---------- 探索(联合筛选 + 图表) ----------
    cube = agg.get("cube") or {}
    ex = ('<div class="fltbar" id="flt"></div>'
          '<div id="expl"></div>'
          f'<p class="note">🔒 <b>这一页没有逐局数据 —— 一条都没有。</b>筛选是在 '
          f'<b>{len(cube.get("n") or []):,} 个预聚合格子</b>(地图 × 英雄 × 日期)上做的:'
          f'任意组合的均值和 σ 都是这些格子里<b>和</b>的函数, 所以不需要原始行也能算准'
          f'(与逐局直算<b>逐位等价</b>)。<br>'
          f'⚠️ 筛完样本 &lt; <b>{MIN_SAMPLES_TO_SHOW}</b> 局就<b>整块不出统计</b> —— '
          f'既是统计诚实(几局的"掉率"是噪音), 也是隐私底线(样本越少越接近把某一局单独挑出来看)。<br>'
          f'这里<b>只做掉率/件数/格数/价值</b>。<b>收益和胜率不在这</b>:它们是从赢家/出价算出来的, '
          f'一旦能筛到很小的样本就等于把出价还原出来 —— 那两个数只以 n≥{min_id} 的聚合形式出现在'
          f'<a href="#hr">英雄</a>和<a href="#tr">段位</a>表里。</p>')

    side = ui.sidebar([
        ("概况", [("ov", "总览", "var(--acc)")]),
        ("掉率", [("mp", "地图掉率", "var(--red)"),
                  ("td", "时间趋势", "var(--purple)"),
                  ("ex", "探索(联合筛选)", "var(--q3)")]),
        ("对比", [("hr", "英雄", "var(--gold)"),
                  ("tr", "段位", "var(--ok)")]),
        ("参考", [("it", "藏品价格", "var(--purple)")]),
    ])

    body = (
        _sec("ov", "总览", f'{window_note} · 每 {mins} 分钟更新一次(重建统计) · 最新游玩日 {latest_play} · 页面生成于 {gen_txt}(北京时间)', ov)
        + _sec("mp", "地图掉率与收益", "每张图的期望件数 ± 波动 · 点表头排序", mp)
        + _sec("td", "时间趋势", "掉率随日期的变化 —— 用来发现官方调掉率", td)
        + _sec("ex", "探索", "地图 × 英雄 × 日期 任意组合 —— 筛完实时重算", ex)
        + _sec("hr", "英雄", "出场 / 胜率 / 均收益 —— 掉率列是选图偏好, 不是英雄属性", hr)
        + _sec("tr", "段位分布", "各段位的局数占比 / 均拍品值 / 均收益", tr)
        + _sec("it", "藏品价格表", f'{t.get("items", 0)} 种金/红藏品 · 点表头排序', it)
    )

    # cube 直接内联(gz 后 ~0.12 MB)。⚠️ </script> 会提前闭合 <script> 标签 —— 必须转义。
    cube_js = json.dumps(cube, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    recent_js = json.dumps(recent_red, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    extra = (f'<script>var CUBE={cube_js},RECENT_RED={recent_js},'
             f'MIN_N={MIN_SAMPLES_TO_SHOW},MIN_DAY={MIN_N_FOR_TREND_DAY};</script>'
             f'<script>{_EXPLORE_JS}</script>'
             f'<script>buildFilters();renderExplore();renderStatic();filterMapStats();</script>')

    foot = (f'数据来自公版客户端默认上传的对局结算。服务器私有域保留逐局业务数据;'
            f'本公共页面只输出<b>去身份统计聚合</b>,敏感细分执行最小样本门槛,'
            f'不展示昵称、账号/uid 标识、session、逐局出价或逐局记录。'
            f'当前公共统计为<b>{window_label}</b>（{window_start} ~ {window_end}，北京时间、两端包含），'
            f'未知/非法 client_ts 不混入窗口。公共统计每 <b>{mins} 分钟更新一次</b>(重建统计), <b>不是实时的</b> —— 最新游玩日 {latest_play}, 页面生成于 {gen_txt}(北京时间)。<br>'
            f'想看<b>自己</b>的对局记录 → <a href="/my">我的记录</a>(需激活 token)。')
    return ui.page(title="拍卖大数据", active="public", body=body, foot=foot, side=side,
                   wide=True, extra=extra)


class JsonlSampleSource:
    """可重复迭代的有界 JSONL 来源；每一轮只保留当前行，不物化全库。"""

    def __init__(
        self,
        path: Path,
        *,
        start: date | None = None,
        end: date | None = None,
        snapshot_bytes: int | None = None,
        history_index=None,
    ):
        self.path = Path(path)
        self.start = start
        self.end = end
        if snapshot_bytes is None:
            try:
                snapshot_bytes = self.path.stat().st_size
            except OSError:
                snapshot_bytes = 0
        self.snapshot_bytes = max(0, int(snapshot_bytes))
        self.last_parsed_count = 0
        self.last_matched_count = 0
        self.last_unknown_date_count = 0
        self.passes = 0
        self.payload_read_bytes = self.payload_read_rows = 0
        self.total_payload_read_bytes = self.total_payload_read_rows = 0
        self._indexed = None
        if history_index is not None:
            if self.path.resolve() != history_index.sources["main"].resolve():
                raise ValueError("history index source mismatch")
            self._indexed = history_index.rows(view="public", start=start, end=end, snapshot_bytes=snapshot_bytes)

    def __iter__(self) -> Iterator[dict]:
        parsed = matched = unknown = 0
        self.passes += 1
        self.payload_read_bytes = self.payload_read_rows = 0
        if self._indexed is not None:
            for rec in self._indexed:
                if not isinstance(rec, dict):
                    continue
                if self.start is not None or self.end is not None:
                    day = _day_of(rec)
                    if day is None:
                        continue
                    day_value = date.fromisoformat(day)
                    if self.start is not None and day_value < self.start:
                        continue
                    if self.end is not None and day_value > self.end:
                        continue
                matched += 1
                yield rec
            self.last_parsed_count = self._indexed.last_parsed_count
            self.last_matched_count = matched
            self.last_unknown_date_count = self._indexed.last_unknown_date_count
            self.payload_read_bytes = self._indexed.bytes_read
            self.payload_read_rows = self._indexed.rows_read
            self.total_payload_read_bytes += self.payload_read_bytes
            self.total_payload_read_rows += self.payload_read_rows
            return
        try:
            stream = self.path.open("rb")
        except OSError:
            self.last_parsed_count = self.last_matched_count = self.last_unknown_date_count = 0
            return
        with stream:
            remaining = self.snapshot_bytes
            while remaining > 0:
                raw_line = stream.readline(remaining)
                if not raw_line:
                    break
                remaining -= len(raw_line)
                self.payload_read_bytes += len(raw_line)
                self.payload_read_rows += 1
                self.total_payload_read_bytes += len(raw_line)
                self.total_payload_read_rows += 1
                line = raw_line.decode("utf-8", errors="replace")
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if not isinstance(rec, dict):
                    continue
                parsed += 1
                if self.start is not None or self.end is not None:
                    day = _day_of(rec)  # public 隐私合同：只认 client_ts 的北京时间自然日。
                    if day is None:
                        unknown += 1
                        continue
                    day_value = date.fromisoformat(day)
                    if self.start is not None and day_value < self.start:
                        continue
                    if self.end is not None and day_value > self.end:
                        continue
                matched += 1
                yield rec
        self.last_parsed_count = parsed
        self.last_matched_count = matched
        self.last_unknown_date_count = unknown


def load_samples(
    path: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    snapshot_bytes: int | None = None,
    history_index=None,
) -> JsonlSampleSource:
    """返回可重复流式来源；坏行跳过，日期窗口在聚合/序列化前生效。"""
    return JsonlSampleSource(
        path,
        start=start,
        end=end,
        snapshot_bytes=snapshot_bytes,
        history_index=history_index,
    )
