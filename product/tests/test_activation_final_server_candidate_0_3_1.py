from __future__ import annotations

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


def test_five_consecutive_800_same_paid_identity_rounds_are_bounded_and_write_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = int(time.time())
    uid = "same-paid"
    device = "SAME-PAID"
    service = runtime._service(tmp_path)
    server._atomic_write_json(service.bindings_path, {uid: device})
    server._atomic_write_json(service.paid_starts_path, {uid: now - 60})
    request = runtime._request_bytes(
        {
            "token": runtime._paid_token(uid, now=now),
            "device": device,
        }
    )
    writes = runtime._install_write_spy(monkeypatch)

    elapsed_maxima: list[float] = []
    for _round in range(5):
        completed, metrics = runtime._run_simultaneous_http_burst(
            service,
            [request] * runtime.BURST_SIZE,
            monkeypatch,
        )
        assert len(completed) == runtime.BURST_SIZE
        elapsed_maxima.append(metrics["elapsed_max_ms"])

    assert writes == []
    assert len(elapsed_maxima) == 5
    assert all(value < 4_000 for value in elapsed_maxima)
