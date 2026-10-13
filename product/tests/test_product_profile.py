"""Product-profile isolation gates for the network diagnostic cohort."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "hero_ref" / "tools"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from product_profile import (  # noqa: E402
    PRODUCT_PROFILE_NETWORK_DIAGNOSTIC,
    PRODUCT_PROFILE_STANDARD,
    STANDARD_SUBSCRIPTION_PRICE_ROWS,
    STANDARD_TOKEN_ENTRY_ACTION_TEXT,
    network_diagnostic_boundary_text,
    normalize_product_profile,
    render_package_manual_text,
    resolve_product_profile,
    token_entry_action_text,
)


def _public_manual() -> str:
    return (ROOT / "scripts" / "pkg_assets" / "使用说明.txt").read_text(
        encoding="utf-8-sig"
    )


def test_standard_profile_is_default_and_keeps_public_manual_exactly() -> None:
    source = _public_manual()

    assert normalize_product_profile(None) == PRODUCT_PROFILE_STANDARD
    assert normalize_product_profile("unknown-profile") == PRODUCT_PROFILE_STANDARD
    assert render_package_manual_text(source, PRODUCT_PROFILE_STANDARD) == source
    assert "30 天 ¥15.88" in source
    assert "30 天 ¥11.11" not in source
    assert "90/365 天有效期内可用" in source
    assert STANDARD_TOKEN_ENTRY_ACTION_TEXT == "点此输入/购买 token"
    assert (
        token_entry_action_text(PRODUCT_PROFILE_STANDARD)
        == "点此输入/购买 token"
    )
    assert (
        token_entry_action_text(PRODUCT_PROFILE_NETWORK_DIAGNOSTIC)
        == "输入已有 token"
    )
    assert STANDARD_SUBSCRIPTION_PRICE_ROWS == (
        (
            "绑定设备：",
            ("30天 ¥15.88", "90天 ¥35.88", "365天 ¥88.88"),
        ),
        (
            "可换绑：",
            ("30天 ¥24.88", "90天 ¥48.88", "365天 ¥118.88"),
        ),
    )


def test_network_diagnostic_profile_replaces_commercial_contract() -> None:
    rendered = render_package_manual_text(
        _public_manual(),
        PRODUCT_PROFILE_NETWORK_DIAGNOSTIC,
    )

    required = (
        "网络诊断内测包使用说明",
        "保留已有 token 激活与计算功能",
        "购买/再次购买与猫猫新权益暂不在本包启用",
        "本包不代表 90 天猫猫等新权益已获得服务端授权",
        "输入已有 token",
        "网络诊断内测支持",
    )
    forbidden = (
        "30 天 ¥12.88",
        "90 天 ¥28.88",
        "365 天 ¥68.88",
        "30 天 ¥19.99",
        "90 天 ¥39.99",
        "365 天 ¥89.99",
        "30 天 ¥15.88",
        "90 天 ¥35.88",
        "365 天 ¥88.88",
        "30 天 ¥24.88",
        "90 天 ¥48.88",
        "365 天 ¥118.88",
        "https://www.kufaka.com/shop/GGTO42L9",
        "店铺二维码",
        "90/365 天有效期内可用",
        "打开「购买与续费」文件夹",
        "点此输入/购买 token",
    )
    assert all(text in rendered for text in required)
    assert all(text not in rendered for text in forbidden)
    assert "标准新设备试用为【12 小时】" in rendered
    assert "【启动会做什么 · 需要什么】" in rendered
    assert "【数据与隐私】" in rendered

    note = network_diagnostic_boundary_text()
    assert "ProductProfile: network-diagnostic" in note
    assert "保留已有 token 激活与计算功能" in note
    assert "本包不代表服务端已授权" in note
    assert all(text not in note for text in forbidden[:6])


def test_compatibility_guidance_is_prominent_and_consistent_across_profiles() -> None:
    required = (
        "火绒、360、游戏加速器",
        "整个文件夹",
        "信任区/白名单",
        "完整退出",
        "仅关闭主窗口可能仍有后台",
        "先启动 Hero Ref",
        "再启动游戏",
    )
    standard_manual = _public_manual()
    diagnostic_manual = render_package_manual_text(
        standard_manual,
        PRODUCT_PROFILE_NETWORK_DIAGNOSTIC,
    )
    boundary = network_diagnostic_boundary_text()
    launcher = (ROOT / "scripts" / "start_live_npcap_overlay.ps1").read_text(
        encoding="utf-8-sig"
    )
    diagnostics = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(
        encoding="utf-8-sig"
    )
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )
    overlay = (TOOLS / "ahmad_tk_overlay.py").read_text(encoding="utf-8")

    for surface in (
        standard_manual,
        diagnostic_manual,
        boundary,
        launcher,
        diagnostics,
        build,
        overlay,
    ):
        assert all(text in surface for text in required)
    for surface in (standard_manual, diagnostic_manual, boundary, launcher, diagnostics):
        assert "不要卸载或永久关闭" in surface
        assert "不要反复更换 token" in surface
    assert "Huorong compatibility pattern confirmed on two machines" not in launcher
    assert "火绒是所有失败机器唯一根因" not in overlay


def test_manifest_profile_resolution_is_explicit_and_fail_safe(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    nested_file = package_root / "hero_ref" / "tools" / "probe.py"
    nested_file.parent.mkdir(parents=True)
    nested_file.write_text("# probe", encoding="utf-8")

    assert resolve_product_profile(nested_file) == PRODUCT_PROFILE_STANDARD

    manifest = package_root / "BUILD_MANIFEST.txt"
    manifest.write_text(
        "PackageVersion: v0.3.0-hotfix1\nProductProfile: network-diagnostic\n",
        encoding="utf-8",
    )
    assert resolve_product_profile(nested_file) == PRODUCT_PROFILE_NETWORK_DIAGNOSTIC

    manifest.write_text(
        "PackageVersion: v0.3.0-hotfix1\nProductProfile: malformed\n",
        encoding="utf-8",
    )
    assert resolve_product_profile(nested_file) == PRODUCT_PROFILE_STANDARD

    manifest.write_text("PackageVersion: v0.3.0-hotfix1\n", encoding="utf-8")
    assert resolve_product_profile(nested_file) == PRODUCT_PROFILE_STANDARD


def test_manual_renderer_rejects_drifted_source() -> None:
    with pytest.raises(ValueError, match="manual"):
        render_package_manual_text(
            "not the package manual",
            PRODUCT_PROFILE_NETWORK_DIAGNOSTIC,
        )


def test_build_profile_has_auditable_positive_and_negative_arms() -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert '[string]$ProductProfile = "standard"' in build
    assert '[ValidateSet("standard", "network-diagnostic")]' in build
    canonical_at = build.index(
        "$ProductProfile = $ProductProfile.ToLowerInvariant()"
    )
    derived_at = build.index(
        '$IsNetworkDiagnosticCohort = ($ProductProfile -ceq "network-diagnostic")'
    )
    assert canonical_at < derived_at
    assert '$IsNetworkDiagnosticCohort = ($ProductProfile -ceq "network-diagnostic")' in build
    assert 'ProductProfile: $ProductProfile' in build
    assert 'CommercialSurfaceMode: $CommercialSurfaceMode' in build
    assert '"disabled-network-diagnostic-cohort"' in build
    assert '"-network-diagnostic"' in build
    assert 'Join-Path $Stage "网络诊断内测说明"' in build
    assert 'Join-Path $Stage "购买与续费"' in build
    assert "if ($IsNetworkDiagnosticCohort)" in build
    assert "if (-not $IsNetworkDiagnosticCohort)" in build
    assert '$runtimeHeroAssets = @("bidking_cat.ico")' in build
    assert '$runtimeHeroAssets += "support_qr.png"' in build
    assert "网络诊断内测包不得包含购买与续费目录" in build


def test_packaged_profile_sources_do_not_embed_standard_purchase_action() -> None:
    profile_source = (TOOLS / "product_profile.py").read_text(encoding="utf-8")
    overlay_source = (TOOLS / "ahmad_tk_overlay.py").read_text(encoding="utf-8")
    forbidden_prices = (
        "¥12.88",
        "¥11.11",
        "¥28.88",
        "¥68.88",
        "¥19.99",
        "¥39.99",
        "¥89.99",
    )

    assert "点此输入/购买 token" not in profile_source
    assert "点此输入/购买 token" not in overlay_source
    assert all(price not in profile_source for price in forbidden_prices)
    assert all(price not in overlay_source for price in forbidden_prices)
    assert 'STANDARD_TOKEN_ENTRY_ACTION_TEXT = "".join(' in profile_source
    assert "token_entry_action_text(self.product_profile)" in overlay_source
    assert "STANDARD_SUBSCRIPTION_PRICE_ROWS" in overlay_source


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
@pytest.mark.parametrize(
    ("input_profile", "expected_profile", "expected_diagnostic"),
    [
        ("standard", "standard", False),
        ("network-diagnostic", "network-diagnostic", True),
        ("Network-Diagnostic", "network-diagnostic", True),
        ("NETWORK-DIAGNOSTIC", "network-diagnostic", True),
    ],
)
def test_build_product_profile_is_canonical_before_all_derived_surfaces(
    tmp_path: Path,
    input_profile: str,
    expected_profile: str,
    expected_diagnostic: bool,
) -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )
    parameter = re.search(
        r'(?m)^\s*\[ValidateSet\("standard", "network-diagnostic"\)\]\r?\n'
        r'\s*\[string\]\$ProductProfile = "standard",\s*$',
        build,
    )
    assert parameter is not None

    derived_start = build.index(
        '$IsNetworkDiagnosticCohort = ($ProductProfile -ceq "network-diagnostic")'
    )
    derived_end = build.index("# 版本单一真源", derived_start)
    canonical = re.search(
        r"(?m)^\$ProductProfile\s*=\s*.+$",
        build[:derived_start],
    )
    initialization = (
        (canonical.group(0) + "\n") if canonical is not None else ""
    ) + build[derived_start:derived_end]
    suffix = re.search(
        r'(?m)^\$ProductProfileSuffix = if \(\$IsNetworkDiagnosticCohort\) '
        r'\{ "-network-diagnostic" \} else \{ "" \}$',
        build,
    )
    assert suffix is not None

    probe = tmp_path / "product_profile_probe.ps1"
    probe.write_text(
        "\n".join(
            [
                "param(",
                parameter.group(0),
                '    [string]$Probe = ""',
                ")",
                initialization,
                suffix.group(0),
                "[ordered]@{",
                "    ProductProfile = $ProductProfile",
                "    IsNetworkDiagnosticCohort = $IsNetworkDiagnosticCohort",
                "    CommercialSurfaceMode = $CommercialSurfaceMode",
                '    PackageName = "bidking-live-v0.3.0-hotfix1-encrypted'
                '$ProductProfileSuffix"',
                '    ManifestProductProfile = "ProductProfile: $ProductProfile"',
                "    CopiesCommercialAssets = (-not $IsNetworkDiagnosticCohort)",
                "} | ConvertTo-Json -Compress",
            ]
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            str(POWERSHELL),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(probe),
            "-ProductProfile",
            input_profile,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {
        "ProductProfile": expected_profile,
        "IsNetworkDiagnosticCohort": expected_diagnostic,
        "CommercialSurfaceMode": (
            "disabled-network-diagnostic-cohort"
            if expected_diagnostic
            else "standard"
        ),
        "PackageName": (
            "bidking-live-v0.3.0-hotfix1-encrypted-network-diagnostic"
            if expected_diagnostic
            else "bidking-live-v0.3.0-hotfix1-encrypted"
        ),
        "ManifestProductProfile": f"ProductProfile: {expected_profile}",
        "CopiesCommercialAssets": not expected_diagnostic,
    }
