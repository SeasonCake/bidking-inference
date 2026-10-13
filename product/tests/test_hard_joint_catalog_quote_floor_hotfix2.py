from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

import ahmad_ref_engine as engine  # noqa: E402


def _exact_q6(*, count: int = 1, cells: int = 20) -> tuple[dict, dict]:
    return (
        {"q6": ((count, cells, 1.0),)},
        {"q6": {"strength": "exact", "reason": "derived_single_solution"}},
    )


def test_exact_one_red_twenty_cells_uses_catalog_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    candidates, locks = _exact_q6()
    monkeypatch.setattr(
        engine,
        "_catalog_min_total_value_for_quality_count_cells",
        lambda quality, count, cells: 519_800
        if (quality, count, cells) == (6, 1, 20)
        else None,
    )

    assert engine._hard_joint_catalog_quote_floor(candidates, locks) == 519_800


@pytest.mark.parametrize(
    ("candidates", "locks"),
    (
        ({"q6": [{"count": 1, "cells": 20, "prob": 0.8}]}, {"q6": {"strength": "exact"}}),
        ({"q6": [{"count": 0, "cells": 20, "prob": 1.0}]}, {"q6": {"strength": "exact"}}),
        ({"q6": [{"count": 1, "cells": 0, "prob": 1.0}]}, {"q6": {"strength": "exact"}}),
        ({"q6": [{"count": 1, "cells": 20, "prob": 1.0}]}, {"q6": {"strength": "probabilistic"}}),
        (
            {
                "q6": [
                    {"count": 1, "cells": 20, "prob": 0.5},
                    {"count": 2, "cells": 20, "prob": 0.5},
                ]
            },
            {"q6": {"strength": "exact"}},
        ),
    ),
)
def test_non_exact_or_impossible_joint_cannot_floor_quote(
    candidates: dict,
    locks: dict,
) -> None:
    assert engine._hard_joint_catalog_quote_floor(candidates, locks) == 0


def test_hard_joint_floor_has_explicit_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    candidates, locks = _exact_q6()
    monkeypatch.setenv(engine.HARD_JOINT_CATALOG_QUOTE_FLOOR_DISABLE, "1")

    assert engine._hard_joint_catalog_quote_floor(candidates, locks) == 0
