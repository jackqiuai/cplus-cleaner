use super::Measurement;
use crate::{Issue, common, jobs::Context};
use cplus_platform_windows::filesystem::{self as winfs, directory, large_scan};
use std::{collections::BTreeSet, io, path::Path};

#[derive(Clone, Copy, Debug, Default)]
pub enum MeasureMode {
    #[default]
    Auto,
    Directory,
}

#[derive(Clone, Debug, Default)]
pub struct MeasurementStats {
    pub root_identity: Option<winfs::Identity>,
    pub batch_pages: u64,
    pub directories: u64,
    pub identity_queries: u64,
    pub allocation_queries: u64,
    pub wof_queries: u64,
    pub refreshed_metadata: u64,
    pub fallback: bool,
    pub cancelled: bool,
}

pub fn measure_for_scan(root: &Path, ctx: &Context) -> Measurement {
    measure_for_scan_with_mode(root, ctx, MeasureMode::Auto)
}

pub fn measure_for_scan_with_mode(root: &Path, ctx: &Context, mode: MeasureMode) -> Measurement {
    let mut out = Measurement::default();
    let root_id = match common::no_links(root).and_then(|_| Ok(winfs::identity(root)?)) {
        Ok(info) if info.directory() => info,
        Ok(_) => {
            failure(
                &mut out,
                root,
                "Application root is not a directory".into(),
                false,
            );
            return out;
        }
        Err(error) => {
            failure(&mut out, root, error.to_string(), false);
            return out;
        }
    };
    let mut seen = BTreeSet::new();
    let native = if matches!(mode, MeasureMode::Auto) {
        directory::scan(root, &|| ctx.cancel.cancelled(), &mut |event| match event {
            directory::Event::File { path, entry } => {
                collect(&mut out, &mut seen, path, Some(entry), root_id.volume, ctx)
            }
            directory::Event::Skipped { path, remote } => {
                if remote {
                    failure(
                        &mut out,
                        path,
                        "Remote placeholder is not available locally".into(),
                        false,
                    );
                } else {
                    out.linked = true;
                    failure_message(
                        &mut out,
                        path,
                        "Linked files or directories are not eligible".into(),
                    );
                }
            }
            directory::Event::Issue { path, error } => failure(
                &mut out,
                path,
                error.to_string(),
                error.kind() == io::ErrorKind::NotFound,
            ),
        })
    } else {
        Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "Directory mode requested",
        ))
    };
    match native {
        Ok(summary) => {
            out.stats.batch_pages = summary.pages;
            out.stats.directories = summary.directories;
            out.stats.cancelled = summary.cancelled;
        }
        Err(error) if error.kind() == io::ErrorKind::Unsupported => {
            // Replay a complete root, never combine partial native output with a fallback.
            out = Measurement::default();
            out.stats.fallback = true;
            seen.clear();
            match large_scan::scan(
                root,
                0,
                large_scan::ScanMode::Directory,
                &|| ctx.cancel.cancelled(),
                &|_| false,
                &mut |event| match event {
                    large_scan::Event::Candidate { path, .. } => {
                        collect(&mut out, &mut seen, path, None, root_id.volume, ctx)
                    }
                    large_scan::Event::Issue { path, error } => failure(
                        &mut out,
                        path,
                        error.to_string(),
                        error.kind() == io::ErrorKind::NotFound,
                    ),
                    large_scan::Event::Visit { .. } => {}
                },
            ) {
                Ok(summary) => {
                    out.stats.directories = summary.directories;
                    out.stats.cancelled = summary.cancelled;
                    out.linked |= summary.skipped_links > 0;
                    if summary.skipped_remote > 0 {
                        out.unreadable += summary.skipped_remote;
                        failure_message(&mut out, root, "Remote placeholders were skipped".into());
                    }
                }
                Err(error) => failure(
                    &mut out,
                    root,
                    error.to_string(),
                    error.kind() == io::ErrorKind::NotFound,
                ),
            }
        }
        Err(error) => failure(
            &mut out,
            root,
            error.to_string(),
            error.kind() == io::ErrorKind::NotFound,
        ),
    }
    if !ctx.cancel.cancelled() {
        let valid = common::no_links(root).and_then(|_| Ok(winfs::identity(root)?));
        if !valid.is_ok_and(|now| now.volume == root_id.volume && now.index == root_id.index) {
            failure(
                &mut out,
                root,
                "Application root changed during measurement".into(),
                false,
            );
        }
    }
    out.stats.cancelled |= ctx.cancel.cancelled();
    out.stats.root_identity = Some(root_id);
    ctx.path_progress("measure", root, out.files.len() as u64, out.physical, true);
    out
}

fn collect(
    out: &mut Measurement,
    seen: &mut BTreeSet<(u32, u64)>,
    path: &Path,
    hint: Option<&directory::Entry>,
    volume: u32,
    ctx: &Context,
) {
    if ctx.cancel.cancelled() {
        return;
    }
    out.stats.identity_queries += 1;
    let info = match winfs::identity(path) {
        Ok(info) => info,
        Err(error) => {
            failure(
                out,
                path,
                error.to_string(),
                error.kind() == io::ErrorKind::NotFound,
            );
            return;
        }
    };
    if info.directory()
        || info.redirected()
        || info.volume != volume
        || large_scan::remote(info.attributes)
    {
        out.linked |= info.redirected();
        failure(
            out,
            path,
            "File changed or is unavailable during measurement".into(),
            false,
        );
        return;
    }
    if let Some(hint) = hint {
        let low = u64::from_le_bytes(hint.id[..8].try_into().unwrap());
        let id_mismatch = hint.id[8..].iter().all(|b| *b == 0) && low != 0 && low != info.index;
        if id_mismatch {
            failure(out, path, "File changed during measurement".into(), false);
            return;
        }
        // Directory metadata can lag behind an open writer. A current query of the same
        // object is authoritative; content-changing operations still take a fresh preflight.
        if hint.size != info.size
            || hint.modified != info.modified
            || hint.attributes != info.attributes
        {
            out.stats.refreshed_metadata += 1;
        }
    }
    out.logical = out.logical.saturating_add(info.size);
    out.modified = out.modified.max(info.modified);
    if seen.insert((info.volume, info.index)) {
        out.stats.allocation_queries += 1;
        match winfs::allocated(path) {
            Ok(bytes) => out.physical = out.physical.saturating_add(bytes),
            Err(error) => failure(out, path, error.to_string(), false),
        }
    }
    out.linked |= info.links > 1;
    if !out.compressed {
        if info.attributes & 0x800 != 0 {
            out.compressed = true;
        } else {
            out.stats.wof_queries += 1;
            out.compressed = winfs::wof_compressed(path);
        }
    }
    out.files.push((path.to_path_buf(), info));
    ctx.path_progress("measure", path, out.files.len() as u64, out.physical, false);
}
fn failure(out: &mut Measurement, path: &Path, message: String, missing: bool) {
    if missing {
        out.missing += 1;
    } else {
        out.unreadable += 1;
    }
    failure_message(out, path, message);
}
fn failure_message(out: &mut Measurement, path: &Path, message: String) {
    if out.errors.len() < 20 {
        out.errors.push(Issue {
            path: common::display_path(path),
            message,
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn current_metadata_refreshes_stale_directory_hints_but_replacement_is_rejected() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("owned.dat");
        std::fs::write(&path, b"current content").unwrap();
        let info = winfs::identity(&path).unwrap();
        let mut id = [0; 16];
        id[..8].copy_from_slice(&info.index.to_le_bytes());
        let mut hint = directory::Entry {
            name: "owned.dat".into(),
            size: 1,
            allocation: 0,
            modified: 1,
            attributes: info.attributes,
            tag: 0,
            id,
        };
        let mut out = Measurement::default();
        let ctx = Context::new(crate::Feature::AppMover);
        collect(
            &mut out,
            &mut BTreeSet::new(),
            &path,
            Some(&hint),
            info.volume,
            &ctx,
        );
        assert!(out.complete());
        assert_eq!(out.logical, info.size);
        assert_eq!(out.stats.refreshed_metadata, 1);
        hint.id[..8].copy_from_slice(&(info.index ^ 1).to_le_bytes());
        let mut changed = Measurement::default();
        collect(
            &mut changed,
            &mut BTreeSet::new(),
            &path,
            Some(&hint),
            info.volume,
            &ctx,
        );
        assert!(!changed.complete());
        assert!(changed.files.is_empty());
    }
}
