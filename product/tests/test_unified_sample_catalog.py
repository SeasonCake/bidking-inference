"""Focused contracts for the local/cloud capability-aware sample catalog."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for _part in ("scripts", "src", "hero_ref/src", "hero_ref/tools"):
    _path = str(ROOT / _part)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from bidking_lab.live.sample_upload import _hash_session as _uploader_hash_session  # noqa: E402
from build_unified_sample_catalog import (  # noqa: E402
    _cloud_replay_grade,
    _hash_session,
    _summary_payload,
    _truth_eligible,
    _version_generation,
)
import build_unified_sample_catalog as catalog_builder  # noqa: E402


def test_local_session_hash_reconstructs_the_exact_uploader_wire_token() -> None:
    numeric_session = "9000000000000018"
    wire_token = f"2501:{numeric_session}"

    expected = _uploader_hash_session(wire_token)
    assert expected == "0006e63bf89df707"  # known local/cloud overlap in the 0715 catalog
    assert _hash_session(numeric_session, 2501) == expected
    assert _hash_session(wire_token, 9999) == expected
    assert _hash_session(None, 2501) is None


def test_cloud_truth_requires_every_core_field_and_a_complete_settlement() -> None:
    complete = {
        "final_quality_counts": "q1=1",
        "final_quality_cells": "q1=1",
        "final_quality_values": "q1=100",
        "inventory_count": 1,
        "inventory_cells": 1,
        "known_value_sum": 100,
        "settlement_incomplete": False,
    }

    assert _truth_eligible(complete)
    assert not _truth_eligible({**complete, "known_value_sum": None})
    assert not _truth_eligible({**complete, "settlement_incomplete": True})


def test_cloud_without_ref_inputs_is_truth_only_not_replay() -> None:
    assert _cloud_replay_grade({}) == ("none", "no_ref_inputs")


def _directional_record() -> dict:
    return {
        "hero": "gabriela",
        "map_id": 2405,
        "round": 2,
        "final_quality_counts": "q6=4",
        "ref_inputs": {
            "hero": "gabriela",
            "counts": {},
            "avg_cells": {},
            "avg_values": {},
            "quality_cells": {},
            "quality_values": {},
            "count_sums": {},
            "total_count": 29,
            "total_cells": None,
            "minimap_items": [{"quality": 6, "cells": 10}],
        },
    }


def test_partial_bidding_reveal_does_not_need_to_equal_final_red_truth() -> None:
    record = _directional_record()

    assert _cloud_replay_grade(record) == ("red_directional_only", None)


def test_missing_minimap_quality_stays_approximate_not_tuning_grade() -> None:
    record = _directional_record()
    record["ref_inputs"]["minimap_items"] = [{"quality": None, "cells": 10}]

    assert _cloud_replay_grade(record) == (
        "approximate_ref_only",
        "minimap_quality_incomplete",
    )


def test_version_generation_preserves_the_user_confirmed_timeline() -> None:
    assert _version_generation("0.2.4-hotfix2") == "early_partial_ref_generation"
    assert _version_generation("0.2.5") == "0.2.5_partial_replay_generation"
    assert _version_generation("0.2.6-hotfix1") == "0.2.6_plus_capture_stable_generation"
    assert _version_generation("0.2.7") == "0.2.6_plus_capture_stable_generation"


def test_compact_summary_binds_full_catalog_without_copying_records(tmp_path: Path) -> None:
    catalog = tmp_path / "unified.json"
    catalog.write_text('{"records": []}\n', encoding="utf-8")
    payload = {
        "schema_version": 1,
        "summary": {"union_sessions": 3},
        "records": [{"session_hash": "one"}, {"session_hash": "two"}],
    }

    summary = _summary_payload(payload, catalog)

    assert "records" not in summary
    assert summary["summary"] == {"union_sessions": 3}
    assert summary["full_catalog"]["size_bytes"] == catalog.stat().st_size
    assert len(summary["full_catalog"]["sha256"]) == 64
    assert summary["full_catalog"]["tracked"] is False


def test_local_truth_only_is_prior_eligible_but_never_replay_or_tuning(
    monkeypatch,
) -> None:
    replay_hash = "replay-session"
    truth_hash = "truth-only-session"
    replay_row = {
        "path": "data/samples/fatbeans/fatbeans_valid_aisha_2501_fixture.json",
        "pool": "main",
        "active": True,
        "replay_eligible": True,
        "truth_eligible": True,
        "hero": "aisha",
        "map_id": 2501,
        "capture_date": "2026-07-17 01:00:00",
        "truth": {"collectible_count": 40, "total_cells": 130, "total_value": 900000},
        "sha256": "a" * 64,
    }
    truth_only_row = {
        "path": (
            "data/samples/fatbeans_truth_only_settlement/normal/"
            "fatbeans_truth_ahmed_5601_fixture.json"
        ),
        "pool": "truth_only_settlement",
        "active": False,
        "replay_eligible": False,
        "truth_eligible": True,
        "hero": "ahmed",
        "map_id": 5601,
        "capture_date": "2026-07-17 01:01:00",
        "truth": {"collectible_count": 53, "total_cells": 136, "total_value": 970524},
        "sha256": "b" * 64,
    }
    monkeypatch.setattr(catalog_builder, "_load_cloud", lambda _paths: ({}, {"sources": []}))
    monkeypatch.setattr(
        catalog_builder,
        "_load_local",
        lambda: (
            {replay_hash: [replay_row], truth_hash: [truth_only_row]},
            {
                "strict_valid_files": 1,
                "unique_sessions": 1,
                "truth_only_files": 1,
                "truth_only_unique_sessions": 1,
                "truth_unique_sessions": 2,
            },
        ),
    )

    payload = catalog_builder.build_catalog([])
    records = {row["session_hash"]: row for row in payload["records"]}

    assert records[replay_hash]["local_raw_replay"] is True
    assert records[replay_hash]["tuning_candidate"] is True
    assert records[replay_hash]["source_kinds"] == ["local_fatbeans_raw"]
    truth_only = records[truth_hash]
    assert truth_only["truth_eligible"] is True
    assert truth_only["local_settlement_truth"] is True
    assert truth_only["local_raw_replay"] is False
    assert truth_only["tuning_candidate"] is False
    assert truth_only["source_kinds"] == ["local_settlement_truth"]
    assert truth_only["local"]["replay_paths"] == []
    assert truth_only["local"]["truth_only_paths"] == [truth_only_row["path"]]
    assert payload["summary"]["local_raw_unique_sessions"] == 1
    assert payload["summary"]["local_truth_unique_sessions"] == 2
    assert payload["summary"]["local_truth_only_unique_sessions"] == 1
