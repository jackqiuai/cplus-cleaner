use crate::{from_wide, processes, wide};
use serde::{Deserialize, Serialize};
use std::{io, mem, ptr};
use windows_sys::Win32::{Foundation::*, Graphics::Gdi::*, UI::WindowsAndMessaging::*};

/// Place an application-owned popup beside the tray without crossing the monitor work area.
pub fn show_tray_menu(raw: u64, x: i32, y: i32) {
    unsafe {
        let hwnd = raw as HWND;
        let monitor = MonitorFromPoint(POINT { x, y }, MONITOR_DEFAULTTONEAREST);
        let mut info = MONITORINFO {
            cbSize: mem::size_of::<MONITORINFO>() as u32,
            ..mem::zeroed()
        };
        let mut rect = RECT::default();
        if GetMonitorInfoW(monitor, &mut info) == 0 || GetWindowRect(hwnd, &mut rect) == 0 {
            ShowWindow(hwnd, SW_SHOW);
        } else {
            let bounds = info.rcWork;
            let px = x.saturating_sub(rect.right - rect.left).clamp(
                bounds.left,
                (bounds.right - (rect.right - rect.left)).max(bounds.left),
            );
            let py = y.saturating_sub(rect.bottom - rect.top).clamp(
                bounds.top,
                (bounds.bottom - (rect.bottom - rect.top)).max(bounds.top),
            );
            SetWindowPos(
                hwnd,
                HWND_TOPMOST,
                px,
                py,
                0,
                0,
                SWP_NOSIZE | SWP_SHOWWINDOW,
            );
        }
        SetForegroundWindow(hwnd);
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Window {
    pub hwnd: u64,
    pub pid: u32,
    pub created: u64,
    pub image: String,
    pub title: String,
    pub class: String,
    pub width: i32,
    pub height: i32,
    pub styles: u32,
    pub exstyles: u32,
    pub pos: i32,
    pub scale: u32,
    pub rect: [i32; 4],
}
fn describe(hwnd: HWND) -> Option<Window> {
    unsafe {
        if IsWindow(hwnd) == 0 || IsWindowVisible(hwnd) == 0 || GetAncestor(hwnd, GA_ROOT) != hwnd {
            return None;
        }
        let mut pid = 0;
        GetWindowThreadProcessId(hwnd, &mut pid);
        if pid == std::process::id() {
            return None;
        }
        let mut class = [0u16; 512];
        if GetClassNameW(hwnd, class.as_mut_ptr(), class.len() as i32) == 0 {
            return None;
        }
        let class = from_wide(&class);
        if [
            "Progman",
            "WorkerW",
            "Shell_TrayWnd",
            "Shell_SecondaryTrayWnd",
            "#32769",
        ]
        .iter()
        .any(|n| class.eq_ignore_ascii_case(n))
        {
            return None;
        }
        let len = GetWindowTextLengthW(hwnd).clamp(0, 32767) as usize;
        let mut title = vec![0u16; len + 1];
        GetWindowTextW(hwnd, title.as_mut_ptr(), title.len() as i32);
        let mut rect: RECT = mem::zeroed();
        if GetWindowRect(hwnd, &mut rect) == 0 {
            return None;
        }
        let p = processes::describe(pid)?;
        let monitor = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
        let mut info: MONITORINFO = mem::zeroed();
        info.cbSize = mem::size_of_val(&info) as u32;
        let pos = if GetMonitorInfoW(monitor, &mut info) != 0 {
            position(
                [
                    info.rcMonitor.left,
                    info.rcMonitor.top,
                    info.rcMonitor.right,
                    info.rcMonitor.bottom,
                ],
                [rect.left, rect.top, rect.right, rect.bottom],
            )
        } else {
            -1
        };
        let mut pid_again = 0;
        GetWindowThreadProcessId(hwnd, &mut pid_again);
        if pid_again != pid || IsWindowVisible(hwnd) == 0 {
            return None;
        }
        let dpi = windows_sys::Win32::UI::HiDpi::GetDpiForWindow(hwnd);
        Some(Window {
            hwnd: hwnd as u64,
            pid,
            created: p.created,
            image: p.path,
            title: from_wide(&title),
            class,
            width: rect.right - rect.left,
            height: rect.bottom - rect.top,
            styles: GetWindowLongW(hwnd, GWL_STYLE) as u32,
            exstyles: GetWindowLongW(hwnd, GWL_EXSTYLE) as u32,
            pos,
            scale: if dpi > 0 { dpi * 100 / 96 } else { 100 },
            rect: [rect.left, rect.top, rect.right, rect.bottom],
        })
    }
}
pub fn enumerate() -> Vec<Window> {
    unsafe extern "system" fn callback(hwnd: HWND, data: LPARAM) -> i32 {
        if let Some(window) = describe(hwnd) {
            unsafe { &mut *(data as *mut Vec<Window>) }.push(window);
        }
        1
    }
    let mut result = vec![];
    unsafe {
        let old = windows_sys::Win32::UI::HiDpi::SetThreadDpiAwarenessContext(-4isize as _);
        EnumWindows(Some(callback), (&mut result as *mut Vec<Window>) as isize);
        if !old.is_null() {
            windows_sys::Win32::UI::HiDpi::SetThreadDpiAwarenessContext(old);
        }
    }
    result
}
pub fn position(m: [i32; 4], r: [i32; 4]) -> i32 {
    let (width, height) = (m[2] - m[0], m[3] - m[1]);
    if width < 3 || height < 2 {
        return -1;
    }
    let (x, y) = (r[0] + (r[2] - r[0]) / 2, r[1] + (r[3] - r[1]) / 2);
    let column = (x - m[0]) / (width / 3);
    if column == 1 {
        if (width - (r[2] - r[0])).abs() < 20 && (height - (r[3] - r[1])).abs() < 20 {
            0
        } else {
            3
        }
    } else if column == 0 {
        if y < m[1] + height / 2 { 1 } else { 2 }
    } else if column == 2 {
        if y < m[1] + height / 2 { 4 } else { 5 }
    } else {
        -1
    }
}
pub fn same(expected: &Window) -> bool {
    describe(expected.hwnd as HWND).is_some_and(|w| {
        w.pid == expected.pid
            && w.created == expected.created
            && w.image.eq_ignore_ascii_case(&expected.image)
            && w.class == expected.class
            && w.title == expected.title
            && w.rect == expected.rect
    })
}
pub fn act(expected: &Window, action: &str) -> io::Result<bool> {
    if !same(expected) {
        return Err(io::Error::other("Window identity changed"));
    }
    let hwnd = expected.hwnd as HWND;
    match action {
        "close" | "close_hide" => {
            let mut result = 0;
            unsafe {
                SendMessageTimeoutW(
                    hwnd,
                    WM_CLOSE,
                    0,
                    0,
                    SMTO_ABORTIFHUNG | SMTO_BLOCK,
                    120,
                    &mut result,
                );
            }
            if action == "close_hide" && same(expected) {
                unsafe {
                    ShowWindow(hwnd, SW_HIDE);
                }
            }
        }
        "hide" => unsafe {
            ShowWindow(hwnd, SW_HIDE);
        },
        "kill" => {
            processes::terminate_snapshot(&processes::Process {
                pid: expected.pid,
                created: expected.created,
                path: expected.image.clone(),
                name: String::new(),
            })?;
        }
        _ => return Err(io::Error::other("Unsupported popup action")),
    }
    std::thread::sleep(std::time::Duration::from_millis(80));
    Ok(unsafe { IsWindow(hwnd) == 0 || IsWindowVisible(hwnd) == 0 })
}
pub fn pixel(window: &Window, x: i32, y: i32) -> Option<u32> {
    let dc = unsafe { GetDC(window.hwnd as HWND) };
    if dc.is_null() {
        return None;
    }
    let color = unsafe { GetPixel(dc, x, y) };
    unsafe {
        ReleaseDC(window.hwnd as HWND, dc);
    }
    if color == u32::MAX { None } else { Some(color) }
}
pub fn foreground(title: &str) {
    let hwnd = unsafe { FindWindowW(ptr::null(), wide(title).as_ptr()) };
    if !hwnd.is_null() {
        unsafe {
            ShowWindow(hwnd, SW_RESTORE);
            SetForegroundWindow(hwnd);
        }
    }
}
