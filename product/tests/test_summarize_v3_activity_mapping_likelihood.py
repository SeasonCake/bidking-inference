import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_activity_mapping_likelihood.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_activity_mapping_likelihood",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _item(item_id: int, quality: int) -> SimpleNamespace:
    return SimpleNamespace(item_id=item_id, quality=quality)


def _encoded_table(rows: list[list[str]]) -> str:
    text = "\n".join("\t".join(row) for row in rows)
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_activity_mapping_likelihood_prefers_higher_quality_prior(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    sample = tmp_path / "sample.json"
    sample.write_text("[]", encoding="utf-8")
    inventory = (_item(1001, 6), _item(1002, 6), _item(1003, 5))
    state = SimpleNamespace(map_id=2521, inventory_items=inventory)
    tables = SimpleNamespace(
        maps={2511: SimpleNamespace(drop_pool_id=2111), 2501: SimpleNamespace(drop_pool_id=2101)},
        drops={},
        items={item.item_id: item for item in inventory},
    )

    def fake_sampler(map_id, **_kwargs):
        if map_id == 2511:
            pool = SimpleNamespace(
                items=np.asarray([_item(1, 6), _item(2, 5)], dtype=object),
                probabilities=np.asarray([0.8, 0.2], dtype=float),
                qualities=np.asarray([6, 5], dtype=int),
            )
        else:
            pool = SimpleNamespace(
                items=np.asarray([_item(3, 6), _item(4, 5)], dtype=object),
                probabilities=np.asarray([0.1, 0.9], dtype=float),
                qualities=np.asarray([6, 5], dtype=int),
            )
        return SimpleNamespace(pools=(pool,), pool_weights=(1.0,))

    monkeypatch.setattr(module, "parse_fatbeans_capture", lambda _path: SimpleNamespace(states=(state,)))
    monkeypatch.setattr(module, "prepare_session_sampler", fake_sampler)

    result = module.summarize_activity_mapping_likelihood(
        [sample],
        tables=tables,
        schemes=("minus10:-10", "minus20:-20"),
    )

    assert result["files"] == 1
    assert result["winner_counts"] == {"minus10": 1}
    assert result["scheme_results"][0]["scheme"] == "minus10"
    assert result["scheme_results"][0]["winner_rows"] == 1
    assert result["map_results"][0]["winner_counts"] == {"minus10": 1}


def test_activity_mapping_likelihood_marks_missing_candidate(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    sample = tmp_path / "sample.json"
    sample.write_text("[]", encoding="utf-8")
    state = SimpleNamespace(map_id=2529, inventory_items=(_item(1001, 4),))
    tables = SimpleNamespace(maps={}, drops={}, items={1001: _item(1001, 4)})

    monkeypatch.setattr(module, "parse_fatbeans_capture", lambda _path: SimpleNamespace(states=(state,)))

    result = module.summarize_activity_mapping_likelihood(
        [sample],
        tables=tables,
        schemes=("minus10:-10",),
    )

    assert result["files"] == 1
    assert result["winner_counts"] == {"none": 1}
    assert result["candidate_status_counts"] == {"missing_bidmap": 1}
    assert result["file_results"][0]["candidates"][0]["candidate_map_id"] == 2519


def test_activity_mapping_likelihood_tracks_exact_item_weight_winner(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    sample = tmp_path / "sample.json"
    sample.write_text("[]", encoding="utf-8")
    inventory = (_item(1001, 6), _item(1001, 6), _item(1002, 6))
    state = SimpleNamespace(map_id=2521, inventory_items=inventory)
    tables = SimpleNamespace(
        maps={
            2511: SimpleNamespace(drop_pool_id=2111),
            2501: SimpleNamespace(drop_pool_id=2101),
        },
        drops={},
        items={item.item_id: item for item in inventory},
    )

    def fake_sampler(map_id, **_kwargs):
        if map_id == 2511:
            probs = np.asarray([0.9, 0.1], dtype=float)
        else:
            probs = np.asarray([0.1, 0.9], dtype=float)
        pool = SimpleNamespace(
            items=np.asarray([_item(1001, 6), _item(1002, 6)], dtype=object),
            probabilities=probs,
            qualities=np.asarray([6, 6], dtype=int),
        )
        return SimpleNamespace(pools=(pool,), pool_weights=(1.0,))

    monkeypatch.setattr(
        module,
        "parse_fatbeans_capture",
        lambda _path: SimpleNamespace(states=(state,)),
    )
    monkeypatch.setattr(module, "prepare_session_sampler", fake_sampler)

    result = module.summarize_activity_mapping_likelihood(
        [sample],
        tables=tables,
        schemes=("minus10:-10", "minus20:-20"),
    )

    assert result["winner_counts"] == {"minus10": 1}
    assert result["item_winner_counts"] == {"minus10": 1}
    assert result["scheme_results"][0]["item_winner_rows"] == 1
    assert result["file_results"][0]["best_item_scheme"] == "minus10"


def test_activity_mapping_likelihood_attaches_rankmap_profiles(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    sample = tmp_path / "sample.json"
    sample.write_text("[]", encoding="utf-8")
    inventory = (_item(1001, 6), _item(1002, 5))
    state = SimpleNamespace(map_id=2521, inventory_items=inventory)
    tables = SimpleNamespace(
        maps={2511: SimpleNamespace(drop_pool_id=2111)},
        drops={},
        items={item.item_id: item for item in inventory},
    )
    rankmap_root = tmp_path / "tables"
    rankmap_root.mkdir()
    (rankmap_root / "RankMap.txt").write_text(
        _encoded_table(
            [
                [
                    "2521",
                    "activity",
                    "白色DOWN红色UP",
                    "[[11,15,1000]]",
                    "[[101,50],[106,500]]",
                    "[[50001,100000,50]]",
                    "[10,1,100,1]",
                ],
            ]
        ),
        encoding="utf-8",
    )

    def fake_sampler(_map_id, **_kwargs):
        pool = SimpleNamespace(
            items=np.asarray([_item(1001, 6), _item(1002, 5)], dtype=object),
            probabilities=np.asarray([0.5, 0.5], dtype=float),
            qualities=np.asarray([6, 5], dtype=int),
        )
        return SimpleNamespace(pools=(pool,), pool_weights=(1.0,))

    monkeypatch.setattr(
        module,
        "parse_fatbeans_capture",
        lambda _path: SimpleNamespace(states=(state,)),
    )
    monkeypatch.setattr(module, "prepare_session_sampler", fake_sampler)

    result = module.summarize_activity_mapping_likelihood(
        [sample],
        tables=tables,
        schemes=("minus10:-10",),
        tables_root=rankmap_root,
    )

    rankmap = result["activity_rankmap"]
    assert rankmap["rankmap_present_ids"] == [2521]
    assert rankmap["rankmap_missing_ids"] == []
    assert rankmap["label_counts"] == {"白色DOWN红色UP": 1}
    assert rankmap["category_weight_profile_counts"] == {
        "[[101,50],[106,500]]": 1,
    }
    assert result["file_results"][0]["rankmap_profile"]["label"] == "白色DOWN红色UP"
    assert result["map_results"][0]["rankmap_labels"] == {"白色DOWN红色UP": 1}
