from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import activation_core as core  # noqa: E402
import activation_server as server  # noqa: E402
import sample_collector_server as collector  # noqa: E402


SECRET = b"activation-readiness-pet-contention-secret"
MASTER_KEY = "S0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0s="


def _service(tmp_path: Path) -> server.ActivationService:
    application = tmp_path / "application"
    authority = tmp_path / "authority"
    application.mkdir()
    authority.mkdir()
    database = authority / "pet_usage.sqlite3"
    server.initialize_pet_usage_database(
        database,
        secret=SECRET,
        legacy_paths=(
            application / "pet_usage.json",
            application / "pet_usage.anchor.jsonl",
        ),
    )
    return server.ActivationService(
        secret=SECRET,
        master_key_b64=MASTER_KEY,
        bindings_path=application / "bindings.json",
        revoked_path=application / "revoked.txt",
        telemetry_path=application / "telemetry.jsonl",
        trials_path=application / "trials.json",
        paid_starts_path=application / "paid_starts.json",
        pet_usage_db_path=database,
        pet_usage_path=application / "pet_usage.json",
    )


def _token(uid: str) -> str:
    issued_at = int(time.time()) - 1
    return core.mint_token(
        uid,
        SECRET,
        iat=issued_at,
        exp=issued_at + 30 * 86_400,
    )


def _request(
    base_url: str,
    path: str,
    body: dict | None = None,
) -> tuple[int, dict, float]:
    encoded = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310
        f"{base_url}{path}",
        data=encoded,
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return (
                response.status,
                json.loads(response.read()),
                time.monotonic() - started,
            )
    except urllib.error.HTTPError as exc:
        return (
            exc.code,
            json.loads(exc.read()),
            time.monotonic() - started,
        )


def _pet(payload: dict) -> dict:
    lease = json.loads(payload["lease"].rsplit(".", 1)[0])
    return lease["feature_usage"]["pet_companion"]


def test_slow_pet_transaction_does_not_queue_readiness_or_other_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(collector, "_ACT", service)
    monkeypatch.setattr(collector, "_RATE_MAX", 100_000)
    monkeypatch.setattr(
        collector.Handler,
        "log_message",
        lambda self, format, *args: None,
    )

    real_transaction = server._pet_usage_sqlite_transaction
    first_entered = threading.Event()
    release_first = threading.Event()
    choose_first = threading.Lock()
    should_delay = True

    @contextmanager
    def delayed_first_transaction(path: Path, *, secret: bytes, now: int):
        nonlocal should_delay
        with choose_first:
            delay = should_delay
            should_delay = False
        if delay:
            first_entered.set()
            assert release_first.wait(timeout=5)
        with real_transaction(path, secret=secret, now=now) as transaction:
            yield transaction

    monkeypatch.setattr(
        server,
        "_pet_usage_sqlite_transaction",
        delayed_first_transaction,
    )

    httpd = collector.SafeThreadingHTTPServer(
        ("127.0.0.1", 0),
        collector.Handler,
    )
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_port}"
    first_body = {
        "token": _token("pet-contention-first"),
        "device": "PET-CONTENTION-FIRST",
        "pet_usage": {
            "session_id": "pet-contention-session",
            "seq": 0,
            "visible": True,
        },
    }
    second_body = {
        "token": _token("pet-contention-second"),
        "device": "PET-CONTENTION-SECOND",
    }

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(_request, base_url, "/activate", first_body)
            assert first_entered.wait(timeout=3)
            readiness = pool.submit(
                _request,
                base_url,
                "/activation-readiness",
            )
            second = pool.submit(_request, base_url, "/activate", second_body)
            completed, _pending = wait((readiness, second), timeout=1.5)
            completed_before_release = len(completed) == 2
            release_first.set()
            first_result = first.result(timeout=5)
            readiness_result = readiness.result(timeout=5)
            second_result = second.result(timeout=5)

        assert completed_before_release
        assert first_result[0] == 200
        assert _pet(first_result[1])["status"] == "active"
        assert readiness_result[:2] == (
            503,
            {"ok": False, "service": "activation", "reason": "state_error"},
        )
        assert readiness_result[2] < 1.5
        assert second_result[0] == 200
        assert _pet(second_result[1])["status"] == "state_error"
        assert second_result[2] < 1.5

        assert _request(base_url, "/activation-readiness")[:2] == (
            200,
            {"ok": True, "service": "activation"},
        )
        retry_code, retry_payload, _elapsed = _request(
            base_url,
            "/activate",
            second_body,
        )
        assert retry_code == 200
        assert _pet(retry_payload)["status"] == "available"
        assert _pet(retry_payload)["usage_revision"] == 2
    finally:
        release_first.set()
        httpd.shutdown()
        httpd.server_close()
        server_thread.join(timeout=3)
