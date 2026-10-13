from __future__ import annotations

import copy
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from test_live_overlay import _ahmad_overlay_module  # noqa: E402


def _ready_capture_status(module, *, updated_at: float = 1_000.0) -> dict[str, object]:
    launch_id = "launch-current"
    return {
        "source": "wss_local",
        "_current_launch_id": launch_id,
        "_network_connection_status": {
            "schema_version": 1,
            "updated_at": updated_at,
            "launch_id": launch_id,
            "game_connection_state": "not_running",
            "wss_intercept_state": "ready",
            "capture_decode_state": "idle",
            "evidence": {
                "game_running": False,
                "player_log_error_counts": {},
                "active_flows": 0,
                "wss_raw_count": 0,
                "accepted_frames": 0,
                "ignored_frames": 0,
                "helper_phase": "helper_running",
                "helper_error_code": "",
            },
        },
        "_wss_helper_status": {
            "schema_version": 2,
            "launch_id": launch_id,
            "phase": "helper_running",
            "error_code": "",
        },
    }


def test_game_launch_candidate_requires_exact_current_ready_state() -> None:
    module = _ahmad_overlay_module()
    ready = _ready_capture_status(module)

    assert module._game_launch_candidate_ready(
        ready,
        process_running_state=False,
        now=1_005.0,
    )

    for process_state in (True, None):
        assert not module._game_launch_candidate_ready(
            ready,
            process_running_state=process_state,
            now=1_005.0,
        )

    variants: list[dict[str, object]] = []
    wrong_source = copy.deepcopy(ready)
    wrong_source["source"] = "windivert"
    variants.append(wrong_source)

    stale = copy.deepcopy(ready)
    stale["_network_connection_status"]["updated_at"] = 980.0  # type: ignore[index]
    variants.append(stale)

    launch_mismatch = copy.deepcopy(ready)
    launch_mismatch["_network_connection_status"]["launch_id"] = "old"  # type: ignore[index]
    variants.append(launch_mismatch)

    helper_launch_mismatch = copy.deepcopy(ready)
    helper_launch_mismatch["_wss_helper_status"]["launch_id"] = "old"  # type: ignore[index]
    variants.append(helper_launch_mismatch)

    for key, value in (
        ("game_connection_state", "connecting"),
        ("wss_intercept_state", "failed"),
        ("capture_decode_state", "recognized"),
    ):
        variant = copy.deepcopy(ready)
        variant["_network_connection_status"][key] = value  # type: ignore[index]
        variants.append(variant)

    game_unknown = copy.deepcopy(ready)
    game_unknown["_network_connection_status"]["evidence"]["game_running"] = None  # type: ignore[index]
    variants.append(game_unknown)

    helper_failed = copy.deepcopy(ready)
    helper_failed["_network_connection_status"]["evidence"][  # type: ignore[index]
        "helper_error_code"
    ] = "wss_redirector_missing_stable"
    variants.append(helper_failed)

    raw_helper_failed = copy.deepcopy(ready)
    raw_helper_failed["_wss_helper_status"][  # type: ignore[index]
        "error_code"
    ] = "wss_redirector_missing_stable"
    variants.append(raw_helper_failed)

    for variant in variants:
        assert not module._game_launch_candidate_ready(
            variant,
            process_running_state=False,
            now=1_005.0,
        )


def _bare_overlay_for_game_launch(module, capture_status):
    overlay = object.__new__(module.AhmadTkOverlay)
    overlay._game_launch_candidate_context = True
    overlay._manual_active = False
    overlay._manual_edit_enabled = False
    overlay._game_launch_pending_until = 0.0
    overlay._game_launch_error_until = 0.0
    overlay._capture_status = lambda: capture_status
    overlay._game_process_state_probe = lambda _name: False
    overlay._sync_game_launch_candidate = lambda _status=None: None
    overlay._notify_game_launch_failure = lambda: None
    return overlay


def test_game_launch_click_uses_fixed_uri_and_blocks_repeated_clicks(monkeypatch) -> None:
    module = _ahmad_overlay_module()
    capture_status = _ready_capture_status(module, updated_at=time.time())
    overlay = _bare_overlay_for_game_launch(module, capture_status)
    launched: list[str] = []
    events: list[str] = []
    monkeypatch.setattr(
        module.os,
        "startfile",
        lambda uri: launched.append(uri),
        raising=False,
    )
    overlay._game_uri_launcher = module._launch_fixed_steam_game_uri
    overlay._record_ui_runtime_status = (
        lambda event, **_kwargs: events.append(str(event))
    )

    assert overlay._launch_game_from_candidate() == "break"
    assert overlay._launch_game_from_candidate() == "break"

    assert launched == [module.STEAM_GAME_URI]
    assert events == ["game_launch_requested"]
    assert overlay._game_launch_pending_until > time.monotonic()


def test_game_launch_click_rechecks_process_race_and_fails_soft() -> None:
    module = _ahmad_overlay_module()
    capture_status = _ready_capture_status(module, updated_at=time.time())
    overlay = _bare_overlay_for_game_launch(module, capture_status)
    launched: list[str] = []
    synced: list[dict[str, object]] = []
    events: list[str] = []
    notices: list[bool] = []
    overlay._game_uri_launcher = lambda: launched.append("called")
    overlay._sync_game_launch_candidate = lambda status=None: synced.append(status)
    overlay._record_ui_runtime_status = (
        lambda event, **_kwargs: events.append(str(event))
    )
    overlay._notify_game_launch_failure = lambda: notices.append(True)

    overlay._game_process_state_probe = lambda _name: True
    assert overlay._launch_game_from_candidate() == "break"
    assert launched == []
    assert synced == [capture_status]

    overlay._game_process_state_probe = lambda _name: False

    def fail_to_launch() -> None:
        raise OSError("private path must not escape")

    overlay._game_uri_launcher = fail_to_launch
    assert overlay._launch_game_from_candidate() == "break"
    assert events == ["game_launch_failed"]
    assert notices == [True]
    assert overlay._game_launch_error_until > time.monotonic()
