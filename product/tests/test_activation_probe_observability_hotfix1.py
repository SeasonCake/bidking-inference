from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import activation_server  # noqa: E402
import sample_collector_server as cs  # noqa: E402


def _empty_probe_metrics(*, window_started: float) -> dict:
    return {
        "window_started": window_started,
        "healthz_requests": 0,
        "healthz_code_2xx": 0,
        "healthz_code_5xx": 0,
        "healthz_code_other": 0,
        "healthz_response_write_ok": 0,
        "healthz_response_write_broken_pipe": 0,
        "healthz_response_write_failed": 0,
        "activation_readiness_requests": 0,
        "activation_readiness_code_2xx": 0,
        "activation_readiness_code_5xx": 0,
        "activation_readiness_code_other": 0,
        "activation_readiness_response_write_ok": 0,
        "activation_readiness_response_write_broken_pipe": 0,
        "activation_readiness_response_write_failed": 0,
    }


def test_probe_summary_has_only_fixed_aggregate_fields(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cs,
        "_ACT_PROBE_METRICS",
        _empty_probe_metrics(window_started=10.0),
    )

    cs._record_activation_probe_metric(
        "healthz",
        200,
        20.0,
        response_write="ok",
        now=20.0,
    )
    assert capsys.readouterr().out == ""

    cs._record_activation_probe_metric(
        "activation_readiness",
        503,
        1700.0,
        response_write="broken_pipe",
        now=71.0,
    )
    output = capsys.readouterr().out
    assert (
        "[activation-probe-alert] route=activation_readiness code=503 "
        "response_write=broken_pipe elapsed_ms=1700.0"
    ) in output
    assert "[activation-probe-summary] window_seconds=61.0" in output
    assert (
        "healthz_requests=1 healthz_code_2xx=1 healthz_code_5xx=0 "
        "healthz_code_other=0"
    ) in output
    assert "healthz_response_write_ok=1" in output
    assert "activation_readiness_requests=1" in output
    assert "activation_readiness_code_2xx=0" in output
    assert "activation_readiness_code_5xx=1" in output
    assert "activation_readiness_code_other=0" in output
    assert "activation_readiness_response_write_broken_pipe=1" in output
    assert all(
        private not in output
        for private in ("token", "device", "ip=", "url=", "payload")
    )


def test_unknown_probe_route_is_redacted(monkeypatch, capsys) -> None:
    private_route = "private-device-token-url"
    monkeypatch.setattr(
        cs,
        "_ACT_PROBE_METRICS",
        _empty_probe_metrics(window_started=10.0),
    )

    cs._record_activation_probe_metric(
        private_route,
        418,
        123.0,
        response_write="not-private-response-detail",
        now=20.0,
    )

    output = capsys.readouterr().out
    assert output == (
        "[activation-probe-alert] route=unknown code_family=other "
        "response_write=failed elapsed_ms=0.0\n"
    )
    assert private_route not in output
    assert cs._ACT_PROBE_METRICS == _empty_probe_metrics(window_started=10.0)


@pytest.mark.parametrize(
    ("send_result", "expected_write"),
    ((True, "ok"), (False, "broken_pipe")),
)
def test_probe_send_records_response_delivery(
    monkeypatch,
    send_result: bool,
    expected_write: str,
) -> None:
    handler = object.__new__(cs.Handler)
    handler._send = lambda _code, _payload: send_result
    recorded = []
    monkeypatch.setattr(
        cs,
        "_record_activation_probe_metric",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    assert handler._send_activation_probe(
        "healthz",
        200,
        {"ok": True},
        started=time.monotonic(),
    ) is send_result
    assert len(recorded) == 1
    args, kwargs = recorded[0]
    assert args[0:2] == ("healthz", 200)
    assert args[2] >= 0.0
    assert kwargs == {"response_write": expected_write}


def test_probe_send_records_unexpected_write_failure(monkeypatch) -> None:
    handler = object.__new__(cs.Handler)

    def fail_send(_code, _payload):
        raise OSError("private response detail")

    handler._send = fail_send
    recorded = []
    monkeypatch.setattr(
        cs,
        "_record_activation_probe_metric",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    with pytest.raises(OSError, match="private response detail"):
        handler._send_activation_probe(
            "activation_readiness",
            503,
            {"ok": False},
            started=time.monotonic(),
        )
    assert len(recorded) == 1
    assert recorded[0][0][0:2] == ("activation_readiness", 503)
    assert recorded[0][1] == {"response_write": "failed"}


def test_healthz_cached_path_uses_probe_observer(monkeypatch) -> None:
    handler = object.__new__(cs.Handler)
    handler.path = "/healthz"
    observed = []
    handler._send_activation_probe = lambda *args, **kwargs: observed.append(
        (args, kwargs)
    )
    monkeypatch.setattr(
        cs,
        "_HEALTH_CACHE",
        {"ts": time.time(), "code": 200, "payload": {"ok": True}},
    )

    handler.do_GET()

    assert len(observed) == 1
    assert observed[0][0] == ("healthz", 200, {"ok": True})
    assert observed[0][1]["started"] <= time.monotonic()


def test_activation_readiness_path_uses_probe_observer(monkeypatch) -> None:
    handler = object.__new__(cs.Handler)
    handler.path = "/activation-readiness"
    observed = []
    handler._send_activation_probe = lambda *args, **kwargs: observed.append(
        (args, kwargs)
    )
    monkeypatch.setattr(
        activation_server,
        "activation_readiness",
        lambda _service: (503, {"ok": False, "reason": "state_error"}),
    )

    handler.do_GET()

    assert len(observed) == 1
    assert observed[0][0] == (
        "activation_readiness",
        503,
        {"ok": False, "reason": "state_error"},
    )
    assert observed[0][1]["started"] <= time.monotonic()
