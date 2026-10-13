from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import sample_collector_server as collector  # noqa: E402


def _metric_snapshot() -> dict:
    with collector._UPLOAD_METRICS_LOCK:
        return dict(collector._UPLOAD_METRICS)


def _empty_records(**updates: int) -> dict[str, int]:
    result = {
        "accepted": 0,
        "quarantined": 0,
        "duplicate": 0,
        "rejected": 0,
    }
    result.update(updates)
    return result


def test_upload_rollout_metric_schema_exists_and_starts_empty() -> None:
    metrics = collector._new_upload_identity_metrics(window_started=10.0)
    assert metrics["window_started"] == 10.0
    assert metrics["requests"] == 0
    assert metrics["identity_trusted"] == 0
    assert metrics["identity_legacy"] == 0
    assert metrics["identity_none"] == 0


def test_fixed_summary_reports_rollout_mix_dispositions_and_latency(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        collector,
        "_UPLOAD_METRICS",
        collector._new_upload_identity_metrics(window_started=10.0),
    )
    collector._record_upload_identity_metric(
        200,
        50.0,
        identity="trusted",
        reason="ok",
        records=_empty_records(accepted=2, duplicate=1),
        now=20.0,
    )
    collector._record_upload_identity_metric(
        200,
        200.0,
        identity="legacy",
        reason="ok",
        records=_empty_records(accepted=1, quarantined=1),
        now=30.0,
    )
    collector._record_upload_identity_metric(
        401,
        600.0,
        identity="none",
        reason="identity_required",
        now=40.0,
    )
    collector._record_upload_identity_metric(
        503,
        1600.0,
        identity="none",
        reason="identity_state_error",
        response_write="broken_pipe",
        now=71.0,
    )

    output = capsys.readouterr().out
    line = next(
        row for row in output.splitlines()
        if row.startswith("[upload-identity-summary]")
    )
    assert "window_seconds=61.0 requests=4" in line
    assert "identity_trusted=1 identity_legacy=1 identity_none=2" in line
    assert "code_2xx=2 code_4xx=1 code_5xx=1 code_other=0" in line
    assert (
        "response_write_ok=3 response_write_broken_pipe=1 "
        "response_write_failed=0"
    ) in line
    assert (
        "latency_lt_100ms=1 latency_100_to_500ms=1 "
        "latency_500_to_1500ms=1 latency_ge_1500ms=1"
    ) in line
    assert (
        "records_accepted=3 records_quarantined=1 records_duplicate=1 "
        "records_rejected=0"
    ) in line
    assert "reason_ok=2" in line
    assert "reason_identity_required=1" in line
    assert "reason_identity_state_error=1" in line
    actual_keys = [field.split("=", 1)[0] for field in line.split()[1:]]
    assert actual_keys == [
        "window_seconds",
        "requests",
        *(f"identity_{value}" for value in collector._UPLOAD_METRIC_IDENTITIES),
        *(f"code_{value}" for value in collector._UPLOAD_METRIC_CODE_FAMILIES),
        *(
            f"response_write_{value}"
            for value in collector._UPLOAD_METRIC_RESPONSE_WRITES
        ),
        *(f"latency_{value}" for value in collector._UPLOAD_METRIC_LATENCY_BUCKETS),
        *(f"records_{value}" for value in collector._UPLOAD_METRIC_DISPOSITIONS),
        *(f"reason_{value}" for value in collector._UPLOAD_METRIC_REASONS),
    ]


def test_private_or_malicious_values_fold_to_none_other_without_magic(
    monkeypatch,
    capsys,
) -> None:
    private = "PRIVATE-IP-URL-HEADER-TOKEN-LEASE-NONCE-DEVICE-UID-SUBJECT-SESSION-BODY"

    class BombStr(str):
        calls = 0

        def explode(self, *_args, **_kwargs):
            type(self).calls += 1
            raise AssertionError("private string protocol must not run")

        __hash__ = explode
        __eq__ = explode
        __str__ = explode
        __repr__ = explode

    class BombDict(dict):
        calls = 0

        def explode(self, *_args, **_kwargs):
            type(self).calls += 1
            raise AssertionError("private mapping protocol must not run")

        __iter__ = explode
        __contains__ = explode
        get = explode
        keys = explode
        __repr__ = explode

    monkeypatch.setattr(
        collector,
        "_UPLOAD_METRICS",
        collector._new_upload_identity_metrics(window_started=10.0),
    )
    collector._record_upload_identity_metric(
        399,
        1.0,
        identity=BombStr(private),
        reason=BombStr(private),
        response_write=BombStr(private),
        records=BombDict(_empty_records(accepted=99)),
        now=71.0,
    )

    output = capsys.readouterr().out
    assert private not in output
    assert BombStr.calls == 0
    assert BombDict.calls == 0
    assert "identity_trusted=0 identity_legacy=0 identity_none=1" in output
    assert "code_other=1" in output
    assert "response_write_failed=1" in output
    assert "records_accepted=0" in output
    assert "reason_other=1" in output


def test_invalid_record_projection_cannot_establish_trusted_metric(monkeypatch) -> None:
    monkeypatch.setattr(
        collector,
        "_UPLOAD_METRICS",
        collector._new_upload_identity_metrics(window_started=10.0),
    )
    collector._record_upload_identity_metric(
        200,
        1.0,
        identity="trusted",
        reason="ok",
        records={"accepted": True},
        now=20.0,
    )
    metrics = _metric_snapshot()
    assert metrics["identity_trusted"] == 0
    assert metrics["identity_none"] == 1
    assert metrics["reason_ok"] == 0
    assert metrics["reason_other"] == 1


def test_authority_identity_projection_is_exact_and_fail_closed() -> None:
    assert collector._upload_metric_identity_from_authority(
        collector.UploadIdentity("server_signed")
    ) == "trusted"
    assert collector._upload_metric_identity_from_authority(
        collector.UploadIdentity("legacy_unverified")
    ) == "legacy"
    assert collector._upload_metric_identity_from_authority(
        collector.UploadIdentity("future-private-value")
    ) == "none"
    assert collector._upload_metric_identity_from_authority(object()) == "none"


def test_window_boundary_includes_trigger_once_and_conserves_concurrency(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        collector,
        "_UPLOAD_METRICS",
        collector._new_upload_identity_metrics(window_started=1.0),
    )

    def record_one(_index: int) -> None:
        collector._record_upload_identity_metric(
            200,
            1.0,
            identity="trusted",
            reason="ok",
            records=_empty_records(accepted=1),
            now=61.0,
        )

    with ThreadPoolExecutor(max_workers=32) as pool:
        list(pool.map(record_one, range(800)))

    output = capsys.readouterr().out
    assert output.count("[upload-identity-summary]") == 1
    assert "window_seconds=60.0 requests=1" in output
    assert "identity_trusted=1" in output
    assert "records_accepted=1" in output
    metrics = _metric_snapshot()
    assert metrics["requests"] == 799
    assert metrics["identity_trusted"] == 799
    assert metrics["records_accepted"] == 799


def test_metrics_restart_loses_unflushed_window_by_design() -> None:
    first = collector._new_upload_identity_metrics(window_started=1.0)
    first["identity_trusted"] = 7
    restarted = collector._new_upload_identity_metrics(window_started=2.0)
    assert restarted["window_started"] == 2.0
    assert restarted["requests"] == 0
    assert restarted["identity_trusted"] == 0


@pytest.mark.parametrize(
    ("send_result", "expected_write"),
    ((True, "ok"), (False, "broken_pipe")),
)
def test_upload_response_records_after_write_result(
    monkeypatch,
    send_result: bool,
    expected_write: str,
) -> None:
    handler = object.__new__(collector.Handler)
    handler._send = lambda _code, _payload: send_result
    recorded = []
    monkeypatch.setattr(
        collector,
        "_record_upload_identity_metric",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )
    records = _empty_records(accepted=1)
    assert handler._send_upload_response(
        200,
        records,
        started=0.0,
        identity="trusted",
        reason="ok",
        records=records,
    ) is send_result
    assert len(recorded) == 1
    args, kwargs = recorded[0]
    assert args[0] == 200
    assert args[1] >= 0.0
    assert kwargs == {
        "identity": "trusted",
        "reason": "ok",
        "response_write": expected_write,
        "records": records,
    }


def test_upload_response_records_failed_write_and_reraises(monkeypatch) -> None:
    handler = object.__new__(collector.Handler)

    def fail_send(_code, _payload):
        raise OSError("PRIVATE-WRITE-EXCEPTION")

    handler._send = fail_send
    recorded = []
    monkeypatch.setattr(
        collector,
        "_record_upload_identity_metric",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )
    with pytest.raises(OSError, match="PRIVATE-WRITE-EXCEPTION"):
        handler._send_upload_response(
            503,
            {"error": "identity_state_error"},
            started=0.0,
            identity="trusted",
            reason="identity_state_error",
        )
    assert len(recorded) == 1
    assert recorded[0][1]["response_write"] == "failed"


@pytest.mark.parametrize(
    ("emit_failure", "failure_point"),
    (
        (BrokenPipeError("PRIVATE-BROKEN-PIPE"), "flush"),
        (OSError("PRIVATE-STDOUT-FAILURE"), "flush"),
        (
            UnicodeEncodeError(
                "ascii", "X", 0, 1, "PRIVATE-ENCODING-FAILURE"
            ),
            "write",
        ),
    ),
)
def test_success_response_survives_summary_emit_failure_without_replay(
    monkeypatch,
    capsys,
    emit_failure,
    failure_point,
) -> None:
    class FailingWriter:
        written = []
        flushes = 0

        def write(self, value):
            if failure_point == "write":
                raise emit_failure
            type(self).written.append(value)
            return len(value)

        def flush(self):
            type(self).flushes += 1
            if failure_point == "flush":
                raise emit_failure

    handler = object.__new__(collector.Handler)
    sent = []
    handler._send = lambda code, payload: sent.append((code, payload)) or True
    records = _empty_records(accepted=1)
    monkeypatch.setattr(
        collector,
        "_UPLOAD_METRICS",
        collector._new_upload_identity_metrics(
            window_started=collector.time.monotonic() - 61.0
        ),
    )

    with monkeypatch.context() as scoped:
        scoped.setattr(sys, "stdout", FailingWriter())
        delivered = handler._send_upload_response(
            200,
            records,
            started=collector.time.monotonic(),
            identity="trusted",
            reason="ok",
            records=records,
        )

    assert delivered is True
    assert sent == [(200, records)]
    assert FailingWriter.flushes == (1 if failure_point == "flush" else 0)
    assert all("PRIVATE-" not in value for value in FailingWriter.written)
    after_failed_emit = _metric_snapshot()
    assert after_failed_emit["requests"] == 0

    collector._record_upload_identity_metric(
        401,
        1.0,
        identity="none",
        reason="bad_auth",
        now=after_failed_emit["window_started"] + 60.0,
    )
    output = capsys.readouterr().out
    assert output.count("[upload-identity-summary]") == 1
    assert "requests=1" in output
    assert "identity_trusted=0 identity_legacy=0 identity_none=1" in output
    assert "records_accepted=0" in output
    assert "reason_ok=0" in output
    assert "reason_bad_auth=1" in output
    assert "PRIVATE-" not in output


@pytest.mark.parametrize(
    "stdout_value",
    (
        object(),
        type("WriteNoneStdout", (), {"write": None, "flush": None})(),
    ),
)
def test_success_response_survives_nonstandard_stdout_exception(
    monkeypatch,
    capsys,
    stdout_value,
) -> None:
    handler = object.__new__(collector.Handler)
    sent = []
    handler._send = lambda code, payload: sent.append((code, payload)) or True
    records = _empty_records(accepted=1)
    monkeypatch.setattr(
        collector,
        "_UPLOAD_METRICS",
        collector._new_upload_identity_metrics(
            window_started=collector.time.monotonic() - 61.0
        ),
    )

    with monkeypatch.context() as scoped:
        scoped.setattr(sys, "stdout", stdout_value)
        delivered = handler._send_upload_response(
            200,
            records,
            started=collector.time.monotonic(),
            identity="trusted",
            reason="ok",
            records=records,
        )

    assert delivered is True
    assert sent == [(200, records)]
    after_failed_emit = _metric_snapshot()
    assert after_failed_emit["requests"] == 0
    collector._record_upload_identity_metric(
        401,
        1.0,
        identity="none",
        reason="bad_auth",
        now=after_failed_emit["window_started"] + 60.0,
    )
    output = capsys.readouterr().out
    assert output.count("[upload-identity-summary]") == 1
    assert "requests=1" in output
    assert "identity_trusted=0 identity_legacy=0 identity_none=1" in output
    assert "records_accepted=0" in output


def test_concurrent_success_consumes_failed_old_emit_once_and_keeps_new_window(
    monkeypatch,
    capsys,
) -> None:
    class ExplodingStdout:
        writes = 0
        lock = collector.threading.Lock()

        def write(self, _value):
            with type(self).lock:
                type(self).writes += 1
            raise RuntimeError("PRIVATE-ORDINARY-EMIT-FAILURE")

        def flush(self):
            raise RuntimeError("PRIVATE-ORDINARY-FLUSH-FAILURE")

    sent = 0
    sent_lock = collector.threading.Lock()
    records = _empty_records(accepted=1)
    monkeypatch.setattr(
        collector,
        "_UPLOAD_METRICS",
        collector._new_upload_identity_metrics(
            window_started=collector.time.monotonic() - 61.0
        ),
    )

    def send_one(_index):
        nonlocal sent
        handler = object.__new__(collector.Handler)

        def send_response(_code, _payload):
            nonlocal sent
            with sent_lock:
                sent += 1
            return True

        handler._send = send_response
        return handler._send_upload_response(
            200,
            records,
            started=collector.time.monotonic(),
            identity="trusted",
            reason="ok",
            records=records,
        )

    with monkeypatch.context() as scoped:
        scoped.setattr(sys, "stdout", ExplodingStdout())
        with ThreadPoolExecutor(max_workers=32) as pool:
            delivered = list(pool.map(send_one, range(32)))

    assert delivered == [True] * 32
    assert sent == 32
    assert ExplodingStdout.writes == 1
    after_failed_emit = _metric_snapshot()
    assert after_failed_emit["requests"] == 31
    assert after_failed_emit["identity_trusted"] == 31
    assert after_failed_emit["records_accepted"] == 31

    collector._record_upload_identity_metric(
        401,
        1.0,
        identity="none",
        reason="bad_auth",
        now=after_failed_emit["window_started"] + 60.0,
    )
    output = capsys.readouterr().out
    assert output.count("[upload-identity-summary]") == 1
    assert "requests=32" in output
    assert "identity_trusted=31 identity_legacy=0 identity_none=1" in output
    assert "records_accepted=31" in output
    assert "reason_ok=31" in output
    assert "reason_bad_auth=1" in output
    assert "PRIVATE-" not in output


@pytest.mark.parametrize(
    "base_failure",
    (
        KeyboardInterrupt(),
        SystemExit(7),
        GeneratorExit(),
    ),
)
def test_summary_emit_does_not_swallow_base_exceptions(
    monkeypatch,
    base_failure,
) -> None:
    class BaseFailureStdout:
        def write(self, _value):
            raise base_failure

        def flush(self):
            raise base_failure

    handler = object.__new__(collector.Handler)
    sent = []
    handler._send = lambda code, payload: sent.append((code, payload)) or True
    records = _empty_records(accepted=1)
    monkeypatch.setattr(
        collector,
        "_UPLOAD_METRICS",
        collector._new_upload_identity_metrics(
            window_started=collector.time.monotonic() - 61.0
        ),
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(sys, "stdout", BaseFailureStdout())
        with pytest.raises(type(base_failure)):
            handler._send_upload_response(
                200,
                records,
                started=collector.time.monotonic(),
                identity="trusted",
                reason="ok",
                records=records,
            )
    assert sent == [(200, records)]


def test_numeric_inputs_are_exact_finite_and_overflow_safe(monkeypatch) -> None:
    huge_int = 10**10_000

    class BombInt(int):
        calls = 0

        def explode(self, *_args, **_kwargs):
            type(self).calls += 1
            raise AssertionError("numeric subclass protocol must not run")

        __float__ = explode
        __lt__ = explode
        __le__ = explode
        __ge__ = explode
        __bool__ = explode
        __repr__ = explode

    class BombFloat(float):
        calls = 0

        def explode(self, *_args, **_kwargs):
            type(self).calls += 1
            raise AssertionError("float subclass protocol must not run")

        __float__ = explode
        __lt__ = explode
        __le__ = explode
        __ge__ = explode
        __bool__ = explode
        __repr__ = explode

    assert collector._upload_metric_nonnegative_float(huge_int) == 0.0
    assert collector._upload_metric_nonnegative_float(float("inf")) == 0.0
    assert collector._upload_metric_nonnegative_float(float("-inf")) == 0.0
    assert collector._upload_metric_nonnegative_float(float("nan")) == 0.0
    assert collector._upload_metric_nonnegative_float(-1.0) == 0.0
    assert collector._upload_metric_nonnegative_float(BombInt(1)) == 0.0
    assert collector._upload_metric_nonnegative_float(BombFloat(1.0)) == 0.0
    assert BombInt.calls == 0
    assert BombFloat.calls == 0

    monkeypatch.setattr(
        collector,
        "_UPLOAD_METRICS",
        collector._new_upload_identity_metrics(window_started=BombInt(1)),
    )
    assert _metric_snapshot()["window_started"] == 0.0
    with collector._UPLOAD_METRICS_LOCK:
        collector._UPLOAD_METRICS["window_started"] = BombInt(1)
    collector._record_upload_identity_metric(
        200,
        huge_int,
        identity="trusted",
        reason="ok",
        records=_empty_records(accepted=1),
        now=BombInt(1),
    )
    collector._record_upload_identity_metric(
        200,
        1e308,
        identity="trusted",
        reason="ok",
        records=_empty_records(accepted=1),
        now=0.0,
    )
    metrics = _metric_snapshot()
    assert metrics["requests"] == 2
    assert metrics["latency_lt_100ms"] == 1
    assert metrics["latency_ge_1500ms"] == 1
    assert BombInt.calls == 0
    assert BombFloat.calls == 0


@pytest.mark.parametrize(
    ("setup", "headers", "expected_code", "expected_reason"),
    (
        ("rate", {"Content-Length": "2"}, 429, "rate_limited"),
        ("bad_length", {"Content-Length": "bad"}, 400, "bad_content_length"),
        (
            "too_large",
            {"Content-Length": str(collector._MAX_BODY + 1)},
            413,
            "payload_too_large",
        ),
        ("bad_auth", {"Content-Length": "2"}, 401, "bad_auth"),
    ),
)
def test_early_ingest_failures_are_fixed_none_metrics(
    monkeypatch,
    setup: str,
    headers: dict[str, str],
    expected_code: int,
    expected_reason: str,
) -> None:
    handler = object.__new__(collector.Handler)
    handler.path = "/ingest"
    handler.headers = headers
    handler.rfile = io.BytesIO(b"{}")
    handler._client_ip = lambda: "127.0.0.1"
    sent = []
    handler._send = lambda code, payload, **_kwargs: sent.append(
        (code, payload)
    ) or True
    monkeypatch.setattr(
        collector,
        "_UPLOAD_METRICS",
        collector._new_upload_identity_metrics(),
    )
    monkeypatch.setattr(collector, "_rate_ok", lambda _ip: setup != "rate")
    monkeypatch.setattr(collector, "_KEY", "required" if setup == "bad_auth" else "")
    handler.do_POST()
    assert sent[0][0] == expected_code
    metrics = _metric_snapshot()
    assert metrics["requests"] == 1
    assert metrics["identity_none"] == 1
    assert metrics[f"reason_{expected_reason}"] == 1


def test_malformed_body_is_none_and_valid_legacy_response_bytes_stay_exact(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(collector, "_KEY", "")
    monkeypatch.setattr(collector, "_UPLOAD_IDENTITY_MODE", "compat")
    monkeypatch.setattr(collector, "_rate_ok", lambda _ip: True)
    monkeypatch.setattr(
        collector,
        "_UPLOAD_METRICS",
        collector._new_upload_identity_metrics(),
    )

    malformed = object.__new__(collector.Handler)
    malformed.path = "/ingest"
    malformed.headers = {"Content-Length": "1"}
    malformed.rfile = io.BytesIO(b"{")
    malformed._client_ip = lambda: "127.0.0.1"
    malformed_sent = []
    malformed._send = lambda code, payload, **_kwargs: malformed_sent.append(
        (code, payload)
    ) or True
    malformed.do_POST()
    assert malformed_sent[0][0] == 400
    metrics = _metric_snapshot()
    assert metrics["identity_none"] == 1
    assert metrics["identity_legacy"] == 0
    assert metrics["reason_bad_json"] == 1

    out = tmp_path / "samples.jsonl"
    monkeypatch.setattr(collector, "_OUT", out)
    monkeypatch.setattr(collector, "_SEEN", set())
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    sample = {
        "map_id": 2401,
        "final_quality_counts": "q1=1",
        "session_hash": "rollout-observability-legacy",
        "known_value_sum": 99999,
        "client_ts": 1_700_000_000,
    }
    raw = json.dumps(sample, separators=(",", ":")).encode("utf-8")
    valid = object.__new__(collector.Handler)
    valid.path = "/ingest"
    valid.headers = {"Content-Length": str(len(raw))}
    valid.rfile = io.BytesIO(raw)
    valid._client_ip = lambda: "127.0.0.1"
    valid_sent = []
    valid._send = lambda code, payload, **_kwargs: valid_sent.append(
        (code, payload)
    ) or True
    valid.do_POST()
    assert valid_sent == [
        (
            200,
            {"accepted": 1, "duplicate": 0, "quarantined": 0, "rejected": 0},
        )
    ]
    stored = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert stored["identity_trust"] == "legacy_unverified"
    assert "uid_hash" not in stored
    metrics = _metric_snapshot()
    assert metrics["identity_legacy"] == 1
    assert metrics["records_accepted"] == 1
    assert metrics["reason_ok"] == 1
