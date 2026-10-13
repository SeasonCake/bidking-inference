from pathlib import Path
from types import SimpleNamespace

from bidking_lab.inference.v3.capacity_source_expansion import (
    CapacitySourceExpansionEntry,
    assess_capacity_source_expansion,
    capacity_source_expansion_entry_for,
    entry_from_mapping,
    load_capacity_source_expansion_entries,
)


def test_capacity_source_expansion_report_is_shadow_only() -> None:
    entry = CapacitySourceExpansionEntry(
        scope="map_id",
        group="2501",
        status="watch_capacity_source_expansion_shadow_only",
        archive_sessions=21,
        source_context_classes="payload_verified_partial_action_only:6",
        unique_round_overflow_rows=7,
        server_side_expansion_rows=1,
        session_capacity_source_semantics_rows=6,
        public_total_match_rows=1,
        unique_non_temp_p95=57,
        unique_non_temp_max=57,
        unique_round_excess_p95=7,
        unique_round_excess_max=7,
    )

    report = assess_capacity_source_expansion(
        entry=entry,
        map_id=2501,
        map_family="shipwreck",
        summary=SimpleNamespace(
            session_total_count_exact=None,
            known_count_floor=52,
        ),
        prior_fields={"v3_prior_items_per_session_max": 44},
    )
    flat = report.to_flat_dict()

    assert flat["v3_cse_available"] is True
    assert flat["v3_cse_ready"] is True
    assert flat["v3_cse_candidate"] is True
    assert flat["v3_cse_pressure_candidate"] is True
    assert flat["v3_cse_active"] is False
    assert flat["v3_cse_affects_bid"] is False
    assert flat["v3_cse_status"] == "watch_capacity_source_expansion_shadow_only"
    assert (
        flat["v3_cse_source_context_classes"]
        == "payload_verified_partial_action_only:6"
    )
    assert flat["v3_cse_target_count_source"] == "floor"
    assert flat["v3_cse_target_prior_max_delta"] == 8
    assert flat["v3_cse_target_to_unique_non_temp_p95_delta"] == -5
    assert flat["v3_cse_prior_max_to_unique_non_temp_p95_delta"] == -13
    assert "source_expansion_candidate" in flat["v3_cse_flags"]
    assert "target_count_above_prior_max" in flat["v3_cse_flags"]
    assert "external_source_confirmation" in flat["v3_cse_flags"]


def test_capacity_source_expansion_loads_exact_before_family(tmp_path: Path) -> None:
    path = tmp_path / "cse.json"
    path.write_text(
        """
        {
          "entries": [
            {
              "scope": "map_family",
              "group": "shipwreck",
              "status": "watch_capacity_source_expansion_shadow_only",
              "archive_sessions": 19,
              "unique_non_temp_p95": 57
            },
            {
              "scope": "map_id",
              "group": "2501",
              "status": "within_capacity_source_semantics_shadow_only",
              "archive_sessions": 3,
              "unique_non_temp_p95": 42
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    entries = load_capacity_source_expansion_entries(path)

    assert (
        capacity_source_expansion_entry_for(
            entries,
            map_id=2501,
            map_family="shipwreck",
        ).scope
        == "map_id"
    )
    assert (
        capacity_source_expansion_entry_for(
            entries,
            map_id=2502,
            map_family="shipwreck",
        ).scope
        == "map_family"
    )


def test_capacity_source_expansion_entry_parses_source_semantics_row() -> None:
    entry = entry_from_mapping(
        {
            "group_by": "map_id",
            "group": "2501",
            "files": 21,
            "source_evidence_classes": {
                "settlement_payload_verified_only": 18,
                "direct_action_matches_inventory": 2,
                "public_total_matches_inventory": 1,
            },
            "source_context_classes": {
                "payload_verified_partial_action_only": 18,
                "direct_action_full_confirmed": 2,
                "public_total_confirmed": 1,
            },
            "mechanism_classes": {
                "session_capacity_source_semantics": 18,
                "server_side_settlement_expansion": 3,
            },
            "unique_above_round_after_temp_zodiac_rows": 21,
            "event_public_total_match_rows": 1,
            "event_full_action_rows": 2,
            "payload_inventory_mismatch_rows": 0,
            "non_zodiac_missing_from_drop_universe_count": {"max": 0},
            "unique_non_temp_item_id_count": {"p95": 57, "max": 57},
            "unique_round_cap_excess_after_temp_zodiac_count": {"p95": 7, "max": 7},
            "bidmap_items_per_session_max": {"max": 44},
            "bidmap_raw_round_cap_max": {"max": 50},
        }
    )

    assert entry.status == "watch_capacity_source_expansion_shadow_only"
    assert entry.candidate is True
    assert entry.archive_sessions == 21
    assert entry.server_side_expansion_rows == 3
    assert entry.session_capacity_source_semantics_rows == 18
    assert entry.payload_verified_only_rows == 18
    assert (
        entry.source_context_classes
        == "direct_action_full_confirmed:2,payload_verified_partial_action_only:18,public_total_confirmed:1"
    )
    assert entry.unique_non_temp_max == 57

    reloaded = entry_from_mapping(entry.to_dict())

    assert reloaded.mechanism_classes == entry.mechanism_classes
    assert reloaded.source_evidence_classes == entry.source_evidence_classes
    assert reloaded.source_context_classes == entry.source_context_classes
    assert reloaded.unique_round_overflow_rows == entry.unique_round_overflow_rows
    assert reloaded.public_total_match_rows == entry.public_total_match_rows
    assert reloaded.full_action_rows == entry.full_action_rows
    assert reloaded.unique_non_temp_p95 == entry.unique_non_temp_p95
    assert reloaded.unique_non_temp_max == entry.unique_non_temp_max
    assert reloaded.unique_round_excess_p95 == entry.unique_round_excess_p95
    assert reloaded.bidmap_items_per_session_max == entry.bidmap_items_per_session_max
