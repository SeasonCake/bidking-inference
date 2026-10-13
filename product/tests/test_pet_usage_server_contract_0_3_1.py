from __future__ import annotations

import json
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import activation_core as core  # noqa: E402
import activation_server as server  # noqa: E402


SECRET = b"pet-usage-server-contract-secret"
MASTER_KEY = "S0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0s="
BASE_NOW = 2_000_000_000


def _service(tmp_path: Path) -> server.ActivationService:
    pet_state = tmp_path / "pet-authority"
    pet_state.mkdir(exist_ok=True)
    pet_usage_db = pet_state / "pet_usage.sqlite3"
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
        pet_usage_lease_seconds=15,
    )


def _token(uid: str, days: int, *, roaming: bool = False, extra_seconds: int = 0) -> str:
    iat = 1_700_000_000
    return core.mint_token(
        uid,
        SECRET,
        iat=iat,
        exp=iat + days * 86_400 + extra_seconds,
        roaming=roaming,
    )


def _lease(body: dict) -> dict:
    signed = body["lease"]
    return json.loads(signed.rsplit(".", 1)[0])


def _pet(body: dict) -> dict:
    return _lease(body)["feature_usage"]["pet_companion"]


def _activate(
    service: server.ActivationService,
    *,
    token: str | None,
    device: str,
    event: dict | None = None,
) -> tuple[int, dict]:
    body: dict = {"device": device}
    if token is not None:
        body["token"] = token
    if event is not None:
        body["pet_usage"] = event
    return service.handle_activate(body)


def _event(session: str, seq: int, visible: bool) -> dict:
    return {"session_id": session, "seq": seq, "visible": visible}


@pytest.mark.parametrize(
    ("days", "plan_kind", "kind", "total_seconds"),
    [
        (29, "custom", "none", None),
        (30, "30d", "quota", 43_200),
        (31, "custom", "quota", 86_400),
        (60, "custom", "quota", 86_400),
        (89, "custom", "quota", 86_400),
        (90, "90d", "full", None),
        (365, "365d", "full", None),
    ],
)
def test_server_authoritative_duration_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    days: int,
    plan_kind: str,
    kind: str,
    total_seconds: int | None,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    service = _service(tmp_path)
    code, body = _activate(
        service,
        token=_token(f"duration-{days}", days),
        device=f"DEVICE-{days}",
    )
    assert code == 200
    lease = _lease(body)
    pet = lease["feature_usage"]["pet_companion"]
    assert lease["plan_kind"] == plan_kind
    assert lease["feature_entitlements"]["pet_companion"] == kind
    assert pet["kind"] == kind
    assert pet["status"] == ("locked" if kind == "none" else ("active" if kind == "full" else "available"))
    if total_seconds is not None:
        assert pet["total_seconds"] == total_seconds
        assert pet["remaining_seconds"] == total_seconds
    if days >= 90:
        assert pet["entitlement_exp"] == lease["paid_exp"]


def test_free_trial_is_independent_600_second_pet_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    code, body = _activate(_service(tmp_path), token=None, device="TRIAL-DEVICE")
    assert code == 200
    lease = _lease(body)
    pet = lease["feature_usage"]["pet_companion"]
    assert lease["plan_kind"] == "trial"
    assert lease["feature_entitlements"] == {"pet_companion": "trial"}
    assert pet["kind"] == "trial"
    assert pet["total_seconds"] == 600
    assert pet["remaining_seconds"] == 600
    assert pet["status"] == "available"


def test_legacy_permanent_is_full_without_paid_exp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    permanent = core.mint_token("legacy-permanent", SECRET, iat=1_700_000_000)
    code, body = _activate(
        _service(tmp_path),
        token=permanent,
        device="PERMANENT-DEVICE",
    )
    assert code == 200
    lease = _lease(body)
    assert lease["plan_kind"] == "legacy_permanent"
    assert "paid_exp" not in lease
    assert _pet(body) == {"kind": "full", "server_now": BASE_NOW, "status": "active"}


def test_non_whole_day_duration_cannot_upgrade_pet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    code, body = _activate(
        _service(tmp_path),
        token=_token("not-whole-day", 30, extra_seconds=1),
        device="ODD-DURATION",
    )
    assert code == 200
    assert _lease(body)["plan_kind"] == "custom"
    assert _pet(body)["kind"] == "none"
    assert _pet(body)["status"] == "locked"


def test_visible_events_charge_server_time_only_and_pause_when_hidden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [BASE_NOW]
    monkeypatch.setattr(server.time, "time", lambda: now[0])
    service = _service(tmp_path)
    token = _token("visible-only", 30)
    session = "pet-session-visible-0001"

    code, body = _activate(
        service,
        token=token,
        device="VISIBLE-DEVICE",
        event=_event(session, 0, True),
    )
    assert code == 200
    assert _pet(body)["status"] == "active"
    assert _pet(body)["remaining_seconds"] == 43_200

    now[0] += 7
    _, body = _activate(
        service,
        token=token,
        device="VISIBLE-DEVICE",
        event=_event(session, 1, False),
    )
    assert _pet(body)["status"] == "paused"
    assert _pet(body)["remaining_seconds"] == 43_193

    now[0] += 100
    _, body = _activate(service, token=token, device="VISIBLE-DEVICE")
    assert _pet(body)["remaining_seconds"] == 43_193
    assert _pet(body)["status"] == "paused"


def test_short_visibility_lease_bounds_crash_charge_and_replay_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [BASE_NOW]
    monkeypatch.setattr(server.time, "time", lambda: now[0])
    service = _service(tmp_path)
    token = _token("bounded-crash", 30)
    session = "pet-session-crash-00001"

    _activate(
        service,
        token=token,
        device="CRASH-DEVICE",
        event=_event(session, 0, True),
    )
    now[0] += 5
    _, replay = _activate(
        service,
        token=token,
        device="CRASH-DEVICE",
        event=_event(session, 0, True),
    )
    assert _pet(replay)["remaining_seconds"] == 43_195
    assert _pet(replay)["visibility_lease_exp"] == BASE_NOW + 15

    now[0] += 15
    _, expired = _activate(service, token=token, device="CRASH-DEVICE")
    assert _pet(expired)["remaining_seconds"] == 43_185
    assert _pet(expired)["status"] == "paused"


def test_free_trial_exhausts_at_exactly_600_server_seconds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [BASE_NOW]
    monkeypatch.setattr(server.time, "time", lambda: now[0])
    service = _service(tmp_path)
    session = "pet-session-trial-exhaust"
    _activate(
        service,
        token=None,
        device="TRIAL-EXHAUST-DEVICE",
        event=_event(session, 0, True),
    )
    body = None
    for seq in range(1, 41):
        now[0] += 15
        _, body = _activate(
            service,
            token=None,
            device="TRIAL-EXHAUST-DEVICE",
            event=_event(session, seq, True),
        )
    assert body is not None
    assert _pet(body)["remaining_seconds"] == 0
    assert _pet(body)["status"] == "exhausted"
    assert _pet(body)["visible"] is False


def test_sequence_and_concurrent_session_fail_closed_without_client_elapsed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [BASE_NOW]
    monkeypatch.setattr(server.time, "time", lambda: now[0])
    service = _service(tmp_path)
    token = _token("sequence", 60)
    session_a = "pet-session-sequence-a"
    session_b = "pet-session-sequence-b"

    _activate(
        service,
        token=token,
        device="SEQ-DEVICE",
        event=_event(session_a, 0, True),
    )
    now[0] += 1
    _, gap = _activate(
        service,
        token=token,
        device="SEQ-DEVICE",
        event=_event(session_a, 2, True),
    )
    assert _pet(gap)["status"] == "sequence_conflict"
    assert _pet(gap)["remaining_seconds"] == 86_399

    _, concurrent = _activate(
        service,
        token=token,
        device="SEQ-DEVICE",
        event=_event(session_b, 0, True),
    )
    assert _pet(concurrent)["status"] == "session_conflict"

    _, forged = _activate(
        service,
        token=token,
        device="SEQ-DEVICE",
        event={
            **_event(session_a, 1, True),
            "used_seconds": 86_400,
        },
    )
    assert _pet(forged)["status"] == "state_error"
    assert "used_seconds" not in json.dumps(_lease(forged), ensure_ascii=False)


def test_concurrent_double_open_admits_only_one_visible_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    service = _service(tmp_path)
    token = _token("concurrent-open", 60)
    barrier = threading.Barrier(2)

    def open_session(session_id: str) -> str:
        barrier.wait(timeout=5)
        _, body = _activate(
            service,
            token=token,
            device="CONCURRENT-DEVICE",
            event=_event(session_id, 0, True),
        )
        return _pet(body)["status"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(open_session, "pet-session-concurrent-a")
        second = pool.submit(open_session, "pet-session-concurrent-b")
        statuses = sorted((first.result(), second.result()))
    assert statuses == ["active", "session_conflict"]


def test_usage_survives_service_restart_and_roaming_rebind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [BASE_NOW]
    monkeypatch.setattr(server.time, "time", lambda: now[0])
    token = _token("roaming-usage", 60, roaming=True)
    session_a = "pet-session-roaming-a"
    service = _service(tmp_path)
    _activate(
        service,
        token=token,
        device="ROAM-A",
        event=_event(session_a, 0, True),
    )
    now[0] += 6
    _, stopped = _activate(
        service,
        token=token,
        device="ROAM-A",
        event=_event(session_a, 1, False),
    )
    assert _pet(stopped)["remaining_seconds"] == 86_394

    restarted = _service(tmp_path)
    _, moved = _activate(restarted, token=token, device="ROAM-B")
    assert _pet(moved)["remaining_seconds"] == 86_394
    assert _pet(moved)["status"] == "available"


def test_pet_state_is_privacy_minimized_and_corruption_denies_only_pet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    service = _service(tmp_path)
    token = _token("PRIVATE-UID-MUST-NOT-PERSIST", 30)
    raw_device = "PRIVATE-DEVICE-MUST-NOT-PERSIST"
    raw_session = "private-session-must-not-persist"
    code, body = _activate(
        service,
        token=token,
        device=raw_device,
        event=_event(raw_session, 0, True),
    )
    assert code == 200
    pet_usage_db = tmp_path / "pet-authority" / "pet_usage.sqlite3"
    raw_state = b"".join(
        path.read_bytes()
        for path in (
            pet_usage_db,
            pet_usage_db.with_name(f"{pet_usage_db.name}-wal"),
            pet_usage_db.with_name(f"{pet_usage_db.name}-shm"),
        )
        if path.exists()
    )
    for canary in (
        b"PRIVATE-UID",
        raw_device.encode("utf-8"),
        raw_session.encode("utf-8"),
        token.encode("utf-8"),
    ):
        assert canary not in raw_state

    pet_usage_db.write_bytes(b"not-a-sqlite-database")
    code, body = _activate(service, token=token, device=raw_device)
    assert code == 200
    assert body["ok"] is True
    assert _pet(body)["status"] == "state_error"
    assert "master_key" in body


def test_30_to_90_new_authoritative_entitlement_becomes_full(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    service = _service(tmp_path)
    _, monthly = _activate(
        service,
        token=_token("order-monthly", 30),
        device="UPGRADE-DEVICE",
    )
    assert _pet(monthly)["kind"] == "quota"
    assert _pet(monthly)["total_seconds"] == 43_200

    _, quarterly = _activate(
        service,
        token=_token("order-quarterly", 90),
        device="UPGRADE-DEVICE",
    )
    assert _pet(quarterly)["kind"] == "full"
    assert _pet(quarterly)["entitlement_exp"] == _lease(quarterly)["paid_exp"]


def test_revoked_paid_token_never_receives_pet_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server.time, "time", lambda: BASE_NOW)
    service = _service(tmp_path)
    token = _token("refunded-order", 60)
    (tmp_path / "revoked.txt").write_text("refunded-order\n", encoding="utf-8")
    code, body = _activate(service, token=token, device="REFUND-DEVICE")
    assert code == 403
    assert body == {"ok": False, "mode": "paid", "reason": "revoked"}
    pet_usage_db = tmp_path / "pet-authority" / "pet_usage.sqlite3"
    with sqlite3.connect(pet_usage_db) as connection:
        assert connection.execute(
            "SELECT revision FROM pet_usage_state WHERE singleton=1"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM pet_usage_commits"
        ).fetchone() == (0,)
