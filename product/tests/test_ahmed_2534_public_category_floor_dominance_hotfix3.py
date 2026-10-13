from __future__ import annotations

import copy
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
for _path in (
    ROOT / "src",
    ROOT / "hero_ref" / "src",
    ROOT / "hero_ref" / "tools",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ahmad_live_panel_server import (  # noqa: E402
    ServerConfig,
    make_handler,
    summarize_snapshot,
)
from ahmad_ref_engine import (  # noqa: E402
    BIDKING_DISABLE_PUBLIC_CATEGORY_TOTAL_VALUE_FLOOR,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from ahmad_tk_overlay import AhmadTkOverlay, _summary_diagnostic_row  # noqa: E402
from bidking_lab.runtime.snapshot import ui_contract_from_artifact  # noqa: E402


_CATEGORY_TOTAL = 1_530_896


def _snapshot(
    *,
    include_category_total: bool = True,
    phase: str = "bidding",
) -> dict[str, object]:
    """Support-safe 2534 bidding shape reconstructed from the final diagnostic."""

    snapshot: dict[str, object] = {
        "hero": "ahmed",
        "hero_id": 204,
        "map_id": 2534,
        "model_map_id": 2504,
        "phase": phase,
        "round": 4,
        "action_round": 4,
        "observed_round": 3,
        "session_id": "2534:support-safe-public-floor-dominance",
        "warehouse_source_qualification": "",
        "official_distribution_eligible": False,
        "warehouse_source": {},
        "structured_ref_inputs": {
            "source": "monitor_pre_settlement_field_updates",
            "hero": "ahmed",
            "total_count": 41,
            "total_cells": None,
            "avg_cells": {
                "q1": 1.9375,
                "q3": 1.75,
                "q4": 3.5714285373687744,
                "q5": 5.25,
            },
            "counts": {"q3": 12},
            "quality_cells": {},
            "avg_values": {},
            "quality_values": {},
            "quality_value_floors": {},
            "count_sums": {},
            "scoped_count_sums": [],
            "split_avg_cells": {},
            "split_counts": {},
            "split_quality_cells": {},
            "field_updates": [],
        },
        "public_info_rows": (
            [
                {
                    "sort": 29,
                    "time": "support-safe",
                    "info_id": 200044,
                    "map_id": 2534,
                    "value": _CATEGORY_TOTAL,
                    "value_field": 12,
                    "revealed_items": 0,
                    "revealed_items_detail": [],
                    "revealed_summary": "",
                }
            ]
            if include_category_total
            else []
        ),
        "action_result_rows": [
            {
                "sort": 3,
                "action_id": 100117,
                "result": 12,
                "result_field": 7,
                "result_wire_type": "varint",
                "round": 1,
            },
            {
                "sort": 11,
                "action_id": 100110,
                "result": 1.9375,
                "result_field": 11,
                "result_wire_type": "float32",
                "result_raw_hex": "0000f83f",
                "round": 1,
            },
        ],
        "skill_reveal_rows": [
            {
                "sort": 23,
                "skill_id": 100204,
                "hero_id": 204,
                "result": 41,
                "result_field": 7,
                "round_index": 3,
            },
            {
                "sort": 23,
                "skill_id": 1002041,
                "hero_id": 204,
                "result": 5.25,
                "result_field": 11,
                "round_index": 1,
            },
            {
                "sort": 23,
                "skill_id": 1002042,
                "hero_id": 204,
                "result": 3.5714285373687744,
                "result_field": 11,
                "round_index": 2,
            },
            {
                "sort": 23,
                "skill_id": 1002043,
                "hero_id": 204,
                "result": 1.75,
                "result_field": 11,
                "round_index": 3,
            },
        ],
    }
    snapshot["ui_contract"] = ui_contract_from_artifact(snapshot)
    return snapshot


def _run(
    snapshot: dict[str, object],
    *,
    max_combos: int = 8_000,
) -> dict[str, object]:
    return run_reference_engine(
        prepare_reference_engine_snapshot(copy.deepcopy(snapshot)),
        max_combos=max_combos,
    ).as_dict()


def _triplet(result: dict[str, object], keys: tuple[str, str, str]) -> list[object]:
    return [result[key] for key in keys]


def test_dominant_category_subtotal_is_a_bound_not_a_posterior_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_bound = _run(_snapshot())
    without_bound = _run(_snapshot(include_category_total=False))

    assert _triplet(
        with_bound, ("value_p25", "value_p50", "value_p75")
    ) == _triplet(without_bound, ("value_p25", "value_p50", "value_p75"))
    for key in (
        "combo_count",
        "red_count_range",
        "red_cells_range",
        "red_value_range",
        "quality_count_ranges",
        "quality_cells_ranges",
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
        "quality_lock",
        "total_grid_range",
    ):
        assert with_bound[key] == without_bound[key], key

    assert _triplet(
        with_bound, ("conservative", "balanced", "aggressive")
    ) == [_CATEGORY_TOTAL] * 3
    assert with_bound["total_value_bound"] == {
        "mode": "lower_bound_only",
        "source": "public_info_200044",
        "lower_bound": _CATEGORY_TOTAL,
        "modeled_value_range": _triplet(
            without_bound, ("value_p25", "value_p50", "value_p75")
        ),
        "modeled_decision_range": _triplet(
            without_bound, ("conservative", "balanced", "aggressive")
        ),
    }
    assert "total_value_bound" not in without_bound

    monkeypatch.setenv(BIDKING_DISABLE_PUBLIC_CATEGORY_TOTAL_VALUE_FLOOR, "1")
    rollback = _run(_snapshot())
    assert rollback == without_bound


@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_dominant_category_bound_is_stable_at_state_caps(
    monkeypatch: pytest.MonkeyPatch,
    max_combos: int,
) -> None:
    with_bound = _run(_snapshot(), max_combos=max_combos)
    without_bound = _run(
        _snapshot(include_category_total=False),
        max_combos=max_combos,
    )

    assert _triplet(
        with_bound, ("value_p25", "value_p50", "value_p75")
    ) == _triplet(without_bound, ("value_p25", "value_p50", "value_p75"))
    assert _triplet(
        with_bound, ("conservative", "balanced", "aggressive")
    ) == [_CATEGORY_TOTAL] * 3
    assert with_bound["total_value_bound"]["modeled_value_range"] == _triplet(
        without_bound, ("value_p25", "value_p50", "value_p75")
    )

    monkeypatch.setenv(BIDKING_DISABLE_PUBLIC_CATEGORY_TOTAL_VALUE_FLOOR, "1")
    assert _run(_snapshot(), max_combos=max_combos) == without_bound


@pytest.mark.parametrize("shape", ("wrong-value-type", "wrong-id-type"))
def test_invalid_category_value_cannot_publish_a_bound(shape: str) -> None:
    snapshot = _snapshot()
    rows = snapshot["public_info_rows"]
    assert isinstance(rows, list)
    if shape == "wrong-value-type":
        rows[0]["value"] = str(_CATEGORY_TOTAL)
    else:
        rows[0]["info_id"] = "200044"
    snapshot["ui_contract"] = ui_contract_from_artifact(snapshot)

    result = _run(snapshot)
    absent = _run(_snapshot(include_category_total=False))

    assert result["evidence"]["public_category_total_value_floor"] == 0
    assert "total_value_bound" not in result
    assert _triplet(result, ("value_p25", "value_p50", "value_p75")) == _triplet(
        absent, ("value_p25", "value_p50", "value_p75")
    )
    assert _triplet(
        result, ("conservative", "balanced", "aggressive")
    ) == _triplet(absent, ("conservative", "balanced", "aggressive"))


def test_settlement_does_not_publish_live_lower_bound_state(tmp_path: Path) -> None:
    snapshot = _snapshot(phase="settled")
    snapshot_path = tmp_path / "settled-snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False),
        encoding="utf-8",
    )

    result = _run(snapshot)
    summary = summarize_snapshot(snapshot, snapshot_path=snapshot_path)

    assert "total_value_bound" not in result
    assert "total_value_bound" not in summary["reference"]
    assert summary["reference"]["price_titles"] != {
        "conservative": "已知下限",
        "balanced": "估价状态",
        "aggressive": "类别残余",
    }


def test_summary_api_and_tk_render_dominant_category_subtotal_as_lower_bound(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    original = copy.deepcopy(snapshot)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(
            ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - self-created loopback product contract
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=30,
        ) as response:
            assert response.status == 200
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)

    tk_row = _summary_diagnostic_row(
        payload,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )
    expected_bound = summary["ahmed_ref"]["total_value_bound"]
    expected_reference = {
        "conservative": f"≥{_CATEGORY_TOTAL:,}",
        "balanced": "下限态",
        "aggressive": "残余未定",
        "decision_range": f"≥{_CATEGORY_TOTAL:,}（下限态；类别残余未定）",
        "total_value_range": f"≥{_CATEGORY_TOTAL:,}（下限态；类别残余未定）",
        "total_value_bound": expected_bound,
    }

    assert snapshot == original
    assert payload["ahmed_ref"]["total_value_bound"] == expected_bound
    assert {
        key: summary["reference"].get(key) for key in expected_reference
    } == expected_reference
    assert {
        key: payload["reference"].get(key) for key in expected_reference
    } == expected_reference
    assert {
        key: tk_row["reference"].get(key) for key in expected_reference
    } == expected_reference
    assert summary["reference"]["price_titles"] == {
        "conservative": "已知下限",
        "balanced": "估价状态",
        "aggressive": "类别残余",
    }
    assert summary["reference"]["action"] == "仅下限可用"
    assert any(flag.get("label") == "总价仅下限" for flag in summary["flags"])


def test_real_tk_renders_lower_bound_labels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tk = pytest.importorskip("tkinter")
    monkeypatch.setattr(AhmadTkOverlay, "_start_activation_worker", lambda self: None)
    monkeypatch.setattr(AhmadTkOverlay, "_poll_startup_update_notice", lambda self: None)
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"real Tk unavailable: {exc}")

    try:
        snapshot = _snapshot()
        snapshot_path = tmp_path / "snapshot.json"
        snapshot_path.write_text(
            json.dumps(snapshot, ensure_ascii=False),
            encoding="utf-8",
        )
        summary = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
        overlay = AhmadTkOverlay(
            root,
            snapshot_path=tmp_path / "unused-latest-snapshot.json",
            interval_ms=100_000,
            ui_prefs_enabled=False,
        )
        root.attributes("-topmost", False)
        overlay.render(summary)
        root.update_idletasks()

        assert {
            key: str(label.cget("text"))
            for key, label in overlay.price_title_labels.items()
        } == {
            "conservative": "已知下限",
            "balanced": "估价状态",
            "aggressive": "类别残余",
        }
        assert {
            key: str(label.cget("text"))
            for key, label in overlay.price_labels.items()
        } == {
            "conservative": f"≥{_CATEGORY_TOTAL:,}",
            "balanced": "下限态",
            "aggressive": "残余未定",
        }
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
