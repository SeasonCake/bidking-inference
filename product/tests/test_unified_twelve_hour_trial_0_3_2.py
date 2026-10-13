from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import activation_core as core  # noqa: E402
import activation_server as server  # noqa: E402


CUTOVER = 2_000_000
THREE_HOURS = 3 * 3600
TWELVE_HOURS = 12 * 3600
SECRET = b"unified-twelve-hour-trial-test-secret"
MASTER_KEY = "dW5pZmllZC10d2VsdmUtaG91ci10cmlhbC1tYXN0ZXIta2V5"


def _record(*, start: int, window: int, last: int) -> dict:
    return {
        "schema_version": core.TRIAL_RECORD_SCHEMA_VERSION,
        "start": start,
        "window_seconds": window,
        "last_server_now": last,
    }


def _grant(device: str, trials: dict, *, now: int, **overrides):
    options = {
        "trial_hours": 12,
        "grandfather_cutover_at": CUTOVER,
        "grandfather_trial_hours": 12,
        "now": now,
    }
    options.update(overrides)
    return core.grant_trial(device, trials=trials, **options)


def test_new_default_trial_is_one_twelve_hour_natural_window() -> None:
    now = CUTOVER + 10
    trials: dict = {}

    ok, reason, lease = core.grant_trial("new-device", trials=trials, now=now)

    assert (ok, reason) == (True, "ok")
    assert lease is not None and lease["exp"] == now + TWELVE_HOURS
    assert trials == {
        "new-device": _record(start=now, window=TWELVE_HOURS, last=now)
    }
    assert core.DEFAULT_TRIAL_HOURS == 12.0
    assert core.UNIFIED_TRIAL_WINDOW_SECONDS == TWELVE_HOURS


@pytest.mark.parametrize("start", (CUTOVER - 100, CUTOVER + 100))
def test_unified_policy_expands_legacy_three_hour_record_from_original_start(
    start: int,
) -> None:
    now = start + 1_000
    trials = {
        "legacy-short": _record(
            start=start,
            window=THREE_HOURS,
            last=now - 10,
        )
    }

    ok, reason, lease = _grant("legacy-short", trials, now=now)

    assert (ok, reason) == (True, "ok")
    assert lease is not None and lease["exp"] == start + TWELVE_HOURS
    assert trials["legacy-short"] == _record(
        start=start,
        window=TWELVE_HOURS,
        last=now,
    )


def test_expired_legacy_three_hour_record_is_not_given_a_second_window() -> None:
    start = CUTOVER + 100
    now = start + TWELVE_HOURS
    trials = {
        "expired": _record(
            start=start,
            window=THREE_HOURS,
            last=start + 100,
        )
    }

    ok, reason, lease = _grant("expired", trials, now=now)

    assert (ok, reason, lease) == (False, "trial_expired", None)
    assert trials["expired"] == _record(
        start=start,
        window=TWELVE_HOURS,
        last=now,
    )


def test_existing_twelve_hour_record_keeps_its_start_and_expiry() -> None:
    start = CUTOVER - 100
    now = CUTOVER + 100
    trials = {
        "existing": _record(
            start=start,
            window=TWELVE_HOURS,
            last=now - 10,
        )
    }

    ok, reason, lease = _grant("existing", trials, now=now)

    assert (ok, reason) == (True, "ok")
    assert lease is not None and lease["exp"] == start + TWELVE_HOURS
    assert trials["existing"] == _record(
        start=start,
        window=TWELVE_HOURS,
        last=now,
    )


def test_explicit_legacy_three_hour_configuration_is_the_revert_control() -> None:
    start = CUTOVER + 100
    now = start + 1_000
    trials = {
        "legacy-config": _record(
            start=start,
            window=THREE_HOURS,
            last=now - 10,
        )
    }

    ok, reason, lease = _grant(
        "legacy-config",
        trials,
        now=now,
        trial_hours=3,
        grandfather_trial_hours=12,
    )

    assert (ok, reason) == (True, "ok")
    assert lease is not None and lease["exp"] == start + THREE_HOURS
    assert trials["legacy-config"]["window_seconds"] == THREE_HOURS


@pytest.mark.parametrize(
    "record",
    (
        _record(start=CUTOVER + 1, window=6 * 3600, last=CUTOVER + 2),
        _record(start=CUTOVER + 1, window=True, last=CUTOVER + 2),
        {
            "schema_version": 2,
            "start": CUTOVER + 1,
            "window_seconds": "10800",
            "last_server_now": CUTOVER + 2,
        },
        {
            "schema_version": 2,
            "start": CUTOVER + 1,
            "window_seconds": THREE_HOURS,
            "last_server_now": CUTOVER + 2,
            "extra": 1,
        },
    ),
)
def test_unified_policy_rejects_noncanonical_or_unknown_records_atomically(
    record: dict,
) -> None:
    trials = {"invalid": copy.deepcopy(record)}
    before = copy.deepcopy(trials)

    assert _grant("invalid", trials, now=CUTOVER + 10) == (
        False,
        "state_error",
        None,
    )
    assert trials == before


def test_unified_policy_keeps_clock_rollback_fail_closed_without_migration() -> None:
    trials = {
        "rollback": _record(
            start=CUTOVER + 1,
            window=THREE_HOURS,
            last=CUTOVER + 20,
        )
    }
    before = copy.deepcopy(trials)

    assert _grant("rollback", trials, now=CUTOVER + 10) == (
        False,
        "state_error",
        None,
    )
    assert trials == before


def test_public_refresh_accepts_only_unified_policy_and_next_default_is_twelve_hours() -> None:
    now = CUTOVER + 100
    current = {
        "old": _record(
            start=CUTOVER - 100,
            window=THREE_HOURS,
            last=now - 20,
        )
    }

    cleared = core.reset_trials_for_public_refresh(
        current,
        refresh_at=now - 10,
        now=now,
    )
    with pytest.raises(ValueError, match="exactly twelve hours"):
        core.reset_trials_for_public_refresh(
            current,
            refresh_at=now - 10,
            trial_hours=3,
            now=now,
        )

    request_at = now + 1
    ok, reason, lease = core.grant_trial(
        "old",
        trials=cleared,
        now=request_at,
    )
    assert (ok, reason) == (True, "ok")
    assert lease is not None and lease["exp"] == request_at + TWELVE_HOURS


def test_activation_service_persists_old_three_hour_record_as_twelve_hours(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = CUTOVER + 100
    now = start + 1_000
    trials_path = tmp_path / "trials.json"
    trials_path.write_text(
        json.dumps(
            {
                "service-device": _record(
                    start=start,
                    window=THREE_HOURS,
                    last=now - 10,
                )
            }
        ),
        encoding="utf-8",
    )
    pet_db = tmp_path / "pet_usage.sqlite3"
    server.initialize_pet_usage_database(
        pet_db,
        secret=SECRET,
        legacy_paths=(
            tmp_path / "pet_usage.json",
            tmp_path / "pet_usage.anchor.jsonl",
        ),
    )
    service = server.ActivationService(
        secret=SECRET,
        master_key_b64=MASTER_KEY,
        bindings_path=tmp_path / "bindings.json",
        revoked_path=tmp_path / "revoked.txt",
        telemetry_path=tmp_path / "telemetry.jsonl",
        trials_path=trials_path,
        pet_usage_db_path=pet_db,
        trial_hours=12,
        trial_grandfather_cutover_at=CUTOVER,
        trial_grandfather_hours=12,
    )
    monkeypatch.setattr(server.time, "time", lambda: now)

    status, response = service.handle_activate({"device": "service-device"})

    assert status == 200
    assert response["mode"] == "trial"
    assert response["trial_exp"] == start + TWELVE_HOURS
    persisted = json.loads(trials_path.read_text(encoding="utf-8"))
    assert persisted["service-device"] == _record(
        start=start,
        window=TWELVE_HOURS,
        last=now,
    )


def test_deploy_default_and_public_package_terms_do_not_reintroduce_three_hours() -> None:
    deploy = (SCRIPTS / "activation_state_aware_deploy.py").read_text(
        encoding="utf-8-sig"
    )
    collector = (SCRIPTS / "sample_collector_server.py").read_text(
        encoding="utf-8-sig"
    )
    manual = (SCRIPTS / "pkg_assets" / "使用说明.txt").read_text(
        encoding="utf-8-sig"
    )
    build = (SCRIPTS / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8-sig")

    assert '"--new-trial-hours",\n        type=_positive_float,\n        default=12.0' in deploy
    assert "default=activation_core.DEFAULT_TRIAL_HOURS" in collector
    for public_text in (manual, build, license_text):
        assert "12 小时" in public_text
        assert "3 小时" not in public_text
