"""Monitor logical field paths survive support projection without relaxing privacy."""
from __future__ import annotations

import ast
import copy
import json
import os
from pathlib import Path
from typing import Any
import zipfile

import pytest

from bidking_lab.secure.diagnostic_redaction import (
    collect_player_aliases, redact_document, redact_payload,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = "monitor_pre_settlement_field_updates"
OWNERS = ("structured_ref_inputs", "ahmad_ref_inputs")
SESSION_FIELDS = ("total_item_count", "total_count", "total_cells", "warehouse_total_cells", "total_avg_cells")
BUCKET_FIELDS = ("avg_cells", "count", "cells", "total_cells", "avg_value", "average_value", "value", "value_sum", "total_value")
SPLIT_FIELDS = ("avg_cells", "count", "cells", "total_cells", "value", "value_sum", "total_value")
PATHS = (
    [("session", field) for field in SESSION_FIELDS]
    + [("bucket", str(quality), field) for quality in range(1, 7) for field in BUCKET_FIELDS]
    + [("bucket_split", quality, field) for quality in ("white", "green") for field in SPLIT_FIELDS]
    + [("bucket_group", "q4q5q6", "count"), ("category_quality_group", "103+107", "q5q6", "count")]
)


def bridge(path, **row_fields):
    return {"source": SOURCE, "field_updates": [{"path": path, "value": 7, "sequence": 2, "confidence": 1.0, "event_kind": "state_update", "phase": "bidding", **row_fields}]}


@pytest.mark.parametrize("owner", OWNERS)
@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("container", [list, tuple])
def test_exact_monitor_vocabulary_and_input_structure(owner, path, container):
    payload = {owner: bridge(container(path))}
    before = copy.deepcopy(payload)
    actual = redact_payload(payload)
    projected = actual[owner]["field_updates"][0]
    assert projected["path"] == list(path)
    assert type(projected["path"]) is list
    assert projected == {**before[owner]["field_updates"][0], "path": list(path)}
    assert actual[owner]["source"] == SOURCE
    assert payload == before


@pytest.mark.parametrize("path", [
    [], ["bucket"], ["bucket", "5"], ["bucket", "5", "count", "private"],
    ["bucket", "0", "count"], ["bucket", "7", "count"], ["bucket", "q5", "count"],
    ["bucket", 5, "count"], ["bucket", True, "count"], ["Bucket", "5", "count"],
    ["bucket", "5", "unknown"], ["session", "unknown"], ["session", "total_count", "extra"],
    ["bucket_split", "red", "count"], ["bucket_split", "green", "avg_value"],
    ["bucket_group", "q4q5", "count"], ["bucket_group", "456", "count"],
    ["bucket_group", "q4q5q6", "cells"],
    ["category_quality_group", "103+999", "q5q6", "count"],
    ["category_quality_group", "103+107", "q4q5", "count"],
    ["category_quality_group", "103+107", "q5q6", "count", "extra"],
    ["bucket", "https://example.test/private", "count"],
    ["bucket", "C:/Users/example", "count"],
    ["bucket", "5", "token=PRIVATE_TOKEN"],
    "bucket.5.count", None, {"path": ["bucket", "5", "count"]},
])
def test_unknown_or_malformed_paths_use_original_path_handling(path):
    normal = redact_payload({"path": path})["path"]
    actual = redact_payload({OWNERS[0]: bridge(path)})[OWNERS[0]]["field_updates"][0]["path"]
    assert actual == normal
    assert type(actual) is not list


@pytest.mark.parametrize("owner", ["ref_inputs", "other", "Structured_ref_inputs", "structured-ref-inputs"])
def test_unknown_owner_is_not_a_monitor_bridge(owner):
    assert isinstance(redact_payload({owner: bridge(["bucket", "5", "count"])})[owner]["field_updates"][0]["path"], str)


@pytest.mark.parametrize("source", [None, "unknown", SOURCE + " ", 1, True, {"source": SOURCE}])
def test_wrong_source_is_not_a_monitor_bridge(source):
    value = bridge(["bucket", "5", "count"])
    if source is None:
        value.pop("source")
    else:
        value["source"] = source
    assert isinstance(redact_payload({OWNERS[0]: value})[OWNERS[0]]["field_updates"][0]["path"], str)


@pytest.mark.parametrize("parent", ["data", "Data", "payload", "payload_b64", "token", "authorization"])
def test_sensitive_parent_wins_over_forged_nested_schema(parent):
    assert redact_payload({parent: {OWNERS[0]: bridge(["bucket", "5", "count"])}})[parent] == "<redacted>"


@pytest.mark.parametrize("player_map", ["latest_bids", "player_ranks"])
def test_dynamic_player_names_cannot_supply_schema_owner_context(player_map):
    payload = {player_map: {OWNERS[0]: bridge(["bucket", "5", "count"])}}
    actual = redact_payload(payload)
    record = next(iter(actual[player_map].values()))
    assert isinstance(record["field_updates"][0]["path"], str)
    assert OWNERS[0] not in actual[player_map]


@pytest.mark.parametrize("owner", OWNERS)
def test_structural_tokens_survive_matching_player_aliases_only_in_schema(owner):
    names = ["bucket", "5", "count", SOURCE]
    payload = {
        "player_ranks": {name: {"id": "PRIVATE_" + name} for name in names},
        "latest_bids": {name: 1 for name in names},
        "note": "bucket 5 count " + SOURCE,
        "source": SOURCE,
        owner: bridge(["bucket", "5", "count"], value={"token": "PRIVATE_TOKEN", "player_name": "bucket"}),
    }
    aliases = collect_player_aliases(payload)
    actual = redact_payload(payload, player_aliases=aliases)
    assert actual[owner]["source"] == SOURCE
    row = actual[owner]["field_updates"][0]
    assert row["path"] == ["bucket", "5", "count"]
    assert row["value"] == {"token": "<redacted>", "player_name": "<redacted>"}
    assert actual["source"] == aliases[SOURCE]
    assert actual["note"] == " ".join(aliases[name] for name in names)
    assert set(actual["player_ranks"]) == {aliases[name] for name in names}
    assert all(record["id"] == "<redacted>" for record in actual["player_ranks"].values())


@pytest.mark.parametrize("suffix", [".json", ".jsonl"])
@pytest.mark.parametrize("outer", [dict, list, tuple])
def test_document_roundtrip_with_nested_owner_and_tuple_input(suffix, outer):
    payload = {OWNERS[1]: {"source": SOURCE, "field_updates": ({"path": ("bucket", "5", "count"), "value": 2},)}}
    if outer is not dict:
        payload = outer([payload])
    projected = json.loads(redact_document(json.dumps(payload) + "\n", suffix=suffix))
    record = projected if outer is dict else projected[0]
    assert record[OWNERS[1]]["field_updates"][0]["path"] == ["bucket", "5", "count"]
    direct = redact_payload(payload)
    assert direct == projected


def test_non_update_nested_paths_and_invalid_rows_keep_redacting():
    value = {"source": SOURCE, "path": ["bucket", "5", "count"], "field_updates": [
        "token=PRIVATE_TOKEN", None,
        {"path": ["bucket", "5", "count"], "nested": {"path": ["bucket", "5", "count"]}},
    ]}
    actual = redact_payload({OWNERS[0]: value})[OWNERS[0]]
    assert isinstance(actual["path"], str)
    assert actual["field_updates"][0] == "token=<redacted>"
    assert actual["field_updates"][1] is None
    assert actual["field_updates"][2]["path"] == ["bucket", "5", "count"]
    assert isinstance(actual["field_updates"][2]["nested"]["path"], str)


@pytest.fixture(scope="module")
def stabilize():
    path = Path(os.environ.get("BIDKING_META_CONSUMER_SOURCE", ROOT / "hero_ref/src/ahmad_ref_engine.py"))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {"_ENV_MISSING", "HERO_ALIASES", "ACTION_COUNTS", "ACTION_AVG_CELLS", "ACTION_TOTAL_CELLS", "ACTION_VALUE_SUM", "normalize_hero_key", "_ahmed_q1_q5_raw_context_guard_targeted", "_ahmed_current_action_structured_target", "_without_ahmed_current_action_structured_fact", "_stabilize_ahmed_current_action_publication"}
    selected = [node for node in tree.body if (isinstance(node, ast.FunctionDef) and node.name in names) or (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in names for target in node.targets))]
    assert len(selected) == len(names)
    scope = {"Any": Any}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), scope)
    return scope["_stabilize_ahmed_current_action_publication"]


def consumer_snapshot(owner, values=(2, 5), hero="ahmed"):
    return {"hero": hero, "session_id": "fixture-session", "phase": "bidding", "observed_round": 1, "action_round": 2,
        "action_result_rows": [{"action_id": 100119, "result": 5, "session_id": "fixture-session", "source_kind": "state_replay", "source_message_id": 0x25, "round": 1, "sort": 2}],
        owner: {"source": SOURCE, "counts": {"q5": 5}, "field_updates": [{"path": ["bucket", "5", "count"], "value": value, "sequence": index} for index, value in enumerate(values)]},
        "token": "PRIVATE_TOKEN", "artifact_path": "C:/Users/example/report.json"}


@pytest.mark.parametrize("owner", OWNERS)
@pytest.mark.parametrize("values", [(2, 5), (1, 2, 5), (2, 2)])
def test_support_zip_keeps_consumer_previous_fact_restore(stabilize, tmp_path, owner, values):
    original = consumer_snapshot(owner, values)
    archive_path = tmp_path / "synthetic-support.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("latest_snapshot.json", redact_document(json.dumps(original), suffix=".json"))
    with zipfile.ZipFile(archive_path) as archive:
        projected = json.loads(archive.read("latest_snapshot.json"))
    expected = stabilize(original)[owner]
    assert stabilize(projected)[owner] == expected
    assert expected["counts"] == {"q5": values[-2]}
    assert len(expected["field_updates"]) == len(values) - 1
    assert projected["token"] == "<redacted>"
    assert projected["artifact_path"] == "<local-path>/report.json"


@pytest.mark.parametrize("owner", OWNERS)
@pytest.mark.parametrize("values,hero", [((), "ahmed"), ((2, 5), "gabriela")])
def test_consumer_no_history_and_other_hero_behaviors_are_unchanged(stabilize, owner, values, hero):
    original = consumer_snapshot(owner, values, hero)
    assert stabilize(redact_payload(original))[owner] == stabilize(original)[owner]


def test_mirrored_owners_and_plain_payload_remain_compatible():
    item = bridge(["bucket", "5", "count"])
    projected = redact_payload({OWNERS[0]: item, OWNERS[1]: item})
    assert projected[OWNERS[0]] == projected[OWNERS[1]]
    assert redact_payload(item, key=OWNERS[0]) == projected[OWNERS[0]]
    assert redact_payload({"status": "ok", "data": {"count": 2}, "token": "PRIVATE"}) == {"status": "ok", "data": "<redacted>", "token": "<redacted>"}


def test_known_schema_still_redacts_activation_and_credential_values():
    activation = "v1|SYNTHETIC_FIXTURE.0123456789abcdef0123456789abcdef"
    payload = {OWNERS[0]: bridge(["bucket", "5", "count"], value=activation,
        activation_token=activation, note="Bearer PRIVATE_BEARER", payload={"password": "PRIVATE_PASSWORD"})}
    row = redact_payload(payload)[OWNERS[0]]["field_updates"][0]
    assert row["path"] == ["bucket", "5", "count"]
    assert row["value"] == "<activation-token>"
    assert row["activation_token"] == "<redacted>"
    assert row["payload"] == "<redacted>"
    assert "PRIVATE_BEARER" not in row["note"]
