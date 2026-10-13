"""Tests for synthetic tool readings."""

from __future__ import annotations

import numpy as np

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import (
    BucketTruth,
    SessionTruth,
    sample_session_truth,
)
from bidking_lab.inference.synth_readings import (
    SESSION_TOOL_SPECS,
    TOOL_SPECS,
    apply_tool,
    build_session_obs,
)


# --- Helpers (mirrors test_ground_truth) ---

def _make_item(item_id, value, quality, shape=(1, 1)) -> Item:
    w, h = shape
    return Item(
        item_id=item_id, name=f"i{item_id}", description="",
        name_key="k", desc_key="d", quality=quality, quality_color="x",
        value=value, shape_w=w, shape_h=h,
        tags=[], allowed_shelves=[], icon_name="", model_name="",
        raw_row=["0"] * 38,
    )


def _truth_from_buckets(buckets: dict[int, BucketTruth], map_id: int = 2407) -> SessionTruth:
    total = sum(b.total_cells for b in buckets.values())
    return SessionTruth(
        map_id=map_id, map_name="mansion_test",
        warehouse_total_cells=total, buckets=buckets,
    )


def _bucket(quality, count, total_cells, value_sum, huge_count=0) -> BucketTruth:
    return BucketTruth(
        quality=quality, count=count, total_cells=total_cells,
        value_sum=value_sum, huge_count=huge_count, items=[],
    )


# --- TOOL_SPECS registration sanity ---

def test_tool_specs_cover_ethan_default_loadout() -> None:
    """All 5 tools in ETHAN_DEFAULT_LOADOUT must have a spec."""
    from bidking_lab.inference.observation import ETHAN_DEFAULT_LOADOUT
    for name in ETHAN_DEFAULT_LOADOUT:
        assert name in TOOL_SPECS, f"missing TOOL_SPECS entry: {name}"


def test_session_tool_specs_include_warehouse() -> None:
    assert "\u603b\u4ed3\u50a8\u7a7a\u95f4" in SESSION_TOOL_SPECS


# --- Apply individual tools ---

def test_apply_white_scan_combines_q1_and_q2() -> None:
    """\u666e\u54c1\u626b\u63cf reveals white+green combined total cells into q=1."""
    truth = _truth_from_buckets({
        1: _bucket(1, count=3, total_cells=4, value_sum=300),
        2: _bucket(2, count=2, total_cells=5, value_sum=600),
    })
    eff = apply_tool(truth, "\u666e\u54c1\u626b\u63cf")
    assert eff.rarity == "white"
    assert eff.silver_cost == 1_200
    # Combined into q=1 by convention.
    assert eff.bucket_patches == {1: {"total_cells": 9}}


def test_apply_green_scan_reveals_blue_total_cells() -> None:
    truth = _truth_from_buckets({3: _bucket(3, count=4, total_cells=12, value_sum=4400)})
    eff = apply_tool(truth, "\u826f\u54c1\u626b\u63cf")
    assert eff.rarity == "green"
    assert eff.silver_cost == 2_500
    assert eff.bucket_patches == {3: {"total_cells": 12}}


def test_apply_purple_value_sum() -> None:
    truth = _truth_from_buckets({
        4: _bucket(4, count=14, total_cells=35, value_sum=86_490, huge_count=0),
    })
    eff = apply_tool(truth, "\u4f18\u54c1\u4f30\u4ef7")
    assert eff.rarity == "blue"
    assert eff.silver_cost == 20_000
    assert eff.bucket_patches == {4: {"value_sum": 86_490}}


def test_apply_purple_avg_cells_uses_truncation_rule() -> None:
    """\u4f18\u54c1\u5747\u683c reveals avg_cells as a parsed Reading (truncated at 2dp)."""
    truth = _truth_from_buckets({
        4: _bucket(4, count=14, total_cells=35, value_sum=86_490),  # 35/14 = 2.5
    })
    eff = apply_tool(truth, "\u4f18\u54c1\u5747\u683c")
    assert 4 in eff.bucket_patches
    reading = eff.bucket_patches[4]["avg_cells"]
    assert reading.raw == "2.5"


def test_apply_gold_value_sum() -> None:
    truth = _truth_from_buckets({5: _bucket(5, count=2, total_cells=20, value_sum=180_000)})
    eff = apply_tool(truth, "\u6781\u54c1\u4f30\u4ef7")
    assert eff.rarity == "purple"
    assert eff.silver_cost == 35_000
    assert eff.bucket_patches == {5: {"value_sum": 180_000}}


def test_apply_warehouse_total() -> None:
    """\u603b\u4ed3\u50a8\u7a7a\u95f4 writes to session-level field at 55k silver (user override)."""
    truth = _truth_from_buckets({
        4: _bucket(4, count=10, total_cells=30, value_sum=100_000),
        6: _bucket(6, count=2, total_cells=12, value_sum=300_000),
    })
    eff = apply_tool(truth, "\u603b\u4ed3\u50a8\u7a7a\u95f4")
    assert eff.silver_cost == 55_000
    assert eff.session_patch == {"warehouse_total_cells": 42}
    assert eff.bucket_patches == {}


def test_apply_avg_cells_handles_empty_bucket_gracefully() -> None:
    """No items in target bucket → no patch (rather than divide-by-zero)."""
    truth = _truth_from_buckets({})
    eff = apply_tool(truth, "\u4f18\u54c1\u5747\u683c")
    assert eff.bucket_patches == {}


# --- build_session_obs ---

def test_build_session_obs_for_ethan_default_kit() -> None:
    """Apply all 5 Ethan tools + warehouse → integrated SessionObs."""
    truth = _truth_from_buckets({
        1: _bucket(1, count=3, total_cells=4, value_sum=300),
        2: _bucket(2, count=2, total_cells=5, value_sum=600),
        3: _bucket(3, count=4, total_cells=12, value_sum=4400),
        4: _bucket(4, count=14, total_cells=35, value_sum=86_490),
        5: _bucket(5, count=2, total_cells=20, value_sum=180_000),
    }, map_id=2407)

    obs, silver = build_session_obs(
        truth,
        hero="ethan",
        tools=("\u666e\u54c1\u626b\u63cf", "\u826f\u54c1\u626b\u63cf",
               "\u4f18\u54c1\u4f30\u4ef7", "\u4f18\u54c1\u5747\u683c",
               "\u6781\u54c1\u4f30\u4ef7"),
    )
    # Silver cost = 1200 + 2500 + 20000 + 20000 + 35000 = 78700
    assert silver == 1_200 + 2_500 + 20_000 + 20_000 + 35_000
    # White-green merged into q=1 with total_cells=9
    assert obs.buckets[1].total_cells == 9
    # Blue at q=3 with total_cells=12
    assert obs.buckets[3].total_cells == 12
    # Purple at q=4: both value_sum AND avg_cells populated
    p = obs.buckets[4]
    assert p.value_sum == 86_490
    assert p.avg_cells.raw == "2.5"
    # Gold at q=5 with value_sum=180_000
    assert obs.buckets[5].value_sum == 180_000


def test_build_session_obs_with_aisha_outline_pins_low_tiers() -> None:
    """Aisha's R1\u2013R3 free outline reveals pin q=1..4 count + total_cells."""
    truth = _truth_from_buckets({
        1: _bucket(1, count=5, total_cells=9, value_sum=650),
        2: _bucket(2, count=4, total_cells=8, value_sum=2400),
        3: _bucket(3, count=6, total_cells=14, value_sum=15_400),
        4: _bucket(4, count=10, total_cells=30, value_sum=80_000),
        6: _bucket(6, count=1, total_cells=16, value_sum=900_000, huge_count=1),
    }, map_id=2407)

    obs, _ = build_session_obs(
        truth,
        hero="aisha",
        tools=("\u603b\u4ed3\u50a8\u7a7a\u95f4",),
        include_aisha_outline=True,
    )
    # All four low buckets are pinned to ground truth counts.
    for q in (1, 2, 3, 4):
        assert obs.buckets[q].count == truth.buckets[q].count
        assert obs.buckets[q].total_cells == truth.buckets[q].total_cells
    # Warehouse total comes from the gold tool.
    assert obs.warehouse_total_cells == truth.warehouse_total_cells


def test_build_session_obs_huge_band_for_ethan_visible_all() -> None:
    """Ethan sees huge bands for purple/gold/red; derived from ground-truth huge_count."""
    truth = _truth_from_buckets({
        4: _bucket(4, count=10, total_cells=46, value_sum=85_000, huge_count=1),
        5: _bucket(5, count=2, total_cells=22, value_sum=180_000, huge_count=0),
        6: _bucket(6, count=3, total_cells=50, value_sum=2_200_000, huge_count=3),
    })
    obs, _ = build_session_obs(
        truth, hero="ethan", tools=("\u4f18\u54c1\u4f30\u4ef7",),
    )
    assert obs.buckets[4].huge_band == "1"      # 1 huge purple
    assert obs.buckets[5].huge_band == "none"   # 0 huge gold
    assert obs.buckets[6].huge_band == "2-3"    # 3 huge red


def test_build_session_obs_huge_band_for_aisha_only_purple() -> None:
    """Aisha sees only the purple huge band; gold/red default to 'none' even when truth says huge_count>0.

    To make q=5 appear in the observation we use 极品估价 (writes into
    q=5); that bucket then exists but its huge_band stays 'none' because
    Aisha cannot eyeball gold huge items.
    """
    truth = _truth_from_buckets({
        4: _bucket(4, count=10, total_cells=46, value_sum=85_000, huge_count=1),
        5: _bucket(5, count=2, total_cells=22, value_sum=180_000, huge_count=0),
        6: _bucket(6, count=3, total_cells=50, value_sum=2_200_000, huge_count=3),
    })
    obs, _ = build_session_obs(
        truth,
        hero="aisha",
        tools=("\u4f18\u54c1\u4f30\u4ef7", "\u6781\u54c1\u4f30\u4ef7"),
    )
    assert obs.buckets[4].huge_band == "1"      # purple visible
    assert obs.buckets[5].huge_band == "none"   # gold present but huge invisible
    # No q=6 bucket since no tool wrote into it and Aisha can't see its huge.
    assert 6 not in obs.buckets


def test_build_session_obs_aisha_skips_red_when_no_tool_targets_it() -> None:
    """Without any tool reading or outline pin for red, q=6 bucket isn't created."""
    truth = _truth_from_buckets({
        4: _bucket(4, count=10, total_cells=46, value_sum=85_000, huge_count=1),
        6: _bucket(6, count=2, total_cells=20, value_sum=1_000_000, huge_count=1),
    })
    obs, _ = build_session_obs(
        truth, hero="aisha", tools=("\u4f18\u54c1\u4f30\u4ef7",),
    )
    assert 4 in obs.buckets
    assert 6 not in obs.buckets
