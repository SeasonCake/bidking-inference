from __future__ import annotations

from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import BucketTruth, SessionTruth
from bidking_lab.inference.size_avg_evidence import (
    build_size_bucket_evidence,
    parse_size_bucket_diagnostics,
    size_avg_readings_from_action_rows,
    size_bucket_eval_fields,
    size_bucket_evidence_diagnostics,
    size_bucket_evidence_score,
    warehouse_size_band,
)
from bidking_lab.inference.v2 import (
    EvidenceFact,
    EvidenceStoreBuilder,
    LayoutFeasibility,
    ResidualProblem,
    RuntimeEvidence,
)


def _item(item_id: int, *, quality: int, value: int, shape: tuple[int, int]) -> Item:
    w, h = shape
    return Item(
        item_id=item_id,
        name=f"item_{item_id}",
        description="",
        name_key=f"item_{item_id}",
        desc_key=f"item_{item_id}_desc",
        quality=quality,
        quality_color="",
        value=value,
        shape_w=w,
        shape_h=h,
        tags=[],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )


def test_warehouse_size_band() -> None:
    assert warehouse_size_band(80) == "small"
    assert warehouse_size_band(110) == "mid"
    assert warehouse_size_band(140) == "large"
    assert warehouse_size_band(None) is None


def test_full_outline_enables_count_exact_and_hard_floor() -> None:
    builder = EvidenceStoreBuilder()
    plane = _item(1086004, quality=6, value=1_688_400, shape=(2, 2))
    cheap = _item(990_001, quality=4, value=10_000, shape=(2, 2))
    for runtime_id, item in enumerate((plane, cheap)):
        builder.add_item(
            RuntimeEvidence(
                runtime_id=runtime_id,
                item_id=item.item_id,
                quality=item.quality,
                cells=4,
                shape_key="22",
                sources=("action:100100",),
            )
        )
    builder.add_fact(
        EvidenceFact(
            kind="action",
            key="100172",
            value=844_200.0,
            source="action:100172",
            strength="soft",
        )
    )
    store = builder.build()
    evidence = build_size_bucket_evidence(
        ((4, 844_200.0),),
        store=store,
        warehouse_total_cells=80,
        total_item_count=2,
        layout_footprint_count=2,
        trusted_footprint_count=2,
    )
    assert len(evidence) == 1
    target = evidence[0]
    assert target.count_exact == 2
    assert target.anchor_hint == "plane_box"
    assert target.strength == "hard_floor"
    assert target.value_floor is not None

    matching = SessionTruth(
        map_id=2401,
        map_name="t",
        warehouse_total_cells=8,
        buckets={
            6: BucketTruth(
                quality=6,
                count=1,
                total_cells=4,
                value_sum=1_688_400,
                items=[plane],
            ),
            4: BucketTruth(
                quality=4,
                count=1,
                total_cells=4,
                value_sum=10_000,
                items=[cheap],
            ),
        },
    )
    under = SessionTruth(
        map_id=2401,
        map_name="t",
        warehouse_total_cells=8,
        buckets={
            4: BucketTruth(
                quality=4,
                count=2,
                total_cells=8,
                value_sum=20_000,
                items=[cheap, cheap],
            ),
        },
    )
    problem = ResidualProblem(
        map_id=2401,
        map_name="t",
        anchors=(),
        known_item_count=0,
        known_cells=0,
        known_value=0,
        anchor_item_counts={},
        bucket_targets={},
        category_targets=(),
        shape_targets=(),
        layout=LayoutFeasibility(
            footprint_count=2,
            trusted_footprint_count=2,
            occupied_cells=8,
            item_cells=8,
            overlap_cells=0,
            overflow_count=0,
            bottom_row=None,
            bounding_cells=8,
            score=1.0,
        ),
        size_bucket_evidence=evidence,
    )
    assert size_bucket_evidence_score(matching, problem) == 1.0
    assert size_bucket_evidence_score(under, problem) == 0.0


def test_low_avg_stays_soft_without_floor() -> None:
    builder = EvidenceStoreBuilder()
    builder.add_fact(
        EvidenceFact(
            kind="action",
            key="100172",
            value=12_000.0,
            source="action:100172",
            strength="soft",
        )
    )
    evidence = build_size_bucket_evidence(
        ((4, 12_000.0),),
        store=builder.build(),
        warehouse_total_cells=120,
    )
    assert evidence[0].tier == "filler"
    assert evidence[0].strength == "soft"
    assert evidence[0].value_floor is None


def test_parse_size_bucket_diagnostics_and_eval_fields() -> None:
    diag = (
        "size_bucket:4:avg=844200:tier=plane_yongle_singleton:strength=hard_floor:"
        "wh=small:count_exact=2:anchor=plane_box:value_floor=1435140"
    )
    parsed = parse_size_bucket_diagnostics(diag)
    assert len(parsed) == 1
    assert parsed[0]["cells"] == 4
    assert parsed[0]["avg_value"] == 844200.0
    assert parsed[0]["tier"] == "plane_yongle_singleton"
    assert parsed[0]["count_exact"] == 2

    readings = size_avg_readings_from_action_rows(
        [
            {
                "action_id": 100172,
                "tool": "四格均价",
                "result": 120000,
                "sort": 10,
            }
        ]
    )
    assert readings[0]["footprint_cells"] == 4
    fields = size_bucket_eval_fields(
        posterior_diagnostics=diag,
        action_result_rows=[
            {
                "action_id": 100172,
                "tool": "四格均价",
                "result": 120000,
            }
        ],
    )
    assert fields["size_bucket_active"] is True
    assert fields["action_100172_used"] is True
    assert fields["size_bucket_4cell_avg"] == 844200.0
    assert fields["size_bucket_reading_4cell_avg"] == 120000.0

    evidence = build_size_bucket_evidence(((4, 12_000.0),), store=EvidenceStoreBuilder().build())
    assert any("size_bucket:4" in line for line in size_bucket_evidence_diagnostics(evidence))
