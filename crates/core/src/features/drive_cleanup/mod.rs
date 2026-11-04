use super::system_slim::{self, Action};
use crate::{common, jobs::Context, *};
use anyhow::Result;
use cplus_platform_windows::filesystem as winfs;
use std::{collections::BTreeMap, path::PathBuf};
const WINDOWS_CACHES: [(&str, Action); 3] = [
    ("drive:windows_delivery", Action::ClearDeliveryOptimization),
    ("drive:windows_updates", Action::ClearWindowsUpdate),
    ("drive:windows_old", Action::ClearPreviousWindows),
];
pub fn default_categories() -> Vec<Category> {
    resource_json("rules/drive-reviewed.json")
        .ok()
        .into_iter()
        .flat_map(|v| v["categories"].as_array().cloned().unwrap_or_default())
        .map(|row| Category {
            id: format!("drive:{}", row["id"].as_str().unwrap_or_default()),
            name: row["name"].as_str().unwrap_or_default().into(),
            group: row["group"].as_str().unwrap_or_default().into(),
            icon: row["icon"].as_str().unwrap_or_default().into(),
            default_selected: row["selected"].as_bool().unwrap_or(false),
            ..Default::default()
        })
        .collect()
}

pub fn rules(days: u32) -> Result<(Vec<Rule>, Vec<Category>)> {
    rules_with_progress(days, None)
}

fn rules_with_progress(days: u32, ctx: Option<&Context>) -> Result<(Vec<Rule>, Vec<Category>)> {
    let catalog = resource_json("rules/drive-reviewed.json")?;
    let mut rules = vec![];
    let mut categories = vec![];
    let mut expanded_roots = BTreeMap::new();
    let current_user =
        regex::Regex::new(r"(?i)^%systemdrive%[/\\]users[/\\]\*[/\\]appdata[/\\]local").unwrap();
    let roaming =
        regex::Regex::new(r"(?i)^%systemdrive%[/\\]users[/\\]\*[/\\]appdata[/\\]roaming").unwrap();
    let homes = regex::Regex::new(r"(?i)^%systemdrive%[/\\]users[/\\]\*").unwrap();
    for row in catalog["categories"].as_array().into_iter().flatten() {
        let category = format!("drive:{}", row["id"].as_str().unwrap_or_default());
        categories.push(Category {
            id: category.clone(),
            name: row["name"].as_str().unwrap_or_default().into(),
            group: row["group"].as_str().unwrap_or_default().into(),
            icon: row["icon"].as_str().unwrap_or_default().into(),
            default_selected: row["selected"].as_bool().unwrap_or(false),
            ..Default::default()
        });
        for path in row["paths"].as_array().into_iter().flatten() {
            if let Some(ctx) = ctx {
                ctx.cancel.check()?;
                ctx.set(
                    "Preparing scan locations",
                    path["root"].as_str().unwrap_or_default(),
                    rules.len() as u64,
                    None,
                    0,
                );
            }
            let template = path["root"].as_str().unwrap_or_default();
            let template = current_user.replace(template, "%LOCALAPPDATA%");
            let template = roaming.replace(&template, "%APPDATA%");
            let template = homes.replace(&template, "%USERPROFILE%");
            let Ok(expanded) = common::expand_env(&template) else {
                continue;
            };
            let mut base = PathBuf::from(expanded);
            let mut patterns: Vec<String> = path["patterns"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|p| p.as_str().map(str::to_owned))
                .collect();
            if !template.ends_with(['\\', '/'])
                && ["*.log", "*.tmp", "*.7z"]
                    .iter()
                    .any(|s| template.ends_with(s))
            {
                if let Some(name) = base.file_name()
                    && patterns.is_empty()
                {
                    patterns.push(name.to_string_lossy().into());
                }
                base = base.parent().unwrap_or(&base).to_path_buf();
            }
            let roots = expanded_roots
                .entry(common::key(&base))
                .or_insert_with(|| common::expand_directories(&base.to_string_lossy()));
            for root in roots.iter().cloned() {
                if !root.exists()
                    || !common::inside(&root, &common::system_drive())
                    || common::key(&root) == common::key(&common::home())
                    || common::no_links(&root).is_err()
                {
                    continue;
                }
                let identity = winfs::identity(&root).ok();
                let id = common::hash(
                    format!("{category}:{}:{patterns:?}", common::key(&root)).as_bytes(),
                );
                rules.push(Rule {
                    id,
                    category: category.clone(),
                    root,
                    recursive: path["recursive"].as_bool().unwrap_or(true),
                    patterns: patterns.clone(),
                    excluded: path["excluded"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(|v| v.as_str().map(str::to_owned))
                        .collect(),
                    days,
                    root_identity: identity,
                    chat_app: None,
                });
            }
        }
    }
    Ok((rules, categories))
}
pub fn scan(days: u32, ctx: &Context) -> Result<ScanResult> {
    let mut timings_ms = BTreeMap::new();
    let started = std::time::Instant::now();
    let (rules, mut categories) = rules_with_progress(days, Some(ctx))?;
    timings_ms.insert("rules".into(), started.elapsed().as_millis() as u64);
    ctx.set("scan", "", 0, None, 0);
    let started = std::time::Instant::now();
    let (files, mut diagnostics) = common::rule_scan::scan(&rules, ctx);
    timings_ms.insert("files".into(), started.elapsed().as_millis() as u64);
    fill_categories(&mut categories, &files);
    let started = std::time::Instant::now();
    let measurements = if ctx.cancel.cancelled() {
        None
    } else {
        ctx.set("scan", "Windows", 0, None, 0);
        Some(system_slim::windows_cache_batch(ctx).map_err(|e| e.to_string()))
    };
    timings_ms.insert("windows".into(), started.elapsed().as_millis() as u64);
    for ((id, _), kind) in WINDOWS_CACHES.into_iter().zip([
        "Delivery Optimization Files",
        "Update Cleanup",
        "Previous Installations",
    ]) {
        if ctx.cancel.cancelled() {
            break;
        }
        let Some(category) = categories.iter_mut().find(|c| c.id == id) else {
            continue;
        };
        ctx.set("scan", &category.name, 0, None, 0);
        let measurement = (|| -> Result<(u64, u64)> {
            let values = measurements
                .as_ref()
                .ok_or_else(|| anyhow::anyhow!("Cancelled"))?
                .as_ref()
                .map_err(|e| anyhow::anyhow!(e.clone()))?;
            let row = values
                .as_array()
                .and_then(|rows| rows.iter().find(|row| row["kind"] == kind))
                .ok_or_else(|| anyhow::anyhow!("Missing Windows cleanup measurement"))?;
            if let Some(error) = row["error"].as_str() {
                anyhow::bail!("{error}");
            }
            cache_sizes(&row.to_string())
        })();
        match measurement {
            Ok((bytes, _)) => {
                category.bytes = bytes;
                category.count = u64::from(bytes > 0);
            }
            Err(error) => {
                category.status = "system_cleanup_unavailable".into();
                category.view_only = true;
                diagnostics.push(Issue {
                    path: category.name.clone(),
                    message: error.to_string(),
                });
            }
        }
    }
    Ok(ScanResult {
        feature: Feature::Drive,
        token: common::token(),
        categories,
        files,
        rules,
        diagnostics,
        cancelled: ctx.cancel.cancelled(),
        elapsed_ms: ctx.elapsed_ms(),
        timings_ms,
        ..Default::default()
    })
}
pub fn fill_categories(categories: &mut [Category], files: &[FileEntry]) {
    let mut grouped: BTreeMap<&str, Vec<&FileEntry>> = BTreeMap::new();
    for file in files {
        grouped.entry(&file.category).or_default().push(file);
    }
    for category in categories {
        if WINDOWS_CACHES.iter().any(|(id, _)| category.id == *id) {
            continue;
        }
        let rows = grouped
            .get(category.id.as_str())
            .cloned()
            .unwrap_or_default();
        category.count = rows.len() as u64;
        category.bytes = rows.iter().map(|f| f.size).sum();
        category.file_ids = rows.iter().map(|f| f.id.clone()).collect();
    }
}

fn cache_sizes(output: &str) -> Result<(u64, u64)> {
    let value: serde_json::Value = serde_json::from_str(output.trim())?;
    let before = value["before"]
        .as_u64()
        .ok_or_else(|| anyhow::anyhow!("Invalid Windows cleanup size"))?;
    let after = value["after"]
        .as_u64()
        .ok_or_else(|| anyhow::anyhow!("Invalid Windows cleanup size"))?;
    if before == u64::MAX || after == u64::MAX {
        anyhow::bail!("Unknown Windows cleanup size");
    }
    Ok((before, after))
}

pub(crate) fn delete_windows(
    store: &Store,
    report: &ScanResult,
    selected: &[String],
    ctx: &Context,
    result: &mut OperationResult,
) {
    if report.feature != Feature::Drive || report.token.is_empty() {
        return;
    }
    for (id, action) in WINDOWS_CACHES {
        if ctx.cancel.cancelled() {
            result.cancelled = true;
            break;
        }
        if !selected.iter().any(|s| s == id)
            || !report
                .categories
                .iter()
                .any(|c| c.id == id && !c.view_only && c.count > 0)
        {
            continue;
        }
        match system_slim::perform(store, action, ctx).and_then(|s| cache_sizes(&s)) {
            Ok((before, after)) => {
                result.bytes += before.saturating_sub(after);
                result.count += 1;
                result.succeeded.push(id.into());
                result.system_sizes.insert(id.into(), after);
            }
            Err(error) => result.errors.push(Issue {
                path: id.into(),
                message: error.to_string(),
            }),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_categories_are_system_items_without_direct_delete_paths() {
        let catalog = resource_json("rules/drive-reviewed.json").unwrap();
        for id in ["windows_delivery", "windows_updates", "windows_old"] {
            let row = catalog["categories"]
                .as_array()
                .unwrap()
                .iter()
                .find(|c| c["id"] == id)
                .unwrap();
            assert_eq!(row["group"], "系统相关");
            assert_eq!(row["selected"], true);
            assert!(row["paths"].as_array().unwrap().is_empty());
        }
        assert!(
            catalog["categories"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|c| c["group"] == "系统相关")
                .all(|c| c["selected"] == true)
        );
        for row in catalog["categories"].as_array().unwrap() {
            assert!(
                row["paths"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .all(|p| p["root"].is_string())
            );
        }
        let mut categories = vec![Category {
            id: "drive:windows_delivery".into(),
            bytes: 1024,
            count: 1,
            ..Default::default()
        }];
        fill_categories(&mut categories, &[]);
        assert_eq!(categories[0].bytes, 1024);
        let report = ScanResult {
            feature: Feature::Drive,
            categories,
            ..Default::default()
        };
        assert_eq!(report.bytes(), 1024);
        assert_eq!(report.count(), 1);
        assert_eq!(
            cache_sizes(r#"{"before":1024,"after":512}"#).unwrap(),
            (1024, 512)
        );
        assert!(cache_sizes(r#"{"before":-1,"after":0}"#).is_err());
        assert!(cache_sizes(r#"{"before":18446744073709551615,"after":0}"#).is_err());
    }
    #[test]
    fn selected_windows_categories_require_a_scan_and_obey_preview_protection() {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::new(Some(temp.path().into()), true).unwrap();
        let mut report = ScanResult {
            feature: Feature::Drive,
            categories: WINDOWS_CACHES
                .iter()
                .map(|(id, _)| Category {
                    id: (*id).into(),
                    count: 1,
                    ..Default::default()
                })
                .collect(),
            ..Default::default()
        };
        let selected = WINDOWS_CACHES
            .iter()
            .map(|(id, _)| (*id).into())
            .collect::<Vec<_>>();
        let ctx = Context::new(Feature::Drive);
        let mut result = OperationResult::default();
        delete_windows(&store, &report, &selected, &ctx, &mut result);
        assert!(result.errors.is_empty());
        report.token = "fixture".into();
        delete_windows(&store, &report, &[], &ctx, &mut result);
        assert!(result.errors.is_empty());
        delete_windows(&store, &report, &selected, &ctx, &mut result);
        assert_eq!(result.errors.len(), 3);
        assert!(result.succeeded.is_empty());
        assert!(result.system_sizes.is_empty());
        assert!(store.activities().is_empty());
    }
    #[test]
    fn rule_preparation_reports_progress_and_honors_cancellation() {
        let ctx = Context::new(Feature::Drive);
        rules_with_progress(0, Some(&ctx)).unwrap();
        assert_eq!(ctx.snapshot().phase, "Preparing scan locations");
        ctx.cancel.cancel();
        assert!(rules_with_progress(0, Some(&ctx)).is_err());
    }
}
