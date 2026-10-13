"""Aisha 2524: q5 cells with multiple catalog counts cannot hard-pin total grid.

The bidding fixture contains only fields visible before settlement.  Settlement
truth is deliberately absent; this regression protects physical support rather
than tuning a posterior to one outcome.
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

import ahmad_ref_engine as engine  # noqa: E402
import ahmad_live_panel_server as panel  # noqa: E402
from ahmad_live_panel_server import (  # noqa: E402
    ServerConfig,
    make_handler,
    summarize_snapshot,
)
from ahmad_ref_engine import (  # noqa: E402
    AISHA_MULTICOUNT_Q5_CELLS_INFERRED_TOTAL_DISABLE,
    AISHA_MULTICOUNT_Q5_CELLS_INFERRED_TOTAL_SOFT_NOTE,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from ahmad_tk_overlay import _remaining_cells_range  # noqa: E402


ROLLBACK = AISHA_MULTICOUNT_Q5_CELLS_INFERRED_TOTAL_DISABLE
NOTE = AISHA_MULTICOUNT_Q5_CELLS_INFERRED_TOTAL_SOFT_NOTE
QUALIFICATION = "postupdate_unknown_warehouse_source"
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
STATIC_DATA_BYTES = 10_408


def _assert_frozen_file(
    root: Path,
    relative: str,
    *,
    size: int,
    sha256: str,
) -> None:
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
    assert type(parsed) is dict  # noqa: E721 - frozen contract root must be exact
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


@pytest.fixture(scope="module", autouse=True)
def _frozen_inputs() -> None:
    _require_frozen_package_inputs()


def test_release_contract_gate_accepts_only_complete_lf_or_crlf(
    tmp_path: Path,
) -> None:
    source = ROOT / "scripts" / "release_zip_contract.json"
    canonical = source.read_bytes().replace(b"\r\n", b"\n")
    target = tmp_path / source.name

    for payload in (canonical, canonical.replace(b"\n", b"\r\n")):
        target.write_bytes(payload)
        parsed = _load_canonical_lf_json(
            target,
            sha256=RELEASE_CONTRACT_CANONICAL_LF_SHA256,
        )
        assert parsed["schema"] == "bidking_release_zip_contract_v1"

    target.write_bytes(canonical.replace(b"\n", b"\r\n", 1))
    with pytest.raises(AssertionError, match="mixed EOL"):
        _load_canonical_lf_json(
            target,
            sha256=RELEASE_CONTRACT_CANONICAL_LF_SHA256,
        )

    mutated = bytearray(canonical)
    mutated[canonical.index(b"bidking_release_zip_contract_v1")] ^= 1
    target.write_bytes(mutated)
    with pytest.raises(AssertionError, match="wrong canonical LF sha256"):
        _load_canonical_lf_json(
            target,
            sha256=RELEASE_CONTRACT_CANONICAL_LF_SHA256,
        )


def _warehouse_source(qualification: str = QUALIFICATION) -> dict:
    official = qualification == "official_random_warehouse"
    return {
        "qualification": qualification,
        "official_distribution_eligible": official,
        "quote_policy": "normal" if official else "advisory_only_no_clamp",
        "quarantine_required": False,
    }


def _target_snapshot(*, qualification: str = QUALIFICATION) -> dict:
    source = _warehouse_source(qualification)
    session_id = "2524:truth-free-r4"
    structured = {
        "source": "monitor_pre_settlement_field_updates",
        "hero": "aisha",
        "total_count": 32,
        "counts": {"q1": 12, "q3": 10, "q4": 4},
        "quality_cells": {"q1": 21, "q3": 34, "q4": 19, "q5": 3},
        "avg_cells": {},
        "quality_values": {},
        "quality_value_floors": {},
    }
    return {
        "hero": "aisha",
        "map_id": 2524,
        "round": 4,
        "action_round": 4,
        "phase": "bidding",
        "session_id": session_id,
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": qualification,
        "official_distribution_eligible": source["official_distribution_eligible"],
        "structured_ref_inputs": structured,
        "skill_reveal_rows": [
            {
                "sort": sort,
                "skill_id": skill_id,
                "hero_id": 103,
                "revealed_items": count,
                "revealed_items_detail": [],
            }
            for sort, (skill_id, count) in enumerate(
                ((1001033, 12), (1001032, 10), (1001031, 4)),
                start=31,
            )
        ],
        "ui_contract": {
            "context": {
                "hero": "aisha",
                "map_id": 2524,
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


def _run(snapshot: dict, *, max_combos: int = 8_000) -> dict:
    prepared = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    return run_reference_engine(prepared, max_combos=max_combos).as_dict()


def _rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ROLLBACK, "1")


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_2524_multicount_q5_cells_keeps_reachable_red_cells(
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()

    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot, max_combos=max_combos)
    _rollback(monkeypatch)
    legacy = _run(snapshot, max_combos=max_combos)

    assert fixed["status"] == legacy["status"] == "ok"
    assert fixed["quality_count_ranges"]["q5"] == legacy["quality_count_ranges"]["q5"]
    assert fixed["quality_count_ranges"]["q6"] == legacy["quality_count_ranges"]["q6"]
    assert {row["value"] for row in fixed["quality_count_candidates"]["q5"]} == {
        1,
        2,
        3,
    }
    assert {row["value"] for row in fixed["quality_count_candidates"]["q6"]} == {
        3,
        4,
        5,
    }
    assert fixed["quality_cells_ranges"]["q5"] == [3, 3, 3]
    assert fixed["quality_cells_ranges"]["q6"] == [8, 14, 23]
    assert max(fixed["quality_cells_ranges"]["q6"]) >= 23
    assert "q6" in fixed["quality_count_cells_candidates"]
    assert fixed["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert NOTE in fixed["notes"]
    assert legacy["total_grid_range"] == [89, 89, 89]
    assert legacy["quality_cells_ranges"]["q6"] == [7, 12, 12]
    assert NOTE not in legacy["notes"]
    assert fixed["evidence"]["phase"] == "bidding"


@pytest.mark.parametrize(
    "qualification",
    [
        "postupdate_unknown_warehouse_source",
        "player_custom_warehouse",
        "official_random_warehouse",
    ],
)
def test_source_pool_qualification_is_unchanged(
    qualification: str,
    tmp_path: Path,
) -> None:
    snapshot = _target_snapshot(qualification=qualification)
    result = summarize_snapshot(snapshot, snapshot_path=tmp_path / "2524.json")

    assert result["ahmed_ref"]["status"] == "ok"
    assert result["context"]["warehouse_source_qualification"] == qualification
    assert result["context"]["warehouse_source"][
        "official_distribution_eligible"
    ] is (qualification == "official_random_warehouse")
    assert NOTE in result["ahmed_ref"]["notes"]
    assert result["quality_composition"]["status"] == "ready"


@pytest.mark.parametrize(
    ("field", "key", "value"),
    [
        ("counts", "q5", 3),
        ("quality_cells", "q6", 23),
        ("avg_cells", "q5", 1.5),
        ("avg_cells", "q6", 4.5),
    ],
)
def test_direct_high_tier_evidence_is_rollback_noop(
    field: str,
    key: str,
    value: int | float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    snapshot["structured_ref_inputs"].setdefault(field, {})[key] = value

    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot)
    _rollback(monkeypatch)
    legacy = _run(snapshot)

    assert fixed == legacy
    assert NOTE not in fixed["notes"]


def test_unique_catalog_q5_count_is_rollback_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    snapshot["structured_ref_inputs"]["quality_cells"]["q5"] = 1

    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot)
    _rollback(monkeypatch)
    legacy = _run(snapshot)

    assert fixed == legacy
    assert fixed["quality_count_ranges"]["q5"] == [1, 1, 1]
    assert NOTE not in fixed["notes"]


@pytest.mark.parametrize(
    ("mutation", "value"),
    [("no_total_count", None), ("settled", None), ("other_hero", "ahmed")],
)
def test_out_of_scope_states_are_rollback_noop(
    mutation: str,
    value: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    if mutation == "no_total_count":
        snapshot["structured_ref_inputs"].pop("total_count")
    elif mutation == "settled":
        snapshot["phase"] = "settled"
        snapshot["ui_contract"]["context"]["phase"] = "settled"
    else:
        snapshot["hero"] = value
        snapshot["structured_ref_inputs"]["hero"] = value
        snapshot["ui_contract"]["context"]["hero"] = value

    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot)
    _rollback(monkeypatch)
    legacy = _run(snapshot)

    assert fixed == legacy
    assert NOTE not in fixed.get("notes", [])


def test_catalog_unavailable_fails_closed_to_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    monkeypatch.setattr(engine, "_q5_catalog_cell_compose_enabled", lambda _key: False)

    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot)
    _rollback(monkeypatch)
    legacy = _run(snapshot)

    assert fixed == legacy
    assert NOTE not in fixed["notes"]


def test_public_q5_cells_conflict_is_rollback_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    snapshot["public_info_rows"] = [
        {
            "sort": 40,
            "info_id": 200011,
            "value": 4,
            "session_id": snapshot["session_id"],
            "round": 4,
        }
    ]

    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot)
    _rollback(monkeypatch)
    legacy = _run(snapshot)

    assert fixed == legacy
    assert "public_info_200011_q5_cells_conflict" in fixed["notes"]
    assert NOTE not in fixed["notes"]


@pytest.mark.parametrize("total_cells", [89, 100])
def test_explicit_total_cells_stays_hard_and_is_rollback_noop(
    total_cells: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    snapshot["structured_ref_inputs"]["total_cells"] = total_cells

    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot)
    _rollback(monkeypatch)
    legacy = _run(snapshot)

    assert fixed == legacy
    assert fixed["total_grid_range"] == [total_cells] * 3
    assert NOTE not in fixed["notes"]


def test_zero_red_residual_is_rollback_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    snapshot["structured_ref_inputs"]["total_count"] = 27
    snapshot["structured_ref_inputs"]["counts"]["q5"] = 1
    snapshot["structured_ref_inputs"]["quality_cells"]["q5"] = 1

    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot)
    _rollback(monkeypatch)
    legacy = _run(snapshot)

    assert fixed == legacy
    assert fixed["quality_count_ranges"]["q6"] == [0, 0, 0]
    assert NOTE not in fixed["notes"]


@pytest.mark.parametrize("bad_quality", [5, 6])
def test_malformed_catalog_domain_fails_closed_to_legacy(
    bad_quality: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    original = engine._catalog_quality_composable_grid_options

    def malformed(quality: int, count: int) -> tuple[int | bool, ...]:
        if quality == bad_quality and (quality == 6 or count == 2):
            return (1, True, 3)
        return original(quality, count)

    monkeypatch.setattr(engine, "_catalog_quality_composable_grid_options", malformed)
    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot)
    _rollback(monkeypatch)
    legacy = _run(snapshot)

    assert fixed == legacy
    assert NOTE not in fixed["notes"]


def test_summary_loopback_api_and_tk_keep_the_same_q6_joint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    summary = summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=tmp_path / "latest_snapshot.json",
    )
    ref = summary["ahmed_ref"]

    assert ref["status"] == "ok"
    assert ref["quality_cells_ranges"]["q6"] == [8, 14, 23]
    assert ref["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert ref["quality_count_cells_candidates"]["q6"]
    assert summary["quality_composition"]["status"] == "ready"
    assert summary["quality_composition"]["qualities"]["q6"][
        "pricing_posterior"
    ] is True
    assert _remaining_cells_range(ref) == (6, 14, 21)

    monkeypatch.setattr(panel, "_read_json", lambda _path: copy.deepcopy(snapshot))
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))),
    )
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
    assert payload["ahmed_ref"]["quality_cells_ranges"]["q6"] == [8, 14, 23]
    assert payload["quality_composition"]["status"] == "ready"


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
    "status": result["status"],
    "quotes": [result["conservative"], result["balanced"], result["aggressive"]],
    "total_grid_range": result["total_grid_range"],
    "q5_count": result["quality_count_ranges"]["q5"],
    "q6_count": result["quality_count_ranges"]["q6"],
    "q6_cells": result["quality_cells_ranges"]["q6"],
    "q6_lock": result["quality_lock"]["q6"],
    "q6_joint": result["quality_count_cells_candidates"].get("q6"),
    "notes": result["notes"],
}))
"""


def _fresh_process(snapshot: dict, *, max_combos: int, rollback: bool) -> dict:
    env = os.environ.copy()
    if rollback:
        env[ROLLBACK] = "1"
    else:
        env.pop(ROLLBACK, None)
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and local code
        [sys.executable, "-c", _FRESH_PROCESS_RUNNER, str(max_combos)],
        cwd=ROOT,
        env=env,
        input=json.dumps(snapshot),
        text=True,
        capture_output=True,
        check=True,
        timeout=120,
    )
    assert completed.stderr == ""
    return json.loads(completed.stdout)


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_fresh_process_default_and_rollback(max_combos: int) -> None:
    fixed = _fresh_process(
        _target_snapshot(),
        max_combos=max_combos,
        rollback=False,
    )
    legacy = _fresh_process(
        _target_snapshot(),
        max_combos=max_combos,
        rollback=True,
    )

    assert fixed["status"] == legacy["status"] == "ok"
    assert fixed["quotes"] == [194_947, 718_897, 875_473]
    assert fixed["total_grid_range"] == [83, 91, 98]
    assert fixed["q6_cells"] == [8, 14, 23]
    assert fixed["q6_lock"]["strength"] == "probabilistic"
    assert fixed["q6_joint"]
    assert NOTE in fixed["notes"]
    assert legacy["quotes"] == [162_818, 751_555, 863_127]
    assert legacy["total_grid_range"] == [89, 89, 89]
    assert legacy["q6_cells"] == [7, 12, 12]
    assert NOTE not in legacy["notes"]
