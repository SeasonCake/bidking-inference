from bidking_lab.inference.v3 import compile_feasible_summary
from bidking_lab.inference.v3.constraints import (
    ConstraintSet,
    HardNumericConstraint,
    ItemAnchor,
    QualityFloorAnchor,
    ShapeAnchor,
)


def _numeric(target: str, value: int) -> HardNumericConstraint:
    return HardNumericConstraint(
        target=target,
        value=value,
        event_id=f"event:{target}",
        source_kind="public_info",
        source_id="test",
        sort_id=1,
    )


def test_v3_feasible_summary_dedupes_anchor_floors_by_key() -> None:
    constraints = ConstraintSet(
        numeric={
            "session.total_cells": _numeric("session.total_cells", 30),
            "bucket.q6.count": _numeric("bucket.q6.count", 2),
            "bucket.q6.cells": _numeric("bucket.q6.cells", 17),
        },
        item_anchors={
            "runtime:1": ItemAnchor(
                key="runtime:1",
                event_id="event:item",
                source_kind="public_info",
                source_id="200021",
                sort_id=2,
                runtime_id=1,
                item_id=1086001,
                quality=6,
                value=200_000,
                shape_key="44",
                cells=16,
            ),
        },
        shape_anchors={
            "runtime:1": ShapeAnchor(
                key="runtime:1",
                event_id="event:shape",
                source_kind="skill_reveal",
                source_id="1002081",
                sort_id=2,
                shape_key="44",
                cells=16,
                runtime_id=1,
                quality=6,
            ),
        },
        quality_floor_anchors={
            "runtime:1": QualityFloorAnchor(
                key="runtime:1",
                event_id="event:quality",
                source_kind="action_result",
                source_id="100136",
                sort_id=3,
                runtime_id=1,
                quality=6,
            ),
            "runtime:2": QualityFloorAnchor(
                key="runtime:2",
                event_id="event:quality2",
                source_kind="action_result",
                source_id="100136",
                sort_id=3,
                runtime_id=2,
                quality=6,
            ),
        },
    )

    report = compile_feasible_summary(constraints)
    q6 = report.bucket(6)

    assert report.feasible is True
    assert report.known_count_floor == 2
    assert report.known_cells_floor == 16
    assert report.known_value_floor == 200_000
    assert q6 is not None
    assert q6.count_floor == 2
    assert q6.cells_floor == 16
    assert q6.value_floor == 200_000
    assert q6.residual_count_exact == 0
    assert q6.residual_cells_exact == 1


def test_v3_feasible_summary_reports_floor_exact_conflicts() -> None:
    constraints = ConstraintSet(
        numeric={
            "bucket.q6.count": _numeric("bucket.q6.count", 1),
        },
        quality_floor_anchors={
            "runtime:1": QualityFloorAnchor(
                key="runtime:1",
                event_id="event:quality1",
                source_kind="action_result",
                source_id="100136",
                sort_id=3,
                runtime_id=1,
                quality=6,
            ),
            "runtime:2": QualityFloorAnchor(
                key="runtime:2",
                event_id="event:quality2",
                source_kind="action_result",
                source_id="100136",
                sort_id=3,
                runtime_id=2,
                quality=6,
            ),
        },
    )

    report = compile_feasible_summary(constraints)

    assert report.feasible is False
    assert report.conflicts == ("q6.count_floor_gt_exact",)


def test_v3_feasible_summary_counts_bucket_value_exact_as_known_floor() -> None:
    constraints = ConstraintSet(
        numeric={
            "bucket.q5.value": _numeric("bucket.q5.value", 152_397),
        },
    )

    report = compile_feasible_summary(constraints)
    q5 = report.bucket(5)

    assert report.feasible is True
    assert report.known_value_floor == 152_397
    assert q5 is not None
    assert q5.value_exact == 152_397
    assert q5.value_floor == 0
