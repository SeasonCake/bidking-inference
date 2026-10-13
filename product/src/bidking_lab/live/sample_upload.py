# -*- coding: utf-8 -*-
"""客户端样本自动上传(配合 scripts/sample_collector_server.py)。

monitor 结算时调用 maybe_upload_settlement(artifact):若配了 BIDKING_SAMPLE_UPLOAD_URL,
把【裁剪+匿名化后】的结算统计 best-effort POST 到收集服务器。**绝不阻塞 / 绝不抛异常**
(后台 daemon 线程 + 短超时 + 全 try/except),失败静默——采样是锦上添花,不能影响对局。

隐私:发 gameplay 统计(map_id + 每品质件数/格子/价值 + 占格/总件 + 是否残缺)+ **游戏内昵称**
(本机/各家/赢家)+ 排位分(含各玩家 player_id)——**作者已授权**(已知协作群内聚合分析,见 AGENTS §0)。
session_id 本地 SHA1 截断成 session_hash 仅用于去重,不发原始 session_id、不发 IP、不发原始抓包。
代码层只有拿到 URL/密钥才上传；正式公版按作者决定内置加密上传配置，激活后默认自动上传。
源码开发/自建包没有配置时仍不上传。0.2.2 起走 HMAC 签名,ingest key 不过网。

上传身份由当前 HTTPS 激活响应中的短期服务器签名 lease 提供；客户端不再计算或声明 uid_hash。
个人记录索引由 collector 验证 lease、实时复核撤销/设备绑定后在服务端私有侧生成。
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from ..http_tls import urlopen_verified
from .monitor import _settlement_category_model_map_matches_physical
from .warehouse_source import (
    D66AEE_PLAYER_WAREHOUSE_GENERATION,
    HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
    OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
    OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION,
    OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION,
    POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
    POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    UNKNOWN_WAREHOUSE_GENERATION,
)

CLIENT_VERSION = "sample_upload_v0"
WAREHOUSE_SOURCE_SCHEMA = "player_warehouse_source_pw2_4ae819_v4"
WAREHOUSE_SOURCE_SCHEMA_D66AEE = "player_warehouse_source_pw2_d66aee_v3"
WAREHOUSE_SOURCE_SCHEMA_OFFICIAL_20260903 = (
    "player_warehouse_source_pw2_eb1e8f_v5"
)
WAREHOUSE_SOURCE_SCHEMA_HOTFIX_20260903 = (
    "player_warehouse_source_pw2_68171c_v6"
)
WAREHOUSE_SOURCE_SCHEMA_OFFICIAL_20260910 = "player_warehouse_source_pw2_b049c3_v7"
WAREHOUSE_SOURCE_SCHEMA_OFFICIAL_20261008 = "player_warehouse_source_pw2_76b7ca_v8"
_EXACT_DISK_WAREHOUSE_SCHEMAS = {
    WAREHOUSE_SOURCE_SCHEMA_OFFICIAL_20260903: (
        OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION
    ),
    WAREHOUSE_SOURCE_SCHEMA_HOTFIX_20260903: (
        HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION
    ),
    WAREHOUSE_SOURCE_SCHEMA_OFFICIAL_20260910: OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION,
    WAREHOUSE_SOURCE_SCHEMA_OFFICIAL_20261008: OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION,
}
_WAREHOUSE_SCHEMA_BY_PROFILE_GENERATION = {
    D66AEE_PLAYER_WAREHOUSE_GENERATION: WAREHOUSE_SOURCE_SCHEMA_D66AEE,
    POSTUPDATE_PLAYER_WAREHOUSE_GENERATION: WAREHOUSE_SOURCE_SCHEMA,
    OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION: (
        WAREHOUSE_SOURCE_SCHEMA_OFFICIAL_20260903
    ),
    HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION: (
        WAREHOUSE_SOURCE_SCHEMA_HOTFIX_20260903
    ),
    OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION: WAREHOUSE_SOURCE_SCHEMA_OFFICIAL_20260910,
    OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION: WAREHOUSE_SOURCE_SCHEMA_OFFICIAL_20261008,
}

_BATTLE_ITEMS: dict[int, str] | None = None
_ITEM_VALUES: dict[int, int] | None = None
_ITEM_NAMES: dict[int, str] | None = None


def _item_values() -> dict[int, int]:
    """藏品 item_id -> 单件价值(items.json 目录, 缓存)。结算帧不广播单件价, 故查目录补。"""
    global _ITEM_VALUES
    if _ITEM_VALUES is None:
        _ITEM_VALUES = {}
        try:
            from ..secure.data_crypt import load_json  # moat 透明解密(加密包)/ 明文直读(源码)
            p = Path(__file__).resolve().parents[3] / "data" / "processed" / "items.json"
            for x in load_json(p):
                iid = x.get("item_id"); v = x.get("value")
                if isinstance(iid, int) and isinstance(v, (int, float)):
                    _ITEM_VALUES[iid] = int(v)
        except Exception:  # noqa: BLE001
            _ITEM_VALUES = {}
    return _ITEM_VALUES


def _item_names() -> dict[int, str]:
    """藏品 item_id -> 名(items.json 目录, 缓存)。采样时目录若缺该新藏品则名为空, 用此按 id 补名。"""
    global _ITEM_NAMES
    if _ITEM_NAMES is None:
        _ITEM_NAMES = {}
        try:
            from ..secure.data_crypt import load_json  # moat 透明解密(加密包)/ 明文直读(源码)
            p = Path(__file__).resolve().parents[3] / "data" / "processed" / "items.json"
            for x in load_json(p):
                iid = x.get("item_id"); nm = x.get("name")
                if isinstance(iid, int) and nm:
                    _ITEM_NAMES[iid] = str(nm)
        except Exception:  # noqa: BLE001
            _ITEM_NAMES = {}
    return _ITEM_NAMES


def _battle_items() -> dict[int, str]:
    """付费道具 id -> 名(battle_items.json, 缓存)。玩家用的付费道具 action_id 即道具 id。"""
    global _BATTLE_ITEMS
    if _BATTLE_ITEMS is None:
        _BATTLE_ITEMS = {}
        try:
            from ..secure.data_crypt import load_json  # moat 透明解密(加密包)/ 明文直读(源码)
            p = Path(__file__).resolve().parents[3] / "data" / "processed" / "battle_items.json"
            for x in load_json(p):
                bid = x.get("battle_item_id")
                if isinstance(bid, int) and x.get("name"):
                    _BATTLE_ITEMS[bid] = str(x["name"])
        except Exception:  # noqa: BLE001
            _BATTLE_ITEMS = {}
    return _BATTLE_ITEMS

_ALLOWED = (
    "quote_application",
    "schema", "map_id", "hero", "round", "action_round",
    "final_quality_counts", "final_quality_cells", "final_quality_values",
    "inventory_count", "inventory_cells", "known_value_sum",
    "catalog_value_complete", "catalog_unknown_item_ids",
    "catalog_valued_item_count", "catalog_settled_item_count",
    "settlement_incomplete",
    "warehouse_source_schema",
    "warehouse_source_qualification", "warehouse_map_name",
    "warehouse_map_name_source", "warehouse_table_status",
    "warehouse_source_reason", "warehouse_source_generation",
    "warehouse_map_table_build", "warehouse_map_table_sha256",
    "warehouse_expected_map_table_sha256",
    "warehouse_map_source_contract_sha256",
    "warehouse_expected_map_source_contract_sha256",
    "warehouse_map_profile_generation", "warehouse_game_version",
    "warehouse_auction_mode", "warehouse_owner_evidence_status",
    "warehouse_source_conflict", "warehouse_quarantine_required",
    "official_distribution_eligible",
    # ❌ settlement_parse_residual 曾在此(0.2.7, commit 350bcb1), 当天被证伪并回退 ——
    #    它作为完整性判据是【100% 假阳性】(离线金标准 400 局报阳 10, 真欠计 0)。
    #    详见 sample_collector_server 里那段"已证伪·别再实现"的注释。
    #    该字段仍留在 artifact / 诊断包里(纯内部诊断, 供 0.2.8 重新设计判据用), 但【不上传】。
)
_WAREHOUSE_SOURCE_UPLOAD_FIELDS = frozenset(
    {
        "warehouse_source_schema",
        "warehouse_source_qualification",
        "warehouse_map_name",
        "warehouse_map_name_source",
        "warehouse_table_status",
        "warehouse_source_reason",
        "warehouse_source_generation",
        "warehouse_map_table_build",
        "warehouse_map_table_sha256",
        "warehouse_expected_map_table_sha256",
        "warehouse_map_source_contract_sha256",
        "warehouse_expected_map_source_contract_sha256",
        "warehouse_map_profile_generation",
        "warehouse_game_version",
        "warehouse_auction_mode",
        "warehouse_owner_evidence_status",
        "warehouse_source_conflict",
        "warehouse_quarantine_required",
        "official_distribution_eligible",
    }
)


def _settlement_category_upload_candidate(
    artifact: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Project only one complete, deidentified v1 category settlement fact."""

    value = artifact.get("settlement_category_fact_v1")
    physical_map_id = (
        value.get("physical_map_id") if type(value) is dict else None
    )
    model_map_id = value.get("model_map_id") if type(value) is dict else None
    artifact_physical_map_id = artifact.get("map_id")
    artifact_model_map_id = artifact.get("model_map_id")
    artifact_warehouse_generation = artifact.get("warehouse_source_generation")
    warehouse_source = artifact.get("warehouse_source")
    artifact_profile_generation = (
        warehouse_source.get("map_profile_generation")
        if isinstance(warehouse_source, Mapping)
        else None
    )
    fact_schema = value.get("schema") if type(value) is dict else None
    current_fact = fact_schema == "settlement_category_fact_v2"
    expected_generation = (
        artifact_profile_generation
        if current_fact
        and artifact_profile_generation
        in {
            OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
            HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
            OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION,
            OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION,
        }
        else POSTUPDATE_PLAYER_WAREHOUSE_GENERATION
    )
    if (
        artifact.get("phase") != "settled"
        or type(value) is not dict  # noqa: E721 - monitor artifact boundary
        or fact_schema
        not in {"settlement_category_fact_v1", "settlement_category_fact_v2"}
        or type(physical_map_id) is not int  # noqa: E721
        or physical_map_id <= 0
        or type(artifact_physical_map_id) is not int  # noqa: E721
        or artifact_physical_map_id <= 0
        or physical_map_id != artifact_physical_map_id
        or type(model_map_id) is not int  # noqa: E721
        or model_map_id <= 0
        or type(artifact_model_map_id) is not int  # noqa: E721
        or artifact_model_map_id <= 0
        or model_map_id != artifact_model_map_id
        or not _settlement_category_model_map_matches_physical(
            physical_map_id,
            model_map_id,
        )
        or type(value.get("warehouse_generation")) is not str  # noqa: E721
        or not value["warehouse_generation"]
        or type(artifact_warehouse_generation) is not str  # noqa: E721
        or value["warehouse_generation"] != artifact_warehouse_generation
        or value["warehouse_generation"] != expected_generation
        or artifact_warehouse_generation != expected_generation
        or (
            current_fact
            and artifact_profile_generation != expected_generation
        )
        or type(value.get("category_authority_generation")) is not str  # noqa: E721
        or not value["category_authority_generation"]
        or (
            current_fact
            and (
                type(value.get("category_authority_identity")) is not str  # noqa: E721
                or re.fullmatch(
                    r"[0-9a-f]{64}",
                    value["category_authority_identity"],
                    flags=re.ASCII,
                )
                is None
            )
        )
        or type(value.get("settled_items_v1")) is not list  # noqa: E721
        or not 1 <= len(value["settled_items_v1"]) <= 512
    ):
        return None
    items: list[dict[str, int]] = []
    for row in value["settled_items_v1"]:
        if (
            type(row) is not dict  # noqa: E721
            or set(row) != {"item_id", "quality", "primary_category"}
            or any(type(row.get(key)) is not int for key in row)  # noqa: E721
            or row["item_id"] <= 0
            or row["quality"] not in {1, 2, 3, 4, 5, 6}
            or (
                row["primary_category"] not in range(100, 112)
                if current_fact
                else row["primary_category"] <= 0
            )
        ):
            return None
        items.append({key: int(row[key]) for key in sorted(row)})
    projected = {
        "physical_map_id": physical_map_id,
        "model_map_id": model_map_id,
        "warehouse_generation": value["warehouse_generation"],
        "category_authority_generation": value["category_authority_generation"],
        "settled_items_v1": items,
    }
    if current_fact:
        projected["category_authority_identity"] = value[
            "category_authority_identity"
        ]
    return projected


def _catalog_item_count(artifact: Mapping[str, Any]) -> int | None:
    """结算里【目录表里有 且 占格>0】的件数 = 真藏品件数。

    为什么要单独数这一个(inventory_count 不够用)
    ------------------------------------------
    审计窗口 2026-07-12 在 raven 5608 的回放里发现: 结算解出的件里混进了【目录表里没有的件】
    —— 0 格 / 0 价 / id 形如 1888888、1444444、1266666、1088888(疑似排位代币或奖励)。
    它们【被算进了 inventory_count】。于是出现一个未决问题:

        游戏声明的 total_item_count 到底数不数这些垃圾件?

      · 若【不数】 -> 服务端的欠计判据(declared > inventory_count)有【假阴性】:
                     垃圾件把实抓件数垫高, 真丢了十几件真藏品也不报警。
      · 若【也数】 -> 判据没问题, 但引擎的 total_item_count 硬剪枝(joint.py)会被垫高的
                     件数污染 -> 拿虚高件数去找藏品 -> 【过估】。

    同一个未决事实决定问题落在哪一侧, 但【服务端只拿得到聚合字段, 判不出来】——
    发布安全窗口在 45,693 条云端样本上试过: 两个假说各被一半数据证伪(6 : 8 : 47),
    因为"0格无品质件"这个特征同时罩住了【真垃圾件】和【品质+格数都解析失败的真藏品】。
    ⇒ 决定性证据只有【逐件清单】, 而清单只在客户端。这个字段就是把答案传上来。

    传上去之后, 三个数一摆即可定案:
        declared == catalog_item_count            -> 声明【不数】垃圾件(审计假说成立)
        declared == inventory_count > catalog_...  -> 声明【也数】(引擎那侧要处理)

    ⚠️ 目录没加载出来时返回 None(【不传】), 绝不用 inventory_count 之类回退 ——
    那会造出一个假的"真藏品件数", 比没有更糟(§5 拒绝静默 coerce)。
    """
    mg = artifact.get("minimap_grid_items")
    if not isinstance(mg, (list, tuple)) or not mg:
        return None
    catalog = _item_values()          # item_id -> 单件价值(items.json 目录)
    if not catalog:                   # 目录读不到(加密包解密失败等) -> 不猜
        return None
    n = 0
    for it in mg:
        if not isinstance(it, Mapping):
            continue
        cells = it.get("cells")
        if it.get("item_id") in catalog and isinstance(cells, int) and cells > 0:
            n += 1
    return n


def _hash_session(sid: Any) -> str | None:
    if not sid:
        return None
    return hashlib.sha1(str(sid).encode("utf-8")).hexdigest()[:16]


def _extract_total_item_count(artifact: Mapping[str, Any]) -> tuple[Any, str | None]:
    """从 inference_input_constraints 取精确总件数。返回 (value, skip_reason)。
    只在 confidence=='exact' 且有 value 时返回 value;其余返回 (None, 原因)。

    刻意【不做估算回退】: 该字段语义=【精确】总件数(残缺判定/件数先验都依赖它确实精确),
    若缺失时灌入 inventory_count 等估算值,会造成假的"已知精确件数"→错误残缺判定(§5 拒绝静默coerce)。
    skip_reason 仅用于上传状态行诊断(见 _post),不进上传记录。
    ⚠️ 领域事实(作者):**精确件数基本只有 ahmed 有**(公开件数锁);除 ahmed 外没有英雄能在 R1 稳定拿到件数。
    所以判别"正常 vs 漂移"的【主判据=英雄】(状态行已带 hero=):
      - **非 ahmed 英雄出 no_field / confidence=非exact = 正常**(本就拿不到精确件数, 如 ethan 实测 constraints 只有 {mode});
      - **ahmed 局出 no_field 才该查**(公开锁应给件数却没拿到 = 漂移/丢失);
      - field_not_mapping / no_value = total_item_count 在但类型异常 → 任何英雄都属【结构漂移】, 该改提取。"""
    tic = artifact.get("inference_input_constraints")
    if not isinstance(tic, Mapping):
        return None, ("no_constraints" if tic is None else "constraints_not_mapping")
    t = tic.get("total_item_count")
    if t is None:
        return None, "no_field"
    if not isinstance(t, Mapping):
        return None, "field_not_mapping"
    conf = t.get("confidence")
    if conf != "exact":
        return None, f"confidence={conf}"
    v = t.get("value")
    if v is None:
        return None, "no_value"
    return v, None


def trim_artifact(artifact: Mapping[str, Any]) -> dict[str, Any] | None:
    """裁剪成可上传的最小结算样本;未完成对局(非 settled / 无品质数据)-> None(不上传)。

    数据质量: 只过滤【真正没法用】的——未完成的对局(非 settled, 或连 final_quality_counts
    都没有)。**结算异常(settlement_incomplete)/ 无价值的仍照常上传(带标记)**, 交服务器
    分流隔离到 samples_quarantine.jsonl 留存待查, 不在客户端丢弃——异常可能是误判/可挽救,
    丢了就没了。主分析库(samples.jsonl)由服务器只收正常样本, 保持干净。"""
    if artifact.get("phase") != "settled":
        return None
    if not artifact.get("final_quality_counts"):
        return None
    rec: dict[str, Any] = {k: artifact[k] for k in _ALLOWED if k in artifact}
    warehouse_source = artifact.get("warehouse_source")
    if isinstance(warehouse_source, Mapping):
        profile_generation = warehouse_source.get("map_profile_generation")
        if profile_generation not in _WAREHOUSE_SCHEMA_BY_PROFILE_GENERATION:
            profile_generation = warehouse_source.get("source_generation")
        rec["warehouse_source_schema"] = (
            _WAREHOUSE_SCHEMA_BY_PROFILE_GENERATION.get(
                profile_generation,
                WAREHOUSE_SOURCE_SCHEMA,
            )
        )
        rec["warehouse_source_qualification"] = warehouse_source.get("qualification")
        rec["warehouse_map_name"] = warehouse_source.get("map_name")
        rec["warehouse_map_name_source"] = warehouse_source.get("map_name_source")
        rec["warehouse_table_status"] = warehouse_source.get("table_status")
        rec["warehouse_source_reason"] = warehouse_source.get("reason")
        rec["warehouse_source_generation"] = warehouse_source.get(
            "source_generation"
        )
        rec["warehouse_map_table_build"] = warehouse_source.get("map_table_build")
        rec["warehouse_map_table_sha256"] = warehouse_source.get("map_table_sha256")
        rec["warehouse_expected_map_table_sha256"] = warehouse_source.get(
            "expected_map_table_sha256"
        )
        rec["warehouse_map_source_contract_sha256"] = warehouse_source.get(
            "map_source_contract_sha256"
        )
        rec["warehouse_expected_map_source_contract_sha256"] = (
            warehouse_source.get("expected_map_source_contract_sha256")
        )
        if (
            type(warehouse_source.get("map_profile_generation")) is str  # noqa: E721
            and warehouse_source["map_profile_generation"]
        ):
            rec["warehouse_map_profile_generation"] = warehouse_source[
                "map_profile_generation"
            ]
        if (
            type(warehouse_source.get("game_version")) is str  # noqa: E721
            and warehouse_source["game_version"]
        ):
            rec["warehouse_game_version"] = warehouse_source["game_version"]
            if (
                rec["warehouse_source_schema"]
                in _EXACT_DISK_WAREHOUSE_SCHEMAS
            ):
                rec["game_version"] = warehouse_source["game_version"]
        rec["warehouse_auction_mode"] = warehouse_source.get("auction_mode")
        rec["warehouse_owner_evidence_status"] = warehouse_source.get(
            "owner_evidence_status"
        )
        rec["warehouse_source_conflict"] = (
            warehouse_source.get("source_conflict") is True
        )
        rec["warehouse_quarantine_required"] = (
            warehouse_source.get("quarantine_required") is True
        )
        rec["official_distribution_eligible"] = (
            warehouse_source.get("official_distribution_eligible") is True
        )
    else:
        # Do not emit a partial PW2 projection.  A fieldless legacy record is
        # normalized by the server to unknown/nonofficial; any partial PW2
        # field set is quarantined and cannot be used to forge official data.
        for key in _WAREHOUSE_SOURCE_UPLOAD_FIELDS:
            rec.pop(key, None)
    rec["session_hash"] = _hash_session(artifact.get("session_id"))
    from bidking_lab.live.quote_preferences import validate_settlement_record, unknown
    rec["quote_application"] = validate_settlement_record(
        artifact.get("quote_application", unknown("missing")), session_hash=rec["session_hash"],
        settlement_cutoff=artifact.get("created_at"),
    ) if rec["session_hash"] else unknown("unavailable")
    # 精确总件数(用于残缺判定/件数先验),从约束里取;取不到的原因留给上传状态行诊断(见 _post / C2)
    tic_val, _ = _extract_total_item_count(artifact)
    if tic_val is not None:
        rec["total_item_count"] = tic_val
    # 真藏品件数(目录内 且 占格>0)。与 inventory_count 的差 = 混进来的非目录垃圾件。见 _catalog_item_count。
    cic = _catalog_item_count(artifact)
    if cic is not None:
        rec["catalog_item_count"] = cic
    # 结算时间(看板爆率时间轴; 服务器另会盖 received_ts 作权威时间)。
    ts = artifact.get("created_at")
    if isinstance(ts, (int, float)):
        rec["client_ts"] = ts
    # 玩家昵称(游戏内显示名, 非真实身份)——供作者看板按玩家分析。
    name = artifact.get("self_name")
    if name:
        rec["player"] = str(name)
    lb = artifact.get("latest_bids")
    if isinstance(lb, Mapping) and lb:
        rec["players"] = [str(k) for k in lb]                         # 本局各家昵称
        wkey = max(lb.items(), key=lambda kv: kv[1] if isinstance(kv[1], (int, float)) else -1)[0]
        rec["winner"] = str(wkey)
        wb = lb.get(wkey)                                             # 赢家出价(看板算玩家总收益:
        if isinstance(wb, (int, float)):                             #   赢家收益=拍品价值−赢家出价−门票)
            rec["winner_bid"] = int(wb)
    # 私人「我的记录」逐局字段。它们只供 uid-scope 页面消费；/public 必须继续彻底丢弃。
    # winner_is_self 只有在本机出价已被 player_id 可靠定位时才上传，避免 monitor 的
    # “无法识别本机玩家时 False”被服务端误读成“确定没拍下”。
    self_bid = artifact.get("self_bid")
    if isinstance(self_bid, (int, float)) and not isinstance(self_bid, bool):
        rec["self_bid"] = int(self_bid)
        winner_is_self = artifact.get("winner_is_self")
        if isinstance(winner_is_self, bool):
            rec["winner_is_self"] = winner_is_self
    welfare = artifact.get("settlement_welfare")
    if isinstance(welfare, (int, float)) and not isinstance(welfare, bool):
        rec["settlement_welfare"] = int(welfare)
    # 排位赛: 本机玩家排位分(看板按玩家看排位变化)。仅排位局有, 经典/活动为 None 不带。
    rs = artifact.get("rank_summary")
    if isinstance(rs, Mapping) and rs.get("self_rank_points") is not None:
        rec["rank_points"] = rs.get("self_rank_points")              # 赛前分
        if rs.get("self_rank_tier") is not None:
            rec["rank_tier"] = rs.get("self_rank_tier")              # 段位
        if rs.get("self_delta") is not None:
            rec["rank_delta"] = rs.get("self_delta")                # 本局涨跌
            rec["rank_after"] = rs.get("self_after")                # 赛后分
    # 各玩家排位分(昵称->赛前分/段位; 供看板显示非我方玩家排位)。只昵称+分数, 无 player_id/账号。
    pr = artifact.get("player_ranks")
    if isinstance(pr, Mapping) and pr:
        rec["player_ranks"] = {str(k): v for k, v in pr.items()}
    # 稀有藏品明细(红/金 物品名+价值): 供看板「具体红/金藏品出现次数」。物品名=游戏藏品名(公开非 PII)。
    mg = artifact.get("minimap_grid_items")
    if isinstance(mg, (list, tuple)):
        iv = _item_values()
        rare = []
        for it in mg:
            if not isinstance(it, Mapping):
                continue
            q = it.get("quality")
            if q not in (5, 6):
                continue
            # 名: 采样时若目录缺该藏品 item_name 为空 -> 按 id 查目录补名 -> 再不行给占位(仍上报, 不静默丢, 见 C2/E)。
            nm = it.get("item_name") or _item_names().get(it.get("item_id")) \
                or ("未知红藏品" if q == 6 else "未知金藏品")
            v = it.get("value")
            if not isinstance(v, (int, float)):               # 结算帧不带单件价 -> 查 items.json 目录
                v = iv.get(it.get("item_id"))
            rare.append({"n": str(nm), "q": int(q), "v": v})
        if rare:
            rec["rare_items"] = rare[:40]
    # 道具/技能使用(供看板分析): 游戏把两类分在不同通道, 这里也分两类——
    #  · 英雄技能揭示 = skill_reveal_rows(艾莎 R1-R4 轮廓等, 带 skill_id/hero_id)。
    #  · 玩家用的【付费道具】= action_result_rows(action_id 即 battle_item_id, 如极品扫描/随机抽检/
    #    宝光四鉴)。**付费道具不在 skill_reveal_rows**——之前读错通道, 付费道具段一直空(2026-06-24 修)。
    items: list[str] = []      # 付费道具
    hskills: list[str] = []    # 英雄技能揭示
    for r in artifact.get("skill_reveal_rows") or ():
        if not isinstance(r, Mapping):
            continue
        t = str(r.get("tool") or "").strip()
        if not t:
            continue
        if t.startswith("道具·"):                              # monitor 已判为付费道具(非本英雄技能)
            nm = t[3:]
            if nm and nm not in items:
                items.append(nm)
        else:
            nm = re.sub(r"^.*?R\d\s*", "", t) or t              # 去「英雄·Rn 」前缀
            if nm not in hskills:
                hskills.append(nm)
    bi = _battle_items()
    for r in artifact.get("action_result_rows") or ():          # 付费道具使用记录
        if not isinstance(r, Mapping):
            continue
        aid = r.get("action_id")
        nm = bi.get(aid) if isinstance(aid, int) else None
        if not nm:                                              # 不在道具表 -> 回退已解析名(monitor item_names)
            nm = str(r.get("tool") or "").strip() or None
        if nm and nm not in items:
            items.append(nm)
    if items:
        rec["tools"] = items[:24]
    if hskills:
        rec["hskills"] = hskills[:24]
    # 决策轮引擎证据桥(供【下拉后离线回放】: 报价/件数误差分析、旋钮 A/B)。上传的结算记录本来只有
    # 最终真值, 没有 bid 时的公开证据(avg_cells / fixed_counts / 总均格 / 揭示轮廓), 故无法回放引擎估计
    # ——补上这块后, `run_reference_engine(prepare_reference_engine_snapshot({map_id,hero,round,ahmad_ref_inputs}))`
    # 能逐字复现群友当局 live 估计(实测 5607/5601 与完整 snapshot 一致)。structured_ref_inputs = monitor 累积
    # 到最后 pre-settled 轮的紧凑桥(纯游戏数值证据, 无 PII), 典型 5.5KB。非 ref 引擎英雄为空则不带。
    # 体积保护: 超阈值(极深大仓)则去掉逐条 field_updates —— 汇总的 avg_cells/counts/... 已足够忠实回放
    # (实测 slim 版与完整版估计一致), 且远低于 collector 的 1MiB 请求上限。
    ref_inputs = artifact.get("structured_ref_inputs") or artifact.get("ahmad_ref_inputs")
    if isinstance(ref_inputs, Mapping) and ref_inputs:
        ri = dict(ref_inputs)
        try:
            if len(json.dumps(ri, ensure_ascii=False).encode("utf-8")) > 128_000:
                ri.pop("field_updates", None)
        except (TypeError, ValueError):
            ri.pop("field_updates", None)
        rec["ref_inputs"] = ri
    category_fact = _settlement_category_upload_candidate(artifact)
    if category_fact is not None:
        rec.update(category_fact)
    rec["client_version"] = CLIENT_VERSION
    return rec


def _uid_hash() -> str | None:
    """历史兼容 helper；正式上传链不再调用或发送此客户端派生值。

    可信上传身份只能由服务器签名 lease 派生并写入私有索引。保留 helper 只为了旧源码调用
    兼容与回归定位，不能重新接回 ``trim_artifact``、请求体或 header。
    """
    try:
        from ..secure.client_activate import resolve_token  # 懒导入(避免循环 + 无激活时零开销)
        tok = resolve_token()          # 纯读取无副作用(env -> 激活文件)
        if not tok:
            return None
        # token = "payload.sighex"; payload = "v1|uid|iat[|exp[|r]]"(见 activation_core.mint_token)
        payload = str(tok).rsplit(".", 1)[0]
        parts = payload.split("|")
        if len(parts) >= 2 and parts[0] == "v1" and parts[1]:
            return hashlib.sha256(parts[1].encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001 - 取不到身份绝不能挡住上传
        return None
    return None


def _write_upload_status(line: str) -> None:
    """把最近一次上传结果写到 data/logs/live/upload_status.txt(覆盖=最新一条)+ 追加滚动历史
    upload_status.jsonl(末 200 行)。供【收集诊断.bat】回传排障 —— 群友"样本没上传"时一眼看结果。
    全 best-effort: 写不了就算了, 绝不影响上传/对局。"""
    try:
        d = Path(__file__).resolve().parents[3] / "data" / "logs" / "live"
        d.mkdir(parents=True, exist_ok=True)
        (d / "upload_status.txt").write_text(line + "\n", encoding="utf-8")
        jl = d / "upload_status.jsonl"
        hist = jl.read_text(encoding="utf-8").splitlines() if jl.exists() else []
        hist.append(line)
        jl.write_text("\n".join(hist[-200:]) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _upload_result_summary(raw: bytes) -> str:
    """把 collector 的分流计数压成状态行；旧服务端/异常响应保持成功但明确标为未报告。"""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "result=unreported"
    if not isinstance(payload, Mapping):
        return "result=unreported"
    parts = []
    for key in ("accepted", "duplicate", "quarantined", "rejected"):
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            parts.append(f"{key}={value}")
    return " ".join(parts) if parts else "result=unreported"


_REFRESHABLE_UPLOAD_IDENTITY_FAILURES = frozenset(
    {"identity_required", "lease_expired"}
)
_UPLOAD_NONCE_RE = re.compile(r"^[0-9a-f]{32}$")


def _upload_signature_message(
    timestamp: str,
    body: bytes,
    *,
    upload_lease: str | None,
    nonce: str | None,
) -> bytes:
    """Return the exact legacy or lease-bound request-auth message."""
    if type(timestamp) is not str or not timestamp.isascii():  # noqa: E721
        raise ValueError("invalid upload timestamp")
    if type(body) is not bytes:  # noqa: E721
        raise ValueError("invalid upload body")
    if upload_lease is None and nonce is None:
        return timestamp.encode("ascii") + b"." + body
    if (
        type(upload_lease) is not str  # noqa: E721
        or type(nonce) is not str  # noqa: E721
        or _UPLOAD_NONCE_RE.fullmatch(nonce) is None
    ):
        raise ValueError("invalid upload identity signature input")
    try:
        lease_digest = hashlib.sha256(upload_lease.encode("ascii")).hexdigest()
    except UnicodeEncodeError as exc:
        raise ValueError("invalid upload identity signature input") from exc
    return (
        b"bidking/upload-request/v1\x00"
        + timestamp.encode("ascii")
        + b"\x00"
        + nonce.encode("ascii")
        + b"\x00"
        + lease_digest.encode("ascii")
        + b"\x00"
        + body
    )


def _same_upload_identity(left, right) -> bool:  # noqa: ANN001
    try:
        fields = ("token", "device", "revision", "clear_epoch")
        left_values = tuple(object.__getattribute__(left, field) for field in fields)
        right_values = tuple(object.__getattribute__(right, field) for field in fields)
    except BaseException:  # noqa: BLE001 - identity comparison is fail closed
        return False
    if not (
        type(left_values[0]) is type(right_values[0]) is str  # noqa: E721
        and type(left_values[1]) is type(right_values[1]) is str  # noqa: E721
        and type(left_values[2]) is type(right_values[2]) is int  # noqa: E721
        and type(left_values[3]) is type(right_values[3]) is int  # noqa: E721
    ):
        return False
    return bool(
        hmac.compare_digest(left_values[0], right_values[0])
        and hmac.compare_digest(left_values[1], right_values[1])
        and left_values[2:] == right_values[2:]
    )


def _current_upload_authority():
    try:
        from ..secure.client_activate import current_upload_authority_snapshot

        return current_upload_authority_snapshot()
    except Exception:  # noqa: BLE001 - compat clients may not expose this API
        return None


def _refresh_upload_lease_once(expected_identity) -> str | None:  # noqa: ANN001
    """Refresh once through the existing generation gate, then recheck identity."""
    try:
        from ..secure import client_activate, enforce

        before = client_activate.activation_identity_snapshot()
        if not _same_upload_identity(expected_identity, before):
            return None
        enforce.refresh_activation(timeout=4.0)
        refreshed = client_activate.current_upload_authority_snapshot()
        if (
            refreshed is None
            or not _same_upload_identity(expected_identity, refreshed.identity)
            or type(refreshed.lease) is not str  # noqa: E721
            or not refreshed.lease
        ):
            return None
        return refreshed.lease
    except Exception:  # noqa: BLE001 - upload refresh is best effort and no-throw
        return None


def _build_upload_request(
    url: str,
    key: str,
    body: bytes,
    *,
    upload_lease: str | None,
) -> urllib.request.Request:
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json; charset=utf-8")
    nonce = os.urandom(16).hex() if upload_lease is not None else None
    if upload_lease is not None:
        request.add_header("X-Upload-Lease", upload_lease)
        request.add_header("X-Upload-Nonce", nonce)
    if key:
        sig_ts = str(int(time.time()))
        message = _upload_signature_message(
            sig_ts,
            body,
            upload_lease=upload_lease,
            nonce=nonce,
        )
        signature = hmac.new(
            key.encode("utf-8"),
            message,
            hashlib.sha256,
        ).hexdigest()
        request.add_header("X-Sig-Ts", sig_ts)
        request.add_header("X-Sig", signature)
    return request


def _post(url: str, key: str, rec: dict[str, Any], tic_skip: str | None = None) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    # tic: Y=带 total_item_count(精确件数, 基本只 ahmed 公开锁有); N(原因)=没取到。
    # 主判据=英雄: 非ahmed出no_field/非exact=正常(R1拿不到稳定件数); ahmed出no_field才该查; 类型异常=漂移。见 C2。
    tic = "Y" if "total_item_count" in rec else (f"N({tic_skip})" if tic_skip else "N")
    meta = f"fields={len(rec)} tic={tic}"
    try:
        body = json.dumps(rec, ensure_ascii=False).encode("utf-8")
        authority = _current_upload_authority()
        upload_lease = authority.lease if authority is not None else None
        request = _build_upload_request(
            url,
            key,
            body,
            upload_lease=upload_lease,
        )
        if _activation_upload_denied():
            return
        try:
            resp = urlopen_verified(request, timeout=4)
        except urllib.error.HTTPError as first_error:
            failure = _upload_failure_class(first_error)
            refreshed_lease = None
            if (
                failure in _REFRESHABLE_UPLOAD_IDENTITY_FAILURES
                and authority is not None
            ):
                refreshed_lease = _refresh_upload_lease_once(authority.identity)
            if refreshed_lease is None:
                raise
            retry = _build_upload_request(
                url,
                key,
                body,
                upload_lease=refreshed_lease,
            )
            if _activation_upload_denied():
                return
            resp = urlopen_verified(retry, timeout=4)
            upload_lease = refreshed_lease
        code = resp.getcode()
        result = _upload_result_summary(resp.read())
        identity = "server_signed" if upload_lease is not None else "legacy_unverified"
        _write_upload_status(f"{ts} OK {code} {result} identity={identity} {meta}")
    except Exception as exc:  # noqa: BLE001  采样失败静默不影响对局, 但落状态供排障(不再"无人知")
        _write_upload_status(
            f"{ts} FAIL class={_upload_failure_class(exc)} {meta}"
        )


def _upload_failure_class(exc: BaseException) -> str:
    """Return one privacy-safe fixed category; never serialize exception text."""
    try:
        actual_mro = type.__getattribute__(type(exc), "__mro__")
        if type(actual_mro) is not tuple:
            return "unknown"
        if any(base is urllib.error.HTTPError for base in actual_mro):
            try:
                supplied = exc.headers.get("X-BidKing-Upload-Status")
            except BaseException:  # noqa: BLE001 - hostile header objects fail closed
                supplied = None
            if type(supplied) is str and supplied in {  # noqa: E721
                "identity_required",
                "lease_expired",
                "uid_revoked",
                "device_revoked",
                "identity_invalid",
                "identity_state_error",
                "request_replay",
                "request_in_flight",
            }:
                return supplied
            code = exc.code
            if type(code) is int and code in (401, 403):  # noqa: E721
                return "identity_rejected"
            if type(code) is int and code == 409:  # noqa: E721
                return "request_replay"
            if type(code) is int and code == 429:  # noqa: E721
                return "rate_limited"
            if type(code) is int and 500 <= code < 600:  # noqa: E721
                return "server_unavailable"
            return "http_error"
        if any(base is TimeoutError for base in actual_mro):
            return "timeout"
        if any(base is OSError for base in actual_mro):
            return "transport_error"
    except BaseException:  # noqa: BLE001
        return "unknown"
    return "unknown"


_LOCAL_CFG_CACHE: tuple[str | None, str] | None = None
_UPLOAD_AUTHORITY_LOCK = threading.RLock()
_UPLOAD_AUTHORITY_EPOCH = 0


def _activation_upload_denied() -> bool:
    """Fail closed when the activation authority cannot be read."""
    try:
        from ..secure import client_activate

        return client_activate.activation_runtime_denied() is True
    except Exception:  # noqa: BLE001
        return True


def _upload_authority_current(epoch: int) -> bool:
    with _UPLOAD_AUTHORITY_LOCK:
        return (
            type(epoch) is int
            and epoch == _UPLOAD_AUTHORITY_EPOCH
            and not _activation_upload_denied()
        )


def revoke_upload_authority() -> bool:
    """Invalidate decrypted config and all queued uploads without exposing it."""
    global _LOCAL_CFG_CACHE, _UPLOAD_AUTHORITY_EPOCH
    with _UPLOAD_AUTHORITY_LOCK:
        _UPLOAD_AUTHORITY_EPOCH += 1
        _LOCAL_CFG_CACHE = None
        return _LOCAL_CFG_CACHE is None


def _revalidate_exact_game_version_before_upload(
    rec: dict[str, Any],
) -> None:
    """Keep an exact-generation upload from borrowing a stale monitor cache."""

    expected = _EXACT_DISK_WAREHOUSE_SCHEMAS.get(
        rec.get("warehouse_source_schema")
    )
    if expected is None:
        return
    try:
        from .version_check import detect_game_version_strict_disk

        observed = detect_game_version_strict_disk()
    except Exception:  # noqa: BLE001 - upload metadata must fail closed
        observed = None
    fresh = observed if type(observed) is str else ""  # noqa: E721
    if (
        fresh == expected
        and rec.get("warehouse_game_version") == expected
        and rec.get("warehouse_map_profile_generation") == expected
        and rec.get("warehouse_source_generation") == expected
    ):
        rec["game_version"] = fresh
        return

    rec["game_version"] = fresh
    rec["warehouse_game_version"] = fresh
    rec["warehouse_source_generation"] = UNKNOWN_WAREHOUSE_GENERATION
    rec["warehouse_source_qualification"] = (
        POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
    )
    rec["warehouse_source_reason"] = (
        "warehouse_game_version_changed_before_upload"
        if fresh
        else "warehouse_game_version_unavailable_before_upload"
    )
    rec["warehouse_source_conflict"] = True
    rec["warehouse_quarantine_required"] = True
    rec["official_distribution_eligible"] = False
    for field_name in (
        "physical_map_id",
        "model_map_id",
        "warehouse_generation",
        "category_authority_generation",
        "category_authority_identity",
        "settled_items_v1",
    ):
        rec.pop(field_name, None)


def _post_if_authorized(
    authority_epoch: int,
    url: str,
    key: str,
    rec: dict[str, Any],
    tic_skip: str | None = None,
) -> None:
    if not _upload_authority_current(authority_epoch):
        return
    _revalidate_exact_game_version_before_upload(rec)
    _post(url, key, rec, tic_skip)


_UPLOAD_ENC_REL = ("data", "processed", "upload_config.enc")   # 群发版加密上传配置(相对仓库根)
_DISABLE_ENC_CFG = "BIDKING_DISABLE_ENCRYPTED_UPLOAD_CONFIG"    # 回退开关: =1/true 跳过加密路径走明文


def _read_encrypted_upload_config(repo: Path) -> tuple[str | None, str]:
    """读加密 upload_config.enc(BKENC1, AES-GCM) -> 解密 -> (url, key)。群发版形态。

    解密需激活后的出厂主密钥(activation.data_key(), 服务端激活下发, 不在包里)——这是 moat:
    包内只有密文, 提取包拿不到 key; 群友激活后运行时解密 -> HMAC 上传。
    无 enc 文件 / 未激活无 key / 解密失败 -> (None, "") 静默回退(不阻塞对局, 落状态供排障)。"""
    try:
        enc = repo.joinpath(*_UPLOAD_ENC_REL)
        if not enc.exists():
            return None, ""
        from ..secure.data_crypt import decrypt_bytes, is_encrypted
        if not is_encrypted(enc):
            _write_upload_status(f"{time.strftime('%Y-%m-%d %H:%M:%S')} CONFIG_ERR upload_config.enc 不是 BKENC1 格式")
            return None, ""
        from ..secure import activation
        try:
            mkey = activation.data_key()          # 激活后才装好 provider; 未激活 -> DataKeyUnavailable
        except Exception as exc:  # noqa: BLE001
            _write_upload_status(f"{time.strftime('%Y-%m-%d %H:%M:%S')} CONFIG_INFO upload_config.enc 在但无解密 key(未激活/无 master_key): {type(exc).__name__}")
            return None, ""
        plain = decrypt_bytes(
            enc.read_bytes(),
            mkey,
            member_path="/".join(_UPLOAD_ENC_REL),
        )   # 篡改/错 key/错路径 -> InvalidTag 异常(被外层接住)
        cfg = json.loads(plain.decode("utf-8"))
        url = str(cfg.get("url") or "")
        key = str(cfg.get("key") or "")
        if not url:
            _write_upload_status(f"{time.strftime('%Y-%m-%d %H:%M:%S')} CONFIG_ERR upload_config.enc 解密成功但无 url")
            return None, ""
        return url, key
    except Exception as exc:  # noqa: BLE001  解密/解析失败静默回退, 不阻塞对局
        _write_upload_status(f"{time.strftime('%Y-%m-%d %H:%M:%S')} CONFIG_ERR 读 upload_config.enc 异常: {type(exc).__name__}")
        return None, ""


def _read_plaintext_upload_config(repo: Path) -> tuple[str | None, str]:
    """明文回退(dev/作者包): scripts/sample_upload.local.ps1 -> (url, key)。

    无文件 / 解析失败 / URL 缺 -> (None, "") 静默(不阻塞对局, 落状态供排障)。
    抽成独立函数便于单测注入(避免本机真实 .local.ps1 干扰测试)。"""
    try:
        p = repo / "scripts" / "sample_upload.local.ps1"
        if not p.exists():
            return None, ""
        txt = p.read_text(encoding="utf-8", errors="ignore")
        mu = re.search(r'BIDKING_SAMPLE_UPLOAD_URL\s*=\s*"([^"]+)"', txt)
        mk = re.search(r'BIDKING_SAMPLE_UPLOAD_KEY\s*=\s*"([^"]+)"', txt)
        url = mu.group(1) if mu else ""
        key = mk.group(1) if mk else ""
        if not url:   # 文件在但 URL 没解析出来 = 配了却没生效, 落状态供排障(否则"明明配了却不上传"无从查)
            _write_upload_status(f"{time.strftime('%Y-%m-%d %H:%M:%S')} CONFIG_ERR .local.ps1 存在但未解析出 BIDKING_SAMPLE_UPLOAD_URL")
            return None, ""
        return url, key
    except Exception as exc:  # noqa: BLE001  读配置失败静默,不影响对局, 但落状态供排障
        _write_upload_status(f"{time.strftime('%Y-%m-%d %H:%M:%S')} CONFIG_ERR 读 .local.ps1 异常: {type(exc).__name__}")
        return None, ""


def _read_local_upload_config() -> tuple[str | None, str]:
    """回退: env 没传时的上传配置来源(返回 (url, key))。env 已在 maybe_upload_settlement 先查。

    优先级(本函数内): 加密 upload_config.enc(群发版, 激活后 activation.data_key() 解密)
    → 明文 scripts/sample_upload.local.ps1(dev/作者包回退) → 无。两条路径按包形态互斥:
    群发版包内只有 enc(无明文 .local.ps1); 作者源码/作者包无 enc → 走明文。

    为什么需要明文回退: 命令行【自动提权(UAC)】启动时, PowerShell 启动器把 `.local.ps1` 的 env 传给
    提权出的 monitor 子进程这条链路【不可靠】(实测自动提权时 env 没到 monitor)。用 `__file__` 绝对路径
    定位配置(不受工作目录/提权重置影响), env 没传时直接读文件 → 不管怎么启动都能上传。
    配置语义:正式公版按产品契约内置 enc 并默认上传；作者包用 .local.ps1；源码/自建包两者都没有则不上传。

    缓存策略: 仅在拿到 url(成功)时缓存; url 为 None 不缓存 → 激活若晚于首次调用(未激活→无 key→None),
    下次结算会重试(结算不频繁, 重读开销可忽略)。回退开关 BIDKING_DISABLE_ENCRYPTED_UPLOAD_CONFIG=1
    → 跳过加密路径直走明文(应急/调试)。"""
    global _LOCAL_CFG_CACHE
    with _UPLOAD_AUTHORITY_LOCK:
        if _activation_upload_denied():
            _LOCAL_CFG_CACHE = None
            return None, ""
        if _LOCAL_CFG_CACHE is None:
            url: str | None = None
            key = ""
            repo = Path(__file__).resolve().parents[3]

            # 1) 加密配置(群发版形态): data/processed/upload_config.enc
            if os.environ.get(_DISABLE_ENC_CFG, "").strip().lower() in ("1", "true", "yes", "on"):
                _write_upload_status(f"{time.strftime('%Y-%m-%d %H:%M:%S')} CONFIG_INFO 加密上传配置被 {_DISABLE_ENC_CFG} 关闭, 走明文回退")
            else:
                url, key = _read_encrypted_upload_config(repo)

            # 2) 明文回退(dev/作者包): scripts/sample_upload.local.ps1
            if not url:
                url, key = _read_plaintext_upload_config(repo)

            # 仅成功时缓存; None 不缓存以允许激活后重试
            if url:
                _LOCAL_CFG_CACHE = (url, key)
        return _LOCAL_CFG_CACHE if _LOCAL_CFG_CACHE is not None else (None, "")


def maybe_upload_settlement(artifact: Mapping[str, Any]) -> bool:
    """monitor 结算后调用。配了上传地址才上传;后台线程 fire-and-forget。返回是否已派发(便于测试)。

    地址来源优先级:env `BIDKING_SAMPLE_UPLOAD_URL` → 回退读 `.local.ps1`(治自动提权 env 丢失,
    见 _read_local_upload_config)。两者都没有 = 源码/自建环境未配置 = 不上传。正式公版会内置加密配置。"""
    with _UPLOAD_AUTHORITY_LOCK:
        if _activation_upload_denied():
            revoke_upload_authority()
            return False
        authority_epoch = _UPLOAD_AUTHORITY_EPOCH
    url = os.environ.get("BIDKING_SAMPLE_UPLOAD_URL")
    key = os.environ.get("BIDKING_SAMPLE_UPLOAD_KEY", "")
    if not url:
        url, key = _read_local_upload_config()  # env 没传(如 UAC 自动提权)→ 读本地配置回退
        if not url:
            return False
    try:
        from .version_check import validate_service_url
        url = validate_service_url(url, purpose="sample upload")
    except (TypeError, ValueError) as exc:
        # 不把 URL/key 写入状态；正式包若被错误配置为外部 HTTP，必须停止上传而不是静默降级。
        _write_upload_status(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} CONFIG_ERR 上传地址被拒绝: {type(exc).__name__}"
        )
        return False
    rec = trim_artifact(artifact)
    if rec is None:
        return False
    try:  # 带上产品版本(+ 探测到的游戏版本): 服务器按版本统计, 版本发行/会员地基。best-effort。
        from . import version_check
        rec["app_version"] = version_check.app_version()
        gv = (
            rec.get("warehouse_game_version")
            if rec.get("warehouse_source_schema")
            in _EXACT_DISK_WAREHOUSE_SCHEMAS
            else version_check.detect_game_version()
        )
        if gv:
            rec["game_version"] = gv
    except Exception:  # noqa: BLE001  版本信息可有可无, 绝不影响上传
        pass
    _revalidate_exact_game_version_before_upload(rec)
    _, tic_skip = _extract_total_item_count(artifact)   # 没取到精确件数的原因 -> 上传状态行(诊断结构漂移)
    if not _upload_authority_current(authority_epoch):
        return False
    threading.Thread(
        target=_post_if_authorized,
        args=(authority_epoch, url, key, rec, tic_skip),
        daemon=True,
    ).start()
    return True
