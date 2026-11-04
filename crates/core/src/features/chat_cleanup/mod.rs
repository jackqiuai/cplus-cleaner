use crate::{common, jobs::Context, *};
mod scanner;
use anyhow::{Result, bail};
use cplus_platform_windows::{
    filesystem as winfs, processes,
    registry::{self, HKEY_CURRENT_USER, Key},
    shell,
};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    os::windows::fs::MetadataExt,
    path::{Path, PathBuf},
};

const PROTECTED_NAMES: &[&str] = &[
    "cookies",
    "login data",
    "preferences",
    "local state",
    "config",
    "config.bin",
    "key_data",
    "settings",
    "settingss",
];
const PROTECTED_SUFFIXES: &[&str] = &[
    ".db",
    ".db-wal",
    ".db-shm",
    ".sqlite",
    ".sqlite3",
    ".sqlite-wal",
    ".sqlite-shm",
    ".key",
    ".json",
    ".ini",
    ".bak",
    ".backup",
    ".db-journal",
];
pub fn protected(path: &Path) -> bool {
    let name = path
        .file_name()
        .unwrap_or_default()
        .to_string_lossy()
        .to_lowercase();
    PROTECTED_NAMES.contains(&name.as_str()) || PROTECTED_SUFFIXES.iter().any(|s| name.ends_with(s))
}
pub fn catalog() -> Result<Vec<serde_json::Value>> {
    Ok(resource_json("assets/chat-rules.json")?["applications"]
        .as_array()
        .cloned()
        .unwrap_or_default())
}
fn children(root: &Path, ctx: &Context) -> Vec<PathBuf> {
    fs::read_dir(root)
        .into_iter()
        .flatten()
        .flatten()
        .take_while(|_| !ctx.cancel.cancelled())
        .filter_map(|e| {
            let p = e.path();
            if e.metadata()
                .is_ok_and(|m| m.is_dir() && m.file_attributes() & 0x400 == 0)
            {
                Some(p)
            } else {
                None
            }
        })
        .collect()
}
pub fn save_folder(store: &Store, app: &str, path: Option<&Path>) -> Result<()> {
    if !catalog()?.iter().any(|v| v["id"] == app) {
        bail!("Unknown chat application");
    }
    let mut paths = store
        .read::<BTreeMap<String, PathBuf>>("chat-paths.json")
        .unwrap_or_default();
    if let Some(path) = path {
        common::no_links(path)?;
        if !path.is_dir() || common::protected_data_root(path) {
            bail!("Select an application data folder");
        }
        paths.insert(app.into(), path.into());
    } else {
        paths.remove(app);
    }
    store.write("chat-paths.json", &paths)
}
pub fn scan(store: &Store, ctx: &Context) -> Result<ScanResult> {
    let specs = catalog()?;
    let paths = store
        .read::<BTreeMap<String, PathBuf>>("chat-paths.json")
        .unwrap_or_default();
    let records = registry::installed();
    let home = common::home();
    let local = common::env_path("LOCALAPPDATA", home.join("AppData/Local"));
    let roaming = common::env_path("APPDATA", home.join("AppData/Roaming"));
    let documents = shell::documents_folder();
    let bases = BTreeMap::from([
        ("local", local.clone()),
        ("roaming", roaming),
        ("documents", documents),
    ]);
    let mut rules = vec![];
    let mut categories = BTreeMap::<String, Category>::new();
    let mut diagnostic = vec![];
    let mut seen_rules = BTreeSet::new();
    let mut packages = None;
    for (index, spec) in specs.iter().enumerate() {
        if ctx.cancel.cancelled() {
            break;
        }
        let id = spec["id"].as_str().unwrap_or_default();
        let name = spec["name"].as_str().unwrap_or(id);
        ctx.set("discover", name, index as u64, Some(specs.len() as u64), 0);
        let mut roots = vec![];
        if let Some(path) = paths.get(id) {
            roots.push(path.clone());
        } else {
            for location in spec["locations"].as_array().into_iter().flatten() {
                if let (Some(base), Some(suffix)) = (
                    location[0].as_str().and_then(|s| bases.get(s)),
                    location[1].as_str(),
                ) {
                    roots.push(base.join(suffix));
                }
            }
            if spec["packages"].as_array().is_some_and(|p| !p.is_empty()) {
                let packages =
                    packages.get_or_insert_with(|| children(&local.join("Packages"), ctx));
                for package in packages.iter() {
                    if spec["packages"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(|v| v.as_str())
                        .any(|prefix| {
                            package
                                .file_name()
                                .unwrap_or_default()
                                .to_string_lossy()
                                .to_lowercase()
                                .starts_with(&prefix.to_lowercase())
                        })
                    {
                        roots.push(package.clone());
                    }
                }
            }
            if id == "wechat"
                && let Ok(key) = Key::open(HKEY_CURRENT_USER, r"Software\Tencent\WeChat", 0, false)
            {
                let path = key.string("FileSavePath");
                if !path.is_empty()
                    && path != "MyDocument:"
                    && let Ok(path) = common::expand_env(&path)
                {
                    let p = PathBuf::from(path);
                    roots.extend([p.clone(), p.join("WeChat Files"), p.join("xwechat_files")]);
                }
            }
        }
        let mut seen = BTreeSet::new();
        let mut found = false;
        for root in roots {
            if !root.is_dir()
                || common::no_links(&root).is_err()
                || !seen.insert(common::key(&root))
            {
                continue;
            }
            found = true;
            let mut parents = vec![root.clone()];
            if spec["accounts"].as_bool() == Some(true) {
                let sub = children(&root, ctx);
                for child in &sub {
                    if ["nt_qq", "users", "accounts", "all_users"].contains(
                        &child
                            .file_name()
                            .unwrap_or_default()
                            .to_string_lossy()
                            .to_lowercase()
                            .as_str(),
                    ) {
                        parents.extend(children(child, ctx));
                    }
                }
                parents.extend(sub);
            }
            for parent in parents {
                let mut layouts: Vec<(String, String, bool)> = spec["rules"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|r| {
                        Some((
                            r[0].as_str()?.into(),
                            r[1].as_str()?.into(),
                            r[2].as_bool().unwrap_or(false),
                        ))
                    })
                    .collect();
                for prefix in spec["chromium"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|v| v.as_str())
                {
                    for suffix in ["Cache/Cache_Data", "Code Cache", "GPUCache"] {
                        layouts.push((
                            Path::new(prefix).join(suffix).to_string_lossy().into(),
                            "缓存文件".into(),
                            true,
                        ));
                    }
                }
                for (suffix, kind, _) in layouts {
                    if ctx.cancel.cancelled() {
                        break;
                    }
                    let path = parent.join(suffix);
                    if seen_rules.contains(&common::key(&path))
                        || !path.is_dir()
                        || common::no_links(&path).is_err()
                    {
                        continue;
                    }
                    seen_rules.insert(common::key(&path));
                    let cat = format!("chat:{id}:{kind}");
                    categories.entry(cat.clone()).or_insert_with(|| Category {
                        id: cat.clone(),
                        name: kind,
                        group: name.into(),
                        app: id.into(),
                        default_selected: true,
                        ..Default::default()
                    });
                    let rule_id = common::hash(common::key(&path).as_bytes());
                    rules.push(Rule {
                        id: rule_id,
                        category: cat,
                        root_identity: winfs::identity(&path).ok(),
                        root: path,
                        recursive: true,
                        patterns: vec![],
                        excluded: vec![],
                        days: 0,
                        chat_app: Some(id.into()),
                    });
                }
            }
        }
        if !categories.values().any(|c| c.app == id) {
            let installed = records.iter().any(|r| {
                spec["aliases"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|v| v.as_str())
                    .any(|a| r.name.to_lowercase().contains(a))
            });
            let status = if found {
                "unsupported_layout"
            } else if installed {
                "data_not_found"
            } else {
                "not_installed"
            };
            categories.insert(
                format!("chat:{id}:status"),
                Category {
                    id: format!("chat:{id}:status"),
                    name: name.into(),
                    group: name.into(),
                    app: id.into(),
                    view_only: true,
                    status: status.into(),
                    ..Default::default()
                },
            );
        }
    }
    let (files, issues, counts) = scanner::scan(&rules, ctx);
    diagnostic.extend(issues);
    let mut categories: Vec<_> = categories.into_values().collect();
    super::drive_cleanup::fill_categories(&mut categories, &files);
    Ok(ScanResult {
        feature: Feature::Chat,
        token: common::token(),
        files,
        categories,
        rules,
        diagnostics: diagnostic,
        cancelled: ctx.cancel.cancelled(),
        elapsed_ms: ctx.elapsed_ms(),
        counts,
        ..Default::default()
    })
}
pub fn delete(
    store: &Store,
    report: &ScanResult,
    selected: &BTreeSet<String>,
    ctx: &Context,
) -> OperationResult {
    let specs = catalog().unwrap_or_default();
    let processes = match processes::list() {
        Ok(v) => v,
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
    let mut chosen = vec![];
    let mut rejected = vec![];
    for file in &report.files {
        if !selected.contains(&file.category) {
            continue;
        }
        let app = report
            .rules
            .iter()
            .find(|r| r.id == file.scope)
            .and_then(|r| r.chat_app.as_deref())
            .unwrap_or_default();
        let running = specs.iter().find(|s| s["id"] == app).is_some_and(|s| {
            processes.iter().any(|p| {
                s["executables"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|v| v.as_str())
                    .any(|n| p.name.eq_ignore_ascii_case(n))
            })
        });
        if running || protected(&file.path) {
            rejected.push(Issue {
                path: file.path.display().to_string(),
                message: if running {
                    "Close the chat application before cleaning"
                } else {
                    "Protected chat data"
                }
                .into(),
            });
        } else {
            chosen.push(file.clone());
        }
    }
    let mut result = common::delete_entries(&chosen, &report.rules, ctx);
    result.errors.extend(rejected);
    let _ = store.append(
        "chat_cleanup",
        "chat_cleanup",
        result.count,
        result.bytes,
        result.ok(),
    );
    result
}
