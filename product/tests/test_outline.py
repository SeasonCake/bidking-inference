"""Tests for the outline observation module."""

from __future__ import annotations

import pytest

from bidking_lab.extract.item_table import Item
from bidking_lab.inference.outline import (
    AISHA_ROUND_QUALITY,
    OutlineObs,
    aisha_outline_quality,
    build_shape_index,
    candidates_for_outline,
    derive_bucket_from_outlines,
    make_aisha_outlines,
    make_ethan_outlines,
)


def _mk_item(item_id: int, *, q: int, w: int, h: int, value: int) -> Item:
    return Item(
        item_id=item_id,
        name=f"item_{item_id}",
        description="",
        name_key=f"n{item_id}",
        desc_key=f"d{item_id}",
        quality=q,
        quality_color="x",
        value=value,
        shape_w=w,
        shape_h=h,
        tags=[],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )


# --- Aisha round → quality (user-confirmed) ---

def test_aisha_round_quality_user_spec() -> None:
    """R1=白(1), R2=绿(2), R3=蓝(3), R4=紫(4)."""
    assert aisha_outline_quality(1) == 1
    assert aisha_outline_quality(2) == 2
    assert aisha_outline_quality(3) == 3
    assert aisha_outline_quality(4) == 4
    assert aisha_outline_quality(5) is None     # no R5 reveal for Aisha


def test_make_aisha_outlines_sets_quality_hint() -> None:
    outs = make_aisha_outlines(round_seen=3, shapes=[(2, 2), (1, 3)])
    assert len(outs) == 2
    for o in outs:
        assert o.quality_hint == (3,)
        assert o.hero == "aisha"
        assert o.round_seen == 3


def test_make_aisha_outlines_unknown_round_has_no_quality() -> None:
    outs = make_aisha_outlines(round_seen=5, shapes=[(1, 1)])
    assert outs[0].quality_hint is None


def test_make_ethan_outlines_has_no_quality_hint() -> None:
    outs = make_ethan_outlines(round_seen=1, shapes=[(3, 3), (4, 4)])
    assert len(outs) == 2
    for o in outs:
        assert o.quality_hint is None
        assert o.hero == "ethan"


# --- Shape index ---

def test_build_shape_index_groups_by_quality_and_shape() -> None:
    items = {
        1: _mk_item(1, q=4, w=2, h=2, value=10_000),  # purple 2x2
        2: _mk_item(2, q=4, w=2, h=2, value=12_000),  # another purple 2x2
        3: _mk_item(3, q=3, w=2, h=2, value=4_000),   # blue 2x2 (same shape, diff quality)
        4: _mk_item(4, q=4, w=4, h=4, value=200_000), # purple 4x4
    }
    idx = build_shape_index(items)
    assert len(idx[(4, 2, 2)]) == 2
    assert len(idx[(3, 2, 2)]) == 1
    assert len(idx[(4, 4, 4)]) == 1


def test_build_shape_index_respects_droppable_filter() -> None:
    items = {
        1: _mk_item(1, q=4, w=2, h=2, value=10_000),
        2: _mk_item(2, q=4, w=2, h=2, value=12_000),
    }
    idx = build_shape_index(items, droppable_ids={1})
    assert len(idx[(4, 2, 2)]) == 1
    assert idx[(4, 2, 2)][0].item_id == 1


def test_candidates_for_outline_uses_quality_hint() -> None:
    items = {
        1: _mk_item(1, q=4, w=2, h=2, value=10_000),
        2: _mk_item(2, q=3, w=2, h=2, value=4_000),
    }
    idx = build_shape_index(items)
    outline = OutlineObs(shape=(2, 2), round_seen=1, quality_hint=(4,))
    cands = candidates_for_outline(outline, idx)
    assert {c.item_id for c in cands} == {1}


def test_candidates_for_outline_falls_back_to_all_qualities() -> None:
    """No quality hint and no filter → search every quality."""
    items = {
        1: _mk_item(1, q=4, w=2, h=2, value=10_000),
        2: _mk_item(2, q=3, w=2, h=2, value=4_000),
    }
    idx = build_shape_index(items)
    outline = OutlineObs(shape=(2, 2), round_seen=1, quality_hint=None)
    cands = candidates_for_outline(outline, idx)
    assert {c.item_id for c in cands} == {1, 2}


# --- derive_bucket_from_outlines (Aisha pattern) ---

def test_derive_bucket_aisha_white_round_pins_count_and_cells() -> None:
    items = {
        1: _mk_item(1, q=1, w=1, h=1, value=100),
        2: _mk_item(2, q=1, w=1, h=1, value=200),
        3: _mk_item(3, q=1, w=2, h=1, value=400),
        4: _mk_item(4, q=2, w=1, h=1, value=300),   # green; shouldn't show up
    }
    idx = build_shape_index(items)
    outlines = make_aisha_outlines(
        round_seen=1,
        shapes=[(1, 1), (1, 1), (2, 1)],   # 3 white outlines
    )
    bucket = derive_bucket_from_outlines(quality=1, outlines=outlines, shape_index=idx)
    assert bucket is not None
    assert bucket.quality == 1
    assert bucket.count == 3
    # cells = 1 + 1 + 2 = 4
    assert bucket.total_cells == 4
    # value range:
    #   outline 1 (1x1): min(100, 200)=100, max=200
    #   outline 2 (1x1): same → +100, +200
    #   outline 3 (2x1): only item 3 → +400, +400
    # Total: 100+100+400=600, 200+200+400=800
    assert bucket.value_range == (600, 800)


def test_derive_bucket_returns_none_for_quality_without_outlines() -> None:
    items = {1: _mk_item(1, q=1, w=1, h=1, value=100)}
    idx = build_shape_index(items)
    outlines = make_aisha_outlines(round_seen=1, shapes=[(1, 1)])
    bucket = derive_bucket_from_outlines(quality=4, outlines=outlines, shape_index=idx)
    assert bucket is None


def test_derive_bucket_ignores_ethan_outlines_without_quality_hint() -> None:
    """Ethan outlines lack quality_hint, so derive_bucket can't use them."""
    items = {1: _mk_item(1, q=1, w=1, h=1, value=100)}
    idx = build_shape_index(items)
    outlines = make_ethan_outlines(round_seen=1, shapes=[(1, 1)])
    bucket = derive_bucket_from_outlines(quality=1, outlines=outlines, shape_index=idx)
    assert bucket is None
