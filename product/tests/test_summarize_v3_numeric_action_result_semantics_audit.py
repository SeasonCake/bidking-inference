import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_numeric_action_result_semantics_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_numeric_action_result_semantics_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_classifies_100105_as_q3_total_cells_not_session_capacity() -> None:
    module = _load_module()

    result = module.classify_numeric_action_result(
        action_id=100105,
        result=56,
        inventory={
            "total_item_count": 57,
            "warehouse_total_cells": 176,
            "buckets": {
                "1": {"count": 18, "total_cells": 31},
                "3": {"count": 16, "total_cells": 56},
                "4": {"count": 12, "total_cells": 38},
                "5": {"count": 9, "total_cells": 44},
                "6": {"count": 2, "total_cells": 7},
            },
        },
    )

    assert result["status"] == "watch_expected_semantic_match"
    assert result["expected_semantic"] == "bucket_total_cells"
    assert result["expected_path"] == ["bucket", "3", "total_cells"]
    assert result["expected_value"] == 56
    assert result["expected_match"] is True
    assert result["parser_implication"] == "not_session_capacity_signal"
    assert result["matched_candidate_values"] == [
        {"candidate": "bucket_3_total_cells", "value": 56}
    ]


def test_classifies_100115_as_session_item_count_signal() -> None:
    module = _load_module()

    result = module.classify_numeric_action_result(
        action_id=100115,
        result=57,
        inventory={
            "total_item_count": 57,
            "warehouse_total_cells": 176,
            "buckets": {
                "3": {"count": 16, "total_cells": 56},
            },
        },
    )

    assert result["status"] == "watch_expected_semantic_match"
    assert result["expected_semantic"] == "session_total_item_count"
    assert result["expected_path"] == ["session", "total_item_count"]
    assert result["expected_match"] is True
    assert result["parser_implication"] == "session_capacity_signal"


def test_source_required_without_session_signal_keeps_blocker() -> None:
    module = _load_module()
    sample = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "samples"
        / "fatbeans"
        / "fatbeans_valid_ethan_2410_1rounds_2410_1295019008815241_0283.json"
    )

    result = module.summarize_numeric_action_result_semantics(
        [sample],
        focus_maps=["2410"],
        source_parser_requirements={
            "rows": [
                {
                    "map_id": "2410",
                    "status": "blocked_session_capacity_source_parser_required",
                    "source_semantics": {
                        "session_capacity_source_semantics_rows": 1,
                    },
                }
            ]
        },
    )

    assert result["status"] == "blocked_session_capacity_still_unexplained"
    assert result["shadow_only"] is True
    assert result["affects_bid"] is False
    assert result["summary"]["numeric_action_rows"] == 2
    assert result["summary"]["source_required_rows"] == 2
    assert result["summary"]["session_capacity_signal_rows"] == 0
    assert result["summary"]["non_session_expected_rows"] == 2
    assert result["summary"]["action_id_counts"] == {"100105": 2}
    assert result["summary"]["expected_semantic_counts"] == {
        "bucket_total_cells": 2
    }
