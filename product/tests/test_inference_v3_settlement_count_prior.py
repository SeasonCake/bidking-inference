from pathlib import Path
from types import SimpleNamespace

from bidking_lab.inference.v3.settlement_count_prior import (
    SettlementCountPriorEntry,
    assess_settlement_count_prior,
    entry_from_mapping,
    load_settlement_count_prior_entries,
    settlement_count_prior_entry_for,
)


def test_settlement_count_prior_report_is_shadow_only() -> None:
    entry = SettlementCountPriorEntry(
        scope="map_id",
        group="2601",
        status="observed_exceeds_table_caps_shadow_only",
        archive_sessions=22,
        non_temp_inventory_count_p95=56,
        non_temp_inventory_count_max=64,
        above_drop_ref_after_temp_zodiac_rows=11,
    )
    report = assess_settlement_count_prior(
        entry=entry,
        map_id=2601,
        summary=SimpleNamespace(
            session_total_count_exact=None,
            known_count_floor=45,
        ),
        prior_fields={"v3_prior_items_per_session_max": 44},
    )

    flat = report.to_flat_dict()

    assert flat["v3_scp_available"] is True
    assert flat["v3_scp_ready"] is True
    assert flat["v3_scp_candidate"] is True
    assert flat["v3_scp_active"] is False
    assert flat["v3_scp_affects_bid"] is False
    assert flat["v3_scp_status"] == "observed_exceeds_table_caps_shadow_only"
    assert flat["v3_scp_target_count_source"] == "floor"
    assert flat["v3_scp_prior_max_to_observed_p95_delta"] == -12
    assert flat["v3_scp_prior_max_to_observed_max_delta"] == -20
    assert "observed_exceeds_table_caps" in flat["v3_scp_flags"]


def test_settlement_count_prior_loads_and_matches_exact_before_prefix(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scp.json"
    path.write_text(
        """
        {
          "entries": [
            {
              "scope": "map_prefix3",
              "group": "260",
              "status": "observed_exceeds_table_caps_shadow_only",
              "archive_sessions": 22,
              "non_temp_inventory_count_p95": 56
            },
            {
              "scope": "map_id",
              "group": "2601",
              "status": "table_caps_cover_observed_shadow_only",
              "archive_sessions": 3,
              "non_temp_inventory_count_p95": 42
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    entries = load_settlement_count_prior_entries(path)

    assert settlement_count_prior_entry_for(entries, map_id=2601).scope == "map_id"
    assert settlement_count_prior_entry_for(entries, map_id=2602).scope == "map_prefix3"


def test_settlement_count_prior_entry_parses_nested_candidate_row() -> None:
    entry = entry_from_mapping(
        {
            "group_by": "map_id",
            "group": "2521",
            "candidate_status": "missing_table_shadow_only",
            "files": 5,
            "inventory_count": {"p95": 67, "max": 67},
            "non_temp_inventory_count": {"p95": 67, "max": 67},
            "missing_table_rows": 5,
        }
    )

    assert entry.scope == "map_id"
    assert entry.group == "2521"
    assert entry.status == "missing_table_shadow_only"
    assert entry.archive_sessions == 5
    assert entry.missing_table is True
    assert entry.non_temp_inventory_count_max == 67
