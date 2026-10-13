import importlib.util
from pathlib import Path
from types import SimpleNamespace

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_capacity_source_expansion_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_capacity_source_expansion_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _item(item_id: int = 1001) -> Item:
    return Item(
        item_id=item_id,
        name=f"item_{item_id}",
        description="",
        name_key=f"item_{item_id}",
        desc_key=f"item_{item_id}_desc",
        quality=4,
        quality_color="",
        value=10_000,
        shape_w=2,
        shape_h=2,
        tags=[],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )


def _tables() -> SimpleNamespace:
    item = _item()
    raw_row = ["0"] * 23
    raw_row[8] = "1"
    raw_row[14] = "[2,2,2,2,2]"
    raw_row[16] = "[[]]"
    raw_row[17] = "[9999,9001,1,2]"
    return SimpleNamespace(
        maps={
            2601: BidMap(
                map_id=2601,
                name="direct_capacity_map",
                description="",
                category=101,
                auction_mode="open",
                sub_pool_weights=[],
                rounds_total=5,
                entry_fee_silver=0,
                starting_budget_silver=100_000,
                drop_pool_id=9001,
                items_per_session_min=1,
                items_per_session_max=2,
                value_tier_ui="",
                mode_flag=4,
                bid_price_ladder=[],
                round_category_hints=[],
                raw_row=raw_row,
            )
        },
        drops={
            9001: DropPool(
                pool_id=9001,
                name="pool",
                description="",
                pool_type=2,
                entries=[
                    DropEntry(
                        category=101,
                        item_id=item.item_id,
                        n_min=1,
                        n_max=1,
                        weight=1,
                    )
                ],
            )
        },
        items={item.item_id: item},
    )


def _detail(path: Path, *, source: str) -> dict:
    return {
        "file": f"{path}#prebid_r1",
        "map_id": 2601,
        "hero_map_evidence_profile": "aisha|2601|shape+layout",
        "item_count_capacity": {
            "total_count_source": source,
            "total_count_target": 3,
            "truth_item_count": 3,
            "prior_items_per_session_max": 2,
            "truth_prior_max_delta": 1,
            "target_truth_delta": 0,
            "cases": ["direct_prior_max_conflict"],
        },
        "consistency_bucket": "hard_capacity_conflict",
        "consistency_classes": ("capacity_direct_prior_max_conflict",),
    }


def test_capacity_source_expansion_splits_public_total_and_full_action(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    public_sample = tmp_path / "fatbeans_20260606_public.json"
    action_sample = tmp_path / "fatbeans_20260606_action.json"
    public_sample.write_text("[]", encoding="utf-8")
    action_sample.write_text("[]", encoding="utf-8")

    inventory_items = (
        SimpleNamespace(runtime_id=101, item_id=1001, quality=4, cells=4),
        SimpleNamespace(runtime_id=102, item_id=1001, quality=4, cells=4),
        SimpleNamespace(runtime_id=103, item_id=1001, quality=4, cells=4),
    )
    states = {
        public_sample.name: SimpleNamespace(
            sort_id=10,
            message_id=0x002D,
            round_index=4,
            map_id=2601,
            inventory_items=inventory_items,
            action_results=(),
            public_infos=(SimpleNamespace(info_id=200017, value=3),),
        ),
        action_sample.name: SimpleNamespace(
            sort_id=10,
            message_id=0x002D,
            round_index=4,
            map_id=2601,
            inventory_items=inventory_items,
            action_results=(
                SimpleNamespace(action_id=100100, observed_items=inventory_items),
            ),
            public_infos=(),
        ),
    }

    monkeypatch.setattr(
        module.capacity_audit,
        "parse_fatbeans_capture",
        lambda path: SimpleNamespace(states=(states[Path(path).name],)),
    )
    monkeypatch.setattr(
        module.capacity_audit,
        "_latest_settlement_payload",
        lambda path: (b"payload", None, {}),
    )
    monkeypatch.setattr(
        module.capacity_audit,
        "_parse_fields",
        lambda payload: [(4, 2, b"inventory")],
    )
    monkeypatch.setattr(
        module.capacity_audit,
        "_inventory_block_metrics",
        lambda block: {
            "inventory_slot_count": 300,
            "occupied_slot_count": 3,
            "raw_item_candidate_count": 3,
            "raw_duplicate_runtime_item_pair_count": 0,
            "slot_field_shapes": {},
            "item_field_signatures": {},
        },
    )
    monkeypatch.setattr(
        module.capacity_audit,
        "settlement_truth_from_fatbeans",
        lambda events, *, items: SimpleNamespace(
            item_count=len(events.states[-1].inventory_items)
        ),
    )

    result = module.summarize_capacity_source_expansion(
        [
            _detail(public_sample, source="exact"),
            _detail(action_sample, source="floor"),
        ],
        tables=_tables(),
        selected_case="direct_prior_max_conflict",
        selected_bucket="hard_capacity_conflict",
        sample_root=tmp_path,
        top=8,
    )

    assert result["errors"] == []
    assert len(result["cells"]) == 2
    by_signal = {
        (cell["total_count_source"], cell["full_action_signal"], cell["public_total_signal"]): cell
        for cell in result["cells"]
    }

    public_cell = by_signal[("exact", "no_full_action", "has_public_total")]
    assert public_cell["semantic_status"] == "blocked_round_cap_overflow_after_temp"
    assert public_cell["public_total_count_values"] == {"3": 1}
    assert public_cell["public_total_latest_delta"]["max"] == 0
    assert public_cell["round_cap_excess_after_temp_zodiac_count"]["max"] == 1
    assert public_cell["examples"][0]["public_total_latest_delta"] == [0.0]

    action_cell = by_signal[("floor", "has_full_action", "no_public_total")]
    assert action_cell["semantic_status"] == "blocked_round_cap_overflow_after_temp"
    assert action_cell["full_observed_action_counts"] == {"100100": 1}
    assert action_cell["action_latest_delta"]["max"] == 0
    assert action_cell["examples"][0]["full_observed_action_ids"] == [100100]
