"""CP313/Cython compatibility for SAV2 mandatory physical units."""

from __future__ import annotations

from types import SimpleNamespace

import bidking_lab.live.monitor as monitor_module


def test_shape_average_units_do_not_construct_a_defaultdict(monkeypatch) -> None:
    def reject_defaultdict(*_args, **_kwargs):
        raise AssertionError("native SAV2 accumulator must be a builtin dict")

    monkeypatch.setattr(monitor_module, "defaultdict", reject_defaultdict)
    problem = SimpleNamespace(
        physical_candidate_domains=(
            SimpleNamespace(
                hard_eligible=True,
                physical_support_eligible=True,
                catalog_status="ok",
                catalog_generation="generation-a",
                cells=3,
                key="runtime:900",
                candidate_item_ids=(91, 92),
            ),
            SimpleNamespace(
                hard_eligible=False,
                physical_support_eligible=True,
                catalog_status="ok",
                catalog_generation="generation-a",
                cells=3,
                key="runtime:901",
                candidate_item_ids=(93, 94),
            ),
        ),
        anchors=(
            SimpleNamespace(
                cells=3,
                key="runtime:900",
                item_id=92,
                quality=6,
                value=143_100,
            ),
        ),
    )

    result = monitor_module._shape_average_mandatory_units_by_cells(problem)

    assert isinstance(result, dict)
    assert result == {
        3: (
            {
                "identity": "runtime:900",
                "candidate_item_ids": [92],
                "expected_quality": 6,
                "expected_cells": 3,
                "expected_value": 143_100,
            },
            {
                "identity": "runtime:901",
                "candidate_item_ids": [93, 94],
                "catalog_generation": "generation-a",
            },
        )
    }
