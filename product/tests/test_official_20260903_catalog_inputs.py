from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

import pytest

from bidking_lab.extract.bid_map_table import parse_bid_map_table
from bidking_lab.extract.hero_table import parse_hero_table
from bidking_lab.extract.item_table import parse_item_table
from bidking_lab.extract.tables import iter_table_rows, load_table_rows
from bidking_lab.live import monitor
from bidking_lab.live.catalog_value import summarize_catalog_values


ROOT = Path(__file__).resolve().parents[1]


def _builder() -> ModuleType:
    path = ROOT / "scripts" / "build_official_20260903_catalog_inputs.py"
    spec = importlib.util.spec_from_file_location(
        "build_official_20260903_catalog_inputs",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _decoded_rows(name: str) -> list[list[str]]:
    path = ROOT / "data" / "raw" / "tables" / f"{name}.txt"
    return list(iter_table_rows(base64.b64decode(path.read_bytes(), validate=True).decode("utf-8")))


def _by_id(path: Path, key: str) -> dict[int, dict[str, object]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value.__class__ is list
    return {int(row[key]): row for row in value}


def test_checked_in_catalog_inputs_match_exact_68171c_profile() -> None:
    builder = _builder()
    assert builder.STEAM_BUILD_ID == "25096377"
    assert builder.PACKAGE_GENERATION == "68171c-2026-09-03-907"
    parsers = {
        "Item": parse_item_table,
        "BidMap": parse_bid_map_table,
        "Hero": parse_hero_table,
    }
    expected_rows = {"Item": 1_427, "BidMap": 246, "Hero": 22}
    for name, identity in builder.TABLE_IDENTITIES.items():
        path = ROOT / "data" / "raw" / "tables" / f"{name}.txt"
        package = path.read_bytes()
        assert (len(package), hashlib.sha256(package).hexdigest()) == (
            identity.package_bytes,
            identity.package_sha256,
        )
        decoded = base64.b64decode(package, validate=True)
        assert (len(decoded), hashlib.sha256(decoded).hexdigest()) == (
            identity.canonical_bytes,
            identity.canonical_sha256,
        )
        rows = list(iter_table_rows(decoded.decode("utf-8")))
        assert (len(rows), {len(row) for row in rows}) == (
            identity.rows,
            {identity.columns},
        )
        assert len(parsers[name](rows)) == expected_rows[name]

    for relative, identity in builder.PROCESSED_IDENTITIES.items():
        payload = (ROOT / relative).read_bytes()
        assert {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()} == identity


def test_processed_items_preserve_only_approved_shared_display_names() -> None:
    builder = _builder()
    official = parse_item_table(_decoded_rows("Item"))
    approved_raw, _source = builder._load_approved_display_catalog(None)  # noqa: SLF001
    approved = {
        int(row["item_id"]): row
        for row in json.loads(approved_raw.decode("utf-8"))
    }
    processed = _by_id(ROOT / "data" / "processed" / "items.json", "item_id")

    assert len(official) == len(processed) == 1_427
    assert len(approved) == 1_336
    assert set(approved) <= set(processed)
    assert len(set(processed) - set(approved)) == 91
    for item_id, old in approved.items():
        current = processed[item_id]
        assert current["name"] == old["name"]
        for field in builder.SHARED_SEMANTIC_FIELDS:
            assert current[field] == getattr(official[item_id], field)
    for item_id in set(processed) - set(approved):
        expected = official[item_id].model_dump(exclude={"raw_row"})
        assert processed[item_id] == expected


def test_processed_maps_and_heroes_are_exact_parser_projections() -> None:
    maps = parse_bid_map_table(_decoded_rows("BidMap"))
    heroes = parse_hero_table(_decoded_rows("Hero"))
    maps_json = _by_id(ROOT / "data" / "processed" / "maps.json", "map_id")
    heroes_json = _by_id(ROOT / "data" / "processed" / "heroes.json", "hero_id")

    expected_maps = json.loads(
        json.dumps(
            [
                row.model_dump(exclude={"raw_row"})
                for _map_id, row in sorted(maps.items())
            ],
            ensure_ascii=False,
        )
    )
    assert maps_json == {int(row["map_id"]): row for row in expected_maps}
    assert heroes_json == {
        hero_id: row.model_dump(exclude={"raw_row"})
        for hero_id, row in sorted(heroes.items())
    }
    assert set(range(5_203, 5_220)) <= set(maps_json)
    assert set(range(5_601, 5_612)) <= set(maps_json)
    assert heroes_json[303]["name"] == "印第安纳"


def test_real_s2_unknown_item_gap_is_closed_without_faking_unknown_ids() -> None:
    builder = _builder()
    tables = monitor.load_monitor_tables()
    inventory = tuple(
        SimpleNamespace(
            item_id=item_id,
            runtime_id=90_000 + index,
            quality=6,
            cells=shape[0] * shape[1],
        )
        for index, (item_id, (_name, _value, shape)) in enumerate(
            builder.SETTLEMENT_CANARIES.items()
        )
    )
    events = SimpleNamespace(
        states=(SimpleNamespace(message_id=0x002D, inventory_items=inventory),)
    )

    assert monitor._inventory_value(events, tables.items) == 3_177_600  # noqa: SLF001
    breakdown = monitor._inventory_quality_breakdown(  # noqa: SLF001
        events,
        tables.items,
    )
    assert breakdown["final_q6_count"] == 8
    assert breakdown["final_q6_value"] == 3_177_600
    summary = summarize_catalog_values(inventory, tables.items)
    assert summary.known_value_sum == 3_177_600
    assert summary.catalog_value_complete is True
    assert summary.unknown_item_ids == ()
    assert summary.valued_item_count == summary.settled_item_count == 8

    unknown_id = 9_999_999
    assert unknown_id not in tables.items
    with_unknown = inventory + (
        SimpleNamespace(
            item_id=unknown_id,
            runtime_id=99_999,
            quality=6,
            cells=1,
        ),
    )
    incomplete = summarize_catalog_values(with_unknown, tables.items)
    assert incomplete.known_value_sum == 3_177_600
    assert incomplete.catalog_value_complete is False
    assert incomplete.unknown_item_ids == (unknown_id,)
    assert incomplete.valued_item_count == 8
    assert incomplete.settled_item_count == 9


def test_catalog_value_summary_has_no_empty_inventory_truth() -> None:
    summary = summarize_catalog_values((), {})
    assert summary.known_value_sum is None
    assert summary.catalog_value_complete is None
    assert summary.unknown_item_ids == ()
    assert summary.settled_item_count == summary.valued_item_count == 0


def test_builder_requires_byte_identical_extraction_passes(tmp_path: Path) -> None:
    builder = _builder()
    pass1 = tmp_path / "pass1.txt"
    pass2 = tmp_path / "pass2.txt"
    raw = b"\xef\xbb\xbf1\ta\r\n2\tb"
    canonical = b"1\ta\n2\tb"
    package = base64.b64encode(canonical)
    identity = builder.TableIdentity(
        raw_bytes=len(raw),
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        rows=2,
        columns=2,
        canonical_bytes=len(canonical),
        canonical_sha256=hashlib.sha256(canonical).hexdigest(),
        package_bytes=len(package),
        package_sha256=hashlib.sha256(package).hexdigest(),
    )
    pass1.write_bytes(raw)
    pass2.write_bytes(raw)
    rows, built_canonical, built_package, receipt = builder._canonicalize_pair(  # noqa: SLF001
        "Synthetic",
        pass1,
        pass2,
        identity=identity,
    )
    assert rows == [["1", "a"], ["2", "b"]]
    assert built_canonical == canonical
    assert built_package == package
    assert receipt["passes_byte_identical"] is True

    pass2.write_bytes(raw.replace(b"2\tb", b"2\tc"))
    with pytest.raises(builder.OfficialCatalogBuildError, match="passes differ"):
        builder._canonicalize_pair(  # noqa: SLF001
            "Synthetic",
            pass1,
            pass2,
            identity=identity,
        )


def test_package_loader_uses_the_same_exact_item_rows() -> None:
    direct = parse_item_table(_decoded_rows("Item"))
    loaded = parse_item_table(load_table_rows(ROOT / "data" / "raw" / "tables" / "Item.txt"))
    assert set(direct) == set(loaded)
    for item_id in (1_006_010, 1_006_018, 1_916_039, 1_916_054):
        assert loaded[item_id] == direct[item_id]
