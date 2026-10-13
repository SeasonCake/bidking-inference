from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "calibration" / "rebuild_items_droppable.py"


def _module():
    spec = importlib.util.spec_from_file_location("rebuild_items_droppable", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalog_row() -> dict[str, object]:
    return {
        "item_id": 1036006,
        "name": "世界冠军奖杯",
        "description": "历史活动藏品",
        "name_key": "itemName_1046010",
        "desc_key": "itemDesc_1046010",
        "quality": 6,
        "quality_color": "red",
        "value": 7_202_026,
        "shape_w": 2,
        "shape_h": 2,
        "tags": [101],
        "allowed_shelves": [0],
        "icon_name": "icon_1036006",
        "model_name": "Cube",
    }


def test_stale_existing_row_is_rebuilt_from_current_catalog() -> None:
    module = _module()
    authoritative = _catalog_row()
    stale = {**authoritative, "quality": 5, "value": 1, "_droppable_src": "orig"}

    row = module._build_authoritative_row(
        1036006,
        old_by={1036006: stale},
        catalog={1036006: authoritative},
        table={},
    )

    assert row is not None
    assert row["quality"] == 6
    assert row["quality_color"] == "red"
    assert row["value"] == 7_202_026
    assert row["_droppable_src"] == "catalog_mem"


def test_semantically_equal_existing_row_keeps_its_provenance() -> None:
    module = _module()
    authoritative = _catalog_row()
    existing = {**authoritative, "_droppable_src": "verified-old"}

    row = module._build_authoritative_row(
        1036006,
        old_by={1036006: existing},
        catalog={1036006: authoritative},
        table={},
    )

    assert row == existing
