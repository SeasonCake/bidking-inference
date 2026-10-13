from __future__ import annotations

import copy
import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_live_panel_server as panel  # noqa: E402
import ahmad_ref_engine as engine  # noqa: E402
import ahmad_tk_overlay as tk_overlay  # noqa: E402
from bidking_lab.extract.bid_map_table import load_bid_map_table  # noqa: E402
from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansCaptureEvents,
    FatbeansStateEvent,
)
from bidking_lab.live.monitor import (  # noqa: E402
    MonitorTables,
    _auction_hall_6001_support_receipt,
    build_monitor_artifact_from_events,
)
from bidking_lab.runtime.snapshot import ui_contract_from_artifact  # noqa: E402


_load_current_bid_map_table = load_bid_map_table


def load_bid_map_table(path: Path) -> dict[int, object]:
    """Project the frozen 4ae 6001 row for this historical replay module."""

    maps = dict(_load_current_bid_map_table(path))
    maps[6001] = maps[6001].model_copy(update={"items_per_session_min": 22})
    return maps


@pytest.fixture(scope="module", autouse=True)
def _select_historical_4ae_replay_generation() -> None:
    """This module verifies retained 4ae replay, not eb1 live routing."""

    original = engine.CURRENT_RANKMAP_PACKAGE_GENERATION
    engine.CURRENT_RANKMAP_PACKAGE_GENERATION = (
        engine.AUCTION_HALL_6001_HISTORICAL_GENERATION
    )
    engine._load_auction_hall_6001_shadow.cache_clear()
    try:
        yield
    finally:
        engine.CURRENT_RANKMAP_PACKAGE_GENERATION = original
        engine._load_auction_hall_6001_shadow.cache_clear()


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    engine._load_auction_hall_6001_shadow.cache_clear()
    engine._auction_hall_6001_value_quantiles.cache_clear()
    yield
    engine._load_auction_hall_6001_shadow.cache_clear()
    engine._auction_hall_6001_value_quantiles.cache_clear()


@pytest.fixture(scope="module")
def exact_receipt() -> dict[str, object]:
    status, _document = engine._load_auction_hall_6001_shadow()
    assert status == "ok"
    return {
        "schema_version": 1,
        "status": "provisional_estimated",
        "generation": engine.AUCTION_HALL_6001_HISTORICAL_GENERATION,
        "map_id": 6001,
        "drop_pool_id": 2601,
        "table_identity": {
            "bytes": engine.AUCTION_HALL_6001_SHADOW_BYTES,
            "sha256": engine.AUCTION_HALL_6001_SHADOW_SHA256,
        },
        "primary_scenario": engine.AUCTION_HALL_6001_PRIMARY_SCENARIO,
        "primary_support_items": 673,
        "upper_envelope_scenario": engine.AUCTION_HALL_6001_UPPER_SCENARIO,
        "upper_envelope_items": 680,
        "provisional_distribution_available": True,
        "provisional_quote_available": True,
        "distribution_authority": False,
        "official_quote_authority": False,
        "bid_mode": "provisional_estimated_only",
        "confidence": "weak_family_2601_generation_bound",
    }


def _snapshot(receipt: dict[str, object], *, total_count: int = 34) -> dict[str, object]:
    return {
        "created_at": time.time(),
        "session_id": "auction-hall-6001-session",
        "hero": "ahmed",
        "map_id": 6001,
        "round": 2,
        "action_round": 2,
        "observed_round": 2,
        "phase": "bidding",
        "formal_mode": "v2",
        "structured_ref_inputs": {"total_count": total_count},
        "public_info_rows": [],
        "auction_hall_6001_provisional": copy.deepcopy(receipt),
        "ui_contract": {
            "context": {
                "session_id": "auction-hall-6001-session",
                "hero": "ahmed",
                "map_id": 6001,
                "model_map_id": 6001,
                "round": 2,
                "action_round": 2,
                "observed_round": 2,
                "phase": "bidding",
                "formal_mode": "v2",
            },
            "distribution_reference": copy.deepcopy(receipt),
        },
    }


def _assert_provisional(result: object) -> dict[str, object]:
    wire = result.as_dict()
    assert wire["status"] == "provisional_estimated"
    assert wire["source"] == "auction_hall_6001_shadow"
    assert 0 < wire["conservative"] <= wire["balanced"] <= wire["aggressive"]
    assert 0 < wire["value_p25"] <= wire["value_p50"] <= wire["value_p75"]
    provisional = wire["provisional_quote"]
    assert provisional["provisional_distribution_available"] is True
    assert provisional["provisional_quote_available"] is True
    assert provisional["official_distribution_authority"] is False
    assert provisional["official_quote_authority"] is False
    assert provisional["primary_support_items"] == 673
    assert provisional["upper_envelope_items"] == 680
    quality_mass = provisional["primary_quality_mass"]
    assert set(quality_mass) == {"q1", "q3", "q4", "q5", "q6"}
    assert sum(quality_mass.values()) == pytest.approx(1.0, abs=2e-12)
    assert quality_mass["q6"] == pytest.approx(0.206186079381)
    return wire


def test_direct_prepared_summary_api_and_tk_share_one_shadow_identity(
    exact_receipt: dict[str, object],
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(exact_receipt)
    direct = _assert_provisional(engine.run_reference_engine(copy.deepcopy(snapshot)))
    prepared = _assert_provisional(
        engine.run_reference_engine(
            engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
        )
    )
    assert direct == prepared

    summary = panel.summarize_snapshot(
        copy.deepcopy(snapshot),
        snapshot_path=tmp_path / "6001-live.json",
    )
    reference = summary["reference"]
    assert reference["label"] == "Hero Ref · 6001估算"
    assert reference["source"] == "auction_hall_6001_provisional"
    assert reference["readiness"] == "provisional_estimated"
    assert reference["action"] == "6001 估算"
    assert all(reference[key] != "-" for key in ("conservative", "balanced", "aggressive"))
    assert reference["provisional_quote"]["table_identity"] == exact_receipt[
        "table_identity"
    ]
    assert reference["provisional_quote"]["primary_quality_mass"] == direct[
        "provisional_quote"
    ]["primary_quality_mass"]
    assert summary["ahmed_ref"]["provisional_quote"]["table_identity"] == (
        exact_receipt["table_identity"]
    )
    assert summary["red"]["quality_probability_summary"] == "金22.7%·红20.6%"
    assert "6001 估算品质边际" in summary["red"]["quality_probability_hover"]
    assert "非官方联合组合" in summary["red"]["quality_probability_hover"]
    assert "非官方候选" in reference["note"]
    assert summary["quality_composition"]["reason"] == (
        "6001估算提供品质边际与件数区间；无官方联合组合"
    )
    assert any(flag["label"] == "6001 估算" for flag in summary["flags"])
    assert summary["quote_receipt"]["result_status"] == "provisional_estimated"
    assert summary["quote_identity"]["result_identity"] == summary["quote_receipt"][
        "result_identity"
    ]
    assert tk_overlay.summarize_snapshot is panel.summarize_snapshot
    assert tk_overlay._valid_quote_receipt(
        summary["quote_receipt"],
        summary=summary,
    )


@pytest.mark.parametrize("mutation", ("missing", "bool", "nan", "sum", "subclass"))
def test_panel_rejects_invalid_provisional_quality_mass(
    exact_receipt: dict[str, object],
    mutation: str,
) -> None:
    wire = _assert_provisional(
        engine.run_reference_engine(_snapshot(exact_receipt))
    )
    mass = wire["provisional_quote"]["primary_quality_mass"]
    if mutation == "missing":
        mass.pop("q6")
    elif mutation == "bool":
        mass["q6"] = True
    elif mutation == "nan":
        mass["q6"] = float("nan")
    elif mutation == "sum":
        mass["q6"] += 0.01
    else:
        class DictSubclass(dict):
            pass

        wire["provisional_quote"]["primary_quality_mass"] = DictSubclass(mass)

    assert panel._provisional_quality_mass(wire) == {}
    assert panel._quality_probability_summary(wire) == "-"


def test_monitor_receipt_and_ui_projection_are_exact_and_nonbinding(
    exact_receipt: dict[str, object],
) -> None:
    maps = load_bid_map_table(ROOT / "data" / "raw" / "tables" / "BidMap.txt")
    tables = MonitorTables(
        maps={6001: maps[6001]},
        drops={},
        items={},
        auction_hall_6001_status="ok",
        auction_hall_6001_receipt=exact_receipt,
    )
    projected = _auction_hall_6001_support_receipt(6001, tables)
    assert projected == exact_receipt
    assert projected["drop_pool_id"] == 2601
    assert projected["official_quote_authority"] is False

    ui_contract = ui_contract_from_artifact(
        {
            "map_id": 6001,
            "auction_hall_6001_provisional": copy.deepcopy(projected),
        }
    )
    assert ui_contract["distribution_reference"] == projected

    tampered = copy.deepcopy(projected)
    tampered["provisional_quote_available"] = 1
    assert ui_contract_from_artifact(
        {"map_id": 6001, "auction_hall_6001_provisional": tampered}
    )["distribution_reference"] == {}


def test_monitor_artifact_routes_6001_to_the_same_provisional_receipt(
    exact_receipt: dict[str, object],
) -> None:
    maps = load_bid_map_table(ROOT / "data" / "raw" / "tables" / "BidMap.txt")
    tables = MonitorTables(
        maps={6001: maps[6001]},
        drops={},
        items={},
        auction_hall_6001_status="ok",
        auction_hall_6001_receipt=exact_receipt,
    )
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        statuses=(),
        states=(
            FatbeansStateEvent(
                sort_id=1,
                capture_time="2026-08-27 20:00:00.000",
                message_id=37,
                session_id="6001:synthetic",
                map_id=6001,
                round_index=2,
            ),
        ),
    )
    artifact = build_monitor_artifact_from_events(
        events,
        tables=tables,
        n_trials=1,
        roi_trials=0,
        run_debug_shadows=False,
    )
    assert artifact["model_map_id"] == 6001
    assert artifact["formal_mode_reason"] == (
        "auction_hall_6001_provisional_estimated"
    )
    assert artifact["auction_hall_6001_provisional"] == exact_receipt
    assert artifact["ui_contract"]["distribution_reference"] == exact_receipt


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("generation",), "old-generation"),
        (("map_id",), 6002),
        (("table_identity", "sha256"), "0" * 64),
        (("provisional_distribution_available",), False),
        (("provisional_quote_available",), 1),
        (("distribution_authority",), True),
        (("official_quote_authority",), True),
        (("primary_support_items",), 680),
    ),
)
def test_wrong_or_mixed_snapshot_receipt_fails_closed(
    exact_receipt: dict[str, object],
    path: tuple[str, ...],
    value: object,
) -> None:
    snapshot = _snapshot(exact_receipt)
    target = snapshot["auction_hall_6001_provisional"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = engine.run_reference_engine(snapshot).as_dict()
    assert result["status"] == "no_reachable_combo"
    assert (result["conservative"], result["balanced"], result["aggressive"]) == (
        None,
        None,
        None,
    )
    assert "provisional_quote" not in result


def test_missing_receipt_and_map_conflict_fail_closed(
    exact_receipt: dict[str, object],
) -> None:
    missing = _snapshot(exact_receipt)
    missing.pop("auction_hall_6001_provisional")
    assert engine.run_reference_engine(missing).status == "no_reachable_combo"

    conflict = _snapshot(exact_receipt)
    conflict["ui_contract"]["context"]["map_id"] = 6002
    assert engine.run_reference_engine(conflict).status == "no_reachable_combo"

    missing_count = _snapshot(exact_receipt)
    missing_count["structured_ref_inputs"] = {}
    result = engine.run_reference_engine(missing_count)
    assert result.status == "missing_total_count"
    assert (result.conservative, result.balanced, result.aggressive) == (
        None,
        None,
        None,
    )


def test_panel_rejects_6001_mapping_subclass_without_executing_hooks(
    exact_receipt: dict[str, object],
    tmp_path: Path,
) -> None:
    class BombMapping(dict):
        calls = 0

        def get(self, *_args: object, **_kwargs: object) -> object:
            type(self).calls += 1
            raise RuntimeError("6001-mapping-subclass-canary")

    snapshot = _snapshot(exact_receipt)
    snapshot["ui_contract"]["context"] = BombMapping(
        snapshot["ui_contract"]["context"]
    )
    summary = panel.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "6001-mapping-subclass.json",
    )

    assert BombMapping.calls == 0
    assert summary["ahmed_ref"]["status"] == "no_reachable_combo"
    assert all(
        summary["reference"][key] == "-"
        for key in ("conservative", "balanced", "aggressive")
    )


@pytest.mark.parametrize("prepared", (False, True))
def test_6001_total_count_rejects_int_subclass_without_conversion_hooks(
    exact_receipt: dict[str, object],
    tmp_path: Path,
    prepared: bool,
) -> None:
    class AliasInt(int):
        calls = 0

        def __str__(self) -> str:
            type(self).calls += 1
            return "34"

    snapshot = _snapshot(exact_receipt)
    snapshot["structured_ref_inputs"]["total_count"] = AliasInt(999)
    candidate = (
        engine.prepare_reference_engine_snapshot(snapshot)
        if prepared
        else snapshot
    )
    result = engine.run_reference_engine(candidate).as_dict()
    summary = panel.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / f"6001-alias-int-{prepared}.json",
    )

    assert AliasInt.calls == 0
    assert result["status"] == "missing_total_count"
    assert all(result[key] is None for key in ("conservative", "balanced", "aggressive"))
    assert summary["reference"]["readiness"] == "missing_total_count"
    assert all(
        summary["reference"][key] == "-"
        for key in ("conservative", "balanced", "aggressive")
    )
    assert tk_overlay.summarize_snapshot is panel.summarize_snapshot


def test_6001_total_count_bomb_hook_never_escapes(
    exact_receipt: dict[str, object],
) -> None:
    class BombInt(int):
        calls = 0

        def __str__(self) -> str:
            type(self).calls += 1
            raise RuntimeError("TOTAL_COUNT_STR_HOOK")

    snapshot = _snapshot(exact_receipt)
    snapshot["structured_ref_inputs"]["total_count"] = BombInt(34)

    assert engine.run_reference_engine(snapshot).status == "missing_total_count"
    assert BombInt.calls == 0


def test_monitor_6001_rejects_maps_mapping_hook_without_executing_it(
    exact_receipt: dict[str, object],
) -> None:
    class BombMapping(dict):
        calls = 0

        def get(self, *_args: object, **_kwargs: object) -> object:
            type(self).calls += 1
            raise RuntimeError("MONITOR_MAPS_GET_HOOK")

    maps = load_bid_map_table(ROOT / "data" / "raw" / "tables" / "BidMap.txt")
    tables = MonitorTables(
        maps=BombMapping({6001: maps[6001]}),
        drops={},
        items={},
        auction_hall_6001_status="ok",
        auction_hall_6001_receipt=exact_receipt,
    )

    assert _auction_hall_6001_support_receipt(6001, tables) == {}
    assert BombMapping.calls == 0


@pytest.mark.parametrize("bomb", (False, True))
def test_monitor_6001_rejects_map_key_protocol_without_executing_it(
    exact_receipt: dict[str, object],
    bomb: bool,
) -> None:
    class ExternalIntKey(int):
        calls = 0

        def __hash__(self) -> int:
            return int.__hash__(6001)

        def __eq__(self, other: object) -> bool:
            type(self).calls += 1
            if bomb:
                raise RuntimeError("MONITOR_MAP_KEY_EQ_HOOK")
            return type(other) is int and other == 6001  # noqa: E721

    maps = load_bid_map_table(ROOT / "data" / "raw" / "tables" / "BidMap.txt")
    external_maps = {ExternalIntKey(999): maps[6001]}
    ExternalIntKey.calls = 0
    tables = MonitorTables(
        maps=external_maps,
        drops={},
        items={},
        auction_hall_6001_status="ok",
        auction_hall_6001_receipt=exact_receipt,
    )

    assert _auction_hall_6001_support_receipt(6001, tables) == {}
    assert ExternalIntKey.calls == 0


@pytest.mark.parametrize("bomb", (False, True))
def test_6001_rejects_receipt_key_protocol_without_executing_it(
    exact_receipt: dict[str, object],
    bomb: bool,
) -> None:
    class ExternalStringKey(str):
        calls = 0

        def __hash__(self) -> int:
            return str.__hash__("map_id")

        def __eq__(self, other: object) -> bool:
            type(self).calls += 1
            if bomb:
                raise RuntimeError("RECEIPT_KEY_EQ_HOOK")
            return type(other) is str and str.__eq__(other, "map_id")  # noqa: E721

    tampered = dict(exact_receipt)
    tampered.pop("map_id")
    tampered[ExternalStringKey("not_map_id")] = 6001
    ExternalStringKey.calls = 0

    maps = load_bid_map_table(ROOT / "data" / "raw" / "tables" / "BidMap.txt")
    tables = MonitorTables(
        maps={6001: maps[6001]},
        drops={},
        items={},
        auction_hall_6001_status="ok",
        auction_hall_6001_receipt=tampered,
    )
    receipt = _auction_hall_6001_support_receipt(6001, tables)

    assert receipt["status"] == "receipt_scope_mismatch"
    assert receipt["provisional_quote_available"] is False
    assert ExternalStringKey.calls == 0

    ui_contract = ui_contract_from_artifact(
        {
            "map_id": 6001,
            "auction_hall_6001_provisional": tampered,
        }
    )

    assert ui_contract["distribution_reference"] == {}
    assert ExternalStringKey.calls == 0

    snapshot = _snapshot(exact_receipt)
    snapshot["auction_hall_6001_provisional"] = tampered
    snapshot["ui_contract"]["distribution_reference"] = tampered
    result = engine.run_reference_engine(snapshot)

    assert result.status == "no_reachable_combo"
    assert (result.conservative, result.balanced, result.aggressive) == (
        None,
        None,
        None,
    )
    assert ExternalStringKey.calls == 0


@pytest.mark.parametrize("bomb", (False, True))
def test_6001_ui_rejects_identity_key_protocol_without_executing_it(
    exact_receipt: dict[str, object],
    bomb: bool,
) -> None:
    class ExternalStringKey(str):
        calls = 0

        def __hash__(self) -> int:
            return str.__hash__("bytes")

        def __eq__(self, other: object) -> bool:
            type(self).calls += 1
            if bomb:
                raise RuntimeError("UI_IDENTITY_KEY_EQ_HOOK")
            return type(other) is str and str.__eq__(other, "bytes")  # noqa: E721

    tampered = copy.deepcopy(exact_receipt)
    identity = tampered["table_identity"]
    byte_count = identity.pop("bytes")
    identity[ExternalStringKey("not_bytes")] = byte_count
    ExternalStringKey.calls = 0

    ui_contract = ui_contract_from_artifact(
        {
            "map_id": 6001,
            "auction_hall_6001_provisional": tampered,
        }
    )

    assert ui_contract["distribution_reference"] == {}
    assert ExternalStringKey.calls == 0


def test_monitor_6001_rejects_status_subclass_without_serializing_it(
    exact_receipt: dict[str, object],
) -> None:
    class BombStatus(str):
        calls = 0

        def __str__(self) -> str:
            type(self).calls += 1
            raise RuntimeError("MONITOR_STATUS_STR_HOOK")

    maps = load_bid_map_table(ROOT / "data" / "raw" / "tables" / "BidMap.txt")
    tables = MonitorTables(
        maps={6001: maps[6001]},
        drops={},
        items={},
        auction_hall_6001_status=BombStatus("ok"),
        auction_hall_6001_receipt=exact_receipt,
    )

    receipt = _auction_hall_6001_support_receipt(6001, tables)

    assert receipt["status"] == "sidecar_status_invalid"
    assert receipt["provisional_quote_available"] is False
    assert BombStatus.calls == 0


def test_exact_kill_switch_disables_only_provisional_quote(
    exact_receipt: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(engine.AUCTION_HALL_6001_PROVISIONAL_DISABLE, "1")
    engine._load_auction_hall_6001_shadow.cache_clear()
    result = engine.run_reference_engine(_snapshot(exact_receipt))
    assert result.status == "no_reachable_combo"
    assert (result.conservative, result.balanced, result.aggressive) == (
        None,
        None,
        None,
    )


def _install_mutated_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    document: dict[str, object],
) -> None:
    raw = (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=True,
        )
        + "\n"
    ).encode("utf-8")
    path = tmp_path / "auction_hall_6001_shadow_distribution.json"
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    fingerprint = json.loads(
        (ROOT / "scripts" / "package_data_fingerprints.json").read_text(
            encoding="utf-8"
        )
    )
    fingerprint["files"][engine.AUCTION_HALL_6001_SHADOW_RELATIVE] = {
        "sha256": digest,
        "size": len(raw),
    }
    original_moat_json = engine._moat_json

    def fake_moat_json(target: Path, *args: object, **kwargs: object) -> object:
        if Path(target) == ROOT / "scripts" / "package_data_fingerprints.json":
            return copy.deepcopy(fingerprint)
        return original_moat_json(target, *args, **kwargs)

    monkeypatch.setattr(engine, "_moat_json", fake_moat_json)
    monkeypatch.setattr(engine, "AUCTION_HALL_6001_SHADOW_PATH", path)
    monkeypatch.setattr(engine, "AUCTION_HALL_6001_SHADOW_BYTES", len(raw))
    monkeypatch.setattr(engine, "AUCTION_HALL_6001_SHADOW_SHA256", digest)
    engine._load_auction_hall_6001_shadow.cache_clear()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("wrong_generation", "sidecar_contract_mismatch"),
        ("wrong_map", "sidecar_contract_mismatch"),
        ("negative_probability", "sidecar_probability_mismatch"),
        ("sum_drift", "sidecar_probability_mismatch"),
        ("nan_probability", "sidecar_malformed"),
        ("negative_quality_mass", "sidecar_probability_mismatch"),
        ("quality_mass_sum_drift", "sidecar_probability_mismatch"),
        ("nan_quality_mass", "sidecar_malformed"),
    ),
)
def test_semantically_invalid_sidecar_fails_closed_after_identity_rebind(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
    expected: str,
) -> None:
    document = json.loads(
        (ROOT / "data" / "processed" / "auction_hall_6001_shadow_distribution.json").read_text(
            encoding="utf-8"
        )
    )
    if mutation == "wrong_generation":
        document["generation"] = "wrong-generation"
    elif mutation == "wrong_map":
        document["map_id"] = 6002
    elif mutation in {
        "negative_quality_mass",
        "quality_mass_sum_drift",
        "nan_quality_mass",
    }:
        quality_mass = document["summary"]["scenarios"][
            engine.AUCTION_HALL_6001_PRIMARY_SCENARIO
        ]["quality_mass"]
        if mutation == "negative_quality_mass":
            quality_mass["q6"] = -0.01
        elif mutation == "quality_mass_sum_drift":
            quality_mass["q6"] += 0.01
        else:
            quality_mass["q6"] = float("nan")
    else:
        row = next(
            item
            for item in document["items"]
            if item["scenario_probabilities"][
                engine.AUCTION_HALL_6001_PRIMARY_SCENARIO
            ]
            > 0
        )
        if mutation == "negative_probability":
            row["scenario_probabilities"][
                engine.AUCTION_HALL_6001_PRIMARY_SCENARIO
            ] = -0.01
        elif mutation == "sum_drift":
            row["scenario_probabilities"][
                engine.AUCTION_HALL_6001_PRIMARY_SCENARIO
            ] += 0.01
        else:
            row["scenario_probabilities"][
                engine.AUCTION_HALL_6001_PRIMARY_SCENARIO
            ] = float("nan")
        values = list(row["scenario_probabilities"].values())
        row["probability_range_per_draw"] = [min(values), max(values)]
    _install_mutated_sidecar(monkeypatch, tmp_path, document)
    assert engine._load_auction_hall_6001_shadow()[0] == expected


def test_strict_sidecar_parser_rejects_duplicate_and_nan() -> None:
    with pytest.raises(ValueError, match="duplicate key"):
        engine._auction_hall_6001_strict_json(b'{"x":1,"x":2}')
    with pytest.raises(ValueError, match="invalid numeric constant"):
        engine._auction_hall_6001_strict_json(b'{"x":NaN}')


def test_adjacent_map_ignores_6001_receipt(
    exact_receipt: dict[str, object],
) -> None:
    base = {
        "hero": "ahmed",
        "map_id": 2601,
        "phase": "bidding",
        "structured_ref_inputs": {"total_count": 34},
        "public_info_rows": [],
    }
    with_receipt = copy.deepcopy(base)
    with_receipt["auction_hall_6001_provisional"] = copy.deepcopy(exact_receipt)
    assert engine.run_reference_engine(base, max_combos=1_000).as_dict() == (
        engine.run_reference_engine(with_receipt, max_combos=1_000).as_dict()
    )
