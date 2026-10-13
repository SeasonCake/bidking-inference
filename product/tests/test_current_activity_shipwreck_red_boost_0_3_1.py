from __future__ import annotations

import copy
import json
import math
import sys
import threading
from collections.abc import Mapping
from decimal import Decimal
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import ahmad_live_panel_server as panel  # noqa: E402
import ahmad_ref_engine as engine  # noqa: E402
from detail_composition_view import detail_composition_view  # noqa: E402


PROBS = {
    "q1": 0.30,
    "q2": 0.15,
    "q3": 0.20,
    "q4": 0.15,
    "q5": 0.126,
    "q6": 0.074,
}
UNKNOWN_SOURCE = {
    "qualification": "postupdate_unknown_warehouse_source",
    "reason": "postupdate_non_ranked_source_unresolved",
    "official_distribution_eligible": False,
    "quote_policy": "advisory_only_no_clamp",
}
PLAYER_SOURCE = {
    "qualification": "player_custom_warehouse",
    "reason": "confirmed_player_custom_warehouse",
    "official_distribution_eligible": False,
    "quote_policy": "advisory_only_no_clamp",
}
OFFICIAL_SOURCE = {
    "qualification": "official_distribution",
    "reason": "official_distribution_verified",
    "official_distribution_eligible": True,
    "quote_policy": "normal",
}


def _structured(total_count: int = 42) -> dict:
    return {
        "source": "monitor_pre_settlement_field_updates",
        "hero": "ahmed",
        "total_count": total_count,
        "counts": {},
        "quality_cells": {},
        "avg_cells": {},
        "avg_values": {},
        "quality_values": {},
        "quality_value_floors": {},
        "count_sums": {},
        "scoped_count_sums": [],
        "split_counts": {},
        "split_quality_cells": {},
        "split_avg_cells": {},
        "minimap_items": [],
    }


def _snapshot(
    *,
    map_id: int = 4523,
    timestamp: int | float = (
        engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH + 1
    ),
    phase: str = "bidding",
    source: dict = UNKNOWN_SOURCE,
) -> dict:
    structured = _structured()
    context = {
        "hero": "ahmed",
        "map_id": map_id,
        "model_map_id": 2501 + ((map_id - 1) % 10),
        "round": 1,
        "action_round": 1,
        "phase": phase,
        "session_id": f"{map_id}:current-activity-test",
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": source["qualification"],
        "official_distribution_eligible": source[
            "official_distribution_eligible"
        ],
    }
    return {
        **context,
        "created_at": timestamp,
        "structured_ref_inputs": copy.deepcopy(structured),
        "ahmad_ref_inputs": copy.deepcopy(structured),
        "ui_contract": {
            "context": context,
            "constraints": {
                "structured_ref_inputs": copy.deepcopy(structured),
                "public_info": {},
            },
        },
    }


def _settled_snapshot() -> dict:
    return {
        "phase": "settled",
        "hero": "aisha",
        "map_id": 4523,
        "created_at": engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH + 1,
        "inventory_count": 56,
        "inventory_cells": 154,
        "settlement_incomplete": False,
        "settlement_parse_residual": 0,
        "final_quality_counts": "q1=3;q2=10;q3=15;q4=12;q5=8;q6=8",
        "final_quality_cells": "q1=3;q2=25;q3=43;q4=34;q5=27;q6=22",
        "layout_stage_rows": [{"布局估计": "71/164/170"}],
        "ui_contract": {
            "context": {"hero": "aisha", "map_id": 4523, "phase": "settled"},
            "truth": {
                "available": True,
                "complete": True,
                "partial": False,
                "captured_items": 56,
                "captured_cells": 154,
                "declared_items": None,
                "declared_cells": None,
                "total_items": 56,
                "total_cells": 154,
                "total_value": 2_163_815,
            },
        },
    }


def _apply(
    map_id: object,
    snapshot: object,
    *,
    phase: object = "bidding",
) -> tuple[dict[str, float], list[str]]:
    notes: list[str] = []
    result = engine._apply_activity_shipwreck_red_boost(
        dict(PROBS),
        map_id,
        snapshot,  # type: ignore[arg-type]
        notes,
        phase=phase,
    )
    return result, notes


def _weighted_candidate_mean(rows: list[dict]) -> float:
    probability = sum(float(row["prob"]) for row in rows)
    return sum(float(row["value"]) * float(row["prob"]) for row in rows) / probability


def test_current_activity_contract_constants_and_map_ranges() -> None:
    assert engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_FACTOR == 1.5
    assert (
        engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START
        == "2026-07-30 12:00:00+08:00"
    )
    assert (
        engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_END
        == "2026-08-06 12:00:00+08:00"
    )
    assert engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH == 1_785_384_000
    assert engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_END_EPOCH == 1_785_988_800

    for map_id in (2521, 2530, 4521, 4530, "2523", "4523"):
        assert engine._is_current_activity_shipwreck_map(map_id) is True
    for map_id in (
        2520,
        2531,
        4520,
        4531,
        2503,
        4503,
        2511,
        2541,
        True,
        "٢٥٢٣",
    ):
        assert engine._is_current_activity_shipwreck_map(map_id) is False


@pytest.mark.parametrize(
    "map_id",
    [2521, 2530, 4521, 4530, "2523", "4523"],
)
@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH - 1, False),
        (engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH, True),
        (engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_END_EPOCH - 1, True),
        (engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_END_EPOCH, False),
    ],
)
def test_current_activity_uses_exact_half_open_beijing_window(
    map_id: int,
    timestamp: int,
    expected: bool,
) -> None:
    result, notes = _apply(map_id, {"created_at": timestamp})
    assert (result != PROBS) is expected
    assert bool(notes) is expected


@pytest.mark.parametrize(
    ("snapshot", "phase"),
    [
        ({}, "bidding"),
        ({"created_at": None}, "bidding"),
        ({"created_at": True}, "bidding"),
        ({"created_at": float("nan")}, "bidding"),
        ({"created_at": 10**400}, "bidding"),
        (
            {"created_at": engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH},
            "settled",
        ),
        (
            {"created_at": engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH},
            "manual",
        ),
    ],
)
def test_current_activity_missing_invalid_time_or_nonlive_phase_is_noop(
    snapshot: dict,
    phase: str,
) -> None:
    result, notes = _apply(4523, snapshot, phase=phase)
    assert result == PROBS
    assert notes == []


def test_current_activity_client_timestamp_fallback_is_bounded() -> None:
    result, notes = _apply(
        4523,
        {"client_ts": engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH},
    )
    assert result != PROBS
    assert notes == ["current_activity_shipwreck_red_boost:1.5"]

    result, notes = _apply(
        4523,
        {
            "created_at": None,
            "client_ts": engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH,
        },
    )
    assert result == PROBS
    assert notes == []


def test_current_activity_multiplies_only_q6_weight_then_renormalizes() -> None:
    result, notes = _apply(
        4523,
        {"created_at": engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH},
    )

    factor = engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_FACTOR
    expected_q6 = PROBS["q6"] * factor / (
        1.0 + (factor - 1.0) * PROBS["q6"]
    )
    assert result["q6"] == pytest.approx(expected_q6)
    assert sum(result.values()) == pytest.approx(1.0)
    assert notes == ["current_activity_shipwreck_red_boost:1.5"]

    # Every non-red relative weight remains identical.  This catches accidental
    # q5, value, total-count or whole-inventory multipliers.
    for left, right in (("q1", "q2"), ("q2", "q3"), ("q3", "q4"), ("q4", "q5")):
        assert result[left] / result[right] == pytest.approx(
            PROBS[left] / PROBS[right]
        )


def test_current_activity_has_independent_rollback_and_does_not_relabel_old_maps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamp = engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH
    monkeypatch.setenv(engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE, "1")

    for map_id in (2521, 2530, 4521, 4530):
        assert _apply(map_id, {"created_at": timestamp}) == (PROBS, [])

    monkeypatch.delenv(engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE)
    for map_id in (2503, 4503, 2531, 4531):
        assert _apply(map_id, {"created_at": timestamp}) == (PROBS, [])

    # The independent switch must not disable either historical activity regime.
    assert _apply(2511, {})[0]["q6"] > PROBS["q6"]
    assert _apply(2541, {})[0]["q6"] > PROBS["q6"]


@pytest.mark.parametrize("map_id", [2523, 4523])
@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_current_activity_default_rollback_ab_at_both_combo_caps(
    map_id: int,
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(map_id=map_id)
    original = copy.deepcopy(snapshot)

    monkeypatch.delenv(
        engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE,
        raising=False,
    )
    default = engine.run_reference_engine(
        copy.deepcopy(snapshot),
        max_combos=max_combos,
    ).as_dict()
    monkeypatch.setenv(engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE, "1")
    rollback = engine.run_reference_engine(
        copy.deepcopy(snapshot),
        max_combos=max_combos,
    ).as_dict()

    assert snapshot == original
    assert default["status"] == rollback["status"] == "count_prior"
    assert default["evidence"]["total_count"] == rollback["evidence"]["total_count"] == 42
    assert "current_activity_shipwreck_red_boost:1.5" in default["notes"]
    assert not any(
        note.startswith("current_activity_shipwreck_red_boost:")
        for note in rollback["notes"]
    )
    assert _weighted_candidate_mean(
        default["quality_count_candidates"]["q6"]
    ) > _weighted_candidate_mean(rollback["quality_count_candidates"]["q6"])
    assert default["quality_lock"]["q6"]["strength"] == "probabilistic"
    assert rollback["quality_lock"]["q6"]["strength"] == "probabilistic"


def test_current_activity_settlement_truth_path_is_rollback_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _settled_snapshot()
    monkeypatch.delenv(
        engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE,
        raising=False,
    )
    default = engine.run_reference_engine(
        copy.deepcopy(snapshot),
        max_combos=8_000,
    ).as_dict()
    monkeypatch.setenv(engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE, "1")
    rollback = engine.run_reference_engine(
        copy.deepcopy(snapshot),
        max_combos=8_000,
    ).as_dict()

    assert default == rollback
    assert default["status"] == "ok"
    assert not any(
        note.startswith("current_activity_shipwreck_red_boost:")
        for note in default["notes"]
    )


@pytest.mark.parametrize("source", [UNKNOWN_SOURCE, PLAYER_SOURCE, OFFICIAL_SOURCE])
def test_current_activity_summary_and_ui_share_engine_output_without_requalification(
    source: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamp = engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH + 60
    snapshot = _snapshot(map_id=4523, timestamp=timestamp, source=source)
    monkeypatch.setattr(panel.time, "time", lambda: float(timestamp + 1))
    cap = panel.hero_max_combos_for_round("ahmed", 1)

    direct = engine.run_reference_engine(
        copy.deepcopy(snapshot),
        max_combos=cap,
    ).as_dict()
    summary = panel.summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=ROOT / "scratch" / "current_activity_summary.json",
    )
    product = summary["ahmed_ref"]
    view = detail_composition_view(summary)

    assert summary["context"]["warehouse_source_qualification"] == source[
        "qualification"
    ]
    assert product["conservative"] == direct["conservative"]
    assert product["balanced"] == direct["balanced"]
    assert product["aggressive"] == direct["aggressive"]
    assert (
        product["quality_count_cells_candidates"]
        == direct["quality_count_cells_candidates"]
    )
    assert product["quality_lock"] == direct["quality_lock"]
    assert "current_activity_shipwreck_red_boost:1.5" in product["notes"]

    raw_q6 = summary["quality_composition"]["qualities"]["q6"]["candidates"]
    ui_q6 = view.quality("q6")
    assert [
        (row.count, row.cells, row.probability) for row in ui_q6.candidates
    ] == [
        (row["count"], row["cells"], row["probability"]) for row in raw_q6
    ]
    assert ui_q6.lock_strength == "probabilistic"


def test_current_activity_snapshot_container_subclass_fails_closed_without_get() -> None:
    class BombDict(dict):
        magic_calls = 0

        def get(self, *_args, **_kwargs):
            type(self).magic_calls += 1
            raise RuntimeError("private-canary")

    snapshot = BombDict(
        created_at=engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH
    )
    result, notes = _apply(4523, snapshot)

    assert result == PROBS
    assert notes == []
    assert BombDict.magic_calls == 0
    assert math.isfinite(sum(result.values()))


class _BombMapScalar:
    magic_calls = 0

    @classmethod
    def _explode(cls, *_args: object, **_kwargs: object) -> object:
        cls.magic_calls += 1
        raise RuntimeError("private-map-scalar-canary")

    __bool__ = _explode
    __eq__ = _explode
    __float__ = _explode
    __int__ = _explode
    __str__ = _explode


class _BombMapInt(int):
    magic_calls = 0

    @classmethod
    def _explode(cls, *_args: object, **_kwargs: object) -> object:
        cls.magic_calls += 1
        raise RuntimeError("private-map-int-canary")

    __bool__ = _explode
    __eq__ = _explode
    __float__ = _explode
    __int__ = _explode
    __str__ = _explode


class _BombMapStr(str):
    magic_calls = 0

    @classmethod
    def _explode(cls, *_args: object, **_kwargs: object) -> object:
        cls.magic_calls += 1
        raise RuntimeError("private-map-str-canary")

    __bool__ = _explode
    __eq__ = _explode
    __float__ = _explode
    __int__ = _explode
    __str__ = _explode
    strip = _explode


class _BombMapDict(dict):
    magic_calls = 0

    @classmethod
    def _explode(cls, *_args: object, **_kwargs: object) -> object:
        cls.magic_calls += 1
        raise RuntimeError("private-map-dict-canary")

    __bool__ = _explode
    __contains__ = _explode
    __getitem__ = _explode
    __iter__ = _explode
    __len__ = _explode
    get = _explode
    items = _explode


class _BombMapMapping(Mapping):
    magic_calls = 0

    @classmethod
    def _explode(cls, *_args: object, **_kwargs: object) -> object:
        cls.magic_calls += 1
        raise RuntimeError("private-map-mapping-canary")

    __bool__ = _explode
    __contains__ = _explode
    __getitem__ = _explode
    __iter__ = _explode
    __len__ = _explode
    get = _explode
    items = _explode


class _CollisionMapKey:
    magic_calls = 0

    def __init__(self) -> None:
        self.armed = False

    def __hash__(self) -> int:
        return hash("map_id")

    def __eq__(self, _other: object) -> bool:
        if self.armed:
            type(self).magic_calls += 1
            raise RuntimeError("private-map-key-canary")
        return False

    def __bool__(self) -> bool:
        type(self).magic_calls += 1
        raise RuntimeError("private-map-key-canary")

    def __str__(self) -> str:
        type(self).magic_calls += 1
        raise RuntimeError("private-map-key-canary")


def _with_raw_map_values(root_map: object, context_map: object) -> dict:
    snapshot = _snapshot()
    snapshot["map_id"] = root_map
    snapshot["ui_contract"]["context"]["map_id"] = context_map
    return snapshot


def _run_default_and_rollback(
    snapshot_factory,
    monkeypatch: pytest.MonkeyPatch,
    *,
    max_combos: int = 8_000,
) -> tuple[dict, dict]:
    monkeypatch.delenv(
        engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE,
        raising=False,
    )
    default = engine.run_reference_engine(
        snapshot_factory(),
        max_combos=max_combos,
    ).as_dict()
    monkeypatch.setenv(engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE, "1")
    rollback = engine.run_reference_engine(
        snapshot_factory(),
        max_combos=max_combos,
    ).as_dict()
    return default, rollback


@pytest.mark.parametrize(
    "map_value_factory",
    [
        pytest.param(lambda: 4523.0, id="integral-float"),
        pytest.param(lambda: 4523.5, id="fractional-float"),
        pytest.param(lambda: "4523.0", id="decimal-string"),
        pytest.param(lambda: True, id="bool"),
        pytest.param(lambda: Decimal("4523"), id="decimal"),
        pytest.param(lambda: _BombMapInt(4523), id="int-subclass"),
        pytest.param(lambda: _BombMapStr("4523"), id="str-subclass"),
    ],
)
def test_current_activity_raw_map_scalar_is_exact_builtin_and_lossless(
    map_value_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def snapshot_factory() -> dict:
        map_value = map_value_factory()
        return _with_raw_map_values(map_value, map_value)

    _BombMapInt.magic_calls = 0
    _BombMapStr.magic_calls = 0
    default, rollback = _run_default_and_rollback(snapshot_factory, monkeypatch)

    assert default == rollback
    assert not any(
        note.startswith("current_activity_shipwreck_red_boost:")
        for note in default["notes"]
    )
    assert _BombMapInt.magic_calls == 0
    assert _BombMapStr.magic_calls == 0


@pytest.mark.parametrize(
    ("map_value", "expected"),
    [
        (0, ("not_activity", 0)),
        (9_999, ("not_activity", 9_999)),
        (-1, ("invalid", None)),
        (10_000, ("invalid", None)),
        (-(10**400), ("invalid", None)),
        (10**400, ("invalid", None)),
    ],
)
def test_current_activity_exact_int_map_is_bounded_like_four_digit_wire(
    map_value: int,
    expected: tuple[str, int | None],
) -> None:
    assert engine._current_activity_map_scalar(map_value) == expected


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
@pytest.mark.parametrize(
    ("root_map", "context_map"),
    [
        (10**400, 10**400),
        (-(10**400), -(10**400)),
        (10_000, 4523),
        (4523, -1),
    ],
)
def test_current_activity_out_of_range_int_owners_are_atomic_noop(
    root_map: int,
    context_map: int,
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def snapshot_factory() -> dict:
        snapshot = _with_raw_map_values(root_map, context_map)
        root_items = [
            (key, value)
            for key, value in snapshot.items()
            if key != "map_id"
        ]
        context = snapshot["ui_contract"]["context"]
        context_items = [
            (key, value)
            for key, value in context.items()
            if key != "map_id"
        ]
        if getattr(snapshot_factory, "reverse", False):
            snapshot = dict(root_items + [("map_id", root_map)])
            context = dict(context_items + [("map_id", context_map)])
        else:
            snapshot = dict([("map_id", root_map)] + root_items)
            context = dict([("map_id", context_map)] + context_items)
        snapshot["ui_contract"]["context"] = context
        snapshot_factory.reverse = not getattr(
            snapshot_factory,
            "reverse",
            False,
        )
        return snapshot

    default, rollback = _run_default_and_rollback(
        snapshot_factory,
        monkeypatch,
        max_combos=max_combos,
    )

    assert default == rollback
    assert default["status"] != "error"
    assert not any(
        note.startswith("current_activity_shipwreck_red_boost:")
        for note in default["notes"]
    )


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
@pytest.mark.parametrize(
    ("root_map", "context_map"),
    [
        (4523.5, 4523),
        (4523, 2523),
        ("4523", 2503),
    ],
)
def test_current_activity_raw_map_invalid_or_conflicting_owners_are_noop(
    root_map: object,
    context_map: object,
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def snapshot_factory() -> dict:
        snapshot = _with_raw_map_values(root_map, context_map)
        root_items = [
            (key, value)
            for key, value in snapshot.items()
            if key != "map_id"
        ]
        context = snapshot["ui_contract"]["context"]
        context_items = [
            (key, value)
            for key, value in context.items()
            if key != "map_id"
        ]
        # Alternate insertion order across the two product runs.  The decision
        # must depend on owner semantics, not dictionary order.
        if getattr(snapshot_factory, "reverse", False):
            snapshot = dict(root_items + [("map_id", root_map)])
            context = dict(context_items + [("map_id", context_map)])
        else:
            snapshot = dict([("map_id", root_map)] + root_items)
            context = dict([("map_id", context_map)] + context_items)
        snapshot["ui_contract"]["context"] = context
        snapshot_factory.reverse = not getattr(
            snapshot_factory,
            "reverse",
            False,
        )
        return snapshot

    default, rollback = _run_default_and_rollback(
        snapshot_factory,
        monkeypatch,
        max_combos=max_combos,
    )
    assert default == rollback


@pytest.mark.parametrize(
    "snapshot_factory",
    [
        lambda: _BombMapDict(_snapshot()),
        lambda: (
            lambda snapshot: (
                snapshot["ui_contract"].__setitem__(
                    "context",
                    _BombMapMapping(),
                )
                or snapshot
            )
        )(_snapshot()),
        lambda: _with_raw_map_values(_BombMapScalar(), _BombMapScalar()),
    ],
)
def test_current_activity_raw_map_owner_protocols_are_not_executed(
    snapshot_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _BombMapScalar.magic_calls = 0
    _BombMapDict.magic_calls = 0
    _BombMapMapping.magic_calls = 0
    default, rollback = _run_default_and_rollback(snapshot_factory, monkeypatch)
    monkeypatch.setattr(
        panel.time,
        "time",
        lambda: float(
            engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH + 2
        ),
    )
    monkeypatch.delenv(
        engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE,
        raising=False,
    )
    default_summary = panel.summarize_snapshot(
        snapshot_factory(),
        snapshot_path=tmp_path / "nomagic-map-summary.json",
    )
    monkeypatch.setenv(engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE, "1")
    rollback_summary = panel.summarize_snapshot(
        snapshot_factory(),
        snapshot_path=tmp_path / "nomagic-map-rollback.json",
    )

    assert default == rollback
    assert default_summary["ahmed_ref"] == rollback_summary["ahmed_ref"]
    assert _BombMapScalar.magic_calls == 0
    assert _BombMapDict.magic_calls == 0
    assert _BombMapMapping.magic_calls == 0


def test_current_activity_raw_map_collision_key_is_no_magic_noop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def snapshot_factory() -> dict:
        snapshot = _snapshot()
        context = snapshot["ui_contract"]["context"]
        del context["map_id"]
        collision_key = _CollisionMapKey()
        context[collision_key] = 4523
        collision_key.armed = True
        return snapshot

    _CollisionMapKey.magic_calls = 0
    default, rollback = _run_default_and_rollback(snapshot_factory, monkeypatch)
    monkeypatch.setattr(
        panel.time,
        "time",
        lambda: float(
            engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH + 2
        ),
    )
    monkeypatch.delenv(
        engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE,
        raising=False,
    )
    default_summary = panel.summarize_snapshot(
        snapshot_factory(),
        snapshot_path=tmp_path / "collision-map-summary.json",
    )
    monkeypatch.setenv(engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE, "1")
    rollback_summary = panel.summarize_snapshot(
        snapshot_factory(),
        snapshot_path=tmp_path / "collision-map-rollback.json",
    )

    assert default == rollback
    assert default_summary["ahmed_ref"] == rollback_summary["ahmed_ref"]
    assert _CollisionMapKey.magic_calls == 0


@pytest.mark.parametrize(
    ("root_map", "context_map"),
    [(4523, "4523"), ("2523", 2523)],
)
def test_current_activity_exact_int_and_ascii_string_owners_share_one_map(
    root_map: object,
    context_map: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE,
        raising=False,
    )
    result = engine.run_reference_engine(
        _with_raw_map_values(root_map, context_map),
        max_combos=8_000,
    ).as_dict()

    assert "current_activity_shipwreck_red_boost:1.5" in result["notes"]


def test_current_activity_2523_keeps_raw_map_decision_across_source_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legal_default, legal_rollback = _run_default_and_rollback(
        lambda: _with_raw_map_values(2523, 2523),
        monkeypatch,
    )
    assert legal_default != legal_rollback
    assert "current_activity_shipwreck_red_boost:1.5" in legal_default["notes"]

    def invalid_snapshot() -> dict:
        snapshot = _with_raw_map_values(2523, 2523)
        context = _BombMapDict()
        dict.update(context, snapshot["ui_contract"]["context"])
        snapshot["ui_contract"]["context"] = context
        return snapshot

    _BombMapDict.magic_calls = 0
    invalid_default, invalid_rollback = _run_default_and_rollback(
        invalid_snapshot,
        monkeypatch,
    )
    assert invalid_default == invalid_rollback
    assert not any(
        note.startswith("current_activity_shipwreck_red_boost:")
        for note in invalid_default["notes"]
    )
    assert _BombMapDict.magic_calls == 0


def test_current_activity_invalid_float_is_consistent_in_summary_api_and_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamp = engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH + 60

    def snapshot_factory() -> dict:
        return _with_raw_map_values(4523.5, 4523.5)

    monkeypatch.setattr(panel.time, "time", lambda: float(timestamp + 1))
    monkeypatch.delenv(
        engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE,
        raising=False,
    )
    direct = engine.run_reference_engine(
        snapshot_factory(),
        max_combos=8_000,
    ).as_dict()
    summary = panel.summarize_snapshot(
        snapshot_factory(),
        snapshot_path=tmp_path / "invalid-map-summary.json",
    )

    monkeypatch.setenv(engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE, "1")
    rollback = engine.run_reference_engine(
        snapshot_factory(),
        max_combos=8_000,
    ).as_dict()
    rollback_summary = panel.summarize_snapshot(
        snapshot_factory(),
        snapshot_path=tmp_path / "invalid-map-rollback.json",
    )

    assert direct == rollback
    assert summary["ahmed_ref"] == rollback_summary["ahmed_ref"]
    assert detail_composition_view(summary) == detail_composition_view(
        rollback_summary
    )

    monkeypatch.delenv(
        engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE,
        raising=False,
    )
    monkeypatch.setattr(panel, "_read_json", lambda _path: snapshot_factory())
    config = panel.ServerConfig(
        project_root=tmp_path,
        snapshot=Path("snapshot.json"),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), panel.make_handler(config))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with urlopen(  # noqa: S310 - loopback-only product regression
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

    assert status == 200
    assert payload["ahmed_ref"] == summary["ahmed_ref"]


@pytest.mark.parametrize("map_value", [-(10**400), 10**400])
def test_current_activity_huge_int_is_no_throw_in_all_product_consumers(
    map_value: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamp = engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_START_EPOCH + 60

    def snapshot_factory() -> dict:
        return _with_raw_map_values(map_value, map_value)

    monkeypatch.setattr(panel.time, "time", lambda: float(timestamp + 1))
    monkeypatch.delenv(
        engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE,
        raising=False,
    )
    direct = engine.run_reference_engine(
        snapshot_factory(),
        max_combos=8_000,
    ).as_dict()
    summary = panel.summarize_snapshot(
        snapshot_factory(),
        snapshot_path=tmp_path / "huge-map-summary.json",
    )

    monkeypatch.setenv(engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE, "1")
    rollback = engine.run_reference_engine(
        snapshot_factory(),
        max_combos=8_000,
    ).as_dict()
    rollback_summary = panel.summarize_snapshot(
        snapshot_factory(),
        snapshot_path=tmp_path / "huge-map-rollback.json",
    )

    assert direct == rollback
    assert direct["status"] != "error"
    assert summary["ahmed_ref"] == rollback_summary["ahmed_ref"]
    assert summary["ahmed_ref"]["status"] != "error"
    assert detail_composition_view(summary) == detail_composition_view(
        rollback_summary
    )

    monkeypatch.delenv(
        engine.CURRENT_ACTIVITY_SHIPWRECK_RED_BOOST_DISABLE,
        raising=False,
    )
    monkeypatch.setattr(panel, "_read_json", lambda _path: snapshot_factory())
    config = panel.ServerConfig(
        project_root=tmp_path,
        snapshot=Path("snapshot.json"),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), panel.make_handler(config))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with urlopen(  # noqa: S310 - loopback-only product regression
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

    assert status == 200
    assert payload["ahmed_ref"] == summary["ahmed_ref"]
    assert detail_composition_view(payload) == detail_composition_view(summary)
