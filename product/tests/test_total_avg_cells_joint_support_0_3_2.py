"""Discrete whole-warehouse support for public total average cells."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ahmad_live_panel_server import (  # noqa: E402
    ServerConfig,
    make_handler,
    summarize_snapshot,
)
from ahmad_ref_engine import (  # noqa: E402
    TOTAL_AVG_CELLS_GRID_OPTIONS_DISABLE,
    extract_evidence,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from ahmad_tk_overlay import _summary_diagnostic_row  # noqa: E402


ROLLBACK = TOTAL_AVG_CELLS_GRID_OPTIONS_DISABLE
FROZEN_INPUTS = {
    "data/raw/tables/Item.txt": (
        527_604,
        "7c5901ea6fdd680118737d5e5733496f2c802c97d60b0cd70df95a6115fe6625",
    ),
    "data/raw/tables/BidMap.txt": (
        80_972,
        "6c54d421fae9e238f1c1b5afc6deea88e325805ca8df706804e8dfb893f50edd",
    ),
    "data/raw/tables/Drop.txt": (
        294_160,
        "9a8ed5748d05d774fb49f3aabd0769c5e81afe149d1ffaa8dcce92314948c307",
    ),
    "data/raw/tables/Hero.txt": (
        10_628,
        "4763011df55a3bb3f3f07ee804351055c9f058aace482a4df32057098efabed9",
    ),
    "data/processed/red_longtail_normalized_0627.json": (
        992,
        "c5cba6367d1a314533aead17488b12a319f24f21c3f8bf016d275472af21d5b0",
    ),
    "data/processed/drop_table_priors_0627.json": (
        30_211,
        "7ff3859d88a74e184074a8df58d11a284755376eeb6cced7befc697212a3d02c",
    ),
    "data/processed/quality_combo_presolve_q456.json": (
        15_710_713,
        "c2cd901aaf32fabdee3cf7f2b7a233fcd049c3f37d59f62e4dd577f95130b5df",
    ),
    (
        "external_references/AuctionAnalyzer4.13.3/_decompiled/"
        "MapBidCalculator/MapBidCalculator/Models/StaticData.cs"
    ): (
        10_408,
        "7543b4aa16addc73dfaaaf3c6df9bc0f6662bd83458de110a16d04ff9f2f9676",
    ),
}


@pytest.fixture(scope="module", autouse=True)
def _frozen_package_inputs() -> None:
    for relative, (expected_size, expected_sha256) in FROZEN_INPUTS.items():
        payload = (ROOT / relative).read_bytes()
        assert len(payload) == expected_size, relative
        assert hashlib.sha256(payload).hexdigest() == expected_sha256, relative


def _source() -> dict:
    return {
        "qualification": "official_random_warehouse",
        "official_distribution_eligible": True,
        "quote_policy": "advisory_only_no_clamp",
        "quarantine_required": False,
    }


def _fact(semantic: str, value: object) -> dict:
    info_ids = {
        "total_item_count": 200017,
        "total_avg_cells": 200014,
        "q4_item_count": 200018,
        "q4_avg_cells": 200013,
    }
    return {
        "info_id": info_ids.get(semantic),
        "semantic": semantic,
        "kind": "count" if semantic.endswith("item_count") else "avg_cells",
        "value": value,
    }


def _snapshot(
    *,
    hero: str = "ahmed",
    phase: str = "bidding",
    total_count: int = 150,
    total_avg_cells: object | None = 2.01,
    fixed_counts: dict[str, int] | None = None,
    extra_facts: list[dict] | None = None,
    facts: list[dict] | None = None,
    total_cells: int | None = None,
) -> dict:
    source = _source()
    if facts is None:
        facts = [_fact("total_item_count", total_count)]
        if total_avg_cells is not None:
            facts.append(_fact("total_avg_cells", total_avg_cells))
        facts.extend(copy.deepcopy(extra_facts or []))
    structured: dict = {"total_count": total_count}
    if fixed_counts is not None:
        structured["fixed_counts"] = dict(fixed_counts)
    if total_cells is not None:
        structured["total_cells"] = total_cells
    return {
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": 2401,
                "phase": phase,
                "warehouse_source_qualification": source["qualification"],
                "warehouse_source": copy.deepcopy(source),
            },
            "constraints": {
                "public_info": {"public_numeric_facts": copy.deepcopy(facts)}
            },
            "source": {
                "warehouse_source_qualification": source["qualification"],
                "warehouse_source": copy.deepcopy(source),
            },
        },
        "structured_ref_inputs": structured,
        "public_info_rows": [
            {"info_id": row.get("info_id"), "value": row.get("value")}
            for row in facts
            if row.get("info_id") is not None
        ],
        "warehouse_source_qualification": source["qualification"],
        "warehouse_source": copy.deepcopy(source),
    }


def _all_q1_counts() -> dict[str, int]:
    return {"q1": 150, "q3": 0, "q4": 0, "q5": 0, "q6": 0}


def _model_projection(result: dict) -> dict:
    return {
        key: result[key]
        for key in (
            "status",
            "conservative",
            "balanced",
            "aggressive",
            "combo_count",
            "quality_count_ranges",
            "quality_cells_ranges",
            "quality_count_candidates",
            "quality_cells_candidates",
            "quality_count_cells_candidates",
            "quality_lock",
            "total_grid_range",
        )
    }


@pytest.mark.parametrize("hero", ("ahmed", "aisha", "ethan", "maria"))
@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_two_catalog_arms_survive_as_one_joint_per_arm(
    hero: str,
    max_combos: int,
) -> None:
    snapshot = _snapshot(hero=hero, fixed_counts=_all_q1_counts())
    evidence = extract_evidence(snapshot)
    result = run_reference_engine(snapshot, max_combos=max_combos).as_dict()

    assert evidence.total_grid_target is None
    assert evidence.total_grid_options == (301, 302)
    assert result["status"] == "count_prior"
    assert result["combo_count"] == 2
    assert result["total_grid_range"] == [301, 301, 302]
    assert result["quality_cells_ranges"]["q1"] == [301, 301, 302]
    assert result["quality_count_cells_candidates"]["q1"] == [
        {"count": 150, "cells": 301, "prob": 0.5},
        {"count": 150, "cells": 302, "prob": 0.5},
    ]
    assert result["quality_cells_candidates"]["q1"] == [
        {"value": 301, "prob": 0.5},
        {"value": 302, "prob": 0.5},
    ]


@pytest.mark.parametrize("hero", ("ahmed", "aisha"))
@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_sparse_count_prior_cannot_turn_one_grid_arm_into_exact(
    hero: str,
    max_combos: int,
) -> None:
    snapshot = _snapshot(hero=hero)
    result = run_reference_engine(snapshot, max_combos=max_combos).as_dict()

    assert result["status"] == "count_prior"
    assert result["combo_count"] == 1_600
    assert result["total_grid_range"] == [301, 301, 302]
    assert result["evidence"]["total_grid_options"] == [301, 302]
    assert all(
        lock == {"strength": "probabilistic", "reason": "combo_multi_solution"}
        for lock in result["quality_lock"].values()
    )
    assert "combo_cap_hit" not in result["notes"]


@pytest.mark.parametrize("max_combos", (1, 2, 3, 4))
def test_fixed_composition_requires_cap_plus_one_complete_proof(
    max_combos: int,
) -> None:
    result = run_reference_engine(
        _snapshot(fixed_counts=_all_q1_counts()),
        max_combos=max_combos,
    ).as_dict()

    if max_combos == 1:
        assert result["status"] == "no_reachable_combo"
        assert result["combo_count"] == 0
        assert result["total_grid_range"] == [None, None, None]
        assert result["quality_count_cells_candidates"] == {}
        assert "combo_cap_hit" in result["notes"]
        assert "total_avg_cells_grid_support_incomplete" in result["notes"]
    else:
        assert result["status"] == "count_prior"
        assert result["combo_count"] == 2
        assert result["total_grid_range"] == [301, 301, 302]
        assert result["quality_count_cells_candidates"]["q1"] == [
            {"count": 150, "cells": 301, "prob": 0.5},
            {"count": 150, "cells": 302, "prob": 0.5},
        ]
        assert "combo_cap_hit" not in result["notes"]


@pytest.mark.parametrize("max_combos", (1, 2, 3, 4))
def test_sparse_support_is_unavailable_at_every_small_cap(
    max_combos: int,
) -> None:
    result = run_reference_engine(
        _snapshot(),
        max_combos=max_combos,
    ).as_dict()

    assert result["status"] == "no_reachable_combo"
    assert result["combo_count"] == 0
    assert result["total_grid_range"] == [None, None, None]
    assert result["quality_count_candidates"] == {}
    assert result["quality_count_cells_candidates"] == {}
    assert "combo_cap_hit" in result["notes"]
    assert "total_avg_cells_grid_support_incomplete" in result["notes"]


def _assert_total_avg_cells_authority_noop(snapshot: dict) -> None:
    baseline = _snapshot(
        total_avg_cells=None,
        fixed_counts=_all_q1_counts(),
    )
    evidence = extract_evidence(snapshot)
    current = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    control = run_reference_engine(baseline, max_combos=8_000).as_dict()

    assert evidence.total_grid_options == ()
    assert "total" not in evidence.avg_cells
    assert _model_projection(current) == _model_projection(control)


def test_aggregate_only_cannot_authorize_total_average_support() -> None:
    snapshot = _snapshot(fixed_counts=_all_q1_counts())
    snapshot["public_info_rows"] = []

    _assert_total_avg_cells_authority_noop(snapshot)


def test_raw_only_cannot_replace_the_prepared_aggregate() -> None:
    snapshot = _snapshot(fixed_counts=_all_q1_counts())
    facts = snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ]
    snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ] = [row for row in facts if row.get("info_id") != 200014]

    _assert_total_avg_cells_authority_noop(snapshot)


@pytest.mark.parametrize("reverse", (False, True))
@pytest.mark.parametrize("malformed", (float("nan"), float("inf")))
def test_raw_malformed_current_sibling_rejects_the_whole_arm(
    reverse: bool,
    malformed: float,
) -> None:
    snapshot = _snapshot(fixed_counts=_all_q1_counts())
    bad_row = {"info_id": 200014, "value": malformed}
    if reverse:
        snapshot["public_info_rows"].insert(0, bad_row)
    else:
        snapshot["public_info_rows"].append(bad_row)

    _assert_total_avg_cells_authority_noop(snapshot)


@pytest.mark.parametrize("reverse", (False, True))
def test_raw_conflict_is_order_independent(reverse: bool) -> None:
    snapshot = _snapshot(fixed_counts=_all_q1_counts())
    conflicting = {"info_id": 200014, "value": 2.02}
    if reverse:
        snapshot["public_info_rows"].insert(0, conflicting)
    else:
        snapshot["public_info_rows"].append(conflicting)

    _assert_total_avg_cells_authority_noop(snapshot)


def test_raw_and_prepared_ledgers_must_match_atomically() -> None:
    snapshot = _snapshot(fixed_counts=_all_q1_counts())
    for row in snapshot["public_info_rows"]:
        if row.get("info_id") == 200014:
            row["value"] = 2.02

    _assert_total_avg_cells_authority_noop(snapshot)


@pytest.mark.parametrize("marker", ("stale", "cross_session"))
def test_stale_or_cross_session_raw_rows_do_not_authorize(
    marker: str,
) -> None:
    snapshot = _snapshot(fixed_counts=_all_q1_counts())
    snapshot["ui_contract"]["context"]["session_id"] = "current"
    for row in snapshot["public_info_rows"]:
        if marker == "stale":
            row["stale"] = True
        else:
            row["session_id"] = "previous"

    _assert_total_avg_cells_authority_noop(snapshot)


@pytest.mark.parametrize("identity", ("session_id", "session_epoch", "epoch"))
def test_present_raw_identity_requires_a_matching_current_anchor(
    identity: str,
) -> None:
    snapshot = _snapshot(fixed_counts=_all_q1_counts())
    for row in snapshot["public_info_rows"]:
        row[identity] = "orphan" if identity == "session_id" else 9

    _assert_total_avg_cells_authority_noop(snapshot)


@pytest.mark.parametrize("stale_kind", ("stale", "cross_session"))
def test_stale_sibling_does_not_override_a_complete_current_ledger(
    stale_kind: str,
) -> None:
    snapshot = _snapshot(fixed_counts=_all_q1_counts())
    snapshot["ui_contract"]["context"]["session_id"] = "current"
    for row in snapshot["public_info_rows"]:
        row["session_id"] = "current"
    sibling = {"info_id": 200014, "value": float("nan")}
    if stale_kind == "stale":
        sibling["stale"] = True
    else:
        sibling["session_id"] = "previous"
    snapshot["public_info_rows"].append(sibling)

    evidence = extract_evidence(snapshot)
    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.total_grid_options == (301, 302)
    assert result["combo_count"] == 2
    assert result["total_grid_range"] == [301, 301, 302]


def test_matching_current_identity_and_same_duplicates_remain_valid() -> None:
    snapshot = _snapshot(fixed_counts=_all_q1_counts())
    snapshot["ui_contract"]["context"].update(
        {"session_id": "current", "session_epoch": 8, "epoch": 11}
    )
    for row in snapshot["public_info_rows"]:
        row.update({"session_id": "current", "session_epoch": 8, "epoch": 11})
    snapshot["public_info_rows"].extend(
        copy.deepcopy(snapshot["public_info_rows"])
    )

    evidence = extract_evidence(snapshot)
    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.total_grid_options == (301, 302)
    assert result["combo_count"] == 2
    assert result["total_grid_range"] == [301, 301, 302]


def test_prepared_invalid_sentinel_survives_lossy_raw_row_projection() -> None:
    snapshot = _snapshot(fixed_counts=_all_q1_counts())
    snapshot["public_info_rows"].append(
        {"info_id": 200014, "value": float("nan")}
    )
    prepared = prepare_reference_engine_snapshot(snapshot)
    prepared["public_info_rows"] = [
        row
        for row in prepared["public_info_rows"]
        if not (
            row.get("info_id") == 200014
            and isinstance(row.get("value"), float)
            and not math.isfinite(row["value"])
        )
    ]
    prepared_again = prepare_reference_engine_snapshot(prepared)

    _assert_total_avg_cells_authority_noop(prepared_again)


@pytest.mark.parametrize("reverse", (False, True))
def test_quality_average_stays_outside_the_whole_warehouse_namespace(
    reverse: bool,
) -> None:
    facts = [
        _fact("total_item_count", 150),
        _fact("total_avg_cells", 2.01),
        _fact("q4_avg_cells", 3.0),
        _fact("q4_item_count", 3),
    ]
    if reverse:
        facts.reverse()
    fixed = {"q1": 147, "q3": 0, "q4": 3, "q5": 0, "q6": 0}
    snapshot = _snapshot(facts=facts, fixed_counts=fixed)
    evidence = extract_evidence(snapshot)
    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.avg_cells == {"total": 2.01, "q4": 3.0}
    assert evidence.total_grid_options == (301, 302)
    assert result["quality_cells_ranges"]["q4"] == [9, 9, 9]
    assert result["quality_count_cells_candidates"]["q4"] == [
        {"count": 3, "cells": 9, "prob": 1.0}
    ]
    assert result["quality_count_cells_candidates"]["q1"] == [
        {"count": 147, "cells": 292, "prob": 0.5},
        {"count": 147, "cells": 293, "prob": 0.5},
    ]


@pytest.mark.parametrize(
    "facts",
    (
        [_fact("total_avg_cells", 2.01), _fact("total_avg_cells", 2.02)],
        [_fact("total_avg_cells", 2.02), _fact("total_avg_cells", 2.01)],
        [_fact("total_avg_cells", 2.01), _fact("total_avg_cells", "broken")],
        [_fact("total_avg_cells", "broken"), _fact("total_avg_cells", 2.01)],
        [_fact("total_avg_cells", 2.01), _fact("total_avg_cells", float("nan"))],
        [_fact("total_avg_cells", float("inf"))],
        [_fact("total_avg_cells", 0)],
    ),
    ids=(
        "conflict-forward",
        "conflict-reverse",
        "malformed-after",
        "malformed-before",
        "nan",
        "inf",
        "zero",
    ),
)
def test_conflicting_or_malformed_total_average_is_a_model_noop(
    facts: list[dict],
) -> None:
    full_facts = [_fact("total_item_count", 150), *copy.deepcopy(facts)]
    snapshot = _snapshot(facts=full_facts, fixed_counts=_all_q1_counts())
    baseline = _snapshot(
        total_avg_cells=None,
        fixed_counts=_all_q1_counts(),
    )
    evidence = extract_evidence(snapshot)
    current = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    control = run_reference_engine(baseline, max_combos=8_000).as_dict()

    assert "total" not in evidence.avg_cells
    assert evidence.total_grid_target is None
    assert evidence.total_grid_options == ()
    assert current == control
    assert not any(
        str(note).startswith("total_avg_cells_grid_options:conflict")
        for note in evidence.source_notes
    )


def test_duplicate_same_value_is_deduplicated_without_changing_support() -> None:
    facts = [
        _fact("total_avg_cells", 2.01),
        _fact("total_item_count", 150),
        _fact("total_avg_cells", 2.01),
    ]
    snapshot = _snapshot(facts=facts, fixed_counts=_all_q1_counts())
    evidence = extract_evidence(snapshot)
    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.total_grid_options == (301, 302)
    assert result["combo_count"] == 2


def test_unique_wire_value_remains_an_exact_catalog_total() -> None:
    fixed = {"q1": 41, "q3": 0, "q4": 0, "q5": 0, "q6": 0}
    snapshot = _snapshot(
        total_count=41,
        total_avg_cells=100 / 41,
        fixed_counts=fixed,
    )
    evidence = extract_evidence(snapshot)
    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.total_grid_target == 100
    assert evidence.total_grid_options == (100,)
    assert result["total_grid_range"] == [100, 100, 100]
    assert result["quality_count_cells_candidates"]["q1"] == [
        {"count": 41, "cells": 100, "prob": 1.0}
    ]


def _one_red_exact_composition(q1_cells: int) -> dict:
    return _snapshot(
        fixed_counts={"q1": 149, "q3": 0, "q4": 0, "q5": 0, "q6": 1},
    ) | {
        "structured_ref_inputs": {
            "total_count": 150,
            "fixed_counts": {
                "q1": 149,
                "q3": 0,
                "q4": 0,
                "q5": 0,
                "q6": 1,
            },
            "quality_cells": {"q1": q1_cells},
        }
    }


def test_exact_quality_composition_may_prove_one_catalog_arm_unique() -> None:
    snapshot = _one_red_exact_composition(295)
    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert result["evidence"]["total_grid_options"] == [301]
    assert result["evidence"]["total_grid_target"] == 301
    assert result["total_grid_range"] == [301, 301, 301]
    assert result["quality_count_cells_candidates"]["q6"] == [
        {"count": 1, "cells": 6, "prob": 1.0}
    ]
    assert "total_avg_cells_grid_composition_unique" in result["notes"]


def test_exact_quality_composition_with_no_catalog_arm_fails_closed() -> None:
    result = run_reference_engine(
        _one_red_exact_composition(288),
        max_combos=8_000,
    ).as_dict()

    assert result["status"] == "no_reachable_combo"
    assert result["combo_count"] == 0
    assert result["evidence"]["total_grid_options"] == [301, 302]
    assert result["quality_count_cells_candidates"] == {}


def test_unreachable_display_average_matches_the_absent_average_control() -> None:
    fixed = {"q1": 3, "q3": 0, "q4": 0, "q5": 0, "q6": 0}
    snapshot = _snapshot(total_count=3, total_avg_cells=2.5, fixed_counts=fixed)
    baseline = _snapshot(total_count=3, total_avg_cells=None, fixed_counts=fixed)
    evidence = extract_evidence(snapshot)

    assert evidence.total_grid_target is None
    assert evidence.total_grid_options == ()
    assert _model_projection(
        run_reference_engine(snapshot, max_combos=8_000).as_dict()
    ) == _model_projection(
        run_reference_engine(baseline, max_combos=8_000).as_dict()
    )


@pytest.mark.parametrize(("total_cells", "expected"), ((301, (301,)), (303, ())))
def test_explicit_total_cells_remains_authoritative(
    total_cells: int,
    expected: tuple[int, ...],
) -> None:
    snapshot = _snapshot(
        fixed_counts=_all_q1_counts(),
        total_cells=total_cells,
    )
    evidence = extract_evidence(snapshot)
    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.total_grid_target == total_cells
    assert evidence.total_grid_options == expected
    assert result["total_grid_range"] == [total_cells] * 3


def test_settled_phase_does_not_inject_total_average_grid_support() -> None:
    snapshot = _snapshot(phase="settled", fixed_counts=_all_q1_counts())
    baseline = _snapshot(
        phase="settled",
        total_avg_cells=None,
        fixed_counts=_all_q1_counts(),
    )
    evidence = extract_evidence(snapshot)

    assert evidence.total_grid_options == ()
    assert evidence.total_grid_target is None
    assert _model_projection(
        run_reference_engine(snapshot, max_combos=8_000).as_dict()
    ) == _model_projection(
        run_reference_engine(baseline, max_combos=8_000).as_dict()
    )


def test_summary_api_and_tk_keep_the_same_two_arm_contract(tmp_path: Path) -> None:
    snapshot = _snapshot(hero="aisha", fixed_counts=_all_q1_counts())
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    summary = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(
            ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - self-created loopback product contract
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=30,
        ) as response:
            assert response.status == 200
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)

    tk_row = _summary_diagnostic_row(
        payload,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )
    for result in (summary["ahmed_ref"], payload["ahmed_ref"]):
        assert result["total_grid_range"] == [301, 301, 302]
        assert result["evidence"]["total_grid_options"] == [301, 302]
    assert tk_row["ref_v0"]["total_grid_range"] == [301, 301, 302]
    assert "total_avg_cells_grid_options:multi:301,302" in tk_row["ref_v0"][
        "notes"
    ]


_FRESH_PROCESS_RUNNER = r"""
import json
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root / "hero_ref" / "src"))
from ahmad_ref_engine import extract_evidence, run_reference_engine

snapshot = json.load(sys.stdin)
evidence = extract_evidence(snapshot)
result = run_reference_engine(snapshot, max_combos=int(sys.argv[1])).as_dict()
print(json.dumps({
    "target": evidence.total_grid_target,
    "options": list(evidence.total_grid_options),
    "status": result["status"],
    "combo_count": result["combo_count"],
    "grid": result["total_grid_range"],
    "q1_joint": result["quality_count_cells_candidates"].get("q1"),
    "locks": result["quality_lock"],
}, sort_keys=True))
"""


def _fresh_process(snapshot: dict, max_combos: int, *, rollback: bool) -> dict:
    env = os.environ.copy()
    if rollback:
        env[ROLLBACK] = "1"
    else:
        env.pop(ROLLBACK, None)
    completed = subprocess.run(
        [sys.executable, "-c", _FRESH_PROCESS_RUNNER, str(max_combos)],
        cwd=ROOT,
        env=env,
        input=json.dumps(snapshot),
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.parametrize("max_combos", (1, 2, 3, 4, 8_000, 50_000))
def test_fresh_process_rollback_is_the_parent_scalar_behavior(
    max_combos: int,
) -> None:
    snapshot = _snapshot(fixed_counts=_all_q1_counts())
    rollback = _fresh_process(snapshot, max_combos, rollback=True)
    current = _fresh_process(snapshot, max_combos, rollback=False)

    assert rollback["target"] == 301
    assert rollback["options"] == []
    assert rollback["combo_count"] == 1
    assert rollback["grid"] == [301, 301, 301]
    assert rollback["q1_joint"] == [{"count": 150, "cells": 301, "prob": 1.0}]
    assert current["target"] is None
    assert current["options"] == [301, 302]
    if max_combos == 1:
        assert current["status"] == "no_reachable_combo"
        assert current["combo_count"] == 0
        assert current["grid"] == [None, None, None]
        assert current["q1_joint"] is None
    else:
        assert current["combo_count"] == 2
        assert current["grid"] == [301, 301, 302]
        assert current["q1_joint"] == [
            {"count": 150, "cells": 301, "prob": 0.5},
            {"count": 150, "cells": 302, "prob": 0.5},
        ]


def test_no_total_average_is_byte_identical_with_the_rollback_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(total_avg_cells=None, fixed_counts=_all_q1_counts())
    current = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert current == rollback


def test_no_total_average_prepare_is_byte_identical_with_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(total_avg_cells=None, fixed_counts=_all_q1_counts())
    current = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))

    assert current == rollback
