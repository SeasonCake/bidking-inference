import importlib.util
from pathlib import Path
from types import SimpleNamespace

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.v3.calibration import propose_prior_calibration
from bidking_lab.inference.v3.capacity_source_expansion import (
    CapacitySourceExpansionEntry,
)
from bidking_lab.inference.v3.settlement_count_prior import SettlementCountPriorEntry
from bidking_lab.inference.v3.underestimate_repair import UnderestimateRepairEntry
from bidking_lab.live.fatbeans import FatbeansActionResult
from bidking_lab.live.fatbeans import FatbeansCaptureEvents
from bidking_lab.live.fatbeans import FatbeansInventoryItem
from bidking_lab.live.fatbeans import FatbeansObservedItem
from bidking_lab.live.fatbeans import FatbeansPlayerBid
from bidking_lab.live.fatbeans import FatbeansPublicInfo
from bidking_lab.live.fatbeans import FatbeansSendEvent
from bidking_lab.live.fatbeans import FatbeansSkillReveal
from bidking_lab.live.fatbeans import FatbeansStateEvent

ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    path = ROOT / "scripts" / "evaluate_fatbeans_v3_samples.py"
    spec = importlib.util.spec_from_file_location("evaluate_fatbeans_v3_samples", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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


def _tables() -> SimpleNamespace:
    filler = _item(1011001, quality=1, value=1_000, shape=(1, 1))
    red = _item(1086001, quality=6, value=200_000, shape=(4, 4))
    return SimpleNamespace(
        maps={
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
        drops={
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
        items={filler.item_id: filler, red.item_id: red},
    )


def test_strict_sample_filter_excludes_mixed_filename() -> None:
    module = _load_module()
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        states=(),
        statuses=(),
    )

    assert (
        module._strict_sample_exclusion_reason(
            Path("fatbeans_mixed_victor_2409_3rounds_2409_x_0001.json"),
            events,
        )
        == "mixed_sample"
    )


def test_strict_sample_filter_excludes_quarantined_invalid_path() -> None:
    module = _load_module()
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        states=(),
        statuses=(),
    )

    assert (
        module._strict_sample_exclusion_reason(
            Path("data/samples/fatbeans_invalid/parse_error/recovered.json"),
            events,
        )
        == "quarantined_sample"
    )


def test_include_mixed_samples_still_excludes_quarantined_invalid_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    invalid_dir = tmp_path / "fatbeans_invalid" / "parse_error"
    invalid_dir.mkdir(parents=True)
    sample = invalid_dir / "recovered.json"
    sample.write_text("{}", encoding="utf-8")
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        states=(),
        statuses=(),
    )

    monkeypatch.setattr(module, "parse_fatbeans_capture", lambda path: events)
    monkeypatch.setattr(
        module,
        "_round_rows_for_events",
        lambda *args, **kwargs: [{"status": "ready"}],
    )

    rows, errors = module.evaluate_paths([sample], include_mixed_samples=True)

    assert rows == []
    assert errors == []


def test_strict_sample_filter_excludes_observed_settlement_inconsistency() -> None:
    module = _load_module()
    observed = FatbeansObservedItem(
        local_index=3,
        runtime_id=101,
        item_id=1074003,
        quality=4,
        value=2400,
        shape_code=11,
        cells=1,
    )
    prebid_state = FatbeansStateEvent(
        sort_id=10,
        capture_time="",
        message_id=0x0027,
        session_id="2409:s",
        map_id=2409,
        round_index=1,
        action_results=(
            FatbeansActionResult(
                action_id=100129,
                result=2,
                result_field=14,
                observed_items=(observed,),
            ),
        ),
    )
    settlement_state = FatbeansStateEvent(
        sort_id=30,
        capture_time="",
        message_id=0x002D,
        session_id="2409:s",
        map_id=2409,
        round_index=1,
        inventory_items=(
            FatbeansInventoryItem(
                runtime_id=202,
                item_id=1085010,
                quality=5,
                cells=1,
                local_index=8,
            ),
        ),
    )
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(
            FatbeansSendEvent(
                sort_id=20,
                capture_time="",
                message_id=0x0022,
                session_id="2409:s",
                value=1000,
            ),
        ),
        states=(prebid_state, settlement_state),
        statuses=(),
    )

    assert (
        module._strict_sample_exclusion_reason(
            Path("fatbeans_valid_victor_2409_1rounds_2409_s_0001.json"),
            events,
        )
        == "observed_settlement_inconsistency"
    )


def test_v3_prebid_rows_compile_ready_constraints() -> None:
    module = _load_module()
    state = SimpleNamespace(
        sort_id=5,
        session_id="2401:abc",
        round_index=1,
        map_id=2401,
        player_id=1,
        bids=(SimpleNamespace(player_id=1, hero_id=208, current_value=0),),
        public_infos=(
            SimpleNamespace(
                info_id=200009,
                value=98,
                value_field=14,
                observed_items=(),
            ),
        ),
        action_results=(),
        skill_reveals=(),
        inventory_items=(),
    )
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(SimpleNamespace(sort_id=10, kind="bid", session_id="2401:abc", value=1000),),
        states=(state,),
        statuses=(),
    )

    rows = module._round_rows_for_events(Path("sample.json"), events)

    assert len(rows) == 1
    assert rows[0]["status"] == "ready"
    assert rows[0]["map_family"] == "villa"
    assert rows[0]["hero"] == "ethan"
    assert rows[0]["evidence_stage"] == "early_1_2"
    assert rows[0]["evidence_profile_key"] == "public:total"
    assert rows[0]["information_density_band"] == "low"
    assert rows[0]["hero_map_id"] == "ethan|2401"
    assert rows[0]["hero_map_evidence_profile"] == "ethan|2401|public:total"
    assert rows[0]["numeric_constraints"] == 1
    assert rows[0]["constraint_ok"] is True


def test_v3_prebid_rows_recover_first_ahmad_window_from_next_state() -> None:
    module = _load_module()
    first_bid = FatbeansSendEvent(
        sort_id=10,
        capture_time="2026-06-08 21:39:31.440",
        message_id=0x0022,
        session_id="2410:abc",
        value=389999,
    )
    round_two_state = FatbeansStateEvent(
        sort_id=12,
        capture_time="2026-06-08 21:39:34.778",
        message_id=0x0025,
        session_id="2410:abc",
        map_id=2410,
        round_index=2,
        player_id=1,
        bids=(
            FatbeansPlayerBid(player_id=1, name="local", hero_id=204, values=(389999,)),
            FatbeansPlayerBid(player_id=2, name="other", hero_id=107, values=(200000,)),
        ),
        skill_reveals=(
            FatbeansSkillReveal(
                skill_id=1002041,
                hero_id=204,
                round_index=1,
                result=3.8,
                result_field=11,
            ),
            FatbeansSkillReveal(
                skill_id=1002042,
                hero_id=204,
                round_index=2,
                result=2.6,
                result_field=11,
            ),
        ),
        public_infos=(
            FatbeansPublicInfo(info_id=200009, map_id=2410, value=80, value_field=14),
        ),
    )
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(first_bid,),
        states=(round_two_state,),
        statuses=(),
    )

    rows = module._round_rows_for_events(Path("sample.json"), events)

    assert len(rows) == 1
    assert rows[0]["status"] == "ready"
    assert rows[0]["map_id"] == 2410
    assert rows[0]["hero"] == "ahmed"
    assert rows[0]["recovered_first_prebid_state"] is True
    assert (
        rows[0]["recovered_first_prebid_source"]
        == "ahmad_first_post_bid_state_stable_r1_projection"
    )


def test_v3_prebid_rows_include_prior_and_truth_shadow_fields() -> None:
    module = _load_module()
    prebid_state = SimpleNamespace(
        sort_id=5,
        session_id="2401:abc",
        round_index=1,
        map_id=2401,
        public_infos=(),
        action_results=(),
        skill_reveals=(),
        inventory_items=(),
    )
    settlement_state = SimpleNamespace(
        sort_id=20,
        session_id="2401:abc",
        round_index=5,
        map_id=2401,
        public_infos=(),
        action_results=(),
        skill_reveals=(),
        inventory_items=(
            SimpleNamespace(item_id=1011001, quality=1, cells=1),
            SimpleNamespace(item_id=1086001, quality=6, cells=16),
        ),
    )
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(SimpleNamespace(sort_id=10, kind="bid", session_id="2401:abc", value=1000),),
        states=(prebid_state, settlement_state),
        statuses=(),
    )

    rows = module._round_rows_for_events(
        Path("sample.json"),
        events,
        tables=_tables(),
        settlement_count_prior_entries={
            ("map_id", "2401"): SettlementCountPriorEntry(
                scope="map_id",
                group="2401",
                status="table_caps_cover_observed_shadow_only",
                archive_sessions=10,
                non_temp_inventory_count_p95=2,
                non_temp_inventory_count_max=2,
            )
        },
        capacity_source_expansion_entries={
            ("map_id", "2401"): CapacitySourceExpansionEntry(
                scope="map_id",
                group="2401",
                status="within_capacity_source_semantics_shadow_only",
                archive_sessions=10,
                source_context_classes="public_total_confirmed:1",
                unique_non_temp_p95=2,
                unique_non_temp_max=2,
            )
        },
        posterior_trials=64,
    )

    assert len(rows) == 1
    assert rows[0]["v3_prior_available"] is True
    assert rows[0]["v3_prior_expected_value"] == 201_000
    assert rows[0]["v3_prior_q6_session_probability"] == 0.75
    assert rows[0]["v3_capacity_truth_item_count"] == 2
    assert rows[0]["v3_capacity_prior_items_per_session_max"] == 2
    assert rows[0]["v3_capacity_truth_prior_max_delta"] == 0
    assert rows[0]["v3_capacity_cases"] == "no_capacity_prior_max_case"
    assert rows[0]["v3_robust_available"] is True
    assert rows[0]["v3_robust_status"] == "ok"
    assert rows[0]["v3_robust_prior_usable"] is True
    assert rows[0]["v3_robust_prior_trusted"] is True
    assert rows[0]["v3_robust_affects_bid"] is False
    assert rows[0]["v3_truth_available"] is True
    assert rows[0]["v3_truth_raw_total_value"] == 201_000
    assert rows[0]["v3_truth_q6_raw_value"] == 200_000
    assert rows[0]["v3_truth_decision_available"] is True
    assert rows[0]["v3_truth_formal_decision_value"] == 201_000
    assert rows[0]["v3_truth_tail_replacement_decision_value"] == 201_000
    assert rows[0]["v3_summary_available"] is True
    assert rows[0]["v3_summary_feasible"] is True
    assert rows[0]["v3_summary_known_count_floor"] == 0
    assert rows[0]["v3_capacity_total_count_source"] == "none"
    assert rows[0]["v3_post_available"] is True
    assert rows[0]["v3_post_affects_bid"] is False
    assert rows[0]["v3_post_match_scope"] == "strict"
    assert rows[0]["v3_post_n_total"] == 64
    assert rows[0]["v3_post_formal_decision_value_p50"] is not None
    assert rows[0]["v3_post_q6_formal_decision_value_p50"] is not None
    assert rows[0]["v3_ccv_available"] is True
    assert rows[0]["v3_ccv_affects_bid"] is False
    assert rows[0]["v3_ccv_ready"] is True
    assert rows[0]["v3_resid_available"] is True
    assert rows[0]["v3_resid_affects_bid"] is False
    assert rows[0]["v3_resid_ready"] is True
    assert rows[0]["v3_resid_gate_available"] is True
    assert rows[0]["v3_resid_gate_affects_bid"] is False
    assert rows[0]["v3_rtc_available"] is True
    assert rows[0]["v3_rtc_affects_bid"] is False
    assert rows[0]["v3_rtc_active"] is False
    assert rows[0]["v3_rtc_candidate"] is False
    assert rows[0]["v3_rtc_q6_count_status"] == "missing_total_exact"
    assert rows[0]["v3_rtc_q6_cells_status"] == "missing_total_exact"
    assert rows[0]["v3_rtc_q6_value_status"] == "missing_total_exact"
    assert rows[0]["v3_cal_available"] is True
    assert rows[0]["v3_cal_affects_bid"] is False
    assert rows[0]["v3_cal_active"] is False
    assert rows[0]["v3_cal_status"] == "missing_entry"
    assert rows[0]["v3_under_available"] is True
    assert rows[0]["v3_under_affects_bid"] is False
    assert rows[0]["v3_under_active"] is False
    assert rows[0]["v3_under_candidate"] is False
    assert rows[0]["v3_under_status"] == "missing_entry"
    assert rows[0]["v3_fv_available"] is True
    assert rows[0]["v3_fv_affects_bid"] is False
    assert rows[0]["v3_fv_active"] is False
    assert rows[0]["v3_fv_candidate"] is False
    assert rows[0]["v3_fv_status"] == "baseline_passthrough"
    assert rows[0]["v3_scp_available"] is True
    assert rows[0]["v3_scp_ready"] is True
    assert rows[0]["v3_scp_affects_bid"] is False
    assert rows[0]["v3_scp_active"] is False
    assert rows[0]["v3_scp_candidate"] is False
    assert rows[0]["v3_scp_status"] == "table_caps_cover_observed_shadow_only"
    assert rows[0]["v3_scp_scope"] == "map_id"
    assert rows[0]["v3_scp_group"] == "2401"
    assert rows[0]["v3_cse_available"] is True
    assert rows[0]["v3_cse_ready"] is True
    assert rows[0]["v3_cse_affects_bid"] is False
    assert rows[0]["v3_cse_active"] is False
    assert rows[0]["v3_cse_candidate"] is False
    assert rows[0]["v3_cse_pressure_candidate"] is False
    assert rows[0]["v3_cse_status"] == "within_capacity_source_semantics_shadow_only"
    assert rows[0]["v3_cse_scope"] == "map_id"
    assert rows[0]["v3_cse_group"] == "2401"
    assert rows[0]["v3_cse_source_context_classes"] == "public_total_confirmed:1"
    assert rows[0]["v3_practical_available"] is True
    assert rows[0]["v3_practical_affects_bid"] is False
    assert rows[0]["v3_practical_active"] is False
    assert rows[0]["v3_practical_status"] == "baseline_passthrough"
    assert rows[0]["v3_practical_formal_decision_value_p50"] is not None


def test_capacity_flat_dict_reports_prior_max_gap() -> None:
    module = _load_module()

    fields = module._capacity_flat_dict(
        prior_fields={
            "v3_prior_items_per_session_min": 2,
            "v3_prior_items_per_session_max": 5,
        },
        truth_fields={"v3_truth_item_count": 7},
        summary_fields={
            "v3_summary_session_total_count_exact": 7,
            "v3_summary_known_count_floor": 6,
        },
    )

    assert fields["v3_capacity_total_count_source"] == "exact"
    assert fields["v3_capacity_total_count_target"] == 7
    assert fields["v3_capacity_truth_item_count"] == 7
    assert fields["v3_capacity_target_prior_max_delta"] == 2
    assert fields["v3_capacity_truth_prior_max_delta"] == 2
    assert fields["v3_capacity_target_truth_delta"] == 0
    assert fields["v3_capacity_target_prior_max_ratio"] == 1.4
    assert fields["v3_capacity_truth_prior_max_ratio"] == 1.4
    assert fields["v3_capacity_flags"] == (
        "target_count_above_prior_max+truth_count_above_prior_max"
    )
    assert fields["v3_capacity_cases"] == "direct_prior_max_conflict"


def test_v3_prebid_rows_mark_252x_activity_map_as_missing_prior() -> None:
    module = _load_module()
    prebid_state = SimpleNamespace(
        sort_id=5,
        session_id="2526:activity",
        round_index=1,
        map_id=2526,
        public_infos=(),
        action_results=(),
        skill_reveals=(),
        inventory_items=(),
    )
    settlement_state = SimpleNamespace(
        sort_id=20,
        session_id="2526:activity",
        round_index=5,
        map_id=2526,
        public_infos=(),
        action_results=(),
        skill_reveals=(),
        inventory_items=(
            SimpleNamespace(item_id=1011001, quality=1, cells=1),
            SimpleNamespace(item_id=1086001, quality=6, cells=16),
        ),
    )
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(
            SimpleNamespace(
                sort_id=10,
                kind="bid",
                session_id="2526:activity",
                value=1000,
            ),
        ),
        states=(prebid_state, settlement_state),
        statuses=(),
    )

    rows = module._round_rows_for_events(
        Path("activity.json"),
        events,
        tables=_tables(),
        posterior_trials=64,
    )

    assert len(rows) == 1
    assert rows[0]["map_family"] == "shipwreck"
    assert rows[0]["v3_prior_available"] is False
    assert rows[0]["v3_prior_error"] == "KeyError"
    assert rows[0]["v3_robust_status"] == "prior_unavailable"
    assert rows[0]["v3_robust_prior_usable"] is False
    assert rows[0]["v3_robust_prior_trusted"] is False
    assert rows[0]["v3_robust_activity_candidate"] is True
    assert rows[0]["v3_robust_fallback_mode"] == "missing_prior_truth_only"
    assert "activity_map_id_candidate" in rows[0]["v3_robust_reasons"]
    assert rows[0]["v3_truth_available"] is True
    assert rows[0]["v3_truth_decision_available"] is True
    assert rows[0]["v3_post_available"] is True
    assert rows[0]["v3_post_ready"] is False


def test_v3_prebid_rows_apply_calibration_shadow_fields() -> None:
    module = _load_module()
    prebid_state = SimpleNamespace(
        sort_id=5,
        session_id="2401:abc",
        round_index=1,
        map_id=2401,
        public_infos=(),
        action_results=(),
        skill_reveals=(),
        inventory_items=(),
    )
    settlement_state = SimpleNamespace(
        sort_id=20,
        session_id="2401:abc",
        round_index=5,
        map_id=2401,
        public_infos=(),
        action_results=(),
        skill_reveals=(),
        inventory_items=(
            SimpleNamespace(item_id=1011001, quality=1, cells=1),
            SimpleNamespace(item_id=1086001, quality=6, cells=16),
        ),
    )
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(SimpleNamespace(sort_id=10, kind="bid", session_id="2401:abc", value=1000),),
        states=(prebid_state, settlement_state),
        statuses=(),
    )
    entry = propose_prior_calibration(
        map_id=2401,
        map_family="villa",
        archive_sessions=70,
        median_ratio=1.5,
        p90_ratio=1.2,
        formal_p50_over_rate=0.4,
        baseline_formal_p50_mae=100_000,
        baseline_formal_p50_bias=-60_000,
    )

    rows = module._round_rows_for_events(
        Path("sample.json"),
        events,
        tables=_tables(),
        calibration_entries={2401: entry},
        posterior_trials=64,
    )

    assert rows[0]["v3_cal_available"] is True
    assert rows[0]["v3_cal_active"] is True
    assert rows[0]["v3_cal_affects_bid"] is False
    assert rows[0]["v3_cal_scale"] > 1.0
    assert rows[0]["v3_cal_formal_decision_value_p50"] >= rows[0][
        "v3_post_formal_decision_value_p50"
    ]


def test_v3_prebid_rows_apply_underestimate_repair_shadow_fields() -> None:
    module = _load_module()
    prebid_state = SimpleNamespace(
        sort_id=5,
        session_id="2401:abc",
        round_index=1,
        map_id=2401,
        player_id=1,
        bids=(SimpleNamespace(player_id=1, hero_id=208, current_value=0),),
        public_infos=(),
        action_results=(),
        skill_reveals=(),
        inventory_items=(),
    )
    settlement_state = SimpleNamespace(
        sort_id=20,
        session_id="2401:abc",
        round_index=5,
        map_id=2401,
        public_infos=(),
        action_results=(),
        skill_reveals=(),
        inventory_items=(
            SimpleNamespace(item_id=1011001, quality=1, cells=1),
            SimpleNamespace(item_id=1086001, quality=6, cells=16),
        ),
    )
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(SimpleNamespace(sort_id=10, kind="bid", session_id="2401:abc", value=1000),),
        states=(prebid_state, settlement_state),
        statuses=(),
    )
    entry = UnderestimateRepairEntry(
        hero="ethan",
        map_id=2401,
        archive_windows=110,
        archive_sessions=29,
        status="watch_only_upshift_candidate",
        gate_reason="bounded_hero_map_upshift",
        scale=1.05,
    )

    rows = module._round_rows_for_events(
        Path("sample.json"),
        events,
        tables=_tables(),
        underestimate_repair_entries={entry.key: entry},
        posterior_trials=64,
    )

    assert rows[0]["hero"] == "ethan"
    assert rows[0]["v3_under_available"] is True
    assert rows[0]["v3_under_candidate"] is True
    assert rows[0]["v3_under_active"] is False
    assert rows[0]["v3_under_affects_bid"] is False
    assert rows[0]["v3_under_scale"] == 1.05
    assert rows[0]["v3_under_source"] == "bounded_upshift"
    assert rows[0]["v3_under_formal_decision_value_p50"] >= rows[0][
        "v3_post_formal_decision_value_p50"
    ]


def test_v3_summary_metrics_use_formal_truth_and_prediction() -> None:
    module = _load_module()
    rows = [
        {
            "status": "ready",
            "v3_truth_decision_available": True,
            "v3_truth_available": True,
            "v3_post_ready": True,
            "v3_post_match_scope": "strict",
            "v3_truth_formal_decision_value": 100,
            "v3_post_formal_decision_value_p50": 90,
            "v3_post_formal_decision_value_p90": 120,
            "v3_truth_q6_formal_decision_value": 30,
            "v3_post_q6_formal_decision_value_p50": 10,
            "v3_post_q6_formal_decision_value_p90": 40,
            "v3_truth_q6_count": 1,
            "v3_truth_q6_cells": 4,
            "v3_truth_q6_raw_value": 100,
            "v3_post_q6_count_p50": 0,
            "v3_post_q6_count_p90": 1,
            "v3_post_q6_cells_p50": 2,
            "v3_post_q6_cells_p90": 4,
            "v3_post_q6_value_p50": 80,
            "v3_ccv_ready": True,
            "v3_ccv_match_scope": "ccv_likelihood",
            "v3_ccv_q6_count_p50": 1,
            "v3_ccv_q6_count_p90": 1,
            "v3_ccv_q6_cells_p50": 4,
            "v3_ccv_q6_cells_p90": 4,
            "v3_ccvc_ready": True,
            "v3_ccvc_match_scope": "ccv_component_likelihood",
            "v3_ccvc_q6_count_p50": 1,
            "v3_ccvc_q6_count_p90": 1,
            "v3_ccvc_q6_cells_p50": 4,
            "v3_ccvc_q6_cells_p90": 4,
            "v3_ccvc_q6_value_p50": 100,
            "v3_resid_ready": True,
            "v3_resid_match_scope": "residual_likelihood",
            "v3_resid_q6_count_p50": 1,
            "v3_resid_q6_count_p90": 1,
            "v3_resid_q6_cells_p50": 4,
            "v3_resid_q6_cells_p90": 4,
            "v3_resid_q6_value_p50": 100,
            "v3_resid_gate_ready": True,
            "v3_resid_gate_active": True,
            "v3_resid_gate_q6_count_p50": 1,
            "v3_resid_gate_q6_count_p90": 1,
            "v3_resid_gate_q6_cells_p50": 4,
            "v3_resid_gate_q6_cells_p90": 4,
            "v3_resid_gate_q6_value_p50": 100,
            "v3_fv_ready": True,
            "v3_fv_active": False,
            "v3_fv_candidate": True,
            "v3_fv_stress_class": "value_floor_stress",
            "v3_fv_formal_decision_value_p50": 95,
            "v3_fv_formal_decision_value_p90": 120,
            "v3_fv_q6_formal_decision_value_p50": 20,
            "v3_fv_q6_formal_decision_value_p90": 40,
        },
        {
            "status": "ready",
            "v3_truth_decision_available": True,
            "v3_truth_available": True,
            "v3_post_ready": True,
            "v3_post_match_scope": "q6_projection",
            "v3_truth_formal_decision_value": 200,
            "v3_post_formal_decision_value_p50": 250,
            "v3_post_formal_decision_value_p90": 260,
            "v3_truth_q6_formal_decision_value": 0,
            "v3_post_q6_formal_decision_value_p50": 0,
            "v3_post_q6_formal_decision_value_p90": 0,
            "v3_truth_q6_count": 0,
            "v3_truth_q6_cells": 0,
            "v3_truth_q6_raw_value": 0,
            "v3_post_q6_count_p50": 1,
            "v3_post_q6_count_p90": 1,
            "v3_post_q6_cells_p50": 5,
            "v3_post_q6_cells_p90": 5,
            "v3_post_q6_value_p50": 50,
            "v3_ccv_ready": True,
            "v3_ccv_match_scope": "q6_projection",
            "v3_ccv_q6_count_p50": 0,
            "v3_ccv_q6_count_p90": 0,
            "v3_ccv_q6_cells_p50": 0,
            "v3_ccv_q6_cells_p90": 0,
            "v3_ccvc_ready": False,
            "v3_ccvc_match_scope": None,
            "v3_ccvc_q6_count_p50": None,
            "v3_ccvc_q6_count_p90": None,
            "v3_ccvc_q6_cells_p50": None,
            "v3_ccvc_q6_cells_p90": None,
            "v3_ccvc_q6_value_p50": None,
            "v3_resid_ready": True,
            "v3_resid_match_scope": "q6_projection",
            "v3_resid_q6_count_p50": 0,
            "v3_resid_q6_count_p90": 0,
            "v3_resid_q6_cells_p50": 0,
            "v3_resid_q6_cells_p90": 0,
            "v3_resid_q6_value_p50": 0,
            "v3_resid_gate_ready": True,
            "v3_resid_gate_active": False,
            "v3_resid_gate_q6_count_p50": 1,
            "v3_resid_gate_q6_count_p90": 1,
            "v3_resid_gate_q6_cells_p50": 5,
            "v3_resid_gate_q6_cells_p90": 5,
            "v3_resid_gate_q6_value_p50": 50,
            "v3_cal_ready": True,
            "v3_cal_active": True,
            "v3_cal_formal_decision_value_p50": 220,
            "v3_cal_formal_decision_value_p90": 260,
            "v3_cal_q6_formal_decision_value_p50": 0,
            "v3_cal_q6_formal_decision_value_p90": 0,
            "v3_fv_ready": True,
            "v3_fv_active": False,
            "v3_fv_candidate": False,
            "v3_fv_stress_class": "capacity_cells_drift",
            "v3_fv_formal_decision_value_p50": 250,
            "v3_fv_formal_decision_value_p90": 260,
            "v3_fv_q6_formal_decision_value_p50": 0,
            "v3_fv_q6_formal_decision_value_p90": 0,
        },
    ]

    summary = module.summarize_rows(rows, [])

    assert summary["metric_rows"] == 2
    assert summary["metric_strict_rows"] == 1
    assert summary["metric_fallback_rows"] == 1
    assert summary["formal_p50_mae"] == 30
    assert summary["formal_p50_mae_strict"] == 10
    assert summary["formal_p50_mae_fallback"] == 50
    assert summary["formal_p50_bias"] == 20
    assert summary["formal_p50_below_rate"] == 0.5
    assert summary["formal_p50_over_rate"] == 0.5
    assert summary["formal_p50_pinball"] == 15
    assert summary["formal_p90_coverage"] == 1.0
    assert summary["formal_p90_extreme_over_rate"] == 0.0
    assert summary["formal_p90_coverage_strict"] == 1.0
    assert summary["formal_p90_coverage_fallback"] == 1.0
    assert summary["formal_p90_pinball"] == 4
    assert summary["q6_formal_p50_mae"] == 10
    assert summary["q6_formal_p50_mae_strict"] == 20
    assert summary["q6_formal_p50_mae_fallback"] == 0
    assert summary["q6_formal_p50_bias"] == -10
    assert summary["q6_formal_p50_over_rate"] == 0.0
    assert summary["q6_formal_p90_extreme_over_rate"] == 0.0
    assert summary["q6_formal_p90_pinball"] == 0.5
    assert summary["q6_count_p50_mae"] == 1
    assert summary["q6_cells_p50_mae"] == 3.5
    assert summary["v3_ccv_likelihood_rows"] == 1
    assert summary["v3_ccv_q6_count_p50_mae"] == 0
    assert summary["v3_ccv_delta_q6_count_p50_mae"] == -1
    assert summary["v3_ccv_q6_cells_p50_mae"] == 0
    assert summary["v3_ccv_delta_q6_cells_p50_mae"] == -3.5
    assert summary["q6_value_p50_mae"] == 35
    assert summary["v3_ccvc_component_likelihood_rows"] == 1
    assert summary["v3_ccvc_q6_count_p50_mae"] == 0
    assert summary["v3_ccvc_delta_q6_count_p50_mae"] == -1
    assert summary["v3_ccvc_q6_cells_p50_mae"] == 0
    assert summary["v3_ccvc_delta_q6_cells_p50_mae"] == -3.5
    assert summary["v3_ccvc_q6_value_p50_mae"] == 0
    assert summary["v3_ccvc_delta_q6_value_p50_mae"] == -35
    assert summary["v3_resid_likelihood_rows"] == 1
    assert summary["v3_resid_q6_count_p50_mae"] == 0
    assert summary["v3_resid_delta_q6_count_p50_mae"] == -1
    assert summary["v3_resid_q6_cells_p50_mae"] == 0
    assert summary["v3_resid_delta_q6_cells_p50_mae"] == -3.5
    assert summary["v3_resid_q6_value_p50_mae"] == 0
    assert summary["v3_resid_delta_q6_value_p50_mae"] == -35
    assert summary["v3_resid_gate_active_rows"] == 1
    assert summary["v3_resid_gate_q6_count_p50_mae"] == 0.5
    assert summary["v3_resid_gate_delta_q6_count_p50_mae"] == -0.5
    assert summary["v3_resid_gate_q6_cells_p50_mae"] == 2.5
    assert summary["v3_resid_gate_delta_q6_cells_p50_mae"] == -1
    assert summary["v3_resid_gate_q6_value_p50_mae"] == 25
    assert summary["v3_resid_gate_delta_q6_value_p50_mae"] == -10
    assert summary["v3_cal_metric_rows"] == 1
    assert summary["v3_cal_active_rows"] == 1
    assert summary["v3_cal_formal_p50_mae"] == 20
    assert summary["v3_cal_delta_formal_p50_mae"] == -10
    assert summary["v3_tail_review_candidate_rows"] == 0
    assert summary["v3_tail_review_hurt_guard_rows"] == 0
    assert summary["v3_tail_review_active_rows"] == 0
    assert summary["v3_fv_metric_rows"] == 2
    assert summary["v3_fv_candidate_rows"] == 1
    assert summary["v3_fv_active_rows"] == 0
    assert summary["v3_fv_capacity_watch_rows"] == 1
    assert summary["v3_fv_value_floor_candidate_rows"] == 1
    assert summary["v3_fv_formal_p50_mae"] == 27.5
    assert summary["v3_fv_delta_formal_p50_mae"] == -2.5
    assert summary["v3_fv_formal_p50_below_rate"] == 0.5
    assert summary["v3_fv_formal_p90_coverage"] == 1.0
    assert summary["v3_cse_pressure_candidate_rows"] == 0


def test_v3_summary_reports_practical_raise_watch_quality() -> None:
    module = _load_module()
    rows = [
        {
            "status": "ready",
            "v3_truth_decision_available": True,
            "v3_truth_formal_decision_value": 200_000,
            "v3_post_formal_decision_value_p90": 150_000,
            "v3_practical_ready": True,
            "v3_practical_candidate": True,
            "v3_practical_recommendation": "raise_watch",
            "v3_practical_formal_decision_value_p50": 190_000,
            "v3_practical_formal_decision_value_p90": 220_000,
        },
        {
            "status": "ready",
            "v3_truth_decision_available": True,
            "v3_truth_formal_decision_value": 200_000,
            "v3_post_formal_decision_value_p90": 150_000,
            "v3_practical_ready": True,
            "v3_practical_candidate": True,
            "v3_practical_recommendation": "raise_watch",
            "v3_practical_formal_decision_value_p50": 190_000,
            "v3_practical_formal_decision_value_p90": 180_000,
        },
        {
            "status": "ready",
            "v3_truth_decision_available": True,
            "v3_truth_formal_decision_value": 200_000,
            "v3_post_formal_decision_value_p90": 220_000,
            "v3_practical_ready": True,
            "v3_practical_candidate": True,
            "v3_practical_recommendation": "raise_watch",
            "v3_practical_formal_decision_value_p50": 220_000,
            "v3_practical_formal_decision_value_p90": 450_001,
        },
    ]

    summary = module.summarize_rows(rows, [])

    assert summary["v3_practical_metric_rows"] == 3
    assert summary["v3_practical_raise_watch_rows"] == 3
    assert summary["v3_practical_raise_watch_evaluable_rows"] == 3
    assert summary["v3_practical_raise_watch_hit_rows"] == 1
    assert summary["v3_practical_raise_watch_miss_rows"] == 1
    assert summary["v3_practical_raise_watch_false_alarm_rows"] == 1
    assert summary["v3_practical_raise_watch_extreme_over_rows"] == 1
    assert summary["v3_practical_raise_watch_misleading_rows"] == 1
    assert summary["v3_practical_raise_watch_hit_rate"] == 0.333333
    assert summary["v3_practical_raise_watch_miss_rate"] == 0.333333
    assert summary["v3_practical_raise_watch_false_alarm_rate"] == 0.333333
    assert summary["v3_practical_raise_watch_extreme_over_rate"] == 0.333333
    assert summary["v3_practical_raise_watch_misleading_rate"] == 0.333333
    assert summary["v3_practical_formal_p90_extreme_over_rate"] == 0.333333


def test_v3_prebid_rows_separate_no_state_windows() -> None:
    module = _load_module()
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(SimpleNamespace(sort_id=10, kind="bid", session_id="2401:abc", value=1000),),
        states=(),
        statuses=(),
    )

    rows = module._round_rows_for_events(Path("sample.json"), events)

    assert len(rows) == 1
    assert rows[0]["status"] == "no_state"
    assert rows[0]["map_family"] == "unknown"
    assert rows[0]["hero"] == "unknown"
    assert rows[0]["evidence_stage"] == "early_1_2"
    assert rows[0]["evidence_profile_key"] == "basic"
    assert rows[0]["numeric_constraints"] == 0
    assert rows[0]["constraint_ok"] is False
    assert rows[0]["v3_rtc_available"] is False
    assert rows[0]["v3_rtc_affects_bid"] is False
    assert rows[0]["v3_rtc_active"] is False
