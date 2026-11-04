//! Metadata-first chat traversal. Keep full identities only for eligible files;
//! physical allocation queries are unnecessary for logical category totals.
use super::protected;
use crate::{FileEntry, Issue, Rule, common, jobs::Context};
use cplus_platform_windows::filesystem;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    os::windows::fs::MetadataExt,
    path::{Path, PathBuf},
};

pub(super) fn scan(
    rules: &[Rule],
    ctx: &Context,
) -> (Vec<FileEntry>, Vec<Issue>, BTreeMap<String, u64>) {
    let mut files = Vec::new();
    let mut issues = Vec::new();
    let mut seen_roots = BTreeSet::new();
    let mut seen_paths = BTreeSet::new();
    let mut seen_ids = BTreeSet::new();
    let mut checked = 0;
    let mut bytes = 0;
    let mut last_path = PathBuf::new();
    for rule in rules {
        if ctx.cancel.cancelled() {
            break;
        }
        if !seen_roots.insert(common::key(&rule.root)) {
            continue;
        }
        ctx.scan_progress(&rule.root, checked, files.len() as u64, bytes, true);
        if let Err(error) = common::no_links(&rule.root) {
            record(&mut issues, &rule.root, error);
            continue;
        }
        if let Some(old) = &rule.root_identity {
            match filesystem::identity(&rule.root) {
                Ok(now) if now.volume == old.volume && now.index == old.index => {}
                _ => {
                    record(&mut issues, &rule.root, "Scan root changed");
                    continue;
                }
            }
        }
        let mut stack = vec![rule.root.clone()];
        while let Some(dir) = stack.pop() {
            if ctx.cancel.cancelled() {
                break;
            }
            last_path.clone_from(&dir);
            ctx.scan_progress(&dir, checked, files.len() as u64, bytes, false);
            // Recheck each directory immediately before opening it, rather
            // than reopening every ancestor for every descendant file.
            match fs::symlink_metadata(&dir) {
                Ok(meta) if meta.is_dir() && meta.file_attributes() & 0x400 == 0 => {}
                Ok(_) => continue,
                Err(error) => {
                    record(&mut issues, &dir, error);
                    continue;
                }
            }
            let entries = match fs::read_dir(&dir) {
                Ok(entries) => entries,
                Err(error) => {
                    record(&mut issues, &dir, error);
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
                        record(&mut issues, &dir, error);
                        continue;
                    }
                };
                let path = entry.path();
                last_path.clone_from(&path);
                ctx.scan_progress(&path, checked, files.len() as u64, bytes, false);
                let meta = match entry.metadata() {
                    Ok(meta) => meta,
                    Err(error) => {
                        record(&mut issues, &path, error);
                        continue;
                    }
                };
                if meta.is_dir() {
                    if rule.recursive
                        && meta.file_attributes() & 0x400 == 0
                        && !rule
                            .excluded
                            .iter()
                            .any(|p| common::wildcard(p, &entry.file_name().to_string_lossy()))
                    {
                        stack.push(path);
                    }
                    continue;
                }
                if !meta.is_file() {
                    continue;
                }
                checked += 1;
                if protected(&path)
                    || !common::matches_rule(&path, rule)
                    || common::self_protected(&path)
                    || !seen_paths.insert(common::key(&path))
                {
                    continue;
                }
                let info = match filesystem::identity(&path) {
                    Ok(info) => info,
                    Err(error) => {
                        record(&mut issues, &path, error);
                        continue;
                    }
                };
                if info.directory()
                    || info.redirected()
                    || info.links != 1
                    || !seen_ids.insert((info.volume, info.index))
                {
                    continue;
                }
                let unix = (info.modified / 10_000_000).saturating_sub(11_644_473_600);
                if rule.days > 0
                    && unix
                        < (chrono::Utc::now().timestamp().max(0) as u64)
                            .saturating_sub(rule.days as u64 * 86400)
                {
                    continue;
                }
                let id = common::hash(
                    format!("{}:{}:{}", common::key(&path), info.volume, info.index).as_bytes(),
                );
                bytes += info.size;
                files.push(FileEntry {
                    id,
                    path,
                    category: rule.category.clone(),
                    size: info.size,
                    allocated: info.size,
                    identity: info,
                    scope: rule.id.clone(),
                    blocked: String::new(),
                });
            }
        }
    }
    ctx.scan_progress(&last_path, checked, files.len() as u64, bytes, true);
    let counts = BTreeMap::from([
        ("files_checked".into(), checked),
        ("matched_files".into(), files.len() as u64),
    ]);
    (files, issues, counts)
}

fn record(issues: &mut Vec<Issue>, path: &Path, error: impl ToString) {
    if issues.len() < 100 {
        issues.push(Issue {
            path: common::display_path(path),
            message: error.to_string(),
        });
    }
}
