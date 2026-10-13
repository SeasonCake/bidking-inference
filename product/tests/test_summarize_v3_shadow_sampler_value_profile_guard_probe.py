import hashlib
import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_shadow_sampler_value_profile_guard_probe.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_shadow_sampler_value_profile_guard_probe",
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
        "status": "watch_with_hurt_alternatives",
        "guard_trial_contract": {
            "status": "shadow_guard_trial_design",
            "action_counts": {
                "freeze_component": 1,
                "require_source_support_gate": 1,
            },
            "component_actions": [
                {
                    "component": "q6_cells",
                    "trial_action": "freeze_component",
                },
                {
                    "component": "q6_count",
                    "trial_action": "require_source_support_gate",
                },
            ],
        },
    }


def _row(session_id: str, *, profile: str) -> dict[str, object]:
    return {
        "file": f"{session_id}#r1",
        "status": "ready",
        "session_id": session_id,
        "map_id": "2503",
        "map_family": "shipwreck",
        "evidence_profile_key": profile,
        "v3_truth_available": True,
        "v3_post_ready": True,
        "v3_ccvc_ready": True,
        "v3_ccvc_match_scope": "ccv_component_likelihood",
        "v3_ccvc_affects_bid": False,
        "v3_post_q6_value_p50": 200,
        "v3_ccvc_q6_value_p50": 300,
        "v3_truth_q6_raw_value": 400,
    }


def test_profile_guard_probe_runs_guardability_candidates() -> None:
    module = _load_module()
    profile = "tool:category+item+shape"
    fold0 = [_session_for_fold(0, prefix=f"vp0_{idx}") for idx in range(4)]
    fold1 = [_session_for_fold(1, prefix=f"vp1_{idx}") for idx in range(4)]
    rows = [_row(session_id, profile=profile) for session_id in (*fold0, *fold1)]
    guardability = {
        "status": "blocked_profile_guard_candidates_need_holdout",
        "candidate_cluster_count": 1,
        "candidate_clusters": [
            {
                "dimension": "evidence_profile_key",
                "group": profile,
                "status": "profile_guard_candidate_needs_holdout",
            }
        ],
    }
    baseline_trial = {
        "guarded_sampler_result": {
            "component_statuses": [
                {
                    "component": "q6_value",
                    "applied_hurts": ["q6_value|evidence_profile_key|all:x"],
                }
            ]
        }
    }

    result = module.summarize_profile_guard_probes(
        {0: rows},
        prototype=_prototype(),
        guardability=guardability,
        baseline_trial=baseline_trial,
        posterior_trials=64,
        components=("q6_value",),
        group_fields=("evidence_profile_key", "map_id,evidence_profile_key"),
        movement_policies=("all",),
        features=(),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
        min_watch_support_rows=2,
        min_watch_support_sessions=2,
    )

    assert result["interface"] == "v3_ccvc_q6_value_profile_guard_probe"
    assert result["status"] == "watch_audit_probe_holdout_clean"
    assert result["shadow_only"] is True
    assert result["affects_bid"] is False
    assert result["can_promote"] is False
    assert result["profiles"] == [profile]
    assert result["baseline_q6_value_hurt_count"] == 1
    assert result["min_probe_q6_value_hurt_count"] == 0
    assert result["probes"][0]["audit_probe"] is True
    assert result["probes"][0]["q6_value_hurt_delta"] == -1
    assert result["probes"][0]["trial_options"][
        "manual_exclude_q6_value_profiles"
    ] == [profile]
