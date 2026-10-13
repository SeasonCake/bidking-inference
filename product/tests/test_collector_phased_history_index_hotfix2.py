from __future__ import annotations

import http.client
import json
import sys
import threading
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sample_collector_server as collector  # noqa: E402


def _sample(session_hash: str = "phase-session") -> dict:
    return {
        "map_id": 2406,
        "hero": "aisha",
        "session_hash": session_hash,
        "final_quality_counts": "q1=1;q6=1",
        "final_quality_cells": "q1=2;q6=6",
        "known_value_sum": 1000,
    }


@pytest.fixture(autouse=True)
def _fresh_process_state(monkeypatch, tmp_path):
    done = threading.Event()
    done.set()
    monkeypatch.setattr(collector, "_HISTORY_INDEX_STATE", "ready")
    monkeypatch.setattr(collector, "_HISTORY_INDEX_DONE", done)
    monkeypatch.setattr(collector, "_HISTORY_INDEX_THREAD", None)
    monkeypatch.setattr(collector, "_SEEN", set())
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    monkeypatch.setattr(collector, "_MAP_COUNT", Counter())
    monkeypatch.setattr(collector, "_OUT", tmp_path / "samples.jsonl")
    monkeypatch.setattr(collector, "_KEY", "")
    monkeypatch.setattr(collector, "_UPLOAD_IDENTITY_MODE", "disabled")
    monkeypatch.setattr(collector, "_UPLOAD_IDENTITY_STORE", None)
    monkeypatch.setattr(collector, "_ACT", None)
    monkeypatch.setattr(collector, "_DASH_PASS", "")
    monkeypatch.setattr(collector, "_DASH_ALLOW_ANON", True)
    monkeypatch.setattr(collector, "_EXPORT_KEY", "export-test")
    monkeypatch.setattr(
        collector,
        "_HEALTH_CACHE",
        {"ts": 0.0, "code": 200, "payload": None},
    )


def _request(
    server,
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], dict]:
    raw = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = dict(headers or {})
    if raw is not None:
        request_headers["Content-Type"] = "application/json"
        request_headers["Content-Length"] = str(len(raw))
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=5,
    )
    try:
        connection.request(method, path, body=raw, headers=request_headers)
        response = connection.getresponse()
        body = response.read()
        return response.status, dict(response.getheaders()), json.loads(body or b"{}")
    finally:
        connection.close()


def _start_http_server():
    server = collector.SafeThreadingHTTPServer(("127.0.0.1", 0), collector.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_background_load_keeps_old_complete_index_until_atomic_publish(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    warm_states: list[str] = []
    monkeypatch.setattr(collector, "_SEEN", {"old-main"})
    monkeypatch.setattr(collector, "_QUAR_SEEN", {"old-quarantine"})
    monkeypatch.setattr(collector, "_MAP_COUNT", Counter({1: 1}))

    def build_index():
        entered.set()
        assert release.wait(timeout=3)
        return {"new-main"}, {"new-quarantine"}, Counter({2406: 1})

    monkeypatch.setattr(collector, "_build_existing_index", build_index)
    monkeypatch.setattr(
        collector,
        "_warm_public_cache",
        lambda: warm_states.append(collector._history_index_state()),
    )

    thread = collector._start_history_index_load()
    assert entered.wait(timeout=2)
    assert collector._history_index_state() == "loading"
    assert collector._SEEN == {"old-main"}
    assert collector._QUAR_SEEN == {"old-quarantine"}
    assert collector._MAP_COUNT == {1: 1}

    release.set()
    assert collector._HISTORY_INDEX_DONE.wait(timeout=3)
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert collector._history_index_state() == "ready"
    assert collector._SEEN == {"new-main"}
    assert collector._QUAR_SEEN == {"new-quarantine"}
    assert collector._MAP_COUNT == {2406: 1}
    assert warm_states == ["ready"]


def test_background_load_failure_keeps_data_routes_closed_and_skips_warmup(monkeypatch):
    warm_calls: list[bool] = []
    monkeypatch.setattr(collector, "_SEEN", {"last-complete"})

    def fail_load():
        raise OSError("private path must not enter the response")

    monkeypatch.setattr(collector, "_load_existing", fail_load)
    monkeypatch.setattr(
        collector, "_warm_public_cache", lambda: warm_calls.append(True),
    )

    thread = collector._start_history_index_load()
    assert collector._HISTORY_INDEX_DONE.wait(timeout=3)
    thread.join(timeout=3)
    assert collector._history_index_state() == "failed"
    assert collector._SEEN == {"last-complete"}
    assert warm_calls == []

    server, server_thread = _start_http_server()
    try:
        status, _, payload = _request(server, "GET", "/healthz")
        assert status == 503
        assert payload == {
            "ok": False,
            "reason": "history_index_unavailable",
            "state": "failed",
        }
        status, _, payload = _request(server, "GET", "/public")
        assert status == 503
        assert payload["state"] == "failed"
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)


def test_loading_phase_serves_activation_but_blocks_health_and_sample_routes(monkeypatch):
    class ActivationStub:
        def handle_activate(self, _body):
            return 200, {"ok": True, "mode": "trial"}

    monkeypatch.setattr(collector, "_HISTORY_INDEX_STATE", "loading")
    monkeypatch.setattr(collector, "_ACT", ActivationStub())
    server, thread = _start_http_server()
    try:
        status, _, payload = _request(server, "GET", "/version")
        assert status == 200 and set(payload) == {"latest", "min", "url", "notice", "game_version"}

        status, headers, payload = _request(server, "GET", "/healthz")
        assert status == 503
        assert payload == {
            "ok": False,
            "reason": "history_index_unavailable",
            "state": "loading",
        }

        status, _, payload = _request(server, "POST", "/activate", payload={})
        assert status == 200 and payload == {"ok": True, "mode": "trial"}

        status, headers, payload = _request(server, "GET", "/public")
        assert status == 503
        assert headers["Retry-After"] == str(collector._HISTORY_INDEX_RETRY_AFTER_SECONDS)
        assert payload == {
            "ok": False,
            "error": "history index unavailable",
            "state": "loading",
        }

        status, _, payload = _request(server, "POST", "/ingest", payload=_sample())
        assert status == 503
        assert payload == {"error": "history_index_unavailable", "state": "loading"}
        assert not collector._OUT.exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_ready_transition_enables_health_and_exact_duplicate_check(monkeypatch):
    record = _sample("already-present")
    collector._OUT.write_text(json.dumps(record) + "\n", encoding="utf-8")
    monkeypatch.setattr(collector, "_SEEN", {"already-present"})
    monkeypatch.setattr(collector, "_MAP_COUNT", Counter({2406: 1}))
    monkeypatch.setattr(collector, "_HISTORY_INDEX_STATE", "ready")
    server, thread = _start_http_server()
    try:
        status, _, payload = _request(server, "GET", "/healthz")
        assert status == 200 and payload == {"ok": True}

        status, _, payload = _request(server, "POST", "/ingest", payload=record)
        assert status == 200
        assert payload == {
            "accepted": 0,
            "duplicate": 1,
            "quarantined": 0,
            "rejected": 0,
        }
        assert collector._OUT.read_text(encoding="utf-8").count("\n") == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_main_binds_before_starting_background_history_load(monkeypatch, tmp_path):
    events: list[str] = []

    class ServerStub:
        def __init__(self, *_args, **_kwargs):
            events.append("bind")

        def serve_forever(self):
            events.append("serve")

    class ThreadStub:
        pass

    def start_history():
        events.append("start-history")
        return ThreadStub()

    monkeypatch.setattr(collector, "SafeThreadingHTTPServer", ServerStub)
    monkeypatch.setattr(collector, "_start_history_index_load", start_history)
    monkeypatch.setattr(collector, "_ACT", None)
    missing = tmp_path / "missing"
    result = collector.main([
        "sample_collector_server.py",
        "--host", "127.0.0.1",
        "--port", "0",
        "--out", str(tmp_path / "samples.jsonl"),
        "--dashboard-pass-file", str(missing / "dashboard"),
        "--export-key-file", str(missing / "export"),
        "--version-manifest-file", str(missing / "version"),
        "--activation-key-file", str(missing / "activation"),
        "--master-key-file", str(missing / "master"),
        "--master-key-v2-file", str(missing / "master-v2"),
    ])

    assert result == 0
    assert events == ["bind", "start-history", "serve"]
