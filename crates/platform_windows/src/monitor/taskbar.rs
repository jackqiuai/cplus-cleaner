//! Application-owned non-activating surface in a verified taskbar gap.
use super::Sample;
use crate::wide;
use std::{
    mem, ptr,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread,
    time::Duration,
};
use windows::Win32::{System::Com::*, UI::Accessibility::*};
use windows_sys::Win32::{
    Foundation::*,
    Graphics::Gdi::*,
    System::LibraryLoader::GetModuleHandleW,
    UI::{HiDpi::*, Input::KeyboardAndMouse::*, WindowsAndMessaging::*},
};

#[derive(Clone, Debug)]
pub enum Action {
    Open(usize, i32, i32),
    Menu(i32, i32),
}
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Status {
    #[default]
    Disabled,
    Starting,
    Visible,
    NoSpace,
    Unavailable,
}
pub fn cursor_position() -> (i32, i32) {
    let mut point = POINT::default();
    unsafe {
        GetCursorPos(&mut point);
    }
    (point.x, point.y)
}
#[derive(Default)]
pub struct Model {
    pub sample: Sample,
    pub status: Status,
}
pub struct Panel {
    pub model: Arc<Mutex<Model>>,
    pub events: mpsc::Receiver<Action>,
    enabled: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
}
impl Default for Panel {
    fn default() -> Self {
        let model = Arc::new(Mutex::new(Model::default()));
        let (tx, events) = mpsc::channel();
        let worker = model.clone();
        let enabled = Arc::new(AtomicBool::new(false));
        let stop = Arc::new(AtomicBool::new(false));
        let flags = (enabled.clone(), stop.clone());
        thread::spawn(move || unsafe {
            run(worker, tx, flags.0, flags.1);
        });
        Self {
            model,
            events,
            enabled,
            stop,
        }
    }
}
impl Drop for Panel {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
    }
}
impl Panel {
    pub fn set_enabled(&self, enabled: bool) {
        self.enabled.store(enabled, Ordering::Release);
    }
}
struct WindowData {
    model: Arc<Mutex<Model>>,
    tx: mpsc::Sender<Action>,
    font: HFONT,
    hovered: i32,
}
const PANEL_WIDTH: i32 = 216;
const COLUMN_EDGES: [i32; 5] = [0, 40, 80, 120, PANEL_WIDTH];
fn column_at(x: i32, width: i32) -> usize {
    (0..3)
        .find(|&i| x < COLUMN_EDGES[i + 1] * width / PANEL_WIDTH)
        .unwrap_or(3)
}
fn usage_color(value: Option<f64>) -> u32 {
    match value {
        Some(v) if v >= 90.0 => rgb(239, 85, 85),
        Some(v) if v >= 70.0 => rgb(229, 139, 36),
        Some(_) => rgb(0, 169, 145),
        None => rgb(120, 144, 156),
    }
}
unsafe extern "system" fn procedure(hwnd: HWND, message: u32, w: WPARAM, l: LPARAM) -> LRESULT {
    unsafe {
        if message == WM_NCCREATE {
            SetWindowLongPtrW(
                hwnd,
                GWLP_USERDATA,
                (*(l as *const CREATESTRUCTW)).lpCreateParams as isize,
            );
        }
        let data = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut WindowData;
        if data.is_null() {
            return DefWindowProcW(hwnd, message, w, l);
        }
        let data = &mut *data;
        match message {
            WM_PAINT => {
                let mut paint: PAINTSTRUCT = mem::zeroed();
                let dc = BeginPaint(hwnd, &mut paint);
                let mut bounds = RECT::default();
                GetClientRect(hwnd, &mut bounds);
                let brush = CreateSolidBrush(rgb(244, 246, 252));
                FillRect(dc, &bounds, brush);
                DeleteObject(brush);
                let old = SelectObject(dc, data.font);
                SetBkMode(dc, TRANSPARENT as i32);
                let sample = data
                    .model
                    .try_lock()
                    .map(|m| m.sample.clone())
                    .unwrap_or_default();
                let columns = [
                    ("CPU".into(), percentage(sample.cpu)),
                    ("MEM".into(), percentage(sample.memory_percent())),
                    ("DISK".into(), percentage(sample.disk_percent())),
                    (
                        speed(sample.network.as_ref().and_then(|n| n.upload)),
                        speed(sample.network.as_ref().and_then(|n| n.download)),
                    ),
                ];
                for (i, (top, bottom)) in columns.iter().enumerate() {
                    let left = bounds.right * COLUMN_EDGES[i] / PANEL_WIDTH;
                    let right = bounds.right * COLUMN_EDGES[i + 1] / PANEL_WIDTH;
                    if data.hovered == i as i32 {
                        let brush = CreateSolidBrush(rgb(226, 247, 238));
                        FillRect(
                            dc,
                            &RECT {
                                left,
                                right,
                                top: 0,
                                bottom: bounds.bottom,
                            },
                            brush,
                        );
                        DeleteObject(brush);
                    }
                    if i == 3 {
                        let inset = (bounds.right * 4 / PANEL_WIDTH).max(1);
                        let arrow_width = bounds.right * 14 / PANEL_WIDTH;
                        let brush = CreateSolidBrush(rgb(220, 228, 233));
                        FillRect(
                            dc,
                            &RECT {
                                left,
                                right: left + 1,
                                top: 4,
                                bottom: bounds.bottom - 4,
                            },
                            brush,
                        );
                        DeleteObject(brush);
                        for (row, value) in [top, bottom].iter().enumerate() {
                            let mut rect = RECT {
                                left: left + inset,
                                right: left + inset + arrow_width,
                                top: row as i32 * bounds.bottom / 2,
                                bottom: (row as i32 + 1) * bounds.bottom / 2,
                            };
                            SetTextColor(
                                dc,
                                if row == 0 {
                                    rgb(246, 90, 112)
                                } else {
                                    rgb(57, 132, 234)
                                },
                            );
                            DrawTextW(
                                dc,
                                wide(if row == 0 { "↑" } else { "↓" }).as_ptr(),
                                -1,
                                &mut rect,
                                DT_CENTER | DT_VCENTER | DT_SINGLELINE,
                            );
                            rect.left = rect.right;
                            rect.right = right - inset;
                            SetTextColor(dc, rgb(32, 54, 66));
                            DrawTextW(
                                dc,
                                wide(value).as_ptr(),
                                -1,
                                &mut rect,
                                DT_LEFT | DT_VCENTER | DT_SINGLELINE,
                            );
                        }
                        continue;
                    }
                    SetTextColor(dc, rgb(32, 54, 66));
                    let mut rect = RECT {
                        left,
                        right,
                        top: 0,
                        bottom: bounds.bottom / 2,
                    };
                    DrawTextW(
                        dc,
                        wide(top).as_ptr(),
                        -1,
                        &mut rect,
                        DT_CENTER | DT_VCENTER | DT_SINGLELINE,
                    );
                    SetTextColor(
                        dc,
                        usage_color(
                            [sample.cpu, sample.memory_percent(), sample.disk_percent()][i],
                        ),
                    );
                    let mut rect = RECT {
                        left,
                        right,
                        top: bounds.bottom / 2,
                        bottom: bounds.bottom,
                    };
                    DrawTextW(
                        dc,
                        wide(bottom).as_ptr(),
                        -1,
                        &mut rect,
                        DT_CENTER | DT_VCENTER | DT_SINGLELINE,
                    );
                }
                SelectObject(dc, old);
                EndPaint(hwnd, &paint);
                0
            }
            WM_MOUSEMOVE => {
                let mut rect = RECT::default();
                GetClientRect(hwnd, &mut rect);
                let index = column_at(l as u16 as i32, rect.right.max(1)) as i32;
                if data.hovered != index {
                    data.hovered = index;
                    InvalidateRect(hwnd, ptr::null(), 0);
                }
                let mut track = TRACKMOUSEEVENT {
                    cbSize: mem::size_of::<TRACKMOUSEEVENT>() as u32,
                    dwFlags: TME_LEAVE,
                    hwndTrack: hwnd,
                    dwHoverTime: 0,
                };
                TrackMouseEvent(&mut track);
                0
            }
            0x02A3 => {
                data.hovered = -1;
                InvalidateRect(hwnd, ptr::null(), 0);
                0
            }
            WM_LBUTTONUP | WM_RBUTTONUP => {
                let mut p = POINT::default();
                GetCursorPos(&mut p);
                let mut rect = RECT::default();
                GetClientRect(hwnd, &mut rect);
                let tab = column_at(l as u16 as i32, rect.right.max(1)) + 1;
                let _ = data.tx.send(if message == WM_LBUTTONUP {
                    Action::Open(tab, p.x, p.y)
                } else {
                    Action::Menu(p.x, p.y)
                });
                0
            }
            WM_ERASEBKGND => 1,
            _ => DefWindowProcW(hwnd, message, w, l),
        }
    }
}
const fn rgb(r: u32, g: u32, b: u32) -> u32 {
    r | g << 8 | b << 16
}
pub fn percentage(value: Option<f64>) -> String {
    value
        .map(|v| format!("{v:.0}%"))
        .unwrap_or_else(|| "--".into())
}
pub fn speed(value: Option<f64>) -> String {
    match value {
        Some(v) if v >= 1024.0 * 1024.0 => format!("{:.1} MB/s", v / 1024.0 / 1024.0),
        Some(v) if v >= 1024.0 => format!("{:.0} KB/s", v / 1024.0),
        Some(v) => format!("{v:.0} B/s"),
        None => "--".into(),
    }
}
/// Find a gap using actual interactive/control bounds, not the task-list container.
fn gap(width: i32, left: i32, right: i32, occupied: &mut [(i32, i32)]) -> Option<i32> {
    occupied.sort_unstable();
    let mut end = right;
    for &(a, b) in occupied.iter().rev() {
        if b <= left || a >= right {
            continue;
        }
        if end - b >= width {
            return Some(end - width);
        }
        end = end.min(a - 6);
    }
    (end - left >= width).then_some(end - width)
}
fn intersects(a: RECT, b: RECT) -> bool {
    a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom
}
struct Peers {
    explorer: u32,
    own: u32,
    bar: RECT,
    intervals: Vec<(i32, i32)>,
    top_level: bool,
}
unsafe extern "system" fn record_peer(hwnd: HWND, raw: LPARAM) -> i32 {
    unsafe {
        let peers = &mut *(raw as *mut Peers);
        let mut pid = 0;
        GetWindowThreadProcessId(hwnd, &mut pid);
        if pid == 0 || pid == peers.own || pid == peers.explorer || IsWindowVisible(hwnd) == 0 {
            return 1;
        }
        let mut rect = RECT::default();
        if GetWindowRect(hwnd, &mut rect) == 0 || !intersects(rect, peers.bar) {
            return 1;
        }
        if peers.top_level {
            // Include detached monitors without treating the desktop as occupied.
            let style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE) as u32;
            if style & WS_EX_TOOLWINDOW == 0
                || rect.bottom - rect.top > (peers.bar.bottom - peers.bar.top) * 2
            {
                return 1;
            }
        }
        peers.intervals.push((rect.left - 6, rect.right + 6));
        1
    }
}
unsafe fn peer_bounds(shell: HWND, bar: RECT) -> Vec<(i32, i32)> {
    unsafe {
        let mut explorer = 0;
        GetWindowThreadProcessId(shell, &mut explorer);
        let mut peers = Peers {
            explorer,
            own: std::process::id(),
            bar,
            intervals: vec![],
            top_level: false,
        };
        EnumChildWindows(
            shell,
            Some(record_peer),
            (&mut peers as *mut Peers) as LPARAM,
        );
        peers.top_level = true;
        EnumWindows(Some(record_peer), (&mut peers as *mut Peers) as LPARAM);
        peers.intervals
    }
}
unsafe fn shell_exposed(shell: HWND, popup: HWND, bar: RECT) -> bool {
    unsafe {
        if IsWindowVisible(shell) == 0 {
            return false;
        }
        let monitor = MonitorFromWindow(shell, MONITOR_DEFAULTTONEAREST);
        let mut info = MONITORINFO {
            cbSize: mem::size_of::<MONITORINFO>() as u32,
            ..mem::zeroed()
        };
        if GetMonitorInfoW(monitor, &mut info) == 0 {
            return false;
        }
        // An auto-hidden taskbar can retain WS_VISIBLE while outside the display.
        if bar.bottom.min(info.rcMonitor.bottom) - bar.top.max(info.rcMonitor.top)
            < bar.bottom - bar.top - 2
        {
            return false;
        }
        let foreground = GetForegroundWindow();
        if !foreground.is_null() && foreground != shell && foreground != popup {
            let mut class = [0u16; 80];
            let n = GetClassNameW(foreground, class.as_mut_ptr(), class.len() as i32);
            let desktop = class[..n.max(0) as usize] == wide("Progman")[..7]
                || class[..n.max(0) as usize] == wide("WorkerW")[..7];
            let mut front = RECT::default();
            if !desktop
                && GetWindowRect(foreground, &mut front) != 0
                && front.left <= info.rcMonitor.left
                && front.top <= info.rcMonitor.top
                && front.right >= info.rcMonitor.right
                && front.bottom >= info.rcMonitor.bottom
            {
                return false;
            }
        }
        true
    }
}
unsafe fn placement(
    automation: &IUIAutomation,
    shell: HWND,
    width: i32,
) -> Option<(i32, i32, i32)> {
    unsafe {
        let mut bounds = RECT::default();
        if GetWindowRect(shell, &mut bounds) == 0
            || bounds.bottom - bounds.top > 150
            || bounds.right - bounds.left < 600
        {
            return None;
        }
        let root = automation
            .ElementFromHandle(windows::Win32::Foundation::HWND(shell))
            .ok()?;
        let condition = automation.CreateTrueCondition().ok()?;
        let nodes = root.FindAll(TreeScope_Descendants, &condition).ok()?;
        let count = nodes.Length().ok()?;
        if count > 2048 {
            return None;
        }
        let mut occupied = peer_bounds(shell, bounds);
        let mut explorer = 0;
        GetWindowThreadProcessId(shell, &mut explorer);
        let mut shell_controls = 0;
        for i in 0..count {
            let Ok(node) = nodes.GetElement(i) else {
                continue;
            };
            if node.CurrentProcessId().ok()? != explorer as i32 {
                continue;
            }
            let kind = node.CurrentControlType().ok()?;
            if ![
                UIA_ButtonControlTypeId,
                UIA_EditControlTypeId,
                UIA_TextControlTypeId,
                UIA_MenuItemControlTypeId,
            ]
            .contains(&kind)
            {
                continue;
            }
            if node.CurrentIsOffscreen().ok()?.as_bool() {
                continue;
            }
            let r = node.CurrentBoundingRectangle().ok()?;
            if r.right > r.left && r.bottom > bounds.top && r.top < bounds.bottom {
                occupied.push((r.left - 5, r.right + 5));
                shell_controls += 1;
            }
        }
        // A missing accessibility tree is not proof that the taskbar is empty.
        if shell_controls == 0 {
            return None;
        }
        let x = gap(width, bounds.left + 8, bounds.right - 8, &mut occupied)?;
        let height = ((36 * GetDpiForWindow(shell).max(96) / 96) as i32)
            .min(bounds.bottom - bounds.top - 4)
            .max(24);
        Some((
            x - bounds.left,
            (bounds.bottom - bounds.top - height) / 2,
            height,
        ))
    }
}
#[derive(Clone, Copy)]
struct Layout {
    shell: usize,
    dpi: u32,
    bounds: Option<(i32, i32, i32)>,
    at: std::time::Instant,
    bar: RECT,
}
unsafe fn pump_messages() {
    unsafe {
        let mut message: MSG = mem::zeroed();
        while PeekMessageW(&mut message, ptr::null_mut(), 0, 0, PM_REMOVE) != 0 {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
    }
}
unsafe fn run(
    model: Arc<Mutex<Model>>,
    tx: mpsc::Sender<Action>,
    enabled_flag: Arc<AtomicBool>,
    stop_flag: Arc<AtomicBool>,
) {
    // UI Automation may synchronously query our HWND. Keep the HWND's message
    // pump independent of that query, and reject stale geometry instead of blocking input.
    let geometry = Arc::new(Mutex::new(None::<Layout>));
    let active = enabled_flag.clone();
    let stopping = stop_flag.clone();
    let output = geometry.clone();
    thread::spawn(move || unsafe {
        SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        let mut initialized = false;
        let mut automation: Option<IUIAutomation> = None;
        loop {
            let (stop, enabled) = (
                stopping.load(Ordering::Acquire),
                active.load(Ordering::Acquire),
            );
            if stop {
                break;
            }
            if enabled && !initialized {
                initialized = CoInitializeEx(None, COINIT_MULTITHREADED).is_ok();
            }
            if enabled && initialized && automation.is_none() {
                automation = CoCreateInstance(&CUIAutomation, None, CLSCTX_INPROC_SERVER).ok();
            }
            let shell = FindWindowW(wide("Shell_TrayWnd").as_ptr(), ptr::null());
            let layout = if enabled
                && !shell.is_null()
                && let Some(a) = &automation
            {
                let dpi = GetDpiForWindow(shell).max(96);
                let mut bar = RECT::default();
                GetWindowRect(shell, &mut bar);
                Some(Layout {
                    shell: shell as usize,
                    dpi,
                    bounds: placement(a, shell, PANEL_WIDTH * dpi as i32 / 96),
                    at: std::time::Instant::now(),
                    bar,
                })
            } else {
                None
            };
            *output.lock().unwrap_or_else(|e| e.into_inner()) = layout;
            for _ in 0..10 {
                if stopping.load(Ordering::Acquire) {
                    break;
                }
                thread::sleep(Duration::from_millis(100));
            }
        }
        drop(automation);
        if initialized {
            CoUninitialize();
        }
    });
    unsafe {
        SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        let instance = GetModuleHandleW(ptr::null());
        let class = wide("CPlusCleaner.Monitor.v1");
        let definition = WNDCLASSW {
            hInstance: instance,
            lpszClassName: class.as_ptr(),
            lpfnWndProc: Some(procedure),
            hCursor: LoadCursorW(ptr::null_mut(), IDC_ARROW),
            ..mem::zeroed()
        };
        RegisterClassW(&definition);
        let mut data = WindowData {
            model: model.clone(),
            tx,
            font: ptr::null_mut(),
            hovered: -1,
        };
        let mut hwnd: HWND = ptr::null_mut();
        let mut previous_shell: HWND = ptr::null_mut();
        let mut dpi = 0;
        let mut painted = std::time::Instant::now();
        let mut enabled_at = None;
        loop {
            let (stop, enabled) = (
                stop_flag.load(Ordering::Acquire),
                enabled_flag.load(Ordering::Acquire),
            );
            if stop {
                break;
            }
            if enabled {
                enabled_at.get_or_insert_with(std::time::Instant::now);
            } else {
                enabled_at = None;
            }
            let shell = FindWindowW(wide("Shell_TrayWnd").as_ptr(), ptr::null());
            if shell != previous_shell || (!hwnd.is_null() && IsWindow(hwnd) == 0) {
                if !hwnd.is_null() {
                    DestroyWindow(hwnd);
                }
                hwnd = ptr::null_mut();
                previous_shell = shell;
            }
            let layout = *geometry.lock().unwrap_or_else(|e| e.into_inner());
            let layout = layout
                .filter(|l| l.shell == shell as usize && l.at.elapsed() < Duration::from_secs(3));
            let mut status = if enabled {
                if enabled_at.is_some_and(|at| at.elapsed() < Duration::from_secs(3)) {
                    Status::Starting
                } else {
                    Status::Unavailable
                }
            } else {
                Status::Disabled
            };
            let mut current = RECT::default();
            let mut placed = false;
            GetWindowRect(shell, &mut current);
            let layout = layout.filter(|l| {
                l.bar.left == current.left
                    && l.bar.top == current.top
                    && l.bar.right == current.right
                    && l.bar.bottom == current.bottom
            });
            if enabled
                && shell_exposed(shell, hwnd, current)
                && let Some(layout) = layout
            {
                if let Some((x, y, height)) = layout.bounds {
                    let width = PANEL_WIDTH * layout.dpi as i32 / 96;
                    let (x, y) = (layout.bar.left + x, layout.bar.top + y);
                    let target = RECT {
                        left: x,
                        top: y,
                        right: x + width,
                        bottom: y + height,
                    };
                    let overlaps = peer_bounds(shell, layout.bar)
                        .iter()
                        .any(|(a, b)| *a < target.right && target.left < *b);
                    if overlaps {
                        if !hwnd.is_null() {
                            ShowWindow(hwnd, SW_HIDE);
                        }
                        if let Ok(mut m) = model.try_lock() {
                            m.status = Status::NoSpace;
                        }
                        pump_messages();
                        thread::sleep(Duration::from_millis(50));
                        continue;
                    }
                    if dpi != layout.dpi {
                        if !data.font.is_null() {
                            DeleteObject(data.font);
                        }
                        data.font = CreateFontW(
                            -((12 * layout.dpi / 96) as i32),
                            0,
                            0,
                            0,
                            400,
                            0,
                            0,
                            0,
                            DEFAULT_CHARSET as u32,
                            0,
                            0,
                            CLEARTYPE_QUALITY as u32,
                            0,
                            wide("Segoe UI").as_ptr(),
                        );
                        dpi = layout.dpi;
                    }
                    if hwnd.is_null() {
                        hwnd = CreateWindowExW(
                            WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST,
                            class.as_ptr(),
                            wide("CPlus Cleaner taskbar status").as_ptr(),
                            WS_POPUP,
                            x,
                            y,
                            width,
                            height,
                            ptr::null_mut(),
                            ptr::null_mut(),
                            instance,
                            (&mut data as *mut WindowData).cast(),
                        );
                    }
                    if !hwnd.is_null() {
                        let mut actual = RECT::default();
                        GetWindowRect(hwnd, &mut actual);
                        let changed = actual.left != target.left
                            || actual.top != target.top
                            || actual.right != target.right
                            || actual.bottom != target.bottom
                            || IsWindowVisible(hwnd) == 0;
                        if changed {
                            let diameter = (8 * layout.dpi / 96) as i32;
                            let region =
                                CreateRoundRectRgn(0, 0, width + 1, height + 1, diameter, diameter);
                            if !region.is_null() && SetWindowRgn(hwnd, region, 1) == 0 {
                                DeleteObject(region);
                            }
                        }
                        let covering = WindowFromPoint(POINT {
                            x: target.left + width / 2,
                            y: target.top + height / 2,
                        });
                        // Explorer can raise its taskbar above an already-visible topmost panel.
                        let covered_by_shell = GetAncestor(covering, GA_ROOT) == shell;
                        // Restore only when occluded by the taskbar, not on every timer tick.
                        let shown = if changed || covered_by_shell {
                            SetWindowPos(
                                hwnd,
                                HWND_TOPMOST,
                                x,
                                y,
                                width,
                                height,
                                SWP_NOACTIVATE | SWP_SHOWWINDOW,
                            )
                        } else {
                            1
                        };
                        placed = true;
                        if IsWindowVisible(hwnd) == 0 {
                            ShowWindow(hwnd, SW_SHOWNOACTIVATE);
                        }
                        if painted.elapsed() >= Duration::from_secs(1) {
                            InvalidateRect(hwnd, ptr::null(), 0);
                            painted = std::time::Instant::now();
                        }
                        status = if shown != 0
                            && IsWindowVisible(hwnd) != 0
                            && WindowFromPoint(POINT {
                                x: target.left + width / 2,
                                y: target.top + height / 2,
                            }) == hwnd
                        {
                            Status::Visible
                        } else {
                            Status::Unavailable
                        };
                    }
                } else {
                    status = Status::NoSpace;
                }
            }
            if !placed && !hwnd.is_null() && IsWindowVisible(hwnd) != 0 {
                ShowWindow(hwnd, SW_HIDE);
            }
            if let Ok(mut m) = model.try_lock() {
                m.status = status;
            }
            pump_messages();
            thread::sleep(Duration::from_millis(50));
        }
        if !hwnd.is_null() {
            DestroyWindow(hwnd);
        }
        if !data.font.is_null() {
            DeleteObject(data.font);
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn compact_columns_keep_their_click_targets_and_usage_colors() {
        for scale in [1, 2] {
            for (x, expected) in [(0, 0), (39, 0), (40, 1), (80, 2), (120, 3), (215, 3)] {
                assert_eq!(column_at(x * scale, PANEL_WIDTH * scale), expected);
            }
        }
        assert_eq!(usage_color(Some(25.0)), rgb(0, 169, 145));
        assert_eq!(usage_color(Some(76.0)), rgb(229, 139, 36));
        assert_eq!(usage_color(Some(95.0)), rgb(239, 85, 85));
        assert_eq!(usage_color(None), rgb(120, 144, 156));
    }
    #[test]
    fn gaps_do_not_overlap_existing_buttons() {
        assert_eq!(
            gap(200, 0, 1000, &mut [(0, 100), (350, 600), (850, 1000)]),
            Some(644)
        );
        assert_eq!(
            gap(300, 0, 1000, &mut [(0, 100), (350, 600), (850, 1000)]),
            None
        );
        assert_eq!(speed(None), "--");
        assert_eq!(percentage(Some(23.4)), "23%");
        // Opaque foreign monitor bounds must be treated just like shell buttons.
        assert_eq!(
            gap(
                286,
                0,
                1200,
                &mut [(0, 100), (600, 800), (950, 1200), (100, 400)]
            ),
            None
        );
        assert_eq!(
            gap(286, 0, 1600, &mut [(0, 100), (600, 800), (950, 1200)]),
            Some(1314)
        );
    }
    #[test]
    fn native_switch_and_shutdown_do_not_wait_for_a_paint_lock() {
        let panel = Panel::default();
        let model = panel.model.clone();
        let _held = model.lock().unwrap();
        let start = std::time::Instant::now();
        panel.set_enabled(true);
        panel.set_enabled(false);
        drop(panel);
        assert!(start.elapsed() < Duration::from_millis(100));
    }
    #[test]
    #[ignore = "Interactive desktop check: briefly shows a panel and requires a free taskbar gap"]
    fn native_panel_releases_its_window_when_disabled() {
        let panel = Panel::default();
        panel.set_enabled(true);
        std::thread::sleep(Duration::from_secs(4));
        let status = panel.model.lock().unwrap().status;
        eprintln!("Native taskbar status: {status:?}");
        assert_eq!(
            status,
            Status::Visible,
            "No usable taskbar gap or shell provider unavailable"
        );
        unsafe {
            let old = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
            let hwnd = FindWindowW(wide("CPlusCleaner.Monitor.v1").as_ptr(), ptr::null());
            assert!(!hwnd.is_null());
            assert!(
                GetParent(hwnd).is_null(),
                "The monitor must not join Explorer's input queue"
            );
            let style = GetWindowLongPtrW(hwnd, GWL_STYLE) as u32;
            assert_eq!(style & WS_CHILD, 0);
            assert_ne!(style & WS_POPUP, 0);
            let shell = FindWindowW(wide("Shell_TrayWnd").as_ptr(), ptr::null());
            let (mut bar, mut actual) = (RECT::default(), RECT::default());
            GetWindowRect(shell, &mut bar);
            GetWindowRect(hwnd, &mut actual);
            let peers = peer_bounds(shell, bar);
            eprintln!("Foreign taskbar rectangles avoided: {}", peers.len());
            assert!(
                peers
                    .iter()
                    .all(|(left, right)| actual.right <= *left || actual.left >= *right)
            );
            for _ in 0..5 {
                std::thread::sleep(Duration::from_secs(1));
                assert_ne!(GetWindowRect(hwnd, &mut actual), 0);
                let point = POINT {
                    x: actual.left + 2,
                    y: actual.top + 2,
                };
                assert_eq!(
                    WindowFromPoint(point),
                    hwnd,
                    "The panel is marked visible but another window covers its pixels"
                );
                let dc = GetDC(ptr::null_mut());
                let color = GetPixel(dc, point.x, point.y);
                ReleaseDC(ptr::null_mut(), dc);
                assert!(color == rgb(244, 246, 252) || color == rgb(226, 247, 238));
            }
            SetThreadDpiAwarenessContext(old);
        }
        panel.set_enabled(false);
        std::thread::sleep(Duration::from_secs(2));
        assert_eq!(panel.model.lock().unwrap().status, Status::Disabled);
    }
}
