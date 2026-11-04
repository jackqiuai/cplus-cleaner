use super::{hashing, *};
use crate::{features::file_scan::Policy, jobs::Context};
use anyhow::{Result, bail};
use cplus_platform_windows::{
    filesystem::{self as winfs, content},
    registry,
};

pub fn delete(
    store: &Store,
    report: &ScanResult,
    chosen: &BTreeSet<String>,
    ctx: &Context,
) -> OperationResult {
    let mut result = OperationResult::default();
    let known: BTreeSet<_> = report.files.iter().map(|f| f.id.as_str()).collect();
    if report.feature != Feature::Duplicates
        || report.cancelled
        || report.token.is_empty()
        || report.duplicate_scan.is_none()
        || chosen.iter().any(|id| !known.contains(id.as_str()))
    {
        result.errors.push(Issue {
            path: String::new(),
            message: "Scan authorization expired; scan again".into(),
        });
        return result;
    }
    let installed: Vec<_> = registry::installed()
        .into_iter()
        .filter(|r| !r.location.is_empty())
        .map(|r| PathBuf::from(r.location))
        .collect();
    let mut read_bytes = 0_u64;
    let mut by_group = BTreeMap::<&str, Vec<&FileEntry>>::new();
    for file in &report.files {
        by_group.entry(&file.category).or_default().push(file);
    }
    for group in &report.categories {
        if ctx.cancel.cancelled() {
            break;
        }
        let files = by_group
            .get(group.id.as_str())
            .map(Vec::as_slice)
            .unwrap_or_default();
        let selected: Vec<_> = files
            .iter()
            .copied()
            .filter(|f| chosen.contains(&f.id))
            .collect();
        if selected.is_empty() {
            continue;
        }
        let error = |message: String| Issue {
            path: group.name.clone(),
            message,
        };
        if selected
            .iter()
            .any(|f| !f.blocked.is_empty() || is_protected(f, report))
        {
            result.errors.push(error(
                "Protected comparison location cannot be deleted".into(),
            ));
            continue;
        }
        // Keep the verified surviving object open, denying writes and deletion until this group
        // finishes. Merely counting an unselected path would not preserve a usable copy.
        let retained = files
            .iter()
            .copied()
            .filter(|f| !chosen.contains(&f.id))
            .find_map(|entry| {
                let attempt = (|| -> Result<_> {
                    validate_scope(entry, report, &installed)?;
                    let mut handle = content::open(&entry.path, &entry.identity, false)?;
                    let digest = hashing::verified_digest(entry, &mut handle, ctx, |n| {
                        read_bytes = read_bytes.saturating_add(n);
                        ctx.set(
                            "Rechecking duplicate copies",
                            common::display_path(&entry.path),
                            result.count,
                            Some(chosen.len() as u64),
                            read_bytes,
                        );
                    })?;
                    if digest != group.id {
                        bail!("Retained copy changed; scan again");
                    }
                    Ok(handle)
                })();
                attempt.ok().map(|handle| (entry, handle))
            });
        let Some((keeper, keeper_handle)) = retained else {
            result.errors.push(error(
                "Keep at least one unchanged copy in each group".into(),
            ));
            continue;
        };
        for entry in selected {
            if ctx.cancel.cancelled() {
                break;
            }
            let attempt = (|| -> Result<u64> {
                validate_scope(entry, report, &installed)?;
                let mut handle = content::open(&entry.path, &entry.identity, true)?;
                let digest = hashing::verified_digest(entry, &mut handle, ctx, |n| {
                    read_bytes = read_bytes.saturating_add(n);
                    ctx.set(
                        "Rechecking duplicate copies",
                        common::display_path(&entry.path),
                        result.count,
                        Some(chosen.len() as u64),
                        read_bytes,
                    );
                })?;
                if digest != group.id {
                    bail!("File changed after scanning; scan again");
                }
                hashing::validate_open(keeper, &keeper_handle)?;
                validate_scope(keeper, report, &installed)?;
                validate_scope(entry, report, &installed)?;
                ctx.cancel.check()?;
                let allocated = winfs::allocated(&entry.path).unwrap_or(entry.allocated);
                content::delete(&handle)?;
                // FILE_DISPOSITION_INFO takes effect when the last handle closes.
                drop(handle);
                Ok(allocated)
            })();
            match attempt {
                Ok(allocated) => {
                    result.count += 1;
                    result.bytes = result.bytes.saturating_add(allocated);
                    result.succeeded.push(entry.id.clone());
                }
                Err(e) => {
                    result.errors.push(Issue {
                        path: common::display_path(&entry.path),
                        message: e.to_string(),
                    });
                    break;
                }
            }
            ctx.set(
                "delete",
                common::display_path(&entry.path),
                result.count,
                Some(chosen.len() as u64),
                result.bytes,
            );
        }
    }
    result.cancelled = ctx.cancel.cancelled();
    let _ = store.append(
        "duplicate_cleanup",
        "duplicate_cleanup",
        result.count,
        result.bytes,
        result.ok(),
    );
    result
}

fn is_protected(entry: &FileEntry, report: &ScanResult) -> bool {
    report.duplicate_scan.as_ref().is_some_and(|s| {
        s.locations
            .iter()
            .any(|l| l.protected && common::inside(&entry.path, &l.path))
    })
}
fn validate_scope(entry: &FileEntry, report: &ScanResult, installed: &[PathBuf]) -> Result<()> {
    let rule = report
        .rules
        .iter()
        .find(|r| r.id == entry.scope)
        .ok_or_else(|| anyhow::anyhow!("Scan authorization expired"))?;
    common::no_links(&rule.root)?;
    common::no_links(&entry.path)?;
    if !common::matches_rule(&entry.path, rule)
        || Policy::new(&rule.root, installed)?.excludes(&entry.path)
    {
        bail!("File is outside the scanned scope or protected");
    }
    let now = winfs::identity(&rule.root)?;
    if rule
        .root_identity
        .as_ref()
        .is_none_or(|old| old.volume != now.volume || old.index != now.index)
    {
        bail!("Scan root changed");
    }
    Ok(())
}
