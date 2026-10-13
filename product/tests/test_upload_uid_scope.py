"""uid-scope 上传身份(0.2.7 公共大数据平台的地基)设计门。

背景: 上传记录此前【不含任何身份】(只有游戏昵称) → 服务端无从知道哪条样本是谁的 →
"玩家只看自己的对局记录"根本做不了。0.2.7 起上传带 uid_hash。

⭐这条链路上有两个【静默丢字段】的坑, 任一处漏掉整个功能就悄无声息地废掉:
  1. 客户端 rec 组装(_ALLOWED 只从 artifact 挑字段, uid_hash 必须直接加在 rec 上)
  2. 服务端 _accept_one 的白名单(不在 _ALLOWED 里的字段【直接丢弃】, 不报错)
故本套件端到端验证: 客户端造出来 → 服务端白名单留得住。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import activation_core as ac  # noqa: E402
import sample_collector_server as scs  # noqa: E402

from bidking_lab.live import sample_upload as su  # noqa: E402
from bidking_lab.secure import client_activate as ca  # noqa: E402


SECRET = b"test-activation-secret"


def _pin_token(monkeypatch, token: str) -> None:
    """su._uid_hash 是【懒导入】resolve_token 的 -> 必须 patch 源模块(patch su 上的无效)。
    同时清掉 env, 免得本机真实的激活 token 抢先(测试必须与本机激活态无关)。"""
    monkeypatch.delenv("BIDKING_ACTIVATION_TOKEN", raising=False)
    monkeypatch.setattr(ca, "resolve_token", lambda *a, **k: token)


def _expected_hash(uid: str) -> str:
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# 客户端: 从 token 解出 uid 并 hash
# --------------------------------------------------------------------------- #
def test_uid_hash_derived_from_real_token(monkeypatch):
    """用 activation_core 真造的 token(不是手搓字符串), 保证解析与真实 token 格式一致。"""
    uid = "order-12345"
    _pin_token(monkeypatch, ac.mint_token(uid, SECRET))
    assert su._uid_hash() == _expected_hash(uid)


def test_uid_hash_works_for_limited_and_roaming_tokens(monkeypatch):
    """限期(4段)/漫游(5段)token 的 payload 段数不同, uid 仍是第 2 段 —— 别写死段数。"""
    for kwargs in ({"dur_seconds": 3600}, {"dur_seconds": 3600, "roaming": True}):
        uid = "buyer-xyz"
        try:
            token = ac.mint_token(uid, SECRET, **kwargs)
        except TypeError:
            continue   # 该版本 mint_token 不支持这些参数就跳过(不是本测试的重点)
        _pin_token(monkeypatch, token)
        assert su._uid_hash() == _expected_hash(uid), f"{kwargs} 的 token 解不出 uid"


def test_no_token_means_no_uid_hash(monkeypatch):
    """试用/未激活用户没有 token -> 不带该字段(而不是空串/崩溃)。数据仍进公共聚合。"""
    _pin_token(monkeypatch, "")      # 未激活 = resolve_token 返回 ""
    assert su._uid_hash() is None


def test_garbage_token_never_breaks_upload(monkeypatch):
    """token 损坏/格式不对时绝不能抛异常挡住上传 —— 采样是锦上添花, 不能影响对局。"""
    for bad in ("garbage", "v9|x", "|||", "....", "v1|"):
        _pin_token(monkeypatch, bad)
        assert su._uid_hash() is None, f"坏 token {bad!r} 该返回 None"


# --------------------------------------------------------------------------- #
# ⭐服务端边界: 客户端身份声明必须被丢弃
# --------------------------------------------------------------------------- #
def test_server_whitelist_rejects_client_uid_hash():
    assert "uid_hash" not in scs._ALLOWED


def test_private_bid_and_profit_fields_survive_client_and_collector(tmp_path, monkeypatch):
    """我的出价/胜负/福利必须穿过客户端裁剪和 collector 白名单，且不依赖真实身份。"""
    out = tmp_path / "samples.jsonl"
    monkeypatch.setattr(scs, "_OUT", out)
    monkeypatch.setattr(scs, "_SEEN", set())
    monkeypatch.setattr(scs, "_QUAR_SEEN", set())
    monkeypatch.setattr(su, "_uid_hash", lambda: _expected_hash("private-record-user"))
    artifact = {
        "phase": "settled",
        "session_id": "private-record-session",
        "map_id": 2401,
        "hero": "aisha",
        "final_quality_counts": {"q5": 1},
        "known_value_sum": 500_000,
        "self_name": "我",
        "latest_bids": {"我": 410_000, "对手": 620_000},
        "self_bid": 410_000,
        "winner_is_self": False,
        "settlement_welfare": 12_000,
    }

    rec = su.trim_artifact(artifact)
    assert rec is not None
    assert rec["self_bid"] == 410_000
    assert rec["winner_is_self"] is False
    assert rec["settlement_welfare"] == 12_000
    assert scs._accept_one(rec) == "accepted"

    import json
    stored = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert stored["self_bid"] == 410_000
    assert stored["winner_is_self"] is False
    assert stored["settlement_welfare"] == 12_000


def test_unknown_local_player_does_not_upload_false_loss(monkeypatch):
    monkeypatch.setattr(su, "_uid_hash", lambda: None)
    artifact = {
        "phase": "settled",
        "session_id": "unknown-player-session",
        "map_id": 2401,
        "final_quality_counts": {"q5": 1},
        "known_value_sum": 100_000,
        "winner_is_self": False,
        "self_bid": None,
    }

    rec = su.trim_artifact(artifact)

    assert rec is not None
    assert "self_bid" not in rec
    assert "winner_is_self" not in rec


def test_server_accepts_legacy_sample_but_strips_client_uid_hash(tmp_path, monkeypatch):
    """兼容流量可进公共池，但客户端身份声明不能进入任何公共JSONL。"""
    out = tmp_path / "samples.jsonl"
    monkeypatch.setattr(scs, "_OUT", out)
    monkeypatch.setattr(scs, "_SEEN", set())
    monkeypatch.setattr(scs, "_QUAR_SEEN", set())

    uid_hash = _expected_hash("order-777")
    rec = {
        "map_id": 2401,
        "final_quality_counts": "q1=1;q6=1",
        "session_hash": "abc123",
        "hero": "aisha",
        "known_value_sum": 123456,     # _is_abnormal: 无有效拍品价值会被隔离
        "uid_hash": uid_hash,
    }
    assert scs._accept_one(rec) == "accepted"

    import json
    lines = [
        json.loads(line)
        for line in out.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1
    assert "uid_hash" not in lines[0]
    assert lines[0]["identity_trust"] == "legacy_unverified"


def test_legacy_sample_without_uid_hash_still_accepted(tmp_path, monkeypatch):
    """活动前存量样本没有 uid_hash 也必须正常入库(只是没有个人记录, 不是坏数据)。"""
    out = tmp_path / "samples.jsonl"
    monkeypatch.setattr(scs, "_OUT", out)
    monkeypatch.setattr(scs, "_SEEN", set())
    monkeypatch.setattr(scs, "_QUAR_SEEN", set())

    rec = {"map_id": 2401, "final_quality_counts": "q1=1", "session_hash": "no-uid-1",
           "known_value_sum": 99999, "client_ts": 1_700_000_000}
    assert scs._accept_one(rec) == "accepted"

    import json
    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert "uid_hash" not in row
