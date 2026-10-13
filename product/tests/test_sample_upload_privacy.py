# -*- coding: utf-8 -*-
"""Privacy contract for the opt-in sample uploader (src/bidking_lab/live/sample_upload.py).

Locks the guarantees that make auto-collection safe to ship: opt-in by default (no upload without an
explicitly-configured URL), session anonymization (hash, never the raw id), a strict field whitelist
(no PII / player identity / raw packets leak), and that only real settlement truth is ever uploaded.
Reviewed-in 2026-06-23 (side-window data-acquisition feature had no test coverage)."""
from __future__ import annotations

import pytest

from bidking_lab.live import sample_upload as su


@pytest.fixture(autouse=True)
def _isolated_uploader_paths(tmp_path, monkeypatch):
    """Keep status/config side effects in the test's temporary application tree."""
    module_path = tmp_path / "app" / "src" / "bidking_lab" / "live" / "sample_upload.py"
    monkeypatch.setattr(su, "__file__", str(module_path))


def _settled_artifact() -> dict:
    return {
        "phase": "settled",
        "session_id": "2410:9000000000000012",
        "map_id": 2410,
        "hero": "gabriela",
        "final_quality_counts": {"q5": 2},
        "final_quality_cells": {"q5": 19},
        "final_quality_values": {"q5": 100000},
        "inventory_count": 24,
        "inventory_cells": 85,
        "known_value_sum": 194604,
        "settlement_incomplete": False,
        # 排位赛 rank_summary: 只有本机 self_* 该上传; changes 整列表(本机的两套分数体系)不外传。
        "rank_summary": {
            "self_rank_points": 1200.0, "self_rank_tier": 5,
            "self_before": 1200.0, "self_delta": -24.0, "self_after": 1176.0,
            "changes": [
                {"before": 1200.0, "delta": -24.0, "after": 1176.0},
                {"before": 1200.0, "delta": 30.0, "after": 1230.0},    # slot7 本机另一分数, 整 changes 列表不上传
            ],
        },
        # 各玩家排位分(昵称->赛前分/段位/player_id): 作者授权上传供看板显示对手排位 + 日后按 id 追踪改名玩家。
        "player_ranks": {"secret-handle": {"p": 1200.0, "t": 5, "id": 900000000000123},
                         "对手昵称": {"p": 1300.0, "t": 6, "id": 222}},
        # identity / raw fields that MUST NEVER be uploaded:
        "local_player_id": 900000000000123,
        "player_name": "secret-handle",
        "ip": "1.2.3.4",
        "raw_packets": ["deadbeef"],
    }


def test_optin_default_no_url_means_no_upload(monkeypatch) -> None:
    # opt-in: 既无 env 又无本地配置文件 -> 不上传。(本机 .local.ps1 可能存在, 故显式置空配置回退。)
    monkeypatch.delenv("BIDKING_SAMPLE_UPLOAD_URL", raising=False)
    monkeypatch.setattr(su, "_read_local_upload_config", lambda: (None, ""))
    assert su.maybe_upload_settlement(_settled_artifact()) is False


def test_local_config_fallback_uploads_when_env_missing(monkeypatch) -> None:
    """env 没传(如命令行 UAC 自动提权丢 env)但本地配置存在 -> 仍上传, 用配置里的 URL/KEY。

    回归: 别把 maybe_upload_settlement 改回只认 env(否则自动提权启动时静默不上传)。"""
    monkeypatch.delenv("BIDKING_SAMPLE_UPLOAD_URL", raising=False)
    monkeypatch.delenv("BIDKING_SAMPLE_UPLOAD_KEY", raising=False)
    monkeypatch.setattr(su, "_read_local_upload_config", lambda: ("https://srv.example/ingest", "k7"))
    posted: dict = {}
    monkeypatch.setattr(su, "_post", lambda url, key, rec, tic_skip=None: posted.update(url=url, key=key, rec=rec, tic_skip=tic_skip))
    # 同步执行后台线程, 便于断言
    monkeypatch.setattr(su.threading, "Thread",
                        lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    assert su.maybe_upload_settlement(_settled_artifact()) is True
    assert posted["url"] == "https://srv.example/ingest" and posted["key"] == "k7"
    assert posted["rec"]["map_id"] == 2410  # 真发的是裁剪后的样本


def test_external_plain_http_upload_is_rejected_before_dispatch(monkeypatch) -> None:
    monkeypatch.setenv(
        "BIDKING_SAMPLE_UPLOAD_URL",
        "http://203.0.113.10:18900/ingest",
    )
    dispatched = {"value": False}
    monkeypatch.setattr(
        su.threading,
        "Thread",
        lambda *a, **k: dispatched.update(value=True),
    )
    assert su.maybe_upload_settlement(_settled_artifact()) is False
    assert dispatched["value"] is False


def test_trim_anonymizes_session_and_drops_pii() -> None:
    rec = su.trim_artifact(_settled_artifact())
    assert rec is not None
    # raw session id never sent; only a non-reversible-by-eye hash for dedup
    assert "session_id" not in rec
    assert rec.get("session_hash") and rec["session_hash"] != "2410:9000000000000012"
    # no identity / raw fields leak through
    for pii in ("local_player_id", "player_name", "ip", "raw_packets"):
        assert pii not in rec
    # ONLY whitelisted (+ derived) keys survive — a new artifact field can't silently start uploading.
    # Derived keys intentionally added by trim_artifact: session_hash/total_item_count/client_ts (dedup
    # + 爆率时间轴), player/players/winner (游戏内昵称, 作者确认非真实身份, 供看板按玩家分析),
    # tools/hskills (付费道具 + 英雄技能, 供道具看板), client_version.
    allowed = set(su._ALLOWED) | {
        "session_hash", "total_item_count", "client_ts", "client_version",
        "player", "players", "winner", "winner_bid", "self_bid", "winner_is_self",
        "settlement_welfare", "tools", "hskills", "rare_items",
        "ref_inputs",  # pre-settlement gameplay evidence bridge; no raw packets/player identity
        "rank_points", "rank_tier", "rank_delta", "rank_after",   # 排位: 本机 self_* 标量
        "player_ranks",   # 各玩家昵称->赛前分/段位(作者授权, 供看板显示对手排位)
    }
    assert set(rec) <= allowed, f"unexpected keys uploaded: {set(rec) - allowed}"
    assert "uid_hash" not in rec
    # 排位: 本机 self_* 标量该带; rank_summary 整对象 + changes 列表(含 slot7 本机另一分数)不外传。
    assert rec.get("rank_after") == 1176.0 and rec.get("rank_tier") == 5 and rec.get("rank_delta") == -24.0
    assert "rank_summary" not in rec and "changes" not in rec and "rank_changes" not in rec
    # player_ranks(作者授权): 上传【昵称->赛前分/段位/player_id】供看板显示对手排位 + 日后按 id 追踪改名玩家。
    assert rec["player_ranks"] == {"secret-handle": {"p": 1200.0, "t": 5, "id": 900000000000123},
                                   "对手昵称": {"p": 1300.0, "t": 6, "id": 222}}
    # 即便 player_ranks 带 id, raw 顶层 PII 字段(原始包/IP/独立 player_name 键)仍绝不作为顶层键外传(白名单守住)。
    assert "ip" not in rec and "raw_packets" not in rec and "player_name" not in rec
    # gameplay stats (the whole point) are preserved
    assert rec["map_id"] == 2410 and rec["inventory_count"] == 24 and rec["hero"] == "gabriela"


def test_warehouse_source_contract_survives_upload_trim() -> None:
    artifact = _settled_artifact() | {
        "warehouse_source": {
            "qualification": "player_custom_warehouse",
            "reason": "localized_name_unknown_prefix",
            "map_name": "未知别墅",
            "map_name_source": "bid_map_table",
            "table_status": "verified",
            "source_generation": "cd4af1-2026-08-13-606",
            "map_table_build": "24704978",
            "map_table_sha256": "actual-name-table-sha",
            "expected_map_table_sha256": "expected-name-table-sha",
            "official_distribution_eligible": False,
        }
    }

    rec = su.trim_artifact(artifact)

    assert rec is not None
    assert rec["warehouse_source_qualification"] == "player_custom_warehouse"
    assert rec["warehouse_map_name"] == "未知别墅"
    assert rec["warehouse_source_generation"] == (
        "cd4af1-2026-08-13-606"
    )
    assert rec["warehouse_map_table_build"] == "24704978"
    assert rec["warehouse_map_table_sha256"] == "actual-name-table-sha"
    assert rec["warehouse_expected_map_table_sha256"] == (
        "expected-name-table-sha"
    )
    assert rec["official_distribution_eligible"] is False


def test_missing_current_warehouse_source_fails_closed_in_upload_record() -> None:
    rec = su.trim_artifact(_settled_artifact())

    assert rec is not None
    assert not any(
        key.startswith("warehouse_")
        or key == "official_distribution_eligible"
        for key in rec
    )


def test_raven_and_gabriela_structured_ref_inputs_reach_upload_record() -> None:
    for hero in ("raven", "gabriela"):
        art = _settled_artifact() | {
            "hero": hero,
            "structured_ref_inputs": {
                "hero": hero,
                "total_count": 27,
                "counts": {"q6": 2},
                "minimap_items": [{"quality": 6, "cells": 2, "row": 3, "col": 4}],
            },
        }

        rec = su.trim_artifact(art)

        assert rec is not None
        assert rec["ref_inputs"]["hero"] == hero
        assert rec["ref_inputs"]["total_count"] == 27
        assert rec["ref_inputs"]["counts"] == {"q6": 2}
        assert rec["ref_inputs"]["minimap_items"][0]["cells"] == 2


def test_paid_items_from_action_results_not_skill_reveals() -> None:
    """付费道具(极品扫描/随机抽检/宝光四鉴…)记在 action_result_rows(action_id=道具id), 不在
    skill_reveal_rows——回归: 之前读错通道导致 tools 段恒空(2026-06-24 修)。"""
    art = _settled_artifact() | {
        "hero": "aisha",
        "action_result_rows": [
            {"action_id": 100107, "tool": "极品扫描"},       # 付费道具
            {"action_id": 100136, "tool": "宝光四鉴"},       # 付费道具
        ],
        "skill_reveal_rows": [
            {"skill_id": 1001034, "hero_id": 103, "tool": "艾莎·R1 白品轮廓"},  # 英雄技能
        ],
    }
    rec = su.trim_artifact(art)
    assert rec is not None
    assert rec.get("tools") == ["极品扫描", "宝光四鉴"]      # 付费道具来自 action_result_rows
    assert rec.get("hskills") == ["白品轮廓"]               # 英雄技能来自 skill_reveal_rows(去 Rn 前缀)


def test_only_settlement_truth_is_uploaded() -> None:
    assert su.trim_artifact({"phase": "bidding", "final_quality_counts": {"q5": 1}}) is None
    assert su.trim_artifact({"phase": "settled"}) is None  # settled but no final quality truth


def test_total_item_count_extraction_and_skip_reason() -> None:
    """C2: 精确总件数仅在 confidence=exact 提取(不估算回退, 避免假"精确件数"→错误残缺判定);
    取不到时给可诊断原因, 区分【结构漂移】(constraints 在却取不到=该改提取逻辑)vs 本就无精确件数。"""
    ex = su._extract_total_item_count
    assert ex({"inference_input_constraints": {"total_item_count": {"value": 13, "confidence": "exact"}}}) == (13, None)
    assert ex({"inference_input_constraints": {"total_item_count": {"value": 13, "confidence": "approx"}}}) == (None, "confidence=approx")
    assert ex({"inference_input_constraints": {"other": 1}}) == (None, "no_field")              # 漂移信号
    assert ex({"inference_input_constraints": {"total_item_count": 13}}) == (None, "field_not_mapping")  # 漂移信号
    assert ex({"final_quality_counts": {"q5": 1}}) == (None, "no_constraints")                   # 正常缺失
    # trim 不灌估算: 非 exact 时 rec 不带该字段(否则假精确件数会污染残缺判定)
    art = {"phase": "settled", "final_quality_counts": {"q5": 2}, "map_id": 2402,
           "inference_input_constraints": {"total_item_count": {"value": 9, "confidence": "approx"}}}
    assert "total_item_count" not in su.trim_artifact(art)


# --------------------------------------------------------------------------- #
# catalog_item_count(0.2.7): 真藏品件数 = 目录内 且 占格>0 的件。
# inventory_count − catalog_item_count = 混进结算的【非目录垃圾件】(0格/0价, 疑似排位代币)。
# 它存在的唯一目的: 定案"游戏声明的 total_item_count 到底数不数垃圾件" —— 服务端只有聚合字段,
# 判不出来(实测 45,693 条: 两个假说各被一半数据证伪), 只有逐件清单能定案。
# --------------------------------------------------------------------------- #
def _artifact_with_items(items: list[dict]) -> dict:
    art = _settled_artifact()
    art["minimap_grid_items"] = items
    art["inventory_count"] = len(items)
    return art


def test_catalog_item_count_excludes_noncatalog_junk(monkeypatch) -> None:
    """垃圾件(不在目录 / 0 格)不计入; 真藏品计入。这个差值就是要测的东西。"""
    monkeypatch.setattr(su, "_item_values", lambda: {101: 5000, 102: 30000, 103: 900})
    art = _artifact_with_items([
        {"item_id": 101, "cells": 4, "quality": 5},      # 真藏品
        {"item_id": 102, "cells": 9, "quality": 6},      # 真藏品
        {"item_id": 103, "cells": 1, "quality": 1},      # 真藏品
        {"item_id": 1888888, "cells": 0, "quality": None},   # 垃圾: 不在目录 + 0 格
        {"item_id": 1444444, "cells": 0, "quality": None},   # 垃圾
        {"item_id": 104, "cells": 3, "quality": 4},          # 不在目录但【有格】-> 也不计(目录才是真源)
    ])
    rec = su.trim_artifact(art)
    assert rec["catalog_item_count"] == 3
    assert rec["inventory_count"] == 6
    # 差值 = 3 = 混进来的非藏品件。判据要用的正是这个差。
    assert rec["inventory_count"] - rec["catalog_item_count"] == 3


def test_catalog_item_count_absent_when_catalog_unavailable(monkeypatch) -> None:
    """⚠️目录读不到(加密包解密失败等)-> 【不传这个字段】, 绝不用 inventory_count 之类回退。

    回退会造出一个假的"真藏品件数" —— 而这个字段【唯一的用途】就是当判据的分母。
    喂它一个猜出来的值, 比没有它更糟(会让人以为问题已经定案)。
    """
    monkeypatch.setattr(su, "_item_values", dict)          # 目录空
    art = _artifact_with_items([{"item_id": 101, "cells": 4, "quality": 5}])
    assert "catalog_item_count" not in su.trim_artifact(art)

    # 连逐件清单都没有(老快照 / 解析失败) -> 同样不传
    art2 = _settled_artifact()
    assert "catalog_item_count" not in su.trim_artifact(art2)


def test_catalog_item_count_survives_the_collector_whitelist() -> None:
    """🔴 两端契约: collector 有【入站白名单】, 不在表里的字段会被【静默丢掉】。

    只改客户端 = 传上去了、但一条都没落盘, 而且没有任何报错 —— 几周后才会发现"数据怎么是空的"。
    这道门把两端锁在一起。
    """
    import sys
    from pathlib import Path
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import sample_collector_server as srv  # noqa: PLC0415

    assert "catalog_item_count" in srv._ALLOWED, (
        "客户端会传 catalog_item_count, 但 collector 的入站白名单里没有它 -> 会被静默丢弃")
    assert "ref_inputs" in srv._ALLOWED, (
        "客户端会传 structured ref bridge, 但 collector 未保留 ref_inputs -> 云端回放证据会静默丢失"
    )
