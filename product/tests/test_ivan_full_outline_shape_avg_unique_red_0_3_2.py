from __future__ import annotations

import struct
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("src", "scripts", "hero_ref/src", "hero_ref/tools"):
    path = str(ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

import ahmad_ref_engine as engine  # noqa: E402
from ahmad_live_panel_server import summarize_snapshot  # noqa: E402
from ahmad_tk_overlay import _summary_diagnostic_row  # noqa: E402


ROLLBACK = "BIDKING_DISABLE_IVAN_FULL_OUTLINE_SHAPE_AVG_RED_IDENTITY"
NOTE = "ivan_full_outline_shape_avg_unique_red:known_items:1"
SESSION = "5611:9000000000000046"
RED_ITEM_ID = 1_106_004
RED_VALUE = 191_800
ALT_RED_ITEM_ID = RED_ITEM_ID + 1
ALT_RED_VALUE = RED_VALUE + 4


def _float32(value: float) -> tuple[float, str]:
    raw = struct.pack("<f", float(value))
    return struct.unpack("<f", raw)[0], raw.hex()


def _outline_item(runtime_id: int, shape_code: int) -> dict:
    return {
        "runtime_id": runtime_id,
        "local_index": runtime_id,
        "shape_code": shape_code,
        "cells": None,
        "quality": None,
        "value": None,
        "item_id": None,
    }


def _snapshot(
    *,
    hero: str = "ivan",
    phase: str = "bidding",
    session_id: str = SESSION,
    avg_session_id: str | None = None,
    outline_items: list[dict] | None = None,
    avg_value: float = RED_VALUE,
) -> dict:
    parsed, raw_hex = _float32(avg_value)
    rows = [
        {
            "action_id": 100100,
            "session_id": session_id,
            "sort": 19,
            "round": 2,
            "revealed_items_detail": outline_items
            or [
                _outline_item(101, 23),
                _outline_item(102, 11),
            ],
        },
        {
            "action_id": 100173,
            "session_id": avg_session_id or session_id,
            "sort": 3,
            "result": parsed,
            "result_field": 9,
            "result_wire_type": "float32",
            "result_raw_hex": raw_hex,
        },
    ]
    return {
        "session_id": session_id,
        "hero": hero,
        "hero_id": 205 if hero == "ivan" else 204,
        "phase": phase,
        "round": 3,
        "map_id": 5611,
        "action_result_rows": rows,
        "ui_contract": {
            "context": {
                "session_id": session_id,
                "hero": hero,
                "hero_id": 205 if hero == "ivan" else 204,
                "phase": phase,
                "round": 3,
                "map_id": 5611,
            },
            "actions": {"results": rows},
        },
    }


def _catalog_unique_red(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine,
        "_catalog_items_by_value",
        lambda: {RED_VALUE: ((6, 6, RED_ITEM_ID),)},
    )
    monkeypatch.setattr(
        engine,
        "_validated_q6_outline_catalog",
        lambda: (((RED_ITEM_ID, RED_VALUE, 2, 3),), "ok"),
    )


def _avg_row(
    value: float = RED_VALUE,
    *,
    session_id: str = SESSION,
    raw_hex: str | None = None,
    action_id: int = 100173,
) -> dict:
    parsed, encoded = _float32(value)
    return {
        "action_id": action_id,
        "session_id": session_id,
        "sort": 3,
        "result": parsed,
        "result_field": 9,
        "result_wire_type": "float32",
        "result_raw_hex": encoded if raw_hex is None else raw_hex,
    }


def _facts(snapshot: dict) -> tuple[tuple[str, int, int], ...]:
    return engine._ivan_full_outline_shape_avg_unique_red_facts(
        snapshot,
        hero=snapshot["hero"],
        phase=snapshot["phase"],
        current_session=snapshot["session_id"],
    )


def test_sanitized_5611_bidding_replay_adds_traceable_red_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_unique_red(monkeypatch)
    snapshot = _snapshot()

    assert _facts(snapshot) == (("runtime:101", 6, RED_VALUE),)
    evidence = engine.extract_evidence(snapshot)
    assert NOTE in evidence.source_notes
    assert evidence.q6_known_item_facts == (("runtime:101", 6, RED_VALUE),)
    assert evidence.quality_cell_floors["q6"] >= 6
    assert evidence.quality_value_floors["q6"] >= RED_VALUE
    assert evidence.quality_value_floor_item_counts["q6"] >= 1
    product = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()
    assert product["evidence"]["q6_known_item_facts"] == [
        {"identity": "runtime:101", "cells": 6, "value": RED_VALUE}
    ]


def test_rollback_is_byte_stable_with_the_noop_source_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_unique_red(monkeypatch)
    snapshot = _snapshot()
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = engine.extract_evidence(snapshot)

    monkeypatch.delenv(ROLLBACK)
    monkeypatch.setattr(
        engine,
        "_ivan_full_outline_shape_avg_unique_red_facts",
        lambda *_args, **_kwargs: (),
    )
    no_source = engine.extract_evidence(snapshot)
    assert rollback == no_source


@pytest.mark.parametrize("hero", ["ahmed", "aisha", "ethan", "unknown"])
def test_other_heroes_are_byte_stable_noops(
    monkeypatch: pytest.MonkeyPatch,
    hero: str,
) -> None:
    _catalog_unique_red(monkeypatch)
    snapshot = _snapshot(hero=hero)
    fixed = asdict(engine.extract_evidence(snapshot))
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = asdict(engine.extract_evidence(snapshot))
    assert fixed == rollback


def test_settled_phase_never_backfills_from_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_unique_red(monkeypatch)
    assert _facts(_snapshot(phase="settled")) == ()


def test_two_items_in_the_same_footprint_bucket_remain_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_unique_red(monkeypatch)
    snapshot = _snapshot(
        outline_items=[
            _outline_item(101, 23),
            _outline_item(102, 32),
            _outline_item(103, 11),
        ]
    )
    assert _facts(snapshot) == ()


def test_same_shape_value_multiple_catalog_ids_remain_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine,
        "_catalog_items_by_value",
        lambda: {RED_VALUE: ((6, 6, RED_ITEM_ID), (6, 6, RED_ITEM_ID + 1))},
    )
    monkeypatch.setattr(
        engine,
        "_validated_q6_outline_catalog",
        lambda: (
            (
                (RED_ITEM_ID, RED_VALUE, 2, 3),
                (RED_ITEM_ID + 1, RED_VALUE, 2, 3),
            ),
            "ok",
        ),
    )
    assert _facts(_snapshot()) == ()


@pytest.mark.parametrize(
    "catalog_status",
    ["catalog_unavailable", "catalog_duplicate_item_id", "catalog_mismatch"],
)
def test_incomplete_or_conflicting_catalog_fails_open(
    monkeypatch: pytest.MonkeyPatch,
    catalog_status: str,
) -> None:
    monkeypatch.setattr(
        engine,
        "_catalog_items_by_value",
        lambda: {RED_VALUE: ((6, 6, RED_ITEM_ID),)},
    )
    monkeypatch.setattr(
        engine,
        "_validated_q6_outline_catalog",
        lambda: ((), catalog_status),
    )
    assert _facts(_snapshot()) == ()


def test_unique_nonred_catalog_item_does_not_become_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine,
        "_catalog_items_by_value",
        lambda: {RED_VALUE: ((5, 6, 1_105_001),)},
    )
    monkeypatch.setattr(
        engine,
        "_validated_q6_outline_catalog",
        lambda: (((RED_ITEM_ID, RED_VALUE, 2, 3),), "ok"),
    )
    assert _facts(_snapshot()) == ()


def test_stale_session_and_malformed_float32_rows_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_unique_red(monkeypatch)
    assert _facts(_snapshot(avg_session_id="5611:9000000000000047")) == ()

    malformed = _snapshot()
    malformed["action_result_rows"][1]["result_raw_hex"] = "00000000"
    assert _facts(malformed) == ()


@pytest.mark.parametrize(
    "malformed_first",
    [False, True],
    ids=["valid-then-bad", "bad-then-valid"],
)
def test_valid_and_malformed_duplicate_atomically_invalidate_bucket(
    monkeypatch: pytest.MonkeyPatch,
    malformed_first: bool,
) -> None:
    _catalog_unique_red(monkeypatch)
    snapshot = _snapshot()
    valid = snapshot["action_result_rows"][1]
    malformed = _avg_row(raw_hex="00000000")
    snapshot["action_result_rows"] = (
        [snapshot["action_result_rows"][0], malformed, valid]
        if malformed_first
        else [snapshot["action_result_rows"][0], valid, malformed]
    )
    assert _facts(snapshot) == ()


def test_duplicate_valid_same_value_is_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_unique_red(monkeypatch)
    snapshot = _snapshot()
    snapshot["action_result_rows"].append(_avg_row())
    assert _facts(snapshot) == (("runtime:101", 6, RED_VALUE),)


def test_duplicate_valid_different_values_atomically_invalidate_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine,
        "_catalog_items_by_value",
        lambda: {
            RED_VALUE: ((6, 6, RED_ITEM_ID),),
            ALT_RED_VALUE: ((6, 6, ALT_RED_ITEM_ID),),
        },
    )
    monkeypatch.setattr(
        engine,
        "_validated_q6_outline_catalog",
        lambda: (
            (
                (RED_ITEM_ID, RED_VALUE, 2, 3),
                (ALT_RED_ITEM_ID, ALT_RED_VALUE, 2, 3),
            ),
            "ok",
        ),
    )
    snapshot = _snapshot()
    snapshot["action_result_rows"].append(_avg_row(ALT_RED_VALUE))
    assert _facts(snapshot) == ()


def test_cross_session_malformed_duplicate_does_not_change_current_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_unique_red(monkeypatch)
    snapshot = _snapshot()
    snapshot["action_result_rows"].append(
        _avg_row(session_id="5611:9000000000000047", raw_hex="00000000")
    )
    assert _facts(snapshot) == (("runtime:101", 6, RED_VALUE),)


def test_unrelated_malformed_action_does_not_change_current_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_unique_red(monkeypatch)
    snapshot = _snapshot()
    snapshot["action_result_rows"].append(
        _avg_row(action_id=999999, raw_hex="00000000")
    )
    assert _facts(snapshot) == (("runtime:101", 6, RED_VALUE),)


def test_conflicting_full_outline_epochs_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_unique_red(monkeypatch)
    snapshot = _snapshot()
    snapshot["action_result_rows"].append(
        {
            "action_id": 100100,
            "session_id": SESSION,
            "sort": 20,
            "round": 2,
            "revealed_items_detail": [
                _outline_item(101, 22),
                _outline_item(102, 11),
            ],
        }
    )
    assert _facts(snapshot) == ()


def test_existing_public_red_same_runtime_is_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_unique_red(monkeypatch)
    monkeypatch.setattr(
        engine,
        "_correlated_q6_known_item_facts",
        lambda _snapshot: (("runtime:101", 6, RED_VALUE),),
    )
    evidence = engine.extract_evidence(_snapshot())
    assert evidence.q6_known_item_facts == (("runtime:101", 6, RED_VALUE),)
    assert NOTE not in evidence.source_notes


@pytest.mark.parametrize(
    "snapshot",
    [
        pytest.param(_snapshot(hero="ahmed"), id="non-ivan"),
        pytest.param(
            _snapshot(
                outline_items=[
                    _outline_item(101, 23),
                    _outline_item(102, 32),
                    _outline_item(103, 11),
                ]
            ),
            id="non-unique-footprint-bucket",
        ),
    ],
)
def test_noneligible_product_result_is_byte_stable(
    monkeypatch: pytest.MonkeyPatch,
    snapshot: dict,
) -> None:
    _catalog_unique_red(monkeypatch)
    fixed = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()
    assert fixed == rollback


def test_summary_api_and_tk_consume_the_same_source_fact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _catalog_unique_red(monkeypatch)
    snapshot = _snapshot()
    snapshot_path = tmp_path / "sanitized-ivan-5611.json"

    fixed = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    fixed_tk = _summary_diagnostic_row(
        fixed,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    rollback_tk = _summary_diagnostic_row(
        rollback,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )

    assert fixed["ahmed_ref"]["evidence"]["q6_known_item_facts"] == [
        {"identity": "runtime:101", "cells": 6, "value": RED_VALUE}
    ]
    assert rollback["ahmed_ref"]["evidence"]["q6_known_item_facts"] == []
    assert NOTE in fixed["ahmed_ref"]["notes"]
    assert NOTE in fixed_tk["ref_v0"]["notes"]
    assert NOTE not in rollback["ahmed_ref"]["notes"]
    assert NOTE not in rollback_tk["ref_v0"]["notes"]
    for key in ("conservative", "balanced", "aggressive", "decision_range"):
        assert fixed_tk["reference"][key] == fixed["reference"][key]
        assert rollback_tk["reference"][key] == rollback["reference"][key]
    assert fixed["reference"]["balanced"] != rollback["reference"]["balanced"]
