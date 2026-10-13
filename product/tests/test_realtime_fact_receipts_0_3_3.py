from __future__ import annotations

from decimal import Decimal
import json
import os
import struct
import subprocess
import sys
import threading
import time
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hero_ref" / "src"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import ahmad_ref_engine as engine
from bidking_lab.live import fact_receipts
from bidking_lab.live.fact_receipts import (
    FactReceiptWriter,
    current_receipt_bundle_paths,
    fact_receipt_session_binding,
)
from bidking_lab.live.fatbeans import FatbeansCaptureEvents  # noqa: E402
from bidking_lab.live.monitor import (
    _current_completed_action_ids,
    _promote_current_action_rows,
)


def _monitor_module():
    spec = importlib.util.spec_from_file_location(
        "fact_receipt_monitor", ROOT / "scripts" / "run_fatbeans_webhook_monitor.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _overlay_module():
    spec = importlib.util.spec_from_file_location(
        "fact_receipt_overlay",
        ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _wait_for(path: Path) -> None:
    for _ in range(100):
        if path.exists():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path.name}")


def _wait_for_bundle(root: Path, session_id: str) -> list[Path]:
    for _ in range(200):
        paths = current_receipt_bundle_paths(
            root,
            expected_session_id=session_id,
        )
        if paths:
            return paths
        time.sleep(0.01)
    raise AssertionError("timed out waiting for current receipt bundle")


def _child_bundle_names(root: Path, expected_session_id: str) -> list[str]:
    script = (
        "import json,sys; from pathlib import Path; "
        "from bidking_lab.live.fact_receipts import current_receipt_bundle_paths; "
        "print(json.dumps([p.name for p in current_receipt_bundle_paths("
        "Path(sys.argv[1]), expected_session_id=sys.argv[2])]))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script, str(root), expected_session_id],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    return json.loads(completed.stdout)


def test_fact_receipt_writes_current_session_without_raw_identity(tmp_path: Path) -> None:
    writer = FactReceiptWriter(tmp_path, max_receipts=4, max_bytes=4096)
    try:
        assert writer.enqueue(
            session_id="2401:123456789",
            receipt={"parser": {"outcome": "accepted"}, "quotes": [1, 2, 3]},
        )
        manifest = tmp_path / "fact_receipts" / "current_manifest.json"
        _wait_for(manifest)
    finally:
        writer.close()

    paths = current_receipt_bundle_paths(
        tmp_path,
        expected_session_id="2401:123456789",
    )
    assert len(paths) >= 3
    packed = "".join(path.read_text(encoding="utf-8") for path in paths)
    assert "2401:123456789" not in packed
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["receipt_count"] == 1
    assert payload["coverage"] == "complete"
    assert payload["session_binding"] == fact_receipt_session_binding(
        "2401:123456789"
    )


def test_fact_receipt_bounds_and_current_session_selector(tmp_path: Path) -> None:
    writer = FactReceiptWriter(tmp_path, max_receipts=1, max_bytes=4096)
    try:
        assert writer.enqueue(session_id="2401:one", receipt={"kind": "fast"})
        _wait_for(tmp_path / "fact_receipts" / "current_manifest.json")
        assert writer.enqueue(session_id="2401:one", receipt={"kind": "full"})
        time.sleep(0.05)
    finally:
        writer.close()

    manifest = json.loads((tmp_path / "fact_receipts" / "current_manifest.json").read_text(encoding="utf-8"))
    assert manifest["receipt_count"] == 1
    assert manifest["coverage"] == "partial"
    assert manifest["coverage_reason"] == "max_bytes_or_count"


def test_enqueue_is_put_nowait_only_and_close_full_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = fact_receipts._atomic_json  # noqa: SLF001
    worker_entered = threading.Event()
    worker_release = threading.Event()
    filesystem_threads: list[str] = []

    def blocked_atomic(path: Path, payload: dict[str, object]) -> None:
        filesystem_threads.append(threading.current_thread().name)
        if payload.get("coverage_reason") == "session_rollover_pending":
            worker_entered.set()
            assert worker_release.wait(5)
        original(path, payload)

    monkeypatch.setattr(fact_receipts, "_atomic_json", blocked_atomic)
    writer = FactReceiptWriter(tmp_path, max_receipts=1)
    assert writer.enqueue(session_id="2401:queue", receipt={"kind": "first"})
    assert worker_entered.wait(2)
    assert writer.enqueue(session_id="2401:queue", receipt={"kind": "second"})
    started = time.perf_counter()
    assert not writer.enqueue(session_id="2401:queue", receipt={"kind": "full"})
    assert time.perf_counter() - started < 0.5
    started = time.perf_counter()
    writer.close()
    assert time.perf_counter() - started < 0.5
    health = writer.health_snapshot()
    assert health["queue_full"] >= 1
    assert health["close_queue_full"] >= 1
    worker_release.set()
    _wait_for_bundle(tmp_path, "2401:queue")
    writer.close()
    assert filesystem_threads
    assert set(filesystem_threads) == {"fact-receipt-writer"}


@pytest.mark.parametrize(
    ("error", "health_key"),
    [
        (PermissionError("denied"), "permission_denied"),
        (OSError(28, "disk full"), "disk_full"),
        (OSError(5, "device error"), "write_failed"),
    ],
)
def test_worker_filesystem_errors_only_update_fixed_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
    health_key: str,
) -> None:
    def failed_atomic(_path: Path, _payload: dict[str, object]) -> None:
        assert threading.current_thread().name == "fact-receipt-writer"
        raise error

    monkeypatch.setattr(fact_receipts, "_atomic_json", failed_atomic)
    writer = FactReceiptWriter(tmp_path)
    try:
        assert writer.enqueue(session_id="2401:error", receipt={"kind": "fast"})
        for _ in range(200):
            if writer.health_snapshot().get(health_key):
                break
            time.sleep(0.01)
        else:
            raise AssertionError(f"missing writer health {health_key}")
        assert current_receipt_bundle_paths(
            tmp_path,
            expected_session_id="2401:error",
        ) == []
    finally:
        writer.close()


def test_cross_process_selector_rejects_rollover_old_missing_and_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = FactReceiptWriter(tmp_path)
    original = fact_receipts._atomic_json  # noqa: SLF001
    try:
        assert writer.enqueue(session_id="2401:old", receipt={"kind": "old"})
        _wait_for_bundle(tmp_path, "2401:old")
        entered = threading.Event()
        release = threading.Event()
        write_order: list[tuple[str, str]] = []
        new_binding = fact_receipt_session_binding("2401:new")
        assert new_binding is not None

        def paused_atomic(path: Path, payload: dict[str, object]) -> None:
            write_order.append((path.name, str(payload.get("coverage_reason") or "")))
            if (
                path.name == "current_manifest.json"
                and payload.get("session_binding") == new_binding
                and payload.get("coverage_reason") == "session_rollover_pending"
            ):
                entered.set()
                assert release.wait(5)
            original(path, payload)

        monkeypatch.setattr(fact_receipts, "_atomic_json", paused_atomic)
        assert writer.enqueue(session_id="2401:new", receipt={"kind": "new"})
        assert entered.wait(2)
        # The root manifest is still old, but an independent overlay process carries
        # the new exact snapshot session and therefore cannot select it.
        assert _child_bundle_names(tmp_path, "2401:new") == []
        release.set()
        new_paths = _wait_for_bundle(tmp_path, "2401:new")
        assert any(name.startswith("receipt-") for name in _child_bundle_names(tmp_path, "2401:new"))
        assert _child_bundle_names(tmp_path, "2401:old") == []
        assert current_receipt_bundle_paths(tmp_path) == []

        class SessionSubclass(str):
            pass

        assert current_receipt_bundle_paths(
            tmp_path,
            expected_session_id=SessionSubclass("2401:new"),
        ) == []
        assert write_order[0] == ("current_manifest.json", "session_rollover_pending")
        leaf_index = next(
            index for index, row in enumerate(write_order) if row[0].startswith("receipt-")
        )
        final_index = max(
            index
            for index, row in enumerate(write_order)
            if row == ("current_manifest.json", "")
        )
        assert 0 < leaf_index < final_index

        current_manifest = tmp_path / "fact_receipts" / "current_manifest.json"
        original_manifest = current_manifest.read_bytes()
        tampered = json.loads(original_manifest)
        tampered["session_binding"] = "0" * 64
        current_manifest.write_text(json.dumps(tampered), encoding="utf-8")
        assert current_receipt_bundle_paths(
            tmp_path,
            expected_session_id="2401:new",
        ) == []
        current_manifest.write_bytes(original_manifest)

        leaf = next(path for path in new_paths if path.name.startswith("receipt-"))
        alias = leaf.with_name("receipt-hardlink.json")
        os.link(leaf, alias)
        try:
            assert current_receipt_bundle_paths(
                tmp_path,
                expected_session_id="2401:new",
            ) == []
        finally:
            alias.unlink()
    finally:
        writer.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_cross_process_selector_rejects_fact_root_junction(tmp_path: Path) -> None:
    writer = FactReceiptWriter(tmp_path)
    try:
        assert writer.enqueue(session_id="2401:junction", receipt={"kind": "fast"})
        _wait_for_bundle(tmp_path, "2401:junction")
    finally:
        writer.close()
    root = tmp_path / "fact_receipts"
    real = tmp_path / "fact_receipts-real"
    root.rename(real)
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(root), str(real)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        real.rename(root)
        pytest.fail(f"junction setup failed: {completed.stderr}")
    try:
        assert _child_bundle_names(tmp_path, "2401:junction") == []
    finally:
        root.rmdir()
        real.rename(root)


def test_overlay_requires_one_exact_snapshot_and_summary_session() -> None:
    module = _overlay_module()
    snapshot = {
        "session_id": "2401:current",
        "ui_contract": {"context": {"session_id": "2401:current"}},
    }
    summary = {"context": {"session_id": "2401:current"}}
    assert (
        module._verified_diagnostic_session_id(snapshot, summary)
        == "2401:current"
    )
    assert module._verified_diagnostic_session_id(snapshot, None) is None
    assert module._verified_diagnostic_session_id(
        snapshot,
        {"context": {"session_id": "2401:old"}},
    ) is None
    assert module._verified_diagnostic_session_id(
        {
            "session_id": "2401:current",
            "ui_contract": {"context": {"session_id": "2401:other"}},
        },
        summary,
    ) is None


def test_fact_receipt_rejects_nested_raw_or_identity_keys(tmp_path: Path) -> None:
    writer = FactReceiptWriter(tmp_path)
    try:
        assert not writer.enqueue(
            session_id="2401:one", receipt={"parser": {"raw_packet": "never-write"}}
        )
        assert not writer.enqueue(
            session_id="2401:one", receipt={"frame": {"payload": "never-write"}}
        )
    finally:
        writer.close()


def test_completed_round_promotion_never_relabels_future_or_other_session() -> None:
    rows = [
        {"session_id": "2401:current", "action_id": 100117, "sort": 2, "round": 2, "source_kind": "state_replay"},
        {"session_id": "2401:current", "action_id": 100118, "sort": 3, "round": 3, "source_kind": "state_replay"},
        {"session_id": "2401:other", "action_id": 100119, "sort": 4, "round": 2, "source_kind": "state_replay"},
    ]
    for row in rows:
        row.update({"source_message_id": 0x0025, "result": 1, "result_field": 1})
    promoted = _promote_current_action_rows(
        rows, session_id="2401:current", action_round=3, observed_round=2
    )
    assert promoted[0]["promoted_action_round"] == 3
    assert promoted[0]["promotion_reason"] == "completed_round_state_replay"
    assert "promoted_action_round" not in promoted[1]
    assert "promoted_action_round" not in promoted[2]


class _MessageIntSubclass(int):
    pass


@pytest.mark.parametrize("bad_message", [0x0027, True, _MessageIntSubclass(0x0025)])
def test_completed_round_promotion_requires_exact_0x25_message(
    bad_message: object,
) -> None:
    row = {
        "session_id": "2401:current",
        "action_id": 100117,
        "sort": 4,
        "round": 2,
        "source_kind": "state_replay",
        "source_message_id": bad_message,
        "result": 5,
        "result_field": 1,
        "promoted_action_round": 3,
        "promotion_reason": "spoofed",
    }
    promoted = _promote_current_action_rows(
        [row],
        session_id="2401:current",
        action_round=3,
        observed_round=2,
    )
    assert "promoted_action_round" not in promoted[0]
    assert "promotion_reason" not in promoted[0]


def test_completed_round_duplicate_or_out_of_order_rows_are_not_promoted() -> None:
    rows = [
        {
            "session_id": "2401:current",
            "action_id": 100117,
            "sort": sort_id,
            "round": 2,
            "source_kind": "state_replay",
            "source_message_id": 0x0025,
            "result": 5,
            "result_field": 1,
        }
        for sort_id in (9, 8)
    ]
    promoted = _promote_current_action_rows(
        rows,
        session_id="2401:current",
        action_round=3,
        observed_round=2,
    )
    assert all("promoted_action_round" not in row for row in promoted)


def test_completed_round_cumulative_replay_promotes_only_latest_result() -> None:
    rows = [
        {
            "session_id": "2401:current",
            "action_id": action_id,
            "sort": 15,
            "round": 2,
            "source_kind": "state_replay",
            "source_message_id": 0x0025,
            "result": result,
            "result_field": result_field,
        }
        for action_id, result, result_field in (
            (100117, 11, 7),
            (100110, 1.5, 11),
        )
    ]

    promoted = _promote_current_action_rows(
        rows,
        session_id="2401:current",
        action_round=3,
        observed_round=2,
    )

    assert [row["action_id"] for row in promoted if "promoted_action_round" in row] == [
        100110
    ]


def test_completed_round_send_authority_selects_exact_current_action_set() -> None:
    rows = [
        {
            "session_id": "2401:current",
            "action_id": action_id,
            "sort": 15,
            "round": 2,
            "source_kind": "state_replay",
            "source_message_id": 0x0025,
            "result": result,
            "result_field": result_field,
        }
        for action_id, result, result_field in (
            (100117, 11, 7),
            (100110, 1.5, 11),
        )
    ]

    selected = _promote_current_action_rows(
        rows,
        session_id="2401:current",
        action_round=3,
        observed_round=2,
        current_action_ids=(100117,),
    )
    settled = _promote_current_action_rows(
        rows,
        session_id="2401:current",
        action_round=3,
        observed_round=2,
        current_action_ids=(),
    )

    assert [row["action_id"] for row in selected if "promoted_action_round" in row] == [
        100117
    ]
    assert all("promoted_action_round" not in row for row in settled)


def test_current_completed_action_ids_uses_send_window_not_cumulative_rows() -> None:
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(
            SimpleNamespace(
                kind="action", sort_id=2, session_id="2401:current", value=100117
            ),
            SimpleNamespace(
                kind="action", sort_id=9, session_id="2401:current", value=100110
            ),
        ),
        states=(
            SimpleNamespace(
                sort_id=8,
                session_id="2401:current",
                message_id=0x0025,
                round_no=1,
            ),
            SimpleNamespace(
                sort_id=15,
                session_id="2401:current",
                message_id=0x0025,
                round_no=2,
            ),
        ),
        statuses=(),
    )

    assert _current_completed_action_ids(
        events,
        session_id="2401:current",
        action_round=3,
        observed_round=2,
        phase="bidding",
    ) == (100110,)
    assert _current_completed_action_ids(
        events,
        session_id="2401:current",
        action_round=4,
        observed_round=3,
        phase="settled",
    ) == ()


class _SequenceIntSubclass(int):
    pass


def _sequence_send(
    sort_id: object,
    action_id: object,
    *,
    session_id: object = "2401:current",
) -> SimpleNamespace:
    return SimpleNamespace(
        kind="action",
        sort_id=sort_id,
        session_id=session_id,
        value=action_id,
    )


def _sequence_state(
    sort_id: object,
    round_no: object,
    *,
    session_id: object = "2401:current",
) -> SimpleNamespace:
    return SimpleNamespace(
        sort_id=sort_id,
        session_id=session_id,
        message_id=0x0025,
        round_no=round_no,
    )


def _sequence_current_action_ids(
    sends: list[SimpleNamespace],
    states: list[SimpleNamespace],
) -> tuple[int, ...]:
    return _current_completed_action_ids(
        FatbeansCaptureEvents(
            packets=(),
            frames=(),
            sends=tuple(sends),
            states=tuple(states),
            statuses=(),
        ),
        session_id="2401:current",
        action_round=3,
        observed_round=2,
        phase="bidding",
    )


def test_current_completed_action_ids_preserves_proven_multi_action_wire_order() -> None:
    assert _sequence_current_action_ids(
        [_sequence_send(9, 100117), _sequence_send(10, 100110)],
        [_sequence_state(8, 1), _sequence_state(15, 2)],
    ) == (100117, 100110)


@pytest.mark.parametrize(
    ("sends", "states"),
    [
        (
            [_sequence_send(9, 100117), _sequence_send(9, 100110)],
            [_sequence_state(8, 1), _sequence_state(15, 2)],
        ),
        (
            [_sequence_send(9, 100110), _sequence_send(10, 100110)],
            [_sequence_state(8, 1), _sequence_state(15, 2)],
        ),
        (
            [_sequence_send(10, 100110), _sequence_send(9, 100117)],
            [_sequence_state(8, 1), _sequence_state(15, 2)],
        ),
        (
            [_sequence_send(True, 100110)],
            [_sequence_state(8, 1), _sequence_state(15, 2)],
        ),
        (
            [_sequence_send(_SequenceIntSubclass(9), 100110)],
            [_sequence_state(8, 1), _sequence_state(15, 2)],
        ),
        (
            [_sequence_send(-1, 100110)],
            [_sequence_state(8, 1), _sequence_state(15, 2)],
        ),
        (
            [_sequence_send(9, 100110, session_id="2401:other")],
            [_sequence_state(8, 1), _sequence_state(15, 2)],
        ),
        (
            [_sequence_send(9, 100110)],
            [_sequence_state(8, 1), _sequence_state(15, 2), _sequence_state(15, 2)],
        ),
        (
            [_sequence_send(9, 100110)],
            [_sequence_state(8, 1), _sequence_state(7, 2)],
        ),
        (
            [_sequence_send(9, 100110)],
            [_sequence_state(8, 1), _sequence_state(12, 3), _sequence_state(15, 2)],
        ),
        (
            [_sequence_send(9, 100110)],
            [_sequence_state(8, 1), _sequence_state(15, 2), _sequence_state(20, 3)],
        ),
        (
            [_sequence_send(9, 100110)],
            [
                _sequence_state(8, 1),
                _sequence_state(10, 2, session_id="2401:other"),
                _sequence_state(15, 2),
            ],
        ),
    ],
    ids=(
        "same-sort-distinct-send",
        "duplicate-action-send",
        "send-sort-regression",
        "bool-send-sort",
        "subclass-send-sort",
        "negative-send-sort",
        "cross-session-send-in-window",
        "duplicate-completed-state",
        "state-sort-regression",
        "future-round-before-current",
        "future-round-after-current",
        "cross-session-state-in-window",
    ),
)
def test_current_completed_action_ids_rejects_unproven_sequence_lineage(
    sends: list[SimpleNamespace],
    states: list[SimpleNamespace],
) -> None:
    assert _sequence_current_action_ids(sends, states) == ()


def test_send_authority_rejects_duplicate_current_result_rows_atomically() -> None:
    rows = [
        {
            "session_id": "2401:current",
            "action_id": 100117,
            "sort": sort_id,
            "round": 2,
            "source_kind": "state_replay",
            "source_message_id": 0x0025,
            "result": result,
            "result_field": 7,
        }
        for sort_id, result in ((15, 11), (16, 12))
    ]

    promoted = _promote_current_action_rows(
        rows,
        session_id="2401:current",
        action_round=3,
        observed_round=2,
        current_action_ids=(100117,),
    )

    assert all("promoted_action_round" not in row for row in promoted)


class _ResultIntSubclass(int):
    pass


class _ResultFloatSubclass(float):
    pass


class _ResultListSubclass(list):
    pass


class _ResultDictSubclass(dict):
    pass


def _completed_result_base(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "session_id": "2401:current",
        "action_id": 100117,
        "sort": 9,
        "round": 2,
        "source_kind": "state_replay",
        "source_message_id": 0x0025,
        "result_field": 7,
    }
    row.update(updates)
    return row


def _valid_observed_item() -> dict[str, object]:
    return {
        "local_index": 14,
        "runtime_id": 1_000_000_001,
        "item_id": 1072001,
        "item_name": "fixture-item",
        "quality": 2,
        "value": 401,
        "shape_code": 11,
        "cells": 1,
    }


def _detail_result_row(
    action_id: int, item: dict[str, object]
) -> dict[str, object]:
    return _completed_result_base(
        action_id=action_id,
        result=None,
        result_field=None,
        revealed_items=1,
        revealed_items_detail=[item],
    )


def _promotion_and_receipt_accept(row: dict[str, object]) -> tuple[bool, int]:
    module = _monitor_module()
    promoted = _promote_current_action_rows(
        [row],
        session_id="2401:current",
        action_round=3,
        observed_round=2,
    )
    marker = promoted[0].get("promoted_action_round") == 3
    accepted = module._current_action_promotion(
        {
            "session_id": "2401:current",
            "action_round": 3,
            "observed_round": 2,
            "action_result_rows": promoted,
        }
    )["accepted_count"]
    return marker, accepted


@pytest.mark.parametrize(
    "updates",
    [
        {},
        {"result": None},
        {"result": True},
        {"result": _ResultIntSubclass(11)},
        {"result": _ResultFloatSubclass(1.5)},
        {"result": Decimal("11")},
        {"result": float("nan")},
        {"result": float("inf")},
        {"result": float("-inf")},
        {"result": -1},
        {"result": 1 << 63},
        {"result": None, "revealed_items": 0, "revealed_items_detail": []},
        {"result": None, "revealed_items": 1, "revealed_items_detail": "item"},
        {
            "result": None,
            "revealed_items": 1,
            "revealed_items_detail": _ResultListSubclass([_valid_observed_item()]),
        },
        {
            "result": None,
            "revealed_items": 1,
            "revealed_items_detail": [_ResultDictSubclass(_valid_observed_item())],
        },
        {
            "result": None,
            "revealed_items": 1,
            "revealed_items_detail": [
                {**_valid_observed_item(), "runtime_id": True}
            ],
        },
        {
            "result": None,
            "revealed_items": 2,
            "revealed_items_detail": [_valid_observed_item()],
        },
        {"action_id": 100169, "result": 0.0, "result_field": 11},
        {"action_id": 7, "result": 5},
        {"action_id": 100117, "result": 5.0},
        {"action_id": 100110, "result": 1},
        {"action_id": 100128, "result": 1},
        {
            "action_id": 100151,
            "result": None,
            "revealed_items": 1,
            "revealed_items_detail": [
                {**_valid_observed_item(), "shape_code": None, "cells": None}
            ],
        },
        {
            "action_id": 100136,
            "result": None,
            "revealed_items": 1,
            "revealed_items_detail": [
                {**_valid_observed_item(), "quality": None}
            ],
        },
        {
            "action_id": 100163,
            "result": None,
            "revealed_items": 1,
            "revealed_items_detail": [{**_valid_observed_item(), "value": None}],
        },
        {
            "action_id": 100162,
            "result": None,
            "revealed_items": 1,
            "revealed_items_detail": [{**_valid_observed_item(), "cells": None}],
        },
        {
            "action_id": 100128,
            "result": None,
            "revealed_items": 1,
            "revealed_items_detail": [
                {**_valid_observed_item(), "item_id": None}
            ],
        },
    ],
)
def test_completed_round_requires_real_strict_result(
    updates: dict[str, object],
) -> None:
    assert _promotion_and_receipt_accept(_completed_result_base(**updates)) == (
        False,
        0,
    )


@pytest.mark.parametrize(
    "row",
    [
        _completed_result_base(result=11),
        _completed_result_base(action_id=100110, result=1.5, result_field=11),
        _completed_result_base(action_id=100169, result=1.5, result_field=11),
        _completed_result_base(action_id=100117, result=0),
        _completed_result_base(
            action_id=100128,
            result=None,
            result_field=None,
            revealed_items=1,
            revealed_items_detail=[_valid_observed_item()],
        ),
        _completed_result_base(
            action_id=100128,
            result=None,
            result_field=None,
            revealed_items=1,
            observed_items=[_valid_observed_item()],
        ),
        _detail_result_row(
            100151,
            {
                **_valid_observed_item(),
                "item_id": None,
                "quality": None,
                "value": None,
            },
        ),
        _detail_result_row(
            100136,
            {
                **_valid_observed_item(),
                "item_id": None,
                "value": None,
                "shape_code": None,
                "cells": None,
            },
        ),
        _detail_result_row(
            100163,
            {
                **_valid_observed_item(),
                "item_id": None,
                "quality": None,
                "shape_code": None,
                "cells": None,
            },
        ),
        _detail_result_row(
            100162,
            {
                **_valid_observed_item(),
                "item_id": None,
                "quality": None,
                "value": None,
                "shape_code": None,
            },
        ),
    ],
)
def test_completed_round_real_scalar_or_item_result_is_accepted(
    row: dict[str, object],
) -> None:
    assert _promotion_and_receipt_accept(row) == (True, 1)


def _second_observed_item() -> dict[str, object]:
    return {
        **_valid_observed_item(),
        "local_index": 15,
        "runtime_id": 1_000_000_002,
        "item_id": 1072002,
        "value": 402,
        "shape_code": 21,
        "cells": 2,
    }


@pytest.mark.parametrize(
    "row",
    [
        _completed_result_base(
            action_id=100128,
            result=None,
            result_field=None,
            revealed_items=1,
            revealed_items_detail=[_valid_observed_item()],
            observed_items=[],
        ),
        _completed_result_base(
            action_id=100128,
            result=None,
            result_field=None,
            revealed_items=1,
            revealed_items_detail=[],
            observed_items=[_valid_observed_item()],
        ),
        _completed_result_base(
            action_id=100128,
            result=None,
            result_field=None,
            revealed_items=0,
            revealed_items_detail=[],
            observed_items=[],
        ),
        _completed_result_base(
            action_id=100128,
            result=3,
            result_field=14,
            revealed_items=2,
            revealed_items_detail=[_valid_observed_item(), _second_observed_item()],
            observed_items=[_second_observed_item(), _valid_observed_item()],
        ),
        _completed_result_base(
            action_id=100128,
            result=2,
            result_field=14,
            revealed_items=2,
            revealed_items_detail=[_valid_observed_item(), _valid_observed_item()],
        ),
        _completed_result_base(
            action_id=100128,
            result=1,
            result_field=14,
            revealed_items=1,
            revealed_items_detail=[_valid_observed_item()],
            observed_items=[{**_valid_observed_item(), "quality": 3}],
        ),
        _completed_result_base(
            action_id=100128,
            result=1,
            result_field=14,
            revealed_items=True,
            revealed_items_detail=[_valid_observed_item()],
        ),
        _completed_result_base(
            action_id=100128,
            result=2,
            result_field=14,
            revealed_items=1,
            revealed_items_detail=[_valid_observed_item()],
        ),
        _completed_result_base(
            action_id=100136,
            result=1,
            result_field=14,
            revealed_items=1,
            revealed_items_detail=[
                {
                    **_valid_observed_item(),
                    "item_id": None,
                    "value": None,
                    "shape_code": None,
                    "cells": None,
                }
            ],
        ),
        _completed_result_base(
            action_id=100117,
            result=11,
            revealed_items=1,
            revealed_items_detail=[_valid_observed_item()],
        ),
    ],
)
def test_completed_round_rejects_mixed_or_inconsistent_result_representations(
    row: dict[str, object],
) -> None:
    assert _promotion_and_receipt_accept(row) == (False, 0)


@pytest.mark.parametrize(
    "row",
    [
        _completed_result_base(
            action_id=100128,
            result=None,
            result_field=None,
            revealed_items_detail=[_valid_observed_item()],
        ),
        _completed_result_base(
            action_id=100128,
            result=None,
            result_field=None,
            observed_items=[_valid_observed_item()],
        ),
        _completed_result_base(
            action_id=100128,
            result=1,
            result_field=14,
            revealed_items=1,
            revealed_items_detail=[_valid_observed_item()],
            observed_items=[_valid_observed_item()],
        ),
        _completed_result_base(
            action_id=100128,
            result=3,
            result_field=14,
            revealed_items=2,
            revealed_items_detail=[_valid_observed_item(), _second_observed_item()],
            observed_items=[_valid_observed_item(), _second_observed_item()],
        ),
        _completed_result_base(
            action_id=100117,
            result=11,
            revealed_items=0,
            revealed_items_detail=[],
        ),
        _completed_result_base(
            action_id=100117,
            result=11,
            revealed_items=0,
            revealed_items_detail=[],
            observed_items=[],
        ),
    ],
)
def test_completed_round_accepts_single_or_exactly_matching_representations(
    row: dict[str, object],
) -> None:
    assert _promotion_and_receipt_accept(row) == (True, 1)


def test_monitor_receipt_counts_only_locally_promoted_exact_row() -> None:
    module = _monitor_module()
    base = {
        "session_id": "2401:current",
        "action_id": 100117,
        "round": 2,
        "source_kind": "state_replay",
        "source_message_id": 0x0025,
        "result": 5,
        "result_field": 1,
    }
    rows = _promote_current_action_rows(
        [{**base, "sort": 9}, {**base, "sort": 8}],
        session_id="2401:current",
        action_round=3,
        observed_round=2,
    )
    receipt = module._monitor_fact_receipt(
        {
            "session_id": "2401:current",
            "round": 3,
            "action_round": 3,
            "observed_round": 2,
            "action_result_rows": rows,
        },
        row_generation=1,
        source_update_seq=9,
        snapshot_mode="fast",
    )
    assert receipt["promotion"]["accepted_count"] == 0
    assert receipt["promotion"]["reason"] == "no_current_action_promotion"
    assert len(receipt["promotion"]["action_set_hash"]) == 64


def test_monitor_receipt_enqueues_a_support_safe_leaf(tmp_path: Path) -> None:
    module = _monitor_module()
    artifact = {
        "session_id": "2401:current", "action_round": 3, "observed_round": 2,
        "round": 3, "packets": 2, "frames": 2, "states": 1,
        "action_result_rows": [{
            "session_id": "2401:current", "action_id": 100117, "sort": 4, "round": 2,
            "source_kind": "state_replay", "source_message_id": 0x0025,
            "result": 5, "result_field": 1, "promoted_action_round": 3,
            "promotion_reason": "completed_round_state_replay",
        }],
        "skill_reveal_rows": [], "public_info_rows": [], "structured_ref_inputs": {},
        "v3_practical_bid_rows": [], "category_grid_items": [], "ui_contract": {}, "panel": {},
    }
    receipt = module._monitor_fact_receipt(artifact, row_generation=1, source_update_seq=4, snapshot_mode="fast")
    writer = FactReceiptWriter(tmp_path)
    try:
        assert writer.enqueue(session_id="2401:current", receipt=receipt)
        manifest = tmp_path / "fact_receipts" / "current_manifest.json"
        _wait_for(manifest)
    finally:
        writer.close()
    paths = current_receipt_bundle_paths(
        tmp_path,
        expected_session_id="2401:current",
    )
    assert any(path.name.startswith("receipt-") for path in paths)
    leaf = next(path for path in paths if path.name.startswith("receipt-"))
    payload = json.loads(leaf.read_text(encoding="utf-8"))
    assert payload["promotion"]["accepted_count"] == 1
    assert "packet" not in json.dumps(payload, sort_keys=True)


def test_ahmed_completed_replay_binding_accepts_only_immediate_current_round() -> None:
    value = 2.5
    raw_hex = struct.pack("<f", value).hex()
    raw = {
        "action_id": 100110,
        "result": value,
        "result_field": 11,
        "result_wire_type": "float32",
        "result_raw_hex": raw_hex,
        "session_id": "2401:42",
        "round": 2,
        "source_message_id": 0x0025,
        "source_kind": "state_replay",
        "sort": 9,
        "promoted_action_round": 3,
        "promotion_reason": "completed_round_state_replay",
    }
    mirror = {key: str(value_) for key, value_ in raw.items()}
    mirror["source_kind"] = "state_replay"
    mirror["promotion_reason"] = "completed_round_state_replay"
    root_sig = engine._ahmed_q1_q5_action_identity_signature(  # noqa: SLF001
        raw, ui_mirror=False, session_id="2401:42", runtime_epoch=42,
        observed_round=2, action_round=3,
    )
    mirror_sig = engine._ahmed_q1_q5_action_identity_signature(  # noqa: SLF001
        mirror, ui_mirror=True, session_id="2401:42", runtime_epoch=42,
        observed_round=2, action_round=3,
    )
    assert root_sig is not None and root_sig == mirror_sig
    assert engine._ahmed_q1_q5_action_identity_signature(  # noqa: SLF001
        raw, ui_mirror=False, session_id="2401:42", runtime_epoch=42,
        observed_round=2, action_round=4,
    ) is None
