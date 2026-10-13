from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for _sub in ("scripts", "scripts/calibration", "src", "hero_ref/src"):
    _path = str(ROOT / _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from ingest_desktop_samples import (  # noqa: E402
    _gold_records,
    _manifest_source_path,
    _packet_capture_date,
    _repo_output_path,
    _root_label,
    _rows_semantic_sha256,
    _unique_quarantine_name,
)
from sample_manifest_inputs import load_sample_paths  # noqa: E402


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_packet_capture_date_normalizes_iso_and_slash_formats() -> None:
    rows = [
        {"CaptureTime": "2026/07/03 04:05:07.999"},
        {"CaptureTime": "2026-07-03T04:05:06.123"},
    ]

    assert _packet_capture_date(rows) == "2026-07-03 04:05:06"


def test_ingest_output_paths_resolve_under_repo() -> None:
    assert _repo_output_path(Path("data/sample_manifests/example.json")) == (
        ROOT / "data/sample_manifests/example.json"
    ).resolve()


def test_quarantine_semantic_hash_ignores_json_formatting() -> None:
    rows = [{"b": 2, "a": "值"}, {"nested": {"z": 1, "x": 0}}]
    reparsed = json.loads(json.dumps(rows, ensure_ascii=False, indent=2))

    assert _rows_semantic_sha256(rows) == _rows_semantic_sha256(reparsed)


def test_quarantine_name_only_adds_semantic_suffix_on_collision() -> None:
    used = {"fatbeans_INVALID_aisha_2509.json"}

    assert (
        _unique_quarantine_name("fatbeans_INVALID_aisha_2509.json", "abcdef012345", used)
        == "fatbeans_INVALID_aisha_2509_abcdef01.json"
    )
    assert (
        _unique_quarantine_name("fatbeans_INVALID_aisha_2510.json", "abcdef012345", used)
        == "fatbeans_INVALID_aisha_2510.json"
    )


def test_focused_gold_manifest_filters_hero_and_pairing(tmp_path: Path) -> None:
    manifest = _write(
        tmp_path / "gold.json",
        {
            "samples": [
                {"path": "data/a.json", "hero": "aisha", "paired": True},
                {"path": "data/b.json", "hero": "aisha", "paired": False},
                {"path": "data/c.json", "hero": "ahmed", "paired": True},
            ]
        },
    )
    assert [path.name for path in load_sample_paths(manifest, hero="aisha", paired_only=True)] == [
        "a.json"
    ]


def test_desktop_manifest_keeps_main_and_excludes_activity(tmp_path: Path) -> None:
    manifest = _write(
        tmp_path / "desktop.json",
        {
            "decisions": [
                {
                    "decision": "accept_main",
                    "destination": "data/main.json",
                    "selected": {"hero": "aisha"},
                },
                {
                    "decision": "accept_activity",
                    "destination": "data/activity.json",
                    "selected": {"hero": "aisha"},
                },
            ]
        },
    )
    assert [path.name for path in load_sample_paths(manifest, hero="aisha")] == ["main.json"]


def test_era_manifest_without_hero_metadata_still_loads_new_rows(tmp_path: Path) -> None:
    manifest = _write(
        tmp_path / "era.json",
        {
            "rows": [
                {"path": "data/new.json", "era": "NEW"},
                {"path": "data/old.json", "era": "OLD"},
            ]
        },
    )
    assert [path.name for path in load_sample_paths(manifest, hero="ahmed")] == ["new.json"]


def test_gold_records_do_not_treat_maria_low_tier_skill_as_q5_value() -> None:
    base = {
        "session_id": "1",
        "decision": "accept_main",
        "destination": "data/sample.json",
        "selected": {
            "hero": "maria",
            "map_id": 2501,
            "capture_date": "2026-07-14",
            "truth": {"counts": {"5": 1}, "cells": {"5": 2}, "values": {"5": 10}},
        },
    }
    maria = {**base, "selected": {**base["selected"], "claim_sources": ["skill:100108:value_sum"]}}
    aisha = {
        **base,
        "session_id": "2",
        "selected": {
            **base["selected"],
            "hero": "aisha",
            "claim_sources": ["action:100125:value_sum", "action:100113:avg_cells"],
        },
    }
    records = _gold_records([maria, aisha])
    assert [row["session_id"] for row in records] == ["2"]
    assert records[0]["paired"] is True
    assert records[0]["truth_q5_value"] == 10


def test_manifest_source_path_strips_workstation_prefix() -> None:
    assert _manifest_source_path(
        Path("C:/any-user/Desktop/new_recordings/data (5)/sample.json")
    ) == "new_recordings/data (5)/sample.json"
    assert _manifest_source_path(
        Path("C:/any-user/Desktop/bidking-live-v0.2.7-encrypted/data/sample.json")
    ) == "bidking-live-v0.2.7-encrypted/data/sample.json"
    assert _manifest_source_path(
        Path(
            "C:/any-user/Desktop/bidking-live-v0.2.9-hotfix2-encrypted/"
            "data/logs/live/sample.json"
        )
    ) == "bidking-live-v0.2.9-hotfix2-encrypted/data/logs/live/sample.json"
    assert _manifest_source_path(
        Path("C:/any-user/Desktop/archive/build/data/logs/live/raw/sample.json")
    ) == "archive/build/data/logs/live/raw/sample.json"


def test_ingest_root_label_preserves_encrypted_package_version() -> None:
    assert _root_label(
        Path("C:/any-user/Desktop/bidking-live-v0.2.9-hotfix2-encrypted/data")
    ) == "bidking-live-v0.2.9-hotfix2-encrypted/data"
