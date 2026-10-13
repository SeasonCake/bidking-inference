from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "src", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from bidking_lab.live import monitor
from bidking_lab.live.fatbeans import (
    FatbeansCaptureEvents,
    FatbeansObservedItem,
    FatbeansSkillReveal,
    FatbeansStateEvent,
    _skill_reveal_category,
    live_batches_from_fatbeans_events,
)


DISABLE = "BIDKING_DISABLE_POSTUPDATE_NINE_HERO_SKILL_COMPAT"


def _events(*reveals: FatbeansSkillReveal) -> FatbeansCaptureEvents:
    return FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=27,
                capture_time="",
                message_id=0x25,
                session_id="sanitized",
                map_id=2401,
                round_index=3,
                skill_reveals=tuple(reveals),
            ),
        ),
    )


def test_postupdate_categories_cover_only_single_category_actions(monkeypatch) -> None:
    monkeypatch.delenv(DISABLE, raising=False)

    assert _skill_reveal_category(1001021) == 105
    assert _skill_reveal_category(1001062) == 107
    assert _skill_reveal_category(10010921) == 102
    assert _skill_reveal_category(10020221) == 101
    assert _skill_reveal_category(1002031) == 106
    assert _skill_reveal_category(1002032) == 109
    assert _skill_reveal_category(10020631) == 110

    # Official text names category unions or a whole-vault selection. Assigning
    # either side would create false category evidence.
    assert _skill_reveal_category(100102) is None
    assert _skill_reveal_category(1001061) is None
    assert _skill_reveal_category(100202) is None
    assert _skill_reveal_category(100205) is None


def test_postupdate_category_mapping_has_named_rollback(monkeypatch) -> None:
    monkeypatch.setenv(DISABLE, "1")

    assert _skill_reveal_category(1001021) is None
    assert _skill_reveal_category(10010921) is None
    assert _skill_reveal_category(10020221) is None
    assert _skill_reveal_category(10020631) is None
    # A pre-update mapping remains unchanged.
    assert _skill_reveal_category(100201) == 104


def test_nested_action_parent_and_round_reach_reference_rows(monkeypatch) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    rows = monitor._skill_reveal_rows(
        _events(
            FatbeansSkillReveal(
                skill_id=10020221,
                hero_id=202,
                round_index=3,
                observed_items=(
                    FatbeansObservedItem(
                        local_index=12,
                        runtime_id=501,
                        item_id=1000001,
                        quality=4,
                        value=12345,
                        shape_code=22,
                        cells=4,
                    ),
                ),
            ),
        ),
        {},
    )
    ref_rows = monitor._skill_reveals_for_ref(rows)

    assert rows[0]["round_index"] == 3
    assert rows[0]["parent_skill_id"] == 1002022
    assert rows[0]["tool"] == "卡洛斯·R3 家具完整信息"
    assert ref_rows[0]["sort"] == 27
    assert ref_rows[0]["round_index"] == 3
    assert ref_rows[0]["parent_skill_id"] == 1002022


def test_nested_action_metadata_has_named_rollback(monkeypatch) -> None:
    monkeypatch.setenv(DISABLE, "1")
    rows = monitor._skill_reveal_rows(
        _events(
            FatbeansSkillReveal(
                skill_id=10020221,
                hero_id=202,
                round_index=3,
                observed_items=(
                    FatbeansObservedItem(
                        local_index=12,
                        runtime_id=501,
                        item_id=None,
                        quality=4,
                        value=None,
                        shape_code=22,
                        cells=4,
                    ),
                ),
            ),
        ),
        {},
    )
    ref_rows = monitor._skill_reveals_for_ref(rows)

    assert "round_index" not in rows[0]
    assert "parent_skill_id" not in rows[0]
    assert "sort" not in ref_rows[0]
    assert "round_index" not in ref_rows[0]
    assert "parent_skill_id" not in ref_rows[0]


def test_cumulative_nested_reveals_merge_only_when_runtime_identity_matches(
    monkeypatch,
) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    events = _events(
        FatbeansSkillReveal(
            skill_id=100109,
            hero_id=109,
            round_index=1,
            observed_items=(
                FatbeansObservedItem(
                    local_index=13,
                    runtime_id=701,
                    item_id=None,
                    quality=5,
                    value=None,
                    shape_code=None,
                    cells=None,
                ),
            ),
        ),
        FatbeansSkillReveal(
            skill_id=1001091,
            hero_id=109,
            round_index=2,
            observed_items=(
                FatbeansObservedItem(
                    local_index=12,
                    runtime_id=701,
                    item_id=None,
                    quality=None,
                    value=None,
                    shape_code=22,
                    cells=4,
                ),
            ),
        ),
        FatbeansSkillReveal(
            skill_id=10010921,
            hero_id=109,
            round_index=3,
            observed_items=(
                FatbeansObservedItem(
                    local_index=12,
                    runtime_id=701,
                    item_id=1000001,
                    quality=5,
                    value=54321,
                    shape_code=22,
                    cells=4,
                ),
            ),
        ),
    )

    batch = live_batches_from_fatbeans_events(events)[0]

    assert len(batch.grid_items) == 1
    item = batch.grid_items[0]
    assert item.runtime_id == 701
    assert item.category == 102
    assert item.quality == 5
    assert item.item_id == 1000001
    assert item.cells == 4
    assert item.value == 54321
    assert item.local_index == 12


def test_parent_skill_id_does_not_join_distinct_physical_runtime_items(
    monkeypatch,
) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    events = _events(
        FatbeansSkillReveal(
            skill_id=100202,
            hero_id=202,
            round_index=1,
            observed_items=(
                FatbeansObservedItem(
                    local_index=12,
                    runtime_id=701,
                    item_id=None,
                    quality=4,
                    value=None,
                    shape_code=22,
                    cells=4,
                ),
            ),
        ),
        FatbeansSkillReveal(
            skill_id=1002022,
            hero_id=202,
            round_index=3,
            observed_items=(
                FatbeansObservedItem(
                    local_index=40,
                    runtime_id=702,
                    item_id=None,
                    quality=3,
                    value=None,
                    shape_code=11,
                    cells=1,
                ),
            ),
        ),
        FatbeansSkillReveal(
            skill_id=10020221,
            hero_id=202,
            round_index=3,
            observed_items=(
                FatbeansObservedItem(
                    local_index=12,
                    runtime_id=701,
                    item_id=1000001,
                    quality=4,
                    value=54321,
                    shape_code=22,
                    cells=4,
                ),
            ),
        ),
    )

    rows = monitor._skill_reveal_rows(events, {})
    child = next(row for row in rows if row["skill_id"] == 10020221)
    batch = live_batches_from_fatbeans_events(events)[0]

    assert child["parent_skill_id"] == 1002022
    assert len(batch.grid_items) == 2
    by_runtime = {item.runtime_id: item for item in batch.grid_items}
    assert by_runtime[701].item_id == 1000001
    assert by_runtime[701].value == 54321
    assert by_runtime[702].item_id is None
    assert by_runtime[702].value is None
