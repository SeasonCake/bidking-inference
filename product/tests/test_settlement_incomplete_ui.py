from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def _server_module():
    path = ROOT / "hero_ref" / "tools" / "ahmad_live_panel_server.py"
    spec = importlib.util.spec_from_file_location(
        "ahmad_live_panel_server_partial_settlement_test",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeRefResult:
    def as_dict(self) -> dict[str, object]:
        return {
            "status": "ok",
            "source": "ref_v0",
            "notes": [],
            "conservative": 600_000,
            "balanced": 664_358,
            "aggressive": 720_000,
            "value_p25": 600_000,
            "value_p50": 664_358,
            "value_p75": 720_000,
            "combo_count": 1,
            "red_count_range": [0, 1, 2],
            "red_cells_range": [0, 4, 8],
            "red_value_range": [0, 180_000, 360_000],
            "total_grid_range": [70, 80, 90],
            "quality_count_ranges": {},
            "quality_cells_ranges": {},
            "evidence": {"hero": "victor", "total_count": 30},
        }


def _snapshot(*, partial: bool) -> dict[str, object]:
    truth: dict[str, object] = {
        "available": True,
        "complete": not partial,
        "partial": partial,
        "captured_items": 17 if partial else 30,
        "captured_cells": 38,
        "declared_items": 30,
        "declared_cells": 38,
        "total_value": 107_049,
        "total_items": 17 if partial else 30,
        "total_cells": 38,
        "q6": {"count": 0, "cells": 0, "value": 0},
        "top_item": {"name": "captured item", "value": 63_450},
    }
    snapshot: dict[str, object] = {
        "created_at": time.time(),
        "phase": "settled",
        "inventory_count": 17 if partial else 30,
        "inventory_cells": 38,
        "settlement_incomplete": partial,
        "settlement_parse_residual": 0,
        "final_quality_counts": "q1=1;q2=5;q3=5;q4=3;q5=3;q6=0",
        "final_quality_cells": "q1=1;q2=9;q3=10;q4=12;q5=6;q6=0",
        "structured_ref_inputs": {"hero": "victor", "total_count": 30},
        "ui_contract": {
            "context": {
                "hero": "victor",
                "map_id": 2541,
                "round": 5,
                "phase": "settled",
                "session_id": "2541:9000000000000024",
            },
            "baseline": {
                "decision": {"attack_bid": "664358"},
                "posterior": {},
            },
            "constraints": {"summary": {}, "counts": {}, "public_info": {}},
            "actions": {"results": []},
            "source": {"created_at": time.time()},
            "truth": truth,
            "minimap": {
                "status": "available",
                "layout_source": "settlement_inventory",
                "known_items": 17,
                "drawable_items": 1,
                "final_total_items": 17,
                "columns": 10,
                "items": [
                    {
                        "row": 1,
                        "col": 1,
                        "width": 1,
                        "height": 1,
                        "quality": 5,
                        "runtime_id": 1,
                        "item_id": 1095007,
                        "source": "settlement_inventory",
                        "layout_source": "settlement_inventory",
                        "render_mode": "footprint",
                    }
                ],
            },
        },
    }
    if partial:
        snapshot["settlement_incomplete"] = True
        snapshot["settlement_incomplete_detail"] = {
            "captured_items": 17,
            "captured_cells": 38,
            "declared_items": 30,
            "declared_cells": 38,
            "captured_value": 107_049,
            "source": "action:100115",
        }
    return snapshot


def _configure_fake_engine(module, monkeypatch) -> None:
    monkeypatch.setattr(
        module,
        "hero_should_run_scheduled_inference",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        module,
        "run_reference_engine",
        lambda *_args, **_kwargs: _FakeRefResult(),
    )
    monkeypatch.setattr(
        module,
        "_pre_settlement_ref_result",
        lambda _snapshot: _FakeRefResult().as_dict(),
    )


def test_partial_settlement_renders_lower_bounds_and_withholds_zero_red(monkeypatch, tmp_path: Path) -> None:
    module = _server_module()
    _configure_fake_engine(module, monkeypatch)

    result = module.summarize_snapshot(
        _snapshot(partial=True),
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert result["reference"]["source"] == "settlement_partial"
    assert result["reference"]["price_titles"] == {
        "conservative": "估价",
        "balanced": "已知下界",
        "aggressive": "完整性",
    }
    assert result["reference"]["conservative"] == "664,358"
    assert result["reference"]["balanced"] == "≥107,049"
    assert result["reference"]["aggressive"] == "17/30件"
    assert result["reference"]["action"] == "结算不完整"
    assert result["reference"]["current_highest"] == "已捕获 17/30 件"
    assert result["reference"]["ref_minus_v3_balanced"] == "-"
    assert result["red"]["count_range"] == "未知"
    assert result["red"]["cells_range"] == "未知"
    assert result["red"]["value_range"] == "未知"
    assert result["red"]["quality_count_summary"] == "已见 紫3件/12格 · 金3件/6格"
    assert result["red"]["low_quality_count_probability_hover"] == ""
    assert result["truth"]["complete"] is False
    assert result["truth"]["partial"] is True
    assert result["truth"]["total_value"] == "≥107,049"
    assert result["truth"]["total_items"] == "17/30"
    assert result["truth"]["total_cells"] == "≥38"
    assert result["truth"]["q6"] == {
        "count": "未知",
        "cells": "未知",
        "value": "未知",
    }
    assert result["minimap"]["partial"] is True
    assert result["minimap"]["captured_items"] == 17
    assert result["minimap"]["declared_items"] == 30
    assert result["minimap"]["captured_cells"] == 38
    assert result["minimap"]["declared_cells"] == 38
    assert result["minimap"]["summary_text"] == (
        "已捕获 17/30 件 · settlement_inventory"
    )
    assert any(flag["label"] == "结算不完整" for flag in result["flags"])


def test_complete_settlement_keeps_exact_truth_and_delta(monkeypatch, tmp_path: Path) -> None:
    module = _server_module()
    _configure_fake_engine(module, monkeypatch)

    result = module.summarize_snapshot(
        _snapshot(partial=False),
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert result["reference"]["source"] == "settlement"
    assert result["reference"]["price_titles"] == {
        "conservative": "估价",
        "balanced": "结算",
        "aggressive": "差值",
    }
    assert result["reference"]["balanced"] == "107,049"
    assert result["reference"]["aggressive"] == "-557,309"
    assert result["reference"]["action"] == "结算完成"
    assert result["red"]["count_range"] == "0 件"
    assert result["red"]["cells_range"] == "0 格"
    assert result["red"]["value_range"] == "0"
    assert result["truth"]["complete"] is True
    assert result["truth"]["partial"] is False
    assert result["truth"]["total_value"] == 107_049
    assert result["truth"]["q6"] == {"count": 0, "cells": 0, "value": 0}


def test_available_but_unconfirmed_settlement_is_not_promoted_by_ui(monkeypatch, tmp_path: Path) -> None:
    module = _server_module()
    _configure_fake_engine(module, monkeypatch)
    snapshot = _snapshot(partial=False)
    truth = snapshot["ui_contract"]["truth"]
    truth["complete"] = False
    truth["partial"] = False

    result = module.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert result["reference"]["source"] == "settlement_partial"
    assert result["reference"]["action"] == "结算不完整"
    assert result["red"]["count_range"] == "未知"
    assert result["red"]["cells_range"] == "未知"
    assert result["red"]["value_range"] == "未知"
    assert result["truth"]["complete"] is False
    assert result["truth"]["partial"] is True
