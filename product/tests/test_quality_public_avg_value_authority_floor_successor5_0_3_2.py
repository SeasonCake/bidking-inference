"""Fail closed before a public q4/q5 average becomes a hard quote floor."""

from __future__ import annotations

import copy
import hashlib
import json
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
    HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_DISABLE,
    HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_NOTE,
    extract_evidence,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from ahmad_tk_overlay import _summary_diagnostic_row  # noqa: E402
from test_aisha_4501_gold_total_avg_residual_0_3_1 import (  # noqa: E402
    _snapshot as _aisha_4501_snapshot,
)


ROLLBACK = HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_DISABLE
NOTE = HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_NOTE
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
_DEFAULT = object()


@pytest.fixture(scope="module", autouse=True)
def _frozen_package_inputs() -> None:
    for relative, (expected_size, expected_sha256) in FROZEN_INPUTS.items():
        payload = (ROOT / relative).read_bytes()
        assert len(payload) == expected_size, relative
        assert hashlib.sha256(payload).hexdigest() == expected_sha256, relative


def _warehouse_source(qualification: str) -> dict:
    return {
        "qualification": qualification,
        "official_distribution_eligible": (
            qualification == "official_random_warehouse"
        ),
        "quote_policy": "advisory_only_no_clamp",
        "quarantine_required": False,
    }


def _aggregate_row(tier: str, value: object) -> dict:
    quality = int(tier[1:])
    return {
        "info_id": 200036 if tier == "q4" else 200037,
        "semantic": f"{tier}_avg_value",
        "kind": "avg_value",
        "quality": quality,
        "value": value,
    }


def _raw_row(tier: str, value: object, *, session_id: str = "session-a") -> dict:
    return {
        "info_id": 200036 if tier == "q4" else 200037,
        "value": value,
        "session_id": session_id,
    }


def _avg_snapshot(
    *,
    hero: str = "ahmed",
    tier: str = "q5",
    count: int = 4,
    avg_value: float = 50_000,
    quality_cells: int = 9,
    phase: str = "bidding",
    qualification: str = "official_random_warehouse",
    aggregate_rows: object = _DEFAULT,
    raw_rows: object = _DEFAULT,
) -> dict:
    fixed_counts = {key: 0 for key in ("q1", "q3", "q4", "q5", "q6")}
    fixed_counts[tier] = count
    source = _warehouse_source(qualification)
    aggregates = (
        [_aggregate_row(tier, avg_value)]
        if aggregate_rows is _DEFAULT
        else copy.deepcopy(aggregate_rows)
    )
    rows = (
        [_raw_row(tier, avg_value)]
        if raw_rows is _DEFAULT
        else copy.deepcopy(raw_rows)
    )
    return {
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": 2404,
                "phase": phase,
                "session_id": "session-a",
                "warehouse_source_qualification": qualification,
                "warehouse_source": copy.deepcopy(source),
            },
            "constraints": {
                "public_info": {"public_avg_values": aggregates},
            },
            "source": {
                "warehouse_source_qualification": qualification,
                "warehouse_source": copy.deepcopy(source),
            },
        },
        "structured_ref_inputs": {
            "total_count": count,
            "fixed_counts": fixed_counts,
            "quality_cells": {tier: quality_cells},
        },
        "public_info_rows": rows,
        "warehouse_source_qualification": qualification,
        "warehouse_source": copy.deepcopy(source),
    }


def _quotes(result: dict) -> list[int]:
    return [int(result[key]) for key in ("conservative", "balanced", "aggressive")]


def _raw_values(result: dict) -> list[int]:
    return [int(result[key]) for key in ("value_p25", "value_p50", "value_p75")]


def _run_pair(
    monkeypatch: pytest.MonkeyPatch,
    snapshot: dict,
    *,
    max_combos: int = 8_000,
) -> tuple[dict, dict]:
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = run_reference_engine(snapshot, max_combos=max_combos).as_dict()
    monkeypatch.delenv(ROLLBACK)
    current = run_reference_engine(snapshot, max_combos=max_combos).as_dict()
    return rollback, current


# The original source's 28-test acceptance matrix remains intact below.
@pytest.mark.parametrize("hero", ("ahmed", "aisha"))
@pytest.mark.parametrize(
    ("tier", "count", "avg_value", "quality_cells", "total"),
    (("q4", 6, 33_333.5, 12, 200_001), ("q5", 4, 50_000, 9, 200_000)),
)
@pytest.mark.parametrize("phase", ("bidding", "manual"))
@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_current_public_row_and_matching_aggregate_make_an_exact_floor(
    hero: str,
    tier: str,
    count: int,
    avg_value: float,
    quality_cells: int,
    total: int,
    phase: str,
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _avg_snapshot(
        hero=hero,
        tier=tier,
        count=count,
        avg_value=avg_value,
        quality_cells=quality_cells,
        phase=phase,
    )
    rollback, current = _run_pair(monkeypatch, snapshot, max_combos=max_combos)

    evidence = extract_evidence(snapshot)
    assert evidence.fixed_counts[tier] == count
    assert evidence.avg_values[tier] == avg_value
    assert evidence.hard_public_avg_values[tier] == avg_value
    assert "hard_public_avg_values" not in current["evidence"]
    assert tier not in evidence.quality_values
    assert _raw_values(current) == _raw_values(rollback) == [total] * 3
    assert current["quality_count_cells_candidates"] == rollback[
        "quality_count_cells_candidates"
    ]
    assert current["quality_lock"] == rollback["quality_lock"]
    assert max(_quotes(rollback)) < total
    assert _quotes(current) == [total] * 3
    assert f"{NOTE}:{tier}={total}" in current["notes"]
    assert all(not str(note).startswith(NOTE) for note in rollback["notes"])


def _no_count_snapshot(*, partial: bool) -> dict:
    snapshot = _avg_snapshot()
    structured = snapshot["structured_ref_inputs"]
    structured["total_count"] = 10
    structured["fixed_counts"] = {"q1": 0, "q3": 0, "q4": 0}
    if partial:
        structured["min_counts"] = {"q5": 4}
    return snapshot


def _normalized_multi_integer_snapshot() -> dict:
    return _avg_snapshot(count=50, avg_value=25_726.69921875, quality_cells=100)


def _conflicting_exact_total_snapshot() -> dict:
    snapshot = _avg_snapshot()
    snapshot["structured_ref_inputs"]["quality_values"] = {"q5": 199_999}
    return snapshot


def _unrelated_q3_snapshot() -> dict:
    snapshot = _avg_snapshot(aggregate_rows=[], raw_rows=[])
    snapshot["ui_contract"]["constraints"]["public_info"]["public_avg_values"] = [
        {"semantic": "q3_avg_value", "kind": "avg_value", "quality": 3, "value": 10_000}
    ]
    return snapshot


@pytest.mark.parametrize(
    "snapshot_factory",
    (
        lambda: _no_count_snapshot(partial=False),
        lambda: _no_count_snapshot(partial=True),
        _normalized_multi_integer_snapshot,
        _conflicting_exact_total_snapshot,
        _unrelated_q3_snapshot,
        lambda: _avg_snapshot(phase="settled"),
    ),
    ids=(
        "missing-hard-count",
        "partial-min-count",
        "wire-normalized-multiple-integers",
        "conflicting-explicit-total",
        "non-target-q3",
        "settled-no-injection",
    ),
)
def test_nonunique_partial_conflicting_and_nontarget_inputs_are_rollback_noops(
    snapshot_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rollback, current = _run_pair(monkeypatch, snapshot_factory())
    assert current == rollback
    assert all(not str(note).startswith(NOTE) for note in current["notes"])


@pytest.mark.parametrize(
    "qualification",
    (
        "official_random_warehouse",
        "postupdate_unknown_warehouse_source",
        "player_custom_warehouse",
    ),
)
def test_summary_preserves_source_pool_while_applying_the_hard_floor(
    qualification: str,
    tmp_path: Path,
) -> None:
    snapshot = _avg_snapshot(qualification=qualification)
    original = copy.deepcopy(snapshot)
    summary = summarize_snapshot(snapshot, snapshot_path=tmp_path / "snapshot.json")
    assert snapshot == original
    assert _quotes(summary["ahmed_ref"]) == [200_000] * 3
    assert summary["context"]["warehouse_source_qualification"] == qualification
    assert summary["context"]["warehouse_source"] == original["warehouse_source"]


def test_summary_api_and_tk_publish_the_same_hard_floor(tmp_path: Path) -> None:
    snapshot = _avg_snapshot()
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    summary = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - self-created loopback product contract
            f"http://127.0.0.1:{server.server_port}/api/latest", timeout=30
        ) as response:
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
    assert _quotes(summary["ahmed_ref"]) == [200_000] * 3
    assert _quotes(payload["ahmed_ref"]) == [200_000] * 3
    assert [
        tk_row["reference"][key]
        for key in ("conservative", "balanced", "aggressive")
    ] == ["200,000"] * 3
    assert f"{NOTE}:q5=200000" in tk_row["ref_v0"]["notes"]


_FRESH_PROCESS_RUNNER = r"""
import json
import sys
from pathlib import Path
root = Path.cwd()
sys.path.insert(0, str(root / "hero_ref" / "src"))
from ahmad_ref_engine import run_reference_engine
snapshot = json.load(sys.stdin)
result = run_reference_engine(snapshot, max_combos=int(sys.argv[1])).as_dict()
print(json.dumps({
    "raw": [result[key] for key in ("value_p25", "value_p50", "value_p75")],
    "quotes": [result[key] for key in ("conservative", "balanced", "aggressive")],
    "combo_count": result["combo_count"],
    "note": [note for note in result["notes"] if note.startswith(
        "hard_quality_avg_value_quote_floor"
    )],
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


@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_fresh_process_rollback_reproduces_the_underfloor_control(
    max_combos: int,
) -> None:
    snapshot = _avg_snapshot()
    rollback = _fresh_process(snapshot, max_combos, rollback=True)
    current = _fresh_process(snapshot, max_combos, rollback=False)
    assert rollback["raw"] == current["raw"] == [200_000] * 3
    assert rollback["quotes"] == [184_000] * 3
    assert current["quotes"] == [200_000] * 3
    assert rollback["combo_count"] == current["combo_count"] == 1
    assert rollback["note"] == []
    assert current["note"] == [f"{NOTE}:q5=200000"]


def _assert_floor_arm_noop(
    monkeypatch: pytest.MonkeyPatch,
    snapshot: dict,
) -> None:
    rollback, current = _run_pair(monkeypatch, snapshot)
    assert current == rollback
    assert all(not str(note).startswith(NOTE) for note in current["notes"])


@pytest.mark.parametrize("tier", ("q4", "q5"))
def test_aggregate_only_never_authorizes_a_public_floor(
    tier: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_floor_arm_noop(
        monkeypatch,
        _avg_snapshot(tier=tier, raw_rows=[]),
    )


@pytest.mark.parametrize("reverse", (False, True), ids=("forward", "reverse"))
def test_conflicting_aggregate_duplicates_are_order_independent_noops(
    reverse: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_aggregate_row("q5", 50_000), _aggregate_row("q5", 50_001)]
    if reverse:
        rows.reverse()
    _assert_floor_arm_noop(
        monkeypatch,
        _avg_snapshot(aggregate_rows=rows),
    )


@pytest.mark.parametrize("reverse", (False, True), ids=("forward", "reverse"))
def test_equal_aggregate_duplicates_deduplicate_without_order_dependence(
    reverse: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_aggregate_row("q5", 50_000), _aggregate_row("q5", 50_000)]
    if reverse:
        rows.reverse()
    rollback, current = _run_pair(
        monkeypatch,
        _avg_snapshot(aggregate_rows=rows),
    )
    assert max(_quotes(rollback)) < 200_000
    assert _quotes(current) == [200_000] * 3
    assert f"{NOTE}:q5=200000" in current["notes"]


@pytest.mark.parametrize(
    ("aggregate_rows", "raw_rows"),
    (
        ([_aggregate_row("q5", 50_000)], [_raw_row("q5", 50_001)]),
        ([_aggregate_row("q5", 50_001)], [_raw_row("q5", 50_000)]),
        ([_aggregate_row("q5", "50000")], [_raw_row("q5", 50_000)]),
        ([_aggregate_row("q5", float("nan"))], [_raw_row("q5", 50_000)]),
        ([_aggregate_row("q5", 50_000)], [_raw_row("q5", "50000")]),
        ([_aggregate_row("q5", 50_000)], [_raw_row("q5", float("inf"))]),
        (
            [{**_aggregate_row("q5", 50_000), "info_id": "200037"}],
            [_raw_row("q5", 50_000)],
        ),
        (
            [{**_aggregate_row("q5", 50_000), "info_id": 200036}],
            [_raw_row("q5", 50_000)],
        ),
        (
            [_aggregate_row("q5", 50_000)],
            [{**_raw_row("q5", 50_000), "info_id": "200037"}],
        ),
        ([_aggregate_row("q5", 50_000)], [{"info_id": 200037}]),
        ([_aggregate_row("q5", True)], [_raw_row("q5", True)]),
        ([_aggregate_row("q5", 50_000)], []),
        ([], [_raw_row("q5", 50_000)]),
    ),
    ids=(
        "mismatch-up",
        "mismatch-down",
        "aggregate-string",
        "aggregate-nan",
        "raw-string",
        "raw-inf",
        "aggregate-string-info-id",
        "aggregate-conflicting-info-id",
        "raw-string-info-id",
        "raw-missing-value",
        "bool-values",
        "aggregate-only",
        "raw-only",
    ),
)
def test_mismatch_and_malformed_public_ledgers_fail_closed(
    aggregate_rows: list[dict],
    raw_rows: list[dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_floor_arm_noop(
        monkeypatch,
        _avg_snapshot(aggregate_rows=aggregate_rows, raw_rows=raw_rows),
    )


@pytest.mark.parametrize(
    "raw_row",
    (
        {**_raw_row("q5", 50_000), "stale": True},
        {**_raw_row("q5", 50_000), "current": False},
        _raw_row("q5", 50_000, session_id="old-session"),
        {**_raw_row("q5", 50_000), "session_epoch": 6},
    ),
    ids=("stale", "not-current", "cross-session", "cross-epoch"),
)
def test_stale_and_cross_session_rows_are_not_current_authority(
    raw_row: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _avg_snapshot(raw_rows=[raw_row])
    snapshot["ui_contract"]["context"]["session_epoch"] = 7
    _assert_floor_arm_noop(monkeypatch, snapshot)


def test_conflicting_current_raw_duplicates_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_floor_arm_noop(
        monkeypatch,
        _avg_snapshot(
            raw_rows=[_raw_row("q5", 50_000), _raw_row("q5", 50_001)]
        ),
    )


def test_equal_current_raw_duplicates_deduplicate() -> None:
    snapshot = _avg_snapshot(
        raw_rows=[_raw_row("q5", 50_000), _raw_row("q5", 50_000)]
    )
    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    assert _quotes(result) == [200_000] * 3
    assert f"{NOTE}:q5=200000" in result["notes"]


def test_stale_and_cross_session_rows_do_not_poison_a_current_match() -> None:
    snapshot = _avg_snapshot(
        raw_rows=[
            _raw_row("q5", 50_000),
            {**_raw_row("q5", 50_001), "stale": True},
            _raw_row("q5", 50_002, session_id="old-session"),
        ]
    )
    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    assert _quotes(result) == [200_000] * 3
    assert f"{NOTE}:q5=200000" in result["notes"]


def test_public_numeric_fact_conflict_joins_the_same_aggregate_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _avg_snapshot()
    snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ] = [_aggregate_row("q5", 50_001)]
    _assert_floor_arm_noop(monkeypatch, snapshot)


@pytest.mark.parametrize("kind", ("aggregate-only", "mismatch"))
def test_summary_keeps_rejected_public_floor_arms_parent_like(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = (
        _avg_snapshot(raw_rows=[])
        if kind == "aggregate-only"
        else _avg_snapshot(raw_rows=[_raw_row("q5", 50_001)])
    )
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = summarize_snapshot(
        copy.deepcopy(snapshot), snapshot_path=tmp_path / f"{kind}-rollback.json"
    )
    monkeypatch.delenv(ROLLBACK)
    current = summarize_snapshot(
        copy.deepcopy(snapshot), snapshot_path=tmp_path / f"{kind}-current.json"
    )
    assert current["ahmed_ref"] == rollback["ahmed_ref"]
    assert all(
        not str(note).startswith(NOTE) for note in current["ahmed_ref"]["notes"]
    )


def test_public_half_ulp_interval_must_contain_one_integer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # At this float32 magnitude half an ULP per item is 0.001953125.
    # Four items around 200000.5 therefore admit no uniquely implied integer total.
    snapshot = _avg_snapshot(avg_value=50_000.125)
    rollback, current = _run_pair(monkeypatch, snapshot)
    assert current == rollback
    assert all(not str(note).startswith(NOTE) for note in current["notes"])


@pytest.mark.parametrize("phase", ("bidding", "manual"))
def test_nonpublic_structured_decimal_interval_semantics_are_preserved(
    phase: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _avg_snapshot(
        tier="q4",
        count=6,
        avg_value=33_333.5,
        quality_cells=12,
        phase=phase,
        aggregate_rows=[],
        raw_rows=[],
    )
    snapshot["structured_ref_inputs"]["avg_values"] = {"q4": 33_333.5}
    rollback, current = _run_pair(monkeypatch, snapshot)
    assert _quotes(rollback) == [184_001] * 3
    assert _quotes(current) == [200_001] * 3
    assert f"{NOTE}:q4=200001" in current["notes"]


def test_nonpublic_integer_display_interval_is_not_false_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _avg_snapshot(aggregate_rows=[], raw_rows=[])
    snapshot["structured_ref_inputs"]["avg_values"] = {"q5": 50_000}
    rollback, current = _run_pair(monkeypatch, snapshot)
    assert current == rollback
    assert all(not str(note).startswith(NOTE) for note in current["notes"])


def test_q4_and_q5_authorities_add_as_disjoint_quality_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _avg_snapshot(
        tier="q4", count=6, avg_value=33_333.5, quality_cells=12
    )
    snapshot["structured_ref_inputs"] = {
        "total_count": 10,
        "fixed_counts": {"q1": 0, "q3": 0, "q4": 6, "q5": 4, "q6": 0},
        "quality_cells": {"q4": 12, "q5": 9},
    }
    snapshot["ui_contract"]["constraints"]["public_info"]["public_avg_values"] = [
        _aggregate_row("q4", 33_333.5),
        _aggregate_row("q5", 50_000),
    ]
    snapshot["public_info_rows"] = [
        _raw_row("q4", 33_333.5),
        _raw_row("q5", 50_000),
    ]
    rollback, current = _run_pair(monkeypatch, snapshot)
    assert max(_quotes(rollback)) < 400_001
    assert _quotes(current) == [400_001] * 3
    assert f"{NOTE}:q4=200001,q5=200000" in current["notes"]


def test_avg_total_below_existing_same_tier_floor_emits_no_avg_floor_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _avg_snapshot()
    snapshot["structured_ref_inputs"]["quality_value_floors"] = {"q5": 210_000}
    rollback, current = _run_pair(monkeypatch, snapshot)
    assert _quotes(current) == _quotes(rollback)
    assert min(_quotes(current)) >= 210_000
    assert all(not str(note).startswith(NOTE) for note in current["notes"])


def _two_tier_snapshot() -> dict:
    snapshot = _avg_snapshot(
        tier="q4",
        count=6,
        avg_value=33_333.5,
        quality_cells=12,
    )
    snapshot["structured_ref_inputs"] = {
        "total_count": 10,
        "fixed_counts": {"q1": 0, "q3": 0, "q4": 6, "q5": 4, "q6": 0},
        "quality_cells": {"q4": 12, "q5": 9},
    }
    snapshot["ui_contract"]["constraints"]["public_info"][
        "public_avg_values"
    ] = [
        _aggregate_row("q4", 33_333.5),
        _aggregate_row("q5", 50_000),
    ]
    snapshot["public_info_rows"] = [
        _raw_row("q4", 33_333.5),
        _raw_row("q5", 50_000),
    ]
    return snapshot


def _legacy_resolvable_quality_alias_snapshot(
    tier: str,
    alias: str,
    *,
    reverse: bool,
    cross_container: bool,
) -> dict:
    count, avg_value, quality_cells = (
        (6, 33_333.5, 12) if tier == "q4" else (4, 50_000, 9)
    )
    snapshot = _avg_snapshot(
        tier=tier,
        count=count,
        avg_value=avg_value,
        quality_cells=quality_cells,
    )
    public_info = snapshot["ui_contract"]["constraints"]["public_info"]
    valid = _aggregate_row(tier, avg_value)
    malformed = {
        "kind": "avg_value",
        "quality": alias,
        "value": avg_value + 1,
    }
    if cross_container:
        first, second = [valid], [malformed]
        if reverse:
            first, second = second, first
        public_info["public_avg_values"] = first
        public_info["public_numeric_facts"] = second
    else:
        rows = [valid, malformed]
        if reverse:
            rows.reverse()
        public_info["public_avg_values"] = rows
    return snapshot


def _nonfinite_quality_alias_snapshot(
    tier: str,
    quality: float,
    *,
    reverse: bool,
    cross_container: bool,
) -> dict:
    count, avg_value, quality_cells = (
        (6, 33_333.5, 12) if tier == "q4" else (4, 50_000, 9)
    )
    snapshot = _avg_snapshot(
        tier=tier,
        count=count,
        avg_value=avg_value,
        quality_cells=quality_cells,
    )
    public_info = snapshot["ui_contract"]["constraints"]["public_info"]
    valid = _aggregate_row(tier, avg_value)
    malformed = {
        "kind": "avg_value",
        "quality": quality,
        "value": avg_value + 1,
    }
    if cross_container:
        first, second = [valid], [malformed]
        if reverse:
            first, second = second, first
        public_info["public_avg_values"] = first
        public_info["public_numeric_facts"] = second
    else:
        rows = [valid, malformed]
        if reverse:
            rows.reverse()
        public_info["public_avg_values"] = rows
    return snapshot


class _LegacyAliasStrSubclass(str):
    pass


class _LegacyAliasFloatSubclass(float):
    pass


@pytest.mark.parametrize(
    ("tier", "alias"),
    (
        ("q4", "4.0"),
        ("q4", "04"),
        ("q4", "bucket-q4"),
        ("q4", "quality/q4"),
        ("q5", "5.0"),
        ("q5", "05"),
        ("q5", "bucket-q5"),
        ("q5", "quality/q5"),
    ),
)
@pytest.mark.parametrize("reverse", (False, True), ids=("forward", "reverse"))
@pytest.mark.parametrize("cross_container", (False, True), ids=("same", "cross"))
def test_every_legacy_resolvable_alias_pollutes_the_same_claimed_tier(
    tier: str,
    alias: str,
    reverse: bool,
    cross_container: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _legacy_resolvable_quality_alias_snapshot(
        tier,
        alias,
        reverse=reverse,
        cross_container=cross_container,
    )

    assert extract_evidence(snapshot).hard_public_avg_values == {}
    _assert_floor_arm_noop(monkeypatch, snapshot)


@pytest.mark.parametrize(
    ("tier", "field", "alias"),
    (
        ("q4", "quality", _LegacyAliasStrSubclass("4.0")),
        ("q5", "quality", _LegacyAliasFloatSubclass(5.0)),
        ("q4", "semantic", _LegacyAliasStrSubclass("q4_avg_value")),
        ("q5", "semantic", _LegacyAliasStrSubclass("q5_avg_value")),
    ),
)
def test_untrusted_legacy_alias_scalar_cannot_escape_the_authority_ledger(
    tier: str,
    field: str,
    alias: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _legacy_resolvable_quality_alias_snapshot(
        tier,
        f"{tier[1:]}.0",
        reverse=False,
        cross_container=False,
    )
    malformed = {"kind": "avg_value", field: alias, "value": 50_001}
    public_info = snapshot["ui_contract"]["constraints"]["public_info"]
    public_info["public_avg_values"] = [
        _aggregate_row(tier, 33_333.5 if tier == "q4" else 50_000),
        malformed,
    ]

    assert extract_evidence(snapshot).hard_public_avg_values == {}
    _assert_floor_arm_noop(monkeypatch, snapshot)


@pytest.mark.parametrize("tier", ("q4", "q5"))
@pytest.mark.parametrize(
    "quality",
    (float("nan"), float("inf"), float("-inf")),
    ids=("nan", "positive-inf", "negative-inf"),
)
@pytest.mark.parametrize("reverse", (False, True), ids=("forward", "reverse"))
@pytest.mark.parametrize("cross_container", (False, True), ids=("same", "cross"))
def test_nonfinite_quality_alias_is_total_and_targeted_invalid(
    tier: str,
    quality: float,
    reverse: bool,
    cross_container: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _nonfinite_quality_alias_snapshot(
        tier,
        quality,
        reverse=reverse,
        cross_container=cross_container,
    )

    assert extract_evidence(snapshot).hard_public_avg_values == {}
    _assert_floor_arm_noop(monkeypatch, snapshot)


@pytest.mark.parametrize("reverse", (False, True), ids=("forward", "reverse"))
@pytest.mark.parametrize("cross_container", (False, True), ids=("same", "cross"))
def test_conflicting_alias_row_invalidates_every_claimed_tier(
    reverse: bool,
    cross_container: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _two_tier_snapshot()
    public_info = snapshot["ui_contract"]["constraints"]["public_info"]
    conflicting = {
        "info_id": 200036,
        "semantic": "q5_avg_value",
        "kind": "avg_value",
        "quality": 5,
        "value": 50_001,
    }
    if cross_container:
        public_info["public_numeric_facts"] = [conflicting]
    elif reverse:
        public_info["public_avg_values"].insert(0, conflicting)
    else:
        public_info["public_avg_values"].append(conflicting)
    if cross_container and reverse:
        public_info["public_avg_values"], public_info["public_numeric_facts"] = (
            public_info["public_numeric_facts"],
            public_info["public_avg_values"],
        )

    evidence = extract_evidence(snapshot)
    assert evidence.hard_public_avg_values == {}
    _assert_floor_arm_noop(monkeypatch, snapshot)


@pytest.mark.parametrize("quality", (999, "5", True, 5.9))
def test_malformed_present_quality_alias_cannot_be_treated_as_absent(
    quality: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _aggregate_row("q5", 50_000)
    row["quality"] = quality
    snapshot = _avg_snapshot(aggregate_rows=[row])

    assert extract_evidence(snapshot).hard_public_avg_values == {}
    _assert_floor_arm_noop(monkeypatch, snapshot)


@pytest.mark.parametrize(
    ("alias", "malformed"),
    (
        ("info_id", 999),
        ("info_id", 200037.0),
        ("semantic", "q5_average"),
        ("semantic", 5),
        ("kind", "AVG_VALUE"),
        ("kind", 5),
    ),
)
def test_every_present_authority_alias_is_strictly_validated(
    alias: str,
    malformed: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _aggregate_row("q5", 50_000)
    row[alias] = malformed
    snapshot = _avg_snapshot(aggregate_rows=[row])

    assert extract_evidence(snapshot).hard_public_avg_values == {}
    _assert_floor_arm_noop(monkeypatch, snapshot)


@pytest.mark.parametrize("reverse", (False, True), ids=("after", "before"))
def test_sanitize_before_authority_keeps_malformed_raw_sibling_rejected(
    reverse: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _avg_snapshot()
    malformed = {
        "info_id": "200037",
        "value": 50_000,
        "session_id": "session-a",
    }
    if reverse:
        snapshot["public_info_rows"].insert(0, malformed)
    else:
        snapshot["public_info_rows"].append(malformed)
    prepared = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))

    assert extract_evidence(prepared).hard_public_avg_values == {}
    _assert_floor_arm_noop(monkeypatch, prepared)


@pytest.mark.parametrize("identity", ("session_id", "session_epoch", "epoch"))
def test_present_raw_identity_requires_corresponding_current_anchor(
    identity: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _avg_snapshot()
    context = snapshot["ui_contract"]["context"]
    context.pop("session_id", None)
    snapshot.pop("session_id", None)
    value = "orphan" if identity == "session_id" else 8
    snapshot["public_info_rows"][0].pop("session_id", None)
    snapshot["public_info_rows"][0][identity] = value

    assert extract_evidence(snapshot).hard_public_avg_values == {}
    _assert_floor_arm_noop(monkeypatch, snapshot)


@pytest.mark.parametrize("identity", ("session_id", "session_epoch", "epoch"))
def test_empty_string_identity_anchor_is_not_current_authority(
    identity: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _avg_snapshot()
    context = snapshot["ui_contract"]["context"]
    context.pop("session_id", None)
    snapshot["public_info_rows"][0].pop("session_id", None)
    context[identity] = ""
    snapshot["public_info_rows"][0][identity] = ""

    assert extract_evidence(snapshot).hard_public_avg_values == {}
    _assert_floor_arm_noop(monkeypatch, snapshot)


def test_identity_free_monitor_row_remains_authoritative() -> None:
    snapshot = _avg_snapshot()
    snapshot["public_info_rows"][0].pop("session_id")
    evidence = extract_evidence(snapshot)
    result = run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.hard_public_avg_values == {"q5": 50_000.0}
    assert _quotes(result) == [200_000] * 3
    assert f"{NOTE}:q5=200000" in result["notes"]


def test_tampered_prepared_authority_marker_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_reference_engine_snapshot(_avg_snapshot())
    marker_key = next(
        key for key in prepared if key.endswith("hard_public_avg_value_marker")
    )
    prepared[marker_key] = "0" * 64

    assert extract_evidence(prepared).hard_public_avg_values == {}
    _assert_floor_arm_noop(monkeypatch, prepared)


def test_rejected_raw_sibling_remains_parent_like_through_summary_api_and_tk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _avg_snapshot()
    snapshot["public_info_rows"].append(
        {"info_id": "200037", "value": 50_000, "session_id": "session-a"}
    )
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = summarize_snapshot(copy.deepcopy(snapshot), snapshot_path=snapshot_path)
    monkeypatch.delenv(ROLLBACK)
    summary = summarize_snapshot(copy.deepcopy(snapshot), snapshot_path=snapshot_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - self-created loopback product contract
            f"http://127.0.0.1:{server.server_port}/api/latest", timeout=30
        ) as response:
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

    assert summary["ahmed_ref"] == rollback["ahmed_ref"]
    assert payload["ahmed_ref"] == rollback["ahmed_ref"]
    assert all(not str(note).startswith(NOTE) for note in tk_row["ref_v0"]["notes"])


@pytest.mark.parametrize(
    ("tier", "alias"),
    (("q4", "4.0"), ("q5", "5.0")),
)
def test_legacy_resolvable_alias_is_rejected_through_summary_api_and_tk(
    tier: str,
    alias: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _legacy_resolvable_quality_alias_snapshot(
        tier,
        alias,
        reverse=False,
        cross_container=True,
    )
    snapshot_path = tmp_path / f"{tier}-alias.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = summarize_snapshot(copy.deepcopy(snapshot), snapshot_path=snapshot_path)
    monkeypatch.delenv(ROLLBACK)
    summary = summarize_snapshot(copy.deepcopy(snapshot), snapshot_path=snapshot_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(
            ServerConfig(project_root=tmp_path, snapshot=Path(snapshot_path.name))
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - self-created loopback product contract
            f"http://127.0.0.1:{server.server_port}/api/latest", timeout=30
        ) as response:
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

    assert summary["ahmed_ref"] == rollback["ahmed_ref"]
    assert payload["ahmed_ref"] == rollback["ahmed_ref"]
    assert all(not str(note).startswith(NOTE) for note in tk_row["ref_v0"]["notes"])


@pytest.mark.parametrize("tier", ("q4", "q5"))
@pytest.mark.parametrize(
    "quality",
    (float("nan"), float("inf"), float("-inf")),
    ids=("nan", "positive-inf", "negative-inf"),
)
def test_nonfinite_alias_is_rejected_through_summary_api_and_tk(
    tier: str,
    quality: float,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _nonfinite_quality_alias_snapshot(
        tier,
        quality,
        reverse=False,
        cross_container=True,
    )
    snapshot_path = tmp_path / f"{tier}-nonfinite.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = summarize_snapshot(copy.deepcopy(snapshot), snapshot_path=snapshot_path)
    monkeypatch.delenv(ROLLBACK)
    summary = summarize_snapshot(copy.deepcopy(snapshot), snapshot_path=snapshot_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(
            ServerConfig(project_root=tmp_path, snapshot=Path(snapshot_path.name))
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - self-created loopback product contract
            f"http://127.0.0.1:{server.server_port}/api/latest", timeout=30
        ) as response:
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

    assert summary["ahmed_ref"] == rollback["ahmed_ref"]
    assert payload["ahmed_ref"] == rollback["ahmed_ref"]
    assert all(not str(note).startswith(NOTE) for note in tk_row["ref_v0"]["notes"])


@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_aisha_4501_default_and_rollback_match_exact_parent_full_wire(
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _aisha_4501_snapshot()
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = run_reference_engine(
        copy.deepcopy(snapshot), max_combos=max_combos
    ).as_dict()
    monkeypatch.delenv(ROLLBACK)
    current = run_reference_engine(
        copy.deepcopy(snapshot), max_combos=max_combos
    ).as_dict()
    assert rollback["q5_total_cells_value_summary"] == {}
    parent_wire = dict(rollback)
    parent_wire.pop("q5_total_cells_value_summary")
    canonical = json.dumps(
        parent_wire,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    assert current == rollback
    assert hashlib.sha256(canonical).hexdigest() == (
        "8d9bf69e9c4d87dd6e7e5cc4a79c5ec29462dd78e41eadcb92d924a0baf8602f"
    )
    assert rollback["combo_count"] == 2
    assert _quotes(rollback) == [1_156_852, 1_453_675, 1_553_314]
