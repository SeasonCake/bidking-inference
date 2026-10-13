"""公版商业口径静态门：打包材料、许可协议和购买文案必须与实际档位一致。"""
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import batch_mint  # noqa: E402
import mint_token  # noqa: E402
import pay_service  # noqa: E402


def test_package_carries_license_and_current_public_terms() -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(encoding="utf-8-sig")
    manual = (ROOT / "scripts" / "pkg_assets" / "使用说明.txt").read_text(encoding="utf-8-sig")
    public_stats = (
        ROOT / "scripts" / "pkg_assets" / "公共大数据平台.html"
    ).read_text(encoding="utf-8-sig")
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8-sig")
    overlay = (ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py").read_text(
        encoding="utf-8-sig"
    )
    web_ui = (ROOT / "scripts" / "web_ui.py").read_text(encoding="utf-8-sig")
    activation_server = (ROOT / "scripts" / "activation_server.py").read_text(
        encoding="utf-8-sig"
    )
    announcement = (
        ROOT / "docs" / "releases" / "ANNOUNCEMENT_0.3.0_subscription_pricing_QQ.zh-CN.md"
    ).read_text(encoding="utf-8-sig")

    assert 'Copy-Item $licenseAsset (Join-Path $Stage "LICENSE.txt")' in build
    assert "试用 12 小时" in manual
    assert "试用 1 小时" not in manual + build + license_text
    assert "当前公测服务器临时提供/刷新 12 小时" not in manual + build
    assert "当前公测由服务器临时提供/刷新为【12 小时】" not in manual
    assert "服务器权威额度为准" in manual + build
    spaced_tiers = (
        "30 天 ¥12.88",
        "90 天 ¥28.88",
        "365 天 ¥68.88",
        "30 天 ¥19.99",
        "90 天 ¥39.99",
        "365 天 ¥89.99",
    )
    assert all(term in manual for term in spaced_tiers)
    assert all(term in build for term in spaced_tiers)
    assert all(term in announcement for term in spaced_tiers)
    assert all(
        stale not in overlay
        for stale in (
            '_price_row(1, "绑定新购：",',
            '_price_row(2, "绑定续费：",',
            '_price_row(3, "漫游新购：",',
            '_price_row(4, "漫游续费：",',
        )
    )
    assert "绑定设备档:30 天 ¥12.88 ｜ 90 天 ¥28.88 ｜ 365 天 ¥68.88" in manual
    assert "可漫游档:30 天 ¥19.99 ｜ 90 天 ¥39.99 ｜ 365 天 ¥89.99" in manual
    assert "绑定设备：30 天 ¥12.88 ｜ 90 天 ¥28.88 ｜ 365 天 ¥68.88" in build
    assert "可漫游：30 天 ¥19.99 ｜ 90 天 ¥39.99 ｜ 365 天 ¥89.99" in build
    assert "绑定设备：\n\n- 30 天 ¥12.88\n- 90 天 ¥28.88\n- 365 天 ¥68.88" in announcement
    assert "可漫游（支持换设备重新绑定）：\n\n- 30 天 ¥19.99\n- 90 天 ¥39.99\n- 365 天 ¥89.99" in announcement
    assert "30 天 ¥11.11" not in manual + build + announcement
    assert {
        key: (tier["amount"], tier["days"], tier["roaming"])
        for key, tier in pay_service.TIERS.items()
    } == {
        "m30": ("12.88", 30, False),
        "d90": ("28.88", 90, False),
        "y365": ("68.88", 365, False),
        "r30": ("19.99", 30, True),
        "r90": ("39.99", 90, True),
        "r365": ("89.99", 365, True),
    }
    assert all(
        stale not in manual + build
        for stale in (
            "绑定设备档:30 天 ¥9.9 ｜ 90 天 ¥19.9 ｜ 365 天 ¥49.9",
            "可漫游档:30 天 ¥19.9 ｜ 90 天 ¥39.9 ｜ 365 天 ¥79.9",
            "绑定设备：30 天 ¥9.9 ｜ 90 天 ¥19.9 ｜ 365 天 ¥49.9",
            "可漫游：30 天 ¥19.9 ｜ 90 天 ¥39.9 ｜ 365 天 ¥79.9",
            "¥12.9",
            "365 天 ¥109.9",
            "60 天 ¥",
        )
    )
    assert "公版最高档" in manual
    assert "永久版联系作者" not in manual and "永久 ¥" not in manual
    assert "猫猫:免费 10 分钟 ｜ 30 天 12 小时 ｜ 90/365 天有效期内可用" in manual
    assert "90/365 天在订阅有效期内可用" in announcement
    assert "90/365 天档在订阅有效期内可用「我的记录」" in manual + build
    assert "90/365 天档在订阅有效期内可使用“我的记录”" in announcement
    assert "365 天档和历史永久版另含" in manual + announcement
    assert "高级个性化" in manual + build + announcement
    assert "互动成长" in manual + build + overlay + announcement
    assert "推进中" not in manual + build + overlay + announcement
    assert "积分" not in manual + build + overlay + announcement
    assert all(hero in manual for hero in ("艾莎", "艾哈迈德", "加布里埃拉", "伊森", "拉文"))
    assert "原有五位主要英雄专项适配" in manual
    assert "玛丽亚、伊万已接入各自专属技能证据与显示" in manual
    assert "不代表所有英雄都已完成与上述五位同等级的专项校准或实战验证" in manual
    assert "玛丽亚、伊万另有专属技能证据与显示" in build
    assert "原有五位专项英雄" in build
    assert "不代表所有英雄均已完成同等级专项校准或实战验证" in build
    assert "七位主要英雄" not in manual + build
    assert "21 位英雄专项" not in manual + build
    assert "最近 300 个对局" in manual
    assert "https://api.bidking.example.invalid/public" in manual
    assert 'Join-Path $Stage "公共大数据平台.html"' in build
    assert "缺公共大数据平台入口资产" in build
    assert "https://api.bidking.example.invalid/public" in public_stats
    assert "无需 token" in public_stats and "免费和付费用户均可使用" in public_stats
    assert 'window.location.replace("https://api.bidking.example.invalid/public")' in public_stats
    assert 'has("preview")' in public_stats
    assert "http://" not in public_stats
    assert "点此输入/购买 token" in manual and "点此输入/购买 token" in build
    assert "购买与续费" in manual
    assert "支付宝付款码.png" in manual and "微信付款码.jpg" in manual
    assert "购买与续费\\扫码购买说明.txt" in manual and "人工核对后回复 token" in manual
    assert '$purchaseDir = Join-Path $Stage "购买与续费"' in build
    assert 'Join-Path $purchaseDir "支付宝付款码.png"' in build
    assert 'Join-Path $purchaseDir "微信付款码.jpg"' in build
    assert 'Join-Path $purchaseDir "扫码购买说明.txt"' in build
    assert "扫码付款不会自动生成 token" in build
    assert "投喂罐头.png" not in build and "投喂罐头.txt" not in build
    assert (ROOT / "hero_ref" / "assets" / "support_qr.png").stat().st_size > 10_000
    assert (ROOT / "hero_ref" / "assets" / "support_qr_wechat.jpg").stat().st_size > 10_000
    assert "免费额度 · …" in manual and "标题下方「激活: …」" not in manual
    assert (
        "付费剩余超过 90 天时整行隐藏,进入最后 90 天后显示倒计时和「更换 token」入口。"
        in manual
    )
    assert "付费剩余超过 30 天时默认隐藏" not in manual
    assert "所有档位均可正常使用计算器" in overlay
    assert "STANDARD_SUBSCRIPTION_PRICE_ROWS" in overlay
    assert "_price_row(row_index, label_text, values)" in overlay
    assert all(
        price not in overlay
        for price in (
            "¥12.88",
            "¥11.11",
            "¥28.88",
            "¥68.88",
            "¥19.99",
            "¥39.99",
            "¥89.99",
        )
    )
    assert "首次与再次购买同价；再次购买会保留并延长已有时间" in overlay
    assert "当前为一次性预付，不会自动扣款；更换档位请先联系" in overlay
    assert all(stale not in overlay for stale in ("绑定新购", "绑定续费", "漫游新购", "漫游续费"))
    assert 'contact_prefix = "购买/激活"' in overlay
    assert "row=contact_row, column=1, columnspan=5, sticky=\"ew\"" in overlay
    assert (
        "猫猫：免费10分钟 ｜ 30天12小时 ｜ "
        "31–89天24小时 ｜ 90天及以上订阅期内不限时"
    ) in overlay
    assert "90/365天可用：我的记录 ｜ 365天另含：完整私有大数据与高级个性化" in overlay
    assert "音效、动画与互动成长将逐步扩充" in overlay
    assert "token_entry_action_text(self.product_profile)" in overlay
    assert "点此输入/购买 token" not in overlay
    assert "首次与再次购买同价；再次购买会保留并延长已有时间" in manual + build + announcement
    assert "当前为一次性预付" in manual + build + announcement
    assert "不会自动扣款" in manual + build + announcement
    assert "免费计算器试用 3 小时" in announcement
    assert "免费猫猫 10 分钟" in announcement
    assert "30/90 天档不包含私有大数据权限" in web_ui
    assert "30/60 天" not in web_ui + activation_server
    public_materials = manual + build + overlay + (
        ROOT / "src" / "bidking_lab" / "secure" / "client_activate.py"
    ).read_text(encoding="utf-8-sig")
    assert "project discussion" in public_materials
    assert "1031573283" not in public_materials
    assert "公版在对局结算后默认自动上传" in manual
    assert "免费试用" in license_text and "365 天" in license_text
    assert "永久版联系作者" not in build and "永久 ¥" not in build


def test_public_wss_cold_start_and_lifecycle_copy_is_consistent() -> None:
    manual = (ROOT / "scripts" / "pkg_assets" / "使用说明.txt").read_text(
        encoding="utf-8-sig"
    )
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )
    launcher = (ROOT / "scripts" / "start_live_npcap_overlay.ps1").read_text(
        encoding="utf-8-sig"
    )
    monitor = (ROOT / "scripts" / "run_wss_live_monitor.py").read_text(
        encoding="utf-8-sig"
    )
    overlay = (ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py").read_text(
        encoding="utf-8-sig"
    )

    cold_start_phrase = "Steam 可以保持开启，但 BidKing.exe 必须完全退出"
    refresh_phrase = "刷新当前界面"
    close_phrase = "游戏退出后后台读取会自动结束"
    assert all(cold_start_phrase in document for document in (manual, build))
    assert "完全退出 BidKing 游戏" in launcher
    assert "完全退出游戏" in overlay
    assert all("等悬浮窗出现" in document for document in (manual, build, launcher, overlay))
    assert all(refresh_phrase in document for document in (manual, build, overlay))
    assert all(close_phrase in document for document in (manual, build, launcher))
    assert "游戏运行中关闭悬浮窗只会关闭界面" in manual
    assert "为了正确读取本局信息" in launcher
    assert "否则本局可能无法正常读取" in build
    assert "本机对局数据兼容" in manual
    assert "不会记录或上传登录账号、激活 token" in manual
    for private_implementation_term in (
        "SetGamePreset",
        "GameUseItem",
        "release.bid-king.cn",
        "0x22 / 0x26",
    ):
        assert private_implementation_term not in manual
    assert "SetGamePreset / GameUseItem" not in launcher
    assert 'Join-Path $Stage "使用说明.txt"' in build
    assert 'Join-Path $subDir "说明.txt"' in build
    assert "wss_cold_start_required" in monitor
    assert "Steam may stay open, but close " in monitor
    assert "BidKing.exe before starting or restarting Hero Ref" in monitor
    assert "整局不显示 / 卡在上一局结算时点这里" not in manual + overlay


def test_package_groups_user_helpers_and_diagnostic_launchers_resolve_root() -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )
    manual = (ROOT / "scripts" / "pkg_assets" / "使用说明.txt").read_text(
        encoding="utf-8-sig"
    )
    collect_bat_bytes = (ROOT / "scripts" / "collect_diagnostics.bat").read_bytes()
    transport_bat_bytes = (
        ROOT / "scripts" / "activation_transport_diagnostic.bat"
    ).read_bytes()
    collect_bat = collect_bat_bytes.decode("ascii")
    transport_bat = transport_bat_bytes.decode("ascii")
    transport_py_bytes = (
        ROOT / "scripts" / "activation_transport_diagnostic.py"
    ).read_bytes()
    contract = json.loads(
        (ROOT / "scripts" / "release_zip_contract.json").read_text(encoding="utf-8")
    )
    packaged_troubleshooting = (
        ROOT / "docs" / "TROUBLESHOOTING_packaged.zh-CN.md"
    ).read_text(encoding="utf-8-sig")
    group_troubleshooting = (
        ROOT / "docs" / "TROUBLESHOOTING_group_friends.zh-CN.md"
    ).read_text(encoding="utf-8-sig")

    assert '$diagnosticsDir = Join-Path $Stage "诊断工具"' in build
    assert 'Join-Path $diagnosticsDir "收集诊断.bat"' in build
    assert 'Join-Path $diagnosticsDir "激活网络诊断.bat"' in build
    assert 'Join-Path $diagnosticsDir "activation_transport_diagnostic.py"' in build
    assert 'Join-Path $Stage "收集诊断.bat"' not in build
    assert 'Join-Path $Stage "激活网络诊断.bat"' not in build
    assert 'Join-Path $Stage "activation_transport_diagnostic.py"' not in build

    for package_input in (
        '"scripts/collect_diagnostics.bat"',
        '"scripts/activation_transport_diagnostic.py"',
        '"scripts/activation_transport_diagnostic.bat"',
    ):
        assert package_input in build

    assert "%~dp0..\\scripts\\collect_diagnostics.ps1" in collect_bat
    assert "%TOOL_DIR%.." in transport_bat
    assert "%PACKAGE_ROOT%\\runtime\\python\\python.exe" in transport_bat
    assert 'pushd "%PACKAGE_ROOT%"' in transport_bat
    assert '"%PYTHON%" -B "%SCRIPT%"' in transport_bat
    assert not collect_bat_bytes.startswith(b"\xef\xbb\xbf")
    assert not transport_bat_bytes.startswith(b"\xef\xbb\xbf")
    assert "echo [START] BidKing diagnostics" in collect_bat
    assert "Windows Documents\\BidKingLive\\exports" in collect_bat
    assert "package root" not in collect_bat
    assert "endlocal & exit /b %RC%" in collect_bat

    diagnostic_members = {
        "诊断工具/收集诊断.bat": collect_bat_bytes,
        "诊断工具/激活网络诊断.bat": transport_bat_bytes,
        "诊断工具/activation_transport_diagnostic.py": transport_py_bytes,
    }
    assert diagnostic_members.keys() <= set(contract["required_members"]["common"])
    for member, payload in diagnostic_members.items():
        assert contract["fixed_member_sha256"][member] == hashlib.sha256(
            payload
        ).hexdigest()

    assert "诊断工具\\收集诊断.bat" in manual
    assert "诊断工具\\激活网络诊断.bat" in manual
    assert "network failure" not in manual.lower()
    assert "网络失败无需反复换 token" in manual
    assert "token 无效:只在此提示下重新核对并粘贴 token" in manual
    assert "程序目录或当前 Windows 身份的写入权限" in manual
    assert "购买与续费\\扫码购买说明.txt" in manual
    for guide in (packaged_troubleshooting, group_troubleshooting):
        assert "文档\\BidKingLive\\exports" in guide
        assert "仅私发" in guide
    assert "桌面生成" not in packaged_troubleshooting
    assert "生成在软件根目录" not in group_troubleshooting


def test_portable_package_only_copies_runtime_hero_assets() -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert 'Copy-Tree "hero_ref\\assets"' not in build
    assert '$runtimeHeroAssets = @("bidking_cat.ico")' in build
    assert '$runtimeHeroAssets += "support_qr.png"' in build
    assert 'Copy-Item -LiteralPath $assetSrc -Destination' in build
    assert '$petAssetsSource = Join-Path $Repo "hero_ref\\assets\\pet_companion"' in build
    assert '$petAssetsDst = Join-Path $heroAssetsDst "pet_companion"' in build
    assert "pet_companion_assets_manifest.json" in build
    assert "staged Pet assets" in build
    assert "extracted ZIP Pet assets" in build
    assert "extracted/runtime-component mitmproxy runtime" in build
    assert "CompatibilityRuntimeMode:" in build
    assert "CompatibilityRuntimeArchiveSHA256:" in build
    assert "禁止整目录" in build


def test_portable_build_version_must_match_source_version() -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "$SourceVersion = $vm.Matches[0].Groups[1].Value" in build
    assert "$Version -cne $SourceVersion" in build
    assert "与源码 HERO_REF_VERSION=$SourceVersion 不一致，拒绝构建" in build


def test_portable_build_version_remains_parseable_by_oldest_supported_client() -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )
    match = re.search(
        r"\$OldestSupportedClientVersionPattern = '([^']+)'",
        build,
    )
    assert match is not None
    powershell_pattern = match.group(1)
    pattern = re.compile(powershell_pattern.replace(r"\z", r"\Z"))

    for value in (
        "0.3.1",
        "0.3.1-r1",
        "0.3.1-hotfix1",
        "0.3.1-hotfix2",
        "0.3.1-HOTFIX2",
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
    ):
        assert pattern.fullmatch(value) is not None
    for value in (
        "0.3.1-hotfix1-r1",
        "v0.3.1",
        "0.3",
        "0.3.1-preview1",
        "0.3.1-hotfix",
        "０.３.１-hotfix２",
        "٠.٣.١-hotfix٢",
        "०.३.१-hotfix२",
        " 0.3.1-hotfix2",
        "0.3.1-hotfix2\t",
        "0.3.1-hotfix2\n",
        "0.3.1-hotfİx2",
        "0.3.1-hotfıx2",
    ):
        assert pattern.fullmatch(value) is None

    digits = tuple(
        chr(codepoint)
        for codepoint in range(0x110000)
        if unicodedata.category(chr(codepoint)) == "Nd"
        and not 0x30 <= codepoint <= 0x39
    )
    assert len(digits) >= 640
    for digit in digits:
        assert pattern.fullmatch(f"{digit}.3.1-hotfix2") is None
        assert pattern.fullmatch(f"0.3.1-hotfix{digit}") is None

    assert powershell_pattern.startswith(r"\A")
    assert powershell_pattern.endswith(r"\z")
    assert "[System.Text.RegularExpressions.Regex]::IsMatch" in build
    assert "[System.Text.RegularExpressions.RegexOptions]::CultureInvariant" in build
    assert "$Version -notmatch $OldestSupportedClientVersionPattern" not in build
    assert "无法由最老受支持客户端解析，拒绝构建" in build


def test_portable_build_does_not_bundle_npcap_installer() -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )
    diagnostics = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "scripts/pkg_assets/npcap-installer.exe" not in build
    assert "vendor/npcap-installer.exe" not in build
    assert "当前发布包不内置 Npcap 安装器" in diagnostics
    assert "运行包内 vendor\\npcap-installer.exe" not in diagnostics


def test_public_launcher_defaults_to_portable_diagnostics() -> None:
    """公版不得持续落盘包含精确三档报价的 engineering 摘要。"""
    launcher = (ROOT / "scripts" / "start_live_npcap_overlay.ps1").read_text(
        encoding="utf-8-sig"
    )
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert '[string]$DiagnosticProfile = "portable"' in launcher
    assert '$PublicDiagnosticProfile = "portable"' in build
    assert '$publicLauncherScript = Join-Path $scriptsDst "start_live_npcap_overlay.ps1"' in build
    assert "公版启动脚本默认诊断档位不是 $PublicDiagnosticProfile，拒绝出包" in build
    assert '"DiagnosticProfile: $PublicDiagnosticProfile"' in build
    assert '[string]$DiagnosticProfile = "__PUBLIC_PROFILE__"' in build
    assert "'@.Replace(\"__PUBLIC_PROFILE__\", $PublicDiagnosticProfile)" in build
    assert '"-DiagnosticProfile", $DiagnosticProfile' in build


def test_twelve_hour_trial_default_is_consistent_across_server_entrypoints() -> None:
    core = (ROOT / "scripts" / "activation_core.py").read_text(encoding="utf-8-sig")
    service = (ROOT / "scripts" / "activation_server.py").read_text(encoding="utf-8-sig")
    collector = (ROOT / "scripts" / "sample_collector_server.py").read_text(encoding="utf-8-sig")

    assert "DEFAULT_TRIAL_HOURS = 12.0" in core
    assert "trial_hours: float = activation_core.DEFAULT_TRIAL_HOURS" in service
    assert "default=activation_core.DEFAULT_TRIAL_HOURS" in collector
    assert "12 小时自然时间" in (ROOT / "LICENSE").read_text(encoding="utf-8-sig")


def test_encrypted_public_package_requires_upload_config_and_diagnostics_recognize_it() -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(encoding="utf-8-sig")
    diagnostics = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(encoding="utf-8-sig")

    assert 'if ($Encrypt -and -not (Test-Path $uploadLocal))' in build
    assert 'if ($Encrypt -and -not $EncUploadConfig)' in build
    assert 'if ($Encrypt -and -not $InspectOnly -and -not $MasterKeyFile)' in build
    assert 'if ($Encrypt -and -not $InspectOnly -and $MasterKeyFile)' in build
    assert "verify_release_master_key.py" in build
    assert '"ReleaseKeyCacheVerified: $ReleaseKeyCacheVerified"' in build
    assert '"UploadConfigRoundTripVerified: $UploadConfigRoundTripVerified"' in build
    assert build.count(
        "$ValidatedReleaseDataKey = (Get-Content -LiteralPath $MasterKeyFile -Raw)"
    ) == 1
    assert build.count("$env:BIDKING_DATA_KEY = $ValidatedReleaseDataKey") == 1
    assert build.index("if ($PreflightOnly)") < build.index(
        "$env:BIDKING_DATA_KEY = $ValidatedReleaseDataKey"
    )
    assert 'data\\processed\\upload_config.enc' in diagnostics
    assert "ReleaseKeyCacheVerified:" in diagnostics
    assert "UploadConfigRoundTripVerified:" in diagnostics
    assert "发布版默认如此, 正常" not in diagnostics


def test_generated_upload_config_cannot_be_overwritten_by_processed_data_copy() -> None:
    """最终成品门必须在 data/processed copy 后验证本次生成物，不能只信早期 round-trip。"""
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(encoding="utf-8-sig")
    manifest = json.loads(
        (ROOT / "scripts" / "package_data_manifest.json").read_text(encoding="utf-8")
    )
    copy_at = build.index(
        "Copy-Item -LiteralPath $src -Destination $dst -Force"
    )
    final_gate_at = build.index("$FinalUploadConfigSHA256 = (")
    generated_gate_at = build.index("$GeneratedUploadConfigSHA256 = (")
    manifest_at = build.index('"UploadConfigRoundTripVerified: $UploadConfigRoundTripVerified"')

    assert "data/processed/upload_config.enc" not in manifest["files"]
    assert 'Copy-Tree "data\\processed"' not in build
    assert '--list-json' in build[:copy_at]
    assert '$rel -ne "data/processed/upload_config.enc"' in build[copy_at:final_gate_at]
    assert (
        "& $TrustedFileHashCommand -LiteralPath $encOut"
        in build[generated_gate_at:copy_at]
    )
    assert (
        "& $TrustedFileHashCommand -LiteralPath $encOut"
        in build[final_gate_at:manifest_at]
    )
    assert generated_gate_at < copy_at
    assert copy_at < final_gate_at < manifest_at
    assert build.index('$UploadConfigRoundTripVerified = $true') > final_gate_at


def test_encrypted_package_removes_cython_build_intermediates() -> None:
    """群发包只保留原位 .pyd，不携带 src/build 下的 obj/lib/重复副本。"""
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(encoding="utf-8-sig")

    assert '$pkgBuildDir = Join-Path $pkgSrc "build"' in build
    assert 'Remove-Item $pkgBuildDir -Recurse -Force' in build
    assert 'if (Test-Path $pkgBuildDir) { throw "Cython 中间目录清理失败:' in build


def test_release_package_bundles_a_strict_ca_source() -> None:
    """HTTPS 不得依赖玩家机上可能过期/污染的 OpenSSL 默认 CA 路径。"""
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(encoding="utf-8-sig")

    assert '"certifi"' in build
    assert 'Join-Path $dstSite "certifi\\cacert.pem"' in build
    assert '"BundledCABundle: $BundledCABundle"' in build
    assert "verified_ssl_context" in build
    assert "ssl.CERT_REQUIRED" in build and "ctx.check_hostname" in build


def test_encrypted_candidate_is_git_traceable_and_rejects_dirty_package_inputs() -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(encoding="utf-8-sig")
    diagnostics = (ROOT / "scripts" / "collect_diagnostics.ps1").read_text(encoding="utf-8-sig")

    assert '"SourceCommit: $SourceCommit"' in build
    assert '"SourceBranch: $SourceBranch"' in build
    assert '"PackageInputsDirty: $PackageInputsDirty"' in build
    assert '"BuildScriptSHA256: $BuildScriptSHA256"' in build
    assert "if ($Encrypt -and $PackageInputsDirty -and -not $InspectOnly)" in build
    assert 'Write-BuildPreflightReceipt "inspect_only" $false "not_run"' in build
    assert "不得清理或覆盖共享工作树" in build
    assert "SourceCommit:" in diagnostics
    assert "PackageInputsDirty:" in diagnostics


def test_batch_mint_cannot_issue_new_permanent_or_overlong_public_tokens() -> None:
    assert batch_mint._parse_spec("365d:2")[2:] == (365 * 86400, 2)
    with pytest.raises(ValueError, match="不再签发永久"):
        batch_mint._parse_spec("perm:1")
    with pytest.raises(ValueError, match="最长 365 天"):
        batch_mint._parse_spec("366d:1")


def test_batch_mint_roaming_cap_is_365_days() -> None:
    batch_mint._validate_roaming_duration([batch_mint._parse_spec("365d:1")])
    with pytest.raises(ValueError, match="最长 365 天"):
        batch_mint._validate_roaming_duration([batch_mint._parse_spec("366d:1")])


def test_single_mint_requires_limited_duration_and_caps_standard_tier() -> None:
    assert mint_token._duration_seconds(365, None, roaming=False)[0] == 365 * 86400
    with pytest.raises(ValueError, match="不再签发永久"):
        mint_token._duration_seconds(None, None, roaming=False)
    with pytest.raises(ValueError, match="最长 365 天"):
        mint_token._duration_seconds(366, None, roaming=False)


def test_single_mint_caps_roaming_tier_at_365_days() -> None:
    assert mint_token._duration_seconds(365, None, roaming=True)[0] == 365 * 86400
    with pytest.raises(ValueError, match="最长 365 天"):
        mint_token._duration_seconds(366, None, roaming=True)


def test_single_mint_cli_rejects_implicit_permanent_and_issues_365d(tmp_path, capsys) -> None:
    assert mint_token.main(["mint_token.py", "--uid", "commercial-contract"]) == 1
    assert "不再签发永久" in capsys.readouterr().err

    secret = b"synthetic-commercial-contract-key"
    key_file = tmp_path / "activation_key.txt"
    key_file.write_bytes(secret)
    assert mint_token.main([
        "mint_token.py", "--uid", "commercial-contract", "--days", "365",
        "--key-file", str(key_file),
    ]) == 0
    token = capsys.readouterr().out.strip()
    payload = mint_token.activation_core.verify_token(token, secret)
    assert payload is not None
    assert payload["exp"] - payload["iat"] == 365 * 86400
