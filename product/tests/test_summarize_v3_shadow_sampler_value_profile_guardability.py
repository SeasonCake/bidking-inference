import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_shadow_sampler_value_profile_guardability.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_shadow_sampler_value_profile_guardability",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _detail(
    *,
    session_id: str,
    map_id: int,
    profile: str,
    semantic: str,
    source: str,
    anchor: str,
    effect: str,
) -> dict[str, object]:
    return {
        "session_id": session_id,
        "map_id": map_id,
        "evidence_profile_key": profile,
        "profile_semantic_class": semantic,
        "profile_source_class": source,
        "profile_anchor_class": anchor,
        "effect": effect,
        "directional_error": effect == "hurt",
    }


def test_guardability_uses_full_detail_rows_not_examples() -> None:
    module = _load_module()
    details = {
        "status": "blocked_map_only_details_ready",
        "label_count": 1,
        "candidate_rows": 3,
        "labels": [
            {
                "watch_label": "q6_value|map_id|up_only:q6_value:2502",
                "detail_rows": [
                    _detail(
                        session_id="s1",
                        map_id=2502,
                        profile="public:total+shape",
                        semantic="public:total|shape",
                        source="public:total",
                        anchor="shape",
                        effect="hurt",
                    ),
                    _detail(
                        session_id="s2",
                        map_id=2502,
                        profile="public:total+shape",
                        semantic="public:total|shape",
                        source="public:total",
                        anchor="shape",
                        effect="hurt",
                    ),
                    _detail(
                        session_id="s3",
                        map_id=2502,
                        profile="item+shape",
                        semantic="no_public|item+shape",
                        source="no_public",
                        anchor="item+shape",
                        effect="helped",
                    ),
                ],
                "example_rows": [],
            }
        ],
    }

    result = module.summarize_guardability(
        details,
        dimensions=("profile_semantic_class",),
        min_rows=2,
        min_sessions=2,
        min_labels=1,
        min_maps=1,
        min_hurt_rate=0.8,
        max_helped_rate=0.1,
    )

    assert result["detail_rows"] == 3
    assert result["candidate_cluster_count"] == 1
    assert result["candidate_clusters"][0]["group"] == "public:total|shape"
    assert result["candidate_clusters"][0]["hurt_rate"] == 1.0
    assert result["shadow_only"] is True
    assert result["affects_bid"] is False
    assert result["can_promote"] is False


def test_guardability_blocks_mixed_or_single_map_clusters() -> None:
    module = _load_module()
    details = {
        "status": "blocked_map_only_details_ready",
        "label_count": 2,
        "candidate_rows": 4,
        "labels": [
            {
                "watch_label": "q6_value|map_id|up_only:q6_value:2502",
                "detail_rows": [
                    _detail(
                        session_id="s1",
                        map_id=2502,
                        profile="public:total+shape",
                        semantic="public:total|shape",
                        source="public:total",
                        anchor="shape",
                        effect="hurt",
                    ),
                    _detail(
                        session_id="s2",
                        map_id=2502,
                        profile="public:total+shape",
                        semantic="public:total|shape",
                        source="public:total",
                        anchor="shape",
                        effect="helped",
                    ),
                ],
            },
            {
                "watch_label": "q6_value|map_id|all:q6_value:2502",
                "detail_rows": [
                    _detail(
                        session_id="s3",
                        map_id=2502,
                        profile="public:total+shape",
                        semantic="public:total|shape",
                        source="public:total",
                        anchor="shape",
                        effect="hurt",
                    ),
                    _detail(
                        session_id="s4",
                        map_id=2502,
                        profile="public:total+shape",
                        semantic="public:total|shape",
                        source="public:total",
                        anchor="shape",
                        effect="hurt",
                    ),
                ],
            },
        ],
    }

    result = module.summarize_guardability(
        details,
        dimensions=("profile_semantic_class",),
        min_rows=4,
        min_sessions=4,
        min_labels=2,
        min_maps=2,
        min_hurt_rate=0.7,
        max_helped_rate=0.3,
    )

    cluster = result["top_clusters"][0]
    assert result["status"] == "blocked_no_stable_profile_guard"
    assert result["candidate_cluster_count"] == 0
    assert cluster["status"] == "overfit_risk_single_label_or_map"
    assert cluster["maps"] == 1


def test_guardability_reports_no_stable_guard_for_mixed_helped_hurt() -> None:
    module = _load_module()
    details = {
        "status": "blocked_map_only_details_ready",
        "labels": [
            {
                "watch_label": "q6_value|map_id|up_only:q6_value:2502",
                "detail_rows": [
                    _detail(
                        session_id=f"s{idx}",
                        map_id=2502 + idx % 2,
                        profile="public:total+shape",
                        semantic="public:total|shape",
                        source="public:total",
                        anchor="shape",
                        effect="hurt" if idx < 3 else "helped",
                    )
                    for idx in range(6)
                ],
            }
        ],
    }

    result = module.summarize_guardability(
        details,
        dimensions=("profile_semantic_class",),
        min_rows=4,
        min_sessions=4,
        min_labels=1,
        min_maps=2,
        min_hurt_rate=0.5,
        max_helped_rate=0.2,
    )

    assert result["status"] == "blocked_no_stable_profile_guard"
    assert result["mixed_cluster_count"] == 1
    assert result["mixed_clusters"][0]["status"] == "mixed_hurt_helped"
