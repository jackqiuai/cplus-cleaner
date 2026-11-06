//! Typed Windows integration used by the feature engines and desktop.
pub mod filesystem;
pub mod monitor;
pub mod processes;
pub mod registry;
pub mod shell;
pub mod windows;

pub fn wide(value: impl AsRef<std::ffi::OsStr>) -> Vec<u16> {
    use std::os::windows::ffi::OsStrExt;
    value.as_ref().encode_wide().chain(Some(0)).collect()
}

pub fn from_wide(value: &[u16]) -> String {
    String::from_utf16_lossy(&value[..value.iter().position(|v| *v == 0).unwrap_or(value.len())])
}
/// Extended-length local/UNC paths, without changing the filesystem target.
pub fn file_wide(path: &std::path::Path) -> Vec<u16> {
    let path = std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf());
    let raw = wide(&path);
    if raw.len() < 249 || raw.starts_with(&[92, 92, 63, 92]) {
        return raw;
    }
    let mut prefix = if raw.starts_with(&[92, 92]) {
        wide(r"\\?\UNC\")
    } else {
        wide(r"\\?\")
    };
    prefix.pop();
    prefix.extend_from_slice(if raw.starts_with(&[92, 92]) {
        &raw[2..]
    } else {
        &raw
    });
    prefix
}
