from __future__ import annotations

import json
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import activation_transport_diagnostic as diag


def _recheck_sequence(at: int, *, recovered_at: int | None) -> dict[str, object]:
    return {
        "started_at": at,
        "last_failure_at": at,
        "recovered_at": recovered_at,
        "attempt_count": 1,
        "attempts": [
            {
                "at": at,
                "endpoint": "endpoint_1",
                "stage": "tls",
                "failure_class": "tls_connection_reset",
            }
        ],
    }


def _write_recheck_history(
    path: Path,
    *,
    now: int,
    sequences: list[dict[str, object]],
) -> None:
    path.write_text(
        json.dumps({"schema": 1, "updated_at": now, "sequences": sequences}),
        encoding="utf-8",
    )


def test_health_payload_requires_exact_json_boolean_true():
    assert diag._health_payload_ok('{"ok": true}') is True
    for body in (
        '{"ok": false}',
        '{"ok": "true"}',
        '{"ok": 1}',
        '{"not_ok": true}',
        '<html>ok</html>',
        'not json',
    ):
        assert diag._health_payload_ok(body) is False

    assert diag._probe_payload_ok('{"ok": true, "service": "activation"}', "activation_readiness")
    assert not diag._probe_payload_ok('{"ok": true}', "activation_readiness")


def test_classify_distinguishes_python_and_curl_paths():
    def rows(transport, health, readiness):
        return [
            {"transport": transport, "name": "endpoint_1", "endpoint": "health", "ok": health},
            {"transport": transport, "name": "endpoint_1", "endpoint": "activation_readiness", "ok": readiness},
        ]

    assert diag.classify(
        rows("packaged_python", True, True) + rows("windows_curl", False, False)
    ) == "PACKAGED_PYTHON_ACTIVATION_READY"
    assert diag.classify(
        rows("packaged_python", False, False) + rows("windows_curl", True, True)
    ) == "PACKAGED_PYTHON_FAIL_WINDOWS_CURL_ACTIVATION_READY"
    assert diag.classify(
        rows("packaged_python", True, False) + rows("windows_curl", True, False)
    ) == "ACTIVATION_SERVICE_NOT_READY"
    assert diag.classify(
        rows("packaged_python", False, False) + rows("windows_curl", False, False)
    ) == "ALL_TESTED_HTTPS_PATHS_FAILED"
    assert diag.classify(
        [
            {
                "transport": "packaged_python",
                "name": "endpoint_1",
                "endpoint": "health",
                "ok": False,
                "failure_class": "tls_connection_reset",
            }
        ]
    ) == "TLS_CONNECTION_RESET"
    assert diag.classify(
        [],
        {"endpoint_2": {"comparison": "dns_answer_unstable_or_suspect"}},
    ) == "DNS_ANSWER_UNSTABLE_OR_SUSPECT"


def test_transport_failures_preserve_multiple_actionable_stages():
    dns = {
        "endpoint_1": {"comparison": "consistent"},
        "endpoint_2": {"comparison": "dns_answer_unstable_or_suspect"},
    }
    results = [
        {"failure_class": "tls_connection_reset"},
        {"failure_class": "connect_timeout"},
        {"failure_class": "tls_connection_reset"},
    ]

    assert diag.transport_failure_classes(results, dns) == [
        "dns_answer_unstable_or_suspect",
        "tls_connection_reset",
        "connect_timeout",
    ]


def test_system_dns_repeats_and_flags_changed_or_disjoint_answers(monkeypatch):
    answers = iter(["1.1.1.1", "8.8.8.8", "1.1.1.1"])

    def fake_getaddrinfo(_host, _port):
        address = next(answers)
        return [(2, 1, 6, "", (address, 443))]

    monkeypatch.setattr(diag.socket, "getaddrinfo", fake_getaddrinfo)

    system = diag.inspect_system_dns("example.test", attempts=3)
    assert system == {
        "status": "answer_changed",
        "answers": ["1.1.1.1", "8.8.8.8"],
        "attempts": 3,
        "successful_attempts": 3,
        "error_types": [],
    }
    assert diag.compare_dns(
        system,
        {"status": "available", "answers": ["9.9.9.9"]},
    ) == "dns_answer_unstable_or_suspect"
    assert diag.compare_dns(
        {**system, "status": "stable", "answers": ["1.1.1.1"]},
        {"status": "available", "answers": ["1.1.1.1", "9.9.9.9"]},
    ) == "consistent"
    assert diag._canonical_dns_ip("198.18.0.1") == "198.18.0.1"
    assert diag.compare_dns(
        {**system, "status": "stable", "answers": ["198.18.0.1"]},
        {"status": "available", "answers": ["1.1.1.1"]},
    ) == "reference_mismatch"
    mismatched_dns = {
        "endpoint_1": {"comparison": "reference_mismatch"},
    }
    assert diag.transport_failure_classes(
        [{"ok": False, "failure_class": "connect_timeout"}],
        mismatched_dns,
    ) == ["dns_answer_unstable_or_suspect", "connect_timeout"]
    assert diag.transport_failure_classes(
        [{"ok": True}],
        mismatched_dns,
    ) == []


def test_diagnostic_probes_health_and_non_writing_activation_readiness():
    assert diag.ENDPOINTS == (
        ("endpoint_1", "health", "https://api.bidking.example.invalid/healthz"),
        ("endpoint_1", "activation_readiness", "https://api.bidking.example.invalid/activation-readiness"),
        ("endpoint_2", "health", "https://backup.bidking.example.invalid/healthz"),
        ("endpoint_2", "activation_readiness", "https://backup.bidking.example.invalid/activation-readiness"),
    )


def test_python_probe_does_not_follow_redirects(monkeypatch):
    requested_paths: list[str] = []

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested_paths.append(self.path)
            if self.path == "/healthz":
                self.send_response(302)
                self.send_header("Location", "/activate")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, _format, *_args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(diag, "certifi", SimpleNamespace(where=lambda: "unused"))
        monkeypatch.setattr(
            diag.ssl,
            "create_default_context",
            lambda **_kwargs: ssl._create_unverified_context(),
        )
        row = diag.check_python(
            "redirect-test",
            "health",
            f"http://127.0.0.1:{httpd.server_port}/healthz",
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)

    assert row["ok"] is False
    assert row["error_type"] == "HTTPError"
    assert requested_paths == ["/healthz"]


def test_python_probe_classifies_reset_without_exception_text(monkeypatch):
    monkeypatch.setattr(diag, "certifi", SimpleNamespace(where=lambda: "unused"))
    monkeypatch.setattr(
        diag.ssl,
        "create_default_context",
        lambda **_kwargs: ssl._create_unverified_context(),
    )

    def reset(*_args, **_kwargs):
        raise urllib.error.URLError(
            ConnectionResetError(r"PRIVATE_TOKEN C:\Users\example")
        )

    monkeypatch.setattr(diag, "_open_no_redirect", reset)
    row = diag.check_python(
        "endpoint_1",
        "health",
        "https://api.bidking.example.invalid/healthz",
    )

    assert row["stage"] == "tls"
    assert row["failure_class"] == "tls_connection_reset"
    serialized = json.dumps(row)
    for canary in ("PRIVATE_TOKEN", "private-user", "C:\\\\Users"):
        assert canary not in serialized


def test_windows_curl_disables_user_config_and_redirects(monkeypatch):
    captured: list[str] = []

    def fake_run(argv, **_kwargs):
        captured[:] = argv
        output_path = Path(argv[argv.index("--output") + 1])
        output_path.write_text('{"ok": true}', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout="200", stderr="")

    monkeypatch.setattr(diag.subprocess, "run", fake_run)

    row = diag.check_curl(
        "endpoint_1",
        "health",
        "https://api.bidking.example.invalid/healthz",
        "curl.exe",
    )

    assert row["ok"] is True
    assert captured[1] == "--disable"
    assert captured[captured.index("--max-redirs") + 1] == "0"
    assert "--location" not in captured
    assert "-L" not in captured


def test_windows_curl_classifies_schannel_reset_without_stderr(monkeypatch):
    def fake_run(argv, **_kwargs):
        output_path = Path(argv[argv.index("--output") + 1])
        output_path.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(
            argv,
            35,
            stdout="000",
            stderr=r"PRIVATE_TOKEN C:\Users\example",
        )

    monkeypatch.setattr(diag.subprocess, "run", fake_run)

    row = diag.check_curl(
        "endpoint_1",
        "health",
        "https://api.bidking.example.invalid/healthz",
        "curl.exe",
    )

    assert row["stage"] == "tls"
    assert row["failure_class"] == "tls_connection_reset"
    assert "stderr" not in row
    serialized = json.dumps(row)
    for canary in ("PRIVATE_TOKEN", "private-user", "C:\\\\Users"):
        assert canary not in serialized


def test_report_contract_contains_no_credential_fields(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(diag, "build_report", lambda: {
        "diagnostic": "transport only",
        "results": [],
        "classification": "ACTIVATION_SERVICE_NOT_READY",
    })
    assert diag.main() == 0
    payload = (tmp_path / diag.OUTPUT_NAME).read_text(encoding="utf-8")
    parsed = json.loads(payload)
    assert parsed["classification"] == "ACTIVATION_SERVICE_NOT_READY"
    assert all(ord(char) < 128 for char in payload)
    lowered = payload.lower()
    for forbidden in ("token", "device_id", "master_key", "upload_key", "ingest_key"):
        assert forbidden not in lowered


def test_progress_output_is_fixed_flush_safe_and_rejects_unknown_phase(monkeypatch):
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        "builtins.print",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    for phase in (
        "start",
        "dns",
        "python_https",
        "windows_https",
        "local_state",
        "summarize",
        "complete",
        "failed",
    ):
        diag._emit_progress(phase)
    diag._emit_progress("PRIVATE_TOKEN")
    diag._emit_progress(object())

    rendered = "\n".join(str(args[0]) for args, _kwargs in calls)
    assert len(calls) == 8
    assert all(kwargs == {"flush": True} for _args, kwargs in calls)
    assert "PRIVATE_TOKEN" not in rendered
    assert "C:\\Users" not in rendered
    assert "[1/7]" in rendered
    assert "[7/7]" in rendered
    assert "[6/7] 正在整理诊断结果。" in rendered
    assert "脱敏" not in rendered
    assert "诊断未完成" in rendered


def test_build_report_emits_progress_before_each_slow_stage(monkeypatch):
    phases: list[str] = []
    monkeypatch.setattr(diag, "_emit_progress", phases.append)
    monkeypatch.setattr(diag, "BASES", (("endpoint_1", "https://example.invalid"),))
    monkeypatch.setattr(
        diag,
        "ENDPOINTS",
        (
            ("endpoint_1", "health", "https://example.invalid/healthz"),
            (
                "endpoint_1",
                "activation_readiness",
                "https://example.invalid/activation-readiness",
            ),
        ),
    )

    def system_dns(_host):
        assert phases[-1] == "dns"
        return {
            "status": "stable",
            "answers": ["203.0.113.1"],
            "attempts": 3,
            "successful_attempts": 3,
            "error_types": [],
        }

    def trusted_dns(_host):
        assert phases[-1] == "dns"
        return {"status": "available", "answers": ["203.0.113.1"]}

    def python_probe(name, endpoint, url):
        assert phases[-1] == "python_https"
        return {
            "name": name,
            "transport": "packaged_python",
            "endpoint": endpoint,
            "url": url,
            "ok": True,
            "status": 200,
            "payload_valid": True,
            "stage": "success",
        }

    def curl_probe(name, endpoint, url, _curl_path):
        assert phases[-1] == "windows_https"
        return {
            "name": name,
            "transport": "windows_curl",
            "endpoint": endpoint,
            "url": url,
            "ok": True,
            "status": 200,
            "payload_valid": True,
            "stage": "success",
        }

    def local_activation():
        assert phases[-1] == "local_state"
        return {"cache_state": "missing"}

    def network_state():
        assert phases[-1] == "local_state"
        return {"status": "available"}

    monkeypatch.setattr(diag, "inspect_system_dns", system_dns)
    monkeypatch.setattr(diag, "inspect_trusted_dns", trusted_dns)
    monkeypatch.setattr(diag, "check_python", python_probe)
    monkeypatch.setattr(diag, "check_curl", curl_probe)
    monkeypatch.setattr(diag, "inspect_local_activation", local_activation)
    monkeypatch.setattr(diag, "inspect_windows_network_state", network_state)
    monkeypatch.setattr(diag, "inspect_windows_proxy_state", network_state)
    monkeypatch.setattr(diag, "inspect_activation_recheck_history", lambda: {})
    monkeypatch.setattr(diag, "_curl_version", lambda _path: "curl-safe")

    report = diag.build_report()

    assert report["classification"] == "PACKAGED_PYTHON_ACTIVATION_READY"
    assert phases == [
        "dns",
        "python_https",
        "windows_https",
        "local_state",
        "summarize",
    ]


def test_build_report_preserves_parent_probe_call_order(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(diag, "_emit_progress", lambda _phase: None)
    monkeypatch.setattr(diag, "BASES", (("endpoint_1", "https://example.invalid"),))
    monkeypatch.setattr(
        diag,
        "ENDPOINTS",
        (("endpoint_1", "health", "https://example.invalid/healthz"),),
    )

    def mark(name, value):
        def probe(*_args, **_kwargs):
            calls.append(name)
            return value

        return probe

    monkeypatch.setattr(
        diag,
        "inspect_system_dns",
        mark(
            "system_dns",
            {
                "status": "stable",
                "answers": ["203.0.113.1"],
                "attempts": 3,
                "successful_attempts": 3,
                "error_types": [],
            },
        ),
    )
    monkeypatch.setattr(
        diag,
        "inspect_trusted_dns",
        mark("trusted_dns", {"status": "available", "answers": ["203.0.113.1"]}),
    )
    monkeypatch.setattr(
        diag,
        "check_python",
        mark(
            "python_https",
            {
                "name": "endpoint_1",
                "transport": "packaged_python",
                "endpoint": "health",
                "url": "https://example.invalid/healthz",
                "ok": True,
                "status": 200,
                "payload_valid": True,
                "stage": "success",
            },
        ),
    )
    monkeypatch.setattr(
        diag,
        "check_curl",
        mark(
            "windows_https",
            {
                "name": "endpoint_1",
                "transport": "windows_curl",
                "endpoint": "health",
                "url": "https://example.invalid/healthz",
                "ok": True,
                "status": 200,
                "payload_valid": True,
                "stage": "success",
            },
        ),
    )
    monkeypatch.setattr(
        diag,
        "inspect_local_activation",
        mark("local_activation", {"cache_state": "missing"}),
    )
    monkeypatch.setattr(diag, "_curl_version", mark("curl_version", "curl-safe"))
    monkeypatch.setattr(
        diag,
        "inspect_windows_network_state",
        mark("windows_network", {"status": "available"}),
    )
    monkeypatch.setattr(
        diag,
        "inspect_windows_proxy_state",
        mark("windows_proxy", {"status": "available"}),
    )
    monkeypatch.setattr(
        diag,
        "inspect_activation_recheck_history",
        mark("recheck_history", {}),
    )

    diag.build_report()

    assert calls == [
        "system_dns",
        "trusted_dns",
        "python_https",
        "windows_https",
        "local_activation",
        "curl_version",
        "windows_network",
        "windows_proxy",
        "recheck_history",
    ]


def test_main_shows_immediate_progress_and_completion_without_full_path(
    monkeypatch, tmp_path: Path, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        diag,
        "build_report",
        lambda: {"diagnostic": "transport only", "results": []},
    )

    assert diag.main() == 0

    output = capsys.readouterr().out
    assert "[1/7]" in output
    assert "[7/7]" in output
    assert diag.OUTPUT_NAME in output
    assert str(tmp_path) not in output


def test_main_reports_fixed_failure_without_leaking_exception(
    monkeypatch, tmp_path: Path, capsys
):
    monkeypatch.chdir(tmp_path)

    def fail():
        raise RuntimeError(r"PRIVATE_TOKEN C:\Users\example")

    monkeypatch.setattr(diag, "build_report", fail)

    assert diag.main() == 1

    output = capsys.readouterr().out
    assert "诊断未完成" in output
    assert "PRIVATE_TOKEN" not in output
    assert "private-user" not in output
    assert not (tmp_path / diag.OUTPUT_NAME).exists()


def test_batch_launcher_marks_start_and_distinguishes_success_from_failure():
    batch = Path(diag.__file__).with_suffix(".bat").read_text(encoding="utf-8")

    assert "Activation network diagnostic started" in batch
    assert 'if "%EXIT_CODE%"=="0"' in batch
    assert "Diagnostic complete" in batch
    assert "Diagnostic did not complete" in batch
    assert "do not send an old report" in batch
    assert Path(diag.__file__).with_suffix(".bat").read_bytes().isascii()


def test_build_report_redacts_urls_hosts_dns_answers_and_exception_details(monkeypatch):
    system_answer = "203.0.113.61"
    reference_answer = "198.51.100.77"

    monkeypatch.setattr(
        diag,
        "inspect_system_dns",
        lambda _host: {
            "status": "stable",
            "answers": [system_answer],
            "attempts": 3,
            "successful_attempts": 3,
            "error_types": ["PRIVATE-DNS-ERROR"],
        },
    )
    monkeypatch.setattr(
        diag,
        "inspect_trusted_dns",
        lambda _host: {"status": "available", "answers": [reference_answer]},
    )

    def safe_success(name, endpoint, url, *_args):
        return {
            "name": name,
            "transport": "windows_curl" if _args else "packaged_python",
            "endpoint": endpoint,
            "url": url,
            "ok": True,
            "status": 200,
            "payload_valid": True,
            "stage": "success",
            "elapsed_ms": 1.0,
            "error_type": "PRIVATE-EXCEPTION",
            "unexpected": "PRIVATE-ROW",
        }

    monkeypatch.setattr(diag, "check_python", safe_success)
    monkeypatch.setattr(diag, "check_curl", safe_success)
    monkeypatch.setattr(diag, "_curl_version", lambda _path: "curl-safe")
    monkeypatch.setattr(diag, "inspect_windows_network_state", lambda: {"status": "available"})
    monkeypatch.setattr(diag, "inspect_windows_proxy_state", lambda: {"status": "available"})
    monkeypatch.setattr(diag, "inspect_local_activation", lambda: {"cache_state": "missing"})

    report = diag.build_report()
    serialized = json.dumps(report)

    assert report["classification"] == "PACKAGED_PYTHON_ACTIVATION_READY"
    assert report["dns"]["endpoint_1"] == {
        "system": {
            "status": "stable",
            "answer_count": 1,
            "attempts": 3,
            "successful_attempts": 3,
            "error_count": 1,
        },
        "trusted_reference": {"status": "available", "answer_count": 1},
        "comparison": "reference_mismatch",
    }
    assert all(
        set(row)
        <= {
            "name",
            "transport",
            "endpoint",
            "ok",
            "stage",
            "status",
            "payload_valid",
            "failure_class",
        }
        for row in report["results"]
    )
    for forbidden in (
        *(url for _name, _endpoint, url in diag.ENDPOINTS),
        *(base.removeprefix("https://") for _name, base in diag.BASES),
        system_answer,
        reference_answer,
        "PRIVATE-DNS-ERROR",
        "PRIVATE-EXCEPTION",
        "PRIVATE-ROW",
    ):
        assert forbidden not in serialized


def test_build_report_keeps_dns_mismatch_classification_without_dns_answers(monkeypatch):
    monkeypatch.setattr(
        diag,
        "inspect_system_dns",
        lambda _host: {
            "status": "stable",
            "answers": ["203.0.113.9"],
            "attempts": 3,
            "successful_attempts": 3,
            "error_types": [],
        },
    )
    monkeypatch.setattr(
        diag,
        "inspect_trusted_dns",
        lambda _host: {"status": "available", "answers": ["198.51.100.8"]},
    )
    monkeypatch.setattr(
        diag,
        "check_python",
        lambda name, endpoint, url: {
            "name": name,
            "transport": "packaged_python",
            "endpoint": endpoint,
            "url": url,
            "ok": False,
            "stage": "tcp",
            "failure_class": "connect_timeout",
        },
    )
    monkeypatch.setattr(
        diag,
        "check_curl",
        lambda name, endpoint, url, _curl: {
            "name": name,
            "transport": "windows_curl",
            "endpoint": endpoint,
            "url": url,
            "ok": False,
            "stage": "tcp",
            "failure_class": "connect_timeout",
        },
    )
    monkeypatch.setattr(diag, "_curl_version", lambda _path: "curl-safe")
    monkeypatch.setattr(diag, "inspect_windows_network_state", lambda: {"status": "available"})
    monkeypatch.setattr(diag, "inspect_windows_proxy_state", lambda: {"status": "available"})
    monkeypatch.setattr(diag, "inspect_local_activation", lambda: {"cache_state": "missing"})

    report = diag.build_report()
    serialized = json.dumps(report)

    assert report["classification"] == "DNS_ANSWER_UNSTABLE_OR_SUSPECT"
    assert report["transport_failure_classes"] == [
        "dns_answer_unstable_or_suspect",
        "connect_timeout",
    ]
    assert "203.0.113.9" not in serialized
    assert "198.51.100.8" not in serialized


def test_build_report_top_level_failures_use_only_sanitized_probe_classes(monkeypatch):
    private_failure = "transport_unknown|https://private.example/198.51.100.91/SecretError"
    monkeypatch.setattr(
        diag,
        "inspect_system_dns",
        lambda _host: {
            "status": "stable",
            "answers": ["203.0.113.9"],
            "attempts": 3,
            "successful_attempts": 3,
            "error_types": [],
        },
    )
    monkeypatch.setattr(
        diag,
        "inspect_trusted_dns",
        lambda _host: {"status": "available", "answers": ["203.0.113.9"]},
    )

    def unknown_failure(name, endpoint, url, *_args):
        return {
            "name": name,
            "transport": "windows_curl" if _args else "packaged_python",
            "endpoint": endpoint,
            "url": url,
            "ok": False,
            "stage": "tls",
            "failure_class": private_failure,
        }

    monkeypatch.setattr(diag, "check_python", unknown_failure)
    monkeypatch.setattr(diag, "check_curl", unknown_failure)
    monkeypatch.setattr(diag, "_curl_version", lambda _path: "curl-safe")
    monkeypatch.setattr(diag, "inspect_windows_network_state", lambda: {"status": "available"})
    monkeypatch.setattr(diag, "inspect_windows_proxy_state", lambda: {"status": "available"})
    monkeypatch.setattr(diag, "inspect_local_activation", lambda: {"cache_state": "missing"})

    report = diag.build_report()
    serialized = json.dumps(report)

    assert {row.get("failure_class") for row in report["results"]} == {"transport_unknown"}
    assert report["transport_failure_classes"] == ["transport_unknown"]
    assert private_failure not in serialized
    assert "private.example" not in serialized
    assert "198.51.100.91" not in serialized
    assert "SecretError" not in serialized


def test_local_activation_report_uses_only_sanitized_product_contract(monkeypatch):
    safe = {
        "credential_available": True,
        "credential_file_present": True,
        "cache_file_present": True,
        "cache_bytes": 386,
        "invalid_marker_present": False,
        "cache_state": "valid",
        "lease_seconds_left": 86400,
        "online_refresh_recommended": False,
    }
    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.inspect_local_activation_state",
        lambda: safe,
    )

    assert diag.inspect_local_activation() == safe
    serialized = json.dumps(safe).lower()
    for forbidden in ("token", "device_id", "master_key", "upload_key", "ingest_key"):
        assert forbidden not in serialized


def test_local_activation_import_failure_uses_frozen_safe_classification(monkeypatch):
    monkeypatch.setitem(sys.modules, "bidking_lab.secure.client_activate", None)

    result = diag.inspect_local_activation()

    assert result == {"cache_state": "inspection_error"}


def test_recent_recheck_history_is_reprojected_without_private_fields(monkeypatch):
    safe = {
        "schema": 1,
        "sequences": [
            {
                "started_at": 2_000_000_000,
                "last_failure_at": 2_000_000_002,
                "recovered_at": 2_000_000_004,
                "attempt_count": 2,
                "attempts": [
                    {
                        "at": 2_000_000_000,
                        "endpoint": "endpoint_1",
                        "stage": "tls",
                        "failure_class": "tls_connection_reset",
                    },
                    {
                        "at": 2_000_000_002,
                        "endpoint": "endpoint_2",
                        "stage": "tcp",
                        "failure_class": "connect_timeout",
                    },
                ],
            }
        ],
    }
    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.read_activation_recheck_history",
        lambda: safe,
    )

    assert diag.inspect_activation_recheck_history() == safe
    serialized = json.dumps(diag.inspect_activation_recheck_history())
    for forbidden in (
        "https://",
        "api.bidking.example.invalid",
        "TOKEN-A",
        "DEVICE-A",
        "198.51.100.9",
    ):
        assert forbidden not in serialized


def test_recent_recheck_history_rejects_extra_or_nonfixed_fields(monkeypatch):
    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.read_activation_recheck_history",
        lambda: {
            "schema": 1,
            "sequences": [],
            "private_token": "PRIVATE_TOKEN",
        },
    )

    assert diag.inspect_activation_recheck_history() == {
        "schema": 1,
        "sequences": [],
    }


def test_recheck_history_sanitizer_rejects_impossible_recovery_timeline():
    now = 2_000_000_100
    damaged = {
        "schema": 1,
        "sequences": [
            {
                "started_at": now - 10,
                "last_failure_at": now - 5,
                "recovered_at": now - 4,
                "attempt_count": 1,
                "attempts": [
                    {
                        "at": now - 1,
                        "endpoint": "endpoint_1",
                        "stage": "tls",
                        "failure_class": "tls_connection_reset",
                    }
                ],
            }
        ],
    }

    assert diag._sanitize_activation_recheck_history(damaged) == {
        "schema": 1,
        "sequences": [],
    }


def test_recheck_history_sanitizer_accepts_valid_truncated_timeline():
    now = 2_000_000_100
    attempts = [
        {
            "at": now - 12 + index,
            "endpoint": "endpoint_1",
            "stage": "tls",
            "failure_class": "tls_connection_reset",
        }
        for index in range(12)
    ]
    safe = {
        "schema": 1,
        "sequences": [
            {
                "started_at": now - 20,
                "last_failure_at": now - 1,
                "recovered_at": now,
                "attempt_count": 13,
                "attempts": attempts,
            }
        ],
    }

    assert diag._sanitize_activation_recheck_history(safe) == safe


def test_recheck_history_inspection_rejects_two_pending_in_one_process_file(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app_dir = tmp_path / "bidking"
    app_dir.mkdir(parents=True)
    now = int(time.time())
    _write_recheck_history(
        app_dir / "activation_recheck_history_101.json",
        now=now,
        sequences=[
            _recheck_sequence(now - 20, recovered_at=None),
            _recheck_sequence(now - 10, recovered_at=None),
        ],
    )

    assert diag.inspect_activation_recheck_history() == {
        "schema": 1,
        "sequences": [],
    }


def test_recheck_history_inspection_rejects_next_start_before_recovery(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app_dir = tmp_path / "bidking"
    app_dir.mkdir(parents=True)
    now = int(time.time())
    _write_recheck_history(
        app_dir / "activation_recheck_history_101.json",
        now=now,
        sequences=[
            _recheck_sequence(now - 20, recovered_at=now - 10),
            _recheck_sequence(now - 15, recovered_at=None),
        ],
    )

    assert diag.inspect_activation_recheck_history() == {
        "schema": 1,
        "sequences": [],
    }


def test_recheck_history_inspection_keeps_pending_from_distinct_process_files(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app_dir = tmp_path / "bidking"
    app_dir.mkdir(parents=True)
    now = int(time.time())
    for process_id in (101, 202):
        _write_recheck_history(
            app_dir / f"activation_recheck_history_{process_id}.json",
            now=now,
            sequences=[
                _recheck_sequence(now - 10, recovered_at=None)
            ],
        )

    history = diag.inspect_activation_recheck_history()

    assert len(history["sequences"]) == 2
    assert all(
        sequence["recovered_at"] is None
        for sequence in history["sequences"]
    )


def test_windows_network_state_aggregates_without_interface_identity(monkeypatch):
    payload = {
        "active_adapter_count": 2,
        "ipv4_default_route_count": 1,
        "ipv6_default_route_count": 0,
        "dns_ipv4_adapter_count": 2,
        "dns_ipv6_adapter_count": 1,
        "ncsi_profile_count": 2,
        "ncsi_ipv4_internet": True,
        "ncsi_ipv6_internet": False,
    }

    def fake_run(argv, **_kwargs):
        assert "Get-NetIPConfiguration" in argv[-1]
        assert "InterfaceAlias" not in argv[-1]
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(diag.sys, "platform", "win32")
    monkeypatch.setattr(diag.subprocess, "run", fake_run)

    result = diag.inspect_windows_network_state()

    assert result == {"status": "available", **payload}
    serialized = json.dumps(result).lower()
    for forbidden in ("interfacealias", "ipaddress", "private-user", "192.168."):
        assert forbidden not in serialized


def test_windows_network_state_rejects_malformed_or_private_payload(monkeypatch):
    private_payload = {
        "active_adapter_count": True,
        "ipv4_default_route_count": 1,
        "ipv6_default_route_count": 0,
        "dns_ipv4_adapter_count": 1,
        "dns_ipv6_adapter_count": 0,
        "ncsi_profile_count": 1,
        "ncsi_ipv4_internet": True,
        "ncsi_ipv6_internet": False,
        "interface_name": "PRIVATE-ADAPTER",
    }
    monkeypatch.setattr(diag.sys, "platform", "win32")
    monkeypatch.setattr(
        diag.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps(private_payload), stderr=""
        ),
    )

    assert diag.inspect_windows_network_state() == {
        "status": "unavailable",
        "reason": "query_failed",
    }


def test_windows_proxy_state_reports_modes_without_addresses(monkeypatch):
    monkeypatch.setattr(diag.sys, "platform", "win32")
    monkeypatch.setattr(
        diag,
        "_read_wininet_settings",
        lambda: {
            "ProxyEnable": 1,
            "AutoConfigURL": "http://PRIVATE-PAC/path",
            "AutoDetect": 0,
        },
    )

    def fake_run(argv, **_kwargs):
        assert argv == ["netsh", "winhttp", "show", "proxy"]
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                "Current WinHTTP proxy settings:\n\n"
                "    Proxy Server(s) : PRIVATE-PROXY:8080"
            ),
            stderr="",
        )

    monkeypatch.setattr(diag.subprocess, "run", fake_run)

    result = diag.inspect_windows_proxy_state()

    assert result == {
        "wininet": {"status": "available", "mode": "proxy_and_auto"},
        "winhttp": {"status": "available", "mode": "proxy"},
    }
    serialized = json.dumps(result)
    assert "PRIVATE-PAC" not in serialized
    assert "PRIVATE-PROXY" not in serialized


def test_windows_proxy_state_distinguishes_direct_and_query_failure(monkeypatch):
    monkeypatch.setattr(diag.sys, "platform", "win32")
    monkeypatch.setattr(
        diag,
        "_read_wininet_settings",
        lambda: {"ProxyEnable": 0, "AutoConfigURL": None, "AutoDetect": 0},
    )
    monkeypatch.setattr(
        diag.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                "Current WinHTTP proxy settings:\n\n"
                "    Direct access (no proxy server)."
            ),
            stderr="",
        ),
    )
    assert diag.inspect_windows_proxy_state() == {
        "wininet": {"status": "available", "mode": "direct"},
        "winhttp": {"status": "available", "mode": "direct"},
    }

    monkeypatch.setattr(
        diag.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(r"PRIVATE_TOKEN C:\Users\example")
        ),
    )
    result = diag.inspect_windows_proxy_state()
    assert result["winhttp"] == {
        "status": "unavailable",
        "mode": "unknown",
        "reason": "query_failed",
    }
    assert "PRIVATE_TOKEN" not in json.dumps(result)


def test_windows_proxy_state_rejects_ambiguous_or_malformed_modes(monkeypatch):
    monkeypatch.setattr(diag.sys, "platform", "win32")
    monkeypatch.setattr(
        diag,
        "_read_wininet_settings",
        lambda: {"ProxyEnable": 0, "AutoConfigURL": None, "AutoDetect": 0},
    )
    monkeypatch.setattr(
        diag.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                "Current WinHTTP proxy settings:\n\n"
                "Direct access (no proxy server).\n"
                "Proxy Server(s) : SECRET-PROXY:8080"
            ),
            stderr="",
        ),
    )

    result = diag.inspect_windows_proxy_state()

    assert result["winhttp"] == {
        "status": "unavailable",
        "mode": "unknown",
        "reason": "query_failed",
    }
    assert "SECRET-PROXY" not in json.dumps(result)

    for malformed in (
        {"ProxyEnable": True, "AutoConfigURL": None, "AutoDetect": 0},
        {"ProxyEnable": 2, "AutoConfigURL": None, "AutoDetect": 0},
        {"ProxyEnable": 0, "AutoConfigURL": 123, "AutoDetect": 0},
        {"ProxyEnable": 0, "AutoConfigURL": None, "AutoDetect": "1"},
    ):
        monkeypatch.setattr(diag, "_read_wininet_settings", lambda value=malformed: value)
        assert diag.inspect_windows_proxy_state()["wininet"] == {
            "status": "unavailable",
            "mode": "unknown",
            "reason": "query_failed",
        }


def test_windows_proxy_state_rejects_unknown_truncated_and_nonzero_winhttp(monkeypatch):
    monkeypatch.setattr(diag.sys, "platform", "win32")
    monkeypatch.setattr(
        diag,
        "_read_wininet_settings",
        lambda: {"ProxyEnable": 0, "AutoConfigURL": None, "AutoDetect": 0},
    )

    for returncode, output in (
        (0, ""),
        (0, "Current WinHTTP proxy settings:"),
        (
            1,
            "Current WinHTTP proxy settings:\n"
            "Direct access (no proxy server).",
        ),
    ):
        monkeypatch.setattr(
            diag.subprocess,
            "run",
            lambda argv, _returncode=returncode, _output=output, **_kwargs: (
                subprocess.CompletedProcess(
                    argv,
                    _returncode,
                    stdout=_output,
                    stderr="PRIVATE-DETAIL",
                )
            ),
        )
        result = diag.inspect_windows_proxy_state()
        assert result["winhttp"] == {
            "status": "unavailable",
            "mode": "unknown",
            "reason": "query_failed",
        }
        assert "PRIVATE-DETAIL" not in json.dumps(result)


@pytest.mark.parametrize(
    "output",
    [
        "Direct access (no proxy server).",
        "Proxy Server(s) : PRIVATE-PROXY:8080",
        (
            "当前的 WinHTTP 代理服务器设置:\n"
            "Direct access (no proxy server)."
        ),
        (
            "Current WinHTTP proxy settings:\n"
            "Diagnostic detail: Direct access (no proxy server)."
        ),
        (
            "Current WinHTTP proxy settings:\n"
            "Direct access (no proxy server).\n"
            "Diagnostic detail: Proxy Server(s) : PRIVATE-PROXY:8080"
        ),
        (
            "Current WinHTTP proxy settings:\n"
            "Direct access (no proxy server).\n"
            "Diagnostic detail: healthy"
        ),
        (
            "Current WinHTTP proxy settings:\n"
            "Bypass List : (none)\n"
            "Proxy Server(s) : PRIVATE-PROXY:8080"
        ),
        (
            "Bypass List : (none)\n"
            "Current WinHTTP proxy settings:\n"
            "Proxy Server(s) : PRIVATE-PROXY:8080"
        ),
        (
            "当前的 WinHTTP 代理服务器设置：\n"
            "绕过列表： (无)\n"
            "代理服务器： PRIVATE-PROXY:8080"
        ),
        (
            "绕过列表： (无)\n"
            "当前的 WinHTTP 代理服务器设置：\n"
            "代理服务器： PRIVATE-PROXY:8080"
        ),
    ],
)
def test_windows_proxy_state_rejects_truncated_mixed_or_embedded_markers(
    monkeypatch,
    output,
):
    monkeypatch.setattr(diag.sys, "platform", "win32")
    monkeypatch.setattr(
        diag,
        "_read_wininet_settings",
        lambda: {"ProxyEnable": 0, "AutoConfigURL": None, "AutoDetect": 0},
    )
    monkeypatch.setattr(
        diag.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            0,
            stdout=output,
            stderr="PRIVATE-DETAIL",
        ),
    )

    result = diag.inspect_windows_proxy_state()

    assert result["winhttp"] == {
        "status": "unavailable",
        "mode": "unknown",
        "reason": "query_failed",
    }
    assert "PRIVATE-DETAIL" not in json.dumps(result)
    assert "PRIVATE-PROXY" not in json.dumps(result)


@pytest.mark.parametrize(
    ("output", "mode"),
    [
        (
            "Current WinHTTP proxy settings:\n"
            "Direct access (no proxy server).",
            "direct",
        ),
        (
            "Current WinHTTP proxy settings:\n"
            "Proxy Server(s) : PRIVATE-PROXY:8080",
            "proxy",
        ),
        (
            "Current WinHTTP proxy settings:\n"
            "Proxy Server(s) : PRIVATE-PROXY:8080\n"
            "Bypass List : (none)",
            "proxy",
        ),
        (
            "当前的 WinHTTP 代理服务器设置:\n"
            "直接访问(没有代理服务器)。",
            "direct",
        ),
        (
            "当前的 WinHTTP 代理服务器设置：\n"
            "代理服务器： PRIVATE-PROXY:8080",
            "proxy",
        ),
        (
            "当前的 WinHTTP 代理服务器设置：\n"
            "代理服务器： PRIVATE-PROXY:8080\n"
            "绕过列表： (无)",
            "proxy",
        ),
    ],
)
def test_windows_proxy_state_accepts_complete_locale_consistent_output(
    monkeypatch,
    output,
    mode,
):
    monkeypatch.setattr(diag.sys, "platform", "win32")
    monkeypatch.setattr(
        diag,
        "_read_wininet_settings",
        lambda: {"ProxyEnable": 0, "AutoConfigURL": None, "AutoDetect": 0},
    )
    monkeypatch.setattr(
        diag.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            0,
            stdout=output,
            stderr="",
        ),
    )

    result = diag.inspect_windows_proxy_state()

    assert result["winhttp"] == {"status": "available", "mode": mode}
    assert "PRIVATE-PROXY" not in json.dumps(result)


def test_local_activation_call_failure_does_not_expose_exception(monkeypatch):
    def fail():
        raise RuntimeError(r"C:\Users\example\token-secret")

    monkeypatch.setattr(
        "bidking_lab.secure.client_activate.inspect_local_activation_state",
        fail,
    )

    result = diag.inspect_local_activation()

    assert result == {"cache_state": "inspection_error"}
    serialized = json.dumps(result).lower()
    for forbidden in ("private-user", "token-secret", "runtimeerror", "c:\\users"):
        assert forbidden not in serialized
