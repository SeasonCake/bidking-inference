import hashlib
import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_shadow_sampler_prototype.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_shadow_sampler_prototype",
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


def _row(
    *,
    session_id: str,
    truth: int = 4,
    baseline: int = 2,
    ccvc: int = 3,
    map_id: str = "2502",
    map_family: str = "shipwreck",
    affects_bid: bool = False,
) -> dict[str, object]:
    return {
        "file": f"{session_id}#r1",
        "status": "ready",
        "session_id": session_id,
        "map_id": map_id,
        "map_family": map_family,
        "evidence_profile_key": "public:total+shape",
        "v3_truth_available": True,
        "v3_post_ready": True,
        "v3_ccvc_ready": True,
        "v3_ccvc_affects_bid": affects_bid,
        "v3_ccvc_match_scope": "ccv_component_likelihood",
        "v3_ccvc_diagnostics": (
            "ccvc_explicit_q6_anchor_count=1;"
            "ccvc_unassigned_anchor_count=0"
        ),
        "v3_summary_session_total_count_exact": 20,
        "v3_summary_q6_count_floor": 1,
        "v3_post_q6_count_p50": baseline,
        "v3_ccvc_q6_count_p50": ccvc,
        "v3_truth_q6_count": truth,
    }


def _watch_rows() -> list[dict[str, object]]:
    fold0 = [_session_for_fold(0, prefix=f"f0_{idx}") for idx in range(4)]
    fold1 = [_session_for_fold(1, prefix=f"f1_{idx}") for idx in range(4)]
    return [_row(session_id=session_id) for session_id in (*fold0, *fold1)]


def test_shadow_sampler_prototype_finds_watch_candidate() -> None:
    module = _load_module()

    result = module.summarize_seed_run(
        _watch_rows(),
        posterior_seed=0,
        components=("q6_count",),
        group_fields=("map_id",),
        movement_policies=("all",),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
    )

    assert result["status"] == "watch_shadow_candidate"
    assert result["row_contract"]["ccvc_component_likelihood_rows"] == 8
    assert result["watch_candidates"][0]["label"] == "q6_count|map_id|all"
    assert result["watch_candidates"][0]["candidate_groups"] == ["q6_count:2502"]
    assert result["watch_candidates"][0]["candidate_delta_p50_mae"] == -1


def test_shadow_sampler_prototype_blocks_affects_bid_rows() -> None:
    module = _load_module()
    rows = _watch_rows()
    rows[0]["v3_ccvc_affects_bid"] = True

    result = module.summarize_seed_run(
        rows,
        posterior_seed=0,
        components=("q6_count",),
        group_fields=("map_id",),
        movement_policies=("all",),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
    )

    assert result["status"] == "blocked_shadow_affects_bid"
    assert result["row_contract"]["shadow_affects_bid_rows"] == 1


def test_shadow_sampler_prototype_marks_seed_instability() -> None:
    module = _load_module()
    seed0 = module.summarize_seed_run(
        _watch_rows(),
        posterior_seed=0,
        components=("q6_count",),
        group_fields=("map_id",),
        movement_policies=("all",),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
    )
    seed1 = module.summarize_seed_run(
        [_row(session_id=row["session_id"], map_id="2503") for row in _watch_rows()],
        posterior_seed=1,
        components=("q6_count",),
        group_fields=("map_id",),
        movement_policies=("all",),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
    )

    stable = module.summarize_prototype_runs(
        (seed0, dict(seed0, posterior_seed=1)),
        posterior_trials=64,
        component_move_cells=True,
        min_watch_support_rows=2,
        min_watch_support_sessions=2,
    )

    assert stable["status"] == "watch_shadow_candidate"
    assert stable["stable_watch_candidate_labels"] == [
        "q6_count|map_id|all:q6_count:2502"
    ]
    stable_component = stable["component_statuses"][0]
    assert stable_component["component"] == "q6_count"
    assert stable_component["status"] == "watch_shadow_candidate"
    assert stable_component["support_gate"]["status"] == "pass"
    assert stable_component["stable_watch_candidate_labels"] == [
        "q6_count|map_id|all:q6_count:2502"
    ]
    assert stable_component["unstable_watch_candidate_labels"] == []
    assert stable_component["watch_labels_by_seed"] == [
        {
            "posterior_seed": 0,
            "watch_labels": ["q6_count|map_id|all:q6_count:2502"],
        },
        {
            "posterior_seed": 1,
            "watch_labels": ["q6_count|map_id|all:q6_count:2502"],
        },
    ]
    assert stable_component["unstable_watch_candidate_metrics"] == []
    stable_support = stable_component["watch_label_metrics_by_seed"]
    assert [
        (
            row["posterior_seed"],
            row["watch_label_metrics"][0]["watch_label"],
            row["watch_label_metrics"][0]["support_rows"],
            row["watch_label_metrics"][0]["support_sessions"],
        )
        for row in stable_support
    ] == [
        (0, "q6_count|map_id|all:q6_count:2502", 8, 8),
        (1, "q6_count|map_id|all:q6_count:2502", 8, 8),
    ]
    assert stable_component["applied_hurts"] == []
    assert stable_component["matrix_status_counts"] == {"watch": 2}
    assert stable_component["next_action"] == (
        "keep as diagnostic candidate; require readiness and live replay "
        "before promotion"
    )

    result = module.summarize_prototype_runs(
        (seed0, seed1),
        posterior_trials=64,
        component_move_cells=True,
        min_watch_support_rows=2,
        min_watch_support_sessions=2,
    )

    assert result["stable_watch_candidate_labels"] == []
    assert result["status"] == "blocked_seed_instability"
    unstable_component = result["component_statuses"][0]
    assert unstable_component["component"] == "q6_count"
    assert unstable_component["status"] == "blocked_seed_instability"
    assert unstable_component["support_gate"]["status"] == "pass"
    assert unstable_component["stable_watch_candidate_labels"] == []
    assert unstable_component["unstable_watch_candidate_labels"] == [
        "q6_count|map_id|all:q6_count:2502",
        "q6_count|map_id|all:q6_count:2503",
    ]
    assert unstable_component["watch_labels_by_seed"] == [
        {
            "posterior_seed": 0,
            "watch_labels": ["q6_count|map_id|all:q6_count:2502"],
        },
        {
            "posterior_seed": 1,
            "watch_labels": ["q6_count|map_id|all:q6_count:2503"],
        },
    ]
    assert [
        (
            row["posterior_seed"],
            row["watch_label"],
            row["support_rows"],
            row["support_sessions"],
        )
        for row in unstable_component["unstable_watch_candidate_metrics"]
    ] == [
        (0, "q6_count|map_id|all:q6_count:2502", 8, 8),
        (1, "q6_count|map_id|all:q6_count:2503", 8, 8),
    ]


def test_shadow_sampler_prototype_requires_candidate_on_every_seed() -> None:
    module = _load_module()
    seed0 = module.summarize_seed_run(
        _watch_rows(),
        posterior_seed=0,
        components=("q6_count",),
        group_fields=("map_id",),
        movement_policies=("all",),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
    )
    seed1 = dict(seed0)
    seed1["posterior_seed"] = 1
    seed1["watch_candidates"] = []
    seed1["status"] = "blocked_holdout_hurt"

    result = module.summarize_prototype_runs(
        (seed0, seed1),
        posterior_trials=64,
        component_move_cells=True,
        min_watch_support_rows=2,
        min_watch_support_sessions=2,
    )

    assert result["stable_watch_candidate_labels"] == []
    assert result["status"] == "blocked_seed_instability"
    component = result["component_statuses"][0]
    assert component["status"] == "blocked_seed_instability"
    assert component["unstable_watch_candidate_labels"] == [
        "q6_count|map_id|all:q6_count:2502"
    ]
    assert component["watch_labels_by_seed"] == [
        {
            "posterior_seed": 0,
            "watch_labels": ["q6_count|map_id|all:q6_count:2502"],
        },
        {
            "posterior_seed": 1,
            "watch_labels": [],
        },
    ]
    assert [
        (
            row["posterior_seed"],
            row["watch_label"],
            row["support_rows"],
            row["support_sessions"],
        )
        for row in component["unstable_watch_candidate_metrics"]
    ] == [
        (0, "q6_count|map_id|all:q6_count:2502", 8, 8),
    ]


def test_shadow_sampler_prototype_keeps_multi_seed_sample_limited() -> None:
    module = _load_module()
    rows = [
        _row(session_id=str(row["session_id"]), truth=4, baseline=2, ccvc=2)
        for row in _watch_rows()
    ]
    seed0 = module.summarize_seed_run(
        rows,
        posterior_seed=0,
        components=("q6_count",),
        group_fields=("map_id",),
        movement_policies=("all",),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
    )
    seed1 = dict(seed0, posterior_seed=1)

    result = module.summarize_prototype_runs(
        (seed0, seed1),
        posterior_trials=64,
        component_move_cells=True,
        min_watch_support_rows=2,
        min_watch_support_sessions=2,
    )

    assert seed0["status"] == "sample_limited"
    assert result["status"] == "sample_limited"
    assert result["component_statuses"][0]["status"] == "sample_limited"


def test_shadow_sampler_prototype_blocks_stable_low_support() -> None:
    module = _load_module()
    seed0 = module.summarize_seed_run(
        _watch_rows(),
        posterior_seed=0,
        components=("q6_count",),
        group_fields=("map_id",),
        movement_policies=("all",),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
    )

    result = module.summarize_prototype_runs(
        (seed0, dict(seed0, posterior_seed=1)),
        posterior_trials=64,
        component_move_cells=True,
        min_watch_support_rows=20,
        min_watch_support_sessions=2,
    )

    assert result["status"] == "blocked_low_support"
    component = result["component_statuses"][0]
    assert component["status"] == "blocked_low_support"
    assert component["support_gate"]["status"] == "blocked_low_support"
    assert component["support_gate"]["min_support_rows"] == 20
    assert [
        (
            row["posterior_seed"],
            row["watch_label"],
            row["support_rows"],
            row["support_sessions"],
            row["support_fail_reasons"],
        )
        for row in component["support_gate"]["stable_low_support_watch_metrics"]
    ] == [
        (0, "q6_count|map_id|all:q6_count:2502", 8, 8, ["rows"]),
        (1, "q6_count|map_id|all:q6_count:2502", 8, 8, ["rows"]),
    ]


def test_shadow_sampler_prototype_reports_applied_hurt_metrics() -> None:
    module = _load_module()
    fold0 = [_session_for_fold(0, prefix=f"hurt_{idx}") for idx in range(4)]
    fold1 = [_session_for_fold(1, prefix=f"help_{idx}") for idx in range(4)]
    rows = [
        *[
            _row(session_id=session_id, truth=1, baseline=2, ccvc=3)
            for session_id in fold0
        ],
        *[
            _row(session_id=session_id, truth=4, baseline=2, ccvc=3)
            for session_id in fold1
        ],
    ]
    seed0 = module.summarize_seed_run(
        rows,
        posterior_seed=0,
        components=("q6_count",),
        group_fields=("map_id",),
        movement_policies=("all",),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
    )

    result = module.summarize_prototype_runs(
        (seed0,),
        posterior_trials=64,
        component_move_cells=True,
        min_watch_support_rows=2,
        min_watch_support_sessions=2,
    )

    assert result["status"] == "blocked_holdout_hurt"
    component = result["component_statuses"][0]
    assert component["status"] == "blocked_holdout_hurt"
    assert component["applied_hurts"] == [
        "q6_count|map_id|all:q6_count:2502"
    ]
    assert [
        (
            row["posterior_seed"],
            row["watch_label"],
            row["candidate_rows"],
            row["candidate_sessions"],
            row["candidate_hurt_rate"],
        )
        for row in component["top_applied_hurt_metrics"]
    ] == [
        (0, "q6_count|map_id|all:q6_count:2502", 4, 4, 1.0),
    ]
    assert component["applied_hurt_metrics_by_seed"] == [
        {
            "posterior_seed": 0,
            "applied_hurt_metrics": [
                {
                    "watch_label": "q6_count|map_id|all:q6_count:2502",
                    "matrix_label": "q6_count|map_id|all",
                    "component": "q6_count",
                    "group_field": "map_id",
                    "movement_policy": "all",
                    "label": "q6_count:2502",
                    "group": "2502",
                    "rows": 4,
                    "sessions": 4,
                    "candidate_rows": 4,
                    "candidate_sessions": 4,
                    "candidate_delta_p50_mae": 1,
                    "candidate_hurt_rate": 1.0,
                    "candidate_hurt_rows": 4,
                    "candidate_helped_rows": 0,
                    "candidate_directional_error_rate": 1.0,
                    "candidate_directional_error_rows": 4,
                    "candidate_baseline_below_rate": 0.0,
                    "candidate_below_rate": 0.0,
                }
            ],
        }
    ]
    guard_contract = result["guard_trial_contract"]
    assert guard_contract["status"] == "shadow_guard_trial_design"
    assert guard_contract["shadow_only"] is True
    assert guard_contract["affects_bid"] is False
    assert guard_contract["action_counts"] == {"guard_hurt_groups": 1}
    assert guard_contract["component_actions"][0]["trial_action"] == (
        "guard_hurt_groups"
    )
    assert guard_contract["component_actions"][0]["candidate_exclude_labels"] == [
        "q6_count:2502"
    ]


def test_shadow_sampler_prototype_guard_trial_contract_classifies_actions() -> None:
    module = _load_module()

    contract = module._guard_trial_contract(
        [
            {
                "component": "q6_cells",
                "status": "blocked_holdout_hurt",
                "support_gate": {"status": "no_watch"},
                "top_applied_hurt_metrics": [
                    {
                        "watch_label": (
                            "q6_cells|map_id|all:q6_cells:2408"
                        ),
                        "label": "q6_cells:2408",
                        "candidate_rows": 14,
                        "candidate_sessions": 4,
                        "candidate_hurt_rate": 0.928571,
                    }
                ],
            },
            {
                "component": "q6_value",
                "status": "blocked_holdout_hurt",
                "support_gate": {"status": "no_watch"},
                "top_applied_hurt_metrics": [
                    {
                        "watch_label": (
                            "q6_value|map_id,evidence_profile_key|all:"
                            "q6_value:map_id=2508|"
                            "evidence_profile_key=item+shape"
                        ),
                        "label": (
                            "q6_value:map_id=2508|"
                            "evidence_profile_key=item+shape"
                        ),
                        "candidate_rows": 4,
                        "candidate_sessions": 1,
                        "candidate_hurt_rate": 1.0,
                    }
                ],
            },
            {
                "component": "q6_count",
                "status": "blocked_seed_instability",
                "support_gate": {
                    "status": "watch_low_support",
                    "low_support_watch_metrics": [
                        {
                            "watch_label": (
                                "q6_count|map_id,evidence_profile_key|"
                                "down_only:q6_count:map_id=2501|"
                                "evidence_profile_key="
                                "tool:category+item+shape"
                            ),
                            "posterior_seed": 1,
                            "support_rows": 8,
                            "support_sessions": 3,
                        }
                    ],
                },
            },
        ]
    )

    assert contract["status"] == "shadow_guard_trial_design"
    assert contract["can_promote"] is False
    assert contract["requires_source_parser"] is True
    assert contract["can_run_shadow_trial_components"] == [
        "q6_cells",
        "q6_value",
    ]
    assert contract["action_counts"] == {
        "freeze_component": 1,
        "guard_hurt_groups_keep_component_inactive": 1,
        "require_source_support_gate": 1,
    }
    actions = {
        row["component"]: row for row in contract["component_actions"]
    }
    assert actions["q6_cells"]["trial_action"] == "freeze_component"
    assert actions["q6_cells"]["candidate_exclude_labels"] == [
        "q6_cells:2408"
    ]
    assert actions["q6_value"]["trial_action"] == (
        "guard_hurt_groups_keep_component_inactive"
    )
    assert actions["q6_count"]["trial_action"] == (
        "require_source_support_gate"
    )
    assert actions["q6_count"]["requires_source_parser"] is True
    assert actions["q6_count"]["low_support_watch_metrics"][0][
        "support_rows"
    ] == 8
