from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import activation_core as core  # noqa: E402
import activation_server as server  # noqa: E402


SECRET = b"trial-grandfathering-test-secret"
MASTER_KEY = "dHJpYWwtZ3JhbmRmYXRoZXJpbmctbWFzdGVyLWtleQ=="
CUTOVER = 1_700_000_100
OLD_START = CUTOVER - 600


def _record(start: int, window: int, last: int) -> dict:
    return {
        "schema_version": core.TRIAL_RECORD_SCHEMA_VERSION,
        "start": start,
        "window_seconds": window,
        "last_server_now": last,
    }


class _BombInt(int):
    calls = 0

    def _boom(self, *_args, **_kwargs):
        type(self).calls += 1
        raise RuntimeError("private trial scalar")

    __int__ = _boom
    __float__ = _boom
    __index__ = _boom
    __eq__ = _boom
    __lt__ = _boom
    __mul__ = _boom


class _BombDict(dict):
    calls = 0

    def _boom(self, *_args, **_kwargs):
        type(self).calls += 1
        raise RuntimeError("private trial container")

    get = _boom
    items = _boom
    keys = _boom
    __iter__ = _boom
    __contains__ = _boom
    __len__ = _boom
    __eq__ = _boom


class _ArmedStr(str):
    calls = 0
    armed = False

    def __hash__(self):
        if type(self).armed:
            type(self).calls += 1
            raise RuntimeError("private trial key")
        return str.__hash__(self)

    def __eq__(self, other):
        if type(self).armed:
            type(self).calls += 1
            raise RuntimeError("private trial key")
        return str.__eq__(self, other)


def _service(
    root: Path,
    *,
    trial_hours: float,
    cutover: int | None = None,
    grandfather_hours: float | None = None,
) -> server.ActivationService:
    root.mkdir(parents=True, exist_ok=True)
    db = root / "pet_usage.sqlite3"
    if not db.exists():
        server.initialize_pet_usage_database(
            db,
            secret=SECRET,
            legacy_paths=(
                root / "pet_usage.json",
                root / "pet_usage.anchor.jsonl",
            ),
        )
    return server.ActivationService(
        secret=SECRET,
        master_key_b64=MASTER_KEY,
        bindings_path=root / "bindings.json",
        revoked_path=root / "revoked.txt",
        telemetry_path=root / "telemetry.jsonl",
        trials_path=root / "trials.json",
        pet_usage_db_path=db,
        trial_hours=trial_hours,
        trial_grandfather_cutover_at=cutover,
        trial_grandfather_hours=grandfather_hours,
    )


def test_offline_migration_preserves_old_window_and_new_device_gets_three_hours():
    original = {
        "old-int": OLD_START,
        "old-usage": {"used": 120, "last": CUTOVER - 5},
    }
    migrated = core.migrate_trials_for_cutover(
        original,
        cutover_at=CUTOVER,
        grandfather_trial_hours=12.0,
        now=CUTOVER - 1,
    )

    assert migrated["old-int"] == _record(
        OLD_START,
        12 * 3600,
        CUTOVER - 1,
    )
    assert migrated["old-usage"] == _record(
        CUTOVER - 121,
        12 * 3600,
        CUTOVER - 1,
    )

    trials = dict(migrated)
    ok, reason, old_lease = core.grant_trial(
        "old-int",
        trials=trials,
        trial_hours=3.0,
        grandfather_cutover_at=CUTOVER,
        grandfather_trial_hours=12.0,
        now=CUTOVER + 1,
    )
    assert ok and reason == "ok"
    assert old_lease["exp"] == OLD_START + 12 * 3600

    ok, reason, new_lease = core.grant_trial(
        "new",
        trials=trials,
        trial_hours=3.0,
        grandfather_cutover_at=CUTOVER,
        grandfather_trial_hours=12.0,
        now=CUTOVER + 1,
    )
    assert ok and reason == "ok"
    assert new_lease["exp"] == CUTOVER + 1 + 3 * 3600
    assert trials["new"] == _record(
        CUTOVER + 1,
        3 * 3600,
        CUTOVER + 1,
    )


@pytest.mark.parametrize(
    "legacy",
    [
        OLD_START,
        {"used": 60, "last": OLD_START},
    ],
)
def test_cutover_runtime_rejects_unmigrated_legacy_records(legacy):
    trials = {"device": legacy}
    before = json.loads(json.dumps(trials))
    result = core.grant_trial(
        "device",
        trials=trials,
        trial_hours=3.0,
        grandfather_cutover_at=CUTOVER,
        grandfather_trial_hours=12.0,
        now=CUTOVER + 1,
    )
    assert result == (False, "state_error", None)
    assert trials == before


def test_cutover_boundary_is_new_policy_and_preboundary_record_is_grandfathered():
    trials = {
        "before": _record(OLD_START, 12 * 3600, CUTOVER - 1),
    }
    before = core.grant_trial(
        "before",
        trials=trials,
        trial_hours=3,
        grandfather_cutover_at=CUTOVER,
        grandfather_trial_hours=12,
        now=CUTOVER,
    )
    boundary = core.grant_trial(
        "at-boundary",
        trials=trials,
        trial_hours=3,
        grandfather_cutover_at=CUTOVER,
        grandfather_trial_hours=12,
        now=CUTOVER,
    )
    assert before[0] and before[2]["exp"] == OLD_START + 12 * 3600
    assert boundary[0] and boundary[2]["exp"] == CUTOVER + 3 * 3600


@pytest.mark.parametrize(
    ("cutover", "grandfather"),
    [
        (CUTOVER, None),
        (None, 12.0),
        (True, 12.0),
        (str(CUTOVER), 12.0),
        (CUTOVER, True),
        (CUTOVER, float("inf")),
        (CUTOVER, 2.0),
    ],
)
def test_invalid_cutover_configuration_is_atomic_fail_closed(cutover, grandfather):
    trials = {"device": OLD_START}
    assert core.grant_trial(
        "device",
        trials=trials,
        trial_hours=3.0,
        grandfather_cutover_at=cutover,
        grandfather_trial_hours=grandfather,
        now=CUTOVER + 1,
    ) == (False, "state_error", None)
    assert trials == {"device": OLD_START}


def test_future_cutover_and_clock_rollback_fail_closed_without_mutation():
    trials: dict = {}
    assert core.grant_trial(
        "new",
        trials=trials,
        trial_hours=3,
        grandfather_cutover_at=CUTOVER + 2,
        grandfather_trial_hours=12,
        now=CUTOVER + 1,
    ) == (False, "state_error", None)
    assert trials == {}

    existing = {
        "old": _record(OLD_START, 12 * 3600, CUTOVER + 20),
    }
    before = json.loads(json.dumps(existing))
    assert core.grant_trial(
        "old",
        trials=existing,
        trial_hours=3,
        grandfather_cutover_at=CUTOVER,
        grandfather_trial_hours=12,
        now=CUTOVER + 19,
    ) == (False, "state_error", None)
    assert existing == before


@pytest.mark.parametrize("now", [-1, 0, 10**400, True])
def test_invalid_server_time_is_atomic_fail_closed(now):
    trials: dict = {}
    assert core.grant_trial(
        "new",
        trials=trials,
        trial_hours=3,
        now=now,
    ) == (False, "state_error", None)
    assert trials == {}


@pytest.mark.parametrize(
    "record",
    [
        {
            "schema_version": 2,
            "start": OLD_START,
            "window_seconds": True,
            "last_server_now": CUTOVER,
        },
        {
            "schema_version": 2,
            "start": OLD_START,
            "window_seconds": 12 * 3600,
            "last_server_now": CUTOVER,
            "extra": 1,
        },
        {
            "schema_version": 2,
            "start": OLD_START,
            "window_seconds": 3 * 3600,
            "last_server_now": CUTOVER,
        },
    ],
)
def test_invalid_or_wrong_policy_fixed_record_fails_closed(record):
    trials = {"old": record}
    before = json.loads(json.dumps(trials))
    assert core.grant_trial(
        "old",
        trials=trials,
        trial_hours=3,
        grandfather_cutover_at=CUTOVER,
        grandfather_trial_hours=12,
        now=CUTOVER + 1,
    ) == (False, "state_error", None)
    assert trials == before


def test_service_two_phase_cutover_preserves_existing_and_persists_new_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    clock = {"now": CUTOVER - 10}
    monkeypatch.setattr(server.time, "time", lambda: clock["now"])
    monkeypatch.setattr(core.time, "time", lambda: clock["now"])
    phase_a = _service(tmp_path, trial_hours=12)
    code, first = phase_a.handle_activate({"device": "existing"})
    assert code == 200
    first_exp = first["trial_exp"]

    state_path = tmp_path / "trials.json"
    current = json.loads(state_path.read_text(encoding="utf-8"))
    migrated = core.migrate_trials_for_cutover(
        current,
        cutover_at=CUTOVER,
        grandfather_trial_hours=12,
        now=CUTOVER - 1,
    )
    state_path.write_text(json.dumps(migrated), encoding="utf-8")

    clock["now"] = CUTOVER + 1
    phase_b = _service(
        tmp_path,
        trial_hours=3,
        cutover=CUTOVER,
        grandfather_hours=12,
    )
    old_code, old = phase_b.handle_activate({"device": "existing"})
    new_code, new = phase_b.handle_activate({"device": "new"})
    assert old_code == new_code == 200
    assert old["trial_exp"] == first_exp
    assert new["trial_exp"] == CUTOVER + 1 + 3 * 3600

    clock["now"] = CUTOVER + 2
    again_code, again = phase_b.handle_activate({"device": "new"})
    assert again_code == 200
    assert again["trial_exp"] == new["trial_exp"]


def test_service_concurrent_first_request_has_one_stable_three_hour_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(server.time, "time", lambda: CUTOVER + 1)
    monkeypatch.setattr(core.time, "time", lambda: CUTOVER + 1)
    service = _service(
        tmp_path,
        trial_hours=3,
        cutover=CUTOVER,
        grandfather_hours=12,
    )
    barrier = threading.Barrier(3)
    results: list[tuple[int, dict]] = []

    def activate() -> None:
        barrier.wait()
        results.append(service.handle_activate({"device": "concurrent"}))

    threads = [threading.Thread(target=activate) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)

    assert [code for code, _body in results] == [200, 200]
    assert {body["trial_exp"] for _code, body in results} == {
        CUTOVER + 1 + 3 * 3600
    }
    state = json.loads((tmp_path / "trials.json").read_text(encoding="utf-8"))
    assert state == {
        "concurrent": _record(
            CUTOVER + 1,
            3 * 3600,
            CUTOVER + 1,
        )
    }


def test_service_clock_rollback_rejects_without_regrant_and_recovers_forward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    clock = {"now": CUTOVER + 1}
    monkeypatch.setattr(server.time, "time", lambda: clock["now"])
    monkeypatch.setattr(core.time, "time", lambda: clock["now"])
    service = _service(
        tmp_path,
        trial_hours=3,
        cutover=CUTOVER,
        grandfather_hours=12,
    )
    first_code, first = service.handle_activate({"device": "clocked"})
    assert first_code == 200

    clock["now"] = CUTOVER + 2
    second_code, second = service.handle_activate({"device": "clocked"})
    assert second_code == 200
    assert second["trial_exp"] == first["trial_exp"]
    state_path = tmp_path / "trials.json"
    authoritative = state_path.read_bytes()

    clock["now"] = CUTOVER + 1
    restarted = _service(
        tmp_path,
        trial_hours=3,
        cutover=CUTOVER,
        grandfather_hours=12,
    )
    rollback_code, rollback = restarted.handle_activate({"device": "clocked"})
    assert rollback_code == 503
    assert rollback == {"ok": False, "mode": "trial", "reason": "state_error"}
    assert state_path.read_bytes() == authoritative

    clock["now"] = CUTOVER + 3
    recovered_code, recovered = restarted.handle_activate({"device": "clocked"})
    assert recovered_code == 200
    assert recovered["trial_exp"] == first["trial_exp"]


def test_offline_admin_requires_exact_hash_and_writes_recoverable_backup(
    tmp_path: Path,
):
    now = int(__import__("time").time())
    cutover = now + 1
    state = tmp_path / "trials.json"
    backup = tmp_path / "backup" / "trials.before.json"
    state.write_text(json.dumps({"old": now - 60}), encoding="utf-8")
    original = state.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    command = [
        sys.executable,
        str(SCRIPTS / "trial_state_admin.py"),
        "--state-file",
        str(state),
        "--mode",
        "migrate",
        "--cutover-at",
        str(cutover),
        "--grandfather-hours",
        "12",
        "--apply",
        "--expected-sha256",
        digest,
        "--backup-file",
        str(backup),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert backup.read_bytes() == original
    migrated = json.loads(state.read_text(encoding="utf-8"))
    assert migrated["old"]["window_seconds"] == 12 * 3600
    assert migrated["old"]["start"] == now - 60

    mismatch = subprocess.run(
        [*command[:-4], "--expected-sha256", "0" * 64, "--backup-file", str(tmp_path / "never")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert mismatch.returncode == 2


def test_downgrade_preserves_exact_expiry_for_old_and_new_records():
    now = CUTOVER + 60
    trials = {
        "old": _record(OLD_START, 12 * 3600, now),
        "new": _record(CUTOVER + 1, 3 * 3600, now),
    }
    downgraded = core.downgrade_trials_for_legacy_runtime(
        trials,
        legacy_trial_hours=12,
        now=now,
    )
    assert downgraded["old"] + 12 * 3600 == OLD_START + 12 * 3600
    assert downgraded["new"] + 12 * 3600 == CUTOVER + 1 + 3 * 3600

    for device, expected_exp in (
        ("old", OLD_START + 12 * 3600),
        ("new", CUTOVER + 1 + 3 * 3600),
    ):
        state = dict(downgraded)
        ok, reason, lease = core.grant_trial(
            device,
            trials=state,
            trial_hours=12,
            now=now,
        )
        assert ok and reason == "ok"
        assert lease["exp"] == expected_exp


def test_downgrade_is_all_or_nothing_and_rejects_legacy_or_future_state():
    valid = _record(OLD_START, 12 * 3600, CUTOVER)
    for invalid in (
        OLD_START,
        {"used": 1, "last": CUTOVER},
        _record(OLD_START, 12 * 3600, CUTOVER + 2),
    ):
        original = {"valid": valid, "invalid": invalid}
        before = json.loads(json.dumps(original))
        with pytest.raises(ValueError):
            core.downgrade_trials_for_legacy_runtime(
                original,
                legacy_trial_hours=12,
                now=CUTOVER + 1,
            )
        assert original == before


def test_trial_projection_rejects_protocol_subclasses_without_magic():
    _BombInt.calls = _BombDict.calls = _ArmedStr.calls = 0
    bomb_trials = _BombDict({"device": OLD_START})
    assert core.grant_trial(
        "device",
        trials=bomb_trials,
        trial_hours=3,
        now=CUTOVER,
    ) == (False, "state_error", None)

    normal = {"device": OLD_START}
    assert core.grant_trial(
        "device",
        trials=normal,
        trial_hours=_BombInt(3),
        now=CUTOVER,
    ) == (False, "state_error", None)

    bad_key = _ArmedStr("device")
    keyed = {bad_key: OLD_START}
    _ArmedStr.armed = True
    try:
        assert core.grant_trial(
            "device",
            trials=keyed,
            trial_hours=3,
            now=CUTOVER,
        ) == (False, "state_error", None)
        with pytest.raises(ValueError):
            core.migrate_trials_for_cutover(
                keyed,
                cutover_at=CUTOVER + 1,
                grandfather_trial_hours=12,
                now=CUTOVER,
            )
    finally:
        _ArmedStr.armed = False

    assert _BombInt.calls == _BombDict.calls == _ArmedStr.calls == 0


def test_record_projection_rejects_dict_and_key_subclasses_without_magic():
    _BombDict.calls = _ArmedStr.calls = 0
    trials = {
        "device": _BombDict(_record(OLD_START, 12 * 3600, CUTOVER)),
    }
    original_record = dict.__getitem__(trials, "device")
    assert core.grant_trial(
        "device",
        trials=trials,
        trial_hours=3,
        grandfather_cutover_at=CUTOVER,
        grandfather_trial_hours=12,
        now=CUTOVER + 1,
    ) == (False, "state_error", None)
    assert dict.__getitem__(trials, "device") is original_record

    bad_key = _ArmedStr("start")
    record = _record(OLD_START, 12 * 3600, CUTOVER)
    record[bad_key] = record.pop("start")
    keyed_trials = {"device": record}
    _ArmedStr.armed = True
    try:
        assert core.grant_trial(
            "device",
            trials=keyed_trials,
            trial_hours=3,
            grandfather_cutover_at=CUTOVER,
            grandfather_trial_hours=12,
            now=CUTOVER + 1,
        ) == (False, "state_error", None)
    finally:
        _ArmedStr.armed = False
    assert _BombDict.calls == _ArmedStr.calls == 0


def test_offline_admin_downgrade_keeps_post_cutover_users_and_expiries(
    tmp_path: Path,
):
    now = int(__import__("time").time())
    state = tmp_path / "trials.json"
    backup = tmp_path / "trials.v2.backup.json"
    old_start = now - 60
    new_start = now - 30
    records = {
        "old": _record(old_start, 12 * 3600, now),
        "new": _record(new_start, 3 * 3600, now),
    }
    state.write_text(json.dumps(records), encoding="utf-8")
    original = state.read_bytes()
    command = [
        sys.executable,
        str(SCRIPTS / "trial_state_admin.py"),
        "--state-file",
        str(state),
        "--mode",
        "downgrade",
        "--downgrade-hours",
        "12",
        "--apply",
        "--expected-sha256",
        hashlib.sha256(original).hexdigest(),
        "--backup-file",
        str(backup),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert backup.read_bytes() == original
    legacy = json.loads(state.read_text(encoding="utf-8"))
    assert set(legacy) == {"old", "new"}
    assert legacy["old"] + 12 * 3600 == old_start + 12 * 3600
    assert legacy["new"] + 12 * 3600 == new_start + 3 * 3600
