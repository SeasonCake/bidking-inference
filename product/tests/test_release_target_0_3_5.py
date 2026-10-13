"""Source target identity with unchanged packaged-version and update consumers."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest
from bidking_lab.live import version_check as vc
from bidking_lab.live import startup_version_notice as notice

SOURCE = Path(__file__).resolve().parents[1] / 'hero_ref/tools/hero_ref_version.py'


def load_source(path):
    spec = importlib.util.spec_from_file_location('release_target_test_version', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def isolated_copy(tmp_path):
    folder = tmp_path / 'a/b/c/d/e/f'
    folder.mkdir(parents=True)
    path = folder / 'hero_ref_version.py'
    shutil.copyfile(SOURCE, path)
    return load_source(path)


def test_source_target_is_0_3_5():
    assert load_source(SOURCE).HERO_REF_VERSION == '0.3.5'


def test_isolated_source_fallback_and_real_app_consumer(tmp_path, monkeypatch):
    module = isolated_copy(tmp_path)
    assert module.resolve_hero_ref_version() == '0.3.5'
    monkeypatch.setitem(sys.modules, 'hero_ref_version', module)
    assert vc.app_version() == '0.3.5'


def test_real_worktree_app_version_matches_exact_manifest_resolution():
    # This reads only the resolver's six explicit parents, never a directory scan.
    expected = load_source(SOURCE).resolve_hero_ref_version()
    assert vc.app_version() == expected


class Response:
    status = 200
    def __init__(self, value):
        self.raw = json.dumps(value).encode('utf-8')
    def read(self):
        return self.raw
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


@pytest.mark.parametrize('latest,minimum,visible,required', [
    ('0.3.4-hotfix2', '0.3.4', False, False),
    ('0.3.5', '0.3.4', False, False),
    ('0.3.5-r1', '0.3.4', True, False),
    ('0.3.5-hotfix1', '0.3.5', True, False),
    ('0.3.6', '0.3.6', True, True),
    ('0.3.5-garbage', '0.3.4', False, False),
    (None, '0.3.4', False, False),
])
def test_real_check_publish_and_notice_states(tmp_path, monkeypatch, latest, minimum, visible, required):
    module = isolated_copy(tmp_path)
    monkeypatch.setitem(sys.modules, 'hero_ref_version', module)
    monkeypatch.setattr(vc, 'detect_game_version', lambda: None)
    calls = []
    def open_response(url, **kwargs):
        calls.append(url)
        return Response({'latest': latest, 'min': minimum, 'url': 'https://download.invalid/release'})
    monkeypatch.setattr(vc, 'urlopen_verified', open_response)
    status = notice.check_and_publish_startup_version(tmp_path / 'logs',
        checker=lambda **kwargs: vc.check_against_server('https://version.invalid/ingest', **kwargs))
    assert status['app'] == '0.3.5' and len(calls) == 1
    saved = notice.read_recent_startup_version_status(tmp_path / 'logs', app_version='0.3.5')
    state = notice.build_update_notice(saved, app_version='0.3.5')
    assert state['visible'] is visible and state['required'] is required
    if latest in {'0.3.4-hotfix2', '0.3.5'}:
        assert status['up_to_date'] is True


def test_old_package_manifest_overrides_and_gets_new_release_notice(tmp_path, monkeypatch):
    module = isolated_copy(tmp_path)
    (Path(module.__file__).parent / 'BUILD_MANIFEST.txt').write_text('PackageVersion: v0.3.4-hotfix2\n', encoding='utf-8')
    monkeypatch.setitem(sys.modules, 'hero_ref_version', module)
    monkeypatch.setattr(vc, 'detect_game_version', lambda: None)
    monkeypatch.setattr(vc, 'urlopen_verified', lambda *args, **kwargs: Response({'latest': '0.3.5', 'min': '0.3.4'}))
    assert vc.app_version() == '0.3.4-hotfix2'
    status = vc.check_against_server('https://version.invalid/ingest')
    assert status['up_to_date'] is False
    assert notice.build_update_notice(status, app_version=vc.app_version())['visible'] is True


@pytest.mark.parametrize('kind', ['unreachable', 'malformed'])
def test_failed_checks_remain_silent(tmp_path, monkeypatch, kind):
    module = isolated_copy(tmp_path)
    monkeypatch.setitem(sys.modules, 'hero_ref_version', module)
    monkeypatch.setattr(vc, 'detect_game_version', lambda: None)
    def failed(*args, **kwargs):
        if kind == 'unreachable':
            raise OSError('synthetic unavailable')
        value = Response({})
        value.raw = b'not-json'
        return value
    monkeypatch.setattr(vc, 'urlopen_verified', failed)
    status = vc.check_against_server('https://version.invalid/ingest')
    assert status['ok'] is False
    assert status['reachable'] is (kind == 'malformed')
    assert notice.build_update_notice(status, app_version='0.3.5')['visible'] is False


def test_project_version_order_is_preserved():
    ordered = ['0.3.4-hotfix2', '0.3.5', '0.3.5-r1', '0.3.5-hotfix1', '0.3.5-hotfix1-r1']
    for old, new in zip(ordered, ordered[1:]):
        assert vc._vtuple(old) < vc._vtuple(new)
        assert vc._ge(new, old) and not vc._ge(old, new)
    assert vc._vtuple('0.3.5-preview') is None
