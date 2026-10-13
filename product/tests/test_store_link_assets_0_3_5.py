"""SHOP-A1 source checks: execute only selected, current builder AST statements.

No complete build, browser, network, native gate or release-artifact claim.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
URL = "https://www.kufaka.com/shop/GGTO42L9"
QR_SHA = "b51766cdf23aa992590d1fc1a1474c4b80b358d0cf50929c942499d3c14b56a3"
SHOP_MEMBERS = ("hero_ref/assets/shop_qr.png", "购买与续费/打开店铺.html", "购买与续费/店铺二维码.png")
INPUTS = (
    "hero_ref/assets/bidking_cat.ico", "hero_ref/assets/support_qr.png",
    "hero_ref/assets/support_qr_wechat.jpg", "hero_ref/assets/shop_qr.png",
    "scripts/pkg_assets/打开店铺.html", "scripts/pkg_assets/使用说明.txt",
    "hero_ref/tools/product_profile.py",
)

# Parse the actual producer; select sibling statements by AST node and target.
# No replica of copying/profile/manual logic is present in this harness.
AST_RUNNER = r'''
$ErrorActionPreference = 'Stop'
$Repo = $env:SHOP_TEST_REPO
$Stage = $env:SHOP_TEST_STAGE
$ProductProfile = $env:SHOP_TEST_PROFILE
$IsNetworkDiagnosticCohort = $ProductProfile -eq 'network-diagnostic'
$BuildPython = $env:SHOP_TEST_PYTHON
$manualAsset = Join-Path $Repo 'scripts/pkg_assets/使用说明.txt'
$manualDst = Join-Path $Stage '使用说明.txt'
$tokens = $null; $errors = $null
$tree = [System.Management.Automation.Language.Parser]::ParseFile($env:SHOP_TEST_BUILDER, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw "Builder parse errors: $errors" }
$start = @($tree.FindAll({ param($node) $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$heroAssetsDst' }, $true))
if ($start.Count -ne 1) { throw 'Non-unique runtime start AST' }
$stmts = @($start[0].Parent.Statements)
$end = @($stmts | Where-Object { $_ -is [System.Management.Automation.Language.ForEachStatementAst] -and $_.Variable.Extent.Text -eq '$asset' -and $_.Condition.Extent.Text -eq '$runtimeHeroAssets' })
$bom = @($stmts | Where-Object { $_ -is [System.Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -eq '$utf8Bom' })
$purchase = @($stmts | Where-Object { $_ -is [System.Management.Automation.Language.IfStatementAst] -and $_.Extent.Text.Contains('$purchaseDir =') })
if ($start.Count -ne 1 -or $end.Count -ne 1 -or $bom.Count -ne 1 -or $purchase.Count -ne 1) { throw "Non-unique selected AST boundaries: start=$($start.Count) end=$($end.Count) bom=$($bom.Count) purchase=$($purchase.Count)" }
$runtime = @($stmts | Where-Object { $_.Extent.StartOffset -ge $start[0].Extent.StartOffset -and $_.Extent.EndOffset -le $end[0].Extent.EndOffset })
if ($runtime.Count -ne 5) { throw 'Unexpected runtime asset AST shape' }
$selected = @($runtime) + @($bom) + @($purchase)
$receipt = @{ builder_sha256 = (Get-FileHash -LiteralPath $env:SHOP_TEST_BUILDER -Algorithm SHA256).Hash.ToLower(); profile = $ProductProfile; statements = @($selected | ForEach-Object { @{ type = $_.GetType().Name; start_line = $_.Extent.StartLineNumber; end_line = $_.Extent.EndLineNumber; start_offset = $_.Extent.StartOffset; end_offset = $_.Extent.EndOffset } }) }
Write-Output ('SHOP_AST=' + ($receipt | ConvertTo-Json -Depth 5 -Compress))
foreach ($statement in $selected) { . ([scriptblock]::Create($statement.Extent.Text)) }
'''


def produce(tmp_path: Path, profile: str = "standard", missing: str = ""):
    repo, stage = tmp_path / "repo", tmp_path / "stage"
    stage.mkdir(parents=True)
    for name in INPUTS:
        if name == missing:
            continue
        dest = repo / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, dest)
    env = dict(os.environ, SHOP_TEST_REPO=str(repo), SHOP_TEST_STAGE=str(stage),
               SHOP_TEST_PROFILE=profile, SHOP_TEST_PYTHON=sys.executable,
               SHOP_TEST_BUILDER=str(ROOT / "scripts/build_live_portable.ps1"))
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    assert pwsh, "PowerShell is a required test input"
    result = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", AST_RUNNER],
                            env=env, capture_output=True, encoding="utf-8", timeout=15)
    print(result.stdout)
    print(result.stderr)
    assert "SHOP_AST=" in result.stdout, result.stderr
    return stage, result


class Page(HTMLParser):
    def __init__(self, html: str):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def check_html(html: str):
    tags = Page(html).tags
    assert [a["content"] for t, a in tags if t == "meta" and a.get("http-equiv") == "refresh"] == [f"0; url={URL}"]
    assert [a.get("href") for t, a in tags if t == "a"] == [URL]
    assert [a.get("src") for t, a in tags if t == "img"] == ["店铺二维码.png"]
    assert "<title>BidKing 店铺</title>" in html
    assert re.search(r"\.qr\s*\{[^}]*padding:\s*24px;[^}]*background:\s*#fff", html)
    assert not any(t in {"script", "iframe", "link"} for t, _ in tags)
    assert "url(" not in html.casefold()
    assert "不是付款码" in html


def test_original_asset_and_support_codes_are_byte_exact():
    expected = {
        "shop_qr.png": QR_SHA,
        "support_qr.png": "f29f4d1e311b0f0723a73c66e7ed49eb1a2c84953963e6aa1159d4e9fe33cabd",
        "support_qr_wechat.jpg": "bf866a996cd110e75cb1d490bebf270db05f37f4669892913edda2f2d10b1a4c",
    }
    for name, digest in expected.items():
        assert hashlib.sha256((ROOT / "hero_ref/assets" / name).read_bytes()).hexdigest() == digest


def test_real_standard_producer_and_html(tmp_path):
    stage, result = produce(tmp_path)
    assert result.returncode == 0
    for name in SHOP_MEMBERS:
        assert (stage / name).is_file()
    assert (stage / SHOP_MEMBERS[0]).read_bytes() == (stage / SHOP_MEMBERS[2]).read_bytes() == (ROOT / INPUTS[3]).read_bytes()
    check_html((stage / SHOP_MEMBERS[1]).read_text(encoding="utf-8"))
    text = (stage / "购买与续费/扫码购买说明.txt").read_text(encoding="utf-8-sig")
    assert text.index("一、店铺入口") < text.index("二、人工扫码购买") < text.index("续费、换档或衔接剩余时间，请先联系作者核对")
    for phrase in (URL, "最多换绑 8 次", "首次绑定不计次", "同卡续签/重签不重置次数", "不追溯补计历史换绑", "30 天 ¥15.88", "90 天 ¥35.88", "365 天 ¥88.88", "30 天 ¥24.88", "90 天 ¥48.88", "365 天 ¥118.88", "GitHub Issues", "project discussion", "90/365 天档在订阅有效期内可用「我的记录」"):
        assert phrase in text
    assert (stage / "购买与续费/支付宝付款码.png").read_bytes() == (ROOT / INPUTS[1]).read_bytes()
    assert (stage / "购买与续费/微信付款码.jpg").read_bytes() == (ROOT / INPUTS[2]).read_bytes()


@pytest.mark.parametrize("missing", [INPUTS[3], INPUTS[4], INPUTS[1], INPUTS[2]])
def test_real_standard_missing_required_asset_fails(tmp_path, missing):
    _, result = produce(tmp_path, missing=missing)
    assert result.returncode != 0
    assert "缺" in result.stderr


def test_real_diagnostic_producer_has_no_store_or_purchase(tmp_path):
    stage, result = produce(tmp_path, "network-diagnostic", missing=INPUTS[3])
    assert result.returncode == 0
    assert not (stage / "购买与续费").exists()
    assert not (stage / SHOP_MEMBERS[0]).exists()
    assert not (stage / "hero_ref/assets/support_qr.png").exists()
    assert (stage / "网络诊断内测说明/内测边界.txt").is_file()


@pytest.mark.parametrize("old,new", [(URL, "https://invalid.example/wrong"), ("店铺二维码.png", "不存在.png"), ("padding: 24px", "padding: 0px")])
def test_html_checker_rejects_known_bad_target_image_or_quiet_zone(old, new):
    html = (ROOT / INPUTS[4]).read_text(encoding="utf-8")
    check_html(html)
    with pytest.raises(AssertionError):
        check_html(html.replace(old, new))


@pytest.fixture(scope="module")
def auditor():
    sys.path.insert(0, str(ROOT / "scripts"))
    import audit_release_zip
    return audit_release_zip


def zip_of(values):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in values.items():
            archive.writestr(name, data)
    stream.seek(0)
    return zipfile.ZipFile(stream)


def test_existing_contract_member_rule_accepts_present_and_rejects_missing(auditor):
    contract_path = ROOT / "scripts/release_zip_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    assert set(SHOP_MEMBERS) <= set(contract["required_members"]["standard"])
    assert not set(SHOP_MEMBERS) & set(contract["required_members"]["network-diagnostic"])
    assert SHOP_MEMBERS[1] in contract["public_text_members"]
    values = {"BUILD_MANIFEST.txt": "ProductProfile: standard\nEncrypted: false\n", **{n: b"fixture" for n in SHOP_MEMBERS}}
    def relevant_issues(entries):
        with zip_of(entries) as archive:
            all_issues = auditor._release_contract_issues(archive, contract_path, (), None, None, None, None)
        assert "release_contract_invalid" not in all_issues, all_issues
        # Explicit projection: other artifact gates are NOT satisfied by this fixture.
        return [issue for issue in all_issues if any(issue.endswith(": " + n) for n in SHOP_MEMBERS) and issue.startswith("release_member_")]
    assert relevant_issues(values) == []
    for missing in SHOP_MEMBERS:
        issues = relevant_issues({n: d for n, d in values.items() if n != missing})
        assert issues == [f"release_member_missing: {missing}"]


@pytest.mark.parametrize("member", SHOP_MEMBERS + ("HERO_REF/ASSETS/SHOP_QR.PNG",))
def test_diagnostic_checker_rejects_named_store_assets(auditor, member):
    with zip_of({"说明.txt": "已有授权诊断"}) as archive:
        assert auditor._network_diagnostic_text_issues(archive) == []
    with zip_of({member: b"deliberately not text"}) as archive:
        assert f"network_diagnostic_store_asset_forbidden: {member}" in auditor._network_diagnostic_text_issues(archive)


def test_diagnostic_existing_text_negative_control_is_preserved(auditor):
    with zip_of({"说明.txt": "30 天 ¥12.88"}) as archive:
        assert any(i.startswith("network_diagnostic_forbidden_text:") for i in auditor._network_diagnostic_text_issues(archive))
