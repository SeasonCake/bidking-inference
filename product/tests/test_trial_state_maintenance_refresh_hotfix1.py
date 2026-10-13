from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import activation_core


def _record(*, start: int, window: int, last: int) -> dict:
    return {
        "schema_version": 2,
        "start": start,
        "window_seconds": window,
        "last_server_now": last,
    }


def _load_admin():
    path = SCRIPTS / "trial_state_admin.py"
    spec = importlib.util.spec_from_file_location(
        "trial_state_maintenance_refresh_hotfix1",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_refresh_clears_expired_shorter_and_longer_records_for_next_activation() -> None:
    now = 2_000_000
    refresh_at = now - 10
    trials = {
        "expired-12h": _record(
            start=now - 50_000,
            window=12 * 3600,
            last=now - 20,
        ),
        "short-active-3h": _record(
            start=now - 1_000,
            window=3 * 3600,
            last=now - 20,
        ),
        "long-active-12h": _record(
            start=now - 1_000,
            window=12 * 3600,
            last=now - 20,
        ),
    }

    refreshed = activation_core.reset_trials_for_public_refresh(
        trials,
        refresh_at=refresh_at,
        trial_hours=12,
        now=now,
    )

    assert refreshed == {}
    assert trials["expired-12h"]["start"] == now - 50_000


def test_refresh_is_idempotent_for_the_same_maintenance_boundary() -> None:
    now = 2_000_000
    refresh_at = now - 10
    trials = {
        "old": _record(start=now - 50_000, window=12 * 3600, last=now - 20)
    }
    first = activation_core.reset_trials_for_public_refresh(
        trials,
        refresh_at=refresh_at,
        now=now,
    )
    second = activation_core.reset_trials_for_public_refresh(
        first,
        refresh_at=refresh_at,
        now=now,
    )
    assert second == first


def test_next_no_token_activation_receives_fresh_twelve_hours() -> None:
    now = 2_000_000
    refresh_at = now - 10
    cleared = activation_core.reset_trials_for_public_refresh(
        {
            "old": _record(
                start=now - 50_000,
                window=12 * 3600,
                last=now - 20,
            )
        },
        refresh_at=refresh_at,
        now=now,
    )

    request_at = now + 5
    ok, reason, lease = activation_core.grant_trial(
        "old",
        trials=cleared,
        trial_hours=12,
        grandfather_cutover_at=now - 100_000,
        grandfather_trial_hours=12,
        now=request_at,
    )

    assert ok is True
    assert reason == "ok"
    assert lease is not None and lease["exp"] == request_at + 12 * 3600
    assert cleared["old"] == _record(
        start=request_at,
        window=12 * 3600,
        last=request_at,
    )


@pytest.mark.parametrize("last_offset", (0, 5))
def test_refresh_rejects_a_record_at_or_after_the_frozen_boundary_atomically(
    last_offset: int,
) -> None:
    now = 2_000_000
    refresh_at = now - 10
    trials = {
        "safe": _record(start=now - 50_000, window=12 * 3600, last=now - 20),
        "raced": _record(
            start=now - 1_000,
            window=3 * 3600,
            last=refresh_at + last_offset,
        ),
    }
    original = json.loads(json.dumps(trials))

    with pytest.raises(
        ValueError,
        match="changed at or after the refresh boundary",
    ):
        activation_core.reset_trials_for_public_refresh(
            trials,
            refresh_at=refresh_at,
            now=now,
        )

    assert trials == original


@pytest.mark.parametrize(
    ("refresh_at", "trial_hours"),
    (
        (True, 12),
        (2_000_001, 12),
        (2_000_000, 0),
        (2_000_000, 3),
        (2_000_000, float("nan")),
    ),
)
def test_refresh_rejects_invalid_policy_values(refresh_at, trial_hours) -> None:
    with pytest.raises((TypeError, ValueError, OverflowError)):
        activation_core.reset_trials_for_public_refresh(
            {},
            refresh_at=refresh_at,
            trial_hours=trial_hours,
            now=2_000_000,
        )


def test_admin_refresh_dry_run_and_apply_are_cas_backed_and_privacy_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    admin = _load_admin()
    now = 2_000_000
    refresh_at = now - 10
    monkeypatch.setattr(admin.time, "time", lambda: now)
    state = tmp_path / "trials.json"
    backup = tmp_path / "trials.before-refresh.json"
    records = {
        "private-device-a": _record(
            start=now - 50_000,
            window=12 * 3600,
            last=now - 20,
        ),
        "private-device-b": _record(
            start=now - 1_000,
            window=12 * 3600,
            last=now - 20,
        ),
    }
    state.write_text(json.dumps(records, separators=(",", ":")), encoding="utf-8")
    original = state.read_bytes()
    expected_sha = hashlib.sha256(original).hexdigest()
    common = [
        "--state-file",
        str(state),
        "--mode",
        "refresh",
        "--refresh-at",
        str(refresh_at),
        "--refresh-hours",
        "12",
    ]

    assert admin.main(common) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run == {
        "input_sha256": expected_sha,
        "input_records": 2,
        "lock_backend": "sqlite-begin-exclusive+claim-link-noreplace",
        "lock_protocol_version": 3,
        "mode": "dry_run_refresh",
        "ok": True,
        "records": 0,
        "removed_records": 2,
    }
    assert state.read_bytes() == original

    assert admin.main(
        [
            *common,
            "--apply",
            "--expected-sha256",
            expected_sha,
            "--backup-file",
            str(backup),
        ]
    ) == 0
    applied_text = capsys.readouterr().out
    applied = json.loads(applied_text)
    assert applied["mode"] == "applied_refresh"
    assert applied["records"] == 0
    assert applied["input_records"] == 2
    assert applied["removed_records"] == 2
    assert applied["input_sha256"] == expected_sha
    assert applied["backup_sha256"] == expected_sha
    assert applied["output_sha256"] == hashlib.sha256(state.read_bytes()).hexdigest()
    assert backup.read_bytes() == original
    assert "private-device" not in applied_text

    assert json.loads(state.read_text(encoding="utf-8")) == {}


def test_admin_rejects_same_second_new_generation_with_its_current_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    admin = _load_admin()
    refresh_at = 2_000_000
    monkeypatch.setattr(admin.time, "time", lambda: refresh_at)
    state = tmp_path / "trials.json"
    first_backup = tmp_path / "trials.before-first-refresh.json"
    second_backup = tmp_path / "trials.before-replayed-refresh.json"
    state.write_text(
        json.dumps(
            {
                "old": _record(
                    start=refresh_at - 50_000,
                    window=12 * 3600,
                    last=refresh_at - 1,
                )
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    first_sha = hashlib.sha256(state.read_bytes()).hexdigest()
    common = [
        "--state-file",
        str(state),
        "--mode",
        "refresh",
        "--refresh-at",
        str(refresh_at),
        "--refresh-hours",
        "12",
    ]

    assert admin.main(
        [
            *common,
            "--apply",
            "--expected-sha256",
            first_sha,
            "--backup-file",
            str(first_backup),
        ]
    ) == 0
    capsys.readouterr()
    assert json.loads(state.read_text(encoding="utf-8")) == {}

    next_generation: dict = {}
    ok, reason, lease = activation_core.grant_trial(
        "private-same-second-device",
        trials=next_generation,
        trial_hours=12,
        grandfather_cutover_at=refresh_at - 1,
        grandfather_trial_hours=12,
        now=refresh_at,
    )
    assert ok is True and reason == "ok" and lease is not None
    state.write_text(
        json.dumps(next_generation, separators=(",", ":")),
        encoding="utf-8",
    )
    next_generation_bytes = state.read_bytes()
    next_generation_sha = hashlib.sha256(next_generation_bytes).hexdigest()

    with pytest.raises(
        ValueError,
        match="changed at or after the refresh boundary",
    ):
        admin.main(
            [
                *common,
                "--apply",
                "--expected-sha256",
                next_generation_sha,
                "--backup-file",
                str(second_backup),
            ]
        )

    assert state.read_bytes() == next_generation_bytes
    assert not second_backup.exists()
    assert "private-same-second-device" not in capsys.readouterr().out
