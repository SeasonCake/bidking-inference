"""Static regression contract for Raven's pre-open settlement-only category facts."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.live import sample_upload  # noqa: E402
import bidking_lab.live.monitor as monitor_module  # noqa: E402
from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansCaptureEvents,
    FatbeansInventoryItem,
    FatbeansStateEvent,
)
from bidking_lab.live.warehouse_source import (  # noqa: E402
    POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
)


def _monitor_module():
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(
        "raven_settlement_protocol_monitor",
        ROOT / "scripts" / "run_fatbeans_webhook_monitor.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fact() -> dict:
    return {
        "schema": "settlement_category_fact_v1",
        "physical_map_id": 5101,
        "model_map_id": 2501,
        "warehouse_generation": POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        "category_authority_generation": "category-safe",
        "settled_items_v1": [
            {"item_id": 1010001, "quality": 1, "primary_category": 101},
            {"item_id": 1060001, "quality": 6, "primary_category": 106},
        ],
    }


def _artifact() -> dict:
    return {
        "phase": "settled",
        "map_id": 5101,
        "model_map_id": 2501,
        "warehouse_source_generation": POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        "session_id": "5101:current",
        "final_quality_counts": {"q1": 1, "q6": 1},
        "settlement_category_fact_v1": _fact(),
    }


def test_closed_settlement_category_fact_reaches_receipt_and_upload_candidate() -> None:
    artifact = _artifact()
    receipt = _monitor_module()._settlement_category_fact_receipt(artifact)
    candidate = sample_upload._settlement_category_upload_candidate(artifact)

    assert receipt is not None
    assert candidate is not None
    assert candidate["physical_map_id"] == 5101
    assert candidate["model_map_id"] == 2501
    assert candidate["settled_items_v1"][1]["quality"] == 6
    assert "session_id" not in receipt
    assert "runtime_id" not in candidate["settled_items_v1"][0]


def test_open_or_tampered_settlement_never_becomes_category_upload_candidate() -> None:
    bidding = _artifact()
    bidding["phase"] = "bidding"
    tampered = _artifact()
    tampered["settlement_category_fact_v1"]["model_map_id"] = 5102

    assert sample_upload._settlement_category_upload_candidate(bidding) is None
    assert sample_upload._settlement_category_upload_candidate(tampered) is None
    assert _monitor_module()._settlement_category_fact_receipt(bidding) is None
    assert _monitor_module()._settlement_category_fact_receipt(tampered) is None


def test_category_candidate_rejects_bad_quality_duplicate_shape_and_oversize() -> None:
    for bad_row in (
        {"item_id": 1, "quality": 0, "primary_category": 101},
        {"item_id": 1, "quality": 1, "primary_category": 101, "runtime_id": 9},
    ):
        artifact = _artifact()
        artifact["settlement_category_fact_v1"]["settled_items_v1"] = [bad_row]
        assert sample_upload._settlement_category_upload_candidate(artifact) is None

    oversized = _artifact()
    oversized["settlement_category_fact_v1"]["settled_items_v1"] *= 257
    assert sample_upload._settlement_category_upload_candidate(oversized) is None


class _CategoryAuthority:
    generation = "category-authority-safe"

    def require_random_drop_category(self, item_id: int) -> int:
        if item_id == 9_999_999:
            raise monitor_module.ItemCategoryAuthorityError("unknown item")
        return 101


class _MapIdSubclass(int):
    pass


class _RuntimeIdSubclass(int):
    pass


class _GenerationSubclass(str):
    pass


def _raw_settlement(
    items: tuple[FatbeansInventoryItem, ...],
    *,
    session_id: str = "5101:42",
    map_id: int = 5101,
    message_id: int = 0x002D,
) -> FatbeansCaptureEvents:
    return FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=1,
                capture_time="2026-08-20T00:00:00",
                message_id=message_id,
                session_id=session_id,
                map_id=map_id,
                round_index=5,
                inventory_items=items,
            ),
        ),
    )


def _raw_category_fact(
    monkeypatch: pytest.MonkeyPatch,
    events: FatbeansCaptureEvents,
    **overrides: object,
) -> dict | None:
    monkeypatch.setattr(
        monitor_module,
        "_settlement_category_authority",
        lambda: _CategoryAuthority(),
    )
    args: dict[str, object] = {
        "session_id": "5101:42",
        "physical_map_id": 5101,
        "model_map_id": 2501,
        "tables": SimpleNamespace(
            maps={2501: object(), 2502: object(), 5101: object()},
        ),
        "warehouse_generation": POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        "settlement_incomplete": False,
    }
    args.update(overrides)
    return monitor_module._settlement_category_fact_v1(events, **args)  # type: ignore[arg-type]


def test_raw_settlement_category_fact_requires_current_session_map_and_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = (
        FatbeansInventoryItem(runtime_id=7, item_id=1_010_001, quality=1, cells=1),
        FatbeansInventoryItem(runtime_id=8, item_id=1_060_001, quality=6, cells=4),
    )
    fact = _raw_category_fact(monkeypatch, _raw_settlement(items))
    assert fact is not None
    assert fact["physical_map_id"] == 5101
    assert fact["model_map_id"] == 2501
    assert fact["settled_items_v1"] == [
        {"item_id": 1_010_001, "quality": 1, "primary_category": 101},
        {"item_id": 1_060_001, "quality": 6, "primary_category": 101},
    ]

    for unsafe_map_id in (None, True, _MapIdSubclass(5101), 5102):
        assert _raw_category_fact(
            monkeypatch,
            _raw_settlement(items, map_id=unsafe_map_id),
        ) is None
    assert _raw_category_fact(
        monkeypatch,
        _raw_settlement(items, session_id="5101:43"),
    ) is None
    assert _raw_category_fact(
        monkeypatch,
        _raw_settlement(items, session_id="5102:42", map_id=5101),
        session_id="5102:42",
    ) is None
    assert _raw_category_fact(
        monkeypatch,
        _raw_settlement(items),
        model_map_id=2502,
    ) is None


def test_raw_settlement_category_fact_fails_closed_for_every_unsafe_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = FatbeansInventoryItem(runtime_id=7, item_id=1_010_001, quality=1, cells=1)
    assert _raw_category_fact(
        monkeypatch,
        _raw_settlement((item,), message_id=0x0025),
    ) is None
    assert _raw_category_fact(
        monkeypatch,
        _raw_settlement((item,)),
        warehouse_generation="mixed-generation",
    ) is None
    assert _raw_category_fact(
        monkeypatch,
        _raw_settlement((item,)),
        settlement_incomplete=True,
    ) is None
    assert _raw_category_fact(
        monkeypatch,
        _raw_settlement((item, item)),
    ) is None
    assert _raw_category_fact(
        monkeypatch,
        _raw_settlement(
            (
                item,
                FatbeansInventoryItem(7, 1_030_001, 3, 2),
            ),
        ),
    ) is None
    for unsafe_runtime_id in (None, True, _RuntimeIdSubclass(7)):
        assert _raw_category_fact(
            monkeypatch,
            _raw_settlement(
                (FatbeansInventoryItem(unsafe_runtime_id, 1_010_001, 1, 1),),
            ),
        ) is None
    assert _raw_category_fact(
        monkeypatch,
        _raw_settlement((FatbeansInventoryItem(7, 1_010_001, 7, 1),)),
    ) is None
    assert _raw_category_fact(
        monkeypatch,
        _raw_settlement((FatbeansInventoryItem(7, 9_999_999, 1, 1),)),
    ) is None
    oversized = tuple(
        FatbeansInventoryItem(runtime_id=index + 1, item_id=1_000_000 + index, quality=1, cells=1)
        for index in range(513)
    )
    assert _raw_category_fact(monkeypatch, _raw_settlement(oversized)) is None

    monkeypatch.setenv(monitor_module.SETTLEMENT_CATEGORY_FACTS_DISABLE, "1")
    assert _raw_category_fact(monkeypatch, _raw_settlement((item,))) is None


def test_receipt_and_upload_bind_fact_generation_and_frozen_map_relation() -> None:
    receipt_module = _monitor_module()

    def assert_no_complete_category_sample(artifact: dict) -> None:
        assert receipt_module._settlement_category_fact_receipt(artifact) is None
        assert sample_upload._settlement_category_upload_candidate(artifact) is None
        record = sample_upload.trim_artifact(artifact)
        assert record is not None
        assert "settled_items_v1" not in record

    for fact_generation, root_generation in (
        (None, POSTUPDATE_PLAYER_WAREHOUSE_GENERATION),
        (True, POSTUPDATE_PLAYER_WAREHOUSE_GENERATION),
        ("mixed-generation", POSTUPDATE_PLAYER_WAREHOUSE_GENERATION),
        (POSTUPDATE_PLAYER_WAREHOUSE_GENERATION, "mixed-generation"),
        ("mixed-generation", "mixed-generation"),
        (POSTUPDATE_PLAYER_WAREHOUSE_GENERATION, None),
        (POSTUPDATE_PLAYER_WAREHOUSE_GENERATION, True),
        (
            _GenerationSubclass(POSTUPDATE_PLAYER_WAREHOUSE_GENERATION),
            POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        ),
        (
            POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
            _GenerationSubclass(POSTUPDATE_PLAYER_WAREHOUSE_GENERATION),
        ),
    ):
        artifact = _artifact()
        artifact["settlement_category_fact_v1"]["warehouse_generation"] = fact_generation
        artifact["warehouse_source_generation"] = root_generation
        assert_no_complete_category_sample(artifact)

    relation_tamper = _artifact()
    relation_tamper["model_map_id"] = 2502
    relation_tamper["settlement_category_fact_v1"]["model_map_id"] = 2502
    assert_no_complete_category_sample(relation_tamper)

    legacy = _artifact()
    legacy.pop("settlement_category_fact_v1")
    record = sample_upload.trim_artifact(legacy)
    assert record is not None
    assert "settled_items_v1" not in record


def test_upload_candidate_requires_exact_artifact_root_map_types() -> None:
    def assert_no_upload_category_sample(artifact: dict) -> None:
        assert sample_upload._settlement_category_upload_candidate(artifact) is None
        record = sample_upload.trim_artifact(artifact)
        assert record is not None
        assert "settled_items_v1" not in record

    for field in ("map_id", "model_map_id"):
        for unsafe_value in (None, True, _MapIdSubclass(1), 1.0, "1"):
            artifact = _artifact()
            artifact["map_id"] = 1
            artifact["model_map_id"] = 1
            artifact["settlement_category_fact_v1"]["physical_map_id"] = 1
            artifact["settlement_category_fact_v1"]["model_map_id"] = 1
            artifact[field] = unsafe_value
            assert_no_upload_category_sample(artifact)

    joint_tamper = _artifact()
    joint_tamper["model_map_id"] = 2502
    joint_tamper["settlement_category_fact_v1"]["model_map_id"] = 2502
    assert_no_upload_category_sample(joint_tamper)
