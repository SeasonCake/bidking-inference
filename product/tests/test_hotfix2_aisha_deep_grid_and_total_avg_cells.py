"""Hotfix2 regressions for deep Aisha grid and strict all-items average cells."""

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
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_ref_engine as engine  # noqa: E402
from ahmad_live_panel_server import (  # noqa: E402
    ServerConfig,
    make_handler,
    summarize_snapshot,
)
from ahmad_tk_overlay import _summary_diagnostic_row  # noqa: E402
from audit_aisha_gap import _events_through_sort  # noqa: E402
from bidking_lab.live.fatbeans import (  # noqa: E402
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)
from bidking_lab.live.monitor import (  # noqa: E402
    _ahmad_ref_inputs_from_batches,
    _public_info_rows,
    build_monitor_artifact_from_events,
    load_monitor_tables,
)
from bidking_lab.runtime.snapshot import (  # noqa: E402
    TOTAL_AVG_COUNT_UI_MIRROR_DISABLE,
    ui_contract_from_artifact,
)


PRODUCT_DATA = Path(
    r"C:\Users\example\Desktop\bidking-live-v0.3.2-hotfix1-encrypted\data"
)
REAL_2401 = PRODUCT_DATA / (
    "logs/live/raw/archive/reset/"
    "wss_live_2026-08-09_215141_2401_1902361429479948_reset.json"
)
REAL_2408 = PRODUCT_DATA / (
    "logs/live/raw/archive/reset/"
    "wss_live_2026-08-09_220922_2408_1902361430161665_reset.json"
)
REAL_2401_SHA256 = "7992cbf53db2d4313f42d31afd2a3b89bac2decd2c362a04e31871aa86f59ab9"
REAL_2408_SHA256 = "c502c678169cd7d8ab775dec45cfa3c983c5f4d294ad2a91779acc49cf3192bf"
DEEP_ROLLBACK = engine.AISHA_DEEP_COUNT_GRID_CENTER_DISABLE
CELL_ROLLBACK = "BIDKING_DISABLE_QUALITY_CELL_COUNT_FLOOR_HOTFIX2"


def _require_real(path: Path, expected_sha256: str) -> None:
    if not path.is_file():
        pytest.skip(f"local-only real replay missing: {path.name}")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256


@pytest.fixture(scope="module")
def real_2401_snapshots() -> tuple[dict, dict]:
    _require_real(REAL_2401, REAL_2401_SHA256)
    events = parse_fatbeans_capture(REAL_2401)
    tables = load_monitor_tables()

    def build(sort_id: int) -> dict:
        return build_monitor_artifact_from_events(
            _events_through_sort(events, sort_id),
            file="qualified-local-replay",
            tables=tables,
            n_trials=5,
            roi_trials=5,
            shadow_trials=1,
            run_debug_shadows=False,
        )

    # seq9 is the reported R2 red light; seq11 is the same view after exact total_count=42 arrives.
    return build(9), build(11)


def _all_published_quality_cells_cover_counts(result: dict) -> None:
    for key, counts in result["quality_count_ranges"].items():
        cells = result["quality_cells_ranges"].get(key)
        if cells is None:
            continue
        for count, cell in zip(counts, cells, strict=True):
            if count is not None and cell is not None:
                assert cell >= count, (key, counts, cells)


@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_real_2401_r2_deep_band_brackets_118_and_cells_remain_physical(
    real_2401_snapshots: tuple[dict, dict],
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DEEP_ROLLBACK, raising=False)
    monkeypatch.delenv(CELL_ROLLBACK, raising=False)
    before_count, after_count = real_2401_snapshots

    first = engine.run_reference_engine(before_count, max_combos=max_combos).as_dict()
    second = engine.run_reference_engine(after_count, max_combos=max_combos).as_dict()

    assert first["total_grid_range"] == [94, 110, 120]
    assert second["total_grid_range"] == [94, 120, 130]
    assert first["total_grid_range"][0] <= 118 <= first["total_grid_range"][2]
    assert second["total_grid_range"][0] <= 118 <= second["total_grid_range"][2]
    assert first["quality_count_ranges"]["q6"] == [1, 3, 5]
    assert first["quality_cells_ranges"]["q6"] == [1, 3, 17]
    _all_published_quality_cells_cover_counts(first)
    _all_published_quality_cells_cover_counts(second)
    assert "aisha_deep_visibility_multi_anchor" in first["notes"]
    assert "aisha_deep_count_grid_soft_center:120" in second["notes"]


def test_real_2401_default_and_rollback_are_clean_ab(
    real_2401_snapshots: tuple[dict, dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before_count, after_count = real_2401_snapshots
    monkeypatch.setenv(DEEP_ROLLBACK, "1")
    monkeypatch.setenv(CELL_ROLLBACK, "1")

    first = engine.run_reference_engine(before_count, max_combos=8_000).as_dict()
    second = engine.run_reference_engine(after_count, max_combos=8_000).as_dict()

    assert first["total_grid_range"] == [100, 100, 104]
    assert first["quality_cells_ranges"]["q6"] == [0, 0, 17]
    assert second["total_grid_range"] == [120, 120, 120]
    assert "aisha_deep_count_grid_soft_center:120" not in second["notes"]


def test_published_cell_floor_does_not_create_a_value_from_none(
    real_2401_snapshots: tuple[dict, dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CELL_ROLLBACK, raising=False)
    result = engine.run_reference_engine(real_2401_snapshots[0], max_combos=8_000)
    count_ranges = dict(result.quality_count_ranges)
    cell_ranges = dict(result.quality_cells_ranges)
    count_ranges["q5"] = (2, 3, 4)
    cell_ranges["q5"] = (None, 2, 4)

    floored = engine._floor_published_quality_cells_to_counts(
        replace(
            result,
            quality_count_ranges=count_ranges,
            quality_cells_ranges=cell_ranges,
        )
    )

    assert floored.quality_cells_ranges["q5"] == (None, 3, 4)


def _strict_total_average_snapshot(
    avg_cells: float,
    *,
    total_count_value: object = 36,
) -> dict:
    source = {
        "qualification": "official_random_warehouse",
        "official_distribution_eligible": True,
        "quote_policy": "advisory_only_no_clamp",
        "quarantine_required": False,
    }
    rows = [
        {
            "sort": 2,
            "info_id": 200014,
            "value": avg_cells,
            "value_field": 11,
        },
        {
            "sort": 1,
            "info_id": 200017,
            "value": total_count_value,
            "value_field": 7,
        },
    ]
    artifact = {
        "session_id": "synthetic-ahmed-count36-total-avg-cells",
        "hero": "ahmed",
        "hero_id": 204,
        "map_id": 2401,
        "model_map_id": 2401,
        "phase": "bidding",
        "round": 2,
        "action_round": 2,
        "observed_round": 1,
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": source["qualification"],
        "official_distribution_eligible": True,
        "quote_policy": source["quote_policy"],
        "public_info_rows": rows,
        "inference_input_constraints": {"total_item_count": 36},
        "structured_ref_inputs": {"total_count": 36},
        "minimap_grid_items": [],
        "category_grid_items": [],
        "panel": {},
        "model_eval": {},
        "v2_posterior_rows": [],
        "bid_rows": [],
        "warehouse_rows": [],
        "tool_rows": [],
    }
    artifact["ui_contract"] = ui_contract_from_artifact(artifact)
    # Product consumers read the monitor artifact from JSON; normalize tuple-valued UI metadata
    # the same way before exercising prepare/extract/run.
    return json.loads(json.dumps(artifact))


class _HostileCount(int):
    calls = 0

    def __int__(self) -> int:
        type(self).calls += 1
        raise AssertionError("hostile count coercion executed")

    def __float__(self) -> float:
        type(self).calls += 1
        raise AssertionError("hostile count coercion executed")


@pytest.mark.parametrize("avg_cells", (3.33, 3.3333332538604736))
@pytest.mark.parametrize("prepared", (False, True))
@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_count36_total_average_quantization_has_unique_120_support(
    avg_cells: float,
    prepared: bool,
    max_combos: int,
) -> None:
    snapshot = _strict_total_average_snapshot(avg_cells)
    if prepared:
        snapshot = engine.prepare_reference_engine_snapshot(snapshot)
    evidence = engine.extract_evidence(snapshot)
    result = engine.run_reference_engine(snapshot, max_combos=max_combos).as_dict()

    assert evidence.total_count == 36
    assert evidence.total_grid_options == (120,)
    assert result["total_grid_range"] == [120, 120, 120]
    facts = snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ]
    assert [(row["info_id"], row["value"]) for row in facts] == [
        (200014, avg_cells),
        (200017, 36),
    ]


def test_real_2408_is_an_adjacent_negative_not_the_count36_capture() -> None:
    _require_real(REAL_2408, REAL_2408_SHA256)
    events = parse_fatbeans_capture(REAL_2408)
    batches = live_batches_from_fatbeans_events(events)
    bridge = _ahmad_ref_inputs_from_batches(batches, hero="ahmed") or {}
    rows = _public_info_rows(events, {})

    assert bridge["total_count"] == 60
    assert bridge["avg_cells"] == {
        "q1": pytest.approx(2.200000047683716),
        "q3": pytest.approx(2.117647171020508),
        "q4": pytest.approx(3.1500000953674316),
        "q5": pytest.approx(3.375),
    }
    assert not any(row.get("info_id") in (200014, 200017) for row in rows)


def test_count36_total_average_survives_runtime_panel_api_and_tk(
    tmp_path: Path,
) -> None:
    snapshot = _strict_total_average_snapshot(3.3333332538604736)
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
    assert summary["ahmed_ref"]["total_grid_range"] == [120, 120, 120]
    assert payload["ahmed_ref"]["total_grid_range"] == [120, 120, 120]
    assert tk_row["ref_v0"]["total_grid_range"] == [120, 120, 120]
    public_info = snapshot["ui_contract"]["constraints"]["public_info"]
    assert public_info["public_numeric_summary"] == "全均格 3.33 / 总件数 36"


def test_count36_runtime_projection_has_exact_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOTAL_AVG_COUNT_UI_MIRROR_DISABLE, "1")
    snapshot = _strict_total_average_snapshot(3.3333332538604736)
    evidence = engine.extract_evidence(snapshot)
    result = engine.run_reference_engine(snapshot, max_combos=8_000).as_dict()
    facts = snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ]

    assert [(row["info_id"], row["value"]) for row in facts] == [
        (200014, 3.3333332538604736),
    ]
    assert evidence.total_grid_options == ()
    assert result["total_grid_range"] == [80, 84, 103]


def test_runtime_count_projection_rejects_scalar_subclass_without_coercion() -> None:
    _HostileCount.calls = 0
    snapshot = _strict_total_average_snapshot(
        3.3333332538604736,
        total_count_value=_HostileCount(36),
    )
    facts = snapshot["ui_contract"]["constraints"]["public_info"][
        "public_numeric_facts"
    ]

    assert [row["info_id"] for row in facts] == [200014]
    assert _HostileCount.calls == 0


def test_summary_api_panel_and_tk_read_the_same_real_2401_grid(
    real_2401_snapshots: tuple[dict, dict],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DEEP_ROLLBACK, raising=False)
    monkeypatch.delenv(CELL_ROLLBACK, raising=False)
    # The screenshot signature is the R2 snapshot after action 100115 supplies total_count=42:
    # rollback total=100/100/104 and q6 count=0/1/3, cells=0/0/10.  The q5 marginal differs
    # slightly between the shipped hotfix1 population and this exact hotfix2 baseline.
    snapshot = real_2401_snapshots[1]
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

    assert summary["ahmed_ref"]["total_grid_range"] == [94, 120, 130]
    assert summary["ahmed_ref"]["quality_count_ranges"]["q6"] == [0, 1, 3]
    assert summary["ahmed_ref"]["quality_cells_ranges"]["q6"] == [0, 4, 11]
    assert payload["ahmed_ref"]["total_grid_range"] == [94, 120, 130]
    assert payload["ahmed_ref"]["quality_cells_ranges"]["q6"] == [0, 4, 11]
    assert tk_row["ref_v0"]["total_grid_range"] == [94, 120, 130]


def test_real_2401_screenshot_prefix_product_rollback_is_exact(
    real_2401_snapshots: tuple[dict, dict],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DEEP_ROLLBACK, "1")
    monkeypatch.setenv(CELL_ROLLBACK, "1")
    snapshot = real_2401_snapshots[1]
    summary = summarize_snapshot(snapshot, snapshot_path=tmp_path / "snapshot.json")
    result = summary["ahmed_ref"]

    assert result["total_grid_range"] == [100, 100, 104]
    assert result["quality_count_ranges"]["q6"] == [0, 1, 3]
    assert result["quality_cells_ranges"]["q6"] == [0, 0, 10]
