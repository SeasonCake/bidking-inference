import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_shadow_sampler_value_source_profile_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_shadow_sampler_value_source_profile_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _trial(*, labels: list[str], audit_probe: bool = False) -> dict[str, object]:
    return {
        "status": (
            "audit_probe_guarded_shadow_trial"
            if audit_probe
            else "blocked_guarded_shadow_trial"
        ),
        "sampler_status": "blocked_seed_instability",
        "audit_probe": audit_probe,
        "guarded_sampler_result": {
            "component_statuses": [
                {
                    "component": "q6_value",
                    "status": "blocked_seed_instability",
                    "support_gate": {
                        "status": "watch_low_support" if audit_probe else "pass",
                        "low_support_watch_metrics": [
                            {
                                "watch_label": labels[0],
                                "support_rows": 8,
                                "support_sessions": 3,
                            }
                        ]
                        if audit_probe
                        else [],
                    },
                    "top_applied_hurt_metrics": [
                        {
                            "watch_label": label,
                            "candidate_rows": 4,
                            "candidate_sessions": 2,
                            "candidate_hurt_rate": 1.0,
                        }
                        for label in labels
                    ],
                }
            ]
        },
    }


def test_value_source_profile_audit_parses_composite_labels() -> None:
    module = _load_module()

    parsed = module._parse_watch_label(
        "q6_value|map_id,evidence_profile_key|all:"
        "q6_value:map_id=2508|evidence_profile_key=item+shape"
    )

    assert parsed["component"] == "q6_value"
    assert parsed["group_field"] == "map_id,evidence_profile_key"
    assert parsed["movement_policy"] == "all"
    assert parsed["map_id"] == "2508"
    assert parsed["evidence_profile_key"] == "item+shape"

    profile = module._parse_evidence_profile_key(
        "public:total+item+shape+layout"
    )
    assert profile["tokens"] == ["public:total", "item", "shape", "layout"]
    assert profile["public_sources"] == ["public:total"]
    assert profile["anchors"] == ["item", "shape", "layout"]
    assert profile["semantic_class"] == "public:total|item+shape+layout"


def test_value_source_profile_audit_reports_risk_migration() -> None:
    module = _load_module()
    baseline = _trial(
        labels=[
            "q6_value|map_id|all:q6_value:2510",
            "q6_value|evidence_profile_key|all:"
            "q6_value:public:total+item+shape",
        ]
    )
    probe = _trial(
        audit_probe=True,
        labels=[
            "q6_value|map_id|up_only:q6_value:2405",
            "q6_value|evidence_profile_key|all:"
            "q6_value:public:max_item_cells+item+shape",
        ],
    )

    result = module.summarize_value_source_profile_audit(
        (("baseline", baseline), ("probe", probe)),
    )

    assert result["status"] == "blocked_risk_migration"
    assert result["shadow_only"] is True
    assert result["affects_bid"] is False
    assert result["can_promote"] is False
    assert result["migration"]["risk_migration_detected"] is True
    assert result["migration"]["introduced_hurt_labels"] == [
        "q6_value|evidence_profile_key|all:"
        "q6_value:public:max_item_cells+item+shape",
        "q6_value|map_id|up_only:q6_value:2405",
    ]
    latest = result["runs"][1]
    assert latest["source_profile_parser_required"] is True
    assert latest["support_gate"] == "watch_low_support"
    assert latest["hurt_map_ids"] == ["2405"]
    assert latest["hurt_evidence_profiles"] == [
        "public:max_item_cells+item+shape"
    ]
    assert latest["profile_public_source_counts"] == {
        "public:max_item_cells": 1
    }
    assert latest["profile_anchor_counts"] == {"item": 1, "shape": 1}
    assert latest["profile_semantic_class_counts"] == {
        "public:max_item_cells|item+shape": 1
    }
    parser = result["source_profile_parser"]
    assert parser["status"] == "blocked_mixed_map_profile_risk"
    assert parser["profile_semantic_migration_detected"] is True
    assert parser["latest_map_only_hurt_label_count"] == 1
    assert parser["latest_profile_hurt_label_count"] == 1
    assert parser["introduced_profile_semantic_classes"] == [
        "public:max_item_cells|item+shape"
    ]
    assert "evidence_profile_key" in parser["minimum_required_inputs"]
