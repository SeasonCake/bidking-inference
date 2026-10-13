# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path

from bidking_lab.secure import client_activate as ca


def _transport_rows(*, timestamp: int) -> tuple[tuple[str, str, str, int], ...]:
    return (
        ("endpoint_1", "tls", "tls_connection_reset", timestamp),
        ("endpoint_2", "tcp", "connect_timeout", timestamp),
    )


def _sequence(at: int, *, recovered_at: int | None) -> dict[str, object]:
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


def _write_history(path: Path, *, now: int, sequences: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps({"schema": 1, "updated_at": now, "sequences": sequences}),
        encoding="utf-8",
    )


def test_recheck_ring_survives_recovery_without_identity_or_endpoint_leak(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(os, "getpid", lambda: 4242)

    ca.record_activation_recheck_outcome(
        _transport_rows(timestamp=2_000_000_000),
        allow_new_sequence=True,
        recovered=False,
        now=2_000_000_000,
    )
    ca.record_activation_recheck_outcome(
        _transport_rows(timestamp=2_000_000_002),
        allow_new_sequence=True,
        recovered=False,
        now=2_000_000_002,
    )
    ca.record_activation_recheck_outcome(
        (("endpoint_1", "http", "http_response", 2_000_000_004),),
        allow_new_sequence=False,
        recovered=True,
        now=2_000_000_004,
    )

    history = ca.read_activation_recheck_history(now=2_000_000_005)
    assert history["schema"] == 1
    assert len(history["sequences"]) == 1
    sequence = history["sequences"][0]
    assert sequence["attempt_count"] == 4
    assert sequence["recovered_at"] == 2_000_000_004
    assert [row["endpoint"] for row in sequence["attempts"]] == [
        "endpoint_1",
        "endpoint_2",
        "endpoint_1",
        "endpoint_2",
    ]
    serialized = json.dumps(history, ensure_ascii=False)
    for forbidden in (
        "https://",
        "api.bidking.example.invalid",
        "TOKEN-A",
        "DEVICE-A",
        "198.51.100.9",
        "private exception",
    ):
        assert forbidden not in serialized


def test_recheck_ring_is_bounded_and_expired_rows_are_omitted(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(os, "getpid", lambda: 4343)
    start = 2_000_000_000
    for index in range(ca._ACTIVATION_RECHECK_HISTORY_MAX_SEQUENCES + 2):
        at = start + index * 10
        ca.record_activation_recheck_outcome(
            _transport_rows(timestamp=at),
            allow_new_sequence=True,
            recovered=False,
            now=at,
        )
        ca.record_activation_recheck_outcome(
            (("endpoint_1", "http", "http_response", at + 1),),
            allow_new_sequence=False,
            recovered=True,
            now=at + 1,
        )

    history = ca.read_activation_recheck_history(now=start + 100)
    assert len(history["sequences"]) == ca._ACTIVATION_RECHECK_HISTORY_MAX_SEQUENCES
    expired = ca.read_activation_recheck_history(
        now=start + ca._ACTIVATION_RECHECK_HISTORY_TTL_SECONDS + 1_000,
    )
    assert expired == {"schema": 1, "sequences": []}


def test_recheck_reader_rejects_extra_private_fields(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app_dir = tmp_path / "bidking"
    app_dir.mkdir(parents=True)
    (app_dir / "activation_recheck_history_999.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "updated_at": 2_000_000_000,
                "private_token": "PRIVATE_TOKEN",
                "sequences": [],
            }
        ),
        encoding="utf-8",
    )

    assert ca.read_activation_recheck_history(now=2_000_000_001) == {
        "schema": 1,
        "sequences": [],
    }


def test_recheck_reader_rejects_attempt_after_claimed_recovery(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app_dir = tmp_path / "bidking"
    app_dir.mkdir(parents=True)
    now = 2_000_000_100
    (app_dir / "activation_recheck_history_999.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "updated_at": now,
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
        ),
        encoding="utf-8",
    )

    assert ca.read_activation_recheck_history(now=now) == {
        "schema": 1,
        "sequences": [],
    }


def test_recheck_reader_accepts_valid_truncated_timeline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app_dir = tmp_path / "bidking"
    app_dir.mkdir(parents=True)
    now = 2_000_000_100
    attempts = [
        {
            "at": now - 12 + index,
            "endpoint": "endpoint_1",
            "stage": "tls",
            "failure_class": "tls_connection_reset",
        }
        for index in range(ca._ACTIVATION_RECHECK_HISTORY_MAX_ATTEMPTS)
    ]
    sequence = {
        "started_at": now - 20,
        "last_failure_at": now - 1,
        "recovered_at": now,
        "attempt_count": len(attempts) + 1,
        "attempts": attempts,
    }
    (app_dir / "activation_recheck_history_999.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "updated_at": now,
                "sequences": [sequence],
            }
        ),
        encoding="utf-8",
    )

    assert ca.read_activation_recheck_history(now=now) == {
        "schema": 1,
        "sequences": [sequence],
    }


def test_recheck_reader_rejects_two_pending_sequences_in_one_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app_dir = tmp_path / "bidking"
    app_dir.mkdir(parents=True)
    now = 2_000_000_100
    _write_history(
        app_dir / "activation_recheck_history_999.json",
        now=now,
        sequences=[
            _sequence(now - 20, recovered_at=None),
            _sequence(now - 10, recovered_at=None),
        ],
    )

    assert ca.read_activation_recheck_history(now=now) == {
        "schema": 1,
        "sequences": [],
    }


def test_recheck_reader_rejects_next_sequence_before_previous_recovery(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app_dir = tmp_path / "bidking"
    app_dir.mkdir(parents=True)
    now = 2_000_000_100
    _write_history(
        app_dir / "activation_recheck_history_999.json",
        now=now,
        sequences=[
            _sequence(now - 20, recovered_at=now - 10),
            _sequence(now - 15, recovered_at=None),
        ],
    )

    assert ca.read_activation_recheck_history(now=now) == {
        "schema": 1,
        "sequences": [],
    }


def test_recheck_reader_keeps_causal_file_and_pending_from_another_process(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app_dir = tmp_path / "bidking"
    app_dir.mkdir(parents=True)
    now = 2_000_000_100
    _write_history(
        app_dir / "activation_recheck_history_101.json",
        now=now,
        sequences=[
            _sequence(now - 30, recovered_at=now - 20),
            _sequence(now - 10, recovered_at=None),
        ],
    )
    _write_history(
        app_dir / "activation_recheck_history_202.json",
        now=now,
        sequences=[_sequence(now - 10, recovered_at=None)],
    )

    history = ca.read_activation_recheck_history(now=now)

    assert len(history["sequences"]) == 3
    assert sum(
        sequence["recovered_at"] is None
        for sequence in history["sequences"]
    ) == 2
