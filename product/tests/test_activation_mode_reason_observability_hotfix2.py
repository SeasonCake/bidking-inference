from __future__ import annotations

import http.client
import io
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import sample_collector_server as collector  # noqa: E402


def _metric_snapshot() -> dict:
    with collector._ACT_METRICS_LOCK:
        return dict(collector._ACT_METRICS)


def _run_server(monkeypatch, service):
    monkeypatch.setattr(collector, "_ACT", service)
    monkeypatch.setattr(collector, "_rate_ok", lambda _ip: True)
    monkeypatch.setattr(collector.Handler, "log_message", lambda *_args: None)
    httpd = collector.SafeThreadingHTTPServer(("127.0.0.1", 0), collector.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _post(
    port: int,
    body: bytes,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        connection.request(
            "POST",
            "/activate",
            body=body,
            headers=request_headers,
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_fixed_mode_reason_summary_preserves_historical_prefix(monkeypatch, capsys):
    monkeypatch.setattr(
        collector,
        "_ACT_METRICS",
        collector._new_activation_metrics(window_started=10.0),
    )

    collector._record_activation_metric(
        200,
        {"ok": True, "mode": "paid"},
        20.0,
        now=20.0,
    )
    collector._record_activation_metric(
        503,
        {"ok": False, "mode": "trial", "reason": "state_error"},
        1700.0,
        response_write="broken_pipe",
        now=71.0,
    )

    output = capsys.readouterr().out
    assert (
        "[activation-summary] window_seconds=61.0 requests=2 "
        "decision_ok=1 decision_failed=1 response_write_ok=1 "
        "response_write_broken_pipe=1 response_write_failed=0 "
        "mean_ms=860.0 max_ms=1700.0"
    ) in output
    assert "code_2xx=1 code_4xx=0 code_5xx=1 code_other=0" in output
    assert "paid_requests=1 paid_decision_ok=1 paid_decision_failed=0" in output
    assert "trial_requests=1 trial_decision_ok=0 trial_decision_failed=1" in output
    assert "paid_mean_ms=20.0 paid_max_ms=20.0" in output
    assert "trial_mean_ms=1700.0 trial_max_ms=1700.0" in output
    assert "reason_ok=1" in output
    assert "reason_state_error=1" in output
    assert (
        "[activation-alert] code=503 mode=trial reason=state_error "
        "elapsed_ms=1700.0 code_family=5xx response_write=broken_pipe"
    ) in output
    summary_line = next(
        line for line in output.splitlines() if line.startswith("[activation-summary]")
    )
    actual_keys = [field.split("=", 1)[0] for field in summary_line.split()[1:]]
    expected_keys = [
        "window_seconds",
        "requests",
        "decision_ok",
        "decision_failed",
        "response_write_ok",
        "response_write_broken_pipe",
        "response_write_failed",
        "mean_ms",
        "max_ms",
        *(f"code_{family}" for family in collector._ACT_METRIC_CODE_FAMILIES),
    ]
    for mode in collector._ACT_METRIC_MODES:
        expected_keys.extend([
            f"{mode}_requests",
            f"{mode}_decision_ok",
            f"{mode}_decision_failed",
            f"{mode}_response_write_ok",
            f"{mode}_response_write_broken_pipe",
            f"{mode}_response_write_failed",
            f"{mode}_mean_ms",
            f"{mode}_max_ms",
        ])
    expected_keys.extend(
        f"reason_{reason}" for reason in collector._ACT_METRIC_REASONS
    )
    assert actual_keys == expected_keys


def test_unknown_values_and_string_subclasses_fold_without_magic(monkeypatch, capsys):
    private = "PRIVATE-token-device-uid-ip-url-header-payload"

    class ExplosiveStr(str):
        calls = 0

        def _explode(self, *_args, **_kwargs):
            type(self).calls += 1
            raise AssertionError("string protocol must not be called")

        __str__ = _explode
        __repr__ = _explode
        __eq__ = _explode
        __hash__ = _explode
        lower = _explode

    class ExplosiveDict(dict):
        calls = 0

        def _explode(self, *_args, **_kwargs):
            type(self).calls += 1
            raise AssertionError("mapping protocol must not be called")

        get = _explode
        __contains__ = _explode
        __iter__ = _explode
        __repr__ = _explode

    monkeypatch.setattr(
        collector,
        "_ACT_METRICS",
        collector._new_activation_metrics(window_started=10.0),
    )
    collector._record_activation_metric(
        599,
        {"mode": private, "reason": private},
        1.0,
        response_write=private,
        mode_hint=private,
        reason_hint=private,
        now=20.0,
    )
    collector._record_activation_metric(
        599,
        {"mode": ExplosiveStr(private), "reason": ExplosiveStr(private)},
        1.0,
        response_write=ExplosiveStr(private),
        mode_hint=ExplosiveStr(private),
        reason_hint=ExplosiveStr(private),
        now=71.0,
    )
    collector._record_activation_metric(
        599,
        ExplosiveDict({"mode": "paid", "reason": "ok"}),
        1.0,
        mode_hint="paid",
        reason_hint="ok",
        now=72.0,
    )

    output = capsys.readouterr().out
    assert private not in output
    assert ExplosiveStr.calls == 0
    assert ExplosiveDict.calls == 0
    assert output.count("mode=none reason=other") == 3
    assert "none_requests=2" in output
    assert "reason_other=2" in output
    assert "response_write_failed=2" in output
    metrics = _metric_snapshot()
    assert metrics["none_requests"] == 1
    assert metrics["reason_other"] == 1
    assert metrics["paid_requests"] == 0


@pytest.mark.parametrize("field", ("mode", "reason"))
def test_activation_wrapper_rejects_nonexact_raw_keys_without_magic(
    monkeypatch,
    field,
):
    class BombStr(str):
        calls = 0
        __hash__ = str.__hash__

        def __eq__(self, _other):
            type(self).calls += 1
            raise AssertionError("raw key equality must not run")

    payload = {"ok": True, "mode": "paid", "reason": "ok"}
    value = payload.pop(field)
    payload[BombStr(field)] = value
    monkeypatch.setattr(
        collector,
        "_ACT_METRICS",
        collector._new_activation_metrics(),
    )
    handler = object.__new__(collector.Handler)
    sends = []
    handler._send = lambda code, body: sends.append((code, body)) or True

    assert handler._send_activation_response(
        200,
        payload,
        started=0.0,
        mode_hint="paid",
        reason_hint="ok",
    ) is True
    assert len(sends) == 1
    assert BombStr.calls == 0
    metrics = _metric_snapshot()
    assert metrics["requests"] == 1
    assert metrics["none_requests"] == 1
    assert metrics["reason_other"] == 1
    assert metrics["paid_requests"] == 0


class _FixedActivationService:
    _RESPONSES = {
        "paid_ok": (200, {"ok": True, "mode": "paid"}),
        "trial_ok": (200, {"ok": True, "mode": "trial"}),
        "bad_token": (401, {"ok": False, "mode": "paid", "reason": "bad_token"}),
        "revoked": (403, {"ok": False, "mode": "paid", "reason": "revoked"}),
        "device_revoked": (
            403,
            {"ok": False, "mode": "paid", "reason": "device_revoked"},
        ),
        "device_mismatch": (
            403,
            {"ok": False, "mode": "paid", "reason": "device_mismatch"},
        ),
        "expired": (401, {"ok": False, "mode": "paid", "reason": "expired"}),
        "trial_expired": (
            403,
            {"ok": False, "mode": "trial", "reason": "trial_expired"},
        ),
        "state_error": (
            503,
            {"ok": False, "mode": "trial", "reason": "state_error"},
        ),
        "upgrade_required": (
            426,
            {"ok": False, "mode": "paid", "reason": "upgrade_required"},
        ),
        "security_key_unavailable": (
            503,
            {"ok": False, "mode": "paid", "reason": "security_key_unavailable"},
        ),
        "bad_request": (400, {"ok": False, "reason": "bad_request"}),
    }

    def handle_activate(self, body: dict):
        if body["case"] == "internal_error":
            raise RuntimeError(body["exception_canary"])
        return self._RESPONSES[body["case"]]


def test_real_http_records_paid_trial_and_fixed_failure_reasons(
    monkeypatch,
    capfd,
):
    private_token = "PRIVATE_TOKEN_CANARY_DO_NOT_LOG"
    private_device = "PRIVATE_DEVICE_CANARY_DO_NOT_LOG"
    private_uid = "PRIVATE_UID_CANARY_DO_NOT_LOG"
    private_url = "https://PRIVATE_URL_CANARY.invalid/activation"
    private_header = "PRIVATE_HEADER_CANARY_DO_NOT_LOG"
    private_exception = "PRIVATE_EXCEPTION_CANARY_DO_NOT_LOG"
    monkeypatch.setattr(
        collector,
        "_ACT_METRICS",
        collector._new_activation_metrics(),
    )
    httpd, thread = _run_server(monkeypatch, _FixedActivationService())
    try:
        cases = (*_FixedActivationService._RESPONSES, "internal_error")
        for case in cases:
            request = {
                "case": case,
                "device": private_device,
                "uid": private_uid,
                "url": private_url,
                "exception_canary": private_exception,
            }
            if case != "trial_ok" and not case.startswith("trial_") and case != "state_error":
                request["token"] = private_token
            expected_code, expected_payload = _FixedActivationService._RESPONSES.get(
                case,
                (503, {"ok": False, "mode": "paid", "reason": "state_error"}),
            )
            code, payload = _post(
                httpd.server_address[1],
                json.dumps(request).encode("utf-8"),
                headers={"X-Private-Canary": private_header},
            )
            assert code == expected_code
            assert payload == expected_payload
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    metrics = _metric_snapshot()
    assert metrics["requests"] == len(_FixedActivationService._RESPONSES) + 1
    assert metrics["paid_decision_ok"] == 1
    assert metrics["trial_decision_ok"] == 1
    assert metrics["paid_decision_failed"] == 9
    assert metrics["trial_decision_failed"] == 2
    for reason in (
        "bad_token",
        "revoked",
        "device_revoked",
        "device_mismatch",
        "expired",
        "trial_expired",
        "state_error",
        "upgrade_required",
        "security_key_unavailable",
        "bad_request",
    ):
        expected = 2 if reason == "state_error" else 1
        assert metrics[f"reason_{reason}"] == expected
    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert private_token not in output
    assert private_device not in output
    assert private_uid not in output
    assert private_url not in output
    assert private_header not in output
    assert private_exception not in output
    assert "device=" not in output
    assert "token=" not in output
    assert "uid=" not in output
    assert "ip=" not in output
    assert "url=" not in output
    assert "header=" not in output
    assert "payload=" not in output


@pytest.mark.parametrize(
    ("setup", "headers", "raw", "expected_code", "expected_reason"),
    (
        ("rate", {"Content-Length": "2"}, b"{}", 429, "rate_limited"),
        ("not_configured", {"Content-Length": "2"}, b"{}", 503, "activation_not_configured"),
        ("bad_length", {"Content-Length": "not-an-int"}, b"", 400, "bad_request"),
        ("too_large", {"Content-Length": str(collector._MAX_BODY + 1)}, b"", 413, "bad_request"),
        ("bad_json", {"Content-Length": "1"}, b"{", 400, "bad_request"),
        ("bad_body", {"Content-Length": "2"}, b"[]", 400, "bad_request"),
    ),
)
def test_early_activate_response_is_recorded_once(
    monkeypatch,
    setup,
    headers,
    raw,
    expected_code,
    expected_reason,
):
    handler = object.__new__(collector.Handler)
    handler.headers = headers
    handler.rfile = io.BytesIO(raw)
    handler._client_ip = lambda: "127.0.0.1"
    sent = []
    handler._send = lambda code, payload: sent.append((code, payload)) or True
    monkeypatch.setattr(
        collector,
        "_ACT_METRICS",
        collector._new_activation_metrics(),
    )
    monkeypatch.setattr(collector, "_ACT", None if setup == "not_configured" else object())
    monkeypatch.setattr(collector, "_rate_ok", lambda _ip: setup != "rate")

    assert handler._do_activation("/activate") is True
    assert len(sent) == 1
    assert sent[0][0] == expected_code
    metrics = _metric_snapshot()
    assert metrics["requests"] == 1
    assert metrics["decision_failed"] == 1
    assert metrics["none_requests"] == 1
    assert metrics["none_response_write_ok"] == 1
    assert metrics[f"reason_{expected_reason}"] == 1
    expected_family = "5xx" if 500 <= expected_code < 600 else "4xx"
    assert metrics[f"code_{expected_family}"] == 1


@pytest.mark.parametrize(
    ("send_result", "expected_write"),
    ((True, "ok"), (False, "broken_pipe")),
)
def test_activation_send_records_exactly_once_after_write_result(
    monkeypatch,
    send_result,
    expected_write,
):
    handler = object.__new__(collector.Handler)
    handler._send = lambda _code, _payload: send_result
    recorded = []
    monkeypatch.setattr(
        collector,
        "_record_activation_metric",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    assert handler._send_activation_response(
        200,
        {"ok": True, "mode": "paid"},
        started=0.0,
    ) is send_result
    assert len(recorded) == 1
    assert recorded[0][1] == {
        "response_write": expected_write,
        "_projection": ("paid", "ok"),
    }


def test_activation_send_failure_records_once_and_reraises(monkeypatch):
    handler = object.__new__(collector.Handler)

    def fail_send(_code, _payload):
        raise OSError("PRIVATE_WRITE_FAILURE_CANARY")

    handler._send = fail_send
    recorded = []
    monkeypatch.setattr(
        collector,
        "_record_activation_metric",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    with pytest.raises(OSError, match="PRIVATE_WRITE_FAILURE_CANARY"):
        handler._send_activation_response(
            503,
            {"ok": False, "mode": "trial", "reason": "state_error"},
            started=0.0,
        )
    assert len(recorded) == 1
    assert recorded[0][1] == {
        "response_write": "failed",
        "_projection": ("trial", "state_error"),
    }


def test_800_concurrent_updates_conserve_trigger_window_and_new_window(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        collector,
        "_ACT_METRICS",
        collector._new_activation_metrics(window_started=0.0),
    )

    def record_one(_index: int) -> None:
        collector._record_activation_metric(
            200,
            {"ok": True, "mode": "paid"},
            2.0,
            now=61.0,
        )

    with ThreadPoolExecutor(max_workers=32) as pool:
        list(pool.map(record_one, range(800)))

    output = capsys.readouterr().out
    assert output.count("[activation-summary]") == 1
    assert "requests=1 decision_ok=1 decision_failed=0" in output
    metrics = _metric_snapshot()
    assert metrics["requests"] == 799
    assert metrics["paid_requests"] == 799
    assert metrics["reason_ok"] == 799
    assert metrics["response_write_ok"] == 799


def test_new_metrics_are_process_local_and_start_empty():
    first = collector._new_activation_metrics(window_started=1.0)
    first["paid_requests"] = 7
    restarted = collector._new_activation_metrics(window_started=2.0)

    assert restarted["window_started"] == 2.0
    assert restarted["requests"] == 0
    assert restarted["paid_requests"] == 0
    assert restarted["reason_ok"] == 0
