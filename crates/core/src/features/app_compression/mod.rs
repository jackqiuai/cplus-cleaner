use super::{app_mover, uninstall};
use crate::{common, jobs::Context, *};
use anyhow::{Context as _, Result, bail};
use cplus_platform_windows::{filesystem as winfs, processes};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    process::Stdio,
    time::{Duration, Instant},
};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Entry {
    pub relative: PathBuf,
    pub rust_identity: winfs::Identity,
    #[serde(default)]
    pub was_compressed: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Manifest {
    pub folder: PathBuf,
    pub root: [u64; 2],
    pub key: String,
    pub files: Vec<Entry>,
}

fn safe_relative(relative: &Path) -> bool {
    !relative.as_os_str().is_empty()
        && !relative.is_absolute()
        && !relative.components().any(|c| {
            matches!(
                c,
                std::path::Component::ParentDir | std::path::Component::Prefix(_)
            )
        })
}
fn stable_file(a: &winfs::Identity, b: &winfs::Identity) -> bool {
    a.volume == b.volume
        && a.index == b.index
        && a.size == b.size
        && a.modified == b.modified
        && b.links == 1
        && !b.redirected()
        && !b.directory()
}
fn validate_manifest(manifest: &Manifest) -> Result<()> {
    app_mover::guard_root(&manifest.folder, false)?;
    let root = winfs::identity(&manifest.folder)?;
    if [root.volume as u64, root.index] != manifest.root {
        bail!("Installation directory was replaced; restore stopped");
    }
    if manifest.files.iter().any(|e| !safe_relative(&e.relative)) {
        bail!("Invalid compression manifest path");
    }
    Ok(())
}
fn read_manifest(store: &Store, id: &str) -> Result<Manifest> {
    if id.len() != 32 || !id.bytes().all(|c| c.is_ascii_hexdigit()) {
        bail!("Invalid compression record ID");
    }
    let value: serde_json::Value = store.read(&format!("compression/{id}.json"))?;
    if let Ok(manifest) = serde_json::from_value::<Manifest>(value.clone()) {
        return Ok(manifest);
    }
    let folder = PathBuf::from(
        value["folder"]
            .as_str()
            .context("Missing compression folder")?,
    );
    let root = [
        value["root"][0]
            .as_u64()
            .context("Invalid volume identity")?,
        value["root"][1].as_u64().context("Invalid root identity")?,
    ];
    let mut files = vec![];
    for row in value["files"]
        .as_array()
        .context("Missing compression files")?
    {
        let relative = PathBuf::from(row["relative"].as_str().context("Invalid stored path")?);
        if !safe_relative(&relative) {
            bail!("Invalid stored path");
        }
        let path = folder.join(&relative);
        let identity = match winfs::identity(&path) {
            Ok(info) => info,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
            Err(e) => return Err(e.into()),
        };
        files.push(Entry {
            relative,
            rust_identity: identity,
            was_compressed: false,
        });
    }
    Ok(Manifest {
        folder,
        root,
        key: value["key"].as_str().unwrap_or(id).into(),
        files,
    })
}
fn save_record(
    store: &Store,
    record: &serde_json::Value,
    manifest: Option<&Manifest>,
) -> Result<()> {
    let id = record["id"]
        .as_str()
        .context("Missing compression record")?;
    if let Some(manifest) = manifest {
        let mut compatible = serde_json::to_value(manifest)?;
        for row in compatible["files"].as_array_mut().unwrap() {
            let identity: winfs::Identity = serde_json::from_value(row["rust_identity"].clone())?;
            row["identity"] = serde_json::json!([
                identity.volume,
                identity.index,
                identity.size,
                identity.modified.saturating_sub(116_444_736_000_000_000) * 100
            ]);
        }
        store.write(&format!("compression/{id}.json"), &compatible)?;
    }
    let mut rows = store
        .read::<Vec<serde_json::Value>>("compression/records.json")
        .unwrap_or_default();
    if let Some(row) = rows.iter_mut().find(|r| r["id"] == id) {
        *row = record.clone();
    } else {
        for row in &mut rows {
            if row["install_path"] == record["install_path"] && row["restored"] != true {
                row["superseded"] = true.into();
            }
        }
        rows.push(record.clone());
    }
    store.write("compression/records.json", &rows)
}
pub fn history(store: &Store, refresh: bool, ctx: &Context) -> Vec<serde_json::Value> {
    refreshed_history(store, refresh, ctx, false, &mut BTreeMap::new())
}
fn refreshed_history(
    store: &Store,
    refresh: bool,
    ctx: &Context,
    for_scan: bool,
    cache: &mut BTreeMap<String, uninstall::Measurement>,
) -> Vec<serde_json::Value> {
    let mut records = store
        .read::<Vec<serde_json::Value>>("compression/records.json")
        .unwrap_or_default();
    if refresh {
        let mut states = BTreeMap::<String, Option<bool>>::new();
        for record in &mut records {
            if ctx.cancel.cancelled() {
                break;
            }
            if record["superseded"] == true || (for_scan && record["restored"] == true) {
                continue;
            }
            let id = record["id"].as_str().unwrap_or_default().to_owned();
            match read_manifest(store, &id).and_then(|m| {
                validate_manifest(&m)?;
                Ok(m)
            }) {
                Ok(manifest) => {
                    let key = common::key(&manifest.folder);
                    let current = winfs::identity(&manifest.folder).ok();
                    if cache.get(&key).is_some_and(|m| {
                        m.stats
                            .root_identity
                            .as_ref()
                            .zip(current.as_ref())
                            .is_none_or(|(a, b)| a.volume != b.volume || a.index != b.index)
                    }) {
                        cache.remove(&key);
                        states.clear();
                    }
                    let measured = cache.entry(key).or_insert_with(|| {
                        let mut data = if for_scan {
                            uninstall::measure_for_scan(&manifest.folder, ctx)
                        } else {
                            uninstall::measure(&manifest.folder, ctx)
                        };
                        if data.stats.root_identity.is_none() {
                            data.stats.root_identity = current;
                        }
                        data.files.sort_by_cached_key(|(path, _)| common::key(path));
                        data
                    });
                    record["current_size"] = measured.physical.into();
                    if ctx.cancel.cancelled() {
                        break;
                    }
                    if !measured.complete() {
                        record["refresh_error"] =
                            "Installation directory could not be fully read".into();
                        continue;
                    }
                    if let Some(object) = record.as_object_mut() {
                        object.remove("refresh_error");
                    }
                    let mut remaining = 0u64;
                    let mut uncertain = false;
                    for entry in &manifest.files {
                        if ctx.cancel.cancelled() {
                            uncertain = true;
                            break;
                        }
                        if entry.was_compressed {
                            continue;
                        }
                        let p = manifest.folder.join(&entry.relative);
                        let key = common::key(&p);
                        let compressed =
                            states.entry(key.clone()).or_insert_with(|| {
                                match measured
                                    .files
                                    .binary_search_by(|(path, _)| common::key(path).cmp(&key))
                                {
                                    Ok(index) => Some(
                                        measured.files[index].1.attributes & 0x800 != 0
                                            || winfs::wof_compressed(&p),
                                    ),
                                    Err(_) => match winfs::compressed(&p) {
                                        Ok(value) => Some(value),
                                        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                                            Some(false)
                                        }
                                        Err(_) => None,
                                    },
                                }
                            });
                        match compressed {
                            Some(true) => remaining += 1,
                            Some(false) => {}
                            None => uncertain = true,
                        }
                    }
                    record["remaining"] = remaining.into();
                    let all_skipped = record["skipped_files"].as_u64().unwrap_or(0) > 0
                        && record["compressed_files"].as_u64().unwrap_or(0) == 0
                        && record["operation"] != "restore";
                    if !uncertain && remaining == 0 && !all_skipped {
                        record["restored"] = true.into();
                        record["status"] = "success".into();
                        record["pending"] = 0.into();
                        record["last_errors"] = serde_json::json!([]);
                    }
                }
                Err(error) => record["refresh_error"] = error.to_string().into(),
            }
        }
        let _ = store.write("compression/records.json", &records);
    }
    records.reverse();
    records
}
pub fn scan(store: &Store, ctx: &Context) -> Result<ScanResult> {
    let candidates = app_mover::candidates_for(Feature::Compression, ctx);
    let mut cache = BTreeMap::new();
    let records = refreshed_history(store, true, ctx, true, &mut cache);
    let mut result = ScanResult {
        feature: Feature::Compression,
        token: common::token(),
        ..Default::default()
    };
    for data in cache.values() {
        app_mover::record_scan_stats(&mut result, &data.stats);
    }
    for mut app in candidates {
        if ctx.cancel.cancelled() {
            break;
        }
        if records.iter().any(|r| {
            r["restored"] != true
                && r["superseded"] != true
                && r["install_path"]
                    .as_str()
                    .is_some_and(|p| common::key(Path::new(p)) == common::key(&app.path))
        }) {
            cache.remove(&common::key(&app.path));
            continue;
        }
        let key = common::key(&app.path);
        let measured = match cache.remove(&key) {
            Some(data)
                if data
                    .stats
                    .root_identity
                    .as_ref()
                    .zip(winfs::identity(&app.path).ok().as_ref())
                    .is_some_and(|(a, b)| a.volume == b.volume && a.index == b.index)
                    && common::no_links(&app.path).is_ok() =>
            {
                *result
                    .counts
                    .entry("reused_history_measurements".into())
                    .or_default() += 1;
                data
            }
            _ => {
                let data = uninstall::measure_for_scan(&app.path, ctx);
                app_mover::record_scan_stats(&mut result, &data.stats);
                data
            }
        };
        if measured.linked {
            continue;
        }
        if !measured.complete() || measured.files.is_empty() {
            result.diagnostics.extend(measured.errors);
            continue;
        }
        app_mover::apply_measurement(&mut app, &measured);
        app.compressed = false;
        result.apps.push(app);
    }
    result.apps.sort_by_key(|a| std::cmp::Reverse(a.physical));
    app_mover::finish_scan(&mut result, ctx);
    Ok(result)
}

struct BatchProgress<'a> {
    bytes: u64,
    completed: u64,
    total: u64,
    current: &'a str,
}
fn compact(
    store: &Store,
    paths: &[PathBuf],
    compress: bool,
    batch: BatchProgress<'_>,
    ctx: &Context,
) -> Result<()> {
    let BatchProgress {
        bytes: total_bytes,
        completed: completed_bytes,
        total: all_bytes,
        current,
    } = batch;
    let executable =
        common::env_path("SystemRoot", r"C:\Windows".into()).join("System32/compact.exe");
    let log = store.root.join(format!("compact-{}.tmp", common::token()));
    let output = fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&log)?;
    let stderr = output.try_clone()?;
    let mut command = processes::hidden(executable);
    command
        .arg(if compress { "/C" } else { "/U" })
        .args(["/A", "/I", "/F"]);
    if compress {
        command.arg("/EXE:LZX");
    }
    command
        .args(paths)
        .stdout(Stdio::from(output))
        .stderr(Stdio::from(stderr));
    let mut child = command.spawn()?;
    let started = Instant::now();
    loop {
        if child.try_wait()?.is_some() {
            break;
        }
        if ctx.cancel.cancelled() {
            let _ = child.kill();
            let _ = child.wait();
            let _ = fs::remove_file(&log);
            bail!("Operation cancelled");
        }
        let read = processes::io_read_bytes(child.id())
            .unwrap_or(0)
            .min(total_bytes.saturating_sub(1));
        ctx.set(
            if compress { "compress" } else { "restore" },
            current,
            completed_bytes + read,
            Some(all_bytes.max(1)),
            completed_bytes + read,
        );
        if started.elapsed() > Duration::from_secs(1800) {
            let _ = child.kill();
            let _ = child.wait();
            let _ = fs::remove_file(&log);
            bail!("Windows compression timed out; current state was retained");
        }
        std::thread::sleep(Duration::from_millis(120));
    }
    let _ = fs::remove_file(log);
    Ok(())
}

pub fn compress_apps(store: &Store, apps: &[AppInfo], ctx: &Context) -> OperationResult {
    let mut result = OperationResult::default();
    let mut names = Vec::new();
    for app in apps {
        if ctx.cancel.cancelled() {
            break;
        }
        names.push(app.name.as_str());
        match compress_one(store, app, ctx) {
            Ok(row) => {
                result.count += 1;
                result.bytes += row["saved_bytes"].as_u64().unwrap_or(0);
                result.succeeded.push(app.id.clone());
                for error in row["last_errors"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|v| v.as_str())
                {
                    result.errors.push(Issue {
                        path: app.name.clone(),
                        message: error.into(),
                    });
                }
            }
            Err(e) => result.errors.push(Issue {
                path: app.name.clone(),
                message: e.to_string(),
            }),
        }
    }
    result.cancelled = ctx.cancel.cancelled();
    let _ = store.append(
        "software_compress",
        &names.join("、"),
        result.count,
        result.bytes,
        result.ok(),
    );
    result
}
pub fn compress_one(store: &Store, app: &AppInfo, ctx: &Context) -> Result<serde_json::Value> {
    app_mover::guard_root(&app.path, false)?;
    app_mover::require_idle(&app.path)?;
    if app_mover::needs_admin(&app.path) {
        bail!("Administrator permission required");
    }
    let root = winfs::identity(&app.path)?;
    if app
        .root_identity
        .as_ref()
        .is_some_and(|old| old.volume != root.volume || old.index != root.index)
    {
        bail!("Application directory changed; scan again");
    }
    let measured = uninstall::measure(&app.path, ctx);
    if !measured.complete() {
        bail!("Installation directory could not be fully read");
    }
    ctx.cancel.check()?;
    let files = measured
        .files
        .into_iter()
        .map(|(path, identity)| {
            let was_compressed = winfs::compressed(&path).unwrap_or(false);
            Entry {
                relative: path.strip_prefix(&app.path).unwrap().into(),
                rust_identity: identity,
                was_compressed,
            }
        })
        .collect();
    let manifest = Manifest {
        folder: app.path.clone(),
        root: [root.volume as u64, root.index],
        key: app.id.clone(),
        files,
    };
    let mut row = serde_json::json!({"schema":3,"id":common::token(),"name":app.name,"install_path":app.path,"old_size":measured.physical,"new_size":measured.physical,"current_size":measured.physical,"saved_bytes":0,"time":chrono::Utc::now().timestamp(),"operation":"compress","restored":false,"superseded":false,"status":"running","pending":manifest.files.len(),"remaining":0,"last_errors":[]});
    save_record(store, &row, Some(&manifest))?;
    operate(store, &mut row, &manifest, true, ctx)?;
    Ok(row)
}
pub fn restore(store: &Store, id: &str, ctx: &Context) -> Result<OperationResult> {
    let mut row = store
        .read::<Vec<serde_json::Value>>("compression/records.json")?
        .into_iter()
        .find(|r| r["id"] == id)
        .context("Compression record not found")?;
    if row["restored"] == true || row["superseded"] == true {
        bail!("This compression record is already restored or superseded");
    }
    let manifest = read_manifest(store, id)?;
    validate_manifest(&manifest)?;
    app_mover::require_idle(&manifest.folder)?;
    row["operation"] = "restore".into();
    row["status"] = "running".into();
    save_record(store, &row, None)?;
    operate(store, &mut row, &manifest, false, ctx)?;
    let errors = row["last_errors"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|v| v.as_str())
        .map(|m| Issue {
            path: row["name"].as_str().unwrap_or_default().into(),
            message: m.into(),
        })
        .collect::<Vec<_>>();
    let success = row["restored"] == true;
    let _ = store.append(
        "software_restore",
        row["name"].as_str().unwrap_or_default(),
        u64::from(success),
        0,
        success,
    );
    Ok(OperationResult {
        count: u64::from(success),
        errors,
        cancelled: ctx.cancel.cancelled(),
        succeeded: if success { vec![id.into()] } else { vec![] },
        ..Default::default()
    })
}
fn operate(
    store: &Store,
    row: &mut serde_json::Value,
    manifest: &Manifest,
    compress: bool,
    ctx: &Context,
) -> Result<()> {
    validate_manifest(manifest)?;
    let mut errors = vec![];
    let mut processed = 0u64;
    let mut skipped = 0u64;
    let mut candidates = vec![];
    for entry in &manifest.files {
        if entry.was_compressed {
            if compress {
                skipped += 1;
            }
            continue;
        }
        let path = manifest.folder.join(&entry.relative);
        match winfs::identity(&path) {
            Ok(now) => {
                if now.directory()
                    || now.redirected()
                    || now.links > 1
                    || common::no_links(&path).is_err()
                {
                    errors.push("Linked or protected file was retained".to_owned());
                    continue;
                }
                if !compress && !winfs::compressed(&path).unwrap_or(true) {
                    continue;
                }
                if compress && !stable_file(&entry.rust_identity, &now) {
                    errors.push("File changed after scanning; retained".into());
                    continue;
                }
                candidates.push((path, now));
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound && !compress => {}
            Err(e) => errors.push(e.to_string()),
        }
    }
    let total = candidates.iter().map(|(_, i)| i.size).sum::<u64>();
    let mut completed = 0u64;
    let mut offset = 0;
    while offset < candidates.len() && !ctx.cancel.cancelled() {
        let start = offset;
        let (mut bytes, mut chars) = (0, 0);
        while offset < candidates.len() {
            let (path, info) = &candidates[offset];
            let length = path.as_os_str().len() + 3;
            if offset > start
                && (offset - start >= 32
                    || chars + length > 6000
                    || bytes + info.size > 64 * common::MIB)
            {
                break;
            }
            bytes += info.size;
            chars += length;
            offset += 1;
        }
        let batch = &candidates[start..offset];
        let paths = batch.iter().map(|(p, _)| p.clone()).collect::<Vec<_>>();
        let current = paths
            .first()
            .map(|p| {
                p.file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .into_owned()
            })
            .unwrap_or_default();
        if let Err(e) = compact(
            store,
            &paths,
            compress,
            BatchProgress {
                bytes,
                completed,
                total,
                current: &current,
            },
            ctx,
        ) {
            if !ctx.cancel.cancelled() {
                errors.push(e.to_string());
            }
            break;
        }
        for (path, old) in batch {
            match winfs::identity(path) {
                Ok(now) if stable_file(old, &now) => {
                    processed += 1;
                    if compress && !winfs::compressed(path).unwrap_or(false) {
                        skipped += 1;
                    }
                }
                Ok(_) => errors
                    .push("File identity changed while processing; current file retained".into()),
                Err(e) => errors.push(e.to_string()),
            }
        }
        completed += bytes;
        row["processed"] = processed.into();
        row["pending"] = (candidates.len() as u64).saturating_sub(processed).into();
        save_record(store, row, None)?;
        ctx.set(
            if compress { "compress" } else { "restore" },
            current,
            completed,
            Some(total.max(1)),
            completed,
        );
    }
    let final_ctx = Context::new(Feature::Compression);
    let measured = uninstall::measure(&manifest.folder, &final_ctx);
    errors.extend(measured.errors.iter().map(|i| i.message.clone()));
    let mut remaining = 0u64;
    for entry in &manifest.files {
        if entry.was_compressed {
            continue;
        }
        let path = manifest.folder.join(&entry.relative);
        if !path.exists() {
            continue;
        }
        match winfs::compressed(&path) {
            Ok(true) => remaining += 1,
            Ok(false) => {}
            Err(e) => errors.push(e.to_string()),
        }
    }
    if !compress && remaining > 0 && !ctx.cancel.cancelled() {
        errors.push("Some files are still compressed; close the application and retry".into());
    }
    errors.truncate(5);
    row["current_size"] = measured.physical.into();
    row["processed"] = processed.into();
    row["remaining"] = remaining.into();
    row["last_errors"] = serde_json::to_value(&errors)?;
    row["status"] = if ctx.cancel.cancelled() {
        "cancelled"
    } else if !errors.is_empty() {
        "partial"
    } else {
        "success"
    }
    .into();
    row["restored"] =
        (!compress && remaining == 0 && errors.is_empty() && !ctx.cancel.cancelled()).into();
    if compress {
        row["new_size"] = measured.physical.into();
        row["saved_bytes"] = row["old_size"]
            .as_u64()
            .unwrap_or(0)
            .saturating_sub(measured.physical)
            .into();
        row["compressed_files"] = remaining.into();
        row["skipped_files"] = skipped.into();
    }
    if row["status"] == "success" {
        row["pending"] = 0.into();
    }
    save_record(store, row, None)?;
    if !ctx.cancel.cancelled() {
        ctx.set("finished", "", total.max(1), Some(total.max(1)), total);
    }
    Ok(())
}

#[cfg(test)]
mod scan_history_tests {
    use super::*;
    fn fixture() -> (tempfile::TempDir, Store, PathBuf, Manifest) {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("owned-app");
        fs::create_dir(&root).unwrap();
        let path = root.join("owned.bin");
        fs::write(&path, b"owned normal content").unwrap();
        let id = winfs::identity(&root).unwrap();
        let manifest = Manifest {
            folder: root.clone(),
            root: [u64::from(id.volume), id.index],
            key: "fixture".into(),
            files: vec![Entry {
                relative: "owned.bin".into(),
                rust_identity: winfs::identity(&path).unwrap(),
                was_compressed: false,
            }],
        };
        let store = Store::new(Some(temp.path().join("profile")), true).unwrap();
        (temp, store, root, manifest)
    }
    #[test]
    fn scan_history_measures_a_shared_root_once_and_detects_manual_decompression() {
        let (_temp, store, root, manifest) = fixture();
        let mut rows = vec![];
        for _ in 0..2 {
            let id = common::token();
            store
                .write(&format!("compression/{id}.json"), &manifest)
                .unwrap();
            rows.push(serde_json::json!({"id":id,"install_path":root,"restored":false,"superseded":false,"operation":"compress","compressed_files":1}));
        }
        store.write("compression/records.json", &rows).unwrap();
        let mut cache = BTreeMap::new();
        let rows = refreshed_history(
            &store,
            true,
            &Context::new(Feature::Compression),
            true,
            &mut cache,
        );
        assert_eq!(cache.len(), 1);
        assert!(cache.values().next().unwrap().stats.batch_pages > 0);
        assert!(
            rows.iter()
                .all(|r| r["restored"] == true && r["remaining"] == 0)
        );
        assert_eq!(cache[&common::key(&root)].files.len(), 1);
    }
    #[test]
    fn scan_history_skips_restored_records_and_retains_invalid_active_manifests() {
        let (_temp, store, root, mut manifest) = fixture();
        let id = common::token();
        manifest.root[1] ^= 1;
        store
            .write(&format!("compression/{id}.json"), &manifest)
            .unwrap();
        store
            .write(
                "compression/records.json",
                &serde_json::json!([
                    {"id":id,"install_path":root,"restored":false,"superseded":false},
                    {"id":"old","install_path":root,"restored":true,"current_size":123}
                ]),
            )
            .unwrap();
        let mut cache = BTreeMap::new();
        let rows = refreshed_history(
            &store,
            true,
            &Context::new(Feature::Compression),
            true,
            &mut cache,
        );
        assert!(cache.is_empty());
        let active = rows.iter().find(|r| r["id"] == id).unwrap();
        assert_eq!(active["restored"], false);
        assert!(active["refresh_error"].is_string());
        let restored = rows.iter().find(|r| r["id"] == "old").unwrap();
        assert_eq!(restored["current_size"], 123);
    }
    #[test]
    fn cancellation_revokes_application_scan_results() {
        let ctx = Context::new(Feature::Compression);
        ctx.cancel.cancel();
        let mut report = ScanResult {
            feature: Feature::Compression,
            token: "old".into(),
            apps: vec![AppInfo::default()],
            ..Default::default()
        };
        app_mover::finish_scan(&mut report, &ctx);
        assert!(report.cancelled);
        assert!(report.token.is_empty());
        assert!(report.apps.is_empty());
    }
}
