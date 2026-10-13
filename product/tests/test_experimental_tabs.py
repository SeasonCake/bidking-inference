from __future__ import annotations

import sys
from pathlib import Path

from bidking_lab.inference.observation import QualityBucketObs, SessionObs


APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import experimental_tabs  # noqa: E402


def test_joint_context_cache_reuses_same_readings(monkeypatch) -> None:
    calls: list[tuple[int, int]] = []

    def fake_joint_context(session, *, per_bucket_top: int, k: int):
        calls.append((per_bucket_top, k))
        return [f"hyp-{len(calls)}"], {4: f"local-{len(calls)}"}

    monkeypatch.setattr(experimental_tabs, "_joint_context", fake_joint_context)
    cache: dict = {}
    session = SessionObs(
        map_id=101,
        hero="ethan",
        warehouse_total_cells=90,
        buckets={4: QualityBucketObs(quality=4, total_cells=24, count=8)},
    )

    first = experimental_tabs._joint_context_cached(
        session,
        per_bucket_top=6,
        k=3,
        cache=cache,
    )
    second = experimental_tabs._joint_context_cached(
        session,
        per_bucket_top=6,
        k=3,
        cache=cache,
    )

    assert first == second
    assert calls == [(6, 3)]


def test_joint_context_cache_refreshes_when_readings_change(monkeypatch) -> None:
    calls: list[tuple[int | None, int]] = []

    def fake_joint_context(session, *, per_bucket_top: int, k: int):
        calls.append((session.buckets[4].total_cells, k))
        return [f"hyp-{len(calls)}"], {}

    monkeypatch.setattr(experimental_tabs, "_joint_context", fake_joint_context)
    cache: dict = {}

    for cells in (24, 25):
        session = SessionObs(
            map_id=101,
            hero="ethan",
            warehouse_total_cells=90,
            buckets={4: QualityBucketObs(quality=4, total_cells=cells, count=8)},
        )
        experimental_tabs._joint_context_cached(
            session,
            per_bucket_top=6,
            k=3,
            cache=cache,
        )

    assert calls == [(24, 3), (25, 3)]
