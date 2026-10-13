from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = ROOT / "tests"
for location in (SCRIPTS, TESTS):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

import activation_server as server  # noqa: E402
import test_activation_trial_runtime_integration_0_3_1 as runtime  # noqa: E402


def _prime_paid(
    root: Path,
    *,
    uid: str = "cache-paid",
    device: str = "CACHE-PAID",
) -> tuple[server.ActivationService, str]:
    now = int(time.time())
    service = runtime._service(root)
    token = runtime._paid_token(uid, now=now)
    server._atomic_write_json(service.bindings_path, {uid: device})
    server._atomic_write_json(service.paid_starts_path, {uid: now - 60})
    assert service.handle_activate({"token": token, "device": device})[0] == 200
    return service, token


def test_external_revoke_device_block_replace_and_corruption_are_immediate(
    tmp_path: Path,
) -> None:
    uid = "external-authority-paid"
    device = "EXTERNAL-AUTHORITY-PAID"
    service, token = _prime_paid(tmp_path, uid=uid, device=device)

    service.revoked_path.write_text(f"{uid}\n", encoding="utf-8")
    assert service.handle_activate({"token": token, "device": device}) == (
        403,
        {"ok": False, "mode": "paid", "reason": "revoked"},
    )

    service.revoked_path.write_text("", encoding="utf-8")
    block_key = b"B" * 32
    service.device_block_key = block_key
    service.device_block_key_state = "valid"
    server._atomic_write_json(
        service.device_blocks_path,
        {
            "schema_version": 1,
            "blocked": {
                server.device_block_id(block_key, device): {
                    "blocked_at": int(time.time()),
                    "reason": "confirmed_abuse",
                    "evidence_ref": "INC-EXTERNAL1234",
                    "actor": "author",
                }
            },
        },
    )
    assert service.handle_activate({"token": token, "device": device}) == (
        403,
        {"ok": False, "mode": "paid", "reason": "device_revoked"},
    )

    service.device_blocks_path.write_text("{", encoding="utf-8")
    assert service.handle_activate({"token": token, "device": device}) == (
        503,
        {"ok": False, "mode": "paid", "reason": "state_error"},
    )

    server._atomic_write_json(
        service.device_blocks_path,
        {"schema_version": 1, "blocked": {}},
    )
    assert service.handle_activate({"token": token, "device": device})[0] == 200


def test_mtime_rollback_and_same_size_replacement_binding_change_are_not_cached(
    tmp_path: Path,
) -> None:
    uid = "mtime-rollback-paid"
    device = "MTIME-ROLLBACK-A"
    replacement = "MTIME-ROLLBACK-B"
    assert len(device) == len(replacement)
    service, token = _prime_paid(tmp_path, uid=uid, device=device)
    original_stat = service.bindings_path.stat()

    replacement_path = service.bindings_path.with_name("bindings.replacement.json")
    replacement_path.write_text(
        json.dumps({uid: replacement}),
        encoding="utf-8",
    )
    os.replace(replacement_path, service.bindings_path)
    os.utime(
        service.bindings_path,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )

    assert service.handle_activate({"token": token, "device": device}) == (
        403,
        {"ok": False, "mode": "paid", "reason": "device_mismatch"},
    )

    restarted = runtime._service(tmp_path)
    assert restarted.handle_activate({"token": token, "device": device}) == (
        403,
        {"ok": False, "mode": "paid", "reason": "device_mismatch"},
    )


@pytest.mark.parametrize(
    "authority_name",
    ("bindings_path", "paid_starts_path", "revoked_path", "device_blocks_path"),
)
def test_external_authority_corruption_after_cache_warmup_fails_closed(
    tmp_path: Path,
    authority_name: str,
) -> None:
    device = f"CORRUPT-{authority_name}"
    service, token = _prime_paid(
        tmp_path,
        uid=f"corrupt-{authority_name}",
        device=device,
    )

    getattr(service, authority_name).write_bytes(b"\xff")

    assert service.handle_activate({"token": token, "device": device}) == (
        503,
        {"ok": False, "mode": "paid", "reason": "state_error"},
    )


def test_supported_writer_refreshes_cache_even_when_stamp_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = runtime._service(tmp_path)
    now = int(time.time())
    token = runtime._paid_token("same-stamp-paid", now=now)
    real_stamp = server._authority_file_stamp
    fixed_paths = {service.bindings_path, service.paid_starts_path}

    def fixed_stamp(path: Path) -> tuple:
        if path in fixed_paths:
            return ("synthetic-unchanged", path.name)
        return real_stamp(path)

    monkeypatch.setattr(server, "_authority_file_stamp", fixed_stamp)
    first = service.handle_activate(
        {"token": token, "device": "SAME-STAMP-PAID"}
    )
    second = service.handle_activate(
        {"token": token, "device": "SAME-STAMP-PAID"}
    )

    assert first[0] == 200
    assert second[0] == 200
    assert json.loads(service.bindings_path.read_text(encoding="utf-8")) == {
        "same-stamp-paid": "SAME-STAMP-PAID"
    }
    assert "same-stamp-paid" in json.loads(
        service.paid_starts_path.read_text(encoding="utf-8")
    )


def test_failed_supported_write_never_publishes_cache_only_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = runtime._service(tmp_path)
    now = int(time.time())
    token = runtime._paid_token("failed-write-paid", now=now)
    real_write = server._atomic_write_json

    def fail_binding(path: Path, value) -> None:
        if path == service.bindings_path:
            raise OSError("synthetic binding write failure")
        real_write(path, value)

    monkeypatch.setattr(server, "_atomic_write_json", fail_binding)
    assert service.handle_activate(
        {"token": token, "device": "FAILED-WRITE-PAID"}
    ) == (
        503,
        {"ok": False, "mode": "paid", "reason": "state_error"},
    )
    assert not service.bindings_path.exists()

    monkeypatch.setattr(server, "_atomic_write_json", real_write)
    assert service.handle_activate(
        {"token": token, "device": "FAILED-WRITE-PAID"}
    )[0] == 200


def test_five_consecutive_800_distinct_paid_rounds_are_complete_and_write_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = int(time.time())
    service = runtime._service(tmp_path)
    bindings: dict[str, str] = {}
    starts: dict[str, int] = {}
    requests: list[bytes] = []
    for index in range(runtime.BURST_SIZE):
        uid = f"cache-burst-{index:04d}"
        device = f"CACHE-BURST-{index:04d}"
        bindings[uid] = device
        starts[uid] = now - 60
        requests.append(
            runtime._request_bytes(
                {
                    "token": runtime._paid_token(uid, now=now),
                    "device": device,
                }
            )
        )
    server._atomic_write_json(service.bindings_path, bindings)
    server._atomic_write_json(service.paid_starts_path, starts)
    writes = runtime._install_write_spy(monkeypatch)
    loader_calls = {
        "bindings": 0,
        "paid_starts": 0,
        "revoked": 0,
        "device_blocks": 0,
    }
    real_bindings = server._load_bindings_state
    real_paid_starts = server._load_paid_starts_state
    real_revoked = server._load_lines
    real_device_blocks = server._load_device_blocks_state

    def load_bindings(path: Path) -> dict:
        loader_calls["bindings"] += 1
        return real_bindings(path)

    def load_paid_starts(path: Path, *, now: int) -> dict:
        loader_calls["paid_starts"] += 1
        return real_paid_starts(path, now=now)

    def load_revoked(path: Path) -> set[str]:
        loader_calls["revoked"] += 1
        return real_revoked(path)

    def load_device_blocks(path: Path) -> dict:
        loader_calls["device_blocks"] += 1
        return real_device_blocks(path)

    monkeypatch.setattr(server, "_load_bindings_state", load_bindings)
    monkeypatch.setattr(server, "_load_paid_starts_state", load_paid_starts)
    monkeypatch.setattr(server, "_load_lines", load_revoked)
    monkeypatch.setattr(server, "_load_device_blocks_state", load_device_blocks)

    elapsed_maxima: list[float] = []
    for _round in range(5):
        _completed, metrics = runtime._run_simultaneous_http_burst(
            service,
            requests,
            monkeypatch,
        )
        elapsed_maxima.append(metrics["elapsed_max_ms"])

    assert writes == []
    assert loader_calls == {
        "bindings": 1,
        "paid_starts": 1,
        "revoked": 1,
        "device_blocks": 1,
    }
    assert len(service._paid_authority_cache) == 4
    assert len(elapsed_maxima) == 5
    assert all(value < 4_000 for value in elapsed_maxima)
