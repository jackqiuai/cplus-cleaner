use cplus_core::{
    Feature,
    features::uninstall::{self, MeasureMode},
    jobs::Context,
};
use cplus_platform_windows::filesystem as winfs;
use std::{
    fs,
    io::Write,
    os::windows::{ffi::OsStrExt, io::AsRawHandle},
    path::Path,
};
use windows_sys::Win32::{
    Foundation::HANDLE,
    Storage::FileSystem::*,
    System::{
        IO::DeviceIoControl,
        Ioctl::{FSCTL_SET_COMPRESSION, FSCTL_SET_SPARSE},
    },
};

fn measure(root: &Path, mode: MeasureMode) -> uninstall::Measurement {
    uninstall::measure_for_scan_with_mode(root, &Context::new(Feature::AppMover), mode)
}
fn same(a: &uninstall::Measurement, b: &uninstall::Measurement) {
    assert_eq!(
        (
            a.logical,
            a.physical,
            a.modified,
            a.missing,
            a.unreadable,
            a.linked,
            a.compressed
        ),
        (
            b.logical,
            b.physical,
            b.modified,
            b.missing,
            b.unreadable,
            b.linked,
            b.compressed
        )
    );
    let paths = |m: &uninstall::Measurement| {
        let mut p: Vec<_> = m
            .files
            .iter()
            .map(|(p, id)| (p.clone(), id.clone()))
            .collect();
        p.sort_by(|a, b| a.0.cmp(&b.0));
        p
    };
    assert_eq!(paths(a), paths(b));
}
fn ioctl(file: &fs::File, code: u32, compression: bool) {
    let format = COMPRESSION_FORMAT_DEFAULT;
    let mut returned = 0;
    let (input, len) = if compression {
        ((&format as *const u16).cast(), 2)
    } else {
        (std::ptr::null(), 0)
    };
    assert_ne!(
        unsafe {
            DeviceIoControl(
                file.as_raw_handle() as HANDLE,
                code,
                input,
                len,
                std::ptr::null_mut(),
                0,
                &mut returned,
                std::ptr::null_mut(),
            )
        },
        0
    );
}

#[test]
fn native_and_fallback_match_legacy_totals_on_unicode_deep_and_empty_paths() {
    let temp = tempfile::tempdir().unwrap();
    let mut deep = temp.path().to_path_buf();
    for _ in 0..8 {
        deep = deep.join("中文-very-long-owned-directory");
    }
    fs::create_dir_all(&deep).unwrap();
    fs::create_dir(temp.path().join("empty")).unwrap();
    fs::write(deep.join("data.bin"), vec![4; 12000]).unwrap();
    fs::write(temp.path().join("zero"), []).unwrap();
    let old = uninstall::measure(temp.path(), &Context::new(Feature::AppMover));
    let batch = measure(temp.path(), MeasureMode::Auto);
    let fallback = measure(temp.path(), MeasureMode::Directory);
    assert!(batch.complete(), "{:?}", batch.errors);
    assert!(batch.stats.batch_pages > 0);
    assert!(fallback.stats.fallback);
    same(&old, &batch);
    same(&old, &fallback);
}

#[test]
fn actual_sparse_and_compressed_allocation_is_not_replaced_by_directory_size() {
    let temp = tempfile::tempdir().unwrap();
    let sparse = fs::File::create(temp.path().join("sparse.bin")).unwrap();
    ioctl(&sparse, FSCTL_SET_SPARSE, false);
    sparse.set_len(4 * 1024 * 1024).unwrap();
    drop(sparse);
    let mut compressed = fs::OpenOptions::new()
        .create(true)
        .truncate(true)
        .read(true)
        .write(true)
        .open(temp.path().join("compressed.bin"))
        .unwrap();
    ioctl(&compressed, FSCTL_SET_COMPRESSION, true);
    compressed.write_all(&vec![2; 1024 * 1024]).unwrap();
    compressed.sync_all().unwrap();
    drop(compressed);
    let old = uninstall::measure(temp.path(), &Context::new(Feature::AppMover));
    let batch = measure(temp.path(), MeasureMode::Auto);
    assert!(batch.complete(), "{:?}", batch.errors);
    assert!(batch.compressed);
    assert!(batch.physical < batch.logical);
    same(&old, &batch);
    assert_eq!(batch.stats.allocation_queries, 2);
}

#[test]
fn hard_links_count_physical_space_once_and_make_the_root_ineligible() {
    let temp = tempfile::tempdir().unwrap();
    let a = temp.path().join("a");
    fs::write(&a, b"same").unwrap();
    fs::hard_link(&a, temp.path().join("b")).unwrap();
    let batch = measure(temp.path(), MeasureMode::Auto);
    let fallback = measure(temp.path(), MeasureMode::Directory);
    assert!(!batch.complete());
    assert!(batch.linked);
    assert_eq!(batch.physical, winfs::allocated(&a).unwrap());
    assert_eq!(batch.logical, 8);
    same(&batch, &fallback);
}

#[test]
fn skipped_junctions_and_remote_entries_never_produce_complete_measurements() {
    let temp = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    fs::write(outside.path().join("outside"), b"outside").unwrap();
    let link = temp.path().join("link");
    winfs::create_junction(&link, outside.path()).unwrap();
    for mode in [MeasureMode::Auto, MeasureMode::Directory] {
        let measured = measure(temp.path(), mode);
        assert!(measured.linked);
        assert!(!measured.complete());
        assert!(measured.files.is_empty());
    }
    fs::remove_dir(link).unwrap();
    let path = temp.path().join("offline");
    fs::write(&path, b"offline").unwrap();
    let wide: Vec<_> = path.as_os_str().encode_wide().chain([0]).collect();
    let original = unsafe { GetFileAttributesW(wide.as_ptr()) };
    assert_ne!(
        unsafe { SetFileAttributesW(wide.as_ptr(), original | FILE_ATTRIBUTE_OFFLINE) },
        0
    );
    for mode in [MeasureMode::Auto, MeasureMode::Directory] {
        let measured = measure(temp.path(), mode);
        assert!(!measured.complete());
        assert!(measured.unreadable > 0);
        assert!(measured.files.is_empty());
    }
    unsafe {
        SetFileAttributesW(wide.as_ptr(), original);
    }
}

#[test]
fn cancellation_and_missing_roots_do_not_authorize_partial_measurements() {
    let temp = tempfile::tempdir().unwrap();
    fs::write(temp.path().join("one"), b"one").unwrap();
    let ctx = Context::new(Feature::AppMover);
    ctx.cancel.cancel();
    let measured = uninstall::measure_for_scan(temp.path(), &ctx);
    assert!(!measured.complete());
    assert!(measured.stats.cancelled);
    assert!(!measure(&temp.path().join("missing"), MeasureMode::Auto).complete());
}

#[test]
fn multiple_native_buffers_do_not_truncate_large_directories() {
    let temp = tempfile::tempdir().unwrap();
    for n in 0..900 {
        fs::write(
            temp.path().join(format!("long-owned-file-{n:04}.data")),
            b"x",
        )
        .unwrap();
    }
    let measured = measure(temp.path(), MeasureMode::Auto);
    assert!(measured.complete(), "{:?}", measured.errors);
    assert_eq!(measured.files.len(), 900);
    assert!(measured.stats.batch_pages > 1);
}

#[test]
fn forward_slash_windows_paths_produce_complete_measurements() {
    let temp = tempfile::tempdir().unwrap();
    fs::write(temp.path().join("one.dat"), b"one").unwrap();
    let text = temp.path().to_string_lossy().replace('\\', "/");
    for mode in [MeasureMode::Auto, MeasureMode::Directory] {
        let measured = measure(Path::new(&text), mode);
        assert!(measured.complete(), "{:?}", measured.errors);
        assert_eq!(measured.files.len(), 1);
        assert_eq!(measured.logical, 3);
    }
}

#[test]
fn wof_lzx_files_keep_their_logical_size_and_actual_disk_usage() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("owned-lzx.bin");
    fs::write(&path, vec![0x42; 2 * 1024 * 1024]).unwrap();
    let compact = std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap())
        .join("System32/compact.exe");
    let status = cplus_platform_windows::processes::hidden(compact)
        .args(["/C", "/EXE:LZX", "/I", "/A", "/F"])
        .arg(&path)
        .output()
        .unwrap();
    assert!(status.status.success());
    assert!(winfs::wof_compressed(&path));
    let old = uninstall::measure(temp.path(), &Context::new(Feature::AppMover));
    let measured = measure(temp.path(), MeasureMode::Auto);
    assert!(measured.complete(), "{:?}", measured.errors);
    assert!(measured.compressed);
    same(&old, &measured);
    assert_eq!(measured.logical, 2 * 1024 * 1024);
    assert_eq!(measured.physical, winfs::allocated(&path).unwrap());
    assert!(measured.physical < measured.logical);
}
