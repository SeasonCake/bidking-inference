"""Aisha direct-grid public-average physical-domain regression for 0.3.1.

Sanitized from HeroRefDiag-20260728-194243-4501_9000000000000035.
At bidding R3 the exact total count/grid residual leaves q4+q6 at 11 items
and 33 cells.  Public q4 average 2.75 permits q4 only at 4/11 or 8/22;
the old direct enumerator nevertheless published q4 6/7/9/10/11 arms.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from fractions import Fraction
import math
import sys
import time
from collections import UserDict
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ahmad_live_panel_server import summarize_snapshot  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    AISHA_PUBLIC_AVG_DIRECT_GRID_DOMAIN_DISABLE,
    AISHA_PUBLIC_AVG_DIRECT_GRID_DOMAIN_NOTE,
    AISHA_PUBLIC_AVG_SEMANTIC_CONFLICT_DISABLE,
    AISHA_PUBLIC_AVG_SEMANTIC_CONFLICT_NOTE_PREFIX,
    PUBLIC_AVG_JSON_MAX_CONTAINER_ITEMS,
    PUBLIC_AVG_JSON_MAX_DEPTH,
    PUBLIC_AVG_JSON_MAX_NODES,
    _stable_public_avg_json_projection,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)


SESSION_ID = "4501:9000000000000035"
QUALIFICATION = "postupdate_unknown_warehouse_source"


def _warehouse_source() -> dict:
    return {
        "qualification": QUALIFICATION,
        "official_distribution_eligible": False,
        "quote_policy": "advisory_only_no_clamp",
    }


def _snapshot(
    *,
    hero: str = "aisha",
    phase: str = "bidding",
    q4_avg: float | None = 2.75,
    total_cells: int | None = 92,
) -> dict:
    source = _warehouse_source()
    public_facts = []
    if q4_avg is not None:
        public_facts.append(
            {
                "info_id": 200013,
                "semantic": "q4_avg_cells",
                "kind": "avg_cells",
                "quality": 4,
                "value": q4_avg,
                "display_value": str(q4_avg),
            }
        )
    structured = {
        "source": "monitor_pre_settlement_field_updates",
        "hero": hero,
        "total_count": 39,
        "counts": {"q3": 11, "q5": 2},
        "quality_cells": {"q3": 21, "q5": 10},
        "split_counts": {"white": 7, "green": 8},
        "split_quality_cells": {"white": 16, "green": 12},
    }
    if total_cells is not None:
        structured["total_cells"] = total_cells
    skill_rows = [
        {
            "sort": 16,
            "skill_id": skill_id,
            "hero_id": 103,
            "revealed_items": count,
            "revealed_items_detail": [],
        }
        for skill_id, count in ((1001034, 7), (1001033, 8), (1001032, 11))
    ]
    return {
        "created_at": time.time(),
        "hero": hero,
        "map_id": 4501,
        "round": 3,
        "phase": phase,
        "session_id": SESSION_ID,
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": QUALIFICATION,
        "official_distribution_eligible": False,
        "skill_reveal_rows": skill_rows if hero == "aisha" else [],
        "structured_ref_inputs": structured,
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": 4501,
                "round": 3,
                "phase": phase,
                "session_id": SESSION_ID,
                "warehouse_source": copy.deepcopy(source),
                "warehouse_source_qualification": QUALIFICATION,
                "official_distribution_eligible": False,
            },
            "constraints": {
                "public_info": {
                    "public_numeric_facts": public_facts,
                }
            },
            "actions": {"results": []},
            "source": {"created_at": time.time()},
        },
    }


def _run(snapshot: dict, *, max_combos: int = 8_000) -> dict:
    return run_reference_engine(
        prepare_reference_engine_snapshot(snapshot),
        max_combos=max_combos,
    ).as_dict()


def _q4_joint(result: dict) -> list[tuple[int, int]]:
    return [
        (int(row["count"]), int(row["cells"]))
        for row in result["quality_count_cells_candidates"]["q4"]
    ]


def _append_public_q4_avg(snapshot: dict, value: float) -> None:
    _append_public_avg(snapshot, "q4_avg_cells", value)


def _append_public_avg(
    snapshot: dict,
    semantic: str,
    value: object,
) -> None:
    rows = snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ]
    rows.append(
        {
            "info_id": 200013,
            "semantic": semantic,
            "kind": "avg_cells",
            "quality": int(semantic[1]),
            "value": value,
            "display_value": str(value),
        }
    )


def _append_public_avg_without_coercion(
    snapshot: dict,
    semantic: str,
    value: object,
) -> None:
    rows = snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ]
    rows.append(
        {
            "info_id": 200013,
            "semantic": semantic,
            "kind": "avg_cells",
            "quality": int(semantic[1]),
            "value": value,
            "display_value": "present-invalid",
        }
    )


class _ExplosiveDict(dict):
    magic_calls = 0

    def _explode(self) -> None:
        type(self).magic_calls += 1
        raise RuntimeError("public avg mapping magic method must not run")

    def __str__(self) -> str:
        self._explode()

    def __float__(self) -> float:
        self._explode()

    def __eq__(self, _other: object) -> bool:
        self._explode()

    def get(self, *_args, **_kwargs):
        self._explode()

    def items(self):
        self._explode()

    def __iter__(self):
        self._explode()

    def __len__(self) -> int:
        self._explode()

    def __contains__(self, _key: object) -> bool:
        self._explode()


class _TextDict(dict):
    magic_calls = 0

    def __str__(self) -> str:
        type(self).magic_calls += 1
        return "2.75"

    def __float__(self) -> float:
        type(self).magic_calls += 1
        raise RuntimeError("public avg mapping float must not run")


class _ExplosiveContainerDict(dict):
    magic_calls = 0

    def _explode(self) -> None:
        type(self).magic_calls += 1
        raise RuntimeError("public avg container magic method must not run")

    def get(self, *_args, **_kwargs):
        self._explode()

    def items(self):
        self._explode()

    def __iter__(self):
        self._explode()

    def __len__(self) -> int:
        self._explode()

    def __contains__(self, _key: object) -> bool:
        self._explode()


class _ExplosiveList(list):
    magic_calls = 0

    def __str__(self) -> str:
        type(self).magic_calls += 1
        raise RuntimeError("public avg list magic method must not run")

    def __float__(self) -> float:
        type(self).magic_calls += 1
        raise RuntimeError("public avg list float must not run")

    def __iter__(self):
        type(self).magic_calls += 1
        raise RuntimeError("public avg list iteration must not run")

    def __len__(self) -> int:
        type(self).magic_calls += 1
        raise RuntimeError("public avg list length must not run")

    def __contains__(self, _item: object) -> bool:
        type(self).magic_calls += 1
        raise RuntimeError("public avg list contains must not run")


class _ExplosiveStr(str):
    magic_calls = 0

    def __str__(self) -> str:
        type(self).magic_calls += 1
        raise RuntimeError("public avg str subclass magic method must not run")

    def __float__(self) -> float:
        type(self).magic_calls += 1
        raise RuntimeError("public avg str subclass float must not run")


class _ExplosiveInt(int):
    magic_calls = 0

    def __str__(self) -> str:
        type(self).magic_calls += 1
        raise RuntimeError("public avg int subclass magic method must not run")

    def __float__(self) -> float:
        type(self).magic_calls += 1
        raise RuntimeError("public avg int subclass float must not run")


class _ExplosiveFloat(float):
    magic_calls = 0

    def __str__(self) -> str:
        type(self).magic_calls += 1
        raise RuntimeError("public avg float subclass magic method must not run")

    def __float__(self) -> float:
        type(self).magic_calls += 1
        raise RuntimeError("public avg float subclass float must not run")


class _ExplosiveObject:
    magic_calls = 0

    def __str__(self) -> str:
        type(self).magic_calls += 1
        raise RuntimeError("public avg object string must not run")

    def __float__(self) -> float:
        type(self).magic_calls += 1
        raise RuntimeError("public avg object float must not run")

    def __eq__(self, _other: object) -> bool:
        type(self).magic_calls += 1
        raise RuntimeError("public avg object compare must not run")

    def __lt__(self, _other: object) -> bool:
        type(self).magic_calls += 1
        raise RuntimeError("public avg object compare must not run")


class _ExplosiveScopeValue:
    magic_calls = 0

    def _explode(self) -> None:
        type(self).magic_calls += 1
        raise RuntimeError("public avg scope value protocol must not run")

    def __bool__(self) -> bool:
        self._explode()

    def __str__(self) -> str:
        self._explode()

    def __float__(self) -> float:
        self._explode()

    def __eq__(self, _other: object) -> bool:
        self._explode()

    def strip(self) -> str:
        self._explode()


class _ExplosiveScopeStr(str):
    magic_calls = 0

    def _explode(self) -> None:
        type(self).magic_calls += 1
        raise RuntimeError("public avg scope str protocol must not run")

    def __bool__(self) -> bool:
        self._explode()

    def __str__(self) -> str:
        self._explode()

    def __float__(self) -> float:
        self._explode()

    def __eq__(self, _other: object) -> bool:
        self._explode()

    def strip(self) -> str:
        self._explode()


class _ExplosiveScopeInt(int):
    magic_calls = 0

    def _explode(self) -> None:
        type(self).magic_calls += 1
        raise RuntimeError("public avg scope int protocol must not run")

    def __bool__(self) -> bool:
        self._explode()

    def __str__(self) -> str:
        self._explode()

    def __float__(self) -> float:
        self._explode()

    def __int__(self) -> int:
        self._explode()

    def __eq__(self, _other: object) -> bool:
        self._explode()


class _ExplosiveScopeFloat(float):
    magic_calls = 0

    def _explode(self) -> None:
        type(self).magic_calls += 1
        raise RuntimeError("public avg scope float protocol must not run")

    def __bool__(self) -> bool:
        self._explode()

    def __str__(self) -> str:
        self._explode()

    def __float__(self) -> float:
        self._explode()

    def __int__(self) -> int:
        self._explode()

    def __eq__(self, _other: object) -> bool:
        self._explode()


class _HashCollisionKey:
    magic_calls = 0

    def __init__(self, target: str) -> None:
        self.target = target
        self.armed = False

    def __hash__(self) -> int:
        return hash(self.target)

    def __eq__(self, _other: object) -> bool:
        if self.armed:
            type(self).magic_calls += 1
            raise RuntimeError("public avg hash-collision compare must not run")
        return False


def _inject_hash_collision_key(
    snapshot: dict,
    location: str,
) -> _HashCollisionKey:
    ui_contract = snapshot["ui_contract"]
    if location == "ui-root":
        container = ui_contract
        target = "context"
        parent = snapshot
        parent_key = "ui_contract"
    elif location == "context":
        container = ui_contract["context"]
        target = "hero"
        parent = ui_contract
        parent_key = "context"
    elif location == "constraints":
        container = ui_contract["constraints"]
        target = "public_info"
        parent = ui_contract
        parent_key = "constraints"
    elif location == "public_info":
        container = ui_contract["constraints"]["public_info"]
        target = "public_numeric_facts"
        parent = ui_contract["constraints"]
        parent_key = "public_info"
    elif location == "fact_row":
        rows = ui_contract["constraints"]["public_info"][
            "public_numeric_facts"
        ]
        container = rows[0]
        target = "semantic"
        parent = rows
        parent_key = 0
    else:
        raise AssertionError(f"unsupported collision location: {location}")

    collision = _HashCollisionKey(target)
    replacement = {collision: "invalid-key", **container}
    parent[parent_key] = replacement
    collision.armed = True
    return collision


def _inject_scope_protocol_value(
    snapshot: dict,
    location: str,
) -> _ExplosiveScopeValue:
    value = _ExplosiveScopeValue()
    if location in {"hero", "phase", "map_id"}:
        snapshot["ui_contract"]["context"][location] = value
    elif location in {"quality", "value"}:
        rows = snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ]
        rows[0][location] = value
    else:
        raise AssertionError(f"unsupported scope value location: {location}")
    return value


def _inject_top_level_scope_fallback(
    snapshot: dict,
    *,
    location: str,
    context_mode: str,
) -> object:
    context = snapshot["ui_contract"]["context"]
    if context_mode == "none":
        context[location] = None
    elif context_mode == "missing":
        context.pop(location)
    else:
        raise AssertionError(f"unsupported context mode: {context_mode}")

    if location in {"hero", "phase"}:
        raw = "aisha" if location == "hero" else "bidding"
        value: object = _ExplosiveScopeStr(raw)
    elif location == "map_id":
        value = _ExplosiveScopeInt(4501)
    else:
        raise AssertionError(f"unsupported fallback location: {location}")
    snapshot[location] = value
    return value


def _without_public_avg(snapshot: dict) -> dict:
    missing = copy.deepcopy(snapshot)
    missing["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ].clear()
    return missing


def _assert_ref_matches_missing(result: dict, missing: dict) -> None:
    for key in (
        "conservative",
        "balanced",
        "aggressive",
        "combo_count",
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_count_ranges",
        "quality_cells_ranges",
        "quality_lock",
    ):
        assert result[key] == missing[key]


def _assert_product_matches_missing(
    snapshot: dict,
    *,
    tmp_path: Path,
    name: str,
    missing_snapshot: dict | None = None,
) -> None:
    _assert_product_matches_reference(
        snapshot,
        reference_snapshot=missing_snapshot or _snapshot(q4_avg=None),
        tmp_path=tmp_path,
        name=name,
    )


def _assert_product_matches_reference(
    snapshot: dict,
    *,
    reference_snapshot: dict,
    tmp_path: Path,
    name: str,
) -> None:
    direct = _run(snapshot)
    reference_direct = _run(copy.deepcopy(reference_snapshot))
    _assert_ref_matches_missing(direct, reference_direct)

    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / f"{name}.json",
    )
    reference_summary = summarize_snapshot(
        copy.deepcopy(reference_snapshot),
        snapshot_path=tmp_path / f"{name}-reference.json",
    )
    _assert_ref_matches_missing(
        summary["ahmed_ref"],
        reference_summary["ahmed_ref"],
    )
    assert summary["quality_composition"] == reference_summary["quality_composition"]
    assert summary["red"] == reference_summary["red"]


def _json_node_count(value: object) -> int:
    if type(value) is dict:  # noqa: E721 - mirrors exact builtin boundary
        return 1 + sum(_json_node_count(child) for child in value.values())
    if type(value) is list:  # noqa: E721 - mirrors exact builtin boundary
        return 1 + sum(_json_node_count(child) for child in value)
    return 1


def _padding_tree_with_nodes(node_count: int) -> object:
    assert node_count > 0
    if node_count == 1:
        return 0
    padding: list[object] = []
    remaining = node_count - 1
    while remaining > PUBLIC_AVG_JSON_MAX_CONTAINER_ITEMS:
        child_nodes = min(
            remaining,
            PUBLIC_AVG_JSON_MAX_CONTAINER_ITEMS + 1,
        )
        padding.append([0] * (child_nodes - 1))
        remaining -= child_nodes
    padding.extend([0] * remaining)
    assert len(padding) <= PUBLIC_AVG_JSON_MAX_CONTAINER_ITEMS
    assert _json_node_count(padding) == node_count
    return padding


def _snapshot_at_json_node_budget(*, extra_nodes: int) -> dict:
    snapshot = _snapshot()
    ui_contract = snapshot["ui_contract"]
    base_nodes = _json_node_count(ui_contract)
    ui_contract["padding"] = _padding_tree_with_nodes(
        PUBLIC_AVG_JSON_MAX_NODES - base_nodes + extra_nodes
    )
    assert _json_node_count(ui_contract) == PUBLIC_AVG_JSON_MAX_NODES + extra_nodes
    return snapshot


def _large_padding() -> list:
    return [
        [0] * 64
        for _index in range(PUBLIC_AVG_JSON_MAX_NODES // 64 + 1)
    ]


def _overbudget_ui_contract_snapshot(*, placement: str) -> dict:
    snapshot = _snapshot()
    ui_contract = snapshot["ui_contract"]
    padding = _large_padding()
    if placement == "root-first":
        snapshot["ui_contract"] = {
            "padding": padding,
            **ui_contract,
        }
    elif placement == "root-middle":
        snapshot["ui_contract"] = {
            "context": ui_contract["context"],
            "padding": padding,
            **{
                key: value
                for key, value in ui_contract.items()
                if key != "context"
            },
        }
    elif placement == "root-last":
        snapshot["ui_contract"] = {
            **ui_contract,
            "padding": padding,
        }
    elif placement == "nested-first":
        ui_contract["source"] = {
            "padding": padding,
            **ui_contract["source"],
        }
    elif placement == "nested-last":
        ui_contract["source"] = {
            **ui_contract["source"],
            "padding": padding,
        }
    elif placement == "facts-first":
        ui_contract["constraints"]["public_info"]["public_numeric_facts"].insert(
            0,
            {"padding": padding},
        )
    elif placement == "facts-last":
        ui_contract["constraints"]["public_info"]["public_numeric_facts"].append(
            {"padding": padding},
        )
    else:
        raise AssertionError(f"unsupported placement: {placement}")
    return snapshot


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_4501_public_q4_avg_direct_grid_keeps_only_physical_arms(
    max_combos: int,
) -> None:
    result = _run(_snapshot(), max_combos=max_combos)

    assert result["status"] == "ok"
    assert result["combo_count"] == 2
    assert _q4_joint(result) == [(8, 22), (4, 11)]
    assert result["quality_count_cells_candidates"]["q6"] == [
        {"count": 3, "cells": 11, "prob": 0.9857},
        {"count": 7, "cells": 22, "prob": 0.0143},
    ]
    assert result["quality_lock"]["q4"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert result["evidence"]["soft_avg_cell_keys"] == ["q4"]
    assert AISHA_PUBLIC_AVG_DIRECT_GRID_DOMAIN_NOTE in result["notes"]
    assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
        214_802,
        783_547,
        857_047,
    ]


@pytest.mark.parametrize("reverse", [False, True])
def test_4501_conflicting_duplicate_q4_avg_is_order_invariant_noop(
    reverse: bool,
) -> None:
    snapshot = _snapshot()
    _append_public_q4_avg(snapshot, 3.0)
    if reverse:
        facts = snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ]
        facts.reverse()

    result = _run(snapshot)
    missing = _run(_snapshot(q4_avg=None))

    assert _q4_joint(result) == _q4_joint(missing)
    assert [
        result[key] for key in ("conservative", "balanced", "aggressive")
    ] == [
        missing[key] for key in ("conservative", "balanced", "aggressive")
    ]
    for key in (
        "combo_count",
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_count_ranges",
        "quality_cells_ranges",
    ):
        assert result[key] == missing[key]
    assert result["quality_lock"] == missing["quality_lock"]
    assert (
        f"{AISHA_PUBLIC_AVG_SEMANTIC_CONFLICT_NOTE_PREFIX}:q4"
        in result["notes"]
    )
    assert "public_q4_avg_cells" not in result["evidence"]["source_notes"]
    assert AISHA_PUBLIC_AVG_DIRECT_GRID_DOMAIN_NOTE not in result["notes"]


@pytest.mark.parametrize(
    ("semantic", "quality", "valid_value"),
    [
        ("q4_avg_cells", "q4", 2.75),
        ("q5_avg_cells", "q5", 5.0),
        ("q6_avg_cells", "q6", 3.0),
    ],
)
@pytest.mark.parametrize(
    "invalid_value",
    [
        math.nan,
        math.inf,
        -math.inf,
        True,
        None,
        "bad",
        {"bad": 1},
        10**400,
        -(10**400),
        251.0,
        -1,
        "1e999",
        "9" * 65,
        " 2.75",
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_4501_present_invalid_public_avg_semantic_is_order_invariant_noop(
    semantic: str,
    quality: str,
    valid_value: float,
    invalid_value: object,
    reverse: bool,
) -> None:
    snapshot = _snapshot()
    if quality != "q4":
        _append_public_avg(snapshot, semantic, valid_value)
    _append_public_avg(snapshot, semantic, invalid_value)
    if reverse:
        rows = snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ]
        matching = [row for row in rows if row["semantic"] == semantic]
        remainder = [row for row in rows if row["semantic"] != semantic]
        snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ] = [*remainder, *reversed(matching)]

    result = _run(snapshot)
    missing = _run(_snapshot(q4_avg=None) if quality == "q4" else _snapshot())

    for key in (
        "conservative",
        "balanced",
        "aggressive",
        "combo_count",
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_count_ranges",
        "quality_cells_ranges",
        "quality_lock",
    ):
        assert result[key] == missing[key]
    assert (
        f"{AISHA_PUBLIC_AVG_SEMANTIC_CONFLICT_NOTE_PREFIX}:{quality}"
        in result["notes"]
    )
    assert f"public_{quality}_avg_cells" not in result["evidence"]["source_notes"]


@pytest.mark.parametrize(
    "factory",
    [
        _TextDict,
        _ExplosiveDict,
        _ExplosiveList,
        lambda: (2.75,),
        lambda: {2.75},
        lambda: _ExplosiveStr("2.75"),
        lambda: _ExplosiveInt(2),
        lambda: _ExplosiveFloat(2.75),
        lambda: UserDict({"value": 2.75}),
        lambda: Decimal("2.75"),
        lambda: Fraction(11, 4),
        _ExplosiveObject,
    ],
    ids=[
        "dict-text",
        "dict-explosive",
        "list-explosive",
        "tuple",
        "set",
        "str-subclass",
        "int-subclass",
        "float-subclass",
        "mapping",
        "decimal",
        "fraction",
        "object-explosive",
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_4501_non_protocol_value_is_stable_across_direct_summary_and_mini(
    factory,
    reverse: bool,
    tmp_path: Path,
) -> None:
    invalid_value = factory()
    snapshot = _snapshot()
    _append_public_avg_without_coercion(
        snapshot,
        "q4_avg_cells",
        invalid_value,
    )
    if reverse:
        snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ].reverse()

    direct = _run(snapshot)
    direct_missing = _run(_snapshot(q4_avg=None))
    _assert_ref_matches_missing(direct, direct_missing)

    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / f"stable-invalid-{int(reverse)}.json",
    )
    missing_summary = summarize_snapshot(
        _snapshot(q4_avg=None),
        snapshot_path=tmp_path / "stable-invalid-missing.json",
    )
    assert summary["ahmed_ref"]["status"] == "ok"
    _assert_ref_matches_missing(
        summary["ahmed_ref"],
        missing_summary["ahmed_ref"],
    )
    assert summary["quality_composition"] == missing_summary["quality_composition"]
    assert summary["red"] == missing_summary["red"]
    assert (
        f"{AISHA_PUBLIC_AVG_SEMANTIC_CONFLICT_NOTE_PREFIX}:q4"
        in direct["notes"]
    )
    if hasattr(invalid_value, "magic_calls"):
        assert type(invalid_value).magic_calls == 0


@pytest.mark.parametrize(
    ("semantic", "valid_value"),
    [
        ("q4_avg_cells", 2.75),
        ("q5_avg_cells", 5.0),
        ("q6_avg_cells", 3.0),
    ],
)
def test_4501_q4_q5_q6_invalid_mapping_has_same_product_payload_as_missing(
    semantic: str,
    valid_value: float,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    if semantic != "q4_avg_cells":
        _append_public_avg_without_coercion(snapshot, semantic, valid_value)
    invalid_value = _TextDict()
    _append_public_avg_without_coercion(snapshot, semantic, invalid_value)
    missing_snapshot = (
        _snapshot(q4_avg=None)
        if semantic == "q4_avg_cells"
        else _snapshot()
    )

    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / f"{semantic}-mapping-invalid.json",
    )
    missing = summarize_snapshot(
        missing_snapshot,
        snapshot_path=tmp_path / f"{semantic}-missing.json",
    )

    _assert_ref_matches_missing(summary["ahmed_ref"], missing["ahmed_ref"])
    assert summary["quality_composition"] == missing["quality_composition"]
    assert summary["red"] == missing["red"]
    assert _TextDict.magic_calls == 0


@pytest.mark.parametrize(
    ("wire_value", "numeric_value"),
    [
        (2, 2.0),
        (2.75, 2.75),
        ("2.75", 2.75),
        ("+2.75", 2.75),
        ("2.75e0", 2.75),
    ],
)
def test_4501_builtin_json_scalar_public_avg_remains_accepted(
    wire_value: object,
    numeric_value: float,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(q4_avg=None)
    _append_public_avg_without_coercion(snapshot, "q4_avg_cells", wire_value)
    expected = _run(_snapshot(q4_avg=numeric_value))

    direct = _run(snapshot)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "valid-json-scalar.json",
    )

    assert direct == expected
    for key in (
        "conservative",
        "balanced",
        "aggressive",
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
    ):
        assert summary["ahmed_ref"][key] == expected[key]
    assert (
        f"{AISHA_PUBLIC_AVG_SEMANTIC_CONFLICT_NOTE_PREFIX}:q4"
        not in direct["notes"]
    )


@pytest.mark.parametrize("invalid_value", [math.nan, math.inf, -math.inf, True, "bad"])
@pytest.mark.parametrize("reverse", [False, True])
def test_4501_present_invalid_public_avg_product_payload_is_noop(
    invalid_value: object,
    reverse: bool,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    _append_public_avg(snapshot, "q4_avg_cells", invalid_value)
    if reverse:
        snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ].reverse()

    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / f"invalid-{invalid_value!s}-{int(reverse)}.json",
    )
    missing = summarize_snapshot(
        _snapshot(q4_avg=None),
        snapshot_path=tmp_path / "missing-invalid.json",
    )

    assert summary["ahmed_ref"]["status"] == "ok"
    for key in (
        "conservative",
        "balanced",
        "aggressive",
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
    ):
        assert summary["ahmed_ref"][key] == missing["ahmed_ref"][key]
    assert summary["red"] == missing["red"]


def test_4501_present_invalid_public_avg_has_independent_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forward = _snapshot()
    _append_public_avg(forward, "q4_avg_cells", math.nan)
    reverse = copy.deepcopy(forward)
    reverse["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ].reverse()

    monkeypatch.setenv(AISHA_PUBLIC_AVG_SEMANTIC_CONFLICT_DISABLE, "1")
    missing = _run(_snapshot(q4_avg=None))
    _assert_ref_matches_missing(_run(forward), missing)
    _assert_ref_matches_missing(_run(reverse), missing)


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_4501_mapping_invalid_is_fail_closed_in_default_and_rollback(
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    invalid_value = _TextDict()
    _append_public_avg_without_coercion(
        snapshot,
        "q4_avg_cells",
        invalid_value,
    )
    missing = _run(_snapshot(q4_avg=None), max_combos=max_combos)

    _assert_ref_matches_missing(
        _run(snapshot, max_combos=max_combos),
        missing,
    )
    monkeypatch.setenv(AISHA_PUBLIC_AVG_SEMANTIC_CONFLICT_DISABLE, "1")
    _assert_ref_matches_missing(
        _run(snapshot, max_combos=max_combos),
        missing,
    )
    assert _TextDict.magic_calls == 0


@pytest.mark.parametrize("rollback", [False, True])
def test_4501_ui_contract_dict_subclass_never_runs_container_magic(
    rollback: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    snapshot["ui_contract"] = _ExplosiveContainerDict(snapshot["ui_contract"])
    _ExplosiveContainerDict.magic_calls = 0
    if rollback:
        monkeypatch.setenv(AISHA_PUBLIC_AVG_SEMANTIC_CONFLICT_DISABLE, "1")

    _assert_product_matches_missing(
        snapshot,
        tmp_path=tmp_path,
        name=f"explosive-ui-contract-{int(rollback)}",
    )
    assert _ExplosiveContainerDict.magic_calls == 0


@pytest.mark.parametrize(
    "location",
    ["constraints", "public_info", "public_numeric_facts", "fact_row"],
)
def test_4501_nested_container_subclass_never_runs_container_magic(
    location: str,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    ui_contract = snapshot["ui_contract"]
    constraints = ui_contract["constraints"]
    public_info = constraints["public_info"]
    rows = public_info["public_numeric_facts"]
    if location == "constraints":
        ui_contract["constraints"] = _ExplosiveContainerDict(constraints)
    elif location == "public_info":
        constraints["public_info"] = _ExplosiveContainerDict(public_info)
    elif location == "public_numeric_facts":
        public_info["public_numeric_facts"] = _ExplosiveList(rows)
    else:
        rows[0] = _ExplosiveContainerDict(rows[0])
    _ExplosiveContainerDict.magic_calls = 0
    _ExplosiveList.magic_calls = 0

    _assert_product_matches_missing(
        snapshot,
        tmp_path=tmp_path,
        name=f"explosive-{location}",
    )
    assert _ExplosiveContainerDict.magic_calls == 0
    assert _ExplosiveList.magic_calls == 0


@pytest.mark.parametrize(
    ("semantic", "valid_value"),
    [
        ("q4_avg_cells", 2.75),
        ("q5_avg_cells", 5.0),
        ("q6_avg_cells", 3.0),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_4501_explosive_mapping_value_is_order_invariant_product_noop(
    semantic: str,
    valid_value: float,
    reverse: bool,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    if semantic != "q4_avg_cells":
        _append_public_avg_without_coercion(snapshot, semantic, valid_value)
    invalid_value = _ExplosiveDict()
    _append_public_avg_without_coercion(snapshot, semantic, invalid_value)
    if reverse:
        rows = snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ]
        matching = [row for row in rows if row["semantic"] == semantic]
        remainder = [row for row in rows if row["semantic"] != semantic]
        snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ] = [*remainder, *reversed(matching)]
    missing_snapshot = (
        _snapshot(q4_avg=None)
        if semantic == "q4_avg_cells"
        else _snapshot()
    )
    _ExplosiveDict.magic_calls = 0

    _assert_product_matches_missing(
        snapshot,
        tmp_path=tmp_path,
        name=f"explosive-value-{semantic}-{int(reverse)}",
        missing_snapshot=missing_snapshot,
    )
    assert _ExplosiveDict.magic_calls == 0


def _recursive_public_avg_value() -> dict:
    value: dict[str, object] = {}
    value["self"] = value
    return value


def _deep_public_avg_value() -> list:
    root: list[object] = []
    cursor = root
    for _index in range(40):
        child: list[object] = []
        cursor.append(child)
        cursor = child
    return root


@pytest.mark.parametrize(
    "factory",
    [
        _recursive_public_avg_value,
        _deep_public_avg_value,
        lambda: [0] * 4097,
        lambda: {1: "non-string-key"},
    ],
    ids=["recursive", "overdeep", "oversized", "non-string-key"],
)
def test_4501_unstable_json_value_is_bounded_product_noop(
    factory,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    invalid_value = factory()
    _append_public_avg_without_coercion(
        snapshot,
        "q4_avg_cells",
        invalid_value,
    )

    _assert_product_matches_missing(
        snapshot,
        tmp_path=tmp_path,
        name=f"unstable-json-{type(invalid_value).__name__}",
    )


@pytest.mark.parametrize(
    "placement",
    [
        "root-first",
        "root-middle",
        "root-last",
        "nested-first",
        "nested-last",
        "facts-first",
        "facts-last",
    ],
)
def test_4501_overbudget_ui_contract_is_atomic_product_noop(
    placement: str,
    tmp_path: Path,
) -> None:
    _assert_product_matches_missing(
        _overbudget_ui_contract_snapshot(placement=placement),
        tmp_path=tmp_path,
        name=f"overbudget-ui-{placement}",
    )


@pytest.mark.parametrize("product_path", ["direct", "summary"])
@pytest.mark.parametrize(
    "location",
    [
        "ui-root",
        "context",
        "constraints",
        "public_info",
        "fact_row",
    ],
)
def test_4501_hash_collision_key_is_atomic_without_comparison_magic(
    product_path: str,
    location: str,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    collision = _inject_hash_collision_key(snapshot, location)
    _HashCollisionKey.magic_calls = 0

    if product_path == "direct":
        result = _run(snapshot)
        missing = _run(_snapshot(q4_avg=None))
        _assert_ref_matches_missing(result, missing)
    else:
        summary = summarize_snapshot(
            snapshot,
            snapshot_path=tmp_path / f"collision-{location}.json",
        )
        missing = summarize_snapshot(
            _snapshot(q4_avg=None),
            snapshot_path=tmp_path / f"collision-{location}-missing.json",
        )
        _assert_ref_matches_missing(summary["ahmed_ref"], missing["ahmed_ref"])
        assert summary["quality_composition"] == missing["quality_composition"]
        assert summary["red"] == missing["red"]

    assert collision.armed is True
    assert _HashCollisionKey.magic_calls == 0


@pytest.mark.parametrize("product_path", ["direct", "summary"])
@pytest.mark.parametrize(
    "location",
    ["hero", "phase", "map_id", "quality", "value"],
)
def test_4501_scope_protocol_value_is_atomic_without_magic(
    product_path: str,
    location: str,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    value = _inject_scope_protocol_value(snapshot, location)
    _ExplosiveScopeValue.magic_calls = 0

    if product_path == "direct":
        result = _run(snapshot)
        missing = _run(_snapshot(q4_avg=None))
        _assert_ref_matches_missing(result, missing)
    else:
        summary = summarize_snapshot(
            snapshot,
            snapshot_path=tmp_path / f"scope-value-{location}.json",
        )
        missing = summarize_snapshot(
            _snapshot(q4_avg=None),
            snapshot_path=tmp_path / f"scope-value-{location}-missing.json",
        )
        _assert_ref_matches_missing(summary["ahmed_ref"], missing["ahmed_ref"])
        assert summary["quality_composition"] == missing["quality_composition"]
        assert summary["red"] == missing["red"]

    assert value is not None
    assert _ExplosiveScopeValue.magic_calls == 0


@pytest.mark.parametrize("product_path", ["direct", "summary"])
@pytest.mark.parametrize("context_mode", ["none", "missing"])
@pytest.mark.parametrize("location", ["hero", "phase", "map_id"])
def test_4501_top_level_scope_fallback_is_stable_without_magic(
    product_path: str,
    context_mode: str,
    location: str,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    value = _inject_top_level_scope_fallback(
        snapshot,
        location=location,
        context_mode=context_mode,
    )
    missing = _without_public_avg(snapshot)
    value_type = type(value)
    value_type.magic_calls = 0
    prepared = prepare_reference_engine_snapshot(snapshot)

    assert prepared[location] is None
    assert prepared["ui_contract"] == {}

    if product_path == "direct":
        result = _run(snapshot)
        missing_result = _run(missing)
        _assert_ref_matches_missing(result, missing_result)
    else:
        summary = summarize_snapshot(
            snapshot,
            snapshot_path=tmp_path / f"fallback-{location}-{context_mode}.json",
        )
        missing_summary = summarize_snapshot(
            missing,
            snapshot_path=tmp_path
            / f"fallback-{location}-{context_mode}-missing.json",
        )
        _assert_ref_matches_missing(
            summary["ahmed_ref"],
            missing_summary["ahmed_ref"],
        )
        assert (
            summary["quality_composition"]
            == missing_summary["quality_composition"]
        )
        assert summary["red"] == missing_summary["red"]

    assert value_type.magic_calls == 0


@pytest.mark.parametrize("location", ["hero", "phase", "map_id"])
def test_4501_valid_context_replaces_unstable_top_level_scope(
    location: str,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    if location in {"hero", "phase"}:
        raw = "aisha" if location == "hero" else "bidding"
        value: object = _ExplosiveScopeStr(raw)
    else:
        value = _ExplosiveScopeInt(4501)
    snapshot[location] = value
    value_type = type(value)
    value_type.magic_calls = 0

    prepared = prepare_reference_engine_snapshot(snapshot)
    assert type(prepared[location]) is type(_snapshot()[location])  # noqa: E721
    assert prepared[location] == _snapshot()[location]
    assert prepared["ui_contract"]["context"][location] == _snapshot()[
        "ui_contract"
    ]["context"][location]
    _assert_product_matches_reference(
        snapshot,
        reference_snapshot=_snapshot(),
        tmp_path=tmp_path,
        name=f"stable-context-{location}",
    )

    assert value_type.magic_calls == 0


@pytest.mark.parametrize("product_path", ["direct", "summary"])
def test_4501_missing_context_container_uses_stable_root_scope(
    product_path: str,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    snapshot["ui_contract"].pop("context")
    reference = _snapshot()

    prepared = prepare_reference_engine_snapshot(snapshot)
    assert prepared["hero"] == "aisha"
    assert prepared["map_id"] == 4501
    assert prepared["phase"] == "bidding"
    assert "context" not in prepared["ui_contract"]

    if product_path == "direct":
        _assert_ref_matches_missing(_run(snapshot), _run(reference))
    else:
        summary = summarize_snapshot(
            snapshot,
            snapshot_path=tmp_path / "missing-context-container.json",
        )
        reference_summary = summarize_snapshot(
            reference,
            snapshot_path=tmp_path / "missing-context-container-reference.json",
        )
        _assert_ref_matches_missing(
            summary["ahmed_ref"],
            reference_summary["ahmed_ref"],
        )
        assert (
            summary["quality_composition"]
            == reference_summary["quality_composition"]
        )
        assert summary["red"] == reference_summary["red"]


@pytest.mark.parametrize("product_path", ["direct", "summary"])
@pytest.mark.parametrize(
    ("context_factory", "magic_type"),
    [
        (lambda _context: None, None),
        (lambda _context: [], None),
        (lambda _context: "aisha", None),
        (lambda _context: True, None),
        (lambda _context: 4501, None),
        (lambda _context: 4501.0, None),
        (
            lambda context: _ExplosiveContainerDict(context),
            _ExplosiveContainerDict,
        ),
        (lambda _context: _ExplosiveList([]), _ExplosiveList),
        (lambda _context: _ExplosiveScopeStr("aisha"), _ExplosiveScopeStr),
        (lambda _context: _ExplosiveScopeInt(4501), _ExplosiveScopeInt),
        (lambda _context: _ExplosiveScopeFloat(4501.0), _ExplosiveScopeFloat),
        (lambda _context: _ExplosiveScopeValue(), _ExplosiveScopeValue),
    ],
    ids=[
        "null",
        "list",
        "string",
        "bool",
        "int",
        "float",
        "dict-subclass",
        "list-subclass",
        "str-subclass",
        "int-subclass",
        "float-subclass",
        "protocol-object",
    ],
)
def test_4501_present_non_dict_context_container_is_atomic_noop(
    product_path: str,
    context_factory,
    magic_type: type | None,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    raw_context = context_factory(snapshot["ui_contract"]["context"])
    snapshot["ui_contract"]["context"] = raw_context
    if magic_type is not None:
        magic_type.magic_calls = 0

    prepared = prepare_reference_engine_snapshot(snapshot)
    assert prepared["hero"] == "aisha"
    assert prepared["map_id"] == 4501
    assert prepared["phase"] == "bidding"
    assert prepared["ui_contract"] == {}

    missing = _snapshot(q4_avg=None)
    if product_path == "direct":
        _assert_ref_matches_missing(_run(snapshot), _run(missing))
    else:
        summary = summarize_snapshot(
            snapshot,
            snapshot_path=tmp_path / "invalid-context-container.json",
        )
        missing_summary = summarize_snapshot(
            missing,
            snapshot_path=tmp_path / "invalid-context-container-missing.json",
        )
        _assert_ref_matches_missing(
            summary["ahmed_ref"],
            missing_summary["ahmed_ref"],
        )
        assert (
            summary["quality_composition"]
            == missing_summary["quality_composition"]
        )
        assert summary["red"] == missing_summary["red"]

    if magic_type is not None:
        assert magic_type.magic_calls == 0


@pytest.mark.parametrize(
    ("location", "value"),
    [
        ("hero", _ExplosiveScopeStr("aisha")),
        ("phase", _ExplosiveScopeStr("bidding")),
        ("map_id", _ExplosiveScopeFloat(4501.0)),
        ("map_id", True),
        ("quality", _ExplosiveScopeInt(4)),
        ("value", _ExplosiveScopeFloat(2.75)),
    ],
    ids=[
        "hero-str-subclass",
        "phase-str-subclass",
        "map-float-subclass",
        "map-bool",
        "quality-int-subclass",
        "value-float-subclass",
    ],
)
def test_4501_scope_builtin_subclasses_and_bool_are_atomic_noop(
    location: str,
    value: object,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    if location in {"hero", "phase", "map_id"}:
        snapshot["ui_contract"]["context"][location] = value
    else:
        snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ][0][location] = value
    missing = _without_public_avg(snapshot)
    value_type = type(value)
    if hasattr(value_type, "magic_calls"):
        value_type.magic_calls = 0

    _assert_product_matches_reference(
        snapshot,
        reference_snapshot=missing,
        tmp_path=tmp_path,
        name=f"scope-subclass-{location}-{value_type.__name__}",
    )

    if hasattr(value_type, "magic_calls"):
        assert value_type.magic_calls == 0


@pytest.mark.parametrize("rollback", [False, True])
@pytest.mark.parametrize("extra_nodes", [0, 1])
def test_4501_json_node_budget_boundary_is_atomic_in_product_paths(
    rollback: bool,
    extra_nodes: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot_at_json_node_budget(extra_nodes=extra_nodes)
    if rollback:
        monkeypatch.setenv(AISHA_PUBLIC_AVG_SEMANTIC_CONFLICT_DISABLE, "1")
    if extra_nodes == 0:
        _assert_product_matches_reference(
            snapshot,
            reference_snapshot=_snapshot(),
            tmp_path=tmp_path,
            name=f"node-budget-max-{int(rollback)}",
        )
    else:
        _assert_product_matches_missing(
            snapshot,
            tmp_path=tmp_path,
            name=f"node-budget-over-{int(rollback)}",
        )


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_4501_overbudget_projection_is_noop_at_product_combo_caps(
    max_combos: int,
) -> None:
    result = _run(
        _overbudget_ui_contract_snapshot(placement="root-middle"),
        max_combos=max_combos,
    )
    missing = _run(_snapshot(q4_avg=None), max_combos=max_combos)
    _assert_ref_matches_missing(result, missing)


@pytest.mark.parametrize("placement", ["root-first", "root-last"])
def test_4501_overbudget_projection_does_not_commit_context_scope(
    placement: str,
    tmp_path: Path,
) -> None:
    snapshot = _overbudget_ui_contract_snapshot(placement=placement)
    snapshot["hero"] = "ahmed"
    root_scope_baseline = copy.deepcopy(snapshot)
    root_scope_baseline["ui_contract"] = {}

    prepared = prepare_reference_engine_snapshot(snapshot)
    assert prepared["hero"] == "ahmed"
    assert prepared["ui_contract"] == {}
    _assert_product_matches_reference(
        snapshot,
        reference_snapshot=root_scope_baseline,
        tmp_path=tmp_path,
        name=f"overbudget-context-scope-{placement}",
    )


@pytest.mark.parametrize("invalid_first", [False, True])
@pytest.mark.parametrize(
    "invalid_factory",
    [
        lambda: {1: "invalid-key"},
        lambda: ("invalid",),
        object,
    ],
    ids=["non-string-key", "tuple", "object"],
)
def test_4501_structural_invalid_value_is_atomic_across_key_order(
    invalid_first: bool,
    invalid_factory,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    ui_contract = snapshot["ui_contract"]
    invalid = invalid_factory()
    if invalid_first:
        snapshot["ui_contract"] = {"invalid": invalid, **ui_contract}
    else:
        snapshot["ui_contract"] = {**ui_contract, "invalid": invalid}

    _assert_product_matches_missing(
        snapshot,
        tmp_path=tmp_path,
        name=f"structural-invalid-{int(invalid_first)}",
    )


@pytest.mark.parametrize("cycle_first", [False, True])
def test_4501_structural_cycle_is_atomic_across_key_order(
    cycle_first: bool,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    ui_contract = snapshot["ui_contract"]
    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    if cycle_first:
        snapshot["ui_contract"] = {"cycle": cycle, **ui_contract}
    else:
        snapshot["ui_contract"] = {**ui_contract, "cycle": cycle}

    _assert_product_matches_missing(
        snapshot,
        tmp_path=tmp_path,
        name=f"structural-cycle-{int(cycle_first)}",
    )


def _nested_list(container_count: int) -> list:
    value: object = 0
    for _index in range(container_count):
        value = [value]
    assert type(value) is list  # noqa: E721 - constructed exact builtin
    return value


@pytest.mark.parametrize("container_count", [PUBLIC_AVG_JSON_MAX_DEPTH - 1])
def test_4501_max_depth_and_shared_references_remain_valid(
    container_count: int,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    shared = [{"value": 1}, [2, 3]]
    snapshot["ui_contract"]["depth_boundary"] = _nested_list(container_count)
    snapshot["ui_contract"]["shared_a"] = shared
    snapshot["ui_contract"]["shared_b"] = shared

    _assert_product_matches_reference(
        snapshot,
        reference_snapshot=_snapshot(),
        tmp_path=tmp_path,
        name="max-depth-shared-reference",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("overdepth", _nested_list(PUBLIC_AVG_JSON_MAX_DEPTH)),
        ("overwidth", [0] * (PUBLIC_AVG_JSON_MAX_CONTAINER_ITEMS + 1)),
    ],
)
@pytest.mark.parametrize("invalid_first", [False, True])
def test_4501_depth_and_width_overflow_are_atomic(
    field: str,
    value: object,
    invalid_first: bool,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    ui_contract = snapshot["ui_contract"]
    if invalid_first:
        snapshot["ui_contract"] = {field: value, **ui_contract}
    else:
        snapshot["ui_contract"] = {**ui_contract, field: value}

    _assert_product_matches_missing(
        snapshot,
        tmp_path=tmp_path,
        name=f"{field}-{int(invalid_first)}",
    )


def test_public_avg_json_projection_accepts_exact_node_and_width_boundaries() -> None:
    exact_nodes = _padding_tree_with_nodes(PUBLIC_AVG_JSON_MAX_NODES)
    assert _json_node_count(exact_nodes) == PUBLIC_AVG_JSON_MAX_NODES
    assert _stable_public_avg_json_projection(exact_nodes) == exact_nodes
    assert (
        _stable_public_avg_json_projection(
            _padding_tree_with_nodes(PUBLIC_AVG_JSON_MAX_NODES + 1)
        )
        is None
    )
    exact_width = [0] * PUBLIC_AVG_JSON_MAX_CONTAINER_ITEMS
    assert _stable_public_avg_json_projection(exact_width) == exact_width
    assert (
        _stable_public_avg_json_projection(
            [0] * (PUBLIC_AVG_JSON_MAX_CONTAINER_ITEMS + 1)
        )
        is None
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_4501_conflicting_duplicate_q4_avg_product_payload_is_noop(
    reverse: bool,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    _append_public_q4_avg(snapshot, 3.0)
    if reverse:
        snapshot["ui_contract"]["constraints"]["public_info"][
            "public_numeric_facts"
        ].reverse()

    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / f"conflict-{int(reverse)}.json",
    )
    missing = summarize_snapshot(
        _snapshot(q4_avg=None),
        snapshot_path=tmp_path / "missing.json",
    )

    for key in (
        "conservative",
        "balanced",
        "aggressive",
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
    ):
        assert summary["ahmed_ref"][key] == missing["ahmed_ref"][key]
    assert summary["red"] == missing["red"]
    assert (
        f"{AISHA_PUBLIC_AVG_SEMANTIC_CONFLICT_NOTE_PREFIX}:q4"
        in summary["ahmed_ref"]["notes"]
    )


def test_4501_same_value_duplicate_q4_avg_keeps_unique_reading() -> None:
    snapshot = _snapshot()
    _append_public_q4_avg(snapshot, 2.75)

    duplicate = _run(snapshot)
    unique = _run(_snapshot())

    assert duplicate == unique
    assert (
        f"{AISHA_PUBLIC_AVG_SEMANTIC_CONFLICT_NOTE_PREFIX}:q4"
        not in duplicate["notes"]
    )


def test_4501_conflicting_duplicate_q4_avg_has_independent_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forward = _snapshot()
    _append_public_q4_avg(forward, 3.0)
    reverse = copy.deepcopy(forward)
    reverse["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ].reverse()

    monkeypatch.setenv(AISHA_PUBLIC_AVG_SEMANTIC_CONFLICT_DISABLE, "1")
    forward_result = _run(forward)
    reverse_result = _run(reverse)

    assert _q4_joint(forward_result) != _q4_joint(reverse_result)
    assert _q4_joint(forward_result) == [
        (9, 27),
        (10, 30),
        (8, 24),
        (11, 33),
        (7, 21),
    ]
    assert _q4_joint(reverse_result) == [(8, 22), (4, 11)]
    assert (
        f"{AISHA_PUBLIC_AVG_SEMANTIC_CONFLICT_NOTE_PREFIX}:q4"
        not in forward_result["notes"]
    )


def test_4501_rollback_reproduces_impossible_q4_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AISHA_PUBLIC_AVG_DIRECT_GRID_DOMAIN_DISABLE, "1")
    rollback = _run(_snapshot())

    assert rollback["combo_count"] == 11
    assert {count for count, _cells in _q4_joint(rollback)} == {
        6,
        7,
        8,
        9,
        10,
        11,
    }
    assert [rollback[key] for key in ("conservative", "balanced", "aggressive")] == [
        143_907,
        696_091,
        839_360,
    ]
    assert AISHA_PUBLIC_AVG_DIRECT_GRID_DOMAIN_NOTE not in rollback["notes"]


def test_direct_grid_wire_average_keeps_only_multiple_of_three_counts() -> None:
    result = _run(_snapshot(q4_avg=3.3333332538604736), max_combos=50_000)

    assert result["status"] == "ok"
    assert result["combo_count"] == 3
    assert result["quality_lock"]["q4"]["strength"] == "probabilistic"
    assert all(count % 3 == 0 for count, _cells in _q4_joint(result))
    assert all(cells == round(count * (10 / 3)) for count, cells in _q4_joint(result))


def test_direct_grid_display_rounding_keeps_reachable_nonexact_product() -> None:
    result = _run(
        _snapshot(
            q4_avg=2.09,
            total_cells=82,
        )
    )

    assert _q4_joint(result) == [(11, 23)]
    assert 2.09 * 11 != 23


@pytest.mark.parametrize("mode", ["missing", "conflicting_exact", "no_hard_total"])
def test_missing_conflicting_or_unanchored_average_is_noop(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if mode == "missing":
        snapshot = _snapshot(q4_avg=None)
    elif mode == "no_hard_total":
        snapshot = _snapshot(total_cells=None)
        snapshot["structured_ref_inputs"]["counts"] = {}
        snapshot["structured_ref_inputs"]["quality_cells"] = {}
        snapshot["structured_ref_inputs"]["split_counts"] = {}
        snapshot["structured_ref_inputs"]["split_quality_cells"] = {}
        snapshot["skill_reveal_rows"] = []
    else:
        snapshot = _snapshot(total_cells=93)
        snapshot["structured_ref_inputs"]["counts"]["q4"] = 4
        snapshot["structured_ref_inputs"]["quality_cells"]["q4"] = 12

    default = _run(copy.deepcopy(snapshot))
    monkeypatch.setenv(AISHA_PUBLIC_AVG_DIRECT_GRID_DOMAIN_DISABLE, "1")
    rollback = _run(copy.deepcopy(snapshot))

    assert default == rollback
    assert AISHA_PUBLIC_AVG_DIRECT_GRID_DOMAIN_NOTE not in default["notes"]


@pytest.mark.parametrize(
    "hero",
    ["ahmed", "gabriela", "ethan", "maria", "raven", "ivan", "carlos"],
)
def test_same_direct_grid_inputs_are_noop_for_other_heroes(
    hero: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(hero=hero)
    default = _run(copy.deepcopy(snapshot))
    monkeypatch.setenv(AISHA_PUBLIC_AVG_DIRECT_GRID_DOMAIN_DISABLE, "1")
    rollback = _run(copy.deepcopy(snapshot))

    assert default == rollback
    assert AISHA_PUBLIC_AVG_DIRECT_GRID_DOMAIN_NOTE not in default["notes"]


def test_settlement_truth_is_not_reinterpreted_by_public_average(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(phase="settled")
    snapshot["final_quality_counts"] = "q1=15;q3=11;q4=8;q5=2;q6=3"
    snapshot["final_quality_cells"] = "q1=28;q3=21;q4=22;q5=10;q6=11"
    snapshot["final_quality_values"] = "q1=0;q3=0;q4=0;q5=0;q6=0"
    snapshot["ui_contract"]["truth"] = {
        "available": True,
        "complete": True,
        "total_items": 39,
        "total_cells": 92,
        "total_value": 0,
    }

    default = _run(copy.deepcopy(snapshot))
    monkeypatch.setenv(AISHA_PUBLIC_AVG_DIRECT_GRID_DOMAIN_DISABLE, "1")
    rollback = _run(copy.deepcopy(snapshot))

    assert default == rollback
    assert default["quality_count_ranges"]["q4"] == [8, 8, 8]
    assert default["quality_cells_ranges"]["q4"] == [22, 22, 22]
    assert AISHA_PUBLIC_AVG_DIRECT_GRID_DOMAIN_NOTE not in default["notes"]


def test_product_summary_and_tk_payload_keep_both_q4_arms(
    tmp_path: Path,
) -> None:
    summary = summarize_snapshot(
        _snapshot(),
        snapshot_path=tmp_path / "latest_snapshot.json",
    )
    ref = summary["ahmed_ref"]

    assert _q4_joint(ref) == [(8, 22), (4, 11)]
    assert ref["quality_lock"]["q4"]["strength"] == "probabilistic"
    assert summary["red"]["quality_count_summary"] == "紫8件/22格 · 金2件/10格"
    assert summary["red"]["purple_gold_probability_hover"] == (
        "紫品：8件/22格 98.6% · 4件/11格 1.4%；金品：2件/10格 100%"
    )
