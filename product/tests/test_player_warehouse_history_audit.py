from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from audit_player_warehouse_sample_qualification import audit_registry  # noqa: E402
from bidking_lab.live.warehouse_source import WAREHOUSE_TABLE_VERIFIED


def _row(
    file: str,
    *,
    map_id: int,
    capture_date: str,
) -> dict:
    return {
        "file": file,
        "map_id": map_id,
        "capture_date": capture_date,
        "active": True,
        "replay_eligible": True,
        "verdict": "valid",
    }


def test_history_audit_keeps_preactivity_same_name_rows_official() -> None:
    registry = {
        "schema_version": 5,
        "generated_at": "2026-07-22T00:00:00+00:00",
        "samples": [
            _row("old-2501.json", map_id=2501, capture_date="2026-07-19 12:00:00"),
            _row("old-2601.json", map_id=2601, capture_date="2026-07-19 13:00:00"),
        ],
    }
    result = audit_registry(
        registry,
        {
            2501: "未知残骸",
            2601: "隐秘拍卖会",
        },
    )

    assert result["historical_same_name_rows"] == 2
    assert result["postupdate_rows"] == 0
    assert result["excluded_active_valid_rows"] == 0
    assert result["official_active_valid_rows"] == 2
    assert result["conclusion"] == (
        "no_postupdate_samples_no_historical_reclassification"
    )


def test_history_audit_excludes_unresolved_postupdate_non_ranked_rows() -> None:
    registry = {
        "schema_version": 5,
        "samples": [
            _row("new-2501.json", map_id=2501, capture_date="2026-07-23 12:00:00"),
            _row("new-2409.json", map_id=2409, capture_date="2026-07-23 13:00:00"),
            _row("new-5601.json", map_id=5601, capture_date="2026-07-23 14:00:00"),
        ],
    }
    result = audit_registry(
        registry,
        {
            2501: "未知残骸",
            2409: "博物收藏家别墅",
            5601: "排位仓",
        },
        table_status=WAREHOUSE_TABLE_VERIFIED,
    )

    assert result["postupdate_rows"] == 3
    assert result["postupdate_by_qualification"] == {
        "official_random_warehouse": 1,
        "postupdate_unknown_warehouse_source": 2,
    }
    assert result["excluded_active_valid_rows"] == 2
    assert result["official_active_valid_rows"] == 1
