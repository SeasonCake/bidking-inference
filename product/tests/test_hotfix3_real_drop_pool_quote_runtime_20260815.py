from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

import pytest

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropEntry, DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.live import monitor as live_monitor
from bidking_lab.live.monitor import MonitorTables
from bidking_lab.simulation.basic_mc import (
    DropPoolResolutionError,
    DropPoolResolutionPolicy,
    PolicyBoundDropPools,
    _bind_verified_drop_pool_resolution_policy,
    bind_drop_pool_resolution_policy,
    flatten_pool,
)


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, relative: str):
    path = ROOT / relative
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _item(item_id: int = 1, *, value: int = 100) -> Item:
    return Item(
        item_id=item_id,
        name=f"item_{item_id}",
        description="",
        name_key=f"key_{item_id}",
        desc_key="",
        quality=3,
        quality_color="blue",
        value=value,
        shape_w=1,
        shape_h=1,
        tags=[],
        allowed_shelves=[],
        icon_name="",
        model_name="",
        raw_row=["0"] * 41,
    )


def _pool(
    pool_id: int,
    entries: list[tuple[int, int, int]],
) -> DropPool:
    return DropPool(
        pool_id=pool_id,
        name=f"pool_{pool_id}",
        description="",
        pool_type=2,
        entries=[
            DropEntry(
                category=category,
                item_id=item_id,
                n_min=1,
                n_max=1,
                weight=weight,
            )
            for category, item_id, weight in entries
        ],
    )


def _map(map_id: int, drop_pool_id: int) -> BidMap:
    return BidMap(
        map_id=map_id,
        name=f"map_{map_id}",
        description="",
        category=101,
        auction_mode="open",
        sub_pool_weights=[],
        rounds_total=5,
        entry_fee_silver=0,
        starting_budget_silver=100_000,
        drop_pool_id=drop_pool_id,
        items_per_session_min=1,
        items_per_session_max=1,
        value_tier_ui="ui_value_low",
        mode_flag=4,
        bid_price_ladder=[],
        raw_row=["0"] * 28,
    )


def _policy(*, allowed_roots: frozenset[int] = frozenset({900})):
    return DropPoolResolutionPolicy(
        policy_id="test_generation_bound_legacy_gap",
        package_generation="test-generation",
        allowed_root_pool_ids=allowed_roots,
        allowed_unknown_leaves=frozenset({(1066, 1_106_013)}),
    )


def _nested_unknown_drop() -> dict[int, DropPool]:
    return {
        900: _pool(900, [(9999, 1066, 1)]),
        1066: _pool(1066, [(101, 1, 3), (106, 1_106_013, 1)]),
    }


def test_generation_bound_legacy_leaf_keeps_residual_mass_observable() -> None:
    drops = _bind_verified_drop_pool_resolution_policy(
        _nested_unknown_drop(),
        _policy(),
    )

    flattened = flatten_pool(900, drops, {1: _item()})

    assert flattened.item_ids == [1]
    assert flattened.probabilities == [pytest.approx(1.0)]
    assert flattened.resolution_status == "conditional_known_support"
    assert flattened.unresolved_probability == pytest.approx(0.25)
    assert flattened.unresolved_leaf_count == 1
    assert flattened.resolution_policy == "test_generation_bound_legacy_gap"
    assert flattened.resolution_generation == "test-generation"


def test_public_policy_binding_data_cannot_authorize_unknown_leaf() -> None:
    public_bound = bind_drop_pool_resolution_policy(
        _nested_unknown_drop(),
        _policy(),
    )

    with pytest.raises(DropPoolResolutionError, match="unknown_item"):
        flatten_pool(900, public_bound, {1: _item()})


@pytest.mark.parametrize("case", ["wrong_root", "other_unknown", "missing", "empty"])
def test_legacy_leaf_policy_keeps_other_incomplete_inputs_fail_closed(case: str) -> None:
    drops = _nested_unknown_drop()
    root = 900
    policy = _policy()
    if case == "wrong_root":
        policy = _policy(allowed_roots=frozenset({901}))
    elif case == "other_unknown":
        drops[1066] = _pool(1066, [(101, 1, 3), (106, 1_106_014, 1)])
    elif case == "missing":
        drops[900] = _pool(900, [(9999, 1067, 1)])
    elif case == "empty":
        drops[1066] = _pool(1066, [])
    bound = _bind_verified_drop_pool_resolution_policy(drops, policy)

    with pytest.raises(DropPoolResolutionError):
        flatten_pool(root, bound, {1: _item()})


def test_complete_pool_output_is_unchanged_and_has_no_degraded_receipt() -> None:
    plain = {900: _pool(900, [(101, 1, 3), (101, 2, 1)])}
    items = {1: _item(1), 2: _item(2, value=200)}
    strict = flatten_pool(900, plain, items)
    bound = flatten_pool(
        900,
        _bind_verified_drop_pool_resolution_policy(plain, _policy()),
        items,
    )

    assert bound == strict
    assert bound.resolution_status == "complete"
    assert bound.unresolved_probability == 0


def test_runtime_binding_requires_all_four_exact_generation_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tables = tmp_path / "data" / "raw" / "tables"
    tables.mkdir(parents=True)
    weights_path = tmp_path / "data" / "processed" / "drop_weights_all_maps_0627.json"
    weights_path.parent.mkdir(parents=True)
    payloads = {
        "BidMap.txt": b"exact-bidmap",
        "Item.txt": b"exact-item",
        "Drop.txt": b"exact-drop",
        "drop_weights_all_maps_0627.json": json.dumps(
            {"900": {"items": {"1": [1.0, 100, 1]}}},
            separators=(",", ":"),
        ).encode("utf-8"),
    }
    identities = {
        name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        for name, raw in payloads.items()
    }
    current_payloads = {
        "BidMap.txt": b"exact-current-bidmap",
        "Item.txt": b"exact-current-item",
    }
    current_identities = {
        name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        for name, raw in current_payloads.items()
    }
    monkeypatch.setattr(live_monitor, "UPDATE606_PACKAGE_BID_MAP", identities["BidMap.txt"])
    monkeypatch.setattr(live_monitor, "UPDATE606_PACKAGE_ITEM", identities["Item.txt"])
    monkeypatch.setattr(
        live_monitor,
        "CURRENT_PACKAGE_BID_MAP",
        current_identities["BidMap.txt"],
    )
    monkeypatch.setattr(
        live_monitor,
        "CURRENT_PACKAGE_ITEM",
        current_identities["Item.txt"],
    )
    monkeypatch.setattr(
        live_monitor,
        "CURRENT_PACKAGE_GENERATION",
        "current-test-generation",
    )
    monkeypatch.setattr(live_monitor, "LEGACY_PACKAGE_DROP", identities["Drop.txt"])
    monkeypatch.setitem(
        live_monitor.LEGACY_MAP_PAYLOADS,
        "drop_weights_all_maps_0627",
        {
            "output_bytes": identities["drop_weights_all_maps_0627.json"]["bytes"],
            "output_sha256": identities["drop_weights_all_maps_0627.json"]["sha256"],
            "output_maps": 1,
        },
    )
    monkeypatch.setattr(
        live_monitor,
        "_load_package_plain_bytes",
        lambda path: payloads[path.name],
    )
    raw_drops = _nested_unknown_drop()

    bound = live_monitor._bind_update606_legacy_drop_policy(tables, raw_drops)
    assert isinstance(bound, PolicyBoundDropPools)
    assert 900 in bound.resolution_policy.allowed_root_pool_ids
    assert (
        bound.resolution_policy.package_generation
        == live_monitor.BID_MAP_UPDATE606_GENERATION
    )

    payloads.update(current_payloads)
    current_bound = live_monitor._bind_update606_legacy_drop_policy(
        tables,
        raw_drops,
    )
    assert isinstance(current_bound, PolicyBoundDropPools)
    assert current_bound.resolution_policy.package_generation == "current-test-generation"
    assert 900 in current_bound.resolution_policy.allowed_root_pool_ids

    payloads["Item.txt"] = b"exact-item"
    rejected = live_monitor._bind_update606_legacy_drop_policy(tables, raw_drops)
    assert rejected is raw_drops
    with pytest.raises(DropPoolResolutionError, match="unknown_item"):
        flatten_pool(900, rejected, {1: _item()})


def test_monitor_receipt_is_degraded_only_for_authorized_unknown_leaf() -> None:
    tables = MonitorTables(
        maps={900: _map(900, 900)},
        drops=_bind_verified_drop_pool_resolution_policy(
            _nested_unknown_drop(),
            _policy(),
        ),
        items={1: _item()},
    )
    receipt = live_monitor._drop_pool_support_receipt(900, tables)

    assert receipt == {
        "schema_version": 1,
        "status": "legacy_incomplete_conditional_known_support",
        "reason": "legacy_unknown_leaf",
        "policy": "test_generation_bound_legacy_gap",
        "package_generation": "test-generation",
        "unresolved_probability": 0.25,
        "unresolved_probability_ppm": 250_000,
        "unresolved_leaf_count": 1,
        "modeled_probability_normalized": True,
        "quote_quality": "degraded",
    }

    complete = MonitorTables(
        maps={900: _map(900, 900)},
        drops=_bind_verified_drop_pool_resolution_policy(
            {900: _pool(900, [(101, 1, 1)])},
            _policy(),
        ),
        items={1: _item()},
    )
    assert live_monitor._drop_pool_support_receipt(900, complete) == {}


def test_monitor_error_sidecar_uses_support_safe_terminal_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script(
        "run_fatbeans_webhook_monitor_drop_pool_test",
        "scripts/run_fatbeans_webhook_monitor.py",
    )
    log_dir = tmp_path / "logs"
    raw_dir = tmp_path / "raw"
    monkeypatch.setattr(
        module,
        "_semantic_signature_from_payload",
        lambda _payload: ("recognized", 1),
    )

    def fail_build(*_args, **_kwargs):
        raise module.DropPoolResolutionError(
            "unknown_item",
            pool_id=1066,
            item_id=1_106_013,
        )

    monkeypatch.setattr(module, "build_monitor_artifact_from_payload", fail_build)
    monitor = module.FatbeansWebhookMonitor(
        config=module.WebhookMonitorConfig(
            log_dir=log_dir,
            raw_dir=raw_dir,
            process_name="BidKing.exe",
            server_ports=(10000,),
            n_trials=1,
            roi_trials=0,
            shadow_trials=1,
            full_shadow_trials=1,
            run_debug_shadows=False,
            seed=1,
            debounce_seconds=0.0,
            min_inference_interval_seconds=0.0,
        ),
        tables=object(),
    )
    monitor.accept_row({"SortID": 1, "Direct": "REV"}, schedule_process=True)
    monitor._process_snapshot(force=True)

    status = json.loads((log_dir / module.COMPUTE_STATUS_FILENAME).read_text("utf-8"))
    assert status["event"] == "error"
    assert status["terminal_reason"] == "drop_pool_incomplete"
    rendered = json.dumps(status, sort_keys=True)
    assert "1066" not in rendered
    assert "1106013" not in rendered
    assert "unknown_item" not in rendered


def test_capture_error_beats_non_game_frame_wait_copy() -> None:
    overlay = _load_script(
        "ahmad_tk_overlay_drop_pool_test",
        "hero_ref/tools/ahmad_tk_overlay.py",
    )
    diagnostics = overlay._capture_wait_diagnostics(
        {
            "source": "wss_local",
            "active_flows": 1,
            "raw_packets": 41,
            "accepted_frames": 38,
            "ignored_frames": 3,
            "ignored_reasons": {"rev_not_game_frame": 3},
            "_network_connection_status": {
                "schema_version": 1,
                "launch_id": "same-launch",
                "game_connection_state": "connected",
                "wss_intercept_state": "attached",
                "capture_decode_state": "recognized",
            },
            "_current_launch_id": "same-launch",
            "_monitor_compute_status": {
                "schema_version": 1,
                "event": "error",
                "trace_seq": 10,
                "terminal_reason": "drop_pool_incomplete",
            },
        }
    )

    assert diagnostics["state"] == "drop_pool_incomplete"
    assert diagnostics["subtitle"] == "已读取对局，掉落资料暂不完整"
    assert diagnostics["action"] == "请导出诊断"
    assert diagnostics["recent"] == "drop_pool_incomplete"
    assert "进入新局" not in json.dumps(diagnostics, ensure_ascii=False)
    assert "1066" not in json.dumps(diagnostics, ensure_ascii=False)
    assert "1106013" not in json.dumps(diagnostics, ensure_ascii=False)


def test_summary_api_and_tk_flags_publish_degraded_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel = _load_script(
        "ahmad_live_panel_server_drop_pool_test",
        "hero_ref/tools/ahmad_live_panel_server.py",
    )
    monkeypatch.setattr(panel, "run_reference_engine", None)
    created_at = time.time()
    support = {
        "schema_version": 1,
        "status": "legacy_incomplete_conditional_known_support",
        "reason": "legacy_unknown_leaf",
        "policy": "update606_legacy_drop_known_support_v1",
        "package_generation": "cd4af1-2026-08-13-606",
        "unresolved_probability": 0.00005,
        "unresolved_probability_ppm": 50,
        "unresolved_leaf_count": 1,
        "modeled_probability_normalized": True,
        "quote_quality": "degraded",
    }
    snapshot = {
        "created_at": created_at,
        "drop_pool_support": support,
        "ui_contract": {
            "context": {
                "hero": "liss",
                "map_id": 2408,
                "round": 1,
                "phase": "bidding",
                "session_id": "2408:test",
            },
            "baseline": {"decision": {}, "posterior": {}},
            "constraints": {"public_info": {}},
            "source": {"created_at": created_at},
        },
    }

    summary = panel.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert summary["drop_pool_support"] == support
    assert summary["diagnostics"]["drop_pool_support"] == support
    assert "掉落资料降级" in {flag["label"] for flag in summary["flags"]}


def test_exact_replay_checker_rejects_changed_capture_without_echoing_identity(
    tmp_path: Path,
) -> None:
    checker = _load_script(
        "check_hotfix3_real_drop_pool_quote_runtime_test",
        "scripts/check_hotfix3_real_drop_pool_quote_runtime.py",
    )
    capture = tmp_path / "private-capture.json"
    capture.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="^capture_identity_mismatch$"):
        checker._read_exact_capture(capture, checker._CAPTURE_CONTRACTS[0])
