use super::{
    hashing::{self, Stage},
    *,
};
use crate::{features::file_scan::Policy, jobs::Context};
use anyhow::{Result, bail};
use cplus_platform_windows::{
    filesystem::{
        self as winfs,
        large_scan::{self, Event, ScanMode},
    },
    registry,
};

pub fn scan_locations(locations: &[Location], minimum: u64, ctx: &Context) -> Result<ScanResult> {
    scan_locations_with_mode(locations, minimum, ScanMode::Auto, ctx)
}
pub fn scan_locations_with_mode(
    locations: &[Location],
    minimum: u64,
    mode: ScanMode,
    ctx: &Context,
) -> Result<ScanResult> {
    if ctx.cancel.cancelled() {
        return Ok(ScanResult {
            feature: Feature::Duplicates,
            cancelled: true,
            ..Default::default()
        });
    }
    if locations.is_empty() {
        bail!("Choose at least one scan location");
    }
    let installed: Vec<_> = registry::installed()
        .into_iter()
        .filter(|r| !r.location.is_empty())
        .map(|r| PathBuf::from(r.location))
        .collect();
    let mut normalized = Vec::<Location>::new();
    for location in locations {
        ctx.cancel.check()?;
        common::no_links(&location.path)?;
        let path = std::fs::canonicalize(&location.path)?;
        if !path.is_dir() {
            bail!("Choose a folder or drive");
        }
        if let Some(existing) = normalized
            .iter_mut()
            .find(|l| common::key(&l.path) == common::key(&path))
        {
            existing.protected |= location.protected;
        } else {
            normalized.push(Location {
                path,
                protected: location.protected,
            });
        }
    }
    normalized.sort_by_key(|l| common::key(&l.path));
    let workers = normalized
        .iter()
        .map(|l| winfs::content::hash_worker_limit(&l.path))
        .min()
        .unwrap_or(1)
        .min(
            std::thread::available_parallelism()
                .map(usize::from)
                .unwrap_or(1),
        )
        .clamp(1, 4);
    let mut summary = Summary {
        locations: normalized.clone(),
        minimum,
        workers,
        ..Default::default()
    };
    let mut report = ScanResult {
        feature: Feature::Duplicates,
        ..Default::default()
    };
    let mut sizes = BTreeMap::<u64, Vec<FileEntry>>::new();
    let mut seen_paths = BTreeSet::new();
    let mut seen_ids = BTreeSet::new();
    for location in &normalized {
        if ctx.cancel.cancelled() {
            break;
        }
        let root = &location.path;
        let policy = Policy::new(root, &installed)?;
        if policy.excludes(root) {
            bail!("System and installed application folders are protected");
        }
        let root_identity = winfs::identity(root)?;
        let scope = common::token();
        report.rules.push(Rule {
            id: scope.clone(),
            category: "duplicates".into(),
            root: root.clone(),
            recursive: true,
            patterns: vec![],
            excluded: vec![],
            days: 0,
            root_identity: Some(root_identity.clone()),
            chat_app: None,
        });
        let broad = common::key(root) == common::key(Path::new(&winfs::disk(root)?.root))
            || common::inside(&common::home(), root);
        // Explicit child scopes own their subtree and filtering rules, including hidden roots
        // and narrow binary-file scans that would be excluded during broad discovery.
        let delegated: Vec<_> = normalized
            .iter()
            .filter(|l| l.path != *root && common::inside(&l.path, root))
            .map(|l| &l.path)
            .collect();
        let excluded = |path: &Path| {
            policy.excludes(path)
                || candidate_excluded(path, root, broad)
                || delegated.iter().any(|child| common::inside(path, child))
        };
        let prefix = summary.checked;
        let mut checked = prefix;
        let native = large_scan::scan(
            root,
            minimum,
            mode,
            &|| ctx.cancel.cancelled(),
            &excluded,
            &mut |event| match event {
                Event::Visit {
                    path,
                    checked: count,
                } => {
                    if !common::inside(path, root) {
                        return;
                    }
                    checked = prefix.saturating_add(count);
                    ctx.scan_progress(path, checked, summary.candidates, 0, false);
                }
                Event::Issue { path, error } => {
                    summary.unreadable += 1;
                    issue(&mut report, path, error.to_string());
                }
                Event::Candidate {
                    path,
                    expected_index,
                } => {
                    if excluded(path)
                        || !common::inside(path, root)
                        || !seen_paths.insert(common::key(path))
                    {
                        return;
                    }
                    let attempt = (|| -> Result<Option<FileEntry>> {
                        common::no_links(path)?;
                        let info = winfs::identity(path)?;
                        if info.directory()
                            || info.redirected()
                            || large_scan::remote(info.attributes)
                            || info.volume != root_identity.volume
                            || expected_index.is_some_and(|id| id != info.index)
                        {
                            bail!("File changed or became unavailable during scanning");
                        }
                        if info.size < minimum {
                            return Ok(None);
                        }
                        if info.links != 1 {
                            summary.hard_links += 1;
                            summary.skipped += 1;
                            return Ok(None);
                        }
                        if !seen_ids.insert((info.volume, info.index)) {
                            summary.skipped += 1;
                            return Ok(None);
                        }
                        let protected = normalized
                            .iter()
                            .any(|l| l.protected && common::inside(path, &l.path));
                        Ok(Some(FileEntry {
                            id: common::hash(
                                format!("{}:{}:{}", common::key(path), info.volume, info.index)
                                    .as_bytes(),
                            ),
                            path: path.into(),
                            category: String::new(),
                            size: info.size,
                            allocated: info.size,
                            identity: info,
                            scope: scope.clone(),
                            blocked: if protected {
                                "Protected comparison location".into()
                            } else {
                                String::new()
                            },
                        }))
                    })();
                    match attempt {
                        Ok(Some(file)) => {
                            summary.candidates += 1;
                            sizes.entry(file.size).or_default().push(file);
                        }
                        Ok(None) => {}
                        Err(error) => {
                            summary.skipped += 1;
                            summary.unreadable += 1;
                            issue(&mut report, path, error.to_string());
                        }
                    }
                }
            },
        )?;
        summary.checked = prefix.saturating_add(native.checked);
        summary.skipped = summary.skipped.saturating_add(native.skipped);
        summary.strategies.push(native.strategy);
        if let Some(reason) = native.fallback {
            summary.fallbacks.push(reason);
        }
    }
    ctx.scan_progress(
        &normalized[0].path,
        summary.checked,
        summary.candidates,
        0,
        true,
    );
    let candidates: Vec<_> = sizes
        .into_values()
        .filter(|v| v.len() > 1)
        .flatten()
        .collect();
    let sampled = hashing::run(&candidates, Stage::Sample, workers, ctx);
    summary.sampled = sampled.hashes.iter().flatten().count() as u64;
    summary.sample_bytes = sampled.bytes;
    record_failures(&mut report, &mut summary, &candidates, &sampled.failures);
    let mut buckets = BTreeMap::<(u64, [u8; 32]), Vec<FileEntry>>::new();
    for (file, hash) in candidates.into_iter().zip(sampled.hashes) {
        if let Some(hash) = hash {
            buckets.entry((file.size, hash)).or_default().push(file);
        }
    }
    let candidates: Vec<_> = buckets
        .into_values()
        .filter(|v| v.len() > 1)
        .flatten()
        .collect();
    let full = hashing::run(&candidates, Stage::Full, workers, ctx);
    summary.hashed = full.hashes.iter().flatten().count() as u64;
    summary.hash_bytes = full.bytes;
    record_failures(&mut report, &mut summary, &candidates, &full.failures);
    let mut groups = BTreeMap::<(u64, [u8; 32]), Vec<FileEntry>>::new();
    for (file, hash) in candidates.into_iter().zip(full.hashes) {
        if let Some(hash) = hash {
            groups.entry((file.size, hash)).or_default().push(file);
        }
    }
    let total = groups.len() as u64;
    for (index, ((_, hash), files)) in groups.into_iter().enumerate() {
        if ctx.cancel.cancelled() {
            break;
        }
        ctx.set(
            "Preparing duplicate results",
            "",
            index as u64,
            Some(total),
            0,
        );
        let mut live = Vec::new();
        for mut file in files {
            if let Err(error) = common::validate_file(&file) {
                summary.changed += 1;
                summary.skipped += 1;
                issue(&mut report, &file.path, error.to_string());
                continue;
            }
            file.allocated = match winfs::allocated(&file.path) {
                Ok(n) => n,
                Err(error) => {
                    summary.estimated_files.insert(file.id.clone());
                    issue(
                        &mut report,
                        &file.path,
                        format!("Disk usage estimate uses file size: {error}"),
                    );
                    file.size
                }
            };
            live.push(file);
        }
        if live.len() < 2 {
            continue;
        }
        let digest: String = hash.iter().map(|b| format!("{b:02x}")).collect();
        live.sort_by_key(|f| common::key(&f.path));
        for file in &mut live {
            file.category = digest.clone();
        }
        report.categories.push(Category {
            id: digest.clone(),
            group: digest,
            name: live[0]
                .path
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .into(),
            count: live.len() as u64,
            ..Default::default()
        });
        report.files.extend(live);
    }
    for rule in &report.rules {
        if ctx.cancel.cancelled() {
            break;
        }
        common::no_links(&rule.root)?;
        let now = winfs::identity(&rule.root)?;
        if rule
            .root_identity
            .as_ref()
            .is_none_or(|old| now.volume != old.volume || now.index != old.index)
        {
            bail!("Scan root changed");
        }
    }
    report.cancelled = ctx.cancel.cancelled();
    report.elapsed_ms = ctx.elapsed_ms();
    report.duplicate_scan = Some(summary);
    if report.cancelled {
        report.rules.clear();
        report.files.clear();
        report.categories.clear();
    } else {
        report.token = common::token();
        refresh_categories(&mut report);
        ctx.set(
            "Preparing duplicate results",
            "",
            total,
            Some(total),
            report.bytes(),
        );
    }
    Ok(report)
}
fn candidate_excluded(path: &Path, root: &Path, broad: bool) -> bool {
    let Ok(relative) = path.strip_prefix(root) else {
        return true;
    };
    // Hidden descendants are excluded; an explicitly selected hidden root remains inspectable.
    if relative.parent().is_some_and(|p| {
        p.components()
            .any(|c| c.as_os_str().to_string_lossy().starts_with('.'))
    }) {
        return true;
    }
    let name = path.file_name().unwrap_or_default().to_string_lossy();
    if name.eq_ignore_ascii_case(".DS_Store")
        || (name.starts_with('.') && path.is_dir() && path != root)
    {
        return true;
    }
    if broad
        && path.extension().is_some_and(|ext| {
            ["bin", "dll", "jar"]
                .iter()
                .any(|e| ext.eq_ignore_ascii_case(e))
        })
        && !path.is_dir()
    {
        return true;
    }
    broad && transient(path) && !transient(root)
}
fn transient(path: &Path) -> bool {
    let mut parts = path.components().filter_map(|c| {
        if let std::path::Component::Normal(s) = c {
            Some(s)
        } else {
            None
        }
    });
    let first = parts.next();
    let _user = parts.next();
    let third = parts.next();
    first.is_some_and(|s| s.eq_ignore_ascii_case("Temp"))
        || (first.is_some_and(|s| s.eq_ignore_ascii_case("Users"))
            && third.is_some_and(|s| s.eq_ignore_ascii_case("AppData")))
}
fn record_failures(
    report: &mut ScanResult,
    summary: &mut Summary,
    files: &[FileEntry],
    failures: &[(usize, String, bool)],
) {
    for (index, message, changed) in failures {
        summary.skipped += 1;
        if *changed {
            summary.changed += 1;
        } else {
            summary.unreadable += 1;
        }
        issue(report, &files[*index].path, message.clone());
    }
}
fn issue(report: &mut ScanResult, path: &Path, message: String) {
    if report.diagnostics.len() < 100 {
        report.diagnostics.push(Issue {
            path: common::display_path(path),
            message,
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn broad_binary_filter_does_not_prune_a_user_directory_with_a_binary_suffix() {
        let temp = tempfile::tempdir().unwrap();
        let directory = temp.path().join("documents.bin");
        std::fs::create_dir(&directory).unwrap();
        let file = temp.path().join("library.dll");
        std::fs::write(&file, b"data").unwrap();
        assert!(!candidate_excluded(&directory, temp.path(), true));
        assert!(candidate_excluded(&file, temp.path(), true));
        assert!(!candidate_excluded(&file, temp.path(), false));
    }
}
