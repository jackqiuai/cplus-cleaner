use cplus_core::{
    features::duplicates::{self, KeeperRule, Location},
    jobs::Context,
    *,
};
use cplus_platform_windows::filesystem::{self as winfs, content, large_scan::ScanMode};
use std::{
    collections::BTreeSet,
    fs,
    io::Write,
    os::windows::io::AsRawHandle,
    path::Path,
    time::{Duration, Instant, UNIX_EPOCH},
};
use windows_sys::Win32::{
    Foundation::HANDLE,
    System::{IO::DeviceIoControl, Ioctl::FSCTL_SET_SPARSE},
};

fn scan(root: &Path, minimum: u64) -> ScanResult {
    duplicates::scan_locations(
        &[Location::cleanable(root)],
        minimum,
        &Context::new(Feature::Duplicates),
    )
    .unwrap()
}

#[test]
fn single_scope_does_not_include_another_location_or_follow_its_junction() {
    let temp = tempfile::tempdir().unwrap();
    let selected = temp.path().join("selected");
    let other = temp.path().join("selected-other");
    fs::create_dir(&selected).unwrap();
    fs::create_dir(&other).unwrap();
    for name in ["a.txt", "b.txt"] {
        fs::write(selected.join(name), b"selected duplicate").unwrap();
        fs::write(other.join(name), b"outside duplicate").unwrap();
    }
    winfs::create_junction(&selected.join("other-drive"), &other).unwrap();
    for mode in [ScanMode::Auto, ScanMode::Directory] {
        let ctx = Context::new(Feature::Duplicates);
        let report =
            duplicates::scan_locations_with_mode(&[Location::cleanable(&selected)], 0, mode, &ctx)
                .unwrap();
        assert_eq!(report.files.len(), 2);
        assert_eq!(report.categories.len(), 1);
        assert!(
            report
                .files
                .iter()
                .all(|file| common::inside(&file.path, &selected))
        );
        let summary = report.duplicate_scan.as_ref().unwrap();
        assert_eq!(summary.locations.len(), 1);
        assert_eq!(
            common::key(&summary.locations[0].path),
            common::key(&common::canonical(&selected))
        );
        assert!(
            summary.checked <= 3,
            "unexpected traversal: {}",
            summary.checked
        );
        assert!(
            report
                .rules
                .iter()
                .all(|rule| common::key(&rule.root) == common::key(&common::canonical(&selected)))
        );
    }
    fs::remove_dir(selected.join("other-drive")).unwrap();
}
fn sparse(path: &Path, size: u64) {
    let file = fs::File::create(path).unwrap();
    let mut returned = 0;
    assert_ne!(
        unsafe {
            DeviceIoControl(
                file.as_raw_handle() as HANDLE,
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
    file.set_len(size).unwrap();
}
fn rewrite_preserving_snapshot(entry: &FileEntry, bytes: &[u8]) {
    assert_eq!(entry.size, bytes.len() as u64);
    fs::write(&entry.path, bytes).unwrap();
    let ticks = entry.identity.modified - 116_444_736_000_000_000;
    let file = fs::OpenOptions::new()
        .write(true)
        .open(&entry.path)
        .unwrap();
    file.set_modified(
        UNIX_EPOCH + Duration::new(ticks / 10_000_000, ((ticks % 10_000_000) * 100) as u32),
    )
    .unwrap();
    drop(file);
    assert_eq!(winfs::identity(&entry.path).unwrap(), entry.identity);
}

#[test]
fn sampling_is_only_a_filter_and_full_hash_detects_unsampled_changes() {
    let temp = tempfile::tempdir().unwrap();
    let payload = vec![0x42; 1024 * 1024];
    for name in ["original.txt", "renamed.data"] {
        fs::write(temp.path().join(name), &payload).unwrap();
    }
    let mut altered = payload.clone();
    altered[100_000] = 7;
    fs::write(temp.path().join("unsampled-difference.dat"), &altered).unwrap();
    altered[0] = 9;
    fs::write(temp.path().join("sample-difference.dat"), &altered).unwrap();
    fs::write(temp.path().join("unique-size.txt"), b"unique").unwrap();
    let report = scan(temp.path(), 0);
    assert_eq!(report.categories.len(), 1);
    assert_eq!(report.files.len(), 2);
    let summary = report.duplicate_scan.as_ref().unwrap();
    assert_eq!(summary.sampled, 4);
    assert_eq!(summary.hashed, 3);
    assert_eq!(summary.hash_bytes, 3 * 1024 * 1024);
    assert!(
        report
            .files
            .iter()
            .all(|f| !f.path.to_string_lossy().contains("difference"))
    );
}

#[test]
fn minimum_is_inclusive_and_all_files_includes_empty_duplicates() {
    let temp = tempfile::tempdir().unwrap();
    for name in ["a.empty", "b.empty"] {
        fs::write(temp.path().join(name), []).unwrap();
    }
    for name in ["a.small", "b.small"] {
        fs::write(temp.path().join(name), b"small").unwrap();
    }
    for name in ["a.large", "b.large"] {
        fs::write(
            temp.path().join(name),
            vec![8; duplicates::DEFAULT_MINIMUM as usize],
        )
        .unwrap();
    }
    assert_eq!(
        scan(temp.path(), duplicates::DEFAULT_MINIMUM)
            .categories
            .len(),
        1
    );
    assert_eq!(scan(temp.path(), 0).categories.len(), 3);
    assert!(
        scan(temp.path(), duplicates::DEFAULT_MINIMUM + 1)
            .categories
            .is_empty()
    );
}

#[test]
fn overlapping_scopes_preserve_protected_descendants_and_delete_all_cleanable_copies() {
    let temp = tempfile::tempdir().unwrap();
    let other = tempfile::tempdir().unwrap();
    let protected = temp.path().join("protected");
    fs::create_dir(&protected).unwrap();
    fs::write(protected.join("keeper.txt"), b"same content").unwrap();
    fs::write(temp.path().join("clean.txt"), b"same content").unwrap();
    fs::write(other.path().join("copy.txt"), b"same content").unwrap();
    let report = duplicates::scan_locations(
        &[
            Location::cleanable(temp.path()),
            Location {
                path: protected.clone(),
                protected: true,
            },
            Location::cleanable(other.path()),
        ],
        0,
        &Context::new(Feature::Duplicates),
    )
    .unwrap();
    assert_eq!(report.files.len(), 3);
    assert_eq!(report.duplicate_scan.as_ref().unwrap().candidates, 3);
    let keeper = duplicates::keeper_id(&report, &report.categories[0]).unwrap();
    assert!(
        !report
            .files
            .iter()
            .find(|f| f.id == keeper)
            .unwrap()
            .blocked
            .is_empty()
    );
    let suggested = duplicates::suggested_ids(&report, None);
    assert_eq!(suggested.len(), 2);
    let store = Store::new(Some(other.path().join("profile")), true).unwrap();
    let protected_ids = report
        .files
        .iter()
        .filter(|f| !f.blocked.is_empty())
        .map(|f| f.id.clone())
        .collect();
    assert_eq!(
        duplicates::delete(
            &store,
            &report,
            &protected_ids,
            &Context::new(Feature::Duplicates)
        )
        .count,
        0
    );
    let expected = report
        .files
        .iter()
        .filter(|f| suggested.contains(&f.id))
        .map(|f| f.allocated)
        .sum::<u64>();
    let result = duplicates::delete(
        &store,
        &report,
        &suggested,
        &Context::new(Feature::Duplicates),
    );
    assert_eq!(result.count, 2, "{:?}", result.errors);
    assert_eq!(result.bytes, expected);
    assert!(protected.join("keeper.txt").exists());
}

#[test]
fn retained_content_is_rehashed_even_when_size_and_timestamp_match() {
    let temp = tempfile::tempdir().unwrap();
    for name in ["a.txt", "b.txt"] {
        fs::write(temp.path().join(name), b"same content").unwrap();
    }
    let report = scan(temp.path(), 0);
    let id = duplicates::keeper_id(&report, &report.categories[0]).unwrap();
    let keeper = report.files.iter().find(|f| f.id == id).unwrap();
    rewrite_preserving_snapshot(keeper, b"evil content");
    let store = Store::new(Some(temp.path().join("profile")), true).unwrap();
    let result = duplicates::delete(
        &store,
        &report,
        &duplicates::suggested_ids(&report, None),
        &Context::new(Feature::Duplicates),
    );
    assert_eq!(result.count, 0);
    assert!(report.files.iter().all(|f| f.path.exists()));
}

#[test]
fn selected_content_is_rehashed_even_when_snapshot_metadata_matches() {
    let temp = tempfile::tempdir().unwrap();
    for name in ["a.txt", "b.txt"] {
        fs::write(temp.path().join(name), b"same content").unwrap();
    }
    let report = scan(temp.path(), 0);
    let chosen = duplicates::suggested_ids(&report, None);
    let target = report
        .files
        .iter()
        .find(|f| chosen.contains(&f.id))
        .unwrap();
    rewrite_preserving_snapshot(target, b"evil content");
    let store = Store::new(Some(temp.path().join("profile")), true).unwrap();
    let result = duplicates::delete(&store, &report, &chosen, &Context::new(Feature::Duplicates));
    assert_eq!(result.count, 0);
    assert!(target.path.exists());
}

#[test]
fn hard_links_and_junctions_never_become_reclaimable_duplicate_copies() {
    let temp = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    fs::write(temp.path().join("original"), b"same").unwrap();
    fs::hard_link(temp.path().join("original"), temp.path().join("alias")).unwrap();
    for name in ["copy1", "copy2"] {
        fs::write(temp.path().join(name), b"same").unwrap();
    }
    fs::write(outside.path().join("outside"), b"same").unwrap();
    let junction = temp.path().join("redirect");
    winfs::create_junction(&junction, outside.path()).unwrap();
    let report = scan(temp.path(), 0);
    fs::remove_dir(junction).unwrap();
    assert_eq!(report.files.len(), 2);
    assert_eq!(report.duplicate_scan.as_ref().unwrap().hard_links, 2);
    assert!(report.duplicate_scan.as_ref().unwrap().skipped >= 3);
    assert!(outside.path().join("outside").exists());
}

#[test]
fn space_totals_follow_the_actual_keeper_and_physical_allocation() {
    let temp = tempfile::tempdir().unwrap();
    sparse(&temp.path().join("a"), 1024 * 1024);
    fs::write(temp.path().join("longer-name"), vec![0; 1024 * 1024]).unwrap();
    let mut report = scan(temp.path(), 0);
    assert_eq!(report.files.len(), 2);
    let keeper = duplicates::keeper_id(&report, &report.categories[0]).unwrap();
    let selected: u64 = report
        .files
        .iter()
        .filter(|f| f.id != keeper)
        .map(|f| f.allocated)
        .sum();
    assert_eq!(report.bytes(), selected);
    assert_eq!(report.reclaimable_bytes(), selected);
    // Changing an actual allocation estimate must change the selected-space total.
    let target = report
        .files
        .iter_mut()
        .find(|f| f.path.ends_with("longer-name"))
        .unwrap();
    target.allocated = 123;
    duplicates::set_keeper_rule(&mut report, KeeperRule::ShortestName);
    assert_eq!(report.bytes(), 123);
}

#[test]
fn verification_handle_denies_replacement_and_writers() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("keeper");
    fs::write(&path, b"keep").unwrap();
    let identity = winfs::identity(&path).unwrap();
    let handle = content::open(&path, &identity, false).unwrap();
    assert!(fs::OpenOptions::new().write(true).open(&path).is_err());
    assert!(fs::rename(&path, temp.path().join("renamed")).is_err());
    assert!(fs::remove_file(&path).is_err());
    drop(handle);
    fs::OpenOptions::new()
        .write(true)
        .open(&path)
        .unwrap()
        .write_all(b"okay")
        .unwrap();
}

#[test]
fn cancellation_during_full_hash_discards_results_and_delete_authorization() {
    let temp = tempfile::tempdir().unwrap();
    for name in ["a", "b", "c", "d"] {
        sparse(&temp.path().join(name), 64 * 1024 * 1024);
    }
    let ctx = Context::new(Feature::Duplicates);
    let report = std::thread::scope(|scope| {
        let job = scope.spawn(|| {
            duplicates::scan_locations(&[Location::cleanable(temp.path())], 0, &ctx).unwrap()
        });
        let deadline = Instant::now() + Duration::from_secs(10);
        while ctx.snapshot().phase != "Verifying full contents" {
            assert!(Instant::now() < deadline, "hash stage was never reached");
            std::thread::sleep(Duration::from_millis(2));
        }
        ctx.cancel.cancel();
        job.join().unwrap()
    });
    assert!(report.cancelled);
    assert!(
        report.token.is_empty()
            && report.files.is_empty()
            && report.categories.is_empty()
            && report.rules.is_empty()
    );
    let store = Store::new(Some(temp.path().join("profile")), true).unwrap();
    let result = duplicates::delete(
        &store,
        &report,
        &BTreeSet::new(),
        &Context::new(Feature::Duplicates),
    );
    assert_eq!(result.count, 0);
    assert!(!result.errors.is_empty());
}

#[test]
fn automatic_scan_falls_back_and_matches_explicit_directory_traversal() {
    let temp = tempfile::tempdir().unwrap();
    for name in ["one", "two"] {
        fs::write(temp.path().join(name), b"same").unwrap();
    }
    let locations = [Location::cleanable(temp.path())];
    let auto = duplicates::scan_locations_with_mode(
        &locations,
        0,
        ScanMode::Auto,
        &Context::new(Feature::Duplicates),
    )
    .unwrap();
    let directory = duplicates::scan_locations_with_mode(
        &locations,
        0,
        ScanMode::Directory,
        &Context::new(Feature::Duplicates),
    )
    .unwrap();
    assert!(!auto.duplicate_scan.as_ref().unwrap().fallbacks.is_empty());
    assert_eq!(auto.categories[0].id, directory.categories[0].id);
    assert_eq!(auto.bytes(), directory.bytes());
    assert_eq!(
        auto.files.iter().map(|f| &f.id).collect::<Vec<_>>(),
        directory.files.iter().map(|f| &f.id).collect::<Vec<_>>()
    );
}

#[test]
fn explicit_hidden_child_scope_is_scanned_when_parent_would_prune_it() {
    let temp = tempfile::tempdir().unwrap();
    let hidden = temp.path().join(".selected");
    fs::create_dir(&hidden).unwrap();
    fs::write(hidden.join("one"), b"same").unwrap();
    fs::write(temp.path().join("two"), b"same").unwrap();
    assert!(scan(temp.path(), 0).files.is_empty());
    let report = duplicates::scan_locations(
        &[
            Location::cleanable(temp.path()),
            Location::cleanable(hidden),
        ],
        0,
        &Context::new(Feature::Duplicates),
    )
    .unwrap();
    assert_eq!(report.files.len(), 2);
    assert_eq!(report.duplicate_scan.as_ref().unwrap().candidates, 2);
}

#[test]
fn all_four_keeper_rules_change_the_actual_preserved_copy() {
    let temp = tempfile::tempdir().unwrap();
    fs::create_dir(temp.path().join("deep")).unwrap();
    for (name, time) in [("bb", 200), ("deep/a", 300), ("ccc", 100), ("dddd", 400)] {
        let path = temp.path().join(name);
        fs::write(&path, b"same").unwrap();
        fs::OpenOptions::new()
            .write(true)
            .open(path)
            .unwrap()
            .set_modified(UNIX_EPOCH + Duration::from_secs(1_700_000_000 + time))
            .unwrap();
    }
    let mut report = scan(temp.path(), 0);
    for (rule, suffix) in [
        (KeeperRule::ShortestPath, "bb"),
        (KeeperRule::ShortestName, "deep/a"),
        (KeeperRule::Oldest, "ccc"),
        (KeeperRule::Newest, "dddd"),
    ] {
        duplicates::set_keeper_rule(&mut report, rule);
        let id = duplicates::keeper_id(&report, &report.categories[0]).unwrap();
        assert!(
            report
                .files
                .iter()
                .find(|f| f.id == id)
                .unwrap()
                .path
                .ends_with(suffix)
        );
        assert_eq!(duplicates::suggested_ids(&report, None).len(), 3);
    }
}

#[test]
fn busy_writers_are_reported_without_hiding_other_verified_duplicates() {
    let temp = tempfile::tempdir().unwrap();
    for name in ["locked", "copy1", "copy2"] {
        fs::write(temp.path().join(name), b"same").unwrap();
    }
    let writer = fs::OpenOptions::new()
        .write(true)
        .open(temp.path().join("locked"))
        .unwrap();
    let report = scan(temp.path(), 0);
    assert_eq!(report.files.len(), 2);
    assert_eq!(report.duplicate_scan.as_ref().unwrap().unreadable, 1);
    assert!(!report.diagnostics.is_empty());
    drop(writer);
}

#[test]
fn offline_files_are_skipped_before_content_reads() {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_ATTRIBUTE_OFFLINE, GetFileAttributesW, SetFileAttributesW,
    };
    let temp = tempfile::tempdir().unwrap();
    for name in ["offline", "one", "two"] {
        fs::write(temp.path().join(name), b"same").unwrap();
    }
    let path = temp.path().join("offline");
    let wide: Vec<_> = path.as_os_str().encode_wide().chain([0]).collect();
    let old = unsafe { GetFileAttributesW(wide.as_ptr()) };
    assert_ne!(
        unsafe { SetFileAttributesW(wide.as_ptr(), old | FILE_ATTRIBUTE_OFFLINE) },
        0
    );
    let report = scan(temp.path(), 0);
    unsafe {
        SetFileAttributesW(wide.as_ptr(), old);
    }
    assert_eq!(report.files.len(), 2);
    assert_eq!(report.duplicate_scan.as_ref().unwrap().sampled, 2);
    assert!(report.duplicate_scan.as_ref().unwrap().skipped >= 1);
}
