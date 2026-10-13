"""私有 dashboard 保持 365 天；“我的记录”扩为有效 90 天及以上。"""
from __future__ import annotations

import base64
import http.cookiejar
import json
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import activation_core as ac  # noqa: E402
import activation_server as asv  # noqa: E402
import my_records  # noqa: E402
import sample_collector_server as scs  # noqa: E402
import web_ui  # noqa: E402


SECRET = b"private-dashboard-test-secret"
NOW = 10_000
DAY = 86400


def _svc(tmp_path: Path) -> asv.ActivationService:
    return asv.ActivationService(
        secret=SECRET,
        master_key_b64="ZmFrZQ==",
        bindings_path=tmp_path / "bindings.json",
        revoked_path=tmp_path / "revoked.txt",
        telemetry_path=tmp_path / "telemetry.jsonl",
        trials_path=tmp_path / "trials.json",
    )


def _limited(uid: str, days: int) -> str:
    return ac.mint_token(uid, SECRET, iat=100, exp=100 + days * DAY)


def test_legacy_permanent_and_exact_365_are_entitled(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    permanent = ac.mint_token("legacy", SECRET, iat=100)
    ok, reason, entitlement = svc.authorize_private_dashboard(permanent, now=NOW)
    assert ok and reason == "ok"
    assert entitlement == {"uid": "legacy", "kind": "legacy_permanent", "membership_exp": None}

    ok, reason, entitlement = svc.authorize_private_dashboard(_limited("annual", 365), now=NOW)
    assert ok and reason == "ok"
    assert entitlement == {"uid": "annual", "kind": "365d", "membership_exp": NOW + 365 * DAY}
    starts = json.loads((tmp_path / "paid_starts.json").read_text(encoding="utf-8"))
    assert starts == {"annual": NOW}


def test_my_records_accepts_90_days_without_unlocking_private_dashboard(
    tmp_path: Path,
) -> None:
    svc = _svc(tmp_path)
    token = _limited("quarterly", 90)

    ok, reason, entitlement = svc.authorize_my_records(token, now=NOW)
    assert ok and reason == "ok"
    assert entitlement == {
        "uid": "quarterly",
        "kind": "90d",
        "membership_exp": NOW + 90 * DAY,
    }

    private_ok, private_reason, private_entitlement = svc.authorize_private_dashboard(
        token,
        now=NOW,
    )
    assert not private_ok
    assert private_reason == "plan_required"
    assert private_entitlement is None


def test_my_records_uses_shared_clock_and_expires_at_90_day_boundary(
    tmp_path: Path,
) -> None:
    svc = _svc(tmp_path)
    token = _limited("quarterly-boundary", 90)

    assert svc.authorize_my_records(token, now=NOW)[0]
    entitlement = svc.authorize_my_records(token, now=NOW + 30 * DAY)[2]
    assert entitlement is not None
    assert entitlement["membership_exp"] == NOW + 90 * DAY

    ok, reason, entitlement = svc.authorize_my_records(
        token,
        now=NOW + 90 * DAY,
    )
    assert not ok and reason == "expired" and entitlement is None


def test_client_activation_clock_is_reused_by_90_day_my_records(
    tmp_path: Path,
    monkeypatch,
) -> None:
    svc = _svc(tmp_path)
    token = _limited("quarterly-client-first", 90)
    monkeypatch.setattr(ac.time, "time", lambda: NOW)
    status, result = svc.handle_activate(
        {
            "token": token,
            "device": "device-90",
            "client_version": "0.3.2-hotfix3",
        }
    )
    assert status == 200 and result["ok"]

    entitlement = svc.authorize_my_records(token, now=NOW + 30 * DAY)[2]
    assert entitlement is not None
    assert entitlement["membership_exp"] == NOW + 90 * DAY


def test_my_records_rejects_below_90_and_non_whole_day_without_starting_clock(
    tmp_path: Path,
) -> None:
    svc = _svc(tmp_path)
    cases = (
        ac.mint_token("p89", SECRET, iat=100, exp=100 + 89 * DAY),
        ac.mint_token("partial", SECRET, iat=100, exp=100 + 90 * DAY + 1),
    )
    for token in cases:
        ok, reason, entitlement = svc.authorize_my_records(token, now=NOW)
        assert not ok and reason == "plan_required" and entitlement is None
    assert not (tmp_path / "paid_starts.json").exists()


def test_private_access_uses_one_subscription_clock_and_expires_at_boundary(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    token = _limited("annual", 365)
    assert svc.authorize_private_dashboard(token, now=NOW)[0]
    # 第二次访问不能把首次使用时间向后推。
    ent = svc.authorize_private_dashboard(token, now=NOW + 30 * DAY)[2]
    assert ent and ent["membership_exp"] == NOW + 365 * DAY
    ok, reason, ent = svc.authorize_private_dashboard(token, now=NOW + 365 * DAY)
    assert not ok and reason == "expired" and ent is None


def test_client_activation_clock_is_reused_by_private_dashboard(
    tmp_path: Path, monkeypatch
) -> None:
    svc = _svc(tmp_path)
    token = _limited("annual-client-first", 365)
    monkeypatch.setattr(ac.time, "time", lambda: NOW)
    status, result = svc.handle_activate({
        "token": token,
        "device": "device-1",
        "client_version": "0.2.7",
    })
    assert status == 200 and result["ok"]

    entitlement = svc.authorize_private_dashboard(token, now=NOW + 30 * DAY)[2]
    assert entitlement is not None
    assert entitlement["membership_exp"] == NOW + 365 * DAY


def test_short_plans_do_not_start_clock_or_gain_private_access(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    for days in (30, 60, 364, 366):
        ok, reason, entitlement = svc.authorize_private_dashboard(_limited(f"p{days}", days), now=NOW)
        assert not ok and reason == "plan_required" and entitlement is None
    assert not (tmp_path / "paid_starts.json").exists()


def test_revoked_365_and_permanent_tokens_are_rejected(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    (tmp_path / "revoked.txt").write_text("annual\nlegacy\n", encoding="utf-8")
    for token in (_limited("annual", 365), ac.mint_token("legacy", SECRET, iat=100)):
        ok, reason, entitlement = svc.authorize_private_dashboard(token, now=NOW)
        assert not ok and reason == "revoked" and entitlement is None

    quarterly = _limited("quarterly-revoked", 90)
    (tmp_path / "revoked.txt").write_text("quarterly-revoked\n", encoding="utf-8")
    ok, reason, entitlement = svc.authorize_my_records(quarterly, now=NOW)
    assert not ok and reason == "revoked" and entitlement is None


def test_dashboard_cookie_cannot_be_forged_from_my_cookie_and_revocation_is_live(
    tmp_path: Path, monkeypatch
) -> None:
    svc = _svc(tmp_path)
    monkeypatch.setattr(scs, "_ACT", svc)
    uid_hash = my_records.uid_hash_of("annual")
    dash_cookie, max_age = scs._dash_make_cookie(uid_hash, NOW + 365 * DAY, now=NOW)
    assert max_age == scs._DASH_TTL
    assert scs._dash_read_cookie(dash_cookie, now=NOW) == uid_hash

    # /my 与 /dashboard 使用不同签名域；任意短期用户的个人记录 cookie 不能横向升级。
    monkeypatch.setattr(scs.time, "time", lambda: NOW)
    my_cookie = scs._my_make_cookie(uid_hash)
    assert scs._dash_read_cookie(my_cookie, now=NOW) is None

    (tmp_path / "revoked.txt").write_text("annual\n", encoding="utf-8")
    assert scs._dash_read_cookie(dash_cookie, now=NOW) is None


def test_dashboard_handler_accepts_subscription_cookie_and_preserves_admin_basic(
    tmp_path: Path, monkeypatch
) -> None:
    svc = _svc(tmp_path)
    monkeypatch.setattr(scs, "_ACT", svc)
    monkeypatch.setattr(scs, "_DASH_PASS", "admin-secret")
    monkeypatch.setattr(scs, "_DASH_ALLOW_ANON", False)
    uid_hash = my_records.uid_hash_of("annual")
    cookie, _ = scs._dash_make_cookie(uid_hash, NOW + 365 * DAY, now=NOW)
    monkeypatch.setattr(scs.time, "time", lambda: NOW)

    handler = object.__new__(scs.Handler)
    handler.headers = {"Cookie": f"{scs._DASH_COOKIE}={cookie}"}
    assert handler._dash_authed()

    handler.headers = {
        "Authorization": "Basic " + base64.b64encode(b"ops:admin-secret").decode("ascii")
    }
    assert handler._dash_authed()


def test_private_login_page_has_clear_scope_and_post_only_token_flow() -> None:
    html = web_ui.private_dashboard_login_html("订阅已到期")
    assert html.index('href="/public"') < html.index('href="/private"') < html.index('href="/my"')
    assert 'method="post" action="/private"' in html
    assert 'autocomplete="current-password"' in html
    assert "每次从“私有大数据”入口进入仍需由你点击确认" in html
    assert "有效的 365 天订阅" in html and "历史永久版" in html
    assert "30/90 天档不包含" in html and "不再新售永久档" in html
    assert "订阅已到期" in html and "token=" not in html


def test_cookie_readers_fail_closed_without_activation_service(monkeypatch) -> None:
    monkeypatch.setattr(scs, "_ACT", None)
    assert scs._my_read_cookie("0123456789abcdef|9999999999|") is None
    assert scs._dash_read_cookie("0123456789abcdef|9999999999|") is None


def test_http_90_my_records_and_365_dashboard_keep_separate_access(
    tmp_path: Path, monkeypatch
) -> None:
    svc = _svc(tmp_path)
    out = tmp_path / "samples.jsonl"
    out.write_text("", encoding="utf-8")
    monkeypatch.setattr(scs, "_ACT", svc)
    monkeypatch.setattr(scs, "_OUT", out)
    monkeypatch.setattr(scs, "_DASH_PASS", "admin-secret")
    monkeypatch.setattr(scs, "_DASH_ALLOW_ANON", False)
    monkeypatch.setattr(scs, "_DASH_CACHE", {
        "key": None, "plain": None, "gz": None, "etag": None, "built_mono": 0.0,
    })
    monkeypatch.setattr(scs, "_DASH_DETAIL_CACHE", {})
    monkeypatch.setattr(scs, "DASH_REBUILD_INTERVAL", 0.0)

    server = scs.ThreadingHTTPServer(("127.0.0.1", 0), scs.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        annual_req = urllib.request.Request(
            base + "/private",
            data=urllib.parse.urlencode({"token": _limited("annual-http", 365)}).encode(),
        )
        with opener.open(annual_req, timeout=10) as response:
            html = response.read().decode("utf-8")
            assert response.geturl().endswith("/dashboard")
            assert "仓库账本" in html
        assert any(cookie.name == scs._DASH_COOKIE for cookie in jar)

        short_opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        short_req = urllib.request.Request(
            base + "/private",
            data=urllib.parse.urlencode({"token": _limited("quarterly-private", 90)}).encode(),
        )
        with short_opener.open(short_req, timeout=10) as response:
            html = response.read().decode("utf-8")
            assert response.geturl().endswith("/private")
            assert "当前档位不含私有大数据权限" in html

        my_jar = http.cookiejar.CookieJar()
        my_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(my_jar))
        my_req = urllib.request.Request(
            base + "/my",
            data=urllib.parse.urlencode({"token": _limited("quarterly-my", 90)}).encode(),
        )
        with my_opener.open(my_req, timeout=10) as response:
            html = response.read().decode("utf-8")
            assert response.geturl().endswith("/my/view") and "我的记录" in html
        assert any(cookie.name == scs._MY_COOKIE for cookie in my_jar)

        # 再点击顶栏入口必须回到确认页并清会话，不能因为旧 cookie 直接放行。
        with my_opener.open(base + "/my", timeout=10) as response:
            html = response.read().decode("utf-8")
            assert response.geturl().endswith("/my") and "查看我的对局记录" in html
        assert not any(cookie.name == scs._MY_COOKIE for cookie in my_jar)

        # 私有大数据入口同样每次重新确认；/dashboard 内部会话只服务当前查看过程。
        with opener.open(base + "/private", timeout=10) as response:
            html = response.read().decode("utf-8")
            assert response.geturl().endswith("/private") and "进入私有大数据" in html
        assert not any(cookie.name == scs._DASH_COOKIE for cookie in jar)

        short_my_req = urllib.request.Request(
            base + "/my",
            data=urllib.parse.urlencode({"token": _limited("short-my", 89)}).encode(),
        )
        with short_opener.open(short_my_req, timeout=10) as response:
            html = response.read().decode("utf-8")
            assert response.geturl().endswith("/my")
            assert "当前档位不含“我的记录”权限" in html
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
