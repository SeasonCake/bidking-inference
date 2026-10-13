from __future__ import annotations

import json
import importlib.util
import re
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_match_records as records  # noqa: E402
import match_records_html as html  # noqa: E402


def _overlay_module():
    path = ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py"
    spec = importlib.util.spec_from_file_location("match_records_overlay_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _session_rows(session_id: str = "2401:current") -> list[dict]:
    return [
        {
            "phase": "bidding",
            "session_id": session_id,
            "created_at": 100,
            "map_id": 2401,
            "hero": "ahmed",
            "round": 2,
            "balanced": 90_000,
        },
        {
            "phase": "settled",
            "session_id": session_id,
            "created_at": 101,
            "map_id": 2401,
            "hero": "ahmed",
            "round": 2,
            "known_value_sum": 120_000,
            "latest_bids": {"玩家甲": 100_000},
        },
    ]


def _write_sessions(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_records_keep_session_identity_for_exact_settlement_target(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions.jsonl"
    _write_sessions(sessions, _session_rows())

    payload = records.build_payload(sessions)

    assert payload["n"] == 1
    assert payload["records"][0]["session_id"] == "2401:current"


def test_build_and_open_embeds_target_and_autofocus_contract(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions.jsonl"
    output = tmp_path / "match_records.html"
    _write_sessions(sessions, _session_rows())

    html.build_and_open(
        sessions,
        output,
        open_browser=False,
        target_session_id="2401:current",
    )
    rendered = output.read_text(encoding="utf-8")

    assert '"target_session_id": "2401:current"' in rendered
    assert '"session_id": "2401:current"' in rendered
    assert 'id="currentMatchRow"' in rendered
    assert "已定位并展开刚结束的这一局" in rendered
    assert 'requestAnimationFrame(()=>targetRow.scrollIntoView({block:"center"}))' in rendered
    assert 'behavior:"smooth"' not in rendered


@pytest.mark.parametrize("invalid_kind", ["incomplete", "zero_value", "missing_value"])
def test_missing_or_invalid_target_has_explicit_fallback_message(
    tmp_path: Path,
    invalid_kind: str,
) -> None:
    sessions = tmp_path / "sessions.jsonl"
    output = tmp_path / "match_records.html"
    rows = _session_rows()
    if invalid_kind == "incomplete":
        rows[-1]["settlement_incomplete"] = True
    elif invalid_kind == "zero_value":
        rows[-1]["known_value_sum"] = 0
    else:
        rows[-1]["known_value_sum"] = None
    _write_sessions(sessions, rows)

    html.build_and_open(
        sessions,
        output,
        open_browser=False,
        target_session_id="2401:current",
    )
    rendered = output.read_text(encoding="utf-8")

    assert '"records": []' in rendered
    assert "刚结束的这一局尚未写入完整结算记录" in rendered


def test_plain_history_entry_does_not_add_target_or_hide_records(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions.jsonl"
    output = tmp_path / "match_records.html"
    rows = _session_rows("2401:first") + _session_rows("2401:second")
    _write_sessions(sessions, rows)
    payload = records.build_payload(sessions)

    html.build_and_open(
        sessions,
        output,
        open_browser=False,
        payload=payload,
    )
    rendered = output.read_text(encoding="utf-8")
    match = re.search(r"const PAYLOAD = (.*?);\nconst RECORDS", rendered, re.DOTALL)
    assert match is not None
    embedded = json.loads(match.group(1))

    assert payload["n"] == 2
    assert len(payload["records"]) == 2
    assert embedded["records"] == payload["records"]
    assert "target_session_id" not in payload
    assert "target_session_id" not in embedded


def test_quality_legend_uses_theme_colors_at_render_time() -> None:
    rendered = html.render_html({"schema": "match_records_v0", "n": 0, "records": []})

    assert "function qualityLegend()" in rendered
    assert "${qualityLegend()}" in rendered
    assert "const QLEG" not in rendered


def test_local_dashboard_reuses_filtered_records_without_coercing_missing_values() -> None:
    payload = {
        "schema": "match_records_v0",
        "n": 5,
        "target_session_id": "2401:current",
        "records": [
            {
                "session_id": "old",
                "time": 1,
                "family": "别墅",
                "my_pl": 120_000,
                "lot_value": 500_000,
                "won_by_me": True,
            },
            {
                "session_id": "missing",
                "time": 2,
                "family": "别墅",
                "my_pl": None,
                "lot_value": 600_000,
                "won_by_me": False,
            },
            {
                "session_id": "2401:current",
                "time": 3,
                "family": "别墅",
                "my_pl": -80_000,
                "lot_value": 700_000,
                "won_by_me": False,
            },
            {
                "session_id": "other",
                "time": 4,
                "family": "沉船",
                "my_pl": 40_000,
                "lot_value": 800_000,
                "won_by_me": True,
            },
            {
                "session_id": "missing-value",
                "time": 5,
                "family": "沉船",
                "my_pl": 20_000,
                "lot_value": None,
                "won_by_me": None,
            },
        ],
    }

    rendered = html.render_html(payload)

    assert 'id="localKpis"' in rendered
    for label in ("筛选对局", "拿下 / 未拿下", "拿下率", "合计盈亏", "平均拍品价值"):
        assert label in rendered
    assert "近期对局盈亏" in rendered and "拍品价值趋势" in rendered
    assert "const DASHBOARD_LIMIT = 21" in rendered
    assert 'chartRows("my_pl")' in rendered and 'chartRows("lot_value")' in rendered
    assert 'function validChartRows(field)' in rendered
    assert '.filter(r=>typeof r[field]==="number" && Number.isFinite(r[field]))' in rendered
    assert "all.length-valid.length" in rendered
    assert "最近 ${rows.length} / 共 ${valid.length}" in rendered
    assert "Math.max(coarseChart?1160:340,Math.round(container.clientWidth||900))" in rendered
    assert 'window.matchMedia("(hover:none) and (pointer:coarse), (max-width:520px)")' in rendered
    assert ".dashboard-head span { flex-basis:100%; }" in rendered
    assert "缺失记录不会按 0 统计" in rendered
    assert "for(let index=0;index<5;index++)" in rendered
    assert "renderDashboard();" in rendered
    assert 'querySelectorAll(".local-chart-point")' in rendered
    assert 'addEventListener("click"' in rendered


def test_local_chart_escape_suppression_and_coarse_targets_are_explicit() -> None:
    rendered = html.render_html(
        {
            "n": 2,
            "families": ["别墅"],
            "records": [
                {
                    "session_id": "one",
                    "time": 1,
                    "family": "别墅",
                    "my_pl": 10_000,
                    "lot_value": 500_000,
                },
                {
                    "session_id": "two",
                    "time": 2,
                    "family": "别墅",
                    "my_pl": -5_000,
                    "lot_value": 600_000,
                },
            ],
        }
    )

    assert "localChartFocusSuppressed" in rendered
    assert 'e.key==="Escape"' in rendered
    assert 'e.key==="Enter"||e.key===" "||e.key.startsWith("Arrow")' in rendered
    assert "localChartFocusSuppressed!==point" in rendered
    assert ".local-chart svg { min-width:1160px; }" in rendered
    assert "overscroll-behavior-inline:contain" in rendered
    assert "valueHitWidth=Math.min(46,valueSpacing)" in rendered
    assert '<rect class="local-chart-hit"' in rendered


def test_portable_build_keeps_local_match_record_dependencies() -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert '"scripts/match_records_html.py", "scripts/build_match_records.py"' in build
    assert '"match_records_html.py", "build_match_records.py"' in build


def test_overlay_forwards_clicked_settlement_session_to_html(
    monkeypatch, tmp_path: Path
) -> None:
    module = _overlay_module()
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay.snapshot_path = tmp_path / "latest_snapshot.json"
    overlay._last_summary = {"context": {"session_id": "2401:current"}}
    overlay._resolve_match_records_scripts_dir = lambda: SCRIPTS
    called: dict = {}

    def fake_build_and_open(*args, **kwargs) -> None:
        called["args"] = args
        called["kwargs"] = kwargs

    class ImmediateThread:
        def __init__(self, *, target, **_kwargs) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    monkeypatch.setattr(html, "build_and_open", fake_build_and_open)
    monkeypatch.setattr(module.threading, "Thread", ImmediateThread)

    assert module.AhmadTkOverlay._open_match_records(overlay) == "break"
    assert called["args"] == (
        tmp_path / "sessions.jsonl",
        tmp_path / "match_records.html",
    )
    assert called["kwargs"] == {
        "open_browser": True,
        "target_session_id": "2401:current",
    }


def test_winner_loss_dividend_and_rank_gap_use_current_settlement_fields(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions.jsonl"
    rows = _session_rows("5601:old") + _session_rows("5601:new")
    rows[1].update(
        created_at=101,
        map_id=5601,
        known_value_sum=100_000,
        latest_bids={"我": 120_000},
        self_name="我",
        self_bid=120_000,
        winner_is_self=True,
        rank_summary={
            "self_rank_points": 1000.0,
            "self_rank_tier": 4,
            "self_slot": 8,
            "self_before": 1000.0,
            "self_delta": 20.0,
            "self_after": 1020.0,
            "changes": [
                {"slot": 7, "before": 1000.0, "delta": 90.0, "after": 1090.0},
                {"slot": 8, "before": 1000.0, "delta": 20.0, "after": 1020.0},
            ],
        },
    )
    rows[3].update(
        created_at=201,
        map_id=5601,
        known_value_sum=100_000,
        latest_bids={"我": 120_000},
        self_name="我",
        self_bid=120_000,
        winner_is_self=True,
        rank_summary={
            "self_rank_points": 1120.0,
            "self_rank_tier": 5,
            "self_slot": 8,
            "self_before": 1120.0,
            "self_delta": 15.0,
            "self_after": 1135.0,
            "changes": [
                {"slot": 7, "before": 1120.0, "delta": 80.0, "after": 1200.0},
                {"slot": 8, "before": 1120.0, "delta": 15.0, "after": 1135.0},
            ],
        },
    )
    _write_sessions(sessions, rows)

    payload = records.build_payload(sessions)
    newest = payload["records"][0]

    assert newest["loss_share"] == 2_000
    assert newest["my_pl"] == -33_000  # 100k - 120k + 2k - ranked 15k
    assert newest["profit_source"] == "mechanic_10pct"
    assert newest["rank_observation"] == {
        "before": 1120.0,
        "delta": 15.0,
        "after": 1135.0,
        "latest": 1135.0,
        "tier": 5,
        "slot": 8,
        "source": "settlement_self_slot8",
        "gap_from_previous": True,
        "previous_observed_rank": 1020.0,
        "current_observed_before": 1120.0,
    }
    rendered = html.render_html(payload)
    assert "for (const c of rs.changes)" not in rendered
    assert "期间对局未记录，不补算单局涨跌" in rendered


def test_rank_observation_rejects_inconsistent_self_triplet() -> None:
    assert records._rank_observation({
        "self_rank_points": 1200.0,
        "self_before": 1200.0,
        "self_delta": 30.0,
        "self_after": 9999.0,
        "changes": [{"slot": 8, "before": 1200.0, "delta": 30.0, "after": 1230.0}],
    }) == {
        "before": 1200.0,
        "delta": None,
        "after": None,
        "latest": 1200.0,
        "tier": None,
        "slot": None,
        "source": "bidding_self_points",
    }
