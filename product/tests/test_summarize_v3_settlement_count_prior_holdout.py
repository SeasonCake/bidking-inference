import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_settlement_count_prior_holdout.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_settlement_count_prior_holdout",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _session_for_fold(module, fold: int, *, prefix: str) -> str:
    for idx in range(1000):
        session_id = f"{prefix}_{idx}"
        if module._stable_fold(session_id, 2) == fold:
            return session_id
    raise AssertionError(f"no session for fold {fold}")


def _row(
    map_id: int,
    *,
    session_id: str,
    non_temp_inventory_count: int,
    table_status: str = "ok",
    prior_max: int | None = 12,
    round_cap: int | None = 18,
) -> dict[str, object]:
    return {
        "status": "ok",
        "session_id": session_id,
        "file": f"{session_id}.json",
        "map_id": map_id,
        "map_prefix3": map_id // 10,
        "table_status": table_status,
        "inventory_count": non_temp_inventory_count,
        "non_temp_inventory_count": non_temp_inventory_count,
        "known_temp_zodiac_count": 0,
        "bidmap_items_per_session_max": prior_max,
        "bidmap_raw_round_cap_max": round_cap,
    }


def test_settlement_count_prior_holdout_blocks_under_covered_p95() -> None:
    module = _load_module()
    fold0 = [
        _session_for_fold(module, 0, prefix=f"scp_holdout_0_{idx}")
        for idx in range(2)
    ]
    fold1 = [
        _session_for_fold(module, 1, prefix=f"scp_holdout_1_{idx}")
        for idx in range(2)
    ]
    rows = [
        _row(2601, session_id=fold0[0], non_temp_inventory_count=10),
        _row(2601, session_id=fold0[1], non_temp_inventory_count=12),
        _row(2601, session_id=fold1[0], non_temp_inventory_count=14),
        _row(2601, session_id=fold1[1], non_temp_inventory_count=20),
    ]

    result = module.summarize_holdout(
        rows=rows,
        group_by="map_id",
        folds=2,
        min_train_sessions=1,
    )

    assert result["sessions"] == 4
    assert result["candidate_rows"] == 2
    assert result["status_counts"] == {"blocked_holdout_under_coverage": 1}
    row = result["rows"][0]
    assert row["group"] == "2601"
    assert row["prior_max_coverage"] == 0.5
    assert row["round_cap_coverage"] == 0.75
    assert row["holdout_p95_coverage"] == 0.5
    assert row["prior_max_under_rows"] == 2
    assert row["round_cap_under_rows"] == 1
    assert row["holdout_p95_under_rows"] == 2
    assert row["holdout_max_under_rows"] == 2


def test_settlement_count_prior_holdout_keeps_missing_table_shadow_only() -> None:
    module = _load_module()
    fold0 = _session_for_fold(module, 0, prefix="scp_missing_0")
    fold1 = _session_for_fold(module, 1, prefix="scp_missing_1")
    rows = [
        _row(
            2521,
            session_id=fold0,
            non_temp_inventory_count=50,
            table_status="missing_bidmap",
            prior_max=None,
            round_cap=None,
        ),
        _row(
            2521,
            session_id=fold1,
            non_temp_inventory_count=54,
            table_status="missing_bidmap",
            prior_max=None,
            round_cap=None,
        ),
    ]

    result = module.summarize_holdout(
        rows=rows,
        group_by="map_id",
        folds=2,
        min_train_sessions=1,
    )

    assert result["candidate_rows"] == 0
    assert result["missing_table_rows"] == 2
    assert result["status_counts"] == {"missing_table_shadow_only": 1}
    assert result["rows"][0]["table_statuses"] == {"missing_bidmap": 2}
