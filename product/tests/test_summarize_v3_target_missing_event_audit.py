import importlib.util
from pathlib import Path
from types import SimpleNamespace

from bidking_lab.inference.v3 import compile_feasible_summary, compile_hard_constraints
from bidking_lab.inference.v3.events import EvidenceEvent


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_target_missing_event_audit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_target_missing_event_audit",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _event(
    event_id: str,
    *,
    targets: tuple[str, ...],
    payload_items: tuple[dict[str, object], ...],
    strength: str = "hard",
    semantic: str = "fixture",
) -> EvidenceEvent:
    return EvidenceEvent(
        event_id=event_id,
        source_kind="fixture",
        source_id=event_id,
        semantic=semantic,
        strength=strength,
        constraint=strength,
        targets=targets,
        payload={"items": payload_items},
    )


def _numeric_event(event_id: str, target: str, value: int) -> EvidenceEvent:
    return EvidenceEvent(
        event_id=event_id,
        source_kind="fixture",
        source_id=event_id,
        semantic=event_id,
        strength="hard",
        constraint="hard",
        targets=(target,),
        payload={"value": value},
    )


def test_event_diagnostics_exposes_disjoint_shape_and_quality_evidence() -> None:
    module = _load_module()
    events = (
        _event(
            "shape-total",
            targets=("session.total_count", "session.total_cells", "shape_anchors"),
            payload_items=({"runtime_id": 1, "shape_key": "22", "cells": 4},),
            semantic="full_outline_session_total",
        ),
        _event(
            "item-no-value",
            targets=("item_anchors", "shape_anchors"),
            payload_items=(
                {
                    "runtime_id": 2,
                    "item_id": 1001,
                },
            ),
            semantic="single_item_reveal",
        ),
        _event(
            "quality-only",
            targets=("quality_floors",),
            payload_items=({"local_index": 9, "quality": 6},),
            strength="partial",
            semantic="random_quality_reveal",
        ),
    )
    constraints = compile_hard_constraints(events)
    summary = compile_feasible_summary(constraints)

    result = module._event_diagnostics(events, constraints, summary)

    assert result["key_target_presence"] == {
        "session.total_count": True,
        "session.total_cells": True,
        "bucket.q6.count": False,
        "bucket.q6.cells": False,
        "bucket.q6.value": False,
    }
    assert result["numeric_target_values"] == {
        "session.total_cells": 4,
        "session.total_count": 1,
    }
    assert result["anchor_event_source_id_counts"] == {
        "fixture:item-no-value": 1,
        "fixture:quality-only": 1,
        "fixture:shape-total": 1,
    }
    assert result["key_event_details"][0]["payload_items"]["items"] == 1
    assert result["summary_fields"]["session_total_cells_exact"] == 4
    assert result["summary_fields"]["q6_cells_exact"] is None
    assert result["summary_fields"]["q6_value_exact"] is None
    assert result["summary_fields"]["q6_count_floor"] == 1
    assert result["summary_fields"]["q6_cells_floor"] == 0
    assert result["summary_fields"]["q6_value_floor"] == 0
    assert result["constraint_anchor_summary"]["item_anchors"]["with_value"] == 0
    assert result["constraint_anchor_summary"]["shape_anchors"]["with_cells"] == 1
    assert result["constraint_anchor_summary"]["shape_anchors"]["q6_count"] == 0
    assert result["constraint_anchor_summary"]["quality_floor_anchors"]["q6_count"] == 1
    assert result["payload_item_summary"]["q6_items"] == 1
    assert result["payload_item_summary"]["q6_with_cells"] == 0
    assert result["payload_item_summary"]["q6_with_value"] == 0
    assert result["q6_residual_target_candidate"]["count"]["status"] == (
        "missing_non_q6_exact"
    )
    assert result["q6_residual_target_candidate"]["cells"]["status"] == (
        "missing_non_q6_exact"
    )
    assert result["q6_residual_target_candidate"]["value"]["status"] == (
        "missing_total_exact"
    )


def test_q6_residual_target_candidate_derives_exact_when_non_q6_complete() -> None:
    module = _load_module()
    events = [
        _numeric_event("total-count", "session.total_count", 7),
        _numeric_event("total-cells", "session.total_cells", 16),
        _numeric_event("total-value", "session.total_value", 100),
    ]
    for quality in range(1, 6):
        events.extend(
            [
                _numeric_event(f"q{quality}-count", f"bucket.q{quality}.count", 1),
                _numeric_event(f"q{quality}-cells", f"bucket.q{quality}.cells", 2),
                _numeric_event(f"q{quality}-value", f"bucket.q{quality}.value", 12),
            ]
        )
    constraints = compile_hard_constraints(events)
    summary = compile_feasible_summary(constraints)

    result = module._event_diagnostics(events, constraints, summary)
    candidate = result["q6_residual_target_candidate"]

    assert candidate["derived_fields"] == ["count", "cells", "value"]
    assert candidate["count"]["status"] == "derived"
    assert candidate["count"]["value"] == 2
    assert candidate["cells"]["status"] == "derived"
    assert candidate["cells"]["value"] == 6
    assert candidate["value"]["status"] == "derived"
    assert candidate["value"]["value"] == 40


def _stress_source_row(file_ref: str) -> dict[str, object]:
    return {
        "file": file_ref,
        "status": "ready",
        "round": 1,
        "session_id": "session-1",
        "bid_sort_id": 20,
        "hero": "aisha",
        "map_id": 2502,
        "map_family": "shipwreck",
        "evidence_profile_key": "shape+layout",
        "hero_map_evidence_profile": "aisha|2502|shape+layout",
        "v3_robust_prior_stress_score": 3,
        "v3_robust_reasons": "q6_cells_above_prior",
        "v3_robust_fallback_mode": "posterior",
        "v3_post_match_scope": "map",
        "numeric_constraints": 2,
        "item_anchors": 1,
        "shape_anchors": 2,
        "quality_floor_anchors": 1,
        "v3_summary_session_total_count_exact": 2,
        "v3_summary_session_total_cells_exact": 4,
        "v3_summary_known_count_floor": 1,
        "v3_summary_known_cells_floor": 4,
        "v3_summary_known_value_floor": 0,
        "v3_summary_q6_cells_exact": None,
        "v3_summary_q6_cells_floor": 0,
        "v3_summary_q6_value_exact": None,
        "v3_summary_q6_value_floor": 0,
        "v3_prior_expected_cells": 2,
        "v3_prior_q6_expected_cells": 2,
        "v3_prior_expected_value": 100,
        "v3_prior_q6_expected_value": 40,
        "v3_truth_total_cells": 4,
        "v3_truth_q6_cells": 4,
        "v3_truth_formal_decision_value": 1000,
        "v3_truth_q6_raw_value": 900,
        "v3_post_total_cells_p50": 4,
        "v3_post_total_cells_p90": 4,
        "v3_post_q6_cells_p50": 3,
        "v3_post_q6_cells_p90": 4,
        "v3_post_formal_decision_value_p50": 700,
        "v3_post_formal_decision_value_p90": 900,
        "v3_post_q6_value_p50": 500,
        "v3_post_q6_value_p90": 800,
        "v3_prior_items_per_session_min": 1,
        "v3_prior_items_per_session_max": 10,
        "v3_truth_item_count": 2,
    }


def test_target_missing_event_audit_replays_prebid_prefix(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    sample = tmp_path / "sample.json"
    sample.write_text("[]", encoding="utf-8")

    outline_item = SimpleNamespace(
        runtime_id=1,
        local_index=None,
        item_id=None,
        quality=None,
        value=None,
        shape_code=22,
        cells=4,
    )
    item_without_value = SimpleNamespace(
        runtime_id=2,
        local_index=None,
        item_id=1001,
        quality=None,
        value=None,
        shape_code=None,
        cells=None,
    )
    quality_item = SimpleNamespace(
        runtime_id=None,
        local_index=9,
        item_id=None,
        quality=6,
        value=None,
        shape_code=None,
        cells=None,
    )
    state = SimpleNamespace(
        sort_id=10,
        session_id="session-1",
        round_index=1,
        map_id=2502,
        public_infos=(
            SimpleNamespace(info_id=200004, value=None, observed_items=(quality_item,)),
        ),
        action_results=(
            SimpleNamespace(
                action_id=100100,
                result=None,
                observed_items=(outline_item,),
            ),
            SimpleNamespace(
                action_id=100128,
                result=None,
                observed_items=(item_without_value,),
            ),
        ),
        skill_reveals=(),
        inventory_items=(),
    )
    events = SimpleNamespace(
        packets=(),
        frames=(),
        sends=(SimpleNamespace(sort_id=20, kind="bid"),),
        states=(state,),
        statuses=(),
    )
    monkeypatch.setattr(module, "parse_fatbeans_capture", lambda _path: events)

    result = module.summarize_target_missing_event_audit(
        [_stress_source_row(f"{sample.name}#prebid_r1_sort20")],
        sample_root=tmp_path,
    )

    assert result["errors"] == []
    assert result["summary"]["selected_rows"] == 1
    assert result["summary"]["audited_rows"] == 1
    assert result["summary"]["map_counts"] == {"2502": 1}
    assert result["summary"]["missing_component_pattern_counts"] == {
        "q6_cells+total_value+q6_value": 1,
    }
    row = result["rows"][0]
    assert row["missing_components"] == ["q6_cells", "total_value", "q6_value"]
    assert row["key_target_presence"]["session.total_cells"] is True
    assert row["key_target_presence"]["bucket.q6.cells"] is False
    assert row["key_target_presence"]["bucket.q6.value"] is False
    assert row["numeric_target_values"]["session.total_cells"] == 4
    assert row["numeric_target_values"]["session.total_count"] == 1
    assert row["anchor_event_source_id_counts"] == {
        "action_result:100100": 1,
        "action_result:100128": 1,
        "public_info:200004": 1,
    }
    assert row["summary_fields"]["session_total_cells_exact"] == 4
    assert row["summary_fields"]["q6_cells_exact"] is None
    assert row["summary_fields"]["q6_value_exact"] is None
    assert row["constraint_anchor_summary"]["quality_floor_anchors"]["q6_count"] == 1
    assert row["payload_item_summary"]["q6_with_value"] == 0
    assert row["q6_residual_target_candidate"]["cells"]["status"] == (
        "missing_non_q6_exact"
    )
    assert row["q6_residual_target_candidate"]["cells"]["truth"] == 4
