"""Build-paired regression contract for BidMap col[11] normalization."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

import bidking_lab.extract.bid_map_table as bid_map_module
from bidking_lab.extract.bid_map_table import parse_bid_map_row, parse_bid_map_table
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.simulation.bidding import simulate_session
from bidking_lab.simulation.hero_value import (
    BIDKING_DISABLE_BIDMAP_COL11_CONSUMER_DECOUPLING,
    simulate_hero_value,
)


def _v2_row(map_id: int, rounds_raw: str) -> list[str]:
    row = ["0"] * bid_map_module.BID_MAP_TABLE_COLUMN_COUNT_V2
    row[0] = str(map_id)
    row[1] = f"map_{map_id}"
    row[2] = "desc"
    row[7] = "101"
    row[9] = "[[]]"
    row[10] = "ui_value_high"
    row[11] = rounds_raw
    row[12] = "[1,1,2500]"
    row[14] = "[50,50,50,50,50]"
    row[15] = "[[1,1,50000]]"
    row[16] = "[[]]"
    row[17] = f"[9999,{map_id},16,32]"
    row[18] = "4"
    row[19] = "[3000,2500,2000,1500,0]"
    row[20] = "[103,0,103,0,0]"
    return row


def _table(rows: int, raw_values: tuple[str, ...]) -> list[list[str]]:
    return [
        _v2_row(2101 + index, raw_values[index % len(raw_values)])
        for index in range(rows)
    ]


def _canonical_sha256(rows: list[list[str]]) -> str:
    payload = "\n".join("\t".join(row) for row in rows).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _approve_synthetic_new_table(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[list[str]],
) -> None:
    monkeypatch.setattr(
        bid_map_module,
        "_SCALED_COL11_TABLE_PROFILES",
        {
            _canonical_sha256(rows):
                bid_map_module.BID_MAP_BUILD_24464806_ROUNDS_PROFILE,
        },
    )


def test_legacy_full_table_keeps_approved_rounds_domain() -> None:
    rows = _table(165, ("10", "15", "20", "25", "30"))

    maps = parse_bid_map_table(rows)

    assert len(maps) == 165
    assert {row.rounds_total for row in maps.values()} == {10, 15, 20, 25, 30}
    assert {
        row.col11_value_tier_scale_basis_points for row in maps.values()
    } == {1000, 1500, 2000, 2500, 3000}


def test_build24464806_full_table_normalizes_only_after_exact_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _table(196, ("1000", "1500", "2000", "2500", "3000"))
    _approve_synthetic_new_table(monkeypatch, rows)

    maps = parse_bid_map_table(rows)

    assert len(maps) == 196
    assert {row.rounds_total for row in maps.values()} == {10, 15, 20, 25, 30}
    assert {
        row.col11_value_tier_scale_basis_points for row in maps.values()
    } == {1000, 1500, 2000, 2500, 3000}
    assert {row.raw_row[11] for row in maps.values()} == {
        "1000",
        "1500",
        "2000",
        "2500",
        "3000",
    }


def test_build24464806_single_row_requires_explicit_profile() -> None:
    row = _v2_row(2503, "3000")

    with pytest.raises(ValueError, match="approved table identity"):
        parse_bid_map_row(row)

    parsed = parse_bid_map_row(
        row,
        rounds_profile=bid_map_module.BID_MAP_BUILD_24464806_ROUNDS_PROFILE,
    )
    assert parsed.rounds_total == 30
    assert parsed.col11_value_tier_scale_basis_points == 3000
    assert parsed.raw_row[11] == "3000"


@pytest.mark.parametrize(
    ("map_id", "expected_category"),
    [(2531, 105), (2550, 105), (4531, 305), (4550, 305)],
)
def test_build24464806_profile_scopes_new_blank_shipwreck_categories(
    map_id: int,
    expected_category: int,
) -> None:
    row = _v2_row(map_id, "3000")
    row[7] = ""

    with pytest.raises(ValueError, match="missing category"):
        parse_bid_map_row(row)

    parsed = parse_bid_map_row(
        row,
        rounds_profile=bid_map_module.BID_MAP_BUILD_24464806_ROUNDS_PROFILE,
    )
    assert parsed.category == expected_category


@pytest.mark.parametrize("mutation", ["partial", "mixed", "unknown_hash"])
def test_scaled_table_partial_mixed_or_unknown_identity_fails_closed(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _table(196, ("1000", "1500", "2000", "2500", "3000"))
    _approve_synthetic_new_table(monkeypatch, rows)

    if mutation == "partial":
        candidate = rows[:1]
    elif mutation == "mixed":
        candidate = [row.copy() for row in rows]
        candidate[17][11] = "30"
    else:
        candidate = [row.copy() for row in rows]
        candidate[17][1] = "mutated"

    with pytest.raises(ValueError, match="approved table identity"):
        parse_bid_map_table(candidate)


@pytest.mark.parametrize(
    "rounds_raw",
    ["1000.0", " 1000", "1000 ", "+1000", "-1000", str(2**200)],
)
def test_build24464806_single_row_malformed_or_overflow_rounds_fail_closed(
    rounds_raw: str,
) -> None:
    with pytest.raises(ValueError, match=r"col\[11\]"):
        parse_bid_map_row(
            _v2_row(2503, rounds_raw),
            rounds_profile=bid_map_module.BID_MAP_BUILD_24464806_ROUNDS_PROFILE,
        )


def test_build24464806_default_and_named_rollback_are_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _v2_row(2503, "3000")
    monkeypatch.delenv(
        bid_map_module.BIDKING_DISABLE_BUILD24464806_ROUNDS_NORMALIZATION,
        raising=False,
    )
    assert (
        parse_bid_map_row(
            row,
            rounds_profile=bid_map_module.BID_MAP_BUILD_24464806_ROUNDS_PROFILE,
        ).rounds_total
        == 30
    )

    monkeypatch.setenv(
        bid_map_module.BIDKING_DISABLE_BUILD24464806_ROUNDS_NORMALIZATION,
        "1",
    )
    assert (
        parse_bid_map_row(
            row,
            rounds_profile=bid_map_module.BID_MAP_BUILD_24464806_ROUNDS_PROFILE,
        ).rounds_total
        == 3000
    )
    assert (
        parse_bid_map_row(
            row,
            rounds_profile=bid_map_module.BID_MAP_BUILD_24464806_ROUNDS_PROFILE,
        ).col11_value_tier_scale_basis_points
        == 3000
    )

    monkeypatch.setenv(
        bid_map_module.BIDKING_DISABLE_BUILD24464806_ROUNDS_NORMALIZATION,
        "true",
    )
    assert (
        parse_bid_map_row(
            row,
            rounds_profile=bid_map_module.BID_MAP_BUILD_24464806_ROUNDS_PROFILE,
        ).rounds_total
        == 30
    )


def test_consumers_do_not_treat_col11_as_session_round_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _v2_row(900, "3000")
    row[17] = "[9999,900,40,40]"
    bid_map = parse_bid_map_row(
        row,
        rounds_profile=bid_map_module.BID_MAP_BUILD_24464806_ROUNDS_PROFILE,
    )
    item = Item(
        item_id=1,
        name="item",
        description="",
        name_key="item",
        desc_key="item",
        quality=3,
        quality_color="blue",
        value=1,
        shape_w=1,
        shape_h=1,
        tags=[],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=["0"] * 40,
    )
    pool = DropPool(
        pool_id=900,
        name="pool",
        description="",
        pool_type=2,
        entries=[
            DropEntry(
                category=101,
                item_id=1,
                n_min=1,
                n_max=1,
                weight=1,
            ),
        ],
    )

    result = simulate_hero_value(
        107,
        900,
        maps={900: bid_map},
        drops={900: pool},
        items={1: item},
        n_trials=4,
        rng=np.random.default_rng(7),
    )

    assert bid_map.rounds_total == 30
    assert bid_map.col11_value_tier_scale_basis_points == 3000
    assert result.baseline_mean == 40

    session = simulate_session(
        900,
        maps={900: bid_map},
        drops={900: pool},
        items={1: item},
        n_trials=4,
        rng=np.random.default_rng(7),
    )
    assert session.items_total_mean == 40

    monkeypatch.setenv(
        BIDKING_DISABLE_BIDMAP_COL11_CONSUMER_DECOUPLING,
        "1",
    )
    rollback = simulate_hero_value(
        107,
        900,
        maps={900: bid_map},
        drops={900: pool},
        items={1: item},
        n_trials=4,
        rng=np.random.default_rng(7),
    )
    assert rollback.baseline_mean == 30


def test_explicit_hero_bid_cap_remains_supported() -> None:
    row = _v2_row(901, "3000")
    row[17] = "[9999,901,40,40]"
    bid_map = parse_bid_map_row(
        row,
        rounds_profile=bid_map_module.BID_MAP_BUILD_24464806_ROUNDS_PROFILE,
    )
    item = Item(
        item_id=1,
        name="item",
        description="",
        name_key="item",
        desc_key="item",
        quality=3,
        quality_color="blue",
        value=1,
        shape_w=1,
        shape_h=1,
        tags=[],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=["0"] * 40,
    )
    pool = DropPool(
        pool_id=901,
        name="pool",
        description="",
        pool_type=2,
        entries=[
            DropEntry(
                category=101,
                item_id=1,
                n_min=1,
                n_max=1,
                weight=1,
            ),
        ],
    )

    result = simulate_hero_value(
        107,
        901,
        maps={901: bid_map},
        drops={901: pool},
        items={1: item},
        rounds_to_bid=5,
        n_trials=4,
        rng=np.random.default_rng(7),
    )

    assert result.baseline_mean == 5

    zero = simulate_hero_value(
        107,
        901,
        maps={901: bid_map},
        drops={901: pool},
        items={1: item},
        rounds_to_bid=0,
        n_trials=4,
        rng=np.random.default_rng(7),
    )
    assert zero.baseline_mean == 0
    assert zero.hero_mean == 0
