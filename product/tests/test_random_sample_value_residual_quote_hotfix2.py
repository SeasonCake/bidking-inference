from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
import threading
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest

from ahmad_live_panel_server import ServerConfig, make_handler, summarize_snapshot
from ahmad_ref_engine import (
    _public_random_value_floors,
    _random_value_floor,
    extract_evidence,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from ahmad_tk_overlay import _summary_diagnostic_row
from test_ahmed_2404_q4_arm_risk_ceiling_0_3_1 import _snapshot_2404
from test_ahmed_2510_zero_red_tail_risk_floor_0_3_1 import _snapshot_2510


ROOT = Path(__file__).resolve().parents[1]
ROLLBACK = "BIDKING_DISABLE_RANDOM_SAMPLE_VALUE_RESIDUAL_QUOTE_FLOOR"
NOTE = "random_sample_value_residual_quote_transform"


def _snapshot(
    *,
    hero: str = "ahmed",
    phase: str = "bidding",
    rows: list[dict[str, object]] | None = None,
    quality_values: dict[str, int] | None = None,
    qualification: str | None = None,
) -> dict[str, object]:
    if rows is None:
        rows = [
            {
                "info_id": 200033,
                "semantic": "random_9_avg_value",
                "kind": "random_avg_value",
                "sample_count": 9,
                "value": 832_548.4,
            }
        ]
    source = None
    if qualification is not None:
        source = {
            "qualification": qualification,
            "official_distribution_eligible": qualification
            == "official_random_warehouse",
            "quote_policy": "advisory_only_no_clamp",
            "quarantine_required": False,
        }
    snapshot = {
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": 2401,
                "phase": phase,
                "round": 1,
            },
            "constraints": {
                "public_info": {
                    "public_random_avg_values": copy.deepcopy(rows),
                }
            },
        },
        "structured_ref_inputs": {
            "total_count": 33,
            "quality_values": dict(quality_values or {}),
        },
    }
    if source is not None:
        snapshot["warehouse_source_qualification"] = qualification
        snapshot["warehouse_source"] = copy.deepcopy(source)
        snapshot["ui_contract"]["context"][
            "warehouse_source_qualification"
        ] = qualification
        snapshot["ui_contract"]["context"]["warehouse_source"] = copy.deepcopy(source)
        snapshot["ui_contract"]["source"] = {
            "warehouse_source_qualification": qualification,
            "warehouse_source": copy.deepcopy(source),
        }
    return snapshot


def _run(snapshot: dict[str, object], *, max_combos: int = 8_000) -> dict:
    prepared = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    return run_reference_engine(prepared, max_combos=max_combos).as_dict()


def _quotes(result: dict) -> list[int]:
    return [
        result[key]
        for key in ("conservative", "balanced", "aggressive")
    ]


def _stable(result: dict) -> dict:
    return {
        key: result[key]
        for key in (
            "combo_count",
            "value_p25",
            "value_p50",
            "value_p75",
            "quality_count_ranges",
            "quality_cells_ranges",
            "quality_count_candidates",
            "quality_cells_candidates",
            "quality_count_cells_candidates",
            "quality_lock",
            "red_value_range",
            "evidence",
        )
    }


@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_random_n_average_value_is_not_discounted_by_final_quote_transform(
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    hard_floor = math.ceil(9 * 832_548.4)

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(snapshot, max_combos=max_combos)
    monkeypatch.delenv(ROLLBACK)
    current = _run(snapshot, max_combos=max_combos)

    assert min(_quotes(rollback)) < hard_floor
    assert min(_quotes(current)) >= hard_floor
    assert _stable(current) == _stable(rollback)
    assert any(
        str(note).startswith(f"{NOTE}:{hard_floor}:")
        for note in current["notes"]
    )


def test_random_sample_floor_uses_max_and_does_not_add_overlapping_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "kind": "random_avg_value",
            "semantic": "random_3_avg_value",
            "sample_count": 3,
            "value": 500_000,
        },
        {
            "kind": "random_avg_value",
            "semantic": "random_9_avg_value",
            "sample_count": 9,
            "value": 300_000,
        },
    ]
    monkeypatch.delenv(ROLLBACK, raising=False)
    result = _run(_snapshot(rows=rows, quality_values={"q6": 2_000_000}))

    assert min(_quotes(result)) >= 2_700_000
    assert any(
        str(note).startswith(f"{NOTE}:2700000:")
        for note in result["notes"]
    )
    assert not any(f"{NOTE}:4700000:" in str(note) for note in result["notes"])


def test_larger_exact_quality_total_wins_without_adding_random_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ROLLBACK, raising=False)
    result = _run(_snapshot(quality_values={"q6": 8_000_000}))

    assert min(_quotes(result)) >= 8_000_000
    assert any(
        str(note).startswith("known_quality_value_residual_quote_transform:8000000:")
        for note in result["notes"]
    )
    assert not any(str(note).startswith(f"{NOTE}:") for note in result["notes"])


@pytest.mark.parametrize("sample_count", (3, 6, 9, 12))
def test_all_product_random_sample_sizes_protect_their_subset_total(
    sample_count: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    avg_value = 50_000.0
    rows = [
        {
            "kind": "random_avg_value",
            "semantic": f"random_{sample_count}_avg_value",
            "sample_count": sample_count,
            "value": avg_value,
        }
    ]
    monkeypatch.delenv(ROLLBACK, raising=False)
    result = _run(_snapshot(rows=rows))

    floor = sample_count * avg_value
    assert min(_quotes(result)) >= floor
    assert any(
        str(note).startswith(f"{NOTE}:{round(floor)}:")
        for note in result["notes"]
    )


@pytest.mark.parametrize("sample_count", (3, 6, 9, 12))
def test_absent_count_uses_only_the_exact_builtin_semantic_sample_size(
    sample_count: int,
) -> None:
    row = {
        "kind": "random_avg_value",
        "semantic": f"random_{sample_count}_avg_value",
        "value": 12_345.5,
    }

    assert _public_random_value_floors(
        {"public_random_avg_values": [row]}
    ) == ((sample_count, sample_count * 12_345.5),)


@pytest.mark.parametrize(
    "semantic",
    (
        "random_03_avg_value",
        "random_06_avg_value",
        "random_09_avg_value",
        "random_012_avg_value",
        "prefix_random_9_avg_value",
        "random_9_avg_value_suffix",
        "random_９_avg_value",
    ),
)
def test_absent_count_rejects_noncanonical_semantic_sample_sizes(
    semantic: str,
) -> None:
    assert _public_random_value_floors(
        {
            "public_random_avg_values": [
                {
                    "kind": "random_avg_value",
                    "semantic": semantic,
                    "value": 12_345.5,
                }
            ]
        }
    ) == ()


def test_present_invalid_count_never_falls_back_or_calls_numeric_protocols() -> None:
    class BombCount:
        calls = 0

        def __int__(self) -> int:
            type(self).calls += 1
            return 9

        def __float__(self) -> float:
            type(self).calls += 1
            return 9.0

        def __str__(self) -> str:
            type(self).calls += 1
            return "9"

    class IntSubclass(int):
        pass

    class FloatSubclass(float):
        pass

    invalid_counts = (
        True,
        3.0,
        3.9,
        6.5,
        9.9,
        12.1,
        "9",
        IntSubclass(9),
        FloatSubclass(9.0),
        float("nan"),
        float("inf"),
        BombCount(),
    )
    rows = [
        {
            "kind": "random_avg_value",
            "semantic": "random_9_avg_value",
            "sample_count": sample_count,
            "value": 832_548.4,
        }
        for sample_count in invalid_counts
    ]

    assert _public_random_value_floors(
        {"public_random_avg_values": rows}
    ) == ()
    assert BombCount.calls == 0


def test_present_invalid_average_never_calls_numeric_protocols() -> None:
    class BombValue:
        calls = 0

        def __int__(self) -> int:
            type(self).calls += 1
            return 832_548

        def __float__(self) -> float:
            type(self).calls += 1
            return 832_548.4

        def __str__(self) -> str:
            type(self).calls += 1
            return "832548.4"

    class IntSubclass(int):
        pass

    class FloatSubclass(float):
        pass

    invalid_values = (
        True,
        "832548.4",
        IntSubclass(832_548),
        FloatSubclass(832_548.4),
        float("nan"),
        float("inf"),
        float("-inf"),
        10**400,
        BombValue(),
    )
    rows = [
        {
            "kind": "random_avg_value",
            "semantic": "random_9_avg_value",
            "sample_count": 9,
            "value": value,
        }
        for value in invalid_values
    ]

    assert _public_random_value_floors(
        {"public_random_avg_values": rows}
    ) == ()
    assert BombValue.calls == 0


def test_random_sample_terminal_k_rounds_up_and_quotes_are_monotone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ROLLBACK, raising=False)
    outputs: list[list[int]] = []
    for average in (2_500_000.1, 2_600_000.1, 2_700_000.1):
        result = _run(
            _snapshot(
                rows=[
                    {
                        "kind": "random_avg_value",
                        "semantic": "random_3_avg_value",
                        "sample_count": 3,
                        "value": average,
                    }
                ]
            )
        )
        floor = math.ceil(3 * average)
        assert min(_quotes(result)) >= floor
        assert any(
            str(note).startswith(f"{NOTE}:{floor}:")
            for note in result["notes"]
        )
        outputs.append(_quotes(result))

    for lower, higher in zip(outputs, outputs[1:]):
        assert all(after >= before for before, after in zip(lower, higher))


def test_exact_builtin_int_product_is_not_rounded_down_through_float() -> None:
    average = 2**53 + 1
    floor = 3 * average
    rows = [
        {
            "kind": "random_avg_value",
            "semantic": "random_3_avg_value",
            "sample_count": 3,
            "value": average,
        }
    ]

    assert _public_random_value_floors(
        {"public_random_avg_values": rows}
    ) == ((3, floor),)
    result = _run(_snapshot(rows=rows))
    assert min(_quotes(result)) >= floor
    assert any(
        str(note).startswith(f"{NOTE}:{floor}:")
        for note in result["notes"]
    )


@pytest.mark.parametrize(
    ("sample_count", "value"),
    (
        (0, 50_000.0),
        (5, 50_000.0),
        (9, 0.0),
        (9, -1.0),
        (12, 1e308),
    ),
)
def test_invalid_or_nonfinite_random_sample_values_fail_closed(
    sample_count: int,
    value: float,
) -> None:
    rows = [
        {
            "kind": "random_avg_value",
            "semantic": f"random_{sample_count}_avg_value",
            "sample_count": sample_count,
            "value": value,
        }
    ]

    assert _run(_snapshot(rows=rows)) == _run(_snapshot(rows=[]))


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_present_nonfinite_random_sample_value_conflicts_without_exception(
    value: float,
) -> None:
    result = _run(
        _snapshot(
            rows=[
                {
                    "kind": "random_avg_value",
                    "semantic": "random_9_avg_value",
                    "sample_count": 9,
                    "value": value,
                }
            ]
        )
    )

    assert _quotes(result) == [None, None, None]
    assert result["status"] != "ok"
    assert not any(
        "public_random_avg_value_floor" in str(note)
        for note in result.get("notes", ())
    )


def test_internal_random_floor_rejects_unbounded_or_protocol_values_without_magic() -> None:
    class BombNumber:
        def __float__(self) -> float:
            raise AssertionError("user conversion must not run")

    evidence = extract_evidence(prepare_reference_engine_snapshot(_snapshot()))
    malformed = replace(
        evidence,
        random_value_floors=(
            (9, 10**400),
            (9, BombNumber()),
            (True, 1_000_000),
            (5, 1_000_000),
        ),
    )

    assert _random_value_floor(malformed) is None


def test_no_random_sample_value_is_byte_identical_to_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(rows=[])

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(snapshot)
    monkeypatch.delenv(ROLLBACK)
    current = _run(snapshot)

    assert current == rollback


def test_settled_random_sample_value_is_byte_identical_to_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(phase="settled")

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(snapshot)
    monkeypatch.delenv(ROLLBACK)
    current = _run(snapshot)

    assert current == rollback


def test_other_hero_gets_total_floor_without_new_red_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "kind": "random_avg_value",
            "semantic": "random_9_avg_value",
            "sample_count": 9,
            "value": 120_000.0,
        }
    ]
    snapshot = _snapshot(hero="ethan", rows=rows)

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(snapshot)
    monkeypatch.delenv(ROLLBACK)
    current = _run(snapshot)

    assert min(_quotes(rollback)) < 1_080_000
    assert min(_quotes(current)) >= 1_080_000
    assert _stable(current) == _stable(rollback)
    assert not any(
        str(note).startswith("random_avg_red_anchor_q6_value_floor")
        for note in current["notes"]
    )


def test_explicit_zero_q6_count_does_not_cancel_low_random_subset_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "kind": "random_avg_value",
            "semantic": "random_9_avg_value",
            "sample_count": 9,
            "value": 50_000.0,
        }
    ]
    snapshot = _snapshot(rows=rows)
    snapshot["structured_ref_inputs"]["fixed_counts"] = {"q6": 0}
    monkeypatch.delenv(ROLLBACK, raising=False)
    result = _run(snapshot)

    assert min(_quotes(result)) >= 450_000


def test_random_sample_k_protects_zero_red_tail_without_moving_risk_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2510()
    snapshot["ui_contract"]["constraints"]["public_info"] = {
        "public_random_avg_values": [
            {
                "kind": "random_avg_value",
                "semantic": "random_12_avg_value",
                "sample_count": 12,
                "value": 40_000.0,
            }
        ]
    }

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(snapshot)
    monkeypatch.delenv(ROLLBACK)
    current = _run(snapshot)

    assert _stable(current) == _stable(rollback)
    for key in (
        "probability",
        "minimum_branch_probability",
        "selected_branch",
        "branches",
        "warehouse_qualification",
        "official_distribution_eligible",
        "quote_policy",
    ):
        assert current["risk_floor"][key] == rollback["risk_floor"][key]
    assert all(
        current["risk_floor"]["tail_quotes"][key]
        >= rollback["risk_floor"]["tail_quotes"][key]
        for key in ("conservative", "balanced", "aggressive")
    )
    assert min(current["risk_floor"]["tail_quotes"].values()) >= 480_000


def test_random_sample_k_preserves_q4_risk_branch_identity_and_raw_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2404()
    snapshot["ui_contract"]["constraints"]["public_info"] = {
        "public_random_avg_values": [
            {
                "kind": "random_avg_value",
                "semantic": "random_3_avg_value",
                "sample_count": 3,
                "value": 150_000.0,
            }
        ]
    }

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(snapshot)
    monkeypatch.delenv(ROLLBACK)
    current = _run(snapshot)

    assert _stable(current) == _stable(rollback)
    for key in (
        "minimum_branch_probability",
        "warehouse_qualification",
        "official_distribution_eligible",
        "quote_policy",
    ):
        assert current["risk_ceiling"][key] == rollback["risk_ceiling"][key]
    for current_branch, rollback_branch in zip(
        current["risk_ceiling"]["branches"],
        rollback["risk_ceiling"]["branches"],
    ):
        for key in (
            "quality",
            "count",
            "cells",
            "probability",
            "eligible",
            "value_p25",
            "value_p50",
            "value_p75",
        ):
            assert current_branch[key] == rollback_branch[key]
    selected = current["risk_ceiling"]["selected_branch"]
    matching = next(
        branch
        for branch in current["risk_ceiling"]["branches"]
        if branch["count"] == selected["count"]
        and branch["cells"] == selected["cells"]
    )
    assert selected == matching
    assert current["risk_ceiling"]["value"] == selected["aggressive"]
    assert current["risk_ceiling"]["value"] > rollback["risk_ceiling"]["value"]


@pytest.mark.parametrize(
    "qualification",
    (
        "official_random_warehouse",
        "postupdate_unknown_warehouse_source",
        "player_custom_warehouse",
    ),
)
def test_source_pool_is_preserved(
    qualification: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(qualification=qualification)
    original = copy.deepcopy(snapshot)
    monkeypatch.delenv(ROLLBACK, raising=False)

    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / f"{qualification}.json",
    )

    assert snapshot == original
    assert summary["context"]["warehouse_source_qualification"] == qualification
    assert min(_quotes(summary["ahmed_ref"])) >= math.ceil(9 * 832_548.4)


def test_summary_loopback_api_and_tk_consume_the_same_protected_quotes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    monkeypatch.delenv(ROLLBACK, raising=False)

    summary = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - loopback product contract
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=10,
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
    expected = _quotes(summary["ahmed_ref"])
    assert _quotes(payload["ahmed_ref"]) == expected
    assert [
        tk_row["reference"][key]
        for key in ("conservative", "balanced", "aggressive")
    ] == [f"{value:,}" for value in expected]


_FRESH_PROCESS_RUNNER = r"""
import copy
import json
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root / "hero_ref" / "src"))
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine

snapshot = json.load(sys.stdin)
result = run_reference_engine(
    prepare_reference_engine_snapshot(copy.deepcopy(snapshot)),
    max_combos=int(sys.argv[1]),
).as_dict()
print(json.dumps({
    "quotes": [result[key] for key in ("conservative", "balanced", "aggressive")],
    "stable": {
        key: result[key]
        for key in (
            "value_p25", "value_p50", "value_p75", "quality_count_ranges",
            "quality_cells_ranges", "quality_count_cells_candidates", "quality_lock",
        )
    },
}, sort_keys=True))
"""


def _fresh(snapshot: dict[str, object], *, max_combos: int, rollback: bool) -> dict:
    env = os.environ.copy()
    if rollback:
        env[ROLLBACK] = "1"
    else:
        env.pop(ROLLBACK, None)
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and local code
        [sys.executable, "-B", "-c", _FRESH_PROCESS_RUNNER, str(max_combos)],
        cwd=ROOT,
        env=env,
        input=json.dumps(snapshot),
        text=True,
        capture_output=True,
        check=True,
        timeout=180,
    )
    assert completed.stderr == ""
    return json.loads(completed.stdout)


@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_fresh_process_rollback_only_changes_terminal_quotes(max_combos: int) -> None:
    snapshot = _snapshot()
    current = _fresh(snapshot, max_combos=max_combos, rollback=False)
    rollback = _fresh(snapshot, max_combos=max_combos, rollback=True)

    assert current["stable"] == rollback["stable"]
    assert min(current["quotes"]) >= math.ceil(9 * 832_548.4)
    assert min(rollback["quotes"]) < math.ceil(9 * 832_548.4)
