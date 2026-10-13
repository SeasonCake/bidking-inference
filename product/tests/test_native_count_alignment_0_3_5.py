"""Source-only native count alignment; never execute the builder or inspect PE files."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(os.environ.get("NATIVE_COUNT_SCRIPTS", ROOT / "scripts"))
QUOTE_MODULE = "bidking_lab/live/quote_preferences.py"
QUOTE_MEMBER = "src/bidking_lab/live/quote_preferences.cp313-win_amd64.pyd"


@pytest.fixture(scope="module")
def inventory():
    spec = importlib.util.spec_from_file_location(
        "native_count_alignment_helper", SCRIPTS / "native_release_hardening.py"
    )
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    manifest = json.loads((SCRIPTS / "client_protection_manifest.json").read_text("utf-8"))
    contract = native.load_contract(SCRIPTS / "release_native_binary_contract.json")
    # Manifest-derived member names plus the declared exact binaries and one ABI
    # name satisfying the existing kernel glob. These are names, not built bytes.
    available = native._protected_members(manifest) | set(contract["inventory"]["exact_members"])
    available.add("src/bidking_kernel.cp313-win_amd64.pyd")
    expected = native.expected_first_party_members(available, contract, manifest)
    return native, manifest, contract, available, expected


def test_actual_manifest_member_set(inventory):
    native, manifest, contract, available, expected = inventory
    protected = native._protected_members(manifest)
    assert len(protected) == contract["protected_cython_module_count"] == 112
    assert len(expected - protected) == contract["additional_native_binary_count"] == 7
    assert expected == available == native.actual_first_party_members(available, contract)
    assert QUOTE_MEMBER in expected


def check_zip_count(value, expected):
    count = value["fixed_manifest_values"]["NativeBinaryCount"]
    assert type(count) is str
    assert count == str(len(expected))


def test_zip_fixed_count_matches_enumerated_inventory(inventory):
    value = json.loads((SCRIPTS / "release_zip_contract.json").read_text("utf-8"))
    check_zip_count(value, inventory[-1])


@pytest.mark.parametrize("delta,rejected", [(0, False), (-1, True), (1, True)])
def test_real_zip_audit_count_comparison(inventory, delta, rejected):
    tree = ast.parse((SCRIPTS / "audit_release_zip.py").read_text("utf-8"))
    comparisons = [node for node in ast.walk(tree) if isinstance(node, ast.Compare)
                   and isinstance(node.left, ast.Call)
                   and ast.unparse(node.left) == "fields.get('nativebinarycount', '')"]
    assert len(comparisons) == 1, "Missing or ambiguous executable count comparison"
    expression = compile(ast.Expression(comparisons[0]), "exact audit count expression", "eval")
    fields = {"nativebinarycount": str(len(inventory[-1]) + delta)}
    assert eval(expression, {"fields": fields}) is rejected


@pytest.mark.parametrize("bad", ["118", "120", 119])
def test_zip_checker_rejects_wrong_count_or_type(inventory, bad):
    with pytest.raises(AssertionError):
        check_zip_count({"fixed_manifest_values": {"NativeBinaryCount": bad}}, inventory[-1])


def test_missing_quote_preferences_manifest_member_is_rejected(inventory):
    native, manifest, contract, available, _ = inventory
    incomplete = copy.deepcopy(manifest)
    live = next(t for t in incomplete["critical_native"]["package_trees"] if t["path"] == "bidking_lab/live")
    live["protected_modules"].remove(QUOTE_MODULE)
    with pytest.raises(native.NativeContractError, match="native_binary_inventory_mismatch"):
        native.expected_first_party_members(available, contract, incomplete)


@pytest.mark.parametrize("change", ["missing_quote", "extra_binary", "missing_kernel", "duplicate_kernel"])
def test_existing_receipt_inventory_gate_rejects_missing_or_extra(inventory, change):
    native, manifest, contract, available, _ = inventory
    names = set(available)
    if change == "missing_quote":
        names.remove(QUOTE_MEMBER)
    elif change == "extra_binary":
        names.add("src/unexpected.cp313-win_amd64.pyd")
    elif change == "missing_kernel":
        names.remove("src/bidking_kernel.cp313-win_amd64.pyd")
    else:
        names.add("src/bidking_kernel_extra.pyd")
    # Empty receipt deliberately stops before PE parsing; this asserts only the
    # existing inventory rejection, not receipt validity or native hardening.
    issues = native.receipt_issues(
        dict.fromkeys(names, b""), contract=contract, protection_manifest=manifest, receipt={}
    )
    assert "native_binary_inventory_mismatch" in issues


@pytest.mark.parametrize("delta,schema,distribution,accept", [
    (0, "bidking_native_binary_receipt_v2", "unsigned", True),
    (-1, "bidking_native_binary_receipt_v2", "unsigned", False),
    (1, "bidking_native_binary_receipt_v2", "unsigned", False),
    (0, "bidking_native_binary_receipt_v1", "unsigned", False),
    (0, "bidking_native_binary_receipt_v2", "signed", False),
])
def test_real_powershell_receipt_condition(inventory, delta, schema, distribution, accept):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell, "PowerShell AST parser required for executable condition coverage"
    script = r'''
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$tree = [System.Management.Automation.Language.Parser]::ParseFile($env:NCOUNT_BUILDER, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Builder parse error' }
$guards = @($tree.FindAll({ param($n)
    $n -is [System.Management.Automation.Language.IfStatementAst] -and
    $n.Clauses.Count -eq 1 -and
    $n.Clauses[0].Item1.Extent.Text -match '\$NativeBinaryCount\s+-ne\s+\d+'
}, $true))
if ($guards.Count -ne 1) { throw 'Ambiguous receipt guard' }
$guard = $guards[0]
if ($guard.ElseClause -or $guard.Clauses[0].Item2.Statements.Count -ne 1 -or
    $guard.Clauses[0].Item2.Statements[0] -isnot [System.Management.Automation.Language.ThrowStatementAst]) {
    throw 'Receipt guard is no longer an isolated rejection statement'
}
$NativeBinaryReceipt = $env:NCOUNT_RECEIPT | ConvertFrom-Json
$NativeBinaryCount = [int]$NativeBinaryReceipt.member_count
Write-Output ('AST_GUARD=' + $guard.Extent.StartLineNumber + ':' + $guard.Extent.EndLineNumber)
try { . ([scriptblock]::Create($guard.Extent.Text)) }
catch { Write-Output 'GUARD_REJECTED'; exit 3 }
Write-Output 'GUARD_ACCEPTED'
'''
    receipt = {"member_count": len(inventory[-1]) + delta, "schema": schema, "distribution": distribution}
    env = dict(os.environ, NCOUNT_BUILDER=str(SCRIPTS / "build_live_portable.ps1"), NCOUNT_RECEIPT=json.dumps(receipt))
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script], env=env, capture_output=True, timeout=10)
    print(result.stdout.decode("utf-8", errors="replace"))
    assert result.returncode == (0 if accept else 3), result.stderr.decode("utf-8", errors="replace")
    assert (b"GUARD_ACCEPTED" if accept else b"GUARD_REJECTED") in result.stdout
