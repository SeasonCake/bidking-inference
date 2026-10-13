"""Hotfix2 SAV1 structured Float32 evidence for 1/2/3/4/6-cell tools."""

from __future__ import annotations

import math
from pathlib import Path
import struct
import sys

import pytest

from bidking_lab.inference.size_avg_evidence import (
    SIZE_AVG_ACTION_FOOTPRINT,
    shape_average_value_evidence_from_action_rows,
)
from bidking_lab.live.fatbeans import (
    FatbeansCaptureEvents,
    FatbeansStateEvent,
    _parse_action_result,
    parse_fatbeans_capture,
)
from bidking_lab.live.monitor import _action_result_rows, load_monitor_tables
from bidking_lab.runtime.snapshot import ui_contract_from_artifact


ROOT = Path(__file__).resolve().parents[1]
REAL_ETHAN = (
    ROOT
    / "data"
    / "samples"
    / "fatbeans"
    / "fatbeans_valid_ethan_2601_5rounds_2601_9000000000000005_0375.json"
)
_COUNT_EXACT_ABSENT = object()


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


def _field_fixed32(field_no: int, value: float) -> bytes:
    return _varint((field_no << 3) | 5) + struct.pack("<f", value)


def _row(
    action_id: int,
    value: float,
    *,
    session_id: str = "2601:123",
    sort: int = 20,
    result_field: int = 9,
    wire_type: str = "float32",
) -> dict[str, object]:
    raw = struct.pack("<f", value)
    parsed = struct.unpack("<f", raw)[0]
    return {
        "action_id": action_id,
        "result": parsed,
        "result_field": result_field,
        "result_wire_type": wire_type,
        "result_raw_hex": raw.hex(),
        "session_id": session_id,
        "sort": sort,
        "round": 2,
    }


def _product_evidence_from_float32_raw(
    raw_hex: str,
    *,
    action_id: int = 100169,
    count_exact: object = 2,
) -> tuple[dict[str, object], dict[str, object]]:
    result = _parse_action_result(
        _field_varint(4, action_id)
        + _varint((9 << 3) | 5)
        + bytes.fromhex(raw_hex)
    )
    assert result is not None
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        states=(
            FatbeansStateEvent(
                sort_id=20,
                capture_time="2026-07-28T00:00:00+08:00",
                message_id=0x0027,
                session_id="2601:123",
                map_id=2601,
                round_index=2,
                action_results=(result,),
            ),
        ),
        statuses=(),
    )
    rows = _action_result_rows(events, {}, session_id="2601:123")
    count_exact_segment = (
        ""
        if count_exact is _COUNT_EXACT_ABSENT
        else f"count_exact={count_exact}:"
    )
    contract = ui_contract_from_artifact(
        {
            "session_id": "2601:123",
            "action_result_rows": rows,
            "v2_posterior_rows": [
                {
                    "诊断": (
                        f"size_bucket:{SIZE_AVG_ACTION_FOOTPRINT[action_id]}:"
                        "avg=1:tier=test:strength=soft:"
                        f"{count_exact_segment}"
                        "count_source=semantic_full_outline"
                    )
                }
            ],
        }
    )
    size_bucket = contract["diagnostics"]["size_bucket"]
    return size_bucket["shape_average_value_evidence"][0], size_bucket


def test_parser_preserves_float32_wire_type_and_raw_bits() -> None:
    raw = struct.pack("<f", 231_570.7)
    result = _parse_action_result(
        _field_varint(4, 100173) + _field_fixed32(9, 231_570.7)
    )

    assert result is not None
    assert result.action_id == 100173
    assert result.result_field == 9
    assert result.result_wire_type == "float32"
    assert result.result_raw_hex == raw.hex()
    assert type(result.result) is float  # noqa: E721
    assert result.result == struct.unpack("<f", raw)[0]


@pytest.mark.parametrize(
    "raw_hex",
    [
        "00000000",  # positive zero
        "00000080",  # negative zero
        "01000000",  # minimum positive subnormal
        "ffff7f00",  # maximum positive subnormal
        "0000807f",  # positive infinity
        "000080ff",  # negative infinity
        "0000c07f",  # quiet NaN
        "ffff7f7f",  # maximum finite Float32
    ],
)
def test_real_product_chain_rejects_non_normal_float32_boundaries(
    raw_hex: str,
) -> None:
    evidence, size_bucket = _product_evidence_from_float32_raw(raw_hex)

    assert evidence["status"] == "unavailable"
    assert evidence["avg_interval"] is None
    assert evidence["total_value_interval"] is None
    assert evidence["consumed_by"] == []
    assert evidence["conflicts"]
    assert size_bucket["reading_active"] is False


def test_real_product_chain_rejects_empty_integer_total_interval() -> None:
    raw_hex = struct.pack("<f", 1.1).hex()
    evidence, size_bucket = _product_evidence_from_float32_raw(raw_hex)

    assert evidence["status"] == "unavailable"
    assert evidence["avg_interval"] is not None
    assert evidence["count_status"] == "conflict"
    assert evidence["total_value_interval"] is None
    assert evidence["consumed_by"] == []
    assert "empty_integer_total_interval" in evidence["conflicts"]
    assert size_bucket["reading_active"] is False


def test_real_product_chain_accepts_normal_float32_with_reachable_total() -> None:
    evidence, size_bucket = _product_evidence_from_float32_raw("0000803f")

    assert evidence["status"] == "interval_only"
    assert evidence["avg_interval"] is not None
    assert evidence["total_value_interval"] == [2, 2]
    assert evidence["consumed_by"] == ["v2_size_avg_legacy"]
    assert evidence["conflicts"] == []
    assert size_bucket["reading_active"] is True


@pytest.mark.parametrize(
    ("count_exact", "expected_conflict"),
    [
        (2, None),
        (10**308, None),
        (
            int(sys.float_info.max),
            "unrepresentable_exact_count_interval",
        ),
        (10**400, "unrepresentable_exact_count_interval"),
    ],
    ids=(
        "ordinary",
        "large_representable",
        "representable_count_overflowing_product",
        "unrepresentable_count",
    ),
)
def test_real_product_chain_exact_count_numeric_boundaries_fail_closed(
    count_exact: int,
    expected_conflict: str | None,
) -> None:
    evidence, size_bucket = _product_evidence_from_float32_raw(
        "0000803f",
        count_exact=count_exact,
    )

    if expected_conflict is None:
        assert evidence["status"] == "interval_only"
        assert evidence["count_status"] == "exact"
        assert evidence["total_value_interval"][0] <= (
            evidence["total_value_interval"][1]
        )
        assert evidence["consumed_by"] == ["v2_size_avg_legacy"]
        assert size_bucket["reading_active"] is True
    else:
        assert evidence["status"] == "unavailable"
        assert evidence["count_status"] == "conflict"
        assert evidence["count_value"] is None
        assert evidence["total_value_interval"] is None
        assert evidence["consumed_by"] == []
        assert evidence["conflicts"] == [expected_conflict]
        assert size_bucket["reading_active"] is False


@pytest.mark.parametrize(
    "count_exact",
    [True, 2.0, "bad", None],
    ids=("bool", "float", "string", "null"),
)
def test_real_product_chain_preserves_present_invalid_exact_count(
    count_exact: object,
) -> None:
    evidence, size_bucket = _product_evidence_from_float32_raw(
        "0000803f",
        count_exact=count_exact,
    )

    assert evidence["status"] == "unavailable"
    assert evidence["count_status"] == "conflict"
    assert evidence["count_value"] is None
    assert evidence["total_value_interval"] is None
    assert evidence["consumed_by"] == []
    assert evidence["conflicts"] == ["invalid_exact_count"]
    assert size_bucket["reading_active"] is False


def test_real_product_chain_keeps_absent_exact_count_compatible() -> None:
    evidence, size_bucket = _product_evidence_from_float32_raw(
        "0000803f",
        count_exact=_COUNT_EXACT_ABSENT,
    )

    assert evidence["status"] == "interval_only"
    assert evidence["count_status"] == "unknown"
    assert evidence["count_value"] is None
    assert evidence["total_value_interval"] is None
    assert evidence["consumed_by"] == ["v2_size_avg_legacy"]
    assert evidence["conflicts"] == []
    assert size_bucket["reading_active"] is True


@pytest.mark.parametrize(
    "count_exact",
    [True, 2.0, "2", 0, -1, None],
    ids=("bool", "float", "string", "zero", "negative", "null"),
)
def test_present_malformed_exact_count_is_conflict(
    count_exact: object,
) -> None:
    evidence = shape_average_value_evidence_from_action_rows(
        [_row(100169, 1.0)],
        current_session_id="2601:123",
        posterior_targets=[
            {
                "cells": 1,
                "count_exact": count_exact,
                "count_provenance": ("semantic_full_outline",),
            }
        ],
    )[0]

    assert evidence["status"] == "unavailable"
    assert evidence["count_status"] == "conflict"
    assert evidence["count_value"] is None
    assert evidence["total_value_interval"] is None
    assert evidence["consumed_by"] == []
    assert evidence["conflicts"] == ["invalid_exact_count"]


@pytest.mark.parametrize(
    ("action_id", "footprint_cells"),
    sorted(SIZE_AVG_ACTION_FOOTPRINT.items()),
)
def test_all_five_actions_publish_verified_float32_intervals(
    action_id: int,
    footprint_cells: int,
) -> None:
    row = _row(action_id, 1.0)
    evidence = shape_average_value_evidence_from_action_rows(
        [row],
        current_session_id="2601:123",
        posterior_targets=[
            {
                "cells": footprint_cells,
                "count_exact": 3,
                "count_provenance": (
                    "exact_total_item_count",
                    "trusted_distinct_footprints",
                ),
            }
        ],
    )[0]

    assert evidence["status"] == "interval_only"
    assert evidence["footprint_cells"] == footprint_cells
    assert evidence["wire_numeric_type"] == "float32"
    assert evidence["source_field"] == 9
    lower, upper = evidence["avg_interval"]
    assert lower <= evidence["raw_value"] <= upper
    assert evidence["count_value"] == 3
    assert evidence["count_status"] == "exact"
    assert evidence["count_provenance"] == [
        "exact_total_item_count",
        "trusted_distinct_footprints",
    ]
    assert evidence["total_value_interval"] == [
        math.ceil(lower * 3),
        math.floor(upper * 3),
    ]
    assert evidence["reachable_total_values"] == []
    assert evidence["quote_effect"] == "none_new_structured_evidence_only"
    assert evidence["consumed_by"] == ["v2_size_avg_legacy"]
    assert evidence["display_rounding"] == "unknown"


@pytest.mark.parametrize("action_id", [0, 5, 100168, 100174, True])
def test_illegal_or_unmapped_footprints_are_not_published(action_id: object) -> None:
    row = _row(100169, 10_000.0)
    row["action_id"] = action_id

    assert shape_average_value_evidence_from_action_rows(
        [row],
        current_session_id="2601:123",
    ) == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("result", None),
        ("result", True),
        ("result", 0),
        ("result", -1),
        ("result", float("nan")),
        ("result", float("inf")),
        ("result", "bad"),
        ("result_field", True),
        ("result_raw_hex", ""),
        ("result_raw_hex", "00000000"),
        ("result_raw_hex", "not-hex!"),
    ],
)
def test_malformed_numeric_or_wire_evidence_fails_closed(
    field: str,
    value: object,
) -> None:
    row = _row(100169, 10_000.0)
    row[field] = value

    evidence = shape_average_value_evidence_from_action_rows(
        [row],
        current_session_id="2601:123",
    )[0]

    assert evidence["status"] == "unavailable"
    assert evidence["avg_interval"] is None
    assert evidence["total_value_interval"] is None
    assert evidence["conflicts"]


def test_unknown_wire_precision_remains_raw_only() -> None:
    row = _row(
        100172,
        88_000.0,
        result_field=11,
        wire_type="varint_as_float",
    )
    row["result_raw_hex"] = None

    evidence = shape_average_value_evidence_from_action_rows(
        [row],
        current_session_id="2601:123",
    )[0]

    assert evidence["status"] == "raw_only"
    assert evidence["raw_value"] == 88_000.0
    assert evidence["avg_interval"] is None
    assert evidence["display_rounding"] == "unknown"


def test_missing_or_cross_session_evidence_is_visible_but_unavailable() -> None:
    missing = _row(100170, 20_000.0)
    missing["session_id"] = None
    cross = _row(100171, 30_000.0, session_id="2601:999")

    evidence = shape_average_value_evidence_from_action_rows(
        [missing, cross],
        current_session_id="2601:123",
    )

    assert evidence[0]["status"] == "unavailable"
    assert "invalid_source_session" in evidence[0]["conflicts"]
    assert evidence[1]["status"] == "unavailable"
    assert "stale_or_cross_session" in evidence[1]["conflicts"]
    assert evidence[0]["consumed_by"] == []
    assert evidence[1]["consumed_by"] == []


def test_missing_current_session_fails_closed() -> None:
    evidence = shape_average_value_evidence_from_action_rows(
        [_row(100173, 231_570.7)],
        current_session_id=None,
    )[0]

    assert evidence["status"] == "unavailable"
    assert evidence["avg_interval"] is not None
    assert evidence["total_value_interval"] is None
    assert evidence["consumed_by"] == []
    assert evidence["conflicts"] == ["invalid_current_session"]


def test_ui_contract_exposes_additive_structured_evidence() -> None:
    contract = ui_contract_from_artifact(
        {
            "session_id": "2601:123",
            "action_result_rows": [_row(100172, 120_000.5)],
            "v2_posterior_rows": [
                {
                    "诊断": (
                        "size_bucket:4:avg=120000:tier=rich_4cell:strength=soft:"
                        "count_exact=2:count_source=semantic_full_outline"
                    )
                }
            ],
        }
    )
    size_bucket = contract["diagnostics"]["size_bucket"]
    evidence = size_bucket["shape_average_value_evidence"][0]

    assert size_bucket["active"] is True
    assert size_bucket["reading_active"] is True
    assert evidence["status"] == "interval_only"
    assert evidence["count_value"] == 2
    assert evidence["count_status"] == "exact"
    assert evidence["count_provenance"] == ["semantic_full_outline"]


def test_real_action_rows_preserve_session_round_and_float32_bits() -> None:
    if not REAL_ETHAN.exists():
        pytest.skip(f"local replay absent: {REAL_ETHAN.name}")

    events = parse_fatbeans_capture(REAL_ETHAN)
    tables = load_monitor_tables()
    rows = _action_result_rows(events, tables.items, session_id="2601:9000000000000005")
    row = next(row for row in rows if row["action_id"] == 100172)

    assert row["session_id"] == "2601:9000000000000005"
    assert row["round"] is not None
    assert row["result_field"] == 9
    assert row["result_wire_type"] == "float32"
    assert isinstance(row["result_raw_hex"], str)
    assert len(row["result_raw_hex"]) == 8

    evidence = shape_average_value_evidence_from_action_rows(
        [row],
        current_session_id="2601:9000000000000005",
    )[0]
    assert evidence["status"] == "interval_only"
    assert evidence["avg_interval"][0] <= row["result"] <= evidence["avg_interval"][1]
