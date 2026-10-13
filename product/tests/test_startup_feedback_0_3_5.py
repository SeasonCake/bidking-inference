"""Static scope controls; Rust tests and central visible harness are separate evidence."""
from pathlib import Path
import os

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUBJECT = Path(os.environ.get("START_FEEDBACK_SUBJECT", ROOT))
CRATE = SUBJECT / "native/bidking_trust_bootstrap"


def check_feedback_source(source):
    assert 'ShowWindow(hwnd, SW_HIDE)' in source
    assert 'let result = work();' in source
    assert 'drop(feedback);' in source
    assert 'result <= 0' in source
    assert 'recv_timeout' in source
    for forbidden in ('std::process::exit', 'CreateMutex', 'SetForegroundWindow', 'WS_EX_TOPMOST', 'startup-timing', 'read_to_string', 'Command::new', 'TerminateProcess'):
        assert forbidden not in source, forbidden


def test_plain_entry_is_wrapped_before_work_without_changing_result_mapping():
    source = (CRATE / "src/main.rs").read_text("utf-8")
    assert 'mod startup_feedback;' in source
    assert 'startup_feedback::is_plain_launch(std::env::args_os())' in source
    assert 'match startup_feedback::run_with_feedback(feedback, windows_bootstrap::entry)' in source
    assert source.count('windows_bootstrap::entry') == 1
    assert 'windows_bootstrap::report_failure(&failure);' in source
    assert 'windows_bootstrap::report_failure(code);' in source


def test_feedback_has_only_the_frozen_copy_and_close_behavior():
    source = (CRATE / "src/startup_feedback.rs").read_text("utf-8")
    check_feedback_source(source)
    assert '正在启动 BidKing Live，请稍候。' in source
    assert '关闭此提示不会中止启动。' in source
    assert 'SW_SHOWNOACTIVATE' in source


@pytest.mark.parametrize("mutation", ["close_cancels", "timing_ready"])
def test_same_checker_rejects_cancel_and_timing_ready_mutants(mutation):
    source = (CRATE / "src/startup_feedback.rs").read_text("utf-8")
    if mutation == "close_cancels":
        mutated = source.replace('ShowWindow(hwnd, SW_HIDE);', 'std::process::exit(0);', 1)
    else:
        mutated = source + '\nfn false_ready() { let _ = std::fs::read_to_string("startup-timing-v1"); }\n'
    assert mutated != source
    with pytest.raises(AssertionError):
        check_feedback_source(mutated)


def test_delta01_first_show_uses_one_commit_or_cancel_state():
    source = (CRATE / "src/startup_feedback.rs").read_text("utf-8")
    assert 'self.0.compare_exchange(0, 1,' in source
    assert 'self.0.fetch_or(2,' in source
    assert 'window.state.stop.show_if_pending(|| unsafe {' in source
    creation = source.split('if message == WM_NCCREATE {', 1)[1].split('\n        let context', 1)[0]
    assert 'SetWindowLongPtrW' in creation
    assert 'return unsafe { DefWindowProcW(hwnd, message, wparam, lparam) };' in creation
