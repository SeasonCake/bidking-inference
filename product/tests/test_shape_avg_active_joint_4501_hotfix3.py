"""Active complete physical support for current-session four-cell means."""

from __future__ import annotations

import copy
from decimal import Decimal
from fractions import Fraction
import hashlib
import itertools
import json
from pathlib import Path
import struct
import sys
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (
    ROOT / "hero_ref" / "src",
    ROOT / "hero_ref" / "tools",
    ROOT / "src",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_live_panel_server as panel  # noqa: E402
import ahmad_ref_engine as engine  # noqa: E402
import ahmad_tk_overlay as overlay  # noqa: E402
from bidking_lab.inference.mean_multiset_support import (  # noqa: E402
    MeanSupportCandidate,
)
from bidking_lab.inference.size_avg_evidence import (  # noqa: E402
    MeanPartitionSupportResult,
    SHAPE_AVG_ACTIVE_JOINT_DISABLE,
    _float32_roundtrip_integer_bounds,
    size_avg_readings_from_action_rows,
    shape_average_value_evidence_from_action_rows,
    solve_repeat_allowed_mean_partition_support,
)


SESSION = "4501:9000000000000054"
RAW_AVG = 829_288.125
RAW_HEX = "82764a49"
EXPECTED_Q6_FLOOR = 5_603_066
EXPECTED_QUOTES = (5_894_264, 5_967_797, 6_041_329)
PARENT_QUOTES = (845_334, 918_866, 992_398)
EXPECTED_JOINT_SUPPORT_SHA256 = (
    "03c973856d7edc8966de401f3b0aed08a4928905dffef3dee153f430690b5f9a"
)
STATIC_DATA_PATH = (
    ROOT
    / "external_references"
    / "AuctionAnalyzer4.13.3"
    / "_decompiled"
    / "MapBidCalculator"
    / "MapBidCalculator"
    / "Models"
    / "StaticData.cs"
)
STATIC_DATA_SHA256 = (
    "7543B4AA16ADDC73DFAAAF3C6DF9BC0F6662BD83458DE110A16D04FF9F2F9676"
)
CALIBRATION_PATH = ROOT / "data" / "processed" / (
    "map_category_calibration_0_3_2.json"
)
CALIBRATION_SHA256 = (
    "37EB8F50D9BE38EC3D1C85909EA3478800ECC574BDA70446F05CA807983018CC"
)
CANDIDATE_TOTAL_FOUR_CELL_COUNTS = [
    7,
    8,
    9,
    10,
    13,
    14,
    15,
    16,
    18,
    19,
    20,
    23,
]


class _FloatSubclass(float):
    """Must not be coerced into a direct field-9 Float32 producer."""

    def __float__(self) -> float:
        raise AssertionError("direct field-9 producer called float subclass")


class _CoercibleResult:
    """A hostile direct-result mirror: ``__float__`` must never run."""

    def __float__(self) -> float:
        raise AssertionError("direct field-9 producer coerced arbitrary object")


def _assert_workflow_input(
    path: Path,
    *,
    label: str,
    expected_sha256: str,
) -> None:
    assert path.is_file(), f"input-assembly:{label}:missing:{path}"
    actual = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    assert actual == expected_sha256, (
        f"input-assembly:{label}:sha256:{actual}:expected:{expected_sha256}"
    )


@pytest.fixture(scope="module")
def _exact_package_shaped_quote_inputs() -> None:
    """Refuse to certify fallback quotes as package-shaped evidence."""

    _assert_workflow_input(
        STATIC_DATA_PATH,
        label="StaticData.cs",
        expected_sha256=STATIC_DATA_SHA256,
    )
    _assert_workflow_input(
        CALIBRATION_PATH,
        label="map_category_calibration_0_3_2.json",
        expected_sha256=CALIBRATION_SHA256,
    )


@pytest.mark.parametrize(
    ("label", "expected_sha256"),
    (
        ("StaticData.cs", STATIC_DATA_SHA256),
        ("map_category_calibration_0_3_2.json", CALIBRATION_SHA256),
    ),
)
def test_package_shaped_quote_gate_rejects_missing_and_wrong_inputs(
    tmp_path: Path,
    label: str,
    expected_sha256: str,
) -> None:
    missing = tmp_path / f"missing-{label}"
    wrong = tmp_path / f"wrong-{label}"
    wrong.write_bytes(b"not-the-approved-workflow-input")

    with pytest.raises(AssertionError) as missing_error:
        _assert_workflow_input(
            missing,
            label=label,
            expected_sha256=expected_sha256,
        )
    assert str(missing_error.value).startswith(
        f"input-assembly:{label}:missing"
    )

    with pytest.raises(AssertionError) as wrong_error:
        _assert_workflow_input(
            wrong,
            label=label,
            expected_sha256=expected_sha256,
        )
    assert str(wrong_error.value).startswith(
        f"input-assembly:{label}:sha256"
    )


def _source() -> dict[str, object]:
    return {
        "qualification": "official_random_warehouse",
        "official_distribution_eligible": True,
        "quote_policy": "advisory_only_no_clamp",
        "quarantine_required": False,
    }


def _row(
    value: float = RAW_AVG,
    *,
    action_id: int = 100172,
    session_id: str = SESSION,
) -> dict[str, object]:
    raw = struct.pack("<f", value)
    return {
        "action_id": action_id,
        "result": struct.unpack("<f", raw)[0],
        "result_field": 9,
        "result_wire_type": "float32",
        "result_raw_hex": raw.hex(),
        "session_id": session_id,
        "sort": 30,
        "round": 3,
        "source_kind": "direct_action_response",
    }


def _snapshot(*, phase: str = "bidding") -> dict[str, object]:
    source = _source()
    row = _row()
    return {
        "hero": "ahmed",
        "hero_id": 101,
        "map_id": 4501,
        "round": 3,
        "phase": phase,
        "session_id": SESSION,
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": source["qualification"],
        "official_distribution_eligible": True,
        "action_result_rows": [copy.deepcopy(row)],
        "structured_ref_inputs": {
            "source": "monitor_pre_settlement_field_updates",
            "total_count": 50,
            "fixed_counts": {
                "q1": 11,
                "q3": 19,
                "q4": 9,
                "q5": 8,
                "q6": 3,
            },
            # The screenshot discloses red+empty=30, not exact q6 cells.
            "quality_cells": {"q1": 24, "q3": 35, "q4": 28, "q5": 30},
        },
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "hero_id": 101,
                "map_id": 4501,
                "round": 3,
                "phase": phase,
                "session_id": SESSION,
                "warehouse_source": copy.deepcopy(source),
            },
            "actions": {"results": [copy.deepcopy(row)]},
            "constraints": {"public_info": {}},
            "minimap": {"status": "available", "items": []},
        },
    }


def _run(
    snapshot: dict[str, object],
    *,
    max_combos: int = 8_000,
    prepared: bool = False,
) -> dict[str, object]:
    payload = copy.deepcopy(snapshot)
    if prepared:
        payload = engine.prepare_reference_engine_snapshot(payload)
    return engine.run_reference_engine(payload, max_combos=max_combos).as_dict()


def _set_direct_result(
    snapshot: dict[str, object],
    value: object,
    *,
    raw_hex: str = RAW_HEX,
    missing: bool = False,
) -> None:
    """Change both raw and prepared mirrors without changing Float32 bits."""

    owners = (
        snapshot["action_result_rows"][0],
        snapshot["ui_contract"]["actions"]["results"][0],
    )
    for row in owners:
        if missing:
            row.pop("result", None)
        else:
            row["result"] = value
        row["result_raw_hex"] = raw_hex


def _without_direct_result(snapshot: dict[str, object]) -> dict[str, object]:
    baseline = copy.deepcopy(snapshot)
    baseline["action_result_rows"] = []
    baseline["ui_contract"]["actions"]["results"] = []
    return baseline


def _quotes(result: dict[str, object]) -> tuple[int, int, int]:
    return tuple(
        int(result[key]) for key in ("conservative", "balanced", "aggressive")
    )


def test_raw_float32_integer_total_oracle_is_not_decimal_denominator_lock() -> None:
    wire = struct.unpack("<f", bytes.fromhex(RAW_HEX))[0]
    reachable = []
    for count in range(1, 24):
        center = wire * count
        totals = range(int(center) - 2, int(center) + 3)
        if any(
            struct.unpack("<f", struct.pack("<f", total / count))[0] == wire
            for total in totals
        ):
            reachable.append(count)

    # These are candidate *total four-cell item counts*.  There are fifteen
    # Float32 arms here; this does not mean a count of fifteen.  In particular,
    # count=12 is not representable for this raw wire value.
    assert reachable == [7, 8, 9, 10, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]


def test_clean_float32_suffix_is_not_fixed_point_decimal_authority() -> None:
    assert Fraction(str(RAW_AVG)) == Fraction(6_634_305, 8)

    # Both totals are integers and both means round to the same field-9 Float32.
    # A denominator-only filter would wrongly delete the 10-item 8.29M arm.
    assert _float32_roundtrip_integer_bounds(RAW_HEX, 8) == (6_634_305, 6_634_305)
    assert _float32_roundtrip_integer_bounds(RAW_HEX, 10) == (8_292_881, 8_292_881)

    low_wire = 37_494.125
    low_hex = struct.pack("<f", low_wire).hex()
    # At lower magnitude the Float32 bucket is narrow enough that the same
    # proof naturally leaves only count=8 in this local physical window.  No
    # separate decimal-denominator shortcut is needed.
    assert [
        count
        for count in (7, 8, 9)
        if _float32_roundtrip_integer_bounds(low_hex, count) is not None
    ] == [8]


@pytest.mark.parametrize(
    ("wire", "count", "integer_total"),
    (
        # Settlement-validated local 4/6-cell controls.  Their full decoded
        # Float32 digits are stable, but literal decimal denominators 32/64 do
        # not divide the true physical counts 13/3.
        (164_044.15625, 13, 2_132_574),
        (220_139.671875, 3, 660_419),
    ),
)
def test_settlement_controls_reject_literal_decimal_denominator_lock(
    wire: float,
    count: int,
    integer_total: int,
) -> None:
    fraction = Fraction(str(wire))
    raw_hex = struct.pack("<f", wire).hex()

    assert count % fraction.denominator != 0
    bounds = _float32_roundtrip_integer_bounds(raw_hex, count)
    assert bounds is not None
    assert bounds[0] <= integer_total <= bounds[1]
    assert struct.unpack("<f", struct.pack("<f", integer_total / count))[0] == wire


@pytest.mark.parametrize("prepared", (False, True))
@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_4501_float32_mean_uses_complete_common_floor_without_band_collapse(
    _exact_package_shaped_quote_inputs: None,
    prepared: bool,
    max_combos: int,
) -> None:
    result = _run(_snapshot(), max_combos=max_combos, prepared=prepared)
    evidence = result["evidence"]
    support = evidence["shape_average_active_support"][0]

    assert _quotes(result) == EXPECTED_QUOTES
    assert EXPECTED_Q6_FLOOR < result["conservative"]
    assert result["conservative"] < result["balanced"] < result["aggressive"]
    assert evidence["shape_average_value_floors"] == {
        "q6": float(EXPECTED_Q6_FLOOR)
    }
    assert support["raw_bits_hex"] == RAW_HEX
    assert support["support_status"] == "ok"
    assert support["support_complete"] is True
    assert support["count_constraint_mode"] == (
        "integer_total_roundtrips_to_float32_mean"
    )
    assert support["decimal_denominator_authoritative"] is False
    assert support["active_status"] == "active"
    assert support["joint_support_count"] == 232
    assert "joint_support" not in support
    assert support["joint_support_publication"] == "complete_digest_only"
    assert support["joint_support_sha256"] == EXPECTED_JOINT_SUPPORT_SHA256
    # Current quality count/cell evidence removes the Float32-only 17/21/22
    # arms.  The remaining twelve entries are twelve candidate counts, not
    # count=12 and not a 9/18 decimal-denominator lock.
    assert [row["item_count"] for row in support["count_arms"]] == (
        CANDIDATE_TOTAL_FOUR_CELL_COUNTS
    )
    assert support["common_facts"]["quality_value_floors"] == {
        "q6": EXPECTED_Q6_FLOOR
    }
    assert support["active_constraints"] == [
        {
            "kind": "quality_value_floor",
            "quality": "q6",
            "value": EXPECTED_Q6_FLOOR,
        }
    ]
    assert "shape_avg_4cell_complete_joint_active" in result["notes"]


@pytest.mark.parametrize("prepared", (False, True))
@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_direct_field9_float32_requires_builtin_finite_parser_value(
    _exact_package_shaped_quote_inputs: None,
    prepared: bool,
    max_combos: int,
) -> None:
    snapshot = _snapshot()

    assert type(snapshot["action_result_rows"][0]["result"]) is float  # noqa: E721
    result = _run(snapshot, max_combos=max_combos, prepared=prepared)

    assert _quotes(result) == EXPECTED_QUOTES
    assert result["evidence"]["shape_average_active_support"][0][
        "raw_bits_hex"
    ] == RAW_HEX


@pytest.mark.parametrize("prepared", (False, True))
@pytest.mark.parametrize("max_combos", (8_000, 50_000))
@pytest.mark.parametrize(
    ("label", "value", "raw_hex", "missing"),
    (
        ("string", "829288.125", RAW_HEX, False),
        ("integer", 829_288, RAW_HEX, False),
        ("bool", True, RAW_HEX, False),
        ("decimal", Decimal("829288.125"), RAW_HEX, False),
        ("float_subclass", _FloatSubclass(RAW_AVG), RAW_HEX, False),
        ("coercible", _CoercibleResult(), RAW_HEX, False),
        ("nan", float("nan"), RAW_HEX, False),
        ("positive_infinity", float("inf"), RAW_HEX, False),
        ("negative_infinity", float("-inf"), RAW_HEX, False),
        ("missing", None, RAW_HEX, True),
        ("raw_mismatch", RAW_AVG, "00000000", False),
    ),
)
def test_direct_field9_invalid_value_or_wire_is_atomic_noop(
    _exact_package_shaped_quote_inputs: None,
    prepared: bool,
    max_combos: int,
    label: str,
    value: object,
    raw_hex: str,
    missing: bool,
) -> None:
    snapshot = _snapshot()
    _set_direct_result(snapshot, value, raw_hex=raw_hex, missing=missing)

    result = _run(snapshot, max_combos=max_combos, prepared=prepared)
    baseline = _run(
        _without_direct_result(_snapshot()),
        max_combos=max_combos,
        prepared=prepared,
    )

    assert result == baseline, label
    assert _quotes(result) == PARENT_QUOTES
    assert "shape_average_value_floors" not in result["evidence"]
    assert "shape_average_active_support" not in result["evidence"]


@pytest.mark.parametrize(
    "value",
    (
        "829288.125",
        Decimal("829288.125"),
        _FloatSubclass(RAW_AVG),
        _CoercibleResult(),
    ),
)
def test_diagnostics_reader_does_not_relax_direct_field9_type(
    value: object,
) -> None:
    row = _row()
    row["result"] = value

    assert size_avg_readings_from_action_rows([row]) == ()


@pytest.mark.parametrize("prepared", (False, True))
@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_direct_field9_conflicting_duplicate_is_atomic_noop(
    _exact_package_shaped_quote_inputs: None,
    prepared: bool,
    max_combos: int,
) -> None:
    snapshot = _snapshot()
    duplicate = _row(RAW_AVG + 128.0)
    snapshot["action_result_rows"].append(copy.deepcopy(duplicate))
    snapshot["ui_contract"]["actions"]["results"].append(duplicate)

    result = _run(snapshot, max_combos=max_combos, prepared=prepared)
    baseline = _run(
        _without_direct_result(_snapshot()),
        max_combos=max_combos,
        prepared=prepared,
    )

    assert result == baseline
    assert "shape_average_value_floors" not in result["evidence"]
    assert "shape_average_active_support" not in result["evidence"]


def test_rollback_is_parent_equivalent_and_omits_active_wire(
    _exact_package_shaped_quote_inputs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    without_reading = copy.deepcopy(snapshot)
    without_reading["action_result_rows"] = []
    without_reading["ui_contract"]["actions"]["results"] = []

    monkeypatch.setenv(SHAPE_AVG_ACTIVE_JOINT_DISABLE, "1")
    rollback = _run(snapshot)
    parent = _run(without_reading)

    assert rollback == parent
    assert _quotes(rollback) == PARENT_QUOTES
    assert "shape_average_value_floors" not in rollback["evidence"]
    assert "shape_average_active_support" not in rollback["evidence"]


def test_current_four_cell_direct_sibling_keeps_mean_arm_inactive(
    _exact_package_shaped_quote_inputs: None,
) -> None:
    snapshot = _snapshot()
    snapshot["physical_candidate_domains"] = [
        {
            "runtime_id": 4501001,
            "local_index": 4,
            "cells": 4,
            "shape_key": "22",
            "required_categories": [107],
            "excluded_categories": [106],
            "excluded_qualities": [1, 2, 3, 4, 5],
            "candidate_item_ids": [1076005],
            "candidate_qualities": [6],
            "candidate_values": [5_603_066],
            "catalog_status": "ok",
            "catalog_generation": "physical-category-v1:shape-avg-precedence",
            "hard_eligible": True,
            "quality": 6,
            "quality_source": "catalog_red_domain",
            "exact_item_id": 1076005,
            "exact_value": 5_603_066,
        }
    ]

    result = _run(snapshot)

    assert _quotes(result) == PARENT_QUOTES
    assert result["evidence"]["physical_candidate_domains"] == (
        snapshot["physical_candidate_domains"]
    )
    assert "shape_average_value_floors" not in result["evidence"]
    assert "shape_average_active_support" not in result["evidence"]


def _domain(
    candidates: list[tuple[int, int, int]],
) -> dict[str, object]:
    return {
        "map_id": 5608,
        "footprint_cells": 4,
        "catalog_status": "ok",
        "catalog_generation": "test-generation",
        "candidates": [
            {
                "item_id": item_id,
                "quality": quality,
                "value": value,
                "cells": 4,
            }
            for item_id, quality, value in candidates
        ],
    }


def _options(**counts: int) -> dict[int, tuple[int, ...]]:
    return {
        quality: (counts.get(f"q{quality}", 0),)
        for quality in range(1, 7)
    }


def _active_evidence(
    value: float,
    candidates: list[tuple[int, int, int]],
    options: dict[int, tuple[int, ...]],
) -> dict[str, object]:
    return shape_average_value_evidence_from_action_rows(
        [_row(value, session_id="5608:1")],
        current_session_id="5608:1",
        candidate_domains_by_cells={4: _domain(candidates)},
        quality_count_options_by_cells={4: options},
        exact_total_item_count=sum(max(row) for row in options.values()),
        active_joint=True,
    )[0]


def test_low_value_requires_aggregate_uniqueness_but_not_unique_item_identity() -> None:
    nonunique = _active_evidence(
        100_000.0,
        [(1, 5, 100_000), (2, 6, 100_000)],
        {1: (0,), 2: (0,), 3: (0,), 4: (0,), 5: (0, 1), 6: (0, 1)},
    )
    equal_price_ids = _active_evidence(
        100_000.0,
        [(11, 6, 100_000), (12, 6, 100_000)],
        _options(q6=1),
    )

    assert nonunique["support_complete"] is True
    assert nonunique["joint_support_count"] > 1
    assert nonunique["active_status"] == "low_value_nonunique"
    assert nonunique["active_constraints"] == []
    assert nonunique["quote_effect"] == "shadow"
    assert equal_price_ids["support_complete"] is True
    assert equal_price_ids["joint_support_count"] == 1
    assert equal_price_ids["active_status"] == "active"
    assert equal_price_ids["active_constraints"] == [
        {"kind": "quality_value_floor", "quality": "q6", "value": 100_000}
    ]
    assert equal_price_ids["count_one"] == {
        "candidate_item_ids": [11, 12],
        "candidate_count": 2,
        "unique_item_id": None,
    }


@pytest.mark.parametrize(
    ("mean", "candidates", "active_status", "constraints"),
    (
        (
            499_999.0,
            [
                (13, 5, 299_998),
                (14, 5, 399_998),
                (15, 6, 600_000),
                (16, 6, 700_000),
            ],
            "low_value_nonunique",
            [],
        ),
        (
            500_000.0,
            [
                (13, 5, 300_000),
                (14, 5, 400_000),
                (15, 6, 600_000),
                (16, 6, 700_000),
            ],
            "active",
            [
                {
                    "kind": "quality_value_floor",
                    "quality": "q5",
                    "value": 300_000,
                },
                {
                    "kind": "quality_value_floor",
                    "quality": "q6",
                    "value": 600_000,
                }
            ],
        ),
    ),
)
def test_nonunique_support_uses_existing_four_cell_high_value_boundary(
    mean: float,
    candidates: list[tuple[int, int, int]],
    active_status: str,
    constraints: list[dict[str, object]],
) -> None:
    evidence = _active_evidence(
        mean,
        candidates,
        _options(q5=1, q6=1),
    )

    assert evidence["support_complete"] is True
    assert evidence["joint_support_count"] == 2
    assert evidence["active_status"] == active_status
    assert evidence["active_constraints"] == constraints


def test_partition_budget_failure_publishes_no_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bidking_lab.inference.size_avg_evidence as module

    monkeypatch.setattr(
        module,
        "_cached_partition_mean_support",
        lambda *_args, **_kwargs: MeanPartitionSupportResult(
            "output_complexity_guard"
        ),
    )
    evidence = _active_evidence(
        600_000.0,
        [(21, 5, 500_000), (22, 6, 700_000)],
        {1: (0,), 2: (0,), 3: (0,), 4: (0,), 5: (0, 1, 2), 6: (0, 1, 2)},
    )

    assert evidence["support_status"] == "output_complexity_guard"
    assert evidence["support_complete"] is False
    assert evidence["joint_support"] == []
    assert evidence["count_arms"] == []
    assert evidence["common_facts"] == {}
    assert evidence["active_constraints"] == []
    assert evidence["quote_effect"] == "none_new_structured_evidence_only"


def test_partition_solver_uses_cap_plus_one_before_publication() -> None:
    candidates = (
        MeanSupportCandidate(23, 5, 300_000, 4),
        MeanSupportCandidate(24, 5, 400_000, 4),
        MeanSupportCandidate(25, 6, 600_000, 4),
        MeanSupportCandidate(26, 6, 700_000, 4),
    )
    options = _options(q5=1, q6=1)

    incomplete = solve_repeat_allowed_mean_partition_support(
        candidates,
        total_lower=1_000_000,
        total_upper=1_000_000,
        item_count=2,
        quality_count_options=options,
        output_state_budget=1,
    )
    complete = solve_repeat_allowed_mean_partition_support(
        candidates,
        total_lower=1_000_000,
        total_upper=1_000_000,
        item_count=2,
        quality_count_options=options,
        output_state_budget=2,
    )

    assert incomplete.status == "output_complexity_guard"
    assert incomplete.states == ()
    assert complete.status == "ok"
    assert len(complete.states) == 2


def test_bitset_width_guard_is_fail_closed() -> None:
    solved = solve_repeat_allowed_mean_partition_support(
        (
            MeanSupportCandidate(31, 5, 100, 4),
            MeanSupportCandidate(32, 6, 200, 4),
        ),
        total_lower=300,
        total_upper=300,
        item_count=2,
        quality_count_options={
            1: (0,),
            2: (0,),
            3: (0,),
            4: (0,),
            5: (1,),
            6: (1,),
        },
        bitset_width_budget=100,
    )

    assert solved.status == "partition_complexity_guard"
    assert solved.states == ()
    assert solved.count_one_candidate_item_ids == ()


def test_partition_solver_matches_small_bruteforce_pivot_oracle() -> None:
    candidates = (
        MeanSupportCandidate(41, 1, 100, 4),
        MeanSupportCandidate(42, 4, 200, 4),
        MeanSupportCandidate(43, 5, 300, 4),
        MeanSupportCandidate(44, 6, 400, 4),
        MeanSupportCandidate(45, 6, 500, 4),
    )
    intervals = {1: (100, 500), 2: (300, 900), 3: (600, 1_300)}
    options = {
        1: (0, 1, 2, 3),
        2: (0,),
        3: (0,),
        4: (0, 1, 2, 3),
        5: (0, 1, 2, 3),
        6: (0, 1, 2, 3),
    }
    solved = solve_repeat_allowed_mean_partition_support(
        candidates,
        total_intervals_by_count=intervals,
        quality_count_options=options,
    )

    expected: set[tuple[int, int, int, int]] = set()
    for count, (lower, upper) in intervals.items():
        for chosen in itertools.product(candidates, repeat=count):
            total = sum(candidate.value for candidate in chosen)
            quality_counts = {
                quality: sum(candidate.quality == quality for candidate in chosen)
                for quality in range(1, 7)
            }
            if not lower <= total <= upper or any(
                quality_counts[quality] not in options[quality]
                for quality in range(1, 7)
            ):
                continue
            expected.add(
                (
                    count,
                    total,
                    quality_counts[6],
                    sum(
                        candidate.value
                        for candidate in chosen
                        if candidate.quality == 6
                    ),
                )
            )
    actual = {
        (state.item_count, state.total_value, state.pivot_count, state.pivot_value)
        for state in solved.states
    }

    assert solved.status == "ok"
    assert actual == expected


@pytest.mark.parametrize(
    "mutation",
    ("foreign_session", "conflicting_duplicate", "settled", "wrong_action"),
)
def test_noncurrent_conflicting_settled_and_nontarget_rows_do_not_activate(
    mutation: str,
) -> None:
    snapshot = _snapshot()
    if mutation == "foreign_session":
        snapshot["action_result_rows"][0]["session_id"] = "4501:other"
        snapshot["ui_contract"]["actions"]["results"][0]["session_id"] = (
            "4501:other"
        )
    elif mutation == "conflicting_duplicate":
        conflicting = _row(RAW_AVG + 128.0)
        snapshot["action_result_rows"].append(copy.deepcopy(conflicting))
        snapshot["ui_contract"]["actions"]["results"].append(conflicting)
    elif mutation == "settled":
        snapshot["phase"] = "settled"
        snapshot["ui_contract"]["context"]["phase"] = "settled"
    else:
        snapshot["action_result_rows"][0]["action_id"] = 100171
        snapshot["ui_contract"]["actions"]["results"][0]["action_id"] = 100171

    result = _run(snapshot)

    assert "shape_average_value_floors" not in result["evidence"]
    assert "shape_average_active_support" not in result["evidence"]
    assert "shape_avg_4cell_complete_joint_active" not in result["notes"]


def test_summary_loopback_api_and_tk_consume_one_engine_result(
    _exact_package_shaped_quote_inputs: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.delenv(SHAPE_AVG_ACTIVE_JOINT_DISABLE, raising=False)

    summary = panel.summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        panel.make_handler(
            panel.ServerConfig(
                project_root=tmp_path,
                snapshot=Path("snapshot.json"),
            )
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - self-created loopback product contract
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=30,
        ) as response:
            assert response.status == 200
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)

    tk_row = overlay._summary_diagnostic_row(
        payload,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )
    assert _quotes(summary["ahmed_ref"]) == EXPECTED_QUOTES
    assert _quotes(payload["ahmed_ref"]) == EXPECTED_QUOTES
    assert tuple(
        tk_row["reference"][key]
        for key in ("conservative", "balanced", "aggressive")
    ) == tuple(f"{value:,}" for value in EXPECTED_QUOTES)
    assert "shape_avg_4cell_complete_joint_active" in tk_row["ref_v0"]["notes"]
    assert payload["ahmed_ref"]["evidence"][
        "shape_average_value_floors"
    ] == {"q6": float(EXPECTED_Q6_FLOOR)}
