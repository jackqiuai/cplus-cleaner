//! Handle-based content verification and conservative storage scheduling.
use super::{Identity, disk, large_scan};
use crate::file_wide;
use std::{
    fs::{File, OpenOptions},
    io, mem,
    os::windows::{fs::OpenOptionsExt, io::AsRawHandle},
    path::Path,
};
use windows_sys::Win32::{
    Foundation::*,
    Storage::FileSystem::*,
    System::{IO::DeviceIoControl, Ioctl::*},
};

pub fn identity(file: &File) -> io::Result<Identity> {
    let handle = file.as_raw_handle();
    let mut info = BY_HANDLE_FILE_INFORMATION::default();
    if unsafe { GetFileInformationByHandle(handle, &mut info) } == 0 {
        return Err(io::Error::last_os_error());
    }
    let mut tag = FILE_ATTRIBUTE_TAG_INFO::default();
    if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
        && unsafe {
            GetFileInformationByHandleEx(
                handle,
                FileAttributeTagInfo,
                (&mut tag as *mut FILE_ATTRIBUTE_TAG_INFO).cast(),
                mem::size_of_val(&tag) as u32,
            )
        } == 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(Identity {
        volume: info.dwVolumeSerialNumber,
        index: (u64::from(info.nFileIndexHigh) << 32) | u64::from(info.nFileIndexLow),
        size: (u64::from(info.nFileSizeHigh) << 32) | u64::from(info.nFileSizeLow),
        modified: (u64::from(info.ftLastWriteTime.dwHighDateTime) << 32)
            | u64::from(info.ftLastWriteTime.dwLowDateTime),
        links: info.nNumberOfLinks,
        attributes: info.dwFileAttributes,
        tag: tag.ReparseTag,
    })
}

/// Deny writers and replacement while the caller hashes the opened object.
pub fn open(path: &Path, expected: &Identity, delete: bool) -> io::Result<File> {
    let file = OpenOptions::new()
        .read(true)
        .access_mode(GENERIC_READ | if delete { DELETE } else { 0 })
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)?;
    let current = identity(&file)?;
    if &current != expected
        || current.directory()
        || current.redirected()
        || current.links != 1
        || large_scan::remote(current.attributes)
    {
        return Err(io::Error::other(
            "File changed or became unavailable during verification",
        ));
    }
    Ok(file)
}

/// Delete the verified object itself; a later path lookup cannot redirect deletion.
pub fn delete(file: &File) -> io::Result<()> {
    let disposition = FILE_DISPOSITION_INFO { DeleteFile: true };
    if unsafe {
        SetFileInformationByHandle(
            file.as_raw_handle(),
            FileDispositionInfo,
            (&disposition as *const FILE_DISPOSITION_INFO).cast(),
            mem::size_of_val(&disposition) as u32,
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

/// Unknown, remote and removable devices deliberately keep one hashing worker.
pub fn hash_worker_limit(root: &Path) -> usize {
    let Ok(volume) = disk(root) else { return 1 };
    let wide = file_wide(Path::new(&volume.root));
    const FIXED_DRIVE: u32 = 3;
    if unsafe { GetDriveTypeW(wide.as_ptr()) } != FIXED_DRIVE {
        return 1;
    }
    let device = format!(r"\\.\{}", volume.root.trim_end_matches(['\\', '/']));
    let raw = unsafe {
        CreateFileW(
            file_wide(Path::new(&device)).as_ptr(),
            0,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            std::ptr::null(),
            OPEN_EXISTING,
            0,
            std::ptr::null_mut(),
        )
    };
    if raw == INVALID_HANDLE_VALUE {
        return 1;
    }
    let query = STORAGE_PROPERTY_QUERY {
        PropertyId: StorageDeviceSeekPenaltyProperty,
        QueryType: PropertyStandardQuery,
        ..Default::default()
    };
    let mut output = DEVICE_SEEK_PENALTY_DESCRIPTOR::default();
    let mut returned = 0;
    let ok = unsafe {
        DeviceIoControl(
            raw,
            IOCTL_STORAGE_QUERY_PROPERTY,
            (&query as *const STORAGE_PROPERTY_QUERY).cast(),
            mem::size_of_val(&query) as u32,
            (&mut output as *mut DEVICE_SEEK_PENALTY_DESCRIPTOR).cast(),
            mem::size_of_val(&output) as u32,
            &mut returned,
            std::ptr::null_mut(),
        )
    };
    unsafe {
        CloseHandle(raw);
    }
    if ok != 0 && returned as usize >= mem::size_of_val(&output) && !output.IncursSeekPenalty {
        4
    } else {
        1
    }
}
