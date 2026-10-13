import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_shadow_sampler_value_map_profile_details.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_shadow_sampler_value_map_profile_details",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _row(
    *,
    session_id: str,
    truth: float,
    profile: str,
    hero: str = "aisha",
) -> dict[str, object]:
    return {
        "file": f"{session_id}.json",
        "session_id": session_id,
        "status": "ready",
        "v3_truth_available": True,
        "v3_post_ready": True,
        "map_id": 2502,
        "map_family": "shipwreck",
        "hero": hero,
        "evidence_profile_key": profile,
        "hero_map_evidence_profile": f"{hero}|2502|{profile}",
        "v3_ccvc_ready": True,
        "v3_ccvc_match_scope": "ccv_component_likelihood",
        "v3_post_q6_value_p50": 100.0,
        "v3_ccvc_q6_value_p50": 150.0,
        "v3_truth_q6_raw_value": truth,
    }


def test_map_profile_details_extracts_latest_map_only_labels() -> None:
    module = _load_module()
    audit = {
        "runs": [
            {
                "label": "probe",
                "top_hurt_metrics": [
                    {
                        "posterior_seed": 1,
                        "watch_label": "q6_value|map_id|up_only:q6_value:2502",
                        "candidate_rows": 2,
                    },
                    {
                        "posterior_seed": 1,
                        "watch_label": (
                            "q6_value|evidence_profile_key|all:"
                            "q6_value:public:total+shape"
                        ),
                        "candidate_rows": 2,
                    },
                ],
            }
        ]
    }

    metrics = module._label_metrics_from_audit(audit)

    assert len(metrics) == 1
    assert metrics[0]["watch_label"] == "q6_value|map_id|up_only:q6_value:2502"
    assert metrics[0]["group"] == "2502"
    assert metrics[0]["movement_policy"] == "up_only"


def test_map_profile_details_replays_rows_and_profiles() -> None:
    module = _load_module()
    rows = [
        _row(
            session_id="s0",
            truth=160.0,
            profile="public:total+item+shape",
        ),
        _row(
            session_id="s2",
            truth=120.0,
            profile="public:max_item_cells+shape",
            hero="ethan",
        ),
    ]
    metric = {
        "watch_label": "q6_value|map_id|up_only:q6_value:2502",
        "posterior_seed": 0,
        "component": "q6_value",
        "group_field": "map_id",
        "group": "2502",
        "movement_policy": "up_only",
        "source_metric": {"candidate_rows": 1, "candidate_sessions": 1},
    }

    result = module.summarize_label_details(
        rows,
        metric,
        folds=2,
        min_windows=1,
        min_sessions=1,
        min_changed=1,
        example_limit=4,
    )

    assert result["candidate_rows"] == 1
    assert result["candidate_sessions"] == 1
    assert result["row_count_matches_source_metric"] is True
    assert result["evidence_profile_counts"] == {
        "public:max_item_cells+shape": 1
    }
    assert result["profile_public_source_counts"] == {
        "public:max_item_cells": 1
    }
    assert result["profile_anchor_counts"] == {"shape": 1}
    assert result["profile_semantic_class_counts"] == {
        "public:max_item_cells|shape": 1
    }
    assert len(result["detail_rows"]) == 1
    assert result["detail_rows"][0]["profile_source_class"] == (
        "public:max_item_cells"
    )
    assert result["detail_rows"][0]["profile_anchor_class"] == "shape"
    assert result["example_rows"][0]["effect"] == "hurt"
    assert result["example_rows"][0]["hero_map_evidence_profile"] == (
        "ethan|2502|public:max_item_cells+shape"
    )


def test_map_profile_details_artifact_is_shadow_only() -> None:
    module = _load_module()
    audit = {
        "status": "blocked_risk_migration",
        "source_profile_parser": {
            "status": "blocked_mixed_map_profile_risk",
        },
        "runs": [
            {
                "label": "probe",
                "top_hurt_metrics": [
                    {
                        "posterior_seed": 0,
                        "watch_label": "q6_value|map_id|up_only:q6_value:2502",
                        "candidate_rows": 1,
                        "candidate_sessions": 1,
                    }
                ],
            }
        ],
    }
    rows = [
        _row(
            session_id="s0",
            truth=160.0,
            profile="public:total+item+shape",
        ),
        _row(
            session_id="s2",
            truth=120.0,
            profile="public:max_item_cells+shape",
            hero="ethan",
        ),
    ]

    result = module.summarize_map_profile_details(
        {0: rows},
        audit,
        folds=2,
        min_windows=1,
        min_sessions=1,
        min_changed=1,
    )

    assert result["interface"] == "v3_ccvc_q6_value_map_profile_details"
    assert result["status"] == "blocked_map_only_details_ready"
    assert result["shadow_only"] is True
    assert result["affects_bid"] is False
    assert result["active"] is False
    assert result["can_promote"] is False
    assert result["source_profile_parser_status"] == (
        "blocked_mixed_map_profile_risk"
    )
    assert result["candidate_rows"] == 1
    assert result["candidate_sessions_sum"] == 1
