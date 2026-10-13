# -*- coding: utf-8 -*-
"""T2 支付服务核心单测: 易支付签名/验签 + 建单 + 回调自动 mint + 幂等 + 防篡改金额 + 公版档位。

不触真网络/真支付平台: 直接构造回调参数验签; mint 用真 activation_core(验 token 可解)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import activation_core  # noqa: E402
import pay_service  # noqa: E402
from pay_service import EpayAdapter, PayService, TIERS  # noqa: E402

_SECRET = b"test-activation-secret-key-123456"
_GATEWAY = "https://pay.example.com"
_PID = "1001"
_KEY = "MERCHANTKEYABC"
_BASE = "http://203.0.113.10:18900"


@pytest.fixture
def svc(tmp_path):
    adapter = EpayAdapter(_GATEWAY, _PID, _KEY)
    return PayService(adapter=adapter, secret=_SECRET,
                      orders_path=tmp_path / "orders.jsonl", base_url=_BASE)


def _signed_notify(adapter: EpayAdapter, order: dict, *, status="TRADE_SUCCESS", money=None) -> dict:
    params = {
        "pid": _PID, "trade_no": "PLATFORM123", "out_trade_no": order["out_trade_no"],
        "type": "alipay", "name": TIERS[order["tier"]]["name"],
        "money": money if money is not None else order["amount"],
        "trade_status": status,
    }
    params["sign"] = adapter.sign(params)
    return params


def test_sign_deterministic_and_verify(svc):
    a = svc.adapter
    p = {"pid": _PID, "out_trade_no": "BK1", "money": "11.11", "name": "x"}
    s1 = a.sign(p)
    s2 = a.sign(dict(reversed(list(p.items()))))  # 顺序无关
    assert s1 == s2
    p["sign"] = s1
    assert a.verify_notify(p) is True
    p["sign"] = "deadbeef"
    assert a.verify_notify(p) is False


def test_build_pay_url_has_sign_and_amount(svc):
    r = svc.create_order(device="DEV_AAA", tier="m30", now=1000)
    assert r["ok"] and r["amount"] == "12.88"
    assert "sign=" in r["pay_url"] and "submit.php" in r["pay_url"]
    assert r["out_trade_no"] in r["pay_url"]


def test_public_tiers_are_finite_and_match_product_contract():
    assert {
        key: (tier["amount"], tier["days"], tier["roaming"])
        for key, tier in TIERS.items()
    } == {
        "m30": ("12.88", 30, False),
        "d90": ("28.88", 90, False),
        "y365": ("68.88", 365, False),
        "r30": ("19.99", 30, True),
        "r90": ("39.99", 90, True),
        "r365": ("89.99", 365, True),
    }
    assert "perm" not in TIERS


def test_create_bad_tier_and_no_device(svc):
    assert svc.create_order(device="D", tier="nope")["error"] == "bad_tier"
    assert svc.create_order(device="", tier="m30")["error"] == "no_device"


def test_same_device_same_second_cross_tier_orders_never_collide(svc):
    first = svc.create_order(device="DEV_SAME", tier="m30", now=1000)
    second = svc.create_order(device="DEV_SAME", tier="y365", now=1000)

    assert first["out_trade_no"] != second["out_trade_no"]
    assert svc.query_order(first["out_trade_no"])["tier"] == "m30"
    assert svc.query_order(second["out_trade_no"])["tier"] == "y365"


def test_same_tier_nonce_collision_retries_without_overwrite(svc, monkeypatch):
    nonces = iter(["a" * 16, "a" * 16, "b" * 16])
    monkeypatch.setattr(pay_service.secrets, "token_hex", lambda _size: next(nonces))

    first = svc.create_order(device="DEV_SAME", tier="m30", now=1000)
    second = svc.create_order(device="DEV_SAME", tier="m30", now=1000)

    assert first["out_trade_no"] != second["out_trade_no"]
    assert len(svc._orders) == 2


def test_notify_mints_30day_membership(svc):
    r = svc.create_order(device="DEV_AAA", tier="m30", now=1000)
    otn = r["out_trade_no"]
    order = svc._orders[otn]
    resp = svc.handle_notify(_signed_notify(svc.adapter, order), now=2000)
    assert resp == "success"
    q = svc.query_order(otn)
    assert q["status"] == "paid" and q["token"]
    info = activation_core.verify_token(q["token"], _SECRET)
    assert info is not None and info["uid"] == otn
    assert info["exp"] == 2000 + 30 * 86400   # 30 天日历到期


def test_notify_mints_90day_device_bound_membership(svc):
    r = svc.create_order(device="DEV_QTR", tier="d90", now=1000)
    assert r["amount"] == "28.88"
    otn = r["out_trade_no"]
    svc.handle_notify(_signed_notify(svc.adapter, svc._orders[otn]), now=2000)
    info = activation_core.verify_token(svc.query_order(otn)["token"], _SECRET)
    assert info is not None
    assert info["exp"] == 2000 + 90 * 86400
    assert info["roaming"] is False


def test_notify_mints_365day_device_bound_highest_tier(svc):
    r = svc.create_order(device="DEV_BBB", tier="y365", now=1000)
    otn = r["out_trade_no"]
    svc.handle_notify(_signed_notify(svc.adapter, svc._orders[otn]), now=2000)
    info = activation_core.verify_token(svc.query_order(otn)["token"], _SECRET)
    assert info is not None
    assert info["exp"] == 2000 + 365 * 86400
    assert info["roaming"] is False


@pytest.mark.parametrize(
    ("tier", "amount", "days"),
    [("r30", "19.99", 30), ("r90", "39.99", 90), ("r365", "89.99", 365)],
)
def test_notify_mints_roaming_tiers(svc, tier, amount, days):
    r = svc.create_order(device="DEV_ROAM", tier=tier, now=1000)
    assert r["amount"] == amount
    otn = r["out_trade_no"]
    svc.handle_notify(_signed_notify(svc.adapter, svc._orders[otn]), now=2000)
    info = activation_core.verify_token(svc.query_order(otn)["token"], _SECRET)
    assert info is not None
    assert info["exp"] == 2000 + days * 86400
    assert info["roaming"] is True


def test_notify_bad_sign_no_mint(svc):
    r = svc.create_order(device="DEV_AAA", tier="m30", now=1000)
    p = _signed_notify(svc.adapter, svc._orders[r["out_trade_no"]])
    p["sign"] = "tampered"
    assert svc.handle_notify(p) == "fail"
    assert svc.query_order(r["out_trade_no"])["token"] is None


def test_notify_tampered_amount_rejected(svc):
    r = svc.create_order(device="DEV_AAA", tier="y365", now=1000)  # 应 68.88
    # 攻击者用最低档金额的回调想拿 365 天最高档
    p = _signed_notify(svc.adapter, svc._orders[r["out_trade_no"]], money="12.88")
    assert svc.handle_notify(p) == "fail"
    assert svc.query_order(r["out_trade_no"])["token"] is None


def test_notify_idempotent(svc):
    r = svc.create_order(device="DEV_AAA", tier="m30", now=1000)
    order = svc._orders[r["out_trade_no"]]
    p = _signed_notify(svc.adapter, order)
    svc.handle_notify(p, now=2000)
    tok1 = svc.query_order(r["out_trade_no"])["token"]
    svc.handle_notify(p, now=3000)   # 平台重推
    tok2 = svc.query_order(r["out_trade_no"])["token"]
    assert tok1 == tok2   # 幂等: 不重复 mint


def test_orders_persist_across_reload(svc, tmp_path):
    r = svc.create_order(device="DEV_AAA", tier="y365", now=1000)
    svc.handle_notify(_signed_notify(svc.adapter, svc._orders[r["out_trade_no"]]), now=2000)
    # 新实例读同一 orders 文件 -> 订单+token 还在
    svc2 = PayService(adapter=EpayAdapter(_GATEWAY, _PID, _KEY), secret=_SECRET,
                      orders_path=tmp_path / "orders.jsonl", base_url=_BASE)
    q = svc2.query_order(r["out_trade_no"])
    assert q["status"] == "paid" and q["token"]
