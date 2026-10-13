# -*- coding: utf-8 -*-
"""服务端 grant_trial【自然日历制】单测(0.2.2, 2026-06-28 与会员统一为日历计时): 试用 =
N 小时自然日历(从首次激活起算, 关着也走)。活动入口在 scripts/, 故把 scripts 加进 path。"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import activation_core as ac  # noqa: E402


def test_default_trial_is_twelve_calendar_hours():
    trials = {}
    ok, reason, lease = ac.grant_trial("DEFAULT-12H", trials=trials, now=1000)
    assert ok and reason == "ok"
    assert trials["DEFAULT-12H"] == {
        "schema_version": 2,
        "start": 1000,
        "window_seconds": 12 * 3600,
        "last_server_now": 1000,
    }
    assert lease["exp"] == 1000 + 12 * 3600


def test_explicit_three_twelve_policy_remains_the_rollback_control():
    start = 1000
    now = 2000
    trials = {
        "EXPLICIT-3H": {
            "schema_version": 2,
            "start": start,
            "window_seconds": 3 * 3600,
            "last_server_now": now - 1,
        }
    }

    ok, reason, lease = ac.grant_trial(
        "EXPLICIT-3H",
        trials=trials,
        trial_hours=3,
        grandfather_cutover_at=500,
        grandfather_trial_hours=12,
        now=now,
    )

    assert (ok, reason) == (True, "ok")
    assert lease["exp"] == start + 3 * 3600
    assert trials["EXPLICIT-3H"]["window_seconds"] == 3 * 3600


def test_first_activation_full_window():
    t = {}
    ok, r, lease = ac.grant_trial("D", trials=t, trial_hours=12.0, now=1000)
    assert ok and r == "ok"
    assert t["D"]["start"] == 1000              # 记首次激活 ts
    assert t["D"]["window_seconds"] == 12 * 3600
    assert lease["exp"] - 1000 == 12 * 3600     # 剩满 12h


def test_calendar_counts_down_regardless_of_calls():
    # 日历: 不管中途调用几次, 到期只看 now 是否过 start+window(关着也走)。
    t = {}
    ac.grant_trial("D", trials=t, trial_hours=12.0, now=1000)
    # 第二次也显式传同一 window(此用例测"到期点锚定 start 不随调用次数变", 与默认无关)
    ok, r, lease = ac.grant_trial("D", trials=t, trial_hours=12.0, now=1000 + 5 * 3600)   # 5h 后(无论开关)
    assert ok and lease["exp"] == 1000 + 12 * 3600                      # 到期点不变
    assert lease["exp"] - (1000 + 5 * 3600) == 7 * 3600                 # 剩 7h


def test_expires_after_window():
    t = {"D": 1000}
    ok, r, lease = ac.grant_trial("D", trials=t, trial_hours=12.0, now=1000 + 12 * 3600 + 1)
    assert not ok and r == "trial_expired" and lease is None


def test_legacy_usage_dict_migrated_to_calendar_start():
    # 旧用时制 dict({used,last}) -> 折算 start = now - used(保留已耗)。
    t = {"OLD": {"used": 3600, "last": 8000}}
    ok, r, lease = ac.grant_trial("OLD", trials=t, trial_hours=12.0, now=9000)
    assert ok and t["OLD"]["start"] == 9000 - 3600         # start 折算
    assert t["OLD"]["window_seconds"] == 12 * 3600
    assert lease["exp"] == (9000 - 3600) + 12 * 3600       # 等效已耗 1h


# --- 付费 token: 永久 vs 限期会员(日历到期) --- #
_SEC = b"unit-test-secret"
_NOW = 1_000_000


def test_permanent_token_no_exp():
    tok = ac.mint_token("buyerA", _SEC)                 # 不带 exp = 永久
    info = ac.verify_token(tok, _SEC)
    assert info["uid"] == "buyerA" and info["exp"] is None
    ok, reason, lease = ac.activate(tok, _SEC, "DEV", bindings={}, revoked=set(), now=_NOW)
    assert ok and reason == "ok" and "paid_exp" not in lease   # 永久无会员到期
    assert lease["plan_kind"] == "legacy_permanent"
    assert lease["feature_entitlements"] == {"pet_companion": "full"}


def test_subscription_plan_and_pet_entitlement_matrix():
    cases = [
        (30, "30d", "quota"),
        (90, "90d", "full"),
        (365, "365d", "full"),
    ]
    for days, expected_plan, expected_pet in cases:
        token = ac.mint_token(
            f"buyer-{days}",
            _SEC,
            iat=_NOW,
            exp=_NOW + days * 86400,
        )
        ok, reason, lease = ac.activate(
            token,
            _SEC,
            f"DEV-{days}",
            bindings={},
            revoked=set(),
            paid_starts={},
            now=_NOW,
        )
        assert ok and reason == "ok"
        assert lease["plan_kind"] == expected_plan
        assert lease["feature_entitlements"] == {
            "pet_companion": expected_pet,
        }

    custom = ac.mint_token(
        "buyer-custom",
        _SEC,
        iat=_NOW,
        exp=_NOW + 3 * 3600,
        roaming=True,
    )
    ok, reason, lease = ac.activate(
        custom,
        _SEC,
        "DEV-CUSTOM",
        bindings={},
        revoked=set(),
        paid_starts={},
        now=_NOW,
    )
    assert ok and reason == "ok"
    assert lease["plan_kind"] == "custom"
    assert lease["feature_entitlements"] == {"pet_companion": "none"}


def test_trial_lease_explicitly_grants_only_metered_pet_entitlement():
    ok, reason, lease = ac.grant_trial(
        "DEV-TRIAL-ENTITLEMENT",
        trials={},
        now=_NOW,
    )
    assert ok and reason == "ok"
    assert lease["plan_kind"] == "trial"
    assert lease["feature_entitlements"] == {"pet_companion": "trial"}
    assert lease["feature_usage"]["pet_companion"] == {
        "kind": "trial",
        "total_seconds": 600,
    }


def test_membership_starts_at_first_activation_not_mint():
    # 核心变更(2026-07-01): 30天会员从【首次激活】起算, 不是 token 生成时刻。
    # 生成5天后才激活 -> 会员到期 = 激活+30天(预生成/囤票不白流逝)。
    tok = ac.mint_token("buyerB", _SEC, iat=_NOW, exp=_NOW + 30 * 86400)
    ps: dict = {}
    act_at = _NOW + 5 * 86400
    ok, reason, lease = ac.activate(tok, _SEC, "DEV", bindings={}, revoked=set(),
                                    paid_starts=ps, now=act_at)
    assert ok and reason == "ok"
    assert ps["buyerB"] == act_at                          # 记首次激活起点
    assert lease["paid_exp"] == act_at + 30 * 86400        # = 激活+30天
    assert lease["paid_exp"] != _NOW + 30 * 86400          # 明确: 不是生成时算
    assert lease["exp"] <= lease["paid_exp"]               # 离线宽限不超会员期


def test_membership_heartbeat_and_rebind_do_not_reset_clock():
    tok = ac.mint_token("buyerB2", _SEC, iat=_NOW, exp=_NOW + 30 * 86400)
    ps: dict = {}
    b: dict = {}
    act_at = _NOW + 5 * 86400
    ac.activate(tok, _SEC, "DEV1", bindings=b, revoked=set(), paid_starts=ps, now=act_at)
    start0 = ps["buyerB2"]
    # 心跳(3天后同设备再激活): 起点不变
    ok, _, lease = ac.activate(tok, _SEC, "DEV1", bindings=b, revoked=set(),
                               paid_starts=ps, now=act_at + 3 * 86400)
    assert ok and ps["buyerB2"] == start0 and lease["paid_exp"] == start0 + 30 * 86400
    # 换机(人工删 binding 后新设备激活): 时钟仍不重置(paid_starts 按 uid)
    del b["buyerB2"]
    ok2, _, lease2 = ac.activate(tok, _SEC, "DEV2", bindings=b, revoked=set(),
                                 paid_starts=ps, now=act_at + 10 * 86400)
    assert ok2 and ps["buyerB2"] == start0 and lease2["paid_exp"] == start0 + 30 * 86400


def test_membership_expires_30d_after_activation():
    tok = ac.mint_token("buyerB3", _SEC, iat=_NOW, exp=_NOW + 30 * 86400)
    ps: dict = {}
    b: dict = {}
    act_at = _NOW + 5 * 86400
    ac.activate(tok, _SEC, "DEV", bindings=b, revoked=set(), paid_starts=ps, now=act_at)
    # 激活后第 31 天 -> 到期
    ok, reason, _ = ac.activate(tok, _SEC, "DEV", bindings=b, revoked=set(),
                                paid_starts=ps, now=act_at + 31 * 86400)
    assert not ok and reason == "expired"


def test_membership_lease_capped_to_membership_end():
    # 会员剩 2 天(28 天前激活)时, lease 宽限封顶到会员结束(不给满 lease_days)。
    tok = ac.mint_token("buyerC", _SEC, iat=_NOW, exp=_NOW + 30 * 86400)
    ps: dict = {"buyerC": _NOW}                            # 已在 _NOW 首次激活
    near = _NOW + 28 * 86400
    ok, _, lease = ac.activate(tok, _SEC, "DEV", bindings={"buyerC": "DEV"}, revoked=set(),
                               paid_starts=ps, lease_days=30, now=near)
    assert ok and lease["exp"] == _NOW + 30 * 86400        # = 会员结束(start+30d), 非 near+30天


def test_bad_token_and_revoked_still_work():
    assert ac.activate("garbage", _SEC, "DEV", bindings={}, revoked=set(), now=_NOW)[1] == "bad_token"
    tok = ac.mint_token("buyerD", _SEC)
    ok, reason, _ = ac.activate(tok, _SEC, "DEV", bindings={}, revoked={"buyerD"}, now=_NOW)
    assert not ok and reason == "revoked"


# --- 按小时临时 token + 网吧可换机(roaming)(2026-07-01) --- #
def test_hourly_token_duration_from_activation():
    tok = ac.mint_token("wangba1", _SEC, iat=_NOW, exp=_NOW + 3 * 3600)   # 3 小时
    info = ac.verify_token(tok, _SEC)
    assert info["exp"] == _NOW + 3 * 3600 and info["roaming"] is False
    ps: dict = {}
    act = _NOW + 10 * 86400                        # 生成很久后才激活
    ok, _, lease = ac.activate(tok, _SEC, "PC1", bindings={}, revoked=set(), paid_starts=ps, now=act)
    assert ok and lease["paid_exp"] == act + 3 * 3600      # 从激活起 3 小时
    ok2, r2, _ = ac.activate(tok, _SEC, "PC1", bindings={"wangba1": "PC1"}, revoked=set(),
                             paid_starts=ps, now=act + 3 * 3600 + 1)
    assert not ok2 and r2 == "expired"             # 激活满 3 小时到期


def test_roaming_token_allows_device_switch():
    tok = ac.mint_token("wangba2", _SEC, iat=_NOW, exp=_NOW + 3 * 3600, roaming=True)
    assert ac.verify_token(tok, _SEC)["roaming"] is True
    b: dict = {}
    ps: dict = {}
    ok1, _, _ = ac.activate(tok, _SEC, "PC1", bindings=b, revoked=set(), paid_starts=ps, now=_NOW)
    assert ok1 and b["wangba2"] == "PC1"
    ok2, _, _ = ac.activate(tok, _SEC, "PC2", bindings=b, revoked=set(), paid_starts=ps, now=_NOW + 600)
    assert ok2 and b["wangba2"] == "PC2"           # roaming: 换机重绑, 不拒
    assert ps["wangba2"] == _NOW                   # 时钟不因换机重置


def test_non_roaming_limited_still_device_locked():
    tok = ac.mint_token("buyerE", _SEC, iat=_NOW, exp=_NOW + 30 * 86400)  # 非 roaming
    b: dict = {}
    ps: dict = {}
    ac.activate(tok, _SEC, "PC1", bindings=b, revoked=set(), paid_starts=ps, now=_NOW)
    ok, r, _ = ac.activate(tok, _SEC, "PC2", bindings=b, revoked=set(), paid_starts=ps, now=_NOW + 600)
    assert not ok and r == "device_mismatch"       # 换机被拒(防转卖)


def test_permanent_token_device_locked_cannot_switch():
    # 永久 token 始终设备绑定: 换机被拒(防反复换机滥用)。
    tok = ac.mint_token("permBuyer", _SEC)             # 永久
    b: dict = {}
    ps: dict = {}
    ac.activate(tok, _SEC, "PC1", bindings=b, revoked=set(), paid_starts=ps, now=_NOW)
    ok, r, _ = ac.activate(tok, _SEC, "PC2", bindings=b, revoked=set(), paid_starts=ps, now=_NOW + 600)
    assert not ok and r == "device_mismatch"           # 永久换机=拒


def test_roaming_ignored_for_permanent_token():
    # 永久 token 即使 mint 时给 roaming=True 也【不带 roaming】(payload 3段, 始终设备绑定)。
    tok = ac.mint_token("permBuyer2", _SEC, roaming=True)   # roaming 对永久无效
    info = ac.verify_token(tok, _SEC)
    assert info["exp"] is None and info["roaming"] is False
    assert tok.split(".", 1)[0].count("|") == 2        # 3段(v1|uid|iat), 无 |r
