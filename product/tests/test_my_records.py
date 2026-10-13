"""个人对局记录 /my(0.2.7 · uid-scope)设计门。

⭐三条安全铁律(错一条就是事故):
  1. 【cookie 必须验签】—— 否则用户改一下 cookie 里的 uid_hash 就能看别人的对局记录。
  2. 【token 绝不进 URL】—— URL 会落进浏览器历史 / Referer / caddy 访问日志,而 token 是能激活
     软件的凭证,泄露 = 被人白嫖。故只走 POST 表单,且 cookie 里只放 uid_hash 不放 token。
  3. 【撤销立即生效】—— 退款/撤销的 token 不能再看数据(与 /activate 同一判据)。

另: uid_hash 会被拼进字符串搜索的 needle,必须校验形状(防注入)。
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import activation_core as ac  # noqa: E402
import activation_server as asv  # noqa: E402
import my_records as mr  # noqa: E402
import sample_collector_server as scs  # noqa: E402


SECRET = b"test-activation-secret"
DAY = 86400


def _svc(tmp_path: Path) -> asv.ActivationService:
    return asv.ActivationService(
        secret=SECRET, master_key_b64="k" * 44,
        bindings_path=tmp_path / "b.json", revoked_path=tmp_path / "revoked.txt",
        telemetry_path=tmp_path / "t.jsonl", trials_path=tmp_path / "tr.json",
    )


def _sample(uid_hash=None, **kw):
    r = {"map_id": 2401, "hero": "aisha", "final_quality_counts": "q1=2;q5=1;q6=2",
         "known_value_sum": 500000, "received_ts": time.time(),
         "player": "我", "winner": "我",
         "rare_items": [{"n": "神医《人痘术》", "q": 6, "v": 158940}]}
    if uid_hash:
        r["uid_hash"] = uid_hash
    r.update(kw)
    return r


def _write(out: Path, recs):
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
                   encoding="utf-8")


# --------------------------------------------------------------------------- #
# 身份链: token -> uid -> uid_hash(两端必须一致)
# --------------------------------------------------------------------------- #
def test_uid_hash_matches_client_side_exactly():
    """服务端算的 uid_hash 必须与客户端 sample_upload._uid_hash 【完全一致】, 否则永远匹配不上。"""
    from bidking_lab.live import sample_upload as su
    from bidking_lab.secure import client_activate as ca
    import pytest as _pt

    uid = "order-abc-123"
    token = ac.mint_token(uid, SECRET)

    class _MP:
        def setattr(self, o, n, v):
            setattr(o, n, v)
    import unittest.mock as um
    with um.patch.object(ca, "resolve_token", lambda *a, **k: token):
        client_side = su._uid_hash()
    assert client_side == mr.uid_hash_of(uid), "两端 uid_hash 算法不一致 -> 个人记录永远是空的"


# --------------------------------------------------------------------------- #
# ⭐铁律 1: cookie 必须验签(改 cookie 看不了别人的数据)
# --------------------------------------------------------------------------- #
def test_cookie_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(scs, "_ACT", _svc(tmp_path))
    uh = mr.uid_hash_of("u-1")
    assert scs._my_read_cookie(scs._my_make_cookie(uh)) == uh


def test_forged_cookie_rejected(tmp_path, monkeypatch):
    """⭐把 cookie 里的 uid_hash 改成别人的 -> 签名对不上 -> 必须拒。"""
    monkeypatch.setattr(scs, "_ACT", _svc(tmp_path))
    mine = mr.uid_hash_of("u-me")
    victim = mr.uid_hash_of("u-victim")
    good = scs._my_make_cookie(mine)
    # 攻击: 保留签名, 只把 uid_hash 换成受害者的
    _, exp, sig = good.split("|")
    forged = f"{victim}|{exp}|{sig}"
    assert scs._my_read_cookie(forged) is None, "伪造的 cookie 被接受了 -> 任何人可看任何人的数据!"


def test_expired_cookie_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(scs, "_ACT", _svc(tmp_path))
    uh = mr.uid_hash_of("u-x")
    payload = f"{uh}|{int(time.time()) - 10}"
    expired = f"{payload}|{scs._my_sign(payload)}"
    assert scs._my_read_cookie(expired) is None


def test_garbage_cookie_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(scs, "_ACT", _svc(tmp_path))
    for bad in ("", "x", "a|b", "a|b|c|d", "||", "a|notanint|sig"):
        assert scs._my_read_cookie(bad) is None


# --------------------------------------------------------------------------- #
# ⭐铁律 3: 撤销立即生效
# --------------------------------------------------------------------------- #
def test_valid_token_gives_uid_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(scs, "_ACT", _svc(tmp_path))
    token = ac.mint_token("buyer-1", SECRET)
    uh, err, membership_exp = scs._my_uid_hash_from_token(token)
    assert err == "" and uh == mr.uid_hash_of("buyer-1") and membership_exp is None


def test_my_records_requires_90_days_or_legacy_long_term(tmp_path, monkeypatch):
    monkeypatch.setattr(scs, "_ACT", _svc(tmp_path))
    monkeypatch.setattr(asv.time, "time", lambda: 10_000)
    for uid, days in (("quarterly", 90), ("custom", 91), ("annual", 365)):
        token = ac.mint_token(uid, SECRET, iat=100, exp=100 + days * DAY)
        uh, err, membership_exp = scs._my_uid_hash_from_token(token)
        assert err == "" and uh == mr.uid_hash_of(uid)
        assert membership_exp == 10_000 + days * DAY

    for uid, seconds in (("short", 89 * DAY), ("partial-day", 90 * DAY + 1)):
        token = ac.mint_token(uid, SECRET, iat=100, exp=100 + seconds)
        uh, err, membership_exp = scs._my_uid_hash_from_token(token)
        assert uh is None and membership_exp is None and "90 天" in err


def test_bad_token_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(scs, "_ACT", _svc(tmp_path))
    for bad in ("", "garbage", "v1|x|1.deadbeef"):
        uh, err, membership_exp = scs._my_uid_hash_from_token(bad)
        assert uh is None and err and membership_exp is None


def test_revoked_token_cannot_view(tmp_path, monkeypatch):
    """⭐退款/撤销的 token 不能再看数据(与 /activate 同一判据, 且实时读文件)。"""
    svc = _svc(tmp_path)
    monkeypatch.setattr(scs, "_ACT", svc)
    token = ac.mint_token("refunded-user", SECRET)
    assert scs._my_uid_hash_from_token(token)[0] is not None      # 撤销前能看
    (tmp_path / "revoked.txt").write_text("refunded-user\n", encoding="utf-8")
    uh, err, membership_exp = scs._my_uid_hash_from_token(token)
    assert uh is None and "撤销" in err and membership_exp is None  # 撤销后立刻不能看


def test_my_cookie_never_outlives_membership(tmp_path, monkeypatch):
    monkeypatch.setattr(scs, "_ACT", _svc(tmp_path))
    uid_hash = mr.uid_hash_of("annual-expiring")
    cookie = scs._my_make_cookie(uid_hash, membership_exp=10_100, now=10_000)
    assert scs._my_read_cookie(cookie, now=10_099) == uid_hash
    assert scs._my_read_cookie(cookie, now=10_100) is None
    assert scs._my_read_cookie(cookie, now=10_101) is None


def test_http_90_day_cookie_expires_at_exact_membership_boundary_and_revokes(
    tmp_path, monkeypatch
):
    svc = _svc(tmp_path)
    out = tmp_path / "samples.jsonl"
    out.write_text("", encoding="utf-8")
    monkeypatch.setattr(scs, "_ACT", svc)
    monkeypatch.setattr(scs, "_OUT", out)
    monkeypatch.setattr(scs, "_rate_ok", lambda _ip: True)
    monkeypatch.setattr(scs, "_my_view_rate_ok", lambda _uid_hash: True)
    monkeypatch.setattr(scs, "_HISTORY_INDEX_STATE", "ready")

    started_at = 10_000
    token = ac.mint_token("quarterly-http-boundary", SECRET, iat=100, exp=100 + 90 * DAY)
    uid_hash, err, membership_exp = scs._my_uid_hash_from_token(token, now=started_at)
    assert err == "" and uid_hash == mr.uid_hash_of("quarterly-http-boundary")
    assert membership_exp == started_at + 90 * DAY

    clock = {"now": membership_exp - 1}
    monkeypatch.setattr(scs.time, "time", lambda: clock["now"])
    server = scs.ThreadingHTTPServer(("127.0.0.1", 0), scs.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def request(method, path, *, body=b"", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

    try:
        body = urllib.parse.urlencode({"token": token}).encode("ascii")
        status, headers, _ = request(
            "POST",
            "/my",
            body=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body)),
            },
        )
        assert status == 302 and headers["Location"] == "/my/view"
        assert "Max-Age=1" in headers["Set-Cookie"]
        cookie_pair = headers["Set-Cookie"].split(";", 1)[0]

        status, _, page = request("GET", "/my/view", headers={"Cookie": cookie_pair})
        assert status == 200 and "我的记录".encode() in page

        clock["now"] = membership_exp
        status, headers, _ = request("GET", "/my/view", headers={"Cookie": cookie_pair})
        assert status == 302 and headers["Location"] == "/my"
        status, headers, page = request(
            "POST",
            "/my",
            body=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body)),
            },
        )
        assert status == 200 and "Set-Cookie" not in headers
        assert "订阅已到期".encode() in page

        clock["now"] = membership_exp + 1
        status, headers, _ = request("GET", "/my/view", headers={"Cookie": cookie_pair})
        assert status == 302 and headers["Location"] == "/my"

        revoke_uid = "quarterly-http-revoke"
        revoke_token = ac.mint_token(revoke_uid, SECRET, iat=200, exp=200 + 90 * DAY)
        clock["now"] = started_at + 1
        revoke_body = urllib.parse.urlencode({"token": revoke_token}).encode("ascii")
        status, headers, _ = request(
            "POST",
            "/my",
            body=revoke_body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(revoke_body)),
            },
        )
        assert status == 302
        revoke_cookie = headers["Set-Cookie"].split(";", 1)[0]
        svc.revoked_path.write_text(revoke_uid + "\n", encoding="utf-8")
        status, headers, _ = request("GET", "/my/view", headers={"Cookie": revoke_cookie})
        assert status == 302 and headers["Location"] == "/my"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_existing_my_cookie_is_rejected_immediately_after_revocation(tmp_path, monkeypatch):
    """已登录 cookie 也必须每次实时读 revoked.txt，不能等 7 天 TTL 结束。"""
    svc = _svc(tmp_path)
    monkeypatch.setattr(scs, "_ACT", svc)
    uid = "refunded-after-login"
    uid_hash = mr.uid_hash_of(uid)
    cookie = scs._my_make_cookie(uid_hash)
    assert scs._my_read_cookie(cookie) == uid_hash

    (tmp_path / "revoked.txt").write_text(uid + "\n", encoding="utf-8")
    assert scs._my_read_cookie(cookie) is None


def test_existing_my_cookie_fails_closed_when_revocation_file_cannot_be_read(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(scs, "_ACT", svc)
    uid_hash = mr.uid_hash_of("revocation-io-failure")
    cookie = scs._my_make_cookie(uid_hash)
    assert scs._my_read_cookie(cookie) == uid_hash

    svc.revoked_path.write_text("someone-else\n", encoding="utf-8")
    original_read_text = Path.read_text

    def fail_revoked_read(path: Path, *args, **kwargs):
        if path == svc.revoked_path:
            raise OSError("synthetic revoked read failure")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_revoked_read)
    assert scs._my_read_cookie(cookie) is None


# --------------------------------------------------------------------------- #
# ⭐uid-scope: 只看得到自己的
# --------------------------------------------------------------------------- #
def test_only_own_samples_returned(tmp_path):
    me = mr.uid_hash_of("me")
    other = mr.uid_hash_of("other")
    out = tmp_path / "s.jsonl"
    _write(out, [
        _sample(me, known_value_sum=111),
        _sample(other, known_value_sum=999),          # 别人的
        _sample(None, known_value_sum=888),           # 存量(无 uid_hash)
        _sample(me, known_value_sum=222),
    ])
    mine = mr.load_user_samples(out, me)
    assert len(mine) == 2
    vals = {r["known_value_sum"] for r in mine}
    assert vals == {111, 222}
    assert 999 not in vals, "看到了别人的数据!"
    assert 888 not in vals, "看到了无主的存量数据!"


def test_malformed_uid_hash_returns_empty(tmp_path):
    """uid_hash 会被拼进字符串搜索的 needle -> 形状不合法必须直接返回空(防注入)。"""
    out = tmp_path / "s.jsonl"
    _write(out, [_sample(mr.uid_hash_of("me"))])
    for bad in ("", "x", "../etc", '" or "1', "A" * 16, "0123456789abcdefX"):
        assert mr.load_user_samples(out, bad) == []


def test_legacy_samples_have_no_owner(tmp_path):
    """0.2.7 之前的存量样本没有 uid_hash -> 谁都查不到它们(只进公共聚合)。"""
    out = tmp_path / "s.jsonl"
    _write(out, [_sample(None) for _ in range(5)])
    assert mr.load_user_samples(out, mr.uid_hash_of("anyone")) == []


# --------------------------------------------------------------------------- #
# 统计与渲染
# --------------------------------------------------------------------------- #
def test_summary_counts_and_winrate(tmp_path):
    me = mr.uid_hash_of("me")
    out = tmp_path / "s.jsonl"
    _write(out, [
        _sample(me, known_value_sum=100, player="我", winner="我"),      # 赢
        _sample(me, known_value_sum=300, player="我", winner="别人"),    # 输
    ])
    s = mr.summarize_user(mr.load_user_samples(out, me))
    assert s["n"] == 2
    assert s["avg_value"] == 200
    assert s["win_rate"] == 0.5
    assert s["best"]["known_value_sum"] == 300


def test_empty_summary_and_pages_render():
    """没数据时也要出页面(而不是崩), 且要解释为什么是空的。"""
    html = mr.build_my_html(mr.summarize_user([]), [])
    assert "还没有你的对局数据" in html
    assert "0.2.7" in html            # 要告诉用户: 旧版本上传的不算
    login = mr.build_login_html()
    assert "<form" in login and 'type="password"' in login
    assert 'autocomplete="current-password"' in login
    assert "每次进入仍需由你点击确认" in login
    assert "公版默认" in html and "公版默认" in login
    assert "上传是自愿的" not in html + login


def test_login_form_posts_not_gets():
    """⭐铁律 2: token 必须走 POST 表单 —— 绝不能出现在 URL 里。"""
    html = mr.build_login_html()
    assert 'method="post"' in html.lower()
    assert 'method="get"' not in html.lower()


def test_my_page_renders_with_data(tmp_path):
    me = mr.uid_hash_of("me")
    out = tmp_path / "s.jsonl"
    _write(out, [_sample(me)])
    samples = mr.load_user_samples(out, me)
    html = mr.build_my_html(mr.summarize_user(samples), samples)
    assert "我的记录" in html
    assert "神医《人痘术》" in html      # 自己的稀有藏品可以显示(这是他自己的数据)
    assert "未知别墅" in html and '<span class="mid">2401</span>' in html
    assert "我的出价" in html and "当局盈亏" in html


def test_personal_history_is_permanent_and_sorted_by_play_time(tmp_path):
    me = mr.uid_hash_of("long-term-user")
    out = tmp_path / "long.jsonl"
    rows = []
    for i in range(520):
        rows.append(_sample(me, client_ts=1_700_000_000 + i,
                            received_ts=1_900_000_000 - i, known_value_sum=i))
    _write(out, rows)
    got = mr.load_user_samples(out, me)
    assert len(got) == 520, "默认读取仍截断在旧的 500 局，不是永久记录"
    assert got[0]["client_ts"] > got[-1]["client_ts"], "错误地按收样时间覆盖了游玩时间顺序"
    assert len(mr.load_user_samples(out, me, limit=7)) == 7


def test_profit_truth_estimate_and_missing_are_distinguished():
    base = _sample(None, map_id=999999, known_value_sum=1000, player="我")
    fee = mr._entry_fee(base)
    won = dict(base, winner="我", winner_bid=800)
    truth = dict(base, winner="别人", winner_bid=1200, settlement_welfare=55)
    estimate = dict(base, winner="别人", winner_bid=1200)
    missing = dict(base, winner="别人", winner_bid=None)
    assert mr._profit_of(won) == (200 - fee, False)
    won_at_loss = dict(base, winner="我", winner_bid=1200)
    assert mr._profit_of(won_at_loss) == (-180 - fee, False)
    assert mr._profit_of(truth) == (55 - fee, False)
    assert mr._profit_of(estimate) == (20 - fee, True)
    assert mr._profit_of(missing) == (None, False), "缺出价被静默当成 0 收益"


def test_winner_dividend_contract_applies_to_every_map_family() -> None:
    for map_id in (2101, 2201, 2301, 2401, 2501, 2601, 5601):
        row = _sample(
            None,
            map_id=map_id,
            known_value_sum=1_000,
            player="我",
            winner="我",
            winner_bid=1_200,
            winner_is_self=True,
        )
        assert mr._profit_of(row) == (-180 - mr._entry_fee(row), False), map_id


def test_recent_records_show_exact_bid_profit_and_mark_legacy_estimate():
    rows = [
        _sample(None, map_id=999999, known_value_sum=777_777, winner="我",
                winner_bid=123_456, self_bid=123_456, winner_is_self=True),
        _sample(None, map_id=999999, known_value_sum=500_000, winner="别人",
                winner_bid=620_000, self_bid=410_000, winner_is_self=False,
                settlement_welfare=23_456),
        _sample(None, map_id=999999, known_value_sum=800_000, winner="别人",
                winner_bid=900_000),
    ]

    html = mr.build_my_html(mr.summarize_user(rows), rows)

    assert "123,456" in html and "410,000" in html
    assert "+654,321" in html and "+23,456" in html
    assert "≈+10,000" in html
    assert "≈ 为旧记录福利估算" in html


def test_personal_timeline_and_curves_render():
    day1, day2 = 1_783_771_340.0, 1_783_857_740.0
    rows = [
        _sample(None, client_ts=day1, received_ts=day2 + 999, winner_bid=400000,
                known_value_sum=500000, player="我", winner="我"),
        _sample(None, client_ts=day2, received_ts=day2 + 1999, winner_bid=600000,
                known_value_sum=500000, player="我", winner="别人"),
    ]
    summary = mr.summarize_user(rows)
    assert len(summary["timeline"]) == 2
    assert summary["timeline"][0]["day"] < summary["timeline"][1]["day"]
    assert summary["date_start"] == summary["timeline"][0]["day"]
    assert summary["date_end"] == summary["timeline"][1]["day"]
    assert summary["profit_n"] == 2 and summary["estimated_profit_n"] == 1
    html = mr.build_my_html(summary, rows)
    assert "盈亏走势" in html and "每日盈亏" in html and "累计收益" in html
    assert "仓库价值走势" in html
    assert "含估算" in html and "福利真值" in html


def test_daily_profit_bars_keep_sign_missing_state_and_recent_day_limit():
    timeline = []
    cumulative = 0
    for index in range(24):
        profit = None if index == 5 else (-10_000 if index % 2 else 20_000)
        if profit is not None:
            cumulative += profit
        timeline.append({
            "day": f"2026-07-{index + 1:02d}",
            "n": 1,
            "profit": profit,
            "cumulative_profit": cumulative,
            "estimated_n": int(index == 7),
        })

    rendered = mr._profit_bar_svg(timeline)

    assert rendered.count('class="profit-point"') == 21
    assert "2026-07-01" not in rendered and "07-04" in rendered
    assert 'fill="var(--pos)"' in rendered and 'fill="var(--neg)"' in rendered
    assert "当日无可计算盈亏" in rendered
    assert "其中 1 局估算" in rendered
    assert "累计收益" in rendered
    assert rendered.count('class="profit-y-grid"') + rendered.count('class="profit-zero-line"') >= 5


def test_profit_chart_switch_defaults_to_daily_without_removing_cumulative_curve():
    day = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc).timestamp()
    row = _sample(None, client_ts=day, winner_bid=400000, known_value_sum=500000)

    rendered = mr.build_my_html(mr.summarize_user([row]), [row])

    assert 'id="daily-profit"' in rendered
    assert 'id="cumulative-profit" hidden' in rendered
    assert 'data-profit-panel="daily-profit"' in rendered
    assert 'data-profit-panel="cumulative-profit"' in rendered
    assert "每日图显示最近 1 个北京时间游玩日" in rendered
    assert 'querySelectorAll(".trend-point,.profit-point")' in rendered


def test_daily_profit_axis_handles_empty_single_day_and_cross_zero():
    assert "暂无可用时间数据" in mr._profit_bar_svg([])
    single = mr._profit_bar_svg([
        {
            "day": "2026-08-04",
            "n": 3,
            "profit": 12_345,
            "cumulative_profit": 12_345,
            "estimated_n": 0,
        }
    ])
    assert single.count('class="profit-point"') == 1
    assert single.count('class="profit-y-label"') == 5
    assert "2026-08-04 · 3 局 · 当日收益 +12,345" in single
    cross_zero = mr._profit_bar_svg([
        {"day": "2026-08-03", "n": 1, "profit": -100, "cumulative_profit": -100},
        {"day": "2026-08-04", "n": 1, "profit": 200, "cumulative_profit": 100},
    ])
    assert 'class="profit-zero-line"' in cross_zero
    assert 'fill="var(--pos)"' in cross_zero and 'fill="var(--neg)"' in cross_zero


def test_personal_timeline_and_rows_use_shanghai_timezone():
    before = datetime(2026, 7, 15, 15, 59, 59, tzinfo=timezone.utc).timestamp()
    after = datetime(2026, 7, 15, 16, 0, 0, tzinfo=timezone.utc).timestamp()
    rows = [_sample(None, client_ts=before), _sample(None, client_ts=after)]
    summary = mr.summarize_user(rows)
    assert [d["day"] for d in summary["timeline"]] == ["2026-07-15", "2026-07-16"]
    html = mr.build_my_html(summary, rows)
    assert "07-16 00:00" in html
    assert "时间(北京)" in html and "时间均为北京时间(UTC+8)" in html
    assert "数据覆盖：2026-07-15 至 2026-07-16" in html


def test_single_day_timeline_explains_single_point_range():
    row = _sample(None, client_ts=datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc).timestamp())
    summary = mr.summarize_user([row])
    html = mr.build_my_html(summary, [row])
    assert summary["date_start"] == summary["date_end"] == "2026-07-14"
    assert "数据覆盖：2026-07-14（1 个北京时间游玩日）" in html
    assert "当前为单日样本，曲线只显示一个日期点" in html


def test_recent_records_expand_in_two_stages_and_cap_at_500():
    newest = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc).timestamp()
    rows = [
        _sample(None, client_ts=newest - i * 3600, known_value_sum=i)
        for i in range(520)
    ]
    rows.sort(key=lambda r: r["client_ts"], reverse=True)
    html = mr.build_my_html(mr.summarize_user(rows), rows)
    assert html.count('class="record-row"') == 500
    assert html.count('class="record-row" data-recent=') == 500
    assert "当前显示 10 / 500 局" in html
    assert "显示最近 3 天" in html
    assert "显示全部（最多 500 局）" in html
    assert 'data-stage="0"' in html
    assert 'row.hidden=!(index&lt;10' not in html  # JS 不应被 HTML 转义破坏
    assert "row.hidden=!(index<10" in html


def test_trend_points_have_rich_hover_details():
    day = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc).timestamp()
    row = _sample(None, client_ts=day, winner_bid=400000, known_value_sum=500000)
    html = mr.build_my_html(mr.summarize_user([row]), [row])
    assert html.count('class="trend-dot"') == 2
    assert "当日收益" in html and "累计收益" in html and "日均仓库价值" in html
    assert 'id="trend-tooltip"' in html and 'addEventListener("pointerenter"' in html
    assert 'addEventListener("mouseenter"' in html and 'addEventListener("mousemove"' in html


def test_public_charts_escape_suppression_and_coarse_targets_are_explicit():
    day = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc).timestamp()
    row = _sample(None, client_ts=day, winner_bid=400000, known_value_sum=500000)
    html = mr.build_my_html(mr.summarize_user([row]), [row])

    assert "focusSuppressed" in html
    assert 'e.key==="Escape"' in html
    assert 'e.key==="Enter"||e.key===" "||e.key.startsWith("Arrow")' in html
    assert "focusSuppressed!==point" in html
    assert ".records-trend-chart{min-width:max(100%,var(--records-chart-min-width,100%))}" in html
    assert ".profit-bars{min-width:max(100%,var(--records-chart-min-width,100%))}" in html
    assert "overflow-x:auto" in html
    assert "min-height:46px" in html


def test_trend_chart_has_intermediate_axes_and_large_accessible_point_targets():
    start = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc).timestamp()
    rows = [
        _sample(
            None,
            client_ts=start + day * 86400,
            winner_bid=400_000 + day * 30_000,
            known_value_sum=500_000 + (day % 4) * 20_000,
            player="我",
            winner="我" if day % 3 else "别人",
        )
        for day in range(22)
    ]
    html = mr.build_my_html(mr.summarize_user(rows), rows)

    # 两张图都要有中间 Y 刻度/参考线，而不是只写最高和最低。
    assert html.count('class="trend-y-grid"') + html.count('class="trend-zero-line"') >= 10
    assert html.count('class="trend-y-label"') >= 10
    assert 'class="trend-zero-line"' in html
    # 可见小点外有更大的透明命中区；鼠标、键盘和触摸都消费同一 tooltip 详情。
    assert html.count('class="trend-point"') == 44
    assert html.count('class="trend-hit"') == 44
    assert 'querySelectorAll(".trend-point,.profit-point")' in html
    assert 'addEventListener("click"' in html
    assert 'data-tip="' in html and "当日收益" in html and "累计收益" in html
    # 22 天仍只抽样有限 X 轴标签，避免文字互相覆盖。
    profit_svg = html[html.index('aria-label="累计收益趋势图"'):html.index("</svg>", html.index('aria-label="累计收益趋势图"'))]
    assert 4 <= profit_svg.count('class="trend-x-label"') <= 9


def test_trend_axis_handles_empty_single_day_and_cross_zero_profit():
    assert "暂无可用时间数据" in mr._trend_svg([], color="red", label="收益")
    single = mr._trend_svg(
        [("2026-08-04", 123.0)],
        color="red",
        label="收益",
        details=["2026-08-04 · 1 局 · 当日收益 +123 · 累计收益 +123"],
    )
    assert single.count('class="trend-point"') == 1
    assert 'class="trend-x-label"' in single
    cross_zero = mr._trend_svg(
        [("2026-08-03", -100.0), ("2026-08-04", 200.0)],
        color="red",
        label="收益",
    )
    assert 'class="trend-zero-line"' in cross_zero
    assert ">0<" in cross_zero


def _coarse_svg_target_width(svg: str) -> float:
    match = re.search(r"--records-chart-min-width:([0-9.]+)px", svg)
    assert match is not None, "chart must publish its point-count-derived coarse width"
    return float(match.group(1))


def _svg_viewbox_width(svg: str) -> float:
    match = re.search(r'viewBox="0 0 ([0-9.]+) [0-9.]+"', svg)
    assert match is not None
    return float(match.group(1))


def _svg_hit_rects(svg: str, class_name: str) -> list[tuple[float, float]]:
    pattern = re.compile(
        rf'class="{re.escape(class_name)}" x="([0-9.-]+)"[^>]+width="([0-9.]+)"'
    )
    return [(float(left), float(width)) for left, width in pattern.findall(svg)]


def _svg_hit_boxes(svg: str, class_name: str) -> list[tuple[float, float, float, float]]:
    pattern = re.compile(
        rf'class="{re.escape(class_name)}" x="([0-9.-]+)" y="([0-9.-]+)" '
        rf'width="([0-9.]+)" height="([0-9.]+)"'
    )
    return [tuple(map(float, values)) for values in pattern.findall(svg)]


def test_public_timeline_low_point_hit_cells_cover_the_plot_band_vertically():
    empty = mr._trend_svg([], color="red", label="累计收益")
    assert 'class="trend-hit"' not in empty

    for count in (1, 2, 21, 22, 23, 61, 257):
        points = [(f"2026-08-{index + 1:02d}", float(index)) for index in range(count)]
        rendered = mr._trend_svg(points, color="red", label="累计收益")
        target_width = _coarse_svg_target_width(rendered)
        viewbox_width = _svg_viewbox_width(rendered)
        hit_boxes = _svg_hit_boxes(rendered, "trend-hit")

        assert len(hit_boxes) == count
        for viewport_width in (400, 720, 1600):
            rendered_width = max(float(viewport_width), target_width)
            assert min(width * rendered_width / viewbox_width for _, _, width, _ in hit_boxes) > 44
            assert min(height * rendered_width / viewbox_width for _, _, _, height in hit_boxes) > 44
        assert all(15 <= top and top + height <= 190 for _, top, _, height in hit_boxes)
        assert all(
            left + width <= next_left + 0.001
            for (left, _, width, _), (next_left, _, _, _) in zip(hit_boxes, hit_boxes[1:])
        )


def test_public_timeline_coarse_hit_cells_scale_with_actual_point_count():
    for count in (21, 22, 23, 61):
        points = [(f"2026-08-{index + 1:02d}", float(index)) for index in range(count)]
        rendered = mr._trend_svg(points, color="red", label="累计收益")
        target_width = _coarse_svg_target_width(rendered)
        viewbox_width = _svg_viewbox_width(rendered)
        hit_rects = _svg_hit_rects(rendered, "trend-hit")

        assert len(hit_rects) == count
        assert min(width * target_width / viewbox_width for _, width in hit_rects) > 44
        assert all(
            left + width <= next_left + 0.001
            for (left, width), (next_left, _) in zip(hit_rects, hit_rects[1:])
        )

    daily = mr._profit_bar_svg([
        {
            "day": f"2026-08-{index + 1:02d}",
            "n": 1,
            "profit": index - 10,
            "cumulative_profit": index - 10,
        }
        for index in range(23)
    ])
    assert daily.count('class="profit-point"') == 21
    daily_width = _coarse_svg_target_width(daily)
    daily_hit_rects = _svg_hit_rects(daily, "profit-hit")
    assert min(
        width * daily_width / 900
        for _, width in daily_hit_rects
    ) > 44
    assert all(
        left + width <= next_left + 0.001
        for (left, width), (next_left, _) in zip(daily_hit_rects, daily_hit_rects[1:])
    )


def test_public_timeline_long_history_keeps_vertical_visual_metrics_stable():
    metrics = []
    for count in (21, 61, 257):
        points = [(f"2026-08-{index + 1:02d}", float(index)) for index in range(count)]
        rendered = mr._trend_svg(points, color="red", label="累计收益")
        target_width = _coarse_svg_target_width(rendered)
        scale = target_width / _svg_viewbox_width(rendered)
        assert f"width:{target_width:.0f}px;min-width:{target_width:.0f}px;" in rendered
        metrics.append({
            "chart_height": 220 * scale,
            "dot_radius": 4 * scale,
            "line_width": 2.5 * scale,
            "axis_font": 10 * scale,
        })

    for key in metrics[0]:
        values = [metric[key] for metric in metrics]
        assert max(values) - min(values) < 0.01, (key, values)

    row = _sample(None, client_ts=datetime(2026, 8, 4, tzinfo=timezone.utc).timestamp())
    html = mr.build_my_html(mr.summarize_user([row]), [row])
    assert ".records-trend-chart{min-width:max(100%,var(--records-chart-min-width,100%))}" in html


def test_public_chart_keyboard_navigation_is_scoped_to_visible_svg():
    day = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc).timestamp()
    row = _sample(None, client_ts=day, winner_bid=400000, known_value_sum=500000)
    html = mr.build_my_html(mr.summarize_user([row]), [row])

    assert 'function visibleChartPeers(point)' in html
    assert 'point.closest("svg")' in html
    assert '.closest("[hidden]")' in html
    assert 'points[Math.max(0,Math.min(points.length-1,index+delta))]' not in html
    assert 'const focusWasInPanel=' in html
    assert 'if(focusWasInPanel)button.focus()' in html


def test_public_chart_keyboard_geometry_uses_the_owned_hit_surface():
    day = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc).timestamp()
    row = _sample(None, client_ts=day, winner_bid=400000, known_value_sum=500000)
    html = mr.build_my_html(mr.summarize_user([row]), [row])

    assert 'function ownedChartHit(point)' in html
    assert 'point.querySelector(".trend-hit")' in html
    assert 'point.querySelector(".profit-hit")' in html
    assert 'function revealOwnedChartHit(point)' in html
    assert 'point.closest(".trend-wrap")' in html
    assert 'wrap.scrollLeft-=' in html
    assert 'wrap.scrollLeft+=' in html
    assert 'next.focus({preventScroll:true})' in html
    assert 'showFromOwnedHit(next)' in html
    assert 'const r=hit.getBoundingClientRect()' in html
    assert 'const r=point.getBoundingClientRect()' not in html
