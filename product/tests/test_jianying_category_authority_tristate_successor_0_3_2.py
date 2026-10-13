from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import bidking_lab.inference.v2 as v2_module
from bidking_lab.data.item_category_authority import (
    ItemCategoryAuthorityError,
    PRIMARY_CATEGORY_POLICY,
    SCHEMA_VERSION,
    TAXONOMY,
    authority_identity_payload,
    load_item_category_authority,
    sha256_json,
)
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.v2 import (
    EvidenceStoreBuilder,
    RuntimeEvidence,
    physical_candidate_domains_from_store,
)
from bidking_lab.secure import data_crypt
from bidking_lab.secure.data_crypt import encrypt_compressed_bytes


_AUTHORITY_MEMBER = "data/processed/item_category_authority_0_3_2.json"
_BKENC2_KEY = bytes(range(32))


_REAL_FALSE_EXCLUSIONS = (
    # item, quality, value, width, height, legacy single category, observed cats
    (1916004, 6, 216_000, 2, 2, 107, (101,)),
    (1916001, 6, 519_800, 5, 4, 110, (107,)),
    (1916025, 6, 158_550, 1, 1, 104, (107,)),
    (1916009, 6, 516_500, 2, 3, 104, (103, 104)),
    (1916010, 6, 610_800, 1, 1, 107, (103,)),
)


def _item(
    item_id: int,
    *,
    quality: int,
    value: int,
    width: int,
    height: int,
) -> Item:
    return Item(
        item_id=item_id,
        name=f"item_{item_id}",
        description="",
        name_key=f"item_{item_id}",
        desc_key=f"item_{item_id}_desc",
        quality=quality,
        quality_color="red",
        value=value,
        shape_w=width,
        shape_h=height,
        tags=[],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=[],
    )


def _catalog_row(
    *,
    quality: int,
    value: int,
    width: int,
    height: int,
    yes: tuple[int, ...],
    no: tuple[int, ...] = (),
):
    row_type = getattr(v2_module, "_PhysicalCategoryCatalogRow", None)
    if row_type is None:
        # Exact historical source arm: the old five-tuple treated the one
        # remembered category as complete membership and must fail the new
        # retained-candidate assertions below.
        return (quality, value, width, height, yes)
    return row_type(
        quality=quality,
        value=value,
        width=width,
        height=height,
        category_yes=yes,
        category_no=no,
        category_unknown=tuple(
            sorted(v2_module._PHYSICAL_CATEGORY_IDS - set(yes) - set(no))
        ),
    )


def _domain(
    monkeypatch: pytest.MonkeyPatch,
    *,
    row,
    item: Item,
    required: tuple[int, ...] = (),
    excluded: tuple[int, ...] = (),
):
    monkeypatch.setattr(
        v2_module,
        "_validated_physical_category_catalog",
        lambda: (
            {item.item_id: row},
            {5608: frozenset({item.item_id})},
            "ok",
            "physical-category-v1:tristate-test",
        ),
    )
    builder = EvidenceStoreBuilder()
    builder.add_item(
        RuntimeEvidence(
            runtime_id=item.item_id,
            quality=6,
            shape_key=f"{item.shape_w}{item.shape_h}",
            cells=item.shape_w * item.shape_h,
            categories=required,
            excluded_categories=excluded,
        )
    )
    build = physical_candidate_domains_from_store(
        5608,
        builder.build(),
        items={item.item_id: item},
    )
    assert build.catalog_status == "ok"
    assert len(build.domains) == 1
    return build.domains[0]


@pytest.mark.parametrize(
    (
        "item_id",
        "quality",
        "value",
        "width",
        "height",
        "legacy_category",
        "observed_categories",
    ),
    _REAL_FALSE_EXCLUSIONS,
)
def test_legacy_single_category_unknown_never_excludes_real_item(
    monkeypatch: pytest.MonkeyPatch,
    item_id: int,
    quality: int,
    value: int,
    width: int,
    height: int,
    legacy_category: int,
    observed_categories: tuple[int, ...],
) -> None:
    item = _item(
        item_id,
        quality=quality,
        value=value,
        width=width,
        height=height,
    )
    domain = _domain(
        monkeypatch,
        row=_catalog_row(
            quality=quality,
            value=value,
            width=width,
            height=height,
            yes=(legacy_category,),
        ),
        item=item,
        required=observed_categories,
    )

    assert domain.candidate_item_ids == (item_id,)
    assert domain.hard_eligible is False
    assert domain.exact_item_id is None
    assert "catalog_category_membership_unknown" in domain.sources


def test_complete_authority_can_prove_yes_and_no_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _item(1916009, quality=6, value=516_500, width=2, height=3)
    known = _catalog_row(
        quality=6,
        value=516_500,
        width=2,
        height=3,
        yes=(103, 104),
        no=tuple(sorted(v2_module._PHYSICAL_CATEGORY_IDS - {103, 104})),
    )

    required = _domain(
        monkeypatch,
        row=known,
        item=item,
        required=(103, 104),
        excluded=(107,),
    )
    denied = _domain(
        monkeypatch,
        row=known,
        item=item,
        required=(101,),
    )

    assert required.candidate_item_ids == (item.item_id,)
    assert required.hard_eligible is True
    assert required.exact_item_id == item.item_id
    assert denied.candidate_item_ids == ()
    assert denied.hard_eligible is False


def test_unknown_excluded_category_is_fail_open_but_not_hard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _item(1916004, quality=6, value=216_000, width=2, height=2)
    domain = _domain(
        monkeypatch,
        row=_catalog_row(
            quality=6,
            value=216_000,
            width=2,
            height=2,
            yes=(107,),
        ),
        item=item,
        required=(107,),
        excluded=(101,),
    )

    assert domain.candidate_item_ids == (item.item_id,)
    assert domain.hard_eligible is False
    assert domain.exact_item_id is None


def _authority_document(processed: list[dict]) -> dict:
    item_table_sha = "1" * 64
    records = [
        {
            "catalog_present": True,
            "category_ids": [101, 107],
            "current_random_drop_eligible": True,
            "desc_key": "itemDesc_1916004",
            "item_id": processed[0]["item_id"],
            "legacy_category_present": True,
            "name_key": "itemName_1916004",
            "primary_category": 107,
            "quality": processed[0]["quality"],
            "shape_h": processed[0]["shape_h"],
            "shape_w": processed[0]["shape_w"],
            "taxonomy_tags": [101, 107],
            "value": processed[0]["value"],
        }
    ]
    sources = {
        "item_table": {
            "archive_sha256": "2" * 64,
            "columns": 41,
            "compression": "none",
            "hash_mode": "raw_uncompressed_bytes",
            "rows": 1,
            "sha256": item_table_sha,
        },
        "items_json": {
            "hash_mode": "canonical_json_utf8",
            "overlap_rows": 1,
            "rows": 1,
            "sha256": "3" * 64,
        },
        "legacy_category": {
            "hash_mode": "canonical_json_utf8",
            "overlap_rows": 1,
            "rows": 1,
            "sha256": "4" * 64,
        },
        "items_droppable": {
            "hash_mode": "canonical_json_utf8",
            "overlap_rows": 1,
            "rows": 1,
            "sha256": v2_module._canonical_json_sha256(processed),
        },
    }
    records_sha256 = sha256_json(records)
    identity_payload = {
        "generation": f"item-table-sha256:{item_table_sha}",
        "primary_category_policy": PRIMARY_CATEGORY_POLICY,
        "records_sha256": records_sha256,
        "schema_version": SCHEMA_VERSION,
        "sources": sources,
        "taxonomy": TAXONOMY,
    }
    return {
        "authority_identity": sha256_json(identity_payload),
        "generation": f"item-table-sha256:{item_table_sha}",
        "primary_category_policy": PRIMARY_CATEGORY_POLICY,
        "records": records,
        "records_sha256": records_sha256,
        "schema_version": SCHEMA_VERSION,
        "sources": sources,
        "taxonomy": copy.deepcopy(TAXONOMY),
    }


def _write_authority(root: Path, document: dict) -> None:
    target = root / "data" / "processed" / "item_category_authority_0_3_2.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def _authority_target(root: Path) -> Path:
    return root / "data" / "processed" / "item_category_authority_0_3_2.json"


def _write_bkenc2_authority(
    root: Path,
    document: dict,
    *,
    raw: bytes | None = None,
) -> Path:
    target = _authority_target(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    plain = (
        raw
        if raw is not None
        else json.dumps(document, ensure_ascii=False).encode("utf-8")
    )
    target.write_bytes(
        encrypt_compressed_bytes(
            plain,
            _BKENC2_KEY,
            member_path=_AUTHORITY_MEMBER,
        )
    )
    return target


def _write_catalog_inputs(root: Path, processed: list[dict]) -> None:
    target = root / "data" / "processed"
    target.mkdir(parents=True, exist_ok=True)
    (target / "items_droppable.json").write_text(
        json.dumps(processed, ensure_ascii=False),
        encoding="utf-8",
    )
    (target / "drop_weights_all_maps_0627.json").write_text(
        json.dumps(
            {
                "5608": {
                    "n_items": 1,
                    "items": {str(processed[0]["item_id"]): [1.0]},
                }
            }
        ),
        encoding="utf-8",
    )


def test_complete_authority_loader_binds_generation_and_multicategory(
    tmp_path: Path,
) -> None:
    processed = [
        {
            "item_id": 1916004,
            "quality": 6,
            "value": 216_000,
            "shape_w": 2,
            "shape_h": 2,
        }
    ]
    document = _authority_document(processed)
    _write_authority(tmp_path, document)

    public_authority = load_item_category_authority(
        tmp_path
        / "data"
        / "processed"
        / "item_category_authority_0_3_2.json"
    )
    assert public_authority.identity == document["authority_identity"]
    assert public_authority.records[1916004].category_ids == (101, 107)

    categories, status, identity = v2_module._complete_physical_category_authority(
        tmp_path,
        processed,
    )

    assert status == "ok"
    assert identity == document["authority_identity"]
    assert categories == {1916004: frozenset({101, 107})}


def test_complete_authority_uses_one_duplicate_safe_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    processed = [
        {
            "item_id": 1916004,
            "quality": 6,
            "value": 216_000,
            "shape_w": 2,
            "shape_h": 2,
        }
    ]
    document = _authority_document(processed)
    target = (
        tmp_path
        / "data"
        / "processed"
        / "item_category_authority_0_3_2.json"
    )
    _write_authority(tmp_path, document)
    clean_raw = target.read_bytes()
    marker = f'"generation": "{document["generation"]}"'
    duplicate_raw = clean_raw.replace(
        marker.encode(),
        (
            '"generation": "item-table-sha256:'
            + "0" * 64
            + '", '
            + marker
        ).encode(),
        1,
    )
    assert duplicate_raw != clean_raw

    original_read_bytes = Path.read_bytes
    authority_byte_reads = 0

    def replace_after_first_strict_read(path: Path, *args, **kwargs) -> bytes:
        nonlocal authority_byte_reads
        raw = original_read_bytes(path, *args, **kwargs)
        if path == target:
            authority_byte_reads += 1
            if authority_byte_reads == 1:
                target.write_bytes(duplicate_raw)
        return raw

    monkeypatch.setattr(Path, "read_bytes", replace_after_first_strict_read)

    categories, status, identity = v2_module._complete_physical_category_authority(
        tmp_path,
        processed,
    )

    assert status == "ok"
    assert identity == document["authority_identity"]
    assert categories == {1916004: frozenset({101, 107})}
    assert authority_byte_reads == 1

    categories, status, _identity = (
        v2_module._complete_physical_category_authority(tmp_path, processed)
    )
    assert categories == {}
    assert status == "authority_malformed"


def test_complete_authority_hydrates_bkenc2_from_canonical_package_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    processed = [
        {
            "item_id": 1916004,
            "quality": 6,
            "value": 216_000,
            "shape_w": 2,
            "shape_h": 2,
        }
    ]
    document = _authority_document(processed)
    target = _write_bkenc2_authority(tmp_path, document)
    monkeypatch.setattr(data_crypt, "resolve_key", lambda: _BKENC2_KEY)

    public = load_item_category_authority(target)
    categories, status, identity = v2_module._complete_physical_category_authority(
        tmp_path,
        processed,
    )

    assert public.identity == document["authority_identity"]
    assert public.records[1916004].category_ids == (101, 107)
    assert categories == {1916004: frozenset({101, 107})}
    assert status == "ok"
    assert identity == document["authority_identity"]


@pytest.mark.parametrize("failure", ("wrong_key", "wrong_byte", "wrong_package_path"))
def test_complete_authority_rejects_unauthenticated_bkenc2(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    processed = [
        {
            "item_id": 1916004,
            "quality": 6,
            "value": 216_000,
            "shape_w": 2,
            "shape_h": 2,
        }
    ]
    target = _write_bkenc2_authority(tmp_path, _authority_document(processed))
    monkeypatch.setattr(data_crypt, "resolve_key", lambda: _BKENC2_KEY)
    if failure == "wrong_key":
        monkeypatch.setattr(data_crypt, "resolve_key", lambda: b"x" * 32)
    elif failure == "wrong_byte":
        damaged = bytearray(target.read_bytes())
        damaged[-1] ^= 0x01
        target.write_bytes(bytes(damaged))
    else:
        renamed = target.with_name("authority-copy.json")
        renamed.write_bytes(target.read_bytes())
        target = renamed

    with pytest.raises(ItemCategoryAuthorityError):
        load_item_category_authority(target)


def test_complete_authority_rejects_duplicate_keys_inside_bkenc2(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    processed = [
        {
            "item_id": 1916004,
            "quality": 6,
            "value": 216_000,
            "shape_w": 2,
            "shape_h": 2,
        }
    ]
    document = _authority_document(processed)
    clean = json.dumps(document, ensure_ascii=False)
    marker = f'"generation": "{document["generation"]}"'
    duplicate = clean.replace(
        marker,
        '"generation": "item-table-sha256:' + "0" * 64 + '", ' + marker,
        1,
    ).encode("utf-8")
    target = _write_bkenc2_authority(tmp_path, document, raw=duplicate)
    monkeypatch.setattr(data_crypt, "resolve_key", lambda: _BKENC2_KEY)

    with pytest.raises(ItemCategoryAuthorityError, match="duplicate JSON key"):
        load_item_category_authority(target)


def test_complete_authority_bkenc2_uses_one_captured_byte_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    processed = [
        {
            "item_id": 1916004,
            "quality": 6,
            "value": 216_000,
            "shape_w": 2,
            "shape_h": 2,
        }
    ]
    document = _authority_document(processed)
    target = _write_bkenc2_authority(tmp_path, document)
    clean_envelope = target.read_bytes()
    clean = json.dumps(document, ensure_ascii=False)
    marker = f'"generation": "{document["generation"]}"'
    duplicate = clean.replace(
        marker,
        '"generation": "item-table-sha256:' + "0" * 64 + '", ' + marker,
        1,
    ).encode("utf-8")
    replacement = encrypt_compressed_bytes(
        duplicate,
        _BKENC2_KEY,
        member_path=_AUTHORITY_MEMBER,
    )
    original_read_bytes = Path.read_bytes
    authority_reads = 0

    def replace_after_read(path: Path, *args, **kwargs) -> bytes:
        nonlocal authority_reads
        raw = original_read_bytes(path, *args, **kwargs)
        if path == target:
            authority_reads += 1
            if authority_reads == 1:
                target.write_bytes(replacement)
        return raw

    monkeypatch.setattr(data_crypt, "resolve_key", lambda: _BKENC2_KEY)
    monkeypatch.setattr(Path, "read_bytes", replace_after_read)

    public = load_item_category_authority(target)
    assert public.identity == document["authority_identity"]
    assert authority_reads == 1
    assert clean_envelope != replacement
    with pytest.raises(ItemCategoryAuthorityError, match="duplicate JSON key"):
        load_item_category_authority(target)


@pytest.mark.parametrize("with_authority", (False, True))
def test_missing_legacy_memory_never_becomes_negative_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    with_authority: bool,
) -> None:
    processed = [
        {
            "item_id": 1916004,
            "quality": 6,
            "value": 216_000,
            "shape_w": 2,
            "shape_h": 2,
            "tags": [],
        }
    ]
    _write_catalog_inputs(tmp_path, processed)
    if with_authority:
        _write_authority(tmp_path, _authority_document(processed))
    monkeypatch.setattr(v2_module, "project_root", lambda: tmp_path)
    v2_module._validated_physical_category_catalog.cache_clear()
    try:
        rows, maps, status, generation = (
            v2_module._validated_physical_category_catalog()
        )
    finally:
        v2_module._validated_physical_category_catalog.cache_clear()

    assert status == "ok"
    assert generation.startswith("physical-category-v1:")
    assert maps == {5608: frozenset({1916004})}
    row = rows[1916004]
    if with_authority:
        assert row.category_yes == (101, 107)
        assert 101 not in row.category_unknown
    else:
        assert row.category_yes == ()
        assert 101 in row.category_unknown
        assert row.category_no == ()


@pytest.mark.parametrize(
    "mutation",
    (
        "records_hash",
        "identity",
        "duplicate_category",
        "droppable_generation",
        "wrong_generation_rehashed",
        "taxonomy_rehashed",
        "record_extra_key_rehashed",
        "taxonomy_tags_conflict_rehashed",
        "duplicate_json_key",
    ),
)
def test_complete_authority_loader_rejects_strict_map_a_contract_violations(
    tmp_path: Path,
    mutation: str,
) -> None:
    processed = [
        {
            "item_id": 1916004,
            "quality": 6,
            "value": 216_000,
            "shape_w": 2,
            "shape_h": 2,
        }
    ]
    document = _authority_document(processed)
    if mutation == "records_hash":
        document["records_sha256"] = "0" * 64
    elif mutation == "identity":
        document["authority_identity"] = "0" * 64
    elif mutation == "duplicate_category":
        document["records"][0]["category_ids"] = [101, 101, 107]
    elif mutation == "droppable_generation":
        altered = copy.deepcopy(processed)
        altered[0]["value"] += 1
        processed = altered
    elif mutation == "wrong_generation_rehashed":
        document["generation"] = "item-table-sha256:" + "0" * 64
    elif mutation == "taxonomy_rehashed":
        document["taxonomy"]["100"]["label"] = "tampered"
    elif mutation == "record_extra_key_rehashed":
        document["records"][0]["unexpected"] = True
    elif mutation == "taxonomy_tags_conflict_rehashed":
        document["records"][0]["taxonomy_tags"] = []

    if mutation in {
        "duplicate_category",
        "wrong_generation_rehashed",
        "taxonomy_rehashed",
        "record_extra_key_rehashed",
        "taxonomy_tags_conflict_rehashed",
    }:
        document["records_sha256"] = sha256_json(document["records"])
        document["authority_identity"] = sha256_json(
            authority_identity_payload(document)
        )
    if mutation == "duplicate_json_key":
        raw = json.dumps(document, ensure_ascii=False)
        marker = f'"generation": "{document["generation"]}"'
        duplicated = (
            '"generation": "item-table-sha256:'
            + "0" * 64
            + '", '
            + marker
        )
        target = (
            tmp_path
            / "data"
            / "processed"
            / "item_category_authority_0_3_2.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(raw.replace(marker, duplicated, 1), encoding="utf-8")
    else:
        _write_authority(tmp_path, document)

    categories, status, _identity = (
        v2_module._complete_physical_category_authority(tmp_path, processed)
    )

    assert categories == {}
    assert status != "ok"
