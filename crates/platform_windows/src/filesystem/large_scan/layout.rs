//! NTFS layout decoding based on the Windows SDK winioctl.h protocol.
//! Offsets are decoded from bounded slices; no pointers into kernel output escape.
use super::{Event, Summary, remote};
use crate::filesystem::{disk, identity};
use std::{
    collections::{HashMap, HashSet},
    ffi::OsString,
    fs::OpenOptions,
    io,
    mem::{offset_of, size_of},
    os::windows::{
        ffi::{OsStrExt, OsStringExt},
        fs::OpenOptionsExt,
        io::AsRawHandle,
    },
    path::{Path, PathBuf},
};
use windows_sys::Win32::{
    Foundation::{ERROR_HANDLE_EOF, GENERIC_READ, HANDLE},
    Storage::FileSystem::{
        FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT, FILE_SHARE_DELETE, FILE_SHARE_READ,
        FILE_SHARE_WRITE,
    },
    System::{IO::DeviceIoControl, Ioctl::*},
};

const BUFFER_BYTES: usize = 8 * 1024 * 1024;
const MAX_RECORDS: u64 = 10_000_000;
const MAX_DIRECTORIES: usize = 1_000_000;
const MAX_CANDIDATE_NAMES: usize = 250_000;
const MAX_CHAIN: usize = 4096;

#[derive(Clone)]
struct Name {
    parent: u64,
    value: OsString,
}
struct Directory {
    name: Name,
    boundary: bool,
}
#[derive(Default)]
struct Index {
    directories: HashMap<u64, Directory>,
    files: Vec<(u64, Name)>,
    records: u64,
    skipped: u64,
}

fn invalid() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidData,
        "Invalid or unsupported NTFS layout response",
    )
}
fn interrupted() -> io::Error {
    io::Error::new(io::ErrorKind::Interrupted, "Scan cancelled")
}
fn uint32(data: &[u8], offset: usize) -> io::Result<u32> {
    let end = offset.checked_add(4).ok_or_else(invalid)?;
    Ok(u32::from_le_bytes(
        data.get(offset..end)
            .ok_or_else(invalid)?
            .try_into()
            .map_err(|_| invalid())?,
    ))
}
fn uint64(data: &[u8], offset: usize) -> io::Result<u64> {
    let end = offset.checked_add(8).ok_or_else(invalid)?;
    Ok(u64::from_le_bytes(
        data.get(offset..end)
            .ok_or_else(invalid)?
            .try_into()
            .map_err(|_| invalid())?,
    ))
}
fn next_offset(current: usize, delta: u32, consumed: usize, limit: usize) -> io::Result<usize> {
    let next = current.checked_add(delta as usize).ok_or_else(invalid)?;
    if (delta as usize) < consumed || next >= limit {
        return Err(invalid());
    }
    Ok(next)
}

fn names(record: &[u8], first: usize) -> io::Result<Vec<Name>> {
    let mut result = Vec::new();
    let mut cursor = first;
    let prefix = offset_of!(FILE_LAYOUT_NAME_ENTRY, FileName);
    for _ in 0..MAX_CHAIN {
        if cursor == 0 {
            return Ok(result);
        }
        if cursor < size_of::<FILE_LAYOUT_ENTRY>() {
            return Err(invalid());
        }
        let flags = uint32(record, cursor + offset_of!(FILE_LAYOUT_NAME_ENTRY, Flags))?;
        let parent = uint64(
            record,
            cursor + offset_of!(FILE_LAYOUT_NAME_ENTRY, ParentFileReferenceNumber),
        )?;
        let length = uint32(
            record,
            cursor + offset_of!(FILE_LAYOUT_NAME_ENTRY, FileNameLength),
        )? as usize;
        if length == 0 || !length.is_multiple_of(2) || length > 510 {
            return Err(invalid());
        }
        let start = cursor.checked_add(prefix).ok_or_else(invalid)?;
        let end = start.checked_add(length).ok_or_else(invalid)?;
        let bytes = record.get(start..end).ok_or_else(invalid)?;
        let value = bytes
            .as_chunks::<2>()
            .0
            .iter()
            .map(|v| u16::from_le_bytes([v[0], v[1]]))
            .collect::<Vec<_>>();
        if value.iter().any(|v| [0, 47, 58, 92].contains(v)) {
            return Err(invalid());
        }
        // DOS-only aliases do not create a second visible row. The root's dot name is ignored.
        if value != [46]
            && value != [46, 46]
            && (flags & FILE_LAYOUT_NAME_ENTRY_DOS == 0
                || flags & FILE_LAYOUT_NAME_ENTRY_PRIMARY != 0)
        {
            result.push(Name {
                parent,
                value: OsString::from_wide(&value),
            });
        }
        let delta = uint32(record, cursor)?;
        if delta == 0 {
            return Ok(result);
        }
        cursor = next_offset(cursor, delta, prefix + length, record.len())?;
    }
    Err(invalid())
}

fn logical_size(record: &[u8], first: usize) -> io::Result<u64> {
    let mut maximum = 0;
    let mut cursor = first;
    let prefix = offset_of!(STREAM_LAYOUT_ENTRY, StreamIdentifier);
    for _ in 0..MAX_CHAIN {
        if cursor == 0 {
            return Ok(maximum);
        }
        if cursor < size_of::<FILE_LAYOUT_ENTRY>() {
            return Err(invalid());
        }
        if !matches!(uint32(record, cursor)?, 1 | 2) {
            return Err(invalid());
        }
        let kind = uint32(
            record,
            cursor + offset_of!(STREAM_LAYOUT_ENTRY, AttributeTypeCode),
        )?;
        let length = uint32(
            record,
            cursor + offset_of!(STREAM_LAYOUT_ENTRY, StreamIdentifierLength),
        )? as usize;
        let consumed = prefix.checked_add(length).ok_or_else(invalid)?;
        if !length.is_multiple_of(2)
            || cursor
                .checked_add(consumed)
                .is_none_or(|end| end > record.len())
        {
            return Err(invalid());
        }
        if kind == 0x80 {
            let bytes = uint64(record, cursor + offset_of!(STREAM_LAYOUT_ENTRY, EndOfFile))?;
            if bytes > i64::MAX as u64 {
                return Err(invalid());
            }
            // An ADS can nominate a path, but live validation still requires its ordinary file
            // length to meet the threshold. Sparse and compressed default streams remain eligible.
            maximum = maximum.max(bytes);
        }
        let delta = uint32(
            record,
            cursor + offset_of!(STREAM_LAYOUT_ENTRY, NextStreamOffset),
        )?;
        if delta == 0 {
            return Ok(maximum);
        }
        cursor = next_offset(cursor, delta, consumed, record.len())?;
    }
    Err(invalid())
}

impl Index {
    fn page(
        &mut self,
        page: &[u8],
        root_id: u64,
        minimum: u64,
        cancelled: &dyn Fn() -> bool,
    ) -> io::Result<()> {
        if page.len() < size_of::<QUERY_FILE_LAYOUT_OUTPUT>() {
            return Err(invalid());
        }
        let count = uint32(page, offset_of!(QUERY_FILE_LAYOUT_OUTPUT, FileEntryCount))?;
        if count == 0 || count as usize > page.len() / size_of::<FILE_LAYOUT_ENTRY>() {
            return Err(invalid());
        }
        let mut cursor =
            uint32(page, offset_of!(QUERY_FILE_LAYOUT_OUTPUT, FirstFileOffset))? as usize;
        if cursor < size_of::<QUERY_FILE_LAYOUT_OUTPUT>() {
            return Err(invalid());
        }
        for number in 0..count {
            if cancelled() {
                return Err(interrupted());
            }
            self.records += 1;
            if self.records > MAX_RECORDS {
                return Err(invalid());
            }
            let remaining = page.get(cursor..).ok_or_else(invalid)?;
            if remaining.len() < size_of::<FILE_LAYOUT_ENTRY>() || uint32(remaining, 0)? != 1 {
                return Err(invalid());
            }
            let delta = uint32(remaining, offset_of!(FILE_LAYOUT_ENTRY, NextFileOffset))?;
            let end = if delta == 0 {
                page.len()
            } else {
                next_offset(cursor, delta, size_of::<FILE_LAYOUT_ENTRY>(), page.len())?
            };
            if (number + 1 == count) != (delta == 0) {
                return Err(invalid());
            }
            let record = page.get(cursor..end).ok_or_else(invalid)?;
            let attributes = uint32(record, offset_of!(FILE_LAYOUT_ENTRY, FileAttributes))?;
            let id = uint64(record, offset_of!(FILE_LAYOUT_ENTRY, FileReferenceNumber))?;
            let first_name =
                uint32(record, offset_of!(FILE_LAYOUT_ENTRY, FirstNameOffset))? as usize;
            let boundary = remote(attributes) || attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0;
            if id == root_id {
                cursor = end;
                continue;
            }
            // NTFS's reserved metadata records are not ordinary user files or traversal parents.
            if id & 0x0000_ffff_ffff_ffff < 24 {
                self.skipped += 1;
                cursor = end;
                continue;
            }
            if attributes & FILE_ATTRIBUTE_DIRECTORY != 0 {
                let mut links = names(record, first_name)?;
                if links.len() != 1 || self.directories.len() >= MAX_DIRECTORIES {
                    return Err(invalid());
                }
                if self
                    .directories
                    .insert(
                        id,
                        Directory {
                            name: links.remove(0),
                            boundary,
                        },
                    )
                    .is_some()
                {
                    return Err(invalid());
                }
            } else if remote(attributes) {
                self.skipped += 1;
            } else {
                let first_stream =
                    uint32(record, offset_of!(FILE_LAYOUT_ENTRY, FirstStreamOffset))? as usize;
                if logical_size(record, first_stream)? >= minimum {
                    for name in names(record, first_name)? {
                        if self.files.len() >= MAX_CANDIDATE_NAMES {
                            return Err(invalid());
                        }
                        self.files.push((id, name));
                    }
                }
            }
            cursor = end;
        }
        Ok(())
    }

    fn paths(
        &self,
        root: &Path,
        root_id: u64,
        cancelled: &dyn Fn() -> bool,
        excluded: &dyn Fn(&Path) -> bool,
    ) -> io::Result<Vec<(PathBuf, u64)>> {
        let mut cache = HashMap::from([(root_id, Some(root.to_path_buf()))]);
        let mut result = Vec::new();
        for (file_id, name) in &self.files {
            if cancelled() {
                return Err(interrupted());
            }
            let mut id = name.parent;
            let mut chain = Vec::new();
            let mut seen = HashSet::new();
            while !cache.contains_key(&id) {
                if cancelled() {
                    return Err(interrupted());
                }
                if chain.len() >= MAX_CHAIN || !seen.insert(id) {
                    return Err(invalid());
                }
                if id & 0x0000_ffff_ffff_ffff < 24 {
                    cache.insert(id, None);
                    break;
                }
                let directory = self.directories.get(&id).ok_or_else(invalid)?;
                if directory.boundary {
                    cache.insert(id, None);
                    break;
                }
                chain.push((id, &directory.name));
                id = directory.name.parent;
            }
            let mut parent = cache.get(&id).cloned().ok_or_else(invalid)?;
            for (id, link) in chain.into_iter().rev() {
                parent = match parent {
                    Some(path) => {
                        let path = path.join(&link.value);
                        if path.as_os_str().encode_wide().count() > 32_760 {
                            return Err(invalid());
                        }
                        (!excluded(&path)).then_some(path)
                    }
                    None => None,
                };
                cache.insert(id, parent.clone());
            }
            if let Some(parent) = parent {
                let path = parent.join(&name.value);
                if path.as_os_str().encode_wide().count() > 32_760 {
                    return Err(invalid());
                }
                if !excluded(&path) {
                    result.push((path, *file_id));
                }
            }
        }
        Ok(result)
    }
}

pub(super) fn collect(
    root: &Path,
    minimum: u64,
    cancelled: &dyn Fn() -> bool,
    excluded: &dyn Fn(&Path) -> bool,
    emit: &mut dyn FnMut(Event<'_>),
) -> io::Result<(Vec<(PathBuf, u64)>, Summary)> {
    let volume = disk(root)?;
    let root_text = root.to_string_lossy();
    let plain = root_text.trim_start_matches(r"\\?\").trim_end_matches('\\');
    if plain.len() != 2
        || !plain.as_bytes()[0].is_ascii_alphabetic()
        || !plain.ends_with(':')
        || !volume.filesystem.eq_ignore_ascii_case("NTFS")
        || !plain.eq_ignore_ascii_case(volume.root.trim_end_matches('\\'))
    {
        return Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "NTFS layout requires a whole local volume",
        ));
    }
    let root_id = identity(root)?.index;
    let handle = OpenOptions::new()
        .read(true)
        .access_mode(GENERIC_READ)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
        .open(format!(r"\\.\{plain}"))?;
    let mut input = QUERY_FILE_LAYOUT_INPUT {
        Flags: QUERY_FILE_LAYOUT_RESTART
            | QUERY_FILE_LAYOUT_INCLUDE_NAMES
            | QUERY_FILE_LAYOUT_INCLUDE_STREAMS
            | QUERY_FILE_LAYOUT_INCLUDE_STREAMS_WITH_NO_CLUSTERS_ALLOCATED,
        FilterType: QUERY_FILE_LAYOUT_FILTER_TYPE_NONE,
        ..Default::default()
    };
    // u64 storage gives the kernel's output buffer native alignment; decoding itself is safe.
    let mut storage = vec![0_u64; BUFFER_BYTES / 8];
    let mut index = Index::default();
    loop {
        if cancelled() {
            return Err(interrupted());
        }
        let mut returned = 0_u32;
        let success = unsafe {
            DeviceIoControl(
                handle.as_raw_handle() as HANDLE,
                FSCTL_QUERY_FILE_LAYOUT,
                (&input as *const QUERY_FILE_LAYOUT_INPUT).cast(),
                size_of::<QUERY_FILE_LAYOUT_INPUT>() as u32,
                storage.as_mut_ptr().cast(),
                BUFFER_BYTES as u32,
                &mut returned,
                std::ptr::null_mut(),
            )
        };
        if success == 0 {
            let error = io::Error::last_os_error();
            if error.raw_os_error() == Some(ERROR_HANDLE_EOF as i32) {
                break;
            }
            return Err(error);
        }
        if returned == 0 || returned as usize > BUFFER_BYTES {
            return Err(invalid());
        }
        // The initialized allocation is BUFFER_BYTES long and returned was checked above.
        let bytes =
            unsafe { std::slice::from_raw_parts(storage.as_ptr().cast::<u8>(), returned as usize) };
        index.page(bytes, root_id, minimum, cancelled)?;
        emit(Event::Visit {
            path: root,
            checked: index.records,
        });
        input.Flags &= !QUERY_FILE_LAYOUT_RESTART;
    }
    let paths = index.paths(root, root_id, cancelled, excluded)?;
    let summary = Summary {
        strategy: "ntfs_file_layout".into(),
        checked: index.records,
        directories: index.directories.len() as u64,
        skipped: index.skipped,
        ..Default::default()
    };
    Ok((paths, summary))
}

#[cfg(test)]
mod tests;
