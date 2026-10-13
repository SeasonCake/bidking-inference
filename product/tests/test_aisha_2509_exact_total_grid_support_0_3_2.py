"""Aisha 2509: an exact item count must not harden an unmeasured total grid.

The fixture is sanitized from the bidding prefix ending at action 100115 in
2509:9000000000000045.  It contains no settlement row or settlement truth.
"""

from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
import threading
from collections import defaultdict
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
from ahmad_live_panel_server import (  # noqa: E402
    ServerConfig,
    make_handler,
    summarize_snapshot,
)
from ahmad_ref_engine import (  # noqa: E402
    QUALITY_KEYS,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from ahmad_tk_overlay import _summary_diagnostic_row  # noqa: E402


ROLLBACK = "BIDKING_DISABLE_AISHA_EXACT_TOTAL_GRID_SUPPORT"
NOTE = "aisha_exact_total_layout_grid_support:prior174:layout183:hi192"
QUALIFICATION = "postupdate_unknown_warehouse_source"


def _field_update(path: list[str], value: int | float, sequence: int) -> dict:
    return {
        "confidence": "exact",
        "event_kind": "tool_revealed",
        "path": path,
        "phase": "bidding",
        "sequence": sequence,
        "source": "packet",
        "value": value,
    }


def _minimap_item(
    quality: int,
    row: int,
    col: int,
    width: int,
    height: int,
    cells: int,
    local_index: int,
    *,
    item_id: int | None = None,
    value: int | None = None,
) -> dict:
    item = {
        "quality": quality,
        "row": row,
        "col": col,
        "width": width,
        "height": height,
        "cells": cells,
        "local_index": local_index,
        "shape_key": f"{width}{height}",
    }
    if item_id is not None:
        item["item_id"] = item_id
    if value is not None:
        item["value"] = value
    return item


def _minimap_items() -> list[dict]:
    return [
        _minimap_item(1, 19, 3, 1, 3, 3, 182),
        _minimap_item(1, 2, 10, 1, 1, 1, 19),
        _minimap_item(1, 1, 10, 1, 1, 1, 9),
        _minimap_item(1, 10, 7, 1, 1, 1, 96),
        _minimap_item(1, 2, 4, 1, 4, 4, 13),
        _minimap_item(1, 8, 10, 1, 1, 1, 79),
        _minimap_item(2, 16, 5, 1, 1, 1, 154),
        _minimap_item(2, 17, 5, 3, 1, 3, 164),
        _minimap_item(2, 5, 1, 1, 1, 1, 40),
        _minimap_item(2, 11, 10, 1, 1, 1, 109),
        _minimap_item(2, 14, 6, 1, 1, 1, 135),
        _minimap_item(2, 15, 1, 1, 1, 1, 140),
        _minimap_item(2, 1, 9, 1, 1, 1, 8),
        _minimap_item(2, 9, 10, 1, 1, 1, 89),
        _minimap_item(2, 17, 10, 1, 1, 1, 169),
        _minimap_item(3, 3, 9, 1, 1, 1, 28, item_id=1053007, value=1450),
        _minimap_item(6, 8, 8, 1, 1, 1, 77, item_id=1056007, value=71500),
        _minimap_item(3, 17, 8, 2, 2, 4, 167, item_id=1053008, value=5040),
    ]


def _target_snapshot(
    *,
    qualification: str = QUALIFICATION,
) -> dict:
    eligible = qualification == "official_random_warehouse"
    avg_q4 = 3.7272727489471436
    quote_policy = "normal" if eligible else "advisory_only_no_clamp"
    warehouse_source = {
        "qualification": qualification,
        "official_distribution_eligible": eligible,
        "quote_policy": quote_policy,
        "quarantine_required": False,
    }
    minimap_items = _minimap_items()
    skill_rows = [
        {
            "sort": 9,
            "skill_id": 1001034,
            "hero_id": 103,
            "revealed_items": 6,
            "revealed_items_detail": copy.deepcopy(minimap_items[:6]),
        },
        {
            "sort": 9,
            "skill_id": 1001033,
            "hero_id": 103,
            "revealed_items": 9,
            "revealed_items_detail": copy.deepcopy(minimap_items[6:15]),
        },
    ]
    action_rows = [
        {
            "sort": 11,
            "action_id": 100115,
            "result": 61,
            "result_field": 7,
            "source_kind": "direct_action_response",
        },
        {
            "sort": 3,
            "action_id": 100112,
            "result": avg_q4,
            "result_field": 11,
            "source_kind": "direct_action_response",
        },
    ]
    snapshot = {
        "session_id": "2509:9000000000000045",
        "hero": "aisha",
        "map_id": 2509,
        "phase": "bidding",
        "round": 2,
        "warehouse_source": warehouse_source,
        "warehouse_source_qualification": qualification,
        "official_distribution_eligible": eligible,
        "quote_policy": quote_policy,
        "skill_reveal_rows": skill_rows,
        "structured_ref_inputs": {
            "source": "monitor_pre_settlement_field_updates",
            "hero": "aisha",
            "total_count": 61,
            "avg_cells": {"q4": avg_q4},
            "split_counts": {"white": 6, "green": 9},
            "split_quality_cells": {"white": 11, "green": 11},
            "field_updates": [
                _field_update(["bucket", "4", "avg_cells"], avg_q4, 9),
                _field_update(["bucket_split", "white", "total_cells"], 11, 9),
                _field_update(["bucket_split", "white", "count"], 6, 9),
                _field_update(["bucket_split", "green", "total_cells"], 11, 9),
                _field_update(["bucket_split", "green", "count"], 9, 9),
                _field_update(["session", "total_item_count"], 61, 11),
            ],
            "minimap_items": minimap_items,
        },
        "public_info_rows": [
            {
                "sort": 9,
                "info_id": 200051,
                "revealed_items_detail": [
                    {
                        "quality": 3,
                        "cells": 1,
                        "item_id": 1053007,
                        "value": 1450,
                        "local_index": 28,
                        "shape_code": 11,
                    },
                    {
                        "quality": 6,
                        "cells": 1,
                        "item_id": 1056007,
                        "value": 71500,
                        "local_index": 77,
                        "shape_code": 11,
                    },
                    {
                        "quality": 3,
                        "cells": 4,
                        "item_id": 1053008,
                        "value": 5040,
                        "local_index": 167,
                        "shape_code": 22,
                    },
                ],
            }
        ],
        "action_result_rows": action_rows,
    }
    snapshot["ui_contract"] = {
        "context": {
            "session_id": snapshot["session_id"],
            "hero": "aisha",
            "map_id": 2509,
            "model_map_id": 2509,
            "phase": "bidding",
            "round": 2,
            "observed_round": 1,
            "action_round": 2,
            "warehouse_source": copy.deepcopy(warehouse_source),
            "warehouse_source_qualification": qualification,
            "official_distribution_eligible": eligible,
            "quote_policy": quote_policy,
        },
        "source": {
            "warehouse_source": copy.deepcopy(warehouse_source),
            "warehouse_source_qualification": qualification,
            "official_distribution_eligible": eligible,
            "quote_policy": quote_policy,
            "created_at": 1.0,
        },
        "constraints": {"public_info": {}},
        "actions": {"results": copy.deepcopy(action_rows)},
        "minimap": {"items": copy.deepcopy(minimap_items)},
    }
    return snapshot


def _run(snapshot: dict, *, max_combos: int = 8_000) -> dict:
    prepared = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    return run_reference_engine(prepared, max_combos=max_combos).as_dict()


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
    "combo_count": result["combo_count"],
    "quotes": [result["conservative"], result["balanced"], result["aggressive"]],
    "total_grid_range": result["total_grid_range"],
    "quality_count_ranges": result["quality_count_ranges"],
    "quality_cells_ranges": result["quality_cells_ranges"],
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
        timeout=60,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_2509_exact_total_retains_physical_grid_support_in_fresh_process(
    max_combos: int,
) -> None:
    snapshot = _target_snapshot()
    fixed = _fresh_process(snapshot, max_combos=max_combos, rollback=False)
    legacy = _fresh_process(snapshot, max_combos=max_combos, rollback=True)

    assert fixed["status"] == legacy["status"] == "count_prior"
    assert fixed["combo_count"] == 85
    assert legacy["combo_count"] == 31
    assert fixed["quotes"] == [713_690, 1_152_898, 1_384_296]
    assert legacy["quotes"] == [689_521, 1_112_719, 1_312_425]
    assert fixed["quality_count_ranges"] == legacy["quality_count_ranges"]
    assert fixed["quality_cells_ranges"] == legacy["quality_cells_ranges"]
    assert fixed["total_grid_range"] == legacy["total_grid_range"] == [178, 193, 197]
    assert NOTE in fixed["notes"]
    assert NOTE not in legacy["notes"]
    assert snapshot["phase"] == "bidding"
    assert "settlement_welfare" not in snapshot
    assert "settlement_items" not in snapshot["ui_contract"]["minimap"]


def _log_mass(rows: list) -> float:
    maximum = max(row.weight for row in rows)
    return maximum + math.log(math.fsum(math.exp(row.weight - maximum) for row in rows))


def test_expansion_preserves_each_count_rows_mass_and_exact_item_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = engine._aisha_exact_total_layout_grid_support
    expanded_calls: list[tuple[list, list]] = []

    def audited(combos, **kwargs):
        expanded = original(combos, **kwargs)
        if len(expanded) > len(combos):
            expanded_calls.append((list(combos), list(expanded)))
        return expanded

    monkeypatch.setattr(engine, "_aisha_exact_total_layout_grid_support", audited)
    result = _run(_target_snapshot())

    assert result["combo_count"] == 85
    assert len(expanded_calls) == 1
    before, after = expanded_calls[0]
    assert all(sum(row.counts.values()) == 61 for row in [*before, *after])

    before_by_counts = defaultdict(list)
    after_by_counts = defaultdict(list)
    before_grids = defaultdict(set)
    for row in before:
        key = tuple(int(row.counts[name]) for name in QUALITY_KEYS)
        before_by_counts[key].append(row)
        before_grids[key].add(tuple(round(float(row.grids[name]), 6) for name in QUALITY_KEYS))
    for row in after:
        key = tuple(int(row.counts[name]) for name in QUALITY_KEYS)
        after_by_counts[key].append(row)

    assert before_by_counts.keys() == after_by_counts.keys()
    for key in before_by_counts:
        assert _log_mass(after_by_counts[key]) == pytest.approx(
            _log_mass(before_by_counts[key]),
            abs=1e-12,
        )
        for row in after_by_counts[key]:
            signature = tuple(round(float(row.grids[name]), 6) for name in QUALITY_KEYS)
            if signature in before_grids[key]:
                continue
            assert int(round(row.total_grid)) in {183, 192}
            for name in QUALITY_KEYS:
                cells = float(row.grids[name])
                assert cells == pytest.approx(round(cells), abs=1e-6)
                assert engine._can_compose_grid_total_with_q6_catalog(
                    name,
                    int(row.counts[name]),
                    int(round(cells)),
                )


@pytest.mark.parametrize(
    "mutation",
    ["no_total_count", "hard_total", "no_layout", "other_hero", "settled", "all_cells_exact"],
)
def test_out_of_scope_or_hard_states_are_rollback_noop(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _target_snapshot()
    structured = snapshot["structured_ref_inputs"]
    if mutation == "no_total_count":
        structured.pop("total_count")
        structured["field_updates"] = [
            row for row in structured["field_updates"]
            if row["path"] != ["session", "total_item_count"]
        ]
        snapshot["action_result_rows"] = [
            row for row in snapshot["action_result_rows"] if row["action_id"] != 100115
        ]
    elif mutation == "hard_total":
        structured["total_cells"] = 193
        structured["field_updates"].append(
            _field_update(["session", "warehouse_total_cells"], 193, 12)
        )
    elif mutation == "no_layout":
        structured["minimap_items"] = []
        snapshot["ui_contract"]["minimap"]["items"] = []
    elif mutation == "other_hero":
        snapshot["hero"] = structured["hero"] = "ahmed"
        snapshot["ui_contract"]["context"]["hero"] = "ahmed"
    elif mutation == "settled":
        snapshot["phase"] = "settled"
        snapshot["ui_contract"]["context"]["phase"] = "settled"
    else:
        structured["quality_cells"] = {
            "q1": 22,
            "q3": 32,
            "q4": 82,
            "q5": 26,
            "q6": 15,
        }

    monkeypatch.delenv(ROLLBACK, raising=False)
    fixed = _run(snapshot)
    monkeypatch.setenv(ROLLBACK, "1")
    legacy = _run(snapshot)

    assert fixed == legacy
    assert not any(str(note).startswith("aisha_exact_total_layout_grid_support") for note in fixed.get("notes", []))


@pytest.mark.parametrize(
    "qualification",
    [QUALIFICATION, "player_custom_warehouse", "official_random_warehouse"],
)
def test_source_pool_is_not_reclassified(
    qualification: str,
    tmp_path: Path,
) -> None:
    summary = summarize_snapshot(
        _target_snapshot(qualification=qualification),
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert summary["context"]["warehouse_source_qualification"] == qualification
    assert summary["context"]["warehouse_source"][
        "official_distribution_eligible"
    ] is (qualification == "official_random_warehouse")
    assert NOTE in summary["ahmed_ref"]["notes"]


def test_summary_loopback_api_and_tk_keep_the_same_quote(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _target_snapshot()
    snapshot_path = tmp_path / "latest_snapshot.json"
    summary = summarize_snapshot(copy.deepcopy(snapshot), snapshot_path=snapshot_path)

    assert summary["ahmed_ref"]["balanced"] == 1_152_898
    assert summary["reference"]["balanced"] == "1,152,898"
    diagnostic = _summary_diagnostic_row(
        summary,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )
    assert diagnostic["reference"]["balanced"] == "1,152,898"

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
    assert payload["ahmed_ref"]["balanced"] == 1_152_898
    assert payload["reference"]["balanced"] == "1,152,898"
