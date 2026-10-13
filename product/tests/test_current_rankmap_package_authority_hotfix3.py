from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_live_panel_server as panel  # noqa: E402
import ahmad_ref_engine as engine  # noqa: E402
from ahmad_tk_overlay import AhmadTkOverlay  # noqa: E402
from bidking_lab.extract import current_rankmap_package_contract as contract  # noqa: E402
from bidking_lab.live import current_rankmap_runtime as rankmap_runtime  # noqa: E402
from bidking_lab.simulation.basic_mc import (  # noqa: E402
    RankMapAlgorithmPriorBoundDropPools,
    flatten_pool,
)


def _raw(rows: list[list[str]]) -> bytes:
    return ("\ufeff" + "\r\n".join("\t".join(row) for row in rows)).encode("utf-8")


def _identity(raw: bytes, rows: list[list[str]]) -> contract.CurrentTextAssetIdentity:
    return contract.CurrentTextAssetIdentity(
        rows=len(rows),
        columns=len(rows[0]),
        utf8_size=len(raw),
        utf8_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _synthetic_contract(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    rank_rows = []
    bid_rows = []
    for map_id, tier in ((2101, "101"), (5606, "402")):
        rank = ["0"] * 7
        rank[0] = str(map_id)
        rank[3] = "[[15,17,1]]"
        rank[6] = "[1,1,1000,0.1]"
        rank_rows.append(rank)
        bid = ["0"] * 28
        bid[0] = str(map_id)
        bid[7] = tier
        bid[9] = "[[5606,20]]" if map_id == 5606 else "[]"
        bid[17] = f"[9999,{map_id},25,30]"
        bid_rows.append(bid)
    item_rows = []
    routes = {
        101: [],
        102: [101],
        103: [101, 102, 103, 104, 105, 106, 201, 304, 305, 402, 501, 601],
        104: [101, 102, 103, 104, 105, 106, 201, 304, 305, 501, 601],
        105: [402],
        106: [],
        107: [],
    }
    for item_id in routes:
        row = ["0"] * 42
        row[0] = str(item_id)
        row[6] = "[101]"
        row[7] = "11"
        row[8] = "6"
        row[9] = "100"
        row[39] = json.dumps(routes[item_id], separators=(",", ":"))
        item_rows.append(row)
    map_rows = [["0"] * 24]
    map_rows[0][0] = "402"
    values = {
        "RankMap": _raw(rank_rows),
        "BidMap": _raw(bid_rows),
        "Item": _raw(item_rows),
        "Map": _raw(map_rows),
    }
    monkeypatch.setattr(
        contract,
        "CURRENT_TEXT_ASSET_IDENTITIES",
        {
            name: _identity(values[name], rows)
            for name, rows in (
                ("RankMap", rank_rows),
                ("BidMap", bid_rows),
                ("Item", item_rows),
                ("Map", map_rows),
            )
        },
    )
    monkeypatch.setattr(contract, "ROUTE_STABLE_RANKMAP_MAP_IDS", (2101,))
    monkeypatch.setattr(contract, "CHANGED_ROUTE_RANKMAP_MAP_IDS", (5606,))
    monkeypatch.setattr(contract, "CURRENT_RANKMAP_MAP_IDS", (2101, 5606))
    monkeypatch.setattr(contract, "RETIRED_SUMMER_PARTY_2026_ITEM_IDS", (101,))
    monkeypatch.setattr(contract, "CURRENT_ALL_MAP_Q6_ITEM_IDS", (103,))
    monkeypatch.setattr(contract, "CURRENT_NON_S2_AUTUMN_Q6_ITEM_IDS", (104,))
    monkeypatch.setattr(contract, "CURRENT_S2_SHADOW_PUPPET_Q6_ITEM_IDS", (105,))
    monkeypatch.setattr(contract, "CURRENT_EXCHANGE_OR_PASS_Q6_ITEM_IDS", (106, 107))
    monkeypatch.setattr(
        contract,
        "CURRENT_S2_DIRECT_Q6_CANARIES",
        {103: (100, (1, 1))},
    )
    union_ids = (102, 103, 104, 105)
    monkeypatch.setattr(contract, "CURRENT_MODEL_UNION_ITEM_ROWS", len(union_ids))
    monkeypatch.setattr(
        contract,
        "CURRENT_MODEL_UNION_ITEM_IDS_SHA256",
        contract._ids_sha256(union_ids),  # noqa: SLF001
    )
    monkeypatch.setattr(
        contract,
        "S2_CLOUD_CALIBRATION",
        {
            "sessions": 191,
            "item_count": 191,
            "quality_counts": {"q1": 0, "q3": 0, "q4": 0, "q5": 0, "q6": 191},
            "method": "synthetic_session_effective_n",
            "source_scope": "synthetic",
        },
    )
    scopes = {
        "route_stable_210x": {
            "map_ids": (2101,),
            "map_ids_sha256": contract._ids_sha256((2101,)),  # noqa: SLF001
            "rankmap_rows_sha256": contract._rows_sha256([rank_rows[0]]),  # noqa: SLF001
            "bidmap_rows_sha256": contract._rows_sha256([bid_rows[0]]),  # noqa: SLF001
            "tier": "101",
            "previous_item_rows": 2,
            "previous_item_ids_sha256": contract._ids_sha256((101, 102)),  # noqa: SLF001
            "item_rows": 3,
            "item_ids_sha256": contract._ids_sha256((102, 103, 104)),  # noqa: SLF001
        },
        "changed_route_ranked_560x": {
            "map_ids": (5606,),
            "map_ids_sha256": contract._ids_sha256((5606,)),  # noqa: SLF001
            "rankmap_rows_sha256": contract._rows_sha256([rank_rows[1]]),  # noqa: SLF001
            "bidmap_rows_sha256": contract._rows_sha256([bid_rows[1]]),  # noqa: SLF001
            "tier": "402",
            "previous_item_rows": 2,
            "previous_item_ids_sha256": contract._ids_sha256((101, 102)),  # noqa: SLF001
            "item_rows": 3,
            "item_ids_sha256": contract._ids_sha256((102, 103, 105)),  # noqa: SLF001
        },
    }
    monkeypatch.setattr(contract, "CURRENT_RANKMAP_SCOPE_IDENTITIES", scopes)
    previous_weights_value = {
        str(map_id): {
            "count": 16.0,
            "n_items": 2,
            "items": {"101": [0.5, 100, 100], "102": [0.5, 100, 100]},
        }
        for map_id in (2101, 5606)
    }
    priors_value = {
        str(map_id): {
            "quality_probs": {"q1": 0.0, "q3": 0.0, "q4": 0.0, "q5": 0.0, "q6": 1.0},
            "quality_values": {"q1": 1, "q3": 2, "q4": 3, "q5": 4, "q6": 5},
        }
        for map_id in (2101, 5606)
    }
    weights = json.dumps(previous_weights_value, separators=(",", ":")).encode()
    priors = json.dumps(priors_value, separators=(",", ":")).encode()
    monkeypatch.setattr(
        contract,
        "PREVIOUS_CURRENT_RANKMAP_DISTRIBUTION_INPUTS",
        {
            "weights": {"bytes": len(weights), "sha256": hashlib.sha256(weights).hexdigest()},
            "priors": {"bytes": len(priors), "sha256": hashlib.sha256(priors).hexdigest()},
        },
    )
    probabilities = [0.333333333333, 0.333333333333, 0.333333333334]
    expected_weights_value = {
        "2101": {
            "count": 16.0,
            "n_items": 3,
            "items": {
                str(item_id): [probability, 100, 100]
                for item_id, probability in zip((102, 103, 104), probabilities)
            },
        },
        "5606": {
            "count": 16.0,
            "n_items": 3,
            "items": {
                str(item_id): [probability, 100, 100]
                for item_id, probability in zip((102, 103, 105), probabilities)
            },
        },
    }
    expected_weights = json.dumps(expected_weights_value, separators=(",", ":")).encode()
    current_all_weights = json.dumps(
        {"2101": expected_weights_value["2101"]},
        separators=(",", ":"),
    ).encode()
    current_all_priors = json.dumps(
        {"2101": priors_value["2101"]},
        separators=(",", ":"),
    ).encode()
    monkeypatch.setattr(
        contract,
        "CURRENT_ALL_MAP_DISTRIBUTION_OUTPUTS",
        {
            "weights": {
                "bytes": len(current_all_weights),
                "sha256": hashlib.sha256(current_all_weights).hexdigest(),
            },
            "priors": {
                "bytes": len(current_all_priors),
                "sha256": hashlib.sha256(current_all_priors).hexdigest(),
            },
        },
    )
    monkeypatch.setattr(
        contract,
        "CURRENT_RANKMAP_DISTRIBUTION_OUTPUTS",
        {
            "weights": {"bytes": len(expected_weights), "maps": 2, "sha256": hashlib.sha256(expected_weights).hexdigest()},
            "priors": {"bytes": len(priors), "maps": 2, "sha256": hashlib.sha256(priors).hexdigest()},
        },
    )
    values["weights"] = weights
    values["priors"] = priors
    values["expected_weights"] = expected_weights
    values["items_droppable"] = json.dumps(
        [
            {
                "item_id": item_id,
                "quality": 6,
                "value": 100,
                "shape_w": 1,
                "shape_h": 1,
                "tags": [101],
            }
            for item_id in union_ids
        ],
        separators=(",", ":"),
    ).encode()
    values["current_all_weights"] = current_all_weights
    values["current_all_priors"] = current_all_priors
    return values


def _appmanifest(
    *, updated: str, build_id: str = contract.CURRENT_STEAM_BUILD_ID
) -> bytes:
    return (
        '"AppState"\n{\n'
        '\t"appid"\t\t"4128580"\n'
        '\t"Universe"\t\t"1"\n'
        '\t"installdir"\t\t"BidKing"\n'
        f'\t"LastUpdated"\t\t"{updated}"\n'
        f'\t"buildid"\t\t"{build_id}"\n'
        "}\n"
    ).encode()


def test_appmanifest_uses_stable_build_identity_not_mutable_timestamp() -> None:
    first = contract.validate_current_appmanifest(_appmanifest(updated="1"))
    second = contract.validate_current_appmanifest(_appmanifest(updated="2"))
    assert first["build_id"] == second["build_id"] == "25096377"
    assert first["sha256"] != second["sha256"]
    with pytest.raises(contract.Update606PackageInputError, match="buildid mismatch"):
        contract.validate_current_appmanifest(
            _appmanifest(updated="2", build_id="25094234")
        )


def _current_activity_indexed() -> dict[str, dict[int, list[str]]]:
    activity_open = [""] * 13
    activity_open[12] = json.dumps(list(range(2531, 2541)), separators=(",", ":"))
    activity_sealed = [""] * 13
    activity_sealed[12] = json.dumps(list(range(4531, 4541)), separators=(",", ":"))
    bid_rows: dict[int, list[str]] = {}
    for category, map_ids in (
        ("105", tuple(range(2541, 2551))),
        ("305", tuple(range(4541, 4551))),
    ):
        expected_root = [
            [map_id, 100 if index == 0 else 20]
            for index, map_id in enumerate(map_ids)
        ]
        for index, map_id in enumerate(map_ids):
            row = [""] * 21
            row[0] = str(map_id)
            row[7] = category
            row[9] = json.dumps(
                expected_root if index == 0 else [[]], separators=(",", ":")
            )
            row[17] = json.dumps(
                [9999, map_id if category == "105" else map_id - 2000, 22, 44],
                separators=(",", ":"),
            )
            row[18] = "4"
            row[20] = "[103,0,0,0,0]"
            bid_rows[map_id] = row
    auction = [""] * 21
    auction[0] = "6001"
    auction[7] = "601"
    auction[9] = "[[]]"
    auction[17] = "[9999,2601,44,44]"
    auction[18] = "20"
    auction[20] = "[103,103,103,104,105]"
    bid_rows[6001] = auction
    return {
        "ActivityMap": {10003: activity_open, 10103: activity_sealed},
        "BidMap": bid_rows,
        "RankMap": {6001: ["6001"]},
    }


def test_current_activity_and_6001_static_canaries_are_semantic_not_open_claims() -> None:
    indexed = _current_activity_indexed()

    activity = contract._validate_current_red_crate_activity_blocks(indexed)
    auction = contract._validate_auction_hall_6001_static_structure(indexed)

    assert activity["open_map_ids"] == list(range(2541, 2551))
    assert activity["sealed_map_ids"] == list(range(4541, 4551))
    assert activity["runtime_open_claim"] is False
    assert activity["activity_map_routes"] == {
        "open": list(range(2531, 2541)),
        "sealed": list(range(4531, 4541)),
    }
    assert activity["round_skill_group_policy"] == (
        "identity_only_not_distribution_input"
    )
    assert auction["drop_pool_id"] == 2601
    assert auction["round_skill_group_ids"] == [103, 103, 103, 104, 105]
    assert auction["lifecycle"] == "dormant_history_no_current_entry"
    assert auction["distribution_authority"] is False
    assert auction["quote_available"] is False


@pytest.mark.parametrize(
    ("table", "map_id", "column", "value", "match"),
    (
        ("ActivityMap", 10003, 12, "[2541]", "root binding"),
        ("BidMap", 2541, 9, "[[2541,100]]", "root binding"),
        ("BidMap", 2542, 17, "[9999,9999,22,44]", "row binding"),
        ("BidMap", 4542, 17, "[9999,2542,21,44]", "row binding"),
        ("BidMap", 4550, 18, "true", "row binding"),
        ("BidMap", 2541, 20, "[103,true,0,0,0]", "row binding"),
        ("BidMap", 6001, 17, "[9999,2602,22,44]", "6001 structure"),
        ("BidMap", 6001, 20, "[103,103,103,104,106]", "6001 structure"),
    ),
)
def test_current_activity_and_6001_static_canaries_fail_closed(
    table: str,
    map_id: int,
    column: int,
    value: str,
    match: str,
) -> None:
    indexed = copy.deepcopy(_current_activity_indexed())
    indexed[table][map_id][column] = value

    with pytest.raises(contract.Update606PackageInputError, match=match):
        contract._validate_current_red_crate_activity_blocks(indexed)
        contract._validate_auction_hall_6001_static_structure(indexed)


def test_current_rankmap_rebind_keeps_scopes_separate_and_never_claims_dropmap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _synthetic_contract(monkeypatch)

    weights, priors, receipt = contract.build_current_rankmap_distribution(
        rankmap_text_asset=values["RankMap"],
        bidmap_text_asset=values["BidMap"],
        item_text_asset=values["Item"],
        map_text_asset=values["Map"],
        calibrated_weights=values["weights"],
        calibrated_priors=values["priors"],
        current_all_map_weights=values["current_all_weights"],
        current_all_map_priors=values["current_all_priors"],
        items_droppable=values["items_droppable"],
    )

    assert weights == values["expected_weights"]
    assert priors == values["priors"]
    assert receipt["authority_mode"] == "rankmap_drop_model_algorithm_prior"
    assert receipt["drop_or_dropmap_textasset"] is False
    assert receipt["claims_item_level_drop_graph"] is False
    assert set(receipt["scopes"]) == {
        "route_stable_210x",
        "changed_route_ranked_560x",
    }
    assert receipt["runtime_open_claim"] is True
    assert receipt["ranked_5606"] == {
        "map_id": 5606,
        "status": "modeled_empirically_calibrated",
        "runtime_open_claim": True,
    }
    assert receipt["recognized_unmodeled_scopes"] == {
        "large_auction_6001": {
            "map_id": 6001,
            "static_table_present": False,
            "lifecycle": "dormant_history_no_current_entry",
            "distribution_authority": False,
            "quote_available": False,
        }
    }
    assert receipt["chenmei"] == {"map_id": 5506, "status": "pending_isolated"}
    assert receipt["raven_chaotic_space"] == (
        contract.raven_chaotic_space_authority_receipt()
    )


def test_current_rankmap_rebind_rejects_rankmap_item_and_generation_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _synthetic_contract(monkeypatch)
    rankmap = bytearray(values["RankMap"])
    rankmap[-1] ^= 1
    with pytest.raises(contract.Update606PackageInputError, match="RankMap.*identity"):
        contract.build_current_rankmap_distribution(
            rankmap_text_asset=bytes(rankmap),
            bidmap_text_asset=values["BidMap"],
            item_text_asset=values["Item"],
            map_text_asset=values["Map"],
            calibrated_weights=values["weights"],
            calibrated_priors=values["priors"],
            current_all_map_weights=values["current_all_weights"],
            current_all_map_priors=values["current_all_priors"],
            items_droppable=values["items_droppable"],
        )

    wrong_weights = json.loads(values["weights"])
    wrong_weights["5606"]["items"]["101"][1] = 999
    wrong_raw = json.dumps(wrong_weights, separators=(",", ":")).encode()
    monkeypatch.setattr(
        contract,
        "PREVIOUS_CURRENT_RANKMAP_DISTRIBUTION_INPUTS",
        {
            "weights": {
                "bytes": len(wrong_raw),
                "sha256": hashlib.sha256(wrong_raw).hexdigest(),
            },
            "priors": {
                "bytes": len(values["priors"]),
                "sha256": hashlib.sha256(values["priors"]).hexdigest(),
            },
        },
    )
    with pytest.raises(contract.Update606PackageInputError, match="previous/current Item"):
        contract.build_current_rankmap_distribution(
            rankmap_text_asset=values["RankMap"],
            bidmap_text_asset=values["BidMap"],
            item_text_asset=values["Item"],
            map_text_asset=values["Map"],
            calibrated_weights=wrong_raw,
            calibrated_priors=values["priors"],
            current_all_map_weights=values["current_all_weights"],
            current_all_map_priors=values["current_all_priors"],
            items_droppable=values["items_droppable"],
        )


def _runtime_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[str, bytes]]:
    package = tmp_path / "package"
    weights = json.dumps(
        {
            "5606": {
                "count": 16.0,
                "n_items": 1,
                "items": {"101": [1.0, 100, 100]},
            }
        },
        separators=(",", ":"),
    ).encode()
    priors = json.dumps({"5606": {}}, separators=(",", ":")).encode()
    authority = json.dumps(
        {
            "schema": "bidking_current_rankmap_distribution_authority_v2",
            "package_generation": contract.CURRENT_PACKAGE_GENERATION,
            "authority_mode": contract.CURRENT_RANKMAP_AUTHORITY_MODE,
            "drop_or_dropmap_textasset": False,
            "drop_or_dropmap_absence_is_not_a_blocker": True,
            "claims_item_level_drop_graph": False,
            "modeled_item_probabilities": True,
            "official_distribution_authority": False,
            "model_distribution_authority": True,
            "runtime_open_claim": True,
            "runtime_open_scope": "s2_ranked_5601_through_5611",
            "ranked_5606": {
                "map_id": 5606,
                "status": "modeled_empirically_calibrated",
                "runtime_open_claim": True,
            },
            "recognized_unmodeled_scopes": {
                "large_auction_6001": {
                    "map_id": 6001,
                    "static_table_present": True,
                    "lifecycle": "dormant_history_no_current_entry",
                    "distribution_authority": False,
                    "quote_available": False,
                }
            },
            "chenmei": {"map_id": 5506, "status": "pending_isolated"},
            "empirical_calibration": {
                "sessions": 191,
                "settled_items": 7_126,
                "decision": "retain_previous_quality_prior",
            },
            "raven_chaotic_space": dict(
                rankmap_runtime.RAVEN_CHAOTIC_SPACE_AUTHORITY_RECEIPT
            ),
        },
        separators=(",", ":"),
    ).encode()
    payloads = {
        "data/raw/tables/Item.txt": b"item",
        "data/raw/tables/BidMap.txt": b"bidmap",
        "data/raw/tables/RankMap.txt": b"rankmap",
        "data/processed/rankmap_drop_weights_current.json": weights,
        "data/processed/rankmap_drop_priors_current.json": priors,
        "data/processed/rankmap_distribution_authority_current.json": authority,
    }
    specs = {
        relative: {
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for relative, payload in payloads.items()
    }
    monkeypatch.setattr(rankmap_runtime, "CURRENT_RANKMAP_MAP_IDS", (5606,))
    monkeypatch.setattr(rankmap_runtime, "_PACKAGE_FILES", specs)
    monkeypatch.setattr(rankmap_runtime, "PACKAGE_BASELINE_ID", "test-rankmap")
    for relative, payload in payloads.items():
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    fingerprint = package / "scripts" / "package_data_fingerprints.json"
    fingerprint.parent.mkdir(parents=True)
    fingerprint.write_text(
        json.dumps(
            {
                "baseline_id": "test-rankmap",
                "files": {
                    relative: {"size": spec["bytes"], "sha256": spec["sha256"]}
                    for relative, spec in specs.items()
                },
            }
        ),
        encoding="utf-8",
    )
    return package, payloads


def test_runtime_binds_algorithm_prior_to_same_current_pool_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package, _payloads = _runtime_package(tmp_path, monkeypatch)
    maps = {5606: SimpleNamespace(drop_pool_id=2706)}
    items = {101: SimpleNamespace(value=100)}
    original_drops: dict[int, object] = {}

    fingerprint_path = package / "scripts/package_data_fingerprints.json"
    fingerprint = fingerprint_path.read_bytes()
    fingerprint_path.unlink()
    missing, missing_status, missing_receipt = (
        rankmap_runtime.bind_current_rankmap_algorithm_prior(
            package_root=package,
            maps=maps,
            drops=original_drops,
            items=items,
        )
    )
    assert missing is original_drops
    assert missing_status == "authority_manifest_unavailable"
    assert missing_receipt == {}
    fingerprint_path.write_bytes(fingerprint)

    bound, status, receipt = rankmap_runtime.bind_current_rankmap_algorithm_prior(
        package_root=package,
        maps=maps,
        drops=original_drops,
        items=items,
    )
    assert status == "ok"
    assert isinstance(bound, RankMapAlgorithmPriorBoundDropPools)
    assert bound.bound_pool_ids == {2706}
    assert receipt["drop_or_dropmap_textasset"] is False
    assert receipt["claims_item_level_drop_graph"] is False
    assert receipt["raven_chaotic_space"]["quote_available"] is False
    assert "map_pool_ids" not in receipt
    flattened = flatten_pool(2706, bound, items)
    assert flattened.item_ids == [101]
    assert flattened.probabilities == [1.0]

    weights_path = package / "data/processed/rankmap_drop_weights_current.json"
    wrong = bytearray(weights_path.read_bytes())
    wrong[-1] ^= 1
    weights_path.write_bytes(wrong)
    rejected, rejected_status, rejected_receipt = (
        rankmap_runtime.bind_current_rankmap_algorithm_prior(
            package_root=package,
            maps=maps,
            drops=original_drops,
            items=items,
        )
    )
    assert rejected is original_drops
    assert rejected_status == "authority_input_hash_mismatch"
    assert rejected_receipt == {}

def _snapshot(map_id: object) -> dict[str, object]:
    return {
        "hero": "ahmed",
        "map_id": map_id,
        "round": 2,
        "phase": "bidding",
        "structured_ref_inputs": {"total_count": 34},
        "public_info_rows": [],
        "ui_contract": {
            "context": {"hero": "ahmed", "map_id": map_id, "round": 2, "phase": "bidding"}
        },
    }


def _static() -> dict[str, object]:
    return {
        "drop_weights": {"104": [80, 180, 340, 240, 100, 50]},
        "nest_prices": {},
        "map_nests": {},
        "drop_table_priors": {
            5606: {
                "quality_probs": {"q1": 0.30, "q3": 0.24, "q4": 0.25, "q5": 0.11, "q6": 0.10},
                "quality_values": {"q1": 1000, "q3": 5000, "q4": 15000, "q5": 50000, "q6": 300000},
            }
        },
    }


def test_current_rankmap_gate_is_green_only_for_exact_authority_and_exact_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine,
        "_current_rankmap_distribution_generation_status",
        lambda: ("ok", engine.CURRENT_RANKMAP_PACKAGE_GENERATION),
    )
    assert engine._current_rankmap_authority_scope_state(_snapshot(5606)) is None
    assert engine._update606_changed_route_scope_state(_snapshot(5606)) is None
    assert engine._current_rankmap_authority_scope_state(_snapshot("5606.0")) == (
        "map_conflict",
        5606,
    )
    assert engine._current_rankmap_authority_scope_state(_snapshot(5506)) is None

    monkeypatch.setattr(
        engine,
        "_current_rankmap_distribution_generation_status",
        lambda: ("authority_input_hash_mismatch", ""),
    )
    assert engine._current_rankmap_authority_scope_state(_snapshot(5606)) == (
        "authority_input_hash_mismatch",
        5606,
    )
    assert engine._update606_changed_route_scope_state(_snapshot(5606)) == (
        "no_approved_distribution",
        5606,
    )


def test_current_rankmap_engine_panel_and_tk_publish_real_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine,
        "_current_rankmap_distribution_generation_status",
        lambda: ("ok", engine.CURRENT_RANKMAP_PACKAGE_GENERATION),
    )
    monkeypatch.setattr(engine, "load_reference_static_data", lambda: _static())
    snapshot = _snapshot(5606)
    result = engine.run_reference_engine(snapshot, static_data=_static(), max_combos=8000)
    assert result.status == "count_prior"
    assert result.conservative and result.balanced and result.aggressive
    assert result.conservative < result.balanced < result.aggressive
    assert "drop_table_prob:5606" in result.notes

    snapshot["rankmap_distribution_support"] = {
        "status": "current_rankmap_algorithm_prior"
    }
    summary = panel.summarize_snapshot(snapshot, snapshot_path=Path("rankmap-5606.json"))
    assert summary["reference"]["readiness"] != "no_reachable_combo"
    assert all(summary["reference"][key] != "-" for key in ("conservative", "balanced", "aggressive"))
    rank_flag = next(
        flag for flag in summary["flags"] if flag["label"] == "本局静态排位模型"
    )
    assert "不代表排位当前开放" in rank_flag["detail"]
    overlay = object.__new__(AhmadTkOverlay)
    tk_summary = overlay._manual_result_summary(snapshot)  # noqa: SLF001
    assert tk_summary["reference"]["readiness"] != "no_reachable_combo"
    assert all(tk_summary["reference"][key] != "-" for key in ("conservative", "balanced", "aggressive"))


def test_missing_current_rankmap_authority_stays_no_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine,
        "_current_rankmap_distribution_generation_status",
        lambda: ("authority_input_unavailable", ""),
    )
    result = engine.run_reference_engine(_snapshot(5606), static_data=_static(), max_combos=8000)
    assert result.status == "no_reachable_combo"
    assert result.balanced is None
    assert result.notes == (
        f"{engine.CURRENT_RANKMAP_AUTHORITY_NOTE_PREFIX}:"
        f"{engine.CURRENT_RANKMAP_PACKAGE_GENERATION}:5606:"
        "authority_input_unavailable",
    )
