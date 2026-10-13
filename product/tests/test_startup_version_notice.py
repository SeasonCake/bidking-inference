from __future__ import annotations

import builtins
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bidking_lab.live import startup_version_notice as svn


def _update_status(**overrides):
    status = {
        "app": "0.2.9-r1",
        "latest": "0.2.9-hotfix1",
        "min": "0.2.0",
        "url": "https://downloads.example/bidking.zip",
        "notice": "修复启动与报价问题",
        "ok": True,
        "reachable": True,
        "up_to_date": False,
        "supported": True,
    }
    status.update(overrides)
    return status


def test_check_and_publish_runs_checker_once_and_round_trips_current_monitor(tmp_path):
    calls = []

    def checker(*, timeout):
        calls.append(timeout)
        return _update_status(secret="must-not-persist")

    status = svn.check_and_publish_startup_version(tmp_path, timeout=2.5, checker=checker)
    assert calls == [2.5]
    assert status["latest"] == "0.2.9-hotfix1"

    raw = json.loads((tmp_path / svn.STATUS_FILENAME).read_text(encoding="utf-8"))
    assert "secret" not in raw
    current = svn.read_recent_startup_version_status(
        tmp_path,
        app_version="0.2.9-r1",
        writer_pids=(raw["writer_pid"],),
        now=raw["checked_at"] + 1,
    )
    assert current is not None
    assert current["latest"] == "0.2.9-hotfix1"


def test_reader_rejects_stale_wrong_app_wrong_monitor_and_malformed_files(tmp_path):
    path = svn.write_startup_version_status(
        tmp_path,
        _update_status(),
        checked_at=1_000.0,
        writer_pid=4321,
    )
    assert svn.read_recent_startup_version_status(
        tmp_path, app_version="0.2.9-r1", writer_pids=(4321,), now=1_001.0
    ) is not None
    assert svn.read_recent_startup_version_status(
        tmp_path, app_version="0.2.9-r1", writer_pids=(9999,), now=1_001.0
    ) is None
    assert svn.read_recent_startup_version_status(
        tmp_path, app_version="0.2.9-hotfix1", writer_pids=(4321,), now=1_001.0
    ) is None
    assert svn.read_recent_startup_version_status(
        tmp_path, app_version="0.2.9-r1", writer_pids=(4321,), now=1_500.0
    ) is None

    path.write_text("not-json", encoding="utf-8")
    assert svn.read_recent_startup_version_status(tmp_path, app_version="0.2.9-r1") is None


def test_parser_runtime_error_is_contained_when_publishing(tmp_path, monkeypatch):
    from bidking_lab.live import version_check

    def _raise_runtime_error(_value):
        raise RuntimeError("audit parser failure")

    monkeypatch.setattr(version_check, "_vtuple", _raise_runtime_error)
    status = svn.check_and_publish_startup_version(
        tmp_path, checker=lambda **_kwargs: _update_status()
    )

    assert status["ok"] is True
    raw = json.loads((tmp_path / svn.STATUS_FILENAME).read_text(encoding="utf-8"))
    assert raw["ok"] is False


def test_parser_runtime_error_is_contained_when_reading(tmp_path, monkeypatch):
    svn.write_startup_version_status(
        tmp_path, _update_status(), checked_at=1_000.0, writer_pid=4321
    )
    from bidking_lab.live import version_check

    def _raise_runtime_error(_value):
        raise RuntimeError("audit parser failure")

    monkeypatch.setattr(version_check, "_vtuple", _raise_runtime_error)

    assert svn.read_recent_startup_version_status(
        tmp_path, app_version="0.2.9-r1", writer_pids=(4321,), now=1_001.0
    ) is None


def test_parser_import_error_is_contained_when_building_notice(monkeypatch):
    real_import = builtins.__import__

    def _raise_version_check_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "version_check" and level == 1:
            raise ImportError("audit parser import failure")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _raise_version_check_import)
    notice = svn.build_update_notice(_update_status(), app_version="0.2.9-r1")

    assert notice["visible"] is False
    assert notice["label"] == ""


def test_checker_dict_subclass_with_exploding_get_fails_closed(tmp_path):
    class _ExplodingStatus(dict):
        def get(self, *_args, **_kwargs):
            raise RuntimeError("audit status mapping failure")

    status = svn.check_and_publish_startup_version(
        tmp_path, checker=lambda **_kwargs: _ExplodingStatus()
    )

    assert status == {"ok": False, "reachable": False}
    raw = json.loads((tmp_path / svn.STATUS_FILENAME).read_text(encoding="utf-8"))
    assert raw["ok"] is False


def test_reader_rejects_nonfinite_and_nonexact_checked_at(tmp_path):
    path = svn.write_startup_version_status(
        tmp_path, _update_status(), checked_at=1_000.0, writer_pid=4321
    )
    for checked_at in (float("nan"), float("inf"), float("-inf"), True, "1000"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["checked_at"] = checked_at
        path.write_text(json.dumps(raw), encoding="utf-8")
        assert svn.read_recent_startup_version_status(
            tmp_path, app_version="0.2.9-r1", writer_pids=(4321,), now=1_001.0
        ) is None


def test_reader_requires_exact_integer_schema_version(tmp_path):
    path = svn.write_startup_version_status(
        tmp_path, _update_status(), checked_at=1_000.0, writer_pid=4321
    )
    for schema_version in (True, 1.0):
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["schema_version"] = schema_version
        path.write_text(json.dumps(raw), encoding="utf-8")
        assert svn.read_recent_startup_version_status(
            tmp_path, app_version="0.2.9-r1", writer_pids=(4321,), now=1_001.0
        ) is None


def test_reader_requires_exact_positive_integer_writer_pid(tmp_path):
    path = svn.write_startup_version_status(
        tmp_path, _update_status(), checked_at=1_000.0, writer_pid=4321
    )
    for writer_pid in (4321.5, True, 0, -1):
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["writer_pid"] = writer_pid
        path.write_text(json.dumps(raw), encoding="utf-8")
        assert svn.read_recent_startup_version_status(
            tmp_path, app_version="0.2.9-r1", writer_pids=(4321,), now=1_001.0
        ) is None


class _CheckedAtIntSubclass(int):
    pass


class _CheckedAtFloatSubclass(float):
    pass


@pytest.mark.parametrize(
    "invalid_checked_at",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        True,
        "1000",
        _CheckedAtIntSubclass(1000),
        _CheckedAtFloatSubclass(1000.0),
    ],
    ids=["nan", "positive-inf", "negative-inf", "bool", "string", "int-subclass", "float-subclass"],
)
def test_writer_does_not_normalize_invalid_checked_at_to_valid_epoch(
    tmp_path, invalid_checked_at
):
    path = svn.write_startup_version_status(
        tmp_path,
        _update_status(),
        checked_at=invalid_checked_at,
        writer_pid=4321,
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["checked_at"] is None
    assert raw["ok"] is False
    for now in (0.0, 1_000.0):
        assert svn.read_recent_startup_version_status(
            tmp_path, app_version="0.2.9-r1", writer_pids=(4321,), now=now
        ) is None


def test_writer_preserves_valid_epoch_checked_at_zero(tmp_path):
    path = svn.write_startup_version_status(
        tmp_path, _update_status(), checked_at=0.0, writer_pid=4321
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["checked_at"] == 0.0
    assert raw["ok"] is True
    assert svn.read_recent_startup_version_status(
        tmp_path, app_version="0.2.9-r1", writer_pids=(4321,), now=0.0
    ) is not None


class _VersionSubclass(str):
    pass


@pytest.mark.parametrize(
    "invalid",
    [
        " 0.3.3-hotfix2",
        "0.3.3-hotfix2 ",
        "\u20030.3.3-hotfix2",
        "0.3.3-hotfix2\u2003",
        "0.3.3-hotfix2\x00",
        "v0.3.3-hotfix2",
        _VersionSubclass("0.3.3-hotfix2"),
        303,
    ],
    ids=["ascii-leading", "ascii-trailing", "unicode-leading", "unicode-trailing", "nul", "malformed", "subclass", "non-string"],
)
@pytest.mark.parametrize("field", ["app", "latest", "min"])
def test_persisted_version_fields_do_not_normalize_invalid_values_before_notice(
    tmp_path, field, invalid
):
    path = svn.write_startup_version_status(
        tmp_path,
        _update_status(**{field: invalid}),
        checked_at=1_000.0,
        writer_pid=4321,
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw[field] is None
    assert raw["ok"] is False
    status = svn.read_recent_startup_version_status(
        tmp_path, app_version="0.2.9-r1", writer_pids=(4321,), now=1_001.0
    )
    notice = svn.build_update_notice(status, app_version="0.2.9-r1")
    assert notice["visible"] is False
    assert notice["label"] == ""


@pytest.mark.parametrize(
    "invalid",
    [
        " 0.3.3-hotfix2",
        "0.3.3-hotfix2 ",
        "\u20030.3.3-hotfix2",
        "0.3.3-hotfix2\u2003",
        "0.3.3-hotfix2\x00",
        "v0.3.3-hotfix2",
        _VersionSubclass("0.3.3-hotfix2"),
        303,
    ],
    ids=["ascii-leading", "ascii-trailing", "unicode-leading", "unicode-trailing", "nul", "malformed", "subclass", "non-string"],
)
def test_notice_rejects_invalid_current_version_without_normalizing(invalid):
    notice = svn.build_update_notice(
        _update_status(latest="0.3.4"), app_version=invalid
    )

    assert notice["visible"] is False
    assert notice["label"] == ""


def test_notice_is_hidden_when_current_offline_current_or_manifest_is_malformed():
    offline = svn.build_update_notice(None, app_version="0.2.9-r1")
    assert offline["visible"] is False
    assert "不影响计算器使用" in offline["tooltip"]

    current = svn.build_update_notice(
        _update_status(latest="0.2.9-r1", up_to_date=True),
        app_version="0.2.9-r1",
    )
    assert current["visible"] is False
    assert "已是最新版" in current["tooltip"]

    malformed = svn.build_update_notice(
        _update_status(latest="v0.2.9-hotfix1"),
        app_version="0.2.9-r1",
    )
    assert malformed["visible"] is False
    assert "格式无效" in malformed["tooltip"]


def test_0_3_2_hotfix3_source_hides_update_for_hotfix2_production_manifest():
    notice = svn.build_update_notice(
        _update_status(
            app="0.3.2-hotfix3",
            latest="0.3.2-hotfix2",
            min="0.3.1",
            up_to_date=True,
            supported=True,
        ),
        app_version="0.3.2-hotfix3",
    )

    assert notice["visible"] is False
    assert notice["required"] is False
    assert notice["label"] == ""
    assert "已是最新版" in notice["tooltip"]


def test_0_3_2_hotfix4_source_hides_update_for_hotfix3_current_manifest():
    notice = svn.build_update_notice(
        _update_status(
            app="0.3.2-hotfix4",
            latest="0.3.2-hotfix3",
            min="0.3.1",
            up_to_date=True,
            supported=True,
        ),
        app_version="0.3.2-hotfix4",
    )

    assert notice["visible"] is False
    assert notice["required"] is False
    assert notice["label"] == ""
    assert "已是最新版" in notice["tooltip"]


def test_0_3_2_hotfix3_source_shows_hotfix4_as_optional_update():
    notice = svn.build_update_notice(
        _update_status(
            app="0.3.2-hotfix3",
            latest="0.3.2-hotfix4",
            min="0.3.2-hotfix2",
            up_to_date=False,
            supported=True,
        ),
        app_version="0.3.2-hotfix3",
    )

    assert notice["visible"] is True
    assert notice["required"] is False
    assert notice["label"] == "有新版"
    assert "最新：v0.3.2-hotfix4" in notice["tooltip"]
    assert "停止支持" not in notice["tooltip"]


def test_0_3_3_hotfix2_source_hides_update_for_hotfix4_current_manifest():
    notice = svn.build_update_notice(
        _update_status(
            app="0.3.3-hotfix2",
            latest="0.3.2-hotfix4",
            min="0.3.1",
            up_to_date=True,
            supported=True,
        ),
        app_version="0.3.3-hotfix2",
    )

    assert notice["visible"] is False
    assert notice["required"] is False
    assert notice["label"] == ""
    assert "已是最新版" in notice["tooltip"]


def test_0_3_2_hotfix4_source_shows_0_3_3_hotfix2_as_optional_update():
    notice = svn.build_update_notice(
        _update_status(
            app="0.3.2-hotfix4",
            latest="0.3.3-hotfix2",
            min="0.3.2-hotfix2",
            up_to_date=False,
            supported=True,
        ),
        app_version="0.3.2-hotfix4",
    )

    assert notice["visible"] is True
    assert notice["required"] is False
    assert notice["label"] == "有新版"
    assert "最新：v0.3.3-hotfix2" in notice["tooltip"]
    assert "停止支持" not in notice["tooltip"]


def test_notice_distinguishes_optional_and_required_updates():
    optional = svn.build_update_notice(_update_status(), app_version="0.2.9-r1")
    assert optional == {
        "visible": True,
        "required": False,
        "label": "有新版",
        "url": "https://downloads.example/bidking.zip",
        "tooltip": (
            "当前：v0.2.9-r1\n"
            "最新：v0.2.9-hotfix1\n"
            "发现新版本，点击打开下载地址。\n"
            "修复启动与报价问题"
        ),
    }

    required = svn.build_update_notice(
        _update_status(min="0.2.9-hotfix1", supported=False),
        app_version="0.2.9-r1",
    )
    assert required["visible"] is True
    assert required["required"] is True
    assert required["label"] == "需更新"
    assert "停止支持" in required["tooltip"]


def test_notice_without_download_url_stays_visible_but_is_not_click_promising():
    notice = svn.build_update_notice(_update_status(url=""), app_version="0.2.9-r1")
    assert notice["visible"] is True
    assert notice["url"] == ""
    assert "联系作者" in notice["tooltip"]
    assert "点击" not in notice["tooltip"]


def test_all_capture_entrypoints_publish_their_single_startup_check():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "scripts/run_npcap_live_monitor.py",
        "scripts/run_wss_live_monitor.py",
        "scripts/run_windivert_live_monitor.py",
        "scripts/run_fatbeans_webhook_monitor.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert source.count("check_and_publish_startup_version(log_dir)") == 1
        assert "st = version_check.check_from_local_config()" not in source


class _FakeTip:
    def __init__(self):
        self.text = ""

    def set_text(self, text):
        self.text = str(text)


class _FakeButton:
    def __init__(self):
        self.manager = ""
        self.options = {}

    def configure(self, **options):
        self.options.update(options)

    def winfo_manager(self):
        return self.manager

    def pack(self, **options):
        self.manager = "pack"
        self.options["pack"] = options

    def pack_forget(self):
        self.manager = ""


class _FakeOverlay(SimpleNamespace):
    """Minimal overlay contract for update-notice layout refreshes."""

    def __init__(self, **values):
        super().__init__(**values)
        self.header_refit_calls = 0

    def _refit_header_title_wrap(self):
        self.header_refit_calls += 1


def test_overlay_badge_is_to_the_left_of_version_and_uses_semantic_state():
    from test_live_overlay import _ahmad_overlay_module

    module = _ahmad_overlay_module()
    overlay = _FakeOverlay(
        _startup_update_notice_applied=False,
        _startup_update_url="",
        version_tip=_FakeTip(),
        update_notice_tip=_FakeTip(),
        update_notice=_FakeButton(),
    )
    latest = "0.3.4-hotfix3"
    status = _update_status(
        app=module.HERO_REF_DISPLAY_VERSION,
        latest=latest,
        min="0.2.0",
    )

    module.AhmadTkOverlay._apply_startup_update_notice(overlay, status)

    assert overlay._startup_update_notice_applied is True
    assert overlay.update_notice.manager == "pack"
    assert overlay.update_notice.options["pack"] == {"side": "right", "padx": (0, 6)}
    assert overlay.update_notice.options["text"] == "有新版"
    assert overlay.update_notice.options["fg"] == module.WARM
    assert f"最新：v{latest}" in overlay.update_notice_tip.text

    required = dict(status, min=latest, supported=False)
    module.AhmadTkOverlay._apply_startup_update_notice(overlay, required)
    assert overlay.update_notice.options["text"] == "需更新"
    assert overlay.update_notice.options["fg"] == module.BAD
    assert overlay.header_refit_calls == 2


def test_overlay_badge_stays_hidden_when_source_matches_latest():
    from test_live_overlay import _ahmad_overlay_module

    module = _ahmad_overlay_module()
    overlay = _FakeOverlay(
        _startup_update_notice_applied=False,
        _startup_update_url="",
        version_tip=_FakeTip(),
        update_notice_tip=_FakeTip(),
        update_notice=_FakeButton(),
    )
    status = _update_status(
        app=module.HERO_REF_DISPLAY_VERSION,
        latest=module.HERO_REF_DISPLAY_VERSION,
        min="0.2.0",
        up_to_date=True,
    )

    module.AhmadTkOverlay._apply_startup_update_notice(overlay, status)

    assert status["up_to_date"] is True
    assert overlay.update_notice.manager == ""
    assert overlay.header_refit_calls == 1


def test_overlay_update_click_accepts_https_and_rejects_plain_http(monkeypatch):
    from test_live_overlay import _ahmad_overlay_module

    module = _ahmad_overlay_module()
    opened = []
    monkeypatch.setattr(module.webbrowser, "open", lambda url, **kwargs: opened.append((url, kwargs)))
    overlay = _FakeOverlay(
        _startup_update_url="https://downloads.example/bidking.zip",
        update_notice_tip=_FakeTip(),
    )
    assert module.AhmadTkOverlay._open_startup_update_url(overlay) == "break"
    assert opened == [
        ("https://downloads.example/bidking.zip", {"new": 2, "autoraise": True})
    ]

    overlay._startup_update_url = "http://downloads.example/bidking.zip"
    assert module.AhmadTkOverlay._open_startup_update_url(overlay) == "break"
    assert len(opened) == 1
    assert "地址不可用" in overlay.update_notice_tip.text


@pytest.mark.parametrize("reason", ["revoked", "device_revoked"])
def test_overlay_authoritative_stop_suppresses_update_badge_url_and_click(
    monkeypatch,
    reason,
):
    from test_live_overlay import _ahmad_overlay_module

    module = _ahmad_overlay_module()
    opened = []
    monkeypatch.setattr(
        module.webbrowser,
        "open",
        lambda url, **kwargs: opened.append((url, kwargs)),
    )
    overlay = _FakeOverlay(
        _act_state={"reason": reason},
        _startup_update_notice_applied=False,
        _startup_update_url="",
        version_tip=_FakeTip(),
        update_notice_tip=_FakeTip(),
        update_notice=_FakeButton(),
    )
    status = _update_status(
        app=module.HERO_REF_DISPLAY_VERSION,
        latest="0.3.4-hotfix3",
        min="0.3.4-hotfix3",
        supported=False,
    )

    module.AhmadTkOverlay._apply_startup_update_notice(overlay, status)

    assert overlay._startup_update_notice_applied is True
    assert overlay._startup_update_notice_suppressed is True
    assert overlay.update_notice.manager == ""
    assert overlay._startup_update_url == ""
    assert overlay.header_refit_calls == 1
    assert module.AhmadTkOverlay._open_startup_update_url(overlay) == "break"
    assert opened == []


def test_overlay_upgrade_required_keeps_required_update_badge_and_download(
    monkeypatch,
):
    from test_live_overlay import _ahmad_overlay_module

    module = _ahmad_overlay_module()
    opened = []
    monkeypatch.setattr(
        module.webbrowser,
        "open",
        lambda url, **kwargs: opened.append((url, kwargs)),
    )
    overlay = _FakeOverlay(
        _act_state={"reason": "upgrade_required"},
        _startup_update_notice_applied=False,
        _startup_update_url="",
        version_tip=_FakeTip(),
        update_notice_tip=_FakeTip(),
        update_notice=_FakeButton(),
    )
    status = _update_status(
        app=module.HERO_REF_DISPLAY_VERSION,
        latest="0.3.4-hotfix3",
        min="0.3.4-hotfix3",
        supported=False,
    )

    module.AhmadTkOverlay._apply_startup_update_notice(overlay, status)

    assert overlay._startup_update_notice_suppressed is False
    assert overlay.update_notice.manager == "pack"
    assert overlay.update_notice.options["text"] == "需更新"
    assert overlay._startup_update_url == "https://downloads.example/bidking.zip"
    assert overlay.header_refit_calls == 1
    assert module.AhmadTkOverlay._open_startup_update_url(overlay) == "break"
    assert opened == [
        ("https://downloads.example/bidking.zip", {"new": 2, "autoraise": True})
    ]
