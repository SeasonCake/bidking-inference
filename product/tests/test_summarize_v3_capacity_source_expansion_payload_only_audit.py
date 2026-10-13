import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_capacity_source_expansion_payload_only_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_capacity_source_expansion_payload_only_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _settlement_row(
    file: str,
    *,
    context: str,
    excess: int,
    mechanism: str = "session_capacity_source_semantics",
    action_max: int = 0,
    action_gap: int = 50,
) -> dict[str, object]:
    return {
        "status": "ok",
        "file": file,
        "map_id": 2501,
        "map_family": "shipwreck",
        "source_context_class": context,
        "source_evidence_class": "settlement_payload_verified_only",
        "mechanism_class": mechanism,
        "unique_round_cap_excess_after_temp_zodiac_count": excess,
        "non_zodiac_missing_from_drop_universe_count": 0,
        "event_action_result_count_all": 5,
        "event_action_observed_item_count_max": action_max,
        "event_action_observed_item_inventory_gap_min": action_gap,
        "event_action_observed_item_ratio_max": 0.1 if action_max else 0.0,
    }


def test_payload_only_audit_joins_holdout_and_prebid_pressure() -> None:
    module = _load_module()
    rows = [
        _settlement_row(
            "payload_partial.json",
            context="payload_verified_partial_action_only",
            excess=7,
            action_max=4,
            action_gap=61,
        ),
        _settlement_row(
            "payload_empty.json",
            context="payload_verified_empty_action_results",
            excess=3,
        ),
        {
            **_settlement_row(
                "public.json",
                context="public_total_confirmed",
                excess=2,
                mechanism="server_side_settlement_expansion",
            ),
            "source_evidence_class": "public_total_matches_inventory",
        },
    ]
    eval_rows = [
        {
            "example_file": "payload_partial.json",
            "truth_unique_round_overflow": True,
            "covered_unique_round_overflow": True,
            "candidate_source": "primary",
            "train_source_semantics_rows": 2,
            "fold": 1,
        },
        {
            "example_file": "payload_empty.json",
            "truth_unique_round_overflow": True,
            "covered_unique_round_overflow": False,
            "candidate_source": "none",
            "train_source_semantics_rows": 0,
            "fold": 2,
        },
    ]
    prebid_rows = [
        {
            "file": "payload_partial.json#prebid_r4",
            "status": "ready",
            "round": 4,
            "v3_cse_ready": True,
            "v3_cse_candidate": True,
            "v3_cse_pressure_candidate": True,
            "v3_cse_target_count_source": "exact",
            "v3_cse_target_prior_max_delta": 2,
            "v3_cse_target_to_unique_non_temp_p95_delta": -4,
        },
        {
            "file": "payload_empty.json#prebid_r2",
            "status": "ready",
            "round": 2,
            "v3_cse_ready": True,
            "v3_cse_candidate": True,
            "v3_cse_pressure_candidate": False,
            "v3_cse_target_count_source": "floor",
        },
    ]
    source_shapes_by_file = {
        "payload_partial.json": {
            "source_action_payload_shape_class": "item_reveal_payload",
            "source_action_result_blocks": 3,
            "source_action_parsed_results": 3,
            "source_action_result_value_blocks": 1,
            "source_action_item_payload_blocks": 4,
            "source_action_item_payload_block_max": 2,
            "source_action_observed_item_count": 4,
            "source_action_observed_item_count_max": 2,
            "source_action_ids": {"100130": 2, "100105": 1},
            "source_action_result_fields": {"14": 1},
        },
        "payload_empty.json": {
            "source_action_payload_shape_class": "numeric_only_result",
            "source_action_result_blocks": 2,
            "source_action_parsed_results": 2,
            "source_action_result_value_blocks": 2,
            "source_action_item_payload_blocks": 0,
            "source_action_item_payload_block_max": 0,
            "source_action_observed_item_count": 0,
            "source_action_observed_item_count_max": 0,
            "source_action_ids": {"100105": 2},
            "source_action_result_fields": {"14": 2},
        },
    }

    result = module.summarize_payload_only(
        rows=rows,
        eval_rows=eval_rows,
        prebid_rows=prebid_rows,
        source_shapes_by_file=source_shapes_by_file,
    )

    assert result["truth_rows"] == 3
    assert result["payload_truth_rows"] == 2
    assert result["external_truth_rows"] == 1
    assert result["payload_map_id_missed_rows"] == 1
    assert result["payload_prebid_candidate_rows"] == 2
    assert result["payload_prebid_pressure_rows"] == 1
    assert result["payload_source_shape_classes"] == {
        "item_reveal_payload": 1,
        "numeric_only_result": 1,
    }

    partial = result["groups"]["payload_verified_partial_action_only"]
    assert partial["rows"] == 1
    assert partial["map_id_missed_rows"] == 0
    assert partial["prebid_pressure_rows"] == 1
    assert partial["action_max"]["max"] == 4
    assert partial["source_shape_classes"] == {"item_reveal_payload": 1}
    assert partial["source_action_ids"] == {"100130": 2, "100105": 1}
    assert partial["source_action_item_payload_blocks"]["max"] == 4
    assert partial["source_action_item_payload_block_max"]["max"] == 2
    assert partial["source_action_observed_item_count_max"]["max"] == 2

    empty = result["groups"]["payload_verified_empty_action_results"]
    assert empty["map_id_missed_rows"] == 1
    assert empty["prebid_pressure_rows"] == 0
    assert empty["source_shape_classes"] == {"numeric_only_result": 1}
    assert empty["source_action_ids"] == {"100105": 2}
    assert empty["source_action_item_payload_blocks"]["max"] == 0
    assert empty["source_action_item_payload_block_max"]["max"] == 0
    assert empty["source_action_observed_item_count_max"]["max"] == 0
