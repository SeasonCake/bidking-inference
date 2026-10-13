from types import SimpleNamespace

from bidking_lab.inference.v3 import (
    action_result_spec,
    audit_fatbeans_events,
    compile_hard_constraints,
    events_from_fatbeans,
    public_info_semantic,
    public_info_spec,
    skill_reveal_spec,
)


def test_public_info_registry_models_exact_numeric_inputs() -> None:
    total_cells = public_info_spec(200009)
    q6_count = public_info_spec(200020)

    assert total_cells.semantic == "total_cells"
    assert total_cells.constraint == "hard"
    assert total_cells.targets == ("session.total_cells",)
    assert total_cells.affects_formal is True

    assert q6_count.semantic == "q6_item_count"
    assert q6_count.constraint == "hard"
    assert q6_count.targets == ("bucket.q6.count",)


def test_public_info_semantic_keeps_v2_legacy_shape() -> None:
    assert public_info_semantic(200031) == {
        "semantic": "random_3_avg_value",
        "model_use": "diagnostic_random_avg_signal",
        "constraint": "diagnostic",
        "reference": "known",
    }
    assert public_info_semantic(999999)["constraint"] == "unknown"
    assert public_info_semantic(999999)["reference"] == "missing"


def test_action_and_skill_registry_cover_observed_live_ids() -> None:
    assert action_result_spec(100136).semantic == "random_4_quality_reveal"
    assert action_result_spec(100136).constraint == "partial"
    assert action_result_spec(100172).semantic == "size_4_avg_value"
    assert action_result_spec(100172).constraint == "soft"
    assert skill_reveal_spec(1001031).semantic == "aisha_q4_outline"
    assert skill_reveal_spec(1002085).semantic == "ethan_full_outline"
    assert skill_reveal_spec(100204).semantic == "ahmed_total_item_count"
    assert skill_reveal_spec(1002041).constraint == "soft"
    assert skill_reveal_spec(1002044).targets == ("bucket.q1.count",)


def test_low_quality_action_registry_distinguishes_green_from_white_green() -> None:
    assert action_result_spec(100104).targets == ("bucket.q1.cells",)
    assert action_result_spec(100116).targets == ("bucket.q2.count",)
    assert action_result_spec(100122).targets == ("bucket.q2.value",)
    assert action_result_spec(100110).targets == ("bucket_group.q1q2.avg_cells",)


def test_fatbeans_events_extract_canonical_evidence_and_coverage_gaps() -> None:
    item = SimpleNamespace(
        item_id=1063002,
        shape_code=12,
        local_index=2,
        quality=3,
    )
    state = SimpleNamespace(
        sort_id=42,
        session_id="2401:abc",
        round_index=2,
        map_id=2401,
        public_infos=(
            SimpleNamespace(
                info_id=200009,
                value=98,
                value_field=14,
                observed_items=(),
            ),
            SimpleNamespace(
                info_id=999999,
                value=1,
                value_field=7,
                observed_items=(),
            ),
        ),
        action_results=(
            SimpleNamespace(
                action_id=100128,
                result=1,
                result_field=14,
                observed_items=(item,),
            ),
        ),
        skill_reveals=(
            SimpleNamespace(
                skill_id=1001031,
                hero_id=103,
                round_index=2,
                observed_items=(item,),
            ),
        ),
        inventory_items=(),
    )
    events = SimpleNamespace(states=(state,))

    canonical = events_from_fatbeans(events)
    assert [event.source_kind for event in canonical] == [
        "public_info",
        "public_info",
        "action_result",
        "skill_reveal",
    ]
    assert canonical[0].semantic == "total_cells"
    assert canonical[2].payload["observed_item_count"] == 1
    assert canonical[2].payload["items"][0]["shape_key"] == "12"
    assert canonical[2].payload["items"][0]["cells"] == 2
    assert canonical[3].hero_id == 103

    report = audit_fatbeans_events(events, file_name="sample.json")
    assert report.events == 4
    assert report.unknown == {"public_info:999999": 1}
    assert report.pending == {}
    assert report.coverage_ok is False
    assert report.parse_ok is True
    assert report.ok is False


def test_compile_hard_constraints_records_exact_numeric_and_anchors() -> None:
    state = SimpleNamespace(
        sort_id=10,
        session_id="2401:abc",
        round_index=1,
        map_id=2401,
        public_infos=(
            SimpleNamespace(
                info_id=200009,
                value=98,
                value_field=14,
                observed_items=(),
            ),
        ),
        action_results=(
            SimpleNamespace(
                action_id=100129,
                result=2,
                result_field=14,
                observed_items=(
                    SimpleNamespace(
                        item_id=1021004,
                        shape_code=21,
                        local_index=38,
                        quality=1,
                    ),
                ),
            ),
        ),
        skill_reveals=(),
        inventory_items=(),
    )
    constraints = compile_hard_constraints(
        events_from_fatbeans(SimpleNamespace(states=(state,)))
    )

    assert constraints.feasible is True
    assert constraints.numeric["session.total_cells"].value == 98
    assert len(constraints.item_anchor_events) == 1
    assert len(constraints.shape_anchor_events) == 1
    anchor = next(iter(constraints.item_anchors.values()))
    assert anchor.key == "local:38:21"
    assert anchor.item_id == 1021004
    assert anchor.quality == 1
    assert anchor.shape_key == "21"
    assert anchor.cells == 2
    footprint = next(iter(constraints.shape_anchors.values()))
    assert footprint.key == "local:38:21"
    assert footprint.shape_key == "21"
    assert footprint.cells == 2


def test_compile_hard_constraints_records_soft_numeric_latest_value() -> None:
    states = (
        SimpleNamespace(
            sort_id=10,
            session_id="2401:abc",
            round_index=1,
            map_id=2401,
            public_infos=(),
            action_results=(
                SimpleNamespace(
                    action_id=100113,
                    result=8.5,
                    result_field=14,
                    observed_items=(),
                ),
            ),
            skill_reveals=(),
            inventory_items=(),
        ),
        SimpleNamespace(
            sort_id=20,
            session_id="2401:abc",
            round_index=2,
            map_id=2401,
            public_infos=(),
            action_results=(
                SimpleNamespace(
                    action_id=100113,
                    result=9.0,
                    result_field=14,
                    observed_items=(),
                ),
            ),
            skill_reveals=(),
            inventory_items=(),
        ),
    )

    constraints = compile_hard_constraints(
        events_from_fatbeans(SimpleNamespace(states=states))
    )

    assert constraints.numeric == {}
    assert len(constraints.soft_numeric) == 1
    soft = next(iter(constraints.soft_numeric.values()))
    assert soft.semantic == "q5_avg_cells"
    assert soft.targets == ("bucket.q5.cells", "bucket.q5.count")
    assert soft.value == 9.0
    assert soft.sort_id == 20


def test_ahmed_numeric_skill_reveals_compile_to_constraints() -> None:
    state = SimpleNamespace(
        sort_id=30,
        session_id="2401:ahmed",
        round_index=2,
        map_id=2401,
        public_infos=(),
        action_results=(),
        skill_reveals=(
            SimpleNamespace(
                skill_id=100204,
                hero_id=204,
                round_index=1,
                result=42,
                result_field=14,
                observed_items=(),
            ),
            SimpleNamespace(
                skill_id=1002041,
                hero_id=204,
                round_index=2,
                result=8.5,
                result_field=11,
                observed_items=(),
            ),
            SimpleNamespace(
                skill_id=1002044,
                hero_id=204,
                round_index=5,
                result=7,
                result_field=14,
                observed_items=(),
            ),
        ),
        inventory_items=(),
    )

    constraints = compile_hard_constraints(
        events_from_fatbeans(SimpleNamespace(states=(state,)))
    )

    assert constraints.numeric["session.total_count"].value == 42
    assert constraints.numeric["bucket.q1.count"].value == 7
    soft = next(iter(constraints.soft_numeric.values()))
    assert soft.semantic == "ahmed_q5_avg_cells"
    assert soft.targets == ("bucket.q5.cells", "bucket.q5.count")
    assert soft.value == 8.5


def test_compile_hard_constraints_derives_outline_cells_from_items() -> None:
    state = SimpleNamespace(
        sort_id=12,
        session_id="2401:abc",
        round_index=1,
        map_id=2401,
        public_infos=(
            SimpleNamespace(
                info_id=200001,
                value=99,
                value_field=14,
                observed_items=(
                    SimpleNamespace(
                        runtime_id=1,
                        local_index=1,
                        item_id=None,
                        quality=4,
                        value=None,
                        shape_code=21,
                        cells=None,
                    ),
                    SimpleNamespace(
                        runtime_id=2,
                        local_index=4,
                        item_id=None,
                        quality=4,
                        value=None,
                        shape_code=22,
                        cells=None,
                    ),
                ),
            ),
        ),
        action_results=(),
        skill_reveals=(),
        inventory_items=(),
    )

    constraints = compile_hard_constraints(
        events_from_fatbeans(SimpleNamespace(states=(state,)))
    )

    assert constraints.numeric["bucket.q4.count"].value == 2
    assert constraints.numeric["bucket.q4.cells"].value == 6


def test_compile_hard_constraints_keeps_quality_only_out_of_footprints() -> None:
    state = SimpleNamespace(
        sort_id=20,
        session_id="2401:abc",
        round_index=2,
        map_id=2401,
        public_infos=(),
        action_results=(
            SimpleNamespace(
                action_id=100136,
                result=None,
                result_field=None,
                observed_items=(
                    SimpleNamespace(
                        runtime_id=123,
                        local_index=14,
                        item_id=None,
                        quality=6,
                        value=None,
                        shape_code=None,
                        cells=None,
                    ),
                ),
            ),
        ),
        skill_reveals=(),
        inventory_items=(),
    )
    constraints = compile_hard_constraints(
        events_from_fatbeans(SimpleNamespace(states=(state,)))
    )

    assert constraints.item_anchors == {}
    assert constraints.shape_anchors == {}
    assert constraints.quality_floor_anchors["runtime:123"].quality == 6
    assert constraints.quality_floor_anchors["runtime:123"].local_index == 14


def test_compile_hard_constraints_preserves_category_anchor() -> None:
    state = SimpleNamespace(
        sort_id=30,
        session_id="2401:abc",
        round_index=3,
        map_id=2401,
        public_infos=(),
        action_results=(
            SimpleNamespace(
                action_id=100151,
                result=None,
                result_field=None,
                observed_items=(
                    SimpleNamespace(
                        runtime_id=456,
                        local_index=7,
                        item_id=None,
                        quality=4,
                        value=None,
                        shape_code=22,
                        cells=None,
                    ),
                ),
            ),
        ),
        skill_reveals=(),
        inventory_items=(),
    )
    constraints = compile_hard_constraints(
        events_from_fatbeans(SimpleNamespace(states=(state,)))
    )

    anchor = constraints.item_anchors["runtime:456"]
    assert anchor.categories == (101,)
    assert anchor.quality == 4
    assert anchor.shape_key == "22"
    assert constraints.shape_anchors["runtime:456"].cells == 4


def test_compile_hard_constraints_reports_exact_conflicts() -> None:
    states = (
        SimpleNamespace(
            sort_id=10,
            session_id="2401:abc",
            round_index=1,
            map_id=2401,
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
        ),
        SimpleNamespace(
            sort_id=20,
            session_id="2401:abc",
            round_index=2,
            map_id=2401,
            public_infos=(
                SimpleNamespace(
                    info_id=200009,
                    value=99,
                    value_field=14,
                    observed_items=(),
                ),
            ),
            action_results=(),
            skill_reveals=(),
            inventory_items=(),
        ),
    )
    constraints = compile_hard_constraints(
        events_from_fatbeans(SimpleNamespace(states=states))
    )

    assert constraints.feasible is False
    assert len(constraints.conflicts) == 1
    conflict = constraints.conflicts[0]
    assert conflict.target == "session.total_cells"
    assert conflict.first.value == 98
    assert conflict.second.value == 99
