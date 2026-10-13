from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALIBRATION = ROOT / "scripts" / "calibration"
AHMAD_SRC = ROOT / "hero_ref" / "src"
for path in (CALIBRATION, AHMAD_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_ref_engine as engine  # noqa: E402
import aisha_calib as ac  # noqa: E402

CANONICAL_IDS = frozenset({1016007, 1036006, 1036007, 1036008})
LOCALIZATION_ALIASES = frozenset({1046010, 1046011, 1046012})


def _refresh_engine_catalogs() -> None:
    engine._validated_current_calculation_items.cache_clear()
    engine._exact_item_values_by_table_quality.cache_clear()
    engine._catalog_cell_sizes_by_table_quality.cache_clear()
    engine._catalog_value_bounds_by_quality.cache_clear()
    engine._catalog_items_by_value.cache_clear()


@pytest.mark.parametrize("raw", [None, "0", "true", "01"])
def test_retired_items_are_lookup_only_not_normalization_reference(
    monkeypatch: pytest.MonkeyPatch,
    raw: str | None,
) -> None:
    if raw is None:
        monkeypatch.delenv(ac.RETIRED_WORLD_CUP_Q6_ENABLE_ENV, raising=False)
    else:
        monkeypatch.setenv(ac.RETIRED_WORLD_CUP_Q6_ENABLE_ENV, raw)

    catalog = ac._catalog()
    assert CANONICAL_IDS <= catalog.keys()
    assert CANONICAL_IDS.isdisjoint(ac._REFERENCE_ITEM_IDS)
    assert LOCALIZATION_ALIASES.isdisjoint(catalog)
    assert LOCALIZATION_ALIASES.isdisjoint(ac._REFERENCE_ITEM_IDS)


def test_exact_retired_switch_adds_only_canonical_reference_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ac.RETIRED_WORLD_CUP_Q6_ENABLE_ENV, "1")
    catalog = ac._catalog()

    assert CANONICAL_IDS <= ac._REFERENCE_ITEM_IDS
    assert LOCALIZATION_ALIASES.isdisjoint(catalog)
    assert LOCALIZATION_ALIASES.isdisjoint(ac._REFERENCE_ITEM_IDS)


@pytest.mark.skipif(
    not (ROOT / "data" / "raw" / "tables" / "Item.txt").is_file(),
    reason="exact package-shaped Item input is unavailable",
)
@pytest.mark.parametrize("raw", [None, "0", "true", "01"])
def test_ahmad_generic_calculation_catalog_excludes_retired_by_default(
    monkeypatch: pytest.MonkeyPatch,
    raw: str | None,
) -> None:
    if raw is None:
        monkeypatch.delenv(engine.RETIRED_WORLD_CUP_Q6_ENABLE_ENV, raising=False)
    else:
        monkeypatch.setenv(engine.RETIRED_WORLD_CUP_Q6_ENABLE_ENV, raw)
    _refresh_engine_catalogs()

    items = engine._current_calculation_items()
    item_ids = {int(item.item_id) for item in items}
    assert CANONICAL_IDS.isdisjoint(item_ids)
    assert LOCALIZATION_ALIASES.isdisjoint(item_ids)
    by_value = engine._catalog_items_by_value()
    candidate_ids = {
        int(item_id)
        for rows in by_value.values()
        for _quality, _cells, item_id in rows
    }
    assert CANONICAL_IDS.isdisjoint(candidate_ids)
    assert LOCALIZATION_ALIASES.isdisjoint(candidate_ids)


@pytest.mark.skipif(
    not (ROOT / "data" / "raw" / "tables" / "Item.txt").is_file(),
    reason="exact package-shaped Item input is unavailable",
)
def test_ahmad_exact_switch_adds_only_canonical_retired_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(engine.RETIRED_WORLD_CUP_Q6_ENABLE_ENV, "1")
    _refresh_engine_catalogs()

    item_ids = {int(item.item_id) for item in engine._current_calculation_items()}
    assert CANONICAL_IDS <= item_ids
    assert LOCALIZATION_ALIASES.isdisjoint(item_ids)
