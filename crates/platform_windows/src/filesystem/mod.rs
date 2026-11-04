use crate::{file_wide, from_wide, wide};
use serde::{Deserialize, Serialize};
use std::{ffi::c_void, fs::File, io, mem, os::windows::io::AsRawHandle, path::Path};
use windows_sys::Win32::{Foundation::*, Storage::FileSystem::*, System::IO::DeviceIoControl};
pub mod content;
pub mod directory;
pub mod large_scan;

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Identity {
    pub volume: u32,
    pub index: u64,
    pub size: u64,
    pub modified: u64,
    pub links: u32,
    pub attributes: u32,
    pub tag: u32,
}
impl Identity {
    pub fn redirected(&self) -> bool {
        self.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 && self.tag != 0x8000_0017
    }
    pub fn directory(&self) -> bool {
        self.attributes & FILE_ATTRIBUTE_DIRECTORY != 0
    }
}

pub fn identity(path: &Path) -> io::Result<Identity> {
    let file = unsafe {
        CreateFileW(
            file_wide(path).as_ptr(),
            FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            std::ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            std::ptr::null_mut(),
        )
    };
    if file == INVALID_HANDLE_VALUE {
        return Err(io::Error::last_os_error());
    }
    let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { mem::zeroed() };
    let result = unsafe { GetFileInformationByHandle(file, &mut info) };
    let error = io::Error::last_os_error();
    let mut tag: FILE_ATTRIBUTE_TAG_INFO = unsafe { mem::zeroed() };
    if result != 0 && info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        let tag_result = unsafe {
            GetFileInformationByHandleEx(
                file,
                FileAttributeTagInfo,
                (&mut tag as *mut FILE_ATTRIBUTE_TAG_INFO).cast(),
                mem::size_of_val(&tag) as u32,
            )
        };
        if tag_result == 0 {
            let error = io::Error::last_os_error();
            unsafe {
                CloseHandle(file);
            };
            return Err(error);
        }
    }
    unsafe {
        CloseHandle(file);
    }
    if result == 0 {
        return Err(error);
    }
    Ok(Identity {
        volume: info.dwVolumeSerialNumber,
        index: ((info.nFileIndexHigh as u64) << 32) | info.nFileIndexLow as u64,
        size: ((info.nFileSizeHigh as u64) << 32) | info.nFileSizeLow as u64,
        modified: ((info.ftLastWriteTime.dwHighDateTime as u64) << 32)
            | info.ftLastWriteTime.dwLowDateTime as u64,
        links: info.nNumberOfLinks,
        attributes: info.dwFileAttributes,
        tag: tag.ReparseTag,
    })
}

pub fn allocated(path: &Path) -> io::Result<u64> {
    let mut high = 0;
    unsafe {
        SetLastError(0);
    }
    let low = unsafe { GetCompressedFileSizeW(file_wide(path).as_ptr(), &mut high) };
    if low == u32::MAX && unsafe { GetLastError() } != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(((high as u64) << 32) | low as u64)
}
pub fn clear_readonly(path: &Path) -> io::Result<()> {
    let path = file_wide(path);
    let attributes = unsafe { GetFileAttributesW(path.as_ptr()) };
    if attributes == INVALID_FILE_ATTRIBUTES {
        return Err(io::Error::last_os_error());
    }
    if unsafe { SetFileAttributesW(path.as_ptr(), attributes & !FILE_ATTRIBUTE_READONLY) } == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

pub fn wof_compressed(path: &Path) -> bool {
    type Probe =
        unsafe extern "system" fn(*const u16, *mut i32, *mut u32, *mut c_void, *mut u32) -> i32;
    static PROBE: std::sync::OnceLock<Option<usize>> = std::sync::OnceLock::new();
    let address = PROBE.get_or_init(|| unsafe {
        let lib = windows_sys::Win32::System::LibraryLoader::LoadLibraryExW(
            wide("wofutil.dll").as_ptr(),
            std::ptr::null_mut(),
            windows_sys::Win32::System::LibraryLoader::LOAD_LIBRARY_SEARCH_SYSTEM32,
        );
        if lib.is_null() {
            return None;
        }
        windows_sys::Win32::System::LibraryLoader::GetProcAddress(
            lib,
            c"WofIsExternalFile".as_ptr().cast(),
        )
        .map(|f| f as usize)
    });
    let Some(address) = address else {
        return false;
    };
    let probe: Probe = unsafe { mem::transmute(*address) };
    let (mut external, mut provider) = (0, 0);
    unsafe {
        probe(
            file_wide(path).as_ptr(),
            &mut external,
            &mut provider,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        ) == 0
            && external != 0
            && provider == 2
    }
}

pub fn compressed(path: &Path) -> io::Result<bool> {
    let info = identity(path)?;
    Ok(info.attributes & FILE_ATTRIBUTE_COMPRESSED != 0 || wof_compressed(path))
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Disk {
    pub root: String,
    pub total: u64,
    pub free: u64,
    pub used: u64,
    pub filesystem: String,
}

pub fn disks() -> Vec<Disk> {
    let mask = unsafe { GetLogicalDrives() };
    (0..26)
        .filter_map(|n| {
            if mask & (1 << n) == 0 {
                return None;
            }
            let root = format!("{}:\\", (b'A' + n) as char);
            if unsafe { GetDriveTypeW(wide(&root).as_ptr()) } != 3 {
                return None;
            }
            disk(Path::new(&root)).ok()
        })
        .collect()
}
pub fn disk(path: &Path) -> io::Result<Disk> {
    let mut mount = [0u16; 32768];
    if unsafe {
        GetVolumePathNameW(
            file_wide(path).as_ptr(),
            mount.as_mut_ptr(),
            mount.len() as u32,
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    let (mut free, mut total, mut all_free) = (0, 0, 0);
    if unsafe { GetDiskFreeSpaceExW(mount.as_ptr(), &mut free, &mut total, &mut all_free) } == 0 {
        return Err(io::Error::last_os_error());
    }
    let mut fs = [0u16; 64];
    unsafe {
        GetVolumeInformationW(
            mount.as_ptr(),
            std::ptr::null_mut(),
            0,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            fs.as_mut_ptr(),
            fs.len() as u32,
        );
    }
    Ok(Disk {
        root: from_wide(&mount).trim_start_matches(r"\\?\").to_string(),
        total,
        free,
        used: total.saturating_sub(free),
        filesystem: from_wide(&fs),
    })
}

/// Create an NTFS junction at a previously nonexistent path. Never replaces data.
pub fn create_junction(link: &Path, target: &Path) -> io::Result<()> {
    if link.exists() || std::fs::symlink_metadata(link).is_ok() {
        return Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "Junction path already exists",
        ));
    }
    let target = std::fs::canonicalize(target)?;
    let display = target
        .to_string_lossy()
        .trim_start_matches(r"\\?\")
        .to_owned();
    let substitute = format!(r"\??\{display}");
    let raw_sub = wide(&substitute);
    let raw_print = wide(&display);
    let sub_bytes = (raw_sub.len() - 1) * 2;
    let print_bytes = (raw_print.len() - 1) * 2;
    let print_offset = raw_sub.len() * 2;
    let data_length = 8 + (raw_sub.len() + raw_print.len()) * 2;
    let mut buffer = Vec::with_capacity(data_length + 8);
    buffer.extend_from_slice(&0xA0000003u32.to_le_bytes());
    buffer.extend_from_slice(&(data_length as u16).to_le_bytes());
    buffer.extend_from_slice(&0u16.to_le_bytes());
    for value in [
        0u16,
        sub_bytes as u16,
        print_offset as u16,
        print_bytes as u16,
    ] {
        buffer.extend_from_slice(&value.to_le_bytes());
    }
    for value in raw_sub.into_iter().chain(raw_print) {
        buffer.extend_from_slice(&value.to_le_bytes());
    }
    std::fs::create_dir(link)?;
    let handle = unsafe {
        CreateFileW(
            wide(link).as_ptr(),
            0x40000000,
            0,
            std::ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
            std::ptr::null_mut(),
        )
    };
    if handle == INVALID_HANDLE_VALUE {
        let err = io::Error::last_os_error();
        let _ = std::fs::remove_dir(link);
        return Err(err);
    }
    let mut returned = 0;
    let ok = unsafe {
        DeviceIoControl(
            handle,
            0x900A4,
            buffer.as_ptr().cast(),
            buffer.len() as u32,
            std::ptr::null_mut(),
            0,
            &mut returned,
            std::ptr::null_mut(),
        )
    };
    let err = io::Error::last_os_error();
    unsafe {
        CloseHandle(handle);
    }
    if ok == 0 {
        let _ = std::fs::remove_dir(link);
        return Err(err);
    }
    Ok(())
}

pub fn flush(file: &File) -> io::Result<()> {
    if unsafe { FlushFileBuffers(file.as_raw_handle().cast()) } == 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}
