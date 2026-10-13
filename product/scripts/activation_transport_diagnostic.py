"""Compare packaged Python HTTPS and Windows curl without sending credentials."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    import certifi
except Exception:  # pragma: no cover - reported in the output below
    certifi = None

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_SOURCE_ROOT = _PACKAGE_ROOT / "src"
if _SOURCE_ROOT.is_dir() and str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))
try:
    from bidking_lab.live.version_check import (
        load_activation_service_endpoint_config,
    )

    _ENDPOINT_CONFIG = load_activation_service_endpoint_config(
        _PACKAGE_ROOT / "activation_service_endpoints.json"
    )
    _ENDPOINT_CONFIG_STATUS = "valid"
except Exception:
    _ENDPOINT_CONFIG = None
    _ENDPOINT_CONFIG_STATUS = "invalid"

_DIAGNOSTIC_ENDPOINT_IDS = frozenset(
    {"endpoint_1", "endpoint_2", "endpoint_3"}
)


def _diagnostic_endpoint_rows(config: Any) -> tuple[tuple, tuple]:
    bases = tuple(
        (endpoint.endpoint_id, endpoint.base)
        for endpoint in (config.endpoints if config else ())
    )
    probes = tuple(
        (name, endpoint, base + path)
        for name, base in bases
        for endpoint, path in (
            ("health", "/healthz"),
            ("activation_readiness", "/activation-readiness"),
        )
    )
    return bases, probes


BASES, ENDPOINTS = _diagnostic_endpoint_rows(_ENDPOINT_CONFIG)
USER_AGENT = "BidKing-Client/1.0"
OUTPUT_NAME = "activation_transport_diagnostic.txt"
TRUSTED_DNS_URL = "https://1.1.1.1/dns-query"
_REPORT_PROBE_STAGES = frozenset({"success", "dns", "tcp", "tls", "http", "unknown"})
_REPORT_PROBE_FAILURES = frozenset(
    {
        "dns_lookup_failed",
        "connect_failed",
        "connect_timeout",
        "connect_refused",
        "tls_connection_reset",
        "tls_handshake_failed",
        "http_reachable_invalid_payload",
        "http_reachable_service_rejected",
        "transport_os_error",
        "transport_unknown",
    }
)
_REPORT_DNS_STATUSES = frozenset(
    {"stable", "answer_changed", "lookup_failed", "available", "no_answer", "unavailable"}
)
_REPORT_DNS_COMPARISONS = frozenset(
    {
        "consistent",
        "reference_mismatch",
        "dns_answer_unstable_or_suspect",
        "system_dns_unavailable",
        "reference_unavailable",
    }
)
_RECHECK_HISTORY_ENDPOINTS = frozenset(
    {"endpoint_1", "endpoint_2", "endpoint_3", "endpoint_4"}
)
_RECHECK_HISTORY_STAGES = frozenset({"dns", "tcp", "tls", "unknown"})
_RECHECK_HISTORY_FAILURES = frozenset(
    {
        "dns_lookup_failed",
        "connect_timeout",
        "connect_refused",
        "tls_connection_reset",
        "tls_handshake_failed",
        "transport_os_error",
        "transport_unknown",
    }
)
_MAX_SAFE_REPORT_EPOCH = 253_402_300_799
_PROGRESS_ROWS = {
    "start": (1, "激活网络诊断已开始，请保持窗口打开。"),
    "dns": (2, "正在检查 DNS 解析。"),
    "python_https": (3, "正在检查内置 HTTPS 连接。"),
    "windows_https": (4, "正在交叉检查 Windows HTTPS 连接。"),
    "local_state": (5, "正在读取本机网络与激活状态。"),
    "summarize": (6, "正在整理诊断结果。"),
    "complete": (7, "诊断完成，报告已生成。"),
    "failed": (None, "诊断未完成；请保留窗口并把截图私发作者，不要发送旧报告。"),
}


def _emit_progress(phase: object) -> None:
    """Print one fixed, privacy-safe progress row for the interactive launcher."""
    if type(phase) is not str:
        return
    row = _PROGRESS_ROWS.get(phase)
    if row is None:
        return
    step, message = row
    prefix = f"[{step}/7]" if step is not None else "[失败]"
    print(f"{prefix} {message}", flush=True)


def _exception_reason(exc: BaseException) -> BaseException:
    """Unwrap urllib's transport wrapper without retaining private exception text."""
    reason = getattr(exc, "reason", None)
    return reason if isinstance(reason, BaseException) else exc


def _safe_transport_failure(exc: BaseException) -> dict[str, str]:
    """Map a transport exception to a fixed privacy-safe stage and class."""
    reason = _exception_reason(exc)
    winerror = getattr(reason, "winerror", None)
    if isinstance(reason, socket.gaierror):
        stage, failure = "dns", "dns_lookup_failed"
    elif isinstance(reason, ssl.SSLError):
        stage, failure = "tls", "tls_handshake_failed"
    elif isinstance(reason, ConnectionResetError) or winerror == 10054:
        stage, failure = "tls", "tls_connection_reset"
    elif isinstance(reason, (socket.timeout, TimeoutError)):
        stage, failure = "tcp", "connect_timeout"
    elif isinstance(reason, ConnectionRefusedError) or winerror == 10061:
        stage, failure = "tcp", "connect_refused"
    elif isinstance(reason, OSError):
        stage, failure = "tcp", "transport_os_error"
    else:
        stage, failure = "unknown", "transport_unknown"
    return {
        "error_type": type(reason).__name__,
        "stage": stage,
        "failure_class": failure,
    }


def _curl_transport_failure(exit_code: int) -> tuple[str, str]:
    """Translate stable curl exit codes without persisting localized stderr."""
    if exit_code == 6:
        return "dns", "dns_lookup_failed"
    if exit_code in {7}:
        return "tcp", "connect_failed"
    if exit_code in {28}:
        return "tcp", "connect_timeout"
    if exit_code in {35, 56}:
        return "tls", "tls_connection_reset"
    if exit_code in {51, 58, 59, 60, 66, 77, 83, 90, 91}:
        return "tls", "tls_handshake_failed"
    return "unknown", "transport_unknown"


def _canonical_dns_ip(value: str) -> str | None:
    """Canonicalize only answers for the fixed service hostname.

    Private, loopback and benchmark-range answers are intentionally preserved:
    seeing one for a public activation hostname is evidence of a fake-IP resolver,
    local interception or DNS injection, not private adapter metadata.
    """
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    return str(address)


def inspect_system_dns(host: str, *, attempts: int = 3) -> dict[str, Any]:
    """Repeat system resolution so short-lived answer drift is visible."""
    snapshots: list[tuple[str, ...]] = []
    error_types: list[str] = []
    for _ in range(max(1, attempts)):
        try:
            answers = sorted(
                {
                    address
                    for item in socket.getaddrinfo(host, 443)
                    if (address := _canonical_dns_ip(str(item[4][0]))) is not None
                }
            )
            snapshots.append(tuple(answers))
        except Exception as exc:  # external resolver boundary
            error_types.append(type(_exception_reason(exc)).__name__)
    successful = [snapshot for snapshot in snapshots if snapshot]
    answers = sorted({answer for snapshot in successful for answer in snapshot})
    if not successful:
        status = "lookup_failed"
    elif len(set(successful)) > 1:
        status = "answer_changed"
    else:
        status = "stable"
    return {
        "status": status,
        "answers": answers,
        "attempts": max(1, attempts),
        "successful_attempts": len(successful),
        "error_types": sorted(set(error_types)),
    }


def inspect_trusted_dns(host: str, *, timeout: float = 4.0) -> dict[str, Any]:
    """Query a direct-IP HTTPS DNS reference without following redirects."""
    if certifi is None:
        return {"status": "unavailable", "answers": [], "failure_class": "certifi_unavailable"}
    url = (
        TRUSTED_DNS_URL
        + "?name="
        + urllib.parse.quote(host, safe="")
        + "&type=A"
    )
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/dns-json",
            },
            method="GET",
        )
        with _open_no_redirect(request, context=context, timeout=timeout) as response:
            if response.geturl() != url or int(response.status) != 200:
                return {
                    "status": "unavailable",
                    "answers": [],
                    "failure_class": "reference_http_unavailable",
                }
            payload = json.loads(response.read(16384).decode("utf-8"))
        answers = sorted(
            {
                address
                for row in payload.get("Answer", [])
                if isinstance(row, dict)
                and (
                    address := _canonical_dns_ip(str(row.get("data") or ""))
                ) is not None
            }
        )
        return {
            "status": "available" if answers else "no_answer",
            "answers": answers,
        }
    except Exception as exc:  # external reference must never leak exception text
        failure = _safe_transport_failure(exc)
        return {
            "status": "unavailable",
            "answers": [],
            "failure_class": failure["failure_class"],
            "error_type": failure["error_type"],
        }


def compare_dns(system: dict[str, Any], reference: dict[str, Any]) -> str:
    """Classify resolver agreement without claiming a network root cause."""
    if system.get("status") == "answer_changed":
        return "dns_answer_unstable_or_suspect"
    system_answers = set(system.get("answers") or [])
    reference_answers = set(reference.get("answers") or [])
    if system_answers and reference_answers:
        if system_answers.isdisjoint(reference_answers):
            return "reference_mismatch"
        return "consistent"
    if not system_answers:
        return "system_dns_unavailable"
    return "reference_unavailable"


def inspect_local_activation() -> dict[str, Any]:
    """Load only the product's privacy-safe local cache classification."""
    package_root = Path(__file__).resolve().parent.parent
    source_root = package_root / "src"
    if source_root.is_dir() and str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    try:
        from bidking_lab.secure.client_activate import inspect_local_activation_state

        return inspect_local_activation_state()
    except Exception:
        # Exception text can contain private local paths/usernames. The diagnostic
        # is routinely sent to support, so expose only a fixed classification.
        return {"cache_state": "inspection_error"}


def _safe_history_epoch(value: Any) -> int | None:
    if type(value) is int and 0 <= value <= _MAX_SAFE_REPORT_EPOCH:
        return value
    return None


def _sanitize_activation_recheck_history(value: Any) -> dict[str, Any]:
    """Re-project the client ring at the diagnostic output boundary."""
    empty = {"schema": 1, "sequences": []}
    if (
        type(value) is not dict
        or any(type(key) is not str for key in dict.keys(value))
        or frozenset(dict.keys(value)) != frozenset({"schema", "sequences"})
        or dict.get(value, "schema") != 1
        or type(dict.get(value, "sequences")) is not list
        or len(dict.get(value, "sequences")) > 4
    ):
        return empty
    safe_sequences: list[dict[str, Any]] = []
    sequence_keys = frozenset(
        {
            "started_at",
            "last_failure_at",
            "recovered_at",
            "attempt_count",
            "attempts",
        }
    )
    attempt_keys = frozenset({"at", "endpoint", "stage", "failure_class"})
    for raw_sequence in dict.get(value, "sequences"):
        if (
            type(raw_sequence) is not dict
            or any(type(key) is not str for key in dict.keys(raw_sequence))
            or frozenset(dict.keys(raw_sequence)) != sequence_keys
        ):
            return empty
        started_at = _safe_history_epoch(dict.get(raw_sequence, "started_at"))
        last_failure_at = _safe_history_epoch(
            dict.get(raw_sequence, "last_failure_at")
        )
        raw_recovered_at = dict.get(raw_sequence, "recovered_at")
        recovered_at = (
            None
            if raw_recovered_at is None
            else _safe_history_epoch(raw_recovered_at)
        )
        attempt_count = dict.get(raw_sequence, "attempt_count")
        raw_attempts = dict.get(raw_sequence, "attempts")
        if (
            started_at is None
            or last_failure_at is None
            or last_failure_at < started_at
            or (raw_recovered_at is not None and recovered_at is None)
            or (recovered_at is not None and recovered_at < last_failure_at)
            or type(attempt_count) is not int
            or not 1 <= attempt_count <= 64
            or type(raw_attempts) is not list
            or not 1 <= len(raw_attempts) <= 12
            or attempt_count < len(raw_attempts)
        ):
            return empty
        attempts: list[dict[str, Any]] = []
        for raw_attempt in raw_attempts:
            if (
                type(raw_attempt) is not dict
                or any(type(key) is not str for key in dict.keys(raw_attempt))
                or frozenset(dict.keys(raw_attempt)) != attempt_keys
            ):
                return empty
            at = _safe_history_epoch(dict.get(raw_attempt, "at"))
            endpoint = dict.get(raw_attempt, "endpoint")
            stage = dict.get(raw_attempt, "stage")
            failure_class = dict.get(raw_attempt, "failure_class")
            if (
                at is None
                or type(endpoint) is not str
                or endpoint not in _RECHECK_HISTORY_ENDPOINTS
                or type(stage) is not str
                or stage not in _RECHECK_HISTORY_STAGES
                or type(failure_class) is not str
                or failure_class not in _RECHECK_HISTORY_FAILURES
            ):
                return empty
            attempts.append(
                {
                    "at": at,
                    "endpoint": endpoint,
                    "stage": stage,
                    "failure_class": failure_class,
                }
            )
        attempt_times = [attempt["at"] for attempt in attempts]
        if (
            any(
                at < started_at or at > last_failure_at
                for at in attempt_times
            )
            or any(
                previous > current
                for previous, current in zip(
                    attempt_times, attempt_times[1:]
                )
            )
            or attempt_times[-1] != last_failure_at
            or (
                attempt_count == len(attempts)
                and attempt_times[0] != started_at
            )
            or (attempt_count > len(attempts) and len(attempts) != 12)
        ):
            return empty
        safe_sequences.append(
            {
                "started_at": started_at,
                "last_failure_at": last_failure_at,
                "recovered_at": recovered_at,
                "attempt_count": attempt_count,
                "attempts": attempts,
            }
        )
    return {"schema": 1, "sequences": safe_sequences}


def inspect_activation_recheck_history() -> dict[str, Any]:
    """Load only the fixed privacy-safe cross-recovery recheck summary."""
    package_root = Path(__file__).resolve().parent.parent
    source_root = package_root / "src"
    if source_root.is_dir() and str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    try:
        from bidking_lab.secure.client_activate import (
            read_activation_recheck_history,
        )

        return _sanitize_activation_recheck_history(
            read_activation_recheck_history()
        )
    except Exception:
        return {"schema": 1, "sequences": []}


def classify(
    results: list[dict[str, Any]], dns: dict[str, Any] | None = None
) -> str:
    if not BASES:
        return "ENDPOINT_CONFIGURATION_INVALID"

    def transport_ready(transport: str) -> bool:
        return any(
            all(
                any(
                    row.get("transport") == transport
                    and row.get("name") == name
                    and row.get("endpoint") == endpoint
                    and row.get("ok") is True
                    for row in results
                )
                for endpoint in ("health", "activation_readiness")
            )
            for name, _base in BASES
        )

    if transport_ready("packaged_python"):
        return "PACKAGED_PYTHON_ACTIVATION_READY"
    if transport_ready("windows_curl"):
        return "PACKAGED_PYTHON_FAIL_WINDOWS_CURL_ACTIVATION_READY"
    if any(row.get("endpoint") == "health" and row.get("ok") is True for row in results):
        return "ACTIVATION_SERVICE_NOT_READY"
    failures = transport_failure_classes(results, dns or {})
    for failure, classification in (
        ("dns_answer_unstable_or_suspect", "DNS_ANSWER_UNSTABLE_OR_SUSPECT"),
        ("tls_connection_reset", "TLS_CONNECTION_RESET"),
        ("connect_timeout", "CONNECT_TIMEOUT"),
        ("http_reachable_service_rejected", "HTTP_REACHABLE_SERVICE_REJECTED"),
        ("http_reachable_invalid_payload", "HTTP_REACHABLE_INVALID_PAYLOAD"),
        ("dns_lookup_failed", "DNS_LOOKUP_FAILED"),
    ):
        if failure in failures:
            return classification
    return "ALL_TESTED_HTTPS_PATHS_FAILED"


def activation_support_guidance(
    classification: Any, local_activation: Any = None,
) -> dict[str, str]:
    """Keep local recovery and independent transport actions privacy-safe."""

    malformed = bool(
        type(local_activation) is dict
        and type(local_activation.get("cache_state")) is str
        and local_activation["cache_state"] == "malformed"
    )
    if malformed:
        local_message = (
            "本机激活缓存异常；请关闭软件并重启。"
            "仍异常请导出激活诊断联系支持，不要反复清除或更换 token。"
        )
        transport = activation_support_guidance(classification)
        if transport["message"]:
            local_message += transport["message"]
        elif type(classification) is str and classification in {
            "ALL_TESTED_HTTPS_PATHS_FAILED", "DNS_ANSWER_UNSTABLE_OR_SUSPECT",
            "TLS_CONNECTION_RESET", "CONNECT_TIMEOUT", "DNS_LOOKUP_FAILED",
            "HTTP_REACHABLE_SERVICE_REJECTED", "HTTP_REACHABLE_INVALID_PAYLOAD",
            "ACTIVATION_SERVICE_NOT_READY",
        }:
            local_message += "激活线路也未就绪；请同时按网络诊断结果排查连接或联系支持。"
        return {"code": "local_activation_cache_malformed", "message": local_message}

    if (
        type(classification) is str
        and classification
        == "PACKAGED_PYTHON_FAIL_WINDOWS_CURL_ACTIVATION_READY"
    ):
        return {
            "code": "system_proxy_tls_path_incompatible",
            "message": (
                "系统代理/TLS路径不兼容；可关闭非必要代理，"
                "或由客户端直连重试。"
            ),
        }
    return {"code": "no_fixed_action", "message": ""}


def transport_failure_classes(
    results: list[dict[str, Any]], dns: dict[str, Any]
) -> list[str]:
    """Return stable actionable classes while preserving multi-path evidence."""
    classes: list[str] = []
    dns_comparisons = {
        row.get("comparison")
        for row in dns.values()
        if isinstance(row, dict)
    }
    if (
        "dns_answer_unstable_or_suspect" in dns_comparisons
        or (
            "reference_mismatch" in dns_comparisons
            and not any(row.get("ok") is True for row in results)
        )
    ):
        classes.append("dns_answer_unstable_or_suspect")
    for row in results:
        failure = row.get("failure_class")
        if isinstance(failure, str) and failure and failure not in classes:
            classes.append(failure)
    return classes


def _report_enum(value: Any, allowed: frozenset[str], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def _report_count(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple, set, frozenset)) else 0


def _sanitize_probe_row(row: dict[str, Any]) -> dict[str, Any]:
    """Keep only fixed classifications needed by support.

    Probe URLs and exception details are deliberately excluded.  `build_report`
    runs only the fixed endpoints, but the resulting report is routinely shared
    with support and must remain safe even if an external boundary returns an
    unexpected value.
    """
    safe: dict[str, Any] = {
        "name": _report_enum(
            row.get("name"),
            _DIAGNOSTIC_ENDPOINT_IDS,
            "unknown",
        ),
        "transport": _report_enum(
            row.get("transport"),
            frozenset({"packaged_python", "windows_curl"}),
            "unknown",
        ),
        "endpoint": _report_enum(
            row.get("endpoint"),
            frozenset({"health", "activation_readiness"}),
            "unknown",
        ),
        "ok": row.get("ok") is True,
        "stage": _report_enum(row.get("stage"), _REPORT_PROBE_STAGES, "unknown"),
    }
    if type(row.get("status")) is int and 0 <= row["status"] <= 999:
        safe["status"] = row["status"]
    if type(row.get("payload_valid")) is bool:
        safe["payload_valid"] = row["payload_valid"]
    failure = row.get("failure_class")
    if failure is not None:
        safe["failure_class"] = _report_enum(
            failure,
            _REPORT_PROBE_FAILURES,
            "transport_unknown",
        )
    return safe


def summarize_transport_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate only fixed DNS/TCP/TLS/HTTP stages and bounded counts."""
    safe_rows = [
        _sanitize_probe_row(row)
        for row in results
        if isinstance(row, dict)
    ]
    stage_counts = {
        "dns": 0,
        "tcp": 0,
        "tls": 0,
        "http": 0,
        "unknown": 0,
    }
    success_count = 0
    http_response_count = 0
    network_failure_count = 0
    for row in safe_rows:
        stage = row.get("stage")
        if row.get("ok") is True:
            success_count += 1
            http_response_count += 1
            continue
        if stage == "http":
            stage_counts["http"] += 1
            http_response_count += 1
            continue
        safe_stage = stage if stage in stage_counts else "unknown"
        stage_counts[safe_stage] += 1
        network_failure_count += 1
    return {
        "probe_count": len(safe_rows),
        "success_count": success_count,
        "http_response_count": http_response_count,
        "network_failure_count": network_failure_count,
        "stage_counts": stage_counts,
    }


def classify_activation_availability(
    results: list[dict[str, Any]],
    local_activation: dict[str, Any],
) -> str:
    """Separate online reachability from credential validity without exposing either."""
    summary = summarize_transport_results(results)
    if summary["success_count"] > 0:
        return "online_recheck_available"
    if summary["http_response_count"] > 0:
        return "activation_service_http_reachable"
    if summary["probe_count"] <= 0:
        return "availability_unknown"
    if isinstance(local_activation, dict) and (
        local_activation.get("credential_available") is True
    ):
        return "credential_online_recheck_failed"
    return "cold_start_all_endpoints_failed"


def _sanitize_dns_report(dns: dict[str, Any]) -> dict[str, Any]:
    """Reduce DNS observations to status and counts; never persist answers/hosts."""
    safe: dict[str, Any] = {}
    for name, _base in BASES:
        row = dns.get(name)
        if not isinstance(row, dict):
            safe[name] = {
                "system": {
                    "status": "unavailable",
                    "answer_count": 0,
                    "attempts": 0,
                    "successful_attempts": 0,
                    "error_count": 0,
                },
                "trusted_reference": {"status": "unavailable", "answer_count": 0},
                "comparison": "reference_unavailable",
            }
            continue
        system = row.get("system") if isinstance(row.get("system"), dict) else {}
        reference = (
            row.get("trusted_reference")
            if isinstance(row.get("trusted_reference"), dict)
            else {}
        )
        attempts = system.get("attempts")
        successful = system.get("successful_attempts")
        safe[name] = {
            "system": {
                "status": _report_enum(
                    system.get("status"),
                    _REPORT_DNS_STATUSES,
                    "unavailable",
                ),
                "answer_count": _report_count(system.get("answers")),
                "attempts": attempts if type(attempts) is int and attempts >= 0 else 0,
                "successful_attempts": (
                    successful
                    if type(successful) is int and successful >= 0
                    else 0
                ),
                "error_count": _report_count(system.get("error_types")),
            },
            "trusted_reference": {
                "status": _report_enum(
                    reference.get("status"),
                    _REPORT_DNS_STATUSES,
                    "unavailable",
                ),
                "answer_count": _report_count(reference.get("answers")),
            },
            "comparison": _report_enum(
                row.get("comparison"),
                _REPORT_DNS_COMPARISONS,
                "reference_unavailable",
            ),
        }
    return safe


def _probe_payload_ok(body: str, endpoint: str) -> bool:
    """公开探针只接受精确布尔值 true；readiness 还必须标明 activation。"""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return False
    return endpoint != "activation_readiness" or payload.get("service") == "activation"


def _health_payload_ok(body: str) -> bool:
    """Backward-compatible helper for the health-only contract tests."""
    return _probe_payload_ok(body, "health")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never let a diagnostic probe leave its fixed allowlisted path."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_no_redirect(request: urllib.request.Request, *, context, timeout: float):
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context),
        _NoRedirectHandler(),
    )
    return opener.open(request, timeout=timeout)


def check_python(name: str, endpoint: str, url: str) -> dict[str, Any]:
    started = time.perf_counter()
    row: dict[str, Any] = {
        "name": name,
        "transport": "packaged_python",
        "endpoint": endpoint,
        "url": url,
        "ok": False,
    }
    try:
        if certifi is None:
            raise RuntimeError("certifi is unavailable")
        context = ssl.create_default_context(cafile=certifi.where())
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            method="GET",
        )
        with _open_no_redirect(request, timeout=8.0, context=context) as response:
            if response.geturl() != url:
                raise RuntimeError("diagnostic final URL mismatch")
            body = response.read(4096).decode("utf-8", errors="replace")
            row.update(
                status=int(response.status),
                ok=response.status == 200 and _probe_payload_ok(body, endpoint),
                payload_valid=_probe_payload_ok(body, endpoint),
            )
            row["stage"] = "success" if row["ok"] else "http"
            if not row["ok"]:
                row["failure_class"] = "http_reachable_invalid_payload"
    except urllib.error.HTTPError as exc:
        row.update(
            status=int(exc.code),
            stage="http",
            failure_class="http_reachable_service_rejected",
            error_type="HTTPError",
        )
    except Exception as exc:  # external boundary: fixed classification only
        row.update(_safe_transport_failure(exc))
    row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return row


def _curl_version(curl_path: str) -> str:
    try:
        completed = subprocess.run(
            [curl_path, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        return (completed.stdout or completed.stderr).splitlines()[0][:240]
    except Exception as exc:
        return f"unavailable:{type(_exception_reason(exc)).__name__}"


def check_curl(name: str, endpoint: str, url: str, curl_path: str) -> dict[str, Any]:
    started = time.perf_counter()
    row: dict[str, Any] = {
        "name": name,
        "transport": "windows_curl",
        "endpoint": endpoint,
        "url": url,
        "ok": False,
    }
    output_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
            output_path = tmp.name
        completed = subprocess.run(
            [
                curl_path,
                "--disable",
                "--silent",
                "--show-error",
                "--proto",
                "=https",
                "--max-redirs",
                "0",
                "--max-time",
                "8",
                "--output",
                output_path,
                "--write-out",
                "%{http_code}",
                "--header",
                f"User-Agent: {USER_AGENT}",
                "--header",
                "Accept: application/json",
                url,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            check=False,
        )
        body = Path(output_path).read_text(encoding="utf-8", errors="replace")
        status_text = completed.stdout.strip()
        status = int(status_text) if status_text.isdigit() else 0
        row.update(
            exit_code=completed.returncode,
            status=status,
            ok=completed.returncode == 0 and status == 200 and _probe_payload_ok(body, endpoint),
            payload_valid=_probe_payload_ok(body, endpoint),
        )
        if row["ok"]:
            row["stage"] = "success"
        elif status > 0:
            row.update(
                stage="http",
                failure_class=(
                    "http_reachable_invalid_payload"
                    if status == 200
                    else "http_reachable_service_rejected"
                ),
            )
        else:
            stage, failure = _curl_transport_failure(completed.returncode)
            row.update(stage=stage, failure_class=failure)
    except Exception as exc:
        row.update(_safe_transport_failure(exc))
    finally:
        if output_path:
            try:
                Path(output_path).unlink()
            except FileNotFoundError:
                pass
    row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return row


def _windows_powershell_path() -> str:
    return os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )


def inspect_windows_network_state() -> dict[str, Any]:
    """Return only aggregated NCSI/route/DNS facts, never interface identities."""
    if sys.platform != "win32":
        return {"status": "unavailable", "reason": "not_windows"}
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$configs = @(Get-NetIPConfiguration -ErrorAction Stop)
$profiles = @(Get-NetConnectionProfile -ErrorAction Stop)
$active = 0
$route4 = 0
$route6 = 0
$dns4 = 0
$dns6 = 0
foreach ($cfg in $configs) {
  if ($null -ne $cfg.NetAdapter -and [string]$cfg.NetAdapter.Status -eq 'Up') {
    $active += 1
  }
  if (@($cfg.IPv4DefaultGateway).Count -gt 0) { $route4 += 1 }
  if (@($cfg.IPv6DefaultGateway).Count -gt 0) { $route6 += 1 }
  $hasDns4 = $false
  $hasDns6 = $false
  foreach ($raw in @($cfg.DNSServer.ServerAddresses)) {
    $parsed = $null
    if ([System.Net.IPAddress]::TryParse([string]$raw, [ref]$parsed)) {
      if ($parsed.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork) {
        $hasDns4 = $true
      } elseif ($parsed.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetworkV6) {
        $hasDns6 = $true
      }
    }
  }
  if ($hasDns4) { $dns4 += 1 }
  if ($hasDns6) { $dns6 += 1 }
}
$payload = [pscustomobject]@{
  active_adapter_count = $active
  ipv4_default_route_count = $route4
  ipv6_default_route_count = $route6
  dns_ipv4_adapter_count = $dns4
  dns_ipv6_adapter_count = $dns6
  ncsi_profile_count = $profiles.Count
  ncsi_ipv4_internet = @($profiles | Where-Object {
    [string]$_.IPv4Connectivity -eq 'Internet'
  }).Count -gt 0
  ncsi_ipv6_internet = @($profiles | Where-Object {
    [string]$_.IPv6Connectivity -eq 'Internet'
  }).Count -gt 0
}
$payload | ConvertTo-Json -Compress
"""
    try:
        completed = subprocess.run(
            [
                _windows_powershell_path(),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            return {"status": "unavailable", "reason": "query_failed"}
        payload = json.loads(completed.stdout.strip())
        if not isinstance(payload, dict):
            raise ValueError("network state is not a mapping")
        integer_keys = (
            "active_adapter_count",
            "ipv4_default_route_count",
            "ipv6_default_route_count",
            "dns_ipv4_adapter_count",
            "dns_ipv6_adapter_count",
            "ncsi_profile_count",
        )
        bool_keys = ("ncsi_ipv4_internet", "ncsi_ipv6_internet")
        if any(type(payload.get(key)) is not int or payload[key] < 0 for key in integer_keys):
            raise ValueError("invalid network counts")
        if any(type(payload.get(key)) is not bool for key in bool_keys):
            raise ValueError("invalid NCSI state")
        return {
            "status": "available",
            **{key: payload[key] for key in integer_keys},
            **{key: payload[key] for key in bool_keys},
        }
    except Exception:
        return {"status": "unavailable", "reason": "query_failed"}


def _read_wininet_settings() -> dict[str, Any]:
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
    ) as key:
        values: dict[str, Any] = {}
        for name in ("ProxyEnable", "AutoConfigURL", "AutoDetect"):
            try:
                values[name] = winreg.QueryValueEx(key, name)[0]
            except FileNotFoundError:
                values[name] = None
        return values


def _parse_winhttp_proxy_mode(output: str) -> str | None:
    """Parse only complete, locale-consistent ``netsh winhttp`` output."""
    if not isinstance(output, str):
        return None
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    normalized = [line.casefold().lstrip("\ufeff") for line in lines]
    english_headers = [
        index
        for index, line in enumerate(normalized)
        if line == "current winhttp proxy settings:"
    ]
    chinese_headers = [
        index
        for index, line in enumerate(normalized)
        if re.fullmatch(r"当前(?:的)?\s*winhttp\s*代理服务器设置[:：]", line)
    ]
    if len(english_headers) + len(chinese_headers) != 1:
        return None

    if english_headers:
        header_index = english_headers[0]
        direct_indexes = [
            index
            for index, line in enumerate(normalized)
            if line == "direct access (no proxy server)."
        ]
        proxy_indexes = [
            index
            for index, line in enumerate(normalized)
            if re.fullmatch(r"proxy server\(s\)\s*:\s*\S.*", line)
        ]
        auxiliary_indexes = [
            index
            for index, line in enumerate(normalized)
            if re.fullmatch(r"bypass list\s*:\s*\S.*", line)
        ]
    else:
        header_index = chinese_headers[0]
        direct_indexes = [
            index
            for index, line in enumerate(normalized)
            if line in {
                "直接访问(没有代理服务器)。",
                "直接访问（没有代理服务器）。",
            }
        ]
        proxy_indexes = [
            index
            for index, line in enumerate(normalized)
            if re.fullmatch(r"代理服务器(?:\(s\)|（s）)?\s*[:：]\s*\S.*", line)
        ]
        auxiliary_indexes = [
            index
            for index, line in enumerate(normalized)
            if re.fullmatch(r"绕过列表\s*[:：]\s*\S.*", line)
        ]

    mode_indexes = direct_indexes + proxy_indexes
    if len(mode_indexes) != 1 or mode_indexes[0] <= header_index:
        return None
    if direct_indexes:
        if len(normalized) != 2 or header_index != 0 or direct_indexes != [1]:
            return None
    else:
        expected_indexes = [header_index, proxy_indexes[0], *auxiliary_indexes]
        if expected_indexes not in ([0, 1], [0, 1, 2]):
            return None
        if len(normalized) != len(expected_indexes):
            return None
    return "direct" if direct_indexes else "proxy"


def inspect_windows_proxy_state() -> dict[str, Any]:
    """Summarize proxy modes without exposing proxy/PAC addresses."""
    def unavailable(reason: str) -> dict[str, str]:
        return {"status": "unavailable", "mode": "unknown", "reason": reason}

    if sys.platform != "win32":
        return {
            "wininet": unavailable("not_windows"),
            "winhttp": unavailable("not_windows"),
        }
    try:
        values = _read_wininet_settings()
        if not isinstance(values, dict):
            raise ValueError("WinINET settings are not a mapping")
        proxy_enable = values.get("ProxyEnable")
        auto_config = values.get("AutoConfigURL")
        auto_detect_value = values.get("AutoDetect")
        if proxy_enable is not None and (
            type(proxy_enable) is not int or proxy_enable not in {0, 1}
        ):
            raise ValueError("invalid ProxyEnable")
        if auto_config is not None and not isinstance(auto_config, str):
            raise ValueError("invalid AutoConfigURL")
        if auto_detect_value is not None and (
            type(auto_detect_value) is not int or auto_detect_value not in {0, 1}
        ):
            raise ValueError("invalid AutoDetect")
        proxy_enabled = type(values.get("ProxyEnable")) is int and values["ProxyEnable"] == 1
        auto_configured = bool(
            isinstance(values.get("AutoConfigURL"), str)
            and values["AutoConfigURL"].strip()
        )
        auto_detect = type(values.get("AutoDetect")) is int and values["AutoDetect"] == 1
        if proxy_enabled and (auto_configured or auto_detect):
            wininet_mode = "proxy_and_auto"
        elif proxy_enabled:
            wininet_mode = "proxy"
        elif auto_configured or auto_detect:
            wininet_mode = "auto"
        else:
            wininet_mode = "direct"
        wininet = {"status": "available", "mode": wininet_mode}
    except Exception:
        wininet = unavailable("query_failed")

    try:
        completed = subprocess.run(
            ["netsh", "winhttp", "show", "proxy"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=6,
            check=False,
        )
        winhttp_mode = _parse_winhttp_proxy_mode(
            completed.stdout + "\n" + completed.stderr
        )
        if completed.returncode != 0 or winhttp_mode is None:
            winhttp = unavailable("query_failed")
        else:
            winhttp = {"status": "available", "mode": winhttp_mode}
    except Exception:
        winhttp = unavailable("query_failed")
    return {"wininet": wininet, "winhttp": winhttp}


def build_report() -> dict[str, Any]:
    curl_path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe")
    dns: dict[str, Any] = {}
    _emit_progress("dns")
    for name, base in BASES:
        host = urllib.parse.urlparse(base).hostname or ""
        system = inspect_system_dns(host)
        reference = inspect_trusted_dns(host)
        dns[name] = {
            "host": host,
            "system": system,
            "trusted_reference": reference,
            "comparison": compare_dns(system, reference),
        }

    results: list[dict[str, Any]] = []
    _emit_progress("python_https")
    for name, endpoint, url in ENDPOINTS:
        results.append(check_python(name, endpoint, url))
    _emit_progress("windows_https")
    for name, endpoint, url in ENDPOINTS:
        results.append(check_curl(name, endpoint, url, curl_path))

    _emit_progress("local_state")
    local_activation = inspect_local_activation()
    curl_version = _curl_version(curl_path)
    windows_network = inspect_windows_network_state()
    windows_proxy = inspect_windows_proxy_state()
    recent_activation_rechecks = inspect_activation_recheck_history()

    _emit_progress("summarize")
    safe_dns = _sanitize_dns_report(dns)
    safe_results = [_sanitize_probe_row(row) for row in results]
    classification = classify(results, dns)
    proxy_keys = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    )
    return {
        "diagnostic": "BidKing activation transport (no credentials sent)",
        "endpoint_configuration": {
            "status": _ENDPOINT_CONFIG_STATUS,
            "generation": (
                _ENDPOINT_CONFIG.generation if _ENDPOINT_CONFIG else None
            ),
            "endpoint_3_enabled": (
                _ENDPOINT_CONFIG.endpoint_3_enabled if _ENDPOINT_CONFIG else False
            ),
        },
        "python": sys.version.split()[0],
        "openssl": ssl.OPENSSL_VERSION,
        "certifi_present": certifi is not None,
        "curl": curl_version,
        "proxy_env_keys_present": sorted(key for key in proxy_keys if os.environ.get(key)),
        "windows_network": windows_network,
        "windows_proxy": windows_proxy,
        "local_activation": local_activation,
        "recent_activation_rechecks": recent_activation_rechecks,
        "dns": safe_dns,
        "results": safe_results,
        "transport_summary": summarize_transport_results(safe_results),
        "activation_availability": classify_activation_availability(
            safe_results,
            local_activation,
        ),
        "classification": classification,
        "support_guidance": activation_support_guidance(classification, local_activation),
        "transport_failure_classes": transport_failure_classes(safe_results, safe_dns),
    }


def main() -> int:
    _emit_progress("start")
    try:
        report = build_report()
        output = Path.cwd() / OUTPUT_NAME
        payload = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
        output.write_text(payload, encoding="utf-8")
    except Exception:
        _emit_progress("failed")
        return 1
    print(payload, end="")
    _emit_progress("complete")
    print(f"报告文件：{OUTPUT_NAME}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
