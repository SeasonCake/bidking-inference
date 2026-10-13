from __future__ import annotations

import hmac
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import activation_core as core  # noqa: E402
import activation_server as server  # noqa: E402
import sample_collector_server as collector  # noqa: E402


SECRET = b"device-revocation-test-secret"
BLOCK_KEY = b"device-revocation-dedicated-block-key-v1"
MASTER_KEY = "S0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0s="
ADMIN = SCRIPTS / "activation_device_admin.py"


def _service(
    tmp_path: Path,
    *,
    device_block_key: bytes | None = BLOCK_KEY,
) -> server.ActivationService:
    pet_usage_db = tmp_path / "pet_usage.sqlite3"
    if not pet_usage_db.exists():
        server.initialize_pet_usage_database(
            pet_usage_db,
            secret=SECRET,
            legacy_paths=(
                tmp_path / "pet_usage.json",
                tmp_path / "pet_usage.anchor.jsonl",
            ),
        )
    return server.ActivationService(
        secret=SECRET,
        master_key_b64=MASTER_KEY,
        bindings_path=tmp_path / "bindings.json",
        revoked_path=tmp_path / "revoked.txt",
        telemetry_path=tmp_path / "telemetry.jsonl",
        trials_path=tmp_path / "trials.json",
        paid_starts_path=tmp_path / "paid_starts.json",
        pet_usage_db_path=pet_usage_db,
        pet_usage_path=tmp_path / "pet_usage.json",
        device_blocks_path=tmp_path / "device_blocks.json",
        device_block_key=device_block_key,
    )


def _block_state(
    device: str,
    *,
    block_key: bytes = BLOCK_KEY,
    now: int | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "blocked": {
            server.device_block_id(block_key, device): {
                "blocked_at": int(now or time.time()),
                "reason": "confirmed_abuse",
                "evidence_ref": "INC-20260727ABCD",
                "actor": "author",
            }
        },
    }


def _service_from_real_collector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    key_material: bytes | None,
    key_read_error: bool = False,
) -> tuple[server.ActivationService, str]:
    activation_key = tmp_path / "activation_key.txt"
    master_key = tmp_path / "master_key.txt"
    device_block_key = tmp_path / "device_block_key.txt"
    activation_key.write_bytes(SECRET)
    master_key.write_text(MASTER_KEY, encoding="utf-8")
    if key_material is not None:
        device_block_key.write_bytes(key_material)
    pet_usage_db = tmp_path / "pet_usage.sqlite3"
    server.initialize_pet_usage_database(
        pet_usage_db,
        secret=SECRET,
        legacy_paths=(
            tmp_path / "pet_usage.json",
            tmp_path / "pet_usage.anchor.jsonl",
        ),
    )

    if key_read_error:
        device_block_key.write_bytes(BLOCK_KEY)
        real_read_bytes = Path.read_bytes

        def fail_target_read(path: Path) -> bytes:
            if path == device_block_key:
                raise PermissionError("simulated unreadable key")
            return real_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", fail_target_read)

    class NoopHTTPServer:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def serve_forever(self) -> None:
            pass

    output: list[str] = []
    monkeypatch.setattr(collector, "_ACT", None)
    monkeypatch.setattr(collector, "_load_existing", lambda: None)
    monkeypatch.setattr(collector, "_warm_public_cache", lambda: None)
    monkeypatch.setattr(collector, "SafeThreadingHTTPServer", NoopHTTPServer)
    monkeypatch.setattr(
        "builtins.print",
        lambda *args, **_kwargs: output.append(" ".join(map(str, args))),
    )
    result = collector.main(
        [
            "sample_collector_server.py",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--out",
            str(tmp_path / "samples.jsonl"),
            "--dashboard-pass-file",
            str(tmp_path / "dashboard_pass.missing"),
            "--export-key-file",
            str(tmp_path / "export_key.missing"),
            "--version-manifest-file",
            str(tmp_path / "version_manifest.missing"),
            "--activation-key-file",
            str(activation_key),
            "--master-key-file",
            str(master_key),
            "--master-key-v2-file",
            str(tmp_path / "master_key_v2.missing"),
            "--bindings-file",
            str(tmp_path / "bindings.json"),
            "--revoked-file",
            str(tmp_path / "revoked.txt"),
            "--device-blocks-file",
            str(tmp_path / "device_blocks.json"),
            "--device-block-key-file",
            str(device_block_key),
            "--telemetry-file",
            str(tmp_path / "telemetry.jsonl"),
            "--trials-file",
            str(tmp_path / "trials.json"),
            "--pet-usage-file",
            str(tmp_path / "pet_usage.json"),
            "--pet-usage-db-file",
            str(pet_usage_db),
        ]
    )
    assert result == 0
    assert isinstance(collector._ACT, server.ActivationService)
    return collector._ACT, "\n".join(output)


@pytest.mark.parametrize(
    ("key_material", "key_read_error", "expected_state", "expected_code"),
    [
        (None, False, "missing", 200),
        (b"", False, "invalid", 503),
        (b" \t\r\n", False, "invalid", 503),
        (BLOCK_KEY, True, "invalid", 503),
        (BLOCK_KEY, False, "valid", 200),
    ],
)
def test_real_collector_preserves_device_block_key_file_presence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key_material: bytes | None,
    key_read_error: bool,
    expected_state: str,
    expected_code: int,
) -> None:
    service, banner = _service_from_real_collector(
        tmp_path,
        monkeypatch,
        key_material=key_material,
        key_read_error=key_read_error,
    )

    assert service.device_block_key_state == expected_state
    ready_code, ready_body = server.activation_readiness(service)
    assert ready_code == expected_code
    assert ready_body == (
        {"ok": True, "service": "activation"}
        if expected_code == 200
        else {"ok": False, "service": "activation", "reason": "state_error"}
    )
    if expected_state == "missing":
        assert "设备撤销key=OFF" in banner
    elif expected_state == "valid":
        assert "设备撤销key=ON" in banner
    else:
        assert "设备撤销key=INVALID" in banner
        token = core.mint_token("candidate", SECRET)
        assert service.handle_activate({"device": "device", "token": token}) == (
            503,
            {"ok": False, "mode": "paid", "reason": "state_error"},
        )
        assert service.handle_activate({"device": "device"}) == (
            503,
            {"ok": False, "mode": "trial", "reason": "state_error"},
        )
        assert not (tmp_path / "bindings.json").exists()
        assert not (tmp_path / "paid_starts.json").exists()
        assert not (tmp_path / "trials.json").exists()


def test_blocked_device_rejects_original_new_roaming_and_trial_without_writes(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    blocked_device = "stable-device-A"
    first = core.mint_token("first-uid", SECRET, iat=100, exp=100 + 90 * 86400)
    code, body = service.handle_activate(
        {"device": blocked_device, "token": first, "client_version": "0.3.0"}
    )
    assert code == 200 and body["ok"] is True

    server._atomic_write_json(tmp_path / "device_blocks.json", _block_state(blocked_device))
    bindings_before = (tmp_path / "bindings.json").read_bytes()
    starts_before = (tmp_path / "paid_starts.json").read_bytes()
    trials_before = (
        (tmp_path / "trials.json").read_bytes()
        if (tmp_path / "trials.json").exists()
        else None
    )

    tokens = [
        first,
        core.mint_token("second-uid", SECRET, iat=200, exp=200 + 365 * 86400),
        core.mint_token(
            "roaming-uid",
            SECRET,
            iat=300,
            exp=300 + 365 * 86400,
            roaming=True,
        ),
    ]
    for token in tokens:
        assert service.handle_activate(
            {"device": blocked_device, "token": token, "client_version": "0.3.0"}
        ) == (
            403,
            {"ok": False, "mode": "paid", "reason": "device_revoked"},
        )
    assert service.handle_activate(
        {"device": blocked_device, "client_version": "0.3.0"}
    ) == (
        403,
        {"ok": False, "mode": "trial", "reason": "device_revoked"},
    )

    assert (tmp_path / "bindings.json").read_bytes() == bindings_before
    assert (tmp_path / "paid_starts.json").read_bytes() == starts_before
    assert (
        (tmp_path / "trials.json").read_bytes()
        if (tmp_path / "trials.json").exists()
        else None
    ) == trials_before

    clean = core.mint_token("clean-uid", SECRET, iat=400, exp=400 + 90 * 86400)
    clean_code, clean_body = service.handle_activate(
        {"device": "stable-device-B", "token": clean, "client_version": "0.3.0"}
    )
    assert clean_code == 200 and clean_body["ok"] is True


def test_uid_revocation_does_not_expand_to_clean_token_on_same_device(tmp_path: Path) -> None:
    service = _service(tmp_path)
    (tmp_path / "revoked.txt").write_text("revoked-only\n", encoding="utf-8")
    revoked = core.mint_token("revoked-only", SECRET)
    clean = core.mint_token("clean-only", SECRET)

    assert service.handle_activate(
        {"device": "same-network-is-not-identity", "token": revoked}
    ) == (
        403,
        {"ok": False, "mode": "paid", "reason": "revoked"},
    )
    code, body = service.handle_activate(
        {"device": "same-network-is-not-identity", "token": clean}
    )
    assert code == 200 and body["ok"] is True


def test_nonempty_device_block_state_without_dedicated_key_fails_closed(
    tmp_path: Path,
) -> None:
    service = server.ActivationService(
        secret=SECRET,
        master_key_b64=MASTER_KEY,
        bindings_path=tmp_path / "bindings.json",
        revoked_path=tmp_path / "revoked.txt",
        telemetry_path=tmp_path / "telemetry.jsonl",
        trials_path=tmp_path / "trials.json",
        paid_starts_path=tmp_path / "paid_starts.json",
        device_blocks_path=tmp_path / "device_blocks.json",
    )
    (tmp_path / "device_blocks.json").write_text(
        json.dumps(_block_state("device")),
        encoding="utf-8",
    )
    token = core.mint_token("candidate", SECRET)

    assert service.handle_activate({"device": "device", "token": token}) == (
        503,
        {"ok": False, "mode": "paid", "reason": "state_error"},
    )
    assert service.handle_activate({"device": "device"}) == (
        503,
        {"ok": False, "mode": "trial", "reason": "state_error"},
    )


@pytest.mark.parametrize("block_key", [SECRET, b"short-key"])
def test_shared_or_invalid_device_block_key_fails_closed_for_paid_trial_and_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    block_key: bytes,
) -> None:
    compare_calls: list[tuple[bytes, bytes]] = []
    real_compare = hmac.compare_digest

    def tracked_compare(left: bytes, right: bytes) -> bool:
        compare_calls.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(server.hmac, "compare_digest", tracked_compare)
    service = _service(tmp_path, device_block_key=block_key)
    assert server.activation_readiness(service) == (
        503,
        {"ok": False, "service": "activation", "reason": "state_error"},
    )
    server._atomic_write_json(
        tmp_path / "device_blocks.json",
        _block_state("device", block_key=block_key),
    )
    token = core.mint_token("candidate", SECRET)

    assert service.handle_activate({"device": "device", "token": token}) == (
        503,
        {"ok": False, "mode": "paid", "reason": "state_error"},
    )
    assert service.handle_activate({"device": "device"}) == (
        503,
        {"ok": False, "mode": "trial", "reason": "state_error"},
    )
    assert server.activation_readiness(service) == (
        503,
        {"ok": False, "service": "activation", "reason": "state_error"},
    )
    if block_key == SECRET:
        byte_calls = [
            (left, right)
            for left, right in compare_calls
            if isinstance(left, bytes) and isinstance(right, bytes)
        ]
        assert byte_calls
        assert all(len(left) == len(right) == 32 for left, right in byte_calls)
    assert not (tmp_path / "bindings.json").exists()
    assert not (tmp_path / "paid_starts.json").exists()
    assert not (tmp_path / "trials.json").exists()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"schema_version": 2, "blocked": {}},
        {"schema_version": 1, "blocked": []},
        {
            "schema_version": 1,
            "blocked": {
                "raw-device": {
                    "blocked_at": 1,
                    "reason": "x",
                    "evidence_ref": "x",
                    "actor": "x",
                }
            },
        },
        {
            "schema_version": 1,
            "blocked": {
                "a" * 64: {
                    "blocked_at": True,
                    "reason": "x",
                    "evidence_ref": "x",
                    "actor": "x",
                }
            },
        },
    ],
)
def test_malformed_device_block_state_fails_closed_without_paid_or_trial_write(
    tmp_path: Path,
    payload,
) -> None:
    service = _service(tmp_path)
    (tmp_path / "device_blocks.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    token = core.mint_token("candidate", SECRET)

    assert service.handle_activate({"device": "device", "token": token}) == (
        503,
        {"ok": False, "mode": "paid", "reason": "state_error"},
    )
    assert service.handle_activate({"device": "device"}) == (
        503,
        {"ok": False, "mode": "trial", "reason": "state_error"},
    )
    assert not (tmp_path / "bindings.json").exists()
    assert not (tmp_path / "paid_starts.json").exists()
    assert not (tmp_path / "trials.json").exists()


def _run_admin(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(ADMIN),
            "--state-dir",
            str(tmp_path),
            *args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=10,
        check=False,
    )


def test_admin_is_redacted_dry_run_idempotent_and_unblock_keeps_uid_revoke(
    tmp_path: Path,
) -> None:
    target_uid = "private-target-uid"
    sibling_uid = "private-sibling-uid"
    other_uid = "private-other-uid"
    raw_device = "private-raw-device-fingerprint"
    (tmp_path / "device_block_key.txt").write_bytes(BLOCK_KEY)
    (tmp_path / "bindings.json").write_text(
        json.dumps(
            {
                target_uid: raw_device,
                sibling_uid: raw_device,
                other_uid: "different-device",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "paid_starts.json").write_text(
        json.dumps({target_uid: int(time.time()) - 10}),
        encoding="utf-8",
    )

    inspect = _run_admin(tmp_path, "inspect", "--uid", target_uid, "--uid", other_uid)
    assert inspect.returncode == 0
    assert raw_device not in inspect.stdout
    assert target_uid not in inspect.stdout
    assert sibling_uid not in inspect.stdout
    assert json.loads(inspect.stdout)["candidates"][0]["same_device_uid_count"] == 2

    command = (
        "block",
        "--uid",
        target_uid,
        "--reason",
        "confirmed_abuse",
        "--evidence-ref",
        "INC-20260727ABCD",
        "--revoke-bound-uids",
    )
    dry_run = _run_admin(tmp_path, *command)
    assert dry_run.returncode == 0
    assert json.loads(dry_run.stdout)["applied"] is False
    assert not (tmp_path / "device_blocks.json").exists()
    assert not (tmp_path / "revoked.txt").exists()

    applied = _run_admin(tmp_path, *command, "--apply")
    assert applied.returncode == 0
    report = json.loads(applied.stdout)
    assert report["applied"] is True
    assert report["matched_uid_count"] == 2
    assert report["newly_revoked_count"] == 2

    state_text = (tmp_path / "device_blocks.json").read_text("utf-8")
    audit_text = (tmp_path / "device_block_audit.jsonl").read_text("utf-8")
    listed = _run_admin(tmp_path, "list")
    assert listed.returncode == 0
    for private_value in (raw_device, target_uid, sibling_uid, other_uid):
        assert private_value not in state_text
        assert private_value not in audit_text
        assert private_value not in applied.stdout
        assert private_value not in listed.stdout
    revoked = set((tmp_path / "revoked.txt").read_text("utf-8").splitlines())
    assert revoked == {target_uid, sibling_uid}

    repeated = _run_admin(tmp_path, *command, "--apply")
    assert repeated.returncode == 0
    repeated_report = json.loads(repeated.stdout)
    assert repeated_report["already_blocked"] is True
    assert repeated_report["newly_revoked_count"] == 0

    unblocked = _run_admin(
        tmp_path,
        "unblock",
        "--uid",
        target_uid,
        "--reason",
        "manual_appeal",
        "--evidence-ref",
        "APL-20260727EFGH",
        "--apply",
    )
    assert unblocked.returncode == 0
    assert json.loads(unblocked.stdout)["was_blocked"] is True
    assert server._load_device_blocks_state(tmp_path / "device_blocks.json")["blocked"] == {}
    assert list(tmp_path.glob("device_blocks.json.before-admin-*.bak"))
    assert set((tmp_path / "revoked.txt").read_text("utf-8").splitlines()) == {
        target_uid,
        sibling_uid,
    }


@pytest.mark.parametrize(
    ("field", "private_value"),
    [
        ("reason", "private-target-uid"),
        ("evidence_ref", "private-raw-device-fingerprint"),
        ("actor", "private-sibling-uid"),
    ],
)
def test_admin_rejects_freeform_identity_metadata_without_new_state(
    tmp_path: Path,
    field: str,
    private_value: str,
) -> None:
    target_uid = "private-target-uid"
    raw_device = "private-raw-device-fingerprint"
    (tmp_path / "device_block_key.txt").write_bytes(BLOCK_KEY)
    (tmp_path / "bindings.json").write_text(
        json.dumps({target_uid: raw_device}),
        encoding="utf-8",
    )
    args = {
        "reason": "confirmed_abuse",
        "evidence_ref": "INC-20260727ABCD",
        "actor": "author",
    }
    args[field] = private_value

    result = _run_admin(
        tmp_path,
        "block",
        "--uid",
        target_uid,
        "--reason",
        args["reason"],
        "--evidence-ref",
        args["evidence_ref"],
        "--actor",
        args["actor"],
        "--apply",
    )

    assert result.returncode == 1
    assert private_value not in result.stdout
    assert private_value not in result.stderr
    assert not (tmp_path / "device_blocks.json").exists()
    assert not (tmp_path / "device_block_audit.jsonl").exists()
