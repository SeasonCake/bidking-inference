from __future__ import annotations

from bidking_lab.inference.display import parse_reading
from bidking_lab.inference.observation import QualityBucketObs, SessionObs
from bidking_lab.live import compare_session_obs, flatten_session_obs, sessions_match


def test_compare_session_obs_returns_no_rows_for_equivalent_sessions() -> None:
    legacy = SessionObs(
        map_id=2405,
        hero="ethan",
        warehouse_total_cells=100,
        buckets={
            4: QualityBucketObs(
                quality=4,
                total_cells=30,
                count=10,
                avg_cells=parse_reading("3.00"),
                huge_band="1",
                huge_cells_override=10,
            ),
        },
    )
    live = SessionObs(
        map_id=2405,
        hero="ethan",
        warehouse_total_cells=100,
        buckets={
            4: QualityBucketObs(
                quality=4,
                total_cells=30,
                count=10,
                avg_cells=parse_reading("3.00"),
                huge_band="1",
                huge_cells_override=10,
            ),
        },
    )

    assert sessions_match(legacy, live)
    assert compare_session_obs(legacy, live) == ()


def test_compare_session_obs_reports_changed_fields() -> None:
    legacy = SessionObs(
        map_id=2405,
        hero="ethan",
        warehouse_total_cells=100,
        buckets={6: QualityBucketObs(quality=6, total_cells=15)},
    )
    live = SessionObs(
        map_id=2405,
        hero="ethan",
        warehouse_total_cells=100,
        buckets={6: QualityBucketObs(quality=6, total_cells=12)},
    )

    rows = compare_session_obs(legacy, live)

    assert {
        "field": "bucket.6.total_cells",
        "legacy": "15",
        "live": "12",
        "status": "different",
    } in rows
    assert not sessions_match(legacy, live)


def test_compare_session_obs_preserves_reading_tail_zero() -> None:
    legacy = SessionObs(
        map_id=2405,
        hero="ethan",
        buckets={
            4: QualityBucketObs(quality=4, avg_cells=parse_reading("2.90")),
        },
    )
    live = SessionObs(
        map_id=2405,
        hero="ethan",
        buckets={
            4: QualityBucketObs(quality=4, avg_cells=parse_reading("2.9")),
        },
    )

    assert {
        "field": "bucket.4.avg_cells",
        "legacy": "2.90",
        "live": "2.9",
        "status": "different",
    } in compare_session_obs(legacy, live)


def test_compare_session_obs_reports_missing_bucket() -> None:
    legacy = SessionObs(
        map_id=2405,
        hero="ethan",
        buckets={5: QualityBucketObs(quality=5, huge_band="1")},
    )
    live = SessionObs(map_id=2405, hero="ethan")

    rows = compare_session_obs(legacy, live)

    assert {
        "field": "bucket.5._present",
        "legacy": "True",
        "live": "—",
        "status": "legacy_only",
    } in rows


def test_flatten_session_obs_uses_stable_paths() -> None:
    session = SessionObs(
        map_id=2405,
        hero="ethan",
        warehouse_total_cells=100,
        buckets={
            6: QualityBucketObs(quality=6, value_range=(300_000, 420_000)),
        },
    )

    flat = flatten_session_obs(session)

    assert flat["session.map_id"] == 2405
    assert flat["bucket.6.value_range"] == (300_000, 420_000)
