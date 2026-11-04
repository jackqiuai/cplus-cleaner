use cplus_core::{
    common,
    features::{self, app_compression, app_mover},
    jobs::Context,
    *,
};
use cplus_platform_windows::filesystem;
use std::{fs, path::Path};
fn scope(root: &Path) -> Rule {
    Rule {
        id: "fixture".into(),
        category: "cache".into(),
        root: root.into(),
        recursive: true,
        patterns: vec![],
        excluded: vec![],
        days: 0,
        root_identity: filesystem::identity(root).ok(),
        chat_app: None,
    }
}
#[test]
fn visible_paths_hide_extended_prefix_without_changing_io_paths() {
    assert_eq!(
        common::display_path(Path::new(r"\\?\C:\Apps\中文")),
        r"C:\Apps\中文"
    );
    assert_eq!(
        common::display_path(Path::new(r"\\?\UNC\server\share\app")),
        r"\\server\share\app"
    );
    assert_eq!(common::display_path(Path::new(r"D:\Apps")), r"D:\Apps");
}

#[test]
fn uninstall_size_walk_counts_files_and_excludes_directory_junctions() {
    let temp = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    fs::create_dir(temp.path().join("sub")).unwrap();
    fs::write(temp.path().join("main.bin"), vec![0; 2048]).unwrap();
    fs::write(temp.path().join("sub/cache.bin"), vec![0; 1024]).unwrap();
    fs::write(outside.path().join("outside.bin"), vec![0; 16384]).unwrap();
    let link = temp.path().join("redirect");
    filesystem::create_junction(&link, outside.path()).unwrap();
    let summary =
        features::uninstall::directory_size(temp.path(), &Context::new(Feature::Uninstall));
    fs::remove_dir(link).unwrap();
    assert_eq!(summary.logical, 3072);
    assert_eq!(summary.files, 2);
    assert_eq!(summary.unreadable, 0);
    assert!(outside.path().join("outside.bin").exists());
}

#[test]
fn missing_registered_icon_falls_back_to_application_executable() {
    let temp = tempfile::tempdir().unwrap();
    let source =
        std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap()).join("System32/cmd.exe");
    fs::copy(source, temp.path().join("command.exe")).unwrap();
    let record = cplus_platform_windows::registry::InstalledRecord {
        name: "Command".into(),
        icon: temp
            .path()
            .join("missing.dll")
            .to_string_lossy()
            .into_owned()
            + ",-42",
        ..Default::default()
    };
    let icon = features::uninstall::application_icon(
        &record,
        temp.path(),
        &mut features::uninstall::IconCache::new(),
    );
    assert!(icon.is_some());
    assert!(
        icon.unwrap()
            .rgba
            .as_chunks::<4>()
            .0
            .iter()
            .any(|p| p[3] != 0)
    );
}

#[test]
fn uninstall_inventory_never_walks_package_cache_or_system32() {
    let temp = tempfile::tempdir().unwrap();
    for dir in ["normal", "Package Cache/product/deep", "sYsTeM32/drivers"] {
        let path = temp.path().join(dir);
        fs::create_dir_all(&path).unwrap();
        fs::write(path.join("payload.bin"), vec![0; 4096]).unwrap();
    }
    let ctx = Context::new(Feature::Uninstall);
    let summary = features::uninstall::directory_size(temp.path(), &ctx);
    assert_eq!(summary.logical, 4096);
    assert_eq!(summary.files, 1);
    for dir in ["Package Cache", "sYsTeM32"] {
        let excluded = features::uninstall::directory_size(&temp.path().join(dir), &ctx);
        assert_eq!(excluded.logical, 0);
        assert_eq!(excluded.files, 0);
    }
}
#[test]
fn scoped_delete_rejects_modified_files() {
    let temp = tempfile::tempdir().unwrap();
    let file = temp.path().join("cache.tmp");
    fs::write(&file, b"before").unwrap();
    let rule = scope(temp.path());
    let ctx = Context::new(Feature::Drive);
    let (files, _) = common::scan_rules(std::slice::from_ref(&rule), &ctx, |_| false);
    assert_eq!(files.len(), 1);
    fs::write(&file, b"changed contents").unwrap();
    let result = common::delete_entries(&files, &[rule], &ctx);
    assert_eq!(result.count, 0);
    assert!(file.exists());
    assert_eq!(result.errors.len(), 1);
}
#[test]
fn owned_file_can_be_deleted() {
    let temp = tempfile::tempdir().unwrap();
    let file = temp.path().join("cache.tmp");
    fs::write(&file, b"cache").unwrap();
    let rule = scope(temp.path());
    let ctx = Context::new(Feature::Drive);
    let (files, _) = common::scan_rules(std::slice::from_ref(&rule), &ctx, |_| false);
    let result = common::delete_entries(&files, &[rule], &ctx);
    assert_eq!(result.count, 1);
    assert!(!file.exists());
}
#[test]
fn explicit_single_file_scope_is_supported() {
    let temp = tempfile::tempdir().unwrap();
    let file = temp.path().join("single.log");
    fs::write(&file, b"log").unwrap();
    let rule = scope(&file);
    let (files, issues) = common::scan_rules(
        std::slice::from_ref(&rule),
        &Context::new(Feature::Drive),
        |_| false,
    );
    assert_eq!(files.len(), 1, "{issues:?}");
    assert!(common::matches_rule(&file, &rule));
}
#[test]
fn junctions_are_never_followed_by_cleanup() {
    let temp = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    fs::write(outside.path().join("keep.txt"), b"keep").unwrap();
    let link = temp.path().join("redirect");
    filesystem::create_junction(&link, outside.path()).unwrap();
    let (files, _) =
        common::scan_rules(&[scope(temp.path())], &Context::new(Feature::Drive), |_| {
            false
        });
    assert!(files.is_empty());
    fs::remove_dir(link).unwrap();
    assert!(outside.path().join("keep.txt").exists());
}
#[test]
fn sha256_copy_retains_original_and_detects_collisions() {
    let temp = tempfile::tempdir().unwrap();
    let original = temp.path().join("original");
    let copied = temp.path().join("copied");
    fs::write(&original, vec![0x42; 2 * 1024 * 1024]).unwrap();
    let ctx = Context::new(Feature::AppMover);
    common::copy_verified(&original, &copied, &ctx, |_| {}).unwrap();
    assert_eq!(fs::read(&original).unwrap(), fs::read(&copied).unwrap());
    assert!(common::copy_verified(&original, &copied, &ctx, |_| {}).is_err());
    assert!(original.exists());
}
#[test]
fn duplicates_keep_a_verified_surviving_copy() {
    let temp = tempfile::tempdir().unwrap();
    for n in ["a.txt", "b.txt", "c.txt"] {
        fs::write(temp.path().join(n), b"identical content").unwrap();
    }
    let store = Store::new(Some(temp.path().join("data")), true).unwrap();
    let ctx = Context::new(Feature::Duplicates);
    let report = features::duplicates::scan(temp.path(), &ctx).unwrap();
    assert_eq!(report.categories.len(), 1);
    assert_eq!(
        report.bytes(),
        report
            .files
            .iter()
            .skip(1)
            .map(|f| f.allocated)
            .sum::<u64>()
    );
    let all = report.files.iter().map(|f| f.id.clone()).collect();
    assert_eq!(
        features::duplicates::delete(&store, &report, &all, &ctx).count,
        0
    );
    let chosen = report.files.iter().skip(1).map(|f| f.id.clone()).collect();
    let result = features::duplicates::delete(&store, &report, &chosen, &ctx);
    assert_eq!(result.count, 2);
    assert!(report.files[0].path.exists());
    let mut remaining = report.clone();
    remaining
        .files
        .retain(|f| !result.succeeded.contains(&f.id));
    features::duplicates::refresh_categories(&mut remaining);
    assert!(remaining.categories.is_empty());
    assert!(remaining.files.is_empty());
}
#[test]
fn duplicate_scan_finds_nested_and_empty_copies_and_rejects_same_size_different_content() {
    let temp = tempfile::tempdir().unwrap();
    fs::create_dir(temp.path().join("nested")).unwrap();
    for (name, contents) in [
        ("first.txt", "matching"),
        ("nested/copy.bin", "matching"),
        ("different.txt", "differnt"),
        ("empty.txt", ""),
        ("nested/empty.txt", ""),
    ] {
        fs::write(temp.path().join(name), contents).unwrap();
    }
    let report =
        features::duplicates::scan(temp.path(), &Context::new(Feature::Duplicates)).unwrap();
    assert_eq!(report.categories.len(), 2);
    assert_eq!(report.files.len(), 4);
    assert!(
        !report
            .files
            .iter()
            .any(|f| f.path.ends_with("different.txt"))
    );
}
#[test]
fn large_file_threshold_is_strictly_over_thirty_mib() {
    assert_eq!(features::large_files::MINIMUM, 30 * common::MIB + 1);
    assert_eq!(
        features::large_files::category(Path::new("codex.exe")),
        Some("installers")
    );
    assert_eq!(
        features::large_files::category(Path::new("README.md")),
        Some("other")
    );
    let temp = tempfile::tempdir().unwrap();
    for (name, n) in [
        ("exact.zip", 30 * common::MIB),
        ("larger.zip", 30 * common::MIB + 1),
    ] {
        fs::File::create(temp.path().join(name))
            .unwrap()
            .set_len(n)
            .unwrap();
    }
    let report = features::large_files::scan_root(
        temp.path(),
        features::large_files::MINIMUM,
        &Context::new(Feature::LargeFiles),
    )
    .unwrap();
    assert_eq!(report.files.len(), 1);
    assert_eq!(report.files[0].path.file_name().unwrap(), "larger.zip");
}
#[test]
fn settings_and_legacy_activity_are_preserved() {
    let temp = tempfile::tempdir().unwrap();
    let store = Store::new(Some(temp.path().into()), true).unwrap();
    store
        .write(
            "settings.json",
            &serde_json::json!({"language":"zh-TW","close_to_tray":true,"cleanup_time":"19:20"}),
        )
        .unwrap();
    assert_eq!(store.settings().language, "zh-TW");
    assert!(store.settings().close_to_tray);
    fs::write(
        temp.path().join("operations.jsonl"),
        "{\"time\":1793214000,\"action\":\"delete\",\"count\":2,\"ok\":true}\n",
    )
    .unwrap();
    let rows = store.activities();
    assert_eq!(rows.len(), 1);
    assert!(!rows[0].id.is_empty());
    store.replace_activities(&[]).unwrap();
    assert!(store.activities().is_empty());
}
#[test]
fn cancelled_copy_never_removes_original() {
    let temp = tempfile::tempdir().unwrap();
    let source = temp.path().join("source");
    let target = temp.path().join("target");
    fs::write(&source, vec![1; 1024 * 1024]).unwrap();
    let ctx = Context::new(Feature::AppMover);
    ctx.cancel.cancel();
    assert!(common::copy_verified(&source, &target, &ctx, |_| {}).is_err());
    assert!(source.exists());
    assert!(!target.exists());
}
#[test]
fn tree_snapshot_detects_a_content_change() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("app");
    fs::create_dir(&root).unwrap();
    fs::write(root.join("app.bin"), b"before").unwrap();
    let ctx = Context::new(Feature::AppMover);
    let tree = app_mover::snapshot(&root, &ctx).unwrap();
    fs::write(root.join("app.bin"), b"after").unwrap();
    assert!(app_mover::equal_tree(&root, &tree, &ctx).is_err());
}
#[test]
fn deferred_mover_verification_rejects_empty_and_hard_linked_trees() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("app");
    fs::create_dir(&root).unwrap();
    let ctx = Context::new(Feature::AppMover);
    assert!(app_mover::snapshot(&root, &ctx).is_err());
    let file = root.join("app.bin");
    fs::write(&file, b"fixture").unwrap();
    assert!(app_mover::snapshot(&root, &ctx).is_ok());
    fs::hard_link(&file, temp.path().join("outside.bin")).unwrap();
    assert!(app_mover::snapshot(&root, &ctx).is_err());
}
#[test]
fn mover_scan_preserves_registered_sizes_without_full_measurements() {
    let ctx = Context::new(Feature::AppMover);
    let report = app_mover::scan(&ctx).unwrap();
    assert!(!report.cancelled);
    assert!(!report.token.is_empty());
    assert_eq!(report.counts.get("measured_directories"), None);
    for app in &report.apps {
        assert!(!app.record.key.is_empty());
        assert!(
            !features::uninstall::excluded_directory(&app.path),
            "{}",
            app.name
        );
        if app.record.estimated_kib != 0 {
            assert_eq!(app.logical, app.record.estimated_kib.saturating_mul(1024));
        }
        assert_eq!((app.physical, app.files), (0, 0));
        assert!(!app.complete);
    }
    println!(
        "Registry scan: {} apps in {} ms",
        report.apps.len(),
        report.elapsed_ms
    );
}
#[test]
fn compression_and_restore_use_actual_allocation() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("owned-app");
    fs::create_dir(&root).unwrap();
    let file = root.join("owned.bin");
    fs::write(&file, vec![0x45; 4 * 1024 * 1024]).unwrap();
    if filesystem::disk(&root).unwrap().filesystem != "NTFS" {
        return;
    }
    let store = Store::new(Some(temp.path().join("profile")), true).unwrap();
    let app = AppInfo {
        id: common::token(),
        name: "Owned test fixture".into(),
        path: root.clone(),
        root_identity: filesystem::identity(&root).ok(),
        ..Default::default()
    };
    let ctx = Context::new(Feature::Compression);
    let before = filesystem::allocated(&file).unwrap();
    let record = app_compression::compress_one(&store, &app, &ctx).unwrap();
    assert_eq!(record["status"], "success");
    assert_eq!(record["old_size"], before);
    assert_eq!(record["new_size"], filesystem::allocated(&file).unwrap());
    let summary = features::uninstall::directory_size(&root, &Context::new(Feature::Uninstall));
    assert_eq!(summary.logical, 4 * 1024 * 1024);
    assert_eq!(summary.files, 1);
    let id = record["id"].as_str().unwrap();
    let result = app_compression::restore(&store, id, &ctx).unwrap();
    assert!(result.ok(), "{:?}", result.errors);
    assert_eq!(result.count, 1);
    assert_eq!(fs::read(&file).unwrap(), vec![0x45; 4 * 1024 * 1024]);
    let records = app_compression::history(&store, true, &ctx);
    assert_eq!(records[0]["restored"], true);
    assert!(app_compression::restore(&store, id, &ctx).is_err());
}
#[test]
fn application_move_and_restore_round_trip_across_volumes() {
    let temp = tempfile::tempdir().unwrap();
    let source = temp.path().join("owned-app");
    fs::create_dir(&source).unwrap();
    fs::create_dir(source.join("empty-directory")).unwrap();
    fs::write(source.join("keep.bin"), vec![0x31; 1024 * 1024]).unwrap();
    let source_disk = filesystem::disk(&source).unwrap();
    let Some(other) = filesystem::disks()
        .into_iter()
        .find(|d| d.filesystem == "NTFS" && !d.root.eq_ignore_ascii_case(&source_disk.root))
    else {
        return;
    };
    let destination = tempfile::Builder::new()
        .prefix("cplus-owned-rust-test-")
        .tempdir_in(&other.root)
        .unwrap();
    assert!(common::inside(destination.path(), Path::new(&other.root)));
    let store = Store::new(Some(temp.path().join("profile")), true).unwrap();
    let ctx = Context::new(Feature::AppMover);
    let app = AppInfo {
        id: common::token(),
        name: "Owned move fixture".into(),
        path: source.clone(),
        root_identity: filesystem::identity(&source).ok(),
        ..Default::default()
    };
    let row = app_mover::move_one(&store, &app, destination.path(), &ctx).unwrap();
    assert_eq!(row["ok"], true);
    assert!(filesystem::identity(&source).unwrap().redirected());
    assert_eq!(
        fs::read(source.join("keep.bin")).unwrap(),
        vec![0x31; 1024 * 1024]
    );
    let result = app_mover::restore(&store, row["id"].as_str().unwrap(), &ctx).unwrap();
    assert!(result.ok());
    assert!(!filesystem::identity(&source).unwrap().redirected());
    assert!(source.join("empty-directory").is_dir());
    assert_eq!(
        fs::read(source.join("keep.bin")).unwrap(),
        vec![0x31; 1024 * 1024]
    );
}
#[test]
fn instance_activation_is_signalled_without_second_window() {
    let name = format!("CPlusCleaner-owned-test-{}", common::token());
    let first = cplus_platform_windows::processes::SingleInstance::acquire(&name, false).unwrap();
    let second = cplus_platform_windows::processes::SingleInstance::acquire(&name, true).unwrap();
    assert!(first.primary);
    assert!(!second.primary);
    assert!(first.activation_pending());
    assert!(!first.activation_pending());
}
#[test]
fn long_unicode_paths_are_measured_and_deleted_safely() {
    let temp = tempfile::tempdir().unwrap();
    let mut folder = temp.path().to_path_buf();
    for _ in 0..9 {
        folder = folder.join("long-filename-测试-path-segment");
    }
    fs::create_dir_all(&folder).unwrap();
    let file = folder.join("owned-cache.tmp");
    fs::write(&file, b"owned long path").unwrap();
    assert!(file.as_os_str().len() > 260);
    assert_eq!(filesystem::identity(&file).unwrap().size, 15);
    assert!(filesystem::allocated(&file).is_ok());
    let ctx = Context::new(Feature::Drive);
    let rule = scope(temp.path());
    let (files, _) = common::scan_rules(std::slice::from_ref(&rule), &ctx, |_| false);
    assert_eq!(files.len(), 1);
    assert_eq!(common::delete_entries(&files, &[rule], &ctx).count, 1);
}
