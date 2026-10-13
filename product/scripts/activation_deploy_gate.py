#!/usr/bin/env python3
"""Validate the exact activation files selected for deployment.

The gate uses only a temporary state directory and synthetic local credentials.  It
never reads production activation state; its only request is an ephemeral loopback
probe against the candidate collector handler.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import importlib.util
import json
import os
import py_compile
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from types import ModuleType


RUNTIME_FILES = (
    "sample_dashboard.py",
    "build_match_records.py",
    "public_stats.py",
    "activation_core.py",
    "membership_card_store.py",
    "sample_history_index.py",
    "upload_lease.py",
    "activation_server.py",
    "activation_http_e2ee.py",
    "quote_preferences.py",
    "upload_identity_store.py",
    "upload_identity_db_admin.py",
    "activation_device_admin.py",
    "sample_collector_server.py",
    "pet_usage_db_admin.py",
    "my_records.py",
)


class CandidateError(RuntimeError):
    """The staged deployment combination is incompatible."""


class CandidateLoading(CandidateError):
    """Only the exact healthz history-loading response is retryable."""


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _read_loopback_json(url: str) -> dict[str, object]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=2) as response:  # noqa: S310 - fixed loopback
            value = json.loads(response.read())
            if response.status != 200 or type(value) is not dict:
                raise CandidateError("candidate runtime probe returned invalid response")
            return value
    except urllib.error.HTTPError as exc:
        try:
            value = json.loads(exc.read())
        except (OSError, ValueError, UnicodeError) as parse_exc:
            raise CandidateError("candidate runtime probe returned HTTP error") from parse_exc
        if (exc.code == 503 and url.endswith("/healthz") and type(value) is dict
                and set(value) == {"ok", "reason", "state"} and value.get("ok") is False
                and value.get("reason") == "history_index_unavailable" and value.get("state") == "loading"):
            raise CandidateLoading("candidate history index is loading") from exc
        reason = value.get("reason") if type(value) is dict else None
        safe_reason = reason if reason in {"state_error", "activation_not_configured"} else "invalid"
        raise CandidateError(
            f"candidate runtime probe returned HTTP {exc.code} ({safe_reason})"
        ) from exc


def _initialize_candidate_membership(server: ModuleType, path: Path) -> None:
    """Use the candidate initializer in an owned process, closing all SQLite handles.

    The initializer's SQLite context commits but does not guarantee immediate
    handle release on Python 3.13; process exit keeps temporary Windows fixture
    cleanup deterministic without changing STORE or its initialization policy.
    """
    source = Path(server.membership_card_store.__file__).resolve()
    command = [sys.executable, "-B", "-c",
               "from pathlib import Path; import membership_card_store, sys; "
               "membership_card_store.MembershipCardStore.initialize(Path(sys.argv[1]))",
               str(path)]
    completed = subprocess.run(command, cwd=source.parent, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    if completed.returncode != 0:
        raise CandidateError("candidate membership fixture initialization failed")


def _candidate_child_stdio_environment() -> dict[str, str]:
    """Chinese candidate logs must survive inherited Windows pipe encodings."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _candidate_history_fixture(state: Path) -> Path:
    """One synthetic duplicate makes legacy-set fallback distinguishable."""
    payload = json.dumps({"session_hash": "deploy-gate-history-dedup", "map_id": 501}).encode() + b"\n"
    (state / "samples.jsonl").write_bytes(payload * 2)
    directory = state.parent / ("private-history-" + state.name)
    directory.mkdir(mode=0o700)
    return directory / "history.sqlite3"


def _verify_candidate_history_database(path: Path) -> None:
    if not path.is_file():
        raise CandidateError("candidate collector did not configure history index")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    try:
        if (connection.execute("PRAGMA application_id").fetchone() != (0x424B4858,)
                or connection.execute("SELECT COUNT(*) FROM seen WHERE kind='main'").fetchone() != (1,)
                or connection.execute("SELECT COUNT(*) FROM rows WHERE kind='main'").fetchone() != (2,)):
            raise CandidateError("candidate history index source/dedup contract failed")
    finally:
        connection.close()


def _close_candidate_history(collector: ModuleType) -> None:
    index = getattr(collector, "_SAMPLE_HISTORY_INDEX", None)
    if index is not None:
        index.close()


def _real_collector_main_smoke(
    candidate_dir: Path,
    server: ModuleType,
    collector: ModuleType,
    *,
    secret: bytes,
    master_key: str,
) -> None:
    """Start the candidate's real CLI/main on isolated synthetic state."""
    with tempfile.TemporaryDirectory(prefix="bidking-activation-runtime-") as raw_tmp:
        state = Path(raw_tmp) / "public-state"
        state.mkdir()
        history_db = _candidate_history_fixture(state)
        activation_key = state / "activation_key.txt"
        master_key_file = state / "master_key.txt"
        master_key_v2_file = state / "master_key_v2.txt"
        device_block_key = state / "device_block_key.txt"
        e2ee_key = state / "activation_http_e2ee_key.txt"
        pet_usage_db = state / "pet_usage.sqlite3"
        membership_db = state / "membership.sqlite3"
        _initialize_candidate_membership(server, membership_db)
        activation_key.write_bytes(secret)
        master_key_file.write_text(master_key, encoding="utf-8")
        master_key_v2_file.write_text(
            base64.b64encode(b"V" * 32).decode("ascii"),
            encoding="utf-8",
        )
        device_block_key.write_bytes(b"D" * 32)
        _write_private_fixture(e2ee_key, base64.b64encode(b"R" * 32) + b"\n")
        server.initialize_pet_usage_database(
            pet_usage_db,
            secret=secret,
            legacy_paths=(
                state / "pet_usage.json",
                state / "pet_usage.anchor.jsonl",
            ),
        )
        port = _unused_loopback_port()
        command = [
            sys.executable,
            "-B",
            str(candidate_dir / "sample_collector_server.py"),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--out",
            str(state / "samples.jsonl"),
            "--history-index-db-file",
            str(history_db),
            "--dashboard-pass-file",
            str(state / "dashboard_pass.missing"),
            "--export-key-file",
            str(state / "export_key.missing"),
            "--version-manifest-file",
            str(state / "version_manifest.missing"),
            "--activation-key-file",
            str(activation_key),
            "--activation-http-e2ee-private-key-file",
            str(e2ee_key),
            "--master-key-file",
            str(master_key_file),
            "--master-key-v2-file",
            str(master_key_v2_file),
            "--bindings-file",
            str(state / "bindings.json"),
            "--membership-store-file",
            str(membership_db),
            "--revoked-file",
            str(state / "revoked.txt"),
            "--device-blocks-file",
            str(state / "device_blocks.json"),
            "--device-block-key-file",
            str(device_block_key),
            "--telemetry-file",
            str(state / "telemetry.jsonl"),
            "--trials-file",
            str(state / "trials.json"),
            "--pet-usage-file",
            str(state / "pet_usage.json"),
            "--pet-usage-db-file",
            str(pet_usage_db),
            "--upload-identity-mode",
            "disabled",
            "--upload-security-epoch",
            "0",
            "--min-client-security-epoch",
            "0",
            "--min-client-version",
            "0.3.1",
            "--v2-min-version",
            "0.2.7",
        ]
        process = subprocess.Popen(  # noqa: S603 - exact candidate argv
            command,
            cwd=candidate_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_candidate_child_stdio_environment(),
        )
        try:
            last_type = "not_ready"
            for _attempt in range(30):
                if process.poll() is not None:
                    raise CandidateError("candidate collector exited before readiness")
                try:
                    health = _read_loopback_json(
                        f"http://127.0.0.1:{port}/healthz"
                    )
                    readiness = _read_loopback_json(
                        f"http://127.0.0.1:{port}/activation-readiness"
                    )
                    if health.get("ok") is True and readiness == {
                        "ok": True,
                        "service": "activation",
                    }:
                        break
                    last_type = "invalid_payload"
                except CandidateLoading:
                    last_type = "history_loading"
                except (OSError, ValueError, urllib.error.URLError) as exc:
                    last_type = type(exc).__name__
                time.sleep(0.2)
            else:
                raise CandidateError(
                    f"candidate collector did not become ready ({last_type})"
                )
            protocol = collector.activation_http_e2ee.ServerProtocol.from_key_file(e2ee_key)
            _verify_candidate_history_database(history_db)
            _verify_candidate_renewal(
                lambda body: _candidate_e2ee_request(collector, protocol, port, body),
                server.activation_core,
                secret=secret,
            )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


def _write_private_fixture(path: Path, payload: bytes) -> None:
    """Create a synthetic private fixture with the production reader's mode."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise CandidateError("cannot create private candidate fixture") from exc
    try:
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("private candidate fixture write did not progress")
            written += count
        os.fsync(descriptor)
    except OSError as exc:
        raise CandidateError("cannot write private candidate fixture") from exc
    finally:
        os.close(descriptor)


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CandidateError(f"cannot load candidate module: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _expect(response, code: int, *, ok: bool, mode: str, reason: str | None = None) -> None:
    actual_code, body = response
    if actual_code != code or body.get("ok") is not ok or body.get("mode") != mode:
        raise CandidateError(f"unexpected {mode} contract")
    if reason is not None and body.get("reason") != reason:
        raise CandidateError(f"unexpected {mode} reason")


def _signed_lease_payload(body: dict) -> dict:
    signed = body.get("lease")
    if not isinstance(signed, str) or "." not in signed:
        raise CandidateError("candidate lease missing")
    try:
        payload = json.loads(signed.rsplit(".", 1)[0])
    except Exception as exc:
        raise CandidateError("candidate lease malformed") from exc
    if not isinstance(payload, dict):
        raise CandidateError("candidate lease payload malformed")
    return payload


def _candidate_e2ee_request(collector: ModuleType, protocol, port: int, body: dict):
    """Synthetic requests to the actual candidate main, never plaintext credentials."""
    module = collector.activation_http_e2ee
    config = module.ActivationHttpE2EEConfig(
        enabled=True, raw_sha256="0" * 64,
        url=f"http://127.0.0.1:{port}/activate-e2ee",
        key_id=protocol.key_id, server_public_key=protocol.public_key,
    )
    raw_request, context = module.build_client_request(config, body)
    request = urllib.request.Request(
        config.url, data=raw_request, headers={"Content-Type": "application/json"}, method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=3) as response:  # noqa: S310 - ephemeral loopback
        if response.status != 200:
            raise CandidateError("candidate renewal E2EE outer status mismatch")
        return module.open_client_response(response.read(), context)


def _verify_candidate_renewal(request, core: ModuleType, *, secret: bytes) -> None:
    """Representative current consumer: first use, 30->90, replay and alias.

    All cards are minted only for this isolated synthetic state. No production
    token is read or emitted, and no business policy is implemented by the gate.
    """
    now, day = int(time.time()), 86400
    account = core.mint_token("deploy-gate-renew-A", secret, iat=now, exp=now + 30 * day)
    card = core.mint_token("deploy-gate-renew-B", secret, iat=now, exp=now + 90 * day)
    ordinary = {"device": "gate-renew", "token": account, "client_version": "0.3.6",
                "client_security_epoch": 1}
    initial = request(ordinary)
    _expect(initial, 200, ok=True, mode="paid")
    first = _signed_lease_payload(initial[1])
    first_pet = first.get("feature_usage", {}).get("pet_companion", {})
    if (initial[1].get("membership_card_capability") !=
            {"schema_version": 1, "pet_quota_schema_version": 2}
            or first.get("plan_kind") != "30d"
            or first.get("membership", {}).get("revision") != 1
            or first["membership"].get("my_records_exp") is not None
            or first["membership"].get("private_dashboard_exp") is not None
            or first_pet.get("kind") != "quota"
            or first_pet.get("total_seconds") != 43_200
            or first_pet.get("remaining_seconds") != 43_200):
        raise CandidateError("candidate configured membership first use failed")
    renewal = {**ordinary, "card_token": card, "card_redemption_schema": 1}
    response = request(renewal)
    _expect(response, 200, ok=True, mode="paid")
    lease = _signed_lease_payload(response[1])
    ack = response[1].get("card_redemption", {})
    expiry = first["paid_exp"] + 90 * day
    membership = lease.get("membership", {})
    pet = lease.get("feature_usage", {}).get("pet_companion", {})
    if (lease.get("uid") != "deploy-gate-renew-A" or lease.get("paid_exp") != expiry
            or lease.get("plan_kind") != "90d"
            or lease.get("feature_entitlements") != {"pet_companion": "full"}
            or pet.get("kind") != "full" or pet.get("status") != "active"
            or pet.get("entitlement_exp") != expiry
            or membership.get("revision") != 2
            or membership.get("canonical_uid") != "deploy-gate-renew-A"
            or membership.get("credential_uid") != "deploy-gate-renew-A"
            or membership.get("my_records_exp") != expiry
            or membership.get("private_dashboard_exp") is not None
            or ack.get("status") != "redeemed" or ack.get("committed") is not True
            or ack.get("credited_seconds") != 90 * day
            or ack.get("membership_exp") != expiry or ack.get("membership_revision") != 2
            or ack.get("pet_quota_added_seconds") != 0 or ack.get("roaming_rebinds_added") != 0
            or type(ack.get("receipt_id")) is not str or not ack["receipt_id"]):
        raise CandidateError("candidate renewal authority contract failed")
    replay = request(renewal)
    _expect(replay, 200, ok=True, mode="paid")
    replay_ack = replay[1].get("card_redemption", {})
    if (replay_ack != {**ack, "status": "replayed"}
            or _signed_lease_payload(replay[1]).get("paid_exp") != expiry):
        raise CandidateError("candidate renewal replay contract failed")
    alias = request({**ordinary, "token": card})
    _expect(alias, 200, ok=True, mode="paid")
    alias_lease = _signed_lease_payload(alias[1])
    if (alias_lease.get("uid") != "deploy-gate-renew-A"
            or alias_lease.get("paid_exp") != expiry
            or alias_lease.get("membership", {}).get("credential_uid") != "deploy-gate-renew-B"
            or alias_lease["membership"].get("revision") != 2):
        raise CandidateError("candidate redeemed credential alias failed")


def _expect_pet_entitlement(
    response,
    *,
    plan_kind: str,
    pet_companion: str,
) -> None:
    code, body = response
    if code != 200 or body.get("ok") is not True:
        raise CandidateError("candidate entitlement activation failed")
    lease = _signed_lease_payload(body)
    if lease.get("plan_kind") != plan_kind or lease.get("feature_entitlements") != {
        "pet_companion": pet_companion,
    }:
        raise CandidateError("candidate entitlement contract failed")


def _expect_upload_lease(server: ModuleType, service, response, *, now: int) -> None:
    code, body = response
    if code != 200 or type(body.get("upload_lease")) is not str:
        raise CandidateError("candidate upload lease missing")
    try:
        payload = server.upload_lease.verify(
            body["upload_lease"],
            service.secret,
            now=now,
        )
    except Exception as exc:
        raise CandidateError("candidate upload lease invalid") from exc
    if payload.get("security_epoch") != 1:
        raise CandidateError("candidate upload lease epoch mismatch")
    allowed, reason, authorized = service.authorize_upload_lease(
        body["upload_lease"],
        now=now,
    )
    if not allowed or reason != "ok" or authorized != payload:
        raise CandidateError("candidate upload lease authority failed")


def _construct_via_candidate_collector(
    collector: ModuleType,
    server: ModuleType,
    state: Path,
    *,
    secret: bytes,
    master_key: str,
    device_block_key_material: bytes | None = None,
    device_block_key_mode: str = "file",
    membership_enabled: bool = True,
    history_enabled: bool = True,
):
    """Exercise the candidate collector's real argparse and service wiring."""
    activation_key = state / "activation_key.txt"
    device_block_key = state / "device_block_key.txt"
    master_key_file = state / "master_key.txt"
    activation_key.write_bytes(secret)
    history_db = _candidate_history_fixture(state) if history_enabled else None
    membership_db = state / "membership.sqlite3"
    if membership_enabled:
        _initialize_candidate_membership(server, membership_db)
    pet_usage_db = state / "pet_usage.sqlite3"
    server.initialize_pet_usage_database(
        pet_usage_db,
        secret=secret,
        legacy_paths=(
            state / "pet_usage.json",
            state / "pet_usage.anchor.jsonl",
        ),
    )
    if device_block_key_mode == "file":
        device_block_key.write_bytes(
            device_block_key_material
            if device_block_key_material is not None
            else b"bidking-deploy-gate-device-block-only"
        )
    elif device_block_key_mode == "unreadable":
        # A directory at the configured file path is a portable, deterministic
        # read failure without weakening host permissions.
        device_block_key.mkdir()
    elif device_block_key_mode != "absent":
        raise CandidateError("unsupported device block key test mode")
    master_key_file.write_text(master_key, encoding="utf-8")
    activation_http_e2ee_key = state / "activation_http_e2ee_key.txt"
    _write_private_fixture(
        activation_http_e2ee_key,
        base64.b64encode(b"E" * 32) + b"\n",
    )
    upload_identity_db = state / "upload_identity.sqlite3"
    collector.upload_identity_store.initialize_upload_identity_database(
        upload_identity_db
    )

    real_http_server = collector.SafeThreadingHTTPServer
    real_load_existing = collector._load_existing
    real_warm_public_cache = collector._warm_public_cache

    class NoopHTTPServer:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def serve_forever(self) -> None:
            pass

    collector.SafeThreadingHTTPServer = NoopHTTPServer
    _close_candidate_history(collector)
    collector._load_existing = real_load_existing if history_enabled else (lambda: None)
    collector._warm_public_cache = lambda: None
    try:
        result = collector.main(
            [
                "sample_collector_server.py",
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--out",
                str(state / "samples.jsonl"),
                *(["--history-index-db-file", str(history_db)] if history_enabled else []),
                "--dashboard-pass-file",
                str(state / "dashboard_pass.missing"),
                "--export-key-file",
                str(state / "export_key.missing"),
                "--version-manifest-file",
                str(state / "version_manifest.missing"),
                "--activation-key-file",
                str(activation_key),
                "--activation-http-e2ee-private-key-file",
                str(activation_http_e2ee_key),
                "--master-key-file",
                str(master_key_file),
                "--master-key-v2-file",
                str(state / "master_key_v2.missing"),
                "--bindings-file",
                str(state / "bindings.json"),
                *(["--membership-store-file", str(membership_db)] if membership_enabled else []),
                "--revoked-file",
                str(state / "revoked.txt"),
                "--device-blocks-file",
                str(state / "device_blocks.json"),
                "--device-block-key-file",
                str(device_block_key),
                "--telemetry-file",
                str(state / "telemetry.jsonl"),
                "--trials-file",
                str(state / "trials.json"),
                "--pet-usage-file",
                str(state / "pet_usage.json"),
                "--pet-usage-db-file",
                str(pet_usage_db),
                "--upload-identity-mode",
                "compat",
                "--upload-security-epoch",
                "1",
                "--upload-identity-db-file",
                str(upload_identity_db),
            ]
        )
    finally:
        collector.SafeThreadingHTTPServer = real_http_server
        collector._load_existing = real_load_existing
        collector._warm_public_cache = real_warm_public_cache

    if result != 0:
        raise CandidateError("candidate collector main returned non-zero")
    if not collector._HISTORY_INDEX_DONE.wait(timeout=5):
        raise CandidateError("candidate history index readiness deadline exceeded")
    if history_enabled:
        index = collector._SAMPLE_HISTORY_INDEX
        if (index is None or index.path != history_db.resolve() or not index.ready
                or collector._history_index_state() != "ready"
                or collector._SEEN or collector._QUAR_SEEN
                or not collector._history_contains("main", "deploy-gate-history-dedup")):
            raise CandidateError("candidate collector history index wiring/fallback failed")
        _verify_candidate_history_database(history_db)
    elif collector._HISTORY_INDEX_DB_FILE is not None or collector._SAMPLE_HISTORY_INDEX is not None:
        raise CandidateError("candidate collector did not preserve omitted legacy history mode")
    service = collector._ACT
    if not isinstance(service, server.ActivationService):
        raise CandidateError("candidate collector did not construct candidate ActivationService")
    if membership_enabled and (
        service.membership_store is None or service.membership_store.path != membership_db
    ):
        raise CandidateError("candidate collector did not configure candidate membership store")
    if not membership_enabled and service.membership_store is not None:
        raise CandidateError("candidate collector did not preserve unconfigured legacy mode")
    protocol = collector._ACTIVATION_HTTP_E2EE
    if not isinstance(protocol, collector.activation_http_e2ee.ServerProtocol):
        raise CandidateError("candidate collector did not construct HTTP E2EE protocol")
    return service, real_http_server


def _verify_invalid_device_block_key(
    server: ModuleType,
    service,
    state: Path,
    *,
    secret: bytes,
    label: str,
) -> None:
    if service.device_block_key_state not in {"invalid", "shared_activation_secret"}:
        raise CandidateError(f"device block key {label} classification failed")
    if server.activation_readiness(service) != (
        503,
        {"ok": False, "service": "activation", "reason": "state_error"},
    ):
        raise CandidateError(f"device block key {label} readiness failed")
    token = server.activation_core.mint_token(
        f"deploy-gate-{label}",
        secret,
        iat=int(time.time()),
        exp=int(time.time()) + 90 * 86400,
    )
    paid_response = service.handle_activate(
        {"device": f"gate-{label}", "token": token}
    )
    _expect(
        paid_response,
        503,
        ok=False,
        mode="paid",
        reason="state_error",
    )
    trial_response = service.handle_activate({"device": f"gate-{label}"})
    _expect(
        trial_response,
        503,
        ok=False,
        mode="trial",
        reason="state_error",
    )
    if "lease" in paid_response[1] or "lease" in trial_response[1]:
        raise CandidateError(f"device block key {label} issued a lease")
    for filename in ("bindings.json", "paid_starts.json", "trials.json"):
        if (state / filename).exists():
            raise CandidateError(f"device block key {label} wrote activation state")


def _verify_candidate_readiness_route(
    collector: ModuleType,
    http_server_class,
) -> None:
    """Probe only the candidate handler on an ephemeral loopback socket."""
    httpd = http_server_class(("127.0.0.1", 0), collector.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_port}/activation-readiness"
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                payload = json.loads(response.read())
                if response.status != 200 or payload != {
                    "ok": True,
                    "service": "activation",
                }:
                    raise CandidateError("candidate collector readiness payload mismatch")
        except CandidateError:
            raise
        except Exception as exc:
            raise CandidateError("candidate collector readiness route failed") from exc
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def _verify_candidate_http_e2ee_route(
    collector: ModuleType,
    http_server_class,
) -> None:
    """Prove the candidate handler returns only an authenticated inner decision."""
    protocol = collector._ACTIVATION_HTTP_E2EE
    config = collector.activation_http_e2ee.ActivationHttpE2EEConfig(
        enabled=True,
        raw_sha256="0" * 64,
        url="http://127.0.0.1/activate-e2ee",
        key_id=protocol.key_id,
        server_public_key=protocol.public_key,
    )
    raw_request, context = collector.activation_http_e2ee.build_client_request(
        config,
        {"device": "gate-http-e2ee", "token": "invalid"},
    )
    httpd = http_server_class(("127.0.0.1", 0), collector.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_port}/activate-e2ee",
            data=raw_request,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            raw_response = response.read()
            if response.status != 200:
                raise CandidateError("candidate HTTP E2EE outer status mismatch")
        status, payload = collector.activation_http_e2ee.open_client_response(
            raw_response,
            context,
        )
        if status != 401 or payload != {
            "ok": False,
            "mode": "paid",
            "reason": "bad_token",
        }:
            raise CandidateError("candidate HTTP E2EE inner decision mismatch")
    except CandidateError:
        raise
    except Exception as exc:
        raise CandidateError("candidate HTTP E2EE route failed") from exc
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def validate_candidate_dir(candidate_dir: Path) -> dict[str, object]:
    candidate_dir = Path(candidate_dir).resolve()
    paths = {name: candidate_dir / name for name in RUNTIME_FILES}
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise CandidateError(f"missing runtime files: {', '.join(missing)}")

    for path in paths.values():
        py_compile.compile(str(path), doraise=True)

    module_names = (
        "activation_core",
        "membership_card_store",
        "sample_history_index",
        "build_match_records",
        "sample_dashboard",
        "public_stats",
        "upload_lease",
        "activation_http_e2ee",
        "quote_preferences",
        "activation_server",
        "upload_identity_store",
        "upload_identity_db_admin",
        "activation_device_admin",
        "pet_usage_db_admin",
        "my_records",
        "sample_collector_server",
    )
    previous = {name: sys.modules.get(name) for name in module_names}
    try:
        core = _load_module("activation_core", paths["activation_core.py"])
        store = _load_module("membership_card_store", paths["membership_card_store.py"])
        _load_module("sample_history_index", paths["sample_history_index.py"])
        _load_module("upload_lease", paths["upload_lease.py"])
        _load_module(
            "activation_http_e2ee",
            paths["activation_http_e2ee.py"],
        )
        server = _load_module("activation_server", paths["activation_server.py"])
        server.require_activation_core_compatible()
        if server.membership_card_store is not store:
            raise CandidateError("candidate server did not use exact candidate membership module")
        admin = _load_module(
            "pet_usage_db_admin",
            paths["pet_usage_db_admin.py"],
        )
        if not callable(getattr(admin, "main", None)):
            raise CandidateError("candidate Pet database admin unavailable")
        _load_module(
            "upload_identity_store",
            paths["upload_identity_store.py"],
        )
        upload_admin = _load_module(
            "upload_identity_db_admin",
            paths["upload_identity_db_admin.py"],
        )
        if not callable(getattr(upload_admin, "main", None)):
            raise CandidateError("candidate upload identity admin unavailable")
        device_admin = _load_module(
            "activation_device_admin",
            paths["activation_device_admin.py"],
        )
        if not callable(getattr(device_admin, "main", None)):
            raise CandidateError("candidate device admin unavailable")
        _load_module("build_match_records", paths["build_match_records.py"])
        _load_module("sample_dashboard", paths["sample_dashboard.py"])
        _load_module("public_stats", paths["public_stats.py"])
        _load_module("my_records", paths["my_records.py"])
        _load_module("quote_preferences", paths["quote_preferences.py"])
        collector = _load_module(
            "sample_collector_server",
            paths["sample_collector_server.py"],
        )

        secret = b"bidking-deploy-gate-activation-secret-v1"
        master_key = base64.b64encode(b"K" * 32).decode("ascii")
        now = int(time.time())
        with tempfile.TemporaryDirectory(prefix="bidking-activation-gate-") as raw_tmp, contextlib.ExitStack() as cleanup:
            state = Path(raw_tmp) / "public-state"
            state.mkdir()
            cleanup.callback(_close_candidate_history, collector)
            service, http_server_class = _construct_via_candidate_collector(
                collector,
                server,
                state,
                secret=secret,
                master_key=master_key,
            )
            _verify_candidate_readiness_route(collector, http_server_class)
            _verify_candidate_http_e2ee_route(collector, http_server_class)
            _verify_candidate_renewal(service.handle_activate, core, secret=secret)

            legacy_state = state / "legacy-unconfigured-positive"
            legacy_state.mkdir()
            legacy_service, _ = _construct_via_candidate_collector(
                collector, server, legacy_state, secret=secret, master_key=master_key,
                membership_enabled=False,
                history_enabled=False,
            )
            legacy_token = core.mint_token(
                "deploy-gate-legacy", secret, iat=now, exp=now + 30 * 86400,
            )
            legacy_response = legacy_service.handle_activate(
                {"device": "gate-legacy", "token": legacy_token},
            )
            _expect(legacy_response, 200, ok=True, mode="paid")
            if ("membership" in _signed_lease_payload(legacy_response[1])
                    or "membership_card_capability" in legacy_response[1]
                    or (legacy_state / "membership.sqlite3").exists()):
                raise CandidateError("candidate unconfigured legacy contract failed")
            # Restore the configured service used by the remaining route/state checks.
            collector._ACT = service

            ready_code, ready_body = server.activation_readiness(service)
            if ready_code != 200 or ready_body != {"ok": True, "service": "activation"}:
                raise CandidateError("activation readiness failed")

            _expect(
                service.handle_activate({"device": "gate-paid", "token": "invalid"}),
                401,
                ok=False,
                mode="paid",
                reason="bad_token",
            )
            trial_response = service.handle_activate(
                {"device": "gate-trial", "upload_lease_schema": 1}
            )
            _expect(
                trial_response,
                200,
                ok=True,
                mode="trial",
            )
            _expect_pet_entitlement(
                trial_response,
                plan_kind="trial",
                pet_companion="trial",
            )
            _expect_upload_lease(
                server,
                service,
                trial_response,
                now=int(time.time()),
            )

            pet_boundaries = (
                (30, "30d", "quota", 43_200),
                (60, "custom", "quota", 86_400),
                (90, "90d", "full", None),
            )
            for days, plan_kind, pet_access, total_seconds in pet_boundaries:
                token = core.mint_token(
                    f"deploy-gate-pet-{days}",
                    secret,
                    iat=now,
                    exp=now + days * 86400,
                )
                response = service.handle_activate(
                    {"device": f"gate-pet-{days}", "token": token}
                )
                _expect_pet_entitlement(
                    response,
                    plan_kind=plan_kind,
                    pet_companion=pet_access,
                )
                pet = _signed_lease_payload(response[1])["feature_usage"]["pet_companion"]
                if pet.get("kind") != pet_access:
                    raise CandidateError("candidate pet usage kind failed")
                if total_seconds is not None and (
                    pet.get("total_seconds") != total_seconds
                    or pet.get("remaining_seconds") != total_seconds
                ):
                    raise CandidateError("candidate pet usage quota failed")

            roaming = core.mint_token(
                "deploy-gate-roaming",
                secret,
                iat=now,
                exp=now + 365 * 86400,
                roaming=True,
            )
            roaming_a_response = service.handle_activate(
                {"device": "gate-roaming-a", "token": roaming}
            )
            _expect(
                roaming_a_response,
                200,
                ok=True,
                mode="paid",
            )
            _expect_pet_entitlement(
                roaming_a_response,
                plan_kind="365d",
                pet_companion="full",
            )
            roaming_b_response = service.handle_activate(
                {"device": "gate-roaming-b", "token": roaming}
            )
            _expect(
                roaming_b_response,
                200,
                ok=True,
                mode="paid",
            )
            _expect_pet_entitlement(
                roaming_b_response,
                plan_kind="365d",
                pet_companion="full",
            )
            bindings = json.loads((state / "bindings.json").read_text(encoding="utf-8"))
            if core.binding_state(bindings.get("deploy-gate-roaming")) != ("gate-roaming-b", 1):
                raise CandidateError("roaming rebind contract failed")
            for used in range(2, 9):
                response = service.handle_activate({"device": f"gate-roaming-{used}", "token": roaming})
                _expect(response, 200, ok=True, mode="paid")
                if _signed_lease_payload(response[1]).get("roaming_rebinds") != core.roaming_rebinds_state(used):
                    raise CandidateError("roaming rebind counter contract failed")
            exhausted = service.handle_activate({"device": "gate-roaming-9", "token": roaming})
            _expect(exhausted, 403, ok=False, mode="paid", reason="device_mismatch")
            if (
                exhausted[1].get("detail") != "roaming_rebind_limit"
                or exhausted[1].get("roaming_rebinds") != core.roaming_rebinds_state(8)
                or any(key in exhausted[1] for key in ("lease", "master_key", "upload_lease"))
            ):
                raise CandidateError("roaming rebind limit contract failed")
            _expect(service.handle_activate({"device": "gate-roaming-8", "token": roaming}),
                    200, ok=True, mode="paid")

            blocked_device = "gate-device-revoked"
            if not isinstance(service.device_block_key, bytes):
                raise CandidateError("device revoke key wiring failed")
            block_id = server.device_block_id(service.device_block_key, blocked_device)
            server._atomic_write_json(
                service.device_blocks_path,
                {
                    "schema_version": 1,
                    "blocked": {
                        block_id: {
                            "blocked_at": now,
                            "reason": "deploy_gate",
                            "evidence_ref": "TEST-20260727ABCD",
                            "actor": "activation_deploy_gate",
                        }
                    },
                },
            )
            blocked_token = core.mint_token(
                "deploy-gate-blocked",
                secret,
                iat=now,
                exp=now + 90 * 86400,
            )
            _expect(
                service.handle_activate(
                    {"device": blocked_device, "token": blocked_token}
                ),
                403,
                ok=False,
                mode="paid",
                reason="device_revoked",
            )
            _expect(
                service.handle_activate({"device": blocked_device}),
                403,
                ok=False,
                mode="trial",
                reason="device_revoked",
            )
            bindings_after = json.loads(
                (state / "bindings.json").read_text(encoding="utf-8")
            )
            if "deploy-gate-blocked" in bindings_after:
                raise CandidateError("device revoke wrote paid binding")
            trials_after = json.loads((state / "trials.json").read_text(encoding="utf-8"))
            if blocked_device in trials_after:
                raise CandidateError("device revoke wrote trial state")

            shared_state = state / "shared-key-negative"
            shared_state.mkdir()
            shared_service, _ = _construct_via_candidate_collector(
                collector,
                server,
                shared_state,
                secret=secret,
                master_key=master_key,
                device_block_key_material=secret,
            )
            shared_blocked_device = "gate-shared-secret-device"
            shared_block_id = server.device_block_id(secret, shared_blocked_device)
            server._atomic_write_json(
                shared_service.device_blocks_path,
                {
                    "schema_version": 1,
                    "blocked": {
                        shared_block_id: {
                            "blocked_at": now,
                            "reason": "deploy_gate",
                            "evidence_ref": "TEST-20260727ABCD",
                            "actor": "activation_deploy_gate",
                        }
                    },
                },
            )
            if server.activation_readiness(shared_service) != (
                503,
                {"ok": False, "service": "activation", "reason": "state_error"},
            ):
                raise CandidateError("device block key separation readiness failed")
            _expect(
                shared_service.handle_activate(
                    {"device": shared_blocked_device, "token": blocked_token}
                ),
                503,
                ok=False,
                mode="paid",
                reason="state_error",
            )
            _expect(
                shared_service.handle_activate({"device": shared_blocked_device}),
                503,
                ok=False,
                mode="trial",
                reason="state_error",
            )

            absent_state = state / "absent-key-positive"
            absent_state.mkdir()
            absent_service, _ = _construct_via_candidate_collector(
                collector,
                server,
                absent_state,
                secret=secret,
                master_key=master_key,
                device_block_key_mode="absent",
            )
            if absent_service.device_block_key_state != "missing":
                raise CandidateError("absent device block key classification failed")
            if server.activation_readiness(absent_service) != (
                200,
                {"ok": True, "service": "activation"},
            ):
                raise CandidateError("absent device block key bootstrap failed")
            absent_paid = core.mint_token(
                "deploy-gate-absent-paid",
                secret,
                iat=now,
                exp=now + 90 * 86400,
            )
            _expect(
                absent_service.handle_activate(
                    {"device": "gate-absent-paid", "token": absent_paid}
                ),
                200,
                ok=True,
                mode="paid",
            )
            _expect(
                absent_service.handle_activate({"device": "gate-absent-trial"}),
                200,
                ok=True,
                mode="trial",
            )

            invalid_key_cases = (
                ("zero-byte", b"", "file"),
                ("whitespace-only", b" \t\r\n", "file"),
                ("short", b"short", "file"),
                ("shared-secret", secret, "file"),
                ("unreadable", None, "unreadable"),
            )
            for label, material, mode in invalid_key_cases:
                invalid_state = state / f"{label}-key-negative"
                invalid_state.mkdir()
                invalid_service, _ = _construct_via_candidate_collector(
                    collector,
                    server,
                    invalid_state,
                    secret=secret,
                    master_key=master_key,
                    device_block_key_material=material,
                    device_block_key_mode=mode,
                )
                _verify_invalid_device_block_key(
                    server,
                    invalid_service,
                    invalid_state,
                    secret=secret,
                    label=label,
                )

            _real_collector_main_smoke(
                candidate_dir,
                server,
                collector,
                secret=secret,
                master_key=master_key,
            )
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    return {
        "ok": True,
        "core_api": getattr(core, "ACTIVATION_CORE_API_VERSION", None),
        "checks": [
            "compile",
            "collector_wiring",
            "collector_main_subprocess",
            "configured_membership_renewal",
            "configured_history_index",
            "unconfigured_legacy",
            "readiness_route",
            "activation_http_e2ee",
            "readiness",
            "bad_token",
            "trial",
            "roaming",
            "device_revoke",
            "device_block_key_separation",
            "pet_entitlement",
            "pet_usage",
        ],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-dir", required=True)
    args = parser.parse_args(argv[1:])
    try:
        report = validate_candidate_dir(Path(args.candidate_dir))
    except Exception as exc:  # noqa: BLE001 - deployment boundary must fail closed
        print(
            json.dumps(
                {"ok": False, "error_type": type(exc).__name__, "error": str(exc)},
                ensure_ascii=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
