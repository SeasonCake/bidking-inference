from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "hero_ref" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import hero_ref_live_schedule as schedule


DISABLE = "BIDKING_DISABLE_POSTUPDATE_NINE_HERO_SKILL_COMPAT"

EXPECTED = {
    "chenmei": (102, {1: 100102, 3: 1001021, 4: 1001022, 5: 1001023}),
    "tatiana": (105, {1: 100105, 3: 1001051, 4: 1001052, 5: 1001053}),
    "naomi": (106, {1: 100106, 3: 1001062, 4: 1001063, 5: 1001064}),
    "helena": (109, {1: 100109, 2: 1001091, 3: 1001092, 4: 1001093, 5: 1001094}),
    "george": (201, {1: 100201, 2: 1002011, 3: 1002012, 4: 1002013, 5: 1002014}),
    "carlos": (202, {1: 100202, 2: 1002021, 3: 1002022, 4: 1002023, 5: 1002024}),
    "leonard": (203, {1: 100203, 3: 1002032, 4: 1002033, 5: 1002034}),
    "ivan": (205, {1: 100205, 2: 1002051, 3: 1002052}),
    "takeda": (206, {1: 100206, 2: 1002062, 3: 10020631, 4: 1002064, 5: 1002065}),
}

LEGACY = {
    "chenmei": {1: 1001021},
    "tatiana": {1: 1001051},
    "naomi": {1: 1001061},
    "helena": {1: 1001091},
    "george": {1: 1002011},
    "carlos": {1: 1002021, 2: 1002022},
    "leonard": {1: 1002031},
    "ivan": {1: 1002051},
    "takeda": {1: 1002061, 2: 1002062},
}

SPARSE_ROUNDS = {
    "chenmei": 3,
    "tatiana": 3,
    "naomi": 3,
    "helena": 2,
    "george": 2,
    "carlos": 2,
    "leonard": 3,
    "ivan": 2,
    "takeda": 2,
}

INVALID_SPARSE_SNAPSHOTS = [
    {},
    {"minimap": {"items": []}},
    {"minimap": {"items": [{}]}},
    {"minimap": {"items": [{"cells": 4}]}},
    {
        "minimap": {
            "items": [
                {
                    "runtime_id": None,
                    "local_index": None,
                    "item_id": None,
                    "cells": None,
                    "shape_code": None,
                    "quality": None,
                }
            ]
        }
    },
    {"minimap": {"items": [{"estimated": True}]}},
    {"minimap": {"items": [{"placeholder": True, "label": "等待布局"}]}},
    {"minimap": {"quality_counts": {}}},
    {"minimap": {"quality_counts": {"q5": 0}}},
    {"minimap": {"quality_counts": {"q4": 0, "q5": 0, "q6": 0}}},
    {"minimap": {"estimated": True, "quality_counts": {"q5": 1}}},
    {
        "minimap": {
            "quality_counts": {"q5": 1},
            "quality_counts_estimated": True,
        }
    },
    {
        "ui_contract": {
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [],
                }
            }
        }
    },
    {
        "ui_contract": {
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [{}],
                }
            }
        }
    },
    {
        "ui_contract": {
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [{"semantic": "q5_avg_value"}],
                }
            }
        }
    },
    {
        "ui_contract": {
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [{"value": 12345}],
                }
            }
        }
    },
    {
        "ui_contract": {
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {"semantic": "display_label", "value": 12345}
                    ],
                }
            }
        }
    },
    {
        "ui_contract": {
            "constraints": {
                "public_info": {
                    "public_numeric_summary": "金均价 12345",
                }
            }
        }
    },
    {"public_info_rows": [{"summary": "等待公开信息", "label": "公开"}]},
]

VALID_SPARSE_SNAPSHOTS = [
    {"minimap": {"items": [{"runtime_id": 9, "cells": 4}]}},
    {"minimap": {"items": [{"local_index": 0, "shape_code": 23}]}},
    {"minimap": {"quality_counts": {"q5": 1}}},
    {
        "minimap": {
            "quality_counts": {"q5": 0},
            "quality_counts_exact": True,
        }
    },
    {
        "ui_contract": {
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {
                            "info_id": 200037,
                            "semantic": "q5_avg_value",
                            "kind": "avg_value",
                            "value": 0.0,
                        }
                    ],
                }
            }
        }
    },
    {"public_info_rows": [{"info_id": 200002, "value": 0, "value_field": 8}]},
    {"action_result_rows": [{"action_id": 100125, "result": 12345}]},
    {"structured_ref_inputs": {"fixed_counts": {"q5": 2}}},
]

INVALID_INTEGER_VALUES = (True, 1.5, "9", -1)


def _invalid_integer_item_snapshot(field: str, value: object) -> dict:
    item: dict[str, object] = {field: value}
    if field == "local_index":
        item["shape_code"] = 22
    elif field == "row":
        item.update({"col": 1, "cells": 4})
    elif field == "col":
        item.update({"row": 1, "cells": 4})
    else:
        item["cells"] = 4
    return {"minimap": {"items": [item]}}


STRICT_INTEGER_INVALID_SPARSE_CASES = [
    pytest.param(
        _invalid_integer_item_snapshot(field, value),
        id=f"{field}-{type(value).__name__}-{value!r}",
    )
    for field in ("runtime_id", "local_index", "item_id", "row", "col")
    for value in INVALID_INTEGER_VALUES
] + [
    pytest.param(
        {"minimap": {"items": [{"runtime_id": 9, "cells": value}]}},
        id=f"cells-{type(value).__name__}-{value!r}",
    )
    for value in INVALID_INTEGER_VALUES
] + [
    pytest.param(
        {"minimap": {"quality_counts": {"q5": value}}},
        id=f"quality-count-{type(value).__name__}-{value!r}",
    )
    for value in INVALID_INTEGER_VALUES
]

INVALID_PUBLIC_VALUES = (
    True,
    float("nan"),
    float("inf"),
    float("-inf"),
    -1.5,
    "37494.125",
)


def _public_numeric_snapshot(value: object) -> dict:
    return {
        "ui_contract": {
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {
                            "semantic": "q5_avg_value",
                            "kind": "avg_value",
                            "value": value,
                        }
                    ],
                }
            }
        }
    }


def _skill_row(hero_id: int, skill_id: int, *, zero: bool = False) -> dict:
    if zero:
        return {
            "hero_id": hero_id,
            "skill_id": skill_id,
            "result": 0,
            "result_field": 7,
            "revealed_items": 0,
            "observed_items": [],
        }
    return {
        "hero_id": hero_id,
        "skill_id": skill_id,
        "revealed_items": 1,
        "observed_items": [{"runtime_id": 1, "cells": 1}],
    }


def test_postupdate_action_vectors_are_staged_and_fail_open(monkeypatch) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    for hero, (hero_id, vector) in EXPECTED.items():
        spec = schedule.get_hero_schedule(hero)
        assert spec is not None
        assert spec.hero_id == hero_id
        assert spec.category is schedule.HeroInferenceCategory.STAGED_ROUNDS
        assert spec.skill_rounds == vector
        assert spec.scheduled_skill_sparse_fail_open is True


def test_postupdate_action_vectors_have_named_rollback(monkeypatch) -> None:
    monkeypatch.setenv(DISABLE, "1")
    for hero, vector in LEGACY.items():
        spec = schedule.get_hero_schedule(hero)
        assert spec is not None
        assert spec.skill_rounds == vector
        assert spec.category is not schedule.HeroInferenceCategory.STAGED_ROUNDS


@pytest.mark.parametrize(
    ("hero", "round_no"),
    [
        (hero, round_no)
        for hero, (_hero_id, vector) in EXPECTED.items()
        for round_no in vector
    ],
)
def test_each_real_scheduled_action_unblocks_its_product_round(
    monkeypatch,
    hero: str,
    round_no: int,
) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    hero_id, vector = EXPECTED[hero]
    skill_id = vector[round_no]
    snapshot = {
        "skill_reveal_rows": [
            _skill_row(
                hero_id,
                skill_id,
                zero=(hero == "naomi" and round_no == 1),
            )
        ]
    }

    assert schedule.hero_quote_ready(snapshot, hero, round_no) == (True, "")


def test_naomi_explicit_zero_is_signal_but_absent_zero_is_not(monkeypatch) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    explicit_zero = {"skill_reveal_rows": [_skill_row(106, 100106, zero=True)]}
    unwired_zero = {
        "skill_reveal_rows": [
            {
                "hero_id": 106,
                "skill_id": 100106,
                "result": 0,
                "result_field": None,
                "revealed_items": 0,
                "observed_items": [],
            }
        ]
    }

    assert schedule.hero_quote_ready(explicit_zero, "naomi", 1) == (True, "")
    assert schedule.hero_quote_ready(unwired_zero, "naomi", 1)[0] is False


def test_late_missing_frame_fails_open_only_with_real_sparse_evidence(
    monkeypatch,
) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    sparse = {"minimap": {"items": [{"runtime_id": 9, "cells": 4}]}}

    assert schedule.hero_quote_ready(sparse, "helena", 4) == (True, "")
    assert schedule.hero_quote_ready({}, "helena", 4)[0] is False

    monkeypatch.setenv(schedule.SCHEDULED_SKILL_SPARSE_FAIL_OPEN_DISABLE, "1")
    assert schedule.hero_quote_ready(sparse, "helena", 4)[0] is False


@pytest.mark.parametrize("hero", tuple(EXPECTED))
@pytest.mark.parametrize("snapshot", INVALID_SPARSE_SNAPSHOTS)
def test_all_postupdate_heroes_reject_unqualified_sparse_signal(
    monkeypatch,
    hero: str,
    snapshot: dict,
) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    monkeypatch.delenv(schedule.SCHEDULED_SKILL_SPARSE_FAIL_OPEN_DISABLE, raising=False)

    assert schedule.hero_quote_ready(snapshot, hero, SPARSE_ROUNDS[hero])[0] is False


@pytest.mark.parametrize("hero", tuple(EXPECTED))
@pytest.mark.parametrize("snapshot", STRICT_INTEGER_INVALID_SPARSE_CASES)
def test_all_postupdate_heroes_reject_non_integer_identity_count_and_cells(
    monkeypatch,
    hero: str,
    snapshot: dict,
) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    monkeypatch.delenv(schedule.SCHEDULED_SKILL_SPARSE_FAIL_OPEN_DISABLE, raising=False)

    assert schedule.hero_quote_ready(snapshot, hero, SPARSE_ROUNDS[hero])[0] is False


@pytest.mark.parametrize("hero", tuple(EXPECTED))
@pytest.mark.parametrize(
    "revealed_items",
    [
        pytest.param(value, id=f"{type(value).__name__}-{value!r}")
        for value in INVALID_INTEGER_VALUES
    ],
)
def test_all_postupdate_heroes_reject_non_integer_scheduled_reveal_counts(
    monkeypatch,
    hero: str,
    revealed_items: object,
) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    monkeypatch.delenv(schedule.SCHEDULED_SKILL_SPARSE_FAIL_OPEN_DISABLE, raising=False)
    hero_id, vector = EXPECTED[hero]
    round_no = SPARSE_ROUNDS[hero]
    snapshot = {
        "skill_reveal_rows": [
            {
                "hero_id": hero_id,
                "skill_id": vector[round_no],
                "revealed_items": revealed_items,
                "observed_items": [],
            }
        ]
    }

    assert schedule.hero_quote_ready(snapshot, hero, round_no)[0] is False


@pytest.mark.parametrize("hero", tuple(EXPECTED))
@pytest.mark.parametrize(
    "value",
    [
        pytest.param(value, id=f"{type(value).__name__}-{value!r}")
        for value in INVALID_PUBLIC_VALUES
    ],
)
def test_all_postupdate_heroes_reject_invalid_public_numeric_types(
    monkeypatch,
    hero: str,
    value: object,
) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    monkeypatch.delenv(schedule.SCHEDULED_SKILL_SPARSE_FAIL_OPEN_DISABLE, raising=False)

    assert schedule.hero_quote_ready(
        _public_numeric_snapshot(value),
        hero,
        SPARSE_ROUNDS[hero],
    )[0] is False


@pytest.mark.parametrize("hero", tuple(EXPECTED))
@pytest.mark.parametrize("snapshot", VALID_SPARSE_SNAPSHOTS)
def test_all_postupdate_heroes_accept_qualified_sparse_signal(
    monkeypatch,
    hero: str,
    snapshot: dict,
) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    monkeypatch.delenv(schedule.SCHEDULED_SKILL_SPARSE_FAIL_OPEN_DISABLE, raising=False)

    assert schedule.hero_quote_ready(snapshot, hero, SPARSE_ROUNDS[hero]) == (True, "")


@pytest.mark.parametrize("hero", tuple(EXPECTED))
@pytest.mark.parametrize(
    "snapshot",
    [
        {"minimap": {"items": [{"runtime_id": 9, "cells": 4}]}},
        {"minimap": {"items": [{"local_index": 0, "shape_key": "22"}]}},
        {"minimap": {"items": [{"item_id": 100, "cells": 4}]}},
        {"minimap": {"items": [{"row": 0, "col": 0, "cells": 4}]}},
        {"minimap": {"quality_counts": {"q5": 1}}},
        _public_numeric_snapshot(37494.125),
    ],
)
def test_all_postupdate_heroes_accept_strict_integer_and_public_float_signals(
    monkeypatch,
    hero: str,
    snapshot: dict,
) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    monkeypatch.delenv(schedule.SCHEDULED_SKILL_SPARSE_FAIL_OPEN_DISABLE, raising=False)

    assert schedule.hero_quote_ready(snapshot, hero, SPARSE_ROUNDS[hero]) == (True, "")


@pytest.mark.parametrize("hero", tuple(EXPECTED))
def test_postupdate_sparse_fail_open_has_exact_named_rollback(
    monkeypatch,
    hero: str,
) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    monkeypatch.setenv(schedule.SCHEDULED_SKILL_SPARSE_FAIL_OPEN_DISABLE, "1")
    snapshot = {"minimap": {"items": [{"runtime_id": 9, "cells": 4}]}}

    assert schedule.hero_quote_ready(snapshot, hero, SPARSE_ROUNDS[hero])[0] is False


def test_non_target_hero_schedule_keeps_existing_valid_signal_contract(monkeypatch) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    physical = {"minimap": {"items": [{"runtime_id": 9, "cells": 4}]}}
    manual = {"structured_ref_inputs": {"total_count": 24}}

    assert schedule.hero_quote_ready(
        {"skill_reveal_rows": [_skill_row(103, 1001033)]},
        "aisha",
        2,
    ) == (True, "")
    assert schedule.hero_quote_ready(physical, "ahmed", 2) == (True, "")
    assert schedule.hero_quote_ready(physical, "sophie", 3) == (True, "")
    assert schedule.hero_quote_ready(manual, "raven", 3) == (True, "")


def test_duplicate_and_out_of_order_cumulative_rows_do_not_hide_signal(
    monkeypatch,
) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    snapshot = {
        "skill_reveal_rows": [
            {
                "sort": 50,
                "hero_id": 202,
                "skill_id": 1002022,
                "revealed_items": 0,
                "observed_items": [],
            },
            {
                "sort": 20,
                **_skill_row(202, 1002022),
            },
            {
                "sort": 10,
                **_skill_row(202, 1002021),
            },
        ]
    }

    assert schedule.hero_quote_ready(snapshot, "carlos", 3) == (True, "")
