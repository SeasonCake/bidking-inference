"""Hotfix3 regressions for all-items average plus trusted packet count."""

from __future__ import annotations

import copy
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (
    ROOT / "hero_ref" / "src",
    ROOT / "hero_ref" / "tools",
    ROOT / "scripts",
    ROOT / "src",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_ref_engine as engine  # noqa: E402
from ahmad_live_panel_server import (  # noqa: E402
    ServerConfig,
    make_handler,
    summarize_snapshot,
)
from ahmad_tk_overlay import _summary_diagnostic_row  # noqa: E402
from bidking_lab.runtime.snapshot import (  # noqa: E402
    TOTAL_AVG_PACKET_COUNT_BRIDGE_DISABLE,
    ui_contract_from_artifact,
)


DIAGNOSTIC_AVG = 2.558823585510254


def _raw_diagnostic_shape() -> dict:
    source = {
        "qualification": "official_random_warehouse",
        "official_distribution_eligible": True,
        "quote_policy": "advisory_only_no_clamp",
        "quarantine_required": False,
    }
    return {
        "session_id": "synthetic-ahmed-4501-total-avg-packet-count",
        "hero": "ahmed",
        "hero_id": 204,
        "map_id": 4501,
        "model_map_id": 4501,
        "phase": "bidding",
        "round": 2,
        "action_round": 2,
        "observed_round": 1,
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": source["qualification"],
        "official_distribution_eligible": True,
        "quote_policy": source["quote_policy"],
        "public_info_rows": [
            {
                "sort": 2,
                "info_id": 200014,
                "value": DIAGNOSTIC_AVG,
                "value_field": 11,
            }
        ],
        "inference_input_constraints": {
            "mode": "pre_settlement_trusted_totals",
            "total_item_count": {
                "value": 34,
                "source": "packet",
                "confidence": "exact",
                "sequence": 25,
            },
        },
        "structured_ref_inputs": {"total_count": 34},
        "minimap_grid_items": [],
        "category_grid_items": [],
        "panel": {},
        "model_eval": {},
        "v2_posterior_rows": [],
        "bid_rows": [],
        "warehouse_rows": [],
        "tool_rows": [],
    }


def _diagnostic_shape() -> dict:
    artifact = _raw_diagnostic_shape()
    artifact["ui_contract"] = ui_contract_from_artifact(artifact)
    return json.loads(json.dumps(artifact))


@pytest.mark.parametrize("max_combos", (8_000, 50_000))
@pytest.mark.parametrize("prepared", (False, True))
def test_packet_exact_total_count_and_public_average_lock_unique_87(
    max_combos: int,
    prepared: bool,
) -> None:
    snapshot = _diagnostic_shape()
    if prepared:
        snapshot = engine.prepare_reference_engine_snapshot(snapshot)
    evidence = engine.extract_evidence(snapshot)
    result = engine.run_reference_engine(snapshot, max_combos=max_combos).as_dict()
    facts = snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ]

    assert [(row["info_id"], row["value"]) for row in facts] == [
        (200014, DIAGNOSTIC_AVG),
        (200017, 34),
    ]
    assert evidence.total_count == 34
    assert evidence.total_grid_options == (87,)
    assert result["total_grid_range"] == [87, 87, 87]


@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_packet_count_bridge_has_exact_rollback(
    monkeypatch: pytest.MonkeyPatch,
    max_combos: int,
) -> None:
    monkeypatch.setenv(TOTAL_AVG_PACKET_COUNT_BRIDGE_DISABLE, "1")
    snapshot = _diagnostic_shape()
    evidence = engine.extract_evidence(snapshot)
    result = engine.run_reference_engine(snapshot, max_combos=max_combos).as_dict()
    facts = snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ]

    assert [(row["info_id"], row["value"]) for row in facts] == [
        (200014, DIAGNOSTIC_AVG),
    ]
    assert evidence.total_grid_options == ()
    assert result["total_grid_range"] != [87, 87, 87]


@pytest.mark.parametrize("semantic", ("total_avg_cells", "total_item_count"))
@pytest.mark.parametrize("same_value", (True, False))
def test_packet_count_bridge_rejects_duplicate_targeted_raw_and_prepared_facts(
    semantic: str,
    same_value: bool,
) -> None:
    artifact = _raw_diagnostic_shape()
    if semantic == "total_avg_cells":
        duplicate = copy.deepcopy(artifact["public_info_rows"][0])
        if not same_value:
            duplicate["value"] = 3.0
        artifact["public_info_rows"].append(duplicate)
    else:
        values = (34, 34) if same_value else (34, 35)
        artifact["public_info_rows"].extend(
            {
                "sort": sort,
                "info_id": 200017,
                "value": value,
                "value_field": 7,
            }
            for sort, value in enumerate(values, start=1)
        )
    artifact["ui_contract"] = ui_contract_from_artifact(artifact)
    snapshot = json.loads(json.dumps(artifact))

    raw_authority = engine._strict_total_avg_cells_raw_authority(snapshot)
    prepared = engine.prepare_reference_engine_snapshot(snapshot)
    public_info = prepared["ui_contract"]["constraints"]["public_info"]
    prepared_authority = engine._strict_total_avg_cells_prepared_authority(
        public_info
    )
    evidence = engine.extract_evidence(prepared)
    result = engine.run_reference_engine(
        prepared,
        max_combos=8_000,
    ).as_dict()

    assert raw_authority.status == "conflict"
    assert prepared_authority.status == "conflict"
    assert evidence.total_grid_options == ()
    assert result["total_grid_range"] != [87, 87, 87]


@pytest.mark.parametrize(
    "case",
    (
        "missing_packet_count",
        "wrong_mode",
        "wrong_source",
        "non_exact",
        "missing_sequence",
        "bool_count",
        "float_count",
        "missing_structured",
        "structured_conflict",
        "public_count_conflict",
        "stale_average_session",
        "cross_context_session",
        "nonfinite_average",
        "duplicate_average_conflict",
        "non_ahmed",
        "settled",
    ),
)
def test_packet_count_bridge_fail_closed_matrix(case: str) -> None:
    artifact = _raw_diagnostic_shape()
    constraint = artifact["inference_input_constraints"]["total_item_count"]
    if case == "missing_packet_count":
        artifact["inference_input_constraints"].pop("total_item_count")
    elif case == "wrong_mode":
        artifact["inference_input_constraints"]["mode"] = "session_totals_stripped"
    elif case == "wrong_source":
        constraint["source"] = "settlement"
    elif case == "non_exact":
        constraint["confidence"] = "approx"
    elif case == "missing_sequence":
        constraint.pop("sequence")
    elif case == "bool_count":
        constraint["value"] = True
        artifact["structured_ref_inputs"]["total_count"] = True
    elif case == "float_count":
        constraint["value"] = 34.0
        artifact["structured_ref_inputs"]["total_count"] = 34.0
    elif case == "missing_structured":
        artifact.pop("structured_ref_inputs")
    elif case == "structured_conflict":
        artifact["structured_ref_inputs"]["total_count"] = 35
    elif case == "public_count_conflict":
        artifact["public_info_rows"].append(
            {"sort": 1, "info_id": 200017, "value": 35, "value_field": 7}
        )
    elif case == "stale_average_session":
        artifact["public_info_rows"][0]["session_id"] = "other-session"
    elif case == "nonfinite_average":
        artifact["public_info_rows"][0]["value"] = float("nan")
    elif case == "duplicate_average_conflict":
        artifact["public_info_rows"].append(
            {"sort": 3, "info_id": 200014, "value": 3.0, "value_field": 11}
        )
    elif case == "non_ahmed":
        artifact["hero"] = "aisha"
        artifact["hero_id"] = 203
    elif case == "settled":
        artifact["phase"] = "settled"

    artifact["ui_contract"] = ui_contract_from_artifact(artifact)
    if case == "cross_context_session":
        artifact["ui_contract"]["context"]["session_id"] = "other-session"
    snapshot = json.loads(json.dumps(artifact))
    evidence = engine.extract_evidence(snapshot)
    result = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.total_grid_options == ()
    assert result["total_grid_range"] != [87, 87, 87]


def test_packet_count_bridge_rejects_conflicting_prepared_mirrors() -> None:
    for path in (
        ("constraints", "summary", "input_total_item_count"),
        ("constraints", "counts", "input_total_item_count"),
        ("baseline", "posterior", "input_total_item_count"),
        ("baseline", "posterior", "total_item_count_status"),
    ):
        snapshot = _diagnostic_shape()
        target = snapshot["ui_contract"]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = "not_exact" if path[-1].endswith("status") else 35

        evidence = engine.extract_evidence(snapshot)
        result = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()

        assert evidence.total_grid_options == (), path
        assert result["total_grid_range"] != [87, 87, 87], path


def test_packet_count_bridge_prepared_marker_binds_packet_sequence() -> None:
    snapshot = engine.prepare_reference_engine_snapshot(_diagnostic_shape())
    snapshot["inference_input_constraints"]["total_item_count"]["sequence"] = 26

    evidence = engine.extract_evidence(snapshot)
    result = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()

    assert evidence.total_grid_options == ()
    assert result["total_grid_range"] != [87, 87, 87]


def test_packet_count_bridge_survives_summary_api_panel_and_tk(
    tmp_path: Path,
) -> None:
    snapshot = _diagnostic_shape()
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
            timeout=60,
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
    assert summary["ahmed_ref"]["total_grid_range"] == [87, 87, 87]
    assert payload["ahmed_ref"]["total_grid_range"] == [87, 87, 87]
    assert tk_row["ref_v0"]["total_grid_range"] == [87, 87, 87]
    public_info = snapshot["ui_contract"]["constraints"]["public_info"]
    assert public_info["public_numeric_summary"] == "全均格 2.55 / 总件数 34"
