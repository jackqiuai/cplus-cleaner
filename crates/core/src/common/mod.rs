use crate::{FileEntry, Issue, OperationResult, Rule, jobs::Context};
pub mod rule_scan;
use anyhow::{Context as _, Result, bail};
use cplus_platform_windows::filesystem as winfs;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};
pub const MIB: u64 = 1024 * 1024;
pub fn token() -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    hash(
        format!(
            "{}:{}:{}",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap_or_default(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        )
        .as_bytes(),
    )[..32]
        .into()
}
pub fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub fn key(path: &Path) -> String {
    path.to_string_lossy()
        .replace('/', "\\")
        .trim_start_matches(r"\\?\")
        .trim_end_matches('\\')
        .to_lowercase()
}
pub fn inside(path: &Path, root: &Path) -> bool {
    let path = key(path);
    let root = key(root);
    path == root || path.starts_with(&(root + "\\"))
}
pub fn canonical(path: &Path) -> PathBuf {
    fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}
/// Keep extended paths for filesystem I/O, but present regular Windows paths in UI text.
pub fn display_text(value: &str) -> String {
    value.replace(r"\\?\UNC\", r"\\").replace(r"\\?\", "")
}
pub fn display_path(path: &Path) -> String {
    display_text(&path.to_string_lossy())
}
pub fn home() -> PathBuf {
    std::env::var_os("USERPROFILE")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\Users\Default"))
}
pub fn system_drive() -> PathBuf {
    PathBuf::from(format!(
        "{}\\",
        std::env::var("SystemDrive")
            .unwrap_or_else(|_| "C:".into())
            .trim_end_matches('\\')
    ))
}
pub fn env_path(name: &str, fallback: PathBuf) -> PathBuf {
    std::env::var_os(name)
        .map(PathBuf::from)
        .unwrap_or(fallback)
}
pub fn no_links(path: &Path) -> Result<()> {
    for ancestor in path.ancestors() {
        if ancestor.exists() {
            let info = winfs::identity(ancestor)?;
            if info.redirected() {
                bail!("Path contains a junction or symbolic link");
            }
        }
    }
    Ok(())
}
pub fn self_protected(path: &Path) -> bool {
    if std::env::current_exe().is_ok_and(|p| key(&p) == key(path)) {
        return true;
    }
    cfg!(debug_assertions)
        && inside(
            path,
            &PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."),
        )
}
pub fn contains_running_app(path: &Path) -> bool {
    std::env::current_exe().is_ok_and(|exe| inside(&exe, path)) || self_protected(path)
}
pub fn protected_data_root(path: &Path) -> bool {
    let p = canonical(path);
    let k = key(&p);
    if k == key(&system_drive()) || k == key(&home()) || p.parent().is_none() {
        return true;
    }
    [
        "SystemRoot",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramData",
    ]
    .into_iter()
    .filter_map(std::env::var_os)
    .any(|base| inside(&p, &PathBuf::from(base)))
        || self_protected(&p)
}
pub fn wildcard(pattern: &str, value: &str) -> bool {
    globset::GlobBuilder::new(pattern)
        .case_insensitive(true)
        .literal_separator(false)
        .backslash_escape(false)
        .build()
        .map(|g| g.compile_matcher().is_match(value))
        .unwrap_or(false)
}
pub fn expand_env(value: &str) -> Result<String> {
    let re = regex::Regex::new(r"%([^%]+)%").unwrap();
    let mut failure = false;
    let result = re.replace_all(value, |c: &regex::Captures| {
        let name = &c[1];
        let alias = match name.to_uppercase().as_str() {
            "CSIDL_APPDATA" => "APPDATA",
            "CSIDL_LOCAL_APPDATA" => "LOCALAPPDATA",
            "CSIDL_WINDOWS" => "SystemRoot",
            "CSIDL_COMMON_APPDATA" => "ProgramData",
            "CSIDL_PROGRAM_FILES" => "ProgramW6432",
            "CSIDL_PROGRAM_FILESX86" => "ProgramFiles(x86)",
            _ => name,
        };
        std::env::var(alias).unwrap_or_else(|_| {
            failure = true;
            String::new()
        })
    });
    if failure {
        bail!("Unresolved path variable");
    }
    Ok(result.into_owned())
}
pub fn expand_directories(template: &str) -> Vec<PathBuf> {
    let template = template.replace('/', "\\");
    let p = PathBuf::from(&template);
    if !template.contains('*') && !template.contains('?') {
        return vec![p];
    }
    let components: Vec<_> = p.components().collect();
    let mut roots = vec![PathBuf::new()];
    for component in components {
        let part = component.as_os_str().to_string_lossy();
        let mut next = vec![];
        for root in roots {
            if part.contains('*') || part.contains('?') {
                if let Ok(children) = fs::read_dir(&root) {
                    for child in children.flatten() {
                        if child.file_type().is_ok_and(|t| t.is_dir())
                            && wildcard(&part, &child.file_name().to_string_lossy())
                            && no_links(&child.path()).is_ok()
                        {
                            next.push(child.path());
                        }
                    }
                }
            } else {
                let child = root.join(component.as_os_str());
                if !child.exists() || no_links(&child).is_ok() {
                    next.push(child);
                }
            }
        }
        roots = next;
    }
    roots
}
pub fn matches_rule(path: &Path, rule: &Rule) -> bool {
    if !inside(path, &rule.root)
        || (key(path) != key(&rule.root)
            && !rule.recursive
            && path.parent().is_none_or(|p| key(p) != key(&rule.root)))
    {
        return false;
    }
    let name = path.file_name().unwrap_or_default().to_string_lossy();
    let rel = path
        .strip_prefix(&rule.root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/");
    (rule.patterns.is_empty() || rule.patterns.iter().any(|p| wildcard(p, &name)))
        && !rule.excluded.iter().any(|p| {
            wildcard(&p.replace('\\', "/"), &rel)
                || wildcard(p, &name)
                || rel.split('/').any(|s| wildcard(p, s))
        })
}
pub fn scan_rules(
    rules: &[Rule],
    ctx: &Context,
    mut exclude: impl FnMut(&Path) -> bool,
) -> (Vec<FileEntry>, Vec<Issue>) {
    let mut files = vec![];
    let mut issues = vec![];
    let mut seen = BTreeSet::new();
    let mut seen_ids = BTreeSet::new();
    let mut amount = 0u64;
    let mut checked = 0u64;
    for rule in rules {
        if ctx.cancel.cancelled() {
            break;
        }
        let mut stack = vec![rule.root.clone()];
        while let Some(dir) = stack.pop() {
            if ctx.cancel.cancelled() {
                break;
            }
            if no_links(&dir).is_err() || self_protected(&dir) {
                continue;
            }
            if dir.is_file() {
                if let Ok(info) = winfs::identity(&dir)
                    && !info.redirected()
                    && info.links == 1
                    && matches_rule(&dir, rule)
                    && !exclude(&dir)
                    && seen.insert(key(&dir))
                    && seen_ids.insert((info.volume, info.index))
                {
                    let unix = (info.modified / 10_000_000).saturating_sub(11_644_473_600);
                    if rule.days == 0
                        || unix
                            >= chrono::Utc::now().timestamp().max(0) as u64
                                - rule.days as u64 * 86400
                    {
                        let size = info.size;
                        amount += size;
                        files.push(FileEntry {
                            id: hash(
                                format!("{}:{}:{}", key(&dir), info.volume, info.index).as_bytes(),
                            ),
                            path: dir.clone(),
                            category: rule.category.clone(),
                            size,
                            allocated: winfs::allocated(&dir).unwrap_or(size),
                            identity: info,
                            scope: rule.id.clone(),
                            blocked: String::new(),
                        });
                    }
                }
                continue;
            }
            let entries = match fs::read_dir(&dir) {
                Ok(e) => e,
                Err(e) => {
                    if e.kind() != std::io::ErrorKind::NotFound && issues.len() < 100 {
                        issues.push(Issue {
                            path: dir.display().to_string(),
                            message: e.to_string(),
                        });
                    }
                    continue;
                }
            };
            for entry in entries.flatten() {
                if ctx.cancel.cancelled() {
                    break;
                }
                let path = entry.path();
                checked += 1;
                ctx.scan_progress(&path, checked, files.len() as u64, amount, false);
                let Ok(info) = winfs::identity(&path) else {
                    continue;
                };
                if info.redirected() || self_protected(&path) {
                    continue;
                }
                if info.directory() {
                    if rule.recursive
                        && !rule
                            .excluded
                            .iter()
                            .any(|p| wildcard(p, &entry.file_name().to_string_lossy()))
                    {
                        stack.push(path);
                    }
                    continue;
                }
                if !matches_rule(&path, rule) || exclude(&path) || info.links > 1 {
                    continue;
                }
                let seconds = info.modified / 10_000_000;
                let unix = seconds.saturating_sub(11_644_473_600);
                if rule.days > 0
                    && unix
                        < chrono::Utc::now().timestamp().max(0) as u64 - rule.days as u64 * 86400
                {
                    continue;
                }
                if !seen.insert(key(&path)) || !seen_ids.insert((info.volume, info.index)) {
                    continue;
                }
                let id = hash(format!("{}:{}:{}", key(&path), info.volume, info.index).as_bytes());
                let size = info.size;
                amount += size;
                files.push(FileEntry {
                    id,
                    path: path.clone(),
                    category: rule.category.clone(),
                    size,
                    allocated: winfs::allocated(&path).unwrap_or(size),
                    identity: info,
                    scope: rule.id.clone(),
                    blocked: String::new(),
                });
            }
        }
    }
    if let Some(rule) = rules.last() {
        ctx.scan_progress(&rule.root, checked, files.len() as u64, amount, true);
    }
    (files, issues)
}
pub fn validate_file(entry: &FileEntry) -> Result<()> {
    no_links(&entry.path)?;
    if self_protected(&entry.path) {
        bail!("The running application is protected");
    }
    let current = winfs::identity(&entry.path)?;
    if current != entry.identity
        || current.directory()
        || current.redirected()
        || current.links != 1
    {
        bail!("File changed after scanning; scan again");
    }
    Ok(())
}
pub fn delete_entries(entries: &[FileEntry], rules: &[Rule], ctx: &Context) -> OperationResult {
    let mut result = OperationResult::default();
    for (index, entry) in entries.iter().enumerate() {
        if ctx.cancel.cancelled() {
            break;
        }
        let attempt = (|| -> Result<()> {
            validate_file(entry)?;
            let rule = rules
                .iter()
                .find(|r| r.id == entry.scope)
                .context("Scan authorization expired")?;
            if !matches_rule(&entry.path, rule) {
                bail!("File is outside the scanned scope");
            }
            if let Some(old) = &rule.root_identity {
                let now = winfs::identity(&rule.root)?;
                if old.volume != now.volume || old.index != now.index {
                    bail!("Scan root changed");
                }
            }
            if !entry.blocked.is_empty() {
                bail!("{}", entry.blocked);
            }
            fs::remove_file(&entry.path)?;
            Ok(())
        })();
        match attempt {
            Ok(()) => {
                result.count += 1;
                result.bytes += entry.size;
                result.succeeded.push(entry.id.clone());
            }
            Err(e) => result.errors.push(Issue {
                path: entry.path.display().to_string(),
                message: e.to_string(),
            }),
        }
        ctx.set(
            "delete",
            entry.path.display().to_string(),
            index as u64 + 1,
            Some(entries.len() as u64),
            result.bytes,
        );
    }
    result.cancelled = ctx.cancel.cancelled();
    result
}

pub fn digest_file(path: &Path, ctx: &Context) -> Result<String> {
    let mut file = fs::File::open(path)?;
    let total = file.metadata()?.len();
    let mut done = 0;
    let mut hash = Sha256::new();
    let mut buffer = vec![0; 1024 * 1024];
    loop {
        ctx.cancel.check()?;
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
        done += n as u64;
        ctx.set(
            "verify",
            path.display().to_string(),
            done,
            Some(total),
            done,
        );
    }
    Ok(format!("{:x}", hash.finalize()))
}
pub fn copy_verified(
    source: &Path,
    target: &Path,
    ctx: &Context,
    mut progress: impl FnMut(u64),
) -> Result<u64> {
    if target.exists() {
        bail!("Destination already exists");
    }
    no_links(source)?;
    let old = winfs::identity(source)?;
    if old.redirected() || old.directory() || old.links != 1 {
        bail!("Linked files are protected");
    }
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)?;
        no_links(parent)?;
    }
    let mut owned = None;
    let result = (|| {
        let mut input = fs::File::open(source)?;
        let mut output = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(target)?;
        owned = Some(winfs::identity(target)?);
        let mut buffer = vec![0u8; 1024 * 1024];
        let mut hash = Sha256::new();
        let mut written = 0;
        loop {
            ctx.cancel.check()?;
            let n = input.read(&mut buffer)?;
            if n == 0 {
                break;
            }
            output.write_all(&buffer[..n])?;
            hash.update(&buffer[..n]);
            written += n as u64;
            progress(written);
        }
        output.sync_all()?;
        drop(output);
        let expected = format!("{:x}", hash.finalize());
        if digest_file(target, ctx)? != expected || winfs::identity(source)? != old {
            bail!("Content verification failed; original retained");
        }
        Ok(written)
    })();
    if result.is_err()
        && owned.as_ref().is_some_and(|old| {
            winfs::identity(target)
                .is_ok_and(|now| now.volume == old.volume && now.index == old.index)
        })
    {
        let _ = fs::remove_file(target);
    }
    result
}
