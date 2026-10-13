from __future__ import annotations

from html import unescape
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import public_stats as ps  # noqa: E402


def _chrome_path() -> Path | None:
    configured = os.environ.get("CHROME_PATH")
    candidates = [
        Path(configured) if configured else None,
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    for command in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        resolved = shutil.which(command)
        if resolved:
            candidates.append(Path(resolved))
    return next((path for path in candidates if path is not None and path.is_file()), None)


def _noon(day: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(day + "T12:00:00").replace(
        tzinfo=ps.SHANGHAI_TZ
    ).timestamp()


def _row(
    day: str,
    q6: int,
    qualification: str,
    eligible: bool,
    **extra,
) -> dict:
    row = {
        "map_id": 2401,
        "final_quality_counts": f"q1=1;q6={q6}",
        "final_quality_values": f"q1=100;q6={q6 * 100}",
        "inventory_count": q6 + 1,
        "inventory_cells": q6 + 1,
        "known_value_sum": q6 * 100 + 100,
        "client_ts": _noon(day),
        "warehouse_source_qualification": qualification,
        "official_distribution_eligible": eligible,
    }
    row.update(extra)
    return row


def _rows(day: str, q6: int, scope: str, *, n: int = 20, **extra) -> list[dict]:
    qualification, eligible = {
        "official": ("official_random_warehouse", True),
        "unknown": ("postupdate_unknown_warehouse_source", False),
        "player_custom": ("player_custom_warehouse", False),
        "conflict": ("official_random_warehouse", False),
    }[scope]
    return [
        _row(day, q6, qualification, eligible, **extra)
        for _ in range(n)
    ]


def _chrome_trends(aggregate: dict, tmp_path: Path) -> dict:
    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("real Chrome/Chromium executable is unavailable")
    tmp_path.mkdir(parents=True, exist_ok=True)

    html = ps.build_public_html(
        aggregate,
        generated_at=_noon("2026-07-30"),
        rebuild_interval=1800,
    )
    server = [
        {
            "day": row["day"],
            "n": row["n"],
            "q6": row["avg_q"]["q6"],
            "low": row["low_confidence"],
        }
        for row in aggregate["trend"]
    ]
    probe = """
<pre id="public-trend-probe"></pre>
<script>
(function(){
  function q6(id){
    var out=[];
    document.querySelectorAll("#"+id+" title").forEach(function(node){
      var text=node.textContent;
      var match=/^红 (\\d{4}-\\d{2}-\\d{2}): (-?[0-9]+(?:\\.[0-9]+)?)/.exec(text);
      if(match) out.push({
        day:match[1],
        q6:Number(match[2]),
        low:text.indexOf("⚠️")>=0
      });
    });
    return out;
  }
  function cubeTrend(){
    var byDay={};
    hits().forEach(function(i){
      var day=C.days[C.d[i]];
      (byDay[day]=byDay[day]||[]).push(i);
    });
    return Object.keys(byDay).sort().map(function(day){
      var value=agg(byDay[day]);
      return {
        day:day,
        n:value.n,
        q6:Number(fmt1(mean(value,"q6"))),
        low:value.n<MIN_DAY
      };
    });
  }
  function options(id){
    return Array.from(document.querySelectorAll("#"+id+" option")).map(
      function(node){ return [node.value,node.textContent]; }
    );
  }
  document.getElementById("public-trend-probe").textContent=JSON.stringify({
    server:SERVER_TREND,
    cube:cubeTrend(),
    interactive:q6("expl"),
    static:q6("tdChart"),
    interactive_text:document.getElementById("expl").textContent.trim(),
    static_text:document.getElementById("tdChart").textContent.trim(),
    map_rows:Array.from(
      document.querySelectorAll("#mapStatsTable tbody tr")
    ).map(function(row){
      return {
        map_id:Number(row.querySelector(".mid").textContent),
        kind:row.dataset.kind,
        mode:row.dataset.mode,
        activity:row.dataset.activity,
        source:row.dataset.source,
        visible:row.style.display!=="none",
        text:row.textContent.trim()
      };
    }),
    mode_options:options("mp-mode"),
    activity_options:options("mp-activity")
  });
})();
</script>
""".replace(
        "SERVER_TREND",
        json.dumps(server, ensure_ascii=False, separators=(",", ":")).replace(
            "</", "<\\/"
        ),
    )
    html = html.replace("</body>", probe + "</body>")
    page = tmp_path / "public.html"
    page.write_text(html, encoding="utf-8")
    profile = tmp_path / "chrome-profile"
    profile.mkdir()

    completed = subprocess.run(
        [
            str(chrome),
            "--headless=new",
            "--disable-gpu",
            "--disable-extensions",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            "--dump-dom",
            page.as_uri(),
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    dumped = completed.stdout.decode("utf-8", errors="strict")
    match = re.search(
        r'<pre id="public-trend-probe">(.*?)</pre>',
        dumped,
        flags=re.DOTALL,
    )
    assert match is not None, "generated public HTML did not complete its browser probe"
    return json.loads(unescape(match.group(1)))


def _browser_shape(server: list[dict]) -> list[dict]:
    return [
        {"day": row["day"], "q6": row["q6"], "low": row["low"]}
        for row in server
    ]


def test_real_chrome_static_trend_uses_unique_accepted_cells(tmp_path: Path):
    rows = []
    for day in ("2026-07-28", "2026-07-29"):
        rows.extend(_rows(day, 1, "official"))
        rows.extend(_rows(day, 3, "unknown"))
        rows.extend(_rows(day, 99, "conflict"))

    aggregate = ps.aggregate_public(rows)
    rendered = _chrome_trends(aggregate, tmp_path)

    assert rendered["server"] == [
        {"day": "2026-07-28", "n": 40, "q6": 2.0, "low": True},
        {"day": "2026-07-29", "n": 40, "q6": 2.0, "low": True},
    ]
    assert rendered["cube"] == rendered["server"]
    expected = _browser_shape(rendered["server"])
    assert rendered["interactive"] == expected
    assert rendered["static"] == expected


def test_real_chrome_static_trend_keeps_player_zero_and_date_boundaries(
    tmp_path: Path,
):
    rows = [
        *_rows("2026-07-27", 5, "player_custom"),
        *_rows("2026-07-28", 0, "official"),
        *_rows("2026-07-28", 4, "unknown"),
        *_rows("2026-07-28", 99, "conflict"),
        *_rows(
            "2026-07-29",
            77,
            "official",
            server_quarantine_reason="warehouse_source_pending",
        ),
    ]
    rows.extend(
        _row(
            "2026-07-29",
            9,
            "official_random_warehouse",
            True,
            final_quality_counts=None,
        )
        for _ in range(20)
    )
    rows.extend(
        _row(
            "2026-07-29",
            9,
            "official_random_warehouse",
            True,
            final_quality_counts="not-a-quality-map",
        )
        for _ in range(20)
    )

    aggregate = ps.aggregate_public(rows)
    rendered = _chrome_trends(aggregate, tmp_path)

    assert aggregate["totals"]["source_scope_counts"] == {
        "official": 20,
        "player_custom": 20,
        "unknown": 20,
        "conflict": 20,
    }
    assert rendered["server"] == [
        {"day": "2026-07-27", "n": 20, "q6": 5.0, "low": True},
        {"day": "2026-07-28", "n": 40, "q6": 2.0, "low": True},
    ]
    assert rendered["cube"] == rendered["server"]
    expected = _browser_shape(rendered["server"])
    assert rendered["interactive"] == expected
    assert rendered["static"] == expected


def test_real_chrome_static_trend_distinguishes_empty_and_single_day(
    tmp_path: Path,
):
    empty_rows = [
        *_rows("2026-07-27", 99, "conflict"),
        *_rows("2026-07-28", 88, "conflict"),
        *_rows(
            "2026-07-29",
            77,
            "official",
            server_quarantine_reason="warehouse_source_pending",
        ),
    ]
    empty = ps.aggregate_public(empty_rows)
    empty_rendered = _chrome_trends(empty, tmp_path / "empty")

    assert empty_rendered["server"] == empty_rendered["cube"] == []
    assert empty_rendered["interactive"] == empty_rendered["static"] == []
    assert "样本 0 局" in empty_rendered["interactive_text"]
    assert empty_rendered["static_text"] == "暂无已接纳趋势数据。"

    single_rows = [
        *_rows("2026-07-28", 0, "official"),
        *_rows("2026-07-28", 3, "unknown"),
        *_rows("2026-07-28", 6, "player_custom"),
        *_rows("2026-07-28", 99, "conflict"),
    ]
    single = ps.aggregate_public(single_rows)
    single_rendered = _chrome_trends(single, tmp_path / "single")

    assert single_rendered["server"] == single_rendered["cube"] == [
        {"day": "2026-07-28", "n": 60, "q6": 3.0, "low": True},
    ]
    assert single_rendered["interactive"] == single_rendered["static"] == []
    assert "只命中 1 天" in single_rendered["interactive_text"]
    assert single_rendered["static_text"] == "只有 1 天数据, 画不了趋势。"


def test_real_chrome_map_lifecycle_and_auction_modes_remain_orthogonal(
    tmp_path: Path,
):
    rows = []
    for map_id in (2401, 5601, 2541, 4551):
        rows.extend(_rows("2026-07-28", 1, "official", map_id=map_id))

    rendered = _chrome_trends(ps.aggregate_public(rows), tmp_path)
    accepted = {
        row["map_id"]: row
        for row in rendered["map_rows"]
        if row["source"] == "accepted"
    }

    assert {
        map_id: (row["kind"], row["mode"], row["activity"], row["visible"])
        for map_id, row in accepted.items()
    } == {
        2401: ("regular", "open", "none", True),
        5601: ("ranked", "sealed", "none", True),
        2541: ("activity", "open", "current", True),
        4551: ("activity", "sealed", "pending", True),
    }
    assert ["open", "明拍"] in rendered["mode_options"]
    assert ["sealed", "暗拍"] in rendered["mode_options"]
    assert ["current", "当前活动"] in rendered["activity_options"]
    assert ["pending", "活动待确认"] in rendered["activity_options"]
    assert "当前活动" in accepted[2541]["text"]
    assert "活动待确认" in accepted[4551]["text"]
