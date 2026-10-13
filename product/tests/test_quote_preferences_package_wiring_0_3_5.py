"""QUOTE-PKG1 declaration and exact candidate-loader source checks (no build)."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import ModuleType
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
MODULE = "bidking_lab/live/quote_preferences.py"
NATIVE_MEMBER = "src/bidking_lab/live/quote_preferences.cp313-win_amd64.pyd"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import audit_release_zip as auditor
import native_release_hardening as native
import activation_deploy_gate as gate


def json_source(name):
    return json.loads((SCRIPTS / name).read_text(encoding="utf-8"))


def live_tree(manifest):
    return next(t for t in manifest["critical_native"]["package_trees"] if t["path"] == "bidking_lab/live")


def native_available(contract, manifest):
    return set(contract["inventory"]["exact_members"]) | native._protected_members(manifest) | {"src/bidking_kernel.cp313-win_amd64.pyd"}


def test_real_manifest_count_and_unique_helper():
    manifest = json_source("client_protection_manifest.json")
    contract = native.load_contract(SCRIPTS / "release_native_binary_contract.json")
    assert live_tree(manifest)["protected_modules"].count(MODULE) == 1
    assert "src/" + MODULE not in manifest["plaintext_allowlist"]
    assert "src/" + MODULE not in manifest["packaged_plaintext_python_allowlist"]
    protected = native._protected_members(manifest)
    assert len(protected) == 110 and NATIVE_MEMBER in protected
    assert len(native.expected_first_party_members(native_available(contract, manifest), contract, manifest)) == 117
    assert contract["additional_native_binary_count"] == 7


def test_current_native_inventory_rejects_missing_helper():
    manifest = json_source("client_protection_manifest.json")
    contract = native.load_contract(SCRIPTS / "release_native_binary_contract.json")
    available = native_available(contract, manifest)
    live_tree(manifest)["protected_modules"].remove(MODULE)
    with pytest.raises(native.NativeContractError, match="native_binary_inventory_mismatch"):
        native.expected_first_party_members(available, contract, manifest)


def test_existing_manifest_checker_rejects_duplicate_helper(tmp_path):
    manifest = json_source("client_protection_manifest.json")
    def check(value):
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w"):
            pass
        with zipfile.ZipFile(stream) as archive:
            return auditor._client_protection_issues(archive, path, {})
    assert "client_protection_manifest_invalid" not in check(manifest)
    live_tree(manifest)["protected_modules"].append(MODULE)
    assert check(manifest) == ["client_protection_manifest_invalid"]


@pytest.mark.parametrize("change", ["old_count", "wrong_count", "extra_binary", "compiler", "abi"])
def test_existing_native_contract_rejects_invalid_declarations(tmp_path, change):
    value = json_source("release_native_binary_contract.json")
    if change in {"old_count", "wrong_count"}:
        value["protected_cython_module_count"] = 109 if change == "old_count" else 111
    elif change == "extra_binary":
        value["additional_native_binary_count"] = 8
    elif change == "compiler":
        value["msvc"]["compile_flags"].append("/Zi")
    else:
        value["pe"]["machine"] = "arm64"
    path = tmp_path / "native.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(native.NativeContractError):
        native.load_contract(path)


def assignment(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    matches = [n.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    assert len(matches) == 1
    return ast.literal_eval(matches[0])


def shell_declaration(source, name):
    found = re.findall(rf"(?m)^{name}=\(([^)]*)\)", source)
    assert len(found) == 1
    return found[0].split()


def test_server_declarations_and_isolation_list_are_exact():
    text = (SCRIPTS / "server_update.sh").read_text(encoding="utf-8")
    shell_group = shell_declaration(text, "ACTIVATION_GROUP")
    shell_files = shell_declaration(text, "FILES")
    state_group = assignment(SCRIPTS / "activation_state_aware_deploy.py", "ACTIVATION_GROUP")
    assert set(shell_group) == set(state_group) == set(gate.RUNTIME_FILES) | {"activation_deploy_gate.py"}
    for group in (shell_group, shell_files, state_group, gate.RUNTIME_FILES):
        assert group.count("quote_preferences.py") == 1
    func = next(n for n in ast.parse((SCRIPTS / "activation_deploy_gate.py").read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef) and n.name == "validate_candidate_dir")
    modules = next(ast.literal_eval(n.value) for n in func.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "module_names" for t in n.targets))
    assert modules.count("quote_preferences") == 1


def test_existing_fixture_source_mappings_select_the_live_helper_only():
    contract_test = ROOT / "tests/test_activation_deploy_contract.py"
    source = contract_test.read_text(encoding="utf-8")
    assert assignment(contract_test, "RUNTIME").count("quote_preferences.py") == 1
    tree = ast.parse(source)
    helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_runtime_source")
    namespace = {"ROOT": ROOT, "SCRIPTS": SCRIPTS, "Path": Path}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(contract_test), "exec"), namespace)
    clone_source = (ROOT / "tests/test_activation_state_aware_deploy_0_3_1.py").read_text(encoding="utf-8")
    clone = next(n for n in ast.parse(clone_source).body if isinstance(n, ast.FunctionDef) and n.name == "_clone")
    mappings = [n.value for n in ast.walk(clone) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "source" for t in n.targets)]
    assert len(mappings) == 1
    expression = compile(ast.Expression(body=mappings[0]), "_clone source expression only", "eval")
    for name, expected in (("quote_preferences.py", ROOT / "src/bidking_lab/live/quote_preferences.py"), ("activation_http_e2ee.py", ROOT / "src/bidking_lab/secure/activation_http_e2ee.py"), ("activation_core.py", SCRIPTS / "activation_core.py")):
        assert namespace["_runtime_source"](name) == expected
        assert eval(expression, {"ROOT": ROOT, "SCRIPTS": SCRIPTS, "name": name}) == expected


def test_server_update_syntax_and_inert_exact_group_selection():
    bash = shutil.which("bash")
    assert bash, "bash parser required; do not run server_update body"
    syntax = subprocess.run([bash, "--noprofile", "--norc", "-n", "scripts/server_update.sh"], cwd=ROOT, capture_output=True, timeout=5)
    assert syntax.returncode == 0, syntax.stderr
    source = (SCRIPTS / "server_update.sh").read_text(encoding="utf-8")
    decl = re.search(r"(?ms)^ACTIVATION_GROUP=\([^)]*\)", source).group(0)
    start = source.index("activation_touched=0\n")
    end = source.index('  state_directories="$(systemctl', start)
    # Exact source selection prefix ends inside the outer activation-touched if.
    # Only close that block; no HOME scan/file check/service/update body is included.
    selection = source[start:end] + "fi\n"
    assert not any(word in selection for word in ("systemctl", "curl", "$HOME", "sudo", "cp ", "rm "))
    script = decl + '\nfound=("$@")\n' + selection
    group = shell_declaration(source, "ACTIVATION_GROUP")
    for args, expected in ((group, 0), ([n for n in group if n != "quote_preferences.py"], 1), (["quote_preferences.py"], 1), (["web_ui.py"], 0)):
        result = subprocess.run([bash, "--noprofile", "--norc", "-c", script, "quote-pkg-selection", *args], capture_output=True, timeout=5)
        assert result.returncode == expected, result.stderr


@pytest.mark.parametrize("count,expected", [(110, 0), (109, 1), (111, 1)])
def test_real_builder_native_count_guard_only(tmp_path, count, expected):
    contract = json_source("release_native_binary_contract.json")
    contract["protected_cython_module_count"] = count
    fixture = tmp_path / "contract.json"
    fixture.write_text(json.dumps(contract), encoding="utf-8")
    script = r'''
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$tree = [System.Management.Automation.Language.Parser]::ParseFile($env:QPKG_BUILDER, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Builder parse error' }
$guards = @($tree.FindAll({ param($n) $n -is [System.Management.Automation.Language.IfStatementAst] -and $n.Extent.Text.Contains('[int64]$NativeBinaryContract.protected_cython_module_count -ne') }, $true))
if ($guards.Count -ne 1) { throw 'Ambiguous native guard' }
$NativeBinaryContract = Get-Content -LiteralPath $env:QPKG_CONTRACT -Raw | ConvertFrom-Json
$NativeHardeningTool = $env:QPKG_HARDENING
Write-Output ('QPKG_AST=' + ($guards[0].Extent.StartLineNumber) + ':' + ($guards[0].Extent.EndLineNumber))
. ([scriptblock]::Create($guards[0].Extent.Text))
'''
    env = dict(os.environ, QPKG_BUILDER=str(SCRIPTS / "build_live_portable.ps1"), QPKG_CONTRACT=str(fixture), QPKG_HARDENING=str(SCRIPTS / "native_release_hardening.py"))
    result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", script], env=env, capture_output=True, timeout=5)
    print(result.stdout.decode("utf-8"))
    assert (result.returncode == 0) == (expected == 0), result.stderr


def synthetic_candidate(tmp_path, helper_source):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    contents = {
        "activation_server.py": "def require_activation_core_compatible(): pass\n",
        "pet_usage_db_admin.py": "def main(): pass\n",
        "upload_identity_db_admin.py": "def main(): pass\n",
        "activation_device_admin.py": "def main(): pass\n",
        "quote_preferences.py": helper_source,
        "sample_collector_server.py": "import quote_preferences\n",
    }
    for name in gate.RUNTIME_FILES:
        if name == "quote_preferences.py" and helper_source is None:
            continue
        (candidate / name).write_text(contents.get(name, "# inert synthetic runtime\n"), encoding="utf-8")
    return candidate


def test_flat_loader_uses_candidate_bytes_and_restores_previous_modules(tmp_path, monkeypatch):
    candidate = synthetic_candidate(tmp_path, "WITNESS = 'candidate-not-ambient'\n")
    ambient = ModuleType("quote_preferences")
    ambient.WITNESS = "ambient-must-not-be-used"
    monkeypatch.setitem(sys.modules, "quote_preferences", ambient)
    class StopAfterExactLoading(Exception):
        pass
    def observe(collector, server, state, **kwargs):
        helper = collector.quote_preferences
        assert helper is sys.modules["quote_preferences"] and helper is not ambient
        assert helper.WITNESS == "candidate-not-ambient"
        assert Path(helper.__file__).resolve() == (candidate / "quote_preferences.py").resolve()
        assert hashlib.sha256(Path(helper.__file__).read_bytes()).digest() == hashlib.sha256((candidate / "quote_preferences.py").read_bytes()).digest()
        raise StopAfterExactLoading
    monkeypatch.setattr(gate, "_construct_via_candidate_collector", observe)
    with pytest.raises(StopAfterExactLoading):
        gate.validate_candidate_dir(candidate)
    assert sys.modules["quote_preferences"] is ambient


@pytest.mark.parametrize("helper_source,error", [(None, gate.CandidateError), ("raise RuntimeError('broken exact candidate')\n", RuntimeError)])
def test_flat_loader_refuses_missing_or_broken_helper_without_ambient_fallback(tmp_path, monkeypatch, helper_source, error):
    candidate = synthetic_candidate(tmp_path, helper_source)
    ambient = ModuleType("quote_preferences")
    monkeypatch.setitem(sys.modules, "quote_preferences", ambient)
    with pytest.raises(error):
        gate.validate_candidate_dir(candidate)
    assert sys.modules["quote_preferences"] is ambient


COPY_PRICES = {
    "绑定设备": ("15.88", "35.88", "88.88"),
    "可换绑": ("24.88", "48.88", "118.88"),
}
STORE_URL = "https://www.kufaka.com/shop/GGTO42L9"


def check_store_copy(text, repeats):
    rows = re.findall(r"(绑定设备|可换绑)(?:档)?[：:]\s*30 天 ¥(\d+\.\d+)\s*｜\s*90 天 ¥(\d+\.\d+)\s*｜\s*365 天 ¥(\d+\.\d+)", text)
    assert len(rows) == 2 * repeats
    for label, values in COPY_PRICES.items():
        assert rows.count((label, *values)) == repeats
    assert not re.search(r"¥(?:12\.88|28\.88|68\.88|19\.99|39\.99|89\.99)(?!\d)", text)
    assert "再次购买会保留并延长已有时间" not in text
    assert "一次性购买，不自动扣款" in text
    assert "续费、换档或衔接剩余时间，请先联系作者核对" in text
    assert text.count("店铺新卡不承诺自动叠加已有时间") == repeats
    assert "店铺新卡自动叠加已有时间" not in text
    assert "既有人工续费承诺按原约定处理" in text
    for term in (STORE_URL, "支付宝付款码.png", "微信付款码.jpg", "synthetic-user-001", "synthetic-user-002", "不是每天 12 小时", "90 天不含完整私有大数据", "会员按自然时间计时"):
        assert term in text


def test_store_copy_manual_exact_prices_and_contact():
    check_store_copy((ROOT / "scripts/pkg_assets/使用说明.txt").read_text(encoding="utf-8-sig"), repeats=2)


@pytest.mark.parametrize("before,after", [("30 天 ¥15.88 ｜ 90 天 ¥35.88", "30 天 ¥35.88 ｜ 90 天 ¥15.88"), ("可换绑档:30 天 ¥24.88", "可换绑档:30 天 ¥15.88"), ("30 天 ¥15.88", "30 天 ¥12.88"), ("店铺新卡不承诺自动叠加已有时间", "店铺新卡自动叠加已有时间")])
def test_store_copy_checker_rejects_swapped_old_price_or_auto_merge(before, after):
    text = (ROOT / "scripts/pkg_assets/使用说明.txt").read_text(encoding="utf-8-sig")
    check_store_copy(text, repeats=2)
    assert before in text
    with pytest.raises(AssertionError):
        check_store_copy(text.replace(before, after, 1), repeats=2)


def store_producer_module():
    path = ROOT / "tests/test_store_link_assets_0_3_5.py"
    spec = importlib.util.spec_from_file_location("store_copy_existing_ast_harness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_store_copy_real_standard_producer(tmp_path):
    producer = store_producer_module()
    stage, result = producer.produce(tmp_path)
    assert result.returncode == 0
    check_store_copy((stage / "购买与续费/扫码购买说明.txt").read_text(encoding="utf-8-sig"), repeats=1)


def check_diagnostic_copy(text):
    assert STORE_URL not in text
    assert "打开店铺.html" not in text
    assert "店铺二维码.png" not in text
    assert "¥" not in text
    assert "【网络诊断内测支持】" in text


def test_store_copy_real_diagnostic_producer_and_injection_control(tmp_path):
    producer = store_producer_module()
    stage, result = producer.produce(tmp_path, "network-diagnostic")
    assert result.returncode == 0
    assert not (stage / "购买与续费").exists()
    text = (stage / "使用说明.txt").read_text(encoding="utf-8-sig")
    check_diagnostic_copy(text)
    with pytest.raises(AssertionError):
        check_diagnostic_copy(text + "\n" + STORE_URL)


def test_store_copy_profile_price_rows_match_both_package_consumers():
    path = Path(os.environ.get("QPKG_PROFILE_SOURCE", ROOT / "hero_ref/tools/product_profile.py"))
    spec = importlib.util.spec_from_file_location("store_copy_profile_snapshot", path)
    profile = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(profile)
    expected = tuple((label + "：", tuple(f"{days}天 ¥{price}" for days, price in zip((30, 90, 365), values))) for label, values in COPY_PRICES.items())
    assert profile.STANDARD_SUBSCRIPTION_PRICE_ROWS == expected
    check_store_copy((ROOT / "scripts/pkg_assets/使用说明.txt").read_text(encoding="utf-8-sig"), repeats=2)
    builder = (SCRIPTS / "build_live_portable.ps1").read_text(encoding="utf-8-sig")
    purchase = re.search(r"\$purchaseText = @'\n(.*?)\n'@", builder, re.S)
    assert purchase
    check_store_copy(purchase.group(1), repeats=1)
