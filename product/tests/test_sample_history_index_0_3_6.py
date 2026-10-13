"""Synthetic source-only index controls; no production files, service or account."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sample_dashboard as dashboard
import public_stats as public
import my_records
from sample_history_index import HistoryIndex, HistoryIndexError, Projection

TZ = timezone(timedelta(hours=8))
OWNER_A = "a" * 16
OWNER_B = "b" * 16


def epoch(day):
    return datetime.fromisoformat(day).replace(tzinfo=TZ).timestamp()


def projection(rec, kind):
    error = False
    try:
        ts = my_records._record_ts(rec)
    except (OverflowError, TypeError, ValueError):
        ts, error = None, True
    try:
        play = datetime.fromtimestamp(ts, tz=TZ).date().isoformat() if ts is not None else None
    except (OverflowError, OSError, TypeError, ValueError):
        play = None
    return Projection(public._day_of(rec), play,
        bool(dashboard._dashboard_sample_allowed(rec) and rec.get("final_quality_counts")),
        error, rec.get("server_quarantine_reason"))


def sample(h, day="2026-10-11", *, uid=OWNER_A, **extra):
    record = {"session_hash": h, "map_id": 2401, "hero": "ahmed", "uid_hash": uid,
              "client_ts": epoch(day), "final_quality_counts": "q1=1", "known_value_sum": 1000}
    record.update(extra)
    return record


def append(path, records):
    with path.open("ab") as stream:
        for record in records:
            stream.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))


def paths(tmp_path):
    folder = tmp_path / "samples"
    folder.mkdir()
    return folder / "samples.jsonl", folder / "samples_quarantine.jsonl", tmp_path / "private" / "history.sqlite3"


def index_for(main, quarantine, db, *, version="real-date-provider-v1", projector=projection):
    return HistoryIndex(db, main, quarantine, projector=projector, parser_version=version)


def test_exact_history_keys_counter_and_independent_quarantine_rescue(tmp_path):
    main, quarantine, db = paths(tmp_path)
    rows = [sample(1, map_id=1), sample("1", map_id="1"), sample(1.0, map_id=1.0), sample(True, map_id=True)]
    append(main, rows)
    append(quarantine, [sample("rescued", server_quarantine_reason="settlement_incomplete")])
    index = index_for(main, quarantine, db)
    index.refresh()
    assert index.contains("main", 1) and index.contains("main", "1")
    assert index.summary()["main_unique"] == 2
    assert index.summary()["map_count"] == Counter({1: 3, "1": 1})
    assert index.contains("quarantine", "rescued") and not index.contains("main", "rescued")
    append(main, [sample("rescued")])
    report = index.refresh()
    assert not report["rebuilt"] and index.contains("main", "rescued")
    assert index.summary()["quarantine_unique"] == 1
    index.close()


def test_fresh_warm_open_does_not_parse_history_and_scans_only_new_tail(tmp_path):
    main, quarantine, db = paths(tmp_path)
    append(main, [sample(str(i), "2026-08-01") for i in range(100)])
    index = index_for(main, quarantine, db)
    first = index.refresh()
    assert first["rebuilt"] and first["scan_bytes"] == main.stat().st_size
    index.close()
    calls = []
    def observe(rec, kind):
        calls.append(kind)
        return projection(rec, kind)
    reopened = index_for(main, quarantine, db, projector=observe)
    warm = reopened.refresh()
    assert not warm["rebuilt"] and warm["scan_bytes"] == 0 and calls == []
    assert warm["validation_bytes"] <= 4 * 4096  # bounded anchors are real I/O
    previous_size = main.stat().st_size
    append(main, [sample("new")])
    tail = reopened.refresh()
    assert tail["scan_bytes"] == main.stat().st_size - previous_size and calls == ["main"]
    assert reopened.contains("main", "0") and reopened.contains("main", "new")
    reopened.close()


def test_window_late_upload_boundary_and_actual_reader_date_rules(tmp_path):
    main, quarantine, db = paths(tmp_path)
    boundary = epoch("2026-09-12")
    records = [sample("before", client_ts=boundary-1), sample("start", client_ts=boundary),
        sample("after", client_ts=boundary+1), sample("fallback", client_ts=None, created_at=epoch("2026-10-10")),
        sample("unknown", client_ts=None), sample("late-old", "2026-08-01", received_ts=epoch("2026-10-11"))]
    append(main, records)
    index = index_for(main, quarantine, db)
    index.refresh()
    for view, old in (("public", public.load_samples), ("dashboard", dashboard._load_samples)):
        selected = index.rows(view=view, start=date(2026,9,12), end=date(2026,10,11))
        actual = list(selected)
        original_source = old(main, start=date(2026,9,12), end=date(2026,10,11))
        expected = list(original_source)
        assert actual == expected
        assert selected.last_unknown_date_count == original_source.last_unknown_date_count
        assert selected.bytes_read < main.stat().st_size
        assert all(rec["session_hash"] != "late-old" for rec in actual)
    assert [rec["session_hash"] for rec in index.rows(view="public", start=date(2026,9,12), end=date(2026,10,11))] == ["start", "after"]
    assert any(rec["session_hash"] == "fallback" for rec in index.rows(view="dashboard", start=date(2026,9,12), end=date(2026,10,11)))
    index.close()


def test_metadata_counts_are_scoped_before_private_window_and_tag_not_authority(tmp_path):
    main, quarantine, db = paths(tmp_path)
    records = [sample("a-now"), sample("b-now", uid=OWNER_B),
        sample("a-unknown", client_ts=None), sample("b-unknown", uid=OWNER_B, client_ts=None),
        sample("a-old", "2026-08-01"), sample("claimed-tag", uid=OWNER_B, owner_tag="not-an-authority")]
    append(main, records)
    index = index_for(main, quarantine, db)
    index.refresh()
    current = index.rows(view="my", start=date(2026,10,1), end=date(2026,10,11), legacy_uid_hash=OWNER_A)
    assert [rec["session_hash"] for rec in current] == ["a-now"]
    assert current.last_parsed_count == 3 and current.last_unknown_date_count == 1
    assert current.rows_read == 1
    signed = index.rows(view="my", start=date(2026,10,1), end=date(2026,10,11), session_hashes={"b-now", "b-unknown"})
    assert [rec["session_hash"] for rec in signed] == ["b-now"]
    assert signed.last_parsed_count == 2 and signed.last_unknown_date_count == 1
    assert list(index.rows(view="my", session_hashes=set())) == []
    # Corrupt a candidate association, not the original row: must fail closed,
    # never export the other owner's metadata as an accepted private scope.
    index._connection.execute("UPDATE rows SET owner_tag=(SELECT owner_tag FROM rows WHERE session_key=? LIMIT 1) WHERE session_key=?",
                              (b"s:a-now", b"s:b-unknown"))
    index._connection.commit()
    bad = index.rows(view="my", start=date(2026,10,1), end=date(2026,10,11), legacy_uid_hash=OWNER_A)
    with pytest.raises(HistoryIndexError, match="candidate_.*mismatch"):
        list(bad)
    assert bad.last_parsed_count == bad.last_unknown_date_count == 0
    assert not index.ready
    assert index.refresh()["rebuilt"]
    index.close()
    raw = db.read_bytes()
    assert OWNER_A.encode() not in raw and OWNER_B.encode() not in raw


@pytest.mark.parametrize("change", ["truncate", "replace", "same-size"])
def test_changed_source_invalidates_generation_and_preserves_previous_index(tmp_path, change):
    main, quarantine, db = paths(tmp_path)
    append(main, [sample("before")])
    index = index_for(main, quarantine, db)
    index.refresh()
    old_rows = index.rows(view="public")
    if change == "truncate":
        main.write_bytes(b"")
    elif change == "replace":
        replacement = main.with_name("replacement")
        append(replacement, [sample("after!")])
        os.replace(replacement, main)
    else:
        old_stat = main.stat()
        main.write_bytes(main.read_bytes().replace(b"before", b"after!"))
        os.utime(main, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns+1_000_000))
    assert index.refresh()["rebuilt"]
    assert not index.contains("main", "before")
    with pytest.raises(HistoryIndexError):
        list(old_rows)
    assert list(db.parent.glob("history.sqlite3.previous-*"))
    index.close()


def test_unsealed_terminal_row_rewound_and_snapshot_never_reads_future_row(tmp_path):
    main, quarantine, db = paths(tmp_path)
    first = json.dumps(sample("one")).encode()
    main.write_bytes(first)
    index = index_for(main, quarantine, db)
    index.refresh()
    assert index.contains("main", "one")
    with main.open("ab") as stream:
        stream.write(b"\n")
    report = index.refresh()
    assert not report["rebuilt"] and index.summary()["map_count"] == Counter({2401:1})
    checkpoint = main.stat().st_size
    append(main, [sample("two")])
    index.refresh()
    source = index.rows(view="public", snapshot_bytes=checkpoint)
    assert [rec["session_hash"] for rec in source] == ["one"]
    assert source.bytes_read == checkpoint
    # Legacy bounded binary readers accept valid JSON when the snapshot stops
    # immediately before its newline, rather than silently dropping that row.
    fragment = index.rows(view="public", snapshot_bytes=checkpoint-1)
    assert list(fragment) == list(public.load_samples(main, snapshot_bytes=checkpoint-1))
    assert fragment.bytes_read == checkpoint-1
    # A torn JSON tail is retained and replayed on append, not marked seen.
    with main.open("ab") as stream:
        stream.write(b'{"session_hash":"three"')
    index.refresh()
    assert not index.contains("main", "three")
    with main.open("ab") as stream:
        stream.write(b',"map_id":2401}\n')
    index.refresh()
    assert index.contains("main", "three")
    index.close()


def test_append_then_database_failure_closes_lookup_until_tail_replay(tmp_path, monkeypatch):
    main, quarantine, db = paths(tmp_path)
    append(main, [sample("old")])
    index = index_for(main, quarantine, db)
    index.refresh()
    append(main, [sample("new")])
    original = index._scan
    def interrupted(connection, kind, info):
        original(connection, kind, info)
        if kind == "main":
            raise sqlite3.OperationalError("synthetic commit boundary")
    monkeypatch.setattr(index, "_scan", interrupted)
    with pytest.raises(sqlite3.OperationalError):
        index.refresh()
    with pytest.raises(HistoryIndexError):
        index.contains("main", "new")
    monkeypatch.setattr(index, "_scan", original)
    assert not index.refresh()["rebuilt"] and index.contains("main", "new")
    assert index.summary()["map_count"] == Counter({2401:2})
    index.close()


def test_parser_version_and_owned_corruption_rebuild_but_foreign_database_untouched(tmp_path):
    main, quarantine, db = paths(tmp_path)
    append(main, [sample("first")])
    index = index_for(main, quarantine, db)
    index.refresh()
    index.close()
    newer = index_for(main, quarantine, db, version="provider-v2")
    assert newer.refresh()["rebuilt"]
    newer._connection.execute("UPDATE sources SET info='broken'")
    newer._connection.commit()
    newer.close()
    healed = index_for(main, quarantine, db, version="provider-v2")
    assert healed.refresh()["rebuilt"] and healed.contains("main", "first")
    healed.close()
    wrong = db.with_name("membership.sqlite3")
    connection = sqlite3.connect(wrong)
    connection.execute("CREATE TABLE membership (n INTEGER)")
    connection.commit()
    connection.close()
    before = wrong.read_bytes()
    with pytest.raises(HistoryIndexError, match="not_owned"):
        index_for(main, quarantine, wrong).refresh()
    assert wrong.read_bytes() == before


@pytest.mark.parametrize("content", [b"\xffbad\n", b"[]\n"])
def test_legacy_reader_failure_boundaries_not_softened_by_index(tmp_path, content):
    main, quarantine, db = paths(tmp_path)
    main.write_bytes(content)
    assert list(public.load_samples(main)) == []
    assert list(dashboard._load_samples(main)) == []
    index = index_for(main, quarantine, db)
    with pytest.raises((HistoryIndexError, UnicodeDecodeError)):
        index.refresh()
    assert not index.ready and not db.exists()


def test_index_path_cannot_be_in_public_sample_directory(tmp_path):
    main, quarantine, _ = paths(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        index_for(main, quarantine, main.parent / "history.sqlite3")


def test_empty_recent_main_never_promotes_recent_quarantine(tmp_path):
    main, quarantine, db = paths(tmp_path)
    append(main, [sample("old-main", "2026-08-01")])
    append(quarantine, [sample("new-quarantine", server_quarantine_reason="warehouse_source_pending")])
    index = index_for(main, quarantine, db)
    index.refresh()
    for view in ("public", "dashboard"):
        source = index.rows(view=view, start=date(2026,9,12), end=date(2026,10,11))
        assert list(source) == [] and source.rows_read == source.bytes_read == 0
    assert index.summary()["quarantine_unique"] == 1
    index.close()


@pytest.mark.parametrize("view,reader", [("public", public.load_samples), ("dashboard", dashboard._load_samples)])
def test_unbounded_unknown_and_captured_unsealed_counter_match_original(tmp_path, view, reader):
    main, quarantine, db = paths(tmp_path)
    append(main, [sample("known"), sample("unknown", client_ts=None)])
    index = index_for(main, quarantine, db)
    index.refresh()
    for bounds, unknown in (({}, 0), ({"start": date(2026,10,1), "end": date(2026,10,11)}, 1)):
        actual, expected = index.rows(view=view, **bounds), reader(main, **bounds)
        assert list(actual) == list(expected)
        assert actual.last_parsed_count == expected.last_parsed_count == 2
        assert actual.last_unknown_date_count == expected.last_unknown_date_count == unknown
    index.close()
    # Capture while JSON is valid but its terminal newline has not arrived.
    payload = json.dumps(sample("unsealed", client_ts=None)).encode()
    main.write_bytes(payload)
    index = index_for(main, quarantine, db)
    index.refresh()
    for bounds in ({}, {"start": date(2026,10,1), "end": date(2026,10,11)}):
        frozen = index.rows(view=view, snapshot_bytes=len(payload), **bounds)
        list(frozen)
        if main.stat().st_size == len(payload):
            with main.open("ab") as stream:
                stream.write(b"\n" + json.dumps(sample("future")).encode() + b"\n")
            assert not index.refresh()["rebuilt"]
        expected = reader(main, snapshot_bytes=len(payload), **bounds)
        assert list(frozen) == list(expected)
        assert frozen.last_parsed_count == expected.last_parsed_count == 1
        assert frozen.last_unknown_date_count == expected.last_unknown_date_count == int(bool(bounds))
    index.close()


@pytest.mark.parametrize("scope", ["public", "tag", "signed"])
def test_single_cursor_across_batches_keeps_snapshot_and_refuses_generation_change(tmp_path, scope):
    main, quarantine, db = paths(tmp_path)
    append(main, [sample(str(i)) for i in range(600)])
    index = index_for(main, quarantine, db)
    index.refresh()
    frozen = index.rows(view="public", start=date(2026,10,1), end=date(2026,10,11))
    iterator = iter(frozen)
    assert [next(iterator)["session_hash"] for _ in range(257)] == [str(i) for i in range(257)]
    append(main, [sample("future")])
    assert not index.refresh()["rebuilt"]
    assert [rec["session_hash"] for rec in iterator] == [str(i) for i in range(257,600)]
    assert frozen.last_parsed_count == frozen.last_matched_count == frozen.rows_read == 600
    kwargs = ({"view":"public"} if scope == "public" else
              {"view":"my", "legacy_uid_hash":OWNER_A} if scope == "tag" else
              {"view":"my", "session_hashes":{str(i) for i in range(600)}})
    iterator = iter(index.rows(**kwargs))
    for _ in range(257):
        next(iterator)
    # Windows ordinary readers deny replacement of their open file handle.
    # Truncation is another original rebuild trigger and is permitted while
    # this reader is open, so it exercises the live iterator's generation gate.
    main.write_bytes((json.dumps(sample("replaced")) + "\n").encode())
    assert index.refresh()["rebuilt"]
    with pytest.raises(HistoryIndexError, match="generation"):
        next(iterator)
    index.close()


@pytest.mark.parametrize("scope", ["public", "tag", "signed"])
def test_cursor_close_and_private_request_cleanup_belong_to_original_connection(tmp_path, scope):
    main, quarantine, db = paths(tmp_path)
    append(main, [sample(str(i)) for i in range(600)])
    index = index_for(main, quarantine, db)
    index.refresh()
    kwargs = ({"view":"public"} if scope == "public" else
              {"view":"my", "legacy_uid_hash":OWNER_A} if scope == "tag" else
              {"view":"my", "session_hashes":{str(i) for i in range(600)}})
    reader = index.rows(**kwargs)
    iterator = iter(reader)
    for _ in range(257):
        next(iterator)
    # A distinct signed request on the same connection is not owned by reader.
    other = index.rows(view="my", session_hashes={"0","1"})
    other_iterator = iter(other)
    next(other_iterator)
    iterator.close()
    assert index._connection.execute("SELECT DISTINCT request FROM history_requested_sessions").fetchall() == [(other.request_id,)]
    assert [rec["session_hash"] for rec in other_iterator] == ["1"]
    iterator = iter(index.rows(**kwargs))
    for _ in range(257):
        next(iterator)
    index.close()
    with pytest.raises(HistoryIndexError, match="not_ready"):
        next(iterator)
    assert not index._read_cursors


def test_real_consumer_readers_keep_results_counts_and_read_only_selected_payload(tmp_path):
    main, quarantine, db = paths(tmp_path)
    records = [sample(str(i), "2026-08-01", uid=OWNER_A if i % 2 else OWNER_B, padding="x"*128) for i in range(100)]
    records += [sample("a-recent"), sample("b-recent", uid=OWNER_B), sample("a-unknown", client_ts=None),
                sample("b-unknown", uid=OWNER_B, client_ts=None), sample("a-fallback", client_ts=None, created_at=epoch("2026-10-10"))]
    append(main, records)
    index = index_for(main, quarantine, db)
    index.refresh()
    bounds = {"start": date(2026,9,12), "end": date(2026,10,11)}
    for reader in (public.load_samples, dashboard._load_samples):
        original = reader(main, **bounds)
        selected = reader(main, history_index=index, **bounds)
        assert list(selected) == list(original)
        assert selected.last_parsed_count == original.last_parsed_count
        assert selected.last_unknown_date_count == original.last_unknown_date_count
        assert selected.payload_read_bytes < main.stat().st_size // 10
        assert selected.payload_read_rows in (2, 3)
        assert list(selected) == list(original)  # actual repeatable consumer
        assert selected.total_payload_read_bytes == selected.payload_read_bytes * selected.passes
        assert selected.total_payload_read_rows == selected.payload_read_rows * selected.passes
    for authority in (None, type("Authority", (), {"sessions_for_uid_hash": lambda self, uid: {"a-recent", "a-unknown", "a-fallback"}})()):
        original = my_records.load_user_samples(main, OWNER_A, identity_store=authority, **bounds)
        selected = my_records.load_user_samples(main, OWNER_A, identity_store=authority, history_index=index, **bounds)
        assert selected == original
        assert selected.parsed_count == original.parsed_count
        assert selected.unknown_date_count == original.unknown_date_count == 1
        assert selected.payload_read_rows == 2
        assert selected.payload_read_bytes < main.stat().st_size // 10
    empty_authority = type("Empty", (), {"sessions_for_uid_hash": lambda self, uid: set()})()
    assert my_records.load_user_samples(main, OWNER_A, history_index=index, identity_store=empty_authority) == []
    with pytest.raises(ValueError, match="source mismatch"):
        public.load_samples(quarantine, history_index=index)
    index.close()


def configure_collector(monkeypatch, main, db):
    import sample_collector_server as collector
    monkeypatch.setattr(collector, "_OUT", main)
    monkeypatch.setattr(collector, "_HISTORY_INDEX_DB_FILE", db)
    monkeypatch.setattr(collector, "_SAMPLE_HISTORY_INDEX", None)
    monkeypatch.setattr(collector, "_HISTORY_INDEX_STATE", "ready")
    monkeypatch.setattr(collector, "_SEEN", set())
    monkeypatch.setattr(collector, "_QUAR_SEEN", set())
    monkeypatch.setattr(collector, "_MAP_COUNT", Counter())
    monkeypatch.setattr(collector, "_QUARANTINE_SUMMARY_CACHE", {"key":None,"value":None,"built_mono":0})
    return collector


def test_actual_collector_lazy_dedup_counts_rescue_and_quarantine_nonobject(tmp_path, monkeypatch):
    main, quarantine, db = paths(tmp_path)
    append(main, [sample(1, map_id=1), sample("1", map_id="1"), sample(1, map_id=1)])
    append(quarantine, [sample("rescue", server_quarantine_reason="settlement_incomplete"), [], "invalid-shape"])
    collector = configure_collector(monkeypatch, main, db)
    expected_q = collector._build_quarantine_summary(quarantine, (quarantine.stat().st_mtime_ns, quarantine.stat().st_size))
    old_seen, old_quarantine, old_maps = collector._build_existing_index()
    monkeypatch.setattr(collector, "_build_existing_index", lambda: pytest.fail("configured path must not reload historical sets"))
    collector._load_existing()
    index = collector._SAMPLE_HISTORY_INDEX
    try:
        assert collector._SEEN == collector._QUAR_SEEN == set()
        assert index.summary()["main_unique"] == len(old_seen) == 2
        assert index.summary()["quarantine_unique"] == len(old_quarantine) == 1
        assert collector._MAP_COUNT == old_maps == Counter({1:2,"1":1})
        assert collector._quarantine_summary(refresh=True) == expected_q
        assert collector._accept_one(sample(1)) == "duplicate"
        assert collector._accept_one(sample("rescue")) == "accepted"
        assert collector._accept_one(sample("rescue")) == "duplicate"
        assert collector._accept_one(sample("bad", known_value_sum=0)) == "quarantined"
        assert collector._accept_one(sample("bad", known_value_sum=0)) == "duplicate"
        assert collector._SEEN == collector._QUAR_SEEN == set()
        assert collector._MAP_COUNT == Counter({1:2,"1":1,2401:1})
        assert index.summary()["map_count"] == collector._MAP_COUNT
    finally:
        index.close()


def test_actual_collector_raw_append_derived_failure_closes_and_restart_replays(tmp_path, monkeypatch):
    main, quarantine, db = paths(tmp_path)
    append(main, [sample("old")])
    collector = configure_collector(monkeypatch, main, db)
    collector._load_existing()
    index = collector._SAMPLE_HISTORY_INDEX
    original = index._scan
    def fail_after_scan(connection, kind, info):
        original(connection, kind, info)
        raise sqlite3.OperationalError("synthetic derived commit failure")
    monkeypatch.setattr(index, "_scan", fail_after_scan)
    with pytest.raises(collector.CollectorHistoryUnavailable):
        collector._accept_one(sample("appended"))
    assert len(main.read_text().splitlines()) == 2
    assert collector._history_index_state() == "failed"
    assert not collector._history_index_ready()
    with pytest.raises(HistoryIndexError):
        index.contains("main", "appended")
    collector._load_existing()  # a fresh provider replays the uncommitted derived tail
    collector._HISTORY_INDEX_STATE = "ready"
    healed = collector._SAMPLE_HISTORY_INDEX
    try:
        assert collector._history_index_ready()
        assert collector._accept_one(sample("appended")) == "duplicate"
        assert collector._MAP_COUNT == Counter({2401:2})
    finally:
        healed.close()


def test_actual_collector_pages_use_index_without_public_or_private_scope_widening(tmp_path, monkeypatch, capsys):
    main, quarantine, db = paths(tmp_path)
    append(main, [sample(str(i), "2026-08-01", padding="x"*512) for i in range(100)] +
                [sample("a-recent"),sample("b-recent",uid=OWNER_B),sample("a-unknown",client_ts=None)])
    append(quarantine, [sample("q-recent", server_quarantine_reason="invalid_value")])
    collector = configure_collector(monkeypatch, main, db)
    collector._load_existing()
    monkeypatch.setattr(collector,"_DASH_CACHE",{"key":None,"plain":None,"gz":None,"etag":None,"built_mono":0})
    monkeypatch.setattr(collector,"_PUB_CACHE",{"key":None,"plain":None,"gz":None,"etag":None,"built_mono":0,"built_wall":0})
    monkeypatch.setattr(collector,"_MY_PAGE_CACHE", __import__("collections").OrderedDict())
    monkeypatch.setattr(collector,"_UPLOAD_IDENTITY_STORE",None)
    captured = []
    for module, name in ((public,"load_samples"),(dashboard,"_load_samples"),(my_records,"load_user_samples")):
        original = getattr(module,name)
        def tracked(*args, _original=original, **kwargs):
            assert kwargs["history_index"] is collector._SAMPLE_HISTORY_INDEX
            selected = _original(*args,**kwargs)
            captured.append(selected)
            return selected
        monkeypatch.setattr(module,name,tracked)
    window = {"mode":"recent-30","start":date(2026,9,12),"end":date(2026,10,11),"label":"recent","schema":"test"}
    try:
        public_body = collector._public_bodies(window)[0]
        collector._dashboard_bodies(window)
        private_body = collector._my_page_html(OWNER_A,window=window)
        assert len(captured) == 3
        assert all(row.payload_read_bytes < main.stat().st_size//10 for row in captured)
        assert captured[0].last_parsed_count == 103 and captured[0].last_unknown_date_count == 1
        assert captured[2].parsed_count == 102 and captured[2].unknown_date_count == 1
        assert captured[2].payload_read_rows == 1
        assert OWNER_A.encode() not in public_body and OWNER_B.encode() not in public_body
        assert OWNER_B not in private_body
        assert "q-recent" not in private_body
        logs = capsys.readouterr().out
        assert "payload_read_bytes=" in logs
        assert OWNER_A not in logs and OWNER_B not in logs
    finally:
        collector._SAMPLE_HISTORY_INDEX.close()


def test_cli_explicit_index_is_optional_and_no_new_authority(tmp_path, monkeypatch, capsys):
    import sample_collector_server as collector
    with pytest.raises(SystemExit) as exit_info:
        collector.main(["collector", "--help"])
    assert exit_info.value.code == 0
    assert "--history-index-db-file" in capsys.readouterr().out
    main, quarantine, db = paths(tmp_path)
    collector = configure_collector(monkeypatch, main, db)
    assert not collector._history_index_ready()
    with pytest.raises(collector.CollectorHistoryUnavailable):
        collector._accept_one(sample("cold-unavailable"))
    assert not main.exists()


def test_actual_heavy_snapshot_cannot_land_between_raw_append_and_index_commit(tmp_path, monkeypatch):
    main, quarantine, db = paths(tmp_path)
    append(main,[sample("old")])
    collector = configure_collector(monkeypatch,main,db)
    collector._load_existing()
    index = collector._SAMPLE_HISTORY_INDEX
    original = index.refresh
    raw_written, release, reader_started, reader_captured = (threading.Event() for _ in range(4))
    failures, results = [], []
    def paused_refresh():
        indexed_size = index._info(index._connection,"main")["size"]
        if main.stat().st_size > indexed_size:
            raw_written.set()
            assert release.wait(3)
        return original()
    monkeypatch.setattr(index,"refresh",paused_refresh)
    def write():
        try:
            assert collector._accept_one(sample("new")) == "accepted"
        except BaseException as exc:
            failures.append(exc)
    def read():
        reader_started.set()
        try:
            with collector._serialized_heavy_operation("snapshot-control") as state:
                reader_captured.set()
                source = public.load_samples(main,history_index=index,snapshot_bytes=state[1])
                results.extend(rec["session_hash"] for rec in source)
        except BaseException as exc:
            failures.append(exc)
    writer,reader = threading.Thread(target=write),threading.Thread(target=read)
    writer.start()
    try:
        assert raw_written.wait(3)
        reader.start()
        assert reader_started.wait(3)
        assert not reader_captured.wait(.05)
    finally:
        release.set()
        writer.join(3)
        if reader.ident is not None:
            reader.join(3)
    try:
        assert not writer.is_alive() and not reader.is_alive()
        assert not failures
        assert results == ["old","new"]
    finally:
        index.close()


@pytest.mark.parametrize("phase", ["warm", "tail", "initial", "rebuild", "exception", "interrupt", "interrupt_check"])
def test_refresh_published_readiness_and_locked_readers_survive_normal_work_only(tmp_path, monkeypatch, phase):
    main, quarantine, db = paths(tmp_path)
    append(main,[sample("old")])
    index = index_for(main,quarantine,db)
    collector = configure_collector(monkeypatch,main,db)
    monkeypatch.setattr(collector,"_SAMPLE_HISTORY_INDEX",index)
    if phase != "initial":
        index.refresh()
    else:
        monkeypatch.setattr(collector,"_HISTORY_INDEX_STATE","loading")
    if phase in {"tail","exception","interrupt"}:
        append(main,[sample("new")])
    if phase == "rebuild":
        main.write_bytes(b"")
    class SyntheticInterrupt(BaseException):
        pass
    failure = (RuntimeError("synthetic transaction exception") if phase=="exception" else
               SyntheticInterrupt("synthetic interruption") if phase in {"interrupt","interrupt_check"} else None)
    entered,release = threading.Event(),threading.Event()
    method = ("_rebuild" if phase in {"initial","rebuild"} else
              "_check_reusable" if phase in {"warm","interrupt_check"} else "_scan")
    original = getattr(index,method)
    def paused(*args):
        entered.set()
        assert release.wait(2)
        if phase == "interrupt_check":
            raise failure
        result = original(*args)
        if failure is not None:
            raise failure
        return result
    monkeypatch.setattr(index,method,paused)
    outcome,values = {},{}
    done,started = {},{}
    def refresh():
        try:
            index.refresh()
            outcome["success"] = True
        except BaseException as exc:
            outcome["exception"] = exc
    readers = []
    for label in ("contains","rows","summary"):
        done[label],started[label] = threading.Event(),threading.Event()
        def read(_label=label):
            started[_label].set()
            try:
                if _label=="contains":
                    values[_label] = index.contains("main","old")
                elif _label=="summary":
                    values[_label] = index.summary()["main_unique"]
                else:
                    values[_label] = sum(1 for _ in index.rows(view="public"))
            except HistoryIndexError:
                values[_label] = "closed"
            finally:
                done[_label].set()
        readers.append(threading.Thread(target=read))
    writer = threading.Thread(target=refresh)
    writer.start()
    try:
        assert entered.wait(2)
        if phase in {"initial","rebuild"}:
            assert not index.ready and not collector._history_index_ready()
        else:
            # No transient failed projection while a valid published index is
            # verified/appended under its serialization lock.
            assert index.ready and collector._history_index_state()=="ready"
        for reader in readers:
            reader.start()
        assert all(event.wait(2) for event in started.values())
        assert all(not event.wait(.025) for event in done.values())
    finally:
        release.set()
        writer.join(2)
        for reader in readers:
            if reader.ident is not None:
                reader.join(2)
    try:
        assert not writer.is_alive() and all(not reader.is_alive() for reader in readers)
        if failure is not None:
            assert outcome["exception"] is failure  # original error/interruption re-raised
            assert not index.ready and collector._history_index_state()=="failed"
            assert values == {"contains":"closed","rows":"closed","summary":"closed"}
            # Normal tail transaction remains uncommitted on any BaseException.
            if phase != "interrupt_check":
                assert index._info(index._connection,"main")["size"] < main.stat().st_size
        else:
            assert outcome=={"success":True} and index.ready
            count = 0 if phase=="rebuild" else 2 if phase=="tail" else 1
            assert values=={"contains":bool(count),"rows":count,"summary":count}
    finally:
        index.close()
