from __future__ import annotations

import copy
import json
import sys
import threading
import urllib.request
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (
    ROOT / "hero_ref" / "src",
    ROOT / "hero_ref" / "tools",
    ROOT / "tests",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ahmad_live_panel_server import _quality_composition_payload  # noqa: E402
from ahmad_ref_engine import RefCombo, _quality_count_arm_authority  # noqa: E402
from detail_composition_view import (  # noqa: E402
    detail_composition_view,
    format_compact_quality_count,
    format_compact_quality_footprint,
    format_quality_dimension_state_badge,
    quality_dimension_triplet,
)
from test_ahmed_4528_probabilistic_red_display_hotfix2 import (  # noqa: E402
    _summary,
)
from test_live_overlay import _ahmad_overlay_module  # noqa: E402
from test_overlay_card_remap_real_tk import _new_root  # noqa: E402
from test_ref_probability_wire_serialization_hotfix2 import (  # noqa: E402
    _result as _wire_result,
)


QUALITY_ORDER = ("q1", "q3", "q4", "q5", "q6")


def _ahmed_4537_quality_result(*, include_q1: bool = True) -> dict[str, object]:
    """Privacy-safe quality/count reduction of the 4537 R3 bidding state."""
    candidates: dict[str, list[dict[str, float | int]]] = {
        "q3": [
            {"count": 18, "cells": 39, "prob": 0.3333},
            {"count": 18, "cells": 40, "prob": 0.3333},
            {"count": 18, "cells": 41, "prob": 0.3333},
        ],
        "q4": [{"count": 20, "cells": 53, "prob": 1.0}],
        "q5": [{"count": 11, "cells": 46, "prob": 1.0}],
        "q6": [
            {"count": 5, "cells": 15, "prob": 0.9998},
            {"count": 15, "cells": 47, "prob": 0.0002},
        ],
    }
    locks: dict[str, dict[str, str]] = {
        "q3": {"strength": "exact", "reason": "exact_lock"},
        "q4": {"strength": "exact", "reason": "derived_single_solution"},
        "q5": {"strength": "exact", "reason": "exact_lock"},
        "q6": {"strength": "probabilistic", "reason": "combo_multi_solution"},
    }
    count_ranges: dict[str, list[int]] = {
        "q3": [18, 18, 18],
        "q4": [20, 20, 20],
        "q5": [11, 11, 11],
        "q6": [5, 5, 15],
    }
    cells_ranges: dict[str, list[int]] = {
        "q3": [39, 40, 41],
        "q4": [53, 53, 53],
        "q5": [46, 46, 46],
        "q6": [15, 15, 47],
    }
    if include_q1:
        # The visible row is the main arm.  Its missing 0.02% counterpart is not
        # licensed to become a count lock or an invented second q1 count.
        candidates["q1"] = [{"count": 20, "cells": 42, "prob": 0.9998}]
        locks["q1"] = {
            "strength": "exact",
            "reason": "derived_single_solution",
        }
        count_ranges["q1"] = [20, 20, 20]
        cells_ranges["q1"] = [42, 42, 42]
    return {
        "status": "ok",
        "quality_count_ranges": count_ranges,
        "quality_cells_ranges": cells_ranges,
        "quality_count_cells_candidates": candidates,
        "quality_lock": locks,
        "quality_count_arms": [
            {"q1": 20, "q3": 18, "q4": 20, "q5": 11, "q6": 5},
            {"q1": 10, "q3": 18, "q4": 20, "q5": 11, "q6": 15},
        ] if include_q1 else [],
        "evidence": {
            "total_count": 74,
            "total_grid_target": None,
            "fixed_counts": {"q3": 18, "q5": 11},
            "quality_cells": {"q5": 46},
        },
    }


def test_4537_probabilistic_q1_does_not_reauthorize_raw_exact_count_lock() -> None:
    payload = _quality_composition_payload(_ahmed_4537_quality_result())
    view = detail_composition_view({"quality_composition": payload})
    q1 = view.quality("q1")

    assert q1.status == "ready"
    assert q1.lock_strength == "probabilistic"
    assert q1.lock_reason == "exact_lock_has_other_mass"
    assert q1.other_probability == pytest.approx(0.0002)
    assert q1.count_lock_value is None
    assert q1.dimension_state == "未锁"
    assert format_quality_dimension_state_badge(q1) == "未锁"

    # q3 has an independent exact skill count and every visible arm agrees, so
    # the narrow exception remains a legitimate count-only lock.
    q3 = view.quality("q3")
    assert q3.count_lock_value == 18
    assert q3.dimension_state == "件锁"


def test_4537_compact_count_uses_only_total_constrained_visible_arms() -> None:
    payload = _quality_composition_payload(_ahmed_4537_quality_result())
    view = detail_composition_view({"quality_composition": payload})
    q1 = view.quality("q1")
    q6 = view.quality("q6")

    # The visible main arm is 20+18+20+11+5=74.  The independent q6=15
    # marginal has no visible q1 counterpart, so it stays in details only.
    assert [(row.count, row.cells) for row in q6.candidates] == [(5, 15), (15, 47)]
    assert [row.value for row in q6.compact_count_candidates] == [5]
    assert q6.count_correlation_state == "partial"
    assert "总件 74" in q6.count_correlation_note
    assert "相关" in q6.count_correlation_note
    assert format_compact_quality_count(q6, fallback="5 / 5 / 15") == (
        "保 5 / 均 5 / 激 5件"
    )

    # q1 itself has unshown probability mass and no independent exact count.
    # Keep the physical main candidate useful, but never make it look locked.
    assert q1.count_correlation_state == "unknown"
    assert quality_dimension_triplet(q1, "count") is None
    assert format_compact_quality_footprint(q1, compact_range=True) == (
        "约20件·约42格"
    )


def test_missing_quality_prevents_total_constrained_compact_count_guess() -> None:
    payload = _quality_composition_payload(_ahmed_4537_quality_result(include_q1=False))
    q6 = detail_composition_view({"quality_composition": payload}).quality("q6")

    assert q6.count_correlation_state == "unknown"
    assert q6.compact_count_candidates == ()
    assert format_compact_quality_count(q6, fallback="5 / 5 / 15") == "联动待定"


def test_compact_tiers_each_use_one_complete_total_constrained_arm() -> None:
    """Mutually exclusive arms must not be spliced into 64/74/84 totals."""
    result = {
        "status": "ok",
        "quality_count_ranges": {
            "q1": [10, 10, 20],
            "q3": [10, 20, 20],
            "q4": [15, 15, 15],
            "q5": [14, 14, 14],
            "q6": [15, 15, 15],
        },
        "quality_cells_ranges": {
            "q1": [20, 20, 40],
            "q3": [30, 60, 60],
            "q4": [45, 45, 45],
            "q5": [56, 56, 56],
            "q6": [75, 75, 75],
        },
        "quality_count_cells_candidates": {
            "q1": [
                {"count": 10, "cells": 20, "prob": 0.6},
                {"count": 20, "cells": 40, "prob": 0.4},
            ],
            "q3": [
                {"count": 20, "cells": 60, "prob": 0.6},
                {"count": 10, "cells": 30, "prob": 0.4},
            ],
            "q4": [{"count": 15, "cells": 45, "prob": 1.0}],
            "q5": [{"count": 14, "cells": 56, "prob": 1.0}],
            "q6": [{"count": 15, "cells": 75, "prob": 1.0}],
        },
        "quality_lock": {
            "q1": {"strength": "probabilistic", "reason": "combo_multi_solution"},
            "q3": {"strength": "probabilistic", "reason": "combo_multi_solution"},
            "q4": {"strength": "exact", "reason": "exact_lock"},
            "q5": {"strength": "exact", "reason": "exact_lock"},
            "q6": {"strength": "exact", "reason": "exact_lock"},
        },
        "quality_count_arms": [
            {"q1": 10, "q3": 20, "q4": 15, "q5": 14, "q6": 15},
            {"q1": 20, "q3": 10, "q4": 15, "q5": 14, "q6": 15},
        ],
        "evidence": {
            "total_count": 74,
            "total_grid_target": None,
            "fixed_counts": {"q4": 15, "q5": 14, "q6": 15},
            "quality_cells": {"q4": 45, "q5": 56, "q6": 75},
        },
    }
    expected_arms = {
        (10, 20, 15, 14, 15),
        (20, 10, 15, 14, 15),
    }

    payload = _quality_composition_payload(result)
    view = detail_composition_view({"quality_composition": payload})
    triplets = {
        key: quality_dimension_triplet(view.quality(key), "count")
        for key in QUALITY_ORDER
    }
    assert all(triplet is not None for triplet in triplets.values())
    tier_arms = [
        tuple(triplets[key][tier] for key in QUALITY_ORDER)  # type: ignore[index]
        for tier in range(3)
    ]

    assert payload["compact_count_arms"] == [
        {"q1": 10, "q3": 20, "q4": 15, "q5": 14, "q6": 15},
        {"q1": 10, "q3": 20, "q4": 15, "q5": 14, "q6": 15},
        {"q1": 20, "q3": 10, "q4": 15, "q5": 14, "q6": 15},
    ]
    assert all(arm in expected_arms for arm in tier_arms)
    assert [sum(arm) for arm in tier_arms] == [74, 74, 74]


def _declared_count_arm_result() -> dict[str, object]:
    declared_arms = [
        {"q1": 10, "q3": 10, "q4": 12, "q5": 16, "q6": 26},
        {"q1": 10, "q3": 11, "q4": 11, "q5": 17, "q6": 25},
    ]
    return {
        "status": "ok",
        "quality_count_ranges": {
            "q1": [10, 10, 10],
            "q3": [10, 10, 11],
            "q4": [11, 12, 12],
            "q5": [16, 16, 17],
            "q6": [25, 26, 26],
        },
        "quality_cells_ranges": {
            "q1": [30, 30, 30],
            "q3": [30, 30, 33],
            "q4": [33, 36, 36],
            "q5": [48, 48, 51],
            "q6": [75, 78, 78],
        },
        "quality_count_cells_candidates": {
            key: [
                {"count": count, "cells": count * 3, "prob": prob}
                for count, prob in rows
            ]
            for key, rows in {
                "q1": [(10, 1.0)],
                "q3": [(10, 0.6), (11, 0.4)],
                "q4": [(12, 0.6), (11, 0.4)],
                "q5": [(16, 0.6), (17, 0.4)],
                "q6": [(26, 0.6), (25, 0.4)],
            }.items()
        },
        "quality_lock": {
            "q1": {"strength": "exact", "reason": "exact_lock"},
            **{
                key: {"strength": "probabilistic", "reason": "combo_multi_solution"}
                for key in ("q3", "q4", "q5", "q6")
            },
        },
        "quality_count_arms": declared_arms,
        "evidence": {"total_count": 74, "fixed_counts": {"q1": 10}},
    }


def test_compact_tiers_are_selected_from_declared_full_count_arms() -> None:
    """Marginal cartesian products must not invent cross-arm splices."""
    result = _declared_count_arm_result()
    declared_arms = result["quality_count_arms"]
    assert isinstance(declared_arms, list)

    payload = _quality_composition_payload(result)

    assert payload["authoritative_count_arms"] == declared_arms
    assert all(arm in declared_arms for arm in payload["compact_count_arms"])
    assert detail_composition_view({"quality_composition": payload}).status == "ready"


def _count_combo(arm: tuple[int, int, int, int, int]) -> RefCombo:
    return RefCombo(
        counts=dict(zip(QUALITY_ORDER, arm)),
        grids={},
        value=0.0,
        weight=0.0,
        total_grid=0.0,
    )


def test_engine_count_arm_authority_deduplicates_and_sorts_complete_arms() -> None:
    arm_a = (10, 10, 12, 16, 26)
    arm_b = (10, 11, 11, 17, 25)

    assert _quality_count_arm_authority(
        [_count_combo(arm_b), _count_combo(arm_a), _count_combo(arm_b)],
        total_count=74,
        support_complete=True,
    ) == (arm_a, arm_b)
    assert _quality_count_arm_authority(
        [_count_combo(arm_a)],
        total_count=74,
        support_complete=True,
    ) == (arm_a,)
    assert _quality_count_arm_authority(
        [], total_count=74, support_complete=True
    ) == ()
    assert _quality_count_arm_authority(
        [_count_combo(arm_a)], total_count=74, support_complete=False
    ) == ()
    assert _quality_count_arm_authority(
        [_count_combo((10, 10, 12, 16, 25))],
        total_count=74,
        support_complete=True,
    ) == ()


def test_engine_count_arm_authority_fails_closed_over_state_cap() -> None:
    combos = [
        _count_combo((index, 0, 0, 0, 4_096 - index))
        for index in range(4_097)
    ]

    assert _quality_count_arm_authority(
        combos,
        total_count=4_096,
        support_complete=True,
    ) == ()


def test_ref_result_wire_carries_complete_count_arm_authority() -> None:
    arm_a = (10, 10, 12, 16, 26)
    arm_b = (10, 11, 11, 17, 25)
    base = _wire_result((1.0,))
    result = replace(
        base,
        evidence=replace(base.evidence, total_count=74),
        quality_count_arms=(arm_a, arm_b),
    )

    assert result.as_dict()["quality_count_arms"] == [
        dict(zip(QUALITY_ORDER, arm_a)),
        dict(zip(QUALITY_ORDER, arm_b)),
    ]


def test_single_authoritative_arm_repeats_without_inventing_a_third_arm() -> None:
    result = _declared_count_arm_result()
    arms = result["quality_count_arms"]
    assert isinstance(arms, list)
    result["quality_count_arms"] = [arms[0]]

    payload = _quality_composition_payload(result)

    assert payload["authoritative_count_arms"] == [arms[0]]
    assert payload["compact_count_arms"] == [arms[0], arms[0], arms[0]]
    assert detail_composition_view({"quality_composition": payload}).status == "ready"


class _ArmListProtocolTrap(list):
    def __bool__(self) -> bool:
        raise AssertionError("list truth protocol executed")

    def __iter__(self):
        raise AssertionError("list iteration protocol executed")


class _ArmDictProtocolTrap(dict):
    def __iter__(self):
        raise AssertionError("dict iteration protocol executed")


@pytest.mark.parametrize(
    "case",
    ("no_arms", "duplicate", "wrong_total", "list_protocol", "row_protocol"),
)
def test_invalid_or_missing_authoritative_arms_stay_correlation_unknown(
    case: str,
) -> None:
    result = _declared_count_arm_result()
    arms = result["quality_count_arms"]
    assert isinstance(arms, list)
    if case == "no_arms":
        result["quality_count_arms"] = []
    elif case == "duplicate":
        result["quality_count_arms"] = [arms[0], dict(arms[0])]
    elif case == "wrong_total":
        bad_arm = dict(arms[0])
        bad_arm["q6"] -= 1
        result["quality_count_arms"] = [bad_arm]
    elif case == "list_protocol":
        result["quality_count_arms"] = _ArmListProtocolTrap(arms)
    else:
        result["quality_count_arms"] = [_ArmDictProtocolTrap(arms[0])]

    payload = _quality_composition_payload(result)
    view = detail_composition_view({"quality_composition": payload})

    assert payload["authoritative_count_arms"] == []
    assert payload["compact_count_arms"] == []
    assert view.status == "ready"
    assert all(
        quality.count_correlation_state == "unknown"
        and quality.compact_count_triplet == ()
        for quality in view.qualities
    )


def test_consumer_rejects_sum_valid_cross_arm_splice() -> None:
    payload = _quality_composition_payload(_declared_count_arm_result())
    spliced = {"q1": 10, "q3": 10, "q4": 11, "q5": 17, "q6": 26}
    assert sum(spliced.values()) == 74
    assert spliced not in payload["authoritative_count_arms"]
    compact = payload["compact_count_arms"]
    qualities = payload["qualities"]
    assert isinstance(compact, list)
    assert isinstance(qualities, dict)
    compact[0] = spliced
    for key in QUALITY_ORDER:
        quality = qualities[key]
        assert isinstance(quality, dict)
        triplet = quality["compact_count_triplet"]
        assert isinstance(triplet, list)
        triplet[0] = spliced[key]

    view = detail_composition_view({"quality_composition": payload})

    assert view.status == "conflict"
    assert all(quality.status == "conflict" for quality in view.qualities)


@pytest.mark.parametrize(
    "case",
    (
        "missing_arms",
        "missing_authority",
        "bool_total",
        "wrong_sum",
        "duplicate_authority",
        "authority_protocol",
        "authority_total_mismatch",
    ),
)
def test_compact_arm_root_contract_fails_closed(case: str) -> None:
    payload = _quality_composition_payload(_ahmed_4537_quality_result())
    if case == "missing_arms":
        payload.pop("compact_count_arms")
    elif case == "missing_authority":
        payload.pop("authoritative_count_arms")
    elif case == "bool_total":
        payload["count_correlation_total"] = True
    elif case == "wrong_sum":
        arms = payload["compact_count_arms"]
        assert isinstance(arms, list)
        first = arms[0]
        assert isinstance(first, dict)
        first["q6"] = 6
    elif case == "duplicate_authority":
        authority = payload["authoritative_count_arms"]
        assert isinstance(authority, list)
        authority.append(dict(authority[0]))
    elif case == "authority_protocol":
        authority = payload["authoritative_count_arms"]
        assert isinstance(authority, list)
        payload["authoritative_count_arms"] = _ArmListProtocolTrap(authority)
    else:
        authority = payload["authoritative_count_arms"]
        assert isinstance(authority, list)
        authority[0]["q6"] += 1

    view = detail_composition_view({"quality_composition": payload})

    assert view.status == "conflict"
    assert all(quality.status == "conflict" for quality in view.qualities)


def _delete_joint_count_contract(payload: dict[str, object]) -> None:
    for field in (
        "count_correlation_total",
        "authoritative_count_arms",
        "compact_count_arms",
    ):
        payload.pop(field, None)
    qualities = payload["qualities"]
    assert isinstance(qualities, dict)
    for quality in qualities.values():
        assert isinstance(quality, dict)
        for field in (
            "compact_count_candidates",
            "compact_count_triplet",
            "count_correlation_state",
            "count_correlation_note",
        ):
            quality.pop(field, None)


@pytest.mark.parametrize(
    "case",
    (
        "delete_all_joint",
        "delete_root_only",
        "delete_per_quality_only",
        "delete_schema_and_joint",
    ),
)
def test_current_composition_cannot_downgrade_to_independent_marginals(
    case: str,
) -> None:
    payload = _quality_composition_payload(_ahmed_4537_quality_result())
    assert payload["schema_version"] == 1
    if case == "delete_all_joint":
        _delete_joint_count_contract(payload)
    elif case == "delete_root_only":
        for field in (
            "count_correlation_total",
            "authoritative_count_arms",
            "compact_count_arms",
        ):
            payload.pop(field)
    elif case == "delete_per_quality_only":
        qualities = payload["qualities"]
        assert isinstance(qualities, dict)
        for quality in qualities.values():
            assert isinstance(quality, dict)
            for field in (
                "compact_count_candidates",
                "compact_count_triplet",
                "count_correlation_state",
                "count_correlation_note",
            ):
                quality.pop(field)
    else:
        payload.pop("schema_version")
        _delete_joint_count_contract(payload)

    view = detail_composition_view({"quality_composition": payload})

    assert view.status == "conflict"
    assert all(quality.status == "conflict" for quality in view.qualities)


class _SchemaIntSubclass(int):
    def __eq__(self, other: object) -> bool:
        raise AssertionError("schema equality protocol executed")


class _SchemaProtocolTrap:
    def __bool__(self) -> bool:
        raise AssertionError("schema bool protocol executed")

    def __int__(self) -> int:
        raise AssertionError("schema int protocol executed")

    def __index__(self) -> int:
        raise AssertionError("schema index protocol executed")

    def __str__(self) -> str:
        raise AssertionError("schema str protocol executed")

    def __eq__(self, other: object) -> bool:
        raise AssertionError("schema equality protocol executed")


@pytest.mark.parametrize(
    ("schema_value", "case"),
    (
        (None, "missing"),
        ("1", "wrong_type"),
        (2, "unknown"),
        (True, "bool"),
        (_SchemaIntSubclass(1), "subclass"),
        (_SchemaProtocolTrap(), "protocol"),
    ),
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_current_composition_schema_generation_is_exact_and_protocol_safe(
    schema_value: object,
    case: str,
) -> None:
    del case
    payload = _quality_composition_payload(_ahmed_4537_quality_result())
    if schema_value is None:
        payload.pop("schema_version")
    else:
        payload["schema_version"] = schema_value

    view = detail_composition_view({"quality_composition": payload})

    assert view.status == "conflict"
    assert all(quality.status == "conflict" for quality in view.qualities)


def test_current_composition_without_total_count_is_explicitly_unknown() -> None:
    result = copy.deepcopy(_ahmed_4537_quality_result())
    evidence = result["evidence"]
    assert isinstance(evidence, dict)
    evidence.pop("total_count")

    payload = _quality_composition_payload(result)
    view = detail_composition_view({"quality_composition": payload})

    assert payload["schema_version"] == 1
    assert view.status == "ready"
    for quality in view.qualities:
        assert quality.count_correlation_state == "independent"
        assert quality.compact_count_triplet == ()


def test_unmarked_flat_ready_payload_is_not_a_legacy_downgrade_path() -> None:
    payload = _quality_composition_payload(_ahmed_4537_quality_result())
    _delete_joint_count_contract(payload)
    qualities = payload["qualities"]
    assert isinstance(qualities, dict)
    flat = {
        "status": payload["status"],
        "reason": payload["reason"],
        **copy.deepcopy(qualities),
    }

    view = detail_composition_view({"quality_composition": flat})

    assert view.status == "conflict"


def _4537_ui_summary() -> dict[str, object]:
    payload = _quality_composition_payload(_ahmed_4537_quality_result())
    view = detail_composition_view({"quality_composition": payload})
    q6 = view.quality("q6")
    summary = _summary()
    summary["quality_composition"] = payload
    red = summary["red"]
    assert isinstance(red, dict)
    red["count_range"] = "5 / 5 / 15"
    red["count_display"] = format_compact_quality_count(
        q6,
        fallback=red["count_range"],
    )
    red["count_probability_hover"] = (
        "红件候选：5件 99.98% · 15件 0.02%；" f"{q6.count_correlation_note}"
    )
    return summary


def test_loopback_api_preserves_joint_compact_value_and_correlation_note(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import ahmad_live_panel_server as panel

    summary = _4537_ui_summary()
    snapshot_path = tmp_path / "api" / "latest_snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        panel,
        "summarize_snapshot",
        lambda _snapshot, *, snapshot_path: summary,
    )
    config = panel.ServerConfig(project_root=tmp_path, snapshot=snapshot_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), panel.make_handler(config))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            wire = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert wire["red"]["count_display"] == "保 5 / 均 5 / 激 5件"
    assert "相关" in wire["red"]["count_probability_hover"]
    assert wire["quality_composition"]["qualities"]["q1"]["count_lock_value"] is None


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_real_tk_renders_total_constrained_triplet_and_unknown_q1(
    tmp_path: Path,
    dpi_scale: float,
) -> None:
    module = _ahmad_overlay_module()
    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"tk-{dpi_scale}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.geometry(f"510x{max(477, root.winfo_reqheight())}+0+0")
        overlay.render(_4537_ui_summary())
        root.update_idletasks()
        root.update()

        assert overlay.quality_state_badges["红件"].cget("text") == "待"
        assert {
            key: label.cget("text")
            for key, label in overlay.red_count_triplet_labels.items()
        } == {
            "low": "保5",
            "sep_left": "/",
            "middle": "均5",
            "sep_right": "/",
            "high": "激5",
        }
        assert overlay.quality_state_badges["白绿"].cget("text") == "未锁"
        assert overlay.red_rows["白绿"].cget("text") == "约20件·约42格"
        red_badge = overlay.quality_state_badges["红件"]
        triplet = overlay.red_count_triplet_frame
        assert triplet.winfo_x() + triplet.winfo_width() <= red_badge.winfo_x()
        q1_value = overlay.red_rows["白绿"]
        q1_badge = overlay.quality_state_badges["白绿"]
        assert q1_value.winfo_x() + q1_value.winfo_width() <= q1_badge.winfo_x()
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except Exception:
            pass
