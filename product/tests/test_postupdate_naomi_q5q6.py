from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "src", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from ahmad_live_panel_server import _infer_q5q6_count_total, _ref_input_summary
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine
from bidking_lab.live.fatbeans import (
    FatbeansCaptureEvents,
    FatbeansSkillReveal,
    FatbeansStateEvent,
    live_batches_from_fatbeans_events,
)
from bidking_lab.live.monitor import _ahmad_ref_inputs_from_batches


DISABLE = "BIDKING_DISABLE_POSTUPDATE_NINE_HERO_SKILL_COMPAT"


def _naomi_events(*, result: int = 3) -> FatbeansCaptureEvents:
    state = FatbeansStateEvent(
        sort_id=17,
        capture_time="",
        message_id=0x25,
        session_id="sanitized",
        map_id=2410,
        round_index=2,
        skill_reveals=(
            FatbeansSkillReveal(
                skill_id=100106,
                hero_id=106,
                round_index=None,
                result=result,
                result_field=7,
            ),
        ),
    )
    return FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        states=(state,),
        statuses=(),
    )


def _run_engine(count_sum: int = 3) -> dict:
    snapshot = {
        "map_id": 2410,
        "hero": "naomi",
        "round": 4,
        "phase": "bidding",
        "structured_ref_inputs": {
            "hero": "naomi",
            "count_sums": {"q5q6_min": count_sum},
        },
    }
    return run_reference_engine(
        prepare_reference_engine_snapshot(snapshot),
        max_combos=20_000,
    ).as_dict()


def test_naomi_exact_gold_red_count_reaches_structured_bridge(monkeypatch) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    batches = live_batches_from_fatbeans_events(_naomi_events())

    assert [
        (update.path, update.value)
        for batch in batches
        for update in batch.field_updates
        if update.path[0] == "category_quality_group"
    ] == [(("category_quality_group", "103+107", "q5q6", "count"), 3)]
    structured = _ahmad_ref_inputs_from_batches(
        batches,
        hero="naomi",
        items={},
    )
    assert structured["count_sums"] == {"q5q6_min": 3}
    assert structured["scoped_count_sums"] == [
        {
            "categories": ["103", "107"],
            "qualities": ["q5", "q6"],
            "count": 3,
            "relation": "exact_within_scope",
            "global_relation": "minimum",
        }
    ]


def test_naomi_zero_gold_red_count_is_preserved_as_exact(monkeypatch) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    batches = live_batches_from_fatbeans_events(_naomi_events(result=0))

    assert [
        update.value
        for batch in batches
        for update in batch.field_updates
        if update.path == ("category_quality_group", "103+107", "q5q6", "count")
    ] == [0]


def test_naomi_scoped_q5q6_count_is_global_floor_and_scoped_ui_fact(monkeypatch) -> None:
    monkeypatch.delenv(DISABLE, raising=False)
    result = _run_engine()

    assert result["status"] == "count_prior"
    assert result["combo_count"] > 0
    assert result["evidence"]["count_sums"] == {"q5q6_min": 3}
    assert _infer_q5q6_count_total(result) is None
    assert "时尚+数码金红件 3（类内精确，全仓下界）" in _ref_input_summary(result)


def test_naomi_named_rollback_restores_preupdate_behavior(monkeypatch) -> None:
    monkeypatch.setenv(DISABLE, "1")
    batches = live_batches_from_fatbeans_events(_naomi_events())
    assert not [
        update
        for batch in batches
        for update in batch.field_updates
        if update.path == ("category_quality_group", "103+107", "q5q6", "count")
    ]

    result = _run_engine()
    assert result["evidence"]["count_sums"] == {}
    assert "金红件" not in _ref_input_summary(result)
