# -*- coding: utf-8 -*-
"""版本检测地基的契约测试(src/bidking_lab/live/version_check.py + 服务器 /version)。

锁住:版本号比较(数字段、hotfix 修订、补位)、游戏版本 env 覆盖、上传URL→版本URL 推导、
与服务器比对(连得上/连不上)、状态行渲染,以及服务器 _public_version_payload 的默认与下发。
2026-06-25 新增(版本发行/自动更新/会员功能地基)。"""
from __future__ import annotations

import json
import unicodedata
from contextlib import contextmanager

import pytest

from bidking_lab.live import version_check as vc


# ---- 版本号比较 ----
def test_version_compare_basic_and_suffix():
    assert vc._ge("0.2.1", "0.2.0")
    assert vc._ge("0.2.1", "0.2.1")          # 相等
    assert not vc._ge("0.2.0", "0.2.1")
    assert vc._ge("0.2.10", "0.2.2")         # 数字比较, 非字典序(10 > 2)
    assert vc._ge("0.2.0-hotfix2", "0.2.0")  # hotfix2 晚于同基线正式版
    assert not vc._ge("0.2.7-hotfix2", "0.2.7-hotfix3")
    assert vc._ge("0.2.7-hotfix3", "0.2.7-hotfix2")
    assert not vc._ge("0.2.7", "0.2.7-hotfix1")
    assert vc._ge("0.2.8", "0.2.7-hotfix3")
    assert not vc._ge("0.2.7-hotfix3", "0.2.8")
    assert vc._ge("0.2.8-hotfix1", "0.2.8")
    assert vc._ge("0.2.9", "0.2.8-hotfix1")
    assert not vc._ge("0.2.8-hotfix1", "0.2.9")
    assert vc._ge("0.2.9-r1", "0.2.9")
    assert not vc._ge("0.2.9", "0.2.9-r1")
    assert not vc._ge("0.2.9-r1", "0.2.9-r2")
    assert vc._ge("0.2.9-r2", "0.2.9-r1")
    assert not vc._ge("0.2.9-r1", "0.2.9-hotfix1")
    assert vc._ge("0.2.9-hotfix1", "0.2.9-r1")
    assert not vc._ge("0.2.9-r99", "0.2.9-hotfix1")
    assert not vc._ge("0.2.9-hotfix1", "0.2.9-hotfix2")
    assert vc._ge("0.3.0", "0.2.9-hotfix2")
    assert not vc._ge("0.2.9-hotfix2", "0.3.0")
    assert vc._ge("0.3.0-hotfix1", "0.3.0")
    assert not vc._ge("0.3.0", "0.3.0-hotfix1")
    assert vc._ge("0.3.0-hotfix1-r1", "0.3.0-hotfix1")
    assert not vc._ge("0.3.0-hotfix1", "0.3.0-hotfix1-r1")
    assert not vc._ge("0.3.0-hotfix1-r1", "0.3.0-hotfix2")
    assert vc._ge("0.3.0-hotfix2", "0.3.0-hotfix1-r1")
    assert not vc._ge("0.3.0-hotfix2", "0.3.0-hotfix2-r1")
    assert vc._ge("0.3.0-hotfix2-r1", "0.3.0-hotfix2")
    assert not vc._ge("0.3.0-hotfix2-r1", "0.3.1")
    assert vc._ge("0.3.1", "0.3.0-hotfix2-r1")
    assert not vc._ge("0.3.1", "0.3.1-hotfix1")
    assert vc._ge("0.3.1-hotfix1", "0.3.1")
    assert not vc._ge("0.3.1-hotfix1", "0.3.1-hotfix2")
    assert vc._ge("0.3.1-hotfix2", "0.3.1-hotfix1")
    assert not vc._ge("0.3.1-hotfix2", "0.3.1-hotfix3")
    assert vc._ge("0.3.1-hotfix3", "0.3.1-hotfix2")
    assert not vc._ge("0.3.1-hotfix3", "0.3.1-hotfix4")
    assert vc._ge("0.3.1-hotfix4", "0.3.1-hotfix3")
    assert not vc._ge("0.3.1-hotfix4", "0.3.1-hotfix5")
    assert vc._ge("0.3.1-hotfix5", "0.3.1-hotfix4")
    assert not vc._ge("0.3.1-hotfix5", "0.3.2")
    assert vc._ge("0.3.2", "0.3.1-hotfix5")
    assert not vc._ge("0.3.2", "0.3.2-hotfix1")
    assert vc._ge("0.3.2-hotfix1", "0.3.2")
    assert not vc._ge("0.3.2-hotfix1", "0.3.2-hotfix2")
    assert vc._ge("0.3.2-hotfix2", "0.3.2-hotfix1")
    assert not vc._ge("0.3.2-hotfix2", "0.3.2-hotfix3")
    assert vc._ge("0.3.2-hotfix3", "0.3.2-hotfix2")
    assert not vc._ge("0.3.2-hotfix3", "0.3.2-hotfix4")
    assert vc._ge("0.3.2-hotfix4", "0.3.2-hotfix3")
    assert not vc._ge("0.3.2-hotfix4", "0.3.3")
    assert vc._ge("0.3.3", "0.3.2-hotfix4")
    assert not vc._ge("0.3.3", "0.3.3-hotfix1")
    assert vc._ge("0.3.3-hotfix1", "0.3.3")
    assert not vc._ge("0.3.3-hotfix1", "0.3.3-hotfix2")
    assert vc._ge("0.3.3-hotfix2", "0.3.3-hotfix1")
    assert not vc._ge("0.3.3-hotfix2", "0.3.3-hotfix3")
    assert vc._ge("0.3.3-hotfix3", "0.3.3-hotfix2")
    assert not vc._ge("0.3.3-hotfix3", "0.3.3-hotfix4")
    assert vc._ge("0.3.3-hotfix4", "0.3.3-hotfix3")
    assert not vc._ge("0.3.3-hotfix4", "0.3.4")
    assert vc._ge("0.3.4", "0.3.3-hotfix4")
    assert not vc._ge("0.3.4", "0.3.4-hotfix1")
    assert vc._ge("0.3.4-hotfix1", "0.3.4")
    assert not vc._ge("0.3.4-hotfix1", "0.3.4-hotfix2")
    assert vc._ge("0.3.4-hotfix2", "0.3.4-hotfix1")
    assert vc._ge("1.0.0", "0.9.9")


def test_release_version_order_is_strict_across_candidate_and_hotfix_channels():
    ordered = (
        "0.2.9",
        "0.2.9-r1",
        "0.2.9-r2",
        "0.2.9-hotfix1",
        "0.2.9-hotfix2",
        "0.3.0",
        "0.3.0-hotfix1",
        "0.3.0-hotfix1-r1",
        "0.3.0-hotfix2",
        "0.3.0-hotfix2-r1",
        "0.3.1",
        "0.3.1-hotfix1",
        "0.3.1-hotfix2",
        "0.3.1-hotfix3",
        "0.3.1-hotfix4",
        "0.3.1-hotfix5",
        "0.3.2",
        "0.3.2-hotfix1",
        "0.3.2-hotfix2",
        "0.3.2-hotfix3",
        "0.3.2-hotfix4",
        "0.3.3",
        "0.3.3-hotfix1",
        "0.3.3-hotfix2",
        "0.3.3-hotfix3",
        "0.3.3-hotfix4",
        "0.3.4",
        "0.3.4-hotfix1",
        "0.3.4-hotfix2",
        "0.3.5",
        "0.3.5-hotfix1",
    )
    parsed = [vc._vtuple(value) for value in ordered]
    assert all(value is not None for value in parsed)
    assert parsed == sorted(parsed)
    assert len(set(parsed)) == len(parsed)
    assert not vc._ge("0.2.9-r99", "0.2.9-hotfix1")


@pytest.mark.parametrize(
    "value",
    (
        None,
        True,
        209,
        [],
        "",
        "0.2",
        "0.2.9.1",
        "v0.2.9",
        "0.2.9-preview1",
        "0.2.9-unknown1",
        "0.2.9-preview-hotfix1",
        "0.2.9-r",
        "0.2.9-hotfix",
        "0.3.0-r1-r1",
        "0.3.0-hotfix1-r",
        "０.３.１-hotfix２",  # fullwidth decimal digits
        "٠.٣.١-hotfix٢",  # Arabic-Indic decimal digits
        "०.३.१-hotfix२",  # Devanagari decimal digits
        " 0.3.1-hotfix2",
        "0.3.1-hotfix2\t",
        "0.3.1-hotfix2\n",
        "0.3.1-hotfİx2",
        "0.3.1-hotfıx2",
    ),
)
def test_release_version_parser_rejects_malformed_values(value):
    assert vc._vtuple(value) is None
    assert vc._ge(value, "0.2.9") is False
    assert vc._ge("0.2.9", value) is False


def test_release_version_parser_rejects_all_non_ascii_decimal_digits():
    digits = tuple(
        chr(codepoint)
        for codepoint in range(0x110000)
        if unicodedata.category(chr(codepoint)) == "Nd"
        and not 0x30 <= codepoint <= 0x39
    )
    assert len(digits) >= 640
    for digit in digits:
        for value in (
            f"{digit}.3.1-hotfix2",
            f"0.{digit}.1-hotfix2",
            f"0.3.{digit}-hotfix2",
            f"0.3.1-r{digit}",
            f"0.3.1-hotfix{digit}",
            f"0.3.1-hotfix2-r{digit}",
        ):
            assert vc._vtuple(value) is None


def test_release_version_parser_rejects_protocol_objects_without_calls():
    class ExplosiveStr(str):
        calls = 0

        def explode(self, *args, **kwargs):
            type(self).calls += 1
            raise AssertionError("str subclass protocol must not be called")

        __str__ = explode
        strip = explode
        lower = explode
        casefold = explode
        __eq__ = explode

    class ExplosiveProtocol:
        calls = 0

        def explode(self, *args, **kwargs):
            type(self).calls += 1
            raise AssertionError("non-str protocol must not be called")

        __str__ = explode
        strip = explode
        lower = explode
        casefold = explode
        __eq__ = explode
        __bool__ = explode
        __int__ = explode

    values = (ExplosiveStr("0.3.1-hotfix2"), ExplosiveProtocol())
    for value in values:
        assert vc._vtuple(value) is None
        assert vc._ge(value, "0.3.1") is False
        assert vc._ge("0.3.1", value) is False
    assert ExplosiveStr.calls == 0
    assert ExplosiveProtocol.calls == 0


def test_version_url_from_ingest():
    assert vc.version_url_from_ingest("http://127.0.0.1:18900/ingest") == "http://127.0.0.1:18900/version"
    assert vc.version_url_from_ingest("https://h/x/ingest") == "https://h/x/version"


def test_detect_game_version_env_override(monkeypatch):
    monkeypatch.setattr(vc, "_find_game_version_file", lambda: None)  # 隔离: 不依赖本机是否装了游戏
    monkeypatch.delenv("BIDKING_GAME_VERSION", raising=False)
    assert vc.detect_game_version() is None                  # 无源/无 env -> None(不阻塞)
    monkeypatch.setenv("BIDKING_GAME_VERSION", "  v6.25  ")
    assert vc.detect_game_version() == "v6.25"               # env 覆盖 + strip


def test_detect_game_version_reads_yoo_file(tmp_path, monkeypatch):
    monkeypatch.delenv("BIDKING_GAME_VERSION", raising=False)
    vf = tmp_path / "DefaultPackage.version"
    vf.write_text("70e6e4-2026-06-25-762\n", encoding="utf-8")
    monkeypatch.setattr(vc, "_find_game_version_file", lambda: vf)
    assert vc.detect_game_version() == "70e6e4-2026-06-25-762"  # 读 YooAsset 版本文件 + strip


def test_app_version_nonempty():
    v = vc.app_version()
    assert isinstance(v, str) and v                          # 至少回退常量, 永不空


def test_source_product_version_is_0_3_5():
    """The source target is 0.3.5; candidate and release are separate stages."""
    assert vc.app_version() == "0.3.5"


@contextmanager
def _fake_urlopen(payload, raise_exc=None):
    """临时替换 version_check 里的 urlopen: 返回 payload(dict->json) 或抛 raise_exc。"""
    class _Resp:
        def __init__(self, body): self._b = body
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake(url, timeout=0, **kwargs):
        if raise_exc:
            raise raise_exc
        return _Resp(json.dumps(payload).encode("utf-8"))

    orig = vc.urllib.request.urlopen
    vc.urllib.request.urlopen = _fake
    try:
        yield
    finally:
        vc.urllib.request.urlopen = orig


def test_check_against_server_up_to_date(monkeypatch):
    monkeypatch.delenv("BIDKING_GAME_VERSION", raising=False)
    monkeypatch.setattr(vc, "app_version", lambda: "0.2.2")
    with _fake_urlopen({"latest": "0.2.2", "min": "0.2.0", "notice": "", "game_version": None}):
        st = vc.check_against_server("https://h/ingest")
    assert st["ok"] and st["up_to_date"] and st["supported"]


def test_0_3_2_hotfix3_source_accepts_hotfix2_production_manifest(monkeypatch):
    monkeypatch.setattr(vc, "app_version", lambda: "0.3.2-hotfix3")
    with _fake_urlopen({"latest": "0.3.2-hotfix2", "min": "0.3.1"}):
        st = vc.check_against_server("https://h/ingest")
    assert st["ok"] is True
    assert st["up_to_date"] is True
    assert st["supported"] is True
    status_line = vc.format_status_line(st)
    assert "已是最新" in status_line
    assert "有新版" not in status_line
    assert "需更新" not in status_line


def test_0_3_2_hotfix4_source_accepts_hotfix3_current_manifest(monkeypatch):
    monkeypatch.setattr(vc, "app_version", lambda: "0.3.2-hotfix4")
    with _fake_urlopen({"latest": "0.3.2-hotfix3", "min": "0.3.1"}):
        st = vc.check_against_server("https://h/ingest")
    assert st["ok"] is True
    assert st["up_to_date"] is True
    assert st["supported"] is True
    status_line = vc.format_status_line(st)
    assert "已是最新" in status_line
    assert "有新版" not in status_line
    assert "需更新" not in status_line


def test_0_3_2_hotfix3_detects_hotfix4_update(monkeypatch):
    monkeypatch.setattr(vc, "app_version", lambda: "0.3.2-hotfix3")
    with _fake_urlopen({"latest": "0.3.2-hotfix4", "min": "0.3.1"}):
        st = vc.check_against_server("https://h/ingest")
    assert st["ok"] is True
    assert st["up_to_date"] is False
    assert st["supported"] is True
    status_line = vc.format_status_line(st)
    assert "有新版 0.3.2-hotfix4" in status_line
    assert "需更新" not in status_line


def test_0_3_3_hotfix2_source_accepts_hotfix4_current_manifest(monkeypatch):
    monkeypatch.setattr(vc, "app_version", lambda: "0.3.3-hotfix2")
    with _fake_urlopen({"latest": "0.3.2-hotfix4", "min": "0.3.1"}):
        st = vc.check_against_server("https://h/ingest")
    assert st["ok"] is True
    assert st["up_to_date"] is True
    assert st["supported"] is True
    status_line = vc.format_status_line(st)
    assert "已是最新" in status_line
    assert "需更新" not in status_line


def test_0_3_2_hotfix4_detects_0_3_3_hotfix1_update(monkeypatch):
    monkeypatch.setattr(vc, "app_version", lambda: "0.3.2-hotfix4")
    with _fake_urlopen({"latest": "0.3.3-hotfix1", "min": "0.3.2-hotfix2"}):
        st = vc.check_against_server("https://h/ingest")
    assert st["ok"] is True
    assert st["up_to_date"] is False
    assert st["supported"] is True
    status_line = vc.format_status_line(st)
    assert "有新版 0.3.3-hotfix1" in status_line
    assert "需更新" not in status_line


def test_0_3_3_hotfix1_detects_0_3_3_hotfix2_update(monkeypatch):
    monkeypatch.setattr(vc, "app_version", lambda: "0.3.3-hotfix1")
    with _fake_urlopen({"latest": "0.3.3-hotfix2", "min": "0.3.2-hotfix2"}):
        st = vc.check_against_server("https://h/ingest")
    assert st["ok"] is True
    assert st["up_to_date"] is False
    assert st["supported"] is True
    assert "有新版 0.3.3-hotfix2" in vc.format_status_line(st)


def test_0_3_2_hotfix3_is_newer_than_hotfix2_production(monkeypatch):
    monkeypatch.setattr(vc, "app_version", lambda: "0.3.2-hotfix3")
    with _fake_urlopen({"latest": "0.3.2-hotfix2", "min": "0.3.1"}):
        st = vc.check_against_server("https://h/ingest")
    assert st["ok"] is True
    assert st["up_to_date"] is True
    assert st["supported"] is True


def test_0_3_2_hotfix3_is_newer_than_discarded_0_3_2_candidate(monkeypatch):
    monkeypatch.setattr(vc, "app_version", lambda: "0.3.2-hotfix3")
    with _fake_urlopen({"latest": "0.3.2", "min": "0.3.1"}):
        st = vc.check_against_server("https://h/ingest")
    assert st["ok"] is True
    assert st["up_to_date"] is True
    assert st["supported"] is True


def test_check_against_server_has_newer_and_unsupported(monkeypatch):
    monkeypatch.setattr(vc, "app_version", lambda: "0.1.9")
    with _fake_urlopen({"latest": "0.2.2", "min": "0.2.0"}):
        st = vc.check_against_server("https://h/ingest")
    assert st["ok"]
    assert st["up_to_date"] is False           # 0.1.9 < 0.2.2
    assert st["supported"] is False            # 0.1.9 < min 0.2.0
    assert "有新版 0.2.2" in vc.format_status_line(st)
    assert "需更新" in vc.format_status_line(st)


def test_check_against_server_detects_newer_hotfix(monkeypatch):
    monkeypatch.setattr(vc, "app_version", lambda: "0.2.7-hotfix2")
    with _fake_urlopen({"latest": "0.2.7-hotfix3", "min": "0.2.0"}):
        st = vc.check_against_server("https://api.example.invalid/ingest")
    assert st["ok"]
    assert st["up_to_date"] is False
    assert "有新版 0.2.7-hotfix3" in vc.format_status_line(st)


def test_check_against_server_detects_hotfix1_newer_than_r1(monkeypatch):
    monkeypatch.setattr(vc, "app_version", lambda: "0.2.9-r1")
    with _fake_urlopen({"latest": "0.2.9-hotfix1", "min": "0.2.0"}):
        st = vc.check_against_server("https://api.example.invalid/ingest")
    assert st["ok"]
    assert st["up_to_date"] is False
    assert "有新版 0.2.9-hotfix1" in vc.format_status_line(st)


def test_check_against_server_unreachable_is_silent(monkeypatch):
    monkeypatch.setattr(vc, "app_version", lambda: "0.2.1")
    with _fake_urlopen(None, raise_exc=OSError("conn refused")):
        st = vc.check_against_server("https://h/ingest")
    assert st["ok"] is False                   # 连不上 = 当作无信息
    assert st["up_to_date"] is True            # 不误报"有新版"
    assert "未连上版本服务" in vc.format_status_line(st)


def test_game_version_match(monkeypatch):
    monkeypatch.setenv("BIDKING_GAME_VERSION", "6.25")
    monkeypatch.setattr(vc, "app_version", lambda: "0.2.2")
    with _fake_urlopen({"latest": "0.2.2", "game_version": "6.25"}):
        st = vc.check_against_server("https://h/ingest")
    assert st["game_match"] is True
    with _fake_urlopen({"latest": "0.2.2", "game_version": "6.26"}):
        st2 = vc.check_against_server("https://h/ingest")
    assert st2["game_match"] is False
    assert "不匹配" in vc.format_status_line(st2)


# ---- 服务器侧 /version 载荷 ----
def _load_server():
    import importlib.util
    import sys
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "scripts" / "sample_collector_server.py"
    module_name = "sample_collector_server"
    spec = importlib.util.spec_from_file_location(module_name, p)
    mod = importlib.util.module_from_spec(spec)
    scripts_dir = str(p.parent)
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = mod
    sys.path.insert(0, scripts_dir)
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(scripts_dir)
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
    return mod


def test_server_version_payload_default_and_populated(tmp_path):
    # 0710 热重载后清单从 _VM_PATH 文件读(mtime 缓存), 不再是启动时灌好的 _VERSION_MANIFEST 全局。
    srv = _load_server()
    srv._VM_PATH = tmp_path / "version_manifest.json"   # 文件不存在 = 不下发
    d = srv._public_version_payload()
    assert d["latest"] is None and d["url"] == "" and d["notice"] == ""   # 缺清单=不下发, 不崩客户端
    srv._VM_PATH.write_text(
        json.dumps({"latest": "0.2.2", "min": "0.2.0", "url": "http://x", "game_version": "6.25"}),
        encoding="utf-8")
    d2 = srv._public_version_payload()
    assert d2["latest"] == "0.2.2" and d2["min"] == "0.2.0" and d2["game_version"] == "6.25"


def test_server_allows_version_fields():
    srv = _load_server()
    assert "app_version" in srv._ALLOWED and "game_version" in srv._ALLOWED
