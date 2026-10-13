"""Hotfix2 SAV0: exact-count provenance for footprint average-value evidence."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from bidking_lab.inference.size_avg_evidence import (
    build_size_bucket_evidence,
    size_bucket_counts_from_context,
)
from bidking_lab.inference.v2 import (
    EvidenceStoreBuilder,
    RuntimeEvidence,
    build_residual_problem,
    evidence_store_from_fatbeans_events,
)
from bidking_lab.live.fatbeans import (
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)
from bidking_lab.live.monitor import _states_to_session, load_monitor_tables


ROOT = Path(__file__).resolve().parents[1]
PARTIAL_ETHAN = (
    ROOT
    / "data"
    / "samples"
    / "fatbeans"
    / "fatbeans_valid_ethan_2601_2rounds_2601_1469222797053689_3689.json"
)
FULL_ETHAN = (
    ROOT
    / "data"
    / "samples"
    / "fatbeans"
    / "fatbeans_valid_ethan_2601_5rounds_2601_9000000000000005_0375.json"
)


def _shape_store(
    cells: tuple[int, ...],
    *,
    source: str = "skill:1002081",
) -> object:
    builder = EvidenceStoreBuilder()
    for runtime_id, footprint_cells in enumerate(cells, 1):
        builder.add_item(
            RuntimeEvidence(
                runtime_id=runtime_id,
                local_index=runtime_id - 1,
                shape_key=f"shape-{runtime_id}",
                cells=footprint_cells,
                sources=(source,),
            )
        )
    return builder.build()


def test_partial_layout_without_exact_total_cannot_become_exact() -> None:
    store = _shape_store((1, 1, 2, 4, 4, 6))

    counts, exact = size_bucket_counts_from_context(
        store,
        total_item_count=None,
        layout_footprint_count=6,
        trusted_footprint_count=6,
    )
    evidence = build_size_bucket_evidence(
        ((4, 200_000.0),),
        store=store,
        total_item_count=None,
        layout_footprint_count=6,
        trusted_footprint_count=6,
    )

    assert counts == {1: 2, 2: 1, 4: 2, 6: 1}
    assert exact is False
    assert evidence[0].count_exact is None
    assert evidence[0].count_partial == 2
    assert evidence[0].value_floor is None
    assert evidence[0].strength == "soft"


def test_exact_total_and_trusted_distinct_partition_remains_exact() -> None:
    store = _shape_store((1, 2, 4, 4, 6))

    counts, exact = size_bucket_counts_from_context(
        store,
        total_item_count=5,
        layout_footprint_count=5,
        trusted_footprint_count=5,
    )

    assert counts == {1: 1, 2: 1, 4: 2, 6: 1}
    assert exact is True


def test_untrusted_layout_does_not_use_surface_count_as_coverage() -> None:
    store = _shape_store((1, 2, 4, 4, 6))

    _counts, exact = size_bucket_counts_from_context(
        store,
        total_item_count=5,
        layout_footprint_count=5,
        trusted_footprint_count=4,
    )

    assert exact is False


def test_semantic_full_outline_proves_coverage_without_separate_total() -> None:
    store = _shape_store((1, 2, 4, 6), source="action:100100")

    counts, without_total = size_bucket_counts_from_context(
        store,
        total_item_count=None,
        layout_footprint_count=4,
        trusted_footprint_count=4,
    )
    counts, with_total = size_bucket_counts_from_context(
        store,
        total_item_count=4,
        layout_footprint_count=4,
        trusted_footprint_count=4,
    )

    assert without_total is True
    assert with_total is True
    assert counts == {1: 1, 2: 1, 4: 1, 6: 1}


def test_full_outline_rejects_unpaired_or_conflicting_total() -> None:
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=1,
            local_index=0,
            shape_key="11",
            cells=1,
            sources=("action:100100",),
        )
    )
    builder.add_item(
        RuntimeEvidence(
            runtime_id=2,
            quality=6,
            sources=("skill:100301",),
        )
    )
    store = builder.build()

    _counts, unpaired = size_bucket_counts_from_context(
        store,
        total_item_count=None,
        layout_footprint_count=1,
        trusted_footprint_count=1,
    )
    _counts, mismatched_total = size_bucket_counts_from_context(
        _shape_store((1, 2), source="action:100100"),
        total_item_count=3,
        layout_footprint_count=2,
        trusted_footprint_count=2,
    )

    assert unpaired is False
    assert mismatched_total is False


@pytest.mark.parametrize("invalid_total", [True, 1.9, "1", 0, -1])
def test_full_outline_rejects_non_strict_explicit_total(
    invalid_total: object,
) -> None:
    store = _shape_store((1,), source="action:100100")

    counts, exact = size_bucket_counts_from_context(
        store,
        total_item_count=invalid_total,
        layout_footprint_count=1,
        trusted_footprint_count=1,
    )

    assert counts == {1: 1}
    assert exact is False


@pytest.mark.parametrize("field", ["layout_footprint_count", "trusted_footprint_count"])
@pytest.mark.parametrize("invalid_count", [True, 1.9, "1", 0, -1])
def test_partial_partition_rejects_non_strict_coverage_counts(
    field: str,
    invalid_count: object,
) -> None:
    store = _shape_store((1,))
    kwargs: dict[str, object] = {
        "total_item_count": 1,
        "layout_footprint_count": 1,
        "trusted_footprint_count": 1,
    }
    kwargs[field] = invalid_count

    counts, exact = size_bucket_counts_from_context(store, **kwargs)

    assert counts == {1: 1}
    assert exact is False


def test_full_outline_source_cannot_borrow_a_later_partial_footprint() -> None:
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=7,
            sources=("action:100100",),
        )
    )
    builder.add_item(
        RuntimeEvidence(
            runtime_id=7,
            local_index=0,
            shape_key="22",
            cells=4,
            sources=("skill:1002081",),
        )
    )
    store = builder.build()

    counts, exact = size_bucket_counts_from_context(
        store,
        total_item_count=None,
        layout_footprint_count=1,
        trusted_footprint_count=1,
    )

    assert counts == {4: 1}
    assert exact is False


def test_cross_alias_full_outline_source_cannot_borrow_partial_footprint() -> None:
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=7,
            local_index=0,
            sources=("action:100100",),
        )
    )
    builder.add_item(
        RuntimeEvidence(
            local_index=0,
            shape_key="22",
            cells=4,
            sources=("skill:1002081",),
        )
    )
    store = builder.build()

    counts, exact = size_bucket_counts_from_context(
        store,
        total_item_count=None,
        layout_footprint_count=1,
        trusted_footprint_count=1,
    )

    assert counts == {4: 1}
    assert exact is False


def test_local_alias_cannot_join_multiple_runtime_identities() -> None:
    builder = EvidenceStoreBuilder()
    for runtime_id in (7, 8):
        builder.add_item(
            RuntimeEvidence(
                runtime_id=runtime_id,
                local_index=0,
                shape_key="11",
                cells=1,
                sources=("action:100100",),
            )
        )
    store = builder.build()

    counts, exact = size_bucket_counts_from_context(
        store,
        total_item_count=None,
        layout_footprint_count=2,
        trusted_footprint_count=2,
    )

    assert counts == {}
    assert exact is False
    assert store.footprint_alias_conflicts == {
        "runtime:7": ("runtime:7", "runtime:8", "local:0"),
    }


def test_runtime_identity_can_move_with_one_stable_footprint() -> None:
    builder = EvidenceStoreBuilder()
    for local_index in (0, 25):
        builder.add_item(
            RuntimeEvidence(
                runtime_id=7,
                local_index=local_index,
                shape_key="11",
                cells=1,
                sources=("action:100100",),
            )
        )
    store = builder.build()

    counts, exact = size_bucket_counts_from_context(
        store,
        total_item_count=1,
        layout_footprint_count=1,
        trusted_footprint_count=1,
    )

    assert counts == {1: 1}
    assert exact is True
    assert store.footprint_alias_conflicts == {}


def _runtime_local_alias_conflict_store(*, reverse: bool = False) -> object:
    rows = [
        RuntimeEvidence(
            runtime_id=7,
            local_index=0,
            shape_key="11",
            cells=1,
            sources=("action:100100",),
        ),
        RuntimeEvidence(
            local_index=0,
            shape_key="22",
            cells=4,
            sources=("action:100100",),
        ),
    ]
    if reverse:
        rows.reverse()
    builder = EvidenceStoreBuilder()
    for row in rows:
        builder.add_item(row)
    return builder.build()


@pytest.mark.parametrize("reverse", [False, True])
def test_runtime_local_alias_conflict_cannot_prove_exact_count(
    reverse: bool,
) -> None:
    store = _runtime_local_alias_conflict_store(reverse=reverse)

    counts, exact = size_bucket_counts_from_context(
        store,
        total_item_count=None,
        layout_footprint_count=2,
        trusted_footprint_count=2,
    )
    evidence = build_size_bucket_evidence(
        ((1, 200_000.0), (4, 200_000.0)),
        store=store,
        total_item_count=None,
        layout_footprint_count=2,
        trusted_footprint_count=2,
    )

    assert counts == {}
    assert exact is False
    assert store.footprint_conflicts == {
        "runtime:7": (("11", 1), ("22", 4)),
    }
    for target in evidence:
        assert target.count_exact is None
        assert target.count_partial is None
        assert target.value_floor is None
        assert target.strength == "soft"
        assert "unstable_footprint_identity" in target.count_provenance


def test_real_residual_runtime_local_alias_conflict_has_no_hard_floor() -> None:
    if not PARTIAL_ETHAN.exists():
        pytest.skip(f"local replay absent: {PARTIAL_ETHAN.name}")

    events = parse_fatbeans_capture(PARTIAL_ETHAN)
    batches = live_batches_from_fatbeans_events(events)
    base_session, *_ = _states_to_session(batches)
    assert base_session is not None
    assert base_session.total_item_count is None

    base_store = evidence_store_from_fatbeans_events(events)
    builder = EvidenceStoreBuilder()
    for fact in base_store.facts:
        builder.add_fact(fact)
    for evidence in _runtime_local_alias_conflict_store().items():
        builder.add_item(evidence)
    store = builder.build()

    tables = load_monitor_tables()
    problem = build_residual_problem(
        base_session.map_id,
        store,
        maps=tables.maps,
        drops=tables.drops,
        items=tables.items,
        obs=base_session,
    )
    targets = {target.cells: target for target in problem.size_bucket_evidence}

    assert {1, 4}.issubset(targets)
    for cells in (1, 4):
        assert targets[cells].count_exact is None
        assert targets[cells].count_partial is None
        assert targets[cells].value_floor is None
        assert targets[cells].strength == "soft"
        assert "unstable_footprint_identity" in targets[cells].count_provenance


def test_real_product_conflicting_identity_stays_partial_without_floor() -> None:
    if not PARTIAL_ETHAN.exists():
        pytest.skip(f"local replay absent: {PARTIAL_ETHAN.name}")

    events = parse_fatbeans_capture(PARTIAL_ETHAN)
    batches = live_batches_from_fatbeans_events(events)
    base_session, *_ = _states_to_session(batches)
    assert base_session is not None
    assert base_session.total_item_count is None

    base_store = evidence_store_from_fatbeans_events(events)
    builder = EvidenceStoreBuilder()
    for fact in base_store.facts:
        builder.add_fact(fact)
    builder.add_item(
        RuntimeEvidence(
            runtime_id=7,
            local_index=0,
            shape_key="11",
            cells=1,
            sources=("action:100100",),
        )
    )
    builder.add_item(
        RuntimeEvidence(
            runtime_id=7,
            local_index=0,
            shape_key="22",
            cells=4,
            sources=("skill:1002081",),
        )
    )
    store = builder.build()

    assert store.footprint_conflicts == {
        "runtime:7": (("11", 1), ("22", 4)),
    }

    tables = load_monitor_tables()
    problem = build_residual_problem(
        base_session.map_id,
        store,
        maps=tables.maps,
        drops=tables.drops,
        items=tables.items,
        obs=base_session,
    )
    targets = {target.cells: target for target in problem.size_bucket_evidence}

    assert targets[4].count_exact is None
    assert targets[4].count_partial is None
    assert targets[4].value_floor is None
    assert targets[4].strength == "soft"
    assert "unstable_footprint_identity" in targets[4].count_provenance


def test_real_ethan_partial_outline_stays_partial_and_truth_is_larger() -> None:
    if not PARTIAL_ETHAN.exists():
        pytest.skip(f"local replay absent: {PARTIAL_ETHAN.name}")

    events = parse_fatbeans_capture(PARTIAL_ETHAN)
    batches = live_batches_from_fatbeans_events(events)
    base_session, *_ = _states_to_session(batches)
    assert base_session is not None
    assert base_session.total_item_count is None

    tables = load_monitor_tables()
    store = evidence_store_from_fatbeans_events(events)
    assert not any(
        "inventory" in evidence.sources for evidence in store.items()
    )
    problem = build_residual_problem(
        base_session.map_id,
        store,
        maps=tables.maps,
        drops=tables.drops,
        items=tables.items,
        obs=base_session,
    )
    targets = {target.cells: target for target in problem.size_bucket_evidence}
    assert targets[1].count_exact is None
    assert targets[1].count_partial == 9
    assert targets[4].count_exact is None
    assert targets[4].count_partial == 11
    assert targets[4].value_floor is None
    assert targets[4].strength == "soft"

    latest_inventory = next(
        (
            tuple(state.inventory_items)
            for state in reversed(events.states)
            if state.inventory_items
        ),
        (),
    )
    truth_counts: Counter[int] = Counter()
    for inventory_item in latest_inventory:
        item = tables.items.get(int(inventory_item.item_id))
        if item is not None:
            truth_counts[int(item.shape_w * item.shape_h)] += 1
    assert truth_counts[1] == 25
    assert truth_counts[4] == 13


def test_real_ethan_semantic_full_outline_remains_exact() -> None:
    if not FULL_ETHAN.exists():
        pytest.skip(f"local replay absent: {FULL_ETHAN.name}")

    events = parse_fatbeans_capture(FULL_ETHAN)
    batches = live_batches_from_fatbeans_events(events)
    base_session, *_ = _states_to_session(batches)
    assert base_session is not None
    assert base_session.total_item_count == 43

    tables = load_monitor_tables()
    store = evidence_store_from_fatbeans_events(events)
    assert not any(
        "inventory" in evidence.sources for evidence in store.items()
    )
    problem = build_residual_problem(
        base_session.map_id,
        store,
        maps=tables.maps,
        drops=tables.drops,
        items=tables.items,
        obs=base_session,
    )
    targets = {target.cells: target for target in problem.size_bucket_evidence}

    assert targets[4].count_exact == 12
    assert targets[4].count_partial is None
