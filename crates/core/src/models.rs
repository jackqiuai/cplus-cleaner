use cplus_platform_windows::{filesystem::Identity, registry::InstalledRecord, shell::Icon};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, path::PathBuf};

#[derive(
    Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum Feature {
    #[default]
    Home,
    Drive,
    Chat,
    LargeFiles,
    AppMover,
    Compression,
    Duplicates,
    Privacy,
    Uninstall,
    Popup,
    System,
}
impl Feature {
    pub const ALL: [Self; 11] = [
        Self::Home,
        Self::Drive,
        Self::Chat,
        Self::LargeFiles,
        Self::AppMover,
        Self::Compression,
        Self::Duplicates,
        Self::Privacy,
        Self::Uninstall,
        Self::Popup,
        Self::System,
    ];
    pub const HOME: [Self; 8] = [
        Self::Drive,
        Self::Chat,
        Self::LargeFiles,
        Self::Privacy,
        Self::AppMover,
        Self::Compression,
        Self::Uninstall,
        Self::Duplicates,
    ];
    pub fn key(self) -> &'static str {
        match self {
            Self::Home => "home",
            Self::Drive => "drive",
            Self::Chat => "chat",
            Self::LargeFiles => "large_files",
            Self::AppMover => "app_mover",
            Self::Compression => "compression",
            Self::Duplicates => "duplicates",
            Self::Privacy => "privacy",
            Self::Uninstall => "uninstall",
            Self::Popup => "popup",
            Self::System => "system",
        }
    }
    pub fn byte_metric(self) -> bool {
        !matches!(
            self,
            Self::Privacy | Self::Popup | Self::Uninstall | Self::Home | Self::System
        )
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Rule {
    pub id: String,
    pub category: String,
    pub root: PathBuf,
    pub recursive: bool,
    pub patterns: Vec<String>,
    pub excluded: Vec<String>,
    pub days: u32,
    pub root_identity: Option<Identity>,
    pub chat_app: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FileEntry {
    pub id: String,
    pub path: PathBuf,
    pub category: String,
    pub size: u64,
    pub allocated: u64,
    pub identity: Identity,
    pub scope: String,
    #[serde(default)]
    pub blocked: String,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Category {
    pub id: String,
    pub name: String,
    pub group: String,
    pub icon: String,
    pub bytes: u64,
    pub count: u64,
    pub default_selected: bool,
    pub view_only: bool,
    pub app: String,
    pub status: String,
    pub file_ids: Vec<String>,
    pub record_ids: Vec<String>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct AppInfo {
    pub id: String,
    pub name: String,
    pub version: String,
    pub publisher: String,
    pub path: PathBuf,
    pub modified: u64,
    pub installed: String,
    pub logical: u64,
    pub physical: u64,
    pub files: u64,
    pub complete: bool,
    pub missing: u64,
    pub unreadable: u64,
    pub blocked: String,
    pub compressed: bool,
    pub root_identity: Option<Identity>,
    pub record: InstalledRecord,
    pub icon: Option<Icon>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct SystemItem {
    pub id: String,
    pub bytes: Option<u64>,
    pub enabled: bool,
    pub message: String,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct PrivacyRecord {
    pub id: String,
    pub category: String,
    pub kind: String,
    pub source: PathBuf,
    pub table: String,
    pub key: String,
    pub digest: String,
    pub raw: Vec<u8>,
    pub value_type: u32,
    pub registry_value: String,
    pub identity: Option<Identity>,
    pub members: Vec<String>,
    pub browser: String,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct RegistryResidue {
    pub id: String,
    pub hive: String,
    pub view: u32,
    pub path: String,
    pub digest: String,
    pub values: Vec<(String, u32, Vec<u8>)>,
    pub blocked: bool,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct ScanResult {
    pub feature: Feature,
    pub token: String,
    pub categories: Vec<Category>,
    pub files: Vec<FileEntry>,
    pub apps: Vec<AppInfo>,
    pub rules: Vec<Rule>,
    pub privacy: Vec<PrivacyRecord>,
    pub popup: Vec<crate::features::popup_blocker::Group>,
    pub system: Vec<SystemItem>,
    pub diagnostics: Vec<Issue>,
    pub cancelled: bool,
    pub elapsed_ms: u64,
    #[serde(default)]
    pub timings_ms: BTreeMap<String, u64>,
    pub counts: BTreeMap<String, u64>,
    #[serde(default)]
    pub large_scan: Option<cplus_platform_windows::filesystem::large_scan::Summary>,
    #[serde(default)]
    pub duplicate_scan: Option<crate::features::duplicates::Summary>,
    #[serde(default)]
    pub registry_residues: Vec<RegistryResidue>,
    #[serde(default)]
    pub residue_owners: Vec<InstalledRecord>,
}
impl ScanResult {
    pub fn allocated_bytes(&self) -> u64 {
        let mut seen = std::collections::BTreeSet::new();
        self.files
            .iter()
            .filter(|file| seen.insert((file.identity.volume, file.identity.index)))
            .map(|file| file.allocated)
            .sum()
    }
    pub fn reclaimable_bytes(&self) -> u64 {
        if self.feature == Feature::Duplicates {
            return self.categories.iter().map(|g| g.bytes).sum();
        }
        self.files
            .iter()
            .filter(|file| file.blocked.is_empty() && file.identity.links == 1)
            .map(|file| file.allocated)
            .sum()
    }
    pub fn bytes(&self) -> u64 {
        if self.feature == Feature::Duplicates {
            self.categories.iter().map(|c| c.bytes).sum()
        } else if self.feature == Feature::Drive {
            self.files.iter().map(|f| f.size).sum::<u64>()
                + self
                    .categories
                    .iter()
                    .filter(|c| {
                        matches!(
                            c.id.as_str(),
                            "drive:windows_delivery"
                                | "drive:windows_updates"
                                | "drive:windows_old"
                        )
                    })
                    .map(|c| c.bytes)
                    .sum::<u64>()
        } else if !self.apps.is_empty() {
            self.apps
                .iter()
                .map(|a| {
                    if self.feature == Feature::Compression {
                        a.physical
                    } else {
                        a.logical
                    }
                })
                .sum()
        } else {
            self.files.iter().map(|f| f.size).sum()
        }
    }
    pub fn count(&self) -> u64 {
        match self.feature {
            Feature::Drive => {
                self.files.len() as u64
                    + self
                        .categories
                        .iter()
                        .filter(|c| {
                            matches!(
                                c.id.as_str(),
                                "drive:windows_delivery"
                                    | "drive:windows_updates"
                                    | "drive:windows_old"
                            )
                        })
                        .map(|c| c.count)
                        .sum::<u64>()
            }
            Feature::Privacy => self.categories.iter().map(|c| c.count).sum(),
            Feature::Popup => self.popup.iter().map(|g| g.rules.len() as u64).sum(),
            Feature::Uninstall | Feature::AppMover | Feature::Compression => self.apps.len() as u64,
            _ => self.files.len() as u64,
        }
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Issue {
    pub path: String,
    pub message: String,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct OperationResult {
    pub system_sizes: BTreeMap<String, u64>,
    pub count: u64,
    pub bytes: u64,
    pub errors: Vec<Issue>,
    pub cancelled: bool,
    pub succeeded: Vec<String>,
    pub message: String,
}
impl OperationResult {
    pub fn ok(&self) -> bool {
        !self.cancelled && self.errors.is_empty()
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Activity {
    pub id: String,
    pub time: String,
    pub action: String,
    pub ok: bool,
    pub summary: String,
    pub count: u64,
    pub bytes: u64,
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}
