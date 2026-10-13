from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from bidking_lab.data.item_category_authority import (
    load_item_category_authority_document,
)
from bidking_lab.extract import current_rankmap_package_contract as contract
from bidking_lab.extract.bid_map_table import load_bid_map_table
from bidking_lab.extract.item_table import load_item_table
from bidking_lab.live import current_rankmap_runtime as runtime
from bidking_lab.simulation.basic_mc import (
    RankMapAlgorithmPriorBoundDropPools,
    flatten_pool,
)


ROOT = Path(__file__).resolve().parents[1]
ALL_WEIGHTS = ROOT / "data/processed/drop_weights_all_maps_0627.json"
ALL_PRIORS = ROOT / "data/processed/drop_table_priors_0627.json"
WEIGHTS = ROOT / "data/processed/rankmap_drop_weights_current.json"
PRIORS = ROOT / "data/processed/rankmap_drop_priors_current.json"
AUTHORITY = ROOT / "data/processed/rankmap_distribution_authority_current.json"
DROPPABLE = ROOT / "data/processed/items_droppable.json"
CATEGORY_AUTHORITY = ROOT / "data/processed/item_category_authority_0_3_2.json"
RED_LONGTAIL = ROOT / "data/processed/red_longtail_normalized_0627.json"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _ids_sha256(values: set[int]) -> str:
    return _sha256(
        json.dumps(tuple(sorted(values)), separators=(",", ":")).encode("ascii")
    )


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_68171c_support_lifecycle_and_quality_buckets_are_exact() -> None:
    weights = _load(WEIGHTS)
    priors = _load(PRIORS)
    authority = _load(AUTHORITY)
    items = load_item_table(ROOT / "data/raw/tables/Item.txt")

    assert isinstance(weights, dict) and isinstance(priors, dict)
    assert set(weights) == set(priors) == {
        str(map_id) for map_id in contract.CURRENT_RANKMAP_MAP_IDS
    }
    assert authority["schema"] == "bidking_current_rankmap_distribution_authority_v2"
    assert authority["package_generation"] == "68171c-2026-09-03-907"
    assert authority["model_distribution_authority"] is True
    assert authority["official_distribution_authority"] is False
    assert authority["drop_or_dropmap_absence_is_not_a_blocker"] is True

    summer = set(contract.RETIRED_SUMMER_PARTY_2026_ITEM_IDS)
    all_map = set(contract.CURRENT_ALL_MAP_Q6_ITEM_IDS)
    autumn = set(contract.CURRENT_NON_S2_AUTUMN_Q6_ITEM_IDS)
    s2_parts = set(contract.CURRENT_S2_SHADOW_PUPPET_Q6_ITEM_IDS)
    excluded = set(contract.CURRENT_EXCHANGE_OR_PASS_Q6_ITEM_IDS)
    for map_id in contract.CURRENT_RANKMAP_MAP_IDS:
        entry = weights[str(map_id)]
        support = {int(item_id) for item_id in entry["items"]}
        scope = (
            contract.CURRENT_RANKMAP_SCOPE_IDENTITIES["route_stable_210x"]
            if map_id in contract.ROUTE_STABLE_RANKMAP_MAP_IDS
            else contract.CURRENT_RANKMAP_SCOPE_IDENTITIES["changed_route_ranked_560x"]
        )
        assert len(support) == entry["n_items"] == scope["item_rows"]
        assert _ids_sha256(support) == scope["item_ids_sha256"]
        assert all_map <= support
        assert not (summer & support)
        assert not (excluded & support)
        if map_id in contract.ROUTE_STABLE_RANKMAP_MAP_IDS:
            assert autumn <= support
            assert not (s2_parts & support)
        else:
            assert s2_parts <= support
            assert not (autumn & support)

        expected_quality = priors[str(map_id)]["quality_probs"]
        observed_quality = {key: 0.0 for key in expected_quality}
        for raw_item_id, triple in entry["items"].items():
            item_id = int(raw_item_id)
            item = items[item_id]
            quality = "q1" if item.quality in (1, 2) else f"q{item.quality}"
            observed_quality[quality] += float(triple[0])
            assert triple[1] == item.value
            assert isinstance(triple[2], int) and triple[2] > 0
        assert sum(observed_quality.values()) == pytest.approx(1.0, abs=2e-11)
        for quality, expected in expected_quality.items():
            assert observed_quality[quality] == pytest.approx(expected, abs=2e-11)


def test_all_101_maps_use_current_702_support_and_match_210x_overlap() -> None:
    all_weights_raw = ALL_WEIGHTS.read_bytes()
    all_priors_raw = ALL_PRIORS.read_bytes()
    assert (len(all_weights_raw), _sha256(all_weights_raw)) == (
        contract.CURRENT_ALL_MAP_DISTRIBUTION_OUTPUTS["weights"]["bytes"],
        contract.CURRENT_ALL_MAP_DISTRIBUTION_OUTPUTS["weights"]["sha256"],
    )
    assert (len(all_priors_raw), _sha256(all_priors_raw)) == (
        contract.CURRENT_ALL_MAP_DISTRIBUTION_OUTPUTS["priors"]["bytes"],
        contract.CURRENT_ALL_MAP_DISTRIBUTION_OUTPUTS["priors"]["sha256"],
    )
    all_weights = json.loads(all_weights_raw)
    all_priors = json.loads(all_priors_raw)
    rank_weights = _load(WEIGHTS)
    rank_priors = _load(PRIORS)
    assert len(all_weights) == 101
    assert len(all_priors) == 72
    expected_support = None
    for entry in all_weights.values():
        support = {int(item_id) for item_id in entry["items"]}
        assert len(support) == entry["n_items"] == 702
        assert _ids_sha256(support) == (
            contract.CURRENT_RANKMAP_SCOPE_IDENTITIES["route_stable_210x"][
                "item_ids_sha256"
            ]
        )
        assert set(contract.CURRENT_ALL_MAP_Q6_ITEM_IDS) <= support
        assert set(contract.CURRENT_NON_S2_AUTUMN_Q6_ITEM_IDS) <= support
        assert not set(contract.RETIRED_SUMMER_PARTY_2026_ITEM_IDS) & support
        assert not set(contract.CURRENT_RANKED_ONLY_MECH_Q6_ITEM_IDS) & support
        assert not set(contract.CURRENT_S2_SHADOW_PUPPET_Q6_ITEM_IDS) & support
        assert not set(contract.CURRENT_EXCHANGE_OR_PASS_Q6_ITEM_IDS) & support
        expected_support = support if expected_support is None else expected_support
        assert support == expected_support
    for map_id in contract.ROUTE_STABLE_RANKMAP_MAP_IDS:
        assert rank_weights[str(map_id)] == all_weights[str(map_id)]
        assert rank_priors[str(map_id)] == all_priors[str(map_id)]

    # The support delta is q6-only.  Previous empirical q1-q5 values remain
    # byte-stable; q6 is scaled by this map's old/new modeled conditional mean.
    assert all_priors["2401"]["quality_values"] == {
        "q1": 451,
        "q3": 2_132,
        "q4": 6_396,
        "q5": 34_406,
        "q6": 213_246,
    }
    assert all_priors["2401"]["_value_src"]["q6"] == (
        "previous_empirical_scaled_by_current_q6_model_delta"
    )
    assert not any(map_id.startswith("45") for map_id in all_priors)


def test_direct_s2_q6_and_shadow_puppet_support_are_reachable() -> None:
    weights = _load(WEIGHTS)
    items = load_item_table(ROOT / "data/raw/tables/Item.txt")
    support = weights["5601"]["items"]

    assert set(map(str, contract.CURRENT_S2_SHADOW_PUPPET_Q6_ITEM_IDS)) <= set(support)
    for item_id in contract.CURRENT_S2_SHADOW_PUPPET_Q6_ITEM_IDS:
        item = items[item_id]
        assert (item.quality, item.value, item.raw_row[39]) == (6, 4_000_000, "[402]")
        assert support[str(item_id)][0] > 0
    for item_id, (value, shape) in contract.CURRENT_S2_DIRECT_Q6_CANARIES.items():
        item = items[item_id]
        assert (item.quality, item.value, (item.shape_w, item.shape_h)) == (
            6,
            value,
            shape,
        )
        assert support[str(item_id)][0] > 0
        assert support[str(item_id)][1] == value
    assert "1006018" not in support
    assert "1306018" not in support


def test_new_fitted_red_values_share_the_runtime_longtail_lookup() -> None:
    raw = RED_LONGTAIL.read_bytes()
    assert (len(raw), _sha256(raw)) == (
        contract.CURRENT_RED_LONGTAIL_NORMALIZED["bytes"],
        contract.CURRENT_RED_LONGTAIL_NORMALIZED["sha256"],
    )
    values = json.loads(raw)
    assert len(values) == contract.CURRENT_RED_LONGTAIL_NORMALIZED["items"] == 75
    # Historical replay lookups remain available even though these items are not
    # in the 68171c modeled pool.
    assert values["1016007"] == 223_682
    assert values["1016008"] == 90_297

    weights = _load(WEIGHTS)
    for item_id, fitted in {
        1_006_010: 667_946,
        1_006_012: 809_933,
        1_046_013: 295_159,
        1_916_048: 90_297,
        1_916_059: 465_637,
    }.items():
        assert values[str(item_id)] == fitted
        containing = [
            entry["items"][str(item_id)]
            for entry in weights.values()
            if str(item_id) in entry["items"]
        ]
        assert containing and all(triple[2] == fitted for triple in containing)


def test_current_normalization_is_exactly_equivalent_to_aisha_calib() -> None:
    path = ROOT / "scripts/calibration/aisha_calib.py"
    spec = importlib.util.spec_from_file_location("aisha_calib_68171c_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    catalog = module._catalog()  # noqa: SLF001
    expected = {
        str(item_id): module.normalized_item_value(item_id)
        for item_id, (_value, _shape, quality, _name) in catalog.items()
        if quality == 6 and module.is_longtail(item_id)
    }
    assert expected == _load(RED_LONGTAIL)

    weights = _load(WEIGHTS)
    for entry in weights.values():
        for item_id_text, triple in entry["items"].items():
            assert triple[2] == module.normalized_item_value(int(item_id_text))


def test_cloud_191_gate_retains_prior_without_item_level_exact_claim() -> None:
    authority = _load(AUTHORITY)
    calibration = authority["empirical_calibration"]

    assert calibration["sessions"] == 191
    assert calibration["settled_items"] == 7_126
    assert calibration["method"] == "conservative_wilson95_session_effective_n"
    assert calibration["item_events_are_not_treated_as_independent_trials"] is True
    assert calibration["decision"] == "retain_previous_quality_prior"
    assert all(
        row["retained_prior_inside_ci"] is True
        and row["ci95"][0] <= row["retained_prior"] <= row["ci95"][1]
        for row in calibration["qualities"].values()
    )
    assert authority["claims_item_level_drop_graph"] is False
    assert authority["algorithm"]["quality_prior_decision"] == (
        "retained_after_current_empirical_ci_gate"
    )


def test_cloud_gate_rejects_an_unsupported_quality_prior() -> None:
    rejected = {
        "q1": 0.2980664,
        "q3": 0.2401921,
        "q4": 0.2487037,
        "q5": 0.01,
        "q6": 0.2030378,
    }
    with pytest.raises(
        contract.Update606PackageInputError,
        match="rejected by empirical gate: q5",
    ):
        contract._s2_empirical_calibration_receipt(rejected)  # noqa: SLF001


def test_current_droppable_and_category_authority_share_one_generation() -> None:
    droppable = _load(DROPPABLE)
    assert isinstance(droppable, list)
    ids = {int(row["item_id"]) for row in droppable}
    assert len(ids) == contract.CURRENT_MODEL_UNION_ITEM_ROWS == 717
    assert _ids_sha256(ids) == contract.CURRENT_MODEL_UNION_ITEM_IDS_SHA256

    authority, document = load_item_category_authority_document(
        CATEGORY_AUTHORITY,
        expected_item_table_sha256=(
            contract.CURRENT_TEXT_ASSET_IDENTITIES["Item"].utf8_sha256
        ),
    )
    eligible = {
        item_id
        for item_id, row in authority.records.items()
        if row.current_random_drop_eligible
    }
    assert eligible == ids
    assert document["sources"]["items_droppable"]["rows"] == 717
    assert authority.records[1_006_010].current_random_drop_eligible is True
    assert authority.records[1_006_018].current_random_drop_eligible is False
    assert authority.records[1_016_008].current_random_drop_eligible is False


def _runtime_package(tmp_path: Path) -> Path:
    package = tmp_path / "package"
    files: dict[str, dict[str, object]] = {}
    for relative, spec in runtime._PACKAGE_FILES.items():  # noqa: SLF001
        source = ROOT / relative
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        raw = target.read_bytes()
        assert (len(raw), _sha256(raw)) == (spec["bytes"], spec["sha256"])
        files[relative] = {"size": len(raw), "sha256": _sha256(raw)}
    fingerprint = package / "scripts/package_data_fingerprints.json"
    fingerprint.parent.mkdir(parents=True, exist_ok=True)
    fingerprint.write_text(
        json.dumps({"baseline_id": runtime.PACKAGE_BASELINE_ID, "files": files}),
        encoding="utf-8",
    )
    return package


def test_runtime_binds_all_18_current_maps_and_fails_closed_on_tamper(
    tmp_path: Path,
) -> None:
    package = _runtime_package(tmp_path)
    maps = load_bid_map_table(ROOT / "data/raw/tables/BidMap.txt")
    items = load_item_table(ROOT / "data/raw/tables/Item.txt")

    bound, status, receipt = runtime.bind_current_rankmap_algorithm_prior(
        package_root=package,
        maps=maps,
        drops={},
        items=items,
    )
    assert status == "ok"
    assert isinstance(bound, RankMapAlgorithmPriorBoundDropPools)
    assert len(bound.bound_pool_ids) == 18
    assert receipt["model_distribution_authority"] is True
    assert receipt["official_distribution_authority"] is False
    assert receipt["runtime_open_claim"] is True
    for map_id in contract.CURRENT_RANKMAP_MAP_IDS:
        pool = flatten_pool(maps[map_id].drop_pool_id, bound, items)
        assert pool.resolution_generation == contract.CURRENT_PACKAGE_GENERATION
        assert sum(pool.probabilities) == pytest.approx(1.0)

    target = package / "data/processed/rankmap_drop_weights_current.json"
    raw = bytearray(target.read_bytes())
    raw[-1] ^= 1
    target.write_bytes(raw)
    rejected, rejected_status, rejected_receipt = (
        runtime.bind_current_rankmap_algorithm_prior(
            package_root=package,
            maps=maps,
            drops={},
            items=items,
        )
    )
    assert rejected == {}
    assert rejected_status == "authority_input_hash_mismatch"
    assert rejected_receipt == {}


def test_current_6001_is_dormant_while_historical_identity_is_retained(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    target = package / "data/raw/tables/Item.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "data/raw/tables/Item.txt", target)

    status, receipt = runtime.load_auction_hall_6001_provisional_reference(
        package_root=package,
    )
    assert status == "dormant_current_generation"
    assert receipt == {}
    assert runtime.PREVIOUS_0_3_4_PACKAGE_GENERATION == (
        "4ae819-2026-08-27-665"
    )
    # Keep the 4ae Item identity as an explicit historical replay control;
    # moving-current consumers are asserted separately against 68171c.
    assert contract.PREVIOUS_0_3_4_PACKAGE_ITEM == {
        "bytes": 638_024,
        "sha256": (
            "f5106709c6a042ef6901f339321e203778df12def829a3020952f4e45033f994"
        ),
        "decoded_sha256": contract.ITEM_TABLE_0_3_4_CANONICAL_SHA256,
        "rows": 1_336,
        "columns": 41,
    }
    assert contract.CURRENT_PACKAGE_ITEM == {
        "bytes": 690_668,
        "sha256": (
            "00f9cc95e6951f436522a214bd16641e3e8f813ec4455999c72de6c9bd2de9e4"
        ),
        "decoded_sha256": (
            contract.ITEM_TABLE_OFFICIAL_20260903_HOTFIX_CANONICAL_SHA256
        ),
        "rows": 1_427,
        "columns": 42,
    }
    assert contract.PREVIOUS_OFFICIAL_20260903_PACKAGE_ITEM == {
        "bytes": 689_916,
        "sha256": (
            "9cdcca9b2f55133d647a303fc38a6b6c9f9eb33630cf51008e09c8a0c0d9ee2e"
        ),
        "decoded_sha256": contract.ITEM_TABLE_OFFICIAL_20260903_CANONICAL_SHA256,
        "rows": 1_427,
        "columns": 42,
    }
    assert contract.PREVIOUS_OFFICIAL_20260903_PACKAGE_GENERATION == (
        "eb1e8f-2026-09-03-711"
    )
    assert contract.CURRENT_PACKAGE_BID_MAP == {
        "bytes": 138_352,
        "sha256": (
            "65174e54a6ac3675c39e19b193cb70df06d19f56e920242a2a50e665f53bd505"
        ),
        "decoded_sha256": (
            contract.BID_MAP_OFFICIAL_20260903_HOTFIX_CANONICAL_SHA256
        ),
        "rows": 246,
        "columns": 28,
    }
    assert contract.PREVIOUS_OFFICIAL_20260903_PACKAGE_BID_MAP == {
        "bytes": 138_356,
        "sha256": (
            "34be4bbb51a730e4b410dad87490b7ecb78f7e3b9034c1b88c20743b0a7d9b5e"
        ),
        "decoded_sha256": contract.BID_MAP_OFFICIAL_20260903_CANONICAL_SHA256,
        "rows": 246,
        "columns": 28,
    }


def test_quality_combo_presolve_remains_offline_historical_not_live_quote_input() -> None:
    live_sources = [ROOT / "hero_ref/src/ahmad_ref_engine.py"]
    live_sources.extend((ROOT / "src/bidking_lab/live").glob("*.py"))
    live_sources.extend((ROOT / "src/bidking_lab/inference").rglob("*.py"))
    assert all(
        "quality_combo_presolve_q456" not in path.read_text(encoding="utf-8")
        for path in live_sources
    )
    payload = ROOT / "data/processed/quality_combo_presolve_q456.json"
    assert (payload.stat().st_size, _sha256(payload.read_bytes())) == (
        15_444_339,
        "7b851efb21d08221c09c12c74065a86a5bdfde158f001598e62c857271cfdd42",
    )
