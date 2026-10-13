import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "build_v3_capacity_source_expansion_shadow.py"
    )
    spec = importlib.util.spec_from_file_location(
        "build_v3_capacity_source_expansion_shadow",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_capacity_source_expansion_artifact_from_source_semantics(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    sample_root = tmp_path / "samples"
    sample_root.mkdir()

    def fake_summary(paths, *, group_by, **_kwargs):
        return {
            "files": 2,
            "settlement_rows": 2,
            "table_overlay_metadata": {"local_overlay_status": "test"},
            "overall": {
                "source_evidence_classes": {"settlement_payload_verified_only": 1},
                "source_context_classes": {
                    "payload_verified_partial_action_only": 1,
                },
                "mechanism_classes": {"session_capacity_source_semantics": 1},
                "unique_above_round_after_temp_zodiac_rows": 1,
            },
            "rows": [
                {
                    "group_by": group_by,
                    "group": "2501" if group_by == "map_id" else "shipwreck",
                    "files": 2,
                    "source_evidence_classes": {
                        "settlement_payload_verified_only": 1,
                    },
                    "source_context_classes": {
                        "payload_verified_partial_action_only": 1,
                    },
                    "mechanism_classes": {
                        "session_capacity_source_semantics": 1,
                    },
                    "unique_above_round_after_temp_zodiac_rows": 1,
                    "event_public_total_match_rows": 0,
                    "event_full_action_rows": 0,
                    "payload_inventory_mismatch_rows": 0,
                    "non_zodiac_missing_from_drop_universe_count": {"max": 0},
                    "unique_non_temp_item_id_count": {"p95": 57, "max": 57},
                    "unique_round_cap_excess_after_temp_zodiac_count": {
                        "p95": 7,
                        "max": 7,
                    },
                    "bidmap_items_per_session_max": {"max": 44},
                    "bidmap_raw_round_cap_max": {"max": 50},
                }
            ],
        }

    monkeypatch.setattr(
        module,
        "summarize_settlement_source_semantics_audit",
        fake_summary,
    )

    artifact = module.build_artifact(
        (("cohort", sample_root),),
        group_bys=("map_id", "map_family"),
    )

    assert artifact["affects_bid"] is False
    assert artifact["active"] is False
    assert artifact["table_overlay_metadata"] == {"local_overlay_status": "test"}
    assert len(artifact["entries"]) == 2
    assert artifact["entries"][0]["status"] == "watch_capacity_source_expansion_shadow_only"
    assert artifact["entries"][0]["unique_round_overflow_rows"] == 1
    assert (
        artifact["entries"][0]["source_context_classes"]
        == "payload_verified_partial_action_only:1"
    )
