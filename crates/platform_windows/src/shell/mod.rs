use crate::{from_wide, wide};
use serde::{Deserialize, Serialize};
use std::{io, mem, path::Path, ptr};
use windows_sys::Win32::{
    Foundation::*,
    Graphics::Gdi::*,
    UI::{Shell::*, WindowsAndMessaging::*},
};

pub fn open(value: &str) -> io::Result<()> {
    let result = unsafe {
        ShellExecuteW(
            ptr::null_mut(),
            wide("open").as_ptr(),
            wide(value).as_ptr(),
            ptr::null(),
            ptr::null(),
            SW_SHOWNORMAL,
        )
    } as isize;
    if result <= 32 {
        Err(io::Error::other(format!(
            "Windows could not open the item ({result})"
        )))
    } else {
        Ok(())
    }
}
pub fn select_file(path: &Path) -> io::Result<()> {
    let arg = format!("/select,\"{}\"", path.display());
    let result = unsafe {
        ShellExecuteW(
            ptr::null_mut(),
            wide("open").as_ptr(),
            wide("explorer.exe").as_ptr(),
            wide(arg).as_ptr(),
            ptr::null(),
            SW_SHOWNORMAL,
        )
    } as isize;
    if result <= 32 {
        Err(io::Error::other("Could not open the file location"))
    } else {
        Ok(())
    }
}
pub struct ElevatedProcess {
    handle: HANDLE,
}
impl Drop for ElevatedProcess {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.handle);
        }
    }
}
pub fn elevate(executable: &Path, parameters: &str) -> io::Result<ElevatedProcess> {
    elevate_with_mode(executable, parameters, SW_HIDE).map(|handle| ElevatedProcess { handle })
}
fn elevate_with_mode(executable: &Path, parameters: &str, show: i32) -> io::Result<HANDLE> {
    let mut info: SHELLEXECUTEINFOW = unsafe { mem::zeroed() };
    info.cbSize = mem::size_of_val(&info) as u32;
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI;
    let verb = wide("runas");
    let file = wide(executable);
    let params = wide(parameters);
    info.lpVerb = verb.as_ptr();
    info.lpFile = file.as_ptr();
    info.lpParameters = params.as_ptr();
    info.nShow = show;
    if unsafe { ShellExecuteExW(&mut info) } == 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(info.hProcess)
    }
}
pub fn elevated_visible(executable: &Path, parameters: &str) -> io::Result<u32> {
    let handle = elevate_with_mode(executable, parameters, SW_SHOWNORMAL)?;
    unsafe {
        windows_sys::Win32::System::Threading::WaitForSingleObject(handle, u32::MAX);
        let mut exit = 0;
        let ok = windows_sys::Win32::System::Threading::GetExitCodeProcess(handle, &mut exit);
        let error = io::Error::last_os_error();
        CloseHandle(handle);
        if ok == 0 { Err(error) } else { Ok(exit) }
    }
}
impl ElevatedProcess {
    pub fn poll(&self) -> io::Result<Option<u32>> {
        let handle = self.handle;
        let state =
            unsafe { windows_sys::Win32::System::Threading::WaitForSingleObject(handle, 0) };
        if state == WAIT_TIMEOUT {
            return Ok(None);
        }
        if state != WAIT_OBJECT_0 {
            return Err(io::Error::last_os_error());
        }
        let mut exit = 0;
        if unsafe { windows_sys::Win32::System::Threading::GetExitCodeProcess(handle, &mut exit) }
            == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(Some(exit))
    }
}
pub fn is_admin() -> bool {
    unsafe { IsUserAnAdmin() != 0 }
}
pub fn traditional(value: &str) -> String {
    let input = wide(value);
    let needed = unsafe {
        windows_sys::Win32::Globalization::LCMapStringEx(
            wide("zh-CN").as_ptr(),
            0x04000000,
            input.as_ptr(),
            (input.len() - 1) as i32,
            ptr::null_mut(),
            0,
            ptr::null(),
            ptr::null_mut(),
            0,
        )
    };
    if needed <= 0 {
        return value.into();
    }
    let mut output = vec![0u16; needed as usize];
    let done = unsafe {
        windows_sys::Win32::Globalization::LCMapStringEx(
            wide("zh-CN").as_ptr(),
            0x04000000,
            input.as_ptr(),
            (input.len() - 1) as i32,
            output.as_mut_ptr(),
            needed,
            ptr::null(),
            ptr::null_mut(),
            0,
        )
    };
    if done > 0 {
        String::from_utf16_lossy(&output)
    } else {
        value.into()
    }
}
pub fn user_language() -> &'static str {
    let id = unsafe { windows_sys::Win32::Globalization::GetUserDefaultUILanguage() };
    if id & 0x3ff == 4 {
        if [0x0404, 0x0c04, 0x1404, 0x7c04].contains(&id) {
            "zh-TW"
        } else {
            "zh"
        }
    } else {
        "en"
    }
}

pub fn documents_folder() -> std::path::PathBuf {
    let mut path = [0u16; 32768];
    if unsafe { SHGetFolderPathW(ptr::null_mut(), 5, ptr::null_mut(), 0, path.as_mut_ptr()) } == 0 {
        from_wide(&path).into()
    } else {
        std::path::PathBuf::from(std::env::var_os("USERPROFILE").unwrap_or_default())
            .join("Documents")
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Icon {
    pub width: u32,
    pub height: u32,
    pub rgba: Vec<u8>,
}
pub fn icon(path: &Path, index: i32) -> Option<Icon> {
    let raw = path.to_string_lossy();
    let normal = raw
        .strip_prefix(r"\\?\UNC\")
        .map(|p| format!(r"\\{p}"))
        .or_else(|| raw.strip_prefix(r"\\?\").map(str::to_owned));
    let path = normal.as_deref().map(Path::new).unwrap_or(path);
    let mut handle = ptr::null_mut();
    if unsafe { ExtractIconExW(wide(path).as_ptr(), index, &mut handle, ptr::null_mut(), 1) } == 0
        || handle.is_null()
    {
        let mut info: SHFILEINFOW = unsafe { mem::zeroed() };
        if unsafe {
            SHGetFileInfoW(
                wide(path).as_ptr(),
                0,
                &mut info,
                mem::size_of_val(&info) as u32,
                SHGFI_ICON | SHGFI_LARGEICON,
            )
        } == 0
        {
            return None;
        }
        handle = info.hIcon;
    }
    if handle.is_null() {
        return None;
    }
    let mut bitmap: BITMAPINFO = unsafe { mem::zeroed() };
    bitmap.bmiHeader.biSize = mem::size_of::<BITMAPINFOHEADER>() as u32;
    bitmap.bmiHeader.biWidth = 32;
    bitmap.bmiHeader.biHeight = -32;
    bitmap.bmiHeader.biPlanes = 1;
    bitmap.bmiHeader.biBitCount = 32;
    bitmap.bmiHeader.biCompression = BI_RGB;
    let dc = unsafe { CreateCompatibleDC(ptr::null_mut()) };
    let mut bits = ptr::null_mut();
    let dib =
        unsafe { CreateDIBSection(dc, &bitmap, DIB_RGB_COLORS, &mut bits, ptr::null_mut(), 0) };
    if dib.is_null() || bits.is_null() {
        unsafe {
            DestroyIcon(handle);
            DeleteDC(dc);
        }
        return None;
    }
    let old = unsafe { SelectObject(dc, dib) };
    unsafe {
        ptr::write_bytes(bits, 0, 32 * 32 * 4);
        DrawIconEx(dc, 0, 0, handle, 32, 32, 0, ptr::null_mut(), DI_NORMAL);
    }
    let mut rgba = unsafe { std::slice::from_raw_parts(bits.cast::<u8>(), 32 * 32 * 4) }.to_vec();
    if rgba.as_chunks::<4>().0.iter().all(|p| p[3] == 0) {
        unsafe {
            ptr::write_bytes(bits, 255, 32 * 32 * 4);
            DrawIconEx(dc, 0, 0, handle, 32, 32, 0, ptr::null_mut(), DI_MASK);
        }
        let mask = unsafe { std::slice::from_raw_parts(bits.cast::<u8>(), 32 * 32 * 4) };
        for (pixel, mask) in rgba
            .as_chunks_mut::<4>()
            .0
            .iter_mut()
            .zip(mask.as_chunks::<4>().0)
        {
            pixel[3] = if mask[0] > 128 { 0 } else { 255 };
        }
    }
    for pixel in rgba.as_chunks_mut::<4>().0 {
        pixel.swap(0, 2);
        let alpha = pixel[3] as u32;
        if alpha > 0 && alpha < 255 {
            for value in &mut pixel[..3] {
                *value = ((*value as u32 * 255) / alpha).min(255) as u8;
            }
        }
    }
    unsafe {
        SelectObject(dc, old);
        DeleteObject(dib);
        DeleteDC(dc);
        DestroyIcon(handle);
    }
    rgba.as_chunks::<4>()
        .0
        .iter()
        .any(|p| p[3] != 0)
        .then_some(Icon {
            width: 32,
            height: 32,
            rgba,
        })
}
/// Native folder picker owned by the user's explicit Choose folder action.
pub fn choose_folder(title: &str) -> Option<std::path::PathBuf> {
    let initialized = unsafe {
        windows_sys::Win32::System::Com::CoInitializeEx(
            ptr::null(),
            windows_sys::Win32::System::Com::COINIT_APARTMENTTHREADED as u32,
        )
    } >= 0;
    let title = wide(title);
    let mut info: BROWSEINFOW = unsafe { mem::zeroed() };
    info.lpszTitle = title.as_ptr();
    info.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
    let item = unsafe { SHBrowseForFolderW(&info) };
    if item.is_null() {
        if initialized {
            unsafe {
                windows_sys::Win32::System::Com::CoUninitialize();
            }
        }
        return None;
    }
    let mut path = [0u16; 32768];
    let success = unsafe { SHGetPathFromIDListW(item, path.as_mut_ptr()) };
    unsafe {
        windows_sys::Win32::System::Com::CoTaskMemFree(item.cast());
        if initialized {
            windows_sys::Win32::System::Com::CoUninitialize();
        }
    }
    if success != 0 {
        Some(from_wide(&path).into())
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shell_icons_keep_alpha_and_visible_pixels() {
        let path = std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap())
            .join("System32/shell32.dll");
        let icon = icon(&path, 4).expect("Windows shell icon");
        assert!(icon.rgba.as_chunks::<4>().0.iter().any(|p| p[3] == 0));
        assert!(icon.rgba.as_chunks::<4>().0.iter().any(|p| p[3] > 0));
    }
}
