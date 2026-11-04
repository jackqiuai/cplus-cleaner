use crate::{common, jobs::Context, *};
use anyhow::{Context as _, Result, bail};
use cplus_platform_windows::{
    filesystem as winfs, processes,
    registry::{HKEY_CURRENT_USER, Key},
};
use rusqlite::{Connection, OpenFlags, backup::StepResult, types::ValueRef};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

const SNAPSHOT_TIMEOUT: Duration = Duration::from_secs(5);
const DATABASE_TIMEOUT: Duration = Duration::from_secs(30);

const EXPLORER: &str = r"Software\Microsoft\Windows\CurrentVersion\Explorer";
const PATHS: [(&str, &str); 4] = [
    (
        "run",
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\RunMRU",
    ),
    (
        "recent_docs",
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs",
    ),
    (
        "app_switched",
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\FeatureUsage\AppSwitched",
    ),
    (
        "regedit",
        r"Software\Microsoft\Windows\CurrentVersion\Applets\Regedit",
    ),
];
const DEFINITIONS: [(&str, &str, &str, &str); 13] = [
    ("run", "Win+R运行痕迹", "windows", "privacy-run"),
    (
        "start_recent",
        "开始菜单最近运行的程序",
        "windows",
        "privacy-recent",
    ),
    ("recent_docs", "最近访问的文件", "windows", "privacy-recent"),
    (
        "extensions",
        "文件扩展名记录",
        "windows",
        "privacy-extension",
    ),
    ("app_switched", "最近运行的程序", "windows", "privacy-apps"),
    (
        "regedit",
        "注册表最后一次位置",
        "windows",
        "privacy-regedit",
    ),
    ("thunder", "迅雷下载历史", "software", "privacy-thunder"),
    (
        "chrome_history",
        "Chrome浏览历史",
        "browsers",
        "privacy-chrome",
    ),
    (
        "chrome_downloads",
        "Chrome下载记录",
        "browsers",
        "privacy-download",
    ),
    ("edge_history", "Edge浏览历史", "browsers", "privacy-edge"),
    (
        "edge_downloads",
        "Edge下载记录",
        "browsers",
        "privacy-download",
    ),
    ("sensitive", "文件敏感隐私", "risk", "privacy-sensitive"),
    ("shopping", "网购痕迹", "risk", "privacy-shopping"),
];
fn reg_record(category: &str, path: &str, name: &str, kind: u32, raw: Vec<u8>) -> PrivacyRecord {
    PrivacyRecord {
        id: common::token(),
        category: category.into(),
        kind: "registry".into(),
        source: path.into(),
        registry_value: name.into(),
        digest: common::hash(&raw),
        raw,
        value_type: kind,
        ..Default::default()
    }
}
pub fn scan(ctx: &Context) -> Result<ScanResult> {
    let mut result = ScanResult {
        feature: Feature::Privacy,
        token: common::token(),
        ..Default::default()
    };
    for (cat, path) in PATHS {
        if ctx.cancel.cancelled() {
            break;
        }
        let Ok(key) = Key::open(HKEY_CURRENT_USER, path, 0, false) else {
            continue;
        };
        ctx.set("registry", cat, 0, None, 0);
        if cat == "recent_docs" {
            for child in key.subkeys() {
                ctx.cancel.check()?;
                let sub = format!("{path}\\{child}");
                let Ok(k) = Key::open(HKEY_CURRENT_USER, &sub, 0, false) else {
                    continue;
                };
                let values = k.values();
                let mut members = vec![];
                for (name, t, bytes) in &values {
                    if !name.is_empty() && name.chars().all(|c| c.is_ascii_digit()) {
                        let r = reg_record(cat, &sub, name, *t, bytes.clone());
                        members.push(r.id.clone());
                        result.privacy.push(r);
                    }
                }
                if !values.is_empty() && k.subkeys().is_empty() {
                    let raw = serde_json::to_vec(&values)?;
                    result.privacy.push(PrivacyRecord {
                        id: common::token(),
                        category: "extensions".into(),
                        kind: "registry_group".into(),
                        source: sub.into(),
                        digest: common::hash(&raw),
                        raw,
                        members,
                        ..Default::default()
                    });
                }
            }
        } else {
            for (name, t, bytes) in key.values() {
                let accepted = match cat {
                    "run" => name.len() == 1 && name.bytes().all(|b| b.is_ascii_alphabetic()),
                    "app_switched" => !name.is_empty(),
                    "regedit" => name == "LastKey" && !bytes.iter().all(|b| *b == 0),
                    _ => false,
                };
                if accepted {
                    result.privacy.push(reg_record(cat, path, &name, t, bytes));
                }
            }
        }
    }
    let local = common::env_path("LOCALAPPDATA", common::home().join("AppData/Local"));
    for (browser, root) in [
        ("chrome", local.join("Google/Chrome/User Data")),
        ("edge", local.join("Microsoft/Edge/User Data")),
    ] {
        for path in profile_databases(&root) {
            if ctx.cancel.cancelled() {
                break;
            }
            if let Err(error) = scan_database(&mut result, browser, &path, ctx) {
                result.diagnostics.push(Issue {
                    path: path.display().to_string(),
                    message: error.to_string(),
                });
            }
        }
    }
    for app in cplus_platform_windows::registry::installed()
        .into_iter()
        .filter(|r| r.name.contains("迅雷") || r.name.to_lowercase().contains("thunder"))
    {
        let path = super::uninstall::location(&app).join("Profiles/TaskDb.dat");
        if path.is_file()
            && let Err(e) = scan_database(&mut result, "thunder", &path, ctx)
        {
            result.diagnostics.push(Issue {
                path: "Thunder".into(),
                message: e.to_string(),
            });
        }
    }
    let mut derived: BTreeMap<(String, String), Vec<String>> = BTreeMap::new();
    for r in &result.privacy {
        if r.kind != "sqlite" {
            continue;
        }
        let Ok(values) = serde_json::from_slice::<Vec<String>>(&r.raw) else {
            continue;
        };
        if r.table == "urls" {
            for v in &values {
                let v = v.trim_start_matches("t:");
                if [
                    "taobao.com",
                    "jd.com",
                    "tmall.com",
                    "amazon.com",
                    "dangdang.com",
                    "suning.com",
                    "vip.com",
                    "pinduoduo.com",
                ]
                .iter()
                .any(|domain| url_domain(v, domain))
                {
                    derived
                        .entry(("shopping".into(), v.into()))
                        .or_default()
                        .push(r.id.clone());
                }
            }
        } else if r.table == "downloads" {
            for value in &values {
                let value = value.trim_start_matches("t:");
                if [
                    ".doc", ".docx", ".pdf", ".txt", ".xls", ".xlsx", ".ppt", ".pptx", ".odt",
                    ".ods", ".odp",
                ]
                .iter()
                .any(|e| value.to_lowercase().ends_with(e))
                {
                    let name = value.rsplit(['\\', '/']).next().unwrap_or(value);
                    derived
                        .entry(("sensitive".into(), name.to_lowercase()))
                        .or_default()
                        .push(r.id.clone());
                }
            }
        }
    }
    for ((category, _), members) in derived {
        result.privacy.push(PrivacyRecord {
            id: common::token(),
            category,
            kind: "derived".into(),
            members,
            ..Default::default()
        });
    }
    for (id, name, group, icon) in DEFINITIONS {
        let records = result
            .privacy
            .iter()
            .filter(|r| r.category == id)
            .map(|r| r.id.clone())
            .collect::<Vec<_>>();
        let count = records.len() as u64;
        result.categories.push(Category {
            id: id.into(),
            name: name.into(),
            group: group.into(),
            icon: icon.into(),
            count,
            record_ids: records,
            default_selected: count > 0,
            view_only: id == "start_recent",
            ..Default::default()
        });
    }
    result.cancelled = ctx.cancel.cancelled();
    result.elapsed_ms = ctx.elapsed_ms();
    Ok(result)
}
fn profile_databases(root: &Path) -> Vec<PathBuf> {
    let mut paths = Vec::new();
    if common::no_links(root).is_err() {
        return paths;
    }
    if let Ok(entries) = std::fs::read_dir(root) {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if name == "Default" || name.starts_with("Profile ") {
                let history = entry.path().join("History");
                if history.is_file() && common::no_links(&history).is_ok() {
                    paths.push(history);
                }
            }
        }
    }
    paths.sort();
    paths
}
fn url_domain(value: &str, domain: &str) -> bool {
    let Some((_, rest)) = value.split_once("://") else {
        return false;
    };
    let host = rest
        .split(['/', ':', '?', '#'])
        .next()
        .unwrap_or("")
        .to_lowercase();
    host == domain || host.ends_with(&format!(".{domain}"))
}
fn open_read(path: &Path) -> Result<Connection> {
    common::no_links(path)?;
    let info = winfs::identity(path)?;
    if info.directory() || info.links != 1 {
        bail!("Linked privacy databases are protected");
    }
    let db = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    db.busy_timeout(Duration::from_millis(100))?;
    db.pragma_update(None, "trusted_schema", false)?;
    Ok(db)
}
fn snapshot(path: &Path, ctx: &Context, timeout: Duration) -> Result<Connection> {
    ctx.cancel.check()?;
    let source = open_read(path)?;
    let mut memory = Connection::open_in_memory()?;
    let started = Instant::now();
    // Pin one read snapshot so an active browser cannot restart the backup
    // indefinitely. Both lock retries and copying have a finite deadline.
    source.execute_batch("BEGIN DEFERRED")?;
    {
        let backup = rusqlite::backup::Backup::new(&source, &mut memory)?;
        loop {
            ctx.cancel.check()?;
            if started.elapsed() >= timeout {
                bail!("History database is busy; close the application and scan again");
            }
            match backup.step(128)? {
                StepResult::Done => break,
                StepResult::More => {}
                StepResult::Busy | StepResult::Locked => {
                    std::thread::sleep(Duration::from_millis(10))
                }
                _ => bail!("Unable to read history database"),
            }
            let progress = backup.progress();
            ctx.set(
                "snapshot",
                path.display().to_string(),
                (progress.pagecount - progress.remaining).max(0) as u64,
                Some(progress.pagecount.max(0) as u64),
                0,
            );
        }
    }
    memory.pragma_update(None, "trusted_schema", false)?;
    Ok(memory)
}
fn quoted(name: &str) -> String {
    format!("\"{}\"", name.replace('"', "\"\""))
}
fn tables(db: &Connection) -> Result<BTreeMap<String, Vec<String>>> {
    let mut tables = BTreeMap::new();
    let mut q=db.prepare("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")?;
    let names = q
        .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    for (name, sql) in names {
        if sql.to_uppercase().contains("CREATE VIRTUAL") {
            bail!("Virtual history tables are unsupported");
        }
        let cols = db
            .prepare(&format!("PRAGMA table_info({})", quoted(&name)))?
            .query_map([], |r| r.get::<_, String>(1))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        tables.insert(name, cols);
    }
    Ok(tables)
}
fn values(row: &rusqlite::Row<'_>, n: usize) -> rusqlite::Result<Vec<String>> {
    (0..n)
        .map(|i| {
            Ok(match row.get_ref(i)? {
                ValueRef::Null => "n:".into(),
                ValueRef::Integer(n) => format!("i:{n}"),
                ValueRef::Real(n) => format!("r:{:x}", n.to_bits()),
                ValueRef::Text(s) => format!("t:{}", String::from_utf8_lossy(s)),
                ValueRef::Blob(b) => format!("b:{}", common::hash(b)),
            })
        })
        .collect()
}
/// Fingerprint all tables so related rows changed since the scan are retained.
fn database_digest(db: &Connection, ctx: &Context) -> Result<String> {
    use sha2::{Digest, Sha256};
    let mut hash = Sha256::new();
    let schema = tables(db)?;
    hash.update(serde_json::to_vec(&schema)?);
    for (name, columns) in schema {
        ctx.cancel.check()?;
        hash.update(name.as_bytes());
        let ordering = (1..=columns.len())
            .map(|n| n.to_string())
            .collect::<Vec<_>>()
            .join(",");
        let mut q = db.prepare(&format!(
            "SELECT * FROM {} ORDER BY {ordering}",
            quoted(&name)
        ))?;
        let mut rows = q.query([])?;
        while let Some(row) = rows.next()? {
            ctx.cancel.check()?;
            hash.update(serde_json::to_vec(&values(row, columns.len())?)?);
            ctx.set("fingerprint", &name, 0, None, 0);
        }
    }
    Ok(format!("{:x}", hash.finalize()))
}
fn scan_database(result: &mut ScanResult, browser: &str, path: &Path, ctx: &Context) -> Result<()> {
    ctx.set("privacy", path.display().to_string(), 0, None, 0);
    let identity = winfs::identity(path)?;
    let db = snapshot(path, ctx, SNAPSHOT_TIMEOUT)?;
    let cancel = ctx.cancel.clone();
    let started = Instant::now();
    db.progress_handler(
        1000,
        Some(move || cancel.cancelled() || started.elapsed() >= DATABASE_TIMEOUT),
    );
    let schema = tables(&db)?;
    let digest = database_digest(&db, ctx)?;
    let mut records = Vec::new();
    for table in if browser == "thunder" {
        vec!["BtFile"]
    } else {
        vec!["urls", "downloads"]
    } {
        let Some(columns) = schema.get(table) else {
            continue;
        };
        if browser == "thunder" && !columns.iter().any(|c| c == "FileName") {
            bail!("Unsupported Thunder history schema");
        }
        if browser != "thunder" && !columns.iter().any(|c| c == "id") {
            bail!("Unsupported browser history schema");
        }
        let key = if browser == "thunder" { "rowid" } else { "id" };
        let mut q = db.prepare(&format!(
            "SELECT {key},* FROM {} ORDER BY {key}",
            quoted(table)
        ))?;
        let mut rows = q.query([])?;
        while let Some(row) = rows.next()? {
            ctx.cancel.check()?;
            let id: i64 = row.get(0)?;
            let raw = serde_json::to_vec(&values(row, columns.len() + 1)?)?;
            records.push(PrivacyRecord {
                id: common::token(),
                category: if browser == "thunder" {
                    "thunder".into()
                } else {
                    format!(
                        "{browser}_{}",
                        if table == "urls" {
                            "history"
                        } else {
                            "downloads"
                        }
                    )
                },
                kind: "sqlite".into(),
                source: path.into(),
                table: table.into(),
                key: id.to_string(),
                digest: digest.clone(),
                raw,
                identity: Some(identity.clone()),
                browser: browser.into(),
                ..Default::default()
            });
            ctx.set(
                "privacy",
                format!("{} · {table}", path.display()),
                records.len() as u64,
                None,
                0,
            );
        }
    }
    // Do not expose partial records if a database failed halfway through.
    result.privacy.extend(records);
    Ok(())
}
fn registry_allowed(record: &PrivacyRecord) -> bool {
    let path = record.source.to_string_lossy();
    PATHS
        .iter()
        .any(|(c, p)| record.category == *c && path.eq_ignore_ascii_case(p))
        || (["recent_docs", "extensions"].contains(&record.category.as_str())
            && path.starts_with(&format!("{EXPLORER}\\RecentDocs\\"))
            && path.matches('\\').count() == EXPLORER.matches('\\').count() + 2)
}
fn registry_delete(r: &PrivacyRecord) -> Result<()> {
    if !registry_allowed(r) {
        bail!("Registry record is outside the privacy scope");
    }
    let path = r.source.to_str().context("Invalid registry path")?;
    let k = Key::open(HKEY_CURRENT_USER, path, 0, true)?;
    if r.kind == "registry_group" {
        if !k.subkeys().is_empty() || common::hash(&serde_json::to_vec(&k.values())?) != r.digest {
            bail!("Recent document records changed; scan again");
        }
        for (name, _, _) in k.values() {
            k.delete_value(&name)?;
        }
        drop(k);
        let (parent, child) = path.rsplit_once('\\').context("Invalid registry group")?;
        Key::open(HKEY_CURRENT_USER, parent, 0, true)?.delete_child(child)?;
    } else {
        let (kind, raw) = k.raw(&r.registry_value)?;
        if kind != r.value_type || raw != r.raw {
            bail!("Privacy record changed; scan again");
        }
        k.delete_value(&r.registry_value)?;
        if r.category == "run" {
            let value = k.string("MRUList").replace(&r.registry_value, "");
            k.set_string("MRUList", &value)?;
        }
        if r.category == "recent_docs"
            && let (Ok(number), Ok((kind, raw))) =
                (r.registry_value.parse::<u32>(), k.raw("MRUListEx"))
            && kind == 3
            && raw.len() % 4 == 0
        {
            let mut remaining = raw
                .as_chunks::<4>()
                .0
                .iter()
                .map(|c| u32::from_le_bytes(*c))
                .filter(|n| *n != number && *n != u32::MAX)
                .collect::<Vec<_>>();
            remaining.push(u32::MAX);
            k.set_raw(
                "MRUListEx",
                kind,
                &remaining
                    .into_iter()
                    .flat_map(u32::to_le_bytes)
                    .collect::<Vec<_>>(),
            )?;
        }
    }
    Ok(())
}
fn assert_schema(db: &Connection, browser: &str) -> Result<BTreeMap<String, Vec<String>>> {
    let schema = tables(db)?;
    let triggers: i64 = db.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'",
        [],
        |r| r.get(0),
    )?;
    if triggers > 0 {
        bail!("History databases with triggers are protected");
    }
    if browser == "thunder" {
        if !schema
            .get("BtFile")
            .is_some_and(|c| c.contains(&"FileName".to_string()))
        {
            bail!("Unsupported Thunder history schema");
        }
        return Ok(schema);
    }
    let allowed = [
        "urls",
        "downloads",
        "visits",
        "visit_source",
        "keyword_search_terms",
        "content_annotations",
        "context_annotations",
        "clusters_and_visits",
        "cluster_visit_duplicates",
        "clusters",
        "cluster_keywords",
        "segments",
        "segment_usage",
        "visited_links",
        "edge_urls",
        "edge_keyword_search_terms_source",
        "edge_visits",
        "downloads_url_chains",
        "downloads_slices",
        "edge_downloads",
    ];
    for (table, required) in [
        ("urls", vec!["id", "url"]),
        ("downloads", vec!["id", "current_path"]),
        ("visits", vec!["id", "url"]),
        ("visit_source", vec!["id"]),
        ("keyword_search_terms", vec!["url_id"]),
        ("content_annotations", vec!["visit_id"]),
        ("context_annotations", vec!["visit_id"]),
        ("clusters_and_visits", vec!["cluster_id", "visit_id"]),
        (
            "cluster_visit_duplicates",
            vec!["visit_id", "duplicate_visit_id"],
        ),
        ("clusters", vec!["cluster_id"]),
        ("cluster_keywords", vec!["cluster_id"]),
        ("segments", vec!["id", "url_id"]),
        ("segment_usage", vec!["segment_id"]),
        ("visited_links", vec!["id", "link_url_id"]),
        ("edge_urls", vec!["url_id"]),
        ("edge_keyword_search_terms_source", vec!["url_id"]),
        ("edge_visits", vec!["visit_id"]),
        ("downloads_url_chains", vec!["id"]),
        ("downloads_slices", vec!["download_id"]),
        ("edge_downloads", vec!["id"]),
    ] {
        if let Some(columns) = schema.get(table)
            && required.iter().any(|c| !columns.contains(&c.to_string()))
        {
            bail!("Unsupported history relationship schema");
        }
    }
    for (name, columns) in &schema {
        if !allowed.contains(&name.as_str())
            && columns
                .iter()
                .any(|c| ["url_id", "visit_id", "download_id", "link_url_id"].contains(&c.as_str()))
        {
            bail!("Unrecognized history relationship; records retained");
        }
    }
    Ok(schema)
}
fn erase(
    db: &Connection,
    schema: &BTreeMap<String, Vec<String>>,
    table: &str,
    column: &str,
    id: i64,
) -> Result<()> {
    if schema
        .get(table)
        .is_some_and(|c| c.contains(&column.to_owned()))
    {
        db.execute(
            &format!("DELETE FROM {} WHERE {}=?1", quoted(table), quoted(column)),
            [id],
        )?;
    }
    Ok(())
}
fn related_ids(
    db: &Connection,
    schema: &BTreeMap<String, Vec<String>>,
    table: &str,
    target: &str,
    column: &str,
    id: i64,
) -> Result<Vec<i64>> {
    if !schema
        .get(table)
        .is_some_and(|c| c.contains(&target.to_owned()) && c.contains(&column.to_owned()))
    {
        return Ok(vec![]);
    }
    Ok(db
        .prepare(&format!(
            "SELECT {} FROM {} WHERE {}=?1",
            quoted(target),
            quoted(table),
            quoted(column)
        ))?
        .query_map([id], |r| r.get(0))?
        .collect::<rusqlite::Result<Vec<_>>>()?)
}
fn erase_record(
    db: &Connection,
    schema: &BTreeMap<String, Vec<String>>,
    table: &str,
    id: i64,
) -> Result<()> {
    match table {
        "downloads" => {
            for (t, c) in [
                ("downloads_url_chains", "id"),
                ("downloads_slices", "download_id"),
                ("edge_downloads", "id"),
            ] {
                erase(db, schema, t, c, id)?;
            }
        }
        "urls" => {
            let visits = related_ids(db, schema, "visits", "id", "url", id)?;
            let mut clusters = BTreeSet::new();
            for visit in &visits {
                clusters.extend(related_ids(
                    db,
                    schema,
                    "clusters_and_visits",
                    "cluster_id",
                    "visit_id",
                    *visit,
                )?);
            }
            for visit in visits {
                for (t, c) in [
                    ("visit_source", "id"),
                    ("content_annotations", "visit_id"),
                    ("context_annotations", "visit_id"),
                    ("clusters_and_visits", "visit_id"),
                    ("cluster_visit_duplicates", "visit_id"),
                    ("cluster_visit_duplicates", "duplicate_visit_id"),
                    ("edge_visits", "visit_id"),
                ] {
                    erase(db, schema, t, c, visit)?;
                }
                for field in ["from_visit", "opener_visit"] {
                    if schema
                        .get("visits")
                        .is_some_and(|c| c.contains(&field.into()))
                    {
                        db.execute(
                            &format!("UPDATE visits SET {field}=0 WHERE {field}=?1"),
                            [visit],
                        )?;
                    }
                }
                erase(db, schema, "visits", "id", visit)?;
            }
            for cluster in clusters {
                if related_ids(
                    db,
                    schema,
                    "clusters_and_visits",
                    "visit_id",
                    "cluster_id",
                    cluster,
                )?
                .is_empty()
                {
                    erase(db, schema, "cluster_keywords", "cluster_id", cluster)?;
                    erase(db, schema, "clusters", "cluster_id", cluster)?;
                }
            }
            for segment in related_ids(db, schema, "segments", "id", "url_id", id)? {
                if schema
                    .get("visits")
                    .is_some_and(|c| c.contains(&"segment_id".into()))
                {
                    db.execute(
                        "UPDATE visits SET segment_id=0 WHERE segment_id=?1",
                        [segment],
                    )?;
                }
                erase(db, schema, "segment_usage", "segment_id", segment)?;
            }
            for link in related_ids(db, schema, "visited_links", "id", "link_url_id", id)? {
                if schema
                    .get("visits")
                    .is_some_and(|c| c.contains(&"visited_link_id".into()))
                {
                    db.execute(
                        "UPDATE visits SET visited_link_id=0 WHERE visited_link_id=?1",
                        [link],
                    )?;
                }
            }
            for (t, c) in [
                ("segments", "url_id"),
                ("keyword_search_terms", "url_id"),
                ("edge_urls", "url_id"),
                ("edge_keyword_search_terms_source", "url_id"),
                ("visited_links", "link_url_id"),
            ] {
                erase(db, schema, t, c, id)?;
            }
        }
        "BtFile" => {}
        _ => bail!("Unsupported privacy table"),
    }
    db.execute(
        &format!(
            "DELETE FROM {} WHERE {}=?1",
            quoted(table),
            if table == "BtFile" { "rowid" } else { "id" }
        ),
        [id],
    )?;
    Ok(())
}
pub fn delete(
    store: &Store,
    report: &ScanResult,
    categories: &[String],
    ctx: &Context,
) -> OperationResult {
    let mut result = OperationResult::default();
    let mut ids = report
        .privacy
        .iter()
        .filter(|r| categories.contains(&r.category))
        .map(|r| r.id.clone())
        .collect::<BTreeSet<_>>();
    for r in &report.privacy {
        if ids.contains(&r.id) {
            ids.extend(r.members.clone());
        }
    }
    let groups = report
        .privacy
        .iter()
        .filter(|r| r.kind == "registry_group" && ids.contains(&r.id))
        .collect::<Vec<_>>();
    let children = groups
        .iter()
        .flat_map(|r| r.members.clone())
        .collect::<BTreeSet<_>>();
    let mut databases: BTreeMap<PathBuf, Vec<&PrivacyRecord>> = BTreeMap::new();
    for r in &report.privacy {
        if ctx.cancel.cancelled() {
            break;
        }
        if !ids.contains(&r.id) || children.contains(&r.id) || r.kind == "derived" {
            continue;
        }
        if r.kind == "sqlite" {
            databases.entry(r.source.clone()).or_default().push(r);
            continue;
        }
        match registry_delete(r) {
            Ok(()) => {
                result.count += 1;
                result.succeeded.push(r.id.clone());
                if r.kind == "registry_group" {
                    result.count += r.members.len() as u64;
                    result.succeeded.extend(r.members.clone());
                }
            }
            Err(e) => result.errors.push(Issue {
                path: r.category.clone(),
                message: e.to_string(),
            }),
        }
    }
    let running = match processes::list() {
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
    for (path, records) in databases {
        if ctx.cancel.cancelled() {
            break;
        }
        let attempt = (|| -> Result<()> {
            let first = records[0];
            let exe = match first.browser.as_str() {
                "chrome" => "chrome.exe",
                "edge" => "msedge.exe",
                _ => "thunder.exe",
            };
            if running.iter().any(|p| p.name.eq_ignore_ascii_case(exe)) {
                bail!("Close the application before deleting its history");
            }
            common::no_links(&path)?;
            let now = winfs::identity(&path)?;
            let old = first
                .identity
                .as_ref()
                .context("Missing database identity")?;
            if now.volume != old.volume || now.index != old.index || now.links != 1 {
                bail!("History database was replaced");
            }
            let mut db = Connection::open_with_flags(
                &path,
                OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
            )?;
            db.busy_timeout(Duration::from_secs(3))?;
            db.pragma_update(None, "trusted_schema", false)?;
            db.pragma_update(None, "foreign_keys", true)?;
            let tx = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
            let schema = assert_schema(&tx, &first.browser)?;
            if database_digest(&tx, ctx)? != first.digest {
                bail!("History changed after scanning; scan again");
            }
            let mut seen = BTreeSet::new();
            for record in &records {
                ctx.cancel.check()?;
                let id = record.key.parse::<i64>()?;
                if seen.insert((record.table.clone(), id)) {
                    erase_record(&tx, &schema, &record.table, id)?;
                }
            }
            tx.commit()?;
            Ok(())
        })();
        match attempt {
            Ok(()) => {
                result.count += records.len() as u64;
                result
                    .succeeded
                    .extend(records.iter().map(|r| r.id.clone()));
            }
            Err(e) => result.errors.push(Issue {
                path: records[0].browser.clone(),
                message: e.to_string(),
            }),
        }
    }
    for derived in report
        .privacy
        .iter()
        .filter(|r| r.kind == "derived" && !r.members.is_empty())
    {
        if derived
            .members
            .iter()
            .all(|id| result.succeeded.contains(id))
        {
            result.succeeded.push(derived.id.clone());
            result.count += 1;
        }
    }
    result.cancelled = ctx.cancel.cancelled();
    let _ = store.append(
        "privacy_cleanup",
        "privacy_cleanup",
        result.count,
        0,
        result.ok(),
    );
    result
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn retained_visits_do_not_keep_dangling_segment_or_link_references() {
        let (_temp, path) = fixture();
        let db = Connection::open(path).unwrap();
        db.execute_batch("INSERT INTO urls VALUES(2,'https://keep.test/'); ALTER TABLE visits ADD COLUMN segment_id INTEGER DEFAULT 0; ALTER TABLE visits ADD COLUMN visited_link_id INTEGER DEFAULT 0; INSERT INTO visits VALUES(22,2,7,8); CREATE TABLE segments(id INTEGER PRIMARY KEY,url_id INTEGER); INSERT INTO segments VALUES(7,1); CREATE TABLE visited_links(id INTEGER PRIMARY KEY,link_url_id INTEGER); INSERT INTO visited_links VALUES(8,1);").unwrap();
        let schema = assert_schema(&db, "fixture").unwrap();
        erase_record(&db, &schema, "urls", 1).unwrap();
        let refs: (i64, i64) = db
            .query_row(
                "SELECT segment_id,visited_link_id FROM visits WHERE id=22",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        assert_eq!(refs, (0, 0));
        assert_eq!(
            db.query_row::<i64, _, _>("SELECT COUNT(*) FROM urls WHERE id=2", [], |r| r.get(0))
                .unwrap(),
            1
        );
    }
    fn fixture() -> (tempfile::TempDir, PathBuf) {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("History");
        let db = Connection::open(&path).unwrap();
        db.execute_batch("CREATE TABLE urls(id INTEGER PRIMARY KEY,url TEXT); CREATE TABLE downloads(id INTEGER PRIMARY KEY,current_path TEXT); CREATE TABLE visits(id INTEGER PRIMARY KEY,url INTEGER); CREATE TABLE cluster_visit_duplicates(visit_id INTEGER,duplicate_visit_id INTEGER,PRIMARY KEY(visit_id,duplicate_visit_id)) WITHOUT ROWID; INSERT INTO urls VALUES(1,'https://example.test/'); INSERT INTO visits VALUES(11,1); INSERT INTO downloads VALUES(2,'C:/Owned/report.pdf');").unwrap();
        (temp, path)
    }
    #[test]
    fn snapshots_support_chromium_without_rowid_tables() {
        let (_temp, path) = fixture();
        let db = snapshot(&path, &Context::new(Feature::Privacy), SNAPSHOT_TIMEOUT).unwrap();
        let digest = database_digest(&db, &Context::new(Feature::Privacy)).unwrap();
        assert_eq!(digest.len(), 64);
        let mut report = ScanResult::default();
        scan_database(
            &mut report,
            "fixture",
            &path,
            &Context::new(Feature::Privacy),
        )
        .unwrap();
        assert_eq!(report.privacy.len(), 2);
    }
    #[test]
    fn locked_history_snapshot_has_a_deadline() {
        let (_temp, path) = fixture();
        let writer = Connection::open(&path).unwrap();
        writer
            .execute_batch("BEGIN EXCLUSIVE; UPDATE urls SET url='https://locked.test/'")
            .unwrap();
        let started = Instant::now();
        let result = snapshot(
            &path,
            &Context::new(Feature::Privacy),
            Duration::from_millis(150),
        );
        assert!(result.is_err());
        assert!(started.elapsed() < Duration::from_secs(2));
        writer.execute_batch("ROLLBACK").unwrap();
        assert!(snapshot(&path, &Context::new(Feature::Privacy), SNAPSHOT_TIMEOUT).is_ok());
    }
    #[test]
    fn cancelled_history_snapshot_returns_without_retrying() {
        let (_temp, path) = fixture();
        let ctx = Context::new(Feature::Privacy);
        ctx.cancel.cancel();
        assert!(snapshot(&path, &ctx, SNAPSHOT_TIMEOUT).is_err());
    }
    #[test]
    fn chromium_scans_all_standard_profiles() {
        let temp = tempfile::tempdir().unwrap();
        for name in ["Default", "Profile 1", "Profile 2", "Cache"] {
            let profile = temp.path().join(name);
            std::fs::create_dir(&profile).unwrap();
            std::fs::write(profile.join("History"), b"fixture").unwrap();
        }
        let paths = profile_databases(temp.path());
        assert_eq!(paths.len(), 3);
        assert!(!paths.iter().any(|p| p.parent().unwrap().ends_with("Cache")));
    }
    #[test]
    fn changed_history_is_retained() {
        let (temp, path) = fixture();
        let mut report = ScanResult::default();
        let ctx = Context::new(Feature::Privacy);
        scan_database(&mut report, "fixture", &path, &ctx).unwrap();
        Connection::open(&path)
            .unwrap()
            .execute("INSERT INTO urls VALUES(3,'https://new.test/')", [])
            .unwrap();
        let store = Store::new(Some(temp.path().join("profile")), true).unwrap();
        let result = delete(&store, &report, &["fixture_history".into()], &ctx);
        assert_eq!(result.count, 0);
        assert_eq!(
            Connection::open(&path)
                .unwrap()
                .query_row::<i64, _, _>("SELECT COUNT(*) FROM urls", [], |r| r.get(0))
                .unwrap(),
            2
        );
    }
    #[test]
    fn browser_relations_are_deleted_in_one_transaction() {
        let (temp, path) = fixture();
        let mut report = ScanResult::default();
        let ctx = Context::new(Feature::Privacy);
        scan_database(&mut report, "fixture", &path, &ctx).unwrap();
        let store = Store::new(Some(temp.path().join("profile")), true).unwrap();
        let result = delete(&store, &report, &["fixture_history".into()], &ctx);
        assert!(result.ok(), "{:?}", result.errors);
        let db = Connection::open(path).unwrap();
        assert_eq!(
            db.query_row::<i64, _, _>("SELECT COUNT(*) FROM urls", [], |r| r.get(0))
                .unwrap(),
            0
        );
        assert_eq!(
            db.query_row::<i64, _, _>("SELECT COUNT(*) FROM visits", [], |r| r.get(0))
                .unwrap(),
            0
        );
        assert_eq!(
            db.query_row::<i64, _, _>("SELECT COUNT(*) FROM downloads", [], |r| r.get(0))
                .unwrap(),
            1
        );
    }
    #[test]
    fn unexpected_relationships_and_triggers_are_rejected() {
        let (_temp, path) = fixture();
        let db = Connection::open(path).unwrap();
        db.execute_batch("CREATE TABLE unknown_relation(url_id INTEGER)")
            .unwrap();
        assert!(assert_schema(&db, "fixture").is_err());
        db.execute_batch("DROP TABLE unknown_relation; CREATE TRIGGER unexpected AFTER DELETE ON urls BEGIN DELETE FROM downloads; END;").unwrap();
        assert!(assert_schema(&db, "fixture").is_err());
    }
    #[test]
    fn shopping_domains_match_hostname_boundaries() {
        assert!(url_domain("https://item.jd.com/123", "jd.com"));
        assert!(!url_domain("https://notjd.com/jd.com", "jd.com"));
    }
}
