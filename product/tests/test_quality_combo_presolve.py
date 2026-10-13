from __future__ import annotations

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.quality_combo_presolve import (
    is_quality_combo_reachable,
    quality_combo_presolve_for_map,
    quality_combo_presolve_payload,
)


def _item(item_id: int, *, quality: int, shape: tuple[int, int]) -> Item:
    w, h = shape
    return Item(
        item_id=item_id,
        name=f"item_{item_id}",
        description="",
        name_key=f"item_{item_id}",
        desc_key=f"item_{item_id}_desc",
        quality=quality,
        quality_color="",
        value=10_000,
        shape_w=w,
        shape_h=h,
        tags=[],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )


def _map() -> BidMap:
    return BidMap(
        map_id=2401,
        name="test_map",
        description="",
        category=101,
        auction_mode="open",
        sub_pool_weights=[],
        rounds_total=5,
        entry_fee_silver=0,
        starting_budget_silver=100_000,
        drop_pool_id=9001,
        items_per_session_min=1,
        items_per_session_max=4,
        value_tier_ui="",
        mode_flag=4,
        bid_price_ladder=[],
        raw_row=[],
    )


def test_quality_combo_presolve_tracks_reachable_cells_by_count() -> None:
    q6_small = _item(6001, quality=6, shape=(1, 1))
    q6_large = _item(6002, quality=6, shape=(2, 2))
    q5 = _item(5001, quality=5, shape=(1, 2))
    maps = {2401: _map()}
    drops = {
        9001: DropPool(
            pool_id=9001,
            name="pool",
            description="",
            pool_type=2,
            entries=[
                DropEntry(category=106, item_id=q6_small.item_id, n_min=1, n_max=1, weight=1),
                DropEntry(category=106, item_id=q6_large.item_id, n_min=1, n_max=1, weight=1),
                DropEntry(category=105, item_id=q5.item_id, n_min=1, n_max=2, weight=1),
            ],
        )
    }
    items = {item.item_id: item for item in (q6_small, q6_large, q5)}

    presolve = quality_combo_presolve_for_map(
        2401,
        maps=maps,
        drops=drops,
        items=items,
        qualities=(5, 6),
        max_count=3,
    )

    assert presolve[6][1] == (1, 4)
    assert presolve[6][2] == (2, 5, 8)
    assert presolve[5][1] == (2,)
    assert presolve[5][2] == (4,)
    assert presolve[5][3] == (6,)


def test_quality_combo_presolve_payload_is_json_ready() -> None:
    q6 = _item(6001, quality=6, shape=(3, 4))
    maps = {2401: _map()}
    drops = {
        9001: DropPool(
            pool_id=9001,
            name="pool",
            description="",
            pool_type=2,
            entries=[
                DropEntry(category=106, item_id=q6.item_id, n_min=1, n_max=1, weight=1),
            ],
        )
    }
    payload = quality_combo_presolve_payload(
        [2401],
        maps=maps,
        drops=drops,
        items={q6.item_id: q6},
        qualities=(6,),
    )

    assert payload["version"] == 1
    assert payload["maps"]["2401"]["6"]["1"] == [12]
    assert is_quality_combo_reachable(
        payload,
        map_id=2401,
        quality=6,
        count=1,
        cells=12,
    ) is True
    assert is_quality_combo_reachable(
        payload,
        map_id=2401,
        quality=6,
        count=1,
        cells=16,
    ) is False
    assert is_quality_combo_reachable(
        payload,
        map_id=2401,
        quality=6,
        count=None,
        cells=16,
    ) is None
