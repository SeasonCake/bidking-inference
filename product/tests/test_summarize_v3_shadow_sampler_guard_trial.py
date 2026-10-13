import hashlib
import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_shadow_sampler_guard_trial.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_shadow_sampler_guard_trial",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _stable_fold(value: str, folds: int) -> int:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % int(folds)


def _session_for_fold(fold: int, *, prefix: str) -> str:
    for idx in range(1000):
        session_id = f"{prefix}_{idx}"
        if _stable_fold(session_id, 2) == fold:
            return session_id
    raise AssertionError(f"no session for fold {fold}")


def _prototype() -> dict[str, object]:
    return {
        "status": "blocked_seed_instability",
        "guard_trial_contract": {
            "status": "shadow_guard_trial_design",
            "action_counts": {
                "freeze_component": 1,
                "guard_hurt_groups_keep_component_inactive": 1,
                "require_source_support_gate": 1,
            },
            "requires_source_parser": True,
            "component_actions": [
                {
                    "component": "q6_cells",
                    "trial_action": "freeze_component",
                    "candidate_exclude_labels": ["q6_cells:2502"],
                },
                {
                    "component": "q6_value",
                    "trial_action": "guard_hurt_groups_keep_component_inactive",
                    "candidate_exclude_labels": ["q6_value:2502"],
                },
                {
                    "component": "q6_count",
                    "trial_action": "require_source_support_gate",
                    "requires_source_parser": True,
                    "low_support_watch_metrics": [
                        {
                            "watch_label": "q6_count|map_id|all:q6_count:2501",
                            "support_rows": 8,
                            "support_sessions": 3,
                        }
                    ],
                },
            ],
        },
    }


def _row(
    *,
    session_id: str,
    map_id: str,
    value_truth: int,
    value_baseline: int,
    value_candidate: int,
    profile: str = "item+shape",
) -> dict[str, object]:
    return {
        "file": f"{session_id}#r1",
        "status": "ready",
        "session_id": session_id,
        "map_id": map_id,
        "map_family": "shipwreck",
        "evidence_profile_key": profile,
        "v3_truth_available": True,
        "v3_post_ready": True,
        "v3_ccvc_ready": True,
        "v3_ccvc_match_scope": "ccv_component_likelihood",
        "v3_ccvc_affects_bid": False,
        "v3_post_q6_count_p50": 2,
        "v3_ccvc_q6_count_p50": 3,
        "v3_truth_q6_count": 4,
        "v3_post_q6_cells_p50": 8,
        "v3_ccvc_q6_cells_p50": 9,
        "v3_truth_q6_cells": 10,
        "v3_post_q6_value_p50": value_baseline,
        "v3_ccvc_q6_value_p50": value_candidate,
        "v3_truth_q6_raw_value": value_truth,
    }


def test_guard_trial_options_from_contract() -> None:
    module = _load_module()

    options = module.build_trial_options(_prototype())

    assert options["component_move_cells"] is False
    assert options["requires_source_parser"] is True
    assert options["excluded_components"] == ["q6_cells", "q6_count"]
    assert options["candidate_exclude_labels"] == ["q6_value:2502"]
    assert "^q6_cells:.*" in options["candidate_exclude_pattern"]
    assert "^q6_count:.*" in options["candidate_exclude_pattern"]
    assert "^q6_value:2502$" in options["candidate_exclude_pattern"]
    assert options["audit_probe"] is False


def test_guard_trial_options_mark_manual_excludes_as_audit_probe() -> None:
    module = _load_module()

    options = module.build_trial_options(
        _prototype(),
        extra_exclude_labels=("q6_value:2510",),
        extra_exclude_components=("q6_value",),
        extra_exclude_q6_value_profiles=("tool:category+item+shape",),
    )

    assert options["audit_probe"] is True
    assert options["manual_exclude_labels"] == ["q6_value:2510"]
    assert options["manual_exclude_components"] == ["q6_value"]
    assert options["manual_exclude_q6_value_profiles"] == [
        "tool:category+item+shape"
    ]
    assert "q6_value:2510" in options["candidate_exclude_labels"]
    assert "q6_value" in options["excluded_components"]
    assert "^q6_value:2510$" in options["candidate_exclude_pattern"]
    assert "^q6_value:.*" in options["candidate_exclude_pattern"]
    assert (
        "^q6_value:tool:category\\+item\\+shape$"
        in options["candidate_exclude_pattern"]
    )
    assert (
        "^q6_value:.*evidence_profile_key=tool:category\\+item\\+shape"
        in options["candidate_exclude_pattern"]
    )


def test_guard_trial_rows_applies_freeze_and_exclude_contract() -> None:
    module = _load_module()
    fold0 = [_session_for_fold(0, prefix=f"g0_{idx}") for idx in range(4)]
    fold1 = [_session_for_fold(1, prefix=f"g1_{idx}") for idx in range(4)]
    rows = [
        *[
            _row(
                session_id=session_id,
                map_id="2502",
                value_truth=100,
                value_baseline=200,
                value_candidate=300,
            )
            for session_id in (fold0[0], fold0[1], fold1[0], fold1[1])
        ],
        *[
            _row(
                session_id=session_id,
                map_id="2503",
                value_truth=400,
                value_baseline=200,
                value_candidate=300,
            )
            for session_id in (fold0[2], fold0[3], fold1[2], fold1[3])
        ],
    ]

    result = module.summarize_guard_trial_rows(
        {0: rows},
        prototype=_prototype(),
        posterior_trials=64,
        components=("q6_count", "q6_cells", "q6_value"),
        group_fields=("map_id",),
        movement_policies=("all",),
        features=(),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
        min_watch_support_rows=2,
        min_watch_support_sessions=2,
    )

    assert result["status"] == "watch_guarded_shadow_trial"
    assert result["sampler_status"] == "watch_shadow_candidate"
    assert result["shadow_only"] is True
    assert result["affects_bid"] is False
    assert result["trial_options"]["component_move_cells"] is False
    components = {
        row["component"]: row
        for row in result["guarded_sampler_result"]["component_statuses"]
    }
    assert components["q6_count"]["status"] == "sample_limited"
    assert components["q6_cells"]["status"] == "sample_limited"
    assert components["q6_value"]["status"] == "watch_shadow_candidate"
    assert components["q6_value"]["stable_watch_candidate_labels"] == [
        "q6_value|map_id|all:q6_value:2503"
    ]


def test_guard_trial_rows_keeps_manual_probe_out_of_watch_status() -> None:
    module = _load_module()
    fold0 = [_session_for_fold(0, prefix=f"p0_{idx}") for idx in range(4)]
    fold1 = [_session_for_fold(1, prefix=f"p1_{idx}") for idx in range(4)]
    rows = [
        _row(
            session_id=session_id,
            map_id="2503",
            value_truth=400,
            value_baseline=200,
            value_candidate=300,
        )
        for session_id in (*fold0, *fold1)
    ]

    result = module.summarize_guard_trial_rows(
        {0: rows},
        prototype=_prototype(),
        posterior_trials=64,
        components=("q6_count", "q6_cells", "q6_value"),
        group_fields=("map_id",),
        movement_policies=("all",),
        features=(),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
        min_watch_support_rows=2,
        min_watch_support_sessions=2,
        extra_exclude_labels=("q6_value:2510",),
    )

    assert result["sampler_status"] == "watch_shadow_candidate"
    assert result["status"] == "audit_probe_guarded_shadow_trial"
    assert result["audit_probe"] is True
    assert result["trial_options"]["manual_exclude_labels"] == ["q6_value:2510"]


def test_guard_trial_rows_applies_q6_value_profile_probe() -> None:
    module = _load_module()
    fold0 = [_session_for_fold(0, prefix=f"vp0_{idx}") for idx in range(4)]
    fold1 = [_session_for_fold(1, prefix=f"vp1_{idx}") for idx in range(4)]
    rows = [
        _row(
            session_id=session_id,
            map_id="2503",
            value_truth=400,
            value_baseline=200,
            value_candidate=300,
            profile="tool:category+item+shape",
        )
        for session_id in (*fold0, *fold1)
    ]

    result = module.summarize_guard_trial_rows(
        {0: rows},
        prototype=_prototype(),
        posterior_trials=64,
        components=("q6_count", "q6_cells", "q6_value"),
        group_fields=("evidence_profile_key", "map_id,evidence_profile_key"),
        movement_policies=("all",),
        features=(),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
        min_watch_support_rows=2,
        min_watch_support_sessions=2,
        extra_exclude_q6_value_profiles=("tool:category+item+shape",),
    )

    assert result["status"] == "audit_probe_guarded_shadow_trial"
    assert result["sampler_status"] == "sample_limited"
    assert result["audit_probe"] is True
    assert result["trial_options"]["manual_exclude_q6_value_profiles"] == [
        "tool:category+item+shape"
    ]
    components = {
        row["component"]: row
        for row in result["guarded_sampler_result"]["component_statuses"]
    }
    assert components["q6_value"]["status"] == "sample_limited"
