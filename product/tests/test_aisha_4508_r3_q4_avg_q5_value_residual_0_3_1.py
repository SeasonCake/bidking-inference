"""Aisha 4508 R3 exact-total residual regression for 0.3.1.

Sanitized from session 4508:9000000000000040.  At the reported R3 frame the
monitor had exact q1/q3 counts and cells, total_count=54, q4 avg_cells=49/11,
and q5 total_value=388947.  The independent tight q5/q6 prior windows could
sum to at most 15 items although the exact residual required 19, so the live
product returned no_reachable_combo and rendered a blank ref_waiting card.
"""

from __future__ import annotations

import copy
import json
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_live_panel_server as panel  # noqa: E402
from ahmad_live_panel_server import (  # noqa: E402
    ServerConfig,
    _stable_action_row_sort,
    make_handler,
    summarize_snapshot,
)
from ahmad_ref_engine import (  # noqa: E402
    AISHA_Q4_AVG_Q5_VALUE_RESIDUAL_DOMAIN_DISABLE,
    AISHA_Q4_AVG_Q5_VALUE_RESIDUAL_DOMAIN_NOTE,
    Q5_EXACT_TOTAL_ACTION_INDEX_MAX,
    Q5_EXACT_TOTAL_ACTION_VALUE_MAX,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)


SESSION_ID = "4508:9000000000000040"
QUALIFICATION = "postupdate_unknown_warehouse_source"
Q4_AVG = 4.454545497894287


def _warehouse_source(qualification: str = QUALIFICATION) -> dict:
    return {
        "qualification": qualification,
        "official_distribution_eligible": qualification == "official_random_warehouse",
        "quote_policy": (
            "normal"
            if qualification == "official_random_warehouse"
            else "advisory_only_no_clamp"
        ),
    }


def _snapshot(
    *,
    hero: str = "aisha",
    phase: str = "bidding",
    qualification: str = QUALIFICATION,
) -> dict:
    source = _warehouse_source(qualification)
    action = {
        "sort": 19,
        "action_id": 100125,
        "result": 388947,
        "result_field": 12,
        "source_kind": "direct_action_response",
    }
    skill_rows = [
        {
            "sort": sort_id,
            "skill_id": skill_id,
            "hero_id": 103,
            "revealed_items": count,
            "revealed_items_detail": [
                {"quality": quality, "shape_code": 11}
                for _index in range(count)
            ],
        }
        for sort_id, skill_id, quality, count in (
            (1, 1001034, 1, 6),
            (9, 1001033, 2, 9),
            (17, 1001032, 3, 9),
        )
    ]
    return {
        "created_at": time.time(),
        "hero": hero,
        "map_id": 4508,
        "round": 3,
        "action_round": 3,
        "phase": phase,
        "session_id": SESSION_ID,
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": qualification,
        "official_distribution_eligible": source[
            "official_distribution_eligible"
        ],
        "structured_ref_inputs": {
            "source": "monitor_pre_settlement_field_updates",
            "hero": hero,
            "total_count": 54,
            "avg_cells": {"q4": Q4_AVG},
            "counts": {"q3": 9},
            "quality_cells": {"q3": 16},
            "quality_values": {"q5": 388947},
            "split_counts": {"white": 6, "green": 9},
            "split_quality_cells": {"white": 8, "green": 9},
        },
        "skill_reveal_rows": skill_rows if hero == "aisha" else [],
        "action_result_rows": [copy.deepcopy(action)],
        "ui_contract": {
            "context": {
                "hero": hero,
                "map_id": 4508,
                "round": 3,
                "phase": phase,
                "session_id": SESSION_ID,
                "warehouse_source": copy.deepcopy(source),
                "warehouse_source_qualification": qualification,
                "official_distribution_eligible": source[
                    "official_distribution_eligible"
                ],
            },
            "constraints": {"public_info": {}},
            "actions": {"results": [copy.deepcopy(action)]},
            "source": {"created_at": time.time()},
        },
    }


def _run(snapshot: dict, *, max_combos: int = 8_000) -> dict:
    return run_reference_engine(
        prepare_reference_engine_snapshot(snapshot),
        max_combos=max_combos,
    ).as_dict()


def _with_q5_value_rows(
    snapshot: dict,
    rows: list[tuple[int, int]],
    *,
    reverse: bool = False,
) -> dict:
    candidate = copy.deepcopy(snapshot)
    action_rows = []
    for sort_id, value in rows:
        row = copy.deepcopy(candidate["action_result_rows"][0])
        row.update(
            {
                "sort": sort_id,
                "result": value,
                "session_id": SESSION_ID,
                "round": 2,
            }
        )
        action_rows.append(row)
    if reverse:
        action_rows.reverse()
    candidate["action_result_rows"] = copy.deepcopy(action_rows)
    candidate["ui_contract"]["actions"]["results"] = copy.deepcopy(action_rows)
    return candidate


def _with_q5_action_field(
    snapshot: dict,
    field: str,
    value: object,
) -> dict:
    candidate = _with_q5_value_rows(snapshot, [(19, 388_947)])
    for rows in (
        candidate["action_result_rows"],
        candidate["ui_contract"]["actions"]["results"],
    ):
        rows[0][field] = value
    return candidate


def _counts(result: dict, quality: str) -> set[int]:
    return {
        int(row["value"])
        for row in result["quality_count_candidates"].get(quality, [])
    }


def _joint(result: dict, quality: str) -> set[tuple[int, int]]:
    return {
        (int(row["count"]), int(row["cells"]))
        for row in result["quality_count_cells_candidates"].get(quality, [])
    }


def _stable_engine_fields(result: dict) -> dict:
    return {
        key: result.get(key)
        for key in (
            "status",
            "conservative",
            "balanced",
            "aggressive",
            "combo_count",
            "quality_count_ranges",
            "quality_cells_ranges",
            "quality_count_candidates",
            "quality_count_cells_candidates",
            "quality_lock",
            "total_grid_range",
        )
    }


def test_4508_r3_restores_physical_residual_without_false_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    default_8k = _run(snapshot, max_combos=8_000)
    default_50k = _run(snapshot, max_combos=50_000)

    assert _stable_engine_fields(default_8k) == _stable_engine_fields(default_50k)
    assert default_8k["status"] == "count_prior"
    assert [
        default_8k["conservative"],
        default_8k["balanced"],
        default_8k["aggressive"],
    ] == [1380951, 1784077, 1967164]
    assert default_8k["combo_count"] == 25
    assert AISHA_Q4_AVG_Q5_VALUE_RESIDUAL_DOMAIN_NOTE in default_8k["notes"]
    assert (11, 49) in _joint(default_8k, "q4")
    assert {11, 13}.issubset(_counts(default_8k, "q5"))
    assert {6, 8}.issubset(_counts(default_8k, "q6"))
    assert default_8k["quality_lock"]["q4"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert default_8k["quality_lock"]["q5"]["strength"] == "probabilistic"
    assert default_8k["quality_lock"]["q6"]["strength"] == "probabilistic"

    monkeypatch.setenv(AISHA_Q4_AVG_Q5_VALUE_RESIDUAL_DOMAIN_DISABLE, "1")
    rollback = _run(snapshot)
    assert rollback["status"] == "no_reachable_combo"
    assert rollback["conservative"] is None
    assert rollback["balanced"] is None
    assert rollback["aggressive"] is None
    assert AISHA_Q4_AVG_Q5_VALUE_RESIDUAL_DOMAIN_NOTE not in rollback["notes"]


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("missing_q4_avg", None),
        ("q5_avg_cells", 3.0),
        ("q5_exact_count", 13),
        ("q6_exact_count", 6),
        ("hard_total_grid", 147),
    ],
)
def test_residual_retry_fails_closed_when_stronger_or_required_evidence_changes(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    value: object,
) -> None:
    snapshot = _snapshot()
    structured = snapshot["structured_ref_inputs"]
    if mutation == "missing_q4_avg":
        structured["avg_cells"].pop("q4")
    elif mutation == "q5_avg_cells":
        structured["avg_cells"]["q5"] = value
    elif mutation == "q5_exact_count":
        structured["counts"]["q5"] = value
    elif mutation == "q6_exact_count":
        structured["counts"]["q6"] = value
    elif mutation == "hard_total_grid":
        structured["total_cells"] = value

    default = _run(snapshot)
    monkeypatch.setenv(AISHA_Q4_AVG_Q5_VALUE_RESIDUAL_DOMAIN_DISABLE, "1")
    rollback = _run(snapshot)

    assert AISHA_Q4_AVG_Q5_VALUE_RESIDUAL_DOMAIN_NOTE not in default["notes"]
    assert _stable_engine_fields(default) == _stable_engine_fields(rollback)


@pytest.mark.parametrize(
    "qualification",
    [
        "official_random_warehouse",
        "postupdate_unknown_warehouse_source",
        "player_custom_warehouse",
    ],
)
def test_residual_retry_does_not_relabel_warehouse_source(
    qualification: str,
) -> None:
    snapshot = _snapshot(qualification=qualification)
    result = _run(snapshot)

    assert AISHA_Q4_AVG_Q5_VALUE_RESIDUAL_DOMAIN_NOTE in result["notes"]
    assert snapshot["warehouse_source"]["qualification"] == qualification
    assert (
        snapshot["warehouse_source"]["official_distribution_eligible"]
        is (qualification == "official_random_warehouse")
    )


def test_residual_retry_is_aisha_bidding_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for snapshot in (
        _snapshot(hero="ahmed"),
        _snapshot(phase="settled"),
    ):
        default = _run(snapshot)
        monkeypatch.setenv(AISHA_Q4_AVG_Q5_VALUE_RESIDUAL_DOMAIN_DISABLE, "1")
        rollback = _run(snapshot)
        monkeypatch.delenv(
            AISHA_Q4_AVG_Q5_VALUE_RESIDUAL_DOMAIN_DISABLE,
            raising=False,
        )
        assert AISHA_Q4_AVG_Q5_VALUE_RESIDUAL_DOMAIN_NOTE not in default["notes"]
        assert _stable_engine_fields(default) == _stable_engine_fields(rollback)


def test_4508_r3_product_summary_is_live_ready(tmp_path: Path) -> None:
    summary = summarize_snapshot(
        _snapshot(),
        snapshot_path=tmp_path / "latest_snapshot.json",
    )
    ref = summary["ahmed_ref"]

    assert summary["status"] == "ok"
    assert ref["status"] == "count_prior"
    assert [ref["conservative"], ref["balanced"], ref["aggressive"]] == [
        1380951,
        1784077,
        1967164,
    ]
    assert AISHA_Q4_AVG_Q5_VALUE_RESIDUAL_DOMAIN_NOTE in ref["notes"]
    assert ref["quality_lock"]["q4"]["strength"] == "probabilistic"
    assert {11, 13}.issubset(_counts(ref, "q5"))
    assert {6, 8}.issubset(_counts(ref, "q6"))


@pytest.mark.parametrize("reverse", [False, True])
def test_same_sort_conflicting_q5_total_value_fails_closed_direct_and_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reverse: bool,
) -> None:
    snapshot = _with_q5_value_rows(
        _snapshot(),
        [(19, 388_947), (19, 400_000)],
        reverse=reverse,
    )

    direct = _run(snapshot)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / f"conflict-{reverse}.json",
    )
    ref = summary["ahmed_ref"]

    assert direct["status"] == "no_reachable_combo"
    assert ref["status"] == "no_reachable_combo"
    assert direct["evidence"]["quality_values"].get("q5") is None
    assert ref["evidence"]["quality_values"].get("q5") is None
    assert "q5_exact_total_action_conflict" in direct["notes"]
    assert "q5_exact_total_action_conflict" in ref["notes"]
    assert AISHA_Q4_AVG_Q5_VALUE_RESIDUAL_DOMAIN_NOTE not in direct["notes"]
    assert AISHA_Q4_AVG_Q5_VALUE_RESIDUAL_DOMAIN_NOTE not in ref["notes"]
    assert [direct["conservative"], direct["balanced"], direct["aggressive"]] == [
        None,
        None,
        None,
    ]
    assert [ref["conservative"], ref["balanced"], ref["aggressive"]] == [
        None,
        None,
        None,
    ]

    monkeypatch.setenv(AISHA_Q4_AVG_Q5_VALUE_RESIDUAL_DOMAIN_DISABLE, "1")
    rollback = _run(snapshot)
    assert _stable_engine_fields(direct) == _stable_engine_fields(rollback)


def test_same_sort_duplicate_q5_total_value_keeps_baseline() -> None:
    baseline = _run(_snapshot())
    duplicate = _run(
        _with_q5_value_rows(
            _snapshot(),
            [(19, 388_947), (19, 388_947)],
        )
    )

    assert _stable_engine_fields(duplicate) == _stable_engine_fields(baseline)
    assert "q5_exact_total_action_conflict" not in duplicate["notes"]


@pytest.mark.parametrize(
    ("field", "foreign_value"),
    [
        ("session_id", "4508:9000000000000039"),
        ("result_field", 13),
    ],
)
def test_same_sort_q5_total_value_isolated_by_current_evidence_key(
    field: str,
    foreign_value: object,
) -> None:
    baseline = _run(_snapshot())
    candidate = _with_q5_value_rows(_snapshot(), [(19, 388_947)])
    foreign = copy.deepcopy(candidate["action_result_rows"][0])
    foreign["result"] = 400_000
    foreign[field] = foreign_value
    candidate["action_result_rows"].append(copy.deepcopy(foreign))
    candidate["ui_contract"]["actions"]["results"].append(copy.deepcopy(foreign))

    result = _run(candidate)

    assert _stable_engine_fields(result) == _stable_engine_fields(baseline)
    assert result["evidence"]["quality_values"]["q5"] == 388_947.0
    assert "q5_exact_total_action_conflict" not in result["notes"]


@pytest.mark.parametrize("reverse", [False, True])
def test_newer_sort_q5_total_value_is_order_invariant(
    reverse: bool,
) -> None:
    result = _run(
        _with_q5_value_rows(
            _snapshot(),
            [(19, 388_947), (20, 400_000)],
            reverse=reverse,
        )
    )

    assert result["status"] == "count_prior"
    assert result["evidence"]["quality_values"]["q5"] == 400_000.0
    assert "q5_exact_total_action_conflict" not in result["notes"]


@pytest.mark.parametrize("field", ["round", "sort", "result_field", "result"])
@pytest.mark.parametrize("value", [10**400, -(10**400)])
def test_unrepresentable_q5_action_numbers_fail_closed_direct_and_summary(
    tmp_path: Path,
    field: str,
    value: int,
) -> None:
    snapshot = _with_q5_action_field(_snapshot(), field, value)

    direct = _run(snapshot)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / f"unrepresentable-{field}-{value < 0}.json",
    )
    ref = summary["ahmed_ref"]

    for result in (direct, ref):
        assert result["status"] == "no_reachable_combo"
        assert result["evidence"]["quality_values"].get("q5") is None
        assert "q5_exact_total_action_conflict" in result["notes"]
        assert [
            result["conservative"],
            result["balanced"],
            result["aggressive"],
        ] == [None, None, None]


@pytest.mark.parametrize(
    ("field", "value", "accepted"),
    [
        ("round", 3, True),
        ("round", 4, False),
        ("round", Q5_EXACT_TOTAL_ACTION_INDEX_MAX, False),
        ("round", Q5_EXACT_TOTAL_ACTION_INDEX_MAX + 1, False),
        ("sort", Q5_EXACT_TOTAL_ACTION_INDEX_MAX, True),
        ("sort", Q5_EXACT_TOTAL_ACTION_INDEX_MAX + 1, False),
        ("result_field", 12, True),
        ("result_field", Q5_EXACT_TOTAL_ACTION_INDEX_MAX, True),
        ("result_field", Q5_EXACT_TOTAL_ACTION_INDEX_MAX + 1, False),
        ("result", Q5_EXACT_TOTAL_ACTION_VALUE_MAX, True),
        ("result", Q5_EXACT_TOTAL_ACTION_VALUE_MAX + 1, False),
    ],
)
def test_q5_action_numeric_boundaries_are_stable_in_direct_and_summary(
    tmp_path: Path,
    field: str,
    value: int,
    accepted: bool,
) -> None:
    snapshot = _with_q5_action_field(_snapshot(), field, value)

    direct = _run(snapshot)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / f"boundary-{field}-{value}.json",
    )
    ref = summary["ahmed_ref"]

    for result in (direct, ref):
        if accepted:
            assert result["status"] in {"count_prior", "no_reachable_combo"}
            assert "q5_exact_total_action_conflict" not in result["notes"]
        else:
            assert result["status"] == "no_reachable_combo"
            assert result["evidence"]["quality_values"].get("q5") is None
            assert "q5_exact_total_action_conflict" in result["notes"]


class _BombInt(int):
    magic_calls = 0

    def _explode(self, *_args: object) -> object:
        type(self).magic_calls += 1
        raise RuntimeError("bomb-int-protocol")

    __bool__ = _explode
    __eq__ = _explode
    __float__ = _explode
    __str__ = _explode


class _BombStr(str):
    magic_calls = 0

    def _explode(self, *_args: object) -> object:
        type(self).magic_calls += 1
        raise RuntimeError("bomb-str-protocol")

    __bool__ = _explode
    __eq__ = _explode
    __float__ = _explode
    __str__ = _explode


class _BombFloat(float):
    magic_calls = 0

    def _explode(self, *_args: object) -> object:
        type(self).magic_calls += 1
        raise RuntimeError("bomb-float-protocol")

    __bool__ = _explode
    __eq__ = _explode
    __float__ = _explode
    __str__ = _explode


@pytest.mark.parametrize(
    ("bomb_type", "raw_value"),
    [
        pytest.param(_BombInt, 19, id="int-subclass"),
        pytest.param(_BombStr, "19", id="str-subclass"),
        pytest.param(_BombFloat, 19.0, id="float-subclass"),
    ],
)
def test_q5_action_sort_subclasses_fail_closed_without_user_protocols(
    tmp_path: Path,
    bomb_type: type,
    raw_value: object,
) -> None:
    bomb_type.magic_calls = 0
    value = bomb_type(raw_value)
    snapshot = _with_q5_action_field(_snapshot(), "sort", value)

    direct = _run(snapshot)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / f"bomb-sort-{bomb_type.__name__}.json",
    )
    ref = summary["ahmed_ref"]

    assert summary["status"] == "ok"
    for result in (direct, ref):
        assert result["status"] == "no_reachable_combo"
        assert result["evidence"]["quality_values"].get("q5") is None
        assert "q5_exact_total_action_conflict" in result["notes"]
        assert [
            result["conservative"],
            result["balanced"],
            result["aggressive"],
        ] == [None, None, None]
    assert bomb_type.magic_calls == 0


@pytest.mark.parametrize(
    "raw_sort",
    [
        pytest.param("\u00a019", id="leading-nbsp"),
        pytest.param("19\u00a0", id="trailing-nbsp"),
        pytest.param("\u200319\u2003", id="em-space"),
        pytest.param("\u200919\u2009", id="thin-space"),
        pytest.param("\u202f19\u202f", id="narrow-nbsp"),
        pytest.param("\u200b19", id="zero-width-leading"),
        pytest.param("19\u200b", id="zero-width-trailing"),
        pytest.param("\uff11\uff19", id="fullwidth-digits"),
        pytest.param("1\u0669", id="mixed-digit-script"),
        pytest.param("\x0019", id="nul-control"),
        pytest.param("19\x7f", id="del-control"),
        pytest.param((" " * 32) + "19", id="overlength-before-trim"),
    ],
)
def test_non_ascii_or_unsafe_q5_action_sort_fails_closed_across_products(
    tmp_path: Path,
    raw_sort: str,
) -> None:
    snapshot = _with_q5_action_field(_snapshot(), "sort", raw_sort)

    direct = _run(snapshot)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "unsafe-sort.json",
    )
    ref = summary["ahmed_ref"]

    assert _stable_action_row_sort(raw_sort) is None
    assert summary["status"] == "ok"
    for result in (direct, ref):
        assert result["status"] == "no_reachable_combo"
        assert result["evidence"]["quality_values"].get("q5") is None
        assert "q5_exact_total_action_conflict" in result["notes"]
        assert [
            result["conservative"],
            result["balanced"],
            result["aggressive"],
        ] == [None, None, None]


@pytest.mark.parametrize(
    ("raw_sort", "expected"),
    [
        pytest.param("19", 19, id="plain-integer"),
        pytest.param(" 19 ", 19, id="ascii-space"),
        pytest.param("+19", 19, id="signed-integer"),
        pytest.param(19, 19, id="exact-int"),
        pytest.param(19.0, 19, id="exact-integral-float"),
    ],
)
def test_safe_q5_action_sort_boundaries_keep_baseline(
    raw_sort: object,
    expected: int,
) -> None:
    baseline = _run(_snapshot())
    candidate = _run(_with_q5_action_field(_snapshot(), "sort", raw_sort))

    assert _stable_action_row_sort(raw_sort) == expected
    assert _stable_engine_fields(candidate) == _stable_engine_fields(baseline)


class _BombSortDict(dict):
    magic_calls = 0

    @classmethod
    def _explode(cls, *_args: object, **_kwargs: object) -> object:
        cls.magic_calls += 1
        raise RuntimeError("action-sort-dict-protocol")

    items = _explode
    __iter__ = _explode
    __len__ = _explode
    __contains__ = _explode
    __str__ = _explode
    __bool__ = _explode


class _BombSortMapping(Mapping):
    magic_calls = 0

    @classmethod
    def _explode(cls, *_args: object, **_kwargs: object) -> object:
        cls.magic_calls += 1
        raise RuntimeError("action-sort-mapping-protocol")

    def __getitem__(self, _key: object) -> object:
        return self._explode()

    def __iter__(self) -> Iterator[object]:
        return self._explode()  # type: ignore[return-value]

    def __len__(self) -> int:
        return self._explode()  # type: ignore[return-value]

    def __str__(self) -> str:
        return self._explode()  # type: ignore[return-value]

    def __bool__(self) -> bool:
        return self._explode()  # type: ignore[return-value]


class _CollisionSortKey(str):
    magic_calls = 0
    armed = False

    def __eq__(self, other: object) -> bool:
        if self.armed:
            type(self).magic_calls += 1
            raise RuntimeError("action-sort-collision-key")
        return str.__eq__(self, other)

    __hash__ = str.__hash__


def _with_untrusted_q5_action_sort(raw_sort: object) -> dict:
    candidate = _with_q5_value_rows(_snapshot(), [(19, 388_947)])
    candidate["action_result_rows"][0]["sort"] = raw_sort
    candidate["ui_contract"]["actions"]["results"][0]["sort"] = raw_sort
    return candidate


@pytest.mark.parametrize(
    "raw_sort",
    [
        pytest.param(_BombSortDict({"nested": 19}), id="bomb-dict"),
        pytest.param(_BombSortMapping(), id="mapping"),
        pytest.param({"nested": 19}, id="builtin-dict"),
        pytest.param(
            {_CollisionSortKey("nested"): 19},
            id="collision-key",
        ),
    ],
)
def test_q5_action_sort_container_is_projected_before_summary_json_clone(
    tmp_path: Path,
    raw_sort: object,
) -> None:
    _BombSortDict.magic_calls = 0
    _BombSortMapping.magic_calls = 0
    _CollisionSortKey.magic_calls = 0
    for key in raw_sort if type(raw_sort) is dict else ():  # noqa: E721
        if type(key) is _CollisionSortKey:  # noqa: E721
            key.armed = True
    snapshot = _with_untrusted_q5_action_sort(raw_sort)

    direct = _run(snapshot)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "untrusted-action-sort.json",
    )

    assert summary["status"] == "ok"
    for result in (direct, summary["ahmed_ref"]):
        assert result["status"] == "no_reachable_combo"
        assert result["evidence"]["quality_values"].get("q5") is None
        assert "q5_exact_total_action_conflict" in result["notes"]
        assert [
            result["conservative"],
            result["balanced"],
            result["aggressive"],
        ] == [None, None, None]
    assert _BombSortDict.magic_calls == 0
    assert _BombSortMapping.magic_calls == 0
    assert _CollisionSortKey.magic_calls == 0


def test_q5_action_sort_bomb_dict_keeps_loopback_api_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _BombSortDict.magic_calls = 0
    snapshot = _with_untrusted_q5_action_sort(_BombSortDict({"nested": 19}))
    monkeypatch.setattr(panel, "_read_json", lambda _path: snapshot)
    config = ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    try:
        with urlopen(  # noqa: S310 - loopback-only product regression
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

    assert status == 200
    assert payload["status"] == "ok"
    assert payload["ahmed_ref"]["status"] == "no_reachable_combo"
    assert payload["ahmed_ref"]["evidence"]["quality_values"].get("q5") is None
    assert "q5_exact_total_action_conflict" in payload["ahmed_ref"]["notes"]
    assert _BombSortDict.magic_calls == 0


class _BombActionRowsList(list):
    magic_calls = 0

    @classmethod
    def _explode(cls, *_args: object, **_kwargs: object) -> object:
        cls.magic_calls += 1
        raise RuntimeError("action-rows-list-protocol")

    __iter__ = _explode
    __len__ = _explode
    __bool__ = _explode
    __getitem__ = _explode


class _BombActionRowsDict(dict):
    magic_calls = 0

    @classmethod
    def _explode(cls, *_args: object, **_kwargs: object) -> object:
        cls.magic_calls += 1
        raise RuntimeError("action-rows-dict-protocol")

    get = _explode
    items = _explode
    __iter__ = _explode
    __len__ = _explode
    __bool__ = _explode
    __contains__ = _explode


def _with_collision_sort_row_key() -> tuple[dict, list[_CollisionSortKey]]:
    snapshot = _with_q5_value_rows(_snapshot(), [(19, 388_947)])
    collision_keys: list[_CollisionSortKey] = []
    for rows in (
        snapshot["action_result_rows"],
        snapshot["ui_contract"]["actions"]["results"],
    ):
        row = rows[0]
        raw_sort = row.pop("sort")
        collision = _CollisionSortKey("sort")
        row[collision] = raw_sort
        collision.armed = True
        collision_keys.append(collision)
    return snapshot, collision_keys


def test_q5_action_row_collision_key_is_atomic_across_products(
    tmp_path: Path,
) -> None:
    _CollisionSortKey.magic_calls = 0
    snapshot, collision_keys = _with_collision_sort_row_key()

    direct = _run(snapshot)
    prepared = panel._aisha_prepare_pass_snapshot(
        snapshot,
        pass_kind=panel.AISHA_ENGINE_PASS_ITEM,
        round_no=3,
    )
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "collision-row-key.json",
    )

    assert prepared["action_result_rows"][0]["sort"] == (
        panel._ACTION_ROW_SORT_JSON_CONFLICT
    )
    for result in (direct, summary["ahmed_ref"]):
        assert result["status"] == "no_reachable_combo"
        assert result["evidence"]["quality_values"].get("q5") is None
        assert "q5_exact_total_action_conflict" in result["notes"]
        assert [
            result["conservative"],
            result["balanced"],
            result["aggressive"],
        ] == [None, None, None]
    assert all(key.magic_calls == 0 for key in collision_keys)
    assert _CollisionSortKey.magic_calls == 0


@pytest.mark.parametrize(
    "outer_rows",
    [
        pytest.param(_BombActionRowsList(), id="bomb-list"),
        pytest.param(_BombActionRowsDict(), id="bomb-dict"),
    ],
)
def test_q5_action_outer_container_is_atomic_without_user_protocols(
    tmp_path: Path,
    outer_rows: object,
) -> None:
    _BombActionRowsList.magic_calls = 0
    _BombActionRowsDict.magic_calls = 0
    snapshot = _snapshot()
    snapshot["action_result_rows"] = outer_rows
    snapshot["ui_contract"]["actions"]["results"] = outer_rows

    direct = _run(snapshot)
    prepared = prepare_reference_engine_snapshot(snapshot)
    summary = summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "outer-action-rows.json",
    )

    assert type(prepared["action_result_rows"]) is list  # noqa: E721
    for result in (direct, summary["ahmed_ref"]):
        assert result["status"] == "no_reachable_combo"
        assert result["evidence"]["quality_values"].get("q5") is None
        assert "q5_exact_total_action_conflict" in result["notes"]
    assert _BombActionRowsList.magic_calls == 0
    assert _BombActionRowsDict.magic_calls == 0


def test_q5_action_collision_key_keeps_loopback_api_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _CollisionSortKey.magic_calls = 0
    snapshot, collision_keys = _with_collision_sort_row_key()
    monkeypatch.setattr(panel, "_read_json", lambda _path: snapshot)
    config = ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    try:
        with urlopen(  # noqa: S310 - loopback-only product regression
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

    assert status == 200
    assert payload["status"] == "ok"
    assert payload["ahmed_ref"]["status"] == "no_reachable_combo"
    assert payload["ahmed_ref"]["evidence"]["quality_values"].get("q5") is None
    assert "q5_exact_total_action_conflict" in payload["ahmed_ref"]["notes"]
    assert all(key.magic_calls == 0 for key in collision_keys)
    assert _CollisionSortKey.magic_calls == 0
