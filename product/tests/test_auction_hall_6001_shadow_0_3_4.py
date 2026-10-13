from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALIBRATION = ROOT / "scripts" / "calibration"
if str(CALIBRATION) not in sys.path:
    sys.path.insert(0, str(CALIBRATION))

import build_auction_hall_6001_shadow as shadow  # noqa: E402

SCRIPTS_ENV = "BIDKING_6001_SCRIPTS_DLL"
RETIRED_WORLD_CUP_Q6_IDS = {1016007, 1036006, 1036007, 1036008}
LOCALIZATION_ALIASES = {1046010, 1046011, 1046012}
CATALOG_ENVELOPE_ONLY_IDS = {
    1006001,
    1006002,
    1006003,
    1006004,
    1006005,
    1006006,
    1006007,
}


def _inputs() -> dict[str, Path]:
    # The explicit environment input keeps Scripts.dll copy-only and out of source.
    scripts = Path(os.environ[SCRIPTS_ENV])
    return {
        "item_table": ROOT / "data" / "raw" / "tables" / "Item.txt",
        "bid_map": ROOT / "data" / "raw" / "tables" / "BidMap.txt",
        "rank_map": ROOT / "data" / "raw" / "tables" / "RankMap.txt",
        "scripts_dll": scripts,
        "items_droppable": ROOT / "data" / "processed" / "items_droppable.json",
        "drop_weights": ROOT
        / "data"
        / "processed"
        / "drop_weights_all_maps_0627.json",
        "drop_priors": ROOT
        / "data"
        / "processed"
        / "drop_table_priors_0627.json",
        "red_longtail": ROOT
        / "data"
        / "processed"
        / "red_longtail_normalized_0627.json",
    }


def _package_ready() -> bool:
    try:
        paths = _inputs()
    except KeyError:
        return False
    return all(path.is_file() for path in paths.values())


requires_exact_package = pytest.mark.skipif(
    not _package_ready(),
    reason=f"exact package-shaped inputs require {SCRIPTS_ENV}",
)


@pytest.fixture(scope="module")
def built_shadow() -> tuple[dict[str, object], dict[str, object]]:
    return shadow.build_shadow(**_inputs())


@requires_exact_package
def test_exact_shadow_is_normalized_provisional_and_not_a_quote(
    built_shadow: tuple[dict[str, object], dict[str, object]],
) -> None:
    document, receipt = built_shadow
    assert document["generation"] == "4ae819-2026-08-27-665"
    assert document["map_id"] == 6001
    assert document["distribution_authority"] is False
    assert document["official_distribution_eligible"] is False
    assert document["provisional_rate_available"] is True
    assert document["provisional_distribution_available"] is True
    assert document["provisional_quote_available"] is True
    assert document["quote_authority"] is False
    assert document["official_quote_authority"] is False
    assert document["affects_bid"] is True
    assert document["bid_mode"] == "provisional_estimated_only"

    algorithm = document["algorithm"]
    assert algorithm["round_skill_group_ids"] == [103, 103, 103, 104, 105]
    assert (
        algorithm["round_skill_group_policy"]
        == "identity_only_not_distribution_input"
    )
    assert algorithm["primary_scenario"] == "group2601_placement_aware_candidate"
    assert algorithm["upper_envelope_scenario"] == "all_current_catalog_upper_envelope"
    assert algorithm["item_weight_algorithm"] == (
        "validated_rankmap_col6_loglog_extrapolation"
    )
    assert "category" not in " ".join(algorithm).lower()
    assert algorithm["weak_family_quality_prior_sum"] == pytest.approx(1.0000001)
    assert sum(algorithm["weak_family_quality_prior_normalized"].values()) == pytest.approx(
        1.0,
        abs=1e-12,
    )

    scenarios = document["summary"]["scenarios"]
    assert scenarios["group2601_placement_aware_candidate"]["support_items"] == 673
    assert scenarios["all_current_catalog_upper_envelope"]["support_items"] == 680
    for scenario in scenarios.values():
        assert scenario["probability_sum"] == pytest.approx(1.0, abs=1e-12)
        assert scenario["quality_mass"] == pytest.approx(
            algorithm["weak_family_quality_prior_normalized"],
            abs=1e-12,
        )
    assert set(document["summary"]["catalog_envelope_only_ids"]) == (
        CATALOG_ENVELOPE_ONLY_IDS
    )
    for scenario_name in scenarios:
        serialized_sum = sum(
            row["scenario_probabilities"][scenario_name]
            for row in document["items"]
        )
        assert scenarios[scenario_name]["probability_sum"] == serialized_sum
        assert receipt["output"]["scenario_probability_sums"][scenario_name] == (
            serialized_sum
        )
    assert receipt["frozen_prior_unchanged"] is True


@requires_exact_package
def test_shadow_support_excludes_retired_and_aliases_and_preserves_ab_control(
    built_shadow: tuple[dict[str, object], dict[str, object]],
) -> None:
    document, _receipt = built_shadow
    rows = {row["item_id"]: row for row in document["items"]}
    assert not (RETIRED_WORLD_CUP_Q6_IDS & rows.keys())
    assert not (LOCALIZATION_ALIASES & rows.keys())
    for item_id in CATALOG_ENVELOPE_ONLY_IDS:
        probabilities = rows[item_id]["scenario_probabilities"]
        assert probabilities["group2601_placement_aware_candidate"] == 0.0
        assert probabilities["all_current_catalog_upper_envelope"] > 0.0


@requires_exact_package
def test_shadow_does_not_mutate_frozen_prior(
    built_shadow: tuple[dict[str, object], dict[str, object]],
) -> None:
    prior_path = _inputs()["drop_priors"]
    before = prior_path.read_bytes()
    shadow.build_shadow(**_inputs())
    after = prior_path.read_bytes()
    assert after == before
    assert hashlib.sha256(after).hexdigest() == shadow.DROP_PRIORS["sha256"]


@requires_exact_package
@pytest.mark.parametrize(
    "name",
    [
        "item_table",
        "bid_map",
        "rank_map",
        "scripts_dll",
        "items_droppable",
        "drop_weights",
        "drop_priors",
        "red_longtail",
    ],
)
def test_each_tampered_input_is_rejected(tmp_path: Path, name: str) -> None:
    inputs = _inputs()
    tampered = tmp_path / inputs[name].name
    shutil.copyfile(inputs[name], tampered)
    with tampered.open("ab") as handle:
        handle.write(b"tamper")
    inputs[name] = tampered
    with pytest.raises(shadow.AuctionHallShadowError, match="identity mismatch"):
        shadow.build_shadow(**inputs)


def test_missing_input_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(shadow.AuctionHallShadowError, match="missing/unreadable"):
        shadow._exact(
            tmp_path / "missing.json",
            shadow.DROP_PRIORS,
            label="drop priors",
        )


def test_duplicate_json_is_rejected() -> None:
    with pytest.raises(shadow.AuctionHallShadowError, match="duplicate probe key"):
        shadow._strict_json(b'{"x": 1, "x": 2}', label="probe")


@requires_exact_package
def test_cli_rejects_output_receipt_path_collision(tmp_path: Path) -> None:
    inputs = _inputs()
    output = tmp_path / "same.json"
    assert shadow.main(
        [
            "--item-table",
            str(inputs["item_table"]),
            "--bid-map",
            str(inputs["bid_map"]),
            "--rank-map",
            str(inputs["rank_map"]),
            "--scripts-dll",
            str(inputs["scripts_dll"]),
            "--items-droppable",
            str(inputs["items_droppable"]),
            "--drop-weights",
            str(inputs["drop_weights"]),
            "--drop-priors",
            str(inputs["drop_priors"]),
            "--red-longtail",
            str(inputs["red_longtail"]),
            "--out",
            str(output),
            "--receipt-out",
            str(output),
        ]
    ) == 2
    assert not output.exists()
