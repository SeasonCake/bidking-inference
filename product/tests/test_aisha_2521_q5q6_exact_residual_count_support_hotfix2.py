"""Core-C: keep Aisha q5/q6 exact-cell physical count support complete.

The bidding fixture is truth-free and reduced from
``2521:9000000000000041``.  Settlement values appear only in the explicit
settled no-injection control.
"""

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

import ahmad_live_panel_server as panel  # noqa: E402
import ahmad_ref_engine as engine  # noqa: E402
from ahmad_live_panel_server import ServerConfig, make_handler, summarize_snapshot  # noqa: E402
from ahmad_tk_overlay import _remaining_cells_range  # noqa: E402


ROLLBACK = engine.AISHA_Q5Q6_EXACT_RESIDUAL_COUNT_SUPPORT_DISABLE
NOTE = engine.AISHA_Q5Q6_EXACT_RESIDUAL_COUNT_SUPPORT_NOTE
PACKAGE_FINGERPRINT_SHA256 = (
    "96d0cfea329038edf2a81bff9ed4e903f53253e7ed9720604d7d564bde4d6dd1"
)
RELEASE_CONTRACT_CANONICAL_LF_SHA256 = (
    "7ea4aaa42c6215476b4137af881380dde41a957d9c82848ce0cf078e52440084"
)
STATIC_DATA_REL = (
    "external_references/AuctionAnalyzer4.13.3/_decompiled/MapBidCalculator/"
    "MapBidCalculator/Models/StaticData.cs"
)
STATIC_DATA_BYTES = 10408


def _assert_frozen_file(root: Path, relative: str, *, size: int, sha256: str) -> None:
    path = root / relative
    assert path.is_file(), f"missing frozen package input: {relative}"
    payload = path.read_bytes()
    assert len(payload) == size, f"wrong frozen package input size: {relative}"
    assert hashlib.sha256(payload).hexdigest() == sha256, (
        f"wrong frozen package input sha256: {relative}"
    )


def _load_canonical_lf_json(path: Path, *, sha256: str) -> dict:
    assert path.is_file(), f"missing frozen JSON input: {path.name}"
    payload = path.read_bytes()
    without_crlf = payload.replace(b"\r\n", b"")
    assert b"\r" not in without_crlf, f"invalid lone CR in frozen JSON input: {path.name}"
    canonical = payload.replace(b"\r\n", b"\n")
    full_crlf = canonical.replace(b"\n", b"\r\n")
    assert payload == canonical or payload == full_crlf, (
        f"mixed EOL in frozen JSON input: {path.name}"
    )
    assert hashlib.sha256(canonical).hexdigest() == sha256, (
        f"wrong canonical LF sha256: {path.name}"
    )
    parsed = json.loads(canonical.decode("utf-8"))
    assert isinstance(parsed, dict), f"wrong frozen JSON root type: {path.name}"
    return parsed


def _require_frozen_package_inputs(root: Path = ROOT) -> None:
    fingerprints_path = root / "scripts" / "package_data_fingerprints.json"
    contract_path = root / "scripts" / "release_zip_contract.json"
    assert hashlib.sha256(fingerprints_path.read_bytes()).hexdigest() == (
        PACKAGE_FINGERPRINT_SHA256
    )
    contract = _load_canonical_lf_json(
        contract_path,
        sha256=RELEASE_CONTRACT_CANONICAL_LF_SHA256,
    )
    fingerprints = json.loads(fingerprints_path.read_text("utf-8"))
    for relative, spec in fingerprints["files"].items():
        _assert_frozen_file(
            root,
            relative,
            size=int(spec["size"]),
            sha256=str(spec["sha256"]),
        )
    _assert_frozen_file(
        root,
        STATIC_DATA_REL,
        size=STATIC_DATA_BYTES,
        sha256=contract["fixed_manifest_values"]["StaticDataSourceSHA256"],
    )


def _warehouse_source(qualification: str) -> dict:
    official = qualification == "official_random_warehouse"
    return {
        "qualification": qualification,
        "official_distribution_eligible": official,
        "quote_policy": "normal" if official else "advisory_only_no_clamp",
        "quarantine_required": False,
    }


def _skill_rows() -> list[dict]:
    return [
        {
            "sort": 33,
            "skill_id": skill_id,
            "hero_id": 103,
            "revealed_items": count,
            "revealed_items_detail": [],
        }
        for skill_id, count in ((1001033, 12), (1001032, 13), (1001031, 16))
    ]


def _known_items() -> tuple[list[dict], list[dict]]:
    action_items = [
        {
            "local_index": 39,
            "runtime_id": 1862572748120724,
            "item_id": 1084011,
            "item_name": "known-q4",
            "quality": 4,
            "value": 7655,
            "shape_code": 12,
            "cells": 2,
        },
        {
            "local_index": 165,
            "runtime_id": 1862572748120753,
            "item_id": 1056010,
            "item_name": "known-q6-diamond",
            "quality": 6,
            "value": 512000,
            "shape_code": 33,
            "cells": 9,
        },
    ]
    minimap_items = [
        {
            "quality": row["quality"],
            "item_id": row["item_id"],
            "cells": row["cells"],
            "shape_key": str(row["shape_code"]),
            "local_index": row["local_index"],
            "value": row["value"],
        }
        for row in action_items
    ]
    return action_items, minimap_items


def _snapshot(
    *,
    qualification: str = "postupdate_unknown_warehouse_source",
) -> dict:
    session_id = "2521:9000000000000041"
    source = _warehouse_source(qualification)
    action_items, minimap_items = _known_items()
    structured = {
        "source": "monitor_pre_settlement_field_updates",
        "hero": "aisha",
        "total_count": None,
        "total_cells": 214,
        "counts": {"q3": 13, "q4": 16},
        "quality_cells": {"q3": 43, "q4": 61, "q5": 26},
        "split_counts": {"green": 12},
        "split_quality_cells": {"green": 30},
        "minimap_items": copy.deepcopy(minimap_items),
    }
    return {
        "hero": "aisha",
        "map_id": 2521,
        "round": 4,
        "action_round": 4,
        "phase": "bidding",
        "session_id": session_id,
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": qualification,
        "official_distribution_eligible": source[
            "official_distribution_eligible"
        ],
        "structured_ref_inputs": structured,
        "skill_reveal_rows": _skill_rows(),
        "action_result_rows": [
            {
                "sort": 3,
                "action_id": 100129,
                "result": 11,
                "result_field": 14,
                "session_id": session_id,
                "source_kind": "direct_action_response",
                "revealed_items": 2,
                "revealed_items_detail": copy.deepcopy(action_items),
            }
        ],
        "minimap_grid_items": copy.deepcopy(minimap_items),
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2521,
                "round": 4,
                "phase": "bidding",
                "session_id": session_id,
                "warehouse_source": copy.deepcopy(source),
                "warehouse_source_qualification": qualification,
                "official_distribution_eligible": source[
                    "official_distribution_eligible"
                ],
            },
            "constraints": {"public_info": {}},
            "source": {"created_at": 1.0},
        },
    }


def _adjacent_2530_snapshot() -> dict:
    session_id = "2530:9000000000000043"
    qualification = "postupdate_unknown_warehouse_source"
    source = _warehouse_source(qualification)
    structured = {
        "source": "monitor_pre_settlement_field_updates",
        "hero": "aisha",
        "total_count": None,
        "total_cells": 133,
        "counts": {"q3": 10, "q4": 8},
        "quality_cells": {"q3": 20, "q4": 18, "q5": 23},
        "split_counts": {"white": 1, "green": 11},
        "split_quality_cells": {"white": 1, "green": 19},
    }
    skill_rows = [
        {
            "sort": sort,
            "skill_id": skill_id,
            "hero_id": 103,
            "revealed_items": count,
            "revealed_items_detail": [],
        }
        for sort, (skill_id, count) in enumerate(
            ((1001034, 1), (1001033, 11), (1001032, 10), (1001031, 8)),
            start=1,
        )
    ]
    return {
        "hero": "aisha",
        "map_id": 2530,
        "round": 4,
        "action_round": 4,
        "phase": "bidding",
        "session_id": session_id,
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": qualification,
        "official_distribution_eligible": False,
        "structured_ref_inputs": structured,
        "skill_reveal_rows": skill_rows,
        "minimap_grid_items": [
            {
                "quality": 6,
                "item_id": 1046002,
                "cells": 6,
                "shape_key": "23",
                "local_index": 122,
                "row": 13,
                "col": 3,
                "value": 199080,
                "category": 104,
            }
        ],
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2530,
                "round": 4,
                "phase": "bidding",
                "session_id": session_id,
                "warehouse_source": copy.deepcopy(source),
                "warehouse_source_qualification": qualification,
                "official_distribution_eligible": False,
            },
            "constraints": {"public_info": {}},
            "source": {"created_at": 1.0},
        },
    }
def _run(snapshot: dict, *, max_combos: int = 8000) -> dict:
    return engine.run_reference_engine(
        engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot)),
        max_combos=max_combos,
    ).as_dict()


def _rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ROLLBACK, "1")


_FRESH_PROCESS_RUNNER = r"""
import json
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root / "hero_ref" / "src"))
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine

snapshot = json.load(sys.stdin)
result = run_reference_engine(
    prepare_reference_engine_snapshot(snapshot),
    max_combos=int(sys.argv[1]),
).as_dict()
print(json.dumps({
    "quotes": [result["conservative"], result["balanced"], result["aggressive"]],
    "q6_counts": [
        row["value"]
        for row in result["quality_count_candidates"].get("q6", [])
    ],
    "q6_count_range": result["quality_count_ranges"]["q6"],
    "q6_cells": result["quality_cells_ranges"]["q6"],
    "q6_lock": result["quality_lock"]["q6"],
    "notes": result["notes"],
}))
"""


def _fresh_process(snapshot: dict, *, max_combos: int, rollback: bool) -> dict:
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
        check=True,
        timeout=180,
    )
    return json.loads(completed.stdout)


def test_frozen_input_gate_rejects_missing_and_wrong_bytes(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="missing frozen package input"):
        _assert_frozen_file(tmp_path, "input.bin", size=2, sha256="0" * 64)
    path = tmp_path / "input.bin"
    path.write_bytes(b"bad")
    with pytest.raises(AssertionError, match="wrong frozen package input size"):
        _assert_frozen_file(tmp_path, "input.bin", size=2, sha256="0" * 64)
    path.write_bytes(b"ok")
    with pytest.raises(AssertionError, match="wrong frozen package input sha256"):
        _assert_frozen_file(tmp_path, "input.bin", size=2, sha256="0" * 64)


def test_release_contract_gate_accepts_lf_and_crlf_but_rejects_other_bytes(
    tmp_path: Path,
) -> None:
    source = ROOT / "scripts" / "release_zip_contract.json"
    canonical = source.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(canonical).hexdigest() == (
        RELEASE_CONTRACT_CANONICAL_LF_SHA256
    )
    target = tmp_path / "release_zip_contract.json"

    with pytest.raises(AssertionError, match="missing frozen JSON input"):
        _load_canonical_lf_json(
            target,
            sha256=RELEASE_CONTRACT_CANONICAL_LF_SHA256,
        )

    for payload in (canonical, canonical.replace(b"\n", b"\r\n")):
        target.write_bytes(payload)
        parsed = _load_canonical_lf_json(
            target,
            sha256=RELEASE_CONTRACT_CANONICAL_LF_SHA256,
        )
        assert parsed["schema"] == "bidking_release_zip_contract_v1"

    mutated = bytearray(canonical)
    mutated[canonical.index(b"bidking_release_zip_contract_v1")] ^= 1
    target.write_bytes(mutated)
    with pytest.raises(AssertionError, match="wrong canonical LF sha256"):
        _load_canonical_lf_json(
            target,
            sha256=RELEASE_CONTRACT_CANONICAL_LF_SHA256,
        )

    target.write_bytes(canonical.replace(b"\n", b"\r", 1))
    with pytest.raises(AssertionError, match="invalid lone CR"):
        _load_canonical_lf_json(
            target,
            sha256=RELEASE_CONTRACT_CANONICAL_LF_SHA256,
        )

    target.write_bytes(canonical.replace(b"\n", b"\r\n", 1))
    with pytest.raises(AssertionError, match="mixed EOL"):
        _load_canonical_lf_json(
            target,
            sha256=RELEASE_CONTRACT_CANONICAL_LF_SHA256,
        )


def test_exact_archive_package_inputs_are_complete() -> None:
    _require_frozen_package_inputs()


@pytest.mark.parametrize("max_combos", [8000, 50000])
@pytest.mark.parametrize(
    "qualification",
    [
        "postupdate_unknown_warehouse_source",
        "player_custom_warehouse",
        "official_random_warehouse",
    ],
)
def test_2521_catalog_support_keeps_eight_and_nine_red_items(
    max_combos: int,
    qualification: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_frozen_package_inputs()
    snapshot = _snapshot(qualification=qualification)
    monkeypatch.delenv(ROLLBACK, raising=False)
    current = _run(snapshot, max_combos=max_combos)
    _rollback(monkeypatch)
    rollback = _run(snapshot, max_combos=max_combos)

    current_counts = {
        int(row["value"])
        for row in current["quality_count_candidates"]["q6"]
    }
    assert {8, 9}.issubset(current_counts)
    assert current["quality_cells_ranges"]["q6"] == [54, 54, 54]
    assert current["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert current["notes"].count(NOTE) == 1
    assert rollback["quality_count_ranges"]["q6"] == [4, 4, 4]
    assert NOTE not in rollback["notes"]
    assert current["evidence"]["quality_value_floors"]["q6"] == 512000
    assert current["evidence"]["q6_known_item_facts"] == [
        {"identity": "local:165", "cells": 9, "value": 512000}
    ]
    monkeypatch.delenv(ROLLBACK, raising=False)
    prepared = engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    evidence = engine.extract_evidence(prepared)
    totals, overrides = engine._aisha_q5q6_exact_residual_count_support(evidence)
    assert 51 in totals and 52 in totals
    assert 8 in overrides["q6"] and 9 in overrides["q6"]
    assert current["evidence"]["phase"] == "bidding"


@pytest.mark.parametrize("max_combos", [8000, 50000])
def test_2521_fresh_process_default_and_rollback(max_combos: int) -> None:
    _require_frozen_package_inputs()
    current = _fresh_process(_snapshot(), max_combos=max_combos, rollback=False)
    rollback = _fresh_process(_snapshot(), max_combos=max_combos, rollback=True)

    assert {8, 9}.issubset({int(value) for value in current["q6_counts"]})
    assert current["q6_cells"] == rollback["q6_cells"] == [54, 54, 54]
    assert current["q6_lock"]["strength"] == "probabilistic"
    assert current["notes"].count(NOTE) == 1
    assert rollback["q6_count_range"] == [4, 4, 4]
    assert NOTE not in rollback["notes"]


@pytest.mark.parametrize("max_combos", [8000, 50000])
def test_adjacent_2530_expands_only_the_same_physical_support_contract(
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _adjacent_2530_snapshot()
    monkeypatch.delenv(ROLLBACK, raising=False)
    current = _run(snapshot, max_combos=max_combos)
    _rollback(monkeypatch)
    rollback = _run(snapshot, max_combos=max_combos)

    current_q5 = {
        int(row["count"])
        for row in current["quality_count_cells_candidates"]["q5"]
    }
    current_q6 = {
        int(row["count"])
        for row in current["quality_count_cells_candidates"]["q6"]
    }
    rollback_q5 = {
        int(row["count"])
        for row in rollback["quality_count_cells_candidates"]["q5"]
    }
    rollback_q6 = {
        int(row["count"])
        for row in rollback["quality_count_cells_candidates"]["q6"]
    }
    assert 8 in current_q5 - rollback_q5
    assert 8 in current_q6 - rollback_q6
    assert current["quality_cells_ranges"] == rollback["quality_cells_ranges"]
    assert current["quality_lock"] == rollback["quality_lock"]
    assert current["quality_lock"]["q6"]["strength"] == "probabilistic"
    assert engine.AISHA_SOFT_TOTAL_Q6_JOINT_NOTE not in current["notes"]
    assert engine.AISHA_SOFT_TOTAL_Q6_JOINT_NOTE not in rollback["notes"]
    assert current["evidence"]["phase"] == rollback["evidence"]["phase"] == "bidding"
    assert [current[key] for key in ("conservative", "balanced", "aggressive")] == [
        1_144_416,
        2_228_454,
        2_573_460,
    ]
    assert [rollback[key] for key in ("conservative", "balanced", "aggressive")] == [
        1_142_165,
        2_206_295,
        2_582_777,
    ]


def test_summary_loopback_api_and_tk_consume_probabilistic_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_frozen_package_inputs()
    snapshot = _snapshot()
    summary = summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=tmp_path / "latest_snapshot.json",
    )
    ref = summary["ahmed_ref"]
    assert {8, 9}.issubset(
        {int(row["value"]) for row in ref["quality_count_candidates"]["q6"]}
    )
    assert ref["quality_lock"]["q6"]["strength"] == "probabilistic"
    assert _remaining_cells_range(ref) == (54, 54, 54)

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
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    assert status == 200
    api_ref = payload["ahmed_ref"]
    assert {8, 9}.issubset(
        {int(row["value"]) for row in api_ref["quality_count_candidates"]["q6"]}
    )
    assert payload["context"]["warehouse_source_qualification"] == (
        "postupdate_unknown_warehouse_source"
    )


def test_stronger_and_incomplete_states_are_rollback_noops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases: list[dict] = []
    exact_total = _snapshot()
    exact_total["structured_ref_inputs"]["total_count"] = 54
    cases.append(exact_total)
    direct_q6 = _snapshot()
    direct_q6["structured_ref_inputs"]["quality_cells"]["q6"] = 54
    cases.append(direct_q6)
    direct_q5_count = _snapshot()
    direct_q5_count["structured_ref_inputs"]["counts"]["q5"] = 4
    cases.append(direct_q5_count)
    direct_q6_count = _snapshot()
    direct_q6_count["structured_ref_inputs"]["counts"]["q6"] = 9
    cases.append(direct_q6_count)
    missing_total = _snapshot()
    missing_total["structured_ref_inputs"].pop("total_cells")
    cases.append(missing_total)
    partial = _snapshot()
    partial["structured_ref_inputs"]["counts"].pop("q4")
    cases.append(partial)
    other_hero = _snapshot()
    other_hero["hero"] = "ahmed"
    other_hero["structured_ref_inputs"]["hero"] = "ahmed"
    other_hero["ui_contract"]["context"]["hero"] = "ahmed"
    cases.append(other_hero)
    conflict = _snapshot()
    conflict["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ] = [
        {
            "semantic": "total_cells",
            "kind": "total_cells",
            "value": 999,
            "display_value": "999",
        }
    ]
    cases.append(conflict)

    monkeypatch.delenv(ROLLBACK, raising=False)
    default = [_run(case) for case in cases]
    _rollback(monkeypatch)
    rollback = [_run(case) for case in cases]
    assert default == rollback
    assert all(NOTE not in result["notes"] for result in default)


def test_settlement_truth_is_not_injected_into_bidding_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    snapshot["phase"] = "settled"
    snapshot["ui_contract"]["context"]["phase"] = "settled"
    snapshot["final_quality_counts"] = "q1=12;q2=0;q3=13;q4=16;q5=4;q6=9"
    snapshot["final_quality_cells"] = "q1=30;q2=0;q3=43;q4=61;q5=26;q6=54"
    snapshot["final_quality_values"] = "q1=0;q2=0;q3=0;q4=0;q5=0;q6=3233620"
    snapshot["inventory_count"] = 54
    snapshot["inventory_cells"] = 214

    monkeypatch.delenv(ROLLBACK, raising=False)
    current = _run(snapshot)
    _rollback(monkeypatch)
    rollback = _run(snapshot)
    assert current == rollback
    assert current["quality_count_ranges"]["q6"] == [9, 9, 9]
    assert current["quality_cells_ranges"]["q6"] == [54, 54, 54]
    assert NOTE not in current["notes"]
