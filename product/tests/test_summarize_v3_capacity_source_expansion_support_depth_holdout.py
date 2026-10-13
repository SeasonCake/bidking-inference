import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_capacity_source_expansion_support_depth_holdout.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_capacity_source_expansion_support_depth_holdout",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _session_for_fold(module, prefix: str, fold: int, *, folds: int = 2) -> str:
    for index in range(1000):
        session = f"{prefix}_{index}"
        if module._stable_fold(session, folds) == fold:
            return session
    raise AssertionError(f"could not find session for fold {fold}")


def _row(
    module,
    file: str,
    *,
    session_id: str,
    map_id: int,
    map_family: str = "shipwreck",
    excess: int = 0,
    context: str = "payload_verified_partial_action_only",
) -> dict[str, object]:
    return {
        "status": "ok",
        "file": file,
        "session_id": session_id,
        "map_id": map_id,
        "map_family": map_family,
        "mechanism_class": "session_capacity_source_semantics",
        "source_context_class": context,
        "source_evidence_class": "settlement_payload_verified_only",
        "unique_round_cap_excess_after_temp_zodiac_count": excess,
        "non_zodiac_missing_from_drop_universe_count": 0,
    }


def test_support_depth_policy_uses_fallback_only_when_support_threshold_passes() -> None:
    module = _load_module()
    fold0 = _session_for_fold(module, "fold0", 0)
    fold1a = _session_for_fold(module, "fold1a", 1)
    fold1b = _session_for_fold(module, "fold1b", 1)
    rows = [
        _row(
            module,
            "target_truth.json",
            session_id=fold0,
            map_id=2509,
            excess=7,
            context="payload_verified_empty_action_results",
        ),
        _row(module, "family_source_a.json", session_id=fold1a, map_id=2501, excess=2),
        _row(module, "family_source_b.json", session_id=fold1b, map_id=2502, excess=1),
        _row(module, "target_fold_within.json", session_id=fold0, map_id=2504, excess=0),
        _row(module, "family_within.json", session_id=fold1a, map_id=2503, excess=0),
    ]

    blocked = module.evaluate_support_depth_policy(
        rows,
        primary_group_by="map_id",
        fallback_group_by="map_family",
        source_filter="all",
        fallback_source_filter="all",
        min_train_source_rows=3,
        min_train_sessions=1,
        folds=2,
    )
    assert blocked["candidate_rows"] == 0
    assert blocked["covered_unique_round_rows"] == 0
    assert blocked["missed_unique_round_rows"] == 3
    assert blocked["missed_examples"][0]["fallback_train_source_rows"] == 2

    covered = module.evaluate_support_depth_policy(
        rows,
        primary_group_by="map_id",
        fallback_group_by="map_family",
        source_filter="all",
        fallback_source_filter="all",
        min_train_source_rows=2,
        min_train_sessions=1,
        folds=2,
    )
    assert covered["candidate_source_counts"] == {"fallback": 2}
    assert covered["candidate_rows"] == 2
    assert covered["covered_unique_round_rows"] == 1
    assert covered["false_positive_candidate_rows"] == 1
    assert covered["candidate_precision"] == 0.5


def test_support_depth_external_filter_does_not_count_payload_sources() -> None:
    module = _load_module()
    fold0 = _session_for_fold(module, "fold0_ext", 0)
    fold1 = _session_for_fold(module, "fold1_ext", 1)
    rows = [
        _row(module, "target.json", session_id=fold0, map_id=2501, excess=4),
        _row(module, "payload_source.json", session_id=fold1, map_id=2501, excess=1),
        _row(
            module,
            "external_source.json",
            session_id=fold1,
            map_id=2501,
            excess=2,
            context="public_total_confirmed",
        ),
    ]

    result = module.evaluate_support_depth_policy(
        rows,
        primary_group_by="map_id",
        fallback_group_by=None,
        source_filter="external",
        min_train_source_rows=1,
        min_train_sessions=1,
        folds=2,
    )

    assert result["candidate_rows"] == 1
    assert result["covered_unique_round_rows"] == 1
    assert result["primary_train_source_rows"]["max"] == 1
