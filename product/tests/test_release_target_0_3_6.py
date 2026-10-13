"""New source/package intent; this is not native or artifact acceptance."""
import ast
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_source_version_is_distinct_from_the_published_hotfix():
    path = ROOT / 'hero_ref/tools/hero_ref_version.py'
    spec = importlib.util.spec_from_file_location('version_036_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.HERO_REF_VERSION == '0.3.6'


def test_new_modules_have_the_selected_value_tier():
    manifest = json.loads((ROOT / 'scripts/client_protection_manifest.json').read_bytes())
    assert 'auction_hall_6001_quote_model' in manifest['critical_native']['engine_modules']
    display = 'src/bidking_lab/secure/membership_ui.py'
    assert display in manifest['plaintext_allowlist']
    assert display in manifest['packaged_plaintext_python_allowlist']
    assert 'src/bidking_lab/extract/official_20261008_model_profile.py' in manifest['build_only_source_authorities']
    # New display helpers do not move the existing credential implementation
    # out of its native boundary.
    protected = {path for tree in manifest['critical_native']['package_trees']
                 for path in tree['protected_modules']}
    assert {'bidking_lab/secure/client_activate.py', 'bidking_lab/secure/enforce.py'} <= protected


def test_new_offline_profile_has_no_packaged_runtime_importer():
    manifest = json.loads((ROOT / 'scripts/client_protection_manifest.json').read_bytes())
    excluded = set(manifest['build_only_source_authorities'])
    target = 'bidking_lab.extract.official_20261008_model_profile'
    leaf = target.rsplit('.', 1)[-1]
    found = []
    for root in manifest['source_roots']:
        for path in (ROOT / root).rglob('*.py'):
            relative = path.relative_to(ROOT).as_posix()
            if relative in excluded:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
                if isinstance(node, ast.Import) and any(alias.name == target for alias in node.names):
                    found.append(relative)
                if isinstance(node, ast.ImportFrom) and (
                    node.module == target or (node.module in (None, 'bidking_lab.extract')
                                             and any(alias.name == leaf for alias in node.names))):
                    found.append(relative)
    assert found == []
