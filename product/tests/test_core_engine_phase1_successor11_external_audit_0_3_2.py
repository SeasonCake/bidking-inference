"""External audit for successor11 hostile-key schema inspection."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "hero_ref" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "hero_ref" / "src"))

import ahmad_ref_engine as engine  # noqa: E402


class _CollisionKey:
    def __init__(self, hash_target: str) -> None:
        self.hash_target = hash_target
        self.armed = False

    def __hash__(self) -> int:
        return hash(self.hash_target)

    def __eq__(self, other: object) -> bool:
        if self.armed:
            raise AssertionError("stable sentinel compared an untrusted key")
        return False


@pytest.mark.parametrize("colliding", (False, True), ids=("control", "collision"))
@pytest.mark.parametrize("nested", (False, True), ids=("row", "value"))
def test_stable_sentinel_schema_never_compares_foreign_keys(
    nested: bool,
    colliding: bool,
) -> None:
    expected_key = "status" if nested else "info_id"
    hash_target = expected_key if colliding else "unrelated-foreign-key"
    hostile = _CollisionKey(hash_target)
    if nested:
        row = {
            "info_id": "200014",
            "value": {hostile: "present_invalid"},
        }
    else:
        row = {
            hostile: "200014",
            "value": {"status": "present_invalid"},
        }
    hostile.armed = True

    assert engine._stable_total_avg_cells_invalid_row(row) is False
