from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from ahmad_ref_engine import (  # noqa: E402
    _activity_price_alias_map_id,
    _apply_activity_shipwreck_red_boost,
    _is_activity_shipwreck_map,
    _is_red_crate_activity_shipwreck_map,
    _quality_item_values,
    load_reference_static_data,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)


@pytest.mark.parametrize(
    ("source_map_id", "price_map_id", "note_fragment"),
    (
        (2541, 2501, "red_crate_shipwreck_minus40:2541->2501"),
        (2545, 2505, "red_crate_shipwreck_minus40:2545->2505"),
        (2550, 2510, "red_crate_shipwreck_minus40:2550->2510"),
        (4541, 2501, "red_crate_shipwreck_45xx_to_sealed_cabin:4541->2501"),
        (4545, 2505, "red_crate_shipwreck_45xx_to_sealed_cabin:4545->2505"),
        (4550, 2510, "red_crate_shipwreck_45xx_to_sealed_cabin:4550->2510"),
    ),
)
def test_red_crate_direct_price_alias_uses_corresponding_base_shipwreck(
    source_map_id: int,
    price_map_id: int,
    note_fragment: str,
) -> None:
    static_data = load_reference_static_data()

    assert _activity_price_alias_map_id(source_map_id, static_data) == (
        price_map_id,
        note_fragment,
    )
    _, price_note = _quality_item_values(source_map_id, static_data)
    assert note_fragment in price_note
    assert "fallback_default_price" not in price_note


@pytest.mark.parametrize("source_map_id", (2551, 4551))
def test_red_crate_direct_price_alias_stops_at_activity_block_boundary(
    source_map_id: int,
) -> None:
    static_data = load_reference_static_data()

    assert _activity_price_alias_map_id(source_map_id, static_data) == (
        source_map_id,
        "",
    )


@pytest.mark.parametrize(
    ("source_map_id", "expected"),
    (
        (2505, (2505, "")),
        (2535, (2505, "activity_shipwreck_minus30:2535->2505")),
        (4505, (2505, "blind_auction_to_sealed_cabin:4505->2505")),
    ),
)
def test_red_crate_alias_does_not_change_existing_price_paths(
    source_map_id: int,
    expected: tuple[int, str],
) -> None:
    assert _activity_price_alias_map_id(
        source_map_id, load_reference_static_data()
    ) == expected


def test_direct_engine_keeps_source_map_and_adds_only_red_crate_q6_boost() -> None:
    snapshot = {
        "hero": "ahmed",
        "map_id": 2545,
        "model_map_id": 2505,
        "round": 1,
        "phase": "bidding",
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2545,
                "model_map_id": 2505,
                "round": 1,
                "phase": "bidding",
            },
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": {"total_count": 20},
    }

    prepared = prepare_reference_engine_snapshot(snapshot)
    assert prepared["map_id"] == 2545
    assert prepared["model_map_id"] == 2505
    assert prepared["ui_contract"]["context"]["map_id"] == 2545
    assert prepared["ui_contract"]["context"]["model_map_id"] == 2505

    result = run_reference_engine(prepared, max_combos=2_000)
    assert result.evidence.map_id == 2545
    assert any(
        "red_crate_shipwreck_minus40:2545->2505" in note
        for note in result.notes
    )
    assert any(
        note == "red_crate_shipwreck_red_boost:1.22" for note in result.notes
    )
    assert not any(
        note.startswith("activity_shipwreck_red_boost:") for note in result.notes
    )

    assert _is_activity_shipwreck_map(2545) is False
    assert _is_red_crate_activity_shipwreck_map(2545) is True
    baseline = {"q1": 0.3, "q3": 0.2, "q4": 0.2, "q5": 0.2, "q6": 0.1}
    boost_notes: list[str] = []
    boosted = _apply_activity_shipwreck_red_boost(
        dict(baseline), 2545, {}, boost_notes
    )
    assert boosted["q6"] > baseline["q6"]
    assert boost_notes == ["red_crate_shipwreck_red_boost:1.22"]
