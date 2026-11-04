//! Logical-size drive cleanup; deletion still uses live authorization in common.
use super::*;
use std::os::windows::fs::MetadataExt;
use std::{collections::BTreeMap, sync::Arc};
use winfs::directory::Entry;
const REPARSE_POINT: u32 = 0x400;
const DIRECTORY: u32 = 0x10;
const WOF_TAG: u32 = 0x8000_0017;

fn compile(pattern: &str) -> Option<globset::GlobMatcher> {
    globset::GlobBuilder::new(pattern)
        .case_insensitive(true)
        .literal_separator(false)
        .backslash_escape(false)
        .build()
        .ok()
        .map(|g| g.compile_matcher())
}
struct Matcher {
    all: bool,
    patterns: Vec<globset::GlobMatcher>,
    excluded: Vec<(Option<globset::GlobMatcher>, Option<globset::GlobMatcher>)>,
}
impl Matcher {
    fn new(rule: &Rule) -> Self {
        Self {
            all: rule.patterns.is_empty(),
            patterns: rule.patterns.iter().filter_map(|p| compile(p)).collect(),
            excluded: rule
                .excluded
                .iter()
                .map(|p| (compile(p), compile(&p.replace('\\', "/"))))
                .collect(),
        }
    }
    fn excluded_name(&self, name: &str) -> bool {
        self.excluded
            .iter()
            .any(|(p, _)| p.as_ref().is_some_and(|p| p.is_match(name)))
    }
    fn matches(&self, path: &Path, rule: &Rule) -> bool {
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
        (self.all || self.patterns.iter().any(|p| p.is_match(name.as_ref())))
            && !self.excluded_name(&name)
            && !self.excluded.iter().any(|(p, normalized)| {
                normalized.as_ref().is_some_and(|p| p.is_match(&rel))
                    || p.as_ref()
                        .is_some_and(|p| rel.split('/').any(|s| p.is_match(s)))
            })
    }
}

fn entries(dir: &Path, ctx: &Context) -> std::io::Result<Vec<Entry>> {
    match winfs::directory::read(dir, &|| ctx.cancel.cancelled()) {
        Err(e) if e.kind() == std::io::ErrorKind::Unsupported => standard_entries(dir, ctx),
        result => result,
    }
}

fn standard_entries(dir: &Path, ctx: &Context) -> std::io::Result<Vec<Entry>> {
    let mut entries = Vec::new();
    for row in fs::read_dir(dir)? {
        if ctx.cancel.cancelled() {
            return Err(std::io::ErrorKind::Interrupted.into());
        }
        let Ok(row) = row else {
            continue;
        };
        let Ok(metadata) = fs::symlink_metadata(row.path()) else {
            continue;
        };
        entries.push(Entry {
            name: row.file_name(),
            size: metadata.file_size(),
            allocation: 0,
            modified: metadata.last_write_time(),
            attributes: metadata.file_attributes(),
            tag: if metadata.file_attributes() & REPARSE_POINT != 0 {
                winfs::identity(&row.path()).map(|i| i.tag).unwrap_or(0)
            } else {
                0
            },
            id: [0; 16],
        });
    }
    Ok(entries)
}

pub fn scan(rules: &[Rule], ctx: &Context) -> (Vec<FileEntry>, Vec<Issue>) {
    scan_with(rules, ctx, entries)
}
fn file_entry(path: PathBuf, rule: &Rule, info: winfs::Identity) -> FileEntry {
    FileEntry {
        id: hash(format!("{}:{}:{}", key(&path), info.volume, info.index).as_bytes()),
        // Drive cleanup reports logical bytes, including sparse and compressed files.
        allocated: info.size,
        size: info.size,
        path,
        category: rule.category.clone(),
        identity: info,
        scope: rule.id.clone(),
        blocked: String::new(),
    }
}
fn scan_with(
    rules: &[Rule],
    ctx: &Context,
    mut read: impl FnMut(&Path, &Context) -> std::io::Result<Vec<Entry>>,
) -> (Vec<FileEntry>, Vec<Issue>) {
    let mut cache: BTreeMap<String, Arc<Vec<Entry>>> = BTreeMap::new();
    let mut files = Vec::new();
    let mut issues = Vec::new();
    let mut seen = BTreeSet::new();
    let mut seen_ids = BTreeSet::new();
    let mut checked = 0;
    let mut amount = 0;
    let now = chrono::Utc::now().timestamp().max(0) as u64;
    for rule in rules {
        let matcher = Matcher::new(rule);
        let recent = |modified: u64| {
            rule.days == 0
                || (modified / 10_000_000).saturating_sub(11_644_473_600)
                    >= now.saturating_sub(rule.days as u64 * 86400)
        };
        let mut pending = vec![rule.root.clone()];
        while let Some(dir) = pending.pop() {
            if ctx.cancel.cancelled() {
                break;
            }
            if no_links(&dir).is_err() || self_protected(&dir) {
                continue;
            }
            if dir.is_file() {
                if let Ok(info) = winfs::identity(&dir)
                    && !info.directory()
                    && !info.redirected()
                    && info.links == 1
                    && matcher.matches(&dir, rule)
                    && recent(info.modified)
                    && seen.insert(key(&dir))
                    && seen_ids.insert((info.volume, info.index))
                {
                    amount += info.size;
                    files.push(file_entry(dir, rule, info));
                }
                continue;
            }
            let directory_key = key(&dir);
            let rows = match cache.get(&directory_key) {
                Some(rows) => rows.clone(),
                None => match read(&dir, ctx) {
                    Ok(rows) => {
                        let rows = Arc::new(rows);
                        cache.insert(directory_key, rows.clone());
                        rows
                    }
                    Err(e) => {
                        if !ctx.cancel.cancelled()
                            && e.kind() != std::io::ErrorKind::NotFound
                            && issues.len() < 100
                        {
                            issues.push(Issue {
                                path: dir.display().to_string(),
                                message: e.to_string(),
                            });
                        }
                        continue;
                    }
                },
            };
            for entry in rows.iter() {
                if ctx.cancel.cancelled() {
                    break;
                }
                let path = dir.join(&entry.name);
                checked += 1;
                ctx.scan_progress(&path, checked, files.len() as u64, amount, false);
                if (entry.attributes & REPARSE_POINT != 0
                    && (entry.attributes & DIRECTORY != 0 || entry.tag != WOF_TAG))
                    || self_protected(&path)
                {
                    continue;
                }
                if entry.attributes & DIRECTORY != 0 {
                    if rule.recursive && !matcher.excluded_name(&entry.name.to_string_lossy()) {
                        pending.push(path);
                    }
                    continue;
                }
                if !matcher.matches(&path, rule)
                    || !recent(entry.modified)
                    || seen.contains(&key(&path))
                {
                    continue;
                }
                let Ok(info) = winfs::identity(&path) else {
                    continue;
                };
                if info.directory()
                    || info.redirected()
                    || info.links != 1
                    || !recent(info.modified)
                    || !seen_ids.insert((info.volume, info.index))
                {
                    continue;
                }
                seen.insert(key(&path));
                amount += info.size;
                files.push(file_entry(path, rule, info));
            }
        }
        if ctx.cancel.cancelled() {
            break;
        }
    }
    if let Some(rule) = rules.last() {
        ctx.scan_progress(&rule.root, checked, files.len() as u64, amount, true);
    }
    (files, issues)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn directory_and_file_roots_use_logical_size_and_retain_live_validation() {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::{
            Foundation::HANDLE,
            System::{IO::DeviceIoControl, Ioctl::FSCTL_SET_SPARSE},
        };
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("sparse.tmp");
        let file = fs::File::create(&path).unwrap();
        let mut returned = 0;
        assert_ne!(
            unsafe {
                DeviceIoControl(
                    file.as_raw_handle() as HANDLE,
                    FSCTL_SET_SPARSE,
                    std::ptr::null_mut(),
                    0,
                    std::ptr::null_mut(),
                    0,
                    &mut returned,
                    std::ptr::null_mut(),
                )
            },
            0
        );
        let logical = 8 * 1024 * 1024;
        file.set_len(logical).unwrap();
        drop(file);
        assert!(winfs::allocated(&path).unwrap() < logical);
        for root in [temp.path(), path.as_path()] {
            let rule = Rule {
                id: "fixture".into(),
                category: "temporary".into(),
                root: root.into(),
                recursive: true,
                patterns: vec!["*.tmp".into()],
                excluded: Vec::new(),
                days: 0,
                root_identity: winfs::identity(root).ok(),
                chat_app: None,
            };
            for batch in [true, false] {
                let (files, errors) = scan_with(
                    std::slice::from_ref(&rule),
                    &Context::new(crate::Feature::Drive),
                    |dir, ctx| {
                        if batch {
                            entries(dir, ctx)
                        } else {
                            standard_entries(dir, ctx)
                        }
                    },
                );
                assert!(errors.is_empty());
                assert_eq!(files.len(), 1);
                assert_eq!((files[0].size, files[0].allocated), (logical, logical));
                validate_file(&files[0]).unwrap();
            }
        }
        let rule = Rule {
            id: "fixture".into(),
            category: "temporary".into(),
            root: path.clone(),
            recursive: false,
            patterns: Vec::new(),
            excluded: Vec::new(),
            days: 0,
            root_identity: winfs::identity(&path).ok(),
            chat_app: None,
        };
        let (files, _) = scan(&[rule], &Context::new(crate::Feature::Drive));
        fs::write(&path, b"changed").unwrap();
        assert!(validate_file(&files[0]).is_err());
    }
    fn normalized(files: &[FileEntry]) -> Vec<(String, String, String, u64, u64)> {
        let mut rows: Vec<_> = files
            .iter()
            .map(|f| {
                (
                    key(&f.path),
                    f.category.clone(),
                    f.scope.clone(),
                    f.size,
                    f.allocated,
                )
            })
            .collect();
        rows.sort();
        rows
    }
    #[test]
    fn batch_and_fallback_preserve_rules_priority_hardlinks_and_cancellation() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path();
        fs::create_dir(root.join("nested")).unwrap();
        fs::create_dir(root.join("excluded")).unwrap();
        for name in [
            "a.tmp",
            "b.log",
            "c.txt",
            "nested/d.tmp",
            "excluded/e.tmp",
            "linked.tmp",
        ] {
            fs::write(root.join(name), name.as_bytes()).unwrap();
        }
        fs::hard_link(root.join("linked.tmp"), root.join("copy.tmp")).unwrap();
        let mut rule = Rule {
            id: "first".into(),
            category: "temporary".into(),
            root: root.into(),
            recursive: true,
            patterns: vec!["*.tmp".into()],
            excluded: vec!["excluded".into()],
            days: 0,
            root_identity: winfs::identity(root).ok(),
            chat_app: None,
        };
        let mut rules = vec![rule.clone()];
        rule.id = "second".into();
        rule.category = "logs".into();
        rule.patterns = vec!["*.log".into()];
        rules.push(rule.clone());
        rule.id = "overlapping".into();
        rule.root = root.join("nested");
        rule.patterns.clear();
        rules.push(rule.clone());
        rule.id = "nonrecursive".into();
        rule.root = root.into();
        rule.recursive = false;
        rule.patterns.clear();
        rules.push(rule);
        let (old, errors) = scan_rules(&rules, &Context::new(crate::Feature::Drive), |_| false);
        assert!(errors.is_empty());
        assert_eq!(old.len(), 4);
        for batch in [true, false] {
            let mut reads = BTreeSet::new();
            let (new, errors) =
                scan_with(&rules, &Context::new(crate::Feature::Drive), |dir, ctx| {
                    assert!(reads.insert(key(dir)), "directory read twice");
                    if batch {
                        entries(dir, ctx)
                    } else {
                        standard_entries(dir, ctx)
                    }
                });
            assert!(errors.is_empty(), "{errors:?}");
            assert_eq!(normalized(&new), normalized(&old));
            assert_eq!(reads.len(), 2);
        }
        let ctx = Context::new(crate::Feature::Drive);
        ctx.cancel.cancel();
        assert!(
            scan_with(&rules, &ctx, |_, _| panic!("read after cancellation"))
                .0
                .is_empty()
        );
        rules[0].days = 1;
        assert_eq!(
            scan(&rules[..1], &Context::new(crate::Feature::Drive))
                .0
                .len(),
            2
        );
        assert!(winfs::directory::read(root, &|| true).is_err());
    }
}

#[cfg(test)]
mod matcher_tests {
    use super::*;
    #[test]
    fn compiled_patterns_preserve_legacy_matching() {
        let mut rule = Rule {
            id: "rule".into(),
            category: "category".into(),
            root: r"C:\scan".into(),
            recursive: true,
            patterns: Vec::new(),
            excluded: Vec::new(),
            days: 0,
            root_identity: None,
            chat_app: None,
        };
        for patterns in [
            vec![],
            vec!["*.TMP"],
            vec!["[invalid"],
            vec!["*.tmp", "*.log"],
        ] {
            rule.patterns = patterns.into_iter().map(String::from).collect();
            for excluded in [
                vec![],
                vec!["skip"],
                vec!["nested\\*.tmp"],
                vec!["*.log", "[invalid"],
            ] {
                rule.excluded = excluded.into_iter().map(String::from).collect();
                for recursive in [true, false] {
                    rule.recursive = recursive;
                    let matcher = Matcher::new(&rule);
                    for path in [
                        r"C:\scan\A.tmp",
                        r"C:\scan\文件.log",
                        r"C:\scan\nested\a.tmp",
                        r"C:\scan\skip\a.tmp",
                        r"C:\outside\a.tmp",
                    ] {
                        assert_eq!(
                            matcher.matches(Path::new(path), &rule),
                            matches_rule(Path::new(path), &rule),
                            "{path} {rule:?}"
                        );
                    }
                }
            }
        }
    }
}
