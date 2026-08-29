"""Feasible quality/session summaries compiled from v3 constraints."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from typing import Any

from bidking_lab.inference.v3.constraints import ConstraintSet

_BUCKET_TARGET_RE = re.compile(r"^bucket\.q(?P<quality>\d+)\.(?P<field>count|cells|value)$")


@dataclass(frozen=True)
class BucketFeasibleSummary:
    quality: int
    count_exact: int | None = None
    cells_exact: int | None = None
    value_exact: int | None = None
    count_floor: int = 0
    cells_floor: int = 0
    value_floor: int = 0

    @property
    def residual_count_exact(self) -> int | None:
        if self.count_exact is None:
            return None
        return max(0, self.count_exact - self.count_floor)

    @property
    def residual_cells_exact(self) -> int | None:
        if self.cells_exact is None:
            return None
        return max(0, self.cells_exact - self.cells_floor)

    @property
    def residual_value_exact(self) -> int | None:
        if self.value_exact is None:
            return None
        return max(0, self.value_exact - self.value_floor)


@dataclass(frozen=True)
class FeasibleSummaryReport:
    session_total_count_exact: int | None
    session_total_cells_exact: int | None
    known_count_floor: int
    known_cells_floor: int
    known_value_floor: int
    buckets: tuple[BucketFeasibleSummary, ...]
    conflicts: tuple[str, ...] = ()

    @property
    def feasible(self) -> bool:
        return not self.conflicts

    def bucket(self, quality: int) -> BucketFeasibleSummary | None:
        for bucket in self.buckets:
            if bucket.quality == quality:
                return bucket
        return None

    def to_flat_dict(self, *, prefix: str = "v3_summary_") -> dict[str, Any]:
        q6 = self.bucket(6)
        return {
            f"{prefix}available": True,
            f"{prefix}feasible": self.feasible,
            f"{prefix}conflict_count": len(self.conflicts),
            f"{prefix}conflicts": ";".join(self.conflicts[:5]),
            f"{prefix}session_total_count_exact": self.session_total_count_exact,
            f"{prefix}session_total_cells_exact": self.session_total_cells_exact,
            f"{prefix}known_count_floor": self.known_count_floor,
            f"{prefix}known_cells_floor": self.known_cells_floor,
            f"{prefix}known_value_floor": self.known_value_floor,
            f"{prefix}q6_count_exact": q6.count_exact if q6 is not None else None,
            f"{prefix}q6_cells_exact": q6.cells_exact if q6 is not None else None,
            f"{prefix}q6_value_exact": q6.value_exact if q6 is not None else None,
            f"{prefix}q6_count_floor": q6.count_floor if q6 is not None else 0,
            f"{prefix}q6_cells_floor": q6.cells_floor if q6 is not None else 0,
            f"{prefix}q6_value_floor": q6.value_floor if q6 is not None else 0,
            f"{prefix}q6_residual_count_exact": (
                q6.residual_count_exact if q6 is not None else None
            ),
            f"{prefix}q6_residual_cells_exact": (
                q6.residual_cells_exact if q6 is not None else None
            ),
            f"{prefix}q6_residual_value_exact": (
                q6.residual_value_exact if q6 is not None else None
            ),
        }


@dataclass
class _AnchorFloor:
    quality: int | None = None
    cells: int | None = None
    value: int | None = None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _anchor_floors(constraints: ConstraintSet) -> dict[str, _AnchorFloor]:
    floors: dict[str, _AnchorFloor] = {}

    def floor_for(key: str) -> _AnchorFloor:
        current = floors.get(key)
        if current is None:
            current = _AnchorFloor()
            floors[key] = current
        return current

    for anchor in constraints.item_anchors.values():
        floor = floor_for(anchor.key)
        if anchor.quality is not None:
            floor.quality = int(anchor.quality)
        if anchor.cells is not None:
            floor.cells = int(anchor.cells)
        if anchor.value is not None:
            floor.value = int(anchor.value)
    for anchor in constraints.shape_anchors.values():
        floor = floor_for(anchor.key)
        if floor.quality is None and anchor.quality is not None:
            floor.quality = int(anchor.quality)
        if floor.cells is None:
            floor.cells = int(anchor.cells)
    for anchor in constraints.quality_floor_anchors.values():
        floor = floor_for(anchor.key)
        if floor.quality is None:
            floor.quality = int(anchor.quality)
    return floors


def _numeric_exact_values(
    constraints: ConstraintSet,
) -> tuple[dict[str, int], dict[int, dict[str, int]]]:
    session: dict[str, int] = {}
    buckets: dict[int, dict[str, int]] = defaultdict(dict)
    for target, constraint in constraints.numeric.items():
        value = _int_or_none(constraint.value)
        if value is None:
            continue
        if target == "session.total_count":
            session["count"] = value
            continue
        if target == "session.total_cells":
            session["cells"] = value
            continue
        match = _BUCKET_TARGET_RE.match(target)
        if match is None:
            continue
        quality = int(match.group("quality"))
        buckets[quality][match.group("field")] = value
    return session, buckets


def compile_feasible_summary(constraints: ConstraintSet) -> FeasibleSummaryReport:
    """Compile hard constraints into quality-level exacts and floors."""

    session_exact, bucket_exact = _numeric_exact_values(constraints)
    anchor_floors = _anchor_floors(constraints)
    bucket_floors: dict[int, dict[str, int]] = defaultdict(
        lambda: {"count": 0, "cells": 0, "value": 0}
    )
    for floor in anchor_floors.values():
        if floor.quality is None:
            continue
        bucket = bucket_floors[int(floor.quality)]
        bucket["count"] += 1
        bucket["cells"] += int(floor.cells or 0)
        bucket["value"] += int(floor.value or 0)

    qualities = sorted(set(bucket_exact) | set(bucket_floors))
    buckets: list[BucketFeasibleSummary] = []
    conflicts: list[str] = []
    for quality in qualities:
        exact = bucket_exact.get(quality, {})
        floors = bucket_floors.get(quality, {})
        summary = BucketFeasibleSummary(
            quality=quality,
            count_exact=exact.get("count"),
            cells_exact=exact.get("cells"),
            value_exact=exact.get("value"),
            count_floor=int(floors.get("count", 0)),
            cells_floor=int(floors.get("cells", 0)),
            value_floor=int(floors.get("value", 0)),
        )
        buckets.append(summary)
        if summary.count_exact is not None and summary.count_floor > summary.count_exact:
            conflicts.append(f"q{quality}.count_floor_gt_exact")
        if summary.cells_exact is not None and summary.cells_floor > summary.cells_exact:
            conflicts.append(f"q{quality}.cells_floor_gt_exact")
        if summary.value_exact is not None and summary.value_floor > summary.value_exact:
            conflicts.append(f"q{quality}.value_floor_gt_exact")

    known_count = sum(bucket.count_floor for bucket in buckets)
    known_cells = sum(bucket.cells_floor for bucket in buckets)
    known_value = sum(
        max(bucket.value_floor, bucket.value_exact or 0)
        for bucket in buckets
    )
    session_total_count_exact = session_exact.get("count")
    session_total_cells_exact = session_exact.get("cells")
    if (
        session_total_count_exact is not None
        and known_count > session_total_count_exact
    ):
        conflicts.append("session.count_floor_gt_exact")
    if (
        session_total_cells_exact is not None
        and known_cells > session_total_cells_exact
    ):
        conflicts.append("session.cells_floor_gt_exact")
    bucket_count_exact_sum = sum(
        bucket.count_exact for bucket in buckets if bucket.count_exact is not None
    )
    bucket_cells_exact_sum = sum(
        bucket.cells_exact for bucket in buckets if bucket.cells_exact is not None
    )
    if (
        session_total_count_exact is not None
        and bucket_count_exact_sum > session_total_count_exact
    ):
        conflicts.append("session.bucket_count_exact_sum_gt_total")
    if (
        session_total_cells_exact is not None
        and bucket_cells_exact_sum > session_total_cells_exact
    ):
        conflicts.append("session.bucket_cells_exact_sum_gt_total")
    return FeasibleSummaryReport(
        session_total_count_exact=session_total_count_exact,
        session_total_cells_exact=session_total_cells_exact,
        known_count_floor=known_count,
        known_cells_floor=known_cells,
        known_value_floor=known_value,
        buckets=tuple(buckets),
        conflicts=tuple(dict.fromkeys(conflicts)),
    )


def empty_feasible_summary_flat_dict(*, prefix: str = "v3_summary_") -> dict[str, Any]:
    return {
        f"{prefix}available": False,
        f"{prefix}feasible": False,
        f"{prefix}conflict_count": None,
        f"{prefix}conflicts": None,
        f"{prefix}session_total_count_exact": None,
        f"{prefix}session_total_cells_exact": None,
        f"{prefix}known_count_floor": None,
        f"{prefix}known_cells_floor": None,
        f"{prefix}known_value_floor": None,
        f"{prefix}q6_count_exact": None,
        f"{prefix}q6_cells_exact": None,
        f"{prefix}q6_value_exact": None,
        f"{prefix}q6_count_floor": None,
        f"{prefix}q6_cells_floor": None,
        f"{prefix}q6_value_floor": None,
        f"{prefix}q6_residual_count_exact": None,
        f"{prefix}q6_residual_cells_exact": None,
        f"{prefix}q6_residual_value_exact": None,
    }


__all__ = (
    "BucketFeasibleSummary",
    "FeasibleSummaryReport",
    "compile_feasible_summary",
    "empty_feasible_summary_flat_dict",
)
