use crate::{FileEntry, common, jobs::Context};
use anyhow::{Result, bail};
use cplus_platform_windows::filesystem::{self as winfs, content};
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::{Read, Seek, SeekFrom},
    sync::{
        Mutex,
        atomic::{AtomicU64, AtomicUsize, Ordering},
        mpsc,
    },
    time::Duration,
};

const SAMPLE: u64 = 16 * 1024;
#[derive(Clone, Copy)]
pub(super) enum Stage {
    Sample,
    Full,
}
impl Stage {
    fn phase(self) -> &'static str {
        match self {
            Self::Sample => "Sampling contents",
            Self::Full => "Verifying full contents",
        }
    }
    fn expected(self, size: u64) -> u64 {
        match self {
            Self::Full => size,
            Self::Sample => offsets(size).len() as u64 * size.min(SAMPLE),
        }
    }
}
fn offsets(size: u64) -> Vec<u64> {
    let tail = size.saturating_sub(SAMPLE);
    let mut values = vec![0, tail / 2, tail];
    values.dedup();
    values
}
pub(super) struct Outcome {
    pub hashes: Vec<Option<[u8; 32]>>,
    pub bytes: u64,
    pub failures: Vec<(usize, String, bool)>,
}
pub(super) fn run(files: &[FileEntry], stage: Stage, workers: usize, ctx: &Context) -> Outcome {
    let next = AtomicUsize::new(0);
    let read = AtomicU64::new(0);
    let current = Mutex::new(String::new());
    let total = files
        .iter()
        .fold(0_u64, |n, f| n.saturating_add(stage.expected(f.size)));
    let mut output = Outcome {
        hashes: vec![None; files.len()],
        bytes: 0,
        failures: vec![],
    };
    let workers = workers.clamp(1, 4).min(files.len().max(1));
    ctx.set(stage.phase(), "", 0, Some(total), 0);
    std::thread::scope(|scope| {
        let (tx, rx) = mpsc::sync_channel(workers * 2);
        for _ in 0..workers {
            let tx = tx.clone();
            let next = &next;
            let read = &read;
            let current = &current;
            scope.spawn(move || {
                while !ctx.cancel.cancelled() {
                    let index = next.fetch_add(1, Ordering::Relaxed);
                    let Some(file) = files.get(index) else { break };
                    *current.lock().unwrap_or_else(|e| e.into_inner()) =
                        common::display_path(&file.path);
                    let result = hash(file, stage, ctx, read);
                    if tx.send((index, result)).is_err() {
                        break;
                    }
                }
            });
        }
        drop(tx);
        loop {
            match rx.recv_timeout(Duration::from_millis(100)) {
                Ok((index, result)) => match result {
                    Ok(hash) => output.hashes[index] = Some(hash),
                    Err(error) if !ctx.cancel.cancelled() => {
                        let changed = winfs::identity(&files[index].path)
                            .is_ok_and(|now| now != files[index].identity)
                            || !files[index].path.exists()
                            || error.to_string().contains("changed");
                        output.failures.push((index, error.to_string(), changed));
                    }
                    Err(_) => {}
                },
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                Err(mpsc::RecvTimeoutError::Disconnected) => break,
            }
            let done = read.load(Ordering::Relaxed);
            ctx.set(
                stage.phase(),
                current.lock().unwrap_or_else(|e| e.into_inner()).clone(),
                done,
                Some(total),
                done,
            );
        }
    });
    output.bytes = read.load(Ordering::Relaxed);
    ctx.set(
        stage.phase(),
        "",
        output.bytes,
        Some(output.bytes),
        output.bytes,
    );
    output
}

fn hash(entry: &FileEntry, stage: Stage, ctx: &Context, read: &AtomicU64) -> Result<[u8; 32]> {
    common::validate_file(entry)?;
    let mut file = content::open(&entry.path, &entry.identity, false)?;
    let mut hasher = Sha256::new();
    match stage {
        Stage::Sample => {
            hasher.update(entry.size.to_le_bytes());
            let mut buffer = vec![0; entry.size.min(SAMPLE) as usize];
            for offset in offsets(entry.size) {
                ctx.cancel.check()?;
                file.seek(SeekFrom::Start(offset))?;
                file.read_exact(&mut buffer)?;
                read.fetch_add(buffer.len() as u64, Ordering::Relaxed);
                hasher.update(offset.to_le_bytes());
                hasher.update(&buffer);
            }
        }
        Stage::Full => read_full(&mut file, &mut hasher, ctx, |n| {
            read.fetch_add(n, Ordering::Relaxed);
        })?,
    }
    validate_open(entry, &file)?;
    Ok(hasher.finalize().into())
}
pub(super) fn validate_open(entry: &FileEntry, file: &File) -> Result<()> {
    if content::identity(file)? != entry.identity {
        bail!("File changed during content verification");
    }
    common::validate_file(entry)
}
pub(super) fn verified_digest(
    entry: &FileEntry,
    file: &mut File,
    ctx: &Context,
    report: impl FnMut(u64),
) -> Result<String> {
    validate_open(entry, file)?;
    file.seek(SeekFrom::Start(0))?;
    let mut hasher = Sha256::new();
    read_full(file, &mut hasher, ctx, report)?;
    validate_open(entry, file)?;
    Ok(format!("{:x}", hasher.finalize()))
}
fn read_full(
    file: &mut File,
    hasher: &mut Sha256,
    ctx: &Context,
    mut report: impl FnMut(u64),
) -> Result<()> {
    let mut buffer = vec![0; 1024 * 1024];
    loop {
        ctx.cancel.check()?;
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        hasher.update(&buffer[..n]);
        report(n as u64);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Feature, Rule};
    #[test]
    fn bounded_parallel_hashing_matches_serial_results_and_read_totals() {
        let temp = tempfile::tempdir().unwrap();
        for n in 0..8 {
            std::fs::write(
                temp.path().join(format!("{n}.data")),
                vec![n % 3; 96 * 1024],
            )
            .unwrap();
        }
        let rule = Rule {
            id: "test".into(),
            category: "duplicates".into(),
            root: temp.path().into(),
            recursive: true,
            patterns: vec![],
            excluded: vec![],
            days: 0,
            root_identity: winfs::identity(temp.path()).ok(),
            chat_app: None,
        };
        let (files, _) = common::scan_rules(&[rule], &Context::new(Feature::Duplicates), |_| false);
        assert_eq!(files.len(), 8);
        for stage in [Stage::Sample, Stage::Full] {
            let serial = run(&files, stage, 1, &Context::new(Feature::Duplicates));
            let parallel = run(&files, stage, 4, &Context::new(Feature::Duplicates));
            assert!(serial.failures.is_empty() && parallel.failures.is_empty());
            assert_eq!(serial.hashes, parallel.hashes);
            assert_eq!(serial.bytes, parallel.bytes);
        }
    }
}
