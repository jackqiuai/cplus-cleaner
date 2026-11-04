use super::*;
use cplus_platform_windows::filesystem::large_scan::{self, Event, ScanMode};

use crate::features::file_scan::Policy;

pub fn scan(ctx: &Context) -> Result<ScanResult> {
    scan_root(&common::system_drive(), MINIMUM, ctx)
}

pub fn scan_root(root: &Path, minimum: u64, ctx: &Context) -> Result<ScanResult> {
    scan_root_with_mode(root, minimum, ScanMode::Auto, ctx)
}

/// An explicit mode is exposed for read-only comparison probes and fixture tests.
pub fn scan_root_with_mode(
    root: &Path,
    minimum: u64,
    mode: ScanMode,
    ctx: &Context,
) -> Result<ScanResult> {
    if !root.is_absolute() {
        bail!("Scan root must be an absolute directory");
    }
    common::no_links(root)?;
    let root = fs::canonicalize(root)?;
    common::no_links(&root)?;
    let root_identity = winfs::identity(&root)?;
    if !root_identity.directory()
        || root_identity.redirected()
        || large_scan::remote(root_identity.attributes)
    {
        bail!("Scan root must be a local directory without links");
    }
    let installed = registry::installed()
        .into_iter()
        .map(|r| crate::features::uninstall::location(&r))
        .filter(|path| path.is_absolute())
        .collect::<Vec<_>>();
    let policy = Policy::new(&root, &installed)?;
    let active = processes::list().unwrap_or_default();
    let scope = common::token();
    let mut result = ScanResult {
        feature: Feature::LargeFiles,
        token: common::token(),
        ..Default::default()
    };
    result.rules.push(Rule {
        id: scope.clone(),
        category: String::new(),
        root: root.clone(),
        recursive: true,
        patterns: vec![],
        excluded: vec![],
        days: 0,
        root_identity: Some(root_identity.clone()),
        chat_app: None,
    });
    let mut seen = BTreeSet::new();
    let mut checked = 0;
    let mut queries = 0_u64;
    let mut rejected = 0_u64;
    let mut found_bytes = 0_u64;
    let mut summary = large_scan::scan(
        &root,
        minimum,
        mode,
        &|| ctx.cancel.cancelled(),
        &|path| policy.excludes(path) || excluded_file_type(path),
        &mut |event| match event {
            Event::Visit {
                path,
                checked: count,
            } => {
                checked = count;
                ctx.scan_progress(path, checked, result.files.len() as u64, found_bytes, false);
            }
            Event::Issue { path, error } => {
                if result.diagnostics.len() < 100 {
                    result.diagnostics.push(Issue {
                        path: path.display().to_string(),
                        message: error.to_string(),
                    });
                }
            }
            Event::Candidate {
                path,
                expected_index,
            } => {
                let attempt = (|| -> Result<()> {
                    if !common::inside(path, &root)
                        || policy.excludes(path)
                        || excluded_file_type(path)
                        || !seen.insert(common::key(path))
                    {
                        return Ok(());
                    }
                    queries += 1;
                    common::no_links(path)?;
                    let info = winfs::identity(path)?;
                    if info.volume != root_identity.volume
                        || info.directory()
                        || info.redirected()
                        || large_scan::remote(info.attributes)
                        || expected_index.is_some_and(|id| id != info.index)
                    {
                        bail!("File changed or became unavailable during scanning");
                    }
                    if info.size < minimum {
                        return Ok(());
                    }
                    let blocked = super::block_reason(path, &info, &installed, &active);
                    let allocated = match winfs::allocated(path) {
                        Ok(bytes) => bytes,
                        Err(error) => {
                            if result.diagnostics.len() < 100 {
                                result.diagnostics.push(Issue {
                                    path: path.display().to_string(),
                                    message: format!("Disk usage estimate uses file size: {error}"),
                                });
                            }
                            info.size
                        }
                    };
                    if blocked.is_empty() {
                        found_bytes = found_bytes.saturating_add(allocated);
                    }
                    result.files.push(FileEntry {
                        id: common::hash(
                            format!("{}:{}:{}", common::key(path), info.volume, info.index)
                                .as_bytes(),
                        ),
                        path: path.to_path_buf(),
                        category: category(path).unwrap_or("other").into(),
                        size: info.size,
                        allocated,
                        identity: info,
                        scope: scope.clone(),
                        blocked,
                    });
                    ctx.scan_progress(path, checked, result.files.len() as u64, found_bytes, false);
                    Ok(())
                })();
                if let Err(error) = attempt {
                    rejected += 1;
                    if result.diagnostics.len() < 100 {
                        result.diagnostics.push(Issue {
                            path: path.display().to_string(),
                            message: error.to_string(),
                        });
                    }
                }
            }
        },
    )?;
    summary.skipped = summary.skipped.saturating_add(rejected);
    result
        .counts
        .insert("candidate_validations".into(), queries);
    result.counts.insert("checked".into(), summary.checked);
    result.counts.insert("skipped".into(), summary.skipped);
    result.cancelled = summary.cancelled || ctx.cancel.cancelled();
    result.large_scan = Some(summary);
    let now = winfs::identity(&root)?;
    if now.volume != root_identity.volume || now.index != root_identity.index {
        bail!("Scan root changed");
    }
    result.files.sort_by(|a, b| {
        b.size
            .cmp(&a.size)
            .then_with(|| common::key(&a.path).cmp(&common::key(&b.path)))
    });
    for id in [
        "archives",
        "installers",
        "logs",
        "videos",
        "audio",
        "documents",
        "images",
        "temporary",
        "other",
    ] {
        result.categories.push(Category {
            id: id.into(),
            name: id.into(),
            group: id.into(),
            default_selected: false,
            ..Default::default()
        });
    }
    super::super::drive_cleanup::fill_categories(&mut result.categories, &result.files);
    result.categories.retain(|c| c.count > 0);
    if result.cancelled {
        result.token.clear();
        result.files.clear();
        result.categories.clear();
        result.rules.clear();
    }
    ctx.scan_progress(
        &root,
        checked,
        result.files.len() as u64,
        result.reclaimable_bytes(),
        true,
    );
    result.elapsed_ms = ctx.elapsed_ms();
    Ok(result)
}
