use super::uninstall::{self, Measurement};
use crate::{common, jobs::Context, *};
use anyhow::{Context as _, Result, bail};
use cplus_platform_windows::{filesystem as winfs, processes, registry, shell};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs,
    io::{BufRead, BufReader},
    path::{Path, PathBuf},
};

pub fn candidates(ctx: &Context) -> Vec<AppInfo> {
    collect_candidates(None, ctx)
}
pub(crate) fn candidates_for(feature: Feature, ctx: &Context) -> Vec<AppInfo> {
    collect_candidates(Some(feature), ctx)
}
fn collect_candidates(feature: Option<Feature>, ctx: &Context) -> Vec<AppInfo> {
    let mut found = BTreeMap::<String, AppInfo>::new();
    let mut icons = uninstall::IconCache::new();
    for row in uninstall::installed_applications(ctx) {
        if ctx.cancel.cancelled() {
            break;
        }
        if row.system
            || !row.parent.is_empty()
            || ["update", "hotfix", "security update"]
                .contains(&row.release_type.to_lowercase().as_str())
        {
            continue;
        }
        let root = uninstall::location(&row);
        if !root.is_dir()
            || uninstall::excluded_directory(&root)
            || uninstall::is_shared(&root)
            || common::no_links(&root).is_err()
        {
            continue;
        }
        let id = common::hash(common::key(&root).as_bytes());
        found.entry(common::key(&root)).or_insert(AppInfo {
            id,
            name: row.name.clone(),
            version: row.version.clone(),
            publisher: row.publisher.clone(),
            installed: row.date.clone(),
            path: root.clone(),
            root_identity: winfs::identity(&root).ok(),
            record: row,
            ..Default::default()
        });
    }
    let mut values: Vec<_> = found.into_values().collect();
    values.sort_by_key(|a| a.path.components().count());
    let mut unique: Vec<AppInfo> = vec![];
    for app in values {
        if !unique.iter().any(|a| common::inside(&app.path, &a.path)) {
            unique.push(app);
        }
    }
    if let Some(feature) = feature {
        unique.retain(|app| {
            (feature != Feature::AppMover || common::inside(&app.path, &common::system_drive()))
                && guard_root(&app.path, false).is_ok()
        });
    }
    for app in &mut unique {
        if ctx.cancel.cancelled() {
            break;
        }
        app.icon = if feature == Some(Feature::AppMover) {
            uninstall::registered_icon(&app.record, &mut icons)
        } else {
            uninstall::application_icon(&app.record, &app.path, &mut icons)
        };
    }
    ctx.set(
        "discover",
        "",
        unique.len() as u64,
        Some(unique.len() as u64),
        0,
    );
    unique
}
pub fn guard_root(root: &Path, allow_junction: bool) -> Result<()> {
    let canonical = common::canonical(root);
    if uninstall::excluded_directory(root)
        || uninstall::excluded_directory(&canonical)
        || uninstall::is_shared(&canonical)
    {
        bail!("System or shared directories are protected");
    }
    if !root.is_absolute() || !root.is_dir() || uninstall::is_shared(root) {
        bail!("System, shared or application directories are protected");
    }
    for parent in if allow_junction {
        root.parent()
            .into_iter()
            .flat_map(Path::ancestors)
            .collect::<Vec<_>>()
    } else {
        root.ancestors().collect()
    } {
        if parent.exists() && winfs::identity(parent)?.redirected() {
            bail!("Installation path contains a junction or symbolic link");
        }
    }
    for variable in ["SystemRoot", "ProgramData"] {
        if let Some(p) = std::env::var_os(variable)
            && common::inside(root, &PathBuf::from(p))
        {
            bail!("System components are protected");
        }
    }
    if cfg!(debug_assertions) {
        let project = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
        if common::inside(&project, root) || common::inside(root, &project) {
            bail!("The current project is protected");
        }
    }
    if !allow_junction && winfs::disk(root)?.filesystem.to_uppercase() != "NTFS" {
        bail!("This operation requires an NTFS volume");
    }
    Ok(())
}
pub fn active(root: &Path) -> Result<bool> {
    Ok(processes::list()?
        .iter()
        .any(|p| common::inside(Path::new(&p.path), root)))
}
pub fn require_idle(root: &Path) -> Result<()> {
    if active(root)? {
        bail!("Close the application before continuing");
    }
    Ok(())
}
fn has_service(root: &Path) -> bool {
    registry::service_images()
        .iter()
        .filter_map(|v| uninstall::executable(v))
        .any(|p| common::inside(&p, root))
}
fn environment_paths() -> Vec<PathBuf> {
    let mut values: Vec<_> = std::env::vars_os()
        .map(|(_, value)| value.to_string_lossy().into_owned())
        .collect();
    for (hive, path) in [
        (registry::HKEY_CURRENT_USER, r"Environment"),
        (
            registry::HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    ] {
        if let Ok(key) = registry::Key::open(hive, path, 0, false) {
            for (name, kind, _) in key.values() {
                if matches!(
                    kind,
                    windows_sys::Win32::System::Registry::REG_SZ
                        | windows_sys::Win32::System::Registry::REG_EXPAND_SZ
                ) {
                    values.push(key.string(&name));
                }
            }
        }
    }
    environment_value_paths(values)
}

fn environment_value_paths(values: impl IntoIterator<Item = String>) -> Vec<PathBuf> {
    values
        .into_iter()
        .flat_map(|value| {
            value
                .split(';')
                .filter_map(|part| {
                    let expanded = common::expand_env(part.trim().trim_matches('"')).ok()?;
                    let path = PathBuf::from(expanded.trim().trim_matches('"'));
                    path.is_absolute().then(|| common::canonical(&path))
                })
                .collect::<Vec<_>>()
        })
        .collect()
}

pub fn scan(ctx: &Context) -> Result<ScanResult> {
    let candidates = candidates_for(Feature::AppMover, ctx);
    let environment = environment_paths();
    let service_paths: Vec<_> = registry::service_images()
        .iter()
        .filter_map(|v| uninstall::executable(v))
        .collect();
    let mut result = ScanResult {
        feature: Feature::AppMover,
        token: common::token(),
        ..Default::default()
    };
    let mut services = 0;
    for mut app in candidates {
        if ctx.cancel.cancelled() {
            break;
        }
        if environment
            .iter()
            .any(|path| common::inside(path, &app.path))
        {
            continue;
        }
        if service_paths
            .iter()
            .any(|path| common::inside(path, &app.path))
        {
            services += 1;
            continue;
        }
        // Registry sizes are estimates; move_one verifies only the selected trees.
        app.logical = app.record.estimated_kib.saturating_mul(1024);
        if fill_missing_size(&mut app, ctx).is_err() {
            continue;
        }
        result.apps.push(app);
    }
    result.apps.sort_by_key(|a| std::cmp::Reverse(a.logical));
    result.counts.insert("excluded_services".into(), services);
    result.counts.insert("service_snapshots".into(), 1);
    finish_scan(&mut result, ctx);
    Ok(result)
}
fn fill_missing_size(app: &mut AppInfo, ctx: &Context) -> Result<()> {
    if app.logical != 0 {
        return Ok(());
    }
    ctx.cancel.check()?;
    let mut size = 0_u64;
    let mut files = 0_u64;
    let mut error = None;
    let summary =
        winfs::directory::scan(
            &app.path,
            &|| ctx.cancel.cancelled(),
            &mut |event| match event {
                winfs::directory::Event::File { path, entry } => {
                    size = size.saturating_add(entry.size);
                    files += 1;
                    ctx.path_progress("inventory", path, files, size, false);
                }
                winfs::directory::Event::Issue { error: issue, .. } => {
                    error.get_or_insert(issue);
                }
                winfs::directory::Event::Skipped { .. } => {
                    error.get_or_insert_with(|| {
                        std::io::Error::other(
                            "Installation directory contains unreadable, linked or changing files",
                        )
                    });
                }
            },
        );
    ctx.cancel.check()?;
    match summary {
        Ok(summary) => {
            if summary.cancelled {
                bail!("Operation cancelled");
            }
            if let Some(error) = error {
                return Err(error.into());
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::Unsupported => {
            // Discard partial batch totals before using the existing lightweight walker.
            let data = uninstall::directory_size(&app.path, ctx);
            ctx.cancel.check()?;
            if data.missing != 0 || data.unreadable != 0 {
                bail!("Installation directory contains unreadable, linked or changing files");
            }
            size = data.logical;
        }
        Err(error) => return Err(error.into()),
    }
    app.logical = size;
    Ok(())
}

#[cfg(test)]
mod size_tests {
    use super::*;

    #[test]
    fn uninstaller_in_package_cache_is_not_an_application_directory() {
        let temp = tempfile::tempdir().unwrap();
        let cache = temp.path().join("Package Cache").join("{python-installer}");
        fs::create_dir_all(&cache).unwrap();
        let exe = cache.join("python-uninstall.exe");
        fs::write(&exe, b"fixture").unwrap();
        let row = registry::InstalledRecord {
            name: "Python 3.13.12 (64-bit)".into(),
            uninstall: format!("\"{}\" /uninstall", exe.display()),
            ..Default::default()
        };
        let root = uninstall::location(&row);
        assert_eq!(common::key(&root), common::key(&cache));
        assert!(uninstall::excluded_directory(&root));
        assert!(guard_root(&root, false).is_err());
        assert!(guard_root(&root, true).is_err());
        assert!(!uninstall::excluded_directory(
            &temp.path().join("Package Cache Viewer")
        ));
    }

    #[test]
    fn environment_references_match_application_paths_not_similarly_named_siblings() {
        let paths = environment_value_paths([
            r#""C:\Tools\Python312\Scripts";C:\Tools\Python312\;relative;;"#.into(),
            r"C:\Java\jdk-21".into(),
            "%SystemRoot%\\System32".into(),
            "%CPLUS_NONEXISTENT_ENV_TEST%\\bin".into(),
        ]);
        assert!(
            paths
                .iter()
                .any(|path| common::inside(path, Path::new(r"C:\Tools\Python312")))
        );
        assert!(
            paths
                .iter()
                .any(|path| common::inside(path, Path::new(r"c:/java/jdk-21")))
        );
        assert!(
            !paths
                .iter()
                .any(|path| common::inside(path, Path::new(r"C:\Tools\Python31")))
        );
        assert!(
            !paths
                .iter()
                .any(|path| common::inside(path, Path::new(r"C:\Tools\Other")))
        );
        assert!(paths.iter().all(|path| path.is_absolute()));
        let system = PathBuf::from(std::env::var_os("SystemRoot").unwrap()).join("System32");
        assert!(
            paths
                .iter()
                .any(|path| common::key(path) == common::key(&common::canonical(&system)))
        );
    }

    #[test]
    fn linked_directory_rejects_the_size_even_when_some_files_are_readable() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("app");
        let outside = temp.path().join("shared");
        fs::create_dir(&root).unwrap();
        fs::create_dir(&outside).unwrap();
        fs::write(root.join("readable.bin"), b"fixture").unwrap();
        winfs::create_junction(&root.join("linked"), &outside).unwrap();
        let mut app = AppInfo {
            path: root.clone(),
            ..Default::default()
        };
        assert!(fill_missing_size(&mut app, &Context::new(Feature::AppMover)).is_err());
        assert_eq!(app.logical, 0);
        fs::remove_dir(root.join("linked")).unwrap();
    }

    #[test]
    fn missing_size_uses_batch_totals_but_registered_sizes_skip_the_directory() {
        let temp = tempfile::tempdir().unwrap();
        let nested = temp.path().join("子目录");
        fs::create_dir(&nested).unwrap();
        fs::write(temp.path().join("a.bin"), [0; 123]).unwrap();
        fs::write(nested.join("b.bin"), [0; 456]).unwrap();
        let mut app = AppInfo {
            path: temp.path().into(),
            ..Default::default()
        };
        let ctx = Context::new(Feature::AppMover);
        fill_missing_size(&mut app, &ctx).unwrap();
        assert_eq!(app.logical, 579);
        assert_eq!((app.physical, app.files), (0, 0));
        assert!(!app.complete);
        app.path = temp.path().join("absent");
        fill_missing_size(&mut app, &ctx).unwrap();
        assert_eq!(app.logical, 579);
        app.logical = 0;
        assert!(fill_missing_size(&mut app, &ctx).is_err());
        assert_eq!(app.logical, 0);
        app.path = temp.path().into();
        ctx.cancel.cancel();
        assert!(fill_missing_size(&mut app, &ctx).is_err());
        assert_eq!(app.logical, 0);
    }
}
pub(crate) fn finish_scan(result: &mut ScanResult, ctx: &Context) {
    // A late snapshot is fresher than querying each application before the remaining walk.
    if !ctx.cancel.cancelled() {
        let processes = processes::list();
        result.counts.insert("process_snapshots".into(), 1);
        for app in &mut result.apps {
            if processes
                .as_ref()
                .map(|rows| {
                    rows.iter()
                        .any(|p| common::inside(Path::new(&p.path), &app.path))
                })
                .unwrap_or(true)
            {
                app.blocked = "Application is running; close it first".into();
            } else if needs_admin(&app.path) {
                app.blocked = "Administrator permission required".into();
            }
        }
    }
    result.cancelled = ctx.cancel.cancelled();
    result.elapsed_ms = ctx.elapsed_ms();
    if result.cancelled {
        result.token.clear();
        result.apps.clear();
    }
}
pub(crate) fn record_scan_stats(result: &mut ScanResult, stats: &uninstall::MeasurementStats) {
    for (key, value) in [
        ("batch_pages", stats.batch_pages),
        ("measured_directories", stats.directories),
        ("identity_queries", stats.identity_queries),
        ("allocation_queries", stats.allocation_queries),
        ("wof_queries", stats.wof_queries),
        ("refreshed_metadata", stats.refreshed_metadata),
        ("directory_fallbacks", u64::from(stats.fallback)),
    ] {
        *result.counts.entry(key.into()).or_default() += value;
    }
}
pub fn needs_admin(path: &Path) -> bool {
    !shell::is_admin()
        && ["ProgramFiles", "ProgramFiles(x86)"]
            .into_iter()
            .filter_map(std::env::var_os)
            .any(|r| common::inside(path, &PathBuf::from(r)))
}
pub fn apply_measurement(app: &mut AppInfo, data: &Measurement) {
    app.logical = data.logical;
    app.physical = data.physical;
    app.files = data.files.len() as u64;
    app.modified = data.modified;
    app.complete = data.complete();
    app.missing = data.missing;
    app.unreadable = data.unreadable;
    app.compressed = data.compressed;
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TreeFile {
    pub relative: PathBuf,
    pub identity: winfs::Identity,
    pub digest: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Tree {
    pub root_identity: winfs::Identity,
    pub files: Vec<TreeFile>,
    pub directories: Vec<PathBuf>,
    pub bytes: u64,
}
pub fn snapshot(root: &Path, ctx: &Context) -> Result<Tree> {
    common::no_links(root)?;
    let root_identity = winfs::identity(root)?;
    let measured = uninstall::measure(root, ctx);
    ctx.cancel.check()?;
    if !measured.complete() {
        bail!("Installation directory contains unreadable, linked or changing files");
    }
    if measured.files.is_empty() {
        bail!("Installation directory contains no files");
    }
    let mut files = vec![];
    for (index, (file, identity)) in measured.files.iter().enumerate() {
        ctx.cancel.check()?;
        let digest = common::digest_file(file, ctx)?;
        if winfs::identity(file)? != *identity {
            bail!("Source changed during verification");
        }
        files.push(TreeFile {
            relative: file.strip_prefix(root)?.into(),
            identity: identity.clone(),
            digest,
        });
        ctx.set(
            "verify",
            file.display().to_string(),
            index as u64 + 1,
            Some(measured.files.len() as u64),
            measured.logical,
        );
    }
    files.sort_by(|a, b| a.relative.cmp(&b.relative));
    let mut directories = vec![];
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        for entry in fs::read_dir(&dir)? {
            let entry = entry?;
            if entry.file_type()?.is_dir() {
                common::no_links(&entry.path())?;
                directories.push(entry.path().strip_prefix(root)?.into());
                stack.push(entry.path());
            }
        }
    }
    directories.sort();
    Ok(Tree {
        root_identity,
        files,
        directories,
        bytes: measured.logical,
    })
}
pub fn equal_tree(root: &Path, expected: &Tree, ctx: &Context) -> Result<()> {
    let current = snapshot(root, ctx)?;
    if current.directories != expected.directories
        || current.files.len() != expected.files.len()
        || current.files.iter().zip(&expected.files).any(|(a, b)| {
            a.relative != b.relative || a.identity.size != b.identity.size || a.digest != b.digest
        })
    {
        bail!("Directory content verification failed");
    }
    Ok(())
}
pub fn copy_tree(root: &Path, target: &Path, expected: &Tree, ctx: &Context) -> Result<()> {
    if target.exists() {
        bail!("Destination already exists");
    }
    fs::create_dir(target)?;
    for relative in &expected.directories {
        fs::create_dir_all(target.join(relative))?;
    }
    let mut copied = 0;
    for file in &expected.files {
        let source = root.join(&file.relative);
        let destination = target.join(&file.relative);
        let count = common::copy_verified(&source, &destination, ctx, |bytes| {
            ctx.set(
                "copy",
                source.display().to_string(),
                copied + bytes,
                Some(expected.bytes),
                copied + bytes,
            )
        })?;
        if let Ok(metadata) = fs::metadata(&source) {
            let handle = fs::OpenOptions::new().write(true).open(&destination)?;
            let mut times = fs::FileTimes::new();
            if let Ok(modified) = metadata.modified() {
                times = times.set_modified(modified);
            }
            let _ = handle.set_times(times);
            fs::set_permissions(&destination, metadata.permissions())?;
        }
        copied += count;
    }
    equal_tree(target, expected, ctx)?;
    equal_tree(root, expected, ctx)?;
    Ok(())
}
pub fn remove_verified_tree(root: &Path, expected: &Tree, ctx: &Context) -> Result<()> {
    common::no_links(root)?;
    equal_tree(root, expected, ctx)?;
    let observed = winfs::identity(root)?;
    if observed.volume != expected.root_identity.volume
        || observed.index != expected.root_identity.index
    {
        bail!("Backup directory identity changed");
    }
    for file in &expected.files {
        let path = root.join(&file.relative);
        let current = winfs::identity(&path)?;
        if current.volume != file.identity.volume
            || current.index != file.identity.index
            || common::digest_file(&path, ctx)? != file.digest
        {
            bail!("Backup file changed; retained");
        }
        if fs::metadata(&path)?.permissions().readonly() {
            winfs::clear_readonly(&path)?;
        }
        fs::remove_file(path)?;
    }
    let mut dirs = expected.directories.clone();
    dirs.sort_by_key(|d| std::cmp::Reverse(d.components().count()));
    for relative in dirs {
        fs::remove_dir(root.join(relative))?;
    }
    fs::remove_dir(root)?;
    Ok(())
}

pub fn history(store: &Store) -> Vec<serde_json::Value> {
    let mut rows = store
        .read::<Vec<serde_json::Value>>("rust-software-migrations.json")
        .unwrap_or_default();
    if let Ok(file) = fs::File::open(store.root.join("software-migrations.jsonl")) {
        let mut old = BTreeMap::new();
        for line in BufReader::new(file)
            .lines()
            .map_while(std::result::Result::ok)
        {
            if let Ok(row) = serde_json::from_str::<serde_json::Value>(&line)
                && let Some(id) = row["id"].as_str()
            {
                old.insert(id.to_owned(), row);
            }
        }
        for (_, row) in old {
            if !rows.iter().any(|r| r["id"] == row["id"]) {
                rows.push(row);
            }
        }
    }
    rows.sort_by(|a, b| {
        b["time"]
            .as_f64()
            .unwrap_or(0.0)
            .partial_cmp(&a["time"].as_f64().unwrap_or(0.0))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    rows
}
fn save_record(store: &Store, record: &serde_json::Value) -> Result<()> {
    let mut rows = history(store);
    if let Some(row) = rows.iter_mut().find(|r| r["id"] == record["id"]) {
        *row = record.clone();
    } else {
        rows.push(record.clone());
    }
    store.write("rust-software-migrations.json", &rows)
}

pub fn move_one(
    store: &Store,
    app: &AppInfo,
    destination: &Path,
    ctx: &Context,
) -> Result<serde_json::Value> {
    let source = &app.path;
    guard_root(source, false)?;
    require_idle(source)?;
    if has_service(source) {
        bail!("Applications containing system services cannot be moved");
    }
    if environment_paths()
        .iter()
        .any(|path| common::inside(path, source))
    {
        bail!("Applications referenced by environment variables cannot be moved");
    }
    if needs_admin(source) {
        bail!("Administrator permission required");
    }
    let now = winfs::identity(source)?;
    if app
        .root_identity
        .as_ref()
        .is_some_and(|old| old.volume != now.volume || old.index != now.index)
    {
        bail!("Installation directory changed; scan again");
    }
    super::large_files::validate_destination(source, destination)?;
    let existing = destination
        .ancestors()
        .find(|p| p.exists())
        .context("Destination does not exist")?;
    if winfs::disk(existing)?.filesystem.to_uppercase() != "NTFS" {
        bail!("Destination requires NTFS");
    }
    let initial = snapshot(source, ctx)?;
    if winfs::disk(existing)?.free < initial.bytes + common::MIB {
        bail!("Not enough free space");
    }
    let id = common::token();
    let safe_name: String = app
        .name
        .chars()
        .map(|c| {
            if "<>:\"/\\|?*".contains(c) || c.is_control() {
                '_'
            } else {
                c
            }
        })
        .take(60)
        .collect();
    let target = destination.join(format!(
        "{}-{}",
        safe_name.trim_matches([' ', '.']),
        &app.id[..8]
    ));
    let backup = source.with_file_name(format!(
        "{}.ccc-move-{id}",
        source.file_name().unwrap_or_default().to_string_lossy()
    ));
    if target.exists() || backup.exists() {
        bail!("Destination or backup exists; choose another folder");
    }
    let mut record = serde_json::json!({"id":id,"name":app.name,"source":source,"target":target,"backup":backup,"size":initial.bytes,"released":0,"time":chrono::Utc::now().timestamp(),"status":"preparing","ok":false,"restored":false});
    save_record(store, &record)?;
    let mut switched = false;
    let attempt = (|| -> Result<()> {
        fs::create_dir_all(destination)?;
        common::no_links(destination)?;
        copy_tree(source, &target, &initial, ctx)?;
        require_idle(source)?;
        ctx.cancel.check()?;
        record["status"] = "switching".into();
        save_record(store, &record)?;
        fs::rename(source, &backup)?;
        if let Err(error) = winfs::create_junction(source, &target) {
            fs::rename(&backup, source)?;
            return Err(error.into());
        }
        switched = true;
        record["ok"] = true.into();
        record["status"] = "cleaning_backup".into();
        save_record(store, &record)?;
        let cleanup = Context::new(Feature::AppMover);
        equal_tree(&target, &initial, &cleanup)?;
        remove_verified_tree(&backup, &initial, &cleanup)?;
        record["released"] = initial.bytes.into();
        record["status"] = "success".into();
        Ok(())
    })();
    if let Err(error) = attempt {
        record["error"] = error.to_string().into();
        record["status"] = if switched {
            "backup_retained"
        } else {
            "not_switched"
        }
        .into();
        save_record(store, &record)?;
        if !switched {
            return Err(error);
        }
    }
    save_record(store, &record)?;
    Ok(record)
}
pub fn move_apps(
    store: &Store,
    apps: &[AppInfo],
    destination: &Path,
    ctx: &Context,
) -> OperationResult {
    let mut result = OperationResult::default();
    let mut names = Vec::new();
    for app in apps {
        if ctx.cancel.cancelled() {
            break;
        }
        names.push(app.name.as_str());
        match move_one(store, app, destination, ctx) {
            Ok(row) => {
                result.count += 1;
                result.bytes += row["released"].as_u64().unwrap_or(0);
                result.succeeded.push(app.id.clone());
                if let Some(error) = row["error"].as_str() {
                    result.errors.push(Issue {
                        path: app.name.clone(),
                        message: error.into(),
                    });
                }
            }
            Err(error) => result.errors.push(Issue {
                path: app.name.clone(),
                message: error.to_string(),
            }),
        }
    }
    result.cancelled = ctx.cancel.cancelled();
    let _ = store.append(
        "software_move",
        &names.join("、"),
        result.count,
        result.bytes,
        result.ok(),
    );
    result
}
pub fn restore(store: &Store, id: &str, ctx: &Context) -> Result<OperationResult> {
    let mut record = history(store)
        .into_iter()
        .find(|r| r["id"] == id)
        .context("Move record not found")?;
    if record["ok"] != true || record["restored"] == true {
        bail!("This move cannot be restored again");
    }
    let source = PathBuf::from(record["source"].as_str().context("Missing source")?);
    let target = PathBuf::from(record["target"].as_str().context("Missing target")?);
    guard_root(&source, true)?;
    if !winfs::identity(&source)?.redirected()
        || common::key(&common::canonical(&source)) != common::key(&common::canonical(&target))
    {
        bail!("Original junction changed; automatic restore stopped");
    }
    guard_root(&target, false)?;
    require_idle(&target)?;
    let current = snapshot(&target, ctx)?;
    if winfs::disk(source.parent().context("Missing parent")?)?.free < current.bytes + common::MIB {
        bail!("Not enough space on original volume");
    }
    let staging = source.with_file_name(format!(
        "{}.ccc-restore-{}",
        source.file_name().unwrap_or_default().to_string_lossy(),
        common::token()
    ));
    copy_tree(&target, &staging, &current, ctx)?;
    ctx.cancel.check()?;
    require_idle(&target)?;
    if !winfs::identity(&source)?.redirected()
        || common::key(&common::canonical(&source)) != common::key(&common::canonical(&target))
    {
        bail!("Original junction changed; automatic restore stopped");
    }
    fs::remove_dir(&source)?;
    if let Err(error) = fs::rename(&staging, &source) {
        let _ = winfs::create_junction(&source, &target);
        return Err(error.into());
    }
    record["restored"] = true.into();
    record["status"] = "restored".into();
    record["released"] = 0.into();
    save_record(store, &record)?;
    store.append(
        "software_restore",
        record["name"].as_str().unwrap_or("software_restore"),
        1,
        0,
        true,
    )?;
    Ok(OperationResult {
        count: 1,
        succeeded: vec![id.into()],
        ..Default::default()
    })
}
