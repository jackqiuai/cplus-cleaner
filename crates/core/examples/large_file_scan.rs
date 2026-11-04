//! Read-only scan probe. Pass an explicit directory; never deletes or moves files.
use cplus_core::{Feature, features::large_files, jobs::Context};
use sha2::{Digest, Sha256};
use std::{path::Path, time::Instant};

fn main() -> anyhow::Result<()> {
    let root = std::env::args()
        .nth(1)
        .ok_or_else(|| anyhow::anyhow!("Pass an absolute directory"))?;
    let ctx = Context::new(Feature::LargeFiles);
    let started = Instant::now();
    let mode = match std::env::args().nth(2).as_deref() {
        Some("directory") => cplus_platform_windows::filesystem::large_scan::ScanMode::Directory,
        Some("layout") => cplus_platform_windows::filesystem::large_scan::ScanMode::Layout,
        _ => cplus_platform_windows::filesystem::large_scan::ScanMode::Auto,
    };
    let report = if std::env::args().nth(2).as_deref() == Some("legacy") {
        legacy_candidates(Path::new(&root), &ctx)?
    } else {
        large_files::scan_root_with_mode(Path::new(&root), large_files::MINIMUM, mode, &ctx)?
    };
    let mut files = report.files.iter().collect::<Vec<_>>();
    files.sort_by(|a, b| a.path.cmp(&b.path));
    let mut digest = Sha256::new();
    for file in files {
        digest.update(cplus_core::common::key(&file.path));
        digest.update(file.size.to_le_bytes());
        digest.update(file.allocated.to_le_bytes());
    }
    println!(
        "{}",
        serde_json::json!({
            "elapsed_ms": started.elapsed().as_millis(),
            "files": report.files.len(), "logical_bytes": report.bytes(),
            "allocated_bytes": report.allocated_bytes(),
            "blocked": report.files.iter().filter(|f| !f.blocked.is_empty()).count(),
            "diagnostics": report.diagnostics.len(), "cancelled": report.cancelled,
            "checked": ctx.snapshot().done, "discovery": report.large_scan, "candidate_validations": report.counts.get("candidate_validations"),
            "reclaimable_bytes": report.reclaimable_bytes(), "digest": format!("{:x}", digest.finalize())
        })
    );
    Ok(())
}

/// The pre-change candidate loop, retained only in this read-only comparison executable.
/// It intentionally keeps the old extension and directory-name filters.
fn legacy_candidates(root: &Path, ctx: &Context) -> anyhow::Result<cplus_core::ScanResult> {
    use cplus_core::{FileEntry, ScanResult, common};
    use cplus_platform_windows::{filesystem, processes, registry};
    use std::{collections::BTreeSet, fs};
    let _installed = registry::installed();
    let _active = processes::list().unwrap_or_default();
    let mut report = ScanResult {
        feature: Feature::LargeFiles,
        ..Default::default()
    };
    let mut stack = vec![root.to_path_buf()];
    let mut seen = BTreeSet::new();
    let mut queries = 0_u64;
    while let Some(directory) = stack.pop() {
        if common::no_links(&directory).is_err() || common::self_protected(&directory) {
            continue;
        }
        let Ok(entries) = fs::read_dir(&directory) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            queries += 1;
            let Ok(info) = filesystem::identity(&path) else {
                continue;
            };
            if info.redirected() || common::self_protected(&path) {
                continue;
            }
            if info.directory() {
                if !["Program Files*", "*Microsoft*", "System32", "$RECYCLE.BIN"]
                    .iter()
                    .any(|pattern| common::wildcard(pattern, &entry.file_name().to_string_lossy()))
                {
                    stack.push(path);
                }
                continue;
            }
            if info.size < large_files::MINIMUM {
                continue;
            }
            let extension = path
                .extension()
                .unwrap_or_default()
                .to_string_lossy()
                .to_lowercase();
            if !matches!(
                extension.as_str(),
                "rar"
                    | "zip"
                    | "7z"
                    | "tar"
                    | "iso"
                    | "xz"
                    | "gz"
                    | "bz2"
                    | "exe"
                    | "log"
                    | "mp4"
                    | "ts"
                    | "mov"
                    | "mxf"
                    | "mpg"
                    | "flv"
                    | "wmv"
                    | "avi"
                    | "m4v"
                    | "f4v"
                    | "3gp"
                    | "asf"
                    | "mkv"
                    | "mp3"
                    | "ogg"
                    | "wav"
                    | "wma"
                    | "ape"
                    | "flac"
                    | "aac"
                    | "ac3"
                    | "mmf"
                    | "amr"
                    | "m4a"
                    | "m4r"
                    | "wv"
                    | "mp2"
                    | "doc"
                    | "docx"
                    | "xls"
                    | "xlsx"
                    | "ppt"
                    | "pptx"
                    | "csv"
                    | "tmp"
            ) {
                continue;
            }
            if !seen.insert(common::key(&path)) {
                continue;
            }
            report.files.push(FileEntry {
                id: String::new(),
                category: String::new(),
                allocated: filesystem::allocated(&path).unwrap_or(info.size),
                size: info.size,
                identity: info,
                path,
                scope: String::new(),
                blocked: String::new(),
            });
            ctx.set(
                "scan",
                String::new(),
                report.files.len() as u64,
                None,
                report.bytes(),
            );
        }
    }
    report
        .counts
        .insert("candidate_validations".into(), queries);
    Ok(report)
}
