import importlib.util
from pathlib import Path
from types import SimpleNamespace

from bidking_lab.live.fatbeans import FatbeansCaptureEvents

ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    path = ROOT / "scripts" / "summarize_fatbeans_sample_manifest.py"
    spec = importlib.util.spec_from_file_location("summarize_fatbeans_sample_manifest", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_manifest_classifies_mixed_capture_windows() -> None:
    module = _load_module()
    state = SimpleNamespace(
        sort_id=10,
        session_id="2401:abc",
        round_index=2,
        map_id=2401,
        bids=(SimpleNamespace(hero_id=208),),
        public_infos=(
            SimpleNamespace(info_id=200009),
            SimpleNamespace(info_id=200017),
        ),
        action_results=(SimpleNamespace(action_id=100104),),
        skill_reveals=(),
        inventory_items=(),
    )
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(
            SimpleNamespace(sort_id=5, kind="bid", session_id="2401:abc", value=1000),
            SimpleNamespace(sort_id=20, kind="bid", session_id="2401:abc", value=2000),
        ),
        states=(state,),
        statuses=(),
    )

    row = module._file_manifest_for_events(Path("sample.json"), events)

    assert row["sample_class"] == "mixed"
    assert row["status"] == "ready_with_gaps_or_conflicts"
    assert row["usable_for_metrics"] is True
    assert row["bid_windows"] == 2
    assert row["ready_windows"] == 1
    assert row["no_state_windows"] == 1
    assert row["window_status_counts"] == {"no_state": 1, "ready": 1}
    assert row["map_ids"] == [2401]
    assert row["hero_ids"] == [208]
    assert row["public_info_counts"] == {"200009": 1, "200017": 1}
    assert row["exact_public_info_counts"] == {"200009": 1, "200017": 1}
    assert row["action_result_counts"] == {"100104": 1}


def test_manifest_records_parse_errors(tmp_path: Path) -> None:
    module = _load_module()
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")

    manifest = module.build_manifest([tmp_path])

    summary = manifest["summary"]
    assert summary["files"] == 1
    assert summary["parsed_files"] == 0
    assert summary["parse_errors"] == 1
    assert summary["invalid_files"] == 1
    assert summary["usable_metric_files"] == 0
    assert manifest["files"][0]["status"] == "parse_error"
    assert manifest["files"][0]["cleanup_action"] == "quarantine_parse_error"


def test_manifest_marks_observed_settlement_inconsistency_mixed() -> None:
    module = _load_module()
    observed_item = SimpleNamespace(
        runtime_id=101,
        local_index=3,
        quality=4,
        item_id=None,
    )
    prebid_state = SimpleNamespace(
        sort_id=10,
        session_id="2401:abc",
        round_index=1,
        map_id=2401,
        bids=(SimpleNamespace(hero_id=209),),
        public_infos=(
            SimpleNamespace(info_id=200001, observed_items=(observed_item,)),
        ),
        action_results=(),
        skill_reveals=(),
        inventory_items=(),
    )
    settlement_state = SimpleNamespace(
        sort_id=30,
        session_id="2401:abc",
        round_index=1,
        map_id=2401,
        bids=(SimpleNamespace(hero_id=209),),
        public_infos=(),
        action_results=(),
        skill_reveals=(),
        inventory_items=(
            SimpleNamespace(runtime_id=202, local_index=9, quality=4),
        ),
    )
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(SimpleNamespace(sort_id=20, kind="bid", session_id="2401:abc", value=1000),),
        states=(prebid_state, settlement_state),
        statuses=(),
    )

    row = module._file_manifest_for_events(Path("sample.json"), events)

    assert row["sample_class"] == "mixed"
    assert row["status"] == "observed_settlement_inconsistency"
    assert row["cleanup_action"] == "exclude_strict_hard_evidence_metrics"
    assert row["usable_for_metrics"] is False
    assert row["observed_settlement_hard_items"] == 1
    assert row["observed_settlement_missing_items"] == 1
    assert row["observed_settlement_missing_examples"][0]["runtime_id"] == 101


def test_manifest_keeps_prior_invalid_path_quarantined() -> None:
    module = _load_module()
    state = SimpleNamespace(
        sort_id=10,
        session_id="2401:abc",
        round_index=1,
        map_id=2401,
        bids=(SimpleNamespace(hero_id=209),),
        public_infos=(),
        action_results=(),
        skill_reveals=(),
        inventory_items=(),
    )
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(SimpleNamespace(sort_id=20, kind="bid", session_id="2401:abc", value=1000),),
        states=(state,),
        statuses=(),
    )

    row = module._file_manifest_for_events(
        Path("data/samples/fatbeans_invalid/parse_error/recovered.json"),
        events,
    )

    assert row["sample_class"] == "invalid"
    assert row["status"] == "quarantined_sample"
    assert row["cleanup_action"] == "quarantine_prior_invalid_sample"
    assert row["usable_for_metrics"] is False


def test_manifest_marks_multi_session_capture_mixed() -> None:
    module = _load_module()
    state_a = SimpleNamespace(
        sort_id=10,
        session_id="2401:abc",
        round_index=1,
        map_id=2401,
        bids=(SimpleNamespace(hero_id=209),),
        public_infos=(),
        action_results=(),
        skill_reveals=(),
        inventory_items=(),
    )
    state_b = SimpleNamespace(
        sort_id=15,
        session_id="2402:def",
        round_index=1,
        map_id=2402,
        bids=(),
        public_infos=(),
        action_results=(SimpleNamespace(action_id=100129, observed_items=()),),
        skill_reveals=(),
        inventory_items=(),
    )
    settlement_state = SimpleNamespace(
        sort_id=30,
        session_id="2401:abc",
        round_index=1,
        map_id=2401,
        bids=(SimpleNamespace(hero_id=209),),
        public_infos=(),
        action_results=(),
        skill_reveals=(),
        inventory_items=(SimpleNamespace(runtime_id=202, local_index=9, quality=4),),
    )
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(SimpleNamespace(sort_id=20, kind="bid", session_id="2401:abc", value=1000),),
        states=(state_a, state_b, settlement_state),
        statuses=(),
    )

    row = module._file_manifest_for_events(Path("sample.json"), events)

    assert row["sample_class"] == "mixed"
    assert row["status"] == "multi_session_capture"
    assert row["cleanup_action"] == "exclude_strict_session_metrics"
    assert row["usable_for_metrics"] is False
    assert row["session_count"] == 2


def test_manifest_can_label_reference_cohort_metadata(tmp_path: Path) -> None:
    module = _load_module()
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")

    manifest = module.build_manifest(
        [tmp_path],
        cohort_role="activity_tuning_reference",
        metric_scope="source_parser_reference_only",
        cohort_note="excluded from default baseline",
    )

    summary = manifest["summary"]
    assert summary["cohort_role"] == "activity_tuning_reference"
    assert summary["metric_scope"] == "source_parser_reference_only"
    assert summary["cohort_note"] == "excluded from default baseline"
    assert summary["affects_bid"] is False
    assert manifest["files"][0]["cohort_role"] == "activity_tuning_reference"
    assert manifest["files"][0]["affects_bid"] is False
