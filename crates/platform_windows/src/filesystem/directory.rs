//! Batch directory facts only; callers must validate files before authorizing operations.
use super::{content, identity, large_scan};
use std::{
    ffi::OsString,
    fs::OpenOptions,
    io,
    mem::offset_of,
    os::windows::{ffi::OsStringExt, fs::OpenOptionsExt, io::AsRawHandle},
    path::Path,
};
use windows_sys::Win32::{Foundation::*, Storage::FileSystem::*};

const BUFFER_BYTES: usize = 64 * 1024;
const HEADER: usize = offset_of!(FILE_ID_EXTD_DIR_INFO, FileName);
#[derive(Clone, Debug)]
pub struct Entry {
    pub name: OsString,
    pub size: u64,
    pub allocation: u64,
    pub modified: u64,
    pub attributes: u32,
    pub tag: u32,
    pub id: [u8; 16],
}
pub enum Event<'a> {
    File { path: &'a Path, entry: &'a Entry },
    Skipped { path: &'a Path, remote: bool },
    Issue { path: &'a Path, error: io::Error },
}
#[derive(Default, Debug)]
pub struct Summary {
    pub pages: u64,
    pub directories: u64,
    pub cancelled: bool,
}

/// Read one directory in batches; the caller owns recursion and rule policy.
pub fn read(directory: &Path, cancelled: &dyn Fn() -> bool) -> io::Result<Vec<Entry>> {
    read_pages(directory, cancelled).map(|(entries, _)| entries)
}
fn read_pages(directory: &Path, cancelled: &dyn Fn() -> bool) -> io::Result<(Vec<Entry>, u64)> {
    let handle = OpenOptions::new()
        .read(true)
        .access_mode(FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .open(directory)?;
    let info = content::identity(&handle)?;
    if !info.directory() || info.redirected() || large_scan::remote(info.attributes) {
        return Err(io::Error::other(
            "Directory became a link or remote placeholder",
        ));
    }
    let mut storage = vec![0_u64; BUFFER_BYTES / 8];
    let mut entries = Vec::new();
    let mut restart = true;
    let mut pages = 0;
    loop {
        if cancelled() {
            return Err(io::ErrorKind::Interrupted.into());
        }
        storage.fill(0);
        let class = if restart {
            FileIdExtdDirectoryRestartInfo
        } else {
            FileIdExtdDirectoryInfo
        };
        let ok = unsafe {
            GetFileInformationByHandleEx(
                handle.as_raw_handle(),
                class,
                storage.as_mut_ptr().cast(),
                BUFFER_BYTES as u32,
            )
        };
        if ok == 0 {
            let code = unsafe { GetLastError() };
            if code == ERROR_NO_MORE_FILES {
                break;
            }
            let error = io::Error::from_raw_os_error(code as i32);
            if matches!(
                code,
                ERROR_INVALID_FUNCTION
                    | ERROR_INVALID_PARAMETER
                    | ERROR_NOT_SUPPORTED
                    | ERROR_CALL_NOT_IMPLEMENTED
            ) {
                return Err(io::Error::new(io::ErrorKind::Unsupported, error));
            }
            return Err(error);
        }
        restart = false;
        pages += 1;
        let bytes =
            unsafe { std::slice::from_raw_parts(storage.as_ptr().cast::<u8>(), BUFFER_BYTES) };
        entries.extend(
            parse(bytes)?
                .into_iter()
                .filter(|e| e.name != "." && e.name != ".."),
        );
    }
    Ok((entries, pages))
}

pub fn scan(
    root: &Path,
    cancelled: &dyn Fn() -> bool,
    emit: &mut dyn FnMut(Event<'_>),
) -> io::Result<Summary> {
    let volume = identity(root)?.volume;
    let mut pending = vec![root.to_path_buf()];
    let mut summary = Summary::default();
    while let Some(directory) = pending.pop() {
        if cancelled() {
            summary.cancelled = true;
            break;
        }
        if let Err(error) = large_scan::safe_directory(root, &directory, volume) {
            emit(Event::Issue {
                path: &directory,
                error,
            });
            continue;
        }
        let (entries, pages) = match read_pages(&directory, cancelled) {
            Ok(entries) => entries,
            Err(e) if e.kind() == io::ErrorKind::Unsupported => return Err(e),
            Err(_) if cancelled() => {
                summary.cancelled = true;
                break;
            }
            Err(error) => {
                emit(Event::Issue {
                    path: &directory,
                    error,
                });
                continue;
            }
        };
        if let Err(error) = large_scan::safe_directory(root, &directory, volume) {
            emit(Event::Issue {
                path: &directory,
                error,
            });
            continue;
        }
        summary.directories += 1;
        summary.pages += pages;
        for entry in entries {
            if cancelled() {
                summary.cancelled = true;
                return Ok(summary);
            }
            let path = directory.join(&entry.name);
            let is_dir = entry.attributes & FILE_ATTRIBUTE_DIRECTORY != 0;
            let remote = large_scan::remote(entry.attributes);
            if remote
                || (entry.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
                    && (is_dir || entry.tag != large_scan::WOF_TAG))
            {
                emit(Event::Skipped {
                    path: &path,
                    remote,
                });
            } else if is_dir {
                pending.push(path);
            } else {
                emit(Event::File {
                    path: &path,
                    entry: &entry,
                });
            }
        }
    }
    Ok(summary)
}
fn parse(bytes: &[u8]) -> io::Result<Vec<Entry>> {
    let invalid = || io::Error::new(io::ErrorKind::InvalidData, "Invalid batch directory record");
    let mut entries = Vec::new();
    let mut offset = 0;
    loop {
        let header = bytes.get(offset..offset + HEADER).ok_or_else(invalid)?;
        let mut info = FILE_ID_EXTD_DIR_INFO::default();
        unsafe {
            std::ptr::copy_nonoverlapping(
                header.as_ptr(),
                (&mut info as *mut FILE_ID_EXTD_DIR_INFO).cast(),
                HEADER,
            );
        }
        let length = info.FileNameLength as usize;
        if length == 0
            || !length.is_multiple_of(2)
            || info.EndOfFile < 0
            || info.AllocationSize < 0
            || info.LastWriteTime < 0
        {
            return Err(invalid());
        }
        let end = offset
            .checked_add(HEADER)
            .and_then(|n| n.checked_add(length))
            .ok_or_else(invalid)?;
        let name = bytes.get(offset + HEADER..end).ok_or_else(invalid)?;
        let units: Vec<_> = name
            .as_chunks::<2>()
            .0
            .iter()
            .map(|c| u16::from_le_bytes([c[0], c[1]]))
            .collect();
        if units.iter().any(|u| matches!(*u, 0 | 47 | 58 | 92)) {
            return Err(invalid());
        }
        entries.push(Entry {
            name: OsString::from_wide(&units),
            size: info.EndOfFile as u64,
            allocation: info.AllocationSize as u64,
            modified: info.LastWriteTime as u64,
            attributes: info.FileAttributes,
            tag: info.ReparsePointTag,
            id: info.FileId.Identifier,
        });
        if info.NextEntryOffset == 0 {
            break;
        }
        let delta = info.NextEntryOffset as usize;
        if delta < HEADER + length || !delta.is_multiple_of(8) {
            return Err(invalid());
        }
        offset = offset
            .checked_add(delta)
            .filter(|n| *n < bytes.len())
            .ok_or_else(invalid)?;
    }
    Ok(entries)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn record(name: &[u16]) -> Vec<u8> {
        let info = FILE_ID_EXTD_DIR_INFO {
            FileNameLength: (name.len() * 2) as u32,
            EndOfFile: 12,
            AllocationSize: 4096,
            LastWriteTime: 23,
            ..Default::default()
        };
        let mut bytes = vec![0; HEADER + name.len() * 2];
        unsafe {
            std::ptr::copy_nonoverlapping(
                (&info as *const FILE_ID_EXTD_DIR_INFO).cast(),
                bytes.as_mut_ptr(),
                HEADER,
            );
        }
        for (out, unit) in bytes[HEADER..].as_chunks_mut::<2>().0.iter_mut().zip(name) {
            out.copy_from_slice(&unit.to_le_bytes());
        }
        bytes
    }
    #[test]
    fn batch_records_preserve_unicode_and_reject_invalid_ranges_and_names() {
        let bytes = record(&"文件.dat".encode_utf16().collect::<Vec<_>>());
        let entries = parse(&bytes).unwrap();
        assert_eq!(entries[0].name, "文件.dat");
        assert_eq!(entries[0].allocation, 4096);
        assert!(parse(&bytes[..bytes.len() - 1]).is_err());
        assert!(parse(&record(&[b'a' as u16, 92, b'b' as u16])).is_err());
        let mut linked = bytes.clone();
        linked[..4].copy_from_slice(&8_u32.to_le_bytes());
        assert!(parse(&linked).is_err());
    }
}
