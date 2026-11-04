use cplus_core::{Feature, Store, common, features::large_files, jobs::Context};
use cplus_platform_windows::filesystem::{self, large_scan::ScanMode};
use std::{fs, io::Write, os::windows::io::AsRawHandle, path::Path};
use windows_sys::Win32::{
    Foundation::HANDLE,
    Storage::FileSystem::COMPRESSION_FORMAT_DEFAULT,
    System::{
        IO::DeviceIoControl,
        Ioctl::{FSCTL_SET_COMPRESSION, FSCTL_SET_SPARSE},
    },
};

fn sized(path: &Path, bytes: u64) {
    fs::File::create(path).unwrap().set_len(bytes).unwrap();
}

#[test]
fn program_components_are_excluded_and_old_results_cannot_delete_or_move_them() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("downloads");
    fs::create_dir(&root).unwrap();
    for name in [
        "module.DLL",
        "driver.sys",
        "device.DrV",
        "control.ocx",
        "boot.efi",
        "language.mui",
        "font.TTF",
        "collection.ttc",
        "font.otf",
        "collection.otc",
        "legacy.fon",
        "bitmap.fnt",
        "web.woff",
        "web.woff2",
        "web.eot",
        "type1.pfb",
        "metrics.pfm",
        "Setup.exe",
        "archive.zip",
        "user-data.bin",
    ] {
        sized(&root.join(name), large_files::MINIMUM);
    }
    for mode in [ScanMode::Auto, ScanMode::Directory] {
        let report = scan(&root, mode);
        assert_eq!(report.files.len(), 3);
        assert!(report.files.iter().all(|f| matches!(
            f.path.extension().and_then(|e| e.to_str()),
            Some("exe" | "zip" | "bin")
        )));
    }
    for old_name in ["module.DLL", "font.TTF"] {
        let mut report = scan(&root, ScanMode::Directory);
        let mut old_file = report.files[0].clone();
        old_file.path = root.join(old_name);
        old_file.id = format!("old-{old_name}-result");
        old_file.identity = filesystem::identity(&old_file.path).unwrap();
        old_file.blocked.clear();
        let id = old_file.id.clone();
        let path = old_file.path.clone();
        report.files.push(old_file);
        let store = Store::new(Some(temp.path().join("profile")), true).unwrap();
        let result = large_files::delete(
            &store,
            &report,
            std::slice::from_ref(&id),
            &Context::new(Feature::LargeFiles),
        );
        assert_eq!(result.count, 0);
        assert!(path.exists());
        let destination = temp.path().join(format!("destination-{old_name}"));
        fs::create_dir(&destination).unwrap();
        let result = large_files::move_files(
            &store,
            &report,
            &[id].into_iter().collect(),
            &destination,
            &Context::new(Feature::LargeFiles),
        );
        assert_eq!(result.count, 0);
        assert!(path.exists());
        assert!(fs::read_dir(destination).unwrap().next().is_none());
    }
}
fn scan(root: &Path, mode: ScanMode) -> cplus_core::ScanResult {
    large_files::scan_root_with_mode(
        root,
        large_files::MINIMUM,
        mode,
        &Context::new(Feature::LargeFiles),
    )
    .unwrap()
}

#[test]
fn native_directory_fallback_matches_all_types_without_company_name_exclusion() {
    let temp = tempfile::tempdir().unwrap();
    let notes = temp.path().join("MicrosoftNotes");
    fs::create_dir(&notes).unwrap();
    for (name, size) in [
        ("archive.zip", 34),
        ("report.pdf", 32),
        ("image.png", 33),
        ("unknown.bin", 35),
    ] {
        sized(&temp.path().join(name), size * common::MIB);
    }
    sized(&notes.join("notes.log"), 31 * common::MIB);
    sized(&temp.path().join("exact.zip"), 30 * common::MIB);
    for n in 0..100 {
        fs::write(temp.path().join(format!("small-{n}.txt")), b"small").unwrap();
    }
    let ctx = Context::new(Feature::LargeFiles);
    let auto =
        large_files::scan_root_with_mode(temp.path(), large_files::MINIMUM, ScanMode::Auto, &ctx)
            .unwrap();
    let directory = scan(temp.path(), ScanMode::Directory);
    assert_eq!(auto.files.len(), 5);
    assert_eq!(
        auto.files
            .iter()
            .map(|f| (&f.path, f.size, f.allocated))
            .collect::<Vec<_>>(),
        directory
            .files
            .iter()
            .map(|f| (&f.path, f.size, f.allocated))
            .collect::<Vec<_>>()
    );
    assert!(
        auto.files
            .windows(2)
            .all(|pair| pair[0].size >= pair[1].size)
    );
    assert!(auto.categories.iter().all(|c| !c.default_selected));
    assert!(auto.categories.iter().any(|c| c.id == "other"));
    assert!(auto.categories.iter().any(|c| c.id == "images"));
    assert_eq!(auto.counts["candidate_validations"], 5);
    assert!(auto.counts["checked"] >= 106);
    assert_eq!(ctx.snapshot().matched, Some(5));
    assert_eq!(
        auto.large_scan.as_ref().unwrap().strategy,
        "win32_directory"
    );
    assert!(auto.large_scan.as_ref().unwrap().fallback.is_some());
}

#[test]
fn hard_links_are_display_only_and_junctions_never_contribute_candidates() {
    let temp = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    sized(&temp.path().join("first.bin"), 32 * common::MIB);
    fs::hard_link(temp.path().join("first.bin"), temp.path().join("alias.bin")).unwrap();
    sized(&outside.path().join("outside.pdf"), 34 * common::MIB);
    let junction = temp.path().join("jump");
    filesystem::create_junction(&junction, outside.path()).unwrap();
    let report = scan(temp.path(), ScanMode::Auto);
    assert_eq!(report.files.len(), 2);
    assert!(
        report
            .files
            .iter()
            .all(|f| !f.blocked.is_empty() && f.identity.links == 2)
    );
    assert_eq!(report.reclaimable_bytes(), 0);
    assert_eq!(report.allocated_bytes(), report.files[0].allocated);
    assert!(
        large_files::scan_root(
            &junction,
            large_files::MINIMUM,
            &Context::new(Feature::LargeFiles)
        )
        .is_err()
    );
    let store = Store::new(Some(temp.path().join("profile")), true).unwrap();
    let ids = report
        .files
        .iter()
        .map(|f| f.id.clone())
        .collect::<Vec<_>>();
    let deletion = large_files::delete(&store, &report, &ids, &Context::new(Feature::LargeFiles));
    assert_eq!(deletion.count, 0);
    assert_eq!(deletion.errors.len(), 2);
    assert!(temp.path().join("first.bin").exists());
    assert!(outside.path().join("outside.pdf").exists());
}

#[test]
fn sparse_and_compressed_files_keep_logical_threshold_and_physical_estimates() {
    let temp = tempfile::tempdir().unwrap();
    let sparse_path = temp.path().join("sparse.bin");
    let sparse = fs::File::create(&sparse_path).unwrap();
    let mut returned = 0;
    assert_ne!(
        unsafe {
            DeviceIoControl(
                sparse.as_raw_handle() as HANDLE,
                FSCTL_SET_SPARSE,
                std::ptr::null(),
                0,
                std::ptr::null_mut(),
                0,
                &mut returned,
                std::ptr::null_mut(),
            )
        },
        0
    );
    sparse.set_len(64 * common::MIB).unwrap();
    drop(sparse);
    let compressed_path = temp.path().join("compressed.bin");
    let mut compressed = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .open(&compressed_path)
        .unwrap();
    let format = COMPRESSION_FORMAT_DEFAULT;
    assert_ne!(
        unsafe {
            DeviceIoControl(
                compressed.as_raw_handle() as HANDLE,
                FSCTL_SET_COMPRESSION,
                (&format as *const u16).cast(),
                2,
                std::ptr::null_mut(),
                0,
                &mut returned,
                std::ptr::null_mut(),
            )
        },
        0
    );
    let block = vec![0_u8; common::MIB as usize];
    for _ in 0..32 {
        compressed.write_all(&block).unwrap();
    }
    compressed.sync_all().unwrap();
    drop(compressed);
    let report = scan(temp.path(), ScanMode::Directory);
    assert_eq!(report.files.len(), 2);
    for file in &report.files {
        assert!(
            file.size > 30 * common::MIB && file.allocated < file.size,
            "candidate={} logical={} allocated={} attributes={:x}",
            file.path.file_name().unwrap().to_string_lossy(),
            file.size,
            file.allocated,
            file.identity.attributes
        );
    }
    assert!(report.reclaimable_bytes() < report.bytes());
    assert_eq!(report.reclaimable_bytes(), report.allocated_bytes());
}

#[test]
fn cancellation_revokes_authorization_and_progress_advances_without_matches() {
    let temp = tempfile::tempdir().unwrap();
    for n in 0..50 {
        fs::write(temp.path().join(format!("small-{n}.bin")), b"small").unwrap();
    }
    let ctx = Context::new(Feature::LargeFiles);
    let empty = large_files::scan_root(temp.path(), large_files::MINIMUM, &ctx).unwrap();
    assert!(empty.files.is_empty());
    assert!(ctx.snapshot().done >= 50);
    assert_eq!(ctx.snapshot().matched, Some(0));
    assert!(!empty.token.is_empty());
    let ctx = Context::new(Feature::LargeFiles);
    ctx.cancel.cancel();
    let cancelled = large_files::scan_root(temp.path(), large_files::MINIMUM, &ctx).unwrap();
    assert!(cancelled.cancelled && cancelled.token.is_empty() && cancelled.rules.is_empty());
    let store = Store::new(Some(temp.path().join("profile")), true).unwrap();
    let result = large_files::delete(&store, &cancelled, &[], &ctx);
    assert!(!result.errors.is_empty());
}

#[test]
fn changed_candidates_are_preserved_and_fresh_deletion_reports_disk_usage() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("candidate.bin");
    sized(&path, 32 * common::MIB);
    let report = scan(temp.path(), ScanMode::Directory);
    let store = Store::new(Some(temp.path().join("profile")), true).unwrap();
    let selected = vec![report.files[0].id.clone()];
    fs::OpenOptions::new()
        .append(true)
        .open(&path)
        .unwrap()
        .write_all(b"changed")
        .unwrap();
    let rejected = large_files::delete(
        &store,
        &report,
        &selected,
        &Context::new(Feature::LargeFiles),
    );
    assert_eq!(rejected.count, 0);
    assert!(path.exists());
    let fresh = scan(temp.path(), ScanMode::Directory);
    let deleted = large_files::delete(
        &store,
        &fresh,
        &[fresh.files[0].id.clone()],
        &Context::new(Feature::LargeFiles),
    );
    assert_eq!(deleted.count, 1);
    assert_eq!(deleted.bytes, fresh.files[0].allocated);
    assert!(!path.exists());
}

#[test]
fn directory_revalidation_rejects_an_ancestor_replaced_with_a_junction() {
    use cplus_platform_windows::filesystem::large_scan::{self, Event};
    use std::cell::Cell;
    let temp = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    let branch = temp.path().join("branch");
    let child = branch.join("child");
    fs::create_dir_all(&child).unwrap();
    fs::create_dir(outside.path().join("child")).unwrap();
    fs::write(outside.path().join("child/outside.bin"), b"outside").unwrap();
    let swapped = Cell::new(false);
    let mut candidates = 0;
    let summary = large_scan::scan(
        temp.path(),
        1,
        ScanMode::Directory,
        &|| false,
        &|_| false,
        &mut |event| match event {
            Event::Visit { path, .. } if path == child && !swapped.get() => {
                fs::rename(&branch, temp.path().join("retired")).unwrap();
                filesystem::create_junction(&branch, outside.path()).unwrap();
                swapped.set(true);
            }
            Event::Candidate { .. } => candidates += 1,
            _ => {}
        },
    )
    .unwrap();
    assert!(swapped.get());
    assert_eq!(candidates, 0);
    assert!(summary.skipped > 0);
    assert!(outside.path().join("child/outside.bin").exists());
}

#[test]
fn cancellation_during_directory_enumeration_stops_candidate_delivery() {
    use cplus_platform_windows::filesystem::large_scan::{self, Event};
    use std::cell::Cell;
    let temp = tempfile::tempdir().unwrap();
    for n in 0..50 {
        fs::write(temp.path().join(format!("file-{n}.bin")), b"data").unwrap();
    }
    let cancelled = Cell::new(false);
    let mut candidates = 0;
    let summary = large_scan::scan(
        temp.path(),
        1,
        ScanMode::Directory,
        &|| cancelled.get(),
        &|_| false,
        &mut |event| match event {
            Event::Visit { checked: 3, .. } => cancelled.set(true),
            Event::Candidate { .. } => {
                assert!(!cancelled.get());
                candidates += 1;
            }
            _ => {}
        },
    )
    .unwrap();
    assert!(summary.cancelled);
    assert_eq!(summary.checked, 3);
    assert_eq!(candidates, 2);
}

#[test]
fn complete_results_are_not_truncated_at_two_thousand_files() {
    let temp = tempfile::tempdir().unwrap();
    for n in 0..2_005 {
        fs::write(temp.path().join(format!("file-{n}.bin")), b"data").unwrap();
    }
    let report = large_files::scan_root_with_mode(
        temp.path(),
        1,
        ScanMode::Directory,
        &Context::new(Feature::LargeFiles),
    )
    .unwrap();
    assert_eq!(report.files.len(), 2_005);
    assert_eq!(
        report.categories.iter().map(|c| c.count).sum::<u64>(),
        2_005
    );
}
