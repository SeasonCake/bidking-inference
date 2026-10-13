//! Best-effort feedback only. The original entry stays on its calling thread.
//! Closing this window never cancels work or supplies a product-ready signal.

use std::ffi::OsString;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, mpsc};
use std::thread::{self, JoinHandle};
use std::time::Duration;

const TITLE: &str = "BidKing Live";
const STATUS: &str = "正在启动 BidKing Live，请稍候。";
const CLOSE_NOTE: &str = "关闭此提示不会中止启动。";
const PREPARE_TIMEOUT: Duration = Duration::from_millis(750);
const CLOSE_TIMEOUT: Duration = Duration::from_secs(2);

// One local presentation decision, not a cross-launch lock or product-ready state.
// Bit 0 commits the first show; bit 1 requests stop. Cancel wins only from Pending.
// A committed show is already in flight and is followed by the existing UI cleanup.
#[derive(Default)]
struct FeedbackState(AtomicU8);

impl FeedbackState {
    fn new() -> Self { Self(AtomicU8::new(0)) }

    fn request_stop(&self) {
        self.0.fetch_or(2, Ordering::AcqRel);
    }

    fn is_stopped(&self) -> bool {
        self.0.load(Ordering::Acquire) & 2 != 0
    }

    fn show_if_pending(&self, show: impl FnOnce()) -> bool {
        // This CAS is the single first-show/cancel linearization point.
        // Unlike a load/check, cancellation and a show cannot both win Pending.
        if self.0.compare_exchange(0, 1, Ordering::AcqRel, Ordering::Acquire).is_err() {
            return false;
        }
        show();
        true
    }
}

pub fn is_plain_launch(mut args: impl Iterator<Item = OsString>) -> bool {
    args.next().is_some() && args.next().is_none()
}

pub fn run_with_feedback<T>(enabled: bool, work: impl FnOnce() -> T) -> T {
    if enabled {
        run_with_ui(work, win32::run, PREPARE_TIMEOUT)
    } else {
        work()
    }
}

struct UiAttempt {
    finished: mpsc::Receiver<()>,
    stop: Arc<FeedbackState>,
    thread: Option<JoinHandle<()>>,
}

impl Drop for UiAttempt {
    fn drop(&mut self) {
        self.stop.request_stop();
        // A broken UI must never keep the original result/error path hostage.
        // The window's timer also runs in native move/size/menu modal loops.
        if !matches!(
            self.finished.recv_timeout(CLOSE_TIMEOUT),
            Err(mpsc::RecvTimeoutError::Timeout)
        ) {
            if let Some(thread) = self.thread.take() {
                let _ = thread.join();
            }
        }
    }
}

fn run_with_ui<T>(
    work: impl FnOnce() -> T,
    ui: impl FnOnce(Arc<FeedbackState>, mpsc::Sender<()>) + Send + 'static,
    prepare_timeout: Duration,
) -> T {
    let stop = Arc::new(FeedbackState::new());
    let ui_stop = Arc::clone(&stop);
    let (ready_tx, ready_rx) = mpsc::channel();
    let (finished_tx, finished_rx) = mpsc::channel();
    let thread = thread::Builder::new()
        .name("bidking-startup-feedback".into())
        .spawn(move || {
            ui(ui_stop, ready_tx);
            let _ = finished_tx.send(());
        });
    let feedback = thread.ok().map(|thread| UiAttempt {
        finished: finished_rx,
        stop,
        thread: Some(thread),
    });
    if let Some(feedback) = &feedback {
        if ready_rx.recv_timeout(prepare_timeout).is_err() {
            // Cancel an uncommitted show; an already committed show must finish
            // and observe stop in its timer. This never cancels original work.
            feedback.stop.request_stop();
        }
    }
    let result = work();
    drop(feedback);
    result
}

#[derive(Debug)]
struct Layout {
    width: i32,
    height: i32,
    padding: i32,
    line_height: i32,
    second_y: i32,
}

fn text_layout(first_width: i32, second_width: i32, font_height: i32) -> Layout {
    let line_height = font_height.max(1) + 4;
    let padding = line_height;
    let second_y = padding + line_height + line_height / 2;
    Layout {
        width: first_width.max(second_width) + 2 * padding,
        height: second_y + line_height + padding,
        padding,
        line_height,
        second_y,
    }
}

mod win32 {
    use super::*;
    use std::cell::Cell;
    use std::mem::zeroed;
    use std::ptr::null;
    use windows_sys::Win32::Foundation::{GetLastError, HWND, LPARAM, LRESULT, RECT, SIZE, WPARAM, ERROR_CLASS_ALREADY_EXISTS};
    use windows_sys::Win32::Graphics::Gdi::{GetDC, GetStockObject, GetTextExtentPoint32W, ReleaseDC, SelectObject, UpdateWindow, DEFAULT_GUI_FONT, COLOR_BTNFACE};
    use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
    use windows_sys::Win32::UI::WindowsAndMessaging::*;

    struct WindowState {
        stop: Arc<FeedbackState>,
        destroyed: Cell<bool>,
    }

    struct Window {
        hwnd: HWND,
        state: Box<WindowState>,
    }

    impl Drop for Window {
        fn drop(&mut self) {
            if !self.state.destroyed.get() {
                unsafe { DestroyWindow(self.hwnd); }
            }
        }
    }

    unsafe extern "system" fn window_proc(hwnd: HWND, message: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
        if message == WM_NCCREATE {
            let create = unsafe { &*(lparam as *const CREATESTRUCTW) };
            unsafe { SetWindowLongPtrW(hwnd, GWLP_USERDATA, create.lpCreateParams as isize); }
            return unsafe { DefWindowProcW(hwnd, message, wparam, lparam) };
        }
        let context = unsafe { GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *const WindowState };
        match message {
            WM_CLOSE => {
                // The title-bar close affects this feedback surface only.
                unsafe { ShowWindow(hwnd, SW_HIDE); }
                0
            }
            WM_TIMER if wparam == 1 => {
                if !context.is_null() && unsafe { &*context }.stop.is_stopped() {
                    unsafe { DestroyWindow(hwnd); }
                }
                0
            }
            WM_DESTROY => {
                if !context.is_null() {
                    unsafe { &*context }.destroyed.set(true);
                }
                unsafe { PostQuitMessage(0); }
                0
            }
            _ => unsafe { DefWindowProcW(hwnd, message, wparam, lparam) },
        }
    }

    fn wide(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(Some(0)).collect()
    }

    pub(super) fn run(stop: Arc<FeedbackState>, ready: mpsc::Sender<()>) {
        let Some(window) = create(stop) else { return; };
        // First visibility and cancellation compete through the same state.
        if !window.state.stop.show_if_pending(|| unsafe {
            ShowWindow(window.hwnd, SW_SHOWNOACTIVATE);
            UpdateWindow(window.hwnd);
        }) {
            return;
        }
        let _ = ready.send(());
        let mut message = unsafe { zeroed() };
        loop {
            let result = unsafe { GetMessageW(&mut message, 0, 0, 0) };
            if result <= 0 {
                // Both WM_QUIT and GetMessage failure clean up the UI only.
                break;
            }
            unsafe {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }
    }

    fn create(stop: Arc<FeedbackState>) -> Option<Window> {
        let class = wide("BidKingStartupFeedbackV1");
        let title = wide(TITLE);
        let first = wide(STATUS);
        let second = wide(CLOSE_NOTE);
        let instance = unsafe { GetModuleHandleW(null()) };
        let font = unsafe { GetStockObject(DEFAULT_GUI_FONT) };
        if instance == 0 || font == 0 { return None; }
        let mut wc: WNDCLASSW = unsafe { zeroed() };
        wc.lpfnWndProc = Some(window_proc);
        wc.hInstance = instance;
        wc.lpszClassName = class.as_ptr();
        wc.hCursor = unsafe { LoadCursorW(0, IDC_ARROW) };
        // Resource 1 is the unchanged product cat icon; harnesses may omit it.
        wc.hIcon = unsafe { LoadIconW(instance, 1usize as *const u16) };
        wc.hbrBackground = (COLOR_BTNFACE + 1) as isize;
        if unsafe { RegisterClassW(&wc) } == 0 && unsafe { GetLastError() } != ERROR_CLASS_ALREADY_EXISTS {
            return None;
        }
        let dc = unsafe { GetDC(0) };
        if dc == 0 { return None; }
        let previous_font = unsafe { SelectObject(dc, font) };
        let mut first_size: SIZE = unsafe { zeroed() };
        let mut second_size: SIZE = unsafe { zeroed() };
        let measured = unsafe {
            GetTextExtentPoint32W(dc, first.as_ptr(), (first.len() - 1) as i32, &mut first_size) != 0
                && GetTextExtentPoint32W(dc, second.as_ptr(), (second.len() - 1) as i32, &mut second_size) != 0
        };
        unsafe { SelectObject(dc, previous_font); ReleaseDC(0, dc); }
        if !measured { return None; }
        let layout = text_layout(first_size.cx, second_size.cx, first_size.cy.max(second_size.cy));
        let style = WS_CAPTION | WS_SYSMENU;
        let mut rect = RECT { left: 0, top: 0, right: layout.width, bottom: layout.height };
        let mut area: RECT = unsafe { zeroed() };
        if unsafe { AdjustWindowRectEx(&mut rect, style, 0, WS_EX_APPWINDOW) } == 0
            || unsafe { SystemParametersInfoW(SPI_GETWORKAREA, 0, &mut area as *mut _ as _, 0) } == 0 {
            return None;
        }
        let width = rect.right - rect.left;
        let height = rect.bottom - rect.top;
        let state = Box::new(WindowState { stop, destroyed: Cell::new(false) });
        let hwnd = unsafe {
            CreateWindowExW(WS_EX_APPWINDOW, class.as_ptr(), title.as_ptr(), style,
                area.left + ((area.right - area.left - width) / 2).max(0),
                area.top + ((area.bottom - area.top - height) / 2).max(0),
                width, height, 0, 0, instance, &*state as *const _ as _)
        };
        if hwnd == 0 { return None; }
        let window = Window { hwnd, state };
        let static_class = wide("STATIC");
        for (text, y) in [(&first, layout.padding), (&second, layout.second_y)] {
            let control = unsafe {
                CreateWindowExW(0, static_class.as_ptr(), text.as_ptr(), WS_CHILD | WS_VISIBLE,
                    layout.padding, y, layout.width - 2 * layout.padding, layout.line_height,
                    hwnd, 0, instance, null())
            };
            if control == 0 { return None; }
            unsafe { SendMessageW(control, WM_SETFONT, font as usize, 1); }
        }
        if unsafe { SetTimer(hwnd, 1, 100, None) } == 0 { return None; }
        Some(window)
    }

    #[cfg(test)]
    mod delta01_native_tests {
        use super::*;

        #[test]
        fn delta01_hidden_title_keeps_default_creation_and_userdata() {
            // Same native create/callback, never shown and no product entry.
            let window = create(Default::default()).unwrap();
            let mut title = [0u16; 64];
            let length = unsafe { GetWindowTextW(window.hwnd, title.as_mut_ptr(), title.len() as i32) };
            let actual = String::from_utf16_lossy(&title[..length as usize]);
            println!("HIDDEN_TITLE={actual}");
            assert_eq!(actual, TITLE);
            assert_ne!(unsafe { GetWindowLongPtrW(window.hwnd, GWLP_USERDATA) }, 0);
            unsafe { SendMessageW(window.hwnd, WM_CLOSE, 0, 0); }
            assert_ne!(unsafe { IsWindow(window.hwnd) }, 0);
            assert_eq!(unsafe { IsWindowVisible(window.hwnd) }, 0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicBool, AtomicUsize};

    #[test]
    fn only_plain_launch_has_feedback() {
        for (args, expected) in [(vec!["exe"], true), (vec![], false), (vec!["exe", "--diagnostics"], false), (vec!["exe", "--repair"], false), (vec!["exe", "--restart"], false), (vec!["exe", ""], false)] {
            assert_eq!(is_plain_launch(args.into_iter().map(OsString::from)), expected);
        }
    }

    #[test]
    fn original_result_and_thread_are_preserved_without_ui() {
        let thread = thread::current().id();
        for value in [Ok(0), Ok(129), Err("original_failure")] {
            let mut calls = 0;
            let result = run_with_feedback(false, || { calls += 1; assert_eq!(thread::current().id(), thread); value });
            assert_eq!(result, value);
            assert_eq!(calls, 1);
        }
    }

    #[test]
    fn ui_creation_or_pump_failure_does_not_swallow_work() {
        for ready_before_exit in [false, true] {
            for value in [Ok(0), Ok(129), Err("original_failure")] {
                let mut calls = 0;
                let result = run_with_ui(
                    || { calls += 1; value },
                    move |_, ready| {
                        if ready_before_exit {
                            let _ = ready.send(());
                        }
                    },
                    Duration::from_millis(50),
                );
                assert_eq!(result, value);
                assert_eq!(calls, 1);
            }
        }
    }

    #[test]
    fn close_only_hides_while_delayed_work_continues_once() {
        let (closed_tx, closed_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let calls = Arc::new(AtomicUsize::new(0));
        let work_calls = Arc::clone(&calls);
        let owner = thread::spawn(move || run_with_ui(|| {
            work_calls.fetch_add(1, Ordering::SeqCst);
            release_rx.recv_timeout(Duration::from_secs(1)).unwrap();
            Ok::<_, &str>(17)
        }, move |stop, ready| {
            ready.send(()).unwrap();
            // Inert UI protocol witness, not a desktop WM_CLOSE test.
            closed_tx.send(()).unwrap();
            while !stop.is_stopped() { thread::sleep(Duration::from_millis(1)); }
        }, Duration::from_millis(50)));
        closed_rx.recv_timeout(Duration::from_secs(1)).unwrap();
        thread::sleep(Duration::from_millis(20));
        assert!(!owner.is_finished());
        release_tx.send(()).unwrap();
        assert_eq!(owner.join().unwrap(), Ok(17));
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn late_ui_after_prepare_timeout_sees_stop() {
        let stopped = Arc::new(AtomicBool::new(false));
        let observed = Arc::clone(&stopped);
        let result = run_with_ui(|| 42, move |stop, _| {
            thread::sleep(Duration::from_millis(30));
            observed.store(stop.is_stopped(), Ordering::Release);
        }, Duration::from_millis(1));
        assert_eq!(result, 42);
        assert!(stopped.load(Ordering::Acquire));
    }

    #[test]
    fn measured_layout_preserves_both_lines_at_standard_and_high_dpi() {
        for (first, second, height) in [(250, 200, 16), (438, 350, 28)] {
            let layout = text_layout(first, second, height);
            assert!(layout.width - 2 * layout.padding >= first.max(second));
            assert!(layout.line_height >= height);
            assert!(layout.second_y >= layout.padding + layout.line_height);
            assert!(layout.height >= layout.second_y + height + layout.padding);
        }
    }

    #[test]
    fn delta01_cancel_wins_before_first_show() {
        for value in [Ok(0), Ok(129), Err("original_failure")] {
            let (entered_tx, entered_rx) = mpsc::channel();
            let (release_tx, release_rx) = mpsc::channel();
            let (observed_tx, observed_rx) = mpsc::channel();
            let mut calls = 0;
            let result = run_with_ui(|| {
                calls += 1;
                entered_rx.recv_timeout(Duration::from_secs(1)).unwrap();
                release_tx.send(()).unwrap();
                value
            }, move |state, _| {
                entered_tx.send(()).unwrap();
                release_rx.recv_timeout(Duration::from_secs(1)).unwrap();
                let mut shows = 0;
                let committed = state.show_if_pending(|| { shows += 1; });
                observed_tx.send((state.is_stopped(), committed, shows)).unwrap();
            }, Duration::from_millis(20));
            assert_eq!(observed_rx.recv().unwrap(), (true, false, 0));
            assert_eq!(result, value);
            assert_eq!(calls, 1);
            println!("CANCEL_FIRST shows=0 calls={calls} result={result:?}");
        }
    }

    #[test]
    fn delta01_show_committed_before_stop_finishes_once() {
        for value in [Ok(0), Ok(129), Err("original_failure")] {
            let (entered_tx, entered_rx) = mpsc::channel();
            let (release_tx, release_rx) = mpsc::channel();
            let (observed_tx, observed_rx) = mpsc::channel();
            let mut calls = 0;
            let started = std::time::Instant::now();
            let result = run_with_ui(|| {
                calls += 1;
                entered_rx.recv_timeout(Duration::from_secs(1)).unwrap();
                release_tx.send(()).unwrap();
                value
            }, move |state, _| {
                let mut shows = 0;
                let committed = state.show_if_pending(|| {
                    entered_tx.send(()).unwrap();
                    release_rx.recv_timeout(Duration::from_secs(1)).unwrap();
                    shows += 1;
                });
                observed_tx.send((state.is_stopped(), committed, shows)).unwrap();
            }, Duration::from_millis(20));
            assert_eq!(observed_rx.recv().unwrap(), (true, true, 1));
            assert_eq!(result, value);
            assert_eq!(calls, 1);
            assert!(started.elapsed() < Duration::from_secs(1));
            println!("SHOW_COMMITTED_FIRST shows=1 cleanup_finished=true calls={calls} result={result:?}");
        }
    }
}
