"""ACT-A01: real client entry, isolated identities and counted mock transport."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from bidking_lab.secure import activation, client_activate as ca, enforce
from test_activation_clear_runtime_deny_0_3_1 import (
    isolated_clear_runtime, _TOKEN_A, _TOKEN_B, _success_response,
)


@pytest.fixture
def calls(monkeypatch, isolated_clear_runtime):
    calls = []

    def post(url, device, token, timeout, *args):
        calls.append(token)
        return _success_response(token)

    monkeypatch.setattr(ca, '_post_activate', post)
    return calls


@pytest.mark.parametrize('entry', ['client', 'enforce'])
def test_partial_clear_tombstone_blocks_before_http(monkeypatch, calls, entry):
    monkeypatch.setattr(ca, '_clear_cache', lambda: {
        'cache_cleared': False, 'cache_invalidated': True,
    })
    info = ca.clear_activation()
    assert info['cleared'] is False and info['cache_invalidated'] is True
    submit = ca.activate if entry == 'client' else enforce.refresh_activation
    result = submit(ui_token=_TOKEN_B)
    assert calls == []
    assert result.activated is False
    assert result.reason == 'local_clear_incomplete'
    assert result.source == 'client'
    assert ca._RUNTIME_ACTIVATION_DENIED.is_set()
    text = enforce.activation_state(result)['text']
    assert '重启' in text and '服务器' not in text
    banner = result.status_line()
    assert '重启' in banner and 'local_clear_incomplete' not in banner
    assert '服务器' not in banner


def test_complete_clear_new_identity_reaches_http_once(calls):
    assert ca.clear_activation()['cleared'] is True
    result = enforce.refresh_activation(ui_token=_TOKEN_B)
    assert calls == [_TOKEN_B]
    assert result.activated is True
    assert activation.is_activated() is True
    assert not ca._RUNTIME_ACTIVATION_DENIED.is_set()


def test_enforce_failed_clear_does_not_autorefresh_http(monkeypatch, calls):
    monkeypatch.setattr(ca, '_clear_cache', lambda: {
        'cache_cleared': False, 'cache_invalidated': True,
    })
    assert enforce.clear_activation()['cleared'] is False
    assert calls == []


@pytest.mark.parametrize('token', [_TOKEN_A, _TOKEN_B])
def test_unfinished_epoch_blocks_every_identity(calls, token):
    ca.begin_activation_identity_clear()
    result = enforce.refresh_activation(ui_token=token)
    assert calls == []
    assert result.reason == 'local_clear_incomplete'


def test_complete_clear_same_old_identity_still_blocked(calls):
    assert ca.clear_activation()['cleared'] is True
    result = enforce.refresh_activation(ui_token=_TOKEN_A)
    assert calls == []
    assert result.reason == 'local_clear_incomplete'
    assert ca._RUNTIME_ACTIVATION_DENIED.is_set()


@pytest.fixture(scope='module')
def dialog_handler_code():
    """Compile the frozen handler once; no widgets or client state are shared."""
    source = Path(__file__).parents[1] / 'hero_ref/tools/ahmad_tk_overlay.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    method = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == '_open_token_dialog')
    start = next(i for i, n in enumerate(method.body)
                 if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                 and n.targets[0].id == 'entry')
    end = next(i for i, n in enumerate(method.body[start:], start)
               if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
               and isinstance(n.value.func, ast.Attribute)
               and n.value.func.attr == 'protocol')
    return compile(ast.Module(body=method.body[start:end + 1], type_ignores=[]),
                   str(source), 'exec')


@pytest.fixture
def dialog(monkeypatch, calls, dialog_handler_code):
    """Execute unchanged real dialog handler statements, with deterministic Tk/workers.

    This is control-flow evidence only; it does not claim rendered Tk visibility.
    """
    workers, callbacks = [], []

    class Widget:
        def __init__(self, *_args, **kwargs):
            self.options = dict(kwargs, state='normal')
            self.value = ''
            self.bindings = {}

        def configure(self, **kwargs):
            self.options.update(kwargs)

        def pack(self, **_kwargs):
            pass

        def focus_set(self):
            pass

        def get(self):
            return self.value

        def delete(self, *_args):
            self.value = ''

        def bind(self, event, callback):
            self.bindings[event] = callback

        def protocol(self, *_args):
            pass

        def destroy(self):
            self.options['destroyed'] = True

    class Worker:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            workers.append(self.target)

    fake_self = SimpleNamespace(root=SimpleNamespace(
        after=lambda _delay, callback: callbacks.append(callback)))
    monkeypatch.setattr(ca, 'is_single_activation_token_input', lambda _: True)

    def open_dialog():
        namespace = dict(self=fake_self, dlg=Widget(),
                         tk=SimpleNamespace(Entry=Widget, Label=Widget, Frame=Widget,
                                            Button=Widget, TclError=RuntimeError),
                         threading=SimpleNamespace(Thread=Worker))
        namespace.update({name: name for name in
                          ('BG', 'TEXT', 'PANEL', 'MUTED', 'GOOD', 'BAD', 'WARN', 'FONT_UI')})
        exec(dialog_handler_code, namespace)
        namespace.update(workers=workers, callbacks=callbacks, reopen=open_dialog)
        return SimpleNamespace(**namespace)

    return open_dialog()


def _run_ui_job(dialog):
    dialog.workers.pop(0)()
    dialog.callbacks.pop(0)()


def test_dialog_partial_clear_does_not_invite_retry_or_return(monkeypatch, dialog, calls):
    monkeypatch.setattr(ca, '_clear_cache', lambda: {
        'cache_cleared': False, 'cache_invalidated': True,
    })
    dialog._do_clear()
    _run_ui_job(dialog)
    assert '可重试激活' not in dialog.status.options['text']
    assert '重启' in dialog.status.options['text']
    assert dialog.ok_btn.options['state'] == 'disabled'
    dialog.entry.value = _TOKEN_B
    dialog.entry.bindings['<Return>'](None)
    dialog._submit()
    assert dialog.workers == []
    assert calls == []


def test_dialog_late_activation_callback_cannot_reopen_later_clear(dialog, calls):
    dialog.entry.value = _TOKEN_B
    dialog._submit()
    dialog.workers.pop(0)()  # Success queued, not yet painted.
    assert calls == [_TOKEN_B]
    dialog._do_clear()
    dialog.callbacks.pop(0)()  # Old success after newer clear began.
    assert dialog.ok_btn.options['state'] == 'disabled'
    assert dialog.status.options['text'] == '清除中…'
    assert dialog.callbacks == []  # No delayed close from stale success.


@pytest.mark.parametrize('component', ['_clear_persisted_tokens', '_clear_cache'])
def test_clear_exception_is_local_and_cannot_send(monkeypatch, calls, component):
    def fail():
        raise OSError('SYNTHETIC-PRIVATE-EXCEPTION')

    monkeypatch.setattr(ca, component, fail)
    info = enforce.clear_activation()
    assert info['cleared'] is False
    result = enforce.refresh_activation(ui_token=_TOKEN_B)
    assert calls == []
    assert result.reason == 'local_clear_incomplete'
    assert 'SYNTHETIC-PRIVATE' not in repr(info) + repr(result)


def test_reported_cleared_without_crossing_identity_cannot_seek_grant(monkeypatch, calls):
    monkeypatch.setattr(ca, '_clear_persisted_tokens', lambda: True)
    info = ca.clear_activation()
    assert info['cleared'] is True
    assert info['can_activate'] is False
    assert ca.save_token(_TOKEN_B) is True
    result = ca.activate(ui_token=_TOKEN_B)
    assert calls == []
    assert result.reason == 'local_clear_incomplete'


@pytest.mark.parametrize('info', [None, {}, {'cleared': True},
                                {'cleared': 'true', 'can_activate': True}])
def test_dialog_missing_or_nonboolean_clear_result_stays_blocked(monkeypatch, dialog, calls, info):
    monkeypatch.setattr(ca, 'clear_activation', lambda **_: info)
    dialog._do_clear()
    _run_ui_job(dialog)
    assert dialog.ok_btn.options['state'] == 'disabled'
    assert '重启' in dialog.status.options['text']
    assert calls == []
    dialog.entry.value = _TOKEN_B
    dialog.entry.bindings['<Return>'](None)
    assert dialog.workers == []


def test_reopened_dialog_checks_existing_incomplete_epoch(dialog, calls):
    ca.begin_activation_identity_clear()
    dialog._close()
    dialog = dialog.reopen()
    assert dialog.ok_btn.options['state'] == 'disabled'
    dialog.entry.value = _TOKEN_B
    dialog.entry.bindings['<Return>'](None)
    assert dialog.workers == [] and calls == []


def test_dialog_clear_exception_survives_reopening_without_raw_error(monkeypatch, dialog, calls):
    def fail():
        raise OSError('SYNTHETIC-PRIVATE-UI-EXCEPTION')

    monkeypatch.setattr(enforce, 'clear_activation', fail)
    dialog._do_clear()
    _run_ui_job(dialog)
    assert '重启' in dialog.status.options['text']
    assert 'SYNTHETIC' not in dialog.status.options['text']
    dialog._close()
    dialog = dialog.reopen()
    dialog.entry.value = _TOKEN_B
    dialog.entry.bindings['<Return>'](None)
    assert dialog.workers == [] and calls == []
    assert dialog.ok_btn.options['state'] == 'disabled'


def test_dialog_old_identity_typing_blocks_but_new_identity_can_submit(dialog, calls):
    assert ca.clear_activation()['can_activate'] is True
    dialog.entry.value = _TOKEN_A
    dialog.entry.bindings['<KeyRelease>'](None)
    assert dialog.ok_btn.options['state'] == 'disabled'
    dialog.entry.bindings['<Return>'](None)
    assert dialog.workers == []
    dialog.entry.value = _TOKEN_B
    dialog.entry.bindings['<KeyRelease>'](None)
    assert dialog.ok_btn.options['state'] == 'normal'
    dialog.entry.bindings['<Return>'](None)
    _run_ui_job(dialog)
    assert calls == [_TOKEN_B]


def test_return_while_clear_worker_is_pending_cannot_submit(dialog, calls):
    dialog._do_clear()
    dialog.entry.value = _TOKEN_B
    dialog.entry.bindings['<Return>'](None)
    dialog._do_clear()
    assert len(dialog.workers) == 1
    assert calls == []


def test_late_delayed_close_does_not_close_newer_clear(dialog, calls):
    dialog.entry.value = _TOKEN_B
    dialog._submit()
    _run_ui_job(dialog)
    assert calls == [_TOKEN_B]
    assert len(dialog.callbacks) == 1
    dialog._do_clear()
    dialog.callbacks.pop(0)()
    assert not dialog.dlg.options.get('destroyed')
    assert dialog.ok_btn.options['state'] == 'disabled'


def test_clear_between_snapshot_and_transport_start_has_zero_http(monkeypatch, calls):
    original = ca._activation_endpoint_candidates

    def endpoints(ingest):
        result = original(ingest)
        ca.begin_activation_identity_clear()
        return result

    monkeypatch.setattr(ca, '_activation_endpoint_candidates', endpoints)
    result = ca.activate(ui_token=_TOKEN_B)
    assert calls == []
    assert result.reason == 'local_clear_incomplete'


def test_clear_during_existing_http_does_not_deadlock_or_grant(monkeypatch, calls):
    assert enforce.refresh_activation(ui_token=_TOKEN_A).activated is True
    assert calls == [_TOKEN_A]
    calls.clear()
    entered, release = threading.Event(), threading.Event()

    def post(*_args):
        calls.append('started')
        entered.set()
        assert release.wait(3)
        return _success_response(_TOKEN_B)

    monkeypatch.setattr(ca, '_post_activate', post)
    results = []
    worker = threading.Thread(target=lambda: results.append(
        enforce.refresh_activation(ui_token=_TOKEN_B)))
    worker.start()
    assert entered.wait(3)
    clear_done = threading.Event()

    def clear():
        ca.begin_activation_identity_clear()
        clear_done.set()

    writer = threading.Thread(target=clear)
    writer.start()
    try:
        assert clear_done.wait(1), 'clear must finish while HTTP still waits'
        assert ca.activation_submission_preflight(_TOKEN_B) is False
    finally:
        release.set()
        worker.join(3)
        writer.join(3)
    assert not worker.is_alive() and not writer.is_alive()
    assert calls == ['started']
    assert results[0].activated is False
    assert ca._RUNTIME_ACTIVATION_DENIED.is_set()
    assert activation.is_activated() is False


def test_complete_clear_auto_trial_keeps_existing_success_flow(monkeypatch, calls):
    def post(_url, device, token, _timeout):
        calls.append(token)
        code, body = _success_response(token)
        body.update(mode='trial', trial_exp=9_000_000_000)
        return code, body

    monkeypatch.setattr(ca, '_post_activate', post)
    info = enforce.clear_activation()
    assert info['cleared'] is True and info['can_activate'] is True
    assert calls == ['']
    assert enforce.current().activated is True
    assert enforce.current().mode == 'trial'
    assert not ca._RUNTIME_ACTIVATION_DENIED.is_set()


@pytest.mark.parametrize('reason', ['state_error', 'server_error'])
def test_real_server_errors_remain_server_errors(monkeypatch, calls, reason):
    def post(*_args):
        calls.append('server')
        return 503, {'ok': False, 'mode': 'paid', 'reason': reason}

    monkeypatch.setattr(ca, '_post_activate', post)
    result = enforce.refresh_activation(ui_token=_TOKEN_B)
    assert calls == ['server']
    assert result.reason == reason and result.source == 'server'
    state = enforce.activation_state(result)
    assert '服务器' in state['text']
    assert state['needs_token'] is False


def test_fresh_process_has_no_inherited_clear_epoch(tmp_path):
    code = '''
import base64, json, os
from pathlib import Path
from bidking_lab.secure import client_activate as ca
assert ca._ACTIVATION_CLEAR_STATE is None and ca._ACTIVATION_CLEAR_EPOCH == 0
ca._package_dir = lambda: Path(os.environ['LOCALAPPDATA']) / 'empty-package'
ca.device_fingerprint = lambda: 'SYNTHETIC-FRESH-DEVICE'
ca._activate_urls = lambda _: ['https://example.invalid/activate']
calls = []
def post(url, device, token, timeout):
    calls.append(1)
    return 200, {'ok': True, 'mode': 'paid',
        'lease': json.dumps({'uid': 'fixture', 'device': device, 'exp': 9000000000}) + '.sig',
        'master_key': base64.b64encode(b'F' * 32).decode()}
ca._post_activate = post
r = ca.activate(ui_token='SYNTHETIC-FRESH-TOKEN', now=2000000)
assert r.activated is True and calls == [1]
print('fresh-process: paid grant, HTTP seam count=1')
'''
    env = dict(os.environ, LOCALAPPDATA=str(tmp_path),
               PYTHONPATH=str(Path(__file__).parents[1] / 'src'))
    for key in ('BIDKING_ACTIVATION_TOKEN', 'BIDKING_ACTIVATION_FILE'):
        env.pop(key, None)
    result = subprocess.run([sys.executable, '-c', code], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'HTTP seam count=1' in result.stdout


@pytest.mark.parametrize('complete', [False, True])
def test_real_post_path_counts_http_boundary_zero_or_one(monkeypatch, isolated_clear_runtime, complete):
    http_calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def getcode(self):
            return 200

        def read(self):
            return json.dumps(_success_response(_TOKEN_B)[1]).encode()

    def urlopen(_request, **_kwargs):
        http_calls.append(1)
        return Response()

    monkeypatch.setattr(ca, 'urlopen_verified', urlopen)
    if not complete:
        monkeypatch.setattr(ca, '_clear_cache', lambda: {
            'cache_cleared': False, 'cache_invalidated': True,
        })
    assert ca.clear_activation()['cleared'] is complete
    result = enforce.refresh_activation(ui_token=_TOKEN_B)
    assert http_calls == ([1] if complete else [])
    assert result.activated is complete


@pytest.mark.parametrize('clear_during_https', [False, True])
def test_emergency_start_obeys_same_clear_gate(monkeypatch, calls, clear_during_https):
    emergency_calls = []

    def https(*_args):
        calls.append('https')
        if clear_during_https:
            ca.begin_activation_identity_clear()
        return ca._ActivationPostFailure('connect_timeout', stage='tcp')

    def emergency(*_args):
        emergency_calls.append(1)
        return _success_response(_TOKEN_B)

    monkeypatch.setattr(ca, '_post_activate', https)
    monkeypatch.setattr(ca, '_post_activate_http_e2ee', emergency)
    monkeypatch.setattr(ca, '_load_activation_http_e2ee_config', lambda:
                        SimpleNamespace(url='http://example.invalid/fixture'))
    result = enforce.refresh_activation(ui_token=_TOKEN_B)
    assert calls == ['https']
    assert emergency_calls == ([] if clear_during_https else [1])
    assert result.activated is (not clear_during_https)
    if clear_during_https:
        assert ca._RUNTIME_ACTIVATION_DENIED.is_set()


def test_later_clear_blocks_next_https_endpoint(monkeypatch, calls):
    monkeypatch.setattr(ca, '_activate_urls', lambda _: [
        'https://one.invalid/activate', 'https://two.invalid/activate'])

    def post(*_args):
        calls.append(1)
        ca.begin_activation_identity_clear()
        return ca._ActivationPostFailure('connect_timeout', stage='tcp')

    monkeypatch.setattr(ca, '_post_activate', post)
    result = enforce.refresh_activation(ui_token=_TOKEN_B)
    assert calls == [1]  # The already-started request cannot be undone.
    assert result.activated is False
    assert ca._RUNTIME_ACTIVATION_DENIED.is_set()


def test_ordinary_start_is_under_both_locks_and_wait_is_outside(monkeypatch, calls):
    original = ca._begin_activation_transport
    observations = []

    def begin(*args):
        observations.append(('begin',
                             ca._ACTIVATION_TRANSPORT_START_LOCK._is_owned(),
                             ca._ACTIVATION_IDENTITY_SOURCE_LOCK._is_owned()))
        handle = original(*args)

        class ObservedHandle:
            def result(self):
                observations.append(('wait',
                                     ca._ACTIVATION_TRANSPORT_START_LOCK._is_owned(),
                                     ca._ACTIVATION_IDENTITY_SOURCE_LOCK._is_owned()))
                return handle.result()

        return ObservedHandle()

    monkeypatch.setattr(ca, '_begin_activation_transport', begin)
    result = enforce.refresh_activation(ui_token=_TOKEN_B)
    assert result.activated is True and calls == [_TOKEN_B]
    assert observations == [('begin', True, True), ('wait', False, False)]


def test_ordinary_writer_wins_transport_barrier_before_any_send(monkeypatch, calls):
    endpoints_ready = threading.Event()
    original = ca._activation_endpoint_candidates

    def endpoints(ingest):
        result = original(ingest)
        endpoints_ready.set()
        return result

    monkeypatch.setattr(ca, '_activation_endpoint_candidates', endpoints)
    results = []
    with ca._ACTIVATION_TRANSPORT_START_LOCK:
        worker = threading.Thread(target=lambda: results.append(
            enforce.refresh_activation(ui_token=_TOKEN_B)))
        worker.start()
        assert endpoints_ready.wait(1)
        ca.begin_activation_identity_clear()
    worker.join(3)
    assert not worker.is_alive()
    assert calls == []
    assert results[0].activated is False
    assert ca._RUNTIME_ACTIVATION_DENIED.is_set()


@pytest.mark.parametrize('close_at', ['before_worker', 'before_callback'])
@pytest.mark.parametrize('outcome', ['success', 'partial', 'exception', 'missing', 'not_crossed'])
def test_f01_reopened_view_consumes_clear_completion(monkeypatch, dialog, calls, close_at, outcome):
    if outcome == 'partial':
        monkeypatch.setattr(ca, '_clear_cache', lambda: {
            'cache_cleared': False, 'cache_invalidated': True})
    elif outcome == 'exception':
        def fail(**_):
            raise OSError('SYNTHETIC-F01-CLEAR-EXCEPTION')
        monkeypatch.setattr(ca, 'clear_activation', fail)
    elif outcome == 'missing':
        monkeypatch.setattr(ca, 'clear_activation', lambda **_: None)
    elif outcome == 'not_crossed':
        monkeypatch.setattr(ca, '_clear_persisted_tokens', lambda: True)
    old = dialog
    old._do_clear()
    if close_at == 'before_callback':
        old.workers.pop(0)()
    old._close()
    current = old.reopen()
    current.entry.value = _TOKEN_B
    if close_at == 'before_worker':
        assert current.ok_btn.options['state'] == 'disabled'
        assert current.status.options['text'] == '清除中…'
        current.entry.bindings['<Return>'](None)
        assert len(current.workers) == 1  # Only the already-queued clear worker.
        current.workers.pop(0)()
    current.callbacks.pop(0)()  # Completion refreshes the currently open view.
    assert current.entry.value == _TOKEN_B  # Never erase input in a reopened view.
    assert current.ok_btn.options['state'] == ('normal' if outcome == 'success' else 'disabled')
    assert ca.activation_submission_preflight(_TOKEN_B) is (outcome == 'success')
    assert 'SYNTHETIC-F01' not in current.status.options['text']
    current.entry.bindings['<Return>'](None)
    if outcome == 'success':
        assert calls == ['']
        _run_ui_job(current)
        assert calls == ['', _TOKEN_B]
    else:
        assert current.workers == [] and calls == []
        assert '重启' in current.status.options['text']


@pytest.mark.parametrize('older_at', ['before_worker', 'before_callback'])
@pytest.mark.parametrize('later_outcome', ['success', 'partial', 'exception'])
def test_f01_older_success_cannot_release_later_pending_clear(
    monkeypatch, dialog, calls, older_at, later_outcome,
):
    old = dialog
    old._do_clear()
    older_record = old.self._activation_clear_operation
    if older_at == 'before_callback':
        old.workers.pop(0)()
    old._close()
    current = old.reopen()
    current._do_clear()
    latest_record = current.self._activation_clear_operation
    assert latest_record is not older_record
    if older_at == 'before_worker':
        current.workers.pop(0)()
    current.callbacks.pop(0)()  # Old completion must not refresh/unlock the later operation.
    assert older_record['state'] == 'complete'
    assert latest_record['state'] == 'pending'
    assert current.self._activation_clear_operation is latest_record
    assert current.ok_btn.options['state'] == 'disabled'
    assert current.status.options['text'] == '清除中…'
    assert calls == ['']
    current.entry.value = _TOKEN_B
    current.entry.bindings['<Return>'](None)
    assert len(current.workers) == 1
    assert ca.save_token(_TOKEN_A) is True  # Give the later real clear a crossing identity.
    if later_outcome == 'partial':
        monkeypatch.setattr(ca, '_clear_cache', lambda: {
            'cache_cleared': False, 'cache_invalidated': True})
    elif later_outcome == 'exception':
        def fail(**_):
            raise OSError('SYNTHETIC-F01-LATER-EXCEPTION')
        monkeypatch.setattr(ca, 'clear_activation', fail)
    _run_ui_job(current)
    assert current.self._activation_clear_operation is latest_record
    assert latest_record['state'] == ('complete' if later_outcome == 'success' else 'failed')
    assert current.ok_btn.options['state'] == ('normal' if later_outcome == 'success' else 'disabled')
    assert calls == (['', ''] if later_outcome == 'success' else [''])


def test_f01_older_worker_completing_last_cannot_erase_later_failure(monkeypatch, dialog, calls):
    old = dialog
    old._do_clear()
    older_record = old.self._activation_clear_operation
    old._close()
    current = old.reopen()
    current._do_clear()
    latest_record = current.self._activation_clear_operation
    with monkeypatch.context() as partial:
        partial.setattr(ca, '_clear_cache', lambda: {
            'cache_cleared': False, 'cache_invalidated': True})
        current.workers.pop(1)()  # Newer operation actually completes first and fails.
        current.callbacks.pop(0)()
    assert latest_record['state'] == 'failed' and calls == []
    assert ca.save_token(_TOKEN_A) is True
    current.workers.pop(0)()  # Older operation now successfully clears a real crossing identity.
    current.callbacks.pop(0)()
    assert older_record['state'] == 'complete'
    assert ca.activation_submission_preflight(_TOKEN_B) is True
    assert calls == ['']
    assert current.self._activation_clear_operation is latest_record
    assert latest_record['state'] == 'failed'
    assert current.ok_btn.options['state'] == 'disabled'
    current.entry.value = _TOKEN_B
    current.entry.bindings['<Return>'](None)
    assert current.workers == []


def test_f01_late_old_window_close_does_not_detach_current_view(dialog, calls):
    old = dialog
    old._do_clear()
    old._close()
    current = old.reopen()
    old._close()  # A repeated old close cannot unregister the new view's callback.
    _run_ui_job(current)
    assert calls == ['']
    assert current.ok_btn.options['state'] == 'normal'
    assert current.self._activation_clear_view_refresh is current._refresh_clear_result


def test_f01_older_failed_worker_refreshes_current_backend_eligibility(monkeypatch, dialog, calls):
    old = dialog
    old._do_clear()
    old._close()
    current = old.reopen()
    current._do_clear()
    latest_record = current.self._activation_clear_operation
    current.workers.pop(1)()  # Newer clear succeeds first.
    current.callbacks.pop(0)()
    assert latest_record['state'] == 'complete'
    assert current.ok_btn.options['state'] == 'normal'
    assert calls == ['']
    monkeypatch.setattr(ca, '_clear_cache', lambda: {
        'cache_cleared': False, 'cache_invalidated': True})
    current.workers.pop(0)()  # Older worker now changes the real backend to denied.
    current.callbacks.pop(0)()
    assert current.self._activation_clear_operation is latest_record
    assert latest_record['state'] == 'complete'  # Do not overwrite the later operation's result.
    assert ca.activation_submission_preflight(_TOKEN_B) is False
    assert current.ok_btn.options['state'] == 'disabled'
    assert '重启' in current.status.options['text']
    current.entry.value = _TOKEN_B
    current.entry.bindings['<Return>'](None)
    assert current.workers == [] and calls == ['']
