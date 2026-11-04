//! Candidate discovery only. Core owns scope policy and live file authorization.
mod layout;

use super::identity;
use crate::file_wide;
use serde::{Deserialize, Serialize};
use std::{ffi::OsString, io, os::windows::ffi::OsStringExt, path::Path};
use windows_sys::Win32::{Foundation::*, Storage::FileSystem::*};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum ScanMode {
    #[default]
    Auto,
    Directory,
    /// Explicit native-only probe; ordinary scans always use Auto.
    Layout,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Summary {
    pub strategy: String,
    pub checked: u64,
    pub directories: u64,
    pub skipped: u64,
    #[serde(default)]
    pub skipped_links: u64,
    #[serde(default)]
    pub skipped_remote: u64,
    pub fallback: Option<String>,
    pub cancelled: bool,
}

pub enum Event<'a> {
    Visit {
        path: &'a Path,
        checked: u64,
    },
    Candidate {
        path: &'a Path,
        expected_index: Option<u64>,
    },
    Issue {
        path: &'a Path,
        error: io::Error,
    },
}

pub(super) const WOF_TAG: u32 = 0x8000_0017;

pub fn remote(attributes: u32) -> bool {
    attributes
        & (FILE_ATTRIBUTE_OFFLINE
            | FILE_ATTRIBUTE_RECALL_ON_OPEN
            | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)
        != 0
}

/// No UAC or subprocesses. A failed native attempt publishes no candidates.
pub fn scan(
    root: &Path,
    minimum: u64,
    mode: ScanMode,
    cancelled: &dyn Fn() -> bool,
    excluded: &dyn Fn(&Path) -> bool,
    emit: &mut dyn FnMut(Event<'_>),
) -> io::Result<Summary> {
    if cancelled() {
        return Ok(Summary {
            cancelled: true,
            ..Default::default()
        });
    }
    let mut fallback = None;
    if mode != ScanMode::Directory {
        match layout::collect(root, minimum, cancelled, excluded, emit) {
            Ok((candidates, mut summary)) => {
                for (path, index) in candidates {
                    if cancelled() {
                        summary.cancelled = true;
                        break;
                    }
                    emit(Event::Candidate {
                        path: &path,
                        expected_index: Some(index),
                    });
                }
                return Ok(summary);
            }
            Err(error) if cancelled() || error.kind() == io::ErrorKind::Interrupted => {
                return Ok(Summary {
                    cancelled: true,
                    ..Default::default()
                });
            }
            Err(error) if mode == ScanMode::Layout => return Err(error),
            Err(error) => fallback = Some(error.to_string()),
        }
    }
    // Reset discovery progress; native candidates were retained until full validation.
    emit(Event::Visit {
        path: root,
        checked: 0,
    });
    let mut summary = directory_scan(root, minimum, cancelled, excluded, emit)?;
    summary.fallback = fallback;
    Ok(summary)
}

struct SearchHandle(HANDLE);
impl Drop for SearchHandle {
    fn drop(&mut self) {
        unsafe {
            FindClose(self.0);
        }
    }
}

pub(super) fn safe_directory(root: &Path, directory: &Path, volume: u32) -> io::Result<()> {
    let info = identity(directory)?;
    if !info.directory()
        || info.attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || remote(info.attributes)
        || info.volume != volume
    {
        return Err(io::Error::other(
            "Directory became a link, remote placeholder or different volume",
        ));
    }
    // One native path resolution checks the ancestor chain without opening every parent in
    // userspace. A renamed ancestor or junction changes the final path. Candidates still
    // receive Core's full no-links and identity checks immediately before publication.
    let resolved = std::fs::canonicalize(directory)?;
    let key = |path: &Path| {
        path.to_string_lossy()
            .replace('/', "\\")
            .trim_start_matches(r"\\?\")
            .trim_end_matches('\\')
            .to_lowercase()
    };
    let actual = key(&resolved);
    let expected = key(directory);
    let root = key(root);
    if actual != expected
        || !(actual == root
            || actual
                .strip_prefix(&root)
                .is_some_and(|tail| tail.starts_with('\\')))
    {
        return Err(io::Error::other("Directory escaped its original scan path"));
    }
    Ok(())
}

fn directory_scan(
    root: &Path,
    minimum: u64,
    cancelled: &dyn Fn() -> bool,
    excluded: &dyn Fn(&Path) -> bool,
    emit: &mut dyn FnMut(Event<'_>),
) -> io::Result<Summary> {
    let root_info = identity(root)?;
    let mut pending = vec![root.to_path_buf()];
    let mut large_fetch = true;
    let mut summary = Summary {
        strategy: "win32_directory".into(),
        ..Default::default()
    };
    while let Some(directory) = pending.pop() {
        if cancelled() {
            summary.cancelled = true;
            break;
        }
        if excluded(&directory) {
            summary.skipped += 1;
            continue;
        }
        if let Err(error) = safe_directory(root, &directory, root_info.volume) {
            if directory == root {
                return Err(error);
            }
            summary.skipped += 1;
            emit(Event::Issue {
                path: &directory,
                error,
            });
            continue;
        }
        summary.directories += 1;
        let pattern = file_wide(&directory.join("*"));
        let mut data = WIN32_FIND_DATAW::default();
        let first = |data: &mut WIN32_FIND_DATAW, large: bool| unsafe {
            FindFirstFileExW(
                pattern.as_ptr(),
                FindExInfoBasic,
                (data as *mut WIN32_FIND_DATAW).cast(),
                FindExSearchNameMatch,
                std::ptr::null(),
                if large { FIND_FIRST_EX_LARGE_FETCH } else { 0 },
            )
        };
        let mut raw = first(&mut data, large_fetch);
        if raw == INVALID_HANDLE_VALUE
            && large_fetch
            && matches!(
                unsafe { GetLastError() },
                ERROR_INVALID_FUNCTION | ERROR_INVALID_PARAMETER | ERROR_NOT_SUPPORTED
            )
        {
            large_fetch = false;
            raw = first(&mut data, false);
        }
        if raw == INVALID_HANDLE_VALUE {
            let error = io::Error::last_os_error();
            if error.raw_os_error() == Some(ERROR_FILE_NOT_FOUND as i32) {
                continue;
            }
            if directory == root {
                return Err(error);
            }
            summary.skipped += 1;
            emit(Event::Issue {
                path: &directory,
                error,
            });
            continue;
        }
        let handle = SearchHandle(raw);
        loop {
            if cancelled() {
                summary.cancelled = true;
                break;
            }
            let length = data
                .cFileName
                .iter()
                .position(|&c| c == 0)
                .unwrap_or(data.cFileName.len());
            let name = &data.cFileName[..length];
            if name != [46] && name != [46, 46] {
                summary.checked += 1;
                let path = directory.join(OsString::from_wide(name));
                emit(Event::Visit {
                    path: &path,
                    checked: summary.checked,
                });
                if cancelled() {
                    summary.cancelled = true;
                    break;
                }
                let attributes = data.dwFileAttributes;
                let is_dir = attributes & FILE_ATTRIBUTE_DIRECTORY != 0;
                let redirected = attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
                    && (is_dir || data.dwReserved0 != WOF_TAG);
                if remote(attributes) || redirected || excluded(&path) {
                    summary.skipped += 1;
                    if remote(attributes) {
                        summary.skipped_remote += 1;
                    } else if redirected {
                        summary.skipped_links += 1;
                    }
                } else if is_dir {
                    pending.push(path);
                } else if ((u64::from(data.nFileSizeHigh) << 32) | u64::from(data.nFileSizeLow))
                    >= minimum
                {
                    emit(Event::Candidate {
                        path: &path,
                        expected_index: None,
                    });
                }
            }
            if unsafe { FindNextFileW(handle.0, &mut data) } == 0 {
                let code = unsafe { GetLastError() };
                if code != ERROR_NO_MORE_FILES {
                    summary.skipped += 1;
                    emit(Event::Issue {
                        path: &directory,
                        error: io::Error::from_raw_os_error(code as i32),
                    });
                }
                break;
            }
        }
    }
    summary.cancelled |= cancelled();
    Ok(summary)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cloud_residency_is_independent_of_sparse_or_compressed_attributes() {
        for flag in [
            FILE_ATTRIBUTE_OFFLINE,
            FILE_ATTRIBUTE_RECALL_ON_OPEN,
            FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
        ] {
            assert!(remote(flag));
        }
        assert!(!remote(
            FILE_ATTRIBUTE_SPARSE_FILE | FILE_ATTRIBUTE_COMPRESSED
        ));
    }
}
