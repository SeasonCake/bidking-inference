"""Runtime no-quote gate for update606 routes without distribution authority."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ENGINE_SRC = ROOT / "hero_ref" / "src"
TOOLS = ROOT / "hero_ref" / "tools"
PACKAGE_SRC = ROOT / "src"
for _path in (ENGINE_SRC, TOOLS, PACKAGE_SRC):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import ahmad_ref_engine as engine  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    CHENMEI_ZHENHAI_PENDING_NOTE_PREFIX,
    CURRENT_RANKMAP_AUTHORITY_NOTE_PREFIX,
    CURRENT_RANKMAP_MAP_IDS,
    CURRENT_RANKMAP_PACKAGE_GENERATION,
    UPDATE606_CHANGED_ROUTE_MAP_IDS,
    UPDATE606_CHANGED_ROUTE_NOTE_PREFIX,
    UPDATE606_CHANGED_ROUTE_REASON_BY_MAP,
    UPDATE606_RUNTIME_PACKAGE_GENERATION,
    _update606_changed_route_map_value,
    _update606_changed_route_scope_state,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from ahmad_live_panel_server import summarize_snapshot  # noqa: E402
from ahmad_tk_overlay import AhmadTkOverlay  # noqa: E402
from bidking_lab.extract import update606_package_input_contract as package_contract  # noqa: E402
from bidking_lab.extract.bid_map_table import BID_MAP_0_3_4_GENERATION  # noqa: E402


EXPECTED_CHANGED_ROUTE_REASON_BY_MAP = (
    (2511, "sub_pool_weights_changed"),
    (4511, "sub_pool_weights_changed"),
    (5201, "activity_route_changed_no_distribution_authority"),
    (5202, "new_activity_route_no_distribution_authority"),
    (5505, "activity_route_repurposed_no_distribution_authority"),
    (5506, "auction_rounds_rate_changed"),
    (5601, "sub_pool_weights_and_auction_rounds_rate_changed"),
    (5602, "auction_rounds_rate_changed"),
    (5603, "auction_rounds_rate_changed"),
    (5604, "auction_rounds_rate_changed"),
    (5605, "auction_rounds_rate_changed"),
    (5606, "auction_rounds_rate_changed"),
    (5607, "auction_rounds_rate_changed"),
    (5608, "auction_rounds_rate_changed"),
    (5609, "auction_rounds_rate_changed"),
    (5610, "auction_rounds_rate_changed"),
)


@pytest.fixture(autouse=True)
def _missing_current_rankmap_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep this file's contract explicitly on the no-authority negative arm."""

    monkeypatch.setattr(
        engine,
        "_current_rankmap_distribution_generation_status",
        lambda: ("authority_input_unavailable", ""),
    )


def _snapshot(
    map_id: object,
    *,
    context_map: object | None = None,
    phase: str = "bidding",
) -> dict[str, object]:
    hero = (
        "chenmei"
        if type(map_id) is int and map_id == 5506  # noqa: E721 - exact wire type
        else "ahmed"
    )
    context_map = map_id if context_map is None else context_map
    return {
        "hero": hero,
        "map_id": map_id,
        "round": 2,
        "phase": phase,
        "structured_ref_inputs": {"total_count": 34},
        "public_info_rows": [],
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": context_map,
                "round": 2,
                "phase": phase,
            }
        },
    }


class _TrapFloat(float):
    calls: list[str] = []

    def __int__(self):
        type(self).calls.append("__int__")
        raise AssertionError("float subclass __int__ invoked")

    def __index__(self):
        type(self).calls.append("__index__")
        raise AssertionError("float subclass __index__ invoked")

    def __float__(self):
        type(self).calls.append("__float__")
        raise AssertionError("float subclass __float__ invoked")

    def __eq__(self, _other):
        type(self).calls.append("__eq__")
        raise AssertionError("float subclass __eq__ invoked")

    def __str__(self):
        type(self).calls.append("__str__")
        raise AssertionError("float subclass __str__ invoked")

    def as_integer_ratio(self):
        type(self).calls.append("as_integer_ratio")
        raise AssertionError("float subclass as_integer_ratio invoked")


class _InheritedTrapFloat(_TrapFloat):
    pass


class _TrapInt(int):
    calls: list[str] = []

    def __int__(self):
        type(self).calls.append("__int__")
        raise AssertionError("int subclass __int__ invoked")

    def __index__(self):
        type(self).calls.append("__index__")
        raise AssertionError("int subclass __index__ invoked")

    def __eq__(self, _other):
        type(self).calls.append("__eq__")
        raise AssertionError("int subclass __eq__ invoked")

    def __str__(self):
        type(self).calls.append("__str__")
        raise AssertionError("int subclass __str__ invoked")


class _TrapStr(str):
    calls: list[str] = []

    def __eq__(self, _other):
        type(self).calls.append("__eq__")
        raise AssertionError("str subclass __eq__ invoked")

    def __str__(self):
        type(self).calls.append("__str__")
        raise AssertionError("str subclass __str__ invoked")


class _PoisonStaticData(dict):
    get_calls = 0

    def get(self, *_args, **_kwargs):
        type(self).get_calls += 1
        raise AssertionError("changed route reached truthy StaticData fallback")


def _assert_no_quote(result, map_id: int) -> None:
    assert result.status == "no_reachable_combo"
    assert result.combo_count == 0
    assert (result.conservative, result.balanced, result.aggressive) == (
        None,
        None,
        None,
    )
    assert result.quality_count_ranges == {}
    assert result.quality_cells_ranges == {}
    assert result.total_grid_range == (None, None, None)
    prefix = (
        CHENMEI_ZHENHAI_PENDING_NOTE_PREFIX
        if map_id == 5506
        else (
            CURRENT_RANKMAP_AUTHORITY_NOTE_PREFIX
            if map_id in CURRENT_RANKMAP_MAP_IDS
            else UPDATE606_CHANGED_ROUTE_NOTE_PREFIX
        )
    )
    assert len(result.notes) == 1
    assert result.notes[0].startswith(f"{prefix}:")


def _current_rankmap_note(map_id: int, reason: str) -> str:
    return (
        f"{CURRENT_RANKMAP_AUTHORITY_NOTE_PREFIX}:"
        f"{CURRENT_RANKMAP_PACKAGE_GENERATION}:{map_id}:{reason}"
    )


def test_runtime_contract_matches_exact_update606_package_scope_identity() -> None:
    assert UPDATE606_RUNTIME_PACKAGE_GENERATION == BID_MAP_0_3_4_GENERATION
    assert UPDATE606_CHANGED_ROUTE_REASON_BY_MAP == EXPECTED_CHANGED_ROUTE_REASON_BY_MAP
    assert UPDATE606_CHANGED_ROUTE_MAP_IDS == tuple(
        map_id for map_id, _reason in EXPECTED_CHANGED_ROUTE_REASON_BY_MAP
    )
    assert package_contract._ids_sha256(  # noqa: SLF001
        UPDATE606_CHANGED_ROUTE_MAP_IDS
    ) == "f43e639dbbdeb3d855cce7aa4eb21a94a8923ca21ef49f3d944e8496a89adbec"
    assert set(UPDATE606_CHANGED_ROUTE_MAP_IDS).issubset(
        engine._AISHA_Q5_TOTAL_CELLS_FORBIDDEN_WEIGHT_MAP_IDS
    )
    assert 6001 not in UPDATE606_CHANGED_ROUTE_MAP_IDS


@pytest.mark.parametrize(
    ("value", "expected", "trap_type"),
    (
        pytest.param(5603, ("present", 5603), None, id="plain-int"),
        pytest.param("5603", ("present", 5603), None, id="plain-str"),
        pytest.param(5603.0, ("invalid_target", 5603), None, id="plain-float"),
        pytest.param(
            5603.25,
            ("invalid_target", 5603),
            None,
            id="fractional-float",
        ),
        pytest.param(
            2501.25,
            ("invalid", None),
            None,
            id="stable-fractional-float",
        ),
        pytest.param(float("nan"), ("invalid", None), None, id="nan"),
        pytest.param(float("inf"), ("invalid", None), None, id="positive-inf"),
        pytest.param(float("-inf"), ("invalid", None), None, id="negative-inf"),
        pytest.param(-5603.0, ("invalid", None), None, id="negative"),
        pytest.param(-5603.25, ("invalid", None), None, id="negative-fractional"),
        pytest.param(10_000.25, ("invalid", None), None, id="out-of-range"),
        pytest.param(True, ("invalid", None), None, id="bool"),
        pytest.param(
            _TrapFloat(5603.0),
            ("invalid_target", 5603),
            _TrapFloat,
            id="float-subclass",
        ),
        pytest.param(
            _InheritedTrapFloat(5603.0),
            ("invalid_target", 5603),
            _InheritedTrapFloat,
            id="inherited-float-subclass",
        ),
        pytest.param(
            _InheritedTrapFloat(5603.25),
            ("invalid_target", 5603),
            _InheritedTrapFloat,
            id="fractional-float-subclass",
        ),
        pytest.param(
            _InheritedTrapFloat(2501.25),
            ("invalid", None),
            _InheritedTrapFloat,
            id="stable-fractional-float-subclass",
        ),
        pytest.param(
            _InheritedTrapFloat(10_000.25),
            ("invalid", None),
            _InheritedTrapFloat,
            id="out-of-range-float-subclass",
        ),
        pytest.param(
            _TrapInt(5603),
            ("invalid_target", 5603),
            _TrapInt,
            id="int-subclass",
        ),
        pytest.param(
            _TrapStr("5603"),
            ("invalid_target", 5603),
            _TrapStr,
            id="str-subclass",
        ),
        pytest.param(
            _TrapFloat(5507.0),
            ("invalid", None),
            _TrapFloat,
            id="route-stable-float-subclass",
        ),
    ),
)
def test_changed_route_map_value_classification_without_subclass_protocols(
    value: object,
    expected: tuple[str, int | None],
    trap_type: type[_TrapFloat] | type[_TrapInt] | type[_TrapStr] | None,
) -> None:
    if trap_type is not None:
        trap_type.calls = []
    assert _update606_changed_route_map_value(value) == expected
    if trap_type is not None:
        assert trap_type.calls == []


@pytest.mark.parametrize("map_id", UPDATE606_CHANGED_ROUTE_MAP_IDS)
def test_all_changed_routes_direct_and_prepared_publish_no_quote(map_id: int) -> None:
    snapshot = _snapshot(map_id)
    for candidate in (snapshot, prepare_reference_engine_snapshot(snapshot)):
        _assert_no_quote(run_reference_engine(candidate, max_combos=8000), map_id)


@pytest.mark.parametrize("map_id", (5602, 5603, 5604))
def test_neighboring_changed_routes_cannot_use_static_or_tier_fallbacks(map_id: int) -> None:
    result = run_reference_engine(_snapshot(map_id), max_combos=8000)
    _assert_no_quote(result, map_id)
    assert "fallback_default_price" not in result.notes
    assert not any("activity_shipwreck_red_boost" in note for note in result.notes)


def test_5603_gate_does_not_touch_fallback_static_data() -> None:
    _PoisonStaticData.get_calls = 0
    result = run_reference_engine(
        _snapshot(5603),
        static_data=_PoisonStaticData({"truthy": object()}),
        max_combos=8000,
    )
    _assert_no_quote(result, 5603)
    assert result.notes == (
        _current_rankmap_note(5603, "authority_input_unavailable"),
    )
    assert _PoisonStaticData.get_calls == 0


@pytest.mark.parametrize("map_id", (5603, "5603"), ids=("plain-int", "plain-str"))
@pytest.mark.parametrize("prepared", (False, True), ids=("direct", "prepared"))
def test_changed_route_plain_wire_owner_keeps_no_approved_distribution(
    map_id: int | str,
    prepared: bool,
) -> None:
    _PoisonStaticData.get_calls = 0
    snapshot = _snapshot(map_id)
    candidate = prepare_reference_engine_snapshot(snapshot) if prepared else snapshot

    result = run_reference_engine(
        candidate,
        static_data=_PoisonStaticData({"truthy": object()}),
        max_combos=8000,
    )

    _assert_no_quote(result, 5603)
    assert result.notes == (
        _current_rankmap_note(5603, "authority_input_unavailable"),
    )
    assert _PoisonStaticData.get_calls == 0


@pytest.mark.parametrize("prepared", (False, True), ids=("direct", "prepared"))
def test_changed_route_plain_float_alias_is_stable_conflict(
    prepared: bool,
) -> None:
    _PoisonStaticData.get_calls = 0
    snapshot = _snapshot(5603.0)
    candidate = prepare_reference_engine_snapshot(snapshot) if prepared else snapshot

    result = run_reference_engine(
        candidate,
        static_data=_PoisonStaticData({"truthy": object()}),
        max_combos=8000,
    )

    _assert_no_quote(result, 5603)
    assert result.notes == (
        _current_rankmap_note(5603, "map_conflict"),
    )
    assert _PoisonStaticData.get_calls == 0


@pytest.mark.parametrize("value_type", (_TrapFloat, _InheritedTrapFloat))
@pytest.mark.parametrize("prepared", (False, True), ids=("direct", "prepared"))
def test_changed_route_float_subclass_base_payload_quarantines_without_protocols(
    value_type: type[_TrapFloat],
    prepared: bool,
) -> None:
    value_type.calls = []
    _PoisonStaticData.get_calls = 0
    value = value_type(5603.0)
    snapshot = _snapshot(value)
    candidate = prepare_reference_engine_snapshot(snapshot) if prepared else snapshot

    result = run_reference_engine(
        candidate,
        static_data=_PoisonStaticData({"truthy": object()}),
        max_combos=8000,
    )

    _assert_no_quote(result, 5603)
    assert result.notes == (
        _current_rankmap_note(5603, "map_conflict"),
    )
    assert value_type.calls == []
    assert _PoisonStaticData.get_calls == 0


@pytest.mark.parametrize(
    "value_factory",
    (lambda: 5603.25, lambda: _InheritedTrapFloat(5603.25)),
    ids=("plain", "hostile-subclass"),
)
@pytest.mark.parametrize("prepared", (False, True), ids=("direct", "prepared"))
def test_changed_route_fractional_alias_owner_is_stable_conflict_without_fallback(
    value_factory,
    prepared: bool,
) -> None:
    _InheritedTrapFloat.calls = []
    _PoisonStaticData.get_calls = 0
    value = value_factory()
    snapshot = _snapshot(value)
    candidate = prepare_reference_engine_snapshot(snapshot) if prepared else snapshot

    result = run_reference_engine(
        candidate,
        static_data=_PoisonStaticData({"truthy": object()}),
        max_combos=8000,
    )

    _assert_no_quote(result, 5603)
    assert result.notes == (
        _current_rankmap_note(5603, "map_conflict"),
    )
    assert _InheritedTrapFloat.calls == []
    assert _PoisonStaticData.get_calls == 0


@pytest.mark.parametrize(
    "value_factory",
    (lambda: 5603.25, lambda: _InheritedTrapFloat(5603.25)),
    ids=("plain", "hostile-subclass"),
)
@pytest.mark.parametrize("owner", ("root", "context"))
@pytest.mark.parametrize("prepared", (False, True), ids=("direct", "prepared"))
def test_changed_route_fractional_alias_single_owner_is_stable_conflict(
    value_factory,
    owner: str,
    prepared: bool,
) -> None:
    _InheritedTrapFloat.calls = []
    _PoisonStaticData.get_calls = 0
    value = value_factory()
    snapshot = _snapshot(value)
    if owner == "root":
        snapshot["ui_contract"]["context"].pop("map_id")
    else:
        snapshot.pop("map_id")
    candidate = prepare_reference_engine_snapshot(snapshot) if prepared else snapshot

    result = run_reference_engine(
        candidate,
        static_data=_PoisonStaticData({"truthy": object()}),
        max_combos=8000,
    )

    _assert_no_quote(result, 5603)
    assert result.notes == (
        _current_rankmap_note(5603, "map_conflict"),
    )
    assert _InheritedTrapFloat.calls == []
    assert _PoisonStaticData.get_calls == 0


@pytest.mark.parametrize("prepared", (False, True), ids=("direct", "prepared"))
def test_route_stable_fractional_owner_is_not_a_changed_route(prepared: bool) -> None:
    snapshot = _snapshot(2501.25)
    candidate = prepare_reference_engine_snapshot(snapshot) if prepared else snapshot
    assert _update606_changed_route_scope_state(candidate) is None


@pytest.mark.parametrize("prepared", (False, True), ids=("direct", "prepared"))
@pytest.mark.parametrize(
    ("root_map", "context_map"),
    (
        pytest.param(5603, 5603.25, id="context-fractional"),
        pytest.param(5603, float("nan"), id="context-nan"),
        pytest.param(5603, float("inf"), id="context-inf"),
        pytest.param(5603, -5603.0, id="context-negative"),
        pytest.param(5603, True, id="context-bool"),
        pytest.param(5603.25, 5603, id="root-fractional"),
        pytest.param(_InheritedTrapFloat(5603.25), 5603, id="root-fractional-subclass"),
        pytest.param(
            5603,
            _InheritedTrapFloat(5603.25),
            id="context-fractional-subclass",
        ),
    ),
)
def test_changed_route_invalid_owner_with_exact_target_is_stable_conflict(
    root_map: object,
    context_map: object,
    prepared: bool,
) -> None:
    _InheritedTrapFloat.calls = []
    _PoisonStaticData.get_calls = 0
    snapshot = _snapshot(root_map, context_map=context_map)
    candidate = prepare_reference_engine_snapshot(snapshot) if prepared else snapshot

    result = run_reference_engine(
        candidate,
        static_data=_PoisonStaticData({"truthy": object()}),
        max_combos=8000,
    )

    _assert_no_quote(result, 5603)
    assert result.notes == (
        _current_rankmap_note(5603, "map_conflict"),
    )
    assert _InheritedTrapFloat.calls == []
    assert _PoisonStaticData.get_calls == 0


@pytest.mark.parametrize(
    ("root_map", "context_map"),
    ((5603, 5602), (5602, 5603), (5603, True), (5603, "5604")),
)
def test_changed_route_raw_owner_conflict_stays_no_quote(
    root_map: int,
    context_map: int | str | bool,
) -> None:
    result = run_reference_engine(_snapshot(root_map, context_map=context_map))
    _assert_no_quote(result, root_map)
    assert result.notes == (
        _current_rankmap_note(root_map, "map_conflict"),
    )


@pytest.mark.parametrize("prepared", (False, True), ids=("direct", "prepared"))
def test_changed_route_builtin_subclasses_cannot_reach_coercive_fallback(
    prepared: bool,
) -> None:
    class TrapInt(int):
        def __str__(self):
            raise AssertionError("int subclass conversion invoked")

        def __eq__(self, _other):
            raise AssertionError("int subclass comparison invoked")

    class TrapStr(str):
        def __str__(self):
            raise AssertionError("str subclass conversion invoked")

        def __eq__(self, _other):
            raise AssertionError("str subclass comparison invoked")

    for value in (TrapInt(5603), TrapStr("5603")):
        _PoisonStaticData.get_calls = 0
        snapshot = _snapshot(5603)
        snapshot["map_id"] = value
        snapshot["ui_contract"]["context"]["map_id"] = value
        candidate = prepare_reference_engine_snapshot(snapshot) if prepared else snapshot
        result = run_reference_engine(
            candidate,
            static_data=_PoisonStaticData({"truthy": object()}),
        )
        _assert_no_quote(result, 5603)
        assert result.notes == (
            _current_rankmap_note(5603, "map_conflict"),
        )
        assert _PoisonStaticData.get_calls == 0


@pytest.mark.parametrize("value", (5603.0, "5603.0", "5,603", " 5603 "))
@pytest.mark.parametrize("prepared", (False, True), ids=("direct", "prepared"))
def test_changed_route_json_aliases_cannot_reach_coercive_fallback(
    value: object,
    prepared: bool,
) -> None:
    _PoisonStaticData.get_calls = 0
    snapshot = _snapshot(5603)
    snapshot["map_id"] = value
    snapshot["ui_contract"]["context"]["map_id"] = value
    candidate = prepare_reference_engine_snapshot(snapshot) if prepared else snapshot
    result = run_reference_engine(
        candidate,
        static_data=_PoisonStaticData({"truthy": object()}),
    )
    _assert_no_quote(result, 5603)
    assert result.notes == (
        _current_rankmap_note(5603, "map_conflict"),
    )
    assert _PoisonStaticData.get_calls == 0


def test_changed_route_dict_subclasses_are_read_only_for_quarantine() -> None:
    class TrapDict(dict):
        def get(self, *_args, **_kwargs):
            raise AssertionError("dict subclass get invoked")

        def items(self):
            raise AssertionError("dict subclass items invoked")

    snapshot = TrapDict(_snapshot(5603))
    result = run_reference_engine(snapshot)
    _assert_no_quote(result, 5603)
    assert result.notes == (
        _current_rankmap_note(5603, "map_conflict"),
    )


@pytest.mark.parametrize("phase", ("bidding", "settled"))
def test_changed_route_summary_api_holds_all_three_quotes(phase: str) -> None:
    summary = summarize_snapshot(
        _snapshot(5603, phase=phase),
        snapshot_path=Path(f"update606_5603_{phase}_no_quote_probe.json"),
    )
    reference = summary["reference"]
    assert reference["readiness"] == "no_reachable_combo"
    assert reference["source"] == "ref_waiting"
    assert (
        reference["conservative"],
        reference["balanced"],
        reference["aggressive"],
    ) == ("-", "-", "-")
    assert "ahmad_ref" not in summary


def test_changed_route_tk_manual_surface_holds_all_three_quotes() -> None:
    overlay = object.__new__(AhmadTkOverlay)
    summary = overlay._manual_result_summary(_snapshot(5603))  # noqa: SLF001
    reference = summary["reference"]
    assert reference["readiness"] == "no_reachable_combo"
    assert reference["action"] == "约束不足"
    assert (
        reference["conservative"],
        reference["balanced"],
        reference["aggressive"],
    ) == ("-", "-", "-")
    assert summary["ahmed_ref"]["combo_count"] == 0


@pytest.mark.parametrize("map_id", (2501, 5507, 9998))
def test_route_stable_and_unknown_maps_keep_existing_quote_path(map_id: int) -> None:
    result = run_reference_engine(_snapshot(map_id), max_combos=8000)
    assert result.status != "no_reachable_combo"
    assert result.balanced is not None
    assert not any(
        note.startswith(
            (UPDATE606_CHANGED_ROUTE_NOTE_PREFIX, CURRENT_RANKMAP_AUTHORITY_NOTE_PREFIX)
        )
        for note in result.notes
    )
