from __future__ import annotations

import json
import sys
import threading
import time
import gzip
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import public_stats  # noqa: E402
import sample_collector_server as scs  # noqa: E402
import sample_dashboard  # noqa: E402
import my_records  # noqa: E402


def _sample() -> dict:
    return {
        "map_id": 2406,
        "hero": "aisha",
        "final_quality_counts": "q1=1;q6=1",
        "final_quality_cells": "q1=2;q6=6",
        "known_value_sum": 1000,
        "session_hash": "memory-guard",
    }


def _fresh_build_state(monkeypatch, out: Path) -> None:
    monkeypatch.setattr(scs, "_OUT", out)
    monkeypatch.setattr(scs, "_DASH_CACHE", {
        "key": None, "plain": None, "gz": None, "etag": None, "built_mono": 0.0,
    })
    monkeypatch.setattr(scs, "_PUB_CACHE", {
        "key": None, "plain": None, "gz": None, "etag": None,
        "built_mono": 0.0, "built_wall": 0.0,
    })
    monkeypatch.setattr(scs, "_DASH_LOCK", threading.Lock())
    monkeypatch.setattr(scs, "_PUB_LOCK", threading.Lock())
    monkeypatch.setattr(scs, "_HEAVY_SCAN_LOCK", threading.Lock())
    monkeypatch.setattr(scs, "_QUARANTINE_SUMMARY_CACHE", {
        "key": None, "value": None, "built_mono": 0.0,
    })
    monkeypatch.setattr(scs, "_QUARANTINE_SUMMARY_LOCK", threading.Lock())


def test_startup_history_load_streams_instead_of_reading_whole_files(tmp_path, monkeypatch):
    out = tmp_path / "samples.jsonl"
    quarantine = tmp_path / "samples_quarantine.jsonl"
    out.write_text(
        json.dumps({"session_hash": "main-1", "map_id": 2406}) + "\n"
        + "{broken\n"
        + json.dumps({"session_hash": "main-2", "map_id": 2501}) + "\n",
        encoding="utf-8",
    )
    quarantine.write_text(json.dumps({"session_hash": "quarantine-1"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(scs, "_OUT", out)
    monkeypatch.setattr(scs, "_SEEN", set())
    monkeypatch.setattr(scs, "_QUAR_SEEN", set())
    monkeypatch.setattr(scs, "_MAP_COUNT", scs.Counter())

    real_read_text = Path.read_text

    def reject_whole_file_read(path: Path, *args, **kwargs):
        if path in {out, quarantine}:
            raise AssertionError("startup must stream production jsonl")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_whole_file_read)
    scs._load_existing()

    assert scs._SEEN == {"main-1", "main-2"}
    assert scs._QUAR_SEEN == {"quarantine-1"}
    assert scs._MAP_COUNT == {2406: 1, 2501: 1}


def test_public_and_dashboard_cannot_expand_full_dataset_concurrently(tmp_path, monkeypatch):
    out = tmp_path / "samples.jsonl"
    out.write_text(json.dumps(_sample()) + "\n", encoding="utf-8")
    _fresh_build_state(monkeypatch, out)

    state_lock = threading.Lock()
    first_entered = threading.Event()
    release_first = threading.Event()
    state = {"active": 0, "max_active": 0, "calls": 0}

    def guarded_load(_path, **_kwargs):
        with state_lock:
            state["active"] += 1
            state["calls"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            first_entered.set()
        assert release_first.wait(timeout=3), "test did not release first heavy loader"
        with state_lock:
            state["active"] -= 1
        return [_sample()]

    monkeypatch.setattr(public_stats, "load_samples", guarded_load)
    monkeypatch.setattr(sample_dashboard, "_load_samples", guarded_load)

    start = threading.Barrier(3)
    errors: list[BaseException] = []

    def run(build):
        try:
            start.wait(timeout=2)
            build()
        except BaseException as exc:  # noqa: BLE001 - propagate thread failures to the test
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=(scs._public_bodies,)),
        threading.Thread(target=run, args=(scs._dashboard_bodies,)),
    ]
    for thread in threads:
        thread.start()
    start.wait(timeout=2)
    assert first_entered.wait(timeout=2), "neither heavy route reached its loader"
    time.sleep(0.1)

    # 旧实现的两个独立路由锁会 calls=2/max_active=2；新门让第二条路径立即 busy，不排队。
    assert state["calls"] == 1
    assert state["max_active"] == 1

    release_first.set()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert len(errors) == 1 and isinstance(errors[0], scs.HeavyOperationBusy)
    errors.clear()
    scs._public_bodies()
    scs._dashboard_bodies()
    assert state["calls"] == 2
    assert state["max_active"] == 1
    assert scs._PUB_CACHE["plain"] is not None
    assert scs._DASH_CACHE["plain"] is not None


def test_concurrency_probe_positive_control_detects_independent_route_locks():
    """正向控制：若退回各路由独立锁，同一探针必须确实观测到并行峰值 2。"""
    locks = [threading.Lock(), threading.Lock()]
    entered = threading.Barrier(3)
    release = threading.Event()
    guard = threading.Lock()
    state = {"active": 0, "max_active": 0}

    def run(lock):
        with lock:
            with guard:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            entered.wait(timeout=2)
            release.wait(timeout=2)
            with guard:
                state["active"] -= 1

    threads = [threading.Thread(target=run, args=(lock,)) for lock in locks]
    for thread in threads:
        thread.start()
    entered.wait(timeout=2)
    release.set()
    for thread in threads:
        thread.join(timeout=2)
    assert state["max_active"] == 2


def _utc_ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


def test_server_windows_are_strict_inclusive_and_beijing_based(tmp_path):
    out = tmp_path / "samples.jsonl"
    records = [
        {**_sample(), "session_hash": "before", "client_ts": _utc_ts("2026-07-18T15:59:59")},
        {**_sample(), "session_hash": "start", "client_ts": _utc_ts("2026-07-18T16:00:00")},
        {**_sample(), "session_hash": "end", "client_ts": _utc_ts("2026-07-19T15:59:59")},
        {**_sample(), "session_hash": "after", "client_ts": _utc_ts("2026-07-19T16:00:00")},
        {**_sample(), "session_hash": "fallback", "created_at": _utc_ts("2026-07-19T00:00:00")},
        {**_sample(), "session_hash": "unknown"},
    ]
    out.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")

    public = public_stats.load_samples(out, start=date(2026, 7, 19), end=date(2026, 7, 19))
    assert [row["session_hash"] for row in public] == ["start", "end"]
    assert public.last_unknown_date_count == 2  # public 只认 client_ts；fallback 也必须排除。

    dashboard = sample_dashboard._load_samples(
        out, start=date(2026, 7, 19), end=date(2026, 7, 19),
    )
    assert [row["session_hash"] for row in dashboard] == ["start", "end", "fallback"]
    assert dashboard.last_unknown_date_count == 1


def test_dashboard_window_parser_rejects_malformed_or_unbounded_ranges(monkeypatch):
    monkeypatch.setattr(scs, "_today_shanghai", lambda **_: date(2026, 7, 20))
    default = scs._parse_dashboard_window("/dashboard")
    assert default["start"] == date(2026, 6, 21) and default["end"] == date(2026, 7, 20)
    assert scs._parse_dashboard_window("/dashboard?window=7")["start"] == date(2026, 7, 14)
    assert scs._parse_dashboard_window("/dashboard?start=2026-07-01&end=2026-07-20")["mode"] == "custom"
    for path in (
        "/dashboard?window=365", "/dashboard?window=7&window=30",
        "/dashboard?start=20260701&end=2026-07-20",
        "/dashboard?start=2026-07-20&end=2026-07-01",
        "/dashboard?start=2025-01-01&end=2026-07-20",
        "/dashboard?x=1", "/dashboard?start=2026-07-01",
    ):
        try:
            scs._parse_dashboard_window(path)
        except scs.InvalidDashboardWindow:
            pass
        else:
            raise AssertionError(f"malformed window accepted: {path}")


def test_old_history_growth_does_not_change_window_payload(tmp_path):
    recent = {**_sample(), "session_hash": "recent", "client_ts": _utc_ts("2026-07-19T01:00:00")}
    old = {**_sample(), "session_hash": "old", "client_ts": _utc_ts("2024-01-01T01:00:00")}
    out = tmp_path / "samples.jsonl"

    def aggregate(old_copies: int):
        rows = [{**old, "session_hash": f"old-{i}"} for i in range(old_copies)] + [recent]
        out.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        source = public_stats.load_samples(
            out, start=date(2026, 7, 1), end=date(2026, 7, 31),
        )
        result = public_stats.aggregate_public(source)
        return result, source.last_matched_count

    small, small_hits = aggregate(1)
    large, large_hits = aggregate(10)
    assert small_hits == large_hits == 1
    assert small == large


def test_streaming_all_history_aggregate_matches_list_contract(tmp_path):
    rows = [
        {**_sample(), "session_hash": "all-a", "client_ts": _utc_ts("2026-07-18T01:00:00")},
        {**_sample(), "session_hash": "all-b", "client_ts": _utc_ts("2026-07-19T01:00:00")},
    ]
    out = tmp_path / "samples.jsonl"
    out.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert public_stats.aggregate_public(public_stats.load_samples(out)) == public_stats.aggregate_public(rows)


def test_public_repeated_scans_stop_at_one_snapshot_when_file_is_appended(tmp_path):
    initial = {
        **_sample(),
        "session_hash": "snapshot-initial",
        "client_ts": _utc_ts("2026-07-19T01:00:00"),
    }
    appended = {
        **_sample(),
        "session_hash": "snapshot-appended",
        "client_ts": _utc_ts("2026-07-19T02:00:00"),
    }
    out = tmp_path / "samples.jsonl"
    out.write_text(json.dumps(initial) + "\n", encoding="utf-8")
    snapshot_bytes = out.stat().st_size
    first_pass_finished = threading.Event()
    append_finished = threading.Event()

    class AppendBetweenPasses(public_stats.JsonlSampleSource):
        def __iter__(self):
            yield from super().__iter__()
            if self.passes == 1:
                first_pass_finished.set()
                assert append_finished.wait(timeout=2)

    def append_row():
        assert first_pass_finished.wait(timeout=2)
        with out.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(appended) + "\n")
        append_finished.set()

    writer = threading.Thread(target=append_row)
    writer.start()
    source = AppendBetweenPasses(out, snapshot_bytes=snapshot_bytes)
    frozen = public_stats.aggregate_public(source)
    writer.join(timeout=3)
    assert not writer.is_alive()

    assert source.passes > 1
    assert frozen == public_stats.aggregate_public([initial])
    assert public_stats.aggregate_public(public_stats.load_samples(out)) == public_stats.aggregate_public(
        [initial, appended]
    )


def test_dashboard_cache_never_crosses_server_windows(tmp_path, monkeypatch):
    out = tmp_path / "samples.jsonl"
    out.write_text(json.dumps({
        **_sample(), "session_hash": "window-a", "client_ts": _utc_ts("2026-07-01T01:00:00"),
    }) + "\n", encoding="utf-8")
    _fresh_build_state(monkeypatch, out)
    monkeypatch.setattr(scs, "DASH_REBUILD_INTERVAL", 3600.0)
    july = {"mode": "custom", "start": date(2026, 7, 1), "end": date(2026, 7, 1),
            "label": "custom", "schema": scs._CACHE_SCHEMA}
    august = {"mode": "custom", "start": date(2026, 8, 1), "end": date(2026, 8, 1),
              "label": "custom", "schema": scs._CACHE_SCHEMA}
    july_html, _, july_etag = scs._dashboard_bodies(july)
    august_html, _, august_etag = scs._dashboard_bodies(august)
    assert july_etag != august_etag
    assert b'"matched_rows": 1' in july_html
    assert b'"matched_rows": 0' in august_html


def test_quarantine_summary_is_aggregate_only_and_keeps_official_total_separate(
    tmp_path, monkeypatch,
):
    out = tmp_path / "samples.jsonl"
    out.write_text(json.dumps(_sample()) + "\n", encoding="utf-8")
    quarantine = tmp_path / "samples_quarantine.jsonl"
    rows = [
        {
            **_sample(),
            "session_hash": "source-pending",
            "warehouse_source_qualification": "postupdate_unknown_warehouse_source",
            "official_distribution_eligible": False,
        },
        {**_sample(), "session_hash": "incomplete", "settlement_incomplete": True},
        {
            **_sample(),
            "session_hash": "shortfall",
            "total_item_count": 10,
            "inventory_count": 9,
        },
        {**_sample(), "session_hash": "invalid-value", "known_value_sum": 0},
        {
            **_sample(),
            "session_hash": "catalog-value-incomplete",
            "catalog_value_complete": False,
        },
    ]
    quarantine.write_text(
        "".join(json.dumps(row) + "\n" for row in rows) + "{broken\n",
        encoding="utf-8",
    )
    _fresh_build_state(monkeypatch, out)
    monkeypatch.setattr(scs, "_MAP_COUNT", scs.Counter({2406: 1}))

    summary = scs._quarantine_summary()
    assert summary == {
        "total": 5,
        "warehouse_source_pending": 1,
        "update606_map_unopened": 0,
        "settlement_incomplete": 1,
        "catalog_value_incomplete": 1,
        "declared_shortfall": 1,
        "invalid_value": 1,
        "other": 0,
        "unreadable": 1,
        "bytes": quarantine.stat().st_size,
    }
    payload = scs._stats_payload()
    assert payload["total"] == payload["official_total"] == 1
    assert payload["quarantine"] == summary
    assert "session_hash" not in json.dumps(payload)
    monkeypatch.setenv("BIDKING_DISABLE_PLAYER_CUSTOM_WAREHOUSE_QUALIFICATION", "1")
    monkeypatch.setattr(scs, "_QUARANTINE_SUMMARY_CACHE", {
        "key": None, "value": None, "built_mono": 0.0,
    })
    assert scs._quarantine_summary()["warehouse_source_pending"] == 1


def test_quarantine_summary_first_build_is_cached_without_a_second_scan(
    tmp_path, monkeypatch,
):
    out = tmp_path / "samples.jsonl"
    quarantine = tmp_path / "samples_quarantine.jsonl"
    quarantine.write_text(json.dumps({
        **_sample(),
        "server_quarantine_reason": "warehouse_source_pending",
    }) + "\n", encoding="utf-8")
    _fresh_build_state(monkeypatch, out)
    clock = [1_000.0]
    monkeypatch.setattr(scs.time, "monotonic", lambda: clock[0])
    real_build = scs._build_quarantine_summary
    build_calls = []

    def counted_build(path, key):
        build_calls.append(key)
        return real_build(path, key)

    monkeypatch.setattr(scs, "_build_quarantine_summary", counted_build)

    first = scs._quarantine_summary()
    second = scs._quarantine_summary()

    assert first == second
    assert first["total"] == first["warehouse_source_pending"] == 1
    assert len(build_calls) == 1
    assert scs._QUARANTINE_SUMMARY_CACHE["built_mono"] == 1_000.0


def test_quarantine_append_inside_ttl_reuses_old_aggregate_until_explicit_refresh(
    tmp_path, monkeypatch,
):
    out = tmp_path / "samples.jsonl"
    quarantine = tmp_path / "samples_quarantine.jsonl"
    first_row = {
        **_sample(),
        "session_hash": "ttl-1",
        "server_quarantine_reason": "warehouse_source_pending",
    }
    quarantine.write_text(json.dumps(first_row) + "\n", encoding="utf-8")
    _fresh_build_state(monkeypatch, out)
    monkeypatch.setattr(scs, "QUARANTINE_SUMMARY_TTL", 300.0)
    clock = [1_000.0]
    monkeypatch.setattr(scs.time, "monotonic", lambda: clock[0])
    real_build = scs._build_quarantine_summary
    build_calls = []

    def counted_build(path, key):
        build_calls.append(key)
        return real_build(path, key)

    monkeypatch.setattr(scs, "_build_quarantine_summary", counted_build)
    assert scs._quarantine_summary()["total"] == 1

    with quarantine.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            **first_row,
            "session_hash": "ttl-2",
        }) + "\n")
    clock[0] = 1_299.9

    assert scs._quarantine_summary()["total"] == 1
    assert scs._stats_payload()["quarantine"]["total"] == 1
    assert len(build_calls) == 1

    refreshed = scs._quarantine_summary(refresh=True)
    assert refreshed["total"] == 2
    assert scs._quarantine_summary()["total"] == 2
    assert len(build_calls) == 2


def test_quarantine_summary_rebuilds_exactly_once_after_ttl(
    tmp_path, monkeypatch,
):
    out = tmp_path / "samples.jsonl"
    quarantine = tmp_path / "samples_quarantine.jsonl"
    quarantine.write_text(json.dumps({
        **_sample(),
        "session_hash": "expired-1",
        "server_quarantine_reason": "settlement_incomplete",
    }) + "\n", encoding="utf-8")
    _fresh_build_state(monkeypatch, out)
    monkeypatch.setattr(scs, "QUARANTINE_SUMMARY_TTL", 10.0)
    clock = [100.0]
    monkeypatch.setattr(scs.time, "monotonic", lambda: clock[0])
    real_build = scs._build_quarantine_summary
    build_calls = []

    def counted_build(path, key):
        build_calls.append(key)
        return real_build(path, key)

    monkeypatch.setattr(scs, "_build_quarantine_summary", counted_build)
    assert scs._quarantine_summary()["total"] == 1
    with quarantine.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            **_sample(),
            "session_hash": "expired-2",
            "server_quarantine_reason": "invalid_value",
        }) + "\n")

    clock[0] = 110.0
    assert scs._quarantine_summary()["total"] == 2
    assert scs._quarantine_summary()["total"] == 2
    assert len(build_calls) == 2


def test_quarantine_summary_expiry_is_single_flight_for_concurrent_callers(
    tmp_path, monkeypatch,
):
    out = tmp_path / "samples.jsonl"
    quarantine = tmp_path / "samples_quarantine.jsonl"
    quarantine.write_text(json.dumps({
        **_sample(),
        "session_hash": "single-flight-1",
        "server_quarantine_reason": "warehouse_source_pending",
    }) + "\n", encoding="utf-8")
    _fresh_build_state(monkeypatch, out)
    monkeypatch.setattr(scs, "QUARANTINE_SUMMARY_TTL", 10.0)
    clock = [100.0]
    monkeypatch.setattr(scs.time, "monotonic", lambda: clock[0])
    assert scs._quarantine_summary()["total"] == 1
    with quarantine.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            **_sample(),
            "session_hash": "single-flight-2",
            "server_quarantine_reason": "warehouse_source_pending",
        }) + "\n")
    clock[0] = 110.0
    real_build = scs._build_quarantine_summary
    build_entered = threading.Event()
    release_build = threading.Event()
    build_calls = []

    def slow_build(path, key):
        build_calls.append(key)
        build_entered.set()
        assert release_build.wait(timeout=3)
        return real_build(path, key)

    monkeypatch.setattr(scs, "_build_quarantine_summary", slow_build)
    results = []
    errors = []

    def load_summary():
        try:
            results.append(scs._quarantine_summary())
        except Exception as exc:  # noqa: BLE001 - thread failures must reach the assertion
            errors.append(exc)

    first = threading.Thread(target=load_summary)
    first.start()
    assert build_entered.wait(timeout=3)
    followers = [threading.Thread(target=load_summary) for _ in range(4)]
    for thread in followers:
        thread.start()
    release_build.set()
    for thread in [first, *followers]:
        thread.join(timeout=3)

    assert not errors
    assert all(not thread.is_alive() for thread in [first, *followers])
    assert len(results) == 5
    assert {result["total"] for result in results} == {2}
    assert len(build_calls) == 1


def test_quarantine_summary_concurrency_positive_control_detects_missing_lock(
    tmp_path, monkeypatch,
):
    class NoopLock:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    out = tmp_path / "samples.jsonl"
    quarantine = tmp_path / "samples_quarantine.jsonl"
    quarantine.write_text(json.dumps({
        **_sample(),
        "session_hash": "positive-control",
        "server_quarantine_reason": "warehouse_source_pending",
    }) + "\n", encoding="utf-8")
    _fresh_build_state(monkeypatch, out)
    monkeypatch.setattr(scs, "QUARANTINE_SUMMARY_TTL", 10.0)
    clock = [100.0]
    monkeypatch.setattr(scs.time, "monotonic", lambda: clock[0])
    assert scs._quarantine_summary()["total"] == 1
    clock[0] = 110.0
    monkeypatch.setattr(scs, "_QUARANTINE_SUMMARY_LOCK", NoopLock())
    real_build = scs._build_quarantine_summary
    both_builders = threading.Barrier(2)
    build_calls = []

    def racing_build(path, key):
        build_calls.append(key)
        both_builders.wait(timeout=3)
        return real_build(path, key)

    monkeypatch.setattr(scs, "_build_quarantine_summary", racing_build)
    threads = [
        threading.Thread(target=scs._quarantine_summary)
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert len(build_calls) == 2


def test_dashboard_true_rebuild_reuses_shared_quarantine_summary(
    tmp_path, monkeypatch,
):
    out = tmp_path / "samples.jsonl"
    out.write_text(json.dumps(_sample()) + "\n", encoding="utf-8")
    quarantine = tmp_path / "samples_quarantine.jsonl"
    quarantine.write_text(json.dumps({
        **_sample(),
        "server_quarantine_reason": "warehouse_source_pending",
    }) + "\n", encoding="utf-8")
    _fresh_build_state(monkeypatch, out)
    monkeypatch.setattr(scs, "QUARANTINE_SUMMARY_TTL", 300.0)
    clock = [1_000.0]
    monkeypatch.setattr(scs.time, "monotonic", lambda: clock[0])
    real_build = scs._build_quarantine_summary
    build_calls = []

    def counted_build(path, key):
        build_calls.append(key)
        return real_build(path, key)

    monkeypatch.setattr(scs, "_build_quarantine_summary", counted_build)
    assert scs._stats_payload()["quarantine"]["total"] == 1

    html, _, _ = scs._dashboard_bodies()

    assert b'\"quarantine_summary\"' in html
    assert b'\"warehouse_source_pending\": 1' in html
    assert len(build_calls) == 1


def test_dashboard_exposes_quarantine_count_without_mixing_rows(tmp_path, monkeypatch):
    out = tmp_path / "samples.jsonl"
    out.write_text(json.dumps(_sample()) + "\n", encoding="utf-8")
    quarantine = tmp_path / "samples_quarantine.jsonl"
    quarantine.write_text(json.dumps({
        **_sample(),
        "session_hash": "source-pending",
        "warehouse_source_qualification": "postupdate_unknown_warehouse_source",
        "official_distribution_eligible": False,
    }) + "\n", encoding="utf-8")
    _fresh_build_state(monkeypatch, out)

    html, _, _ = scs._dashboard_bodies()
    assert b'\"quarantine_summary\"' in html
    assert b'\"warehouse_source_pending\": 1' in html
    assert b'\"matched_rows\": 1' in html
    assert "完整玩家仓/来源未知样本进入正常大数据".encode() in html


def test_dashboard_does_not_rescan_growing_quarantine_before_cache_expiry(
    tmp_path, monkeypatch,
):
    out = tmp_path / "samples.jsonl"
    out.write_text(json.dumps(_sample()) + "\n", encoding="utf-8")
    quarantine = tmp_path / "samples_quarantine.jsonl"
    quarantine.write_text(json.dumps({
        **_sample(),
        "session_hash": "source-pending-1",
        "warehouse_source_qualification": "postupdate_unknown_warehouse_source",
        "official_distribution_eligible": False,
    }) + "\n", encoding="utf-8")
    _fresh_build_state(monkeypatch, out)
    monkeypatch.setattr(scs, "DASH_REBUILD_INTERVAL", 300.0)
    clock = [1_000.0]
    monkeypatch.setattr(scs.time, "monotonic", lambda: clock[0])
    real_summary = scs._quarantine_summary
    calls = []

    def counted_summary():
        calls.append(True)
        return real_summary()

    monkeypatch.setattr(scs, "_quarantine_summary", counted_summary)
    _, _, first_etag = scs._dashboard_bodies()
    assert len(calls) == 1

    with quarantine.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            **_sample(),
            "session_hash": "source-pending-2",
            "warehouse_source_qualification": "postupdate_unknown_warehouse_source",
            "official_distribution_eligible": False,
        }) + "\n")
    clock[0] = 1_299.0
    _, _, cached_etag = scs._dashboard_bodies()
    assert cached_etag == first_etag
    assert len(calls) == 1

    clock[0] = 1_301.0
    refreshed_html, _, refreshed_etag = scs._dashboard_bodies()
    assert refreshed_etag != first_etag
    assert len(calls) == 2
    assert b'\"warehouse_source_pending\": 2' in refreshed_html


def test_source_conflict_rows_persist_server_derived_reason(tmp_path, monkeypatch):
    out = tmp_path / "samples.jsonl"
    _fresh_build_state(monkeypatch, out)
    monkeypatch.setattr(scs, "_SEEN", set())
    monkeypatch.setattr(scs, "_QUAR_SEEN", set())
    result = scs._accept_one({
        **_sample(),
        "session_hash": "server-reason",
        "server_quarantine_reason": "settlement_incomplete",
        "warehouse_source_qualification": "postupdate_unknown_warehouse_source",
        "official_distribution_eligible": True,
    })
    assert result == "quarantined"
    row = json.loads(
        out.with_name("samples_quarantine.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert row["server_quarantine_reason"] == "warehouse_source_pending"
    assert "server_quarantine_reason" not in scs._ALLOWED


def test_public_and_personal_pages_never_read_quarantine_rows(tmp_path, monkeypatch):
    uid_hash = "0123456789abcdef"
    out = tmp_path / "samples.jsonl"
    out.write_text(json.dumps({
        **_sample(),
        "uid_hash": uid_hash,
        "player": "main-visible",
    }) + "\n", encoding="utf-8")
    quarantine = tmp_path / "samples_quarantine.jsonl"
    quarantine.write_text(json.dumps({
        **_sample(),
        "map_id": 987654321,
        "session_hash": "private-quarantine-row",
        "uid_hash": uid_hash,
        "player": "quarantine-secret-player",
        "warehouse_source_qualification": "postupdate_unknown_warehouse_source",
        "official_distribution_eligible": False,
    }) + "\n", encoding="utf-8")
    _fresh_build_state(monkeypatch, out)
    monkeypatch.setattr(scs, "_MY_PAGE_CACHE", scs.OrderedDict())
    monkeypatch.setattr(scs, "_MY_PAGE_LOCK", threading.Lock())
    public_paths = []
    personal_paths = []
    real_public_load = public_stats.load_samples
    real_personal_load = my_records.load_user_samples

    def public_load(path, *args, **kwargs):
        public_paths.append(Path(path))
        return real_public_load(path, *args, **kwargs)

    def personal_load(path, *args, **kwargs):
        personal_paths.append(Path(path))
        return real_personal_load(path, *args, **kwargs)

    monkeypatch.setattr(public_stats, "load_samples", public_load)
    monkeypatch.setattr(my_records, "load_user_samples", personal_load)

    public_html, _, _ = scs._public_bodies()
    personal_html = scs._my_page_html(uid_hash)

    assert public_paths == [out]
    assert personal_paths == [out]
    assert b"987654321" not in public_html
    assert "quarantine-secret-player" not in personal_html


def test_dashboard_detail_payload_has_explicit_upper_bound():
    rows = [
        {**_sample(), "session_hash": f"detail-{i}",
         "rare_items": [{"n": f"rare-{i}", "q": 6, "v": 100 + i}]}
        for i in range(3)
    ]
    payload = sample_dashboard.dashboard_detail_payload(rows, "rare", max_items=2)
    assert len(payload["items"]) == 2
    assert payload == {**payload, "matched_items": 3, "truncated": True, "max_items": 2}


def test_my_window_uses_play_time_fallback_and_excludes_unknown(tmp_path):
    uid_hash = "0123456789abcdef"
    out = tmp_path / "samples.jsonl"
    rows = [
        {**_sample(), "uid_hash": uid_hash, "client_ts": _utc_ts("2026-07-19T00:00:00")},
        {**_sample(), "uid_hash": uid_hash, "created_at": _utc_ts("2026-07-19T01:00:00")},
        {**_sample(), "uid_hash": uid_hash},
        {**_sample(), "uid_hash": "ffffffffffffffff", "client_ts": _utc_ts("2026-07-19T00:00:00")},
    ]
    out.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    samples = my_records.load_user_samples(
        out, uid_hash, start=date(2026, 7, 19), end=date(2026, 7, 19),
    )
    assert len(samples) == 2
    assert samples.parsed_count == 3 and samples.unknown_date_count == 1


def _get(url: str, headers: dict | None = None):
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_http_stats_rejects_unauthenticated_request_before_summary_scan(
    tmp_path, monkeypatch,
):
    out = tmp_path / "samples.jsonl"
    out.write_text(json.dumps(_sample()) + "\n", encoding="utf-8")
    _fresh_build_state(monkeypatch, out)
    monkeypatch.setattr(scs, "_DASH_PASS", "admin-secret")
    monkeypatch.setattr(scs, "_DASH_ALLOW_ANON", False)
    payload_calls = []

    def reject_payload():
        payload_calls.append(True)
        raise AssertionError("unauthenticated /stats must not build the summary")

    monkeypatch.setattr(scs, "_stats_payload", reject_payload)
    server = ThreadingHTTPServer(("127.0.0.1", 0), scs.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, headers, body = _get(base + "/stats")
        assert status == 401
        assert headers.get("WWW-Authenticate") == 'Basic realm="BidKing Dashboard"'
        assert body == b""
        assert payload_calls == []
    finally:
        server.shutdown()
        server.server_close()


def test_http_heavy_busy_is_immediate_but_health_and_version_remain_available(
    tmp_path, monkeypatch,
):
    out = tmp_path / "samples.jsonl"
    out.write_text(json.dumps(_sample()) + "\n", encoding="utf-8")
    _fresh_build_state(monkeypatch, out)
    monkeypatch.setattr(scs, "_DASH_ALLOW_ANON", True)
    monkeypatch.setattr(scs, "_HEALTH_CACHE", {"ts": 0.0, "code": 200, "payload": None})
    server = ThreadingHTTPServer(("127.0.0.1", 0), scs.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert scs._HEAVY_SCAN_LOCK.acquire(blocking=False)
        started = time.monotonic()
        status, headers, _ = _get(base + "/dashboard")
        elapsed = time.monotonic() - started
        assert status == 503 and headers.get("Retry-After") == str(scs.HEAVY_RETRY_AFTER_SECONDS)
        assert elapsed < 1.0
        assert _get(base + "/healthz")[0] == 200
        assert _get(base + "/version")[0] == 200
        activation = urllib.request.Request(
            base + "/activate", data=b"{}", method="POST",
            headers={"Content-Type": "application/json"},
        )
        activation_started = time.monotonic()
        try:
            urllib.request.urlopen(activation, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 503  # 测试服务未配置激活，但请求没有被重任务门阻塞。
        assert time.monotonic() - activation_started < 1.0
    finally:
        if scs._HEAVY_SCAN_LOCK.locked():
            scs._HEAVY_SCAN_LOCK.release()
        server.shutdown()
        server.server_close()


def test_export_streams_snapshot_without_read_bytes(tmp_path, monkeypatch):
    out = tmp_path / "samples.jsonl"
    expected = (json.dumps(_sample()) + "\n") * 20
    out.write_text(expected, encoding="utf-8")
    expected_bytes = out.read_bytes()
    _fresh_build_state(monkeypatch, out)
    monkeypatch.setattr(scs, "_EXPORT_KEY", "export-test")
    real_read_bytes = Path.read_bytes

    def reject_read_bytes(path: Path, *args, **kwargs):
        if path == out:
            raise AssertionError("export must stream instead of reading the full database")
        return real_read_bytes(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", reject_read_bytes)
    server = ThreadingHTTPServer(("127.0.0.1", 0), scs.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, headers, raw = _get(base + "/export", {"X-Api-Key": "export-test"})
        assert status == 200 and raw == expected_bytes
        status, headers, compressed = _get(
            base + "/export", {"X-Api-Key": "export-test", "Accept-Encoding": "gzip"},
        )
        assert status == 200 and headers.get("Content-Encoding") == "gzip"
        assert gzip.decompress(compressed) == expected_bytes
    finally:
        server.shutdown()
        server.server_close()
