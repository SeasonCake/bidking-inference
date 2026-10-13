import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_capacity_source_expansion_source_key_holdout.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_capacity_source_expansion_source_key_holdout",
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
    *,
    file: str,
    session_id: str,
    map_id: int,
    map_family: str = "shipwreck",
    excess: int = 0,
) -> dict[str, object]:
    return {
        "status": "ok",
        "file": file,
        "session_id": session_id,
        "map_id": map_id,
        "map_family": map_family,
        "mechanism_class": "session_capacity_source_semantics",
        "source_context_class": "payload_verified_partial_action_only",
        "unique_round_cap_excess_after_temp_zodiac_count": excess,
        "non_zodiac_missing_from_drop_universe_count": 0,
    }


def test_source_key_holdout_compares_shape_and_map_id_candidates() -> None:
    module = _load_module()
    fold0 = _session_for_fold(module, "fold0", 0)
    fold1 = _session_for_fold(module, "fold1", 1)
    rows = [
        _row(file="truth_a.json", session_id=fold0, map_id=2501, excess=3),
        _row(file="truth_b.json", session_id=fold1, map_id=2502, excess=2),
        _row(file="within_item.json", session_id=fold1, map_id=2503, excess=0),
        _row(
            file="truth_numeric.json",
            session_id=fold0,
            map_id=2410,
            map_family="villa",
            excess=1,
        ),
        _row(
            file="within_numeric.json",
            session_id=fold1,
            map_id=2411,
            map_family="villa",
            excess=0,
        ),
    ]
    source_shapes_by_file = {
        "truth_a.json": {
            "source_action_payload_shape_class": "item_reveal_payload",
            "source_action_ids": {"100129": 2},
        },
        "truth_b.json": {
            "source_action_payload_shape_class": "item_reveal_payload",
            "source_action_ids": {"100136": 1},
        },
        "within_item.json": {
            "source_action_payload_shape_class": "item_reveal_payload",
            "source_action_ids": {"100128": 1},
        },
        "truth_numeric.json": {
            "source_action_payload_shape_class": "numeric_only_result",
            "source_action_ids": {"100105": 1},
        },
        "within_numeric.json": {
            "source_action_payload_shape_class": "numeric_only_result",
            "source_action_ids": {"100105": 1},
        },
    }

    result = module.summarize_source_key_holdout(
        rows=rows,
        source_shapes_by_file=source_shapes_by_file,
        source_keys=("source_shape", "map_id"),
        folds=2,
        min_train_sessions=1,
    )

    assert result["sessions"] == 5
    assert result["source_shape_counts"] == {
        "item_reveal_payload": 3,
        "numeric_only_result": 2,
    }
    assert result["truth_source_shape_counts"] == {
        "item_reveal_payload": 2,
        "numeric_only_result": 1,
    }

    by_key = {row["source_key"]: row for row in result["rows"]}
    assert by_key["source_shape"]["truth_unique_round_rows"] == 3
    assert by_key["source_shape"]["covered_unique_round_rows"] == 2
    assert by_key["source_shape"]["candidate_rows"] == 4
    assert by_key["source_shape"]["false_positive_candidate_rows"] == 2
    assert by_key["source_shape"]["candidate_precision"] == 0.5

    assert by_key["map_id"]["covered_unique_round_rows"] == 0
    assert by_key["map_id"]["candidate_rows"] == 0
