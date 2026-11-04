use crate::{Activity, common};
use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use std::{
    fs,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub autostart: bool,
    pub scheduled_cleanup: bool,
    pub cleanup_time: String,
    pub close_to_tray: bool,
    pub last_cleanup_date: String,
    pub language: String,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            autostart: false,
            scheduled_cleanup: false,
            cleanup_time: "20:00".into(),
            close_to_tray: false,
            last_cleanup_date: String::new(),
            language: String::new(),
        }
    }
}
impl Settings {
    pub fn validate(&self) -> Result<()> {
        if self.cleanup_time.len() != 5 || self.cleanup_time.as_bytes().get(2) != Some(&b':') {
            bail!("Invalid cleanup time; use HH:MM");
        }
        if !["", "zh", "zh-TW", "en"].contains(&self.language.as_str()) {
            bail!("Invalid language");
        }
        chrono::NaiveTime::parse_from_str(&self.cleanup_time, "%H:%M")
            .context("Invalid cleanup time")?;
        Ok(())
    }
}
#[derive(Clone, Debug)]
pub struct Store {
    pub root: PathBuf,
    lock: Arc<Mutex<()>>,
    pub isolated: bool,
}
impl Store {
    pub fn new(path: Option<PathBuf>, isolated: bool) -> Result<Self> {
        let canonical = PathBuf::from(
            std::env::var_os("LOCALAPPDATA")
                .unwrap_or_else(|| std::env::temp_dir().into_os_string()),
        )
        .join("CPLUSCLEANER");
        let root = path.unwrap_or_else(|| {
            if isolated {
                canonical.join("Rust-Preview")
            } else {
                canonical
            }
        });
        fs::create_dir_all(&root)?;
        Ok(Self {
            root,
            lock: Arc::new(Mutex::new(())),
            isolated,
        })
    }
    pub fn read<T: DeserializeOwned>(&self, name: &str) -> Result<T> {
        Ok(serde_json::from_slice(&fs::read(self.path(name)?)?)?)
    }
    pub fn path(&self, name: &str) -> Result<PathBuf> {
        let relative = Path::new(name);
        if relative.is_absolute()
            || relative.components().any(|c| {
                matches!(
                    c,
                    std::path::Component::ParentDir | std::path::Component::Prefix(_)
                )
            })
        {
            bail!("Invalid storage key");
        }
        Ok(self.root.join(relative))
    }
    pub fn write<T: Serialize + ?Sized>(&self, name: &str, value: &T) -> Result<()> {
        let _lock = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        atomic_write(&self.path(name)?, &serde_json::to_vec_pretty(value)?)
    }
    pub fn settings(&self) -> Settings {
        self.read::<Settings>("settings.json")
            .ok()
            .filter(|s| s.validate().is_ok())
            .unwrap_or_default()
    }
    pub fn save_settings(&self, settings: &Settings) -> Result<()> {
        settings.validate()?;
        self.write("settings.json", settings)
    }
    pub fn activities(&self) -> Vec<Activity> {
        let migrated = self.root.join("rust-activity-index.json").exists();
        let sources = if migrated {
            vec!["activity.jsonl"]
        } else {
            vec!["operations.jsonl", "system-actions.jsonl", "activity.jsonl"]
        };
        let mut rows = std::collections::BTreeMap::new();
        for source in sources {
            if let Ok(file) = fs::File::open(self.root.join(source)) {
                for line in BufReader::new(file).lines().map_while(Result::ok) {
                    let Ok(mut value) = serde_json::from_str::<serde_json::Value>(&line) else {
                        continue;
                    };
                    if let Some(time) = value["time"]
                        .as_i64()
                        .or_else(|| value["time"].as_f64().map(|n| n as i64))
                    {
                        value["time"] = chrono::DateTime::from_timestamp(time, 0)
                            .map(|d| {
                                d.with_timezone(&chrono::Local)
                                    .format("%Y-%m-%d %H:%M:%S")
                                    .to_string()
                            })
                            .unwrap_or_default()
                            .into();
                    }
                    if value["id"].as_str().is_none_or(str::is_empty) {
                        value["id"] = common::hash(line.as_bytes()).into();
                    }
                    if let Ok(row) = serde_json::from_value::<Activity>(value) {
                        rows.insert(row.id.clone(), row);
                    }
                }
            }
        }
        let mut rows = rows.into_values().collect::<Vec<_>>();
        rows.sort_by(|a, b| a.time.cmp(&b.time));
        rows
    }
    pub fn append(
        &self,
        action: &str,
        summary: &str,
        count: u64,
        bytes: u64,
        ok: bool,
    ) -> Result<()> {
        let _lock = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        let row = Activity {
            id: common::token(),
            time: chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),
            action: action.into(),
            ok,
            summary: summary.into(),
            count,
            bytes,
            ..Default::default()
        };
        let mut file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.root.join("activity.jsonl"))?;
        file.write_all(&serde_json::to_vec(&row)?)?;
        file.write_all(b"\n")?;
        file.sync_all()?;
        Ok(())
    }
    pub fn replace_activities(&self, rows: &[Activity]) -> Result<()> {
        let _lock = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        let mut bytes = vec![];
        for row in rows {
            bytes.extend(serde_json::to_vec(row)?);
            bytes.push(b'\n');
        }
        atomic_write(&self.root.join("activity.jsonl"), &bytes)?;
        atomic_write(
            &self.root.join("rust-activity-index.json"),
            b"{\"version\":1}",
        )
    }
    pub fn default_destination(&self, app: bool) -> PathBuf {
        let file = if app {
            "software-migration-settings.json"
        } else {
            "bigfiles-settings.json"
        };
        if let Ok(value) = self.read::<serde_json::Value>(file)
            && value["custom"].as_bool() == Some(true)
            && let Some(s) = value["destination"].as_str()
        {
            return s.into();
        }
        cplus_platform_windows::filesystem::disks()
            .into_iter()
            .filter(|d| {
                d.root.chars().next().is_some_and(|c| c >= 'D')
                    && (!app || d.filesystem.eq_ignore_ascii_case("NTFS"))
                    && !d
                        .root
                        .eq_ignore_ascii_case(&common::system_drive().to_string_lossy())
            })
            .map(|d| PathBuf::from(d.root).join(if app { "Program Files" } else { "bigfiles" }))
            .next()
            .unwrap_or_default()
    }
    pub fn save_destination(&self, app: bool, value: &Path) -> Result<()> {
        self.write(
            if app {
                "software-migration-settings.json"
            } else {
                "bigfiles-settings.json"
            },
            &serde_json::json!({"custom":true,"destination":value}),
        )
    }
    pub fn backup_legacy(&self) -> Result<()> {
        if self.isolated || self.root.join("rust-migration.json").exists() {
            return Ok(());
        }
        let backup = self.root.join("rust-migration-backup");
        fs::create_dir_all(&backup)?;
        for item in fs::read_dir(&self.root)? {
            let item = item?;
            if item.file_type()?.is_file() {
                let name = item.file_name();
                if !backup.join(&name).exists() {
                    fs::copy(item.path(), backup.join(name))?;
                }
            }
        }
        self.write("rust-migration.json",&serde_json::json!({"schema":1,"created":chrono::Local::now().to_rfc3339(),"backup":backup}))
    }
}
pub fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path.parent().context("Missing parent directory")?;
    fs::create_dir_all(parent)?;
    let staging = parent.join(format!(".{}.tmp", common::token()));
    let result = (|| {
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&staging)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&staging, path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(staging);
    }
    result
}
