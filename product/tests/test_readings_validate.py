"""Warehouse vs bucket cells budget checks."""

from __future__ import annotations

from bidking_lab.inference.readings_validate import (
    check_warehouse_cell_budget,
    sum_filled_bucket_cells,
)


def _ethan_state(**kw):
    base = {
        "hero": "ethan",
        "warehouse_cells": 120,
        "wg_cells": 40,
        "blue_cells": 30,
        "purple_cells": 50,
        "gold_cells": 6,
    }
    base.update(kw)
    return base


def test_sum_filled_bucket_cells_ethan():
    assert sum_filled_bucket_cells(_ethan_state()) == 126


def test_overflow_detected():
    msg = check_warehouse_cell_budget(_ethan_state())
    assert msg is not None
    assert "126" in msg
    assert "120" in msg


def test_within_budget_ok():
    assert check_warehouse_cell_budget(_ethan_state(purple_cells=40)) is None


def test_estimated_warehouse_budget_uses_tolerance():
    state = _ethan_state(
        warehouse_cells=120,
        warehouse_cells_mode="estimate",
        warehouse_cells_tolerance=10,
    )
    assert check_warehouse_cell_budget(state) is None

    msg = check_warehouse_cell_budget(
        _ethan_state(
            warehouse_cells=120,
            warehouse_cells_mode="estimate",
            warehouse_cells_tolerance=3,
        )
    )
    assert msg is not None
    assert "120+3" in msg


def test_aisha_split_sums():
    st = {
        "hero": "aisha",
        "aisha_split": True,
        "warehouse_cells": 100,
        "white_cells": 10,
        "green_cells": 10,
        "blue_cells": 20,
        "purple_cells": 30,
        "gold_cells": 5,
        "red_cells_total": 0,
    }
    assert sum_filled_bucket_cells(st) == 75
    assert check_warehouse_cell_budget(st) is None
