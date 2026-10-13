"""Hotfix3 complete physical support for count arms above five.

The regression starts with Ahmed 5609's qualified bidding prefixes but keeps
the implementation contract generic: Float32 average-cells and catalog-backed
same-price averages create physical equations, never a count-one shortcut.
The normal warehouse posterior ranks every complete joint arm; publication
must retain those arms or fail closed when the caller cap truncates them.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import threading
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_live_panel_server as panel  # noqa: E402
import ahmad_ref_engine as engine  # noqa: E402
import ahmad_tk_overlay as overlay  # noqa: E402
from ahmad_live_panel_server import ServerConfig, make_handler  # noqa: E402


Q5_AVG = 4.166666507720947
ROLLBACK = engine.COMPLETE_GT5_JOINT_SUPPORT_DISABLE
NOTE = engine.COMPLETE_GT5_JOINT_SUPPORT_NOTE
INCOMPLETE_NOTE = engine.COMPLETE_GT5_JOINT_SUPPORT_INCOMPLETE_NOTE
STATIC_DATA_SHA256 = (
    "7543b4aa16addc73dfaaaf3c6df9bc0f6662bd83458de110a16d04ff9f2f9676"
)


@pytest.fixture(scope="module", autouse=True)
def _require_approved_package_data() -> None:
    """Classify absent copy-only inputs as setup, never as a product result."""
    manifest = json.loads((ROOT / "scripts" / "package_data_manifest.json").read_text("utf-8"))
    fingerprints = json.loads(
        (ROOT / "scripts" / "package_data_fingerprints.json").read_text("utf-8")
    )["files"]
    failures: list[str] = []
    for relative in manifest["files"]:
        path = ROOT / relative
        expected = fingerprints[relative]
        if not path.is_file():
            failures.append(f"missing:{relative}")
            continue
        payload = path.read_bytes()
        actual = hashlib.sha256(payload).hexdigest()
        if len(payload) != expected["size"] or actual != expected["sha256"]:
            failures.append(f"wrong-sha:{relative}:{actual}")
    if failures:
        pytest.fail("input-assembly/package-data: " + ",".join(failures))


@pytest.fixture(scope="module", autouse=True)
def _require_workflow_static_data() -> None:
    """Keep posterior assertions on the approved workflow-only price input."""
    path = engine._resolve_static_data()
    if not path.is_file():
        pytest.fail(f"input-assembly/workflow-only: missing:{path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != STATIC_DATA_SHA256:
        pytest.fail(
            "input-assembly/workflow-only: "
            f"wrong-sha:{path}:{actual}"
        )


def _snapshot(
    stage: str = "seq19",
    *,
    hero: str = "ahmed",
    map_id: int = 5609,
    phase: str = "bidding",
) -> dict:
    avg_cells = {"q5": Q5_AVG}
    quality_cells: dict[str, int] = {}
    if stage in {"seq14", "seq17", "seq19"}:
        avg_cells["q1"] = 1.5555555820465088
    if stage in {"seq17", "seq19"}:
        avg_cells["q4"] = 2.200000047683716
    if stage == "seq19":
        quality_cells["q4"] = 11
    return {
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": map_id,
                "phase": phase,
                "round": 3,
            },
        },
        "structured_ref_inputs": {
            "total_count": 38,
            "counts": {"q3": 12},
            "avg_cells": avg_cells,
            "quality_cells": quality_cells,
        },
        "skill_reveals": [
            {"hero_id": 204, "skill_id": 1002041, "result": Q5_AVG},
        ],
    }


EXPECTED = {
    "seq9": {
        "q5": {6, 12, 18, 24},
        "q5_range": [6, 6, 24],
        "q5_cells": [25, 25, 100],
        "q6": set(range(0, 8)),
        "q6_range": [0, 3, 7],
        "quotes": [606_502, 796_782, 979_317],
    },
    "seq14": {
        "q5": {6, 12},
        "q5_range": [6, 6, 12],
        "q5_cells": [25, 25, 50],
        "q6": set(range(0, 8)),
        "q6_range": [0, 3, 7],
        "quotes": [650_560, 814_893, 979_227],
    },
    "seq17": {
        "q5": {6, 12},
        "q5_range": [6, 6, 12],
        "q5_cells": [25, 25, 50],
        "q6": {0, 1, 6},
        "q6_range": [0, 1, 6],
        "quotes": [444_087, 526_259, 1_212_078],
    },
    "seq19": {
        "q5": {6, 12},
        "q5_range": [6, 6, 12],
        "q5_cells": [25, 25, 50],
        "q6": {0, 6},
        "q6_range": [0, 6, 6],
        "quotes": [1_212_078, 1_307_366, 1_402_654],
    },
}


def _candidate_values(result: dict, key: str) -> set[int]:
    return {
        int(row["value"])
        for row in result["quality_count_candidates"].get(key, [])
    }


def _joint_counts(result: dict, key: str) -> set[int]:
    return {
        int(row["count"])
        for row in result["quality_count_cells_candidates"].get(key, [])
    }


@pytest.mark.parametrize("stage", tuple(EXPECTED))
@pytest.mark.parametrize("max_combos", (8_000, 50_000))
@pytest.mark.parametrize("prepared", (False, True))
def test_5609_prefix_publishes_the_complete_joint_support(
    stage: str,
    max_combos: int,
    prepared: bool,
) -> None:
    snapshot = _snapshot(stage)
    if prepared:
        snapshot = engine.prepare_reference_engine_snapshot(snapshot)
    result = engine.run_reference_engine(snapshot, max_combos=max_combos).as_dict()
    expected = EXPECTED[stage]

    assert result["status"] in {"ok", "count_prior"}
    assert result["combo_count"] > 0
    assert _candidate_values(result, "q5") == expected["q5"]
    assert _joint_counts(result, "q5") == expected["q5"]
    assert result["quality_count_ranges"]["q5"] == expected["q5_range"]
    assert result["quality_cells_ranges"]["q5"] == expected["q5_cells"]
    assert _candidate_values(result, "q6") == expected["q6"]
    assert _joint_counts(result, "q6") == expected["q6"]
    assert result["quality_count_ranges"]["q6"] == expected["q6_range"]
    assert result["red_count_range"] == expected["q6_range"]
    assert result["red_cells_range"] == result["quality_cells_ranges"]["q6"]
    assert result["quality_lock"]["q5"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert result["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert [result[key] for key in ("conservative", "balanced", "aggressive")] == (
        expected["quotes"]
    )
    assert NOTE in result["notes"]
    assert engine.AHMED_Q5_AVG_OVERBOUND_FAIL_OPEN_NOTE not in result["notes"]


@pytest.mark.parametrize(
    ("stage", "cap", "published_combos"),
    (
        ("seq9", 1, None),
        ("seq9", 2, None),
        ("seq9", 3, None),
        ("seq9", 4, None),
        ("seq17", 1, None),
        ("seq17", 2, None),
        ("seq17", 3, 3),
        ("seq17", 4, 3),
        ("seq19", 1, None),
        ("seq19", 2, 2),
        ("seq19", 3, 2),
        ("seq19", 4, 2),
    ),
)
def test_cap_plus_one_proves_support_before_publication(
    stage: str,
    cap: int,
    published_combos: int | None,
) -> None:
    result = engine.run_reference_engine(_snapshot(stage), max_combos=cap).as_dict()
    if published_combos is not None:
        assert result["status"] in {"ok", "count_prior"}
        assert result["combo_count"] == published_combos
        assert NOTE in result["notes"]
        assert INCOMPLETE_NOTE not in result["notes"]
        return
    assert result["status"] == "incomplete_support"
    assert result["combo_count"] == 0
    assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
        None,
        None,
        None,
    ]
    assert result["quality_count_ranges"]["q5"] == [None, None, None]
    assert result["quality_count_candidates"].get("q5") is None
    assert result["quality_count_cells_candidates"].get("q5") is None
    for key in ("q1", "q3", "q4", "q5", "q6"):
        if key not in result["evidence"]["fixed_counts"]:
            assert result["quality_lock"].get(key) is None
    assert result["red_count_range"] == [None, None, None]
    assert result["red_cells_range"] == [None, None, None]
    assert result["red_value_range"] == [None, None, None]
    assert INCOMPLETE_NOTE in result["notes"]


def test_complete_gt5_incomplete_publication_precedes_total_grid_early_return(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _snapshot("seq19")
    prepared_snapshot = engine.prepare_reference_engine_snapshot(
        copy.deepcopy(snapshot)
    )
    base_evidence = engine.extract_evidence(snapshot)
    overlap_evidence = replace(base_evidence, total_grid_options=(100, 101))
    monkeypatch.setattr(engine, "extract_evidence", lambda _snapshot: overlap_evidence)

    entrypoints = (
        (engine._run_reference_engine_impl, snapshot),
        (engine.run_reference_engine, snapshot),
        (engine.run_reference_engine, prepared_snapshot),
    )
    for runner, candidate_snapshot in entrypoints:
        for cap in (1, 2, 3):
            result = runner(candidate_snapshot, max_combos=cap).as_dict()
            assert result["status"] == "incomplete_support"
            assert result["combo_count"] == 0
            assert [
                result[key]
                for key in ("conservative", "balanced", "aggressive")
            ] == [None, None, None]
            assert result["quality_count_ranges"]["q3"] == [12, 12, 12]
            assert result["quality_cells_ranges"]["q4"] == [11, 11, 11]
            for key in ("q5", "q6"):
                assert result["quality_count_ranges"][key] == [None, None, None]
                assert result["quality_cells_ranges"][key] == [None, None, None]
                assert result["quality_count_candidates"].get(key) is None
                assert result["quality_cells_candidates"].get(key) is None
                assert result["quality_count_cells_candidates"].get(key) is None
                assert result["quality_lock"].get(key) is None
            assert result["red_count_range"] == [None, None, None]
            assert result["red_cells_range"] == [None, None, None]
            assert result["red_value_range"] == [None, None, None]
            assert result["total_grid_range"] == [None, None, None]
            assert INCOMPLETE_NOTE in result["notes"]
            assert engine.TOTAL_AVG_CELLS_GRID_SUPPORT_INCOMPLETE_NOTE in result[
                "notes"
            ]

    complete = []
    for cap in (4, 8_000, 50_000):
        result = engine.run_reference_engine(snapshot, max_combos=cap).as_dict()
        assert result["status"] == "ok"
        assert result["combo_count"] == 4
        assert result["total_grid_range"] == [100, 100, 101]
        assert INCOMPLETE_NOTE not in result["notes"]
        complete.append(result)
    assert complete[0] == complete[1] == complete[2]

    monkeypatch.setattr(panel, "hero_max_combos_for_round", lambda *args, **kwargs: 1)
    summary = panel.summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=tmp_path / "snapshot.json",
    )
    assert summary["ahmed_ref"]["status"] == "incomplete_support"
    assert summary["ahmed_ref"]["combo_count"] == 0
    assert summary["quality_composition"] == {
        "schema_version": panel.QUALITY_COMPOSITION_SCHEMA_VERSION,
        "status": "unavailable",
        "reason": "等待引擎提供结构化组合",
        "qualities": {},
    }
    tk_row = overlay._summary_diagnostic_row(
        summary,
        snapshot_path=tmp_path / "snapshot.json",
        render_mode="test",
        manual_active=False,
        settlement_values_hidden=False,
    )
    assert tk_row["reference"]["readiness"] == "incomplete_support"
    assert tk_row["ref_v0"]["combo_count"] == 0

    monkeypatch.setattr(panel, "_read_json", lambda _path: copy.deepcopy(snapshot))
    config = ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with urlopen(  # noqa: S310 - loopback-only product regression
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            assert response.status == 200
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    assert payload["ahmed_ref"]["status"] == "incomplete_support"
    assert payload["ahmed_ref"]["combo_count"] == 0

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = engine.run_reference_engine(snapshot, max_combos=1).as_dict()
    assert rollback["status"] == "no_reachable_combo"
    assert rollback["combo_count"] == 0
    assert INCOMPLETE_NOTE not in rollback["notes"]
    assert engine.TOTAL_AVG_CELLS_GRID_SUPPORT_INCOMPLETE_NOTE in rollback[
        "notes"
    ]


def test_named_rollback_restores_hotfix2_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = engine.run_reference_engine(_snapshot(), max_combos=50_000).as_dict()
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = engine.run_reference_engine(_snapshot(), max_combos=50_000).as_dict()

    assert default["status"] == "ok"
    assert rollback["status"] == "incomplete_support"
    assert rollback["combo_count"] == 0
    assert rollback["balanced"] is None
    assert engine.AHMED_Q5_AVG_OVERBOUND_FAIL_OPEN_NOTE in rollback["notes"]
    assert NOTE not in rollback["notes"]


@pytest.mark.parametrize("prepared", (False, True))
def test_total_and_quality_cell_siblings_intersect_the_same_joint(
    prepared: bool,
) -> None:
    snapshot = _snapshot("seq19")
    snapshot["structured_ref_inputs"]["total_cells"] = 114
    if prepared:
        snapshot = engine.prepare_reference_engine_snapshot(snapshot)
    result = engine.run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert result["total_grid_range"] == [114, 114, 114]
    assert result["quality_count_ranges"]["q4"] == [5, 5, 5]
    assert result["quality_cells_ranges"]["q4"] == [11, 11, 11]
    assert {
        (int(row["count"]), int(row["cells"]))
        for row in result["quality_count_cells_candidates"]["q5"]
    } == {(6, 25), (12, 50)}
    assert _candidate_values(result, "q6") == {0, 6}
    assert result["quality_lock"]["q5"]["strength"] == "probabilistic"
    assert result["quality_lock"]["q6"]["strength"] == "probabilistic"
    assert NOTE in result["notes"]


@pytest.mark.parametrize(
    ("hero", "map_id"),
    (("ivan", 2501), ("gabriela", 5609), ("ahmed", 2501)),
)
def test_source_external_hero_and_map_do_not_define_the_solver(
    hero: str,
    map_id: int,
) -> None:
    result = engine.run_reference_engine(
        _snapshot("seq9", hero=hero, map_id=map_id),
        max_combos=50_000,
    ).as_dict()

    assert _candidate_values(result, "q5") == {6, 12, 18, 24}
    assert result["quality_count_ranges"]["q5"] == [6, 6, 24]
    assert result["quality_lock"]["q5"]["strength"] == "probabilistic"
    assert NOTE in result["notes"]


def _install_same_price_q6_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    original = engine._validated_map_quality_catalog

    def catalog(map_id: int, quality: int):
        if map_id == 9901 and quality == 6:
            return (
                ((100, 1), (100, 4)),
                ((1, 100, 1), (2, 100, 4)),
                "ok",
                "physical-category-v1:complete-gt5-test",
            )
        return original(map_id, quality)

    monkeypatch.setattr(engine, "_validated_map_quality_catalog", catalog)


def _same_price_snapshot() -> dict:
    return {
        "ui_contract": {
            "context": {"hero": "aisha", "map_id": 9901, "phase": "bidding"},
            "constraints": {
                "public_info": {
                    "public_avg_values": [
                        {
                            "semantic": "q6_avg_value",
                            "kind": "avg_value",
                            "quality": 6,
                            "value": 100.0,
                        }
                    ]
                }
            },
        },
        "structured_ref_inputs": {"total_count": 8},
    }


def test_same_price_identities_and_repetition_are_not_count_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_same_price_q6_catalog(monkeypatch)
    snapshot = _same_price_snapshot()
    evidence = engine.extract_evidence(snapshot)

    assert evidence.fixed_counts.get("q6") is None
    assert {count for count, _cells in evidence.quality_count_cells_options["q6"]} == set(
        range(1, 9)
    )
    assert {(count, cells) for count, cells in evidence.quality_count_cells_options["q6"]}.issuperset(
        {(1, 1), (1, 4), (8, 8), (8, 32)}
    )

    direct = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()
    prepared = engine.run_reference_engine(
        engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot)),
        max_combos=8_000,
    ).as_dict()
    for result in (direct, prepared):
        assert _candidate_values(result, "q6") == set(range(1, 9))
        assert result["quality_count_ranges"]["q6"] == [1, 1, 8]
        assert result["quality_lock"]["q6"]["strength"] == "probabilistic"
        assert result["evidence"]["fixed_counts"].get("q6") is None
        assert NOTE in result["notes"]

    incomplete = engine.run_reference_engine(snapshot, max_combos=1).as_dict()
    assert incomplete["status"] == "incomplete_support"
    assert incomplete["combo_count"] == 0
    assert incomplete["quality_count_ranges"]["q6"] == [None, None, None]
    assert incomplete["quality_count_candidates"].get("q6") is None
    assert incomplete["quality_count_cells_candidates"].get("q6") is None
    assert incomplete["quality_lock"].get("q6") is None
    assert (
        incomplete["evidence"]["quality_count_cells_options"].get("q6")
        is None
    )

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()
    assert 8 not in _candidate_values(rollback, "q6")
    assert rollback["evidence"]["fixed_counts"].get("q6") is None


def test_float32_q6_average_keeps_every_physical_even_count_above_five(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = [{"action_id": 100114, "result": 4.5, "tool": "珍品均格"}]
    snapshot = {
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2401,
                "phase": "bidding",
                "round": 4,
            },
            "actions": {"results": actions},
        },
        "action_result_rows": actions,
        "structured_ref_inputs": {
            "total_count": 40,
            "min_counts": {"q3": 1},
        },
    }
    result = engine.run_reference_engine(
        engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot)),
        max_combos=50_000,
    ).as_dict()

    expected_counts = set(range(2, 39, 2))
    assert _candidate_values(result, "q6") == expected_counts
    assert _joint_counts(result, "q6") == expected_counts
    assert result["quality_count_ranges"]["q6"] == [2, 2, 38]
    assert result["quality_cells_ranges"]["q6"] == [9, 9, 171]
    assert result["quality_lock"]["q6"]["strength"] == "probabilistic"
    assert NOTE in result["notes"]

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = engine.run_reference_engine(
        engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot)),
        max_combos=50_000,
    ).as_dict()
    assert rollback["quality_count_ranges"]["q6"] == [2, 2, 4]
    assert 6 not in _candidate_values(rollback, "q6")


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    (
        (
            {
                "ui_contract": {
                    "context": {
                        "hero": "aisha",
                        "map_id": 4541,
                        "phase": "bidding",
                        "round": 3,
                    },
                },
                "structured_ref_inputs": {
                    "total_count": 56,
                    "fixed_counts": {"q1": 13, "q3": 15},
                    "avg_cells": {
                        "q1": 28 / 13,
                        "q3": 43 / 15,
                        "q4": 34 / 12,
                        "q5": 27 / 8,
                    },
                    "quality_cells": {"q1": 28, "q3": 43},
                },
            },
            {"q4": {6, 12, 18}, "q5": {8, 16}, "q6": {0, 2, 6}},
        ),
        (
            {
                "ui_contract": {
                    "context": {
                        "hero": "ahmed",
                        "map_id": 2505,
                        "phase": "bidding",
                        "round": 2,
                    },
                    "actions": {
                        "results": [
                            {"action_id": 100117, "result": 12},
                            {"action_id": 100110, "result": 2.0322580337524414},
                        ],
                    },
                },
                "structured_ref_inputs": {
                    "total_count": 66,
                    "fixed_counts": {"q3": 12},
                    "avg_cells": {
                        "q1": 2.0322580337524414,
                        "q5": 3.222222328186035,
                    },
                },
                "skill_reveals": [
                    {
                        "hero_id": 204,
                        "skill_id": 1002041,
                        "result": 3.222222328186035,
                    },
                ],
            },
            {"q4": {0, 14}, "q5": {9, 18}, "q6": {0, 5}},
        ),
    ),
)
def test_source_external_sparse_lanes_keep_the_complete_joint(
    snapshot: dict,
    expected: dict[str, set[int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()
    for key, counts in expected.items():
        assert _candidate_values(result, key) == counts
        assert _joint_counts(result, key) == counts
        assert result["quality_lock"][key]["strength"] == "probabilistic"
    assert NOTE in result["notes"]

    monkeypatch.setenv(ROLLBACK, "1")
    rollback = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()
    assert NOTE not in rollback["notes"]
    assert any(
        rollback["quality_lock"][key]["strength"] == "exact"
        for key in expected
    )


def test_direct_q5_count_and_nonbidding_phase_are_feature_noops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = [_snapshot(phase="manual"), _snapshot(phase="settled")]
    direct_count = _snapshot()
    direct_count["structured_ref_inputs"]["counts"]["q5"] = 12
    cases.append(direct_count)

    for snapshot in cases:
        default = engine.run_reference_engine(snapshot, max_combos=50_000).as_dict()
        monkeypatch.setenv(ROLLBACK, "1")
        rollback = engine.run_reference_engine(snapshot, max_combos=50_000).as_dict()
        monkeypatch.delenv(ROLLBACK)
        assert default == rollback
        assert NOTE not in default["notes"]


def test_summary_loopback_panel_and_tk_share_the_same_unlocked_joint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    monkeypatch.setattr(panel, "hero_max_combos_for_round", lambda *args, **kwargs: 50_000)
    summary = panel.summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=tmp_path / "latest_snapshot.json",
    )
    ref = summary["ahmed_ref"]
    for key in ("q5", "q6"):
        quality = summary["quality_composition"]["qualities"][key]
        assert quality["dimension_state"] == "未锁"
        assert quality["lock_strength"] == "probabilistic"
    assert ref["quality_count_ranges"]["q5"] == [6, 6, 12]
    assert ref["quality_cells_ranges"]["q5"] == [25, 25, 50]
    assert ref["red_count_range"] == ref["quality_count_ranges"]["q6"]
    assert ref["red_cells_range"] == ref["quality_cells_ranges"]["q6"]
    assert "12件/50格" in summary["red"]["purple_gold_probability_hover"]

    tk_row = overlay._summary_diagnostic_row(
        summary,
        snapshot_path=tmp_path / "latest_snapshot.json",
        render_mode="test",
        manual_active=False,
        settlement_values_hidden=False,
    )
    assert tk_row["reference"]["readiness"] == "live_ready"
    assert tk_row["red"]["count_range"] == summary["red"]["count_range"]
    assert "12件/50格" in tk_row["red"]["purple_gold_probability_hover"]

    monkeypatch.setattr(panel, "_read_json", lambda _path: copy.deepcopy(snapshot))
    config = ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with urlopen(  # noqa: S310 - loopback-only product regression
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            assert response.status == 200
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    assert payload["ahmed_ref"]["quality_count_ranges"]["q5"] == [6, 6, 12]
    assert payload["ahmed_ref"]["quality_lock"]["q5"]["strength"] == "probabilistic"
