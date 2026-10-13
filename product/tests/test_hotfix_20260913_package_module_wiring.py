"""The new quote implementation must use the existing engine packaging path."""
import copy
import ast
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
MODULE = 'wuqilin_5215_quote_model'
MEMBER = f'hero_ref/src/{MODULE}.cp313-win_amd64.pyd'
CALIBRATED = 'data/processed/shipwreck_calibrated_b049.json'
RETIRED = 'data/processed/activity_2511_provisional_b049.json'


def test_new_quote_module_uses_existing_encrypted_engine_group():
    manifest = json.loads((SCRIPTS / 'client_protection_manifest.json').read_bytes())
    assert MODULE in manifest['critical_native']['engine_modules']
    assert f'hero_ref/src/{MODULE}.py' not in manifest['plaintext_allowlist']
    assert f'hero_ref/src/{MODULE}.py' not in manifest['packaged_plaintext_python_allowlist']


def test_existing_inventory_rejects_missing_new_quote_module():
    spec = importlib.util.spec_from_file_location('hotfix_package_native', SCRIPTS / 'native_release_hardening.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    manifest = json.loads((SCRIPTS / 'client_protection_manifest.json').read_bytes())
    contract = native.load_contract(SCRIPTS / 'release_native_binary_contract.json')
    available = native._protected_members(manifest) | set(contract['inventory']['exact_members'])
    available.add('src/bidking_kernel.cp313-win_amd64.pyd')
    assert MEMBER in available
    assert len(native.expected_first_party_members(available, contract, manifest)) == 118
    missing = copy.deepcopy(manifest)
    missing['critical_native']['engine_modules'].remove(MODULE)
    with pytest.raises(native.NativeContractError, match='native_binary_inventory_mismatch'):
        native.expected_first_party_members(available, contract, missing)


def test_calibrated_shipwreck_replaces_old_provisional_package_member():
    manifest = json.loads((SCRIPTS / 'package_data_manifest.json').read_bytes())
    fingerprints = json.loads((SCRIPTS / 'package_data_fingerprints.json').read_bytes())
    contract = json.loads((SCRIPTS / 'release_zip_contract.json').read_bytes())
    module = ast.parse((SCRIPTS / 'encrypt_moat_files.py').read_text(encoding='utf-8'))
    assignments = [node for node in module.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == '_MOAT_RELS'
                           for target in node.targets)]
    assert len(assignments) == 1
    moat_members = ast.literal_eval(assignments[0].value)
    for members in (manifest['files'], fingerprints['files'],
                    contract['member_protection'], moat_members):
        assert CALIBRATED in members
        assert RETIRED not in members
    assert len(manifest['files']) == 21
    assert contract['member_protection'][CALIBRATED] == {'mode': 'bkenc2-gzip', 'required': True}
