from __future__ import annotations

import copy
import json
import math
import os
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ahmad_ref_engine import run_reference_engine  # noqa: E402
from detail_composition_view import (  # noqa: E402
    QUALITY_COMPOSITION_SCHEMA_VERSION,
    detail_composition_view,
)
from test_live_overlay import _ahmad_overlay_module, _ahmad_server_module  # noqa: E402


tk = pytest.importorskip("tkinter")

Q1_AVG_CELLS = 1.2222222089767456
Q1_AVG_CELLS_FLOAT32_LE = "c7719c3f"


def _cloud_support_snapshot(
    *,
    include_q1_cells: bool,
    map_id: int = 4508,
    session_id: str = "4508:support-safe",
) -> dict[str, object]:
    """Sanitized bidding inputs from the 2026-08-10 production support row.

    The cloud row contains no complete raw timeline.  This fixture therefore
    preserves only the published ref inputs and Float32 payload, and deliberately
    excludes settlement truth from inference.
    """
    round_no = 4 if include_q1_cells else 3
    created_at = time.time()
    actions: list[dict[str, object]] = [
        {"action_id": 100117, "result": 5},
        {
            "action_id": 100110,
            "result": Q1_AVG_CELLS,
            "result_raw_hex": Q1_AVG_CELLS_FLOAT32_LE,
        },
    ]
    structured: dict[str, object] = {
        "hero": "ahmed",
        "total_count": 41,
        "fixed_counts": {"q3": 5},
        "avg_cells": {
            "q1": Q1_AVG_CELLS,
            "q3": 1.600000023841858,
            "q4": 2.5714285373687744,
            "q5": 4.181818008422852,
        },
    }
    if include_q1_cells:
        actions.append({"action_id": 100104, "result": 11})
        structured["quality_cells"] = {"q1": 11}

    context = {
        "hero": "ahmed",
        "map_id": map_id,
        "round": round_no,
        "action_round": round_no,
        "phase": "bidding",
        "session_id": session_id,
    }
    return {
        "created_at": created_at,
        **context,
        "ui_contract": {
            "context": copy.deepcopy(context),
            "actions": {"results": actions},
            "source": {"created_at": created_at},
        },
        "structured_ref_inputs": structured,
        "skill_reveals": [
            {"hero_id": 204, "skill_id": 100204, "result": 41},
            {
                "hero_id": 204,
                "skill_id": 1002041,
                "result": 4.181818008422852,
            },
            {
                "hero_id": 204,
                "skill_id": 1002042,
                "result": 2.5714285373687744,
            },
            {
                "hero_id": 204,
                "skill_id": 1002043,
                "result": 1.600000023841858,
            },
        ],
    }


def _summarize(
    module,
    tmp_path: Path,
    *,
    include_q1_cells: bool,
    map_id: int = 4508,
    session_id: str = "4508:support-safe",
) -> dict[str, object]:
    return module.summarize_snapshot(
        _cloud_support_snapshot(
            include_q1_cells=include_q1_cells,
            map_id=map_id,
            session_id=session_id,
        ),
        snapshot_path=tmp_path / "latest_snapshot.json",
    )


def _unrelated_current_conflict(summary: dict[str, object]) -> dict[str, object]:
    current = copy.deepcopy(summary)
    current["quality_composition"] = {
        "status": "conflict",
        "reason": "结构化组合字段冲突",
        "qualities": {},
    }
    current["flags"] = [
        {
            "label": "组合更新冲突",
            "level": "danger",
            "detail": "当前非白绿品质结构化组合不可验证",
        }
    ]
    return current


def _q6_unavailable(summary: dict[str, object]) -> dict[str, object]:
    current = copy.deepcopy(summary)
    current["quality_composition"] = {"status": "unavailable"}
    current["flags"] = [
        {
            "label": "红品更新中",
            "level": "watch",
            "detail": "当前帧尚无 q6 联合候选",
        }
    ]
    return current


def _summarize_unrelated_q6_conflict(
    module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict[str, object]:
    snapshot = _cloud_support_snapshot(include_q1_cells=True)
    raw = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    raw["quality_count_cells_candidates"]["q6"].append(
        copy.deepcopy(raw["quality_count_cells_candidates"]["q6"][0])
    )

    class FakeRefResult:
        def as_dict(self) -> dict[str, object]:
            return copy.deepcopy(raw)

    monkeypatch.setattr(
        module,
        "run_reference_engine",
        lambda _snapshot, **_kwargs: FakeRefResult(),
    )
    monkeypatch.setattr(
        module,
        "hero_should_run_scheduled_inference",
        lambda *_args, **_kwargs: False,
    )
    return module.summarize_snapshot(
        snapshot,
        snapshot_path=tmp_path / "latest_snapshot.json",
    )


def _summarize_stale_unrelated_q6_conflict(
    module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict[str, object]:
    stale_time = time.time() - module.STALE_SNAPSHOT_SECONDS - 5.0
    snapshot = _cloud_support_snapshot(include_q1_cells=True)
    snapshot["created_at"] = stale_time
    snapshot["ui_contract"]["source"]["created_at"] = stale_time
    raw = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    raw["quality_count_cells_candidates"]["q6"].append(
        copy.deepcopy(raw["quality_count_cells_candidates"]["q6"][0])
    )

    class FakeRefResult:
        def as_dict(self) -> dict[str, object]:
            return copy.deepcopy(raw)

    monkeypatch.setattr(
        module,
        "run_reference_engine",
        lambda _snapshot, **_kwargs: FakeRefResult(),
    )
    monkeypatch.setattr(
        module,
        "hero_should_run_scheduled_inference",
        lambda *_args, **_kwargs: False,
    )
    snapshot_path = tmp_path / "latest_snapshot.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    os.utime(snapshot_path, (stale_time, stale_time))
    return module.summarize_snapshot(snapshot, snapshot_path=snapshot_path)


def _new_root(attempts: int = 4):
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return tk.Tk()
        except tk.TclError as exc:  # noqa: PERF203 - bounded environment retry
            last = exc
    pytest.skip(f"real Tk unavailable after {attempts} attempts: {last}")


def test_cloud_support_inputs_lock_q1_in_engine_summary_and_loopback_api(
    tmp_path: Path,
) -> None:
    module = _ahmad_server_module()
    snapshot = _cloud_support_snapshot(include_q1_cells=True)
    snapshot_path = tmp_path / "api" / "latest_snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    engine = run_reference_engine(snapshot, max_combos=8_000).as_dict()
    assert engine["status"] == "ok"
    assert engine["combo_count"] == 2
    assert engine["quality_count_ranges"]["q1"] == [9, 9, 9]
    assert engine["quality_cells_ranges"]["q1"] == [11, 11, 11]
    assert engine["quality_count_cells_candidates"]["q1"] == [
        {"count": 9, "cells": 11, "prob": 1.0}
    ]

    direct = module.summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    assert direct["red"]["quality_joint_exact_facts"]["q1"] == {
        "count": 9,
        "cells": 11,
        "source": "joint_exact",
    }
    assert direct["quality_composition"]["qualities"]["q1"][
        "dimension_state"
    ] == "锁定"
    assert direct["truth"]["available"] is False

    config = module.ServerConfig(project_root=tmp_path, snapshot=snapshot_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.make_handler(config))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            assert response.status == 200
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert payload["red"]["quality_joint_exact_facts"] == direct["red"][
        "quality_joint_exact_facts"
    ]
    assert payload["quality_composition"]["qualities"]["q1"] == direct[
        "quality_composition"
    ]["qualities"]["q1"]
    assert "function exactLowQualityFacts(red)" in module.INDEX_HTML
    assert "red.quality_joint_exact_facts" in module.INDEX_HTML
    assert "当前硬事实" in module.INDEX_HTML


def test_parent_continuity_is_known_fail_and_default_replaces_only_current_exact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    server = _ahmad_server_module()
    overlay_module = _ahmad_overlay_module()
    cached_r3 = _summarize(server, tmp_path / "r3", include_q1_cells=False)
    current_r4 = _unrelated_current_conflict(
        _summarize(server, tmp_path / "r4", include_q1_cells=True)
    )
    assert overlay_module._strict_quote_summary_session_id(cached_r3) == (
        "4508:support-safe"
    )
    assert overlay_module._strict_quote_summary_session_id(current_r4) == (
        "4508:support-safe"
    )

    old_q1 = cached_r3["quality_composition"]["qualities"]["q1"]
    assert [(row["count"], row["cells"]) for row in old_q1["candidates"]] == [
        (9, 11),
        (18, 22),
    ]
    assert old_q1["dimension_state"] == "未锁"

    overlay = object.__new__(overlay_module.AhmadTkOverlay)
    assert overlay._summary_with_quality_continuity(cached_r3) is cached_r3
    monkeypatch.setenv(
        overlay_module.CURRENT_JOINT_EXACT_CONTINUITY_DISABLE_ENV,
        "1",
    )
    parent = overlay._summary_with_quality_continuity(current_r4)
    parent_q1 = detail_composition_view(parent).quality("q1")
    assert [(row.count, row.cells) for row in parent_q1.candidates] == [
        (9, 11),
        (18, 22),
    ]
    assert parent_q1.dimension_state == "未锁"
    assert parent["continuity"] == {
        "reason": "quality_composition_conflict",
        "source_round": "3",
    }

    monkeypatch.delenv(
        overlay_module.CURRENT_JOINT_EXACT_CONTINUITY_DISABLE_ENV,
        raising=False,
    )
    fixed = overlay._summary_with_quality_continuity(current_r4)
    fixed_q1 = detail_composition_view(fixed).quality("q1")
    assert [(row.count, row.cells, row.probability) for row in fixed_q1.candidates] == [
        (9, 11, 1.0)
    ]
    assert fixed_q1.dimension_state == "锁定"
    assert fixed["reference"] == cached_r3["reference"]
    assert fixed["quality_composition"]["qualities"]["q4"] == cached_r3[
        "quality_composition"
    ]["qualities"]["q4"]
    assert fixed["continuity"] == {
        "reason": "quality_composition_conflict",
        "source_round": "3",
        "current_joint_exact_qualities": ["q1", "q3", "q5"],
    }
    assert fixed["flags"][1]["label"] == "组合更新冲突"


def test_q6_r3_to_r4_single_unavailable_frame_keeps_one_bounded_view(
    tmp_path: Path,
) -> None:
    server = _ahmad_server_module()
    overlay_module = _ahmad_overlay_module()
    cached_r3 = _summarize(server, tmp_path / "r3", include_q1_cells=False)
    current_r4 = _q6_unavailable(
        _summarize(server, tmp_path / "r4", include_q1_cells=True)
    )
    overlay = object.__new__(overlay_module.AhmadTkOverlay)

    assert overlay._summary_with_quality_continuity(cached_r3) is cached_r3
    first = overlay._summary_with_quality_continuity(current_r4)
    assert first is not current_r4
    assert first["context"]["round"] == 4
    assert first["reference"] == cached_r3["reference"]
    assert first["continuity"] == {
        "reason": "quality_q6_next_round_pending",
        "source_round": "3",
    }
    assert first["flags"][0]["label"] == "沿用R3"
    assert "仅本帧" in first["flags"][0]["detail"]
    assert detail_composition_view(first).quality("q6").status in {
        "ready",
        "exact",
    }

    # The same bad identity gets exactly one continuity render.
    second = overlay._summary_with_quality_continuity(copy.deepcopy(current_r4))
    assert second["quality_composition"] == {"status": "unavailable"}
    assert "continuity" not in second

    # A verified R4 clears the one-frame marker and becomes the new authority.
    recovered_r4 = _summarize(
        server,
        tmp_path / "recovered-r4",
        include_q1_cells=True,
    )
    assert overlay._summary_with_quality_continuity(recovered_r4) is recovered_r4
    assert overlay._quality_unavailable_continuity_identity is None


@pytest.mark.parametrize(
    "case",
    [
        "same_round",
        "jump_round",
        "other_map",
        "other_session",
        "round_bool",
        "map_subclass",
        "settled",
        "stale",
        "ref_error",
    ],
)
def test_q6_next_round_continuity_fails_closed_outside_exact_scope(
    case: str,
    tmp_path: Path,
) -> None:
    server = _ahmad_server_module()
    overlay_module = _ahmad_overlay_module()
    cached_r3 = _summarize(server, tmp_path / "r3", include_q1_cells=False)
    current = _q6_unavailable(
        _summarize(server, tmp_path / "r4", include_q1_cells=True)
    )
    context = current["context"]
    assert isinstance(context, dict)
    if case == "same_round":
        context["round"] = 3
    elif case == "jump_round":
        context["round"] = 5
    elif case == "other_map":
        context["map_id"] = 4509
    elif case == "other_session":
        context["session_id"] = "4508:other"
    elif case == "round_bool":
        context["round"] = True
    elif case == "map_subclass":
        class MapId(int):
            pass

        context["map_id"] = MapId(4508)
    elif case == "settled":
        context["phase"] = "settled"
    elif case == "stale":
        current["stale"] = {"reason": "stale_snapshot"}
    elif case == "ref_error":
        current["ahmed_ref"] = {"status": "error"}
    else:  # pragma: no cover - selector table is exhaustive
        raise AssertionError(case)

    overlay = object.__new__(overlay_module.AhmadTkOverlay)
    overlay._summary_with_quality_continuity(cached_r3)
    displayed = overlay._summary_with_quality_continuity(current)
    assert displayed is current
    assert "continuity" not in displayed


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "empty",
        "bool",
        "number",
        "string_subclass",
        "root_only",
        "whitespace",
    ],
)
def test_fresh_and_conflict_continuity_reject_invalid_context_session_authority(
    case: str,
    tmp_path: Path,
) -> None:
    server = _ahmad_server_module()
    overlay_module = _ahmad_overlay_module()
    cached = _summarize(server, tmp_path / "cached", include_q1_cells=False)
    fresh = _summarize(server, tmp_path / "fresh", include_q1_cells=True)
    current = _unrelated_current_conflict(copy.deepcopy(fresh))

    def invalidate(summary: dict[str, object]) -> None:
        context = summary["context"]
        assert isinstance(context, dict)
        summary["session_id"] = "4508:support-safe"
        if case == "missing":
            context.pop("session_id")
            summary.pop("session_id")
        elif case == "empty":
            context["session_id"] = ""
        elif case == "bool":
            context["session_id"] = True
        elif case == "number":
            context["session_id"] = 4508
        elif case == "string_subclass":
            class SessionId(str):
                pass

            context["session_id"] = SessionId("4508:support-safe")
        elif case == "root_only":
            context.pop("session_id")
        elif case == "whitespace":
            context["session_id"] = " 4508:support-safe "
        else:  # pragma: no cover - selector table is exhaustive
            raise AssertionError(case)

    invalidate(fresh)
    invalidate(current)
    assert overlay_module._strict_quote_summary_session_id(fresh) is None
    assert overlay_module._strict_quote_summary_session_id(current) is None

    # Invalid fresh input may render statelessly, but cannot seed continuity.
    fresh_overlay = object.__new__(overlay_module.AhmadTkOverlay)
    assert fresh_overlay._summary_with_quality_continuity(fresh) is fresh
    assert not getattr(fresh_overlay, "_last_verified_quality_summary", {})
    assert not getattr(fresh_overlay, "_last_verified_quality_session_id", "")

    # A prior valid nested-session cache cannot make an invalid current frame
    # consume the additive exact facts, even when the root ID looks identical.
    overlay = object.__new__(overlay_module.AhmadTkOverlay)
    assert overlay._summary_with_quality_continuity(cached) is cached
    displayed = overlay._summary_with_quality_continuity(current)
    assert displayed is current
    assert "continuity" not in displayed
    assert detail_composition_view(displayed).status == "conflict"
    assert overlay._last_verified_quality_summary is cached
    assert overlay._last_verified_quality_session_id == "4508:support-safe"


def test_server_kill_restores_parent_summary_shape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _ahmad_server_module()
    snapshot = _cloud_support_snapshot(include_q1_cells=True)
    snapshot_path = tmp_path / "latest_snapshot.json"
    default = module.summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    expected_parent = copy.deepcopy(default)
    expected_parent["red"].pop("quality_joint_exact_facts")

    monkeypatch.setenv(module.CURRENT_JOINT_EXACT_CONTINUITY_DISABLE_ENV, "1")
    killed = module.summarize_snapshot(snapshot, snapshot_path=snapshot_path)

    for payload in (expected_parent, killed):
        payload["diagnostics"].pop("performance")
    assert killed == expected_parent


def test_joint_fact_projection_survives_unrelated_tier_conflict_but_not_q1_error(
    tmp_path: Path,
) -> None:
    module = _ahmad_server_module()
    raw = run_reference_engine(
        _cloud_support_snapshot(include_q1_cells=True),
        max_combos=8_000,
    ).as_dict()
    duplicate_q6 = copy.deepcopy(raw)
    duplicate_q6["quality_count_cells_candidates"]["q6"].append(
        copy.deepcopy(duplicate_q6["quality_count_cells_candidates"]["q6"][0])
    )
    facts = module._quality_joint_exact_facts(duplicate_q6)
    assert facts["q1"] == {"count": 9, "cells": 11, "source": "joint_exact"}
    assert module._quality_composition_payload(duplicate_q6)["status"] == "conflict"

    cases: list[dict[str, object]] = []
    duplicate_q1 = copy.deepcopy(raw)
    duplicate_q1["quality_count_cells_candidates"]["q1"].append(
        copy.deepcopy(duplicate_q1["quality_count_cells_candidates"]["q1"][0])
    )
    cases.append(duplicate_q1)

    for value in (-1, True, "9"):
        malformed = copy.deepcopy(raw)
        malformed["quality_count_cells_candidates"]["q1"][0]["count"] = value
        cases.append(malformed)
    for value in (math.nan, math.inf, -math.inf, 0.999999):
        malformed = copy.deepcopy(raw)
        malformed["quality_count_cells_candidates"]["q1"][0]["prob"] = value
        cases.append(malformed)

    range_mismatch = copy.deepcopy(raw)
    range_mismatch["quality_cells_ranges"]["q1"] = [11, 11, 12]
    cases.append(range_mismatch)
    evidence_mismatch = copy.deepcopy(raw)
    evidence_mismatch["evidence"]["quality_cells"]["q1"] = 12
    cases.append(evidence_mismatch)
    marginal_mismatch = copy.deepcopy(raw)
    marginal_mismatch["quality_count_candidates"]["q1"] = [
        {"value": 18, "prob": 1.0}
    ]
    cases.append(marginal_mismatch)
    lock_mismatch = copy.deepcopy(raw)
    lock_mismatch["quality_lock"]["q1"]["reason"] = "count_only"
    cases.append(lock_mismatch)

    for malformed in cases:
        assert "q1" not in module._quality_joint_exact_facts(malformed)


def test_panel_root_conflict_keeps_q1_fact_without_publishing_partial_composition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _ahmad_server_module()
    summary = _summarize_unrelated_q6_conflict(module, monkeypatch, tmp_path)

    assert summary["quality_composition"] == {
        "schema_version": QUALITY_COMPOSITION_SCHEMA_VERSION,
        "status": "conflict",
        "reason": "结构化组合字段冲突",
        "qualities": {},
    }
    assert summary["ahmed_ref"] == {
        "status": "conflict",
        "source": "ref_v0",
        "notes": ["quality_composition_conflict"],
    }
    assert summary["red"]["quality_joint_exact_facts"]["q1"] == {
        "count": 9,
        "cells": 11,
        "source": "joint_exact",
    }
    assert summary["reference"]["conservative"] == "-"
    assert summary["reference"]["balanced"] == "-"
    assert summary["reference"]["aggressive"] == "-"


def test_age_stale_keeps_old_quote_without_publishing_or_consuming_current_fact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    server = _ahmad_server_module()
    overlay_module = _ahmad_overlay_module()
    cached = _summarize(server, tmp_path / "cached", include_q1_cells=False)
    current = _summarize_stale_unrelated_q6_conflict(
        server,
        monkeypatch,
        tmp_path / "current",
    )

    assert current["status"] == "ok"
    assert current["stale"]["keep_last_quote"] is True
    assert "quality_joint_exact_facts" not in current["red"]

    # Even a malformed or older server cannot make Tk consume an additive fact
    # once the summary declares itself stale.
    current["red"]["quality_joint_exact_facts"] = {
        "q1": {"count": 9, "cells": 11, "source": "joint_exact"}
    }
    overlay = object.__new__(overlay_module.AhmadTkOverlay)
    assert overlay._summary_with_quality_continuity(cached) is cached
    displayed = overlay._summary_with_quality_continuity(current)
    q1 = detail_composition_view(displayed).quality("q1")
    assert [(row.count, row.cells) for row in q1.candidates] == [
        (9, 11),
        (18, 22),
    ]
    assert q1.dimension_state == "未锁"
    assert displayed["reference"] == cached["reference"]
    assert "current_joint_exact_qualities" not in displayed["continuity"]

    monkeypatch.setenv(server.CURRENT_JOINT_EXACT_CONTINUITY_DISABLE_ENV, "1")
    killed = _summarize_stale_unrelated_q6_conflict(
        server,
        monkeypatch,
        tmp_path / "killed",
    )
    assert "quality_joint_exact_facts" not in killed["red"]


def test_continuity_rejects_malformed_facts_and_non_live_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    server = _ahmad_server_module()
    overlay_module = _ahmad_overlay_module()
    cached = _summarize(server, tmp_path / "cached", include_q1_cells=False)
    current = _unrelated_current_conflict(
        _summarize(server, tmp_path / "current", include_q1_cells=True)
    )

    malformed_values = (
        {"count": True, "cells": 11, "source": "joint_exact"},
        {"count": 9, "cells": -1, "source": "joint_exact"},
        {"count": 9, "cells": 11, "source": "legacy_range"},
        {"count": 9, "cells": 11, "source": "joint_exact", "extra": 1},
    )
    for raw_fact in malformed_values:
        overlay = object.__new__(overlay_module.AhmadTkOverlay)
        overlay._summary_with_quality_continuity(cached)
        malformed = copy.deepcopy(current)
        malformed["red"]["quality_joint_exact_facts"] = {"q1": raw_fact}
        displayed = overlay._summary_with_quality_continuity(malformed)
        assert "current_joint_exact_qualities" not in displayed["continuity"]
        assert detail_composition_view(displayed).quality("q1").dimension_state == "未锁"

    overlay = object.__new__(overlay_module.AhmadTkOverlay)
    overlay._summary_with_quality_continuity(cached)
    new_session = copy.deepcopy(current)
    new_session["context"]["session_id"] = "4508:new-session"
    assert overlay._summary_with_quality_continuity(new_session) is new_session

    overlay = object.__new__(overlay_module.AhmadTkOverlay)
    overlay._summary_with_quality_continuity(cached)
    settled = copy.deepcopy(current)
    settled["context"]["phase"] = "settled"
    assert overlay._summary_with_quality_continuity(settled) is settled

    overlay = object.__new__(overlay_module.AhmadTkOverlay)
    overlay._summary_with_quality_continuity(cached)
    ref_error = copy.deepcopy(current)
    ref_error["ahmed_ref"] = {"status": "error"}
    displayed = overlay._summary_with_quality_continuity(ref_error)
    assert displayed["continuity"] == {
        "reason": "ref_engine_transient_error",
        "source_round": "3",
    }
    assert detail_composition_view(displayed).quality("q1").dimension_state == "未锁"

    monkeypatch.setenv(
        overlay_module.CURRENT_JOINT_EXACT_CONTINUITY_DISABLE_ENV,
        "1",
    )
    overlay = object.__new__(overlay_module.AhmadTkOverlay)
    overlay._summary_with_quality_continuity(cached)
    killed = overlay._summary_with_quality_continuity(current)
    assert "current_joint_exact_qualities" not in killed["continuity"]
    assert detail_composition_view(killed).quality("q1").dimension_state == "未锁"


def test_source_external_quality_fact_is_generic_and_not_map_hardcoded(
    tmp_path: Path,
) -> None:
    server = _ahmad_server_module()
    overlay_module = _ahmad_overlay_module()
    cached = _summarize(
        server,
        tmp_path / "cached",
        include_q1_cells=False,
        map_id=2510,
        session_id="2510:external-control",
    )
    current = _unrelated_current_conflict(
        _summarize(
            server,
            tmp_path / "current",
            include_q1_cells=True,
            map_id=2510,
            session_id="2510:external-control",
        )
    )
    current["red"]["quality_joint_exact_facts"] = {
        "q3": {"count": 5, "cells": 8, "source": "joint_exact"}
    }

    overlay = object.__new__(overlay_module.AhmadTkOverlay)
    overlay._summary_with_quality_continuity(cached)
    displayed = overlay._summary_with_quality_continuity(current)
    assert displayed["context"]["map_id"] == 2510
    assert displayed["continuity"]["current_joint_exact_qualities"] == ["q3"]
    assert detail_composition_view(displayed).quality("q3").dimension_state == "锁定"
    assert detail_composition_view(displayed).quality("q1").dimension_state == "未锁"


@pytest.mark.parametrize("dpi_scale", (1.0, 1.25, 1.5, 1.75))
def test_real_tk_4508_keeps_old_quote_but_pending_unpaired_current_quality(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    dpi_scale: float,
) -> None:
    server = _ahmad_server_module()
    module = _ahmad_overlay_module()
    monkeypatch.setattr(module.AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(
        module.AhmadTkOverlay,
        "_poll_startup_update_notice",
        lambda self: None,
    )
    cached = _summarize(server, tmp_path / "r3", include_q1_cells=False)
    current = _summarize_unrelated_q6_conflict(
        server,
        monkeypatch,
        tmp_path / "r4",
    )

    root = _new_root()
    original_scaling = float(root.tk.call("tk", "scaling"))
    try:
        root.tk.call("tk", "scaling", (96.0 / 72.0) * dpi_scale)
        overlay = module.AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / f"tk-{dpi_scale}" / "latest_snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        root.geometry("510x620+0+0")
        overlay._apply_live_summary(cached, cached, force=True)
        root.update_idletasks()
        cached_quote = overlay.price_labels["balanced"].cget("text")
        assert overlay.red_rows["白绿"].cget("text") == "9件·11格"
        assert overlay.quality_state_badges["白绿"].cget("text") == "锁定"

        overlay._apply_live_summary(current, current, force=True)
        root.update_idletasks()
        root.update()
        assert overlay.price_labels["balanced"].cget("text") == cached_quote
        assert overlay.red_rows["白绿"].cget("text") == "待定"
        assert overlay.quality_state_badges["白绿"].cget("text") == "冲突"
        assert "沿用R3报价" in overlay.subtitle.cget("text")
        assert overlay._last_live_summary["continuity"] == {
            "reason": "quality_composition_conflict",
            "source_round": "3",
            "current_joint_exact_qualities": ["q1", "q3", "q5"],
        }
        assert root.winfo_width() == 510
        q1_widget = overlay.red_rows["白绿"]
        q1_badge = overlay.quality_state_badges["白绿"]
        assert q1_widget.winfo_x() + q1_widget.winfo_width() <= q1_badge.winfo_x()
        assert q1_badge.winfo_x() + q1_badge.winfo_width() <= q1_badge.master.winfo_width()
    finally:
        try:
            root.tk.call("tk", "scaling", original_scaling)
            root.destroy()
        except tk.TclError:
            pass
