from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALIBRATION = ROOT / "scripts" / "calibration"
if str(CALIBRATION) not in sys.path:
    sys.path.insert(0, str(CALIBRATION))

import aisha_calib as ac  # noqa: E402
import refresh_current_normalization_outputs as refresh  # noqa: E402


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def test_refresh_preserves_support_and_separates_lookup_from_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items_path = tmp_path / "items.json"
    droppable_path = tmp_path / "items_droppable.json"
    items_path.write_text(
        json.dumps(
            [
                {
                    "item_id": 1,
                    "value": 100,
                    "shape_w": 1,
                    "shape_h": 1,
                    "quality": 6,
                    "name": "active",
                },
                {
                    "item_id": 1036006,
                    "value": 1_000,
                    "shape_w": 1,
                    "shape_h": 1,
                    "quality": 6,
                    "name": "retired",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    droppable_path.write_text(
        json.dumps(
            [
                {
                    "item_id": 1,
                    "value": 100,
                    "shape_w": 1,
                    "shape_h": 1,
                    "quality": 6,
                }
            ]
        ),
        encoding="utf-8",
    )
    weights = {
        "2101": {
            "count": 1.0,
            "n_items": 2,
            "items": {
                "1": [0.75, 100, 100],
                "1036006": [0.25, 1_000, 999],
            },
        }
    }
    priors = {
        "2101": {
            "quality_probs": {"q1": 0.0, "q3": 0.0, "q4": 0.0, "q5": 0.0, "q6": 1.0},
            "quality_values": {"q1": 1, "q3": 2, "q4": 3, "q5": 4, "q6": 777},
            "_value_src": {"all": "frozen_truth"},
        }
    }
    weights_raw = _json_bytes(weights)
    priors_raw = _json_bytes(priors)
    monkeypatch.setattr(refresh, "LEGACY_DROP_WEIGHTS_BYTES", len(weights_raw))
    monkeypatch.setattr(
        refresh,
        "LEGACY_DROP_WEIGHTS_SHA256",
        hashlib.sha256(weights_raw).hexdigest(),
    )
    monkeypatch.setattr(refresh, "LEGACY_DROP_WEIGHTS_MAPS", 1)
    monkeypatch.setattr(refresh, "LEGACY_DROP_PRIORS_BYTES", len(priors_raw))
    monkeypatch.setattr(
        refresh,
        "LEGACY_DROP_PRIORS_SHA256",
        hashlib.sha256(priors_raw).hexdigest(),
    )
    monkeypatch.setattr(refresh, "LEGACY_DROP_PRIORS_MAPS", 1)

    saved = {
        name: getattr(ac, name)
        for name in (
            "ITEMS_JSON",
            "ITEMS_DROPPABLE_JSON",
            "_CATALOG",
            "_REFERENCE_ITEM_IDS",
            "_REFERENCE_ENABLE_RETIRED",
            "_SHAPE_FLOOR",
            "_REPLACEMENT",
            "_QMED_PERCELL",
        )
    }
    try:
        outputs, receipt = refresh.refresh_outputs(
            legacy_drop_weights=weights_raw,
            legacy_drop_priors=priors_raw,
            items_json=items_path,
            items_droppable=droppable_path,
        )
    finally:
        for name, value in saved.items():
            setattr(ac, name, value)

    updated_weights = json.loads(outputs["drop_weights_all_maps_0627.json"])
    assert updated_weights["2101"]["items"]["1"] == [0.75, 100, 100]
    assert updated_weights["2101"]["items"]["1036006"] == [0.25, 1_000, 100]
    updated_priors = json.loads(outputs["drop_table_priors_0627.json"])
    assert updated_priors == priors
    red_lookup = json.loads(outputs["red_longtail_normalized_0627.json"])
    assert red_lookup == {"1036006": 100}
    assert receipt["reference"] == {
        "items": 1,
        "retired_world_cup_included": False,
        "localization_aliases_are_item_ids": False,
    }
    assert receipt["rows"] == {"changed_fitted_values": 1, "total": 2}


def test_refresh_rejects_nonfrozen_weight_input(tmp_path: Path) -> None:
    with pytest.raises(refresh.NormalizationRefreshError, match="identity mismatch"):
        refresh.refresh_outputs(
            legacy_drop_weights=b"{}",
            legacy_drop_priors=b"{}",
            items_json=tmp_path / "items.json",
            items_droppable=tmp_path / "droppable.json",
        )
