"""Lane C phase-1 contract for full-q6 average multiset support.

This file intentionally contains a small source-external reference solver.  It accepts an
already-canonical candidate domain; it does not load Item.txt, infer categories, filter boxes by
name, or decide map reachability.  The production source must receive that domain from Lane A.

An earlier Lane-C phase recorded two known-fails for the legacy
``public q6 avg == one catalog price -> fixed_counts[q6] = 1`` behavior.  The current exact parent
already preserves the count/cell arms, while the product tests below add and verify the missing
value-correlated support on the combined Lane-A/Lane-C source.
"""

from __future__ import annotations

import builtins
import math
import struct
import sys
from dataclasses import dataclass
from itertools import combinations_with_replacement
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pytest

ROOT = Path(__file__).resolve().parents[1]
HERO_REF_SRC = ROOT / "hero_ref" / "src"
if str(HERO_REF_SRC) not in sys.path:
    sys.path.insert(0, str(HERO_REF_SRC))

import ahmad_ref_engine as engine  # noqa: E402
from bidking_lab.inference.size_avg_evidence import (  # noqa: E402
    _float32_interval_from_wire,
)
from bidking_lab.inference.q6_full_avg_evidence import (  # noqa: E402
    integer_total_bounds_from_q6_full_average,
    q6_full_average_value_evidence_from_public_rows,
)
from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansCaptureEvents,
    FatbeansPublicInfo,
    FatbeansStateEvent,
    _parse_public_info,
)
from bidking_lab.live.monitor import _public_info_rows  # noqa: E402
from bidking_lab.runtime.snapshot import (  # noqa: E402
    _ui_public_numeric_contract,
    ui_contract_from_artifact,
)


@dataclass(frozen=True, order=True)
class CanonicalQ6Candidate:
    """One Lane-A-supplied, current-map-reachable q6 catalog candidate."""

    item_id: int
    value: int
    cells: int


@dataclass(frozen=True)
class Float32MeanEquation:
    raw_hex: str
    parsed_value: float
    lower: float
    upper: float
    session_id: str


@dataclass(frozen=True, order=True)
class ReferenceSupportRow:
    count: int
    cells: int
    total_value: int
    item_ids: tuple[int, ...]


@dataclass(frozen=True)
class ReferenceSupport:
    status: str
    rows: tuple[ReferenceSupportRow, ...] = ()
    joint_states: tuple[tuple[int, int, int], ...] = ()
    count_floor: int | None = None
    cells_floor: int | None = None
    total_value_floor: int | None = None
    count_one_unique_item_id: int | None = None
    global_correlated_item_id: int | None = None
    unavoidable_item_ids: tuple[int, ...] = ()


def _full_q6_avg_row(
    value: float,
    *,
    session_id: str = "2401:123",
    raw_hex: str | None = None,
    wire_type: str = "float32",
    endian: str = "little",
) -> dict[str, Any]:
    raw = struct.pack("<f", value)
    parsed = struct.unpack("<f", raw)[0]
    return {
        "info_id": 200038,
        "semantic": "q6_avg_value",
        "kind": "avg_value",
        "quality": 6,
        "value": parsed,
        "value_field": 9,
        "value_wire_type": wire_type,
        "value_raw_hex": raw.hex() if raw_hex is None else raw_hex,
        "value_endian": endian,
        "session_id": session_id,
    }


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field_varint(field_no: int, value: int) -> bytes:
    return _varint((field_no << 3) | 0) + _varint(value)


def _field_fixed32_raw(field_no: int, raw: bytes) -> bytes:
    return _varint((field_no << 3) | 5) + raw


def _field_fixed64_raw(field_no: int, raw: bytes) -> bytes:
    return _varint((field_no << 3) | 1) + raw


def _reference_full_q6_mean_equation(
    rows: Sequence[Mapping[str, Any]],
    *,
    current_session_id: str,
) -> tuple[str, Float32MeanEquation | None]:
    """Strict phase-1 oracle for the 200038-only Float32 provenance contract."""

    selected: list[tuple[str, Float32MeanEquation]] = []
    for row in rows:
        if (
            type(row) is not dict  # noqa: E721 - exact JSON container is part of the contract
            or type(row.get("info_id")) is not int  # noqa: E721
            or row.get("info_id") != 200038
        ):
            continue
        if (
            row.get("semantic") != "q6_avg_value"
            or row.get("kind") != "avg_value"
            or row.get("quality") != 6
            or row.get("value_field") not in {9, 11}
            or row.get("value_wire_type") != "float32"
            or row.get("value_endian") != "little"
            or row.get("session_id") != current_session_id
        ):
            return "conflict", None
        raw_hex = row.get("value_raw_hex")
        interval = _float32_interval_from_wire(
            raw_hex=raw_hex,
            parsed_value=row.get("value"),
        )
        if interval is None:
            return "conflict", None
        equation = Float32MeanEquation(
            raw_hex=str(raw_hex).lower(),
            parsed_value=float(row["value"]),
            lower=float(interval[0]),
            upper=float(interval[1]),
            session_id=current_session_id,
        )
        selected.append((equation.raw_hex, equation))
    if not selected:
        return "not_applicable", None
    unique = {raw_hex for raw_hex, _equation in selected}
    if len(unique) != 1:
        return "duplicate_conflict", None
    return "ok", selected[0][1]


def _reference_repeat_allowed_support(
    candidates: Iterable[CanonicalQ6Candidate],
    *,
    count_domain: Iterable[int],
    mean: Float32MeanEquation,
    exact_cells: int | None = None,
    minimum_cells: int = 0,
) -> ReferenceSupport:
    """Enumerate exact synthetic supports without owning catalog recognition.

    This deliberately slow oracle is bounded to the tiny source-external fixtures below.  The
    product solver may use DP/bitsets, but it must return the same support semantics.
    """

    by_id: dict[int, CanonicalQ6Candidate] = {}
    for candidate in candidates:
        if (
            isinstance(candidate.item_id, bool)
            or not isinstance(candidate.item_id, int)
            or isinstance(candidate.value, bool)
            or not isinstance(candidate.value, int)
            or isinstance(candidate.cells, bool)
            or not isinstance(candidate.cells, int)
            or candidate.value <= 0
            or candidate.cells <= 0
        ):
            return ReferenceSupport(status="candidate_domain_conflict")
        previous = by_id.get(candidate.item_id)
        if previous is not None and previous != candidate:
            return ReferenceSupport(status="candidate_domain_conflict")
        by_id[candidate.item_id] = candidate
    ordered = tuple(sorted(by_id.values()))
    if not ordered:
        return ReferenceSupport(status="catalog_unavailable")

    rows: set[ReferenceSupportRow] = set()
    for count in sorted({int(value) for value in count_domain if int(value) > 0}):
        lower_total = math.ceil(mean.lower * count)
        upper_total = math.floor(mean.upper * count)
        if lower_total > upper_total:
            continue
        for indexes in combinations_with_replacement(range(len(ordered)), count):
            chosen = tuple(ordered[index] for index in indexes)
            total_value = sum(candidate.value for candidate in chosen)
            if not lower_total <= total_value <= upper_total:
                continue
            cells = sum(candidate.cells for candidate in chosen)
            if exact_cells is not None and cells != exact_cells:
                continue
            if cells < int(minimum_cells):
                continue
            rows.add(
                ReferenceSupportRow(
                    count=count,
                    cells=cells,
                    total_value=total_value,
                    item_ids=tuple(candidate.item_id for candidate in chosen),
                )
            )
    ordered_rows = tuple(sorted(rows))
    if not ordered_rows:
        return ReferenceSupport(status="no_support")

    count_one_rows = tuple(row for row in ordered_rows if row.count == 1)
    count_one_ids = {
        row.item_ids[0]
        for row in count_one_rows
        if len(row.item_ids) == 1
    }
    count_one_unique = next(iter(count_one_ids)) if len(count_one_ids) == 1 else None
    unavoidable = set(ordered_rows[0].item_ids)
    for row in ordered_rows[1:]:
        unavoidable.intersection_update(row.item_ids)
    global_correlated = (
        count_one_unique
        if count_one_unique is not None
        and len(count_one_rows) == len(ordered_rows)
        and all(row.item_ids == (count_one_unique,) for row in ordered_rows)
        else None
    )
    return ReferenceSupport(
        status="ok",
        rows=ordered_rows,
        joint_states=tuple(
            sorted({(row.count, row.cells, row.total_value) for row in ordered_rows})
        ),
        count_floor=min(row.count for row in ordered_rows),
        cells_floor=min(row.cells for row in ordered_rows),
        total_value_floor=min(row.total_value for row in ordered_rows),
        count_one_unique_item_id=count_one_unique,
        global_correlated_item_id=global_correlated,
        unavoidable_item_ids=tuple(sorted(unavoidable)),
    )


def test_q6_catalog_avg_helper_does_not_force_fixed_count_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine,
        "_exact_item_values_by_table_quality",
        lambda: {6: frozenset({143_100})},
    )
    fixed_counts: dict[str, int] = {}
    min_counts: dict[str, int] = {}
    notes: list[str] = []

    engine._apply_exact_catalog_value_locks(
        fixed_counts=fixed_counts,
        min_counts=min_counts,
        avg_values={"q6": 143_100.0},
        quality_values={},
        source_notes=notes,
    )

    assert fixed_counts.get("q6") is None
    assert min_counts.get("q6") is None


def test_signed_deviation_dp_matches_bruteforce_joint_support() -> None:
    candidates = (
        CanonicalQ6Candidate(1, 80, 1),
        CanonicalQ6Candidate(2, 100, 2),
        CanonicalQ6Candidate(3, 120, 4),
        CanonicalQ6Candidate(4, 140, 3),
    )
    row = _full_q6_avg_row(100.0)
    reference_status, mean = _reference_full_q6_mean_equation(
        [row],
        current_session_id="2401:123",
    )
    assert reference_status == "ok" and mean is not None
    reference = _reference_repeat_allowed_support(
        candidates,
        count_domain=range(1, 6),
        mean=mean,
    )
    product_mean = q6_full_average_value_evidence_from_public_rows(
        [row],
        current_session_id="2401:123",
    )

    product_states: set[tuple[int, int, int]] = set()
    pairs = tuple((candidate.value, candidate.cells) for candidate in candidates)
    for count in range(1, 6):
        bounds = integer_total_bounds_from_q6_full_average(product_mean, count)
        assert bounds.status == "ok"
        assert bounds.lower is not None and bounds.upper is not None
        status, states = engine._exact_fixed_count_cells_value_candidates_from_catalog(
            pairs,
            bounds.lower,
            bounds.upper,
            count,
            None,
        )
        assert status == "ok"
        product_states.update(states)

    assert tuple(sorted(product_states)) == reference.joint_states


def test_strict_200038_caller_keeps_q6_count_unfixed_and_presence_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine,
        "_exact_item_values_by_table_quality",
        lambda: {6: frozenset({143_100})},
    )
    evidence = engine.extract_evidence(
        {
            "session_id": "2401:123",
            "ui_contract": {
                "context": {
                    "hero": "ahmed",
                    "map_id": 2401,
                    "phase": "bidding",
                    "round": 2,
                    "session_id": "2401:123",
                },
                "constraints": {
                    "public_info": {
                        "public_numeric_facts": [_full_q6_avg_row(143_100.0)]
                    }
                },
            },
        }
    )

    assert evidence.fixed_counts.get("q6") is None
    assert evidence.min_counts.get("q6") == 1


def test_exact_200038_helper_import_failure_is_visible_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _full_q6_avg_row(100.0)
    original_import = builtins.__import__

    def blocked_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "bidking_lab.inference.q6_full_avg_evidence":
            raise ImportError("synthetic missing strict helper")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    evidence = engine._strict_q6_full_avg_evidence(
        {"public_numeric_facts": [row]},
        current_session="2401:123",
    )

    assert evidence.status == "conflict"
    assert evidence.conflicts == ("strict_evidence_helper_unavailable",)


def test_q6_exact_total_branch_remains_single_item_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine,
        "_exact_item_values_by_table_quality",
        lambda: {6: frozenset({143_100})},
    )
    fixed_counts: dict[str, int] = {}
    min_counts: dict[str, int] = {}

    engine._apply_exact_catalog_value_locks(
        fixed_counts=fixed_counts,
        min_counts=min_counts,
        avg_values={},
        quality_values={"q6": 143_100.0},
        source_notes=[],
    )

    assert fixed_counts == {"q6": 1}
    assert min_counts == {"q6": 1}


@pytest.mark.parametrize(
    ("mutation", "expected_status"),
    [
        ({"value_raw_hex": None}, "conflict"),
        ({"value_raw_hex": "0000000000000000", "value_wire_type": "float64"}, "conflict"),
        ({"value_endian": "big"}, "conflict"),
        ({"value_raw_hex": "00000080", "value": -0.0}, "conflict"),
        ({"value_raw_hex": "01000000", "value": struct.unpack("<f", bytes.fromhex("01000000"))[0]}, "conflict"),
        ({"value_raw_hex": "0000807f", "value": float("inf")}, "conflict"),
        ({"value_raw_hex": "0000c07f", "value": float("nan")}, "conflict"),
        ({"session_id": "2401:999"}, "conflict"),
    ],
    ids=(
        "missing-raw",
        "float64",
        "wrong-endian",
        "negative-zero",
        "subnormal",
        "infinity",
        "nan",
        "cross-session",
    ),
)
def test_reference_mean_equation_fails_closed_on_wire_and_session_boundaries(
    mutation: dict[str, Any],
    expected_status: str,
) -> None:
    row = _full_q6_avg_row(143_100.0)
    row.update(mutation)

    status, equation = _reference_full_q6_mean_equation(
        [row],
        current_session_id="2401:123",
    )

    assert status == expected_status
    assert equation is None
    product = q6_full_average_value_evidence_from_public_rows(
        [row],
        current_session_id="2401:123",
    )
    assert product.status == "conflict"
    assert product.avg_interval is None


def test_reference_mean_equation_rejects_duplicate_conflict() -> None:
    status, equation = _reference_full_q6_mean_equation(
        [_full_q6_avg_row(143_100.0), _full_q6_avg_row(143_101.0)],
        current_session_id="2401:123",
    )

    assert status == "duplicate_conflict"
    assert equation is None
    product = q6_full_average_value_evidence_from_public_rows(
        [_full_q6_avg_row(143_100.0), _full_q6_avg_row(143_101.0)],
        current_session_id="2401:123",
    )
    assert product.status == "conflict"
    assert product.conflicts == ("duplicate_conflict",)


@pytest.mark.parametrize(
    "row",
    [
        {"info_id": 200037, "semantic": "q5_avg_value", "kind": "avg_value", "quality": 5},
        {"info_id": 200036, "semantic": "q4_avg_value", "kind": "avg_value", "quality": 4},
        {"info_id": 200033, "semantic": "random_9_avg_value", "kind": "random_avg_value"},
        {"action_id": 100163, "semantic": "direct_item_value", "kind": "item_value"},
        {"action_id": 100169, "semantic": "shape_avg_value", "kind": "avg_value"},
        {"action_id": 100126, "semantic": "q6_total_value", "kind": "exact_total_value"},
        {"semantic": "q6_avg_value", "kind": "avg_value", "quality": 6},
    ],
    ids=(
        "q5-full",
        "q4-full",
        "random-n",
        "direct-item",
        "shape-average",
        "q6-exact-total",
        "legacy-semantic-only-q6",
    ),
)
def test_reference_mean_equation_is_strictly_200038_only(row: dict[str, Any]) -> None:
    status, equation = _reference_full_q6_mean_equation(
        [row],
        current_session_id="2401:123",
    )

    assert status == "not_applicable"
    assert equation is None
    assert q6_full_average_value_evidence_from_public_rows(
        [row],
        current_session_id="2401:123",
    ).status == "not_applicable"


def test_product_parser_preserves_float32_wire_and_marks_mixed_duplicate_bad() -> None:
    raw = struct.pack("<f", 143_100.0)
    prefix = _field_varint(1, 200038) + _field_varint(3, 5608)

    good = _parse_public_info(prefix + _field_fixed32_raw(9, raw))
    assert good is not None
    assert good.value == struct.unpack("<f", raw)[0]
    assert good.value_field == 9
    assert good.value_wire_type == "float32"
    assert good.value_raw_hex == raw.hex()
    assert good.value_endian == "little"
    assert good.value_conflicts == ()

    float64_raw = struct.pack("<d", 143_100.0)
    mixed = _parse_public_info(
        prefix
        + _field_fixed32_raw(9, raw)
        + _field_fixed64_raw(9, float64_raw)
    )
    assert mixed is not None
    assert mixed.value_wire_type == "float32"
    assert mixed.value_raw_hex == raw.hex()
    assert set(mixed.value_conflicts) == {
        "duplicate_value_conflict",
    }

    float64_only = _parse_public_info(
        prefix + _field_fixed64_raw(9, float64_raw)
    )
    assert float64_only is not None
    assert float64_only.value_wire_type == "float64"
    assert float64_only.value_raw_hex == float64_raw.hex()
    assert float64_only.value_conflicts == ("invalid_value_wire",)


@pytest.mark.parametrize("bad_is_latest", (False, True))
def test_monitor_never_drops_conflicting_q6_wire_observation(
    bad_is_latest: bool,
) -> None:
    raw = struct.pack("<f", 143_100.0)
    good = FatbeansPublicInfo(
        info_id=200038,
        map_id=5608,
        value=struct.unpack("<f", raw)[0],
        value_field=9,
        value_wire_type="float32",
        value_raw_hex=raw.hex(),
        value_endian="little",
    )
    bad_raw = struct.pack("<d", 143_100.0)
    bad = FatbeansPublicInfo(
        info_id=200038,
        map_id=5608,
        value=0.0,
        value_field=9,
        value_wire_type="float64",
        value_raw_hex=bad_raw.hex(),
        value_endian="little",
        value_conflicts=("invalid_value_wire",),
    )
    ordered = (good, bad) if bad_is_latest else (bad, good)
    states = tuple(
        FatbeansStateEvent(
            sort_id=index,
            capture_time=f"2026-08-08 12:00:0{index}.000",
            message_id=0x0025,
            session_id="5608:strict",
            map_id=5608,
            round_index=2,
            public_infos=(info,),
        )
        for index, info in enumerate(ordered, 1)
    )

    rows = _public_info_rows(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=(),
            statuses=(),
            states=states,
        ),
        {},
        session_id="5608:strict",
    )

    assert len(rows) == 1
    assert set(rows[0]["value_conflicts"]) == {
        "cross_observation_value_conflict",
        "invalid_value_wire",
    }
    evidence = q6_full_average_value_evidence_from_public_rows(
        [
            {
                **rows[0],
                "semantic": "q6_avg_value",
                "kind": "avg_value",
                "quality": 6,
            }
        ],
        current_session_id="5608:strict",
    )
    assert evidence.status == "conflict"
    assert "parser_duplicate_or_wire_conflict" in evidence.conflicts


def test_snapshot_publishes_one_strict_q6_mean_source_and_fail_closed_fact() -> None:
    valid = _full_q6_avg_row(143_100.0, session_id="5608:strict")
    raw_monitor_row = {
        key: value
        for key, value in valid.items()
        if key not in {"semantic", "kind", "quality"}
    }
    projected = _ui_public_numeric_contract(
        [raw_monitor_row],
        current_session_id="5608:strict",
    )
    assert projected["q6_full_avg_evidence"]["status"] == "ok"
    assert projected["public_avg_values"] == projected["public_numeric_facts"]
    assert projected["public_avg_values"][0]["value_raw_hex"] == raw_monitor_row[
        "value_raw_hex"
    ]
    assert projected["public_avg_values"][0]["avg_interval"] == projected[
        "q6_full_avg_evidence"
    ]["avg_interval"]
    full_contract = ui_contract_from_artifact(
        {
            "session_id": "5608:strict",
            "public_info_rows": [raw_monitor_row],
        }
    )
    public_info = full_contract["constraints"]["public_info"]
    assert public_info["q6_full_avg_evidence"]["status"] == "ok"
    assert public_info["public_avg_values"][0]["value_raw_hex"] == (
        raw_monitor_row["value_raw_hex"]
    )

    invalid = {
        **raw_monitor_row,
        "value_conflicts": ["duplicate_value_conflict"],
    }
    blocked = _ui_public_numeric_contract(
        [raw_monitor_row, invalid],
        current_session_id="5608:strict",
    )
    assert blocked["q6_full_avg_evidence"]["status"] == "conflict"
    assert blocked["public_avg_values"] == []
    assert blocked["public_numeric_facts"][0]["value"] is None
    assert blocked["public_numeric_facts"][0]["display_value"] == "不可用"


def test_reference_support_keeps_ancient_sword_box_and_other_item_pair() -> None:
    status, mean = _reference_full_q6_mean_equation(
        [_full_q6_avg_row(143_100.0)],
        current_session_id="2401:123",
    )
    assert status == "ok" and mean is not None
    candidates = (
        CanonicalQ6Candidate(1046007, 143_100, 3),  # 古剑
        CanonicalQ6Candidate(1116028, 143_100, 3),  # 红色 3x1 文玩藏品箱
        CanonicalQ6Candidate(9000001, 127_260, 1),
        CanonicalQ6Candidate(9000002, 158_940, 4),
    )

    support = _reference_repeat_allowed_support(
        candidates,
        count_domain=(1, 2),
        mean=mean,
    )

    assert support.status == "ok"
    assert (1, 3, 143_100) in support.joint_states
    assert (2, 5, 286_200) in support.joint_states
    assert support.count_one_unique_item_id is None
    assert support.global_correlated_item_id is None
    assert support.unavoidable_item_ids == ()
    assert support.count_floor == 1
    assert support.total_value_floor == 143_100
    assert support.cells_floor == 3


@pytest.mark.parametrize(
    ("value", "candidates", "expected_cells"),
    [
        (
            888_888,
            (
                CanonicalQ6Candidate(1016009, 888_888, 1),
                CanonicalQ6Candidate(1016014, 888_888, 4),
            ),
            {1, 4},
        ),
        (
            4_000_000,
            (
                CanonicalQ6Candidate(1006004, 4_000_000, 3),
                CanonicalQ6Candidate(1006006, 4_000_000, 6),
            ),
            {3, 6},
        ),
    ],
)
def test_reference_support_same_price_cross_cell_keeps_every_cell_arm(
    value: int,
    candidates: tuple[CanonicalQ6Candidate, ...],
    expected_cells: set[int],
) -> None:
    status, mean = _reference_full_q6_mean_equation(
        [_full_q6_avg_row(float(value))],
        current_session_id="2401:123",
    )
    assert status == "ok" and mean is not None

    support = _reference_repeat_allowed_support(
        candidates,
        count_domain=(1,),
        mean=mean,
    )

    assert {cells for _count, cells, _value in support.joint_states} == expected_cells
    assert support.cells_floor == min(expected_cells)
    assert support.count_one_unique_item_id is None
    assert support.global_correlated_item_id is None


def test_reference_support_allows_repeated_items_and_multicount_same_mean() -> None:
    status, mean = _reference_full_q6_mean_equation(
        [_full_q6_avg_row(100.0)],
        current_session_id="2401:123",
    )
    assert status == "ok" and mean is not None
    repeated = CanonicalQ6Candidate(1, 100, 2)

    support = _reference_repeat_allowed_support(
        (repeated,),
        count_domain=(1, 2, 3),
        mean=mean,
    )

    assert support.joint_states == (
        (1, 2, 100),
        (2, 4, 200),
        (3, 6, 300),
    )
    assert any(row.item_ids == (1, 1, 1) for row in support.rows)
    assert support.count_one_unique_item_id == 1
    assert support.global_correlated_item_id is None
    assert support.unavoidable_item_ids == (1,)


def test_reference_support_only_emits_global_item_fact_for_unique_count_one() -> None:
    status, mean = _reference_full_q6_mean_equation(
        [_full_q6_avg_row(375_000.0)],
        current_session_id="2401:123",
    )
    assert status == "ok" and mean is not None
    target = CanonicalQ6Candidate(1916002, 375_000, 20)

    singleton = _reference_repeat_allowed_support(
        (target,),
        count_domain=(1,),
        mean=mean,
    )
    assert singleton.global_correlated_item_id == target.item_id
    assert singleton.cells_floor == 20
    assert singleton.total_value_floor == 375_000

    alternative = (
        target,
        CanonicalQ6Candidate(9100001, 300_000, 2),
        CanonicalQ6Candidate(9100002, 400_000, 3),
        CanonicalQ6Candidate(9100003, 425_000, 4),
    )
    multi = _reference_repeat_allowed_support(
        alternative,
        count_domain=(1, 3),
        mean=mean,
    )
    assert (3, 9, 1_125_000) in multi.joint_states
    assert multi.count_one_unique_item_id == target.item_id
    assert multi.global_correlated_item_id is None
    assert multi.unavoidable_item_ids == ()
    assert multi.cells_floor == 9
    assert multi.total_value_floor == 375_000


def test_reference_support_new_droppable_expands_support_without_special_cases() -> None:
    status, mean = _reference_full_q6_mean_equation(
        [_full_q6_avg_row(888_888.0)],
        current_session_id="2401:123",
    )
    assert status == "ok" and mean is not None
    original = (CanonicalQ6Candidate(1016009, 888_888, 1),)
    expanded = original + (CanonicalQ6Candidate(9999999, 888_888, 6),)

    before = _reference_repeat_allowed_support(original, count_domain=(1,), mean=mean)
    after = _reference_repeat_allowed_support(expanded, count_domain=(1,), mean=mean)

    assert before.global_correlated_item_id == 1016009
    assert after.global_correlated_item_id is None
    assert {cells for _count, cells, _value in after.joint_states} == {1, 6}


def test_reference_support_uses_only_supplied_current_map_domain() -> None:
    status, mean = _reference_full_q6_mean_equation(
        [_full_q6_avg_row(888_888.0)],
        current_session_id="2401:123",
    )
    assert status == "ok" and mean is not None
    map_reachable_positive_weight = (
        CanonicalQ6Candidate(1016009, 888_888, 1),
    )

    support = _reference_repeat_allowed_support(
        map_reachable_positive_weight,
        count_domain=(1,),
        mean=mean,
    )
    unreachable = _reference_repeat_allowed_support((), count_domain=(1,), mean=mean)

    assert support.global_correlated_item_id == 1016009
    assert unreachable.status == "catalog_unavailable"


def test_reference_support_rejects_conflicting_duplicate_candidate_identity() -> None:
    status, mean = _reference_full_q6_mean_equation(
        [_full_q6_avg_row(100.0)],
        current_session_id="2401:123",
    )
    assert status == "ok" and mean is not None

    support = _reference_repeat_allowed_support(
        (
            CanonicalQ6Candidate(1, 100, 1),
            CanonicalQ6Candidate(1, 100, 4),
        ),
        count_domain=(1,),
        mean=mean,
    )

    assert support.status == "candidate_domain_conflict"
    assert support.rows == ()
