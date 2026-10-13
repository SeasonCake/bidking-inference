import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_bidmap_raw_capacity_candidates.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_bidmap_raw_capacity_candidates",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _tables() -> SimpleNamespace:
    raw_row = [""] * 23
    raw_row[7] = "105"
    raw_row[11] = "25"
    raw_row[14] = "[4,4,4,4,4]"
    raw_row[17] = "[9999,2601,1,2]"
    raw_row[20] = "[103,0,0,0,0]"
    return SimpleNamespace(
        maps={
            2601: SimpleNamespace(
                map_id=2601,
                raw_row=raw_row,
            )
        }
    )


def test_bidmap_raw_capacity_candidates_ignore_drop_ref_sentinel(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    sample = tmp_path / "sample.json"
    sample.write_text("[]", encoding="utf-8")

    def fake_audit_file(path, *, tables, drop_universe_cache):
        return {
            "file": Path(path).name,
            "status": "ok",
            "map_id": 2601,
            "non_temp_inventory_count": 6,
            "unique_non_temp_item_id_count": 5,
            "non_temp_inventory_cells": 22,
            "unique_non_temp_inventory_cells": 20,
            "unique_q6_non_temp_cells": 8,
            "drop_ref_excess_after_temp_zodiac_count": 4,
            "unique_drop_ref_excess_after_temp_zodiac_count": 3,
            "round_cap_excess_after_temp_zodiac_count": 2,
            "unique_round_cap_excess_after_temp_zodiac_count": 1,
            "non_zodiac_missing_from_drop_universe_count": 0,
            "missing_from_drop_universe_count": 0,
        }

    monkeypatch.setattr(module.scp, "_resolve_paths", lambda paths: (sample,))
    monkeypatch.setattr(module.scp, "_audit_file", fake_audit_file)

    result = module.summarize_bidmap_raw_capacity_candidates(
        [tmp_path],
        tables=_tables(),
        top=4,
    )

    columns = {row["column"]: row for row in result["capacity_columns"]}
    assert columns[17]["candidate_value_counts"] == {"2": 1}
    assert columns[17]["coverage"]["unique_non_temp_item_id_count"]["over_rows"] == 1
    assert columns[17]["coverage"]["unique_non_temp_item_id_count"]["over_examples"] == [
        {
            "file": "sample.json",
            "map_id": 2601,
            "candidate": 2,
            "target": 5,
            "excess": 3,
            "unique_residual_mode": "unique_round_cap_overflow_after_temp",
        }
    ]
    assert columns[14]["candidate_value_counts"] == {"4": 1}
    assert columns[14]["coverage"]["unique_non_temp_item_id_count"]["over_rows"] == 1
    assert columns[11]["coverage"]["unique_non_temp_item_id_count"]["covered_rows"] == 1

    non_capacity_columns = {
        row["column"]: row for row in result["count_sized_non_capacity_columns"]
    }
    assert non_capacity_columns[7]["capacity_candidate"] is False
    assert non_capacity_columns[7]["candidate_value_counts"] == {"105": 1}
