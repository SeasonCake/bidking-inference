"""Core-F: keep a partial known-red footprint as a floor, never a hard total.

The bidding fixture is truth-free and reduced from
``2530:9000000000000044``. Settlement fields appear only in the explicit
no-injection control.
"""

from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_live_panel_server as panel  # noqa: E402
import ahmad_ref_engine as engine  # noqa: E402
from ahmad_live_panel_server import ServerConfig, make_handler, summarize_snapshot  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    AISHA_PARTIAL_RED_FLOOR_INFERRED_TOTAL_GUARD_DISABLE,
    AISHA_PARTIAL_RED_FLOOR_INFERRED_TOTAL_SOFT_NOTE,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from ahmad_tk_overlay import _remaining_cells_range  # noqa: E402
from test_aisha_2521_q5q6_exact_residual_count_support_hotfix2 import (  # noqa: E402
    _require_frozen_package_inputs,
)


QUALIFICATION = "postupdate_unknown_warehouse_source"
ROLLBACK = AISHA_PARTIAL_RED_FLOOR_INFERRED_TOTAL_GUARD_DISABLE


class _IntSubclass(int):
    pass


class _FloatSubclass(float):
    pass


class _NumericProtocolBomb:
    """Structured scalar that fails if the engine attempts user-code coercion."""

    def _fail(self, *_args: object) -> object:
        raise AssertionError("numeric protocol must not be called")

    __bool__ = _fail
    __eq__ = _fail
    __float__ = _fail
    __ge__ = _fail
    __gt__ = _fail
    __int__ = _fail
    __le__ = _fail
    __lt__ = _fail
    __str__ = _fail


@pytest.fixture(scope="module", autouse=True)
def _frozen_inputs() -> None:
    _require_frozen_package_inputs()


def _warehouse_source(qualification: str = QUALIFICATION) -> dict:
    official = qualification == "official_random_warehouse"
    return {
        "qualification": qualification,
        "official_distribution_eligible": official,
        "quote_policy": "normal" if official else "advisory_only_no_clamp",
        "quarantine_required": False,
    }


def _skill_rows() -> list[dict]:
    return [
        {
            "sort": index,
            "skill_id": skill_id,
            "hero_id": 103,
            "revealed_items": count,
            "revealed_items_detail": [],
        }
        for index, (skill_id, count) in enumerate(
            ((1001033, 12), (1001032, 10), (1001031, 9)),
            start=31,
        )
    ]


def _revealed_item(
    *,
    runtime_id: int,
    local_index: int,
    item_id: int,
    quality: int,
    cells: int,
    value: int,
) -> dict:
    return {
        "runtime_id": runtime_id,
        "local_index": local_index,
        "item_id": item_id,
        "quality": quality,
        "cells": cells,
        "shape_code": int(f"{max(1, cells // 4)}{max(1, cells // 4)}"),
        "value": value,
    }


def _target_snapshot(*, qualification: str = QUALIFICATION) -> dict:
    source = _warehouse_source(qualification)
    session_id = "2530:9000000000000044"
    structured = {
        "source": "monitor_pre_settlement_field_updates",
        "hero": "aisha",
        "total_count": 37,
        "total_cells": None,
        "avg_cells": {"q4": 4.111111164093018, "q5": 5.0},
        "counts": {"q3": 10, "q4": 9},
        "quality_cells": {"q3": 20, "q4": 37},
        "quality_values": {"q5": 66782},
        "split_counts": {"green": 12},
        "split_quality_cells": {"green": 26},
    }
    return {
        "hero": "aisha",
        "map_id": 2530,
        "round": 5,
        "action_round": 5,
        "phase": "bidding",
        "session_id": session_id,
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": qualification,
        "official_distribution_eligible": source["official_distribution_eligible"],
        "structured_ref_inputs": structured,
        "skill_reveal_rows": _skill_rows(),
        "action_result_rows": [
            {
                "sort": 35,
                "action_id": 100129,
                "result": 17,
                "result_field": 14,
                "session_id": session_id,
                "round": 4,
                "revealed_items": 2,
                "revealed_items_detail": [
                    _revealed_item(
                        runtime_id=1862572815590634,
                        local_index=43,
                        item_id=1054004,
                        quality=4,
                        cells=1,
                        value=10960,
                    ),
                    _revealed_item(
                        runtime_id=1862572815590646,
                        local_index=115,
                        item_id=1086001,
                        quality=6,
                        cells=16,
                        value=444000,
                    ),
                ],
            }
        ],
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2530,
                "round": 5,
                "phase": "bidding",
                "session_id": session_id,
                "warehouse_source": copy.deepcopy(source),
                "warehouse_source_qualification": qualification,
                "official_distribution_eligible": source[
                    "official_distribution_eligible"
                ],
            },
            "constraints": {"public_info": {}},
            "source": {"created_at": 1.0},
        },
    }


def _run(snapshot: dict, *, max_combos: int = 8000) -> dict:
    prepared = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    return run_reference_engine(prepared, max_combos=max_combos).as_dict()


def _q6_single_item_catalog_domain() -> tuple[int, ...]:
    table_quality = engine._quality_key_item_table_quality("q6")
    assert table_quality is not None
    domain = engine._catalog_quality_composable_grid_options(table_quality, 1)
    assert domain
    return domain


def _rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ROLLBACK, "1")


@pytest.mark.parametrize("max_combos", [8000, 50000])
def test_target_partial_red_floor_is_probabilistic_not_hard_total(
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    default = _run(snapshot, max_combos=max_combos)
    _rollback(monkeypatch)
    rollback = _run(snapshot, max_combos=max_combos)

    assert default["status"] == rollback["status"] == "ok"
    assert default["quality_count_ranges"]["q6"] == [4, 4, 4]
    assert default["quality_cells_ranges"]["q6"] == [19, 26, 28]
    assert min(default["total_grid_range"]) >= 93 + 19
    assert default["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "partial_reveal_min",
    }
    assert AISHA_PARTIAL_RED_FLOOR_INFERRED_TOTAL_SOFT_NOTE in default["notes"]
    assert rollback["quality_cells_ranges"]["q6"] == [16, 16, 16]
    assert rollback["total_grid_range"] == [109, 109, 109]
    assert rollback["quality_lock"]["q6"] == {
        "strength": "exact",
        "reason": "derived_single_solution",
    }
    assert AISHA_PARTIAL_RED_FLOOR_INFERRED_TOTAL_SOFT_NOTE not in rollback["notes"]
    assert [default[key] for key in ("conservative", "balanced", "aggressive")] == [
        rollback[key] for key in ("conservative", "balanced", "aggressive")
    ]
    assert default["evidence"]["phase"] == "bidding"
    assert "settlement" not in json.dumps(default["evidence"], ensure_ascii=False)


@pytest.mark.parametrize(
    ("total_cells", "status", "q6_cells"),
    [(109, "no_reachable_combo", None), (112, "ok", [19, 19, 19]), (133, "ok", [40, 40, 40])],
)
def test_explicit_hard_total_respects_effective_partial_floor(
    total_cells: int,
    status: str,
    q6_cells: list[int] | None,
) -> None:
    snapshot = _target_snapshot()
    snapshot["structured_ref_inputs"]["total_cells"] = total_cells
    result = _run(snapshot)

    assert result["status"] == status
    assert result.get("quality_cells_ranges", {}).get("q6") == q6_cells
    if status != "ok":
        assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
            None,
            None,
            None,
        ]


@pytest.mark.parametrize(
    ("field", "value"),
    [("counts", 4), ("quality_cells", 40)],
)
def test_direct_q6_count_or_cells_is_rollback_noop(
    field: str,
    value: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    snapshot["structured_ref_inputs"][field]["q6"] = value
    default = _run(snapshot)
    _rollback(monkeypatch)
    rollback = _run(snapshot)

    assert default == rollback
    assert AISHA_PARTIAL_RED_FLOOR_INFERRED_TOTAL_SOFT_NOTE not in default["notes"]


@pytest.mark.parametrize(
    ("avg_cells", "rollback_cells"),
    [(4, [18, 18, 18]), (4.0, [18, 18, 18]), (4.5, [19, 20, 21])],
)
def test_direct_q6_average_keeps_physical_floor_and_probabilistic_cell_support(
    avg_cells: int | float,
    rollback_cells: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    snapshot["structured_ref_inputs"]["avg_cells"]["q6"] = avg_cells

    default = _run(snapshot)
    _rollback(monkeypatch)
    rollback = _run(snapshot)

    assert default["status"] == rollback["status"] == "ok"
    assert default["quality_count_ranges"]["q6"] == [4, 4, 4]
    assert default["quality_cells_ranges"]["q6"] == [19, 20, 21]
    assert default["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "partial_reveal_min",
    }
    assert rollback["quality_cells_ranges"]["q6"] == rollback_cells
    assert rollback["quality_lock"]["q6"] == {
        "strength": "exact",
        "reason": "derived_single_solution",
    }
    assert AISHA_PARTIAL_RED_FLOOR_INFERRED_TOTAL_SOFT_NOTE not in default["notes"]
    assert AISHA_PARTIAL_RED_FLOOR_INFERRED_TOTAL_SOFT_NOTE not in rollback["notes"]


@pytest.mark.parametrize(
    "raw_avg",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
        pytest.param(-1.0, id="negative-float"),
        pytest.param(-1, id="negative-int"),
        pytest.param(0.0, id="zero-float"),
        pytest.param(0, id="zero-int"),
        pytest.param(True, id="bool"),
        pytest.param("4.0", id="string"),
        pytest.param(_FloatSubclass(4.0), id="float-subclass"),
        pytest.param(_IntSubclass(4), id="int-subclass"),
        pytest.param(_NumericProtocolBomb(), id="numeric-protocol-object"),
        pytest.param(251.0, id="above-structured-upper-bound"),
        pytest.param(1 << 10000, id="oversized-int"),
    ],
)
def test_direct_q6_invalid_average_is_absent_without_protocol_coercion(
    raw_avg: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for rollback_enabled in (False, True):
        if rollback_enabled:
            _rollback(monkeypatch)
        else:
            monkeypatch.delenv(ROLLBACK, raising=False)

        baseline = _run(_target_snapshot())
        snapshot = _target_snapshot()
        snapshot["structured_ref_inputs"]["avg_cells"]["q6"] = raw_avg
        result = _run(snapshot)

        assert result == baseline
        assert "q6" not in result["evidence"]["avg_cells"]
        assert "structured_ref_bridge_avg_cells_q6" not in result["notes"]


@pytest.mark.parametrize("raw_avg", [21, 21.0, 250.0])
def test_direct_q6_catalog_impossible_average_fails_closed(
    raw_avg: int | float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    snapshot["structured_ref_inputs"]["avg_cells"]["q6"] = raw_avg

    for rollback_enabled in (False, True):
        if rollback_enabled:
            _rollback(monkeypatch)
        else:
            monkeypatch.delenv(ROLLBACK, raising=False)
        result = _run(snapshot)

        assert result["status"] == "no_reachable_combo"
        assert result["evidence"]["avg_cells"]["q6"] == float(raw_avg)
        assert "structured_ref_bridge_avg_cells_q6" in result["notes"]
        assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
            None,
            None,
            None,
        ]


@pytest.mark.parametrize("raw_avg", [5e-324, 0.5])
def test_direct_q6_below_catalog_minimum_average_fails_closed(
    raw_avg: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    snapshot["structured_ref_inputs"]["avg_cells"]["q6"] = raw_avg

    for rollback_enabled in (False, True):
        if rollback_enabled:
            _rollback(monkeypatch)
        else:
            monkeypatch.delenv(ROLLBACK, raising=False)
        result = _run(snapshot)

        assert result["status"] == "no_reachable_combo"
        assert result["evidence"]["avg_cells"]["q6"] == raw_avg
        assert "structured_ref_bridge_avg_cells_q6" in result["notes"]
        assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
            None,
            None,
            None,
        ]


def test_direct_q6_nextafter_below_catalog_minimum_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lower = min(_q6_single_item_catalog_domain())
    raw_avg = math.nextafter(float(lower), 0.0)
    assert 0.0 < raw_avg < lower
    snapshot = _target_snapshot()
    snapshot["structured_ref_inputs"]["avg_cells"]["q6"] = raw_avg

    for rollback_enabled in (False, True):
        if rollback_enabled:
            _rollback(monkeypatch)
        else:
            monkeypatch.delenv(ROLLBACK, raising=False)
        result = _run(snapshot)

        assert result["status"] == "no_reachable_combo"
        assert result["evidence"]["avg_cells"]["q6"] == raw_avg
        assert "structured_ref_bridge_avg_cells_q6" in result["notes"]


def test_direct_q6_catalog_boundary_averages_remain_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain = _q6_single_item_catalog_domain()
    lower, upper = min(domain), max(domain)
    assert lower < upper
    averages = (
        (lower, "no_reachable_combo"),
        (float(lower), "no_reachable_combo"),
        (math.nextafter(float(lower), math.inf), "no_reachable_combo"),
        (upper, "ok"),
        (float(upper), "ok"),
    )

    for raw_avg, rollback_status in averages:
        monkeypatch.delenv(ROLLBACK, raising=False)
        snapshot = _target_snapshot()
        snapshot["structured_ref_inputs"]["avg_cells"]["q6"] = raw_avg
        default = _run(snapshot)
        _rollback(monkeypatch)
        rollback = _run(snapshot)

        assert default["status"] == "ok"
        assert rollback["status"] == rollback_status
        for result in (default, rollback):
            assert result["evidence"]["avg_cells"]["q6"] == float(raw_avg)
            assert "structured_ref_bridge_avg_cells_q6" in result["notes"]


def test_partial_red_avg_catalog_domain_is_validated_without_protocols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain = _q6_single_item_catalog_domain()
    lower, upper = min(domain), max(domain)
    assert engine._catalog_quality_single_item_grid_bounds("q6") == (lower, upper)
    assert engine._aisha_partial_red_floor_avg_can_use_catalog_fallback("q6", lower)
    assert engine._aisha_partial_red_floor_avg_can_use_catalog_fallback(
        "q6",
        math.nextafter(float(lower), math.inf),
    )
    assert engine._aisha_partial_red_floor_avg_can_use_catalog_fallback("q6", upper)
    assert not engine._aisha_partial_red_floor_avg_can_use_catalog_fallback(
        "q6",
        math.nextafter(float(lower), 0.0),
    )

    abnormal_domains: tuple[object, ...] = (
        (),
        list(domain),
        (0, *domain),
        (float(lower), *domain[1:]),
        (True, *domain[1:]),
        (_NumericProtocolBomb(), *domain[1:]),
        tuple(reversed(domain)),
        (*domain, domain[-1]),
        (*domain, int(engine.PUBLIC_AVG_CELLS_MAX_VALUE) + 1),
    )
    for abnormal in abnormal_domains:
        monkeypatch.setattr(
            engine,
            "_catalog_quality_composable_grid_options",
            lambda _quality, _count, value=abnormal: value,
        )
        assert engine._catalog_quality_single_item_grid_bounds("q6") is None
        assert not engine._aisha_partial_red_floor_avg_can_use_catalog_fallback(
            "q6",
            float(lower),
        )


def test_empty_catalog_domain_disables_partial_red_average_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lower = min(_q6_single_item_catalog_domain())
    monkeypatch.setattr(
        engine,
        "_catalog_quality_composable_grid_options",
        lambda _quality, _count: (),
    )
    snapshot = _target_snapshot()
    snapshot["structured_ref_inputs"]["avg_cells"]["q6"] = float(lower)

    result = _run(snapshot)

    assert result["status"] == "no_reachable_combo"
    assert result["evidence"]["avg_cells"]["q6"] == float(lower)
    assert "structured_ref_bridge_avg_cells_q6" in result["notes"]


@pytest.mark.parametrize("hero", ["ahmed", "gabriela"])
def test_below_catalog_minimum_average_does_not_enable_other_heroes(
    hero: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lower = min(_q6_single_item_catalog_domain())
    raw_avg = math.nextafter(float(lower), 0.0)
    snapshot = _target_snapshot()
    snapshot["hero"] = hero
    snapshot["structured_ref_inputs"]["hero"] = hero
    snapshot["ui_contract"]["context"]["hero"] = hero
    snapshot["structured_ref_inputs"]["avg_cells"]["q6"] = raw_avg

    default = _run(snapshot)
    _rollback(monkeypatch)
    rollback = _run(snapshot)

    assert default == rollback
    assert default["status"] == "no_reachable_combo"
    assert default["evidence"]["avg_cells"]["q6"] == raw_avg


def test_direct_q6_average_cannot_make_a_below_floor_explicit_total_reachable() -> None:
    snapshot = _target_snapshot()
    snapshot["structured_ref_inputs"]["avg_cells"]["q6"] = 4.0
    snapshot["structured_ref_inputs"]["total_cells"] = 109

    result = _run(snapshot)

    assert result["status"] == "no_reachable_combo"
    assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
        None,
        None,
        None,
    ]
    assert AISHA_PARTIAL_RED_FLOOR_INFERRED_TOTAL_SOFT_NOTE not in result["notes"]


def test_known_item_count_equal_to_q6_count_is_rollback_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    details = snapshot["action_result_rows"][0]["revealed_items_detail"]
    details.extend(
        _revealed_item(
            runtime_id=300 + index,
            local_index=120 + index,
            item_id=item_id,
            quality=6,
            cells=1,
            value=value,
        )
        for index, (item_id, value) in enumerate(
            ((1055005, 68400), (1056001, 52500), (1056003, 75500))
        )
    )
    snapshot["action_result_rows"][0]["revealed_items"] = len(details)

    default = _run(snapshot)
    _rollback(monkeypatch)
    rollback = _run(snapshot)

    assert default == rollback
    assert default["evidence"]["quality_cell_floors"]["q6"] == 19
    assert default["evidence"]["quality_cell_floor_item_counts"]["q6"] == 4
    assert AISHA_PARTIAL_RED_FLOOR_INFERRED_TOTAL_SOFT_NOTE not in default["notes"]


def test_multiple_known_red_items_keep_an_effective_floor_and_probabilistic_cells() -> None:
    snapshot = _target_snapshot()
    details = snapshot["action_result_rows"][0]["revealed_items_detail"]
    details.append(
        _revealed_item(
            runtime_id=301,
            local_index=120,
            item_id=1055005,
            quality=6,
            cells=1,
            value=68400,
        )
    )
    snapshot["action_result_rows"][0]["revealed_items"] = len(details)

    result = _run(snapshot)

    assert result["evidence"]["quality_cell_floors"]["q6"] == 17
    assert result["evidence"]["quality_cell_floor_item_counts"]["q6"] == 2
    assert result["quality_cells_ranges"]["q6"] == [19, 23, 28]
    assert result["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "partial_reveal_min",
    }


def test_no_total_count_no_floor_q6_zero_and_other_hero_are_noops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    no_count = _target_snapshot()
    no_count["structured_ref_inputs"].pop("total_count")
    no_floor = _target_snapshot()
    no_floor["action_result_rows"] = []
    malformed_floor = _target_snapshot()
    malformed_red = malformed_floor["action_result_rows"][0]["revealed_items_detail"][1]
    malformed_red["cells"] = "bad"
    malformed_red["shape_code"] = "bad"
    malformed_red["item_id"] = None
    zero = _target_snapshot()
    zero["structured_ref_inputs"]["total_count"] = 33
    zero["action_result_rows"] = []
    other = _target_snapshot()
    other["hero"] = "ahmed"
    other["structured_ref_inputs"]["hero"] = "ahmed"
    other["ui_contract"]["context"]["hero"] = "ahmed"
    cases = [no_count, no_floor, malformed_floor, zero, other]

    default = [_run(case) for case in cases]
    _rollback(monkeypatch)
    rollback = [_run(case) for case in cases]

    assert default == rollback
    assert all(
        AISHA_PARTIAL_RED_FLOOR_INFERRED_TOTAL_SOFT_NOTE not in item["notes"]
        for item in default
    )


@pytest.mark.parametrize(
    "qualification",
    [
        "postupdate_unknown_warehouse_source",
        "player_custom_warehouse",
        "official_random_warehouse",
    ],
)
def test_source_pool_is_unchanged(qualification: str) -> None:
    snapshot = _target_snapshot(qualification=qualification)
    summary = summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=Path("missing-core-f-source-pool.json"),
    )

    assert summary["context"]["warehouse_source_qualification"] == qualification
    assert summary["context"]["warehouse_source"] == _warehouse_source(qualification)
    assert summary["ahmed_ref"]["quality_cells_ranges"]["q6"] == [19, 26, 28]


def test_settlement_truth_is_not_reinterpreted_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    snapshot["phase"] = "settled"
    snapshot["ui_contract"]["context"]["phase"] = "settled"
    snapshot["ui_contract"]["truth"] = {
        "available": True,
        "complete": True,
        "total_items": 37,
        "total_cells": 133,
        "total_value": 1374318,
    }
    snapshot["final_quality_counts"] = "q1=0;q2=12;q3=10;q4=9;q5=2;q6=4"
    snapshot["final_quality_cells"] = "q1=0;q2=26;q3=20;q4=37;q5=10;q6=40"
    snapshot["final_quality_values"] = "q1=0;q2=0;q3=0;q4=0;q5=66782;q6=1307536"

    default = _run(snapshot)
    _rollback(monkeypatch)
    rollback = _run(snapshot)

    assert default == rollback
    assert default["quality_count_ranges"]["q6"] == [4, 4, 4]
    assert default["quality_cells_ranges"]["q6"] == [40, 40, 40]
    assert AISHA_PARTIAL_RED_FLOOR_INFERRED_TOTAL_SOFT_NOTE not in default["notes"]


def test_summary_loopback_api_and_tk_share_probabilistic_cells(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    summary = summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=tmp_path / "latest_snapshot.json",
    )
    ref = summary["ahmed_ref"]
    assert ref["quality_cells_ranges"]["q6"] == [19, 26, 28]
    assert ref["quality_lock"]["q6"]["strength"] == "probabilistic"
    assert _remaining_cells_range(ref) == (19, 19, 28)

    monkeypatch.setattr(panel, "_read_json", lambda _path: copy.deepcopy(snapshot))
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))),
    )
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
    assert payload["ahmed_ref"]["quality_cells_ranges"]["q6"] == [19, 26, 28]
    assert payload["ahmed_ref"]["quality_lock"]["q6"]["strength"] == "probabilistic"


def test_invalid_direct_q6_average_is_absent_through_summary_api_and_tk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = summarize_snapshot(
        _target_snapshot(),
        snapshot_path=tmp_path / "baseline_snapshot.json",
    )["ahmed_ref"]
    snapshot = _target_snapshot()
    snapshot["structured_ref_inputs"]["avg_cells"]["q6"] = float("nan")
    ref = summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=tmp_path / "invalid_snapshot.json",
    )["ahmed_ref"]

    assert ref == baseline
    assert _remaining_cells_range(ref) == (19, 19, 28)

    monkeypatch.setattr(panel, "_read_json", lambda _path: copy.deepcopy(snapshot))
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))),
    )
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
    assert payload["ahmed_ref"] == baseline


def test_below_catalog_minimum_conflict_reaches_summary_api_and_tk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lower = min(_q6_single_item_catalog_domain())
    raw_avg = math.nextafter(float(lower), 0.0)
    snapshot = _target_snapshot()
    snapshot["structured_ref_inputs"]["avg_cells"]["q6"] = raw_avg
    ref = summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=tmp_path / "below_catalog_minimum_snapshot.json",
    )["ahmed_ref"]

    assert ref["status"] == "no_reachable_combo"
    assert ref["evidence"]["avg_cells"]["q6"] == raw_avg
    assert "structured_ref_bridge_avg_cells_q6" in ref["notes"]
    assert _remaining_cells_range(ref) == tuple(ref["total_grid_range"])

    monkeypatch.setattr(panel, "_read_json", lambda _path: copy.deepcopy(snapshot))
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))),
    )
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
    assert payload["ahmed_ref"] == ref


def test_fresh_process_default_rollback_8k_50k(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(_target_snapshot(), ensure_ascii=False),
        encoding="utf-8",
    )
    code = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'hero_ref' / 'src'))
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine
snapshot = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
result = run_reference_engine(
    prepare_reference_engine_snapshot(snapshot), max_combos=int(sys.argv[3])
).as_dict()
print(json.dumps({
    'status': result['status'],
    'quotes': [result[k] for k in ('conservative', 'balanced', 'aggressive')],
    'total': result['total_grid_range'],
    'q6_count': result['quality_count_ranges']['q6'],
    'q6_cells': result['quality_cells_ranges']['q6'],
    'q6_lock': result['quality_lock']['q6'],
}, sort_keys=True))
"""
    for limit in (8000, 50000):
        arms = {}
        for name, disabled in (("default", False), ("rollback", True)):
            env = os.environ.copy()
            if disabled:
                env[ROLLBACK] = "1"
            else:
                env.pop(ROLLBACK, None)
            completed = subprocess.run(  # noqa: S603 - fixed local interpreter and code
                [sys.executable, "-c", code, str(ROOT), str(snapshot_path), str(limit)],
                check=True,
                capture_output=True,
                text=True,
                env=env,
                cwd=ROOT,
                timeout=120,
            )
            assert completed.stderr == ""
            arms[name] = json.loads(completed.stdout)
        assert arms["default"]["q6_cells"] == [19, 26, 28]
        assert arms["default"]["q6_lock"]["strength"] == "probabilistic"
        assert arms["rollback"]["q6_cells"] == [16, 16, 16]
        assert arms["rollback"]["q6_lock"]["strength"] == "exact"
        assert arms["default"]["quotes"] == arms["rollback"]["quotes"]
