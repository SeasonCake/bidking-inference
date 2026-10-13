import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "build_v3_settlement_count_prior_shadow.py"
    )
    spec = importlib.util.spec_from_file_location(
        "build_v3_settlement_count_prior_shadow",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_entry_from_candidate_row_is_shadow_only() -> None:
    module = _load_module()

    entry = module._entry_from_row(
        {
            "group_by": "map_id",
            "group": "2601",
            "candidate_status": "observed_exceeds_table_caps_shadow_only",
            "files": 22,
            "inventory_count": {"p95": 60, "max": 65},
            "non_temp_inventory_count": {"p95": 56, "max": 64},
            "known_temp_zodiac_count": {"max": 7},
            "above_drop_ref_after_temp_zodiac_rows": 11,
            "above_round_cap_after_temp_zodiac_rows": 1,
        },
        cohort="default_archive",
    )

    assert entry["scope"] == "map_id"
    assert entry["group"] == "2601"
    assert entry["status"] == "observed_exceeds_table_caps_shadow_only"
    assert entry["gate_reason"] == "observed_settlement_count_exceeds_current_table_caps"
    assert entry["archive_sessions"] == 22
    assert entry["non_temp_inventory_count_max"] == 64
    assert entry["source"] == "archive_settlement_count_prior_shadow:default_archive"
