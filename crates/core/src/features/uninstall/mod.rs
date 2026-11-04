mod inventory;
mod scan_measurement;
pub use scan_measurement::{
    MeasureMode, MeasurementStats, measure_for_scan, measure_for_scan_with_mode,
};
pub mod residue;
use crate::{common, jobs::Context, *};
use anyhow::{Context as _, Result, bail};
use cplus_platform_windows::{
    filesystem as winfs, processes,
    registry::{self, InstalledRecord},
    shell,
};
pub(crate) use inventory::installed_applications;
pub(crate) use inventory::registered_icon;
pub use inventory::{IconCache, application_icon, directory_size, excluded_directory};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    os::windows::process::CommandExt,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

pub fn parse_command(value: &str) -> Option<(PathBuf, String)> {
    let value = common::expand_env(value).ok()?;
    let value = value.trim();
    if let Some(rest) = value.strip_prefix('"') {
        let end = rest.find('"')?;
        return Some((PathBuf::from(&rest[..end]), rest[end + 1..].trim().into()));
    }
    let end = value
        .as_bytes()
        .windows(4)
        .position(|part| part.eq_ignore_ascii_case(b".exe"))?
        + 4;
    Some((PathBuf::from(&value[..end]), value[end..].trim().into()))
}
pub fn executable(value: &str) -> Option<PathBuf> {
    parse_command(value).map(|p| p.0).or_else(|| {
        let value = common::expand_env(value).ok()?;
        let plain = value.trim().trim_matches('"').split(',').next()?.to_owned();
        let p = PathBuf::from(plain);
        p.is_absolute().then_some(p)
    })
}
pub fn location(row: &InstalledRecord) -> PathBuf {
    if let Ok(path) = common::expand_env(row.location.trim().trim_matches('"')) {
        let p = PathBuf::from(path);
        if p.is_absolute() && p.is_dir() {
            return common::canonical(&p);
        }
    }
    if let Some((exe, _)) = parse_command(&row.uninstall)
        && exe.is_absolute()
        && !exe
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .eq_ignore_ascii_case("msiexec.exe")
        && let Some(parent) = exe.parent()
        && parent.is_dir()
    {
        return common::canonical(parent);
    }
    PathBuf::new()
}
#[derive(Clone, Debug, Default)]
pub struct Measurement {
    pub stats: MeasurementStats,
    pub files: Vec<(PathBuf, winfs::Identity)>,
    pub logical: u64,
    pub physical: u64,
    pub modified: u64,
    pub missing: u64,
    pub unreadable: u64,
    pub linked: bool,
    pub compressed: bool,
    pub errors: Vec<Issue>,
}
impl Measurement {
    pub fn complete(&self) -> bool {
        self.unreadable == 0 && self.missing == 0 && !self.linked && !self.stats.cancelled
    }
}
pub fn measure(root: &Path, ctx: &Context) -> Measurement {
    let mut result = Measurement::default();
    let mut stack = vec![root.to_path_buf()];
    let mut identities = BTreeSet::new();
    while let Some(dir) = stack.pop() {
        if ctx.cancel.cancelled() {
            break;
        }
        if common::no_links(&dir).is_err() {
            result.linked = true;
            continue;
        }
        let entries = match fs::read_dir(&dir) {
            Ok(v) => v,
            Err(e) => {
                if e.kind() == std::io::ErrorKind::NotFound {
                    result.missing += 1;
                } else {
                    result.unreadable += 1;
                }
                if result.errors.len() < 20 {
                    result.errors.push(Issue {
                        path: dir.display().to_string(),
                        message: e.to_string(),
                    });
                }
                continue;
            }
        };
        for entry in entries.flatten() {
            if ctx.cancel.cancelled() {
                break;
            }
            let path = entry.path();
            match winfs::identity(&path) {
                Ok(info) => {
                    if info.redirected() {
                        result.linked = true;
                        continue;
                    }
                    if info.directory() {
                        stack.push(path);
                        continue;
                    }
                    result.modified = result.modified.max(info.modified);
                    result.logical += info.size;
                    if identities.insert((info.volume, info.index)) {
                        match winfs::allocated(&path) {
                            Ok(n) => result.physical += n,
                            Err(e) => {
                                result.unreadable += 1;
                                if result.errors.len() < 20 {
                                    result.errors.push(Issue {
                                        path: path.display().to_string(),
                                        message: e.to_string(),
                                    });
                                }
                            }
                        }
                    }
                    if info.links > 1 {
                        result.linked = true;
                    }
                    result.compressed |=
                        info.attributes & 0x800 != 0 || winfs::wof_compressed(&path);
                    result.files.push((path.clone(), info));
                    ctx.set(
                        "measure",
                        path.display().to_string(),
                        result.files.len() as u64,
                        None,
                        result.physical,
                    );
                }
                Err(e) => {
                    if e.kind() == std::io::ErrorKind::NotFound {
                        result.missing += 1;
                    } else {
                        result.unreadable += 1;
                    }
                    if result.errors.len() < 20 {
                        result.errors.push(Issue {
                            path: path.display().to_string(),
                            message: e.to_string(),
                        });
                    }
                }
            }
        }
    }
    result
}
pub fn scan(ctx: &Context) -> Result<ScanResult> {
    let mut result = ScanResult {
        feature: Feature::Uninstall,
        token: common::token(),
        ..Default::default()
    };
    let entries = installed_applications(ctx);
    let total = entries.len() as u64;
    let mut sizes = BTreeMap::new();
    let mut icons = IconCache::new();
    for (index, row) in entries.into_iter().enumerate() {
        if ctx.cancel.cancelled() {
            break;
        }
        let Some(root) = inventory::application_root(&row) else {
            continue;
        };
        ctx.set(
            "inventory",
            &row.name,
            index as u64,
            Some(total),
            result.apps.iter().map(|a| a.logical).sum(),
        );
        let measurement = if root.is_dir() && !is_shared(&root) {
            sizes
                .entry(common::key(&root))
                .or_insert_with(|| directory_size(&root, ctx))
                .clone()
        } else {
            inventory::SizeSummary::default()
        };
        let size = if measurement.files > 0 {
            measurement.logical
        } else {
            row.estimated_kib.saturating_mul(1024)
        };
        let id = common::hash(format!("{}:{}", row.hive, row.key).as_bytes());
        let image = application_icon(&row, &root, &mut icons);
        let name = row.name.clone();
        let version = row.version.clone();
        let publisher = row.publisher.clone();
        let installed = row.date.clone();
        result.diagnostics.extend(measurement.errors.clone());
        result.apps.push(AppInfo {
            id,
            name,
            version,
            publisher,
            path: root.clone(),
            modified: measurement.modified,
            installed,
            logical: size,
            physical: 0,
            files: measurement.files,
            complete: measurement.unreadable == 0 && measurement.missing == 0,
            missing: measurement.missing,
            unreadable: measurement.unreadable,
            blocked: String::new(),
            compressed: false,
            root_identity: winfs::identity(&root).ok(),
            record: row,
            icon: image,
        });
    }
    result.apps.sort_by(|a, b| {
        b.installed
            .cmp(&a.installed)
            .then(a.name.to_lowercase().cmp(&b.name.to_lowercase()))
    });
    result.cancelled = ctx.cancel.cancelled();
    result.elapsed_ms = ctx.elapsed_ms();
    Ok(result)
}
pub fn is_shared(root: &Path) -> bool {
    let key = common::key(root);
    if key.is_empty()
        || key == common::key(&common::system_drive())
        || key == common::key(&common::home())
    {
        return true;
    }
    for name in ["SystemRoot", "ProgramData"] {
        if let Some(path) = std::env::var_os(name)
            && common::inside(root, &PathBuf::from(path))
        {
            return true;
        }
    }
    for name in [
        "ProgramFiles",
        "ProgramFiles(x86)",
        "LOCALAPPDATA",
        "APPDATA",
    ] {
        if let Some(path) = std::env::var_os(name) {
            let path = PathBuf::from(path);
            if key == common::key(&path) {
                return true;
            }
            if ["Common Files", "WindowsApps"]
                .iter()
                .any(|c| common::inside(root, &path.join(c)))
            {
                return true;
            }
        }
    }
    common::contains_running_app(root)
}

pub fn uninstall(store: &Store, items: &[AppInfo], ctx: &Context) -> OperationResult {
    let mut result = OperationResult::default();
    let msi_switch = regex::Regex::new(r"(?i)(^|\s)/i(\s|\{)").unwrap();
    for (index, item) in items.iter().enumerate() {
        if ctx.cancel.cancelled() {
            break;
        }
        let attempt = (|| -> Result<()> {
            let fresh = registry::installed()
                .into_iter()
                .find(|r| {
                    r.hive == item.record.hive
                        && r.view == item.record.view
                        && r.key == item.record.key
                })
                .context("Uninstall registration changed; scan again")?;
            if fresh.uninstall != item.record.uninstall {
                bail!("Uninstall command changed; scan again");
            }
            let (mut executable, mut args) =
                parse_command(&fresh.uninstall).context("Invalid uninstall command")?;
            if executable
                .file_name()
                .is_some_and(|s| s.eq_ignore_ascii_case("msiexec.exe"))
            {
                executable = common::env_path("SystemRoot", r"C:\Windows".into())
                    .join("System32/msiexec.exe");
                args = msi_switch.replace(&args, "${1}/X${2}").into_owned();
            }
            if !executable.is_absolute() || !executable.is_file() {
                bail!("Publisher uninstaller is missing");
            }
            ctx.set(
                "uninstall",
                &item.name,
                index as u64,
                Some(items.len() as u64),
                0,
            );
            let mut child = match processes::hidden(&executable).raw_arg(&args).spawn() {
                Ok(c) => Some(c),
                Err(e) if e.raw_os_error() == Some(740) => {
                    let exit = shell::elevated_visible(&executable, &args)?;
                    if ![0, 1641, 3010].contains(&exit) {
                        bail!("Publisher uninstaller returned {exit}");
                    }
                    None
                }
                Err(e) => return Err(e.into()),
            };
            let started = Instant::now();
            if let Some(child) = child.as_mut() {
                loop {
                    if let Some(status) = child.try_wait()? {
                        if !status.success()
                            && !status.code().is_some_and(|c| [1641, 3010].contains(&c))
                        {
                            bail!(
                                "Publisher uninstaller returned {}",
                                status.code().unwrap_or(-1)
                            );
                        }
                        break;
                    }
                    ctx.set(
                        "uninstall",
                        &item.name,
                        index as u64,
                        Some(items.len() as u64),
                        0,
                    );
                    std::thread::sleep(Duration::from_millis(150));
                    if started.elapsed() > Duration::from_secs(60 * 60) {
                        bail!("Uninstaller is still running; check the publisher window");
                    }
                }
            }
            let verification = Instant::now();
            while registry::installed()
                .iter()
                .any(|r| r.hive == item.record.hive && r.key == item.record.key)
                && verification.elapsed() < Duration::from_secs(20)
                && !ctx.cancel.cancelled()
            {
                ctx.set(
                    "uninstall",
                    &item.name,
                    index as u64,
                    Some(items.len() as u64),
                    0,
                );
                std::thread::sleep(Duration::from_millis(500));
            }
            if registry::installed()
                .iter()
                .any(|r| r.hive == item.record.hive && r.key == item.record.key)
            {
                bail!(
                    "The application is still registered; finish the publisher uninstaller or restart Windows"
                );
            }
            Ok(())
        })();
        match attempt {
            Ok(()) => {
                result.count += 1;
                result.succeeded.push(item.id.clone());
            }
            Err(e) => result.errors.push(Issue {
                path: item.name.clone(),
                message: e.to_string(),
            }),
        }
    }
    result.cancelled = ctx.cancel.cancelled();
    let _ = store.append(
        "software_uninstall",
        "software_uninstall",
        result.count,
        0,
        result.ok(),
    );
    result
}
