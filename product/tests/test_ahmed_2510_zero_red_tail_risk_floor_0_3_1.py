from __future__ import annotations

import copy
import json
import sys
import threading
from collections.abc import Mapping
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
AHMAD_TOOLS = ROOT / "hero_ref" / "tools"
for _path in (AHMAD_SRC, AHMAD_TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import ahmad_live_panel_server as panel  # noqa: E402
from ahmad_live_panel_server import (  # noqa: E402
    ServerConfig,
    make_handler,
    summarize_snapshot,
)
from ahmad_ref_engine import (  # noqa: E402
    AHMED_NONOFFICIAL_ZERO_RED_QUOTE_CAP_DISABLE,
    AHMED_NONOFFICIAL_ZERO_RED_RISK_FLOOR_SPLIT_DISABLE,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)


UNKNOWN_SOURCE = {
    "qualification": "postupdate_unknown_warehouse_source",
    "official_distribution_eligible": False,
    "quote_policy": "advisory_only_no_clamp",
}
PLAYER_SOURCE = {
    "qualification": "player_custom_warehouse",
    "official_distribution_eligible": False,
    "quote_policy": "advisory_only_no_clamp",
}
OFFICIAL_SOURCE = {
    "qualification": "official_distribution",
    "official_distribution_eligible": True,
    "quote_policy": "normal",
}
MAP_TABLE_SHA256 = "a" * 64
OTHER_MAP_TABLE_SHA256 = "b" * 64
UNKNOWN_SOURCE_WITH_PROVENANCE = {
    **UNKNOWN_SOURCE,
    "quarantine_required": False,
    "map_table_sha256": MAP_TABLE_SHA256,
    "expected_map_table_sha256": MAP_TABLE_SHA256,
}


class _ExplodingSourceDict(dict):
    magic_calls = 0

    @classmethod
    def _explode(cls, *_args: object, **_kwargs: object) -> None:
        cls.magic_calls += 1
        raise RuntimeError("warehouse source mapping magic must not run")

    get = _explode
    items = _explode
    __iter__ = _explode
    __len__ = _explode
    __contains__ = _explode
    __bool__ = _explode
    __str__ = _explode


class _ExplodingSourceMapping(Mapping):
    magic_calls = 0

    @classmethod
    def _explode(cls, *_args: object, **_kwargs: object) -> None:
        cls.magic_calls += 1
        raise RuntimeError("warehouse source Mapping magic must not run")

    __getitem__ = _explode
    __iter__ = _explode
    __len__ = _explode
    get = _explode
    items = _explode
    __contains__ = _explode
    __bool__ = _explode
    __str__ = _explode


class _ExplodingQualification(str):
    magic_calls = 0

    def __str__(self) -> str:
        type(self).magic_calls += 1
        raise RuntimeError("warehouse source scalar magic must not run")

    def strip(self, *_args: object, **_kwargs: object) -> str:
        type(self).magic_calls += 1
        raise RuntimeError("warehouse source scalar magic must not run")


class _CollisionKey:
    def __init__(self, target: str) -> None:
        self._hash = hash(target)
        self.armed = False
        self.magic_calls = 0

    def __hash__(self) -> int:
        return self._hash

    def __eq__(self, _other: object) -> bool:
        if self.armed:
            self.magic_calls += 1
            raise RuntimeError("warehouse source key comparison must not run")
        return False


def _snapshot_2510(
    *,
    source: dict | None = UNKNOWN_SOURCE,
    phase: str = "bidding",
    q5_avg_cells: float = 3.1666667461395264,
) -> dict:
    """Sanitized bidding projection of cloud row 078ebb7b684c760a.

    Settlement labels are deliberately absent.  The product input contains only
    the pre-settlement field-update facts that were available to the engine.
    """

    context = {
        "hero": "ahmed",
        "map_id": 2510,
        "phase": phase,
        "round": 4,
    }
    snapshot = {
        "hero": "ahmed",
        "map_id": 2510,
        "phase": phase,
        "round": 4,
        "ui_contract": {
            "context": context,
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": {
            "source": "monitor_pre_settlement_field_updates",
            "total_count": 35,
            "counts": {"q1": 5, "q3": 10, "q4": 8},
            "quality_cells": {"q1": 15},
            "avg_cells": {
                "q1": 3.0,
                "q3": 3.299999952316284,
                "q4": 3.5,
                "q5": q5_avg_cells,
            },
        },
    }
    if source is not None:
        snapshot["warehouse_source"] = copy.deepcopy(source)
        context["warehouse_source"] = copy.deepcopy(source)
    return snapshot


def _snapshot_2503_direct_q5() -> dict:
    """Sanitized bidding projection of the independent 2503 exact-q5 control."""

    snapshot = _snapshot_2510()
    snapshot["map_id"] = 2503
    snapshot["ui_contract"]["context"]["map_id"] = 2503
    snapshot["structured_ref_inputs"] = {
        "source": "monitor_pre_settlement_field_updates",
        "total_count": 51,
        "counts": {"q3": 17, "q4": 16},
        "quality_cells": {"q1": 13, "q5": 26},
        "avg_cells": {
            "q1": 1.4444444179534912,
            "q3": 2.8235294818878174,
            "q4": 3.75,
            "q5": 6.5,
        },
    }
    return snapshot


def _run(snapshot: dict, *, max_combos: int = 8_000) -> dict:
    return run_reference_engine(snapshot, max_combos=max_combos).as_dict()


def _quotes(result: dict) -> tuple[int | None, int | None, int | None]:
    return (
        result.get("conservative"),
        result.get("balanced"),
        result.get("aggressive"),
    )


def _without_zero_red_policy_outputs(result: dict) -> dict:
    stable = copy.deepcopy(result)
    for key in ("conservative", "balanced", "aggressive", "risk_floor", "notes"):
        stable.pop(key, None)
    return stable


def _declare_source(
    owner: dict,
    source: dict,
    *,
    include_nested: bool = True,
) -> None:
    if include_nested:
        owner["warehouse_source"] = copy.deepcopy(source)
    owner["warehouse_source_qualification"] = source["qualification"]
    owner["official_distribution_eligible"] = source[
        "official_distribution_eligible"
    ]


def _declare_all_source_owners(snapshot: dict, source: dict) -> None:
    ui_contract = snapshot["ui_contract"]
    context = ui_contract["context"]
    source_owner = ui_contract.setdefault("source", {})
    for owner in (context, source_owner, snapshot):
        _declare_source(owner, source)


def _declare_legacy_source_triplet(
    owner: dict,
    source: dict,
    *,
    reverse: bool = False,
) -> None:
    fields = [
        ("warehouse_source_qualification", source["qualification"]),
        (
            "official_distribution_eligible",
            source["official_distribution_eligible"],
        ),
        ("quote_policy", source["quote_policy"]),
    ]
    if reverse:
        fields.reverse()
    owner.update(fields)


def _partial_legacy_owner_with_full_provenance_owner(
    *,
    legacy_owner_name: str,
    reverse: bool = False,
) -> dict:
    snapshot = _snapshot_2510(source=None)
    ui_contract = snapshot["ui_contract"]
    owners = {
        "root": snapshot,
        "context": ui_contract["context"],
        "source": ui_contract.setdefault("source", {}),
    }
    full_owner_name = "context" if legacy_owner_name == "root" else "root"
    _declare_source(owners[full_owner_name], UNKNOWN_SOURCE_WITH_PROVENANCE)
    _declare_legacy_source_triplet(
        owners[legacy_owner_name],
        UNKNOWN_SOURCE_WITH_PROVENANCE,
        reverse=reverse,
    )
    return snapshot


def _assert_source_rejection_is_default_rollback_noop(
    snapshot: dict,
    monkeypatch: pytest.MonkeyPatch,
    *,
    max_combos: int = 8_000,
) -> tuple[dict, dict]:
    default = _run(snapshot, max_combos=max_combos)
    monkeypatch.setenv(AHMED_NONOFFICIAL_ZERO_RED_RISK_FLOOR_SPLIT_DISABLE, "1")
    rollback = _run(snapshot, max_combos=max_combos)
    monkeypatch.delenv(
        AHMED_NONOFFICIAL_ZERO_RED_RISK_FLOOR_SPLIT_DISABLE,
        raising=False,
    )

    assert default["risk_floor"] == rollback["risk_floor"] == {}
    assert default == rollback
    return default, rollback


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_2510_small_zero_red_tail_is_structured_without_overwriting_ordinary_quotes(
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2510()
    default = _run(snapshot, max_combos=max_combos)

    monkeypatch.setenv(AHMED_NONOFFICIAL_ZERO_RED_QUOTE_CAP_DISABLE, "1")
    policy_disabled = _run(snapshot, max_combos=max_combos)

    assert _quotes(default) == _quotes(policy_disabled)
    assert default["quality_count_cells_candidates"]["q5"] == [
        {"count": 6, "cells": 19, "prob": 0.9733},
        {"count": 12, "cells": 38, "prob": 0.0267},
    ]
    assert default["quality_count_candidates"]["q6"] == [
        {"value": 6, "prob": 0.9733},
        {"value": 0, "prob": 0.0267},
    ]
    assert default["quality_lock"]["q5"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert default["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }

    risk = default["risk_floor"]
    assert risk["status"] == "available"
    assert risk["kind"] == "nonofficial_q5q6_zero_red_tail"
    assert risk["warehouse_qualification"] == "postupdate_unknown_warehouse_source"
    assert risk["official_distribution_eligible"] is False
    assert risk["quote_policy"] == "advisory_only_no_clamp"
    assert risk["probability"] == pytest.approx(0.0267, abs=0.0001)
    assert risk["selected_branch"] == {
        "q5_count": 12,
        "q6_count": 0,
        "probability": pytest.approx(0.0267, abs=0.0001),
        "eligible": True,
    }
    assert tuple(risk["ordinary_quotes"].values()) == _quotes(default)
    assert risk["tail_quotes"]["aggressive"] < default["conservative"]
    assert policy_disabled["risk_floor"] == {}


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_2510_split_rollback_restores_historical_tail_overwrite(
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2510()
    default = _run(snapshot, max_combos=max_combos)

    monkeypatch.setenv(AHMED_NONOFFICIAL_ZERO_RED_RISK_FLOOR_SPLIT_DISABLE, "1")
    rollback = _run(snapshot, max_combos=max_combos)

    assert _quotes(rollback) == tuple(default["risk_floor"]["tail_quotes"].values())
    assert rollback["risk_floor"] == {}
    assert _without_zero_red_policy_outputs(default) == (
        _without_zero_red_policy_outputs(rollback)
    )


@pytest.mark.parametrize("evidence_kind", ["q5_count", "q5_cells", "total_cells"])
def test_2510_stronger_evidence_removes_the_zero_red_tail(
    evidence_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2510()
    structured = snapshot["structured_ref_inputs"]
    if evidence_kind == "q5_count":
        structured["counts"]["q5"] = 6
    elif evidence_kind == "q5_cells":
        structured["quality_cells"]["q5"] = 19
    else:
        structured["total_cells"] = 120

    default = _run(snapshot)
    monkeypatch.setenv(AHMED_NONOFFICIAL_ZERO_RED_RISK_FLOOR_SPLIT_DISABLE, "1")
    rollback = _run(snapshot)

    assert default["risk_floor"] == rollback["risk_floor"] == {}
    assert _quotes(default) == _quotes(rollback)
    assert default["quality_count_ranges"]["q6"] == [6, 6, 6]
    if evidence_kind == "total_cells":
        assert default["quality_count_cells_candidates"]["q6"] == [
            {"count": 6, "cells": 25, "prob": 1.0}
        ]


def test_2510_subthreshold_zero_red_tail_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2510(q5_avg_cells=1.6666666)
    default = _run(snapshot)
    monkeypatch.setenv(AHMED_NONOFFICIAL_ZERO_RED_RISK_FLOOR_SPLIT_DISABLE, "1")
    rollback = _run(snapshot)

    assert [
        row
        for row in default["quality_count_cells_candidates"]["q6"]
        if row["count"] == 0
    ] == [{"count": 0, "cells": 0, "prob": 0.0121}]
    assert default["risk_floor"] == rollback["risk_floor"] == {}
    assert default == rollback


@pytest.mark.parametrize(
    ("source", "qualification"),
    [
        (UNKNOWN_SOURCE, "postupdate_unknown_warehouse_source"),
        (PLAYER_SOURCE, "player_custom_warehouse"),
    ],
)
def test_2510_nonofficial_source_pools_remain_distinct(
    source: dict,
    qualification: str,
) -> None:
    result = _run(_snapshot_2510(source=source))

    assert result["risk_floor"]["warehouse_qualification"] == qualification
    assert result["risk_floor"]["official_distribution_eligible"] is False


@pytest.mark.parametrize(
    "snapshot",
    [
        _snapshot_2510(source=OFFICIAL_SOURCE),
        _snapshot_2510(source=None),
        _snapshot_2510(phase="settled"),
    ],
)
def test_2510_official_missing_or_settled_source_is_noop(
    snapshot: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = _run(snapshot)
    monkeypatch.setenv(AHMED_NONOFFICIAL_ZERO_RED_RISK_FLOOR_SPLIT_DISABLE, "1")
    rollback = _run(snapshot)

    assert default["risk_floor"] == rollback["risk_floor"] == {}
    assert default == rollback


def test_2510_exact_zero_red_is_priced_by_the_ordinary_posterior() -> None:
    snapshot = _snapshot_2510()
    snapshot["structured_ref_inputs"]["counts"]["q5"] = 12

    result = _run(snapshot)

    assert result["quality_count_ranges"]["q6"] == [0, 0, 0]
    assert result["quality_count_cells_candidates"]["q6"] == [
        {"count": 0, "cells": 0, "prob": 1.0}
    ]
    assert result["quality_lock"]["q6"]["strength"] == "exact"
    assert result["risk_floor"] == {}


def test_real_2503_direct_q5_cells_and_average_is_a_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2503_direct_q5()
    default = _run(snapshot)
    monkeypatch.setenv(AHMED_NONOFFICIAL_ZERO_RED_RISK_FLOOR_SPLIT_DISABLE, "1")
    rollback = _run(snapshot)

    assert default["quality_count_cells_candidates"]["q5"] == [
        {"count": 4, "cells": 26, "prob": 1.0}
    ]
    assert default["quality_count_ranges"]["q6"] == [5, 5, 5]
    assert default["risk_floor"] == rollback["risk_floor"] == {}
    assert default == rollback


def test_2510_product_summary_forwards_the_same_structured_risk_floor() -> None:
    snapshot = _snapshot_2510()
    engine = _run(snapshot)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=ROOT / "scratch" / "2510_zero_red_tail_core_test.json",
    )

    assert summary["ahmed_ref"]["risk_floor"] == engine["risk_floor"]
    assert summary["reference"]["decision_range"] == " / ".join(
        f"{value:,}" for value in _quotes(engine)
    )


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
@pytest.mark.parametrize(
    ("context_source", "source_owner_source", "root_source"),
    [
        (UNKNOWN_SOURCE, None, OFFICIAL_SOURCE),
        (OFFICIAL_SOURCE, None, UNKNOWN_SOURCE),
        (PLAYER_SOURCE, None, UNKNOWN_SOURCE),
        (UNKNOWN_SOURCE, PLAYER_SOURCE, UNKNOWN_SOURCE),
    ],
)
def test_2510_conflicting_source_owners_fail_closed_in_both_orders(
    context_source: dict,
    source_owner_source: dict | None,
    root_source: dict,
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2510(source=None)
    _declare_source(snapshot["ui_contract"]["context"], context_source)
    if source_owner_source is not None:
        source_owner = snapshot["ui_contract"].setdefault("source", {})
        _declare_source(source_owner, source_owner_source)
    _declare_source(snapshot, root_source)

    default, _rollback = _assert_source_rejection_is_default_rollback_noop(
        snapshot,
        monkeypatch,
        max_combos=max_combos,
    )
    clean = _run(_snapshot_2510(), max_combos=max_combos)
    assert _quotes(default) == _quotes(clean)
    for key in (
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
    ):
        assert default[key] == clean[key]


@pytest.mark.parametrize(
    ("context_source", "root_source"),
    [
        (UNKNOWN_SOURCE, OFFICIAL_SOURCE),
        (OFFICIAL_SOURCE, UNKNOWN_SOURCE),
        (PLAYER_SOURCE, UNKNOWN_SOURCE),
        (UNKNOWN_SOURCE, PLAYER_SOURCE),
    ],
)
def test_2510_product_summary_rejects_conflicting_source_owners(
    context_source: dict,
    root_source: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2510(source=None)
    _declare_source(snapshot["ui_contract"]["context"], context_source)
    _declare_source(snapshot, root_source)

    default = summarize_snapshot(
        snapshot,
        snapshot_path=ROOT / "scratch" / "2510_source_conflict_default.json",
    )
    monkeypatch.setenv(AHMED_NONOFFICIAL_ZERO_RED_RISK_FLOOR_SPLIT_DISABLE, "1")
    rollback = summarize_snapshot(
        snapshot,
        snapshot_path=ROOT / "scratch" / "2510_source_conflict_rollback.json",
    )

    assert default["status"] == rollback["status"] == "ok"
    assert default["ahmed_ref"]["risk_floor"] == rollback["ahmed_ref"]["risk_floor"] == {}
    assert default["ahmed_ref"] == rollback["ahmed_ref"]
    assert (
        default["context"]["warehouse_source_qualification"]
        == rollback["context"]["warehouse_source_qualification"]
        == ""
    )
    assert default["reference"]["decision_range"] == rollback["reference"][
        "decision_range"
    ]


@pytest.mark.parametrize(
    ("source", "qualification"),
    [
        (UNKNOWN_SOURCE, "postupdate_unknown_warehouse_source"),
        (PLAYER_SOURCE, "player_custom_warehouse"),
    ],
)
@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_2510_consistent_source_declarations_across_all_owners_remain_eligible(
    source: dict,
    qualification: str,
    max_combos: int,
) -> None:
    snapshot = _snapshot_2510(source=None)
    _declare_all_source_owners(snapshot, source)

    result = _run(snapshot, max_combos=max_combos)

    assert result["risk_floor"]["warehouse_qualification"] == qualification
    assert result["risk_floor"]["official_distribution_eligible"] is False


@pytest.mark.parametrize("partial_owner", ["context", "source", "root"])
@pytest.mark.parametrize("missing_field", list(UNKNOWN_SOURCE))
def test_2510_partial_nested_source_contract_is_fail_closed(
    partial_owner: str,
    missing_field: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2510(source=None)
    _declare_all_source_owners(snapshot, UNKNOWN_SOURCE)
    owner = (
        snapshot
        if partial_owner == "root"
        else snapshot["ui_contract"][partial_owner]
    )
    owner["warehouse_source"].pop(missing_field)

    _assert_source_rejection_is_default_rollback_noop(snapshot, monkeypatch)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("warehouse_source_qualification", "player_custom_warehouse"),
        ("official_distribution_eligible", True),
        ("quote_policy", "normal"),
    ],
)
def test_2510_conflicting_alias_is_fail_closed(
    field: str,
    value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2510(source=None)
    _declare_all_source_owners(snapshot, UNKNOWN_SOURCE)
    snapshot["ui_contract"]["source"][field] = value

    _assert_source_rejection_is_default_rollback_noop(snapshot, monkeypatch)


@pytest.mark.parametrize(
    "malformed_source",
    [
        _ExplodingSourceDict(UNKNOWN_SOURCE),
        _ExplodingSourceMapping(),
        {
            "qualification": _ExplodingQualification(
                "postupdate_unknown_warehouse_source"
            ),
            "official_distribution_eligible": False,
            "quote_policy": "advisory_only_no_clamp",
        },
    ],
)
@pytest.mark.parametrize("owner_name", ["context", "source", "root"])
def test_2510_malicious_source_container_or_scalar_never_runs_magic(
    malformed_source: object,
    owner_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2510(source=None)
    owner = (
        snapshot
        if owner_name == "root"
        else snapshot["ui_contract"].setdefault(owner_name, {})
    )
    owner["warehouse_source"] = malformed_source
    _ExplodingSourceDict.magic_calls = 0
    _ExplodingSourceMapping.magic_calls = 0
    _ExplodingQualification.magic_calls = 0

    _assert_source_rejection_is_default_rollback_noop(snapshot, monkeypatch)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=ROOT / "scratch" / "2510_malicious_source_contract.json",
    )

    assert summary["status"] == "ok"
    assert summary["ahmed_ref"]["risk_floor"] == {}
    assert _ExplodingSourceDict.magic_calls == 0
    assert _ExplodingSourceMapping.magic_calls == 0
    assert _ExplodingQualification.magic_calls == 0


def test_2510_hash_collision_key_is_rejected_without_comparison_magic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collision = _CollisionKey("qualification")
    malformed_source = {
        "official_distribution_eligible": False,
        "quote_policy": "advisory_only_no_clamp",
    }
    malformed_source[collision] = "postupdate_unknown_warehouse_source"
    collision.armed = True
    snapshot = _snapshot_2510(source=None)
    snapshot["warehouse_source"] = malformed_source

    _assert_source_rejection_is_default_rollback_noop(snapshot, monkeypatch)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=ROOT / "scratch" / "2510_collision_source_contract.json",
    )

    assert summary["status"] == "ok"
    assert summary["ahmed_ref"]["risk_floor"] == {}
    assert collision.magic_calls == 0


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_2510_matching_nonquarantined_provenance_remains_eligible(
    max_combos: int,
) -> None:
    snapshot = _snapshot_2510(source=None)
    _declare_all_source_owners(snapshot, UNKNOWN_SOURCE_WITH_PROVENANCE)

    direct = _run(snapshot, max_combos=max_combos)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=ROOT / "scratch" / "2510_valid_provenance.json",
    )

    assert direct["risk_floor"]["warehouse_qualification"] == (
        "postupdate_unknown_warehouse_source"
    )
    assert summary["ahmed_ref"]["risk_floor"] == direct["risk_floor"]


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
@pytest.mark.parametrize("owner_name", ["context", "source", "root"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("quarantine_required", True, id="quarantined"),
        pytest.param(
            "expected_map_table_sha256",
            OTHER_MAP_TABLE_SHA256,
            id="map-table-hash-mismatch",
        ),
    ],
)
def test_2510_quarantine_or_hash_mismatch_is_atomic_default_rollback_noop(
    max_combos: int,
    owner_name: str,
    field: str,
    value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2510(source=None)
    _declare_all_source_owners(snapshot, UNKNOWN_SOURCE_WITH_PROVENANCE)
    owner = (
        snapshot
        if owner_name == "root"
        else snapshot["ui_contract"][owner_name]
    )
    owner["warehouse_source"][field] = value

    direct, _rollback = _assert_source_rejection_is_default_rollback_noop(
        snapshot,
        monkeypatch,
        max_combos=max_combos,
    )
    clean = _run(
        _snapshot_2510(source=UNKNOWN_SOURCE_WITH_PROVENANCE),
        max_combos=max_combos,
    )
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=ROOT / "scratch" / "2510_rejected_provenance.json",
    )

    for key in (
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
    ):
        assert direct[key] == clean[key]
    assert summary["status"] == "ok"
    assert summary["ahmed_ref"]["risk_floor"] == {}
    assert summary["reference"]["decision_range"] == " / ".join(
        f"{value:,}" for value in _quotes(direct)
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("quarantine_required", "false", id="quarantine-wrong-type"),
        pytest.param(
            "map_table_sha256",
            _ExplodingQualification(MAP_TABLE_SHA256),
            id="hash-subclass",
        ),
        pytest.param(
            "expected_map_table_sha256",
            None,
            id="hash-null",
        ),
    ],
)
def test_2510_provenance_present_invalid_is_fail_closed_without_magic(
    field: str,
    value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2510(source=None)
    _declare_all_source_owners(snapshot, UNKNOWN_SOURCE_WITH_PROVENANCE)
    snapshot["ui_contract"]["source"]["warehouse_source"][field] = value
    _ExplodingQualification.magic_calls = 0

    _assert_source_rejection_is_default_rollback_noop(snapshot, monkeypatch)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=ROOT / "scratch" / "2510_invalid_provenance.json",
    )

    assert summary["status"] == "ok"
    assert summary["ahmed_ref"]["risk_floor"] == {}
    assert _ExplodingQualification.magic_calls == 0


@pytest.mark.parametrize(
    "mutation",
    ["missing-expected-hash", "missing-quarantine", "different-matching-hash-pair"],
)
def test_2510_partial_or_cross_owner_provenance_is_fail_closed(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2510(source=None)
    _declare_all_source_owners(snapshot, UNKNOWN_SOURCE_WITH_PROVENANCE)
    source_contract = snapshot["ui_contract"]["source"]["warehouse_source"]
    if mutation == "missing-expected-hash":
        source_contract.pop("expected_map_table_sha256")
    elif mutation == "missing-quarantine":
        source_contract.pop("quarantine_required")
    else:
        source_contract["map_table_sha256"] = OTHER_MAP_TABLE_SHA256
        source_contract["expected_map_table_sha256"] = OTHER_MAP_TABLE_SHA256

    _assert_source_rejection_is_default_rollback_noop(snapshot, monkeypatch)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=ROOT / "scratch" / "2510_partial_provenance.json",
    )

    assert summary["status"] == "ok"
    assert summary["ahmed_ref"]["risk_floor"] == {}


def _collision_source_owner() -> dict:
    collision = _CollisionKey("warehouse_source")
    owner = {collision: copy.deepcopy(UNKNOWN_SOURCE_WITH_PROVENANCE)}
    collision.armed = True
    owner["_collision"] = collision
    return owner


@pytest.mark.parametrize(
    "malformed_owner",
    [
        _ExplodingSourceDict(),
        _ExplodingSourceMapping(),
        _ExplodingQualification("source"),
        pytest.param(_collision_source_owner(), id="collision-key"),
    ],
)
def test_2510_invalid_ui_source_owner_cannot_be_erased_before_summary_decision(
    malformed_owner: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2510(source=None)
    _declare_source(snapshot, UNKNOWN_SOURCE_WITH_PROVENANCE)
    _declare_source(
        snapshot["ui_contract"]["context"],
        UNKNOWN_SOURCE_WITH_PROVENANCE,
    )
    snapshot["ui_contract"]["source"] = malformed_owner
    _ExplodingSourceDict.magic_calls = 0
    _ExplodingSourceMapping.magic_calls = 0
    _ExplodingQualification.magic_calls = 0
    collision = (
        malformed_owner.get("_collision")
        if type(malformed_owner) is dict  # noqa: E721 - exact safe fixture dict
        and "_collision" in malformed_owner
        else None
    )

    _assert_source_rejection_is_default_rollback_noop(snapshot, monkeypatch)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=ROOT / "scratch" / "2510_invalid_source_owner.json",
    )

    assert summary["status"] == "ok"
    assert summary["ahmed_ref"]["risk_floor"] == {}
    assert _ExplodingSourceDict.magic_calls == 0
    assert _ExplodingSourceMapping.magic_calls == 0
    assert _ExplodingQualification.magic_calls == 0
    if collision is not None:
        assert collision.magic_calls == 0


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
@pytest.mark.parametrize("legacy_owner_name", ["root", "context", "source"])
@pytest.mark.parametrize("reverse", [False, True])
def test_2510_each_present_owner_must_be_independently_complete(
    max_combos: int,
    legacy_owner_name: str,
    reverse: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _partial_legacy_owner_with_full_provenance_owner(
        legacy_owner_name=legacy_owner_name,
        reverse=reverse,
    )

    default, rollback = _assert_source_rejection_is_default_rollback_noop(
        snapshot,
        monkeypatch,
        max_combos=max_combos,
    )
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=ROOT / "scratch" / "2510_partial_owner.json",
    )

    assert default == rollback
    assert summary["status"] == "ok"
    assert summary["ahmed_ref"]["risk_floor"] == {}
    assert summary["reference"]["decision_range"] == " / ".join(
        f"{value:,}" for value in _quotes(default)
    )


def test_2510_partial_owner_is_fail_closed_through_loopback_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _partial_legacy_owner_with_full_provenance_owner(
        legacy_owner_name="root",
    )
    monkeypatch.setattr(panel, "_read_json", lambda _path: snapshot)
    config = ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    try:
        with urlopen(  # noqa: S310 - loopback-only product regression
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

    assert status == 200
    assert payload["status"] == "ok"
    assert payload["ahmed_ref"]["risk_floor"] == {}


def _owner_container_fixture(
    *,
    owner_name: str,
    container_kind: str,
) -> tuple[dict, _CollisionKey | None]:
    snapshot = _snapshot_2510(source=None)
    _declare_all_source_owners(snapshot, UNKNOWN_SOURCE_WITH_PROVENANCE)
    ui_contract = snapshot["ui_contract"]
    owner = snapshot if owner_name == "root" else ui_contract[owner_name]
    collision: _CollisionKey | None = None

    if container_kind == "dict-subclass":
        replacement = _ExplodingSourceDict()
        dict.update(replacement, owner)
    elif container_kind == "mapping":
        replacement = _ExplodingSourceMapping()
    else:
        replacement = dict.copy(owner)
        source = replacement.pop("warehouse_source")
        collision = _CollisionKey("warehouse_source")
        replacement[collision] = source
        collision.armed = True

    if owner_name == "root":
        snapshot = replacement
    else:
        ui_contract[owner_name] = replacement
    return snapshot, collision


@pytest.mark.parametrize("owner_name", ["root", "context", "source"])
@pytest.mark.parametrize(
    "container_kind",
    ["dict-subclass", "mapping", "collision-key"],
)
def test_2510_owner_container_is_stabilized_before_direct_prepare_and_summary(
    owner_name: str,
    container_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, collision = _owner_container_fixture(
        owner_name=owner_name,
        container_kind=container_kind,
    )
    _ExplodingSourceDict.magic_calls = 0
    _ExplodingSourceMapping.magic_calls = 0

    prepared = prepare_reference_engine_snapshot(snapshot)
    default, rollback = _assert_source_rejection_is_default_rollback_noop(
        snapshot,
        monkeypatch,
    )
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=ROOT / "scratch" / "2510_unsafe_owner_container.json",
    )

    assert type(prepared) is dict  # noqa: E721 - stable product boundary
    assert default == rollback
    assert default["risk_floor"] == {}
    assert summary["status"] == "ok"
    assert summary["ahmed_ref"]["risk_floor"] == {}
    assert _ExplodingSourceDict.magic_calls == 0
    assert _ExplodingSourceMapping.magic_calls == 0
    if collision is not None:
        assert collision.magic_calls == 0


@pytest.mark.parametrize("owner_name", ["root", "context", "source"])
@pytest.mark.parametrize(
    "container_kind",
    ["dict-subclass", "mapping", "collision-key"],
)
def test_2510_owner_container_keeps_loopback_api_available(
    owner_name: str,
    container_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, collision = _owner_container_fixture(
        owner_name=owner_name,
        container_kind=container_kind,
    )
    _ExplodingSourceDict.magic_calls = 0
    _ExplodingSourceMapping.magic_calls = 0
    config = ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
    monkeypatch.setattr(
        panel,
        "_read_json",
        lambda path: snapshot if path == config.snapshot_path else {},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    try:
        with urlopen(  # noqa: S310 - loopback-only product regression
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

    assert status == 200
    assert payload["status"] == "ok"
    assert payload["ahmed_ref"]["risk_floor"] == {}
    assert _ExplodingSourceDict.magic_calls == 0
    assert _ExplodingSourceMapping.magic_calls == 0
    if collision is not None:
        assert collision.magic_calls == 0


def test_2510_exact_builtin_source_owners_keep_risk_floor() -> None:
    snapshot = _snapshot_2510(source=None)
    _declare_all_source_owners(snapshot, UNKNOWN_SOURCE_WITH_PROVENANCE)

    prepared = prepare_reference_engine_snapshot(snapshot)
    direct = _run(snapshot)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=ROOT / "scratch" / "2510_exact_owner_control.json",
    )

    assert type(prepared) is dict  # noqa: E721
    assert direct["risk_floor"]["status"] == "available"
    assert summary["ahmed_ref"]["risk_floor"] == direct["risk_floor"]
