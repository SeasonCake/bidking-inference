from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "inspect_0_3_4_official_update.py"
CURRENT_BUILD = "100"
CURRENT_GENERATION = "aaaaaa-2026-08-20-599"
SUCCESSOR_BUILD = "101"
SUCCESSOR_GENERATION = "bbbbbb-2026-08-27-600"
OBSERVED_UNFROZEN_GENERATION = "e8d0e7-2026-09-03-753"
APP_ID = "999999"
INSTALL_DIR = "BidKingTest"
TEXT_COLUMNS = {
    "Activity": 19,
    "ActivityMap": 18,
    "BattleItem": 6,
    "BidMap": 28,
    "Hero": 21,
    "Item": 41,
    "Map": 24,
    "RankMap": 7,
}


def _module():
    spec = importlib.util.spec_from_file_location(
        "inspect_0_3_4_official_update", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(row_id: int, columns: int) -> list[str]:
    return [str(row_id), *("0" for _ in range(columns - 1))]


def _render(rows: list[list[str]]) -> bytes:
    return ("\n".join("\t".join(row) for row in rows) + "\n").encode("utf-8")


def _base_text_assets() -> dict[str, bytes]:
    rows: dict[str, list[list[str]]] = {
        "Activity": [_row(50001, TEXT_COLUMNS["Activity"])],
        "ActivityMap": [_row(30001, TEXT_COLUMNS["ActivityMap"])],
        "BattleItem": [_row(1, TEXT_COLUMNS["BattleItem"])],
        "BidMap": [_row(6001, TEXT_COLUMNS["BidMap"])],
        "Hero": [_row(1, TEXT_COLUMNS["Hero"])],
        "Item": [_row(1, TEXT_COLUMNS["Item"])],
        "Map": [_row(601, TEXT_COLUMNS["Map"])],
        "RankMap": [
            _row(5601, TEXT_COLUMNS["RankMap"]),
            _row(5602, TEXT_COLUMNS["RankMap"]),
        ],
    }
    rows["Activity"][0][18] = "[]"
    rows["ActivityMap"][0][1] = "[601]"
    rows["ActivityMap"][0][12] = "[4541]"
    rows["BidMap"][0][7] = "601"
    return {name: _render(value) for name, value in rows.items()}


def _write_fixture(
    root: Path,
    *,
    successor: bool,
    opening_6001: bool = False,
    generation_override: str | None = None,
) -> dict[str, bytes]:
    artifacts = root / "official" / "artifacts"
    textassets = root / "official" / "textassets"
    artifacts.mkdir(parents=True)
    textassets.mkdir(parents=True)
    build = SUCCESSOR_BUILD if successor else CURRENT_BUILD
    generation = generation_override or (
        SUCCESSOR_GENERATION if successor else CURRENT_GENERATION
    )
    (artifacts / f"appmanifest_{APP_ID}.acf").write_text(
        "\n".join(
            (
                '"AppState"',
                "{",
                f'  "appid" "{APP_ID}"',
                f'  "buildid" "{build}"',
                f'  "installdir" "{INSTALL_DIR}"',
                "}",
            )
        ),
        encoding="utf-8",
    )
    (artifacts / "DefaultPackage.version").write_text(generation, encoding="utf-8")
    (artifacts / f"DefaultPackage_{generation}.hash").write_bytes(b"HASH")
    for name in (
        "BuildinCatalog.bytes",
        "BuildinCatalog.json",
        "manifest.bytes",
        "tables.bundle",
        "Scripts.dll",
    ):
        (artifacts / name).write_bytes(f"fixture:{name}".encode("ascii"))

    current = _base_text_assets()
    written = dict(current)
    if opening_6001:
        activity = [_row(50001, TEXT_COLUMNS["Activity"])]
        activity[0][18] = "[30001]"
        activity_map = [_row(30001, TEXT_COLUMNS["ActivityMap"])]
        activity_map[0][1] = "[601]"
        activity_map[0][12] = "[6001]"
        bid_map = [
            _row(6001, TEXT_COLUMNS["BidMap"]),
            _row(2551, TEXT_COLUMNS["BidMap"]),
        ]
        bid_map[0][7] = "601"
        bid_map[1][7] = "105"
        written.update(
            {
                "Activity": _render(activity),
                "ActivityMap": _render(activity_map),
                "BidMap": _render(bid_map),
            }
        )
    for name, raw in written.items():
        (textassets / f"{name}.txt").write_bytes(raw)
    return current


def _text_identities(module, assets: dict[str, bytes]):
    return {
        name: module.FrozenTextAssetIdentity(
            rows=len(raw.decode("utf-8-sig").splitlines()),
            columns=TEXT_COLUMNS[name],
            utf8_size=len(raw),
            utf8_sha256=hashlib.sha256(raw).hexdigest(),
        )
        for name, raw in assets.items()
    }


def _freeze_current_contract(
    module,
    monkeypatch,
    current: dict[str, bytes],
    *,
    successor: dict[str, bytes] | None = None,
) -> None:
    monkeypatch.setattr(
        module,
        "CURRENT_APP_MANIFEST",
        {"app_id": APP_ID, "build_id": CURRENT_BUILD, "install_dir": INSTALL_DIR},
    )
    monkeypatch.setattr(module, "CURRENT_RANKMAP_MAP_IDS", (5601, 5602))
    profiles = {
        CURRENT_GENERATION: module.FrozenGenerationProfile(
            steam_build_id=CURRENT_BUILD,
            text_assets=_text_identities(module, current),
            artifacts={},
        )
    }
    latest = CURRENT_GENERATION
    if successor is not None:
        profiles[SUCCESSOR_GENERATION] = module.FrozenGenerationProfile(
            steam_build_id=SUCCESSOR_BUILD,
            text_assets=_text_identities(module, successor),
            artifacts={},
        )
        latest = SUCCESSOR_GENERATION
    monkeypatch.setattr(
        module,
        "FROZEN_GENERATION_PROFILES",
        profiles,
    )
    monkeypatch.setattr(module, "LATEST_FROZEN_PACKAGE_GENERATION", latest)


def test_current_generation_is_exact_frozen_input(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    current = _write_fixture(tmp_path, successor=False)
    _freeze_current_contract(module, monkeypatch, current)

    receipt = module.inspect(tmp_path)

    assert receipt["state"] == "current_frozen_input"
    assert receipt["changed_text_assets"] == []
    assert receipt["next_step"] == "use_exact_current_generation_contracts"
    assert receipt["build_input_bundle"] == "not_implemented_not_claimed"


def test_successor_generation_is_exact_frozen_input(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    successor = _write_fixture(tmp_path, successor=True)
    _freeze_current_contract(
        module,
        monkeypatch,
        _base_text_assets(),
        successor=successor,
    )

    receipt = module.inspect(tmp_path)

    assert receipt["state"] == "current_frozen_input"
    assert receipt["matched_frozen_profile"] is True
    assert receipt["comparison_profile_generation"] == SUCCESSOR_GENERATION
    assert receipt["changed_text_assets"] == []
    assert receipt["next_step"] == "use_exact_current_generation_contracts"


def test_exact_historical_profile_is_retained_as_predecessor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    current = _write_fixture(tmp_path, successor=False)
    module = _module()
    _freeze_current_contract(
        module,
        monkeypatch,
        current,
        successor=_base_text_assets(),
    )

    receipt = module.inspect(tmp_path)

    assert receipt["state"] == "predecessor_input"
    assert receipt["matched_frozen_profile"] is True
    assert receipt["comparison_profile_generation"] == CURRENT_GENERATION
    assert receipt["latest_frozen_profile_generation"] == SUCCESSOR_GENERATION
    assert receipt["package_generation_matches_current"] is False
    assert receipt["steam"]["matches_current_generation"] is False
    assert all(
        row["matches_comparison_profile"] is True
        and row["matches_current_generation"] is False
        for row in receipt["text_assets"].values()
    )
    assert receipt["next_step"] == "replace_predecessor_with_current_or_later_input"


def test_successor_without_semantic_table_delta_stays_unfrozen(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    current = _write_fixture(tmp_path, successor=True)
    _freeze_current_contract(module, monkeypatch, current)

    receipt = module.inspect(tmp_path)

    assert receipt["state"] == "observed_unfrozen"
    assert receipt["matched_frozen_profile"] is False
    assert receipt["changed_text_assets"] == []
    assert receipt["critical"]["map_6001"] == {
        "recognized": True,
        "bidmap_parent": "601",
        "bidmap_row_sha256": receipt["critical"]["map_6001"]["bidmap_row_sha256"],
        "static_route_present": False,
        "runtime_availability": "not_established_by_static_intake",
        "runtime_open_claim": False,
        "distribution_authority": False,
        "quote_available": False,
        "qualification": "structure_only_distribution_contract_required",
    }


def test_successor_projects_6001_opening_and_new_activity_without_quote_claim(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    current = _write_fixture(tmp_path, successor=True, opening_6001=True)
    _freeze_current_contract(module, monkeypatch, current)

    receipt = module.inspect(tmp_path)

    assert receipt["changed_text_assets"] == ["Activity", "ActivityMap", "BidMap"]
    assert receipt["critical"]["activity"] == {
        "successor_candidate_map_ids": [2551],
        "official_update_activity_map_ids": [],
        "official_update_ranked_map_ids": [],
        "activity_map_rows_routing_6001": [30001],
        "activity_rows_routing_6001": [50001],
        "runtime_availability": "not_established_by_static_intake",
        "runtime_open_claim": False,
    }
    assert receipt["critical"]["map_6001"]["static_route_present"] is True
    assert receipt["critical"]["map_6001"]["runtime_open_claim"] is False
    assert receipt["critical"]["map_6001"]["distribution_authority"] is False
    assert receipt["critical"]["map_6001"]["quote_available"] is False
    assert receipt["server_actions"] == "not_run"


def test_optional_empty_activity_routes_are_exact_empty_lists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    current = _write_fixture(tmp_path, successor=True)
    activity = tmp_path / "official" / "textassets" / "Activity.txt"
    activity_map = tmp_path / "official" / "textassets" / "ActivityMap.txt"
    activity_rows = module._text_rows("Activity", activity.read_bytes())
    activity_map_rows = module._text_rows("ActivityMap", activity_map.read_bytes())
    activity_rows[0][18] = ""
    activity_map_rows[0][12] = ""
    activity.write_bytes(_render(activity_rows))
    activity_map.write_bytes(_render(activity_map_rows))
    _freeze_current_contract(module, monkeypatch, current)

    receipt = module.inspect(tmp_path)

    assert receipt["critical"]["activity"]["activity_map_rows_routing_6001"] == []
    assert receipt["critical"]["activity"]["activity_rows_routing_6001"] == []
    assert receipt["critical"]["rankmap"]["runtime_open_claim"] is False


@pytest.mark.parametrize("bad", ("null", "true", "[6001,true]", "not-json"))
def test_optional_activity_route_nonempty_malformed_or_non_int_fails_closed(
    bad: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    current = _write_fixture(tmp_path, successor=True)
    activity_map = tmp_path / "official" / "textassets" / "ActivityMap.txt"
    rows = module._text_rows("ActivityMap", activity_map.read_bytes())
    rows[0][12] = bad
    activity_map.write_bytes(_render(rows))
    _freeze_current_contract(module, monkeypatch, current)

    with pytest.raises(module.OfficialUpdateIntakeError, match="not JSON|integer array"):
        module.inspect(tmp_path)


def test_mixed_steam_and_package_generation_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    successor = _write_fixture(tmp_path, successor=True)
    _freeze_current_contract(
        module,
        monkeypatch,
        _base_text_assets(),
        successor=successor,
    )
    appmanifest = tmp_path / "official" / "artifacts" / f"appmanifest_{APP_ID}.acf"
    appmanifest.write_text(
        appmanifest.read_text(encoding="utf-8").replace(
            f'"buildid" "{SUCCESSOR_BUILD}"',
            f'"buildid" "{CURRENT_BUILD}"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(module.OfficialUpdateIntakeError, match="mixed"):
        module.inspect(tmp_path)


def test_current_generation_with_changed_table_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    current = _write_fixture(tmp_path, successor=False, opening_6001=True)
    _freeze_current_contract(module, monkeypatch, current)

    with pytest.raises(module.OfficialUpdateIntakeError, match="changed TextAsset"):
        module.inspect(tmp_path)


def test_exact_successor_wrong_textasset_sha_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    successor = _write_fixture(tmp_path, successor=True)
    _freeze_current_contract(
        module,
        monkeypatch,
        _base_text_assets(),
        successor=successor,
    )
    item = tmp_path / "official" / "textassets" / "Item.txt"
    item.write_bytes(item.read_bytes().replace(b"1\t", b"2\t", 1))

    with pytest.raises(module.OfficialUpdateIntakeError, match="changed TextAsset"):
        module.inspect(tmp_path)


def test_exact_successor_wrong_artifact_sha_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    successor = _write_fixture(tmp_path, successor=True)
    _freeze_current_contract(
        module,
        monkeypatch,
        _base_text_assets(),
        successor=successor,
    )
    scripts_path = tmp_path / "official" / "artifacts" / "Scripts.dll"
    scripts_raw = scripts_path.read_bytes()
    profiles = dict(module.FROZEN_GENERATION_PROFILES)
    selected = profiles[SUCCESSOR_GENERATION]
    profiles[SUCCESSOR_GENERATION] = module.FrozenGenerationProfile(
        steam_build_id=selected.steam_build_id,
        text_assets=selected.text_assets,
        artifacts={
            "Scripts.dll": {
                "bytes": len(scripts_raw),
                "sha256": hashlib.sha256(scripts_raw).hexdigest(),
            }
        },
    )
    monkeypatch.setattr(module, "FROZEN_GENERATION_PROFILES", profiles)
    scripts_path.write_bytes(scripts_raw + b"tamper")

    with pytest.raises(module.OfficialUpdateIntakeError, match="changed artifact"):
        module.inspect(tmp_path)


def test_e8_observed_generation_cannot_match_by_date_or_steam_build(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    successor = _write_fixture(
        tmp_path,
        successor=True,
        generation_override=OBSERVED_UNFROZEN_GENERATION,
    )
    _freeze_current_contract(
        module,
        monkeypatch,
        _base_text_assets(),
        successor=successor,
    )

    receipt = module.inspect(tmp_path)

    assert receipt["state"] == "observed_unfrozen"
    assert receipt["matched_frozen_profile"] is False
    assert receipt["package_generation"] == OBSERVED_UNFROZEN_GENERATION
    assert receipt["next_step"] == (
        "freeze_exact_generation_inputs_before_consumer_authority"
    )


def test_raw_bom_crlf_identity_is_distinct_from_canonical_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    successor = _write_fixture(tmp_path, successor=True)
    item_path = tmp_path / "official" / "textassets" / "Item.txt"
    canonical = successor["Item"]
    raw_bom_crlf = b"\xef\xbb\xbf" + canonical.replace(b"\n", b"\r\n")
    item_path.write_bytes(raw_bom_crlf)
    successor_with_raw_identity = dict(successor)
    successor_with_raw_identity["Item"] = raw_bom_crlf
    _freeze_current_contract(
        module,
        monkeypatch,
        _base_text_assets(),
        successor=successor_with_raw_identity,
    )

    receipt = module.inspect(tmp_path)
    assert receipt["state"] == "current_frozen_input"
    assert receipt["text_assets"]["Item"]["sha256"] == hashlib.sha256(
        raw_bom_crlf
    ).hexdigest()

    item_path.write_bytes(canonical)
    with pytest.raises(module.OfficialUpdateIntakeError, match="changed TextAsset"):
        module.inspect(tmp_path)


def test_missing_required_input_is_reported_before_any_authority_claim(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    current = _write_fixture(tmp_path, successor=True)
    _freeze_current_contract(module, monkeypatch, current)
    (tmp_path / "official" / "textassets" / "RankMap.txt").unlink()

    with pytest.raises(module.OfficialUpdateIntakeError, match="RankMap.txt"):
        module.inspect(tmp_path)


def test_wrong_app_identity_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    current = _write_fixture(tmp_path, successor=True)
    _freeze_current_contract(module, monkeypatch, current)
    appmanifest = tmp_path / "official" / "artifacts" / f"appmanifest_{APP_ID}.acf"
    appmanifest.write_text(
        appmanifest.read_text(encoding="utf-8").replace(
            f'"appid" "{APP_ID}"',
            '"appid" "111111"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(module.OfficialUpdateIntakeError, match="appid mismatch"):
        module.inspect(tmp_path)


def test_real_profile_registry_keeps_eb1_and_promotes_only_68171c() -> None:
    module = _module()

    assert module.LATEST_FROZEN_PACKAGE_GENERATION == (
        "68171c-2026-09-03-907"
    )
    assert module.FROZEN_GENERATION_PROFILES[
        "eb1e8f-2026-09-03-711"
    ].steam_build_id == "25094235"
    current = module.FROZEN_GENERATION_PROFILES["68171c-2026-09-03-907"]
    assert current.steam_build_id == "25096377"
    assert current.text_assets["Item"].utf8_sha256 == (
        "bfec359189e9fb5c77440abdc36f2fad7d77299e5d37394b5b855a8e4dcae57e"
    )
    assert current.text_assets["BidMap"].utf8_sha256 == (
        "f93a30b6bbcad22a003cf1363919af1d42a2eb600ec9ee006dc37865a0fc15a6"
    )
    assert OBSERVED_UNFROZEN_GENERATION not in module.FROZEN_GENERATION_PROFILES
