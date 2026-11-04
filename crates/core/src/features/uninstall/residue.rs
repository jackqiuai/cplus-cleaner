use crate::{common, jobs::Context, *};
use anyhow::Result;
use cplus_platform_windows::{filesystem, registry};
use std::{collections::BTreeSet, path::PathBuf};
mod registry_residue;

/// Residues are offered only after the publisher registration has disappeared.
pub fn scan(apps: &[AppInfo], ctx: &Context) -> Result<ScanResult> {
    let mut result = ScanResult {
        feature: Feature::Uninstall,
        token: common::token(),
        ..Default::default()
    };
    let installed = registry::installed();
    let mut seen = BTreeSet::new();
    for app in apps {
        if installed
            .iter()
            .any(|r| r.hive == app.record.hive && r.key == app.record.key)
        {
            continue;
        }
        result.residue_owners.push(app.record.clone());
        let mut roots = vec![app.path.clone()];
        for base in [
            "APPDATA",
            "LOCALAPPDATA",
            "ProgramData",
            "ProgramFiles",
            "ProgramFiles(x86)",
        ] {
            if let Some(base) = std::env::var_os(base) {
                for name in [
                    app.name.as_str(),
                    app.path.file_name().and_then(|s| s.to_str()).unwrap_or(""),
                ] {
                    if registry_residue::component(name) {
                        roots.push(PathBuf::from(&base).join(name));
                        if base == "LOCALAPPDATA" || base == "APPDATA" {
                            roots.push(PathBuf::from(&base).join("Programs").join(name));
                        }
                    }
                }
            }
        }
        if registry_residue::component(&app.name) {
            for base in ["APPDATA", "ProgramData"] {
                if let Some(base) = std::env::var_os(base) {
                    let folder = PathBuf::from(base).join("Microsoft/Windows/Start Menu/Programs");
                    roots.push(folder.join(&app.name));
                    roots.push(folder.join(format!("{}.lnk", app.name)));
                }
            }
        }
        for root in roots {
            let program_data = std::env::var_os("ProgramData").map(PathBuf::from);
            let dedicated_data = program_data.as_ref().is_some_and(|p| {
                root.parent() == Some(p.as_path())
                    && root
                        .file_name()
                        .and_then(|s| s.to_str())
                        .is_some_and(registry_residue::component)
            });
            let start_menu = ["APPDATA", "ProgramData"]
                .iter()
                .filter_map(std::env::var_os)
                .any(|p| {
                    root.parent()
                        == Some(
                            PathBuf::from(p)
                                .join("Microsoft/Windows/Start Menu/Programs")
                                .as_path(),
                        )
                });
            if super::excluded_directory(&root)
                || !root.exists()
                || common::contains_running_app(&root)
                || (super::is_shared(&root) && !dedicated_data && !start_menu)
                || common::no_links(&root).is_err()
                || !seen.insert(common::key(&root))
            {
                continue;
            }
            if installed.iter().any(|r| {
                let p = super::location(r);
                !p.as_os_str().is_empty()
                    && (common::inside(&p, &root) || common::inside(&root, &p))
            }) {
                continue;
            }
            let id = format!("residue:{}", common::hash(common::key(&root).as_bytes()));
            result.categories.push(Category {
                id: id.clone(),
                name: app.name.clone(),
                group: "File residues".into(),
                status: root.display().to_string(),
                ..Default::default()
            });
            result.rules.push(Rule {
                id: id.clone(),
                category: id,
                root: root.clone(),
                recursive: true,
                patterns: vec![],
                excluded: vec!["Package Cache".into(), "System32".into()],
                days: 0,
                root_identity: filesystem::identity(&root).ok(),
                chat_app: None,
            });
        }
        for (row, category) in registry_residue::discover(app, &installed) {
            result.registry_residues.push(row);
            result.categories.push(category);
        }
    }
    let (files, issues) = common::scan_rules(&result.rules, ctx, super::excluded_directory);
    result.files = files;
    result.diagnostics = issues;
    super::super::drive_cleanup::fill_categories(&mut result.categories, &result.files);
    for category in &mut result.categories {
        if result.registry_residues.iter().any(|r| r.id == category.id) {
            category.count = 1;
        }
    }
    Ok(result)
}
pub fn delete(report: &ScanResult, selected: &[String], ctx: &Context) -> OperationResult {
    let installed = registry::installed();
    if report.residue_owners.is_empty()
        || report.residue_owners.iter().any(|owner| {
            installed
                .iter()
                .any(|r| r.hive == owner.hive && r.key == owner.key)
        })
    {
        return OperationResult {
            errors: vec![Issue {
                path: String::new(),
                message: "The application is registered again; residue cleanup stopped".into(),
            }],
            ..Default::default()
        };
    }
    let entries = report
        .files
        .iter()
        .filter(|f| selected.contains(&f.category) && !super::excluded_directory(&f.path))
        .map(|f| {
            let mut f = f.clone();
            if installed.iter().any(|r| {
                let root = super::location(r);
                !root.as_os_str().is_empty()
                    && (common::inside(&f.path, &root) || common::inside(&root, &f.path))
            }) {
                f.blocked = "Shared with an installed application".into();
            }
            f
        })
        .collect::<Vec<_>>();
    let mut result = common::delete_entries(&entries, &report.rules, ctx);
    if !ctx.cancel.cancelled() {
        let keys = registry_residue::delete(&report.registry_residues, selected);
        result.count += keys.count;
        result.errors.extend(keys.errors);
        result.succeeded.extend(keys.succeeded);
    }
    result
}
