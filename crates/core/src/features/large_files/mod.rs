mod scan;
use crate::{common, jobs::Context, *};
use anyhow::{Context as _, Result, bail};
use cplus_platform_windows::{filesystem as winfs, processes, registry};
use std::{
    collections::BTreeSet,
    fs,
    path::{Path, PathBuf},
};
pub const MINIMUM: u64 = 30 * common::MIB + 1;
fn excluded_file_type(path: &Path) -> bool {
    matches!(
        path.extension()
            .unwrap_or_default()
            .to_string_lossy()
            .to_ascii_lowercase()
            .as_str(),
        "dll"
            | "sys"
            | "drv"
            | "ocx"
            | "efi"
            | "mui"
            | "ttf"
            | "ttc"
            | "otf"
            | "otc"
            | "fon"
            | "fnt"
            | "woff"
            | "woff2"
            | "eot"
            | "pfb"
            | "pfm"
    )
}
pub fn history(store: &Store) -> Vec<serde_json::Value> {
    use std::io::{BufRead, BufReader};
    let mut rows = std::collections::BTreeMap::new();
    if let Ok(file) = fs::File::open(store.root.join("bigfiles-history.jsonl")) {
        for line in BufReader::new(file).lines().map_while(Result::ok) {
            if let Ok(row) = serde_json::from_str::<serde_json::Value>(&line)
                && let Some(id) = row["id"].as_str()
            {
                rows.insert(id.to_owned(), row);
            }
        }
    }
    for row in store
        .read::<Vec<serde_json::Value>>("rust-bigfiles-history.json")
        .unwrap_or_default()
    {
        if let Some(id) = row["id"].as_str() {
            rows.insert(id.to_owned(), row);
        }
    }
    let mut rows = rows.into_values().collect::<Vec<_>>();
    rows.sort_by(|a, b| {
        b["time"]
            .as_f64()
            .unwrap_or(0.0)
            .total_cmp(&a["time"].as_f64().unwrap_or(0.0))
    });
    rows
}
pub fn category(path: &Path) -> Option<&'static str> {
    let ext = path
        .extension()
        .unwrap_or_default()
        .to_string_lossy()
        .to_lowercase();
    match ext.as_str() {
        "rar" | "zip" | "7z" | "tar" | "iso" | "xz" | "gz" | "bz2" => Some("archives"),
        "exe" => Some("installers"),
        "log" => Some("logs"),
        "mp4" | "ts" | "mov" | "mxf" | "mpg" | "flv" | "wmv" | "avi" | "m4v" | "f4v" | "3gp"
        | "asf" | "mkv" => Some("videos"),
        "mp3" | "ogg" | "wav" | "wma" | "ape" | "flac" | "aac" | "ac3" | "mmf" | "amr" | "m4a"
        | "m4r" | "wv" | "mp2" => Some("audio"),
        "doc" | "docx" | "xls" | "xlsx" | "ppt" | "pptx" | "csv" | "pdf" | "odt" | "ods"
        | "odp" | "rtf" | "txt" => Some("documents"),
        "tmp" => Some("temporary"),
        "jpg" | "jpeg" | "png" | "gif" | "bmp" | "webp" | "tif" | "tiff" | "heic" | "raw"
        | "psd" => Some("images"),
        _ => Some("other"),
    }
}
pub use scan::{scan, scan_root, scan_root_with_mode};
fn block_reason(
    path: &Path,
    info: &winfs::Identity,
    installed: &[PathBuf],
    active: &[processes::Process],
) -> String {
    if excluded_file_type(path) {
        return "Program components, fonts and drivers are protected".into();
    }
    if info.links > 1 {
        return "Hard-linked file; display only".into();
    }
    if common::protected_data_root(path) || installed.iter().any(|r| common::inside(path, r)) {
        return "Installed or protected file; use App Mover".into();
    }
    if active
        .iter()
        .any(|p| common::key(Path::new(&p.path)) == common::key(path))
    {
        return "Application is running".into();
    }
    String::new()
}
pub fn delete(
    store: &Store,
    report: &ScanResult,
    selected: &[String],
    ctx: &Context,
) -> OperationResult {
    if report.cancelled || report.token.is_empty() {
        return OperationResult {
            errors: vec![Issue {
                path: String::new(),
                message: "Scan again before deleting or moving files".into(),
            }],
            ..Default::default()
        };
    }
    let installed = registry::installed()
        .iter()
        .map(super::uninstall::location)
        .filter(|p| !p.as_os_str().is_empty())
        .collect::<Vec<_>>();
    let active = match processes::list() {
        Ok(p) => p,
        Err(e) => {
            return OperationResult {
                errors: vec![Issue {
                    path: String::new(),
                    message: e.to_string(),
                }],
                ..Default::default()
            };
        }
    };
    let entries = report
        .files
        .iter()
        .filter(|f| selected.contains(&f.id) || selected.contains(&f.category))
        .map(|f| {
            let mut f = f.clone();
            let reason = block_reason(&f.path, &f.identity, &installed, &active);
            if !reason.is_empty() {
                f.blocked = reason;
            }
            f
        })
        .collect::<Vec<_>>();
    let mut result = common::delete_entries(&entries, &report.rules, ctx);
    result.bytes = entries
        .iter()
        .filter(|file| result.succeeded.contains(&file.id))
        .map(|file| file.allocated)
        .sum();
    let _ = store.append(
        "large_file_delete",
        "large_file_delete",
        result.count,
        result.bytes,
        result.ok(),
    );
    result
}
pub fn move_files(
    store: &Store,
    report: &ScanResult,
    chosen: &BTreeSet<String>,
    destination: &Path,
    ctx: &Context,
) -> OperationResult {
    if report.cancelled || report.token.is_empty() {
        return OperationResult {
            errors: vec![Issue {
                path: String::new(),
                message: "Scan again before deleting or moving files".into(),
            }],
            ..Default::default()
        };
    }
    let mut result = OperationResult::default();
    let mut records = store
        .read::<Vec<serde_json::Value>>("rust-bigfiles-history.json")
        .unwrap_or_default();
    let files: Vec<_> = report
        .files
        .iter()
        .filter(|f| chosen.contains(&f.id))
        .collect();
    for (index, file) in files.iter().enumerate() {
        if ctx.cancel.cancelled() {
            break;
        }
        let attempt = (|| -> Result<PathBuf> {
            common::validate_file(file)?;
            let installed = registry::installed()
                .iter()
                .map(super::uninstall::location)
                .filter(|p| !p.as_os_str().is_empty())
                .collect::<Vec<_>>();
            let processes = processes::list()?;
            let fresh = block_reason(&file.path, &file.identity, &installed, &processes);
            if !fresh.is_empty() {
                bail!("{fresh}");
            }
            if !file.blocked.is_empty() {
                bail!("{}", file.blocked);
            }
            validate_destination(&file.path, destination)?;
            let value = file.path.to_string_lossy();
            let plain = value.trim_start_matches(r"\\?\");
            let drive = plain
                .chars()
                .next()
                .context("Missing source drive")?
                .to_string();
            let relative = plain.get(3..).context("Invalid local source path")?;
            let target = destination.join(drive).join(relative);
            if target.exists() {
                bail!("Destination already exists");
            }
            let record = serde_json::json!({"id":common::token(),"path":file.path,"target":target,"size":file.size,"time":chrono::Utc::now().timestamp(),"status":"copying"});
            records.push(record);
            store.write("rust-bigfiles-history.json", &records)?;
            common::copy_verified(&file.path, &target, ctx, |bytes| {
                ctx.set(
                    "copy",
                    file.path.display().to_string(),
                    bytes,
                    Some(file.size),
                    result.bytes + bytes,
                )
            })?;
            common::validate_file(file)?;
            fs::remove_file(&file.path)?;
            let row = records.last_mut().unwrap();
            row["status"] = "success".into();
            store.write("rust-bigfiles-history.json", &records)?;
            Ok(target)
        })();
        match attempt {
            Ok(_) => {
                result.count += 1;
                result.bytes += file.size;
                result.succeeded.push(file.id.clone());
            }
            Err(e) => {
                if let Some(row) = records.iter_mut().rev().find(|r| {
                    r["path"] == file.path.to_string_lossy().as_ref() && r["status"] == "copying"
                }) {
                    row["status"] = if ctx.cancel.cancelled() {
                        "cancelled"
                    } else {
                        "failed"
                    }
                    .into();
                    row["error"] = e.to_string().into();
                    let _ = store.write("rust-bigfiles-history.json", &records);
                }
                result.errors.push(Issue {
                    path: file.path.display().to_string(),
                    message: e.to_string(),
                });
            }
        };
        ctx.set(
            "move",
            file.path.display().to_string(),
            index as u64 + 1,
            Some(files.len() as u64),
            result.bytes,
        );
    }
    result.cancelled = ctx.cancel.cancelled();
    let _ = store.append(
        "large_file_move",
        "large_file_move",
        result.count,
        result.bytes,
        result.ok(),
    );
    result
}
pub fn validate_destination(source: &Path, destination: &Path) -> Result<()> {
    if destination.to_string_lossy().starts_with(r"\\") {
        bail!("Choose a local drive folder");
    }
    if !destination.is_absolute()
        || destination.as_os_str().is_empty()
        || destination.parent().is_none()
    {
        bail!("Choose a folder on another local drive");
    }
    common::no_links(destination)?;
    if common::inside(destination, source) || common::inside(source, destination) {
        bail!("Source and destination overlap");
    }
    let existing = destination
        .ancestors()
        .find(|p| p.exists())
        .context("Destination drive does not exist")?;
    let a = winfs::disk(source)?;
    let b = winfs::disk(existing)?;
    if a.root.eq_ignore_ascii_case(&b.root) {
        bail!("Destination must be on another volume");
    }
    if b.free < fs::metadata(source)?.len() + common::MIB {
        bail!("Not enough free space on the destination drive");
    }
    Ok(())
}
