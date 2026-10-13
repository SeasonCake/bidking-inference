from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ingest_desktop_samples as ingest  # noqa: E402


@pytest.mark.parametrize("capture_prefix", ("npcap", "windivert", "wss"))
def test_desktop_ingest_extracts_capture_date_for_supported_reset_sources(
    tmp_path: Path,
    capture_prefix: str,
) -> None:
    source = (
        tmp_path
        / f"{capture_prefix}_live_2026-07-24_120102_2501_1234567890123456_reset.json"
    )
    source.write_text("[]", encoding="utf-8")

    candidate = ingest.Candidate("fixture", source, "1234567890123456")

    assert ingest._source_date(candidate) == "2026-07-24 12:01:02"


def test_desktop_ingest_collects_wss_reset_capture(tmp_path: Path) -> None:
    source = (
        tmp_path
        / "data"
        / "logs"
        / "live"
        / "raw"
        / "archive"
        / "reset"
        / "wss_live_2026-07-24_120102_2501_1234567890123456_reset.json"
    )
    source.parent.mkdir(parents=True)
    source.write_text("[]", encoding="utf-8")

    by_session, diagnostics = ingest.collect_candidates([tmp_path])

    assert set(by_session) == {"1234567890123456"}
    assert by_session["1234567890123456"][0].path == source
    assert diagnostics == {}


def test_desktop_ingest_deduplicates_recordings_inbox_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (
        tmp_path
        / "data"
        / "samples"
        / "recordings_inbox_20260725_fixture"
        / "captures"
        / "fatbeans_valid_ahmed_2501_3rounds_2501_1234567890123456_3456.json"
    )
    source.parent.mkdir(parents=True)
    source.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(ingest, "ROOT", tmp_path)
    for name in (
        "MAIN_POOL",
        "ACTIVITY_POOL",
        "RED_CRATE_ACTIVITY_POOL",
        "TRUTH_ONLY_MAIN_POOL",
        "TRUTH_ONLY_ACTIVITY_POOL",
        "TRUTH_ONLY_RED_CRATE_ACTIVITY_POOL",
        "PLAYER_CUSTOM_WAREHOUSE_POOL",
        "POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE_POOL",
    ):
        monkeypatch.setattr(ingest, name, tmp_path / f"missing-{name.lower()}")

    existing = ingest._existing_sessions()

    assert existing == {"1234567890123456": source}


def test_desktop_ingest_reuses_pending_quarantine_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "1234567890123456"
    source = (
        tmp_path
        / "data"
        / "samples"
        / "recordings_inbox_20260725_fixture"
        / "quarantine_positive_controls"
        / f"fatbeans_INVALID_aisha_2501_3rounds_2501_{session_id}_3456.json"
    )
    source.parent.mkdir(parents=True)
    source.write_text('[{"fixture": true}]', encoding="utf-8")
    main_pool = tmp_path / "data" / "samples" / "fatbeans"
    main_pool.mkdir(parents=True)
    target_dir = main_pool / "ZZZ_INVALID_DO_NOT_USE_fixture"
    monkeypatch.setattr(ingest, "ROOT", tmp_path)
    monkeypatch.setattr(ingest, "MAIN_POOL", main_pool)

    by_semantics, used_names = ingest._existing_quarantine_controls(target_dir)

    rows = [{"fixture": True}]
    assert by_semantics[
        (session_id, ingest._rows_semantic_sha256(rows))
    ] == source
    assert used_names == {source.name}


def _record(
    source: Path,
    *,
    qualification: str,
    eligible: bool,
) -> dict:
    session_id = "1234567890123456"
    return {
        "source": source.name,
        "session_id": session_id,
        "session_token": f"2501:{session_id}",
        "hero": "aisha",
        "map_id": 2501,
        "rounds": 3,
        "bytes": source.stat().st_size,
        "state_count": 4,
        "ready_windows": 3,
        "claims_present": 2,
        "verdict": "valid",
        "reason": None,
        "reasons": [],
        "replay_verdict": "valid",
        "replay_reasons": [],
        "truth_verdict": "valid",
        "truth_reasons": [],
        "truth": {
            "collectible_count": 41,
            "total_cells": 134,
            "total_value": 900_000,
        },
        "truth_signature": {
            "collectible_count": 41,
            "total_cells": 134,
        },
        "warehouse_source_qualification": qualification,
        "official_distribution_eligible": eligible,
    }


@pytest.mark.parametrize(
    ("qualification", "eligible", "pool_name", "decision_name"),
    (
        (
            "player_custom_warehouse",
            False,
            "player_custom",
            "accept_player_custom_warehouse",
        ),
        (
            "postupdate_unknown_warehouse_source",
            False,
            "source_unknown",
            "accept_postupdate_unknown_warehouse_source",
        ),
    ),
)
def test_desktop_ingest_routes_postupdate_nonofficial_sources_out_of_main_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qualification: str,
    eligible: bool,
    pool_name: str,
    decision_name: str,
) -> None:
    source = tmp_path / "npcap_live_2026-07-23_120000_2501_1234567890123456.json"
    source.write_text("[]", encoding="utf-8")
    target = tmp_path / pool_name
    record = _record(
        source,
        qualification=qualification,
        eligible=eligible,
    )
    monkeypatch.setattr(ingest, "ROOT", tmp_path)
    monkeypatch.setattr(ingest, "PLAYER_CUSTOM_WAREHOUSE_POOL", target)
    monkeypatch.setattr(
        ingest,
        "POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE_POOL",
        target,
    )
    monkeypatch.setattr(
        ingest,
        "_candidate_record",
        lambda _candidate, *, tables: (record, []),
    )

    decision, chosen, rows = ingest._choose_session(
        record["session_id"],
        [ingest.Candidate("fixture", source, record["session_id"])],
        tables=None,
    )

    assert decision["decision"] == decision_name
    assert decision["destination"].startswith(f"{pool_name}/fatbeans_valid_aisha_2501_")
    assert chosen is not None
    assert rows == []


def test_desktop_ingest_keeps_explicit_historical_official_source_on_old_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "npcap_live_2026-07-19_120000_2501_1234567890123456.json"
    source.write_text("[]", encoding="utf-8")
    target = tmp_path / "main"
    record = _record(
        source,
        qualification="official_random_warehouse",
        eligible=True,
    )
    monkeypatch.setattr(ingest, "ROOT", tmp_path)
    monkeypatch.setattr(ingest, "MAIN_POOL", target)
    monkeypatch.setattr(
        ingest,
        "_candidate_record",
        lambda _candidate, *, tables: (record, []),
    )

    decision, chosen, rows = ingest._choose_session(
        record["session_id"],
        [ingest.Candidate("fixture", source, record["session_id"])],
        tables=None,
    )

    assert decision["decision"] == "accept_main"
    assert decision["destination"].startswith("main/fatbeans_valid_aisha_2501_")
    assert chosen is not None
    assert rows == []


def test_gold_manifest_excludes_nonofficial_warehouse_sources(tmp_path: Path) -> None:
    source = tmp_path / "fixture.json"
    source.write_text("[]", encoding="utf-8")
    official = _record(
        source,
        qualification="official_random_warehouse",
        eligible=True,
    )
    unresolved = _record(
        source,
        qualification="postupdate_unknown_warehouse_source",
        eligible=False,
    )
    for selected in (official, unresolved):
        selected["claim_sources"] = ["action:100125:value_sum"]
        selected["truth"]["counts"] = {"5": 3}
        selected["truth"]["cells"] = {"5": 8}
        selected["truth"]["values"] = {"5": 90_000}

    records = ingest._gold_records(
        [
            {
                "session_id": "1234567890123456",
                "decision": "accept_main",
                "destination": "main/official.json",
                "selected": official,
            },
            {
                "session_id": "1234567890123457",
                "decision": "accept_postupdate_unknown_warehouse_source",
                "destination": "source_unknown/unresolved.json",
                "selected": unresolved,
            },
        ]
    )

    assert [row["path"] for row in records] == ["main/official.json"]
