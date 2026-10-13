import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_ccv_direction_holdout.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_ccv_direction_holdout",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _session_for_fold(module, fold: int, *, prefix: str) -> str:
    for idx in range(1000):
        session_id = f"{prefix}_{idx}"
        if module._stable_fold(session_id, 2) == fold:
            return session_id
    raise AssertionError(f"no session for fold {fold}")


def _row(
    *,
    session_id: str,
    truth: int,
    baseline: int,
    ccv: int,
    group: str = "2502",
) -> dict[str, object]:
    return {
        "file": f"{session_id}#r1",
        "status": "ready",
        "session_id": session_id,
        "map_id": group,
        "v3_truth_available": True,
        "v3_post_ready": True,
        "v3_ccv_ready": True,
        "v3_ccv_match_scope": "ccv_likelihood",
        "v3_post_q6_count_p50": baseline,
        "v3_ccv_q6_count_p50": ccv,
        "v3_truth_q6_count": truth,
    }


def _ccvc_row(
    *,
    session_id: str,
    truth: int,
    baseline: int,
    ccvc: int,
    group: str = "2502",
) -> dict[str, object]:
    row = _row(
        session_id=session_id,
        truth=truth,
        baseline=baseline,
        ccv=baseline,
        group=group,
    )
    row.update(
        {
            "v3_ccv_ready": False,
            "v3_ccvc_ready": True,
            "v3_ccvc_match_scope": "ccv_component_likelihood",
            "v3_ccvc_q6_count_p50": ccvc,
        }
    )
    return row


def test_direction_holdout_applies_training_direction_candidate() -> None:
    module = _load_module()
    fold0 = [_session_for_fold(module, 0, prefix=f"f0_{idx}") for idx in range(4)]
    fold1 = [_session_for_fold(module, 1, prefix=f"f1_{idx}") for idx in range(4)]
    rows = [
        _row(session_id=session_id, truth=4, baseline=2, ccv=3)
        for session_id in (*fold0, *fold1)
    ]

    result = module.summarize_holdout(
        rows,
        group_field="map_id",
        components=("q6_count",),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
    )

    assert result["overall_status"] == "watch"
    assert result["candidate_only"]["candidate_rows"] == 8
    assert result["candidate_only"]["candidate_groups"] == ["q6_count:2502"]
    assert result["candidate_only"]["candidate_only_delta_p50_mae"] == -1
    assert result["candidate_only"]["candidate_only_hurt_rate"] == 0.0


def test_direction_holdout_uses_candidate_prefix() -> None:
    module = _load_module()
    fold0 = [_session_for_fold(module, 0, prefix=f"ccvc0_{idx}") for idx in range(4)]
    fold1 = [_session_for_fold(module, 1, prefix=f"ccvc1_{idx}") for idx in range(4)]
    rows = [
        _ccvc_row(session_id=session_id, truth=4, baseline=2, ccvc=3)
        for session_id in (*fold0, *fold1)
    ]

    result = module.summarize_holdout(
        rows,
        group_field="map_id",
        candidate_prefix="v3_ccvc_",
        components=("q6_count",),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
    )

    assert result["candidate_prefix"] == "v3_ccvc_"
    assert result["overall_status"] == "watch"
    assert result["candidate_only"]["candidate_rows"] == 8
    assert result["candidate_only"]["candidate_only_delta_p50_mae"] == -1


def test_direction_holdout_blocks_train_candidate_that_hurts_holdout() -> None:
    module = _load_module()
    fold0 = [_session_for_fold(module, 0, prefix=f"hurt_{idx}") for idx in range(4)]
    fold1 = [_session_for_fold(module, 1, prefix=f"help_{idx}") for idx in range(4)]
    rows = [
        *[
            _row(session_id=session_id, truth=4, baseline=2, ccv=1)
            for session_id in fold0
        ],
        *[
            _row(session_id=session_id, truth=4, baseline=2, ccv=3)
            for session_id in fold1
        ],
    ]

    result = module.summarize_holdout(
        rows,
        group_field="map_id",
        components=("q6_count",),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
    )

    assert result["overall_status"] == "blocked_holdout_directional_hurt"
    assert result["candidate_only"]["candidate_rows"] == 4
    assert result["candidate_only"]["candidate_only_delta_p50_mae"] == 1
    assert result["candidate_only"]["candidate_only_hurt_rate"] == 1.0
    assert result["candidate_only"]["candidate_only_directional_error_rate"] == 1.0


def test_direction_holdout_can_apply_up_only_movement_policy() -> None:
    module = _load_module()
    fold0 = [_session_for_fold(module, 0, prefix=f"hurt_{idx}") for idx in range(4)]
    fold1 = [_session_for_fold(module, 1, prefix=f"help_{idx}") for idx in range(4)]
    rows = [
        *[
            _row(session_id=session_id, truth=4, baseline=2, ccv=1)
            for session_id in fold0
        ],
        *[
            _row(session_id=session_id, truth=4, baseline=2, ccv=3)
            for session_id in fold1
        ],
    ]

    result = module.summarize_holdout(
        rows,
        group_field="map_id",
        components=("q6_count",),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
        movement_policy="up_only",
    )

    assert result["movement_policy"] == "up_only"
    assert result["overall_status"] == "watch"
    assert result["candidate_only"]["candidate_rows"] == 4
    assert result["candidate_only"]["candidate_only_delta_p50_mae"] == 0
    assert result["candidate_only"]["candidate_only_hurt_rate"] == 0.0
    assert result["candidate_only"]["candidate_only_directional_error_rate"] == 0.0


def test_direction_holdout_can_exclude_candidate_groups_by_pattern() -> None:
    module = _load_module()
    fold0 = [_session_for_fold(module, 0, prefix=f"hurt_{idx}") for idx in range(4)]
    fold1 = [_session_for_fold(module, 1, prefix=f"help_{idx}") for idx in range(4)]
    rows = [
        *[
            _row(
                session_id=session_id,
                truth=4,
                baseline=2,
                ccv=1,
                group="2503",
            )
            for session_id in fold0
        ],
        *[
            _row(
                session_id=session_id,
                truth=4,
                baseline=2,
                ccv=3,
                group="2502",
            )
            for session_id in fold1
        ],
    ]

    result = module.summarize_holdout(
        rows,
        group_field="map_id",
        components=("q6_count",),
        folds=2,
        min_windows=2,
        min_sessions=2,
        min_changed=2,
        candidate_exclude_pattern="^q6_count:2502$",
    )

    assert result["candidate_exclude_pattern"] == "^q6_count:2502$"
    assert result["candidate_only"]["candidate_rows"] == 0
    assert result["overall_status"] == "sample_limited"
