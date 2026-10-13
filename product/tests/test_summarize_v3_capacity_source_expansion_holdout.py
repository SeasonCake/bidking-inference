import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_capacity_source_expansion_holdout.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_capacity_source_expansion_holdout",
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
    map_id: int = 2506,
    map_family: str = "shipwreck",
    bidmap_sub_pool_kind: str = "leaf",
    unique_round_excess: int = 0,
    mechanism_class: str = "not_unique_round_cap_blocker",
    source_evidence_class: str = "settlement_payload_verified_only",
    source_context_class: str = "payload_verified_partial_action_only",
) -> dict[str, object]:
    return {
        "status": "ok",
        "file": f"{session_id}.json",
        "session_id": session_id,
        "session_token_prefix6": session_id,
        "map_id": map_id,
        "map_family": map_family,
        "bidmap_sub_pool_kind": bidmap_sub_pool_kind,
        "unique_round_cap_excess_after_temp_zodiac_count": unique_round_excess,
        "non_zodiac_missing_from_drop_universe_count": 0,
        "raw_candidate_inventory_delta": 0,
        "occupied_slot_inventory_delta": 0,
        "mechanism_class": mechanism_class,
        "source_evidence_class": source_evidence_class,
        "source_context_class": source_context_class,
    }


def test_capacity_source_expansion_holdout_covers_source_semantics() -> None:
    module = _load_module()
    fold0 = _session_for_fold(module, 0, prefix="cse_cover_0")
    fold1 = _session_for_fold(module, 1, prefix="cse_cover_1")
    rows = [
        _row(
            session_id=fold0,
            unique_round_excess=6,
            mechanism_class="session_capacity_source_semantics",
        ),
        _row(
            session_id=fold1,
            unique_round_excess=4,
            mechanism_class="server_side_settlement_expansion",
            source_evidence_class="public_total_matches_inventory",
            source_context_class="public_total_confirmed",
        ),
        _row(session_id=fold0 + "_normal"),
    ]

    result = module.summarize_holdout(
        rows=rows,
        group_by="map_family",
        folds=2,
        min_train_sessions=1,
    )

    assert result["truth_unique_round_rows"] == 2
    assert result["covered_unique_round_rows"] == 2
    assert result["false_positive_candidate_rows"] == 1
    assert result["unique_round_recall"] == 1.0
    assert result["candidate_precision"] == 0.666667
    assert result["status_counts"] == {"watch_capacity_source_expansion_holdout": 1}
    assert result["truth_source_context_classes"] == {
        "payload_verified_partial_action_only": 1,
        "public_total_confirmed": 1,
    }
    row = result["rows"][0]
    assert row["group"] == "shipwreck"
    assert row["truth_mechanism_classes"] == {
        "server_side_settlement_expansion": 1,
        "session_capacity_source_semantics": 1,
    }


def test_capacity_source_expansion_holdout_blocks_low_sample() -> None:
    module = _load_module()
    fold0 = _session_for_fold(module, 0, prefix="cse_low_0")
    fold1 = _session_for_fold(module, 1, prefix="cse_low_1")
    rows = [
        _row(
            session_id=fold0,
            unique_round_excess=6,
            mechanism_class="session_capacity_source_semantics",
        ),
        _row(
            session_id=fold1,
            unique_round_excess=4,
            mechanism_class="server_side_settlement_expansion",
        ),
    ]

    result = module.summarize_holdout(
        rows=rows,
        group_by="map_family",
        folds=2,
        min_train_sessions=2,
    )

    assert result["candidate_rows"] == 0
    assert result["covered_unique_round_rows"] == 0
    assert result["sample_limited_rows"] == 2
    assert result["status_counts"] == {"blocked_low_sample": 1}


def test_capacity_source_expansion_holdout_can_use_fallback_group() -> None:
    module = _load_module()
    fold0 = _session_for_fold(module, 0, prefix="cse_fallback_0")
    fold1 = _session_for_fold(module, 1, prefix="cse_fallback_1")
    fold1_normal = _session_for_fold(module, 1, prefix="cse_fallback_normal")
    rows = [
        _row(
            session_id=fold0,
            map_id=2408,
            map_family="villa",
            unique_round_excess=2,
            mechanism_class="session_capacity_source_semantics",
        ),
        _row(
            session_id=fold1,
            map_id=2410,
            map_family="villa",
            unique_round_excess=3,
            mechanism_class="session_capacity_source_semantics",
        ),
        _row(
            session_id=fold1_normal,
            map_id=2408,
            map_family="villa",
        ),
    ]

    result = module.summarize_holdout(
        rows=rows,
        group_by="map_id",
        fallback_group_by="map_family_sub_pool_kind",
        folds=2,
        min_train_sessions=1,
    )

    assert result["truth_unique_round_rows"] == 2
    assert result["covered_unique_round_rows"] == 2
    assert result["false_positive_candidate_rows"] == 1
    assert result["unique_round_recall"] == 1.0
    assert result["candidate_precision"] == 0.666667
    assert result["candidate_source_counts"] == {"fallback": 2, "primary": 1}
    assert result["missed_examples"] == []
