"""Aisha q5 exact-cells + physical-domain q5/q6 joint value lane.

Fixtures are catalog-derived bidding inputs only.  Settlement values and private
support bundles are deliberately absent from this source-owned regression file.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from fractions import Fraction
from http.server import ThreadingHTTPServer
import json
import sys
import threading
from pathlib import Path
from typing import Callable
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_ref_engine as engine  # noqa: E402
import ahmad_live_panel_server as panel  # noqa: E402
import ahmad_tk_overlay as overlay  # noqa: E402
from bidking_lab.inference import v2 as v2_module  # noqa: E402
from test_aisha_5606_q5_total_cells_conditional_value_0_3_2 import (  # noqa: E402
    _target_snapshot,
)


ROLLBACK = engine.AISHA_Q5_TOTAL_CELLS_VALUE_DISABLE
SESSION_ID = "2509:9000000000000050"


def _domain(
    *,
    map_id: int,
    runtime_id: int,
    local_index: int,
    width: int,
    height: int,
    observed_quality: int | None,
) -> dict:
    catalog, reachable_by_map, status, generation = (
        v2_module._validated_physical_category_catalog()
    )
    assert status == "ok"
    reachable = reachable_by_map[map_id]
    excluded_qualities = [] if observed_quality is not None else [1, 2, 3, 4]
    base_candidates = [
        (int(item_id), int(row[0]), int(row[1]))
        for item_id in sorted(reachable)
        for row in (catalog[int(item_id)],)
        if (int(row[2]), int(row[3])) == (width, height)
        and (observed_quality is None or int(row[0]) == observed_quality)
        and int(row[0]) not in excluded_qualities
    ]
    required_categories: list[int] = []
    excluded_categories: list[int] = []
    candidates: list[tuple[int, int, int]] = []
    choices = [([], [category]) for category in range(101, 111)] + [
        ([category], []) for category in range(101, 111)
    ]
    for required, excluded in choices:
        selected = []
        membership_complete = True
        for item_id, quality, value in base_candidates:
            row = catalog[item_id]
            required_states = [
                v2_module._category_membership(row, category)
                for category in required
            ]
            excluded_states = [
                v2_module._category_membership(row, category)
                for category in excluded
            ]
            if "no" in required_states or "yes" in excluded_states:
                continue
            membership_complete = membership_complete and all(
                state == "yes" for state in required_states
            ) and all(state == "no" for state in excluded_states)
            selected.append((item_id, quality, value))
        selected_qualities = {quality for _item_id, quality, _value in selected}
        expected_qualities = (
            {observed_quality} if observed_quality is not None else {5, 6}
        )
        if selected and membership_complete and selected_qualities == expected_qualities:
            required_categories = required
            excluded_categories = excluded
            candidates = selected
            break
    assert candidates
    return {
        "key": f"runtime:{runtime_id}",
        "runtime_id": runtime_id,
        "local_index": local_index,
        "quality": observed_quality,
        "observed_quality": observed_quality,
        "shape_key": f"{width}{height}",
        "cells": width * height,
        "required_categories": required_categories,
        "excluded_categories": excluded_categories,
        "excluded_qualities": excluded_qualities,
        "candidate_item_ids": [item_id for item_id, _quality, _value in candidates],
        "candidate_qualities": sorted(
            {quality for _item_id, quality, _value in candidates}
        ),
        "candidate_values": sorted(
            {value for _item_id, _quality, value in candidates}
        ),
        "exact_item_id": None,
        "exact_value": None,
        "quality_source": "observed" if observed_quality is not None else None,
        "catalog_status": "ok",
        "catalog_generation": generation,
        "hard_eligible": observed_quality is not None,
        "physical_support_eligible": True,
        "sources": [],
    }


def _snapshot() -> dict:
    snapshot = _target_snapshot()
    structured = snapshot["structured_ref_inputs"]
    structured.pop("total_count")
    snapshot["action_result_rows"] = [
        row for row in snapshot["action_result_rows"] if row["action_id"] != 100115
    ]
    snapshot["ui_contract"]["actions"]["results"] = [
        row
        for row in snapshot["ui_contract"]["actions"]["results"]
        if row["action_id"] != "100115"
    ]
    root_q5 = snapshot["action_result_rows"][0]
    root_q5.update(
        result=19,
        result_field=14,
        result_wire_type="varint",
        result_raw_hex=None,
    )
    ui_q5 = snapshot["ui_contract"]["actions"]["results"][0]
    ui_q5.update(
        result="19",
        result_field="14",
        result_wire_type="varint",
        result_raw_hex="",
    )
    for owner in (snapshot, snapshot["ui_contract"]["context"]):
        owner["map_id"] = 2509
        owner["session_id"] = SESSION_ID
    for rows in (
        snapshot["action_result_rows"],
        snapshot["skill_reveal_rows"],
        snapshot["skill_reveals"],
        snapshot["ui_contract"]["actions"]["results"],
    ):
        for row in rows:
            row["session_id"] = SESSION_ID

    physical_domains = []
    outline_rows = []
    for item in snapshot["minimap_grid_items"]:
        width = min(int(item["width"]), int(item["height"]))
        height = max(int(item["width"]), int(item["height"]))
        outline_rows.append(
            {
                "runtime_id": int(item["runtime_id"]),
                "local_index": int(item["local_index"]),
                "quality": int(item["quality"]),
                "width": width,
                "height": height,
                "cells": width * height,
            }
        )
        physical_domains.append(
            _domain(
                map_id=2509,
                runtime_id=int(item["runtime_id"]),
                local_index=int(item["local_index"]),
                width=width,
                height=height,
                observed_quality=int(item["quality"]),
            )
        )
    next_runtime = 90_000
    next_local = len(physical_domains)
    for width, height in ((2, 2), (2, 2), (1, 1)):
        outline_rows.append(
            {
                "runtime_id": next_runtime,
                "local_index": next_local,
                "quality": None,
                "width": width,
                "height": height,
                "cells": width * height,
            }
        )
        physical_domains.append(
            _domain(
                map_id=2509,
                runtime_id=next_runtime,
                local_index=next_local,
                width=width,
                height=height,
                observed_quality=None,
            )
        )
        next_runtime += 1
        next_local += 1
    snapshot["physical_candidate_domains"] = copy.deepcopy(physical_domains)
    structured["physical_candidate_domains"] = copy.deepcopy(physical_domains)
    structured["minimap_items"] = outline_rows
    return snapshot


def _run(snapshot: dict, *, max_combos: int = 8_000) -> dict:
    prepared = engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    return engine.run_reference_engine(prepared, max_combos=max_combos).as_dict()


def _default_and_rollback(
    snapshot: dict,
    monkeypatch: pytest.MonkeyPatch,
    *,
    max_combos: int = 8_000,
) -> tuple[dict, dict]:
    monkeypatch.delenv(ROLLBACK, raising=False)
    default = _run(snapshot, max_combos=max_combos)
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(snapshot, max_combos=max_combos)
    return default, rollback


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_physical_joint_posterior_activates_and_preserves_anticorrelation(
    monkeypatch: pytest.MonkeyPatch,
    max_combos: int,
) -> None:
    default, rollback = _default_and_rollback(
        _snapshot(),
        monkeypatch,
        max_combos=max_combos,
    )

    summary = default["q5_total_cells_value_summary"]
    assert summary["status"] == "conditional_complete"
    assert summary["physical_joint"] is True
    assert summary["physical_domain_count"] == 3
    assert summary["source_ids"][0] == 100107
    assert set(summary["source_ids"][1:]) == {1001031, 1001032, 1001033, 1001034}
    assert summary["p90_value"] >= summary["p50_value"] >= summary["floor_value"]
    assert summary["q6_p90_value"] >= summary["q6_p50_value"]
    assert summary["high_p90_value"] >= summary["high_p50_value"]
    assert summary["joint_p90_value"] >= summary["joint_p50_value"]
    assert default["red_value_range"][2] == summary["q6_p90_value"]
    notes = ";".join(default.get("notes", ()))
    assert "pure_prior_red_strip" not in notes
    assert "no_room_red_bid_strip" not in notes
    assert "red_cells_geometry_clamp" not in notes
    assert len(summary["arms"]) > 1
    assert default != rollback
    assert rollback["q5_total_cells_value_summary"] == {}

    rows = [
        (float(row["q5_count"]), float(row["q6_count"]), float(row["posterior_prob"]))
        for row in summary["arms"]
        if float(row["posterior_prob"]) > 0
    ]
    weight = sum(row[2] for row in rows)
    q5_mean = sum(q5 * probability for q5, _q6, probability in rows) / weight
    q6_mean = sum(q6 * probability for _q5, q6, probability in rows) / weight
    covariance = sum(
        (q5 - q5_mean) * (q6 - q6_mean) * probability
        for q5, q6, probability in rows
    ) / weight
    assert covariance < 0


def _duplicate_action(snapshot: dict) -> None:
    snapshot["action_result_rows"].append(copy.deepcopy(snapshot["action_result_rows"][0]))
    snapshot["ui_contract"]["actions"]["results"].append(
        copy.deepcopy(snapshot["ui_contract"]["actions"]["results"][0])
    )


class _IntSubclass(int):
    pass


class _FloatSubclass(float):
    pass


class _StrSubclass(str):
    pass


class _HostileStrSubclass(str):
    def __str__(self) -> str:
        raise AssertionError("hostile action-id __str__ must not be called")


class _HostilePropertyObject:
    @property
    def __class__(self) -> type:
        raise AssertionError("hostile action-id __class__ must not be read")

    def __str__(self) -> str:
        raise AssertionError("hostile action-id __str__ must not be called")

    def __int__(self) -> int:
        raise AssertionError("hostile action-id __int__ must not be called")

    def __float__(self) -> float:
        raise AssertionError("hostile action-id __float__ must not be called")

    def __eq__(self, other: object) -> bool:
        raise AssertionError("hostile action-id __eq__ must not be called")

    def __hash__(self) -> int:
        raise AssertionError("hostile action-id __hash__ must not be called")

    def __bool__(self) -> bool:
        raise AssertionError("hostile action-id __bool__ must not be called")


@pytest.mark.parametrize(
    ("owner", "action_id"),
    (
        ("root", "100107"),
        ("ui", 100107),
        ("root", 100107.0),
        ("ui", 100107.0),
        ("root", "+100107"),
        ("ui", "100107.0"),
        ("root", _IntSubclass(100107)),
        ("ui", _IntSubclass(100107)),
        ("root", _FloatSubclass(100107.0)),
        ("ui", _FloatSubclass(100107.0)),
        ("root", _StrSubclass("100107")),
        ("ui", _StrSubclass("100107")),
    ),
)
def test_numeric_target_with_wrong_wire_type_is_atomic_noop(
    owner: str,
    action_id: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    rows = (
        snapshot["action_result_rows"]
        if owner == "root"
        else snapshot["ui_contract"]["actions"]["results"]
    )
    rows.append({**copy.deepcopy(rows[0]), "action_id": action_id})

    default, rollback = _default_and_rollback(snapshot, monkeypatch)

    assert default == rollback
    assert default["q5_total_cells_value_summary"] == {}


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
@pytest.mark.parametrize("prepared", [False, True], ids=["direct", "prepared"])
@pytest.mark.parametrize(
    "action_id",
    (
        pytest.param(_HostileStrSubclass("100107"), id="hostile-str-subclass"),
        pytest.param(Decimal("100107"), id="decimal"),
        pytest.param(Fraction(100107, 1), id="fraction"),
    ),
)
def test_protocol_safe_target_ledger_rejects_external_numeric_aliases(
    action_id: object,
    prepared: bool,
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    snapshot["action_result_rows"].append(
        {**copy.deepcopy(snapshot["action_result_rows"][0]), "action_id": action_id}
    )
    payload = copy.deepcopy(snapshot)
    if prepared:
        payload = engine.prepare_reference_engine_snapshot(payload)

    monkeypatch.delenv(ROLLBACK, raising=False)
    default = engine.run_reference_engine(
        copy.deepcopy(payload), max_combos=max_combos
    ).as_dict()
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = engine.run_reference_engine(
        copy.deepcopy(payload), max_combos=max_combos
    ).as_dict()

    assert default == rollback
    assert default["q5_total_cells_value_summary"] == {}


@pytest.mark.parametrize(
    ("owner", "action_id"),
    (
        ("root", "100108"),
        ("ui", 100108),
        ("root", _HostileStrSubclass("100108")),
        ("ui", _HostileStrSubclass("100108")),
    ),
)
def test_wrong_wire_type_for_unrelated_action_does_not_poison_authority(
    owner: str,
    action_id: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    rows = (
        snapshot["action_result_rows"]
        if owner == "root"
        else snapshot["ui_contract"]["actions"]["results"]
    )
    rows.append({**copy.deepcopy(rows[0]), "action_id": action_id})

    default, rollback = _default_and_rollback(snapshot, monkeypatch)

    assert default["q5_total_cells_value_summary"]["physical_joint"] is True
    assert default != rollback


@pytest.mark.parametrize("owner", ["root", "ui"])
def test_protocol_unsafe_unrelated_action_does_not_read_instance_properties(
    owner: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    rows = (
        snapshot["action_result_rows"]
        if owner == "root"
        else snapshot["ui_contract"]["actions"]["results"]
    )
    rows.append(
        {
            **copy.deepcopy(rows[0]),
            "action_id": _HostilePropertyObject(),
        }
    )

    default, rollback = _default_and_rollback(snapshot, monkeypatch)

    assert default["q5_total_cells_value_summary"]["physical_joint"] is True
    assert default != rollback


def _cross_session_action(snapshot: dict) -> None:
    snapshot["action_result_rows"][0]["session_id"] = "2509:9000000000000051"


def _missing_root_action(snapshot: dict) -> None:
    snapshot["action_result_rows"].pop(0)


def _conflicting_action_mirror(snapshot: dict) -> None:
    snapshot["ui_contract"]["actions"]["results"][0]["result"] = "18"


def _cross_epoch_action(snapshot: dict) -> None:
    snapshot["action_result_rows"][0]["epoch"] = 1


def _future_round_action(snapshot: dict) -> None:
    snapshot["action_result_rows"][0]["action_round"] = 5


def _partial_skill_mirror(snapshot: dict) -> None:
    snapshot["skill_reveals"].pop()


def _cross_session_skill(snapshot: dict) -> None:
    snapshot["skill_reveal_rows"][0]["session_id"] = "2509:9000000000000051"


def _catalog_generation_mismatch(snapshot: dict) -> None:
    snapshot["physical_candidate_domains"][0]["catalog_generation"] = "stale"
    snapshot["structured_ref_inputs"]["physical_candidate_domains"][0][
        "catalog_generation"
    ] = "stale"


def _catalog_identity_mismatch(snapshot: dict) -> None:
    for owner in (
        snapshot["physical_candidate_domains"][0],
        snapshot["structured_ref_inputs"]["physical_candidate_domains"][0],
    ):
        owner["candidate_item_ids"] = owner["candidate_item_ids"][:-1]


def _outline_binding_mismatch(snapshot: dict) -> None:
    snapshot["structured_ref_inputs"]["minimap_items"][-1]["runtime_id"] += 1


def _settled(snapshot: dict) -> None:
    snapshot["phase"] = "settled"
    snapshot["ui_contract"]["context"]["phase"] = "settled"


def _non_aisha(snapshot: dict) -> None:
    snapshot["hero"] = "ahmed"
    snapshot["structured_ref_inputs"]["hero"] = "ahmed"
    snapshot["ui_contract"]["context"]["hero"] = "ahmed"


def _total_count_present(snapshot: dict) -> None:
    snapshot["structured_ref_inputs"]["total_count"] = 32


def _direct_q6_present(snapshot: dict) -> None:
    snapshot["structured_ref_inputs"]["fixed_counts"] = {"q6": 2}


@pytest.mark.parametrize(
    "mutate",
    [
        _duplicate_action,
        _cross_session_action,
        _missing_root_action,
        _conflicting_action_mirror,
        _cross_epoch_action,
        _future_round_action,
        _partial_skill_mirror,
        _cross_session_skill,
        _catalog_generation_mismatch,
        _catalog_identity_mismatch,
        _outline_binding_mismatch,
        _settled,
        _non_aisha,
        _total_count_present,
        _direct_q6_present,
    ],
    ids=[
        "duplicate-action",
        "cross-session",
        "missing-root-action",
        "conflicting-action-mirror",
        "cross-epoch-action",
        "future-round-action",
        "partial-skill-mirror",
        "cross-session-skill",
        "catalog-generation-mismatch",
        "catalog-identity-mismatch",
        "outline-binding-mismatch",
        "settled",
        "non-aisha",
        "total-count-present",
        "direct-q6-present",
    ],
)
def test_partial_conflicting_or_out_of_scope_inputs_are_atomic_noops(
    mutate: Callable[[dict], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    mutate(snapshot)
    default, rollback = _default_and_rollback(snapshot, monkeypatch)

    assert default == rollback
    assert default["q5_total_cells_value_summary"] == {}


def test_physical_domain_cap_is_an_atomic_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "AISHA_Q5_TOTAL_CELLS_PHYSICAL_MAX_DOMAINS", 2)

    default, rollback = _default_and_rollback(_snapshot(), monkeypatch)

    assert default == rollback
    assert default["q5_total_cells_value_summary"] == {}


def test_summary_api_panel_and_tk_share_the_same_joint_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    monkeypatch.delenv(ROLLBACK, raising=False)
    direct = _run(snapshot)
    summary = panel.summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=tmp_path / "summary.json",
    )
    monkeypatch.setattr(panel, "_read_json", lambda _path: copy.deepcopy(snapshot))
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        panel.make_handler(
            panel.ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
        ),
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with urlopen(  # noqa: S310 - loopback-only product regression
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            api = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    tk_adapter = overlay.AhmadTkOverlay.__new__(overlay.AhmadTkOverlay)
    tk_payload = tk_adapter._manual_result_summary(copy.deepcopy(snapshot))

    expected = direct["q5_total_cells_value_summary"]
    assert expected["physical_joint"] is True
    assert summary["ahmed_ref"]["q5_total_cells_value_summary"] == expected
    assert summary["q5_total_cells_value_summary"] == expected
    assert api["ahmed_ref"]["q5_total_cells_value_summary"] == expected
    assert api["q5_total_cells_value_summary"] == expected
    assert summary["reference"]["balanced"] == tk_payload["reference"]["balanced"]
    assert summary["reference"]["decision_range"] == tk_payload["reference"][
        "decision_range"
    ]
