# -*- coding: utf-8 -*-
"""审计 0710 服务端加固/提速回归门:
- received_ts 服务器无条件盖章(白名单+setdefault 曾允许客户端伪造收样时间)
- version_manifest mtime 热重载(改文件即生效, 坏 JSON 保留旧值)
- dashboard payload JSON script-tag 嵌入往返(含 </script>/引号/反斜杠/U+2028 等险字符)
- HTTP 层: /dashboard ETag→304、/export gzip、/healthz 结果缓存
"""
from __future__ import annotations

import gzip
import http.client
import json
import ssl
import sys
import threading
import time
import urllib.request
import pytest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sample_collector_server as cs  # noqa: E402
import sample_dashboard as sd  # noqa: E402


def _fresh_state(monkeypatch, tmp_path: Path) -> Path:
    out = tmp_path / "samples.jsonl"
    monkeypatch.setattr(cs, "_OUT", out)
    monkeypatch.setattr(cs, "_SEEN", set())
    monkeypatch.setattr(cs, "_QUAR_SEEN", set())
    monkeypatch.setattr(cs, "_MAP_COUNT", type(cs._MAP_COUNT)())
    monkeypatch.setattr(cs, "_DASH_CACHE", {
        "key": None, "plain": None, "gz": None, "etag": None, "built_mono": 0.0,
    })
    monkeypatch.setattr(cs, "_DASH_DETAIL_CACHE", {})
    monkeypatch.setattr(cs, "DASH_REBUILD_INTERVAL", 0.0)
    monkeypatch.setattr(cs, "_HEALTH_CACHE", {"ts": 0.0, "code": 200, "payload": None})
    return out


def test_received_ts_is_server_stamped_not_client_forgeable(monkeypatch, tmp_path):
    out = _fresh_state(monkeypatch, tmp_path)
    rec = {"map_id": 2406, "final_quality_counts": "q1=1;q6=0", "known_value_sum": 100,
           "session_hash": "h1", "received_ts": 1.0}   # 客户端伪造的收样时间
    assert cs._accept_one(rec) == "accepted"
    got = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert got["received_ts"] != 1.0                    # 伪造值必须被覆盖
    assert abs(got["received_ts"] - time.time()) < 60   # 服务器当前时间


def test_collector_startup_warning_is_gbk_safe():
    """Windows 默认 GBK 重定向 stdout 时，banner 不能在监听端口前先崩。"""
    with pytest.raises(UnicodeEncodeError):
        "⚠️OFF".encode("gbk")  # 检测器正例：确认当前 codec 真能抓到原失败签名
    warning = cs._ingest_auth_status("")
    assert warning.startswith("[WARN] OFF")
    assert warning.encode("gbk")
    assert cs._ingest_auth_status("configured") == "ON(HMAC/ApiKey)"


def test_version_manifest_hot_reloads_and_keeps_last_good(monkeypatch, tmp_path):
    vm = tmp_path / "version_manifest.json"
    monkeypatch.setattr(cs, "_VM_PATH", vm)
    monkeypatch.setattr(cs, "_VM_CACHE", {"key": None, "data": {}})
    # 文件不存在 -> 空清单(不下发)
    assert cs._public_version_payload()["latest"] is None
    vm.write_text(json.dumps({"latest": "0.2.6-hotfix1"}), encoding="utf-8")
    assert cs._public_version_payload()["latest"] == "0.2.6-hotfix1"
    # 改文件【不重启】即生效
    vm.write_text(json.dumps({"latest": "0.2.7", "min": "0.2.0"}), encoding="utf-8")
    assert cs._public_version_payload()["latest"] == "0.2.7"
    # 坏 JSON -> 保留上一份好的, 不回空
    vm.write_text("{broken", encoding="utf-8")
    assert cs._public_version_payload()["latest"] == "0.2.7"


def test_dashboard_json_scripttag_roundtrips_hostile_strings():
    nasty = 'we"ird\\na\'me</script><b> end'
    sample = {"map_id": 2406, "final_quality_counts": "q1=1", "known_value_sum": 5,
              "player": nasty, "session_hash": "h2"}
    html = sd.build_dashboard_html([sample])
    marker = '<script type="application/json" id="__P__">'
    i = html.index(marker) + len(marker)
    j = html.index("</script>", i)   # payload 内的 "</" 已转义为 <\/ , 第一个 </script> 即结束标签
    payload = json.loads(html[i:j])  # \/ 是合法 JSON 转义 -> 还原为 /
    assert payload["rows"][0]["player"] == nasty
    # 前端消费方式同步存在(JSON.parse + textContent)
    assert 'JSON.parse(document.getElementById("__P__").textContent)' in html


def test_dashboard_profit_keeps_direct_welfare_and_winner_dividend_contract():
    sample = {
        "map_id": 5601,
        "final_quality_counts": "q1=1",
        "known_value_sum": 1_000,
        "player": "me",
        "players": ["me", "other"],
        "winner": "other",
        "winner_bid": 1_200,
        "settlement_welfare": 55,
        "session_hash": "profit-contract",
    }
    row = sd._rows_for_client([sample])[0]
    packed = sd._pack_rows_for_client([row])
    html = sd.build_dashboard_html([sample])

    assert row["welfare"] == 55
    assert packed["rows"][0][16] == 55
    assert "lot-r.wbid+dividend-fee" in html
    assert 'p===r.player && typeof r.welfare==="number"' in html
    assert 'source:"settlement_direct"' in html


def test_server_dashboard_compacts_core_and_lazy_loads_heavy_details():
    sample = {
        "map_id": 2406, "hero": "aisha", "final_quality_counts": "q1=1;q6=1",
        "final_quality_cells": "q1=2;q6=5", "known_value_sum": 5,
        "player": "me", "players": ["me", "other"], "winner": "me",
        "rare_items": [{"n": "大型稀有藏品", "q": 6, "v": 123456}],
        "tools": ["透视"], "hskills": ["技能"],
        "player_ranks": {"other": {"p": 1234, "t": "黄金"}},
        "session_hash": "compact-1",
    }
    urls = {"rare": "/dashboard-data?kind=rare", "tools": "/dashboard-data?kind=tools",
            "player_ranks": "/dashboard-data?kind=player_ranks"}
    full = sd.build_dashboard_html([sample])
    compact = sd.build_dashboard_html([sample], detail_urls=urls)
    marker = '<script type="application/json" id="__P__">'
    start = compact.index(marker) + len(marker)
    payload = json.loads(compact[start:compact.index("</script>", start)])

    assert payload["row_format"] == "compact-v1" and isinstance(payload["rows"][0], list)
    assert payload["detail_urls"] == urls
    assert "大型稀有藏品" in full and "大型稀有藏品" not in compact
    assert sd.dashboard_detail_payload([sample], "rare")["items"] == [
        [0, [{"n": "大型稀有藏品", "q": 6, "v": 123456}]],
    ]
    assert sd.dashboard_detail_payload([sample], "tools")["items"] == [[0, ["透视"], ["技能"]]]
    assert sd.dashboard_detail_payload([sample], "player_ranks")["items"][0][0] == 0
    assert "function hydrateRows" in compact and "async function ensureDetail" in compact
    assert 'v==="prop"?"rare"' in compact


def test_compact_dashboard_payload_has_meaningful_size_gate():
    samples = [{
        "map_id": 2406, "hero": "aisha", "final_quality_counts": "q1=9;q6=2",
        "final_quality_cells": "q1=18;q6=12", "final_quality_values": "q1=9;q6=999999",
        "known_value_sum": 999999, "player": f"player-{i}",
        "players": [f"player-{i}", f"opponent-{i}"], "winner": f"player-{i}",
        "rare_items": [{"n": "稀有藏品名称" * 8, "q": 6, "v": 999999}] * 4,
        "player_ranks": {f"opponent-{i}": {"p": 1234, "t": "黄金"}},
        "session_hash": f"size-{i}",
    } for i in range(120)]
    urls = {"rare": "/dashboard-data?kind=rare", "tools": "/dashboard-data?kind=tools",
            "player_ranks": "/dashboard-data?kind=player_ranks"}
    full = sd.build_dashboard_html(samples).encode("utf-8")
    compact = sd.build_dashboard_html(samples, detail_urls=urls).encode("utf-8")
    assert len(compact) < len(full) * 0.55


def test_dashboard_build_scans_replayable_source_once_and_preserves_diagnostics():
    samples = [
        {
            "map_id": 2406,
            "hero": "aisha",
            "final_quality_counts": "q1=1;q10=2",
            "final_quality_cells": "q1=2;q10=7",
            "final_quality_values": "q1=5;q10=99",
            "known_value_sum": 104,
            "player": "me",
            "session_hash": "single-pass-1",
            "client_ts": 100.0,
        },
        {
            "map_id": 2501,
            "hero": "raven",
            "final_quality_counts": "q1=2;q6=1",
            "known_value_sum": 500,
            "player": "me",
            "session_hash": "single-pass-2",
            "client_ts": 200.0,
        },
    ]
    expected_rows = sd._pack_rows_for_client(
        sd._iter_rows_for_client(samples, include_details=False)
    )
    expected_warnings = sd._scan_unknown_qualities(samples)
    expected_anomalies = sd._collect_anomaly_samples(samples)

    class ReplayableSource:
        def __init__(self, rows):
            self.rows = rows
            self.passes = 0
            self.last_parsed_count = 0
            self.last_unknown_date_count = 0

        def __iter__(self):
            self.passes += 1
            for row in self.rows:
                yield row
            self.last_parsed_count = len(self.rows)

    source = ReplayableSource(samples)
    html = sd.build_dashboard_html(
        source,
        detail_urls={"rare": "/dashboard-data?kind=rare"},
        server_window={"mode": "test"},
    )
    marker = '<script type="application/json" id="__P__">'
    start = html.index(marker) + len(marker)
    payload = json.loads(html[start:html.index("</script>", start)])

    assert source.passes == 1
    for key, value in expected_rows.items():
        assert payload[key] == value
    assert payload["warn_quals"] == expected_warnings
    assert payload["anomaly_samples"] == expected_anomalies
    assert payload["server_window"]["parsed_rows"] == 2
    assert payload["server_window"]["matched_rows"] == 2


def test_dashboard_rebuild_is_throttled_but_expires(monkeypatch, tmp_path):
    """持续上传时服务旧缓存；超过窗口后必须纳入新样本，且未变数据不重复构建。"""
    out = _fresh_state(monkeypatch, tmp_path)
    monkeypatch.setattr(cs, "DASH_REBUILD_INTERVAL", 300.0)
    clock = [1_000.0]
    monkeypatch.setattr(cs.time, "monotonic", lambda: clock[0])
    out.write_text(json.dumps({
        "map_id": 2406, "final_quality_counts": "q1=1", "known_value_sum": 5,
        "session_hash": "throttle-1",
    }) + "\n", encoding="utf-8")

    real_build = sd.build_dashboard_html
    build_calls = []

    def counted_build(samples, **kwargs):
        build_calls.append(len(samples))
        return real_build(samples, **kwargs)

    monkeypatch.setattr(sd, "build_dashboard_html", counted_build)
    _, _, first_etag = cs._dashboard_bodies()
    assert build_calls == [1]

    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "map_id": 2501, "final_quality_counts": "q1=2", "known_value_sum": 7,
            "session_hash": "throttle-2",
        }) + "\n")
    clock[0] = 1_299.0
    _, _, stale_etag = cs._dashboard_bodies()
    assert stale_etag == first_etag and build_calls == [1]

    clock[0] = 1_301.0
    _, _, refreshed_etag = cs._dashboard_bodies()
    assert refreshed_etag != first_etag and build_calls == [1, 2]

    clock[0] = 2_000.0
    _, _, unchanged_etag = cs._dashboard_bodies()
    assert unchanged_etag == refreshed_etag and build_calls == [1, 2]


def test_dashboard_clicks_do_not_rebuild_unchanged_full_dataset_state():
    """主题/侧栏切换不能反复扫描全量样本并重建数千项筛选器。"""
    html = sd.build_dashboard_html([
        {"map_id": 2406, "hero": "aisha", "final_quality_counts": "q1=1;q6=1",
         "known_value_sum": 5, "player": "me", "session_hash": "perf-1"}
    ])
    cycle = html.split("function cycleTheme()", 1)[1].split("const QL", 1)[0]
    assert "render()" not in cycle
    assert 'const QC = {q1:"var(--q1)"' in html
    assert "const MAPS_BY_FAMILY = new Map()" in html
    assert "let FILTER_CACHE = {key:null" in html
    assert "if(LAST_FILTER_UI_KEY===uiKey) return" in html
    assert "const VIEW_CACHE = new Map()" in html
    assert "VIEW_CACHE_LIMIT = 10" in html
    assert "if(VIEW_CACHE.size>VIEW_CACHE_LIMIT)" in html
    assert "cacheKey=JSON.stringify(F)" in html
    assert "invalidateViewCache(); render()" in html
    assert 'node=document.createElement("div")' in html
    assert "p.hidden=p!==node" in html
    assert "host.dataset.renderMs" in html and "host.dataset.cacheHit" in html
    assert 'document.getElementById("content").innerHTML=html' not in html
    overview = html.split("function vOverview()", 1)[1].split("function vSnapshot()", 1)[0]
    assert "rs.filter" not in overview, "总览仍在每张地图上重复全量扫描"
    assert "个人时间记录" in html and "singleLineChart" in html


def test_dashboard_calendar_is_fixed_to_shanghai_not_browser_locale():
    html = sd.build_dashboard_html([{
        "map_id": 2406, "hero": "aisha", "client_ts": 1784159999,
        "final_quality_counts": "q1=1;q6=1", "known_value_sum": 5,
        "player": "me", "session_hash": "tz-boundary",
    }])
    assert "const SHANGHAI_OFFSET_MS = 8*60*60*1000" in html
    assert "getUTCFullYear()" in html and "getUTCHours()" in html and "getUTCDay()" in html
    assert "时间(北京)" in html and "北京时间口径" in html
    for local_api in ("getFullYear()", "getMonth()", "getDate()", "getDay()", "getHours()", "toLocaleString()"):
        assert local_api not in html, f"看板仍依赖访问者本地时区: {local_api}"


def test_dashboard_labels_legacy_missing_hero_as_liss():
    payload = sd._rows_for_client([
        {"map_id": 2406, "hero": None, "final_quality_counts": "q1=1;q6=1",
         "known_value_sum": 5, "session_hash": "liss-legacy"}
    ])
    assert payload[0]["hero"] == "liss"
    assert "莉丝" in sd.build_dashboard_html([])


def _start_server(monkeypatch, tmp_path):
    out = _fresh_state(monkeypatch, tmp_path)
    out.write_text(json.dumps({"map_id": 2406, "final_quality_counts": "q1=1",
                               "known_value_sum": 5, "session_hash": "h3",
                               "client_ts": time.time(),
                               "rare_items": [{"n": "rare-marker", "q": 6, "v": 99}]}) + "\n",
                   encoding="utf-8")
    monkeypatch.setattr(cs, "_DASH_PASS", "")
    monkeypatch.setattr(cs, "_DASH_ALLOW_ANON", True)   # loopback dev 语义
    monkeypatch.setattr(cs, "_EXPORT_KEY", "ek-test")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), cs.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}", out


def _get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:  # 304 走这里
        return e.code, dict(e.headers), b""


@pytest.mark.parametrize("path", ["/dashboard", "/public"])
def test_buffered_page_disconnect_does_not_attempt_a_second_500(monkeypatch, path):
    class DisconnectedWriter:
        def __init__(self):
            self.calls = 0

        def write(self, _body):
            self.calls += 1
            raise BrokenPipeError("synthetic client disconnect")

    writer = DisconnectedWriter()
    handler = object.__new__(cs.Handler)
    handler.path = path
    handler.headers = {}
    handler.wfile = writer
    handler.close_connection = False
    handler.send_response = lambda _code: None
    handler.send_header = lambda _key, _value: None
    handler.end_headers = lambda: None
    handler._require_dash_auth = lambda: True
    handler._client_ip = lambda: "127.0.0.1"
    monkeypatch.setattr(cs, "_rate_ok", lambda _ip: True)
    if path == "/dashboard":
        monkeypatch.setattr(
            cs,
            "_dashboard_bodies",
            lambda _window: (b"rendered-dashboard", b"gz", '"etag"'),
        )
    else:
        monkeypatch.setattr(
            cs,
            "_public_bodies",
            lambda _window: (b"rendered-public", b"gz", '"etag"'),
        )

    handler.do_GET()

    assert writer.calls == 1
    assert handler.close_connection is True


@pytest.mark.parametrize("winerror", [64, 109, 232, 10053, 10054])
def test_buffered_writer_recognizes_windows_disconnect_codes(winerror):
    class DisconnectedWriter:
        def write(self, _body):
            exc = OSError("private disconnect detail")
            exc.winerror = winerror
            raise exc

    handler = object.__new__(cs.Handler)
    handler.wfile = DisconnectedWriter()
    handler.close_connection = False

    assert handler._write_response_body(b"body") is False
    assert handler.close_connection is True


def test_buffered_writer_does_not_swallow_unrelated_oserror():
    class FailedWriter:
        def write(self, _body):
            exc = OSError("private access detail")
            exc.winerror = 5
            raise exc

    handler = object.__new__(cs.Handler)
    handler.wfile = FailedWriter()
    handler.close_connection = False

    with pytest.raises(OSError, match="private access detail"):
        handler._write_response_body(b"body")
    assert handler.close_connection is False


@pytest.mark.parametrize("path", ["/dashboard", "/public"])
@pytest.mark.parametrize("failure", ["winerror64", "ssl_eof"])
def test_http_buffered_page_disconnect_closes_without_second_response(
    monkeypatch,
    path,
    failure,
):
    """Exercise the real Handler/socket path after response headers were committed."""
    responses: list[int] = []
    body_writes: list[str] = []
    handler_errors: list[BaseException] = []
    finished = threading.Event()

    class DisconnectedBodyWriter:
        def __init__(self, raw):
            self.raw = raw

        def write(self, _body):
            body_writes.append(failure)
            if failure == "winerror64":
                exc = OSError("network name no longer available")
                exc.winerror = 64
                raise exc
            raise ssl.SSLEOFError(8, "EOF occurred in violation of protocol")

        def __getattr__(self, name):
            return getattr(self.raw, name)

    class DisconnectingHandler(cs.Handler):
        def send_response(self, code, message=None):
            responses.append(code)
            return super().send_response(code, message)

        def end_headers(self):
            super().end_headers()
            self.wfile = DisconnectedBodyWriter(self.wfile)

        def _require_dash_auth(self):
            return True

        def finish(self):
            try:
                super().finish()
            finally:
                finished.set()

    class RecordingServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            handler_errors.append(sys.exc_info()[1])

    monkeypatch.setattr(cs, "_rate_ok", lambda _ip: True)
    monkeypatch.setattr(
        cs,
        "_dashboard_bodies",
        lambda _window: (b"rendered-dashboard", b"gz", '"etag"'),
    )
    monkeypatch.setattr(
        cs,
        "_public_bodies",
        lambda _window: (b"rendered-public", b"gz", '"etag"'),
    )
    srv = RecordingServer(("127.0.0.1", 0), DisconnectingHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        assert response.status == 200
        with pytest.raises(http.client.IncompleteRead):
            response.read()
        assert finished.wait(5)
    finally:
        conn.close()
        srv.shutdown()
        srv.server_close()

    assert responses == [200]
    assert body_writes == [failure]
    assert handler_errors == []


def test_http_dashboard_etag_304_export_gzip_healthz_cache(monkeypatch, tmp_path):
    srv, base, out = _start_server(monkeypatch, tmp_path)
    try:
        # /dashboard: 首次 200 + ETag; 带 If-None-Match 重访 -> 304 零字节
        st, hd, body = _get(base + "/dashboard")
        assert st == 200 and hd.get("ETag") and body
        assert b"rare-marker" not in body, "首屏仍内嵌稀有藏品重字段"
        etag = hd["ETag"]
        st2, hd2, body2 = _get(base + "/dashboard", {"If-None-Match": etag})
        assert st2 == 304 and body2 == b""
        # 数据变了 -> etag 变 -> 200 重新给内容
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"map_id": 2501, "final_quality_counts": "q1=2",
                                "known_value_sum": 7, "session_hash": "h4"}) + "\n")
        st3, hd3, body3 = _get(base + "/dashboard", {"If-None-Match": etag})
        assert st3 == 200 and hd3.get("ETag") != etag and body3
        # 重字段独立鉴权端点按需 gzip；再次带 ETag 请求必须 304。
        std, hdd, detail_gz = _get(base + "/dashboard-data?kind=rare", {"Accept-Encoding": "gzip"})
        assert std == 200 and hdd.get("Content-Encoding") == "gzip" and hdd.get("ETag")
        detail = json.loads(gzip.decompress(detail_gz))
        assert detail["kind"] == "rare" and detail["items"][0][1][0]["n"] == "rare-marker"
        std2, _, detail2 = _get(base + "/dashboard-data?kind=rare", {"If-None-Match": hdd["ETag"]})
        assert std2 == 304 and detail2 == b""
        # /export: 无 Accept-Encoding -> 原始 ndjson; 带 gzip -> 压缩且解压等于原文件
        st4, hd4, plain = _get(base + "/export", {"X-Api-Key": "ek-test"})
        assert st4 == 200 and plain == out.read_bytes() and "Content-Encoding" not in hd4
        st5, hd5, gz = _get(base + "/export", {"X-Api-Key": "ek-test", "Accept-Encoding": "gzip"})
        assert st5 == 200 and hd5.get("Content-Encoding") == "gzip"
        assert gzip.decompress(gz) == out.read_bytes()
        # /healthz: 结果进缓存(30s 内第二次直接回缓存)
        st6, _, b6 = _get(base + "/healthz")
        assert st6 == 200 and json.loads(b6)["ok"] is True
        assert cs._HEALTH_CACHE["payload"] is not None
        st7, _, _ = _get(base + "/healthz")
        assert st7 == 200
    finally:
        srv.shutdown()
