# -*- coding: utf-8 -*-
"""2026-07-01 安全审计 #2-#7 回归(打包/加密窗口域: 激活服务端 + 收集器)。
#1 由 client_activate 修(见 test_client_activate); #5 是打包脚本(PS1, 不在 pytest)。

覆盖:
  #2 设备绑定并发竞态(TOCTOU)→ 加锁: 并发同 token 只绑一台 + 锁内重读磁盘(人工改绑生效)
  #3 uid 含 '|' → mint 硬拒(防永久/限期误判 + 撤销绕过)
  #4 /export 缺独立 export key → fail-closed(不回退 ingest key)
  #6 trials 并发竞态 → 加锁: 并发试用不丢记录/不损坏 trials.json
  #7 _RATE 桶无界增长 → 周期清扫过期空桶
"""
from __future__ import annotations

import http.client
import io
import json
import sys
import threading
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import activation_core as ac  # noqa: E402
import activation_server as activation_server_module  # noqa: E402
import my_records as mr  # noqa: E402
import sample_collector_server as cs  # noqa: E402
from activation_server import ActivationService  # noqa: E402

_SECRET = b"audit-secret-key-0701"


def _mk_service(tmp_path: Path) -> ActivationService:
    return ActivationService(
        secret=_SECRET, master_key_b64="bWFzdGVyLWtleQ==",
        bindings_path=tmp_path / "bindings.json",
        revoked_path=tmp_path / "revoked.txt",
        telemetry_path=tmp_path / "telemetry.jsonl",
        trials_path=tmp_path / "trials.json",
        lease_days=30, trial_hours=12.0,
    )


# --------------------------- #3 uid 含 '|' 硬拒 --------------------------- #
def test_mint_rejects_pipe_in_uid():
    with pytest.raises(ValueError):
        ac.mint_token("order|123", _SECRET)             # 含 '|' -> 拒(否则永久被误判限期)
    with pytest.raises(ValueError):
        ac.mint_token("a|b", _SECRET, exp=999999999)


def test_mint_normal_uid_roundtrips_and_revocation_works():
    # 纯数字/普通 uid 正常; 永久 token verify 为永久(exp None); 撤销生效。
    perm = ac.mint_token("synthetic-user-001", _SECRET)          # 永久(无 exp)
    info = ac.verify_token(perm, _SECRET)
    assert info is not None and info["uid"] == "synthetic-user-001" and info["exp"] is None
    ok, reason, _ = ac.activate(perm, _SECRET, "DEV1", bindings={}, revoked={"synthetic-user-001"})
    assert not ok and reason == "revoked"                # 撤销该完整 uid 生效


# --------------------------- #2 设备绑定并发 --------------------------- #
def test_concurrent_paid_activation_binds_exactly_one_device(tmp_path):
    # 每轮全新 service + 全新 token, 两线程屏障同步最大化争用 -> 恰一台 200 绑定、另一台 403 device_mismatch。
    for i in range(6):
        d = tmp_path / f"round{i}"
        d.mkdir()
        svc = _mk_service(d)
        token = ac.mint_token(f"buyer{i}", _SECRET)      # 全新永久 token(从未激活过)
        barrier = threading.Barrier(2)
        results: list[tuple[int, str]] = []
        lock = threading.Lock()

        def worker(dev: str) -> None:
            barrier.wait()
            code, resp = svc.handle_activate({"token": token, "device": dev})
            with lock:
                results.append((code, resp.get("reason", "")))

        threads = [threading.Thread(target=worker, args=(f"DEV_{k}",)) for k in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        codes = sorted(c for c, _ in results)
        assert codes == [200, 403], f"round{i}: 期望恰一台绑定, 实得 {results}"
        assert any(c == 403 and r == "device_mismatch" for c, r in results)
        bindings = json.loads((d / "bindings.json").read_text(encoding="utf-8"))
        assert len(bindings) == 1 and f"buyer{i}" in bindings  # 只一条绑定, JSON 未损坏


def test_paid_reread_from_disk_allows_manual_rebind(tmp_path):
    # #2 的第二面: 锁内重读磁盘 -> 人工删 bindings.json 里该 uid(改绑)后, 新设备可激活。
    svc = _mk_service(tmp_path)
    token = ac.mint_token("buyerX", _SECRET)
    assert svc.handle_activate({"token": token, "device": "OLD_DEV"})[0] == 200
    assert svc.handle_activate({"token": token, "device": "NEW_DEV"})[0] == 403  # 换机被拒
    # 管理员改绑: 从磁盘删该 uid
    (tmp_path / "bindings.json").write_text("{}", encoding="utf-8")
    code, resp = svc.handle_activate({"token": token, "device": "NEW_DEV"})
    assert code == 200, "锁内重读磁盘应让人工改绑立即生效"                  # 不再读陈旧内存


# --------------------------- #6 trials 并发 --------------------------- #
def test_concurrent_trial_keeps_trials_json_valid_and_single_entry(tmp_path):
    svc = _mk_service(tmp_path)
    barrier = threading.Barrier(3)

    def worker() -> None:
        barrier.wait()
        svc.handle_activate({"device": "DEV_TRIAL"})     # 无 token = 试用

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    trials = json.loads((tmp_path / "trials.json").read_text(encoding="utf-8"))  # 未损坏(加锁防torn write)
    assert list(trials.keys()) == ["DEV_TRIAL"]          # 同设备一条记录(无 lost-update 复制)


# --------------------------- #7 限流桶清扫 --------------------------- #
def test_rate_sweep_removes_stale_and_empty_buckets(monkeypatch):
    monkeypatch.setattr(cs, "_RATE", cs.defaultdict(list))
    now = 100000.0
    cs._RATE["1.1.1.1"] = [now - cs._RATE_WINDOW - 5]    # 过期(最近时间戳超窗口)
    cs._RATE["2.2.2.2"] = []                             # 空桶
    cs._RATE["3.3.3.3"] = [now - 1]                      # 新鲜, 该保留
    cs._rate_sweep(now)
    assert set(cs._RATE.keys()) == {"3.3.3.3"}           # 只留新鲜桶


def test_rate_ok_periodic_sweep_bounds_growth(monkeypatch):
    monkeypatch.setattr(cs, "_RATE", cs.defaultdict(list))
    monkeypatch.setattr(cs, "_rate_calls", 0)
    monkeypatch.setattr(cs, "_RATE_SWEEP_EVERY", 50)     # 调小便于测
    base = 200000.0
    # 50 个不同【过期】IP 打一次(时间在很早), 再触发一次新鲜调用达到 sweep 阈值。
    for i in range(49):
        assert cs._rate_ok(f"10.0.0.{i}", now=base) is True
    # 第 50 次(新鲜时间, 远晚于 base+window)触发 sweep -> 清掉前 49 个过期桶。
    cs._rate_ok("10.9.9.9", now=base + cs._RATE_WINDOW + 100)
    assert len(cs._RATE) <= 2, f"周期清扫后桶数应被钉住, 实得 {len(cs._RATE)}"


def test_effective_client_ip_only_trusts_loopback_proxy():
    """known-good: 本机 Caddy 的 XFF 生效；known-fail: 公网直连伪造 XFF 不能借 localhost 免限流。"""
    assert cs._effective_client_ip("127.0.0.1", "198.51.100.7, 127.0.0.1") == "198.51.100.7"
    assert cs._effective_client_ip("::1", "2001:db8::7") == "2001:db8::7"
    assert cs._effective_client_ip("203.0.113.9", "127.0.0.1") == "203.0.113.9"
    assert cs._effective_client_ip("127.0.0.1", "not-an-ip") == "127.0.0.1"


def test_my_view_rate_is_per_signed_uid_and_recovers_after_window(monkeypatch):
    monkeypatch.setattr(cs, "_MY_VIEW_RATE", {})
    monkeypatch.setattr(cs, "_MY_VIEW_RATE_MAX", 3)
    base = 300000.0
    assert [cs._my_view_rate_ok("a" * 16, now=base) for _ in range(3)] == [True, True, True]
    assert cs._my_view_rate_ok("a" * 16, now=base) is False
    assert cs._my_view_rate_ok("b" * 16, now=base) is True  # 同 NAT 的另一位合法用户不抢配额
    assert cs._my_view_rate_ok("a" * 16, now=base + cs._MY_VIEW_RATE_WINDOW + 1) is True
    assert "b" * 16 not in cs._MY_VIEW_RATE  # 顺手回收已经过期的其它 uid 桶


def test_my_page_cache_coalesces_scan_with_bounded_staleness(tmp_path, monkeypatch):
    out = tmp_path / "samples.jsonl"
    out.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(cs, "_OUT", out)
    monkeypatch.setattr(cs, "_MY_PAGE_CACHE", cs.OrderedDict())
    calls: list[int] = []

    def fake_load(path, _uid_hash, *, identity_store=None):
        size = Path(path).stat().st_size
        calls.append(size)
        return [{"size": size}]

    monkeypatch.setattr(mr, "load_user_samples", fake_load)
    monkeypatch.setattr(mr, "summarize_user", lambda samples: {"n": len(samples)})
    monkeypatch.setattr(mr, "build_my_html", lambda _summary, samples: f"size={samples[0]['size']}")

    uid_hash = "c" * 16
    first = cs._my_page_html(uid_hash, now=100.0)
    assert first == "size=5" and calls == [5]
    # 数据没变：普通刷新命中同一 source key，不再扫库。
    assert cs._my_page_html(uid_hash, now=102.0) == first
    assert calls == [5]

    out.write_text("new-and-larger\n", encoding="utf-8")
    # 数据刚变：5 秒内复用旧页，吸收连续刷新；窗口后必须看到新数据。
    assert cs._my_page_html(uid_hash, now=103.0) == first
    refreshed = cs._my_page_html(uid_hash, now=106.0)
    assert refreshed != first and calls == [5, out.stat().st_size]
    # 数据不再变化：即使很久以后仍按 source key 命中，不周期性白扫。
    assert cs._my_page_html(uid_hash, now=2000.0) == refreshed
    assert calls == [5, out.stat().st_size]


def test_my_page_cache_lru_is_bounded(tmp_path, monkeypatch):
    out = tmp_path / "samples.jsonl"
    out.write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(cs, "_OUT", out)
    monkeypatch.setattr(cs, "_MY_PAGE_CACHE", cs.OrderedDict())
    monkeypatch.setattr(cs, "_MY_PAGE_CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(cs, "_MY_PAGE_CACHE_MAX_CHARS", 1 << 20)
    monkeypatch.setattr(mr, "load_user_samples", lambda _path, uid, *, identity_store=None: [{"uid": uid}])
    monkeypatch.setattr(mr, "summarize_user", lambda samples: {"n": len(samples)})
    monkeypatch.setattr(mr, "build_my_html", lambda _summary, samples: samples[0]["uid"])

    for i, uid_hash in enumerate(("1" * 16, "2" * 16, "3" * 16)):
        cs._my_page_html(uid_hash, now=100.0 + i)
    assert list(cs._MY_PAGE_CACHE) == ["2" * 16, "3" * 16]


# --------------------------- #4 /export fail-closed --------------------------- #
@pytest.fixture
def _server(tmp_path, monkeypatch):
    out = tmp_path / "samples.jsonl"
    out.write_text('{"map_id":2402}\n', encoding="utf-8")
    monkeypatch.setattr(cs, "_OUT", out)
    monkeypatch.setattr(cs, "_KEY", "INGEST_KEY_SHIPPED_TO_CLIENTS")
    srv = cs.ThreadingHTTPServer(("127.0.0.1", 0), cs.Handler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield port
    finally:
        srv.shutdown()


def _get_export(port: int, api_key: str | None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"X-Api-Key": api_key} if api_key is not None else {}
    conn.request("GET", "/export", headers=headers)
    r = conn.getresponse()
    r.read()
    conn.close()
    return r.status


def _get_status(port: int, path: str, *, headers: dict[str, str] | None = None) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers=headers or {})
    response = conn.getresponse()
    response.read()
    conn.close()
    return response.status


def test_https_proxy_requests_are_rate_limited_by_forwarded_client(_server, monkeypatch):
    """本地 HTTP server 模拟 Caddy：socket peer 是 loopback，但不同 XFF 必须进入不同真实 IP 桶。"""
    monkeypatch.setattr(cs, "_RATE", cs.defaultdict(list))
    monkeypatch.setattr(cs, "_rate_calls", 0)
    monkeypatch.setattr(cs, "_RATE_MAX", 2)
    first = {"X-Forwarded-For": "198.51.100.10"}
    second = {"X-Forwarded-For": "198.51.100.11"}
    assert [_get_status(_server, "/version", headers=first) for _ in range(2)] == [200, 200]
    assert _get_status(_server, "/version", headers=first) == 429
    assert _get_status(_server, "/version", headers=second) == 200


def test_authenticated_my_route_uses_protected_page_builder(_server, tmp_path, monkeypatch):
    svc = _mk_service(tmp_path)
    monkeypatch.setattr(cs, "_ACT", svc)
    monkeypatch.setattr(cs, "_RATE", cs.defaultdict(list))
    monkeypatch.setattr(cs, "_rate_calls", 0)
    monkeypatch.setattr(cs, "_MY_VIEW_RATE", {})
    monkeypatch.setattr(cs, "_MY_VIEW_RATE_MAX", 1)
    calls: list[str] = []

    def fake_page(uid_hash: str, **_kwargs) -> str:
        calls.append(uid_hash)
        return "protected-my-page"

    monkeypatch.setattr(cs, "_my_page_html", fake_page)
    uid_hash = mr.uid_hash_of("protected-user")
    cookie = cs._my_make_cookie(uid_hash)
    headers = {
        "Cookie": f"{cs._MY_COOKIE}={cookie}",
        "X-Forwarded-For": "198.51.100.12",
    }

    conn = http.client.HTTPConnection("127.0.0.1", _server, timeout=5)
    conn.request("GET", "/my/view", headers=headers)
    response = conn.getresponse()
    assert response.status == 200 and b"protected-my-page" in response.read()
    conn.close()
    assert calls == [uid_hash]
    assert _get_status(_server, "/my/view", headers=headers) == 429
    assert calls == [uid_hash]


def test_export_fail_closed_without_export_key(_server, monkeypatch):
    monkeypatch.setattr(cs, "_EXPORT_KEY", "")           # 未配独立 export key(默认态)
    # 关键: 用【ingest key】(下发给每个客户端)也【拉不动】-> 不再回退 ingest key。
    assert _get_export(_server, "INGEST_KEY_SHIPPED_TO_CLIENTS") == 403
    assert _get_export(_server, None) == 403


def test_export_requires_correct_export_key(_server, monkeypatch):
    monkeypatch.setattr(cs, "_EXPORT_KEY", "ADMIN_EXPORT_KEY")
    assert _get_export(_server, "wrong") == 401
    assert _get_export(_server, "INGEST_KEY_SHIPPED_TO_CLIENTS") == 401   # ingest key 不再管用
    assert _get_export(_server, "ADMIN_EXPORT_KEY") == 200                # 只认独立 export key


# --------------------- 会员【激活起算】服务端级(2026-07-01) --------------------- #
def test_membership_first_activation_start_persists_and_no_reset(tmp_path):
    """服务端: 会员首次激活起点落 paid_starts.json; 已有起点再激活【不重置】(激活起算的持久化)。"""
    import time as _t
    svc = _mk_service(tmp_path)
    now = int(_t.time())
    start = now - 5 * 86400                                    # 假装 5 天前首次激活
    (tmp_path / "paid_starts.json").write_text(json.dumps({"m1": start}), encoding="utf-8")
    tok = ac.mint_token("m1", _SECRET, iat=now, exp=now + 30 * 86400)   # 时长 30 天
    code, resp = svc.handle_activate({"token": tok, "device": "DEV_M"})
    assert code == 200                                        # 5 天前激活的 30 天会员仍有效
    ps = json.loads((tmp_path / "paid_starts.json").read_text(encoding="utf-8"))
    assert ps["m1"] == start                                  # 起点【未被重置】为 now
    lease = json.loads(resp["lease"].rsplit(".", 1)[0])
    assert lease["paid_exp"] == start + 30 * 86400            # 从 5 天前起算(剩 ~25 天)


def test_membership_brand_new_activation_records_now(tmp_path):
    """全新会员 token 首次激活: paid_starts 记 now, 会员从现在起算 30 天(不是生成时刻)。"""
    import time as _t
    svc = _mk_service(tmp_path)
    now = int(_t.time())
    tok = ac.mint_token("m2", _SECRET, iat=now, exp=now + 30 * 86400)
    code, resp = svc.handle_activate({"token": tok, "device": "DEV_M2"})
    assert code == 200
    ps = json.loads((tmp_path / "paid_starts.json").read_text(encoding="utf-8"))
    assert abs(ps["m2"] - now) <= 5                           # 记录 ≈ now(首次激活起算)
    lease = json.loads(resp["lease"].rsplit(".", 1)[0])
    assert abs(lease["paid_exp"] - (now + 30 * 86400)) <= 5   # ≈ now+30 天


def test_corrupt_paid_start_fails_closed_without_reset_or_disconnect(tmp_path):
    """单个会员起点损坏时返回503，不重置权益，也不让 TypeError 打断 /activate。"""
    svc = _mk_service(tmp_path)
    now = 1_800_000_000
    token = ac.mint_token("corrupt-paid", _SECRET, iat=now, exp=now + 30 * 86400)
    original = {"corrupt-paid": None}
    (tmp_path / "paid_starts.json").write_text(json.dumps(original), encoding="utf-8")
    code, response = svc.handle_activate({"token": token, "device": "DEV_CORRUPT_PAID"})
    assert code == 503
    assert response == {"ok": False, "mode": "paid", "reason": "state_error"}
    assert "lease" not in response and "master_key" not in response
    assert json.loads((tmp_path / "paid_starts.json").read_text(encoding="utf-8")) == original


def test_collector_activation_boundary_returns_structured_503(monkeypatch, capsys):
    class BrokenActivation:
        def handle_activate(self, _body):
            raise TypeError("synthetic internal failure")

    monkeypatch.setattr(cs, "_ACT", BrokenActivation())
    code, response = cs._handle_activation_body({"token": "SECRET_TOKEN", "device": "PRIVATE_DEVICE"})
    assert code == 503
    assert response == {"ok": False, "mode": "paid", "reason": "state_error"}
    output = capsys.readouterr().out
    assert "[activation-internal-error] type=TypeError" in output
    assert "SECRET_TOKEN" not in output and "PRIVATE_DEVICE" not in output


# --------------------------- 状态文件原子写(防 torn-write 全体重置) --------------------------- #
def test_state_files_written_atomically_no_temp_leak(tmp_path):
    """paid_starts/bindings 走 os.replace 原子写(防写一半崩溃→损坏JSON→_load_json 回退{}→
    全体首激起点/绑定丢失=时钟集体重置)。断言: 激活后文件是合法 JSON 且目录无 .tmp 残留。"""
    import time as _t
    svc = _mk_service(tmp_path)
    now = int(_t.time())
    tok = ac.mint_token("mA", _SECRET, iat=now, exp=now + 30 * 86400)
    assert svc.handle_activate({"token": tok, "device": "DEV_A"})[0] == 200
    # 两个状态文件都完整可解析(原子写保证读者永不看到半截文件)
    assert json.loads((tmp_path / "paid_starts.json").read_text("utf-8"))["mA"]
    assert json.loads((tmp_path / "bindings.json").read_text("utf-8"))["mA"] == "DEV_A"
    # 无临时文件泄漏(.paid_starts.json.tmp-* / .bindings.json.tmp-*)
    leaked = [p.name for p in tmp_path.iterdir() if ".tmp-" in p.name]
    assert leaked == [], f"临时文件未清理: {leaked}"


def test_paid_activation_fails_closed_if_binding_state_cannot_persist(tmp_path, monkeypatch):
    """起算点已写但绑定写失败时也不能发 lease；重试复用同一起点。"""
    svc = _mk_service(tmp_path)
    now = 1_800_000_000
    token = ac.mint_token("persist-paid", _SECRET, iat=now, exp=now + 365 * 86400)
    original_write = activation_server_module._atomic_write_json

    def fail_bindings(path, obj):
        if Path(path) == svc.bindings_path:
            raise OSError("synthetic binding write failure")
        original_write(path, obj)

    monkeypatch.setattr(ac.time, "time", lambda: now)
    monkeypatch.setattr(activation_server_module, "_atomic_write_json", fail_bindings)
    code, response = svc.handle_activate({"token": token, "device": "DEV_PERSIST"})
    assert code == 503 and response == {"ok": False, "mode": "paid", "reason": "state_error"}
    assert "lease" not in response and "master_key" not in response
    start = json.loads((tmp_path / "paid_starts.json").read_text(encoding="utf-8"))["persist-paid"]
    assert start == now

    monkeypatch.setattr(activation_server_module, "_atomic_write_json", original_write)
    monkeypatch.setattr(ac.time, "time", lambda: now + 600)
    code, response = svc.handle_activate({"token": token, "device": "DEV_PERSIST"})
    assert code == 200 and response["ok"]
    starts = json.loads((tmp_path / "paid_starts.json").read_text(encoding="utf-8"))
    assert starts["persist-paid"] == now


def test_trial_activation_fails_closed_if_start_cannot_persist(tmp_path, monkeypatch):
    svc = _mk_service(tmp_path)

    def fail_write(_path, _obj):
        raise OSError("synthetic trial write failure")

    monkeypatch.setattr(activation_server_module, "_atomic_write_json", fail_write)
    code, response = svc.handle_activate({"device": "DEV_TRIAL_FAIL"})
    assert code == 503 and response == {"ok": False, "mode": "trial", "reason": "state_error"}
    assert "lease" not in response and "master_key" not in response


def test_existing_trial_heartbeat_does_not_rewrite_unchanged_state(tmp_path, monkeypatch):
    svc = _mk_service(tmp_path)
    code, response = svc.handle_activate({"device": "DEV_TRIAL_HEARTBEAT"})
    assert code == 200 and response["ok"]

    writes: list[Path] = []

    def record_write(path, _obj):
        writes.append(Path(path))

    monkeypatch.setattr(activation_server_module, "_atomic_write_json", record_write)
    code, response = svc.handle_activate({"device": "DEV_TRIAL_HEARTBEAT"})
    assert code == 200 and response["ok"]
    assert writes == []


def test_activation_metrics_log_only_aggregate_fields(monkeypatch, capsys):
    monkeypatch.setattr(
        cs,
        "_ACT_METRICS",
        {
            "window_started": 10.0,
            "requests": 0,
            "decision_ok": 0,
            "decision_failed": 0,
            "response_write_ok": 0,
            "response_write_broken_pipe": 0,
            "response_write_failed": 0,
            "elapsed_total_ms": 0.0,
            "elapsed_max_ms": 0.0,
        },
    )
    cs._record_activation_metric(200, {"mode": "trial", "reason": "ok"}, 20.0, now=20.0)
    assert capsys.readouterr().out == ""

    cs._record_activation_metric(
        503,
        {"mode": "trial", "reason": "state_error"},
        1700.0,
        response_write="broken_pipe",
        now=71.0,
    )
    output = capsys.readouterr().out
    assert "[activation-alert] code=503 mode=trial reason=state_error elapsed_ms=1700.0" in output
    assert "[activation-summary]" in output
    assert "requests=2 decision_ok=1 decision_failed=1" in output
    assert "response_write_ok=1 response_write_broken_pipe=1 response_write_failed=0" in output
    assert " device=" not in output and " token=" not in output and " ip=" not in output


def test_activation_metric_separates_successful_decision_from_broken_response_write(monkeypatch):
    handler = object.__new__(cs.Handler)
    handler.headers = {"Content-Length": "2"}
    handler.rfile = io.BytesIO(b"{}")
    handler._client_ip = lambda: "127.0.0.1"
    handler._send = lambda _code, _response: False

    monkeypatch.setattr(cs, "_rate_ok", lambda _ip: True)
    monkeypatch.setattr(cs, "_ACT", object())
    monkeypatch.setattr(
        cs,
        "_handle_activation_body",
        lambda _body: (200, {"ok": True, "mode": "paid", "reason": "ok"}),
    )
    recorded = []
    monkeypatch.setattr(
        cs,
        "_record_activation_metric",
        lambda code, response, elapsed_ms, **kwargs: recorded.append(
            (code, response, elapsed_ms, kwargs)
        ),
    )

    assert handler._do_activation("/activate") is False
    assert len(recorded) == 1
    code, response, elapsed_ms, kwargs = recorded[0]
    assert code == 200
    assert response == {"ok": True, "mode": "paid", "reason": "ok"}
    assert elapsed_ms >= 0.0
    assert kwargs == {
        "response_write": "broken_pipe",
        "_projection": ("paid", "ok"),
    }


def test_activation_metric_records_unexpected_response_write_failure(monkeypatch):
    handler = object.__new__(cs.Handler)
    handler.headers = {"Content-Length": "2"}
    handler.rfile = io.BytesIO(b"{}")
    handler._client_ip = lambda: "127.0.0.1"

    def fail_send(_code, _response):
        raise OSError("private response path canary")

    handler._send = fail_send
    monkeypatch.setattr(cs, "_rate_ok", lambda _ip: True)
    monkeypatch.setattr(cs, "_ACT", object())
    monkeypatch.setattr(
        cs,
        "_handle_activation_body",
        lambda _body: (200, {"ok": True, "mode": "paid", "reason": "ok"}),
    )
    recorded = []
    monkeypatch.setattr(
        cs,
        "_record_activation_metric",
        lambda code, response, elapsed_ms, **kwargs: recorded.append(
            (code, response, elapsed_ms, kwargs)
        ),
    )

    with pytest.raises(OSError, match="private response path canary"):
        handler._do_activation("/activate")
    assert len(recorded) == 1
    assert recorded[0][0] == 200
    assert recorded[0][3] == {
        "response_write": "failed",
        "_projection": ("paid", "ok"),
    }


def test_activation_server_redacts_unexpected_handler_error(monkeypatch, capfd):
    private_canary = r"PRIVATE_PATH_CANARY C:\Users\example\activation"
    handler_error = threading.Event()
    recorded = []

    class HeaderFailureHandler(cs.Handler):
        def end_headers(self):
            raise OSError(private_canary)

    class RecordingServer(cs.SafeThreadingHTTPServer):
        def handle_error(self, request, client_address):
            super().handle_error(request, client_address)
            handler_error.set()

    monkeypatch.setattr(cs, "_rate_ok", lambda _ip: True)
    monkeypatch.setattr(cs, "_ACT", object())
    monkeypatch.setattr(
        cs,
        "_handle_activation_body",
        lambda _body: (200, {"ok": True, "mode": "paid", "reason": "ok"}),
    )
    monkeypatch.setattr(
        cs,
        "_record_activation_metric",
        lambda code, response, elapsed_ms, **kwargs: recorded.append(
            (code, response, elapsed_ms, kwargs)
        ),
    )

    server = RecordingServer(("127.0.0.1", 0), HeaderFailureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        server.server_address[1],
        timeout=5,
    )
    try:
        connection.request(
            "POST",
            "/activate",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises((http.client.RemoteDisconnected, ConnectionError, OSError)):
            connection.getresponse()
        assert handler_error.wait(5)
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert len(recorded) == 1
    assert recorded[0][0] == 200
    assert recorded[0][3] == {
        "response_write": "failed",
        "_projection": ("paid", "ok"),
    }
    captured = capfd.readouterr()
    combined = captured.out + captured.err
    assert "[http-handler-error] category=unexpected_handler_error" in combined
    assert private_canary not in combined
    assert "127.0.0.1" not in combined
    assert "Traceback" not in combined


def test_corrupt_trials_value_fails_closed_without_reset_or_disconnect(tmp_path):
    """损坏的已有试用状态必须503且原样保留，不能静默重置为一份新额度。"""
    svc = _mk_service(tmp_path)
    path = tmp_path / "trials.json"
    original = json.dumps({"DEV_CORRUPT": "abc"})
    path.write_text(original, encoding="utf-8")
    code, resp = svc.handle_activate({"device": "DEV_CORRUPT"})
    assert code == 503
    assert resp == {"ok": False, "mode": "trial", "reason": "state_error"}
    assert path.read_text(encoding="utf-8") == original


def test_existing_malformed_paid_state_is_not_replaced(tmp_path):
    """已有安全状态损坏时必须停服待修，不能回退空字典并延长会员。"""
    svc = _mk_service(tmp_path)
    p = tmp_path / "paid_starts.json"
    original = '{"m1": 1700000000, "m2":'
    p.write_text(original, encoding="utf-8")
    token = ac.mint_token("m2", _SECRET)
    code, response = svc.handle_activate({"token": token, "device": "DEV_M2"})
    assert code == 503
    assert response == {"ok": False, "mode": "paid", "reason": "state_error"}
    assert p.read_text(encoding="utf-8") == original
    assert [q.name for q in tmp_path.iterdir() if ".tmp-" in q.name] == []
