from __future__ import annotations

import copy
import json
import sys
from dataclasses import replace
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "hero_ref" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from bidking_lab.live.live_refresh_signatures import (
    bid_signature_from_latest_bids,
    bid_signature_from_payload,
    format_current_highest_from_bids,
    inference_semantic_signature_from_payload,
    inference_semantic_signature_from_states,
    patch_artifact_bid_fields,
    ref_input_signature_from_snapshot,
    refresh_latest_snapshot_bids,
)
from bidking_lab.live.fatbeans import FatbeansSkillReveal, FatbeansStateEvent
from hero_ref_live_schedule import (
    HERO_REF_SCHEDULES,
    hero_should_run_scheduled_inference,
    hero_uses_dual_pass,
)

from test_live_fatbeans import (
    _fatbeans_row,
    _player_bid_block,
    _rev_state_with_bids_frame,
)


def _bid_capture_payload(
    session: str,
    *,
    sort_id: int,
    leader_value: int,
) -> str:
    rows = [
        _fatbeans_row(
            "REV",
            _rev_state_with_bids_frame(
                session=session,
                map_id=2401,
                round_no=1,
                bids=(
                    _player_bid_block(
                        player_id=9,
                        name="Me",
                        hero_id=204,
                        value=200_000,
                    ),
                    _player_bid_block(
                        player_id=3,
                        name="Carol",
                        hero_id=107,
                        value=leader_value,
                    ),
                ),
            ),
            sort_id=sort_id,
        ),
    ]
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def test_inference_semantic_signature_ignores_bid_only_payload_change() -> None:
    session = "2401:9000000000000017"
    base = _bid_capture_payload(session, sort_id=1, leader_value=100_000)
    bid_only = _bid_capture_payload(session, sort_id=2, leader_value=333_333)
    assert inference_semantic_signature_from_payload(base) == inference_semantic_signature_from_payload(
        bid_only
    )
    assert bid_signature_from_payload(base) != bid_signature_from_payload(bid_only)


def test_inference_semantic_signature_tracks_same_round_skill_result() -> None:
    """A skill placeholder and its decoded value are different engine inputs.

    Ahmed averages can arrive under the same skill id/round after an earlier
    placeholder row.  Omitting ``result`` made the monitor skip that update and
    left the UI unlocked until an unrelated later-round fact forced a rebuild.
    """
    base = FatbeansStateEvent(
        sort_id=1,
        capture_time="2026-08-06T00:00:00+08:00",
        message_id=0x21,
        session_id="2521:same-round",
        map_id=2521,
        round_index=4,
        skill_reveals=(
            FatbeansSkillReveal(
                skill_id=1002043,
                hero_id=204,
                round_index=4,
                result=0.0,
                result_field=0,
            ),
        ),
    )
    decoded = replace(
        base,
        sort_id=2,
        skill_reveals=(
            replace(base.skill_reveals[0], result=1.1666666269302368, result_field=11),
        ),
    )

    before = inference_semantic_signature_from_states((base,))
    after = inference_semantic_signature_from_states((decoded,))

    assert before != after


def test_bid_signature_tracks_latest_bids() -> None:
    first = bid_signature_from_latest_bids({"Alice": 100000})
    second = bid_signature_from_latest_bids({"Alice": 120000})
    assert first != second


def test_patch_artifact_bid_fields_updates_display_without_inference_fields() -> None:
    artifact = {
        "session_id": "2401:1",
        "phase": "bidding",
        "balanced": 777777,
        "inference_signature": ["2401:1", 2401, 1, 9, "0x1", [], [], [], []],
        "bid_rows": [{"当前最高": "Old 1", "建议": "keep-me"}],
        "v2_bid_rows": [{"当前最高": "Old 1"}],
        "warehouse_rows": [{"总格 P10/P50/P90": "90 / 95 / 100"}],
        "formal_mode": "v3_practical",
    }
    patch_artifact_bid_fields(
        artifact,
        latest_bids={"Bob": 250000},
        self_bid_summary={
            "local_player_id": 9,
            "self_name": "Me",
            "self_bid": 200000,
            "winner_is_self": False,
        },
    )
    assert artifact["balanced"] == 777777
    assert artifact["inference_signature"] == ["2401:1", 2401, 1, 9, "0x1", [], [], [], []]
    assert artifact["bid_rows"][0]["当前最高"] == "Bob 250,000"
    assert artifact["bid_rows"][0]["建议"] == "keep-me"
    assert artifact["ui_contract"]["baseline"]["decision"]["current_highest"] == "Bob 250,000"


def test_refresh_latest_snapshot_bids_patches_existing_snapshot(tmp_path: Path) -> None:
    session = "2401:9000000000000017"
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    latest = log_dir / "latest_snapshot.json"
    latest.write_text(
        json.dumps(
            {
                "session_id": session,
                "phase": "bidding",
                "balanced": 555555,
                "inference_signature": list(
                    inference_semantic_signature_from_payload(
                        _bid_capture_payload(session, sort_id=1, leader_value=100_000)
                    )
                    or ()
                ),
                "bid_rows": [{"当前最高": ""}],
                "formal_mode": "v3_practical",
            }
        ),
        encoding="utf-8",
    )
    payload = _bid_capture_payload(session, sort_id=2, leader_value=333_333)
    assert refresh_latest_snapshot_bids(log_dir, payload)
    patched = json.loads(latest.read_text(encoding="utf-8"))
    assert patched["balanced"] == 555555
    assert patched["latest_bids"]["Carol"] == 333333
    assert patched["bid_rows"][0]["当前最高"] == "Carol 333,333"


def test_format_current_highest_from_bids_picks_leader() -> None:
    assert format_current_highest_from_bids({"A": 100, "B": 250000}) == "B 250,000"


# --- ref_input_signature_from_snapshot ---------------------------------------
# Overlay memoize key for the Hero Ref engine. Must stay stable across bid-only
# updates and heartbeats, but change when any ref-relevant input changes
# (public info, skill reveal, action result, inventory, minimap, manual inputs,
# context). This is what lets the overlay skip the ~0.1-1s+ recompute that was
# observed firing 11 times with identical output in a 3-game aisha session.

def _ref_snapshot(
    *,
    session: str = "2401:sess1",
    map_id: int = 2401,
    round_no: int = 2,
    phase: str = "bidding",
    hero: str = "aisha",
    public_info_rows: list | None = None,
    skill_reveals: list | None = None,
    action_result_rows: list | None = None,
    minimap_grid_items: list | None = None,
    ui_contract_minimap_items: list | None = None,
    ui_contract_minimap_quality_counts: dict | None = None,
    structured_ref_inputs: dict | None = None,
    latest_bids: dict | None = None,
) -> dict:
    snap: dict = {
        "session_id": session,
        "map_id": map_id,
        "round": round_no,
        "phase": phase,
        "hero": hero,
        "public_info_rows": public_info_rows or [],
        "skill_reveals": skill_reveals or [],
        "action_result_rows": action_result_rows or [],
        "minimap_grid_items": minimap_grid_items or [],
        "structured_ref_inputs": structured_ref_inputs or {},
        "latest_bids": latest_bids or {},
    }
    uc_mm: dict = {}
    if ui_contract_minimap_items is not None:
        uc_mm["items"] = ui_contract_minimap_items
    if ui_contract_minimap_quality_counts is not None:
        uc_mm["quality_counts"] = ui_contract_minimap_quality_counts
    if uc_mm:
        snap["ui_contract"] = {"minimap": uc_mm}
    return snap


def test_ref_input_signature_stable_across_bid_only_changes() -> None:
    """Bid-only updates (latest_bids, current highest) must not change the signature."""
    snap = _ref_snapshot(
        public_info_rows=[{"info_id": 200011, "map_id": 2401, "value": 10, "value_field": "f"}],
        latest_bids={"A": 100},
    )
    before = ref_input_signature_from_snapshot(snap)
    snap["latest_bids"] = {"A": 999_999, "B": 888_888}
    snap["bid_rows"] = [{"当前最高": "B 888,888"}]
    after = ref_input_signature_from_snapshot(snap)
    assert before == after


def test_ref_input_signature_changes_on_minimap() -> None:
    """Minimap layout changes are ref-relevant and must change the signature.

    This is the gap monitor's inference_signature leaves open (it does not
    track minimap); the overlay memoize key must catch it.
    """
    snap = _ref_snapshot(minimap_grid_items=[
        {"runtime_id": 1, "item_id": 10, "quality": "q3", "cells": 4, "local_index": 0}
    ])
    before = ref_input_signature_from_snapshot(snap)
    snap["minimap_grid_items"].append(
        {"runtime_id": 2, "item_id": 20, "quality": "q5", "cells": 5, "local_index": 1}
    )
    after = ref_input_signature_from_snapshot(snap)
    assert before != after


def test_ref_input_signature_changes_on_public_info() -> None:
    snap = _ref_snapshot()
    before = ref_input_signature_from_snapshot(snap)
    snap["public_info_rows"].append(
        {"info_id": 200011, "map_id": 2401, "value": 10, "value_field": "f"}
    )
    after = ref_input_signature_from_snapshot(snap)
    assert before != after


def test_ref_input_signature_tracks_same_round_actions_for_every_scheduled_hero() -> None:
    """The refresh trigger is shared; no hero may depend on Aisha's dual pass."""
    for hero_key in sorted(HERO_REF_SCHEDULES):
        snap = _ref_snapshot(hero=hero_key, action_result_rows=[])
        before = ref_input_signature_from_snapshot(snap)
        snap["action_result_rows"].append(
            {
                "action_id": 100110,
                "result": 2.6,
                "result_field": 11,
                "round": 2,
                "source_kind": "direct_action_response",
            }
        )
        assert ref_input_signature_from_snapshot(snap) != before, hero_key


def test_ref_input_signature_tracks_same_round_skills_for_every_scheduled_hero() -> None:
    for hero_key, schedule in sorted(HERO_REF_SCHEDULES.items()):
        skill_id = next(iter(schedule.skill_rounds.values()))
        snap = _ref_snapshot(hero=hero_key, skill_reveals=[])
        before = ref_input_signature_from_snapshot(snap)
        snap["skill_reveals"].append(
            {
                "skill_id": skill_id,
                "hero_id": schedule.hero_id,
                "result": 1.5,
                "result_field": 11,
            }
        )
        assert ref_input_signature_from_snapshot(snap) != before, hero_key


def test_ref_input_signature_tracks_skill_completeness_and_authority_metadata() -> None:
    row = {
        "skill_id": 10010801,
        "hero_id": 108,
        "sort": 9,
        "session_id": "4521:maria-current",
        "revealed_items": 2,
        "observed_items": [
            {"runtime_id": 101, "quality": 1},
            {"runtime_id": 102, "quality": 3},
        ],
    }
    exact = _ref_snapshot(
        hero="maria",
        session="4521:maria-current",
        skill_reveals=[row],
    )
    exact_signature = ref_input_signature_from_snapshot(exact)

    for field, replacement in (
        ("revealed_items", None),
        ("session_id", "4521:maria-foreign"),
        ("sort", 10),
    ):
        changed = copy.deepcopy(exact)
        changed["skill_reveals"][0][field] = replacement
        assert ref_input_signature_from_snapshot(changed) != exact_signature, field


def test_all_scheduled_heroes_share_live_inference_route_except_aisha_dual_pass() -> None:
    assert len(HERO_REF_SCHEDULES) == 21
    for hero_key in sorted(HERO_REF_SCHEDULES):
        assert hero_should_run_scheduled_inference(hero_key, 1, "reading"), hero_key
        assert hero_should_run_scheduled_inference(hero_key, 3, "bidding"), hero_key
        assert hero_uses_dual_pass(hero_key, 1, "reading") is (
            hero_key == "aisha"
        ), hero_key


def test_ref_input_signature_changes_on_context_round() -> None:
    snap = _ref_snapshot(round_no=2)
    before = ref_input_signature_from_snapshot(snap)
    snap["round"] = 3
    after = ref_input_signature_from_snapshot(snap)
    assert before != after


def test_ref_input_signature_changes_on_structured_inputs() -> None:
    snap = _ref_snapshot()
    before = ref_input_signature_from_snapshot(snap)
    snap["structured_ref_inputs"] = {"total_count": {"q4": 6}}
    after = ref_input_signature_from_snapshot(snap)
    assert before != after


def test_ref_input_signature_ignores_empty_structured_inputs() -> None:
    """Empty/blank structured inputs must not pollute the signature."""
    snap = _ref_snapshot(structured_ref_inputs={})
    before = ref_input_signature_from_snapshot(snap)
    snap["structured_ref_inputs"] = {"total_count": {}, "fixed_counts": None, "avg_cells": []}
    after = ref_input_signature_from_snapshot(snap)
    assert before == after


def test_ref_input_signature_handles_mixed_type_result_fields() -> None:
    """Real snapshots mix int/str in 'result'/'value'; sorting must not raise."""
    snap = _ref_snapshot(
        action_result_rows=[
            {"action_id": "a1", "result": 0, "result_field": "f1"},
            {"action_id": "a2", "result": "0", "result_field": "f2"},
            {"action_id": "a3", "result": None, "result_field": "f3"},
        ]
    )
    # Must not raise TypeError on mixed-type sort.
    sig = ref_input_signature_from_snapshot(snap)
    assert sig is not None
    # Same inputs in different order must produce the same signature.
    snap2 = _ref_snapshot(
        action_result_rows=[
            {"action_id": "a3", "result": None, "result_field": "f3"},
            {"action_id": "a2", "result": "0", "result_field": "f2"},
            {"action_id": "a1", "result": 0, "result_field": "f1"},
        ]
    )
    assert ref_input_signature_from_snapshot(snap2) == sig
