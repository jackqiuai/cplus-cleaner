//! Lightweight uninstall inventory; destructive operations use separate identity checks.
use crate::{Issue, common, jobs::Context};
use cplus_platform_windows::{
    filesystem,
    registry::{self, InstalledRecord},
    shell::{self, Icon},
};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    os::windows::fs::MetadataExt,
    path::{Path, PathBuf},
};

pub(crate) fn installed_applications(ctx: &Context) -> Vec<InstalledRecord> {
    let mut entries = BTreeMap::new();
    for mut row in registry::installed() {
        if ctx.cancel.cancelled() {
            break;
        }
        if row.msi && row.date.trim().is_empty() {
            row.date = registry::msi_property(&row.key, "InstallDate");
        }
        fill_directory_date(&mut row);
        entries.insert((row.hive.clone(), row.key.to_lowercase()), row);
    }
    entries
        .into_values()
        .take_while(|_| !ctx.cancel.cancelled())
        .collect()
}

fn fill_directory_date(row: &mut InstalledRecord) {
    if !row.date.trim().is_empty() {
        return;
    }
    let root = super::location(row);
    if excluded_directory(&root) || super::is_shared(&root) {
        return;
    }
    if let Ok(created) = fs::metadata(root).and_then(|info| info.created()) {
        row.date = chrono::DateTime::<chrono::Local>::from(created)
            .format("%Y%m%d")
            .to_string();
    }
}

pub(super) fn application_root(row: &InstalledRecord) -> Option<PathBuf> {
    if row.system || row.uninstall.is_empty() {
        return None;
    }
    let (exe, _) = super::parse_command(&row.uninstall)?;
    let msi = exe
        .file_name()
        .unwrap_or_default()
        .eq_ignore_ascii_case("msiexec.exe");
    if !msi && !exe.is_file() {
        return None;
    }
    let root = super::location(row);
    Some(if excluded_directory(&root) {
        PathBuf::new()
    } else {
        root
    })
}

#[derive(Clone, Default)]
pub struct SizeSummary {
    pub logical: u64,
    pub files: u64,
    pub modified: u64,
    pub missing: u64,
    pub unreadable: u64,
    pub errors: Vec<Issue>,
}

/// Installer caches and Windows system binaries are not application size roots.
/// Match complete path components, including descendants and extended paths.
pub fn excluded_directory(path: &Path) -> bool {
    path.components().any(|part| {
        part.as_os_str().eq_ignore_ascii_case("Package Cache")
            || part.as_os_str().eq_ignore_ascii_case("System32")
    })
}

/// Like the Python inventory's scandir walk, reuse directory-entry metadata.
/// Do not open every file for allocation, identity or WOF compression queries.
pub fn directory_size(root: &Path, ctx: &Context) -> SizeSummary {
    let mut out = SizeSummary::default();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        if ctx.cancel.cancelled() {
            break;
        }
        if excluded_directory(&dir) {
            continue;
        }
        match fs::symlink_metadata(&dir) {
            Ok(info) if info.file_attributes() & 0x400 != 0 => continue,
            Ok(_) => {}
            Err(error) => {
                failure(&mut out, &dir, error);
                continue;
            }
        }
        let entries = match fs::read_dir(&dir) {
            Ok(entries) => entries,
            Err(error) => {
                failure(&mut out, &dir, error);
                continue;
            }
        };
        for entry in entries {
            if ctx.cancel.cancelled() {
                break;
            }
            let entry = match entry {
                Ok(entry) => entry,
                Err(error) => {
                    failure(&mut out, &dir, error);
                    continue;
                }
            };
            let path = entry.path();
            if excluded_directory(&path) {
                continue;
            }
            let info = match entry.metadata() {
                Ok(info) => info,
                Err(error) => {
                    failure(&mut out, &path, error);
                    continue;
                }
            };
            if info.file_attributes() & 0x400 != 0 {
                // Never recurse into a junction. WOF-backed regular files are
                // the sole allowed reparse type, as in the Python walker.
                if info.is_dir() {
                    continue;
                }
                match filesystem::identity(&path) {
                    Ok(identity) if !identity.redirected() => {}
                    Ok(_) => continue,
                    Err(error) => {
                        failure(&mut out, &path, error);
                        continue;
                    }
                }
            }
            if info.is_dir() {
                stack.push(path);
            } else if info.is_file() {
                out.logical = out.logical.saturating_add(info.len());
                out.modified = out.modified.max(info.last_write_time());
                out.files += 1;
                ctx.set(
                    "inventory",
                    common::display_path(&path),
                    out.files,
                    None,
                    out.logical,
                );
            }
        }
    }
    out.logical = out.logical / 1024 * 1024;
    out
}

fn failure(out: &mut SizeSummary, path: &Path, error: std::io::Error) {
    if error.kind() == std::io::ErrorKind::NotFound {
        out.missing += 1;
        return;
    }
    out.unreadable += 1;
    if out.errors.len() < 3 {
        out.errors.push(Issue {
            path: common::display_path(path),
            message: error.to_string(),
        });
    }
}

pub type IconCache = BTreeMap<(String, i32), Option<Icon>>;

/// DisplayIcon's trailing number may be a negative resource identifier.
pub fn icon_location(value: &str) -> Option<(PathBuf, i32)> {
    let expanded = common::expand_env(value).ok()?;
    let text = expanded.trim().trim_start_matches('@');
    let (path, index) = match text.rsplit_once(',') {
        Some((path, index)) if index.trim().parse::<i32>().is_ok() => {
            (path, index.trim().parse().ok()?)
        }
        _ => (text, 0),
    };
    let path = PathBuf::from(path.trim().trim_matches('"'));
    (!path.as_os_str().is_empty() && path.is_absolute()).then_some((path, index))
}

pub(crate) fn registered_icon(row: &InstalledRecord, cache: &mut IconCache) -> Option<Icon> {
    let mut candidates = Vec::new();
    if let Some(icon) = icon_location(&row.icon) {
        candidates.push(icon);
    }
    if row.msi {
        let product_icon = registry::msi_property(&row.key, "ProductIcon");
        if let Some(icon) = icon_location(&product_icon) {
            candidates.push(icon);
        }
    }
    for (path, index) in candidates {
        if let Some(icon) = cached_icon(&path, index, cache) {
            return Some(icon);
        }
    }
    None
}

pub fn application_icon(row: &InstalledRecord, root: &Path, cache: &mut IconCache) -> Option<Icon> {
    if let Some(icon) = registered_icon(row, cache) {
        return Some(icon);
    }
    // Missing or stale DisplayIcon must not hide an otherwise valid application.
    // Search only a bounded number of likely application directories and files.
    for path in executable_candidates(root, &row.name) {
        if let Some(icon) = cached_icon(&path, 0, cache) {
            return Some(icon);
        }
    }
    super::parse_command(&row.uninstall)
        .filter(|(path, _)| {
            path.is_absolute()
                && !path
                    .file_name()
                    .is_some_and(|n| n.eq_ignore_ascii_case("msiexec.exe"))
        })
        .and_then(|(path, _)| cached_icon(&path, 0, cache))
}

fn cached_icon(path: &Path, index: i32, cache: &mut IconCache) -> Option<Icon> {
    let key = (common::key(path), index);
    cache
        .entry(key)
        .or_insert_with(|| path.is_file().then(|| shell::icon(path, index)).flatten())
        .clone()
}

fn executable_candidates(root: &Path, name: &str) -> Vec<PathBuf> {
    if excluded_directory(root) || !root.is_dir() || super::is_shared(root) {
        return vec![];
    }
    let tokens = name
        .to_lowercase()
        .split(|c: char| !c.is_alphanumeric())
        .filter(|s| s.len() > 2)
        .map(str::to_owned)
        .collect::<Vec<_>>();
    let mut pending = vec![(root.to_path_buf(), 0)];
    let mut found = vec![];
    let mut visited = BTreeSet::new();
    let mut count = 0;
    while let Some((dir, depth)) = pending.pop() {
        if excluded_directory(&dir) || !visited.insert(common::key(&dir)) {
            continue;
        }
        let Ok(entries) = fs::read_dir(dir) else {
            continue;
        };
        for entry in entries.flatten() {
            if excluded_directory(&entry.path()) {
                continue;
            }
            count += 1;
            if count > 256 {
                break;
            }
            let Ok(info) = entry.metadata() else {
                continue;
            };
            if info.file_attributes() & 0x400 != 0 {
                continue;
            }
            let filename = entry.file_name().to_string_lossy().to_lowercase();
            if info.is_dir()
                && depth < 2
                && (matches!(filename.as_str(), "bin" | "application" | "current")
                    || filename.starts_with("app-")
                    || filename.chars().next().is_some_and(|c| c.is_ascii_digit()))
            {
                pending.push((entry.path(), depth + 1));
            } else if info.is_file()
                && filename.ends_with(".exe")
                && !["unins", "update", "crash", "helper", "setup"]
                    .iter()
                    .any(|s| filename.contains(s))
            {
                let matching = tokens
                    .iter()
                    .filter(|s| filename.contains(s.as_str()))
                    .count();
                found.push((matching, info.len(), entry.path()));
            }
        }
        if count > 256 {
            break;
        }
    }
    found.sort_by(|a, b| b.0.cmp(&a.0).then(b.1.cmp(&a.1)));
    found.into_iter().take(4).map(|(_, _, path)| path).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn missing_date_uses_directory_creation_time_without_replacing_existing_dates() {
        let temp = tempfile::tempdir().unwrap();
        let created = fs::metadata(temp.path()).unwrap().created().unwrap();
        let mut row = InstalledRecord {
            location: temp.path().display().to_string(),
            ..Default::default()
        };
        fill_directory_date(&mut row);
        assert_eq!(
            row.date,
            chrono::DateTime::<chrono::Local>::from(created)
                .format("%Y%m%d")
                .to_string()
        );
        row.date = "20200102".into();
        fill_directory_date(&mut row);
        assert_eq!(row.date, "20200102");
        row.date.clear();
        row.location = temp.path().join("missing").display().to_string();
        fill_directory_date(&mut row);
        assert!(row.date.is_empty());
    }
    #[test]
    fn shared_inventory_fills_only_missing_msi_dates() {
        let original: BTreeMap<_, _> = registry::installed()
            .into_iter()
            .map(|row| ((row.hive.clone(), row.key.to_lowercase()), row))
            .collect();
        let ctx = Context::new(crate::Feature::AppMover);
        for row in installed_applications(&ctx) {
            let before = &original[&(row.hive.clone(), row.key.to_lowercase())];
            let expected = if before.msi && before.date.trim().is_empty() {
                registry::msi_property(&before.key, "InstallDate")
            } else {
                before.date.clone()
            };
            if !expected.trim().is_empty() {
                assert_eq!(row.date, expected, "{}", row.name);
            }
        }
    }
    #[test]
    fn shared_inventory_validates_uninstallers_and_excludes_cache_roots() {
        let temp = tempfile::tempdir().unwrap();
        let exe = temp.path().join("uninstall.exe");
        fs::write(&exe, b"fixture").unwrap();
        let mut row = InstalledRecord {
            location: temp.path().display().to_string(),
            uninstall: format!("\"{}\" /quiet", exe.display()),
            ..Default::default()
        };
        assert_eq!(
            application_root(&row),
            Some(crate::common::canonical(temp.path()))
        );
        row.system = true;
        assert!(application_root(&row).is_none());
        row.system = false;
        fs::remove_file(&exe).unwrap();
        assert!(application_root(&row).is_none());
        row.uninstall = "msiexec.exe /x {fixture}".into();
        assert_eq!(
            application_root(&row),
            Some(crate::common::canonical(temp.path()))
        );
        let cache = temp.path().join("Package Cache");
        fs::create_dir(&cache).unwrap();
        row.location = cache.display().to_string();
        assert_eq!(application_root(&row), Some(PathBuf::new()));
        row.uninstall.clear();
        assert!(application_root(&row).is_none());
    }
    #[test]
    fn excluded_inventory_paths_match_components_and_descendants() {
        for path in [
            r"C:\ProgramData\Package Cache",
            r"D:\PACKAGE CACHE\{id}\files",
            r"C:\Windows\System32\drivers",
            r"\\?\C:\Windows\system32",
            r"\\server\share\Package Cache\app",
        ] {
            assert!(excluded_directory(Path::new(path)), "{path}");
        }
        for path in [
            r"C:\Apps\System32Tools",
            r"D:\Apps\Package Cache Viewer",
            r"C:\Program Files\App",
        ] {
            assert!(!excluded_directory(Path::new(path)), "{path}");
        }
    }
    #[test]
    fn icon_locations_preserve_quoted_commas_and_resource_ids() {
        assert_eq!(
            icon_location(r#""C:\Apps\A,B\program.dll",-42"#),
            Some((r"C:\Apps\A,B\program.dll".into(), -42))
        );
        assert_eq!(
            icon_location(r"C:\Apps\program.exe,3"),
            Some((r"C:\Apps\program.exe".into(), 3))
        );
        assert_eq!(
            icon_location(r"C:\Apps\program.ico"),
            Some((r"C:\Apps\program.ico".into(), 0))
        );
    }
}
