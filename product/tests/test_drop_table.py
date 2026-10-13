"""Unit tests for Drop.txt schema parser."""

from __future__ import annotations

import pytest

from bidking_lab.extract.drop_table import (
    DropPool,
    parse_drop_row,
    parse_drop_table,
)


def test_parse_minimal_pool() -> None:
    row = ["801", "", "个人模拟测试", "2", "[[8,8001,1,1,10],[8,8002,1,1,5]]"]
    pool = parse_drop_row(row)
    assert pool.pool_id == 801
    assert pool.name == ""
    assert pool.description == "个人模拟测试"
    assert pool.pool_type == 2
    assert len(pool.entries) == 2
    e0 = pool.entries[0]
    assert (e0.category, e0.item_id, e0.n_min, e0.n_max, e0.weight) == (8, 8001, 1, 1, 10)
    assert pool.total_weight == 15


def test_parse_empty_entries() -> None:
    pool = parse_drop_row(["999", "x", "y", "1", "[]"])
    assert pool.entries == []
    assert pool.total_weight == 0


def test_parse_doubly_nested_empty_entries() -> None:
    pool = parse_drop_row(["999", "x", "y", "1", "[[]]"])
    assert pool.entries == []


def test_parse_table_dedups_by_pool_id() -> None:
    rows = [
        ["10", "", "", "1", "[[1,2,1,1,3]]"],
        ["10", "", "", "1", "[[1,2,1,1,4]]"],
    ]
    with pytest.raises(ValueError, match="duplicate pool_id"):
        parse_drop_table(rows)


def test_parse_table_round_trip() -> None:
    rows = [
        ["1", "name1", "desc1", "2", "[[8,8001,1,1,10]]"],
        ["2", "name2", "desc2", "1", "[[14,1410101,1,1,10000]]"],
    ]
    out = parse_drop_table(rows)
    assert set(out.keys()) == {1, 2}
    assert isinstance(out[1], DropPool)
    assert out[2].entries[0].item_id == 1410101


def test_rejects_wrong_column_count() -> None:
    with pytest.raises(ValueError, match="must have 5 columns"):
        parse_drop_row(["1", "x", "y", "2"])


def test_rejects_malformed_entry_shape() -> None:
    with pytest.raises(ValueError, match="expected 5"):
        parse_drop_row(["1", "x", "y", "2", "[[1,2,3]]"])
