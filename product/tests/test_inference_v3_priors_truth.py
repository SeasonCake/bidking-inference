from types import SimpleNamespace

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import BucketTruth, SessionTruth
from bidking_lab.inference.v3.constraints import ConstraintSet, ItemAnchor
from bidking_lab.inference.v3 import (
    decision_truth_from_session_truth,
    decision_truth_from_fatbeans,
    ordinary_shape_replacement_values,
    settlement_truth_from_fatbeans,
    summarize_drop_prior,
)


def _item(
    item_id: int,
    *,
    quality: int,
    value: int,
    shape: tuple[int, int],
) -> Item:
    width, height = shape
    return Item(
        item_id=item_id,
        name=f"item_{item_id}",
        description="",
        name_key=f"item_{item_id}",
        desc_key=f"item_{item_id}_desc",
        quality=quality,
        quality_color="",
        value=value,
        shape_w=width,
        shape_h=height,
        tags=[],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )


def _tables() -> tuple[dict[int, BidMap], dict[int, DropPool], dict[int, Item]]:
    filler = _item(1011001, quality=1, value=1_000, shape=(1, 1))
    red = _item(1086001, quality=6, value=200_000, shape=(4, 4))
    return (
        {
            2401: BidMap(
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
                items_per_session_min=2,
                items_per_session_max=2,
                value_tier_ui="",
                mode_flag=4,
                bid_price_ladder=[],
                raw_row=[],
            ),
        },
        {
            9001: DropPool(
                pool_id=9001,
                name="pool",
                description="",
                pool_type=2,
                entries=[
                    DropEntry(
                        category=101,
                        item_id=filler.item_id,
                        n_min=1,
                        n_max=1,
                        weight=1,
                    ),
                    DropEntry(
                        category=108,
                        item_id=red.item_id,
                        n_min=1,
                        n_max=1,
                        weight=1,
                    ),
                ],
            ),
        },
        {
            filler.item_id: filler,
            red.item_id: red,
        },
    )


def test_v3_drop_prior_is_deterministic_by_quality() -> None:
    maps, drops, items = _tables()

    prior = summarize_drop_prior(2401, maps=maps, drops=drops, items=items)
    q6 = prior.quality(6)

    assert prior.expected_session_count == 2
    assert prior.expected_session_cells == 17
    assert prior.expected_session_value == 201_000
    assert q6 is not None
    assert q6.draw_probability == 0.5
    assert q6.session_probability == 0.75
    assert q6.expected_session_count == 1
    assert q6.expected_session_cells == 16
    assert q6.expected_session_value == 200_000


def test_v3_replacement_values_use_ordinary_shape_p50() -> None:
    maps, drops, items = _tables()

    replacements = ordinary_shape_replacement_values(
        2401,
        maps=maps,
        drops=drops,
        items=items,
    )

    assert replacements[(6, 4, 4)] == 200_000


def test_v3_settlement_truth_extracts_raw_inventory_value() -> None:
    _maps, _drops, items = _tables()
    events = SimpleNamespace(
        states=(
            SimpleNamespace(sort_id=5, session_id="s1", map_id=2401, inventory_items=()),
            SimpleNamespace(
                sort_id=20,
                session_id="s1",
                map_id=2401,
                inventory_items=(
                    SimpleNamespace(item_id=1011001, quality=None, cells=1),
                    SimpleNamespace(item_id=1086001, quality=6, cells=16),
                ),
            ),
        )
    )

    truth = settlement_truth_from_fatbeans(events, items=items)

    assert truth is not None
    assert truth.item_count == 2
    assert truth.total_cells == 17
    assert truth.raw_total_value == 201_000
    assert truth.quality(1).raw_value == 1_000
    assert truth.quality(6).count == 1
    assert truth.quality(6).cells == 16


def test_v3_decision_truth_trims_unanchored_confusable_tail() -> None:
    tail = _item(1086002, quality=6, value=2_000_000, shape=(1, 1))
    events = SimpleNamespace(
        states=(
            SimpleNamespace(
                sort_id=20,
                session_id="s1",
                map_id=2401,
                inventory_items=(
                    SimpleNamespace(item_id=tail.item_id, quality=6, cells=1),
                ),
            ),
        )
    )

    unanchored = decision_truth_from_fatbeans(
        events,
        items={tail.item_id: tail},
        constraints=ConstraintSet(),
        replacement_values={(6, 1, 1): 180_000},
    )
    anchored = decision_truth_from_fatbeans(
        events,
        items={tail.item_id: tail},
        constraints=ConstraintSet(
            item_anchors={
                "runtime:1": ItemAnchor(
                    key="runtime:1",
                    event_id="event:1",
                    source_kind="public_info",
                    source_id="200021",
                    sort_id=5,
                    runtime_id=1,
                    item_id=tail.item_id,
                    quality=6,
                    value=tail.value,
                    shape_key="11",
                    cells=1,
                )
            }
        ),
        replacement_values={(6, 1, 1): 180_000},
    )

    assert unanchored is not None
    assert unanchored.formal_decision_value == 0
    assert unanchored.tail_replacement_decision_value == 180_000
    assert unanchored.q6_trimmed_tail_value == 2_000_000
    assert anchored is not None
    assert anchored.formal_decision_value == 2_000_000
    assert anchored.tail_replacement_decision_value == 2_000_000


def test_v3_session_truth_uses_same_decision_trim_rules() -> None:
    tail = _item(1086002, quality=6, value=2_000_000, shape=(1, 1))
    truth = SessionTruth(
        map_id=2401,
        map_name="test_map",
        warehouse_total_cells=1,
        buckets={
            6: BucketTruth(
                quality=6,
                count=1,
                total_cells=1,
                value_sum=tail.value,
                items=[tail],
            )
        },
    )

    decision = decision_truth_from_session_truth(
        truth,
        constraints=ConstraintSet(),
        replacement_values={(6, 1, 1): 180_000},
    )

    assert decision.formal_decision_value == 0
    assert decision.tail_replacement_decision_value == 180_000
    assert decision.q6_trimmed_tail_value == 2_000_000
