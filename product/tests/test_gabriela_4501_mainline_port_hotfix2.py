"""Hotfix2 mainline port contract for Gabriela 4501.

The sanitized bidding snapshot comes from session
``4501:9000000000000038``. Settlement values are labels used only in the
directional assertions; they are never passed to the engine.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (
    ROOT / "hero_ref" / "src",
    ROOT / "hero_ref" / "tools",
    ROOT / "src",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_ref_engine as engine  # noqa: E402
import ahmad_live_panel_server as panel  # noqa: E402
import ahmad_tk_overlay as overlay  # noqa: E402
from ahmad_live_panel_server import summarize_snapshot  # noqa: E402


TARGET_Q6_VALUE = 1_378_140
TARGET_FULL_VALUE = 1_638_273
FROZEN_STATIC_DATA_SHA256 = (
    "7543b4aa16addc73dfaaaf3c6df9bc0f6662bd83458de110a16d04ff9f2f9676"
)


def _require_frozen_static_data() -> None:
    """Keep numeric product gates bound to the packaged authoritative input."""

    path = engine.STATIC_DATA
    assert path.is_file(), f"missing frozen StaticData input: {path.name}"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == FROZEN_STATIC_DATA_SHA256


def _snapshot(*, exact_red_cells: int | None = 25) -> dict:
    session_id = "4501:9000000000000038"
    source = {
        "qualification": "postupdate_unknown_warehouse_source",
        "official_distribution_eligible": False,
        "quote_policy": "advisory_only_no_clamp",
    }
    revealed_red = {
        "quality": 6,
        "cells": 1,
        "shape_key": "11",
        "local_index": 59,
        "runtime_id": 1059,
        "row": 1,
        "col": 1,
        "width": 1,
        "height": 1,
        "value": 54_300,
    }
    action_row = {
        "action_id": 100108,
        "result": exact_red_cells,
        "sort": 27,
        "session_id": session_id,
        "round": 3,
        "source_kind": "direct_action_response",
    }
    structured = {
        "source": "monitor_pre_settlement_field_updates",
        "min_counts": {"q3": 1},
        "minimap_items": [copy.deepcopy(revealed_red)],
    }
    if exact_red_cells is not None:
        structured["quality_cells"] = {"q6": exact_red_cells}
    return {
        "created_at": time.time(),
        "hero": "gabriela",
        "hero_id": 104,
        "map_id": 4501,
        "round": 4,
        "phase": "bidding",
        "session_id": session_id,
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": source["qualification"],
        "official_distribution_eligible": False,
        "action_result_rows": [copy.deepcopy(action_row)],
        "skill_reveal_rows": [
            {
                "hero_id": 104,
                "skill_id": 1001044,
                "sort": 28,
                "observed_items": [copy.deepcopy(revealed_red)],
            }
        ],
        "structured_ref_inputs": structured,
        "ui_contract": {
            "context": {
                "hero": "gabriela",
                "hero_id": 104,
                "map_id": 4501,
                "round": 4,
                "phase": "bidding",
                "session_id": session_id,
                "warehouse_source": copy.deepcopy(source),
            },
            "actions": {"results": [copy.deepcopy(action_row)]},
            "constraints": {"public_info": {}},
            "minimap": {
                "status": "available",
                "items": [copy.deepcopy(revealed_red)],
            },
        },
    }


def _verified_snapshot(
    *,
    qualification: str = "postupdate_unknown_warehouse_source",
) -> dict:
    snapshot = _snapshot()
    source = snapshot["warehouse_source"]
    source.update(
        {
            "qualification": qualification,
            "map_id": 4501,
            "table_status": "verified",
            "source_generation": "postupdate_player_warehouse_activity",
            "auction_mode": "open",
            "source_conflict": False,
            "quarantine_required": False,
            "parser_replay_eligible": True,
            "map_table_sha256": "a" * 64,
            "expected_map_table_sha256": "a" * 64,
            "map_source_contract_sha256": "b" * 64,
            "expected_map_source_contract_sha256": "b" * 64,
        }
    )
    snapshot["warehouse_source_qualification"] = qualification
    snapshot["ui_contract"]["context"]["warehouse_source"] = copy.deepcopy(
        source
    )
    return snapshot


def _clear_owner(owner: dict) -> None:
    for key in (
        "warehouse_source",
        "warehouse_source_qualification",
        "official_distribution_eligible",
        "quote_policy",
    ):
        owner.pop(key, None)


def _conditional_enabled(result: dict) -> bool:
    return any(
        note.startswith(engine.GABRIELA_Q6_CONDITIONAL_VALUE_NOTE)
        for note in result["notes"]
    )


def _with_mode(rollback: bool, callback: Callable[[], dict]) -> dict:
    key = engine.GABRIELA_Q6_CONDITIONAL_VALUE_DISABLE
    old = os.environ.get(key)
    try:
        if rollback:
            os.environ[key] = "1"
        else:
            os.environ.pop(key, None)
        return callback()
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def _run(snapshot: dict, *, max_combos: int, rollback: bool) -> dict:
    return _with_mode(
        rollback,
        lambda: engine.run_reference_engine(
            copy.deepcopy(snapshot), max_combos=max_combos
        ).as_dict(),
    )


def _run_uncloned(snapshot: dict, *, max_combos: int, rollback: bool) -> dict:
    """Exercise hostile wire objects without invoking test-side copy protocols."""

    return _with_mode(
        rollback,
        lambda: engine.run_reference_engine(
            snapshot,
            max_combos=max_combos,
        ).as_dict(),
    )


def _run_physical_residual(snapshot: dict, *, rollback: bool) -> dict:
    key = engine.Q6_KNOWN_ITEM_EXACT_CELLS_RESIDUAL_DISABLE
    old = os.environ.get(key)
    try:
        if rollback:
            os.environ[key] = "1"
        else:
            os.environ.pop(key, None)
        return engine.run_reference_engine(
            copy.deepcopy(snapshot),
            max_combos=8_000,
        ).as_dict()
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def _loopback(snapshot: dict, tmp_path: Path) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "latest_snapshot.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        panel.make_handler(panel.ServerConfig(project_root=ROOT, snapshot=path)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        with urllib.request.urlopen(  # noqa: S310 - self-created loopback
            f"http://{host}:{port}/api/latest", timeout=30
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()


def test_exact_red_cells_outrank_partial_reveal_and_close_physical_support(
    tmp_path: Path,
) -> None:
    ref = summarize_snapshot(
        _snapshot(), snapshot_path=tmp_path / "snapshot.json"
    )["ahmed_ref"]

    assert ref["evidence"]["quality_cells"]["q6"] == 25.0
    assert ref["red_cells_range"] == [25, 25, 25]
    assert ref["quality_cells_ranges"]["q6"] == [25, 25, 25]
    assert {(row["count"], row["cells"]) for row in ref[
        "quality_count_cells_candidates"
    ]["q6"]} == {(3, 25), (4, 25)}


def test_physical_residual_rollback_restores_impossible_two_red_arm() -> None:
    _require_frozen_static_data()
    default = _run_physical_residual(_snapshot(), rollback=False)
    rollback = _run_physical_residual(_snapshot(), rollback=True)

    assert default["red_count_range"] == [3, 3, 3]
    assert default["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "partial_reveal_min",
    }
    assert {
        (row["count"], row["cells"])
        for row in default["quality_count_cells_candidates"]["q6"]
    } == {(3, 25), (4, 25)}
    assert rollback["red_count_range"] == [2, 2, 3]
    assert {
        (row["count"], row["cells"])
        for row in rollback["quality_count_cells_candidates"]["q6"]
    } == {(2, 25), (3, 25)}
    assert [
        default["conservative"],
        default["balanced"],
        default["aggressive"],
    ] == [625_748, 707_845, 789_952]
    assert [
        rollback["conservative"],
        rollback["balanced"],
        rollback["aggressive"],
    ] == [418_509, 486_725, 561_374]


def test_exact_cells_without_correlated_item_does_not_invent_residual() -> None:
    snapshot = _snapshot()
    snapshot["skill_reveal_rows"] = []
    snapshot["structured_ref_inputs"]["minimap_items"] = []
    snapshot["ui_contract"]["minimap"]["items"] = []

    default = _run_physical_residual(snapshot, rollback=False)
    rollback = _run_physical_residual(snapshot, rollback=True)
    assert default["evidence"]["q6_known_item_facts"] == []
    assert default == rollback


def test_red_plus_empty_geometry_never_falls_below_exact_red_cells() -> None:
    ref = {
        "total_grid_range": [88, 104, 120],
        "quality_cells_ranges": {
            "q1": [20, 20, 20],
            "q3": [20, 20, 20],
            "q4": [20, 20, 20],
            "q5": [19, 19, 19],
            "q6": [25, 25, 25],
        },
        "evidence": {"quality_cells": {"q6": 25.0}},
    }
    assert overlay._remaining_cells_range(ref) == (25, 25, 41)


@pytest.mark.parametrize(
    (
        "max_combos",
        "default_quotes",
        "rollback_quotes",
        "default_red_values",
        "rollback_red_values",
    ),
    [
        (
            8_000,
            [860_751, 964_625, 1_067_664],
            [625_748, 707_845, 789_952],
            [658_083, 887_510, 1_116_936],
            [574_500, 596_389, 768_881],
        ),
        (
            50_000,
            [880_790, 985_741, 1_089_496],
            [645_022, 728_771, 812_515],
            [658_083, 887_510, 1_116_936],
            [574_500, 596_389, 768_881],
        ),
    ],
)
def test_exact_4501_joint_conditions_count_value_and_quotes(
    max_combos: int,
    default_quotes: list[int],
    rollback_quotes: list[int],
    default_red_values: list[int],
    rollback_red_values: list[int],
) -> None:
    _require_frozen_static_data()
    snapshot = _verified_snapshot()
    default = _run(snapshot, max_combos=max_combos, rollback=False)
    rollback = _run(snapshot, max_combos=max_combos, rollback=True)

    assert default["red_cells_range"] == rollback["red_cells_range"] == [25, 25, 25]
    assert default["quality_lock"]["q6"]["strength"] == "probabilistic"
    assert default["red_value_range"][1] > rollback["red_value_range"][1]
    assert abs(TARGET_Q6_VALUE - default["red_value_range"][1]) < abs(
        TARGET_Q6_VALUE - rollback["red_value_range"][1]
    )
    assert abs(TARGET_FULL_VALUE - default["balanced"]) < abs(
        TARGET_FULL_VALUE - rollback["balanced"]
    )
    assert _conditional_enabled(default)
    assert not _conditional_enabled(rollback)
    assert [
        default["conservative"],
        default["balanced"],
        default["aggressive"],
    ] == default_quotes
    assert [
        rollback["conservative"],
        rollback["balanced"],
        rollback["aggressive"],
    ] == rollback_quotes
    assert default["red_value_range"] == default_red_values
    assert rollback["red_value_range"] == rollback_red_values


def test_prepared_decision_survives_json_clone_and_second_prepare() -> None:
    prepared = engine.prepare_reference_engine_snapshot(_verified_snapshot())
    clone = json.loads(json.dumps(prepared))
    prepared_twice = engine.prepare_reference_engine_snapshot(clone)

    for candidate in (prepared, clone, prepared_twice):
        decision = engine._prepared_warehouse_quote_provenance_decision(
            candidate, map_id=4501
        )
        assert decision is not None
        assert (
            decision.legacy_qualification,
            decision.gabriela_qualification,
        ) == (
            "postupdate_unknown_warehouse_source",
            "postupdate_unknown_warehouse_source",
        )


@pytest.mark.parametrize(
    ("snapshot_factory", "expected"),
    [
        (
            _snapshot,
            ("postupdate_unknown_warehouse_source", None),
        ),
        (
            lambda: _verified_snapshot(
                qualification="player_custom_warehouse"
            ),
            ("player_custom_warehouse", "player_custom_warehouse"),
        ),
    ],
    ids=["legacy-only", "strict-player"],
)
def test_prepared_legal_atomic_states_survive_clone_and_second_prepare(
    snapshot_factory: Callable[[], dict],
    expected: tuple[str | None, str | None],
) -> None:
    prepared = engine.prepare_reference_engine_snapshot(snapshot_factory())
    clone = json.loads(json.dumps(prepared))
    prepared_twice = engine.prepare_reference_engine_snapshot(clone)
    for candidate in (prepared, clone, prepared_twice):
        decision = engine._prepared_warehouse_quote_provenance_decision(
            candidate,
            map_id=4501,
        )
        assert decision is not None
        assert (
            decision.legacy_qualification,
            decision.gabriela_qualification,
        ) == expected


@pytest.mark.parametrize("owner_name", ["root", "source", "context"])
def test_any_complete_owner_conflict_invalidates_the_atomic_decision(
    owner_name: str,
) -> None:
    snapshot = _verified_snapshot()
    source = copy.deepcopy(snapshot["warehouse_source"])
    snapshot["ui_contract"]["source"] = {
        "warehouse_source": copy.deepcopy(source),
    }
    owners = {
        "root": snapshot["warehouse_source"],
        "source": snapshot["ui_contract"]["source"]["warehouse_source"],
        "context": snapshot["ui_contract"]["context"]["warehouse_source"],
    }
    assert engine._atomic_nonofficial_warehouse_quote_provenance(
        snapshot,
        map_id=4501,
    ).gabriela_qualification == "postupdate_unknown_warehouse_source"

    owners[owner_name]["source_conflict"] = True
    assert engine._atomic_nonofficial_warehouse_quote_provenance(
        snapshot,
        map_id=4501,
    ).gabriela_qualification is None


@pytest.mark.parametrize(
    ("legacy", "gabriela"),
    [
        (None, "postupdate_unknown_warehouse_source"),
        ("", None),
        ("official_random_warehouse", None),
        (
            "postupdate_unknown_warehouse_source",
            "player_custom_warehouse",
        ),
        (True, None),
    ],
)
def test_prepared_decision_rejects_illegal_atomic_states(
    legacy: object,
    gabriela: object,
) -> None:
    decision = engine._WarehouseQuoteProvenanceDecision(
        legacy_qualification=legacy,
        gabriela_qualification=gabriela,
    )
    assert engine._warehouse_quote_provenance_decision_auth_payload(decision) is None
    with pytest.raises(ValueError, match="invalid warehouse quote provenance decision"):
        engine._warehouse_quote_provenance_prepared_marker(decision)


def test_real_marker_cannot_authenticate_mutated_decision_across_paths(
    tmp_path: Path,
) -> None:
    def mutated() -> dict:
        prepared = engine.prepare_reference_engine_snapshot(_verified_snapshot())
        prepared[engine._WAREHOUSE_QUOTE_PROVENANCE_PREPARED_DECISION_KEY] = {
            "legacy_qualification": "player_custom_warehouse",
            "gabriela_qualification": "player_custom_warehouse",
        }
        return prepared

    direct_default = _run(mutated(), max_combos=8_000, rollback=False)
    direct_rollback = _run(mutated(), max_combos=8_000, rollback=True)
    summary_default = _with_mode(
        False,
        lambda: summarize_snapshot(
            mutated(), snapshot_path=tmp_path / "summary-default.json"
        ),
    )
    summary_rollback = _with_mode(
        True,
        lambda: summarize_snapshot(
            mutated(), snapshot_path=tmp_path / "summary-rollback.json"
        ),
    )
    api_default = _with_mode(False, lambda: _loopback(mutated(), tmp_path / "api-a"))
    api_rollback = _with_mode(True, lambda: _loopback(mutated(), tmp_path / "api-b"))

    assert direct_default == direct_rollback
    assert summary_default["ahmed_ref"] == summary_rollback["ahmed_ref"]
    assert api_default["ahmed_ref"] == api_rollback["ahmed_ref"]
    assert not _conditional_enabled(direct_default)


def test_intact_marker_decision_pair_cannot_move_between_owner_states() -> None:
    unknown = engine.prepare_reference_engine_snapshot(_verified_snapshot())
    player = engine.prepare_reference_engine_snapshot(
        _verified_snapshot(qualification="player_custom_warehouse")
    )
    player[engine._WAREHOUSE_QUOTE_PROVENANCE_PREPARED_MARKER_KEY] = unknown[
        engine._WAREHOUSE_QUOTE_PROVENANCE_PREPARED_MARKER_KEY
    ]
    player[engine._WAREHOUSE_QUOTE_PROVENANCE_PREPARED_DECISION_KEY] = copy.deepcopy(
        unknown[engine._WAREHOUSE_QUOTE_PROVENANCE_PREPARED_DECISION_KEY]
    )
    assert _run(player, max_combos=8_000, rollback=False) == _run(
        player, max_combos=8_000, rollback=True
    )


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("map_id", 4502),
        ("table_status", "stale"),
        ("source_generation", "unknown"),
        ("auction_mode", "sealed"),
        ("source_conflict", True),
        ("quarantine_required", True),
        ("parser_replay_eligible", False),
        ("map_table_sha256", "c" * 64),
        ("map_source_contract_sha256", "d" * 64),
    ],
)
def test_strict_provenance_field_conflicts_fail_closed(
    field: str,
    bad_value: object,
) -> None:
    snapshot = _verified_snapshot()
    snapshot["ui_contract"]["context"]["warehouse_source"][field] = bad_value
    decision = engine._atomic_nonofficial_warehouse_quote_provenance(
        snapshot,
        map_id=4501,
    )
    assert decision.gabriela_qualification is None


def test_direct_q6_value_outranks_conditional_model() -> None:
    snapshot = _verified_snapshot()
    snapshot["structured_ref_inputs"]["quality_values"] = {
        "q6": TARGET_Q6_VALUE,
    }
    default = _run(snapshot, max_combos=8_000, rollback=False)
    rollback = _run(snapshot, max_combos=8_000, rollback=True)
    assert default == rollback
    assert default["red_value_range"] == [
        TARGET_Q6_VALUE,
        TARGET_Q6_VALUE,
        TARGET_Q6_VALUE,
    ]


@pytest.mark.parametrize("missing", ["exact_cells", "known_item"])
def test_exact_cells_and_correlated_known_item_are_both_required(
    missing: str,
) -> None:
    snapshot = _verified_snapshot()
    if missing == "exact_cells":
        snapshot["action_result_rows"] = []
        snapshot["ui_contract"]["actions"]["results"] = []
        snapshot["structured_ref_inputs"].pop("quality_cells", None)
    else:
        snapshot["skill_reveal_rows"] = []
        snapshot["structured_ref_inputs"]["minimap_items"] = []
        snapshot["ui_contract"]["minimap"]["items"] = []

    assert _run(snapshot, max_combos=8_000, rollback=False) == _run(
        snapshot,
        max_combos=8_000,
        rollback=True,
    )


def test_missing_authoritative_drop_population_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _verified_snapshot()
    engine._gabriela_q6_known_item_exact_cells_distribution.cache_clear()
    monkeypatch.setattr(engine, "_load_drop_weight_items", lambda: {})
    try:
        default = _run(snapshot, max_combos=8_000, rollback=False)
        rollback = _run(snapshot, max_combos=8_000, rollback=True)
    finally:
        engine._gabriela_q6_known_item_exact_cells_distribution.cache_clear()
    assert default == rollback


def test_authoritative_q6_conditioning_uses_weighted_map_population() -> None:
    evidence = engine.extract_evidence(
        engine.prepare_reference_engine_snapshot(_verified_snapshot())
    )
    count3 = engine._gabriela_q6_known_item_exact_cells_distribution(
        4501,
        3,
        25,
        tuple(evidence.q6_known_item_facts),
    )
    count4 = engine._gabriela_q6_known_item_exact_cells_distribution(
        4501,
        4,
        25,
        tuple(evidence.q6_known_item_facts),
    )
    assert count3[0] == count4[0] == "ok"
    assert count4[1] > count3[1] > 0
    assert sum(weight for _value, weight in count3[2]) == pytest.approx(1.0)
    assert sum(weight for _value, weight in count4[2]) == pytest.approx(1.0)
    assert count4[2][2][0] > count3[2][2][0] > 800_000
    assert "sampling:with_replacement;moments:exact" in count3[3]


class _BombDict(dict):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.magic_calls = 0

    def _explode(self, *_args: object, **_kwargs: object) -> object:
        self.magic_calls += 1
        raise RuntimeError("mapping magic must not run")

    get = _explode
    keys = _explode
    items = _explode
    __iter__ = _explode
    __bool__ = _explode


class _BombMapping(Mapping):
    def __init__(self) -> None:
        self.magic_calls = 0

    def _explode(self, *_args: object, **_kwargs: object) -> object:
        self.magic_calls += 1
        raise RuntimeError("mapping protocol must not run")

    __getitem__ = _explode
    __iter__ = _explode
    __len__ = _explode


class _BombStr(str):
    def __new__(cls, value: str) -> "_BombStr":
        instance = super().__new__(cls, value)
        instance.magic_calls = 0
        return instance

    def __eq__(self, _other: object) -> bool:
        self.magic_calls += 1
        raise RuntimeError("string equality must not run")

    def __bool__(self) -> bool:
        self.magic_calls += 1
        raise RuntimeError("string truthiness must not run")


class _BombFloat(float):
    def __new__(cls, value: float) -> "_BombFloat":
        instance = super().__new__(cls, value)
        instance.magic_calls = 0
        return instance

    def __float__(self) -> float:
        self.magic_calls += 1
        raise RuntimeError("numeric conversion must not run")

    def __round__(self, _ndigits: int | None = None) -> int:
        self.magic_calls += 1
        raise RuntimeError("numeric rounding must not run")


class _BombInt(int):
    def __new__(cls, value: int) -> "_BombInt":
        instance = super().__new__(cls, value)
        instance.magic_calls = 0
        return instance

    def __int__(self) -> int:
        self.magic_calls += 1
        raise RuntimeError("integer conversion must not run")

    def __float__(self) -> float:
        self.magic_calls += 1
        raise RuntimeError("float conversion must not run")


def test_untrusted_prepared_marker_fails_closed_without_magic() -> None:
    snapshot = _verified_snapshot()
    marker = _BombStr("a" * 64)
    snapshot[engine._WAREHOUSE_QUOTE_PROVENANCE_PREPARED_MARKER_KEY] = marker
    snapshot[engine._WAREHOUSE_QUOTE_PROVENANCE_PREPARED_DECISION_KEY] = {
        "legacy_qualification": None,
        "gabriela_qualification": None,
    }
    default = _run_uncloned(snapshot, max_combos=8_000, rollback=False)
    rollback = _run_uncloned(snapshot, max_combos=8_000, rollback=True)
    assert default == rollback
    assert marker.magic_calls == 0


@pytest.mark.parametrize("owner_kind", ["dict", "mapping", "scalar"])
def test_malicious_owner_types_fail_closed_without_magic(owner_kind: str) -> None:
    snapshot = _verified_snapshot()
    if owner_kind == "dict":
        tracker: object = _BombDict(snapshot["warehouse_source"])
        snapshot["ui_contract"]["context"]["warehouse_source"] = tracker
    elif owner_kind == "mapping":
        tracker = _BombMapping()
        snapshot["ui_contract"]["context"]["warehouse_source"] = tracker
    else:
        tracker = _BombStr("verified")
        snapshot["ui_contract"]["context"]["warehouse_source"][
            "table_status"
        ] = tracker

    default = _run_uncloned(snapshot, max_combos=8_000, rollback=False)
    rollback = _run_uncloned(snapshot, max_combos=8_000, rollback=True)
    assert default == rollback
    assert getattr(tracker, "magic_calls") == 0


@pytest.mark.parametrize("tracker", [_BombInt(25), _BombFloat(25.0)])
def test_red_plus_empty_ignores_nonexact_numeric_subclasses_without_magic(
    tracker: _BombInt | _BombFloat,
) -> None:
    ref = {
        "total_grid_range": [88, 104, 120],
        "quality_cells_ranges": {
            "q1": [20, 20, 20],
            "q3": [20, 20, 20],
            "q4": [20, 20, 20],
            "q5": [19, 19, 19],
        },
        "evidence": {"quality_cells": {"q6": tracker}},
    }
    assert overlay._remaining_cells_range(ref) == (9, 25, 41)
    assert tracker.magic_calls == 0


@pytest.mark.parametrize(
    "raw_red_cells",
    [
        True,
        "25",
        10**10_000,
        1e308,
        float("nan"),
        float("inf"),
        float("-inf"),
        -1,
        -1.0,
        overlay._MANUAL_MAX_GRID_CELLS + 1,
        float(overlay._MANUAL_MAX_GRID_CELLS + 1),
        121,
        25.5,
    ],
    ids=[
        "bool",
        "string",
        "huge_int",
        "huge_float",
        "nan",
        "positive_inf",
        "negative_inf",
        "negative_int",
        "negative_float",
        "over_project_bound_int",
        "over_project_bound_float",
        "over_total_range",
        "fractional",
    ],
)
def test_red_plus_empty_rejects_invalid_exact_red_cells_without_escaping(
    raw_red_cells: object,
) -> None:
    ref = {
        "total_grid_range": [88, 104, 120],
        "quality_cells_ranges": {
            "q1": [20, 20, 20],
            "q3": [20, 20, 20],
            "q4": [20, 20, 20],
            "q5": [19, 19, 19],
        },
        "evidence": {"quality_cells": {"q6": raw_red_cells}},
    }
    assert overlay._remaining_cells_range(ref) == (9, 25, 41)


@pytest.mark.parametrize("raw_red_cells", [25, 25.0])
def test_red_plus_empty_accepts_exact_builtin_integral_red_cells(
    raw_red_cells: int | float,
) -> None:
    ref = {
        "total_grid_range": [88, 104, 120],
        "quality_cells_ranges": {
            "q1": [20, 20, 20],
            "q3": [20, 20, 20],
            "q4": [20, 20, 20],
            "q5": [19, 19, 19],
        },
        "evidence": {"quality_cells": {"q6": raw_red_cells}},
    }
    assert overlay._remaining_cells_range(ref) == (25, 25, 41)


def test_real_tk_labels_geometric_room_separately_from_pure_red(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tk = pytest.importorskip("tkinter")
    monkeypatch.setattr(
        overlay.AhmadTkOverlay,
        "_start_activation_worker",
        lambda self: None,
    )
    monkeypatch.setattr(
        overlay.AhmadTkOverlay,
        "_poll_startup_update_notice",
        lambda self: None,
    )
    last_error: Exception | None = None
    root = None
    for _attempt in range(3):
        try:
            root = tk.Tk()
            break
        except tk.TclError as exc:  # noqa: PERF203 - bounded environment retry
            last_error = exc
    if root is None:
        pytest.skip(f"真 Tk 不可用(环境层): {last_error}")
    try:
        instance = overlay.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        root.update_idletasks()
        root.update()

        value_widget = instance.action_rows["红/空格"]
        caption_texts = {
            str(child.cget("text"))
            for child in value_widget.master.winfo_children()
            if child.winfo_class() == "Label"
        }
        assert "红+空" in caption_texts
        assert "红/空格" not in caption_texts
        summary = summarize_snapshot(
            _verified_snapshot(), snapshot_path=tmp_path / "render.json"
        )
        quality_cells = summary["ahmed_ref"]["evidence"]["quality_cells"]
        quality_cells.pop("q6", None)
        expected_range = overlay._remaining_cells_range(summary["ahmed_ref"])
        quality_cells["q6"] = 10**10_000
        instance.render(summary)
        root.update_idletasks()
        assert str(value_widget.cget("text")) == instance._range_text(
            expected_range, suffix="格"
        )
        instance._set_remaining_cells_tip({})
        tip = value_widget._hover_tip  # type: ignore[attr-defined]
        assert "红 + 空位" in tip.text
        assert "不等于左侧纯红格" in tip.text
    finally:
        root.destroy()


@pytest.mark.parametrize(
    "case", ["other_hero", "other_map", "other_round", "settled", "official"]
)
def test_other_scope_and_official_sources_are_noop(case: str) -> None:
    snapshot = _verified_snapshot()
    context = snapshot["ui_contract"]["context"]
    if case == "other_hero":
        snapshot["hero"] = context["hero"] = "aisha"
    elif case == "other_map":
        snapshot["map_id"] = context["map_id"] = 4502
    elif case == "other_round":
        snapshot["round"] = context["round"] = 3
    elif case == "settled":
        snapshot["phase"] = context["phase"] = "settled"
    else:
        for source in (
            snapshot["warehouse_source"],
            context["warehouse_source"],
        ):
            source["qualification"] = "official_random_warehouse"
            source["official_distribution_eligible"] = True
        snapshot["warehouse_source_qualification"] = "official_random_warehouse"
        snapshot["official_distribution_eligible"] = True

    assert _run(snapshot, max_combos=8_000, rollback=False) == _run(
        snapshot, max_combos=8_000, rollback=True
    )


def test_summary_and_loopback_publish_the_same_structured_semantics(
    tmp_path: Path,
) -> None:
    snapshot = _verified_snapshot()
    summary = summarize_snapshot(
        copy.deepcopy(snapshot), snapshot_path=tmp_path / "summary.json"
    )
    api = _loopback(copy.deepcopy(snapshot), tmp_path / "api")
    for payload in (summary, api):
        q6 = payload["quality_composition"]["qualities"]["q6"]
        assert q6["lock_strength"] == "probabilistic"
        assert q6["shown_probability"] + q6["other_probability"] == pytest.approx(
            1.0
        )
        assert payload["ahmed_ref"]["red_cells_range"] == [25, 25, 25]
