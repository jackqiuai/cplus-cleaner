//! Exact-content duplicate discovery; samples never authorize deletion.
mod deletion;
mod hashing;
mod scan;

use crate::*;
pub use deletion::delete;
pub use scan::{scan_locations, scan_locations_with_mode};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

pub const DEFAULT_MINIMUM: u64 = 200 * 1024;
pub const PAGE_SIZE: usize = 40;
pub const ENTRY_PAGE_SIZE: usize = 80;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct Location {
    pub path: PathBuf,
    pub protected: bool,
}
impl Location {
    pub fn cleanable(path: impl Into<PathBuf>) -> Self {
        Self {
            path: path.into(),
            protected: false,
        }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub enum KeeperRule {
    #[default]
    ShortestPath,
    ShortestName,
    Oldest,
    Newest,
}
impl KeeperRule {
    pub const ALL: [Self; 4] = [
        Self::ShortestPath,
        Self::ShortestName,
        Self::Oldest,
        Self::Newest,
    ];
    pub fn label(self) -> &'static str {
        match self {
            Self::ShortestPath => "Shortest path",
            Self::ShortestName => "Shortest filename",
            Self::Oldest => "Oldest modified",
            Self::Newest => "Newest modified",
        }
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Summary {
    pub locations: Vec<Location>,
    pub minimum: u64,
    pub checked: u64,
    pub candidates: u64,
    pub skipped: u64,
    pub unreadable: u64,
    pub changed: u64,
    pub hard_links: u64,
    pub sampled: u64,
    pub hashed: u64,
    pub sample_bytes: u64,
    pub hash_bytes: u64,
    pub workers: usize,
    pub strategies: Vec<String>,
    pub fallbacks: Vec<String>,
    pub estimated_files: BTreeSet<String>,
    pub keeper_rule: KeeperRule,
    pub keepers: BTreeMap<String, String>,
    pub retained: BTreeSet<String>,
}

/// The original one-folder API retains its all-file semantics for existing callers.
pub fn scan(root: &Path, ctx: &jobs::Context) -> anyhow::Result<ScanResult> {
    scan_locations(&[Location::cleanable(root)], 0, ctx)
}

pub fn keeper_id<'a>(report: &'a ScanResult, group: &'a Category) -> Option<&'a str> {
    report
        .duplicate_scan
        .as_ref()
        .and_then(|s| s.keepers.get(&group.id))
        .map(String::as_str)
        .or_else(|| group.file_ids.first().map(String::as_str))
}
pub fn is_retained(report: &ScanResult, id: &str) -> bool {
    if let Some(summary) = &report.duplicate_scan {
        return summary.retained.contains(id);
    }
    report
        .categories
        .iter()
        .any(|g| keeper_id(report, g) == Some(id))
}
pub fn suggested_ids(report: &ScanResult, group: Option<&str>) -> BTreeSet<String> {
    if report.cancelled || report.token.is_empty() {
        return BTreeSet::new();
    }
    report
        .files
        .iter()
        .filter(|f| {
            f.blocked.is_empty()
                && group.is_none_or(|g| f.category == g)
                && !is_retained(report, &f.id)
        })
        .map(|f| f.id.clone())
        .collect()
}

pub fn set_keeper_rule(report: &mut ScanResult, rule: KeeperRule) {
    if let Some(summary) = report.duplicate_scan.as_mut() {
        summary.keeper_rule = rule;
    }
    refresh_categories(report);
}

/// Recompute space from the actual keeper and allocated sizes, including protected copies.
pub fn refresh_categories(report: &mut ScanResult) {
    let rule = report
        .duplicate_scan
        .as_ref()
        .map(|s| s.keeper_rule)
        .unwrap_or_default();
    let mut keepers = BTreeMap::new();
    let mut by_group = BTreeMap::<&str, Vec<&FileEntry>>::new();
    for file in &report.files {
        by_group.entry(&file.category).or_default().push(file);
    }
    for group in &mut report.categories {
        let files = by_group
            .get(group.id.as_str())
            .map(Vec::as_slice)
            .unwrap_or_default();
        group.file_ids = files.iter().map(|f| f.id.clone()).collect();
        group.count = files.len() as u64;
        let keeper = files.iter().copied().min_by(|a, b| {
            // Protection supplies a keeper, so every cleanable copy may be selected.
            a.blocked
                .is_empty()
                .cmp(&b.blocked.is_empty())
                .then_with(|| compare(a, b, rule))
        });
        group.bytes = files
            .iter()
            .filter(|f| f.blocked.is_empty() && keeper.is_none_or(|k| f.id != k.id))
            .fold(0_u64, |n, f| n.saturating_add(f.allocated));
        if let Some(keeper) = keeper {
            keepers.insert(group.id.clone(), keeper.id.clone());
        }
    }
    report.categories.retain(|g| g.count > 1);
    report
        .categories
        .sort_by(|a, b| b.bytes.cmp(&a.bytes).then_with(|| a.id.cmp(&b.id)));
    let groups: BTreeSet<_> = report.categories.iter().map(|g| g.id.as_str()).collect();
    report
        .files
        .retain(|f| groups.contains(f.category.as_str()));
    let remaining: BTreeSet<_> = report.files.iter().map(|f| f.id.as_str()).collect();
    if let Some(summary) = report.duplicate_scan.as_mut() {
        keepers.retain(|g, _| groups.contains(g.as_str()));
        summary.retained = keepers.values().cloned().collect();
        summary.keepers = keepers;
        summary
            .estimated_files
            .retain(|id| remaining.contains(id.as_str()));
    }
}

fn compare(a: &FileEntry, b: &FileEntry, rule: KeeperRule) -> std::cmp::Ordering {
    let path_length = |f: &FileEntry| f.path.as_os_str().len();
    let name_length = |f: &FileEntry| f.path.file_name().map(|s| s.len()).unwrap_or(0);
    match rule {
        KeeperRule::ShortestPath => path_length(a).cmp(&path_length(b)),
        KeeperRule::ShortestName => name_length(a).cmp(&name_length(b)),
        KeeperRule::Oldest => a.identity.modified.cmp(&b.identity.modified),
        KeeperRule::Newest => b.identity.modified.cmp(&a.identity.modified),
    }
    .then_with(|| path_length(a).cmp(&path_length(b)))
    .then_with(|| a.identity.modified.cmp(&b.identity.modified))
    .then_with(|| common::key(&a.path).cmp(&common::key(&b.path)))
}
